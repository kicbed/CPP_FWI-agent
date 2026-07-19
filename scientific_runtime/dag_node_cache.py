"""Pure construction of scope-bound semantic DAG node cache identities.

This module deliberately does not read artifacts, consult SQLite, or authorize a
cache hit.  It only turns already-verified Plan, registry, input-binding, scope,
and execution-provenance documents into one canonical identity.  The durable
cache-hit path must re-verify those source facts before using this identity.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from scientific_runtime_contracts import canonical_json_bytes


_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_GIT_OBJECT = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")

_FINGERPRINT_FIELDS = frozenset(
    {
        "provenance_mode",
        "algorithm",
        "adapter_version",
        "source",
        "environment",
        "runtime",
        "seed",
        "hardware",
        "normalized_config_hash",
        "input_hashes",
        "determinism",
    }
)
_FINGERPRINT_TRANSIENT_FIELDS = frozenset(
    {
        "task_id",
        "plan_id",
        "approval_id",
        "draft_id",
        "supervisor_term",
        "fencing_token",
        "owner_id",
        "term_acquired_at",
        "idempotency_key",
        "timestamp",
        "created_at",
        "updated_at",
        "recorded_at",
    }
)
_NODE_CONTRACT_FIELDS = (
    "algorithm",
    "inputs",
    "dependencies",
    "parameters",
    "resources",
    "side_effects",
    "outputs",
)


class NodeCacheIdentityError(ValueError):
    """A stable fail-closed cache-identity construction error."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class NodeCacheIdentity:
    """Canonical semantic document and the SHA-256 cache key derived from it."""

    document: dict[str, Any]
    document_hash: str


def _invalid() -> NodeCacheIdentityError:
    return NodeCacheIdentityError("NODE_CACHE_IDENTITY_INVALID")


