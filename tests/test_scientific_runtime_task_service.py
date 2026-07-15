from __future__ import annotations

import copy
import fcntl
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from scientific_runtime import (
    DispatchError,
    DispatchPreparation,
    RegistryService,
    SQLiteTaskStore,
    TaskConflict,
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
from scientific_runtime.task_store import APPLICATION_ID, encode_document
from tests.test_scientific_runtime_contracts import (
    append_second_plan_node,
    algorithm_manifest,
    approval_decision,
    dataset_ref,
    fingerprint,
    plan_graph,
    run_event,
    task_draft,
)


NOW = "2026-07-15T03:00:00Z"
PROJECT_ID = "project-1"
PRINCIPAL_ID = "user-1"


def dispatch_fingerprint() -> dict:
    value = fingerprint()
    value["provenance_mode"] = "development"
    value["source"] = {"identity_complete": False, "dirty": None}
    return value


class FakeDispatcher:
    def __init__(self, store: SQLiteTaskStore, *, failure_code: str | None = None):
        self.store = store
        self.failure_code = failure_code
        self.prepare_calls = 0
        self.dispatch_calls = 0
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
            adapter_version="1.0.0",
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
            raise DispatchError(self.failure_code)
        return {
            "submission_id": "submission-test-001",
            "task_id": intent.task_id,
            "node_id": intent.node_id,
            "job_id": "fwi-20260715T030000Z-000000000001",
            "idempotency_key": intent.node_idempotency_key,
            "plan_hash": intent.plan_hash,
            "request_hash": "sha256:" + "a" * 64,
            "algorithm": copy.deepcopy(intent.request["algorithm"]),
            # The queued fingerprint is preflight evidence.  Runtime events
            # bind to the actual fingerprint returned in this receipt.
            "fingerprint": fingerprint(),
            "adapter_version": intent.adapter_version,
        }


class ScientificRuntimeTaskServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary.name) / "task.sqlite3"
        self.store = SQLiteTaskStore(self.database_path)
        self.registry = RegistryService(self.store, clock=lambda: NOW)
        self.registry.register_dataset(dataset=dataset_ref())
        self.registry.register_algorithm(manifest=algorithm_manifest())
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
        self.assertEqual(result.intent.state, "dispatched")
        return service.list_run_events(task_id, **self.scope)[0]

    def test_initialization_enables_wal_and_is_reentrant(self) -> None:
        self.assertEqual(self.store.journal_mode(), "wal")
        self.assertEqual(self.store.migration_version(), 3)
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
        self.assertEqual(reopened.migration_version(), 3)
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
        self.assertEqual(results, [("wal", 3)] * 8)

    def test_newer_database_migration_is_rejected(self) -> None:
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute("PRAGMA user_version = 4")
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
        created = self.create()
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

        plan = plan_graph()
        with self.assertRaises(TaskNotFound):
            self.service.persist_plan(
                task_id=task_id,
                project_id="project-2",
                principal_id=PRINCIPAL_ID,
                plan=plan,
            )
        self.service.persist_plan(task_id=task_id, plan=plan, **self.scope)
        approval = approval_decision(plan)
        with self.assertRaises(TaskNotFound):
            self.service.persist_approval(
                task_id=task_id,
                project_id="project-2",
                principal_id=PRINCIPAL_ID,
                approval=approval,
            )
        foreign_approval = approval_decision(plan)
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
        started = run_event()
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
        created = self.create()
        task_id = created.snapshot.task_id
        self.persist_plan_and_approval(task_id)
        queued_event = self.seed_validated_queue_event(task_id)

        started = run_event()
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
        created = self.create()
        task_id = created.snapshot.task_id
        self.persist_plan_and_approval(task_id)
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

        started = run_event()
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
        created = self.create()
        task_id = created.snapshot.task_id
        self.persist_plan_and_approval(task_id)
        self.seed_validated_queue_event(task_id)

        incoherent = run_event()
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

        deferred = run_event()
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

        started = run_event()
        started.update(
            {"event_id": "event-started-002", "sequence": 2, "task_id": task_id}
        )
        self.service.record_run_event(
            task_id=task_id,
            expected_status="Queued",
            event=started,
            **self.scope,
        )
        drifted = run_event()
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
        created = self.create()
        task_id = created.snapshot.task_id
        self.persist_plan_and_approval(task_id)
        self.seed_validated_queue_event(task_id)
        started = run_event()
        started.update(
            {"event_id": "event-started-002", "sequence": 2, "task_id": task_id}
        )
        self.service.record_run_event(
            task_id=task_id,
            expected_status="Queued",
            event=started,
            **self.scope,
        )

        success = run_event()
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

        late_progress = run_event()
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
        created = self.create()
        task_id = created.snapshot.task_id
        self.persist_plan_and_approval(task_id)
        self.seed_validated_queue_event(task_id)
        started = run_event()
        started.update(
            {"event_id": "event-started-002", "sequence": 2, "task_id": task_id}
        )
        self.service.record_run_event(
            task_id=task_id,
            expected_status="Queued",
            event=started,
            **self.scope,
        )

        checkpoint = run_event()
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
        created = self.create()
        task_id = created.snapshot.task_id
        self.persist_plan_and_approval(task_id)
        self.seed_validated_queue_event(task_id)

        unknown_node = run_event()
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
                event = run_event()
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
        created = self.create()
        task_id = created.snapshot.task_id
        self.persist_plan_and_approval(task_id)
        self.seed_validated_queue_event(task_id)
        success = run_event()
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

    def test_atomic_submit_consumes_budget_queues_and_dispatches_after_commit(
        self,
    ) -> None:
        created = self.create()
        task_id = created.snapshot.task_id
        _, approval = self.persist_plan_and_approval(task_id)
        dispatcher = FakeDispatcher(self.store)
        service = self.submit_service(dispatcher)

        result = service.submit_task(
            task_id=task_id,
            approval_id=approval["approval_id"],
            idempotency_key="submit-operation-key",
            **self.scope,
        )

        self.assertFalse(result.replayed)
        self.assertTrue(result.dispatch_attempted)
        self.assertEqual(result.snapshot.status, "Queued")
        self.assertEqual(result.intent.state, "dispatched")
        self.assertIsNotNone(result.intent.dispatch_claimed_at)
        self.assertIsNotNone(result.intent.outcome_recorded_at)
        self.assertEqual(dispatcher.dispatch_calls, 1)
        self.assertEqual(self.raw_count("dispatch_intents"), 1)
        self.assertEqual(self.raw_count("dispatch_attempts"), 1)
        self.assertEqual(self.raw_count("dispatch_outcomes"), 1)
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
        self.assertEqual(dispatcher.dispatch_calls, 1)
        self.assertEqual(self.raw_count("run_events"), 1)

    def test_submit_adapter_error_is_sticky_reconciliation_not_task_failure(
        self,
    ) -> None:
        created = self.create()
        task_id = created.snapshot.task_id
        _, approval = self.persist_plan_and_approval(task_id)
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
        self.assertEqual(result.intent.state, "reconciliation_required")
        self.assertEqual(
            result.intent.failure_code, "SUBMISSION_RECONCILIATION_REQUIRED"
        )
        self.assertEqual(len(service.list_run_events(task_id, **self.scope)), 1)

        replay = service.submit_task(
            task_id=task_id,
            approval_id=approval["approval_id"],
            idempotency_key="submit-reconciliation-key",
            **self.scope,
        )
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.intent.state, "reconciliation_required")
        self.assertEqual(dispatcher.dispatch_calls, 1)

    def test_invalid_dispatch_receipt_requires_reconciliation(self) -> None:
        created = self.create()
        task_id = created.snapshot.task_id
        _, approval = self.persist_plan_and_approval(task_id)
        dispatcher = FakeDispatcher(self.store)
        valid_dispatch = dispatcher.dispatch

        def invalid_dispatch(intent):
            handle = valid_dispatch(intent)
            handle["fingerprint"].pop("runtime")
            return handle

        dispatcher.dispatch = invalid_dispatch
        result = self.submit_service(dispatcher).submit_task(
            task_id=task_id,
            approval_id=approval["approval_id"],
            idempotency_key="submit-invalid-receipt",
            **self.scope,
        )
        self.assertEqual(result.snapshot.status, "Queued")
        self.assertEqual(result.intent.state, "reconciliation_required")
        self.assertEqual(result.intent.failure_code, "DISPATCH_RECEIPT_INVALID")
        self.assertEqual(self.raw_count("dispatch_outcomes"), 1)

    def test_submit_exact_replay_precedes_expiry_budget_and_preflight(self) -> None:
        created = self.create()
        task_id = created.snapshot.task_id
        _, approval = self.persist_plan_and_approval(task_id)
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
        self.assertEqual(dispatcher.dispatch_calls, 1)

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
        created = self.create()
        task_id = created.snapshot.task_id
        _, approval = self.persist_plan_and_approval(task_id)
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

    def test_concurrent_same_submit_key_converges_to_one_dispatch(self) -> None:
        created = self.create()
        task_id = created.snapshot.task_id
        _, approval = self.persist_plan_and_approval(task_id)
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
        self.assertEqual(dispatcher.dispatch_calls, 1)
        self.assertEqual(self.raw_count("dispatch_intents"), 1)
        self.assertEqual(self.raw_count("dispatch_attempts"), 1)
        self.assertEqual(self.raw_count("dispatch_outcomes"), 1)
        self.assertEqual(self.raw_count("run_events"), 1)
        self.assertEqual(
            self.store.get_approval_budget(
                task_id=task_id, approval_id=approval["approval_id"]
            ).tasks_used,
            1,
        )

    def test_concurrent_different_submit_keys_admit_only_one_task(self) -> None:
        created = self.create()
        task_id = created.snapshot.task_id
        _, approval = self.persist_plan_and_approval(task_id)
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
        self.assertEqual(dispatcher.dispatch_calls, 1)
        self.assertEqual(self.raw_count("dispatch_intents"), 1)
        self.assertEqual(self.raw_count("run_events"), 1)
        self.assertEqual(
            self.store.get_approval_budget(
                task_id=task_id, approval_id=approval["approval_id"]
            ).tasks_used,
            1,
        )

    def test_submit_status_failure_rolls_back_every_admission_write(self) -> None:
        created = self.create()
        task_id = created.snapshot.task_id
        _, approval = self.persist_plan_and_approval(task_id)
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

    def test_commit_before_dispatch_crash_stays_pending_and_is_not_retried(self) -> None:
        created = self.create()
        task_id = created.snapshot.task_id
        _, approval = self.persist_plan_and_approval(task_id)
        dispatcher = FakeDispatcher(self.store)
        original_claim = self.store.claim_dispatch

        def simulate_crash(**_kwargs):
            raise TaskStoreConflict("simulated process loss after commit")

        self.store.claim_dispatch = simulate_crash
        try:
            with self.assertRaises(TaskConflict):
                self.submit_service(dispatcher).submit_task(
                    task_id=task_id,
                    approval_id=approval["approval_id"],
                    idempotency_key="submit-before-dispatch-crash",
                    **self.scope,
                )
        finally:
            self.store.claim_dispatch = original_claim
        self.assertEqual(self.store.get_task(task_id).status, "Queued")
        self.assertEqual(self.store.get_dispatch_intent(task_id).state, "pending")
        self.assertEqual(dispatcher.dispatch_calls, 0)

        replay = self.submit_service(dispatcher).submit_task(
            task_id=task_id,
            approval_id=approval["approval_id"],
            idempotency_key="submit-before-dispatch-crash",
            **self.scope,
        )
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.intent.state, "pending")
        self.assertEqual(dispatcher.dispatch_calls, 0)

    def test_started_worker_without_receipt_stays_dispatching_and_is_not_retried(
        self,
    ) -> None:
        created = self.create()
        task_id = created.snapshot.task_id
        _, approval = self.persist_plan_and_approval(task_id)
        dispatcher = FakeDispatcher(self.store)
        original_record = self.store.record_dispatch_success

        def lose_receipt(**_kwargs):
            raise TaskStoreConflict("simulated receipt persistence loss")

        self.store.record_dispatch_success = lose_receipt
        try:
            with self.assertRaises(TaskConflict):
                self.submit_service(dispatcher).submit_task(
                    task_id=task_id,
                    approval_id=approval["approval_id"],
                    idempotency_key="submit-after-worker-crash",
                    **self.scope,
                )
        finally:
            self.store.record_dispatch_success = original_record
        self.assertEqual(dispatcher.dispatch_calls, 1)
        self.assertEqual(
            self.store.get_dispatch_intent(task_id).state, "dispatching"
        )

        replay = self.submit_service(dispatcher).submit_task(
            task_id=task_id,
            approval_id=approval["approval_id"],
            idempotency_key="submit-after-worker-crash",
            **self.scope,
        )
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.intent.state, "dispatching")
        self.assertEqual(dispatcher.dispatch_calls, 1)

    def test_hash_consistent_dispatch_request_tampering_fails_closed(self) -> None:
        created = self.create()
        task_id = created.snapshot.task_id
        _, approval = self.persist_plan_and_approval(task_id)
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
