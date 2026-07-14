"""Structured numerical and environment metrics."""

from __future__ import annotations

import platform
import sys
from typing import Any

import deepwave
import numpy as np
import torch

from .config import FWIConfig
from .deepwave_2d import deepwave_version


def relative_l2(value: np.ndarray, reference: np.ndarray) -> float:
    denominator = np.linalg.norm(reference.astype(np.float64))
    numerator = np.linalg.norm((value - reference).astype(np.float64))
    if denominator == 0:
        return 0.0 if numerator == 0 else float("inf")
    return float(numerator / denominator)


def environment_info(config: FWIConfig) -> dict[str, Any]:
    cuda_available = bool(torch.cuda.is_available())
    device_name = (
        torch.cuda.get_device_name(0)
        if config.device == "cuda" and cuda_available
        else platform.processor() or "CPU"
    )
    return {
        "python_version": platform.python_version(),
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "torch_version": torch.__version__,
        "deepwave_version": deepwave_version(),
        "cuda_available": cuda_available,
        "cuda_runtime_version": torch.version.cuda,
        "cuda_device_count": torch.cuda.device_count() if cuda_available else 0,
        "requested_device": config.device,
        "device_name": device_name,
    }


def count_nonfinite(*arrays: np.ndarray) -> tuple[int, int]:
    nan_count = sum(int(np.isnan(value).sum()) for value in arrays)
    inf_count = sum(int(np.isinf(value).sum()) for value in arrays)
    return nan_count, inf_count


def calculate_metrics(
    *,
    config: FWIConfig,
    true_velocity: np.ndarray,
    initial_velocity: np.ndarray,
    inverted_velocity: np.ndarray,
    observed_data: np.ndarray,
    predicted_data: np.ndarray,
    losses: list[float],
    elapsed_seconds: float,
    model_update_relative_l2: float,
    gradient_clip_values: list[float],
    first_arrival_check: dict[str, Any] | None,
    gradient_check: dict[str, Any] | None = None,
) -> dict[str, Any]:
    residual = predicted_data - observed_data
    nan_count, inf_count = count_nonfinite(
        true_velocity,
        initial_velocity,
        inverted_velocity,
        observed_data,
        predicted_data,
        residual,
        np.asarray(losses),
    )
    initial_loss = float(losses[0])
    final_loss = float(losses[-1])
    loss_reduction = (
        float((initial_loss - final_loss) / initial_loss)
        if initial_loss > 0
        else (0.0 if final_loss == 0 else float("-inf"))
    )
    environment = environment_info(config)
    peak_gpu_memory = (
        float(torch.cuda.max_memory_allocated(0) / (1024**2))
        if config.device == "cuda" and torch.cuda.is_available()
        else 0.0
    )
    result: dict[str, Any] = {
        "model_shape": list(true_velocity.shape),
        "dx_m": config.dx_m,
        "dz_m": config.dz_m,
        "source_frequency_hz": config.source_frequency_hz,
        "dt_s": config.dt_s,
        "nt": config.nt,
        "accuracy": config.accuracy,
        "pml_width": config.pml_width,
        "n_shots": config.n_shots,
        "n_receivers": config.n_receivers,
        "iterations": config.iterations,
        "optimizer": config.optimizer,
        "learning_rate": config.learning_rate,
        "gradient_clip_quantile": config.gradient_clip_quantile,
        "gradient_clip_values": gradient_clip_values,
        "initial_loss": initial_loss,
        "final_loss": final_loss,
        "loss_reduction_fraction": loss_reduction,
        "initial_model_relative_l2": relative_l2(initial_velocity, true_velocity),
        "final_model_relative_l2": relative_l2(inverted_velocity, true_velocity),
        "observed_predicted_relative_l2": relative_l2(
            predicted_data, observed_data
        ),
        "model_update_relative_l2": model_update_relative_l2,
        "true_velocity_min": float(true_velocity.min()),
        "true_velocity_max": float(true_velocity.max()),
        "initial_velocity_min": float(initial_velocity.min()),
        "initial_velocity_max": float(initial_velocity.max()),
        "inverted_velocity_min": float(inverted_velocity.min()),
        "inverted_velocity_max": float(inverted_velocity.max()),
        "nan_count": nan_count,
        "inf_count": inf_count,
        "elapsed_seconds": float(elapsed_seconds),
        "torch_version": environment["torch_version"],
        "deepwave_version": environment["deepwave_version"],
        "device": config.device,
        "device_name": environment["device_name"],
        "peak_gpu_memory_mb": peak_gpu_memory,
    }
    if first_arrival_check is not None:
        result["first_arrival_check"] = first_arrival_check
    if gradient_check is not None:
        result["gradient_check"] = gradient_check
    return result
