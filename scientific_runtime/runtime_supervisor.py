"""Fenced runtime scheduler, Worker evidence projector, and status pump.

One scope-local Supervisor term authorizes current managed first dispatch.
The fixed Adapter's inherited kernel locks remain the external execution and
capacity authority; SQLite fencing protects claims, evidence, and outcomes.
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
_DAG_AGGREGATE_STATUSES = frozenset(
    {
        "AwaitingApproval",
        "Queued",
        "Running",
        "Waiting",
        "Succeeded",
        "Failed",
        "Cancelled",
    }
)

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

    def advance_runtime_dag(
        self,
        task_id: str,
        *,
        project_id: str,
        principal_id: str,
        supervisor_lease: Any,
    ) -> Any | None:
        """Optionally advance one internal multi-node DAG under this term."""

        ...

    def get_dispatch_intent(
        self, task_id: str, *, project_id: str, principal_id: str
    ) -> Any:
        ...

    def project_worker_attempt(
        self,
        task_id: str,
        *,
        project_id: str,
        principal_id: str,
        supervisor_lease: Any,
    ) -> Any:
        ...

    def schedule_runtime_dispatch(
        self,
        task_id: str,
        *,
        project_id: str,
        principal_id: str,
        supervisor_lease: Any,
    ) -> Any:
        ...

    def reconcile_runtime_dispatch(
        self,
        task_id: str,
        *,
        project_id: str,
        principal_id: str,
        supervisor_lease: Any,
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

    def process_runtime_cancellation(
        self,
        task_id: str,
        *,
        project_id: str,
        principal_id: str,
        supervisor_lease: Any,
    ) -> Any:
        ...

    def process_runtime_timeout(
        self,
        task_id: str,
        *,
        project_id: str,
        principal_id: str,
        supervisor_lease: Any,
    ) -> Any:
        ...

    def process_runtime_checkpoint(
        self,
        task_id: str,
        *,
        project_id: str,
        principal_id: str,
        supervisor_lease: Any,
    ) -> Any:
        """Optionally reconcile one durable checkpoint/wait/resume state."""

        ...

    def process_runtime_retry(
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
    projected_task_ids: tuple[str, ...] = ()
    adopted_task_ids: tuple[str, ...] = ()
    scheduled_task_ids: tuple[str, ...] = ()
    dispatched_task_ids: tuple[str, ...] = ()
    cancel_processed_task_ids: tuple[str, ...] = ()
    cancel_resolved_task_ids: tuple[str, ...] = ()
    timeout_armed_task_ids: tuple[str, ...] = ()
    timeout_processed_task_ids: tuple[str, ...] = ()
    timeout_resolved_task_ids: tuple[str, ...] = ()
    checkpoint_processed_task_ids: tuple[str, ...] = ()
    checkpoint_waiting_task_ids: tuple[str, ...] = ()
    checkpoint_resumed_task_ids: tuple[str, ...] = ()
    reconciled_task_ids: tuple[str, ...] = ()
    retry_processed_task_ids: tuple[str, ...] = ()
    retry_dispatched_task_ids: tuple[str, ...] = ()
    retry_exhausted_task_ids: tuple[str, ...] = ()
    dag_advanced_task_ids: tuple[str, ...] = ()
    dag_admitted_nodes: tuple[tuple[str, str], ...] = ()
    dag_cache_hit_nodes: tuple[tuple[str, str], ...] = ()
    dag_blocked_nodes: tuple[tuple[str, str], ...] = ()


class _SupervisorFailure(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class RuntimeSupervisor:
    """Schedule and observe runtime tasks under one fenced supervisor lease.

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
        worker_projection_interval_seconds: float = 60.0,
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
            (
                "worker_projection_interval_seconds",
                worker_projection_interval_seconds,
            ),
            ("start_timeout_seconds", start_timeout_seconds),
            ("join_timeout_seconds", join_timeout_seconds),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not 0 < value < float("inf")
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
        self._worker_projection_interval_seconds = float(
            worker_projection_interval_seconds
        )
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
        self._worker_projection_deadlines: dict[str, float] = {}

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
                self._worker_projection_deadlines = {}
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

    def _advance_task_dag(
        self,
        task_id: str,
        lease: Any,
        processor: Callable[..., Any],
    ) -> Any | None:
        """Run and strictly validate one optional internal DAG advance."""

        result = processor(
            task_id,
            project_id=self._project_id,
            principal_id=self._principal_id,
            supervisor_lease=lease,
        )
        if result is None:
            return None

        snapshot = getattr(result, "snapshot", None)
        snapshot_status = getattr(snapshot, "status", None)
        aggregate_status = getattr(result, "aggregate_status", None)
        active_intent = getattr(result, "active_intent", None)
        supplied_active_intents = getattr(result, "active_intents", None)
        active_intents = (
            (active_intent,) if active_intent is not None else ()
        ) if supplied_active_intents is None else supplied_active_intents
        admitted_node_id = getattr(result, "admitted_node_id", None)
        cache_hit_node_id = getattr(result, "cache_hit_node_id", None)
        cache_key_hash = getattr(result, "cache_key_hash", None)
        blocked_node_ids = getattr(result, "blocked_node_ids", None)
        deferred_code = getattr(result, "deferred_code", None)
        valid_admitted_node = admitted_node_id is None or (
            isinstance(admitted_node_id, str)
            and _OPAQUE_ID.fullmatch(admitted_node_id) is not None
        )
        valid_cache_hit_node = (
            cache_hit_node_id is None and cache_key_hash is None
        ) or (
            isinstance(cache_hit_node_id, str)
            and _OPAQUE_ID.fullmatch(cache_hit_node_id) is not None
            and isinstance(cache_key_hash, str)
            and re.fullmatch(r"sha256:[0-9a-f]{64}", cache_key_hash) is not None
        )
        valid_blocked_nodes = (
            isinstance(blocked_node_ids, tuple)
            and all(
                isinstance(node_id, str)
                and _OPAQUE_ID.fullmatch(node_id) is not None
                for node_id in blocked_node_ids
            )
            and len(set(blocked_node_ids)) == len(blocked_node_ids)
        )
        valid_active_intents = (
            isinstance(active_intents, tuple)
            and len(active_intents) <= 2
            and len(
                {
                    getattr(intent, "intent_id", None)
                    for intent in active_intents
                }
            )
            == len(active_intents)
            and all(
                getattr(intent, "task_id", None) == task_id
                for intent in active_intents
            )
            and (
                active_intent is None
                or any(intent is active_intent for intent in active_intents)
            )
        )
        if (
            not isinstance(task_id, str)
            or _OPAQUE_ID.fullmatch(task_id) is None
            or getattr(snapshot, "task_id", None) != task_id
            or getattr(snapshot, "project_id", None) != self._project_id
            or getattr(snapshot, "principal_id", None) != self._principal_id
            or snapshot_status not in _DAG_AGGREGATE_STATUSES
            or aggregate_status != snapshot_status
            or not valid_active_intents
            or (
                active_intent is not None
                and getattr(active_intent, "task_id", None) != task_id
            )
            or not valid_admitted_node
            or not valid_cache_hit_node
            or not valid_blocked_nodes
            or (
                admitted_node_id is not None and cache_hit_node_id is not None
            )
            or (
                cache_hit_node_id is not None
                and (active_intent is not None or cache_hit_node_id in blocked_node_ids)
            )
            or (
                admitted_node_id is not None
                and admitted_node_id in blocked_node_ids
            )
            or (
                deferred_code is not None
                and (
                    not isinstance(deferred_code, str)
                    or _STABLE_CODE.fullmatch(deferred_code) is None
                )
            )
        ):
            raise _SupervisorFailure(FATAL)
        return result

    def _process_task_timeout(self, task_id: str, lease: Any) -> Any:
        """Run and strictly validate one supervised timeout pass."""

        processor = getattr(self._task_service, "process_runtime_timeout", None)
        if not callable(processor):
            # Compatibility for bounded TaskService implementations that do
            # not advertise the session-level automatic-timeout feature.
            return None
        result = processor(
            task_id,
            project_id=self._project_id,
            principal_id=self._principal_id,
            supervisor_lease=lease,
        )
        state = getattr(result, "state", None)
        snapshot = getattr(result, "snapshot", None)
        deferred_code = getattr(result, "deferred_code", None)
        replayed = getattr(result, "replayed", None)
        if (
            getattr(snapshot, "task_id", None) != task_id
            or state
            not in {
                "none",
                "armed",
                "requested",
                "timed_out",
                "superseded",
                "not_triggered",
                "suppressed",
            }
            or type(replayed) is not bool
            or (
                deferred_code is not None
                and (
                    not isinstance(deferred_code, str)
                    or _STABLE_CODE.fullmatch(deferred_code) is None
                )
            )
            or (state == "none" and deferred_code is not None)
            or (
                state == "armed"
                and deferred_code not in {None, "TIMEOUT_NOT_DUE"}
            )
        ):
            raise _SupervisorFailure(FATAL)
        return result

    def _process_task_retry(self, task_id: str, lease: Any) -> Any:
        """Run and strictly validate one finite automatic-retry pass."""

        processor = getattr(self._task_service, "process_runtime_retry", None)
        if not callable(processor):
            return None
        result = processor(
            task_id,
            project_id=self._project_id,
            principal_id=self._principal_id,
            supervisor_lease=lease,
        )
        state = getattr(result, "state", None)
        snapshot = getattr(result, "snapshot", None)
        intent = getattr(result, "intent", None)
        deferred_code = getattr(result, "deferred_code", None)
        boolean_fields = (
            getattr(result, "authorized", None),
            getattr(result, "authorization_replayed", None),
            getattr(result, "dispatch_attempted", None),
            getattr(result, "projected", None),
            getattr(result, "adopted", None),
            getattr(result, "timeout_armed", None),
        )
        if (
            getattr(snapshot, "task_id", None) != task_id
            or getattr(intent, "task_id", None) != task_id
            or state not in {"none", "retrying", "dispatched", "exhausted"}
            or any(type(value) is not bool for value in boolean_fields)
            or (
                deferred_code is not None
                and (
                    not isinstance(deferred_code, str)
                    or _STABLE_CODE.fullmatch(deferred_code) is None
                )
            )
            or (state == "retrying" and getattr(snapshot, "status", None) != "Retrying")
            or (state == "retrying" and getattr(intent, "state", None) != "retrying")
            or (state == "dispatched" and getattr(intent, "state", None) != "dispatched")
            or (state == "exhausted" and getattr(snapshot, "status", None) != "Failed")
        ):
            raise _SupervisorFailure(FATAL)
        return result

    def _process_task_checkpoint(self, task_id: str, lease: Any) -> Any:
        """Run and strictly validate an optional checkpoint/resume pass."""

        processor = getattr(self._task_service, "process_runtime_checkpoint", None)
        if not callable(processor):
            return None
        result = processor(
            task_id,
            project_id=self._project_id,
            principal_id=self._principal_id,
            supervisor_lease=lease,
        )
        state = getattr(result, "state", None)
        snapshot = getattr(result, "snapshot", None)
        snapshot_status = getattr(snapshot, "status", None)
        replayed = getattr(result, "replayed", None)
        deferred_code = getattr(result, "deferred_code", None)
        if (
            getattr(snapshot, "task_id", None) != task_id
            or state
            not in {
                "none",
                "waiting",
                "resume_requested",
                "resumed",
                "action_required",
            }
            or snapshot_status not in {"Running", "Waiting"}
            or (
                state in {"waiting", "resume_requested"}
                and snapshot_status != "Waiting"
            )
            or (state == "resumed" and snapshot_status != "Running")
            or type(replayed) is not bool
            or (
                deferred_code is not None
                and (
                    not isinstance(deferred_code, str)
                    or _STABLE_CODE.fullmatch(deferred_code) is None
                )
            )
        ):
            raise _SupervisorFailure(FATAL)
        return result

    def _observe_tasks(
        self, snapshots: list[Any], lease: Any, next_heartbeat: float
    ) -> tuple[RuntimeSupervisorCycleResult, Any, float]:
        scanned: list[str] = []
        refreshed: list[str] = []
        projected: list[str] = []
        adopted: list[str] = []
        scheduled: list[str] = []
        dispatched: list[str] = []
        cancel_processed: list[str] = []
        cancel_resolved: list[str] = []
        timeout_armed: list[str] = []
        timeout_processed: list[str] = []
        timeout_resolved: list[str] = []
        checkpoint_processed: list[str] = []
        checkpoint_waiting: list[str] = []
        checkpoint_resumed: list[str] = []
        reconciled: list[str] = []
        retry_processed: list[str] = []
        retry_dispatched: list[str] = []
        retry_exhausted: list[str] = []
        dag_advanced: list[str] = []
        dag_admitted: list[tuple[str, str]] = []
        dag_cache_hits: list[tuple[str, str]] = []
        dag_blocked: list[tuple[str, str]] = []
        deferred: list[tuple[str, str]] = []
        failures: list[tuple[str, str]] = []
        reconciliation_probe_used = False
        dag_processor = getattr(
            self._task_service, "advance_runtime_dag", None
        )
        for snapshot in snapshots:
            if self._stop_event.is_set():
                break
            task_id = snapshot.task_id
            dag_active_intent = None
            dag_active_intents: tuple[Any, ...] = ()
            parallel_dag_active = False
            dag_intent_pre_dispatch = False
            heartbeat_checked = False
            if callable(dag_processor):
                lease, next_heartbeat = self._heartbeat_if_due(
                    lease, next_heartbeat
                )
                heartbeat_checked = True
                try:
                    dag_result = self._advance_task_dag(
                        task_id, lease, dag_processor
                    )
                except _SupervisorFailure:
                    raise
                except Exception as error:
                    self._raise_if_fatal_task_error(error)
                    failures.append(
                        (
                            task_id,
                            self._stable_error_code(
                                error, "DAG_ADVANCE_FAILED"
                            ),
                        )
                    )
                    continue
                if dag_result is not None:
                    snapshot = dag_result.snapshot
                    dag_active_intent = getattr(
                        dag_result, "active_intent", None
                    )
                    supplied_active_intents = getattr(
                        dag_result, "active_intents", None
                    )
                    dag_active_intents = (
                        (dag_active_intent,)
                        if dag_active_intent is not None
                        else ()
                    ) if supplied_active_intents is None else supplied_active_intents
                    parallel_dag_active = len(dag_active_intents) > 1
                    dag_intent_pre_dispatch = getattr(
                        dag_active_intent, "state", None
                    ) in {
                        "pending",
                        "dispatching",
                        "reconciliation_required",
                    }
                    dag_advanced.append(task_id)
                    admitted_node_id = getattr(
                        dag_result, "admitted_node_id", None
                    )
                    if admitted_node_id is not None:
                        dag_admitted.append((task_id, admitted_node_id))
                    cache_hit_node_id = getattr(
                        dag_result, "cache_hit_node_id", None
                    )
                    if cache_hit_node_id is not None:
                        dag_cache_hits.append((task_id, cache_hit_node_id))
                    dag_blocked.extend(
                        (task_id, node_id)
                        for node_id in dag_result.blocked_node_ids
                    )
                    dag_deferred_code = getattr(
                        dag_result, "deferred_code", None
                    )
                    if dag_deferred_code is not None:
                        deferred.append((task_id, dag_deferred_code))
                    if cache_hit_node_id is not None:
                        # A hit intentionally has no current dispatch intent.
                        # Let the next cycle claim the next deterministic node
                        # instead of falling through to historical P2 controls.
                        continue
            status = getattr(snapshot, "status", None)
            cancellation = getattr(snapshot, "cancellation", None)
            terminal_cancel_pending = (
                status in {"Succeeded", "Failed"}
                and getattr(cancellation, "state", None) == "requested"
            )
            if (
                status not in {"Queued", "Running", "Waiting", "Retrying"}
                and not terminal_cancel_pending
            ):
                continue
            scanned.append(task_id)
            if not heartbeat_checked:
                lease, next_heartbeat = self._heartbeat_if_due(
                    lease, next_heartbeat
                )
            if getattr(cancellation, "state", None) == "requested":
                try:
                    cancellation_result = (
                        self._task_service.process_runtime_cancellation(
                            task_id,
                            project_id=self._project_id,
                            principal_id=self._principal_id,
                            supervisor_lease=lease,
                        )
                    )
                except Exception as error:
                    self._raise_if_fatal_task_error(error)
                    failures.append(
                        (
                            task_id,
                            self._stable_error_code(
                                error, "CANCEL_PROCESS_FAILED"
                            ),
                        )
                    )
                    continue
                cancellation_state = getattr(
                    cancellation_result, "state", None
                )
                cancellation_snapshot = getattr(
                    cancellation_result, "snapshot", None
                )
                deferred_code = getattr(
                    cancellation_result, "deferred_code", None
                )
                if (
                    getattr(cancellation_snapshot, "task_id", None) != task_id
                    or cancellation_state
                    not in {"requested", "cancelled", "superseded"}
                    or (
                        deferred_code is not None
                        and (
                            not isinstance(deferred_code, str)
                            or _STABLE_CODE.fullmatch(deferred_code) is None
                        )
                    )
                ):
                    raise _SupervisorFailure(FATAL)
                cancel_processed.append(task_id)
                if cancellation_state == "requested":
                    deferred.append(
                        (task_id, deferred_code or "CANCEL_IN_PROGRESS")
                    )
                else:
                    cancel_resolved.append(task_id)
                # A durable request owns this task's terminal race.  Neither
                # first dispatch nor the ordinary status pump may run in the
                # same cycle after cancellation processing.
                continue
            timeout = getattr(snapshot, "timeout", None)
            if getattr(timeout, "state", None) == "requested":
                try:
                    timeout_result = self._process_task_timeout(task_id, lease)
                except Exception as error:
                    self._raise_if_fatal_task_error(error)
                    failures.append(
                        (
                            task_id,
                            self._stable_error_code(
                                error, "TIMEOUT_PROCESS_FAILED"
                            ),
                        )
                    )
                    continue
                timeout_state = getattr(timeout_result, "state", "none")
                timeout_code = getattr(timeout_result, "deferred_code", None)
                if timeout_state in {"none", "armed"}:
                    raise _SupervisorFailure(FATAL)
                timeout_processed.append(task_id)
                if timeout_state == "requested":
                    deferred.append(
                        (task_id, timeout_code or "TIMEOUT_IN_PROGRESS")
                    )
                else:
                    timeout_resolved.append(task_id)
                # Timeout authorization owns the exact Worker stop race.  The
                # ordinary status bridge must not publish generic failure in
                # the same cycle.
                continue
            timeout_checked_before_checkpoint = False
            checkpoint_supported = callable(
                getattr(
                    self._task_service,
                    "process_runtime_checkpoint",
                    None,
                )
            )
            task_wide_attempt_controls_deferred = (
                parallel_dag_active or dag_intent_pre_dispatch
            )
            if not task_wide_attempt_controls_deferred and status in {
                "Running",
                "Waiting",
            } and (
                checkpoint_supported or status == "Waiting"
            ):
                try:
                    timeout_result = self._process_task_timeout(task_id, lease)
                except _SupervisorFailure:
                    raise
                except Exception as error:
                    self._raise_if_fatal_task_error(error)
                    failures.append(
                        (
                            task_id,
                            self._stable_error_code(
                                error, "TIMEOUT_PROCESS_FAILED"
                            ),
                        )
                    )
                    continue
                timeout_checked_before_checkpoint = True
                timeout_state = getattr(timeout_result, "state", "none")
                timeout_code = getattr(timeout_result, "deferred_code", None)
                if timeout_state != "none":
                    timeout_processed.append(task_id)
                if timeout_state == "requested":
                    deferred.append(
                        (task_id, timeout_code or "TIMEOUT_IN_PROGRESS")
                    )
                    continue
                if timeout_state in {
                    "timed_out",
                    "superseded",
                    "not_triggered",
                    "suppressed",
                }:
                    timeout_resolved.append(task_id)
                    continue
            if not task_wide_attempt_controls_deferred and status in {
                "Running",
                "Waiting",
            }:
                try:
                    checkpoint_result = self._process_task_checkpoint(
                        task_id, lease
                    )
                except _SupervisorFailure:
                    raise
                except Exception as error:
                    self._raise_if_fatal_task_error(error)
                    failures.append(
                        (
                            task_id,
                            self._stable_error_code(
                                error, "CHECKPOINT_PROCESS_FAILED"
                            ),
                        )
                    )
                    continue
                if checkpoint_result is None:
                    if status == "Waiting":
                        deferred.append(
                            (task_id, "CHECKPOINT_RESUME_UNSUPPORTED")
                        )
                        continue
                else:
                    checkpoint_state = getattr(checkpoint_result, "state", None)
                    checkpoint_code = getattr(
                        checkpoint_result, "deferred_code", None
                    )
                    if checkpoint_state != "none":
                        checkpoint_processed.append(task_id)
                    if checkpoint_state in {"waiting", "resume_requested"}:
                        checkpoint_waiting.append(task_id)
                    elif checkpoint_state == "resumed":
                        checkpoint_resumed.append(task_id)
                    if checkpoint_state == "none" and status == "Running":
                        if checkpoint_code is None:
                            pass
                        else:
                            deferred.append((task_id, checkpoint_code))
                            continue
                    else:
                        if checkpoint_state in {
                            "none",
                            "waiting",
                            "resume_requested",
                            "action_required",
                        }:
                            default_code = {
                                "none": "CHECKPOINT_WAITING",
                                "waiting": "CHECKPOINT_WAITING",
                                "resume_requested": "CHECKPOINT_RESUME_IN_PROGRESS",
                                "action_required": "CHECKPOINT_ACTION_REQUIRED",
                            }[checkpoint_state]
                            deferred.append(
                                (task_id, checkpoint_code or default_code)
                            )
                        elif checkpoint_code is not None:
                            deferred.append((task_id, checkpoint_code))
                        # A Waiting input never reaches retry/status in this
                        # cycle, and any state-changing checkpoint result gets
                        # one clean cycle boundary before further observation.
                        continue
            if dag_active_intent is None:
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
            else:
                intent = dag_active_intent
            if intent is None:
                deferred.append((task_id, "DISPATCH_INTENT_MISSING"))
                continue
            intent_state = getattr(intent, "state", None)
            if intent_state == "retrying":
                try:
                    retry_result = self._process_task_retry(task_id, lease)
                except Exception as error:
                    self._raise_if_fatal_task_error(error)
                    failures.append(
                        (
                            task_id,
                            self._stable_error_code(
                                error, "WORKER_RETRY_PROCESS_FAILED"
                            ),
                        )
                    )
                    continue
                if retry_result is None:
                    deferred.append((task_id, "WORKER_RETRY_UNSUPPORTED"))
                    continue
                retry_processed.append(task_id)
                retry_state = getattr(retry_result, "state", None)
                retry_code = getattr(retry_result, "deferred_code", None)
                if getattr(retry_result, "timeout_armed", False):
                    timeout_armed.append(task_id)
                if retry_state == "retrying":
                    deferred.append(
                        (task_id, retry_code or "WORKER_EXIT_RETRY_IN_PROGRESS")
                    )
                    continue
                if retry_state == "exhausted":
                    retry_exhausted.append(task_id)
                    continue
                if retry_state != "dispatched":
                    raise _SupervisorFailure(FATAL)
                retry_dispatched.append(task_id)
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
                        (
                            task_id,
                            self._stable_error_code(
                                error, "STATUS_REFRESH_FAILED"
                            ),
                        )
                    )
                else:
                    refreshed.append(task_id)
                continue
            schedule_projected = False
            force_reconciliation_projection = False
            if intent_state == "reconciliation_required":
                if reconciliation_probe_used:
                    deferred.append((task_id, "RECONCILIATION_CYCLE_LIMIT"))
                    continue
                if not self._worker_projection_due(task_id):
                    deferred.append((task_id, "RECONCILIATION_PROBE_NOT_DUE"))
                    continue
                reconciliation_probe_used = True
                lease, next_heartbeat = self._heartbeat_if_due(
                    lease, next_heartbeat
                )
                try:
                    reconciliation = (
                        self._task_service.reconcile_runtime_dispatch(
                            task_id,
                            project_id=self._project_id,
                            principal_id=self._principal_id,
                            supervisor_lease=lease,
                        )
                    )
                except Exception as error:
                    self._raise_if_fatal_task_error(error)
                    # A positive resolution may have committed immediately
                    # before timeout arming or result delivery failed.  Do not
                    # let this probe consume the normal projection cadence;
                    # the next cycle must inspect a possibly managed receipt.
                    self._worker_projection_deadlines.pop(task_id, None)
                    failures.append(
                        (
                            task_id,
                            self._stable_error_code(
                                error, "RECONCILIATION_PROBE_FAILED"
                            ),
                        )
                    )
                    continue
                reconciled_intent = getattr(reconciliation, "intent", None)
                reconciled_state = getattr(reconciled_intent, "state", None)
                evidence_kind = getattr(
                    reconciliation, "evidence_kind", None
                )
                authorized_flag = getattr(
                    reconciliation, "authorized", None
                )
                authorization_replayed = getattr(
                    reconciliation, "authorization_replayed", None
                )
                probe_attempted = getattr(
                    reconciliation, "probe_attempted", None
                )
                reconciled_projected = getattr(
                    reconciliation, "projected", None
                )
                reconciled_adopted = getattr(
                    reconciliation, "adopted", None
                )
                reconciliation_timeout_armed = getattr(
                    reconciliation, "timeout_armed", False
                )
                deferred_code = getattr(
                    reconciliation, "deferred_code", None
                )
                if (
                    getattr(reconciled_intent, "task_id", None) != task_id
                    or reconciled_state
                    not in {
                        "reconciliation_required",
                        "dispatched",
                        "not_dispatched",
                    }
                    or evidence_kind
                    not in {
                        None,
                        "managed_worker_receipt",
                        "private_receipt",
                        "managed_pre_running_failure",
                    }
                    or type(authorized_flag) is not bool
                    or type(authorization_replayed) is not bool
                    or type(probe_attempted) is not bool
                    or type(reconciled_projected) is not bool
                    or type(reconciled_adopted) is not bool
                    or type(reconciliation_timeout_armed) is not bool
                    or (probe_attempted and not authorized_flag)
                    or (
                        reconciled_projected
                        and evidence_kind
                        not in {
                            "managed_worker_receipt",
                            "managed_pre_running_failure",
                        }
                    )
                    or (
                        reconciled_adopted
                        and reconciled_state != "dispatched"
                    )
                    or (
                        reconciled_state == "dispatched"
                        and deferred_code is not None
                    )
                    or (
                        reconciled_state == "not_dispatched"
                        and (
                            evidence_kind != "managed_pre_running_failure"
                            or reconciled_adopted
                            or reconciliation_timeout_armed
                            or deferred_code is not None
                        )
                    )
                    or (
                        deferred_code is not None
                        and (
                            not isinstance(deferred_code, str)
                            or _STABLE_CODE.fullmatch(deferred_code) is None
                        )
                    )
                ):
                    raise _SupervisorFailure(FATAL)
                if reconciled_state == "not_dispatched":
                    reconciled.append(task_id)
                    if reconciled_projected:
                        projected.append(task_id)
                    continue
                if reconciled_state != "dispatched":
                    deferred.append(
                        (
                            task_id,
                            deferred_code or "RECONCILIATION_ACTION_REQUIRED",
                        )
                    )
                    continue
                reconciled.append(task_id)
                dispatched.append(task_id)
                if reconciled_projected:
                    projected.append(task_id)
                if reconciled_adopted:
                    adopted.append(task_id)
                if reconciliation_timeout_armed:
                    timeout_armed.append(task_id)
                intent = reconciled_intent
                intent_state = "dispatched"
                schedule_projected = reconciled_projected
                force_reconciliation_projection = (
                    evidence_kind == "managed_worker_receipt"
                    and not reconciled_projected
                )
            elif intent_state in {"pending", "dispatching"}:
                lease, next_heartbeat = self._heartbeat_if_due(
                    lease, next_heartbeat
                )
                try:
                    schedule = self._task_service.schedule_runtime_dispatch(
                        task_id,
                        project_id=self._project_id,
                        principal_id=self._principal_id,
                        supervisor_lease=lease,
                    )
                except Exception as error:
                    self._raise_if_fatal_task_error(error)
                    failures.append(
                        (
                            task_id,
                            self._stable_error_code(error, "DISPATCH_SCHEDULE_FAILED"),
                        )
                    )
                    continue
                scheduled_intent = getattr(schedule, "intent", None)
                scheduled_state = getattr(scheduled_intent, "state", None)
                authorized_flag = getattr(schedule, "authorized", None)
                authorization_replayed = getattr(
                    schedule, "authorization_replayed", None
                )
                attempted_flag = getattr(schedule, "dispatch_attempted", None)
                schedule_projected = getattr(schedule, "projected", None)
                schedule_adopted = getattr(schedule, "adopted", None)
                schedule_timeout_armed = getattr(
                    schedule, "timeout_armed", False
                )
                deferred_code = getattr(schedule, "deferred_code", None)
                if (
                    getattr(scheduled_intent, "task_id", None) != task_id
                    or scheduled_state
                    not in {
                        "pending",
                        "dispatching",
                        "dispatched",
                        "reconciliation_required",
                    }
                    or type(authorized_flag) is not bool
                    or type(authorization_replayed) is not bool
                    or type(attempted_flag) is not bool
                    or type(schedule_projected) is not bool
                    or type(schedule_adopted) is not bool
                    or type(schedule_timeout_armed) is not bool
                    or attempted_flag != authorized_flag
                    or (authorization_replayed and not authorized_flag)
                    or (
                        schedule_adopted
                        and scheduled_state != "dispatched"
                    )
                    or (
                        deferred_code is not None
                        and (
                            not isinstance(deferred_code, str)
                            or _STABLE_CODE.fullmatch(deferred_code) is None
                        )
                    )
                ):
                    raise _SupervisorFailure(FATAL)
                if attempted_flag:
                    scheduled.append(task_id)
                if schedule_projected:
                    projected.append(task_id)
                if schedule_adopted:
                    adopted.append(task_id)
                if schedule_timeout_armed:
                    timeout_armed.append(task_id)
                if scheduled_state != "dispatched":
                    code = deferred_code or {
                        "pending": "DISPATCH_PENDING",
                        "dispatching": "DISPATCH_IN_PROGRESS",
                        "reconciliation_required": "RECONCILIATION_REQUIRED",
                    }.get(scheduled_state, "DISPATCH_INTENT_UNSUPPORTED")
                    deferred.append((task_id, code))
                    continue
                dispatched.append(task_id)
                intent = scheduled_intent
                intent_state = "dispatched"
                self._schedule_worker_projection(task_id)
            elif intent_state != "dispatched":
                code = {
                    "reconciliation_required": "RECONCILIATION_REQUIRED",
                }.get(intent_state, "DISPATCH_INTENT_UNSUPPORTED")
                deferred.append((task_id, code))
                continue
            if force_reconciliation_projection:
                self._worker_projection_deadlines.pop(task_id, None)
            projection_due = not schedule_projected and self._worker_projection_due(
                task_id
            )
            if projection_due:
                lease, next_heartbeat = self._heartbeat_if_due(
                    lease, next_heartbeat
                )
                try:
                    projection = self._task_service.project_worker_attempt(
                        task_id,
                        project_id=self._project_id,
                        principal_id=self._principal_id,
                        supervisor_lease=lease,
                    )
                except Exception as error:
                    self._raise_if_fatal_task_error(error)
                    failures.append(
                        (
                            task_id,
                            self._stable_error_code(
                                error, "WORKER_PROJECTION_FAILED"
                            ),
                        )
                    )
                    projection = None
            else:
                projection = None
            if projection is not None:
                projected_intent = getattr(projection, "intent", None)
                deferred_code = getattr(projection, "deferred_code", None)
                projected_flag = getattr(projection, "projected", None)
                adopted_flag = getattr(projection, "adopted", None)
                projection_timeout_armed = getattr(
                    projection, "timeout_armed", False
                )
                projected_state = getattr(projected_intent, "state", None)
                if (
                    getattr(projected_intent, "task_id", None) != task_id
                    or projected_state != "dispatched"
                    or type(projected_flag) is not bool
                    or type(adopted_flag) is not bool
                    or type(projection_timeout_armed) is not bool
                    or (
                        projected_flag
                        and not isinstance(
                            getattr(projection, "evidence", None), dict
                        )
                    )
                    or (
                        adopted_flag
                        and (not projected_flag or intent_state != "dispatching")
                    )
                    or (
                        deferred_code is not None
                        and (
                            not isinstance(deferred_code, str)
                            or _STABLE_CODE.fullmatch(deferred_code) is None
                        )
                    )
                ):
                    raise _SupervisorFailure(FATAL)
                if projected_flag:
                    projected.append(task_id)
                if adopted_flag:
                    adopted.append(task_id)
                if projection_timeout_armed:
                    timeout_armed.append(task_id)
                if projected_state == "dispatched":
                    intent = projected_intent
            if self._stop_event.is_set():
                break
            lease, next_heartbeat = self._heartbeat_if_due(
                lease, next_heartbeat
            )
            if (
                not timeout_checked_before_checkpoint
                and not task_wide_attempt_controls_deferred
            ):
                try:
                    timeout_result = self._process_task_timeout(task_id, lease)
                except Exception as error:
                    self._raise_if_fatal_task_error(error)
                    failures.append(
                        (
                            task_id,
                            self._stable_error_code(
                                error, "TIMEOUT_PROCESS_FAILED"
                            ),
                        )
                    )
                    continue
                timeout_state = getattr(timeout_result, "state", "none")
                timeout_code = getattr(timeout_result, "deferred_code", None)
                if timeout_state != "none":
                    timeout_processed.append(task_id)
                if timeout_state == "requested":
                    deferred.append(
                        (task_id, timeout_code or "TIMEOUT_IN_PROGRESS")
                    )
                    continue
                if timeout_state in {
                    "timed_out",
                    "superseded",
                    "not_triggered",
                    "suppressed",
                }:
                    timeout_resolved.append(task_id)
                    continue
            # Timeout/checkpoint decisions remain task-wide and therefore stay
            # deferred while the fixed Recipe has two live branches.  The DAG
            # retry hook is different: it scans the durable active DAG nodes
            # and consumes each exact attempt-1 ``worker_exit`` proof without
            # authorizing a retry.  It must run for dispatched parallel intents
            # or a failed branch and its descendants can never converge.
            if dag_intent_pre_dispatch:
                retry_result = None
            else:
                try:
                    retry_result = self._process_task_retry(task_id, lease)
                except Exception as error:
                    self._raise_if_fatal_task_error(error)
                    failures.append(
                        (
                            task_id,
                            self._stable_error_code(
                                error, "WORKER_RETRY_PROCESS_FAILED"
                            ),
                        )
                    )
                    continue
            if retry_result is not None:
                retry_state = getattr(retry_result, "state", None)
                retry_code = getattr(retry_result, "deferred_code", None)
                if retry_state != "none":
                    retry_processed.append(task_id)
                if getattr(retry_result, "timeout_armed", False):
                    timeout_armed.append(task_id)
                if retry_state == "retrying":
                    deferred.append(
                        (
                            task_id,
                            getattr(retry_result, "deferred_code", None)
                            or "WORKER_EXIT_RETRY_IN_PROGRESS",
                        )
                    )
                    continue
                if retry_state == "exhausted":
                    retry_exhausted.append(task_id)
                    continue
                if retry_state == "dispatched":
                    retry_dispatched.append(task_id)
                if retry_state == "none" and retry_code is not None:
                    deferred.append((task_id, retry_code))
                    continue
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
        active_task_ids = set(scanned)
        self._worker_projection_deadlines = {
            task_id: deadline
            for task_id, deadline in self._worker_projection_deadlines.items()
            if task_id in active_task_ids
        }
        return (
            RuntimeSupervisorCycleResult(
                scanned_task_ids=tuple(scanned),
                refreshed_task_ids=tuple(refreshed),
                deferred=tuple(deferred),
                task_failures=tuple(failures),
                projected_task_ids=tuple(projected),
                adopted_task_ids=tuple(adopted),
                scheduled_task_ids=tuple(scheduled),
                dispatched_task_ids=tuple(dispatched),
                cancel_processed_task_ids=tuple(cancel_processed),
                cancel_resolved_task_ids=tuple(cancel_resolved),
                timeout_armed_task_ids=tuple(dict.fromkeys(timeout_armed)),
                timeout_processed_task_ids=tuple(timeout_processed),
                timeout_resolved_task_ids=tuple(timeout_resolved),
                checkpoint_processed_task_ids=tuple(checkpoint_processed),
                checkpoint_waiting_task_ids=tuple(checkpoint_waiting),
                checkpoint_resumed_task_ids=tuple(checkpoint_resumed),
                reconciled_task_ids=tuple(reconciled),
                retry_processed_task_ids=tuple(retry_processed),
                retry_dispatched_task_ids=tuple(retry_dispatched),
                retry_exhausted_task_ids=tuple(retry_exhausted),
                dag_advanced_task_ids=tuple(dag_advanced),
                dag_admitted_nodes=tuple(dag_admitted),
                dag_cache_hit_nodes=tuple(dag_cache_hits),
                dag_blocked_nodes=tuple(dag_blocked),
            ),
            lease,
            next_heartbeat,
        )

    def _worker_projection_due(self, task_id: str) -> bool:
        """Rate-limit sampled Worker evidence without slowing status refresh."""

        now = self._monotonic()
        deadline = self._worker_projection_deadlines.get(task_id)
        if deadline is not None and now < deadline:
            return False
        self._worker_projection_deadlines[task_id] = (
            now + self._worker_projection_interval_seconds
        )
        return True

    def _schedule_worker_projection(self, task_id: str) -> None:
        self._worker_projection_deadlines[task_id] = (
            self._monotonic() + self._worker_projection_interval_seconds
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
