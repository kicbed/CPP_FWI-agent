"""Local staged-launch fencing shared by the fixed Adapter and FWI Worker.

SQLite remains the authoritative scientific task store.  This module owns only
the local execution permit needed by the fixed, single-host Worker backend:

* one immutable attempt binding per staged launch;
* a private per-attempt ``flock`` inherited by the Worker;
* a bounded set of private capacity-slot ``flock`` leases;
* an exact Worker heartbeat written only after both inherited leases validate.

The run root is never scanned for work.  Every path is derived from an already
validated job/attempt identity, and all JSON is private, bounded, hashed, and
atomically replaced.
"""

from __future__ import annotations

import ast
import copy
import contextlib
import fcntl
import hashlib
import json
import math
import os
import re
import secrets
import stat
import struct
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Literal, Mapping


CONTROL_DIRECTORY = ".scientific-runtime-adapter-v1"
LAUNCH_TICKET_NAME = ".worker-launch.json"
WORKER_READY_NAME = ".worker-ready.json"
WORKER_HEARTBEAT_NAME = ".worker-heartbeat.json"
WORKER_EXIT_NAME = ".worker-exit.json"
WORKER_CHECKPOINT_NAME = ".worker-checkpoint.json"
WORKER_RESUME_REQUEST_NAME = ".worker-resume.json"
WORKER_RESUME_ACK_NAME = ".worker-resume-ack.json"
WORKER_CANCEL_DIRECTORY = "worker-cancel"
WORKER_STOP_DIRECTORY = "worker-stop"
WORKER_TERMINAL_ARBITRATION_DIRECTORY = "worker-terminal-arbitration"
CONTROL_SCHEMA_VERSION = "1.0.0"
CHECKPOINT_PROTOCOL_VERSION = "1.0.0"
STOP_PROTOCOL_VERSION = "2.0.0"
MAX_CONTROL_JSON_BYTES = 64 * 1024
MAX_CHECKPOINT_FILE_BYTES = 2 * 1024 * 1024
MAX_CHECKPOINT_PAYLOAD_BYTES = 8 * 1024 * 1024
MAX_CAPACITY = 64

SUBMISSION_ID = re.compile(r"^submission-[0-9a-f]{64}$")
ATTEMPT_ID = re.compile(r"^attempt-[0-9a-f]{32}$")
CHECKPOINT_ID = re.compile(r"^checkpoint-[0-9a-f]{32}$")
RESUME_ID = re.compile(r"^resume-[0-9a-f]{32}$")
JOB_ID = re.compile(r"^fwi-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$")
SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
CANCEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
CANCEL_CAPABILITY = re.compile(r"^cancel-capability-[0-9a-f]{64}$")
STOP_CAPABILITY = re.compile(r"^stop-capability-[0-9a-f]{64}$")
CANCELLED_WORKER_EXIT_CODE = 75
WALL_TIME_EXCEEDED_WORKER_EXIT_CODE = 76
SUPPORTED_STOP_REASONS = ("user_requested", "wall_time_exceeded")


class WorkerControlError(RuntimeError):
    """A staged launch, inherited lease, or heartbeat is not trustworthy."""

    def __init__(self, message: str):
        prefix = message.split(":", 1)[0]
        self.code = (
            prefix
            if re.fullmatch(r"[A-Z][A-Z0-9_]*", prefix)
            else "WORKER_CONTROL_ERROR"
        )
        super().__init__(message)


class WorkerCancellationRequested(BaseException):
    """Cooperative unwind raised only inside the exact managed Worker."""

    def __init__(self, cancel_id: str, reason: str):
        self.cancel_id = cancel_id
        self.reason = reason
        super().__init__("managed Worker cancellation requested")


class WorkerWallTimeExceeded(BaseException):
    """Cooperative timeout unwind raised only inside the exact Worker."""

    def __init__(self, timeout_id: str, reason: str):
        self.timeout_id = timeout_id
        self.reason = reason
        super().__init__("managed Worker wall time exceeded")


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise WorkerControlError("WORKER_CONTROL_INVALID: timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise WorkerControlError(
            "WORKER_CONTROL_INVALID: timestamp is invalid"
        ) from error
    return parsed


def _stable_json_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise WorkerControlError(
            "WORKER_CONTROL_INVALID: control JSON is not canonical"
        ) from error


def _sha256_document(value: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_stable_json_bytes(value)).hexdigest()


def _record_with_hash(value: Mapping[str, Any]) -> dict[str, Any]:
    document = copy.deepcopy(dict(value))
    document["record_hash"] = _sha256_document(document)
    return document


def _validate_record_hash(value: Mapping[str, Any]) -> None:
    record_hash = value.get("record_hash")
    payload = {key: copy.deepcopy(item) for key, item in value.items() if key != "record_hash"}
    if not isinstance(record_hash, str) or record_hash != _sha256_document(payload):
        raise WorkerControlError(
            "WORKER_CONTROL_INVALID: private control integrity check failed"
        )


def _require_owned_directory(path: Path, *, private: bool) -> Path:
    try:
        entry = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise WorkerControlError(
            "WORKER_CONTROL_UNAVAILABLE: private control directory is unavailable"
        ) from error
    if (
        path.is_symlink()
        or resolved != path
        or not stat.S_ISDIR(entry.st_mode)
        or entry.st_uid != os.geteuid()
        or stat.S_IMODE(entry.st_mode) & (0o077 if private else 0o022)
    ):
        raise WorkerControlError(
            "WORKER_CONTROL_INVALID: owned control directory is unsafe"
        )
    return path


def _require_private_directory(path: Path) -> Path:
    """Validate one existing owner-only directory without creating it."""

    return _require_owned_directory(path, private=True)


def _require_protected_directory(path: Path) -> Path:
    """Validate an owned existing directory that is not group/world writable."""

    return _require_owned_directory(path, private=False)


def _ensure_private_directory(path: Path) -> Path:
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    return _require_private_directory(path)


def _validate_root_and_run(run_root: Path | str, run_dir: Path | str) -> tuple[Path, Path]:
    root = Path(run_root)
    candidate = Path(run_dir)
    if not root.is_absolute() or not candidate.is_absolute():
        raise WorkerControlError(
            "WORKER_CONTROL_INVALID: run paths must be absolute"
        )
    if root.is_symlink() or candidate.is_symlink():
        raise WorkerControlError(
            "WORKER_CONTROL_INVALID: run paths cannot be symbolic links"
        )
    try:
        resolved_root = root.resolve(strict=True)
        resolved_candidate = candidate.resolve(strict=True)
    except OSError as error:
        raise WorkerControlError(
            "WORKER_CONTROL_UNAVAILABLE: run directory is unavailable"
        ) from error
    if resolved_root != root or resolved_candidate != candidate:
        raise WorkerControlError(
            "WORKER_CONTROL_INVALID: run paths must already be canonical"
        )
    root = resolved_root
    candidate = resolved_candidate
    # Validation must never recreate a run directory that a purge or operator
    # already removed.  Creation is confined to the Adapter's submit path.
    _require_protected_directory(root)
    _require_private_directory(candidate)
    if candidate.parent != root or JOB_ID.fullmatch(candidate.name) is None:
        raise WorkerControlError(
            "WORKER_CONTROL_INVALID: run directory is not a direct job directory"
        )
    return root, candidate


def _atomic_write_private_json(path: Path, value: Mapping[str, Any]) -> None:
    data = _stable_json_bytes(value) + b"\n"
    if len(data) > MAX_CONTROL_JSON_BYTES:
        raise WorkerControlError(
            "WORKER_CONTROL_INVALID: private control JSON is too large"
        )
    # Every caller establishes its control directory before writing.  Requiring
    # the parent here prevents a late heartbeat from recreating a purged run.
    parent = _require_private_directory(path.parent)
    temp_parent = parent
    if path.name in {
        LAUNCH_TICKET_NAME,
        WORKER_READY_NAME,
        WORKER_HEARTBEAT_NAME,
        WORKER_CHECKPOINT_NAME,
        WORKER_RESUME_REQUEST_NAME,
        WORKER_RESUME_ACK_NAME,
    }:
        # ``prepare_run_dir`` enumerates the queued job before numerical work.
        # Keep transient heartbeat/ticket files out of that artifact directory
        # while retaining an atomic same-filesystem rename into the final path.
        root = _require_protected_directory(parent.parent)
        if JOB_ID.fullmatch(parent.name) is None:
            raise WorkerControlError(
                "WORKER_CONTROL_INVALID: Worker record is outside a job directory"
            )
        control = _ensure_private_directory(root / CONTROL_DIRECTORY)
        temp_parent = _ensure_private_directory(control / "worker-write-tmp")
    descriptor = -1
    temp_name = ""
    try:
        descriptor, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", dir=temp_parent
        )
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
        temp_name = ""
        directory_descriptor = os.open(
            parent,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
        if temp_parent != parent:
            temp_directory_descriptor = os.open(
                temp_parent,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_CLOEXEC", 0),
            )
            try:
                os.fsync(temp_directory_descriptor)
            finally:
                os.close(temp_directory_descriptor)
    except OSError as error:
        raise WorkerControlError(
            "WORKER_CONTROL_UNAVAILABLE: private control write failed"
        ) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temp_name:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass


def _create_private_json(path: Path, value: Mapping[str, Any]) -> None:
    """Durably publish one complete append-only private control record.

    The final name is created with a same-directory hard link only after the
    unique temporary inode has been fully written and fsynced.  Readers can
    therefore observe either no record or the complete record, never a partial
    JSON document.  ``link`` also preserves O_EXCL-style no-replacement
    semantics when concurrent publishers race.
    """

    data = _stable_json_bytes(value) + b"\n"
    if len(data) > MAX_CONTROL_JSON_BYTES:
        raise WorkerControlError(
            "WORKER_CONTROL_INVALID: private control JSON is too large"
        )
    parent = _require_private_directory(path.parent)
    descriptor = -1
    directory_descriptor = -1
    temp_name = ""
    try:
        descriptor, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", dir=parent
        )
        offset = 0
        while offset < len(data):
            written = os.write(descriptor, data[offset:])
            if written <= 0:
                raise OSError("private control write made no progress")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        directory_descriptor = os.open(
            parent,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        temporary_basename = Path(temp_name).name
        os.link(
            temporary_basename,
            path.name,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        os.unlink(temporary_basename, dir_fd=directory_descriptor)
        temp_name = ""
        os.fsync(directory_descriptor)
    except FileExistsError:
        raise
    except OSError as error:
        raise WorkerControlError(
            "WORKER_CONTROL_UNAVAILABLE: private control create failed"
        ) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temp_name:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
        if directory_descriptor >= 0:
            os.close(directory_descriptor)


def _read_private_json(path: Path) -> dict[str, Any]:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    data: bytes | None = None
    for publication_attempt in range(11):
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError:
            raise
        except OSError as error:
            raise WorkerControlError(
                "WORKER_CONTROL_UNAVAILABLE: private control read failed"
            ) from error
        try:
            entry = os.fstat(descriptor)
            if entry.st_nlink == 2 and publication_attempt < 10:
                # _create_private_json publishes a complete inode with a hard
                # link and immediately removes its private temporary name.  A
                # reader that lands in that tiny complete-but-two-link window
                # retries; a persistent second link still fails closed below.
                time.sleep(0.001)
                continue
            if (
                not stat.S_ISREG(entry.st_mode)
                or entry.st_uid != os.geteuid()
                or entry.st_nlink != 1
                or stat.S_IMODE(entry.st_mode) & 0o077
                or entry.st_size > MAX_CONTROL_JSON_BYTES
            ):
                raise WorkerControlError(
                    "WORKER_CONTROL_INVALID: private control file is unsafe"
                )
            chunks: list[bytes] = []
            remaining = MAX_CONTROL_JSON_BYTES + 1
            while remaining > 0:
                chunk = os.read(descriptor, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            break
        finally:
            os.close(descriptor)
    assert data is not None
    if len(data) > MAX_CONTROL_JSON_BYTES:
        raise WorkerControlError(
            "WORKER_CONTROL_INVALID: private control JSON is too large"
        )
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WorkerControlError(
            "WORKER_CONTROL_INVALID: private control JSON is malformed"
        ) from error
    if not isinstance(value, dict):
        raise WorkerControlError(
            "WORKER_CONTROL_INVALID: private control JSON root is invalid"
        )
    return value


def _open_private_lock(path: Path, *, blocking: bool) -> int | None:
    _ensure_private_directory(path.parent)
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        descriptor = os.open(path, flags, 0o600)
        entry = os.fstat(descriptor)
        if (
            not stat.S_ISREG(entry.st_mode)
            or entry.st_uid != os.geteuid()
            or entry.st_nlink != 1
            or stat.S_IMODE(entry.st_mode) & 0o077
        ):
            raise OSError("private lock is unsafe")
        operation = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        try:
            fcntl.flock(descriptor, operation)
        except BlockingIOError:
            os.close(descriptor)
            return None
        return descriptor
    except OSError as error:
        descriptor = locals().get("descriptor", -1)
        if isinstance(descriptor, int) and descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise WorkerControlError(
            "WORKER_CONTROL_UNAVAILABLE: private control lock failed"
        ) from error


def _validate_or_record_lock_identity(
    path: Path, descriptor: int, *, create: bool = True
) -> None:
    entry = os.fstat(descriptor)
    identity_path = path.with_name(path.name + ".identity.json")
    try:
        value = _read_private_json(identity_path)
    except FileNotFoundError:
        if not create:
            raise WorkerControlError(
                "WORKER_FENCE_INVALID: permanent lock identity is missing"
            )
        value = _record_with_hash(
            {
                "schema_version": CONTROL_SCHEMA_VERSION,
                "lock_name": path.name,
                "device": entry.st_dev,
                "inode": entry.st_ino,
            }
        )
        _atomic_write_private_json(identity_path, value)
    required = {
        "schema_version",
        "lock_name",
        "device",
        "inode",
        "record_hash",
    }
    if set(value) != required:
        raise WorkerControlError(
            "WORKER_FENCE_INVALID: lock identity fields are invalid"
        )
    _validate_record_hash(value)
    if (
        value["schema_version"] != CONTROL_SCHEMA_VERSION
        or value["lock_name"] != path.name
        or value["device"] != entry.st_dev
        or value["inode"] != entry.st_ino
    ):
        raise WorkerControlError(
            "WORKER_FENCE_INVALID: permanent lock inode changed"
        )


@dataclass(frozen=True)
class LaunchAttemptBinding:
    submission_id: str
    attempt_id: str
    attempt_number: int
    job_id: str
    request_hash: str
    created_at: str

    def __post_init__(self) -> None:
        if SUBMISSION_ID.fullmatch(self.submission_id) is None:
            raise WorkerControlError(
                "WORKER_CONTROL_INVALID: submission identity is invalid"
            )
        if ATTEMPT_ID.fullmatch(self.attempt_id) is None:
            raise WorkerControlError(
                "WORKER_CONTROL_INVALID: attempt identity is invalid"
            )
        if type(self.attempt_number) is not int or not 1 <= self.attempt_number <= 1_000_000:
            raise WorkerControlError(
                "WORKER_CONTROL_INVALID: attempt number is invalid"
            )
        if JOB_ID.fullmatch(self.job_id) is None:
            raise WorkerControlError(
                "WORKER_CONTROL_INVALID: job identity is invalid"
            )
        if SHA256.fullmatch(self.request_hash) is None:
            raise WorkerControlError(
                "WORKER_CONTROL_INVALID: request hash is invalid"
            )
        _parse_timestamp(self.created_at)

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": CONTROL_SCHEMA_VERSION,
            "submission_id": self.submission_id,
            "attempt_id": self.attempt_id,
            "attempt_number": self.attempt_number,
            "job_id": self.job_id,
            "request_hash": self.request_hash,
            "created_at": self.created_at,
        }

    @property
    def binding_hash(self) -> str:
        return _sha256_document(self.payload())

    def record(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "attempt_number": self.attempt_number,
            "binding_hash": self.binding_hash,
        }


@dataclass(frozen=True)
class WorkerCancelEvidence:
    """Path-free proof for one exact managed-attempt cancellation channel."""

    attempt_id: str
    capability_record_hash: str
    cancel_id: str | None = None
    reason: str | None = None
    requested_at: str | None = None
    request_record_hash: str | None = None
    acknowledged_at: str | None = None
    acknowledgement_record_hash: str | None = None

    @property
    def requested(self) -> bool:
        return self.request_record_hash is not None

    @property
    def acknowledged(self) -> bool:
        return self.acknowledgement_record_hash is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CONTROL_SCHEMA_VERSION,
            "attempt_id": self.attempt_id,
            "capability_record_hash": self.capability_record_hash,
            "cancel_id": self.cancel_id,
            "reason": self.reason,
            "requested_at": self.requested_at,
            "request_record_hash": self.request_record_hash,
            "acknowledged_at": self.acknowledged_at,
            "acknowledgement_record_hash": self.acknowledgement_record_hash,
        }


@dataclass(frozen=True)
class WorkerStopEvidence:
    """Path-free proof for one exact v2 managed-attempt stop channel."""

    attempt_id: str
    binding_hash: str
    capability_record_hash: str
    supported_reasons: tuple[str, ...]
    request_id: str | None = None
    reason: str | None = None
    requested_at: str | None = None
    wall_time_seconds: int | None = None
    started_at: str | None = None
    deadline_at: str | None = None
    ready_record_hash: str | None = None
    request_record_hash: str | None = None
    acknowledged_at: str | None = None
    acknowledgement_record_hash: str | None = None

    @property
    def requested(self) -> bool:
        return self.request_record_hash is not None

    @property
    def acknowledged(self) -> bool:
        return self.acknowledgement_record_hash is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": STOP_PROTOCOL_VERSION,
            "attempt_id": self.attempt_id,
            "binding_hash": self.binding_hash,
            "capability_record_hash": self.capability_record_hash,
            "supported_reasons": list(self.supported_reasons),
            "request_id": self.request_id,
            "reason": self.reason,
            "requested_at": self.requested_at,
            "wall_time_seconds": self.wall_time_seconds,
            "started_at": self.started_at,
            "deadline_at": self.deadline_at,
            "ready_record_hash": self.ready_record_hash,
            "request_record_hash": self.request_record_hash,
            "acknowledged_at": self.acknowledged_at,
            "acknowledgement_record_hash": self.acknowledgement_record_hash,
        }


