"""Controlled P1.2a Algorithm Adapter for the fixed Deepwave FWI baseline.

The adapter deliberately supports only the single-node ``acoustic_fwi_2d``
slice.  The packaged v1 manifest also describes a legacy forward operation,
but that Worker writes the initial model to ``models/inverted.npy``.  Exposing
that file as an inverted-model output would be scientifically misleading, so
the standard adapter keeps forward unavailable until its output contract is
versioned correctly.  The existing MCP forward entry point remains unchanged.

This module is not a scheduler or a second task database.  SQLite remains the
authoritative task state.  The small, private index below exists only to make
Worker submission idempotent across adapter instances.  It never scans the run
root for executable work, and an incomplete submission is left for the P2
reconciliation design rather than being launched again speculatively.
"""

from __future__ import annotations

import copy
import csv
import fcntl
import hashlib
import io
import json
import math
import os
import re
import selectors
import stat
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from jsonschema import Draft7Validator

from scientific_runtime_contracts import schema_errors

from .fwi_registry import load_deepwave_manifest


ALGORITHM_ID = "deepwave.acoustic_fwi"
ALGORITHM_VERSION = "1.0.0"
ADAPTER_VERSION = "1.0.0"
LOGICAL_ENTRYPOINT = "fwi.deepwave_adapter"
MODEL_ID = "marmousi_94_288"
BOUND_MANIFEST_HASH = (
    "sha256:20c22a2c54259622435850b05eb7eeb020ff4d74af2cec51439aa465793f8dcd"
)
CONTROL_DIRECTORY = ".scientific-runtime-adapter-v1"
MAX_JSON_BYTES = 8 * 1024 * 1024
# The only standard P1 output is a 94 x 288 float32 array (~106 KiB).  Keep
# enough room for the NPY header without permitting a Worker-controlled shape
# declaration to turn collection into a large-memory operation.
MAX_NPY_BYTES = 128 * 1024
MAX_CSV_BYTES = 8 * 1024 * 1024
MAX_PROBE_OUTPUT_BYTES = 1024 * 1024
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKER_PYTHON = Path("/root/.venvs/cpp-fwi-agent/bin/python")
PROBE_SLOTS = threading.BoundedSemaphore(value=2)

OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
PLAN_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
NODE_IDEMPOTENCY_KEY = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$"
)
JOB_ID = re.compile(r"^fwi-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$")


class AdapterError(RuntimeError):
    """Base class for stable Deepwave Adapter failures."""

    def __init__(self, message: str):
        prefix = message.split(":", 1)[0]
        self.code = (
            prefix
            if re.fullmatch(r"[A-Z][A-Z0-9_]*", prefix)
            else "ADAPTER_ERROR"
        )
        super().__init__(message)


class AdapterValidationError(AdapterError, ValueError):
    """The typed request is invalid or outside the P1 capability boundary."""

    def __init__(self, code: str, errors: list[str] | tuple[str, ...]):
        self.code = code
        self.errors = tuple(errors)
        super().__init__(f"{code}: {'; '.join(self.errors)}")


class AdapterIdempotencyConflict(AdapterError):
    """An idempotency key is already bound to another immutable request."""


class AdapterHandleError(AdapterError, ValueError):
    """A handle is malformed, unknown, or inconsistent with private state."""


class AdapterStatusError(AdapterError):
    """Worker status evidence is missing or malformed."""


class AdapterArtifactError(AdapterError):
    """A Worker artifact is unavailable, unsafe, or semantically invalid."""


class AdapterUnavailable(AdapterError):
    """A trusted runtime dependency or launch operation is unavailable."""


@dataclass(frozen=True)
class AdapterValidation:
    project_id: str
    principal_id: str
    algorithm: dict[str, str]
    dataset: dict[str, Any]
    dataset_access_scope: dict[str, Any]
    task_type: str
    parameters: dict[str, Any]
    resources: dict[str, Any]
    command: str
    worker_config: dict[str, Any]
    normalized_config_hash: str
    device_details: dict[str, Any]
    fingerprint: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return copy.deepcopy(
            {
                "project_id": self.project_id,
                "principal_id": self.principal_id,
                "algorithm": self.algorithm,
                "dataset": self.dataset,
                "dataset_access_scope": self.dataset_access_scope,
                "task_type": self.task_type,
                "parameters": self.parameters,
                "resources": self.resources,
                "command": self.command,
                "worker_config": self.worker_config,
                "normalized_config_hash": self.normalized_config_hash,
                "device_details": self.device_details,
                "fingerprint": self.fingerprint,
            }
        )


@dataclass(frozen=True)
class AdapterEstimate:
    normalized_config_hash: str
    requested_resources: dict[str, Any]
    policy_limits: dict[str, Any]
    estimated_wall_time_seconds: None
    basis: str
    limitations: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "normalized_config_hash": self.normalized_config_hash,
            "requested_resources": copy.deepcopy(self.requested_resources),
            "policy_limits": copy.deepcopy(self.policy_limits),
            "estimated_wall_time_seconds": self.estimated_wall_time_seconds,
            "basis": self.basis,
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True)
class AdapterHandle:
    submission_id: str
    task_id: str
    node_id: str
    job_id: str
    idempotency_key: str
    plan_hash: str
    request_hash: str
    algorithm: dict[str, str]
    fingerprint: dict[str, Any]
    adapter_version: str = ADAPTER_VERSION

    def as_dict(self) -> dict[str, Any]:
        return copy.deepcopy(
            {
                "submission_id": self.submission_id,
                "task_id": self.task_id,
                "node_id": self.node_id,
                "job_id": self.job_id,
                "idempotency_key": self.idempotency_key,
                "plan_hash": self.plan_hash,
                "request_hash": self.request_hash,
                "algorithm": self.algorithm,
                "fingerprint": self.fingerprint,
                "adapter_version": self.adapter_version,
            }
        )


@dataclass(frozen=True)
class AdapterStatus:
    job_id: str
    task_id: str
    node_id: str
    status: str
    stage: str
    completed: int
    total: int
    message: str
    updated_at: str
    terminal: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "task_id": self.task_id,
            "node_id": self.node_id,
            "status": self.status,
            "stage": self.stage,
            "completed": self.completed,
            "total": self.total,
            "message": self.message,
            "updated_at": self.updated_at,
            "terminal": self.terminal,
        }


@dataclass(frozen=True)
class AdapterCancelResult:
    supported: bool
    accepted: bool
    code: str
    status: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "supported": self.supported,
            "accepted": self.accepted,
            "code": self.code,
            "status": self.status,
        }


class WorkerLauncher(Protocol):
    """Trusted, non-user-selectable launcher boundary used by submit()."""

    def launch(
        self,
        *,
        command: str,
        config_path: Path,
        run_dir: Path,
        run_root: Path,
    ) -> Any: ...


def _sanitized_worker_environment(
    python_executable: Path, *, run_root: Path | None = None
) -> dict[str, str]:
    environment = {
        "PYTHONPATH": str(PROJECT_ROOT),
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PATH": (
            f"{python_executable.parent}:"
            "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        ),
        "HOME": "/root",
        "LANG": "C.UTF-8",
    }
    if run_root is not None:
        environment["FWI_RUN_ROOT"] = str(run_root)
    for name in (
        "CUDA_VISIBLE_DEVICES",
        "NVIDIA_VISIBLE_DEVICES",
        "NVIDIA_DRIVER_CAPABILITIES",
        "LD_LIBRARY_PATH",
        "TMPDIR",
        "OMP_NUM_THREADS",
    ):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    return environment


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _parse_timestamp(value: str, *, code: str) -> datetime:
    if not isinstance(value, str):
        raise AdapterStatusError(f"{code}: timestamp must be a string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise AdapterStatusError(f"{code}: timestamp is not RFC3339") from error
    if parsed.tzinfo is None:
        raise AdapterStatusError(f"{code}: timestamp must include an offset")
    return parsed.astimezone(timezone.utc)


def _stable_json_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise AdapterValidationError(
            "ADAPTER_REQUEST_INVALID", [f"request is not canonical JSON: {error}"]
        ) from error


def _sha256_document(value: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_stable_json_bytes(value)).hexdigest()


DIRECTORY_OPEN_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)


def _absolute_path(value: Path) -> Path:
    requested = Path(value).expanduser()
    if (
        not requested.is_absolute()
        or ".." in requested.parts
        or requested.name in {"", ".", ".."}
    ):
        raise OSError("path must be absolute and normalized beneath a named entry")
    return Path(os.path.normpath(str(requested)))


def _open_directory_fd(path: Path) -> int:
    """Open one absolute directory inode without following any path symlink."""

    candidate = _absolute_path(path)
    descriptor = os.open("/", DIRECTORY_OPEN_FLAGS)
    try:
        for part in candidate.parts[1:]:
            next_descriptor = os.open(
                part, DIRECTORY_OPEN_FLAGS, dir_fd=descriptor
            )
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _safe_parent_fd(path: Path) -> tuple[Path, int]:
    candidate = _absolute_path(path)
    if candidate.parent == candidate:
        raise OSError("path has no usable parent")
    return candidate, _open_directory_fd(candidate.parent)


def _parent_allows_owned_child(parent_status: os.stat_result) -> bool:
    mode = stat.S_IMODE(parent_status.st_mode)
    if parent_status.st_uid not in {0, os.geteuid()}:
        return False
    if not mode & 0o022:
        return True
    # Root-owned sticky directories such as /tmp cannot have an euid-owned
    # child renamed by another unprivileged user.
    return parent_status.st_uid == 0 and bool(mode & stat.S_ISVTX)


