from __future__ import annotations

import copy
import unittest
from datetime import datetime, timezone

from jsonschema import Draft7Validator

from scientific_runtime_contracts import (
    canonical_json_bytes,
    compute_plan_hash,
    evaluate_execution_gate,
    load_schema,
    schema_errors,
)
from scientific_runtime.fwi_registry import load_deepwave_manifest
from scientific_runtime.fixed_recipe import fixed_recipe_plan_inputs


HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64
HASH_C = "sha256:" + "c" * 64
HASH_D = "sha256:" + "d" * 64
ALGORITHM_VERSION = "1.1.0"
CURRENT_OPTIMIZER_ALGORITHM_VERSION = "1.5.0"
LEGACY_OPTIMIZER_ALGORITHM_VERSION = "1.2.0"


def dataset_ref() -> dict:
    return {
        "schema_version": "1.0.0",
        "id": "marmousi_94_288",
        "version": "1.0.0",
        "content_hash": HASH_A,
        "data_type": "velocity_model_2d",
        "immutable": True,
        "metadata": {
            "shape": [94, 288],
            "dtype": "float32",
            "axis_order": ["z", "x"],
            "units": "m/s",
            "physics": "2d_acoustic_constant_density",
            "parameter": "vp",
            "grid_spacing_m": {"dx": 10, "dz": 10},
            "value_range": {"minimum": 1500, "maximum": 5500},
        },
        "lineage": [],
        "access_scope": {
            "project_id": "project-1",
            "principals": ["user-1"],
            "permissions": ["read", "execute"],
        },
        "extensions": {},
    }


def algorithm_manifest() -> dict:
    return {
        "schema_version": "1.0.0",
        "id": "deepwave.acoustic_fwi",
        "version": ALGORITHM_VERSION,
        "task_types": ["acoustic_forward_2d", "acoustic_fwi_2d"],
        "parameter_schema": {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "preset": {"enum": ["forward", "fwi_smoke", "fwi_demo"]},
                "device": {"enum": ["cpu", "cuda"]},
                "iterations": {"type": "integer", "minimum": 0, "maximum": 10000},
                "seed": {"type": "integer", "minimum": 0},
            },
            "required": ["preset", "device", "iterations", "seed"],
        },
        "inputs": [{"port": "model", "data_type": "velocity_model_2d"}],
        "outputs": [
            {"port": "inverted_model", "data_type": "inverted_velocity_model_2d"},
            {"port": "loss", "data_type": "loss_curve"},
        ],
        "resource_limits": {
            "devices": ["cpu", "cuda"],
            "max_gpu_count": 1,
            "max_cpu_cores": 16,
            "max_memory_mb": 65536,
            "max_wall_time_seconds": 7200,
        },
        "security": {
            "allowlisted": True,
            "side_effects": ["compute", "write_artifacts"],
            "arbitrary_paths": False,
            "shell": False,
            "network_access": False,
        },
        "adapter": {
            "protocol": "algorithm-adapter-v1",
            "version": "1.1.0",
            "entrypoint_ref": "fwi.deepwave_adapter",
            "methods": ["validate", "estimate", "submit", "status", "cancel", "collect"],
            "idempotent_submit": True,
            "checkpoint_capable": False,
        },
        "extensions": {},
    }


def resources() -> dict:
    return {
        "device": "cuda",
        "gpu_count": 1,
        "cpu_cores": 4,
        "memory_mb": 8192,
        "wall_time_seconds": 1800,
    }


def task_draft() -> dict:
    return {
        "schema_version": "1.0.0",
        "draft_id": "draft-001",
        "revision": 1,
        "status": "AwaitingApproval",
        "goal": "Run the registered Marmousi Deepwave FWI smoke baseline.",
        "task_type": "acoustic_fwi_2d",
        "datasets": [dataset_ref()],
        "algorithm": {"id": "deepwave.acoustic_fwi", "version": ALGORITHM_VERSION},
        "parameters": {
            "preset": "fwi_smoke",
            "device": "cuda",
            "iterations": 2,
            "seed": 2026,
        },
        "resources": resources(),
        "missing_fields": [],
        "suggestions": ["Keep the two-iteration smoke limit for the first slice."],
        "confidence": {
            "intent": 1.0,
            "parameters": 1.0,
            "datasets": 1.0,
            "explanation": "All values came from the deterministic Guided form.",
        },
        "extensions": {},
    }


def optimizer_task_draft(
    *,
    optimizer: str = "adam",
    learning_rate_milli: int = 10_000,
    algorithm_version: str = CURRENT_OPTIMIZER_ALGORITHM_VERSION,
) -> dict:
    draft = task_draft()
    draft["schema_version"] = "1.1.0"
    draft["algorithm"]["version"] = algorithm_version
    draft["parameters"].update(
        optimizer=optimizer, learning_rate_milli=learning_rate_milli
    )
    return draft


