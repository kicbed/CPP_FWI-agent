"""Validated P1 task service with atomic admission and post-commit dispatch."""

from __future__ import annotations

import copy
import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from jsonschema import Draft7Validator
from jsonschema.exceptions import SchemaError

from scientific_runtime_contracts import (
    compute_plan_hash,
    evaluate_execution_gate,
    schema_errors,
)

from .fwi_registry import (
    DEEPWAVE_ALGORITHM_ID,
    DEEPWAVE_ALGORITHM_VERSION,
    load_deepwave_manifest,
)
from .task_dispatcher import DispatchError, DispatchPreparation, TaskDispatcher
from .task_store import (
    ALLOWED_TRANSITIONS,
    IdempotencyConflict,
    TASK_STATUSES,
    DispatchIntentSnapshot,
    SubmitGateContext,
    TaskSnapshot,
    TaskStore,
    TaskStoreConflict,
    encode_document,
)


OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

RUN_EVENT_STATUS = {
    "node_started": frozenset({"Running"}),
    "node_progress": frozenset({"Running"}),
    # P1's only executable capability will be one FWI node. Multi-node
    # lifecycle semantics are deferred to the P3 scheduler.
    "node_succeeded": frozenset({"Succeeded"}),
    "node_failed": frozenset({"Failed"}),
}

RUN_EVENT_EXPECTED_STATUS = {
    "node_started": frozenset({"Queued"}),
    "node_progress": frozenset({"Running"}),
    "node_succeeded": frozenset({"Running"}),
    "node_failed": frozenset({"Queued", "Running"}),
}

P2_EVENT_TYPES = frozenset(
    {
        "checkpoint_created",
        "node_waiting",
        "node_retrying",
        "cancel_requested",
        "task_cancelled",
    }
)


class TaskServiceError(RuntimeError):
    """Base class for stable TaskService failures."""


class TaskValidationError(TaskServiceError):
    """A public contract or service precondition is invalid."""

    def __init__(self, code: str, errors: list[str] | tuple[str, ...]):
        self.code = code
        self.errors = tuple(errors)
        super().__init__(f"{code}: {'; '.join(self.errors)}")


class TaskNotFound(TaskServiceError):
    """The requested durable task identity does not exist."""


class TaskConflict(TaskServiceError):
    """A revision, relationship, state, or immutable identity conflicts."""


class TaskIdempotencyConflict(TaskConflict):
    """A mutation key was reused for different request content."""


