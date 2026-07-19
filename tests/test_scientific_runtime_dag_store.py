from __future__ import annotations

import json
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
    RegistryService,
    SQLiteTaskStore,
    TaskConflict,
    TaskService,
    TaskStoreCorruption,
    TaskSupervisorLeaseLost,
)
from scientific_runtime_contracts import compute_plan_hash
from tests.test_scientific_runtime_contracts import (
    algorithm_manifest,
    append_second_plan_node,
    approval_decision,
    dataset_ref,
    plan_graph,
    task_draft,
)


NOW = "2026-07-15T02:30:00Z"
AFTER_EXPIRY = "2026-07-15T02:31:00Z"
PROJECT_ID = "project-1"
PRINCIPAL_ID = "user-1"


class ScientificRuntimeDagStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary.name) / "task.sqlite3"
        self.store = SQLiteTaskStore(self.database_path)
        self.clock_value = NOW
        self.next_task = 0
        self.registry = RegistryService(self.store, clock=lambda: self.clock_value)
        self.registry.register_dataset(dataset=dataset_ref())
        self.registry.register_algorithm(manifest=algorithm_manifest())

        def task_id_factory() -> str:
            self.next_task += 1
            return f"task-dag-{self.next_task:04d}"

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

    def count(self, table: str) -> int:
        self.assertIn(
            table,
            {
                "dag_node_state_events",
                "dag_node_claim_candidates",
                "dispatch_intents",
                "run_events",
            },
        )
        connection = sqlite3.connect(self.database_path)
        try:
            return int(
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            )
        finally:
            connection.close()

    def approved_dag(self, *, dependent: bool = True) -> tuple[str, dict]:
        draft = task_draft()
        draft["draft_id"] = f"draft-dag-{self.next_task + 1:04d}"
        created = self.service.create_task(
            draft=draft,
            idempotency_key=f"create-dag-{self.next_task + 1}",
            **self.scope,
        )
        plan = plan_graph()
        plan["plan_id"] = f"plan-dag-{self.next_task:04d}"
        plan["draft"] = {
            "draft_id": created.snapshot.draft["draft_id"],
            "revision": created.snapshot.draft["revision"],
        }
        second = append_second_plan_node(plan)
        if dependent:
            second["dependencies"] = ["invert"]
        plan["plan_hash"] = compute_plan_hash(plan)
        self.service.persist_plan(
            task_id=created.snapshot.task_id,
            plan=plan,
            **self.scope,
        )
        approval = approval_decision(plan)
        approval["approval_id"] = f"approval-dag-{self.next_task:04d}"
        self.service.persist_approval(
            task_id=created.snapshot.task_id,
            approval=approval,
            **self.scope,
        )
        return created.snapshot.task_id, plan

    def acquire(self, owner_id: str = "dag-supervisor"):
        return self.service.acquire_runtime_supervisor_lease(
            owner_id=owner_id,
            lease_seconds=30,
            **self.scope,
        ).lease

    def claim(self, task_id: str, plan: dict, lease):
        return self.service.claim_ready_dag_node_candidate(
            task_id,
            expected_plan_hash=plan["plan_hash"],
            supervisor_lease=lease,
            **self.scope,
        )

    def test_claim_initializes_exact_pending_map_without_runtime_side_effects(
        self,
    ) -> None:
        task_id, plan = self.approved_dag()
        self.assertIsNone(
            self.service.get_dag_node_state_snapshot(task_id, **self.scope)
        )
        budget = self.store.get_approval_budget(
            task_id=task_id,
            approval_id=f"approval-dag-{self.next_task:04d}",
        )
        self.assertEqual((budget.tasks_used, budget.max_tasks), (0, 1))

        lease = self.acquire()
        claimed = self.claim(task_id, plan, lease)

        self.assertFalse(claimed.replayed)
        self.assertFalse(claimed.dispatch_authorized)
        self.assertEqual(claimed.node.node_id, "invert")
        self.assertEqual((claimed.node.revision, claimed.node.state), (1, "Pending"))
        state_map = self.service.get_dag_node_state_snapshot(
            task_id, **self.scope
        )
        self.assertEqual(
            [(node.node_id, node.revision, node.state) for node in state_map.nodes],
            [
                ("invert", 1, "Pending"),
                ("invert-second", 1, "Pending"),
            ],
        )
        self.assertEqual(self.store.get_task(task_id).status, "AwaitingApproval")
        self.assertEqual(self.count("dag_node_state_events"), 2)
        self.assertEqual(self.count("dag_node_claim_candidates"), 1)
        self.assertEqual(self.count("dispatch_intents"), 0)
        self.assertEqual(self.count("run_events"), 0)
        self.assertEqual(
            self.store.get_approval_budget(
                task_id=task_id,
                approval_id=f"approval-dag-{self.next_task:04d}",
            ).tasks_used,
            0,
        )

        connection = sqlite3.connect(self.database_path)
        try:
            row = connection.execute(
                """
                SELECT readiness_document_json
                FROM dag_node_claim_candidates
                """
            ).fetchone()
        finally:
            connection.close()
        readiness = json.loads(row[0])
        self.assertEqual(readiness["selected_node_id"], "invert")
        self.assertEqual(readiness["runnable_node_ids"], ["invert"])
        self.assertEqual(readiness["waiting_node_ids"], ["invert-second"])
        self.service.release_runtime_supervisor_lease(lease)

    def test_same_term_replay_and_concurrency_converge_to_one_candidate(self) -> None:
        task_id, plan = self.approved_dag(dependent=False)
        lease = self.acquire(owner_id="dag-concurrent")

        def claim_once(_: int):
            return self.store.claim_ready_dag_node_candidate(
                task_id=task_id,
                expected_plan_hash=plan["plan_hash"],
                supervisor_lease=lease,
                supervisor_clock=lambda: "2026-07-15T02:30:00.000000Z",
                **self.scope,
            )

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(claim_once, range(8)))

        self.assertEqual(sum(not result.replayed for result in results), 1)
        self.assertEqual(sum(result.replayed for result in results), 7)
        self.assertEqual({result.node.node_id for result in results}, {"invert"})
        self.assertEqual({result.fencing_token for result in results}, {1})
        self.assertEqual(self.count("dag_node_state_events"), 2)
        self.assertEqual(self.count("dag_node_claim_candidates"), 1)
        replay = self.claim(task_id, plan, lease)
        self.assertTrue(replay.replayed)
        self.assertEqual(self.count("dag_node_claim_candidates"), 1)
        self.service.release_runtime_supervisor_lease(lease)

    def test_successor_term_may_reaudit_same_pending_candidate(self) -> None:
        task_id, plan = self.approved_dag()
        first_lease = self.acquire(owner_id="dag-first")
        first = self.claim(task_id, plan, first_lease)
        self.service.release_runtime_supervisor_lease(first_lease)

        second_lease = self.acquire(owner_id="dag-second")
        second = self.claim(task_id, plan, second_lease)

        self.assertEqual((first.fencing_token, second.fencing_token), (1, 2))
        self.assertEqual(first.node, second.node)
        self.assertFalse(second.replayed)
        self.assertFalse(second.dispatch_authorized)
        self.assertEqual(self.count("dag_node_state_events"), 2)
        self.assertEqual(self.count("dag_node_claim_candidates"), 2)
        self.service.release_runtime_supervisor_lease(second_lease)

    def test_same_term_reapproval_creates_a_new_exact_candidate(self) -> None:
        task_id, plan = self.approved_dag()
        lease = self.acquire(owner_id="dag-reapproval")
        first = self.claim(task_id, plan, lease)

        replacement = approval_decision(plan)
        replacement["approval_id"] = "approval-dag-replacement"
        self.service.persist_approval(
            task_id=task_id,
            approval=replacement,
            **self.scope,
        )
        connection = sqlite3.connect(self.database_path)
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            recorded_at, recorded_at_us = connection.execute(
                """
                SELECT recorded_at, recorded_at_us
                FROM dag_node_claim_candidates
                WHERE approval_id = ?
                """,
                (first.approval_id,),
            ).fetchone()
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "current approved plan",
            ):
                connection.execute(
                    """
                    INSERT INTO dag_node_claim_candidates(
                        task_id, plan_id, plan_hash, approval_id,
                        node_id, node_revision, node_state,
                        project_id, principal_id, fencing_token,
                        owner_id, term_acquired_at,
                        readiness_document_json, readiness_document_hash,
                        recorded_at, recorded_at_us
                    ) VALUES (?, ?, ?, ?, ?, 1, 'Pending', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        plan["plan_id"],
                        plan["plan_hash"],
                        replacement["approval_id"],
                        "invert",
                        PROJECT_ID,
                        PRINCIPAL_ID,
                        lease.fencing_token,
                        lease.owner_id,
                        lease.acquired_at,
                        '{"arbitrary":"not-readiness"}',
                        "sha256:" + "0" * 64,
                        recorded_at,
                        recorded_at_us,
                    ),
                )
        finally:
            connection.rollback()
            connection.close()
        second = self.claim(task_id, plan, lease)

        self.assertEqual(first.fencing_token, second.fencing_token)
        self.assertEqual(first.node, second.node)
        self.assertNotEqual(first.approval_id, second.approval_id)
        self.assertEqual(second.approval_id, replacement["approval_id"])
        self.assertFalse(second.replayed)
        self.assertEqual(self.count("dag_node_state_events"), 2)
        self.assertEqual(self.count("dag_node_claim_candidates"), 2)
        self.assertTrue(self.claim(task_id, plan, lease).replayed)
        self.assertEqual(self.count("dag_node_claim_candidates"), 2)
        self.service.release_runtime_supervisor_lease(lease)

    def test_single_node_sql_facts_are_rejected_and_tamper_is_not_hidden(
        self,
    ) -> None:
        created = self.service.create_task(
            draft=task_draft(),
            idempotency_key="create-single-sql",
            **self.scope,
        )
        plan = plan_graph()
        self.service.persist_plan(
            task_id=created.snapshot.task_id,
            plan=plan,
            **self.scope,
        )
        self.service.persist_approval(
            task_id=created.snapshot.task_id,
            approval=approval_decision(plan),
            **self.scope,
        )
        recorded_at = "2026-07-15T02:30:00.000000Z"
        recorded_at_us = int(
            datetime.fromisoformat(
                recorded_at.replace("Z", "+00:00")
            ).astimezone(timezone.utc).timestamp()
            * 1_000_000
        )
        values = (
            created.snapshot.task_id,
            plan["plan_id"],
            plan["plan_hash"],
            recorded_at,
            recorded_at_us,
        )
        connection = sqlite3.connect(self.database_path)
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "current approved plan",
            ):
                connection.execute(
                    """
                    INSERT INTO dag_node_state_events(
                        task_id, plan_id, plan_hash, node_id, revision,
                        previous_state, state, recorded_at, recorded_at_us
                    ) VALUES (?, ?, ?, 'invert', 1, NULL, 'Pending', ?, ?)
                    """,
                    values,
                )
            connection.rollback()

            connection.execute(
                "DROP TRIGGER dag_node_initial_state_requires_current_approved_plan"
            )
            connection.execute(
                """
                INSERT INTO dag_node_state_events(
                    task_id, plan_id, plan_hash, node_id, revision,
                    previous_state, state, recorded_at, recorded_at_us
                ) VALUES (?, ?, ?, 'invert', 1, NULL, 'Pending', ?, ?)
                """,
                values,
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaisesRegex(TaskStoreCorruption, "single-node plan"):
            self.store.get_dag_node_state_snapshot(
                task_id=created.snapshot.task_id,
                **self.scope,
            )

    def test_hash_scope_and_term_failures_rollback_before_initialization(self) -> None:
        task_id, plan = self.approved_dag()
        lease = self.acquire(owner_id="dag-stale")
        with self.assertRaisesRegex(TaskConflict, "exact current approved"):
            self.service.claim_ready_dag_node_candidate(
                task_id,
                expected_plan_hash="sha256:" + "0" * 64,
                supervisor_lease=lease,
                **self.scope,
            )
        self.assertEqual(self.count("dag_node_state_events"), 0)

        foreign_lease = self.service.acquire_runtime_supervisor_lease(
            project_id="project-other",
            principal_id=PRINCIPAL_ID,
            owner_id="dag-foreign",
            lease_seconds=30,
        ).lease
        with self.assertRaises(TaskSupervisorLeaseLost):
            self.claim(task_id, plan, foreign_lease)
        self.service.release_runtime_supervisor_lease(foreign_lease)
        self.assertEqual(self.count("dag_node_state_events"), 0)

        self.service.release_runtime_supervisor_lease(lease)
        with self.assertRaises(TaskSupervisorLeaseLost):
            self.claim(task_id, plan, lease)
        self.assertEqual(self.count("dag_node_state_events"), 0)

        fresh = self.acquire(owner_id="dag-expired")
        self.clock_value = AFTER_EXPIRY
        with self.assertRaises(TaskSupervisorLeaseLost):
            self.claim(task_id, plan, fresh)
        self.assertEqual(self.count("dag_node_state_events"), 0)
        self.assertEqual(self.count("dag_node_claim_candidates"), 0)

    def test_single_node_plan_and_direct_transition_remain_fail_closed(self) -> None:
        created = self.service.create_task(
            draft=task_draft(),
            idempotency_key="create-single-dormant",
            **self.scope,
        )
        plan = plan_graph()
        self.service.persist_plan(
            task_id=created.snapshot.task_id,
            plan=plan,
            **self.scope,
        )
        self.service.persist_approval(
            task_id=created.snapshot.task_id,
            approval=approval_decision(plan),
            **self.scope,
        )
        lease = self.acquire(owner_id="dag-single")
        with self.assertRaisesRegex(TaskConflict, "multi-node plan"):
            self.claim(created.snapshot.task_id, plan, lease)
        self.assertEqual(self.count("dag_node_state_events"), 0)
        self.service.release_runtime_supervisor_lease(lease)

        task_id, dag_plan = self.approved_dag()
        dag_lease = self.acquire(owner_id="dag-immutable")
        self.claim(task_id, dag_plan, dag_lease)
        connection = sqlite3.connect(self.database_path)
        try:
            with self.assertRaisesRegex(
                sqlite3.IntegrityError, "requires an exact active fact"
            ):
                connection.execute(
                    """
                    INSERT INTO dag_node_state_events(
                        task_id, plan_id, plan_hash, node_id, revision,
                        previous_state, state, recorded_at, recorded_at_us
                    ) VALUES (?, ?, ?, 'invert', 2, 'Pending', 'Queued', ?, ?)
                    """,
                    (
                        task_id,
                        dag_plan["plan_id"],
                        dag_plan["plan_hash"],
                        "2026-07-15T02:30:01.000000Z",
                        1787126401000000,
                    ),
                )
            connection.rollback()
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                connection.execute(
                    "UPDATE dag_node_state_events SET state = 'Queued'"
                )
            connection.rollback()
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                connection.execute("DELETE FROM dag_node_claim_candidates")
        finally:
            connection.close()
        self.assertEqual(self.count("dag_node_state_events"), 2)
        self.assertEqual(self.count("dag_node_claim_candidates"), 1)
        self.service.release_runtime_supervisor_lease(dag_lease)

    def test_v17_approved_dag_upgrades_then_initializes_lazily(self) -> None:
        legacy_migrations = Path(self.temporary.name) / "v17-migrations"
        legacy_migrations.mkdir(mode=0o700)
        for migration in sorted(
            task_store_module.MIGRATIONS_DIRECTORY.glob(
                "[0-9][0-9][0-9][0-9]_*.sql"
            )
        ):
            if int(migration.name.split("_", 1)[0]) <= 17:
                shutil.copy2(migration, legacy_migrations / migration.name)

        legacy_directory = Path(self.temporary.name) / "legacy-store"
        legacy_directory.mkdir(mode=0o700)
        legacy_path = legacy_directory / "task.sqlite3"
        with mock.patch.object(
            task_store_module,
            "MIGRATIONS_DIRECTORY",
            legacy_migrations,
        ):
            legacy_store = SQLiteTaskStore(legacy_path)
            legacy_registry = RegistryService(legacy_store, clock=lambda: NOW)
            legacy_registry.register_dataset(dataset=dataset_ref())
            legacy_registry.register_algorithm(manifest=algorithm_manifest())
            legacy_service = TaskService(
                legacy_store,
                task_id_factory=lambda: "task-legacy-dag",
                clock=lambda: NOW,
            )
            draft = task_draft()
            draft["draft_id"] = "draft-legacy-dag"
            created = legacy_service.create_task(
                draft=draft,
                idempotency_key="create-legacy-dag",
                **self.scope,
            )
            plan = plan_graph()
            plan["plan_id"] = "plan-legacy-dag"
            plan["draft"] = {
                "draft_id": draft["draft_id"],
                "revision": draft["revision"],
            }
            append_second_plan_node(plan)["dependencies"] = ["invert"]
            plan["plan_hash"] = compute_plan_hash(plan)
            legacy_service.persist_plan(
                task_id=created.snapshot.task_id,
                plan=plan,
                **self.scope,
            )
            approval = approval_decision(plan)
            approval["approval_id"] = "approval-legacy-dag"
            legacy_service.persist_approval(
                task_id=created.snapshot.task_id,
                approval=approval,
                **self.scope,
            )
            self.assertEqual(legacy_store.migration_version(), 17)

        upgraded_store = SQLiteTaskStore(legacy_path)
        self.assertEqual(upgraded_store.migration_version(), 21)
        upgraded_service = TaskService(upgraded_store, clock=lambda: NOW)
        self.assertIsNone(
            upgraded_service.get_dag_node_state_snapshot(
                created.snapshot.task_id,
                **self.scope,
            )
        )
        lease = upgraded_service.acquire_runtime_supervisor_lease(
            owner_id="legacy-dag-supervisor",
            lease_seconds=30,
            **self.scope,
        ).lease
        claimed = upgraded_service.claim_ready_dag_node_candidate(
            created.snapshot.task_id,
            expected_plan_hash=plan["plan_hash"],
            supervisor_lease=lease,
            **self.scope,
        )
        self.assertEqual(
            (claimed.node.node_id, claimed.node.state),
            ("invert", "Pending"),
        )
        self.assertFalse(claimed.dispatch_authorized)
        connection = sqlite3.connect(legacy_path)
        try:
            self.assertEqual(
                connection.execute("PRAGMA foreign_key_check").fetchall(),
                [],
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM dag_node_state_events"
                ).fetchone()[0],
                2,
            )
        finally:
            connection.close()
        upgraded_service.release_runtime_supervisor_lease(lease)


if __name__ == "__main__":
    unittest.main()
