"""P0 contract validation and deterministic execution-gate reference logic.

This module is deliberately storage- and scheduler-free.  It makes the P0
contract rules executable without creating the P1 TaskService prematurely.
"""

from __future__ import annotations

import copy
import hashlib
import json
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft7Validator, FormatChecker, RefResolver


CONTRACT_DIR = (
    Path(__file__).resolve().parents[1] / "contracts" / "scientific_runtime" / "v1"
)


@dataclass(frozen=True, order=True)
class GateViolation:
    """One deterministic reason why a plan cannot enter Queued."""

    code: str
    path: str
    message: str


class PlanDataEdgeError(ValueError):
    """A PlanGraph node-output binding is ambiguous or inconsistent."""

    def __init__(self, errors: Sequence[str]):
        self.errors = tuple(sorted(set(errors)))
        super().__init__("; ".join(self.errors))


@dataclass(frozen=True, order=True)
class PlanDataEdge:
    """One typed, plan-hash-bound upstream output to downstream input edge."""

    target_node_id: str
    target_input_port: str
    source_node_id: str
    source_output_port: str
    data_type: str


def _schema_documents() -> dict[str, dict[str, Any]]:
    documents: dict[str, dict[str, Any]] = {}
    for path in sorted(CONTRACT_DIR.glob("*.schema.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        documents[path.name] = value
        documents[path.resolve().as_uri()] = value
        schema_id = value.get("$id")
        if schema_id:
            documents[schema_id] = value
    return documents


def load_schema(name: str) -> dict[str, Any]:
    """Load one public schema by filename, returning an independent object."""

    path = CONTRACT_DIR / name
    if not path.is_file() or path.parent != CONTRACT_DIR:
        raise ValueError(f"unknown scientific runtime schema: {name}")
    return json.loads(path.read_text(encoding="utf-8"))


def _validator(name: str) -> Draft7Validator:
    schema = load_schema(name)
    Draft7Validator.check_schema(schema)
    resolver = RefResolver.from_schema(schema, store=_schema_documents())
    return Draft7Validator(
        schema,
        resolver=resolver,
        format_checker=FormatChecker(),
    )


def schema_errors(name: str, instance: Mapping[str, Any]) -> list[str]:
    """Return stable, human-readable Draft-07 validation errors."""

    errors = sorted(
        _validator(name).iter_errors(instance),
        key=lambda error: (list(error.absolute_path), error.message),
    )
    rendered: list[str] = []
    for error in errors:
        path = "/" + "/".join(str(part) for part in error.absolute_path)
        rendered.append(f"{path}: {error.message}")
    return rendered


def _canonical_value(value: Any, path: str = "$") -> Any:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        raise ValueError(
            f"floating-point JSON is not permitted in hash-bound v1 plans at {path}"
        )
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, list):
        return [
            _canonical_value(item, f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"non-string JSON key at {path}")
            normalized_key = unicodedata.normalize("NFC", key)
            if normalized_key in normalized:
                raise ValueError(f"duplicate NFC-normalized key at {path}")
            normalized[normalized_key] = _canonical_value(
                item, f"{path}.{normalized_key}"
            )
        return normalized
    raise ValueError(f"unsupported JSON value at {path}: {type(value).__name__}")


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    """Apply the scientific-runtime-v1 canonical JSON profile."""

    normalized = _canonical_value(value)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def compute_plan_hash(plan: Mapping[str, Any]) -> str:
    """Hash every PlanGraph field except its derived plan_hash member."""

    hash_input = copy.deepcopy(dict(plan))
    hash_input.pop("plan_hash", None)
    digest = hashlib.sha256(canonical_json_bytes(hash_input)).hexdigest()
    return f"sha256:{digest}"


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include an offset")
    return parsed.astimezone(timezone.utc)


def _contains_identity(
    values: Sequence[Mapping[str, Any]],
    expected: Mapping[str, Any],
    fields: Sequence[str],
) -> bool:
    return any(all(value.get(field) == expected.get(field) for field in fields) for value in values)


def _dag_errors(nodes: Sequence[Mapping[str, Any]]) -> list[GateViolation]:
    violations: list[GateViolation] = []
    ids = [str(node.get("node_id", "")) for node in nodes]
    if len(ids) != len(set(ids)):
        violations.append(
            GateViolation("DUPLICATE_NODE_ID", "/nodes", "node_id values must be unique")
        )
        return violations

    known = set(ids)
    edges: dict[str, list[str]] = {}
    for index, node in enumerate(nodes):
        node_id = str(node.get("node_id", ""))
        dependencies = [str(value) for value in node.get("dependencies", [])]
        edges[node_id] = dependencies
        for dependency in dependencies:
            if dependency not in known:
                violations.append(
                    GateViolation(
                        "UNKNOWN_DEPENDENCY",
                        f"/nodes/{index}/dependencies",
                        f"dependency {dependency!r} does not name a plan node",
                    )
                )

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> bool:
        if node_id in visiting:
            return True
        if node_id in visited:
            return False
        visiting.add(node_id)
        for dependency in edges.get(node_id, []):
            if dependency in known and visit(dependency):
                return True
        visiting.remove(node_id)
        visited.add(node_id)
        return False

    if any(visit(node_id) for node_id in ids if node_id not in visited):
        violations.append(
            GateViolation("CYCLIC_DAG", "/nodes", "plan dependencies must be acyclic")
        )
    return violations


def extract_plan_data_edges(plan: Mapping[str, Any]) -> tuple[PlanDataEdge, ...]:
    """Return canonical typed data edges declared by a PlanGraph.

    JSON Schema validates each binding's local shape.  This cross-document
    check proves that every node-output source is a direct dependency, names
    exactly one declared upstream output port, and preserves its data type.
    Dataset-bound inputs are intentionally omitted from the returned edges.
    """

    if not isinstance(plan, Mapping):
        raise PlanDataEdgeError(["plan must be an object"])
    nodes = plan.get("nodes")
    if not isinstance(nodes, list):
        raise PlanDataEdgeError(["/nodes must be an array"])

    errors: list[str] = []
    nodes_by_id: dict[str, Mapping[str, Any]] = {}
    for node_index, node in enumerate(nodes):
        if not isinstance(node, Mapping):
            errors.append(f"/nodes/{node_index} must be an object")
            continue
        node_id = node.get("node_id")
        if not isinstance(node_id, str) or not node_id:
            errors.append(f"/nodes/{node_index}/node_id is invalid")
            continue
        if node_id in nodes_by_id:
            errors.append(f"/nodes/{node_index}/node_id is duplicated")
            continue
        nodes_by_id[node_id] = node

    edges: list[PlanDataEdge] = []
    for node_index, node in enumerate(nodes):
        if not isinstance(node, Mapping):
            continue
        target_node_id = node.get("node_id")
        if not isinstance(target_node_id, str) or target_node_id not in nodes_by_id:
            continue
        dependencies = node.get("dependencies")
        dependency_ids = (
            {value for value in dependencies if isinstance(value, str)}
            if isinstance(dependencies, list)
            else set()
        )
        inputs = node.get("inputs")
        if not isinstance(inputs, list):
            errors.append(f"/nodes/{node_index}/inputs must be an array")
            continue
        seen_target_ports: set[str] = set()
        for input_index, binding in enumerate(inputs):
            if not isinstance(binding, Mapping):
                errors.append(
                    f"/nodes/{node_index}/inputs/{input_index} must be an object"
                )
                continue
            path = f"/nodes/{node_index}/inputs/{input_index}"
            target_port = binding.get("port")
            if not isinstance(target_port, str) or not target_port:
                errors.append(f"{path}/port is invalid")
                continue
            if target_port in seen_target_ports:
                errors.append(f"{path}/port is duplicated")
                continue
            seen_target_ports.add(target_port)
            source = binding.get("source")
            if source is None:
                continue
            if not isinstance(source, Mapping):
                errors.append(f"{path}/source must be an object")
                continue
            source_node_id = source.get("node_id")
            source_port = source.get("port")
            data_type = source.get("data_type")
            if not all(
                isinstance(value, str) and value
                for value in (source_node_id, source_port, data_type)
            ):
                errors.append(f"{path}/source identity is invalid")
                continue
            source_node = nodes_by_id.get(source_node_id)
            if source_node is None:
                errors.append(f"{path}/source/node_id does not name a plan node")
                continue
            if source_node_id not in dependency_ids:
                errors.append(
                    f"{path}/source/node_id must be a direct dependency"
                )
            outputs = source_node.get("outputs")
            matches = (
                [
                    output
                    for output in outputs
                    if isinstance(output, Mapping)
                    and output.get("port") == source_port
                ]
                if isinstance(outputs, list)
                else []
            )
            if len(matches) != 1:
                errors.append(
                    f"{path}/source/port must name exactly one upstream output"
                )
                continue
            if matches[0].get("data_type") != data_type:
                errors.append(
                    f"{path}/source/data_type differs from the upstream output"
                )
                continue
            edges.append(
                PlanDataEdge(
                    target_node_id=target_node_id,
                    target_input_port=target_port,
                    source_node_id=source_node_id,
                    source_output_port=source_port,
                    data_type=data_type,
                )
            )

    if errors:
        raise PlanDataEdgeError(errors)
    return tuple(sorted(edges))


def _resource_errors(
    resources: Mapping[str, Any],
    manifest_limits: Mapping[str, Any],
    approval_limits: Mapping[str, Any],
    path: str,
) -> list[GateViolation]:
    violations: list[GateViolation] = []
    if resources.get("device") not in manifest_limits.get("devices", []):
        violations.append(
            GateViolation(
                "RESOURCE_UNSUPPORTED",
                path + "/device",
                "requested device is not declared by the algorithm manifest",
            )
        )
    manifest_fields = {
        "gpu_count": "max_gpu_count",
        "cpu_cores": "max_cpu_cores",
        "memory_mb": "max_memory_mb",
        "wall_time_seconds": "max_wall_time_seconds",
    }
    for field, limit_field in manifest_fields.items():
        if resources.get(field, 0) > manifest_limits.get(limit_field, -1):
            violations.append(
                GateViolation(
                    "RESOURCE_EXCEEDS_ALGORITHM_LIMIT",
                    f"{path}/{field}",
                    f"requested {field} exceeds AlgorithmManifest {limit_field}",
                )
            )
    if resources.get("device") != approval_limits.get("device"):
        violations.append(
            GateViolation(
                "RESOURCE_OUTSIDE_APPROVAL",
                path + "/device",
                "requested device differs from the approved device",
            )
        )
    for field in ("gpu_count", "cpu_cores", "memory_mb", "wall_time_seconds"):
        if resources.get(field, 0) > approval_limits.get(field, -1):
            violations.append(
                GateViolation(
                    "RESOURCE_OUTSIDE_APPROVAL",
                    f"{path}/{field}",
                    f"requested {field} exceeds the approval scope",
                )
            )
    return violations


def evaluate_execution_gate(
    *,
    draft: Mapping[str, Any],
    plan: Mapping[str, Any],
    approval: Mapping[str, Any],
    dataset_registry: Mapping[tuple[str, str], Mapping[str, Any]],
    algorithm_registry: Mapping[tuple[str, str], Mapping[str, Any]],
    principal_id: str,
    project_id: str,
    approval_tasks_used: int = 0,
    now: datetime | None = None,
) -> list[GateViolation]:
    """Evaluate every deterministic P0 gate needed before Queued.

    The caller supplies immutable registry snapshots.  P1 will move these reads
    and the resulting transition into one SQLite transaction.
    """

    violations: list[GateViolation] = []
    for name, value, path in (
        ("task-draft.schema.json", draft, "/draft"),
        ("plan-graph.schema.json", plan, "/plan"),
        ("approval-decision.schema.json", approval, "/approval"),
    ):
        for error in schema_errors(name, value):
            violations.append(GateViolation("SCHEMA_INVALID", path, error))
    if violations:
        return sorted(set(violations))

    try:
        expected_hash = compute_plan_hash(plan)
    except ValueError as error:
        expected_hash = None
        violations.append(
            GateViolation(
                "PLAN_CANONICALIZATION_INVALID",
                "/plan",
                str(error),
            )
        )
    if expected_hash is not None and plan["plan_hash"] != expected_hash:
        violations.append(
            GateViolation("PLAN_HASH_INVALID", "/plan/plan_hash", "plan_hash does not match canonical plan content")
        )
    if approval["plan_id"] != plan["plan_id"]:
        violations.append(
            GateViolation("APPROVAL_PLAN_MISMATCH", "/approval/plan_id", "approval targets a different plan_id")
        )
    if approval["plan_hash"] != plan["plan_hash"]:
        violations.append(
            GateViolation("APPROVAL_HASH_MISMATCH", "/approval/plan_hash", "approval is not bound to the current plan_hash")
        )
    if approval["decision"] != "approved":
        violations.append(
            GateViolation("PLAN_NOT_APPROVED", "/approval/decision", "an approved decision is required")
        )

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("execution-gate time must be timezone-aware")
    current = current.astimezone(timezone.utc)
    if _parse_timestamp(approval["expires_at"]) <= current:
        violations.append(
            GateViolation("APPROVAL_EXPIRED", "/approval/expires_at", "approval has expired")
        )
    if _parse_timestamp(approval["decided_at"]) > current:
        violations.append(
            GateViolation("APPROVAL_NOT_YET_VALID", "/approval/decided_at", "approval decision is in the future")
        )
    if _parse_timestamp(approval["expires_at"]) <= _parse_timestamp(approval["decided_at"]):
        violations.append(
            GateViolation("APPROVAL_WINDOW_INVALID", "/approval/expires_at", "approval must expire after it is decided")
        )
    if _parse_timestamp(approval["decided_at"]) < _parse_timestamp(plan["created_at"]):
        violations.append(
            GateViolation("APPROVAL_PREDATES_PLAN", "/approval/decided_at", "approval cannot predate the plan it binds")
        )
    if approval["actor"]["type"] == "user" and approval["actor"]["id"] != principal_id:
        violations.append(
            GateViolation("APPROVAL_ACTOR_MISMATCH", "/approval/actor", "P0 user approval must belong to the submitting principal")
        )
    if approval["actor"]["type"] == "agent_delegation":
        violations.append(
            GateViolation("DELEGATED_APPROVAL_UNSUPPORTED", "/approval/actor", "P0/P1 Guided execution does not activate agent delegation")
        )
    if approval_tasks_used < 0:
        raise ValueError("approval_tasks_used must be non-negative")
    if approval_tasks_used >= approval["scope"]["max_tasks"]:
        violations.append(
            GateViolation("APPROVAL_TASK_BUDGET_EXHAUSTED", "/approval/scope/max_tasks", "approval task budget is exhausted")
        )

    if draft["status"] != "AwaitingApproval":
        violations.append(
            GateViolation(
                "DRAFT_NOT_AWAITING_APPROVAL",
                "/draft/status",
                "draft must be AwaitingApproval before execution",
            )
        )
    if draft["missing_fields"] or plan["missing_fields"]:
        violations.append(
            GateViolation("UNRESOLVED_FIELDS", "/draft/missing_fields", "all required fields must be resolved before execution")
        )
    if plan["draft"] != {"draft_id": draft["draft_id"], "revision": draft["revision"]}:
        violations.append(
            GateViolation("DRAFT_REVISION_MISMATCH", "/plan/draft", "plan does not target the current draft revision")
        )
    if plan["task_type"] != draft["task_type"]:
        violations.append(
            GateViolation(
                "TASK_TYPE_OUTSIDE_DRAFT",
                "/plan/task_type",
                "plan task_type differs from the current TaskDraft",
            )
        )

    violations.extend(_dag_errors(plan["nodes"]))
    try:
        extract_plan_data_edges(plan)
    except PlanDataEdgeError as error:
        violations.extend(
            GateViolation("DATA_EDGE_INVALID", "/plan/nodes", detail)
            for detail in error.errors
        )
    idempotency_keys = [node["idempotency_key"] for node in plan["nodes"]]
    if len(idempotency_keys) != len(set(idempotency_keys)):
        violations.append(
            GateViolation("DUPLICATE_IDEMPOTENCY_KEY", "/plan/nodes", "idempotency_key values must be unique within a plan")
        )

    draft_dataset_by_key = {
        (item["id"], item["version"]): item
        for item in draft["datasets"]
    }
    draft_datasets = {
        (item["id"], item["version"], item["content_hash"], item["data_type"])
        for item in draft["datasets"]
    }
    approval_scope = approval["scope"]
    for node_index, node in enumerate(plan["nodes"]):
        algorithm = node["algorithm"]
        algorithm_key = (algorithm["id"], algorithm["version"])
        manifest = algorithm_registry.get(algorithm_key)
        node_path = f"/plan/nodes/{node_index}"
        if algorithm != draft["algorithm"]:
            violations.append(
                GateViolation("ALGORITHM_OUTSIDE_DRAFT", node_path + "/algorithm", "P0 plan algorithm differs from the current TaskDraft")
            )
        if node["parameters"] != draft["parameters"]:
            violations.append(
                GateViolation(
                    "PARAMETERS_OUTSIDE_DRAFT",
                    node_path + "/parameters",
                    "P0 plan node parameters differ from the current TaskDraft",
                )
            )
        if node["resources"] != draft["resources"]:
            violations.append(
                GateViolation(
                    "RESOURCES_OUTSIDE_DRAFT",
                    node_path + "/resources",
                    "P0 plan node resources differ from the current TaskDraft",
                )
            )
        preset = node["parameters"]["preset"]
        if (
            (plan["task_type"] == "acoustic_forward_2d" and preset != "forward")
            or (plan["task_type"] == "acoustic_fwi_2d" and preset == "forward")
        ):
            violations.append(
                GateViolation("TASK_PARAMETER_MISMATCH", node_path + "/parameters/preset", "FWI preset is incompatible with the planned task type")
            )
        if node["parameters"]["device"] != node["resources"]["device"]:
            violations.append(
                GateViolation("PARAMETER_RESOURCE_MISMATCH", node_path + "/parameters/device", "parameter device must equal resource device")
            )
        if manifest is None:
            violations.append(
                GateViolation("ALGORITHM_NOT_REGISTERED", node_path + "/algorithm", "pinned algorithm version is not registered")
            )
            continue
        manifest_errors = schema_errors("algorithm-manifest.schema.json", manifest)
        if manifest_errors:
            violations.append(
                GateViolation("ALGORITHM_MANIFEST_INVALID", node_path + "/algorithm", "; ".join(manifest_errors))
            )
            continue
        input_ports = [item["port"] for item in manifest["inputs"]]
        output_ports = [item["port"] for item in manifest["outputs"]]
        if len(input_ports) != len(set(input_ports)) or len(output_ports) != len(set(output_ports)):
            violations.append(
                GateViolation("ALGORITHM_MANIFEST_INVALID", node_path + "/algorithm", "algorithm input/output port names must be unique")
            )
            continue
        if not manifest["security"]["allowlisted"]:
            violations.append(
                GateViolation("ALGORITHM_NOT_ALLOWLISTED", node_path + "/algorithm", "registered algorithm version is not allowlisted")
            )
        if not _contains_identity(approval_scope["algorithms"], algorithm, ("id", "version")):
            violations.append(
                GateViolation("ALGORITHM_OUTSIDE_APPROVAL", node_path + "/algorithm", "algorithm version is outside approval scope")
            )
        if plan["task_type"] not in manifest["task_types"]:
            violations.append(
                GateViolation("TASK_TYPE_MISMATCH", node_path + "/algorithm", "algorithm does not declare this task type")
            )

        parameter_validator = Draft7Validator(manifest["parameter_schema"])
        if list(parameter_validator.iter_errors(node["parameters"])):
            violations.append(
                GateViolation("PARAMETER_SCHEMA_MISMATCH", node_path + "/parameters", "node parameters violate AlgorithmManifest parameter_schema")
            )
        manifest_inputs = {item["port"]: item["data_type"] for item in manifest["inputs"]}
        planned_input_ports = [item["port"] for item in node["inputs"]]
        if (
            len(planned_input_ports) != len(set(planned_input_ports))
            or set(planned_input_ports) != set(manifest_inputs)
        ):
            violations.append(
                GateViolation(
                    "INPUT_PORT_MISMATCH",
                    node_path + "/inputs",
                    "planned input ports must match AlgorithmManifest exactly",
                )
            )
        for input_index, binding in enumerate(node["inputs"]):
            input_path = f"{node_path}/inputs/{input_index}"
            dataset = binding.get("dataset")
            source = binding.get("source")
            binding_type = (
                dataset.get("data_type")
                if isinstance(dataset, Mapping)
                else source.get("data_type")
                if isinstance(source, Mapping)
                else None
            )
            if manifest_inputs.get(binding["port"]) != binding_type:
                violations.append(
                    GateViolation("INPUT_TYPE_MISMATCH", input_path, "bound data type does not match the algorithm input port")
                )
            if not isinstance(dataset, Mapping):
                # A node-output binding is already covered by the approved
                # PlanGraph/hash and the DATA_EDGE checks above.  Its concrete
                # artifact hash is verified later at P3 node admission.
                continue
            if (dataset["id"], dataset["version"], dataset["content_hash"], dataset["data_type"]) not in draft_datasets:
                violations.append(
                    GateViolation("DATASET_OUTSIDE_DRAFT", input_path, "plan input is not present in the approved draft revision")
                )
            registered = dataset_registry.get((dataset["id"], dataset["version"]))
            if registered is None:
                violations.append(
                    GateViolation("DATASET_NOT_REGISTERED", input_path, "dataset id/version is not registered")
                )
                continue
            registered_errors = schema_errors("dataset-ref.schema.json", registered)
            if registered_errors:
                violations.append(
                    GateViolation("DATASET_REF_INVALID", input_path, "; ".join(registered_errors))
                )
                continue
            if registered["content_hash"] != dataset["content_hash"]:
                violations.append(
                    GateViolation("DATASET_HASH_MISMATCH", input_path, "plan hash does not match the registered immutable dataset")
                )
            draft_dataset = draft_dataset_by_key.get((dataset["id"], dataset["version"]))
            if draft_dataset is not None and registered != draft_dataset:
                violations.append(
                    GateViolation("DATASET_METADATA_MISMATCH", input_path, "TaskDraft DatasetRef differs from the immutable registry record")
                )
            scope = registered["access_scope"]
            if (
                scope["project_id"] != project_id
                or principal_id not in scope["principals"]
                or "execute" not in scope["permissions"]
            ):
                violations.append(
                    GateViolation("DATASET_ACCESS_DENIED", input_path, "principal lacks execute access to this dataset version")
                )
            if not _contains_identity(
                approval_scope["datasets"],
                dataset,
                ("id", "version", "content_hash", "data_type"),
            ):
                violations.append(
                    GateViolation("DATASET_OUTSIDE_APPROVAL", input_path, "dataset version is outside approval scope")
                )

        manifest_outputs = {item["port"]: item["data_type"] for item in manifest["outputs"]}
        planned_output_ports = [item["port"] for item in node["outputs"]]
        if (
            len(planned_output_ports) != len(set(planned_output_ports))
            or set(planned_output_ports) != set(manifest_outputs)
        ):
            violations.append(
                GateViolation(
                    "OUTPUT_PORT_MISMATCH",
                    node_path + "/outputs",
                    "planned output ports must match AlgorithmManifest exactly",
                )
            )
        for output_index, output in enumerate(node["outputs"]):
            if manifest_outputs.get(output["port"]) != output["data_type"]:
                violations.append(
                    GateViolation("OUTPUT_TYPE_MISMATCH", f"{node_path}/outputs/{output_index}", "planned output does not match AlgorithmManifest")
                )

        declared_effects = set(manifest["security"]["side_effects"])
        approved_effects = set(approval_scope["side_effects"])
        requested_effects = set(node["side_effects"])
        if not requested_effects.issubset(declared_effects):
            violations.append(
                GateViolation("SIDE_EFFECT_UNDECLARED", node_path + "/side_effects", "node requests side effects absent from AlgorithmManifest")
            )
        if not requested_effects.issubset(approved_effects):
            violations.append(
                GateViolation("SIDE_EFFECT_OUTSIDE_APPROVAL", node_path + "/side_effects", "node requests side effects outside approval scope")
            )
        violations.extend(
            _resource_errors(
                node["resources"],
                manifest["resource_limits"],
                approval_scope["resource_limits"],
                node_path + "/resources",
            )
        )

    return sorted(set(violations))
