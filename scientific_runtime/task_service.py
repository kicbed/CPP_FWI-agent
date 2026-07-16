"""Validated task service with atomic admission and fenced runtime scheduling."""

from __future__ import annotations

import copy
import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

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
from .task_dispatcher import (
    DispatchDeferred,
    DispatchError,
    DispatchPreparation,
    DispatchReceiptProbe,
    DispatchRetryProof,
    TaskDispatcher,
)
from .task_store import (
    ALLOWED_TRANSITIONS,
    TASK_STATUSES,
    DispatchIntentSnapshot,
    IdempotencyConflict,
    RuntimeSupervisorLease,
    RuntimeSupervisorLeaseAcquisition,
    RuntimeSupervisorLeaseLost,
    SubmitGateContext,
    TaskSnapshot,
    TaskSnapshotPage,
    TaskStore,
    TaskStoreConflict,
    encode_document,
)


OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
MAX_RUNTIME_EVENT_SCAN = 100_000
RETRYABLE_FAILURE_CLASSES = frozenset(
    {"pre_running_launch_failure", "worker_exit"}
)

RUN_EVENT_STATUS = {
    "node_started": frozenset({"Running"}),
    "node_progress": frozenset({"Running"}),
    # P1's only executable capability will be one FWI node. Multi-node
    # lifecycle semantics are deferred to the P3 scheduler.
    "node_succeeded": frozenset({"Succeeded"}),
    "node_failed": frozenset({"Failed"}),
    "cancel_requested": frozenset({"Queued", "Running"}),
    "task_cancelled": frozenset({"Cancelled"}),
}

RUN_EVENT_EXPECTED_STATUS = {
    "node_started": frozenset({"Queued"}),
    "node_progress": frozenset({"Running"}),
    "node_succeeded": frozenset({"Running"}),
    "node_failed": frozenset({"Queued", "Running"}),
    "cancel_requested": frozenset({"Queued", "Running"}),
    "task_cancelled": frozenset({"Queued", "Running"}),
}