def plan_graph() -> dict:
    dataset = dataset_ref()
    plan = {
        "schema_version": "1.0.0",
        "plan_id": "plan-001",
        "draft": {"draft_id": "draft-001", "revision": 1},
        "task_type": "acoustic_fwi_2d",
        "nodes": [
            {
                "node_id": "invert",
                "algorithm": {
                    "id": "deepwave.acoustic_fwi",
                    "version": ALGORITHM_VERSION,
                },
                "inputs": [
                    {
                        "port": "model",
                        "dataset": {
                            key: dataset[key]
                            for key in ("id", "version", "content_hash", "data_type")
                        },
                    }
                ],
                "outputs": [
                    {"port": "inverted_model", "data_type": "inverted_velocity_model_2d"},
                    {"port": "loss", "data_type": "loss_curve"},
                ],
                "dependencies": [],
                "parameters": {
                    "preset": "fwi_smoke",
                    "device": "cuda",
                    "iterations": 2,
                    "seed": 2026,
                },
                "resources": resources(),
                "side_effects": ["compute", "write_artifacts"],
                "idempotency_key": "task-001:invert:0001",
                "risks": [
                    {
                        "code": "inverse_crime",
                        "severity": "medium",
                        "mitigation": "Label the result as synthetic workflow evidence only.",
                    }
                ],
                "acceptance_criteria": [
                    {
                        "id": "finite_metrics",
                        "description": "All reported numerical metrics are finite.",
                        "required": True,
                    }
                ],
            }
        ],
        "missing_fields": [],
        "plan_hash": HASH_D,
        "created_at": "2026-07-15T02:00:00Z",
        "extensions": {},
    }
    plan["plan_hash"] = compute_plan_hash(plan)
    return plan


def optimizer_plan_graph(
    *,
    optimizer: str = "adam",
    learning_rate_milli: int = 10_000,
    algorithm_version: str = CURRENT_OPTIMIZER_ALGORITHM_VERSION,
) -> dict:
    plan = plan_graph()
    plan["schema_version"] = "1.1.0"
    plan["nodes"][0]["algorithm"]["version"] = algorithm_version
    plan["nodes"][0]["parameters"].update(
        optimizer=optimizer, learning_rate_milli=learning_rate_milli
    )
    if algorithm_version in {"1.4.0", "1.5.0"}:
        plan["nodes"][0]["outputs"] = copy.deepcopy(
            load_deepwave_manifest(algorithm_version)["outputs"]
        )
    plan["plan_hash"] = compute_plan_hash(plan)
    return plan


def approval_decision(plan: dict | None = None) -> dict:
    current_plan = plan or plan_graph()
    dataset = dataset_ref()
    return {
        "schema_version": "1.0.0",
        "approval_id": "approval-001",
        "plan_id": current_plan["plan_id"],
        "plan_hash": current_plan["plan_hash"],
        "decision": "approved",
        "actor": {"type": "user", "id": "user-1"},
        "scope": {
            "datasets": [
                {
                    key: dataset[key]
                    for key in ("id", "version", "content_hash", "data_type")
                }
            ],
            "algorithms": [
                {"id": "deepwave.acoustic_fwi", "version": ALGORITHM_VERSION}
            ],
            "resource_limits": resources(),
            "side_effects": ["compute", "write_artifacts"],
            "max_tasks": 1,
        },
        "decided_at": "2026-07-15T02:01:00Z",
        "expires_at": "2026-07-15T03:01:00Z",
        "extensions": {},
    }


def retry_approval_decision(plan: dict | None = None) -> dict:
    value = approval_decision(plan)
    value["schema_version"] = "1.1.0"
    wall_time = value["scope"]["resource_limits"]["wall_time_seconds"]
    value["scope"]["retry_policy"] = {
        "max_attempts": 2,
        "max_concurrent_attempts": 1,
        "max_cumulative_attempt_wall_time_seconds": 2 * wall_time,
        "retryable_failure_classes": [
            "pre_running_launch_failure",
            "worker_exit",
        ],
    }
    return value


def fingerprint(*, dirty: bool = False) -> dict:
    source = {
        "identity_complete": True,
        "git_commit": "1" * 40,
        "git_tree": "2" * 40,
        "dirty": dirty,
    }
    if dirty:
        source["diff_hash"] = HASH_C
    return {
        "provenance_mode": "reproducible",
        "algorithm": {"id": "deepwave.acoustic_fwi", "version": ALGORITHM_VERSION},
        "adapter_version": "1.1.0",
        "source": source,
        "environment": {"environment_lock_hash": HASH_B},
        "runtime": {
            "python": "3.10.12",
            "pytorch": "2.x",
            "deepwave": "0.x",
            "cuda": "12.x",
        },
        "seed": 2026,
        "hardware": {
            "device": "cuda",
            "device_name": "test-gpu",
            "compute_capability": "test-only",
        },
        "normalized_config_hash": HASH_D,
        "input_hashes": [HASH_A],
        "determinism": {
            "requested": True,
            "framework_deterministic": True,
            "flags": {"torch_deterministic_algorithms": True},
            "known_nondeterminism": ["Bitwise equality across library or GPU versions is not promised."],
        },
    }


