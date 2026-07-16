from __future__ import annotations

import base64
import copy
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scientific_runtime.registry_service import RegistryService
from scientific_runtime.fwi_registry import load_deepwave_manifest
from scientific_runtime.task_dispatcher import DispatchPreparation
from scientific_runtime.task_service import TaskService
from scientific_runtime.task_store import SQLiteTaskStore
from scientific_runtime.workbench_service import (
    GuidedWorkbench,
    WorkbenchConflict,
    WorkbenchNotFound,
    WorkbenchValidationError,
    _stable_id,
)
from scientific_runtime_contracts import schema_errors
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


def development_fingerprint() -> dict:
    value = fingerprint()
    value["algorithm"]["version"] = "1.4.0"
    value["adapter_version"] = "1.4.0"
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
            adapter_version="1.4.0",
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
        self.assertFalse(capabilities["features"]["running_cancel"])
        self.assertFalse(capabilities["features"]["automatic_reconciliation"])
        self.assertFalse(capabilities["features"]["startup_dispatch_recovery"])
        self.assertTrue(capabilities["features"]["startup_receipt_recovery"])
        self.assertTrue(capabilities["features"]["startup_status_catchup"])
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
            {"id": "deepwave.acoustic_fwi", "version": "1.4.0"},
        )
        self.assertEqual(
            capabilities["capabilities"],
            {
                "cancel": False,
                "retry": False,
                "sse": False,
                "startup_dispatch_recovery": False,
                "startup_receipt_recovery": True,
                "startup_status_catchup": True,
                "continuous_status_supervision": True,
                "supervisor_leases": True,
                "automatic_reconciliation": False,
                "dag": False,
            },
        )

        catalog = self.workbench.list_catalog()
        self.assertEqual(len(catalog["datasets"]), 1)
        self.assertEqual(catalog["datasets"][0]["id"], "marmousi_94_288")
        self.assertNotIn("access_scope", catalog["datasets"][0])
        self.assertEqual(len(catalog["algorithms"]), 1)
        self.assertEqual(catalog["algorithms"][0]["version"], "1.4.0")
        serialized = repr(catalog)
        self.assertNotIn("entrypoint_ref", serialized)
        self.assertNotIn("/root/", serialized)

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
        self.assertEqual(current_snapshot.draft["algorithm"]["version"], "1.4.0")
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
        self.assertEqual(current["draft"]["algorithm"]["version"], "1.4.0")
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
        self.assertEqual(snapshot.approval["scope"]["max_tasks"], 1)
        self.assertEqual(snapshot.approval["plan_hash"], created["plan"]["plan_hash"])
        self.assertEqual(result["status"], "Queued")
        self.assertEqual(result["dispatch"]["state"], "dispatched")
        self.assertNotIn("handle", repr(result))
        self.assertNotIn("fwi-workbench-test-job", repr(result))
        self.assertEqual(self.dispatcher.dispatch_calls, 1)

        replay = self.workbench.approve_and_submit(
            created["task_id"], created["plan"]["plan_hash"], "approve-001"
        )
        self.assertTrue(replay["replayed"])
        self.assertFalse(replay["dispatch_attempted"])
        self.assertEqual(self.dispatcher.dispatch_calls, 1)

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
        self.assertEqual(self.dispatcher.dispatch_calls, 1)
        self.assertEqual(len(self.store.approval_history(created["task_id"])), 1)
        self.assertEqual(
            self.store.get_dispatch_intent(created["task_id"]).state,
            "dispatched",
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

    def test_get_refresh_events_and_artifact_projection_do_not_leak_runtime_ids(self) -> None:
        created = self.workbench.create_task(guided_form(), "create-read")
        self.workbench.approve_and_submit(
            created["task_id"], created["plan"]["plan_hash"], "approve-read"
        )
        refreshed = self.workbench.get_task(created["task_id"])
        self.assertEqual(refreshed["runtime_status"]["status"], "Queued")
        self.assertNotIn("job_id", refreshed["runtime_status"])
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

    def test_unknown_task_is_a_stable_not_found(self) -> None:
        with self.assertRaises(WorkbenchNotFound) as caught:
            self.workbench.get_task("task-does-not-exist", refresh=False)
        self.assertEqual(caught.exception.code, "NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
