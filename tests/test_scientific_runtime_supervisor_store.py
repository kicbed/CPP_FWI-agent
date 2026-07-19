from __future__ import annotations

import copy
import hashlib
import shutil
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from scientific_runtime import task_store as task_store_module
from scientific_runtime.fwi_registry import load_deepwave_manifest
from scientific_runtime.registry_service import RegistryService
from scientific_runtime.task_service import (
    TaskConflict,
    TaskService,
    TaskSupervisorLeaseLost,
)
from scientific_runtime.task_store import (
    RuntimeSupervisorLeaseLost,
    SQLiteTaskStore,
    TaskStoreConflict,
    TaskStoreCorruption,
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
    WorkerExitRetryFakeDispatcher,
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
CHECKPOINT_AT = "2026-07-15T03:00:01.000000Z"
RESUME_ACK_AT = "2026-07-15T03:00:10.000000Z"


def checkpoint_adapter_proof(
    *,
    task_id: str,
    node_id: str,
    submission_id: str,
    attempt_id: str,
    attempt_number: int,
    binding_hash: str,
    ready_record_hash: str,
    state: str = "waiting",
    checkpoint_proof_hash: str | None = None,
    resume_id: str | None = None,
    resume_request_record_hash: str | None = None,
) -> dict:
    payload = {
        "schema_version": "1.0.0",
        "task_id": task_id,
        "node_id": node_id,
        "submission_id": submission_id,
        "attempt_id": attempt_id,
        "attempt_number": attempt_number,
        "checkpoint_id": "checkpoint-" + "a" * 32,
        "checkpoint_index": 1,
        "completed_updates": 1,
        "binding_hash": binding_hash,
        "submission_receipt_record_hash": "sha256:" + "1" * 64,
        "ready_record_hash": ready_record_hash,
        "checkpoint_manifest_relative_path": (
            "checkpoints/checkpoint-" + "a" * 32 + "/manifest.json"
        ),
        "checkpoint_manifest_size_bytes": 512,
        "checkpoint_manifest_hash": "sha256:" + "2" * 64,
        "checkpoint_receipt_record_hash": "sha256:" + "3" * 64,
        "checkpoint_created_at": CHECKPOINT_AT,
        "state": state,
        "checkpoint_proof_hash": checkpoint_proof_hash,
        "resume_id": resume_id,
        "resume_request_record_hash": resume_request_record_hash,
        "resume_acknowledgement_record_hash": (
            "sha256:" + "4" * 64 if state == "resumed" else None
        ),
        "resume_acknowledged_at": (
            RESUME_ACK_AT if state == "resumed" else None
        ),
    }
    return {**payload, "proof_hash": encode_document(payload)[1]}


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


def timeout_capability_proof(*, attempt_id: str, binding_hash: str) -> dict:
    payload = {
        "schema_version": "2.0.0",
        "private_schema_version": "1.1.0",
        "attempt_id": attempt_id,
        "binding_hash": binding_hash,
        "capability_record_hash": "sha256:" + "8" * 64,
        "supported_reasons": ["user_requested", "wall_time_exceeded"],
    }
    return {**payload, "proof_hash": encode_document(payload)[1]}


def timeout_adapter_proof(
    *,
    timeout,
    state: str,
    terminal_status: str,
    terminal_failure_code: str | None,
    ready_record_hash: str,
) -> dict:
    confirmed = state == "timed_out"
    payload = {
        "schema_version": "1.0.0",
        "task_id": timeout.task_id,
        "request_id": timeout.timeout_id,
        "reason": "wall_time_exceeded",
        "state": state,
        "code": "TIMEOUT_COMPLETED" if confirmed else "TIMEOUT_TERMINAL_WON",
        "attempt_id": timeout.attempt_id,
        "wall_time_seconds": timeout.wall_time_seconds,
        "started_at": timeout.started_at,
        "deadline_at": timeout.deadline_at,
        "ready_record_hash": ready_record_hash,
        "capability_record_hash": "sha256:" + "8" * 64,
        "request_record_hash": "sha256:" + "9" * 64 if confirmed else None,
        "acknowledgement_record_hash": (
            "sha256:" + "a" * 64 if confirmed else None
        ),
        "terminal_status": terminal_status,
        "terminal_failure_code": terminal_failure_code,
        "local_run_state": "retained",
        "replayed": False,
        "receipt_record_hash": "sha256:" + "b" * 64,
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
        self.registry.register_algorithm(manifest=load_deepwave_manifest("1.4.0"))
        self.registry.register_algorithm(manifest=load_deepwave_manifest("1.5.0"))
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

    def test_worker_evidence_timestamp_has_one_rfc3339_spelling_set(
        self,
    ) -> None:
        for accepted in (
            "2026-07-15T03:00:00Z",
            "2026-07-15T03:00:00.123Z",
            "2026-07-15T03:00:00.123456Z",
        ):
            task_store_module._worker_evidence_timestamp(accepted)
        for rejected in (
            "2026-07-15 03:00:00Z",
            "2026-07-15t03:00:00Z",
            "2026-07-15X03:00:00Z",
            "2026-07-15T03:00Z",
            "2026-07-15T03Z",
            "2026-07-15T03:00:00.1Z",
        ):
            with self.subTest(timestamp=rejected):
                with self.assertRaisesRegex(
                    TaskStoreConflict, "Worker evidence timestamp is invalid"
                ):
                    task_store_module._worker_evidence_timestamp(rejected)

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _drop_retry_schema(connection: sqlite3.Connection) -> None:
        connection.execute("DROP VIEW effective_dispatched_intents")
        for trigger in (
            "worker_exit_retry_reservation_requires_exact_case",
            "worker_exit_retry_reservation_requires_active_term",
            "worker_exit_retry_timeout_retirement_requires_exact_window",
            "worker_exit_retry_reservation_retires_timeout",
            "supervised_worker_exit_retry_attempt_requires_active_term",
            "worker_launch_attempt_requires_retry_reservation",
            "worker_exit_retry_replacement_requires_exact_case",
            "worker_exit_retry_replacement_requires_active_term",
            "worker_exit_retry_exhaustion_requires_exact_case",
            "worker_exit_retry_reservations_are_immutable",
            "worker_exit_retry_reservations_cannot_be_deleted",
            "supervised_worker_exit_retry_attempts_are_immutable",
            "supervised_worker_exit_retry_attempts_cannot_be_deleted",
            "worker_exit_retry_timeout_retirements_are_immutable",
            "worker_exit_retry_timeout_retirements_cannot_be_deleted",
            "worker_exit_retry_dispatch_replacements_are_immutable",
            "worker_exit_retry_dispatch_replacements_cannot_be_deleted",
            "worker_exit_retry_exhaustions_are_immutable",
            "worker_exit_retry_exhaustions_cannot_be_deleted",
            "task_cancel_request_requires_exact_running_attempt",
            "worker_attempt_timeout_window_requires_exact_start",
            "supervised_timeout_attempt_requires_due_window",
        ):
            connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        connection.execute("DROP TABLE worker_exit_retry_exhaustions")
        connection.execute("DROP TABLE worker_exit_retry_dispatch_replacements")
        connection.execute("DROP TABLE worker_exit_retry_timeout_retirements")
        connection.execute("DROP TABLE supervised_worker_exit_retry_attempts")
        connection.execute("DROP TABLE worker_exit_retry_reservations")
        for trigger in (
            "approvals_initialize_retry_budget",
            "approval_retry_budgets_are_immutable",
            "approval_retry_budgets_cannot_be_deleted",
            "worker_retry_reservation_requires_exact_case",
            "worker_retry_reservation_requires_active_term",
            "supervised_retry_attempt_requires_active_term",
            "worker_launch_attempt_requires_retry_reservation",
            "worker_launch_attempt_rejects_attempt_three",
            "worker_retry_reservations_are_immutable",
            "worker_retry_reservations_cannot_be_deleted",
            "supervised_retry_attempts_are_immutable",
            "supervised_retry_attempts_cannot_be_deleted",
            "worker_retry_exhaustion_requires_exact_case",
            "worker_retry_exhaustions_are_immutable",
            "worker_retry_exhaustions_cannot_be_deleted",
        ):
            connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        connection.execute("DROP TABLE worker_retry_exhaustions")
        connection.execute("DROP TABLE supervised_retry_attempts")
        connection.execute("DROP TABLE worker_retry_reservations")
        connection.execute("DROP TABLE approval_retry_budgets")

    @staticmethod
    def _drop_negative_reconciliation_schema(
        connection: sqlite3.Connection,
    ) -> None:
        for trigger in (
            "dispatch_reconciliation_observation_requires_exact_case",
            "dispatch_reconciliation_observation_requires_active_term",
            "dispatch_reconciliation_observation_sequence_is_contiguous",
            "dispatch_reconciliation_negative_requires_exact_case",
            "dispatch_reconciliation_negative_requires_active_term",
            "dispatch_reconciliation_observations_are_immutable",
            "dispatch_reconciliation_observations_cannot_be_deleted",
            "dispatch_reconciliation_negative_resolutions_are_immutable",
            "dispatch_reconciliation_negative_resolutions_cannot_be_deleted",
        ):
            connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        connection.execute(
            "DROP TABLE dispatch_reconciliation_negative_resolutions"
        )
        connection.execute("DROP TABLE dispatch_reconciliation_observations")

    @staticmethod
    def _drop_checkpoint_schema(connection: sqlite3.Connection) -> None:
        for trigger in (
            "worker_attempt_waiting_requires_checkpoint_capable_intent",
            "worker_checkpoint_wait_requires_active_term",
            "worker_checkpoint_wait_requires_exact_live_attempt",
            "checkpoint_resume_request_requires_current_wait",
            "checkpoint_resume_authorization_requires_active_term",
            "checkpoint_resume_authorization_requires_current_wait",
            "checkpoint_resume_authorization_reuses_worker_request",
            "checkpoint_resume_outcome_requires_active_term",
            "checkpoint_resume_outcome_requires_exact_ack",
            "worker_checkpoint_waits_are_append_only",
            "worker_checkpoint_waits_cannot_be_deleted",
            "checkpoint_resume_requests_are_append_only",
            "checkpoint_resume_requests_cannot_be_deleted",
            "checkpoint_resume_authorizations_are_append_only",
            "checkpoint_resume_authorizations_cannot_be_deleted",
            "checkpoint_resume_outcomes_are_append_only",
            "checkpoint_resume_outcomes_cannot_be_deleted",
        ):
            connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        connection.execute("DROP TABLE task_checkpoint_resume_outcomes")
        connection.execute(
            "DROP TABLE supervised_checkpoint_resume_authorizations"
        )
        connection.execute("DROP TABLE task_checkpoint_resume_requests")
        connection.execute("DROP TABLE worker_checkpoint_waits")

    @staticmethod
    def _drop_dag_schema(connection: sqlite3.Connection) -> None:
        trigger_names = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'trigger' AND name LIKE 'dag_node_%'"
        ).fetchall()
        for (trigger_name,) in trigger_names:
            connection.execute(f'DROP TRIGGER "{trigger_name}"')
        connection.execute("DROP TABLE dag_node_cache_hit_facts")
        connection.execute("DROP TABLE dag_node_cache_entries")
        connection.execute("DROP TABLE dag_node_scheduler_transition_facts")
        connection.execute("DROP TABLE dag_task_execution_runs")
        connection.execute("DROP TABLE dag_node_terminal_facts")
        connection.execute("DROP TABLE dag_node_execution_transition_facts")
        connection.execute("DROP TABLE dag_node_execution_admissions")
        connection.execute("DROP TABLE dag_node_succeeded_outputs")
        connection.execute("DROP TABLE dag_node_input_binding_facts")
        connection.execute("DROP TABLE dag_node_claim_candidates")
        connection.execute("DROP TABLE dag_node_state_events")

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
        self,
        *,
        key: str,
        deferred: bool = False,
        wall_time_seconds: int | None = None,
        algorithm_version: str = "1.6.0",
        dispatcher: FakeDispatcher | None = None,
    ):
        token = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
        draft = optimizer_task_draft(algorithm_version=algorithm_version)
        draft["draft_id"] = f"draft-{token}"
        if wall_time_seconds is not None:
            draft["resources"]["wall_time_seconds"] = wall_time_seconds
        created = self.service.create_task(
            draft=draft,
            idempotency_key=f"create-{key}",
            **self.scope,
        )
        task_id = created.snapshot.task_id

        plan = optimizer_plan_graph(algorithm_version=algorithm_version)
        plan["nodes"][0]["outputs"] = copy.deepcopy(
            load_deepwave_manifest(algorithm_version)["outputs"]
        )
        plan["plan_id"] = f"plan-{token}"
        plan["draft"] = {
            "draft_id": draft["draft_id"],
            "revision": created.snapshot.draft["revision"],
        }
        plan["nodes"][0]["idempotency_key"] = f"node-{token}-submit"
        if wall_time_seconds is not None:
            plan["nodes"][0]["resources"][
                "wall_time_seconds"
            ] = wall_time_seconds
        plan["plan_hash"] = compute_plan_hash(plan)
        self.service.persist_plan(task_id=task_id, plan=plan, **self.scope)
        approval = executable_approval_decision(plan)
        if algorithm_version not in {"1.5.0", "1.6.0"}:
            # Historical bindings remain readable, but ApprovalDecision 1.1
            # grants the finite retry budget only to the current 1.5 pair.
            approval["schema_version"] = "1.0.0"
            approval["scope"].pop("retry_policy")
        approval["approval_id"] = f"approval-{token}"
        self.service.persist_approval(
            task_id=task_id,
            approval=approval,
            **self.scope,
        )

        if dispatcher is None:
            dispatcher = FakeDispatcher(
                self.store,
                failure_code=(
                    "ADAPTER_CONCURRENCY_LIMIT" if deferred else None
                ),
                adapter_version=algorithm_version,
            )
        dispatcher.defer_dispatch = deferred
        runtime = TaskService(
            self.store,
            clock=lambda: self.now[0],
            dispatcher=dispatcher,
        )
        submit_arguments = {
            "task_id": task_id,
            "approval_id": approval["approval_id"],
            "idempotency_key": f"submit-{key}",
            **self.scope,
        }
        if algorithm_version == "1.6.0":
            submitted = runtime.submit_task(**submit_arguments)
        else:
            # Reconstruct a receipt admitted while 1.4 was the fixed current
            # binding so the current Store's read-only compatibility path is
            # exercised without weakening production admission.
            runtime._p1_manifest = load_deepwave_manifest(algorithm_version)
            with mock.patch(
                "scientific_runtime.task_service.DEEPWAVE_ALGORITHM_VERSION",
                algorithm_version,
            ):
                submitted = runtime.submit_task(**submit_arguments)
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

    def _reconciliation_runtime(
        self, *, key: str, algorithm_version: str = "1.6.0"
    ):
        task_id, dispatcher, runtime, intent = self._pending_runtime(
            key=key, algorithm_version=algorithm_version
        )
        claimed, claimed_now = self.store.claim_dispatch(
            intent_id=intent.intent_id,
            now=NOW,
        )
        self.assertTrue(claimed_now)
        reconciled = self.store.record_dispatch_reconciliation(
            intent_id=claimed.intent_id,
            failure_code="SUBMISSION_RECONCILIATION_REQUIRED",
            now=NOW,
        )
        self.assertEqual(reconciled.state, "reconciliation_required")
        self.assertIsNotNone(reconciled.reconciliation)
        return task_id, dispatcher, runtime, reconciled

    def test_historical_waiting_observation_is_rejected_before_persistence(
        self,
    ) -> None:
        task_id, dispatcher, runtime, _ = self._pending_runtime(
            key="historical-waiting-store-boundary",
            algorithm_version="1.4.0",
        )
        dispatcher.first_dispatch_heartbeat_state = "waiting"
        lease = self._acquire(
            "historical-waiting-store-owner", lease_seconds=30
        ).lease
        try:
            with self.assertRaisesRegex(TaskConflict, "exact 1.6"):
                runtime.schedule_runtime_dispatch(
                    task_id,
                    supervisor_lease=lease,
                    **self.scope,
                )
        finally:
            runtime.release_runtime_supervisor_lease(lease)
        connection = self._connection()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM worker_attempt_observations"
                ).fetchone()[0],
                0,
            )
        finally:
            connection.close()
        self.assertIsNone(self.store.get_task_cancel_candidate(task_id))

    def _negative_reconciliation_inputs(
        self,
        intent,
        *,
        attempt_id: str | None = None,
    ) -> dict:
        if attempt_id is None:
            attempt_id = "attempt-" + hashlib.sha256(
                intent.intent_id.encode("utf-8")
            ).hexdigest()[:32]
        evidence = managed_worker_evidence(
            ticket_state="failed",
            attempt_id=attempt_id,
        )
        adapter_version = intent.adapter_version
        private_schema_version = {
            "1.4.0": "1.1.0",
            "1.5.0": "1.2.0",
            "1.6.0": "1.2.0",
        }[adapter_version]
        private_record_hash = "sha256:" + hashlib.sha256(
            (intent.intent_id + "\x1fprivate-negative-proof").encode("utf-8")
        ).hexdigest()
        evidence_hash = encode_document(evidence)[1]
        proof_payload = {
            "schema_version": "1.0.0",
            "result": "not_dispatched",
            "evidence_kind": "managed_pre_running_failure",
            "adapter_version": adapter_version,
            "private_schema_version": private_schema_version,
            "private_record_hash": private_record_hash,
            "attempt_id": attempt_id,
            "attempt_number": 1,
            "evidence_hash": evidence_hash,
        }
        private_proof_hash = encode_document(proof_payload)[1]
        sequence = self.store.latest_run_event_sequence(intent.task_id) + 1
        identity = {
            "intent_id": intent.intent_id,
            "attempt_id": attempt_id,
            "evidence_hash": evidence_hash,
            "private_proof_hash": private_proof_hash,
            "event_type": "node_failed",
            "sequence": sequence,
        }
        event_id = "event-" + encode_document(identity)[1].removeprefix(
            "sha256:"
        )[:32]
        terminal_event = {
            "schema_version": "1.0.0",
            "event_id": event_id,
            "sequence": sequence,
            "task_id": intent.task_id,
            "node_id": intent.node_id,
            "event_type": "node_failed",
            "task_status": "Failed",
            "error": {
                "code": "dispatch_not_started",
                "message": "FWI Worker did not reach its running boundary",
                "retryable": False,
            },
            "occurred_at": evidence["ticket"]["updated_at"],
            "fingerprint": intent.queue_fingerprint,
            "extensions": {
                "org.agent_rpc.dispatch_reconciliation": {
                    "intent_id": intent.intent_id,
                    "attempt_id": attempt_id,
                    "attempt_number": 1,
                    "evidence_hash": evidence_hash,
                    "adapter_version": adapter_version,
                    "private_schema_version": private_schema_version,
                    "private_record_hash": private_record_hash,
                    "private_proof_hash": private_proof_hash,
                    "result": "not_dispatched",
                }
            },
        }
        return {
            "intent_id": intent.intent_id,
            "attempt_id": attempt_id,
            "attempt_number": 1,
            "adapter_version": adapter_version,
            "private_schema_version": private_schema_version,
            "private_record_hash": private_record_hash,
            "private_proof_hash": private_proof_hash,
            "evidence": evidence,
            "terminal_event": terminal_event,
        }

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

    def _checkpoint_runtime(
        self,
        *,
        key: str,
        project_waiting: bool = False,
        wall_time_seconds: int | None = None,
        checkpoint_clock: str = T_PLUS_5,
        commit: bool = True,
    ):
        task_id, dispatcher, runtime, _ = self._pending_runtime(
            key=key, wall_time_seconds=wall_time_seconds
        )
        acquisition = runtime.acquire_runtime_supervisor_lease(
            **self.scope,
            owner_id=f"checkpoint-owner-{key}",
            lease_seconds=30,
        )
        self.assertTrue(acquisition.acquired)
        if wall_time_seconds is not None:
            def supports_exact_timeout(_intent, *, attempt_id):
                connection = self._connection()
                try:
                    attempt = connection.execute(
                        "SELECT binding_hash FROM worker_launch_attempts "
                        "WHERE attempt_id = ?",
                        (attempt_id,),
                    ).fetchone()
                    self.assertIsNotNone(attempt)
                    assert attempt is not None
                    return timeout_capability_proof(
                        attempt_id=attempt_id,
                        binding_hash=attempt["binding_hash"],
                    )
                finally:
                    connection.close()

            dispatcher.supports_exact_timeout = supports_exact_timeout
        scheduled = runtime.schedule_runtime_dispatch(
            task_id,
            **self.scope,
            supervisor_lease=acquisition.lease,
        )
        self.assertEqual(scheduled.intent.state, "dispatched")
        dispatcher.adapter_status = {
            "status": "Running",
            "stage": "inversion",
            "completed": 1,
            "total": 2,
            "message": "iteration 1 of 2",
            "updated_at": NOW,
            "terminal": False,
        }
        refreshed = runtime.refresh_runtime_status(
            task_id,
            **self.scope,
            supervisor_lease=acquisition.lease,
        )
        self.assertEqual(refreshed.snapshot.status, "Running")
        if project_waiting:
            evidence = copy.deepcopy(dispatcher.worker_observation["evidence"])
            heartbeat = evidence["heartbeat"]
            heartbeat["sequence"] += 1
            heartbeat["state"] = "waiting"
            heartbeat["updated_at"] = CHECKPOINT_AT
            heartbeat_payload = {
                "schema_version": "1.0.0",
                "submission_id": evidence["submission_id"],
                "attempt_id": evidence["attempt_id"],
                "attempt_number": evidence["attempt_number"],
                "binding_hash": evidence["binding_hash"],
                "job_id": evidence["job_id"],
                "capacity_slot": evidence["ticket"]["capacity_slot"],
                "capacity_generation": evidence["ticket"][
                    "capacity_generation"
                ],
                "sequence": heartbeat["sequence"],
                "state": "waiting",
                "worker_pid": evidence["ready"]["worker_pid"],
                "started_at": evidence["ready"]["started_at"],
                "updated_at": CHECKPOINT_AT,
            }
            heartbeat["record_hash"] = encode_document(heartbeat_payload)[1]
            projected = self.store.record_supervised_worker_observation(
                intent_id=scheduled.intent.intent_id,
                evidence=evidence,
                handle=None,
                supervisor_lease=acquisition.lease,
                supervisor_clock=lambda: T_PLUS_1,
            )
            self.assertEqual(projected.observation_sequence, 2)
        connection = self._connection()
        try:
            attempt = connection.execute(
                """
                SELECT attempt.*, observation.ready_record_hash,
                       observation.heartbeat_state
                FROM worker_launch_attempts AS attempt
                JOIN worker_attempt_observations AS observation
                  ON observation.attempt_id = attempt.attempt_id
                WHERE attempt.intent_id = ?
                ORDER BY observation.observation_sequence DESC LIMIT 1
                """,
                (scheduled.intent.intent_id,),
            ).fetchone()
            self.assertIsNotNone(attempt)
            assert attempt is not None
        finally:
            connection.close()
        proof = checkpoint_adapter_proof(
            task_id=task_id,
            node_id=scheduled.intent.node_id,
            submission_id=attempt["submission_id"],
            attempt_id=attempt["attempt_id"],
            attempt_number=attempt["attempt_number"],
            binding_hash=attempt["binding_hash"],
            ready_record_hash=attempt["ready_record_hash"],
        )
        extension = {
            "org.agent_rpc.checkpoint_wait": {
                "checkpoint_id": proof["checkpoint_id"],
                "checkpoint_index": 1,
                "completed_updates": 1,
                "same_attempt": True,
            }
        }
        sequence = self.store.latest_run_event_sequence(task_id) + 1
        base = {
            "schema_version": "1.0.0",
            "task_id": task_id,
            "node_id": scheduled.intent.node_id,
            "occurred_at": CHECKPOINT_AT,
            "fingerprint": scheduled.intent.handle["fingerprint"],
            "extensions": extension,
        }
        checkpoint_event = {
            **base,
            "event_id": f"event-checkpoint-{key}",
            "sequence": sequence,
            "event_type": "checkpoint_created",
            "task_status": "Running",
            "checkpoint": {
                "relative_path": proof[
                    "checkpoint_manifest_relative_path"
                ]
            },
        }
        waiting_event = {
            **base,
            "event_id": f"event-waiting-{key}",
            "sequence": sequence + 1,
            "event_type": "node_waiting",
            "task_status": "Waiting",
        }
        waited = None
        if commit:
            waited = self.store.record_supervised_checkpoint_wait(
                intent_id=scheduled.intent.intent_id,
                checkpoint_proof=proof,
                checkpoint_event=checkpoint_event,
                waiting_event=waiting_event,
                supervisor_lease=acquisition.lease,
                supervisor_clock=lambda: checkpoint_clock,
            )
        return (
            task_id,
            scheduled.intent,
            acquisition.lease,
            waited,
            proof,
            runtime,
            dispatcher,
            checkpoint_event,
            waiting_event,
        )

    def test_checkpoint_wait_and_exact_resume_are_one_attempt(self) -> None:
        task_id, intent, lease, waited, waiting_proof, _, _, _, _ = (
            self._checkpoint_runtime(key="store-resume")
        )
        assert waited is not None
        self.assertEqual(waited.snapshot.status, "Waiting")
        self.assertEqual(waited.checkpoint.state, "waiting")
        self.assertEqual(
            [event["event_type"] for event in self.store.list_run_events(task_id)[-2:]],
            ["checkpoint_created", "node_waiting"],
        )
        admission = {
            "schema_version": "1.0.0",
            "task_id": task_id,
            "checkpoint_id": waiting_proof["checkpoint_id"],
            "action": "resume_exact_checkpoint",
            "extensions": {},
        }
        request = self.store.request_checkpoint_resume(
            task_id=task_id,
            **self.scope,
            idempotency_key=(
                f"checkpoint-resume:{waiting_proof['checkpoint_id']}"
            ),
            request_hash=encode_document(admission)[1],
            clock=lambda: T_PLUS_5,
        )
        self.assertEqual(request.checkpoint.state, "resume_requested")
        authorization = self.store.authorize_supervised_checkpoint_resume(
            resume_id=request.resume_id,
            supervisor_lease=lease,
            supervisor_clock=lambda: T_PLUS_5,
        )
        token = authorization.adapter_token()
        self.assertEqual(
            token["checkpoint_proof_hash"], waiting_proof["proof_hash"]
        )
        self.assertRegex(
            token["resume_request_record_hash"], r"^sha256:[0-9a-f]{64}$"
        )
        resumed_proof = checkpoint_adapter_proof(
            task_id=task_id,
            node_id=intent.node_id,
            submission_id=waiting_proof["submission_id"],
            attempt_id=waiting_proof["attempt_id"],
            attempt_number=waiting_proof["attempt_number"],
            binding_hash=waiting_proof["binding_hash"],
            ready_record_hash=waiting_proof["ready_record_hash"],
            state="resumed",
            checkpoint_proof_hash=waiting_proof["proof_hash"],
            resume_id=request.resume_id,
            resume_request_record_hash=token[
                "resume_request_record_hash"
            ],
        )
        running_event = {
            "schema_version": "1.0.0",
            "event_id": "event-resumed-store-resume",
            "sequence": self.store.latest_run_event_sequence(task_id) + 1,
            "task_id": task_id,
            "node_id": intent.node_id,
            "event_type": "node_started",
            "task_status": "Running",
            "occurred_at": RESUME_ACK_AT,
            "fingerprint": intent.handle["fingerprint"],
            "extensions": {
                "org.agent_rpc.checkpoint_resume": {
                    "checkpoint_id": waiting_proof["checkpoint_id"],
                    "resume_id": request.resume_id,
                    "same_attempt": True,
                }
            },
        }
        completed = self.store.complete_supervised_checkpoint_resume(
            resume_id=request.resume_id,
            adapter_proof=resumed_proof,
            running_event=running_event,
            supervisor_lease=lease,
            supervisor_clock=lambda: T_PLUS_10,
        )
        self.assertEqual(completed.snapshot.status, "Running")
        self.assertEqual(completed.checkpoint.state, "resumed")
        self.assertEqual(
            completed.checkpoint.attempt_id, waiting_proof["attempt_id"]
        )
        connection = self._connection()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM worker_launch_attempts "
                    "WHERE intent_id = ?",
                    (intent.intent_id,),
                ).fetchone()[0],
                1,
            )
        finally:
            connection.close()

    def test_checkpoint_resume_request_and_ack_survive_term_takeover(self) -> None:
        task_id, intent, first, waited, waiting_proof, _, _, _, _ = (
            self._checkpoint_runtime(key="store-resume-takeover")
        )
        assert waited is not None
        admission = {
            "schema_version": "1.0.0",
            "task_id": task_id,
            "checkpoint_id": waiting_proof["checkpoint_id"],
            "action": "resume_exact_checkpoint",
            "extensions": {},
        }
        request = self.store.request_checkpoint_resume(
            task_id=task_id,
            **self.scope,
            idempotency_key=(
                f"checkpoint-resume:{waiting_proof['checkpoint_id']}"
            ),
            request_hash=encode_document(admission)[1],
            clock=lambda: T_PLUS_5,
        )
        first_authorization = (
            self.store.authorize_supervised_checkpoint_resume(
                resume_id=request.resume_id,
                supervisor_lease=first,
                supervisor_clock=lambda: T_PLUS_5,
            )
        )
        stable_token = first_authorization.adapter_token()
        resumed_proof = checkpoint_adapter_proof(
            task_id=task_id,
            node_id=intent.node_id,
            submission_id=waiting_proof["submission_id"],
            attempt_id=waiting_proof["attempt_id"],
            attempt_number=waiting_proof["attempt_number"],
            binding_hash=waiting_proof["binding_hash"],
            ready_record_hash=waiting_proof["ready_record_hash"],
            state="resumed",
            checkpoint_proof_hash=waiting_proof["proof_hash"],
            resume_id=request.resume_id,
            resume_request_record_hash=stable_token[
                "resume_request_record_hash"
            ],
        )
        running_event = {
            "schema_version": "1.0.0",
            "event_id": "event-resumed-store-resume-takeover",
            "sequence": self.store.latest_run_event_sequence(task_id) + 1,
            "task_id": task_id,
            "node_id": intent.node_id,
            "event_type": "node_started",
            "task_status": "Running",
            "occurred_at": RESUME_ACK_AT,
            "fingerprint": intent.handle["fingerprint"],
            "extensions": {
                "org.agent_rpc.checkpoint_resume": {
                    "checkpoint_id": waiting_proof["checkpoint_id"],
                    "resume_id": request.resume_id,
                    "same_attempt": True,
                }
            },
        }

        self.store.release_runtime_supervisor_lease(
            lease=first, clock=lambda: T_PLUS_11
        )
        with self.assertRaises(RuntimeSupervisorLeaseLost):
            self.store.complete_supervised_checkpoint_resume(
                resume_id=request.resume_id,
                adapter_proof=resumed_proof,
                running_event=running_event,
                supervisor_lease=first,
                supervisor_clock=lambda: T_PLUS_11,
            )

        successor = self._acquire(
            "store-resume-takeover-successor",
            now=T_PLUS_11,
            lease_seconds=30,
        ).lease
        successor_authorization = (
            self.store.authorize_supervised_checkpoint_resume(
                resume_id=request.resume_id,
                supervisor_lease=successor,
                supervisor_clock=lambda: T_PLUS_11,
            )
        )
        self.assertNotEqual(
            successor_authorization.fencing_token,
            first_authorization.fencing_token,
        )
        self.assertEqual(
            successor_authorization.authorized_at,
            "2026-07-15T03:00:11.000000Z",
        )
        self.assertEqual(successor_authorization.adapter_token(), stable_token)
        completed = self.store.complete_supervised_checkpoint_resume(
            resume_id=request.resume_id,
            adapter_proof=resumed_proof,
            running_event=running_event,
            supervisor_lease=successor,
            supervisor_clock=lambda: T_PLUS_15,
        )
        self.assertEqual(completed.snapshot.status, "Running")
        self.assertEqual(completed.checkpoint.checkpoint_id, waiting_proof["checkpoint_id"])

        connection = self._connection()
        try:
            authorizations = connection.execute(
                """
                SELECT fencing_token, resume_request_record_hash,
                       authorization_hash
                FROM supervised_checkpoint_resume_authorizations
                WHERE resume_id = ? ORDER BY fencing_token
                """,
                (request.resume_id,),
            ).fetchall()
            self.assertEqual(len(authorizations), 2)
            self.assertEqual(
                len({row["resume_request_record_hash"] for row in authorizations}),
                1,
            )
            self.assertEqual(
                len({row["authorization_hash"] for row in authorizations}),
                1,
            )
            outcome = connection.execute(
                "SELECT fencing_token FROM task_checkpoint_resume_outcomes "
                "WHERE resume_id = ?",
                (request.resume_id,),
            ).fetchone()
            self.assertEqual(outcome["fencing_token"], successor.fencing_token)
        finally:
            connection.close()

    def test_waiting_worker_projection_can_commit_checkpoint(self) -> None:
        task_id, intent, _, waited, proof, _, _, _, _ = self._checkpoint_runtime(
            key="waiting-projection", project_waiting=True
        )
        assert waited is not None
        self.assertEqual(waited.snapshot.status, "Waiting")
        self.assertEqual(waited.checkpoint.attempt_id, proof["attempt_id"])
        candidate = self.store.get_task_cancel_candidate(task_id)
        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(candidate["heartbeat"]["state"], "waiting")
        self.assertEqual(candidate["attempt_id"], proof["attempt_id"])
        self.assertEqual(intent.adapter_version, "1.6.0")

    def test_waiting_task_can_cancel_the_exact_projected_attempt(self) -> None:
        (
            task_id,
            _,
            lease,
            waited,
            waiting_proof,
            runtime,
            dispatcher,
            _,
            _,
        ) = self._checkpoint_runtime(
            key="waiting-cancel", project_waiting=True
        )
        assert waited is not None
        self.assertEqual(waited.snapshot.status, "Waiting")
        self.now[0] = T_PLUS_5
        admitted = runtime.cancel_task(
            task_id=task_id,
            reason="user_requested",
            idempotency_key="cancel-waiting-cancel",
            **self.scope,
        )
        cancellation = admitted.snapshot.cancellation
        self.assertIsNotNone(cancellation)
        assert cancellation is not None
        self.assertEqual(cancellation.attempt_id, waiting_proof["attempt_id"])
        authorized = self.store.authorize_supervised_cancel(
            request_id=cancellation.request_id,
            supervisor_lease=lease,
            supervisor_clock=lambda: T_PLUS_5,
        )
        self.assertFalse(authorized.replayed)
        completed = runtime.process_runtime_cancellation(
            task_id,
            **self.scope,
            supervisor_lease=lease,
        )
        self.assertEqual(completed.state, "cancelled")
        self.assertEqual(self.store.get_task(task_id).status, "Cancelled")
        self.assertEqual(dispatcher.cancel_requests[-1][1], waiting_proof["attempt_id"])

    def test_due_timeout_can_complete_from_waiting(self) -> None:
        (
            task_id,
            intent,
            lease,
            waited,
            waiting_proof,
            _,
            _,
            _,
            _,
        ) = self._checkpoint_runtime(
            key="waiting-timeout",
            project_waiting=True,
            wall_time_seconds=5,
            checkpoint_clock=T_PLUS_1,
        )
        assert waited is not None
        timeout = waited.snapshot.timeout
        self.assertIsNotNone(timeout)
        assert timeout is not None
        self.assertEqual(timeout.state, "armed")
        self.assertEqual(timeout.attempt_id, waiting_proof["attempt_id"])
        authorization = self.store.authorize_supervised_timeout(
            timeout_id=timeout.timeout_id,
            supervisor_lease=lease,
            supervisor_clock=lambda: T_PLUS_5,
        )
        self.assertTrue(authorization.authorized)
        self.assertEqual(authorization.timeout.state, "requested")
        proof = timeout_adapter_proof(
            timeout=timeout,
            state="timed_out",
            terminal_status="Failed",
            terminal_failure_code="WALL_TIME_EXCEEDED",
            ready_record_hash=self._timeout_ready_record_hash(timeout.timeout_id),
        )
        event = self._confirmed_timeout_failure_event(
            timeout=timeout,
            intent=intent,
            proof=proof,
            event_id="event-waiting-timeout",
            occurred_at=T_PLUS_5,
        )
        completed = self.store.complete_supervised_timeout(
            timeout_id=timeout.timeout_id,
            result="timeout_confirmed",
            terminal_event=event,
            adapter_proof=proof,
            supervisor_lease=lease,
            supervisor_clock=lambda: T_PLUS_5,
        )
        self.assertEqual(completed.snapshot.status, "Failed")
        self.assertEqual(completed.timeout.state, "timed_out")

    def test_preexisting_cancel_blocks_checkpoint_wait_atomically(self) -> None:
        (
            task_id,
            intent,
            lease,
            waited,
            proof,
            runtime,
            _,
            checkpoint_event,
            waiting_event,
        ) = self._checkpoint_runtime(key="cancel-before-wait", commit=False)
        self.assertIsNone(waited)
        self.now[0] = T_PLUS_1
        admitted = runtime.cancel_task(
            task_id=task_id,
            reason="user_requested",
            idempotency_key="cancel-before-checkpoint-wait",
            **self.scope,
        )
        self.assertIsNotNone(admitted.snapshot.cancellation)
        sequence = self.store.latest_run_event_sequence(task_id)
        with self.assertRaisesRegex(
            TaskStoreConflict, "cancellation has priority"
        ):
            self.store.record_supervised_checkpoint_wait(
                intent_id=intent.intent_id,
                checkpoint_proof=proof,
                checkpoint_event=checkpoint_event,
                waiting_event=waiting_event,
                supervisor_lease=lease,
                supervisor_clock=lambda: T_PLUS_5,
            )
        self.assertEqual(self.store.latest_run_event_sequence(task_id), sequence)
        self.assertEqual(self.store.get_task(task_id).status, "Running")
        connection = self._connection()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM worker_checkpoint_waits "
                    "WHERE task_id = ?",
                    (task_id,),
                ).fetchone()[0],
                0,
            )
        finally:
            connection.close()

    def test_authorized_timeout_blocks_checkpoint_wait_atomically(self) -> None:
        (
            task_id,
            intent,
            lease,
            waited,
            proof,
            _,
            _,
            checkpoint_event,
            waiting_event,
        ) = self._checkpoint_runtime(
            key="timeout-before-wait",
            wall_time_seconds=5,
            commit=False,
        )
        self.assertIsNone(waited)
        snapshot = self.store.get_task(task_id)
        timeout = snapshot.timeout
        self.assertIsNotNone(timeout)
        assert timeout is not None
        authorization = self.store.authorize_supervised_timeout(
            timeout_id=timeout.timeout_id,
            supervisor_lease=lease,
            supervisor_clock=lambda: T_PLUS_5,
        )
        self.assertTrue(authorization.authorized)
        sequence = self.store.latest_run_event_sequence(task_id)
        with self.assertRaisesRegex(TaskStoreConflict, "timeout has priority"):
            self.store.record_supervised_checkpoint_wait(
                intent_id=intent.intent_id,
                checkpoint_proof=proof,
                checkpoint_event=checkpoint_event,
                waiting_event=waiting_event,
                supervisor_lease=lease,
                supervisor_clock=lambda: T_PLUS_5,
            )
        self.assertEqual(self.store.latest_run_event_sequence(task_id), sequence)
        self.assertEqual(self.store.get_task(task_id).status, "Running")
        connection = self._connection()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM worker_checkpoint_waits "
                    "WHERE task_id = ?",
                    (task_id,),
                ).fetchone()[0],
                0,
            )
        finally:
            connection.close()

    def test_checkpoint_wait_and_cancel_race_serializes_without_partial_state(
        self,
    ) -> None:
        (
            task_id,
            intent,
            lease,
            _,
            proof,
            runtime,
            _,
            checkpoint_event,
            waiting_event,
        ) = self._checkpoint_runtime(key="cancel-wait-race", commit=False)
        self.now[0] = T_PLUS_1
        barrier = threading.Barrier(2)

        def commit_checkpoint() -> str:
            barrier.wait(timeout=5)
            try:
                self.store.record_supervised_checkpoint_wait(
                    intent_id=intent.intent_id,
                    checkpoint_proof=proof,
                    checkpoint_event=checkpoint_event,
                    waiting_event=waiting_event,
                    supervisor_lease=lease,
                    supervisor_clock=lambda: T_PLUS_1,
                )
            except TaskStoreConflict:
                return "checkpoint_conflict"
            return "checkpoint_committed"

        def request_cancel() -> str:
            barrier.wait(timeout=5)
            runtime.cancel_task(
                task_id=task_id,
                reason="user_requested",
                idempotency_key="cancel-concurrent-checkpoint-wait",
                **self.scope,
            )
            return "cancel_requested"

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = (
                executor.submit(commit_checkpoint),
                executor.submit(request_cancel),
            )
            results = {future.result() for future in futures}
        self.assertIn("cancel_requested", results)
        self.assertTrue(
            {"checkpoint_conflict", "checkpoint_committed"} & results
        )
        snapshot = self.store.get_task(task_id)
        self.assertIsNotNone(snapshot.cancellation)
        assert snapshot.cancellation is not None
        self.assertEqual(snapshot.cancellation.attempt_id, proof["attempt_id"])
        connection = self._connection()
        try:
            checkpoint_count = connection.execute(
                "SELECT COUNT(*) FROM worker_checkpoint_waits WHERE task_id = ?",
                (task_id,),
            ).fetchone()[0]
            self.assertIn(checkpoint_count, {0, 1})
            if checkpoint_count == 0:
                self.assertEqual(snapshot.status, "Running")
            else:
                self.assertEqual(snapshot.status, "Waiting")
        finally:
            connection.close()

    def test_checkpoint_wait_and_timeout_race_serializes_timeout_priority(
        self,
    ) -> None:
        (
            task_id,
            intent,
            lease,
            _,
            proof,
            _,
            _,
            checkpoint_event,
            waiting_event,
        ) = self._checkpoint_runtime(
            key="timeout-wait-race",
            wall_time_seconds=5,
            commit=False,
        )
        timeout = self.store.get_task(task_id).timeout
        self.assertIsNotNone(timeout)
        assert timeout is not None
        barrier = threading.Barrier(2)

        def commit_checkpoint() -> str:
            barrier.wait(timeout=5)
            try:
                self.store.record_supervised_checkpoint_wait(
                    intent_id=intent.intent_id,
                    checkpoint_proof=proof,
                    checkpoint_event=checkpoint_event,
                    waiting_event=waiting_event,
                    supervisor_lease=lease,
                    supervisor_clock=lambda: T_PLUS_1,
                )
            except TaskStoreConflict:
                return "checkpoint_conflict"
            return "checkpoint_committed"

        def authorize_timeout() -> str:
            barrier.wait(timeout=5)
            authorization = self.store.authorize_supervised_timeout(
                timeout_id=timeout.timeout_id,
                supervisor_lease=lease,
                supervisor_clock=lambda: T_PLUS_5,
            )
            self.assertTrue(authorization.authorized)
            return "timeout_authorized"

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = (
                executor.submit(commit_checkpoint),
                executor.submit(authorize_timeout),
            )
            results = {future.result() for future in futures}
        self.assertIn("timeout_authorized", results)
        self.assertTrue(
            {"checkpoint_conflict", "checkpoint_committed"} & results
        )
        snapshot = self.store.get_task(task_id)
        self.assertIsNotNone(snapshot.timeout)
        assert snapshot.timeout is not None
        self.assertEqual(snapshot.timeout.state, "requested")
        connection = self._connection()
        try:
            checkpoint_count = connection.execute(
                "SELECT COUNT(*) FROM worker_checkpoint_waits WHERE task_id = ?",
                (task_id,),
            ).fetchone()[0]
            self.assertIn(checkpoint_count, {0, 1})
            self.assertEqual(
                snapshot.status,
                "Waiting" if checkpoint_count else "Running",
            )
        finally:
            connection.close()

    def _timeout_runtime(self, *, key: str, wall_time_seconds: int = 5):
        task_id, dispatcher, runtime, _ = self._pending_runtime(
            key=key, wall_time_seconds=wall_time_seconds
        )
        acquisition = runtime.acquire_runtime_supervisor_lease(
            **self.scope,
            owner_id=f"timeout-owner-{key}",
            lease_seconds=30,
        )
        self.assertTrue(acquisition.acquired)

        def supports_exact_timeout(_intent, *, attempt_id):
            connection = self._connection()
            try:
                attempt = connection.execute(
                    """
                    SELECT binding_hash FROM worker_launch_attempts
                    WHERE attempt_id = ?
                    """,
                    (attempt_id,),
                ).fetchone()
                self.assertIsNotNone(attempt)
                assert attempt is not None
                return timeout_capability_proof(
                    attempt_id=attempt_id,
                    binding_hash=attempt["binding_hash"],
                )
            finally:
                connection.close()

        dispatcher.supports_exact_timeout = supports_exact_timeout
        scheduled = runtime.schedule_runtime_dispatch(
            task_id,
            **self.scope,
            supervisor_lease=acquisition.lease,
        )
        self.assertEqual(scheduled.intent.state, "dispatched")
        self.assertTrue(scheduled.timeout_armed)
        connection = self._connection()
        try:
            attempt = connection.execute(
                """
                SELECT attempt_id, binding_hash
                FROM worker_launch_attempts WHERE intent_id = ?
                """,
                (scheduled.intent.intent_id,),
            ).fetchone()
            self.assertIsNotNone(attempt)
            assert attempt is not None
            proof = timeout_capability_proof(
                attempt_id=attempt["attempt_id"],
                binding_hash=attempt["binding_hash"],
            )
        finally:
            connection.close()
        armed = self.store.arm_worker_attempt_timeout(
            intent_id=scheduled.intent.intent_id,
            attempt_id=attempt["attempt_id"],
            capability_proof=proof,
            supervisor_lease=acquisition.lease,
            supervisor_clock=lambda: self.now[0],
        )
        self.assertEqual(armed.timeout.state, "armed")
        return (
            task_id,
            dispatcher,
            runtime,
            scheduled.intent,
            acquisition.lease,
            armed,
            proof,
        )

    def _staged_then_running_timeout_runtime(self, *, key: str):
        task_id, dispatcher, runtime, _ = self._pending_runtime(
            key=key,
            deferred=True,
            wall_time_seconds=5,
        )
        acquisition = runtime.acquire_runtime_supervisor_lease(
            **self.scope,
            owner_id=f"timeout-owner-{key}",
            lease_seconds=30,
        )
        self.assertTrue(acquisition.acquired)
        staged = runtime.schedule_runtime_dispatch(
            task_id,
            **self.scope,
            supervisor_lease=acquisition.lease,
        )
        dispatcher.failure_code = None
        dispatcher.defer_dispatch = False
        handle = dispatcher.dispatch(staged.intent)
        self.assertIsNotNone(dispatcher.worker_observation)
        assert dispatcher.worker_observation is not None
        evidence = dispatcher.worker_observation["evidence"]
        projected = self.store.record_supervised_worker_observation(
            intent_id=staged.intent.intent_id,
            evidence=evidence,
            handle=handle,
            supervisor_lease=acquisition.lease,
            supervisor_clock=lambda: T_PLUS_1,
        )
        self.assertEqual(projected.observation_sequence, 2)
        connection = self._connection()
        try:
            attempt = connection.execute(
                "SELECT * FROM worker_launch_attempts WHERE attempt_id = ?",
                (projected.attempt_id,),
            ).fetchone()
            self.assertIsNotNone(attempt)
            assert attempt is not None
        finally:
            connection.close()
        proof = timeout_capability_proof(
            attempt_id=attempt["attempt_id"],
            binding_hash=attempt["binding_hash"],
        )
        return task_id, staged.intent, acquisition.lease, attempt, proof

    def _timeout_ready_record_hash(self, timeout_id: str) -> str:
        connection = self._connection()
        try:
            row = connection.execute(
                """
                SELECT ready_record_hash FROM worker_attempt_timeout_windows
                WHERE timeout_id = ?
                """,
                (timeout_id,),
            ).fetchone()
            self.assertIsNotNone(row)
            assert row is not None
            return row["ready_record_hash"]
        finally:
            connection.close()

    def _natural_timeout_failure_event(
        self,
        *,
        timeout,
        intent,
        event_id: str,
        occurred_at: str,
    ) -> dict:
        return {
            "schema_version": "1.0.0",
            "event_id": event_id,
            "sequence": self.store.latest_run_event_sequence(timeout.task_id) + 1,
            "task_id": timeout.task_id,
            "node_id": intent.node_id,
            "event_type": "node_failed",
            "task_status": "Failed",
            "error": {
                "code": "worker_failed",
                "message": "FWI Worker reported a failure",
                "retryable": False,
            },
            "occurred_at": occurred_at,
            "fingerprint": intent.handle["fingerprint"],
            "extensions": {
                "org.agent_rpc.adapter_status": {
                    "job_id": intent.handle["job_id"],
                    "stage": "failed",
                    "worker_updated_at": occurred_at,
                }
            },
        }

    def _confirmed_timeout_failure_event(
        self,
        *,
        timeout,
        intent,
        proof: dict,
        event_id: str,
        occurred_at: str,
    ) -> dict:
        return {
            "schema_version": "1.0.0",
            "event_id": event_id,
            "sequence": self.store.latest_run_event_sequence(timeout.task_id) + 1,
            "task_id": timeout.task_id,
            "node_id": intent.node_id,
            "event_type": "node_failed",
            "task_status": "Failed",
            "error": {
                "code": "wall_time_exceeded",
                "message": "FWI Worker exceeded its wall-time limit",
                "retryable": False,
            },
            "occurred_at": occurred_at,
            "fingerprint": intent.handle["fingerprint"],
            "extensions": {
                "org.agent_rpc.timeout": {
                    "timeout_id": timeout.timeout_id,
                    "attempt_id": timeout.attempt_id,
                    "wall_time_seconds": timeout.wall_time_seconds,
                    "started_at": timeout.started_at,
                    "deadline_at": timeout.deadline_at,
                    "failure_code": "WALL_TIME_EXCEEDED",
                    "proof_hash": proof["proof_hash"],
                }
            },
        }

    def _insert_direct_terminal_preempted_timeout(
        self,
        connection: sqlite3.Connection,
        *,
        timeout,
        intent,
        lease,
        proof: dict,
        event: dict,
        resolved_at: str,
        resolved_at_us: int,
    ) -> None:
        event_json, event_hash = encode_document(event)
        _, fingerprint_hash = encode_document(event["fingerprint"])
        connection.execute(
            """
            INSERT INTO run_events(
                task_id, sequence, event_id, event_type, task_status,
                node_id, fingerprint_hash, document_json, document_hash,
                occurred_at, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                timeout.task_id,
                event["sequence"],
                event["event_id"],
                event["event_type"],
                event["task_status"],
                event["node_id"],
                fingerprint_hash,
                event_json,
                event_hash,
                event["occurred_at"],
                resolved_at,
            ),
        )
        connection.execute(
            "UPDATE tasks SET status = 'Failed', updated_at = ? "
            "WHERE task_id = ?",
            (resolved_at, timeout.task_id),
        )
        proof_json, proof_hash = encode_document(proof)
        outcome = {
            "schema_version": "1.0.0",
            "request_id": timeout.timeout_id,
            "task_id": timeout.task_id,
            "result": "terminal_preempted",
            "terminal_status": "Failed",
            "failure_code": None,
            "adapter_proof": proof,
            "resolved_at": resolved_at,
            "extensions": {},
        }
        outcome_json, outcome_hash = encode_document(outcome)
        connection.execute(
            """
            INSERT INTO task_timeout_outcomes(
                timeout_id, task_id, project_id, principal_id,
                intent_id, attempt_id, result, terminal_status,
                failure_code, terminal_event_sequence,
                adapter_proof_json, adapter_proof_hash,
                document_json, document_hash, fencing_token,
                resolved_at, resolved_at_us
            ) VALUES (?, ?, ?, ?, ?, ?, 'terminal_preempted', 'Failed',
                      NULL, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                timeout.timeout_id,
                timeout.task_id,
                PROJECT_ID,
                PRINCIPAL_ID,
                intent.intent_id,
                timeout.attempt_id,
                event["sequence"],
                proof_json,
                proof_hash,
                outcome_json,
                outcome_hash,
                lease.fencing_token,
                resolved_at,
                resolved_at_us,
            ),
        )

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

    def test_fresh_v20_has_supervisor_tables_and_immutable_triggers(self) -> None:
        self.assertEqual(self.store.migration_version(), 23)
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
            "worker_attempt_timeout_windows",
            "supervised_timeout_attempts",
            "task_timeout_outcomes",
            "supervised_dispatch_reconciliation_attempts",
            "dispatch_reconciliation_resolutions",
            "approval_retry_budgets",
            "worker_retry_reservations",
            "supervised_retry_attempts",
            "worker_exit_retry_reservations",
            "supervised_worker_exit_retry_attempts",
            "worker_exit_retry_timeout_retirements",
            "worker_exit_retry_dispatch_replacements",
            "worker_exit_retry_exhaustions",
            "dispatch_reconciliation_observations",
            "dispatch_reconciliation_negative_resolutions",
            "worker_checkpoint_waits",
            "task_checkpoint_resume_requests",
            "supervised_checkpoint_resume_authorizations",
            "task_checkpoint_resume_outcomes",
            "dag_node_state_events",
            "dag_node_claim_candidates",
            "dag_node_execution_admissions",
            "dag_node_execution_transition_facts",
            "dag_node_terminal_facts",
            "dag_task_execution_runs",
            "dag_node_scheduler_transition_facts",
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
            "worker_attempt_waiting_requires_checkpoint_capable_intent",
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
            "worker_attempt_timeout_window_requires_exact_start",
            "worker_attempt_timeout_window_requires_active_term",
            "supervised_timeout_attempt_requires_due_window",
            "supervised_timeout_attempt_requires_active_term",
            "task_cancel_request_rejects_authorized_timeout",
            "task_timeout_outcome_requires_terminal_event",
            "task_timeout_outcome_requires_active_term",
            "worker_attempt_timeout_windows_are_immutable",
            "worker_attempt_timeout_windows_cannot_be_deleted",
            "supervised_timeout_attempts_are_immutable",
            "supervised_timeout_attempts_cannot_be_deleted",
            "task_timeout_outcomes_are_immutable",
            "task_timeout_outcomes_cannot_be_deleted",
            "supervised_dispatch_reconciliation_requires_exact_case",
            "supervised_dispatch_reconciliation_requires_active_term",
            "dispatch_reconciliation_resolution_requires_exact_proof",
            "dispatch_reconciliation_resolution_requires_active_term",
            "supervised_dispatch_reconciliation_attempts_are_immutable",
            "supervised_dispatch_reconciliation_attempts_cannot_be_deleted",
            "dispatch_reconciliation_resolutions_are_immutable",
            "dispatch_reconciliation_resolutions_cannot_be_deleted",
            "approvals_initialize_retry_budget",
            "approval_retry_budgets_are_immutable",
            "approval_retry_budgets_cannot_be_deleted",
            "worker_retry_reservation_requires_exact_case",
            "worker_retry_reservation_requires_active_term",
            "supervised_retry_attempt_requires_active_term",
            "worker_launch_attempt_requires_retry_reservation",
            "worker_launch_attempt_rejects_attempt_three",
            "worker_retry_reservations_are_immutable",
            "worker_retry_reservations_cannot_be_deleted",
            "supervised_retry_attempts_are_immutable",
            "supervised_retry_attempts_cannot_be_deleted",
            "worker_exit_retry_reservation_requires_exact_case",
            "worker_exit_retry_reservation_requires_active_term",
            "worker_exit_retry_timeout_retirement_requires_exact_window",
            "worker_exit_retry_reservation_retires_timeout",
            "supervised_worker_exit_retry_attempt_requires_active_term",
            "worker_exit_retry_replacement_requires_exact_case",
            "worker_exit_retry_replacement_requires_active_term",
            "worker_exit_retry_exhaustion_requires_exact_case",
            "worker_exit_retry_reservations_are_immutable",
            "worker_exit_retry_reservations_cannot_be_deleted",
            "supervised_worker_exit_retry_attempts_are_immutable",
            "supervised_worker_exit_retry_attempts_cannot_be_deleted",
            "worker_exit_retry_timeout_retirements_are_immutable",
            "worker_exit_retry_timeout_retirements_cannot_be_deleted",
            "worker_exit_retry_dispatch_replacements_are_immutable",
            "worker_exit_retry_dispatch_replacements_cannot_be_deleted",
            "worker_exit_retry_exhaustions_are_immutable",
            "worker_exit_retry_exhaustions_cannot_be_deleted",
            "dispatch_reconciliation_observation_requires_exact_case",
            "dispatch_reconciliation_observation_requires_active_term",
            "dispatch_reconciliation_observation_sequence_is_contiguous",
            "dispatch_reconciliation_negative_requires_exact_case",
            "dispatch_reconciliation_negative_requires_active_term",
            "dispatch_reconciliation_observations_are_immutable",
            "dispatch_reconciliation_observations_cannot_be_deleted",
            "dispatch_reconciliation_negative_resolutions_are_immutable",
            "dispatch_reconciliation_negative_resolutions_cannot_be_deleted",
            "worker_checkpoint_wait_requires_active_term",
            "worker_checkpoint_wait_requires_exact_live_attempt",
            "checkpoint_resume_request_requires_current_wait",
            "checkpoint_resume_authorization_requires_active_term",
            "checkpoint_resume_authorization_requires_current_wait",
            "checkpoint_resume_authorization_reuses_worker_request",
            "checkpoint_resume_outcome_requires_active_term",
            "checkpoint_resume_outcome_requires_exact_ack",
            "worker_checkpoint_waits_are_append_only",
            "worker_checkpoint_waits_cannot_be_deleted",
            "checkpoint_resume_requests_are_append_only",
            "checkpoint_resume_requests_cannot_be_deleted",
            "checkpoint_resume_authorizations_are_append_only",
            "checkpoint_resume_authorizations_cannot_be_deleted",
            "checkpoint_resume_outcomes_are_append_only",
            "checkpoint_resume_outcomes_cannot_be_deleted",
            "dag_node_initial_state_has_exact_shape",
            "dag_node_initial_state_requires_current_approved_plan",
            "dag_node_transition_state_requires_exact_active_fact",
            "dag_node_state_events_are_append_only",
            "dag_node_state_events_cannot_be_deleted",
            "dag_node_claim_requires_current_approved_plan",
            "dag_node_claim_requires_latest_pending_revision",
            "dag_node_claim_requires_active_term",
            "dag_node_claim_candidates_are_append_only",
            "dag_node_claim_candidates_cannot_be_deleted",
            "dag_node_execution_admission_requires_exact_current_case",
            "dag_node_execution_admission_requires_active_term",
            "dag_node_execution_admission_requires_exact_document",
            "dag_node_execution_admissions_are_append_only",
            "dag_node_execution_admissions_cannot_be_deleted",
            "dag_node_terminal_fact_requires_exact_current_case",
            "dag_node_terminal_fact_requires_exact_p2_evidence",
            "dag_node_terminal_fact_requires_active_completion_term",
            "dag_node_terminal_success_requires_complete_receipt",
            "fixed_recipe_terminal_success_requires_succeeded_worker",
            "dag_node_terminal_facts_are_append_only",
            "dag_node_terminal_facts_cannot_be_deleted",
            "dag_node_execution_transition_requires_exact_current_case",
            "dag_node_execution_transition_requires_active_term",
            "dag_node_execution_transition_facts_are_append_only",
            "dag_node_execution_transition_facts_cannot_be_deleted",
            "dag_node_execution_blocks_second_launch_attempt",
            "dag_node_execution_blocks_pre_running_retry",
            "dag_node_execution_blocks_worker_exit_retry",
            "dag_task_execution_runs_are_append_only",
            "dag_task_execution_runs_cannot_be_deleted",
            "dag_node_scheduler_transition_requires_exact_case",
            "dag_node_scheduler_transition_requires_active_term",
            "dag_node_scheduler_transition_facts_are_append_only",
            "dag_node_scheduler_transition_facts_cannot_be_deleted",
        }
        expected_indexes = {
            "idx_worker_attempt_timeout_windows_scope_deadline",
            "idx_worker_attempt_timeout_windows_task",
            "idx_supervised_timeout_attempts_term",
            "idx_supervised_dispatch_reconciliation_attempts_term",
            "idx_dispatch_reconciliation_resolutions_scope",
            "idx_worker_exit_retry_reservations_scope",
            "idx_supervised_worker_exit_retry_attempts_term",
            "idx_worker_exit_retry_replacements_scope",
            "idx_worker_exit_retry_exhaustions_scope",
            "idx_dispatch_reconciliation_observations_scope",
            "idx_dispatch_reconciliation_negative_scope",
            "idx_worker_checkpoint_waits_task",
            "idx_checkpoint_resume_requests_task",
            "idx_checkpoint_resume_authorizations_term",
            "idx_dag_node_state_events_current",
            "idx_dag_node_claim_candidates_term",
            "idx_dag_node_execution_admissions_term",
            "idx_dag_node_execution_transition_facts_term",
            "idx_dag_node_terminal_facts_term",
            "idx_dag_node_scheduler_transition_facts_term",
        }
        connection = self._connection()
        try:
            rows = connection.execute(
                """
                SELECT type, name FROM sqlite_master
                WHERE type IN ('table', 'trigger', 'index', 'view')
                """
            ).fetchall()
            tables = {row["name"] for row in rows if row["type"] == "table"}
            triggers = {row["name"] for row in rows if row["type"] == "trigger"}
            indexes = {row["name"] for row in rows if row["type"] == "index"}
            views = {row["name"] for row in rows if row["type"] == "view"}
            self.assertTrue(expected_tables <= tables)
            self.assertTrue(expected_triggers <= triggers)
            self.assertTrue(expected_indexes <= indexes)
            self.assertIn("effective_dispatched_intents", views)
            timeout_query_plan = connection.execute(
                """
                EXPLAIN QUERY PLAN
                SELECT 1 FROM worker_attempt_timeout_windows
                WHERE task_id = ? LIMIT 1
                """,
                ("task-timeout-query-plan",),
            ).fetchall()
            self.assertTrue(
                any(
                    "idx_worker_attempt_timeout_windows_task" in row[3]
                    for row in timeout_query_plan
                )
            )
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
            timeout_migration = connection.execute(
                "SELECT name FROM schema_migrations WHERE version = 12"
            ).fetchone()
            self.assertEqual(
                timeout_migration["name"], "0012_task_timeout.sql"
            )
            reconciliation_migration = connection.execute(
                "SELECT name FROM schema_migrations WHERE version = 13"
            ).fetchone()
            self.assertEqual(
                reconciliation_migration["name"],
                "0013_dispatch_reconciliation.sql",
            )
            retry_migration = connection.execute(
                "SELECT name FROM schema_migrations WHERE version = 14"
            ).fetchone()
            self.assertEqual(retry_migration["name"], "0014_task_retry.sql")
            worker_exit_retry_migration = connection.execute(
                "SELECT name FROM schema_migrations WHERE version = 15"
            ).fetchone()
            self.assertEqual(
                worker_exit_retry_migration["name"],
                "0015_worker_exit_retry.sql",
            )
            negative_migration = connection.execute(
                "SELECT name FROM schema_migrations WHERE version = 16"
            ).fetchone()
            self.assertEqual(
                negative_migration["name"],
                "0016_dispatch_negative_reconciliation.sql",
            )
            checkpoint_migration = connection.execute(
                "SELECT name FROM schema_migrations WHERE version = 17"
            ).fetchone()
            self.assertEqual(
                checkpoint_migration["name"],
                "0017_checkpoint_wait_resume.sql",
            )
            dag_migration = connection.execute(
                "SELECT name FROM schema_migrations WHERE version = 18"
            ).fetchone()
            self.assertEqual(
                dag_migration["name"],
                "0018_dag_node_claim_candidates.sql",
            )
            input_binding_migration = connection.execute(
                "SELECT name FROM schema_migrations WHERE version = 19"
            ).fetchone()
            self.assertEqual(
                input_binding_migration["name"],
                "0019_dag_node_input_bindings.sql",
            )
            execution_migration = connection.execute(
                "SELECT name FROM schema_migrations WHERE version = 20"
            ).fetchone()
            self.assertEqual(
                execution_migration["name"],
                "0020_dag_node_execution_kernel.sql",
            )
            scheduler_migration = connection.execute(
                "SELECT name FROM schema_migrations WHERE version = 21"
            ).fetchone()
            self.assertEqual(
                scheduler_migration["name"],
                "0021_dag_runtime_scheduler.sql",
            )
            cache_migration = connection.execute(
                "SELECT name FROM schema_migrations WHERE version = 22"
            ).fetchone()
            self.assertEqual(
                cache_migration["name"],
                "0022_dag_node_cache_lineage.sql",
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

    def test_real_v16_database_upgrades_catalog_constraints_to_v20(
        self,
    ) -> None:
        historical_directory = Path(self.temporary.name) / "historical-v16"
        historical_directory.mkdir(mode=0o700)
        historical_path = historical_directory / "task.sqlite3"
        connection = sqlite3.connect(historical_path, isolation_level=None)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(task_store_module.SCHEMA_MIGRATIONS_SQL)
            for migration in task_store_module._load_migrations()[:16]:
                for statement in task_store_module._migration_statements(
                    migration.text
                ):
                    connection.execute(statement)
                connection.execute(
                    """
                    INSERT INTO schema_migrations(
                        version, name, checksum, applied_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        migration.version,
                        migration.path.name,
                        migration.checksum,
                        NOW,
                    ),
                )
                connection.execute(
                    f"PRAGMA user_version = {migration.version}"
                )
            connection.execute(
                f"PRAGMA application_id = {task_store_module.APPLICATION_ID}"
            )
            connection.commit()
            heartbeat_schema = connection.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'table' "
                "AND name = 'worker_attempt_observations'"
            ).fetchone()[0]
            negative_schema = connection.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'table' "
                "AND name = 'dispatch_reconciliation_observations'"
            ).fetchone()[0]
            self.assertNotIn("'waiting'", heartbeat_schema)
            self.assertNotIn("adapter_version = '1.6.0'", negative_schema)
        finally:
            connection.close()

        upgraded = SQLiteTaskStore(historical_path)
        self.assertEqual(upgraded.migration_version(), 23)
        connection = sqlite3.connect(historical_path)
        try:
            heartbeat_schema = connection.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'table' "
                "AND name = 'worker_attempt_observations'"
            ).fetchone()[0]
            negative_schema = connection.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'table' "
                "AND name = 'dispatch_reconciliation_observations'"
            ).fetchone()[0]
            self.assertIn("'waiting'", heartbeat_schema)
            self.assertIn("adapter_version = '1.6.0'", negative_schema)
            self.assertEqual(
                connection.execute("PRAGMA quick_check").fetchall(),
                [("ok",)],
            )
            self.assertEqual(
                connection.execute("PRAGMA foreign_key_check").fetchall(), []
            )
        finally:
            connection.close()

    def test_v15_worker_exit_retry_columns_are_a_stable_store_interface(
        self,
    ) -> None:
        expected_columns = {
            "worker_exit_retry_reservations": [
                "intent_id",
                "attempt_number",
                "task_id",
                "project_id",
                "principal_id",
                "approval_id",
                "previous_attempt_id",
                "previous_observation_sequence",
                "evidence_hash",
                "private_schema_version",
                "private_proof_hash",
                "failure_kind",
                "source_outcome_document_hash",
                "source_handle_hash",
                "retry_event_sequence",
                "retry_event_hash",
                "first_fencing_token",
                "reserved_at",
                "reserved_at_us",
            ],
            "supervised_worker_exit_retry_attempts": [
                "intent_id",
                "attempt_number",
                "project_id",
                "principal_id",
                "fencing_token",
                "authorized_at",
                "authorized_at_us",
            ],
            "worker_exit_retry_timeout_retirements": [
                "timeout_id",
                "intent_id",
                "attempt_number",
                "attempt_id",
                "timeout_window_hash",
                "project_id",
                "principal_id",
                "fencing_token",
                "retired_at",
                "retired_at_us",
            ],
            "worker_exit_retry_dispatch_replacements": [
                "intent_id",
                "attempt_number",
                "task_id",
                "project_id",
                "principal_id",
                "approval_id",
                "source_outcome_document_hash",
                "source_handle_hash",
                "attempt_id",
                "observation_sequence",
                "evidence_hash",
                "handle_json",
                "handle_hash",
                "effective_outcome_json",
                "effective_outcome_hash",
                "fencing_token",
                "replaced_at",
                "replaced_at_us",
            ],
            "worker_exit_retry_exhaustions": [
                "intent_id",
                "attempt_number",
                "task_id",
                "project_id",
                "principal_id",
                "approval_id",
                "attempt_id",
                "observation_sequence",
                "evidence_hash",
                "private_schema_version",
                "private_proof_hash",
                "failure_kind",
                "max_attempts",
                "terminal_event_sequence",
                "terminal_event_hash",
                "fencing_token",
                "exhausted_at",
                "exhausted_at_us",
            ],
        }
        connection = self._connection()
        try:
            for table, columns in expected_columns.items():
                actual = [
                    row["name"]
                    for row in connection.execute(
                        f"PRAGMA table_info({table})"
                    ).fetchall()
                ]
                self.assertEqual(actual, columns, table)
        finally:
            connection.close()

    def test_v14_database_upgrades_in_place_to_v20(self) -> None:
        legacy_migrations = Path(self.temporary.name) / "v14-migrations"
        legacy_migrations.mkdir(mode=0o700)
        for migration in sorted(
            task_store_module.MIGRATIONS_DIRECTORY.glob("[0-9][0-9][0-9][0-9]_*.sql")
        ):
            if int(migration.name.split("_", 1)[0]) <= 14:
                shutil.copy2(migration, legacy_migrations / migration.name)

        legacy_database = Path(self.temporary.name) / "legacy-v14.sqlite3"
        with mock.patch.object(
            task_store_module,
            "MIGRATIONS_DIRECTORY",
            legacy_migrations,
        ):
            legacy = SQLiteTaskStore(legacy_database)
            self.assertEqual(legacy.migration_version(), 14)

        upgraded = SQLiteTaskStore(legacy_database)
        self.assertEqual(upgraded.migration_version(), 23)
        connection = sqlite3.connect(legacy_database)
        try:
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0], 23
            )
            self.assertEqual(
                connection.execute("PRAGMA foreign_key_check").fetchall(), []
            )
            self.assertEqual(
                connection.execute(
                    "SELECT name FROM schema_migrations WHERE version = 16"
                ).fetchone()[0],
                "0016_dispatch_negative_reconciliation.sql",
            )
        finally:
            connection.close()

    def test_v15_database_upgrades_in_place_to_v20(self) -> None:
        legacy_migrations = Path(self.temporary.name) / "v15-migrations"
        legacy_migrations.mkdir(mode=0o700)
        for migration in sorted(
            task_store_module.MIGRATIONS_DIRECTORY.glob(
                "[0-9][0-9][0-9][0-9]_*.sql"
            )
        ):
            if int(migration.name.split("_", 1)[0]) <= 15:
                shutil.copy2(migration, legacy_migrations / migration.name)

        legacy_database = Path(self.temporary.name) / "legacy-v15.sqlite3"
        with mock.patch.object(
            task_store_module,
            "MIGRATIONS_DIRECTORY",
            legacy_migrations,
        ):
            legacy = SQLiteTaskStore(legacy_database)
            self.assertEqual(legacy.migration_version(), 15)

        upgraded = SQLiteTaskStore(legacy_database)
        self.assertEqual(upgraded.migration_version(), 23)
        connection = sqlite3.connect(legacy_database)
        try:
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0], 23
            )
            self.assertEqual(
                connection.execute("PRAGMA foreign_key_check").fetchall(), []
            )
            self.assertEqual(
                connection.execute(
                    "SELECT name FROM schema_migrations WHERE version = 16"
                ).fetchone()[0],
                "0016_dispatch_negative_reconciliation.sql",
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM dispatch_reconciliation_observations"
                ).fetchone()[0],
                0,
            )
        finally:
            connection.close()

    def test_v15_effective_receipt_replacement_and_rows_are_immutable(
        self,
    ) -> None:
        task_id, _, _ = self._submitted_runtime(key="v15-replacement-view")
        intent = self.store.get_dispatch_intent(task_id)
        self.assertIsNotNone(intent)
        assert intent is not None and intent.handle is not None
        _, source_handle_hash = encode_document(intent.handle)
        replacement_handle = copy.deepcopy(intent.handle)
        replacement_handle["job_id"] = "job-worker-exit-retry-2"
        replacement_handle_json, replacement_handle_hash = encode_document(
            replacement_handle
        )
        replacement_outcome = {
            "status": "dispatched",
            "handle": replacement_handle,
            "recorded_at": T_PLUS_1,
        }
        replacement_outcome_json, replacement_outcome_hash = encode_document(
            replacement_outcome
        )
        digest = "sha256:" + "a" * 64

        connection = self._connection()
        try:
            source = connection.execute(
                "SELECT * FROM effective_dispatched_intents WHERE intent_id = ?",
                (intent.intent_id,),
            ).fetchone()
            self.assertIsNotNone(source)
            assert source is not None
            source_outcome_hash = source["outcome_document_hash"]
            self.assertEqual(source["source"], "direct")

            connection.execute("PRAGMA foreign_keys = OFF")
            for trigger in (
                "worker_exit_retry_reservation_requires_exact_case",
                "worker_exit_retry_reservation_requires_active_term",
                "worker_exit_retry_reservation_retires_timeout",
                "supervised_worker_exit_retry_attempt_requires_active_term",
                "worker_exit_retry_timeout_retirement_requires_exact_window",
                "worker_exit_retry_replacement_requires_exact_case",
                "worker_exit_retry_replacement_requires_active_term",
                "worker_exit_retry_exhaustion_requires_exact_case",
            ):
                connection.execute(f"DROP TRIGGER {trigger}")

            connection.execute(
                """
                INSERT INTO worker_exit_retry_reservations(
                    intent_id, attempt_number, task_id, project_id,
                    principal_id, approval_id, previous_attempt_id,
                    previous_observation_sequence, evidence_hash,
                    private_schema_version, private_proof_hash, failure_kind,
                    source_outcome_document_hash, source_handle_hash,
                    retry_event_sequence, retry_event_hash,
                    first_fencing_token, reserved_at, reserved_at_us
                ) VALUES (?, 2, ?, ?, ?, ?, ?, 1, ?, '1.1.0', ?,
                          'worker_exit', ?, ?, 1, ?, 1, ?, 1)
                """,
                (
                    intent.intent_id,
                    task_id,
                    PROJECT_ID,
                    PRINCIPAL_ID,
                    intent.approval_id,
                    "attempt-worker-exit-source",
                    digest,
                    digest,
                    source_outcome_hash,
                    source_handle_hash,
                    digest,
                    T_PLUS_1,
                ),
            )
            self.assertIsNone(
                connection.execute(
                    "SELECT 1 FROM effective_dispatched_intents WHERE intent_id = ?",
                    (intent.intent_id,),
                ).fetchone()
            )

            connection.execute(
                """
                INSERT INTO supervised_worker_exit_retry_attempts(
                    intent_id, attempt_number, project_id, principal_id,
                    fencing_token, authorized_at, authorized_at_us
                ) VALUES (?, 2, ?, ?, 1, ?, 1)
                """,
                (intent.intent_id, PROJECT_ID, PRINCIPAL_ID, T_PLUS_1),
            )
            connection.execute(
                """
                INSERT INTO worker_exit_retry_timeout_retirements(
                    timeout_id, intent_id, attempt_number, attempt_id,
                    timeout_window_hash, project_id, principal_id,
                    fencing_token, retired_at, retired_at_us
                ) VALUES (?, ?, 2, ?, ?, ?, ?, 1, ?, 1)
                """,
                (
                    "timeout-worker-exit-source",
                    intent.intent_id,
                    "attempt-worker-exit-source",
                    digest,
                    PROJECT_ID,
                    PRINCIPAL_ID,
                    T_PLUS_1,
                ),
            )
            connection.execute(
                """
                INSERT INTO worker_exit_retry_dispatch_replacements(
                    intent_id, attempt_number, task_id, project_id,
                    principal_id, approval_id, source_outcome_document_hash,
                    source_handle_hash, attempt_id, observation_sequence,
                    evidence_hash, handle_json, handle_hash,
                    effective_outcome_json, effective_outcome_hash,
                    fencing_token, replaced_at, replaced_at_us
                ) VALUES (?, 2, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, 1, ?, 1)
                """,
                (
                    intent.intent_id,
                    task_id,
                    PROJECT_ID,
                    PRINCIPAL_ID,
                    intent.approval_id,
                    source_outcome_hash,
                    source_handle_hash,
                    "attempt-worker-exit-retry-2",
                    digest,
                    replacement_handle_json,
                    replacement_handle_hash,
                    replacement_outcome_json,
                    replacement_outcome_hash,
                    T_PLUS_1,
                ),
            )
            effective = connection.execute(
                "SELECT * FROM effective_dispatched_intents WHERE intent_id = ?",
                (intent.intent_id,),
            ).fetchone()
            self.assertIsNotNone(effective)
            assert effective is not None
            self.assertEqual(effective["source"], "worker_exit_retry_replacement")
            self.assertEqual(
                effective["outcome_document_hash"], replacement_outcome_hash
            )
            self.assertEqual(
                effective["outcome_document_json"], replacement_outcome_json
            )

            connection.execute(
                """
                INSERT INTO worker_exit_retry_exhaustions(
                    intent_id, attempt_number, task_id, project_id,
                    principal_id, approval_id, attempt_id,
                    observation_sequence, evidence_hash,
                    private_schema_version, private_proof_hash, failure_kind,
                    max_attempts, terminal_event_sequence,
                    terminal_event_hash, fencing_token, exhausted_at,
                    exhausted_at_us
                ) VALUES (?, 2, ?, ?, ?, ?, ?, 1, ?, '1.3.0', ?,
                          'worker_exit', 2, 2, ?, 1, ?, 2)
                """,
                (
                    intent.intent_id,
                    task_id,
                    PROJECT_ID,
                    PRINCIPAL_ID,
                    intent.approval_id,
                    "attempt-worker-exit-retry-2",
                    digest,
                    digest,
                    digest,
                    T_PLUS_1,
                ),
            )
            connection.commit()

            tables = (
                "worker_exit_retry_reservations",
                "supervised_worker_exit_retry_attempts",
                "worker_exit_retry_timeout_retirements",
                "worker_exit_retry_dispatch_replacements",
                "worker_exit_retry_exhaustions",
            )
            for table in tables:
                with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                    connection.execute(
                        f"UPDATE {table} SET project_id = 'tampered'"
                    )
                connection.rollback()
                with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                    connection.execute(f"DELETE FROM {table}")
                connection.rollback()
        finally:
            connection.rollback()
            connection.close()

    def test_v15_reservation_retires_an_armed_attempt_one_timeout(
        self,
    ) -> None:
        (
            task_id,
            _,
            _,
            intent,
            lease,
            armed,
            _,
        ) = self._timeout_runtime(key="v15-timeout-retirement")
        _, source_handle_hash = encode_document(intent.handle)

        connection = self._connection()
        try:
            source = connection.execute(
                """
                SELECT outcome_document_hash
                FROM effective_dispatched_intents WHERE intent_id = ?
                """,
                (intent.intent_id,),
            ).fetchone()
            observation = connection.execute(
                """
                SELECT observation.observation_sequence,
                       observation.document_hash
                FROM worker_attempt_timeout_windows AS timeout
                JOIN worker_attempt_observations AS observation
                  ON observation.attempt_id = timeout.attempt_id
                 AND observation.observation_sequence
                     = timeout.start_observation_sequence
                WHERE timeout.timeout_id = ?
                """,
                (armed.timeout.timeout_id,),
            ).fetchone()
            self.assertIsNotNone(source)
            self.assertIsNotNone(observation)
            assert source is not None and observation is not None

            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute(
                "DROP TRIGGER worker_exit_retry_reservation_requires_exact_case"
            )
            connection.execute(
                "DROP TRIGGER worker_exit_retry_reservation_requires_active_term"
            )
            digest = "sha256:" + "b" * 64
            connection.execute(
                """
                INSERT INTO worker_exit_retry_reservations(
                    intent_id, attempt_number, task_id, project_id,
                    principal_id, approval_id, previous_attempt_id,
                    previous_observation_sequence, evidence_hash,
                    private_schema_version, private_proof_hash, failure_kind,
                    source_outcome_document_hash, source_handle_hash,
                    retry_event_sequence, retry_event_hash,
                    first_fencing_token, reserved_at, reserved_at_us
                ) VALUES (?, 2, ?, ?, ?, ?, ?, ?, ?, '1.1.0', ?,
                          'worker_exit', ?, ?, 1, ?, ?, ?, 1)
                """,
                (
                    intent.intent_id,
                    task_id,
                    PROJECT_ID,
                    PRINCIPAL_ID,
                    intent.approval_id,
                    armed.timeout.attempt_id,
                    observation["observation_sequence"],
                    observation["document_hash"],
                    digest,
                    source["outcome_document_hash"],
                    source_handle_hash,
                    digest,
                    lease.fencing_token,
                    T_PLUS_1,
                ),
            )
            retirement = connection.execute(
                """
                SELECT * FROM worker_exit_retry_timeout_retirements
                WHERE timeout_id = ?
                """,
                (armed.timeout.timeout_id,),
            ).fetchone()
            self.assertIsNotNone(retirement)
            assert retirement is not None
            self.assertEqual(retirement["attempt_id"], armed.timeout.attempt_id)
            self.assertEqual(
                retirement["timeout_window_hash"],
                connection.execute(
                    """
                    SELECT document_hash FROM worker_attempt_timeout_windows
                    WHERE timeout_id = ?
                    """,
                    (armed.timeout.timeout_id,),
                ).fetchone()[0],
            )
            deadline_at_us = connection.execute(
                """
                SELECT deadline_at_us FROM worker_attempt_timeout_windows
                WHERE timeout_id = ?
                """,
                (armed.timeout.timeout_id,),
            ).fetchone()[0]
            connection.commit()

            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "due pending window",
            ):
                connection.execute(
                    """
                    INSERT INTO supervised_timeout_attempts(
                        timeout_id, project_id, principal_id, intent_id,
                        attempt_id, fencing_token, action,
                        authorized_at, authorized_at_us
                    ) VALUES (?, ?, ?, ?, ?, ?,
                              'deliver_exact_attempt_timeout', ?, ?)
                    """,
                    (
                        armed.timeout.timeout_id,
                        PROJECT_ID,
                        PRINCIPAL_ID,
                        intent.intent_id,
                        armed.timeout.attempt_id,
                        lease.fencing_token,
                        T_PLUS_10,
                        deadline_at_us + 1,
                    ),
                )
            connection.rollback()
            self.assertEqual(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM worker_attempt_timeout_windows AS timeout
                    WHERE timeout.task_id = ?
                      AND NOT EXISTS (
                          SELECT 1
                          FROM worker_exit_retry_timeout_retirements AS retirement
                          WHERE retirement.timeout_id = timeout.timeout_id
                      )
                    """,
                    (task_id,),
                ).fetchone()[0],
                0,
            )
        finally:
            connection.rollback()
            connection.close()

    def test_v15_mixed_worker_exit_exhaustion_projects_purge_cleanup_token(
        self,
    ) -> None:
        dispatcher = WorkerExitRetryFakeDispatcher(
            self.store,
            second_attempt_outcome="pre_running_failure",
        )
        task_id, dispatcher, runtime, _ = self._pending_runtime(
            key="v15-mixed-exhaustion-purge-proof",
            dispatcher=dispatcher,
        )
        lease = self._acquire(
            "v15-mixed-exhaustion-owner",
            lease_seconds=30,
        ).lease
        scheduled = runtime.schedule_runtime_dispatch(
            task_id,
            **self.scope,
            supervisor_lease=lease,
        )
        self.assertEqual(scheduled.intent.state, "dispatched")
        dispatcher.adapter_status = {
            "status": "Failed",
            "stage": "worker_exit",
            "completed": 0,
            "total": scheduled.intent.request["parameters"]["iterations"],
            "message": "FWI Worker exited after ready",
            "updated_at": NOW,
            "terminal": True,
        }
        retrying = runtime.process_runtime_retry(
            task_id,
            **self.scope,
            supervisor_lease=lease,
        )
        self.assertEqual(retrying.state, "retrying")
        exhausted = runtime.process_runtime_retry(
            task_id,
            **self.scope,
            supervisor_lease=lease,
        )
        self.assertEqual(exhausted.state, "exhausted")
        self.assertEqual(exhausted.snapshot.status, "Failed")
        self.assertEqual(exhausted.intent.state, "retry_exhausted")
        self.assertIsNone(exhausted.intent.handle)

        self.now[0] = T_PLUS_10
        trashed = runtime.trash_task(
            task_id=task_id,
            expected_visibility_revision=0,
            idempotency_key="trash-v15-mixed-exhaustion",
            **self.scope,
        )
        self.assertEqual(trashed.snapshot.visibility_revision, 1)
        _, purge_request_hash = encode_document(
            {
                "task_id": task_id,
                "project_id": PROJECT_ID,
                "principal_id": PRINCIPAL_ID,
                "action": "purge_task",
                "expected_visibility_revision": 1,
            }
        )
        purge = self.store.reserve_task_purge(
            task_id=task_id,
            expected_visibility_revision=1,
            idempotency_key="purge-v15-mixed-exhaustion",
            request_hash=purge_request_hash,
            now=T_PLUS_10,
            **self.scope,
        )
        proof = self.store.get_retry_exhaustion_cleanup_proof(
            purge_id=purge.purge_id,
            task_id=task_id,
            **self.scope,
        )
        self.assertIsNotNone(proof)
        assert proof is not None
        self.assertEqual(proof.private_schema_version, "1.3.0")
        self.assertEqual(proof.failure_kind, "pre_running_launch_failure")
        self.assertEqual(proof.previous_failure_kind, "worker_exit")
        self.assertEqual(proof.previous_private_schema_version, "1.1.0")
        token = proof.adapter_token()
        expected_keys = {
            "schema_version",
            "purge_id",
            "intent_id",
            "task_id",
            "project_id",
            "principal_id",
            "approval_id",
            "attempt_id",
            "attempt_number",
            "observation_sequence",
            "evidence",
            "evidence_hash",
            "private_schema_version",
            "private_proof_hash",
            "failure_kind",
            "previous_attempt_id",
            "previous_observation_sequence",
            "previous_private_proof_hash",
            "previous_failure_kind",
            "previous_private_schema_version",
            "retry_reserved_at",
            "terminal_event_sequence",
            "terminal_event_hash",
            "exhausted_at",
            "proof_hash",
        }
        self.assertEqual(set(token), expected_keys)
        self.assertEqual(token["schema_version"], "1.1.0")
        self.assertEqual(token["previous_failure_kind"], "worker_exit")
        self.assertEqual(token["previous_private_schema_version"], "1.1.0")
        payload = {key: value for key, value in token.items() if key != "proof_hash"}
        self.assertEqual(token["proof_hash"], encode_document(payload)[1])
        replay = self.store.get_retry_exhaustion_cleanup_proof(
            purge_id=purge.purge_id,
            task_id=task_id,
            **self.scope,
        )
        self.assertIsNotNone(replay)
        assert replay is not None
        self.assertEqual(replay.adapter_token(), token)

    def test_retry_reservation_is_single_use_and_replays_across_terms(self) -> None:
        task_id, _, _, pending = self._pending_runtime(
            key="retry-reservation-across-terms"
        )
        first = self._acquire("retry-reservation-owner", lease_seconds=30).lease
        claimed = self.store.authorize_supervised_dispatch(
            intent_id=pending.intent_id,
            reason="pending_first_dispatch",
            supervisor_lease=first,
            supervisor_clock=lambda: NOW,
        ).intent
        self.assertEqual(claimed.state, "dispatching")

        first_attempt_id = "attempt-" + "a" * 32
        stopped = self.store.record_supervised_worker_observation(
            intent_id=claimed.intent_id,
            evidence=managed_worker_evidence(
                ticket_state="failed",
                heartbeat_sequence=None,
                attempt_id=first_attempt_id,
            ),
            handle=None,
            supervisor_lease=first,
            supervisor_clock=lambda: NOW,
        )
        private_proof_hash = "sha256:" + "9" * 64
        reserved = self.store.authorize_supervised_retry(
            intent_id=claimed.intent_id,
            previous_attempt_id=stopped.attempt_id,
            previous_observation_sequence=stopped.observation_sequence,
            failure_kind="pre_running_launch_failure",
            private_proof_hash=private_proof_hash,
            supervisor_lease=first,
            supervisor_clock=lambda: NOW,
        )
        self.assertFalse(reserved.reservation_replayed)
        self.assertFalse(reserved.authorization_replayed)
        stable_token = reserved.adapter_token()
        self.assertEqual(stable_token["next_attempt_number"], 2)

        replay = self.store.authorize_supervised_retry(
            intent_id=claimed.intent_id,
            previous_attempt_id=stopped.attempt_id,
            previous_observation_sequence=stopped.observation_sequence,
            failure_kind="pre_running_launch_failure",
            private_proof_hash=private_proof_hash,
            supervisor_lease=first,
            supervisor_clock=lambda: NOW,
        )
        self.assertTrue(replay.reservation_replayed)
        self.assertTrue(replay.authorization_replayed)
        self.assertEqual(replay.adapter_token(), stable_token)
        with self.assertRaisesRegex(TaskStoreConflict, "differs"):
            self.store.authorize_supervised_retry(
                intent_id=claimed.intent_id,
                previous_attempt_id=stopped.attempt_id,
                previous_observation_sequence=stopped.observation_sequence,
                failure_kind="pre_running_launch_failure",
                private_proof_hash="sha256:" + "8" * 64,
                supervisor_lease=first,
                supervisor_clock=lambda: NOW,
            )

        self.store.release_runtime_supervisor_lease(
            lease=first,
            clock=lambda: T_PLUS_1,
        )
        successor = self._acquire(
            "retry-reservation-successor",
            now=T_PLUS_1,
            lease_seconds=30,
        ).lease
        redelivered = self.store.authorize_supervised_retry(
            intent_id=claimed.intent_id,
            previous_attempt_id=stopped.attempt_id,
            previous_observation_sequence=stopped.observation_sequence,
            failure_kind="pre_running_launch_failure",
            private_proof_hash=private_proof_hash,
            supervisor_lease=successor,
            supervisor_clock=lambda: T_PLUS_1,
        )
        self.assertTrue(redelivered.reservation_replayed)
        self.assertFalse(redelivered.authorization_replayed)
        self.assertEqual(redelivered.adapter_token(), stable_token)

        second_attempt_id = "attempt-" + "b" * 32
        staged = self.store.record_supervised_worker_observation(
            intent_id=claimed.intent_id,
            evidence=managed_worker_evidence(
                ticket_state="staged",
                heartbeat_sequence=None,
                attempt_id=second_attempt_id,
                attempt_number=2,
                job_id="fwi-20260715T030001Z-000000000002",
                created_at=stable_token["authorized_at"],
            ),
            handle=None,
            supervisor_lease=successor,
            supervisor_clock=lambda: T_PLUS_1,
        )
        self.assertEqual(staged.attempt_id, second_attempt_id)
        with self.assertRaisesRegex(
            TaskStoreConflict,
            "staged resume requires exact pre-Popen Worker evidence",
        ):
            self.store.authorize_supervised_dispatch(
                intent_id=claimed.intent_id,
                reason="staged_attempt_resume",
                supervisor_lease=successor,
                supervisor_clock=lambda: T_PLUS_1,
            )
        resumed = self.store.resume_supervised_retry(
            intent_id=claimed.intent_id,
            supervisor_lease=successor,
            supervisor_clock=lambda: T_PLUS_1,
        )
        self.assertTrue(resumed.reservation_replayed)
        self.assertTrue(resumed.authorization_replayed)
        self.assertEqual(resumed.adapter_token(), stable_token)

        connection = self._connection()
        try:
            attempts = connection.execute(
                """
                SELECT attempt_number FROM worker_launch_attempts
                WHERE intent_id = ? ORDER BY attempt_number
                """,
                (claimed.intent_id,),
            ).fetchall()
            self.assertEqual([row[0] for row in attempts], [1, 2])
            self.assertEqual(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM worker_retry_reservations
                    WHERE intent_id = ?
                    """,
                    (claimed.intent_id,),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM supervised_retry_attempts
                    WHERE intent_id = ?
                    """,
                    (claimed.intent_id,),
                ).fetchone()[0],
                2,
            )
        finally:
            connection.close()

    def test_v8_runtime_with_active_lease_upgrades_in_place_to_v21(self) -> None:
        task_id, _, _ = self._submitted_runtime(key="upgrade-v8-v9")
        acquired = self._acquire("upgrade-owner", lease_seconds=30)
        self.assertTrue(acquired.acquired)
        connection = self._connection()
        try:
            self._drop_dag_schema(connection)
            self._drop_checkpoint_schema(connection)
            self._drop_negative_reconciliation_schema(connection)
            self._drop_retry_schema(connection)
            connection.execute("DROP TABLE dispatch_reconciliation_resolutions")
            connection.execute(
                "DROP TABLE supervised_dispatch_reconciliation_attempts"
            )
            connection.execute(
                "DROP TRIGGER task_cancel_request_rejects_authorized_timeout"
            )
            connection.execute("DROP TABLE task_timeout_outcomes")
            connection.execute("DROP TABLE supervised_timeout_attempts")
            connection.execute("DROP TABLE worker_attempt_timeout_windows")
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
        self.assertEqual(reopened.migration_version(), 23)
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

    def test_v10_runtime_with_active_lease_upgrades_in_place_to_v21(self) -> None:
        task_id, _, _ = self._submitted_runtime(key="upgrade-v10-v12")
        acquired = self._acquire("upgrade-v12-owner", lease_seconds=30)
        self.assertTrue(acquired.acquired)
        connection = self._connection()
        try:
            self._drop_dag_schema(connection)
            self._drop_checkpoint_schema(connection)
            self._drop_negative_reconciliation_schema(connection)
            self._drop_retry_schema(connection)
            connection.execute("DROP TABLE dispatch_reconciliation_resolutions")
            connection.execute(
                "DROP TABLE supervised_dispatch_reconciliation_attempts"
            )
            connection.execute(
                "DROP TRIGGER task_cancel_request_rejects_authorized_timeout"
            )
            connection.execute("DROP TABLE task_timeout_outcomes")
            connection.execute("DROP TABLE supervised_timeout_attempts")
            connection.execute("DROP TABLE worker_attempt_timeout_windows")
            connection.execute(
                "DROP TRIGGER task_cancel_request_blocks_supervised_dispatch"
            )
            connection.execute("DROP TABLE task_cancel_outcomes")
            connection.execute("DROP TABLE supervised_cancel_attempts")
            connection.execute("DROP TABLE task_cancel_requests")
            connection.execute("DELETE FROM schema_migrations WHERE version >= 11")
            connection.execute("PRAGMA user_version = 10")
            connection.commit()
        finally:
            connection.close()

        reopened = SQLiteTaskStore(self.database_path)
        self.assertEqual(reopened.migration_version(), 23)
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

    def test_unresolved_reconciliation_observations_are_append_only(self) -> None:
        task_id, _, _, reconciled = self._reconciliation_runtime(
            key="negative-observation-matrix"
        )
        lease = self._acquire(
            "negative-observation-owner", lease_seconds=30
        ).lease
        connection = self._connection()
        try:
            outcome_before = tuple(
                connection.execute(
                    "SELECT outcome, document_json, document_hash, recorded_at "
                    "FROM dispatch_outcomes WHERE intent_id = ?",
                    (reconciled.intent_id,),
                ).fetchone()
            )
            budget_before = tuple(
                connection.execute(
                    "SELECT max_tasks, tasks_used FROM approval_budgets "
                    "WHERE task_id = ? AND approval_id = ?",
                    (task_id, reconciled.approval_id),
                ).fetchone()
            )
            event_count_before = connection.execute(
                "SELECT COUNT(*) FROM run_events WHERE task_id = ?",
                (task_id,),
            ).fetchone()[0]
        finally:
            connection.close()

        transient = (
            self.store.record_supervised_dispatch_reconciliation_observation(
                intent_id=reconciled.intent_id,
                classification="transient",
                failure_code="ADAPTER_PROBE_UNAVAILABLE",
                supervisor_lease=lease,
                supervisor_clock=lambda: NOW,
            )
        )
        self.assertFalse(transient.replayed)
        self.assertEqual(transient.observation_sequence, 1)
        replay = (
            self.store.record_supervised_dispatch_reconciliation_observation(
                intent_id=reconciled.intent_id,
                classification="transient",
                failure_code="ADAPTER_PROBE_UNAVAILABLE",
                supervisor_lease=lease,
                supervisor_clock=lambda: T_PLUS_1,
            )
        )
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.observation_sequence, 1)
        uncertain = (
            self.store.record_supervised_dispatch_reconciliation_observation(
                intent_id=reconciled.intent_id,
                classification="uncertain",
                failure_code="PRIVATE_STATE_UNCERTAIN",
                supervisor_lease=lease,
                supervisor_clock=lambda: T_PLUS_1,
            )
        )
        self.assertFalse(uncertain.replayed)
        self.assertEqual(uncertain.observation_sequence, 2)
        self.assertEqual(uncertain.intent.state, "reconciliation_required")
        self.assertIsNotNone(uncertain.intent.reconciliation)
        assert uncertain.intent.reconciliation is not None
        self.assertEqual(
            uncertain.intent.reconciliation.action_classification,
            "uncertain",
        )
        self.assertEqual(
            uncertain.intent.reconciliation.action_failure_code,
            "PRIVATE_STATE_UNCERTAIN",
        )

        connection = self._connection()
        try:
            rows = connection.execute(
                "SELECT observation_sequence, classification, failure_code "
                "FROM dispatch_reconciliation_observations "
                "WHERE intent_id = ? ORDER BY observation_sequence",
                (reconciled.intent_id,),
            ).fetchall()
            self.assertEqual(
                [tuple(row) for row in rows],
                [
                    (1, "transient", "ADAPTER_PROBE_UNAVAILABLE"),
                    (2, "uncertain", "PRIVATE_STATE_UNCERTAIN"),
                ],
            )
            self.assertEqual(
                tuple(
                    connection.execute(
                        "SELECT outcome, document_json, document_hash, "
                        "recorded_at FROM dispatch_outcomes "
                        "WHERE intent_id = ?",
                        (reconciled.intent_id,),
                    ).fetchone()
                ),
                outcome_before,
            )
            self.assertEqual(
                tuple(
                    connection.execute(
                        "SELECT max_tasks, tasks_used FROM approval_budgets "
                        "WHERE task_id = ? AND approval_id = ?",
                        (task_id, reconciled.approval_id),
                    ).fetchone()
                ),
                budget_before,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM run_events WHERE task_id = ?",
                    (task_id,),
                ).fetchone()[0],
                event_count_before,
            )
        finally:
            connection.close()

    def test_exact_negative_reconciliation_is_atomic_and_replay_safe(
        self,
    ) -> None:
        task_id, _, _, reconciled = self._reconciliation_runtime(
            key="negative-exact-close"
        )
        lease = self._acquire("negative-exact-owner", lease_seconds=30).lease
        self.store.record_supervised_dispatch_reconciliation_observation(
            intent_id=reconciled.intent_id,
            classification="uncertain",
            failure_code="PRIVATE_STATE_UNCERTAIN",
            supervisor_lease=lease,
            supervisor_clock=lambda: NOW,
        )
        inputs = self._negative_reconciliation_inputs(reconciled)
        connection = self._connection()
        try:
            outcome_before = tuple(
                connection.execute(
                    "SELECT outcome, document_json, document_hash, recorded_at "
                    "FROM dispatch_outcomes WHERE intent_id = ?",
                    (reconciled.intent_id,),
                ).fetchone()
            )
            budget_before = tuple(
                connection.execute(
                    "SELECT max_tasks, tasks_used FROM approval_budgets "
                    "WHERE task_id = ? AND approval_id = ?",
                    (task_id, reconciled.approval_id),
                ).fetchone()
            )
        finally:
            connection.close()

        closed = self.store.finalize_supervised_negative_dispatch_reconciliation(
            **inputs,
            supervisor_lease=lease,
            supervisor_clock=lambda: T_PLUS_1,
        )
        self.assertFalse(closed.replayed)
        self.assertEqual(closed.snapshot.status, "Failed")
        self.assertEqual(closed.intent.state, "not_dispatched")
        self.assertEqual(closed.intent.failure_code, "DISPATCH_NOT_STARTED")
        self.assertIsNone(closed.intent.handle)
        self.assertIsNotNone(closed.intent.reconciliation)
        assert closed.intent.reconciliation is not None
        self.assertEqual(closed.intent.reconciliation.state, "resolved")
        self.assertEqual(
            closed.intent.reconciliation.result, "not_dispatched"
        )
        replay = self.store.finalize_supervised_negative_dispatch_reconciliation(
            **inputs,
            supervisor_lease=lease,
            supervisor_clock=lambda: T_PLUS_1,
        )
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.terminal_event_sequence, 2)

        connection = self._connection()
        try:
            self.assertEqual(
                tuple(
                    connection.execute(
                        "SELECT outcome, document_json, document_hash, "
                        "recorded_at FROM dispatch_outcomes "
                        "WHERE intent_id = ?",
                        (reconciled.intent_id,),
                    ).fetchone()
                ),
                outcome_before,
            )
            self.assertEqual(
                tuple(
                    connection.execute(
                        "SELECT max_tasks, tasks_used FROM approval_budgets "
                        "WHERE task_id = ? AND approval_id = ?",
                        (task_id, reconciled.approval_id),
                    ).fetchone()
                ),
                budget_before,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM run_events WHERE task_id = ?",
                    (task_id,),
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM dispatch_reconciliation_observations "
                    "WHERE intent_id = ?",
                    (reconciled.intent_id,),
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM "
                    "dispatch_reconciliation_negative_resolutions "
                    "WHERE intent_id = ?",
                    (reconciled.intent_id,),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM worker_retry_reservations "
                    "WHERE intent_id = ?",
                    (reconciled.intent_id,),
                ).fetchone()[0],
                0,
            )
            resolution = connection.execute(
                "SELECT approval_tasks_used, approval_budget_refunded "
                "FROM dispatch_reconciliation_negative_resolutions "
                "WHERE intent_id = ?",
                (reconciled.intent_id,),
            ).fetchone()
            self.assertEqual(
                tuple(resolution), (budget_before[1], 0)
            )
            with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                connection.execute(
                    "UPDATE dispatch_reconciliation_negative_resolutions "
                    "SET result = 'not_dispatched' WHERE intent_id = ?",
                    (reconciled.intent_id,),
                )
        finally:
            connection.rollback()
            connection.close()

    def test_negative_reconciliation_rejects_stale_term_without_partial_rows(
        self,
    ) -> None:
        task_id, _, _, reconciled = self._reconciliation_runtime(
            key="negative-stale-term"
        )
        first = self._acquire(
            "negative-stale-owner", lease_seconds=1
        ).lease
        self._acquire(
            "negative-takeover-owner", now=T_PLUS_5, lease_seconds=30
        )
        inputs = self._negative_reconciliation_inputs(reconciled)
        with self.assertRaises(RuntimeSupervisorLeaseLost):
            self.store.finalize_supervised_negative_dispatch_reconciliation(
                **inputs,
                supervisor_lease=first,
                supervisor_clock=lambda: T_PLUS_5,
            )
        connection = self._connection()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM dispatch_reconciliation_observations "
                    "WHERE intent_id = ?",
                    (reconciled.intent_id,),
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM "
                    "dispatch_reconciliation_negative_resolutions "
                    "WHERE intent_id = ?",
                    (reconciled.intent_id,),
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM run_events WHERE task_id = ?",
                    (task_id,),
                ).fetchone()[0],
                1,
            )
        finally:
            connection.close()

    def test_positive_and_negative_reconciliation_have_one_winner(self) -> None:
        _, dispatcher, _, reconciled = self._reconciliation_runtime(
            key="negative-positive-race"
        )
        lease = self._acquire("negative-race-owner", lease_seconds=30).lease
        self.store.authorize_supervised_dispatch_reconciliation(
            intent_id=reconciled.intent_id,
            evidence_kind="managed_worker_receipt",
            supervisor_lease=lease,
            supervisor_clock=lambda: NOW,
        )
        negative = self._negative_reconciliation_inputs(reconciled)
        positive_attempt_id = "attempt-" + "9" * 32
        handle = dispatcher.recover_existing_receipt(reconciled)
        barrier = threading.Barrier(2)

        def close_negative():
            barrier.wait()
            return self.store.finalize_supervised_negative_dispatch_reconciliation(
                **negative,
                supervisor_lease=lease,
                supervisor_clock=lambda: T_PLUS_1,
            )

        def close_positive():
            barrier.wait()
            return self.store.record_supervised_worker_observation(
                intent_id=reconciled.intent_id,
                evidence=managed_worker_evidence(
                    attempt_id=positive_attempt_id
                ),
                handle=handle,
                supervisor_lease=lease,
                supervisor_clock=lambda: T_PLUS_1,
            )

        outcomes: list[object] = []
        failures: list[Exception] = []
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(close_negative),
                executor.submit(close_positive),
            ]
            for future in futures:
                try:
                    outcomes.append(future.result())
                except Exception as error:  # exact loser is timing-dependent
                    failures.append(error)
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], TaskStoreConflict)
        projected = self.store.get_dispatch_intent(reconciled.task_id)
        self.assertIsNotNone(projected)
        assert projected is not None
        self.assertIn(projected.state, {"dispatched", "not_dispatched"})
        connection = self._connection()
        try:
            positive_count = connection.execute(
                "SELECT COUNT(*) FROM dispatch_reconciliation_resolutions "
                "WHERE intent_id = ?",
                (reconciled.intent_id,),
            ).fetchone()[0]
            negative_count = connection.execute(
                "SELECT COUNT(*) FROM "
                "dispatch_reconciliation_negative_resolutions "
                "WHERE intent_id = ?",
                (reconciled.intent_id,),
            ).fetchone()[0]
            self.assertEqual(positive_count + negative_count, 1)
        finally:
            connection.close()

    def test_managed_proof_resolves_reconciliation_without_rewriting_outcome(
        self,
    ) -> None:
        task_id, dispatcher, _, reconciled = self._reconciliation_runtime(
            key="managed-reconciliation"
        )
        connection = self._connection()
        try:
            original = tuple(
                connection.execute(
                    """
                    SELECT outcome, document_json, document_hash, recorded_at
                    FROM dispatch_outcomes WHERE intent_id = ?
                    """,
                    (reconciled.intent_id,),
                ).fetchone()
            )
        finally:
            connection.close()

        lease = self._acquire(
            "managed-reconciliation-owner", lease_seconds=30
        ).lease
        authorization = self.store.authorize_supervised_dispatch_reconciliation(
            intent_id=reconciled.intent_id,
            evidence_kind="managed_worker_receipt",
            supervisor_lease=lease,
            supervisor_clock=lambda: NOW,
        )
        self.assertFalse(authorization.replayed)
        handle = dispatcher.recover_existing_receipt(reconciled)
        attempt_id = "attempt-" + hashlib.sha256(task_id.encode()).hexdigest()[:32]
        projected = self.store.record_supervised_worker_observation(
            intent_id=reconciled.intent_id,
            evidence=managed_worker_evidence(attempt_id=attempt_id),
            handle=handle,
            supervisor_lease=lease,
            supervisor_clock=lambda: T_PLUS_1,
        )
        self.assertTrue(projected.adopted)
        self.assertFalse(projected.replayed)
        self.assertEqual(projected.intent.state, "dispatched")
        self.assertIsNotNone(projected.intent.reconciliation)
        assert projected.intent.reconciliation is not None
        self.assertEqual(projected.intent.reconciliation.state, "resolved")
        self.assertEqual(
            projected.intent.reconciliation.evidence_kind,
            "managed_worker_receipt",
        )
        self.assertIsNotNone(self.store.get_task_cancel_candidate(task_id))
        replayed_outcome = self.store.record_dispatch_reconciliation(
            intent_id=reconciled.intent_id,
            failure_code="SUBMISSION_RECONCILIATION_REQUIRED",
            now=NOW,
        )
        self.assertEqual(replayed_outcome.state, "dispatched")

        connection = self._connection()
        try:
            self.assertEqual(
                tuple(
                    connection.execute(
                        """
                        SELECT outcome, document_json, document_hash, recorded_at
                        FROM dispatch_outcomes WHERE intent_id = ?
                        """,
                        (reconciled.intent_id,),
                    ).fetchone()
                ),
                original,
            )
            resolution = connection.execute(
                """
                SELECT source_outcome_hash, evidence_kind, attempt_id,
                       observation_sequence, fencing_token
                FROM dispatch_reconciliation_resolutions
                WHERE intent_id = ?
                """,
                (reconciled.intent_id,),
            ).fetchone()
            self.assertEqual(resolution["source_outcome_hash"], original[2])
            self.assertEqual(
                resolution["evidence_kind"], "managed_worker_receipt"
            )
            self.assertEqual(resolution["attempt_id"], attempt_id)
            self.assertEqual(resolution["observation_sequence"], 1)
            self.assertEqual(resolution["fencing_token"], lease.fencing_token)
            effective = connection.execute(
                """
                SELECT source FROM effective_dispatched_intents
                WHERE intent_id = ?
                """,
                (reconciled.intent_id,),
            ).fetchone()
            self.assertEqual(effective["source"], "reconciliation")
            with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                connection.execute(
                    """
                    UPDATE dispatch_reconciliation_resolutions
                    SET result = 'dispatched' WHERE intent_id = ?
                    """,
                    (reconciled.intent_id,),
                )
        finally:
            connection.rollback()
            connection.close()

    def test_private_proof_resolves_reconciliation_and_rejects_managed_rebind(
        self,
    ) -> None:
        task_id, dispatcher, _, reconciled = self._reconciliation_runtime(
            key="private-reconciliation", algorithm_version="1.4.0"
        )
        connection = self._connection()
        try:
            original = tuple(
                connection.execute(
                    """
                    SELECT outcome, document_json, document_hash, recorded_at
                    FROM dispatch_outcomes WHERE intent_id = ?
                    """,
                    (reconciled.intent_id,),
                ).fetchone()
            )
        finally:
            connection.close()
        lease = self._acquire(
            "private-reconciliation-owner", lease_seconds=30
        ).lease
        self.store.authorize_supervised_dispatch_reconciliation(
            intent_id=reconciled.intent_id,
            evidence_kind="private_receipt",
            supervisor_lease=lease,
            supervisor_clock=lambda: NOW,
        )
        receipt = dispatcher.recover_existing_private_receipt(reconciled)
        adoption = self.store.record_supervised_private_receipt_adoption(
            intent_id=reconciled.intent_id,
            handle=receipt["handle"],
            private_schema_version=receipt["private_schema_version"],
            receipt_record_hash=receipt["receipt_record_hash"],
            supervisor_lease=lease,
            supervisor_clock=lambda: T_PLUS_1,
        )
        self.assertTrue(adoption.adopted)
        self.assertEqual(adoption.intent.state, "dispatched")
        self.assertEqual(
            adoption.intent.reconciliation.evidence_kind,
            "private_receipt",
        )
        replay = self.store.record_supervised_private_receipt_adoption(
            intent_id=reconciled.intent_id,
            handle=receipt["handle"],
            private_schema_version=receipt["private_schema_version"],
            receipt_record_hash=receipt["receipt_record_hash"],
            supervisor_lease=lease,
            supervisor_clock=lambda: T_PLUS_1,
        )
        self.assertTrue(replay.replayed)
        self.assertFalse(replay.adopted)
        with self.assertRaisesRegex(TaskStoreConflict, "private receipt"):
            self.store.record_supervised_worker_observation(
                intent_id=reconciled.intent_id,
                evidence=managed_worker_evidence(
                    attempt_id="attempt-" + "1" * 32
                ),
                handle=dispatcher.recover_existing_receipt(reconciled),
                supervisor_lease=lease,
                supervisor_clock=lambda: T_PLUS_1,
            )

        connection = self._connection()
        try:
            after = tuple(
                connection.execute(
                    """
                    SELECT outcome, document_json, document_hash, recorded_at
                    FROM dispatch_outcomes WHERE intent_id = ?
                    """,
                    (reconciled.intent_id,),
                ).fetchone()
            )
            self.assertEqual(after, original)
            self.assertEqual(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM worker_launch_attempts
                    WHERE task_id = ?
                    """,
                    (task_id,),
                ).fetchone()[0],
                0,
            )
        finally:
            connection.close()

    def test_reconciliation_adoption_and_resolution_roll_back_together(
        self,
    ) -> None:
        _, dispatcher, _, private_intent = self._reconciliation_runtime(
            key="private-reconciliation-rollback",
            algorithm_version="1.4.0",
        )
        lease = self._acquire(
            "reconciliation-rollback-owner", lease_seconds=30
        ).lease
        self.store.authorize_supervised_dispatch_reconciliation(
            intent_id=private_intent.intent_id,
            evidence_kind="private_receipt",
            supervisor_lease=lease,
            supervisor_clock=lambda: NOW,
        )
        receipt = dispatcher.recover_existing_private_receipt(private_intent)
        with mock.patch.object(
            SQLiteTaskStore,
            "_insert_dispatch_reconciliation_resolution",
            side_effect=TaskStoreConflict("injected resolution fault"),
        ):
            with self.assertRaisesRegex(TaskStoreConflict, "injected"):
                self.store.record_supervised_private_receipt_adoption(
                    intent_id=private_intent.intent_id,
                    handle=receipt["handle"],
                    private_schema_version=receipt["private_schema_version"],
                    receipt_record_hash=receipt["receipt_record_hash"],
                    supervisor_lease=lease,
                    supervisor_clock=lambda: T_PLUS_1,
                )

        _, managed_dispatcher, _, managed_intent = self._reconciliation_runtime(
            key="managed-reconciliation-rollback"
        )
        self.store.authorize_supervised_dispatch_reconciliation(
            intent_id=managed_intent.intent_id,
            evidence_kind="managed_worker_receipt",
            supervisor_lease=lease,
            supervisor_clock=lambda: NOW,
        )
        attempt_id = "attempt-" + "2" * 32
        with mock.patch.object(
            SQLiteTaskStore,
            "_insert_dispatch_reconciliation_resolution",
            side_effect=TaskStoreConflict("injected resolution fault"),
        ):
            with self.assertRaisesRegex(TaskStoreConflict, "injected"):
                self.store.record_supervised_worker_observation(
                    intent_id=managed_intent.intent_id,
                    evidence=managed_worker_evidence(attempt_id=attempt_id),
                    handle=managed_dispatcher.recover_existing_receipt(
                        managed_intent
                    ),
                    supervisor_lease=lease,
                    supervisor_clock=lambda: T_PLUS_1,
                )

        connection = self._connection()
        try:
            for intent_id in (
                private_intent.intent_id,
                managed_intent.intent_id,
            ):
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM dispatch_reconciliation_resolutions
                        WHERE intent_id = ?
                        """,
                        (intent_id,),
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT COUNT(*)
                        FROM supervised_private_receipt_adoptions
                        WHERE intent_id = ?
                        """,
                        (intent_id,),
                    ).fetchone()[0]
                    + connection.execute(
                        """
                        SELECT COUNT(*) FROM supervised_dispatch_adoptions
                        WHERE intent_id = ?
                        """,
                        (intent_id,),
                    ).fetchone()[0],
                    0,
                )
            self.assertEqual(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM worker_launch_attempts
                    WHERE intent_id = ?
                    """,
                    (managed_intent.intent_id,),
                ).fetchone()[0],
                0,
            )
        finally:
            connection.close()
        self.assertEqual(
            self.store.get_dispatch_intent(private_intent.task_id).state,
            "reconciliation_required",
        )
        self.assertEqual(
            self.store.get_dispatch_intent(managed_intent.task_id).state,
            "reconciliation_required",
        )

    def test_reconciliation_rejects_weak_proof_and_stale_term(self) -> None:
        _, dispatcher, _, reconciled = self._reconciliation_runtime(
            key="reconciliation-fence"
        )
        first = self._acquire(
            "reconciliation-first-owner", lease_seconds=1
        ).lease
        self.store.authorize_supervised_dispatch_reconciliation(
            intent_id=reconciled.intent_id,
            evidence_kind="managed_worker_receipt",
            supervisor_lease=first,
            supervisor_clock=lambda: NOW,
        )
        handle = dispatcher.recover_existing_receipt(reconciled)
        with self.assertRaisesRegex(TaskStoreConflict, "positive Worker proof"):
            self.store.record_supervised_worker_observation(
                intent_id=reconciled.intent_id,
                evidence=managed_worker_evidence(
                    ticket_state="failed", attempt_id="attempt-" + "3" * 32
                ),
                handle=handle,
                supervisor_lease=first,
                supervisor_clock=lambda: NOW,
            )
        takeover = self._acquire(
            "reconciliation-takeover-owner",
            now=T_PLUS_5,
            lease_seconds=10,
        ).lease
        with self.assertRaises(RuntimeSupervisorLeaseLost):
            self.store.record_supervised_worker_observation(
                intent_id=reconciled.intent_id,
                evidence=managed_worker_evidence(
                    attempt_id="attempt-" + "3" * 32
                ),
                handle=handle,
                supervisor_lease=first,
                supervisor_clock=lambda: T_PLUS_5,
            )
        authorized = self.store.authorize_supervised_dispatch_reconciliation(
            intent_id=reconciled.intent_id,
            evidence_kind="managed_worker_receipt",
            supervisor_lease=takeover,
            supervisor_clock=lambda: T_PLUS_5,
        )
        self.assertEqual(authorized.fencing_token, takeover.fencing_token)
        self.assertEqual(
            self.store.get_dispatch_intent(reconciled.task_id).state,
            "reconciliation_required",
        )

    def test_committed_dangling_private_adoption_is_corruption(self) -> None:
        _, dispatcher, _, reconciled = self._reconciliation_runtime(
            key="dangling-private-reconciliation",
            algorithm_version="1.4.0",
        )
        lease = self._acquire(
            "dangling-private-owner", lease_seconds=30
        ).lease
        authorization = self.store.authorize_supervised_dispatch_reconciliation(
            intent_id=reconciled.intent_id,
            evidence_kind="private_receipt",
            supervisor_lease=lease,
            supervisor_clock=lambda: NOW,
        )
        receipt = dispatcher.recover_existing_private_receipt(reconciled)
        recorded_at = "2026-07-15T03:00:01.000000Z"
        outcome = {
            "status": "dispatched",
            "handle": receipt["handle"],
            "recorded_at": recorded_at,
        }
        _, outcome_hash = encode_document(outcome)
        connection = self._connection()
        try:
            authorized_at_us = connection.execute(
                """
                SELECT authorized_at_us
                FROM supervised_dispatch_reconciliation_attempts
                WHERE intent_id = ? AND fencing_token = ?
                  AND evidence_kind = 'private_receipt'
                """,
                (reconciled.intent_id, authorization.fencing_token),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO supervised_private_receipt_adoptions(
                    intent_id, project_id, principal_id, fencing_token,
                    private_schema_version, receipt_record_hash,
                    outcome_document_hash, recorded_at, recorded_at_us
                ) VALUES (?, ?, ?, ?, '1.0.0', ?, ?, ?, ?)
                """,
                (
                    reconciled.intent_id,
                    PROJECT_ID,
                    PRINCIPAL_ID,
                    lease.fencing_token,
                    receipt["receipt_record_hash"],
                    outcome_hash,
                    recorded_at,
                    authorized_at_us + 1_000_000,
                ),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(TaskStoreCorruption, "dangling adoption"):
            self.store.get_dispatch_intent(reconciled.task_id)

    def test_timeout_window_uses_first_durable_running_observation_and_replays(
        self,
    ) -> None:
        (
            task_id,
            _,
            _,
            intent,
            lease,
            armed,
            capability_proof,
        ) = self._timeout_runtime(key="timeout-window-replay")
        # The active TaskService projection performed the first arm; this
        # direct Store call proves the exact capability/window replay.
        self.assertTrue(armed.replayed)
        self.assertEqual(armed.timeout.started_at, "2026-07-15T03:00:00.000000Z")
        self.assertEqual(armed.timeout.deadline_at, "2026-07-15T03:00:05.000000Z")
        self.assertEqual(armed.timeout.wall_time_seconds, 5)
        self.assertEqual(armed.snapshot.timeout, armed.timeout)

        replay = self.store.arm_worker_attempt_timeout(
            intent_id=intent.intent_id,
            attempt_id=armed.timeout.attempt_id,
            capability_proof=capability_proof,
            supervisor_lease=lease,
            supervisor_clock=lambda: T_PLUS_1,
        )
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.timeout, armed.timeout)

        before_due = self.store.authorize_supervised_timeout(
            timeout_id=armed.timeout.timeout_id,
            supervisor_lease=lease,
            supervisor_clock=lambda: T_PLUS_1,
        )
        self.assertFalse(before_due.authorized)
        self.assertFalse(before_due.replayed)
        self.assertEqual(before_due.timeout.state, "armed")

        due = self.store.authorize_supervised_timeout(
            timeout_id=armed.timeout.timeout_id,
            supervisor_lease=lease,
            supervisor_clock=lambda: T_PLUS_5,
        )
        self.assertTrue(due.authorized)
        self.assertFalse(due.replayed)
        self.assertEqual(due.timeout.state, "requested")
        self.assertEqual(
            due.authorized_at, "2026-07-15T03:00:05.000000Z"
        )
        due_replay = self.store.authorize_supervised_timeout(
            timeout_id=armed.timeout.timeout_id,
            supervisor_lease=lease,
            supervisor_clock=lambda: T_PLUS_10,
        )
        self.assertTrue(due_replay.authorized)
        self.assertTrue(due_replay.replayed)
        self.assertEqual(due_replay.authorized_at, due.authorized_at)

        connection = self._connection()
        try:
            window = connection.execute(
                """
                SELECT start_observation_sequence, started_at_us,
                       deadline_at_us, wall_time_seconds
                FROM worker_attempt_timeout_windows WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
            self.assertEqual(window["start_observation_sequence"], 1)
            self.assertEqual(window["started_at_us"], 1784084400000000)
            self.assertEqual(window["deadline_at_us"], 1784084405000000)
            self.assertEqual(window["wall_time_seconds"], 5)
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM supervised_timeout_attempts"
                ).fetchone()[0],
                1,
            )
        finally:
            connection.close()

    def test_timeout_window_rejects_an_invalid_exact_stop_capability(self) -> None:
        task_id, _, runtime, _ = self._pending_runtime(
            key="timeout-invalid-capability", wall_time_seconds=5
        )
        acquisition = runtime.acquire_runtime_supervisor_lease(
            **self.scope,
            owner_id="timeout-invalid-capability-owner",
            lease_seconds=30,
        )
        self.assertTrue(acquisition.acquired)
        scheduled = runtime.schedule_runtime_dispatch(
            task_id,
            **self.scope,
            supervisor_lease=acquisition.lease,
        )
        self.assertFalse(scheduled.timeout_armed)
        connection = self._connection()
        try:
            attempt = connection.execute(
                """
                SELECT attempt_id, binding_hash FROM worker_launch_attempts
                WHERE intent_id = ?
                """,
                (scheduled.intent.intent_id,),
            ).fetchone()
            self.assertIsNotNone(attempt)
            assert attempt is not None
        finally:
            connection.close()
        proof = timeout_capability_proof(
            attempt_id=attempt["attempt_id"],
            binding_hash=attempt["binding_hash"],
        )
        proof["supported_reasons"] = ["wall_time_exceeded"]
        proof["proof_hash"] = encode_document(
            {key: value for key, value in proof.items() if key != "proof_hash"}
        )[1]

        with self.assertRaisesRegex(TaskStoreConflict, "capability proof"):
            self.store.arm_worker_attempt_timeout(
                intent_id=scheduled.intent.intent_id,
                attempt_id=attempt["attempt_id"],
                capability_proof=proof,
                supervisor_lease=acquisition.lease,
                supervisor_clock=lambda: NOW,
            )
        self.assertIsNone(self.store.get_task(task_id).timeout)
        connection = self._connection()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM worker_attempt_timeout_windows"
                ).fetchone()[0],
                0,
            )
        finally:
            connection.close()

    def test_timeout_arm_rejects_forged_relational_running_observation(
        self,
    ) -> None:
        task_id, dispatcher, runtime, _ = self._pending_runtime(
            key="timeout-forged-running-observation",
            deferred=True,
            wall_time_seconds=5,
        )
        acquisition = runtime.acquire_runtime_supervisor_lease(
            **self.scope,
            owner_id="timeout-forged-observation-owner",
            lease_seconds=30,
        )
        self.assertTrue(acquisition.acquired)
        staged = runtime.schedule_runtime_dispatch(
            task_id,
            **self.scope,
            supervisor_lease=acquisition.lease,
        )
        self.assertEqual(staged.intent.state, "dispatching")
        connection = self._connection()
        try:
            attempt = connection.execute(
                """
                SELECT attempt_id, binding_hash FROM worker_launch_attempts
                WHERE intent_id = ?
                """,
                (staged.intent.intent_id,),
            ).fetchone()
            self.assertIsNotNone(attempt)
            assert attempt is not None
            first = connection.execute(
                """
                SELECT ticket_state FROM worker_attempt_observations
                WHERE attempt_id = ? AND observation_sequence = 1
                """,
                (attempt["attempt_id"],),
            ).fetchone()
            self.assertEqual(first["ticket_state"], "staged")
        finally:
            connection.close()
        dispatcher.failure_code = None
        dispatcher.defer_dispatch = False
        handle = dispatcher.dispatch(staged.intent)
        dispatch_outcome = {
            "status": "dispatched",
            "handle": handle,
            "recorded_at": NOW,
        }
        outcome_json, outcome_hash = encode_document(dispatch_outcome)
        running = managed_worker_evidence(attempt_id=attempt["attempt_id"])
        fake_document = managed_worker_evidence(
            ticket_state="staged", attempt_id=attempt["attempt_id"]
        )
        fake_document["ticket"]["updated_at"] = T_PLUS_1
        fake_ticket = {
            **{
                key: fake_document[key]
                for key in (
                    "schema_version",
                    "submission_id",
                    "attempt_id",
                    "attempt_number",
                    "job_id",
                    "request_hash",
                    "created_at",
                )
            },
            "binding_hash": fake_document["binding_hash"],
            "state": "staged",
            "capacity_slot": None,
            "capacity_generation": None,
            "worker_pid": None,
            "updated_at": T_PLUS_1,
        }
        fake_document["ticket"]["record_hash"] = encode_document(fake_ticket)[1]
        fake_json, fake_hash = encode_document(fake_document)

        connection = self._connection()
        try:
            connection.execute(
                """
                INSERT INTO dispatch_outcomes(
                    intent_id, outcome, document_json, document_hash, recorded_at
                ) VALUES (?, 'dispatched', ?, ?, ?)
                """,
                (
                    staged.intent.intent_id,
                    outcome_json,
                    outcome_hash,
                    NOW,
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
                ) VALUES (?, 2, 'spawned', ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          'running', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt["attempt_id"],
                    running["ticket"]["capacity_slot"],
                    running["ticket"]["capacity_generation"],
                    running["ticket"]["worker_pid"],
                    running["ticket"]["updated_at"],
                    running["ticket"]["record_hash"],
                    running["ready"]["worker_pid"],
                    running["ready"]["started_at"],
                    running["ready"]["record_hash"],
                    running["heartbeat"]["sequence"],
                    running["heartbeat"]["updated_at"],
                    running["heartbeat"]["record_hash"],
                    fake_json,
                    fake_hash,
                    PROJECT_ID,
                    PRINCIPAL_ID,
                    acquisition.lease.fencing_token,
                    "2026-07-15T03:00:01.000000Z",
                    1784084401000000,
                ),
            )
            connection.commit()
        finally:
            connection.close()
        capability = timeout_capability_proof(
            attempt_id=attempt["attempt_id"],
            binding_hash=attempt["binding_hash"],
        )
        with self.assertRaisesRegex(TaskStoreCorruption, "columns differ"):
            self.store.arm_worker_attempt_timeout(
                intent_id=staged.intent.intent_id,
                attempt_id=attempt["attempt_id"],
                capability_proof=capability,
                supervisor_lease=acquisition.lease,
                supervisor_clock=lambda: T_PLUS_1,
            )
        self.assertIsNone(self.store.get_task(task_id).timeout)

    def test_timeout_projection_rejects_corrupt_source_observation(self) -> None:
        task_id, _, _, _, _, armed, _ = self._timeout_runtime(
            key="timeout-source-observation-corruption"
        )
        connection = self._connection()
        try:
            connection.execute(
                "DROP TRIGGER worker_attempt_observations_are_append_only"
            )
            connection.execute(
                """
                UPDATE worker_attempt_observations
                SET document_hash = ?
                WHERE attempt_id = ? AND observation_sequence = 1
                """,
                ("sha256:" + "f" * 64, armed.timeout.attempt_id),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(
            TaskStoreCorruption, "observation hash does not match"
        ):
            self.store.get_task(task_id)

    def test_timeout_arm_rejects_hidden_earlier_running_observation(self) -> None:
        task_id, intent, lease, attempt, proof = (
            self._staged_then_running_timeout_runtime(
                key="timeout-hidden-earlier-running"
            )
        )
        hidden_running = managed_worker_evidence(
            attempt_id=attempt["attempt_id"], heartbeat_sequence=2
        )
        hidden_json, hidden_hash = encode_document(hidden_running)
        connection = self._connection()
        try:
            connection.execute(
                "DROP TRIGGER worker_attempt_observations_are_append_only"
            )
            connection.execute(
                """
                UPDATE worker_attempt_observations
                SET document_json = ?, document_hash = ?
                WHERE attempt_id = ? AND observation_sequence = 1
                """,
                (
                    hidden_json,
                    hidden_hash,
                    attempt["attempt_id"],
                ),
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaisesRegex(TaskStoreCorruption, "columns differ"):
            self.store.arm_worker_attempt_timeout(
                intent_id=intent.intent_id,
                attempt_id=attempt["attempt_id"],
                capability_proof=proof,
                supervisor_lease=lease,
                supervisor_clock=lambda: T_PLUS_1,
            )
        self.assertIsNone(self.store.get_task(task_id).timeout)

    def test_timeout_arm_rejects_forged_launch_attempt_binding(self) -> None:
        task_id, intent, lease, attempt, _ = (
            self._staged_then_running_timeout_runtime(
                key="timeout-forged-launch-binding"
            )
        )
        forged_binding = "sha256:" + "7" * 64
        connection = self._connection()
        try:
            connection.execute("DROP TRIGGER worker_launch_attempts_are_append_only")
            connection.execute(
                """
                UPDATE worker_launch_attempts SET binding_hash = ?
                WHERE attempt_id = ?
                """,
                (forged_binding, attempt["attempt_id"]),
            )
            connection.commit()
        finally:
            connection.close()
        forged_proof = timeout_capability_proof(
            attempt_id=attempt["attempt_id"], binding_hash=forged_binding
        )

        with self.assertRaisesRegex(TaskStoreCorruption, "hashed observation"):
            self.store.arm_worker_attempt_timeout(
                intent_id=intent.intent_id,
                attempt_id=attempt["attempt_id"],
                capability_proof=forged_proof,
                supervisor_lease=lease,
                supervisor_clock=lambda: T_PLUS_1,
            )
        self.assertIsNone(self.store.get_task(task_id).timeout)

    def test_timeout_projection_rejects_corrupt_launch_attempt_binding(
        self,
    ) -> None:
        task_id, _, _, _, _, armed, _ = self._timeout_runtime(
            key="timeout-launch-binding-corruption"
        )
        connection = self._connection()
        try:
            connection.execute("DROP TRIGGER worker_launch_attempts_are_append_only")
            connection.execute(
                """
                UPDATE worker_launch_attempts SET job_id = 'job-forged-timeout'
                WHERE attempt_id = ?
                """,
                (armed.timeout.attempt_id,),
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaisesRegex(TaskStoreCorruption, "hashed observation"):
            self.store.get_task(task_id)

    def test_timeout_projection_rejects_observation_sequence_gap(self) -> None:
        task_id, _, _, intent, lease, armed, _ = self._timeout_runtime(
            key="timeout-observation-sequence-gap"
        )
        evidence = managed_worker_evidence(
            attempt_id=armed.timeout.attempt_id, heartbeat_sequence=2
        )
        projected = self.store.record_supervised_worker_observation(
            intent_id=intent.intent_id,
            evidence=evidence,
            handle=intent.handle,
            supervisor_lease=lease,
            supervisor_clock=lambda: T_PLUS_1,
        )
        self.assertEqual(projected.observation_sequence, 2)
        connection = self._connection()
        try:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute(
                "DROP TRIGGER worker_attempt_observations_cannot_be_deleted"
            )
            connection.execute(
                """
                DELETE FROM worker_attempt_observations
                WHERE attempt_id = ? AND observation_sequence = 1
                """,
                (armed.timeout.attempt_id,),
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaisesRegex(TaskStoreCorruption, "audit chain"):
            self.store.get_task(task_id)

    def test_timeout_projection_rejects_observation_scope_corruption(
        self,
    ) -> None:
        task_id, _, _, _, _, armed, _ = self._timeout_runtime(
            key="timeout-observation-scope-corruption"
        )
        connection = self._connection()
        try:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute(
                "DROP TRIGGER worker_attempt_observations_are_append_only"
            )
            connection.execute(
                """
                UPDATE worker_attempt_observations
                SET project_id = 'project-forged-timeout'
                WHERE attempt_id = ? AND observation_sequence = 1
                """,
                (armed.timeout.attempt_id,),
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaisesRegex(TaskStoreCorruption, "audit chain"):
            self.store.get_task(task_id)

    def test_timeout_completion_is_failed_with_exact_wall_time_code(self) -> None:
        task_id, _, _, intent, lease, armed, _ = self._timeout_runtime(
            key="timeout-complete"
        )
        authorization = self.store.authorize_supervised_timeout(
            timeout_id=armed.timeout.timeout_id,
            supervisor_lease=lease,
            supervisor_clock=lambda: T_PLUS_5,
        )
        self.assertTrue(authorization.authorized)
        connection = self._connection()
        try:
            ready_record_hash = connection.execute(
                """
                SELECT ready_record_hash
                FROM worker_attempt_timeout_windows WHERE timeout_id = ?
                """,
                (armed.timeout.timeout_id,),
            ).fetchone()[0]
        finally:
            connection.close()
        proof = timeout_adapter_proof(
            timeout=armed.timeout,
            state="timed_out",
            terminal_status="Failed",
            terminal_failure_code="WALL_TIME_EXCEEDED",
            ready_record_hash=ready_record_hash,
        )
        sequence = self.store.latest_run_event_sequence(task_id) + 1
        event = {
            "schema_version": "1.0.0",
            "event_id": "event-timeout-complete",
            "sequence": sequence,
            "task_id": task_id,
            "node_id": intent.node_id,
            "event_type": "node_failed",
            "task_status": "Failed",
            "error": {
                "code": "wall_time_exceeded",
                "message": "FWI Worker exceeded its wall-time limit",
                "retryable": False,
            },
            "occurred_at": T_PLUS_5,
            "fingerprint": intent.handle["fingerprint"],
            "extensions": {
                "org.agent_rpc.timeout": {
                    "timeout_id": armed.timeout.timeout_id,
                    "attempt_id": armed.timeout.attempt_id,
                    "wall_time_seconds": armed.timeout.wall_time_seconds,
                    "started_at": armed.timeout.started_at,
                    "deadline_at": armed.timeout.deadline_at,
                    "failure_code": "WALL_TIME_EXCEEDED",
                    "proof_hash": proof["proof_hash"],
                }
            },
        }
        completed = self.store.complete_supervised_timeout(
            timeout_id=armed.timeout.timeout_id,
            result="timeout_confirmed",
            terminal_event=event,
            adapter_proof=proof,
            supervisor_lease=lease,
            supervisor_clock=lambda: T_PLUS_5,
        )
        self.assertFalse(completed.replayed)
        self.assertEqual(completed.snapshot.status, "Failed")
        self.assertEqual(completed.timeout.state, "timed_out")
        self.assertEqual(completed.timeout.result, "timeout_confirmed")
        self.assertEqual(completed.timeout.failure_code, "WALL_TIME_EXCEEDED")
        self.assertEqual(completed.timeout.terminal_status, "Failed")

        replay = self.store.complete_supervised_timeout(
            timeout_id=armed.timeout.timeout_id,
            result="timeout_confirmed",
            terminal_event=event,
            adapter_proof=proof,
            supervisor_lease=lease,
            supervisor_clock=lambda: T_PLUS_10,
        )
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.timeout, completed.timeout)
        events = self.store.list_run_events(task_id)
        self.assertEqual(events[-1]["error"]["code"], "wall_time_exceeded")
        connection = self._connection()
        try:
            outcome = connection.execute(
                """
                SELECT result, terminal_status, failure_code
                FROM task_timeout_outcomes WHERE timeout_id = ?
                """,
                (armed.timeout.timeout_id,),
            ).fetchone()
            self.assertEqual(
                tuple(outcome),
                ("timeout_confirmed", "Failed", "WALL_TIME_EXCEEDED"),
            )
            for table in (
                "worker_attempt_timeout_windows",
                "supervised_timeout_attempts",
                "task_timeout_outcomes",
            ):
                with self.subTest(table=table, operation="update"):
                    with self.assertRaisesRegex(
                        sqlite3.IntegrityError, "immutable"
                    ):
                        connection.execute(
                            f"UPDATE {table} SET timeout_id = timeout_id "
                            "WHERE timeout_id = ?",
                            (armed.timeout.timeout_id,),
                        )
                    connection.rollback()
                with self.subTest(table=table, operation="delete"):
                    with self.assertRaisesRegex(
                        sqlite3.IntegrityError, "immutable"
                    ):
                        connection.execute(
                            f"DELETE FROM {table} WHERE timeout_id = ?",
                            (armed.timeout.timeout_id,),
                        )
                    connection.rollback()
            self.assertEqual(
                connection.execute("PRAGMA foreign_key_check").fetchall(), []
            )
        finally:
            connection.close()

    def test_natural_failure_can_win_an_authorized_timeout_race(self) -> None:
        task_id, _, _, intent, lease, armed, _ = self._timeout_runtime(
            key="timeout-natural-terminal-wins"
        )
        authorization = self.store.authorize_supervised_timeout(
            timeout_id=armed.timeout.timeout_id,
            supervisor_lease=lease,
            supervisor_clock=lambda: T_PLUS_5,
        )
        self.assertTrue(authorization.authorized)
        connection = self._connection()
        try:
            ready_record_hash = connection.execute(
                """
                SELECT ready_record_hash FROM worker_attempt_timeout_windows
                WHERE timeout_id = ?
                """,
                (armed.timeout.timeout_id,),
            ).fetchone()[0]
        finally:
            connection.close()
        proof = timeout_adapter_proof(
            timeout=armed.timeout,
            state="terminal_won",
            terminal_status="Failed",
            terminal_failure_code=None,
            ready_record_hash=ready_record_hash,
        )
        event = {
            "schema_version": "1.0.0",
            "event_id": "event-timeout-natural-failure",
            "sequence": self.store.latest_run_event_sequence(task_id) + 1,
            "task_id": task_id,
            "node_id": intent.node_id,
            "event_type": "node_failed",
            "task_status": "Failed",
            "error": {
                "code": "worker_failed",
                "message": "FWI Worker reported a failure",
                "retryable": False,
            },
            "occurred_at": T_PLUS_5,
            "fingerprint": intent.handle["fingerprint"],
            "extensions": {
                "org.agent_rpc.adapter_status": {
                    "job_id": intent.handle["job_id"],
                    "stage": "failed",
                    "worker_updated_at": T_PLUS_5,
                }
            },
        }
        completed = self.store.complete_supervised_timeout(
            timeout_id=armed.timeout.timeout_id,
            result="terminal_preempted",
            terminal_event=event,
            adapter_proof=proof,
            supervisor_lease=lease,
            supervisor_clock=lambda: T_PLUS_5,
        )
        self.assertFalse(completed.replayed)
        self.assertEqual(completed.snapshot.status, "Failed")
        self.assertEqual(completed.timeout.state, "superseded")
        self.assertEqual(completed.timeout.result, "terminal_preempted")
        self.assertIsNone(completed.timeout.failure_code)
        self.assertEqual(
            self.store.list_run_events(task_id)[-1]["error"]["code"],
            "worker_failed",
        )

    def test_supplied_natural_timeout_winner_requires_exact_adapter_event(
        self,
    ) -> None:
        task_id, _, _, intent, lease, armed, _ = self._timeout_runtime(
            key="timeout-natural-supplied-shape"
        )
        self.store.authorize_supervised_timeout(
            timeout_id=armed.timeout.timeout_id,
            supervisor_lease=lease,
            supervisor_clock=lambda: T_PLUS_5,
        )
        proof = timeout_adapter_proof(
            timeout=armed.timeout,
            state="terminal_won",
            terminal_status="Failed",
            terminal_failure_code=None,
            ready_record_hash=self._timeout_ready_record_hash(
                armed.timeout.timeout_id
            ),
        )
        event = self._natural_timeout_failure_event(
            timeout=armed.timeout,
            intent=intent,
            event_id="event-timeout-natural-supplied-shape",
            occurred_at=T_PLUS_5,
        )
        event["extensions"] = {}

        with self.assertRaisesRegex(
            TaskStoreConflict, "natural terminal timeout event is invalid"
        ):
            self.store.complete_supervised_timeout(
                timeout_id=armed.timeout.timeout_id,
                result="terminal_preempted",
                terminal_event=event,
                adapter_proof=proof,
                supervisor_lease=lease,
                supervisor_clock=lambda: T_PLUS_5,
            )
        event = self._natural_timeout_failure_event(
            timeout=armed.timeout,
            intent=intent,
            event_id="event-timeout-natural-invalid-worker-time",
            occurred_at="not-a-worker-timestamp",
        )
        with self.assertRaisesRegex(
            TaskStoreConflict, "natural terminal timeout event is invalid"
        ):
            self.store.complete_supervised_timeout(
                timeout_id=armed.timeout.timeout_id,
                result="terminal_preempted",
                terminal_event=event,
                adapter_proof=proof,
                supervisor_lease=lease,
                supervisor_clock=lambda: T_PLUS_5,
            )
        self.assertEqual(self.store.get_task(task_id).status, "Queued")

    def test_existing_natural_timeout_winner_requires_exact_adapter_event(
        self,
    ) -> None:
        task_id, _, _, intent, lease, armed, _ = self._timeout_runtime(
            key="timeout-natural-existing-shape"
        )
        self.store.authorize_supervised_timeout(
            timeout_id=armed.timeout.timeout_id,
            supervisor_lease=lease,
            supervisor_clock=lambda: T_PLUS_5,
        )
        proof = timeout_adapter_proof(
            timeout=armed.timeout,
            state="terminal_won",
            terminal_status="Failed",
            terminal_failure_code=None,
            ready_record_hash=self._timeout_ready_record_hash(
                armed.timeout.timeout_id
            ),
        )
        event = self._natural_timeout_failure_event(
            timeout=armed.timeout,
            intent=intent,
            event_id="event-timeout-natural-existing-shape",
            occurred_at=T_PLUS_5,
        )
        event["error"] = {"code": "worker_failed"}
        event_json, event_hash = encode_document(event)
        _, fingerprint_hash = encode_document(event["fingerprint"])
        connection = self._connection()
        try:
            connection.execute(
                """
                INSERT INTO run_events(
                    task_id, sequence, event_id, event_type, task_status,
                    node_id, fingerprint_hash, document_json, document_hash,
                    occurred_at, recorded_at
                ) VALUES (?, ?, ?, 'node_failed', 'Failed', ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    event["sequence"],
                    event["event_id"],
                    event["node_id"],
                    fingerprint_hash,
                    event_json,
                    event_hash,
                    event["occurred_at"],
                    T_PLUS_5,
                ),
            )
            connection.execute(
                "UPDATE tasks SET status = 'Failed', updated_at = ? "
                "WHERE task_id = ?",
                (T_PLUS_5, task_id),
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaisesRegex(
            TaskStoreCorruption, "natural timeout winner is inconsistent"
        ):
            self.store.complete_supervised_timeout(
                timeout_id=armed.timeout.timeout_id,
                result="terminal_preempted",
                terminal_event=None,
                adapter_proof=proof,
                supervisor_lease=lease,
                supervisor_clock=lambda: T_PLUS_5,
            )

    def test_timeout_projection_rejects_malformed_natural_terminal_event(
        self,
    ) -> None:
        task_id, _, _, intent, lease, armed, _ = self._timeout_runtime(
            key="timeout-natural-loader-shape"
        )
        self.store.authorize_supervised_timeout(
            timeout_id=armed.timeout.timeout_id,
            supervisor_lease=lease,
            supervisor_clock=lambda: T_PLUS_5,
        )
        proof = timeout_adapter_proof(
            timeout=armed.timeout,
            state="terminal_won",
            terminal_status="Failed",
            terminal_failure_code=None,
            ready_record_hash=self._timeout_ready_record_hash(
                armed.timeout.timeout_id
            ),
        )
        event = self._natural_timeout_failure_event(
            timeout=armed.timeout,
            intent=intent,
            event_id="event-timeout-natural-loader-shape",
            occurred_at=T_PLUS_5,
        )
        event["extensions"] = {}
        connection = self._connection()
        try:
            self._insert_direct_terminal_preempted_timeout(
                connection,
                timeout=armed.timeout,
                intent=intent,
                lease=lease,
                proof=proof,
                event=event,
                resolved_at="2026-07-15T03:00:05.000000Z",
                resolved_at_us=1784084405000000,
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaisesRegex(
            TaskStoreCorruption, "terminal event is inconsistent"
        ):
            self.store.get_task(task_id)

    def test_timeout_outcome_cannot_precede_deadline_or_authorization(self) -> None:
        task_id, _, _, intent, lease, armed, _ = self._timeout_runtime(
            key="timeout-causal-order-store"
        )
        authorization = self.store.authorize_supervised_timeout(
            timeout_id=armed.timeout.timeout_id,
            supervisor_lease=lease,
            supervisor_clock=lambda: T_PLUS_5,
        )
        self.assertTrue(authorization.authorized)
        proof = timeout_adapter_proof(
            timeout=armed.timeout,
            state="terminal_won",
            terminal_status="Failed",
            terminal_failure_code=None,
            ready_record_hash=self._timeout_ready_record_hash(
                armed.timeout.timeout_id
            ),
        )
        event = self._natural_timeout_failure_event(
            timeout=armed.timeout,
            intent=intent,
            event_id="event-timeout-causal-order-store",
            occurred_at=T_PLUS_5,
        )
        before_sequence = self.store.latest_run_event_sequence(task_id)

        with self.assertRaisesRegex(
            TaskStoreConflict, "precedes its durable authorization"
        ):
            self.store.complete_supervised_timeout(
                timeout_id=armed.timeout.timeout_id,
                result="terminal_preempted",
                terminal_event=event,
                adapter_proof=proof,
                supervisor_lease=lease,
                supervisor_clock=lambda: T_PLUS_1,
            )
        self.assertEqual(self.store.get_task(task_id).status, "Queued")
        self.assertEqual(
            self.store.latest_run_event_sequence(task_id), before_sequence
        )

        completed = self.store.complete_supervised_timeout(
            timeout_id=armed.timeout.timeout_id,
            result="terminal_preempted",
            terminal_event=event,
            adapter_proof=proof,
            supervisor_lease=lease,
            supervisor_clock=lambda: T_PLUS_5,
        )
        replay = self.store.complete_supervised_timeout(
            timeout_id=armed.timeout.timeout_id,
            result="terminal_preempted",
            terminal_event=event,
            adapter_proof=proof,
            supervisor_lease=lease,
            supervisor_clock=lambda: T_PLUS_10,
        )
        self.assertFalse(completed.replayed)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.timeout, completed.timeout)

    def test_confirmed_timeout_event_must_fall_inside_supervisor_window(
        self,
    ) -> None:
        task_id, _, _, intent, lease, armed, _ = self._timeout_runtime(
            key="timeout-terminal-event-causal-window"
        )
        authorization = self.store.authorize_supervised_timeout(
            timeout_id=armed.timeout.timeout_id,
            supervisor_lease=lease,
            supervisor_clock=lambda: T_PLUS_5,
        )
        self.assertTrue(authorization.authorized)
        proof = timeout_adapter_proof(
            timeout=armed.timeout,
            state="timed_out",
            terminal_status="Failed",
            terminal_failure_code="WALL_TIME_EXCEEDED",
            ready_record_hash=self._timeout_ready_record_hash(
                armed.timeout.timeout_id
            ),
        )
        before_sequence = self.store.latest_run_event_sequence(task_id)
        for label, occurred_at in (
            ("before-deadline", T_PLUS_1),
            ("after-resolution", T_PLUS_10),
        ):
            with self.subTest(label=label):
                event = self._confirmed_timeout_failure_event(
                    timeout=armed.timeout,
                    intent=intent,
                    proof=proof,
                    event_id=f"event-timeout-{label}",
                    occurred_at=occurred_at,
                )
                with self.assertRaisesRegex(TaskStoreConflict, "causal order"):
                    self.store.complete_supervised_timeout(
                        timeout_id=armed.timeout.timeout_id,
                        result="timeout_confirmed",
                        terminal_event=event,
                        adapter_proof=proof,
                        supervisor_lease=lease,
                        supervisor_clock=lambda: T_PLUS_5,
                    )
        self.assertEqual(
            self.store.latest_run_event_sequence(task_id), before_sequence
        )
        valid_event = self._confirmed_timeout_failure_event(
            timeout=armed.timeout,
            intent=intent,
            proof=proof,
            event_id="event-timeout-causal-window-valid",
            occurred_at=T_PLUS_5,
        )
        completed = self.store.complete_supervised_timeout(
            timeout_id=armed.timeout.timeout_id,
            result="timeout_confirmed",
            terminal_event=valid_event,
            adapter_proof=proof,
            supervisor_lease=lease,
            supervisor_clock=lambda: T_PLUS_5,
        )
        self.assertEqual(completed.timeout.state, "timed_out")
        self.assertEqual(
            self.store.list_run_events(task_id)[-1]["occurred_at"], T_PLUS_5
        )

    def test_timeout_projection_rejects_terminal_event_outside_causal_window(
        self,
    ) -> None:
        task_id, _, _, intent, lease, armed, _ = self._timeout_runtime(
            key="timeout-terminal-event-causal-corruption"
        )
        self.store.authorize_supervised_timeout(
            timeout_id=armed.timeout.timeout_id,
            supervisor_lease=lease,
            supervisor_clock=lambda: T_PLUS_5,
        )
        proof = timeout_adapter_proof(
            timeout=armed.timeout,
            state="timed_out",
            terminal_status="Failed",
            terminal_failure_code="WALL_TIME_EXCEEDED",
            ready_record_hash=self._timeout_ready_record_hash(
                armed.timeout.timeout_id
            ),
        )
        event = self._confirmed_timeout_failure_event(
            timeout=armed.timeout,
            intent=intent,
            proof=proof,
            event_id="event-timeout-causal-corruption",
            occurred_at=T_PLUS_5,
        )
        self.store.complete_supervised_timeout(
            timeout_id=armed.timeout.timeout_id,
            result="timeout_confirmed",
            terminal_event=event,
            adapter_proof=proof,
            supervisor_lease=lease,
            supervisor_clock=lambda: T_PLUS_5,
        )
        connection = self._connection()
        try:
            connection.execute("DROP TRIGGER run_events_are_append_only")
            for occurred_at in (T_PLUS_1, T_PLUS_10):
                tampered = copy.deepcopy(event)
                tampered["occurred_at"] = occurred_at
                document_json, document_hash = encode_document(tampered)
                connection.execute(
                    """
                    UPDATE run_events
                    SET occurred_at = ?, document_json = ?, document_hash = ?
                    WHERE task_id = ? AND sequence = ?
                    """,
                    (
                        occurred_at,
                        document_json,
                        document_hash,
                        task_id,
                        event["sequence"],
                    ),
                )
                connection.commit()
                with self.subTest(occurred_at=occurred_at):
                    with self.assertRaisesRegex(
                        TaskStoreCorruption, "terminal event is inconsistent"
                    ):
                        self.store.get_task(task_id)
        finally:
            connection.close()

    def test_direct_sql_timeout_outcome_rejects_backdated_resolution(self) -> None:
        task_id, _, _, intent, lease, armed, _ = self._timeout_runtime(
            key="timeout-causal-order-sql"
        )
        authorization = self.store.authorize_supervised_timeout(
            timeout_id=armed.timeout.timeout_id,
            supervisor_lease=lease,
            supervisor_clock=lambda: T_PLUS_5,
        )
        self.assertTrue(authorization.authorized)
        proof = timeout_adapter_proof(
            timeout=armed.timeout,
            state="terminal_won",
            terminal_status="Failed",
            terminal_failure_code=None,
            ready_record_hash=self._timeout_ready_record_hash(
                armed.timeout.timeout_id
            ),
        )
        event = self._natural_timeout_failure_event(
            timeout=armed.timeout,
            intent=intent,
            event_id="event-timeout-causal-order-sql",
            occurred_at=T_PLUS_1,
        )
        connection = self._connection()
        try:
            with self.assertRaisesRegex(
                sqlite3.IntegrityError, "exact terminal event"
            ):
                self._insert_direct_terminal_preempted_timeout(
                    connection,
                    timeout=armed.timeout,
                    intent=intent,
                    lease=lease,
                    proof=proof,
                    event=event,
                    resolved_at="2026-07-15T03:00:01.000000Z",
                    resolved_at_us=1784084401000000,
                )
        finally:
            connection.rollback()
            connection.close()
        self.assertEqual(self.store.get_task(task_id).status, "Queued")

    def test_terminal_preempted_rejects_self_hashed_worker_failure_code(
        self,
    ) -> None:
        task_id, _, _, intent, lease, armed, _ = self._timeout_runtime(
            key="timeout-terminal-failure-code"
        )
        authorization = self.store.authorize_supervised_timeout(
            timeout_id=armed.timeout.timeout_id,
            supervisor_lease=lease,
            supervisor_clock=lambda: T_PLUS_5,
        )
        self.assertTrue(authorization.authorized)
        proof = timeout_adapter_proof(
            timeout=armed.timeout,
            state="terminal_won",
            terminal_status="Failed",
            terminal_failure_code="WORKER_FAILED",
            ready_record_hash=self._timeout_ready_record_hash(
                armed.timeout.timeout_id
            ),
        )
        self.assertEqual(
            proof["proof_hash"],
            encode_document(
                {
                    key: value
                    for key, value in proof.items()
                    if key != "proof_hash"
                }
            )[1],
        )
        event = self._natural_timeout_failure_event(
            timeout=armed.timeout,
            intent=intent,
            event_id="event-timeout-terminal-failure-code",
            occurred_at=T_PLUS_5,
        )
        with self.assertRaisesRegex(
            TaskStoreConflict, "Adapter proof is invalid"
        ):
            self.store.complete_supervised_timeout(
                timeout_id=armed.timeout.timeout_id,
                result="terminal_preempted",
                terminal_event=event,
                adapter_proof=proof,
                supervisor_lease=lease,
                supervisor_clock=lambda: T_PLUS_5,
            )
        self.assertEqual(self.store.get_task(task_id).status, "Queued")

        connection = self._connection()
        try:
            with self.assertRaisesRegex(
                sqlite3.IntegrityError, "exact terminal event"
            ):
                self._insert_direct_terminal_preempted_timeout(
                    connection,
                    timeout=armed.timeout,
                    intent=intent,
                    lease=lease,
                    proof=proof,
                    event=event,
                    resolved_at="2026-07-15T03:00:05.000000Z",
                    resolved_at_us=1784084405000000,
                )
        finally:
            connection.rollback()
            connection.close()
        self.assertEqual(self.store.get_task(task_id).status, "Queued")
        connection = self._connection()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM task_timeout_outcomes"
                ).fetchone()[0],
                0,
            )
        finally:
            connection.close()

    def test_timeout_projection_rejects_corrupt_causal_time_indexes(self) -> None:
        task_id, _, _, intent, lease, armed, _ = self._timeout_runtime(
            key="timeout-causal-order-corruption"
        )
        authorization = self.store.authorize_supervised_timeout(
            timeout_id=armed.timeout.timeout_id,
            supervisor_lease=lease,
            supervisor_clock=lambda: T_PLUS_5,
        )
        self.assertTrue(authorization.authorized)
        proof = timeout_adapter_proof(
            timeout=armed.timeout,
            state="terminal_won",
            terminal_status="Failed",
            terminal_failure_code=None,
            ready_record_hash=self._timeout_ready_record_hash(
                armed.timeout.timeout_id
            ),
        )
        event = self._natural_timeout_failure_event(
            timeout=armed.timeout,
            intent=intent,
            event_id="event-timeout-causal-order-corruption",
            occurred_at=T_PLUS_5,
        )
        completed = self.store.complete_supervised_timeout(
            timeout_id=armed.timeout.timeout_id,
            result="terminal_preempted",
            terminal_event=event,
            adapter_proof=proof,
            supervisor_lease=lease,
            supervisor_clock=lambda: T_PLUS_5,
        )
        self.assertEqual(completed.timeout.state, "superseded")

        connection = self._connection()
        try:
            connection.execute(
                "DROP TRIGGER task_timeout_outcomes_are_immutable"
            )
            connection.execute(
                """
                UPDATE task_timeout_outcomes SET resolved_at_us = ?
                WHERE timeout_id = ?
                """,
                (1784084401000000, armed.timeout.timeout_id),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(TaskStoreCorruption, "causal order"):
            self.store.get_task(task_id)

        connection = self._connection()
        try:
            connection.execute(
                """
                UPDATE task_timeout_outcomes SET resolved_at_us = ?
                WHERE timeout_id = ?
                """,
                (1784084405000000, armed.timeout.timeout_id),
            )
            connection.commit()
        finally:
            connection.close()
        self.assertEqual(self.store.get_task(task_id).timeout.state, "superseded")

        connection = self._connection()
        try:
            connection.execute(
                "DROP TRIGGER supervised_timeout_attempts_are_immutable"
            )
            connection.execute(
                """
                UPDATE supervised_timeout_attempts SET authorized_at_us = ?
                WHERE timeout_id = ? AND fencing_token = ?
                """,
                (
                    1784084401000000,
                    armed.timeout.timeout_id,
                    lease.fencing_token,
                ),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(
            TaskStoreCorruption, "authorization time is inconsistent"
        ):
            self.store.get_task(task_id)

        connection = self._connection()
        try:
            connection.execute(
                """
                UPDATE supervised_timeout_attempts
                SET authorized_at = ?
                WHERE timeout_id = ? AND fencing_token = ?
                """,
                (
                    "2026-07-15T03:00:01.000000Z",
                    armed.timeout.timeout_id,
                    lease.fencing_token,
                ),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(
            TaskStoreCorruption, "authorization time is inconsistent"
        ):
            self.store.get_task(task_id)

    def test_pending_timeout_rejects_malformed_authorization_time(self) -> None:
        task_id, _, _, _, lease, armed, _ = self._timeout_runtime(
            key="timeout-authorization-time-corruption"
        )
        authorization = self.store.authorize_supervised_timeout(
            timeout_id=armed.timeout.timeout_id,
            supervisor_lease=lease,
            supervisor_clock=lambda: T_PLUS_5,
        )
        self.assertTrue(authorization.authorized)
        connection = self._connection()
        try:
            connection.execute(
                "DROP TRIGGER supervised_timeout_attempts_are_immutable"
            )
            connection.execute(
                """
                UPDATE supervised_timeout_attempts
                SET authorized_at = 'not-a-runtime-timestamp'
                WHERE timeout_id = ? AND fencing_token = ?
                """,
                (armed.timeout.timeout_id, lease.fencing_token),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(
            TaskStoreCorruption, "authorization time is invalid"
        ):
            self.store.get_task(task_id)
        with self.assertRaisesRegex(
            TaskStoreCorruption, "authorization time is invalid"
        ):
            self.store.authorize_supervised_timeout(
                timeout_id=armed.timeout.timeout_id,
                supervisor_lease=lease,
                supervisor_clock=lambda: T_PLUS_10,
            )

    def test_timeout_projection_rejects_corrupt_terminal_run_event(self) -> None:
        task_id, _, _, intent, lease, armed, _ = self._timeout_runtime(
            key="timeout-terminal-event-corruption"
        )
        authorization = self.store.authorize_supervised_timeout(
            timeout_id=armed.timeout.timeout_id,
            supervisor_lease=lease,
            supervisor_clock=lambda: T_PLUS_5,
        )
        self.assertTrue(authorization.authorized)
        proof = timeout_adapter_proof(
            timeout=armed.timeout,
            state="timed_out",
            terminal_status="Failed",
            terminal_failure_code="WALL_TIME_EXCEEDED",
            ready_record_hash=self._timeout_ready_record_hash(
                armed.timeout.timeout_id
            ),
        )
        event = {
            "schema_version": "1.0.0",
            "event_id": "event-timeout-terminal-corruption",
            "sequence": self.store.latest_run_event_sequence(task_id) + 1,
            "task_id": task_id,
            "node_id": intent.node_id,
            "event_type": "node_failed",
            "task_status": "Failed",
            "error": {
                "code": "wall_time_exceeded",
                "message": "FWI Worker exceeded its wall-time limit",
                "retryable": False,
            },
            "occurred_at": T_PLUS_5,
            "fingerprint": intent.handle["fingerprint"],
            "extensions": {
                "org.agent_rpc.timeout": {
                    "timeout_id": armed.timeout.timeout_id,
                    "attempt_id": armed.timeout.attempt_id,
                    "wall_time_seconds": armed.timeout.wall_time_seconds,
                    "started_at": armed.timeout.started_at,
                    "deadline_at": armed.timeout.deadline_at,
                    "failure_code": "WALL_TIME_EXCEEDED",
                    "proof_hash": proof["proof_hash"],
                }
            },
        }
        completed = self.store.complete_supervised_timeout(
            timeout_id=armed.timeout.timeout_id,
            result="timeout_confirmed",
            terminal_event=event,
            adapter_proof=proof,
            supervisor_lease=lease,
            supervisor_clock=lambda: T_PLUS_5,
        )
        self.assertEqual(completed.timeout.state, "timed_out")
        connection = self._connection()
        try:
            terminal = connection.execute(
                """
                SELECT document_hash FROM run_events
                WHERE task_id = ? AND sequence = ?
                """,
                (task_id, event["sequence"]),
            ).fetchone()
            original_hash = terminal["document_hash"]
            connection.execute("DROP TRIGGER run_events_are_append_only")
            connection.execute(
                """
                UPDATE run_events SET document_hash = ?
                WHERE task_id = ? AND sequence = ?
                """,
                ("sha256:" + "f" * 64, task_id, event["sequence"]),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(TaskStoreCorruption, "hash does not match"):
            self.store.get_task(task_id)

        connection = self._connection()
        try:
            connection.execute(
                """
                UPDATE run_events SET document_hash = ?,
                                      event_type = 'node_succeeded'
                WHERE task_id = ? AND sequence = ?
                """,
                (original_hash, task_id, event["sequence"]),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(TaskStoreCorruption, "identity does not match"):
            self.store.get_task(task_id)

        tampered_event = copy.deepcopy(event)
        tampered_event["extensions"]["org.agent_rpc.timeout"][
            "proof_hash"
        ] = "sha256:" + "e" * 64
        tampered_json, tampered_hash = encode_document(tampered_event)
        connection = self._connection()
        try:
            connection.execute(
                """
                UPDATE run_events
                SET event_type = 'node_failed', document_json = ?,
                    document_hash = ?
                WHERE task_id = ? AND sequence = ?
                """,
                (
                    tampered_json,
                    tampered_hash,
                    task_id,
                    event["sequence"],
                ),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(
            TaskStoreCorruption, "terminal event is inconsistent"
        ):
            self.store.get_task(task_id)

    def test_timeout_and_user_cancel_delivery_are_first_writer_wins(self) -> None:
        task_id, _, runtime, intent, lease, armed, _ = self._timeout_runtime(
            key="timeout-wins-stop-slot"
        )
        self.store.authorize_supervised_timeout(
            timeout_id=armed.timeout.timeout_id,
            supervisor_lease=lease,
            supervisor_clock=lambda: T_PLUS_5,
        )
        self.assertFalse(runtime.can_cancel_task(task_id, **self.scope))
        with self.assertRaises(TaskStoreConflict):
            self.store.request_task_cancellation(
                task_id=task_id,
                project_id=PROJECT_ID,
                principal_id=PRINCIPAL_ID,
                request_id="cancel-" + "e" * 32,
                reason="user_requested",
                idempotency_key="cancel-after-timeout",
                request_hash="sha256:" + "f" * 64,
                build_documents=lambda *_: ({}, {}),
                clock=lambda: T_PLUS_5,
            )
        connection = self._connection()
        try:
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "authorized timeout already owns the exact stop slot",
            ):
                self._insert_direct_cancel_request(
                    connection,
                    task_id=task_id,
                    intent=intent,
                    attempt_id=armed.timeout.attempt_id,
                    request_id="cancel-" + "d" * 32,
                    event_id="event-direct-cancel-after-timeout",
                )
        finally:
            connection.rollback()
            connection.close()
        self.store.release_runtime_supervisor_lease(
            lease=lease, clock=lambda: T_PLUS_5
        )
        self.now[0] = T_PLUS_5

        cancel_task_id, _, cancel_runtime, _, cancel_lease, cancel_armed, _ = (
            self._timeout_runtime(key="cancel-wins-stop-slot")
        )
        cancelled = cancel_runtime.cancel_task(
            task_id=cancel_task_id,
            reason="user_requested",
            idempotency_key="cancel-wins-before-timeout",
            **self.scope,
        )
        self.assertEqual(cancelled.snapshot.timeout.state, "suppressed")
        with self.assertRaises(TaskStoreConflict):
            self.store.authorize_supervised_timeout(
                timeout_id=cancel_armed.timeout.timeout_id,
                supervisor_lease=cancel_lease,
                supervisor_clock=lambda: T_PLUS_10,
            )
        connection = self._connection()
        try:
            with self.assertRaisesRegex(
                sqlite3.IntegrityError, "due pending window"
            ):
                connection.execute(
                    """
                    INSERT INTO supervised_timeout_attempts(
                        timeout_id, project_id, principal_id, intent_id,
                        attempt_id, fencing_token, action,
                        authorized_at, authorized_at_us
                    ) VALUES (?, ?, ?, ?, ?, ?,
                              'deliver_exact_attempt_timeout', ?, ?)
                    """,
                    (
                        cancel_armed.timeout.timeout_id,
                        PROJECT_ID,
                        PRINCIPAL_ID,
                        cancel_armed.timeout.intent_id,
                        cancel_armed.timeout.attempt_id,
                        cancel_lease.fencing_token,
                        "2026-07-15T03:00:10.000000Z",
                        1784084410000000,
                    ),
                )
        finally:
            connection.rollback()
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

    def test_direct_sql_rejects_unreserved_attempt_two(self) -> None:
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

        new_attempt_id = "attempt-" + "b" * 32
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
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "retry attempt requires its durable reservation",
            ):
                connection.execute(
                    """
                    INSERT INTO worker_launch_attempts(
                        attempt_id, intent_id, task_id, project_id,
                        principal_id, attempt_number, submission_id, job_id,
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
            connection.rollback()
            self.assertEqual(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM worker_launch_attempts
                    WHERE intent_id = ? AND attempt_number = 2
                    """,
                    (scheduled.intent.intent_id,),
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
                ("1.3.0", "1.6.0"),
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
            key="private-receipt-adoption", algorithm_version="1.4.0"
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