def run_event() -> dict:
    return {
        "schema_version": "1.0.0",
        "event_id": "event-001",
        "sequence": 1,
        "task_id": "task-001",
        "node_id": "invert",
        "event_type": "node_started",
        "task_status": "Running",
        "occurred_at": "2026-07-15T02:02:00Z",
        "fingerprint": fingerprint(),
        "extensions": {},
    }


def artifact_manifest(plan: dict | None = None) -> dict:
    current_plan = plan or plan_graph()
    dataset = dataset_ref()
    return {
        "schema_version": "1.0.0",
        "artifact_id": "artifact-001",
        "task_id": "task-001",
        "node_id": "invert",
        "artifact_type": "metrics",
        "media_type": "application/json",
        "location": {"relative_path": "artifacts/metrics.json"},
        "content_hash": HASH_B,
        "size_bytes": 1024,
        "created_at": "2026-07-15T02:03:00Z",
        "metrics": {"initial_loss": 1, "final_loss": 0.5},
        "display": {"component": "metric_table", "title": "FWI metrics", "order": 0},
        "fingerprint": fingerprint(),
        "lineage": {
            "plan_hash": current_plan["plan_hash"],
            "algorithm": {
                "id": "deepwave.acoustic_fwi",
                "version": ALGORITHM_VERSION,
            },
            "inputs": [
                {
                    key: dataset[key]
                    for key in ("id", "version", "content_hash", "data_type")
                }
            ],
        },
        "extensions": {},
    }


def registry_inputs() -> tuple[dict, dict]:
    dataset = dataset_ref()
    manifest = algorithm_manifest()
    return (
        {(dataset["id"], dataset["version"]): dataset},
        {(manifest["id"], manifest["version"]): manifest},
    )


def rehash(plan: dict, approval: dict | None = None) -> None:
    plan["plan_hash"] = compute_plan_hash(plan)
    if approval is not None:
        approval["plan_id"] = plan["plan_id"]
        approval["plan_hash"] = plan["plan_hash"]


def append_second_plan_node(plan: dict) -> dict:
    node = copy.deepcopy(plan["nodes"][0])
    node["node_id"] = "invert-second"
    node["idempotency_key"] = "task-001:invert-second:0001"
    plan["nodes"].append(node)
    return node


