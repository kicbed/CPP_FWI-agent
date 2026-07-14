"""Configuration loading, preset resolution, and validation."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


MARMOUSI_MODEL_PATH = "/root/fwi-data/models/marmousi_94_288.npy"
MARMOUSI_METADATA_PATH = "/root/fwi-data/models/marmousi_94_288.json"
DEFAULT_RUN_ROOT = "/root/fwi-runs"

MARMOUSI_DEFAULTS: dict[str, Any] = {
    "model_id": "marmousi_94_288",
    "model_path": MARMOUSI_MODEL_PATH,
    "metadata_path": MARMOUSI_METADATA_PATH,
    "dx_m": 10.0,
    "dz_m": 10.0,
    "device": "cuda",
    "dtype": "float32",
    "source_wavelet": "ricker",
    "source_frequency_hz": 8.0,
    "dt_s": 0.001,
    "nt": 2000,
    "accuracy": 4,
    "pml_width": 20,
    "n_shots": 3,
    "n_receivers": 96,
    "shot_batch_size": 1,
    "source_depth_m": 20.0,
    "receiver_depth_m": 20.0,
    "velocity_min_mps": 1500.0,
    "velocity_max_mps": 5500.0,
    "seed": 2026,
    "initial_smoothing_sigma_cells": 8.0,
    "preserve_top_rows": 1,
    "optimizer": "adam",
    "learning_rate": 10.0,
    "gradient_clip_quantile": 0.98,
}

HOMOGENEOUS_DEFAULTS: dict[str, Any] = {
    "model_id": "homogeneous_48_96",
    "model_path": None,
    "metadata_path": None,
    "homogeneous_shape": (48, 96),
    "homogeneous_velocity_mps": 2000.0,
    "dx_m": 10.0,
    "dz_m": 10.0,
    "device": "cpu",
    "dtype": "float32",
    "source_wavelet": "ricker",
    "source_frequency_hz": 10.0,
    "dt_s": 0.001,
    "nt": 600,
    "accuracy": 4,
    "pml_width": 12,
    "n_shots": 1,
    "n_receivers": 24,
    "shot_batch_size": 1,
    "source_depth_m": 20.0,
    "receiver_depth_m": 20.0,
    "velocity_min_mps": 1500.0,
    "velocity_max_mps": 5500.0,
    "seed": 2026,
    "initial_smoothing_sigma_cells": 0.0,
    "preserve_top_rows": 0,
    "optimizer": "adam",
    "learning_rate": 10.0,
    "gradient_clip_quantile": 0.98,
}

PRESETS: dict[str, dict[str, Any]] = {
    "homogeneous_smoke": {**HOMOGENEOUS_DEFAULTS, "iterations": 0},
    "marmousi_94_288_demo": {**MARMOUSI_DEFAULTS, "iterations": 0},
    "forward": {**MARMOUSI_DEFAULTS, "iterations": 0},
    "fwi_smoke": {**MARMOUSI_DEFAULTS, "iterations": 2},
    "fwi_demo": {**MARMOUSI_DEFAULTS, "iterations": 5},
}


class FWIConfig(BaseModel):
    """Resolved numerical configuration for one worker invocation."""

    model_config = ConfigDict(extra="forbid")

    job_id: str | None = None
    preset: Literal[
        "homogeneous_smoke",
        "marmousi_94_288_demo",
        "forward",
        "fwi_smoke",
        "fwi_demo",
    ] = "forward"
    model_id: Literal["marmousi_94_288", "homogeneous_48_96"]
    model_path: str | None = None
    metadata_path: str | None = None
    homogeneous_shape: tuple[int, int] = (48, 96)
    homogeneous_velocity_mps: float = 2000.0

    dx_m: float = Field(gt=0)
    dz_m: float = Field(gt=0)
    device: Literal["cpu", "cuda"]
    dtype: Literal["float32"] = "float32"
    source_wavelet: Literal["ricker"] = "ricker"
    source_frequency_hz: float = Field(gt=0)
    dt_s: float = Field(gt=0)
    nt: int = Field(ge=2)
    accuracy: Literal[2, 4, 6, 8] = 4
    pml_width: int = Field(ge=0)
    n_shots: int = Field(ge=1)
    n_receivers: int = Field(ge=1)
    shot_batch_size: int = Field(ge=1)
    source_depth_m: float = Field(ge=0)
    receiver_depth_m: float = Field(ge=0)
    velocity_min_mps: float = Field(gt=0)
    velocity_max_mps: float = Field(gt=0)
    seed: int = 2026

    initial_smoothing_sigma_cells: float = Field(ge=0)
    preserve_top_rows: int = Field(ge=0)
    iterations: int = Field(ge=0)
    optimizer: Literal["adam", "sgd"] = "adam"
    learning_rate: float = Field(gt=0)
    gradient_clip_quantile: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def validate_consistency(self) -> "FWIConfig":
        if self.velocity_min_mps >= self.velocity_max_mps:
            raise ValueError("velocity_min_mps must be less than velocity_max_mps")
        if self.shot_batch_size > self.n_shots:
            raise ValueError("shot_batch_size cannot exceed n_shots")
        if self.model_id == "marmousi_94_288":
            if not self.model_path or not self.metadata_path:
                raise ValueError("Marmousi requires model_path and metadata_path")
        if self.model_id == "homogeneous_48_96":
            if min(self.homogeneous_shape) < 8:
                raise ValueError("homogeneous_shape dimensions must be at least 8")
            if not (
                self.velocity_min_mps
                <= self.homogeneous_velocity_mps
                <= self.velocity_max_mps
            ):
                raise ValueError("homogeneous velocity is outside configured bounds")
        return self

    @property
    def courant_number(self) -> float:
        """User-step 2-D Courant number; Deepwave may internally substep."""

        return self.velocity_max_mps * self.dt_s * math.sqrt(
            1.0 / self.dz_m**2 + 1.0 / self.dx_m**2
        )


def read_config_file(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.is_file():
        raise FileNotFoundError(f"config file does not exist: {config_path}")
    text = config_path.read_text(encoding="utf-8")
    suffix = config_path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        value = yaml.safe_load(text)
    else:
        value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("configuration root must be an object")
    return value


def resolve_config(raw: dict[str, Any]) -> FWIConfig:
    preset_name = raw.get("preset", "forward")
    if preset_name not in PRESETS:
        raise ValueError(f"unknown preset: {preset_name!r}")
    merged = dict(PRESETS[preset_name])
    merged.update(raw)
    merged["preset"] = preset_name
    return FWIConfig.model_validate(merged)


def load_config(path: str | Path) -> tuple[dict[str, Any], FWIConfig]:
    raw = read_config_file(path)
    return raw, resolve_config(raw)


def configured_run_root() -> Path:
    value = os.environ.get("FWI_RUN_ROOT", DEFAULT_RUN_ROOT)
    root = Path(value)
    if not root.is_absolute():
        raise ValueError("FWI_RUN_ROOT must be an absolute path")
    return root
