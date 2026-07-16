from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

from scientific_runtime import (
    DispatchDeferred,
    DispatchError,
    DispatchPreparation,
    RegistryService,
    SQLiteTaskStore,
    TaskConflict,
    TaskDispatchError,
    TaskIdempotencyConflict,
    TaskNotFound,
    TaskService,
    TaskStoreConflict,
    TaskStoreCorruption,
    TaskStoreError,
    TaskStoreUnavailable,
    TaskValidationError,
)
from scientific_runtime_contracts import compute_plan_hash, schema_errors
from scientific_runtime.fwi_registry import load_deepwave_manifest
from scientific_runtime.task_store import APPLICATION_ID, encode_document
from tests.test_scientific_runtime_contracts import (
    append_second_plan_node,
    algorithm_manifest,
    approval_decision,
    dataset_ref,
    fingerprint,
    optimizer_plan_graph,
    optimizer_task_draft,
    plan_graph,
    run_event,
    task_draft,
)


NOW = "2026-07-15T03:00:00Z"
PROJECT_ID = "project-1"
PRINCIPAL_ID = "user-1"
CURRENT_ALGORITHM_VERSION = "1.4.0"
CURRENT_ADAPTER_VERSION = "1.4.0"
MANAGED_SUBMISSION_ID = "submission-" + "1" * 64
MANAGED_ATTEMPT_ID = "attempt-" + "2" * 32
MANAGED_JOB_ID = "fwi-20260715T030000Z-000000000001"
MANAGED_REQUEST_HASH = "sha256:" + "a" * 64


def executable_approval_decision(plan: dict) -> dict:
    value = approval_decision(plan)
    value["scope"]["algorithms"] = [
        {"id": "deepwave.acoustic_fwi", "version": CURRENT_ALGORITHM_VERSION}
    ]
    return value


def executable_fingerprint() -> dict:
    value = fingerprint()
    value["algorithm"]["version"] = CURRENT_ALGORITHM_VERSION
    value["adapter_version"] = CURRENT_ADAPTER_VERSION
    return value


def executable_run_event() -> dict:
    value = run_event()
    value["fingerprint"] = executable_fingerprint()
    return value


def dispatch_fingerprint() -> dict:
    value = executable_fingerprint()
    value["provenance_mode"] = "development"
    value["source"] = {"identity_complete": False, "dirty": None}
    return value


def managed_worker_evidence(
    *,
    ticket_state: str = "spawned",
    heartbeat_sequence: int | None = 1,
    heartbeat_state: str = "running",
    attempt_id: str = MANAGED_ATTEMPT_ID,
) -> dict:
    binding = {
        "schema_version": "1.0.0",
        "submission_id": MANAGED_SUBMISSION_ID,
        "attempt_id": attempt_id,
        "attempt_number": 1,
        "job_id": MANAGED_JOB_ID,
        "request_hash": MANAGED_REQUEST_HASH,
        "created_at": NOW,
    }
    binding_hash = encode_document(binding)[1]
    ticket = {
        **binding,
        "binding_hash": binding_hash,
        "state": ticket_state,
        "capacity_slot": 0 if ticket_state == "spawned" else None,
        "capacity_generation": 1 if ticket_state == "spawned" else None,
        "worker_pid": 4242 if ticket_state == "spawned" else None,
        "updated_at": NOW,
    }
    if ticket_state != "spawned":
        return {
            **binding,
            "binding_hash": binding_hash,
            "ticket": {
                "state": ticket_state,
                "capacity_slot": None,
                "capacity_generation": None,
                "worker_pid": None,
                "updated_at": NOW,
                "record_hash": encode_document(ticket)[1],
            },
            "ready": None,
            "heartbeat": None,
        }
    ready = {
        "schema_version": "1.0.0",
        "submission_id": MANAGED_SUBMISSION_ID,
        "attempt_id": attempt_id,
        "attempt_number": 1,
        "binding_hash": binding_hash,
        "job_id": MANAGED_JOB_ID,
        "capacity_slot": 0,
        "capacity_generation": 1,
        "worker_pid": 4242,
        "started_at": NOW,
    }
    heartbeat = None
    if heartbeat_sequence is not None:
        heartbeat_payload = {
            **{key: value for key, value in ready.items() if key != "record_hash"},
            "sequence": heartbeat_sequence,
            "state": heartbeat_state,
            "updated_at": NOW,
        }
        heartbeat = {
            "sequence": heartbeat_sequence,
            "state": heartbeat_state,
            "updated_at": NOW,
            "record_hash": encode_document(heartbeat_payload)[1],
        }
    return {
        **binding,
        "binding_hash": binding_hash,
        "ticket": {
            "state": "spawned",
            "capacity_slot": 0,
            "capacity_generation": 1,
            "worker_pid": 4242,
            "updated_at": NOW,
            "record_hash": encode_document(ticket)[1],
        },
        "ready": {
            "worker_pid": 4242,
            "started_at": NOW,
            "record_hash": encode_document(ready)[1],
        },
        "heartbeat": heartbeat,
    }


class FakeDispatcher:
    def __init__(self, store: SQLiteTaskStore, *, failure_code: str | None = None):
        self.store = store
        self.failure_code = failure_code
        self.defer_dispatch = False
        self.prepare_calls = 0
        self.dispatch_calls = 0
        self.receipt_recovery_calls = 0
        self.receipt_recovery_failure_code: str | None = None
        self.private_receipt_recovery_calls = 0
        self.private_receipt_recovery_failure_code: str | None = None
        self.worker_observation_calls = 0
        self.worker_observation: dict | None = None
        self.worker_observation_failure_code: str | None = None
        self.status_calls = 0
        self.collect_calls = 0
        self.read_calls = 0
        self.purge_calls = 0
        self.purge_ids: list[str] = []
        self.adapter_status: dict | None = None
        self.manifests: list[dict] = []
        self.artifact_data: dict[str, bytes] = {}
        self.lock = threading.Lock()

    def prepare(self, snapshot):
        with self.lock:
            self.prepare_calls += 1
        request = TaskService._expected_dispatch_request(snapshot)
        current_fingerprint = dispatch_fingerprint()
        request["normalized_config_hash"] = current_fingerprint[
            "normalized_config_hash"
        ]
        return DispatchPreparation(
            adapter_id="fwi.deepwave_adapter",
            adapter_version=CURRENT_ADAPTER_VERSION,
            request=request,
            queue_fingerprint=current_fingerprint,
        )

    def dispatch(self, intent):
        # This read uses a second connection and proves that Adapter dispatch is
        # invoked only after the admission transaction committed.
        visible = self.store.get_task(intent.task_id)
        assert visible is not None and visible.status == "Queued"
        budget = self.store.get_approval_budget(
            task_id=intent.task_id, approval_id=intent.approval_id
        )
        assert budget is not None and budget.tasks_used == 1
        assert self.store.get_dispatch_intent(intent.task_id).state == "dispatching"
        with self.lock:
            self.dispatch_calls += 1
        if self.failure_code is not None:
            if self.defer_dispatch:
                if self.failure_code == "ADAPTER_CONCURRENCY_LIMIT":
                    attempt_id = "attempt-" + hashlib.sha256(
                        intent.task_id.encode("utf-8")
                    ).hexdigest()[:32]
                    self.worker_observation = {
                        "evidence": managed_worker_evidence(
                            ticket_state="staged", attempt_id=attempt_id
                        ),
                        "handle": None,
                    }
                raise DispatchDeferred(self.failure_code)
            raise DispatchError(self.failure_code)
        handle = {
            "submission_id": MANAGED_SUBMISSION_ID,
            "task_id": intent.task_id,
            "node_id": intent.node_id,
            "job_id": MANAGED_JOB_ID,
            "idempotency_key": intent.node_idempotency_key,
            "plan_hash": intent.plan_hash,
            "request_hash": MANAGED_REQUEST_HASH,
            "algorithm": copy.deepcopy(intent.request["algorithm"]),
            # The queued fingerprint is preflight evidence.  Runtime events
            # bind to the actual fingerprint returned in this receipt.
            "fingerprint": executable_fingerprint(),
            "adapter_version": intent.adapter_version,
        }
        attempt_id = "attempt-" + hashlib.sha256(
            intent.task_id.encode("utf-8")
        ).hexdigest()[:32]
        self.worker_observation = {
            "evidence": managed_worker_evidence(attempt_id=attempt_id),
            "handle": copy.deepcopy(handle),
        }
        return handle

    @staticmethod
    def supports_supervised_dispatch(intent):
        return (
            intent.adapter_id == "fwi.deepwave_adapter"
            and intent.adapter_version == CURRENT_ADAPTER_VERSION
        )

    def ensure_first_dispatch(self, intent):
        return self.dispatch(intent)

    def recover_existing_receipt(self, intent):
        with self.lock:
            self.receipt_recovery_calls += 1
        if self.receipt_recovery_failure_code is not None:
            raise DispatchError(self.receipt_recovery_failure_code)
        return {
            "submission_id": MANAGED_SUBMISSION_ID,
            "task_id": intent.task_id,
            "node_id": intent.node_id,
            "job_id": MANAGED_JOB_ID,
            "idempotency_key": intent.node_idempotency_key,
            "plan_hash": intent.plan_hash,
            "request_hash": MANAGED_REQUEST_HASH,
            "algorithm": copy.deepcopy(intent.request["algorithm"]),
            "fingerprint": executable_fingerprint(),
            "adapter_version": intent.adapter_version,
        }

    def recover_existing_private_receipt(self, intent):
        with self.lock:
            self.private_receipt_recovery_calls += 1
        if self.private_receipt_recovery_failure_code is not None:
            raise DispatchError(self.private_receipt_recovery_failure_code)
        return {
            "handle": {
                "submission_id": MANAGED_SUBMISSION_ID,
                "task_id": intent.task_id,
                "node_id": intent.node_id,
                "job_id": MANAGED_JOB_ID,
                "idempotency_key": intent.node_idempotency_key,
                "plan_hash": intent.plan_hash,
                "request_hash": MANAGED_REQUEST_HASH,
                "algorithm": copy.deepcopy(intent.request["algorithm"]),
                "fingerprint": executable_fingerprint(),
                "adapter_version": intent.adapter_version,
            },
            "private_schema_version": "1.0.0",
            "receipt_record_hash": "sha256:" + "b" * 64,
        }

    def observe_existing_worker_attempt(self, intent):
        del intent
        with self.lock:
            self.worker_observation_calls += 1
        if self.worker_observation_failure_code is not None:
            raise DispatchError(self.worker_observation_failure_code)
        if self.worker_observation is None:
            raise DispatchError("ADAPTER_SUBMISSION_NOT_FOUND")
        return copy.deepcopy(self.worker_observation)

    def status(self, intent):
        with self.lock:
            self.status_calls += 1
        value = copy.deepcopy(self.adapter_status) if self.adapter_status else {
            "status": "Queued",
            "stage": "queued",
            "completed": 0,
            "total": intent.request["parameters"]["iterations"],
            "message": "FWI job is queued",
            "updated_at": NOW,
            "terminal": False,
        }
        value.update(
            {
                "job_id": intent.handle["job_id"],
                "task_id": intent.task_id,
                "node_id": intent.node_id,
            }
        )
        return value

    def collect(self, intent):
        with self.lock:
            self.collect_calls += 1
        return copy.deepcopy(self.manifests)

    def read_artifact(self, intent, artifact_id):
        with self.lock:
            self.read_calls += 1
        manifest = next(
            value for value in self.manifests if value["artifact_id"] == artifact_id
        )
        return (
            copy.deepcopy(self.manifests),
            copy.deepcopy(manifest),
            self.artifact_data[artifact_id],
        )

    def purge(self, intent, *, purge_id):
        with self.lock:
            self.purge_calls += 1
            self.purge_ids.append(purge_id)
        return {
            "task_id": intent.task_id,
            "purge_id": purge_id,
            "local_run_state": "deleted",
            "replayed": False,
        }


class ScientificRuntimeTaskServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary.name) / "task.sqlite3"
        self.store = SQLiteTaskStore(self.database_path)
        self.registry = RegistryService(self.store, clock=lambda: NOW)
        self.registry.register_dataset(dataset=dataset_ref())
        self.registry.register_algorithm(manifest=algorithm_manifest())
        self.registry.register_algorithm(manifest=load_deepwave_manifest())
        self.next_id = 0

        def make_task_id() -> str:
            self.next_id += 1
            return f"task-generated-{self.next_id:04d}"

        self.service = TaskService(
            self.store, task_id_factory=make_task_id, clock=lambda: NOW
        )
        self.scope = {"project_id": PROJECT_ID, "principal_id": PRINCIPAL_ID}

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def create(self, *, draft: dict | None = None, key: str = "create-key"):
        return self.service.create_task(
            project_id=PROJECT_ID,
            principal_id=PRINCIPAL_ID,
            draft=draft or task_draft(),
            idempotency_key=key,
        )

    def register_project_dataset(self, project_id: str) -> dict:
        dataset = dataset_ref()
        dataset["access_scope"]["project_id"] = project_id
        self.registry.register_dataset(dataset=dataset)
        return dataset

    def persist_plan_and_approval(self, task_id: str) -> tuple[dict, dict]:
        plan = plan_graph()
        self.service.persist_plan(task_id=task_id, plan=plan, **self.scope)
        approval = approval_decision(plan)
        self.service.persist_approval(
            task_id=task_id, approval=approval, **self.scope
        )
        return plan, approval

    def create_executable(
        self, *, draft: dict | None = None, key: str = "create-key"
    ):
        return self.create(draft=draft or optimizer_task_draft(), key=key)

    def persist_executable_plan_and_approval(
        self, task_id: str, *, plan: dict | None = None
    ) -> tuple[dict, dict]:
        snapshot = self.store.get_task(task_id)
        self.assertIsNotNone(snapshot)
        current_plan = (
            copy.deepcopy(plan) if plan is not None else optimizer_plan_graph()
        )
        current_plan["draft"] = {
            "draft_id": snapshot.draft["draft_id"],
            "revision": snapshot.draft["revision"],
        }
        current_plan["plan_hash"] = compute_plan_hash(current_plan)
        self.service.persist_plan(task_id=task_id, plan=current_plan, **self.scope)
        approval = executable_approval_decision(current_plan)
        self.service.persist_approval(
            task_id=task_id, approval=approval, **self.scope
        )
        return current_plan, approval

    def raw_count(self, table: str) -> int:
        self.assertIn(
            table,
            {
                "tasks",
                "draft_revisions",
                "plans",
                "approvals",
                "run_events",
                "idempotency_records",
                "dispatch_intents",
                "dispatch_attempts",
                "dispatch_outcomes",
                "submit_idempotency_links",
                "workbench_mutations",
                "task_abandonments",
                "task_visibility_events",
                "task_visibility",
                "task_visibility_mutations",
                "task_purge_requests",
                "task_purge_idempotency",
                "task_purge_outcomes",
                "worker_launch_attempts",
                "worker_attempt_observations",
                "supervised_dispatch_adoptions",
                "supervised_dispatch_attempts",
                "supervised_private_receipt_adoptions",
            },
        )
        connection = sqlite3.connect(self.database_path)
        try:
            return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        finally:
            connection.close()

    def seed_validated_queue_event(self, task_id: str) -> dict:
        """Admit a real P1 submit fixture through the atomic product boundary."""

        snapshot = self.store.get_task(task_id)
        self.assertIsNotNone(snapshot)
        self.assertIsNotNone(snapshot.approval)
        dispatcher = FakeDispatcher(self.store)
        service = self.submit_service(dispatcher)
        result = service.submit_task(
            task_id=task_id,
            approval_id=snapshot.approval["approval_id"],
            idempotency_key=f"seed-queue-{task_id}",
            **self.scope,
        )
        self.assertEqual(result.intent.state, "pending")
        scheduled, lease = self.schedule_once(service, task_id)
        self.assertEqual(scheduled.intent.state, "dispatched")
        service.release_runtime_supervisor_lease(lease)
        return service.list_run_events(task_id, **self.scope)[0]

    def schedule_once(
        self,
        service: TaskService,
        task_id: str,
        *,
        owner_id: str = "supervisor-test",
    ):
        acquisition = service.acquire_runtime_supervisor_lease(
            **self.scope,
            owner_id=owner_id,
            lease_seconds=30,
        )
        scheduled = service.schedule_runtime_dispatch(
            task_id,
            **self.scope,
            supervisor_lease=acquisition.lease,
        )
        return scheduled, acquisition.lease

    def test_initialization_enables_wal_and_is_reentrant(self) -> None:
        self.assertEqual(self.store.journal_mode(), "wal")
        self.assertEqual(self.store.migration_version(), 10)
        self.assertEqual(os.stat(self.database_path).st_mode & 0o777, 0o600)
        connection = sqlite3.connect(self.database_path)
        try:
            self.assertEqual(
                connection.execute("PRAGMA application_id").fetchone()[0],
                APPLICATION_ID,
            )
            self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            connection.close()

        created = self.create()
        reopened = SQLiteTaskStore(self.database_path)
        self.assertEqual(reopened.journal_mode(), "wal")
        self.assertEqual(reopened.migration_version(), 10)
        self.assertEqual(reopened.get_task(created.snapshot.task_id), created.snapshot)

        def unexpected_call() -> str:
            raise AssertionError("idempotent replay allocated new request state")

        replay = TaskService(
            reopened,
            task_id_factory=unexpected_call,
            clock=unexpected_call,
        ).create_task(
            project_id=PROJECT_ID,
            principal_id=PRINCIPAL_ID,
            draft=created.snapshot.draft,
            idempotency_key="create-key",
        )
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.snapshot.task_id, created.snapshot.task_id)

    def test_aggregate_reads_use_explicit_sqlite_read_transactions(self) -> None:
        created = self.create(key="create-aggregate-read-transaction")
        plan = plan_graph()
        self.service.persist_plan(
            task_id=created.snapshot.task_id,
            plan=plan,
            idempotency_key="aggregate-read-plan-key",
            **self.scope,
        )
        connection = sqlite3.connect(self.database_path)
        try:
            request_hash = connection.execute(
                """
                SELECT request_hash FROM workbench_mutations
                WHERE project_id = ? AND principal_id = ?
                  AND operation = 'persist_plan' AND idempotency_key = ?
                """,
                (PROJECT_ID, PRINCIPAL_ID, "aggregate-read-plan-key"),
            ).fetchone()[0]
        finally:
            connection.close()

        transaction_states: list[bool] = []
        original_load_snapshot = self.store._load_snapshot

        def observe_transaction(connection, task_id):
            transaction_states.append(connection.in_transaction)
            return original_load_snapshot(connection, task_id)

        self.store._load_snapshot = observe_transaction
        try:
            snapshot = self.store.get_task(created.snapshot.task_id)
            mutation = self.store.lookup_workbench_mutation(
                project_id=PROJECT_ID,
                principal_id=PRINCIPAL_ID,
                operation="persist_plan",
                idempotency_key="aggregate-read-plan-key",
                request_hash=request_hash,
            )
        finally:
            del self.store.__dict__["_load_snapshot"]

        self.assertIsNotNone(snapshot)
        self.assertIsNotNone(mutation)
        self.assertEqual(transaction_states, [True, True])

    def test_database_path_must_be_absolute_private_and_non_symlinked(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be absolute"):
            SQLiteTaskStore("relative-task.sqlite3")

        target = Path(self.temporary.name) / "private-target"
        target.mkdir(mode=0o700)
        linked = Path(self.temporary.name) / "linked-parent"
        linked.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            SQLiteTaskStore(linked / "task.sqlite3")

    def test_concurrent_first_initialization_converges(self) -> None:
        database_path = Path(self.temporary.name) / "concurrent-first.sqlite3"
        barrier = threading.Barrier(8)

        def initialize(_: int) -> tuple[str, int]:
            barrier.wait(timeout=5)
            store = SQLiteTaskStore(database_path)
            return store.journal_mode(), store.migration_version()

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(initialize, range(8)))
        self.assertEqual(results, [("wal", 10)] * 8)

    def test_newer_database_migration_is_rejected(self) -> None:
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute("PRAGMA user_version = 11")
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(TaskStoreError, "newer migration"):
            SQLiteTaskStore(self.database_path)

    def test_inconsistent_database_migration_metadata_is_rejected(self) -> None:
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute("PRAGMA user_version = 0")
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(TaskStoreError, "metadata is inconsistent"):
            SQLiteTaskStore(self.database_path)

    def test_existing_non_task_database_is_not_claimed(self) -> None:
        unrelated_path = Path(self.temporary.name) / "unrelated.sqlite3"
        connection = sqlite3.connect(unrelated_path)
        try:
            connection.execute("CREATE TABLE unrelated(value TEXT NOT NULL)")
            connection.execute("INSERT INTO unrelated(value) VALUES ('preserve-me')")
            connection.commit()
        finally:
            connection.close()

        with self.assertRaisesRegex(TaskStoreError, "refusing to claim"):
            SQLiteTaskStore(unrelated_path)

        connection = sqlite3.connect(unrelated_path)
        try:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "delete")
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
            self.assertEqual(
                connection.execute("SELECT value FROM unrelated").fetchone()[0],
                "preserve-me",
            )
            self.assertIsNone(
                connection.execute(
                    """
                    SELECT 1 FROM sqlite_master
                    WHERE type = 'table' AND name = 'schema_migrations'
                    """
                ).fetchone()
            )
        finally:
            connection.close()

    def test_live_schema_tampering_is_rejected_on_reopen(self) -> None:
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute("DROP TRIGGER draft_revisions_are_append_only")
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(TaskStoreCorruption, "schema does not match"):
            SQLiteTaskStore(self.database_path)

    def test_create_is_idempotent_and_task_identity_is_immutable(self) -> None:
        draft = task_draft()
        first = self.create(draft=draft)
        reordered = {key: draft[key] for key in reversed(list(draft))}
        second = self.create(draft=reordered)

        self.assertFalse(first.replayed)
        self.assertTrue(second.replayed)
        self.assertEqual(second.snapshot.task_id, first.snapshot.task_id)
        self.assertEqual(self.raw_count("tasks"), 1)
        self.assertEqual(self.raw_count("draft_revisions"), 1)
        self.assertEqual(self.raw_count("idempotency_records"), 1)

        connection = sqlite3.connect(self.database_path)
        try:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                connection.execute(
                    "UPDATE tasks SET task_id = 'task-replaced' WHERE task_id = ?",
                    (first.snapshot.task_id,),
                )
        finally:
            connection.close()

    def test_idempotency_key_payload_conflict_rolls_back(self) -> None:
        self.create()
        changed = task_draft()
        changed["goal"] = "A different request using the same key."
        with self.assertRaises(TaskIdempotencyConflict):
            self.create(draft=changed)
        self.assertEqual(self.raw_count("tasks"), 1)
        self.assertEqual(self.raw_count("draft_revisions"), 1)
        self.assertEqual(self.raw_count("idempotency_records"), 1)

    def test_compatible_replay_is_exact_hash_and_scope_bound_for_create_and_revise(self) -> None:
        initial = task_draft()
        created = self.create(draft=initial, key="compatible-create-key")
        exact_create = self.service.lookup_compatible_create_task(
            drafts=[initial],
            idempotency_key="compatible-create-key",
            **self.scope,
        )
        self.assertTrue(exact_create.replayed)
        self.assertEqual(exact_create.snapshot.task_id, created.snapshot.task_id)

        changed_initial = copy.deepcopy(initial)
        changed_initial["goal"] = "a different create payload"
        with self.assertRaises(TaskIdempotencyConflict):
            self.service.lookup_compatible_create_task(
                drafts=[changed_initial],
                idempotency_key="compatible-create-key",
                **self.scope,
            )

        _, create_hash = encode_document(
            {
                "project_id": PROJECT_ID,
                "principal_id": PRINCIPAL_ID,
                "draft": initial,
            }
        )
        self.assertIsNone(
            self.store.lookup_compatible_create_task(
                project_id="other-project",
                principal_id=PRINCIPAL_ID,
                idempotency_key="compatible-create-key",
                request_hashes=[create_hash],
            )
        )

        revision = copy.deepcopy(initial)
        revision["revision"] = 2
        revision["goal"] = "exact revision payload"
        self.service.revise_draft(
            task_id=created.snapshot.task_id,
            expected_revision=1,
            draft=revision,
            idempotency_key="compatible-revise-key",
            **self.scope,
        )
        exact_revision = self.service.lookup_compatible_draft_revision(
            task_id=created.snapshot.task_id,
            expected_revision=1,
            drafts=[revision],
            idempotency_key="compatible-revise-key",
            **self.scope,
        )
        self.assertEqual(exact_revision.draft, revision)

        changed_revision = copy.deepcopy(revision)
        changed_revision["goal"] = "a different revision payload"
        with self.assertRaises(TaskIdempotencyConflict):
            self.service.lookup_compatible_draft_revision(
                task_id=created.snapshot.task_id,
                expected_revision=1,
                drafts=[changed_revision],
                idempotency_key="compatible-revise-key",
                **self.scope,
            )

        _, revision_hash = encode_document(
            {
                "task_id": created.snapshot.task_id,
                "project_id": PROJECT_ID,
                "principal_id": PRINCIPAL_ID,
                "expected_revision": 1,
                "draft": revision,
            }
        )
        self.assertIsNone(
            self.store.lookup_compatible_workbench_mutation(
                project_id="other-project",
                principal_id=PRINCIPAL_ID,
                operation="revise_draft",
                idempotency_key="compatible-revise-key",
                request_hashes=[revision_hash],
            )
        )

        other_initial = task_draft()
        other_initial["draft_id"] = "draft-compatible-other-task"
        other = self.create(
            draft=other_initial, key="compatible-other-task"
        )
        other_revision = copy.deepcopy(revision)
        other_revision["draft_id"] = other.snapshot.draft["draft_id"]
        with self.assertRaises(TaskIdempotencyConflict):
            self.service.lookup_compatible_draft_revision(
                task_id=other.snapshot.task_id,
                expected_revision=1,
                drafts=[other_revision],
                idempotency_key="compatible-revise-key",
                **self.scope,
            )

    def test_corrupt_idempotency_scope_fails_closed(self) -> None:
        foreign_dataset = self.register_project_dataset("project-2")
        foreign_draft = task_draft()
        foreign_draft["draft_id"] = "draft-foreign-scope"
        foreign_draft["datasets"] = [foreign_dataset]
        foreign = self.service.create_task(
            project_id="project-2",
            principal_id=PRINCIPAL_ID,
            draft=foreign_draft,
            idempotency_key="foreign-create-key",
        )
        requested_draft = task_draft()
        _, request_hash = encode_document(
            {
                "project_id": PROJECT_ID,
                "principal_id": PRINCIPAL_ID,
                "draft": requested_draft,
            }
        )
        response_json, _ = encode_document({"task_id": foreign.snapshot.task_id})
        values = (
            PROJECT_ID,
            PRINCIPAL_ID,
            "corrupt-scope-key",
            request_hash,
            foreign.snapshot.task_id,
            response_json,
            NOW,
        )
        insert = """
            INSERT INTO idempotency_records(
                project_id, principal_id, operation, idempotency_key,
                request_hash, task_id, response_json, created_at
            ) VALUES (?, ?, 'create_task', ?, ?, ?, ?, ?)
        """
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(insert, values)
            connection.rollback()
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute(insert, values)
            connection.commit()
        finally:
            connection.close()

        with self.assertRaisesRegex(TaskStoreCorruption, "crosses its project"):
            self.service.create_task(
                project_id=PROJECT_ID,
                principal_id=PRINCIPAL_ID,
                draft=requested_draft,
                idempotency_key="corrupt-scope-key",
            )

    def test_mid_create_uniqueness_failure_rolls_back_inserted_task(self) -> None:
        draft = task_draft()
        self.create(draft=draft, key="create-one")
        with self.assertRaises(TaskConflict):
            self.create(draft=draft, key="create-two")
        self.assertEqual(self.raw_count("tasks"), 1)
        self.assertEqual(self.raw_count("draft_revisions"), 1)
        self.assertEqual(self.raw_count("idempotency_records"), 1)

    def test_different_keys_and_draft_ids_create_distinct_tasks(self) -> None:
        first = self.create(key="create-one")
        other = task_draft()
        other["draft_id"] = "draft-002"
        second = self.create(draft=other, key="create-two")
        self.assertNotEqual(first.snapshot.task_id, second.snapshot.task_id)
        self.assertEqual(self.raw_count("tasks"), 2)

    def test_invalid_initial_draft_never_creates_partial_state(self) -> None:
        invalid = task_draft()
        invalid.pop("extensions")
        with self.assertRaises(TaskValidationError):
            self.create(draft=invalid)
        self.assertEqual(self.raw_count("tasks"), 0)
        self.assertEqual(self.raw_count("idempotency_records"), 0)

    def test_scope_isolation_and_approval_actor_are_enforced(self) -> None:
        created = self.create_executable()
        task_id = created.snapshot.task_id
        with self.assertRaises(TaskNotFound):
            self.service.get_task(
                task_id, project_id="project-2", principal_id=PRINCIPAL_ID
            )

        revision = copy.deepcopy(created.snapshot.draft)
        revision["revision"] = 2
        with self.assertRaises(TaskNotFound):
            self.service.revise_draft(
                task_id=task_id,
                project_id=PROJECT_ID,
                principal_id="user-2",
                expected_revision=1,
                draft=revision,
            )

        plan = optimizer_plan_graph()
        with self.assertRaises(TaskNotFound):
            self.service.persist_plan(
                task_id=task_id,
                project_id="project-2",
                principal_id=PRINCIPAL_ID,
                plan=plan,
            )
        self.service.persist_plan(task_id=task_id, plan=plan, **self.scope)
        approval = executable_approval_decision(plan)
        with self.assertRaises(TaskNotFound):
            self.service.persist_approval(
                task_id=task_id,
                project_id="project-2",
                principal_id=PRINCIPAL_ID,
                approval=approval,
            )
        foreign_approval = executable_approval_decision(plan)
        foreign_approval["actor"]["id"] = "user-2"
        with self.assertRaisesRegex(TaskValidationError, "APPROVAL_ACTOR_MISMATCH"):
            self.service.persist_approval(
                task_id=task_id, approval=foreign_approval, **self.scope
            )
        self.assertEqual(self.raw_count("approvals"), 0)

        self.service.persist_approval(task_id=task_id, approval=approval, **self.scope)
        with self.assertRaises(TaskNotFound):
            self.service.list_run_events(
                task_id,
                project_id="project-2",
                principal_id=PRINCIPAL_ID,
            )
        self.seed_validated_queue_event(task_id)
        started = executable_run_event()
        started.update(
            {"event_id": "event-started-002", "sequence": 2, "task_id": task_id}
        )
        with self.assertRaises(TaskNotFound):
            self.service.record_run_event(
                task_id=task_id,
                project_id="project-2",
                principal_id=PRINCIPAL_ID,
                expected_status="Queued",
                event=started,
            )

        other_dataset = self.register_project_dataset("project-2")
        other_draft = task_draft()
        other_draft["draft_id"] = "draft-other-scope"
        other_draft["datasets"] = [other_dataset]
        other = self.service.create_task(
            project_id="project-2",
            principal_id=PRINCIPAL_ID,
            draft=other_draft,
            idempotency_key="create-key",
        )
        self.assertNotEqual(other.snapshot.task_id, task_id)

    def test_task_list_is_scope_bound_keyset_paginated_and_read_only(self) -> None:
        created_ids: list[str] = []
        for number in range(1, 5):
            draft = task_draft()
            draft["draft_id"] = f"draft-list-{number:04d}"
            created_ids.append(
                self.create(draft=draft, key=f"create-list-{number:04d}")
                .snapshot.task_id
            )

        foreign_draft = task_draft()
        foreign_draft["draft_id"] = "draft-list-foreign-principal"
        self.store.create_task(
            task_id="task-list-foreign-principal",
            project_id=PROJECT_ID,
            principal_id="user-2",
            draft=foreign_draft,
            idempotency_key="create-list-foreign-principal",
            request_hash="sha256:" + "f" * 64,
            now=NOW,
        )

        before = {
            table: self.raw_count(table)
            for table in (
                "run_events",
                "dispatch_intents",
                "dispatch_attempts",
                "dispatch_outcomes",
                "workbench_mutations",
                "task_abandonments",
            )
        }
        transaction_states: list[bool] = []
        original_load_snapshot = self.store._load_snapshot

        def observe_transaction(connection, task_id):
            transaction_states.append(connection.in_transaction)
            return original_load_snapshot(connection, task_id)

        self.store._load_snapshot = observe_transaction
        try:
            first = self.service.list_tasks(limit=2, **self.scope)
        finally:
            del self.store.__dict__["_load_snapshot"]
        self.assertEqual(
            [snapshot.task_id for snapshot in first.snapshots],
            list(reversed(created_ids[2:4])),
        )
        self.assertEqual(first.next_cursor, created_ids[2])
        self.assertTrue(transaction_states)
        self.assertTrue(all(transaction_states))

        # A new task sorts ahead of the cursor.  It must neither duplicate nor
        # displace the older remainder of the already-started traversal.
        inserted = task_draft()
        inserted["draft_id"] = "draft-list-inserted"
        inserted_id = self.create(
            draft=inserted, key="create-list-inserted"
        ).snapshot.task_id
        second = self.service.list_tasks(
            cursor=first.next_cursor, limit=2, **self.scope
        )
        self.assertEqual(
            [snapshot.task_id for snapshot in second.snapshots],
            list(reversed(created_ids[0:2])),
        )
        self.assertIsNone(second.next_cursor)
        self.assertNotIn(
            inserted_id,
            [snapshot.task_id for snapshot in first.snapshots + second.snapshots],
        )
        self.assertNotIn(
            "task-list-foreign-principal",
            [snapshot.task_id for snapshot in first.snapshots + second.snapshots],
        )
        self.assertEqual(
            {
                table: self.raw_count(table)
                for table in (
                    "run_events",
                    "dispatch_intents",
                    "dispatch_attempts",
                    "dispatch_outcomes",
                    "workbench_mutations",
                    "task_abandonments",
                )
            },
            before,
        )

        def unexpected_call():
            raise AssertionError("task listing must not allocate mutable state")

        reopened = TaskService(
            SQLiteTaskStore(self.database_path),
            task_id_factory=unexpected_call,
            clock=unexpected_call,
        ).list_tasks(limit=50, **self.scope)
        self.assertEqual(
            [snapshot.task_id for snapshot in reopened.snapshots],
            [inserted_id, *list(reversed(created_ids))],
        )

    def test_task_list_rejects_invalid_limits_and_opaque_or_cross_scope_cursors(
        self,
    ) -> None:
        draft = task_draft()
        draft["draft_id"] = "draft-list-validation"
        self.create(draft=draft, key="create-list-validation")

        for limit in (True, 0, 51, 1.0):
            with self.subTest(limit=limit):
                with self.assertRaises(TaskValidationError) as raised:
                    self.service.list_tasks(limit=limit, **self.scope)
                self.assertEqual(raised.exception.code, "INVALID_TASK_LIST_LIMIT")

        for cursor in (True, "", "task/not-opaque"):
            with self.subTest(cursor=cursor):
                with self.assertRaises(TaskValidationError) as raised:
                    self.service.list_tasks(cursor=cursor, **self.scope)
                self.assertEqual(raised.exception.code, "INVALID_TASK_CURSOR")

        foreign_draft = task_draft()
        foreign_draft["draft_id"] = "draft-list-cross-scope-cursor"
        self.store.create_task(
            task_id="task-list-cross-scope-cursor",
            project_id="project-2",
            principal_id=PRINCIPAL_ID,
            draft=foreign_draft,
            idempotency_key="create-list-cross-scope-cursor",
            request_hash="sha256:" + "e" * 64,
            now=NOW,
        )
        cursor_errors = []
        for cursor in ("task-list-cross-scope-cursor", "task-list-missing"):
            with self.subTest(cursor=cursor):
                with self.assertRaises(TaskValidationError) as raised:
                    self.service.list_tasks(cursor=cursor, **self.scope)
                cursor_errors.append((raised.exception.code, raised.exception.errors))
        self.assertEqual(cursor_errors[0], cursor_errors[1])
        self.assertEqual(cursor_errors[0][0], "INVALID_TASK_CURSOR")

        for limit in (True, 0, 51):
            with self.subTest(store_limit=limit):
                with self.assertRaises(TaskStoreConflict):
                    self.store.list_tasks(limit=limit, **self.scope)

    def test_terminal_task_visibility_is_scoped_append_only_cas_and_reversible(
        self,
    ) -> None:
        created = self.create(key="create-visibility")
        task_id = created.snapshot.task_id
        self.assertEqual(created.snapshot.visibility_revision, 0)
        self.assertIsNone(created.snapshot.trashed_at)

        with self.assertRaises(TaskConflict):
            self.service.trash_task(
                task_id=task_id,
                expected_visibility_revision=0,
                idempotency_key="trash-before-terminal",
                **self.scope,
            )

        abandoned = self.service.abandon_task(
            task_id=task_id,
            idempotency_key="abandon-before-trash",
            **self.scope,
        )
        self.assertEqual(abandoned.snapshot.status, "Cancelled")
        first = self.service.trash_task(
            task_id=task_id,
            expected_visibility_revision=0,
            idempotency_key="trash-terminal",
            **self.scope,
        )
        self.assertFalse(first.replayed)
        self.assertEqual(first.snapshot.visibility_revision, 1)
        self.assertEqual(first.snapshot.trashed_at, NOW)
        replay = self.service.trash_task(
            task_id=task_id,
            expected_visibility_revision=0,
            idempotency_key="trash-terminal",
            **self.scope,
        )
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.snapshot.visibility_revision, 1)
        self.assertEqual(self.raw_count("task_visibility_events"), 1)
        self.assertEqual(self.raw_count("task_visibility_mutations"), 1)

        self.assertEqual(
            [item.task_id for item in self.service.list_tasks(**self.scope).snapshots],
            [],
        )
        trashed = self.service.list_tasks(view="trash", **self.scope)
        self.assertEqual([item.task_id for item in trashed.snapshots], [task_id])
        self.assertEqual(trashed.snapshots[0].visibility_revision, 1)
        self.assertEqual(
            self.service.get_task(task_id, **self.scope).trashed_at,
            NOW,
        )

        with self.assertRaises(TaskConflict):
            self.service.restore_task(
                task_id=task_id,
                expected_visibility_revision=0,
                idempotency_key="restore-stale",
                **self.scope,
            )
        restored = self.service.restore_task(
            task_id=task_id,
            expected_visibility_revision=1,
            idempotency_key="restore-terminal",
            **self.scope,
        )
        self.assertFalse(restored.replayed)
        self.assertEqual(restored.snapshot.status, "Cancelled")
        self.assertEqual(restored.snapshot.visibility_revision, 2)
        self.assertIsNone(restored.snapshot.trashed_at)
        self.assertEqual(
            [item.task_id for item in self.service.list_tasks(**self.scope).snapshots],
            [task_id],
        )
        self.assertEqual(
            self.service.list_tasks(view="trash", **self.scope).snapshots,
            (),
        )

        foreign = TaskService(self.store, clock=lambda: NOW)
        for missing in (task_id, "task-does-not-exist"):
            scope = (
                {"project_id": "other-project", "principal_id": PRINCIPAL_ID}
                if missing == task_id
                else self.scope
            )
            with self.subTest(missing=missing):
                with self.assertRaises(TaskNotFound) as caught:
                    foreign.trash_task(
                        task_id=missing,
                        expected_visibility_revision=2,
                        idempotency_key="scope-hidden",
                        **scope,
                    )
                self.assertEqual(
                    str(caught.exception),
                    "task does not exist in the requested scope",
                )

        connection = sqlite3.connect(self.database_path)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE task_visibility_events SET action = 'restored'"
                )
            connection.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM task_visibility_events")
        finally:
            connection.rollback()
            connection.close()

    def test_permanent_delete_purges_trashed_abandoned_task_once(self) -> None:
        created = self.create(key="create-purge-abandoned")
        task_id = created.snapshot.task_id
        self.service.abandon_task(
            task_id=task_id,
            idempotency_key="abandon-purge-abandoned",
            **self.scope,
        )
        trashed = self.service.trash_task(
            task_id=task_id,
            expected_visibility_revision=0,
            idempotency_key="trash-purge-abandoned",
            **self.scope,
        )

        hidden_errors = []
        for hidden_task_id, hidden_scope in (
            (
                task_id,
                {"project_id": "other-project", "principal_id": PRINCIPAL_ID},
            ),
            ("task-purge-missing", self.scope),
        ):
            with self.assertRaises(TaskNotFound) as hidden:
                self.service.purge_task(
                    task_id=hidden_task_id,
                    expected_visibility_revision=1,
                    idempotency_key="purge-scope-hidden",
                    **hidden_scope,
                )
            hidden_errors.append(str(hidden.exception))
        self.assertEqual(hidden_errors[0], hidden_errors[1])

        first = self.service.purge_task(
            task_id=task_id,
            expected_visibility_revision=trashed.snapshot.visibility_revision,
            idempotency_key="purge-abandoned",
            **self.scope,
        )
        replay = self.service.purge_task(
            task_id=task_id,
            expected_visibility_revision=trashed.snapshot.visibility_revision,
            idempotency_key="purge-abandoned",
            **self.scope,
        )

        self.assertEqual(first.task_id, task_id)
        self.assertEqual(first.purge_state, "purged")
        self.assertEqual(first.local_run_state, "not_created")
        self.assertTrue(first.audit_retained)
        self.assertFalse(first.replayed)
        self.assertEqual(replay.purge_id, first.purge_id)
        self.assertTrue(replay.replayed)
        self.assertEqual(
            self.service.list_tasks(view="trash", **self.scope).snapshots,
            (),
        )
        with self.assertRaises(TaskNotFound):
            self.service.get_task(task_id, **self.scope)
        with self.assertRaises(TaskNotFound):
            self.service.restore_task(
                task_id=task_id,
                expected_visibility_revision=1,
                idempotency_key="restore-after-purge",
                **self.scope,
            )
        self.assertEqual(self.raw_count("task_purge_requests"), 1)
        self.assertEqual(self.raw_count("task_purge_outcomes"), 1)
        self.assertEqual(self.raw_count("task_purge_idempotency"), 1)

    def test_trash_rejects_terminal_state_without_resolved_execution_evidence(
        self,
    ) -> None:
        created = self.create(key="create-unresolved-visibility")
        task_id = created.snapshot.task_id
        connection = sqlite3.connect(self.database_path)
        try:
            # Construct an otherwise unreachable legacy/corrupt terminal row
            # to prove both Store and migration fail closed. Normal task APIs
            # cannot create Cancelled without abandonment or runtime evidence.
            connection.execute("DROP TRIGGER runtime_status_requires_latest_event")
            connection.execute(
                "UPDATE tasks SET status = 'Cancelled' WHERE task_id = ?",
                (task_id,),
            )
            connection.commit()
        finally:
            connection.close()

        _, request_hash = encode_document(
            {
                "task_id": task_id,
                "project_id": PROJECT_ID,
                "principal_id": PRINCIPAL_ID,
                "action": "trash_task",
                "expected_visibility_revision": 0,
            }
        )
        with self.assertRaisesRegex(TaskStoreConflict, "unresolved"):
            self.store.change_task_visibility(
                task_id=task_id,
                project_id=PROJECT_ID,
                principal_id=PRINCIPAL_ID,
                operation="trash_task",
                expected_visibility_revision=0,
                idempotency_key="trash-unresolved-terminal",
                request_hash=request_hash,
                now=NOW,
            )

        connection = sqlite3.connect(self.database_path)
        try:
            with self.assertRaisesRegex(
                sqlite3.IntegrityError, "resolved terminal"
            ):
                connection.execute(
                    """
                    INSERT INTO task_visibility_events(
                        task_id, project_id, principal_id, revision, event_id,
                        action, previous_state, state, trashed_at,
                        document_json, document_hash, occurred_at, recorded_at
                    ) VALUES (?, ?, ?, 1, ?, 'trashed', 'active', 'trashed', ?,
                              '{}', ?, ?, ?)
                    """,
                    (
                        task_id,
                        PROJECT_ID,
                        PRINCIPAL_ID,
                        "visibility-unresolved-direct",
                        NOW,
                        "sha256:" + "a" * 64,
                        NOW,
                        NOW,
                    ),
                )
        finally:
            connection.rollback()
            connection.close()

    def test_trash_rejects_legacy_approved_abandonment_without_dispatch(
        self,
    ) -> None:
        created = self.create(key="create-legacy-approved-abandonment")
        task_id = created.snapshot.task_id
        self.persist_plan_and_approval(task_id)
        abandonment = {
            "schema_version": "1.0.0",
            "task_id": task_id,
            "previous_status": "AwaitingApproval",
            "status": "Cancelled",
            "reason": "user_discarded_draft",
            "actor": {"type": "user", "id": PRINCIPAL_ID},
            "abandoned_at": NOW,
            "extensions": {},
        }
        abandonment_json, abandonment_hash = encode_document(abandonment)
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            # Recreate the state that v4/v5 allowed before v6 added the
            # approved-abandon guard.  Immutable legacy evidence is retained,
            # but it must not become eligible for Trash after upgrade.
            connection.execute("DROP TRIGGER task_abandonments_require_pre_runtime_task")
            connection.execute("DROP TRIGGER runtime_status_requires_latest_event")
            connection.execute(
                """
                INSERT INTO task_abandonments(
                    task_id, project_id, principal_id, document_json,
                    document_hash, abandoned_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    PROJECT_ID,
                    PRINCIPAL_ID,
                    abandonment_json,
                    abandonment_hash,
                    NOW,
                ),
            )
            connection.execute(
                "UPDATE tasks SET status = 'Cancelled' WHERE task_id = ?",
                (task_id,),
            )
            connection.commit()
        finally:
            connection.close()

        _, request_hash = encode_document(
            {
                "task_id": task_id,
                "project_id": PROJECT_ID,
                "principal_id": PRINCIPAL_ID,
                "action": "trash_task",
                "expected_visibility_revision": 0,
            }
        )
        with self.assertRaisesRegex(TaskStoreConflict, "unresolved"):
            self.store.change_task_visibility(
                task_id=task_id,
                project_id=PROJECT_ID,
                principal_id=PRINCIPAL_ID,
                operation="trash_task",
                expected_visibility_revision=0,
                idempotency_key="trash-legacy-approved-abandonment",
                request_hash=request_hash,
                now=NOW,
            )

        connection = sqlite3.connect(self.database_path)
        try:
            with self.assertRaisesRegex(
                sqlite3.IntegrityError, "resolved terminal"
            ):
                connection.execute(
                    """
                    INSERT INTO task_visibility_events(
                        task_id, project_id, principal_id, revision, event_id,
                        action, previous_state, state, trashed_at,
                        document_json, document_hash, occurred_at, recorded_at
                    ) VALUES (?, ?, ?, 1, ?, 'trashed', 'active', 'trashed', ?,
                              '{}', ?, ?, ?)
                    """,
                    (
                        task_id,
                        PROJECT_ID,
                        PRINCIPAL_ID,
                        "visibility-legacy-approved-direct",
                        NOW,
                        "sha256:" + "b" * 64,
                        NOW,
                        NOW,
                    ),
                )
        finally:
            connection.rollback()
            connection.close()

    def test_dispatched_terminal_task_can_be_trashed_and_purged(self) -> None:
        task_id, _, dispatcher, service = self.submitted_runtime(
            key="visibility-dispatched-success"
        )
        dispatcher.adapter_status = {
            "status": "Succeeded",
            "stage": "complete",
            "completed": 2,
            "total": 2,
            "message": "complete",
            "updated_at": "2026-07-15T03:08:00Z",
            "terminal": True,
        }
        terminal = service.refresh_runtime_status(task_id, **self.scope)
        self.assertEqual(terminal.snapshot.status, "Succeeded")
        trashed = service.trash_task(
            task_id=task_id,
            expected_visibility_revision=0,
            idempotency_key="trash-dispatched-success",
            **self.scope,
        )
        self.assertEqual(trashed.snapshot.visibility_revision, 1)
        self.assertEqual(trashed.snapshot.trashed_at, NOW)
        purged = service.purge_task(
            task_id=task_id,
            expected_visibility_revision=1,
            idempotency_key="purge-dispatched-success",
            **self.scope,
        )
        replay = service.purge_task(
            task_id=task_id,
            expected_visibility_revision=1,
            idempotency_key="purge-dispatched-success",
            **self.scope,
        )
        self.assertEqual(purged.local_run_state, "deleted")
        self.assertFalse(purged.replayed)
        self.assertEqual(replay.purge_id, purged.purge_id)
        self.assertTrue(replay.replayed)
        self.assertEqual(dispatcher.purge_calls, 1)
        self.assertEqual(dispatcher.purge_ids, [purged.purge_id])
        with self.assertRaises(TaskNotFound):
            service.get_task(task_id, **self.scope)

    def test_approved_pre_runtime_task_cannot_be_abandoned(self) -> None:
        created = self.create(key="create-approved-abandon-guard")
        self.persist_plan_and_approval(created.snapshot.task_id)
        with self.assertRaises(TaskConflict):
            self.service.abandon_task(
                task_id=created.snapshot.task_id,
                idempotency_key="approved-abandon-blocked",
                **self.scope,
            )
        self.assertEqual(
            self.store.get_task(created.snapshot.task_id).status,
            "AwaitingApproval",
        )
        self.assertEqual(self.raw_count("task_abandonments"), 0)

    def test_draft_revisions_append_and_invalidate_current_plan(self) -> None:
        created = self.create()
        task_id = created.snapshot.task_id
        old_plan, old_approval = self.persist_plan_and_approval(task_id)

        revision = copy.deepcopy(created.snapshot.draft)
        revision["revision"] = 2
        revision["parameters"]["iterations"] = 3
        revised = self.service.revise_draft(
            task_id=task_id,
            expected_revision=1,
            draft=revision,
            **self.scope,
        )
        self.assertEqual(revised.task_id, task_id)
        self.assertEqual(revised.draft, revision)
        self.assertIsNone(revised.plan)
        self.assertIsNone(revised.approval)
        self.assertEqual(self.store.draft_history(task_id), [created.snapshot.draft, revision])
        self.assertEqual(self.store.plan_history(task_id), [old_plan])
        self.assertEqual(self.store.approval_history(task_id), [old_approval])

    def test_stale_draft_revision_is_atomic(self) -> None:
        created = self.create()
        revision = copy.deepcopy(created.snapshot.draft)
        revision["revision"] = 2
        self.service.revise_draft(
            task_id=created.snapshot.task_id,
            expected_revision=1,
            draft=revision,
            **self.scope,
        )
        stale = copy.deepcopy(revision)
        stale["goal"] = "Stale concurrent update"
        with self.assertRaises(TaskConflict):
            self.service.revise_draft(
                task_id=created.snapshot.task_id,
                expected_revision=1,
                draft=stale,
                **self.scope,
            )
        self.assertEqual(len(self.store.draft_history(created.snapshot.task_id)), 2)

    def test_plan_and_approval_are_hash_bound_and_survive_reopen(self) -> None:
        created = self.create()
        task_id = created.snapshot.task_id
        plan, approval = self.persist_plan_and_approval(task_id)

        reopened_service = TaskService(SQLiteTaskStore(self.database_path))
        restored = reopened_service.get_task(task_id, **self.scope)
        self.assertEqual(restored.plan, plan)
        self.assertEqual(restored.approval, approval)

        bad_plan = copy.deepcopy(plan)
        bad_plan["nodes"][0]["parameters"]["iterations"] = 3
        with self.assertRaisesRegex(TaskValidationError, "PLAN_HASH_INVALID"):
            self.service.persist_plan(
                task_id=task_id, plan=bad_plan, **self.scope
            )

        bad_approval = copy.deepcopy(approval)
        bad_approval["approval_id"] = "approval-other"
        bad_approval["plan_hash"] = "sha256:" + "0" * 64
        with self.assertRaises(TaskConflict):
            self.service.persist_approval(
                task_id=task_id, approval=bad_approval, **self.scope
            )
        self.assertEqual(self.raw_count("approvals"), 1)

    def test_plan_semantics_cannot_drift_from_current_draft(self) -> None:
        created = self.create()
        plan = plan_graph()
        plan["nodes"][0]["parameters"]["iterations"] = 3
        plan["plan_hash"] = compute_plan_hash(plan)
        with self.assertRaisesRegex(TaskValidationError, "PLAN_DRAFT_MISMATCH"):
            self.service.persist_plan(
                task_id=created.snapshot.task_id, plan=plan, **self.scope
            )
        self.assertEqual(self.raw_count("plans"), 0)

    def test_exact_plan_or_approval_replay_does_not_reactivate_old_state(self) -> None:
        created = self.create()
        task_id = created.snapshot.task_id
        plan, first_approval = self.persist_plan_and_approval(task_id)

        second_approval = copy.deepcopy(first_approval)
        second_approval["approval_id"] = "approval-002"
        second = self.service.persist_approval(
            task_id=task_id, approval=second_approval, **self.scope
        )
        self.assertEqual(second.approval, second_approval)

        replayed_plan = self.service.persist_plan(
            task_id=task_id, plan=plan, **self.scope
        )
        self.assertEqual(replayed_plan.approval, second_approval)
        replayed_old_approval = self.service.persist_approval(
            task_id=task_id, approval=first_approval, **self.scope
        )
        self.assertEqual(replayed_old_approval.approval, second_approval)
        self.assertEqual(self.raw_count("plans"), 1)
        self.assertEqual(self.raw_count("approvals"), 2)

    def test_legacy_exact_plan_and_approval_reserve_new_idempotency_keys(
        self,
    ) -> None:
        created = self.create(key="create-legacy-exact-idempotency")
        task_id = created.snapshot.task_id
        plan, approval = self.persist_plan_and_approval(task_id)
        self.assertEqual(self.raw_count("workbench_mutations"), 0)

        bound_plan = self.service.persist_plan(
            task_id=task_id,
            plan=plan,
            idempotency_key="bind-legacy-exact-plan",
            **self.scope,
        )
        replayed_plan = self.service.persist_plan(
            task_id=task_id,
            plan=plan,
            idempotency_key="bind-legacy-exact-plan",
            **self.scope,
        )
        self.assertEqual(bound_plan.plan, plan)
        self.assertEqual(replayed_plan, bound_plan)

        conflicting_plan = copy.deepcopy(plan)
        conflicting_plan["plan_id"] = "plan-idempotency-conflict"
        conflicting_plan["plan_hash"] = compute_plan_hash(conflicting_plan)
        with self.assertRaises(TaskIdempotencyConflict):
            self.service.persist_plan(
                task_id=task_id,
                plan=conflicting_plan,
                idempotency_key="bind-legacy-exact-plan",
                **self.scope,
            )

        bound_approval = self.service.persist_approval(
            task_id=task_id,
            approval=approval,
            idempotency_key="bind-legacy-exact-approval",
            **self.scope,
        )
        replayed_approval = self.service.persist_approval(
            task_id=task_id,
            approval=approval,
            idempotency_key="bind-legacy-exact-approval",
            **self.scope,
        )
        self.assertEqual(bound_approval.approval, approval)
        self.assertEqual(replayed_approval, bound_approval)

        conflicting_approval = copy.deepcopy(approval)
        conflicting_approval["approval_id"] = "approval-idempotency-conflict"
        with self.assertRaises(TaskIdempotencyConflict):
            self.service.persist_approval(
                task_id=task_id,
                approval=conflicting_approval,
                idempotency_key="bind-legacy-exact-approval",
                **self.scope,
            )

        self.assertEqual(self.raw_count("plans"), 1)
        self.assertEqual(self.raw_count("approvals"), 1)
        self.assertEqual(self.raw_count("workbench_mutations"), 2)

    def test_hash_consistent_cross_operation_workbench_outcomes_fail_closed(
        self,
    ) -> None:
        created = self.create(key="create-workbench-outcome-tamper")
        task_id = created.snapshot.task_id
        revision = copy.deepcopy(created.snapshot.draft)
        revision["revision"] = 2
        self.service.revise_draft(
            task_id=task_id,
            expected_revision=1,
            draft=revision,
            idempotency_key="outcome-schema-revise",
            **self.scope,
        )

        plan = plan_graph()
        plan["plan_id"] = "plan-outcome-schema"
        plan["draft"] = {"draft_id": revision["draft_id"], "revision": 2}
        plan["plan_hash"] = compute_plan_hash(plan)
        self.service.persist_plan(
            task_id=task_id,
            plan=plan,
            idempotency_key="outcome-schema-plan",
            **self.scope,
        )
        approval = approval_decision(plan)
        approval["approval_id"] = "approval-outcome-schema"
        approval["decision"] = "rejected"
        self.service.persist_approval(
            task_id=task_id,
            approval=approval,
            idempotency_key="outcome-schema-approval",
            **self.scope,
        )
        self.service.abandon_task(
            task_id=task_id,
            idempotency_key="outcome-schema-abandon",
            **self.scope,
        )
        self.assertEqual(self.raw_count("workbench_mutations"), 4)

        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                """
                SELECT project_id, principal_id, operation, idempotency_key,
                       request_hash
                FROM workbench_mutations
                ORDER BY operation
                """
            ).fetchall()
            forged_outcomes = {
                "revise_draft": {
                    "task_id": task_id,
                    "plan_id": "plan-forged",
                    "plan_hash": "sha256:" + "a" * 64,
                },
                "persist_plan": {
                    "task_id": task_id,
                    "approval_id": "approval-forged",
                    "decision": "approved",
                },
                "persist_approval": {
                    "task_id": task_id,
                    "status": "Cancelled",
                },
                "abandon_task": {
                    "task_id": task_id,
                    "draft_id": revision["draft_id"],
                    "draft_revision": 2,
                },
            }
            connection.execute("DROP TRIGGER workbench_mutations_are_immutable")
            for row in rows:
                outcome_json, outcome_hash = encode_document(
                    forged_outcomes[row["operation"]]
                )
                connection.execute(
                    """
                    UPDATE workbench_mutations
                    SET outcome_json = ?, outcome_hash = ?
                    WHERE project_id = ? AND principal_id = ?
                      AND operation = ? AND idempotency_key = ?
                    """,
                    (
                        outcome_json,
                        outcome_hash,
                        row["project_id"],
                        row["principal_id"],
                        row["operation"],
                        row["idempotency_key"],
                    ),
                )
            connection.commit()
        finally:
            connection.close()

        for row in rows:
            with self.subTest(operation=row["operation"]):
                with self.assertRaisesRegex(
                    TaskStoreCorruption, "workbench mutation outcome is invalid"
                ):
                    self.store.lookup_workbench_mutation(
                        project_id=row["project_id"],
                        principal_id=row["principal_id"],
                        operation=row["operation"],
                        idempotency_key=row["idempotency_key"],
                        request_hash=row["request_hash"],
                    )

    def test_p1_1_service_cannot_create_a_queued_task(self) -> None:
        queued_draft = task_draft()
        queued_draft["draft_id"] = "draft-direct-queued"
        queued_draft["status"] = "Queued"
        with self.assertRaisesRegex(TaskStoreConflict, "pre-runtime"):
            self.store.create_task(
                task_id="task-direct-queued",
                project_id=self.scope["project_id"],
                principal_id=self.scope["principal_id"],
                draft=queued_draft,
                idempotency_key="create-direct-queued",
                request_hash="sha256:" + "a" * 64,
                now=NOW,
            )
        self.assertIsNone(self.store.get_task("task-direct-queued"))

        connection = sqlite3.connect(self.database_path)
        try:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "start before runtime"):
                connection.execute(
                    """
                    INSERT INTO tasks(
                        task_id, project_id, principal_id, status,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, 'Queued', ?, ?)
                    """,
                    ("task-sql-queued", "project-alpha", "user-alice", NOW, NOW),
                )
        finally:
            connection.close()

        created = self.create()
        task_id = created.snapshot.task_id
        self.persist_plan_and_approval(task_id)
        connection = sqlite3.connect(self.database_path)
        try:
            with self.assertRaisesRegex(
                sqlite3.IntegrityError, "latest run event"
            ):
                connection.execute(
                    "UPDATE tasks SET status = 'Queued' WHERE task_id = ?",
                    (task_id,),
                )
        finally:
            connection.close()
        event = run_event()
        event.update(
            {
                "event_id": "event-queued-001",
                "task_id": task_id,
                "event_type": "task_queued",
                "task_status": "Queued",
            }
        )
        event.pop("node_id", None)
        with self.assertRaisesRegex(TaskConflict, "reserved"):
            self.service.record_run_event(
                task_id=task_id,
                expected_status="AwaitingApproval",
                event=event,
                **self.scope,
            )
        with self.assertRaisesRegex(TaskStoreConflict, "before validated submission"):
            self.store.commit_runtime_transition(
                task_id=task_id,
                expected_status="AwaitingApproval",
                event=event,
                now=NOW,
            )
        self.assertEqual(
            self.service.get_task(task_id, **self.scope).status,
            "AwaitingApproval",
        )
        self.assertEqual(self.raw_count("run_events"), 0)

    def test_corrupt_persisted_document_hash_fails_closed(self) -> None:
        created = self.create()
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute("DROP TRIGGER draft_revisions_are_append_only")
            connection.execute(
                "UPDATE draft_revisions SET document_json = '{}' WHERE task_id = ?",
                (created.snapshot.task_id,),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(TaskStoreCorruption, "hash does not match"):
            self.store.get_task(created.snapshot.task_id)

    def test_corrupt_current_relationships_fail_closed(self) -> None:
        created = self.create()
        task_id = created.snapshot.task_id
        first_plan, _ = self.persist_plan_and_approval(task_id)
        second_plan = plan_graph()
        second_plan["plan_id"] = "plan-002"
        second_plan["plan_hash"] = compute_plan_hash(second_plan)
        self.service.persist_plan(task_id=task_id, plan=second_plan, **self.scope)
        second_approval = approval_decision(second_plan)
        second_approval["approval_id"] = "approval-002"
        self.service.persist_approval(
            task_id=task_id, approval=second_approval, **self.scope
        )

        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "UPDATE tasks SET current_plan_id = ? WHERE task_id = ?",
                (first_plan["plan_id"], task_id),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(TaskStoreCorruption, "current approval"):
            self.store.get_task(task_id)

    def test_runtime_state_and_events_commit_atomically_and_are_append_only(self) -> None:
        created = self.create_executable()
        task_id = created.snapshot.task_id
        self.persist_executable_plan_and_approval(task_id)
        queued_event = self.seed_validated_queue_event(task_id)

        started = executable_run_event()
        started.update(
            {
                "event_id": "event-started-002",
                "sequence": 2,
                "task_id": task_id,
            }
        )
        running = self.service.record_run_event(
            task_id=task_id,
            expected_status="Queued",
            event=started,
            **self.scope,
        )
        self.assertEqual(running.status, "Running")
        self.assertEqual(
            self.service.list_run_events(task_id, **self.scope),
            [queued_event, started],
        )
        self.assertEqual(
            self.service.list_run_events(
                task_id, after_sequence=1, **self.scope
            ),
            [started],
        )
        reopened = TaskService(SQLiteTaskStore(self.database_path))
        self.assertEqual(reopened.get_task(task_id, **self.scope).status, "Running")
        self.assertEqual(
            reopened.list_run_events(task_id, **self.scope),
            [queued_event, started],
        )

        duplicate = copy.deepcopy(started)
        duplicate["sequence"] = 3
        duplicate["event_type"] = "node_progress"
        duplicate["progress"] = {
            "completed": 1,
            "total": 2,
            "unit": "iterations",
            "message": "duplicate event ID must roll back",
        }
        with self.assertRaises(TaskConflict):
            self.service.record_run_event(
                task_id=task_id,
                expected_status="Running",
                event=duplicate,
                **self.scope,
            )
        self.assertEqual(self.service.get_task(task_id, **self.scope).status, "Running")
        self.assertEqual(
            len(self.service.list_run_events(task_id, **self.scope)), 2
        )

        connection = sqlite3.connect(self.database_path)
        try:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                connection.execute(
                    "UPDATE run_events SET event_type = 'node_failed' WHERE event_id = ?",
                    (started["event_id"],),
                )
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                connection.execute(
                    "DELETE FROM run_events WHERE event_id = ?",
                    (started["event_id"],),
                )
        finally:
            connection.close()

    def test_event_insert_rolls_back_when_status_update_fails(self) -> None:
        created = self.create_executable()
        task_id = created.snapshot.task_id
        self.persist_executable_plan_and_approval(task_id)
        queued_event = self.seed_validated_queue_event(task_id)
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                """
                CREATE TRIGGER fail_running_status_for_test
                BEFORE UPDATE OF status ON tasks
                WHEN NEW.status = 'Running'
                BEGIN
                    SELECT RAISE(ABORT, 'injected status failure');
                END
                """
            )
            connection.commit()
        finally:
            connection.close()

        started = executable_run_event()
        started.update(
            {
                "event_id": "event-started-rollback",
                "sequence": 2,
                "task_id": task_id,
            }
        )
        with self.assertRaises(TaskConflict):
            self.service.record_run_event(
                task_id=task_id,
                expected_status="Queued",
                event=started,
                **self.scope,
            )
        self.assertEqual(self.service.get_task(task_id, **self.scope).status, "Queued")
        self.assertEqual(
            self.service.list_run_events(task_id, **self.scope), [queued_event]
        )

    def test_run_event_semantics_are_checked_beyond_json_schema(self) -> None:
        created = self.create_executable()
        task_id = created.snapshot.task_id
        self.persist_executable_plan_and_approval(task_id)
        self.seed_validated_queue_event(task_id)

        incoherent = executable_run_event()
        incoherent.update(
            {
                "event_id": "event-incoherent-002",
                "sequence": 2,
                "task_id": task_id,
                "event_type": "node_failed",
                "task_status": "Succeeded",
            }
        )
        self.assertEqual(schema_errors("run-event.schema.json", incoherent), [])
        with self.assertRaisesRegex(TaskValidationError, "RUN_EVENT_STATE_MISMATCH"):
            self.service.record_run_event(
                task_id=task_id,
                expected_status="Queued",
                event=incoherent,
                **self.scope,
            )

        deferred = executable_run_event()
        deferred.update(
            {
                "event_id": "event-waiting-002",
                "sequence": 2,
                "task_id": task_id,
                "event_type": "node_waiting",
                "task_status": "Waiting",
            }
        )
        self.assertEqual(schema_errors("run-event.schema.json", deferred), [])
        with self.assertRaisesRegex(TaskValidationError, "RUN_EVENT_UNSUPPORTED_IN_P1"):
            self.service.record_run_event(
                task_id=task_id,
                expected_status="Queued",
                event=deferred,
                **self.scope,
            )
        self.assertEqual(self.service.get_task(task_id, **self.scope).status, "Queued")
        self.assertEqual(
            len(self.service.list_run_events(task_id, **self.scope)), 1
        )

        started = executable_run_event()
        started.update(
            {"event_id": "event-started-002", "sequence": 2, "task_id": task_id}
        )
        self.service.record_run_event(
            task_id=task_id,
            expected_status="Queued",
            event=started,
            **self.scope,
        )
        drifted = executable_run_event()
        drifted.update(
            {
                "event_id": "event-drifted-003",
                "sequence": 3,
                "task_id": task_id,
                "event_type": "node_progress",
                "task_status": "Running",
                "progress": {
                    "completed": 1,
                    "total": 2,
                    "unit": "iterations",
                    "message": "fingerprint must remain stable",
                },
            }
        )
        drifted["fingerprint"]["adapter_version"] = "1.0.1"
        self.assertEqual(schema_errors("run-event.schema.json", drifted), [])
        with self.assertRaisesRegex(TaskConflict, "fingerprint changed"):
            self.service.record_run_event(
                task_id=task_id,
                expected_status="Running",
                event=drifted,
                **self.scope,
            )
        self.assertEqual(len(self.service.list_run_events(task_id, **self.scope)), 2)

    def test_p1_single_node_success_is_terminal(self) -> None:
        created = self.create_executable()
        task_id = created.snapshot.task_id
        self.persist_executable_plan_and_approval(task_id)
        self.seed_validated_queue_event(task_id)
        started = executable_run_event()
        started.update(
            {"event_id": "event-started-002", "sequence": 2, "task_id": task_id}
        )
        self.service.record_run_event(
            task_id=task_id,
            expected_status="Queued",
            event=started,
            **self.scope,
        )

        success = executable_run_event()
        success.update(
            {
                "event_id": "event-succeeded-003",
                "sequence": 3,
                "task_id": task_id,
                "event_type": "node_succeeded",
                "task_status": "Running",
            }
        )
        self.assertEqual(schema_errors("run-event.schema.json", success), [])
        with self.assertRaisesRegex(TaskValidationError, "RUN_EVENT_STATE_MISMATCH"):
            self.service.record_run_event(
                task_id=task_id,
                expected_status="Running",
                event=success,
                **self.scope,
            )

        success["task_status"] = "Succeeded"
        terminal = self.service.record_run_event(
            task_id=task_id,
            expected_status="Running",
            event=success,
            **self.scope,
        )
        self.assertEqual(terminal.status, "Succeeded")

        late_progress = executable_run_event()
        late_progress.update(
            {
                "event_id": "event-progress-004",
                "sequence": 4,
                "task_id": task_id,
                "event_type": "node_progress",
                "task_status": "Running",
                "progress": {
                    "completed": 1,
                    "total": 1,
                    "unit": "iterations",
                    "message": "must remain terminal",
                },
            }
        )
        with self.assertRaisesRegex(TaskValidationError, "RUN_EVENT_STATE_MISMATCH"):
            self.service.record_run_event(
                task_id=task_id,
                expected_status="Succeeded",
                event=late_progress,
                **self.scope,
            )
        self.assertEqual(len(self.service.list_run_events(task_id, **self.scope)), 3)

    def test_p2_checkpoint_and_waiting_state_remain_unavailable(self) -> None:
        created = self.create_executable()
        task_id = created.snapshot.task_id
        self.persist_executable_plan_and_approval(task_id)
        self.seed_validated_queue_event(task_id)
        started = executable_run_event()
        started.update(
            {"event_id": "event-started-002", "sequence": 2, "task_id": task_id}
        )
        self.service.record_run_event(
            task_id=task_id,
            expected_status="Queued",
            event=started,
            **self.scope,
        )

        checkpoint = executable_run_event()
        checkpoint.update(
            {
                "event_id": "event-checkpoint-003",
                "sequence": 3,
                "task_id": task_id,
                "event_type": "checkpoint_created",
                "task_status": "Waiting",
                "checkpoint": {"relative_path": "checkpoints/state.bin"},
            }
        )
        self.assertEqual(schema_errors("run-event.schema.json", checkpoint), [])
        with self.assertRaisesRegex(TaskValidationError, "RUN_EVENT_UNSUPPORTED_IN_P1"):
            self.service.record_run_event(
                task_id=task_id,
                expected_status="Running",
                event=checkpoint,
                **self.scope,
            )
        self.assertEqual(self.service.get_task(task_id, **self.scope).status, "Running")
        self.assertEqual(
            len(self.service.list_run_events(task_id, **self.scope)), 2
        )

    def test_run_event_must_match_plan_node_and_fingerprint(self) -> None:
        created = self.create_executable()
        task_id = created.snapshot.task_id
        self.persist_executable_plan_and_approval(task_id)
        self.seed_validated_queue_event(task_id)

        unknown_node = executable_run_event()
        unknown_node.update(
            {
                "event_id": "event-unknown-node",
                "sequence": 2,
                "task_id": task_id,
                "node_id": "ghost-node",
            }
        )
        with self.assertRaisesRegex(TaskValidationError, "RUN_EVENT_NODE_UNKNOWN"):
            self.service.record_run_event(
                task_id=task_id,
                expected_status="Queued",
                event=unknown_node,
                **self.scope,
            )

        mutations = {
            "algorithm": lambda event: event["fingerprint"].__setitem__(
                "algorithm", {"id": "deepwave.other", "version": "1.0.0"}
            ),
            "seed": lambda event: event["fingerprint"].__setitem__("seed", 2027),
            "device": lambda event: event["fingerprint"]["hardware"].__setitem__(
                "device", "cpu"
            ),
            "input_hashes": lambda event: event["fingerprint"].__setitem__(
                "input_hashes", ["sha256:" + "9" * 64]
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                event = executable_run_event()
                event.update(
                    {
                        "event_id": f"event-bad-{label}",
                        "sequence": 2,
                        "task_id": task_id,
                    }
                )
                mutate(event)
                self.assertEqual(schema_errors("run-event.schema.json", event), [])
                with self.assertRaisesRegex(
                    TaskValidationError, "RUN_EVENT_FINGERPRINT_MISMATCH"
                ):
                    self.service.record_run_event(
                        task_id=task_id,
                        expected_status="Queued",
                        event=event,
                        **self.scope,
                    )
        self.assertEqual(self.service.get_task(task_id, **self.scope).status, "Queued")
        self.assertEqual(
            len(self.service.list_run_events(task_id, **self.scope)), 1
        )

    def test_success_event_cannot_carry_an_error(self) -> None:
        created = self.create_executable()
        task_id = created.snapshot.task_id
        self.persist_executable_plan_and_approval(task_id)
        self.seed_validated_queue_event(task_id)
        success = executable_run_event()
        success.update(
            {
                "event_id": "event-false-success",
                "sequence": 2,
                "task_id": task_id,
                "event_type": "node_succeeded",
                "task_status": "Succeeded",
                "error": {
                    "code": "worker_failed",
                    "message": "must not be hidden",
                    "retryable": False,
                },
            }
        )
        self.assertEqual(schema_errors("run-event.schema.json", success), [])
        with self.assertRaisesRegex(TaskValidationError, "RUN_EVENT_DETAIL_FORBIDDEN"):
            self.service.record_run_event(
                task_id=task_id,
                expected_status="Queued",
                event=success,
                **self.scope,
            )
        self.assertEqual(self.service.get_task(task_id, **self.scope).status, "Queued")

    def test_event_query_pagination_is_strictly_typed(self) -> None:
        created = self.create()
        for kwargs in ({"after_sequence": True}, {"limit": None}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(TaskValidationError):
                    self.service.list_run_events(
                        created.snapshot.task_id, **self.scope, **kwargs
                    )

        event = run_event()
        event["task_id"] = created.snapshot.task_id
        with self.assertRaisesRegex(TaskValidationError, "INVALID_EXPECTED_STATUS"):
            self.service.record_run_event(
                task_id=created.snapshot.task_id,
                expected_status=None,
                event=event,
                **self.scope,
            )

    def test_write_lock_timeout_has_a_stable_store_error(self) -> None:
        contended_store = SQLiteTaskStore(self.database_path, busy_timeout_ms=1)
        locker = sqlite3.connect(self.database_path, isolation_level=None)
        try:
            locker.execute("BEGIN IMMEDIATE")
            with self.assertRaisesRegex(TaskStoreUnavailable, "store is busy"):
                contended_store.create_task(
                    task_id="task-contended",
                    project_id=PROJECT_ID,
                    principal_id=PRINCIPAL_ID,
                    draft=task_draft(),
                    idempotency_key="contended-create-key",
                    request_hash="sha256:" + "b" * 64,
                    now=NOW,
                )
        finally:
            locker.rollback()
            locker.close()
        self.assertIsNone(contended_store.get_task("task-contended"))

        lock_fd = os.open(self.database_path, os.O_RDWR)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            with self.assertRaisesRegex(TaskStoreUnavailable, "initialization is busy"):
                SQLiteTaskStore(self.database_path, busy_timeout_ms=1)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

    def test_concurrent_same_key_creation_converges(self) -> None:
        draft = task_draft()
        barrier = threading.Barrier(4)

        def create_once(_: int) -> str:
            service = TaskService(SQLiteTaskStore(self.database_path), clock=lambda: NOW)
            barrier.wait(timeout=5)
            return service.create_task(
                project_id=PROJECT_ID,
                principal_id=PRINCIPAL_ID,
                draft=draft,
                idempotency_key="concurrent-key",
            ).snapshot.task_id

        with ThreadPoolExecutor(max_workers=4) as executor:
            task_ids = list(executor.map(create_once, range(4)))
        self.assertEqual(len(set(task_ids)), 1)
        self.assertEqual(self.raw_count("tasks"), 1)
        self.assertEqual(self.raw_count("idempotency_records"), 1)

    def test_concurrent_revision_compare_and_swap_has_one_winner(self) -> None:
        created = self.create()
        task_id = created.snapshot.task_id
        barrier = threading.Barrier(2)

        def revise(goal: str) -> str:
            draft = copy.deepcopy(created.snapshot.draft)
            draft["revision"] = 2
            draft["goal"] = goal
            service = TaskService(SQLiteTaskStore(self.database_path), clock=lambda: NOW)
            barrier.wait(timeout=5)
            try:
                return service.revise_draft(
                    task_id=task_id,
                    project_id=PROJECT_ID,
                    principal_id=PRINCIPAL_ID,
                    expected_revision=1,
                    draft=draft,
                ).draft["goal"]
            except TaskConflict:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(revise, ["winner one", "winner two"]))
        self.assertEqual(results.count("conflict"), 1)
        self.assertEqual(len(self.store.draft_history(task_id)), 2)

    def submit_service(
        self,
        dispatcher: FakeDispatcher,
        *,
        clock=lambda: NOW,
    ) -> TaskService:
        return TaskService(self.store, clock=clock, dispatcher=dispatcher)

    def submitted_runtime(
        self, *, key: str
    ) -> tuple[str, dict, FakeDispatcher, TaskService]:
        token = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
        draft = optimizer_task_draft()
        draft["draft_id"] = f"draft-{token}"
        created = self.create_executable(draft=draft, key=f"create-{key}")
        task_id = created.snapshot.task_id
        plan = optimizer_plan_graph()
        plan["plan_id"] = f"plan-{token}"
        plan["draft"]["draft_id"] = draft["draft_id"]
        plan["nodes"][0]["idempotency_key"] = f"node-{token}-submit"
        plan["plan_hash"] = compute_plan_hash(plan)
        self.service.persist_plan(task_id=task_id, plan=plan, **self.scope)
        approval = executable_approval_decision(plan)
        approval["approval_id"] = f"approval-{token}"
        self.service.persist_approval(
            task_id=task_id, approval=approval, **self.scope
        )
        dispatcher = FakeDispatcher(self.store)
        service = self.submit_service(dispatcher)
        result = service.submit_task(
            task_id=task_id,
            approval_id=approval["approval_id"],
            idempotency_key=f"submit-{key}",
            **self.scope,
        )
        self.assertEqual(result.intent.state, "pending")
        scheduled, lease = self.schedule_once(service, task_id)
        self.assertEqual(scheduled.intent.state, "dispatched")
        service.release_runtime_supervisor_lease(lease)
        return task_id, plan, dispatcher, service

    def approved_runtime(
        self, *, key: str, dispatcher: FakeDispatcher | None = None
    ) -> tuple[str, dict, FakeDispatcher, TaskService]:
        """Build one approved executable task without hiding submit crashes."""

        token = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
        draft = optimizer_task_draft()
        draft["draft_id"] = f"draft-{token}"
        created = self.create_executable(draft=draft, key=f"create-{key}")
        task_id = created.snapshot.task_id
        plan = optimizer_plan_graph()
        plan["plan_id"] = f"plan-{token}"
        plan["draft"]["draft_id"] = draft["draft_id"]
        plan["nodes"][0]["idempotency_key"] = f"node-{token}-submit"
        plan["plan_hash"] = compute_plan_hash(plan)
        self.service.persist_plan(task_id=task_id, plan=plan, **self.scope)
        approval = executable_approval_decision(plan)
        approval["approval_id"] = f"approval-{token}"
        self.service.persist_approval(
            task_id=task_id, approval=approval, **self.scope
        )
        current_dispatcher = dispatcher or FakeDispatcher(self.store)
        return (
            task_id,
            approval,
            current_dispatcher,
            self.submit_service(current_dispatcher),
        )

    def seed_dispatching_intent(
        self,
        *,
        task_id: str,
        approval: dict,
        dispatcher: FakeDispatcher,
        service: TaskService,
        key: str,
        launch: bool,
    ):
        submitted = service.submit_task(
            task_id=task_id,
            approval_id=approval["approval_id"],
            idempotency_key=key,
            **self.scope,
        )
        claimed, claimed_now = self.store.claim_dispatch(
            intent_id=submitted.intent.intent_id,
            now=NOW,
        )
        self.assertTrue(claimed_now)
        if launch:
            dispatcher.dispatch(claimed)
        return claimed

    def test_fenced_worker_projection_adopts_once_and_rejects_regression(self) -> None:
        dispatcher = FakeDispatcher(
            self.store, failure_code="ADAPTER_CONCURRENCY_LIMIT"
        )
        dispatcher.defer_dispatch = True
        task_id, approval, dispatcher, service = self.approved_runtime(
            key="worker-projection", dispatcher=dispatcher
        )
        submitted = service.submit_task(
            task_id=task_id,
            approval_id=approval["approval_id"],
            idempotency_key="submit-worker-projection",
            **self.scope,
        )
        claimed, claimed_now = self.store.claim_dispatch(
            intent_id=submitted.intent.intent_id,
            now=NOW,
        )
        self.assertTrue(claimed_now)
        self.assertEqual(claimed.state, "dispatching")

        acquisition = service.acquire_runtime_supervisor_lease(
            owner_id="worker-projection-owner",
            lease_seconds=30,
            **self.scope,
        )
        dispatcher.worker_observation = {
            "evidence": managed_worker_evidence(ticket_state="staged"),
            "handle": None,
        }
        staged = service.project_worker_attempt(
            task_id,
            supervisor_lease=acquisition.lease,
            **self.scope,
        )
        self.assertTrue(staged.projected)
        self.assertFalse(staged.adopted)
        self.assertEqual(staged.intent.state, "dispatching")
        self.assertEqual(self.raw_count("worker_launch_attempts"), 1)
        self.assertEqual(self.raw_count("worker_attempt_observations"), 1)
        self.assertEqual(self.raw_count("supervised_dispatch_adoptions"), 0)

        dispatcher.worker_observation = {
            "evidence": managed_worker_evidence(heartbeat_sequence=None),
            "handle": None,
        }
        with self.assertRaises(TaskConflict):
            service.project_worker_attempt(
                task_id,
                supervisor_lease=acquisition.lease,
                **self.scope,
            )
        self.assertEqual(self.raw_count("worker_attempt_observations"), 1)

        dispatcher.worker_observation = {
            "evidence": managed_worker_evidence(),
            "handle": dispatcher.recover_existing_receipt(claimed),
        }
        mismatched = copy.deepcopy(dispatcher.worker_observation)
        mismatched["handle"]["request_hash"] = "sha256:" + "f" * 64
        dispatcher.worker_observation = mismatched
        with self.assertRaises(TaskConflict):
            service.project_worker_attempt(
                task_id,
                supervisor_lease=acquisition.lease,
                **self.scope,
            )
        self.assertEqual(self.raw_count("worker_attempt_observations"), 1)
        self.assertEqual(self.raw_count("supervised_dispatch_adoptions"), 0)

        dispatcher.worker_observation = {
            "evidence": managed_worker_evidence(),
            "handle": dispatcher.recover_existing_receipt(claimed),
        }
        first = service.project_worker_attempt(
            task_id,
            supervisor_lease=acquisition.lease,
            **self.scope,
        )
        self.assertTrue(first.projected)
        self.assertTrue(first.adopted)
        self.assertFalse(first.replayed)
        self.assertEqual(first.intent.state, "dispatched")
        self.assertEqual(self.raw_count("worker_launch_attempts"), 1)
        self.assertEqual(self.raw_count("worker_attempt_observations"), 2)
        self.assertEqual(self.raw_count("supervised_dispatch_adoptions"), 1)

        replay = service.project_worker_attempt(
            task_id,
            supervisor_lease=acquisition.lease,
            **self.scope,
        )
        self.assertTrue(replay.projected)
        self.assertFalse(replay.adopted)
        self.assertTrue(replay.replayed)
        self.assertEqual(self.raw_count("worker_attempt_observations"), 2)

        dispatcher.worker_observation["evidence"] = managed_worker_evidence(
            attempt_id="attempt-" + "3" * 32
        )
        with self.assertRaises(TaskConflict):
            service.project_worker_attempt(
                task_id,
                supervisor_lease=acquisition.lease,
                **self.scope,
            )
        self.assertEqual(self.raw_count("worker_launch_attempts"), 1)
        self.assertEqual(self.raw_count("worker_attempt_observations"), 2)

        dispatcher.worker_observation["evidence"] = managed_worker_evidence(
            heartbeat_sequence=2
        )
        advanced = service.project_worker_attempt(
            task_id,
            supervisor_lease=acquisition.lease,
            **self.scope,
        )
        self.assertFalse(advanced.replayed)
        self.assertEqual(self.raw_count("worker_attempt_observations"), 3)

        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            with self.assertRaises(sqlite3.IntegrityError):
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
                    SELECT attempt_id, 4, ticket_state,
                           capacity_slot, capacity_generation, ticket_worker_pid,
                           ticket_updated_at, ticket_record_hash,
                           ready_worker_pid, ready_started_at, ready_record_hash,
                           3, NULL, heartbeat_updated_at, ?, document_json, ?,
                           project_id, principal_id, fencing_token,
                           observed_at, observed_at_us
                    FROM worker_attempt_observations
                    WHERE attempt_id = ? AND observation_sequence = 3
                    """,
                    (
                        "sha256:" + "8" * 64,
                        "sha256:" + "9" * 64,
                        MANAGED_ATTEMPT_ID,
                    ),
                )
            connection.rollback()
            with self.assertRaisesRegex(
                sqlite3.IntegrityError, "worker observation cannot regress"
            ):
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
                    SELECT attempt_id, 4, 'staged',
                           NULL, NULL, NULL,
                           ticket_updated_at, ticket_record_hash,
                           NULL, NULL, NULL,
                           NULL, NULL, NULL, NULL,
                           document_json, ?, project_id, principal_id,
                           fencing_token, observed_at, observed_at_us
                    FROM worker_attempt_observations
                    WHERE attempt_id = ? AND observation_sequence = 3
                    """,
                    ("sha256:" + "7" * 64, MANAGED_ATTEMPT_ID),
                )
            connection.rollback()
        finally:
            connection.close()

        dispatcher.worker_observation["evidence"] = managed_worker_evidence()
        with self.assertRaises(TaskConflict):
            service.project_worker_attempt(
                task_id,
                supervisor_lease=acquisition.lease,
                **self.scope,
            )
        self.assertEqual(self.raw_count("worker_attempt_observations"), 3)

        dispatcher.worker_observation["evidence"] = managed_worker_evidence(
            heartbeat_sequence=2,
            heartbeat_state="failed",
        )
        with self.assertRaises(TaskConflict):
            service.project_worker_attempt(
                task_id,
                supervisor_lease=acquisition.lease,
                **self.scope,
            )
        self.assertEqual(self.raw_count("worker_attempt_observations"), 3)

        dispatcher.worker_observation["evidence"] = managed_worker_evidence(
            heartbeat_sequence=3,
            heartbeat_state="succeeded",
        )
        terminal = service.project_worker_attempt(
            task_id,
            supervisor_lease=acquisition.lease,
            **self.scope,
        )
        self.assertFalse(terminal.replayed)
        self.assertEqual(self.raw_count("worker_attempt_observations"), 4)

        dispatcher.worker_observation["evidence"] = managed_worker_evidence(
            heartbeat_sequence=4,
            heartbeat_state="running",
        )
        with self.assertRaises(TaskConflict):
            service.project_worker_attempt(
                task_id,
                supervisor_lease=acquisition.lease,
                **self.scope,
            )
        self.assertEqual(self.raw_count("worker_attempt_observations"), 4)

        # Simulate storage corruption/future-writer drift after disabling the
        # migration's append-only guard.  Read-side validation must reject a
        # legal-looking relational state that differs from canonical JSON.
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "DROP TRIGGER worker_attempt_observations_are_append_only"
            )
            connection.execute(
                """
                UPDATE worker_attempt_observations
                SET heartbeat_state = 'failed'
                WHERE attempt_id = ? AND observation_sequence = 4
                """,
                (MANAGED_ATTEMPT_ID,),
            )
            connection.commit()
        finally:
            connection.close()
        dispatcher.worker_observation["evidence"] = managed_worker_evidence(
            heartbeat_sequence=3,
            heartbeat_state="succeeded",
        )
        with self.assertRaises(TaskStoreCorruption):
            service.project_worker_attempt(
                task_id,
                supervisor_lease=acquisition.lease,
                **self.scope,
            )

    def artifact_manifests(
        self, task_id: str
    ) -> tuple[list[dict], dict[str, bytes]]:
        intent = self.store.get_dispatch_intent(task_id)
        self.assertIsNotNone(intent)
        self.assertIsNotNone(intent.handle)
        expected_input = {
            key: intent.request["dataset"][key]
            for key in ("id", "version", "content_hash", "data_type")
        }
        payloads = {
            "artifact-inverted-model": b"\x93NUMPY-test-inverted-model",
            "artifact-loss-curve": b"iteration,loss\n1,1.0\n2,0.5\n",
            "artifact-true-model-figure": b"\x89PNG\r\n\x1a\ntrue-model",
            "artifact-initial-model-figure": b"\x89PNG\r\n\x1a\ninitial-model",
            "artifact-inverted-model-figure": b"\x89PNG\r\n\x1a\ninverted-model",
            "artifact-model-error-figure": b"\x89PNG\r\n\x1a\nmodel-error",
            "artifact-shot-gathers-figure": b"\x89PNG\r\n\x1a\nshot-gathers",
            "artifact-loss-curve-figure": b"\x89PNG\r\n\x1a\nloss-curve",
        }

        def manifest(
            *,
            artifact_id: str,
            port: str,
            artifact_type: str,
            media_type: str,
            relative_path: str,
            component: str,
            order: int,
            figure_id: str = "",
            width_px: int = 0,
            height_px: int = 0,
        ) -> dict:
            data = payloads[artifact_id]
            value = {
                "schema_version": "1.0.0",
                "artifact_id": artifact_id,
                "task_id": task_id,
                "node_id": intent.node_id,
                "artifact_type": artifact_type,
                "media_type": media_type,
                "location": {
                    "relative_path": f"{intent.handle['job_id']}/{relative_path}"
                },
                "content_hash": "sha256:" + hashlib.sha256(data).hexdigest(),
                "size_bytes": len(data),
                "created_at": "2026-07-15T03:05:00Z",
                "metrics": {"initial_loss": 1.0, "final_loss": 0.5},
                "display": {
                    "component": component,
                    "title": artifact_type,
                    "order": order,
                },
                "fingerprint": copy.deepcopy(intent.handle["fingerprint"]),
                "lineage": {
                    "plan_hash": intent.plan_hash,
                    "algorithm": copy.deepcopy(intent.request["algorithm"]),
                    "inputs": [expected_input],
                },
                "extensions": {
                    "org.agent_rpc.adapter": {
                        "output_port": port,
                        "worker_job_id": intent.handle["job_id"],
                    }
                },
            }
            if figure_id:
                value["extensions"]["org.agent_rpc.figure"] = {
                    "figure_id": figure_id,
                    "width_px": width_px,
                    "height_px": height_px,
                }
            return value

        manifests = [
            manifest(
                artifact_id="artifact-inverted-model",
                port="inverted_model",
                artifact_type="inverted_velocity_model_2d",
                media_type="application/x-npy",
                relative_path="models/inverted.npy",
                component="download",
                order=0,
            ),
            manifest(
                artifact_id="artifact-loss-curve",
                port="loss",
                artifact_type="loss_curve",
                media_type="text/csv",
                relative_path="loss.csv",
                component="line_chart",
                order=1,
            ),
        ]
        for order, (artifact_id, port, figure_id, path, width, height) in enumerate(
            (
                (
                    "artifact-true-model-figure",
                    "true_model_figure",
                    "true_model",
                    "figures/true_model.png",
                    1440,
                    608,
                ),
                (
                    "artifact-initial-model-figure",
                    "initial_model_figure",
                    "initial_model",
                    "figures/initial_model.png",
                    1440,
                    608,
                ),
                (
                    "artifact-inverted-model-figure",
                    "inverted_model_figure",
                    "inverted_model",
                    "figures/inverted_model.png",
                    1440,
                    608,
                ),
                (
                    "artifact-model-error-figure",
                    "model_error_figure",
                    "model_error",
                    "figures/model_error.png",
                    1440,
                    608,
                ),
                (
                    "artifact-shot-gathers-figure",
                    "shot_gathers_figure",
                    "shot_gathers",
                    "figures/shot_gathers.png",
                    2160,
                    800,
                ),
                (
                    "artifact-loss-curve-figure",
                    "loss_curve_figure",
                    "loss_curve",
                    "figures/loss_curve.png",
                    1120,
                    720,
                ),
            ),
            start=2,
        ):
            manifests.append(
                manifest(
                    artifact_id=artifact_id,
                    port=port,
                    artifact_type="figure",
                    media_type="image/png",
                    relative_path=path,
                    component="image",
                    order=order,
                    figure_id=figure_id,
                    width_px=width,
                    height_px=height,
                )
            )
        return manifests, payloads

    def test_atomic_submit_admits_pending_then_fenced_scheduler_dispatches(
        self,
    ) -> None:
        created = self.create_executable()
        task_id = created.snapshot.task_id
        _, approval = self.persist_executable_plan_and_approval(task_id)
        dispatcher = FakeDispatcher(self.store)
        service = self.submit_service(dispatcher)

        result = service.submit_task(
            task_id=task_id,
            approval_id=approval["approval_id"],
            idempotency_key="submit-operation-key",
            **self.scope,
        )

        self.assertFalse(result.replayed)
        self.assertFalse(result.dispatch_attempted)
        self.assertEqual(result.snapshot.status, "Queued")
        self.assertEqual(result.intent.state, "pending")
        self.assertIsNone(result.intent.dispatch_claimed_at)
        self.assertIsNone(result.intent.outcome_recorded_at)
        self.assertEqual(dispatcher.dispatch_calls, 0)
        self.assertEqual(self.raw_count("dispatch_intents"), 1)
        self.assertEqual(self.raw_count("dispatch_attempts"), 0)
        self.assertEqual(self.raw_count("dispatch_outcomes"), 0)
        self.assertEqual(self.raw_count("submit_idempotency_links"), 1)
        budget = self.store.get_approval_budget(
            task_id=task_id, approval_id=approval["approval_id"]
        )
        self.assertEqual((budget.tasks_used, budget.max_tasks), (1, 1))
        events = service.list_run_events(task_id, **self.scope)
        self.assertEqual(len(events), 1)
        self.assertEqual(
            (events[0]["sequence"], events[0]["event_type"], events[0]["task_status"]),
            (1, "task_queued", "Queued"),
        )
        self.assertNotIn("node_id", events[0])
        self.assertEqual(
            events[0]["extensions"]["agent_rpc.dispatch"],
            {
                "state": "pending",
                "fingerprint_basis": "adapter_preflight",
                "worker_runtime_started": False,
            },
        )

        replay = service.submit_task(
            task_id=task_id,
            approval_id=approval["approval_id"],
            idempotency_key="submit-operation-key",
            **self.scope,
        )
        self.assertTrue(replay.replayed)
        self.assertFalse(replay.dispatch_attempted)
        self.assertEqual(replay.intent, result.intent)
        self.assertEqual(dispatcher.prepare_calls, 1)
        self.assertEqual(dispatcher.dispatch_calls, 0)
        self.assertEqual(self.raw_count("run_events"), 1)

        scheduled, lease = self.schedule_once(service, task_id)
        self.assertTrue(scheduled.authorized)
        self.assertTrue(scheduled.dispatch_attempted)
        self.assertTrue(scheduled.projected)
        self.assertTrue(scheduled.adopted)
        self.assertEqual(scheduled.intent.state, "dispatched")
        self.assertEqual(dispatcher.dispatch_calls, 1)
        self.assertEqual(self.raw_count("dispatch_attempts"), 1)
        self.assertEqual(self.raw_count("supervised_dispatch_attempts"), 1)
        self.assertEqual(self.raw_count("dispatch_outcomes"), 1)
        service.release_runtime_supervisor_lease(lease)

    def test_scheduler_adapter_error_remains_recoverable_for_reconciliation(
        self,
    ) -> None:
        created = self.create_executable()
        task_id = created.snapshot.task_id
        _, approval = self.persist_executable_plan_and_approval(task_id)
        dispatcher = FakeDispatcher(
            self.store, failure_code="SUBMISSION_RECONCILIATION_REQUIRED"
        )
        service = self.submit_service(dispatcher)

        result = service.submit_task(
            task_id=task_id,
            approval_id=approval["approval_id"],
            idempotency_key="submit-reconciliation-key",
            **self.scope,
        )
        self.assertEqual(result.snapshot.status, "Queued")
        self.assertEqual(result.intent.state, "pending")
        scheduled, lease = self.schedule_once(service, task_id)
        self.assertEqual(scheduled.intent.state, "dispatching")
        self.assertEqual(
            scheduled.deferred_code, "SUBMISSION_RECONCILIATION_REQUIRED"
        )
        self.assertIsNone(scheduled.intent.failure_code)
        self.assertEqual(len(service.list_run_events(task_id, **self.scope)), 1)

        replay = service.submit_task(
            task_id=task_id,
            approval_id=approval["approval_id"],
            idempotency_key="submit-reconciliation-key",
            **self.scope,
        )
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.intent.state, "dispatching")
        self.assertEqual(dispatcher.dispatch_calls, 1)
        service.release_runtime_supervisor_lease(lease)

    def test_invalid_dispatch_receipt_requires_reconciliation(self) -> None:
        created = self.create_executable()
        task_id = created.snapshot.task_id
        _, approval = self.persist_executable_plan_and_approval(task_id)
        dispatcher = FakeDispatcher(self.store)
        valid_dispatch = dispatcher.dispatch

        def invalid_dispatch(intent):
            handle = valid_dispatch(intent)
            handle["fingerprint"]["adapter_version"] = "1.0.0"
            return handle

        dispatcher.dispatch = invalid_dispatch
        service = self.submit_service(dispatcher)
        result = service.submit_task(
            task_id=task_id,
            approval_id=approval["approval_id"],
            idempotency_key="submit-invalid-receipt",
            **self.scope,
        )
        self.assertEqual(result.snapshot.status, "Queued")
        scheduled, lease = self.schedule_once(service, task_id)
        self.assertEqual(scheduled.intent.state, "dispatching")
        self.assertEqual(scheduled.deferred_code, "DISPATCH_RECEIPT_INVALID")
        self.assertEqual(self.raw_count("dispatch_outcomes"), 0)
        service.release_runtime_supervisor_lease(lease)

    def test_deferred_dispatch_keeps_claim_recoverable_without_outcome(self) -> None:
        created = self.create_executable()
        task_id = created.snapshot.task_id
        _, approval = self.persist_executable_plan_and_approval(task_id)
        dispatcher = FakeDispatcher(
            self.store, failure_code="ADAPTER_CONCURRENCY_LIMIT"
        )
        dispatcher.defer_dispatch = True
        service = self.submit_service(dispatcher)
        result = service.submit_task(
            task_id=task_id,
            approval_id=approval["approval_id"],
            idempotency_key="submit-capacity-deferred",
            **self.scope,
        )
        self.assertFalse(result.dispatch_attempted)
        self.assertEqual(result.snapshot.status, "Queued")
        self.assertEqual(result.intent.state, "pending")
        scheduled, lease = self.schedule_once(service, task_id)
        self.assertTrue(scheduled.dispatch_attempted)
        self.assertTrue(scheduled.projected)
        self.assertEqual(scheduled.intent.state, "dispatching")
        self.assertEqual(scheduled.deferred_code, "ADAPTER_CONCURRENCY_LIMIT")
        self.assertIsNone(scheduled.intent.failure_code)
        self.assertEqual(self.raw_count("dispatch_outcomes"), 0)
        self.assertEqual(self.raw_count("worker_launch_attempts"), 1)
        self.assertEqual(self.raw_count("supervised_dispatch_attempts"), 1)

        replay = service.submit_task(
            task_id=task_id,
            approval_id=approval["approval_id"],
            idempotency_key="submit-capacity-deferred",
            **self.scope,
        )
        self.assertTrue(replay.replayed)
        self.assertFalse(replay.dispatch_attempted)
        self.assertEqual(replay.intent.state, "dispatching")
        self.assertEqual(dispatcher.dispatch_calls, 1)

        same_term_retry = service.schedule_runtime_dispatch(
            task_id,
            supervisor_lease=lease,
            **self.scope,
        )
        self.assertTrue(same_term_retry.authorization_replayed)
        self.assertEqual(same_term_retry.intent.state, "dispatching")
        self.assertEqual(dispatcher.dispatch_calls, 2)
        self.assertEqual(self.raw_count("worker_launch_attempts"), 1)
        self.assertEqual(self.raw_count("supervised_dispatch_attempts"), 1)
        service.release_runtime_supervisor_lease(lease)

        successor = service.acquire_runtime_supervisor_lease(
            owner_id="capacity-successor",
            lease_seconds=30,
            **self.scope,
        )
        dispatcher.failure_code = None
        resumed = service.schedule_runtime_dispatch(
            task_id,
            supervisor_lease=successor.lease,
            **self.scope,
        )
        self.assertTrue(resumed.authorized)
        self.assertFalse(resumed.authorization_replayed)
        self.assertTrue(resumed.dispatch_attempted)
        self.assertTrue(resumed.projected)
        self.assertTrue(resumed.adopted)
        self.assertEqual(resumed.intent.state, "dispatched")
        self.assertEqual(dispatcher.dispatch_calls, 3)
        self.assertEqual(self.raw_count("worker_launch_attempts"), 1)
        self.assertEqual(self.raw_count("supervised_dispatch_attempts"), 2)
        self.assertEqual(self.raw_count("dispatch_outcomes"), 1)
        service.release_runtime_supervisor_lease(successor.lease)

    def test_submit_exact_replay_precedes_expiry_budget_and_preflight(self) -> None:
        created = self.create_executable()
        task_id = created.snapshot.task_id
        _, approval = self.persist_executable_plan_and_approval(task_id)
        now = [NOW]
        dispatcher = FakeDispatcher(self.store)
        service = self.submit_service(dispatcher, clock=lambda: now[0])
        first = service.submit_task(
            task_id=task_id,
            approval_id=approval["approval_id"],
            idempotency_key="submit-replay-before-gate",
            **self.scope,
        )
        now[0] = "2026-07-16T03:00:00Z"
        dispatcher.failure_code = "PREPARE_MUST_NOT_RUN"
        replay = service.submit_task(
            task_id=task_id,
            approval_id=approval["approval_id"],
            idempotency_key="submit-replay-before-gate",
            **self.scope,
        )
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.intent, first.intent)
        self.assertEqual(dispatcher.prepare_calls, 1)
        self.assertEqual(dispatcher.dispatch_calls, 0)

    def test_submit_gate_time_is_sampled_after_waiting_for_write_lock(self) -> None:
        created = self.create()
        task_id = created.snapshot.task_id
        _, approval = self.persist_plan_and_approval(task_id)
        dispatcher = FakeDispatcher(self.store)
        now = [NOW]
        entered_store = threading.Event()
        original_submit = self.store.submit_task

        def coordinated_submit(**kwargs):
            entered_store.set()
            return original_submit(**kwargs)

        self.store.submit_task = coordinated_submit
        blocker = sqlite3.connect(self.database_path, isolation_level=None)
        blocker.execute("PRAGMA foreign_keys = ON")
        blocker.execute("BEGIN IMMEDIATE")
        try:
            service = self.submit_service(dispatcher, clock=lambda: now[0])
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    service.submit_task,
                    task_id=task_id,
                    approval_id=approval["approval_id"],
                    idempotency_key="submit-lock-expiry",
                    **self.scope,
                )
                self.assertTrue(entered_store.wait(timeout=5))
                now[0] = approval["expires_at"]
                blocker.commit()
                with self.assertRaises(TaskValidationError) as raised:
                    future.result(timeout=5)
                self.assertEqual(raised.exception.code, "EXECUTION_GATE_REJECTED")
        finally:
            if blocker.in_transaction:
                blocker.rollback()
            blocker.close()
            self.store.submit_task = original_submit
        self.assertEqual(self.store.get_task(task_id).status, "AwaitingApproval")
        self.assertEqual(self.raw_count("dispatch_intents"), 0)
        self.assertEqual(dispatcher.dispatch_calls, 0)

    def test_submit_gate_or_capability_failure_has_no_atomic_side_effects(self) -> None:
        created = self.create()
        task_id = created.snapshot.task_id
        plan, approval = self.persist_plan_and_approval(task_id)
        dispatcher = FakeDispatcher(self.store)
        expired = self.submit_service(
            dispatcher, clock=lambda: approval["expires_at"]
        )
        with self.assertRaises(TaskValidationError) as raised:
            expired.submit_task(
                task_id=task_id,
                approval_id=approval["approval_id"],
                idempotency_key="submit-expired-key",
                **self.scope,
            )
        self.assertEqual(raised.exception.code, "EXECUTION_GATE_REJECTED")
        self.assertEqual(self.store.get_task(task_id).status, "AwaitingApproval")
        self.assertEqual(self.raw_count("dispatch_intents"), 0)
        self.assertEqual(self.raw_count("run_events"), 0)
        self.assertEqual(
            self.store.get_approval_budget(
                task_id=task_id, approval_id=approval["approval_id"]
            ).tasks_used,
            0,
        )
        self.assertEqual(dispatcher.dispatch_calls, 0)

        multi_plan = plan_graph()
        multi_plan["plan_id"] = "plan-multi-node"
        append_second_plan_node(multi_plan)
        multi_plan["plan_hash"] = compute_plan_hash(multi_plan)
        self.service.persist_plan(task_id=task_id, plan=multi_plan, **self.scope)
        multi_approval = approval_decision(multi_plan)
        multi_approval["approval_id"] = "approval-multi-node"
        self.service.persist_approval(
            task_id=task_id, approval=multi_approval, **self.scope
        )
        with self.assertRaises(TaskValidationError) as raised:
            self.submit_service(dispatcher).submit_task(
                task_id=task_id,
                approval_id=multi_approval["approval_id"],
                idempotency_key="submit-multi-node-key",
                **self.scope,
            )
        self.assertEqual(
            raised.exception.code, "PLAN_CAPABILITY_UNSUPPORTED_IN_P1"
        )
        self.assertEqual(self.store.get_task(task_id).status, "AwaitingApproval")

    def test_submit_idempotency_conflict_and_new_key_cannot_duplicate_task(self) -> None:
        created = self.create_executable()
        task_id = created.snapshot.task_id
        _, approval = self.persist_executable_plan_and_approval(task_id)
        dispatcher = FakeDispatcher(self.store)
        service = self.submit_service(dispatcher)
        service.submit_task(
            task_id=task_id,
            approval_id=approval["approval_id"],
            idempotency_key="submit-bound-key",
            **self.scope,
        )
        with self.assertRaises(TaskIdempotencyConflict):
            service.submit_task(
                task_id=task_id,
                approval_id="approval-different",
                idempotency_key="submit-bound-key",
                **self.scope,
            )
        with self.assertRaises(TaskConflict):
            service.submit_task(
                task_id=task_id,
                approval_id=approval["approval_id"],
                idempotency_key="submit-new-key",
                **self.scope,
            )
        self.assertEqual(self.raw_count("dispatch_intents"), 1)
        self.assertEqual(self.raw_count("run_events"), 1)

    def test_submit_rejects_same_version_registry_manifest_drift(self) -> None:
        database_path = Path(self.temporary.name) / "manifest-drift.sqlite3"
        store = SQLiteTaskStore(database_path)
        registry = RegistryService(store, clock=lambda: NOW)
        registry.register_dataset(dataset=dataset_ref())
        drifted_manifest = algorithm_manifest()
        drifted_manifest["extensions"] = {
            "org.example.drift": {"reason": "same version is not the packaged binding"}
        }
        registry.register_algorithm(manifest=drifted_manifest)
        service = TaskService(
            store,
            task_id_factory=lambda: "task-manifest-drift",
            clock=lambda: NOW,
        )
        task_id = service.create_task(
            project_id=PROJECT_ID,
            principal_id=PRINCIPAL_ID,
            draft=task_draft(),
            idempotency_key="create-manifest-drift",
        ).snapshot.task_id
        plan = plan_graph()
        service.persist_plan(task_id=task_id, plan=plan, **self.scope)
        approval = approval_decision(plan)
        service.persist_approval(task_id=task_id, approval=approval, **self.scope)
        dispatcher = FakeDispatcher(store)
        with self.assertRaises(TaskValidationError) as raised:
            TaskService(store, clock=lambda: NOW, dispatcher=dispatcher).submit_task(
                task_id=task_id,
                approval_id=approval["approval_id"],
                idempotency_key="submit-manifest-drift",
                **self.scope,
            )
        self.assertEqual(
            raised.exception.code, "PLAN_CAPABILITY_UNSUPPORTED_IN_P1"
        )
        self.assertIn("adapter_binding_mismatch", raised.exception.errors)
        self.assertEqual(store.get_task(task_id).status, "AwaitingApproval")
        self.assertEqual(dispatcher.dispatch_calls, 0)

    def test_concurrent_same_submit_key_converges_to_one_pending_intent(self) -> None:
        created = self.create_executable()
        task_id = created.snapshot.task_id
        _, approval = self.persist_executable_plan_and_approval(task_id)
        dispatcher = FakeDispatcher(self.store)
        barrier = threading.Barrier(8)

        def submit(_: int):
            service = TaskService(
                SQLiteTaskStore(self.database_path),
                clock=lambda: NOW,
                dispatcher=dispatcher,
            )
            barrier.wait(timeout=10)
            return service.submit_task(
                task_id=task_id,
                approval_id=approval["approval_id"],
                idempotency_key="concurrent-submit-key",
                **self.scope,
            )

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(submit, range(8)))
        self.assertEqual(sum(not result.replayed for result in results), 1)
        self.assertEqual(dispatcher.dispatch_calls, 0)
        self.assertEqual(self.raw_count("dispatch_intents"), 1)
        self.assertEqual(self.raw_count("dispatch_attempts"), 0)
        self.assertEqual(self.raw_count("dispatch_outcomes"), 0)
        self.assertEqual(self.raw_count("run_events"), 1)
        self.assertEqual(
            self.store.get_approval_budget(
                task_id=task_id, approval_id=approval["approval_id"]
            ).tasks_used,
            1,
        )

    def test_concurrent_different_submit_keys_admit_only_one_task(self) -> None:
        created = self.create_executable()
        task_id = created.snapshot.task_id
        _, approval = self.persist_executable_plan_and_approval(task_id)
        dispatcher = FakeDispatcher(self.store)
        barrier = threading.Barrier(8)

        def submit(index: int) -> str:
            service = TaskService(
                SQLiteTaskStore(self.database_path),
                clock=lambda: NOW,
                dispatcher=dispatcher,
            )
            barrier.wait(timeout=10)
            try:
                result = service.submit_task(
                    task_id=task_id,
                    approval_id=approval["approval_id"],
                    idempotency_key=f"different-submit-key-{index}",
                    **self.scope,
                )
                return "admitted" if not result.replayed else "replayed"
            except TaskConflict:
                return "conflict"

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(submit, range(8)))
        self.assertEqual(results.count("admitted"), 1)
        self.assertEqual(results.count("conflict"), 7)
        self.assertEqual(dispatcher.dispatch_calls, 0)
        self.assertEqual(self.raw_count("dispatch_intents"), 1)
        self.assertEqual(self.raw_count("run_events"), 1)
        self.assertEqual(
            self.store.get_approval_budget(
                task_id=task_id, approval_id=approval["approval_id"]
            ).tasks_used,
            1,
        )

    def test_submit_status_failure_rolls_back_every_admission_write(self) -> None:
        created = self.create_executable()
        task_id = created.snapshot.task_id
        _, approval = self.persist_executable_plan_and_approval(task_id)
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                """
                CREATE TRIGGER fail_queued_status_for_test
                BEFORE UPDATE OF status ON tasks
                WHEN NEW.status = 'Queued'
                BEGIN
                    SELECT RAISE(ABORT, 'injected queued failure');
                END
                """
            )
            connection.commit()
        finally:
            connection.close()
        dispatcher = FakeDispatcher(self.store)
        with self.assertRaises(TaskConflict):
            self.submit_service(dispatcher).submit_task(
                task_id=task_id,
                approval_id=approval["approval_id"],
                idempotency_key="submit-rollback-key",
                **self.scope,
            )
        self.assertEqual(self.store.get_task(task_id).status, "AwaitingApproval")
        for table in (
            "dispatch_intents",
            "dispatch_attempts",
            "dispatch_outcomes",
            "submit_idempotency_links",
            "run_events",
        ):
            self.assertEqual(self.raw_count(table), 0)
        self.assertEqual(
            self.store.get_approval_budget(
                task_id=task_id, approval_id=approval["approval_id"]
            ).tasks_used,
            0,
        )
        self.assertEqual(dispatcher.dispatch_calls, 0)

    def test_admission_commit_stays_pending_until_fenced_scheduler(self) -> None:
        created = self.create_executable()
        task_id = created.snapshot.task_id
        _, approval = self.persist_executable_plan_and_approval(task_id)
        dispatcher = FakeDispatcher(self.store)
        service = self.submit_service(dispatcher)
        admitted = service.submit_task(
            task_id=task_id,
            approval_id=approval["approval_id"],
            idempotency_key="submit-before-dispatch-crash",
            **self.scope,
        )
        self.assertFalse(admitted.dispatch_attempted)
        self.assertEqual(self.store.get_task(task_id).status, "Queued")
        self.assertEqual(self.store.get_dispatch_intent(task_id).state, "pending")
        self.assertEqual(dispatcher.dispatch_calls, 0)

        replay = service.submit_task(
            task_id=task_id,
            approval_id=approval["approval_id"],
            idempotency_key="submit-before-dispatch-crash",
            **self.scope,
        )
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.intent.state, "pending")
        self.assertEqual(dispatcher.dispatch_calls, 0)

        scheduled, lease = self.schedule_once(service, task_id)
        self.assertEqual(scheduled.intent.state, "dispatched")
        self.assertEqual(dispatcher.dispatch_calls, 1)
        service.release_runtime_supervisor_lease(lease)

    def test_worker_launch_without_projection_is_adopted_without_relaunch(
        self,
    ) -> None:
        created = self.create_executable()
        task_id = created.snapshot.task_id
        _, approval = self.persist_executable_plan_and_approval(task_id)
        dispatcher = FakeDispatcher(self.store)
        service = self.submit_service(dispatcher)
        submitted = service.submit_task(
            task_id=task_id,
            approval_id=approval["approval_id"],
            idempotency_key="submit-after-worker-crash",
            **self.scope,
        )
        acquisition = service.acquire_runtime_supervisor_lease(
            owner_id="projection-loss-owner",
            lease_seconds=30,
            **self.scope,
        )
        original_record = self.store.record_supervised_worker_observation

        def lose_receipt(**_kwargs):
            raise TaskStoreConflict("simulated receipt persistence loss")

        self.store.record_supervised_worker_observation = lose_receipt
        try:
            with self.assertRaises(TaskConflict):
                service.schedule_runtime_dispatch(
                    task_id,
                    supervisor_lease=acquisition.lease,
                    **self.scope,
                )
        finally:
            self.store.record_supervised_worker_observation = original_record
        self.assertEqual(dispatcher.dispatch_calls, 1)
        self.assertEqual(
            self.store.get_dispatch_intent(task_id).state, "dispatching"
        )

        recovered = service.schedule_runtime_dispatch(
            task_id,
            supervisor_lease=acquisition.lease,
            **self.scope,
        )
        self.assertEqual(recovered.intent.state, "dispatched")
        self.assertTrue(recovered.adopted)
        self.assertEqual(dispatcher.dispatch_calls, 1)
        replay = service.submit_task(
            task_id=task_id,
            approval_id=approval["approval_id"],
            idempotency_key="submit-after-worker-crash",
            **self.scope,
        )
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.intent.state, "dispatched")
        service.release_runtime_supervisor_lease(acquisition.lease)

    def test_current_legacy_private_receipt_is_fenced_adopted_without_launch(
        self,
    ) -> None:
        created = self.create_executable(key="create-private-receipt")
        task_id = created.snapshot.task_id
        _, approval = self.persist_executable_plan_and_approval(task_id)
        dispatcher = FakeDispatcher(self.store)
        dispatcher.worker_observation_failure_code = (
            "WORKER_EVIDENCE_UNAVAILABLE"
        )
        service = self.submit_service(dispatcher)
        admitted = service.submit_task(
            task_id=task_id,
            approval_id=approval["approval_id"],
            idempotency_key="submit-private-receipt",
            **self.scope,
        )
        claimed, claimed_now = self.store.claim_dispatch(
            intent_id=admitted.intent.intent_id,
            now=NOW,
        )
        self.assertTrue(claimed_now)
        self.assertEqual(claimed.state, "dispatching")

        acquisition = service.acquire_runtime_supervisor_lease(
            owner_id="private-receipt-owner",
            lease_seconds=30,
            **self.scope,
        )
        scheduled = service.schedule_runtime_dispatch(
            task_id,
            supervisor_lease=acquisition.lease,
            **self.scope,
        )
        self.assertEqual(scheduled.intent.state, "dispatched")
        self.assertFalse(scheduled.authorized)
        self.assertFalse(scheduled.dispatch_attempted)
        self.assertFalse(scheduled.projected)
        self.assertTrue(scheduled.adopted)
        self.assertEqual(dispatcher.dispatch_calls, 0)
        self.assertEqual(dispatcher.private_receipt_recovery_calls, 1)
        self.assertEqual(self.raw_count("supervised_dispatch_attempts"), 0)
        self.assertEqual(self.raw_count("worker_launch_attempts"), 0)
        self.assertEqual(self.raw_count("dispatch_outcomes"), 1)
        self.assertEqual(
            self.raw_count("supervised_private_receipt_adoptions"), 1
        )
        service.release_runtime_supervisor_lease(acquisition.lease)

    def test_startup_recovery_defers_pending_without_claim_or_dispatch(self) -> None:
        task_id, approval, dispatcher, service = self.approved_runtime(
            key="startup-pending"
        )
        original_claim = self.store.claim_dispatch
        service.submit_task(
            task_id=task_id,
            approval_id=approval["approval_id"],
            idempotency_key="submit-startup-pending",
            **self.scope,
        )

        self.assertEqual(self.store.get_dispatch_intent(task_id).state, "pending")
        self.store.claim_dispatch = lambda **_kwargs: self.fail(
            "startup recovery must not claim pending work"
        )
        try:
            recovered = service.recover_runtime_on_startup(
                PROJECT_ID, PRINCIPAL_ID
            )
        finally:
            self.store.claim_dispatch = original_claim
        self.assertEqual(recovered.scanned_task_ids, (task_id,))
        self.assertEqual(recovered.pending_deferred_task_ids, (task_id,))
        self.assertEqual(recovered.receipt_recovery_attempted_task_ids, ())
        self.assertEqual(recovered.receipt_recovered_task_ids, ())
        self.assertEqual(recovered.status_refreshed_task_ids, ())
        self.assertEqual(recovered.reconciliation_required_task_ids, ())
        self.assertEqual(self.store.get_dispatch_intent(task_id).state, "pending")
        self.assertEqual(dispatcher.dispatch_calls, 0)
        self.assertEqual(dispatcher.receipt_recovery_calls, 0)
        self.assertEqual(dispatcher.status_calls, 0)

        replay = service.submit_task(
            task_id=task_id,
            approval_id=approval["approval_id"],
            idempotency_key="submit-startup-pending",
            **self.scope,
        )
        self.assertTrue(replay.replayed)
        self.assertFalse(replay.dispatch_attempted)
        self.assertEqual(replay.intent.state, "pending")
        self.assertEqual(dispatcher.dispatch_calls, 0)

    def test_startup_recovery_defers_more_pending_tasks_than_process_capacity(
        self,
    ) -> None:
        dispatcher = FakeDispatcher(self.store)
        service = self.submit_service(dispatcher)
        task_ids = []

        for index in range(3):
            task_id, approval, _, _ = self.approved_runtime(
                key=f"startup-pending-capacity-{index}", dispatcher=dispatcher
            )
            task_ids.append(task_id)
            service.submit_task(
                task_id=task_id,
                approval_id=approval["approval_id"],
                idempotency_key=f"submit-startup-pending-capacity-{index}",
                **self.scope,
            )

        recovered = service.recover_runtime_on_startup(PROJECT_ID, PRINCIPAL_ID)
        self.assertEqual(set(recovered.pending_deferred_task_ids), set(task_ids))
        self.assertEqual(recovered.receipt_recovery_attempted_task_ids, ())
        self.assertEqual(dispatcher.dispatch_calls, 0)
        self.assertEqual(dispatcher.receipt_recovery_calls, 0)
        self.assertEqual(self.raw_count("dispatch_outcomes"), 0)
        self.assertEqual(
            {self.store.get_dispatch_intent(task_id).state for task_id in task_ids},
            {"pending"},
        )

    def test_startup_inventory_defers_lost_receipt_to_fenced_scheduler(self) -> None:
        task_id, approval, dispatcher, service = self.approved_runtime(
            key="startup-lost-receipt"
        )
        self.seed_dispatching_intent(
            task_id=task_id,
            approval=approval,
            dispatcher=dispatcher,
            service=service,
            key="submit-startup-lost-receipt",
            launch=True,
        )

        self.assertEqual(dispatcher.dispatch_calls, 1)
        self.assertEqual(
            self.store.get_dispatch_intent(task_id).state, "dispatching"
        )
        recovered = service.recover_runtime_on_startup(
            PROJECT_ID, PRINCIPAL_ID
        )
        self.assertEqual(recovered.receipt_recovery_attempted_task_ids, ())
        self.assertEqual(recovered.receipt_recovered_task_ids, ())
        self.assertEqual(
            recovered.dispatching_deferred,
            ((task_id, "SUPERVISED_DISPATCH_REQUIRED"),),
        )
        self.assertEqual(self.store.get_dispatch_intent(task_id).state, "dispatching")
        self.assertEqual(dispatcher.dispatch_calls, 1)
        self.assertEqual(dispatcher.receipt_recovery_calls, 0)
        self.assertEqual(self.raw_count("dispatch_outcomes"), 0)

        scheduled, lease = self.schedule_once(service, task_id)
        self.assertEqual(scheduled.intent.state, "dispatched")
        self.assertTrue(scheduled.adopted)
        self.assertEqual(dispatcher.dispatch_calls, 1)
        service.release_runtime_supervisor_lease(lease)

    def test_startup_recovery_defers_dispatching_without_existing_receipt(
        self,
    ) -> None:
        task_id, approval, dispatcher, service = self.approved_runtime(
            key="startup-no-existing-receipt"
        )
        self.seed_dispatching_intent(
            task_id=task_id,
            approval=approval,
            dispatcher=dispatcher,
            service=service,
            key="submit-startup-no-existing-receipt",
            launch=False,
        )
        recovered = service.recover_runtime_on_startup(PROJECT_ID, PRINCIPAL_ID)
        self.assertEqual(
            recovered.dispatching_deferred,
            ((task_id, "SUPERVISED_DISPATCH_REQUIRED"),),
        )
        self.assertEqual(recovered.receipt_recovered_task_ids, ())
        self.assertEqual(self.store.get_dispatch_intent(task_id).state, "dispatching")
        self.assertEqual(dispatcher.dispatch_calls, 0)
        self.assertEqual(dispatcher.receipt_recovery_calls, 0)
        self.assertEqual(self.raw_count("dispatch_outcomes"), 0)

        scheduled, lease = self.schedule_once(service, task_id)
        self.assertEqual(scheduled.intent.state, "dispatched")
        self.assertEqual(dispatcher.dispatch_calls, 1)
        service.release_runtime_supervisor_lease(lease)

    def test_startup_inventory_never_reads_or_writes_adapter_receipts(self) -> None:
        task_id, approval, dispatcher, service = self.approved_runtime(
            key="startup-malformed-handle"
        )
        self.seed_dispatching_intent(
            task_id=task_id,
            approval=approval,
            dispatcher=dispatcher,
            service=service,
            key="submit-startup-malformed-handle",
            launch=True,
        )
        dispatcher.recover_existing_receipt = lambda _intent: self.fail(
            "pre-lease startup inventory must not read Adapter receipts"
        )
        recovered = service.recover_runtime_on_startup(
            PROJECT_ID, PRINCIPAL_ID
        )

        self.assertEqual(recovered.status_refreshed_task_ids, ())
        self.assertEqual(
            recovered.dispatching_deferred,
            ((task_id, "SUPERVISED_DISPATCH_REQUIRED"),),
        )
        self.assertEqual(recovered.reconciliation_required_task_ids, ())
        intent = self.store.get_dispatch_intent(task_id)
        self.assertEqual(intent.state, "dispatching")
        self.assertIsNone(intent.failure_code)
        self.assertEqual(self.raw_count("dispatch_outcomes"), 0)

    def test_startup_recovery_repeated_and_concurrent_calls_converge(self) -> None:
        task_id, approval, dispatcher, service = self.approved_runtime(
            key="startup-concurrent"
        )
        self.seed_dispatching_intent(
            task_id=task_id,
            approval=approval,
            dispatcher=dispatcher,
            service=service,
            key="submit-startup-concurrent",
            launch=True,
        )

        def recover(_: int):
            recovery = TaskService(
                SQLiteTaskStore(self.database_path),
                clock=lambda: NOW,
                dispatcher=dispatcher,
            )
            return recovery.recover_runtime_on_startup(
                PROJECT_ID, PRINCIPAL_ID
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(recover, range(2)))
        self.assertEqual(
            [result.dispatching_deferred for result in results],
            [
                ((task_id, "SUPERVISED_DISPATCH_REQUIRED"),),
                ((task_id, "SUPERVISED_DISPATCH_REQUIRED"),),
            ],
        )
        self.assertEqual(self.store.get_dispatch_intent(task_id).state, "dispatching")
        self.assertEqual(self.raw_count("dispatch_outcomes"), 0)
        self.assertEqual(dispatcher.dispatch_calls, 1)
        self.assertEqual(dispatcher.receipt_recovery_calls, 0)

        repeated = service.recover_runtime_on_startup(PROJECT_ID, PRINCIPAL_ID)
        self.assertEqual(repeated.receipt_recovery_attempted_task_ids, ())
        self.assertEqual(repeated.status_refreshed_task_ids, ())
        self.assertEqual(dispatcher.receipt_recovery_calls, 0)

    def test_startup_inventory_preserves_reconciliation_outcome(
        self,
    ) -> None:
        task_id, approval, dispatcher, service = self.approved_runtime(
            key="startup-first-outcome"
        )
        claimed = self.seed_dispatching_intent(
            task_id=task_id,
            approval=approval,
            dispatcher=dispatcher,
            service=service,
            key="submit-startup-first-outcome",
            launch=False,
        )
        self.store.record_dispatch_reconciliation(
            intent_id=claimed.intent_id,
            failure_code="CONCURRENT_RECOVERY_WON",
            now=NOW,
        )
        recovered = service.recover_runtime_on_startup(PROJECT_ID, PRINCIPAL_ID)
        self.assertEqual(recovered.reconciliation_required_task_ids, (task_id,))
        self.assertEqual(
            self.store.get_dispatch_intent(task_id).state,
            "reconciliation_required",
        )
        self.assertEqual(self.raw_count("dispatch_outcomes"), 1)

    def test_startup_inventory_ignores_divergent_receipt_callback(self) -> None:
        task_id, approval, dispatcher, service = self.approved_runtime(
            key="startup-divergent-receipts"
        )
        self.seed_dispatching_intent(
            task_id=task_id,
            approval=approval,
            dispatcher=dispatcher,
            service=service,
            key="submit-startup-divergent-receipts",
            launch=True,
        )
        dispatcher.recover_existing_receipt = lambda _intent: self.fail(
            "startup inventory must not compare Adapter receipts"
        )
        recovered = service.recover_runtime_on_startup(PROJECT_ID, PRINCIPAL_ID)
        self.assertEqual(
            recovered.dispatching_deferred,
            ((task_id, "SUPERVISED_DISPATCH_REQUIRED"),),
        )
        intent = self.store.get_dispatch_intent(task_id)
        self.assertEqual(intent.state, "dispatching")
        self.assertIsNone(intent.handle)
        self.assertEqual(dispatcher.status_calls, 0)
        self.assertEqual(self.raw_count("dispatch_outcomes"), 0)

    def test_startup_recovery_never_retries_reconciliation_required(self) -> None:
        dispatcher = FakeDispatcher(
            self.store, failure_code="SUBMISSION_RECONCILIATION_REQUIRED"
        )
        task_id, approval, dispatcher, service = self.approved_runtime(
            key="startup-reconciliation-required", dispatcher=dispatcher
        )
        submitted = service.submit_task(
            task_id=task_id,
            approval_id=approval["approval_id"],
            idempotency_key="submit-startup-reconciliation-required",
            **self.scope,
        )
        claimed, claimed_now = self.store.claim_dispatch(
            intent_id=submitted.intent.intent_id,
            now=NOW,
        )
        self.assertTrue(claimed_now)
        reconciled = self.store.record_dispatch_reconciliation(
            intent_id=claimed.intent_id,
            failure_code="SUBMISSION_RECONCILIATION_REQUIRED",
            now=NOW,
        )
        self.assertEqual(reconciled.state, "reconciliation_required")
        calls = dispatcher.dispatch_calls

        recovered = service.recover_runtime_on_startup(
            PROJECT_ID, PRINCIPAL_ID
        )
        self.assertEqual(recovered.receipt_recovery_attempted_task_ids, ())
        self.assertEqual(recovered.receipt_recovered_task_ids, ())
        self.assertEqual(recovered.status_refreshed_task_ids, ())
        self.assertEqual(recovered.reconciliation_required_task_ids, (task_id,))
        self.assertEqual(dispatcher.dispatch_calls, calls)
        self.assertEqual(dispatcher.receipt_recovery_calls, 0)
        self.assertEqual(dispatcher.status_calls, 0)

    def test_startup_recovery_is_scope_bound_and_fails_before_limit_side_effects(
        self,
    ) -> None:
        first = task_draft()
        first["draft_id"] = "draft-recovery-limit-001"
        first_task = self.create(draft=first, key="recovery-limit-001")
        second = task_draft()
        second["draft_id"] = "draft-recovery-limit-002"
        second_task = self.create(draft=second, key="recovery-limit-002")

        foreign_project = "project-recovery-foreign"
        foreign_dataset = self.register_project_dataset(foreign_project)
        foreign_draft = task_draft()
        foreign_draft["draft_id"] = "draft-recovery-foreign"
        foreign_draft["datasets"] = [foreign_dataset]
        foreign_service = TaskService(
            self.store,
            task_id_factory=lambda: "task-recovery-foreign",
            clock=lambda: NOW,
        )
        foreign_service.create_task(
            project_id=foreign_project,
            principal_id=PRINCIPAL_ID,
            draft=foreign_draft,
            idempotency_key="create-recovery-foreign",
        )
        dispatcher = FakeDispatcher(self.store)
        service = self.submit_service(dispatcher)

        with self.assertRaises(TaskValidationError) as raised:
            service.recover_runtime_on_startup(
                PROJECT_ID, PRINCIPAL_ID, max_tasks=1
            )
        self.assertEqual(
            raised.exception.code, "STARTUP_RECOVERY_LIMIT_EXCEEDED"
        )
        self.assertEqual(dispatcher.dispatch_calls, 0)
        self.assertEqual(dispatcher.status_calls, 0)

        recovered = service.recover_runtime_on_startup(
            PROJECT_ID, PRINCIPAL_ID, max_tasks=2
        )
        self.assertEqual(
            set(recovered.scanned_task_ids),
            {first_task.snapshot.task_id, second_task.snapshot.task_id},
        )
        self.assertNotIn("task-recovery-foreign", recovered.scanned_task_ids)
        for invalid in (True, 0, 10001):
            with self.subTest(invalid=invalid):
                with self.assertRaises(TaskValidationError) as invalid_limit:
                    service.recover_runtime_on_startup(
                        PROJECT_ID, PRINCIPAL_ID, max_tasks=invalid
                    )
                self.assertEqual(
                    invalid_limit.exception.code,
                    "INVALID_STARTUP_RECOVERY_LIMIT",
                )

    def test_startup_recovery_scans_every_active_page(self) -> None:
        task_ids: set[str] = set()
        for index in range(51):
            draft = task_draft()
            draft["draft_id"] = f"draft-recovery-page-{index:03d}"
            created = self.create(
                draft=draft,
                key=f"create-recovery-page-{index:03d}",
            )
            task_ids.add(created.snapshot.task_id)

        recovered = self.service.recover_runtime_on_startup(
            PROJECT_ID, PRINCIPAL_ID, max_tasks=51
        )
        self.assertEqual(len(recovered.scanned_task_ids), 51)
        self.assertEqual(set(recovered.scanned_task_ids), task_ids)
        self.assertEqual(recovered.receipt_recovery_attempted_task_ids, ())
        self.assertEqual(recovered.pending_deferred_task_ids, ())
        self.assertEqual(recovered.status_refreshed_task_ids, ())
        self.assertEqual(recovered.status_refresh_failures, ())

    def test_startup_inventory_leaves_terminal_catchup_to_supervisor(self) -> None:
        task_id, _, dispatcher, service = self.submitted_runtime(
            key="startup-terminal-catch-up"
        )
        dispatcher.adapter_status = {
            "status": "Succeeded",
            "stage": "complete",
            "completed": 2,
            "total": 2,
            "message": "complete",
            "updated_at": "2026-07-15T03:05:00Z",
            "terminal": True,
        }
        recovered = service.recover_runtime_on_startup(
            PROJECT_ID, PRINCIPAL_ID
        )
        self.assertEqual(recovered.receipt_recovery_attempted_task_ids, ())
        self.assertEqual(recovered.status_refreshed_task_ids, ())
        self.assertEqual(service.get_task(task_id, **self.scope).status, "Queued")
        self.assertEqual(dispatcher.status_calls, 0)

        acquisition = service.acquire_runtime_supervisor_lease(
            owner_id="terminal-catchup-owner",
            lease_seconds=30,
            **self.scope,
        )
        service.refresh_runtime_status(
            task_id,
            supervisor_lease=acquisition.lease,
            **self.scope,
        )
        self.assertEqual(service.get_task(task_id, **self.scope).status, "Succeeded")
        self.assertEqual(dispatcher.status_calls, 1)
        self.assertEqual(
            [
                event["event_type"]
                for event in service.list_run_events(task_id, **self.scope)
            ],
            ["task_queued", "node_started", "node_succeeded"],
        )

    def test_startup_inventory_does_not_call_status_for_pending_scope(self) -> None:
        dispatcher = FakeDispatcher(self.store)
        healthy_id, healthy_approval, _, service = self.approved_runtime(
            key="startup-status-healthy", dispatcher=dispatcher
        )
        service.submit_task(
            task_id=healthy_id,
            approval_id=healthy_approval["approval_id"],
            idempotency_key="submit-startup-status-healthy",
            **self.scope,
        )
        broken_id, broken_approval, _, service = self.approved_runtime(
            key="startup-status-broken", dispatcher=dispatcher
        )
        service.submit_task(
            task_id=broken_id,
            approval_id=broken_approval["approval_id"],
            idempotency_key="submit-startup-status-broken",
            **self.scope,
        )
        dispatcher.status = lambda _intent: self.fail(
            "pre-lease startup inventory must not call Adapter status"
        )
        recovered = service.recover_runtime_on_startup(
            PROJECT_ID, PRINCIPAL_ID
        )
        self.assertEqual(recovered.status_refresh_failures, ())
        self.assertEqual(recovered.status_refreshed_task_ids, ())
        self.assertEqual(
            set(recovered.pending_deferred_task_ids), {healthy_id, broken_id}
        )
        self.assertEqual(dispatcher.status_calls, 0)

    def test_startup_inventory_does_not_enter_status_transition_path(self) -> None:
        dispatcher = FakeDispatcher(self.store)
        first_id, first_approval, _, service = self.approved_runtime(
            key="startup-status-conflict-first", dispatcher=dispatcher
        )
        service.submit_task(
            task_id=first_id,
            approval_id=first_approval["approval_id"],
            idempotency_key="submit-startup-status-conflict-first",
            **self.scope,
        )
        second_id, second_approval, _, service = self.approved_runtime(
            key="startup-status-conflict-second", dispatcher=dispatcher
        )
        service.submit_task(
            task_id=second_id,
            approval_id=second_approval["approval_id"],
            idempotency_key="submit-startup-status-conflict-second",
            **self.scope,
        )
        service.refresh_runtime_status = lambda *_args, **_kwargs: self.fail(
            "startup inventory must not enter status transitions"
        )
        recovered = service.recover_runtime_on_startup(
            PROJECT_ID, PRINCIPAL_ID
        )

        self.assertEqual(recovered.status_refresh_failures, ())
        self.assertEqual(recovered.status_refreshed_task_ids, ())
        self.assertEqual(
            set(recovered.pending_deferred_task_ids), {first_id, second_id}
        )

    def test_pre_runtime_abandon_is_exactly_idempotent_and_not_runtime_cancel(
        self,
    ) -> None:
        created = self.create(key="create-abandon-exact")
        now = [NOW]
        service = TaskService(self.store, clock=lambda: now[0])
        first = service.abandon_task(
            task_id=created.snapshot.task_id,
            idempotency_key="abandon-exact-key",
            **self.scope,
        )
        self.assertFalse(first.replayed)
        self.assertEqual(first.snapshot.status, "Cancelled")
        self.assertEqual(self.raw_count("task_abandonments"), 1)
        self.assertEqual(self.raw_count("workbench_mutations"), 1)

        now[0] = "2026-07-16T03:00:00Z"
        replay = service.abandon_task(
            task_id=created.snapshot.task_id,
            idempotency_key="abandon-exact-key",
            **self.scope,
        )
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.snapshot, first.snapshot)
        self.assertEqual(self.raw_count("task_abandonments"), 1)

        another_draft = task_draft()
        another_draft["draft_id"] = "draft-abandon-conflict"
        another = self.create(
            draft=another_draft, key="create-abandon-conflict"
        )
        with self.assertRaises(TaskIdempotencyConflict):
            service.abandon_task(
                task_id=another.snapshot.task_id,
                idempotency_key="abandon-exact-key",
                **self.scope,
            )
        self.assertEqual(
            self.store.get_task(another.snapshot.task_id).status,
            "AwaitingApproval",
        )

        task_id, _, _, runtime_service = self.submitted_runtime(
            key="abandon-after-dispatch"
        )
        with self.assertRaises(TaskConflict):
            runtime_service.abandon_task(
                task_id=task_id,
                idempotency_key="abandon-is-not-cancel",
                **self.scope,
            )
        self.assertEqual(self.store.get_task(task_id).status, "Queued")
        self.assertEqual(self.raw_count("task_abandonments"), 1)

        forged = {
            "schema_version": "1.0.0",
            "task_id": task_id,
            "previous_status": "AwaitingApproval",
            "status": "Cancelled",
            "reason": "user_discarded_draft",
            "actor": {"type": "user", "id": PRINCIPAL_ID},
            "abandoned_at": NOW,
            "extensions": {},
        }
        forged_json, forged_hash = encode_document(forged)
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO task_abandonments(
                        task_id, project_id, principal_id, document_json,
                        document_hash, abandoned_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        PROJECT_ID,
                        PRINCIPAL_ID,
                        forged_json,
                        forged_hash,
                        NOW,
                    ),
                )
        finally:
            connection.rollback()
            connection.close()

    def test_refresh_runtime_status_is_monotonic_and_poll_idempotent(self) -> None:
        task_id, _, dispatcher, service = self.submitted_runtime(
            key="status-lifecycle"
        )
        queued = service.refresh_runtime_status(task_id, **self.scope)
        self.assertEqual(queued.snapshot.status, "Queued")
        self.assertEqual(len(service.list_run_events(task_id, **self.scope)), 1)

        dispatcher.adapter_status = {
            "status": "Running",
            "stage": "inversion",
            "completed": 1,
            "total": 2,
            "message": "iteration 1 of 2",
            "updated_at": "2026-07-15T03:02:00Z",
            "terminal": False,
        }
        running = service.refresh_runtime_status(task_id, **self.scope)
        self.assertEqual(running.snapshot.status, "Running")
        events = service.list_run_events(task_id, **self.scope)
        self.assertEqual(
            [event["event_type"] for event in events],
            ["task_queued", "node_started", "node_progress"],
        )
        self.assertEqual(events[-1]["progress"]["completed"], 1)

        repeated = service.refresh_runtime_status(task_id, **self.scope)
        self.assertEqual(repeated.snapshot.status, "Running")
        self.assertEqual(len(service.list_run_events(task_id, **self.scope)), 3)

        dispatcher.adapter_status.update(
            {
                "completed": 2,
                "message": "iteration 2 of 2",
                "updated_at": "2026-07-15T03:03:00Z",
            }
        )
        service.refresh_runtime_status(task_id, **self.scope)
        self.assertEqual(len(service.list_run_events(task_id, **self.scope)), 4)

        dispatcher.adapter_status.update(
            {
                "completed": 1,
                "message": "regressed progress",
                "updated_at": "2026-07-15T03:04:00Z",
            }
        )
        with self.assertRaises(TaskDispatchError) as raised:
            service.refresh_runtime_status(task_id, **self.scope)
        self.assertEqual(raised.exception.code, "ADAPTER_PROGRESS_REGRESSION")
        self.assertEqual(len(service.list_run_events(task_id, **self.scope)), 4)

        dispatcher.adapter_status = {
            "status": "Succeeded",
            "stage": "complete",
            "completed": 2,
            "total": 2,
            "message": "complete",
            "updated_at": "2026-07-15T03:05:00Z",
            "terminal": True,
        }
        terminal = service.refresh_runtime_status(task_id, **self.scope)
        self.assertEqual(terminal.snapshot.status, "Succeeded")
        self.assertEqual(
            [
                event["event_type"]
                for event in service.list_run_events(task_id, **self.scope)
            ],
            [
                "task_queued",
                "node_started",
                "node_progress",
                "node_progress",
                "node_succeeded",
            ],
        )
        service.refresh_runtime_status(task_id, **self.scope)
        self.assertEqual(len(service.list_run_events(task_id, **self.scope)), 5)

    def test_refresh_runtime_status_pages_beyond_one_thousand_events(self) -> None:
        task_id, _, dispatcher, service = self.submitted_runtime(
            key="status-long-history"
        )
        dispatcher.adapter_status = {
            "status": "Running",
            "stage": "inversion",
            "completed": 1,
            "total": 2,
            "message": "iteration 1 of 2",
            "updated_at": "2026-07-15T03:02:00Z",
            "terminal": False,
        }
        service.refresh_runtime_status(task_id, **self.scope)

        first_page = [
            {
                "sequence": sequence,
                "event_id": f"event-long-history-{sequence:04d}",
                "event_type": "task_queued",
                "extensions": {},
            }
            for sequence in range(1, 1001)
        ]
        second_page = [
            {
                "sequence": 1001,
                "event_id": "event-long-history-1001",
                "event_type": "node_progress",
                "node_id": "invert",
                "progress": {"completed": 1},
                "extensions": {
                    "org.agent_rpc.adapter_status": {
                        "worker_updated_at": "2026-07-15T03:03:00Z"
                    }
                },
            }
        ]
        page_calls = []
        recorded = []
        original_list = service.list_run_events
        original_record = service.record_run_event
        original_high_water = service._store.latest_run_event_sequence

        def paged_events(_task_id, *, after_sequence=0, **_kwargs):
            page_calls.append(after_sequence)
            return first_page if after_sequence == 0 else second_page

        def capture_event(**kwargs):
            recorded.append(kwargs["event"])
            return service.get_task(task_id, **self.scope)

        dispatcher.adapter_status.update(
            {
                "completed": 2,
                "message": "iteration 2 of 2",
                "updated_at": "2026-07-15T03:04:00Z",
            }
        )
        service.list_run_events = paged_events
        service.record_run_event = capture_event
        service._store.latest_run_event_sequence = lambda _task_id: 1001
        try:
            result = service.refresh_runtime_status(task_id, **self.scope)
        finally:
            service.list_run_events = original_list
            service.record_run_event = original_record
            service._store.latest_run_event_sequence = original_high_water

        self.assertEqual(result.snapshot.status, "Running")
        self.assertEqual(page_calls, [0, 1000])
        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0]["sequence"], 1002)
        self.assertEqual(recorded[0]["progress"]["completed"], 2)

    def test_refresh_runtime_status_rejects_a_nonadvancing_event_page(self) -> None:
        task_id, _, _, service = self.submitted_runtime(
            key="status-nonadvancing-history"
        )
        original_list = service.list_run_events
        original_high_water = service._store.latest_run_event_sequence

        def repeated_page(_task_id, **_kwargs):
            return [
                {
                    "sequence": 1,
                    "event_id": "event-repeated-sequence",
                    "event_type": "task_queued",
                    "extensions": {},
                }
            ]

        service.list_run_events = repeated_page
        service._store.latest_run_event_sequence = lambda _task_id: 2
        try:
            with self.assertRaises(TaskConflict) as raised:
                service.refresh_runtime_status(task_id, **self.scope)
        finally:
            service.list_run_events = original_list
            service._store.latest_run_event_sequence = original_high_water

        self.assertIn("did not advance monotonically", str(raised.exception))

    def test_worker_timestamp_regression_fails_closed(self) -> None:
        task_id, _, dispatcher, service = self.submitted_runtime(
            key="status-worker-time-regression"
        )
        dispatcher.adapter_status = {
            "status": "Running",
            "stage": "inversion",
            "completed": 1,
            "total": 2,
            "message": "iteration 1 of 2",
            "updated_at": "2026-07-15T03:02:00Z",
            "terminal": False,
        }
        running = service.refresh_runtime_status(task_id, **self.scope)
        self.assertEqual(running.snapshot.status, "Running")
        before = service.list_run_events(task_id, **self.scope)
        self.assertEqual(len(before), 3)

        dispatcher.adapter_status.update(
            {
                "message": "stale worker observation",
                "updated_at": "2026-07-15T03:01:59Z",
            }
        )
        with self.assertRaises(TaskDispatchError) as raised:
            service.refresh_runtime_status(task_id, **self.scope)
        self.assertEqual(raised.exception.code, "ADAPTER_STATUS_REGRESSION")
        self.assertEqual(service.list_run_events(task_id, **self.scope), before)
        self.assertEqual(
            service.get_task(task_id, **self.scope).status,
            "Running",
        )

    def test_refresh_supports_direct_success_and_failure_without_fake_states(
        self,
    ) -> None:
        success_id, _, success_dispatcher, success_service = self.submitted_runtime(
            key="status-direct-success"
        )
        success_dispatcher.adapter_status = {
            "status": "Succeeded",
            "stage": "complete",
            "completed": 2,
            "total": 2,
            "message": "complete",
            "updated_at": "2026-07-15T03:06:00Z",
            "terminal": True,
        }
        success = success_service.refresh_runtime_status(success_id, **self.scope)
        self.assertEqual(success.snapshot.status, "Succeeded")
        self.assertEqual(
            [
                event["event_type"]
                for event in success_service.list_run_events(success_id, **self.scope)
            ],
            ["task_queued", "node_started", "node_succeeded"],
        )

        failed_id, _, failed_dispatcher, failed_service = self.submitted_runtime(
            key="status-direct-failure"
        )
        failed_dispatcher.adapter_status = {
            "status": "Failed",
            "stage": "failed",
            "completed": 0,
            "total": 2,
            "message": "worker failed",
            "updated_at": "2026-07-15T03:07:00Z",
            "terminal": True,
        }
        failed = failed_service.refresh_runtime_status(failed_id, **self.scope)
        self.assertEqual(failed.snapshot.status, "Failed")
        self.assertEqual(
            [
                event["event_type"]
                for event in failed_service.list_run_events(failed_id, **self.scope)
            ],
            ["task_queued", "node_failed"],
        )
        failed_service.refresh_runtime_status(failed_id, **self.scope)
        self.assertEqual(
            len(failed_service.list_run_events(failed_id, **self.scope)), 2
        )

    def test_terminal_artifacts_match_declared_outputs_and_runtime(self) -> None:
        task_id, plan, dispatcher, service = self.submitted_runtime(
            key="terminal-artifacts"
        )
        dispatcher.adapter_status = {
            "status": "Succeeded",
            "stage": "complete",
            "completed": 2,
            "total": 2,
            "message": "complete",
            "updated_at": "2026-07-15T03:08:00Z",
            "terminal": True,
        }
        service.refresh_runtime_status(task_id, **self.scope)
        manifests, payloads = self.artifact_manifests(task_id)
        dispatcher.manifests = manifests
        dispatcher.artifact_data = payloads

        collected = service.collect_artifacts(task_id, **self.scope)
        self.assertEqual(
            [(value["artifact_type"], value["media_type"]) for value in collected],
            [
                ("inverted_velocity_model_2d", "application/x-npy"),
                ("loss_curve", "text/csv"),
                *(("figure", "image/png") for _ in range(6)),
            ],
        )
        self.assertTrue(
            all(value["lineage"]["plan_hash"] == plan["plan_hash"] for value in collected)
        )
        for manifest in collected:
            returned, data = service.read_artifact(
                task_id, manifest["artifact_id"], **self.scope
            )
            self.assertEqual(returned, manifest)
            self.assertEqual(
                "sha256:" + hashlib.sha256(data).hexdigest(),
                manifest["content_hash"],
            )

        wrong_set = copy.deepcopy(manifests)
        wrong_set[1]["artifact_type"] = "inverted_velocity_model_2d"
        wrong_set[1]["media_type"] = "application/x-npy"
        wrong_set[1]["display"]["component"] = "download"
        wrong_set[1]["extensions"]["org.agent_rpc.adapter"][
            "output_port"
        ] = "inverted_model"
        dispatcher.manifests = wrong_set
        with self.assertRaises(TaskDispatchError) as raised:
            service.collect_artifacts(task_id, **self.scope)
        self.assertEqual(raised.exception.code, "ADAPTER_ARTIFACT_INVALID")

        for label, mutate in (
            (
                "component",
                lambda value: value[2]["display"].__setitem__(
                    "component", "download"
                ),
            ),
            (
                "display-order",
                lambda value: value[2]["display"].__setitem__("order", 1),
            ),
            (
                "figure-id",
                lambda value: value[2]["extensions"][
                    "org.agent_rpc.figure"
                ].__setitem__("figure_id", "initial_model"),
            ),
            (
                "figure-dimensions",
                lambda value: value[2]["extensions"][
                    "org.agent_rpc.figure"
                ].__setitem__("width_px", 1439),
            ),
        ):
            with self.subTest(artifact_contract=label):
                tampered = copy.deepcopy(manifests)
                mutate(tampered)
                dispatcher.manifests = tampered
                with self.assertRaises(TaskDispatchError) as raised:
                    service.collect_artifacts(task_id, **self.scope)
                self.assertEqual(
                    raised.exception.code, "ADAPTER_ARTIFACT_INVALID"
                )

        intent = self.store.get_dispatch_intent(task_id)
        self.assertIsNotNone(intent)
        persisted_two_output_plan = copy.deepcopy(
            service.get_task(task_id, **self.scope).plan
        )
        persisted_two_output_plan["nodes"][0]["outputs"] = persisted_two_output_plan[
            "nodes"
        ][0]["outputs"][:2]
        historical_snapshot = replace(
            service.get_task(task_id, **self.scope), plan=persisted_two_output_plan
        )
        self.assertEqual(
            len(
                TaskService._validate_collected_artifacts(
                    historical_snapshot, intent, copy.deepcopy(manifests[:2])
                )
            ),
            2,
        )

        for label, mutate in (
            ("task", lambda value: value.__setitem__("task_id", "task-other")),
            (
                "plan",
                lambda value: value["lineage"].__setitem__(
                    "plan_hash", "sha256:" + "f" * 64
                ),
            ),
            (
                "fingerprint",
                lambda value: value["fingerprint"].__setitem__("seed", 9),
            ),
            (
                "input",
                lambda value: value["lineage"]["inputs"][0].__setitem__(
                    "content_hash", "sha256:" + "e" * 64
                ),
            ),
        ):
            with self.subTest(binding=label):
                tampered = copy.deepcopy(manifests)
                mutate(tampered[0])
                dispatcher.manifests = tampered
                with self.assertRaises(TaskDispatchError) as raised:
                    service.collect_artifacts(task_id, **self.scope)
                self.assertEqual(
                    raised.exception.code, "ADAPTER_ARTIFACT_INVALID"
                )

        dispatcher.manifests = manifests
        dispatcher.artifact_data = copy.deepcopy(payloads)
        dispatcher.artifact_data[manifests[0]["artifact_id"]] += b"tampered"
        with self.assertRaises(TaskDispatchError) as raised:
            service.read_artifact(
                task_id, manifests[0]["artifact_id"], **self.scope
            )
        self.assertEqual(raised.exception.code, "ADAPTER_ARTIFACT_INVALID")

    def test_hash_consistent_dispatch_request_tampering_fails_closed(self) -> None:
        created = self.create_executable()
        task_id = created.snapshot.task_id
        _, approval = self.persist_executable_plan_and_approval(task_id)
        self.submit_service(FakeDispatcher(self.store)).submit_task(
            task_id=task_id,
            approval_id=approval["approval_id"],
            idempotency_key="submit-before-intent-tamper",
            **self.scope,
        )
        connection = sqlite3.connect(self.database_path)
        try:
            row = connection.execute(
                "SELECT request_json FROM dispatch_intents WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            document = json.loads(row[0])
            document["request"]["parameters"]["iterations"] += 1
            document_json, document_hash = encode_document(document)
            connection.execute("DROP TRIGGER dispatch_intents_are_immutable")
            connection.execute(
                """
                UPDATE dispatch_intents
                SET request_json = ?, request_hash = ?
                WHERE task_id = ?
                """,
                (document_json, document_hash, task_id),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(
            TaskStoreCorruption, "payload differs from current plan"
        ):
            self.store.get_task(task_id)


if __name__ == "__main__":
    unittest.main()
