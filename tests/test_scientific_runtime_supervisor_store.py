from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from scientific_runtime.fwi_registry import load_deepwave_manifest
from scientific_runtime.registry_service import RegistryService
from scientific_runtime.task_service import (
    TaskService,
    TaskSupervisorLeaseLost,
)
from scientific_runtime.task_store import (
    RuntimeSupervisorLeaseLost,
    SQLiteTaskStore,
    TaskStoreConflict,
)
from scientific_runtime_contracts import compute_plan_hash
from tests.test_scientific_runtime_contracts import (
    algorithm_manifest,
    dataset_ref,
    optimizer_plan_graph,
    optimizer_task_draft,
)
from tests.test_scientific_runtime_task_service import (
    FakeDispatcher,
    NOW,
    PRINCIPAL_ID,
    PROJECT_ID,
    executable_approval_decision,
)


T_PLUS_1 = "2026-07-15T03:00:01Z"
T_PLUS_5 = "2026-07-15T03:00:05Z"
T_PLUS_10 = "2026-07-15T03:00:10Z"
T_PLUS_11 = "2026-07-15T03:00:11Z"
T_PLUS_15 = "2026-07-15T03:00:15Z"
T_PLUS_19 = "2026-07-15T03:00:19Z"
T_PLUS_20 = "2026-07-15T03:00:20Z"
T_PLUS_21 = "2026-07-15T03:00:21Z"
T_PLUS_30 = "2026-07-15T03:00:30Z"


class ScientificRuntimeSupervisorStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary.name) / "task.sqlite3"
        self.store = SQLiteTaskStore(self.database_path)
        self.now = [NOW]
        self.registry = RegistryService(self.store, clock=lambda: self.now[0])
        self.registry.register_dataset(dataset=dataset_ref())
        self.registry.register_algorithm(manifest=algorithm_manifest())
        self.registry.register_algorithm(manifest=load_deepwave_manifest())
        self.next_task_id = 0

        def task_id_factory() -> str:
            self.next_task_id += 1
            return f"task-supervisor-{self.next_task_id:04d}"

        self.task_id_factory = task_id_factory
        self.service = TaskService(
            self.store,
            task_id_factory=self.task_id_factory,
            clock=lambda: self.now[0],
        )
        self.scope = {
            "project_id": PROJECT_ID,
            "principal_id": PRINCIPAL_ID,
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _acquire(
        self,
        owner_id: str,
        *,
        now: str = NOW,
        lease_seconds: int = 10,
    ):
        return self.store.acquire_runtime_supervisor_lease(
            **self.scope,
            owner_id=owner_id,
            lease_seconds=lease_seconds,
            clock=lambda: now,
        )

    def _submitted_runtime(
        self, *, key: str
    ) -> tuple[str, FakeDispatcher, TaskService]:
        token = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
        draft = optimizer_task_draft()
        draft["draft_id"] = f"draft-{token}"
        created = self.service.create_task(
            draft=draft,
            idempotency_key=f"create-{key}",
            **self.scope,
        )
        task_id = created.snapshot.task_id

        plan = optimizer_plan_graph()
        plan["plan_id"] = f"plan-{token}"
        plan["draft"] = {
            "draft_id": draft["draft_id"],
            "revision": created.snapshot.draft["revision"],
        }
        plan["nodes"][0]["idempotency_key"] = f"node-{token}-submit"
        plan["plan_hash"] = compute_plan_hash(plan)
        self.service.persist_plan(task_id=task_id, plan=plan, **self.scope)
        approval = executable_approval_decision(plan)
        approval["approval_id"] = f"approval-{token}"
        self.service.persist_approval(
            task_id=task_id,
            approval=approval,
            **self.scope,
        )

        dispatcher = FakeDispatcher(self.store)
        runtime = TaskService(
            self.store,
            clock=lambda: self.now[0],
            dispatcher=dispatcher,
        )
        submitted = runtime.submit_task(
            task_id=task_id,
            approval_id=approval["approval_id"],
            idempotency_key=f"submit-{key}",
            **self.scope,
        )
        self.assertEqual(submitted.intent.state, "dispatched")
        return task_id, dispatcher, runtime

    def test_fresh_v8_has_supervisor_tables_and_immutable_triggers(self) -> None:
        self.assertEqual(self.store.migration_version(), 8)
        expected_tables = {
            "runtime_supervisor_terms",
            "runtime_supervisor_leases",
            "runtime_supervisor_term_closures",
            "supervised_run_event_commits",
        }
        expected_triggers = {
            "runtime_supervisor_terms_are_append_only",
            "runtime_supervisor_terms_cannot_be_deleted",
            "runtime_supervisor_term_closures_are_append_only",
            "runtime_supervisor_term_closures_cannot_be_deleted",
            "runtime_supervisor_lease_scope_is_immutable",
            "runtime_supervisor_lease_fence_is_contiguous",
            "runtime_supervisor_lease_term_is_immutable",
            "runtime_supervisor_heartbeat_is_monotonic",
            "runtime_supervisor_leases_cannot_be_deleted",
            "supervised_run_event_commit_requires_active_term",
            "supervised_run_event_commits_are_append_only",
            "supervised_run_event_commits_cannot_be_deleted",
        }
        connection = self._connection()
        try:
            rows = connection.execute(
                """
                SELECT type, name FROM sqlite_master
                WHERE type IN ('table', 'trigger')
                """
            ).fetchall()
            tables = {row["name"] for row in rows if row["type"] == "table"}
            triggers = {row["name"] for row in rows if row["type"] == "trigger"}
            self.assertTrue(expected_tables <= tables)
            self.assertTrue(expected_triggers <= triggers)
            migration = connection.execute(
                """
                SELECT name FROM schema_migrations WHERE version = 8
                """
            ).fetchone()
            self.assertEqual(migration["name"], "0008_runtime_supervisor.sql")
        finally:
            connection.close()

        acquired = self._acquire("owner-fresh-v8")
        self.assertTrue(acquired.acquired)
        released = self.store.release_runtime_supervisor_lease(
            lease=acquired.lease,
            clock=lambda: T_PLUS_1,
        )
        self.assertEqual(released.state, "released")

        connection = self._connection()
        try:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                connection.execute(
                    """
                    UPDATE runtime_supervisor_terms SET owner_id = 'tampered'
                    WHERE project_id = ? AND principal_id = ?
                    """,
                    (PROJECT_ID, PRINCIPAL_ID),
                )
            connection.rollback()
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                connection.execute(
                    """
                    UPDATE runtime_supervisor_term_closures
                    SET reason = 'expired_takeover'
                    WHERE project_id = ? AND principal_id = ?
                    """,
                    (PROJECT_ID, PRINCIPAL_ID),
                )
            connection.rollback()
            with self.assertRaisesRegex(sqlite3.IntegrityError, "cannot be deleted"):
                connection.execute(
                    """
                    DELETE FROM runtime_supervisor_leases
                    WHERE project_id = ? AND principal_id = ?
                    """,
                    (PROJECT_ID, PRINCIPAL_ID),
                )
        finally:
            connection.rollback()
            connection.close()

    def test_active_foreign_owner_cannot_take_over(self) -> None:
        first = self._acquire("owner-active-a")
        blocked = self._acquire(
            "owner-active-b",
            now=T_PLUS_1,
        )
        self.assertTrue(first.acquired)
        self.assertFalse(blocked.acquired)
        self.assertEqual(blocked.lease, first.lease)

        connection = self._connection()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM runtime_supervisor_terms"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM runtime_supervisor_term_closures"
                ).fetchone()[0],
                0,
            )
        finally:
            connection.close()

    def test_same_owner_acquire_is_exact_replay(self) -> None:
        first = self._acquire("owner-replay")
        replay = self._acquire(
            "owner-replay",
            now=T_PLUS_1,
        )
        self.assertTrue(first.acquired)
        self.assertTrue(replay.acquired)
        self.assertEqual(replay.lease, first.lease)

        connection = self._connection()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM runtime_supervisor_terms"
                ).fetchone()[0],
                1,
            )
            stored = connection.execute(
                """
                SELECT heartbeat_at, expires_at FROM runtime_supervisor_leases
                WHERE project_id = ? AND principal_id = ?
                """,
                (PROJECT_ID, PRINCIPAL_ID),
            ).fetchone()
            self.assertEqual(stored["heartbeat_at"], first.lease.heartbeat_at)
            self.assertEqual(stored["expires_at"], first.lease.expires_at)
        finally:
            connection.close()

    def test_lease_clock_is_sampled_only_after_the_sqlite_writer_lock(self) -> None:
        blocker = self._connection()
        clock_sampled = threading.Event()

        def transaction_clock() -> str:
            clock_sampled.set()
            return T_PLUS_1

        try:
            blocker.execute("BEGIN IMMEDIATE")
            with ThreadPoolExecutor(max_workers=1) as executor:
                acquisition = executor.submit(
                    self.store.acquire_runtime_supervisor_lease,
                    **self.scope,
                    owner_id="owner-after-lock",
                    lease_seconds=10,
                    clock=transaction_clock,
                )
                self.assertFalse(clock_sampled.wait(0.1))
                blocker.commit()
                result = acquisition.result(timeout=5)
        finally:
            if blocker.in_transaction:
                blocker.rollback()
            blocker.close()

        self.assertTrue(clock_sampled.is_set())
        self.assertTrue(result.acquired)
        self.assertEqual(result.lease.acquired_at, "2026-07-15T03:00:01.000000Z")
        self.assertEqual(result.lease.expires_at, "2026-07-15T03:00:11.000000Z")

    def test_replay_and_reacquire_reject_regressed_control_plane_time(self) -> None:
        first = self._acquire("owner-time-floor")
        heartbeat = self.store.heartbeat_runtime_supervisor_lease(
            lease=first.lease,
            lease_seconds=10,
            clock=lambda: T_PLUS_5,
        )
        self.assertEqual(heartbeat.expires_at, "2026-07-15T03:00:15.000000Z")

        with self.assertRaisesRegex(TaskStoreConflict, "clock regressed"):
            self._acquire("owner-time-floor", now=T_PLUS_1)
        foreign = self._acquire("owner-time-foreign", now=T_PLUS_1)
        self.assertFalse(foreign.acquired)
        self.assertEqual(foreign.lease, heartbeat)

        released = self.store.release_runtime_supervisor_lease(
            lease=heartbeat,
            clock=lambda: T_PLUS_19,
        )
        self.assertEqual(released.state, "released")
        with self.assertRaisesRegex(TaskStoreConflict, "clock regressed"):
            self._acquire("owner-time-floor", now=T_PLUS_11)
        reacquired = self._acquire("owner-time-floor", now=T_PLUS_20)
        self.assertTrue(reacquired.acquired)
        self.assertEqual(reacquired.lease.fencing_token, 2)

    def test_exact_expiry_takeover_increments_fence_and_closes_old_term(self) -> None:
        first = self._acquire("owner-expiry-a")
        takeover = self._acquire(
            "owner-expiry-b",
            now=T_PLUS_10,
        )
        self.assertTrue(takeover.acquired)
        self.assertEqual(takeover.lease.fencing_token, first.lease.fencing_token + 1)
        self.assertEqual(takeover.lease.owner_id, "owner-expiry-b")

        connection = self._connection()
        try:
            closure = connection.execute(
                """
                SELECT reason, final_heartbeat_at, final_expires_at, closed_at
                FROM runtime_supervisor_term_closures
                WHERE project_id = ? AND principal_id = ? AND fencing_token = ?
                """,
                (PROJECT_ID, PRINCIPAL_ID, first.lease.fencing_token),
            ).fetchone()
            self.assertEqual(closure["reason"], "expired_takeover")
            self.assertEqual(closure["final_heartbeat_at"], first.lease.heartbeat_at)
            self.assertEqual(closure["final_expires_at"], first.lease.expires_at)
            self.assertEqual(closure["closed_at"], takeover.lease.acquired_at)
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM runtime_supervisor_terms"
                ).fetchone()[0],
                2,
            )
        finally:
            connection.close()

    def test_old_term_cannot_heartbeat_or_release_after_takeover(self) -> None:
        first = self._acquire("owner-stale-a")
        takeover = self._acquire(
            "owner-stale-b",
            now=T_PLUS_10,
        )
        with self.assertRaises(RuntimeSupervisorLeaseLost):
            self.store.heartbeat_runtime_supervisor_lease(
                lease=first.lease,
                lease_seconds=10,
                clock=lambda: T_PLUS_11,
            )
        with self.assertRaises(RuntimeSupervisorLeaseLost):
            self.store.release_runtime_supervisor_lease(
                lease=first.lease,
                clock=lambda: T_PLUS_11,
            )
        self.assertEqual(
            self.store.get_runtime_supervisor_lease(**self.scope),
            takeover.lease,
        )

    def test_release_replays_and_same_owner_gets_a_new_aba_fence(self) -> None:
        first = self._acquire("owner-aba")
        released = self.store.release_runtime_supervisor_lease(
            lease=first.lease,
            clock=lambda: T_PLUS_1,
        )
        replay = self.store.release_runtime_supervisor_lease(
            lease=first.lease,
            clock=lambda: T_PLUS_1,
        )
        self.assertEqual(released.state, "released")
        self.assertEqual(replay, released)

        reacquired = self._acquire(
            "owner-aba",
            now=T_PLUS_5,
            lease_seconds=15,
        )
        self.assertTrue(reacquired.acquired)
        self.assertEqual(reacquired.lease.fencing_token, 2)
        self.assertEqual(reacquired.lease.owner_id, first.lease.owner_id)
        self.assertNotEqual(reacquired.lease.acquired_at, first.lease.acquired_at)
        with self.assertRaises(RuntimeSupervisorLeaseLost):
            self.store.release_runtime_supervisor_lease(
                lease=first.lease,
                clock=lambda: T_PLUS_11,
            )

        connection = self._connection()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM runtime_supervisor_terms"
                ).fetchone()[0],
                2,
            )
            closures = connection.execute(
                """
                SELECT fencing_token, reason
                FROM runtime_supervisor_term_closures ORDER BY fencing_token
                """
            ).fetchall()
            self.assertEqual(
                [(row["fencing_token"], row["reason"]) for row in closures],
                [(1, "released")],
            )
        finally:
            connection.close()

    def test_concurrent_acquire_has_exactly_one_owner(self) -> None:
        owners = [f"owner-concurrent-{index}" for index in range(8)]
        barrier = threading.Barrier(len(owners))

        def acquire(owner_id: str):
            barrier.wait()
            return self._acquire(owner_id)

        with ThreadPoolExecutor(max_workers=len(owners)) as executor:
            results = list(executor.map(acquire, owners))

        self.assertEqual(sum(result.acquired for result in results), 1)
        winner = next(result.lease for result in results if result.acquired)
        self.assertTrue(all(result.lease == winner for result in results))
        self.assertIn(winner.owner_id, owners)
        self.assertEqual(winner.fencing_token, 1)

        connection = self._connection()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM runtime_supervisor_terms"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM runtime_supervisor_leases"
                ).fetchone()[0],
                1,
            )
        finally:
            connection.close()

    def test_guarded_runtime_commit_records_the_exact_supervisor_term(self) -> None:
        task_id, dispatcher, runtime = self._submitted_runtime(key="guarded-commit")
        acquisition = self._acquire("owner-guarded")
        intent = self.store.get_dispatch_intent(task_id)
        snapshot = self.store.get_task(task_id)
        self.assertIsNotNone(intent)
        self.assertIsNotNone(intent.handle)
        self.assertIsNotNone(snapshot)
        adapter_status = {
            "job_id": intent.handle["job_id"],
            "task_id": task_id,
            "node_id": intent.node_id,
            "status": "Running",
            "stage": "inversion",
            "completed": 1,
            "total": 2,
            "message": "iteration 1 of 2",
            "updated_at": T_PLUS_1,
            "terminal": False,
        }
        event = TaskService._adapter_event(
            snapshot=snapshot,
            intent=intent,
            adapter_status=adapter_status,
            event_type="node_started",
            sequence=2,
        )
        self.now[0] = T_PLUS_1
        committed = runtime.record_run_event(
            task_id=task_id,
            expected_status="Queued",
            event=event,
            supervisor_lease=acquisition.lease,
            **self.scope,
        )
        self.assertEqual(committed.status, "Running")
        self.assertEqual(dispatcher.status_calls, 0)

        connection = self._connection()
        try:
            audit = connection.execute(
                """
                SELECT task_id, sequence, project_id, principal_id,
                       fencing_token, recorded_at
                FROM supervised_run_event_commits
                """
            ).fetchone()
            self.assertEqual(audit["task_id"], task_id)
            self.assertEqual(audit["sequence"], 2)
            self.assertEqual(audit["project_id"], PROJECT_ID)
            self.assertEqual(audit["principal_id"], PRINCIPAL_ID)
            self.assertEqual(
                audit["fencing_token"], acquisition.lease.fencing_token
            )
            self.assertEqual(audit["recorded_at"], "2026-07-15T03:00:01.000000Z")
            lease_activity = connection.execute(
                """
                SELECT heartbeat_at, expires_at
                FROM runtime_supervisor_leases
                WHERE project_id = ? AND principal_id = ?
                """,
                (PROJECT_ID, PRINCIPAL_ID),
            ).fetchone()
            self.assertEqual(
                lease_activity["heartbeat_at"],
                "2026-07-15T03:00:01.000000Z",
            )
            self.assertEqual(
                lease_activity["expires_at"],
                "2026-07-15T03:00:10.000000Z",
            )
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                connection.execute(
                    """
                    UPDATE supervised_run_event_commits SET fencing_token = 99
                    WHERE task_id = ? AND sequence = 2
                    """,
                    (task_id,),
                )
        finally:
            connection.rollback()
            connection.close()

        with self.assertRaises(RuntimeSupervisorLeaseLost):
            self.store.release_runtime_supervisor_lease(
                lease=acquisition.lease,
                clock=lambda: NOW,
            )

    def test_supervised_commit_cannot_backdate_before_current_heartbeat(self) -> None:
        task_id, dispatcher, runtime = self._submitted_runtime(
            key="guarded-clock-floor"
        )
        first = self._acquire("owner-clock-floor")
        self.now[0] = T_PLUS_5
        heartbeat = runtime.heartbeat_runtime_supervisor_lease(
            first.lease,
            lease_seconds=10,
        )
        intent = self.store.get_dispatch_intent(task_id)
        snapshot = self.store.get_task(task_id)
        self.assertIsNotNone(intent)
        self.assertIsNotNone(intent.handle)
        self.assertIsNotNone(snapshot)
        adapter_status = {
            "job_id": intent.handle["job_id"],
            "task_id": task_id,
            "node_id": intent.node_id,
            "status": "Running",
            "stage": "inversion",
            "completed": 1,
            "total": 2,
            "message": "iteration 1 of 2",
            "updated_at": T_PLUS_1,
            "terminal": False,
        }
        event = TaskService._adapter_event(
            snapshot=snapshot,
            intent=intent,
            adapter_status=adapter_status,
            event_type="node_started",
            sequence=2,
        )

        self.now[0] = T_PLUS_1
        with self.assertRaises(TaskSupervisorLeaseLost):
            runtime.record_run_event(
                task_id=task_id,
                expected_status="Queued",
                event=event,
                supervisor_lease=heartbeat,
                **self.scope,
            )

        self.assertEqual(self.store.get_task(task_id).status, "Queued")
        self.assertEqual(len(self.store.list_run_events(task_id)), 1)
        self.assertEqual(dispatcher.status_calls, 0)
        connection = self._connection()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM supervised_run_event_commits"
                ).fetchone()[0],
                0,
            )
        finally:
            connection.close()

    def test_status_observation_crossing_expiry_is_fenced_then_replayed_by_new_term(
        self,
    ) -> None:
        task_id, dispatcher, runtime = self._submitted_runtime(
            key="status-crosses-expiry"
        )
        first = self._acquire("owner-status-old")
        dispatcher.adapter_status = {
            "status": "Running",
            "stage": "inversion",
            "completed": 1,
            "total": 2,
            "message": "iteration 1 of 2",
            "updated_at": T_PLUS_5,
            "terminal": False,
        }
        entered_status = threading.Event()
        release_status = threading.Event()
        original_status = dispatcher.status

        def blocked_status(intent):
            observation = original_status(intent)
            entered_status.set()
            self.assertTrue(release_status.wait(5))
            return observation

        dispatcher.status = blocked_status
        with ThreadPoolExecutor(max_workers=1) as executor:
            stale_refresh = executor.submit(
                runtime.refresh_runtime_status,
                task_id,
                supervisor_lease=first.lease,
                **self.scope,
            )
            self.assertTrue(entered_status.wait(5))
            self.now[0] = T_PLUS_10
            takeover = self._acquire(
                "owner-status-new",
                now=T_PLUS_10,
            )
            release_status.set()
            with self.assertRaises(TaskSupervisorLeaseLost):
                stale_refresh.result(timeout=5)

        dispatcher.status = original_status
        self.assertEqual(self.store.get_task(task_id).status, "Queued")
        self.assertEqual(len(self.store.list_run_events(task_id)), 1)
        connection = self._connection()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM supervised_run_event_commits"
                ).fetchone()[0],
                0,
            )
        finally:
            connection.close()

        refreshed = runtime.refresh_runtime_status(
            task_id,
            supervisor_lease=takeover.lease,
            **self.scope,
        )
        self.assertEqual(refreshed.snapshot.status, "Running")
        self.assertEqual(
            [event["event_type"] for event in self.store.list_run_events(task_id)],
            ["task_queued", "node_started", "node_progress"],
        )
        connection = self._connection()
        try:
            audits = connection.execute(
                """
                SELECT sequence, fencing_token
                FROM supervised_run_event_commits ORDER BY sequence
                """
            ).fetchall()
            self.assertEqual(
                [(row["sequence"], row["fencing_token"]) for row in audits],
                [
                    (2, takeover.lease.fencing_token),
                    (3, takeover.lease.fencing_token),
                ],
            )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
