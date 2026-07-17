from __future__ import annotations

import threading
import tempfile
import unittest
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from scientific_runtime.runtime_supervisor import (
    FATAL,
    LEASE_HELD,
    LEASE_LOST,
    LEASE_RELEASE_FAILED,
    STOP_TIMEOUT,
    TASK_LIMIT_EXCEEDED,
    RuntimeSupervisor,
)
from scientific_runtime.task_service import TaskService
from scientific_runtime.task_store import (
    RuntimeSupervisorLeaseLost,
    SQLiteTaskStore,
)


PROJECT_ID = "project-1"
PRINCIPAL_ID = "user-1"
OWNER_ID = "workbench-1"


@dataclass(frozen=True)
class FakeLease:
    project_id: str
    principal_id: str
    fencing_token: int
    owner_id: str
    state: str = "active"
    acquired_at: str = "2026-07-16T00:00:00Z"
    heartbeat_at: str = "2026-07-16T00:00:00Z"
    expires_at: str = "2026-07-16T00:00:30Z"


@dataclass(frozen=True)
class FakeAcquisition:
    lease: FakeLease
    acquired: bool


@dataclass(frozen=True)
class FakeSnapshot:
    task_id: str
    project_id: str
    principal_id: str
    status: str
    cancellation: "FakeCancellation | None" = None
    timeout: "FakeTimeout | None" = None


@dataclass(frozen=True)
class FakeCancellation:
    state: str


@dataclass(frozen=True)
class FakeTimeout:
    state: str


@dataclass(frozen=True)
class FakeCancelProcess:
    snapshot: FakeSnapshot
    state: str
    adapter_result: dict | None
    replayed: bool
    deferred_code: str | None = None


@dataclass(frozen=True)
class FakeTimeoutProcess:
    snapshot: FakeSnapshot
    state: str
    adapter_result: dict | None
    replayed: bool
    deferred_code: str | None = None


@dataclass(frozen=True)
class FakeCheckpointProcess:
    snapshot: FakeSnapshot
    state: str
    replayed: bool
    deferred_code: str | None = None


@dataclass(frozen=True)
class FakeRetryProcess:
    snapshot: FakeSnapshot
    intent: "FakeIntent"
    state: str
    authorized: bool
    authorization_replayed: bool
    dispatch_attempted: bool
    projected: bool
    adopted: bool
    deferred_code: str | None = None
    timeout_armed: bool = False


@dataclass(frozen=True)
class FakeIntent:
    task_id: str
    state: str


@dataclass(frozen=True)
class FakeProjection:
    intent: FakeIntent
    evidence: dict | None
    projected: bool
    adopted: bool
    replayed: bool
    deferred_code: str | None = None
    timeout_armed: bool = False


@dataclass(frozen=True)
class FakeSchedule:
    intent: FakeIntent
    authorized: bool
    authorization_replayed: bool
    dispatch_attempted: bool
    projected: bool
    adopted: bool
    deferred_code: str | None = None
    timeout_armed: bool = False


@dataclass(frozen=True)
class FakeReconciliation:
    intent: FakeIntent
    evidence_kind: str | None
    authorized: bool
    authorization_replayed: bool
    probe_attempted: bool
    projected: bool
    adopted: bool
    deferred_code: str | None = None
    timeout_armed: bool = False


@dataclass(frozen=True)
class FakePage:
    snapshots: tuple[FakeSnapshot, ...]
    next_cursor: str | None


class FakeStatusError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class StepClock:
    def __init__(self) -> None:
        self.value = 0.0
        self.lock = threading.Lock()

    def __call__(self) -> float:
        with self.lock:
            self.value += 1.0
            return self.value


class ManualClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


