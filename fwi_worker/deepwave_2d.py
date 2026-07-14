"""Thin, testable wrapper around Deepwave's 2-D scalar propagator."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import version
from typing import Iterable

import deepwave
import numpy as np
import torch

from .acquisition import AcquisitionGeometry
from .config import FWIConfig


@dataclass(frozen=True)
class ForwardResult:
    receiver_amplitudes: np.ndarray
    source_peak_time_s: float


def deepwave_version() -> str:
    return version("deepwave")


def validate_device(device: str) -> torch.device:
    if device == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA was requested but torch.cuda.is_available() is false")
        if torch.cuda.device_count() < 1:
            raise RuntimeError("CUDA was requested but no CUDA device is visible")
        return torch.device("cuda:0")
    if device != "cpu":
        raise ValueError(f"unsupported device: {device}")
    return torch.device("cpu")


def make_source_wavelet(config: FWIConfig, device: torch.device) -> torch.Tensor:
    # A 1.5/f peak delay is the convention used by Deepwave's FWI examples and
    # keeps the causal portion of the Ricker wavelet inside the sampled window.
    peak_time_s = 1.5 / config.source_frequency_hz
    return deepwave.wavelets.ricker(
        config.source_frequency_hz,
        config.nt,
        config.dt_s,
        peak_time_s,
        dtype=torch.float32,
    ).to(device)


def _batch_receiver_data(
    velocity: torch.Tensor,
    config: FWIConfig,
    source_locations: torch.Tensor,
    receiver_locations: torch.Tensor,
    wavelet: torch.Tensor,
) -> torch.Tensor:
    batch_size = source_locations.shape[0]
    source_amplitudes = wavelet.reshape(1, 1, -1).expand(batch_size, 1, -1)
    return deepwave.scalar(
        velocity,
        [config.dz_m, config.dx_m],
        config.dt_s,
        source_amplitudes=source_amplitudes,
        source_locations=source_locations,
        receiver_locations=receiver_locations,
        accuracy=config.accuracy,
        pml_width=config.pml_width,
        pml_freq=config.source_frequency_hz,
        max_vel=config.velocity_max_mps,
    )[-1]


def shot_slices(n_shots: int, batch_size: int) -> Iterable[slice]:
    for start in range(0, n_shots, batch_size):
        yield slice(start, min(n_shots, start + batch_size))


def simulate_tensor(
    velocity: torch.Tensor,
    config: FWIConfig,
    geometry: AcquisitionGeometry,
    *,
    wavelet: torch.Tensor | None = None,
) -> torch.Tensor:
    """Simulate all shots and return a device tensor [shot, receiver, time]."""

    device = velocity.device
    if wavelet is None:
        wavelet = make_source_wavelet(config, device)
    outputs: list[torch.Tensor] = []
    for shot_slice in shot_slices(config.n_shots, config.shot_batch_size):
        sources = torch.as_tensor(
            geometry.source_locations[shot_slice], dtype=torch.long, device=device
        )
        receivers = torch.as_tensor(
            geometry.receiver_locations[shot_slice], dtype=torch.long, device=device
        )
        outputs.append(
            _batch_receiver_data(
                velocity, config, sources, receivers, wavelet
            )
        )
    return torch.cat(outputs, dim=0)


def forward_model(
    velocity: np.ndarray,
    config: FWIConfig,
    geometry: AcquisitionGeometry,
) -> ForwardResult:
    device = validate_device(config.device)
    model = torch.as_tensor(velocity, dtype=torch.float32, device=device)
    with torch.no_grad():
        receiver_data = simulate_tensor(model, config, geometry)
    if not torch.isfinite(receiver_data).all().item():
        raise FloatingPointError("Deepwave forward output contains NaN or Inf")
    if torch.count_nonzero(receiver_data).item() == 0:
        raise FloatingPointError("Deepwave forward output is entirely zero")
    return ForwardResult(
        receiver_amplitudes=receiver_data.detach().cpu().numpy().astype(
            np.float32, copy=False
        ),
        source_peak_time_s=1.5 / config.source_frequency_hz,
    )


def rough_first_arrival_check(
    data: np.ndarray,
    config: FWIConfig,
    geometry: AcquisitionGeometry,
) -> dict[str, float | bool | int]:
    """Compare a near-offset peak against homogeneous direct-wave timing.

    This deliberately remains a coarse smoke check.  It is only meaningful for
    the homogeneous preset and includes the Ricker peak delay.
    """

    source_x = geometry.source_x_m[0]
    offsets = np.abs(geometry.receiver_x_m - source_x)
    # Prefer a short non-zero offset so the direct event dominates reflections.
    candidates = np.flatnonzero(offsets > 0)
    receiver_index = int(candidates[np.argmin(offsets[candidates])])
    trace = data[0, receiver_index]
    measured_peak = float(np.argmax(np.abs(trace)) * config.dt_s)
    expected_peak = float(
        1.5 / config.source_frequency_hz
        + offsets[receiver_index] / config.homogeneous_velocity_mps
    )
    tolerance = max(0.025, 0.5 / config.source_frequency_hz)
    error = abs(measured_peak - expected_peak)
    return {
        "receiver_index": receiver_index,
        "offset_m": float(offsets[receiver_index]),
        "measured_peak_time_s": measured_peak,
        "expected_peak_time_s": expected_peak,
        "absolute_error_s": error,
        "tolerance_s": tolerance,
        "passed": bool(error <= tolerance),
    }


def small_model_gradient_check(device_name: str = "cpu") -> dict[str, float | bool]:
    """Run a centered directional-derivative check through Deepwave scalar.

    The check uses float64 and a compact homogeneous model to reduce cancellation
    error.  It is intentionally separate from production float32 propagation.
    """

    device = validate_device(device_name)
    nz, nx = 24, 32
    velocity = torch.full(
        (nz, nx), 2000.0, dtype=torch.float64, device=device, requires_grad=True
    )
    nt, dt, frequency = 220, 0.001, 12.0
    wavelet = deepwave.wavelets.ricker(
        frequency, nt, dt, 1.5 / frequency, dtype=torch.float64
    ).to(device)
    source = torch.tensor([[[2, nx // 2]]], dtype=torch.long, device=device)
    receivers = torch.tensor(
        [[[2, 8], [2, 12], [2, 20], [2, 24]]],
        dtype=torch.long,
        device=device,
    )

    def objective(model: torch.Tensor) -> torch.Tensor:
        data = deepwave.scalar(
            model,
            10.0,
            dt,
            source_amplitudes=wavelet.reshape(1, 1, -1),
            source_locations=source,
            receiver_locations=receivers,
            accuracy=4,
            pml_width=8,
            pml_freq=frequency,
            max_vel=2200.0,
        )[-1]
        return data.square().sum()

    value = objective(velocity)
    (gradient,) = torch.autograd.grad(value, velocity)
    if not torch.isfinite(gradient).all() or torch.linalg.vector_norm(gradient) == 0:
        raise FloatingPointError("gradient check produced an invalid gradient")
    direction = gradient.detach() / torch.linalg.vector_norm(gradient.detach())
    analytic = float(torch.sum(gradient.detach() * direction).cpu())
    errors: list[float] = []
    estimates: list[float] = []
    with torch.no_grad():
        for epsilon in (2.0, 1.0, 0.5):
            plus = float(objective(velocity + epsilon * direction).cpu())
            minus = float(objective(velocity - epsilon * direction).cpu())
            estimate = (plus - minus) / (2.0 * epsilon)
            estimates.append(estimate)
            errors.append(abs(estimate - analytic) / max(abs(analytic), 1e-20))
    best_error = min(errors)
    return {
        "analytic_directional_derivative": analytic,
        "finite_difference_directional_derivative": estimates[errors.index(best_error)],
        "relative_error": best_error,
        "passed": bool(best_error < 5e-3),
    }