def _validate_run_root(value: Path, *, create: bool) -> Path:
    try:
        candidate = _absolute_path(value)
    except OSError as error:
        raise AdapterValidationError(
            "RUN_ROOT_INVALID", ["run root must be an absolute non-symlink path"]
        ) from error
    if candidate == Path("/") or candidate.parent == Path("/"):
        raise AdapterValidationError(
            "RUN_ROOT_INVALID", ["run root must be a dedicated nested directory"]
        )
    forbidden = tuple(
        Path(item)
        for item in (
            "/etc",
            "/usr",
            "/bin",
            "/sbin",
            "/lib",
            "/lib32",
            "/lib64",
            "/boot",
            "/proc",
            "/sys",
            "/dev",
            "/run",
        )
    )
    if any(candidate == root or _is_relative_to(candidate, root) for root in forbidden):
        raise AdapterValidationError(
            "RUN_ROOT_INVALID", ["run root overlaps a sensitive system directory"]
        )
    project_root = PROJECT_ROOT.resolve(strict=True)
    home = Path.home().resolve(strict=True)
    if (
        candidate == Path("/var")
        or candidate == project_root
        or _is_relative_to(candidate, project_root)
        or _is_relative_to(project_root, candidate)
        or candidate == home
        or _is_relative_to(home, candidate)
    ):
        raise AdapterValidationError(
            "RUN_ROOT_INVALID", ["run root overlaps a protected directory"]
        )
    parent_descriptor = -1
    root_descriptor = -1
    try:
        _, parent_descriptor = _safe_parent_fd(candidate)
        parent_status = os.fstat(parent_descriptor)
        if not _parent_allows_owned_child(parent_status):
            raise OSError("run root parent does not protect an owned child")
        try:
            root_descriptor = os.open(
                candidate.name, DIRECTORY_OPEN_FLAGS, dir_fd=parent_descriptor
            )
        except FileNotFoundError:
            if not create:
                return candidate
            try:
                os.mkdir(candidate.name, mode=0o700, dir_fd=parent_descriptor)
                os.fsync(parent_descriptor)
            except FileExistsError:
                # A concurrent first submit may have created the same root.
                # Re-open through the already trusted parent FD and apply the
                # same owner/mode checks; never accept a symlink replacement.
                pass
            root_descriptor = os.open(
                candidate.name, DIRECTORY_OPEN_FLAGS, dir_fd=parent_descriptor
            )
        root_status = os.fstat(root_descriptor)
        if (
            not stat.S_ISDIR(root_status.st_mode)
            or root_status.st_uid != os.geteuid()
            or stat.S_IMODE(root_status.st_mode) & 0o022
        ):
            raise OSError("run root ownership or permissions are unsafe")
    except OSError as error:
        raise AdapterValidationError(
            "RUN_ROOT_INVALID",
            ["run root must be an owned, protected, non-symlink directory"],
        ) from error
    finally:
        if root_descriptor >= 0:
            os.close(root_descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)
    return candidate


def _ensure_private_directory(path: Path) -> Path:
    parent_descriptor = -1
    directory_descriptor = -1
    try:
        candidate, parent_descriptor = _safe_parent_fd(path)
        try:
            os.mkdir(candidate.name, mode=0o700, dir_fd=parent_descriptor)
            os.fsync(parent_descriptor)
        except FileExistsError:
            pass
        directory_descriptor = os.open(
            candidate.name, DIRECTORY_OPEN_FLAGS, dir_fd=parent_descriptor
        )
        link_status = os.fstat(directory_descriptor)
    except OSError as error:
        raise AdapterUnavailable(
            f"ADAPTER_STATE_UNAVAILABLE: cannot create {path.name}"
        ) from error
    finally:
        if directory_descriptor >= 0:
            os.close(directory_descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)
    if (
        not stat.S_ISDIR(link_status.st_mode)
        or link_status.st_uid != os.geteuid()
        or stat.S_IMODE(link_status.st_mode) & 0o077
    ):
        raise AdapterUnavailable(
            f"ADAPTER_STATE_UNAVAILABLE: {path.name} is not a private owned directory"
        )
    return candidate


def _create_private_directory(path: Path) -> Path:
    parent_descriptor = -1
    directory_descriptor = -1
    try:
        candidate, parent_descriptor = _safe_parent_fd(path)
        os.mkdir(candidate.name, mode=0o700, dir_fd=parent_descriptor)
        os.fsync(parent_descriptor)
        directory_descriptor = os.open(
            candidate.name, DIRECTORY_OPEN_FLAGS, dir_fd=parent_descriptor
        )
        value = os.fstat(directory_descriptor)
        if (
            not stat.S_ISDIR(value.st_mode)
            or value.st_uid != os.geteuid()
            or stat.S_IMODE(value.st_mode) & 0o077
        ):
            raise OSError("new directory is not private")
        os.fsync(directory_descriptor)
        return candidate
    finally:
        if directory_descriptor >= 0:
            os.close(directory_descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)


def _require_private_directory(path: Path, *, parent: Path) -> Path:
    if path.parent != parent:
        raise AdapterHandleError(
            f"ADAPTER_HANDLE_INVALID: {path.name} escaped its parent"
        )
    parent_descriptor = -1
    directory_descriptor = -1
    try:
        candidate, parent_descriptor = _safe_parent_fd(path)
        directory_descriptor = os.open(
            candidate.name, DIRECTORY_OPEN_FLAGS, dir_fd=parent_descriptor
        )
        link_status = os.fstat(directory_descriptor)
        if (
            not stat.S_ISDIR(link_status.st_mode)
            or link_status.st_uid != os.geteuid()
            or stat.S_IMODE(link_status.st_mode) & 0o077
        ):
            raise AdapterHandleError(
                f"ADAPTER_HANDLE_INVALID: {path.name} is not a private regular directory"
            )
    except OSError as error:
        raise AdapterHandleError(
            f"ADAPTER_HANDLE_INVALID: {path.name} is unavailable"
        ) from error
    finally:
        if directory_descriptor >= 0:
            os.close(directory_descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)
    return candidate


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    directory_descriptor = -1
    descriptor = -1
    temp_name = ""
    try:
        candidate, directory_descriptor = _safe_parent_fd(path)
        for _ in range(100):
            temp_name = f".{candidate.name}.{os.urandom(16).hex()}"
            try:
                descriptor = os.open(
                    temp_name,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0),
                    0o600,
                    dir_fd=directory_descriptor,
                )
                break
            except FileExistsError:
                continue
        if descriptor < 0:
            raise AdapterUnavailable(
                "ADAPTER_STATE_UNAVAILABLE: cannot allocate an atomic state file"
            )
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(
            temp_name,
            candidate.name,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
        )
        temp_name = ""
        os.fsync(directory_descriptor)
    except AdapterError:
        raise
    except (OSError, TypeError, ValueError) as error:
        raise AdapterUnavailable(
            "ADAPTER_STATE_UNAVAILABLE: atomic state write failed"
        ) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temp_name:
            try:
                os.unlink(temp_name, dir_fd=directory_descriptor)
            except (FileNotFoundError, OSError):
                pass
        if directory_descriptor >= 0:
            os.close(directory_descriptor)


def _read_json_file(
    path: Path,
    *,
    code: str,
    max_bytes: int = MAX_JSON_BYTES,
    private: bool = False,
) -> dict[str, Any]:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    directory_descriptor = -1
    try:
        candidate, directory_descriptor = _safe_parent_fd(path)
        descriptor = os.open(candidate.name, flags, dir_fd=directory_descriptor)
    except OSError as error:
        raise AdapterStatusError(f"{code}: JSON file is unavailable") from error
    finally:
        if directory_descriptor >= 0:
            os.close(directory_descriptor)
    try:
        file_status = os.fstat(descriptor)
        if (
            not stat.S_ISREG(file_status.st_mode)
            or file_status.st_size > max_bytes
            or (
                private
                and (
                    file_status.st_uid != os.geteuid()
                    or stat.S_IMODE(file_status.st_mode) & 0o077
                )
            )
        ):
            raise AdapterStatusError(f"{code}: JSON file is not a bounded regular file")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > max_bytes:
            raise AdapterStatusError(f"{code}: JSON file is too large")
    finally:
        os.close(descriptor)
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AdapterStatusError(f"{code}: JSON file is malformed") from error
    if not isinstance(value, dict):
        raise AdapterStatusError(f"{code}: JSON root must be an object")
    return value


