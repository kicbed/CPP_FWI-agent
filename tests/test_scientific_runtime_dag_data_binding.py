from __future__ import annotations

import copy
import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Callable

import scientific_runtime.task_service as task_service_module

from scientific_runtime import (
    DagDataBindingError,
    DeepwaveTaskDispatcher,
    DispatchError,
    RegistryService,
    SQLiteTaskStore,
    TaskService,
    TaskValidationError,
    bind_dag_artifact_input,
    load_deepwave_manifest,
)
from scientific_runtime.dag_scheduler import (
    PENDING,
    DagScheduleError,
    evaluate_dag_readiness,
)
from scientific_runtime_contracts import (
    PlanDataEdge,
    canonical_json_bytes,
    compute_plan_hash,
    extract_plan_data_edges,
    schema_errors,
)
from tests.test_scientific_runtime_contracts import (
    artifact_manifest,
    approval_decision,
    dataset_ref,
    optimizer_task_draft,
    optimizer_plan_graph,
    plan_graph,
    run_event,
)


ARTIFACT_DATA = b"verified-upstream-velocity-model"


def typed_plan() -> dict:
    plan = optimizer_plan_graph()
    plan["schema_version"] = "1.2.0"
    source = copy.deepcopy(plan["nodes"][0])
    source["node_id"] = "prepare"
    source["algorithm"] = {
        "id": "test.velocity_passthrough",
        "version": "1.5.0",
    }
    source["outputs"] = [
        {"port": "prepared_model", "data_type": "velocity_model_2d"}
    ]
    source["idempotency_key"] = "task-001:prepare:0001"
    source["dependencies"] = []
    target = copy.deepcopy(source)
    target["node_id"] = "invert"
    target["idempotency_key"] = "task-001:invert:0001"
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
    return plan


def upstream_artifact(
    plan: dict,
    *,
    output_port: str = "prepared_model",
    data_type: str = "velocity_model_2d",
) -> dict:
    value = artifact_manifest(plan)
    content_hash = "sha256:" + hashlib.sha256(ARTIFACT_DATA).hexdigest()
    source_algorithm = copy.deepcopy(plan["nodes"][0]["algorithm"])
    value.update(
        task_id="task-001",
        node_id="prepare",
        artifact_type=data_type,
        content_hash=content_hash,
        size_bytes=len(ARTIFACT_DATA),
    )
    value["lineage"]["plan_hash"] = plan["plan_hash"]
    value["lineage"]["algorithm"] = source_algorithm
    value["fingerprint"]["algorithm"] = source_algorithm
    value["extensions"] = {
        "org.agent_rpc.adapter": {
            "output_port": output_port,
            "worker_job_id": "job-upstream-001",
        }
    }
    return value


