from __future__ import annotations

import copy
import hashlib
import unittest

from scientific_runtime.dag_node_cache import (
    NodeCacheIdentityError,
    build_node_cache_identity,
)
from scientific_runtime_contracts import canonical_json_bytes


HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64
HASH_C = "sha256:" + "c" * 64
HASH_D = "sha256:" + "d" * 64


def document_hash(value: dict) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def plan() -> dict:
    return {
        "schema_version": "1.2.0",
        "plan_id": "plan-task-001",
        "draft": {"draft_id": "draft-task-001", "revision": 7},
        "task_type": "acoustic_fwi_2d",
        "nodes": [
            {
                "node_id": "invert",
                "algorithm": {
                    "id": "deepwave.acoustic_fwi",
                    "version": "1.6.0",
                },
                "inputs": [
                    {
                        "port": "model",
                        "dataset": {
                            "id": "marmousi",
                            "version": "1.0.0",
                            "content_hash": HASH_A,
                            "data_type": "velocity_model_2d",
                        },
                    }
                ],
                "dependencies": [],
                "parameters": {
                    "preset": "fwi_smoke",
                    "iterations": 2,
                    "seed": 2026,
                },
                "resources": {
                    "device": "cpu",
                    "gpu_count": 0,
                    "cpu_cores": 4,
                    "memory_mb": 8192,
                    "wall_time_seconds": 1800,
                },
                "side_effects": ["compute", "write_artifacts"],
                "outputs": [
                    {
                        "port": "inverted_model",
                        "data_type": "inverted_velocity_model_2d",
                    }
                ],
                "idempotency_key": "task-001:invert:0001",
                "risks": [{"code": "instance-only-explanation"}],
            }
        ],
        "plan_hash": HASH_D,
        "created_at": "2026-07-19T01:00:00Z",
        "extensions": {"request_trace": "trace-task-001"},
    }


def manifest() -> dict:
    return {
        "schema_version": "1.0.0",
        "id": "deepwave.acoustic_fwi",
        "version": "1.6.0",
        "task_types": ["acoustic_fwi_2d"],
        "parameter_schema": {
            "type": "object",
            "properties": {"iterations": {"type": "integer"}},
        },
        "inputs": [{"port": "model", "data_type": "velocity_model_2d"}],
        "outputs": [
            {
                "port": "inverted_model",
                "data_type": "inverted_velocity_model_2d",
            }
        ],
        "security": {
            "allowlisted": True,
            "side_effects": ["compute", "write_artifacts"],
            "network_access": False,
        },
        "adapter": {"version": "1.6.0", "protocol": "algorithm-adapter-v1"},
    }


def binding(*, task_id: str = "task-001") -> dict:
    return {
        "schema_version": "1.0.0",
        "task_id": task_id,
        "plan": {"plan_id": f"plan-{task_id}", "plan_hash": HASH_D},
        "approval_id": f"approval-{task_id}",
        "target": {"node_id": "invert", "revision": 1, "state": "Pending"},
        "scope": {"project_id": "project-1", "principal_id": "user-1"},
        "supervisor_term": {
            "fencing_token": 9,
            "owner_id": "supervisor-1",
            "acquired_at": "2026-07-19T01:00:01Z",
        },
        "claim_readiness_document_hash": HASH_C,
        "inputs": [
            {
                "input_index": 0,
                "target_input_port": "model",
                "kind": "dataset",
                "dataset": {
                    "id": "marmousi",
                    "version": "1.0.0",
                    "content_hash": HASH_A,
                    "data_type": "velocity_model_2d",
                },
                "dataset_document_hash": HASH_B,
            }
        ],
    }


