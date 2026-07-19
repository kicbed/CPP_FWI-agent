"""Pure, non-executable binding of P3 DAG inputs to verified artifacts."""

from __future__ import annotations

import copy
import hashlib
import re
from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

from scientific_runtime_contracts import (
    PlanDataEdgeError,
    canonical_json_bytes,
    extract_plan_data_edges,
    schema_errors,
)

from .dag_scheduler import DagScheduleError, PENDING, evaluate_dag_readiness


_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


class DagDataBindingError(ValueError):
    """A stable fail-closed rejection from the pure artifact-binding kernel."""

    def __init__(self, code: str, errors: list[str] | tuple[str, ...]):
        self.code = code
        self.errors = tuple(sorted(set(errors)))
        super().__init__(f"{code}: {'; '.join(self.errors)}")


@dataclass(frozen=True)
class DagArtifactInputBinding:
    """Canonical identity for one verified artifact consumed by a DAG node."""

    task_id: str
    plan_id: str
    plan_hash: str
    target_node_id: str
    target_input_port: str
    source_node_id: str
    source_output_port: str
    source_algorithm_id: str
    source_algorithm_version: str
    data_type: str
    artifact_id: str
    artifact_schema_version: str
    artifact_media_type: str
    artifact_content_hash: str
    artifact_size_bytes: int
    binding_document_hash: str

    def document(self) -> dict[str, Any]:
        """Return the exact integer/string-only document behind the hash."""

        return {
            "schema_version": "1.0.0",
            "task_id": self.task_id,
            "plan": {
                "plan_id": self.plan_id,
                "plan_hash": self.plan_hash,
            },
            "target": {
                "node_id": self.target_node_id,
                "input_port": self.target_input_port,
            },
            "source": {
                "node_id": self.source_node_id,
                "output_port": self.source_output_port,
                "algorithm": {
                    "id": self.source_algorithm_id,
                    "version": self.source_algorithm_version,
                },
                "data_type": self.data_type,
            },
            "artifact": {
                "artifact_id": self.artifact_id,
                "schema_version": self.artifact_schema_version,
                "media_type": self.artifact_media_type,
                "content_hash": self.artifact_content_hash,
                "size_bytes": self.artifact_size_bytes,
            },
        }

    @property
    def dispatch_authorized(self) -> bool:
        """A verified data binding is still not node execution authorization."""

        return False


