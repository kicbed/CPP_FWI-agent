"""Trusted bridge from durable dispatch intents to the fixed FWI Adapter."""

from __future__ import annotations

import copy
import contextlib
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Mapping, Protocol

from .fwi_adapter import (
    ADAPTER_VERSION,
    ALGORITHM_ID,
    LOGICAL_ENTRYPOINT,
    SUPPORTED_ADAPTER_VERSIONS,
    SUPPORTED_MANAGED_REQUEST_VERSIONS,
    AdapterCheckpointProof,
    AdapterDispatchNotStartedProof,
    AdapterError,
    AdapterExistingDispatchReceiptProof,
    AdapterHandle,
    AdapterManagedCancelProof,
    AdapterManagedTimeoutProof,
    AdapterPreRunningRetryProof,
    AdapterReconciliationDeferred,
    AdapterWorkerExitRetryProof,
    DeepwaveAdapter,
    is_supported_receipt_binding,
)
from .task_store import (
    DagNodeInputBindingFact,
    DispatchIntentSnapshot,
    RetryExhaustionCleanupProof,
    TaskSnapshot,
    encode_document,
)


_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_MANAGED_SUBMISSION_ID = re.compile(r"^submission-[0-9a-f]{64}$")
_MANAGED_ATTEMPT_ID = re.compile(r"^attempt-[0-9a-f]{32}$")
_MANAGED_JOB_ID = re.compile(r"^fwi-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$")
_MANAGED_CHECKPOINT_ID = re.compile(r"^checkpoint-[0-9a-f]{32}$")
_MANAGED_RESUME_ID = re.compile(r"^resume-[0-9a-f]{32}$")


def _timestamp_is_invalid(value: Any) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return True
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return True
    return parsed.tzinfo is None


