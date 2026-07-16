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

from jsonschema import Draft7Validator

from scientific_runtime import (
    RegistryCorruption,
    RegistryConflict,
    RegistryNotFound,
    RegistryService,
    RegistryValidationError,
    SQLiteTaskStore,
    TaskConflict,
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
    schema_errors,
)
from tests.test_scientific_runtime_contracts import (
    algorithm_manifest,
    approval_decision,
    dataset_ref,
    plan_graph,
    run_event,
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

    def test_fresh_v14_has_all_migration_checksums_and_task_discovery_index(self) -> None:
        self.assertEqual(self.store.migration_version(), 14)
        connection = sqlite3.connect(self.database_path)
        try:
            rows = connection.execute(
                "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
            ).fetchall()
            index_sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
                ("idx_tasks_scope_created",),
            ).fetchone()[0]
            visibility_mutation_index_sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
                ("idx_task_visibility_mutations_task",),
            ).fetchone()[0]
            visibility_query_plan = connection.execute(
                """
                EXPLAIN QUERY PLAN
                SELECT 1 FROM task_visibility_mutations
                WHERE task_id = ? LIMIT 1
                """,
                ("task-query-plan",),
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual(
            [(row[0], row[1]) for row in rows],
            [
                (1, "0001_task_store.sql"),
                (2, "0002_catalog_registry.sql"),
                (3, "0003_submit_dispatch.sql"),
                (4, "0004_workbench_runtime.sql"),
                (5, "0005_task_discovery.sql"),
                (6, "0006_task_visibility.sql"),
                (7, "0007_task_purge.sql"),
                (8, "0008_runtime_supervisor.sql"),
                (9, "0009_worker_attempt_projection.sql"),
                (10, "0010_supervised_dispatch.sql"),
                (11, "0011_task_cancellation.sql"),
                (12, "0012_task_timeout.sql"),
                (13, "0013_dispatch_reconciliation.sql"),
                (14, "0014_task_retry.sql"),
            ],
        )
        for version, name, checksum in rows:
            path = Path(__file__).parents[1] / "scientific_runtime" / "migrations" / name
            self.assertEqual(
                checksum,
                hashlib.sha256(path.read_bytes()).hexdigest(),
                msg=f"migration {version}",
            )
        self.assertEqual(
            " ".join(index_sql.split()),
            "CREATE INDEX idx_tasks_scope_created ON tasks(project_id, "
            "principal_id, created_at DESC, task_id DESC)",
        )
        self.assertEqual(
            " ".join(visibility_mutation_index_sql.split()),
            "CREATE INDEX idx_task_visibility_mutations_task ON "
            "task_visibility_mutations(task_id)",
        )
        self.assertTrue(
            any(
                "idx_task_visibility_mutations_task" in row[3]
                for row in visibility_query_plan
            )
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
        manifest_v2["version"] = "1.2.0"
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
            ["1.1.0", "1.2.0"],
        )

    def test_packaged_algorithm_upgrade_preserves_legacy_parameter_policy(self) -> None:
        legacy = load_deepwave_manifest("1.0.0")
        previous = load_deepwave_manifest("1.1.0")
        optimizer_version = load_deepwave_manifest("1.2.0")
        previous_current = load_deepwave_manifest("1.3.0")
        previous_outputs = load_deepwave_manifest("1.4.0")
        current = load_deepwave_manifest()
        legacy_json, legacy_hash = encode_document(legacy)
        _, optimizer_version_hash = encode_document(optimizer_version)
        _, previous_current_hash = encode_document(previous_current)
        _, current_hash = encode_document(current)
        self.assertEqual(
            legacy_hash,
            "sha256:20c22a2c54259622435850b05eb7eeb020ff4d74af2cec51439aa465793f8dcd",
        )
        self.assertEqual(legacy["version"], "1.0.0")
        self.assertEqual(
            legacy["parameter_schema"]["properties"]["iterations"]["maximum"],
            100,
        )
        self.assertEqual(legacy["adapter"]["version"], "1.0.0")
        self.assertEqual(previous["version"], "1.1.0")
        self.assertEqual(
            previous["parameter_schema"]["properties"]["iterations"]["maximum"],
            10000,
        )
        self.assertEqual(previous["adapter"]["version"], "1.1.0")
        self.assertEqual(optimizer_version["version"], "1.2.0")
        self.assertEqual(
            optimizer_version_hash,
            "sha256:f1ead959e4aaffadb0c32b6ed98dedce508f5153c9e41453b65d28f1c9daea66",
        )
        self.assertEqual(
            optimizer_version["parameter_schema"]["properties"]["iterations"]["minimum"],
            0,
        )
        self.assertNotIn(
            "maximum",
            optimizer_version["parameter_schema"]["properties"]["seed"],
        )
        self.assertEqual(optimizer_version["adapter"]["version"], "1.2.0")
        self.assertEqual(previous_current["version"], "1.3.0")
        self.assertEqual(
            previous_current_hash,
            "sha256:6424a8d70f8e962460484e085ed0ab216fb4706bd156b111bf31baa592f72d81",
        )
        self.assertEqual(previous_current["adapter"]["version"], "1.3.0")
        self.assertEqual(len(previous_current["outputs"]), 2)
        self.assertEqual(previous_outputs["version"], "1.4.0")
        self.assertEqual(previous_outputs["adapter"]["version"], "1.4.0")
        self.assertEqual(current["version"], "1.5.0")
        self.assertEqual(
            current_hash,
            "sha256:09168e087073e3f39a8829e457a9197d50331eea140cb1dbe989224d6ec6b658",
        )
        self.assertEqual(current["schema_version"], "1.1.0")
        self.assertEqual(
            current["parameter_schema"]["properties"]["iterations"]["maximum"],
            10000,
        )
        self.assertEqual(
            current["parameter_schema"]["properties"]["iterations"]["minimum"],
            1,
        )
        self.assertEqual(
            current["parameter_schema"]["properties"]["seed"]["maximum"],
            2147483647,
        )
        self.assertEqual(current["adapter"]["version"], "1.5.0")
        self.assertEqual(
            current["outputs"],
            [
                {"port": "inverted_model", "data_type": "inverted_velocity_model_2d"},
                {"port": "loss", "data_type": "loss_curve"},
                {"port": "true_model_figure", "data_type": "figure"},
                {"port": "initial_model_figure", "data_type": "figure"},
                {"port": "inverted_model_figure", "data_type": "figure"},
                {"port": "model_error_figure", "data_type": "figure"},
                {"port": "shot_gathers_figure", "data_type": "figure"},
                {"port": "loss_curve_figure", "data_type": "figure"},
            ],
        )
        self.assertEqual(
            set(current["parameter_schema"]["required"]),
            {"preset", "device", "iterations", "seed", "optimizer", "learning_rate_milli"},
        )

        self.registry.register_algorithm(manifest=legacy)
        connection = sqlite3.connect(self.database_path)
        try:
            legacy_row_before = connection.execute(
                """
                SELECT document_json, document_hash
                FROM algorithm_registry
                WHERE algorithm_id = ? AND version = ?
                """,
                (legacy["id"], legacy["version"]),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(legacy_row_before, (legacy_json, legacy_hash))
        legacy_before = self.registry.get_algorithm(
            algorithm_id=legacy["id"], version=legacy["version"]
        )
        reopened = RegistryService(
            SQLiteTaskStore(self.database_path), clock=lambda: NOW
        )
        reopened.register_algorithm(manifest=previous)
        reopened.register_algorithm(manifest=optimizer_version)
        reopened.register_algorithm(manifest=previous_current)
        reopened.register_algorithm(manifest=previous_outputs)
        reopened.register_algorithm(manifest=current)
        self.assertEqual(
            reopened.get_algorithm(
                algorithm_id=legacy["id"], version=legacy["version"]
            ),
            legacy_before,
        )
        connection = sqlite3.connect(self.database_path)
        try:
            legacy_row_after = connection.execute(
                """
                SELECT document_json, document_hash
                FROM algorithm_registry
                WHERE algorithm_id = ? AND version = ?
                """,
                (legacy["id"], legacy["version"]),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(legacy_row_after, legacy_row_before)
        self.assertEqual(
            [value["version"] for value in reopened.list_algorithms()],
            ["1.0.0", "1.1.0", "1.2.0", "1.3.0", "1.4.0", "1.5.0"],
        )

    def test_iteration_policy_is_bound_to_algorithm_version(self) -> None:
        self.registry.register_dataset(dataset=dataset_for())
        legacy = load_deepwave_manifest("1.0.0")
        current = load_deepwave_manifest()
        self.registry.register_algorithm(manifest=legacy)
        self.registry.register_algorithm(manifest=current)
        service = self.service()

        legacy_draft = task_draft()
        legacy_draft["draft_id"] = "draft-legacy-limit"
        legacy_draft["algorithm"]["version"] = "1.0.0"
        legacy_draft["parameters"]["iterations"] = 10000
        with self.assertRaisesRegex(
            TaskValidationError, "PARAMETER_SCHEMA_MISMATCH"
        ):
            service.create_task(
                project_id=PROJECT_ID,
                principal_id=PRINCIPAL_ID,
                draft=legacy_draft,
                idempotency_key="legacy-limit-rejected",
            )

        current_draft = task_draft()
        current_draft["draft_id"] = "draft-current-limit"
        current_draft["schema_version"] = "1.1.0"
        current_draft["algorithm"]["version"] = "1.5.0"
        current_draft["parameters"].update(
            {"optimizer": "adam", "learning_rate_milli": 10000}
        )
        current_draft["parameters"]["iterations"] = 10000
        created = service.create_task(
            project_id=PROJECT_ID,
            principal_id=PRINCIPAL_ID,
            draft=current_draft,
            idempotency_key="current-limit-accepted",
        )
        self.assertEqual(created.snapshot.draft["parameters"]["iterations"], 10000)

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
        self.assertEqual(schema_errors("algorithm-manifest.schema.json", manifest), [])
        self.assertEqual(manifest["version"], "1.5.0")
        self.assertEqual(manifest["adapter"]["version"], "1.5.0")
        self.assertEqual(manifest["task_types"], ["acoustic_fwi_2d"])
        self.assertEqual(
            manifest["parameter_schema"]["properties"]["preset"]["enum"],
            ["fwi_smoke", "fwi_demo"],
        )
        self.assertIn("optimizer", manifest["parameter_schema"]["required"])
        parameter_validator = Draft7Validator(manifest["parameter_schema"])
        base_parameters = {
            "preset": "fwi_smoke",
            "device": "cuda",
            "iterations": 2,
            "seed": 2026,
        }
        for optimizer, learning_rate_milli in (
            ("adam", 100),
            ("adam", 100_000),
            ("sgd", 100_000_000),
            ("sgd", 1_000_000_000_000),
        ):
            self.assertEqual(
                list(
                    parameter_validator.iter_errors(
                        base_parameters
                        | {
                            "optimizer": optimizer,
                            "learning_rate_milli": learning_rate_milli,
                        }
                    )
                ),
                [],
            )
        for optimizer, learning_rate_milli in (
            ("adam", 100_001),
            ("sgd", 99_999_999),
        ):
            self.assertTrue(
                list(
                    parameter_validator.iter_errors(
                        base_parameters
                        | {
                            "optimizer": optimizer,
                            "learning_rate_milli": learning_rate_milli,
                        }
                    )
                )
            )
        for invalid_parameters in (
            base_parameters | {"iterations": 0, "optimizer": "adam", "learning_rate_milli": 10_000},
            base_parameters | {"seed": 2147483648, "optimizer": "adam", "learning_rate_milli": 10_000},
        ):
            self.assertTrue(list(parameter_validator.iter_errors(invalid_parameters)))
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

    def upgrade_fixture_to_v2(self) -> None:
        migration_path = (
            Path(__file__).parents[1]
            / "scientific_runtime"
            / "migrations"
            / "0002_catalog_registry.sql"
        )
        migration_text = migration_path.read_text(encoding="utf-8")
        connection = sqlite3.connect(self.database_path, isolation_level=None)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("BEGIN IMMEDIATE")
            for statement in _migration_statements(migration_text):
                connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, name, checksum, applied_at)
                VALUES (2, ?, ?, ?)
                """,
                (
                    migration_path.name,
                    hashlib.sha256(migration_text.encode("utf-8")).hexdigest(),
                    NOW,
                ),
            )
            connection.execute("PRAGMA user_version = 2")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def upgrade_fixture_to_v3(self) -> None:
        self.upgrade_fixture_to_v2()
        migration_path = (
            Path(__file__).parents[1]
            / "scientific_runtime"
            / "migrations"
            / "0003_submit_dispatch.sql"
        )
        migration_text = migration_path.read_text(encoding="utf-8")
        connection = sqlite3.connect(self.database_path, isolation_level=None)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("BEGIN IMMEDIATE")
            for statement in _migration_statements(migration_text):
                connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, name, checksum, applied_at)
                VALUES (3, ?, ?, ?)
                """,
                (
                    migration_path.name,
                    hashlib.sha256(migration_text.encode("utf-8")).hexdigest(),
                    NOW,
                ),
            )
            connection.execute("PRAGMA user_version = 3")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def upgrade_fixture_to_v4(self) -> None:
        self.upgrade_fixture_to_v3()
        migration_path = (
            Path(__file__).parents[1]
            / "scientific_runtime"
            / "migrations"
            / "0004_workbench_runtime.sql"
        )
        migration_text = migration_path.read_text(encoding="utf-8")
        connection = sqlite3.connect(self.database_path, isolation_level=None)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("BEGIN IMMEDIATE")
            for statement in _migration_statements(migration_text):
                connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, name, checksum, applied_at)
                VALUES (4, ?, ?, ?)
                """,
                (
                    migration_path.name,
                    hashlib.sha256(migration_text.encode("utf-8")).hexdigest(),
                    NOW,
                ),
            )
            connection.execute("PRAGMA user_version = 4")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def test_v1_database_upgrades_in_place_and_backfills_approval_budget(self) -> None:
        task_id, approval = self.seed_v1_database()
        store = SQLiteTaskStore(self.database_path)
        self.assertEqual(store.migration_version(), 14)
        snapshot = store.get_task(task_id)
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.approval, approval)
        budget = store.get_approval_budget(
            task_id=task_id, approval_id=approval["approval_id"]
        )
        self.assertIsNotNone(budget)
        self.assertEqual((budget.max_tasks, budget.tasks_used), (1, 0))

    def test_v2_database_upgrades_in_place_to_submit_schema(self) -> None:
        task_id, _ = self.seed_v1_database()
        self.upgrade_fixture_to_v2()
        store = SQLiteTaskStore(self.database_path)
        self.assertEqual(store.migration_version(), 14)
        self.assertEqual(store.get_task(task_id).status, "AwaitingApproval")
        connection = sqlite3.connect(self.database_path)
        try:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        finally:
            connection.close()
        self.assertTrue(
            {
                "dispatch_intents",
                "dispatch_attempts",
                "dispatch_outcomes",
                "submit_idempotency_links",
            }.issubset(tables)
        )

    def test_v3_database_upgrades_to_current_and_abandonment_tamper_fails_closed(
        self,
    ) -> None:
        task_id, approval = self.seed_v1_database()
        self.upgrade_fixture_to_v3()
        connection = sqlite3.connect(self.database_path)
        try:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 3)
            self.assertIsNone(
                connection.execute(
                    """
                    SELECT 1 FROM sqlite_master
                    WHERE type = 'table' AND name = 'task_abandonments'
                    """
                ).fetchone()
            )
        finally:
            connection.close()

        store = SQLiteTaskStore(self.database_path)
        self.assertEqual(store.migration_version(), 14)
        snapshot = store.get_task(task_id)
        self.assertEqual(snapshot.approval, approval)
        service = TaskService(
            store,
            task_id_factory=lambda: "task-v3-v6-unapproved",
            clock=lambda: NOW,
        )
        with self.assertRaises(TaskConflict):
            service.abandon_task(
                task_id=task_id,
                project_id=PROJECT_ID,
                principal_id=PRINCIPAL_ID,
                idempotency_key="v3-v6-approved-abandon-blocked",
            )
        draft = copy.deepcopy(snapshot.draft)
        draft["draft_id"] = "draft-v3-v6-unapproved"
        draft["revision"] = 1
        created = store.create_task(
            task_id="task-v3-v6-unapproved",
            draft=draft,
            project_id=PROJECT_ID,
            principal_id=PRINCIPAL_ID,
            idempotency_key="v3-v6-create-unapproved",
            request_hash="sha256:" + "9" * 64,
            now=NOW,
        )
        abandoned_task_id = created.snapshot.task_id
        abandoned = service.abandon_task(
            task_id=abandoned_task_id,
            project_id=PROJECT_ID,
            principal_id=PRINCIPAL_ID,
            idempotency_key="v3-v6-abandon",
        )
        self.assertEqual(abandoned.snapshot.status, "Cancelled")

        connection = sqlite3.connect(self.database_path)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    UPDATE workbench_mutations SET outcome_json = '{}'
                    WHERE task_id = ?
                    """,
                    (abandoned_task_id,),
                )
            connection.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "DELETE FROM task_abandonments WHERE task_id = ?",
                    (abandoned_task_id,),
                )
            connection.rollback()
            connection.execute("DROP TRIGGER task_abandonments_are_immutable")
            connection.execute(
                """
                UPDATE task_abandonments SET document_json = '{}'
                WHERE task_id = ?
                """,
                (abandoned_task_id,),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(TaskStoreCorruption, "task abandonment hash"):
            store.get_task(abandoned_task_id)

    def test_v4_database_upgrades_to_v5_task_discovery_without_rewriting_tasks(
        self,
    ) -> None:
        task_id, approval = self.seed_v1_database()
        self.upgrade_fixture_to_v4()
        connection = sqlite3.connect(self.database_path)
        try:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 4)
            self.assertIsNone(
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = ?",
                    ("idx_tasks_scope_created",),
                ).fetchone()
            )
        finally:
            connection.close()

        store = SQLiteTaskStore(self.database_path)
        self.assertEqual(store.migration_version(), 14)
        snapshot = store.get_task(task_id)
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.approval, approval)
        page = store.list_tasks(
            project_id=PROJECT_ID,
            principal_id=PRINCIPAL_ID,
            limit=20,
        )
        self.assertEqual(
            [listed.task_id for listed in page.snapshots], [task_id]
        )
        self.assertIsNone(page.next_cursor)
        connection = sqlite3.connect(self.database_path)
        try:
            self.assertIsNotNone(
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = ?",
                    ("idx_tasks_scope_created",),
                ).fetchone()
            )
        finally:
            connection.close()

    def test_v3_upgrade_rejects_unexplainable_v2_runtime_state_atomically(self) -> None:
        task_id, _ = self.seed_v1_database()
        self.upgrade_fixture_to_v2()
        event = run_event()
        event.update(
            {
                "event_id": "event-v2-queued",
                "sequence": 1,
                "task_id": task_id,
                "event_type": "task_queued",
                "task_status": "Queued",
            }
        )
        event.pop("node_id", None)
        event_json, event_hash = encode_document(event)
        _, fingerprint_hash = encode_document(event["fingerprint"])
        connection = sqlite3.connect(self.database_path, isolation_level=None)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO run_events(
                    task_id, sequence, event_id, event_type, task_status,
                    node_id, fingerprint_hash, document_json, document_hash,
                    occurred_at, recorded_at
                ) VALUES (?, 1, ?, 'task_queued', 'Queued', NULL, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    event["event_id"],
                    fingerprint_hash,
                    event_json,
                    event_hash,
                    event["occurred_at"],
                    NOW,
                ),
            )
            connection.execute(
                "UPDATE tasks SET status = 'Queued', updated_at = ? WHERE task_id = ?",
                (NOW, task_id),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(TaskStoreCorruption):
            SQLiteTaskStore(self.database_path)
        connection = sqlite3.connect(self.database_path)
        try:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertIsNone(
                connection.execute(
                    """
                    SELECT 1 FROM sqlite_master
                    WHERE type = 'table' AND name = 'dispatch_intents'
                    """
                ).fetchone()
            )
        finally:
            connection.close()

    def test_concurrent_v1_upgrade_converges_without_duplicate_backfill(self) -> None:
        task_id, approval = self.seed_v1_database()

        def reopen(_: int) -> tuple[int, str]:
            store = SQLiteTaskStore(self.database_path)
            snapshot = store.get_task(task_id)
            return store.migration_version(), snapshot.approval["approval_id"]

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(reopen, range(8)))
        self.assertEqual(results, [(14, approval["approval_id"])] * 8)
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
                    "SELECT COUNT(*) FROM schema_migrations WHERE version = 5"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM schema_migrations WHERE version = 6"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM schema_migrations WHERE version = 7"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM schema_migrations WHERE version = 8"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM schema_migrations WHERE version = 9"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM schema_migrations WHERE version = 10"
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
