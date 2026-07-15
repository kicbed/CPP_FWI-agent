from __future__ import annotations

import copy
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scientific_runtime.registry_service import RegistryService
from scientific_runtime.task_dispatcher import DispatchPreparation
from scientific_runtime.task_service import TaskService
from scientific_runtime.task_store import SQLiteTaskStore
from scientific_runtime.workbench_service import (
    GuidedWorkbench,
    WorkbenchConflict,
    WorkbenchNotFound,
    WorkbenchValidationError,
)
from scientific_runtime_contracts import schema_errors
from tests.test_scientific_runtime_contracts import (
    algorithm_manifest,
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
    }
    value.update(changes)
    return value


def development_fingerprint() -> dict:
    value = fingerprint()
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
            adapter_version="1.1.0",
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
            "fingerprint": fingerprint(),
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
        return values[0], b"artifact-test-bytes"


class ScientificRuntimeWorkbenchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = SQLiteTaskStore(Path(self.temporary.name) / "tasks.sqlite3")
        self.registry = RegistryService(self.store, clock=lambda: NOW)
        self.registry.register_dataset(dataset=dataset_ref())
        self.registry.register_algorithm(manifest=algorithm_manifest())
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

    def test_capabilities_and_catalog_are_fixed_scoped_and_path_free(self) -> None:
        capabilities = self.workbench.session_capabilities()
        self.assertEqual(capabilities["mode"], "guided")
        self.assertEqual(
            capabilities["scope"],
            {"project_id": PROJECT_ID, "principal_id": PRINCIPAL_ID},
        )
        self.assertFalse(capabilities["features"]["running_cancel"])
        self.assertFalse(capabilities["features"]["automatic_reconciliation"])
        self.assertEqual(
            capabilities["form"]["iterations"], {"minimum": 1, "maximum": 10000}
        )
        self.assertEqual(
            capabilities["algorithm"],
            {"id": "deepwave.acoustic_fwi", "version": "1.1.0"},
        )
        self.assertEqual(
            capabilities["capabilities"],
            {
                "cancel": False,
                "retry": False,
                "sse": False,
                "automatic_reconciliation": False,
                "dag": False,
            },
        )

        catalog = self.workbench.list_catalog()
        self.assertEqual(len(catalog["datasets"]), 1)
        self.assertEqual(catalog["datasets"][0]["id"], "marmousi_94_288")
        self.assertNotIn("access_scope", catalog["datasets"][0])
        self.assertEqual(len(catalog["algorithms"]), 1)
        self.assertEqual(catalog["algorithms"][0]["version"], "1.1.0")
        serialized = repr(catalog)
        self.assertNotIn("entrypoint_ref", serialized)
        self.assertNotIn("/root/", serialized)

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
        self.assertEqual(snapshot.draft["resources"]["gpu_count"], 1)
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