class FakeTaskService:
    def __init__(
        self,
        snapshots: list[FakeSnapshot] | None = None,
        intents: dict[str, FakeIntent | Exception | None] | None = None,
    ) -> None:
        self.snapshots = list(snapshots or [])
        self.intents = dict(intents or {})
        self.calls: list[str] = []
        self.acquire_calls = 0
        self.heartbeat_calls = 0
        self.release_calls = 0
        self.list_calls = 0
        self.intent_calls: list[str] = []
        self.projection_calls: list[str] = []
        self.schedule_calls: list[str] = []
        self.reconciliation_calls: list[str] = []
        self.refresh_calls: list[str] = []
        self.cancel_calls: list[str] = []
        self.timeout_calls: list[str] = []
        self.checkpoint_calls: list[str] = []
        self.retry_calls: list[str] = []
        self.dispatch_calls = 0
        self.lease_held = False
        self.lose_on_heartbeat = False
        self.list_error: Exception | None = None
        self.refresh_failures: dict[str, Exception] = {}
        self.projection_failures: dict[str, Exception] = {}
        self.projection_results: dict[str, FakeProjection] = {}
        self.schedule_failures: dict[str, Exception] = {}
        self.schedule_results: dict[str, FakeSchedule] = {}
        self.reconciliation_failures: dict[str, Exception] = {}
        self.reconciliation_results: dict[str, FakeReconciliation] = {}
        self.cancel_failures: dict[str, Exception] = {}
        self.cancel_results: dict[str, FakeCancelProcess] = {}
        self.timeout_failures: dict[str, Exception] = {}
        self.timeout_results: dict[str, FakeTimeoutProcess] = {}
        self.checkpoint_failures: dict[str, Exception] = {}
        self.checkpoint_results: dict[str, FakeCheckpointProcess] = {}
        self.retry_failures: dict[str, Exception] = {}
        self.retry_results: dict[str, FakeRetryProcess] = {}
        self.refresh_hook: Callable[[str], None] | None = None
        self.release_failures_remaining = 0
        self.active_lease: FakeLease | None = None
        self.lock = threading.Lock()

    def acquire_runtime_supervisor_lease(
        self,
        *,
        project_id: str,
        principal_id: str,
        owner_id: str,
        lease_seconds: int,
    ) -> FakeAcquisition:
        del lease_seconds
        with self.lock:
            self.calls.append("acquire")
            self.acquire_calls += 1
            if self.lease_held:
                return FakeAcquisition(
                    FakeLease(project_id, principal_id, 7, "other-owner"),
                    acquired=False,
                )
            lease = FakeLease(project_id, principal_id, 8, owner_id)
            self.active_lease = lease
            return FakeAcquisition(lease, acquired=True)

    def heartbeat_runtime_supervisor_lease(
        self, lease: FakeLease, *, lease_seconds: int
    ) -> FakeLease:
        del lease_seconds
        with self.lock:
            self.calls.append("heartbeat")
            self.heartbeat_calls += 1
            if self.lose_on_heartbeat or lease != self.active_lease:
                self.active_lease = None
                raise RuntimeSupervisorLeaseLost("simulated fenced lease loss")
            assert self.active_lease is not None
            updated = replace(
                self.active_lease,
                heartbeat_at=f"heartbeat-{self.heartbeat_calls}",
                expires_at=f"expiry-{self.heartbeat_calls}",
            )
            self.active_lease = updated
            return updated

    def release_runtime_supervisor_lease(self, lease: FakeLease) -> FakeLease:
        with self.lock:
            self.calls.append("release")
            self.release_calls += 1
            if self.release_failures_remaining:
                self.release_failures_remaining -= 1
                raise RuntimeError("simulated transient release failure")
            if lease != self.active_lease:
                raise RuntimeSupervisorLeaseLost("simulated stale release")
            self.active_lease = None
            return replace(lease, state="released")

    def list_tasks(
        self,
        *,
        project_id: str,
        principal_id: str,
        cursor: str | None = None,
        limit: int = 20,
        view: str = "active",
    ) -> FakePage:
        with self.lock:
            self.calls.append("list")
            self.list_calls += 1
        if self.list_error is not None:
            raise self.list_error
        assert view == "active"
        assert project_id == PROJECT_ID
        assert principal_id == PRINCIPAL_ID
        start = 0
        if cursor is not None:
            start = next(
                index + 1
                for index, snapshot in enumerate(self.snapshots)
                if snapshot.task_id == cursor
            )
        values = self.snapshots[start : start + limit]
        has_more = start + len(values) < len(self.snapshots)
        next_cursor = values[-1].task_id if values and has_more else None
        return FakePage(tuple(values), next_cursor)

    def get_dispatch_intent(
        self, task_id: str, *, project_id: str, principal_id: str
    ) -> FakeIntent | None:
        assert project_id == PROJECT_ID
        assert principal_id == PRINCIPAL_ID
        with self.lock:
            self.calls.append("intent")
            self.intent_calls.append(task_id)
        value = self.intents.get(task_id)
        if isinstance(value, Exception):
            raise value
        return value

    def project_worker_attempt(
        self,
        task_id: str,
        *,
        project_id: str,
        principal_id: str,
        supervisor_lease: FakeLease,
    ) -> FakeProjection:
        assert project_id == PROJECT_ID
        assert principal_id == PRINCIPAL_ID
        with self.lock:
            if supervisor_lease != self.active_lease:
                raise RuntimeSupervisorLeaseLost("simulated stale projection write")
            self.calls.append("project")
            self.projection_calls.append(task_id)
        failure = self.projection_failures.get(task_id)
        if failure is not None:
            raise failure
        configured = self.projection_results.get(task_id)
        if configured is not None:
            return configured
        intent = self.intents.get(task_id)
        assert isinstance(intent, FakeIntent)
        return FakeProjection(
            intent=intent,
            evidence=None,
            projected=False,
            adopted=False,
            replayed=False,
            deferred_code=(
                "WORKER_EVIDENCE_NOT_READY"
                if intent.state == "dispatching"
                else "WORKER_EVIDENCE_UNAVAILABLE"
            ),
        )

    def schedule_runtime_dispatch(
        self,
        task_id: str,
        *,
        project_id: str,
        principal_id: str,
        supervisor_lease: FakeLease,
    ) -> FakeSchedule:
        assert project_id == PROJECT_ID
        assert principal_id == PRINCIPAL_ID
        with self.lock:
            if supervisor_lease != self.active_lease:
                raise RuntimeSupervisorLeaseLost("simulated stale schedule write")
            self.calls.append("schedule")
            self.schedule_calls.append(task_id)
        failure = self.schedule_failures.get(task_id)
        if failure is not None:
            raise failure
        configured = self.schedule_results.get(task_id)
        if configured is not None:
            self.intents[task_id] = configured.intent
            return configured
        intent = self.intents.get(task_id)
        assert isinstance(intent, FakeIntent)
        if intent.state == "pending":
            dispatched = FakeIntent(task_id, "dispatched")
            self.intents[task_id] = dispatched
            return FakeSchedule(
                intent=dispatched,
                authorized=True,
                authorization_replayed=False,
                dispatch_attempted=True,
                projected=True,
                adopted=True,
            )
        projection = self.project_worker_attempt(
            task_id,
            project_id=project_id,
            principal_id=principal_id,
            supervisor_lease=supervisor_lease,
        )
        self.intents[task_id] = projection.intent
        deferred_code = projection.deferred_code
        if deferred_code is None and projection.intent.state == "dispatching":
            ticket = (
                projection.evidence.get("ticket")
                if isinstance(projection.evidence, dict)
                else None
            )
            state = ticket.get("state") if isinstance(ticket, dict) else None
            deferred_code = {
                "staged": "WORKER_ATTEMPT_STAGED",
                "failed": "WORKER_ATTEMPT_FAILED",
            }.get(state, "WORKER_ATTEMPT_STARTING")
        return FakeSchedule(
            intent=projection.intent,
            authorized=False,
            authorization_replayed=False,
            dispatch_attempted=False,
            projected=projection.projected,
            adopted=projection.adopted,
            deferred_code=deferred_code,
        )

    def reconcile_runtime_dispatch(
        self,
        task_id: str,
        *,
        project_id: str,
        principal_id: str,
        supervisor_lease: FakeLease,
    ) -> FakeReconciliation:
        assert project_id == PROJECT_ID
        assert principal_id == PRINCIPAL_ID
        with self.lock:
            if supervisor_lease != self.active_lease:
                raise RuntimeSupervisorLeaseLost(
                    "simulated stale reconciliation write"
                )
            self.calls.append("reconcile")
            self.reconciliation_calls.append(task_id)
        failure = self.reconciliation_failures.get(task_id)
        if failure is not None:
            raise failure
        configured = self.reconciliation_results.get(task_id)
        if configured is not None:
            self.intents[task_id] = configured.intent
            return configured
        intent = self.intents.get(task_id)
        assert isinstance(intent, FakeIntent)
        return FakeReconciliation(
            intent=intent,
            evidence_kind=None,
            authorized=True,
            authorization_replayed=False,
            probe_attempted=True,
            projected=False,
            adopted=False,
            deferred_code="RECONCILIATION_REQUIRED",
        )

    def refresh_runtime_status(
        self,
        task_id: str,
        *,
        project_id: str,
        principal_id: str,
        supervisor_lease: FakeLease,
    ) -> object:
        assert project_id == PROJECT_ID
        assert principal_id == PRINCIPAL_ID
        with self.lock:
            if supervisor_lease != self.active_lease:
                raise RuntimeSupervisorLeaseLost("simulated stale status write")
            self.calls.append("refresh")
            self.refresh_calls.append(task_id)
        if self.refresh_hook is not None:
            self.refresh_hook(task_id)
        failure = self.refresh_failures.get(task_id)
        if failure is not None:
            raise failure
        return object()

    def process_runtime_cancellation(
        self,
        task_id: str,
        *,
        project_id: str,
        principal_id: str,
        supervisor_lease: FakeLease,
    ) -> FakeCancelProcess:
        assert project_id == PROJECT_ID
        assert principal_id == PRINCIPAL_ID
        with self.lock:
            if supervisor_lease != self.active_lease:
                raise RuntimeSupervisorLeaseLost("simulated stale cancel write")
            self.calls.append("cancel")
            self.cancel_calls.append(task_id)
        failure = self.cancel_failures.get(task_id)
        if failure is not None:
            raise failure
        configured = self.cancel_results.get(task_id)
        if configured is not None:
            return configured
        current = next(value for value in self.snapshots if value.task_id == task_id)
        resolved = replace(
            current,
            status="Cancelled",
            cancellation=FakeCancellation("cancelled"),
        )
        return FakeCancelProcess(
            snapshot=resolved,
            state="cancelled",
            adapter_result={"state": "cancelled"},
            replayed=False,
        )

    def process_runtime_timeout(
        self,
        task_id: str,
        *,
        project_id: str,
        principal_id: str,
        supervisor_lease: FakeLease,
    ) -> FakeTimeoutProcess:
        assert project_id == PROJECT_ID
        assert principal_id == PRINCIPAL_ID
        with self.lock:
            if supervisor_lease != self.active_lease:
                raise RuntimeSupervisorLeaseLost("simulated stale timeout write")
            self.calls.append("timeout")
            self.timeout_calls.append(task_id)
        failure = self.timeout_failures.get(task_id)
        if failure is not None:
            raise failure
        configured = self.timeout_results.get(task_id)
        if configured is not None:
            return configured
        current = next(value for value in self.snapshots if value.task_id == task_id)
        state = "none" if current.timeout is None else current.timeout.state
        return FakeTimeoutProcess(
            snapshot=current,
            state=state,
            adapter_result=None,
            replayed=True,
            deferred_code="TIMEOUT_NOT_DUE" if state == "armed" else None,
        )

    def process_runtime_retry(
        self,
        task_id: str,
        *,
        project_id: str,
        principal_id: str,
        supervisor_lease: FakeLease,
    ) -> FakeRetryProcess:
        assert project_id == PROJECT_ID
        assert principal_id == PRINCIPAL_ID
        with self.lock:
            if supervisor_lease != self.active_lease:
                raise RuntimeSupervisorLeaseLost("simulated stale retry write")
            self.calls.append("retry")
            self.retry_calls.append(task_id)
        failure = self.retry_failures.get(task_id)
        if failure is not None:
            raise failure
        configured = self.retry_results.get(task_id)
        if configured is not None:
            self.intents[task_id] = configured.intent
            return configured
        current = next(value for value in self.snapshots if value.task_id == task_id)
        intent = self.intents.get(task_id)
        assert isinstance(intent, FakeIntent)
        return FakeRetryProcess(
            snapshot=current,
            intent=intent,
            state="none",
            authorized=False,
            authorization_replayed=False,
            dispatch_attempted=False,
            projected=False,
            adopted=False,
        )

    def process_runtime_checkpoint(
        self,
        task_id: str,
        *,
        project_id: str,
        principal_id: str,
        supervisor_lease: FakeLease,
    ) -> FakeCheckpointProcess:
        assert project_id == PROJECT_ID
        assert principal_id == PRINCIPAL_ID
        with self.lock:
            if supervisor_lease != self.active_lease:
                raise RuntimeSupervisorLeaseLost(
                    "simulated stale checkpoint write"
                )
            self.calls.append("checkpoint")
            self.checkpoint_calls.append(task_id)
        failure = self.checkpoint_failures.get(task_id)
        if failure is not None:
            raise failure
        configured = self.checkpoint_results.get(task_id)
        if configured is not None:
            return configured
        current = next(value for value in self.snapshots if value.task_id == task_id)
        return FakeCheckpointProcess(
            snapshot=current,
            state="none",
            replayed=True,
        )

    def dispatch(self) -> None:
        self.dispatch_calls += 1
        raise AssertionError("the observation-only supervisor must not dispatch")


