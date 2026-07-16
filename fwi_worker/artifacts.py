"""Controlled run-directory creation and artifact manifest helpers."""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from .config import configured_run_root
from .job_state import atomic_write_json


JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
SYNTHETIC_VALIDATION_DISCLAIMER = (
    "观测数据与反演传播均使用 Deepwave 生成，本次属于合成端到端/逆犯罪验证，"
    "主要用于验证系统调用、梯度、优化和结果展示流程，不能据此宣称对实际数据的"
    "普遍反演效果。"
)


def generate_job_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("fwi-%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


def validate_job_id(job_id: str) -> str:
    if not JOB_ID_RE.fullmatch(job_id):
        raise ValueError(
            "job_id must match ^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"
        )
    return job_id


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def prepare_run_dir(
    requested: str | Path | None,
    configured_job_id: str | None,
    *,
    managed_launch: bool = False,
) -> tuple[Path, str]:
    if type(managed_launch) is not bool:
        raise ValueError("managed_launch must be a boolean")
    root = configured_run_root()
    root.mkdir(parents=True, exist_ok=True)
    root_resolved = root.resolve(strict=True)
    if requested is None:
        job_id = validate_job_id(configured_job_id or generate_job_id())
        run_dir = root_resolved / job_id
    else:
        requested_path = Path(requested)
        if not requested_path.is_absolute():
            raise ValueError("--run-dir must be an absolute path")
        if requested_path.is_symlink():
            raise ValueError("--run-dir itself cannot be a symbolic link")
        # Resolve existing parents and any existing final component to prevent
        # a symlink from escaping FWI_RUN_ROOT.
        run_dir = requested_path.resolve(strict=False)
        job_id = validate_job_id(configured_job_id or run_dir.name)
        if run_dir.name != job_id:
            raise ValueError("run directory basename must equal job_id")
    if not _is_relative_to(run_dir, root_resolved) or run_dir == root_resolved:
        raise ValueError("run directory must be a child of FWI_RUN_ROOT")
    already_exists = run_dir.exists()
    if already_exists:
        if not run_dir.is_dir():
            raise ValueError("requested run path exists but is not a directory")
        status_path = run_dir / "status.json"
        if status_path.is_symlink() or not status_path.is_file():
            raise ValueError("existing run directory must contain a regular status.json")
        status = json.loads(status_path.read_text(encoding="utf-8"))
        if not isinstance(status, dict):
            raise ValueError("existing status.json root must be an object")
        if status.get("status") != "queued" or status.get("job_id") != job_id:
            raise ValueError(
                "existing run directory is only reusable for its matching queued job"
            )
        allowed_entries = {
            "status.json",
            "config.original.json",
            "run.log",
        }
        managed_control_entries = {
            ".worker-launch.json",
            ".worker-ready.json",
            ".worker-heartbeat.json",
        }
        if managed_launch:
            # Only the private bootstrap sets this flag, after validating the
            # exact ticket and both inherited kernel leases.  The legacy CLI
            # therefore cannot enter an Adapter-managed queued directory.
            allowed_entries.update(managed_control_entries)
        present_entries = {entry.name for entry in run_dir.iterdir()}
        if managed_launch and not managed_control_entries.issubset(present_entries):
            raise ValueError(
                "managed queued run is missing private launch evidence"
            )
        unexpected = sorted(present_entries - allowed_entries)
        if unexpected:
            raise ValueError(
                "queued run directory contains unexpected pre-existing artifacts: "
                + ", ".join(unexpected)
            )
        for entry in run_dir.iterdir():
            if entry.is_symlink():
                raise ValueError(
                    f"queued run directory entry cannot be a symbolic link: {entry.name}"
                )
            if entry.name in managed_control_entries:
                value = entry.lstat()
                if (
                    not stat.S_ISREG(value.st_mode)
                    or value.st_uid != os.geteuid()
                    or value.st_nlink != 1
                    or stat.S_IMODE(value.st_mode) & 0o077
                ):
                    raise ValueError(
                        "managed Worker control entry is not a private regular file"
                    )
        original_config = run_dir / "config.original.json"
        if not original_config.is_file():
            raise ValueError(
                "existing queued run directory must contain config.original.json"
            )
    else:
        run_dir.mkdir(parents=False)
    if run_dir.resolve(strict=True) != run_dir or not _is_relative_to(run_dir, root_resolved):
        raise ValueError("resolved run directory escapes FWI_RUN_ROOT")
    canonical_run_dir = run_dir.resolve(strict=True)
    for child in ("models", "data", "figures"):
        child_path = run_dir / child
        # exist_ok=False closes the simple race where a symlink is inserted
        # between validation and directory creation.
        child_path.mkdir(exist_ok=False)
        if child_path.is_symlink() or child_path.resolve(strict=True).parent != canonical_run_dir:
            raise ValueError(f"artifact subdirectory escaped run directory: {child}")
    return run_dir, job_id


def write_json(path: Path, value: Any) -> None:
    atomic_write_json(path, value)


def save_npy(path: Path, value: np.ndarray) -> None:
    """Atomically save an NPY without following a pre-existing target symlink."""

    requested_parent = path.parent
    parent = requested_parent.resolve(strict=True)
    if requested_parent.is_symlink() or requested_parent != parent:
        raise ValueError("NPY artifact parent cannot be a symbolic link")
    run_dir = parent.parent.resolve(strict=True)
    root = configured_run_root().resolve(strict=True)
    if (
        parent.name not in {"models", "data"}
        or run_dir == root
        or not _is_relative_to(run_dir, root)
    ):
        raise ValueError("NPY target must be in a controlled models/data directory")
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"refusing to overwrite existing NPY artifact: {path}")
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            np.save(stream, value, allow_pickle=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def artifact_url(job_id: str, relative_path: str) -> str:
    validate_job_id(job_id)
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("artifact path must be safe and relative")
    return f"/fwi-artifacts/{job_id}/{relative.as_posix()}"


def build_manifest(
    *,
    run_dir: Path,
    job_id: str,
    model_id: str,
    metrics: dict[str, Any],
    figures: list[dict[str, Any]],
    plot_details: dict[str, Any],
    command: str,
    status: str = "succeeded",
    failure_reason: str | None = None,
) -> dict[str, Any]:
    if status not in {"succeeded", "failed"}:
        raise ValueError("manifest status must be succeeded or failed")
    figure_entries = []
    for figure in figures:
        relative = figure["relative_path"]
        absolute = (run_dir / relative).resolve(strict=True)
        if not _is_relative_to(absolute, run_dir.resolve(strict=True)):
            raise ValueError("figure path escaped run directory")
        figure_entries.append(
            {
                "id": figure["id"],
                "title": figure["title"],
                "path": str(absolute),
                "url": artifact_url(job_id, relative),
                "mime_type": "image/png",
            }
        )
    return {
        "type": "fwi_result",
        "schema_version": "1",
        "job_id": job_id,
        "status": status,
        "model_id": model_id,
        "physics": "2d_acoustic_constant_density",
        "parameter": "vp",
        "true_model_known": True,
        "observed_data_origin": "Deepwave scalar synthetic true-model forward",
        "inversion_propagator": "Deepwave scalar",
        "command": command,
        "summary": (
            f"Deepwave 二维常密度声学 {command} 合成验证已完成；结果仅适用于该配置。"
            if status == "succeeded"
            else f"Deepwave 二维常密度声学 {command} 合成验证失败：{failure_reason}"
        ),
        "failure_reason": failure_reason,
        "disclaimer": SYNTHETIC_VALIDATION_DISCLAIMER,
        "metrics": metrics,
        "plot_details": plot_details,
        "figures": figure_entries,
    }


def build_partial_failure_manifest(
    *,
    job_id: str,
    model_id: str,
    command: str,
    metrics: dict[str, Any],
    error: Exception,
) -> dict[str, Any]:
    reason = f"{type(error).__name__}: {error}"
    return {
        "type": "fwi_result",
        "schema_version": "1",
        "job_id": job_id,
        "status": "failed",
        "model_id": model_id,
        "physics": "2d_acoustic_constant_density",
        "parameter": "vp",
        "command": command,
        "summary": f"Deepwave 二维常密度声学 {command} 合成验证失败：{reason}",
        "failure_reason": reason,
        "disclaimer": SYNTHETIC_VALIDATION_DISCLAIMER,
        "metrics": metrics,
        "plot_details": {},
        "figures": [],
    }
