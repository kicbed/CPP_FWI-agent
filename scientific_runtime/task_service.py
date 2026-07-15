"""Validated, side-effect-free P1.1 service for durable task aggregates."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from scientific_runtime_contracts import compute_plan_hash, schema_errors

from .task_store import (
    ALLOWED_TRANSITIONS,
    IdempotencyConflict,
    TASK_STATUSES,
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
    """A create key was reused for different request content."""


@dataclass(frozen=True)
class CreateTaskResult:
    snapshot: TaskSnapshot
    replayed: bool


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
    """Create and query durable tasks without submitting numerical work.

    P1.1 intentionally has no queue/submit method.  Dataset Catalog snapshots,
    approval budget consumption, the full deterministic gate, and the Queued
    event must first be combined in one later SQLite transaction.
    """

    def __init__(
        self,
        store: TaskStore,
        *,
        task_id_factory: Callable[[], str] = _task_id,
        clock: Callable[[], str] = _utc_now,
    ) -> None:
        self._store = store
        self._task_id_factory = task_id_factory
        self._clock = clock

    @staticmethod
    def _validate_schema(name: str, value: Mapping[str, Any]) -> None:
        errors = schema_errors(name, value)
        if errors:
            raise TaskValidationError("SCHEMA_INVALID", errors)

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
    ) -> TaskSnapshot:
        _validate_opaque_id(task_id, field="task_id")
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool):
            raise TaskValidationError(
                "INVALID_REVISION", ["expected_revision must be an integer"]
            )
        self._validate_schema("task-draft.schema.json", draft)
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
        try:
            return self._store.append_draft_revision(
                task_id=task_id,
                expected_revision=expected_revision,
                draft=draft,
                now=self._clock(),
            )
        except TaskStoreConflict as error:
            raise TaskConflict(str(error)) from error

    def persist_plan(
        self,
        *,
        task_id: str,
        project_id: str,
        principal_id: str,
        plan: Mapping[str, Any],
    ) -> TaskSnapshot:
        _validate_opaque_id(task_id, field="task_id")
        self._validate_schema("plan-graph.schema.json", plan)
        try:
            expected_hash = compute_plan_hash(plan)
        except ValueError as error:
            raise TaskValidationError("PLAN_CANONICALIZATION_INVALID", [str(error)]) from error
        if plan["plan_hash"] != expected_hash:
            raise TaskValidationError(
                "PLAN_HASH_INVALID", ["plan_hash does not match canonical plan content"]
            )
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
        try:
            return self._store.store_plan(
                task_id=task_id, plan=plan, now=self._clock()
            )
        except TaskStoreConflict as error:
            raise TaskConflict(str(error)) from error

    def persist_approval(
        self,
        *,
        task_id: str,
        project_id: str,
        principal_id: str,
        approval: Mapping[str, Any],
    ) -> TaskSnapshot:
        _validate_opaque_id(task_id, field="task_id")
        current = self.get_task(
            task_id, project_id=project_id, principal_id=principal_id
        )
        self._validate_schema("approval-decision.schema.json", approval)
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
                task_id=task_id, approval=approval, now=self._clock()
            )
        except TaskStoreConflict as error:
            raise TaskConflict(str(error)) from error

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

        ``task_queued`` is deliberately reserved: P1.1 has no Dataset Catalog
        transaction, approval-budget consumer, or submit API, so this service
        cannot authorize entry into Queued.
        """

        _validate_opaque_id(task_id, field="task_id")
        if not isinstance(expected_status, str) or expected_status not in TASK_STATUSES:
            raise TaskValidationError(
                "INVALID_EXPECTED_STATUS",
                ["expected_status must be a known task status"],
            )
        self._validate_schema("run-event.schema.json", event)
        if event["event_type"] == "task_queued":
            raise TaskConflict("task_queued is reserved for the future atomic submit path")
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
        try:
            return self._store.commit_runtime_transition(
                task_id=task_id,
                expected_status=expected_status,
                event=event,
                now=self._clock(),
            )
        except TaskStoreConflict as error:
            raise TaskConflict(str(error)) from error