def _document_hash(value: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def bind_dag_artifact_input(
    plan: Mapping[str, Any],
    *,
    task_id: str,
    target_node_id: str,
    target_input_port: str,
    artifact_manifest: Mapping[str, Any],
    artifact_data: bytes,
    expected_lineage_inputs: Sequence[Mapping[str, Any]] | None = None,
    expected_fingerprint_input_hashes: Sequence[str] | None = None,
) -> DagArtifactInputBinding:
    """Validate bytes and bind one exact upstream artifact to a typed input.

    The PlanGraph hash fixes the logical source node/output and target
    node/input.  The returned document additionally fixes the concrete
    ``artifact_id`` and byte ``content_hash``.  This function performs no I/O,
    persistence, state transition, claim, budget mutation, or dispatch.

    The PlanGraph is expected to have passed TaskService/Registry input and
    output compatibility checks already.  This kernel proves the plan-local
    edge and concrete artifact identity; it does not replace AlgorithmManifest
    validation.
    """

    identity_errors: list[str] = []
    if not isinstance(task_id, str) or _OPAQUE_ID.fullmatch(task_id) is None:
        identity_errors.append("task_id must be a v1 opaque identifier")
    if not isinstance(target_node_id, str) or not target_node_id:
        identity_errors.append("target_node_id is invalid")
    if not isinstance(target_input_port, str) or not target_input_port:
        identity_errors.append("target_input_port is invalid")
    if not isinstance(artifact_data, bytes):
        identity_errors.append("artifact_data must be exact bytes")
    if identity_errors:
        raise DagDataBindingError("DAG_DATA_BINDING_INVALID", identity_errors)

    if not isinstance(plan, Mapping):
        raise DagDataBindingError(
            "DAG_DATA_PLAN_INVALID", ["plan must be an object"]
        )

    try:
        node_states = {
            node["node_id"]: PENDING
            for node in plan.get("nodes", [])
            if isinstance(node, Mapping) and isinstance(node.get("node_id"), str)
        }
        evaluate_dag_readiness(plan, node_states=node_states)
        edges = extract_plan_data_edges(plan)
    except DagScheduleError as error:
        raise DagDataBindingError(error.code, error.errors) from error
    except PlanDataEdgeError as error:
        raise DagDataBindingError("DAG_DATA_EDGE_INVALID", error.errors) from error

    matches = [
        edge
        for edge in edges
        if edge.target_node_id == target_node_id
        and edge.target_input_port == target_input_port
    ]
    if len(matches) != 1:
        raise DagDataBindingError(
            "DAG_DATA_EDGE_NOT_FOUND",
            ["target node/input does not identify exactly one typed data edge"],
        )
    edge = matches[0]

    if not isinstance(artifact_manifest, Mapping):
        raise DagDataBindingError(
            "DAG_ARTIFACT_INVALID", ["artifact_manifest must be an object"]
        )
    manifest_errors = schema_errors("artifact-manifest.schema.json", artifact_manifest)
    if manifest_errors:
        raise DagDataBindingError("DAG_ARTIFACT_INVALID", manifest_errors)

    nodes_by_id = {node["node_id"]: node for node in plan["nodes"]}
    source_node = nodes_by_id[edge.source_node_id]
    source_inputs = source_node.get("inputs")
    if not isinstance(source_inputs, list):
        raise DagDataBindingError(
            "DAG_ARTIFACT_LINEAGE_UNSUPPORTED",
            ["producing node inputs are not a valid Plan contract"],
        )
    if expected_lineage_inputs is None:
        if any(
            not isinstance(binding, Mapping)
            or not isinstance(binding.get("dataset"), Mapping)
            for binding in source_inputs
        ):
            raise DagDataBindingError(
                "DAG_ARTIFACT_LINEAGE_UNSUPPORTED",
                [
                    "multi-level lineage requires a durable producer "
                    "receipt resolved to Dataset roots"
                ],
            )
        lineage_inputs = [
            copy.deepcopy(dict(binding["dataset"]))
            for binding in source_inputs
        ]
    else:
        if (
            not isinstance(expected_lineage_inputs, Sequence)
            or isinstance(expected_lineage_inputs, (str, bytes, bytearray))
            or not expected_lineage_inputs
            or any(not isinstance(value, Mapping) for value in expected_lineage_inputs)
        ):
            raise DagDataBindingError(
                "DAG_ARTIFACT_LINEAGE_UNSUPPORTED",
                ["durable Dataset-root lineage is invalid"],
            )
        lineage_inputs = [
            copy.deepcopy(dict(value)) for value in expected_lineage_inputs
        ]
    if expected_fingerprint_input_hashes is None:
        expected_input_hashes = [
            value.get("content_hash") for value in lineage_inputs
        ]
    elif (
        not isinstance(expected_fingerprint_input_hashes, Sequence)
        or isinstance(expected_fingerprint_input_hashes, (str, bytes, bytearray))
        or not expected_fingerprint_input_hashes
        or any(
            not isinstance(value, str) or _SHA256.fullmatch(value) is None
            for value in expected_fingerprint_input_hashes
        )
    ):
        raise DagDataBindingError(
            "DAG_ARTIFACT_LINEAGE_UNSUPPORTED",
            ["durable producer input hashes are invalid"],
        )
    else:
        expected_input_hashes = list(expected_fingerprint_input_hashes)
    lineage = artifact_manifest.get("lineage")
    fingerprint = artifact_manifest.get("fingerprint")
    extensions = artifact_manifest.get("extensions")
    adapter_extension = (
        extensions.get("org.agent_rpc.adapter")
        if isinstance(extensions, Mapping)
        else None
    )
    observed_hash = "sha256:" + hashlib.sha256(artifact_data).hexdigest()

    errors: list[str] = []
    comparisons = (
        (artifact_manifest.get("task_id"), task_id, "task_id"),
        (artifact_manifest.get("node_id"), edge.source_node_id, "source node_id"),
        (artifact_manifest.get("artifact_type"), edge.data_type, "artifact_type"),
        (
            adapter_extension.get("output_port")
            if isinstance(adapter_extension, Mapping)
            else None,
            edge.source_output_port,
            "source output_port",
        ),
        (
            lineage.get("plan_hash") if isinstance(lineage, Mapping) else None,
            plan["plan_hash"],
            "lineage plan_hash",
        ),
        (
            lineage.get("algorithm") if isinstance(lineage, Mapping) else None,
            source_node["algorithm"],
            "lineage algorithm",
        ),
        (
            lineage.get("inputs") if isinstance(lineage, Mapping) else None,
            lineage_inputs,
            "lineage inputs",
        ),
        (
            fingerprint.get("algorithm")
            if isinstance(fingerprint, Mapping)
            else None,
            source_node["algorithm"],
            "fingerprint algorithm",
        ),
        (
            fingerprint.get("input_hashes")
            if isinstance(fingerprint, Mapping)
            else None,
            expected_input_hashes,
            "fingerprint input_hashes",
        ),
        (
            fingerprint.get("seed") if isinstance(fingerprint, Mapping) else None,
            source_node["parameters"]["seed"],
            "fingerprint seed",
        ),
        (
            fingerprint.get("hardware", {}).get("device")
            if isinstance(fingerprint, Mapping)
            and isinstance(fingerprint.get("hardware"), Mapping)
            else None,
            source_node["resources"]["device"],
            "fingerprint device",
        ),
        (artifact_manifest.get("content_hash"), observed_hash, "content_hash"),
        (artifact_manifest.get("size_bytes"), len(artifact_data), "size_bytes"),
    )
    for actual, expected, label in comparisons:
        if actual != expected:
            errors.append(f"artifact {label} does not match the bound source")
    if errors:
        raise DagDataBindingError("DAG_ARTIFACT_MISMATCH", errors)

    source_algorithm = source_node["algorithm"]
    provisional = DagArtifactInputBinding(
        task_id=task_id,
        plan_id=plan["plan_id"],
        plan_hash=plan["plan_hash"],
        target_node_id=edge.target_node_id,
        target_input_port=edge.target_input_port,
        source_node_id=edge.source_node_id,
        source_output_port=edge.source_output_port,
        source_algorithm_id=source_algorithm["id"],
        source_algorithm_version=source_algorithm["version"],
        data_type=edge.data_type,
        artifact_id=artifact_manifest["artifact_id"],
        artifact_schema_version=artifact_manifest["schema_version"],
        artifact_media_type=artifact_manifest["media_type"],
        artifact_content_hash=observed_hash,
        artifact_size_bytes=len(artifact_data),
        binding_document_hash="",
    )
    binding_hash = _document_hash(provisional.document())
    return replace(provisional, binding_document_hash=binding_hash)
