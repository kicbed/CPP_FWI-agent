"""Trusted bridge from durable dispatch intents to the fixed FWI Adapter."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Protocol

from .fwi_adapter import (
    ADAPTER_VERSION,
    LOGICAL_ENTRYPOINT,
    SUPPORTED_ADAPTER_VERSIONS,
    AdapterError,
    AdapterExistingDispatchReceiptProof,
    AdapterHandle,
    AdapterManagedCancelProof,
    AdapterManagedTimeoutProof,
    DeepwaveAdapter,
    is_supported_receipt_binding,
)
from .task_store import DispatchIntentSnapshot, TaskSnapshot, encode_document


_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_MANAGED_ATTEMPT_ID = re.compile(r"^attempt-[0-9a-f]{32}$")


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


class TaskDispatcher(Protocol):
    """Fixed dispatcher with supervised submit and zero-relaunch receipt paths."""

    def prepare(self, snapshot: TaskSnapshot) -> DispatchPreparation:
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

    def read_artifact(
        self, intent: DispatchIntentSnapshot, artifact_id: str
    ) -> tuple[list[dict[str, Any]], dict[str, Any], bytes]:
        ...

    def purge(
        self, intent: DispatchIntentSnapshot, *, purge_id: str
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
        input_identity = node["inputs"][0]["dataset"]
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

    def dispatch(self, intent: DispatchIntentSnapshot) -> dict[str, Any]:
        """Backward-compatible one-shot entry; production uses the scheduler."""

        return self.ensure_first_dispatch(intent)

    @staticmethod
    def supports_supervised_dispatch(intent: DispatchIntentSnapshot) -> bool:
        """Return true only for the current fixed managed Adapter identity."""

        return (
            isinstance(intent, DispatchIntentSnapshot)
            and intent.adapter_id == LOGICAL_ENTRYPOINT
            and intent.adapter_version == ADAPTER_VERSION
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
            or intent.adapter_version != ADAPTER_VERSION
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
            handle.adapter_version != ADAPTER_VERSION
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

    def probe_existing_dispatch_receipt(
        self, intent: DispatchIntentSnapshot
    ) -> DispatchReceiptProbe:
        """Recognize one exact positive receipt without launching a Worker."""

        if (
            intent.adapter_id != LOGICAL_ENTRYPOINT
            or intent.adapter_version != ADAPTER_VERSION
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
        if not isinstance(proof, AdapterExistingDispatchReceiptProof):
            raise DispatchDeferred("DISPATCH_RECEIPT_PROBE_INVALID")
        handle = proof.handle
        if (
            handle.adapter_version != ADAPTER_VERSION
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
            if (
                proof.private_schema_version != "1.1.0"
                or proof.receipt_record_hash is not None
                or not isinstance(ticket, Mapping)
                or ticket.get("state") != "spawned"
                or not isinstance(ready, Mapping)
                or not isinstance(heartbeat, Mapping)
                or heartbeat.get("state")
                not in {"running", "succeeded", "failed"}
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

    def observe_existing_worker_attempt(
        self, intent: DispatchIntentSnapshot
    ) -> dict[str, Any]:
        """Observe an exact managed attempt without dispatch capability."""

        if (
            intent.adapter_id == LOGICAL_ENTRYPOINT
            and intent.adapter_version in SUPPORTED_ADAPTER_VERSIONS
            and intent.adapter_version != ADAPTER_VERSION
        ):
            raise DispatchError("WORKER_EVIDENCE_UNAVAILABLE")
        if (
            intent.adapter_id != LOGICAL_ENTRYPOINT
            or intent.adapter_version != ADAPTER_VERSION
            or intent.state not in {"dispatching", "dispatched"}
            or (intent.state == "dispatching" and intent.handle is not None)
            or (intent.state == "dispatched" and not isinstance(intent.handle, Mapping))
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
            or handle.get("adapter_version") != ADAPTER_VERSION
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
        return {
            "evidence": copy.deepcopy(dict(evidence)),
            "handle": None if handle is None else copy.deepcopy(dict(handle)),
        }

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
            or proof.get("private_schema_version") != "1.1.0"
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