class SafeSubprocessWorkerLauncher:
    """Fixed argv, secret-minimizing, process-local two-job launcher.

    The active counter is intentionally described as a P1 process-local bound,
    not a global scheduler.  P2 will own durable process identity, cancellation,
    lease and restart reconciliation.
    """

    _state_lock = threading.Lock()
    _process_active = 0

    def __init__(
        self,
        *,
        python_executable: Path | None = None,
        project_root: Path = PROJECT_ROOT,
        max_active: int = 2,
    ) -> None:
        self._python = Path(python_executable or DEFAULT_WORKER_PYTHON)
        self._project_root = Path(project_root).resolve(strict=True)
        if (
            not self._python.is_absolute()
            or not self._python.is_file()
            or not os.access(self._python, os.X_OK)
        ):
            raise AdapterUnavailable(
                "WORKER_PYTHON_UNAVAILABLE: configured Python is not executable"
            )
        if max_active < 1:
            raise ValueError("max_active must be positive")
        self._max_active = max_active

    @property
    def python_executable(self) -> Path:
        """Return the fixed runtime used for the numerical child."""

        return self._python

    def _reserve(self) -> None:
        with self._state_lock:
            if type(self)._process_active >= self._max_active:
                raise AdapterUnavailable(
                    "ADAPTER_CONCURRENCY_LIMIT: process-local Worker limit reached"
                )
            type(self)._process_active += 1

    def _release(self) -> None:
        with self._state_lock:
            type(self)._process_active = max(0, type(self)._process_active - 1)

    def _child_environment(self, run_root: Path) -> dict[str, str]:
        return _sanitized_worker_environment(self._python, run_root=run_root)

    @staticmethod
    def _mark_unexpected_exit(run_dir: Path, return_code: int) -> None:
        status_path = run_dir / "status.json"
        try:
            value = _read_json_file(status_path, code="WORKER_STATUS_INVALID")
            if value.get("status") == "failed" or (
                value.get("status") == "succeeded" and return_code == 0
            ):
                return
            value.update(
                {
                    "status": "failed",
                    "stage": "worker_exit",
                    "message": f"FWI worker exited with code {return_code}",
                    "updated_at": _utc_now(),
                }
            )
            _atomic_write_json(status_path, value)
        except Exception:
            # Status is Worker evidence, while SQLite remains task truth.  A
            # malformed/missing file is surfaced by status() rather than being
            # replaced with invented success evidence.
            return

    def launch(
        self,
        *,
        command: str,
        config_path: Path,
        run_dir: Path,
        run_root: Path,
    ) -> int:
        if command != "invert":
            raise AdapterValidationError(
                "TASK_TYPE_UNSUPPORTED_IN_P1",
                ["the standard P1 Adapter launches only inversion"],
            )
        self._reserve()
        log_path = run_dir / "run.log"
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_APPEND
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        descriptor = -1
        directory_descriptor = -1
        try:
            candidate, directory_descriptor = _safe_parent_fd(log_path)
            descriptor = os.open(
                candidate.name, flags, 0o600, dir_fd=directory_descriptor
            )
            argv = [
                str(self._python),
                "-m",
                "fwi_worker",
                "invert",
                "--config",
                str(config_path),
                "--run-dir",
                str(run_dir),
            ]
            process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=descriptor,
                stderr=subprocess.STDOUT,
                cwd=str(self._project_root),
                env=self._child_environment(run_root),
                close_fds=True,
                shell=False,
            )
        except Exception:
            self._release()
            raise
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if directory_descriptor >= 0:
                os.close(directory_descriptor)

        def reap() -> None:
            try:
                return_code = process.wait()
                self._mark_unexpected_exit(run_dir, return_code)
            finally:
                self._release()

        try:
            threading.Thread(target=reap, name="fwi-adapter-reaper", daemon=True).start()
        except Exception:
            process.terminate()
            process.wait()
            self._release()
            raise
        return int(process.pid)