class ScientificRuntimeSchemaTest(unittest.TestCase):
    def test_all_public_schemas_are_valid_draft_07(self) -> None:
        for name in (
            "common.schema.json",
            "dataset-ref.schema.json",
            "algorithm-manifest.schema.json",
            "task-draft.schema.json",
            "plan-graph.schema.json",
            "approval-decision.schema.json",
            "run-event.schema.json",
            "artifact-manifest.schema.json",
        ):
            with self.subTest(name=name):
                Draft7Validator.check_schema(load_schema(name))

    def test_seven_minimal_fwi_contracts_validate(self) -> None:
        plan = plan_graph()
        cases = {
            "dataset-ref.schema.json": dataset_ref(),
            "algorithm-manifest.schema.json": algorithm_manifest(),
            "task-draft.schema.json": task_draft(),
            "plan-graph.schema.json": plan,
            "approval-decision.schema.json": approval_decision(plan),
            "run-event.schema.json": run_event(),
            "artifact-manifest.schema.json": artifact_manifest(plan),
        }
        for name, value in cases.items():
            with self.subTest(name=name):
                self.assertEqual(schema_errors(name, value), [])

        legacy_manifest = algorithm_manifest()
        legacy_manifest["version"] = "1.0.0"
        legacy_manifest["parameter_schema"]["properties"]["iterations"][
            "maximum"
        ] = 100
        legacy_manifest["adapter"]["version"] = "1.0.0"
        self.assertEqual(
            schema_errors("algorithm-manifest.schema.json", legacy_manifest), []
        )

    def test_missing_and_unknown_top_level_fields_are_rejected(self) -> None:
        missing = dataset_ref()
        missing.pop("content_hash")
        self.assertTrue(schema_errors("dataset-ref.schema.json", missing))

        unknown = dataset_ref()
        unknown["server_path"] = "/root/fwi-data/models/model.npy"
        errors = schema_errors("dataset-ref.schema.json", unknown)
        self.assertTrue(any("Additional properties" in error for error in errors))

        manifest = algorithm_manifest()
        manifest["parameter_schema"]["properties"]["preset"]["$ref"] = "file:///etc/passwd"
        self.assertTrue(schema_errors("algorithm-manifest.schema.json", manifest))

        manifest = algorithm_manifest()
        manifest["adapter"]["idempotent_submit"] = False
        self.assertTrue(schema_errors("algorithm-manifest.schema.json", manifest))

    def test_resources_and_fwi_parameters_remain_bounded(self) -> None:
        draft = task_draft()
        draft["resources"]["gpu_count"] = 2
        self.assertTrue(schema_errors("task-draft.schema.json", draft))

        draft = task_draft()
        draft["parameters"]["iterations"] = 10000
        self.assertEqual(schema_errors("task-draft.schema.json", draft), [])

        draft = task_draft()
        draft["parameters"]["iterations"] = 10001
        self.assertTrue(schema_errors("task-draft.schema.json", draft))

        draft = task_draft()
        draft["parameters"]["iterations"] = 2.5
        self.assertTrue(schema_errors("task-draft.schema.json", draft))

    def test_optimizer_contract_minor_is_strict_and_legacy_remains_valid(self) -> None:
        current_manifest = load_deepwave_manifest(
            CURRENT_OPTIMIZER_ALGORITHM_VERSION
        )
        current_draft = optimizer_task_draft()
        current_plan = optimizer_plan_graph()
        for name, value in (
            ("algorithm-manifest.schema.json", current_manifest),
            ("task-draft.schema.json", current_draft),
            ("plan-graph.schema.json", current_plan),
        ):
            with self.subTest(name=name):
                self.assertEqual(schema_errors(name, value), [])

        legacy_optimizer_manifest = load_deepwave_manifest(
            LEGACY_OPTIMIZER_ALGORITHM_VERSION
        )
        legacy_optimizer_draft = optimizer_task_draft(
            algorithm_version=LEGACY_OPTIMIZER_ALGORITHM_VERSION
        )
        legacy_optimizer_plan = optimizer_plan_graph(
            algorithm_version=LEGACY_OPTIMIZER_ALGORITHM_VERSION
        )
        for name, value in (
            ("algorithm-manifest.schema.json", legacy_optimizer_manifest),
            ("task-draft.schema.json", legacy_optimizer_draft),
            ("plan-graph.schema.json", legacy_optimizer_plan),
        ):
            with self.subTest(name=f"legacy-1.2:{name}"):
                self.assertEqual(schema_errors(name, value), [])

        self.assertEqual(
            legacy_optimizer_manifest["parameter_schema"]["properties"]
            ["iterations"]["minimum"],
            0,
        )
        self.assertNotIn(
            "maximum",
            legacy_optimizer_manifest["parameter_schema"]["properties"]["seed"],
        )
        self.assertEqual(
            current_manifest["parameter_schema"]["properties"]["iterations"]
            ["minimum"],
            1,
        )
        self.assertEqual(
            current_manifest["parameter_schema"]["properties"]["seed"]["maximum"],
            2147483647,
        )

        legacy_with_new_parameters = task_draft()
        legacy_with_new_parameters["parameters"].update(
            optimizer="adam", learning_rate_milli=10_000
        )
        self.assertTrue(
            schema_errors("task-draft.schema.json", legacy_with_new_parameters)
        )

        current_without_optimizer = optimizer_task_draft()
        current_without_optimizer["parameters"].pop("optimizer")
        self.assertTrue(schema_errors("task-draft.schema.json", current_without_optimizer))

        current_without_conditional_bounds = copy.deepcopy(current_manifest)
        current_without_conditional_bounds["parameter_schema"].pop("allOf")
        self.assertTrue(
            schema_errors(
                "algorithm-manifest.schema.json", current_without_conditional_bounds
            )
        )

        current_with_zero_iterations = copy.deepcopy(current_manifest)
        current_with_zero_iterations["parameter_schema"]["properties"]["iterations"][
            "minimum"
        ] = 0
        self.assertTrue(
            schema_errors("algorithm-manifest.schema.json", current_with_zero_iterations)
        )

        current_without_seed_maximum = copy.deepcopy(current_manifest)
        current_without_seed_maximum["parameter_schema"]["properties"]["seed"].pop(
            "maximum"
        )
        self.assertTrue(
            schema_errors("algorithm-manifest.schema.json", current_without_seed_maximum)
        )

        current_advertising_forward = copy.deepcopy(current_manifest)
        current_advertising_forward["task_types"].insert(0, "acoustic_forward_2d")
        current_advertising_forward["parameter_schema"]["properties"]["preset"][
            "enum"
        ].insert(0, "forward")
        self.assertTrue(
            schema_errors("algorithm-manifest.schema.json", current_advertising_forward)
        )

        legacy_optimizer_with_current_bounds = copy.deepcopy(
            legacy_optimizer_manifest
        )
        legacy_optimizer_with_current_bounds["parameter_schema"]["properties"][
            "iterations"
        ]["minimum"] = 1
        legacy_optimizer_with_current_bounds["parameter_schema"]["properties"][
            "seed"
        ]["maximum"] = 2147483647
        self.assertTrue(
            schema_errors(
                "algorithm-manifest.schema.json",
                legacy_optimizer_with_current_bounds,
            )
        )

        legacy_with_optimizer_conditions = algorithm_manifest()
        legacy_with_optimizer_conditions["parameter_schema"]["allOf"] = copy.deepcopy(
            current_manifest["parameter_schema"]["allOf"]
        )
        self.assertTrue(
            schema_errors(
                "algorithm-manifest.schema.json", legacy_with_optimizer_conditions
            )
        )

        legacy_with_current_fwi_bounds = algorithm_manifest()
        legacy_with_current_fwi_bounds["parameter_schema"]["properties"]["iterations"][
            "minimum"
        ] = 1
        legacy_with_current_fwi_bounds["parameter_schema"]["properties"]["seed"][
            "maximum"
        ] = 2147483647
        self.assertTrue(
            schema_errors(
                "algorithm-manifest.schema.json", legacy_with_current_fwi_bounds
            )
        )

    def test_optimizer_learning_rate_uses_integer_fixed_point_and_profile_bounds(self) -> None:
        for optimizer, lower, upper in (
            ("adam", 100, 100_000),
            ("sgd", 100_000_000, 1_000_000_000_000),
        ):
            for learning_rate_milli in (lower, upper):
                with self.subTest(
                    optimizer=optimizer, learning_rate_milli=learning_rate_milli
                ):
                    self.assertEqual(
                        schema_errors(
                            "task-draft.schema.json",
                            optimizer_task_draft(
                                optimizer=optimizer,
                                learning_rate_milli=learning_rate_milli,
                            ),
                        ),
                        [],
                    )
            for learning_rate_milli in (lower - 1, upper + 1):
                self.assertTrue(
                    schema_errors(
                        "task-draft.schema.json",
                        optimizer_task_draft(
                            optimizer=optimizer,
                            learning_rate_milli=learning_rate_milli,
                        ),
                    )
                )

        # Draft-07 treats a mathematically integral JSON number such as
        # 10000.0 as an integer; the hash boundary separately rejects every
        # Python float.  Here the schema rejects booleans, strings and a
        # genuinely fractional value.
        for invalid in (True, "10000", 10_000.5):
            with self.subTest(invalid=invalid):
                self.assertTrue(
                    schema_errors(
                        "task-draft.schema.json",
                        optimizer_task_draft(learning_rate_milli=invalid),
                    )
                )

    def test_arbitrary_artifact_paths_are_rejected(self) -> None:
        artifact = artifact_manifest()
        artifact["location"] = {"relative_path": "../private/model.npy"}
        self.assertTrue(schema_errors("artifact-manifest.schema.json", artifact))

        artifact["location"] = {"relative_path": "/root/fwi-runs/job/metrics.json"}
        self.assertTrue(schema_errors("artifact-manifest.schema.json", artifact))

    def test_dirty_provenance_requires_exact_diff_or_source_archive_hash(self) -> None:
        event = run_event()
        event["fingerprint"]["source"]["dirty"] = True
        errors = schema_errors("run-event.schema.json", event)
        self.assertTrue(errors)

        event["fingerprint"] = fingerprint(dirty=True)
        self.assertEqual(schema_errors("run-event.schema.json", event), [])

        event["fingerprint"] = fingerprint()
        event["fingerprint"]["provenance_mode"] = "development"
        event["fingerprint"]["source"] = {
            "identity_complete": False,
            "dirty": None,
        }
        self.assertEqual(schema_errors("run-event.schema.json", event), [])

    def test_extensions_require_an_explicit_namespace(self) -> None:
        draft = task_draft()
        draft["extensions"] = {"vendor": {"flag": True}}
        self.assertTrue(schema_errors("task-draft.schema.json", draft))
        draft["extensions"] = {"org.example": {"flag": True}}
        self.assertEqual(schema_errors("task-draft.schema.json", draft), [])

    def test_agent_delegation_requires_revocable_session_budget(self) -> None:
        approval = approval_decision()
        approval["delegation"] = {
            "session_id": "session-1",
            "revocable": True,
            "budget_id": "budget-1",
        }
        self.assertTrue(schema_errors("approval-decision.schema.json", approval))

        approval = approval_decision()
        approval["actor"] = {"type": "agent_delegation", "id": "agent-1"}
        self.assertTrue(schema_errors("approval-decision.schema.json", approval))
        approval["delegation"] = {
            "session_id": "session-1",
            "revocable": True,
            "budget_id": "budget-1",
        }
        self.assertEqual(schema_errors("approval-decision.schema.json", approval), [])

    def test_approval_v1_1_requires_exact_finite_retry_shape(self) -> None:
        approval = retry_approval_decision()
        self.assertEqual(
            schema_errors("approval-decision.schema.json", approval), []
        )

        missing = copy.deepcopy(approval)
        missing["scope"].pop("retry_policy")
        self.assertTrue(schema_errors("approval-decision.schema.json", missing))

        legacy_with_retry = copy.deepcopy(approval)
        legacy_with_retry["schema_version"] = "1.0.0"
        self.assertTrue(
            schema_errors("approval-decision.schema.json", legacy_with_retry)
        )

        for field, invalid in (
            ("max_attempts", 3),
            ("max_concurrent_attempts", 2),
            ("retryable_failure_classes", ["worker_exit", "worker_exit"]),
        ):
            with self.subTest(field=field):
                malformed = copy.deepcopy(approval)
                malformed["scope"]["retry_policy"][field] = invalid
                self.assertTrue(
                    schema_errors("approval-decision.schema.json", malformed)
                )


