"""Read-only model loading and sidecar integrity checks."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from scipy.ndimage import gaussian_filter

from .config import FWIConfig


EXPECTED_MARMOUSI = {
    "id": "marmousi_94_288",
    "shape": [94, 288],
    "axis_order": ["z", "x"],
    "mat_variable": "data",
    "source_dtype": "uint16",
    "compute_dtype": "float32",
    "physics": "2d_acoustic_constant_density",
    "parameter": "vp",
    "velocity_unit": "m/s",
    "velocity_min_mps": 1500.0,
    "velocity_max_mps": 5500.0,
    "dx_m": 10.0,
    "dz_m": 10.0,
    "source_sha256": "4E1A50D4AFC5C81016E775FE99C0AC716B975701FCB89885DA6F4CE433DC4357",
    "sha256": "B80918E3A609A679F16A47DD30978812D80E4FAB1FCBD5CE692D9CA97022A688",
    "source_path": "/root/fwi-data/models/marmousi_94_288.mat",
}


@dataclass(frozen=True)
class LoadedModel:
    velocity: np.ndarray
    metadata: dict[str, Any]


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _require_equal(metadata: dict[str, Any], key: str, expected: Any) -> None:
    actual = metadata.get(key)
    if actual != expected:
        raise ValueError(
            f"metadata field {key!r} mismatch: expected {expected!r}, got {actual!r}"
        )


def read_and_validate_sidecar(config: FWIConfig) -> dict[str, Any]:
    if config.model_id != "marmousi_94_288":
        raise ValueError("sidecar validation is only used for the Marmousi preset")
    metadata_path = Path(config.metadata_path or "")
    model_path = Path(config.model_path or "")
    if not metadata_path.is_file():
        raise FileNotFoundError(f"metadata sidecar does not exist: {metadata_path}")
    if not model_path.is_file():
        raise FileNotFoundError(f"model NPY does not exist: {model_path}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if not isinstance(metadata, dict):
        raise ValueError("metadata sidecar root must be an object")
    for key, expected in EXPECTED_MARMOUSI.items():
        _require_equal(metadata, key, expected)
    if metadata.get("path") != str(model_path):
        raise ValueError("sidecar path does not match configured model_path")
    if float(config.dx_m) != float(metadata["dx_m"]):
        raise ValueError("configured dx_m does not match the sidecar")
    if float(config.dz_m) != float(metadata["dz_m"]):
        raise ValueError("configured dz_m does not match the sidecar")
    if config.dtype != metadata["compute_dtype"]:
        raise ValueError("configured dtype does not match the sidecar")
    expected_hash = str(metadata["sha256"]).upper()
    actual_hash = sha256_file(model_path)
    if actual_hash != expected_hash:
        raise ValueError(
            f"model SHA256 mismatch: expected {expected_hash}, got {actual_hash}"
        )
    source_path = Path(str(metadata["source_path"]))
    if not source_path.is_file():
        raise FileNotFoundError(f"original MAT model does not exist: {source_path}")
    expected_source_hash = str(metadata["source_sha256"]).upper()
    actual_source_hash = sha256_file(source_path)
    if actual_source_hash != expected_source_hash:
        raise ValueError(
            "original MAT SHA256 mismatch: "
            f"expected {expected_source_hash}, got {actual_source_hash}"
        )
    return metadata


def load_model(config: FWIConfig) -> LoadedModel:
    if config.model_id == "homogeneous_48_96":
        nz, nx = config.homogeneous_shape
        velocity = np.full(
            (nz, nx), config.homogeneous_velocity_mps, dtype=np.float32
        )
        metadata: dict[str, Any] = {
            "id": config.model_id,
            "shape": [nz, nx],
            "axis_order": ["z", "x"],
            "compute_dtype": "float32",
            "physics": "2d_acoustic_constant_density",
            "parameter": "vp",
            "velocity_unit": "m/s",
            "velocity_min_mps": float(velocity.min()),
            "velocity_max_mps": float(velocity.max()),
            "dx_m": config.dx_m,
            "dz_m": config.dz_m,
            "x_cell_extent_m": [0.0, nx * config.dx_m],
            "z_cell_extent_m": [0.0, nz * config.dz_m],
            "synthetic_homogeneous": True,
        }
        return LoadedModel(velocity=velocity, metadata=metadata)

    metadata = read_and_validate_sidecar(config)
    # Copy into private memory so the original read-only input is never mutated.
    velocity = np.array(np.load(config.model_path, allow_pickle=False), copy=True)
    if velocity.shape != tuple(metadata["shape"]):
        raise ValueError(
            f"model shape mismatch: expected {metadata['shape']}, got {velocity.shape}"
        )
    if velocity.dtype != np.float32:
        raise ValueError(f"model dtype must be float32, got {velocity.dtype}")
    if not np.isfinite(velocity).all():
        raise ValueError("model contains NaN or Inf")
    actual_min = float(velocity.min())
    actual_max = float(velocity.max())
    if actual_min != float(metadata["velocity_min_mps"]):
        raise ValueError("model minimum velocity does not match the sidecar")
    if actual_max != float(metadata["velocity_max_mps"]):
        raise ValueError("model maximum velocity does not match the sidecar")
    if actual_min < config.velocity_min_mps or actual_max > config.velocity_max_mps:
        raise ValueError("model velocities are outside configured inversion bounds")
    return LoadedModel(velocity=velocity, metadata=metadata)


def make_initial_model(true_velocity: np.ndarray, config: FWIConfig) -> np.ndarray:
    if config.initial_smoothing_sigma_cells == 0:
        return np.array(true_velocity, dtype=np.float32, copy=True)
    slowness = 1.0 / true_velocity.astype(np.float64)
    smoothed = gaussian_filter(
        slowness,
        sigma=config.initial_smoothing_sigma_cells,
        mode="nearest",
    )
    initial = np.reciprocal(smoothed)
    if config.preserve_top_rows:
        rows = min(config.preserve_top_rows, true_velocity.shape[0])
        initial[:rows, :] = true_velocity[:rows, :]
    initial = np.clip(
        initial, config.velocity_min_mps, config.velocity_max_mps
    ).astype(np.float32)
    if not np.isfinite(initial).all():
        raise ValueError("initial model contains NaN or Inf")
    return initial