@dataclass(frozen=True)
class WorkerAttemptEvidence:
    """One path-free, exact snapshot of an already staged Worker attempt.

    This is sampled evidence, not a lease and not a liveness decision.  In
    particular, a stale heartbeat never authorizes another Worker launch; the
    inherited kernel locks remain the execution and capacity authority.
    """

    submission_id: str
    attempt_id: str
    attempt_number: int
    job_id: str
    request_hash: str
    binding_hash: str
    created_at: str
    ticket_state: str
    capacity_slot: int | None
    capacity_generation: int | None
    ticket_worker_pid: int | None
    ticket_updated_at: str
    ticket_record_hash: str
    ready_worker_pid: int | None = None
    ready_started_at: str | None = None
    ready_record_hash: str | None = None
    heartbeat_sequence: int | None = None
    heartbeat_state: str | None = None
    heartbeat_updated_at: str | None = None
    heartbeat_record_hash: str | None = None

    @property
    def ready(self) -> bool:
        return self.ready_record_hash is not None

    @property
    def started(self) -> bool:
        return self.ready and self.heartbeat_record_hash is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0.0",
            "submission_id": self.submission_id,
            "attempt_id": self.attempt_id,
            "attempt_number": self.attempt_number,
            "job_id": self.job_id,
            "request_hash": self.request_hash,
            "binding_hash": self.binding_hash,
            "created_at": self.created_at,
            "ticket": {
                "state": self.ticket_state,
                "capacity_slot": self.capacity_slot,
                "capacity_generation": self.capacity_generation,
                "worker_pid": self.ticket_worker_pid,
                "updated_at": self.ticket_updated_at,
                "record_hash": self.ticket_record_hash,
            },
            "ready": (
                None
                if not self.ready
                else {
                    "worker_pid": self.ready_worker_pid,
                    "started_at": self.ready_started_at,
                    "record_hash": self.ready_record_hash,
                }
            ),
            "heartbeat": (
                None
                if self.heartbeat_record_hash is None
                else {
                    "sequence": self.heartbeat_sequence,
                    "state": self.heartbeat_state,
                    "updated_at": self.heartbeat_updated_at,
                    "record_hash": self.heartbeat_record_hash,
                }
            ),
        }


@dataclass(frozen=True)
class WorkerCheckpointEvidence:
    """Exact path-bounded checkpoint and same-Worker resume evidence."""

    submission_id: str
    attempt_id: str
    attempt_number: int
    job_id: str
    request_hash: str
    binding_hash: str
    ticket_record_hash: str
    ready_record_hash: str
    checkpoint_id: str
    checkpoint_index: int
    completed_updates: int
    manifest_relative_path: str
    manifest_size_bytes: int
    manifest_hash: str
    checkpoint_created_at: str
    checkpoint_record_hash: str
    state: Literal["waiting", "requested", "resumed"]
    resume_id: str | None = None
    checkpoint_proof_hash: str | None = None
    authorized_at: str | None = None
    resume_requested_at: str | None = None
    resume_request_record_hash: str | None = None
    resume_acknowledged_at: str | None = None
    resume_acknowledgement_record_hash: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CHECKPOINT_PROTOCOL_VERSION,
            "submission_id": self.submission_id,
            "attempt_id": self.attempt_id,
            "attempt_number": self.attempt_number,
            "job_id": self.job_id,
            "request_hash": self.request_hash,
            "binding_hash": self.binding_hash,
            "ticket_record_hash": self.ticket_record_hash,
            "ready_record_hash": self.ready_record_hash,
            "checkpoint_id": self.checkpoint_id,
            "checkpoint_index": self.checkpoint_index,
            "completed_updates": self.completed_updates,
            "manifest_relative_path": self.manifest_relative_path,
            "manifest_size_bytes": self.manifest_size_bytes,
            "manifest_hash": self.manifest_hash,
            "checkpoint_created_at": self.checkpoint_created_at,
            "checkpoint_record_hash": self.checkpoint_record_hash,
            "state": self.state,
            "resume_id": self.resume_id,
            "checkpoint_proof_hash": self.checkpoint_proof_hash,
            "authorized_at": self.authorized_at,
            "resume_requested_at": self.resume_requested_at,
            "resume_request_record_hash": self.resume_request_record_hash,
            "resume_acknowledged_at": self.resume_acknowledged_at,
            "resume_acknowledgement_record_hash": (
                self.resume_acknowledgement_record_hash
            ),
        }


@dataclass(frozen=True)
class WorkerExitEvidence:
    """Append-only proof that one exact ready Worker exited unexpectedly.

    The receipt is path-free and does not by itself authorize retry.  A reader
    re-proves the stable idle execution fence, the exact started-attempt
    sidecars, absence of either stop request protocol, and the bound pre/post
    status document before returning it.
    """

    submission_id: str
    attempt_id: str
    attempt_number: int
    job_id: str
    request_hash: str
    binding_hash: str
    created_at: str
    ticket_record_hash: str
    ready_record_hash: str
    heartbeat_sequence: int
    heartbeat_state: str
    heartbeat_record_hash: str
    pre_status_hash: str
    post_status_hash: str
    return_code: int
    observed_at: str
    record_hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CONTROL_SCHEMA_VERSION,
            "submission_id": self.submission_id,
            "attempt_id": self.attempt_id,
            "attempt_number": self.attempt_number,
            "job_id": self.job_id,
            "request_hash": self.request_hash,
            "binding_hash": self.binding_hash,
            "created_at": self.created_at,
            "ticket_record_hash": self.ticket_record_hash,
            "ready_record_hash": self.ready_record_hash,
            "heartbeat_sequence": self.heartbeat_sequence,
            "heartbeat_state": self.heartbeat_state,
            "heartbeat_record_hash": self.heartbeat_record_hash,
            "pre_status_hash": self.pre_status_hash,
            "post_status_hash": self.post_status_hash,
            "return_code": self.return_code,
            "observed_at": self.observed_at,
            "record_hash": self.record_hash,
        }


@contextlib.contextmanager
def _hold_worker_terminal_arbitration(
    run_root: Path | str, binding: LaunchAttemptBinding
) -> Iterator[Path]:
    """Serialize durable stop-request and unexpected-exit publication.

    This lock is distinct from the execution fence because a stop request must
    be publishable while the Worker still owns that fence.  It is stable per
    submission, so all serial attempts share one non-replaceable arbitration
    inode without accumulating one lock per attempt.
    """

    root = Path(run_root)
    if not root.is_absolute() or root.is_symlink():
        raise WorkerControlError(
            "WORKER_CONTROL_INVALID: run root is not canonical"
        )
    try:
        resolved = root.resolve(strict=True)
    except OSError as error:
        raise WorkerControlError(
            "WORKER_CONTROL_UNAVAILABLE: run root is unavailable"
        ) from error
    if resolved != root:
        raise WorkerControlError(
            "WORKER_CONTROL_INVALID: run root is not canonical"
        )
    _require_protected_directory(root)
    control = _ensure_private_directory(root / CONTROL_DIRECTORY)
    arbitration = _ensure_private_directory(
        control / WORKER_TERMINAL_ARBITRATION_DIRECTORY
    )
    lock_path = arbitration / f"{binding.submission_id}.lock"
    descriptor = _open_private_lock(lock_path, blocking=True)
    if descriptor is None:
        raise WorkerControlError(
            "WORKER_CONTROL_UNAVAILABLE: terminal arbitration lock failed"
        )
    try:
        _validate_or_record_lock_identity(lock_path, descriptor)
        yield root
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _reject_worker_exit_receipt_for_stop(
    run_root: Path,
    binding: LaunchAttemptBinding,
    *,
    conflict_code: str,
) -> None:
    """Fail closed when an exact exit receipt already won arbitration."""

    _, job_dir = _validate_root_and_run(
        run_root, run_root / binding.job_id
    )
    try:
        value = _read_private_json(job_dir / WORKER_EXIT_NAME)
    except FileNotFoundError:
        return
    _validate_worker_exit_receipt(value, binding)
    raise WorkerControlError(
        f"{conflict_code}: worker-exit receipt already won terminal arbitration"
    )


def _cancel_control_paths(
    run_root: Path | str,
    binding: LaunchAttemptBinding,
    *,
    create: bool,
) -> tuple[Path, Path, Path]:
    root = Path(run_root)
    if not root.is_absolute() or root.is_symlink():
        raise WorkerControlError(
            "WORKER_CONTROL_INVALID: run root is not canonical"
        )
    try:
        resolved = root.resolve(strict=True)
    except OSError as error:
        raise WorkerControlError(
            "WORKER_CONTROL_UNAVAILABLE: run root is unavailable"
        ) from error
    if resolved != root:
        raise WorkerControlError(
            "WORKER_CONTROL_INVALID: run root is not canonical"
        )
    _require_protected_directory(root)
    if create:
        control = _ensure_private_directory(root / CONTROL_DIRECTORY)
        cancel = _ensure_private_directory(control / WORKER_CANCEL_DIRECTORY)
    else:
        control = _require_private_directory(root / CONTROL_DIRECTORY)
        cancel = _require_private_directory(control / WORKER_CANCEL_DIRECTORY)
    prefix = binding.attempt_id
    return (
        cancel / f"{prefix}.capability.json",
        cancel / f"{prefix}.request.json",
        cancel / f"{prefix}.ack.json",
    )


def _cancel_binding_payload(binding: LaunchAttemptBinding) -> dict[str, Any]:
    return {
        **binding.payload(),
        "binding_hash": binding.binding_hash,
    }


def _validate_cancel_capability(
    value: Mapping[str, Any], binding: LaunchAttemptBinding
) -> dict[str, Any]:
    required = {
        *binding.payload().keys(),
        "binding_hash",
        "capability",
        "worker_pid",
        "capacity_slot",
        "capacity_generation",
        "issued_at",
        "record_hash",
    }
    if set(value) != required:
        raise WorkerControlError(
            "WORKER_CANCEL_INVALID: cancellation capability fields are invalid"
        )
    _validate_record_hash(value)
    if (
        any(
            value.get(key) != expected
            for key, expected in _cancel_binding_payload(binding).items()
        )
        or CANCEL_CAPABILITY.fullmatch(value.get("capability", "")) is None
        or type(value.get("worker_pid")) is not int
        or value["worker_pid"] <= 0
        or type(value.get("capacity_slot")) is not int
        or not 0 <= value["capacity_slot"] < MAX_CAPACITY
        or type(value.get("capacity_generation")) is not int
        or value["capacity_generation"] < 1
    ):
        raise WorkerControlError(
            "WORKER_CANCEL_INVALID: cancellation capability binding changed"
        )
    _parse_timestamp(value.get("issued_at"))
    return copy.deepcopy(dict(value))


def ensure_worker_cancel_capability(
    run_root: Path | str,
    binding: LaunchAttemptBinding,
    *,
    worker_pid: int,
    capacity_slot: int,
    capacity_generation: int,
) -> dict[str, Any]:
    """Worker-publish one exact, append-only private stop capability."""

    if (
        type(worker_pid) is not int
        or worker_pid != os.getpid()
        or type(capacity_slot) is not int
        or not 0 <= capacity_slot < MAX_CAPACITY
        or type(capacity_generation) is not int
        or capacity_generation < 1
    ):
        raise WorkerControlError(
            "WORKER_CANCEL_INVALID: Worker capability identity is invalid"
        )

    capability_path, _, _ = _cancel_control_paths(run_root, binding, create=True)
    try:
        existing = _read_private_json(capability_path)
    except FileNotFoundError:
        candidate = _record_with_hash(
            {
                **_cancel_binding_payload(binding),
                "capability": "cancel-capability-" + secrets.token_hex(32),
                "worker_pid": worker_pid,
                "capacity_slot": capacity_slot,
                "capacity_generation": capacity_generation,
                "issued_at": _utc_now(),
            }
        )
        try:
            _create_private_json(capability_path, candidate)
            return candidate
        except FileExistsError:
            existing = _read_private_json(capability_path)
    existing = _validate_cancel_capability(existing, binding)
    if (
        existing["worker_pid"] != worker_pid
        or existing["capacity_slot"] != capacity_slot
        or existing["capacity_generation"] != capacity_generation
    ):
        raise WorkerControlError(
            "WORKER_CANCEL_INVALID: Worker capability identity changed"
        )
    return existing


def _read_legacy_worker_cancel_capability(
    run_root: Path | str, binding: LaunchAttemptBinding
) -> dict[str, Any] | None:
    """Read a Worker-issued capability without creating any control path."""

    root = Path(run_root)
    if not root.is_absolute() or root.is_symlink():
        raise WorkerControlError(
            "WORKER_CONTROL_INVALID: run root is not canonical"
        )
    try:
        resolved = root.resolve(strict=True)
    except OSError as error:
        raise WorkerControlError(
            "WORKER_CONTROL_UNAVAILABLE: run root is unavailable"
        ) from error
    if resolved != root:
        raise WorkerControlError(
            "WORKER_CONTROL_INVALID: run root is not canonical"
        )
    _require_protected_directory(root)
    control = _require_private_directory(root / CONTROL_DIRECTORY)
    cancel_path = control / WORKER_CANCEL_DIRECTORY
    try:
        cancel_path.lstat()
    except FileNotFoundError:
        return None
    cancel = _require_private_directory(cancel_path)
    capability_path = cancel / f"{binding.attempt_id}.capability.json"
    request_path = cancel / f"{binding.attempt_id}.request.json"
    acknowledgement_path = cancel / f"{binding.attempt_id}.ack.json"
    try:
        capability = _read_private_json(capability_path)
    except FileNotFoundError:
        if any(
            path.exists() or path.is_symlink()
            for path in (request_path, acknowledgement_path)
        ):
            raise WorkerControlError(
                "WORKER_CANCEL_INVALID: cancellation controls have no capability"
            )
        return None
    return _validate_cancel_capability(capability, binding)


def _validate_cancel_request(
    value: Mapping[str, Any],
    binding: LaunchAttemptBinding,
    capability: Mapping[str, Any],
) -> dict[str, Any]:
    required = {
        *binding.payload().keys(),
        "binding_hash",
        "capability_record_hash",
        "cancel_id",
        "reason",
        "requested_at",
        "record_hash",
    }
    if set(value) != required:
        raise WorkerControlError(
            "WORKER_CANCEL_INVALID: cancellation request fields are invalid"
        )
    _validate_record_hash(value)
    if (
        any(
            value.get(key) != expected
            for key, expected in _cancel_binding_payload(binding).items()
        )
        or value.get("capability_record_hash") != capability.get("record_hash")
        or CANCEL_ID.fullmatch(value.get("cancel_id", "")) is None
        or value.get("reason") != "user_requested"
    ):
        raise WorkerControlError(
            "WORKER_CANCEL_INVALID: cancellation request binding changed"
        )
    _parse_timestamp(value.get("requested_at"))
    return copy.deepcopy(dict(value))


def _validate_cancel_acknowledgement(
    value: Mapping[str, Any],
    binding: LaunchAttemptBinding,
    capability: Mapping[str, Any],
    request: Mapping[str, Any],
) -> dict[str, Any]:
    required = {
        *binding.payload().keys(),
        "binding_hash",
        "capability_record_hash",
        "cancel_id",
        "reason",
        "request_record_hash",
        "acknowledged_at",
        "record_hash",
    }
    if set(value) != required:
        raise WorkerControlError(
            "WORKER_CANCEL_INVALID: cancellation acknowledgement fields are invalid"
        )
    _validate_record_hash(value)
    expected = {
        **_cancel_binding_payload(binding),
        "capability_record_hash": capability["record_hash"],
        "cancel_id": request["cancel_id"],
        "reason": request["reason"],
        "request_record_hash": request["record_hash"],
    }
    if any(value.get(key) != item for key, item in expected.items()):
        raise WorkerControlError(
            "WORKER_CANCEL_INVALID: cancellation acknowledgement binding changed"
        )
    _parse_timestamp(value.get("acknowledged_at"))
    return copy.deepcopy(dict(value))


def _request_legacy_worker_cancel_unlocked(
    run_root: Path | str,
    binding: LaunchAttemptBinding,
    *,
    cancel_id: str,
    reason: str,
    requested_at: str,
) -> tuple[WorkerCancelEvidence, bool]:
    """Create or exactly replay one append-only cancellation request."""

    if CANCEL_ID.fullmatch(cancel_id) is None or reason != "user_requested":
        raise WorkerControlError(
            "WORKER_CANCEL_INVALID: cancellation identity or reason is invalid"
        )
    _parse_timestamp(requested_at)
    capability = _read_legacy_worker_cancel_capability(run_root, binding)
    if capability is None:
        raise WorkerControlError(
            "WORKER_CANCEL_UNSUPPORTED: exact Worker issued no cancellation capability"
        )
    _, request_path, _ = _cancel_control_paths(run_root, binding, create=False)
    replayed = False
    try:
        request = _read_private_json(request_path)
        replayed = True
    except FileNotFoundError:
        candidate = _record_with_hash(
            {
                **_cancel_binding_payload(binding),
                "capability_record_hash": capability["record_hash"],
                "cancel_id": cancel_id,
                "reason": reason,
                "requested_at": requested_at,
            }
        )
        try:
            _create_private_json(request_path, candidate)
            request = candidate
        except FileExistsError:
            request = _read_private_json(request_path)
            replayed = True
    request = _validate_cancel_request(request, binding, capability)
    if request["cancel_id"] != cancel_id or request["reason"] != reason:
        raise WorkerControlError(
            "WORKER_CANCEL_CONFLICT: attempt is bound to another cancellation request"
        )
    return _read_legacy_worker_cancel_evidence(run_root, binding), replayed


def _request_legacy_worker_cancel(
    run_root: Path | str,
    binding: LaunchAttemptBinding,
    *,
    cancel_id: str,
    reason: str,
    requested_at: str,
) -> tuple[WorkerCancelEvidence, bool]:
    with _hold_worker_terminal_arbitration(run_root, binding) as root:
        _reject_worker_exit_receipt_for_stop(
            root, binding, conflict_code="WORKER_CANCEL_CONFLICT"
        )
        return _request_legacy_worker_cancel_unlocked(
            root,
            binding,
            cancel_id=cancel_id,
            reason=reason,
            requested_at=requested_at,
        )


def _read_legacy_worker_cancel_evidence(
    run_root: Path | str, binding: LaunchAttemptBinding
) -> WorkerCancelEvidence:
    capability = _read_legacy_worker_cancel_capability(run_root, binding)
    if capability is None:
        raise WorkerControlError(
            "WORKER_CANCEL_UNSUPPORTED: exact Worker issued no cancellation capability"
        )
    capability_path, request_path, ack_path = _cancel_control_paths(
        run_root, binding, create=False
    )
    del capability_path
    try:
        request = _validate_cancel_request(
            _read_private_json(request_path), binding, capability
        )
    except FileNotFoundError:
        try:
            _read_private_json(ack_path)
        except FileNotFoundError:
            pass
        else:
            raise WorkerControlError(
                "WORKER_CANCEL_INVALID: acknowledgement has no request"
            )
        return WorkerCancelEvidence(
            attempt_id=binding.attempt_id,
            capability_record_hash=capability["record_hash"],
        )
    try:
        acknowledgement = _validate_cancel_acknowledgement(
            _read_private_json(ack_path), binding, capability, request
        )
    except FileNotFoundError:
        acknowledgement = None
    return WorkerCancelEvidence(
        attempt_id=binding.attempt_id,
        capability_record_hash=capability["record_hash"],
        cancel_id=request["cancel_id"],
        reason=request["reason"],
        requested_at=request["requested_at"],
        request_record_hash=request["record_hash"],
        acknowledged_at=(
            None if acknowledgement is None else acknowledgement["acknowledged_at"]
        ),
        acknowledgement_record_hash=(
            None if acknowledgement is None else acknowledgement["record_hash"]
        ),
    )