class ScientificRuntimeDagDataBindingTest(unittest.TestCase):
    def test_plan_graph_1_2_adds_only_typed_node_output_inputs(self) -> None:
        legacy = plan_graph()
        self.assertEqual(schema_errors("plan-graph.schema.json", legacy), [])

        plan = typed_plan()
        self.assertEqual(schema_errors("plan-graph.schema.json", plan), [])
        self.assertEqual(
            extract_plan_data_edges(plan),
            (
                PlanDataEdge(
                    target_node_id="invert",
                    target_input_port="model",
                    source_node_id="prepare",
                    source_output_port="prepared_model",
                    data_type="velocity_model_2d",
                ),
            ),
        )

        legacy_with_source = copy.deepcopy(plan)
        legacy_with_source["schema_version"] = "1.1.0"
        legacy_with_source["plan_hash"] = compute_plan_hash(legacy_with_source)
        self.assertTrue(schema_errors("plan-graph.schema.json", legacy_with_source))

        both_kinds = copy.deepcopy(plan)
        both_kinds["nodes"][1]["inputs"][0]["dataset"] = copy.deepcopy(
            plan["nodes"][0]["inputs"][0]["dataset"]
        )
        both_kinds["plan_hash"] = compute_plan_hash(both_kinds)
        self.assertTrue(schema_errors("plan-graph.schema.json", both_kinds))

        runtime_hash_in_plan = copy.deepcopy(plan)
        runtime_hash_in_plan["nodes"][1]["inputs"][0]["source"][
            "content_hash"
        ] = "sha256:" + "a" * 64
        runtime_hash_in_plan["plan_hash"] = compute_plan_hash(runtime_hash_in_plan)
        self.assertTrue(
            schema_errors("plan-graph.schema.json", runtime_hash_in_plan)
        )

        changed_source = copy.deepcopy(plan)
        changed_source["nodes"][1]["inputs"][0]["source"]["port"] = "loss"
        self.assertNotEqual(compute_plan_hash(changed_source), plan["plan_hash"])

    def test_readiness_requires_direct_typed_unambiguous_source(self) -> None:
        plan = typed_plan()
        readiness = evaluate_dag_readiness(
            plan,
            node_states={"prepare": PENDING, "invert": PENDING},
        )
        self.assertEqual(readiness.runnable_node_ids, ("prepare",))
        self.assertEqual(readiness.waiting_node_ids, ("invert",))

        cases: dict[str, Callable[[dict], None]] = {
            "not_dependency": lambda value: value["nodes"][1].update(
                dependencies=[]
            ),
            "unknown_output": lambda value: value["nodes"][1]["inputs"][0][
                "source"
            ].update(port="missing"),
            "type_drift": lambda value: value["nodes"][1]["inputs"][0][
                "source"
            ].update(data_type="loss_curve"),
            "ambiguous_output": lambda value: value["nodes"][0]["outputs"].append(
                copy.deepcopy(value["nodes"][0]["outputs"][0])
            ),
            "duplicate_target_port": lambda value: value["nodes"][1][
                "inputs"
            ].append(copy.deepcopy(value["nodes"][0]["inputs"][0])),
        }
        for label, mutate in cases.items():
            with self.subTest(label=label):
                invalid = typed_plan()
                mutate(invalid)
                invalid["plan_hash"] = compute_plan_hash(invalid)
                with self.assertRaises(DagScheduleError) as raised:
                    evaluate_dag_readiness(
                        invalid,
                        node_states={"prepare": PENDING, "invert": PENDING},
                    )
                self.assertEqual(raised.exception.code, "DAG_DATA_EDGE_INVALID")

    def test_verified_artifact_bytes_produce_one_canonical_dormant_binding(
        self,
    ) -> None:
        plan = typed_plan()
        artifact = upstream_artifact(plan)
        self.assertEqual(schema_errors("artifact-manifest.schema.json", artifact), [])

        binding = bind_dag_artifact_input(
            plan,
            task_id="task-001",
            target_node_id="invert",
            target_input_port="model",
            artifact_manifest=artifact,
            artifact_data=ARTIFACT_DATA,
        )
        self.assertEqual(binding.artifact_content_hash, artifact["content_hash"])
        self.assertEqual(binding.source_node_id, "prepare")
        self.assertEqual(binding.source_output_port, "prepared_model")
        self.assertFalse(binding.dispatch_authorized)
        self.assertEqual(
            binding.binding_document_hash,
            "sha256:"
            + hashlib.sha256(canonical_json_bytes(binding.document())).hexdigest(),
        )

        reordered = {key: artifact[key] for key in reversed(tuple(artifact))}
        replay = bind_dag_artifact_input(
            plan,
            task_id="task-001",
            target_node_id="invert",
            target_input_port="model",
            artifact_manifest=reordered,
            artifact_data=ARTIFACT_DATA,
        )
        self.assertEqual(replay, binding)

        alternate_media = copy.deepcopy(artifact)
        alternate_media["media_type"] = "application/octet-stream"
        alternate = bind_dag_artifact_input(
            plan,
            task_id="task-001",
            target_node_id="invert",
            target_input_port="model",
            artifact_manifest=alternate_media,
            artifact_data=ARTIFACT_DATA,
        )
        self.assertNotEqual(
            alternate.binding_document_hash, binding.binding_document_hash
        )

    def test_artifact_binding_rejects_identity_lineage_port_and_byte_drift(
        self,
    ) -> None:
        plan = typed_plan()
        base = upstream_artifact(plan)

        def set_output_port(value: dict) -> None:
            value["extensions"]["org.agent_rpc.adapter"]["output_port"] = "loss"

        def set_lineage_plan(value: dict) -> None:
            value["lineage"]["plan_hash"] = "sha256:" + "f" * 64

        def set_lineage_algorithm(value: dict) -> None:
            value["lineage"]["algorithm"] = {
                "id": "other.algorithm",
                "version": "1.0.0",
            }

        def set_lineage_inputs(value: dict) -> None:
            value["lineage"]["inputs"][0]["content_hash"] = (
                "sha256:" + "e" * 64
            )

        def set_fingerprint_inputs(value: dict) -> None:
            value["fingerprint"]["input_hashes"] = ["sha256:" + "e" * 64]

        mutations: dict[str, Callable[[dict], None]] = {
            "task": lambda value: value.update(task_id="task-other"),
            "node": lambda value: value.update(node_id="invert"),
            "type": lambda value: value.update(artifact_type="loss_curve"),
            "port": set_output_port,
            "plan": set_lineage_plan,
            "algorithm": set_lineage_algorithm,
            "lineage_inputs": set_lineage_inputs,
            "fingerprint_inputs": set_fingerprint_inputs,
            "seed": lambda value: value["fingerprint"].update(seed=2027),
            "device": lambda value: value["fingerprint"]["hardware"].update(
                device="cpu"
            ),
            "size": lambda value: value.update(size_bytes=len(ARTIFACT_DATA) + 1),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                artifact = copy.deepcopy(base)
                mutate(artifact)
                with self.assertRaises(DagDataBindingError) as raised:
                    bind_dag_artifact_input(
                        plan,
                        task_id="task-001",
                        target_node_id="invert",
                        target_input_port="model",
                        artifact_manifest=artifact,
                        artifact_data=ARTIFACT_DATA,
                    )
                self.assertEqual(raised.exception.code, "DAG_ARTIFACT_MISMATCH")

        with self.assertRaises(DagDataBindingError) as raised:
            bind_dag_artifact_input(
                plan,
                task_id="task-001",
                target_node_id="invert",
                target_input_port="model",
                artifact_manifest=base,
                artifact_data=ARTIFACT_DATA + b"-tampered",
            )
        self.assertEqual(raised.exception.code, "DAG_ARTIFACT_MISMATCH")

        malformed = copy.deepcopy(base)
        del malformed["artifact_id"]
        with self.assertRaises(DagDataBindingError) as raised:
            bind_dag_artifact_input(
                plan,
                task_id="task-001",
                target_node_id="invert",
                target_input_port="model",
                artifact_manifest=malformed,
                artifact_data=ARTIFACT_DATA,
            )
        self.assertEqual(raised.exception.code, "DAG_ARTIFACT_INVALID")

        chained = typed_plan()
        root = copy.deepcopy(chained["nodes"][0])
        root["node_id"] = "root"
        root["idempotency_key"] = "task-001:root:0001"
        chained["nodes"][0]["dependencies"] = ["root"]
        chained["nodes"][0]["inputs"] = [
            {
                "port": "model",
                "source": {
                    "node_id": "root",
                    "port": "prepared_model",
                    "data_type": "velocity_model_2d",
                },
            }
        ]
        chained["nodes"] = [root, *chained["nodes"]]
        chained["plan_hash"] = compute_plan_hash(chained)
        with self.assertRaises(DagDataBindingError) as raised:
            bind_dag_artifact_input(
                chained,
                task_id="task-001",
                target_node_id="invert",
                target_input_port="model",
                artifact_manifest=upstream_artifact(chained),
                artifact_data=ARTIFACT_DATA,
            )
        self.assertEqual(
            raised.exception.code, "DAG_ARTIFACT_LINEAGE_UNSUPPORTED"
        )

    def test_binding_requires_an_exact_declared_target_input(self) -> None:
        plan = typed_plan()
        with self.assertRaises(DagDataBindingError) as raised:
            bind_dag_artifact_input(
                plan,
                task_id="task-001",
                target_node_id="invert",
                target_input_port="unknown",
                artifact_manifest=upstream_artifact(plan),
                artifact_data=ARTIFACT_DATA,
            )
        self.assertEqual(raised.exception.code, "DAG_DATA_EDGE_NOT_FOUND")

        with self.assertRaises(DagDataBindingError) as raised:
            bind_dag_artifact_input(
                None,  # type: ignore[arg-type]
                task_id="task-001",
                target_node_id="invert",
                target_input_port="model",
                artifact_manifest=upstream_artifact(plan),
                artifact_data=ARTIFACT_DATA,
            )
        self.assertEqual(raised.exception.code, "DAG_DATA_PLAN_INVALID")

    def test_typed_plan_persists_and_binding_has_zero_runtime_side_effects(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database_path = Path(temporary) / "task.sqlite3"
            store = SQLiteTaskStore(database_path)
            registry = RegistryService(store, clock=lambda: "2026-07-15T02:30:00Z")
            dataset = dataset_ref()
            registry.register_dataset(dataset=dataset)

            manifest = copy.deepcopy(load_deepwave_manifest("1.5.0"))
            manifest["id"] = "test.velocity_passthrough"
            manifest["outputs"] = [
                {"port": "prepared_model", "data_type": "velocity_model_2d"}
            ]
            registry.register_algorithm(manifest=manifest)

            draft = optimizer_task_draft(algorithm_version="1.5.0")
            draft["algorithm"]["id"] = manifest["id"]
            service = TaskService(
                store,
                task_id_factory=lambda: "task-001",
                clock=lambda: "2026-07-15T02:30:00Z",
            )
            created = service.create_task(
                project_id="project-1",
                principal_id="user-1",
                draft=draft,
                idempotency_key="create-typed-edge",
            )

            plan = optimizer_plan_graph(algorithm_version="1.5.0")
            plan["schema_version"] = "1.2.0"
            plan["draft"] = {
                "draft_id": created.snapshot.draft["draft_id"],
                "revision": created.snapshot.draft["revision"],
            }
            source = copy.deepcopy(plan["nodes"][0])
            source["node_id"] = "prepare"
            source["algorithm"]["id"] = manifest["id"]
            source["outputs"] = copy.deepcopy(manifest["outputs"])
            source["idempotency_key"] = "task-001:prepare:0001"
            target = copy.deepcopy(source)
            target["node_id"] = "invert"
            target["idempotency_key"] = "task-001:invert:0001"
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
            incompatible = copy.deepcopy(plan)
            incompatible["nodes"][0]["outputs"][0]["data_type"] = "loss_curve"
            incompatible["nodes"][1]["inputs"][0]["source"][
                "data_type"
            ] = "loss_curve"
            incompatible["plan_hash"] = compute_plan_hash(incompatible)
            with self.assertRaises(TaskValidationError) as raised:
                service.persist_plan(
                    task_id="task-001",
                    project_id="project-1",
                    principal_id="user-1",
                    plan=incompatible,
                )
            self.assertEqual(raised.exception.code, "PLAN_REGISTRY_MISMATCH")
            self.assertIsNone(
                service.get_task(
                    "task-001",
                    project_id="project-1",
                    principal_id="user-1",
                ).plan
            )
            service.persist_plan(
                task_id="task-001",
                project_id="project-1",
                principal_id="user-1",
                plan=plan,
            )
            approval = approval_decision(plan)
            approval["scope"]["algorithms"] = [
                {"id": manifest["id"], "version": manifest["version"]}
            ]
            service.persist_approval(
                task_id="task-001",
                project_id="project-1",
                principal_id="user-1",
                approval=approval,
            )
            lease = service.acquire_runtime_supervisor_lease(
                project_id="project-1",
                principal_id="user-1",
                owner_id="typed-edge-supervisor",
                lease_seconds=30,
            ).lease
            candidate = service.claim_ready_dag_node_candidate(
                "task-001",
                project_id="project-1",
                principal_id="user-1",
                expected_plan_hash=plan["plan_hash"],
                supervisor_lease=lease,
            )
            self.assertEqual(candidate.node.node_id, "prepare")
            self.assertFalse(candidate.dispatch_authorized)

            approved_snapshot = service.get_task(
                "task-001", project_id="project-1", principal_id="user-1"
            )
            event = run_event()
            event["task_id"] = "task-001"
            event["node_id"] = "invert"
            with self.assertRaises(TaskValidationError) as raised:
                task_service_module._validate_run_event_binding(
                    approved_snapshot, event
                )
            self.assertEqual(
                raised.exception.code, "RUN_EVENT_INPUT_BINDING_REQUIRED"
            )
            with self.assertRaises(DispatchError):
                DeepwaveTaskDispatcher._request_from_snapshot(approved_snapshot)
            submitting_service = TaskService(
                store,
                clock=lambda: "2026-07-15T02:30:00Z",
                dispatcher=object(),
            )
            with self.assertRaises(TaskValidationError) as raised:
                submitting_service.submit_task(
                    task_id="task-001",
                    project_id="project-1",
                    principal_id="user-1",
                    approval_id=approval["approval_id"],
                    idempotency_key="submit-typed-edge",
                )
            self.assertEqual(
                raised.exception.code, "PLAN_CAPABILITY_UNSUPPORTED_IN_P1"
            )

            artifact = upstream_artifact(
                plan,
                output_port="prepared_model",
                data_type="velocity_model_2d",
            )
            artifact["lineage"]["algorithm"] = copy.deepcopy(source["algorithm"])
            artifact["fingerprint"]["algorithm"] = copy.deepcopy(
                source["algorithm"]
            )
            binding = bind_dag_artifact_input(
                plan,
                task_id="task-001",
                target_node_id="invert",
                target_input_port="model",
                artifact_manifest=artifact,
                artifact_data=ARTIFACT_DATA,
            )
            self.assertFalse(binding.dispatch_authorized)

            snapshot = service.get_task(
                "task-001", project_id="project-1", principal_id="user-1"
            )
            budget = store.get_approval_budget(
                task_id="task-001", approval_id=approval["approval_id"]
            )
            connection = sqlite3.connect(database_path)
            try:
                dispatch_count = connection.execute(
                    "SELECT COUNT(*) FROM dispatch_intents"
                ).fetchone()[0]
                event_count = connection.execute(
                    "SELECT COUNT(*) FROM run_events"
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(snapshot.status, "AwaitingApproval")
            self.assertEqual(budget.tasks_used, 0)
            self.assertEqual(dispatch_count, 0)
            self.assertEqual(event_count, 0)


if __name__ == "__main__":
    unittest.main()