def fingerprint() -> dict:
    return {
        "provenance_mode": "reproducible",
        "algorithm": {
            "id": "deepwave.acoustic_fwi",
            "version": "1.6.0",
        },
        "adapter_version": "1.6.0",
        "source": {
            "identity_complete": True,
            "git_commit": "1" * 40,
            "git_tree": "2" * 40,
            "dirty": False,
        },
        "environment": {"environment_lock_hash": HASH_C},
        "runtime": {
            "python": "3.10.12",
            "pytorch": "2.6.0",
            "deepwave": "0.0.23",
            "cuda": None,
        },
        "seed": 2026,
        "hardware": {
            "device": "cpu",
            "device_name": "test-cpu",
            "compute_capability": None,
        },
        "normalized_config_hash": HASH_D,
        "input_hashes": [HASH_A],
        "determinism": {
            "requested": True,
            "framework_deterministic": True,
            "flags": {"torch_deterministic_algorithms": True},
            "known_nondeterminism": [],
        },
    }


def approval_scope() -> dict:
    return {
        "datasets": [
            {
                "id": "marmousi",
                "version": "1.0.0",
                "content_hash": HASH_A,
                "data_type": "velocity_model_2d",
            }
        ],
        "algorithms": [
            {"id": "deepwave.acoustic_fwi", "version": "1.6.0"}
        ],
        "resource_limits": {
            "device": "cpu",
            "gpu_count": 0,
            "cpu_cores": 4,
            "memory_mb": 8192,
            "wall_time_seconds": 1800,
        },
        "side_effects": ["compute", "write_artifacts"],
        "max_tasks": 1,
    }


def build(
    *,
    current_plan: dict | None = None,
    current_binding: dict | None = None,
    current_manifest: dict | None = None,
    current_fingerprint: dict | None = None,
    current_scope: dict | None = None,
    adapter_id: str = "fwi.deepwave_adapter",
    adapter_version: str = "1.6.0",
    project_id: str = "project-1",
    principal_id: str = "user-1",
):
    algorithm_manifest = current_manifest or manifest()
    return build_node_cache_identity(
        current_plan or plan(),
        node_id="invert",
        input_binding_document=current_binding or binding(),
        adapter_id=adapter_id,
        adapter_version=adapter_version,
        queue_fingerprint=current_fingerprint or fingerprint(),
        algorithm_manifest=algorithm_manifest,
        algorithm_manifest_document_hash=document_hash(algorithm_manifest),
        approval_scope=current_scope or approval_scope(),
        project_id=project_id,
        principal_id=principal_id,
    )