class ScientificRuntimeCanonicalizationTest(unittest.TestCase):
    def test_plan_hash_is_independent_of_object_key_order_and_hash_field(self) -> None:
        plan = plan_graph()
        reordered = dict(reversed(list(plan.items())))
        self.assertEqual(compute_plan_hash(plan), compute_plan_hash(reordered))
        reordered["plan_hash"] = HASH_A
        self.assertEqual(compute_plan_hash(plan), compute_plan_hash(reordered))

    def test_unicode_is_nfc_normalized(self) -> None:
        composed = canonical_json_bytes({"goal": "caf\u00e9"})
        decomposed = canonical_json_bytes({"goal": "cafe\u0301"})
        self.assertEqual(composed, decomposed)

    def test_hash_bound_v1_plan_rejects_floating_point_values(self) -> None:
        plan = plan_graph()
        plan["extensions"] = {"org.example": {"threshold": 0.5}}
        with self.assertRaisesRegex(ValueError, "floating-point JSON"):
            compute_plan_hash(plan)

    def test_optimizer_fixed_point_is_hash_bound_without_json_floats(self) -> None:
        plan = optimizer_plan_graph(learning_rate_milli=2_000)
        original_hash = compute_plan_hash(plan)
        self.assertEqual(plan["plan_hash"], original_hash)

        plan["nodes"][0]["parameters"]["learning_rate_milli"] = 2_001
        self.assertNotEqual(compute_plan_hash(plan), original_hash)