P2_EVENT_TYPES = frozenset(
    {
        "checkpoint_created",
        "node_waiting",
        "node_retrying",
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


class TaskSupervisorLeaseLost(TaskServiceError):
    """The background supervisor no longer owns its exact fenced term."""

    code = "RUNTIME_SUPERVISOR_LEASE_LOST"

    def __init__(self) -> None:
        super().__init__(self.code)


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
class TaskVisibilityResult:
    snapshot: TaskSnapshot
    replayed: bool


@dataclass(frozen=True)
class TaskPurgeResult:
    """Stable public outcome for one irreversible local task purge."""

    task_id: str
    purge_id: str
    purge_state: str
    purged_at: str
    local_run_state: str
    audit_retained: bool
    replayed: bool


@dataclass(frozen=True)
class TaskRuntimeResult:
    snapshot: TaskSnapshot
    intent: DispatchIntentSnapshot | None
    adapter_status: dict[str, Any] | None


@dataclass(frozen=True)
class TaskWorkerProjectionResult:
    """One Supervisor-only Worker evidence projection outcome."""

    intent: DispatchIntentSnapshot
    evidence: dict[str, Any] | None
    projected: bool
    adopted: bool
    replayed: bool
    attempt_id: str | None = None
    observation_sequence: int | None = None
    document_hash: str | None = None
    deferred_code: str | None = None
    timeout_armed: bool = False


@dataclass(frozen=True)
class TaskScheduleResult:
    """One Supervisor-owned pass through the first-dispatch state machine."""

    intent: DispatchIntentSnapshot
    authorized: bool
    authorization_replayed: bool
    dispatch_attempted: bool
    projected: bool
    adopted: bool
    deferred_code: str | None = None
    timeout_armed: bool = False


@dataclass(frozen=True)
class TaskDispatchReconciliationResult:
    """One bounded, zero-launch reconciliation observation pass."""

    intent: DispatchIntentSnapshot
    evidence_kind: str | None
    authorized: bool
    authorization_replayed: bool
    probe_attempted: bool
    projected: bool
    adopted: bool
    deferred_code: str | None = None
    timeout_armed: bool = False


@dataclass(frozen=True)
class TaskCancellationResult:
    """Public admission result for one durable exact-attempt cancellation."""

    snapshot: TaskSnapshot
    replayed: bool


@dataclass(frozen=True)
class TaskCancellationProcessResult:
    """One Supervisor-owned cancellation delivery/finalization pass."""

    snapshot: TaskSnapshot
    state: str
    adapter_result: dict[str, Any] | None
    replayed: bool
    deferred_code: str | None = None


@dataclass(frozen=True)
class TaskTimeoutProcessResult:
    """One Supervisor-owned exact-attempt timeout enforcement pass."""

    snapshot: TaskSnapshot
    state: str
    adapter_result: dict[str, Any] | None
    replayed: bool
    deferred_code: str | None = None


@dataclass(frozen=True)
class RuntimeRecoveryResult:
    """Bounded outcome of one scope-wide startup recovery pass."""

    project_id: str
    principal_id: str
    scanned_task_ids: tuple[str, ...]
    receipt_recovery_attempted_task_ids: tuple[str, ...]
    receipt_recovered_task_ids: tuple[str, ...]
    pending_deferred_task_ids: tuple[str, ...]
    dispatching_deferred: tuple[tuple[str, str], ...]
    status_refreshed_task_ids: tuple[str, ...]
    status_refresh_failures: tuple[tuple[str, str], ...]
    reconciliation_required_task_ids: tuple[str, ...]


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


def _validate_approval_retry_policy(
    approval: Mapping[str, Any], plan: Mapping[str, Any]
) -> None:
    """Keep historical approvals single-attempt and bind v1.1 cumulative budget."""

    version = approval.get("schema_version")
    scope = approval.get("scope")
    policy = scope.get("retry_policy") if isinstance(scope, Mapping) else None
    if version == "1.0.0":
        if policy is not None:
            raise TaskValidationError(
                "APPROVAL_RETRY_POLICY_INVALID",
                ["ApprovalDecision 1.0 cannot grant retry attempts"],
            )
        return
    if version != "1.1.0":
        raise TaskValidationError(
            "APPROVAL_VERSION_UNSUPPORTED",
            ["ApprovalDecision must use schema_version 1.0.0 or 1.1.0"],
        )
    nodes = plan.get("nodes")
    resources = scope.get("resource_limits") if isinstance(scope, Mapping) else None
    if (
        not isinstance(policy, Mapping)
        or not isinstance(nodes, list)
        or len(nodes) != 1
        or not isinstance(nodes[0], Mapping)
        or nodes[0].get("algorithm")
        != {
            "id": DEEPWAVE_ALGORITHM_ID,
            "version": DEEPWAVE_ALGORITHM_VERSION,
        }
        or not isinstance(nodes[0].get("resources"), Mapping)
        or not isinstance(resources, Mapping)
    ):
        raise TaskValidationError(
            "APPROVAL_RETRY_POLICY_INVALID",
            [
                "retry policy requires one current deepwave.acoustic_fwi@1.5.0 "
                "resource-bound plan node"
            ],
        )
    plan_resources = nodes[0]["resources"]
    wall_time = plan_resources.get("wall_time_seconds")
    if resources != plan_resources or type(wall_time) is not int:
        raise TaskValidationError(
            "APPROVAL_RETRY_POLICY_INVALID",
            ["retry resources must exactly match the approved plan node"],
        )
    failures = policy.get("retryable_failure_classes")
    if (
        set(policy)
        != {
            "max_attempts",
            "max_concurrent_attempts",
            "max_cumulative_attempt_wall_time_seconds",
            "retryable_failure_classes",
        }
        or policy.get("max_attempts") != 2
        or policy.get("max_concurrent_attempts") != 1
        or policy.get("max_cumulative_attempt_wall_time_seconds") != 2 * wall_time
        or not isinstance(failures, list)
        or len(failures) != 2
        or frozenset(failures) != RETRYABLE_FAILURE_CLASSES
    ):
        raise TaskValidationError(
            "APPROVAL_RETRY_POLICY_INVALID",
            [
                "retry policy must bind two serial attempts, cumulative 2W, "
                "and the fixed failure allowlist"
            ],
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

    @staticmethod
    def _require_not_purged(snapshot: TaskSnapshot) -> TaskSnapshot:
        """Keep a purge reservation from being bypassed by stale replays."""

        if snapshot.purge_id is not None:
            raise TaskNotFound("task has been permanently deleted")
        return snapshot

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
            return CreateTaskResult(
                snapshot=self._require_not_purged(replay.snapshot), replayed=True
            )
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
        return CreateTaskResult(
            snapshot=self._require_not_purged(result.snapshot),
            replayed=result.replayed,
        )

    def lookup_compatible_create_task(
        self,
        *,
        project_id: str,
        principal_id: str,
        drafts: Sequence[Mapping[str, Any]],
        idempotency_key: str,
    ) -> CreateTaskResult | None:
        """Replay a create only when a validated historical request hash matches.

        This is a read-only upgrade bridge.  It never creates from a historical
        candidate and therefore cannot reopen old Algorithm versions for new
        submissions.
        """

        _validate_opaque_id(project_id, field="project_id")
        _validate_opaque_id(principal_id, field="principal_id")
        _validate_idempotency_key(idempotency_key)
        if (
            isinstance(drafts, (str, bytes, Mapping))
            or not isinstance(drafts, Sequence)
            or not 1 <= len(drafts) <= 8
        ):
            raise TaskValidationError(
                "INVALID_COMPATIBILITY_CANDIDATES",
                ["historical create candidates must contain 1-8 drafts"],
            )
        request_hashes: list[str] = []
        for draft in drafts:
            if not isinstance(draft, Mapping):
                raise TaskValidationError(
                    "INVALID_COMPATIBILITY_CANDIDATES",
                    ["historical create candidates must be TaskDraft objects"],
                )
            self._validate_schema("task-draft.schema.json", draft)
            if draft["revision"] != 1:
                raise TaskValidationError(
                    "INVALID_INITIAL_REVISION",
                    ["a historical create candidate must start at revision 1"],
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
            request_hashes.append(request_hash)
        if len(set(request_hashes)) != len(request_hashes):
            raise TaskValidationError(
                "INVALID_COMPATIBILITY_CANDIDATES",
                ["historical create candidates must have distinct request identities"],
            )
        try:
            replay = self._store.lookup_compatible_create_task(
                project_id=project_id,
                principal_id=principal_id,
                idempotency_key=idempotency_key,
                request_hashes=request_hashes,
            )
        except IdempotencyConflict as error:
            raise TaskIdempotencyConflict(str(error)) from error
        except TaskStoreConflict as error:
            raise TaskConflict(str(error)) from error
        if replay is None:
            return None
        return CreateTaskResult(
            snapshot=self._require_not_purged(replay.snapshot), replayed=True
        )

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
        return self._require_not_purged(snapshot)

    def list_tasks(
        self,
        *,
        project_id: str,
        principal_id: str,
        cursor: str | None = None,
        limit: int = 20,
        view: str = "active",
    ) -> TaskSnapshotPage:
        """Return a strict, scope-bound page without refreshing runtime state."""

        _validate_opaque_id(project_id, field="project_id")
        _validate_opaque_id(principal_id, field="principal_id")
        if cursor is not None and (
            not isinstance(cursor, str) or OPAQUE_ID.fullmatch(cursor) is None
        ):
            raise TaskValidationError(
                "INVALID_TASK_CURSOR",
                ["cursor must be a v1 opaque task identifier"],
            )
        if type(limit) is not int or not 1 <= limit <= 50:
            raise TaskValidationError(
                "INVALID_TASK_LIST_LIMIT",
                ["limit must be an integer from 1 to 50"],
            )
        if view not in {"active", "trash"}:
            raise TaskValidationError(
                "INVALID_TASK_LIST_VIEW",
                ["view must be active or trash"],
            )
        try:
            return self._store.list_tasks(
                project_id=project_id,
                principal_id=principal_id,
                cursor=cursor,
                limit=limit,
                view=view,
            )
        except TaskStoreConflict as error:
            # A missing and a cross-scope cursor are deliberately
            # indistinguishable to callers.
            raise TaskValidationError(
                "INVALID_TASK_CURSOR",
                ["cursor does not identify a task in the requested scope"],
            ) from error

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

    def lookup_compatible_draft_revision(
        self,
        *,
        task_id: str,
        project_id: str,
        principal_id: str,
        expected_revision: int,
        drafts: Sequence[Mapping[str, Any]],
        idempotency_key: str,
    ) -> TaskSnapshot | None:
        """Replay one historical revision only through its exact ledger hash."""

        _validate_opaque_id(task_id, field="task_id")
        _validate_opaque_id(project_id, field="project_id")
        _validate_opaque_id(principal_id, field="principal_id")
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool):
            raise TaskValidationError(
                "INVALID_REVISION", ["expected_revision must be an integer"]
            )
        _validate_idempotency_key(idempotency_key)
        if (
            isinstance(drafts, (str, bytes, Mapping))
            or not isinstance(drafts, Sequence)
            or not 1 <= len(drafts) <= 8
        ):
            raise TaskValidationError(
                "INVALID_COMPATIBILITY_CANDIDATES",
                ["historical revision candidates must contain 1-8 drafts"],
            )
        request_hashes: list[str] = []
        candidate_identity: tuple[str, int] | None = None
        for draft in drafts:
            if not isinstance(draft, Mapping):
                raise TaskValidationError(
                    "INVALID_COMPATIBILITY_CANDIDATES",
                    ["historical revision candidates must be TaskDraft objects"],
                )
            self._validate_schema("task-draft.schema.json", draft)
            identity = (draft["draft_id"], draft["revision"])
            if draft["revision"] != expected_revision + 1:
                raise TaskValidationError(
                    "INVALID_REVISION",
                    ["historical draft revision must increase by exactly one"],
                )
            if candidate_identity is None:
                candidate_identity = identity
            elif candidate_identity != identity:
                raise TaskValidationError(
                    "INVALID_COMPATIBILITY_CANDIDATES",
                    ["historical revision candidates must share one draft identity"],
                )
            self._validate_registered_draft(
                draft, project_id=project_id, principal_id=principal_id
            )
            _, request_hash = encode_document(
                {
                    "task_id": task_id,
                    "project_id": project_id,
                    "principal_id": principal_id,
                    "expected_revision": expected_revision,
                    "draft": dict(draft),
                }
            )
            request_hashes.append(request_hash)
        if len(set(request_hashes)) != len(request_hashes):
            raise TaskValidationError(
                "INVALID_COMPATIBILITY_CANDIDATES",
                ["historical revision candidates must have distinct request identities"],
            )
        try:
            replay = self._store.lookup_compatible_workbench_mutation(
                project_id=project_id,
                principal_id=principal_id,
                operation="revise_draft",
                idempotency_key=idempotency_key,
                request_hashes=request_hashes,
            )
        except IdempotencyConflict as error:
            raise TaskIdempotencyConflict(str(error)) from error
        except TaskStoreConflict as error:
            raise TaskConflict(str(error)) from error
        if replay is None:
            return None
        expected_outcome = {
            "task_id": task_id,
            "draft_id": candidate_identity[0],
            "draft_revision": candidate_identity[1],
        }
        if replay.task_id != task_id or replay.outcome != expected_outcome:
            raise TaskIdempotencyConflict(
                "idempotency key identifies another task or draft revision"
            )
        return self.get_task(
            task_id, project_id=project_id, principal_id=principal_id
        )

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
        _validate_approval_retry_policy(approval, current.plan)
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
        if (
            current.approval is not None
            and current.approval.get("decision") == "approved"
        ):
            raise TaskConflict(
                "an approved task cannot be abandoned before submit reconciliation"
            )
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

    def cancel_task(
        self,
        *,
        task_id: str,
        project_id: str,
        principal_id: str,
        reason: str,
        idempotency_key: str,
    ) -> TaskCancellationResult:
        """Durably request cancellation of one exact managed Worker attempt.

        This admission path performs only a read-only exact-capability probe;
        it never publishes an Adapter request or signals a PID.  Delivery and
        terminal resolution belong exclusively to an active, fenced
        :class:`RuntimeSupervisor` term.
        """

        _validate_opaque_id(task_id, field="task_id")
        _validate_opaque_id(project_id, field="project_id")
        _validate_opaque_id(principal_id, field="principal_id")
        _validate_idempotency_key(idempotency_key)
        if reason != "user_requested":
            raise TaskValidationError(
                "INVALID_CANCEL_REASON",
                ["reason must be user_requested"],
            )
        identity = "\x1f".join((project_id, principal_id, task_id))
        request_id = "cancel-" + hashlib.sha256(
            identity.encode("utf-8")
        ).hexdigest()[:32]
        _, request_hash = encode_document(
            {
                "task_id": task_id,
                "project_id": project_id,
                "principal_id": principal_id,
                "action": "cancel_task",
                "reason": reason,
            }
        )
        try:
            replay = self._store.lookup_task_cancellation(
                project_id=project_id,
                principal_id=principal_id,
                idempotency_key=idempotency_key,
                request_id=request_id,
                task_id=task_id,
                reason=reason,
                request_hash=request_hash,
            )
        except IdempotencyConflict as error:
            raise TaskIdempotencyConflict(str(error)) from error
        except TaskStoreConflict as error:
            raise TaskConflict(str(error)) from error
        if replay is not None:
            return TaskCancellationResult(
                snapshot=replay.snapshot,
                replayed=True,
            )
        current = self.get_task(
            task_id, project_id=project_id, principal_id=principal_id
        )
        if current.status in {"Draft", "NeedsInput", "AwaitingApproval"}:
            raise TaskConflict(
                "pre-runtime tasks must use the abandon operation"
            )
        if current.status not in {"Queued", "Running"}:
            raise TaskConflict("only a queued or running task can be cancelled")
        current_timeout = getattr(current, "timeout", None)
        if (
            current_timeout is not None
            and getattr(current_timeout, "state", None) != "armed"
        ):
            raise TaskConflict(
                "automatic timeout already owns this exact Worker attempt"
            )
        intent = self._store.get_dispatch_intent(task_id)
        candidate = self._store.get_task_cancel_candidate(task_id)
        if (
            intent is None
            or candidate is None
            or self._dispatcher is None
        ):
            raise TaskConflict(
                "task cancellation requires an exact managed Worker capability"
            )
        try:
            supports_cancel = self._dispatcher.supports_exact_cancel(
                intent, attempt_id=candidate["attempt_id"]
            )
        except DispatchError as error:
            raise TaskDispatchError(error.code) from error
        except Exception as error:
            raise TaskDispatchError("ADAPTER_CANCEL_CAPABILITY_UNAVAILABLE") from error
        if supports_cancel is not True:
            raise TaskConflict(
                "task cancellation is unavailable for this exact Worker attempt"
            )
        def build_documents(
            snapshot: TaskSnapshot,
            intent: DispatchIntentSnapshot,
            evidence: Mapping[str, Any],
            sequence: int,
            requested_at: str,
        ) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
            if intent.handle is None:
                raise TaskConflict(
                    "task cancellation requires a dispatched receipt"
                )
            request = {
                "schema_version": "1.0.0",
                "request_id": request_id,
                "task_id": task_id,
                "intent_id": intent.intent_id,
                "attempt_id": evidence["attempt_id"],
                "reason": reason,
                "actor": {"type": "user", "id": principal_id},
                "requested_at": requested_at,
                "extensions": {},
            }
            _, event_identity = encode_document(
                {
                    "request_id": request_id,
                    "event_type": "cancel_requested",
                    "sequence": sequence,
                }
            )
            event = {
                "schema_version": "1.0.0",
                "event_id": "event-"
                + event_identity.removeprefix("sha256:")[:32],
                "sequence": sequence,
                "task_id": task_id,
                "node_id": intent.node_id,
                "event_type": "cancel_requested",
                "task_status": snapshot.status,
                "occurred_at": requested_at,
                "fingerprint": copy.deepcopy(intent.handle["fingerprint"]),
                "extensions": {
                    "org.agent_rpc.cancellation": {
                        "request_id": request_id,
                        "attempt_id": evidence["attempt_id"],
                        "reason": reason,
                    }
                },
            }
            self._validate_schema("run-event.schema.json", event)
            _validate_run_event_semantics(event)
            _validate_run_event_binding(snapshot, event)
            return request, event

        try:
            record = self._store.request_task_cancellation(
                task_id=task_id,
                project_id=project_id,
                principal_id=principal_id,
                request_id=request_id,
                reason=reason,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                build_documents=build_documents,
                clock=self._clock,
            )
        except IdempotencyConflict as error:
            raise TaskIdempotencyConflict(str(error)) from error
        except TaskStoreConflict as error:
            raise TaskConflict(str(error)) from error
        return TaskCancellationResult(
            snapshot=record.snapshot,
            replayed=record.replayed,
        )

    def _change_task_visibility(
        self,
        *,
        operation: str,
        task_id: str,
        project_id: str,
        principal_id: str,
        expected_visibility_revision: int,
        idempotency_key: str,
    ) -> TaskVisibilityResult:
        _validate_opaque_id(task_id, field="task_id")
        _validate_opaque_id(project_id, field="project_id")
        _validate_opaque_id(principal_id, field="principal_id")
        _validate_idempotency_key(idempotency_key)
        if (
            type(expected_visibility_revision) is not int
            or not 0 <= expected_visibility_revision <= 2**63 - 1
        ):
            raise TaskValidationError(
                "INVALID_VISIBILITY_REVISION",
                ["expected_visibility_revision must be a non-negative integer"],
            )
        current = self.get_task(
            task_id, project_id=project_id, principal_id=principal_id
        )
        if operation == "trash_task" and current.status not in {
            "Succeeded",
            "Failed",
            "Cancelled",
        }:
            raise TaskConflict("only a terminal task can be moved to trash")
        _, request_hash = encode_document(
            {
                "task_id": task_id,
                "project_id": project_id,
                "principal_id": principal_id,
                "action": operation,
                "expected_visibility_revision": expected_visibility_revision,
            }
        )
        try:
            result = self._store.change_task_visibility(
                task_id=task_id,
                project_id=project_id,
                principal_id=principal_id,
                operation=operation,
                expected_visibility_revision=expected_visibility_revision,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                now=self._clock(),
            )
        except IdempotencyConflict as error:
            raise TaskIdempotencyConflict(str(error)) from error
        except TaskStoreConflict as error:
            raise TaskConflict(str(error)) from error
        return TaskVisibilityResult(
            snapshot=result.snapshot, replayed=result.replayed
        )

    def trash_task(
        self,
        *,
        task_id: str,
        project_id: str,
        principal_id: str,
        expected_visibility_revision: int,
        idempotency_key: str,
    ) -> TaskVisibilityResult:
        return self._change_task_visibility(
            operation="trash_task",
            task_id=task_id,
            project_id=project_id,
            principal_id=principal_id,
            expected_visibility_revision=expected_visibility_revision,
            idempotency_key=idempotency_key,
        )

    def restore_task(
        self,
        *,
        task_id: str,
        project_id: str,
        principal_id: str,
        expected_visibility_revision: int,
        idempotency_key: str,
    ) -> TaskVisibilityResult:
        return self._change_task_visibility(
            operation="restore_task",
            task_id=task_id,
            project_id=project_id,
            principal_id=principal_id,
            expected_visibility_revision=expected_visibility_revision,
            idempotency_key=idempotency_key,
        )

    @staticmethod
    def _validated_purge_outcome(
        outcome: Mapping[str, Any],
        *,
        task_id: str,
        purge_id: str,
        replayed: bool,
    ) -> TaskPurgeResult:
        expected_fields = {
            "task_id",
            "purge_id",
            "purge_state",
            "purged_at",
            "local_run_state",
            "audit_retained",
        }
        purged_at = outcome.get("purged_at")
        try:
            parsed = datetime.fromisoformat(
                purged_at.replace("Z", "+00:00")
            )
        except (AttributeError, ValueError) as error:
            raise TaskDispatchError("TASK_PURGE_OUTCOME_INVALID") from error
        if (
            set(outcome) != expected_fields
            or outcome.get("task_id") != task_id
            or outcome.get("purge_id") != purge_id
            or outcome.get("purge_state") != "purged"
            or parsed.tzinfo is None
            or outcome.get("local_run_state") not in {"deleted", "not_created"}
            or outcome.get("audit_retained") is not True
            or type(replayed) is not bool
        ):
            raise TaskDispatchError("TASK_PURGE_OUTCOME_INVALID")
        return TaskPurgeResult(
            task_id=task_id,
            purge_id=purge_id,
            purge_state="purged",
            purged_at=purged_at,
            local_run_state=outcome["local_run_state"],
            audit_retained=True,
            replayed=replayed,
        )

    def purge_task(
        self,
        *,
        task_id: str,
        project_id: str,
        principal_id: str,
        expected_visibility_revision: int,
        idempotency_key: str,
    ) -> TaskPurgeResult:
        """Irreversibly delete one trashed terminal task's local run data.

        SQLite first reserves an immutable purge tombstone.  That reservation
        prevents restore, status, and artifact reads while the Adapter removes
        the Worker-owned directory.  A retry continues the same reservation,
        so a crash between filesystem cleanup and outcome persistence cannot
        launch the task again or make it visible.
        """

        _validate_opaque_id(task_id, field="task_id")
        _validate_opaque_id(project_id, field="project_id")
        _validate_opaque_id(principal_id, field="principal_id")
        _validate_idempotency_key(idempotency_key)
        if (
            type(expected_visibility_revision) is not int
            or not 0 <= expected_visibility_revision <= 2**63 - 1
        ):
            raise TaskValidationError(
                "INVALID_VISIBILITY_REVISION",
                ["expected_visibility_revision must be a non-negative integer"],
            )
        scoped_snapshot = self._store.get_task(task_id)
        if (
            scoped_snapshot is None
            or scoped_snapshot.project_id != project_id
            or scoped_snapshot.principal_id != principal_id
        ):
            raise TaskNotFound("task does not exist in the requested scope")
        _, request_hash = encode_document(
            {
                "task_id": task_id,
                "project_id": project_id,
                "principal_id": principal_id,
                "action": "purge_task",
                "expected_visibility_revision": expected_visibility_revision,
            }
        )
        try:
            reservation = self._store.reserve_task_purge(
                task_id=task_id,
                project_id=project_id,
                principal_id=principal_id,
                expected_visibility_revision=expected_visibility_revision,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                now=self._clock(),
            )
        except IdempotencyConflict as error:
            raise TaskIdempotencyConflict(str(error)) from error
        except TaskStoreConflict as error:
            raise TaskConflict(str(error)) from error

        snapshot = reservation.snapshot
        if (
            snapshot.task_id != task_id
            or snapshot.project_id != project_id
            or snapshot.principal_id != principal_id
            or snapshot.visibility_revision != expected_visibility_revision
            or snapshot.status not in {"Succeeded", "Failed", "Cancelled"}
            or snapshot.trashed_at is None
            or snapshot.purge_id != reservation.purge_id
            or snapshot.purge_requested_at is None
            or type(reservation.replayed) is not bool
        ):
            raise TaskDispatchError("TASK_PURGE_RESERVATION_INVALID")
        if reservation.outcome is not None:
            if not isinstance(reservation.outcome, Mapping):
                raise TaskDispatchError("TASK_PURGE_OUTCOME_INVALID")
            return self._validated_purge_outcome(
                reservation.outcome,
                task_id=task_id,
                purge_id=reservation.purge_id,
                replayed=True,
            )

        intent = self._store.get_dispatch_intent(task_id)
        adapter_replayed = False
        if intent is None:
            abandonment = snapshot.abandonment
            if (
                snapshot.status != "Cancelled"
                or not isinstance(abandonment, Mapping)
                or abandonment.get("reason") != "user_discarded_draft"
            ):
                raise TaskDispatchError("DISPATCH_RECEIPT_UNAVAILABLE")
            local_run_state = "not_created"
        else:
            if self._dispatcher is None:
                raise TaskDispatchError("DISPATCHER_UNAVAILABLE")
            if intent.task_id != task_id:
                raise TaskDispatchError("DISPATCH_RECEIPT_UNAVAILABLE")
            try:
                if intent.state == "retry_exhausted" and intent.handle is None:
                    exhaustion = (
                        self._store.get_retry_exhaustion_cleanup_proof(
                            purge_id=reservation.purge_id,
                            task_id=task_id,
                            project_id=project_id,
                            principal_id=principal_id,
                        )
                    )
                    if exhaustion is None:
                        raise TaskDispatchError(
                            "WORKER_RETRY_EXHAUSTION_PURGE_UNAVAILABLE"
                        )
                    adapter_result = self._dispatcher.purge_retry_exhausted(
                        intent,
                        purge_id=reservation.purge_id,
                        exhaustion=exhaustion,
                    )
                elif intent.state == "dispatched" and intent.handle is not None:
                    adapter_result = self._dispatcher.purge(
                        intent, purge_id=reservation.purge_id
                    )
                else:
                    raise TaskDispatchError("DISPATCH_RECEIPT_UNAVAILABLE")
            except DispatchError as error:
                raise TaskDispatchError(error.code) from error
            except TaskStoreConflict as error:
                raise TaskConflict(str(error)) from error
            except TaskDispatchError:
                raise
            except Exception as error:
                raise TaskDispatchError("ADAPTER_PURGE_UNAVAILABLE") from error
            if (
                not isinstance(adapter_result, Mapping)
                or set(adapter_result)
                != {"task_id", "purge_id", "local_run_state", "replayed"}
                or adapter_result.get("task_id") != task_id
                or adapter_result.get("purge_id") != reservation.purge_id
                or adapter_result.get("local_run_state") != "deleted"
                or type(adapter_result.get("replayed")) is not bool
            ):
                raise TaskDispatchError("ADAPTER_PURGE_INVALID")
            local_run_state = "deleted"
            adapter_replayed = adapter_result["replayed"]

        try:
            completed = self._store.complete_task_purge(
                purge_id=reservation.purge_id,
                task_id=task_id,
                project_id=project_id,
                principal_id=principal_id,
                local_run_state=local_run_state,
                now=self._clock(),
            )
        except TaskStoreConflict as error:
            raise TaskConflict(str(error)) from error
        if (
            completed.purge_id != reservation.purge_id
            or completed.snapshot.task_id != task_id
            or completed.snapshot.purged_at is None
            or completed.snapshot.purge_local_run_state != local_run_state
            or not isinstance(completed.outcome, Mapping)
            or type(completed.replayed) is not bool
        ):
            raise TaskDispatchError("TASK_PURGE_OUTCOME_INVALID")
        return self._validated_purge_outcome(
            completed.outcome,
            task_id=task_id,
            purge_id=reservation.purge_id,
            replayed=(
                reservation.replayed
                or adapter_replayed
                or completed.replayed
            ),
        )

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
    def _validate_runtime_supervisor_lease_seconds(lease_seconds: int) -> None:
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
            raise TaskValidationError(
                "INVALID_RUNTIME_SUPERVISOR_LEASE",
                ["lease_seconds must be an integer from 1 to 3600"],
            )

    def _runtime_supervisor_clock(self) -> str:
        """Return canonical UTC when called inside the Store write transaction."""

        value = self._clock()
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (AttributeError, ValueError) as error:
            raise TaskDispatchError("RUNTIME_SUPERVISOR_CLOCK_INVALID") from error
        if parsed.tzinfo is None:
            raise TaskDispatchError("RUNTIME_SUPERVISOR_CLOCK_INVALID")
        parsed = parsed.astimezone(timezone.utc)
        return parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")

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

    def _validate_dispatch_receipt(
        self,
        *,
        snapshot: TaskSnapshot,
        intent: DispatchIntentSnapshot,
        handle: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Apply the one receipt boundary shared by submit and recovery."""

        if not isinstance(handle, Mapping):
            raise TaskValidationError(
                "DISPATCH_RECEIPT_INVALID",
                ["receipt must be an immutable handle object"],
            )
        try:
            validated_handle = copy.deepcopy(dict(handle))
        except Exception as error:
            raise TaskValidationError(
                "DISPATCH_RECEIPT_INVALID",
                ["receipt must be an immutable handle object"],
            ) from error
        fingerprint = validated_handle.get("fingerprint")
        receipt_event = {
            "schema_version": "1.0.0",
            "event_id": "dispatch-receipt-validation",
            "sequence": 2,
            "task_id": intent.task_id,
            "node_id": intent.node_id,
            "event_type": "node_started",
            "task_status": "Running",
            "occurred_at": self._clock(),
            "fingerprint": fingerprint,
            "extensions": {},
        }
        self._validate_schema("run-event.schema.json", receipt_event)
        required_handle_fields = {
            "submission_id",
            "task_id",
            "node_id",
            "job_id",
            "idempotency_key",
            "plan_hash",
            "request_hash",
            "algorithm",
            "adapter_version",
            "fingerprint",
        }
        request = intent.request
        if (
            set(validated_handle) != required_handle_fields
            or not isinstance(fingerprint, Mapping)
            or validated_handle.get("task_id") != intent.task_id
            or validated_handle.get("node_id") != intent.node_id
            or validated_handle.get("idempotency_key")
            != intent.node_idempotency_key
            or validated_handle.get("plan_hash") != intent.plan_hash
            or validated_handle.get("adapter_version") != intent.adapter_version
            or fingerprint.get("adapter_version") != intent.adapter_version
            or validated_handle.get("algorithm") != request.get("algorithm")
            or fingerprint.get("algorithm") != request.get("algorithm")
            or fingerprint.get("seed") != request.get("parameters", {}).get("seed")
            or fingerprint.get("hardware", {}).get("device")
            != request.get("resources", {}).get("device")
            or fingerprint.get("normalized_config_hash")
            != request.get("normalized_config_hash")
            or fingerprint.get("input_hashes")
            != [request.get("dataset", {}).get("content_hash")]
            or not isinstance(validated_handle.get("submission_id"), str)
            or not isinstance(validated_handle.get("job_id"), str)
            or not isinstance(validated_handle.get("request_hash"), str)
        ):
            raise TaskValidationError(
                "DISPATCH_RECEIPT_INVALID",
                ["receipt identity differs from its immutable intent"],
            )
        _validate_run_event_binding(snapshot, receipt_event)
        return validated_handle

    def _record_dispatch_reconciliation(
        self, *, intent: DispatchIntentSnapshot, failure_code: str
    ) -> DispatchIntentSnapshot:
        try:
            return self._store.record_dispatch_reconciliation(
                intent_id=intent.intent_id,
                failure_code=failure_code,
                now=self._clock(),
            )
        except TaskStoreConflict as error:
            # A concurrent recovery may have persisted the exact idempotent
            # receipt while this caller observed a transient dispatch error.
            current = self._store.get_dispatch_intent(intent.task_id)
            if current is not None and current.state == "dispatched":
                return current
            if (
                current is not None
                and current.state == "reconciliation_required"
                and current.failure_code == failure_code
            ):
                return current
            if current is not None and current.state in {
                "dispatched",
                "reconciliation_required",
            }:
                raise TaskConflict(
                    "concurrent dispatch reconciliation outcome diverged"
                ) from error
            raise TaskConflict(str(error)) from error

    def _dispatch_claimed_intent(
        self,
        *,
        snapshot: TaskSnapshot,
        intent: DispatchIntentSnapshot,
    ) -> DispatchIntentSnapshot:
        """Dispatch or replay one already-claimed immutable intent."""

        if self._dispatcher is None:
            raise TaskDispatchError("DISPATCHER_UNAVAILABLE")
        try:
            handle = self._dispatcher.dispatch(intent)
        except DispatchDeferred:
            # Capacity pressure or a post-Popen ambiguity has no trustworthy
            # terminal outcome.  Preserve ``dispatching`` so exact startup
            # recovery can adopt a later fenced ready receipt without a second
            # launch.  This legacy one-shot path never retries; only the active
            # supervised scheduler may apply the current finite-retry policy.
            return intent
        except DispatchError as error:
            return self._record_dispatch_reconciliation(
                intent=intent, failure_code=error.code
            )
        except Exception:
            return self._record_dispatch_reconciliation(
                intent=intent, failure_code="DISPATCH_UNAVAILABLE"
            )

        try:
            validated_handle = self._validate_dispatch_receipt(
                snapshot=snapshot, intent=intent, handle=handle
            )
        except TaskValidationError:
            return self._record_dispatch_reconciliation(
                intent=intent, failure_code="DISPATCH_RECEIPT_INVALID"
            )
        try:
            return self._store.record_dispatch_success(
                intent_id=intent.intent_id,
                handle=validated_handle,
                now=self._clock(),
            )
        except TaskStoreConflict as error:
            current = self._store.get_dispatch_intent(intent.task_id)
            if (
                current is not None
                and current.state == "dispatched"
                and current.handle == validated_handle
            ):
                return current
            if current is not None and current.state in {
                "dispatched",
                "reconciliation_required",
            }:
                raise TaskConflict(
                    "concurrent dispatch success outcome diverged"
                ) from error
            raise TaskConflict(str(error)) from error

    def submit_task(
        self,
        *,
        task_id: str,
        project_id: str,
        principal_id: str,
        approval_id: str,
        idempotency_key: str,
    ) -> SubmitTaskResult:
        """Atomically admit one approved FWI node for fenced scheduling."""

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
                snapshot=self._require_not_purged(replay.snapshot),
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
            # A same-key submit may commit after the initial replay lookup but
            # before this snapshot read.  Recheck the durable idempotency row
            # before treating the now-Queued task as a conflicting request.
            try:
                late_replay = self._store.lookup_submit_task(
                    project_id=project_id,
                    principal_id=principal_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                )
            except IdempotencyConflict as error:
                raise TaskIdempotencyConflict(str(error)) from error
            if late_replay is not None:
                return SubmitTaskResult(
                    snapshot=self._require_not_purged(late_replay.snapshot),
                    intent=late_replay.intent,
                    replayed=True,
                    dispatch_attempted=False,
                )
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
        return SubmitTaskResult(
            snapshot=admitted.snapshot,
            intent=admitted.intent,
            replayed=False,
            dispatch_attempted=False,
        )

    def acquire_runtime_supervisor_lease(
        self,
        *,
        project_id: str,
        principal_id: str,
        owner_id: str,
        lease_seconds: int,
    ) -> RuntimeSupervisorLeaseAcquisition:
        """Acquire or exactly replay one scope-local supervisor term."""

        _validate_opaque_id(project_id, field="project_id")
        _validate_opaque_id(principal_id, field="principal_id")
        _validate_opaque_id(owner_id, field="owner_id")
        self._validate_runtime_supervisor_lease_seconds(lease_seconds)
        try:
            return self._store.acquire_runtime_supervisor_lease(
                project_id=project_id,
                principal_id=principal_id,
                owner_id=owner_id,
                lease_seconds=lease_seconds,
                clock=self._runtime_supervisor_clock,
            )
        except TaskStoreConflict as error:
            raise TaskConflict(str(error)) from error

    def heartbeat_runtime_supervisor_lease(
        self,
        lease: RuntimeSupervisorLease,
        *,
        lease_seconds: int,
    ) -> RuntimeSupervisorLease:
        """Extend only the exact current, unexpired supervisor term."""

        if not isinstance(lease, RuntimeSupervisorLease):
            raise TaskValidationError(
                "INVALID_RUNTIME_SUPERVISOR_LEASE",
                ["lease must be a durable runtime supervisor lease"],
            )
        self._validate_runtime_supervisor_lease_seconds(lease_seconds)
        try:
            return self._store.heartbeat_runtime_supervisor_lease(
                lease=lease,
                lease_seconds=lease_seconds,
                clock=self._runtime_supervisor_clock,
            )
        except RuntimeSupervisorLeaseLost as error:
            raise TaskSupervisorLeaseLost() from error
        except TaskStoreConflict as error:
            raise TaskConflict(str(error)) from error

    def release_runtime_supervisor_lease(
        self, lease: RuntimeSupervisorLease
    ) -> RuntimeSupervisorLease:
        """Close only the exact current supervisor term; never a Worker."""

        if not isinstance(lease, RuntimeSupervisorLease):
            raise TaskValidationError(
                "INVALID_RUNTIME_SUPERVISOR_LEASE",
                ["lease must be a durable runtime supervisor lease"],
            )
        try:
            return self._store.release_runtime_supervisor_lease(
                lease=lease,
                clock=self._runtime_supervisor_clock,
            )
        except RuntimeSupervisorLeaseLost as error:
            raise TaskSupervisorLeaseLost() from error
        except TaskStoreConflict as error:
            raise TaskConflict(str(error)) from error

    @staticmethod
    def _worker_projection_deferred_code(
        projection: TaskWorkerProjectionResult,
    ) -> str | None:
        if projection.deferred_code is not None:
            return projection.deferred_code
        evidence = projection.evidence
        ticket = evidence.get("ticket") if isinstance(evidence, Mapping) else None
        state = ticket.get("state") if isinstance(ticket, Mapping) else None
        return {
            "staged": "WORKER_ATTEMPT_STAGED",
            "leased": "WORKER_ATTEMPT_STARTING",
            "spawned": "WORKER_ATTEMPT_STARTING",
            "failed": "WORKER_ATTEMPT_FAILED",
        }.get(state, "WORKER_ATTEMPT_STARTING" if evidence is not None else None)

    def reconcile_runtime_dispatch(
        self,
        task_id: str,
        *,
        project_id: str,
        principal_id: str,
        supervisor_lease: RuntimeSupervisorLease,
    ) -> TaskDispatchReconciliationResult:
        """Resolve one exact positive receipt without launching a Worker."""

        snapshot = self.get_task(
            task_id, project_id=project_id, principal_id=principal_id
        )
        if (
            not isinstance(supervisor_lease, RuntimeSupervisorLease)
            or supervisor_lease.project_id != project_id
            or supervisor_lease.principal_id != principal_id
        ):
            raise TaskSupervisorLeaseLost()
        intent = self._store.get_dispatch_intent(task_id)
        if intent is None:
            raise TaskConflict("runtime task has no dispatch intent")
        reconciliation = getattr(intent, "reconciliation", None)
        if intent.state == "dispatched" and getattr(
            reconciliation, "state", None
        ) == "resolved":
            return TaskDispatchReconciliationResult(
                intent=intent,
                evidence_kind=getattr(reconciliation, "evidence_kind", None),
                authorized=False,
                authorization_replayed=False,
                probe_attempted=False,
                projected=False,
                adopted=False,
            )
        if intent.state != "reconciliation_required":
            return TaskDispatchReconciliationResult(
                intent=intent,
                evidence_kind=None,
                authorized=False,
                authorization_replayed=False,
                probe_attempted=False,
                projected=False,
                adopted=False,
                deferred_code="RECONCILIATION_NOT_REQUIRED",
            )
        if snapshot.status != "Queued":
            return TaskDispatchReconciliationResult(
                intent=intent,
                evidence_kind=None,
                authorized=False,
                authorization_replayed=False,
                probe_attempted=False,
                projected=False,
                adopted=False,
                deferred_code="RECONCILIATION_TASK_NOT_QUEUED",
            )
        if self._dispatcher is None:
            raise TaskDispatchError("DISPATCHER_UNAVAILABLE")
        probe = getattr(
            self._dispatcher, "probe_existing_dispatch_receipt", None
        )
        if not callable(probe):
            return TaskDispatchReconciliationResult(
                intent=intent,
                evidence_kind=None,
                authorized=False,
                authorization_replayed=False,
                probe_attempted=False,
                projected=False,
                adopted=False,
                deferred_code="RECONCILIATION_PROBE_UNSUPPORTED",
            )

        authorizations = []
        for evidence_kind in (
            "managed_worker_receipt",
            "private_receipt",
        ):
            try:
                authorization = (
                    self._store.authorize_supervised_dispatch_reconciliation(
                        intent_id=intent.intent_id,
                        evidence_kind=evidence_kind,
                        supervisor_lease=supervisor_lease,
                        supervisor_clock=self._runtime_supervisor_clock,
                    )
                )
            except RuntimeSupervisorLeaseLost as error:
                raise TaskSupervisorLeaseLost() from error
            except TaskStoreConflict:
                current = self._store.get_dispatch_intent(task_id)
                current_reconciliation = getattr(
                    current, "reconciliation", None
                )
                if (
                    current is not None
                    and current.state == "dispatched"
                    and getattr(current_reconciliation, "state", None)
                    == "resolved"
                ):
                    return TaskDispatchReconciliationResult(
                        intent=current,
                        evidence_kind=getattr(
                            current_reconciliation, "evidence_kind", None
                        ),
                        authorized=bool(authorizations),
                        authorization_replayed=bool(authorizations)
                        and all(value.replayed for value in authorizations),
                        probe_attempted=False,
                        projected=False,
                        adopted=False,
                    )
                return TaskDispatchReconciliationResult(
                    intent=current or intent,
                    evidence_kind=None,
                    authorized=bool(authorizations),
                    authorization_replayed=bool(authorizations)
                    and all(value.replayed for value in authorizations),
                    probe_attempted=False,
                    projected=False,
                    adopted=False,
                    deferred_code="RECONCILIATION_AUTHORIZATION_CONFLICT",
                )
            if (
                authorization.intent.intent_id != intent.intent_id
                or authorization.evidence_kind != evidence_kind
                or type(authorization.replayed) is not bool
            ):
                raise TaskDispatchError("RECONCILIATION_AUTHORIZATION_INVALID")
            authorizations.append(authorization)
            authorized_reconciliation = getattr(
                authorization.intent, "reconciliation", None
            )
            if (
                authorization.intent.state == "dispatched"
                and getattr(authorized_reconciliation, "state", None)
                == "resolved"
            ):
                return TaskDispatchReconciliationResult(
                    intent=authorization.intent,
                    evidence_kind=getattr(
                        authorized_reconciliation, "evidence_kind", None
                    ),
                    authorized=True,
                    authorization_replayed=all(
                        value.replayed for value in authorizations
                    ),
                    probe_attempted=False,
                    projected=False,
                    adopted=False,
                )
            if authorization.intent.state != "reconciliation_required":
                raise TaskDispatchError("RECONCILIATION_AUTHORIZATION_INVALID")

        authorization_replayed = all(
            value.replayed for value in authorizations
        )
        try:
            positive = probe(intent)
        except (DispatchDeferred, DispatchError) as error:
            return TaskDispatchReconciliationResult(
                intent=self._store.get_dispatch_intent(task_id) or intent,
                evidence_kind=None,
                authorized=True,
                authorization_replayed=authorization_replayed,
                probe_attempted=True,
                projected=False,
                adopted=False,
                deferred_code=error.code,
            )
        except Exception:
            return TaskDispatchReconciliationResult(
                intent=self._store.get_dispatch_intent(task_id) or intent,
                evidence_kind=None,
                authorized=True,
                authorization_replayed=authorization_replayed,
                probe_attempted=True,
                projected=False,
                adopted=False,
                deferred_code="RECONCILIATION_PROBE_UNAVAILABLE",
            )
        if not isinstance(positive, DispatchReceiptProbe):
            return TaskDispatchReconciliationResult(
                intent=intent,
                evidence_kind=None,
                authorized=True,
                authorization_replayed=authorization_replayed,
                probe_attempted=True,
                projected=False,
                adopted=False,
                deferred_code="RECONCILIATION_PROBE_INVALID",
            )
        try:
            validated_handle = self._validate_dispatch_receipt(
                snapshot=snapshot,
                intent=intent,
                handle=positive.handle,
            )
        except TaskValidationError:
            return TaskDispatchReconciliationResult(
                intent=intent,
                evidence_kind=positive.evidence_kind,
                authorized=True,
                authorization_replayed=authorization_replayed,
                probe_attempted=True,
                projected=False,
                adopted=False,
                deferred_code="RECONCILIATION_PROBE_INVALID",
            )

        try:
            if positive.evidence_kind == "managed_worker_receipt":
                if not isinstance(positive.evidence, Mapping):
                    raise TaskValidationError(
                        "RECONCILIATION_PROBE_INVALID",
                        ["managed receipt requires exact Worker evidence"],
                    )
                completion = self._store.record_supervised_worker_observation(
                    intent_id=intent.intent_id,
                    evidence=positive.evidence,
                    handle=validated_handle,
                    supervisor_lease=supervisor_lease,
                    supervisor_clock=self._runtime_supervisor_clock,
                )
                resolved = completion.intent
                projected = True
                adopted = completion.adopted
            elif positive.evidence_kind == "private_receipt":
                if (
                    positive.evidence is not None
                    or positive.private_schema_version != "1.0.0"
                    or not isinstance(positive.receipt_record_hash, str)
                ):
                    raise TaskValidationError(
                        "RECONCILIATION_PROBE_INVALID",
                        ["private receipt proof is incomplete"],
                    )
                completion = self._store.record_supervised_private_receipt_adoption(
                    intent_id=intent.intent_id,
                    handle=validated_handle,
                    private_schema_version=positive.private_schema_version,
                    receipt_record_hash=positive.receipt_record_hash,
                    supervisor_lease=supervisor_lease,
                    supervisor_clock=self._runtime_supervisor_clock,
                )
                resolved = completion.intent
                projected = False
                adopted = completion.adopted
            else:
                raise TaskValidationError(
                    "RECONCILIATION_PROBE_INVALID",
                    ["receipt proof kind is unsupported"],
                )
        except RuntimeSupervisorLeaseLost as error:
            raise TaskSupervisorLeaseLost() from error
        except (TaskValidationError, TaskStoreConflict):
            current = self._store.get_dispatch_intent(task_id)
            current_reconciliation = getattr(current, "reconciliation", None)
            if (
                current is not None
                and current.state == "dispatched"
                and getattr(current_reconciliation, "state", None) == "resolved"
            ):
                return TaskDispatchReconciliationResult(
                    intent=current,
                    evidence_kind=getattr(
                        current_reconciliation, "evidence_kind", None
                    ),
                    authorized=True,
                    authorization_replayed=authorization_replayed,
                    probe_attempted=True,
                    projected=False,
                    adopted=False,
                )
            return TaskDispatchReconciliationResult(
                intent=current or intent,
                evidence_kind=positive.evidence_kind,
                authorized=True,
                authorization_replayed=authorization_replayed,
                probe_attempted=True,
                projected=False,
                adopted=False,
                deferred_code="RECONCILIATION_ADOPTION_CONFLICT",
            )
        resolved_reconciliation = getattr(resolved, "reconciliation", None)
        if (
            resolved.state != "dispatched"
            or resolved.handle != validated_handle
            or getattr(resolved_reconciliation, "state", None) != "resolved"
            or getattr(resolved_reconciliation, "result", None) != "dispatched"
            or getattr(resolved_reconciliation, "evidence_kind", None)
            != positive.evidence_kind
            or type(adopted) is not bool
        ):
            raise TaskDispatchError("RECONCILIATION_OUTCOME_INVALID")
        timeout_armed = False
        if positive.evidence_kind == "managed_worker_receipt":
            assert isinstance(positive.evidence, Mapping)
            timeout_armed = self._arm_projected_worker_timeout(
                task_id=task_id,
                snapshot=snapshot,
                intent=resolved,
                evidence=positive.evidence,
                attempt_id=completion.attempt_id,
                supervisor_lease=supervisor_lease,
            )
        return TaskDispatchReconciliationResult(
            intent=resolved,
            evidence_kind=positive.evidence_kind,
            authorized=True,
            authorization_replayed=authorization_replayed,
            probe_attempted=True,
            projected=projected,
            adopted=adopted,
            timeout_armed=timeout_armed,
        )

    def schedule_runtime_dispatch(
        self,
        task_id: str,
        *,
        project_id: str,
        principal_id: str,
        supervisor_lease: RuntimeSupervisorLease,
    ) -> TaskScheduleResult:
        """Ensure one current managed first dispatch under an exact term.

        SQLite authorizes the control-plane attempt.  The fixed Adapter's
        submission and inherited kernel locks remain the external side-effect
        fence, and a successful launch becomes ``dispatched`` only through the
        existing fenced Worker evidence projection transaction.
        """

        snapshot = self.get_task(
            task_id, project_id=project_id, principal_id=principal_id
        )
        if (
            not isinstance(supervisor_lease, RuntimeSupervisorLease)
            or supervisor_lease.project_id != project_id
            or supervisor_lease.principal_id != principal_id
        ):
            raise TaskSupervisorLeaseLost()
        intent = self._store.get_dispatch_intent(task_id)
        if intent is None:
            raise TaskConflict("runtime task has no dispatch intent")
        if intent.state not in {"pending", "dispatching"}:
            return TaskScheduleResult(
                intent=intent,
                authorized=False,
                authorization_replayed=False,
                dispatch_attempted=False,
                projected=False,
                adopted=False,
                deferred_code={
                    "reconciliation_required": "RECONCILIATION_REQUIRED",
                    "retry_exhausted": "WORKER_RETRY_EXHAUSTED",
                }.get(intent.state),
            )
        if snapshot.status != "Queued":
            raise TaskConflict("only a queued task can receive first dispatch")
        if self._dispatcher is None:
            raise TaskDispatchError("DISPATCHER_UNAVAILABLE")
        try:
            supported = self._dispatcher.supports_supervised_dispatch(intent)
        except Exception as error:
            raise TaskDispatchError("DISPATCH_INTENT_UNSUPPORTED") from error
        if supported is not True:
            return TaskScheduleResult(
                intent=intent,
                authorized=False,
                authorization_replayed=False,
                dispatch_attempted=False,
                projected=False,
                adopted=False,
                deferred_code="DISPATCH_INTENT_UNSUPPORTED",
            )

        prior_projection: TaskWorkerProjectionResult | None = None
        if intent.state == "pending":
            reason = "pending_first_dispatch"
        else:
            prior_projection = self.project_worker_attempt(
                task_id,
                project_id=project_id,
                principal_id=principal_id,
                supervisor_lease=supervisor_lease,
            )
            intent = prior_projection.intent
            if intent.state == "dispatched":
                return TaskScheduleResult(
                    intent=intent,
                    authorized=False,
                    authorization_replayed=False,
                    dispatch_attempted=False,
                    projected=prior_projection.projected,
                    adopted=prior_projection.adopted,
                    deferred_code=None,
                )
            if prior_projection.deferred_code == "WORKER_EVIDENCE_UNAVAILABLE":
                try:
                    proof = self._dispatcher.recover_existing_private_receipt(
                        intent
                    )
                except DispatchError as error:
                    return TaskScheduleResult(
                        intent=intent,
                        authorized=False,
                        authorization_replayed=False,
                        dispatch_attempted=False,
                        projected=prior_projection.projected,
                        adopted=False,
                        deferred_code=error.code,
                    )
                except Exception:
                    return TaskScheduleResult(
                        intent=intent,
                        authorized=False,
                        authorization_replayed=False,
                        dispatch_attempted=False,
                        projected=prior_projection.projected,
                        adopted=False,
                        deferred_code="PRIVATE_RECEIPT_RECOVERY_UNAVAILABLE",
                    )
                if (
                    not isinstance(proof, Mapping)
                    or set(proof)
                    != {
                        "handle",
                        "private_schema_version",
                        "receipt_record_hash",
                    }
                    or proof.get("private_schema_version") != "1.0.0"
                    or not isinstance(proof.get("handle"), Mapping)
                    or not isinstance(proof.get("receipt_record_hash"), str)
                    or re.fullmatch(
                        r"sha256:[0-9a-f]{64}", proof["receipt_record_hash"]
                    )
                    is None
                ):
                    return TaskScheduleResult(
                        intent=intent,
                        authorized=False,
                        authorization_replayed=False,
                        dispatch_attempted=False,
                        projected=prior_projection.projected,
                        adopted=False,
                        deferred_code="PRIVATE_RECEIPT_PROOF_INVALID",
                    )
                try:
                    validated_handle = self._validate_dispatch_receipt(
                        snapshot=snapshot,
                        intent=intent,
                        handle=proof["handle"],
                    )
                    adoption = (
                        self._store.record_supervised_private_receipt_adoption(
                            intent_id=intent.intent_id,
                            handle=validated_handle,
                            private_schema_version=proof[
                                "private_schema_version"
                            ],
                            receipt_record_hash=proof[
                                "receipt_record_hash"
                            ],
                            supervisor_lease=supervisor_lease,
                            supervisor_clock=self._runtime_supervisor_clock,
                        )
                    )
                except RuntimeSupervisorLeaseLost as error:
                    raise TaskSupervisorLeaseLost() from error
                except (TaskValidationError, TaskStoreConflict):
                    return TaskScheduleResult(
                        intent=self._store.get_dispatch_intent(task_id) or intent,
                        authorized=False,
                        authorization_replayed=False,
                        dispatch_attempted=False,
                        projected=prior_projection.projected,
                        adopted=False,
                        deferred_code="PRIVATE_RECEIPT_ADOPTION_CONFLICT",
                    )
                return TaskScheduleResult(
                    intent=adoption.intent,
                    authorized=False,
                    authorization_replayed=False,
                    dispatch_attempted=False,
                    projected=prior_projection.projected,
                    adopted=adoption.adopted,
                    deferred_code=None,
                )
            retry_result = self._schedule_pre_running_retry(
                task_id=task_id,
                project_id=project_id,
                principal_id=principal_id,
                snapshot=snapshot,
                intent=intent,
                projection=prior_projection,
                supervisor_lease=supervisor_lease,
            )
            if retry_result is not None:
                return retry_result
            evidence = prior_projection.evidence
            ticket = evidence.get("ticket") if isinstance(evidence, Mapping) else None
            staged = (
                prior_projection.projected
                and isinstance(ticket, Mapping)
                and ticket.get("state") == "staged"
                and evidence.get("ready") is None
                and evidence.get("heartbeat") is None
            )
            if staged:
                reason = "staged_attempt_resume"
            elif prior_projection.deferred_code == "ADAPTER_SUBMISSION_NOT_FOUND":
                reason = "dispatching_no_record_takeover"
            else:
                return TaskScheduleResult(
                    intent=intent,
                    authorized=False,
                    authorization_replayed=False,
                    dispatch_attempted=False,
                    projected=prior_projection.projected,
                    adopted=prior_projection.adopted,
                    deferred_code=self._worker_projection_deferred_code(
                        prior_projection
                    ),
                )

        try:
            authorization = self._store.authorize_supervised_dispatch(
                intent_id=intent.intent_id,
                reason=reason,
                supervisor_lease=supervisor_lease,
                supervisor_clock=self._runtime_supervisor_clock,
            )
        except RuntimeSupervisorLeaseLost as error:
            raise TaskSupervisorLeaseLost() from error
        except TaskStoreConflict as error:
            current = self._store.get_dispatch_intent(task_id)
            if current is not None and current.state in {
                "dispatched",
                "reconciliation_required",
            }:
                return TaskScheduleResult(
                    intent=current,
                    authorized=False,
                    authorization_replayed=False,
                    dispatch_attempted=False,
                    projected=False,
                    adopted=False,
                    deferred_code=(
                        "RECONCILIATION_REQUIRED"
                        if current.state == "reconciliation_required"
                        else None
                    ),
                )
            raise TaskConflict(str(error)) from error
        intent = authorization.intent
        if intent.state != "dispatching":
            raise TaskConflict("supervised dispatch authorization did not claim intent")

        try:
            handle = self._dispatcher.ensure_first_dispatch(intent)
        except DispatchDeferred as error:
            projection = self.project_worker_attempt(
                task_id,
                project_id=project_id,
                principal_id=principal_id,
                supervisor_lease=supervisor_lease,
            )
            return TaskScheduleResult(
                intent=projection.intent,
                authorized=True,
                authorization_replayed=authorization.replayed,
                dispatch_attempted=True,
                projected=projection.projected,
                adopted=projection.adopted,
                timeout_armed=projection.timeout_armed,
                deferred_code=(
                    None
                    if projection.intent.state == "dispatched"
                    else error.code
                ),
            )
        except DispatchError as error:
            return TaskScheduleResult(
                intent=intent,
                authorized=True,
                authorization_replayed=authorization.replayed,
                dispatch_attempted=True,
                projected=False,
                adopted=False,
                deferred_code=error.code,
            )
        except Exception:
            return TaskScheduleResult(
                intent=intent,
                authorized=True,
                authorization_replayed=authorization.replayed,
                dispatch_attempted=True,
                projected=False,
                adopted=False,
                deferred_code="DISPATCH_UNAVAILABLE",
            )

        try:
            validated_handle = self._validate_dispatch_receipt(
                snapshot=snapshot,
                intent=intent,
                handle=handle,
            )
        except TaskValidationError:
            return TaskScheduleResult(
                intent=intent,
                authorized=True,
                authorization_replayed=authorization.replayed,
                dispatch_attempted=True,
                projected=False,
                adopted=False,
                deferred_code="DISPATCH_RECEIPT_INVALID",
            )
        projection = self.project_worker_attempt(
            task_id,
            project_id=project_id,
            principal_id=principal_id,
            supervisor_lease=supervisor_lease,
        )
        if (
            projection.intent.state != "dispatched"
            or projection.intent.handle != validated_handle
        ):
            raise TaskDispatchError("DISPATCH_RECEIPT_NOT_PROJECTED")
        return TaskScheduleResult(
            intent=projection.intent,
            authorized=True,
            authorization_replayed=authorization.replayed,
            dispatch_attempted=True,
            projected=projection.projected,
            adopted=projection.adopted,
            timeout_armed=projection.timeout_armed,
            deferred_code=None,
        )

    def _schedule_pre_running_retry(
        self,
        *,
        task_id: str,
        project_id: str,
        principal_id: str,
        snapshot: TaskSnapshot,
        intent: DispatchIntentSnapshot,
        projection: TaskWorkerProjectionResult,
        supervisor_lease: RuntimeSupervisorLease,
    ) -> TaskScheduleResult | None:
        """Authorize and deliver the one approved stopped attempt-1 retry."""

        approval = snapshot.approval
        retry_policy = (
            approval.get("scope", {}).get("retry_policy")
            if isinstance(approval, Mapping)
            and isinstance(approval.get("scope"), Mapping)
            else None
        )
        retryable_classes = (
            retry_policy.get("retryable_failure_classes")
            if isinstance(retry_policy, Mapping)
            else None
        )
        if (
            not isinstance(approval, Mapping)
            or approval.get("schema_version") != "1.1.0"
            or not isinstance(retry_policy, Mapping)
            or retry_policy.get("max_attempts") != 2
            or retry_policy.get("max_concurrent_attempts") != 1
            or not isinstance(retryable_classes, list)
            or len(retryable_classes) != 2
            or not all(isinstance(item, str) for item in retryable_classes)
            or set(retryable_classes) != RETRYABLE_FAILURE_CLASSES
        ):
            return None
        evidence = projection.evidence
        ticket = evidence.get("ticket") if isinstance(evidence, Mapping) else None
        exhausted_retry = (
            projection.projected
            and isinstance(evidence, Mapping)
            and evidence.get("attempt_number") == 2
            and isinstance(ticket, Mapping)
            and ticket.get("state") == "failed"
            and ticket.get("worker_pid") is None
            and evidence.get("ready") is None
            and evidence.get("heartbeat") is None
        )
        if exhausted_retry:
            return self._finalize_pre_running_retry_exhaustion(
                task_id=task_id,
                project_id=project_id,
                principal_id=principal_id,
                snapshot=snapshot,
                intent=intent,
                projection=projection,
                supervisor_lease=supervisor_lease,
            )
        staged_retry = (
            projection.projected
            and isinstance(evidence, Mapping)
            and evidence.get("attempt_number") == 2
            and isinstance(ticket, Mapping)
            and ticket.get("state") == "staged"
            and ticket.get("capacity_slot") is None
            and ticket.get("capacity_generation") is None
            and ticket.get("worker_pid") is None
            and evidence.get("ready") is None
            and evidence.get("heartbeat") is None
        )
        if staged_retry:
            try:
                authorization = self._store.resume_supervised_retry(
                    intent_id=intent.intent_id,
                    supervisor_lease=supervisor_lease,
                    supervisor_clock=self._runtime_supervisor_clock,
                )
            except RuntimeSupervisorLeaseLost as error:
                raise TaskSupervisorLeaseLost() from error
            except TaskStoreConflict:
                return TaskScheduleResult(
                    intent=self._store.get_dispatch_intent(task_id) or intent,
                    authorized=False,
                    authorization_replayed=False,
                    dispatch_attempted=False,
                    projected=True,
                    adopted=False,
                    deferred_code="WORKER_RETRY_RESUME_CONFLICT",
                )
            if (
                authorization.intent.intent_id != intent.intent_id
                or authorization.attempt_number != 2
                or authorization.failure_kind != "pre_running_launch_failure"
                or type(authorization.authorization_replayed) is not bool
            ):
                raise TaskDispatchError("WORKER_RETRY_AUTHORIZATION_INVALID")
            return self._deliver_authorized_pre_running_retry(
                task_id=task_id,
                project_id=project_id,
                principal_id=principal_id,
                snapshot=snapshot,
                intent=intent,
                authorization=authorization,
                supervisor_lease=supervisor_lease,
            )
        retry_candidate = (
            projection.projected
            and isinstance(evidence, Mapping)
            and evidence.get("attempt_number") == 1
            and isinstance(ticket, Mapping)
            and ticket.get("state") == "failed"
            and ticket.get("worker_pid") is None
            and evidence.get("ready") is None
            and evidence.get("heartbeat") is None
        )
        if not retry_candidate:
            return None
        if (
            projection.attempt_id != evidence.get("attempt_id")
            or type(projection.observation_sequence) is not int
            or projection.observation_sequence < 1
            or not isinstance(projection.document_hash, str)
        ):
            raise TaskDispatchError("WORKER_RETRY_PROJECTION_INVALID")
        try:
            _, calculated_evidence_hash = encode_document(dict(evidence))
        except TaskStoreConflict as error:
            raise TaskDispatchError("WORKER_RETRY_PROJECTION_INVALID") from error
        if calculated_evidence_hash != projection.document_hash:
            raise TaskDispatchError("WORKER_RETRY_PROJECTION_INVALID")

        try:
            proof = self._dispatcher.probe_pre_running_retry(intent)
        except DispatchError as error:
            return TaskScheduleResult(
                intent=intent,
                authorized=False,
                authorization_replayed=False,
                dispatch_attempted=False,
                projected=True,
                adopted=False,
                deferred_code=error.code,
            )
        except Exception:
            return TaskScheduleResult(
                intent=intent,
                authorized=False,
                authorization_replayed=False,
                dispatch_attempted=False,
                projected=True,
                adopted=False,
                deferred_code="WORKER_RETRY_PROOF_UNAVAILABLE",
            )
        if (
            not isinstance(proof, DispatchRetryProof)
            or proof.failure_kind != "pre_running_launch_failure"
            or proof.previous_attempt_id != projection.attempt_id
            or proof.previous_attempt_number != 1
            or proof.private_schema_version != "1.2.0"
            or proof.evidence != evidence
            or re.fullmatch(r"sha256:[0-9a-f]{64}", proof.private_proof_hash)
            is None
        ):
            raise TaskDispatchError("WORKER_RETRY_PROOF_INVALID")

        try:
            authorization = self._store.authorize_supervised_retry(
                intent_id=intent.intent_id,
                previous_attempt_id=projection.attempt_id,
                previous_observation_sequence=projection.observation_sequence,
                failure_kind=proof.failure_kind,
                private_proof_hash=proof.private_proof_hash,
                supervisor_lease=supervisor_lease,
                supervisor_clock=self._runtime_supervisor_clock,
            )
        except RuntimeSupervisorLeaseLost as error:
            raise TaskSupervisorLeaseLost() from error
        except TaskStoreConflict:
            return TaskScheduleResult(
                intent=self._store.get_dispatch_intent(task_id) or intent,
                authorized=False,
                authorization_replayed=False,
                dispatch_attempted=False,
                projected=True,
                adopted=False,
                deferred_code="WORKER_RETRY_AUTHORIZATION_CONFLICT",
            )
        if (
            authorization.intent.intent_id != intent.intent_id
            or authorization.attempt_number != 2
            or authorization.previous_attempt_id != projection.attempt_id
            or authorization.previous_observation_sequence
            != projection.observation_sequence
            or authorization.evidence_hash != projection.document_hash
            or authorization.private_proof_hash != proof.private_proof_hash
            or authorization.failure_kind != proof.failure_kind
            or type(authorization.authorization_replayed) is not bool
        ):
            raise TaskDispatchError("WORKER_RETRY_AUTHORIZATION_INVALID")
        return self._deliver_authorized_pre_running_retry(
            task_id=task_id,
            project_id=project_id,
            principal_id=principal_id,
            snapshot=snapshot,
            intent=intent,
            authorization=authorization,
            supervisor_lease=supervisor_lease,
        )

    def _finalize_pre_running_retry_exhaustion(
        self,
        *,
        task_id: str,
        project_id: str,
        principal_id: str,
        snapshot: TaskSnapshot,
        intent: DispatchIntentSnapshot,
        projection: TaskWorkerProjectionResult,
        supervisor_lease: RuntimeSupervisorLease,
    ) -> TaskScheduleResult:
        """Close exact stopped attempt 2 without authorizing attempt 3."""

        evidence = projection.evidence
        if (
            not isinstance(evidence, Mapping)
            or projection.attempt_id != evidence.get("attempt_id")
            or evidence.get("attempt_number") != 2
            or type(projection.observation_sequence) is not int
            or projection.observation_sequence < 1
            or not isinstance(projection.document_hash, str)
        ):
            raise TaskDispatchError("WORKER_RETRY_EXHAUSTION_PROJECTION_INVALID")
        try:
            _, calculated_evidence_hash = encode_document(dict(evidence))
        except TaskStoreConflict as error:
            raise TaskDispatchError(
                "WORKER_RETRY_EXHAUSTION_PROJECTION_INVALID"
            ) from error
        if calculated_evidence_hash != projection.document_hash:
            raise TaskDispatchError("WORKER_RETRY_EXHAUSTION_PROJECTION_INVALID")

        try:
            proof = self._dispatcher.probe_pre_running_retry_exhaustion(intent)
        except DispatchError as error:
            return TaskScheduleResult(
                intent=intent,
                authorized=False,
                authorization_replayed=False,
                dispatch_attempted=False,
                projected=True,
                adopted=False,
                deferred_code=error.code,
            )
        except Exception:
            return TaskScheduleResult(
                intent=intent,
                authorized=False,
                authorization_replayed=False,
                dispatch_attempted=False,
                projected=True,
                adopted=False,
                deferred_code="WORKER_RETRY_EXHAUSTION_PROOF_UNAVAILABLE",
            )
        if (
            not isinstance(proof, DispatchRetryProof)
            or proof.failure_kind != "pre_running_launch_failure"
            or proof.previous_attempt_id != projection.attempt_id
            or proof.previous_attempt_number != 2
            or proof.private_schema_version != "1.2.0"
            or proof.evidence != evidence
            or re.fullmatch(r"sha256:[0-9a-f]{64}", proof.private_proof_hash)
            is None
        ):
            raise TaskDispatchError("WORKER_RETRY_EXHAUSTION_PROOF_INVALID")

        sequence = self._store.latest_run_event_sequence(task_id) + 1
        extension = {
            "intent_id": intent.intent_id,
            "attempt_id": projection.attempt_id,
            "attempt_number": 2,
            "observation_sequence": projection.observation_sequence,
            "evidence_hash": projection.document_hash,
            "private_schema_version": proof.private_schema_version,
            "private_proof_hash": proof.private_proof_hash,
            "failure_kind": proof.failure_kind,
            "max_attempts": 2,
        }
        _, identity_hash = encode_document(
            {
                "intent_id": intent.intent_id,
                "attempt_id": projection.attempt_id,
                "observation_sequence": projection.observation_sequence,
                "evidence_hash": projection.document_hash,
                "private_proof_hash": proof.private_proof_hash,
                "event_type": "node_failed",
                "sequence": sequence,
            }
        )
        event = {
            "schema_version": "1.0.0",
            "event_id": "event-" + identity_hash.removeprefix("sha256:")[:32],
            "sequence": sequence,
            "task_id": task_id,
            "node_id": intent.node_id,
            "event_type": "node_failed",
            "task_status": "Failed",
            "error": {
                "code": "retry_exhausted",
                "message": "FWI Worker exhausted its approved launch attempts",
                "retryable": False,
            },
            "occurred_at": evidence["ticket"]["updated_at"],
            "fingerprint": copy.deepcopy(intent.queue_fingerprint),
            "extensions": {
                "org.agent_rpc.retry_exhaustion": extension,
            },
        }
        self._validate_schema("run-event.schema.json", event)
        _validate_run_event_semantics(event)
        _validate_run_event_binding(snapshot, event)
        try:
            exhaustion = self._store.finalize_supervised_retry_exhaustion(
                intent_id=intent.intent_id,
                attempt_id=projection.attempt_id,
                observation_sequence=projection.observation_sequence,
                evidence=evidence,
                private_schema_version=proof.private_schema_version,
                private_proof_hash=proof.private_proof_hash,
                failure_kind=proof.failure_kind,
                terminal_event=event,
                supervisor_lease=supervisor_lease,
                supervisor_clock=self._runtime_supervisor_clock,
            )
        except RuntimeSupervisorLeaseLost as error:
            raise TaskSupervisorLeaseLost() from error
        except TaskStoreConflict:
            current = self._store.get_dispatch_intent(task_id)
            if (
                current is not None
                and current.intent_id == intent.intent_id
                and current.state == "retry_exhausted"
                and current.failure_code == "WORKER_RETRY_EXHAUSTED"
                and current.handle is None
            ):
                return TaskScheduleResult(
                    intent=current,
                    authorized=False,
                    authorization_replayed=True,
                    dispatch_attempted=False,
                    projected=True,
                    adopted=False,
                    deferred_code="WORKER_RETRY_EXHAUSTED",
                )
            return TaskScheduleResult(
                intent=current or intent,
                authorized=False,
                authorization_replayed=False,
                dispatch_attempted=False,
                projected=True,
                adopted=False,
                deferred_code="WORKER_RETRY_EXHAUSTION_CONFLICT",
            )
        if (
            exhaustion.snapshot.status != "Failed"
            or exhaustion.intent.intent_id != intent.intent_id
            or exhaustion.intent.state != "retry_exhausted"
            or exhaustion.intent.failure_code != "WORKER_RETRY_EXHAUSTED"
            or exhaustion.attempt_id != projection.attempt_id
            or exhaustion.observation_sequence != projection.observation_sequence
            or exhaustion.evidence_hash != projection.document_hash
            or exhaustion.private_proof_hash != proof.private_proof_hash
        ):
            raise TaskDispatchError("WORKER_RETRY_EXHAUSTION_OUTCOME_INVALID")
        return TaskScheduleResult(
            intent=exhaustion.intent,
            authorized=False,
            authorization_replayed=exhaustion.replayed,
            dispatch_attempted=False,
            projected=True,
            adopted=False,
            deferred_code="WORKER_RETRY_EXHAUSTED",
        )

    def _deliver_authorized_pre_running_retry(
        self,
        *,
        task_id: str,
        project_id: str,
        principal_id: str,
        snapshot: TaskSnapshot,
        intent: DispatchIntentSnapshot,
        authorization: Any,
        supervisor_lease: RuntimeSupervisorLease,
    ) -> TaskScheduleResult:
        """Deliver one stable SQLite retry token and project its exact result."""

        try:
            token = authorization.adapter_token()
        except Exception as error:
            raise TaskDispatchError("WORKER_RETRY_AUTHORIZATION_INVALID") from error
        if (
            not isinstance(token, Mapping)
            or set(token)
            != {
                "schema_version",
                "intent_id",
                "previous_attempt_id",
                "previous_observation_sequence",
                "failure_kind",
                "private_proof_hash",
                "next_attempt_number",
                "authorized_at",
            }
            or token.get("schema_version") != "1.0.0"
            or token.get("intent_id") != intent.intent_id
            or token.get("previous_attempt_id")
            != authorization.previous_attempt_id
            or token.get("previous_observation_sequence")
            != authorization.previous_observation_sequence
            or token.get("failure_kind") != "pre_running_launch_failure"
            or token.get("private_proof_hash")
            != authorization.private_proof_hash
            or token.get("next_attempt_number") != 2
        ):
            raise TaskDispatchError("WORKER_RETRY_AUTHORIZATION_INVALID")
        try:
            self._parse_gate_time(token.get("authorized_at"))
        except TaskDispatchError as error:
            raise TaskDispatchError("WORKER_RETRY_AUTHORIZATION_INVALID") from error

        try:
            handle = self._dispatcher.retry_pre_running(
                intent, authorization=token
            )
        except DispatchDeferred as error:
            current = self.project_worker_attempt(
                task_id,
                project_id=project_id,
                principal_id=principal_id,
                supervisor_lease=supervisor_lease,
            )
            return TaskScheduleResult(
                intent=current.intent,
                authorized=True,
                authorization_replayed=authorization.authorization_replayed,
                dispatch_attempted=True,
                projected=current.projected,
                adopted=current.adopted,
                timeout_armed=current.timeout_armed,
                deferred_code=(
                    None if current.intent.state == "dispatched" else error.code
                ),
            )
        except DispatchError as error:
            return TaskScheduleResult(
                intent=intent,
                authorized=True,
                authorization_replayed=authorization.authorization_replayed,
                dispatch_attempted=True,
                projected=True,
                adopted=False,
                deferred_code=error.code,
            )
        except Exception:
            return TaskScheduleResult(
                intent=intent,
                authorized=True,
                authorization_replayed=authorization.authorization_replayed,
                dispatch_attempted=True,
                projected=True,
                adopted=False,
                deferred_code="WORKER_RETRY_UNAVAILABLE",
            )
        try:
            validated_handle = self._validate_dispatch_receipt(
                snapshot=snapshot,
                intent=intent,
                handle=handle,
            )
        except TaskValidationError:
            return TaskScheduleResult(
                intent=intent,
                authorized=True,
                authorization_replayed=authorization.authorization_replayed,
                dispatch_attempted=True,
                projected=True,
                adopted=False,
                deferred_code="DISPATCH_RECEIPT_INVALID",
            )
        current = self.project_worker_attempt(
            task_id,
            project_id=project_id,
            principal_id=principal_id,
            supervisor_lease=supervisor_lease,
        )
        if (
            current.intent.state != "dispatched"
            or current.intent.handle != validated_handle
        ):
            raise TaskDispatchError("DISPATCH_RECEIPT_NOT_PROJECTED")
        return TaskScheduleResult(
            intent=current.intent,
            authorized=True,
            authorization_replayed=authorization.authorization_replayed,
            dispatch_attempted=True,
            projected=current.projected,
            adopted=current.adopted,
            timeout_armed=current.timeout_armed,
        )

    def _arm_projected_worker_timeout(
        self,
        *,
        task_id: str,
        snapshot: TaskSnapshot,
        intent: DispatchIntentSnapshot,
        evidence: Mapping[str, Any],
        attempt_id: str,
        supervisor_lease: RuntimeSupervisorLease,
    ) -> bool:
        """Arm the first exact running-attempt timeout from existing evidence."""

        ticket = evidence.get("ticket")
        ready = evidence.get("ready")
        heartbeat = evidence.get("heartbeat")
        timeout_capable = (
            getattr(snapshot, "timeout", None) is None
            and intent.state == "dispatched"
            and isinstance(ticket, Mapping)
            and ticket.get("state") == "spawned"
            and isinstance(ready, Mapping)
            and isinstance(heartbeat, Mapping)
            and heartbeat.get("state") == "running"
        )
        if not timeout_capable:
            return False
        timeout_probe = getattr(
            self._dispatcher, "supports_exact_timeout", None
        )
        capability_proof = None
        if callable(timeout_probe):
            try:
                capability_proof = timeout_probe(
                    intent, attempt_id=attempt_id
                )
            except DispatchError as error:
                raise TaskDispatchError(error.code) from error
            except Exception as error:
                raise TaskDispatchError(
                    "ADAPTER_TIMEOUT_CAPABILITY_UNAVAILABLE"
                ) from error
        if capability_proof is None:
            return False
        try:
            timeout_record = self._store.arm_worker_attempt_timeout(
                intent_id=intent.intent_id,
                attempt_id=attempt_id,
                capability_proof=capability_proof,
                supervisor_lease=supervisor_lease,
                supervisor_clock=self._runtime_supervisor_clock,
            )
        except RuntimeSupervisorLeaseLost as error:
            raise TaskSupervisorLeaseLost() from error
        except TaskStoreConflict as error:
            raise TaskConflict(str(error)) from error
        armed_timeout = getattr(timeout_record, "timeout", None)
        replayed = getattr(timeout_record, "replayed", None)
        armed_snapshot = getattr(timeout_record, "snapshot", None)
        if (
            getattr(armed_snapshot, "task_id", None) != task_id
            or getattr(armed_timeout, "intent_id", None) != intent.intent_id
            or getattr(armed_timeout, "attempt_id", None) != attempt_id
            or getattr(armed_timeout, "state", None)
            not in {
                "armed",
                "requested",
                "timed_out",
                "superseded",
                "not_triggered",
                "suppressed",
            }
            or type(replayed) is not bool
        ):
            raise TaskDispatchError("TIMEOUT_WINDOW_INVALID")
        return not replayed

    def project_worker_attempt(
        self,
        task_id: str,
        *,
        project_id: str,
        principal_id: str,
        supervisor_lease: RuntimeSupervisorLease,
    ) -> TaskWorkerProjectionResult:
        """Project existing fixed-Adapter evidence under one exact term."""

        snapshot = self.get_task(
            task_id, project_id=project_id, principal_id=principal_id
        )
        if (
            not isinstance(supervisor_lease, RuntimeSupervisorLease)
            or supervisor_lease.project_id != project_id
            or supervisor_lease.principal_id != principal_id
        ):
            raise TaskSupervisorLeaseLost()
        intent = self._store.get_dispatch_intent(task_id)
        if intent is None:
            raise TaskConflict("runtime task has no dispatch intent")
        if intent.state not in {"dispatching", "dispatched"}:
            return TaskWorkerProjectionResult(
                intent=intent,
                evidence=None,
                projected=False,
                adopted=False,
                replayed=False,
                deferred_code={
                    "pending": "DISPATCH_PENDING",
                    "reconciliation_required": "RECONCILIATION_REQUIRED",
                }.get(intent.state, "DISPATCH_INTENT_UNSUPPORTED"),
            )
        if self._dispatcher is None:
            raise TaskDispatchError("DISPATCHER_UNAVAILABLE")
        try:
            observed = self._dispatcher.observe_existing_worker_attempt(intent)
        except DispatchError as error:
            if error.code in {
                "ADAPTER_SUBMISSION_BUSY",
                "ADAPTER_SUBMISSION_NOT_FOUND",
                "ADAPTER_SUBMISSION_PREPARING",
                "WORKER_EVIDENCE_NOT_READY",
                "WORKER_EVIDENCE_UNAVAILABLE",
            }:
                return TaskWorkerProjectionResult(
                    intent=intent,
                    evidence=None,
                    projected=False,
                    adopted=False,
                    replayed=False,
                    deferred_code=error.code,
                )
            raise TaskDispatchError(error.code) from error
        except Exception as error:
            raise TaskDispatchError("WORKER_EVIDENCE_UNAVAILABLE") from error
        if not isinstance(observed, Mapping) or set(observed) != {
            "evidence",
            "handle",
        }:
            raise TaskDispatchError("WORKER_EVIDENCE_INVALID")
        evidence = observed.get("evidence")
        handle = observed.get("handle")
        if not isinstance(evidence, Mapping):
            raise TaskDispatchError("WORKER_EVIDENCE_INVALID")
        validated_handle: dict[str, Any] | None = None
        if handle is not None:
            try:
                validated_handle = self._validate_dispatch_receipt(
                    snapshot=snapshot,
                    intent=intent,
                    handle=handle,
                )
            except TaskValidationError as error:
                raise TaskDispatchError("DISPATCH_RECEIPT_INVALID") from error
        if intent.state == "dispatched" and validated_handle != intent.handle:
            raise TaskDispatchError("WORKER_EVIDENCE_INVALID")
        try:
            projection = self._store.record_supervised_worker_observation(
                intent_id=intent.intent_id,
                evidence=evidence,
                handle=validated_handle,
                supervisor_lease=supervisor_lease,
                supervisor_clock=self._runtime_supervisor_clock,
            )
        except RuntimeSupervisorLeaseLost as error:
            raise TaskSupervisorLeaseLost() from error
        except TaskStoreConflict as error:
            raise TaskConflict(str(error)) from error
        projected_intent = projection.intent
        timeout_armed = self._arm_projected_worker_timeout(
            task_id=task_id,
            snapshot=snapshot,
            intent=projected_intent,
            evidence=evidence,
            attempt_id=projection.attempt_id,
            supervisor_lease=supervisor_lease,
        )
        return TaskWorkerProjectionResult(
            intent=projected_intent,
            evidence=copy.deepcopy(dict(evidence)),
            projected=True,
            adopted=projection.adopted,
            replayed=projection.replayed,
            attempt_id=projection.attempt_id,
            observation_sequence=projection.observation_sequence,
            document_hash=projection.document_hash,
            timeout_armed=timeout_armed,
        )

    def recover_runtime_on_startup(
        self,
        project_id: str,
        principal_id: str,
        max_tasks: int = 10000,
    ) -> RuntimeRecoveryResult:
        """Run one bounded, read-only startup inventory before lease acquire.

        First dispatch, exact receipt adoption, Worker evidence projection and
        status catch-up now belong to the active fenced Supervisor term.  This
        pre-lease pass never calls the Adapter and never writes runtime state.
        """

        _validate_opaque_id(project_id, field="project_id")
        _validate_opaque_id(principal_id, field="principal_id")
        if type(max_tasks) is not int or not 1 <= max_tasks <= 10000:
            raise TaskValidationError(
                "INVALID_STARTUP_RECOVERY_LIMIT",
                ["max_tasks must be an integer from 1 to 10000"],
            )

        snapshots: list[TaskSnapshot] = []
        cursor: str | None = None
        while True:
            remaining = max_tasks - len(snapshots)
            page = self.list_tasks(
                project_id=project_id,
                principal_id=principal_id,
                cursor=cursor,
                limit=min(50, remaining),
                view="active",
            )
            snapshots.extend(page.snapshots)
            if page.next_cursor is None:
                break
            if len(snapshots) >= max_tasks:
                raise TaskValidationError(
                    "STARTUP_RECOVERY_LIMIT_EXCEEDED",
                    ["active task count exceeds the bounded startup recovery limit"],
                )
            cursor = page.next_cursor

        receipt_recovery_attempted: list[str] = []
        receipt_recovered: list[str] = []
        pending_deferred: list[str] = []
        dispatching_deferred: list[tuple[str, str]] = []
        status_refreshed: list[str] = []
        status_refresh_failures: list[tuple[str, str]] = []
        reconciliation_required: list[str] = []
        for listed in snapshots:
            if listed.status not in {"Queued", "Running"}:
                continue
            intent = self._store.get_dispatch_intent(listed.task_id)
            if intent is None:
                raise TaskConflict("runtime task has no dispatch intent")
            if intent.state != "dispatched" and listed.status != "Queued":
                raise TaskConflict(
                    "incomplete dispatch intent has an invalid task status"
                )
            if intent.state == "reconciliation_required":
                reconciliation_required.append(listed.task_id)
                continue

            if intent.state == "pending":
                pending_deferred.append(listed.task_id)
                continue
            elif intent.state == "dispatching":
                dispatching_deferred.append(
                    (listed.task_id, "SUPERVISED_DISPATCH_REQUIRED")
                )
                continue
            elif intent.state != "dispatched":
                raise TaskConflict("dispatch intent has an unsupported recovery state")

        return RuntimeRecoveryResult(
            project_id=project_id,
            principal_id=principal_id,
            scanned_task_ids=tuple(snapshot.task_id for snapshot in snapshots),
            receipt_recovery_attempted_task_ids=tuple(
                receipt_recovery_attempted
            ),
            receipt_recovered_task_ids=tuple(receipt_recovered),
            pending_deferred_task_ids=tuple(pending_deferred),
            dispatching_deferred=tuple(dispatching_deferred),
            status_refreshed_task_ids=tuple(status_refreshed),
            status_refresh_failures=tuple(status_refresh_failures),
            reconciliation_required_task_ids=tuple(reconciliation_required),
        )

    def get_dispatch_intent(
        self, task_id: str, *, project_id: str, principal_id: str
    ) -> DispatchIntentSnapshot | None:
        self.get_task(task_id, project_id=project_id, principal_id=principal_id)
        return self._store.get_dispatch_intent(task_id)

    def can_cancel_task(
        self, task_id: str, *, project_id: str, principal_id: str
    ) -> bool:
        """Return the server-proven current exact-attempt cancel capability."""

        snapshot = self.get_task(
            task_id, project_id=project_id, principal_id=principal_id
        )
        if snapshot.cancellation is not None:
            return False
        timeout = getattr(snapshot, "timeout", None)
        if timeout is not None and getattr(timeout, "state", None) != "armed":
            return False
        candidate = self._store.get_task_cancel_candidate(task_id)
        if candidate is None or self._dispatcher is None:
            return False
        intent = self._store.get_dispatch_intent(task_id)
        if intent is None:
            return False
        try:
            return self._dispatcher.supports_exact_cancel(
                intent, attempt_id=candidate["attempt_id"]
            ) is True
        except Exception:
            return False

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
        supervisor_lease: RuntimeSupervisorLease | None = None,
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
        if event["event_type"] in {"cancel_requested", "task_cancelled"}:
            raise TaskConflict(
                "cancellation events are reserved for the supervised cancel path"
            )
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
        if supervisor_lease is not None and (
            not isinstance(supervisor_lease, RuntimeSupervisorLease)
            or supervisor_lease.project_id != project_id
            or supervisor_lease.principal_id != principal_id
        ):
            raise TaskSupervisorLeaseLost()
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
                now=None if supervisor_lease is not None else self._clock(),
                supervisor_lease=supervisor_lease,
                supervisor_clock=(
                    self._runtime_supervisor_clock
                    if supervisor_lease is not None
                    else None
                ),
            )
        except RuntimeSupervisorLeaseLost as error:
            raise TaskSupervisorLeaseLost() from error
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
            or status
            not in {"Queued", "Running", "Succeeded", "Failed", "Cancelled"}
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
            or result["terminal"]
            != (status in {"Succeeded", "Failed", "Cancelled"})
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

    def process_runtime_cancellation(
        self,
        task_id: str,
        *,
        project_id: str,
        principal_id: str,
        supervisor_lease: RuntimeSupervisorLease,
    ) -> TaskCancellationProcessResult:
        """Deliver/finalize one durable exact-attempt cancellation request."""

        _validate_opaque_id(task_id, field="task_id")
        _validate_opaque_id(project_id, field="project_id")
        _validate_opaque_id(principal_id, field="principal_id")
        if (
            not isinstance(supervisor_lease, RuntimeSupervisorLease)
            or supervisor_lease.project_id != project_id
            or supervisor_lease.principal_id != principal_id
        ):
            raise TaskSupervisorLeaseLost()
        snapshot = self.get_task(
            task_id, project_id=project_id, principal_id=principal_id
        )
        cancellation = snapshot.cancellation
        if cancellation is None:
            return TaskCancellationProcessResult(
                snapshot=snapshot,
                state="none",
                adapter_result=None,
                replayed=True,
            )
        if cancellation.state != "requested":
            return TaskCancellationProcessResult(
                snapshot=snapshot,
                state=cancellation.state,
                adapter_result=(
                    None
                    if cancellation.adapter_proof is None
                    else copy.deepcopy(cancellation.adapter_proof)
                ),
                replayed=True,
            )
        intent = self._store.get_dispatch_intent(task_id)
        if (
            intent is None
            or intent.intent_id != cancellation.intent_id
            or intent.state != "dispatched"
            or intent.handle is None
        ):
            raise TaskConflict(
                "pending cancellation lost its dispatched intent binding"
            )
        if self._dispatcher is None:
            raise TaskDispatchError("DISPATCHER_UNAVAILABLE")
        try:
            authorization = self._store.authorize_supervised_cancel(
                request_id=cancellation.request_id,
                supervisor_lease=supervisor_lease,
                supervisor_clock=self._runtime_supervisor_clock,
            )
        except RuntimeSupervisorLeaseLost as error:
            raise TaskSupervisorLeaseLost() from error
        except TaskStoreConflict as error:
            raise TaskConflict(str(error)) from error
        if authorization.cancellation.state != "requested":
            current = self.get_task(
                task_id, project_id=project_id, principal_id=principal_id
            )
            return TaskCancellationProcessResult(
                snapshot=current,
                state=authorization.cancellation.state,
                adapter_result=(
                    None
                    if authorization.cancellation.adapter_proof is None
                    else copy.deepcopy(
                        authorization.cancellation.adapter_proof
                    )
                ),
                replayed=True,
            )
        try:
            proof = self._dispatcher.cancel(
                intent,
                request_id=cancellation.request_id,
                attempt_id=cancellation.attempt_id,
                reason=cancellation.reason,
            )
        except DispatchError as error:
            raise TaskDispatchError(error.code) from error
        except Exception as error:
            raise TaskDispatchError("ADAPTER_CANCEL_UNAVAILABLE") from error
        if not isinstance(proof, Mapping):
            raise TaskDispatchError("ADAPTER_CANCEL_RESPONSE_INVALID")
        adapter_result = copy.deepcopy(dict(proof))
        state = adapter_result.get("state")
        if (
            adapter_result.get("task_id") != task_id
            or adapter_result.get("request_id") != cancellation.request_id
            or adapter_result.get("attempt_id") != cancellation.attempt_id
            or adapter_result.get("reason") != cancellation.reason
            or state
            not in {
                "requested",
                "pending",
                "cancelled",
                "terminal_won",
                "deferred",
            }
        ):
            raise TaskDispatchError("ADAPTER_CANCEL_RESPONSE_INVALID")
        if state in {"requested", "pending", "deferred"}:
            code = adapter_result.get("code")
            return TaskCancellationProcessResult(
                snapshot=snapshot,
                state="requested",
                adapter_result=adapter_result,
                replayed=authorization.replayed,
                deferred_code=(code if state == "deferred" else None),
            )

        if state == "terminal_won":
            terminal = adapter_result.get("terminal_status")
            if terminal not in {"Succeeded", "Failed"}:
                raise TaskDispatchError("ADAPTER_CANCEL_RESPONSE_INVALID")
            try:
                observed = self._dispatcher.status(intent)
            except DispatchError as error:
                raise TaskDispatchError(error.code) from error
            except Exception as error:
                raise TaskDispatchError("ADAPTER_STATUS_UNAVAILABLE") from error
            adapter_status = self._validated_adapter_status(intent, observed)
            if adapter_status["status"] != terminal:
                raise TaskDispatchError("ADAPTER_CANCEL_RESPONSE_INVALID")
            current = self.get_task(
                task_id, project_id=project_id, principal_id=principal_id
            )
            if current.status == "Queued" and terminal == "Succeeded":
                # Preserve the existing one-node event history contract.  The
                # intermediate Running projection is safe; the actual natural
                # terminal and cancellation outcome are committed atomically
                # below under the same active term.
                started = self._adapter_event(
                    snapshot=current,
                    intent=intent,
                    adapter_status=adapter_status,
                    event_type="node_started",
                    sequence=self._store.latest_run_event_sequence(task_id) + 1,
                )
                self.record_run_event(
                    task_id=task_id,
                    project_id=project_id,
                    principal_id=principal_id,
                    expected_status="Queued",
                    event=started,
                    supervisor_lease=supervisor_lease,
                )
                current = self.get_task(
                    task_id, project_id=project_id, principal_id=principal_id
                )
            terminal_event: Mapping[str, Any] | None
            if current.status in {"Succeeded", "Failed"}:
                if current.status != terminal:
                    raise TaskDispatchError("ADAPTER_STATUS_CONFLICT")
                terminal_event = None
            else:
                event_type = (
                    "node_succeeded"
                    if terminal == "Succeeded"
                    else "node_failed"
                )
                terminal_event = self._adapter_event(
                    snapshot=current,
                    intent=intent,
                    adapter_status=adapter_status,
                    event_type=event_type,
                    sequence=self._store.latest_run_event_sequence(task_id) + 1,
                )
                self._validate_schema(
                    "run-event.schema.json", terminal_event
                )
                _validate_run_event_semantics(terminal_event)
                _validate_run_event_binding(current, terminal_event)
            try:
                completed = self._store.complete_supervised_cancel(
                    request_id=cancellation.request_id,
                    result="terminal_preempted",
                    terminal_event=terminal_event,
                    adapter_proof=adapter_result,
                    supervisor_lease=supervisor_lease,
                    supervisor_clock=self._runtime_supervisor_clock,
                )
            except RuntimeSupervisorLeaseLost as error:
                raise TaskSupervisorLeaseLost() from error
            except TaskStoreConflict as error:
                raise TaskConflict(str(error)) from error
            return TaskCancellationProcessResult(
                snapshot=completed.snapshot,
                state=completed.cancellation.state,
                adapter_result=adapter_result,
                replayed=completed.replayed,
            )

        if adapter_result.get("terminal_status") != "Cancelled":
            raise TaskDispatchError("ADAPTER_CANCEL_RESPONSE_INVALID")
        sequence = self._store.latest_run_event_sequence(task_id) + 1
        occurred_at = self._runtime_supervisor_clock()
        _, event_identity = encode_document(
            {
                "request_id": cancellation.request_id,
                "event_type": "task_cancelled",
                "proof_hash": adapter_result.get("proof_hash"),
                "sequence": sequence,
            }
        )
        event = {
            "schema_version": "1.0.0",
            "event_id": "event-"
            + event_identity.removeprefix("sha256:")[:32],
            "sequence": sequence,
            "task_id": task_id,
            "node_id": intent.node_id,
            "event_type": "task_cancelled",
            "task_status": "Cancelled",
            "occurred_at": occurred_at,
            "fingerprint": copy.deepcopy(intent.handle["fingerprint"]),
            "extensions": {
                "org.agent_rpc.cancellation": {
                    "request_id": cancellation.request_id,
                    "attempt_id": cancellation.attempt_id,
                    "reason": cancellation.reason,
                    "proof_hash": adapter_result.get("proof_hash"),
                }
            },
        }
        self._validate_schema("run-event.schema.json", event)
        _validate_run_event_semantics(event)
        _validate_run_event_binding(snapshot, event)
        try:
            completed = self._store.complete_supervised_cancel(
                request_id=cancellation.request_id,
                result="cancel_confirmed",
                terminal_event=event,
                adapter_proof=adapter_result,
                supervisor_lease=supervisor_lease,
                supervisor_clock=self._runtime_supervisor_clock,
            )
        except RuntimeSupervisorLeaseLost as error:
            raise TaskSupervisorLeaseLost() from error
        except TaskStoreConflict as error:
            raise TaskConflict(str(error)) from error
        return TaskCancellationProcessResult(
            snapshot=completed.snapshot,
            state=completed.cancellation.state,
            adapter_result=adapter_result,
            replayed=completed.replayed,
        )

    def process_runtime_timeout(
        self,
        task_id: str,
        *,
        project_id: str,
        principal_id: str,
        supervisor_lease: RuntimeSupervisorLease,
    ) -> TaskTimeoutProcessResult:
        """Arm, deliver, or finalize one automatic exact-attempt timeout."""

        _validate_opaque_id(task_id, field="task_id")
        _validate_opaque_id(project_id, field="project_id")
        _validate_opaque_id(principal_id, field="principal_id")
        if (
            not isinstance(supervisor_lease, RuntimeSupervisorLease)
            or supervisor_lease.project_id != project_id
            or supervisor_lease.principal_id != principal_id
        ):
            raise TaskSupervisorLeaseLost()
        snapshot = self.get_task(
            task_id, project_id=project_id, principal_id=principal_id
        )
        timeout = getattr(snapshot, "timeout", None)
        if timeout is None:
            return TaskTimeoutProcessResult(
                snapshot=snapshot,
                state="none",
                adapter_result=None,
                replayed=True,
            )
        timeout_state = getattr(timeout, "state", None)
        if timeout_state not in {
            "armed",
            "requested",
            "timed_out",
            "superseded",
            "not_triggered",
            "suppressed",
        }:
            raise TaskConflict("task timeout state is invalid")
        if timeout_state not in {"armed", "requested"}:
            return TaskTimeoutProcessResult(
                snapshot=snapshot,
                state=timeout_state,
                adapter_result=(
                    None
                    if getattr(timeout, "adapter_proof", None) is None
                    else copy.deepcopy(timeout.adapter_proof)
                ),
                replayed=True,
            )
        intent = self._store.get_dispatch_intent(task_id)
        if (
            intent is None
            or intent.intent_id != getattr(timeout, "intent_id", None)
            or intent.state != "dispatched"
            or intent.handle is None
        ):
            raise TaskConflict("task timeout lost its dispatched intent binding")
        if self._dispatcher is None:
            raise TaskDispatchError("DISPATCHER_UNAVAILABLE")
        try:
            authorization = self._store.authorize_supervised_timeout(
                timeout_id=timeout.timeout_id,
                supervisor_lease=supervisor_lease,
                supervisor_clock=self._runtime_supervisor_clock,
            )
        except RuntimeSupervisorLeaseLost as error:
            raise TaskSupervisorLeaseLost() from error
        except TaskStoreConflict as error:
            current = self.get_task(
                task_id, project_id=project_id, principal_id=principal_id
            )
            current_timeout = getattr(current, "timeout", None)
            if getattr(current_timeout, "state", None) == "suppressed":
                return TaskTimeoutProcessResult(
                    snapshot=current,
                    state="suppressed",
                    adapter_result=None,
                    replayed=True,
                )
            raise TaskConflict(str(error)) from error
        authorized_timeout = getattr(authorization, "timeout", None)
        authorized = getattr(authorization, "authorized", None)
        authorization_replayed = getattr(authorization, "replayed", None)
        if (
            getattr(authorized_timeout, "timeout_id", None) != timeout.timeout_id
            or getattr(authorized_timeout, "task_id", None) != task_id
            or getattr(authorized_timeout, "attempt_id", None)
            != timeout.attempt_id
            or getattr(authorized_timeout, "state", None)
            not in {
                "armed",
                "requested",
                "timed_out",
                "superseded",
                "not_triggered",
                "suppressed",
            }
            or type(authorized) is not bool
            or type(authorization_replayed) is not bool
        ):
            raise TaskDispatchError("TIMEOUT_AUTHORIZATION_INVALID")
        if not authorized:
            current = self.get_task(
                task_id, project_id=project_id, principal_id=principal_id
            )
            current_timeout = getattr(current, "timeout", None)
            current_state = getattr(current_timeout, "state", None)
            if current_state not in {
                "armed",
                "requested",
                "timed_out",
                "superseded",
                "not_triggered",
                "suppressed",
            }:
                raise TaskDispatchError("TIMEOUT_AUTHORIZATION_INVALID")
            return TaskTimeoutProcessResult(
                snapshot=current,
                state=current_state,
                adapter_result=(
                    None
                    if getattr(current_timeout, "adapter_proof", None) is None
                    else copy.deepcopy(current_timeout.adapter_proof)
                ),
                replayed=authorization_replayed,
                deferred_code=(
                    "TIMEOUT_NOT_DUE" if current_state == "armed" else None
                ),
            )
        if getattr(authorized_timeout, "state", None) != "requested":
            raise TaskDispatchError("TIMEOUT_AUTHORIZATION_INVALID")
        try:
            proof = self._dispatcher.timeout(
                intent,
                timeout_id=authorized_timeout.timeout_id,
                attempt_id=authorized_timeout.attempt_id,
                wall_time_seconds=authorized_timeout.wall_time_seconds,
                started_at=authorized_timeout.started_at,
                deadline_at=authorized_timeout.deadline_at,
            )
        except DispatchError as error:
            raise TaskDispatchError(error.code) from error
        except Exception as error:
            raise TaskDispatchError("ADAPTER_TIMEOUT_UNAVAILABLE") from error
        if not isinstance(proof, Mapping):
            raise TaskDispatchError("ADAPTER_TIMEOUT_RESPONSE_INVALID")
        adapter_result = copy.deepcopy(dict(proof))
        state = adapter_result.get("state")
        if (
            adapter_result.get("task_id") != task_id
            or adapter_result.get("request_id")
            != authorized_timeout.timeout_id
            or adapter_result.get("attempt_id")
            != authorized_timeout.attempt_id
            or adapter_result.get("reason") != "wall_time_exceeded"
            or adapter_result.get("wall_time_seconds")
            != authorized_timeout.wall_time_seconds
            or adapter_result.get("started_at")
            != authorized_timeout.started_at
            or adapter_result.get("deadline_at")
            != authorized_timeout.deadline_at
            or state
            not in {
                "requested",
                "pending",
                "timed_out",
                "terminal_won",
                "deferred",
            }
        ):
            raise TaskDispatchError("ADAPTER_TIMEOUT_RESPONSE_INVALID")
        if state in {"requested", "pending", "deferred"}:
            current = self.get_task(
                task_id, project_id=project_id, principal_id=principal_id
            )
            current_timeout = getattr(current, "timeout", None)
            if getattr(current_timeout, "state", None) != "requested":
                raise TaskDispatchError("TIMEOUT_STATE_CONFLICT")
            return TaskTimeoutProcessResult(
                snapshot=current,
                state="requested",
                adapter_result=adapter_result,
                replayed=authorization_replayed,
                deferred_code=(
                    adapter_result.get("code") if state == "deferred" else None
                ),
            )

        if state == "terminal_won":
            terminal = adapter_result.get("terminal_status")
            if terminal not in {"Succeeded", "Failed"}:
                raise TaskDispatchError("ADAPTER_TIMEOUT_RESPONSE_INVALID")
            try:
                observed = self._dispatcher.status(intent)
            except DispatchError as error:
                raise TaskDispatchError(error.code) from error
            except Exception as error:
                raise TaskDispatchError("ADAPTER_STATUS_UNAVAILABLE") from error
            adapter_status = self._validated_adapter_status(intent, observed)
            if adapter_status["status"] != terminal:
                raise TaskDispatchError("ADAPTER_TIMEOUT_RESPONSE_INVALID")
            current = self.get_task(
                task_id, project_id=project_id, principal_id=principal_id
            )
            if current.status == "Queued" and terminal == "Succeeded":
                started = self._adapter_event(
                    snapshot=current,
                    intent=intent,
                    adapter_status=adapter_status,
                    event_type="node_started",
                    sequence=self._store.latest_run_event_sequence(task_id) + 1,
                )
                self.record_run_event(
                    task_id=task_id,
                    project_id=project_id,
                    principal_id=principal_id,
                    expected_status="Queued",
                    event=started,
                    supervisor_lease=supervisor_lease,
                )
                current = self.get_task(
                    task_id, project_id=project_id, principal_id=principal_id
                )
            terminal_event: Mapping[str, Any] | None
            if current.status in {"Succeeded", "Failed"}:
                if current.status != terminal:
                    raise TaskDispatchError("ADAPTER_STATUS_CONFLICT")
                terminal_event = None
            else:
                event_type = (
                    "node_succeeded"
                    if terminal == "Succeeded"
                    else "node_failed"
                )
                terminal_event = self._adapter_event(
                    snapshot=current,
                    intent=intent,
                    adapter_status=adapter_status,
                    event_type=event_type,
                    sequence=self._store.latest_run_event_sequence(task_id) + 1,
                )
                self._validate_schema("run-event.schema.json", terminal_event)
                _validate_run_event_semantics(terminal_event)
                _validate_run_event_binding(current, terminal_event)
            try:
                completed = self._store.complete_supervised_timeout(
                    timeout_id=authorized_timeout.timeout_id,
                    result="terminal_preempted",
                    terminal_event=terminal_event,
                    adapter_proof=adapter_result,
                    supervisor_lease=supervisor_lease,
                    supervisor_clock=self._runtime_supervisor_clock,
                )
            except RuntimeSupervisorLeaseLost as error:
                raise TaskSupervisorLeaseLost() from error
            except TaskStoreConflict as error:
                raise TaskConflict(str(error)) from error
            return TaskTimeoutProcessResult(
                snapshot=completed.snapshot,
                state=completed.timeout.state,
                adapter_result=adapter_result,
                replayed=completed.replayed,
            )

        if (
            adapter_result.get("terminal_status") != "Failed"
            or adapter_result.get("terminal_failure_code")
            != "WALL_TIME_EXCEEDED"
        ):
            raise TaskDispatchError("ADAPTER_TIMEOUT_RESPONSE_INVALID")
        current = self.get_task(
            task_id, project_id=project_id, principal_id=principal_id
        )
        sequence = self._store.latest_run_event_sequence(task_id) + 1
        occurred_at = self._runtime_supervisor_clock()
        _, event_identity = encode_document(
            {
                "timeout_id": authorized_timeout.timeout_id,
                "event_type": "node_failed",
                "proof_hash": adapter_result.get("proof_hash"),
                "sequence": sequence,
            }
        )
        event = {
            "schema_version": "1.0.0",
            "event_id": "event-"
            + event_identity.removeprefix("sha256:")[:32],
            "sequence": sequence,
            "task_id": task_id,
            "node_id": intent.node_id,
            "event_type": "node_failed",
            "task_status": "Failed",
            "error": {
                "code": "wall_time_exceeded",
                "message": "FWI Worker exceeded its wall-time limit",
                "retryable": False,
            },
            "occurred_at": occurred_at,
            "fingerprint": copy.deepcopy(intent.handle["fingerprint"]),
            "extensions": {
                "org.agent_rpc.timeout": {
                    "timeout_id": authorized_timeout.timeout_id,
                    "attempt_id": authorized_timeout.attempt_id,
                    "wall_time_seconds": authorized_timeout.wall_time_seconds,
                    "started_at": authorized_timeout.started_at,
                    "deadline_at": authorized_timeout.deadline_at,
                    "failure_code": "WALL_TIME_EXCEEDED",
                    "proof_hash": adapter_result.get("proof_hash"),
                }
            },
        }
        self._validate_schema("run-event.schema.json", event)
        _validate_run_event_semantics(event)
        _validate_run_event_binding(current, event)
        try:
            completed = self._store.complete_supervised_timeout(
                timeout_id=authorized_timeout.timeout_id,
                result="timeout_confirmed",
                terminal_event=event,
                adapter_proof=adapter_result,
                supervisor_lease=supervisor_lease,
                supervisor_clock=self._runtime_supervisor_clock,
            )
        except RuntimeSupervisorLeaseLost as error:
            raise TaskSupervisorLeaseLost() from error
        except TaskStoreConflict as error:
            raise TaskConflict(str(error)) from error
        return TaskTimeoutProcessResult(
            snapshot=completed.snapshot,
            state=completed.timeout.state,
            adapter_result=adapter_result,
            replayed=completed.replayed,
        )

    def refresh_runtime_status(
        self,
        task_id: str,
        *,
        project_id: str,
        principal_id: str,
        supervisor_lease: RuntimeSupervisorLease | None = None,
    ) -> TaskRuntimeResult:
        """Observe one trusted Adapter receipt and advance SQLite monotonically."""

        snapshot = self.get_task(
            task_id, project_id=project_id, principal_id=principal_id
        )
        if supervisor_lease is not None and (
            not isinstance(supervisor_lease, RuntimeSupervisorLease)
            or supervisor_lease.project_id != project_id
            or supervisor_lease.principal_id != principal_id
        ):
            raise TaskSupervisorLeaseLost()
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
        timeout = getattr(snapshot, "timeout", None)
        supervised_control_pending = (
            snapshot.cancellation is not None
            and snapshot.cancellation.state == "requested"
        ) or (
            timeout is not None
            and getattr(timeout, "state", None) == "requested"
        )
        if supervised_control_pending:
            # Durable lifecycle control has a dedicated fenced state machine.
            # Neither a browser GET nor an ordinary active-term status pass
            # may race that state machine or publish its terminal result.
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
            event_ids: set[str] = set()
            last_sequence = 0
            previous_worker_time: str | None = None
            previous_progress_completed: int | None = None
            after_sequence = 0
            event_high_water = self._store.latest_run_event_sequence(task_id)
            if event_high_water > MAX_RUNTIME_EVENT_SCAN:
                raise TaskDispatchError("RUN_EVENT_HISTORY_LIMIT_EXCEEDED")
            while after_sequence < event_high_water:
                event_page = self.list_run_events(
                    task_id,
                    project_id=project_id,
                    principal_id=principal_id,
                    after_sequence=after_sequence,
                    limit=min(1000, event_high_water - after_sequence),
                )
                if not event_page:
                    raise TaskConflict("run event history changed during status scan")
                page_sequence = after_sequence
                for event in event_page:
                    sequence = event.get("sequence")
                    if (
                        type(sequence) is not int
                        or sequence != page_sequence + 1
                        or sequence > event_high_water
                    ):
                        raise TaskConflict(
                            "run event history did not advance monotonically"
                        )
                    page_sequence = sequence
                    event_ids.add(event["event_id"])
                    last_sequence = sequence
                    adapter_observation = event.get("extensions", {}).get(
                        "org.agent_rpc.adapter_status"
                    )
                    if isinstance(adapter_observation, Mapping) and isinstance(
                        adapter_observation.get("worker_updated_at"), str
                    ):
                        previous_worker_time = adapter_observation[
                            "worker_updated_at"
                        ]
                    if (
                        event.get("event_type") == "node_progress"
                        and event.get("node_id") == intent.node_id
                    ):
                        progress = event.get("progress")
                        if isinstance(progress, Mapping) and type(
                            progress.get("completed")
                        ) is int:
                            previous_progress_completed = progress["completed"]
                after_sequence = last_sequence
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
                if (
                    previous_progress_completed is not None
                    and adapter_status["completed"]
                    < previous_progress_completed
                ):
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
                sequence=last_sequence + 1,
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
                    supervisor_lease=supervisor_lease,
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
        snapshot: TaskSnapshot,
        intent: DispatchIntentSnapshot,
        manifests: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if intent.handle is None:
            raise TaskDispatchError("DISPATCH_RECEIPT_UNAVAILABLE")
        if not isinstance(snapshot.plan, Mapping):
            raise TaskDispatchError("ADAPTER_ARTIFACT_INVALID")
        nodes = snapshot.plan.get("nodes")
        matching_nodes = (
            [node for node in nodes if isinstance(node, Mapping)
             and node.get("node_id") == intent.node_id]
            if isinstance(nodes, list)
            else []
        )
        if len(matching_nodes) != 1:
            raise TaskDispatchError("ADAPTER_ARTIFACT_INVALID")
        planned_outputs = matching_nodes[0].get("outputs")
        if not isinstance(planned_outputs, list) or not planned_outputs:
            raise TaskDispatchError("ADAPTER_ARTIFACT_INVALID")
        presentation_for_type = {
            "inverted_velocity_model_2d": ("application/x-npy", "download"),
            "loss_curve": ("text/csv", "line_chart"),
            "figure": ("image/png", "image"),
        }
        figure_contract = {
            "true_model_figure": ("true_model", 1440, 608),
            "initial_model_figure": ("initial_model", 1440, 608),
            "inverted_model_figure": ("inverted_model", 1440, 608),
            "model_error_figure": ("model_error", 1440, 608),
            "shot_gathers_figure": ("shot_gathers", 2160, 800),
            "loss_curve_figure": ("loss_curve", 1120, 720),
        }
        expected_outputs: set[tuple[str, str, str, str]] = set()
        for output in planned_outputs:
            if not isinstance(output, Mapping):
                raise TaskDispatchError("ADAPTER_ARTIFACT_INVALID")
            port = output.get("port")
            data_type = output.get("data_type")
            if not isinstance(port, str) or not isinstance(data_type, str):
                raise TaskDispatchError("ADAPTER_ARTIFACT_INVALID")
            presentation = presentation_for_type.get(data_type)
            if presentation is None:
                raise TaskDispatchError("ADAPTER_ARTIFACT_INVALID")
            expected_outputs.add((port, data_type, *presentation))
        if len(expected_outputs) != len(planned_outputs):
            raise TaskDispatchError("ADAPTER_ARTIFACT_INVALID")
        identifiers: set[str] = set()
        observed_outputs: set[tuple[str, str, str, str]] = set()
        display_orders: set[int] = set()
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
            display = manifest.get("display")
            component = (
                display.get("component") if isinstance(display, Mapping) else None
            )
            display_order = (
                display.get("order") if isinstance(display, Mapping) else None
            )
            figure_extension = (
                extensions.get("org.agent_rpc.figure")
                if isinstance(extensions, Mapping)
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
                or not isinstance(component, str)
                or type(display_order) is not int
                or display_order < 0
                or display_order in display_orders
            ):
                raise TaskDispatchError("ADAPTER_ARTIFACT_INVALID")
            if manifest.get("artifact_type") == "figure":
                figure_id = (
                    figure_extension.get("figure_id")
                    if isinstance(figure_extension, Mapping)
                    else None
                )
                width_px = (
                    figure_extension.get("width_px")
                    if isinstance(figure_extension, Mapping)
                    else None
                )
                height_px = (
                    figure_extension.get("height_px")
                    if isinstance(figure_extension, Mapping)
                    else None
                )
                if (
                    not isinstance(figure_id, str)
                    or OPAQUE_ID.fullmatch(figure_id) is None
                    or type(width_px) is not int
                    or not 1 <= width_px <= 8192
                    or type(height_px) is not int
                    or not 1 <= height_px <= 8192
                    or figure_contract.get(output_port)
                    != (figure_id, width_px, height_px)
                ):
                    raise TaskDispatchError("ADAPTER_ARTIFACT_INVALID")
            identifiers.add(artifact_id)
            display_orders.add(display_order)
            observed_outputs.add(
                (
                    output_port,
                    manifest["artifact_type"],
                    manifest["media_type"],
                    component,
                )
            )
            validated.append(copy.deepcopy(manifest))
        if (
            len(validated) != len(expected_outputs)
            or len(observed_outputs) != len(validated)
            or observed_outputs != expected_outputs
            or display_orders != set(range(len(expected_outputs)))
        ):
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
        return self._validate_collected_artifacts(
            runtime.snapshot, runtime.intent, manifests
        )

    def read_artifact(
        self,
        task_id: str,
        artifact_id: str,
        *,
        project_id: str,
        principal_id: str,
    ) -> tuple[dict[str, Any], bytes]:
        _validate_opaque_id(artifact_id, field="artifact_id")
        runtime = self.refresh_runtime_status(
            task_id, project_id=project_id, principal_id=principal_id
        )
        if runtime.snapshot.status != "Succeeded" or runtime.intent is None:
            raise TaskDispatchError("RESULT_NOT_READY")
        if self._dispatcher is None:
            raise TaskDispatchError("DISPATCH_RECEIPT_UNAVAILABLE")
        try:
            manifests, manifest, data = self._dispatcher.read_artifact(
                runtime.intent, artifact_id
            )
        except DispatchError as error:
            if error.code == "ARTIFACT_NOT_FOUND":
                raise TaskNotFound(
                    "artifact does not exist in the requested task"
                ) from error
            raise TaskDispatchError(error.code) from error
        except Exception as error:
            raise TaskDispatchError("ADAPTER_ARTIFACT_UNAVAILABLE") from error
        if (
            not isinstance(manifests, list)
            or not all(isinstance(value, dict) for value in manifests)
            or not isinstance(manifest, dict)
        ):
            raise TaskDispatchError("ADAPTER_ARTIFACT_INVALID")
        validated = self._validate_collected_artifacts(
            runtime.snapshot, runtime.intent, manifests
        )
        expected = next(
            (value for value in validated if value["artifact_id"] == artifact_id),
            None,
        )
        if expected is None:
            raise TaskNotFound("artifact does not exist in the requested task")
        if (
            manifest != expected
            or not isinstance(data, bytes)
            or len(data) != expected["size_bytes"]
            or "sha256:" + hashlib.sha256(data).hexdigest()
            != expected["content_hash"]
        ):
            raise TaskDispatchError("ADAPTER_ARTIFACT_INVALID")
        return copy.deepcopy(manifest), data