class ScientificRuntimeDagNodeCacheIdentityTest(unittest.TestCase):
    def test_identity_is_canonical_and_ignores_instance_metadata(self) -> None:
        first = build()
        changed_plan = copy.deepcopy(plan())
        changed_plan.update(
            plan_id="plan-task-999",
            draft={"draft_id": "draft-task-999", "revision": 99},
            plan_hash="sha256:" + "9" * 64,
            created_at="2030-01-01T00:00:00Z",
            extensions={"request_trace": "trace-task-999"},
        )
        changed_plan["nodes"][0]["idempotency_key"] = "task-999:invert:1234"
        changed_plan["nodes"][0]["risks"] = [{"code": "different-narrative"}]
        changed_binding = binding(task_id="task-999")
        changed_binding["target"]["revision"] = 41
        changed_binding["supervisor_term"] = {
            "fencing_token": 91,
            "owner_id": "other-supervisor",
            "acquired_at": "2030-01-01T00:00:01Z",
        }
        changed_binding["claim_readiness_document_hash"] = "sha256:" + "8" * 64
        changed_fingerprint = fingerprint()
        changed_fingerprint.update(
            task_id="task-999",
            plan_id="plan-task-999",
            approval_id="approval-task-999",
            timestamp="2030-01-01T00:00:02Z",
        )

        replay = build(
            current_plan=changed_plan,
            current_binding=changed_binding,
            current_fingerprint=changed_fingerprint,
        )
        self.assertEqual(replay, first)
        self.assertEqual(first.document["schema_version"], "1.0.0")
        self.assertEqual(document_hash(first.document), first.document_hash)
        self.assertNotIn("task_id", repr(first.document))
        self.assertNotIn("idempotency_key", repr(first.document))
        self.assertNotIn("supervisor_term", repr(first.document))

        reordered_plan = {key: plan()[key] for key in reversed(tuple(plan()))}
        reordered_binding = {
            key: binding()[key] for key in reversed(tuple(binding()))
        }
        reordered_manifest = {
            key: manifest()[key] for key in reversed(tuple(manifest()))
        }
        reordered_fingerprint = {
            key: fingerprint()[key] for key in reversed(tuple(fingerprint()))
        }
        reordered_scope = {
            key: approval_scope()[key]
            for key in reversed(tuple(approval_scope()))
        }
        self.assertEqual(
            build(
                current_plan=reordered_plan,
                current_binding=reordered_binding,
                current_manifest=reordered_manifest,
                current_fingerprint=reordered_fingerprint,
                current_scope=reordered_scope,
            ),
            first,
        )

    def test_semantic_execution_and_scope_changes_produce_distinct_keys(self) -> None:
        baseline = build().document_hash
        cases = {}

        changed_plan = copy.deepcopy(plan())
        changed_plan["nodes"][0]["parameters"]["iterations"] = 3
        cases["parameters"] = build(current_plan=changed_plan).document_hash

        changed_plan = copy.deepcopy(plan())
        changed_binding = binding()
        changed_fingerprint = fingerprint()
        changed_plan["nodes"][0]["inputs"][0]["dataset"]["content_hash"] = HASH_B
        changed_binding["inputs"][0]["dataset"]["content_hash"] = HASH_B
        changed_fingerprint["input_hashes"] = [HASH_B]
        cases["input hash"] = build(
            current_plan=changed_plan,
            current_binding=changed_binding,
            current_fingerprint=changed_fingerprint,
        ).document_hash

        changed_manifest = copy.deepcopy(manifest())
        changed_manifest["security"]["network_access"] = True
        cases["algorithm manifest"] = build(
            current_manifest=changed_manifest
        ).document_hash

        changed_plan = copy.deepcopy(plan())
        changed_manifest = copy.deepcopy(manifest())
        changed_fingerprint = fingerprint()
        changed_scope = approval_scope()
        changed_plan["nodes"][0]["algorithm"]["version"] = "1.7.0"
        changed_manifest["version"] = "1.7.0"
        changed_fingerprint["algorithm"]["version"] = "1.7.0"
        changed_scope["algorithms"][0]["version"] = "1.7.0"
        cases["algorithm version"] = build(
            current_plan=changed_plan,
            current_manifest=changed_manifest,
            current_fingerprint=changed_fingerprint,
            current_scope=changed_scope,
        ).document_hash

        changed_fingerprint = fingerprint()
        changed_fingerprint["adapter_version"] = "1.7.0"
        cases["adapter"] = build(
            adapter_id="fwi.next_adapter",
            adapter_version="1.7.0",
            current_fingerprint=changed_fingerprint,
        ).document_hash

        changed_plan = copy.deepcopy(plan())
        changed_plan["nodes"][0]["outputs"][0]["data_type"] = "other_model"
        cases["outputs"] = build(current_plan=changed_plan).document_hash

        changed_fingerprint = fingerprint()
        changed_fingerprint["environment"]["environment_lock_hash"] = HASH_D
        cases["environment"] = build(
            current_fingerprint=changed_fingerprint
        ).document_hash

        changed_fingerprint = fingerprint()
        changed_fingerprint["source"]["git_commit"] = "3" * 40
        cases["code commit"] = build(
            current_fingerprint=changed_fingerprint
        ).document_hash

        changed_fingerprint = fingerprint()
        changed_fingerprint["runtime"]["python"] = "3.11.9"
        cases["runtime"] = build(
            current_fingerprint=changed_fingerprint
        ).document_hash

        cases["project"] = build(
            current_binding={
                **binding(),
                "scope": {"project_id": "project-2", "principal_id": "user-1"},
            },
            project_id="project-2",
        ).document_hash
        cases["principal"] = build(
            current_binding={
                **binding(),
                "scope": {"project_id": "project-1", "principal_id": "user-2"},
            },
            principal_id="user-2",
        ).document_hash
        changed_scope = approval_scope()
        changed_scope["max_tasks"] = 2
        cases["approval scope"] = build(
            current_scope=changed_scope
        ).document_hash

        exact_cache_index = {baseline: "durable source"}
        for label, value in cases.items():
            with self.subTest(label=label):
                self.assertNotEqual(value, baseline)
                self.assertIsNone(exact_cache_index.get(value))
        self.assertEqual(len(set(cases.values())), len(cases))

    def test_node_output_binds_artifact_and_transitive_dataset_lineage(self) -> None:
        current_plan = plan()
        current_plan["nodes"][0]["inputs"] = [
            {
                "port": "model",
                "source": {
                    "node_id": "prepare",
                    "port": "prepared_model",
                    "data_type": "velocity_model_2d",
                },
            }
        ]
        current_plan["nodes"][0]["dependencies"] = ["prepare"]
        current_binding = binding()
        current_binding["inputs"] = [
            {
                "input_index": 0,
                "target_input_port": "model",
                "kind": "node_output",
                "binding": {
                    "schema_version": "1.0.0",
                    "task_id": "task-001",
                    "plan": {"plan_id": "plan-task-001", "plan_hash": HASH_D},
                    "target": {"node_id": "invert", "input_port": "model"},
                    "source": {
                        "node_id": "prepare",
                        "output_port": "prepared_model",
                        "algorithm": {"id": "prepare", "version": "1.0.0"},
                        "data_type": "velocity_model_2d",
                    },
                    "artifact": {
                        "artifact_id": "artifact-task-001-prepare",
                        "schema_version": "1.0.0",
                        "media_type": "application/x-npy",
                        "content_hash": HASH_B,
                        "size_bytes": 4096,
                    },
                },
                "binding_document_hash": HASH_C,
                "artifact_manifest_hash": HASH_D,
                "producer": {
                    "node_id": "prepare",
                    "succeeded_revision": 2,
                    "approval_id": "approval-task-001",
                    "input_binding_document_hash": HASH_A,
                    "receipt_document_hash": HASH_B,
                    "receipt_record_hash": HASH_C,
                    "succeeded_at": "2026-07-19T01:01:00Z",
                    "fencing_token": 9,
                    "owner_id": "supervisor-1",
                    "term_acquired_at": "2026-07-19T01:00:01Z",
                    "semantic_cache_key_hash": HASH_D,
                    "cacheable": True,
                    "transitive_dataset_roots": [
                        {
                            "id": "marmousi",
                            "version": "1.0.0",
                            "content_hash": HASH_A,
                            "data_type": "velocity_model_2d",
                            "catalog_document_hash": HASH_B,
                        }
                    ],
                },
            }
        ]
        current_fingerprint = fingerprint()
        current_fingerprint["input_hashes"] = [HASH_B]
        identity = build(
            current_plan=current_plan,
            current_binding=current_binding,
            current_fingerprint=current_fingerprint,
        )
        normalized = identity.document["inputs"][0]
        self.assertEqual(normalized["artifact"]["content_hash"], HASH_B)
        self.assertEqual(
            normalized["producer"]["semantic_cache_key_hash"], HASH_D
        )
        self.assertTrue(normalized["producer"]["cacheable"])
        self.assertEqual(
            normalized["producer"]["transitive_dataset_roots"][0][
                "catalog_document_hash"
            ],
            HASH_B,
        )
        rendered = repr(identity.document)
        self.assertNotIn("artifact-task-001", rendered)
        self.assertNotIn("receipt_document_hash", rendered)
        self.assertNotIn("succeeded_at", rendered)

        uncacheable_binding = copy.deepcopy(current_binding)
        uncacheable_binding["inputs"][0]["producer"]["cacheable"] = False
        with self.assertRaises(NodeCacheIdentityError) as raised:
            build(
                current_plan=current_plan,
                current_binding=uncacheable_binding,
                current_fingerprint=current_fingerprint,
            )
        self.assertEqual(raised.exception.code, "NODE_CACHE_UNCACHEABLE")

    def test_b_c_d_lineage_reconstructs_to_original_dataset(self) -> None:
        dataset_root = {
            "id": "marmousi",
            "version": "1.0.0",
            "content_hash": HASH_A,
            "data_type": "velocity_model_2d",
            "catalog_document_hash": HASH_B,
        }
        lineage_scope = approval_scope()
        lineage_scope["algorithms"] = [
            {"id": f"lineage.{node_id.lower()}", "version": "1.0.0"}
            for node_id in ("B", "C", "D")
        ]

        def node_plan(
            node_id: str,
            *,
            source_node_id: str | None,
        ) -> dict:
            current = plan()
            node = current["nodes"][0]
            node["node_id"] = node_id
            node["algorithm"] = {
                "id": f"lineage.{node_id.lower()}",
                "version": "1.0.0",
            }
            node["dependencies"] = (
                [] if source_node_id is None else [source_node_id]
            )
            node["inputs"] = (
                [
                    {
                        "port": "model",
                        "dataset": {
                            key: dataset_root[key]
                            for key in (
                                "id",
                                "version",
                                "content_hash",
                                "data_type",
                            )
                        },
                    }
                ]
                if source_node_id is None
                else [
                    {
                        "port": "model",
                        "source": {
                            "node_id": source_node_id,
                            "port": "model",
                            "data_type": "velocity_model_2d",
                        },
                    }
                ]
            )
            node["outputs"] = [
                {"port": "model", "data_type": "velocity_model_2d"}
            ]
            return current

        def node_manifest(node_id: str) -> dict:
            current = manifest()
            current["id"] = f"lineage.{node_id.lower()}"
            current["version"] = "1.0.0"
            current["inputs"] = [
                {"port": "model", "data_type": "velocity_model_2d"}
            ]
            current["outputs"] = [
                {"port": "model", "data_type": "velocity_model_2d"}
            ]
            return current

        def node_fingerprint(node_id: str, input_hash: str) -> dict:
            current = fingerprint()
            current["algorithm"] = {
                "id": f"lineage.{node_id.lower()}",
                "version": "1.0.0",
            }
            current["input_hashes"] = [input_hash]
            return current

        def dataset_binding(node_id: str) -> dict:
            current = binding()
            current["target"]["node_id"] = node_id
            current["inputs"][0] = {
                "input_index": 0,
                "target_input_port": "model",
                "kind": "dataset",
                "dataset": {
                    key: dataset_root[key]
                    for key in (
                        "id",
                        "version",
                        "content_hash",
                        "data_type",
                    )
                },
                "dataset_document_hash": dataset_root[
                    "catalog_document_hash"
                ],
            }
            return current

        def output_binding(
            node_id: str,
            *,
            source_node_id: str,
            artifact_hash: str,
            producer_key: str,
        ) -> dict:
            current = binding()
            current["target"]["node_id"] = node_id
            current["inputs"] = [
                {
                    "input_index": 0,
                    "target_input_port": "model",
                    "kind": "node_output",
                    "binding": {
                        "schema_version": "1.0.0",
                        "target": {
                            "node_id": node_id,
                            "input_port": "model",
                        },
                        "source": {
                            "node_id": source_node_id,
                            "output_port": "model",
                            "data_type": "velocity_model_2d",
                        },
                        "artifact": {
                            "schema_version": "1.0.0",
                            "media_type": "application/x-npy",
                            "content_hash": artifact_hash,
                            "size_bytes": 4096,
                        },
                    },
                    "producer": {
                        "semantic_cache_key_hash": producer_key,
                        "cacheable": True,
                        "transitive_dataset_roots": [dataset_root],
                    },
                }
            ]
            return current

        identities = []
        b_manifest = node_manifest("B")
        b_identity = build_node_cache_identity(
            node_plan("B", source_node_id=None),
            node_id="B",
            input_binding_document=dataset_binding("B"),
            adapter_id="lineage.test_adapter",
            adapter_version="1.6.0",
            queue_fingerprint=node_fingerprint("B", HASH_A),
            algorithm_manifest=b_manifest,
            algorithm_manifest_document_hash=document_hash(b_manifest),
            approval_scope=lineage_scope,
            project_id="project-1",
            principal_id="user-1",
        )
        identities.append(b_identity)

        previous_node_id = "B"
        previous_key = b_identity.document_hash
        for node_id, artifact_hash in (("C", HASH_C), ("D", HASH_D)):
            current_manifest = node_manifest(node_id)
            current_identity = build_node_cache_identity(
                node_plan(node_id, source_node_id=previous_node_id),
                node_id=node_id,
                input_binding_document=output_binding(
                    node_id,
                    source_node_id=previous_node_id,
                    artifact_hash=artifact_hash,
                    producer_key=previous_key,
                ),
                adapter_id="lineage.test_adapter",
                adapter_version="1.6.0",
                queue_fingerprint=node_fingerprint(node_id, artifact_hash),
                algorithm_manifest=current_manifest,
                algorithm_manifest_document_hash=document_hash(
                    current_manifest
                ),
                approval_scope=lineage_scope,
                project_id="project-1",
                principal_id="user-1",
            )
            identities.append(current_identity)
            previous_node_id = node_id
            previous_key = current_identity.document_hash

        by_cache_key = {
            identity.document_hash: identity for identity in identities
        }
        cursor = identities[-1]
        reconstructed_nodes = []
        while True:
            reconstructed_nodes.append(
                cursor.document["node_contract"]["node_id"]
            )
            current_input = cursor.document["inputs"][0]
            if current_input["kind"] == "dataset":
                reconstructed_root = current_input["dataset"]
                break
            self.assertEqual(
                current_input["producer"]["transitive_dataset_roots"],
                [dataset_root],
            )
            producer_key = current_input["producer"][
                "semantic_cache_key_hash"
            ]
            self.assertIn(producer_key, by_cache_key)
            cursor = by_cache_key[producer_key]

        self.assertEqual(reconstructed_nodes, ["D", "C", "B"])
        self.assertEqual(reconstructed_root, dataset_root)
        self.assertEqual(
            [
                identity.document["execution_fingerprint"]["input_hashes"]
                for identity in identities
            ],
            [[HASH_A], [HASH_C], [HASH_D]],
        )

    def test_mismatched_hashes_and_incomplete_provenance_fail_closed(self) -> None:
        with self.assertRaises(NodeCacheIdentityError) as raised:
            build_node_cache_identity(
                plan(),
                node_id="invert",
                input_binding_document=binding(),
                adapter_id="fwi.deepwave_adapter",
                adapter_version="1.6.0",
                queue_fingerprint=fingerprint(),
                algorithm_manifest=manifest(),
                algorithm_manifest_document_hash=HASH_A,
                approval_scope=approval_scope(),
                project_id="project-1",
                principal_id="user-1",
            )
        self.assertEqual(raised.exception.code, "NODE_CACHE_IDENTITY_INVALID")

        drifted = fingerprint()
        drifted["input_hashes"] = [HASH_B]
        with self.assertRaises(NodeCacheIdentityError) as raised:
            build(current_fingerprint=drifted)
        self.assertEqual(raised.exception.code, "NODE_CACHE_IDENTITY_INVALID")

        for label, mutate in {
            "incomplete": lambda value: value["source"].update(
                identity_complete=False
            ),
            "dirty": lambda value: value["source"].update(dirty=True),
            "missing tree": lambda value: value["source"].pop("git_tree"),
            "missing environment": lambda value: value["environment"].pop(
                "environment_lock_hash"
            ),
            "development mode": lambda value: value.update(
                provenance_mode="development"
            ),
        }.items():
            with self.subTest(label=label):
                uncacheable = fingerprint()
                mutate(uncacheable)
                with self.assertRaises(NodeCacheIdentityError) as raised:
                    build(current_fingerprint=uncacheable)
                self.assertEqual(raised.exception.code, "NODE_CACHE_UNCACHEABLE")


if __name__ == "__main__":
    unittest.main()
