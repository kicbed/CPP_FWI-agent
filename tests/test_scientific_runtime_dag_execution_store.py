from __future__ import annotations

import contextlib
import copy
import json
import shutil
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest import mock

import scientific_runtime.task_store as task_store_module

from scientific_runtime import (
    RegistryService,
    SQLiteTaskStore,
    TaskConflict,
    TaskDispatchError,
    TaskService,
    TaskStoreConflict,
    TaskStoreCorruption,
    TaskSupervisorLeaseLost,
    load_deepwave_manifest,
)
from scientific_runtime.task_dispatcher import (
    DispatchNotStartedProof,
    DispatchPreparation,
    DispatchRetryProof,
)
from scientific_runtime.task_store import encode_document
from scientific_runtime_contracts import compute_plan_hash
from tests.test_scientific_runtime_contracts import approval_decision, dataset_ref
from tests import test_scientific_runtime_task_service as task_service_fixtures
from tests.test_scientific_runtime_task_service import (
    FakeDispatcher,
    checkpoint_wait_proof,
    current_optimizer_plan_graph,
    current_optimizer_task_draft,
    dispatch_fingerprint,
    managed_worker_evidence,
)


NOW = "2026-07-15T03:00:00Z"
RUNTIME_NOW = "2026-07-15T03:00:10Z"
PROJECT_ID = "project-1"
PRINCIPAL_ID = "user-1"


class ControlledDagDispatcher(FakeDispatcher):
    """Deterministic Adapter boundary exercising the real P2 dispatch path."""

    def __init__(self, store: SQLiteTaskStore) -> None:
        super().__init__(store)
        self.output_attempt_id: str | None = None
        self.output_receipt_record_hash = "sha256:" + "9" * 64
        self.worker_exit_probe_calls = 0

    def prepare_node(self, snapshot, *, node_id, input_binding):
        with self.lock:
            self.prepare_calls += 1
        plan = snapshot.plan
        node = next(value for value in plan["nodes"] if value["node_id"] == node_id)
        bound = input_binding.binding_document["inputs"][0]
        dataset = next(
            value
            for value in snapshot.draft["datasets"]
            if all(
                value[key] == bound["dataset"][key]
                for key in ("id", "version", "content_hash", "data_type")
            )
        )
        fingerprint = dispatch_fingerprint(self.adapter_version)
        request = {
            "task_id": snapshot.task_id,
            "node_id": node["node_id"],
            "plan_hash": plan["plan_hash"],
            "idempotency_key": node["idempotency_key"],
            "project_id": snapshot.project_id,
            "principal_id": snapshot.principal_id,
            "algorithm": copy.deepcopy(node["algorithm"]),
            "dataset": copy.deepcopy(dataset),
            "task_type": plan["task_type"],
            "parameters": copy.deepcopy(node["parameters"]),
            "resources": copy.deepcopy(node["resources"]),
            "normalized_config_hash": fingerprint["normalized_config_hash"],
        }
        return DispatchPreparation(
            adapter_id="fwi.deepwave_adapter",
            adapter_version=self.adapter_version,
            request=request,
            queue_fingerprint=fingerprint,
        )

    def dispatch(self, intent):
        handle = super().dispatch(intent)
        # DAG RunEvents preserve the same exact preflight/adopted fingerprint
        # identity already committed with the queued intent.
        handle["fingerprint"] = copy.deepcopy(intent.queue_fingerprint)
        self.worker_observation["handle"]["fingerprint"] = copy.deepcopy(
            intent.queue_fingerprint
        )
        return handle

    @contextlib.contextmanager
    def verified_node_outputs(self, intent):
        evidence = self.worker_observation["evidence"]
        yield {
            "schema_version": "1.0.0",
            "receipt_record_hash": self.output_receipt_record_hash,
            "attempt_id": self.output_attempt_id or evidence["attempt_id"],
            "attempt_number": evidence["attempt_number"],
            "manifests": copy.deepcopy(self.manifests),
            "intent_id": intent.intent_id,
        }

    def probe_worker_exit_retry(self, intent):
        del intent
        self.worker_exit_probe_calls += 1
        evidence = copy.deepcopy(self.worker_observation["evidence"])
        private_evidence = {
            "schema_version": "1.0.0",
            "failure_kind": "worker_exit",
            "submission_id": evidence["submission_id"],
            "attempt_id": evidence["attempt_id"],
            "attempt_number": evidence["attempt_number"],
            "job_id": evidence["job_id"],
            "request_hash": evidence["request_hash"],
            "binding_hash": evidence["binding_hash"],
            "observed_at": NOW,
        }
        return DispatchRetryProof(
            failure_kind="worker_exit",
            previous_attempt_id=evidence["attempt_id"],
            previous_attempt_number=1,
            private_schema_version="1.1.0",
            private_proof_hash=encode_document(private_evidence)[1],
            evidence=evidence,
            private_evidence=private_evidence,
        )


class ScientificRuntimeDagExecutionStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary.name) / "task.sqlite3"
        self.store = SQLiteTaskStore(self.database_path)
        self.registry = RegistryService(self.store, clock=lambda: NOW)
        self.registry.register_dataset(dataset=dataset_ref())
        self.registry.register_algorithm(manifest=load_deepwave_manifest("1.6.0"))
        self.dispatcher = ControlledDagDispatcher(self.store)
        self.next_task = 0

        def task_id_factory() -> str:
            self.next_task += 1
            return f"task-dag-execution-{self.next_task:03d}"

        self.service = TaskService(
            self.store,
            task_id_factory=task_id_factory,
            clock=lambda: RUNTIME_NOW,
            dispatcher=self.dispatcher,
        )
        self.scope = {
            "project_id": PROJECT_ID,
            "principal_id": PRINCIPAL_ID,
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _count(self, table: str, *, task_id: str | None = None) -> int:
        self.assertIn(
            table,
            {
                "dag_node_execution_admissions",
                "dag_node_execution_transition_facts",
                "dag_node_terminal_facts",
                "dispatch_intents",
                "dispatch_attempts",
                "dispatch_outcomes",
                "run_events",
                "worker_launch_attempts",
            },
        )
        connection = sqlite3.connect(self.database_path)
        try:
            if task_id is None:
                row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            else:
                row = connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE task_id = ?", (task_id,)
                ).fetchone()
            return int(row[0])
        finally:
            connection.close()

    def _approved_dag(self, suffix: str):
        draft = current_optimizer_task_draft()
        draft["draft_id"] = f"draft-dag-execution-{suffix}"
        created = self.service.create_task(
            draft=draft,
            idempotency_key=f"create-dag-execution-{suffix}",
            **self.scope,
        )
        task_id = created.snapshot.task_id

        plan = current_optimizer_plan_graph()
        plan["plan_id"] = f"plan-dag-execution-{suffix}"
        plan["draft"] = {
            "draft_id": draft["draft_id"],
            "revision": draft["revision"],
        }
        root = copy.deepcopy(plan["nodes"][0])
        root["node_id"] = "root"
        root["idempotency_key"] = f"{task_id}:root:0001"
        root["dependencies"] = []
        spare = copy.deepcopy(root)
        spare["node_id"] = "spare"
        spare["idempotency_key"] = f"{task_id}:spare:0001"
        plan["nodes"] = [root, spare]
        plan["plan_hash"] = compute_plan_hash(plan)
        self.service.persist_plan(task_id=task_id, plan=plan, **self.scope)

        approval = approval_decision(plan)
        approval["approval_id"] = f"approval-dag-execution-{suffix}"
        approval["scope"]["algorithms"] = [copy.deepcopy(root["algorithm"])]
        self.service.persist_approval(
            task_id=task_id, approval=approval, **self.scope
        )
        return task_id, plan, approval

    def _bound_root(self, suffix: str, *, owner_id: str | None = None):
        task_id, plan, approval = self._approved_dag(suffix)
        lease = self.service.acquire_runtime_supervisor_lease(
            owner_id=owner_id or f"dag-execution-{suffix}",
            lease_seconds=30,
            **self.scope,
        ).lease
        claim = self.service.claim_ready_dag_node_candidate(
            task_id,
            expected_plan_hash=plan["plan_hash"],
            supervisor_lease=lease,
            **self.scope,
        )
        binding = self.service.bind_ready_dag_node_inputs(
            task_id,
            expected_plan_hash=plan["plan_hash"],
            claim_candidate=claim,
            artifact_inputs=(),
            supervisor_lease=lease,
            **self.scope,
        )
        self.assertEqual(
            (claim.node.node_id, binding.target_node_id), ("root", "root")
        )
        return task_id, plan, approval, lease, binding

    def _admit(self, task_id, plan, binding, lease):
        return self.service.admit_ready_dag_node_execution(
            task_id,
            expected_plan_hash=plan["plan_hash"],
            input_binding=binding,
            supervisor_lease=lease,
            **self.scope,
        )

    def _node_states(self, task_id: str) -> dict[str, tuple[int, str]]:
        state = self.store.get_dag_node_state_snapshot(
            task_id=task_id, **self.scope
        )
        self.assertIsNotNone(state)
        return {value.node_id: (value.revision, value.state) for value in state.nodes}

    def _artifact_manifests(self, task_id: str) -> list[dict]:
        # Reuse the established P2 exact artifact fixture against this DAG intent.
        manifests, _ = (
            task_service_fixtures.ScientificRuntimeTaskServiceTest.artifact_manifests(
                self, task_id
            )
        )
        return manifests

    def _project_terminal_heartbeat(self, task_id: str, lease, state: str) -> None:
        intent = self.store.get_dispatch_intent(task_id)
        self.assertIsNotNone(intent)
        self.assertIsNotNone(intent.handle)
        current = self.dispatcher.worker_observation["evidence"]
        current_heartbeat = current["heartbeat"]
        self.dispatcher.worker_observation = {
            "evidence": managed_worker_evidence(
                attempt_id=current["attempt_id"],
                attempt_number=current["attempt_number"],
                job_id=current["job_id"],
                heartbeat_sequence=current_heartbeat["sequence"] + 1,
                heartbeat_state=state,
            ),
            "handle": copy.deepcopy(intent.handle),
        }
        projected = self.service.project_worker_attempt(
            task_id, supervisor_lease=lease, **self.scope
        )
        self.assertTrue(projected.projected)

    def test_controlled_root_node_reaches_v20_succeeded_receipt_once(self) -> None:
        task_id, plan, approval, lease, binding = self._bound_root("success")

        def admit_once(_: int):
            return self._admit(task_id, plan, binding, lease)

        with ThreadPoolExecutor(max_workers=6) as executor:
            admissions = list(executor.map(admit_once, range(6)))

        self.assertEqual(sum(not value.replayed for value in admissions), 1)
        self.assertEqual(sum(value.replayed for value in admissions), 5)
        self.assertEqual({value.intent.intent_id for value in admissions}, {
            admissions[0].intent.intent_id
        })
        self.assertEqual(self._node_states(task_id)["root"], (2, "Queued"))
        self.assertEqual(self._count("dag_node_execution_admissions"), 1)
        self.assertEqual(self._count("dispatch_intents", task_id=task_id), 1)
        self.assertEqual(
            self.store.get_approval_budget(
                task_id=task_id, approval_id=approval["approval_id"]
            ).tasks_used,
            1,
        )

        first_dispatch = self.service.schedule_runtime_dispatch(
            task_id, supervisor_lease=lease, **self.scope
        )
        replayed_dispatch = self.service.schedule_runtime_dispatch(
            task_id, supervisor_lease=lease, **self.scope
        )
        self.assertEqual(first_dispatch.intent.state, "dispatched")
        self.assertEqual(replayed_dispatch.intent.state, "dispatched")
        self.assertEqual(self.dispatcher.dispatch_calls, 1)
        self.assertEqual(self._count("dispatch_attempts"), 1)
        self.assertEqual(self._count("dispatch_outcomes"), 1)
        self.assertEqual(self._count("worker_launch_attempts", task_id=task_id), 1)

        self.dispatcher.adapter_status = {
            "status": "Running",
            "stage": "inversion",
            "completed": 1,
            "total": 2,
            "message": "iteration 1 of 2",
            "updated_at": "2026-07-15T03:00:01Z",
            "terminal": False,
        }
        running = self.service.refresh_runtime_status(
            task_id, supervisor_lease=lease, **self.scope
        )
        self.assertEqual(running.snapshot.status, "Running")
        self.assertEqual(self._node_states(task_id)["root"], (3, "Running"))

        self.dispatcher.manifests = self._artifact_manifests(task_id)
        self._project_terminal_heartbeat(task_id, lease, "succeeded")
        self.dispatcher.adapter_status = {
            "status": "Succeeded",
            "stage": "complete",
            "completed": 2,
            "total": 2,
            "message": "complete",
            "updated_at": "2026-07-15T03:00:02Z",
            "terminal": True,
        }
        def refresh_success(_: int):
            return self.service.refresh_runtime_status(
                task_id, supervisor_lease=lease, **self.scope
            )

        with ThreadPoolExecutor(max_workers=6) as executor:
            concurrent_successes = list(executor.map(refresh_success, range(6)))
        succeeded = concurrent_successes[0]
        replayed_success = self.service.refresh_runtime_status(
            task_id, supervisor_lease=lease, **self.scope
        )
        self.assertEqual(
            {value.snapshot.status for value in concurrent_successes}, {"Running"}
        )
        self.assertEqual(
            (succeeded.snapshot.status, replayed_success.snapshot.status),
            ("Running", "Running"),
        )
        self.assertEqual(self._node_states(task_id), {
            "root": (4, "Succeeded"),
            "spare": (1, "Pending"),
        })
        self.assertEqual(
            [value["event_type"] for value in self.service.list_run_events(
                task_id, **self.scope
            )],
            [
                "task_queued",
                "node_started",
                "node_progress",
                "node_succeeded",
            ],
        )
        self.assertEqual(self._count("dag_node_terminal_facts"), 1)
        self.assertEqual(self._count("dag_node_execution_transition_facts"), 3)

        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            row = connection.execute(
                "SELECT * FROM dag_node_terminal_facts WHERE task_id = ?", (task_id,)
            ).fetchone()
            receipt = json.loads(row["receipt_document_json"])
        finally:
            connection.close()
        self.assertEqual((row["node_state"], row["attempt_number"]),
                         ("Succeeded", 1))
        self.assertEqual(receipt["schema_version"], "2.0.0")
        self.assertEqual(row["receipt_document_hash"], encode_document(receipt)[1])
        self.assertEqual(
            receipt["receipt_record_hash"],
            self.dispatcher.output_receipt_record_hash,
        )
        self.assertEqual(receipt["plan"], {
            "plan_id": plan["plan_id"], "plan_hash": plan["plan_hash"]
        })
        self.assertEqual(receipt["approval_id"], approval["approval_id"])
        self.assertEqual(
            receipt["input_binding_document_hash"], binding.binding_document_hash
        )
        self.assertEqual(
            receipt["dispatch"]["node_idempotency_key"],
            f"{task_id}:root:0001",
        )
        self.assertEqual(receipt["dispatch"]["attempt_number"], 1)
        self.assertEqual(
            receipt["input_supervisor_term"]["fencing_token"],
            binding.fencing_token,
        )
        self.assertEqual(
            receipt["completion_supervisor_term"]["fencing_token"],
            lease.fencing_token,
        )
        self.assertEqual(len(receipt["outputs"]), len(plan["nodes"][0]["outputs"]))

        state_map = self.store.get_dag_node_state_snapshot(
            task_id=task_id, **self.scope
        )
        root_state = next(
            value for value in state_map.nodes if value.node_id == "root"
        )
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            loaded = self.store._load_dag_node_succeeded_outputs(
                connection,
                task_id=task_id,
                plan=plan,
                approval_id=approval["approval_id"],
                source_state=root_state,
            )
        finally:
            connection.close()
        self.assertEqual(loaded["receipt_document_hash"], row["receipt_document_hash"])
        self.assertEqual(loaded["fencing_token"], lease.fencing_token)
        self.assertEqual(set(loaded["outputs_by_port"]), {
            value["port"] for value in plan["nodes"][0]["outputs"]
        })

    def test_nonempty_v20_dag_kernel_rebuild_preserves_exact_rows_and_advances(
        self,
    ) -> None:
        legacy_migrations = Path(self.temporary.name) / "v20-migrations"
        legacy_migrations.mkdir(mode=0o700)
        for migration in sorted(
            task_store_module.MIGRATIONS_DIRECTORY.glob(
                "[0-9][0-9][0-9][0-9]_*.sql"
            )
        ):
            if int(migration.name.split("_", 1)[0]) <= 20:
                shutil.copy2(migration, legacy_migrations / migration.name)

        self.database_path = Path(self.temporary.name) / "nonempty-v20.sqlite3"
        with mock.patch.object(
            task_store_module,
            "MIGRATIONS_DIRECTORY",
            legacy_migrations,
        ):
            self.store = SQLiteTaskStore(self.database_path)
        self.assertEqual(self.store.migration_version(), 20)

        # Current admission code needs the v21 Task-run anchor to identify the
        # first intent.  Give this historical fixture a temporary bridge while
        # exercising the real v20 admission/dispatch/terminal tables, then
        # remove it before reopening so the source schema is exactly v20.
        connection = sqlite3.connect(self.database_path, isolation_level=None)
        try:
            connection.execute(
                """
                CREATE TABLE dag_task_execution_runs (
                    task_id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    plan_hash TEXT NOT NULL,
                    approval_id TEXT NOT NULL,
                    first_intent_id TEXT NOT NULL UNIQUE,
                    project_id TEXT NOT NULL,
                    principal_id TEXT NOT NULL,
                    admitted_at TEXT NOT NULL,
                    admitted_at_us INTEGER NOT NULL
                )
                """
            )
        finally:
            connection.close()

        self.registry = RegistryService(self.store, clock=lambda: NOW)
        self.registry.register_dataset(dataset=dataset_ref())
        self.registry.register_algorithm(manifest=load_deepwave_manifest("1.6.0"))
        self.dispatcher = ControlledDagDispatcher(self.store)
        self.service = TaskService(
            self.store,
            task_id_factory=lambda: "task-dag-execution-v20-upgrade",
            clock=lambda: RUNTIME_NOW,
            dispatcher=self.dispatcher,
        )
        task_id, plan, approval, lease, binding = self._bound_root(
            "v20-upgrade"
        )
        admission = self._admit(task_id, plan, binding, lease)
        self.service.schedule_runtime_dispatch(
            task_id, supervisor_lease=lease, **self.scope
        )
        self.dispatcher.adapter_status = {
            "status": "Running",
            "stage": "inversion",
            "completed": 1,
            "total": 2,
            "message": "iteration 1 of 2",
            "updated_at": "2026-07-15T03:00:01Z",
            "terminal": False,
        }
        self.service.refresh_runtime_status(
            task_id, supervisor_lease=lease, **self.scope
        )
        self.dispatcher.manifests = self._artifact_manifests(task_id)
        self._project_terminal_heartbeat(task_id, lease, "succeeded")
        self.dispatcher.adapter_status = {
            "status": "Succeeded",
            "stage": "complete",
            "completed": 2,
            "total": 2,
            "message": "complete",
            "updated_at": "2026-07-15T03:00:02Z",
            "terminal": True,
        }
        completed = self.service.refresh_runtime_status(
            task_id, supervisor_lease=lease, **self.scope
        )
        self.assertEqual(completed.snapshot.status, "Running")
        self.assertEqual(self._node_states(task_id)["root"], (4, "Succeeded"))

        preserved_tables = (
            "dispatch_intents",
            "dag_node_execution_admissions",
            "dag_node_terminal_facts",
        )
        legacy_intent = self.store.get_dispatch_intent_by_id(
            admission.intent.intent_id
        )
        self.assertIsNotNone(legacy_intent)
        connection = sqlite3.connect(self.database_path, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            before = {
                table: [
                    dict(row)
                    for row in connection.execute(
                        f"SELECT * FROM {table} WHERE task_id = ? ORDER BY rowid",
                        (task_id,),
                    ).fetchall()
                ]
                for table in preserved_tables
            }
            transitions_before = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM dag_node_execution_transition_facts "
                    "WHERE intent_id = ? ORDER BY node_revision",
                    (admission.intent.intent_id,),
                ).fetchall()
            ]
            self.assertTrue(all(len(rows) == 1 for rows in before.values()))
            self.assertEqual(len(transitions_before), 3)
            receipt_before = json.loads(
                before["dag_node_terminal_facts"][0]["receipt_document_json"]
            )
            self.assertEqual(receipt_before["schema_version"], "2.0.0")
            for table in preserved_tables:
                table_sql = connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
                    (table,),
                ).fetchone()[0]
                self.assertIn("task_id TEXT NOT NULL UNIQUE", table_sql)
            connection.execute("DROP TABLE dag_task_execution_runs")
        finally:
            connection.close()

        failed_rebuild_path = Path(self.temporary.name) / "failed-v21.sqlite3"
        source = sqlite3.connect(self.database_path)
        target = sqlite3.connect(failed_rebuild_path)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        failing_migrations = Path(self.temporary.name) / "failing-v21-migrations"
        failing_migrations.mkdir(mode=0o700)
        for migration in sorted(
            task_store_module.MIGRATIONS_DIRECTORY.glob(
                "[0-9][0-9][0-9][0-9]_*.sql"
            )
        ):
            if int(migration.name.split("_", 1)[0]) <= 20:
                shutil.copy2(migration, failing_migrations / migration.name)
        v21_path = task_store_module.MIGRATIONS_DIRECTORY / (
            "0021_dag_runtime_scheduler.sql"
        )
        v21_text = v21_path.read_text(encoding="utf-8")
        rebuild_marker = "DROP TABLE dispatch_intents;\n"
        self.assertIn(rebuild_marker, v21_text)
        forced_failure = """
CREATE TEMP TABLE scientific_runtime_v21_forced_failure (
    value INTEGER NOT NULL CHECK (value = 0)
);
INSERT INTO scientific_runtime_v21_forced_failure
SELECT 1 FROM scientific_runtime_v21_dispatch_intents LIMIT 1;
DROP TABLE scientific_runtime_v21_forced_failure;
"""
        (failing_migrations / v21_path.name).write_text(
            v21_text.replace(
                rebuild_marker,
                rebuild_marker + forced_failure,
                1,
            ),
            encoding="utf-8",
        )
        with mock.patch.object(
            task_store_module,
            "MIGRATIONS_DIRECTORY",
            failing_migrations,
        ):
            with self.assertRaises(TaskStoreCorruption):
                SQLiteTaskStore(failed_rebuild_path)
        connection = sqlite3.connect(failed_rebuild_path)
        connection.row_factory = sqlite3.Row
        try:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 20)
            self.assertIsNone(
                connection.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = 21"
                ).fetchone()
            )
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            rolled_back = {
                table: [
                    dict(row)
                    for row in connection.execute(
                        f"SELECT * FROM {table} WHERE task_id = ? ORDER BY rowid",
                        (task_id,),
                    ).fetchall()
                ]
                for table in preserved_tables
            }
            self.assertEqual(rolled_back, before)
            self.assertEqual(
                [
                    dict(row)
                    for row in connection.execute(
                        "SELECT * FROM dag_node_execution_transition_facts "
                        "WHERE intent_id = ? ORDER BY node_revision",
                        (admission.intent.intent_id,),
                    ).fetchall()
                ],
                transitions_before,
            )
            self.assertIsNone(
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE name IN "
                    "('dag_task_execution_runs', "
                    "'dag_node_scheduler_transition_facts')"
                ).fetchone()
            )
        finally:
            connection.close()

        upgraded = SQLiteTaskStore(self.database_path)
        self.assertEqual(upgraded.migration_version(), 21)
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(
                [row[0] for row in connection.execute("PRAGMA quick_check")],
                ["ok"],
            )
            after = {
                table: [
                    dict(row)
                    for row in connection.execute(
                        f"SELECT * FROM {table} WHERE task_id = ? ORDER BY rowid",
                        (task_id,),
                    ).fetchall()
                ]
                for table in preserved_tables
            }
            self.assertEqual(after, before)
            self.assertEqual(
                [
                    dict(row)
                    for row in connection.execute(
                        "SELECT * FROM dag_node_execution_transition_facts "
                        "WHERE intent_id = ? ORDER BY node_revision",
                        (admission.intent.intent_id,),
                    ).fetchall()
                ],
                transitions_before,
            )
            transition_sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' "
                "AND name = 'dag_node_execution_transition_facts'"
            ).fetchone()[0]
            self.assertIn("'Cancelled'", transition_sql)
            self.assertEqual(
                connection.execute(
                    "SELECT name FROM sqlite_temp_master WHERE name LIKE "
                    "'scientific_runtime_v21_%'"
                ).fetchall(),
                [],
            )
            self.assertEqual(
                connection.execute(
                    "SELECT first_intent_id FROM dag_task_execution_runs "
                    "WHERE task_id = ?",
                    (task_id,),
                ).fetchone()[0],
                admission.intent.intent_id,
            )
        finally:
            connection.close()

        decoded = upgraded.get_dispatch_intent_by_id(admission.intent.intent_id)
        self.assertEqual(decoded, legacy_intent)
        state = upgraded.get_dag_node_state_snapshot(
            task_id=task_id, **self.scope
        )
        self.assertEqual(
            {node.node_id: (node.revision, node.state) for node in state.nodes},
            {"root": (4, "Succeeded"), "spare": (1, "Pending")},
        )

        self.store = upgraded
        self.dispatcher.store = upgraded
        self.service = TaskService(
            upgraded,
            task_id_factory=lambda: "unused-after-v21-upgrade",
            clock=lambda: RUNTIME_NOW,
            dispatcher=self.dispatcher,
        )
        advanced = self.service.advance_runtime_dag(
            task_id,
            supervisor_lease=lease,
            **self.scope,
        )
        self.assertEqual(advanced.admitted_node_id, "spare")
        self.assertEqual(advanced.snapshot.status, "Running")
        self.assertEqual(
            upgraded.get_dispatch_intent_by_id(admission.intent.intent_id),
            legacy_intent,
        )
        self.assertEqual(
            len(upgraded.list_dispatch_intents(task_id)),
            2,
        )

    def test_dag_timeout_checkpoint_and_private_receipt_controls_fail_closed(
        self,
    ) -> None:
        task_id, plan, _, lease, binding = self._bound_root("controls")
        admission = self._admit(task_id, plan, binding, lease)
        with self.assertRaisesRegex(
            TaskStoreConflict, "requires a managed Worker receipt"
        ):
            self.store.record_supervised_private_receipt_adoption(
                intent_id=admission.intent.intent_id,
                handle={},
                private_schema_version="1.0.0",
                receipt_record_hash="sha256:" + "d" * 64,
                supervisor_lease=lease,
                supervisor_clock=lambda: RUNTIME_NOW,
            )
        scheduled = self.service.schedule_runtime_dispatch(
            task_id, supervisor_lease=lease, **self.scope
        )
        self.assertFalse(scheduled.timeout_armed)
        connection = sqlite3.connect(self.database_path)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM worker_attempt_timeout_windows "
                    "WHERE task_id = ?",
                    (task_id,),
                ).fetchone()[0],
                0,
            )
        finally:
            connection.close()

        self.dispatcher.adapter_status = {
            "status": "Running",
            "stage": "inversion",
            "completed": 1,
            "total": 2,
            "message": "iteration 1 of 2",
            "updated_at": "2026-07-15T03:00:01Z",
            "terminal": False,
        }
        running = self.service.refresh_runtime_status(
            task_id, supervisor_lease=lease, **self.scope
        )
        self.assertEqual(running.snapshot.status, "Running")
        self.assertEqual(self._node_states(task_id)["root"], (3, "Running"))

        intent = self.store.get_dispatch_intent(task_id)
        current = self.dispatcher.worker_observation["evidence"]
        self.dispatcher.worker_observation = {
            "evidence": managed_worker_evidence(
                attempt_id=current["attempt_id"],
                attempt_number=current["attempt_number"],
                job_id=current["job_id"],
                heartbeat_sequence=current["heartbeat"]["sequence"] + 1,
                heartbeat_state="waiting",
            ),
            "handle": copy.deepcopy(intent.handle),
        }
        with self.assertRaisesRegex(
            TaskDispatchError, "DAG_CHECKPOINT_UNSUPPORTED"
        ):
            self.service.project_worker_attempt(
                task_id, supervisor_lease=lease, **self.scope
            )
        self.assertEqual(
            self.service.get_task(task_id, **self.scope).status, "Running"
        )
        self.assertEqual(self._node_states(task_id)["root"], (3, "Running"))

        checkpoint_proof = checkpoint_wait_proof(
            task_id, self.dispatcher.worker_observation
        )
        checkpoint_proof["node_id"] = "root"
        checkpoint_proof["proof_hash"] = encode_document(
            {
                key: value
                for key, value in checkpoint_proof.items()
                if key != "proof_hash"
            }
        )[1]
        first_sequence = self.store.latest_run_event_sequence(task_id) + 1
        checkpoint_event, waiting_event = self.service._checkpoint_events(
            snapshot=running.snapshot,
            intent=intent,
            proof=checkpoint_proof,
            first_sequence=first_sequence,
        )
        with self.assertRaisesRegex(
            TaskStoreConflict, "checkpoint wait conflicts with durable state"
        ):
            self.store.record_supervised_checkpoint_wait(
                intent_id=intent.intent_id,
                checkpoint_proof=checkpoint_proof,
                checkpoint_event=checkpoint_event,
                waiting_event=waiting_event,
                supervisor_lease=lease,
                supervisor_clock=lambda: RUNTIME_NOW,
            )
        connection = sqlite3.connect(self.database_path)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM worker_checkpoint_waits WHERE task_id = ?",
                    (task_id,),
                ).fetchone()[0],
                0,
            )
        finally:
            connection.close()
        self.assertEqual(
            self.service.get_task(task_id, **self.scope).status, "Running"
        )
        self.assertEqual(self._node_states(task_id)["root"], (3, "Running"))

    def test_stale_term_reapproval_and_binding_tamper_fail_closed(self) -> None:
        stale_id, stale_plan, stale_approval, stale_lease, stale_binding = (
            self._bound_root("stale")
        )
        self.service.release_runtime_supervisor_lease(stale_lease)
        successor = self.service.acquire_runtime_supervisor_lease(
            owner_id="dag-execution-stale-successor",
            lease_seconds=30,
            **self.scope,
        ).lease
        with self.assertRaises(TaskSupervisorLeaseLost):
            self._admit(stale_id, stale_plan, stale_binding, successor)
        self.assertEqual(self._count("dag_node_execution_admissions"), 0)
        self.assertEqual(
            self.store.get_approval_budget(
                task_id=stale_id, approval_id=stale_approval["approval_id"]
            ).tasks_used,
            0,
        )

        reapproval_id, reapproval_plan, old_approval, lease, old_binding = (
            self._bound_root("reapproval")
        )
        replacement = copy.deepcopy(old_approval)
        replacement["approval_id"] = "approval-dag-execution-reapproval-new"
        self.service.persist_approval(
            task_id=reapproval_id, approval=replacement, **self.scope
        )
        with self.assertRaises(TaskConflict):
            self._admit(reapproval_id, reapproval_plan, old_binding, lease)

        tamper_id, tamper_plan, tamper_approval, tamper_lease, binding = (
            self._bound_root("tamper")
        )
        tampered = replace(binding, binding_document_hash="sha256:" + "f" * 64)
        with self.assertRaises(TaskConflict):
            self._admit(tamper_id, tamper_plan, tampered, tamper_lease)
        self.assertEqual(self._count("dag_node_execution_admissions"), 0)
        self.assertEqual(
            self.store.get_approval_budget(
                task_id=tamper_id, approval_id=tamper_approval["approval_id"]
            ).tasks_used,
            0,
        )

    def test_stale_completion_term_writes_nothing_and_successor_converges(
        self,
    ) -> None:
        task_id, plan, _, first_lease, binding = self._bound_root(
            "terminal-takeover"
        )
        self._admit(task_id, plan, binding, first_lease)
        self.service.schedule_runtime_dispatch(
            task_id, supervisor_lease=first_lease, **self.scope
        )
        self.dispatcher.adapter_status = {
            "status": "Running",
            "stage": "inversion",
            "completed": 1,
            "total": 2,
            "message": "iteration 1 of 2",
            "updated_at": "2026-07-15T03:00:01Z",
            "terminal": False,
        }
        self.service.refresh_runtime_status(
            task_id, supervisor_lease=first_lease, **self.scope
        )
        self.assertEqual(self._node_states(task_id)["root"], (3, "Running"))

        self.service.release_runtime_supervisor_lease(first_lease)
        successor = self.service.acquire_runtime_supervisor_lease(
            owner_id="dag-execution-terminal-successor",
            lease_seconds=30,
            **self.scope,
        ).lease
        self.dispatcher.manifests = self._artifact_manifests(task_id)
        self._project_terminal_heartbeat(task_id, successor, "succeeded")
        self.dispatcher.adapter_status = {
            "status": "Succeeded",
            "stage": "complete",
            "completed": 2,
            "total": 2,
            "message": "complete",
            "updated_at": "2026-07-15T03:00:02Z",
            "terminal": True,
        }
        event_count = self._count("run_events", task_id=task_id)
        with self.assertRaises(TaskSupervisorLeaseLost):
            self.service.refresh_runtime_status(
                task_id, supervisor_lease=first_lease, **self.scope
            )
        self.assertEqual(self._count("dag_node_terminal_facts"), 0)
        self.assertEqual(self._count("run_events", task_id=task_id), event_count)
        self.assertEqual(self._node_states(task_id)["root"], (3, "Running"))

        succeeded = self.service.refresh_runtime_status(
            task_id, supervisor_lease=successor, **self.scope
        )
        self.assertEqual(succeeded.snapshot.status, "Running")
        self.assertEqual(self._node_states(task_id)["root"], (4, "Succeeded"))
        connection = sqlite3.connect(self.database_path)
        try:
            receipt = json.loads(
                connection.execute(
                    "SELECT receipt_document_json FROM dag_node_terminal_facts "
                    "WHERE task_id = ?",
                    (task_id,),
                ).fetchone()[0]
            )
        finally:
            connection.close()
        self.assertEqual(
            receipt["input_supervisor_term"]["fencing_token"],
            first_lease.fencing_token,
        )
        self.assertEqual(
            receipt["completion_supervisor_term"]["fencing_token"],
            successor.fencing_token,
        )

    def test_exact_negative_reconciliation_fails_node_without_launch(self) -> None:
        task_id, plan, approval, lease, binding = self._bound_root(
            "exact-negative"
        )
        admission = self._admit(task_id, plan, binding, lease)
        self.dispatcher.failure_code = "WORKER_LAUNCH_FAILED"
        scheduled = self.service.schedule_runtime_dispatch(
            task_id, supervisor_lease=lease, **self.scope
        )
        self.assertEqual(scheduled.intent.state, "reconciliation_required")
        self.assertEqual(self._node_states(task_id)["root"], (2, "Queued"))

        evidence = managed_worker_evidence(ticket_state="failed")
        private_record_hash = "sha256:" + "c" * 64
        evidence_hash = encode_document(evidence)[1]
        private_proof_hash = encode_document(
            {
                "schema_version": "1.0.0",
                "result": "not_dispatched",
                "evidence_kind": "managed_pre_running_failure",
                "adapter_version": self.dispatcher.adapter_version,
                "private_schema_version": "1.2.0",
                "private_record_hash": private_record_hash,
                "attempt_id": evidence["attempt_id"],
                "attempt_number": 1,
                "evidence_hash": evidence_hash,
            }
        )[1]
        self.dispatcher.reconciliation_probe_result = DispatchNotStartedProof(
            result="not_dispatched",
            evidence_kind="managed_pre_running_failure",
            adapter_version=self.dispatcher.adapter_version,
            private_schema_version="1.2.0",
            private_record_hash=private_record_hash,
            private_proof_hash=private_proof_hash,
            attempt_id=evidence["attempt_id"],
            attempt_number=1,
            evidence=evidence,
        )
        resolved = self.service.reconcile_runtime_dispatch(
            task_id, supervisor_lease=lease, **self.scope
        )
        replayed = self.service.reconcile_runtime_dispatch(
            task_id, supervisor_lease=lease, **self.scope
        )
        self.assertEqual((resolved.intent.state, replayed.intent.state),
                         ("not_dispatched", "not_dispatched"))
        self.assertEqual(self._node_states(task_id)["root"], (3, "Failed"))
        self.assertEqual(
            self.service.get_task(task_id, **self.scope).status, "Running"
        )
        self.assertEqual(self._count("worker_launch_attempts", task_id=task_id), 0)
        self.assertEqual(self._count("dag_node_terminal_facts"), 1)
        self.assertEqual(
            self.store.get_approval_budget(
                task_id=task_id, approval_id=approval["approval_id"]
            ).tasks_used,
            1,
        )
        connection = sqlite3.connect(self.database_path)
        try:
            terminal = connection.execute(
                "SELECT attempt_id, attempt_number, node_state "
                "FROM dag_node_terminal_facts WHERE intent_id = ?",
                (admission.intent.intent_id,),
            ).fetchone()
            transition_reason = connection.execute(
                "SELECT reason FROM dag_node_execution_transition_facts "
                "WHERE intent_id = ? ORDER BY node_revision DESC LIMIT 1",
                (admission.intent.intent_id,),
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(terminal, (None, None, "Failed"))
        self.assertEqual(transition_reason, "dispatch_not_started")

    def test_failed_terminal_is_append_only_and_attempt_two_is_blocked(self) -> None:
        task_id, plan, _, lease, binding = self._bound_root("failed")
        admission = self._admit(task_id, plan, binding, lease)
        self.service.schedule_runtime_dispatch(
            task_id, supervisor_lease=lease, **self.scope
        )
        self._project_terminal_heartbeat(task_id, lease, "failed")
        self.dispatcher.adapter_status = {
            "status": "Failed",
            "stage": "failed",
            "completed": 0,
            "total": 2,
            "message": "controlled worker failure",
            "updated_at": "2026-07-15T03:00:01Z",
            "terminal": True,
        }
        failed = self.service.refresh_runtime_status(
            task_id, supervisor_lease=lease, **self.scope
        )
        self.assertEqual(failed.snapshot.status, "Running")
        self.assertEqual(self._node_states(task_id)["root"], (4, "Failed"))
        self.assertEqual(
            [
                value["event_type"]
                for value in self.service.list_run_events(task_id, **self.scope)
            ],
            ["task_queued", "node_started", "node_failed"],
        )
        self.assertEqual(self._count("dag_node_terminal_facts"), 1)

        connection = sqlite3.connect(self.database_path)
        try:
            terminal = connection.execute(
                """
                SELECT node_state, receipt_document_json, receipt_document_hash
                FROM dag_node_terminal_facts WHERE intent_id = ?
                """,
                (admission.intent.intent_id,),
            ).fetchone()
            self.assertEqual(terminal, ("Failed", None, None))
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "DAG node execution permits exactly one launch attempt",
            ):
                connection.execute(
                    """
                    INSERT INTO worker_launch_attempts(
                        attempt_id, intent_id, task_id, project_id, principal_id,
                        attempt_number, submission_id, job_id,
                        adapter_request_hash, binding_hash, created_at,
                        first_fencing_token, first_observed_at, first_observed_at_us
                    )
                    SELECT ?, intent_id, task_id, project_id, principal_id,
                           2, submission_id, job_id, adapter_request_hash,
                           binding_hash, created_at, first_fencing_token,
                           first_observed_at, first_observed_at_us
                    FROM worker_launch_attempts WHERE intent_id = ?
                    """,
                    ("attempt-" + "e" * 32, admission.intent.intent_id),
                )
        finally:
            connection.rollback()
            connection.close()

        with self.assertRaises(sqlite3.IntegrityError):
            connection = sqlite3.connect(self.database_path)
            try:
                connection.execute(
                    "UPDATE dag_node_terminal_facts SET node_state = 'Succeeded'"
                )
            finally:
                connection.rollback()
                connection.close()

    def test_worker_exit_fails_node_without_minting_attempt_two(self) -> None:
        task_id, plan, _, lease, binding = self._bound_root("worker-exit")
        self._admit(task_id, plan, binding, lease)
        self.service.schedule_runtime_dispatch(
            task_id, supervisor_lease=lease, **self.scope
        )
        self.dispatcher.adapter_status = {
            "status": "Running",
            "stage": "inversion",
            "completed": 1,
            "total": 2,
            "message": "iteration 1 of 2",
            "updated_at": "2026-07-15T03:00:01Z",
            "terminal": False,
        }
        running = self.service.refresh_runtime_status(
            task_id, supervisor_lease=lease, **self.scope
        )
        self.assertEqual(running.snapshot.status, "Running")
        self.assertEqual(self._node_states(task_id)["root"], (3, "Running"))

        self.dispatcher.adapter_status = {
            "status": "Failed",
            "stage": "worker_exit",
            "completed": 1,
            "total": 2,
            "message": "controlled Worker exit after ready",
            "updated_at": "2026-07-15T03:00:02Z",
            "terminal": True,
        }
        failed = self.service.process_runtime_retry(
            task_id, supervisor_lease=lease, **self.scope
        )
        replayed = self.service.process_runtime_retry(
            task_id, supervisor_lease=lease, **self.scope
        )
        self.assertEqual((failed.snapshot.status, replayed.snapshot.status),
                         ("Running", "Running"))
        self.assertEqual(self._node_states(task_id)["root"], (4, "Failed"))
        self.assertEqual(self.dispatcher.worker_exit_probe_calls, 1)
        connection = sqlite3.connect(self.database_path)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM worker_launch_attempts WHERE task_id = ?",
                    (task_id,),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM worker_retry_reservations WHERE task_id = ?",
                    (task_id,),
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM worker_exit_retry_reservations "
                    "WHERE task_id = ?",
                    (task_id,),
                ).fetchone()[0],
                0,
            )
            reason = connection.execute(
                "SELECT reason FROM dag_node_execution_transition_facts "
                "WHERE intent_id = (SELECT intent_id FROM dispatch_intents "
                "WHERE task_id = ?) ORDER BY node_revision DESC LIMIT 1",
                (task_id,),
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(reason, "worker_exit_no_retry")

    def test_receipt_attempt_divergence_cannot_publish_success(self) -> None:
        task_id, plan, _, lease, binding = self._bound_root("receipt-tamper")
        self._admit(task_id, plan, binding, lease)
        self.service.schedule_runtime_dispatch(
            task_id, supervisor_lease=lease, **self.scope
        )
        self.dispatcher.manifests = self._artifact_manifests(task_id)
        self._project_terminal_heartbeat(task_id, lease, "succeeded")
        self.dispatcher.output_attempt_id = "attempt-" + "f" * 32
        self.dispatcher.adapter_status = {
            "status": "Succeeded",
            "stage": "complete",
            "completed": 2,
            "total": 2,
            "message": "complete",
            "updated_at": "2026-07-15T03:00:01Z",
            "terminal": True,
        }
        # Direct Adapter success first commits node_started, then reaches the
        # terminal proof in the same convergence pass.  The divergent attempt
        # must prevent that second transition from becoming SQLite truth.
        with self.assertRaises((TaskConflict, TaskDispatchError)):
            self.service.refresh_runtime_status(
                task_id, supervisor_lease=lease, **self.scope
            )
        self.assertEqual(self._node_states(task_id)["root"], (3, "Running"))
        self.assertEqual(self._count("dag_node_terminal_facts"), 0)


if __name__ == "__main__":
    unittest.main()
