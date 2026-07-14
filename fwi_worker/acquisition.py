"""Acquisition geometry creation and checked coordinate conversion."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import FWIConfig


@dataclass(frozen=True)
class AcquisitionGeometry:
    source_locations: np.ndarray
    receiver_locations: np.ndarray
    source_x_m: np.ndarray
    receiver_x_m: np.ndarray


def meters_to_grid_index(
    coordinate_m: float, spacing_m: float, size: int, *, label: str
) -> int:
    if not np.isfinite(coordinate_m):
        raise ValueError(f"{label} must be finite")
    if spacing_m <= 0 or size <= 0:
        raise ValueError("spacing and grid size must be positive")
    index = int(round(coordinate_m / spacing_m))
    if index < 0 or index >= size:
        raise ValueError(
            f"{label}={coordinate_m} m maps to out-of-bounds index {index} "
            f"for size {size}"
        )
    return index


def _uniform_physical_positions_to_indices(
    count: int,
    start_m: float,
    stop_m: float,
    spacing_m: float,
    size: int,
    *,
    label: str,
) -> np.ndarray:
    if count < 1:
        raise ValueError("count must be positive")
    available = int(round((stop_m - start_m) / spacing_m)) + 1
    if count > available:
        raise ValueError(
            f"cannot place {count} unique {label} positions in {available} grid cells"
        )
    if count == 1:
        positions_m = np.asarray([(start_m + stop_m) / 2.0])
    else:
        positions_m = np.linspace(start_m, stop_m, count)
    indices = np.asarray(
        [
            meters_to_grid_index(float(position), spacing_m, size, label=label)
            for position in positions_m
        ],
        dtype=np.int64,
    )
    if np.unique(indices).size != count:
        raise ValueError(f"uniform {label} positions map to duplicate grid cells")
    return indices


def build_acquisition(config: FWIConfig, shape: tuple[int, int]) -> AcquisitionGeometry:
    nz, nx = shape
    source_z = meters_to_grid_index(
        config.source_depth_m, config.dz_m, nz, label="source_depth_m"
    )
    receiver_z = meters_to_grid_index(
        config.receiver_depth_m, config.dz_m, nz, label="receiver_depth_m"
    )
    # Keep sources and receivers off the exact horizontal edges. Deepwave adds
    # its PML outside the supplied model; this margin is an acquisition choice,
    # not a replacement for the PML.
    margin = max(1, config.accuracy // 2)
    if nx <= 2 * margin:
        raise ValueError("grid is too narrow for the configured edge margin")
    start_x_m = margin * config.dx_m
    stop_x_m = (nx - margin - 1) * config.dx_m
    source_x = _uniform_physical_positions_to_indices(
        config.n_shots,
        start_x_m,
        stop_x_m,
        config.dx_m,
        nx,
        label="source_x_m",
    )
    receiver_x = _uniform_physical_positions_to_indices(
        config.n_receivers,
        start_x_m,
        stop_x_m,
        config.dx_m,
        nx,
        label="receiver_x_m",
    )

    source_locations = np.empty((config.n_shots, 1, 2), dtype=np.int64)
    source_locations[:, 0, 0] = source_z
    source_locations[:, 0, 1] = source_x
    receiver_locations = np.empty(
        (config.n_shots, config.n_receivers, 2), dtype=np.int64
    )
    receiver_locations[:, :, 0] = receiver_z
    receiver_locations[:, :, 1] = receiver_x[None, :]

    for label, locations in (
        ("source", source_locations),
        ("receiver", receiver_locations),
    ):
        if (
            (locations[..., 0] < 0).any()
            or (locations[..., 0] >= nz).any()
            or (locations[..., 1] < 0).any()
            or (locations[..., 1] >= nx).any()
        ):
            raise ValueError(f"{label} locations are out of bounds")

    return AcquisitionGeometry(
        source_locations=source_locations,
        receiver_locations=receiver_locations,
        source_x_m=source_x.astype(np.float64) * config.dx_m,
        receiver_x_m=receiver_x.astype(np.float64) * config.dx_m,
    )