class DispatchError(RuntimeError):
    """A stable, path-free preparation or runtime-dispatch failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class DispatchDeferred(DispatchError):
    """No terminal dispatch outcome is known; keep the intent recoverable."""


DEFERRED_DISPATCH_CODES = frozenset(
    {
        "ADAPTER_CONCURRENCY_LIMIT",
        "ADAPTER_SUBMISSION_BUSY",
        "SUBMISSION_LAUNCH_PENDING",
    }
)


@dataclass(frozen=True)
class DispatchPreparation:
    """Side-effect-free Adapter evidence derived from a durable task view."""

    adapter_id: str
    adapter_version: str
    request: dict[str, Any]
    queue_fingerprint: dict[str, Any]


@dataclass(frozen=True)
class DispatchReceiptProbe:
    """Bounded positive result from a zero-launch reconciliation probe."""

    evidence_kind: Literal["managed_worker_receipt", "private_receipt"]
    handle: dict[str, Any]
    evidence: dict[str, Any] | None
    private_schema_version: str
    receipt_record_hash: str | None


@dataclass(frozen=True)
class DispatchNotStartedProof:
    """Independently validated exact pre-running negative dispatch proof."""

    result: Literal["not_dispatched"]
    evidence_kind: Literal["managed_pre_running_failure"]
    adapter_version: str
    private_schema_version: str
    private_record_hash: str
    private_proof_hash: str
    attempt_id: str
    attempt_number: int
    evidence: dict[str, Any]


@dataclass(frozen=True)
class DispatchReconciliationDeferred:
    """Typed reconciliation result that preserves a recoverable intent."""

    classification: Literal["transient", "uncertain"]
    failure_code: str


@dataclass(frozen=True)
class DispatchRetryProof:
    """Path-free exact stopped-attempt proof read from private Adapter state."""

    failure_kind: Literal["pre_running_launch_failure", "worker_exit"]
    previous_attempt_id: str
    previous_attempt_number: int
    private_schema_version: str
    private_proof_hash: str
    evidence: dict[str, Any]
    private_evidence: dict[str, Any] | None = None


@dataclass(frozen=True)
class DispatchCheckpointProof:
    """Validated path-bounded checkpoint observation."""

    task_id: str
    node_id: str
    submission_id: str
    attempt_id: str
    attempt_number: int
    checkpoint_id: str
    checkpoint_index: int
    completed_updates: int
    binding_hash: str
    submission_receipt_record_hash: str
    ready_record_hash: str
    checkpoint_manifest_relative_path: str
    checkpoint_manifest_size_bytes: int
    checkpoint_manifest_hash: str
    checkpoint_receipt_record_hash: str
    checkpoint_proof_hash: str | None
    checkpoint_created_at: str
    state: Literal["waiting", "requested", "resumed", "action_required"]
    resume_id: str | None
    resume_request_record_hash: str | None
    resume_acknowledgement_record_hash: str | None
    resume_acknowledged_at: str | None
    proof_hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0.0",
            "task_id": self.task_id,
            "node_id": self.node_id,
            "submission_id": self.submission_id,
            "attempt_id": self.attempt_id,
            "attempt_number": self.attempt_number,
            "checkpoint_id": self.checkpoint_id,
            "checkpoint_index": self.checkpoint_index,
            "completed_updates": self.completed_updates,
            "binding_hash": self.binding_hash,
            "submission_receipt_record_hash": (
                self.submission_receipt_record_hash
            ),
            "ready_record_hash": self.ready_record_hash,
            "checkpoint_manifest_relative_path": (
                self.checkpoint_manifest_relative_path
            ),
            "checkpoint_manifest_size_bytes": (
                self.checkpoint_manifest_size_bytes
            ),
            "checkpoint_manifest_hash": self.checkpoint_manifest_hash,
            "checkpoint_receipt_record_hash": (
                self.checkpoint_receipt_record_hash
            ),
            "checkpoint_proof_hash": self.checkpoint_proof_hash,
            "checkpoint_created_at": self.checkpoint_created_at,
            "state": self.state,
            "resume_id": self.resume_id,
            "resume_request_record_hash": self.resume_request_record_hash,
            "resume_acknowledgement_record_hash": (
                self.resume_acknowledgement_record_hash
            ),
            "resume_acknowledged_at": self.resume_acknowledged_at,
            "proof_hash": self.proof_hash,
        }


@dataclass(frozen=True)
class DispatchCheckpointResumeResult(DispatchCheckpointProof):
    """Result of one no-launch resume request or exact replay."""


class TaskDispatcher(Protocol):
    """Fixed dispatcher with supervised submit and zero-relaunch receipt paths."""

    def prepare(self, snapshot: TaskSnapshot) -> DispatchPreparation:
        ...

    def prepare_node(
        self,
        snapshot: TaskSnapshot,
        *,
        node_id: str,
        input_binding: DagNodeInputBindingFact,
    ) -> DispatchPreparation:
        ...

    def dispatch(self, intent: DispatchIntentSnapshot) -> dict[str, Any]:
        ...

    def supports_supervised_dispatch(self, intent: DispatchIntentSnapshot) -> bool:
        ...

    def ensure_first_dispatch(
        self, intent: DispatchIntentSnapshot
    ) -> dict[str, Any]:
        ...

    def recover_existing_receipt(
        self, intent: DispatchIntentSnapshot
    ) -> dict[str, Any]:
        ...

    def recover_existing_private_receipt(
        self, intent: DispatchIntentSnapshot
    ) -> dict[str, Any]:
        ...

    def observe_existing_worker_attempt(
        self, intent: DispatchIntentSnapshot
    ) -> dict[str, Any]:
        ...

    def probe_existing_dispatch_receipt(
        self, intent: DispatchIntentSnapshot
    ) -> DispatchReceiptProbe:
        ...

    def probe_dispatch_reconciliation(
        self, intent: DispatchIntentSnapshot
    ) -> (
        DispatchReceiptProbe
        | DispatchNotStartedProof
        | DispatchReconciliationDeferred
    ):
        ...

    def probe_pre_running_retry(
        self, intent: DispatchIntentSnapshot
    ) -> DispatchRetryProof:
        ...

    def probe_pre_running_retry_exhaustion(
        self, intent: DispatchIntentSnapshot
    ) -> DispatchRetryProof:
        ...

    def probe_worker_exit_retry(
        self, intent: DispatchIntentSnapshot
    ) -> DispatchRetryProof:
        ...

    def probe_worker_exit_retry_exhaustion(
        self, intent: DispatchIntentSnapshot
    ) -> DispatchRetryProof:
        ...

    def retry_pre_running(
        self, intent: DispatchIntentSnapshot, *, authorization: Mapping[str, Any]
    ) -> dict[str, Any]:
        ...

    def retry_worker_exit(
        self, intent: DispatchIntentSnapshot, *, authorization: Mapping[str, Any]
    ) -> dict[str, Any]:
        ...

    def probe_runtime_checkpoint(
        self, intent: DispatchIntentSnapshot
    ) -> DispatchCheckpointProof | None:
        ...

    def resume_runtime_checkpoint(
        self,
        intent: DispatchIntentSnapshot,
        *,
        authorization: Mapping[str, Any],
    ) -> DispatchCheckpointResumeResult:
        ...

    def status(self, intent: DispatchIntentSnapshot) -> dict[str, Any]:
        ...

    def supports_exact_cancel(
        self, intent: DispatchIntentSnapshot, *, attempt_id: str
    ) -> bool:
        ...

    def supports_exact_timeout(
        self, intent: DispatchIntentSnapshot, *, attempt_id: str
    ) -> dict[str, Any] | None:
        ...

    def cancel(
        self,
        intent: DispatchIntentSnapshot,
        *,
        request_id: str,
        attempt_id: str,
        reason: str,
    ) -> dict[str, Any]:
        ...

    def timeout(
        self,
        intent: DispatchIntentSnapshot,
        *,
        timeout_id: str,
        attempt_id: str,
        wall_time_seconds: int,
        started_at: str,
        deadline_at: str,
    ) -> dict[str, Any]:
        ...

    def collect(self, intent: DispatchIntentSnapshot) -> list[dict[str, Any]]:
        ...

    def verified_node_outputs(self, intent: DispatchIntentSnapshot):
        ...

    def read_artifact(
        self, intent: DispatchIntentSnapshot, artifact_id: str
    ) -> tuple[list[dict[str, Any]], dict[str, Any], bytes]:
        ...

    def purge(
        self, intent: DispatchIntentSnapshot, *, purge_id: str
    ) -> dict[str, Any]:
        ...

    def purge_retry_exhausted(
        self,
        intent: DispatchIntentSnapshot,
        *,
        purge_id: str,
        exhaustion: RetryExhaustionCleanupProof,
    ) -> dict[str, Any]:
        ...


class DeepwaveTaskDispatcher:
    """Fixed code mapping for ``fwi.deepwave_adapter``; never dynamic import."""

    def __init__(self, adapter: DeepwaveAdapter):
        self._adapter = adapter

    @staticmethod
    def _request_from_snapshot(snapshot: TaskSnapshot) -> dict[str, Any]:
        if snapshot.plan is None or len(snapshot.plan.get("nodes", [])) != 1:
            raise DispatchError("PLAN_CAPABILITY_UNSUPPORTED_IN_P1")
        node = snapshot.plan["nodes"][0]
        if len(node.get("inputs", [])) != 1:
            raise DispatchError("PLAN_CAPABILITY_UNSUPPORTED_IN_P1")
        input_identity = node["inputs"][0].get("dataset")
        if not isinstance(input_identity, Mapping):
            raise DispatchError("PLAN_CAPABILITY_UNSUPPORTED_IN_P1")
        dataset = next(
            (
                value
                for value in snapshot.draft.get("datasets", [])
                if all(
                    value.get(key) == input_identity.get(key)
                    for key in ("id", "version", "content_hash", "data_type")
                )
            ),
            None,
        )
        if dataset is None:
            raise DispatchError("PLAN_CAPABILITY_UNSUPPORTED_IN_P1")
        return {
            "task_id": snapshot.task_id,
            "node_id": node["node_id"],
            "plan_hash": snapshot.plan["plan_hash"],
            "idempotency_key": node["idempotency_key"],
            "project_id": snapshot.project_id,
            "principal_id": snapshot.principal_id,
            "algorithm": copy.deepcopy(node["algorithm"]),
            "dataset": copy.deepcopy(dataset),
            "task_type": snapshot.plan["task_type"],
            "parameters": copy.deepcopy(node["parameters"]),
            "resources": copy.deepcopy(node["resources"]),
        }

    def prepare(self, snapshot: TaskSnapshot) -> DispatchPreparation:
        request = self._request_from_snapshot(snapshot)
        return self._prepare_request(request)

    def _prepare_request(self, request: Mapping[str, Any]) -> DispatchPreparation:
        request = copy.deepcopy(dict(request))
        try:
            validated = self._adapter.validate(
                **{
                    key: value
                    for key, value in request.items()
                    if key
                    in {
                        "project_id",
                        "principal_id",
                        "algorithm",
                        "dataset",
                        "task_type",
                        "parameters",
                        "resources",
                    }
                }
            )
        except AdapterError as error:
            raise DispatchError(error.code) from error
        except Exception as error:
            raise DispatchError("DISPATCH_PREPARATION_UNAVAILABLE") from error
        if validated.fingerprint is None:
            raise DispatchError("DISPATCH_PREPARATION_UNAVAILABLE")
        request["normalized_config_hash"] = validated.normalized_config_hash
        return DispatchPreparation(
            adapter_id=LOGICAL_ENTRYPOINT,
            adapter_version=ADAPTER_VERSION,
            request=request,
            queue_fingerprint=copy.deepcopy(validated.fingerprint),
        )

    def prepare_node(
        self,
        snapshot: TaskSnapshot,
        *,
        node_id: str,
        input_binding: DagNodeInputBindingFact,
    ) -> DispatchPreparation:
        """Prepare one exact dataset-root DAG node without enabling public DAG."""

        plan = snapshot.plan
        approval = snapshot.approval
        plan_nodes = plan.get("nodes") if isinstance(plan, Mapping) else None
        if (
            not isinstance(plan, Mapping)
            or not isinstance(plan_nodes, list)
            or len(plan_nodes) <= 1
            or not isinstance(approval, Mapping)
            or input_binding.task_id != snapshot.task_id
            or input_binding.plan_id != plan.get("plan_id")
            or input_binding.plan_hash != plan.get("plan_hash")
            or input_binding.approval_id != approval.get("approval_id")
            or input_binding.target_node_id != node_id
            or input_binding.project_id != snapshot.project_id
            or input_binding.principal_id != snapshot.principal_id
        ):
            raise DispatchError("DAG_NODE_PREPARATION_INVALID")
        matching = [
            value
            for value in plan_nodes
            if isinstance(value, Mapping) and value.get("node_id") == node_id
        ]
        binding_inputs = input_binding.binding_document.get("inputs")
        if (
            len(matching) != 1
            or not isinstance(binding_inputs, list)
            or len(binding_inputs) != 1
        ):
            raise DispatchError("DAG_NODE_CAPABILITY_UNSUPPORTED")
        node = matching[0]
        bound = binding_inputs[0]
        planned_inputs = node.get("inputs")
        if (
            not isinstance(bound, Mapping)
            or bound.get("kind") != "dataset"
            or not isinstance(planned_inputs, list)
            or len(planned_inputs) != 1
            or not isinstance(planned_inputs[0], Mapping)
            or bound.get("target_input_port") != planned_inputs[0].get("port")
            or bound.get("dataset") != planned_inputs[0].get("dataset")
        ):
            raise DispatchError("DAG_NODE_CAPABILITY_UNSUPPORTED")
        identity = bound.get("dataset")
        if not isinstance(identity, Mapping):
            raise DispatchError("DAG_NODE_CAPABILITY_UNSUPPORTED")
        dataset = next(
            (
                value
                for value in snapshot.draft.get("datasets", [])
                if isinstance(value, Mapping)
                and all(
                    value.get(key) == identity.get(key)
                    for key in ("id", "version", "content_hash", "data_type")
                )
            ),
            None,
        )
        if dataset is None:
            raise DispatchError("DAG_NODE_CAPABILITY_UNSUPPORTED")
        request = {
            "task_id": snapshot.task_id,
            "node_id": node["node_id"],
            "plan_hash": plan["plan_hash"],
            "idempotency_key": node["idempotency_key"],
            "project_id": snapshot.project_id,
            "principal_id": snapshot.principal_id,
            "algorithm": copy.deepcopy(node["algorithm"]),
            "dataset": copy.deepcopy(dict(dataset)),
            "task_type": plan["task_type"],
            "parameters": copy.deepcopy(node["parameters"]),
            "resources": copy.deepcopy(node["resources"]),
        }
        return self._prepare_request(request)

    def dispatch(self, intent: DispatchIntentSnapshot) -> dict[str, Any]:
        """Backward-compatible one-shot entry; production uses the scheduler."""

        return self.ensure_first_dispatch(intent)

    @staticmethod
    def supports_supervised_dispatch(intent: DispatchIntentSnapshot) -> bool:
        """Keep active managed intents operable without launching old pending work."""

        return (
            isinstance(intent, DispatchIntentSnapshot)
            and intent.adapter_id == LOGICAL_ENTRYPOINT
            and (
                intent.adapter_version == ADAPTER_VERSION
                or (
                    intent.adapter_version in SUPPORTED_MANAGED_REQUEST_VERSIONS
                    and intent.state != "pending"
                )
            )
        )

    def ensure_first_dispatch(
        self, intent: DispatchIntentSnapshot
    ) -> dict[str, Any]:
        """Enter the Adapter's lock-protected first-launch state machine."""

        if (
            intent.adapter_id != LOGICAL_ENTRYPOINT
            or intent.adapter_version != ADAPTER_VERSION
        ):
            raise DispatchError("DISPATCH_INTENT_INVALID")
        request = copy.deepcopy(intent.request)
        normalized_config_hash = request.pop("normalized_config_hash", None)
        expected = {
            "task_id",
            "node_id",
            "plan_hash",
            "idempotency_key",
            "project_id",
            "principal_id",
            "algorithm",
            "dataset",
            "task_type",
            "parameters",
            "resources",
        }
        if set(request) != expected or not isinstance(normalized_config_hash, str):
            raise DispatchError("DISPATCH_INTENT_INVALID")
        try:
            handle = self._adapter.submit(**request)
        except AdapterError as error:
            if error.code in DEFERRED_DISPATCH_CODES:
                raise DispatchDeferred(error.code) from error
            raise DispatchError(error.code) from error
        except Exception as error:
            raise DispatchError("DISPATCH_UNAVAILABLE") from error
        if handle.fingerprint.get("normalized_config_hash") != normalized_config_hash:
            raise DispatchError("DISPATCH_FINGERPRINT_DRIFT")
        return handle.as_dict()

    def _recover_existing_receipt(
        self,
        intent: DispatchIntentSnapshot,
        *,
        private_proof: bool,
    ) -> dict[str, Any]:
        """Read an exact private launched receipt without first dispatch."""

        if (
            intent.adapter_id != LOGICAL_ENTRYPOINT
            or intent.adapter_version not in SUPPORTED_MANAGED_REQUEST_VERSIONS
            or intent.state != "dispatching"
            or intent.handle is not None
            or not isinstance(intent.request, Mapping)
            or not isinstance(intent.queue_fingerprint, Mapping)
        ):
            raise DispatchError("DISPATCH_INTENT_INVALID")
        request = copy.deepcopy(dict(intent.request))
        normalized_config_hash = request.pop("normalized_config_hash", None)
        expected = {
            "task_id",
            "node_id",
            "plan_hash",
            "idempotency_key",
            "project_id",
            "principal_id",
            "algorithm",
            "dataset",
            "task_type",
            "parameters",
            "resources",
        }
        if (
            set(request) != expected
            or not isinstance(normalized_config_hash, str)
            or request["task_id"] != intent.task_id
            or request["node_id"] != intent.node_id
            or request["plan_hash"] != intent.plan_hash
            or request["idempotency_key"] != intent.node_idempotency_key
            or intent.queue_fingerprint.get("normalized_config_hash")
            != normalized_config_hash
            or request.get("algorithm")
            != {"id": ALGORITHM_ID, "version": intent.adapter_version}
            or not is_supported_receipt_binding(
                request["algorithm"],
                intent.adapter_version,
                intent.queue_fingerprint,
            )
        ):
            raise DispatchError("DISPATCH_INTENT_INVALID")
        try:
            if private_proof:
                proof = self._adapter.lookup_existing_private_receipt(**request)
                handle = proof.handle
            else:
                proof = None
                handle = self._adapter.lookup_existing_handle(**request)
        except AdapterError as error:
            raise DispatchError(error.code) from error
        except Exception as error:
            raise DispatchError("DISPATCH_RECOVERY_UNAVAILABLE") from error
        if (
            handle.adapter_version != intent.adapter_version
            or handle.task_id != intent.task_id
            or handle.node_id != intent.node_id
            or handle.plan_hash != intent.plan_hash
            or handle.idempotency_key != intent.node_idempotency_key
            or handle.fingerprint.get("normalized_config_hash")
            != normalized_config_hash
        ):
            raise DispatchError("DISPATCH_FINGERPRINT_DRIFT")
        return proof.as_dict() if proof is not None else handle.as_dict()

    def recover_existing_receipt(
        self, intent: DispatchIntentSnapshot
    ) -> dict[str, Any]:
        """Read an exact private launched receipt without first dispatch."""

        return self._recover_existing_receipt(intent, private_proof=False)

    def recover_existing_private_receipt(
        self, intent: DispatchIntentSnapshot
    ) -> dict[str, Any]:
        """Prove a current receipt that predates managed Worker evidence."""

        return self._recover_existing_receipt(intent, private_proof=True)

    @staticmethod
    def _validated_reconciliation_receipt(
        *,
        proof: Any,
        intent: DispatchIntentSnapshot,
        request: Mapping[str, Any],
        normalized_config_hash: str,
    ) -> DispatchReceiptProbe:
        """Validate positive Adapter evidence independently of its reader."""

        if not isinstance(proof, AdapterExistingDispatchReceiptProof):
            raise DispatchDeferred("DISPATCH_RECEIPT_PROBE_INVALID")
        handle = proof.handle
        if (
            not isinstance(handle, AdapterHandle)
            or not isinstance(handle.fingerprint, Mapping)
            or handle.adapter_version != intent.adapter_version
            or handle.task_id != intent.task_id
            or handle.node_id != intent.node_id
            or handle.plan_hash != intent.plan_hash
            or handle.idempotency_key != intent.node_idempotency_key
            or handle.fingerprint.get("normalized_config_hash")
            != normalized_config_hash
            or handle.algorithm != request["algorithm"]
            or not is_supported_receipt_binding(
                handle.algorithm,
                handle.adapter_version,
                handle.fingerprint,
            )
        ):
            raise DispatchDeferred("DISPATCH_FINGERPRINT_DRIFT")

        evidence = proof.worker_evidence
        if proof.evidence_kind == "managed_worker_receipt":
            ticket = evidence.get("ticket") if isinstance(evidence, Mapping) else None
            ready = evidence.get("ready") if isinstance(evidence, Mapping) else None
            heartbeat = (
                evidence.get("heartbeat") if isinstance(evidence, Mapping) else None
            )
            heartbeat_state = (
                heartbeat.get("state")
                if isinstance(heartbeat, Mapping)
                else None
            )
            heartbeat_positive = heartbeat_state in {
                "running",
                "succeeded",
                "failed",
            } or (
                intent.adapter_version == "1.6.0"
                and heartbeat_state == "waiting"
            )
            if (
                proof.private_schema_version not in {"1.1.0", "1.2.0"}
                or proof.receipt_record_hash is not None
                or not isinstance(ticket, Mapping)
                or ticket.get("state") != "spawned"
                or not isinstance(ready, Mapping)
                or not isinstance(heartbeat, Mapping)
                or not heartbeat_positive
                or evidence.get("submission_id") != handle.submission_id
                or evidence.get("job_id") != handle.job_id
                or evidence.get("request_hash") != handle.request_hash
            ):
                raise DispatchDeferred("DISPATCH_RECEIPT_PROBE_INVALID")
        elif proof.evidence_kind == "private_receipt":
            if (
                proof.private_schema_version != "1.0.0"
                or evidence is not None
                or not isinstance(proof.receipt_record_hash, str)
                or _SHA256.fullmatch(proof.receipt_record_hash) is None
            ):
                raise DispatchDeferred("DISPATCH_RECEIPT_PROBE_INVALID")
        else:
            raise DispatchDeferred("DISPATCH_RECEIPT_PROBE_INVALID")
        return DispatchReceiptProbe(
            evidence_kind=proof.evidence_kind,
            handle=handle.as_dict(),
            evidence=(
                None if evidence is None else copy.deepcopy(dict(evidence))
            ),
            private_schema_version=proof.private_schema_version,
            receipt_record_hash=proof.receipt_record_hash,
        )

    def probe_existing_dispatch_receipt(
        self, intent: DispatchIntentSnapshot
    ) -> DispatchReceiptProbe:
        """Recognize one exact positive receipt without launching a Worker."""

        if (
            intent.adapter_id != LOGICAL_ENTRYPOINT
            or intent.adapter_version not in SUPPORTED_MANAGED_REQUEST_VERSIONS
            or intent.state != "reconciliation_required"
            or intent.handle is not None
            or not isinstance(intent.request, Mapping)
            or not isinstance(intent.queue_fingerprint, Mapping)
        ):
            raise DispatchDeferred("DISPATCH_RECEIPT_PROBE_UNSUPPORTED")
        request = copy.deepcopy(dict(intent.request))
        normalized_config_hash = request.pop("normalized_config_hash", None)
        expected = {
            "task_id",
            "node_id",
            "plan_hash",
            "idempotency_key",
            "project_id",
            "principal_id",
            "algorithm",
            "dataset",
            "task_type",
            "parameters",
            "resources",
        }
        if (
            set(request) != expected
            or not isinstance(normalized_config_hash, str)
            or request["task_id"] != intent.task_id
            or request["node_id"] != intent.node_id
            or request["plan_hash"] != intent.plan_hash
            or request["idempotency_key"] != intent.node_idempotency_key
            or intent.queue_fingerprint.get("normalized_config_hash")
            != normalized_config_hash
            or request.get("algorithm")
            != {"id": ALGORITHM_ID, "version": intent.adapter_version}
            or not is_supported_receipt_binding(
                request["algorithm"],
                intent.adapter_version,
                intent.queue_fingerprint,
            )
        ):
            raise DispatchDeferred("DISPATCH_RECEIPT_PROBE_UNSUPPORTED")
        try:
            proof = self._adapter.probe_existing_dispatch_receipt(**request)
        except AdapterError as error:
            raise DispatchDeferred(error.code) from error
        except Exception as error:
            raise DispatchDeferred(
                "DISPATCH_RECEIPT_PROBE_UNAVAILABLE"
            ) from error
        return self._validated_reconciliation_receipt(
            proof=proof,
            intent=intent,
            request=request,
            normalized_config_hash=normalized_config_hash,
        )

    def probe_dispatch_reconciliation(
        self, intent: DispatchIntentSnapshot
    ) -> (
        DispatchReceiptProbe
        | DispatchNotStartedProof
        | DispatchReconciliationDeferred
    ):
        """Classify one exact ambiguity without launch or retry capability."""

        if (
            not isinstance(intent, DispatchIntentSnapshot)
            or intent.adapter_id != LOGICAL_ENTRYPOINT
            or intent.adapter_version not in {"1.4.0", "1.5.0", "1.6.0"}
            or intent.state != "reconciliation_required"
            or intent.handle is not None
            or not isinstance(intent.request, Mapping)
            or not isinstance(intent.queue_fingerprint, Mapping)
        ):
            return DispatchReconciliationDeferred(
                classification="uncertain",
                failure_code="DISPATCH_RECONCILIATION_UNSUPPORTED",
            )
        request = copy.deepcopy(dict(intent.request))
        normalized_config_hash = request.pop("normalized_config_hash", None)
        expected = {
            "task_id",
            "node_id",
            "plan_hash",
            "idempotency_key",
            "project_id",
            "principal_id",
            "algorithm",
            "dataset",
            "task_type",
            "parameters",
            "resources",
        }
        algorithm = request.get("algorithm")
        if (
            set(request) != expected
            or not isinstance(normalized_config_hash, str)
            or request["task_id"] != intent.task_id
            or request["node_id"] != intent.node_id
            or request["plan_hash"] != intent.plan_hash
            or request["idempotency_key"] != intent.node_idempotency_key
            or intent.queue_fingerprint.get("normalized_config_hash")
            != normalized_config_hash
            or algorithm
            != {
                "id": "deepwave.acoustic_fwi",
                "version": intent.adapter_version,
            }
        ):
            return DispatchReconciliationDeferred(
                classification="uncertain",
                failure_code="DISPATCH_RECONCILIATION_UNSUPPORTED",
            )
        try:
            adapter_result = self._adapter.probe_dispatch_reconciliation(
                **request,
                normalized_config_hash=normalized_config_hash,
            )
        except AdapterError as error:
            return DispatchReconciliationDeferred(
                classification=(
                    "transient"
                    if error.code
                    in {"ADAPTER_SUBMISSION_BUSY", "WORKER_ATTEMPT_BUSY"}
                    else "uncertain"
                ),
                failure_code=error.code,
            )
        except Exception:
            return DispatchReconciliationDeferred(
                classification="uncertain",
                failure_code="DISPATCH_RECONCILIATION_UNAVAILABLE",
            )

        if isinstance(adapter_result, AdapterReconciliationDeferred):
            valid_code = (
                isinstance(adapter_result.failure_code, str)
                and re.fullmatch(
                    r"[A-Z][A-Z0-9_]{0,127}",
                    adapter_result.failure_code,
                )
                is not None
            )
            transient_code = valid_code and adapter_result.failure_code in {
                "ADAPTER_SUBMISSION_BUSY",
                "WORKER_ATTEMPT_BUSY",
            }
            if (
                adapter_result.classification not in {"transient", "uncertain"}
                or not valid_code
                or (adapter_result.classification == "transient")
                != transient_code
            ):
                return DispatchReconciliationDeferred(
                    classification="uncertain",
                    failure_code="DISPATCH_RECONCILIATION_PROBE_INVALID",
                )
            return DispatchReconciliationDeferred(
                classification=adapter_result.classification,
                failure_code=adapter_result.failure_code,
            )

        if isinstance(adapter_result, AdapterExistingDispatchReceiptProof):
            try:
                return self._validated_reconciliation_receipt(
                    proof=adapter_result,
                    intent=intent,
                    request=request,
                    normalized_config_hash=normalized_config_hash,
                )
            except DispatchDeferred as error:
                return DispatchReconciliationDeferred(
                    classification="uncertain",
                    failure_code=error.code,
                )
            except Exception:
                return DispatchReconciliationDeferred(
                    classification="uncertain",
                    failure_code="DISPATCH_RECONCILIATION_PROBE_INVALID",
                )

        if not isinstance(adapter_result, AdapterDispatchNotStartedProof):
            return DispatchReconciliationDeferred(
                classification="uncertain",
                failure_code="DISPATCH_RECONCILIATION_PROBE_INVALID",
            )
        evidence = adapter_result.evidence
        ticket = evidence.get("ticket") if isinstance(evidence, Mapping) else None
        required_evidence = {
            "schema_version",
            "submission_id",
            "attempt_id",
            "attempt_number",
            "job_id",
            "request_hash",
            "binding_hash",
            "created_at",
            "ticket",
            "ready",
            "heartbeat",
        }
        ticket_fields = {
            "state",
            "capacity_slot",
            "capacity_generation",
            "worker_pid",
            "updated_at",
            "record_hash",
        }
        if (
            adapter_result.result != "not_dispatched"
            or adapter_result.evidence_kind
            != "managed_pre_running_failure"
            or adapter_result.adapter_version != intent.adapter_version
            or (
                adapter_result.adapter_version,
                adapter_result.private_schema_version,
            )
            not in {
                ("1.4.0", "1.1.0"),
                ("1.5.0", "1.2.0"),
                ("1.6.0", "1.2.0"),
            }
            or not isinstance(adapter_result.private_record_hash, str)
            or _SHA256.fullmatch(adapter_result.private_record_hash) is None
            or not isinstance(adapter_result.private_proof_hash, str)
            or _SHA256.fullmatch(adapter_result.private_proof_hash) is None
            or not isinstance(adapter_result.attempt_id, str)
            or _MANAGED_ATTEMPT_ID.fullmatch(adapter_result.attempt_id) is None
            or adapter_result.attempt_number != 1
            or not isinstance(evidence, Mapping)
            or set(evidence) != required_evidence
            or evidence.get("schema_version") != "1.0.0"
            or evidence.get("attempt_id") != adapter_result.attempt_id
            or evidence.get("attempt_number") != 1
            or not isinstance(evidence.get("submission_id"), str)
            or _MANAGED_SUBMISSION_ID.fullmatch(evidence["submission_id"])
            is None
            or not isinstance(evidence.get("job_id"), str)
            or _MANAGED_JOB_ID.fullmatch(evidence["job_id"]) is None
            or evidence.get("ready") is not None
            or evidence.get("heartbeat") is not None
            or not isinstance(evidence.get("created_at"), str)
            or not evidence["created_at"].endswith("Z")
            or not isinstance(ticket, Mapping)
            or set(ticket) != ticket_fields
            or ticket.get("state")
            not in {"staged", "leased", "spawned", "failed"}
            or not isinstance(evidence.get("request_hash"), str)
            or _SHA256.fullmatch(evidence["request_hash"]) is None
            or not isinstance(evidence.get("binding_hash"), str)
            or _SHA256.fullmatch(evidence["binding_hash"]) is None
            or not isinstance(ticket.get("record_hash"), str)
            or _SHA256.fullmatch(ticket["record_hash"]) is None
            or not isinstance(ticket.get("updated_at"), str)
            or not ticket["updated_at"].endswith("Z")
        ):
            return DispatchReconciliationDeferred(
                classification="uncertain",
                failure_code="DISPATCH_RECONCILIATION_PROBE_INVALID",
            )

        dataset = request.get("dataset")
        access_scope = (
            dataset.get("access_scope") if isinstance(dataset, Mapping) else None
        )
        try:
            if not isinstance(dataset, Mapping) or not isinstance(
                access_scope, Mapping
            ):
                raise ValueError("durable dataset binding is invalid")
            submission_hash = encode_document(
                {
                    "task_id": intent.task_id,
                    "plan_hash": intent.plan_hash,
                    "idempotency_key": intent.node_idempotency_key,
                }
            )[1]
            expected_submission_id = (
                "submission-" + submission_hash.removeprefix("sha256:")
            )
            expected_request_hash = encode_document(
                {
                    "submission_id": expected_submission_id,
                    "task_id": intent.task_id,
                    "node_id": intent.node_id,
                    "plan_hash": intent.plan_hash,
                    "idempotency_key": intent.node_idempotency_key,
                    "project_id": request["project_id"],
                    "principal_id": request["principal_id"],
                    "algorithm": copy.deepcopy(dict(request["algorithm"])),
                    "dataset": {
                        key: copy.deepcopy(dataset.get(key))
                        for key in ("id", "version", "content_hash", "data_type")
                    },
                    "dataset_access_scope": copy.deepcopy(dict(access_scope)),
                    "task_type": request["task_type"],
                    "parameters": copy.deepcopy(dict(request["parameters"])),
                    "resources": copy.deepcopy(dict(request["resources"])),
                    "normalized_config_hash": normalized_config_hash,
                }
            )[1]
            created_at = datetime.fromisoformat(
                evidence["created_at"].replace("Z", "+00:00")
            )
            if created_at.tzinfo is None:
                raise ValueError("attempt timestamp has no timezone")
            created_stamp = created_at.astimezone(timezone.utc).strftime(
                "%Y%m%dT%H%M%SZ"
            )
            job_suffix = hashlib.sha256(
                expected_submission_id.encode("utf-8")
            ).hexdigest()[:12]
            expected_job_id = f"fwi-{created_stamp}-{job_suffix}"
        except (KeyError, TypeError, ValueError):
            return DispatchReconciliationDeferred(
                classification="uncertain",
                failure_code="DISPATCH_RECONCILIATION_PROBE_INVALID",
            )
        if (
            evidence["submission_id"] != expected_submission_id
            or evidence["request_hash"] != expected_request_hash
            or evidence["job_id"] != expected_job_id
        ):
            return DispatchReconciliationDeferred(
                classification="uncertain",
                failure_code="DISPATCH_RECONCILIATION_PROBE_INVALID",
            )

        state = ticket["state"]
        slot = ticket["capacity_slot"]
        generation = ticket["capacity_generation"]
        worker_pid = ticket["worker_pid"]
        if state == "staged":
            valid_ticket = slot is None and generation is None and worker_pid is None
        elif state == "leased":
            valid_ticket = (
                type(slot) is int
                and slot >= 0
                and type(generation) is int
                and generation >= 1
                and worker_pid is None
            )
        elif state == "spawned":
            valid_ticket = (
                type(slot) is int
                and slot >= 0
                and type(generation) is int
                and generation >= 1
                and type(worker_pid) is int
                and worker_pid >= 1
            )
        else:
            valid_ticket = worker_pid is None and (
                (slot is None and generation is None)
                or (
                    type(slot) is int
                    and slot >= 0
                    and type(generation) is int
                    and generation >= 1
                )
            )
        if not valid_ticket:
            return DispatchReconciliationDeferred(
                classification="uncertain",
                failure_code="DISPATCH_RECONCILIATION_PROBE_INVALID",
            )

        try:
            binding_payload = {
                "schema_version": "1.0.0",
                "submission_id": evidence["submission_id"],
                "attempt_id": evidence["attempt_id"],
                "attempt_number": evidence["attempt_number"],
                "job_id": evidence["job_id"],
                "request_hash": evidence["request_hash"],
                "created_at": evidence["created_at"],
            }
            ticket_payload = {
                **binding_payload,
                "binding_hash": evidence["binding_hash"],
                "state": state,
                "capacity_slot": slot,
                "capacity_generation": generation,
                "worker_pid": worker_pid,
                "updated_at": ticket["updated_at"],
            }
            _, binding_hash = encode_document(binding_payload)
            _, ticket_hash = encode_document(ticket_payload)
            _, evidence_hash = encode_document(dict(evidence))
            _, private_proof_hash = encode_document(
                {
                    "schema_version": "1.0.0",
                    "result": "not_dispatched",
                    "evidence_kind": "managed_pre_running_failure",
                    "adapter_version": adapter_result.adapter_version,
                    "private_schema_version": (
                        adapter_result.private_schema_version
                    ),
                    "private_record_hash": adapter_result.private_record_hash,
                    "attempt_id": adapter_result.attempt_id,
                    "attempt_number": adapter_result.attempt_number,
                    "evidence_hash": evidence_hash,
                }
            )
        except Exception:
            return DispatchReconciliationDeferred(
                classification="uncertain",
                failure_code="DISPATCH_RECONCILIATION_PROBE_INVALID",
            )
        if (
            binding_hash != evidence["binding_hash"]
            or ticket_hash != ticket["record_hash"]
            or private_proof_hash != adapter_result.private_proof_hash
        ):
            return DispatchReconciliationDeferred(
                classification="uncertain",
                failure_code="DISPATCH_RECONCILIATION_PROBE_INVALID",
            )
        return DispatchNotStartedProof(
            result="not_dispatched",
            evidence_kind="managed_pre_running_failure",
            adapter_version=adapter_result.adapter_version,
            private_schema_version=adapter_result.private_schema_version,
            private_record_hash=adapter_result.private_record_hash,
            private_proof_hash=adapter_result.private_proof_hash,
            attempt_id=adapter_result.attempt_id,
            attempt_number=adapter_result.attempt_number,
            evidence=copy.deepcopy(dict(evidence)),
        )

    def observe_existing_worker_attempt(
        self, intent: DispatchIntentSnapshot
    ) -> dict[str, Any]:
        """Observe an exact managed attempt without dispatch capability."""

        if (
            intent.adapter_id == LOGICAL_ENTRYPOINT
            and intent.adapter_version in SUPPORTED_ADAPTER_VERSIONS
            and intent.adapter_version not in SUPPORTED_MANAGED_REQUEST_VERSIONS
        ):
            raise DispatchError("WORKER_EVIDENCE_UNAVAILABLE")
        if (
            intent.adapter_id != LOGICAL_ENTRYPOINT
            or intent.adapter_version not in SUPPORTED_MANAGED_REQUEST_VERSIONS
            or intent.state not in {"dispatching", "dispatched", "retrying"}
            or (intent.state == "dispatching" and intent.handle is not None)
            or (intent.state == "dispatched" and not isinstance(intent.handle, Mapping))
            or (intent.state == "retrying" and intent.handle is not None)
            or not isinstance(intent.request, Mapping)
            or not isinstance(intent.queue_fingerprint, Mapping)
        ):
            raise DispatchError("DISPATCH_INTENT_INVALID")
        request = copy.deepcopy(dict(intent.request))
        normalized_config_hash = request.pop("normalized_config_hash", None)
        expected = {
            "task_id",
            "node_id",
            "plan_hash",
            "idempotency_key",
            "project_id",
            "principal_id",
            "algorithm",
            "dataset",
            "task_type",
            "parameters",
            "resources",
        }
        if (
            set(request) != expected
            or not isinstance(normalized_config_hash, str)
            or request["task_id"] != intent.task_id
            or request["node_id"] != intent.node_id
            or request["plan_hash"] != intent.plan_hash
            or request["idempotency_key"] != intent.node_idempotency_key
            or intent.queue_fingerprint.get("normalized_config_hash")
            != normalized_config_hash
            or request.get("algorithm")
            != {"id": ALGORITHM_ID, "version": intent.adapter_version}
            or not is_supported_receipt_binding(
                request["algorithm"],
                intent.adapter_version,
                intent.queue_fingerprint,
            )
        ):
            raise DispatchError("DISPATCH_INTENT_INVALID")
        try:
            observed = self._adapter.observe_existing_worker_attempt(**request)
        except AdapterError as error:
            raise DispatchError(error.code) from error
        except Exception as error:
            raise DispatchError("WORKER_EVIDENCE_UNAVAILABLE") from error
        if not isinstance(observed, Mapping) or set(observed) != {
            "evidence",
            "handle",
        }:
            raise DispatchError("WORKER_EVIDENCE_INVALID")
        evidence = observed.get("evidence")
        handle = observed.get("handle")
        if not isinstance(evidence, Mapping):
            raise DispatchError("WORKER_EVIDENCE_INVALID")
        handle_fingerprint = (
            handle.get("fingerprint") if isinstance(handle, Mapping) else None
        )
        if handle is not None and (
            not isinstance(handle, Mapping)
            or not isinstance(handle_fingerprint, Mapping)
            or handle.get("adapter_version") != intent.adapter_version
            or handle.get("task_id") != intent.task_id
            or handle.get("node_id") != intent.node_id
            or handle.get("plan_hash") != intent.plan_hash
            or handle.get("idempotency_key") != intent.node_idempotency_key
            or handle_fingerprint.get("normalized_config_hash")
            != normalized_config_hash
        ):
            raise DispatchError("DISPATCH_FINGERPRINT_DRIFT")
        if intent.state == "dispatched" and handle != intent.handle:
            raise DispatchError("WORKER_EVIDENCE_INVALID")
        if intent.state == "retrying" and (
            evidence.get("attempt_number") != 2
            or (handle is not None and handle.get("job_id") != evidence.get("job_id"))
        ):
            raise DispatchError("WORKER_EVIDENCE_INVALID")
        heartbeat = evidence.get("heartbeat")
        if (
            isinstance(heartbeat, Mapping)
            and heartbeat.get("state") == "waiting"
            and intent.adapter_version != "1.6.0"
        ):
            raise DispatchError("WORKER_EVIDENCE_INVALID")
        return {
            "evidence": copy.deepcopy(dict(evidence)),
            "handle": None if handle is None else copy.deepcopy(dict(handle)),
        }

    @staticmethod
    def _pre_dispatch_request(intent: DispatchIntentSnapshot) -> tuple[dict[str, Any], str]:
        if (
            intent.adapter_id != LOGICAL_ENTRYPOINT
            or intent.adapter_version not in SUPPORTED_MANAGED_REQUEST_VERSIONS
            or intent.state != "dispatching"
            or intent.handle is not None
            or not isinstance(intent.request, Mapping)
            or not isinstance(intent.queue_fingerprint, Mapping)
        ):
            raise DispatchError("DISPATCH_INTENT_INVALID")
        request = copy.deepcopy(dict(intent.request))
        normalized_config_hash = request.pop("normalized_config_hash", None)
        if (
            set(request)
            != {
                "task_id",
                "node_id",
                "plan_hash",
                "idempotency_key",
                "project_id",
                "principal_id",
                "algorithm",
                "dataset",
                "task_type",
                "parameters",
                "resources",
            }
            or not isinstance(normalized_config_hash, str)
            or request["task_id"] != intent.task_id
            or request["node_id"] != intent.node_id
            or request["plan_hash"] != intent.plan_hash
            or request["idempotency_key"] != intent.node_idempotency_key
            or intent.queue_fingerprint.get("normalized_config_hash")
            != normalized_config_hash
            or request.get("algorithm")
            != {"id": ALGORITHM_ID, "version": intent.adapter_version}
            or not is_supported_receipt_binding(
                request["algorithm"],
                intent.adapter_version,
                intent.queue_fingerprint,
            )
        ):
            raise DispatchError("DISPATCH_INTENT_INVALID")
        return request, normalized_config_hash

    def probe_pre_running_retry(
        self, intent: DispatchIntentSnapshot
    ) -> DispatchRetryProof:
        request, _ = self._pre_dispatch_request(intent)
        try:
            proof = self._adapter.probe_pre_running_retry(**request)
        except AdapterError as error:
            raise DispatchError(error.code) from error
        except Exception as error:
            raise DispatchError("WORKER_RETRY_PROOF_UNAVAILABLE") from error
        return self._validated_pre_running_failure_proof(
            proof, expected_attempt_number=1
        )

    def probe_pre_running_retry_exhaustion(
        self, intent: DispatchIntentSnapshot
    ) -> DispatchRetryProof:
        """Read exact attempt-2 exhaustion proof without launching or mutating."""

        request, _ = self._retry_request(
            intent, allowed_states={"dispatching", "retrying"}
        )
        try:
            proof = self._adapter.probe_pre_running_retry_exhaustion(**request)
        except AdapterError as error:
            raise DispatchError(error.code) from error
        except Exception as error:
            raise DispatchError(
                "WORKER_RETRY_EXHAUSTION_PROOF_UNAVAILABLE"
            ) from error
        return self._validated_pre_running_failure_proof(
            proof,
            expected_attempt_number=2,
            expected_private_schema_version=(
                "1.3.0" if intent.state == "retrying" else "1.2.0"
            ),
        )

    @staticmethod
    def _validated_pre_running_failure_proof(
        proof: Any,
        *,
        expected_attempt_number: int,
        expected_private_schema_version: str = "1.2.0",
    ) -> DispatchRetryProof:
        """Validate one path-free proof independently of Adapter internals."""

        if not isinstance(proof, AdapterPreRunningRetryProof):
            raise DispatchError("WORKER_RETRY_PROOF_INVALID")
        evidence = proof.evidence
        ticket = evidence.get("ticket") if isinstance(evidence, Mapping) else None
        try:
            expected_private_proof_hash = encode_document(
                {
                    "schema_version": "1.0.0",
                    "failure_kind": "pre_running_launch_failure",
                    "submission_id": evidence["submission_id"],
                    "attempt_id": evidence["attempt_id"],
                    "attempt_number": evidence["attempt_number"],
                    "job_id": evidence["job_id"],
                    "request_hash": evidence["request_hash"],
                    "binding_hash": evidence["binding_hash"],
                    "ticket_record_hash": ticket["record_hash"],
                }
            )[1]
        except (KeyError, TypeError, ValueError):
            expected_private_proof_hash = None
        if (
            proof.failure_kind != "pre_running_launch_failure"
            or proof.previous_attempt_number != expected_attempt_number
            or not isinstance(proof.previous_attempt_id, str)
            or _MANAGED_ATTEMPT_ID.fullmatch(proof.previous_attempt_id) is None
            or proof.private_schema_version != expected_private_schema_version
            or not isinstance(proof.private_proof_hash, str)
            or _SHA256.fullmatch(proof.private_proof_hash) is None
            or proof.private_proof_hash != expected_private_proof_hash
            or not isinstance(ticket, Mapping)
            or ticket.get("state") != "failed"
            or ticket.get("worker_pid") is not None
            or evidence.get("attempt_id") != proof.previous_attempt_id
            or evidence.get("attempt_number") != expected_attempt_number
            or evidence.get("ready") is not None
            or evidence.get("heartbeat") is not None
        ):
            raise DispatchError("WORKER_RETRY_PROOF_INVALID")
        return DispatchRetryProof(
            failure_kind="pre_running_launch_failure",
            previous_attempt_id=proof.previous_attempt_id,
            previous_attempt_number=expected_attempt_number,
            private_schema_version=expected_private_schema_version,
            private_proof_hash=proof.private_proof_hash,
            evidence=copy.deepcopy(dict(evidence)),
        )

    @staticmethod
    def _retry_request(
        intent: DispatchIntentSnapshot, *, allowed_states: set[str]
    ) -> tuple[dict[str, Any], str]:
        if (
            intent.adapter_id != LOGICAL_ENTRYPOINT
            or intent.adapter_version not in SUPPORTED_MANAGED_REQUEST_VERSIONS
            or intent.state not in allowed_states
            or (intent.state == "retrying" and intent.handle is not None)
            or not isinstance(intent.request, Mapping)
            or not isinstance(intent.queue_fingerprint, Mapping)
        ):
            raise DispatchError("DISPATCH_INTENT_INVALID")
        request = copy.deepcopy(dict(intent.request))
        normalized_config_hash = request.pop("normalized_config_hash", None)
        if (
            set(request)
            != {
                "task_id",
                "node_id",
                "plan_hash",
                "idempotency_key",
                "project_id",
                "principal_id",
                "algorithm",
                "dataset",
                "task_type",
                "parameters",
                "resources",
            }
            or not isinstance(normalized_config_hash, str)
            or request["task_id"] != intent.task_id
            or request["node_id"] != intent.node_id
            or request["plan_hash"] != intent.plan_hash
            or request["idempotency_key"] != intent.node_idempotency_key
            or intent.queue_fingerprint.get("normalized_config_hash")
            != normalized_config_hash
            or request.get("algorithm")
            != {"id": ALGORITHM_ID, "version": intent.adapter_version}
            or not is_supported_receipt_binding(
                request["algorithm"],
                intent.adapter_version,
                intent.queue_fingerprint,
            )
        ):
            raise DispatchError("DISPATCH_INTENT_INVALID")
        return request, normalized_config_hash

    def probe_worker_exit_retry(
        self, intent: DispatchIntentSnapshot
    ) -> DispatchRetryProof:
        return self._probe_worker_exit(intent, expected_attempt_number=1)

    def probe_worker_exit_retry_exhaustion(
        self, intent: DispatchIntentSnapshot
    ) -> DispatchRetryProof:
        return self._probe_worker_exit(intent, expected_attempt_number=2)

    def _probe_worker_exit(
        self, intent: DispatchIntentSnapshot, *, expected_attempt_number: int
    ) -> DispatchRetryProof:
        request, _ = self._retry_request(intent, allowed_states={"dispatched"})
        try:
            proof = (
                self._adapter.probe_worker_exit_retry(**request)
                if expected_attempt_number == 1
                else self._adapter.probe_worker_exit_retry_exhaustion(**request)
            )
        except AdapterError as error:
            raise DispatchError(error.code) from error
        except Exception as error:
            raise DispatchError("WORKER_EXIT_PROOF_UNAVAILABLE") from error
        if not isinstance(proof, AdapterWorkerExitRetryProof):
            raise DispatchError("WORKER_EXIT_PROOF_INVALID")
        evidence = proof.evidence
        private = proof.exit_evidence
        ticket = evidence.get("ticket") if isinstance(evidence, Mapping) else None
        ready = evidence.get("ready") if isinstance(evidence, Mapping) else None
        heartbeat = (
            evidence.get("heartbeat") if isinstance(evidence, Mapping) else None
        )
        if not isinstance(private, Mapping):
            raise DispatchError("WORKER_EXIT_PROOF_INVALID")
        private_payload = dict(private)
        private_record_hash = private_payload.pop("record_hash", None)
        try:
            calculated_private_hash = encode_document(private_payload)[1]
        except (TypeError, ValueError):
            calculated_private_hash = None
        expected_schema = (
            {"1.1.0", "1.2.0"}
            if expected_attempt_number == 1
            else {"1.2.0", "1.3.0"}
        )
        if (
            proof.failure_kind != "worker_exit"
            or proof.previous_attempt_number != expected_attempt_number
            or not isinstance(proof.previous_attempt_id, str)
            or _MANAGED_ATTEMPT_ID.fullmatch(proof.previous_attempt_id) is None
            or proof.private_schema_version not in expected_schema
            or proof.private_proof_hash != private_record_hash
            or not isinstance(private_record_hash, str)
            or _SHA256.fullmatch(private_record_hash) is None
            or calculated_private_hash != private_record_hash
            or not isinstance(ticket, Mapping)
            or ticket.get("state") != "spawned"
            or not isinstance(ready, Mapping)
            or not isinstance(heartbeat, Mapping)
            or heartbeat.get("state") != "running"
            or evidence.get("attempt_id") != proof.previous_attempt_id
            or evidence.get("attempt_number") != expected_attempt_number
            or private.get("submission_id") != evidence.get("submission_id")
            or private.get("attempt_id") != evidence.get("attempt_id")
            or private.get("attempt_number") != evidence.get("attempt_number")
            or private.get("job_id") != evidence.get("job_id")
            or private.get("request_hash") != evidence.get("request_hash")
            or private.get("binding_hash") != evidence.get("binding_hash")
            or private.get("ticket_record_hash") != ticket.get("record_hash")
            or private.get("ready_record_hash") != ready.get("record_hash")
            or private.get("heartbeat_record_hash")
            != heartbeat.get("record_hash")
            or private.get("heartbeat_sequence")
            != heartbeat.get("sequence")
            or private.get("heartbeat_state") != "running"
            or private.get("created_at") != evidence.get("created_at")
            or type(private.get("return_code")) is not int
            or private.get("return_code") in {0, 75, 76}
            or not isinstance(private.get("pre_status_hash"), str)
            or _SHA256.fullmatch(private["pre_status_hash"]) is None
            or not isinstance(private.get("post_status_hash"), str)
            or _SHA256.fullmatch(private["post_status_hash"]) is None
            or not isinstance(private.get("observed_at"), str)
            or not private["observed_at"].endswith("Z")
        ):
            raise DispatchError("WORKER_EXIT_PROOF_INVALID")
        return DispatchRetryProof(
            failure_kind="worker_exit",
            previous_attempt_id=proof.previous_attempt_id,
            previous_attempt_number=expected_attempt_number,
            private_schema_version=proof.private_schema_version,
            private_proof_hash=proof.private_proof_hash,
            evidence=copy.deepcopy(dict(evidence)),
            private_evidence=copy.deepcopy(dict(private)),
        )

    def retry_worker_exit(
        self, intent: DispatchIntentSnapshot, *, authorization: Mapping[str, Any]
    ) -> dict[str, Any]:
        request, normalized_config_hash = self._retry_request(
            intent, allowed_states={"retrying"}
        )
        token = self._validated_retry_authorization(
            intent, authorization=authorization, failure_kind="worker_exit"
        )
        try:
            handle = self._adapter.retry_worker_exit(
                **request, authorization=token
            )
        except AdapterError as error:
            if error.code in DEFERRED_DISPATCH_CODES:
                raise DispatchDeferred(error.code) from error
            raise DispatchError(error.code) from error
        except Exception as error:
            raise DispatchError("WORKER_RETRY_UNAVAILABLE") from error
        if (
            not isinstance(handle, AdapterHandle)
            or handle.adapter_version != intent.adapter_version
            or handle.task_id != intent.task_id
            or handle.node_id != intent.node_id
            or handle.plan_hash != intent.plan_hash
            or handle.idempotency_key != intent.node_idempotency_key
            or handle.algorithm != request["algorithm"]
            or handle.fingerprint.get("normalized_config_hash")
            != normalized_config_hash
        ):
            raise DispatchError("DISPATCH_FINGERPRINT_DRIFT")
        return handle.as_dict()

    @staticmethod
    def _validated_retry_authorization(
        intent: DispatchIntentSnapshot,
        *,
        authorization: Mapping[str, Any],
        failure_kind: str,
    ) -> dict[str, Any]:
        if not isinstance(authorization, Mapping):
            raise DispatchError("WORKER_RETRY_AUTHORIZATION_INVALID")
        token = copy.deepcopy(dict(authorization))
        if (
            set(token)
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
            or not isinstance(token.get("previous_attempt_id"), str)
            or _MANAGED_ATTEMPT_ID.fullmatch(token["previous_attempt_id"]) is None
            or type(token.get("previous_observation_sequence")) is not int
            or token["previous_observation_sequence"] < 1
            or token.get("failure_kind") != failure_kind
            or not isinstance(token.get("private_proof_hash"), str)
            or _SHA256.fullmatch(token["private_proof_hash"]) is None
            or token.get("next_attempt_number") != 2
            or not isinstance(token.get("authorized_at"), str)
            or not token["authorized_at"].endswith("Z")
        ):
            raise DispatchError("WORKER_RETRY_AUTHORIZATION_INVALID")
        return token

    def retry_pre_running(
        self, intent: DispatchIntentSnapshot, *, authorization: Mapping[str, Any]
    ) -> dict[str, Any]:
        request, normalized_config_hash = self._pre_dispatch_request(intent)
        if not isinstance(authorization, Mapping):
            raise DispatchError("WORKER_RETRY_AUTHORIZATION_INVALID")
        token = copy.deepcopy(dict(authorization))
        if (
            set(token)
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
            or not isinstance(token.get("previous_attempt_id"), str)
            or _MANAGED_ATTEMPT_ID.fullmatch(token["previous_attempt_id"]) is None
            or type(token.get("previous_observation_sequence")) is not int
            or token["previous_observation_sequence"] < 1
            or token.get("failure_kind") != "pre_running_launch_failure"
            or not isinstance(token.get("private_proof_hash"), str)
            or _SHA256.fullmatch(token["private_proof_hash"]) is None
            or token.get("next_attempt_number") != 2
            or not isinstance(token.get("authorized_at"), str)
            or not token["authorized_at"].endswith("Z")
        ):
            raise DispatchError("WORKER_RETRY_AUTHORIZATION_INVALID")
        try:
            handle = self._adapter.retry_pre_running(
                **request, authorization=token
            )
        except AdapterError as error:
            if error.code in DEFERRED_DISPATCH_CODES:
                raise DispatchDeferred(error.code) from error
            raise DispatchError(error.code) from error
        except Exception as error:
            raise DispatchError("WORKER_RETRY_UNAVAILABLE") from error
        if (
            not isinstance(handle, AdapterHandle)
            or handle.adapter_version != intent.adapter_version
            or handle.task_id != intent.task_id
            or handle.node_id != intent.node_id
            or handle.plan_hash != intent.plan_hash
            or handle.idempotency_key != intent.node_idempotency_key
            or handle.algorithm != request["algorithm"]
            or handle.fingerprint.get("normalized_config_hash")
            != normalized_config_hash
        ):
            raise DispatchError("DISPATCH_FINGERPRINT_DRIFT")
        return handle.as_dict()

    @staticmethod
    def _handle_from_intent(intent: DispatchIntentSnapshot) -> AdapterHandle:
        if (
            intent.adapter_id != LOGICAL_ENTRYPOINT
            or intent.adapter_version not in SUPPORTED_ADAPTER_VERSIONS
            or intent.state != "dispatched"
            or intent.handle is None
        ):
            raise DispatchError("DISPATCH_RECEIPT_UNAVAILABLE")
        try:
            handle = AdapterHandle(**copy.deepcopy(intent.handle))
        except (TypeError, ValueError) as error:
            raise DispatchError("DISPATCH_RECEIPT_INVALID") from error
        if (
            handle.adapter_version != intent.adapter_version
            or handle.task_id != intent.task_id
            or handle.node_id != intent.node_id
            or handle.idempotency_key != intent.node_idempotency_key
            or handle.plan_hash != intent.plan_hash
            or (
                intent.request.get("algorithm") is not None
                and handle.algorithm != intent.request.get("algorithm")
            )
            or not is_supported_receipt_binding(
                handle.algorithm,
                handle.adapter_version,
                handle.fingerprint,
            )
        ):
            raise DispatchError("DISPATCH_RECEIPT_INVALID")
        return handle

    @staticmethod
    def _validated_checkpoint_result(
        intent: DispatchIntentSnapshot,
        handle: AdapterHandle,
        result: AdapterCheckpointProof,
        *,
        resume_result: bool,
    ) -> DispatchCheckpointProof | DispatchCheckpointResumeResult:
        if not isinstance(result, AdapterCheckpointProof):
            raise DispatchError("ADAPTER_CHECKPOINT_RESPONSE_INVALID")
        proof = result.as_dict()
        expected = {
            "schema_version",
            "task_id",
            "node_id",
            "submission_id",
            "attempt_id",
            "attempt_number",
            "checkpoint_id",
            "checkpoint_index",
            "completed_updates",
            "binding_hash",
            "submission_receipt_record_hash",
            "ready_record_hash",
            "checkpoint_manifest_relative_path",
            "checkpoint_manifest_size_bytes",
            "checkpoint_manifest_hash",
            "checkpoint_receipt_record_hash",
            "checkpoint_proof_hash",
            "checkpoint_created_at",
            "state",
            "resume_id",
            "resume_request_record_hash",
            "resume_acknowledgement_record_hash",
            "resume_acknowledged_at",
            "proof_hash",
        }
        payload = {
            key: copy.deepcopy(value)
            for key, value in proof.items()
            if key != "proof_hash"
        }
        _, actual_hash = encode_document(payload)
        state = proof.get("state")
        resume_id = proof.get("resume_id")
        request_hash = proof.get("resume_request_record_hash")
        acknowledgement_hash = proof.get(
            "resume_acknowledgement_record_hash"
        )
        checkpoint_proof_hash = proof.get("checkpoint_proof_hash")
        acknowledged_at = proof.get("resume_acknowledged_at")

        def timestamp(value: Any) -> datetime | None:
            if not isinstance(value, str) or not value.endswith("Z"):
                return None
            try:
                parsed = datetime.fromisoformat(value[:-1] + "+00:00")
            except ValueError:
                return None
            return parsed if parsed.tzinfo is not None else None

        created = timestamp(proof.get("checkpoint_created_at"))
        acknowledged = (
            None if acknowledged_at is None else timestamp(acknowledged_at)
        )
        hashes_valid = all(
            isinstance(proof.get(field), str)
            and _SHA256.fullmatch(proof[field]) is not None
            for field in (
                "binding_hash",
                "submission_receipt_record_hash",
                "ready_record_hash",
                "checkpoint_manifest_hash",
                "checkpoint_receipt_record_hash",
                "proof_hash",
            )
        )
        resume_fields_valid = False
        if state in {"waiting", "action_required"}:
            resume_fields_valid = all(
                value is None
                for value in (
                    checkpoint_proof_hash,
                    resume_id,
                    request_hash,
                    acknowledgement_hash,
                    acknowledged_at,
                )
            )
        elif state == "requested":
            resume_fields_valid = (
                isinstance(resume_id, str)
                and _MANAGED_RESUME_ID.fullmatch(resume_id) is not None
                and isinstance(checkpoint_proof_hash, str)
                and _SHA256.fullmatch(checkpoint_proof_hash) is not None
                and isinstance(request_hash, str)
                and _SHA256.fullmatch(request_hash) is not None
                and acknowledgement_hash is None
                and acknowledged_at is None
            )
        elif state == "resumed":
            resume_fields_valid = (
                isinstance(resume_id, str)
                and _MANAGED_RESUME_ID.fullmatch(resume_id) is not None
                and isinstance(checkpoint_proof_hash, str)
                and _SHA256.fullmatch(checkpoint_proof_hash) is not None
                and isinstance(request_hash, str)
                and _SHA256.fullmatch(request_hash) is not None
                and isinstance(acknowledgement_hash, str)
                and _SHA256.fullmatch(acknowledgement_hash) is not None
                and acknowledged is not None
                and created is not None
                and acknowledged >= created
            )
        relative_path = proof.get("checkpoint_manifest_relative_path")
        checkpoint_id = proof.get("checkpoint_id")
        if (
            set(proof) != expected
            or proof.get("schema_version") != "1.0.0"
            or proof.get("task_id") != intent.task_id
            or proof.get("node_id") != intent.node_id
            or proof.get("submission_id") != handle.submission_id
            or not isinstance(handle.submission_id, str)
            or _MANAGED_SUBMISSION_ID.fullmatch(handle.submission_id) is None
            or proof.get("attempt_id") is None
            or _MANAGED_ATTEMPT_ID.fullmatch(proof["attempt_id"]) is None
            or type(proof.get("attempt_number")) is not int
            or proof["attempt_number"] not in {1, 2}
            or not isinstance(checkpoint_id, str)
            or _MANAGED_CHECKPOINT_ID.fullmatch(checkpoint_id) is None
            or proof.get("checkpoint_index") != 1
            or proof.get("completed_updates") != 1
            or not hashes_valid
            or relative_path != f"checkpoints/{checkpoint_id}/manifest.json"
            or not isinstance(relative_path, str)
            or "\\" in relative_path
            or type(proof.get("checkpoint_manifest_size_bytes")) is not int
            or not 1 <= proof["checkpoint_manifest_size_bytes"] <= 64 * 1024
            or created is None
            or state
            not in {"waiting", "requested", "resumed", "action_required"}
            or (resume_result and state not in {"requested", "resumed"})
            or not resume_fields_valid
            or proof["proof_hash"] != actual_hash
        ):
            raise DispatchError("ADAPTER_CHECKPOINT_RESPONSE_INVALID")
        arguments = {
            key: copy.deepcopy(value)
            for key, value in proof.items()
            if key != "schema_version"
        }
        result_type = (
            DispatchCheckpointResumeResult
            if resume_result
            else DispatchCheckpointProof
        )
        return result_type(**arguments)

    def probe_runtime_checkpoint(
        self, intent: DispatchIntentSnapshot
    ) -> DispatchCheckpointProof | None:
        """Read the exact checkpoint without launching or requesting resume."""

        handle = self._handle_from_intent(intent)
        try:
            result = self._adapter.probe_runtime_checkpoint(handle)
        except AdapterError as error:
            if error.code in {
                "ADAPTER_SUBMISSION_BUSY",
                "CHECKPOINT_EVIDENCE_PENDING",
                "CHECKPOINT_ACTION_REQUIRED",
            }:
                raise DispatchDeferred(error.code) from error
            raise DispatchError(error.code) from error
        except Exception as error:
            raise DispatchError("CHECKPOINT_PROBE_UNAVAILABLE") from error
        if result is None:
            return None
        validated = self._validated_checkpoint_result(
            intent, handle, result, resume_result=False
        )
        assert isinstance(validated, DispatchCheckpointProof)
        return validated

    def resume_runtime_checkpoint(
        self,
        intent: DispatchIntentSnapshot,
        *,
        authorization: Mapping[str, Any],
    ) -> DispatchCheckpointResumeResult:
        """Append/replay one exact same-live-attempt resume request."""

        handle = self._handle_from_intent(intent)
        required = {
            "schema_version",
            "intent_id",
            "task_id",
            "node_id",
            "submission_id",
            "attempt_id",
            "attempt_number",
            "checkpoint_id",
            "checkpoint_manifest_hash",
            "checkpoint_receipt_record_hash",
            "checkpoint_proof_hash",
            "resume_id",
            "authorized_at",
            "resume_request_record_hash",
        }
        if not isinstance(authorization, Mapping) or set(authorization) != required:
            raise DispatchError("CHECKPOINT_RESUME_AUTHORIZATION_INVALID")
        token = copy.deepcopy(dict(authorization))
        request_payload = {
            key: copy.deepcopy(token[key])
            for key in (
                "schema_version",
                "resume_id",
                "submission_id",
                "attempt_id",
                "attempt_number",
                "checkpoint_id",
                "checkpoint_manifest_hash",
                "checkpoint_receipt_record_hash",
                "checkpoint_proof_hash",
                "authorized_at",
            )
        }
        _, expected_request_hash = encode_document(request_payload)
        if (
            token.get("schema_version") != "1.0.0"
            or token.get("intent_id") != intent.intent_id
            or token.get("task_id") != intent.task_id
            or token.get("node_id") != intent.node_id
            or token.get("submission_id") != handle.submission_id
            or _MANAGED_ATTEMPT_ID.fullmatch(token.get("attempt_id", "")) is None
            or token.get("attempt_number") not in {1, 2}
            or _MANAGED_CHECKPOINT_ID.fullmatch(
                token.get("checkpoint_id", "")
            )
            is None
            or any(
                _SHA256.fullmatch(token.get(field, "")) is None
                for field in (
                    "checkpoint_manifest_hash",
                    "checkpoint_receipt_record_hash",
                    "checkpoint_proof_hash",
                    "resume_request_record_hash",
                )
            )
            or _MANAGED_RESUME_ID.fullmatch(token.get("resume_id", "")) is None
            or _timestamp_is_invalid(token.get("authorized_at"))
            or token["resume_request_record_hash"] != expected_request_hash
        ):
            raise DispatchError("CHECKPOINT_RESUME_AUTHORIZATION_INVALID")
        try:
            result = self._adapter.resume_runtime_checkpoint(
                handle, authorization=token
            )
        except AdapterError as error:
            if error.code in {
                "ADAPTER_SUBMISSION_BUSY",
                "CHECKPOINT_EVIDENCE_PENDING",
                "CHECKPOINT_ACTION_REQUIRED",
            }:
                raise DispatchDeferred(error.code) from error
            raise DispatchError(error.code) from error
        except Exception as error:
            raise DispatchError("CHECKPOINT_RESUME_UNAVAILABLE") from error
        validated = self._validated_checkpoint_result(
            intent, handle, result, resume_result=True
        )
        assert isinstance(validated, DispatchCheckpointResumeResult)
        return validated

    def status(self, intent: DispatchIntentSnapshot) -> dict[str, Any]:
        handle = self._handle_from_intent(intent)
        try:
            return self._adapter.status(handle).as_dict()
        except AdapterError as error:
            raise DispatchError(error.code) from error
        except Exception as error:
            raise DispatchError("ADAPTER_STATUS_UNAVAILABLE") from error

    def supports_exact_cancel(
        self, intent: DispatchIntentSnapshot, *, attempt_id: str
    ) -> bool:
        """Read-only exact-Worker cancellation capability probe."""

        handle = self._handle_from_intent(intent)
        try:
            result = self._adapter.supports_exact_cancel(
                handle, attempt_id=attempt_id
            )
        except AdapterError as error:
            raise DispatchError(error.code) from error
        except Exception as error:
            raise DispatchError("ADAPTER_CANCEL_CAPABILITY_UNAVAILABLE") from error
        if type(result) is not bool:
            raise DispatchError("ADAPTER_CANCEL_CAPABILITY_INVALID")
        return result

    def supports_exact_timeout(
        self, intent: DispatchIntentSnapshot, *, attempt_id: str
    ) -> dict[str, Any] | None:
        """Read one path-free exact-Worker timeout capability proof."""

        handle = self._handle_from_intent(intent)
        try:
            result = self._adapter.supports_exact_timeout(
                handle, attempt_id=attempt_id
            )
        except AdapterError as error:
            raise DispatchError(error.code) from error
        except Exception as error:
            raise DispatchError("ADAPTER_TIMEOUT_CAPABILITY_UNAVAILABLE") from error
        if result is None:
            return None
        if not isinstance(result, Mapping):
            raise DispatchError("ADAPTER_TIMEOUT_CAPABILITY_INVALID")
        proof = copy.deepcopy(dict(result))
        expected = {
            "schema_version",
            "private_schema_version",
            "attempt_id",
            "binding_hash",
            "capability_record_hash",
            "supported_reasons",
            "proof_hash",
        }
        supplied_hash = proof.get("proof_hash")
        payload = {
            key: copy.deepcopy(value)
            for key, value in proof.items()
            if key != "proof_hash"
        }
        _, actual_hash = encode_document(payload)
        if (
            set(proof) != expected
            or proof.get("schema_version") != "2.0.0"
            or proof.get("private_schema_version")
            not in {"1.1.0", "1.2.0", "1.3.0"}
            or proof.get("attempt_id") != attempt_id
            or not isinstance(attempt_id, str)
            or _MANAGED_ATTEMPT_ID.fullmatch(attempt_id) is None
            or not isinstance(proof.get("binding_hash"), str)
            or _SHA256.fullmatch(proof["binding_hash"]) is None
            or not isinstance(proof.get("capability_record_hash"), str)
            or _SHA256.fullmatch(proof["capability_record_hash"]) is None
            or proof.get("supported_reasons")
            != ["user_requested", "wall_time_exceeded"]
            or not isinstance(supplied_hash, str)
            or _SHA256.fullmatch(supplied_hash) is None
            or supplied_hash != actual_hash
        ):
            raise DispatchError("ADAPTER_TIMEOUT_CAPABILITY_INVALID")
        return proof

    def cancel(
        self,
        intent: DispatchIntentSnapshot,
        *,
        request_id: str,
        attempt_id: str,
        reason: str = "user_requested",
    ) -> dict[str, Any]:
        """Request/finalize cancellation for one dispatched exact receipt."""

        handle = self._handle_from_intent(intent)
        try:
            result = self._adapter.cancel(
                handle,
                cancel_id=request_id,
                attempt_id=attempt_id,
                reason=reason,
            )
        except AdapterError as error:
            raise DispatchError(error.code) from error
        except Exception as error:
            raise DispatchError("ADAPTER_CANCEL_UNAVAILABLE") from error
        if not isinstance(result, AdapterManagedCancelProof):
            raise DispatchError("ADAPTER_CANCEL_RESPONSE_INVALID")
        proof = result.as_dict()
        expected = {
            "schema_version",
            "task_id",
            "request_id",
            "reason",
            "state",
            "code",
            "attempt_id",
            "capability_record_hash",
            "request_record_hash",
            "acknowledgement_record_hash",
            "terminal_status",
            "local_run_state",
            "replayed",
            "receipt_record_hash",
            "proof_hash",
        }
        supplied_hash = proof.get("proof_hash")
        payload = {
            key: copy.deepcopy(value)
            for key, value in proof.items()
            if key != "proof_hash"
        }
        _, actual_hash = encode_document(payload)
        state = proof.get("state")
        code = proof.get("code")
        capability_hash = proof.get("capability_record_hash")
        request_hash = proof.get("request_record_hash")
        acknowledgement_hash = proof.get("acknowledgement_record_hash")
        terminal_status = proof.get("terminal_status")
        replayed = proof.get("replayed")
        nullable_hashes = (
            capability_hash,
            request_hash,
            acknowledgement_hash,
        )
        hash_chain_valid = (
            all(
                value is None
                or (isinstance(value, str) and _SHA256.fullmatch(value))
                for value in nullable_hashes
            )
            and not (capability_hash is None and request_hash is not None)
            and not (request_hash is None and acknowledgement_hash is not None)
        )
        state_valid = False
        if state == "requested":
            state_valid = (
                code == "CANCEL_REQUESTED"
                and capability_hash is not None
                and request_hash is not None
                and terminal_status is None
                and replayed is False
            )
        elif state == "pending":
            state_valid = (
                code == "CANCEL_PENDING"
                and capability_hash is not None
                and request_hash is not None
                and terminal_status is None
                and replayed is True
            )
        elif state == "cancelled":
            state_valid = (
                code == "CANCEL_COMPLETED"
                and all(value is not None for value in nullable_hashes)
                and terminal_status == "Cancelled"
            )
        elif state == "terminal_won":
            state_valid = (
                code == "CANCEL_TERMINAL_WON"
                and terminal_status in {"Succeeded", "Failed"}
            )
        elif state == "deferred":
            no_capability_codes = {
                "CANCEL_MANAGED_ATTEMPT_UNAVAILABLE",
                "CANCEL_ATTEMPT_MISMATCH",
                "CANCEL_WORKER_CAPABILITY_UNAVAILABLE",
            }
            other_deferred_codes = {
                "CANCEL_WORKER_NOT_RUNNING",
                "CANCEL_EXIT_UNPROVEN",
                "CANCEL_TERMINAL_PROOF_UNAVAILABLE",
            }
            state_valid = (
                code in no_capability_codes | other_deferred_codes
                and terminal_status in {None, "Cancelled"}
                and (
                    code not in no_capability_codes
                    or (
                        all(value is None for value in nullable_hashes)
                        and replayed is False
                    )
                )
                and (
                    code
                    not in {
                        "CANCEL_EXIT_UNPROVEN",
                        "CANCEL_TERMINAL_PROOF_UNAVAILABLE",
                    }
                    or (
                        capability_hash is not None
                        and request_hash is not None
                    )
                )
            )
        if (
            set(proof) != expected
            or proof.get("schema_version") != "1.0.0"
            or proof.get("task_id") != intent.task_id
            or proof.get("request_id") != request_id
            or proof.get("attempt_id") != attempt_id
            or not isinstance(attempt_id, str)
            or _MANAGED_ATTEMPT_ID.fullmatch(attempt_id) is None
            or proof.get("reason") != reason
            or reason != "user_requested"
            or not hash_chain_valid
            or not state_valid
            or proof.get("local_run_state") != "retained"
            or type(replayed) is not bool
            or not isinstance(proof.get("receipt_record_hash"), str)
            or _SHA256.fullmatch(proof["receipt_record_hash"]) is None
            or not isinstance(supplied_hash, str)
            or _SHA256.fullmatch(supplied_hash) is None
            or supplied_hash != actual_hash
        ):
            raise DispatchError("ADAPTER_CANCEL_RESPONSE_INVALID")
        return proof

    def timeout(
        self,
        intent: DispatchIntentSnapshot,
        *,
        timeout_id: str,
        attempt_id: str,
        wall_time_seconds: int,
        started_at: str,
        deadline_at: str,
    ) -> dict[str, Any]:
        """Request or finalize timeout of one dispatched exact receipt."""

        handle = self._handle_from_intent(intent)
        try:
            result = self._adapter.timeout(
                handle,
                timeout_id=timeout_id,
                attempt_id=attempt_id,
                wall_time_seconds=wall_time_seconds,
                started_at=started_at,
                deadline_at=deadline_at,
            )
        except AdapterError as error:
            raise DispatchError(error.code) from error
        except Exception as error:
            raise DispatchError("ADAPTER_TIMEOUT_UNAVAILABLE") from error
        if not isinstance(result, AdapterManagedTimeoutProof):
            raise DispatchError("ADAPTER_TIMEOUT_RESPONSE_INVALID")
        converted = result.as_dict()
        if not isinstance(converted, Mapping):
            raise DispatchError("ADAPTER_TIMEOUT_RESPONSE_INVALID")
        proof = copy.deepcopy(dict(converted))
        expected = {
            "schema_version",
            "task_id",
            "request_id",
            "reason",
            "state",
            "code",
            "attempt_id",
            "wall_time_seconds",
            "started_at",
            "deadline_at",
            "ready_record_hash",
            "capability_record_hash",
            "request_record_hash",
            "acknowledgement_record_hash",
            "terminal_status",
            "terminal_failure_code",
            "local_run_state",
            "replayed",
            "receipt_record_hash",
            "proof_hash",
        }
        supplied_hash = proof.get("proof_hash")
        payload = {
            key: copy.deepcopy(value)
            for key, value in proof.items()
            if key != "proof_hash"
        }
        _, actual_hash = encode_document(payload)
        state = proof.get("state")
        code = proof.get("code")
        capability_hash = proof.get("capability_record_hash")
        request_hash = proof.get("request_record_hash")
        acknowledgement_hash = proof.get("acknowledgement_record_hash")
        nullable_hashes = (
            capability_hash,
            request_hash,
            acknowledgement_hash,
        )
        hash_chain_valid = (
            all(
                value is None
                or (isinstance(value, str) and _SHA256.fullmatch(value))
                for value in nullable_hashes
            )
            and not (capability_hash is None and request_hash is not None)
            and not (request_hash is None and acknowledgement_hash is not None)
        )
        terminal_status = proof.get("terminal_status")
        terminal_failure = proof.get("terminal_failure_code")
        ready_hash = proof.get("ready_record_hash")
        replayed = proof.get("replayed")
        ready_hash_valid = (
            isinstance(ready_hash, str)
            and _SHA256.fullmatch(ready_hash) is not None
        )
        exact_request_chain = (
            ready_hash_valid
            and capability_hash is not None
            and request_hash is not None
        )
        empty_stop_chain = all(value is None for value in nullable_hashes)
        state_valid = False
        if state == "requested":
            state_valid = (
                code == "TIMEOUT_REQUESTED"
                and exact_request_chain
                and terminal_status is None
                and terminal_failure is None
                and replayed is False
            )
        elif state == "pending":
            state_valid = (
                code == "TIMEOUT_PENDING"
                and exact_request_chain
                and terminal_status is None
                and terminal_failure is None
                and replayed is True
            )
        elif state == "timed_out":
            state_valid = (
                code == "TIMEOUT_COMPLETED"
                and all(value is not None for value in nullable_hashes)
                and ready_hash_valid
                and terminal_status == "Failed"
                and terminal_failure == "WALL_TIME_EXCEEDED"
            )
        elif state == "terminal_won":
            state_valid = (
                code == "TIMEOUT_TERMINAL_WON"
                and ready_hash_valid
                and capability_hash is not None
                and terminal_status in {"Succeeded", "Failed"}
                and terminal_failure is None
            )
        elif state == "deferred":
            deferred_codes = {
                "TIMEOUT_MANAGED_ATTEMPT_UNAVAILABLE",
                "TIMEOUT_ATTEMPT_MISMATCH",
                "TIMEOUT_WORKER_CAPABILITY_UNAVAILABLE",
                "TIMEOUT_WORKER_NOT_RUNNING",
                "TIMEOUT_NOT_DUE",
                "TIMEOUT_EXIT_UNPROVEN",
            }
            pre_request_codes = {
                "TIMEOUT_MANAGED_ATTEMPT_UNAVAILABLE",
                "TIMEOUT_ATTEMPT_MISMATCH",
            }
            state_valid = (
                code in deferred_codes
                and terminal_status is None
                and terminal_failure is None
                and (
                    code not in pre_request_codes
                    or (empty_stop_chain and replayed is False)
                )
                and (
                    code != "TIMEOUT_WORKER_CAPABILITY_UNAVAILABLE"
                    or (empty_stop_chain and replayed is False)
                    or (exact_request_chain and replayed is True)
                )
                and (
                    replayed is not True
                    or (
                        capability_hash is not None
                        and request_hash is not None
                    )
                )
                and (
                    code != "TIMEOUT_EXIT_UNPROVEN"
                    or exact_request_chain
                )
            )
        if (
            set(proof) != expected
            or proof.get("schema_version") != "1.0.0"
            or proof.get("task_id") != intent.task_id
            or proof.get("request_id") != timeout_id
            or proof.get("reason") != "wall_time_exceeded"
            or proof.get("attempt_id") != attempt_id
            or not isinstance(attempt_id, str)
            or _MANAGED_ATTEMPT_ID.fullmatch(attempt_id) is None
            or proof.get("wall_time_seconds") != wall_time_seconds
            or type(wall_time_seconds) is not int
            or wall_time_seconds < 1
            or proof.get("started_at") != started_at
            or proof.get("deadline_at") != deadline_at
            or not isinstance(started_at, str)
            or not isinstance(deadline_at, str)
            or (
                ready_hash is not None
                and (
                    not isinstance(ready_hash, str)
                    or _SHA256.fullmatch(ready_hash) is None
                )
            )
            or not hash_chain_valid
            or not state_valid
            or proof.get("local_run_state") != "retained"
            or type(replayed) is not bool
            or not isinstance(proof.get("receipt_record_hash"), str)
            or _SHA256.fullmatch(proof["receipt_record_hash"]) is None
            or not isinstance(supplied_hash, str)
            or _SHA256.fullmatch(supplied_hash) is None
            or supplied_hash != actual_hash
        ):
            raise DispatchError("ADAPTER_TIMEOUT_RESPONSE_INVALID")
        return proof

    def collect(self, intent: DispatchIntentSnapshot) -> list[dict[str, Any]]:
        handle = self._handle_from_intent(intent)
        try:
            return self._adapter.collect(handle)
        except AdapterError as error:
            raise DispatchError(error.code) from error
        except Exception as error:
            raise DispatchError("ADAPTER_COLLECT_UNAVAILABLE") from error

    @contextlib.contextmanager
    def verified_node_outputs(self, intent: DispatchIntentSnapshot):
        """Yield one path-free success proof while Adapter fences stay held."""

        handle = self._handle_from_intent(intent)
        try:
            with self._adapter.verified_succeeded_outputs(handle) as value:
                if not isinstance(value, Mapping):
                    raise DispatchError("ADAPTER_ARTIFACT_INVALID")
                proof = copy.deepcopy(dict(value))
                if (
                    set(proof)
                    != {
                        "schema_version",
                        "receipt_record_hash",
                        "attempt_id",
                        "attempt_number",
                        "manifests",
                    }
                    or proof.get("schema_version") != "1.0.0"
                    or _SHA256.fullmatch(proof.get("receipt_record_hash", ""))
                    is None
                    or _MANAGED_ATTEMPT_ID.fullmatch(proof.get("attempt_id", ""))
                    is None
                    or proof.get("attempt_number") != 1
                    or not isinstance(proof.get("manifests"), list)
                    or not all(
                        isinstance(item, dict) for item in proof["manifests"]
                    )
                ):
                    raise DispatchError("ADAPTER_ARTIFACT_INVALID")
                yield {**proof, "intent_id": intent.intent_id}
        except DispatchError:
            raise
        except AdapterError as error:
            raise DispatchError(error.code) from error
        except Exception as error:
            raise DispatchError("ADAPTER_ARTIFACT_UNAVAILABLE") from error

    def read_artifact(
        self, intent: DispatchIntentSnapshot, artifact_id: str
    ) -> tuple[list[dict[str, Any]], dict[str, Any], bytes]:
        handle = self._handle_from_intent(intent)
        try:
            return self._adapter.collect_and_read_artifact(handle, artifact_id)
        except AdapterError as error:
            raise DispatchError(error.code) from error
        except Exception as error:
            raise DispatchError("ADAPTER_ARTIFACT_UNAVAILABLE") from error

    def purge(
        self, intent: DispatchIntentSnapshot, *, purge_id: str
    ) -> dict[str, Any]:
        handle = self._handle_from_intent(intent)
        try:
            result = self._adapter.purge(handle, purge_id=purge_id).as_dict()
        except AdapterError as error:
            raise DispatchError(error.code) from error
        except Exception as error:
            raise DispatchError("ADAPTER_PURGE_UNAVAILABLE") from error
        expected = {"task_id", "purge_id", "local_run_state", "replayed"}
        if (
            set(result) != expected
            or result["task_id"] != intent.task_id
            or result["purge_id"] != purge_id
            or result["local_run_state"] != "deleted"
            or type(result["replayed"]) is not bool
        ):
            raise DispatchError("ADAPTER_PURGE_RESPONSE_INVALID")
        return result

    def purge_retry_exhausted(
        self,
        intent: DispatchIntentSnapshot,
        *,
        purge_id: str,
        exhaustion: RetryExhaustionCleanupProof,
    ) -> dict[str, Any]:
        """Clean an exact exhausted private chain without fabricating a receipt."""

        if (
            not isinstance(exhaustion, RetryExhaustionCleanupProof)
            or exhaustion.purge_id != purge_id
            or exhaustion.intent_id != intent.intent_id
            or exhaustion.task_id != intent.task_id
            or exhaustion.approval_id != intent.approval_id
            or intent.adapter_id != LOGICAL_ENTRYPOINT
            or intent.adapter_version not in SUPPORTED_MANAGED_REQUEST_VERSIONS
            or intent.state != "retry_exhausted"
            or intent.handle is not None
            or intent.failure_code != "WORKER_RETRY_EXHAUSTED"
            or not isinstance(intent.request, Mapping)
            or not isinstance(intent.queue_fingerprint, Mapping)
        ):
            raise DispatchError("WORKER_RETRY_EXHAUSTION_PURGE_INVALID")
        request = copy.deepcopy(dict(intent.request))
        normalized_config_hash = request.pop("normalized_config_hash", None)
        if (
            set(request)
            != {
                "task_id",
                "node_id",
                "plan_hash",
                "idempotency_key",
                "project_id",
                "principal_id",
                "algorithm",
                "dataset",
                "task_type",
                "parameters",
                "resources",
            }
            or not isinstance(normalized_config_hash, str)
            or request["task_id"] != intent.task_id
            or request["node_id"] != intent.node_id
            or request["plan_hash"] != intent.plan_hash
            or request["idempotency_key"] != intent.node_idempotency_key
            or request["project_id"] != exhaustion.project_id
            or request["principal_id"] != exhaustion.principal_id
            or request["algorithm"]
            != {"id": ALGORITHM_ID, "version": intent.adapter_version}
            or intent.queue_fingerprint.get("normalized_config_hash")
            != normalized_config_hash
            or not is_supported_receipt_binding(
                request["algorithm"],
                intent.adapter_version,
                intent.queue_fingerprint,
            )
        ):
            raise DispatchError("WORKER_RETRY_EXHAUSTION_PURGE_INVALID")
        try:
            result = self._adapter.purge_retry_exhausted(
                **request,
                purge_id=purge_id,
                exhaustion=exhaustion.adapter_token(),
            ).as_dict()
        except AdapterError as error:
            raise DispatchError(error.code) from error
        except Exception as error:
            raise DispatchError(
                "ADAPTER_RETRY_EXHAUSTION_PURGE_UNAVAILABLE"
            ) from error
        expected = {"task_id", "purge_id", "local_run_state", "replayed"}
        if (
            set(result) != expected
            or result["task_id"] != intent.task_id
            or result["purge_id"] != purge_id
            or result["local_run_state"] != "deleted"
            or type(result["replayed"]) is not bool
        ):
            raise DispatchError("ADAPTER_PURGE_RESPONSE_INVALID")
        return result