class ScientificRuntimeExecutionGateTest(unittest.TestCase):
    NOW = datetime(2026, 7, 15, 2, 30, tzinfo=timezone.utc)

    def evaluate(
        self,
        *,
        draft: dict | None = None,
        plan: dict | None = None,
        approval: dict | None = None,
        datasets: dict | None = None,
        algorithms: dict | None = None,
        principal_id: str = "user-1",
        project_id: str = "project-1",
        approval_tasks_used: int = 0,
    ):
        current_plan = plan or plan_graph()
        current_approval = approval or approval_decision(current_plan)
        default_datasets, default_algorithms = registry_inputs()
        return evaluate_execution_gate(
            draft=draft or task_draft(),
            plan=current_plan,
            approval=current_approval,
            dataset_registry=datasets if datasets is not None else default_datasets,
            algorithm_registry=algorithms if algorithms is not None else default_algorithms,
            principal_id=principal_id,
            project_id=project_id,
            approval_tasks_used=approval_tasks_used,
            now=self.NOW,
        )

    def assert_has_code(self, violations, code: str) -> None:
        self.assertIn(code, {violation.code for violation in violations})

    def test_valid_guided_fwi_plan_opens_the_gate(self) -> None:
        self.assertEqual(self.evaluate(), [])

    def test_claimed_fixed_recipe_requires_exact_versioned_graph(self) -> None:
        extension = {"id": "forward_qc_fwi", "version": "1.0.0"}
        draft = optimizer_task_draft(algorithm_version="1.6.0")
        draft["extensions"] = {"org.agent_rpc.recipe": copy.deepcopy(extension)}
        plan = optimizer_plan_graph(algorithm_version="1.6.0")
        plan["schema_version"] = "1.2.0"
        plan["extensions"] = {"org.agent_rpc.recipe": copy.deepcopy(extension)}
        template = plan["nodes"][0]
        template["outputs"] = copy.deepcopy(load_deepwave_manifest()["outputs"])
        plan["nodes"] = []
        for node_id, dependencies in (
            ("data_check", []),
            ("forward", ["data_check"]),
            ("quality_check", ["data_check"]),
            ("fwi", ["forward", "quality_check"]),
            ("result_check", ["fwi"]),
        ):
            node = copy.deepcopy(template)
            node["node_id"] = node_id
            node["dependencies"] = dependencies
            node["idempotency_key"] = f"task-001:{node_id}:0001"
            node["inputs"] = fixed_recipe_plan_inputs(
                node_id, template["inputs"][0]["dataset"]
            )
            plan["nodes"].append(node)
        rehash(plan)
        approval = approval_decision(plan)
        approval["scope"]["algorithms"] = [
            {"id": "deepwave.acoustic_fwi", "version": "1.6.0"}
        ]
        datasets, _ = registry_inputs()
        manifest = load_deepwave_manifest()
        algorithms = {(manifest["id"], manifest["version"]): manifest}
        self.assertEqual(
            self.evaluate(
                draft=draft,
                plan=plan,
                approval=approval,
                datasets=datasets,
                algorithms=algorithms,
            ),
            [],
        )

        forged = copy.deepcopy(plan)
        forged["extensions"]["org.agent_rpc.recipe"]["version"] = "9.9.9"
        rehash(forged)
        forged_approval = copy.deepcopy(approval)
        rehash(forged, forged_approval)
        self.assert_has_code(
            self.evaluate(
                draft=draft,
                plan=forged,
                approval=forged_approval,
                datasets=datasets,
                algorithms=algorithms,
            ),
            "FIXED_RECIPE_INVALID",
        )

        missing_edge = copy.deepcopy(plan)
        missing_edge["nodes"][3]["inputs"].pop()
        rehash(missing_edge)
        missing_edge_approval = copy.deepcopy(approval)
        rehash(missing_edge, missing_edge_approval)
        self.assert_has_code(
            self.evaluate(
                draft=draft,
                plan=missing_edge,
                approval=missing_edge_approval,
                datasets=datasets,
                algorithms=algorithms,
            ),
            "FIXED_RECIPE_PLAN_INVALID",
        )

    def test_unregistered_or_unpinned_algorithm_is_rejected(self) -> None:
        self.assert_has_code(self.evaluate(algorithms={}), "ALGORITHM_NOT_REGISTERED")

        plan = plan_graph()
        plan["nodes"][0]["algorithm"].pop("version")
        self.assert_has_code(self.evaluate(plan=plan), "SCHEMA_INVALID")

    def test_registered_but_non_allowlisted_algorithm_is_rejected(self) -> None:
        _, algorithms = registry_inputs()
        manifest = next(iter(algorithms.values()))
        manifest["security"]["allowlisted"] = False
        self.assert_has_code(self.evaluate(algorithms=algorithms), "ALGORITHM_NOT_ALLOWLISTED")

    def test_dataset_hash_and_access_scope_are_deterministic_gates(self) -> None:
        datasets, _ = registry_inputs()
        registered = next(iter(datasets.values()))
        registered["content_hash"] = HASH_B
        self.assert_has_code(self.evaluate(datasets=datasets), "DATASET_HASH_MISMATCH")

        self.assert_has_code(self.evaluate(principal_id="user-2"), "DATASET_ACCESS_DENIED")
        self.assert_has_code(self.evaluate(project_id="project-2"), "DATASET_ACCESS_DENIED")

        draft = task_draft()
        draft["datasets"][0]["metadata"]["value_range"]["maximum"] = 5400
        self.assert_has_code(self.evaluate(draft=draft), "DATASET_METADATA_MISMATCH")

    def test_io_type_mismatch_is_rejected(self) -> None:
        _, algorithms = registry_inputs()
        next(iter(algorithms.values()))["inputs"][0]["data_type"] = "shot_gather_2d"
        self.assert_has_code(
            self.evaluate(algorithms=algorithms),
            "INPUT_TYPE_MISMATCH",
        )

    def test_plan_ports_must_match_the_registered_manifest_exactly(self) -> None:
        plan = plan_graph()
        plan["nodes"][0]["inputs"][0]["port"] = "unknown_model"
        rehash(plan)
        self.assert_has_code(self.evaluate(plan=plan), "INPUT_PORT_MISMATCH")

        plan = plan_graph()
        plan["nodes"][0]["outputs"].pop()
        rehash(plan)
        self.assert_has_code(self.evaluate(plan=plan), "OUTPUT_PORT_MISMATCH")

        plan = plan_graph()
        plan["nodes"][0]["outputs"].append(
            copy.deepcopy(plan["nodes"][0]["outputs"][0])
        )
        rehash(plan)
        self.assert_has_code(self.evaluate(plan=plan), "OUTPUT_PORT_MISMATCH")

    def test_plan_algorithm_and_device_cannot_drift_from_the_draft(self) -> None:
        draft = task_draft()
        draft["algorithm"] = {"id": "deepwave.other", "version": "1.0.0"}
        self.assert_has_code(self.evaluate(draft=draft), "ALGORITHM_OUTSIDE_DRAFT")

        plan = plan_graph()
        plan["nodes"][0]["parameters"]["device"] = "cpu"
        approval = approval_decision(plan)
        rehash(plan, approval)
        self.assert_has_code(
            self.evaluate(plan=plan, approval=approval),
            "PARAMETER_RESOURCE_MISMATCH",
        )

        draft = task_draft()
        draft["task_type"] = "acoustic_forward_2d"
        plan = plan_graph()
        plan["task_type"] = "acoustic_forward_2d"
        approval = approval_decision(plan)
        rehash(plan, approval)
        self.assert_has_code(
            self.evaluate(draft=draft, plan=plan, approval=approval),
            "TASK_PARAMETER_MISMATCH",
        )

    def test_cyclic_dag_is_rejected(self) -> None:
        plan = plan_graph()
        second = copy.deepcopy(plan["nodes"][0])
        second["node_id"] = "quality_check"
        second["idempotency_key"] = "task-001:quality_check:0001"
        second["dependencies"] = ["invert"]
        plan["nodes"][0]["dependencies"] = ["quality_check"]
        plan["nodes"].append(second)
        approval = approval_decision(plan)
        rehash(plan, approval)
        self.assert_has_code(self.evaluate(plan=plan, approval=approval), "CYCLIC_DAG")

    def test_unresolved_fields_block_execution(self) -> None:
        draft = task_draft()
        draft["status"] = "NeedsInput"
        draft["missing_fields"] = ["parameters.iterations"]
        self.assert_has_code(self.evaluate(draft=draft), "UNRESOLVED_FIELDS")

    def test_draft_must_be_awaiting_approval_before_execution(self) -> None:
        draft = task_draft()
        draft["status"] = "Draft"
        self.assertEqual(
            [(violation.code, violation.path) for violation in self.evaluate(draft=draft)],
            [("DRAFT_NOT_AWAITING_APPROVAL", "/draft/status")],
        )

    def test_plan_task_type_cannot_drift_from_the_draft(self) -> None:
        draft = task_draft()
        draft["task_type"] = "acoustic_forward_2d"
        self.assertEqual(
            [(violation.code, violation.path) for violation in self.evaluate(draft=draft)],
            [("TASK_TYPE_OUTSIDE_DRAFT", "/plan/task_type")],
        )

    def test_each_plan_node_parameters_must_match_the_draft(self) -> None:
        plan = plan_graph()
        second = append_second_plan_node(plan)
        second["parameters"]["iterations"] = 3
        approval = approval_decision(plan)
        rehash(plan, approval)
        self.assertEqual(
            [
                (violation.code, violation.path)
                for violation in self.evaluate(plan=plan, approval=approval)
            ],
            [("PARAMETERS_OUTSIDE_DRAFT", "/plan/nodes/1/parameters")],
        )

    def test_each_plan_node_resources_must_match_the_draft(self) -> None:
        plan = plan_graph()
        second = append_second_plan_node(plan)
        second["resources"]["cpu_cores"] = 5
        approval = approval_decision(plan)
        approval["scope"]["resource_limits"]["cpu_cores"] = 5
        rehash(plan, approval)
        self.assertEqual(
            [
                (violation.code, violation.path)
                for violation in self.evaluate(plan=plan, approval=approval)
            ],
            [("RESOURCES_OUTSIDE_DRAFT", "/plan/nodes/1/resources")],
        )

    def test_malformed_and_duplicate_idempotency_keys_are_rejected(self) -> None:
        plan = plan_graph()
        plan["nodes"][0]["idempotency_key"] = "short"
        self.assert_has_code(self.evaluate(plan=plan), "SCHEMA_INVALID")

        plan = plan_graph()
        second = copy.deepcopy(plan["nodes"][0])
        second["node_id"] = "quality_check"
        plan["nodes"].append(second)
        approval = approval_decision(plan)
        rehash(plan, approval)
        self.assert_has_code(
            self.evaluate(plan=plan, approval=approval),
            "DUPLICATE_IDEMPOTENCY_KEY",
        )

    def test_side_effect_policy_and_resource_limits_are_enforced(self) -> None:
        _, algorithms = registry_inputs()
        manifest = next(iter(algorithms.values()))
        manifest["security"]["side_effects"] = ["compute"]
        self.assert_has_code(
            self.evaluate(algorithms=algorithms),
            "SIDE_EFFECT_UNDECLARED",
        )

        plan = plan_graph()
        plan["nodes"][0]["resources"]["wall_time_seconds"] = 7201
        approval = approval_decision(plan)
        approval["scope"]["resource_limits"]["wall_time_seconds"] = 7201
        rehash(plan, approval)
        self.assert_has_code(
            self.evaluate(plan=plan, approval=approval),
            "RESOURCE_EXCEEDS_ALGORITHM_LIMIT",
        )

    def test_expired_or_hash_mismatched_approval_is_rejected(self) -> None:
        plan = plan_graph()
        approval = approval_decision(plan)
        approval["expires_at"] = "2026-07-15T02:20:00Z"
        self.assert_has_code(
            self.evaluate(plan=plan, approval=approval),
            "APPROVAL_EXPIRED",
        )

        approval = approval_decision(plan)
        approval["plan_hash"] = HASH_A
        self.assert_has_code(
            self.evaluate(plan=plan, approval=approval),
            "APPROVAL_HASH_MISMATCH",
        )

        approval = approval_decision(plan)
        approval["expires_at"] = approval["decided_at"]
        self.assert_has_code(
            self.evaluate(plan=plan, approval=approval),
            "APPROVAL_WINDOW_INVALID",
        )

        approval = approval_decision(plan)
        approval["actor"]["id"] = "user-2"
        self.assert_has_code(
            self.evaluate(plan=plan, approval=approval),
            "APPROVAL_ACTOR_MISMATCH",
        )

        self.assert_has_code(
            self.evaluate(plan=plan, approval_tasks_used=1),
            "APPROVAL_TASK_BUDGET_EXHAUSTED",
        )

        approval = approval_decision(plan)
        approval["decided_at"] = "2026-07-15T01:59:00Z"
        self.assert_has_code(
            self.evaluate(plan=plan, approval=approval),
            "APPROVAL_PREDATES_PLAN",
        )

        approval = approval_decision(plan)
        approval["actor"] = {"type": "agent_delegation", "id": "agent-1"}
        approval["delegation"] = {
            "session_id": "session-1",
            "revocable": True,
            "budget_id": "budget-1",
        }
        self.assert_has_code(
            self.evaluate(plan=plan, approval=approval),
            "DELEGATED_APPROVAL_UNSUPPORTED",
        )

    def test_mutating_approved_plan_invalidates_its_derived_hash(self) -> None:
        plan = plan_graph()
        approval = approval_decision(plan)
        plan["nodes"][0]["parameters"]["iterations"] = 3
        self.assert_has_code(
            self.evaluate(plan=plan, approval=approval),
            "PLAN_HASH_INVALID",
        )

        plan = plan_graph()
        plan["extensions"] = {"org.example": {"threshold": 0.5}}
        approval = approval_decision(plan)
        self.assert_has_code(
            self.evaluate(plan=plan, approval=approval),
            "PLAN_CANONICALIZATION_INVALID",
        )


if __name__ == "__main__":
    unittest.main()
