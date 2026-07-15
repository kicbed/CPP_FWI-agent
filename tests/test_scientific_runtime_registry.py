from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from scientific_runtime import (
    RegistryCorruption,
    RegistryConflict,
    RegistryNotFound,
    RegistryService,
    RegistryValidationError,
    SQLiteTaskStore,
    TaskService,
    TaskStoreCorruption,
    TaskValidationError,
    load_deepwave_manifest,
)
from scientific_runtime.fwi_registry import _dataset_ref_from_validated_metadata
from scientific_runtime.task_store import (
    APPLICATION_ID,
    SCHEMA_MIGRATIONS_SQL,
    _migration_statements,
    encode_document,
)
from scientific_runtime_contracts import (
    compute_plan_hash,
    evaluate_execution_gate,
)
from tests.test_scientific_runtime_contracts import (
    algorithm_manifest,
    approval_decision,
    dataset_ref,
    plan_graph,
    task_draft,
)


NOW = "2026-07-15T04:00:00Z"
PROJECT_ID = "project-1"
PRINCIPAL_ID = "user-1"


def dataset_for(
    project_id: str = PROJECT_ID,
    principal_id: str = PRINCIPAL_ID,
) -> dict:
    value = dataset_ref()
    value["access_scope"] = {
        "project_id": project_id,
        "principals": [principal_id],
        "permissions": ["read", "execute"],
    }
    return value


class ScientificRuntimeRegistryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary.name) / "task.sqlite3"
        self.store = SQLiteTaskStore(self.database_path)
        self.registry = RegistryService(self.store, clock=lambda: NOW)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def register_baseline(self) -> tuple[dict, dict]:
        dataset = dataset_for()
        manifest = algorithm_manifest()
        self.registry.register_dataset(dataset=dataset)
        self.registry.register_algorithm(manifest=manifest)
        return dataset, manifest

    def service(self) -> TaskService:
        return TaskService(
            self.store,
            task_id_factory=lambda: "task-registry-001",
            clock=lambda: NOW,
        )

    def count(self, table: str) -> int:
        connection = sqlite3.connect(self.database_path)
        try:
            return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        finally:
            connection.close()

    def test_fresh_v2_has_both_migration_checksums(self) -> None:
        self.assertEqual(self.store.migration_version(), 2)
        connection = sqlite3.connect(self.database_path)
        try:
            rows = connection.execute(
                "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual(
            [(row[0], row[1]) for row in rows],
            [(1, "0001_task_store.sql"), (2, "0002_catalog_registry.sql")],
        )
        for version, name, checksum in rows:
            path = Path(__file__).parents[1] / "scientific_runtime" / "migrations" / name
            self.assertEqual(
                checksum,
                hashlib.sha256(path.read_bytes()).hexdigest(),
                msg=f"migration {version}",
            )

    def test_dataset_registration_is_immutable_idempotent_and_project_scoped(self) -> None:
        dataset = dataset_for()
        first = self.registry.register_dataset(dataset=dataset)
        second = self.registry.register_dataset(dataset=copy.deepcopy(dataset))
        self.assertFalse(first.replayed)
        self.assertTrue(second.replayed)
        self.assertEqual(self.count("dataset_versions"), 1)
        self.assertEqual(self.count("dataset_catalog"), 1)

        project_two = dataset_for("project-2")
        self.registry.register_dataset(dataset=project_two)
        self.assertEqual(self.count("dataset_versions"), 1)
        self.assertEqual(self.count("dataset_catalog"), 2)

        metadata_conflict = dataset_for("project-3")
        metadata_conflict["metadata"]["value_range"]["maximum"] = 5400
        with self.assertRaises(RegistryConflict):
            self.registry.register_dataset(dataset=metadata_conflict)

        conflicting = dataset_for("project-3")
        conflicting["content_hash"] = "sha256:" + "f" * 64
        with self.assertRaises(RegistryConflict):
            self.registry.register_dataset(dataset=conflicting)
        self.assertEqual(self.count("dataset_versions"), 1)
        self.assertEqual(self.count("dataset_catalog"), 2)

    def test_dataset_permissions_distinguish_read_from_execute(self) -> None:
        read_only = dataset_for()
        read_only["access_scope"]["permissions"] = ["read"]
        self.registry.register_dataset(dataset=read_only)
        self.assertEqual(
            self.registry.get_dataset(
                project_id=PROJECT_ID,
                principal_id=PRINCIPAL_ID,
                dataset_id=read_only["id"],
                version=read_only["version"],
            ),
            read_only,
        )
        with self.assertRaises(RegistryNotFound):
            self.registry.get_dataset(
                project_id=PROJECT_ID,
                principal_id=PRINCIPAL_ID,
                dataset_id=read_only["id"],
                version=read_only["version"],
                permission="execute",
            )
        self.registry.register_algorithm(manifest=algorithm_manifest())
        draft = task_draft()
        draft["datasets"] = [read_only]
        with self.assertRaisesRegex(TaskValidationError, "DATASET_ACCESS_DENIED"):
            self.service().create_task(
                project_id=PROJECT_ID,
                principal_id=PRINCIPAL_ID,
                draft=draft,
                idempotency_key="create-read-only",
            )

    def test_algorithm_registration_is_immutable_idempotent_and_concurrent(self) -> None:
        manifest = algorithm_manifest()

        def register(_: int) -> bool:
            service = RegistryService(SQLiteTaskStore(self.database_path), clock=lambda: NOW)
            return service.register_algorithm(manifest=manifest).replayed

        with ThreadPoolExecutor(max_workers=4) as executor:
            replayed = list(executor.map(register, range(4)))
        self.assertEqual(replayed.count(False), 1)
        self.assertEqual(replayed.count(True), 3)
        self.assertEqual(self.count("algorithm_registry"), 1)

        conflicting = copy.deepcopy(manifest)
        conflicting["security"]["allowlisted"] = False
        with self.assertRaises(RegistryConflict):
            self.registry.register_algorithm(manifest=conflicting)
        self.assertEqual(self.count("algorithm_registry"), 1)

    def test_new_versions_coexist_without_mutating_old_snapshots(self) -> None:
        dataset_v1 = dataset_for()
        manifest_v1 = algorithm_manifest()
        self.registry.register_dataset(dataset=dataset_v1)
        self.registry.register_algorithm(manifest=manifest_v1)

        dataset_v2 = copy.deepcopy(dataset_v1)
        dataset_v2["version"] = "1.0.1"
        manifest_v2 = copy.deepcopy(manifest_v1)
        manifest_v2["version"] = "1.0.1"
        self.registry.register_dataset(dataset=dataset_v2)
        self.registry.register_algorithm(manifest=manifest_v2)

        self.assertEqual(
            [value["version"] for value in self.registry.list_datasets(
                project_id=PROJECT_ID, principal_id=PRINCIPAL_ID
            )],
            ["1.0.0", "1.0.1"],
        )
        self.assertEqual(
            [value["version"] for value in self.registry.list_algorithms()],
            ["1.0.0", "1.0.1"],
        )

    def test_invalid_registry_documents_never_persist(self) -> None:
        invalid_dataset = dataset_for()
        invalid_dataset["server_path"] = "/root/fwi-data/models/model.npy"
        with self.assertRaisesRegex(RegistryValidationError, "SCHEMA_INVALID"):
            self.registry.register_dataset(dataset=invalid_dataset)

        invalid_manifest = algorithm_manifest()
        invalid_manifest["inputs"].append(copy.deepcopy(invalid_manifest["inputs"][0]))
        with self.assertRaisesRegex(RegistryValidationError, "MANIFEST_INVALID"):
            self.registry.register_algorithm(manifest=invalid_manifest)
        self.assertEqual(self.count("dataset_catalog"), 0)
        self.assertEqual(self.count("algorithm_registry"), 0)

    def test_scoped_dataset_reads_do_not_leak_existence(self) -> None:
        dataset = dataset_for()
        self.registry.register_dataset(dataset=dataset)
        self.assertEqual(
            self.registry.get_dataset(
                project_id=PROJECT_ID,
                principal_id=PRINCIPAL_ID,
                dataset_id=dataset["id"],
                version=dataset["version"],
            ),
            dataset,
        )
        for project_id, principal_id in (
            ("project-2", PRINCIPAL_ID),
            (PROJECT_ID, "user-2"),
        ):
            with self.subTest(project_id=project_id, principal_id=principal_id):
                with self.assertRaisesRegex(RegistryNotFound, "requested scope"):
                    self.registry.get_dataset(
                        project_id=project_id,
                        principal_id=principal_id,
                        dataset_id=dataset["id"],
                        version=dataset["version"],
                    )
                self.assertEqual(
                    self.registry.list_datasets(
                        project_id=project_id,
                        principal_id=principal_id,
                    ),
                    [],
                )

    def test_non_allowlisted_algorithms_are_hidden_by_default(self) -> None:
        manifest = algorithm_manifest()
        manifest["security"]["allowlisted"] = False
        self.registry.register_algorithm(manifest=manifest)
        with self.assertRaises(RegistryNotFound):
            self.registry.get_algorithm(
                algorithm_id=manifest["id"], version=manifest["version"]
            )
        self.assertEqual(self.registry.list_algorithms(), [])
        self.assertEqual(
            self.registry.get_algorithm(
                algorithm_id=manifest["id"],
                version=manifest["version"],
                require_allowlisted=False,
            ),
            manifest,
        )

    def test_registry_lookup_keys_are_strictly_typed(self) -> None:
        dataset, manifest = self.register_baseline()
        for version in (None, True, "01.0.0", "1.0"):
            with self.subTest(version=version):
                with self.assertRaisesRegex(
                    RegistryValidationError, "INVALID_VERSION"
                ):
                    self.registry.get_dataset(
                        project_id=PROJECT_ID,
                        principal_id=PRINCIPAL_ID,
                        dataset_id=dataset["id"],
                        version=version,
                    )
        with self.assertRaisesRegex(
            RegistryValidationError, "INVALID_REGISTRY_KEY"
        ):
            self.registry.get_algorithm(
                algorithm_id="Deepwave/../../unsafe",
                version=manifest["version"],
            )

    def test_registry_rows_survive_reopen_and_reject_mutation(self) -> None:
        dataset, manifest = self.register_baseline()
        loaded = self.registry.get_dataset(
            project_id=PROJECT_ID,
            principal_id=PRINCIPAL_ID,
            dataset_id=dataset["id"],
            version=dataset["version"],
        )
        loaded["metadata"]["shape"][0] = 1

        reopened = RegistryService(SQLiteTaskStore(self.database_path))
        self.assertEqual(
            reopened.get_dataset(
                project_id=PROJECT_ID,
                principal_id=PRINCIPAL_ID,
                dataset_id=dataset["id"],
                version=dataset["version"],
            ),
            dataset,
        )
        self.assertEqual(
            reopened.get_algorithm(
                algorithm_id=manifest["id"], version=manifest["version"]
            ),
            manifest,
        )

        connection = sqlite3.connect(self.database_path)
        try:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                connection.execute(
                    "UPDATE dataset_catalog SET data_type = 'changed'"
                )
            with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                connection.execute("DELETE FROM algorithm_registry")
        finally:
            connection.close()

    def test_registry_hash_and_index_corruption_fail_closed(self) -> None:
        dataset, manifest = self.register_baseline()
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute("DROP TRIGGER dataset_catalog_is_immutable")
            connection.execute(
                "UPDATE dataset_catalog SET document_json = ?",
                ("{}",),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(TaskStoreCorruption, "hash does not match"):
            self.store.get_dataset(
                project_id=PROJECT_ID,
                dataset_id=dataset["id"],
                version=dataset["version"],
            )
        with self.assertRaisesRegex(RegistryCorruption, "hash does not match"):
            self.registry.register_dataset(dataset=dataset)

        other_path = Path(self.temporary.name) / "algorithm-corrupt.sqlite3"
        other_store = SQLiteTaskStore(other_path)
        other_registry = RegistryService(other_store)
        other_registry.register_algorithm(manifest=manifest)
        connection = sqlite3.connect(other_path)
        try:
            connection.execute("DROP TRIGGER algorithm_registry_is_immutable")
            connection.execute(
                "UPDATE algorithm_registry SET allowlisted = 0"
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(TaskStoreCorruption, "identity"):
            other_store.get_algorithm(
                algorithm_id=manifest["id"], version=manifest["version"]
            )
        with self.assertRaisesRegex(RegistryCorruption, "identity"):
            other_registry.register_algorithm(manifest=manifest)

    def test_schema_revalidation_rejects_hash_consistent_registry_corruption(self) -> None:
        manifest = algorithm_manifest()
        self.registry.register_algorithm(manifest=manifest)
        invalid = copy.deepcopy(manifest)
        invalid.pop("adapter")
        document_json, document_hash = encode_document(invalid)
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute("DROP TRIGGER algorithm_registry_is_immutable")
            connection.execute(
                """
                UPDATE algorithm_registry
                SET document_json = ?, document_hash = ?
                WHERE algorithm_id = ? AND version = ?
                """,
                (document_json, document_hash, manifest["id"], manifest["version"]),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(RegistryCorruption, "AlgorithmManifest"):
            self.registry.get_algorithm(
                algorithm_id=manifest["id"], version=manifest["version"]
            )

    def test_task_service_resolves_only_server_owned_registry_snapshots(self) -> None:
        dataset, _ = self.register_baseline()
        service = self.service()
        created = service.create_task(
            project_id=PROJECT_ID,
            principal_id=PRINCIPAL_ID,
            draft=task_draft(),
            idempotency_key="create-registry-task",
        )
        self.assertEqual(created.snapshot.draft["datasets"], [dataset])

        mismatched = task_draft()
        mismatched["draft_id"] = "draft-hash-mismatch"
        mismatched["datasets"][0]["content_hash"] = "sha256:" + "f" * 64
        with self.assertRaisesRegex(TaskValidationError, "DATASET_METADATA_MISMATCH"):
            service.create_task(
                project_id=PROJECT_ID,
                principal_id=PRINCIPAL_ID,
                draft=mismatched,
                idempotency_key="create-mismatch",
            )

        unknown = task_draft()
        unknown["draft_id"] = "draft-unknown-algorithm"
        unknown["algorithm"]["version"] = "2.0.0"
        with self.assertRaisesRegex(TaskValidationError, "ALGORITHM_NOT_REGISTERED"):
            service.create_task(
                project_id=PROJECT_ID,
                principal_id=PRINCIPAL_ID,
                draft=unknown,
                idempotency_key="create-unknown",
            )
        self.assertEqual(self.count("tasks"), 1)

        other_path = Path(self.temporary.name) / "missing-dataset.sqlite3"
        other_store = SQLiteTaskStore(other_path)
        RegistryService(other_store).register_algorithm(manifest=algorithm_manifest())
        with self.assertRaisesRegex(TaskValidationError, "DATASET_NOT_REGISTERED"):
            TaskService(other_store).create_task(
                project_id=PROJECT_ID,
                principal_id=PRINCIPAL_ID,
                draft=task_draft(),
                idempotency_key="create-missing-dataset",
            )

    def test_sqlite_registry_snapshot_drives_the_side_effect_free_gate(self) -> None:
        dataset, manifest = self.register_baseline()
        snapshots = self.store.load_registry_snapshots(
            project_id=PROJECT_ID,
            dataset_keys=[(dataset["id"], dataset["version"])],
            algorithm_keys=[(manifest["id"], manifest["version"])],
        )
        plan = plan_graph()
        self.assertEqual(
            evaluate_execution_gate(
                draft=task_draft(),
                plan=plan,
                approval=approval_decision(plan),
                dataset_registry=snapshots.datasets,
                algorithm_registry=snapshots.algorithms,
                principal_id=PRINCIPAL_ID,
                project_id=PROJECT_ID,
                approval_tasks_used=0,
                now=datetime(2026, 7, 15, 2, 30, tzinfo=timezone.utc),
            ),
            [],
        )

    def test_task_service_rejects_non_allowlisted_and_incompatible_manifests(self) -> None:
        self.registry.register_dataset(dataset=dataset_for())
        manifest = algorithm_manifest()
        manifest["security"]["allowlisted"] = False
        self.registry.register_algorithm(manifest=manifest)
        with self.assertRaisesRegex(TaskValidationError, "ALGORITHM_NOT_ALLOWLISTED"):
            self.service().create_task(
                project_id=PROJECT_ID,
                principal_id=PRINCIPAL_ID,
                draft=task_draft(),
                idempotency_key="create-not-allowlisted",
            )

        other_path = Path(self.temporary.name) / "incompatible.sqlite3"
        other_store = SQLiteTaskStore(other_path)
        other_registry = RegistryService(other_store)
        other_registry.register_dataset(dataset=dataset_for())
        incompatible = algorithm_manifest()
        incompatible["task_types"] = ["acoustic_forward_2d"]
        other_registry.register_algorithm(manifest=incompatible)
        with self.assertRaisesRegex(TaskValidationError, "TASK_TYPE_MISMATCH"):
            TaskService(other_store).create_task(
                project_id=PROJECT_ID,
                principal_id=PRINCIPAL_ID,
                draft=task_draft(),
                idempotency_key="create-incompatible",
            )

        resource_path = Path(self.temporary.name) / "resource-limit.sqlite3"
        resource_store = SQLiteTaskStore(resource_path)
        resource_registry = RegistryService(resource_store)
        resource_registry.register_dataset(dataset=dataset_for())
        cpu_only = algorithm_manifest()
        cpu_only["resource_limits"]["devices"] = ["cpu"]
        resource_registry.register_algorithm(manifest=cpu_only)
        with self.assertRaisesRegex(TaskValidationError, "RESOURCE_LIMIT_EXCEEDED"):
            TaskService(resource_store).create_task(
                project_id=PROJECT_ID,
                principal_id=PRINCIPAL_ID,
                draft=task_draft(),
                idempotency_key="create-resource-exceeded",
            )

    def test_plan_ports_and_side_effects_are_checked_against_registry(self) -> None:
        self.register_baseline()
        service = self.service()
        created = service.create_task(
            project_id=PROJECT_ID,
            principal_id=PRINCIPAL_ID,
            draft=task_draft(),
            idempotency_key="create-plan-registry",
        )
        unknown_input = plan_graph()
        unknown_input["nodes"][0]["inputs"][0]["port"] = "unknown_model"
        unknown_input["plan_hash"] = compute_plan_hash(unknown_input)
        with self.assertRaisesRegex(TaskValidationError, "PLAN_REGISTRY_MISMATCH"):
            service.persist_plan(
                task_id=created.snapshot.task_id,
                project_id=PROJECT_ID,
                principal_id=PRINCIPAL_ID,
                plan=unknown_input,
            )

        plan = plan_graph()
        plan["nodes"][0]["outputs"][0]["port"] = "loss"
        plan["plan_hash"] = compute_plan_hash(plan)
        with self.assertRaisesRegex(TaskValidationError, "PLAN_REGISTRY_MISMATCH"):
            service.persist_plan(
                task_id=created.snapshot.task_id,
                project_id=PROJECT_ID,
                principal_id=PRINCIPAL_ID,
                plan=plan,
            )
        missing_output = plan_graph()
        missing_output["nodes"][0]["outputs"].pop()
        missing_output["plan_hash"] = compute_plan_hash(missing_output)
        with self.assertRaisesRegex(TaskValidationError, "PLAN_REGISTRY_MISMATCH"):
            service.persist_plan(
                task_id=created.snapshot.task_id,
                project_id=PROJECT_ID,
                principal_id=PRINCIPAL_ID,
                plan=missing_output,
            )

        duplicate_output = plan_graph()
        duplicate_output["nodes"][0]["outputs"].append(
            copy.deepcopy(duplicate_output["nodes"][0]["outputs"][0])
        )
        duplicate_output["plan_hash"] = compute_plan_hash(duplicate_output)
        with self.assertRaisesRegex(TaskValidationError, "PLAN_REGISTRY_MISMATCH"):
            service.persist_plan(
                task_id=created.snapshot.task_id,
                project_id=PROJECT_ID,
                principal_id=PRINCIPAL_ID,
                plan=duplicate_output,
            )
        self.assertEqual(self.count("plans"), 0)

        restricted_path = Path(self.temporary.name) / "side-effects.sqlite3"
        restricted_store = SQLiteTaskStore(restricted_path)
        restricted_registry = RegistryService(restricted_store, clock=lambda: NOW)
        restricted_registry.register_dataset(dataset=dataset_for())
        restricted_manifest = algorithm_manifest()
        restricted_manifest["security"]["side_effects"] = ["compute"]
        restricted_registry.register_algorithm(manifest=restricted_manifest)
        restricted_service = TaskService(
            restricted_store,
            task_id_factory=lambda: "task-restricted-effects",
            clock=lambda: NOW,
        )
        restricted_task = restricted_service.create_task(
            project_id=PROJECT_ID,
            principal_id=PRINCIPAL_ID,
            draft=task_draft(),
            idempotency_key="create-restricted-effects",
        )
        with self.assertRaisesRegex(TaskValidationError, "PLAN_REGISTRY_MISMATCH"):
            restricted_service.persist_plan(
                task_id=restricted_task.snapshot.task_id,
                project_id=PROJECT_ID,
                principal_id=PRINCIPAL_ID,
                plan=plan_graph(),
            )

    def test_approval_budget_is_persisted_and_bound_to_decision(self) -> None:
        self.register_baseline()
        service = self.service()
        created = service.create_task(
            project_id=PROJECT_ID,
            principal_id=PRINCIPAL_ID,
            draft=task_draft(),
            idempotency_key="create-budget-task",
        )
        plan = plan_graph()
        service.persist_plan(
            task_id=created.snapshot.task_id,
            project_id=PROJECT_ID,
            principal_id=PRINCIPAL_ID,
            plan=plan,
        )
        approval = approval_decision(plan)
        service.persist_approval(
            task_id=created.snapshot.task_id,
            project_id=PROJECT_ID,
            principal_id=PRINCIPAL_ID,
            approval=approval,
        )
        budget = self.store.get_approval_budget(
            task_id=created.snapshot.task_id,
            approval_id=approval["approval_id"],
        )
        self.assertIsNotNone(budget)
        self.assertEqual((budget.max_tasks, budget.tasks_used), (1, 0))

        connection = sqlite3.connect(self.database_path)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE approval_budgets SET tasks_used = 0.5"
                )
            connection.rollback()
            with self.assertRaisesRegex(sqlite3.IntegrityError, "identity"):
                connection.execute("UPDATE approval_budgets SET max_tasks = 2")
            connection.rollback()
            connection.execute("DROP TRIGGER approval_budget_identity_is_immutable")
            connection.execute("UPDATE approval_budgets SET max_tasks = 2")
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(TaskStoreCorruption, "does not match"):
            self.store.get_approval_budget(
                task_id=created.snapshot.task_id,
                approval_id=approval["approval_id"],
            )

        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute("PRAGMA ignore_check_constraints = ON")
            connection.execute(
                "UPDATE approval_budgets SET max_tasks = 1, tasks_used = 0.5"
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(TaskStoreCorruption, "does not match"):
            self.store.get_approval_budget(
                task_id=created.snapshot.task_id,
                approval_id=approval["approval_id"],
            )

    def test_packaged_fwi_snapshot_is_schema_valid_and_path_free(self) -> None:
        manifest = load_deepwave_manifest()
        self.assertEqual(manifest, algorithm_manifest())
        metadata = {
            "id": "marmousi_94_288",
            "shape": [94, 288],
            "axis_order": ["z", "x"],
            "compute_dtype": "float32",
            "physics": "2d_acoustic_constant_density",
            "parameter": "vp",
            "velocity_unit": "m/s",
            "velocity_min_mps": 1500.0,
            "velocity_max_mps": 5500.0,
            "dx_m": 10.0,
            "dz_m": 10.0,
            "sha256": "B80918E3A609A679F16A47DD30978812D80E4FAB1FCBD5CE692D9CA97022A688",
            "path": "/private/model.npy",
            "source_path": "/private/model.mat",
        }
        dataset = _dataset_ref_from_validated_metadata(
            metadata,
            project_id=PROJECT_ID,
            principals=[PRINCIPAL_ID],
        )
        serialized = json.dumps(dataset, sort_keys=True)
        self.assertEqual(
            dataset["content_hash"],
            "sha256:b80918e3a609a679f16a47dd30978812d80e4fab1fcbd5ce692d9ca97022a688",
        )
        self.assertNotIn("/private", serialized)
        self.assertNotIn("path", serialized)


class ScientificRuntimeV1UpgradeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary.name) / "v1.sqlite3"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def seed_v1_database(
        self, *, approval_max_tasks: int | float = 1
    ) -> tuple[str, dict]:
        migration_path = (
            Path(__file__).parents[1]
            / "scientific_runtime"
            / "migrations"
            / "0001_task_store.sql"
        )
        migration_text = migration_path.read_text(encoding="utf-8")
        connection = sqlite3.connect(self.database_path, isolation_level=None)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(SCHEMA_MIGRATIONS_SQL)
            for statement in _migration_statements(migration_text):
                connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, name, checksum, applied_at)
                VALUES (1, ?, ?, ?)
                """,
                (
                    migration_path.name,
                    hashlib.sha256(migration_text.encode("utf-8")).hexdigest(),
                    NOW,
                ),
            )
            connection.execute("PRAGMA user_version = 1")
            connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")

            task_id = "task-v1-upgrade"
            draft = task_draft()
            plan = plan_graph()
            approval = approval_decision(plan)
            approval["scope"]["max_tasks"] = approval_max_tasks
            draft_json, draft_hash = encode_document(draft)
            plan_json, plan_document_hash = encode_document(plan)
            approval_json, approval_hash = encode_document(approval)
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO tasks(
                    task_id, project_id, principal_id, status, created_at, updated_at
                ) VALUES (?, ?, ?, 'AwaitingApproval', ?, ?)
                """,
                (task_id, PROJECT_ID, PRINCIPAL_ID, NOW, NOW),
            )
            connection.execute(
                """
                INSERT INTO draft_revisions(
                    task_id, draft_id, revision, document_json, document_hash, recorded_at
                ) VALUES (?, ?, 1, ?, ?, ?)
                """,
                (task_id, draft["draft_id"], draft_json, draft_hash, NOW),
            )
            connection.execute(
                """
                INSERT INTO plans(
                    task_id, plan_id, draft_id, draft_revision, plan_hash,
                    document_json, document_hash, recorded_at
                ) VALUES (?, ?, ?, 1, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    plan["plan_id"],
                    draft["draft_id"],
                    plan["plan_hash"],
                    plan_json,
                    plan_document_hash,
                    NOW,
                ),
            )
            node = plan["nodes"][0]
            connection.execute(
                """
                INSERT INTO plan_node_idempotency(
                    task_id, plan_id, node_id, idempotency_key
                ) VALUES (?, ?, ?, ?)
                """,
                (task_id, plan["plan_id"], node["node_id"], node["idempotency_key"]),
            )
            connection.execute(
                """
                INSERT INTO approvals(
                    task_id, approval_id, plan_id, plan_hash, decision,
                    document_json, document_hash, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    approval["approval_id"],
                    plan["plan_id"],
                    plan["plan_hash"],
                    approval["decision"],
                    approval_json,
                    approval_hash,
                    NOW,
                ),
            )
            connection.execute(
                """
                UPDATE tasks
                SET current_draft_id = ?, current_draft_revision = 1,
                    current_plan_id = ?, current_approval_id = ?
                WHERE task_id = ?
                """,
                (
                    draft["draft_id"],
                    plan["plan_id"],
                    approval["approval_id"],
                    task_id,
                ),
            )
            connection.commit()
            return task_id, approval
        finally:
            connection.close()

    def test_v1_database_upgrades_in_place_and_backfills_approval_budget(self) -> None:
        task_id, approval = self.seed_v1_database()
        store = SQLiteTaskStore(self.database_path)
        self.assertEqual(store.migration_version(), 2)
        snapshot = store.get_task(task_id)
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.approval, approval)
        budget = store.get_approval_budget(
            task_id=task_id, approval_id=approval["approval_id"]
        )
        self.assertIsNotNone(budget)
        self.assertEqual((budget.max_tasks, budget.tasks_used), (1, 0))

    def test_concurrent_v1_upgrade_converges_without_duplicate_backfill(self) -> None:
        task_id, approval = self.seed_v1_database()

        def reopen(_: int) -> tuple[int, str]:
            store = SQLiteTaskStore(self.database_path)
            snapshot = store.get_task(task_id)
            return store.migration_version(), snapshot.approval["approval_id"]

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(reopen, range(8)))
        self.assertEqual(results, [(2, approval["approval_id"])] * 8)
        connection = sqlite3.connect(self.database_path)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM schema_migrations WHERE version = 2"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM approval_budgets"
                ).fetchone()[0],
                1,
            )
        finally:
            connection.close()

    def test_v1_migration_metadata_tampering_fails_before_upgrade(self) -> None:
        self.seed_v1_database()
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "UPDATE schema_migrations SET name = 'renamed.sql' WHERE version = 1"
            )
            connection.commit()
        finally:
            connection.close()
        from scientific_runtime import TaskStoreError

        with self.assertRaisesRegex(TaskStoreError, "metadata is inconsistent"):
            SQLiteTaskStore(self.database_path)
        connection = sqlite3.connect(self.database_path)
        try:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertIsNone(
                connection.execute(
                    """
                    SELECT 1 FROM sqlite_master
                    WHERE type = 'table' AND name = 'dataset_catalog'
                    """
                ).fetchone()
            )
        finally:
            connection.close()

    def test_failed_v2_budget_backfill_rolls_back_the_whole_migration(self) -> None:
        self.seed_v1_database(approval_max_tasks=1.5)
        with self.assertRaises(TaskStoreCorruption):
            SQLiteTaskStore(self.database_path)
        connection = sqlite3.connect(self.database_path)
        try:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0],
                1,
            )
            self.assertIsNone(
                connection.execute(
                    """
                    SELECT 1 FROM sqlite_master
                    WHERE type = 'table' AND name = 'approval_budgets'
                    """
                ).fetchone()
            )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