def _acknowledge_worker_cancel(
    run_root: Path | str, binding: LaunchAttemptBinding
) -> WorkerCancelEvidence:
    capability = _read_legacy_worker_cancel_capability(run_root, binding)
    if capability is None:
        raise WorkerControlError(
            "WORKER_CANCEL_UNSUPPORTED: exact Worker issued no cancellation capability"
        )
    capability_path, request_path, ack_path = _cancel_control_paths(
        run_root, binding, create=False
    )
    del capability_path
    request = _validate_cancel_request(
        _read_private_json(request_path), binding, capability
    )
    candidate = _record_with_hash(
        {
            **_cancel_binding_payload(binding),
            "capability_record_hash": capability["record_hash"],
            "cancel_id": request["cancel_id"],
            "reason": request["reason"],
            "request_record_hash": request["record_hash"],
            "acknowledged_at": _utc_now(),
        }
    )
    try:
        _create_private_json(ack_path, candidate)
    except FileExistsError:
        existing = _read_private_json(ack_path)
        _validate_cancel_acknowledgement(
            existing, binding, capability, request
        )
    return _read_legacy_worker_cancel_evidence(run_root, binding)


def _purge_legacy_worker_cancel_control(
    run_root: Path | str, binding: LaunchAttemptBinding
) -> bool:
    """Delete only the exact attempt's private cancel controls after purge."""

    try:
        paths = _cancel_control_paths(run_root, binding, create=False)
    except WorkerControlError as error:
        if error.code == "WORKER_CONTROL_UNAVAILABLE":
            return False
        raise
    temporary_prefixes = tuple(f".{path.name}." for path in paths)
    temporary_paths = tuple(
        candidate
        for candidate in paths[0].parent.iterdir()
        if candidate.name.startswith(temporary_prefixes)
    )
    canonical_present = any(
        path.exists() or path.is_symlink() for path in paths
    )
    if not canonical_present and not temporary_paths:
        return False
    # Validate the whole chain before deleting any member.  A corrupt control
    # record remains visible for reconciliation instead of being erased.
    if canonical_present:
        _read_legacy_worker_cancel_evidence(run_root, binding)
    removed = False
    for path in (*temporary_paths, *reversed(paths)):
        try:
            entry = path.lstat()
        except FileNotFoundError:
            continue
        if (
            not stat.S_ISREG(entry.st_mode)
            or entry.st_uid != os.geteuid()
            or entry.st_nlink != 1
            or stat.S_IMODE(entry.st_mode) & 0o077
        ):
            raise WorkerControlError(
                "WORKER_CANCEL_INVALID: cancellation control is unsafe"
            )
        path.unlink()
        removed = True
    if removed:
        directory_descriptor = os.open(
            paths[0].parent,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    return removed


def _stop_control_paths(
    run_root: Path | str,
    binding: LaunchAttemptBinding,
    *,
    create: bool,
) -> tuple[Path, Path, Path]:
    root = Path(run_root)
    if not root.is_absolute() or root.is_symlink():
        raise WorkerControlError(
            "WORKER_CONTROL_INVALID: run root is not canonical"
        )
    try:
        resolved = root.resolve(strict=True)
    except OSError as error:
        raise WorkerControlError(
            "WORKER_CONTROL_UNAVAILABLE: run root is unavailable"
        ) from error
    if resolved != root:
        raise WorkerControlError(
            "WORKER_CONTROL_INVALID: run root is not canonical"
        )
    _require_protected_directory(root)
    if create:
        control = _ensure_private_directory(root / CONTROL_DIRECTORY)
        stop = _ensure_private_directory(control / WORKER_STOP_DIRECTORY)
    else:
        control = _require_private_directory(root / CONTROL_DIRECTORY)
        stop = _require_private_directory(control / WORKER_STOP_DIRECTORY)
    prefix = binding.attempt_id
    return (
        stop / f"{prefix}.capability.json",
        stop / f"{prefix}.request.json",
        stop / f"{prefix}.ack.json",
    )


def _validate_stop_capability(
    value: Mapping[str, Any], binding: LaunchAttemptBinding
) -> dict[str, Any]:
    required = {
        *binding.payload().keys(),
        "binding_hash",
        "protocol_version",
        "supported_reasons",
        "capability",
        "worker_pid",
        "capacity_slot",
        "capacity_generation",
        "wall_time_seconds",
        "issued_at",
        "record_hash",
    }
    if set(value) != required:
        raise WorkerControlError(
            "WORKER_STOP_INVALID: stop capability fields are invalid"
        )
    _validate_record_hash(value)
    if (
        any(
            value.get(key) != expected
            for key, expected in _cancel_binding_payload(binding).items()
        )
        or value.get("protocol_version") != STOP_PROTOCOL_VERSION
        or value.get("supported_reasons") != list(SUPPORTED_STOP_REASONS)
        or STOP_CAPABILITY.fullmatch(value.get("capability", "")) is None
        or type(value.get("worker_pid")) is not int
        or value["worker_pid"] <= 0
        or type(value.get("capacity_slot")) is not int
        or not 0 <= value["capacity_slot"] < MAX_CAPACITY
        or type(value.get("capacity_generation")) is not int
        or value["capacity_generation"] < 1
        or type(value.get("wall_time_seconds")) is not int
        or not 1 <= value["wall_time_seconds"] <= 86_400
    ):
        raise WorkerControlError(
            "WORKER_STOP_INVALID: stop capability binding changed"
        )
    _parse_timestamp(value.get("issued_at"))
    return copy.deepcopy(dict(value))


def ensure_worker_stop_capability(
    run_root: Path | str,
    binding: LaunchAttemptBinding,
    *,
    worker_pid: int,
    capacity_slot: int,
    capacity_generation: int,
    wall_time_seconds: int,
) -> dict[str, Any]:
    """Worker-publish one exact v2, append-only stop capability."""

    if (
        type(worker_pid) is not int
        or worker_pid != os.getpid()
        or type(capacity_slot) is not int
        or not 0 <= capacity_slot < MAX_CAPACITY
        or type(capacity_generation) is not int
        or capacity_generation < 1
        or type(wall_time_seconds) is not int
        or not 1 <= wall_time_seconds <= 86_400
    ):
        raise WorkerControlError(
            "WORKER_STOP_INVALID: Worker stop capability identity is invalid"
        )

    capability_path, _, _ = _stop_control_paths(run_root, binding, create=True)
    try:
        existing = _read_private_json(capability_path)
    except FileNotFoundError:
        candidate = _record_with_hash(
            {
                **_cancel_binding_payload(binding),
                "protocol_version": STOP_PROTOCOL_VERSION,
                "supported_reasons": list(SUPPORTED_STOP_REASONS),
                "capability": "stop-capability-" + secrets.token_hex(32),
                "worker_pid": worker_pid,
                "capacity_slot": capacity_slot,
                "capacity_generation": capacity_generation,
                "wall_time_seconds": wall_time_seconds,
                "issued_at": _utc_now(),
            }
        )
        try:
            _create_private_json(capability_path, candidate)
            return candidate
        except FileExistsError:
            existing = _read_private_json(capability_path)
    existing = _validate_stop_capability(existing, binding)
    if (
        existing["worker_pid"] != worker_pid
        or existing["capacity_slot"] != capacity_slot
        or existing["capacity_generation"] != capacity_generation
        or existing["wall_time_seconds"] != wall_time_seconds
    ):
        raise WorkerControlError(
            "WORKER_STOP_INVALID: Worker stop capability identity changed"
        )
    return existing


def read_worker_stop_capability(
    run_root: Path | str, binding: LaunchAttemptBinding
) -> dict[str, Any] | None:
    """Read a Worker-issued v2 capability without creating control state."""

    root = Path(run_root)
    if not root.is_absolute() or root.is_symlink():
        raise WorkerControlError(
            "WORKER_CONTROL_INVALID: run root is not canonical"
        )
    try:
        resolved = root.resolve(strict=True)
    except OSError as error:
        raise WorkerControlError(
            "WORKER_CONTROL_UNAVAILABLE: run root is unavailable"
        ) from error
    if resolved != root:
        raise WorkerControlError(
            "WORKER_CONTROL_INVALID: run root is not canonical"
        )
    _require_protected_directory(root)
    control = _require_private_directory(root / CONTROL_DIRECTORY)
    stop_path = control / WORKER_STOP_DIRECTORY
    try:
        stop_path.lstat()
    except FileNotFoundError:
        return None
    stop = _require_private_directory(stop_path)
    capability_path = stop / f"{binding.attempt_id}.capability.json"
    request_path = stop / f"{binding.attempt_id}.request.json"
    acknowledgement_path = stop / f"{binding.attempt_id}.ack.json"
    try:
        capability = _read_private_json(capability_path)
    except FileNotFoundError:
        if any(
            path.exists() or path.is_symlink()
            for path in (request_path, acknowledgement_path)
        ):
            raise WorkerControlError(
                "WORKER_STOP_INVALID: stop controls have no capability"
            )
        return None
    return _validate_stop_capability(capability, binding)


def _validate_stop_request(
    value: Mapping[str, Any],
    binding: LaunchAttemptBinding,
    capability: Mapping[str, Any],
) -> dict[str, Any]:
    required = {
        *binding.payload().keys(),
        "binding_hash",
        "protocol_version",
        "capability_record_hash",
        "request_id",
        "reason",
        "requested_at",
        "wall_time_seconds",
        "started_at",
        "deadline_at",
        "ready_record_hash",
        "record_hash",
    }
    if set(value) != required:
        raise WorkerControlError(
            "WORKER_STOP_INVALID: stop request fields are invalid"
        )
    _validate_record_hash(value)
    reason = value.get("reason")
    if (
        any(
            value.get(key) != expected
            for key, expected in _cancel_binding_payload(binding).items()
        )
        or value.get("protocol_version") != STOP_PROTOCOL_VERSION
        or value.get("capability_record_hash") != capability.get("record_hash")
        or CANCEL_ID.fullmatch(value.get("request_id", "")) is None
        or reason not in capability.get("supported_reasons", [])
    ):
        raise WorkerControlError(
            "WORKER_STOP_INVALID: stop request binding changed"
        )
    requested = _parse_timestamp(value.get("requested_at"))
    if reason == "user_requested":
        if any(
            value.get(field) is not None
            for field in (
                "wall_time_seconds",
                "started_at",
                "deadline_at",
                "ready_record_hash",
            )
        ):
            raise WorkerControlError(
                "WORKER_STOP_INVALID: user stop request has timeout fields"
            )
    else:
        wall_time_seconds = value.get("wall_time_seconds")
        if (
            type(wall_time_seconds) is not int
            or wall_time_seconds != capability.get("wall_time_seconds")
            or SHA256.fullmatch(value.get("ready_record_hash", "")) is None
        ):
            raise WorkerControlError(
                "WORKER_STOP_INVALID: timeout wall time changed"
            )
        started = _parse_timestamp(value.get("started_at"))
        deadline = _parse_timestamp(value.get("deadline_at"))
        if (
            deadline - started != timedelta(seconds=wall_time_seconds)
            or requested < deadline
        ):
            raise WorkerControlError(
                "WORKER_STOP_INVALID: timeout window is invalid"
            )
    return copy.deepcopy(dict(value))


def _validate_stop_acknowledgement(
    value: Mapping[str, Any],
    binding: LaunchAttemptBinding,
    capability: Mapping[str, Any],
    request: Mapping[str, Any],
) -> dict[str, Any]:
    required = {
        *binding.payload().keys(),
        "binding_hash",
        "protocol_version",
        "capability_record_hash",
        "request_id",
        "reason",
        "request_record_hash",
        "acknowledged_at",
        "record_hash",
    }
    if set(value) != required:
        raise WorkerControlError(
            "WORKER_STOP_INVALID: stop acknowledgement fields are invalid"
        )
    _validate_record_hash(value)
    expected = {
        **_cancel_binding_payload(binding),
        "protocol_version": STOP_PROTOCOL_VERSION,
        "capability_record_hash": capability["record_hash"],
        "request_id": request["request_id"],
        "reason": request["reason"],
        "request_record_hash": request["record_hash"],
    }
    if any(value.get(key) != item for key, item in expected.items()):
        raise WorkerControlError(
            "WORKER_STOP_INVALID: stop acknowledgement binding changed"
        )
    _parse_timestamp(value.get("acknowledged_at"))
    return copy.deepcopy(dict(value))


def _request_worker_stop_unlocked(
    run_root: Path | str,
    binding: LaunchAttemptBinding,
    *,
    request_id: str,
    reason: str,
    requested_at: str,
    wall_time_seconds: int | None = None,
    started_at: str | None = None,
    deadline_at: str | None = None,
    ready_record_hash: str | None = None,
) -> tuple[WorkerStopEvidence, bool]:
    """Create or exactly replay the v2 attempt's single stop request."""

    if CANCEL_ID.fullmatch(request_id) is None or reason not in SUPPORTED_STOP_REASONS:
        raise WorkerControlError(
            "WORKER_STOP_INVALID: stop identity or reason is invalid"
        )
    _parse_timestamp(requested_at)
    capability = read_worker_stop_capability(run_root, binding)
    if capability is None or reason not in capability["supported_reasons"]:
        raise WorkerControlError(
            "WORKER_STOP_UNSUPPORTED: exact Worker issued no compatible stop capability"
        )
    _, request_path, _ = _stop_control_paths(run_root, binding, create=False)
    replayed = False
    try:
        request = _read_private_json(request_path)
        replayed = True
    except FileNotFoundError:
        candidate = _record_with_hash(
            {
                **_cancel_binding_payload(binding),
                "protocol_version": STOP_PROTOCOL_VERSION,
                "capability_record_hash": capability["record_hash"],
                "request_id": request_id,
                "reason": reason,
                "requested_at": requested_at,
                "wall_time_seconds": wall_time_seconds,
                "started_at": started_at,
                "deadline_at": deadline_at,
                "ready_record_hash": ready_record_hash,
            }
        )
        candidate = _validate_stop_request(candidate, binding, capability)
        try:
            _create_private_json(request_path, candidate)
            request = candidate
        except FileExistsError:
            request = _read_private_json(request_path)
            replayed = True
    request = _validate_stop_request(request, binding, capability)
    if request["request_id"] != request_id or request["reason"] != reason:
        raise WorkerControlError(
            "WORKER_STOP_CONFLICT: attempt is bound to another stop request"
        )
    expected_timeout = (
        wall_time_seconds,
        started_at,
        deadline_at,
        ready_record_hash,
    )
    actual_timeout = tuple(
        request[field]
        for field in (
            "wall_time_seconds",
            "started_at",
            "deadline_at",
            "ready_record_hash",
        )
    )
    if actual_timeout != expected_timeout:
        raise WorkerControlError(
            "WORKER_STOP_CONFLICT: attempt timeout window changed"
        )
    return read_worker_stop_evidence(run_root, binding), replayed


def request_worker_stop(
    run_root: Path | str,
    binding: LaunchAttemptBinding,
    *,
    request_id: str,
    reason: str,
    requested_at: str,
    wall_time_seconds: int | None = None,
    started_at: str | None = None,
    deadline_at: str | None = None,
    ready_record_hash: str | None = None,
) -> tuple[WorkerStopEvidence, bool]:
    """Arbitrate and publish one exact v2 stop request."""

    with _hold_worker_terminal_arbitration(run_root, binding) as root:
        _reject_worker_exit_receipt_for_stop(
            root, binding, conflict_code="WORKER_STOP_CONFLICT"
        )
        return _request_worker_stop_unlocked(
            root,
            binding,
            request_id=request_id,
            reason=reason,
            requested_at=requested_at,
            wall_time_seconds=wall_time_seconds,
            started_at=started_at,
            deadline_at=deadline_at,
            ready_record_hash=ready_record_hash,
        )


def read_worker_stop_evidence(
    run_root: Path | str, binding: LaunchAttemptBinding
) -> WorkerStopEvidence:
    capability = read_worker_stop_capability(run_root, binding)
    if capability is None:
        raise WorkerControlError(
            "WORKER_STOP_UNSUPPORTED: exact Worker issued no stop capability"
        )
    _, request_path, ack_path = _stop_control_paths(
        run_root, binding, create=False
    )
    try:
        request = _validate_stop_request(
            _read_private_json(request_path), binding, capability
        )
    except FileNotFoundError:
        try:
            _read_private_json(ack_path)
        except FileNotFoundError:
            pass
        else:
            raise WorkerControlError(
                "WORKER_STOP_INVALID: acknowledgement has no request"
            )
        return WorkerStopEvidence(
            attempt_id=binding.attempt_id,
            binding_hash=binding.binding_hash,
            capability_record_hash=capability["record_hash"],
            supported_reasons=tuple(capability["supported_reasons"]),
        )
    try:
        acknowledgement = _validate_stop_acknowledgement(
            _read_private_json(ack_path), binding, capability, request
        )
    except FileNotFoundError:
        acknowledgement = None
    return WorkerStopEvidence(
        attempt_id=binding.attempt_id,
        binding_hash=binding.binding_hash,
        capability_record_hash=capability["record_hash"],
        supported_reasons=tuple(capability["supported_reasons"]),
        request_id=request["request_id"],
        reason=request["reason"],
        requested_at=request["requested_at"],
        wall_time_seconds=request["wall_time_seconds"],
        started_at=request["started_at"],
        deadline_at=request["deadline_at"],
        ready_record_hash=request["ready_record_hash"],
        request_record_hash=request["record_hash"],
        acknowledged_at=(
            None if acknowledgement is None else acknowledgement["acknowledged_at"]
        ),
        acknowledgement_record_hash=(
            None if acknowledgement is None else acknowledgement["record_hash"]
        ),
    )


def _acknowledge_worker_stop(
    run_root: Path | str, binding: LaunchAttemptBinding
) -> WorkerStopEvidence:
    capability = read_worker_stop_capability(run_root, binding)
    if capability is None:
        raise WorkerControlError(
            "WORKER_STOP_UNSUPPORTED: exact Worker issued no stop capability"
        )
    _, request_path, ack_path = _stop_control_paths(
        run_root, binding, create=False
    )
    request = _validate_stop_request(
        _read_private_json(request_path), binding, capability
    )
    candidate = _record_with_hash(
        {
            **_cancel_binding_payload(binding),
            "protocol_version": STOP_PROTOCOL_VERSION,
            "capability_record_hash": capability["record_hash"],
            "request_id": request["request_id"],
            "reason": request["reason"],
            "request_record_hash": request["record_hash"],
            "acknowledged_at": _utc_now(),
        }
    )
    try:
        _create_private_json(ack_path, candidate)
    except FileExistsError:
        existing = _read_private_json(ack_path)
        _validate_stop_acknowledgement(existing, binding, capability, request)
    return read_worker_stop_evidence(run_root, binding)


def purge_worker_stop_control(
    run_root: Path | str, binding: LaunchAttemptBinding
) -> bool:
    """Delete only the exact attempt's v2 stop controls after purge."""

    try:
        paths = _stop_control_paths(run_root, binding, create=False)
    except WorkerControlError as error:
        if error.code == "WORKER_CONTROL_UNAVAILABLE":
            return False
        raise
    temporary_prefixes = tuple(f".{path.name}." for path in paths)
    temporary_paths = tuple(
        candidate
        for candidate in paths[0].parent.iterdir()
        if candidate.name.startswith(temporary_prefixes)
    )
    canonical_present = any(path.exists() or path.is_symlink() for path in paths)
    if not canonical_present and not temporary_paths:
        return False
    if canonical_present:
        read_worker_stop_evidence(run_root, binding)
    removed = False
    for path in (*temporary_paths, *reversed(paths)):
        try:
            entry = path.lstat()
        except FileNotFoundError:
            continue
        if (
            not stat.S_ISREG(entry.st_mode)
            or entry.st_uid != os.geteuid()
            or entry.st_nlink != 1
            or stat.S_IMODE(entry.st_mode) & 0o077
        ):
            raise WorkerControlError(
                "WORKER_STOP_INVALID: stop control is unsafe"
            )
        path.unlink()
        removed = True
    if removed:
        directory_descriptor = os.open(
            paths[0].parent,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    return removed


def _stop_evidence_as_cancel(evidence: WorkerStopEvidence) -> WorkerCancelEvidence:
    return WorkerCancelEvidence(
        attempt_id=evidence.attempt_id,
        capability_record_hash=evidence.capability_record_hash,
        cancel_id=evidence.request_id,
        reason=evidence.reason,
        requested_at=evidence.requested_at,
        request_record_hash=evidence.request_record_hash,
        acknowledged_at=evidence.acknowledged_at,
        acknowledgement_record_hash=evidence.acknowledgement_record_hash,
    )


def read_worker_cancel_capability(
    run_root: Path | str, binding: LaunchAttemptBinding
) -> dict[str, Any] | None:
    """Read the new stop capability, or a legacy cancel-only capability."""

    stop = read_worker_stop_capability(run_root, binding)
    if stop is not None:
        return stop
    return _read_legacy_worker_cancel_capability(run_root, binding)


def request_worker_cancel(
    run_root: Path | str,
    binding: LaunchAttemptBinding,
    *,
    cancel_id: str,
    reason: str,
    requested_at: str,
) -> tuple[WorkerCancelEvidence, bool]:
    """Route user cancellation through v2 when the exact Worker advertises it."""

    stop = read_worker_stop_capability(run_root, binding)
    if stop is not None:
        try:
            evidence, replayed = request_worker_stop(
                run_root,
                binding,
                request_id=cancel_id,
                reason=reason,
                requested_at=requested_at,
            )
        except WorkerControlError as error:
            if error.code == "WORKER_STOP_CONFLICT":
                raise WorkerControlError(
                    "WORKER_CANCEL_CONFLICT: attempt is bound to another stop request"
                ) from error
            raise
        return _stop_evidence_as_cancel(evidence), replayed
    return _request_legacy_worker_cancel(
        run_root,
        binding,
        cancel_id=cancel_id,
        reason=reason,
        requested_at=requested_at,
    )


def read_worker_cancel_evidence(
    run_root: Path | str, binding: LaunchAttemptBinding
) -> WorkerCancelEvidence:
    stop = read_worker_stop_capability(run_root, binding)
    if stop is not None:
        return _stop_evidence_as_cancel(
            read_worker_stop_evidence(run_root, binding)
        )
    return _read_legacy_worker_cancel_evidence(run_root, binding)


def purge_worker_cancel_control(
    run_root: Path | str, binding: LaunchAttemptBinding
) -> bool:
    """Purge both current v2 and legacy v1 controls for the exact attempt."""

    stop_removed = purge_worker_stop_control(run_root, binding)
    legacy_removed = _purge_legacy_worker_cancel_control(run_root, binding)
    return stop_removed or legacy_removed


def binding_from_submission_record(record: Mapping[str, Any]) -> LaunchAttemptBinding:
    attempt = record.get("launch_attempt")
    if not isinstance(attempt, Mapping) or set(attempt) != {
        "attempt_id",
        "attempt_number",
        "binding_hash",
    }:
        raise WorkerControlError(
            "WORKER_CONTROL_INVALID: launch attempt record is invalid"
        )
    binding = LaunchAttemptBinding(
        submission_id=record.get("submission_id"),
        attempt_id=attempt.get("attempt_id"),
        attempt_number=attempt.get("attempt_number"),
        job_id=record.get("job_id"),
        request_hash=record.get("request_hash"),
        created_at=record.get("created_at"),
    )
    if attempt.get("binding_hash") != binding.binding_hash:
        raise WorkerControlError(
            "WORKER_CONTROL_INVALID: launch attempt binding changed"
        )
    return binding


def stage_launch_attempt(
    run_root: Path | str,
    run_dir: Path | str,
    binding: LaunchAttemptBinding,
) -> Path:
    _, job_dir = _validate_root_and_run(run_root, run_dir)
    if job_dir.name != binding.job_id:
        raise WorkerControlError(
            "WORKER_CONTROL_INVALID: launch binding does not match job directory"
        )
    ticket = _record_with_hash(
        {
            **binding.payload(),
            "binding_hash": binding.binding_hash,
            "state": "staged",
            "capacity_slot": None,
            "capacity_generation": None,
            "worker_pid": None,
            "updated_at": binding.created_at,
        }
    )
    path = job_dir / LAUNCH_TICKET_NAME
    _atomic_write_private_json(path, ticket)
    return path


def _read_ticket(run_dir: Path, binding: LaunchAttemptBinding) -> dict[str, Any]:
    value = _read_private_json(run_dir / LAUNCH_TICKET_NAME)
    required = {
        "schema_version",
        "submission_id",
        "attempt_id",
        "attempt_number",
        "job_id",
        "request_hash",
        "created_at",
        "binding_hash",
        "state",
        "capacity_slot",
        "capacity_generation",
        "worker_pid",
        "updated_at",
        "record_hash",
    }
    if set(value) != required:
        raise WorkerControlError(
            "WORKER_CONTROL_INVALID: launch ticket fields are inconsistent"
        )
    _validate_record_hash(value)
    for key, expected in {
        **binding.payload(),
        "binding_hash": binding.binding_hash,
    }.items():
        if value.get(key) != expected:
            raise WorkerControlError(
                "WORKER_CONTROL_INVALID: launch ticket binding changed"
            )
    if value["state"] not in {"staged", "leased", "spawned", "failed"}:
        raise WorkerControlError(
            "WORKER_CONTROL_INVALID: launch ticket state is invalid"
        )
    state = value["state"]
    slot = value["capacity_slot"]
    generation = value["capacity_generation"]
    worker_pid = value["worker_pid"]
    if state == "staged":
        valid_projection = slot is None and generation is None and worker_pid is None
    elif state == "leased":
        valid_projection = (
            type(slot) is int
            and slot >= 0
            and type(generation) is int
            and generation >= 1
            and worker_pid is None
        )
    elif state == "spawned":
        valid_projection = (
            type(slot) is int
            and slot >= 0
            and type(generation) is int
            and generation >= 1
            and type(worker_pid) is int
            and worker_pid > 0
        )
    else:
        valid_projection = worker_pid is None and (
            (slot is None and generation is None)
            or (
                type(slot) is int
                and slot >= 0
                and type(generation) is int
                and generation >= 1
            )
        )
    if not valid_projection:
        raise WorkerControlError(
            "WORKER_CONTROL_INVALID: launch ticket projection is invalid"
        )
    _parse_timestamp(value["updated_at"])
    return value


def _control_paths(root: Path) -> tuple[Path, Path, Path]:
    control = _ensure_private_directory(root / CONTROL_DIRECTORY)
    capacity = _ensure_private_directory(control / "worker-capacity")
    slots = _ensure_private_directory(capacity / "slots")
    attempts = _ensure_private_directory(capacity / "attempts")
    return capacity, slots, attempts


@contextlib.contextmanager
def hold_idle_execution_fence(
    run_root: Path | str,
    binding: LaunchAttemptBinding,
) -> Iterator[None]:
    """Hold an idle stable submission fence without creating control state.

    An active Adapter-managed Worker (or child not yet through its bootstrap)
    causes ``WORKER_ATTEMPT_BUSY``.  Missing, replaced, or unsafe state fails
    closed instead of being treated as an idle Worker.
    """

    root = Path(run_root)
    if not root.is_absolute() or root.is_symlink():
        raise WorkerControlError(
            "WORKER_CONTROL_INVALID: run root is not canonical"
        )
    try:
        resolved = root.resolve(strict=True)
    except OSError as error:
        raise WorkerControlError(
            "WORKER_CONTROL_UNAVAILABLE: run root is unavailable"
        ) from error
    if resolved != root:
        raise WorkerControlError(
            "WORKER_CONTROL_INVALID: run root is not canonical"
        )
    _require_protected_directory(root)
    control = _require_private_directory(root / CONTROL_DIRECTORY)
    capacity = _require_private_directory(control / "worker-capacity")
    attempts = _require_private_directory(capacity / "attempts")
    lock_path = attempts / f"{binding.submission_id}.lock"
    flags = (
        os.O_RDWR
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor = -1
    try:
        descriptor = os.open(lock_path, flags)
        opened = os.fstat(descriptor)
        named = lock_path.stat(follow_symlinks=False)
        if (
            opened.st_dev != named.st_dev
            or opened.st_ino != named.st_ino
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.geteuid()
            or opened.st_nlink != 1
            or stat.S_IMODE(opened.st_mode) & 0o077
        ):
            raise WorkerControlError(
                "WORKER_FENCE_INVALID: execution fence is unsafe"
            )
        _validate_or_record_lock_identity(lock_path, descriptor, create=False)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise WorkerControlError(
                "WORKER_ATTEMPT_BUSY: execution fence is still active"
            ) from error
        # Re-check the name after taking the lock so an inode replacement can
        # never be interpreted as an idle stable fence.
        named = lock_path.stat(follow_symlinks=False)
        if opened.st_dev != named.st_dev or opened.st_ino != named.st_ino:
            raise WorkerControlError(
                "WORKER_FENCE_INVALID: execution fence identity changed"
            )
    except WorkerControlError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as error:
        if descriptor >= 0:
            os.close(descriptor)
        raise WorkerControlError(
            "WORKER_CONTROL_UNAVAILABLE: execution fence is unavailable"
        ) from error

    try:
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def execution_fence_is_held(
    run_root: Path | str,
    binding: LaunchAttemptBinding,
) -> bool:
    """Return true only for an actively held stable submission fence."""

    try:
        with hold_idle_execution_fence(run_root, binding):
            return False
    except WorkerControlError as error:
        if error.code == "WORKER_ATTEMPT_BUSY":
            return True
        raise


def checkpoint_id_for_binding(binding: LaunchAttemptBinding) -> str:
    """Derive the sole v1 checkpoint identity from the immutable attempt."""

    material = _stable_json_bytes(
        {
            "schema_version": CHECKPOINT_PROTOCOL_VERSION,
            "binding_hash": binding.binding_hash,
            "checkpoint_index": 1,
            "completed_updates": 1,
        }
    )
    return "checkpoint-" + hashlib.sha256(material).hexdigest()[:32]


def _checkpoint_manifest_relative_path(checkpoint_id: str) -> str:
    if CHECKPOINT_ID.fullmatch(checkpoint_id) is None:
        raise WorkerControlError(
            "WORKER_CHECKPOINT_INVALID: checkpoint identity is invalid"
        )
    return f"checkpoints/{checkpoint_id}/manifest.json"


def _read_checkpoint_bytes(
    job_dir: Path,
    relative_path: str,
    *,
    maximum_bytes: int,
) -> bytes:
    """Read one fixed checkpoint child without following a symbolic link."""

    relative = Path(relative_path)
    if (
        relative.is_absolute()
        or relative.as_posix() != relative_path
        or len(relative.parts) != 3
        or relative.parts[0] != "checkpoints"
        or CHECKPOINT_ID.fullmatch(relative.parts[1]) is None
        or relative.parts[2]
        not in {
            "manifest.json",
            "model.npy",
            "losses.npy",
            "gradient_clip_values.npy",
            "optimizer_exp_avg.npy",
            "optimizer_exp_avg_sq.npy",
        }
    ):
        raise WorkerControlError(
            "WORKER_CHECKPOINT_INVALID: checkpoint path is unsafe"
        )
    checkpoints = _require_private_directory(job_dir / "checkpoints")
    checkpoint_dir = _require_private_directory(checkpoints / relative.parts[1])
    path = checkpoint_dir / relative.parts[2]
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        named = path.stat(follow_symlinks=False)
        if (
            opened.st_dev != named.st_dev
            or opened.st_ino != named.st_ino
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.geteuid()
            or opened.st_nlink != 1
            or stat.S_IMODE(opened.st_mode) & 0o077
            or opened.st_size < 1
            or opened.st_size > maximum_bytes
        ):
            raise WorkerControlError(
                "WORKER_CHECKPOINT_INVALID: checkpoint file is unsafe"
            )
        chunks: list[bytes] = []
        remaining = maximum_bytes + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) != opened.st_size or len(data) > maximum_bytes:
            raise WorkerControlError(
                "WORKER_CHECKPOINT_INVALID: checkpoint file size changed"
            )
        return data
    except WorkerControlError:
        raise
    except OSError as error:
        raise WorkerControlError(
            "WORKER_CHECKPOINT_UNAVAILABLE: checkpoint file is unavailable"
        ) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _validate_checkpoint_npy(
    data: bytes,
    descriptor: Mapping[str, Any],
) -> None:
    """Validate the bounded no-pickle NPY header and every finite scalar."""

    if not data.startswith(b"\x93NUMPY") or len(data) < 10:
        raise WorkerControlError(
            "WORKER_CHECKPOINT_INVALID: checkpoint array is not NPY"
        )
    major, minor = data[6], data[7]
    if (major, minor) == (1, 0):
        header_length = struct.unpack("<H", data[8:10])[0]
        header_start = 10
    elif major in {2, 3} and minor == 0 and len(data) >= 12:
        header_length = struct.unpack("<I", data[8:12])[0]
        header_start = 12
    else:
        raise WorkerControlError(
            "WORKER_CHECKPOINT_INVALID: checkpoint NPY version is unsupported"
        )
    header_end = header_start + header_length
    if header_end > len(data) or header_length > 4096:
        raise WorkerControlError(
            "WORKER_CHECKPOINT_INVALID: checkpoint NPY header is invalid"
        )
    try:
        header = ast.literal_eval(data[header_start:header_end].decode("latin1"))
    except (SyntaxError, ValueError, UnicodeDecodeError) as error:
        raise WorkerControlError(
            "WORKER_CHECKPOINT_INVALID: checkpoint NPY header is invalid"
        ) from error
    expected_dtype = descriptor.get("dtype")
    item_size = {"float32": 4, "float64": 8}.get(expected_dtype)
    dtype_code = {"float32": "<f4", "float64": "<f8"}.get(expected_dtype)
    shape = descriptor.get("shape")
    if (
        not isinstance(header, dict)
        or set(header) != {"descr", "fortran_order", "shape"}
        or header.get("descr") != dtype_code
        or header.get("fortran_order") is not False
        or not isinstance(shape, list)
        or not shape
        or len(shape) > 2
        or any(type(value) is not int or not 1 <= value <= 4096 for value in shape)
        or tuple(shape) != header.get("shape")
        or item_size is None
    ):
        raise WorkerControlError(
            "WORKER_CHECKPOINT_INVALID: checkpoint NPY contract changed"
        )
    element_count = 1
    for dimension in shape:
        element_count *= dimension
    if element_count > 1_000_000 or header_end + element_count * item_size != len(data):
        raise WorkerControlError(
            "WORKER_CHECKPOINT_INVALID: checkpoint NPY payload size is invalid"
        )
    scalar_format = "<f" if item_size == 4 else "<d"
    if any(
        not math.isfinite(value[0])
        for value in struct.iter_unpack(scalar_format, data[header_end:])
    ):
        raise WorkerControlError(
            "WORKER_CHECKPOINT_INVALID: checkpoint array is not finite"
        )


def _validate_checkpoint_file_descriptor_metadata(
    checkpoint_id: str,
    value: Any,
    *,
    name: str,
    dtype: str,
    shape: list[int],
) -> int:
    required = {"relative_path", "size_bytes", "sha256", "dtype", "shape"}
    expected_path = f"checkpoints/{checkpoint_id}/{name}"
    if (
        not isinstance(value, Mapping)
        or set(value) != required
        or value.get("relative_path") != expected_path
        or type(value.get("size_bytes")) is not int
        or not 1 <= value["size_bytes"] <= MAX_CHECKPOINT_FILE_BYTES
        or SHA256.fullmatch(value.get("sha256", "")) is None
        or value.get("dtype") != dtype
        or value.get("shape") != shape
    ):
        raise WorkerControlError(
            "WORKER_CHECKPOINT_INVALID: checkpoint file descriptor is invalid"
        )
    return value["size_bytes"]


def _validate_checkpoint_file_descriptor(
    job_dir: Path,
    checkpoint_id: str,
    value: Any,
    *,
    name: str,
    dtype: str,
    shape: list[int],
) -> None:
    _validate_checkpoint_file_descriptor_metadata(
        checkpoint_id,
        value,
        name=name,
        dtype=dtype,
        shape=shape,
    )
    expected_path = f"checkpoints/{checkpoint_id}/{name}"
    data = _read_checkpoint_bytes(
        job_dir, expected_path, maximum_bytes=MAX_CHECKPOINT_FILE_BYTES
    )
    if (
        len(data) != value["size_bytes"]
        or "sha256:" + hashlib.sha256(data).hexdigest() != value["sha256"]
    ):
        raise WorkerControlError(
            "WORKER_CHECKPOINT_INVALID: checkpoint file integrity changed"
        )
    _validate_checkpoint_npy(data, value)


def _validate_checkpoint_manifest(
    job_dir: Path,
    binding: LaunchAttemptBinding,
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    expected_path = _checkpoint_manifest_relative_path(receipt["checkpoint_id"])
    if receipt.get("manifest_relative_path") != expected_path:
        raise WorkerControlError(
            "WORKER_CHECKPOINT_INVALID: checkpoint manifest path changed"
        )
    data = _read_checkpoint_bytes(
        job_dir, expected_path, maximum_bytes=MAX_CONTROL_JSON_BYTES
    )
    if (
        len(data) != receipt.get("manifest_size_bytes")
        or "sha256:" + hashlib.sha256(data).hexdigest()
        != receipt.get("manifest_hash")
    ):
        raise WorkerControlError(
            "WORKER_CHECKPOINT_INVALID: checkpoint manifest integrity changed"
        )
    try:
        manifest = json.loads(
            data.decode("utf-8"),
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"invalid constant {value}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise WorkerControlError(
            "WORKER_CHECKPOINT_INVALID: checkpoint manifest is malformed"
        ) from error
    required = {
        "schema_version",
        "checkpoint_id",
        "checkpoint_index",
        "completed_updates",
        "next_state_index",
        "binding_hash",
        "job_id",
        "request_hash",
        "config_hash",
        "optimizer",
        "model",
        "history",
        "created_at",
    }
    if (
        not isinstance(manifest, dict)
        or set(manifest) != required
        or manifest.get("schema_version") != CHECKPOINT_PROTOCOL_VERSION
        or manifest.get("checkpoint_id") != receipt["checkpoint_id"]
        or manifest.get("checkpoint_index") != 1
        or manifest.get("completed_updates") != 1
        or manifest.get("next_state_index") != 1
        or manifest.get("binding_hash") != binding.binding_hash
        or manifest.get("job_id") != binding.job_id
        or manifest.get("request_hash") != binding.request_hash
        or SHA256.fullmatch(manifest.get("config_hash", "")) is None
        or manifest.get("created_at") != receipt["checkpoint_created_at"]
        or data != _stable_json_bytes(manifest) + b"\n"
    ):
        raise WorkerControlError(
            "WORKER_CHECKPOINT_INVALID: checkpoint manifest fields changed"
        )
    _parse_timestamp(manifest["created_at"])
    model = manifest.get("model")
    if not isinstance(model, Mapping):
        raise WorkerControlError(
            "WORKER_CHECKPOINT_INVALID: checkpoint model descriptor is invalid"
        )
    model_shape = model.get("shape")
    if (
        not isinstance(model_shape, list)
        or len(model_shape) != 2
        or any(type(value) is not int or not 1 <= value <= 4096 for value in model_shape)
    ):
        raise WorkerControlError(
            "WORKER_CHECKPOINT_INVALID: checkpoint model shape is invalid"
        )
    history = manifest.get("history")
    if not isinstance(history, Mapping) or set(history) != {
        "losses",
        "gradient_clip_values",
    }:
        raise WorkerControlError(
            "WORKER_CHECKPOINT_INVALID: checkpoint history is invalid"
        )
    optimizer = manifest.get("optimizer")
    if not isinstance(optimizer, Mapping) or set(optimizer) != {
        "name",
        "learning_rate",
        "step",
        "state",
    }:
        raise WorkerControlError(
            "WORKER_CHECKPOINT_INVALID: checkpoint optimizer is invalid"
        )
    learning_rate = optimizer.get("learning_rate")
    if (
        optimizer.get("name") not in {"adam", "sgd"}
        or isinstance(learning_rate, bool)
        or not isinstance(learning_rate, (int, float))
        or not 0 < float(learning_rate) < float("inf")
        or optimizer.get("step") != 1
        or not isinstance(optimizer.get("state"), Mapping)
    ):
        raise WorkerControlError(
            "WORKER_CHECKPOINT_INVALID: checkpoint optimizer fields changed"
        )
    optimizer_state = optimizer["state"]
    if optimizer["name"] == "adam":
        if set(optimizer_state) != {"exp_avg", "exp_avg_sq"}:
            raise WorkerControlError(
                "WORKER_CHECKPOINT_INVALID: Adam checkpoint state is incomplete"
            )
    elif dict(optimizer_state):
        raise WorkerControlError(
            "WORKER_CHECKPOINT_INVALID: SGD checkpoint state is unexpected"
        )

    descriptor_specs: list[tuple[Any, str, str, list[int]]] = [
        (model, "model.npy", "float32", model_shape),
        (history["losses"], "losses.npy", "float64", [1]),
        (
            history["gradient_clip_values"],
            "gradient_clip_values.npy",
            "float64",
            [1],
        ),
    ]
    if optimizer["name"] == "adam":
        descriptor_specs.extend(
            (
                optimizer_state[field],
                name,
                "float32",
                model_shape,
            )
            for field, name in (
                ("exp_avg", "optimizer_exp_avg.npy"),
                ("exp_avg_sq", "optimizer_exp_avg_sq.npy"),
            )
        )
    declared_payload_bytes = len(data) + sum(
        _validate_checkpoint_file_descriptor_metadata(
            receipt["checkpoint_id"],
            descriptor,
            name=name,
            dtype=dtype,
            shape=shape,
        )
        for descriptor, name, dtype, shape in descriptor_specs
    )
    if declared_payload_bytes > MAX_CHECKPOINT_PAYLOAD_BYTES:
        raise WorkerControlError(
            "WORKER_CHECKPOINT_INVALID: checkpoint payload exceeds the aggregate bound"
        )
    for descriptor, name, dtype, shape in descriptor_specs:
        _validate_checkpoint_file_descriptor(
            job_dir,
            receipt["checkpoint_id"],
            descriptor,
            name=name,
            dtype=dtype,
            shape=shape,
        )
    return manifest


def _validate_checkpoint_receipt(
    value: Mapping[str, Any],
    binding: LaunchAttemptBinding,
    job_dir: Path,
) -> dict[str, Any]:
    required = {
        *binding.payload().keys(),
        "binding_hash",
        "ticket_record_hash",
        "ready_record_hash",
        "checkpoint_id",
        "checkpoint_index",
        "completed_updates",
        "manifest_relative_path",
        "manifest_size_bytes",
        "manifest_hash",
        "checkpoint_created_at",
        "record_hash",
    }
    if set(value) != required:
        raise WorkerControlError(
            "WORKER_CHECKPOINT_INVALID: checkpoint receipt fields are invalid"
        )
    _validate_record_hash(value)
    expected = {
        **binding.payload(),
        "binding_hash": binding.binding_hash,
        "checkpoint_id": checkpoint_id_for_binding(binding),
        "checkpoint_index": 1,
        "completed_updates": 1,
    }
    if any(value.get(key) != expected_value for key, expected_value in expected.items()):
        raise WorkerControlError(
            "WORKER_CHECKPOINT_INVALID: checkpoint receipt binding changed"
        )
    if (
        SHA256.fullmatch(value.get("ticket_record_hash", "")) is None
        or SHA256.fullmatch(value.get("ready_record_hash", "")) is None
        or SHA256.fullmatch(value.get("manifest_hash", "")) is None
        or type(value.get("manifest_size_bytes")) is not int
        or not 1 <= value["manifest_size_bytes"] <= MAX_CONTROL_JSON_BYTES
    ):
        raise WorkerControlError(
            "WORKER_CHECKPOINT_INVALID: checkpoint receipt evidence is invalid"
        )
    _parse_timestamp(value.get("checkpoint_created_at"))
    _validate_checkpoint_manifest(job_dir, binding, value)
    return copy.deepcopy(dict(value))


def _checkpoint_resume_request_payload(
    *,
    resume_id: str,
    submission_id: str,
    attempt_id: str,
    attempt_number: int,
    checkpoint_id: str,
    checkpoint_manifest_hash: str,
    checkpoint_receipt_record_hash: str,
    checkpoint_proof_hash: str,
    authorized_at: str,
) -> dict[str, Any]:
    return {
        "schema_version": CHECKPOINT_PROTOCOL_VERSION,
        "resume_id": resume_id,
        "submission_id": submission_id,
        "attempt_id": attempt_id,
        "attempt_number": attempt_number,
        "checkpoint_id": checkpoint_id,
        "checkpoint_manifest_hash": checkpoint_manifest_hash,
        "checkpoint_receipt_record_hash": checkpoint_receipt_record_hash,
        "checkpoint_proof_hash": checkpoint_proof_hash,
        "authorized_at": authorized_at,
    }


def _validate_checkpoint_resume_request(
    value: Mapping[str, Any],
    binding: LaunchAttemptBinding,
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    required = {
        "schema_version",
        "resume_id",
        "submission_id",
        "attempt_id",
        "attempt_number",
        "checkpoint_id",
        "checkpoint_manifest_hash",
        "checkpoint_receipt_record_hash",
        "checkpoint_proof_hash",
        "authorized_at",
        "record_hash",
    }
    if set(value) != required:
        raise WorkerControlError(
            "WORKER_RESUME_INVALID: resume request fields are invalid"
        )
    _validate_record_hash(value)
    if (
        value.get("schema_version") != CHECKPOINT_PROTOCOL_VERSION
        or RESUME_ID.fullmatch(value.get("resume_id", "")) is None
        or value.get("submission_id") != binding.submission_id
        or value.get("attempt_id") != binding.attempt_id
        or value.get("attempt_number") != binding.attempt_number
        or value.get("checkpoint_id") != receipt["checkpoint_id"]
        or value.get("checkpoint_manifest_hash") != receipt["manifest_hash"]
        or value.get("checkpoint_receipt_record_hash") != receipt["record_hash"]
        or SHA256.fullmatch(value.get("checkpoint_proof_hash", "")) is None
    ):
        raise WorkerControlError(
            "WORKER_RESUME_INVALID: resume request binding changed"
        )
    _parse_timestamp(value.get("authorized_at"))
    return copy.deepcopy(dict(value))


def _validate_checkpoint_resume_ack(
    value: Mapping[str, Any],
    binding: LaunchAttemptBinding,
    receipt: Mapping[str, Any],
    request: Mapping[str, Any],
) -> dict[str, Any]:
    required = {
        "schema_version",
        "submission_id",
        "attempt_id",
        "attempt_number",
        "checkpoint_id",
        "checkpoint_receipt_record_hash",
        "resume_id",
        "resume_request_record_hash",
        "resume_acknowledged_at",
        "record_hash",
    }
    if set(value) != required:
        raise WorkerControlError(
            "WORKER_RESUME_INVALID: resume acknowledgement fields are invalid"
        )
    _validate_record_hash(value)
    if any(
        value.get(key) != expected
        for key, expected in {
            "schema_version": CHECKPOINT_PROTOCOL_VERSION,
            "submission_id": binding.submission_id,
            "attempt_id": binding.attempt_id,
            "attempt_number": binding.attempt_number,
            "checkpoint_id": receipt["checkpoint_id"],
            "checkpoint_receipt_record_hash": receipt["record_hash"],
            "resume_id": request["resume_id"],
            "resume_request_record_hash": request["record_hash"],
        }.items()
    ):
        raise WorkerControlError(
            "WORKER_RESUME_INVALID: resume acknowledgement binding changed"
        )
    if _parse_timestamp(value.get("resume_acknowledged_at")) < _parse_timestamp(
        request.get("authorized_at")
    ):
        raise WorkerControlError(
            "WORKER_RESUME_INVALID: resume acknowledgement moved backwards"
        )
    return copy.deepcopy(dict(value))


def read_worker_checkpoint_evidence(
    run_root: Path | str,
    run_dir: Path | str,
    binding: LaunchAttemptBinding,
) -> WorkerCheckpointEvidence | None:
    """Read one exact checkpoint without launching or inferring restartability."""

    root, job_dir = _validate_root_and_run(run_root, run_dir)
    try:
        receipt_value = _read_private_json(job_dir / WORKER_CHECKPOINT_NAME)
    except FileNotFoundError:
        for name in (WORKER_RESUME_REQUEST_NAME, WORKER_RESUME_ACK_NAME):
            try:
                _read_private_json(job_dir / name)
            except FileNotFoundError:
                continue
            raise WorkerControlError(
                "WORKER_CHECKPOINT_INVALID: resume evidence has no checkpoint"
            )
        return None
    receipt = _validate_checkpoint_receipt(receipt_value, binding, job_dir)
    attempt = read_worker_attempt_evidence(root, job_dir, binding)
    if (
        attempt is None
        or attempt.ticket_state != "spawned"
        or not attempt.ready
        or attempt.ticket_record_hash != receipt["ticket_record_hash"]
        or attempt.ready_record_hash != receipt["ready_record_hash"]
        or attempt.heartbeat_state
        not in {"running", "waiting", "succeeded", "failed", "stopped"}
    ):
        raise WorkerControlError(
            "WORKER_CHECKPOINT_INVALID: active attempt evidence changed"
        )
    try:
        request_value = _read_private_json(job_dir / WORKER_RESUME_REQUEST_NAME)
    except FileNotFoundError:
        request_value = None
    try:
        ack_value = _read_private_json(job_dir / WORKER_RESUME_ACK_NAME)
    except FileNotFoundError:
        ack_value = None
    if request_value is None and ack_value is not None:
        raise WorkerControlError(
            "WORKER_RESUME_INVALID: acknowledgement has no request"
        )
    request = (
        None
        if request_value is None
        else _validate_checkpoint_resume_request(request_value, binding, receipt)
    )
    ack = (
        None
        if ack_value is None or request is None
        else _validate_checkpoint_resume_ack(ack_value, binding, receipt, request)
    )
    if ack is None:
        if not execution_fence_is_held(root, binding):
            raise WorkerControlError(
                "WORKER_CHECKPOINT_ORPHANED: waiting Worker released its fence"
            )
        if attempt.heartbeat_state != "waiting":
            raise WorkerControlError(
                "WORKER_CHECKPOINT_PENDING: waiting heartbeat is not durable"
            )
        try:
            status = _read_private_json(job_dir / "status.json")
        except FileNotFoundError as error:
            raise WorkerControlError(
                "WORKER_CHECKPOINT_PENDING: waiting status is unavailable"
            ) from error
        if (
            status.get("job_id") != binding.job_id
            or status.get("status") != "waiting"
            or status.get("stage") != "checkpoint_wait"
            or status.get("checkpoint_id") != receipt["checkpoint_id"]
            or status.get("checkpoint_record_hash") != receipt["record_hash"]
        ):
            raise WorkerControlError(
                "WORKER_CHECKPOINT_PENDING: waiting status is not durable"
            )
    state: Literal["waiting", "requested", "resumed"]
    if ack is not None:
        state = "resumed"
    elif request is not None:
        state = "requested"
    else:
        state = "waiting"
    return WorkerCheckpointEvidence(
        submission_id=binding.submission_id,
        attempt_id=binding.attempt_id,
        attempt_number=binding.attempt_number,
        job_id=binding.job_id,
        request_hash=binding.request_hash,
        binding_hash=binding.binding_hash,
        ticket_record_hash=receipt["ticket_record_hash"],
        ready_record_hash=receipt["ready_record_hash"],
        checkpoint_id=receipt["checkpoint_id"],
        checkpoint_index=receipt["checkpoint_index"],
        completed_updates=receipt["completed_updates"],
        manifest_relative_path=receipt["manifest_relative_path"],
        manifest_size_bytes=receipt["manifest_size_bytes"],
        manifest_hash=receipt["manifest_hash"],
        checkpoint_created_at=receipt["checkpoint_created_at"],
        checkpoint_record_hash=receipt["record_hash"],
        state=state,
        resume_id=None if request is None else request["resume_id"],
        checkpoint_proof_hash=(
            None if request is None else request["checkpoint_proof_hash"]
        ),
        authorized_at=None if request is None else request["authorized_at"],
        resume_requested_at=None if request is None else request["authorized_at"],
        resume_request_record_hash=(
            None if request is None else request["record_hash"]
        ),
        resume_acknowledged_at=(
            None if ack is None else ack["resume_acknowledged_at"]
        ),
        resume_acknowledgement_record_hash=(
            None if ack is None else ack["record_hash"]
        ),
    )


def request_worker_checkpoint_resume(
    run_root: Path | str,
    run_dir: Path | str,
    binding: LaunchAttemptBinding,
    *,
    request_document: Mapping[str, Any],
) -> WorkerCheckpointEvidence:
    """Append one exact same-live-attempt resume request; never launch."""

    root, job_dir = _validate_root_and_run(run_root, run_dir)
    with _hold_worker_terminal_arbitration(root, binding):
        evidence = read_worker_checkpoint_evidence(root, job_dir, binding)
        if evidence is None:
            raise WorkerControlError(
                "WORKER_CHECKPOINT_MISSING: no checkpoint is waiting"
            )
        receipt = _read_private_json(job_dir / WORKER_CHECKPOINT_NAME)
        document = _validate_checkpoint_resume_request(
            request_document, binding, receipt
        )
        if evidence.state == "resumed":
            existing = _read_private_json(job_dir / WORKER_RESUME_REQUEST_NAME)
            if existing != document:
                raise WorkerControlError(
                    "WORKER_RESUME_CONFLICT: another resume request was acknowledged"
                )
            return evidence
        stop = read_worker_stop_evidence(root, binding)
        if stop.requested or stop.acknowledged:
            raise WorkerControlError(
                "WORKER_RESUME_UNSAFE: exact Worker has a stop request"
            )
        path = job_dir / WORKER_RESUME_REQUEST_NAME
        try:
            existing = _read_private_json(path)
        except FileNotFoundError:
            try:
                _create_private_json(path, document)
                existing = document
            except FileExistsError:
                existing = _read_private_json(path)
        validated = _validate_checkpoint_resume_request(existing, binding, receipt)
        if validated != document:
            raise WorkerControlError(
                "WORKER_RESUME_CONFLICT: attempt has another resume request"
            )
    refreshed = read_worker_checkpoint_evidence(root, job_dir, binding)
    if refreshed is None:
        raise WorkerControlError(
            "WORKER_CHECKPOINT_INVALID: checkpoint disappeared after resume request"
        )
    return refreshed


def _ensure_capacity_policy(capacity: Path, max_active: int) -> None:
    lock = _open_private_lock(capacity / "policy.lock", blocking=True)
    if lock is None:
        raise WorkerControlError(
            "WORKER_CONTROL_UNAVAILABLE: capacity policy lock was not acquired"
        )
    try:
        path = capacity / "policy.json"
        try:
            value = _read_private_json(path)
        except FileNotFoundError:
            value = _record_with_hash(
                {
                    "schema_version": CONTROL_SCHEMA_VERSION,
                    "max_active": max_active,
                }
            )
            _atomic_write_private_json(path, value)
        required = {"schema_version", "max_active", "record_hash"}
        if set(value) != required:
            raise WorkerControlError(
                "WORKER_CAPACITY_POLICY_INVALID: capacity policy fields are invalid"
            )
        _validate_record_hash(value)
        if (
            value["schema_version"] != CONTROL_SCHEMA_VERSION
            or value["max_active"] != max_active
        ):
            raise WorkerControlError(
                "WORKER_CAPACITY_POLICY_MISMATCH: launchers disagree on local capacity"
            )
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        os.close(lock)


def _read_slot_projection(path: Path, slot: int) -> dict[str, Any] | None:
    try:
        value = _read_private_json(path)
    except FileNotFoundError:
        return None
    required = {
        "schema_version",
        "slot",
        "generation",
        "attempt_id",
        "job_id",
        "acquired_at",
        "record_hash",
    }
    if set(value) != required:
        raise WorkerControlError(
            "WORKER_CAPACITY_STATE_INVALID: capacity slot fields are invalid"
        )
    _validate_record_hash(value)
    if (
        value["schema_version"] != CONTROL_SCHEMA_VERSION
        or value["slot"] != slot
        or type(value["generation"]) is not int
        or value["generation"] < 1
        or ATTEMPT_ID.fullmatch(value.get("attempt_id", "")) is None
        or JOB_ID.fullmatch(value.get("job_id", "")) is None
    ):
        raise WorkerControlError(
            "WORKER_CAPACITY_STATE_INVALID: capacity slot projection is invalid"
        )
    _parse_timestamp(value["acquired_at"])
    return value


class ParentLaunchLease:
    """Two inherited file leases held continuously across ``Popen``/exec."""

    def __init__(
        self,
        *,
        root: Path,
        run_dir: Path,
        binding: LaunchAttemptBinding,
        attempt_fd: int,
        capacity_fd: int,
        capacity_slot: int,
        capacity_generation: int,
    ) -> None:
        self.root = root
        self.run_dir = run_dir
        self.binding = binding
        self.attempt_fd = attempt_fd
        self.capacity_fd = capacity_fd
        self.capacity_slot = capacity_slot
        self.capacity_generation = capacity_generation
        self._closed = False

    @classmethod
    def acquire(
        cls,
        run_root: Path | str,
        run_dir: Path | str,
        *,
        max_active: int,
    ) -> "ParentLaunchLease":
        if type(max_active) is not int or not 1 <= max_active <= MAX_CAPACITY:
            raise WorkerControlError(
                "WORKER_CAPACITY_POLICY_INVALID: max_active is invalid"
            )
        root, job_dir = _validate_root_and_run(run_root, run_dir)
        staged = _read_private_json(job_dir / LAUNCH_TICKET_NAME)
        try:
            binding = LaunchAttemptBinding(
                submission_id=staged.get("submission_id"),
                attempt_id=staged.get("attempt_id"),
                attempt_number=staged.get("attempt_number"),
                job_id=staged.get("job_id"),
                request_hash=staged.get("request_hash"),
                created_at=staged.get("created_at"),
            )
        except AttributeError as error:
            raise WorkerControlError(
                "WORKER_CONTROL_INVALID: launch ticket is invalid"
            ) from error
        ticket = _read_ticket(job_dir, binding)
        if ticket["state"] != "staged":
            raise WorkerControlError(
                "WORKER_ATTEMPT_REPLAY: launch ticket is not newly staged"
            )

        capacity, slots, attempts = _control_paths(root)
        _ensure_capacity_policy(capacity, max_active)
        # This inode is stable for the submission, not the attempt.  A future
        # retry with a new attempt token therefore cannot overlap an old Worker
        # that is still holding the execution fence.
        attempt_path = attempts / f"{binding.submission_id}.lock"
        attempt_fd = _open_private_lock(attempt_path, blocking=False)
        if attempt_fd is None:
            raise WorkerControlError(
                "WORKER_ATTEMPT_BUSY: launch attempt is already active"
            )
        capacity_fd = -1
        selected_slot = -1
        try:
            _validate_or_record_lock_identity(attempt_path, attempt_fd)
            for slot in range(max_active):
                candidate = _open_private_lock(
                    slots / f"slot-{slot:03d}.lock", blocking=False
                )
                if candidate is not None:
                    capacity_fd = candidate
                    _validate_or_record_lock_identity(
                        slots / f"slot-{slot:03d}.lock", candidate
                    )
                    selected_slot = slot
                    break
            if capacity_fd < 0:
                raise WorkerControlError(
                    "ADAPTER_CONCURRENCY_LIMIT: cross-process Worker capacity is full"
                )
            projection_path = slots / f"slot-{selected_slot:03d}.json"
            previous = _read_slot_projection(projection_path, selected_slot)
            generation = 1 if previous is None else previous["generation"] + 1
            acquired_at = _utc_now()
            projection = _record_with_hash(
                {
                    "schema_version": CONTROL_SCHEMA_VERSION,
                    "slot": selected_slot,
                    "generation": generation,
                    "attempt_id": binding.attempt_id,
                    "job_id": binding.job_id,
                    "acquired_at": acquired_at,
                }
            )
            _atomic_write_private_json(projection_path, projection)
            ticket.update(
                {
                    "state": "leased",
                    "capacity_slot": selected_slot,
                    "capacity_generation": generation,
                    "worker_pid": None,
                    "updated_at": acquired_at,
                }
            )
            ticket = _record_with_hash(
                {key: value for key, value in ticket.items() if key != "record_hash"}
            )
            _atomic_write_private_json(job_dir / LAUNCH_TICKET_NAME, ticket)
            return cls(
                root=root,
                run_dir=job_dir,
                binding=binding,
                attempt_fd=attempt_fd,
                capacity_fd=capacity_fd,
                capacity_slot=selected_slot,
                capacity_generation=generation,
            )
        except Exception:
            if capacity_fd >= 0:
                os.close(capacity_fd)
            os.close(attempt_fd)
            raise

    @property
    def pass_fds(self) -> tuple[int, int]:
        if self._closed:
            raise WorkerControlError(
                "WORKER_CONTROL_INVALID: inherited leases are already closed"
            )
        return self.attempt_fd, self.capacity_fd

    @property
    def child_arguments(self) -> list[str]:
        return [
            "--launch-attempt-id",
            self.binding.attempt_id,
            "--launch-attempt-fd",
            str(self.attempt_fd),
            "--capacity-lease-fd",
            str(self.capacity_fd),
        ]

    def mark_spawned(self, pid: int) -> None:
        if type(pid) is not int or pid <= 0 or self._closed:
            raise WorkerControlError(
                "WORKER_CONTROL_INVALID: spawned Worker identity is invalid"
            )
        ticket = _read_ticket(self.run_dir, self.binding)
        if (
            ticket["capacity_slot"] != self.capacity_slot
            or ticket["capacity_generation"] != self.capacity_generation
        ):
            raise WorkerControlError(
                "WORKER_CONTROL_INVALID: launch ticket lost its active lease"
            )
        if ticket["state"] == "spawned" and ticket["worker_pid"] == pid:
            return
        if ticket["state"] != "leased" or ticket["worker_pid"] is not None:
            raise WorkerControlError(
                "WORKER_CONTROL_INVALID: another Worker activated this attempt"
            )
        ticket.update(
            {"state": "spawned", "worker_pid": pid, "updated_at": _utc_now()}
        )
        ticket = _record_with_hash(
            {key: value for key, value in ticket.items() if key != "record_hash"}
        )
        _atomic_write_private_json(self.run_dir / LAUNCH_TICKET_NAME, ticket)

    def abort(self) -> None:
        try:
            ticket = _read_ticket(self.run_dir, self.binding)
            ticket.update(
                {"state": "failed", "worker_pid": None, "updated_at": _utc_now()}
            )
            ticket = _record_with_hash(
                {key: value for key, value in ticket.items() if key != "record_hash"}
            )
            _atomic_write_private_json(self.run_dir / LAUNCH_TICKET_NAME, ticket)
        finally:
            self.close_parent()

    def close_parent(self) -> None:
        if self._closed:
            return
        self._closed = True
        for descriptor in (self.attempt_fd, self.capacity_fd):
            try:
                os.close(descriptor)
            except OSError:
                pass


def mark_launch_failed(run_dir: Path | str, binding: LaunchAttemptBinding) -> None:
    job_dir = Path(run_dir)
    try:
        ticket = _read_ticket(job_dir, binding)
    except (FileNotFoundError, WorkerControlError):
        return
    ticket.update({"state": "failed", "worker_pid": None, "updated_at": _utc_now()})
    ticket = _record_with_hash(
        {key: value for key, value in ticket.items() if key != "record_hash"}
    )
    _atomic_write_private_json(job_dir / LAUNCH_TICKET_NAME, ticket)


def _fd_matches_private_file(descriptor: int, path: Path) -> None:
    if type(descriptor) is not int or descriptor < 0:
        raise WorkerControlError(
            "WORKER_FENCE_INVALID: inherited descriptor is invalid"
        )
    try:
        opened = os.fstat(descriptor)
        expected = path.stat(follow_symlinks=False)
    except OSError as error:
        raise WorkerControlError(
            "WORKER_FENCE_INVALID: inherited lease file is unavailable"
        ) from error
    if (
        opened.st_dev != expected.st_dev
        or opened.st_ino != expected.st_ino
        or not stat.S_ISREG(opened.st_mode)
        or opened.st_uid != os.geteuid()
        or opened.st_nlink != 1
        or stat.S_IMODE(opened.st_mode) & 0o077
    ):
        raise WorkerControlError(
            "WORKER_FENCE_INVALID: inherited lease does not match control state"
        )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        raise WorkerControlError(
            "WORKER_FENCE_INVALID: inherited lease is not held"
        ) from error
    _validate_or_record_lock_identity(path, descriptor, create=False)


class WorkerHeartbeat:
    """Worker-owned heartbeat that retains both launch leases until stop."""

    def __init__(
        self,
        *,
        run_root: Path | str,
        run_dir: Path | str,
        attempt_id: str,
        attempt_fd: int,
        capacity_fd: int,
        interval_seconds: float = 1.0,
        cancel_grace_seconds: float = 5.0,
        wall_time_seconds: int = 86_400,
        hard_exit: Callable[[int], Any] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if ATTEMPT_ID.fullmatch(attempt_id) is None:
            raise WorkerControlError(
                "WORKER_FENCE_INVALID: launch attempt identity is invalid"
            )
        if (
            isinstance(interval_seconds, bool)
            or not isinstance(interval_seconds, (int, float))
            or not 0.01 <= float(interval_seconds) <= 60.0
        ):
            raise WorkerControlError(
                "WORKER_HEARTBEAT_INVALID: heartbeat interval is invalid"
            )
        if (
            isinstance(cancel_grace_seconds, bool)
            or not isinstance(cancel_grace_seconds, (int, float))
            or not 0.01 <= float(cancel_grace_seconds) <= 300.0
        ):
            raise WorkerControlError(
                "WORKER_CANCEL_INVALID: cancellation grace is invalid"
            )
        if hard_exit is not None and not callable(hard_exit):
            raise WorkerControlError(
                "WORKER_CANCEL_INVALID: hard-exit callback is invalid"
            )
        if type(wall_time_seconds) is not int or not 1 <= wall_time_seconds <= 86_400:
            raise WorkerControlError(
                "WORKER_STOP_INVALID: wall time is invalid"
            )
        if not callable(monotonic):
            raise WorkerControlError(
                "WORKER_STOP_INVALID: monotonic clock is invalid"
            )
        self.root, self.run_dir = _validate_root_and_run(run_root, run_dir)
        self.attempt_id = attempt_id
        self.attempt_fd = attempt_fd
        self.capacity_fd = capacity_fd
        self.interval_seconds = float(interval_seconds)
        self.cancel_grace_seconds = float(cancel_grace_seconds)
        self.wall_time_seconds = wall_time_seconds
        self._hard_exit = hard_exit or os._exit
        self._monotonic = monotonic
        self._stop = threading.Event()
        self._thread_started = threading.Event()
        self._ready_published = threading.Event()
        self._cancel_requested = threading.Event()
        self._thread: threading.Thread | None = None
        self._write_lock = threading.RLock()
        self._cancel_lock = threading.Lock()
        self._failure: BaseException | None = None
        self._sequence = 0
        self._active_heartbeat_state = "running"
        self._started_at: str | None = None
        self._started_monotonic: float | None = None
        self._ticket: dict[str, Any] | None = None
        self._binding: LaunchAttemptBinding | None = None
        self._stop_evidence: WorkerStopEvidence | None = None
        self._cancel_deadline: float | None = None
        self._cancel_force_deadline: float | None = None
        self._closed = False

    def start(self) -> None:
        if self._thread is not None or self._closed:
            raise WorkerControlError(
                "WORKER_ATTEMPT_REPLAY: heartbeat was already started"
            )
        raw_ticket = _read_private_json(self.run_dir / LAUNCH_TICKET_NAME)
        binding = LaunchAttemptBinding(
            submission_id=raw_ticket.get("submission_id"),
            attempt_id=raw_ticket.get("attempt_id"),
            attempt_number=raw_ticket.get("attempt_number"),
            job_id=raw_ticket.get("job_id"),
            request_hash=raw_ticket.get("request_hash"),
            created_at=raw_ticket.get("created_at"),
        )
        ticket = _read_ticket(self.run_dir, binding)
        if binding.attempt_id != self.attempt_id or ticket["state"] not in {
            "leased",
            "spawned",
        }:
            raise WorkerControlError(
                "WORKER_FENCE_INVALID: launch ticket is not the active attempt"
            )
        slot = ticket["capacity_slot"]
        generation = ticket["capacity_generation"]
        if (
            type(slot) is not int
            or slot < 0
            or type(generation) is not int
            or generation < 1
        ):
            raise WorkerControlError(
                "WORKER_FENCE_INVALID: capacity lease identity is invalid"
            )
        capacity, slots, attempts = _control_paths(self.root)
        _ = capacity
        _fd_matches_private_file(
            self.attempt_fd, attempts / f"{binding.submission_id}.lock"
        )
        _fd_matches_private_file(
            self.capacity_fd, slots / f"slot-{slot:03d}.lock"
        )
        os.set_inheritable(self.attempt_fd, False)
        os.set_inheritable(self.capacity_fd, False)
        projection = _read_slot_projection(slots / f"slot-{slot:03d}.json", slot)
        if (
            projection is None
            or projection["generation"] != generation
            or projection["attempt_id"] != self.attempt_id
            or projection["job_id"] != self.run_dir.name
        ):
            raise WorkerControlError(
                "WORKER_FENCE_INVALID: capacity lease projection changed"
            )
        worker_pid = os.getpid()
        if ticket["state"] == "leased" and ticket["worker_pid"] is None:
            ticket.update(
                {
                    "state": "spawned",
                    "worker_pid": worker_pid,
                    "updated_at": _utc_now(),
                }
            )
            ticket = _record_with_hash(
                {
                    key: value
                    for key, value in ticket.items()
                    if key != "record_hash"
                }
            )
            _atomic_write_private_json(
                self.run_dir / LAUNCH_TICKET_NAME, ticket
            )
        elif ticket["state"] != "spawned" or ticket["worker_pid"] != worker_pid:
            raise WorkerControlError(
                "WORKER_FENCE_INVALID: launch ticket Worker identity changed"
            )
        ready_path = self.run_dir / WORKER_READY_NAME
        heartbeat_path = self.run_dir / WORKER_HEARTBEAT_NAME
        if (
            ready_path.exists()
            or ready_path.is_symlink()
            or heartbeat_path.exists()
            or heartbeat_path.is_symlink()
        ):
            raise WorkerControlError(
                "WORKER_ATTEMPT_REPLAY: this attempt already wrote readiness evidence"
            )
        self._binding = binding
        self._ticket = ticket
        ensure_worker_stop_capability(
            self.root,
            binding,
            worker_pid=worker_pid,
            capacity_slot=slot,
            capacity_generation=generation,
            wall_time_seconds=self.wall_time_seconds,
        )
        self._started_at = _utc_now()
        self._started_monotonic = self._monotonic()
        self._write_heartbeat("running")
        thread = threading.Thread(
            target=self._run,
            name="fwi-worker-heartbeat",
            daemon=False,
        )
        self._thread = thread
        try:
            thread.start()
            if not self._thread_started.wait(1.0) or not thread.is_alive():
                raise WorkerControlError(
                    "WORKER_HEARTBEAT_FAILED: heartbeat thread did not start"
                )
            self._write_ready()
            self._ready_published.set()
            self._observe_cancel_request()
        except Exception:
            self._stop.set()
            self._ready_published.set()
            if thread.is_alive():
                thread.join(self.interval_seconds + 1.0)
            self._close_descriptors()
            raise

    def _observe_cancel_request(self) -> None:
        if self._cancel_requested.is_set():
            return
        assert self._binding is not None
        with self._cancel_lock:
            if self._cancel_requested.is_set():
                return
            evidence = read_worker_stop_evidence(self.root, self._binding)
            if not evidence.requested:
                return
            if evidence.reason == "wall_time_exceeded":
                if (
                    evidence.wall_time_seconds != self.wall_time_seconds
                    or self._started_monotonic is None
                ):
                    raise WorkerControlError(
                        "WORKER_STOP_INVALID: timeout policy changed"
                    )
                # The durable Supervisor clock determines when it may publish
                # a timeout request.  This process-local monotonic guard makes
                # a wall-clock jump unable to force an early acknowledgement.
                if (
                    self._monotonic() - self._started_monotonic
                    < self.wall_time_seconds
                ):
                    return
                attempt = read_worker_attempt_evidence(
                    self.root, self.run_dir, self._binding
                )
                if (
                    attempt is None
                    or not attempt.ready
                    or attempt.ready_record_hash
                    != evidence.ready_record_hash
                ):
                    raise WorkerControlError(
                        "WORKER_STOP_INVALID: timeout ready receipt changed"
                    )
            evidence = _acknowledge_worker_stop(self.root, self._binding)
            if not evidence.acknowledged:
                raise WorkerControlError(
                    "WORKER_STOP_INVALID: stop acknowledgement was not durable"
                )
            try:
                deadline_started = self._monotonic()
            except BaseException:
                deadline_started = None
            try:
                force_started = time.monotonic()
            except BaseException:
                force_started = None
            self._stop_evidence = evidence
            self._cancel_deadline = (
                None
                if deadline_started is None
                else deadline_started + self.cancel_grace_seconds
            )
            self._cancel_force_deadline = (
                None
                if force_started is None
                else force_started + self.cancel_grace_seconds
            )
            self._cancel_requested.set()

    def raise_if_cancel_requested(self) -> None:
        """Raise only in the managed numerical main thread at a safe checkpoint."""

        if not self._cancel_requested.is_set():
            self._observe_cancel_request()
            if not self._cancel_requested.is_set():
                return
        with self._cancel_lock:
            evidence = self._stop_evidence
        if evidence is None or evidence.request_id is None or evidence.reason is None:
            raise WorkerControlError(
                "WORKER_STOP_INVALID: stop acknowledgement was lost"
            )
        if evidence.reason == "wall_time_exceeded":
            raise WorkerWallTimeExceeded(evidence.request_id, evidence.reason)
        raise WorkerCancellationRequested(evidence.request_id, evidence.reason)

    @property
    def cancel_evidence(self) -> WorkerCancelEvidence | None:
        with self._cancel_lock:
            evidence = self._stop_evidence
        return None if evidence is None else _stop_evidence_as_cancel(evidence)

    @property
    def stop_evidence(self) -> WorkerStopEvidence | None:
        with self._cancel_lock:
            return self._stop_evidence

    @property
    def checkpoint_binding(self) -> LaunchAttemptBinding:
        """Return the already-validated immutable binding after start()."""

        if self._binding is None or not self._ready_published.is_set():
            raise WorkerControlError(
                "WORKER_CHECKPOINT_INVALID: Worker is not ready"
            )
        return self._binding

    def _write_ready(self) -> None:
        assert self._binding is not None
        assert self._ticket is not None
        assert self._started_at is not None
        ready = _record_with_hash(
            {
                "schema_version": CONTROL_SCHEMA_VERSION,
                "submission_id": self._binding.submission_id,
                "attempt_id": self._binding.attempt_id,
                "attempt_number": self._binding.attempt_number,
                "binding_hash": self._binding.binding_hash,
                "job_id": self._binding.job_id,
                "capacity_slot": self._ticket["capacity_slot"],
                "capacity_generation": self._ticket["capacity_generation"],
                "worker_pid": os.getpid(),
                "started_at": self._started_at,
            }
        )
        _atomic_write_private_json(self.run_dir / WORKER_READY_NAME, ready)

    def _validate_active_projection(self) -> None:
        assert self._ticket is not None
        _, slots, _ = _control_paths(self.root)
        slot = self._ticket["capacity_slot"]
        projection = _read_slot_projection(slots / f"slot-{slot:03d}.json", slot)
        if (
            projection is None
            or projection["generation"] != self._ticket["capacity_generation"]
            or projection["attempt_id"] != self.attempt_id
            or projection["job_id"] != self.run_dir.name
        ):
            raise WorkerControlError(
                "WORKER_FENCE_LOST: capacity lease projection changed"
            )
        os.fstat(self.attempt_fd)
        os.fstat(self.capacity_fd)

    def _write_heartbeat_locked(self, state: str) -> None:
        assert self._binding is not None
        assert self._ticket is not None
        assert self._started_at is not None
        self._validate_active_projection()
        self._sequence += 1
        heartbeat = _record_with_hash(
            {
                "schema_version": CONTROL_SCHEMA_VERSION,
                "submission_id": self._binding.submission_id,
                "attempt_id": self._binding.attempt_id,
                "attempt_number": self._binding.attempt_number,
                "binding_hash": self._binding.binding_hash,
                "job_id": self._binding.job_id,
                "capacity_slot": self._ticket["capacity_slot"],
                "capacity_generation": self._ticket["capacity_generation"],
                "sequence": self._sequence,
                "state": state,
                "worker_pid": os.getpid(),
                "started_at": self._started_at,
                "updated_at": _utc_now(),
            }
        )
        _atomic_write_private_json(
            self.run_dir / WORKER_HEARTBEAT_NAME, heartbeat
        )

    def _write_heartbeat(self, state: str) -> None:
        with self._write_lock:
            self._write_heartbeat_locked(state)

    def _write_active_heartbeat(self) -> None:
        with self._write_lock:
            # Route through the public write seam while holding the re-entrant
            # state lock.  Existing fault-injection tests and operational
            # instrumentation therefore continue to observe active writes.
            self._write_heartbeat(self._active_heartbeat_state)

    def _set_active_heartbeat_state(self, state: str) -> None:
        if state not in {"running", "waiting"}:
            raise WorkerControlError(
                "WORKER_HEARTBEAT_INVALID: active heartbeat state is invalid"
            )
        with self._write_lock:
            self._active_heartbeat_state = state
            self._write_heartbeat_locked(state)

    def wait_for_checkpoint_resume(
        self,
        manifest_evidence: Mapping[str, Any],
        *,
        on_waiting: Callable[[Mapping[str, Any]], None],
        on_resumed: Callable[[Mapping[str, Any], Mapping[str, Any]], None],
    ) -> WorkerCheckpointEvidence:
        """Publish the sole checkpoint and block the same fenced Worker.

        No process is relaunched and neither inherited lease is released.  The
        numerical main thread can leave this method only after the exact
        append-only resume request is acknowledged, or by cooperative stop.
        """

        if self._binding is None or self._ticket is None:
            raise WorkerControlError(
                "WORKER_CHECKPOINT_INVALID: Worker is not started"
            )
        if not callable(on_waiting) or not callable(on_resumed):
            raise WorkerControlError(
                "WORKER_CHECKPOINT_INVALID: checkpoint callbacks are invalid"
            )
        required_manifest_evidence = {
            "checkpoint_id",
            "checkpoint_index",
            "completed_updates",
            "manifest_relative_path",
            "manifest_size_bytes",
            "manifest_hash",
            "checkpoint_created_at",
        }
        evidence = copy.deepcopy(dict(manifest_evidence))
        if (
            set(evidence) != required_manifest_evidence
            or evidence.get("checkpoint_id")
            != checkpoint_id_for_binding(self._binding)
            or evidence.get("checkpoint_index") != 1
            or evidence.get("completed_updates") != 1
            or evidence.get("manifest_relative_path")
            != _checkpoint_manifest_relative_path(evidence["checkpoint_id"])
            or type(evidence.get("manifest_size_bytes")) is not int
            or not 1 <= evidence["manifest_size_bytes"] <= MAX_CONTROL_JSON_BYTES
            or SHA256.fullmatch(evidence.get("manifest_hash", "")) is None
        ):
            raise WorkerControlError(
                "WORKER_CHECKPOINT_INVALID: checkpoint manifest evidence is invalid"
            )
        _parse_timestamp(evidence.get("checkpoint_created_at"))
        for name in (
            WORKER_CHECKPOINT_NAME,
            WORKER_RESUME_REQUEST_NAME,
            WORKER_RESUME_ACK_NAME,
        ):
            path = self.run_dir / name
            if path.exists() or path.is_symlink():
                raise WorkerControlError(
                    "WORKER_CHECKPOINT_REPLAY: checkpoint barrier already exists"
                )
        self._validate_active_projection()
        attempt = read_worker_attempt_evidence(
            self.root, self.run_dir, self._binding
        )
        if (
            attempt is None
            or attempt.ticket_state != "spawned"
            or not attempt.ready
            or attempt.heartbeat_state != "running"
            or attempt.ready_record_hash is None
        ):
            raise WorkerControlError(
                "WORKER_CHECKPOINT_INVALID: running attempt evidence is unavailable"
            )
        receipt = _record_with_hash(
            {
                **self._binding.payload(),
                "binding_hash": self._binding.binding_hash,
                "ticket_record_hash": attempt.ticket_record_hash,
                "ready_record_hash": attempt.ready_record_hash,
                **evidence,
            }
        )
        _validate_checkpoint_receipt(receipt, self._binding, self.run_dir)
        try:
            _create_private_json(self.run_dir / WORKER_CHECKPOINT_NAME, receipt)
        except FileExistsError as error:
            raise WorkerControlError(
                "WORKER_CHECKPOINT_REPLAY: checkpoint receipt already exists"
            ) from error

        # Publish the waiting heartbeat before status.  A concurrent observer
        # treats the tiny interval as CHECKPOINT_PENDING and never authorizes
        # resume until both projections bind this receipt.
        self._set_active_heartbeat_state("waiting")
        on_waiting(copy.deepcopy(receipt))
        waiting = read_worker_checkpoint_evidence(
            self.root, self.run_dir, self._binding
        )
        if waiting is None or waiting.state != "waiting":
            raise WorkerControlError(
                "WORKER_CHECKPOINT_INVALID: waiting evidence was not durable"
            )

        while True:
            self.raise_if_cancel_requested()
            if self._failure is not None or self._stop.is_set():
                raise WorkerControlError(
                    "WORKER_HEARTBEAT_FAILED: heartbeat stopped during checkpoint wait"
                ) from self._failure
            try:
                request_value = _read_private_json(
                    self.run_dir / WORKER_RESUME_REQUEST_NAME
                )
            except FileNotFoundError:
                self._stop.wait(min(0.05, self.interval_seconds))
                continue
            request = _validate_checkpoint_resume_request(
                request_value, self._binding, receipt
            )
            # Resume acknowledgement and stop request publication share the
            # same terminal arbitration.  If stop landed first, the final
            # recheck acknowledges it and exits without a resume ack.  If the
            # ack wins, a later stop observes an already-running Worker.
            with _hold_worker_terminal_arbitration(self.root, self._binding):
                ack_path = self.run_dir / WORKER_RESUME_ACK_NAME
                if ack_path.exists() or ack_path.is_symlink():
                    raise WorkerControlError(
                        "WORKER_RESUME_REPLAY: resume acknowledgement already exists"
                    )
                self.raise_if_cancel_requested()
                acknowledged_at = _utc_now()
                if _parse_timestamp(acknowledged_at) < _parse_timestamp(
                    request["authorized_at"]
                ):
                    raise WorkerControlError(
                        "WORKER_RESUME_INVALID: authorization time is in the future"
                    )
                acknowledgement = _record_with_hash(
                    {
                        "schema_version": CHECKPOINT_PROTOCOL_VERSION,
                        "submission_id": self._binding.submission_id,
                        "attempt_id": self._binding.attempt_id,
                        "attempt_number": self._binding.attempt_number,
                        "checkpoint_id": receipt["checkpoint_id"],
                        "checkpoint_receipt_record_hash": receipt["record_hash"],
                        "resume_id": request["resume_id"],
                        "resume_request_record_hash": request["record_hash"],
                        "resume_acknowledged_at": acknowledged_at,
                    }
                )
                try:
                    _create_private_json(ack_path, acknowledgement)
                except FileExistsError as error:
                    raise WorkerControlError(
                        "WORKER_RESUME_REPLAY: resume acknowledgement already exists"
                    ) from error
                _validate_checkpoint_resume_ack(
                    acknowledgement, self._binding, receipt, request
                )
                # The append-only ack is the commit point.  Publish Running
                # heartbeat/status only after it exists, while stop writers
                # are still excluded by the same arbitration lock.
                self._set_active_heartbeat_state("running")
                on_resumed(copy.deepcopy(receipt), copy.deepcopy(request))
            resumed = read_worker_checkpoint_evidence(
                self.root, self.run_dir, self._binding
            )
            if resumed is None or resumed.state != "resumed":
                raise WorkerControlError(
                    "WORKER_RESUME_INVALID: resume acknowledgement was not durable"
                )
            return resumed

    def _enforce_acknowledged_stop(self) -> None:
        """Keep the exact acknowledged stop deadline authoritative.

        Once the append-only acknowledgement is valid, later heartbeat,
        injected-clock, or wait failures may reduce evidence quality but must
        never set ``_stop`` and let an uncooperative numerical loop escape.
        Only an explicit cooperative ``stop()`` may win before hard exit.
        """

        evidence = self.stop_evidence
        if evidence is None or not evidence.acknowledged:
            raise WorkerControlError(
                "WORKER_STOP_INVALID: acknowledged stop evidence was lost"
            )
        exit_code = (
            WALL_TIME_EXCEEDED_WORKER_EXIT_CODE
            if evidence.reason == "wall_time_exceeded"
            else CANCELLED_WORKER_EXIT_CODE
        )
        while not self._stop.is_set():
            remaining_values: list[float] = []
            if self._cancel_deadline is not None:
                try:
                    remaining_values.append(
                        self._cancel_deadline - self._monotonic()
                    )
                except BaseException:
                    pass
            if self._cancel_force_deadline is not None:
                try:
                    remaining_values.append(
                        self._cancel_force_deadline - time.monotonic()
                    )
                except BaseException:
                    pass
            remaining = min(remaining_values) if remaining_values else 0.0
            if remaining <= 0:
                break
            try:
                if self._stop.wait(min(self.interval_seconds, remaining)):
                    return
            except BaseException:
                # Re-sample the immutable deadlines.  The trusted fallback
                # monotonic deadline prevents a faulty injected clock from
                # turning this into an unbounded retry.
                continue
            if self._stop.is_set():
                return
            try:
                self._write_active_heartbeat()
            except BaseException:
                continue

        if self._stop.is_set():
            return
        try:
            self._write_heartbeat("stopped")
        except BaseException:
            # Stopped evidence is best effort.  The exact process exit remains
            # mandatory and releases both inherited kernel fences.
            pass
        if self._stop.is_set():
            return
        self._hard_exit(exit_code)
        raise WorkerControlError(
            "WORKER_STOP_FAILED: hard-exit callback returned"
        )

    def _run(self) -> None:
        try:
            self._thread_started.set()
            while not self._ready_published.wait(0.01):
                if self._stop.is_set():
                    return
            while not self._stop.is_set():
                self._observe_cancel_request()
                if self._cancel_requested.is_set():
                    self._enforce_acknowledged_stop()
                    return
                if self._stop.wait(self.interval_seconds):
                    return
                self._write_active_heartbeat()
        except BaseException as error:
            self._failure = error
            self._stop.set()

    def stop(self, outcome: str) -> None:
        if outcome not in {"succeeded", "failed", "stopped"}:
            raise WorkerControlError(
                "WORKER_HEARTBEAT_INVALID: terminal heartbeat state is invalid"
            )
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(self.interval_seconds + 1.0)
        try:
            if thread is not None and thread.is_alive():
                raise WorkerControlError(
                    "WORKER_HEARTBEAT_FAILED: heartbeat thread did not stop"
                )
            if self._failure is not None:
                raise WorkerControlError(
                    "WORKER_HEARTBEAT_FAILED: independent heartbeat stopped"
                ) from self._failure
            if self._binding is not None:
                self._write_heartbeat(outcome)
        finally:
            self._close_descriptors()

    def _close_descriptors(self) -> None:
        if self._closed:
            return
        self._closed = True
        for descriptor in (self.attempt_fd, self.capacity_fd):
            try:
                os.close(descriptor)
            except OSError:
                pass


def read_worker_attempt_evidence(
    run_root: Path | str,
    run_dir: Path | str,
    binding: LaunchAttemptBinding,
) -> WorkerAttemptEvidence | None:
    """Read one exact attempt without creating paths or inferring liveness."""

    _, job_dir = _validate_root_and_run(run_root, run_dir)
    try:
        ticket = _read_ticket(job_dir, binding)
    except FileNotFoundError:
        return None
    evidence = {
        "submission_id": binding.submission_id,
        "attempt_id": binding.attempt_id,
        "attempt_number": binding.attempt_number,
        "job_id": binding.job_id,
        "request_hash": binding.request_hash,
        "binding_hash": binding.binding_hash,
        "created_at": binding.created_at,
        "ticket_state": ticket["state"],
        "capacity_slot": ticket["capacity_slot"],
        "capacity_generation": ticket["capacity_generation"],
        "ticket_worker_pid": ticket["worker_pid"],
        "ticket_updated_at": ticket["updated_at"],
        "ticket_record_hash": ticket["record_hash"],
    }
    if ticket["state"] != "spawned":
        return WorkerAttemptEvidence(**evidence)
    try:
        ready = _read_private_json(job_dir / WORKER_READY_NAME)
    except FileNotFoundError:
        return WorkerAttemptEvidence(**evidence)
    ready_required = {
        "schema_version",
        "submission_id",
        "attempt_id",
        "attempt_number",
        "binding_hash",
        "job_id",
        "capacity_slot",
        "capacity_generation",
        "worker_pid",
        "started_at",
        "record_hash",
    }
    if set(ready) != ready_required:
        raise WorkerControlError(
            "WORKER_READY_INVALID: ready receipt fields are inconsistent"
        )
    _validate_record_hash(ready)
    ready_expected = {
        "schema_version": CONTROL_SCHEMA_VERSION,
        "submission_id": binding.submission_id,
        "attempt_id": binding.attempt_id,
        "attempt_number": binding.attempt_number,
        "binding_hash": binding.binding_hash,
        "job_id": binding.job_id,
        "capacity_slot": ticket["capacity_slot"],
        "capacity_generation": ticket["capacity_generation"],
    }
    if any(ready.get(key) != value for key, value in ready_expected.items()):
        raise WorkerControlError(
            "WORKER_READY_INVALID: ready receipt binding changed"
        )
    if type(ready["worker_pid"]) is not int or ready["worker_pid"] <= 0:
        raise WorkerControlError(
            "WORKER_READY_INVALID: ready Worker identity is invalid"
        )
    _parse_timestamp(ready["started_at"])
    if ticket["worker_pid"] != ready["worker_pid"]:
        raise WorkerControlError(
            "WORKER_READY_INVALID: ready Worker differs from launch ticket"
        )
    evidence.update(
        {
            "ready_worker_pid": ready["worker_pid"],
            "ready_started_at": ready["started_at"],
            "ready_record_hash": ready["record_hash"],
        }
    )
    try:
        heartbeat = _read_private_json(job_dir / WORKER_HEARTBEAT_NAME)
    except FileNotFoundError as error:
        # WorkerHeartbeat writes the initial heartbeat before it publishes the
        # ready receipt.  With the submission lock held by the Adapter reader,
        # ready-without-heartbeat is therefore corruption, not a launch
        # transient that may be projected indefinitely.
        raise WorkerControlError(
            "WORKER_HEARTBEAT_INVALID: ready receipt has no heartbeat"
        ) from error
    required = {
        "schema_version",
        "submission_id",
        "attempt_id",
        "attempt_number",
        "binding_hash",
        "job_id",
        "capacity_slot",
        "capacity_generation",
        "sequence",
        "state",
        "worker_pid",
        "started_at",
        "updated_at",
        "record_hash",
    }
    if set(heartbeat) != required:
        raise WorkerControlError(
            "WORKER_HEARTBEAT_INVALID: heartbeat fields are inconsistent"
        )
    _validate_record_hash(heartbeat)
    expected = {
        "schema_version": CONTROL_SCHEMA_VERSION,
        "submission_id": binding.submission_id,
        "attempt_id": binding.attempt_id,
        "attempt_number": binding.attempt_number,
        "binding_hash": binding.binding_hash,
        "job_id": binding.job_id,
        "capacity_slot": ticket["capacity_slot"],
        "capacity_generation": ticket["capacity_generation"],
        "worker_pid": ready["worker_pid"],
        "started_at": ready["started_at"],
    }
    if any(heartbeat.get(key) != value for key, value in expected.items()):
        raise WorkerControlError(
            "WORKER_HEARTBEAT_INVALID: heartbeat binding changed"
        )
    if (
        type(heartbeat["sequence"]) is not int
        or heartbeat["sequence"] < 1
        or heartbeat["state"]
        not in {"running", "waiting", "succeeded", "failed", "stopped"}
        or type(heartbeat["worker_pid"]) is not int
        or heartbeat["worker_pid"] <= 0
    ):
        raise WorkerControlError(
            "WORKER_HEARTBEAT_INVALID: heartbeat state is invalid"
        )
    _parse_timestamp(heartbeat["started_at"])
    _parse_timestamp(heartbeat["updated_at"])
    evidence.update(
        {
            "heartbeat_sequence": heartbeat["sequence"],
            "heartbeat_state": heartbeat["state"],
            "heartbeat_updated_at": heartbeat["updated_at"],
            "heartbeat_record_hash": heartbeat["record_hash"],
        }
    )
    return WorkerAttemptEvidence(**evidence)


def read_pre_running_attempt_evidence(
    run_root: Path | str,
    run_dir: Path | str,
    binding: LaunchAttemptBinding,
) -> WorkerAttemptEvidence | None:
    """Read an attempt only if no ready or heartbeat sidecar exists.

    The ordinary evidence reader intentionally stops at a non-spawned ticket.
    Retry proof needs a stronger negative fact: a failed ticket must not hide
    sidecars left by an attempt that may have crossed the B1 boundary.  A
    heartbeat without ready is deliberately treated as reconciliation
    uncertainty: durable evidence cannot distinguish a failed ready publish
    from a deleted/corrupt ready receipt.  A caller relying on this absence
    must also hold the exact execution fence.
    """

    _, job_dir = _validate_root_and_run(run_root, run_dir)
    evidence = read_worker_attempt_evidence(run_root, job_dir, binding)
    if evidence is not None and (
        evidence.ready or evidence.heartbeat_record_hash is not None
    ):
        raise WorkerControlError(
            "WORKER_RETRY_UNSAFE: Worker attempt has started evidence"
        )
    for name in (WORKER_READY_NAME, WORKER_HEARTBEAT_NAME):
        try:
            _read_private_json(job_dir / name)
        except FileNotFoundError:
            continue
        raise WorkerControlError(
            "WORKER_RETRY_UNSAFE: Worker attempt has a start sidecar"
        )
    return evidence


_WORKER_EXIT_STATUS_MUTATIONS = frozenset(
    {"status", "stage", "message", "updated_at"}
)


def _worker_exit_status_hashes(
    binding: LaunchAttemptBinding,
    pre_status: Mapping[str, Any],
    post_status: Mapping[str, Any],
    return_code: int,
) -> tuple[str, str]:
    """Validate and hash the reaper's exact nonterminal-to-exit transition."""

    if not isinstance(pre_status, Mapping) or not isinstance(post_status, Mapping):
        raise WorkerControlError(
            "WORKER_EXIT_INVALID: status evidence must be JSON objects"
        )
    pre = copy.deepcopy(dict(pre_status))
    post = copy.deepcopy(dict(post_status))
    if set(pre) != set(post):
        raise WorkerControlError(
            "WORKER_EXIT_INVALID: worker-exit status fields changed"
        )
    if pre.get("job_id") != binding.job_id or post.get("job_id") != binding.job_id:
        raise WorkerControlError(
            "WORKER_EXIT_INVALID: worker-exit status job changed"
        )
    if pre.get("status") not in {"queued", "running"}:
        raise WorkerControlError(
            "WORKER_EXIT_UNSAFE: terminal Worker status cannot become worker_exit"
        )
    if (
        post.get("status") != "failed"
        or post.get("stage") != "worker_exit"
        or post.get("failure_code") is not None
        or post.get("message")
        != f"FWI worker exited with code {return_code}"
    ):
        raise WorkerControlError(
            "WORKER_EXIT_INVALID: post status is not an ordinary worker_exit"
        )
    if not isinstance(pre.get("stage"), str) or not isinstance(
        post.get("message"), str
    ):
        raise WorkerControlError(
            "WORKER_EXIT_INVALID: worker-exit status text is invalid"
        )
    _parse_timestamp(pre.get("updated_at"))
    if _parse_timestamp(post.get("updated_at")) < _parse_timestamp(
        pre.get("updated_at")
    ):
        raise WorkerControlError(
            "WORKER_EXIT_INVALID: worker-exit status time moved backwards"
        )
    for key in set(pre) - _WORKER_EXIT_STATUS_MUTATIONS:
        if pre[key] != post[key]:
            raise WorkerControlError(
                "WORKER_EXIT_INVALID: worker-exit changed scientific status data"
            )
    pre_hash = _sha256_document(pre)
    post_hash = _sha256_document(post)
    if pre_hash == post_hash:
        raise WorkerControlError(
            "WORKER_EXIT_INVALID: worker-exit status transition is empty"
        )
    return pre_hash, post_hash


def _require_no_worker_exit_stop(
    run_root: Path | str, binding: LaunchAttemptBinding
) -> None:
    """Reject every requested v2 or legacy stop channel, including corruption."""

    capability = read_worker_stop_capability(run_root, binding)
    if capability is None:
        raise WorkerControlError(
            "WORKER_EXIT_UNSAFE: exact Worker has no v2 stop capability"
        )
    stop = read_worker_stop_evidence(run_root, binding)
    if stop.requested or stop.acknowledged:
        raise WorkerControlError(
            "WORKER_EXIT_UNSAFE: Worker has a stop request"
        )
    legacy_capability = _read_legacy_worker_cancel_capability(
        run_root, binding
    )
    if legacy_capability is not None:
        legacy = _read_legacy_worker_cancel_evidence(run_root, binding)
        if legacy.requested or legacy.acknowledged:
            raise WorkerControlError(
                "WORKER_EXIT_UNSAFE: Worker has a legacy cancellation request"
            )


def _validate_worker_exit_receipt(
    value: Mapping[str, Any], binding: LaunchAttemptBinding
) -> WorkerExitEvidence:
    required = {
        *binding.payload().keys(),
        "binding_hash",
        "ticket_record_hash",
        "ready_record_hash",
        "heartbeat_sequence",
        "heartbeat_state",
        "heartbeat_record_hash",
        "pre_status_hash",
        "post_status_hash",
        "return_code",
        "observed_at",
        "record_hash",
    }
    if set(value) != required:
        raise WorkerControlError(
            "WORKER_EXIT_INVALID: worker-exit receipt fields are invalid"
        )
    _validate_record_hash(value)
    if any(
        value.get(key) != expected
        for key, expected in {
            **binding.payload(),
            "binding_hash": binding.binding_hash,
        }.items()
    ):
        raise WorkerControlError(
            "WORKER_EXIT_INVALID: worker-exit receipt binding changed"
        )
    if any(
        SHA256.fullmatch(value.get(field, "")) is None
        for field in (
            "ticket_record_hash",
            "ready_record_hash",
            "heartbeat_record_hash",
            "pre_status_hash",
            "post_status_hash",
        )
    ):
        raise WorkerControlError(
            "WORKER_EXIT_INVALID: worker-exit evidence hash is invalid"
        )
    if (
        type(value.get("heartbeat_sequence")) is not int
        or value["heartbeat_sequence"] < 1
        or value.get("heartbeat_state") != "running"
        or type(value.get("return_code")) is not int
        or value["return_code"]
        in {
            0,
            CANCELLED_WORKER_EXIT_CODE,
            WALL_TIME_EXCEEDED_WORKER_EXIT_CODE,
        }
        or value["pre_status_hash"] == value["post_status_hash"]
    ):
        raise WorkerControlError(
            "WORKER_EXIT_INVALID: worker-exit outcome is invalid"
        )
    _parse_timestamp(value.get("observed_at"))
    return WorkerExitEvidence(
        submission_id=value["submission_id"],
        attempt_id=value["attempt_id"],
        attempt_number=value["attempt_number"],
        job_id=value["job_id"],
        request_hash=value["request_hash"],
        binding_hash=value["binding_hash"],
        created_at=value["created_at"],
        ticket_record_hash=value["ticket_record_hash"],
        ready_record_hash=value["ready_record_hash"],
        heartbeat_sequence=value["heartbeat_sequence"],
        heartbeat_state=value["heartbeat_state"],
        heartbeat_record_hash=value["heartbeat_record_hash"],
        pre_status_hash=value["pre_status_hash"],
        post_status_hash=value["post_status_hash"],
        return_code=value["return_code"],
        observed_at=value["observed_at"],
        record_hash=value["record_hash"],
    )


def _read_worker_exit_context(
    run_root: Path | str,
    run_dir: Path,
    binding: LaunchAttemptBinding,
) -> tuple[WorkerAttemptEvidence, dict[str, Any], str]:
    try:
        checkpoint = _read_private_json(run_dir / WORKER_CHECKPOINT_NAME)
    except FileNotFoundError:
        checkpoint = None
    if checkpoint is not None:
        _validate_checkpoint_receipt(checkpoint, binding, run_dir)
        try:
            checkpoint_evidence = read_worker_checkpoint_evidence(
                run_root, run_dir, binding
            )
        except WorkerControlError as error:
            raise WorkerControlError(
                "WORKER_EXIT_UNSAFE: unresolved checkpoint wait is not retryable"
            ) from error
        if checkpoint_evidence is None or checkpoint_evidence.state != "resumed":
            raise WorkerControlError(
                "WORKER_EXIT_UNSAFE: unresolved checkpoint wait is not retryable"
            )
    attempt = read_worker_attempt_evidence(run_root, run_dir, binding)
    if (
        attempt is None
        or attempt.ticket_state != "spawned"
        or not attempt.ready
        or not attempt.started
        or attempt.heartbeat_state != "running"
        or attempt.ready_record_hash is None
        or attempt.heartbeat_sequence is None
        or attempt.heartbeat_record_hash is None
    ):
        raise WorkerControlError(
            "WORKER_EXIT_UNSAFE: exact ready Worker has no running exit evidence"
        )
    _require_no_worker_exit_stop(run_root, binding)
    try:
        status = _read_private_json(run_dir / "status.json")
    except FileNotFoundError as error:
        raise WorkerControlError(
            "WORKER_EXIT_UNSAFE: Worker status evidence is missing"
        ) from error
    return attempt, status, _sha256_document(status)


def _validate_live_worker_exit(
    evidence: WorkerExitEvidence,
    attempt: WorkerAttemptEvidence,
    status: Mapping[str, Any],
    status_hash: str,
) -> None:
    if (
        evidence.ticket_record_hash != attempt.ticket_record_hash
        or evidence.ready_record_hash != attempt.ready_record_hash
        or evidence.heartbeat_sequence != attempt.heartbeat_sequence
        or evidence.heartbeat_state != attempt.heartbeat_state
        or evidence.heartbeat_record_hash != attempt.heartbeat_record_hash
    ):
        raise WorkerControlError(
            "WORKER_EXIT_INVALID: worker-exit sidecar evidence changed"
        )
    if status_hash == evidence.pre_status_hash:
        if status.get("job_id") != evidence.job_id or status.get("status") not in {
            "queued",
            "running",
        }:
            raise WorkerControlError(
                "WORKER_EXIT_INVALID: pre-exit status no longer matches"
            )
    elif status_hash == evidence.post_status_hash:
        if (
            status.get("job_id") != evidence.job_id
            or status.get("status") != "failed"
            or status.get("stage") != "worker_exit"
            or status.get("failure_code") is not None
        ):
            raise WorkerControlError(
                "WORKER_EXIT_INVALID: post-exit status no longer matches"
            )
    else:
        raise WorkerControlError(
            "WORKER_EXIT_INVALID: Worker status is outside the bound transition"
        )


def _finalize_worker_exit_status(
    evidence: WorkerExitEvidence,
    run_dir: Path,
    status: Mapping[str, Any],
    status_hash: str,
    *,
    post_status: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    """Install only the exact post document bound by an existing receipt."""

    if status_hash == evidence.post_status_hash:
        return copy.deepcopy(dict(status)), status_hash
    if status_hash != evidence.pre_status_hash:
        raise WorkerControlError(
            "WORKER_EXIT_INVALID: Worker status is outside the bound transition"
        )
    candidate = (
        copy.deepcopy(dict(post_status))
        if post_status is not None
        else {
            **copy.deepcopy(dict(status)),
            "status": "failed",
            "stage": "worker_exit",
            "message": f"FWI worker exited with code {evidence.return_code}",
            "updated_at": evidence.observed_at,
        }
    )
    if _sha256_document(candidate) != evidence.post_status_hash:
        raise WorkerControlError(
            "WORKER_EXIT_INVALID: bound post status cannot be reconstructed"
        )
    status_path = run_dir / "status.json"
    # Re-check immediately before replacement.  Legitimate terminal writers
    # share the arbitration lock; an uncoordinated drift remains visible and
    # is never overwritten deliberately.
    current = _read_private_json(status_path)
    current_hash = _sha256_document(current)
    if current_hash == evidence.post_status_hash:
        return current, current_hash
    if current_hash != evidence.pre_status_hash:
        raise WorkerControlError(
            "WORKER_EXIT_INVALID: Worker status changed before finalization"
        )
    _atomic_write_private_json(status_path, candidate)
    written = _read_private_json(status_path)
    written_hash = _sha256_document(written)
    if written_hash != evidence.post_status_hash:
        raise WorkerControlError(
            "WORKER_EXIT_INVALID: Worker exit finalization changed"
        )
    return written, written_hash


def record_worker_exit(
    run_root: Path | str,
    run_dir: Path | str,
    binding: LaunchAttemptBinding,
    *,
    return_code: int,
    pre_status: Mapping[str, Any],
    post_status: Mapping[str, Any],
    observed_at: str | None = None,
) -> WorkerExitEvidence:
    """Append one exact post-ready unexpected-exit receipt.

    The caller must have synchronously reaped the exact process.  This method
    independently proves that the inherited execution fence is idle.  It
    publishes the receipt first, then atomically installs only the already-
    bound ``post_status`` document.  A reader can finish that exact transition
    after a crash between those two durable writes.
    """

    if type(return_code) is not int or return_code in {
        0,
        CANCELLED_WORKER_EXIT_CODE,
        WALL_TIME_EXCEEDED_WORKER_EXIT_CODE,
    }:
        raise WorkerControlError(
            "WORKER_EXIT_INVALID: return code is not an unexpected failure"
        )
    pre_hash, post_hash = _worker_exit_status_hashes(
        binding, pre_status, post_status, return_code
    )
    status_observed_at = post_status.get("updated_at")
    if observed_at is None:
        observed_at = status_observed_at
    if observed_at != status_observed_at:
        raise WorkerControlError(
            "WORKER_EXIT_INVALID: observation time differs from post status"
        )
    _parse_timestamp(observed_at)
    root, job_dir = _validate_root_and_run(run_root, run_dir)
    receipt_path = job_dir / WORKER_EXIT_NAME
    with _hold_worker_terminal_arbitration(root, binding):
        with hold_idle_execution_fence(root, binding):
            attempt, status, status_hash = _read_worker_exit_context(
                root, job_dir, binding
            )
            try:
                existing_value = _read_private_json(receipt_path)
            except FileNotFoundError:
                existing_value = None
            if existing_value is None:
                if status_hash != pre_hash:
                    raise WorkerControlError(
                        "WORKER_EXIT_UNSAFE: initial receipt requires the "
                        "pre-exit status"
                    )
                candidate = _record_with_hash(
                    {
                        **binding.payload(),
                        "binding_hash": binding.binding_hash,
                        "ticket_record_hash": attempt.ticket_record_hash,
                        "ready_record_hash": attempt.ready_record_hash,
                        "heartbeat_sequence": attempt.heartbeat_sequence,
                        "heartbeat_state": attempt.heartbeat_state,
                        "heartbeat_record_hash": attempt.heartbeat_record_hash,
                        "pre_status_hash": pre_hash,
                        "post_status_hash": post_hash,
                        "return_code": return_code,
                        "observed_at": observed_at,
                    }
                )
                try:
                    _create_private_json(receipt_path, candidate)
                    existing_value = candidate
                except FileExistsError:
                    existing_value = _read_private_json(receipt_path)
            evidence = _validate_worker_exit_receipt(existing_value, binding)
            expected = {
                "ticket_record_hash": attempt.ticket_record_hash,
                "ready_record_hash": attempt.ready_record_hash,
                "heartbeat_sequence": attempt.heartbeat_sequence,
                "heartbeat_state": "running",
                "heartbeat_record_hash": attempt.heartbeat_record_hash,
                "pre_status_hash": pre_hash,
                "post_status_hash": post_hash,
                "return_code": return_code,
            }
            if any(
                getattr(evidence, key) != value
                for key, value in expected.items()
            ):
                raise WorkerControlError(
                    "WORKER_EXIT_CONFLICT: attempt has another worker-exit receipt"
                )
            if evidence.observed_at != observed_at:
                raise WorkerControlError(
                    "WORKER_EXIT_CONFLICT: worker-exit observation time changed"
                )
            # Re-read after append-only publication so concurrent stop/status
            # evidence cannot make a successful return look stronger than it is.
            attempt, status, status_hash = _read_worker_exit_context(
                root, job_dir, binding
            )
            _validate_live_worker_exit(evidence, attempt, status, status_hash)
            status, status_hash = _finalize_worker_exit_status(
                evidence,
                job_dir,
                status,
                status_hash,
                post_status=post_status,
            )
            _validate_live_worker_exit(evidence, attempt, status, status_hash)
            _require_no_worker_exit_stop(root, binding)
            return evidence


def read_worker_exit_evidence(
    run_root: Path | str,
    run_dir: Path | str,
    binding: LaunchAttemptBinding,
) -> WorkerExitEvidence:
    """Re-prove and return one exact append-only unexpected-exit receipt."""

    root, job_dir = _validate_root_and_run(run_root, run_dir)
    with _hold_worker_terminal_arbitration(root, binding):
        with hold_idle_execution_fence(root, binding):
            try:
                value = _read_private_json(job_dir / WORKER_EXIT_NAME)
            except FileNotFoundError as error:
                raise WorkerControlError(
                    "WORKER_EXIT_MISSING: worker-exit receipt is missing"
                ) from error
            evidence = _validate_worker_exit_receipt(value, binding)
            attempt, status, status_hash = _read_worker_exit_context(
                root, job_dir, binding
            )
            _validate_live_worker_exit(evidence, attempt, status, status_hash)
            status, status_hash = _finalize_worker_exit_status(
                evidence, job_dir, status, status_hash
            )
            _validate_live_worker_exit(evidence, attempt, status, status_hash)
            _require_no_worker_exit_stop(root, binding)
            return evidence


def worker_attempt_started(
    run_root: Path | str,
    run_dir: Path | str,
    binding: LaunchAttemptBinding,
) -> bool:
    """Return true only after the exact fenced Worker wrote a heartbeat."""

    evidence = read_worker_attempt_evidence(run_root, run_dir, binding)
    return evidence is not None and evidence.started


__all__ = [
    "CANCELLED_WORKER_EXIT_CODE",
    "CHECKPOINT_PROTOCOL_VERSION",
    "CONTROL_DIRECTORY",
    "STOP_PROTOCOL_VERSION",
    "SUPPORTED_STOP_REASONS",
    "WALL_TIME_EXCEEDED_WORKER_EXIT_CODE",
    "WORKER_EXIT_NAME",
    "WORKER_CHECKPOINT_NAME",
    "WORKER_RESUME_ACK_NAME",
    "WORKER_RESUME_REQUEST_NAME",
    "LaunchAttemptBinding",
    "ParentLaunchLease",
    "WorkerAttemptEvidence",
    "WorkerCheckpointEvidence",
    "WorkerCancelEvidence",
    "WorkerCancellationRequested",
    "WorkerControlError",
    "WorkerExitEvidence",
    "WorkerHeartbeat",
    "WorkerStopEvidence",
    "WorkerWallTimeExceeded",
    "binding_from_submission_record",
    "checkpoint_id_for_binding",
    "ensure_worker_cancel_capability",
    "ensure_worker_stop_capability",
    "execution_fence_is_held",
    "hold_idle_execution_fence",
    "mark_launch_failed",
    "purge_worker_cancel_control",
    "purge_worker_stop_control",
    "read_worker_exit_evidence",
    "read_pre_running_attempt_evidence",
    "read_worker_cancel_capability",
    "read_worker_cancel_evidence",
    "read_worker_checkpoint_evidence",
    "read_worker_stop_capability",
    "read_worker_stop_evidence",
    "read_worker_attempt_evidence",
    "request_worker_cancel",
    "request_worker_checkpoint_resume",
    "request_worker_stop",
    "record_worker_exit",
    "stage_launch_attempt",
    "worker_attempt_started",
]
