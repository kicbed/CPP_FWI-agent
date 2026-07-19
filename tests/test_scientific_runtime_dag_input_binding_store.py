from __future__ import annotations

import copy
import hashlib
import shutil
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import scientific_runtime.task_store as task_store_module

from scientific_runtime import (
    DagNodeArtifactInput,
    RegistryService,
    SQLiteTaskStore,
    TaskConflict,
    TaskService,
    TaskSupervisorLeaseLost,
    load_deepwave_manifest,
)
from scientific_runtime.task_store import encode_document
from scientific_runtime_contracts import compute_plan_hash
from tests.test_scientific_runtime_contracts import (
    approval_decision,
    artifact_manifest,
    dataset_ref,
    optimizer_plan_graph,
    optimizer_task_draft,
)


NOW = "2026-07-15T02:30:00Z"
PROJECT_ID = "project-1"
PRINCIPAL_ID = "user-1"
ARTIFACT_DATA = b"verified-upstream-velocity-model"


class ScientificRuntimeDagInputBindingStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary.name) / "task.sqlite3"
        self.store = SQLiteTaskStore(self.database_path)
        self.clock_value = NOW
        self.next_task = 0
        self.registry = RegistryService(
            self.store, clock=lambda: self.clock_value
        )
        self.registry.register_dataset(dataset=dataset_ref())
        self.algorithm = copy.deepcopy(load_deepwave_manifest("1.5.0"))
        self.algorithm["id"] = "test.velocity_passthrough"
        self.algorithm["outputs"] = [
            {"port": "prepared_model", "data_type": "velocity_model_2d"}
        ]
        self.registry.register_algorithm(manifest=self.algorithm)

        def task_id_factory() -> str:
            self.next_task += 1
            return f"task-{self.next_task:03d}"

        self.service = TaskService(
            self.store,
            task_id_factory=task_id_factory,
            clock=lambda: self.clock_value,
        )
        self.scope = {
            "project_id": PROJECT_ID,
            "principal_id": PRINCIPAL_ID,
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _timestamp_us(value: str) -> int:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        parsed = parsed.astimezone(timezone.utc)
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        delta = parsed - epoch
        return (
            delta.days * 86_400_000_000
            + delta.seconds * 1_000_000
            + delta.microseconds
        )

    def _count(self, table: str, *, task_id: str | None = None) -> int:
        self.assertIn(
            table,
            {
                "dag_node_state_events",
                "dag_node_claim_candidates",
                "dag_node_input_binding_facts",
                "dag_node_succeeded_outputs",
                "dispatch_intents",
                "run_events",
            },
        )
        connection = sqlite3.connect(self.database_path)
        try:
            if task_id is None:
                row = connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()
            else:
                row = connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
            return int(row[0])
        finally:
            connection.close()

    def _approved_typed_plan(self) -> tuple[str, dict, dict]:
        sequence = self.next_task + 1
        draft = optimizer_task_draft(algorithm_version="1.5.0")
        draft["draft_id"] = f"draft-dag-binding-{sequence:03d}"
        draft["algorithm"]["id"] = self.algorithm["id"]
        created = self.service.create_task(
            draft=draft,
            idempotency_key=f"create-dag-binding-{sequence:03d}",
            **self.scope,
        )
        task_id = created.snapshot.task_id

        plan = optimizer_plan_graph(algorithm_version="1.5.0")
        plan["schema_version"] = "1.2.0"
        plan["plan_id"] = f"plan-dag-binding-{sequence:03d}"
        plan["draft"] = {
            "draft_id": created.snapshot.draft["draft_id"],
            "revision": created.snapshot.draft["revision"],
        }
        source = copy.deepcopy(plan["nodes"][0])
        source["node_id"] = "prepare"
        source["algorithm"] = {
            "id": self.algorithm["id"],
            "version": self.algorithm["version"],
        }
        source["outputs"] = copy.deepcopy(self.algorithm["outputs"])
        source["idempotency_key"] = f"{task_id}:prepare:0001"
        source["dependencies"] = []
        target = copy.deepcopy(source)
        target["node_id"] = "invert"
        target["idempotency_key"] = f"{task_id}:invert:0001"
        target["dependencies"] = ["prepare"]
        target["inputs"] = [
            {
                "port": "model",
                "source": {
                    "node_id": "prepare",
                    "port": "prepared_model",
                    "data_type": "velocity_model_2d",
                },
            }
        ]
        plan["nodes"] = [source, target]
        plan["plan_hash"] = compute_plan_hash(plan)
        self.service.persist_plan(task_id=task_id, plan=plan, **self.scope)

        approval = approval_decision(plan)
        approval["approval_id"] = f"approval-dag-binding-{sequence:03d}"
        approval["scope"]["algorithms"] = [
            {"id": self.algorithm["id"], "version": self.algorithm["version"]}
        ]
        self.service.persist_approval(
            task_id=task_id, approval=approval, **self.scope
        )
        return task_id, plan, approval

    def _acquire(self, owner_id: str):
        return self.service.acquire_runtime_supervisor_lease(
            owner_id=owner_id, lease_seconds=30, **self.scope
        ).lease

    def _claim(self, task_id: str, plan: dict, lease):
        return self.service.claim_ready_dag_node_candidate(
            task_id,
            expected_plan_hash=plan["plan_hash"],
            supervisor_lease=lease,
            **self.scope,
        )

    def _bind(self, task_id: str, plan: dict, lease, claim, materials=()):
        return self.service.bind_ready_dag_node_inputs(
            task_id,
            expected_plan_hash=plan["plan_hash"],
            claim_candidate=claim,
            artifact_inputs=materials,
            supervisor_lease=lease,
            **self.scope,
        )

    def _artifact(self, task_id: str, plan: dict) -> dict:
        value = artifact_manifest(plan)
        source = plan["nodes"][0]
        value.update(
            artifact_id=f"artifact-{task_id}",
            task_id=task_id,
            node_id="prepare",
            artifact_type="velocity_model_2d",
            content_hash="sha256:"
            + hashlib.sha256(ARTIFACT_DATA).hexdigest(),
            size_bytes=len(ARTIFACT_DATA),
        )
        value["lineage"]["plan_hash"] = plan["plan_hash"]
        value["lineage"]["algorithm"] = copy.deepcopy(source["algorithm"])
        value["fingerprint"]["algorithm"] = copy.deepcopy(source["algorithm"])
        value["extensions"] = {
            "org.agent_rpc.adapter": {
                "output_port": "prepared_model",
                "worker_job_id": f"job-{task_id}",
            }
        }
        return value

    def _drop_transition_guard(self) -> None:
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "DROP TRIGGER IF EXISTS "
                "dag_node_state_events_are_initial_pending_only"
            )
            connection.execute(
                "DROP TRIGGER IF EXISTS "
                "dag_node_transition_state_requires_exact_active_fact"
            )
            connection.commit()
        finally:
            connection.close()

    def _append_future_success(
        self, task_id: str, plan: dict, *, succeeded_at: str
    ) -> None:
        connection = sqlite3.connect(self.database_path)
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            connection.execute(
                """
                INSERT INTO dag_node_state_events(
                    task_id, plan_id, plan_hash, node_id, revision,
                    previous_state, state, recorded_at, recorded_at_us
                ) VALUES (?, ?, ?, 'prepare', 2, 'Pending', 'Succeeded', ?, ?)
                """,
                (
                    task_id,
                    plan["plan_id"],
                    plan["plan_hash"],
                    succeeded_at,
                    self._timestamp_us(succeeded_at),
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def seeded_future_succeeded_output(
        self,
        task_id: str,
        plan: dict,
        approval: dict,
        root_binding,
        *,
        succeeded_at: str = "2026-07-15T02:30:01.000000Z",
        receipt_at: str = "2026-07-15T02:30:02.000000Z",
    ) -> dict:
        """Seed the dormant reader contract; this is not causal runtime proof."""

        self._drop_transition_guard()
        self._append_future_success(task_id, plan, succeeded_at=succeeded_at)
        artifact = self._artifact(task_id, plan)
        _, artifact_hash = encode_document(artifact)
        _, receipt_record_hash = encode_document(
            {
                "schema_version": "1.0.0",
                "fixture": "seeded_future_succeeded_output",
                "task_id": task_id,
                "node_id": "prepare",
                "node_revision": 2,
                "artifact_manifest_hash": artifact_hash,
            }
        )
        receipt = {
            "schema_version": "1.0.0",
            "task_id": task_id,
            "plan": {
                "plan_id": plan["plan_id"],
                "plan_hash": plan["plan_hash"],
            },
            "approval_id": approval["approval_id"],
            "node": {
                "node_id": "prepare",
                "input_binding_revision": root_binding.target_node_revision,
                "succeeded_revision": 2,
                "state": "Succeeded",
            },
            "scope": {
                "project_id": PROJECT_ID,
                "principal_id": PRINCIPAL_ID,
            },
            "supervisor_term": {
                "fencing_token": root_binding.fencing_token,
                "owner_id": root_binding.owner_id,
                "acquired_at": root_binding.term_acquired_at,
            },
            "input_binding_document_hash": root_binding.binding_document_hash,
            "receipt_record_hash": receipt_record_hash,
            "outputs": [
                {
                    "output_port": "prepared_model",
                    "data_type": "velocity_model_2d",
                    "artifact_manifest": copy.deepcopy(artifact),
                    "artifact_manifest_hash": artifact_hash,
                }
            ],
            "succeeded_at": receipt_at,
        }
        receipt_json, receipt_hash = encode_document(receipt)
        connection = sqlite3.connect(self.database_path)
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            connection.execute(
                """
                INSERT INTO dag_node_succeeded_outputs(
                    task_id, plan_id, plan_hash, approval_id,
                    node_id, input_binding_node_revision,
                    input_binding_document_hash, node_revision, node_state,
                    project_id, principal_id, fencing_token,
                    owner_id, term_acquired_at,
                    receipt_document_json, receipt_document_hash,
                    recorded_at, recorded_at_us
                ) VALUES (?, ?, ?, ?, 'prepare', ?, ?, 2, 'Succeeded',
                          ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    plan["plan_id"],
                    plan["plan_hash"],
                    approval["approval_id"],
                    root_binding.target_node_revision,
                    root_binding.binding_document_hash,
                    PROJECT_ID,
                    PRINCIPAL_ID,
                    root_binding.fencing_token,
                    root_binding.owner_id,
                    root_binding.term_acquired_at,
                    receipt_json,
                    receipt_hash,
                    receipt_at,
                    self._timestamp_us(receipt_at),
                ),
            )
            connection.commit()
        finally:
            connection.close()
        return {
            "artifact": artifact,
            "artifact_manifest_hash": artifact_hash,
            "receipt_document_hash": receipt_hash,
            "receipt_record_hash": receipt_record_hash,
        }

    def _assert_runtime_untouched(self, task_id: str, approval_id: str) -> None:
        snapshot = self.service.get_task(task_id, **self.scope)
        budget = self.store.get_approval_budget(
            task_id=task_id, approval_id=approval_id
        )
        self.assertEqual(snapshot.status, "AwaitingApproval")
        self.assertEqual(budget.tasks_used, 0)
        self.assertEqual(self._count("dispatch_intents", task_id=task_id), 0)
        self.assertEqual(self._count("run_events", task_id=task_id), 0)

    def test_real_v19_pending_claim_and_binding_upgrade_in_place_to_v20(
        self,
    ) -> None:
        legacy_migrations = Path(self.temporary.name) / "v19-migrations"
        legacy_migrations.mkdir(mode=0o700)
        for migration in sorted(
            task_store_module.MIGRATIONS_DIRECTORY.glob(
                "[0-9][0-9][0-9][0-9]_*.sql"
            )
        ):
            if int(migration.name.split("_", 1)[0]) <= 19:
                shutil.copy2(migration, legacy_migrations / migration.name)

        legacy_directory = Path(self.temporary.name) / "legacy-v19"
        legacy_directory.mkdir(mode=0o700)
        legacy_path = legacy_directory / "task.sqlite3"
        with mock.patch.object(
            task_store_module,
            "MIGRATIONS_DIRECTORY",
            legacy_migrations,
        ):
            self.database_path = legacy_path
            self.store = SQLiteTaskStore(legacy_path)
            self.registry = RegistryService(
                self.store, clock=lambda: self.clock_value
            )
            self.registry.register_dataset(dataset=dataset_ref())
            self.registry.register_algorithm(manifest=self.algorithm)
            self.next_task = 0
            self.service = TaskService(
                self.store,
                task_id_factory=lambda: "task-v19-binding-upgrade",
                clock=lambda: self.clock_value,
            )
            task_id, plan, approval = self._approved_typed_plan()
            lease = self._acquire("v19-binding-supervisor")
            claim = self._claim(task_id, plan, lease)
            binding = self._bind(task_id, plan, lease, claim)
            self.assertEqual(self.store.migration_version(), 19)

        upgraded = SQLiteTaskStore(legacy_path)
        self.assertEqual(upgraded.migration_version(), 23)
        upgraded_service = TaskService(upgraded, clock=lambda: self.clock_value)
        state = upgraded_service.get_dag_node_state_snapshot(
            task_id, **self.scope
        )
        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(
            [(node.node_id, node.revision, node.state) for node in state.nodes],
            [("invert", 1, "Pending"), ("prepare", 1, "Pending")],
        )

        connection = sqlite3.connect(legacy_path)
        try:
            preserved_claim = connection.execute(
                """
                SELECT node_id, node_revision, readiness_document_hash
                FROM dag_node_claim_candidates WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
            preserved_binding = connection.execute(
                """
                SELECT target_node_id, target_node_revision,
                       binding_document_hash
                FROM dag_node_input_binding_facts WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
            self.assertEqual(
                preserved_claim,
                (
                    claim.node.node_id,
                    claim.node.revision,
                    claim.readiness_document_hash,
                ),
            )
            self.assertEqual(
                preserved_binding,
                (
                    binding.target_node_id,
                    binding.target_node_revision,
                    binding.binding_document_hash,
                ),
            )
            for table in (
                "dag_node_execution_admissions",
                "dag_node_execution_transition_facts",
                "dag_node_terminal_facts",
                "dispatch_intents",
                "run_events",
                "worker_launch_attempts",
            ):
                self.assertEqual(
                    connection.execute(
                        f"SELECT COUNT(*) FROM {table}",
                    ).fetchone()[0],
                    0,
                    table,
                )
            self.assertEqual(
                connection.execute("PRAGMA foreign_key_check").fetchall(), []
            )
        finally:
            connection.close()

    def test_dataset_root_replay_and_same_term_concurrency(self) -> None:
        first_task, first_plan, first_approval = self._approved_typed_plan()
        lease = self._acquire("dag-binding-root")
        first_claim = self._claim(first_task, first_plan, lease)
        first = self._bind(first_task, first_plan, lease, first_claim)
        replay = self._bind(first_task, first_plan, lease, first_claim)

        self.assertFalse(first.replayed)
        self.assertTrue(replay.replayed)
        self.assertFalse(first.dispatch_authorized)
        self.assertEqual(replay.binding_document, first.binding_document)
        self.assertEqual(replay.binding_document_hash, first.binding_document_hash)
        _, expected_hash = encode_document(first.binding_document)
        self.assertEqual(first.binding_document_hash, expected_hash)
        self.assertEqual(first.target_node_id, "prepare")
        self.assertEqual(
            first.binding_document["inputs"],
            [
                {
                    "input_index": 0,
                    "target_input_port": "model",
                    "kind": "dataset",
                    "dataset": {
                        key: dataset_ref()[key]
                        for key in ("id", "version", "content_hash", "data_type")
                    },
                    "dataset_document_hash": first.binding_document["inputs"][0][
                        "dataset_document_hash"
                    ],
                }
            ],
        )
        self.assertEqual(
            self._count("dag_node_input_binding_facts", task_id=first_task), 1
        )
        self._assert_runtime_untouched(first_task, first_approval["approval_id"])

        second_task, second_plan, second_approval = self._approved_typed_plan()
        second_claim = self._claim(second_task, second_plan, lease)

        def bind_once(_: int):
            return self._bind(second_task, second_plan, lease, second_claim)

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(bind_once, range(8)))

        self.assertEqual(sum(not value.replayed for value in results), 1)
        self.assertEqual(sum(value.replayed for value in results), 7)
        self.assertEqual({value.binding_document_hash for value in results}, {
            results[0].binding_document_hash
        })
        self.assertEqual(
            self._count("dag_node_input_binding_facts", task_id=second_task), 1
        )
        self._assert_runtime_untouched(second_task, second_approval["approval_id"])
        self.service.release_runtime_supervisor_lease(lease)

    def test_seeded_future_receipt_binds_consumer_without_runtime_effects(
        self,
    ) -> None:
        task_id, plan, approval = self._approved_typed_plan()
        lease = self._acquire("dag-binding-seeded-reader")
        root_claim = self._claim(task_id, plan, lease)
        root_binding = self._bind(task_id, plan, lease, root_claim)
        fixture = self.seeded_future_succeeded_output(
            task_id, plan, approval, root_binding
        )

        self.clock_value = "2026-07-15T02:30:03Z"
        consumer_claim = self._claim(task_id, plan, lease)
        consumer = self._bind(
            task_id,
            plan,
            lease,
            consumer_claim,
            (
                DagNodeArtifactInput(
                    target_input_port="model",
                    artifact_manifest=fixture["artifact"],
                    artifact_data=ARTIFACT_DATA,
                ),
            ),
        )

        self.assertFalse(consumer.replayed)
        self.assertFalse(consumer.dispatch_authorized)
        self.assertEqual(consumer.target_node_id, "invert")
        self.assertEqual(len(consumer.binding_document["inputs"]), 1)
        bound = consumer.binding_document["inputs"][0]
        self.assertEqual(bound["kind"], "node_output")
        self.assertEqual(
            bound["artifact_manifest_hash"], fixture["artifact_manifest_hash"]
        )
        self.assertEqual(bound["binding"]["artifact"]["content_hash"],
                         fixture["artifact"]["content_hash"])
        self.assertEqual(bound["producer"]["succeeded_revision"], 2)
        self.assertEqual(
            bound["producer"]["input_binding_document_hash"],
            root_binding.binding_document_hash,
        )
        self.assertEqual(
            bound["producer"]["receipt_document_hash"],
            fixture["receipt_document_hash"],
        )
        self.assertEqual(
            bound["producer"]["receipt_record_hash"],
            fixture["receipt_record_hash"],
        )
        _, expected_hash = encode_document(consumer.binding_document)
        self.assertEqual(consumer.binding_document_hash, expected_hash)
        self.assertEqual(
            self._count("dag_node_input_binding_facts", task_id=task_id), 2
        )
        self.assertEqual(
            self._count("dag_node_succeeded_outputs", task_id=task_id), 1
        )
        self._assert_runtime_untouched(task_id, approval["approval_id"])
        self.service.release_runtime_supervisor_lease(lease)

    def test_failures_rollback_and_reserved_facts_are_append_only(self) -> None:
        task_id, plan, approval = self._approved_typed_plan()
        first_lease = self._acquire("dag-binding-failures-first")
        first_claim = self._claim(task_id, plan, first_lease)
        artifact = self._artifact(task_id, plan)

        with self.assertRaises(TaskConflict):
            self._bind(
                task_id,
                plan,
                first_lease,
                first_claim,
                (DagNodeArtifactInput("extra", artifact, ARTIFACT_DATA),),
            )
        self.assertEqual(
            self._count("dag_node_input_binding_facts", task_id=task_id), 0
        )

        self.service.release_runtime_supervisor_lease(first_lease)
        with self.assertRaises(TaskSupervisorLeaseLost):
            self._bind(task_id, plan, first_lease, first_claim)
        self.assertEqual(
            self._count("dag_node_input_binding_facts", task_id=task_id), 0
        )

        lease = self._acquire("dag-binding-failures-successor")
        root_claim = self._claim(task_id, plan, lease)
        root_binding = self._bind(task_id, plan, lease, root_claim)
        fixture = self.seeded_future_succeeded_output(
            task_id, plan, approval, root_binding
        )
        self.clock_value = "2026-07-15T02:30:03Z"
        consumer_claim = self._claim(task_id, plan, lease)
        with self.assertRaises(TaskConflict):
            self._bind(task_id, plan, lease, consumer_claim)
        with self.assertRaises(TaskConflict):
            self._bind(
                task_id,
                plan,
                lease,
                consumer_claim,
                (
                    DagNodeArtifactInput(
                        "model",
                        fixture["artifact"],
                        ARTIFACT_DATA + b"-tampered",
                    ),
                ),
            )
        self.assertEqual(
            self._count("dag_node_input_binding_facts", task_id=task_id), 1
        )

        missing_task, missing_plan, missing_approval = self._approved_typed_plan()
        missing_claim = self._claim(missing_task, missing_plan, lease)
        missing_root = self._bind(
            missing_task, missing_plan, lease, missing_claim
        )
        self._append_future_success(
            missing_task,
            missing_plan,
            succeeded_at="2026-07-15T02:30:04.000000Z",
        )
        self.clock_value = "2026-07-15T02:30:05Z"
        missing_consumer_claim = self._claim(missing_task, missing_plan, lease)
        with self.assertRaises(TaskConflict):
            self._bind(
                missing_task,
                missing_plan,
                lease,
                missing_consumer_claim,
                (
                    DagNodeArtifactInput(
                        "model",
                        self._artifact(missing_task, missing_plan),
                        ARTIFACT_DATA,
                    ),
                ),
            )
        self.assertFalse(missing_root.dispatch_authorized)
        self.assertEqual(
            self._count("dag_node_input_binding_facts", task_id=missing_task), 1
        )
        self.assertEqual(
            self._count("dag_node_succeeded_outputs", task_id=missing_task), 0
        )

        for table in (
            "dag_node_input_binding_facts",
            "dag_node_succeeded_outputs",
        ):
            for operation in ("UPDATE", "DELETE"):
                connection = sqlite3.connect(self.database_path)
                try:
                    statement = (
                        f"UPDATE {table} SET recorded_at = recorded_at"
                        if operation == "UPDATE"
                        else f"DELETE FROM {table}"
                    )
                    with self.assertRaisesRegex(
                        sqlite3.IntegrityError, "append-only"
                    ):
                        connection.execute(statement)
                    connection.rollback()
                finally:
                    connection.close()

        self.assertEqual(
            self._count("dag_node_input_binding_facts", task_id=task_id), 1
        )
        self.assertEqual(
            self._count("dag_node_succeeded_outputs", task_id=task_id), 1
        )
        self._assert_runtime_untouched(task_id, approval["approval_id"])
        self._assert_runtime_untouched(
            missing_task, missing_approval["approval_id"]
        )
        self.service.release_runtime_supervisor_lease(lease)


if __name__ == "__main__":
    unittest.main()
