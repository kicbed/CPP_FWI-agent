"""Trusted bridge from durable dispatch intents to the fixed FWI Adapter."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from .fwi_adapter import (
    ADAPTER_VERSION,
    LOGICAL_ENTRYPOINT,
    SUPPORTED_ADAPTER_VERSIONS,
    AdapterError,
    AdapterHandle,
    DeepwaveAdapter,
    is_supported_receipt_binding,
)
from .task_store import DispatchIntentSnapshot, TaskSnapshot


class DispatchError(RuntimeError):
    """A stable, path-free preparation or one-shot dispatch failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class DispatchPreparation:
    """Side-effect-free Adapter evidence derived from a durable task view."""

    adapter_id: str
    adapter_version: str
    request: dict[str, Any]
    queue_fingerprint: dict[str, Any]


class TaskDispatcher(Protocol):
    """Fixed dispatcher with separate submit and read-only receipt paths."""

    def prepare(self, snapshot: TaskSnapshot) -> DispatchPreparation:
        ...

    def dispatch(self, intent: DispatchIntentSnapshot) -> dict[str, Any]:
        ...

    def recover_existing_receipt(
        self, intent: DispatchIntentSnapshot
    ) -> dict[str, Any]:
        ...

    def status(self, intent: DispatchIntentSnapshot) -> dict[str, Any]:
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
            raise DispatchError(error.code) from error
        except Exception as error:
            raise DispatchError("DISPATCH_UNAVAILABLE") from error
        if handle.fingerprint.get("normalized_config_hash") != normalized_config_hash:
            raise DispatchError("DISPATCH_FINGERPRINT_DRIFT")
        return handle.as_dict()

    def recover_existing_receipt(
        self, intent: DispatchIntentSnapshot
    ) -> dict[str, Any]:
        """Adopt an exact private launched receipt without first dispatch."""

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

    def status(self, intent: DispatchIntentSnapshot) -> dict[str, Any]:
        handle = self._handle_from_intent(intent)
        try:
            return self._adapter.status(handle).as_dict()
        except AdapterError as error:
            raise DispatchError(error.code) from error
        except Exception as error:
            raise DispatchError("ADAPTER_STATUS_UNAVAILABLE") from error

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
