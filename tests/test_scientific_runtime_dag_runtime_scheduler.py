from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import shutil
import sqlite3
import tempfile
import unittest
from collections import Counter
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import scientific_runtime.task_store as task_store_module
from scientific_runtime.fixed_recipe import (
    fixed_recipe_plan_inputs,
    load_fixed_recipe_manifest,
)

from scientific_runtime import (
    DispatchError,
    RegistryService,
    RuntimeSupervisor,
    SQLiteTaskStore,
    TaskConflict,
    TaskDispatchError,
    TaskNotFound,
    TaskService,
    TaskStoreConflict,
    TaskStoreCorruption,
    TaskSupervisorLeaseLost,
    TaskValidationError,
    load_deepwave_manifest,
)
from scientific_runtime_contracts import compute_plan_hash
from scientific_runtime.task_store import encode_document, is_fixed_recipe_parallel_plan
from tests import test_scientific_runtime_task_service as task_service_fixtures
from tests.test_scientific_runtime_contracts import approval_decision, dataset_ref
from tests.test_scientific_runtime_dag_execution_store import (
    ControlledDagDispatcher,
)
from tests.test_scientific_runtime_task_service import (
    MANAGED_REQUEST_HASH,
    MANAGED_SUBMISSION_ID,
    current_optimizer_plan_graph,
    current_optimizer_task_draft,
    executable_fingerprint,
    managed_worker_evidence,
)


PROJECT_ID = "project-1"
PRINCIPAL_ID = "user-1"


class MultiNodeControlledDagDispatcher(ControlledDagDispatcher):
    """The v20 controlled Adapter with exact per-node external identities."""

    def __init__(self, store: SQLiteTaskStore) -> None:
        super().__init__(store)
        self.dispatch_counts: Counter[tuple[str, str]] = Counter()
        self._observations: dict[str, dict] = {}
        self._statuses: dict[str, dict] = {}
        self._manifests_by_intent: dict[str, list[dict]] = {}
        self._artifact_data_by_intent: dict[str, dict[str, bytes]] = {}
        self.worker_projection_deferred_codes: dict[str, str] = {}

    @staticmethod
    def _attempt_id(intent) -> str:
        identity = f"{intent.task_id}\x1f{intent.node_id}\x1f{intent.intent_id}"
        return "attempt-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]

    @staticmethod
    def _job_id(intent) -> str:
        digest = hashlib.sha256(intent.intent_id.encode("utf-8")).hexdigest()
        suffix = int(digest[:12], 16) % 1_000_000_000_000
        return f"fwi-20260715T030000Z-{suffix:012d}"

    def _activate(self, intent) -> None:
        observation = self._observations.get(intent.intent_id)
        if observation is not None:
            self.worker_observation = copy.deepcopy(observation)

    def set_node_outputs(self, intent, manifests, artifact_data) -> None:
        node_manifests = copy.deepcopy(manifests)
        node_artifact_data: dict[str, bytes] = {}
        for manifest in node_manifests:
            physical_id = manifest["artifact_id"]
            node_id = f"{physical_id}-{intent.node_id}"
            manifest["artifact_id"] = node_id
            node_artifact_data[node_id] = bytes(artifact_data[physical_id])
        self._manifests_by_intent[intent.intent_id] = node_manifests
        self._artifact_data_by_intent[intent.intent_id] = node_artifact_data
        self._activate_outputs(intent)

    def _activate_outputs(self, intent) -> None:
        manifests = self._manifests_by_intent.get(intent.intent_id)
        artifact_data = self._artifact_data_by_intent.get(intent.intent_id)
        if manifests is not None and artifact_data is not None:
            self.manifests = copy.deepcopy(manifests)
            self.artifact_data = copy.deepcopy(artifact_data)

    def prepare_node(self, snapshot, *, node_id, input_binding):
        prepared = super().prepare_node(
            snapshot, node_id=node_id, input_binding=input_binding
        )
        extensions = snapshot.plan.get("extensions")
        if not isinstance(extensions, dict):
            return prepared
        recipe = extensions.get("org.agent_rpc.recipe")
        if recipe is None:
            return prepared
        recipe_input_hashes = [
            bound["binding"]["artifact"]["content_hash"]
            for bound in input_binding.binding_document["inputs"]
            if bound.get("kind") == "node_output"
        ]
        request = copy.deepcopy(prepared.request)
        request["recipe"] = copy.deepcopy(recipe)
        request["recipe_input_hashes"] = recipe_input_hashes
        fingerprint = copy.deepcopy(prepared.queue_fingerprint)
        fingerprint["input_hashes"] = [
            input_binding.binding_document["inputs"][0]["dataset"][
                "content_hash"
            ],
            *recipe_input_hashes,
        ]
        return replace(
            prepared,
            request=request,
            queue_fingerprint=fingerprint,
        )

    def dispatch(self, intent):
        # Keep the existing real P2 boundary, but permit successor nodes after
        # the Task aggregate has already entered Running.
        visible = self.store.get_task(intent.task_id)
        assert visible is not None and visible.status in {"Queued", "Running"}
        with self.lock:
            self.dispatch_calls += 1
            self.dispatch_counts[(intent.task_id, intent.node_id)] += 1
        if self.failure_code is not None:
            return super().dispatch(intent)

        job_id = self._job_id(intent)
        attempt_id = self._attempt_id(intent)
        handle = {
            "submission_id": MANAGED_SUBMISSION_ID,
            "task_id": intent.task_id,
            "node_id": intent.node_id,
            "job_id": job_id,
            "idempotency_key": intent.node_idempotency_key,
            "plan_hash": intent.plan_hash,
            "request_hash": intent.request_hash,
            "algorithm": copy.deepcopy(intent.request["algorithm"]),
            "fingerprint": copy.deepcopy(intent.queue_fingerprint),
            "adapter_version": intent.adapter_version,
        }
        observation = {
            "evidence": managed_worker_evidence(
                attempt_id=attempt_id,
                job_id=job_id,
                request_hash=intent.request_hash,
            ),
            "handle": copy.deepcopy(handle),
        }
        self._observations[intent.intent_id] = copy.deepcopy(observation)
        self.worker_observation = copy.deepcopy(observation)
        return handle

    def observe_existing_worker_attempt(self, intent):
        self._activate(intent)
        deferred_code = self.worker_projection_deferred_codes.get(intent.node_id)
        if deferred_code is not None:
            raise DispatchError(deferred_code)
        return super().observe_existing_worker_attempt(intent)

    def probe_existing_dispatch_receipt(self, intent):
        self._activate(intent)
        return super().probe_existing_dispatch_receipt(intent)

    def status(self, intent):
        self._activate(intent)
        with self.lock:
            self.status_calls += 1
        value = copy.deepcopy(
            self._statuses.get(
                intent.intent_id,
                {
                    "status": "Queued",
                    "stage": "queued",
                    "completed": 0,
                    "total": intent.request["parameters"]["iterations"],
                    "message": "controlled node is queued",
                    "updated_at": "2026-07-15T03:00:10Z",
                    "terminal": False,
                },
            )
        )
        value.update(
            {
                "job_id": intent.handle["job_id"],
                "task_id": intent.task_id,
                "node_id": intent.node_id,
            }
        )
        return value

    def set_status(self, intent, *, state: str, updated_at: str) -> None:
        terminal = state in {"Succeeded", "Failed", "Cancelled"}
        recipe_forward = (
            intent.request.get("recipe")
            == {"id": "forward_qc_fwi", "version": "1.0.0"}
            and intent.node_id != "fwi"
        )
        total = (
            0
            if recipe_forward
            else int(intent.request["parameters"]["iterations"])
        )
        self._statuses[intent.intent_id] = {
            "status": state,
            "stage": state.lower(),
            "completed": total if state == "Succeeded" else 0,
            "total": total,
            "message": f"controlled {intent.node_id} {state.lower()}",
            "updated_at": updated_at,
            "terminal": terminal,
        }

    def set_terminal_heartbeat(self, intent, *, state: str) -> None:
        prior = self._observations[intent.intent_id]["evidence"]
        heartbeat = prior["heartbeat"]
        sequence = 1 if heartbeat is None else heartbeat["sequence"] + 1
        observation = {
            "evidence": managed_worker_evidence(
                attempt_id=prior["attempt_id"],
                attempt_number=prior["attempt_number"],
                job_id=prior["job_id"],
                request_hash=prior["request_hash"],
                heartbeat_sequence=sequence,
                heartbeat_state=state,
            ),
            "handle": copy.deepcopy(self._observations[intent.intent_id]["handle"]),
        }
        self._observations[intent.intent_id] = copy.deepcopy(observation)
        self.worker_observation = copy.deepcopy(observation)

    @contextlib.contextmanager
    def verified_node_outputs(self, intent):
        self._activate(intent)
        self._activate_outputs(intent)
        with super().verified_node_outputs(intent) as outputs:
            yield outputs

    def collect(self, intent):
        self._activate_outputs(intent)
        return super().collect(intent)

    def read_artifact(self, intent, artifact_id):
        self._activate_outputs(intent)
        return super().read_artifact(intent, artifact_id)


class ReproducibleMultiNodeDagDispatcher(MultiNodeControlledDagDispatcher):
    """Controlled Worker whose adopted provenance is safe to cache."""

    def prepare_node(self, snapshot, *, node_id, input_binding):
        prepared = super().prepare_node(
            snapshot, node_id=node_id, input_binding=input_binding
        )
        fingerprint = executable_fingerprint(self.adapter_version)
        fingerprint["input_hashes"] = copy.deepcopy(
            prepared.queue_fingerprint["input_hashes"]
        )
        request = copy.deepcopy(prepared.request)
        request["normalized_config_hash"] = fingerprint[
            "normalized_config_hash"
        ]
        return replace(
            prepared,
            request=request,
            queue_fingerprint=fingerprint,
        )


class CacheVerificationTamperingDispatcher(
    ReproducibleMultiNodeDagDispatcher
):
    """Inject one failure after the durable source has been committed."""

    cache_verification_tamper: str | None = None

    @contextlib.contextmanager
    def verified_node_outputs(self, intent):
        with super().verified_node_outputs(intent) as supplied:
            mode = self.cache_verification_tamper
            if mode == "bytes":
                raise DispatchError("ARTIFACT_HASH_MISMATCH")
            if mode == "symlink":
                raise DispatchError("ARTIFACT_SYMLINK_REJECTED")
            outputs = copy.deepcopy(supplied)
            if mode == "missing":
                outputs["manifests"] = []
            elif mode is not None:
                manifest = outputs["manifests"][0]
                if mode == "manifest":
                    manifest["artifact_id"] += "-tampered"
                elif mode == "lineage":
                    manifest["lineage"]["inputs"][0]["content_hash"] = (
                        "sha256:" + "1" * 64
                    )
                elif mode == "content_hash":
                    manifest["content_hash"] = "sha256:" + "2" * 64
                elif mode == "size":
                    manifest["size_bytes"] += 1
                elif mode == "media_type":
                    manifest["media_type"] = "application/octet-stream"
                elif mode == "schema_version":
                    manifest["schema_version"] = "9.9.9"
                else:
                    raise AssertionError(f"unknown cache tamper mode: {mode}")
            yield outputs


class ScientificRuntimeDagRuntimeSchedulerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary.name) / "task.sqlite3"
        self.clock_value = datetime(
            2026, 7, 15, 3, 0, 10, tzinfo=timezone.utc
        )
        self.next_task = 0
        self.store = SQLiteTaskStore(self.database_path)
        self.registry = RegistryService(self.store, clock=self._now)
        self.registry.register_dataset(dataset=dataset_ref())
        self.registry.register_algorithm(manifest=load_deepwave_manifest("1.6.0"))
        self.dispatcher = MultiNodeControlledDagDispatcher(self.store)
        self.service = self._new_service(self.store)
        self.scope = {
            "project_id": PROJECT_ID,
            "principal_id": PRINCIPAL_ID,
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _now(self) -> str:
        return self.clock_value.isoformat().replace("+00:00", "Z")

    def _task_id_factory(self) -> str:
        self.next_task += 1
        return f"task-dag-runtime-{self.next_task:03d}"

    def _new_service(self, store: SQLiteTaskStore) -> TaskService:
        return TaskService(
            store,
            task_id_factory=self._task_id_factory,
            clock=self._now,
            dispatcher=self.dispatcher,
        )

    def _approved_dag(
        self,
        suffix: str,
        *,
        fixed_recipe: bool = False,
        fixed_recipe_version: str = "1.0.0",
        include_result_check: bool = False,
        iterations: int = 2,
    ) -> tuple[str, dict]:
        draft = current_optimizer_task_draft()
        draft["draft_id"] = f"draft-dag-runtime-{suffix}"
        draft["parameters"]["iterations"] = iterations
        if fixed_recipe:
            draft["extensions"] = {
                "org.agent_rpc.recipe": {
                    "id": "forward_qc_fwi",
                    "version": fixed_recipe_version,
                }
            }
        created = self.service.create_task(
            draft=draft,
            idempotency_key=f"create-dag-runtime-{suffix}",
            **self.scope,
        )
        task_id = created.snapshot.task_id

        plan = current_optimizer_plan_graph()
        plan["plan_id"] = f"plan-dag-runtime-{suffix}"
        plan["draft"] = {
            "draft_id": draft["draft_id"],
            "revision": draft["revision"],
        }
        template = copy.deepcopy(plan["nodes"][0])
        dependencies = (
            {
                "data_check": [],
                "forward": ["data_check"],
                "quality_check": ["data_check"],
                "fwi": ["forward", "quality_check"],
                "result_check": ["fwi"],
            }
            if fixed_recipe
            else {
                "a": [],
                "b": ["a"],
                "c": ["a"],
                "d": ["b", "c"],
            }
        )
        if include_result_check and not fixed_recipe:
            dependencies["e"] = ["d"]
        nodes = []
        for node_id, required in dependencies.items():
            node = copy.deepcopy(template)
            node["node_id"] = node_id
            node["dependencies"] = required
            node["idempotency_key"] = f"{task_id}:{node_id}:0001"
            node["parameters"]["iterations"] = iterations
            if fixed_recipe and fixed_recipe_version == "1.0.0":
                node["inputs"] = fixed_recipe_plan_inputs(
                    node_id, template["inputs"][0]["dataset"]
                )
                node["outputs"] = copy.deepcopy(
                    load_fixed_recipe_manifest()["plan_outputs"]
                )
            nodes.append(node)
        plan["nodes"] = nodes
        if fixed_recipe:
            plan["schema_version"] = "1.2.0"
            plan["extensions"] = {
                "org.agent_rpc.recipe": {
                    "id": "forward_qc_fwi",
                    "version": fixed_recipe_version,
                }
            }
        plan["plan_hash"] = compute_plan_hash(plan)
        self.service.persist_plan(task_id=task_id, plan=plan, **self.scope)

        approval = approval_decision(plan)
        approval["approval_id"] = f"approval-dag-runtime-{suffix}"
        approval["scope"]["algorithms"] = [
            copy.deepcopy(template["algorithm"])
        ]
        self.service.persist_approval(
            task_id=task_id,
            approval=approval,
            **self.scope,
        )
        return task_id, plan

    def _acquire(self, owner_id: str):
        return self.service.acquire_runtime_supervisor_lease(
            owner_id=owner_id,
            lease_seconds=2,
            **self.scope,
        ).lease

    def _crash_restart(self, owner_id: str):
        """Expire the old term, reopen SQLite, and rebuild service state."""

        self.clock_value += timedelta(seconds=3)
        self.store = SQLiteTaskStore(self.database_path)
        self.dispatcher.store = self.store
        self.service = self._new_service(self.store)
        return self._acquire(owner_id)

    def _advance(self, task_id: str, lease):
        result = self.service.advance_runtime_dag(
            task_id,
            supervisor_lease=lease,
            **self.scope,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.aggregate_status, result.snapshot.status)
        return result

    def _node_states(self, task_id: str) -> dict[str, tuple[int, str]]:
        snapshot = self.store.get_dag_node_state_snapshot(
            task_id=task_id,
            **self.scope,
        )
        self.assertIsNotNone(snapshot)
        return {
            node.node_id: (node.revision, node.state)
            for node in snapshot.nodes
        }

    def _active_intent(self, task_id: str):
        intent = self.store.get_dispatch_intent(task_id)
        self.assertIsNotNone(intent)
        return intent

    def _start_active(self, task_id: str, lease, expected_node_id: str):
        scheduled = self.service.schedule_runtime_dispatch(
            task_id,
            supervisor_lease=lease,
            **self.scope,
        )
        self.assertEqual(scheduled.intent.node_id, expected_node_id)
        self.assertEqual(scheduled.intent.state, "dispatched")
        self.dispatcher.set_status(
            scheduled.intent,
            state="Running",
            updated_at=self._now(),
        )
        running = self.service.refresh_runtime_status(
            task_id,
            supervisor_lease=lease,
            **self.scope,
        )
        self.assertEqual(
            self._node_states(task_id)[expected_node_id][1], "Running"
        )
        return running

    def _finish_active(
        self,
        task_id: str,
        lease,
        expected_node_id: str,
        *,
        state: str,
    ):
        intent = self._active_intent(task_id)
        self.assertEqual(intent.node_id, expected_node_id)
        if state == "Succeeded":
            manifests, artifact_data = (
                task_service_fixtures.ScientificRuntimeTaskServiceTest.artifact_manifests(
                    self, task_id
                )
            )
            set_node_outputs = getattr(self.dispatcher, "set_node_outputs", None)
            if callable(set_node_outputs):
                set_node_outputs(intent, manifests, artifact_data)
            else:
                self.dispatcher.manifests = manifests
                self.dispatcher.artifact_data = artifact_data
        self.dispatcher.set_terminal_heartbeat(
            intent, state=state.lower()
        )
        self.dispatcher.set_status(intent, state=state, updated_at=self._now())
        projection = self.service.project_worker_attempt(
            task_id,
            supervisor_lease=lease,
            **self.scope,
        )
        self.assertTrue(projection.projected)
        result = self.service.refresh_runtime_status(
            task_id,
            supervisor_lease=lease,
            **self.scope,
        )
        self.assertEqual(self._node_states(task_id)[expected_node_id][1], state)
        return result

    def _run_success(self, task_id: str, lease, node_id: str) -> None:
        self._start_active(task_id, lease, node_id)
        self._finish_active(
            task_id,
            lease,
            node_id,
            state="Succeeded",
        )

    def _dispatch_count(self, task_id: str, node_id: str) -> int:
        return self.dispatcher.dispatch_counts[(task_id, node_id)]

    def _cacheable_source_and_target(
        self,
        suffix: str,
        *,
        tampering: bool = False,
    ):
        dispatcher_type = (
            CacheVerificationTamperingDispatcher
            if tampering
            else ReproducibleMultiNodeDagDispatcher
        )
        self.dispatcher = dispatcher_type(self.store)
        self.service = self._new_service(self.store)
        source_task, _ = self._approved_dag(f"{suffix}-source")
        source_lease = self._acquire(f"{suffix}-source")
        self.assertEqual(
            self._advance(source_task, source_lease).admitted_node_id, "a"
        )
        connection = sqlite3.connect(self.database_path)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM dag_node_cache_entries "
                    "WHERE source_task_id = ?",
                    (source_task,),
                ).fetchone()[0],
                0,
            )
        finally:
            connection.close()
        self._run_success(source_task, source_lease, "a")
        self.service.release_runtime_supervisor_lease(source_lease)

        target_task, _ = self._approved_dag(f"{suffix}-target")
        target_lease = self._acquire(f"{suffix}-target")
        return source_task, target_task, target_lease

    def _assert_target_has_no_worker_or_cache_facts(self, task_id: str) -> None:
        connection = sqlite3.connect(self.database_path)
        try:
            for table in (
                "dag_node_cache_hit_facts",
                "dispatch_intents",
                "dag_node_execution_admissions",
            ):
                task_column = (
                    "target_task_id"
                    if table == "dag_node_cache_hit_facts"
                    else "task_id"
                )
                self.assertEqual(
                    connection.execute(
                        f"SELECT COUNT(*) FROM {table} "
                        f"WHERE {task_column} = ?",
                        (task_id,),
                    ).fetchone()[0],
                    0,
                    table,
                )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM worker_launch_attempts AS attempt "
                    "JOIN dispatch_intents AS intent "
                    "ON intent.intent_id = attempt.intent_id "
                    "WHERE intent.task_id = ?",
                    (task_id,),
                ).fetchone()[0],
                0,
            )
        finally:
            connection.close()

    def _table_count(self, table: str, task_id: str) -> int:
        self.assertIn(
            table,
            {
                "dag_node_execution_admissions",
                "dag_node_terminal_facts",
                "dispatch_intents",
                "task_cancel_outcomes",
            },
        )
        connection = sqlite3.connect(self.database_path)
        try:
            return int(
                connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE task_id = ?",
                    (task_id,),
                ).fetchone()[0]
            )
        finally:
            connection.close()

    def test_reproducible_scope_local_cache_hit_starts_no_worker(self) -> None:
        self.dispatcher = ReproducibleMultiNodeDagDispatcher(self.store)
        self.service = self._new_service(self.store)
        source_task, _ = self._approved_dag("cache-source")
        source_lease = self._acquire("cache-source")
        self.assertEqual(
            self._advance(source_task, source_lease).admitted_node_id, "a"
        )
        self._run_success(source_task, source_lease, "a")
        self.service.release_runtime_supervisor_lease(source_lease)

        target_task, _ = self._approved_dag("cache-target")
        target_lease = self._acquire("cache-target")
        dispatches_before = self.dispatcher.dispatch_calls
        hit = self._advance(target_task, target_lease)

        self.assertEqual(hit.cache_hit_node_id, "a")
        self.assertRegex(hit.cache_key_hash, r"^sha256:[0-9a-f]{64}$")
        self.assertIsNone(hit.active_intent)
        self.assertIsNone(hit.admitted_node_id)
        self.assertEqual(hit.snapshot.status, "Running")
        self.assertEqual(self._node_states(target_task)["a"], (2, "Succeeded"))
        self.assertEqual(self._dispatch_count(target_task, "a"), 0)
        self.assertEqual(self.dispatcher.dispatch_calls, dispatches_before)

        connection = sqlite3.connect(self.database_path)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM dag_node_cache_entries "
                    "WHERE source_task_id = ? AND source_node_id = 'a'",
                    (source_task,),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM dag_node_cache_hit_facts "
                    "WHERE target_task_id = ? AND target_node_id = 'a'",
                    (target_task,),
                ).fetchone()[0],
                1,
            )
            cache_event = json.loads(
                connection.execute(
                    "SELECT document_json FROM run_events "
                    "WHERE task_id = ? AND event_type = 'node_succeeded'",
                    (target_task,),
                ).fetchone()[0]
            )
            cache_extension = cache_event["extensions"][
                "org.agent_rpc.node_cache"
            ]
            self.assertEqual(cache_extension["state"], "hit")
            self.assertFalse(cache_extension["worker_runtime_started"])
            for table in (
                "dispatch_intents",
                "dag_node_execution_admissions",
                "worker_launch_attempts",
                "worker_retry_reservations",
                "worker_exit_retry_reservations",
            ):
                task_column = (
                    "task_id"
                    if table in {"dispatch_intents", "dag_node_execution_admissions"}
                    else None
                )
                if task_column is not None:
                    count = connection.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE task_id = ?",
                        (target_task,),
                    ).fetchone()[0]
                else:
                    count = connection.execute(
                        f"SELECT COUNT(*) FROM {table} AS fact "
                        "JOIN dispatch_intents AS intent "
                        "ON intent.intent_id = fact.intent_id "
                        "WHERE intent.task_id = ?",
                        (target_task,),
                    ).fetchone()[0]
                self.assertEqual(count, 0, table)
            for table in (
                "dag_node_cache_entries",
                "dag_node_cache_hit_facts",
            ):
                for operation in ("UPDATE", "DELETE"):
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

        connection = sqlite3.connect(self.database_path)
        try:
            receipt_before = connection.execute(
                "SELECT cache_key_hash, output_receipt_document_hash "
                "FROM dag_node_cache_hit_facts WHERE target_task_id = ?",
                (target_task,),
            ).fetchone()
        finally:
            connection.close()
        target_lease = self._crash_restart("cache-target-restart")
        restarted = self._advance(target_task, target_lease)
        self.assertEqual(restarted.admitted_node_id, "b")
        self.assertEqual(self._node_states(target_task)["a"], (2, "Succeeded"))
        self.assertEqual(self._dispatch_count(target_task, "a"), 0)
        connection = sqlite3.connect(self.database_path)
        try:
            receipt_after = connection.execute(
                "SELECT cache_key_hash, output_receipt_document_hash "
                "FROM dag_node_cache_hit_facts WHERE target_task_id = ?",
                (target_task,),
            ).fetchone()
            self.assertEqual(receipt_after, receipt_before)
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM worker_launch_attempts AS attempt "
                    "JOIN dispatch_intents AS intent "
                    "ON intent.intent_id = attempt.intent_id "
                    "WHERE intent.task_id = ?",
                    (target_task,),
                ).fetchone()[0],
                0,
            )
        finally:
            connection.close()

    def test_nonempty_v22_upgrades_in_place_preserving_active_cache_and_checkpoint_facts(
        self,
    ) -> None:
        legacy_migrations = Path(self.temporary.name) / "v22-migrations"
        legacy_migrations.mkdir(mode=0o700)
        for migration in sorted(
            task_store_module.MIGRATIONS_DIRECTORY.glob(
                "[0-9][0-9][0-9][0-9]_*.sql"
            )
        ):
            if int(migration.name.split("_", 1)[0]) <= 22:
                shutil.copy2(migration, legacy_migrations / migration.name)

        self.database_path = Path(self.temporary.name) / "nonempty-v22.sqlite3"
        with mock.patch.object(
            task_store_module,
            "MIGRATIONS_DIRECTORY",
            legacy_migrations,
        ):
            self.store = SQLiteTaskStore(self.database_path)
        self.assertEqual(self.store.migration_version(), 22)
        self.registry = RegistryService(self.store, clock=self._now)
        self.registry.register_dataset(dataset=dataset_ref())
        self.registry.register_algorithm(manifest=load_deepwave_manifest("1.6.0"))
        self.dispatcher = ReproducibleMultiNodeDagDispatcher(self.store)
        self.service = self._new_service(self.store)

        source_task, cache_target, cache_lease = self._cacheable_source_and_target(
            "v22-upgrade-cache"
        )
        cache_hit = self._advance(cache_target, cache_lease)
        self.assertEqual(cache_hit.cache_hit_node_id, "a")
        self.service.release_runtime_supervisor_lease(cache_lease)

        active_task, _ = self._approved_dag("v22-upgrade-active", iterations=3)
        active_lease = self._acquire("v22-upgrade-active")
        active = self._advance(active_task, active_lease)
        self.assertEqual(active.admitted_node_id, "a")
        self.assertEqual(self._node_states(active_task)["a"], (2, "Queued"))
        self.service.release_runtime_supervisor_lease(active_lease)

        checkpoint_task, _ = self._approved_dag(
            "v22-upgrade-checkpoint", iterations=4
        )
        checkpoint_lease = self._acquire("v22-upgrade-checkpoint")
        self.assertEqual(
            self._advance(checkpoint_task, checkpoint_lease).admitted_node_id,
            "a",
        )
        self._start_active(checkpoint_task, checkpoint_lease, "a")
        checkpoint_intent = self._active_intent(checkpoint_task)
        observation = copy.deepcopy(
            self.dispatcher._observations[checkpoint_intent.intent_id]
        )
        evidence = observation["evidence"]
        waiting_evidence = managed_worker_evidence(
            attempt_id=evidence["attempt_id"],
            attempt_number=evidence["attempt_number"],
            job_id=evidence["job_id"],
            request_hash=evidence["request_hash"],
            heartbeat_sequence=evidence["heartbeat"]["sequence"] + 1,
            heartbeat_state="waiting",
        )
        waiting_observation = {
            "evidence": waiting_evidence,
            "handle": copy.deepcopy(observation["handle"]),
        }
        self.dispatcher._observations[checkpoint_intent.intent_id] = copy.deepcopy(
            waiting_observation
        )
        self.dispatcher.worker_observation = copy.deepcopy(waiting_observation)
        projected = self.service.project_worker_attempt(
            checkpoint_task,
            supervisor_lease=checkpoint_lease,
            **self.scope,
        )
        self.assertTrue(projected.projected)
        checkpoint_proof = task_service_fixtures.checkpoint_wait_proof(
            checkpoint_task,
            waiting_observation,
            checkpoint_created_at="2026-07-15T03:00:10.000000Z",
        )
        checkpoint_proof["node_id"] = "a"
        checkpoint_proof["proof_hash"] = encode_document(
            {
                key: value
                for key, value in checkpoint_proof.items()
                if key != "proof_hash"
            }
        )[1]
        self.dispatcher.checkpoint_probe_result = copy.deepcopy(checkpoint_proof)
        waiting = self.service.process_runtime_checkpoint(
            checkpoint_task,
            supervisor_lease=checkpoint_lease,
            **self.scope,
        )
        self.assertEqual((waiting.state, waiting.snapshot.status), ("waiting", "Waiting"))

        preserved_tables = (
            "dag_node_execution_admissions",
            "dag_node_cache_entries",
            "dag_node_cache_hit_facts",
            "worker_checkpoint_waits",
        )
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            before = {
                table: [
                    dict(row)
                    for row in connection.execute(
                        f"SELECT * FROM {table} ORDER BY rowid"
                    ).fetchall()
                ]
                for table in preserved_tables
            }
            self.assertTrue(before["dag_node_execution_admissions"])
            self.assertTrue(before["dag_node_cache_entries"])
            self.assertEqual(len(before["dag_node_cache_hit_facts"]), 1)
            self.assertEqual(len(before["worker_checkpoint_waits"]), 1)
        finally:
            connection.close()

        upgraded = SQLiteTaskStore(self.database_path)
        self.assertEqual(upgraded.migration_version(), 23)
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            after = {
                table: [
                    dict(row)
                    for row in connection.execute(
                        f"SELECT * FROM {table} ORDER BY rowid"
                    ).fetchall()
                ]
                for table in preserved_tables
            }
            self.assertEqual(after, before)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(
                [row[0] for row in connection.execute("PRAGMA quick_check")],
                ["ok"],
            )
        finally:
            connection.close()
        self.assertEqual(
            upgraded.get_dag_node_state_snapshot(
                task_id=active_task,
                **self.scope,
            ).nodes[0].state,
            "Queued",
        )
        self.assertEqual(upgraded.get_task(checkpoint_task).status, "Waiting")
        self.assertEqual(
            before["dag_node_cache_entries"][0]["source_task_id"],
            source_task,
        )

    def test_parameter_change_is_a_cache_miss_and_starts_a_worker(self) -> None:
        self.dispatcher = ReproducibleMultiNodeDagDispatcher(self.store)
        self.service = self._new_service(self.store)
        source_task, source_plan = self._approved_dag(
            "cache-parameter-source",
            iterations=2,
        )
        source_lease = self._acquire("cache-parameter-source")
        self.assertEqual(
            self._advance(source_task, source_lease).admitted_node_id, "a"
        )
        self._run_success(source_task, source_lease, "a")
        self.service.release_runtime_supervisor_lease(source_lease)

        target_task, target_plan = self._approved_dag(
            "cache-parameter-target",
            iterations=3,
        )
        self.assertNotEqual(source_plan["plan_hash"], target_plan["plan_hash"])
        target_lease = self._acquire("cache-parameter-target")
        dispatches_before = self.dispatcher.dispatch_calls

        miss = self._advance(target_task, target_lease)

        self.assertIsNone(miss.cache_hit_node_id)
        self.assertEqual(miss.admitted_node_id, "a")
        self.assertEqual(miss.active_intent.request["parameters"]["iterations"], 3)
        self.assertEqual(self.dispatcher.dispatch_calls, dispatches_before)
        self.assertEqual(self._dispatch_count(target_task, "a"), 0)

        self._start_active(target_task, target_lease, "a")
        self.assertEqual(self.dispatcher.dispatch_calls, dispatches_before + 1)
        self.assertEqual(self._dispatch_count(target_task, "a"), 1)
        connection = sqlite3.connect(self.database_path)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM dag_node_cache_hit_facts "
                    "WHERE target_task_id = ?",
                    (target_task,),
                ).fetchone()[0],
                0,
            )
        finally:
            connection.close()

    def test_cache_reverification_tampering_fails_closed_without_worker(self) -> None:
        _, target_task, target_lease = self._cacheable_source_and_target(
            "cache-reverify-tamper", tampering=True
        )
        dispatcher = self.dispatcher
        self.assertIsInstance(dispatcher, CacheVerificationTamperingDispatcher)
        dispatches_before = dispatcher.dispatch_calls

        for mode in (
            "missing",
            "bytes",
            "manifest",
            "lineage",
            "content_hash",
            "size",
            "media_type",
            "schema_version",
            "symlink",
        ):
            with self.subTest(mode=mode):
                dispatcher.cache_verification_tamper = mode
                with self.assertRaises(TaskDispatchError):
                    self._advance(target_task, target_lease)
                self.assertEqual(dispatcher.dispatch_calls, dispatches_before)
                self.assertEqual(self._dispatch_count(target_task, "a"), 0)
                self._assert_target_has_no_worker_or_cache_facts(target_task)

        dispatcher.cache_verification_tamper = None
        hit = self._advance(target_task, target_lease)
        self.assertEqual(hit.cache_hit_node_id, "a")
        self.assertEqual(dispatcher.dispatch_calls, dispatches_before)
        self.assertEqual(self._dispatch_count(target_task, "a"), 0)

    def test_cache_permission_document_tampering_fails_closed(self) -> None:
        source_task, target_task, target_lease = (
            self._cacheable_source_and_target("cache-permission-tamper")
        )
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "DROP TRIGGER dag_node_cache_entries_are_append_only"
            )
            connection.execute(
                "UPDATE dag_node_cache_entries "
                "SET cache_key_document_json = json_set("
                "cache_key_document_json, "
                "'$.permission_scope.principal_id', 'attacker') "
                "WHERE source_task_id = ? AND source_node_id = 'a'",
                (source_task,),
            )
            connection.commit()
        finally:
            connection.close()

        dispatches_before = self.dispatcher.dispatch_calls
        with self.assertRaises(TaskStoreCorruption):
            self._advance(target_task, target_lease)
        self.assertEqual(self.dispatcher.dispatch_calls, dispatches_before)
        self._assert_target_has_no_worker_or_cache_facts(target_task)

    def test_cache_lineage_document_tampering_fails_closed(self) -> None:
        source_task, target_task, target_lease = (
            self._cacheable_source_and_target("cache-lineage-tamper")
        )
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "DROP TRIGGER dag_node_cache_entries_are_append_only"
            )
            connection.execute(
                "UPDATE dag_node_cache_entries "
                "SET trusted_lineage_document_json = json_set("
                "trusted_lineage_document_json, "
                "'$.dataset_roots[0].content_hash', "
                "'sha256:3333333333333333333333333333333333333333333333333333333333333333') "
                "WHERE source_task_id = ? AND source_node_id = 'a'",
                (source_task,),
            )
            connection.commit()
        finally:
            connection.close()

        dispatches_before = self.dispatcher.dispatch_calls
        with self.assertRaises(TaskStoreCorruption):
            self._advance(target_task, target_lease)
        self.assertEqual(self.dispatcher.dispatch_calls, dispatches_before)
        self._assert_target_has_no_worker_or_cache_facts(target_task)

    def test_missing_cache_entry_is_a_no_reuse_miss(self) -> None:
        source_task, target_task, target_lease = (
            self._cacheable_source_and_target("cache-entry-missing")
        )
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "DROP TRIGGER dag_node_cache_entries_cannot_be_deleted"
            )
            connection.execute(
                "DELETE FROM dag_node_cache_entries "
                "WHERE source_task_id = ? AND source_node_id = 'a'",
                (source_task,),
            )
            connection.commit()
        finally:
            connection.close()

        dispatches_before = self.dispatcher.dispatch_calls
        miss = self._advance(target_task, target_lease)
        self.assertIsNone(miss.cache_hit_node_id)
        self.assertEqual(miss.admitted_node_id, "a")
        self.assertEqual(self.dispatcher.dispatch_calls, dispatches_before)
        self.assertEqual(self._dispatch_count(target_task, "a"), 0)

    def test_chain_fan_out_and_fan_in_execute_in_deterministic_order(self) -> None:
        task_id, _ = self._approved_dag("fan-in-success")
        lease = self._acquire("fan-in-success")

        first = self._advance(task_id, lease)
        self.assertEqual(first.admitted_node_id, "a")
        self.assertEqual(self._node_states(task_id), {
            "a": (2, "Queued"),
            "b": (1, "Pending"),
            "c": (1, "Pending"),
            "d": (1, "Pending"),
        })
        self._run_success(task_id, lease, "a")

        second = self._advance(task_id, lease)
        self.assertEqual(second.admitted_node_id, "b")
        self.assertEqual(self._node_states(task_id)["a"][1], "Succeeded")
        self.assertEqual(self._node_states(task_id)["c"][1], "Pending")
        self._run_success(task_id, lease, "b")

        third = self._advance(task_id, lease)
        self.assertEqual(third.admitted_node_id, "c")
        self.assertEqual(self._node_states(task_id)["d"][1], "Pending")
        self._run_success(task_id, lease, "c")

        fourth = self._advance(task_id, lease)
        self.assertEqual(fourth.admitted_node_id, "d")
        states_before_d = self._node_states(task_id)
        self.assertEqual(
            (states_before_d["b"][1], states_before_d["c"][1]),
            ("Succeeded", "Succeeded"),
        )
        completed = self._start_active(task_id, lease, "d")
        self.assertEqual(completed.snapshot.status, "Running")
        completed = self._finish_active(
            task_id, lease, "d", state="Succeeded"
        )

        self.assertEqual(completed.snapshot.status, "Succeeded")
        self.assertEqual(
            [self._dispatch_count(task_id, node) for node in "abcd"],
            [1, 1, 1, 1],
        )
        self.assertEqual(self._table_count("dispatch_intents", task_id), 4)
        self.assertEqual(
            self._table_count("dag_node_terminal_facts", task_id), 4
        )

    def test_dag_successor_can_wait_queued_while_task_is_running(self) -> None:
        task_id, _ = self._approved_dag("successor-resource-wait")
        lease = self._acquire("successor-resource-wait")
        self.assertEqual(self._advance(task_id, lease).admitted_node_id, "a")
        self._run_success(task_id, lease, "a")

        successor = self._advance(task_id, lease)
        self.assertEqual(successor.admitted_node_id, "b")
        scheduled = self.service.schedule_runtime_dispatch(
            task_id,
            supervisor_lease=lease,
            **self.scope,
        )
        self.assertEqual(
            (scheduled.intent.node_id, scheduled.intent.state),
            ("b", "dispatched"),
        )

        observed = self.service.refresh_runtime_status(
            task_id,
            supervisor_lease=lease,
            **self.scope,
        )

        self.assertEqual(observed.snapshot.status, "Running")
        self.assertEqual(observed.adapter_status["status"], "Queued")
        self.assertEqual(self._node_states(task_id)["b"][1], "Queued")
        self.assertEqual(self._dispatch_count(task_id, "b"), 1)

    def test_fixed_recipe_fan_out_overlaps_and_restart_keeps_one_dispatch(
        self,
    ) -> None:
        task_id, _ = self._approved_dag(
            "fixed-recipe-parallel",
            fixed_recipe=True,
            include_result_check=True,
        )
        lease = self._acquire("fixed-recipe-parallel-first")
        self.assertEqual(
            self._advance(task_id, lease).admitted_node_id, "data_check"
        )
        self._run_success(task_id, lease, "data_check")

        admitted_b = self._advance(task_id, lease)
        self.assertEqual(admitted_b.admitted_node_id, "forward")
        self._start_active(task_id, lease, "forward")
        admitted_c = self._advance(task_id, lease)
        self.assertEqual(admitted_c.admitted_node_id, "quality_check")
        self.assertEqual(
            tuple(intent.node_id for intent in admitted_c.active_intents),
            ("forward", "quality_check"),
        )
        self._start_active(task_id, lease, "quality_check")
        self.assertEqual(
            {node: state for node, (_revision, state) in self._node_states(task_id).items()},
            {
                "data_check": "Succeeded",
                "forward": "Running",
                "quality_check": "Running",
                "fwi": "Pending",
                "result_check": "Pending",
            },
        )
        facts = self.service.get_dag_runtime_node_facts(task_id, **self.scope)
        self.assertEqual(
            set(facts),
            {
                "schema_version",
                "task_id",
                "plan_id",
                "plan_hash",
                "runtime_initialized",
                "nodes",
            },
        )
        facts_by_node = {node["node_id"]: node for node in facts["nodes"]}
        data_check_hashes_before = tuple(
            (
                output["port"],
                output["artifact_manifest"]["content_hash"],
                output["artifact_manifest_hash"],
            )
            for output in facts_by_node["data_check"]["outputs"]
        )
        self.assertEqual(
            (
                facts_by_node["forward"]["state"],
                facts_by_node["quality_check"]["state"],
            ),
            ("Running", "Running"),
        )
        self.assertNotEqual(
            facts_by_node["forward"]["admission"]["intent_id"],
            facts_by_node["quality_check"]["admission"]["intent_id"],
        )
        for intent in admitted_c.active_intents:
            self.assertEqual(
                intent.request["recipe"],
                {"id": "forward_qc_fwi", "version": "1.0.0"},
            )
        self.assertTrue(facts_by_node["data_check"]["outputs"])
        self.assertRegex(
            facts_by_node["data_check"]["lineage"]["document_hash"],
            r"^sha256:[0-9a-f]{64}$",
        )
        with self.assertRaises(TaskNotFound):
            self.service.get_dag_runtime_node_facts(
                task_id,
                project_id=PROJECT_ID,
                principal_id="other-user",
            )
        counts_before = tuple(
            self._dispatch_count(task_id, node)
            for node in ("forward", "quality_check")
        )

        lease = self._crash_restart("fixed-recipe-parallel-successor")
        recovered = self._advance(task_id, lease)
        self.assertEqual(
            tuple(intent.node_id for intent in recovered.active_intents),
            ("forward", "quality_check"),
        )
        self.assertEqual(recovered.active_intent.node_id, "quality_check")
        self.assertEqual(
            recovered.active_intent.request["recipe"],
            {"id": "forward_qc_fwi", "version": "1.0.0"},
        )
        restarted_facts = self.service.get_dag_runtime_node_facts(
            task_id, **self.scope
        )
        restarted_data_check = next(
            node
            for node in restarted_facts["nodes"]
            if node["node_id"] == "data_check"
        )
        self.assertEqual(
            tuple(
                (
                    output["port"],
                    output["artifact_manifest"]["content_hash"],
                    output["artifact_manifest_hash"],
                )
                for output in restarted_data_check["outputs"]
            ),
            data_check_hashes_before,
        )
        self.assertEqual(self._dispatch_count(task_id, "data_check"), 1)
        replay = self.service.schedule_runtime_dispatch(
            task_id, supervisor_lease=lease, **self.scope
        )
        self.assertEqual(
            (replay.intent.node_id, replay.intent.state),
            ("quality_check", "dispatched"),
        )
        self.assertEqual(
            tuple(
                self._dispatch_count(task_id, node)
                for node in ("forward", "quality_check")
            ),
            counts_before,
        )

        self._finish_active(task_id, lease, "quality_check", state="Failed")
        blocked = self._advance(task_id, lease)
        self.assertEqual(blocked.active_intent.node_id, "forward")
        self.assertEqual(
            (
                self._node_states(task_id)["fwi"][1],
                self._node_states(task_id)["result_check"][1],
            ),
            ("Blocked", "Blocked"),
        )
        self._finish_active(task_id, lease, "forward", state="Succeeded")
        terminal = self._advance(task_id, lease)
        self.assertEqual(terminal.snapshot.status, "Failed")
        self.assertEqual(
            [
                self._dispatch_count(task_id, node)
                for node in (
                    "data_check",
                    "forward",
                    "quality_check",
                    "fwi",
                    "result_check",
                )
            ],
            [1, 1, 1, 0, 0],
        )
        terminal_facts = self.service.get_dag_runtime_node_facts(
            task_id, **self.scope
        )
        terminal_by_node = {
            node["node_id"]: node for node in terminal_facts["nodes"]
        }
        self.assertEqual(
            terminal_by_node["quality_check"]["failure"]["code"],
            "ADAPTER_FAILED",
        )
        self.assertEqual(
            terminal_by_node["result_check"]["failure"],
            {
                "code": "DEPENDENCY_FAILED",
                "blocked_by_node_ids": ["quality_check"],
            },
        )

    def _assert_parallel_worker_exit_converges(self, failed_node_id: str) -> None:
        survivor_node_id = (
            "quality_check" if failed_node_id == "forward" else "forward"
        )
        task_id, _ = self._approved_dag(
            f"parallel-worker-exit-{failed_node_id}",
            fixed_recipe=True,
            include_result_check=True,
        )
        lease = self._acquire(f"parallel-worker-exit-{failed_node_id}-first")
        self.assertEqual(
            self._advance(task_id, lease).admitted_node_id, "data_check"
        )
        self._run_success(task_id, lease, "data_check")
        self.assertEqual(self._advance(task_id, lease).admitted_node_id, "forward")
        self._start_active(task_id, lease, "forward")
        fan_out = self._advance(task_id, lease)
        self.assertEqual(fan_out.admitted_node_id, "quality_check")
        self._start_active(task_id, lease, "quality_check")
        active_by_node = {
            intent.node_id: intent
            for intent in self.store.list_dispatch_intents(task_id)
            if intent.node_id in {"forward", "quality_check"}
        }
        failed_intent = active_by_node[failed_node_id]
        self.dispatcher.set_terminal_heartbeat(failed_intent, state="failed")
        self.dispatcher.set_status(
            failed_intent,
            state="Failed",
            updated_at=self._now(),
        )
        self.dispatcher._statuses[failed_intent.intent_id]["stage"] = "worker_exit"
        self.dispatcher._statuses[failed_intent.intent_id]["message"] = (
            "controlled nonzero Worker exit"
        )
        probes_before = self.dispatcher.worker_exit_probe_calls

        runtime = RuntimeSupervisor(
            self.service,
            project_id=PROJECT_ID,
            principal_id=PRINCIPAL_ID,
            owner_id=f"parallel-worker-exit-{failed_node_id}-first",
            lease_seconds=2,
            heartbeat_interval_seconds=1,
        )
        cycle, _, _ = runtime._observe_tasks(
            [self.service.get_task(task_id, **self.scope)],
            lease,
            float("inf"),
        )

        states = self._node_states(task_id)
        self.assertEqual(states[failed_node_id][1], "Failed")
        self.assertEqual(states[survivor_node_id][1], "Running")
        self.assertEqual(states["fwi"][1], "Blocked")
        self.assertEqual(states["result_check"][1], "Blocked")
        self.assertEqual(self.service.get_task(task_id, **self.scope).status, "Running")
        self.assertEqual(cycle.refreshed_task_ids, (task_id,))
        self.assertEqual(cycle.task_failures, ())
        self.assertEqual(
            self.dispatcher.worker_exit_probe_calls,
            probes_before + 1,
        )
        self.assertEqual(
            [
                self._dispatch_count(task_id, node_id)
                for node_id in (
                    "data_check",
                    "forward",
                    "quality_check",
                    "fwi",
                    "result_check",
                )
            ],
            [1, 1, 1, 0, 0],
        )

        lease = self._crash_restart(
            f"parallel-worker-exit-{failed_node_id}-restart"
        )
        recovered = self._advance(task_id, lease)
        self.assertEqual(recovered.active_intent.node_id, survivor_node_id)
        self.assertEqual(
            tuple(intent.node_id for intent in recovered.active_intents),
            (survivor_node_id,),
        )
        recovered_states = self._node_states(task_id)
        self.assertEqual(recovered_states[failed_node_id][1], "Failed")
        self.assertEqual(recovered_states["fwi"][1], "Blocked")
        self.assertEqual(recovered_states["result_check"][1], "Blocked")

        terminal = self._finish_active(
            task_id,
            lease,
            survivor_node_id,
            state="Succeeded",
        )
        self.assertEqual(terminal.snapshot.status, "Failed")
        self.assertEqual(
            [
                self._dispatch_count(task_id, node_id)
                for node_id in (
                    "data_check",
                    "forward",
                    "quality_check",
                    "fwi",
                    "result_check",
                )
            ],
            [1, 1, 1, 0, 0],
        )

    def test_parallel_newer_branch_worker_exit_converges_after_restart(self) -> None:
        self._assert_parallel_worker_exit_converges("quality_check")

    def test_parallel_earlier_branch_worker_exit_does_not_wait_for_newer(self) -> None:
        self._assert_parallel_worker_exit_converges("forward")

    def test_parallel_worker_exit_deferred_code_is_plan_ordered(self) -> None:
        task_id, _ = self._approved_dag(
            "parallel-worker-exit-deferred",
            fixed_recipe=True,
            include_result_check=True,
        )
        lease = self._acquire("parallel-worker-exit-deferred")
        self.assertEqual(
            self._advance(task_id, lease).admitted_node_id, "data_check"
        )
        self._run_success(task_id, lease, "data_check")
        self.assertEqual(self._advance(task_id, lease).admitted_node_id, "forward")
        self._start_active(task_id, lease, "forward")
        self.assertEqual(
            self._advance(task_id, lease).admitted_node_id, "quality_check"
        )
        self._start_active(task_id, lease, "quality_check")
        active_by_node = {
            intent.node_id: intent
            for intent in self.store.list_dispatch_intents(task_id)
            if intent.node_id in {"forward", "quality_check"}
        }
        for node_id, intent in active_by_node.items():
            self.dispatcher.set_terminal_heartbeat(intent, state="failed")
            self.dispatcher.set_status(
                intent,
                state="Failed",
                updated_at=self._now(),
            )
            self.dispatcher._statuses[intent.intent_id]["stage"] = "worker_exit"
            self.dispatcher._statuses[intent.intent_id]["message"] = (
                f"controlled {node_id} nonzero Worker exit"
            )
        self.dispatcher.worker_projection_deferred_codes = {
            "forward": "WORKER_EVIDENCE_NOT_READY",
            "quality_check": "WORKER_EVIDENCE_UNAVAILABLE",
        }

        deferred = self.service.process_runtime_retry(
            task_id,
            supervisor_lease=lease,
            **self.scope,
        )

        self.assertEqual(deferred.state, "none")
        self.assertEqual(deferred.deferred_code, "WORKER_EVIDENCE_NOT_READY")
        self.assertFalse(deferred.projected)
        states = self._node_states(task_id)
        self.assertEqual(states["forward"][1], "Running")
        self.assertEqual(states["quality_check"][1], "Running")
        self.assertEqual(self.dispatcher.worker_exit_probe_calls, 0)

        self.dispatcher.worker_projection_deferred_codes.clear()
        converged = self.service.process_runtime_retry(
            task_id,
            supervisor_lease=lease,
            **self.scope,
        )

        self.assertTrue(converged.projected)
        self.assertIsNone(converged.deferred_code)
        states = self._node_states(task_id)
        self.assertEqual(states["forward"][1], "Failed")
        self.assertEqual(states["quality_check"][1], "Failed")
        self.assertEqual(states["fwi"][1], "Blocked")
        self.assertEqual(states["result_check"][1], "Blocked")
        self.assertEqual(converged.snapshot.status, "Failed")
        self.assertEqual(self.dispatcher.worker_exit_probe_calls, 2)

        connection = sqlite3.connect(self.database_path)
        try:
            blocker_document = json.loads(
                connection.execute(
                    "SELECT blocker_document_json "
                    "FROM dag_node_scheduler_transition_facts "
                    "WHERE task_id = ? AND node_id = 'fwi'",
                    (task_id,),
                ).fetchone()[0]
            )
            blocker_document["blocked_by_node_ids"] = ["unknown-node"]
            blocker_json, blocker_hash = encode_document(blocker_document)
            connection.execute(
                "DROP TRIGGER dag_node_scheduler_transition_facts_are_append_only"
            )
            connection.execute(
                "UPDATE dag_node_scheduler_transition_facts "
                "SET blocker_document_json = ?, blocker_document_hash = ? "
                "WHERE task_id = ? AND node_id = 'fwi'",
                (blocker_json, blocker_hash, task_id),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(TaskStoreCorruption):
            self.store.get_dag_node_state_snapshot(
                task_id=task_id,
                **self.scope,
            )

    def test_fixed_recipe_restart_dispatches_admitted_branch_before_sibling(
        self,
    ) -> None:
        task_id, _ = self._approved_dag(
            "fixed-recipe-pre-dispatch-restart",
            fixed_recipe=True,
            include_result_check=True,
        )
        lease = self._acquire("fixed-recipe-pre-dispatch-first")
        self.assertEqual(
            self._advance(task_id, lease).admitted_node_id, "data_check"
        )
        self._run_success(task_id, lease, "data_check")

        forward = self._advance(task_id, lease)
        self.assertEqual(forward.admitted_node_id, "forward")
        self.assertEqual(forward.active_intent.state, "pending")
        self.assertEqual(self._dispatch_count(task_id, "forward"), 0)

        lease = self._crash_restart("fixed-recipe-pre-dispatch-successor")
        recovered = self._advance(task_id, lease)
        self.assertIsNone(recovered.admitted_node_id)
        self.assertEqual(
            [(intent.node_id, intent.state) for intent in recovered.active_intents],
            [("forward", "pending")],
        )
        self.assertEqual(self._node_states(task_id)["quality_check"][1], "Pending")

        scheduled = self.service.schedule_runtime_dispatch(
            task_id,
            supervisor_lease=lease,
            **self.scope,
        )
        self.assertEqual(
            (scheduled.intent.node_id, scheduled.intent.state),
            ("forward", "dispatched"),
        )
        before_running = self._advance(task_id, lease)
        self.assertIsNone(before_running.admitted_node_id)
        self.assertEqual(
            [(intent.node_id, intent.state) for intent in before_running.active_intents],
            [("forward", "dispatched")],
        )
        self.assertEqual(self._node_states(task_id)["quality_check"][1], "Pending")

        self.dispatcher.set_status(
            scheduled.intent,
            state="Running",
            updated_at=self._now(),
        )
        self.service.refresh_runtime_status(
            task_id,
            supervisor_lease=lease,
            **self.scope,
        )
        self.assertEqual(self._node_states(task_id)["forward"][1], "Running")
        quality = self._advance(task_id, lease)
        self.assertEqual(quality.admitted_node_id, "quality_check")
        self.assertEqual(
            [(intent.node_id, intent.state) for intent in quality.active_intents],
            [("forward", "dispatched"), ("quality_check", "pending")],
        )
        self.assertEqual(self._dispatch_count(task_id, "forward"), 1)

    def test_fixed_recipe_success_obeys_fan_in_and_records_timeline(self) -> None:
        task_id, plan = self._approved_dag(
            "fixed-recipe-success",
            fixed_recipe=True,
            include_result_check=True,
        )
        self.assertTrue(is_fixed_recipe_parallel_plan(plan))
        original_prepare = self.dispatcher.prepare_node

        def prepare_with_real_development_source(
            snapshot, *, node_id, input_binding
        ):
            prepared = original_prepare(
                snapshot, node_id=node_id, input_binding=input_binding
            )
            fingerprint = copy.deepcopy(prepared.queue_fingerprint)
            fingerprint["provenance_mode"] = "development"
            fingerprint["source"] = {
                "identity_complete": False,
                "dirty": True,
            }
            return replace(prepared, queue_fingerprint=fingerprint)

        # The real Adapter's best-effort Git probe reports dirty=True on a
        # candidate tree.  This remains development provenance, not a claim of
        # reproducibility, and must not block production Recipe admission.
        self.dispatcher.prepare_node = prepare_with_real_development_source
        lease = self._acquire("fixed-recipe-success")

        self.assertEqual(
            self._advance(task_id, lease).admitted_node_id, "data_check"
        )
        self._start_active(task_id, lease, "data_check")
        self.clock_value += timedelta(milliseconds=100)
        self._finish_active(task_id, lease, "data_check", state="Succeeded")

        self.clock_value += timedelta(milliseconds=100)
        self.assertEqual(
            self._advance(task_id, lease).admitted_node_id, "forward"
        )
        self._start_active(task_id, lease, "forward")
        self.clock_value += timedelta(milliseconds=100)
        quality_admission = self._advance(task_id, lease)
        self.assertEqual(quality_admission.admitted_node_id, "quality_check")
        self._start_active(task_id, lease, "quality_check")
        states = self._node_states(task_id)
        self.assertEqual(
            (states["forward"][1], states["quality_check"][1], states["fwi"][1]),
            ("Running", "Running", "Pending"),
        )

        self.clock_value += timedelta(milliseconds=100)
        self._finish_active(task_id, lease, "quality_check", state="Succeeded")
        waiting_for_forward = self._advance(task_id, lease)
        self.assertEqual(waiting_for_forward.active_intent.node_id, "forward")
        self.assertEqual(self._node_states(task_id)["fwi"][1], "Pending")
        self.clock_value += timedelta(milliseconds=100)
        self._finish_active(task_id, lease, "forward", state="Succeeded")

        self.clock_value += timedelta(milliseconds=100)
        fwi = self._advance(task_id, lease)
        self.assertEqual(fwi.admitted_node_id, "fwi")
        self._start_active(task_id, lease, "fwi")
        self.clock_value += timedelta(milliseconds=100)
        self._finish_active(task_id, lease, "fwi", state="Succeeded")

        self.clock_value += timedelta(milliseconds=100)
        result_check = self._advance(task_id, lease)
        self.assertEqual(result_check.admitted_node_id, "result_check")
        self._start_active(task_id, lease, "result_check")
        self.clock_value += timedelta(milliseconds=100)
        terminal = self._finish_active(
            task_id, lease, "result_check", state="Succeeded"
        )
        self.assertEqual(terminal.snapshot.status, "Succeeded")
        self.assertEqual(
            [
                self._dispatch_count(task_id, node)
                for node in (
                    "data_check",
                    "forward",
                    "quality_check",
                    "fwi",
                    "result_check",
                )
            ],
            [1, 1, 1, 1, 1],
        )
        for intent in self.store.list_dispatch_intents(task_id):
            self.assertEqual(
                intent.queue_fingerprint["source"],
                {"identity_complete": False, "dirty": True},
            )

        connection = sqlite3.connect(self.database_path)
        try:
            timeline = {
                (node_id, state): recorded_at_us
                for node_id, state, recorded_at_us in connection.execute(
                    "SELECT node_id, state, recorded_at_us "
                    "FROM dag_node_state_events WHERE task_id = ?",
                    (task_id,),
                )
            }
        finally:
            connection.close()
        self.assertLess(
            timeline[("data_check", "Succeeded")],
            timeline[("forward", "Running")],
        )
        self.assertLess(
            timeline[("forward", "Running")],
            timeline[("quality_check", "Running")],
        )
        self.assertLess(
            timeline[("quality_check", "Running")],
            timeline[("quality_check", "Succeeded")],
        )
        self.assertLess(
            timeline[("quality_check", "Succeeded")],
            timeline[("forward", "Succeeded")],
        )
        self.assertLess(
            timeline[("forward", "Succeeded")],
            timeline[("fwi", "Queued")],
        )
        self.assertLess(
            timeline[("fwi", "Succeeded")],
            timeline[("result_check", "Queued")],
        )
        self.assertLess(
            timeline[("result_check", "Running")],
            timeline[("result_check", "Succeeded")],
        )

        collected = self.service.collect_artifacts(task_id, **self.scope)
        self.assertEqual(len(collected), 8)
        self.assertEqual(
            Counter(value["node_id"] for value in collected),
            Counter(
                {
                    "data_check": 2,
                    "forward": 1,
                    "quality_check": 1,
                    "fwi": 2,
                    "result_check": 2,
                }
            ),
        )
        for manifest in collected:
            returned, data = self.service.read_artifact(
                task_id, manifest["artifact_id"], **self.scope
            )
            self.assertEqual(returned, manifest)
            self.assertEqual(
                "sha256:" + hashlib.sha256(data).hexdigest(),
                manifest["content_hash"],
            )

        trashed = self.service.trash_task(
            task_id=task_id,
            expected_visibility_revision=0,
            idempotency_key="trash-fixed-recipe-success",
            **self.scope,
        )
        purged = self.service.purge_task(
            task_id=task_id,
            expected_visibility_revision=trashed.snapshot.visibility_revision,
            idempotency_key="purge-fixed-recipe-success",
            **self.scope,
        )
        replay = self.service.purge_task(
            task_id=task_id,
            expected_visibility_revision=trashed.snapshot.visibility_revision,
            idempotency_key="purge-fixed-recipe-success",
            **self.scope,
        )
        self.assertEqual(purged.local_run_state, "deleted")
        self.assertFalse(purged.replayed)
        self.assertEqual(replay.purge_id, purged.purge_id)
        self.assertTrue(replay.replayed)
        self.assertEqual(self.dispatcher.purge_calls, 5)
        self.assertEqual(
            self.dispatcher.purge_ids, [purged.purge_id] * 5
        )

    def test_fixed_recipe_success_synchronizes_terminal_worker_evidence(self) -> None:
        task_id, _ = self._approved_dag(
            "fixed-recipe-terminal-evidence",
            fixed_recipe=True,
            include_result_check=True,
        )
        lease = self._acquire("fixed-recipe-terminal-evidence")
        self.assertEqual(
            self._advance(task_id, lease).admitted_node_id, "data_check"
        )
        self._start_active(task_id, lease, "data_check")
        intent = self._active_intent(task_id)
        manifests, artifact_data = (
            task_service_fixtures.ScientificRuntimeTaskServiceTest.artifact_manifests(
                self, task_id
            )
        )
        self.dispatcher.set_node_outputs(intent, manifests, artifact_data)
        self.dispatcher.set_terminal_heartbeat(intent, state="succeeded")
        self.dispatcher.set_status(
            intent, state="Succeeded", updated_at=self._now()
        )

        # Prove the Store independently refuses a Recipe receipt when a caller
        # claims terminal evidence without durably projecting it first.
        fake_projection = mock.Mock(
            evidence={"heartbeat": {"state": "succeeded"}}
        )
        event_count = self.store.latest_run_event_sequence(task_id)
        with mock.patch.object(
            self.service,
            "project_worker_attempt",
            return_value=fake_projection,
        ):
            with self.assertRaisesRegex(
                TaskConflict, "concurrent Adapter status updates did not converge"
            ):
                self.service.refresh_runtime_status(
                    task_id,
                    supervisor_lease=lease,
                    **self.scope,
                )
        self.assertEqual(
            self.store.latest_run_event_sequence(task_id), event_count
        )
        self.assertEqual(self._node_states(task_id)["data_check"][1], "Running")
        self.assertEqual(self._table_count("dag_node_terminal_facts", task_id), 0)

        # The production service path closes the cadence race by projecting
        # the terminal Worker document synchronously, then publishes one exact
        # Succeeded receipt that remains readable through the product model.
        succeeded = self.service.refresh_runtime_status(
            task_id,
            supervisor_lease=lease,
            **self.scope,
        )
        self.assertEqual(succeeded.snapshot.status, "Running")
        self.assertEqual(
            self._node_states(task_id)["data_check"][1], "Succeeded"
        )
        facts = self.service.get_dag_runtime_node_facts(task_id, **self.scope)
        self.assertIsNotNone(facts)
        data_check = next(
            node for node in facts["nodes"] if node["node_id"] == "data_check"
        )
        self.assertEqual(data_check["state"], "Succeeded")
        connection = sqlite3.connect(self.database_path)
        try:
            terminal = connection.execute(
                "SELECT terminal.worker_observation_sequence, "
                "observation.heartbeat_state, "
                "terminal.worker_observation_hash = observation.document_hash "
                "FROM dag_node_terminal_facts AS terminal "
                "JOIN worker_attempt_observations AS observation "
                "ON observation.attempt_id = terminal.attempt_id "
                "AND observation.observation_sequence = "
                "terminal.worker_observation_sequence "
                "WHERE terminal.task_id = ? AND terminal.node_id = 'data_check'",
                (task_id,),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(terminal, (2, "succeeded", 1))

    def test_all_cache_hit_recipe_lists_reads_and_purges_no_local_run(self) -> None:
        self.dispatcher = ReproducibleMultiNodeDagDispatcher(self.store)
        self.service = self._new_service(self.store)
        source_task, _ = self._approved_dag(
            "fixed-recipe-cache-source",
            fixed_recipe=True,
            include_result_check=True,
        )
        source_lease = self._acquire("fixed-recipe-cache-source")
        for node_id in (
            "data_check",
            "forward",
            "quality_check",
            "fwi",
            "result_check",
        ):
            self.assertEqual(
                self._advance(source_task, source_lease).admitted_node_id,
                node_id,
            )
            self._run_success(source_task, source_lease, node_id)
        self.service.release_runtime_supervisor_lease(source_lease)
        source_artifacts = self.service.collect_artifacts(
            source_task, **self.scope
        )
        self.assertEqual(len(source_artifacts), 8)

        target_task, _ = self._approved_dag(
            "fixed-recipe-cache-target",
            fixed_recipe=True,
            include_result_check=True,
        )
        target_lease = self._acquire("fixed-recipe-cache-target")
        for node_id in (
            "data_check",
            "forward",
            "quality_check",
            "fwi",
            "result_check",
        ):
            hit = self._advance(target_task, target_lease)
            self.assertEqual(hit.cache_hit_node_id, node_id)
            self.assertIsNone(hit.admitted_node_id)
        self.assertEqual(
            self.service.get_task(target_task, **self.scope).status,
            "Succeeded",
        )
        self.assertEqual(self.store.list_dispatch_intents(target_task), ())
        target_artifacts = self.service.collect_artifacts(
            target_task, **self.scope
        )
        self.assertEqual(len(target_artifacts), 8)
        for manifest in target_artifacts:
            returned, data = self.service.read_artifact(
                target_task, manifest["artifact_id"], **self.scope
            )
            self.assertEqual(returned, manifest)
            self.assertEqual(
                "sha256:" + hashlib.sha256(data).hexdigest(),
                manifest["content_hash"],
            )

        # Exercise the v23 database authority directly on isolated copies of
        # the completed cache-only Task.  A forged terminal status is not
        # enough: every node must retain exactly one cache hit bound to the
        # current Plan and Approval.
        for corruption in ("missing_node", "wrong_approval"):
            with self.subTest(cache_only_trash_corruption=corruption):
                corrupted_path = (
                    Path(self.temporary.name)
                    / f"cache-only-trash-{corruption}.sqlite3"
                )
                source_connection = sqlite3.connect(self.database_path)
                corrupted_connection = sqlite3.connect(corrupted_path)
                try:
                    source_connection.backup(corrupted_connection)
                    corrupted_connection.execute("PRAGMA foreign_keys = OFF")
                    if corruption == "missing_node":
                        corrupted_connection.execute(
                            "DROP TRIGGER "
                            "dag_node_cache_hit_facts_cannot_be_deleted"
                        )
                        corrupted_connection.execute(
                            "DELETE FROM dag_node_cache_hit_facts "
                            "WHERE target_task_id = ? "
                            "AND target_node_id = 'result_check'",
                            (target_task,),
                        )
                    else:
                        corrupted_connection.execute(
                            "DROP TRIGGER dag_node_cache_hit_facts_are_append_only"
                        )
                        corrupted_connection.execute(
                            "UPDATE dag_node_cache_hit_facts "
                            "SET target_approval_id = 'approval-forged' "
                            "WHERE target_task_id = ? "
                            "AND target_node_id = 'result_check'",
                            (target_task,),
                        )
                    corrupted_connection.commit()
                    with self.assertRaisesRegex(
                        sqlite3.IntegrityError,
                        "only a resolved terminal task can be moved to trash",
                    ):
                        corrupted_connection.execute(
                            """
                            INSERT INTO task_visibility_events(
                                task_id, project_id, principal_id, revision,
                                event_id, action, previous_state, state,
                                trashed_at, document_json, document_hash,
                                occurred_at, recorded_at
                            ) VALUES (?, ?, ?, 1, ?, 'trashed', 'active',
                                      'trashed', ?, '{}', ?, ?, ?)
                            """,
                            (
                                target_task,
                                PROJECT_ID,
                                PRINCIPAL_ID,
                                f"visibility-corrupt-{corruption}",
                                self._now(),
                                "sha256:" + "0" * 64,
                                self._now(),
                                self._now(),
                            ),
                        )
                    corrupted_connection.rollback()
                finally:
                    corrupted_connection.close()
                    source_connection.close()

        trashed = self.service.trash_task(
            task_id=target_task,
            expected_visibility_revision=0,
            idempotency_key="trash-fixed-recipe-cache-target",
            **self.scope,
        )
        purged = self.service.purge_task(
            task_id=target_task,
            expected_visibility_revision=trashed.snapshot.visibility_revision,
            idempotency_key="purge-fixed-recipe-cache-target",
            **self.scope,
        )
        self.assertEqual(purged.local_run_state, "not_created")
        self.assertEqual(self.dispatcher.purge_calls, 0)
        source_manifest = source_artifacts[0]
        returned, data = self.service.read_artifact(
            source_task, source_manifest["artifact_id"], **self.scope
        )
        self.assertEqual(returned, source_manifest)
        self.assertEqual(
            "sha256:" + hashlib.sha256(data).hexdigest(),
            source_manifest["content_hash"],
        )

    def test_near_match_recipe_version_is_rejected_before_execution(self) -> None:
        task_id, _ = self._approved_dag(
            "near-match-recipe",
            fixed_recipe=True,
            fixed_recipe_version="1.0.1",
        )
        lease = self._acquire("near-match-recipe")
        with self.assertRaisesRegex(
            TaskValidationError, "FIXED_RECIPE_INVALID"
        ):
            self._advance(task_id, lease)
        self.assertEqual(self._dispatch_count(task_id, "data_check"), 0)

    def test_recipe_extension_on_non_recipe_topology_is_not_parallel(self) -> None:
        plan = current_optimizer_plan_graph()
        template = copy.deepcopy(plan["nodes"][0])
        plan["schema_version"] = "1.2.0"
        plan["extensions"] = {
            "org.agent_rpc.recipe": {
                "id": "forward_qc_fwi",
                "version": "1.0.0",
            }
        }
        plan["nodes"] = []
        for node_id, dependencies in {
            "a": [],
            "b": ["a"],
            "c": ["a"],
            "d": ["b", "c"],
            "e": ["d"],
        }.items():
            node = copy.deepcopy(template)
            node["node_id"] = node_id
            node["dependencies"] = dependencies
            plan["nodes"].append(node)
        self.assertFalse(is_fixed_recipe_parallel_plan(plan))

    def test_v23_database_rejects_second_active_for_spoofed_recipe_document(
        self,
    ) -> None:
        task_id, _ = self._approved_dag(
            "v23-direct-recipe-guard",
            fixed_recipe=True,
            include_result_check=True,
        )
        lease = self._acquire("v23-direct-recipe-guard")
        self.assertEqual(
            self._advance(task_id, lease).admitted_node_id,
            "data_check",
        )
        self._run_success(task_id, lease, "data_check")
        self.assertEqual(self._advance(task_id, lease).admitted_node_id, "forward")
        self._start_active(task_id, lease, "forward")
        self.assertEqual(
            self._advance(task_id, lease).admitted_node_id,
            "quality_check",
        )

        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            admission_row = connection.execute(
                "SELECT * FROM dag_node_execution_admissions "
                "WHERE task_id = ? AND node_id = 'quality_check'",
                (task_id,),
            ).fetchone()
            self.assertIsNotNone(admission_row)
            admission = dict(admission_row)
            original_plan_json = connection.execute(
                "SELECT document_json FROM plans WHERE task_id = ?",
                (task_id,),
            ).fetchone()[0]
        finally:
            connection.close()

        # Prepare one exact candidate row while deliberately bypassing only
        # the append-only guards needed to simulate a compromised caller. The
        # v23 admission trigger under test remains installed throughout.
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute("PRAGMA foreign_keys = OFF")
            for trigger in (
                "dag_node_execution_transition_facts_cannot_be_deleted",
                "dag_node_execution_admissions_cannot_be_deleted",
                "dag_node_state_events_cannot_be_deleted",
                "plans_are_append_only",
            ):
                connection.execute(f"DROP TRIGGER {trigger}")
            connection.execute(
                "DELETE FROM dag_node_execution_transition_facts "
                "WHERE intent_id = ?",
                (admission["intent_id"],),
            )
            connection.execute(
                "DELETE FROM dag_node_execution_admissions WHERE intent_id = ?",
                (admission["intent_id"],),
            )
            connection.execute(
                "DELETE FROM dag_node_state_events "
                "WHERE task_id = ? AND node_id = 'quality_check' "
                "AND revision = ?",
                (task_id, admission["queued_revision"]),
            )
            connection.commit()
        finally:
            connection.close()

        original_plan = json.loads(original_plan_json)
        spoofed_topology = copy.deepcopy(original_plan)
        spoofed_topology["nodes"][4]["dependencies"] = ["forward"]
        spoofed_input = copy.deepcopy(original_plan)
        spoofed_input["nodes"][3]["inputs"][1]["port"] = "fake_evidence"
        admission_columns = list(admission)
        insert_sql = (
            "INSERT INTO dag_node_execution_admissions("
            + ",".join(admission_columns)
            + ") VALUES ("
            + ",".join("?" for _ in admission_columns)
            + ")"
        )
        admission_values = tuple(admission[column] for column in admission_columns)

        for label, spoofed in (
            ("topology", spoofed_topology),
            ("input", spoofed_input),
        ):
            with self.subTest(label=label):
                connection = sqlite3.connect(self.database_path)
                try:
                    connection.execute(
                        "UPDATE plans SET document_json = ? WHERE task_id = ?",
                        (
                            json.dumps(
                                spoofed,
                                ensure_ascii=False,
                                allow_nan=False,
                                separators=(",", ":"),
                                sort_keys=True,
                            ),
                            task_id,
                        ),
                    )
                    connection.commit()
                    with self.assertRaisesRegex(
                        sqlite3.IntegrityError,
                        "DAG node admission requires the exact current ready case",
                    ):
                        connection.execute(insert_sql, admission_values)
                    connection.rollback()
                    self.assertEqual(
                        connection.execute(
                            "SELECT COUNT(*) FROM dag_node_execution_admissions "
                            "WHERE task_id = ? AND node_id = 'quality_check'",
                            (task_id,),
                        ).fetchone()[0],
                        0,
                    )
                finally:
                    connection.close()

    def test_recipe_durable_request_hashes_must_match_admission_binding(self) -> None:
        task_id, _ = self._approved_dag(
            "recipe-request-binding-tamper",
            fixed_recipe=True,
            include_result_check=True,
        )
        lease = self._acquire("recipe-request-binding-tamper")
        self.assertEqual(
            self._advance(task_id, lease).admitted_node_id,
            "data_check",
        )
        self._run_success(task_id, lease, "data_check")
        forward = self._advance(task_id, lease)
        self.assertEqual(forward.admitted_node_id, "forward")
        intent_id = forward.active_intent.intent_id

        connection = sqlite3.connect(self.database_path)
        try:
            document = json.loads(
                connection.execute(
                    "SELECT request_json FROM dispatch_intents "
                    "WHERE intent_id = ?",
                    (intent_id,),
                ).fetchone()[0]
            )
            tampered_hash = "sha256:" + "3" * 64
            document["request"]["recipe_input_hashes"][0] = tampered_hash
            document["queue_fingerprint"]["input_hashes"][1] = tampered_hash
            document_json, document_hash = encode_document(document)
            _, fingerprint_hash = encode_document(document["queue_fingerprint"])
            connection.execute("DROP TRIGGER dispatch_intents_are_immutable")
            connection.execute(
                "UPDATE dispatch_intents "
                "SET request_json = ?, request_hash = ?, fingerprint_hash = ? "
                "WHERE intent_id = ?",
                (
                    document_json,
                    document_hash,
                    fingerprint_hash,
                    intent_id,
                ),
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaisesRegex(
            TaskStoreCorruption,
            "Recipe request differs from its exact plan",
        ):
            self.store.get_dispatch_intent_by_id(intent_id)

    def test_failed_branch_blocks_only_descendants_and_survives_restart(self) -> None:
        task_id, _ = self._approved_dag("local-failure")
        lease = self._acquire("local-failure-first")
        self.assertEqual(self._advance(task_id, lease).admitted_node_id, "a")
        self._run_success(task_id, lease, "a")
        self.assertEqual(self._advance(task_id, lease).admitted_node_id, "b")
        self._start_active(task_id, lease, "b")
        self._finish_active(task_id, lease, "b", state="Failed")

        reconciled = self._advance(task_id, lease)
        self.assertEqual(reconciled.admitted_node_id, "c")
        states = self._node_states(task_id)
        self.assertEqual(states["b"][1], "Failed")
        self.assertEqual(states["c"][1], "Queued")
        self.assertEqual(states["d"][1], "Blocked")
        blocked_revision = states["d"][0]

        lease = self._crash_restart("local-failure-successor")
        replay = self._advance(task_id, lease)
        self.assertIsNotNone(replay.active_intent)
        self.assertEqual(replay.active_intent.node_id, "c")
        self.assertEqual(
            self._node_states(task_id)["d"],
            (blocked_revision, "Blocked"),
        )
        self._run_success(task_id, lease, "c")

        terminal = self.service.get_task(task_id, **self.scope)
        self.assertEqual(terminal.status, "Failed")
        self.assertEqual(
            [self._dispatch_count(task_id, node) for node in "abcd"],
            [1, 1, 1, 0],
        )
        self.assertEqual(
            self._node_states(task_id)["d"],
            (blocked_revision, "Blocked"),
        )

    def test_node_local_cancel_blocks_descendant_while_independent_branch_runs(
        self,
    ) -> None:
        task_id, _ = self._approved_dag("node-local-cancel")
        lease = self._acquire("node-local-cancel-first")
        self.assertEqual(self._advance(task_id, lease).admitted_node_id, "a")
        self._run_success(task_id, lease, "a")
        self.assertEqual(self._advance(task_id, lease).admitted_node_id, "b")
        self._start_active(task_id, lease, "b")

        b_intent = self._active_intent(task_id)
        self.assertEqual(b_intent.node_id, "b")
        self.dispatcher.set_status(
            b_intent,
            state="Cancelled",
            updated_at=self._now(),
        )
        cancelled = self.service.refresh_runtime_status(
            task_id,
            supervisor_lease=lease,
            **self.scope,
        )
        self.assertEqual(cancelled.snapshot.status, "Running")
        states = self._node_states(task_id)
        self.assertEqual(states["b"][1], "Cancelled")
        self.assertEqual(states["c"][1], "Pending")
        self.assertEqual(states["d"][1], "Blocked")
        cancelled_revision = states["b"][0]
        blocked_revision = states["d"][0]

        events = self.service.list_run_events(task_id, **self.scope)
        cancelled_events = [
            event for event in events if event["event_type"] == "node_cancelled"
        ]
        self.assertEqual(len(cancelled_events), 1)
        self.assertEqual(cancelled_events[0]["node_id"], "b")
        self.assertEqual(cancelled_events[0]["task_status"], "Running")
        connection = sqlite3.connect(self.database_path)
        try:
            terminal = connection.execute(
                "SELECT node_state, adapter_status_json, receipt_document_json "
                "FROM dag_node_terminal_facts WHERE task_id = ? AND node_id = 'b'",
                (task_id,),
            ).fetchone()
            self.assertIsNotNone(terminal)
            self.assertEqual(terminal[0], "Cancelled")
            self.assertIn('"status":"Cancelled"', terminal[1])
            self.assertIsNone(terminal[2])
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM task_cancel_requests WHERE task_id = ?",
                    (task_id,),
                ).fetchone()[0],
                0,
            )
        finally:
            connection.close()

        terminal_fact_count = self._table_count(
            "dag_node_terminal_facts", task_id
        )
        status_replay = self.service.refresh_runtime_status(
            task_id,
            supervisor_lease=lease,
            **self.scope,
        )
        self.assertEqual(status_replay.snapshot.status, "Running")
        self.assertEqual(
            self._table_count("dag_node_terminal_facts", task_id),
            terminal_fact_count,
        )
        self.assertEqual(self._node_states(task_id), states)
        lease = self._crash_restart("node-local-cancel-successor")
        resumed = self._advance(task_id, lease)
        self.assertEqual(resumed.admitted_node_id, "c")
        self.assertEqual(
            self._node_states(task_id)["b"],
            (cancelled_revision, "Cancelled"),
        )
        self.assertEqual(
            self._node_states(task_id)["d"],
            (blocked_revision, "Blocked"),
        )
        self.assertEqual(
            self._table_count("dag_node_terminal_facts", task_id),
            terminal_fact_count,
        )

        self._run_success(task_id, lease, "c")
        terminal_snapshot = self.service.get_task(task_id, **self.scope)
        self.assertEqual(terminal_snapshot.status, "Failed")
        self.assertEqual(
            [self._dispatch_count(task_id, node) for node in "abcd"],
            [1, 1, 1, 0],
        )
        final_states = self._node_states(task_id)
        final_fact_count = self._table_count("dag_node_terminal_facts", task_id)

        lease = self._crash_restart("node-local-cancel-terminal")
        replay = self._advance(task_id, lease)
        self.assertEqual(replay.snapshot.status, "Failed")
        self.assertIsNone(replay.active_intent)
        self.assertIsNone(replay.admitted_node_id)
        self.assertEqual(self._node_states(task_id), final_states)
        self.assertEqual(
            self._table_count("dag_node_terminal_facts", task_id),
            final_fact_count,
        )
        self.assertEqual(
            [self._dispatch_count(task_id, node) for node in "abcd"],
            [1, 1, 1, 0],
        )

    def test_cancel_request_is_a_durable_successor_admission_barrier(self) -> None:
        task_id, _ = self._approved_dag("cancel-barrier")
        lease = self._acquire("cancel-barrier")
        self.assertEqual(self._advance(task_id, lease).admitted_node_id, "a")
        self._run_success(task_id, lease, "a")
        self.assertEqual(self._advance(task_id, lease).admitted_node_id, "b")
        self._start_active(task_id, lease, "b")

        requested = self.service.cancel_task(
            task_id=task_id,
            reason="user_requested",
            idempotency_key="cancel-dag-runtime-active-b",
            **self.scope,
        )
        self.assertEqual(requested.snapshot.cancellation.state, "requested")
        lease = self._crash_restart("cancel-barrier-successor")
        barrier = self._advance(task_id, lease)
        self.assertIsNone(barrier.admitted_node_id)
        self.assertEqual(barrier.deferred_code, "CANCEL_CONTROL_PENDING")
        self.assertEqual(
            [self._dispatch_count(task_id, node) for node in "abcd"],
            [1, 1, 0, 0],
        )

        cancelled = self.service.process_runtime_cancellation(
            task_id,
            supervisor_lease=lease,
            **self.scope,
        )
        self.assertEqual(cancelled.state, "cancelled")
        self.assertEqual(cancelled.snapshot.status, "Cancelled")
        replay = self._advance(task_id, lease)
        self.assertIsNone(replay.active_intent)
        self.assertIsNone(replay.admitted_node_id)
        self.assertEqual(
            [self._dispatch_count(task_id, node) for node in "abcd"],
            [1, 1, 0, 0],
        )

    def test_cancel_terminal_won_projects_node_then_cancels_unstarted_branches(
        self,
    ) -> None:
        for terminal_status in ("Succeeded", "Failed"):
            with self.subTest(terminal_status=terminal_status):
                suffix = f"cancel-terminal-{terminal_status.lower()}"
                task_id, _ = self._approved_dag(suffix)
                lease = self._acquire(f"{suffix}-first")
                self.assertEqual(
                    self._advance(task_id, lease).admitted_node_id, "a"
                )
                self._run_success(task_id, lease, "a")
                self.assertEqual(
                    self._advance(task_id, lease).admitted_node_id, "b"
                )
                self._start_active(task_id, lease, "b")
                requested = self.service.cancel_task(
                    task_id=task_id,
                    reason="user_requested",
                    idempotency_key=f"cancel-dag-{terminal_status.lower()}-race",
                    **self.scope,
                )
                self.assertEqual(
                    requested.snapshot.cancellation.state, "requested"
                )

                self.dispatcher.cancel_result_state = "terminal_won"
                self.dispatcher.cancel_terminal_status = terminal_status
                active = self._active_intent(task_id)
                if terminal_status == "Succeeded":
                    manifests, artifact_data = (
                        task_service_fixtures.ScientificRuntimeTaskServiceTest.artifact_manifests(
                            self, task_id
                        )
                    )
                    self.dispatcher.manifests = manifests
                    self.dispatcher.artifact_data = artifact_data
                self.dispatcher.set_status(
                    active,
                    state=terminal_status,
                    updated_at=self._now(),
                )
                active_intents = self._table_count("dispatch_intents", task_id)
                admissions = self._table_count(
                    "dag_node_execution_admissions", task_id
                )
                status_calls = self.dispatcher.status_calls
                resolved = self.service.process_runtime_cancellation(
                    task_id,
                    supervisor_lease=lease,
                    **self.scope,
                )

                self.assertEqual(resolved.state, "cancelled")
                self.assertEqual(
                    resolved.adapter_result["terminal_status"], terminal_status
                )
                self.assertEqual(resolved.snapshot.status, "Cancelled")
                self.assertEqual(
                    resolved.snapshot.cancellation.state, "cancelled"
                )
                self.assertEqual(
                    resolved.snapshot.cancellation.result, "cancel_confirmed"
                )
                self.assertEqual(self.dispatcher.status_calls, status_calls + 1)
                self.assertEqual(
                    self._table_count("dispatch_intents", task_id),
                    active_intents,
                )
                self.assertEqual(
                    self._table_count("dag_node_terminal_facts", task_id),
                    2,
                )
                self.assertEqual(
                    self._table_count("dag_node_execution_admissions", task_id),
                    admissions,
                )
                self.assertEqual(
                    self._table_count("task_cancel_outcomes", task_id), 1
                )
                self.assertEqual(
                    self._node_states(task_id)["b"][1], terminal_status
                )
                self.assertEqual(
                    [self._dispatch_count(task_id, node) for node in "abcd"],
                    [1, 1, 0, 0],
                )

                self.service.release_runtime_supervisor_lease(lease)

    def test_cancel_terminal_won_crash_after_final_node_replays_under_new_term(
        self,
    ) -> None:
        task_id, _ = self._approved_dag("cancel-terminal-crash")
        lease = self._acquire("cancel-terminal-crash-first")
        self.assertEqual(self._advance(task_id, lease).admitted_node_id, "a")
        self._run_success(task_id, lease, "a")
        self.assertEqual(self._advance(task_id, lease).admitted_node_id, "b")
        self._run_success(task_id, lease, "b")
        self.assertEqual(self._advance(task_id, lease).admitted_node_id, "c")
        self._run_success(task_id, lease, "c")
        self.assertEqual(self._advance(task_id, lease).admitted_node_id, "d")
        self._start_active(task_id, lease, "d")
        requested = self.service.cancel_task(
            task_id=task_id,
            reason="user_requested",
            idempotency_key="cancel-terminal-crash-window",
            **self.scope,
        )
        self.assertEqual(requested.snapshot.cancellation.state, "requested")
        self.dispatcher.cancel_result_state = "terminal_won"
        self.dispatcher.cancel_terminal_status = "Succeeded"
        active = self._active_intent(task_id)
        manifests, artifact_data = (
            task_service_fixtures.ScientificRuntimeTaskServiceTest.artifact_manifests(
                self, task_id
            )
        )
        self.dispatcher.manifests = manifests
        self.dispatcher.artifact_data = artifact_data
        self.dispatcher.set_status(
            active, state="Succeeded", updated_at=self._now()
        )

        def crash_before_outcome(**_kwargs):
            raise TaskStoreConflict("injected terminal-fact/outcome crash")

        self.store.complete_supervised_cancel = crash_before_outcome
        with self.assertRaises(TaskConflict, msg="injected terminal-fact/outcome crash"):
            self.service.process_runtime_cancellation(
                task_id,
                supervisor_lease=lease,
                **self.scope,
            )
        crashed = self.store.get_task(task_id)
        self.assertEqual(crashed.status, "Succeeded")
        self.assertEqual(crashed.cancellation.state, "requested")
        event_sequence = self.store.latest_run_event_sequence(task_id)
        terminal_facts = self._table_count("dag_node_terminal_facts", task_id)
        dispatch_counts = [
            self._dispatch_count(task_id, node) for node in "abcd"
        ]

        lease = self._crash_restart("cancel-terminal-crash-replay")
        completed = self.service.process_runtime_cancellation(
            task_id,
            supervisor_lease=lease,
            **self.scope,
        )
        self.assertEqual(completed.state, "superseded")
        self.assertEqual(completed.snapshot.status, "Succeeded")
        self.assertEqual(
            completed.snapshot.cancellation.result, "terminal_preempted"
        )
        self.assertEqual(
            self.store.latest_run_event_sequence(task_id), event_sequence
        )
        self.assertEqual(
            self._table_count("dag_node_terminal_facts", task_id), terminal_facts
        )
        self.assertEqual(
            [self._dispatch_count(task_id, node) for node in "abcd"],
            dispatch_counts,
        )
        self.assertEqual(self._table_count("task_cancel_outcomes", task_id), 1)

    def test_cancel_terminal_won_uses_dag_aggregate_not_node_status(self) -> None:
        task_id, _ = self._approved_dag("cancel-terminal-aggregate")
        lease = self._acquire("cancel-terminal-aggregate")
        self.assertEqual(self._advance(task_id, lease).admitted_node_id, "a")
        self._run_success(task_id, lease, "a")
        self.assertEqual(self._advance(task_id, lease).admitted_node_id, "b")
        self._start_active(task_id, lease, "b")
        self._finish_active(task_id, lease, "b", state="Failed")
        self.assertEqual(self._advance(task_id, lease).admitted_node_id, "c")
        self._start_active(task_id, lease, "c")
        requested = self.service.cancel_task(
            task_id=task_id,
            reason="user_requested",
            idempotency_key="cancel-terminal-aggregate-mismatch",
            **self.scope,
        )
        self.assertEqual(requested.snapshot.cancellation.state, "requested")
        self.dispatcher.cancel_result_state = "terminal_won"
        self.dispatcher.cancel_terminal_status = "Succeeded"
        active = self._active_intent(task_id)
        manifests, artifact_data = (
            task_service_fixtures.ScientificRuntimeTaskServiceTest.artifact_manifests(
                self, task_id
            )
        )
        self.dispatcher.manifests = manifests
        self.dispatcher.artifact_data = artifact_data
        self.dispatcher.set_status(
            active, state="Succeeded", updated_at=self._now()
        )

        completed = self.service.process_runtime_cancellation(
            task_id,
            supervisor_lease=lease,
            **self.scope,
        )
        self.assertEqual(completed.state, "superseded")
        self.assertEqual(completed.snapshot.status, "Failed")
        self.assertEqual(completed.snapshot.cancellation.terminal_status, "Failed")
        self.assertEqual(
            completed.snapshot.cancellation.adapter_proof["terminal_status"],
            "Succeeded",
        )
        self.assertEqual(self._node_states(task_id)["c"][1], "Succeeded")
        self.assertEqual(
            [self._dispatch_count(task_id, node) for node in "abcd"],
            [1, 1, 1, 0],
        )
        self.assertEqual(self._table_count("task_cancel_outcomes", task_id), 1)

    def test_restart_after_a_and_during_b_c_never_repeats_dispatch(self) -> None:
        task_id, _ = self._approved_dag("restart-successors")
        lease = self._acquire("restart-after-a-first")
        self.assertEqual(self._advance(task_id, lease).admitted_node_id, "a")
        self._run_success(task_id, lease, "a")
        before_a_restart = self._dispatch_count(task_id, "a")

        lease = self._crash_restart("restart-after-a-second")
        self.assertEqual(self._advance(task_id, lease).admitted_node_id, "b")
        self._start_active(task_id, lease, "b")
        before_b_restart = self._dispatch_count(task_id, "b")

        lease = self._crash_restart("restart-during-b")
        active_b = self._advance(task_id, lease)
        self.assertEqual(active_b.active_intent.node_id, "b")
        self.service.schedule_runtime_dispatch(
            task_id, supervisor_lease=lease, **self.scope
        )
        after_b_restart = self._dispatch_count(task_id, "b")
        self._finish_active(task_id, lease, "b", state="Succeeded")
        self.assertEqual(self._advance(task_id, lease).admitted_node_id, "c")
        self._start_active(task_id, lease, "c")
        before_c_restart = self._dispatch_count(task_id, "c")

        lease = self._crash_restart("restart-during-c")
        active_c = self._advance(task_id, lease)
        self.assertEqual(active_c.active_intent.node_id, "c")
        self.service.schedule_runtime_dispatch(
            task_id, supervisor_lease=lease, **self.scope
        )
        after_c_restart = self._dispatch_count(task_id, "c")

        self.assertEqual(
            (before_a_restart, self._dispatch_count(task_id, "a")),
            (1, 1),
        )
        self.assertEqual((before_b_restart, after_b_restart), (1, 1))
        self.assertEqual((before_c_restart, after_c_restart), (1, 1))
        self.assertEqual(self._node_states(task_id)["a"][1], "Succeeded")

    def test_restart_before_and_after_receipt_projection_adopts_once(self) -> None:
        before_task, _ = self._approved_dag("receipt-before")
        lease = self._acquire("receipt-before-first")
        self.assertEqual(self._advance(before_task, lease).admitted_node_id, "a")
        self._run_success(before_task, lease, "a")
        self.assertEqual(self._advance(before_task, lease).admitted_node_id, "b")
        original_record = self.store.record_supervised_worker_observation

        def lose_projection(**_kwargs):
            raise TaskStoreConflict("simulated dispatch receipt projection loss")

        self.store.record_supervised_worker_observation = lose_projection
        try:
            with self.assertRaises(TaskConflict):
                self.service.schedule_runtime_dispatch(
                    before_task,
                    supervisor_lease=lease,
                    **self.scope,
                )
        finally:
            self.store.record_supervised_worker_observation = original_record
        before_counts = (self._dispatch_count(before_task, "b"),)

        lease = self._crash_restart("receipt-before-successor")
        self.assertEqual(
            self._advance(before_task, lease).active_intent.node_id, "b"
        )
        recovered = self.service.schedule_runtime_dispatch(
            before_task, supervisor_lease=lease, **self.scope
        )
        self.assertTrue(recovered.adopted)
        before_counts += (self._dispatch_count(before_task, "b"),)

        self.service.release_runtime_supervisor_lease(lease)
        after_task, _ = self._approved_dag("receipt-after")
        lease = self._acquire("receipt-after-first")
        self.assertEqual(self._advance(after_task, lease).admitted_node_id, "a")
        self._run_success(after_task, lease, "a")
        self.assertEqual(self._advance(after_task, lease).admitted_node_id, "b")
        projected = self.service.schedule_runtime_dispatch(
            after_task, supervisor_lease=lease, **self.scope
        )
        self.assertEqual(projected.intent.state, "dispatched")
        after_counts = (self._dispatch_count(after_task, "b"),)

        lease = self._crash_restart("receipt-after-successor")
        self.assertEqual(
            self._advance(after_task, lease).active_intent.node_id, "b"
        )
        replayed = self.service.schedule_runtime_dispatch(
            after_task, supervisor_lease=lease, **self.scope
        )
        self.assertEqual(replayed.intent.state, "dispatched")
        after_counts += (self._dispatch_count(after_task, "b"),)

        self.assertEqual(before_counts, (1, 1))
        self.assertEqual(after_counts, (1, 1))

    def test_stale_term_and_same_owner_aba_cannot_advance_or_dispatch(self) -> None:
        task_id, _ = self._approved_dag("stale-aba")
        first = self._acquire("control-a")
        self.assertEqual(self._advance(task_id, first).admitted_node_id, "a")
        initial_states = self._node_states(task_id)
        initial_intents = self._table_count("dispatch_intents", task_id)

        self.service.release_runtime_supervisor_lease(first)
        second = self._acquire("control-b")
        with self.assertRaises(TaskSupervisorLeaseLost):
            self._advance(task_id, first)
        self.assertEqual(self._node_states(task_id), initial_states)
        self.assertEqual(
            self._table_count("dispatch_intents", task_id), initial_intents
        )

        self.service.release_runtime_supervisor_lease(second)
        third = self._acquire("control-a")
        self.assertGreater(third.fencing_token, second.fencing_token)
        with self.assertRaises(TaskSupervisorLeaseLost):
            self._advance(task_id, first)
        self.assertEqual(self._dispatch_count(task_id, "a"), 0)

        current = self._advance(task_id, third)
        self.assertEqual(current.active_intent.node_id, "a")
        self._start_active(task_id, third, "a")
        self.assertEqual(self._dispatch_count(task_id, "a"), 1)


if __name__ == "__main__":
    unittest.main()