def _uncacheable() -> NodeCacheIdentityError:
    return NodeCacheIdentityError("NODE_CACHE_UNCACHEABLE")


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _canonical_copy(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return the normalized JSON object used by the cache hash profile."""

    try:
        encoded = canonical_json_bytes(value)
        decoded = json.loads(encoded.decode("utf-8"))
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError) as error:
        raise _invalid() from error
    if not isinstance(decoded, dict):
        raise _invalid()
    return decoded


def _document_hash(value: Mapping[str, Any]) -> str:
    try:
        digest = hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    except (TypeError, ValueError, UnicodeError) as error:
        raise _invalid() from error
    return f"sha256:{digest}"


def _normalize_dataset_root(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _invalid()
    wrapped = value.get("dataset")
    dataset = wrapped if isinstance(wrapped, Mapping) else value
    catalog_hash = value.get("catalog_document_hash")
    legacy_catalog_hash = value.get("dataset_document_hash")
    if catalog_hash is None:
        catalog_hash = legacy_catalog_hash
    elif legacy_catalog_hash is not None and legacy_catalog_hash != catalog_hash:
        raise _invalid()
    root = {
        "id": dataset.get("id"),
        "version": dataset.get("version"),
        "content_hash": dataset.get("content_hash"),
        "data_type": dataset.get("data_type"),
        "catalog_document_hash": catalog_hash,
    }
    if (
        not _is_nonempty_string(root["id"])
        or not _is_nonempty_string(root["version"])
        or not _is_sha256(root["content_hash"])
        or not _is_nonempty_string(root["data_type"])
        or not _is_sha256(root["catalog_document_hash"])
    ):
        raise _invalid()
    return root


def _normalize_dataset_input(
    value: Mapping[str, Any],
    *,
    expected: Mapping[str, Any],
    input_index: int,
    target_port: str,
) -> dict[str, Any]:
    if value.get("kind") != "dataset" or not isinstance(
        value.get("dataset"), Mapping
    ):
        raise _invalid()
    root = _normalize_dataset_root(
        {
            "dataset": value["dataset"],
            "dataset_document_hash": value.get("dataset_document_hash"),
            "catalog_document_hash": value.get("catalog_document_hash"),
        }
    )
    expected_identity = {
        key: expected.get(key)
        for key in ("id", "version", "content_hash", "data_type")
    }
    if {key: root[key] for key in expected_identity} != expected_identity:
        raise _invalid()
    return {
        "input_index": input_index,
        "target_input_port": target_port,
        "kind": "dataset",
        "dataset": root,
    }


def _normalize_transitive_roots(value: Any) -> list[dict[str, Any]]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or not value
    ):
        raise _invalid()
    roots = [_normalize_dataset_root(item) for item in value]
    keyed = [(canonical_json_bytes(root), root) for root in roots]
    if len({key for key, _ in keyed}) != len(keyed):
        raise _invalid()
    return [root for _, root in sorted(keyed, key=lambda item: item[0])]


def _normalize_node_output_input(
    value: Mapping[str, Any],
    *,
    expected: Mapping[str, Any],
    input_index: int,
    target_port: str,
    node_id: str,
) -> dict[str, Any]:
    binding = value.get("binding")
    producer = value.get("producer")
    if (
        value.get("kind") != "node_output"
        or not isinstance(binding, Mapping)
        or not isinstance(producer, Mapping)
    ):
        raise _invalid()
    target = binding.get("target")
    source = binding.get("source")
    artifact = binding.get("artifact")
    if (
        not isinstance(target, Mapping)
        or not isinstance(source, Mapping)
        or not isinstance(artifact, Mapping)
        or target.get("node_id") != node_id
        or target.get("input_port") != target_port
        or source.get("node_id") != expected.get("node_id")
        or source.get("output_port") != expected.get("port")
        or source.get("data_type") != expected.get("data_type")
    ):
        raise _invalid()
    artifact_identity = {
        "schema_version": artifact.get("schema_version"),
        "media_type": artifact.get("media_type"),
        "content_hash": artifact.get("content_hash"),
        "size_bytes": artifact.get("size_bytes"),
    }
    if (
        not _is_nonempty_string(artifact_identity["schema_version"])
        or not _is_nonempty_string(artifact_identity["media_type"])
        or not _is_sha256(artifact_identity["content_hash"])
        or type(artifact_identity["size_bytes"]) is not int
        or artifact_identity["size_bytes"] < 0
    ):
        raise _invalid()
    producer_key = producer.get("semantic_cache_key_hash")
    if not _is_sha256(producer_key) or type(producer.get("cacheable")) is not bool:
        raise _invalid()
    if producer["cacheable"] is not True:
        raise _uncacheable()
    roots = _normalize_transitive_roots(
        producer.get("transitive_dataset_roots")
    )
    return {
        "input_index": input_index,
        "target_input_port": target_port,
        "kind": "node_output",
        "source": {
            "node_id": source["node_id"],
            "output_port": source["output_port"],
            "data_type": source["data_type"],
        },
        "artifact": artifact_identity,
        "producer": {
            "semantic_cache_key_hash": producer_key,
            "cacheable": True,
            "transitive_dataset_roots": roots,
        },
    }


def _normalize_inputs(
    node: Mapping[str, Any],
    *,
    node_id: str,
    input_binding_document: Mapping[str, Any],
    project_id: str,
    principal_id: str,
) -> list[dict[str, Any]]:
    if input_binding_document.get("schema_version") != "1.0.0":
        raise _invalid()
    target = input_binding_document.get("target")
    scope = input_binding_document.get("scope")
    if (
        not isinstance(target, Mapping)
        or target.get("node_id") != node_id
        or not isinstance(scope, Mapping)
        or dict(scope)
        != {"project_id": project_id, "principal_id": principal_id}
    ):
        raise _invalid()
    planned_inputs = node.get("inputs")
    bound_inputs = input_binding_document.get("inputs")
    if not isinstance(planned_inputs, list) or not isinstance(bound_inputs, list):
        raise _invalid()
    if len(planned_inputs) != len(bound_inputs):
        raise _invalid()

    by_index: dict[int, Mapping[str, Any]] = {}
    for value in bound_inputs:
        if not isinstance(value, Mapping) or type(value.get("input_index")) is not int:
            raise _invalid()
        index = value["input_index"]
        if index < 0 or index in by_index:
            raise _invalid()
        by_index[index] = value
    if set(by_index) != set(range(len(planned_inputs))):
        raise _invalid()

    normalized: list[dict[str, Any]] = []
    for index, expected_input in enumerate(planned_inputs):
        value = by_index[index]
        if (
            not isinstance(expected_input, Mapping)
            or not _is_nonempty_string(expected_input.get("port"))
            or value.get("target_input_port") != expected_input["port"]
        ):
            raise _invalid()
        dataset = expected_input.get("dataset")
        source = expected_input.get("source")
        if isinstance(dataset, Mapping) and source is None:
            normalized.append(
                _normalize_dataset_input(
                    value,
                    expected=dataset,
                    input_index=index,
                    target_port=expected_input["port"],
                )
            )
        elif isinstance(source, Mapping) and dataset is None:
            normalized.append(
                _normalize_node_output_input(
                    value,
                    expected=source,
                    input_index=index,
                    target_port=expected_input["port"],
                    node_id=node_id,
                )
            )
        else:
            raise _invalid()
    return normalized


def _normalize_fingerprint(
    value: Mapping[str, Any],
    *,
    algorithm: Mapping[str, Any],
    adapter_version: str,
    input_hashes: list[str],
) -> dict[str, Any]:
    keys = set(value)
    if not _FINGERPRINT_FIELDS.issubset(keys) or not (
        keys - _FINGERPRINT_FIELDS
    ).issubset(_FINGERPRINT_TRANSIENT_FIELDS):
        raise _invalid()
    if (
        value.get("algorithm") != algorithm
        or value.get("adapter_version") != adapter_version
        or value.get("input_hashes") != input_hashes
    ):
        raise _invalid()
    source = value.get("source")
    environment = value.get("environment")
    if (
        value.get("provenance_mode") != "reproducible"
        or not isinstance(source, Mapping)
        or source.get("identity_complete") is not True
        or source.get("dirty") is not False
        or not isinstance(source.get("git_commit"), str)
        or _GIT_OBJECT.fullmatch(source["git_commit"]) is None
        or not isinstance(source.get("git_tree"), str)
        or _GIT_OBJECT.fullmatch(source["git_tree"]) is None
        or not isinstance(environment, Mapping)
        or not _is_sha256(environment.get("environment_lock_hash"))
    ):
        raise _uncacheable()
    selected = {key: value[key] for key in _FINGERPRINT_FIELDS}
    return _canonical_copy(selected)


def build_node_cache_identity(
    plan: Mapping[str, Any],
    *,
    node_id: str,
    input_binding_document: Mapping[str, Any],
    adapter_id: str,
    adapter_version: str,
    queue_fingerprint: Mapping[str, Any],
    algorithm_manifest: Mapping[str, Any],
    algorithm_manifest_document_hash: str,
    approval_scope: Mapping[str, Any],
    project_id: str,
    principal_id: str,
) -> NodeCacheIdentity:
    """Build a v1 canonical node cache key from semantic, verified facts.

    Task/Plan/Approval instance identifiers, timestamps, supervisor terms,
    idempotency keys, artifact identifiers, and producer receipt identifiers are
    intentionally absent.  The project/principal permission boundary and exact
    Approval scope are intentionally present, so this never creates a public or
    cross-principal cache namespace.
    """

    if (
        not isinstance(plan, Mapping)
        or not _is_nonempty_string(node_id)
        or not isinstance(input_binding_document, Mapping)
        or not _is_nonempty_string(adapter_id)
        or not _is_nonempty_string(adapter_version)
        or not isinstance(queue_fingerprint, Mapping)
        or not isinstance(algorithm_manifest, Mapping)
        or not _is_sha256(algorithm_manifest_document_hash)
        or not isinstance(approval_scope, Mapping)
        or not _is_nonempty_string(project_id)
        or not _is_nonempty_string(principal_id)
        or not _is_nonempty_string(plan.get("schema_version"))
        or not _is_nonempty_string(plan.get("task_type"))
        or not isinstance(plan.get("nodes"), list)
    ):
        raise _invalid()

    matches = [
        node
        for node in plan["nodes"]
        if isinstance(node, Mapping) and node.get("node_id") == node_id
    ]
    if len(matches) != 1:
        raise _invalid()
    node = matches[0]
    if any(field not in node for field in _NODE_CONTRACT_FIELDS):
        raise _invalid()
    algorithm = node.get("algorithm")
    if not isinstance(algorithm, Mapping):
        raise _invalid()
    if (
        algorithm_manifest.get("id") != algorithm.get("id")
        or algorithm_manifest.get("version") != algorithm.get("version")
        or _document_hash(algorithm_manifest)
        != algorithm_manifest_document_hash
    ):
        raise _invalid()

    normalized_inputs = _normalize_inputs(
        node,
        node_id=node_id,
        input_binding_document=input_binding_document,
        project_id=project_id,
        principal_id=principal_id,
    )
    input_hashes = [
        value["dataset"]["content_hash"]
        if value["kind"] == "dataset"
        else value["artifact"]["content_hash"]
        for value in normalized_inputs
    ]
    fingerprint = _normalize_fingerprint(
        queue_fingerprint,
        algorithm=algorithm,
        adapter_version=adapter_version,
        input_hashes=input_hashes,
    )

    node_contract = {
        "plan_schema_version": plan["schema_version"],
        "task_type": plan["task_type"],
        "node_id": node_id,
        **{field: node[field] for field in _NODE_CONTRACT_FIELDS},
    }
    document = _canonical_copy(
        {
            "schema_version": "1.0.0",
            "permission_scope": {
                "project_id": project_id,
                "principal_id": principal_id,
            },
            "approval_scope": approval_scope,
            "node_contract": node_contract,
            "algorithm_registry": {
                "manifest": algorithm_manifest,
                "manifest_document_hash": algorithm_manifest_document_hash,
            },
            "adapter": {"id": adapter_id, "version": adapter_version},
            "inputs": normalized_inputs,
            "execution_fingerprint": fingerprint,
        }
    )
    return NodeCacheIdentity(document=document, document_hash=_document_hash(document))


__all__ = [
    "NodeCacheIdentity",
    "NodeCacheIdentityError",
    "build_node_cache_identity",
]
