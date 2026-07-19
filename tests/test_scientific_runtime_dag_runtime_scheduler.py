from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import sqlite3
import tempfile
import unittest
from collections import Counter
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scientific_runtime import (
    DispatchError,
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
from scientific_runtime_contracts import compute_plan_hash
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
            "request_hash": MANAGED_REQUEST_HASH,
            "algorithm": copy.deepcopy(intent.request["algorithm"]),
            "fingerprint": copy.deepcopy(intent.queue_fingerprint),
            "adapter_version": intent.adapter_version,
        }
        observation = {
            "evidence": managed_worker_evidence(
                attempt_id=attempt_id,
                job_id=job_id,
            ),
            "handle": copy.deepcopy(handle),
        }
        self._observations[intent.intent_id] = copy.deepcopy(observation)
        self.worker_observation = copy.deepcopy(observation)
        return handle

    def observe_existing_worker_attempt(self, intent):
        self._activate(intent)
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
        self._statuses[intent.intent_id] = {
            "status": state,
            "stage": state.lower(),
            "completed": 2 if state == "Succeeded" else 0,
            "total": 2,
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
        with super().verified_node_outputs(intent) as outputs:
            yield outputs


class ReproducibleMultiNodeDagDispatcher(MultiNodeControlledDagDispatcher):
    """Controlled Worker whose adopted provenance is safe to cache."""

    def prepare_node(self, snapshot, *, node_id, input_binding):
        prepared = super().prepare_node(
            snapshot, node_id=node_id, input_binding=input_binding
        )
        fingerprint = executable_fingerprint(self.adapter_version)
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

    def _approved_dag(self, suffix: str) -> tuple[str, dict]:
        draft = current_optimizer_task_draft()
        draft["draft_id"] = f"draft-dag-runtime-{suffix}"
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
        dependencies = {
            "a": [],
            "b": ["a"],
            "c": ["a"],
            "d": ["b", "c"],
        }
        nodes = []
        for node_id, required in dependencies.items():
            node = copy.deepcopy(template)
            node["node_id"] = node_id
            node["dependencies"] = required
            node["idempotency_key"] = f"{task_id}:{node_id}:0001"
            nodes.append(node)
        plan["nodes"] = nodes
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
