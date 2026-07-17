"""A deliberately simple bounded single-parameter waveform inversion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import torch

from .acquisition import AcquisitionGeometry
from .config import FWIConfig
from .deepwave_2d import make_source_wavelet, shot_slices, simulate_tensor, validate_device


@dataclass(frozen=True)
class InversionResult:
    inverted_velocity: np.ndarray
    predicted_data: np.ndarray
    residual_data: np.ndarray
    losses: list[float]
    gradient_clip_values: list[float]
    model_update_relative_l2: float


@dataclass(frozen=True)
class InversionCheckpointState:
    """Live optimizer state at the sole post-update checkpoint barrier."""

    completed_updates: int
    next_state_index: int
    velocity: torch.nn.Parameter
    optimizer: torch.optim.Optimizer
    losses: tuple[float, ...]
    gradient_clip_values: tuple[float, ...]


ProgressCallback = Callable[[int, float, float | None], None]
CancellationCheck = Callable[[], None]
CheckpointCallback = Callable[[InversionCheckpointState], None]


def run_inversion(
    initial_velocity: np.ndarray,
    observed_data: np.ndarray,
    config: FWIConfig,
    geometry: AcquisitionGeometry,
    progress: ProgressCallback | None = None,
    cancel_check: CancellationCheck | None = None,
    checkpoint: CheckpointCallback | None = None,
) -> InversionResult:
    if config.iterations < 1:
        raise ValueError("inversion requires iterations >= 1")
    expected_data_shape = (config.n_shots, config.n_receivers, config.nt)
    if observed_data.shape != expected_data_shape:
        raise ValueError(
            f"observed data shape must be {expected_data_shape}, got {observed_data.shape}"
        )
    if observed_data.dtype != np.float32:
        raise ValueError(f"observed data dtype must be float32, got {observed_data.dtype}")
    if not np.isfinite(observed_data).all():
        raise FloatingPointError("observed data contains NaN or Inf")
    device = validate_device(config.device)
    velocity = torch.nn.Parameter(
        torch.as_tensor(initial_velocity, dtype=torch.float32, device=device).clone()
    )
    observed = torch.as_tensor(observed_data, dtype=torch.float32, device=device)
    observed_energy = torch.sum(observed.square()).detach()
    if not torch.isfinite(observed_energy) or observed_energy.item() <= 0:
        raise FloatingPointError("observed data energy is not finite and positive")
    if config.optimizer == "adam":
        optimizer: torch.optim.Optimizer = torch.optim.Adam(
            [velocity], lr=config.learning_rate
        )
    else:
        optimizer = torch.optim.SGD([velocity], lr=config.learning_rate)
    wavelet = make_source_wavelet(config, device)
    losses: list[float] = []
    clip_values: list[float] = []
    final_predicted: torch.Tensor | None = None

    # State k is the model after k updates.  Recording k=0 and k=N makes the
    # initial/final comparison explicit without hiding the last model update.
    for state_index in range(config.iterations + 1):
        if cancel_check is not None:
            cancel_check()
        needs_gradient = state_index < config.iterations
        if needs_gradient:
            optimizer.zero_grad(set_to_none=True)
        predictions: list[torch.Tensor] = []
        total_loss_value = 0.0
        for shot_slice in shot_slices(config.n_shots, config.shot_batch_size):
            if cancel_check is not None:
                cancel_check()
            # Use a batch-specific geometry to avoid propagating all shots at
            # once while retaining one shared model parameter.
            batch_geometry = AcquisitionGeometry(
                source_locations=geometry.source_locations[shot_slice],
                receiver_locations=geometry.receiver_locations[shot_slice],
                source_x_m=geometry.source_x_m[shot_slice],
                receiver_x_m=geometry.receiver_x_m,
            )
            batch_config = config.model_copy(
                update={
                    "n_shots": batch_geometry.source_locations.shape[0],
                    "shot_batch_size": batch_geometry.source_locations.shape[0],
                }
            )
            context = torch.enable_grad() if needs_gradient else torch.no_grad()
            with context:
                prediction = simulate_tensor(
                    velocity, batch_config, batch_geometry, wavelet=wavelet
                )
                if cancel_check is not None:
                    cancel_check()
                residual = prediction - observed[shot_slice]
                batch_loss = torch.sum(residual.square()) / observed_energy
            if not torch.isfinite(prediction).all().item():
                raise FloatingPointError(
                    f"predicted data became NaN/Inf at model state {state_index}"
                )
            if not torch.isfinite(batch_loss).item():
                raise FloatingPointError(f"loss became NaN/Inf at state {state_index}")
            total_loss_value += float(batch_loss.detach().cpu())
            predictions.append(prediction.detach())
            if needs_gradient:
                batch_loss.backward()

        losses.append(total_loss_value)
        final_predicted = torch.cat(predictions, dim=0)
        clip_value: float | None = None
        if needs_gradient:
            if velocity.grad is None:
                raise FloatingPointError("Deepwave did not produce a velocity gradient")
            if not torch.isfinite(velocity.grad).all().item():
                raise FloatingPointError(
                    f"velocity gradient became NaN/Inf at update {state_index + 1}"
                )
            gradient_abs = velocity.grad.detach().abs()
            if torch.count_nonzero(gradient_abs).item() == 0:
                raise FloatingPointError("velocity gradient is entirely zero")
            clip_tensor = torch.quantile(
                gradient_abs, config.gradient_clip_quantile
            )
            clip_value = float(clip_tensor.detach().cpu())
            if not np.isfinite(clip_value) or clip_value <= 0:
                raise FloatingPointError("gradient clipping threshold is invalid")
            torch.nn.utils.clip_grad_value_(velocity, clip_value)
            optimizer.step()
            with torch.no_grad():
                velocity.clamp_(
                    min=config.velocity_min_mps, max=config.velocity_max_mps
                )
            if not torch.isfinite(velocity).all().item():
                raise FloatingPointError(
                    f"velocity model became NaN/Inf at update {state_index + 1}"
                )
            clip_values.append(clip_value)
        if progress is not None:
            progress(state_index, total_loss_value, clip_value)
        if checkpoint is not None and state_index == 0:
            checkpoint(
                InversionCheckpointState(
                    completed_updates=1,
                    next_state_index=1,
                    velocity=velocity,
                    optimizer=optimizer,
                    losses=tuple(losses),
                    gradient_clip_values=tuple(clip_values),
                )
            )
        if cancel_check is not None:
            cancel_check()

    assert final_predicted is not None
    inverted = velocity.detach().cpu().numpy().astype(np.float32, copy=False)
    predicted_np = final_predicted.cpu().numpy().astype(np.float32, copy=False)
    residual_np = predicted_np - observed_data
    update_norm = float(
        np.linalg.norm((inverted - initial_velocity).astype(np.float64))
        / max(np.linalg.norm(initial_velocity.astype(np.float64)), np.finfo(float).eps)
    )
    if update_norm == 0:
        raise FloatingPointError("optimizer completed without changing the model")
    return InversionResult(
        inverted_velocity=inverted,
        predicted_data=predicted_np,
        residual_data=residual_np.astype(np.float32, copy=False),
        losses=losses,
        gradient_clip_values=clip_values,
        model_update_relative_l2=update_norm,
    )
