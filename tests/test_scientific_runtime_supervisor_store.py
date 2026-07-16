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
    encode_document,
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
    managed_worker_evidence,
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


def cancel_adapter_proof(
    *,
    task_id: str,
    request_id: str,
    attempt_id: str,
    state: str,
    terminal_status: str,
) -> dict:
    cancelled = state == "cancelled"
    payload = {
        "schema_version": "1.0.0",
        "task_id": task_id,
        "request_id": request_id,
        "reason": "user_requested",
        "state": state,
        "code": "CANCEL_COMPLETED" if cancelled else "CANCEL_TERMINAL_WON",
        "attempt_id": attempt_id,
        "capability_record_hash": "sha256:" + "a" * 64 if cancelled else None,
        "request_record_hash": "sha256:" + "b" * 64 if cancelled else None,
        "acknowledgement_record_hash": (
            "sha256:" + "c" * 64 if cancelled else None
        ),
        "terminal_status": terminal_status,
        "local_run_state": "retained",
        "replayed": False,
        "receipt_record_hash": "sha256:" + "d" * 64,
    }
    return {**payload, "proof_hash": encode_document(payload)[1]}


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

    def _pending_runtime(
        self, *, key: str, deferred: bool = False
    ):
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

        dispatcher = FakeDispatcher(
            self.store,
            failure_code=("ADAPTER_CONCURRENCY_LIMIT" if deferred else None),
        )
        dispatcher.defer_dispatch = deferred
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
        self.assertEqual(submitted.intent.state, "pending")
        return task_id, dispatcher, runtime, submitted.intent

    def _submitted_runtime(
        self, *, key: str, deferred: bool = False
    ) -> tuple[str, FakeDispatcher, TaskService]:
        task_id, dispatcher, runtime, intent = self._pending_runtime(
            key=key,
            deferred=deferred,
        )
        claimed, claimed_now = self.store.claim_dispatch(
            intent_id=intent.intent_id,
            now=self.now[0],
        )
        self.assertTrue(claimed_now)
        final_intent = runtime._dispatch_claimed_intent(
            snapshot=self.store.get_task(task_id),
            intent=claimed,
        )
        self.assertEqual(
            final_intent.state, "dispatching" if deferred else "dispatched"
        )
        return task_id, dispatcher, runtime

    def _cancellable_runtime(self, *, key: str):
        task_id, dispatcher, runtime, _ = self._pending_runtime(key=key)
        acquisition = runtime.acquire_runtime_supervisor_lease(
            **self.scope,
            owner_id=f"cancel-owner-{key}",
            lease_seconds=10,
        )
        self.assertTrue(acquisition.acquired)
        scheduled = runtime.schedule_runtime_dispatch(
            task_id,
            **self.scope,
            supervisor_lease=acquisition.lease,
        )
        self.assertEqual(scheduled.intent.state, "dispatched")
        self.assertTrue(runtime.can_cancel_task(task_id, **self.scope))
        admitted = runtime.cancel_task(
            task_id=task_id,
            reason="user_requested",
            idempotency_key=f"cancel-{key}",
            **self.scope,
        )
        self.assertFalse(admitted.replayed)
        self.assertIsNotNone(admitted.snapshot.cancellation)
        return task_id, dispatcher, runtime, acquisition.lease, admitted

    def _insert_direct_cancel_request(
        self,
        connection: sqlite3.Connection,
        *,
        task_id: str,
        intent,
        attempt_id: str,
        request_id: str,
        event_id: str,
    ) -> None:
        task = connection.execute(
            "SELECT status FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        self.assertIsNotNone(task)
        assert task is not None and intent.handle is not None
        next_sequence = connection.execute(
            "SELECT MAX(sequence) + 1 FROM run_events WHERE task_id = ?",
            (task_id,),
        ).fetchone()[0]
        event = {
            "schema_version": "1.0.0",
            "event_id": event_id,
            "sequence": next_sequence,
            "task_id": task_id,
            "node_id": intent.node_id,
            "event_type": "cancel_requested",
            "task_status": task["status"],
            "occurred_at": NOW,
            "fingerprint": intent.handle["fingerprint"],
            "extensions": {
                "org.agent_rpc.cancellation": {
                    "request_id": request_id,
                    "attempt_id": attempt_id,
                    "reason": "user_requested",
                }
            },
        }
        event_json, event_hash = encode_document(event)
        _, fingerprint_hash = encode_document(event["fingerprint"])
        connection.execute(
            """
            INSERT INTO run_events(
                task_id, sequence, event_id, event_type, task_status,
                node_id, fingerprint_hash, document_json, document_hash,
                occurred_at, recorded_at
            ) VALUES (?, ?, ?, 'cancel_requested', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                next_sequence,
                event_id,
                task["status"],
                intent.node_id,
                fingerprint_hash,
                event_json,
                event_hash,
                NOW,
                NOW,
            ),
        )

        request = {
            "schema_version": "1.0.0",
            "request_id": request_id,
            "task_id": task_id,
            "intent_id": intent.intent_id,
            "attempt_id": attempt_id,
            "reason": "user_requested",
            "actor": {"type": "user", "id": PRINCIPAL_ID},
            "requested_at": NOW,
            "extensions": {},
        }
        request_json, request_document_hash = encode_document(request)
        _, request_hash = encode_document(
            {
                "task_id": task_id,
                "project_id": PROJECT_ID,
                "principal_id": PRINCIPAL_ID,
                "action": "cancel_task",
                "reason": "user_requested",
            }
        )
        connection.execute(
            """
            INSERT INTO task_cancel_requests(
                request_id, task_id, project_id, principal_id,
                intent_id, attempt_id, reason, idempotency_key,
                request_hash, request_event_sequence,
                document_json, document_hash, requested_at, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'user_requested', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                task_id,
                PROJECT_ID,
                PRINCIPAL_ID,
                intent.intent_id,
                attempt_id,
                f"direct-sql-{request_id}",
                request_hash,
                next_sequence,
                request_json,
                request_document_hash,
                NOW,
                NOW,
            ),
        )

    def _terminal_event(
        self,
        *,
        task_id: str,
        intent,
        request_id: str,
        attempt_id: str,
        terminal_status: str,
        proof_hash: str,
        event_id: str,
    ) -> dict:
        event_type = {
            "Cancelled": "task_cancelled",
            "Succeeded": "node_succeeded",
            "Failed": "node_failed",
        }[terminal_status]
        return {
            "schema_version": "1.0.0",
            "event_id": event_id,
            "sequence": self.store.latest_run_event_sequence(task_id) + 1,
            "task_id": task_id,
            "node_id": intent.node_id,
            "event_type": event_type,
            "task_status": terminal_status,
            "occurred_at": NOW,
            "fingerprint": intent.handle["fingerprint"],
            "extensions": {
                "org.agent_rpc.cancellation": {
                    "request_id": request_id,
                    "attempt_id": attempt_id,
                    "reason": "user_requested",
                    "proof_hash": proof_hash,
                }
            },
        }

    def test_fresh_v11_has_supervisor_tables_and_immutable_triggers(self) -> None:
        self.assertEqual(self.store.migration_version(), 11)
        expected_tables = {
            "runtime_supervisor_terms",
            "runtime_supervisor_leases",
            "runtime_supervisor_term_closures",
            "supervised_run_event_commits",
            "worker_launch_attempts",
            "worker_attempt_observations",
            "supervised_dispatch_adoptions",
            "supervised_dispatch_attempts",
            "supervised_private_receipt_adoptions",
            "task_cancel_requests",
            "supervised_cancel_attempts",
            "task_cancel_outcomes",
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
            "worker_launch_attempt_requires_matching_intent",
            "worker_launch_attempt_requires_active_term",
            "worker_attempt_observation_requires_matching_attempt",
            "worker_attempt_observation_requires_active_term",
            "worker_attempt_observation_sequence_is_contiguous",
            "worker_attempt_observation_cannot_regress",
            "supervised_dispatch_adoption_requires_matching_attempt",
            "supervised_dispatch_adoption_requires_active_term",
            "worker_launch_attempts_are_append_only",
            "worker_launch_attempts_cannot_be_deleted",
            "worker_attempt_observations_are_append_only",
            "worker_attempt_observations_cannot_be_deleted",
            "supervised_dispatch_adoptions_are_append_only",
            "supervised_dispatch_adoptions_cannot_be_deleted",
            "supervised_dispatch_attempt_requires_matching_intent",
            "supervised_pending_dispatch_requires_atomic_claim",
            "supervised_no_record_takeover_requires_no_worker_projection",
            "supervised_staged_resume_requires_exact_projection",
            "supervised_dispatch_attempt_requires_active_term",
            "supervised_dispatch_attempts_are_immutable",
            "supervised_dispatch_attempts_cannot_be_deleted",
            "supervised_private_receipt_requires_exact_outcome",
            "supervised_private_receipt_requires_active_term",
            "supervised_private_receipt_adoptions_are_immutable",
            "supervised_private_receipt_adoptions_cannot_be_deleted",
            "task_cancel_request_requires_exact_running_attempt",
            "task_cancel_request_blocks_supervised_dispatch",
            "supervised_cancel_attempt_requires_pending_request",
            "supervised_cancel_attempt_requires_active_term",
            "task_cancel_outcome_requires_terminal_event",
            "task_cancel_outcome_requires_active_term",
            "task_cancel_requests_are_immutable",
            "task_cancel_requests_cannot_be_deleted",
            "supervised_cancel_attempts_are_immutable",
            "supervised_cancel_attempts_cannot_be_deleted",
            "task_cancel_outcomes_are_immutable",
            "task_cancel_outcomes_cannot_be_deleted",
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
            worker_migration = connection.execute(
                "SELECT name FROM schema_migrations WHERE version = 9"
            ).fetchone()
            self.assertEqual(
                worker_migration["name"], "0009_worker_attempt_projection.sql"
            )
            dispatch_migration = connection.execute(
                "SELECT name FROM schema_migrations WHERE version = 10"
            ).fetchone()
            self.assertEqual(
                dispatch_migration["name"], "0010_supervised_dispatch.sql"
            )
            cancel_migration = connection.execute(
                "SELECT name FROM schema_migrations WHERE version = 11"
            ).fetchone()
            self.assertEqual(
                cancel_migration["name"], "0011_task_cancellation.sql"
            )
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

    def test_v8_runtime_with_active_lease_upgrades_in_place_to_v11(self) -> None:
        task_id, _, _ = self._submitted_runtime(key="upgrade-v8-v9")
        acquired = self._acquire("upgrade-owner", lease_seconds=30)
        self.assertTrue(acquired.acquired)
        connection = self._connection()
        try:
            connection.execute(
                "DROP TRIGGER task_cancel_request_blocks_supervised_dispatch"
            )
            connection.execute("DROP TABLE task_cancel_outcomes")
            connection.execute("DROP TABLE supervised_cancel_attempts")
            connection.execute("DROP TABLE task_cancel_requests")
            connection.execute("DROP TABLE supervised_private_receipt_adoptions")
            connection.execute("DROP TABLE supervised_dispatch_attempts")
            connection.execute("DROP TABLE supervised_dispatch_adoptions")
            connection.execute("DROP TABLE worker_attempt_observations")
            connection.execute("DROP TABLE worker_launch_attempts")
            connection.execute("DELETE FROM schema_migrations WHERE version >= 9")
            connection.execute("PRAGMA user_version = 8")
            connection.commit()
        finally:
            connection.close()

        reopened = SQLiteTaskStore(self.database_path)
        self.assertEqual(reopened.migration_version(), 11)
        self.assertEqual(reopened.get_task(task_id).status, "Queued")
        lease = reopened.get_runtime_supervisor_lease(**self.scope)
        self.assertIsNotNone(lease)
        assert lease is not None
        self.assertEqual(lease.fencing_token, acquired.lease.fencing_token)
        self.assertEqual(lease.state, "active")
        connection = self._connection()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM worker_launch_attempts"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM supervised_dispatch_attempts"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM supervised_private_receipt_adoptions"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM task_cancel_requests"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute("PRAGMA foreign_key_check").fetchall(), []
            )
        finally:
            connection.close()

    def test_v10_runtime_with_active_lease_upgrades_in_place_to_v11(self) -> None:
        task_id, _, _ = self._submitted_runtime(key="upgrade-v10-v11")
        acquired = self._acquire("upgrade-v11-owner", lease_seconds=30)
        self.assertTrue(acquired.acquired)
        connection = self._connection()
        try:
            connection.execute(
                "DROP TRIGGER task_cancel_request_blocks_supervised_dispatch"
            )
            connection.execute("DROP TABLE task_cancel_outcomes")
            connection.execute("DROP TABLE supervised_cancel_attempts")
            connection.execute("DROP TABLE task_cancel_requests")
            connection.execute("DELETE FROM schema_migrations WHERE version = 11")
            connection.execute("PRAGMA user_version = 10")
            connection.commit()
        finally:
            connection.close()

        reopened = SQLiteTaskStore(self.database_path)
        self.assertEqual(reopened.migration_version(), 11)
        self.assertEqual(reopened.get_task(task_id).status, "Queued")
        lease = reopened.get_runtime_supervisor_lease(**self.scope)
        self.assertIsNotNone(lease)
        assert lease is not None
        self.assertEqual(lease.fencing_token, acquired.lease.fencing_token)
        self.assertEqual(lease.state, "active")
        connection = self._connection()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM task_cancel_requests"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute("PRAGMA foreign_key_check").fetchall(), []
            )
        finally:
            connection.close()

    def test_cancel_request_attempt_and_outcome_are_append_only(self) -> None:
        task_id, _, runtime, lease, admitted = self._cancellable_runtime(
            key="cancel-immutable"
        )
        cancellation = admitted.snapshot.cancellation
        assert cancellation is not None
        self.assertEqual(cancellation.state, "requested")

        authorization = self.store.authorize_supervised_cancel(
            request_id=cancellation.request_id,
            supervisor_lease=lease,
            supervisor_clock=lambda: NOW,
        )
        self.assertFalse(authorization.replayed)
        completed = runtime.process_runtime_cancellation(
            task_id,
            **self.scope,
            supervisor_lease=lease,
        )
        self.assertEqual(completed.state, "cancelled")

        connection = self._connection()
        try:
            for table, predicate, value in (
                ("task_cancel_requests", "request_id", cancellation.request_id),
                ("supervised_cancel_attempts", "request_id", cancellation.request_id),
                ("task_cancel_outcomes", "request_id", cancellation.request_id),
            ):
                with self.subTest(table=table, operation="update"):
                    with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                        connection.execute(
                            f"UPDATE {table} SET request_id = request_id "
                            f"WHERE {predicate} = ?",
                            (value,),
                        )
                    connection.rollback()
                with self.subTest(table=table, operation="delete"):
                    with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                        connection.execute(
                            f"DELETE FROM {table} WHERE {predicate} = ?",
                            (value,),
                        )
                    connection.rollback()
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM task_cancel_requests"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM supervised_cancel_attempts"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM task_cancel_outcomes"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute("PRAGMA foreign_key_check").fetchall(), []
            )
        finally:
            connection.rollback()
            connection.close()

    def test_cancel_completion_rejects_an_unbound_adapter_proof(self) -> None:
        task_id, _, _, lease, admitted = self._cancellable_runtime(
            key="cancel-reject-empty-proof"
        )
        cancellation = admitted.snapshot.cancellation
        assert cancellation is not None
        self.store.authorize_supervised_cancel(
            request_id=cancellation.request_id,
            supervisor_lease=lease,
            supervisor_clock=lambda: NOW,
        )
        before_sequence = self.store.latest_run_event_sequence(task_id)

        with self.assertRaisesRegex(TaskStoreConflict, "Adapter proof is invalid"):
            self.store.complete_supervised_cancel(
                request_id=cancellation.request_id,
                result="cancel_confirmed",
                terminal_event=None,
                adapter_proof={},
                supervisor_lease=lease,
                supervisor_clock=lambda: NOW,
            )

        self.assertEqual(self.store.get_task(task_id).status, "Queued")
        self.assertEqual(self.store.latest_run_event_sequence(task_id), before_sequence)
        connection = self._connection()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM task_cancel_outcomes"
                ).fetchone()[0],
                0,
            )
        finally:
            connection.close()

    def test_cancel_completion_rejects_a_cross_terminal_adapter_proof(self) -> None:
        task_id, _, _, lease, admitted = self._cancellable_runtime(
            key="cancel-reject-cross-terminal-proof"
        )
        cancellation = admitted.snapshot.cancellation
        assert cancellation is not None
        self.store.authorize_supervised_cancel(
            request_id=cancellation.request_id,
            supervisor_lease=lease,
            supervisor_clock=lambda: NOW,
        )
        intent = self.store.get_dispatch_intent(task_id)
        assert intent is not None and intent.handle is not None
        proof = cancel_adapter_proof(
            task_id=task_id,
            request_id=cancellation.request_id,
            attempt_id=cancellation.attempt_id,
            state="terminal_won",
            terminal_status="Succeeded",
        )
        event = self._terminal_event(
            task_id=task_id,
            intent=intent,
            request_id=cancellation.request_id,
            attempt_id=cancellation.attempt_id,
            terminal_status="Failed",
            proof_hash=proof["proof_hash"],
            event_id="event-cancel-reject-cross-terminal-proof",
        )
        before_sequence = self.store.latest_run_event_sequence(task_id)

        with self.assertRaisesRegex(TaskStoreConflict, "contradicts"):
            self.store.complete_supervised_cancel(
                request_id=cancellation.request_id,
                result="terminal_preempted",
                terminal_event=event,
                adapter_proof=proof,
                supervisor_lease=lease,
                supervisor_clock=lambda: NOW,
            )

        self.assertEqual(self.store.get_task(task_id).status, "Queued")
        self.assertEqual(self.store.latest_run_event_sequence(task_id), before_sequence)
        connection = self._connection()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM task_cancel_outcomes"
                ).fetchone()[0],
                0,
            )
        finally:
            connection.close()

    def test_direct_sql_cancel_outcome_rejects_a_cross_request_proof(self) -> None:
        task_id, _, _, lease, admitted = self._cancellable_runtime(
            key="cancel-reject-direct-cross-proof"
        )
        cancellation = admitted.snapshot.cancellation
        assert cancellation is not None
        self.store.authorize_supervised_cancel(
            request_id=cancellation.request_id,
            supervisor_lease=lease,
            supervisor_clock=lambda: NOW,
        )
        intent = self.store.get_dispatch_intent(task_id)
        assert intent is not None and intent.handle is not None
        proof = cancel_adapter_proof(
            task_id=task_id,
            request_id="cancel-" + "f" * 32,
            attempt_id=cancellation.attempt_id,
            state="cancelled",
            terminal_status="Cancelled",
        )
        event = self._terminal_event(
            task_id=task_id,
            intent=intent,
            request_id=cancellation.request_id,
            attempt_id=cancellation.attempt_id,
            terminal_status="Cancelled",
            proof_hash=proof["proof_hash"],
            event_id="event-cancel-reject-direct-cross-proof",
        )
        event_json, event_hash = encode_document(event)
        _, fingerprint_hash = encode_document(event["fingerprint"])
        proof_json, proof_hash = encode_document(proof)
        outcome = {
            "schema_version": "1.0.0",
            "request_id": cancellation.request_id,
            "task_id": task_id,
            "result": "cancel_confirmed",
            "terminal_status": "Cancelled",
            "adapter_proof": proof,
            "resolved_at": NOW,
            "extensions": {},
        }
        outcome_json, outcome_hash = encode_document(outcome)

        connection = self._connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO run_events(
                    task_id, sequence, event_id, event_type, task_status,
                    node_id, fingerprint_hash, document_json, document_hash,
                    occurred_at, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    event["sequence"],
                    event["event_id"],
                    event["event_type"],
                    event["task_status"],
                    event["node_id"],
                    fingerprint_hash,
                    event_json,
                    event_hash,
                    NOW,
                    NOW,
                ),
            )
            connection.execute(
                "UPDATE tasks SET status = 'Cancelled', updated_at = ? "
                "WHERE task_id = ?",
                (NOW, task_id),
            )
            authorization = connection.execute(
                """
                SELECT authorized_at_us FROM supervised_cancel_attempts
                WHERE request_id = ? AND fencing_token = ?
                """,
                (cancellation.request_id, lease.fencing_token),
            ).fetchone()
            assert authorization is not None
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "cancel outcome requires its exact terminal event",
            ):
                connection.execute(
                    """
                    INSERT INTO task_cancel_outcomes(
                        request_id, task_id, project_id, principal_id,
                        intent_id, attempt_id, result, terminal_status,
                        terminal_event_sequence, adapter_proof_json,
                        adapter_proof_hash, document_json, document_hash,
                        fencing_token, resolved_at, resolved_at_us
                    ) VALUES (?, ?, ?, ?, ?, ?, 'cancel_confirmed',
                              'Cancelled', ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        cancellation.request_id,
                        task_id,
                        PROJECT_ID,
                        PRINCIPAL_ID,
                        cancellation.intent_id,
                        cancellation.attempt_id,
                        event["sequence"],
                        proof_json,
                        proof_hash,
                        outcome_json,
                        outcome_hash,
                        lease.fencing_token,
                        NOW,
                        authorization["authorized_at_us"],
                    ),
                )
            connection.rollback()
            self.assertEqual(
                connection.execute(
                    "SELECT status FROM tasks WHERE task_id = ?", (task_id,)
                ).fetchone()[0],
                "Queued",
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM run_events WHERE event_id = ?",
                    (event["event_id"],),
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM task_cancel_outcomes"
                ).fetchone()[0],
                0,
            )
        finally:
            connection.rollback()
            connection.close()

    def test_direct_sql_cancel_request_rejects_an_older_running_attempt(
        self,
    ) -> None:
        task_id, _, runtime, _ = self._pending_runtime(
            key="cancel-reject-older-attempt"
        )
        acquisition = runtime.acquire_runtime_supervisor_lease(
            **self.scope,
            owner_id="cancel-reject-older-attempt-owner",
            lease_seconds=10,
        )
        self.assertTrue(acquisition.acquired)
        scheduled = runtime.schedule_runtime_dispatch(
            task_id,
            **self.scope,
            supervisor_lease=acquisition.lease,
        )
        self.assertEqual(scheduled.intent.state, "dispatched")
        self.assertIsNotNone(scheduled.intent.handle)

        new_attempt_id = "attempt-" + "b" * 32
        request_id = "cancel-" + "c" * 32
        event_id = "event-direct-sql-older-attempt-cancel"
        connection = self._connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            old_attempt = connection.execute(
                """
                SELECT * FROM worker_launch_attempts
                WHERE intent_id = ? ORDER BY attempt_number ASC LIMIT 1
                """,
                (scheduled.intent.intent_id,),
            ).fetchone()
            self.assertIsNotNone(old_attempt)
            assert old_attempt is not None
            old_attempt_id = old_attempt["attempt_id"]
            connection.execute(
                """
                INSERT INTO worker_launch_attempts(
                    attempt_id, intent_id, task_id, project_id, principal_id,
                    attempt_number, submission_id, job_id,
                    adapter_request_hash, binding_hash, created_at,
                    first_fencing_token, first_observed_at,
                    first_observed_at_us
                )
                SELECT ?, intent_id, task_id, project_id, principal_id,
                       attempt_number + 1, submission_id, job_id,
                       adapter_request_hash, ?, created_at,
                       first_fencing_token, first_observed_at,
                       first_observed_at_us
                FROM worker_launch_attempts WHERE attempt_id = ?
                """,
                (
                    new_attempt_id,
                    "sha256:" + "d" * 64,
                    old_attempt_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO worker_attempt_observations(
                    attempt_id, observation_sequence, ticket_state,
                    capacity_slot, capacity_generation, ticket_worker_pid,
                    ticket_updated_at, ticket_record_hash,
                    ready_worker_pid, ready_started_at, ready_record_hash,
                    heartbeat_sequence, heartbeat_state,
                    heartbeat_updated_at, heartbeat_record_hash,
                    document_json, document_hash, project_id, principal_id,
                    fencing_token, observed_at, observed_at_us
                )
                SELECT ?, 1, ticket_state,
                       capacity_slot, capacity_generation, ticket_worker_pid,
                       ticket_updated_at, ticket_record_hash,
                       ready_worker_pid, ready_started_at, ready_record_hash,
                       heartbeat_sequence, heartbeat_state,
                       heartbeat_updated_at, heartbeat_record_hash,
                       document_json, document_hash, project_id, principal_id,
                       fencing_token, observed_at, observed_at_us
                FROM worker_attempt_observations
                WHERE attempt_id = ?
                ORDER BY observation_sequence DESC LIMIT 1
                """,
                (new_attempt_id, old_attempt_id),
            )

            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "cancel request requires an exact running attempt",
            ):
                self._insert_direct_cancel_request(
                    connection,
                    task_id=task_id,
                    intent=scheduled.intent,
                    attempt_id=old_attempt_id,
                    request_id=request_id,
                    event_id=event_id,
                )
            connection.rollback()
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM task_cancel_requests WHERE request_id = ?",
                    (request_id,),
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM run_events WHERE event_id = ?",
                    (event_id,),
                ).fetchone()[0],
                0,
            )
        finally:
            connection.rollback()
            connection.close()

    def test_direct_sql_cancel_request_rejects_historical_algorithm_binding(
        self,
    ) -> None:
        task_id, _, runtime, _ = self._pending_runtime(
            key="cancel-reject-historical-algorithm"
        )
        acquisition = runtime.acquire_runtime_supervisor_lease(
            **self.scope,
            owner_id="cancel-reject-historical-algorithm-owner",
            lease_seconds=10,
        )
        self.assertTrue(acquisition.acquired)
        scheduled = runtime.schedule_runtime_dispatch(
            task_id,
            **self.scope,
            supervisor_lease=acquisition.lease,
        )
        self.assertEqual(scheduled.intent.state, "dispatched")
        request_id = "cancel-" + "e" * 32
        event_id = "event-direct-sql-historical-algorithm-cancel"

        connection = self._connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            attempt_id = connection.execute(
                """
                SELECT attempt_id FROM worker_launch_attempts
                WHERE intent_id = ? ORDER BY attempt_number DESC LIMIT 1
                """,
                (scheduled.intent.intent_id,),
            ).fetchone()[0]
            # Build the impossible cross-version fixture without weakening the
            # production schema: the DROP and tamper are rolled back below.
            connection.execute("DROP TRIGGER dispatch_intents_are_immutable")
            connection.execute(
                """
                UPDATE dispatch_intents
                SET request_json = json_set(
                    request_json, '$.request.algorithm.version', '1.3.0'
                )
                WHERE intent_id = ?
                """,
                (scheduled.intent.intent_id,),
            )
            binding = connection.execute(
                """
                SELECT adapter_version,
                       json_extract(
                           request_json, '$.request.algorithm.version'
                       ) AS algorithm_version
                FROM dispatch_intents WHERE intent_id = ?
                """,
                (scheduled.intent.intent_id,),
            ).fetchone()
            self.assertEqual(
                (binding["algorithm_version"], binding["adapter_version"]),
                ("1.3.0", "1.4.0"),
            )
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "cancel request requires an exact running attempt",
            ):
                self._insert_direct_cancel_request(
                    connection,
                    task_id=task_id,
                    intent=scheduled.intent,
                    attempt_id=attempt_id,
                    request_id=request_id,
                    event_id=event_id,
                )
            connection.rollback()
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM task_cancel_requests WHERE request_id = ?",
                    (request_id,),
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM run_events WHERE event_id = ?",
                    (event_id,),
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type = 'trigger'
                      AND name = 'dispatch_intents_are_immutable'
                    """
                ).fetchone()[0],
                1,
            )
        finally:
            connection.rollback()
            connection.close()

    def test_cancel_authorization_replays_only_inside_the_active_term(self) -> None:
        _, _, _, old_lease, admitted = self._cancellable_runtime(
            key="cancel-active-term"
        )
        cancellation = admitted.snapshot.cancellation
        assert cancellation is not None

        first = self.store.authorize_supervised_cancel(
            request_id=cancellation.request_id,
            supervisor_lease=old_lease,
            supervisor_clock=lambda: NOW,
        )
        self.assertFalse(first.replayed)
        replay = self.store.authorize_supervised_cancel(
            request_id=cancellation.request_id,
            supervisor_lease=old_lease,
            supervisor_clock=lambda: T_PLUS_1,
        )
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.authorized_at, first.authorized_at)

        self.store.release_runtime_supervisor_lease(
            lease=old_lease,
            clock=lambda: T_PLUS_1,
        )
        with self.assertRaises(RuntimeSupervisorLeaseLost):
            self.store.authorize_supervised_cancel(
                request_id=cancellation.request_id,
                supervisor_lease=old_lease,
                supervisor_clock=lambda: T_PLUS_5,
            )

        next_term = self._acquire(
            "cancel-active-term-takeover",
            now=T_PLUS_5,
            lease_seconds=10,
        )
        self.assertTrue(next_term.acquired)
        self.assertGreater(
            next_term.lease.fencing_token,
            old_lease.fencing_token,
        )
        takeover = self.store.authorize_supervised_cancel(
            request_id=cancellation.request_id,
            supervisor_lease=next_term.lease,
            supervisor_clock=lambda: T_PLUS_5,
        )
        self.assertFalse(takeover.replayed)

        connection = self._connection()
        try:
            attempts = connection.execute(
                """
                SELECT fencing_token, action
                FROM supervised_cancel_attempts
                WHERE request_id = ?
                ORDER BY fencing_token
                """,
                (cancellation.request_id,),
            ).fetchall()
            self.assertEqual(
                [row["fencing_token"] for row in attempts],
                [old_lease.fencing_token, next_term.lease.fencing_token],
            )
            self.assertEqual(
                {row["action"] for row in attempts},
                {"deliver_exact_attempt_cancel"},
            )
        finally:
            connection.close()

    def test_supervised_dispatch_authorization_claims_replays_and_is_immutable(
        self,
    ) -> None:
        task_id, _, _, pending = self._pending_runtime(key="dispatch-authorize")
        acquisition = self._acquire("dispatch-authorize-owner", lease_seconds=30)

        authorized = self.store.authorize_supervised_dispatch(
            intent_id=pending.intent_id,
            reason="pending_first_dispatch",
            supervisor_lease=acquisition.lease,
            supervisor_clock=lambda: NOW,
        )
        self.assertFalse(authorized.replayed)
        self.assertEqual(authorized.intent.state, "dispatching")
        self.assertEqual(authorized.reason, "pending_first_dispatch")
        self.assertEqual(
            authorized.fencing_token, acquisition.lease.fencing_token
        )

        replay = self.store.authorize_supervised_dispatch(
            intent_id=pending.intent_id,
            reason="pending_first_dispatch",
            supervisor_lease=acquisition.lease,
            supervisor_clock=lambda: T_PLUS_1,
        )
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.authorized_at, authorized.authorized_at)
        self.assertEqual(replay.intent.state, "dispatching")
        with self.assertRaises(RuntimeSupervisorLeaseLost):
            self.store.authorize_supervised_dispatch(
                intent_id=pending.intent_id,
                reason="dispatching_no_record_takeover",
                supervisor_lease=acquisition.lease,
                supervisor_clock=lambda: T_PLUS_30,
            )

        connection = self._connection()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM dispatch_attempts"
                ).fetchone()[0],
                1,
            )
            audit = connection.execute(
                """
                SELECT project_id, principal_id, fencing_token, reason,
                       authorized_at, authorized_at_us
                FROM supervised_dispatch_attempts WHERE intent_id = ?
                """,
                (pending.intent_id,),
            ).fetchone()
            self.assertEqual(audit["project_id"], PROJECT_ID)
            self.assertEqual(audit["principal_id"], PRINCIPAL_ID)
            self.assertEqual(
                audit["fencing_token"], acquisition.lease.fencing_token
            )
            self.assertEqual(audit["reason"], "pending_first_dispatch")
            self.assertEqual(
                audit["authorized_at"], "2026-07-15T03:00:00.000000Z"
            )
            self.assertEqual(audit["authorized_at_us"], 1784084400000000)

            with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                connection.execute(
                    """
                    UPDATE supervised_dispatch_attempts
                    SET reason = 'staged_attempt_resume'
                    WHERE intent_id = ?
                    """,
                    (pending.intent_id,),
                )
            connection.rollback()
            with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                connection.execute(
                    "DELETE FROM supervised_dispatch_attempts WHERE intent_id = ?",
                    (pending.intent_id,),
                )
        finally:
            connection.rollback()
            connection.close()

        self.assertEqual(
            self.store.get_dispatch_intent(task_id).state, "dispatching"
        )

    def test_direct_sql_cannot_mislabel_an_older_claim_as_pending(self) -> None:
        _, _, _, pending = self._pending_runtime(key="dispatch-reason-trigger")
        claimed, claimed_now = self.store.claim_dispatch(
            intent_id=pending.intent_id,
            now=NOW,
        )
        self.assertTrue(claimed_now)
        self.assertEqual(claimed.state, "dispatching")
        acquisition = self._acquire(
            "dispatch-reason-trigger-owner", lease_seconds=30
        )

        connection = self._connection()
        try:
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "pending dispatch requires its atomic claim",
            ):
                connection.execute(
                    """
                    INSERT INTO supervised_dispatch_attempts(
                        intent_id, project_id, principal_id, fencing_token,
                        reason, authorized_at, authorized_at_us
                    ) VALUES (?, ?, ?, ?, 'pending_first_dispatch', ?, ?)
                    """,
                    (
                        pending.intent_id,
                        PROJECT_ID,
                        PRINCIPAL_ID,
                        acquisition.lease.fencing_token,
                        T_PLUS_1,
                        1784084401000000,
                    ),
                )
        finally:
            connection.rollback()
            connection.close()

    def test_concurrent_supervised_pending_claim_has_one_audit_row(self) -> None:
        _, _, _, pending = self._pending_runtime(key="dispatch-concurrent")
        acquisition = self._acquire("dispatch-concurrent-owner", lease_seconds=30)
        callers = 8
        barrier = threading.Barrier(callers)

        def authorize(_index: int):
            barrier.wait(timeout=5)
            return self.store.authorize_supervised_dispatch(
                intent_id=pending.intent_id,
                reason="pending_first_dispatch",
                supervisor_lease=acquisition.lease,
                supervisor_clock=lambda: NOW,
            )

        with ThreadPoolExecutor(max_workers=callers) as executor:
            results = list(executor.map(authorize, range(callers)))

        self.assertEqual(sum(not result.replayed for result in results), 1)
        self.assertTrue(all(result.intent.state == "dispatching" for result in results))
        connection = self._connection()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM dispatch_attempts"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM supervised_dispatch_attempts"
                ).fetchone()[0],
                1,
            )
        finally:
            connection.close()

    def test_supervised_pending_claim_rolls_back_if_audit_insert_fails(
        self,
    ) -> None:
        _, dispatcher, _, pending = self._pending_runtime(
            key="dispatch-audit-rollback"
        )
        acquisition = self._acquire(
            "dispatch-audit-rollback-owner", lease_seconds=30
        )
        connection = self._connection()
        try:
            connection.execute(
                """
                CREATE TRIGGER test_reject_supervised_dispatch_audit
                BEFORE INSERT ON supervised_dispatch_attempts
                BEGIN
                    SELECT RAISE(ABORT, 'synthetic audit failure');
                END
                """
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(TaskStoreConflict):
            self.store.authorize_supervised_dispatch(
                intent_id=pending.intent_id,
                reason="pending_first_dispatch",
                supervisor_lease=acquisition.lease,
                supervisor_clock=lambda: NOW,
            )
        self.assertEqual(
            self.store.get_dispatch_intent(pending.task_id).state, "pending"
        )
        self.assertEqual(dispatcher.dispatch_calls, 0)
        connection = self._connection()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM dispatch_attempts"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM supervised_dispatch_attempts"
                ).fetchone()[0],
                0,
            )
        finally:
            connection.close()

    def test_expired_authorization_rolls_back_and_new_term_can_take_over(
        self,
    ) -> None:
        _, _, _, pending = self._pending_runtime(key="dispatch-takeover")
        expired = self._acquire("dispatch-expired-owner", lease_seconds=10)

        with self.assertRaises(RuntimeSupervisorLeaseLost):
            self.store.authorize_supervised_dispatch(
                intent_id=pending.intent_id,
                reason="pending_first_dispatch",
                supervisor_lease=expired.lease,
                supervisor_clock=lambda: T_PLUS_10,
            )
        self.assertEqual(
            self.store.get_dispatch_intent(pending.task_id).state, "pending"
        )
        connection = self._connection()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM dispatch_attempts"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM supervised_dispatch_attempts"
                ).fetchone()[0],
                0,
            )
        finally:
            connection.close()

        first_active = self._acquire(
            "dispatch-first-active", now=T_PLUS_10, lease_seconds=10
        )
        first_authorization = self.store.authorize_supervised_dispatch(
            intent_id=pending.intent_id,
            reason="pending_first_dispatch",
            supervisor_lease=first_active.lease,
            supervisor_clock=lambda: T_PLUS_10,
        )
        self.assertEqual(first_authorization.intent.state, "dispatching")

        takeover = self._acquire(
            "dispatch-takeover-owner", now=T_PLUS_20, lease_seconds=10
        )
        with self.assertRaises(RuntimeSupervisorLeaseLost):
            self.store.authorize_supervised_dispatch(
                intent_id=pending.intent_id,
                reason="dispatching_no_record_takeover",
                supervisor_lease=first_active.lease,
                supervisor_clock=lambda: T_PLUS_20,
            )
        with self.assertRaises(TaskStoreConflict):
            self.store.authorize_supervised_dispatch(
                intent_id=pending.intent_id,
                reason="staged_attempt_resume",
                supervisor_lease=takeover.lease,
                supervisor_clock=lambda: T_PLUS_20,
            )
        recovered = self.store.authorize_supervised_dispatch(
            intent_id=pending.intent_id,
            reason="dispatching_no_record_takeover",
            supervisor_lease=takeover.lease,
            supervisor_clock=lambda: T_PLUS_20,
        )
        self.assertFalse(recovered.replayed)
        self.assertEqual(recovered.intent.state, "dispatching")
        self.assertEqual(recovered.reason, "dispatching_no_record_takeover")

        connection = self._connection()
        try:
            rows = connection.execute(
                """
                SELECT fencing_token, reason
                FROM supervised_dispatch_attempts
                WHERE intent_id = ? ORDER BY fencing_token
                """,
                (pending.intent_id,),
            ).fetchall()
            self.assertEqual(
                [(row["fencing_token"], row["reason"]) for row in rows],
                [
                    (
                        first_active.lease.fencing_token,
                        "pending_first_dispatch",
                    ),
                    (
                        takeover.lease.fencing_token,
                        "dispatching_no_record_takeover",
                    ),
                ],
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM dispatch_attempts"
                ).fetchone()[0],
                1,
            )
        finally:
            connection.close()

    def test_private_receipt_adoption_is_fenced_atomic_and_immutable(self) -> None:
        _, dispatcher, _, pending = self._pending_runtime(
            key="private-receipt-adoption"
        )
        claimed, claimed_now = self.store.claim_dispatch(
            intent_id=pending.intent_id,
            now=NOW,
        )
        self.assertTrue(claimed_now)
        handle = dispatcher.recover_existing_receipt(claimed)
        acquisition = self._acquire(
            "private-receipt-adoption-owner", lease_seconds=30
        )
        connection = self._connection()
        try:
            connection.execute(
                """
                CREATE TRIGGER test_reject_private_receipt_audit
                BEFORE INSERT ON supervised_private_receipt_adoptions
                BEGIN
                    SELECT RAISE(ABORT, 'synthetic private audit failure');
                END
                """
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(TaskStoreConflict):
            self.store.record_supervised_private_receipt_adoption(
                intent_id=pending.intent_id,
                handle=handle,
                private_schema_version="1.0.0",
                receipt_record_hash="sha256:" + "b" * 64,
                supervisor_lease=acquisition.lease,
                supervisor_clock=lambda: NOW,
            )
        self.assertEqual(
            self.store.get_dispatch_intent(pending.task_id).state,
            "dispatching",
        )
        connection = self._connection()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM dispatch_outcomes"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM supervised_private_receipt_adoptions"
                ).fetchone()[0],
                0,
            )
            connection.execute("DROP TRIGGER test_reject_private_receipt_audit")
            connection.commit()
        finally:
            connection.close()

        adopted = self.store.record_supervised_private_receipt_adoption(
            intent_id=pending.intent_id,
            handle=handle,
            private_schema_version="1.0.0",
            receipt_record_hash="sha256:" + "b" * 64,
            supervisor_lease=acquisition.lease,
            supervisor_clock=lambda: NOW,
        )
        self.assertTrue(adopted.adopted)
        self.assertFalse(adopted.replayed)
        self.assertEqual(adopted.intent.state, "dispatched")
        replay = self.store.record_supervised_private_receipt_adoption(
            intent_id=pending.intent_id,
            handle=handle,
            private_schema_version="1.0.0",
            receipt_record_hash="sha256:" + "b" * 64,
            supervisor_lease=acquisition.lease,
            supervisor_clock=lambda: T_PLUS_1,
        )
        self.assertFalse(replay.adopted)
        self.assertTrue(replay.replayed)

        connection = self._connection()
        try:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                connection.execute(
                    """
                    UPDATE supervised_private_receipt_adoptions
                    SET receipt_record_hash = ? WHERE intent_id = ?
                    """,
                    ("sha256:" + "c" * 64, pending.intent_id),
                )
            connection.rollback()
            with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                connection.execute(
                    """
                    DELETE FROM supervised_private_receipt_adoptions
                    WHERE intent_id = ?
                    """,
                    (pending.intent_id,),
                )
        finally:
            connection.rollback()
            connection.close()

    def test_stale_supervisor_term_cannot_project_or_adopt_worker(self) -> None:
        task_id, dispatcher, runtime = self._submitted_runtime(
            key="stale-worker-projection", deferred=True
        )
        intent = self.store.get_dispatch_intent(task_id)
        self.assertIsNotNone(intent)
        assert intent is not None
        dispatcher.worker_observation = {
            "evidence": managed_worker_evidence(),
            "handle": dispatcher.recover_existing_receipt(intent),
        }
        old = self._acquire("worker-owner-old", lease_seconds=10)
        self.now[0] = T_PLUS_11
        current = self._acquire(
            "worker-owner-current", now=T_PLUS_11, lease_seconds=10
        )
        self.assertTrue(current.acquired)
        with self.assertRaises(TaskSupervisorLeaseLost):
            runtime.project_worker_attempt(
                task_id, supervisor_lease=old.lease, **self.scope
            )
        connection = self._connection()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM worker_launch_attempts"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM dispatch_outcomes"
                ).fetchone()[0],
                0,
            )
        finally:
            connection.close()

        projected = runtime.project_worker_attempt(
            task_id, supervisor_lease=current.lease, **self.scope
        )
        self.assertTrue(projected.adopted)
        self.assertEqual(projected.intent.state, "dispatched")

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
