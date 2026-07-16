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

import copy
import contextlib
import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping


CONTROL_DIRECTORY = ".scientific-runtime-adapter-v1"
LAUNCH_TICKET_NAME = ".worker-launch.json"
WORKER_READY_NAME = ".worker-ready.json"
WORKER_HEARTBEAT_NAME = ".worker-heartbeat.json"
CONTROL_SCHEMA_VERSION = "1.0.0"
MAX_CONTROL_JSON_BYTES = 64 * 1024
MAX_CAPACITY = 64

SUBMISSION_ID = re.compile(r"^submission-[0-9a-f]{64}$")
ATTEMPT_ID = re.compile(r"^attempt-[0-9a-f]{32}$")
JOB_ID = re.compile(r"^fwi-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$")
SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


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


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _parse_timestamp(value: Any) -> None:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise WorkerControlError("WORKER_CONTROL_INVALID: timestamp is invalid")
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise WorkerControlError(
            "WORKER_CONTROL_INVALID: timestamp is invalid"
        ) from error


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


def _read_private_json(path: Path) -> dict[str, Any]:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
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
    finally:
        os.close(descriptor)
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
        valid_projection = worker_pid is None
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
        self.root, self.run_dir = _validate_root_and_run(run_root, run_dir)
        self.attempt_id = attempt_id
        self.attempt_fd = attempt_fd
        self.capacity_fd = capacity_fd
        self.interval_seconds = float(interval_seconds)
        self._stop = threading.Event()
        self._thread_started = threading.Event()
        self._thread: threading.Thread | None = None
        self._write_lock = threading.Lock()
        self._failure: BaseException | None = None
        self._sequence = 0
        self._started_at: str | None = None
        self._ticket: dict[str, Any] | None = None
        self._binding: LaunchAttemptBinding | None = None
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
        self._started_at = _utc_now()
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
        except Exception:
            self._stop.set()
            if thread.is_alive():
                thread.join(self.interval_seconds + 1.0)
            self._close_descriptors()
            raise

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

    def _write_heartbeat(self, state: str) -> None:
        assert self._binding is not None
        assert self._ticket is not None
        assert self._started_at is not None
        with self._write_lock:
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

    def _run(self) -> None:
        try:
            self._thread_started.set()
            while not self._stop.wait(self.interval_seconds):
                self._write_heartbeat("running")
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


def worker_attempt_started(
    run_root: Path | str,
    run_dir: Path | str,
    binding: LaunchAttemptBinding,
) -> bool:
    """Return true only after the exact fenced Worker wrote a heartbeat."""

    _, job_dir = _validate_root_and_run(run_root, run_dir)
    try:
        ticket = _read_ticket(job_dir, binding)
    except FileNotFoundError:
        return False
    if ticket["state"] not in {"leased", "spawned"}:
        return False
    try:
        ready = _read_private_json(job_dir / WORKER_READY_NAME)
    except FileNotFoundError:
        return False
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
    try:
        heartbeat = _read_private_json(job_dir / WORKER_HEARTBEAT_NAME)
    except FileNotFoundError:
        return False
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
        or heartbeat["state"] not in {"running", "succeeded", "failed", "stopped"}
        or type(heartbeat["worker_pid"]) is not int
        or heartbeat["worker_pid"] <= 0
    ):
        raise WorkerControlError(
            "WORKER_HEARTBEAT_INVALID: heartbeat state is invalid"
        )
    _parse_timestamp(heartbeat["started_at"])
    _parse_timestamp(heartbeat["updated_at"])
    return True


__all__ = [
    "CONTROL_DIRECTORY",
    "LaunchAttemptBinding",
    "ParentLaunchLease",
    "WorkerControlError",
    "WorkerHeartbeat",
    "binding_from_submission_record",
    "execution_fence_is_held",
    "hold_idle_execution_fence",
    "mark_launch_failed",
    "stage_launch_attempt",
    "worker_attempt_started",
]