def _fixed_worker_probe(*arguments: str) -> dict[str, Any]:
    python = DEFAULT_WORKER_PYTHON
    if not python.is_file() or not os.access(python, os.X_OK):
        raise AdapterUnavailable(
            "WORKER_PYTHON_UNAVAILABLE: fixed FWI Python is not executable"
        )
    if not PROBE_SLOTS.acquire(timeout=5):
        raise AdapterUnavailable(
            "WORKER_PROBE_BUSY: fixed runtime probe capacity is exhausted"
        )
    process: subprocess.Popen[bytes] | None = None
    output = b""
    try:
        process = subprocess.Popen(
            [str(python), "-m", "fwi_worker.adapter_probe", *arguments],
            cwd=PROJECT_ROOT,
            env=_sanitized_worker_environment(python),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            shell=False,
        )
        if process.stdout is None:
            raise OSError("probe stdout pipe is unavailable")
        descriptor = process.stdout.fileno()
        os.set_blocking(descriptor, False)
        chunks: list[bytes] = []
        total = 0
        deadline = time.monotonic() + 60
        with selectors.DefaultSelector() as selector:
            selector.register(descriptor, selectors.EVENT_READ)
            while True:
                remaining_time = deadline - time.monotonic()
                if remaining_time <= 0:
                    raise subprocess.TimeoutExpired(process.args, 60)
                events = selector.select(timeout=remaining_time)
                if not events:
                    raise subprocess.TimeoutExpired(process.args, 60)
                chunk = os.read(
                    descriptor,
                    min(64 * 1024, MAX_PROBE_OUTPUT_BYTES + 1 - total),
                )
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > MAX_PROBE_OUTPUT_BYTES:
                    raise AdapterUnavailable(
                        "WORKER_PROBE_INVALID: probe output is too large"
                    )
        output = b"".join(chunks)
        remaining_time = max(0.001, deadline - time.monotonic())
        return_code = process.wait(timeout=remaining_time)
    except AdapterError:
        raise
    except subprocess.TimeoutExpired as error:
        raise AdapterUnavailable(
            "WORKER_PROBE_TIMEOUT: fixed runtime probe exceeded 60 seconds"
        ) from error
    except (OSError, subprocess.SubprocessError) as error:
        raise AdapterUnavailable(
            "WORKER_PROBE_UNAVAILABLE: fixed runtime probe could not start"
        ) from error
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        if process is not None and process.stdout is not None:
            process.stdout.close()
        PROBE_SLOTS.release()
    if return_code != 0:
        raise AdapterUnavailable(
            "WORKER_PROBE_FAILED: fixed runtime rejected the requested evidence probe"
        )
    try:
        value = json.loads(output.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AdapterUnavailable("WORKER_PROBE_INVALID: probe output is malformed") from error
    if not isinstance(value, dict):
        raise AdapterUnavailable("WORKER_PROBE_INVALID: probe output must be an object")
    return value


def _default_dataset_identity_provider() -> dict[str, Any]:
    value = _fixed_worker_probe("dataset").get("dataset")
    if not isinstance(value, dict):
        raise AdapterUnavailable("WORKER_PROBE_INVALID: dataset evidence is missing")
    return value


def _default_device_validator(device: str) -> dict[str, Any]:
    value = _fixed_worker_probe("runtime", "--device", device).get("device_details")
    if not isinstance(value, dict) or value.get("device") != device:
        raise AdapterUnavailable("WORKER_PROBE_INVALID: device evidence is missing")
    return value


def _git_source_evidence() -> dict[str, Any]:
    source: dict[str, Any] = {"identity_complete": False, "dirty": None}
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        tree = subprocess.run(
            ["git", "rev-parse", "HEAD^{tree}"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        porcelain = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
        if re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", commit):
            source["git_commit"] = commit
        if re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", tree):
            source["git_tree"] = tree
        source["dirty"] = bool(porcelain)
    except (OSError, subprocess.SubprocessError):
        pass
    return source


def _default_fingerprint_factory(
    *,
    algorithm: Mapping[str, str],
    normalized_config_hash: str,
    input_hashes: list[str],
    seed: int,
    device: str,
    device_details: Mapping[str, Any],
) -> dict[str, Any]:
    # The legacy child does not enable deterministic algorithms.  Never copy
    # a potentially different flag from the control process into its evidence.
    deterministic = False
    cudnn_deterministic = False
    known = [
        "The legacy Worker records seed but does not consume it in the numerical path.",
        "Bitwise equality across library, driver, CPU, or GPU versions is not promised.",
        "The environment hash is an installed-package snapshot, not a rebuildable lock.",
    ]
    return {
        "provenance_mode": "development",
        "algorithm": dict(algorithm),
        "adapter_version": ADAPTER_VERSION,
        "source": _git_source_evidence(),
        "environment": {
            "environment_lock_hash": device_details[
                "development_environment_snapshot_hash"
            ]
        },
        "runtime": copy.deepcopy(device_details["runtime"]),
        "seed": seed,
        "hardware": {
            "device": device,
            "device_name": str(device_details.get("device_name") or device),
            "compute_capability": device_details.get("compute_capability"),
        },
        "normalized_config_hash": normalized_config_hash,
        "input_hashes": list(input_hashes),
        "determinism": {
            "requested": False,
            "framework_deterministic": deterministic,
            "flags": {
                "torch_deterministic_algorithms": deterministic,
                "cudnn_deterministic": cudnn_deterministic,
            },
            "known_nondeterminism": known,
        },
    }


class DeepwaveAdapter:
    """Algorithm Adapter v1 for one fixed, registered Marmousi FWI node."""

    def __init__(
        self,
        *,
        run_root: Path | str | None = None,
        launcher: WorkerLauncher | None = None,
        dataset_identity_provider: Callable[[], Mapping[str, Any]] = (
            _default_dataset_identity_provider
        ),
        registry_snapshot_provider: Callable[..., Mapping[str, Any]] | None = None,
        device_validator: Callable[[str], Mapping[str, Any] | None] = (
            _default_device_validator
        ),
        fingerprint_factory: Callable[..., Mapping[str, Any]] = (
            _default_fingerprint_factory
        ),
        clock: Callable[[], str] = _utc_now,
    ) -> None:
        configured = Path(
            run_root
            if run_root is not None
            else os.environ.get("FWI_RUN_ROOT", "/root/fwi-runs")
        )
        # Validation is read-only when the deployment root already exists.
        self._run_root = _validate_run_root(configured, create=False)
        self._launcher = launcher or SafeSubprocessWorkerLauncher()
        if (
            isinstance(self._launcher, SafeSubprocessWorkerLauncher)
            # A venv and the system interpreter can resolve to the same binary
            # while selecting different sys.prefix/site-packages.  Bind the
            # exact configured venv entry path, not only its symlink target.
            and self._launcher.python_executable != DEFAULT_WORKER_PYTHON
        ):
            raise AdapterUnavailable(
                "WORKER_RUNTIME_MISMATCH: Adapter evidence and Worker must use the fixed FWI runtime"
            )
        self._dataset_identity_provider = dataset_identity_provider
        self._registry_snapshot_provider = registry_snapshot_provider
        self._device_validator = device_validator
        self._fingerprint_factory = fingerprint_factory
        self._clock = clock
        self._manifest = load_deepwave_manifest()
        if _sha256_document(self._manifest) != BOUND_MANIFEST_HASH:
            raise AdapterUnavailable(
                "ADAPTER_MANIFEST_MISMATCH: Adapter v1 is not bound to this manifest"
            )

    @staticmethod
    def _validate_algorithm(algorithm: Mapping[str, Any]) -> dict[str, str]:
        if not isinstance(algorithm, Mapping) or set(algorithm) != {"id", "version"}:
            raise AdapterValidationError(
                "ALGORITHM_IDENTITY_INVALID",
                ["algorithm must contain only id and version"],
            )
        value = {"id": algorithm.get("id"), "version": algorithm.get("version")}
        if value != {"id": ALGORITHM_ID, "version": ALGORITHM_VERSION}:
            raise AdapterValidationError(
                "ALGORITHM_VERSION_UNAVAILABLE",
                [f"Adapter is bound to {ALGORITHM_ID}@{ALGORITHM_VERSION}"],
            )
        return value  # type: ignore[return-value]

    def _validate_dataset(
        self,
        dataset: Mapping[str, Any],
        *,
        project_id: str,
        principal_id: str,
        verify_local: bool,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if not isinstance(dataset, Mapping):
            raise AdapterValidationError(
                "DATASET_INVALID", ["dataset must be a DatasetRef object"]
            )
        supplied = copy.deepcopy(dict(dataset))
        errors = schema_errors("dataset-ref.schema.json", supplied)
        if errors:
            raise AdapterValidationError("DATASET_INVALID", errors)
        scope = supplied["access_scope"]
        if (
            scope["project_id"] != project_id
            or principal_id not in scope["principals"]
            or "execute" not in scope["permissions"]
        ):
            raise AdapterValidationError(
                "DATASET_ACCESS_DENIED",
                ["current project/principal must have registered execute access"],
            )
        if (
            supplied["id"] != MODEL_ID
            or supplied["version"] != "1.0.0"
            or supplied["data_type"] != "velocity_model_2d"
        ):
            raise AdapterValidationError(
                "DATASET_IDENTITY_MISMATCH",
                ["P1 Adapter is bound to marmousi_94_288@1.0.0"],
            )
        identity = {
                key: supplied[key]
                for key in ("id", "version", "content_hash", "data_type")
            }
        access_scope = copy.deepcopy(scope)
        if not verify_local:
            return identity, access_scope
        if self._registry_snapshot_provider is None:
            raise AdapterUnavailable(
                "REGISTRY_SNAPSHOT_PROVIDER_REQUIRED: first execution must bind a server-resolved DatasetRef"
            )
        try:
            registered_value = copy.deepcopy(
                dict(
                    self._registry_snapshot_provider(
                        project_id=project_id,
                        principal_id=principal_id,
                        dataset_id=supplied["id"],
                        dataset_version=supplied["version"],
                    )
                )
            )
        except Exception as error:
            raise AdapterUnavailable(
                f"REGISTRY_SNAPSHOT_UNAVAILABLE: {type(error).__name__}"
            ) from error
        registered_errors = schema_errors("dataset-ref.schema.json", registered_value)
        if registered_errors:
            raise AdapterUnavailable(
                "REGISTRY_SNAPSHOT_INVALID: trusted registry value is not a DatasetRef"
            )
        if supplied != registered_value:
            raise AdapterValidationError(
                "DATASET_REGISTRY_MISMATCH",
                ["DatasetRef differs from the server-resolved Registry snapshot"],
            )
        try:
            trusted_value = copy.deepcopy(dict(self._dataset_identity_provider()))
        except Exception as error:
            raise AdapterUnavailable(
                f"DATASET_VERIFICATION_UNAVAILABLE: {type(error).__name__}"
            ) from error
        trusted_errors = schema_errors("dataset-ref.schema.json", trusted_value)
        if trusted_errors:
            raise AdapterUnavailable(
                "DATASET_VERIFICATION_INVALID: trusted identity is not a DatasetRef"
            )
        core_fields = (
            "schema_version",
            "id",
            "version",
            "content_hash",
            "data_type",
            "immutable",
            "metadata",
            "lineage",
            "extensions",
        )
        mismatches = [field for field in core_fields if supplied[field] != trusted_value[field]]
        if mismatches:
            raise AdapterValidationError(
                "DATASET_IDENTITY_MISMATCH",
                ["registered dataset differs from verified local input: " + ", ".join(mismatches)],
            )
        return identity, access_scope

    def _validate_parameters(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(parameters, Mapping):
            raise AdapterValidationError(
                "PARAMETERS_INVALID", ["parameters must be an object"]
            )
        value = copy.deepcopy(dict(parameters))
        errors = sorted(
            self._manifest_parameter_validator().iter_errors(value),
            key=lambda error: (list(error.absolute_path), error.message),
        )
        rendered = [
            "/" + "/".join(str(part) for part in error.absolute_path) + ": " + error.message
            for error in errors
        ]
        if rendered:
            raise AdapterValidationError("PARAMETERS_INVALID", rendered)
        if type(value["seed"]) is not int or not 0 <= value["seed"] <= 2_147_483_647:
            raise AdapterValidationError(
                "PARAMETERS_INVALID", ["seed must be a strict integer in 0..2147483647"]
            )
        if type(value["iterations"]) is not int:
            raise AdapterValidationError(
                "PARAMETERS_INVALID", ["iterations must be a strict integer"]
            )
        if value["preset"] not in {"fwi_smoke", "fwi_demo"}:
            raise AdapterValidationError(
                "TASK_TYPE_UNSUPPORTED_IN_P1",
                ["P1 standard Adapter supports only inversion presets"],
            )
        if not 1 <= value["iterations"] <= 100:
            raise AdapterValidationError(
                "PARAMETERS_INVALID", ["inversion iterations must be in 1..100"]
            )
        return value

    def _manifest_parameter_validator(self) -> Draft7Validator:
        return Draft7Validator(self._manifest["parameter_schema"])

    def _validate_resources(
        self, resources: Mapping[str, Any], *, device: str
    ) -> dict[str, Any]:
        expected = {
            "device",
            "gpu_count",
            "cpu_cores",
            "memory_mb",
            "wall_time_seconds",
        }
        if not isinstance(resources, Mapping) or set(resources) != expected:
            raise AdapterValidationError(
                "RESOURCES_INVALID",
                ["resources must contain exactly the v1 resource fields"],
            )
        value = copy.deepcopy(dict(resources))
        if value["device"] != device:
            raise AdapterValidationError(
                "RESOURCE_DEVICE_MISMATCH",
                ["resource device must equal the parameter device"],
            )
        limits = self._manifest["resource_limits"]
        integer_fields = {
            "gpu_count": (0, limits["max_gpu_count"]),
            "cpu_cores": (1, limits["max_cpu_cores"]),
            "memory_mb": (256, limits["max_memory_mb"]),
            "wall_time_seconds": (1, limits["max_wall_time_seconds"]),
        }
        if value["device"] not in limits["devices"]:
            raise AdapterValidationError(
                "RESOURCE_UNSUPPORTED", ["device is not declared by the manifest"]
            )
        for field, (minimum, maximum) in integer_fields.items():
            item = value[field]
            if type(item) is not int or not minimum <= item <= maximum:
                raise AdapterValidationError(
                    "RESOURCE_LIMIT_EXCEEDED",
                    [f"{field} must be a strict integer in {minimum}..{maximum}"],
                )
        expected_gpu = 1 if value["device"] == "cuda" else 0
        if value["gpu_count"] != expected_gpu:
            raise AdapterValidationError(
                "RESOURCE_DEVICE_MISMATCH",
                [f"{value['device']} requires gpu_count={expected_gpu}"],
            )
        return value

    def _validate_request(
        self,
        *,
        project_id: str,
        principal_id: str,
        algorithm: Mapping[str, Any],
        dataset: Mapping[str, Any],
        task_type: str,
        parameters: Mapping[str, Any],
        resources: Mapping[str, Any],
        verify_runtime: bool,
    ) -> AdapterValidation:
        for name, value in (("project_id", project_id), ("principal_id", principal_id)):
            if not isinstance(value, str) or OPAQUE_ID.fullmatch(value) is None:
                raise AdapterValidationError(
                    "AUTH_SCOPE_INVALID", [f"{name} must be a v1 opaque identifier"]
                )
        algorithm_value = self._validate_algorithm(algorithm)
        if task_type != "acoustic_fwi_2d":
            raise AdapterValidationError(
                "TASK_TYPE_UNSUPPORTED_IN_P1",
                ["P1 standard Adapter supports only acoustic_fwi_2d"],
            )
        dataset_identity, dataset_access_scope = self._validate_dataset(
            dataset,
            project_id=project_id,
            principal_id=principal_id,
            verify_local=verify_runtime,
        )
        parameter_value = self._validate_parameters(parameters)
        resource_value = self._validate_resources(
            resources, device=parameter_value["device"]
        )
        device_details: dict[str, Any] = {}
        if verify_runtime:
            try:
                details = self._device_validator(parameter_value["device"])
            except Exception as error:
                raise AdapterUnavailable(
                    f"DEVICE_UNAVAILABLE: {type(error).__name__}: {error}"
                ) from error
            device_details = copy.deepcopy(dict(details or {}))

        # Keep the control-plane validator importable without the numerical
        # environment.  The four public parameters are the only caller-
        # controlled Worker config; all other numerical defaults are fixed by
        # the versioned adapter/Worker source and are revalidated in the child.
        normalized_material = {
            "adapter_version": ADAPTER_VERSION,
            "project_id": project_id,
            "principal_id": principal_id,
            "algorithm": algorithm_value,
            "dataset": dataset_identity,
            "dataset_access_scope": dataset_access_scope,
            "task_type": task_type,
            "parameters": parameter_value,
        }
        normalized_hash = _sha256_document(normalized_material)
        worker_config = {
            "model_id": MODEL_ID,
            "preset": parameter_value["preset"],
            "device": parameter_value["device"],
            "iterations": parameter_value["iterations"],
            "seed": parameter_value["seed"],
        }
        return AdapterValidation(
            project_id=project_id,
            principal_id=principal_id,
            algorithm=algorithm_value,
            dataset=dataset_identity,
            dataset_access_scope=dataset_access_scope,
            task_type=task_type,
            parameters=parameter_value,
            resources=resource_value,
            command="invert",
            worker_config=worker_config,
            normalized_config_hash=normalized_hash,
            device_details=device_details,
        )

    def validate(
        self,
        *,
        project_id: str,
        principal_id: str,
        algorithm: Mapping[str, Any],
        dataset: Mapping[str, Any],
        task_type: str,
        parameters: Mapping[str, Any],
        resources: Mapping[str, Any],
    ) -> AdapterValidation:
        validated = self._validate_request(
            project_id=project_id,
            principal_id=principal_id,
            algorithm=algorithm,
            dataset=dataset,
            task_type=task_type,
            parameters=parameters,
            resources=resources,
            verify_runtime=True,
        )
        fingerprint = self._validate_fingerprint(
            self._fingerprint_factory(
                algorithm=validated.algorithm,
                normalized_config_hash=validated.normalized_config_hash,
                input_hashes=[validated.dataset["content_hash"]],
                seed=validated.parameters["seed"],
                device=validated.parameters["device"],
                device_details=validated.device_details,
            ),
            validated=validated,
        )
        return AdapterValidation(
            **{
                **validated.as_dict(),
                "fingerprint": fingerprint,
            }
        )

    def estimate(self, **kwargs: Any) -> AdapterEstimate:
        validated = self.validate(**kwargs)
        return AdapterEstimate(
            normalized_config_hash=validated.normalized_config_hash,
            requested_resources=copy.deepcopy(validated.resources),
            policy_limits=copy.deepcopy(self._manifest["resource_limits"]),
            estimated_wall_time_seconds=None,
            basis="manifest_policy_limits_only",
            limitations=(
                "No calibrated runtime model is available in P1.2a.",
                "CPU, memory, and wall-time values are policy caps, not OS isolation guarantees.",
            ),
        )

    @staticmethod
    def _validate_submit_identity(
        *, task_id: str, node_id: str, plan_hash: str, idempotency_key: str
    ) -> None:
        if not isinstance(task_id, str) or OPAQUE_ID.fullmatch(task_id) is None:
            raise AdapterValidationError(
                "TASK_ID_INVALID", ["task_id must be a v1 opaque identifier"]
            )
        if not isinstance(node_id, str) or IDENTIFIER.fullmatch(node_id) is None:
            raise AdapterValidationError(
                "NODE_ID_INVALID", ["node_id must be a v1 identifier"]
            )
        if not isinstance(plan_hash, str) or PLAN_HASH.fullmatch(plan_hash) is None:
            raise AdapterValidationError(
                "PLAN_HASH_INVALID", ["plan_hash must be a lowercase SHA-256 identity"]
            )
        if (
            not isinstance(idempotency_key, str)
            or NODE_IDEMPOTENCY_KEY.fullmatch(idempotency_key) is None
        ):
            raise AdapterValidationError(
                "IDEMPOTENCY_KEY_INVALID",
                ["idempotency_key must match the PlanGraph node-key contract"],
            )

    def _control_paths(self) -> tuple[Path, Path]:
        root = _validate_run_root(self._run_root, create=True)
        control = _ensure_private_directory(root / CONTROL_DIRECTORY)
        submissions = _ensure_private_directory(control / "submissions")
        locks = _ensure_private_directory(control / "locks")
        return submissions, locks

    @staticmethod
    def _submission_id(task_id: str, plan_hash: str, idempotency_key: str) -> str:
        material = _stable_json_bytes(
            {
                "task_id": task_id,
                "plan_hash": plan_hash,
                "idempotency_key": idempotency_key,
            }
        )
        digest = hashlib.sha256(material).hexdigest()
        return f"submission-{digest}"

    def _job_id(self, submission_id: str, created_at: str) -> str:
        parsed = _parse_timestamp(created_at, code="CLOCK_INVALID")
        stamp = parsed.strftime("%Y%m%dT%H%M%SZ")
        suffix = hashlib.sha256(submission_id.encode("utf-8")).hexdigest()[:12]
        return f"fwi-{stamp}-{suffix}"

    @staticmethod
    def _request_payload(
        *,
        submission_id: str,
        task_id: str,
        node_id: str,
        plan_hash: str,
        idempotency_key: str,
        validated: AdapterValidation,
    ) -> dict[str, Any]:
        return {
            "submission_id": submission_id,
            "task_id": task_id,
            "node_id": node_id,
            "plan_hash": plan_hash,
            "idempotency_key": idempotency_key,
            "project_id": validated.project_id,
            "principal_id": validated.principal_id,
            "algorithm": validated.algorithm,
            "dataset": validated.dataset,
            "dataset_access_scope": validated.dataset_access_scope,
            "task_type": validated.task_type,
            "parameters": validated.parameters,
            "resources": validated.resources,
            "normalized_config_hash": validated.normalized_config_hash,
        }

    @staticmethod
    def _record_request_payload(record: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: copy.deepcopy(record[key])
            for key in (
                "submission_id",
                "task_id",
                "node_id",
                "plan_hash",
                "idempotency_key",
                "project_id",
                "principal_id",
                "algorithm",
                "dataset",
                "dataset_access_scope",
                "task_type",
                "parameters",
                "resources",
                "normalized_config_hash",
            )
        }

    @staticmethod
    def _handle_from_record(record: Mapping[str, Any]) -> AdapterHandle:
        return AdapterHandle(
            submission_id=record["submission_id"],
            task_id=record["task_id"],
            node_id=record["node_id"],
            job_id=record["job_id"],
            idempotency_key=record["idempotency_key"],
            plan_hash=record["plan_hash"],
            request_hash=record["request_hash"],
            algorithm=copy.deepcopy(record["algorithm"]),
            fingerprint=copy.deepcopy(record["fingerprint"]),
            adapter_version=record["adapter_version"],
        )

    @staticmethod
    def _record_integrity_payload(record: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: copy.deepcopy(value)
            for key, value in record.items()
            if key != "record_hash"
        }

    @staticmethod
    def _write_submission(path: Path, record: dict[str, Any]) -> None:
        record["record_hash"] = _sha256_document(
            DeepwaveAdapter._record_integrity_payload(record)
        )
        _atomic_write_json(path, record)

    @staticmethod
    def _read_submission(path: Path) -> dict[str, Any]:
        try:
            value = _read_json_file(
                path, code="ADAPTER_SUBMISSION_INVALID", private=True
            )
        except AdapterStatusError as error:
            raise AdapterHandleError(str(error)) from error
        required = {
            "schema_version",
            "submission_id",
            "task_id",
            "node_id",
            "job_id",
            "idempotency_key",
            "project_id",
            "principal_id",
            "request_hash",
            "plan_hash",
            "algorithm",
            "adapter_version",
            "dataset",
            "dataset_access_scope",
            "task_type",
            "parameters",
            "resources",
            "worker_config",
            "normalized_config_hash",
            "fingerprint",
            "created_at",
            "launch_state",
            "record_hash",
        }
        if set(value) != required:
            raise AdapterHandleError(
                "ADAPTER_SUBMISSION_INVALID: private record fields are inconsistent"
            )
        if value["schema_version"] != "1.0.0" or value["adapter_version"] != ADAPTER_VERSION:
            raise AdapterHandleError(
                "ADAPTER_SUBMISSION_INVALID: private record version is unsupported"
            )
        if value["launch_state"] not in {"preparing", "launching", "launched", "failed"}:
            raise AdapterHandleError(
                "ADAPTER_SUBMISSION_INVALID: launch state is unknown"
            )
        if _sha256_document(DeepwaveAdapter._record_request_payload(value)) != value["request_hash"]:
            raise AdapterHandleError(
                "ADAPTER_SUBMISSION_INVALID: request hash no longer matches"
            )
        if (
            _sha256_document(DeepwaveAdapter._record_integrity_payload(value))
            != value["record_hash"]
        ):
            raise AdapterHandleError(
                "ADAPTER_SUBMISSION_INVALID: private record integrity check failed"
            )
        return value

    @staticmethod
    def _lock_submission(lock_path: Path):
        class SubmissionLock:
            def __enter__(self_nonlocal):
                flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
                directory_descriptor = -1
                try:
                    candidate, directory_descriptor = _safe_parent_fd(lock_path)
                    self_nonlocal.descriptor = os.open(
                        candidate.name,
                        flags,
                        0o600,
                        dir_fd=directory_descriptor,
                    )
                    lock_status = os.fstat(self_nonlocal.descriptor)
                    if (
                        not stat.S_ISREG(lock_status.st_mode)
                        or lock_status.st_uid != os.geteuid()
                        or stat.S_IMODE(lock_status.st_mode) & 0o077
                    ):
                        raise OSError("submission lock is not private")
                    fcntl.flock(self_nonlocal.descriptor, fcntl.LOCK_EX)
                except OSError as error:
                    descriptor = getattr(self_nonlocal, "descriptor", -1)
                    if descriptor >= 0:
                        os.close(descriptor)
                    raise AdapterUnavailable(
                        "ADAPTER_STATE_UNAVAILABLE: cannot lock submission"
                    ) from error
                finally:
                    if directory_descriptor >= 0:
                        os.close(directory_descriptor)
                return self_nonlocal

            def __exit__(self_nonlocal, exc_type, exc, traceback):
                fcntl.flock(self_nonlocal.descriptor, fcntl.LOCK_UN)
                os.close(self_nonlocal.descriptor)

        return SubmissionLock()

    @staticmethod
    def _validate_fingerprint(
        fingerprint: Mapping[str, Any], *, validated: AdapterValidation
    ) -> dict[str, Any]:
        value = copy.deepcopy(dict(fingerprint))
        event = {
            "schema_version": "1.0.0",
            "event_id": "adapter-fingerprint-validation",
            "sequence": 1,
            "task_id": "adapter-fingerprint-validation",
            "node_id": "invert",
            "event_type": "node_started",
            "task_status": "Running",
            "occurred_at": "2026-01-01T00:00:00Z",
            "fingerprint": value,
            "extensions": {},
        }
        errors = schema_errors("run-event.schema.json", event)
        if errors:
            raise AdapterUnavailable(
                "FINGERPRINT_INVALID: " + "; ".join(errors)
            )
        mismatches: list[str] = []
        if value["provenance_mode"] != "development":
            mismatches.append("P1.2a fingerprint must be development mode")
        if value["source"]["identity_complete"] is not False:
            mismatches.append("P1.2a source identity must remain explicitly incomplete")
        if value["algorithm"] != validated.algorithm:
            mismatches.append("algorithm")
        if value["adapter_version"] != ADAPTER_VERSION:
            mismatches.append("adapter_version")
        if value["seed"] != validated.parameters["seed"]:
            mismatches.append("seed")
        if value["hardware"]["device"] != validated.parameters["device"]:
            mismatches.append("device")
        if value["normalized_config_hash"] != validated.normalized_config_hash:
            mismatches.append("normalized_config_hash")
        if value["input_hashes"] != [validated.dataset["content_hash"]]:
            mismatches.append("input_hashes")
        if mismatches:
            raise AdapterUnavailable(
                "FINGERPRINT_INVALID: fingerprint differs from the validated request: "
                + ", ".join(mismatches)
            )
        return value

    def submit(
        self,
        *,
        task_id: str,
        node_id: str,
        plan_hash: str,
        idempotency_key: str,
        project_id: str,
        principal_id: str,
        algorithm: Mapping[str, Any],
        dataset: Mapping[str, Any],
        task_type: str,
        parameters: Mapping[str, Any],
        resources: Mapping[str, Any],
    ) -> AdapterHandle:
        self._validate_submit_identity(
            task_id=task_id,
            node_id=node_id,
            plan_hash=plan_hash,
            idempotency_key=idempotency_key,
        )
        normalized = self._validate_request(
            project_id=project_id,
            principal_id=principal_id,
            algorithm=algorithm,
            dataset=dataset,
            task_type=task_type,
            parameters=parameters,
            resources=resources,
            verify_runtime=False,
        )
        # P0/SQLite scope node keys to an immutable plan, not globally and not
        # merely to a node label.  A changed node under the same plan/key is a
        # conflict; a genuinely new plan hash receives an independent scope.
        submission_id = self._submission_id(task_id, plan_hash, idempotency_key)
        request_payload = self._request_payload(
            submission_id=submission_id,
            task_id=task_id,
            node_id=node_id,
            plan_hash=plan_hash,
            idempotency_key=idempotency_key,
            validated=normalized,
        )
        request_hash = _sha256_document(request_payload)
        submissions, locks = self._control_paths()
        index_name = submission_id.removeprefix("submission-") + ".json"
        index_path = submissions / index_name
        lock_path = locks / (index_name + ".lock")
        with self._lock_submission(lock_path):
            if index_path.exists() or index_path.is_symlink():
                record = self._read_submission(index_path)
                if record["submission_id"] != submission_id or record["request_hash"] != request_hash:
                    raise AdapterIdempotencyConflict(
                        "ADAPTER_IDEMPOTENCY_CONFLICT: key is bound to another request"
                    )
                state = record["launch_state"]
                if state == "launched":
                    return self._handle_from_record(record)
                if state == "failed":
                    raise AdapterUnavailable(
                        "WORKER_LAUNCH_FAILED: prior launch failed and P1 does not retry"
                    )
                raise AdapterUnavailable(
                    "SUBMISSION_RECONCILIATION_REQUIRED: incomplete P1 submission is not relaunched"
                )

            # Readiness is deliberately evaluated only for a first submission.
            # A byte-identical replay must remain able to recover its handle if
            # the GPU or model mount later becomes temporarily unavailable.
            validated = self.validate(
                project_id=project_id,
                principal_id=principal_id,
                algorithm=algorithm,
                dataset=dataset,
                task_type=task_type,
                parameters=parameters,
                resources=resources,
            )
            if validated.normalized_config_hash != normalized.normalized_config_hash:
                raise AdapterUnavailable(
                    "ADAPTER_VALIDATION_DRIFT: live validation changed request identity"
                )
            created_at = self._clock()
            _parse_timestamp(created_at, code="CLOCK_INVALID")
            job_id = self._job_id(submission_id, created_at)
            if validated.fingerprint is None:
                raise AdapterUnavailable(
                    "FINGERPRINT_INVALID: live validation returned no fingerprint"
                )
            fingerprint = copy.deepcopy(validated.fingerprint)
            record: dict[str, Any] = {
                "schema_version": "1.0.0",
                **request_payload,
                "job_id": job_id,
                "request_hash": request_hash,
                "adapter_version": ADAPTER_VERSION,
                "worker_config": copy.deepcopy(validated.worker_config),
                "fingerprint": fingerprint,
                "created_at": created_at,
                "launch_state": "preparing",
            }
            self._write_submission(index_path, record)
            job_dir = self._run_root / job_id
            try:
                job_dir = _create_private_directory(job_dir)
            except OSError as error:
                raise AdapterUnavailable(
                    "JOB_DIRECTORY_CONFLICT: cannot create a unique direct job directory"
                ) from error
            if job_dir.parent != self._run_root:
                raise AdapterUnavailable(
                    "JOB_DIRECTORY_INVALID: job directory escaped the run root"
                )
            worker_config = {"job_id": job_id, **validated.worker_config}
            _atomic_write_json(job_dir / "config.original.json", worker_config)
            _atomic_write_json(
                job_dir / "status.json",
                {
                    "job_id": job_id,
                    "status": "queued",
                    "stage": "queued",
                    "iteration": 0,
                    "total_iterations": validated.parameters["iterations"],
                    "message": "FWI Adapter job queued",
                    "updated_at": created_at,
                },
            )
            record["launch_state"] = "launching"
            self._write_submission(index_path, record)
            try:
                self._launcher.launch(
                    command=validated.command,
                    config_path=job_dir / "config.original.json",
                    run_dir=job_dir,
                    run_root=self._run_root,
                )
            except Exception as error:
                record["launch_state"] = "failed"
                self._write_submission(index_path, record)
                _atomic_write_json(
                    job_dir / "status.json",
                    {
                        "job_id": job_id,
                        "status": "failed",
                        "stage": "submit",
                        "iteration": 0,
                        "total_iterations": validated.parameters["iterations"],
                        "message": "FWI worker could not be started",
                        "updated_at": self._clock(),
                    },
                )
                raise AdapterUnavailable(
                    f"WORKER_LAUNCH_FAILED: {type(error).__name__}"
                ) from error
            record["launch_state"] = "launched"
            self._write_submission(index_path, record)
            return self._handle_from_record(record)

    def _record_for_handle(self, handle: AdapterHandle) -> dict[str, Any]:
        if not isinstance(handle, AdapterHandle):
            raise AdapterHandleError(
                "ADAPTER_HANDLE_INVALID: expected an AdapterHandle"
            )
        if (
            not handle.submission_id.startswith("submission-")
            or not JOB_ID.fullmatch(handle.job_id)
            or PLAN_HASH.fullmatch(handle.plan_hash) is None
            or handle.adapter_version != ADAPTER_VERSION
            or self._submission_id(
                handle.task_id,
                handle.plan_hash,
                handle.idempotency_key,
            )
            != handle.submission_id
        ):
            raise AdapterHandleError(
                "ADAPTER_HANDLE_INVALID: handle fields are malformed"
            )
        root = _validate_run_root(self._run_root, create=False)
        control_root = _require_private_directory(
            root / CONTROL_DIRECTORY, parent=root
        )
        control = _require_private_directory(
            control_root / "submissions", parent=control_root
        )
        index_name = handle.submission_id.removeprefix("submission-") + ".json"
        record = self._read_submission(control / index_name)
        if self._handle_from_record(record) != handle:
            raise AdapterHandleError(
                "ADAPTER_HANDLE_INVALID: handle does not match its private record"
            )
        if record["launch_state"] != "launched":
            raise AdapterHandleError(
                "ADAPTER_HANDLE_INVALID: submission is not in launched state"
            )
        return record

    def _job_directory(self, record: Mapping[str, Any]) -> Path:
        job_id = record["job_id"]
        if not isinstance(job_id, str) or JOB_ID.fullmatch(job_id) is None:
            raise AdapterHandleError("ADAPTER_HANDLE_INVALID: job identity is malformed")
        unresolved = self._run_root / job_id
        if unresolved.parent != self._run_root:
            raise AdapterStatusError(
                "ADAPTER_STATUS_INVALID: job directory escaped the run root"
            )
        descriptor = -1
        try:
            descriptor = _open_directory_fd(unresolved)
            link_status = os.fstat(descriptor)
        except OSError as error:
            raise AdapterStatusError("ADAPTER_STATUS_INVALID: job directory is missing") from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if (
            not stat.S_ISDIR(link_status.st_mode)
            or link_status.st_uid != os.geteuid()
            or stat.S_IMODE(link_status.st_mode) & 0o022
        ):
            raise AdapterStatusError(
                "ADAPTER_STATUS_INVALID: job directory ownership or permissions are unsafe"
            )
        return unresolved

    def status(self, handle: AdapterHandle) -> AdapterStatus:
        record = self._record_for_handle(handle)
        job_dir = self._job_directory(record)
        value = _read_json_file(job_dir / "status.json", code="ADAPTER_STATUS_INVALID")
        if value.get("job_id") != record["job_id"]:
            raise AdapterStatusError(
                "ADAPTER_STATUS_INVALID: status identity does not match the handle"
            )
        worker_status = value.get("status")
        mapping = {
            "queued": "Queued",
            "running": "Running",
            "succeeded": "Succeeded",
            "failed": "Failed",
        }
        if worker_status not in mapping:
            raise AdapterStatusError(
                "ADAPTER_STATUS_INVALID: Worker status is unknown"
            )
        for field in ("stage", "message", "updated_at"):
            if not isinstance(value.get(field), str):
                raise AdapterStatusError(
                    f"ADAPTER_STATUS_INVALID: {field} must be a string"
                )
        allowed_stages = {
            "queued",
            "running",
            "validate_model",
            "generate_observed",
            "gradient_check",
            "invert",
            "plot",
            "complete",
            "failed",
            "worker_exit",
        }
        if value["stage"] not in allowed_stages or len(value["message"]) > 1000:
            raise AdapterStatusError(
                "ADAPTER_STATUS_INVALID: Worker stage or message is outside the v1 contract"
            )
        completed = value.get("iteration")
        total = value.get("total_iterations")
        if (
            type(completed) is not int
            or type(total) is not int
            or completed < 0
            or total < 0
            or completed > total
        ):
            raise AdapterStatusError(
                "ADAPTER_STATUS_INVALID: progress counters are invalid"
            )
        if total != record["parameters"]["iterations"]:
            raise AdapterStatusError(
                "ADAPTER_STATUS_INVALID: total iterations differ from the request"
            )
        combinations_valid = (
            (
                worker_status == "queued"
                and value["stage"] == "queued"
                and completed == 0
            )
            or (
                worker_status == "running"
                and value["stage"]
                not in {"queued", "complete", "failed", "worker_exit"}
            )
            or (
                worker_status == "succeeded"
                and value["stage"] == "complete"
                and completed == total
            )
            or (
                worker_status == "failed"
                and value["stage"] in {"failed", "worker_exit"}
            )
        )
        if not combinations_valid:
            raise AdapterStatusError(
                "ADAPTER_STATUS_INVALID: status, stage, and progress contradict one another"
            )
        _parse_timestamp(value["updated_at"], code="ADAPTER_STATUS_INVALID")
        status = mapping[worker_status]
        controlled_messages = {
            "Queued": "FWI job is queued",
            "Running": f"FWI job is running ({value['stage']})",
            "Succeeded": "FWI job succeeded",
            "Failed": "FWI Worker reported a failure",
        }
        return AdapterStatus(
            job_id=record["job_id"],
            task_id=record["task_id"],
            node_id=record["node_id"],
            status=status,
            stage=value["stage"],
            completed=completed,
            total=total,
            # Worker exception text is retained in the private run directory.
            # It is never promoted into a standard event/status surface because
            # legacy exceptions can contain server-side paths.
            message=controlled_messages[status],
            updated_at=value["updated_at"],
            terminal=status in {"Succeeded", "Failed"},
        )

    def cancel(self, handle: AdapterHandle) -> AdapterCancelResult:
        # P1 intentionally has no process cancellation protocol.  Validate the
        # controlled handle, but do not depend on mutable Worker status evidence
        # and do not signal or rewrite the job.
        self._record_for_handle(handle)
        return AdapterCancelResult(
            supported=False,
            accepted=False,
            code="CANCEL_NOT_SUPPORTED_IN_P1",
            status="Unsupported",
        )

    @staticmethod
    def _read_artifact_bytes(
        job_dir: Path, relative_path: str, *, max_bytes: int
    ) -> bytes:
        relative = Path(relative_path)
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            raise AdapterArtifactError(
                "ADAPTER_ARTIFACT_INVALID: artifact path is not a safe relative path"
            )
        directory_flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            directory_descriptor = _open_directory_fd(job_dir)
        except OSError as error:
            raise AdapterArtifactError(
                "ADAPTER_ARTIFACT_INVALID: job directory is unavailable"
            ) from error
        try:
            for part in relative.parts[:-1]:
                try:
                    next_descriptor = os.open(
                        part, directory_flags, dir_fd=directory_descriptor
                    )
                except OSError as error:
                    raise AdapterArtifactError(
                        "ADAPTER_ARTIFACT_INVALID: artifact parent is unavailable"
                    ) from error
                os.close(directory_descriptor)
                directory_descriptor = next_descriptor
            flags = (
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0)
                | getattr(os, "O_CLOEXEC", 0)
            )
            try:
                descriptor = os.open(
                    relative.parts[-1], flags, dir_fd=directory_descriptor
                )
            except OSError as error:
                raise AdapterArtifactError(
                    "ADAPTER_ARTIFACT_INVALID: artifact is unavailable"
                ) from error
        finally:
            os.close(directory_descriptor)
        try:
            file_status = os.fstat(descriptor)
            if not stat.S_ISREG(file_status.st_mode) or file_status.st_size > max_bytes:
                raise AdapterArtifactError(
                    "ADAPTER_ARTIFACT_INVALID: artifact is not a bounded regular file"
                )
            chunks: list[bytes] = []
            remaining = max_bytes + 1
            while remaining > 0:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            if len(data) > max_bytes:
                raise AdapterArtifactError(
                    "ADAPTER_ARTIFACT_INVALID: artifact is too large"
                )
            return data
        finally:
            os.close(descriptor)

    @staticmethod
    def _validate_npy(
        data: bytes, *, shape: tuple[int, int]
    ) -> tuple[float, float]:
        import numpy as np

        stream = io.BytesIO(data)
        try:
            version = np.lib.format.read_magic(stream)
            if version != (1, 0):
                raise ValueError("only the fixed NPY v1 header is accepted")
            declared_shape, fortran_order, dtype = (
                np.lib.format.read_array_header_1_0(stream)
            )
        except Exception as error:
            raise AdapterArtifactError(
                "ADAPTER_ARTIFACT_INVALID: inverted model is not a safe NPY"
            ) from error
        payload_offset = stream.tell()
        expected_payload_bytes = math.prod(shape) * np.dtype(np.float32).itemsize
        if (
            declared_shape != shape
            or fortran_order
            or dtype != np.dtype(np.float32)
            or len(data) != payload_offset + expected_payload_bytes
        ):
            raise AdapterArtifactError(
                "ADAPTER_ARTIFACT_INVALID: inverted model shape, dtype, order, or size is wrong"
            )
        # Header and exact byte count are fixed before interpreting the payload;
        # frombuffer is a bounded view and cannot allocate from a declared shape.
        value = np.frombuffer(
            data,
            dtype=np.float32,
            count=math.prod(shape),
            offset=payload_offset,
        ).reshape(shape)
        if not np.isfinite(value).all():
            raise AdapterArtifactError(
                "ADAPTER_ARTIFACT_INVALID: inverted model contains NaN or Inf"
            )
        minimum = float(value.min())
        maximum = float(value.max())
        if minimum < 1500.0 or maximum > 5500.0:
            raise AdapterArtifactError(
                "ADAPTER_ARTIFACT_INVALID: inverted velocity is outside the fixed physical bounds"
            )
        return minimum, maximum

    @staticmethod
    def _validate_loss_csv(
        data: bytes, *, iterations: int, expected_frequency_hz: float
    ) -> list[float]:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise AdapterArtifactError(
                "ADAPTER_ARTIFACT_INVALID: loss curve is not UTF-8"
            ) from error
        reader = csv.reader(io.StringIO(text, newline=""))
        rows = list(reader)
        if not rows or rows[0] != ["iteration", "frequency_hz", "loss"]:
            raise AdapterArtifactError(
                "ADAPTER_ARTIFACT_INVALID: loss curve header is invalid"
            )
        values = rows[1:]
        if len(values) != iterations + 1:
            raise AdapterArtifactError(
                "ADAPTER_ARTIFACT_INVALID: loss curve row count is invalid"
            )
        losses: list[float] = []
        for index, row in enumerate(values):
            if len(row) != 3:
                raise AdapterArtifactError(
                    "ADAPTER_ARTIFACT_INVALID: loss curve row is malformed"
                )
            try:
                row_index = int(row[0])
                frequency = float(row[1])
                loss = float(row[2])
            except ValueError as error:
                raise AdapterArtifactError(
                    "ADAPTER_ARTIFACT_INVALID: loss curve contains non-numeric data"
                ) from error
            if (
                row_index != index
                or not math.isfinite(frequency)
                or frequency <= 0
                or not math.isclose(
                    frequency,
                    expected_frequency_hz,
                    rel_tol=0.0,
                    abs_tol=1e-12,
                )
                or not math.isfinite(loss)
                or loss < 0
            ):
                raise AdapterArtifactError(
                    "ADAPTER_ARTIFACT_INVALID: loss curve values are invalid"
                )
            losses.append(loss)
        return losses

    @staticmethod
    def _scalar_metrics(
        value: Mapping[str, Any],
        *,
        iterations: int,
        device: str,
        losses: list[float],
        fingerprint: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise AdapterArtifactError(
                "ADAPTER_ARTIFACT_INVALID: metrics root must be an object"
            )
        integer_fields = ("iterations", "nan_count", "inf_count")
        nonnegative_fields = (
            "initial_loss",
            "final_loss",
            "initial_model_relative_l2",
            "final_model_relative_l2",
            "observed_predicted_relative_l2",
            "model_update_relative_l2",
            "elapsed_seconds",
        )
        finite_fields = ("loss_reduction_fraction",)
        text_fields = ("device_name", "torch_version", "deepwave_version")
        required = {*integer_fields, *nonnegative_fields, *finite_fields, *text_fields, "device"}
        if any(field not in value for field in required):
            raise AdapterArtifactError(
                "ADAPTER_ARTIFACT_INVALID: required structured metrics are missing"
            )
        result: dict[str, Any] = {}
        for field in integer_fields:
            item = value[field]
            if type(item) is not int or item < 0:
                raise AdapterArtifactError(
                    f"ADAPTER_ARTIFACT_INVALID: {field} must be a nonnegative integer"
                )
            result[field] = item
        if result["iterations"] != iterations or result["nan_count"] or result["inf_count"]:
            raise AdapterArtifactError(
                "ADAPTER_ARTIFACT_INVALID: metric iterations/nonfinite counts contradict success"
            )
        for field in (*nonnegative_fields, *finite_fields):
            item = value[field]
            if (
                isinstance(item, bool)
                or not isinstance(item, (int, float))
                or not math.isfinite(float(item))
                or (field in nonnegative_fields and item < 0)
            ):
                raise AdapterArtifactError(
                    f"ADAPTER_ARTIFACT_INVALID: {field} must be a bounded finite number"
                )
            result[field] = item
        if value["device"] != device:
            raise AdapterArtifactError(
                "ADAPTER_ARTIFACT_INVALID: metrics device differs from the request"
            )
        result["device"] = device
        for field in text_fields:
            item = value[field]
            limit = 200 if field == "device_name" else 128
            if (
                not isinstance(item, str)
                or not 1 <= len(item) <= limit
                or any(character in item for character in ("/", "\\", "\n", "\r", "\x00"))
            ):
                raise AdapterArtifactError(
                    f"ADAPTER_ARTIFACT_INVALID: {field} is not a safe bounded label"
                )
            result[field] = item
        expected_reduction = (
            (losses[0] - losses[-1]) / losses[0]
            if losses[0] > 0
            else (0.0 if losses[-1] == 0 else float("-inf"))
        )
        comparisons = {
            "initial_loss": losses[0],
            "final_loss": losses[-1],
            "loss_reduction_fraction": expected_reduction,
        }
        if any(
            not math.isclose(
                float(result[field]), expected, rel_tol=1e-9, abs_tol=1e-12
            )
            for field, expected in comparisons.items()
        ):
            raise AdapterArtifactError(
                "ADAPTER_ARTIFACT_INVALID: loss metrics differ from the validated CSV"
            )
        runtime = fingerprint.get("runtime")
        hardware = fingerprint.get("hardware")
        if (
            not isinstance(runtime, Mapping)
            or not isinstance(hardware, Mapping)
            or result["torch_version"] != runtime.get("pytorch")
            or result["deepwave_version"] != runtime.get("deepwave")
            or result["device_name"] != hardware.get("device_name")
            or result["device"] != hardware.get("device")
        ):
            raise AdapterArtifactError(
                "ADAPTER_ARTIFACT_INVALID: runtime metrics differ from the frozen fingerprint"
            )
        return result

    @staticmethod
    def _artifact_manifest(
        *,
        record: Mapping[str, Any],
        port: str,
        artifact_type: str,
        media_type: str,
        relative_path: str,
        data: bytes,
        created_at: str,
        metrics: Mapping[str, Any],
        component: str,
        title: str,
        order: int,
    ) -> dict[str, Any]:
        content_hash = "sha256:" + hashlib.sha256(data).hexdigest()
        artifact_identity = _sha256_document(
            {
                "task_id": record["task_id"],
                "node_id": record["node_id"],
                "port": port,
                "content_hash": content_hash,
            }
        ).removeprefix("sha256:")
        value = {
            "schema_version": "1.0.0",
            "artifact_id": f"artifact-{artifact_identity[:32]}",
            "task_id": record["task_id"],
            "node_id": record["node_id"],
            "artifact_type": artifact_type,
            "media_type": media_type,
            "location": {
                "relative_path": f"{record['job_id']}/{relative_path}"
            },
            "content_hash": content_hash,
            "size_bytes": len(data),
            "created_at": created_at,
            "metrics": copy.deepcopy(dict(metrics)),
            "display": {"component": component, "title": title, "order": order},
            "fingerprint": copy.deepcopy(record["fingerprint"]),
            "lineage": {
                "plan_hash": record["plan_hash"],
                "algorithm": copy.deepcopy(record["algorithm"]),
                "inputs": [copy.deepcopy(record["dataset"])],
            },
            "extensions": {
                "org.agent_rpc.adapter": {
                    "output_port": port,
                    "worker_job_id": record["job_id"],
                }
            },
        }
        errors = schema_errors("artifact-manifest.schema.json", value)
        if errors:
            raise AdapterArtifactError(
                "ADAPTER_ARTIFACT_INVALID: generated manifest failed its schema: "
                + "; ".join(errors)
            )
        return value

    def collect(self, handle: AdapterHandle) -> list[dict[str, Any]]:
        record = self._record_for_handle(handle)
        current = self.status(handle)
        if current.status != "Succeeded":
            raise AdapterArtifactError(
                "RESULT_NOT_READY: artifacts are available only after success"
            )
        job_dir = self._job_directory(record)
        try:
            config_document = _read_json_file(
                job_dir / "config.original.json", code="ADAPTER_ARTIFACT_INVALID"
            )
            legacy_manifest = _read_json_file(
                job_dir / "manifest.json", code="ADAPTER_ARTIFACT_INVALID"
            )
            metrics_document = _read_json_file(
                job_dir / "metrics.json", code="ADAPTER_ARTIFACT_INVALID"
            )
        except AdapterStatusError as error:
            raise AdapterArtifactError(str(error)) from error
        if (
            config_document != {"job_id": record["job_id"], **record["worker_config"]}
            or legacy_manifest.get("schema_version") != "1"
            or legacy_manifest.get("type") != "fwi_result"
            or legacy_manifest.get("job_id") != record["job_id"]
            or legacy_manifest.get("status") != "succeeded"
            or legacy_manifest.get("command") != "invert"
            or legacy_manifest.get("model_id") != MODEL_ID
            or legacy_manifest.get("physics") != "2d_acoustic_constant_density"
            or legacy_manifest.get("parameter") != "vp"
            or legacy_manifest.get("metrics") != metrics_document
        ):
            raise AdapterArtifactError(
                "ADAPTER_ARTIFACT_INVALID: legacy result identity is inconsistent"
            )
        inverted = self._read_artifact_bytes(
            job_dir, "models/inverted.npy", max_bytes=MAX_NPY_BYTES
        )
        loss = self._read_artifact_bytes(job_dir, "loss.csv", max_bytes=MAX_CSV_BYTES)
        shape = tuple(int(item) for item in record["dataset"].get("shape", []))
        # The public lineage identity intentionally excludes metadata.  Shape
        # comes from the fixed, verified baseline in this adapter version.
        if shape != (94, 288):
            shape = (94, 288)
        self._validate_npy(inverted, shape=shape)
        losses = self._validate_loss_csv(
            loss,
            iterations=record["parameters"]["iterations"],
            expected_frequency_hz=8.0,
        )
        if (
            record["parameters"]["preset"] == "fwi_demo"
            and losses[-1] >= losses[0]
        ):
            raise AdapterArtifactError(
                "ADAPTER_ARTIFACT_INVALID: fwi_demo did not reduce its objective"
            )
        metrics = self._scalar_metrics(
            metrics_document,
            iterations=record["parameters"]["iterations"],
            device=record["parameters"]["device"],
            losses=losses,
            fingerprint=record["fingerprint"],
        )
        return [
            self._artifact_manifest(
                record=record,
                port="inverted_model",
                artifact_type="inverted_velocity_model_2d",
                media_type="application/x-npy",
                relative_path="models/inverted.npy",
                data=inverted,
                created_at=current.updated_at,
                metrics=metrics,
                component="download",
                title="Inverted velocity model",
                order=0,
            ),
            self._artifact_manifest(
                record=record,
                port="loss",
                artifact_type="loss_curve",
                media_type="text/csv",
                relative_path="loss.csv",
                data=loss,
                created_at=current.updated_at,
                metrics=metrics,
                component="line_chart",
                title="FWI loss curve",
                order=1,
            ),
        ]

    def read_artifact(
        self, handle: AdapterHandle, artifact_id: str
    ) -> tuple[dict[str, Any], bytes]:
        """Return one revalidated standard artifact without trusting a path."""

        if (
            not isinstance(artifact_id, str)
            or re.fullmatch(r"artifact-[0-9a-f]{32}", artifact_id) is None
        ):
            raise AdapterArtifactError(
                "ARTIFACT_ID_INVALID: artifact identity is malformed"
            )
        manifests = self.collect(handle)
        manifest = next(
            (value for value in manifests if value.get("artifact_id") == artifact_id),
            None,
        )
        if manifest is None:
            raise AdapterArtifactError(
                "ARTIFACT_NOT_FOUND: artifact identity is not part of this task"
            )
        record = self._record_for_handle(handle)
        location = manifest.get("location")
        relative_path = (
            location.get("relative_path") if isinstance(location, Mapping) else None
        )
        prefix = f"{record['job_id']}/"
        if not isinstance(relative_path, str) or not relative_path.startswith(prefix):
            raise AdapterArtifactError(
                "ADAPTER_ARTIFACT_INVALID: artifact location is not task-bound"
            )
        worker_relative_path = relative_path[len(prefix):]
        media_type = manifest.get("media_type")
        maximum = {
            "application/x-npy": MAX_NPY_BYTES,
            "text/csv": MAX_CSV_BYTES,
        }.get(media_type)
        if maximum is None:
            raise AdapterArtifactError(
                "ADAPTER_ARTIFACT_INVALID: artifact media type is unsupported"
            )
        data = self._read_artifact_bytes(
            self._job_directory(record), worker_relative_path, max_bytes=maximum
        )
        content_hash = "sha256:" + hashlib.sha256(data).hexdigest()
        if (
            len(data) != manifest.get("size_bytes")
            or content_hash != manifest.get("content_hash")
        ):
            raise AdapterArtifactError(
                "ADAPTER_ARTIFACT_INVALID: artifact changed during validated access"
            )
        return copy.deepcopy(manifest), data


__all__ = [
    "AdapterArtifactError",
    "AdapterCancelResult",
    "AdapterError",
    "AdapterEstimate",
    "AdapterHandle",
    "AdapterHandleError",
    "AdapterIdempotencyConflict",
    "AdapterStatus",
    "AdapterStatusError",
    "AdapterUnavailable",
    "AdapterValidation",
    "AdapterValidationError",
    "DeepwaveAdapter",
    "SafeSubprocessWorkerLauncher",
]
