"""Bounded no-pickle checkpoint payload for the managed 1.6 Worker."""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import numpy as np
import torch

from worker_launch_control import (
    CHECKPOINT_PROTOCOL_VERSION,
    LaunchAttemptBinding,
    checkpoint_id_for_binding,
)

from .config import FWIConfig
from .inversion import InversionCheckpointState


MAX_CHECKPOINT_FILE_BYTES = 2 * 1024 * 1024
MAX_CHECKPOINT_TOTAL_BYTES = 8 * 1024 * 1024


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _stable_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _require_private_directory(path: Path) -> Path:
    entry = path.lstat()
    if (
        path.is_symlink()
        or path.resolve(strict=True) != path
        or not stat.S_ISDIR(entry.st_mode)
        or entry.st_uid != os.geteuid()
        or stat.S_IMODE(entry.st_mode) & 0o077
    ):
        raise ValueError("checkpoint directory is not private and canonical")
    return path


def _ensure_private_directory(path: Path) -> Path:
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    return _require_private_directory(path)


def _create_private_bytes(path: Path, data: bytes) -> None:
    if not data or len(data) > MAX_CHECKPOINT_FILE_BYTES:
        raise ValueError("checkpoint file size is outside the fixed bound")
    parent = _require_private_directory(path.parent)
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor = -1
    try:
        descriptor = os.open(path, flags, 0o600)
        offset = 0
        while offset < len(data):
            written = os.write(descriptor, data[offset:])
            if written <= 0:
                raise OSError("checkpoint write made no progress")
            offset += written
        os.fsync(descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    directory_descriptor = os.open(
        parent,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)


def _npy_bytes(value: np.ndarray, *, dtype: np.dtype[Any]) -> bytes:
    array = np.asarray(value, dtype=dtype)
    if not array.size or array.ndim not in {1, 2} or not np.isfinite(array).all():
        raise FloatingPointError("checkpoint array must be non-empty and finite")
    stream = io.BytesIO()
    np.save(stream, np.ascontiguousarray(array), allow_pickle=False)
    data = stream.getvalue()
    if len(data) > MAX_CHECKPOINT_FILE_BYTES:
        raise ValueError("checkpoint array exceeds the fixed file bound")
    return data


def _write_array(
    checkpoint_dir: Path,
    checkpoint_id: str,
    name: str,
    value: np.ndarray,
    *,
    dtype: np.dtype[Any],
) -> dict[str, Any]:
    data = _npy_bytes(value, dtype=dtype)
    path = checkpoint_dir / name
    _create_private_bytes(path, data)
    stored = np.asarray(value, dtype=dtype)
    return {
        "relative_path": f"checkpoints/{checkpoint_id}/{name}",
        "size_bytes": len(data),
        "sha256": _sha256(data),
        "dtype": "float32" if stored.dtype == np.dtype("float32") else "float64",
        "shape": list(stored.shape),
    }


def _optimizer_payload(
    checkpoint_dir: Path,
    checkpoint_id: str,
    config: FWIConfig,
    checkpoint: InversionCheckpointState,
    model_shape: list[int],
) -> dict[str, Any]:
    optimizer = checkpoint.optimizer
    if len(optimizer.param_groups) != 1 or optimizer.param_groups[0].get("params") != [
        checkpoint.velocity
    ]:
        raise ValueError("checkpoint optimizer parameter binding changed")
    learning_rate = float(optimizer.param_groups[0].get("lr", float("nan")))
    if not math.isfinite(learning_rate) or learning_rate != float(config.learning_rate):
        raise ValueError("checkpoint optimizer learning rate changed")
    state = optimizer.state.get(checkpoint.velocity, {})
    if config.optimizer == "adam":
        if not isinstance(optimizer, torch.optim.Adam) or set(state) != {
            "step",
            "exp_avg",
            "exp_avg_sq",
        }:
            raise ValueError("Adam checkpoint state is incomplete")
        raw_step = state["step"]
        step_value = float(raw_step.detach().cpu()) if torch.is_tensor(raw_step) else float(raw_step)
        if not math.isfinite(step_value) or step_value != 1.0:
            raise ValueError("Adam checkpoint step is not the first update")
        optimizer_state = {}
        for field, name in (
            ("exp_avg", "optimizer_exp_avg.npy"),
            ("exp_avg_sq", "optimizer_exp_avg_sq.npy"),
        ):
            tensor = state[field]
            if not torch.is_tensor(tensor) or list(tensor.shape) != model_shape:
                raise ValueError("Adam checkpoint tensor shape changed")
            optimizer_state[field] = _write_array(
                checkpoint_dir,
                checkpoint_id,
                name,
                tensor.detach().cpu().numpy(),
                dtype=np.dtype("float32"),
            )
    else:
        if not isinstance(optimizer, torch.optim.SGD) or dict(state):
            raise ValueError("SGD checkpoint state is unexpected")
        group = optimizer.param_groups[0]
        if (
            float(group.get("momentum", 0.0)) != 0.0
            or float(group.get("dampening", 0.0)) != 0.0
            or bool(group.get("nesterov", False))
        ):
            raise ValueError("SGD checkpoint contract changed")
        optimizer_state = {}
    return {
        "name": config.optimizer,
        "learning_rate": learning_rate,
        "step": 1,
        "state": optimizer_state,
    }


@dataclass(frozen=True)
class CheckpointManifestEvidence:
    checkpoint_id: str
    checkpoint_index: int
    completed_updates: int
    manifest_relative_path: str
    manifest_size_bytes: int
    manifest_hash: str
    checkpoint_created_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "checkpoint_index": self.checkpoint_index,
            "completed_updates": self.completed_updates,
            "manifest_relative_path": self.manifest_relative_path,
            "manifest_size_bytes": self.manifest_size_bytes,
            "manifest_hash": self.manifest_hash,
            "checkpoint_created_at": self.checkpoint_created_at,
        }


