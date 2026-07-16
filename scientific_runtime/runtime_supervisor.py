"""Fenced, observation-only runtime status supervisor.

The supervisor deliberately has no dispatch or Worker-starting capability.  It
owns one scope-local fenced lease and periodically asks :class:`TaskService` to
observe only tasks that already have a durable ``dispatched`` receipt.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .task_store import RuntimeSupervisorLeaseLost, TaskStoreError


_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_STABLE_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")

LEASE_HELD = "RUNTIME_SUPERVISOR_LEASE_HELD"
LEASE_LOST = "RUNTIME_SUPERVISOR_LEASE_LOST"
LEASE_ACQUIRE_FAILED = "RUNTIME_SUPERVISOR_LEASE_ACQUIRE_FAILED"
LEASE_RELEASE_FAILED = "RUNTIME_SUPERVISOR_LEASE_RELEASE_FAILED"
START_TIMEOUT = "RUNTIME_SUPERVISOR_START_TIMEOUT"
STOP_TIMEOUT = "RUNTIME_SUPERVISOR_STOP_TIMEOUT"
TASK_LIMIT_EXCEEDED = "RUNTIME_SUPERVISOR_TASK_LIMIT_EXCEEDED"
SCAN_FAILED = "RUNTIME_SUPERVISOR_SCAN_FAILED"
STORE_FAILED = "RUNTIME_SUPERVISOR_STORE_FAILED"
FATAL = "RUNTIME_SUPERVISOR_FATAL"


class RuntimeSupervisorTaskService(Protocol):
    """The narrow TaskService surface available to the supervisor."""

    def acquire_runtime_supervisor_lease(
        self,
        *,
        project_id: str,
        principal_id: str,
        owner_id: str,
        lease_seconds: int,
    ) -> Any:
        ...

    def heartbeat_runtime_supervisor_lease(
        self, lease: Any, *, lease_seconds: int
    ) -> Any:
        ...

    def release_runtime_supervisor_lease(self, lease: Any) -> Any:
        ...

    def list_tasks(
        self,
        *,
        project_id: str,
        principal_id: str,
        cursor: str | None = None,
        limit: int = 20,
        view: str = "active",
    ) -> Any:
        ...

    def get_dispatch_intent(
        self, task_id: str, *, project_id: str, principal_id: str
    ) -> Any:
        ...

    def refresh_runtime_status(
        self,
        task_id: str,
        *,
        project_id: str,
        principal_id: str,
        supervisor_lease: Any,
    ) -> Any:
        ...


@dataclass(frozen=True)
class RuntimeSupervisorCycleResult:
    """Sanitized outcome of the most recently completed observation cycle."""

    scanned_task_ids: tuple[str, ...]
    refreshed_task_ids: tuple[str, ...]
    deferred: tuple[tuple[str, str], ...]
    task_failures: tuple[tuple[str, str], ...]


class _SupervisorFailure(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class RuntimeSupervisor:
    """Continuously observe dispatched tasks under one fenced supervisor lease.

    Construction is side-effect free.  :meth:`start` is the only method that
    acquires a lease or creates a thread, and :meth:`stop` is the only normal
    path that releases an owned lease.  The worker thread is intentionally
    non-daemon so process shutdown cannot silently abandon in-flight state.
    """

    def __init__(
        self,
        task_service: RuntimeSupervisorTaskService,
        *,
        project_id: str,
        principal_id: str,
        owner_id: str,
        lease_seconds: int = 30,
        heartbeat_interval_seconds: float | None = None,
        poll_interval_seconds: float = 1.0,
        max_tasks: int = 10_000,
        start_timeout_seconds: float = 5.0,
        join_timeout_seconds: float = 5.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        for field, value in (
            ("project_id", project_id),
            ("principal_id", principal_id),
            ("owner_id", owner_id),
        ):
            if not isinstance(value, str) or _OPAQUE_ID.fullmatch(value) is None:
                raise ValueError(f"{field} must be a v1 opaque identifier")
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be an integer from 1 to 3600")
        if heartbeat_interval_seconds is None:
            heartbeat_interval_seconds = lease_seconds / 3
        if (
            isinstance(heartbeat_interval_seconds, bool)
            or not isinstance(heartbeat_interval_seconds, (int, float))
            or not 0 < heartbeat_interval_seconds < lease_seconds
        ):
            raise ValueError(
                "heartbeat_interval_seconds must be positive and shorter than the lease"
            )
        for field, value in (
            ("poll_interval_seconds", poll_interval_seconds),
            ("start_timeout_seconds", start_timeout_seconds),
            ("join_timeout_seconds", join_timeout_seconds),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value <= 0
            ):
                raise ValueError(f"{field} must be positive")
        if type(max_tasks) is not int or not 1 <= max_tasks <= 10_000:
            raise ValueError("max_tasks must be an integer from 1 to 10000")
        if not callable(monotonic):
            raise ValueError("monotonic must be callable")

        self._task_service = task_service
        self._project_id = project_id
        self._principal_id = principal_id
        self._owner_id = owner_id
        self._lease_seconds = lease_seconds
        self._heartbeat_interval_seconds = float(heartbeat_interval_seconds)
        self._poll_interval_seconds = float(poll_interval_seconds)
        self._max_tasks = max_tasks
        self._start_timeout_seconds = float(start_timeout_seconds)
        self._join_timeout_seconds = float(join_timeout_seconds)
        self._monotonic = monotonic

        self._lifecycle_lock = threading.Lock()
        self._state = threading.Condition()
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lease: Any = None
        self._healthy = False
        self._failure_code: str | None = None
        self._cycle_count = 0
        self._last_cycle: RuntimeSupervisorCycleResult | None = None

    @property
    def running(self) -> bool:
        with self._state:
            return self._thread is not None and self._thread.is_alive()

    @property
    def healthy(self) -> bool:
        with self._state:
            return self._healthy

    @property
    def failure_code(self) -> str | None:
        with self._state:
            return self._failure_code

    @property
    def lease(self) -> Any:
        with self._state:
            return self._lease

    @property
    def thread(self) -> threading.Thread | None:
        with self._state:
            return self._thread

    @property
    def cycle_count(self) -> int:
        with self._state:
            return self._cycle_count

    @property
    def last_cycle(self) -> RuntimeSupervisorCycleResult | None:
        with self._state:
            return self._last_cycle

    def wait_for_cycle(self, minimum_count: int = 1, timeout: float = 5.0) -> bool:
        """Wait for a completed cycle without introducing a polling thread."""

        if type(minimum_count) is not int or minimum_count < 1:
            raise ValueError("minimum_count must be a positive integer")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ValueError("timeout must be a non-negative number")
        if timeout < 0:
            raise ValueError("timeout must be a non-negative number")
        with self._state:
            self._state.wait_for(
                lambda: self._cycle_count >= minimum_count
                or self._failure_code is not None
                or (self._thread is not None and not self._thread.is_alive()),
                timeout=float(timeout),
            )
            return self._cycle_count >= minimum_count

    def wait_until_stopped(self, timeout: float = 5.0) -> bool:
        """Wait for the current worker to terminate without releasing its lease."""

        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ValueError("timeout must be a non-negative number")
        if timeout < 0:
            raise ValueError("timeout must be a non-negative number")
        with self._state:
            thread = self._thread
        if thread is None:
            return True
        if thread is threading.current_thread():
            return False
        thread.join(float(timeout))
        return not thread.is_alive()

    def start(self) -> bool:
        """Acquire the scope lease and start exactly one ready worker thread.

        ``False`` is returned when another owner holds the lease or startup
        cannot safely complete.  Details are available through
        :attr:`failure_code`; a foreign lease is never retained or released.
        """

        with self._lifecycle_lock:
            with self._state:
                if self._thread is not None and self._thread.is_alive():
                    return self._healthy
                if self._lease is not None:
                    self._healthy = False
                    self._state.notify_all()
                    return False
                self._failure_code = None
                self._healthy = False
                self._cycle_count = 0
                self._last_cycle = None
                self._stop_event = threading.Event()
                self._ready_event = threading.Event()

            try:
                acquisition = self._task_service.acquire_runtime_supervisor_lease(
                    project_id=self._project_id,
                    principal_id=self._principal_id,
                    owner_id=self._owner_id,
                    lease_seconds=self._lease_seconds,
                )
            except Exception as error:
                self._set_failure(
                    LEASE_LOST if self._is_lease_lost(error) else LEASE_ACQUIRE_FAILED
                )
                return False

            acquired = getattr(acquisition, "acquired", None)
            lease = getattr(acquisition, "lease", None)
            if type(acquired) is not bool or lease is None:
                self._set_failure(LEASE_ACQUIRE_FAILED)
                return False
            if not acquired:
                self._set_failure(LEASE_HELD)
                return False
            if not self._valid_owned_lease(lease):
                # The returned identity is not provably ours, so releasing it
                # would be less safe than allowing the malformed lease to
                # expire at the storage boundary.
                self._set_failure(LEASE_ACQUIRE_FAILED)
                return False

            thread = threading.Thread(
                target=self._run,
                name="scientific-runtime-supervisor",
                daemon=False,
            )
            with self._state:
                self._lease = lease
                self._thread = thread
            try:
                thread.start()
            except Exception:
                with self._state:
                    self._thread = None
                try:
                    self._task_service.release_runtime_supervisor_lease(lease)
                except Exception as error:
                    self._set_failure(
                        LEASE_LOST
                        if self._is_lease_lost(error)
                        else LEASE_RELEASE_FAILED
                    )
                    return False
                with self._state:
                    self._lease = None
                self._set_failure(FATAL)
                return False

            if self._ready_event.wait(self._start_timeout_seconds):
                with self._state:
                    return self._healthy and thread.is_alive()

            self._stop_event.set()
            thread.join(self._join_timeout_seconds)
            if not thread.is_alive():
                try:
                    self._task_service.release_runtime_supervisor_lease(lease)
                except Exception as error:
                    self._set_failure(
                        LEASE_LOST
                        if self._is_lease_lost(error)
                        else LEASE_RELEASE_FAILED
                    )
                    return False
                with self._state:
                    self._lease = None
                    self._thread = None
            self._set_failure(START_TIMEOUT)
            return False

    def stop(self) -> bool:
        """Cooperatively stop, boundedly join, then release only our lease."""

        with self._lifecycle_lock:
            self._stop_event.set()
            with self._state:
                thread = self._thread
            if thread is not None and thread is not threading.current_thread():
                thread.join(self._join_timeout_seconds)
            if thread is not None and thread.is_alive():
                self._set_failure(STOP_TIMEOUT)
                return False

            with self._state:
                lease = self._lease
                self._thread = None
                self._healthy = False
            if lease is not None:
                try:
                    self._task_service.release_runtime_supervisor_lease(lease)
                except Exception as error:
                    self._set_failure(
                        LEASE_LOST
                        if self._is_lease_lost(error)
                        else LEASE_RELEASE_FAILED
                    )
                    return False
                with self._state:
                    self._lease = None
            with self._state:
                self._state.notify_all()
            return True

    def _run(self) -> None:
        try:
            # A same-owner acquire can be an exact replay of an existing term.
            # Heartbeat before declaring readiness so a nearly expired replay
            # cannot briefly advertise a healthy supervisor.
            lease = self._current_lease()
            lease, next_heartbeat = self._heartbeat_if_due(
                lease, self._monotonic()
            )
            if self._stop_event.is_set():
                return
            with self._state:
                self._healthy = True
                self._state.notify_all()
            self._ready_event.set()
            while not self._stop_event.is_set():
                lease = self._current_lease()
                lease, next_heartbeat = self._heartbeat_if_due(
                    lease, next_heartbeat
                )
                scanned, lease, next_heartbeat = self._scan_active_tasks(
                    lease, next_heartbeat
                )
                if self._stop_event.is_set():
                    break
                cycle, lease, next_heartbeat = self._observe_tasks(
                    scanned, lease, next_heartbeat
                )
                if self._stop_event.is_set():
                    break
                with self._state:
                    self._last_cycle = cycle
                    self._cycle_count += 1
                    self._state.notify_all()

                now = self._monotonic()
                wait_seconds = min(
                    self._poll_interval_seconds,
                    max(0.0, next_heartbeat - now),
                )
                self._stop_event.wait(wait_seconds)
        except _SupervisorFailure as error:
            self._set_failure(error.code)
        except TaskStoreError as error:
            self._set_failure(
                LEASE_LOST if self._is_lease_lost(error) else STORE_FAILED
            )
        except Exception as error:
            self._set_failure(LEASE_LOST if self._is_lease_lost(error) else FATAL)
        finally:
            self._ready_event.set()
            with self._state:
                self._healthy = False
                self._state.notify_all()

    def _scan_active_tasks(
        self, lease: Any, next_heartbeat: float
    ) -> tuple[list[Any], Any, float]:
        snapshots: list[Any] = []
        seen_task_ids: set[str] = set()
        seen_cursors: set[str] = set()
        cursor: str | None = None
        while True:
            if self._stop_event.is_set():
                return snapshots, lease, next_heartbeat
            lease, next_heartbeat = self._heartbeat_if_due(
                lease, next_heartbeat
            )
            remaining = self._max_tasks - len(snapshots)
            if remaining <= 0:
                raise _SupervisorFailure(TASK_LIMIT_EXCEEDED)
            limit = min(50, remaining)
            try:
                page = self._task_service.list_tasks(
                    project_id=self._project_id,
                    principal_id=self._principal_id,
                    cursor=cursor,
                    limit=limit,
                    view="active",
                )
            except Exception as error:
                if self._is_lease_lost(error):
                    raise _SupervisorFailure(LEASE_LOST) from error
                if isinstance(error, TaskStoreError):
                    raise
                raise _SupervisorFailure(SCAN_FAILED) from error
            page_snapshots = getattr(page, "snapshots", None)
            next_cursor = getattr(page, "next_cursor", None)
            if (
                not isinstance(page_snapshots, (list, tuple))
                or len(page_snapshots) > limit
                or (next_cursor is not None and not isinstance(next_cursor, str))
            ):
                raise _SupervisorFailure(SCAN_FAILED)
            if next_cursor is not None and not page_snapshots:
                raise _SupervisorFailure(SCAN_FAILED)
            for snapshot in page_snapshots:
                task_id = getattr(snapshot, "task_id", None)
                if (
                    not isinstance(task_id, str)
                    or task_id in seen_task_ids
                    or getattr(snapshot, "project_id", None) != self._project_id
                    or getattr(snapshot, "principal_id", None) != self._principal_id
                ):
                    raise _SupervisorFailure(SCAN_FAILED)
                seen_task_ids.add(task_id)
                snapshots.append(snapshot)
            if next_cursor is None:
                return snapshots, lease, next_heartbeat
            if len(snapshots) >= self._max_tasks:
                raise _SupervisorFailure(TASK_LIMIT_EXCEEDED)
            if next_cursor == cursor or next_cursor in seen_cursors:
                raise _SupervisorFailure(SCAN_FAILED)
            seen_cursors.add(next_cursor)
            cursor = next_cursor

    def _observe_tasks(
        self, snapshots: list[Any], lease: Any, next_heartbeat: float
    ) -> tuple[RuntimeSupervisorCycleResult, Any, float]:
        scanned: list[str] = []
        refreshed: list[str] = []
        deferred: list[tuple[str, str]] = []
        failures: list[tuple[str, str]] = []
        for snapshot in snapshots:
            if self._stop_event.is_set():
                break
            status = getattr(snapshot, "status", None)
            if status not in {"Queued", "Running"}:
                continue
            task_id = snapshot.task_id
            scanned.append(task_id)
            lease, next_heartbeat = self._heartbeat_if_due(
                lease, next_heartbeat
            )
            try:
                intent = self._task_service.get_dispatch_intent(
                    task_id,
                    project_id=self._project_id,
                    principal_id=self._principal_id,
                )
            except Exception as error:
                self._raise_if_fatal_task_error(error)
                failures.append(
                    (
                        task_id,
                        self._stable_error_code(
                            error, "DISPATCH_INTENT_READ_FAILED"
                        ),
                    )
                )
                continue
            if intent is None:
                deferred.append((task_id, "DISPATCH_INTENT_MISSING"))
                continue
            intent_state = getattr(intent, "state", None)
            if intent_state != "dispatched":
                code = {
                    "pending": "DISPATCH_PENDING",
                    "dispatching": "DISPATCHING",
                    "reconciliation_required": "RECONCILIATION_REQUIRED",
                }.get(intent_state, "DISPATCH_INTENT_UNSUPPORTED")
                deferred.append((task_id, code))
                continue
            if self._stop_event.is_set():
                break
            lease, next_heartbeat = self._heartbeat_if_due(
                lease, next_heartbeat
            )
            try:
                self._task_service.refresh_runtime_status(
                    task_id,
                    project_id=self._project_id,
                    principal_id=self._principal_id,
                    supervisor_lease=lease,
                )
            except Exception as error:
                self._raise_if_fatal_task_error(error)
                failures.append(
                    (task_id, self._stable_error_code(error, "STATUS_REFRESH_FAILED"))
                )
            else:
                refreshed.append(task_id)
        return (
            RuntimeSupervisorCycleResult(
                scanned_task_ids=tuple(scanned),
                refreshed_task_ids=tuple(refreshed),
                deferred=tuple(deferred),
                task_failures=tuple(failures),
            ),
            lease,
            next_heartbeat,
        )

    def _heartbeat_if_due(
        self, lease: Any, next_heartbeat: float
    ) -> tuple[Any, float]:
        if self._stop_event.is_set():
            return lease, next_heartbeat
        now = self._monotonic()
        if now < next_heartbeat:
            return lease, next_heartbeat
        try:
            updated = self._task_service.heartbeat_runtime_supervisor_lease(
                lease=lease, lease_seconds=self._lease_seconds
            )
        except Exception as error:
            if self._is_lease_lost(error):
                with self._state:
                    self._lease = None
                raise _SupervisorFailure(LEASE_LOST) from error
            raise _SupervisorFailure(STORE_FAILED) from error
        if not self._same_active_lease(lease, updated):
            raise _SupervisorFailure(LEASE_LOST)
        with self._state:
            self._lease = updated
        return updated, self._monotonic() + self._heartbeat_interval_seconds

    def _current_lease(self) -> Any:
        with self._state:
            lease = self._lease
        if lease is None:
            raise _SupervisorFailure(LEASE_LOST)
        return lease

    def _valid_owned_lease(self, lease: Any) -> bool:
        return (
            getattr(lease, "project_id", None) == self._project_id
            and getattr(lease, "principal_id", None) == self._principal_id
            and getattr(lease, "owner_id", None) == self._owner_id
            and type(getattr(lease, "fencing_token", None)) is int
            and lease.fencing_token >= 1
            and getattr(lease, "state", None) == "active"
            and isinstance(getattr(lease, "acquired_at", None), str)
            and bool(lease.acquired_at)
        )

    @staticmethod
    def _same_active_lease(previous: Any, updated: Any) -> bool:
        return (
            updated is not None
            and getattr(updated, "project_id", None)
            == getattr(previous, "project_id", None)
            and getattr(updated, "principal_id", None)
            == getattr(previous, "principal_id", None)
            and getattr(updated, "owner_id", None)
            == getattr(previous, "owner_id", None)
            and getattr(updated, "fencing_token", None)
            == getattr(previous, "fencing_token", None)
            and getattr(updated, "acquired_at", None)
            == getattr(previous, "acquired_at", None)
            and getattr(updated, "state", None) == "active"
        )

    def _raise_if_fatal_task_error(self, error: Exception) -> None:
        if self._is_lease_lost(error):
            with self._state:
                self._lease = None
            raise _SupervisorFailure(LEASE_LOST) from error
        if isinstance(error, TaskStoreError):
            raise error
        code = getattr(error, "code", None)
        if isinstance(code, str) and _STABLE_CODE.fullmatch(code) is not None:
            return
        if (
            error.__class__.__module__ == "scientific_runtime.task_service"
            and error.__class__.__name__
            in {"TaskConflict", "TaskNotFound", "TaskValidationError"}
        ):
            return
        raise _SupervisorFailure(FATAL) from error

    @staticmethod
    def _is_lease_lost(error: Exception) -> bool:
        return isinstance(error, RuntimeSupervisorLeaseLost) or getattr(
            error, "code", None
        ) == LEASE_LOST

    @staticmethod
    def _stable_error_code(error: Exception, fallback: str) -> str:
        code = getattr(error, "code", None)
        if isinstance(code, str) and _STABLE_CODE.fullmatch(code) is not None:
            return code
        return fallback

    def _set_failure(self, code: str) -> None:
        with self._state:
            if code == LEASE_LOST:
                self._lease = None
            self._failure_code = code
            self._healthy = False
            self._state.notify_all()