def snapshot(
    task_id: str,
    status: str,
    cancellation_state: str | None = None,
    timeout_state: str | None = None,
) -> FakeSnapshot:
    cancellation = (
        None if cancellation_state is None else FakeCancellation(cancellation_state)
    )
    timeout = None if timeout_state is None else FakeTimeout(timeout_state)
    return FakeSnapshot(
        task_id, PROJECT_ID, PRINCIPAL_ID, status, cancellation, timeout
    )


def supervisor(
    service: FakeTaskService,
    **overrides: object,
) -> RuntimeSupervisor:
    arguments: dict[str, object] = {
        "project_id": PROJECT_ID,
        "principal_id": PRINCIPAL_ID,
        "owner_id": OWNER_ID,
        "lease_seconds": 30,
        "heartbeat_interval_seconds": 10,
        "poll_interval_seconds": 10,
        "start_timeout_seconds": 1,
        "join_timeout_seconds": 1,
    }
    arguments.update(overrides)
    return RuntimeSupervisor(service, **arguments)  # type: ignore[arg-type]


class RuntimeSupervisorTests(unittest.TestCase):
    def test_real_sqlite_empty_cycle_acquires_heartbeats_and_releases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteTaskStore(Path(directory) / "runtime.sqlite3")
            runtime = RuntimeSupervisor(
                TaskService(store),
                project_id=PROJECT_ID,
                principal_id=PRINCIPAL_ID,
                owner_id="workbench-real-sqlite",
                lease_seconds=2,
                heartbeat_interval_seconds=0.25,
                poll_interval_seconds=0.01,
                start_timeout_seconds=1,
                join_timeout_seconds=1,
            )

            self.assertTrue(runtime.start())
            self.assertTrue(runtime.wait_for_cycle(timeout=1))
            active = store.get_runtime_supervisor_lease(
                project_id=PROJECT_ID,
                principal_id=PRINCIPAL_ID,
            )
            self.assertIsNotNone(active)
            self.assertEqual(active.state, "active")
            self.assertEqual(active.owner_id, "workbench-real-sqlite")
            self.assertTrue(runtime.stop())

            released = store.get_runtime_supervisor_lease(
                project_id=PROJECT_ID,
                principal_id=PRINCIPAL_ID,
            )
            self.assertIsNotNone(released)
            self.assertEqual(released.state, "released")
            self.assertEqual(released.fencing_token, active.fencing_token)

    def test_constructor_is_side_effect_free_and_held_lease_starts_no_thread(
        self,
    ) -> None:
        service = FakeTaskService()
        runtime = supervisor(service)

        self.assertEqual(service.calls, [])
        self.assertIsNone(runtime.thread)
        self.assertFalse(runtime.running)
        service.lease_held = True

        self.assertFalse(runtime.start())
        self.assertEqual(runtime.failure_code, LEASE_HELD)
        self.assertFalse(runtime.healthy)
        self.assertIsNone(runtime.thread)
        self.assertEqual(service.acquire_calls, 1)
        self.assertEqual(service.release_calls, 0)
        self.assertTrue(runtime.stop())
        self.assertEqual(service.release_calls, 0)

    def test_start_waits_ready_and_stop_is_idempotent_without_thread_leak(
        self,
    ) -> None:
        service = FakeTaskService()
        runtime = supervisor(service)

        self.assertTrue(runtime.start())
        worker = runtime.thread
        self.assertIsNotNone(worker)
        assert worker is not None
        self.assertTrue(worker.is_alive())
        self.assertFalse(worker.daemon)
        self.assertTrue(runtime.healthy)
        self.assertGreaterEqual(service.heartbeat_calls, 1)
        self.assertEqual(service.calls[:2], ["acquire", "heartbeat"])
        self.assertTrue(runtime.wait_for_cycle(timeout=1))

        self.assertTrue(runtime.stop())
        self.assertFalse(worker.is_alive())
        self.assertFalse(runtime.running)
        self.assertFalse(runtime.healthy)
        self.assertIsNone(runtime.failure_code)
        self.assertEqual(service.release_calls, 1)
        self.assertIsNone(service.active_lease)
        self.assertTrue(runtime.stop())
        self.assertEqual(service.release_calls, 1)

    def test_pending_cancellation_preempts_dispatch_and_status_in_same_cycle(
        self,
    ) -> None:
        task_id = "cancel-first"
        service = FakeTaskService(
            [snapshot(task_id, "Running", "requested", "requested")],
            {task_id: FakeIntent(task_id, "dispatched")},
        )
        runtime = supervisor(service)
        try:
            self.assertTrue(runtime.start())
            self.assertTrue(runtime.wait_for_cycle(timeout=1))
            cycle = runtime.last_cycle
            self.assertIsNotNone(cycle)
            assert cycle is not None
            self.assertEqual(cycle.scanned_task_ids, (task_id,))
            self.assertEqual(cycle.cancel_processed_task_ids, (task_id,))
            self.assertEqual(cycle.cancel_resolved_task_ids, (task_id,))
            self.assertEqual(cycle.refreshed_task_ids, ())
            self.assertEqual(cycle.projected_task_ids, ())
            self.assertEqual(cycle.scheduled_task_ids, ())
            self.assertEqual(cycle.dispatched_task_ids, ())
            self.assertEqual(cycle.deferred, ())
            self.assertEqual(cycle.task_failures, ())
            self.assertEqual(service.cancel_calls, [task_id])
            self.assertEqual(service.timeout_calls, [])
            self.assertEqual(service.checkpoint_calls, [])
            self.assertEqual(service.retry_calls, [])
            self.assertEqual(service.intent_calls, [])
            self.assertEqual(service.projection_calls, [])
            self.assertEqual(service.schedule_calls, [])
            self.assertEqual(service.refresh_calls, [])
            self.assertNotIn("intent", service.calls)
            self.assertNotIn("schedule", service.calls)
            self.assertNotIn("refresh", service.calls)
        finally:
            runtime.stop()

    def test_requested_timeout_preempts_dispatch_and_status_in_same_cycle(
        self,
    ) -> None:
        task_id = "timeout-first"
        current = snapshot(task_id, "Running", timeout_state="requested")
        service = FakeTaskService(
            [current],
            {task_id: FakeIntent(task_id, "dispatched")},
        )
        service.timeout_results[task_id] = FakeTimeoutProcess(
            snapshot=current,
            state="requested",
            adapter_result={"state": "pending"},
            replayed=True,
            deferred_code="TIMEOUT_EXIT_UNPROVEN",
        )
        runtime = supervisor(service)
        try:
            self.assertTrue(runtime.start())
            self.assertTrue(runtime.wait_for_cycle(timeout=1))
            cycle = runtime.last_cycle
            self.assertIsNotNone(cycle)
            assert cycle is not None
            self.assertEqual(cycle.timeout_processed_task_ids, (task_id,))
            self.assertEqual(cycle.timeout_resolved_task_ids, ())
            self.assertEqual(
                cycle.deferred, ((task_id, "TIMEOUT_EXIT_UNPROVEN"),)
            )
            self.assertEqual(cycle.refreshed_task_ids, ())
            self.assertEqual(service.timeout_calls, [task_id])
            self.assertEqual(service.cancel_calls, [])
            self.assertEqual(service.checkpoint_calls, [])
            self.assertEqual(service.retry_calls, [])
            self.assertEqual(service.intent_calls, [])
            self.assertEqual(service.projection_calls, [])
            self.assertEqual(service.schedule_calls, [])
            self.assertEqual(service.refresh_calls, [])
        finally:
            runtime.stop()

    def test_running_checkpoint_enters_waiting_before_retry_or_status(self) -> None:
        task_id = "checkpoint-running-to-waiting"
        current = snapshot(task_id, "Running", timeout_state="armed")
        service = FakeTaskService(
            [current],
            {task_id: FakeIntent(task_id, "dispatched")},
        )
        service.timeout_results[task_id] = FakeTimeoutProcess(
            snapshot=current,
            state="armed",
            adapter_result=None,
            replayed=True,
            deferred_code="TIMEOUT_NOT_DUE",
        )
        service.checkpoint_results[task_id] = FakeCheckpointProcess(
            snapshot=snapshot(task_id, "Waiting", timeout_state="armed"),
            state="waiting",
            replayed=False,
        )
        runtime = supervisor(service)
        lease = FakeLease(PROJECT_ID, PRINCIPAL_ID, 8, OWNER_ID)
        service.active_lease = lease

        cycle, _, _ = runtime._observe_tasks([current], lease, float("inf"))

        self.assertEqual(cycle.scanned_task_ids, (task_id,))
        self.assertEqual(cycle.timeout_processed_task_ids, (task_id,))
        self.assertEqual(cycle.checkpoint_processed_task_ids, (task_id,))
        self.assertEqual(cycle.checkpoint_waiting_task_ids, (task_id,))
        self.assertEqual(cycle.checkpoint_resumed_task_ids, ())
        self.assertEqual(cycle.deferred, ((task_id, "CHECKPOINT_WAITING"),))
        self.assertEqual(cycle.refreshed_task_ids, ())
        self.assertEqual(service.timeout_calls, [task_id])
        self.assertEqual(service.checkpoint_calls, [task_id])
        self.assertEqual(service.intent_calls, [])
        self.assertEqual(service.retry_calls, [])
        self.assertEqual(service.refresh_calls, [])
        self.assertLess(
            service.calls.index("timeout"), service.calls.index("checkpoint")
        )

    def test_waiting_resume_skips_retry_and_status_for_one_cycle(self) -> None:
        task_id = "checkpoint-waiting-resumed"
        current = snapshot(task_id, "Waiting")
        service = FakeTaskService(
            [current],
            {task_id: FakeIntent(task_id, "retrying")},
        )
        service.checkpoint_results[task_id] = FakeCheckpointProcess(
            snapshot=snapshot(task_id, "Running"),
            state="resumed",
            replayed=True,
        )
        runtime = supervisor(service)
        lease = FakeLease(PROJECT_ID, PRINCIPAL_ID, 8, OWNER_ID)
        service.active_lease = lease

        cycle, _, _ = runtime._observe_tasks([current], lease, float("inf"))

        self.assertEqual(cycle.scanned_task_ids, (task_id,))
        self.assertEqual(cycle.checkpoint_processed_task_ids, (task_id,))
        self.assertEqual(cycle.checkpoint_waiting_task_ids, ())
        self.assertEqual(cycle.checkpoint_resumed_task_ids, (task_id,))
        self.assertEqual(cycle.deferred, ())
        self.assertEqual(service.timeout_calls, [task_id])
        self.assertEqual(service.checkpoint_calls, [task_id])
        self.assertEqual(service.intent_calls, [])
        self.assertEqual(service.retry_calls, [])
        self.assertEqual(service.refresh_calls, [])
        self.assertLess(
            service.calls.index("timeout"), service.calls.index("checkpoint")
        )

    def test_waiting_cancel_and_timeout_preempt_checkpoint_resume(self) -> None:
        cases = (
            ("cancel", snapshot("waiting-cancel", "Waiting", "requested")),
            (
                "timeout",
                snapshot("waiting-timeout", "Waiting", timeout_state="requested"),
            ),
        )
        for control, current in cases:
            with self.subTest(control=control):
                service = FakeTaskService(
                    [current],
                    {current.task_id: FakeIntent(current.task_id, "dispatched")},
                )
                if control == "timeout":
                    service.timeout_results[current.task_id] = FakeTimeoutProcess(
                        snapshot=current,
                        state="requested",
                        adapter_result={"state": "pending"},
                        replayed=True,
                        deferred_code="TIMEOUT_EXIT_UNPROVEN",
                    )
                runtime = supervisor(service)
                lease = FakeLease(PROJECT_ID, PRINCIPAL_ID, 8, OWNER_ID)
                service.active_lease = lease

                cycle, _, _ = runtime._observe_tasks(
                    [current], lease, float("inf")
                )

                self.assertEqual(cycle.scanned_task_ids, (current.task_id,))
                self.assertEqual(service.checkpoint_calls, [])
                self.assertEqual(service.intent_calls, [])
                self.assertEqual(service.retry_calls, [])
                self.assertEqual(service.refresh_calls, [])
                if control == "cancel":
                    self.assertEqual(
                        cycle.cancel_processed_task_ids, (current.task_id,)
                    )
                    self.assertEqual(service.cancel_calls, [current.task_id])
                    self.assertEqual(service.timeout_calls, [])
                else:
                    self.assertEqual(
                        cycle.timeout_processed_task_ids, (current.task_id,)
                    )
                    self.assertEqual(service.cancel_calls, [])
                    self.assertEqual(service.timeout_calls, [current.task_id])

    def test_absent_checkpoint_hook_preserves_running_and_fences_waiting(self) -> None:
        for status in ("Running", "Waiting"):
            with self.subTest(status=status):
                task_id = f"checkpoint-legacy-{status.lower()}"
                current = snapshot(task_id, status)
                service = FakeTaskService(
                    [current],
                    {task_id: FakeIntent(task_id, "dispatched")},
                )
                service.process_runtime_checkpoint = None  # type: ignore[assignment]
                runtime = supervisor(service)
                lease = FakeLease(PROJECT_ID, PRINCIPAL_ID, 8, OWNER_ID)
                service.active_lease = lease

                cycle, _, _ = runtime._observe_tasks(
                    [current], lease, float("inf")
                )

                self.assertEqual(service.checkpoint_calls, [])
                if status == "Running":
                    self.assertEqual(cycle.refreshed_task_ids, (task_id,))
                    self.assertEqual(cycle.deferred, ())
                else:
                    self.assertEqual(cycle.refreshed_task_ids, ())
                    self.assertEqual(
                        cycle.deferred,
                        ((task_id, "CHECKPOINT_RESUME_UNSUPPORTED"),),
                    )
                    self.assertEqual(service.intent_calls, [])
                    self.assertEqual(service.retry_calls, [])

    def test_malformed_checkpoint_result_stops_the_supervisor(self) -> None:
        cases = (
            FakeCheckpointProcess(
                snapshot("checkpoint-malformed", "Waiting"),
                state="unknown",
                replayed=False,
            ),
            FakeCheckpointProcess(
                snapshot("checkpoint-malformed", "Waiting"),
                state="waiting",
                replayed=False,
                deferred_code="not_stable",
            ),
            FakeCheckpointProcess(
                snapshot("checkpoint-malformed", "Running"),
                state="waiting",
                replayed=False,
            ),
            FakeCheckpointProcess(
                snapshot("another-task", "Waiting"),
                state="waiting",
                replayed=False,
            ),
        )
        for malformed in cases:
            with self.subTest(result=malformed):
                task_id = "checkpoint-malformed"
                current = snapshot(task_id, "Running")
                service = FakeTaskService(
                    [current],
                    {task_id: FakeIntent(task_id, "dispatched")},
                )
                service.checkpoint_results[task_id] = malformed
                runtime = supervisor(service)
                try:
                    runtime.start()
                    self.assertTrue(runtime.wait_until_stopped(timeout=1))
                    self.assertEqual(runtime.failure_code, FATAL)
                    self.assertFalse(runtime.healthy)
                    self.assertEqual(service.checkpoint_calls, [task_id])
                    self.assertEqual(service.intent_calls, [])
                    self.assertEqual(service.retry_calls, [])
                    self.assertEqual(service.refresh_calls, [])
                finally:
                    runtime.stop()

    def test_worker_exit_retry_runs_after_timeout_and_preempts_generic_status(
        self,
    ) -> None:
        task_id = "worker-exit-attempt-one"
        current = snapshot(task_id, "Running", timeout_state="armed")
        retrying_snapshot = snapshot(task_id, "Retrying")
        dispatched_intent = FakeIntent(task_id, "dispatched")
        retrying_intent = FakeIntent(task_id, "retrying")
        service = FakeTaskService([current], {task_id: dispatched_intent})
        service.projection_results[task_id] = FakeProjection(
            intent=dispatched_intent,
            evidence={"ticket": {"state": "spawned"}},
            projected=True,
            adopted=False,
            replayed=False,
        )
        service.timeout_results[task_id] = FakeTimeoutProcess(
            snapshot=current,
            state="armed",
            adapter_result=None,
            replayed=True,
            deferred_code="TIMEOUT_NOT_DUE",
        )
        service.retry_results[task_id] = FakeRetryProcess(
            snapshot=retrying_snapshot,
            intent=retrying_intent,
            state="retrying",
            authorized=True,
            authorization_replayed=False,
            dispatch_attempted=True,
            projected=True,
            adopted=False,
            deferred_code="ADAPTER_CONCURRENCY_LIMIT",
        )
        runtime = supervisor(service)
        lease = FakeLease(PROJECT_ID, PRINCIPAL_ID, 8, OWNER_ID)
        service.active_lease = lease

        cycle, _, _ = runtime._observe_tasks([current], lease, float("inf"))

        self.assertEqual(cycle.timeout_processed_task_ids, (task_id,))
        self.assertEqual(cycle.retry_processed_task_ids, (task_id,))
        self.assertEqual(cycle.retry_dispatched_task_ids, ())
        self.assertEqual(cycle.retry_exhausted_task_ids, ())
        self.assertEqual(
            cycle.deferred, ((task_id, "ADAPTER_CONCURRENCY_LIMIT"),)
        )
        self.assertEqual(cycle.refreshed_task_ids, ())
        self.assertEqual(service.timeout_calls, [task_id])
        self.assertEqual(service.retry_calls, [task_id])
        self.assertEqual(service.refresh_calls, [])
        self.assertLess(service.calls.index("timeout"), service.calls.index("retry"))
        self.assertNotIn("refresh", service.calls)

    def test_retry_none_is_checked_before_same_cycle_status_refresh(self) -> None:
        task_id = "worker-exit-race-window"
        current = snapshot(task_id, "Running")
        dispatched_intent = FakeIntent(task_id, "dispatched")
        service = FakeTaskService([current], {task_id: dispatched_intent})
        runtime = supervisor(service)
        lease = FakeLease(PROJECT_ID, PRINCIPAL_ID, 8, OWNER_ID)
        service.active_lease = lease

        cycle, _, _ = runtime._observe_tasks([current], lease, float("inf"))

        self.assertEqual(cycle.retry_processed_task_ids, ())
        self.assertEqual(cycle.refreshed_task_ids, (task_id,))
        self.assertEqual(service.retry_calls, [task_id])
        self.assertEqual(service.refresh_calls, [task_id])
        self.assertLess(service.calls.index("retry"), service.calls.index("refresh"))

    def test_retrying_attempt_two_dispatches_and_refreshes_in_same_cycle(
        self,
    ) -> None:
        task_id = "worker-exit-attempt-two-ready"
        current = snapshot(task_id, "Retrying")
        retrying_intent = FakeIntent(task_id, "retrying")
        dispatched_intent = FakeIntent(task_id, "dispatched")
        service = FakeTaskService([current], {task_id: retrying_intent})
        service.retry_results[task_id] = FakeRetryProcess(
            snapshot=snapshot(task_id, "Running"),
            intent=dispatched_intent,
            state="dispatched",
            authorized=True,
            authorization_replayed=True,
            dispatch_attempted=True,
            projected=True,
            adopted=True,
            timeout_armed=True,
        )
        runtime = supervisor(service)
        lease = FakeLease(PROJECT_ID, PRINCIPAL_ID, 8, OWNER_ID)
        service.active_lease = lease

        cycle, _, _ = runtime._observe_tasks([current], lease, float("inf"))

        self.assertEqual(cycle.retry_processed_task_ids, (task_id,))
        self.assertEqual(cycle.retry_dispatched_task_ids, (task_id,))
        self.assertEqual(cycle.retry_exhausted_task_ids, ())
        self.assertEqual(cycle.timeout_armed_task_ids, (task_id,))
        self.assertEqual(cycle.refreshed_task_ids, (task_id,))
        self.assertEqual(cycle.deferred, ())
        self.assertEqual(service.retry_calls, [task_id])
        self.assertEqual(service.refresh_calls, [task_id])
        self.assertEqual(service.projection_calls, [])
        self.assertEqual(service.timeout_calls, [])
        self.assertLess(service.calls.index("retry"), service.calls.index("refresh"))

    def test_attempt_two_exhaustion_is_counted_without_status_projection(
        self,
    ) -> None:
        cases = (
            ("pre-ready", "Retrying", "retrying", "retry_exhausted"),
            ("post-ready", "Running", "dispatched", "dispatched"),
        )
        for label, status, initial_state, exhausted_state in cases:
            with self.subTest(label=label):
                task_id = f"worker-exit-exhausted-{label}"
                current = snapshot(task_id, status)
                initial_intent = FakeIntent(task_id, initial_state)
                service = FakeTaskService([current], {task_id: initial_intent})
                service.retry_results[task_id] = FakeRetryProcess(
                    snapshot=snapshot(task_id, "Failed"),
                    intent=FakeIntent(task_id, exhausted_state),
                    state="exhausted",
                    authorized=False,
                    authorization_replayed=False,
                    dispatch_attempted=False,
                    projected=True,
                    adopted=False,
                    deferred_code="WORKER_RETRY_EXHAUSTED",
                )
                runtime = supervisor(service)
                lease = FakeLease(PROJECT_ID, PRINCIPAL_ID, 8, OWNER_ID)
                service.active_lease = lease

                cycle, _, _ = runtime._observe_tasks(
                    [current], lease, float("inf")
                )

                self.assertEqual(cycle.retry_processed_task_ids, (task_id,))
                self.assertEqual(cycle.retry_dispatched_task_ids, ())
                self.assertEqual(cycle.retry_exhausted_task_ids, (task_id,))
                self.assertEqual(cycle.refreshed_task_ids, ())
                self.assertEqual(cycle.deferred, ())
                self.assertEqual(service.retry_calls, [task_id])
                self.assertEqual(service.refresh_calls, [])

    def test_newly_armed_not_due_timeout_keeps_ordinary_status_observation(
        self,
    ) -> None:
        task_id = "timeout-newly-armed"
        current = snapshot(task_id, "Running")
        armed = replace(current, timeout=FakeTimeout("armed"))
        service = FakeTaskService(
            [current],
            {task_id: FakeIntent(task_id, "dispatched")},
        )
        service.projection_results[task_id] = FakeProjection(
            intent=FakeIntent(task_id, "dispatched"),
            evidence={"ticket": {"state": "spawned"}},
            projected=True,
            adopted=False,
            replayed=False,
            timeout_armed=True,
        )
        service.timeout_results[task_id] = FakeTimeoutProcess(
            snapshot=armed,
            state="armed",
            adapter_result=None,
            replayed=True,
            deferred_code="TIMEOUT_NOT_DUE",
        )
        runtime = supervisor(service)
        try:
            self.assertTrue(runtime.start())
            self.assertTrue(runtime.wait_for_cycle(timeout=1))
            cycle = runtime.last_cycle
            self.assertIsNotNone(cycle)
            assert cycle is not None
            self.assertEqual(cycle.timeout_armed_task_ids, (task_id,))
            self.assertEqual(cycle.timeout_processed_task_ids, (task_id,))
            self.assertEqual(cycle.timeout_resolved_task_ids, ())
            self.assertEqual(cycle.refreshed_task_ids, (task_id,))
            self.assertEqual(cycle.deferred, ())
            self.assertEqual(service.timeout_calls, [task_id])
            self.assertEqual(service.refresh_calls, [task_id])
        finally:
            runtime.stop()

    def test_schedules_pending_and_observes_active_queued_or_running_tasks(self) -> None:
        snapshots = [
            snapshot("queued-dispatched", "Queued"),
            snapshot("running-dispatched", "Running"),
            snapshot("queued-pending", "Queued"),
            snapshot("queued-dispatching", "Queued"),
            snapshot("queued-reconcile", "Queued"),
            snapshot("terminal", "Succeeded"),
            snapshot("draft", "Draft"),
        ]
        service = FakeTaskService(
            snapshots,
            {
                "queued-dispatched": FakeIntent("queued-dispatched", "dispatched"),
                "running-dispatched": FakeIntent(
                    "running-dispatched", "dispatched"
                ),
                "queued-pending": FakeIntent("queued-pending", "pending"),
                "queued-dispatching": FakeIntent(
                    "queued-dispatching", "dispatching"
                ),
                "queued-reconcile": FakeIntent(
                    "queued-reconcile", "reconciliation_required"
                ),
                "terminal": FakeIntent("terminal", "dispatched"),
            },
        )
        runtime = supervisor(service)
        try:
            self.assertTrue(runtime.start())
            self.assertTrue(runtime.wait_for_cycle(timeout=1))
            cycle = runtime.last_cycle
            self.assertIsNotNone(cycle)
            assert cycle is not None
            self.assertEqual(
                cycle.scanned_task_ids,
                (
                    "queued-dispatched",
                    "running-dispatched",
                    "queued-pending",
                    "queued-dispatching",
                    "queued-reconcile",
                ),
            )
            self.assertEqual(
                cycle.refreshed_task_ids,
                ("queued-dispatched", "running-dispatched", "queued-pending"),
            )
            self.assertEqual(
                cycle.deferred,
                (
                    ("queued-dispatching", "WORKER_EVIDENCE_NOT_READY"),
                    ("queued-reconcile", "RECONCILIATION_REQUIRED"),
                ),
            )
            self.assertEqual(cycle.scheduled_task_ids, ("queued-pending",))
            self.assertEqual(cycle.dispatched_task_ids, ("queued-pending",))
            self.assertEqual(cycle.task_failures, ())
            self.assertEqual(
                service.intent_calls,
                [
                    "queued-dispatched",
                    "running-dispatched",
                    "queued-pending",
                    "queued-dispatching",
                    "queued-reconcile",
                ],
            )
            self.assertEqual(
                service.projection_calls,
                ["queued-dispatched", "running-dispatched", "queued-dispatching"],
            )
            self.assertEqual(
                service.refresh_calls,
                ["queued-dispatched", "running-dispatched", "queued-pending"],
            )
            self.assertEqual(service.dispatch_calls, 0)
        finally:
            runtime.stop()

    def test_exact_ready_projection_is_adopted_and_refreshed_in_same_cycle(self) -> None:
        task_id = "late-ready"
        service = FakeTaskService(
            [snapshot(task_id, "Queued")],
            {task_id: FakeIntent(task_id, "dispatching")},
        )
        service.projection_results[task_id] = FakeProjection(
            intent=FakeIntent(task_id, "dispatched"),
            evidence={"ticket": {"state": "spawned"}},
            projected=True,
            adopted=True,
            replayed=False,
        )
        runtime = supervisor(service)
        try:
            self.assertTrue(runtime.start())
            self.assertTrue(runtime.wait_for_cycle(timeout=1))
            cycle = runtime.last_cycle
            self.assertIsNotNone(cycle)
            assert cycle is not None
            self.assertEqual(cycle.projected_task_ids, (task_id,))
            self.assertEqual(cycle.adopted_task_ids, (task_id,))
            self.assertEqual(cycle.dispatched_task_ids, (task_id,))
            self.assertEqual(cycle.refreshed_task_ids, (task_id,))
            self.assertEqual(cycle.deferred, ())
            self.assertEqual(cycle.task_failures, ())
            self.assertEqual(service.projection_calls, [task_id])
            self.assertEqual(service.refresh_calls, [task_id])
            self.assertEqual(service.dispatch_calls, 0)
        finally:
            runtime.stop()

    def test_reconciliation_positive_receipt_is_refreshed_in_same_cycle(self) -> None:
        task_id = "reconciliation-ready"
        service = FakeTaskService(
            [snapshot(task_id, "Queued")],
            {task_id: FakeIntent(task_id, "reconciliation_required")},
        )
        service.reconciliation_results[task_id] = FakeReconciliation(
            intent=FakeIntent(task_id, "dispatched"),
            evidence_kind="managed_worker_receipt",
            authorized=True,
            authorization_replayed=False,
            probe_attempted=True,
            projected=True,
            adopted=True,
            timeout_armed=True,
        )
        runtime = supervisor(service)
        try:
            self.assertTrue(runtime.start())
            self.assertTrue(runtime.wait_for_cycle(timeout=1))
            cycle = runtime.last_cycle
            self.assertIsNotNone(cycle)
            assert cycle is not None
            self.assertEqual(cycle.reconciled_task_ids, (task_id,))
            self.assertEqual(cycle.projected_task_ids, (task_id,))
            self.assertEqual(cycle.adopted_task_ids, (task_id,))
            self.assertEqual(cycle.dispatched_task_ids, (task_id,))
            self.assertEqual(cycle.timeout_armed_task_ids, (task_id,))
            self.assertEqual(cycle.refreshed_task_ids, (task_id,))
            self.assertEqual(cycle.deferred, ())
            self.assertEqual(service.reconciliation_calls, [task_id])
            self.assertEqual(service.schedule_calls, [])
            self.assertEqual(service.projection_calls, [])
            self.assertEqual(service.refresh_calls, [task_id])
        finally:
            runtime.stop()

    def test_reconciliation_exact_negative_closes_without_status_or_dispatch(self) -> None:
        task_id = "reconciliation-not-dispatched"
        service = FakeTaskService(
            [snapshot(task_id, "Queued")],
            {task_id: FakeIntent(task_id, "reconciliation_required")},
        )
        service.reconciliation_results[task_id] = FakeReconciliation(
            intent=FakeIntent(task_id, "not_dispatched"),
            evidence_kind="managed_pre_running_failure",
            authorized=True,
            authorization_replayed=False,
            probe_attempted=True,
            projected=True,
            adopted=False,
        )
        runtime = supervisor(service)
        try:
            self.assertTrue(runtime.start())
            self.assertTrue(runtime.wait_for_cycle(timeout=1))
            cycle = runtime.last_cycle
            self.assertIsNotNone(cycle)
            assert cycle is not None
            self.assertEqual(cycle.reconciled_task_ids, (task_id,))
            self.assertEqual(cycle.projected_task_ids, (task_id,))
            self.assertEqual(cycle.dispatched_task_ids, ())
            self.assertEqual(cycle.refreshed_task_ids, ())
            self.assertEqual(cycle.timeout_armed_task_ids, ())
            self.assertEqual(cycle.deferred, ())
            self.assertEqual(cycle.task_failures, ())
            self.assertEqual(service.reconciliation_calls, [task_id])
            self.assertEqual(service.schedule_calls, [])
            self.assertEqual(service.projection_calls, [])
            self.assertEqual(service.refresh_calls, [])
        finally:
            runtime.stop()

    def test_concurrent_managed_resolution_is_projected_in_same_cycle(self) -> None:
        task_id = "reconciliation-concurrent-managed"
        service = FakeTaskService(
            [snapshot(task_id, "Queued")],
            {task_id: FakeIntent(task_id, "reconciliation_required")},
        )
        dispatched = FakeIntent(task_id, "dispatched")
        service.reconciliation_results[task_id] = FakeReconciliation(
            intent=dispatched,
            evidence_kind="managed_worker_receipt",
            authorized=False,
            authorization_replayed=False,
            probe_attempted=False,
            projected=False,
            adopted=False,
        )
        service.projection_results[task_id] = FakeProjection(
            intent=dispatched,
            evidence={"ticket": {"state": "spawned"}},
            projected=True,
            adopted=False,
            replayed=False,
            timeout_armed=True,
        )
        runtime = supervisor(service)
        try:
            self.assertTrue(runtime.start())
            self.assertTrue(runtime.wait_for_cycle(timeout=1))
            cycle = runtime.last_cycle
            self.assertIsNotNone(cycle)
            assert cycle is not None
            self.assertEqual(cycle.reconciled_task_ids, (task_id,))
            self.assertEqual(cycle.projected_task_ids, (task_id,))
            self.assertEqual(cycle.timeout_armed_task_ids, (task_id,))
            self.assertEqual(cycle.refreshed_task_ids, (task_id,))
            self.assertEqual(service.reconciliation_calls, [task_id])
            self.assertEqual(service.projection_calls, [task_id])
            self.assertEqual(service.refresh_calls, [task_id])
        finally:
            runtime.stop()

    def test_reconciliation_probe_is_bounded_to_one_task_per_cycle(self) -> None:
        task_ids = ["reconcile-1", "reconcile-2", "reconcile-3"]
        values = [snapshot(task_id, "Queued") for task_id in task_ids]
        service = FakeTaskService(
            values,
            {
                task_id: FakeIntent(task_id, "reconciliation_required")
                for task_id in task_ids
            },
        )
        clock = ManualClock()
        runtime = supervisor(
            service,
            monotonic=clock,
            worker_projection_interval_seconds=60,
        )
        lease = FakeLease(PROJECT_ID, PRINCIPAL_ID, 8, OWNER_ID)
        service.active_lease = lease

        first, _, _ = runtime._observe_tasks(values, lease, float("inf"))
        second, _, _ = runtime._observe_tasks(values, lease, float("inf"))
        third, _, _ = runtime._observe_tasks(values, lease, float("inf"))

        self.assertEqual(
            service.reconciliation_calls,
            task_ids,
        )
        self.assertEqual(
            [
                sum(
                    code == "RECONCILIATION_CYCLE_LIMIT"
                    for _, code in cycle.deferred
                )
                for cycle in (first, second, third)
            ],
            [2, 1, 0],
        )
        self.assertEqual(service.refresh_calls, [])

    def test_private_receipt_adoption_needs_no_worker_projection(self) -> None:
        task_id = "legacy-private-receipt"
        service = FakeTaskService(
            [snapshot(task_id, "Queued")],
            {task_id: FakeIntent(task_id, "dispatching")},
        )
        service.schedule_results[task_id] = FakeSchedule(
            intent=FakeIntent(task_id, "dispatched"),
            authorized=False,
            authorization_replayed=False,
            dispatch_attempted=False,
            projected=False,
            adopted=True,
        )
        runtime = supervisor(service)
        try:
            self.assertTrue(runtime.start())
            self.assertTrue(runtime.wait_for_cycle(timeout=1))
            cycle = runtime.last_cycle
            self.assertIsNotNone(cycle)
            assert cycle is not None
            self.assertEqual(cycle.projected_task_ids, ())
            self.assertEqual(cycle.adopted_task_ids, (task_id,))
            self.assertEqual(cycle.dispatched_task_ids, (task_id,))
            self.assertEqual(cycle.refreshed_task_ids, (task_id,))
            self.assertEqual(cycle.deferred, ())
            self.assertEqual(cycle.task_failures, ())
            self.assertEqual(service.projection_calls, [])
            self.assertEqual(service.refresh_calls, [task_id])
        finally:
            runtime.stop()

    def test_failed_launch_evidence_has_an_explicit_deferred_code(self) -> None:
        task_id = "failed-attempt"
        service = FakeTaskService(
            [snapshot(task_id, "Queued")],
            {task_id: FakeIntent(task_id, "dispatching")},
        )
        service.projection_results[task_id] = FakeProjection(
            intent=FakeIntent(task_id, "dispatching"),
            evidence={"ticket": {"state": "failed"}},
            projected=True,
            adopted=False,
            replayed=False,
        )
        runtime = supervisor(service)
        try:
            self.assertTrue(runtime.start())
            self.assertTrue(runtime.wait_for_cycle(timeout=1))
            cycle = runtime.last_cycle
            self.assertIsNotNone(cycle)
            assert cycle is not None
            self.assertEqual(
                cycle.deferred, ((task_id, "WORKER_ATTEMPT_FAILED"),)
            )
            self.assertEqual(service.refresh_calls, [])
        finally:
            runtime.stop()

    def test_dispatched_worker_projection_has_an_independent_cadence(self) -> None:
        task_id = "sampled-worker"
        service = FakeTaskService(
            [snapshot(task_id, "Running")],
            {task_id: FakeIntent(task_id, "dispatched")},
        )
        clock = ManualClock()
        runtime = supervisor(
            service,
            monotonic=clock,
            worker_projection_interval_seconds=60,
        )
        lease = FakeLease(PROJECT_ID, PRINCIPAL_ID, 8, OWNER_ID)
        service.active_lease = lease

        runtime._observe_tasks(service.snapshots, lease, float("inf"))
        clock.value = 30.0
        runtime._observe_tasks(service.snapshots, lease, float("inf"))
        clock.value = 60.0
        runtime._observe_tasks(service.snapshots, lease, float("inf"))

        self.assertEqual(service.projection_calls, [task_id, task_id])
        self.assertEqual(service.refresh_calls, [task_id, task_id, task_id])

    def test_one_task_failure_is_contained_and_later_task_is_refreshed(self) -> None:
        service = FakeTaskService(
            [snapshot("first", "Queued"), snapshot("second", "Running")],
            {
                "first": FakeIntent("first", "dispatched"),
                "second": FakeIntent("second", "dispatched"),
            },
        )
        service.refresh_failures["first"] = FakeStatusError(
            "ADAPTER_STATUS_UNAVAILABLE"
        )
        runtime = supervisor(service)
        try:
            self.assertTrue(runtime.start())
            self.assertTrue(runtime.wait_for_cycle(timeout=1))
            self.assertTrue(runtime.healthy)
            cycle = runtime.last_cycle
            self.assertIsNotNone(cycle)
            assert cycle is not None
            self.assertEqual(cycle.refreshed_task_ids, ("second",))
            self.assertEqual(
                cycle.task_failures,
                (("first", "ADAPTER_STATUS_UNAVAILABLE"),),
            )
            self.assertEqual(service.refresh_calls, ["first", "second"])
        finally:
            runtime.stop()

    def test_unclassified_programming_failure_is_fatal(self) -> None:
        service = FakeTaskService(
            [snapshot("first", "Queued"), snapshot("second", "Running")],
            {
                "first": FakeIntent("first", "dispatched"),
                "second": FakeIntent("second", "dispatched"),
            },
        )
        service.refresh_failures["first"] = TypeError("simulated API mismatch")
        runtime = supervisor(service)
        try:
            runtime.start()
            self.assertTrue(runtime.wait_until_stopped(timeout=1))
            self.assertEqual(runtime.failure_code, FATAL)
            self.assertFalse(runtime.healthy)
            self.assertEqual(service.refresh_calls, ["first"])
        finally:
            self.assertTrue(runtime.stop())

    def test_lease_loss_is_fatal_and_stale_lease_is_not_released(self) -> None:
        service = FakeTaskService([snapshot("task-1", "Queued")])
        service.intents["task-1"] = FakeIntent("task-1", "dispatched")
        service.lose_on_heartbeat = True
        runtime = supervisor(
            service,
            lease_seconds=3,
            heartbeat_interval_seconds=0.5,
            monotonic=StepClock(),
        )

        runtime.start()
        self.assertTrue(runtime.wait_until_stopped(timeout=1))
        self.assertEqual(runtime.failure_code, LEASE_LOST)
        self.assertFalse(runtime.healthy)
        self.assertIsNone(runtime.lease)
        self.assertGreaterEqual(service.heartbeat_calls, 1)
        self.assertTrue(runtime.stop())
        self.assertEqual(service.release_calls, 0)
        self.assertEqual(service.refresh_calls, [])

    def test_max_tasks_fails_before_any_task_observation(self) -> None:
        values = [snapshot(f"task-{index}", "Queued") for index in range(3)]
        service = FakeTaskService(
            values,
            {item.task_id: FakeIntent(item.task_id, "dispatched") for item in values},
        )
        runtime = supervisor(service, max_tasks=2)
        try:
            runtime.start()
            self.assertTrue(runtime.wait_until_stopped(timeout=1))
            self.assertEqual(runtime.failure_code, TASK_LIMIT_EXCEEDED)
            self.assertEqual(service.intent_calls, [])
            self.assertEqual(service.refresh_calls, [])
            self.assertFalse(runtime.healthy)
        finally:
            self.assertTrue(runtime.stop())
        self.assertEqual(service.release_calls, 1)

    def test_exactly_max_tasks_completes_the_bounded_cycle(self) -> None:
        values = [snapshot(f"task-{index}", "Queued") for index in range(2)]
        service = FakeTaskService(
            values,
            {item.task_id: FakeIntent(item.task_id, "dispatched") for item in values},
        )
        runtime = supervisor(service, max_tasks=2)
        try:
            self.assertTrue(runtime.start())
            self.assertTrue(runtime.wait_for_cycle(timeout=1))
            self.assertIsNone(runtime.failure_code)
            self.assertEqual(service.refresh_calls, ["task-0", "task-1"])
        finally:
            self.assertTrue(runtime.stop())

    def test_lease_loss_during_status_refresh_self_fences_before_next_task(
        self,
    ) -> None:
        service = FakeTaskService(
            [snapshot("first", "Queued"), snapshot("second", "Queued")],
            {
                "first": FakeIntent("first", "dispatched"),
                "second": FakeIntent("second", "dispatched"),
            },
        )
        service.refresh_failures["first"] = FakeStatusError(LEASE_LOST)
        runtime = supervisor(service)

        runtime.start()
        self.assertTrue(runtime.wait_until_stopped(timeout=1))
        self.assertEqual(runtime.failure_code, LEASE_LOST)
        self.assertIsNone(runtime.lease)
        self.assertEqual(service.refresh_calls, ["first"])
        self.assertTrue(runtime.stop())
        self.assertEqual(service.release_calls, 0)

    def test_stop_during_cycle_prevents_observing_the_next_task(self) -> None:
        service = FakeTaskService(
            [snapshot("first", "Queued"), snapshot("second", "Queued")],
            {
                "first": FakeIntent("first", "dispatched"),
                "second": FakeIntent("second", "dispatched"),
            },
        )
        entered = threading.Event()
        release_refresh = threading.Event()

        def block_first(task_id: str) -> None:
            if task_id == "first":
                entered.set()
                self.assertTrue(release_refresh.wait(1))

        service.refresh_hook = block_first
        runtime = supervisor(service)
        self.assertTrue(runtime.start())
        self.assertTrue(entered.wait(1))
        stop_result: list[bool] = []
        stopper = threading.Thread(target=lambda: stop_result.append(runtime.stop()))
        stopper.start()
        try:
            self.assertTrue(runtime._stop_event.wait(1))
        finally:
            release_refresh.set()
        stopper.join(1)

        self.assertFalse(stopper.is_alive())
        self.assertEqual(stop_result, [True])
        self.assertEqual(service.refresh_calls, ["first"])
        self.assertEqual(service.release_calls, 1)
        self.assertIsNone(runtime.failure_code)

    def test_stop_timeout_does_not_release_a_lease_still_used_by_thread(self) -> None:
        service = FakeTaskService([snapshot("blocked", "Queued")])
        service.intents["blocked"] = FakeIntent("blocked", "dispatched")
        entered = threading.Event()
        release_refresh = threading.Event()

        def block(_: str) -> None:
            entered.set()
            release_refresh.wait(1)

        service.refresh_hook = block
        runtime = supervisor(service, join_timeout_seconds=0.02)
        self.assertTrue(runtime.start())
        self.assertTrue(entered.wait(1))
        try:
            self.assertFalse(runtime.stop())
            self.assertEqual(runtime.failure_code, STOP_TIMEOUT)
            self.assertEqual(service.release_calls, 0)
            self.assertIsNotNone(runtime.lease)
            self.assertFalse(runtime.start())
        finally:
            release_refresh.set()
        self.assertTrue(runtime.wait_until_stopped(timeout=1))
        self.assertTrue(runtime.stop())
        self.assertEqual(service.release_calls, 1)

    def test_transient_release_failure_retains_lease_for_idempotent_retry(self) -> None:
        service = FakeTaskService()
        service.release_failures_remaining = 1
        runtime = supervisor(service)
        self.assertTrue(runtime.start())
        self.assertTrue(runtime.wait_for_cycle(timeout=1))

        self.assertFalse(runtime.stop())
        self.assertEqual(runtime.failure_code, LEASE_RELEASE_FAILED)
        self.assertIsNotNone(runtime.lease)
        self.assertIsNotNone(service.active_lease)
        self.assertEqual(service.release_calls, 1)

        self.assertTrue(runtime.stop())
        self.assertIsNone(runtime.lease)
        self.assertIsNone(service.active_lease)
        self.assertEqual(service.release_calls, 2)


if __name__ == "__main__":
    unittest.main()