def save_checkpoint_payload(
    *,
    run_dir: Path,
    binding: LaunchAttemptBinding,
    config: FWIConfig,
    checkpoint: InversionCheckpointState,
    clock: Callable[[], str] = _utc_now,
) -> CheckpointManifestEvidence:
    """Append the complete first-update payload before entering Waiting."""

    run_dir = _require_private_directory(run_dir)
    if checkpoint.completed_updates != 1 or checkpoint.next_state_index != 1:
        raise ValueError("only the deterministic first-update checkpoint is supported")
    if len(checkpoint.losses) != 1 or len(checkpoint.gradient_clip_values) != 1:
        raise ValueError("checkpoint history does not match one completed update")
    if not all(math.isfinite(value) for value in (*checkpoint.losses, *checkpoint.gradient_clip_values)):
        raise FloatingPointError("checkpoint history contains NaN or Inf")
    model = checkpoint.velocity.detach().cpu().numpy().astype(np.float32, copy=False)
    if model.ndim != 2 or model.size > 1_000_000 or not np.isfinite(model).all():
        raise FloatingPointError("checkpoint model is outside the bounded finite contract")
    checkpoint_id = checkpoint_id_for_binding(binding)
    checkpoints_dir = _ensure_private_directory(run_dir / "checkpoints")
    checkpoint_dir = checkpoints_dir / checkpoint_id
    try:
        checkpoint_dir.mkdir(mode=0o700)
    except FileExistsError as error:
        raise FileExistsError("checkpoint payload already exists") from error
    checkpoint_dir = _require_private_directory(checkpoint_dir)
    created_at = clock()
    if not isinstance(created_at, str) or not created_at.endswith("Z"):
        raise ValueError("checkpoint clock did not return a UTC timestamp")
    try:
        datetime.fromisoformat(created_at[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError("checkpoint clock did not return a UTC timestamp") from error

    model_descriptor = _write_array(
        checkpoint_dir,
        checkpoint_id,
        "model.npy",
        model,
        dtype=np.dtype("float32"),
    )
    losses_descriptor = _write_array(
        checkpoint_dir,
        checkpoint_id,
        "losses.npy",
        np.asarray(checkpoint.losses, dtype=np.float64),
        dtype=np.dtype("float64"),
    )
    clips_descriptor = _write_array(
        checkpoint_dir,
        checkpoint_id,
        "gradient_clip_values.npy",
        np.asarray(checkpoint.gradient_clip_values, dtype=np.float64),
        dtype=np.dtype("float64"),
    )
    optimizer = _optimizer_payload(
        checkpoint_dir,
        checkpoint_id,
        config,
        checkpoint,
        list(model.shape),
    )
    config_hash = _sha256(_stable_json_bytes(config.model_dump(mode="json")))
    manifest = {
        "schema_version": CHECKPOINT_PROTOCOL_VERSION,
        "checkpoint_id": checkpoint_id,
        "checkpoint_index": 1,
        "completed_updates": 1,
        "next_state_index": 1,
        "binding_hash": binding.binding_hash,
        "job_id": binding.job_id,
        "request_hash": binding.request_hash,
        "config_hash": config_hash,
        "optimizer": optimizer,
        "model": model_descriptor,
        "history": {
            "losses": losses_descriptor,
            "gradient_clip_values": clips_descriptor,
        },
        "created_at": created_at,
    }
    manifest_data = _stable_json_bytes(manifest) + b"\n"
    total_size = len(manifest_data) + sum(
        entry["size_bytes"]
        for entry in (
            model_descriptor,
            losses_descriptor,
            clips_descriptor,
            *optimizer["state"].values(),
        )
    )
    if total_size > MAX_CHECKPOINT_TOTAL_BYTES:
        raise ValueError("checkpoint payload exceeds the fixed aggregate bound")
    _create_private_bytes(checkpoint_dir / "manifest.json", manifest_data)
    return CheckpointManifestEvidence(
        checkpoint_id=checkpoint_id,
        checkpoint_index=1,
        completed_updates=1,
        manifest_relative_path=f"checkpoints/{checkpoint_id}/manifest.json",
        manifest_size_bytes=len(manifest_data),
        manifest_hash=_sha256(manifest_data),
        checkpoint_created_at=created_at,
    )