class TaskDispatchError(TaskServiceError):
    """Trusted Adapter preparation could not produce an admissible request."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class CreateTaskResult:
    snapshot: TaskSnapshot
    replayed: bool


@dataclass(frozen=True)
class SubmitTaskResult:
    snapshot: TaskSnapshot
    intent: DispatchIntentSnapshot
    replayed: bool
    dispatch_attempted: bool


@dataclass(frozen=True)
class AbandonTaskResult:
    snapshot: TaskSnapshot
    replayed: bool


@dataclass(frozen=True)
class TaskRuntimeResult:
    snapshot: TaskSnapshot
    intent: DispatchIntentSnapshot | None
    adapter_status: dict[str, Any] | None


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _task_id() -> str:
    return f"task-{uuid.uuid4().hex}"


def _validate_opaque_id(value: str, *, field: str) -> None:
    if not isinstance(value, str) or OPAQUE_ID.fullmatch(value) is None:
        raise TaskValidationError(
            "INVALID_IDENTITY", [f"{field} must be a v1 opaque identifier"]
        )


def _validate_idempotency_key(value: str) -> None:
    # P0 has not standardized the HTTP Idempotency-Key syntax.  P1.1 only
    # applies a storage-safe bound; it deliberately does not reuse the plan
    # node idempotency-key regex or add the key to a public JSON contract.
    if not isinstance(value, str) or not value or len(value) > 255:
        raise TaskValidationError(
            "INVALID_IDEMPOTENCY_KEY",
            ["idempotency_key must contain between 1 and 255 characters"],
        )
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise TaskValidationError(
            "INVALID_IDEMPOTENCY_KEY",
            ["idempotency_key cannot contain control characters"],
        )


def _validate_run_event_semantics(event: Mapping[str, Any]) -> None:
    event_type = event["event_type"]
    if event_type in P2_EVENT_TYPES:
        raise TaskValidationError(
            "RUN_EVENT_UNSUPPORTED_IN_P1",
            [f"{event_type} semantics are deferred to P2"],
        )
    allowed_statuses = RUN_EVENT_STATUS.get(event_type)
    if allowed_statuses is None or event["task_status"] not in allowed_statuses:
        raise TaskValidationError(
            "RUN_EVENT_STATE_MISMATCH",
            [f"{event_type} is incompatible with task_status={event['task_status']}"],
        )
    if not event.get("node_id"):
        raise TaskValidationError(
            "RUN_EVENT_NODE_REQUIRED", [f"{event_type} requires node_id"]
        )
    required_detail = {
        "node_progress": "progress",
        "checkpoint_created": "checkpoint",
        "node_failed": "error",
    }.get(event_type)
    if required_detail is not None and required_detail not in event:
        raise TaskValidationError(
            "RUN_EVENT_DETAIL_REQUIRED",
            [f"{event_type} requires {required_detail}"],
        )
    if event_type != "node_failed" and "error" in event:
        raise TaskValidationError(
            "RUN_EVENT_DETAIL_FORBIDDEN",
            [f"{event_type} cannot carry an error"],
        )
    if event_type != "node_progress" and "progress" in event:
        raise TaskValidationError(
            "RUN_EVENT_DETAIL_FORBIDDEN",
            [f"{event_type} cannot carry progress"],
        )
    if "checkpoint" in event:
        raise TaskValidationError(
            "RUN_EVENT_UNSUPPORTED_IN_P1",
            ["checkpoint semantics are deferred to P2"],
        )
    progress = event.get("progress")
    if progress is not None and progress["completed"] > progress["total"]:
        raise TaskValidationError(
            "RUN_EVENT_PROGRESS_INVALID",
            ["progress.completed cannot exceed progress.total"],
        )


def _validate_run_event_binding(
    snapshot: TaskSnapshot, event: Mapping[str, Any]
) -> None:
    if snapshot.plan is None:
        raise TaskValidationError(
            "RUN_EVENT_PLAN_MISSING", ["runtime task has no current plan"]
        )
    node = next(
        (
            value
            for value in snapshot.plan["nodes"]
            if value["node_id"] == event["node_id"]
        ),
        None,
    )
    if node is None:
        raise TaskValidationError(
            "RUN_EVENT_NODE_UNKNOWN",
            ["event node_id is not present in the current plan"],
        )
    fingerprint = event["fingerprint"]
    expected_input_hashes = [
        binding["dataset"]["content_hash"] for binding in node["inputs"]
    ]
    mismatches: list[str] = []
    if fingerprint["algorithm"] != node["algorithm"]:
        mismatches.append("algorithm")
    if fingerprint["seed"] != node["parameters"]["seed"]:
        mismatches.append("seed")
    if fingerprint["hardware"]["device"] != node["resources"]["device"]:
        mismatches.append("device")
    if fingerprint["input_hashes"] != expected_input_hashes:
        mismatches.append("input_hashes")
    if mismatches:
        raise TaskValidationError(
            "RUN_EVENT_FINGERPRINT_MISMATCH",
            ["fingerprint differs from the current plan: " + ", ".join(mismatches)],
        )


def _validate_plan_draft_consistency(
    plan: Mapping[str, Any], draft: Mapping[str, Any]
) -> None:
    errors: list[str] = []
    if plan["task_type"] != draft["task_type"]:
        errors.append("plan task_type differs from the current draft")

    draft_datasets = {
        (item["id"], item["version"], item["content_hash"], item["data_type"])
        for item in draft["datasets"]
    }
    node_ids = [node["node_id"] for node in plan["nodes"]]
    if len(node_ids) != len(set(node_ids)):
        errors.append("plan node_id values must be unique")
    node_keys = [node["idempotency_key"] for node in plan["nodes"]]
    if len(node_keys) != len(set(node_keys)):
        errors.append("plan node idempotency keys must be unique")

    known_nodes = set(node_ids)
    dependencies: dict[str, list[str]] = {}
    for node in plan["nodes"]:
        node_id = node["node_id"]
        dependencies[node_id] = list(node["dependencies"])
        if node["algorithm"] != draft["algorithm"]:
            errors.append(f"node {node_id} algorithm differs from the current draft")
        if node["parameters"] != draft["parameters"]:
            errors.append(f"node {node_id} parameters differ from the current draft")
        if node["resources"] != draft["resources"]:
            errors.append(f"node {node_id} resources differ from the current draft")
        for binding in node["inputs"]:
            dataset = binding["dataset"]
            identity = (
                dataset["id"],
                dataset["version"],
                dataset["content_hash"],
                dataset["data_type"],
            )
            if identity not in draft_datasets:
                errors.append(f"node {node_id} input is outside the current draft")
        for dependency in node["dependencies"]:
            if dependency not in known_nodes:
                errors.append(f"node {node_id} has an unknown dependency")

    visiting: set[str] = set()
    visited: set[str] = set()

    def has_cycle(node_id: str) -> bool:
        if node_id in visiting:
            return True
        if node_id in visited:
            return False
        visiting.add(node_id)
        for dependency in dependencies.get(node_id, []):
            if dependency in known_nodes and has_cycle(dependency):
                return True
        visiting.remove(node_id)
        visited.add(node_id)
        return False

    if any(has_cycle(node_id) for node_id in node_ids if node_id not in visited):
        errors.append("plan dependencies must be acyclic")
    if errors:
        raise TaskValidationError("PLAN_DRAFT_MISMATCH", errors)


class TaskService:
    """Durable P1 task aggregate and one fixed, approved dispatch boundary."""

    def __init__(
        self,
        store: TaskStore,
        *,
        task_id_factory: Callable[[], str] = _task_id,
        clock: Callable[[], str] = _utc_now,
        dispatcher: TaskDispatcher | None = None,
    ) -> None:
        self._store = store
        self._task_id_factory = task_id_factory
        self._clock = clock
        self._dispatcher = dispatcher
        # Cache the trusted packaged binding before any submit transaction.
        self._p1_manifest = (
            load_deepwave_manifest() if dispatcher is not None else None
        )

    @staticmethod
    def _validate_schema(name: str, value: Mapping[str, Any]) -> None:
        errors = schema_errors(name, value)
        if errors:
            raise TaskValidationError("SCHEMA_INVALID", errors)

    def _lookup_workbench_replay(
        self,
        *,
        task_id: str,
        project_id: str,
        principal_id: str,
        operation: str,
        idempotency_key: str,
        request_hash: str,
    ) -> TaskSnapshot | None:
        try:
            replay = self._store.lookup_workbench_mutation(
                project_id=project_id,
                principal_id=principal_id,
                operation=operation,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
        except IdempotencyConflict as error:
            raise TaskIdempotencyConflict(str(error)) from error
        if replay is None:
            return None
        if replay.task_id != task_id:
            raise TaskIdempotencyConflict(
                "idempotency key identifies another task"
            )
        return self.get_task(
            task_id, project_id=project_id, principal_id=principal_id
        )

    def _validate_registered_draft(
        self,
        draft: Mapping[str, Any],
        *,
        project_id: str,
        principal_id: str,
    ) -> dict[str, Any]:
        dataset = draft["datasets"][0]
        algorithm = draft["algorithm"]
        dataset_key = (dataset["id"], dataset["version"])
        algorithm_key = (algorithm["id"], algorithm["version"])
        registry = self._store.load_registry_snapshots(
            project_id=project_id,
            dataset_keys=[dataset_key],
            algorithm_keys=[algorithm_key],
        )
        registered_dataset = registry.datasets.get(dataset_key)
        if registered_dataset is None:
            raise TaskValidationError(
                "DATASET_NOT_REGISTERED",
                ["TaskDraft dataset id/version is not registered for this project"],
            )
        dataset_errors = schema_errors(
            "dataset-ref.schema.json", registered_dataset
        )
        if dataset_errors:
            raise TaskValidationError(
                "REGISTRY_SNAPSHOT_INVALID", dataset_errors
            )
        access_scope = registered_dataset["access_scope"]
        if (
            access_scope["project_id"] != project_id
            or principal_id not in access_scope["principals"]
            or "execute" not in access_scope["permissions"]
        ):
            raise TaskValidationError(
                "DATASET_ACCESS_DENIED",
                ["principal lacks execute access to the registered dataset"],
            )
        if registered_dataset != dataset:
            raise TaskValidationError(
                "DATASET_METADATA_MISMATCH",
                ["TaskDraft DatasetRef differs from the immutable Catalog record"],
            )
        manifest = registry.algorithms.get(algorithm_key)
        if manifest is None:
            raise TaskValidationError(
                "ALGORITHM_NOT_REGISTERED",
                ["TaskDraft algorithm id/version is not registered"],
            )
        manifest_errors = schema_errors(
            "algorithm-manifest.schema.json", manifest
        )
        if manifest_errors:
            raise TaskValidationError(
                "REGISTRY_SNAPSHOT_INVALID", manifest_errors
            )
        try:
            Draft7Validator.check_schema(manifest["parameter_schema"])
        except SchemaError as error:
            raise TaskValidationError(
                "REGISTRY_SNAPSHOT_INVALID",
                [f"AlgorithmManifest parameter_schema is invalid: {error.message}"],
            ) from error
        for field in ("inputs", "outputs"):
            ports = [item["port"] for item in manifest[field]]
            if len(ports) != len(set(ports)):
                raise TaskValidationError(
                    "REGISTRY_SNAPSHOT_INVALID",
                    [f"AlgorithmManifest {field} ports must be unique"],
                )
        if not manifest["security"]["allowlisted"]:
            raise TaskValidationError(
                "ALGORITHM_NOT_ALLOWLISTED",
                ["registered algorithm version is not allowlisted"],
            )
        if draft["task_type"] not in manifest["task_types"]:
            raise TaskValidationError(
                "TASK_TYPE_MISMATCH",
                ["algorithm does not declare the TaskDraft task type"],
            )
        parameter_errors = list(
            Draft7Validator(manifest["parameter_schema"]).iter_errors(
                draft["parameters"]
            )
        )
        if parameter_errors:
            raise TaskValidationError(
                "PARAMETER_SCHEMA_MISMATCH",
                ["TaskDraft parameters violate AlgorithmManifest parameter_schema"],
            )
        preset = draft["parameters"]["preset"]
        if (
            draft["parameters"]["device"] != draft["resources"]["device"]
            or (
                draft["task_type"] == "acoustic_forward_2d"
                and preset != "forward"
            )
            or (
                draft["task_type"] == "acoustic_fwi_2d"
                and preset == "forward"
            )
        ):
            raise TaskValidationError(
                "TASK_PARAMETER_MISMATCH",
                ["task type, preset, and requested device are inconsistent"],
            )
        input_types = {item["data_type"] for item in manifest["inputs"]}
        if registered_dataset["data_type"] not in input_types:
            raise TaskValidationError(
                "INPUT_TYPE_MISMATCH",
                ["dataset type is not accepted by the registered algorithm"],
            )

        resources = draft["resources"]
        limits = manifest["resource_limits"]
        resource_errors: list[str] = []
        if resources["device"] not in limits["devices"]:
            resource_errors.append("device")
        for field, limit_field in (
            ("gpu_count", "max_gpu_count"),
            ("cpu_cores", "max_cpu_cores"),
            ("memory_mb", "max_memory_mb"),
            ("wall_time_seconds", "max_wall_time_seconds"),
        ):
            if resources[field] > limits[limit_field]:
                resource_errors.append(field)
        if resource_errors:
            raise TaskValidationError(
                "RESOURCE_LIMIT_EXCEEDED",
                [
                    "TaskDraft resources exceed AlgorithmManifest limits: "
                    + ", ".join(resource_errors)
                ],
            )
        return manifest

    @staticmethod
    def _validate_registered_plan(
        plan: Mapping[str, Any], manifest: Mapping[str, Any]
    ) -> None:
        input_ports = {
            item["port"]: item["data_type"] for item in manifest["inputs"]
        }
        output_ports = {
            item["port"]: item["data_type"] for item in manifest["outputs"]
        }
        declared_effects = set(manifest["security"]["side_effects"])
        errors: list[str] = []
        for node in plan["nodes"]:
            node_id = node["node_id"]
            planned_input_ports = [binding["port"] for binding in node["inputs"]]
            if (
                len(planned_input_ports) != len(set(planned_input_ports))
                or set(planned_input_ports) != set(input_ports)
            ):
                errors.append(
                    f"node {node_id} input ports differ from AlgorithmManifest"
                )
            for binding in node["inputs"]:
                expected_type = input_ports.get(binding["port"])
                if expected_type != binding["dataset"]["data_type"]:
                    errors.append(
                        f"node {node_id} input does not match AlgorithmManifest"
                    )
            planned_output_ports = [output["port"] for output in node["outputs"]]
            if (
                len(planned_output_ports) != len(set(planned_output_ports))
                or set(planned_output_ports) != set(output_ports)
            ):
                errors.append(
                    f"node {node_id} output ports differ from AlgorithmManifest"
                )
            for output in node["outputs"]:
                if output_ports.get(output["port"]) != output["data_type"]:
                    errors.append(
                        f"node {node_id} output does not match AlgorithmManifest"
                    )
            if not set(node["side_effects"]).issubset(declared_effects):
                errors.append(
                    f"node {node_id} requests undeclared side effects"
                )
        if errors:
            raise TaskValidationError("PLAN_REGISTRY_MISMATCH", errors)

    def create_task(
        self,
        *,
        project_id: str,
        principal_id: str,
        draft: Mapping[str, Any],
        idempotency_key: str,
    ) -> CreateTaskResult:
        _validate_opaque_id(project_id, field="project_id")
        _validate_opaque_id(principal_id, field="principal_id")
        _validate_idempotency_key(idempotency_key)
        self._validate_schema("task-draft.schema.json", draft)
        if draft["revision"] != 1:
            raise TaskValidationError(
                "INVALID_INITIAL_REVISION", ["a new task draft must start at revision 1"]
            )
        self._validate_registered_draft(
            draft, project_id=project_id, principal_id=principal_id
        )
        _, request_hash = encode_document(
            {
                "project_id": project_id,
                "principal_id": principal_id,
                "draft": dict(draft),
            }
        )
        try:
            replay = self._store.lookup_create_task(
                project_id=project_id,
                principal_id=principal_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
        except IdempotencyConflict as error:
            raise TaskIdempotencyConflict(str(error)) from error
        if replay is not None:
            return CreateTaskResult(snapshot=replay.snapshot, replayed=True)
        task_id = self._task_id_factory()
        _validate_opaque_id(task_id, field="generated task_id")
        try:
            result = self._store.create_task(
                task_id=task_id,
                project_id=project_id,
                principal_id=principal_id,
                draft=draft,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                now=self._clock(),
            )
        except IdempotencyConflict as error:
            raise TaskIdempotencyConflict(str(error)) from error
        except TaskStoreConflict as error:
            raise TaskConflict(str(error)) from error
        return CreateTaskResult(snapshot=result.snapshot, replayed=result.replayed)

    def get_task(
        self, task_id: str, *, project_id: str, principal_id: str
    ) -> TaskSnapshot:
        _validate_opaque_id(task_id, field="task_id")
        _validate_opaque_id(project_id, field="project_id")
        _validate_opaque_id(principal_id, field="principal_id")
        snapshot = self._store.get_task(task_id)
        if (
            snapshot is None
            or snapshot.project_id != project_id
            or snapshot.principal_id != principal_id
        ):
            raise TaskNotFound("task does not exist in the requested scope")
        return snapshot

    def revise_draft(
        self,
        *,
        task_id: str,
        project_id: str,
        principal_id: str,
        expected_revision: int,
        draft: Mapping[str, Any],
        idempotency_key: str | None = None,
    ) -> TaskSnapshot:
        _validate_opaque_id(task_id, field="task_id")
        _validate_opaque_id(project_id, field="project_id")
        _validate_opaque_id(principal_id, field="principal_id")
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool):
            raise TaskValidationError(
                "INVALID_REVISION", ["expected_revision must be an integer"]
            )
        self._validate_schema("task-draft.schema.json", draft)
        request_hash = None
        if idempotency_key is not None:
            _validate_idempotency_key(idempotency_key)
            _, request_hash = encode_document(
                {
                    "task_id": task_id,
                    "project_id": project_id,
                    "principal_id": principal_id,
                    "expected_revision": expected_revision,
                    "draft": dict(draft),
                }
            )
            replay = self._lookup_workbench_replay(
                task_id=task_id,
                project_id=project_id,
                principal_id=principal_id,
                operation="revise_draft",
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if replay is not None:
                return replay
        current = self.get_task(
            task_id, project_id=project_id, principal_id=principal_id
        )
        if current.status not in {"Draft", "NeedsInput", "AwaitingApproval"}:
            raise TaskConflict("draft cannot be revised after the task entered runtime")
        if draft["status"] not in ALLOWED_TRANSITIONS[current.status]:
            raise TaskConflict("invalid draft status transition")
        if draft["draft_id"] != current.draft["draft_id"]:
            raise TaskConflict("draft_id is immutable within a task")
        if draft["revision"] != expected_revision + 1:
            raise TaskConflict("draft revision must increase by exactly one")
        self._validate_registered_draft(
            draft, project_id=project_id, principal_id=principal_id
        )
        try:
            return self._store.append_draft_revision(
                task_id=task_id,
                expected_revision=expected_revision,
                draft=draft,
                now=self._clock(),
                project_id=project_id if idempotency_key is not None else None,
                principal_id=principal_id if idempotency_key is not None else None,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
        except IdempotencyConflict as error:
            raise TaskIdempotencyConflict(str(error)) from error
        except TaskStoreConflict as error:
            raise TaskConflict(str(error)) from error

    def persist_plan(
        self,
        *,
        task_id: str,
        project_id: str,
        principal_id: str,
        plan: Mapping[str, Any],
        idempotency_key: str | None = None,
    ) -> TaskSnapshot:
        _validate_opaque_id(task_id, field="task_id")
        _validate_opaque_id(project_id, field="project_id")
        _validate_opaque_id(principal_id, field="principal_id")
        self._validate_schema("plan-graph.schema.json", plan)
        try:
            expected_hash = compute_plan_hash(plan)
        except ValueError as error:
            raise TaskValidationError("PLAN_CANONICALIZATION_INVALID", [str(error)]) from error
        if plan["plan_hash"] != expected_hash:
            raise TaskValidationError(
                "PLAN_HASH_INVALID", ["plan_hash does not match canonical plan content"]
            )
        request_hash = None
        if idempotency_key is not None:
            _validate_idempotency_key(idempotency_key)
            _, request_hash = encode_document(
                {
                    "task_id": task_id,
                    "project_id": project_id,
                    "principal_id": principal_id,
                    "plan": dict(plan),
                }
            )
            replay = self._lookup_workbench_replay(
                task_id=task_id,
                project_id=project_id,
                principal_id=principal_id,
                operation="persist_plan",
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if replay is not None:
                return replay
        current = self.get_task(
            task_id, project_id=project_id, principal_id=principal_id
        )
        if current.status != "AwaitingApproval":
            raise TaskConflict("plans can only target an AwaitingApproval draft")
        if plan["draft"] != {
            "draft_id": current.draft["draft_id"],
            "revision": current.draft["revision"],
        }:
            raise TaskConflict("plan does not target the current draft revision")
        _validate_plan_draft_consistency(plan, current.draft)
        manifest = self._validate_registered_draft(
            current.draft,
            project_id=project_id,
            principal_id=principal_id,
        )
        self._validate_registered_plan(plan, manifest)
        try:
            return self._store.store_plan(
                task_id=task_id,
                plan=plan,
                now=self._clock(),
                project_id=project_id if idempotency_key is not None else None,
                principal_id=principal_id if idempotency_key is not None else None,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
        except IdempotencyConflict as error:
            raise TaskIdempotencyConflict(str(error)) from error
        except TaskStoreConflict as error:
            raise TaskConflict(str(error)) from error

    def persist_approval(
        self,
        *,
        task_id: str,
        project_id: str,
        principal_id: str,
        approval: Mapping[str, Any],
        idempotency_key: str | None = None,
    ) -> TaskSnapshot:
        _validate_opaque_id(task_id, field="task_id")
        _validate_opaque_id(project_id, field="project_id")
        _validate_opaque_id(principal_id, field="principal_id")
        self._validate_schema("approval-decision.schema.json", approval)
        request_hash = None
        if idempotency_key is not None:
            _validate_idempotency_key(idempotency_key)
            _, request_hash = encode_document(
                {
                    "task_id": task_id,
                    "project_id": project_id,
                    "principal_id": principal_id,
                    "approval": dict(approval),
                }
            )
            replay = self._lookup_workbench_replay(
                task_id=task_id,
                project_id=project_id,
                principal_id=principal_id,
                operation="persist_approval",
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if replay is not None:
                return replay
        current = self.get_task(
            task_id, project_id=project_id, principal_id=principal_id
        )
        if approval["actor"]["type"] != "user":
            raise TaskValidationError(
                "DELEGATED_APPROVAL_UNSUPPORTED",
                ["P1 Guided persistence does not activate agent delegation"],
            )
        if approval["actor"]["id"] != principal_id:
            raise TaskValidationError(
                "APPROVAL_ACTOR_MISMATCH",
                ["approval actor must match the authenticated principal"],
            )
        if current.status != "AwaitingApproval":
            raise TaskConflict("decisions can only target an AwaitingApproval task")
        if current.plan is None:
            raise TaskConflict("task has no current plan")
        if (
            approval["plan_id"] != current.plan["plan_id"]
            or approval["plan_hash"] != current.plan["plan_hash"]
        ):
            raise TaskConflict("approval does not bind the current plan hash")
        try:
            return self._store.store_approval(
                task_id=task_id,
                approval=approval,
                now=self._clock(),
                project_id=project_id if idempotency_key is not None else None,
                principal_id=principal_id if idempotency_key is not None else None,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
        except IdempotencyConflict as error:
            raise TaskIdempotencyConflict(str(error)) from error
        except TaskStoreConflict as error:
            raise TaskConflict(str(error)) from error

    def abandon_task(
        self,
        *,
        task_id: str,
        project_id: str,
        principal_id: str,
        idempotency_key: str,
    ) -> AbandonTaskResult:
        """Persist a user discard without activating P2 runtime cancellation."""

        _validate_opaque_id(task_id, field="task_id")
        _validate_opaque_id(project_id, field="project_id")
        _validate_opaque_id(principal_id, field="principal_id")
        _validate_idempotency_key(idempotency_key)
        _, request_hash = encode_document(
            {
                "task_id": task_id,
                "project_id": project_id,
                "principal_id": principal_id,
                "action": "user_discarded_draft",
            }
        )
        replay = self._lookup_workbench_replay(
            task_id=task_id,
            project_id=project_id,
            principal_id=principal_id,
            operation="abandon_task",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            return AbandonTaskResult(snapshot=replay, replayed=True)
        current = self.get_task(
            task_id, project_id=project_id, principal_id=principal_id
        )
        if current.status not in {"Draft", "NeedsInput", "AwaitingApproval"}:
            raise TaskConflict("only a pre-runtime task can be abandoned")
        now = self._clock()
        abandonment = {
            "schema_version": "1.0.0",
            "task_id": task_id,
            "previous_status": current.status,
            "status": "Cancelled",
            "reason": "user_discarded_draft",
            "actor": {"type": "user", "id": principal_id},
            "abandoned_at": now,
            "extensions": {},
        }
        try:
            snapshot, replayed = self._store.abandon_task(
                task_id=task_id,
                project_id=project_id,
                principal_id=principal_id,
                abandonment=abandonment,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                now=now,
            )
        except IdempotencyConflict as error:
            raise TaskIdempotencyConflict(str(error)) from error
        except TaskStoreConflict as error:
            raise TaskConflict(str(error)) from error
        return AbandonTaskResult(snapshot=snapshot, replayed=replayed)

    @staticmethod
    def _parse_gate_time(value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (AttributeError, ValueError) as error:
            raise TaskDispatchError("SUBMIT_CLOCK_INVALID") from error
        if parsed.tzinfo is None:
            raise TaskDispatchError("SUBMIT_CLOCK_INVALID")
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _expected_dispatch_request(snapshot: TaskSnapshot) -> dict[str, Any]:
        plan = snapshot.plan
        if plan is None or len(plan.get("nodes", [])) != 1:
            raise TaskValidationError(
                "PLAN_CAPABILITY_UNSUPPORTED_IN_P1", ["single_node_required"]
            )
        node = plan["nodes"][0]
        if len(node.get("inputs", [])) != 1:
            raise TaskValidationError(
                "PLAN_CAPABILITY_UNSUPPORTED_IN_P1", ["single_input_required"]
            )
        identity = node["inputs"][0]["dataset"]
        dataset = next(
            (
                value
                for value in snapshot.draft.get("datasets", [])
                if all(
                    value.get(key) == identity.get(key)
                    for key in ("id", "version", "content_hash", "data_type")
                )
            ),
            None,
        )
        if dataset is None:
            raise TaskValidationError(
                "PLAN_CAPABILITY_UNSUPPORTED_IN_P1", ["dataset_binding_invalid"]
            )
        return {
            "task_id": snapshot.task_id,
            "node_id": node["node_id"],
            "plan_hash": plan["plan_hash"],
            "idempotency_key": node["idempotency_key"],
            "project_id": snapshot.project_id,
            "principal_id": snapshot.principal_id,
            "algorithm": copy.deepcopy(node["algorithm"]),
            "dataset": copy.deepcopy(dataset),
            "task_type": plan["task_type"],
            "parameters": copy.deepcopy(node["parameters"]),
            "resources": copy.deepcopy(node["resources"]),
        }

    def _authorize_submit(
        self,
        context: SubmitGateContext,
        *,
        approval_id: str,
        preparation: DispatchPreparation,
        gate_time: datetime,
    ) -> None:
        snapshot = context.snapshot
        if snapshot.plan is None or snapshot.approval is None:
            raise TaskConflict("task has no current plan and approval")
        if snapshot.approval.get("approval_id") != approval_id:
            raise TaskConflict("approval is not current for this task")
        violations = evaluate_execution_gate(
            draft=snapshot.draft,
            plan=snapshot.plan,
            approval=snapshot.approval,
            dataset_registry=context.registry.datasets,
            algorithm_registry=context.registry.algorithms,
            principal_id=snapshot.principal_id,
            project_id=snapshot.project_id,
            approval_tasks_used=context.budget.tasks_used,
            now=gate_time,
        )
        if violations:
            raise TaskValidationError(
                "EXECUTION_GATE_REJECTED",
                [
                    f"{violation.code} {violation.path}: {violation.message}"
                    for violation in violations
                ],
            )

        plan = snapshot.plan
        node = plan["nodes"][0] if len(plan.get("nodes", [])) == 1 else None
        reasons: list[str] = []
        if node is None:
            reasons.append("single_node_required")
        else:
            if node.get("dependencies") != []:
                reasons.append("dependencies_unsupported")
            if node.get("algorithm") != {
                "id": DEEPWAVE_ALGORITHM_ID,
                "version": DEEPWAVE_ALGORITHM_VERSION,
            }:
                reasons.append("algorithm_unsupported")
            if node.get("parameters", {}).get("preset") not in {
                "fwi_smoke",
                "fwi_demo",
            }:
                reasons.append("preset_unsupported")
        if plan.get("task_type") != "acoustic_fwi_2d":
            reasons.append("task_type_unsupported")
        datasets = snapshot.draft.get("datasets", [])
        if (
            len(datasets) != 1
            or datasets[0].get("id") != "marmousi_94_288"
            or datasets[0].get("version") != "1.0.0"
        ):
            reasons.append("dataset_unsupported")
        manifest = context.registry.algorithms.get(
            (DEEPWAVE_ALGORITHM_ID, DEEPWAVE_ALGORITHM_VERSION)
        )
        if self._p1_manifest is None or manifest != self._p1_manifest:
            reasons.append("adapter_binding_mismatch")
        if (
            preparation.adapter_id != "fwi.deepwave_adapter"
            or preparation.adapter_version != self._p1_manifest["adapter"]["version"]
        ):
            reasons.append("dispatcher_unsupported")
        expected_request = self._expected_dispatch_request(snapshot)
        prepared_request = copy.deepcopy(preparation.request)
        normalized_config_hash = prepared_request.pop(
            "normalized_config_hash", None
        )
        if prepared_request != expected_request:
            reasons.append("dispatch_request_drift")
        fingerprint = preparation.queue_fingerprint
        if (
            not isinstance(normalized_config_hash, str)
            or fingerprint.get("provenance_mode") != "development"
            or fingerprint.get("source", {}).get("identity_complete") is not False
            or fingerprint.get("normalized_config_hash") != normalized_config_hash
            or node is None
            or fingerprint.get("algorithm") != node.get("algorithm")
            or fingerprint.get("seed") != node.get("parameters", {}).get("seed")
            or fingerprint.get("hardware", {}).get("device")
            != node.get("resources", {}).get("device")
            or fingerprint.get("input_hashes")
            != [binding["dataset"]["content_hash"] for binding in node.get("inputs", [])]
        ):
            reasons.append("queue_fingerprint_drift")
        if reasons:
            raise TaskValidationError(
                "PLAN_CAPABILITY_UNSUPPORTED_IN_P1", sorted(set(reasons))
            )

    def _build_submit_admission(
        self,
        context: SubmitGateContext,
        *,
        approval_id: str,
        preparation: DispatchPreparation,
        request_hash: str,
        now: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        self._authorize_submit(
            context,
            approval_id=approval_id,
            preparation=preparation,
            gate_time=self._parse_gate_time(now),
        )
        snapshot = context.snapshot
        if snapshot.plan is None:
            raise TaskConflict("task has no current plan")
        node = snapshot.plan["nodes"][0]
        token = request_hash.removeprefix("sha256:")[:32]
        intent = {
            "schema_version": "1.0.0",
            "intent_id": f"dispatch-{token}",
            "task_id": snapshot.task_id,
            "plan_id": snapshot.plan["plan_id"],
            "plan_hash": snapshot.plan["plan_hash"],
            "approval_id": approval_id,
            "node_id": node["node_id"],
            "node_idempotency_key": node["idempotency_key"],
            "adapter": {
                "id": preparation.adapter_id,
                "version": preparation.adapter_version,
            },
            "request": copy.deepcopy(preparation.request),
            "queue_fingerprint": copy.deepcopy(preparation.queue_fingerprint),
            "created_at": now,
        }
        queued_event = {
            "schema_version": "1.0.0",
            "event_id": f"event-queued-{token}",
            "sequence": 1,
            "task_id": snapshot.task_id,
            "event_type": "task_queued",
            "task_status": "Queued",
            "occurred_at": now,
            "fingerprint": copy.deepcopy(preparation.queue_fingerprint),
            "extensions": {
                "agent_rpc.dispatch": {
                    "state": "pending",
                    "fingerprint_basis": "adapter_preflight",
                    "worker_runtime_started": False,
                }
            },
        }
        self._validate_schema("run-event.schema.json", queued_event)
        return intent, queued_event

    def submit_task(
        self,
        *,
        task_id: str,
        project_id: str,
        principal_id: str,
        approval_id: str,
        idempotency_key: str,
    ) -> SubmitTaskResult:
        """Atomically admit one approved FWI node, then dispatch it once."""

        _validate_opaque_id(task_id, field="task_id")
        _validate_opaque_id(project_id, field="project_id")
        _validate_opaque_id(principal_id, field="principal_id")
        _validate_opaque_id(approval_id, field="approval_id")
        _validate_idempotency_key(idempotency_key)
        _, request_hash = encode_document(
            {
                "task_id": task_id,
                "project_id": project_id,
                "principal_id": principal_id,
                "approval_id": approval_id,
            }
        )
        try:
            replay = self._store.lookup_submit_task(
                project_id=project_id,
                principal_id=principal_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
        except IdempotencyConflict as error:
            raise TaskIdempotencyConflict(str(error)) from error
        if replay is not None:
            return SubmitTaskResult(
                snapshot=replay.snapshot,
                intent=replay.intent,
                replayed=True,
                dispatch_attempted=False,
            )
        if self._dispatcher is None:
            raise TaskDispatchError("DISPATCHER_UNAVAILABLE")
        current = self.get_task(
            task_id, project_id=project_id, principal_id=principal_id
        )
        if current.status != "AwaitingApproval":
            raise TaskConflict("task is not awaiting approval")
        if (
            current.approval is None
            or current.approval.get("approval_id") != approval_id
        ):
            raise TaskConflict("approval is not current for this task")
        self._expected_dispatch_request(current)
        try:
            preparation = self._dispatcher.prepare(current)
        except DispatchError as error:
            raise TaskDispatchError(error.code) from error
        except Exception as error:
            raise TaskDispatchError("DISPATCH_PREPARATION_UNAVAILABLE") from error
        try:
            admitted = self._store.submit_task(
                task_id=task_id,
                project_id=project_id,
                principal_id=principal_id,
                approval_id=approval_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                admit=lambda context, now: self._build_submit_admission(
                    context,
                    approval_id=approval_id,
                    preparation=preparation,
                    request_hash=request_hash,
                    now=now,
                ),
                clock=self._clock,
            )
        except IdempotencyConflict as error:
            raise TaskIdempotencyConflict(str(error)) from error
        except TaskStoreConflict as error:
            raise TaskConflict(str(error)) from error
        if admitted.replayed:
            return SubmitTaskResult(
                snapshot=admitted.snapshot,
                intent=admitted.intent,
                replayed=True,
                dispatch_attempted=False,
            )
        try:
            claimed, is_new_claim = self._store.claim_dispatch(
                intent_id=admitted.intent.intent_id, now=self._clock()
            )
        except TaskStoreConflict as error:
            raise TaskConflict(str(error)) from error
        if not is_new_claim:
            return SubmitTaskResult(
                snapshot=admitted.snapshot,
                intent=claimed,
                replayed=False,
                dispatch_attempted=False,
            )
        try:
            handle = self._dispatcher.dispatch(claimed)
        except DispatchError as error:
            try:
                final_intent = self._store.record_dispatch_reconciliation(
                    intent_id=claimed.intent_id,
                    failure_code=error.code,
                    now=self._clock(),
                )
            except TaskStoreConflict as store_error:
                raise TaskConflict(str(store_error)) from store_error
        except Exception:
            try:
                final_intent = self._store.record_dispatch_reconciliation(
                    intent_id=claimed.intent_id,
                    failure_code="DISPATCH_UNAVAILABLE",
                    now=self._clock(),
                )
            except TaskStoreConflict as store_error:
                raise TaskConflict(str(store_error)) from store_error
        else:
            fingerprint = (
                handle.get("fingerprint")
                if isinstance(handle, Mapping)
                else None
            )
            receipt_event = {
                "schema_version": "1.0.0",
                "event_id": "dispatch-receipt-validation",
                "sequence": 2,
                "task_id": claimed.task_id,
                "node_id": claimed.node_id,
                "event_type": "node_started",
                "task_status": "Running",
                "occurred_at": self._clock(),
                "fingerprint": fingerprint,
                "extensions": {},
            }
            try:
                if (
                    not isinstance(handle, Mapping)
                    or not isinstance(fingerprint, Mapping)
                    or handle.get("adapter_version") != claimed.adapter_version
                    or fingerprint.get("adapter_version")
                    != claimed.adapter_version
                    or handle.get("algorithm")
                    != claimed.request.get("algorithm")
                    or fingerprint.get("algorithm")
                    != claimed.request.get("algorithm")
                ):
                    raise TaskValidationError(
                        "DISPATCH_RECEIPT_INVALID",
                        ["receipt identity differs from its immutable intent"],
                    )
                self._validate_schema("run-event.schema.json", receipt_event)
                _validate_run_event_binding(admitted.snapshot, receipt_event)
            except TaskValidationError:
                try:
                    final_intent = self._store.record_dispatch_reconciliation(
                        intent_id=claimed.intent_id,
                        failure_code="DISPATCH_RECEIPT_INVALID",
                        now=self._clock(),
                    )
                except TaskStoreConflict as store_error:
                    raise TaskConflict(str(store_error)) from store_error
            else:
                try:
                    final_intent = self._store.record_dispatch_success(
                        intent_id=claimed.intent_id,
                        handle=handle,
                        now=self._clock(),
                    )
                except TaskStoreConflict as store_error:
                    raise TaskConflict(str(store_error)) from store_error
        return SubmitTaskResult(
            snapshot=admitted.snapshot,
            intent=final_intent,
            replayed=False,
            dispatch_attempted=True,
        )

    def get_dispatch_intent(
        self, task_id: str, *, project_id: str, principal_id: str
    ) -> DispatchIntentSnapshot | None:
        self.get_task(task_id, project_id=project_id, principal_id=principal_id)
        return self._store.get_dispatch_intent(task_id)

    def list_run_events(
        self,
        task_id: str,
        *,
        project_id: str,
        principal_id: str,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if type(after_sequence) is not int or after_sequence < 0:
            raise TaskValidationError(
                "INVALID_EVENT_CURSOR",
                ["after_sequence must be a non-negative integer"],
            )
        if type(limit) is not int or limit < 1 or limit > 1000:
            raise TaskValidationError(
                "INVALID_EVENT_LIMIT", ["limit must be an integer from 1 to 1000"]
            )
        self.get_task(task_id, project_id=project_id, principal_id=principal_id)
        try:
            return self._store.list_run_events(
                task_id, after_sequence=after_sequence, limit=limit
            )
        except TaskStoreConflict as error:
            raise TaskNotFound(str(error)) from error

    def record_run_event(
        self,
        *,
        task_id: str,
        project_id: str,
        principal_id: str,
        expected_status: str,
        event: Mapping[str, Any],
    ) -> TaskSnapshot:
        """Persist a validated post-queue event with its state transition.

        ``task_queued`` remains reserved for :meth:`submit_task`, where the
        Gate, approval budget, intent, idempotency record, event, and state are
        committed atomically.
        """

        _validate_opaque_id(task_id, field="task_id")
        if not isinstance(expected_status, str) or expected_status not in TASK_STATUSES:
            raise TaskValidationError(
                "INVALID_EXPECTED_STATUS",
                ["expected_status must be a known task status"],
            )
        self._validate_schema("run-event.schema.json", event)
        if event["event_type"] == "task_queued":
            raise TaskConflict("task_queued is reserved for the atomic submit path")
        _validate_run_event_semantics(event)
        if expected_status in {"Waiting", "Retrying"} or event["task_status"] in {
            "Waiting",
            "Retrying",
        }:
            raise TaskValidationError(
                "RUN_EVENT_UNSUPPORTED_IN_P1",
                ["Waiting and Retrying state semantics are deferred to P2"],
            )
        if event["task_id"] != task_id:
            raise TaskConflict("event task_id does not match the requested task")
        current = self.get_task(
            task_id, project_id=project_id, principal_id=principal_id
        )
        if current.status in {"Draft", "NeedsInput", "AwaitingApproval"}:
            raise TaskConflict("runtime events cannot be recorded before validated submission")
        if current.status != expected_status:
            raise TaskConflict("task status precondition failed")
        if expected_status not in RUN_EVENT_EXPECTED_STATUS[event["event_type"]]:
            raise TaskValidationError(
                "RUN_EVENT_STATE_MISMATCH",
                [
                    f"{event['event_type']} cannot be appended from "
                    f"expected_status={expected_status}"
                ],
            )
        _validate_run_event_binding(current, event)
        if expected_status == "Queued":
            intent = self._store.get_dispatch_intent(task_id)
            if (
                intent is None
                or intent.state != "dispatched"
                or intent.handle is None
            ):
                raise TaskConflict(
                    "runtime events require a completed dispatch receipt"
                )
            if event.get("node_id") != intent.node_id:
                raise TaskValidationError(
                    "RUN_EVENT_NODE_UNKNOWN",
                    ["event node_id differs from the dispatched P1 node"],
                )
            if event.get("fingerprint") != intent.handle.get("fingerprint"):
                raise TaskValidationError(
                    "RUN_EVENT_FINGERPRINT_MISMATCH",
                    ["event fingerprint differs from the Adapter dispatch receipt"],
                )
        try:
            return self._store.commit_runtime_transition(
                task_id=task_id,
                expected_status=expected_status,
                event=event,
                now=self._clock(),
            )
        except TaskStoreConflict as error:
            raise TaskConflict(str(error)) from error

    @staticmethod
    def _validated_adapter_status(
        intent: DispatchIntentSnapshot, value: Mapping[str, Any]
    ) -> dict[str, Any]:
        required = {
            "job_id",
            "task_id",
            "node_id",
            "status",
            "stage",
            "completed",
            "total",
            "message",
            "updated_at",
            "terminal",
        }
        result = copy.deepcopy(dict(value))
        status = result.get("status")
        if (
            set(result) != required
            or intent.handle is None
            or result.get("job_id") != intent.handle.get("job_id")
            or result.get("task_id") != intent.task_id
            or result.get("node_id") != intent.node_id
            or status not in {"Queued", "Running", "Succeeded", "Failed"}
            or type(result.get("completed")) is not int
            or type(result.get("total")) is not int
            or result["completed"] < 0
            or result["total"] < 0
            or result["completed"] > result["total"]
            or result["total"]
            != intent.request.get("parameters", {}).get("iterations")
            or not isinstance(result.get("stage"), str)
            or not isinstance(result.get("message"), str)
            or len(result["message"]) > 1000
            or type(result.get("terminal")) is not bool
            or result["terminal"] != (status in {"Succeeded", "Failed"})
        ):
            raise TaskDispatchError("ADAPTER_STATUS_INVALID")
        try:
            TaskService._parse_gate_time(result.get("updated_at"))
        except TaskDispatchError as error:
            raise TaskDispatchError("ADAPTER_STATUS_INVALID") from error
        return result

    @staticmethod
    def _adapter_event(
        *,
        snapshot: TaskSnapshot,
        intent: DispatchIntentSnapshot,
        adapter_status: Mapping[str, Any],
        event_type: str,
        sequence: int,
    ) -> dict[str, Any]:
        if intent.handle is None:
            raise TaskDispatchError("DISPATCH_RECEIPT_UNAVAILABLE")
        target_status = {
            "node_started": "Running",
            "node_progress": "Running",
            "node_succeeded": "Succeeded",
            "node_failed": "Failed",
        }[event_type]
        _, identity_hash = encode_document(
            {
                "task_id": snapshot.task_id,
                "node_id": intent.node_id,
                "job_id": adapter_status["job_id"],
                "event_type": event_type,
                "worker_updated_at": adapter_status["updated_at"],
                "stage": adapter_status["stage"],
                "completed": adapter_status["completed"],
                "total": adapter_status["total"],
            }
        )
        event: dict[str, Any] = {
            "schema_version": "1.0.0",
            "event_id": "event-" + identity_hash.removeprefix("sha256:")[:32],
            "sequence": sequence,
            "task_id": snapshot.task_id,
            "node_id": intent.node_id,
            "event_type": event_type,
            "task_status": target_status,
            "occurred_at": adapter_status["updated_at"],
            "fingerprint": copy.deepcopy(intent.handle["fingerprint"]),
            "extensions": {
                "org.agent_rpc.adapter_status": {
                    "job_id": adapter_status["job_id"],
                    "stage": adapter_status["stage"],
                    "worker_updated_at": adapter_status["updated_at"],
                }
            },
        }
        if event_type == "node_progress":
            event["progress"] = {
                "completed": adapter_status["completed"],
                "total": adapter_status["total"],
                "unit": "iterations",
                "message": adapter_status["message"],
            }
        elif event_type == "node_failed":
            event["error"] = {
                "code": "worker_failed",
                "message": "FWI Worker reported a failure",
                "retryable": False,
            }
        return event

    def refresh_runtime_status(
        self, task_id: str, *, project_id: str, principal_id: str
    ) -> TaskRuntimeResult:
        """Observe one trusted Adapter receipt and advance SQLite monotonically."""

        snapshot = self.get_task(
            task_id, project_id=project_id, principal_id=principal_id
        )
        intent = self._store.get_dispatch_intent(task_id)
        if intent is None:
            if snapshot.status in {
                "Draft",
                "NeedsInput",
                "AwaitingApproval",
                "Cancelled",
            }:
                return TaskRuntimeResult(snapshot, None, None)
            raise TaskConflict("runtime task has no dispatch intent")
        if intent.state != "dispatched":
            return TaskRuntimeResult(snapshot, intent, None)
        if self._dispatcher is None:
            raise TaskDispatchError("DISPATCHER_UNAVAILABLE")
        try:
            observed = self._dispatcher.status(intent)
        except DispatchError as error:
            raise TaskDispatchError(error.code) from error
        except Exception as error:
            raise TaskDispatchError("ADAPTER_STATUS_UNAVAILABLE") from error
        adapter_status = self._validated_adapter_status(intent, observed)
        target = adapter_status["status"]

        for _ in range(8):
            snapshot = self.get_task(
                task_id, project_id=project_id, principal_id=principal_id
            )
            events = self.list_run_events(
                task_id,
                project_id=project_id,
                principal_id=principal_id,
                limit=1000,
            )
            event_ids = {event["event_id"] for event in events}
            previous_worker_time = next(
                (
                    event.get("extensions", {})
                    .get("org.agent_rpc.adapter_status", {})
                    .get("worker_updated_at")
                    for event in reversed(events)
                    if isinstance(
                        event.get("extensions", {}).get(
                            "org.agent_rpc.adapter_status"
                        ),
                        Mapping,
                    )
                    and isinstance(
                        event["extensions"]["org.agent_rpc.adapter_status"].get(
                            "worker_updated_at"
                        ),
                        str,
                    )
                ),
                None,
            )
            if (
                previous_worker_time is not None
                and self._parse_gate_time(adapter_status["updated_at"])
                < self._parse_gate_time(previous_worker_time)
            ):
                raise TaskDispatchError("ADAPTER_STATUS_REGRESSION")

            if target == "Queued":
                if snapshot.status != "Queued":
                    raise TaskDispatchError("ADAPTER_STATUS_REGRESSION")
                return TaskRuntimeResult(snapshot, intent, adapter_status)

            if snapshot.status in {"Succeeded", "Failed", "Cancelled"}:
                if snapshot.status != target:
                    raise TaskDispatchError("ADAPTER_STATUS_CONFLICT")
                return TaskRuntimeResult(snapshot, intent, adapter_status)

            if snapshot.status == "Queued" and target in {"Running", "Succeeded"}:
                event_type = "node_started"
            elif target == "Running" and snapshot.status == "Running":
                previous_progress = [
                    event
                    for event in events
                    if event.get("event_type") == "node_progress"
                    and event.get("node_id") == intent.node_id
                ]
                if previous_progress:
                    completed = previous_progress[-1]["progress"]["completed"]
                    if adapter_status["completed"] < completed:
                        raise TaskDispatchError("ADAPTER_PROGRESS_REGRESSION")
                event_type = "node_progress"
            elif target == "Succeeded" and snapshot.status == "Running":
                event_type = "node_succeeded"
            elif target == "Failed" and snapshot.status in {"Queued", "Running"}:
                event_type = "node_failed"
            else:
                raise TaskDispatchError("ADAPTER_STATUS_CONFLICT")

            event = self._adapter_event(
                snapshot=snapshot,
                intent=intent,
                adapter_status=adapter_status,
                event_type=event_type,
                sequence=len(events) + 1,
            )
            if event["event_id"] in event_ids:
                return TaskRuntimeResult(snapshot, intent, adapter_status)
            try:
                self.record_run_event(
                    task_id=task_id,
                    project_id=project_id,
                    principal_id=principal_id,
                    expected_status=snapshot.status,
                    event=event,
                )
            except TaskConflict:
                # Another status poll may have committed the same monotonic
                # observation.  Re-read and prove convergence before failing.
                continue
            if event_type == "node_progress":
                current = self.get_task(
                    task_id, project_id=project_id, principal_id=principal_id
                )
                return TaskRuntimeResult(current, intent, adapter_status)
        raise TaskConflict("concurrent Adapter status updates did not converge")

    @staticmethod
    def _validate_collected_artifacts(
        intent: DispatchIntentSnapshot, manifests: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if intent.handle is None:
            raise TaskDispatchError("DISPATCH_RECEIPT_UNAVAILABLE")
        identifiers: set[str] = set()
        observed_outputs: set[tuple[str, str, str]] = set()
        validated: list[dict[str, Any]] = []
        expected_input = {
            key: intent.request["dataset"][key]
            for key in ("id", "version", "content_hash", "data_type")
        }
        for manifest in manifests:
            errors = schema_errors("artifact-manifest.schema.json", manifest)
            artifact_id = manifest.get("artifact_id")
            extensions = manifest.get("extensions")
            adapter_extension = (
                extensions.get("org.agent_rpc.adapter")
                if isinstance(extensions, Mapping)
                else None
            )
            output_port = (
                adapter_extension.get("output_port")
                if isinstance(adapter_extension, Mapping)
                else None
            )
            if errors:
                raise TaskDispatchError("ADAPTER_ARTIFACT_INVALID")
            if (
                artifact_id in identifiers
                or manifest.get("task_id") != intent.task_id
                or manifest.get("node_id") != intent.node_id
                or manifest.get("fingerprint") != intent.handle.get("fingerprint")
                or manifest.get("lineage", {}).get("plan_hash") != intent.plan_hash
                or manifest.get("lineage", {}).get("algorithm")
                != intent.request.get("algorithm")
                or manifest.get("lineage", {}).get("inputs") != [expected_input]
                or not isinstance(output_port, str)
            ):
                raise TaskDispatchError("ADAPTER_ARTIFACT_INVALID")
            identifiers.add(artifact_id)
            observed_outputs.add(
                (
                    output_port,
                    manifest["artifact_type"],
                    manifest["media_type"],
                )
            )
            validated.append(copy.deepcopy(manifest))
        if len(validated) != 2 or observed_outputs != {
            (
                "inverted_model",
                "inverted_velocity_model_2d",
                "application/x-npy",
            ),
            ("loss", "loss_curve", "text/csv"),
        }:
            raise TaskDispatchError("ADAPTER_ARTIFACT_INVALID")
        return sorted(validated, key=lambda item: item["display"]["order"])

    def collect_artifacts(
        self, task_id: str, *, project_id: str, principal_id: str
    ) -> list[dict[str, Any]]:
        runtime = self.refresh_runtime_status(
            task_id, project_id=project_id, principal_id=principal_id
        )
        if runtime.snapshot.status != "Succeeded" or runtime.intent is None:
            raise TaskDispatchError("RESULT_NOT_READY")
        if self._dispatcher is None:
            raise TaskDispatchError("DISPATCHER_UNAVAILABLE")
        try:
            manifests = self._dispatcher.collect(runtime.intent)
        except DispatchError as error:
            raise TaskDispatchError(error.code) from error
        except Exception as error:
            raise TaskDispatchError("ADAPTER_COLLECT_UNAVAILABLE") from error
        if not isinstance(manifests, list) or not all(
            isinstance(value, dict) for value in manifests
        ):
            raise TaskDispatchError("ADAPTER_ARTIFACT_INVALID")
        return self._validate_collected_artifacts(runtime.intent, manifests)

    def read_artifact(
        self,
        task_id: str,
        artifact_id: str,
        *,
        project_id: str,
        principal_id: str,
    ) -> tuple[dict[str, Any], bytes]:
        _validate_opaque_id(artifact_id, field="artifact_id")
        manifests = self.collect_artifacts(
            task_id, project_id=project_id, principal_id=principal_id
        )
        expected = next(
            (value for value in manifests if value["artifact_id"] == artifact_id),
            None,
        )
        if expected is None:
            raise TaskNotFound("artifact does not exist in the requested task")
        intent = self.get_dispatch_intent(
            task_id, project_id=project_id, principal_id=principal_id
        )
        if intent is None or self._dispatcher is None:
            raise TaskDispatchError("DISPATCH_RECEIPT_UNAVAILABLE")
        try:
            manifest, data = self._dispatcher.read_artifact(intent, artifact_id)
        except DispatchError as error:
            if error.code == "ARTIFACT_NOT_FOUND":
                raise TaskNotFound(
                    "artifact does not exist in the requested task"
                ) from error
            raise TaskDispatchError(error.code) from error
        except Exception as error:
            raise TaskDispatchError("ADAPTER_ARTIFACT_UNAVAILABLE") from error
        if (
            manifest != expected
            or not isinstance(data, bytes)
            or len(data) != expected["size_bytes"]
            or "sha256:" + hashlib.sha256(data).hexdigest()
            != expected["content_hash"]
        ):
            raise TaskDispatchError("ADAPTER_ARTIFACT_INVALID")
        return copy.deepcopy(manifest), data
