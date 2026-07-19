from __future__ import annotations

import base64
import copy
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scientific_runtime.registry_service import RegistryService
from scientific_runtime.fwi_registry import load_deepwave_manifest
from scientific_runtime.task_dispatcher import DispatchPreparation
from scientific_runtime.task_service import TaskCancellationResult, TaskService
from scientific_runtime.task_store import (
    SQLiteTaskStore,
    TaskCancellationSnapshot,
    TaskCheckpointSnapshot,
    TaskTimeoutSnapshot,
)
from scientific_runtime.workbench_service import (
    GuidedWorkbench,
    WorkbenchConflict,
    WorkbenchNotFound,
    WorkbenchRuntimeError,
    WorkbenchValidationError,
    _stable_id,
)
from scientific_runtime_contracts import (
    compute_plan_hash,
    extract_plan_data_edges,
    schema_errors,
)
from tests.test_scientific_runtime_contracts import (
    artifact_manifest,
    dataset_ref,
    fingerprint,
)


NOW = "2026-07-15T03:00:00Z"
PROJECT_ID = "project-1"
PRINCIPAL_ID = "user-1"


def guided_form(**changes):
    value = {
        "goal": "Run the registered Marmousi Deepwave FWI smoke baseline.",
        "dataset_id": "marmousi_94_288",
        "dataset_version": "1.0.0",
        "preset": "fwi_smoke",
        "device": "cuda",
        "iterations": 2,
        "seed": 2026,
        "optimizer": "adam",
        "learning_rate": "10",
    }
    value.update(changes)
    return value


def legacy_guided_form(**changes):
    value = guided_form(**changes)
    value.pop("optimizer")
    value.pop("learning_rate")
    return value


def recipe_guided_form(**changes):
    value = guided_form(
        goal="Run the fixed forward, quality-check, and FWI Recipe."
    )
    value.update(
        recipe_id="forward_qc_fwi",
        recipe_version="1.0.0",
    )
    value.update(changes)
    return value


def development_fingerprint() -> dict:
    value = fingerprint()
    value["algorithm"]["version"] = "1.6.0"
    value["adapter_version"] = "1.6.0"
    value["provenance_mode"] = "development"
    value["source"] = {"identity_complete": False, "dirty": None}
    return value


class FakeDispatcher:
    def __init__(self):
        self.prepare_calls = 0
        self.dispatch_calls = 0
        self.status_calls = 0
        self.lock = threading.Lock()

    def prepare(self, snapshot):
        with self.lock:
            self.prepare_calls += 1
        request = TaskService._expected_dispatch_request(snapshot)
        queue_fingerprint = development_fingerprint()
        request["normalized_config_hash"] = queue_fingerprint[
            "normalized_config_hash"
        ]
        return DispatchPreparation(
            adapter_id="fwi.deepwave_adapter",
            adapter_version="1.6.0",
            request=request,
            queue_fingerprint=queue_fingerprint,
        )

    def dispatch(self, intent):
        with self.lock:
            self.dispatch_calls += 1
        return {
            "submission_id": "submission-workbench-test",
            "task_id": intent.task_id,
            "node_id": intent.node_id,
            "job_id": "fwi-workbench-test-job",
            "idempotency_key": intent.node_idempotency_key,
            "plan_hash": intent.plan_hash,
            "request_hash": "sha256:" + "a" * 64,
            "algorithm": copy.deepcopy(intent.request["algorithm"]),
            "fingerprint": development_fingerprint(),
            "adapter_version": intent.adapter_version,
        }

    def status(self, intent):
        with self.lock:
            self.status_calls += 1
        return {
            "job_id": "fwi-workbench-test-job",
            "task_id": intent.task_id,
            "node_id": intent.node_id,
            "status": "Queued",
            "stage": "queued",
            "completed": 0,
            "total": 2,
            "message": "queued",
            "updated_at": NOW,
            "terminal": False,
        }

    def collect(self, intent):
        value = artifact_manifest()
        value["task_id"] = intent.task_id
        value["lineage"]["plan_hash"] = intent.plan_hash
        return [value]

    def read_artifact(self, intent, artifact_id):
        values = self.collect(intent)
        if artifact_id != values[0]["artifact_id"]:
            raise AssertionError("unexpected artifact id")
        return values, values[0], b"artifact-test-bytes"


class ScientificRuntimeWorkbenchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = SQLiteTaskStore(Path(self.temporary.name) / "tasks.sqlite3")
        self.registry = RegistryService(self.store, clock=lambda: NOW)
        self.registry.register_dataset(dataset=dataset_ref())
        self.registry.register_algorithm(manifest=load_deepwave_manifest())
        self.dispatcher = FakeDispatcher()
        self.task_number = 0

        def task_id_factory():
            self.task_number += 1
            return f"task-workbench-{self.task_number:04d}"

        self.clock_tick = 0

        def task_clock():
            value = datetime(2026, 7, 15, 3, tzinfo=timezone.utc) + timedelta(
                seconds=self.clock_tick
            )
            self.clock_tick += 1
            return value.isoformat().replace("+00:00", "Z")

        self.tasks = TaskService(
            self.store,
            task_id_factory=task_id_factory,
            clock=task_clock,
            dispatcher=self.dispatcher,
        )
        self.workbench = GuidedWorkbench(
            self.tasks,
            self.registry,
            project_id=PROJECT_ID,
            principal_id=PRINCIPAL_ID,
            enable_fixed_recipe_dag=True,
            clock=task_clock,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def legacy_draft(
        self,
        *,
        version: str,
        form: dict,
        draft_id: str,
        revision: int,
    ) -> dict:
        manifest = load_deepwave_manifest(version)
        self.registry.register_algorithm(manifest=manifest)
        dataset = self.registry.get_dataset(
            project_id=PROJECT_ID,
            principal_id=PRINCIPAL_ID,
            dataset_id="marmousi_94_288",
            version="1.0.0",
            permission="execute",
        )
        return {
            "schema_version": "1.0.0",
            "draft_id": draft_id,
            "revision": revision,
            "status": "AwaitingApproval",
            "goal": form["goal"],
            "task_type": "acoustic_fwi_2d",
            "datasets": [copy.deepcopy(dataset)],
            "algorithm": {"id": manifest["id"], "version": version},
            "parameters": {
                "preset": form["preset"],
                "device": form["device"],
                "iterations": form["iterations"],
                "seed": form["seed"],
            },
            "resources": self.workbench._resources(form, manifest),
            "missing_fields": [],
            "suggestions": [
                "Review the fixed dataset, parameters, resources, and "
                "synthetic-workflow limits before approval."
            ],
            "confidence": {
                "intent": 1.0,
                "parameters": 1.0,
                "datasets": 1.0,
                "explanation": (
                    "All executable values came from the validated Guided form "
                    "and Registry snapshots."
                ),
            },
            "extensions": {},
        }

    def historical_optimizer_draft(
        self,
        *,
        version: str,
        form: dict,
        draft_id: str,
        revision: int,
    ) -> tuple[dict, dict]:
        manifest = load_deepwave_manifest(version)
        self.registry.register_algorithm(manifest=manifest)
        normalized, dataset, _, _ = self.workbench._validated_form(form)
        return (
            self.workbench._draft(
                form=normalized,
                dataset=dataset,
                manifest=manifest,
                draft_id=draft_id,
                revision=revision,
            ),
            manifest,
        )

    def test_capabilities_and_catalog_are_fixed_scoped_and_path_free(self) -> None:
        capabilities = self.workbench.session_capabilities()
        self.assertEqual(capabilities["mode"], "guided")
        self.assertEqual(
            capabilities["scope"],
            {"project_id": PROJECT_ID, "principal_id": PRINCIPAL_ID},
        )
        self.assertTrue(capabilities["features"]["running_cancel"])
        self.assertTrue(capabilities["features"]["runtime_timeout"])
        self.assertTrue(capabilities["features"]["checkpoint_wait_resume"])
        self.assertTrue(
            capabilities["features"]["positive_receipt_reconciliation"]
        )
        self.assertTrue(
            capabilities["features"]["exact_negative_reconciliation"]
        )
        self.assertNotIn("can_timeout", capabilities["features"])
        self.assertNotIn("timeout", capabilities["capabilities"])
        self.assertFalse(capabilities["features"]["automatic_reconciliation"])
        self.assertFalse(capabilities["features"]["startup_dispatch_recovery"])
        self.assertFalse(capabilities["features"]["startup_receipt_recovery"])
        self.assertFalse(capabilities["features"]["startup_status_catchup"])
        self.assertTrue(
            capabilities["features"]["supervised_runtime_scheduling"]
        )
        self.assertTrue(
            capabilities["features"]["continuous_status_supervision"]
        )
        self.assertTrue(capabilities["features"]["supervisor_leases"])
        self.assertEqual(
            capabilities["form"]["iterations"], {"minimum": 1, "maximum": 10000}
        )
        self.assertEqual(capabilities["form"]["optimizers"], ["adam", "sgd"])
        self.assertEqual(
            capabilities["form"]["learning_rate"],
            {
                "representation": "decimal_string",
                "scale": 1000,
                "bounds": {
                    "adam": {"minimum": "0.1", "maximum": "100"},
                    "sgd": {"minimum": "100000", "maximum": "1000000000"},
                },
            },
        )
        self.assertEqual(
            capabilities["form"]["gradient_clip_quantile"],
            {"value": "0.98", "editable": False},
        )
        self.assertEqual(
            [profile["recommendation"] for profile in capabilities["form"]["optimization_profiles"]],
            ["recommended", "conservative", "experimental"],
        )
        self.assertIn(
            "CUDA 两步 finite/model-update 校准已通过",
            capabilities["form"]["optimization_profiles"][2]["evidence"],
        )
        self.assertEqual(
            capabilities["algorithm"],
            {"id": "deepwave.acoustic_fwi", "version": "1.6.0"},
        )
        self.assertTrue(capabilities["features"]["streaming_events"])
        self.assertEqual(
            capabilities["capabilities"],
            {
                "cancel": True,
                "retry": False,
                "manual_retry": False,
                "finite_automatic_retry": {
                    "max_attempts": 2,
                    "max_concurrent_attempts": 1,
                    "pre_running_launch_failure": True,
                    "worker_exit": True,
                },
                "checkpoint_resume": {
                    "automatic": True,
                    "browser_mutation": False,
                    "same_attempt": True,
                    "capacity_released_while_waiting": False,
                },
                "sse": True,
                "startup_dispatch_recovery": False,
                "startup_receipt_recovery": False,
                "startup_status_catchup": False,
                "supervised_runtime_scheduling": True,
                "continuous_status_supervision": True,
                "supervisor_leases": True,
                "positive_receipt_reconciliation": True,
                "exact_negative_reconciliation": True,
                "automatic_reconciliation": False,
                "dag": True,
            },
        )
        self.assertEqual(
            [(item["id"], item["version"]) for item in capabilities["recipes"]],
            [("forward_qc_fwi", "1.0.0")],
        )
        self.assertEqual(
            capabilities["form"]["recipe_selector_fields"],
            ["recipe_id", "recipe_version"],
        )

        catalog = self.workbench.list_catalog()
        self.assertEqual(len(catalog["datasets"]), 1)
        self.assertEqual(catalog["datasets"][0]["id"], "marmousi_94_288")
        self.assertNotIn("access_scope", catalog["datasets"][0])
        self.assertEqual(len(catalog["algorithms"]), 1)
        self.assertEqual(catalog["algorithms"][0]["version"], "1.6.0")
        self.assertEqual(
            catalog["algorithms"][0]["adapter"],
            {"protocol": "algorithm-adapter-v1", "version": "1.6.0"},
        )
        serialized = repr(catalog)
        self.assertNotIn("entrypoint_ref", serialized)
        self.assertNotIn("/root/", serialized)
        self.assertEqual(catalog["recipes"][0]["id"], "forward_qc_fwi")
        self.assertEqual(
            [stage["node_id"] for stage in catalog["recipes"][0]["stages"]],
            ["data_check", "forward", "quality_check", "fwi", "result_check"],
        )

    def test_dag_capability_requires_explicit_production_composition(self) -> None:
        ungated = GuidedWorkbench(
            self.tasks,
            self.registry,
            project_id=PROJECT_ID,
            principal_id=PRINCIPAL_ID,
            clock=lambda: NOW,
        )

        capabilities = ungated.session_capabilities()
        self.assertFalse(capabilities["capabilities"]["dag"])
        self.assertEqual(capabilities["recipes"], [])
        self.assertEqual(capabilities["form"]["recipe_selector_fields"], [])
        self.assertEqual(ungated.list_catalog()["recipes"], [])
        with self.assertRaises(WorkbenchRuntimeError) as caught:
            ungated.create_task(
                recipe_guided_form(),
                "ungated-recipe-must-fail-closed",
            )
        self.assertEqual(caught.exception.code, "GUIDED_CAPABILITY_UNAVAILABLE")

    def test_startup_recovery_is_internal_bounded_and_scope_bound(self) -> None:
        calls = []
        marker = object()
        original = self.tasks.recover_runtime_on_startup

        def recover_runtime_on_startup(**kwargs):
            calls.append(kwargs)
            return marker

        self.tasks.recover_runtime_on_startup = recover_runtime_on_startup
        try:
            self.assertIs(
                self.workbench.recover_runtime_on_startup(max_tasks=321), marker
            )
        finally:
            self.tasks.recover_runtime_on_startup = original
        self.assertEqual(
            calls,
            [
                {
                    "project_id": PROJECT_ID,
                    "principal_id": PRINCIPAL_ID,
                    "max_tasks": 321,
                }
            ],
        )
        for invalid in (True, 0, 10001, "100"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(WorkbenchValidationError):
                    self.workbench.recover_runtime_on_startup(invalid)

    def test_create_composes_schema_valid_draft_and_single_node_plan(self) -> None:
        result = self.workbench.create_task(guided_form(), "http-create-001")
        snapshot = self.store.get_task(result["task_id"])
        self.assertIsNotNone(snapshot)
        self.assertEqual(schema_errors("task-draft.schema.json", snapshot.draft), [])
        self.assertEqual(schema_errors("plan-graph.schema.json", snapshot.plan), [])
        self.assertEqual(snapshot.plan["plan_hash"], result["plan"]["plan_hash"])
        self.assertEqual(len(snapshot.plan["nodes"]), 1)
        self.assertEqual(snapshot.plan["nodes"][0]["node_id"], "invert")
        self.assertEqual(snapshot.plan["nodes"][0]["dependencies"], [])
        self.assertEqual(len(snapshot.plan["nodes"][0]["outputs"]), 8)
        self.assertEqual(snapshot.draft["resources"]["gpu_count"], 1)
        self.assertEqual(snapshot.draft["schema_version"], "1.1.0")
        self.assertEqual(snapshot.plan["schema_version"], "1.1.0")
        self.assertEqual(snapshot.draft["parameters"]["optimizer"], "adam")
        self.assertEqual(snapshot.draft["parameters"]["learning_rate_milli"], 10000)
        self.assertNotIn("idempotency_key", result["plan"]["nodes"][0])
        self.assertIsNone(result["dispatch"])
        self.assertIsNone(result["recipe"])

    def test_explicit_recipe_composes_exact_fan_out_fan_in_plan(self) -> None:
        result = self.workbench.create_task(
            recipe_guided_form(device="cpu", iterations=1),
            "recipe-create-001",
        )
        snapshot = self.store.get_task(result["task_id"])
        self.assertEqual(schema_errors("task-draft.schema.json", snapshot.draft), [])
        self.assertEqual(schema_errors("plan-graph.schema.json", snapshot.plan), [])
        self.assertEqual(snapshot.plan["schema_version"], "1.2.0")
        self.assertEqual(
            snapshot.draft["extensions"],
            {"org.agent_rpc.recipe": {"id": "forward_qc_fwi", "version": "1.0.0"}},
        )
        self.assertEqual(snapshot.plan["extensions"], snapshot.draft["extensions"])
        self.assertEqual(
            [(node["node_id"], node["dependencies"]) for node in snapshot.plan["nodes"]],
            [
                ("data_check", []),
                ("forward", ["data_check"]),
                ("quality_check", ["data_check"]),
                ("fwi", ["forward", "quality_check"]),
                ("result_check", ["fwi"]),
            ],
        )
        self.assertEqual(
            [
                (
                    edge.target_node_id,
                    edge.target_input_port,
                    edge.source_node_id,
                    edge.source_output_port,
                )
                for edge in extract_plan_data_edges(snapshot.plan)
            ],
            [
                ("forward", "checked_model", "data_check", "inverted_model"),
                ("fwi", "forward_evidence", "forward", "shot_gathers_figure"),
                ("fwi", "quality_evidence", "quality_check", "model_error_figure"),
                ("quality_check", "dataset_quality", "data_check", "loss"),
                ("result_check", "fwi_loss", "fwi", "loss"),
                ("result_check", "fwi_model", "fwi", "inverted_model"),
            ],
        )
        self.assertTrue(
            all(
                node["algorithm"]
                == {"id": "deepwave.acoustic_fwi", "version": "1.6.0"}
                and len(node["outputs"]) == 8
                for node in snapshot.plan["nodes"]
            )
        )
        self.assertEqual(result["recipe"]["version"], "1.0.0")
        self.assertTrue(
            all(node["adapter"]["version"] == "1.6.0" for node in result["plan"]["nodes"])
        )
        self.assertEqual(len(result["runtime_nodes"]), 5)
        self.assertTrue(
            all(node["status"] == "Pending" for node in result["runtime_nodes"])
        )
        self.assertTrue(
            all(
                node["wait_reason"] == "approval_required"
                and set(node)
                == {
                    "node_id",
                    "label",
                    "status",
                    "dependencies",
                    "algorithm",
                    "adapter",
                    "parameters",
                    "resources",
                    "wait_reason",
                    "cache",
                    "checkpoint",
                    "failure",
                    "lineage",
                    "outputs",
                }
                for node in result["runtime_nodes"]
            )
        )
        self.assertIsNone(result["dispatch"])

    def test_ordinary_guided_remains_single_node_with_many_registered_algorithms(self) -> None:
        for version in ("1.0.0", "1.1.0", "1.2.0", "1.3.0", "1.4.0", "1.5.0"):
            self.registry.register_algorithm(manifest=load_deepwave_manifest(version))
        result = self.workbench.create_task(
            guided_form(
                goal="Run forward checks and FWI, but no Recipe was selected."
            ),
            "ordinary-many-algorithms",
        )
        self.assertIsNone(result["recipe"])
        self.assertEqual(
            [node["node_id"] for node in result["plan"]["nodes"]], ["invert"]
        )
        self.assertEqual(result["runtime_nodes"], [])

        with self.assertRaises(WorkbenchValidationError) as caught:
            self.workbench.create_task(
                recipe_guided_form(recipe_version="2.0.0"),
                "unsupported-recipe",
            )
        self.assertEqual(caught.exception.code, "RECIPE_UNSUPPORTED")

    def test_recipe_plan_hash_covers_nodes_dependencies_parameters_and_old_approval(self) -> None:
        created = self.workbench.create_task(
            recipe_guided_form(device="cpu", iterations=1),
            "recipe-hash-create",
        )
        snapshot = self.store.get_task(created["task_id"])
        baseline = snapshot.plan["plan_hash"]
        for label, mutate in (
            ("node", lambda plan: plan["nodes"][0].update(node_id="data_check_changed")),
            ("dependency", lambda plan: plan["nodes"][3]["dependencies"].reverse()),
            ("parameter", lambda plan: plan["nodes"][3]["parameters"].update(iterations=2)),
        ):
            with self.subTest(label=label):
                changed = copy.deepcopy(snapshot.plan)
                mutate(changed)
                changed["plan_hash"] = "sha256:" + "0" * 64
                self.assertNotEqual(compute_plan_hash(changed), baseline)

        approved = self.workbench.approve_and_submit(
            created["task_id"], baseline, "recipe-hash-approve"
        )
        self.assertTrue(approved["submitted"])
        self.assertFalse(approved["dispatch_attempted"])
        self.assertEqual(self.dispatcher.dispatch_calls, 0)
        revised = self.workbench.revise_task(
            created["task_id"],
            1,
            recipe_guided_form(device="cpu", iterations=2),
            "recipe-hash-revise",
        )
        self.assertNotEqual(revised["plan"]["plan_hash"], baseline)
        self.assertIsNone(revised["approval"])
        with self.assertRaises(WorkbenchConflict) as caught:
            self.workbench.approve_and_submit(
                created["task_id"], baseline, "recipe-hash-old-approval"
            )
        self.assertEqual(caught.exception.code, "PLAN_HASH_CONFLICT")

    def test_recipe_projects_no_task_wide_cancel_and_rejects_cancel_before_service(
        self,
    ) -> None:
        created = self.workbench.create_task(
            recipe_guided_form(device="cpu", iterations=1),
            "recipe-cancel-create",
        )
        approved = self.workbench.approve_and_submit(
            created["task_id"],
            created["plan"]["plan_hash"],
            "recipe-cancel-approve",
        )
        self.assertFalse(approved["can_cancel"])

        original_cancel = self.tasks.cancel_task

        def unexpected_cancel(**_kwargs):
            self.fail("fixed Recipe cancellation must be rejected at the facade")

        self.tasks.cancel_task = unexpected_cancel
        try:
            with self.assertRaises(WorkbenchConflict) as caught:
                self.workbench.cancel_task(
                    created["task_id"], "recipe-cancel-key", "user_requested"
                )
        finally:
            self.tasks.cancel_task = original_cancel
        self.assertEqual(caught.exception.code, "RECIPE_CANCEL_UNAVAILABLE")

    def test_create_lost_response_replay_is_stable_and_conflict_is_closed(self) -> None:
        first = self.workbench.create_task(guided_form(), "http-create-replay")
        second = self.workbench.create_task(guided_form(), "http-create-replay")
        self.assertEqual(first["task_id"], second["task_id"])
        self.assertEqual(first["draft"]["draft_id"], second["draft"]["draft_id"])
        self.assertEqual(first["plan"]["plan_id"], second["plan"]["plan_id"])
        self.assertEqual(first["plan"]["plan_hash"], second["plan"]["plan_hash"])
        self.assertTrue(second["replayed"])

        with self.assertRaises(WorkbenchConflict) as caught:
            self.workbench.create_task(
                guided_form(iterations=3), "http-create-replay"
            )
        self.assertEqual(caught.exception.code, "IDEMPOTENCY_CONFLICT")

    def test_seven_field_create_replays_exact_legacy_versions_and_completes_old_plan(self) -> None:
        for version in ("1.0.0", "1.1.0"):
            with self.subTest(version=version):
                key = f"legacy-create-{version}"
                form = legacy_guided_form()
                draft_id = _stable_id(
                    "draft", PROJECT_ID, PRINCIPAL_ID, 1, key
                )
                old_draft = self.legacy_draft(
                    version=version,
                    form=form,
                    draft_id=draft_id,
                    revision=1,
                )
                seeded = self.tasks.create_task(
                    draft=old_draft,
                    idempotency_key=self.workbench._mutation_key("create", key),
                    project_id=PROJECT_ID,
                    principal_id=PRINCIPAL_ID,
                )
                self.assertIsNone(seeded.snapshot.plan)
                before_task_number = self.task_number

                replay = self.workbench.create_task(form, key)
                snapshot = self.store.get_task(seeded.snapshot.task_id)
                self.assertTrue(replay["replayed"])
                self.assertEqual(replay["task_id"], seeded.snapshot.task_id)
                self.assertEqual(self.task_number, before_task_number)
                self.assertEqual(snapshot.draft, old_draft)
                self.assertEqual(snapshot.plan["schema_version"], "1.0.0")
                self.assertEqual(snapshot.plan["nodes"][0]["algorithm"]["version"], version)
                self.assertEqual(snapshot.plan["nodes"][0]["parameters"], old_draft["parameters"])

                exact_again = self.workbench.create_task(form, key)
                self.assertTrue(exact_again["replayed"])
                self.assertEqual(exact_again["plan"], replay["plan"])
                with self.assertRaises(WorkbenchConflict) as caught:
                    self.workbench.create_task(
                        dict(form, goal="different durable request"), key
                    )
                self.assertEqual(caught.exception.code, "IDEMPOTENCY_CONFLICT")

        new_key = "seven-field-current-create"
        current = self.workbench.create_task(legacy_guided_form(), new_key)
        current_snapshot = self.store.get_task(current["task_id"])
        self.assertFalse(current["replayed"])
        self.assertEqual(current_snapshot.draft["algorithm"]["version"], "1.6.0")
        self.assertEqual(current_snapshot.draft["parameters"]["optimizer"], "adam")
        self.assertEqual(current_snapshot.draft["parameters"]["learning_rate_milli"], 10_000)
        current_replay = self.workbench.create_task(legacy_guided_form(), new_key)
        self.assertTrue(current_replay["replayed"])
        self.assertEqual(current_replay["task_id"], current["task_id"])

    def test_seven_field_create_and_revise_replay_old_hash_after_later_revision(self) -> None:
        create_key = "legacy-delayed-create"
        create_form = legacy_guided_form()
        draft_id = _stable_id("draft", PROJECT_ID, PRINCIPAL_ID, 1, create_key)
        initial = self.legacy_draft(
            version="1.1.0",
            form=create_form,
            draft_id=draft_id,
            revision=1,
        )
        created = self.tasks.create_task(
            draft=initial,
            idempotency_key=self.workbench._mutation_key("create", create_key),
            project_id=PROJECT_ID,
            principal_id=PRINCIPAL_ID,
        )

        revise_key = "legacy-delayed-revise"
        revise_form = legacy_guided_form(device="cpu", iterations=3, seed=7)
        revision = self.legacy_draft(
            version="1.1.0",
            form=revise_form,
            draft_id=draft_id,
            revision=2,
        )
        self.tasks.revise_draft(
            task_id=created.snapshot.task_id,
            expected_revision=1,
            draft=revision,
            idempotency_key=self.workbench._mutation_key("revise", revise_key),
            project_id=PROJECT_ID,
            principal_id=PRINCIPAL_ID,
        )

        delayed_create = self.workbench.create_task(create_form, create_key)
        self.assertTrue(delayed_create["replayed"])
        self.assertEqual(delayed_create["draft"]["revision"], 2)
        self.assertIsNone(delayed_create["plan"])

        delayed_revision = self.workbench.revise_task(
            created.snapshot.task_id, 1, revise_form, revise_key
        )
        self.assertTrue(delayed_revision["replayed"])
        self.assertEqual(delayed_revision["draft"]["revision"], 2)
        self.assertEqual(delayed_revision["draft"]["algorithm"]["version"], "1.1.0")
        self.assertEqual(delayed_revision["plan"]["nodes"][0]["parameters"], revision["parameters"])
        self.assertEqual(
            self.store.get_task(created.snapshot.task_id).plan["schema_version"],
            "1.0.0",
        )

        with self.assertRaises(WorkbenchConflict) as caught:
            self.workbench.revise_task(
                created.snapshot.task_id,
                1,
                dict(revise_form, iterations=4),
                revise_key,
            )
        self.assertEqual(caught.exception.code, "IDEMPOTENCY_CONFLICT")

        other = self.workbench.create_task(
            guided_form(), "legacy-revise-other-task-create"
        )
        with self.assertRaises(WorkbenchConflict) as caught:
            self.workbench.revise_task(other["task_id"], 1, revise_form, revise_key)
        self.assertEqual(caught.exception.code, "IDEMPOTENCY_CONFLICT")

        current = self.workbench.revise_task(
            created.snapshot.task_id,
            2,
            legacy_guided_form(iterations=4, seed=8),
            "seven-field-current-revise",
        )
        self.assertFalse(current["replayed"])
        self.assertEqual(current["draft"]["revision"], 3)
        self.assertEqual(current["draft"]["algorithm"]["version"], "1.6.0")
        self.assertEqual(current["draft"]["parameters"]["optimizer"], "adam")
        current_replay = self.workbench.revise_task(
            created.snapshot.task_id,
            2,
            legacy_guided_form(iterations=4, seed=8),
            "seven-field-current-revise",
        )
        self.assertTrue(current_replay["replayed"])
        self.assertEqual(current_replay["draft"]["revision"], 3)

    def test_optimizer_form_replays_v1_2_v1_3_create_and_revise_ledgers(self) -> None:
        for version in ("1.2.0", "1.3.0"):
            for input_style in ("nine-field", "expanded-seven-field"):
                with self.subTest(version=version, input_style=input_style):
                    form_factory = (
                        guided_form
                        if input_style == "nine-field"
                        else legacy_guided_form
                    )
                    suffix = f"{version}-{input_style}"
                    create_key = f"optimizer-history-create-{suffix}"
                    create_form = form_factory()
                    draft_id = _stable_id(
                        "draft", PROJECT_ID, PRINCIPAL_ID, 1, create_key
                    )
                    old_draft, old_manifest = self.historical_optimizer_draft(
                        version=version,
                        form=create_form,
                        draft_id=draft_id,
                        revision=1,
                    )
                    seeded = self.tasks.create_task(
                        draft=old_draft,
                        idempotency_key=self.workbench._mutation_key(
                            "create", create_key
                        ),
                        project_id=PROJECT_ID,
                        principal_id=PRINCIPAL_ID,
                    )
                    self.assertIsNone(seeded.snapshot.plan)

                    create_replay = self.workbench.create_task(
                        create_form, create_key
                    )
                    create_snapshot = self.store.get_task(
                        seeded.snapshot.task_id
                    )
                    self.assertTrue(create_replay["replayed"])
                    self.assertEqual(create_snapshot.draft, old_draft)
                    self.assertEqual(
                        create_snapshot.plan["nodes"][0]["outputs"],
                        old_manifest["outputs"],
                    )
                    self.assertEqual(
                        len(create_snapshot.plan["nodes"][0]["outputs"]), 2
                    )
                    with self.assertRaises(WorkbenchConflict) as caught:
                        self.workbench.create_task(
                            form_factory(goal="different durable request"),
                            create_key,
                        )
                    self.assertEqual(
                        caught.exception.code, "IDEMPOTENCY_CONFLICT"
                    )

                    revise_key = f"optimizer-history-revise-{suffix}"
                    revise_form = form_factory(
                        device="cpu", iterations=3, seed=7
                    )
                    old_revision, _ = self.historical_optimizer_draft(
                        version=version,
                        form=revise_form,
                        draft_id=draft_id,
                        revision=2,
                    )
                    self.tasks.revise_draft(
                        task_id=seeded.snapshot.task_id,
                        expected_revision=1,
                        draft=old_revision,
                        idempotency_key=self.workbench._mutation_key(
                            "revise", revise_key
                        ),
                        project_id=PROJECT_ID,
                        principal_id=PRINCIPAL_ID,
                    )

                    revise_replay = self.workbench.revise_task(
                        seeded.snapshot.task_id,
                        1,
                        revise_form,
                        revise_key,
                    )
                    revise_snapshot = self.store.get_task(
                        seeded.snapshot.task_id
                    )
                    self.assertTrue(revise_replay["replayed"])
                    self.assertEqual(revise_snapshot.draft, old_revision)
                    self.assertEqual(
                        revise_snapshot.plan["nodes"][0]["outputs"],
                        old_manifest["outputs"],
                    )
                    self.assertEqual(
                        len(revise_snapshot.plan["nodes"][0]["outputs"]), 2
                    )
                    with self.assertRaises(WorkbenchConflict) as caught:
                        self.workbench.revise_task(
                            seeded.snapshot.task_id,
                            1,
                            form_factory(device="cpu", iterations=4, seed=7),
                            revise_key,
                        )
                    self.assertEqual(
                        caught.exception.code, "IDEMPOTENCY_CONFLICT"
                    )

    def test_form_rejects_browser_execution_controls_and_boolean_integers(self) -> None:
        for forbidden in ("path", "shell", "handle", "job_id", "extra_args"):
            with self.subTest(forbidden=forbidden):
                with self.assertRaises(WorkbenchValidationError) as caught:
                    self.workbench.create_task(
                        guided_form(**{forbidden: "/tmp/untrusted"}),
                        f"forbidden-{forbidden}",
                    )
                self.assertEqual(caught.exception.code, "INVALID_FORM_FIELDS")
        with self.assertRaises(WorkbenchValidationError) as caught:
            self.workbench.create_task(guided_form(iterations=True), "bad-bool")
        self.assertEqual(caught.exception.code, "ITERATIONS_OUT_OF_RANGE")

    def test_iteration_upper_bound_creates_only_a_pre_runtime_plan(self) -> None:
        result = self.workbench.create_task(
            guided_form(iterations=10000), "create-max-iterations"
        )
        snapshot = self.store.get_task(result["task_id"])
        self.assertEqual(snapshot.status, "AwaitingApproval")
        self.assertEqual(snapshot.draft["parameters"]["iterations"], 10000)
        self.assertEqual(snapshot.draft["resources"]["wall_time_seconds"], 7200)
        self.assertIsNone(self.store.get_dispatch_intent(result["task_id"]))

        for index, value in enumerate((0, 10001, -3, 2.5, "10000", True)):
            with self.subTest(iterations=value):
                with self.assertRaises(WorkbenchValidationError) as caught:
                    self.workbench.create_task(
                        guided_form(iterations=value), f"bad-iterations-{index}"
                    )
                self.assertEqual(caught.exception.code, "ITERATIONS_OUT_OF_RANGE")

    def test_optimizer_profiles_are_hash_bound_and_invalid_values_create_nothing(self) -> None:
        sgd = self.workbench.create_task(
            guided_form(optimizer="sgd", learning_rate="10000000"),
            "create-sgd-profile",
        )
        snapshot = self.store.get_task(sgd["task_id"])
        self.assertEqual(
            snapshot.draft["parameters"],
            {
                "preset": "fwi_smoke",
                "device": "cuda",
                "iterations": 2,
                "seed": 2026,
                "optimizer": "sgd",
                "learning_rate_milli": 10_000_000_000,
            },
        )
        self.assertEqual(
            snapshot.plan["nodes"][0]["parameters"],
            snapshot.draft["parameters"],
        )
        self.assertIn("experimental", " ".join(snapshot.draft["suggestions"]).lower())
        before = self.task_number

        invalid = (
            ({"optimizer": "rmsprop"}, "OPTIMIZER_UNSUPPORTED"),
            ({"optimizer": True}, "OPTIMIZER_UNSUPPORTED"),
            ({"learning_rate": 10}, "LEARNING_RATE_INVALID"),
            ({"learning_rate": "01"}, "LEARNING_RATE_INVALID"),
            ({"learning_rate": "1e1"}, "LEARNING_RATE_INVALID"),
            ({"learning_rate": "+10"}, "LEARNING_RATE_INVALID"),
            ({"learning_rate": "10.0000"}, "LEARNING_RATE_INVALID"),
            ({"learning_rate": "0.099"}, "LEARNING_RATE_OUT_OF_RANGE"),
            ({"learning_rate": "100.001"}, "LEARNING_RATE_OUT_OF_RANGE"),
            (
                {"optimizer": "sgd", "learning_rate": "99999.999"},
                "LEARNING_RATE_OUT_OF_RANGE",
            ),
            (
                {"optimizer": "sgd", "learning_rate": "1000000000.001"},
                "LEARNING_RATE_OUT_OF_RANGE",
            ),
        )
        for index, (changes, code) in enumerate(invalid):
            with self.subTest(changes=changes):
                with self.assertRaises(WorkbenchValidationError) as caught:
                    self.workbench.create_task(
                        guided_form(**changes), f"invalid-optimizer-{index}"
                    )
                self.assertEqual(caught.exception.code, code)
        self.assertEqual(self.task_number, before)

    def test_task_discovery_is_paginated_scope_bound_and_read_only(self) -> None:
        created = [
            self.workbench.create_task(guided_form(goal=f"task {index}"), f"list-{index}")
            for index in range(3)
        ]
        status_calls = self.dispatcher.status_calls

        first = self.workbench.list_tasks(limit=2)
        self.assertEqual(
            [item["task_id"] for item in first["tasks"]],
            [created[2]["task_id"], created[1]["task_id"]],
        )
        self.assertRegex(first["next_cursor"], r"^v1_[A-Za-z0-9_-]+$")
        self.assertEqual(first["tasks"][0]["optimizer"], "adam")
        self.assertEqual(first["tasks"][0]["learning_rate_milli"], 10_000)

        second = self.workbench.list_tasks(cursor=first["next_cursor"], limit=2)
        self.assertEqual(
            [item["task_id"] for item in second["tasks"]],
            [created[0]["task_id"]],
        )
        self.assertIsNone(second["next_cursor"])
        self.assertEqual(self.dispatcher.status_calls, status_calls)
        serialized = repr(first)
        self.assertNotIn("access_scope", serialized)
        self.assertNotIn("entrypoint_ref", serialized)
        self.assertNotIn("job_id", serialized)

        foreign_draft = copy.deepcopy(self.store.get_task(created[0]["task_id"]).draft)
        foreign_draft["draft_id"] = "draft-foreign-list"
        foreign = self.store.create_task(
            task_id="task-foreign-list",
            project_id="other-project",
            principal_id="other-user",
            draft=foreign_draft,
            idempotency_key="foreign-list-task",
            request_hash="sha256:" + "d" * 64,
            now=NOW,
        )

        def encoded_cursor(task_id: str) -> str:
            token = base64.urlsafe_b64encode(task_id.encode("ascii")).decode("ascii")
            return "v1_" + token.rstrip("=")

        failures = []
        for cursor in (
            encoded_cursor(foreign.snapshot.task_id),
            encoded_cursor("task-does-not-exist"),
        ):
            with self.assertRaises(WorkbenchValidationError) as caught:
                self.workbench.list_tasks(cursor=cursor)
            failures.append((caught.exception.code, caught.exception.errors))
        self.assertEqual(failures[0], failures[1])
        self.assertEqual(failures[0][0], "INVALID_TASK_CURSOR")

        for cursor in ("", "v1_!!", first["next_cursor"] + "="):
            with self.subTest(cursor=cursor):
                with self.assertRaises(WorkbenchValidationError) as caught:
                    self.workbench.list_tasks(cursor=cursor)
                self.assertEqual(caught.exception.code, "INVALID_TASK_CURSOR")

        for limit in (0, 51, True, "20"):
            with self.subTest(limit=limit):
                with self.assertRaises(WorkbenchValidationError) as caught:
                    self.workbench.list_tasks(limit=limit)
                self.assertEqual(caught.exception.code, "INVALID_TASK_LIST_LIMIT")

    def test_task_discovery_never_probes_runtime_cancel_capability(self) -> None:
        created = self.workbench.create_task(
            guided_form(goal="list cancellation projection"),
            "list-cancel-projection",
        )
        original = self.tasks.can_cancel_task

        def unexpected_probe(*args, **kwargs):
            self.fail(
                "list_tasks must remain SQLite-only and never probe cancel capability"
            )

        self.tasks.can_cancel_task = unexpected_probe
        try:
            listed = self.workbench.list_tasks()
        finally:
            self.tasks.can_cancel_task = original

        item = next(
            task
            for task in listed["tasks"]
            if task["task_id"] == created["task_id"]
        )
        self.assertFalse(item["can_cancel"])
        self.assertIsNone(item["cancellation"])

    def test_task_trash_restore_projection_views_replay_and_cursor_binding(self) -> None:
        created = [
            self.workbench.create_task(
                guided_form(goal=f"visibility task {index}"), f"visibility-{index}"
            )
            for index in range(3)
        ]
        for index, task in enumerate(created):
            abandoned = self.workbench.abandon_task(
                task["task_id"], f"visibility-abandon-{index}"
            )
            self.assertEqual(abandoned["status"], "Cancelled")
            self.assertEqual(abandoned["visibility_revision"], 0)
            self.assertIsNone(abandoned["trashed_at"])

        first = self.workbench.trash_task(
            created[1]["task_id"], 0, "visibility-trash-one"
        )
        replay = self.workbench.trash_task(
            created[1]["task_id"], 0, "visibility-trash-one"
        )
        self.assertFalse(first["replayed"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(first["visibility_revision"], 1)
        self.assertIsInstance(first["trashed_at"], str)
        detail = self.workbench.get_task(created[1]["task_id"])
        self.assertEqual(detail["visibility_revision"], 1)
        self.assertEqual(detail["trashed_at"], first["trashed_at"])
        self.assertEqual(self.workbench.list_events(created[1]["task_id"]), [])

        active = self.workbench.list_tasks(view="active")
        self.assertNotIn(
            created[1]["task_id"], [item["task_id"] for item in active["tasks"]]
        )
        self.assertTrue(
            all(
                item["visibility_revision"] == 0 and item["trashed_at"] is None
                for item in active["tasks"]
            )
        )
        trashed = self.workbench.list_tasks(view="trash")
        self.assertEqual(
            [item["task_id"] for item in trashed["tasks"]],
            [created[1]["task_id"]],
        )
        self.assertEqual(trashed["tasks"][0]["visibility_revision"], 1)

        self.workbench.trash_task(
            created[0]["task_id"], 0, "visibility-trash-zero"
        )
        first_page = self.workbench.list_tasks(limit=1, view="trash")
        self.assertIsNotNone(first_page["next_cursor"])
        with self.assertRaises(WorkbenchValidationError) as caught:
            self.workbench.list_tasks(
                cursor=first_page["next_cursor"], limit=1, view="active"
            )
        self.assertEqual(caught.exception.code, "INVALID_TASK_CURSOR")
        second_page = self.workbench.list_tasks(
            cursor=first_page["next_cursor"], limit=1, view="trash"
        )
        self.assertEqual(len(second_page["tasks"]), 1)

        with self.assertRaises(WorkbenchConflict) as caught:
            self.workbench.trash_task(
                created[2]["task_id"], 0, "visibility-trash-one"
            )
        self.assertEqual(caught.exception.code, "IDEMPOTENCY_CONFLICT")

        restored = self.workbench.restore_task(
            created[1]["task_id"], 1, "visibility-restore-one"
        )
        self.assertEqual(restored["status"], "Cancelled")
        self.assertEqual(restored["visibility_revision"], 2)
        self.assertIsNone(restored["trashed_at"])
        self.assertIn(
            created[1]["task_id"],
            [item["task_id"] for item in self.workbench.list_tasks()["tasks"]],
        )

        foreign = GuidedWorkbench(
            self.tasks,
            self.registry,
            project_id="other-project",
            principal_id=PRINCIPAL_ID,
            clock=lambda: NOW,
        )
        errors = []
        for task_id in (created[1]["task_id"], "task-does-not-exist"):
            with self.assertRaises(WorkbenchNotFound) as caught:
                foreign.trash_task(task_id, 2, "visibility-hidden")
            errors.append((caught.exception.code, caught.exception.errors))
        self.assertEqual(errors[0], errors[1])

    def test_trash_permanent_delete_removes_task_but_retains_audit(self) -> None:
        created = self.workbench.create_task(
            guided_form(goal="permanently delete this abandoned task"),
            "purge-create",
        )
        task_id = created["task_id"]
        self.workbench.abandon_task(task_id, "purge-abandon")
        trashed = self.workbench.trash_task(task_id, 0, "purge-trash")
        before = self.workbench.list_tasks(view="trash")["tasks"]
        self.assertEqual([item["task_id"] for item in before], [task_id])
        self.assertIsNone(before[0]["purge_state"])

        first = self.workbench.purge_task(
            task_id, trashed["visibility_revision"], "purge-confirmed"
        )
        replay = self.workbench.purge_task(
            task_id, trashed["visibility_revision"], "purge-confirmed"
        )

        self.assertEqual(first["task_id"], task_id)
        self.assertEqual(first["purge_state"], "purged")
        self.assertEqual(first["local_run_state"], "not_created")
        self.assertTrue(first["audit_retained"])
        self.assertFalse(first["replayed"])
        self.assertEqual(replay["purge_id"], first["purge_id"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(self.workbench.list_tasks(view="trash")["tasks"], [])
        with self.assertRaises(WorkbenchNotFound):
            self.workbench.get_task(task_id, refresh=False)

    def test_revise_uses_cas_builds_new_plan_and_replays_exactly(self) -> None:
        created = self.workbench.create_task(guided_form(), "create-revise")
        revised_form = guided_form(device="cpu", iterations=3, seed=7)
        first = self.workbench.revise_task(
            created["task_id"], 1, revised_form, "revise-001"
        )
        second = self.workbench.revise_task(
            created["task_id"], 1, revised_form, "revise-001"
        )
        self.assertEqual(first["draft"]["revision"], 2)
        self.assertEqual(first["draft"]["draft_id"], created["draft"]["draft_id"])
        self.assertEqual(first["draft"]["resources"]["gpu_count"], 0)
        self.assertNotEqual(first["plan"]["plan_hash"], created["plan"]["plan_hash"])
        self.assertEqual(first["plan"], second["plan"])
        self.assertTrue(second["replayed"])

    def test_delayed_create_and_revise_replays_return_current_revision(self) -> None:
        create_form = guided_form()
        created = self.workbench.create_task(create_form, "delayed-create")
        revision_two_form = guided_form(device="cpu", iterations=3, seed=7)
        self.workbench.revise_task(
            created["task_id"], 1, revision_two_form, "delayed-revise-1"
        )
        revision_three_form = guided_form(iterations=4, seed=8)
        latest = self.workbench.revise_task(
            created["task_id"], 2, revision_three_form, "delayed-revise-2"
        )
        before_snapshot = self.store.get_task(created["task_id"])
        connection = sqlite3.connect(self.store.database_path)
        try:
            before_plans = connection.execute(
                """
                SELECT plan_id, document_hash, recorded_at
                FROM plans WHERE task_id = ? ORDER BY plan_id
                """,
                (created["task_id"],),
            ).fetchall()
            before_mutations = connection.execute(
                """
                SELECT operation, idempotency_key, request_hash,
                       outcome_hash, created_at
                FROM workbench_mutations WHERE task_id = ?
                ORDER BY operation, idempotency_key
                """,
                (created["task_id"],),
            ).fetchall()
        finally:
            connection.close()

        delayed_create = self.workbench.create_task(create_form, "delayed-create")
        delayed_revision = self.workbench.revise_task(
            created["task_id"], 1, revision_two_form, "delayed-revise-1"
        )

        self.assertTrue(delayed_create["replayed"])
        self.assertTrue(delayed_revision["replayed"])
        for replay in (delayed_create, delayed_revision):
            self.assertEqual(replay["draft"]["revision"], 3)
            self.assertEqual(replay["draft"], latest["draft"])
            self.assertEqual(replay["plan"], latest["plan"])

        self.assertEqual(self.store.get_task(created["task_id"]), before_snapshot)
        connection = sqlite3.connect(self.store.database_path)
        try:
            after_plans = connection.execute(
                """
                SELECT plan_id, document_hash, recorded_at
                FROM plans WHERE task_id = ? ORDER BY plan_id
                """,
                (created["task_id"],),
            ).fetchall()
            after_mutations = connection.execute(
                """
                SELECT operation, idempotency_key, request_hash,
                       outcome_hash, created_at
                FROM workbench_mutations WHERE task_id = ?
                ORDER BY operation, idempotency_key
                """,
                (created["task_id"],),
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual(after_plans, before_plans)
        self.assertEqual(after_mutations, before_mutations)

    def test_delayed_create_does_not_restore_missing_plan_after_abandon(self) -> None:
        interrupted_task_ids: list[str] = []

        def interrupt_plan_persistence(**kwargs):
            interrupted_task_ids.append(kwargs["snapshot"].task_id)
            raise RuntimeError("simulated interruption after core task creation")

        self.workbench._persist_plan = interrupt_plan_persistence
        try:
            with self.assertRaisesRegex(RuntimeError, "simulated interruption"):
                self.workbench.create_task(
                    guided_form(), "delayed-create-abandoned"
                )
        finally:
            del self.workbench.__dict__["_persist_plan"]

        task_id = interrupted_task_ids[0]
        abandoned = self.workbench.abandon_task(
            task_id, "abandon-interrupted-create"
        )
        self.assertEqual(abandoned["status"], "Cancelled")
        before = self.store.get_task(task_id)
        self.assertIsNone(before.plan)
        connection = sqlite3.connect(self.store.database_path)
        try:
            before_counts = (
                connection.execute(
                    "SELECT COUNT(*) FROM plans WHERE task_id = ?", (task_id,)
                ).fetchone()[0],
                connection.execute(
                    "SELECT COUNT(*) FROM workbench_mutations WHERE task_id = ?",
                    (task_id,),
                ).fetchone()[0],
            )
        finally:
            connection.close()

        replay = self.workbench.create_task(
            guided_form(), "delayed-create-abandoned"
        )
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["status"], "Cancelled")
        self.assertIsNone(replay["plan"])
        self.assertEqual(self.store.get_task(task_id), before)
        connection = sqlite3.connect(self.store.database_path)
        try:
            after_counts = (
                connection.execute(
                    "SELECT COUNT(*) FROM plans WHERE task_id = ?", (task_id,)
                ).fetchone()[0],
                connection.execute(
                    "SELECT COUNT(*) FROM workbench_mutations WHERE task_id = ?",
                    (task_id,),
                ).fetchone()[0],
            )
        finally:
            connection.close()
        self.assertEqual(after_counts, before_counts)

    def test_approve_binds_exact_scope_submits_and_never_projects_handle(self) -> None:
        created = self.workbench.create_task(guided_form(), "create-approve")
        result = self.workbench.approve_and_submit(
            created["task_id"], created["plan"]["plan_hash"], "approve-001"
        )
        snapshot = self.store.get_task(created["task_id"])
        self.assertEqual(schema_errors("approval-decision.schema.json", snapshot.approval), [])
        self.assertEqual(snapshot.approval["schema_version"], "1.1.0")
        self.assertEqual(snapshot.approval["scope"]["max_tasks"], 1)
        wall_time = snapshot.approval["scope"]["resource_limits"][
            "wall_time_seconds"
        ]
        self.assertEqual(
            snapshot.approval["scope"]["retry_policy"],
            {
                "max_attempts": 2,
                "max_concurrent_attempts": 1,
                "max_cumulative_attempt_wall_time_seconds": 2 * wall_time,
                "retryable_failure_classes": [
                    "pre_running_launch_failure",
                    "worker_exit",
                ],
            },
        )
        self.assertEqual(snapshot.approval["plan_hash"], created["plan"]["plan_hash"])
        self.assertEqual(result["status"], "Queued")
        self.assertEqual(result["dispatch"]["state"], "pending")
        self.assertIsNone(result["dispatch"]["reconciliation"])
        self.assertFalse(result["dispatch_attempted"])
        self.assertNotIn("handle", repr(result))
        self.assertNotIn("fwi-workbench-test-job", repr(result))
        self.assertEqual(self.dispatcher.dispatch_calls, 0)

        replay = self.workbench.approve_and_submit(
            created["task_id"], created["plan"]["plan_hash"], "approve-001"
        )
        self.assertTrue(replay["replayed"])
        self.assertFalse(replay["dispatch_attempted"])
        self.assertEqual(self.dispatcher.dispatch_calls, 0)

    def test_dispatch_reconciliation_projection_is_bounded_and_path_free(self) -> None:
        created = self.workbench.create_task(guided_form(), "create-reconciliation")
        self.workbench.approve_and_submit(
            created["task_id"], created["plan"]["plan_hash"], "approve-reconciliation"
        )
        snapshot = self.store.get_task(created["task_id"])
        base_intent = self.store.get_dispatch_intent(created["task_id"])
        self.assertIsNotNone(base_intent)
        recorded_at = "2026-07-16T12:00:00.000000Z"
        resolved_at = "2026-07-16T12:00:01.000000Z"

        required = self.workbench._project(
            snapshot,
            intent={
                "state": "reconciliation_required",
                "failure_code": "DISPATCH_RECEIPT_UNKNOWN",
                "created_at": base_intent.created_at,
                "dispatch_claimed_at": recorded_at,
                "outcome_recorded_at": recorded_at,
                "reconciliation": {
                    "failure_code": "DISPATCH_RECEIPT_UNKNOWN",
                    "recorded_at": recorded_at,
                    "state": "required",
                    "result": None,
                    "evidence_kind": None,
                    "resolved_at": None,
                    "handle": {"job_id": "private-job"},
                    "document_hash": "sha256:" + "a" * 64,
                    "pid": 4242,
                    "relative_path": "/root/private/run",
                },
            },
        )
        self.assertEqual(
            required["dispatch"]["reconciliation"],
            {
                "failure_code": "DISPATCH_RECEIPT_UNKNOWN",
                "recorded_at": recorded_at,
                "state": "action_required",
                "result": None,
                "evidence_kind": None,
                "resolved_at": None,
            },
        )

        resolved = self.workbench._project(
            snapshot,
            intent={
                "state": "dispatched",
                "failure_code": None,
                "created_at": base_intent.created_at,
                "dispatch_claimed_at": recorded_at,
                "outcome_recorded_at": resolved_at,
                "handle": {"job_id": "must-not-project"},
                "reconciliation": {
                    "failure_code": "DISPATCH_RECEIPT_UNKNOWN",
                    "recorded_at": recorded_at,
                    "state": "resolved",
                    "result": "dispatched",
                    "evidence_kind": "managed_worker_receipt",
                    "resolved_at": resolved_at,
                    "attempt_id": "must-not-project",
                    "receipt_record_hash": "sha256:" + "b" * 64,
                },
            },
        )
        self.assertEqual(resolved["dispatch"]["state"], "dispatched")
        self.assertEqual(
            resolved["dispatch"]["reconciliation"],
            {
                "failure_code": "DISPATCH_RECEIPT_UNKNOWN",
                "recorded_at": recorded_at,
                "state": "resolved",
                "result": "dispatched",
                "evidence_kind": "managed_worker_receipt",
                "resolved_at": resolved_at,
            },
        )
        private_receipt = self.workbench._project(
            snapshot,
            intent={
                "state": "dispatched",
                "failure_code": None,
                "created_at": base_intent.created_at,
                "dispatch_claimed_at": recorded_at,
                "outcome_recorded_at": resolved_at,
                "reconciliation": {
                    "failure_code": "DISPATCH_RECEIPT_UNKNOWN",
                    "recorded_at": recorded_at,
                    "state": "resolved",
                    "result": "dispatched",
                    "evidence_kind": "private_receipt",
                    "resolved_at": resolved_at,
                    "receipt_record_hash": "sha256:" + "c" * 64,
                },
            },
        )
        self.assertEqual(
            private_receipt["dispatch"]["reconciliation"]["evidence_kind"],
            "private_receipt",
        )
        not_dispatched = self.workbench._project(
            replace(snapshot, status="Failed"),
            intent={
                "state": "not_dispatched",
                "failure_code": "DISPATCH_NOT_STARTED",
                "created_at": base_intent.created_at,
                "dispatch_claimed_at": recorded_at,
                "outcome_recorded_at": resolved_at,
                "reconciliation": {
                    "failure_code": "SUBMISSION_RECONCILIATION_REQUIRED",
                    "recorded_at": recorded_at,
                    "state": "resolved",
                    "result": "not_dispatched",
                    "evidence_kind": "managed_pre_running_failure",
                    "resolved_at": resolved_at,
                    "attempt_id": "attempt-must-not-project",
                    "private_proof_hash": "sha256:" + "d" * 64,
                    "pid": 5252,
                    "relative_path": "/root/private/failed-run",
                },
            },
        )
        self.assertEqual(not_dispatched["status"], "Failed")
        self.assertEqual(
            not_dispatched["dispatch"],
            {
                "state": "not_dispatched",
                "failure_code": "DISPATCH_NOT_STARTED",
                "created_at": base_intent.created_at,
                "dispatch_claimed_at": recorded_at,
                "outcome_recorded_at": resolved_at,
                "reconciliation": {
                    "failure_code": "SUBMISSION_RECONCILIATION_REQUIRED",
                    "recorded_at": recorded_at,
                    "state": "resolved",
                    "result": "not_dispatched",
                    "evidence_kind": "managed_pre_running_failure",
                    "resolved_at": resolved_at,
                },
            },
        )
        serialized = repr(
            (
                required["dispatch"],
                resolved["dispatch"],
                private_receipt["dispatch"],
                not_dispatched["dispatch"],
            )
        )
        for private_value in (
            "private-job",
            "/root/private/run",
            "must-not-project",
            "sha256:" + "a" * 64,
            "sha256:" + "b" * 64,
            "sha256:" + "c" * 64,
            "sha256:" + "d" * 64,
            "4242",
            "5252",
            "attempt-must-not-project",
            "/root/private/failed-run",
        ):
            self.assertNotIn(private_value, serialized)
        self.assertFalse(
            self.workbench.session_capabilities()["capabilities"]["retry"]
        )

        with self.assertRaises(WorkbenchRuntimeError):
            self.workbench._project(
                snapshot,
                intent={
                    "state": "dispatched",
                    "reconciliation": {
                        "failure_code": "DISPATCH_RECEIPT_UNKNOWN",
                        "recorded_at": recorded_at,
                        "state": "resolved",
                        "result": "dispatched",
                        "evidence_kind": "private_receipt",
                        "resolved_at": "/root/private/resolution",
                    },
                },
            )

        negative_reconciliation = {
            "failure_code": "SUBMISSION_RECONCILIATION_REQUIRED",
            "recorded_at": recorded_at,
            "state": "resolved",
            "result": "not_dispatched",
            "evidence_kind": "managed_pre_running_failure",
            "resolved_at": resolved_at,
        }
        for invalid_snapshot, invalid_intent in (
            (
                snapshot,
                {
                    "state": "not_dispatched",
                    "failure_code": "DISPATCH_NOT_STARTED",
                    "reconciliation": negative_reconciliation,
                },
            ),
            (
                replace(snapshot, status="Failed"),
                {
                    "state": "dispatched",
                    "failure_code": None,
                    "reconciliation": negative_reconciliation,
                },
            ),
        ):
            with self.subTest(invalid_intent=invalid_intent):
                with self.assertRaises(WorkbenchRuntimeError):
                    self.workbench._project(
                        invalid_snapshot,
                        intent=invalid_intent,
                    )

    def test_worker_exit_retrying_projection_is_bounded_and_path_free(self) -> None:
        created = self.workbench.create_task(
            guided_form(), "create-worker-exit-retrying-projection"
        )
        self.workbench.approve_and_submit(
            created["task_id"],
            created["plan"]["plan_hash"],
            "approve-worker-exit-retrying-projection",
        )
        snapshot = replace(
            self.store.get_task(created["task_id"]),
            status="Running",
        )
        base_intent = self.store.get_dispatch_intent(created["task_id"])
        self.assertIsNotNone(base_intent)
        assert base_intent is not None
        recorded_at = "2026-07-17T12:00:00.000000Z"
        resolved_at = "2026-07-17T12:00:01.000000Z"
        retrying_at = "2026-07-17T12:00:02.000000Z"
        private_values = {
            "intent_id": "intent-private-worker-exit",
            "attempt_id": "attempt-private-worker-exit",
            "previous_attempt_id": "attempt-private-previous",
            "evidence_hash": "sha256:" + "a" * 64,
            "handle": {"job_id": "fwi-private-worker-exit-job"},
            "pid": 4242,
            "relative_path": "/root/private/worker-exit",
            "private_proof": {"stopped": True},
        }
        original_can_cancel = self.tasks.can_cancel_task
        self.tasks.can_cancel_task = lambda *_args, **_kwargs: False
        try:
            projected = self.workbench._project(
                snapshot,
                intent={
                    "state": "retrying",
                    "failure_code": None,
                    "created_at": base_intent.created_at,
                    "dispatch_claimed_at": base_intent.created_at,
                    "outcome_recorded_at": retrying_at,
                    "reconciliation": {
                        "failure_code": "DISPATCH_RECEIPT_UNKNOWN",
                        "recorded_at": recorded_at,
                        "state": "resolved",
                        "result": "dispatched",
                        "evidence_kind": "managed_worker_receipt",
                        "resolved_at": resolved_at,
                        "attempt_id": "attempt-private-reconciliation",
                        "evidence_hash": "sha256:" + "b" * 64,
                    },
                    **private_values,
                },
            )
        finally:
            self.tasks.can_cancel_task = original_can_cancel

        self.assertEqual(projected["status"], "Running")
        self.assertEqual(
            projected["dispatch"],
            {
                "state": "retrying",
                "failure_code": None,
                "created_at": base_intent.created_at,
                "dispatch_claimed_at": base_intent.created_at,
                "outcome_recorded_at": retrying_at,
                "reconciliation": {
                    "failure_code": "DISPATCH_RECEIPT_UNKNOWN",
                    "recorded_at": recorded_at,
                    "state": "resolved",
                    "result": "dispatched",
                    "evidence_kind": "managed_worker_receipt",
                    "resolved_at": resolved_at,
                },
            },
        )
        self.assertFalse(projected["can_cancel"])
        serialized = repr(projected["dispatch"])
        for private_value in (
            *private_values,
            *private_values.values(),
            "attempt-private-reconciliation",
            "sha256:" + "b" * 64,
            "job_id",
            "4242",
        ):
            self.assertNotIn(str(private_value), serialized)

        for invalid_intent in (
            {
                "state": "retrying",
                "failure_code": "WORKER_EXIT",
                "reconciliation": None,
            },
            {
                "state": "retrying",
                "failure_code": None,
                "reconciliation": {
                    "failure_code": "DISPATCH_RECEIPT_UNKNOWN",
                    "recorded_at": recorded_at,
                    "state": "required",
                    "result": None,
                    "evidence_kind": None,
                    "resolved_at": None,
                },
            },
        ):
            with self.subTest(invalid_intent=invalid_intent):
                with self.assertRaises(WorkbenchRuntimeError) as caught:
                    self.workbench._project(snapshot, intent=invalid_intent)
                self.assertEqual(caught.exception.code, "SERVICE_RESPONSE_INVALID")

    def test_retry_exhaustion_projection_is_bounded_and_path_free(self) -> None:
        created = self.workbench.create_task(
            guided_form(), "create-retry-exhaustion-projection"
        )
        self.workbench.approve_and_submit(
            created["task_id"],
            created["plan"]["plan_hash"],
            "approve-retry-exhaustion-projection",
        )
        snapshot = replace(
            self.store.get_task(created["task_id"]),
            status="Failed",
        )
        base_intent = self.store.get_dispatch_intent(created["task_id"])
        self.assertIsNotNone(base_intent)
        assert base_intent is not None
        exhausted_at = "2026-07-16T12:00:02.000000Z"
        private_proof_hash = "sha256:" + "d" * 64
        original_can_cancel = self.tasks.can_cancel_task
        self.tasks.can_cancel_task = lambda *_args, **_kwargs: False
        try:
            projected = self.workbench._project(
                snapshot,
                intent={
                    "state": "retry_exhausted",
                    "failure_code": "WORKER_RETRY_EXHAUSTED",
                    "created_at": base_intent.created_at,
                    "dispatch_claimed_at": base_intent.created_at,
                    "outcome_recorded_at": exhausted_at,
                    "reconciliation": None,
                    "proof": {"attempt_number": 2},
                    "private_proof_hash": private_proof_hash,
                    "document_hash": "sha256:" + "e" * 64,
                    "job_id": "fwi-private-retry-job",
                    "relative_path": "/root/private/retry",
                    "handle": {"submission_id": "private-submission"},
                },
            )
        finally:
            self.tasks.can_cancel_task = original_can_cancel

        self.assertEqual(projected["status"], "Failed")
        self.assertEqual(
            projected["dispatch"],
            {
                "state": "retry_exhausted",
                "failure_code": "WORKER_RETRY_EXHAUSTED",
                "created_at": base_intent.created_at,
                "dispatch_claimed_at": base_intent.created_at,
                "outcome_recorded_at": exhausted_at,
                "reconciliation": None,
            },
        )
        self.assertFalse(projected["can_cancel"])
        serialized = repr(projected["dispatch"])
        for private_value in (
            private_proof_hash,
            "sha256:" + "e" * 64,
            "fwi-private-retry-job",
            "/root/private/retry",
            "private-submission",
            "attempt_number",
        ):
            self.assertNotIn(private_value, serialized)

    def test_concurrent_first_approval_same_key_converges_despite_clock_skew(
        self,
    ) -> None:
        created = self.workbench.create_task(guided_form(), "create-approve-race")
        clock_barrier = threading.Barrier(2)
        clock_lock = threading.Lock()
        sampled_times: list[str] = []
        plan_created_at = datetime.fromisoformat(
            created["plan"]["created_at"].replace("Z", "+00:00")
        )

        def racing_clock() -> str:
            with clock_lock:
                offset = len(sampled_times)
                value = (
                    plan_created_at + timedelta(microseconds=offset + 1)
                ).isoformat().replace("+00:00", "Z")
                sampled_times.append(value)
            clock_barrier.wait(timeout=5)
            return value

        self.workbench._clock = racing_clock

        def approve() -> dict:
            return self.workbench.approve_and_submit(
                created["task_id"],
                created["plan"]["plan_hash"],
                "approve-race-key",
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(approve) for _ in range(2)]
            results = [future.result(timeout=10) for future in futures]

        self.assertEqual(len(set(sampled_times)), 2)
        self.assertEqual([result["status"] for result in results], ["Queued"] * 2)
        self.assertEqual(self.dispatcher.dispatch_calls, 0)
        self.assertEqual(len(self.store.approval_history(created["task_id"])), 1)
        self.assertEqual(
            self.store.get_dispatch_intent(created["task_id"]).state,
            "pending",
        )

    def test_approve_rejects_stale_plan_hash_before_mutation(self) -> None:
        created = self.workbench.create_task(guided_form(), "create-stale")
        with self.assertRaises(WorkbenchConflict) as caught:
            self.workbench.approve_and_submit(
                created["task_id"], "sha256:" + "f" * 64, "approve-stale"
            )
        self.assertEqual(caught.exception.code, "PLAN_HASH_CONFLICT")
        self.assertIsNone(self.store.get_task(created["task_id"]).approval)
        self.assertEqual(self.dispatcher.dispatch_calls, 0)

    def test_abandon_is_pre_runtime_idempotent_and_not_p2_cancel(self) -> None:
        created = self.workbench.create_task(guided_form(), "create-abandon")
        first = self.workbench.abandon_task(created["task_id"], "abandon-001")
        second = self.workbench.abandon_task(created["task_id"], "abandon-001")
        self.assertEqual(first["status"], "Cancelled")
        self.assertTrue(second["replayed"])

        submitted = self.workbench.create_task(guided_form(), "create-running")
        self.workbench.approve_and_submit(
            submitted["task_id"], submitted["plan"]["plan_hash"], "submit-running"
        )
        with self.assertRaises(WorkbenchConflict):
            self.workbench.abandon_task(submitted["task_id"], "not-a-cancel")

    def test_detail_projects_exact_cancel_capability_and_cancellation(self) -> None:
        created = self.workbench.create_task(
            guided_form(goal="detail cancellation projection"),
            "detail-cancel-projection",
        )
        task_id = created["task_id"]
        capability_calls = []
        original_can_cancel = self.tasks.can_cancel_task

        def can_cancel(current_task_id, **scope):
            capability_calls.append((current_task_id, scope))
            return True

        self.tasks.can_cancel_task = can_cancel
        try:
            available = self.workbench.get_task(task_id, refresh=False)
        finally:
            self.tasks.can_cancel_task = original_can_cancel

        self.assertTrue(available["can_cancel"])
        self.assertIsNone(available["cancellation"])
        self.assertEqual(
            capability_calls,
            [
                (
                    task_id,
                    {
                        "project_id": PROJECT_ID,
                        "principal_id": PRINCIPAL_ID,
                    },
                )
            ],
        )

        snapshot = self.store.get_task(task_id)
        cancellation = TaskCancellationSnapshot(
            request_id="cancel-" + "a" * 32,
            task_id=task_id,
            intent_id="intent-" + "b" * 32,
            attempt_id="attempt-" + "c" * 32,
            reason="user_requested",
            requested_at="2026-07-15T03:00:20Z",
            state="requested",
        )
        requested = replace(snapshot, cancellation=cancellation)
        original_get_task = self.tasks.get_task

        def get_requested_task(current_task_id, **scope):
            self.assertEqual(current_task_id, task_id)
            self.assertEqual(
                scope,
                {"project_id": PROJECT_ID, "principal_id": PRINCIPAL_ID},
            )
            return requested

        def unexpected_probe(*args, **kwargs):
            self.fail("a durable cancellation must suppress capability probing")

        self.tasks.get_task = get_requested_task
        self.tasks.can_cancel_task = unexpected_probe
        try:
            detail = self.workbench.get_task(task_id, refresh=False)
        finally:
            self.tasks.get_task = original_get_task
            self.tasks.can_cancel_task = original_can_cancel

        self.assertFalse(detail["can_cancel"])
        self.assertEqual(
            detail["cancellation"],
            {
                "state": "requested",
                "reason": "user_requested",
                "requested_at": "2026-07-15T03:00:20Z",
                "resolved_at": None,
                "failure_code": None,
            },
        )

    def test_cancel_maps_three_arguments_and_replays_exactly(self) -> None:
        created = self.workbench.create_task(
            guided_form(goal="cancel facade mapping"),
            "cancel-facade-create",
        )
        task_id = created["task_id"]
        snapshot = self.store.get_task(task_id)
        cancellation = TaskCancellationSnapshot(
            request_id="cancel-" + "d" * 32,
            task_id=task_id,
            intent_id="intent-" + "e" * 32,
            attempt_id="attempt-" + "f" * 32,
            reason="user_requested",
            requested_at="2026-07-15T03:00:30Z",
            state="requested",
        )
        requested = replace(snapshot, cancellation=cancellation)
        cancel_calls = []
        original_cancel = self.tasks.cancel_task
        original_can_cancel = self.tasks.can_cancel_task

        def cancel_task(**kwargs):
            cancel_calls.append(kwargs)
            return TaskCancellationResult(
                snapshot=requested,
                replayed=len(cancel_calls) > 1,
            )

        def unexpected_probe(*args, **kwargs):
            self.fail("cancel response must use its durable cancellation projection")

        self.tasks.cancel_task = cancel_task
        self.tasks.can_cancel_task = unexpected_probe
        try:
            first = self.workbench.cancel_task(
                task_id,
                "cancel-facade-key",
                "user_requested",
            )
            replay = self.workbench.cancel_task(
                task_id,
                "cancel-facade-key",
                "user_requested",
            )
            with self.assertRaises(WorkbenchValidationError) as caught:
                self.workbench.cancel_task(
                    task_id,
                    "cancel-invalid-reason",
                    "timeout",
                )
        finally:
            self.tasks.cancel_task = original_cancel
            self.tasks.can_cancel_task = original_can_cancel

        expected_call = {
            "task_id": task_id,
            "reason": "user_requested",
            "idempotency_key": self.workbench._mutation_key(
                "cancel", "cancel-facade-key"
            ),
            "project_id": PROJECT_ID,
            "principal_id": PRINCIPAL_ID,
        }
        self.assertEqual(cancel_calls, [expected_call, expected_call])
        self.assertFalse(first["replayed"])
        self.assertTrue(replay["replayed"])
        self.assertFalse(first["can_cancel"])
        self.assertEqual(first["cancellation"], replay["cancellation"])
        self.assertEqual(
            first["cancellation"],
            {
                "state": "requested",
                "reason": "user_requested",
                "requested_at": "2026-07-15T03:00:30Z",
                "resolved_at": None,
                "failure_code": None,
            },
        )
        self.assertEqual(caught.exception.code, "INVALID_CANCEL_REASON")

    def test_timeout_projection_is_bounded_and_cancel_closes_after_authorization(
        self,
    ) -> None:
        created = self.workbench.create_task(
            guided_form(goal="bounded timeout projection"),
            "timeout-projection-create",
        )
        task_id = created["task_id"]
        snapshot = self.store.get_task(task_id)
        wall_time_seconds = snapshot.draft["resources"]["wall_time_seconds"]
        timeout = TaskTimeoutSnapshot(
            timeout_id="timeout-" + "a" * 32,
            task_id=task_id,
            intent_id="intent-" + "b" * 32,
            attempt_id="attempt-" + "c" * 32,
            wall_time_seconds=wall_time_seconds,
            started_at="2026-07-15T03:00:00.000000Z",
            deadline_at="2026-07-15T03:06:00.000000Z",
            state="requested",
        )
        requested = replace(snapshot, timeout=timeout)
        original_get_task = self.tasks.get_task
        original_list_tasks = self.tasks.list_tasks
        original_can_cancel = self.tasks.can_cancel_task

        def get_requested_task(current_task_id, **scope):
            self.assertEqual(current_task_id, task_id)
            self.assertEqual(scope, self.workbench._scope)
            return requested

        def list_requested_tasks(**scope):
            self.assertEqual(
                scope,
                {
                    **self.workbench._scope,
                    "cursor": None,
                    "limit": 20,
                    "view": "active",
                },
            )

            class Page:
                snapshots = (requested,)
                next_cursor = None

            return Page()

        def unexpected_probe(*args, **kwargs):
            self.fail("authorized timeout must close cancellation without probing")

        self.tasks.get_task = get_requested_task
        self.tasks.list_tasks = list_requested_tasks
        self.tasks.can_cancel_task = unexpected_probe
        try:
            detail = self.workbench.get_task(task_id, refresh=False)
            listed = self.workbench.list_tasks()["tasks"][0]
        finally:
            self.tasks.get_task = original_get_task
            self.tasks.list_tasks = original_list_tasks
            self.tasks.can_cancel_task = original_can_cancel

        expected = {
            "state": "requested",
            "wall_time_seconds": wall_time_seconds,
            "started_at": "2026-07-15T03:00:00.000000Z",
            "deadline_at": "2026-07-15T03:06:00.000000Z",
            "resolved_at": None,
            "failure_code": None,
            "terminal_status": None,
        }
        self.assertFalse(detail["can_cancel"])
        self.assertEqual(detail["timeout"], expected)
        self.assertEqual(listed["timeout"], expected)
        self.assertEqual(listed["wall_time_seconds"], wall_time_seconds)
        serialized = repr({"detail": detail, "listed": listed})
        for private in (
            timeout.timeout_id,
            timeout.intent_id,
            timeout.attempt_id,
            "proof_hash",
            "record_hash",
            "worker_pid",
        ):
            self.assertNotIn(private, serialized)

        armed = replace(requested, timeout=replace(timeout, state="armed"))
        self.tasks.get_task = lambda *_args, **_kwargs: armed
        self.tasks.can_cancel_task = lambda *_args, **_kwargs: True
        try:
            armed_detail = self.workbench.get_task(task_id, refresh=False)
        finally:
            self.tasks.get_task = original_get_task
            self.tasks.can_cancel_task = original_can_cancel
        self.assertTrue(armed_detail["can_cancel"])
        self.assertEqual(armed_detail["timeout"]["state"], "armed")

    def test_checkpoint_wait_resume_projection_is_bounded_and_path_free(
        self,
    ) -> None:
        created = self.workbench.create_task(
            guided_form(goal="bounded checkpoint projection"),
            "checkpoint-projection-create",
        )
        task_id = created["task_id"]
        snapshot = self.store.get_task(task_id)
        checkpoint = TaskCheckpointSnapshot(
            checkpoint_id="checkpoint-" + "a" * 32,
            intent_id="intent-private-checkpoint",
            node_id="invert",
            submission_id="submission-private-checkpoint",
            attempt_id="attempt-" + "b" * 32,
            attempt_number=1,
            checkpoint_index=2,
            completed_updates=17,
            checkpoint_manifest_relative_path="checkpoints/private-state.bin",
            checkpoint_manifest_size_bytes=4096,
            checkpoint_manifest_hash="sha256:" + "c" * 64,
            checkpoint_receipt_record_hash="sha256:" + "d" * 64,
            checkpoint_created_at="2026-07-15T03:00:20.000000Z",
            state="resume_requested",
            resume_id="resume-" + "e" * 32,
            resume_requested_at="2026-07-15T03:00:21.000000Z",
        )
        waiting = replace(snapshot, status="Waiting", checkpoint=checkpoint)
        private_status = {
            "status": "Waiting",
            "stage": "checkpoint_wait",
            "completed": 17,
            "total": 100,
            "updated_at": "2026-07-15T03:00:21.000000Z",
            "submission_id": checkpoint.submission_id,
            "attempt_id": checkpoint.attempt_id,
            "checkpoint_id": checkpoint.checkpoint_id,
            "checkpoint_manifest_relative_path": "/root/private/checkpoint.bin",
            "checkpoint_manifest_hash": checkpoint.checkpoint_manifest_hash,
            "checkpoint_receipt_record_hash": (
                checkpoint.checkpoint_receipt_record_hash
            ),
            "checkpoint_proof_hash": "sha256:" + "f" * 64,
            "resume_request_record_hash": "sha256:" + "1" * 64,
            "resume_acknowledgement_record_hash": "sha256:" + "2" * 64,
            "proof_hash": "sha256:" + "3" * 64,
        }
        original_refresh = self.tasks.refresh_runtime_status
        original_list_tasks = self.tasks.list_tasks
        original_can_cancel = self.tasks.can_cancel_task

        def refresh_waiting_task(**scope):
            self.assertEqual(
                scope,
                {"task_id": task_id, **self.workbench._scope},
            )
            return {
                "snapshot": waiting,
                "intent": None,
                "adapter_status": private_status,
            }

        def list_waiting_tasks(**scope):
            self.assertEqual(
                scope,
                {
                    **self.workbench._scope,
                    "cursor": None,
                    "limit": 20,
                    "view": "active",
                },
            )

            class Page:
                snapshots = (waiting,)
                next_cursor = None

            return Page()

        self.tasks.refresh_runtime_status = refresh_waiting_task
        self.tasks.list_tasks = list_waiting_tasks
        self.tasks.can_cancel_task = lambda *_args, **_kwargs: False
        try:
            detail = self.workbench.get_task(task_id)
            listed = self.workbench.list_tasks()["tasks"][0]
            waiting_checkpoint = replace(
                checkpoint,
                state="waiting",
                resume_id=None,
                resume_requested_at=None,
            )
            waiting_projection = self.workbench._project(
                replace(
                    snapshot,
                    status="Waiting",
                    checkpoint=waiting_checkpoint,
                )
            )["checkpoint"]
            resumed_projection = self.workbench._project(
                replace(
                    snapshot,
                    status="Running",
                    checkpoint=replace(
                        checkpoint,
                        state="resumed",
                        resume_acknowledged_at=(
                            "2026-07-15T03:00:22.000000Z"
                        ),
                    ),
                )
            )["checkpoint"]
        finally:
            self.tasks.refresh_runtime_status = original_refresh
            self.tasks.list_tasks = original_list_tasks
            self.tasks.can_cancel_task = original_can_cancel

        expected = {
            "state": "resume_requested",
            "checkpoint_index": 2,
            "completed_updates": 17,
            "created_at": "2026-07-15T03:00:20.000000Z",
            "resume_requested_at": "2026-07-15T03:00:21.000000Z",
            "resumed_at": None,
            "same_attempt": True,
            "capacity_released_while_waiting": False,
        }
        self.assertEqual(detail["checkpoint"], expected)
        self.assertEqual(listed["checkpoint"], expected)
        self.assertEqual(
            waiting_projection,
            expected
            | {
                "state": "waiting",
                "resume_requested_at": None,
            },
        )
        self.assertEqual(
            resumed_projection,
            expected
            | {
                "state": "resumed",
                "resumed_at": "2026-07-15T03:00:22.000000Z",
            },
        )

        public_runtime = {
            "checkpoint": detail["checkpoint"],
            "runtime_status": detail["runtime_status"],
            "listed_checkpoint": listed["checkpoint"],
        }
        serialized_public = repr(public_runtime)
        for private_field in (
            "attempt_id",
            "attempt_number",
            "submission_id",
            "checkpoint_id",
            "checkpoint_manifest_relative_path",
            "checkpoint_manifest_size_bytes",
            "checkpoint_manifest_hash",
            "checkpoint_receipt_record_hash",
            "checkpoint_proof_hash",
            "resume_id",
            "resume_request_record_hash",
            "resume_acknowledgement_record_hash",
            "proof_hash",
        ):
            self.assertNotIn(private_field, serialized_public)
        serialized_all = repr({"detail": detail, "listed": listed})
        for private_value in (
            checkpoint.checkpoint_id,
            checkpoint.intent_id,
            checkpoint.submission_id,
            checkpoint.attempt_id,
            checkpoint.resume_id,
            checkpoint.checkpoint_manifest_relative_path,
            checkpoint.checkpoint_manifest_hash,
            checkpoint.checkpoint_receipt_record_hash,
            "/root/private/checkpoint.bin",
            "sha256:" + "f" * 64,
            "sha256:" + "1" * 64,
            "sha256:" + "2" * 64,
            "sha256:" + "3" * 64,
        ):
            self.assertNotIn(private_value, serialized_all)

    def test_get_refresh_events_and_artifact_projection_do_not_leak_runtime_ids(self) -> None:
        created = self.workbench.create_task(guided_form(), "create-read")
        self.workbench.approve_and_submit(
            created["task_id"], created["plan"]["plan_hash"], "approve-read"
        )
        refreshed = self.workbench.get_task(created["task_id"])
        self.assertIsNone(refreshed["runtime_status"])
        events = self.workbench.list_events(created["task_id"])
        self.assertEqual(events[0]["event_type"], "task_queued")

        # The real TaskService owns scope/receipt validation.  Its dispatcher
        # collection is terminal-only, so exercise only the facade projection
        # here with a deliberately path-bearing trusted manifest.
        intent = self.store.get_dispatch_intent(created["task_id"])
        manifests = self.dispatcher.collect(intent)
        worker_job_id = "fwi-private-worker-job-001"
        old_relative_path = f"{worker_job_id}/artifacts/metrics.json"
        manifests[0]["location"] = {"relative_path": old_relative_path}
        manifests[0]["extensions"] = {
            "org.agent_rpc.adapter": {
                "output_port": "loss",
                "worker_job_id": worker_job_id,
            }
        }

        class ArtifactTaskView:
            def collect_artifacts(inner_self, **kwargs):
                return manifests

            def read_artifact(inner_self, **kwargs):
                return manifests[0], b"artifact-test-bytes"

        facade = GuidedWorkbench(
            ArtifactTaskView(),
            self.registry,
            project_id=PROJECT_ID,
            principal_id=PRINCIPAL_ID,
            clock=lambda: NOW,
        )
        listed = facade.list_artifacts(created["task_id"])
        self.assertEqual(schema_errors("artifact-manifest.schema.json", listed[0]), [])
        self.assertEqual(
            listed[0]["location"]["relative_path"],
            f"{created['task_id']}/{listed[0]['artifact_id']}",
        )
        serialized = repr(listed[0])
        self.assertNotIn(worker_job_id, serialized)
        self.assertNotIn(old_relative_path, serialized)
        self.assertNotIn("worker_job_id", serialized)
        self.assertNotIn("/root/", serialized)
        manifest, data = facade.read_artifact(
            created["task_id"], listed[0]["artifact_id"]
        )
        self.assertEqual(manifest, listed[0])
        self.assertEqual(schema_errors("artifact-manifest.schema.json", manifest), [])
        self.assertNotIn(worker_job_id, repr(manifest))
        self.assertNotIn(old_relative_path, repr(manifest))
        self.assertEqual(data, b"artifact-test-bytes")

    def test_list_events_removes_exact_retry_exhaustion_private_proof(self) -> None:
        private_extension = {
            "intent_id": "intent-private-retry-exhaustion",
            "attempt_id": "attempt-" + "7" * 32,
            "attempt_number": 2,
            "observation_sequence": 3,
            "evidence_hash": "sha256:" + "8" * 64,
            "private_schema_version": "1.2.0",
            "private_proof_hash": "sha256:" + "9" * 64,
            "failure_kind": "pre_running_launch_failure",
            "max_attempts": 2,
            "private_path": "/root/private/retry-attempt",
        }
        worker_exit_extension = {
            "intent_id": "intent-private-worker-exit",
            "attempt_number": 2,
            "previous_attempt_id": "attempt-private-worker-exit",
            "previous_observation_sequence": 4,
            "evidence_hash": "sha256:" + "a" * 64,
            "private_schema_version": "1.1.0",
            "private_proof_hash": "sha256:" + "b" * 64,
            "failure_kind": "worker_exit",
            "max_attempts": 2,
            "source_outcome_document_hash": "sha256:" + "c" * 64,
            "source_handle_hash": "sha256:" + "d" * 64,
            "pid": 4242,
            "private_path": "/root/private/worker-exit-retry",
        }
        reconciliation_extension = {
            "intent_id": "intent-private-dispatch-reconciliation",
            "attempt_id": "attempt-" + "6" * 32,
            "attempt_number": 1,
            "evidence_hash": "sha256:" + "5" * 64,
            "adapter_version": "1.5.0",
            "private_schema_version": "1.2.0",
            "private_record_hash": "sha256:" + "4" * 64,
            "private_proof_hash": "sha256:" + "3" * 64,
            "result": "not_dispatched",
        }
        canonical = {
            "schema_version": "1.0.0",
            "event_id": "event-retry-exhausted-public",
            "sequence": 2,
            "task_id": "task-retry-exhausted-public",
            "node_id": "invert",
            "event_type": "node_failed",
            "task_status": "Failed",
            "error": {
                "code": "retry_exhausted",
                "message": "FWI Worker exhausted its approved launch attempts",
                "retryable": False,
            },
            "occurred_at": NOW,
            "fingerprint": {},
            "extensions": {
                "org.agent_rpc.retry_exhaustion": private_extension,
                "org.agent_rpc.worker_exit_retry": worker_exit_extension,
                "org.agent_rpc.dispatch_reconciliation": (
                    reconciliation_extension
                ),
                "org.agent_rpc.adapter_status": {
                    "stage": "submit",
                    "job_id": "fwi-private-retry-job",
                },
            },
        }

        class ExactExhaustionEventView:
            def list_run_events(inner_self, *_args, **_kwargs):
                return [copy.deepcopy(canonical)]

        facade = GuidedWorkbench(
            ExactExhaustionEventView(),
            self.registry,
            project_id=PROJECT_ID,
            principal_id=PRINCIPAL_ID,
            clock=lambda: NOW,
        )
        projected = facade.list_events(canonical["task_id"])
        self.assertEqual(projected[0]["error"]["code"], "retry_exhausted")
        self.assertNotIn(
            "org.agent_rpc.retry_exhaustion", projected[0]["extensions"]
        )
        self.assertNotIn(
            "org.agent_rpc.worker_exit_retry", projected[0]["extensions"]
        )
        self.assertNotIn(
            "org.agent_rpc.dispatch_reconciliation",
            projected[0]["extensions"],
        )
        self.assertEqual(
            projected[0]["extensions"]["org.agent_rpc.adapter_status"],
            {"stage": "submit"},
        )
        # Projection is copy-based; the SQLite-owned canonical event remains
        # untouched for audit and recovery.
        self.assertEqual(
            canonical["extensions"]["org.agent_rpc.retry_exhaustion"],
            private_extension,
        )
        self.assertEqual(
            canonical["extensions"]["org.agent_rpc.worker_exit_retry"],
            worker_exit_extension,
        )
        self.assertEqual(
            canonical["extensions"]["org.agent_rpc.dispatch_reconciliation"],
            reconciliation_extension,
        )
        serialized = repr(projected)
        for private in (
            private_extension["intent_id"],
            private_extension["attempt_id"],
            private_extension["evidence_hash"],
            private_extension["private_proof_hash"],
            private_extension["private_schema_version"],
            private_extension["private_path"],
            worker_exit_extension["intent_id"],
            worker_exit_extension["previous_attempt_id"],
            worker_exit_extension["evidence_hash"],
            worker_exit_extension["private_proof_hash"],
            worker_exit_extension["source_outcome_document_hash"],
            worker_exit_extension["source_handle_hash"],
            worker_exit_extension["private_path"],
            reconciliation_extension["intent_id"],
            reconciliation_extension["attempt_id"],
            reconciliation_extension["evidence_hash"],
            reconciliation_extension["private_record_hash"],
            reconciliation_extension["private_proof_hash"],
            "intent_id",
            "attempt_id",
            "previous_attempt_id",
            "evidence_hash",
            "private_proof_hash",
            "private_schema_version",
            "source_outcome_document_hash",
            "source_handle_hash",
            "4242",
            "fwi-private-retry-job",
        ):
            self.assertNotIn(private, serialized)

    def test_list_events_redacts_dag_failure_and_bounds_cache_hit_evidence(
        self,
    ) -> None:
        dag_private = {
            "intent_id": "intent-private-dag-no-retry",
            "attempt_id": "attempt-private-dag-no-retry",
            "attempt_number": 1,
            "observation_sequence": 8,
            "evidence_hash": "sha256:" + "1" * 64,
            "private_schema_version": "1.2.0",
            "private_proof_hash": "sha256:" + "2" * 64,
            "failure_kind": "worker_exit",
            "max_node_attempts": 1,
        }
        cache_private = {
            "state": "hit",
            "cache_hit_id": "cache-hit-private-001",
            "cache_entry_id": "cache-entry-private-001",
            "cache_key_hash": "sha256:" + "3" * 64,
            "source_intent_id": "intent-private-cache-source",
            "source_receipt_document_hash": "sha256:" + "4" * 64,
            "worker_runtime_started": False,
            "private_path": "/root/private/cache-source",
        }
        canonical = {
            "schema_version": "1.0.0",
            "event_id": "event-public-cache-hit",
            "sequence": 9,
            "task_id": "task-public-cache-hit",
            "node_id": "data_check",
            "event_type": "node_succeeded",
            "task_status": "Running",
            "occurred_at": NOW,
            "fingerprint": {},
            "extensions": {
                "org.agent_rpc.dag_no_retry": dag_private,
                "org.agent_rpc.node_cache": cache_private,
                "org.agent_rpc.public_progress": {"completed": 1, "total": 5},
            },
        }

        class ExactDagCacheEventView:
            def list_run_events(inner_self, *_args, **_kwargs):
                return [copy.deepcopy(canonical)]

        facade = GuidedWorkbench(
            ExactDagCacheEventView(),
            self.registry,
            project_id=PROJECT_ID,
            principal_id=PRINCIPAL_ID,
            clock=lambda: NOW,
        )
        projected = facade.list_events(canonical["task_id"])
        extensions = projected[0]["extensions"]
        self.assertNotIn("org.agent_rpc.dag_no_retry", extensions)
        self.assertEqual(
            extensions["org.agent_rpc.node_cache"],
            {
                "state": "hit",
                "cache_key_hash": cache_private["cache_key_hash"],
                "worker_runtime_started": False,
            },
        )
        self.assertEqual(
            extensions["org.agent_rpc.public_progress"],
            {"completed": 1, "total": 5},
        )
        self.assertEqual(
            canonical["extensions"]["org.agent_rpc.dag_no_retry"], dag_private
        )
        self.assertEqual(
            canonical["extensions"]["org.agent_rpc.node_cache"], cache_private
        )
        serialized = repr(projected)
        for private in (
            dag_private["intent_id"],
            dag_private["attempt_id"],
            dag_private["evidence_hash"],
            dag_private["private_proof_hash"],
            cache_private["cache_hit_id"],
            cache_private["cache_entry_id"],
            cache_private["source_intent_id"],
            cache_private["source_receipt_document_hash"],
            cache_private["private_path"],
            "source_intent_id",
            "source_receipt_document_hash",
            "/root/",
        ):
            self.assertNotIn(private, serialized)

    def test_list_events_bounds_checkpoint_wait_resume_evidence(self) -> None:
        checkpoint_id = "checkpoint-" + "1" * 32
        resume_id = "checkpoint-resume-" + "2" * 32
        attempt_id = "attempt-" + "3" * 32
        checkpoint_path = "task-private/checkpoints/checkpoint-private.json"
        proof_hash = "sha256:" + "4" * 64
        wait_extension = {
            "checkpoint_id": checkpoint_id,
            "checkpoint_index": 1,
            "completed_updates": 12,
            "same_attempt": True,
            "attempt_id": attempt_id,
            "proof_hash": proof_hash,
            "checkpoint_manifest_relative_path": checkpoint_path,
        }
        resume_extension = {
            "checkpoint_id": checkpoint_id,
            "resume_id": resume_id,
            "same_attempt": True,
            "attempt_id": attempt_id,
            "proof_hash": proof_hash,
            "private_path": "/root/private/checkpoint-resume",
        }
        canonical = [
            {
                "schema_version": "1.0.0",
                "event_id": "event-private-checkpoint-created",
                "sequence": 4,
                "task_id": "task-private-checkpoint-events",
                "node_id": "invert",
                "event_type": "checkpoint_created",
                "task_status": "Running",
                "checkpoint": {"relative_path": checkpoint_path},
                "occurred_at": NOW,
                "fingerprint": {},
                "extensions": {
                    "org.agent_rpc.checkpoint_wait": wait_extension,
                    "org.agent_rpc.public_progress": {"phase": "checkpoint"},
                },
            },
            {
                "schema_version": "1.0.0",
                "event_id": "event-private-node-waiting",
                "sequence": 5,
                "task_id": "task-private-checkpoint-events",
                "node_id": "invert",
                "event_type": "node_waiting",
                "task_status": "Waiting",
                "occurred_at": NOW,
                "fingerprint": {},
                "extensions": {
                    "org.agent_rpc.checkpoint_wait": wait_extension,
                },
            },
            {
                "schema_version": "1.0.0",
                "event_id": "event-private-checkpoint-resumed",
                "sequence": 6,
                "task_id": "task-private-checkpoint-events",
                "node_id": "invert",
                "event_type": "node_started",
                "task_status": "Running",
                "occurred_at": NOW,
                "fingerprint": {},
                "extensions": {
                    "org.agent_rpc.checkpoint_resume": resume_extension,
                },
            },
        ]

        class ExactCheckpointEventView:
            def list_run_events(inner_self, *_args, **_kwargs):
                return copy.deepcopy(canonical)

        facade = GuidedWorkbench(
            ExactCheckpointEventView(),
            self.registry,
            project_id=PROJECT_ID,
            principal_id=PRINCIPAL_ID,
            clock=lambda: NOW,
        )
        projected = facade.list_events(canonical[0]["task_id"])
        self.assertEqual(
            [
                (event["sequence"], event["event_type"], event["task_status"])
                for event in projected
            ],
            [
                (4, "checkpoint_created", "Running"),
                (5, "node_waiting", "Waiting"),
                (6, "node_started", "Running"),
            ],
        )
        self.assertEqual(
            projected[0]["extensions"]["org.agent_rpc.public_progress"],
            {"phase": "checkpoint"},
        )
        self.assertNotIn("checkpoint", projected[0])
        for event in projected:
            self.assertNotIn(
                "org.agent_rpc.checkpoint_wait", event["extensions"]
            )
            self.assertNotIn(
                "org.agent_rpc.checkpoint_resume", event["extensions"]
            )

        # Public projection is copy-based; canonical recovery evidence remains
        # available to the service/store boundary.
        self.assertEqual(
            canonical[0]["checkpoint"]["relative_path"], checkpoint_path
        )
        self.assertEqual(
            canonical[1]["extensions"]["org.agent_rpc.checkpoint_wait"],
            wait_extension,
        )
        self.assertEqual(
            canonical[2]["extensions"]["org.agent_rpc.checkpoint_resume"],
            resume_extension,
        )
        serialized = repr(projected)
        for private in (
            checkpoint_id,
            resume_id,
            attempt_id,
            checkpoint_path,
            proof_hash,
            resume_extension["private_path"],
            "checkpoint_id",
            "resume_id",
            "attempt_id",
            "proof_hash",
            "relative_path",
            "org.agent_rpc.checkpoint_wait",
            "org.agent_rpc.checkpoint_resume",
        ):
            self.assertNotIn(private, serialized)

    def test_unknown_task_is_a_stable_not_found(self) -> None:
        with self.assertRaises(WorkbenchNotFound) as caught:
            self.workbench.get_task("task-does-not-exist", refresh=False)
        self.assertEqual(caught.exception.code, "NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
