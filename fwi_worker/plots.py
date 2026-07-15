"""Non-interactive, consistently scaled FWI result plots."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from PIL import Image

from .acquisition import AcquisitionGeometry
from .config import FWIConfig


MODEL_INTERPOLATION = "nearest"
PLOT_DPI = 160


def _save_figure_atomic(fig: plt.Figure, path: Path) -> None:
    parent = path.parent.resolve(strict=True)
    if path.parent.is_symlink() or path.parent != parent or parent.name != "figures":
        raise ValueError("PNG target must be in a regular figures directory")
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"refusing to overwrite existing PNG artifact: {path}")
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.stem}.", suffix=".png", dir=parent
    )
    os.close(fd)
    try:
        # Keep the Algorithm 1.4 figure contract independent of a developer's
        # ~/.config/matplotlib/matplotlibrc.  In particular, savefig.bbox=tight
        # would otherwise change the declared pixel dimensions.
        with matplotlib.rc_context(
            {
                "savefig.bbox": None,
                "savefig.transparent": False,
                "savefig.facecolor": "white",
                "savefig.edgecolor": "white",
            }
        ):
            fig.savefig(temp_name, dpi=PLOT_DPI, format="png")
        with open(temp_name, "rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def model_extent_km(
    metadata: dict[str, Any], shape: tuple[int, int], config: FWIConfig
) -> list[float]:
    x_extent = metadata.get(
        "x_cell_extent_m", [0.0, shape[1] * config.dx_m]
    )
    z_extent = metadata.get(
        "z_cell_extent_m", [0.0, shape[0] * config.dz_m]
    )
    return [
        float(x_extent[0]) / 1000.0,
        float(x_extent[1]) / 1000.0,
        float(z_extent[1]) / 1000.0,
        float(z_extent[0]) / 1000.0,
    ]


def plot_velocity_model(
    velocity: np.ndarray,
    path: Path,
    title: str,
    metadata: dict[str, Any],
    config: FWIConfig,
) -> None:
    extent = model_extent_km(metadata, velocity.shape, config)
    fig, axis = plt.subplots(figsize=(9, 3.8), constrained_layout=True)
    image = axis.imshow(
        velocity,
        cmap="turbo",
        vmin=config.velocity_min_mps,
        vmax=config.velocity_max_mps,
        interpolation=MODEL_INTERPOLATION,
        aspect="auto",
        extent=extent,
    )
    axis.set_title(title)
    axis.set_xlabel("Distance (km)")
    axis.set_ylabel("Depth (km)")
    colorbar = fig.colorbar(image, ax=axis)
    colorbar.set_label("Velocity (m/s)")
    _save_figure_atomic(fig, path)
    plt.close(fig)


def plot_model_error(
    error: np.ndarray,
    path: Path,
    metadata: dict[str, Any],
    config: FWIConfig,
) -> float:
    extent = model_extent_km(metadata, error.shape, config)
    max_abs_error = float(np.max(np.abs(error)))
    # Matplotlib rejects identical limits for a zero error image. A symmetric
    # one-unit display range is recorded explicitly for that special case.
    display_limit = max(max_abs_error, 1.0)
    fig, axis = plt.subplots(figsize=(9, 3.8), constrained_layout=True)
    image = axis.imshow(
        error,
        cmap="seismic",
        vmin=-display_limit,
        vmax=display_limit,
        interpolation=MODEL_INTERPOLATION,
        aspect="auto",
        extent=extent,
    )
    axis.set_title("Velocity Model Error (Inverted - True)")
    axis.set_xlabel("Distance (km)")
    axis.set_ylabel("Depth (km)")
    colorbar = fig.colorbar(image, ax=axis)
    colorbar.set_label("Velocity error (m/s)")
    _save_figure_atomic(fig, path)
    plt.close(fig)
    return display_limit


def _symmetric_clip(data: np.ndarray, percentile: float = 99.0) -> float:
    finite = np.abs(data[np.isfinite(data)])
    if finite.size == 0:
        raise ValueError("cannot plot shot gather without finite samples")
    value = float(np.percentile(finite, percentile))
    if value <= 0:
        value = float(finite.max())
    return float(max(value, float(np.finfo(np.float32).tiny)))


def plot_shot_gathers(
    observed: np.ndarray,
    predicted: np.ndarray,
    residual: np.ndarray,
    path: Path,
    config: FWIConfig,
    geometry: AcquisitionGeometry,
) -> dict[str, float | int]:
    shot_index = 0
    observed_predicted_clip = _symmetric_clip(
        np.concatenate((observed[shot_index].ravel(), predicted[shot_index].ravel()))
    )
    residual_clip = _symmetric_clip(residual[shot_index])
    x_min = float(geometry.receiver_x_m.min() / 1000.0)
    x_max = float(geometry.receiver_x_m.max() / 1000.0)
    t_max = (config.nt - 1) * config.dt_s
    extent = [x_min, x_max, t_max, 0.0]
    fig, axes = plt.subplots(1, 3, figsize=(13.5, 5), constrained_layout=True)
    panels = (
        (observed[shot_index], "Observed", observed_predicted_clip),
        (predicted[shot_index], "Predicted", observed_predicted_clip),
        (residual[shot_index], "Residual (Predicted - Observed)", residual_clip),
    )
    for axis, (data, title, clip) in zip(axes, panels):
        image = axis.imshow(
            data.T,
            cmap="gray",
            vmin=-clip,
            vmax=clip,
            interpolation=MODEL_INTERPOLATION,
            aspect="auto",
            extent=extent,
        )
        axis.set_title(title)
        axis.set_xlabel("Receiver distance (km)")
        axis.set_ylabel("Time (s)")
        fig.colorbar(image, ax=axis, shrink=0.8, label="Amplitude")
    fig.suptitle("Shot 1 Gathers (symmetric 99th-percentile clipping)")
    _save_figure_atomic(fig, path)
    plt.close(fig)
    return {
        "shot_index": shot_index,
        "percentile": 99.0,
        "observed_predicted_clip": observed_predicted_clip,
        "residual_clip": residual_clip,
    }


def plot_loss(losses: list[float], path: Path) -> dict[str, Any]:
    values = np.asarray(losses, dtype=np.float64)
    if not np.isfinite(values).all():
        raise ValueError("loss curve contains NaN or Inf")
    fig, axis = plt.subplots(figsize=(7, 4.5), constrained_layout=True)
    iterations = np.arange(len(values))
    axis.plot(iterations, values, marker="o")
    use_log = bool(
        np.all(values > 0)
        and values.max() / max(values.min(), np.finfo(float).tiny) >= 10.0
    )
    if use_log:
        axis.set_yscale("log")
    axis.set_xlabel("Iteration")
    axis.set_ylabel("Loss")
    axis.set_title("L2 Waveform Residual Loss")
    axis.grid(True, alpha=0.25)
    _save_figure_atomic(fig, path)
    plt.close(fig)
    return {"y_scale": "log" if use_log else "linear"}


def verify_png(path: Path) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"PNG is missing or empty: {path}")
    with Image.open(path) as image:
        if image.format != "PNG":
            raise ValueError(f"image is not a PNG: {path}")
        image.verify()
    # Re-open after verify to force a complete decode, not merely header checks.
    with Image.open(path) as image:
        image.load()


def generate_all_plots(
    *,
    run_dir: Path,
    true_velocity: np.ndarray,
    initial_velocity: np.ndarray,
    inverted_velocity: np.ndarray,
    observed: np.ndarray,
    predicted: np.ndarray,
    residual: np.ndarray,
    losses: list[float],
    metadata: dict[str, Any],
    config: FWIConfig,
    geometry: AcquisitionGeometry,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    figures_dir = run_dir / "figures"
    figure_specs = [
        ("true_model", "True Velocity Model", "figures/true_model.png"),
        ("initial_model", "Smoothed Initial Velocity Model", "figures/initial_model.png"),
        ("inverted_model", "Inverted Velocity Model", "figures/inverted_model.png"),
        ("model_error", "Velocity Model Error", "figures/model_error.png"),
        ("shot_gathers", "Observed, Predicted, and Residual Shot Gathers", "figures/shot_gathers.png"),
        ("loss_curve", "L2 Waveform Residual Loss", "figures/loss_curve.png"),
    ]
    plot_velocity_model(
        true_velocity,
        figures_dir / "true_model.png",
        "True Velocity Model",
        metadata,
        config,
    )
    plot_velocity_model(
        initial_velocity,
        figures_dir / "initial_model.png",
        "Smoothed Initial Velocity Model",
        metadata,
        config,
    )
    plot_velocity_model(
        inverted_velocity,
        figures_dir / "inverted_model.png",
        "Inverted Velocity Model",
        metadata,
        config,
    )
    error_limit = plot_model_error(
        inverted_velocity - true_velocity,
        figures_dir / "model_error.png",
        metadata,
        config,
    )
    shot_clips = plot_shot_gathers(
        observed,
        predicted,
        residual,
        figures_dir / "shot_gathers.png",
        config,
        geometry,
    )
    loss_details = plot_loss(losses, figures_dir / "loss_curve.png")
    for _, _, relative in figure_specs:
        verify_png(run_dir / relative)
    details = {
        "backend": "matplotlib Agg",
        "dpi": PLOT_DPI,
        "interpolation": MODEL_INTERPOLATION,
        "model_cmap": "turbo",
        "model_vmin_mps": config.velocity_min_mps,
        "model_vmax_mps": config.velocity_max_mps,
        "model_extent_km": model_extent_km(metadata, true_velocity.shape, config),
        "error_cmap": "seismic",
        "error_symmetric_limit_mps": error_limit,
        "shot_gather_clipping": shot_clips,
        "loss_curve": loss_details,
    }
    return (
        [
            {"id": figure_id, "title": title, "relative_path": relative}
            for figure_id, title, relative in figure_specs
        ],
        details,
    )
