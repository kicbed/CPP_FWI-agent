"""Trusted P1 bridge from durable dispatch intents to the fixed FWI Adapter."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from .fwi_adapter import (
    ADAPTER_VERSION,
    LOGICAL_ENTRYPOINT,
    AdapterError,
    DeepwaveAdapter,
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
    """P1 one-shot dispatcher; automatic reconciliation remains P2."""

    def prepare(self, snapshot: TaskSnapshot) -> DispatchPreparation:
        ...

    def dispatch(self, intent: DispatchIntentSnapshot) -> dict[str, Any]:
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
