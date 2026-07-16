"""Server-owned Guided composer and path-free Workbench facade.

The browser supplies only the small, documented FWI form.  Registry snapshots,
contract documents, resource requests, approval scopes, and every execution
identifier are assembled here.  In particular, this boundary never accepts a
filesystem path, shell fragment, Adapter handle, or Worker job identifier.
"""

from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping

from jsonschema import Draft7Validator

from scientific_runtime_contracts import compute_plan_hash

from .fwi_registry import DEEPWAVE_ALGORITHM_ID, DEEPWAVE_ALGORITHM_VERSION
from .registry_service import (
    RegistryConflict,
    RegistryCorruption,
    RegistryNotFound,
    RegistryService,
    RegistryServiceError,
    RegistryValidationError,
)
from .task_service import (
    TaskConflict,
    TaskDispatchError,
    TaskIdempotencyConflict,
    TaskNotFound,
    TaskService,
    TaskServiceError,
    TaskValidationError,
)
from .task_store import DispatchIntentSnapshot, TaskSnapshot, TaskStoreError


DATASET_ID = "marmousi_94_288"
DATASET_VERSION = "1.0.0"
ALGORITHM_ID = DEEPWAVE_ALGORITHM_ID
ALGORITHM_VERSION = DEEPWAVE_ALGORITHM_VERSION
TASK_TYPE = "acoustic_fwi_2d"
NODE_ID = "invert"
MAX_FWI_ITERATIONS = 10_000

FORM_FIELDS = frozenset(
    {
        "goal",
        "dataset_id",
        "dataset_version",
        "preset",
        "device",
        "iterations",
        "seed",
        "optimizer",
        "learning_rate",
    }
)
LEGACY_FORM_FIELDS = FORM_FIELDS - {"optimizer", "learning_rate"}
LEGACY_ALGORITHM_VERSIONS = ("1.0.0", "1.1.0")
HISTORICAL_OPTIMIZER_ALGORITHM_VERSIONS = ("1.2.0", "1.3.0")
PRESETS = frozenset({"fwi_smoke", "fwi_demo"})
DEVICES = frozenset({"cpu", "cuda"})
OPTIMIZERS = frozenset({"adam", "sgd"})
LEARNING_RATE_SCALE = 1000
LEARNING_RATE_INPUT = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]{1,3})?$")
LEARNING_RATE_BOUNDS = {
    "adam": (Decimal("0.1"), Decimal("100")),
    "sgd": (Decimal("100000"), Decimal("1000000000")),
}
GRADIENT_CLIP_QUANTILE = "0.98"
OPTIMIZATION_PROFILES = (
    {
        "id": "adam_verified",
        "label": "Adam 已验证基线",
        "optimizer": "adam",
        "learning_rate": "10",
        "recommendation": "recommended",
        "evidence": "固定 Marmousi CUDA 闭环已验证；本项目默认推荐。",
    },
    {
        "id": "adam_conservative",
        "label": "Adam 保守检查",
        "optimizer": "adam",
        "learning_rate": "2",
        "recommendation": "conservative",
        "evidence": "微型 CPU smoke 已验证 finite 更新；不等同于长程收敛结论。",
    },
    {
        "id": "sgd_calibration",
        "label": "SGD 校准起点",
        "optimizer": "sgd",
        "learning_rate": "10000000",
        "recommendation": "experimental",
        "evidence": "固定 Marmousi CUDA 两步 finite/model-update 校准已通过；仍不是收敛推荐。",
    },
)
SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
TASK_CURSOR = re.compile(r"^v1_[A-Za-z0-9_-]{4,175}$")
TASK_LIST_VIEWS = frozenset({"active", "trash"})


class WorkbenchError(RuntimeError):
    """Base class for stable Guided Workbench failures."""

    def __init__(self, code: str, errors: list[str] | tuple[str, ...]):
        self.code = code
        self.errors = tuple(errors)
        super().__init__(f"{code}: {'; '.join(self.errors)}")


class WorkbenchValidationError(WorkbenchError, ValueError):
    """A browser form or public method argument is invalid."""


class WorkbenchNotFound(WorkbenchError):
    """A task or visible Catalog entry does not exist in this session scope."""


class WorkbenchConflict(WorkbenchError):
    """A revision, plan, state, or mutation idempotency precondition conflicts."""


class WorkbenchRuntimeError(WorkbenchError):
    """A trusted persistence, dispatch, status, or artifact boundary failed."""


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _stable_digest(*parts: Any) -> str:
    encoded = json.dumps(
        parts,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _stable_id(prefix: str, *parts: Any) -> str:
    return f"{prefix}-{_stable_digest(*parts)[:40]}"


def _identity(document: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(document[key])
        for key in ("id", "version", "content_hash", "data_type")
    }


def _encode_task_cursor(task_id: str, view: str) -> str:
    if view not in TASK_LIST_VIEWS:
        raise WorkbenchValidationError(
            "INVALID_TASK_LIST_VIEW", ["view must be active or trash"]
        )
    prefix = "a" if view == "active" else "t"
    encoded = base64.urlsafe_b64encode(
        f"{prefix}:{task_id}".encode("ascii")
    ).decode("ascii")
    return "v1_" + encoded.rstrip("=")


def _decode_task_cursor(cursor: str, view: str) -> str:
    if view not in TASK_LIST_VIEWS:
        raise WorkbenchValidationError(
            "INVALID_TASK_LIST_VIEW", ["view must be active or trash"]
        )
    if not isinstance(cursor, str) or TASK_CURSOR.fullmatch(cursor) is None:
        raise WorkbenchValidationError(
            "INVALID_TASK_CURSOR", ["task cursor is invalid"]
        )
    token = cursor.removeprefix("v1_")
    padded = token + "=" * (-len(token) % 4)
    try:
        decoded = base64.b64decode(
            padded.encode("ascii"), altchars=b"-_", validate=True
        ).decode("ascii")
    except (binascii.Error, UnicodeDecodeError) as error:
        raise WorkbenchValidationError(
            "INVALID_TASK_CURSOR", ["task cursor is invalid"]
        ) from error
    prefix = "a" if view == "active" else "t"
    expected_prefix = prefix + ":"
    task_id = decoded.removeprefix(expected_prefix)
    if (
        not decoded.startswith(expected_prefix)
        or OPAQUE_ID.fullmatch(task_id) is None
        or _encode_task_cursor(task_id, view) != cursor
    ):
        raise WorkbenchValidationError(
            "INVALID_TASK_CURSOR", ["task cursor is invalid"]
        )
    return task_id


def _value(value: Any, field: str, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, Mapping):
        return value.get(field, default)
    return getattr(value, field, default)


def _as_mapping(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return copy.deepcopy(dict(value))
    converter = getattr(value, "as_dict", None)
    if callable(converter):
        result = converter()
        if isinstance(result, Mapping):
            return copy.deepcopy(dict(result))
    fields = getattr(value, "__dataclass_fields__", None)
    if fields is not None:
        return {
            name: copy.deepcopy(getattr(value, name))
            for name in fields
        }
    raise WorkbenchRuntimeError(
        "SERVICE_RESPONSE_INVALID", ["expected a mapping-like service result"]
    )


def _timestamp(value: str, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as error:
        raise WorkbenchRuntimeError(
            "WORKBENCH_CLOCK_INVALID", [f"{field} must be an aware ISO-8601 timestamp"]
        ) from error
    if parsed.tzinfo is None:
        raise WorkbenchRuntimeError(
            "WORKBENCH_CLOCK_INVALID", [f"{field} must include a timezone"]
        )
    return parsed.astimezone(timezone.utc)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _public_dataset(dataset: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": dataset["id"],
        "version": dataset["version"],
        "content_hash": dataset["content_hash"],
        "data_type": dataset["data_type"],
        "immutable": dataset["immutable"],
        "metadata": copy.deepcopy(dataset["metadata"]),
        "lineage": copy.deepcopy(dataset["lineage"]),
    }


def _public_dispatch_reconciliation(value: Any) -> dict[str, Any]:
    """Project only the stable, non-sensitive reconciliation summary."""

    state = _value(value, "state")
    failure_code = _value(value, "failure_code")
    recorded_at = _value(value, "recorded_at")
    result = _value(value, "result")
    evidence_kind = _value(value, "evidence_kind")
    resolved_at = _value(value, "resolved_at")
    valid_failure_code = (
        isinstance(failure_code, str)
        and re.fullmatch(r"[A-Z0-9_]{1,128}", failure_code) is not None
        and failure_code.replace("_", "").isalnum()
    )
    valid_recorded_at = isinstance(recorded_at, str) and 0 < len(recorded_at) <= 80
    required = (
        state == "required"
        and result is None
        and evidence_kind is None
        and resolved_at is None
    )
    resolved = (
        state == "resolved"
        and result == "dispatched"
        and evidence_kind in {"managed_worker_receipt", "private_receipt"}
        and isinstance(resolved_at, str)
        and 0 < len(resolved_at) <= 80
    )
    if not valid_failure_code or not valid_recorded_at or not (required or resolved):
        raise WorkbenchRuntimeError(
            "SERVICE_RESPONSE_INVALID",
            ["dispatch reconciliation projection is invalid"],
        )
    _timestamp(recorded_at, field="dispatch reconciliation recorded_at")
    if resolved:
        _timestamp(resolved_at, field="dispatch reconciliation resolved_at")
    return {
        "failure_code": failure_code,
        "recorded_at": recorded_at,
        "state": "action_required" if required else "resolved",
        "result": result,
        "evidence_kind": evidence_kind,
        "resolved_at": resolved_at,
    }


def _public_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": manifest["id"],
        "version": manifest["version"],
        "adapter": {
            "protocol": manifest["adapter"]["protocol"],
            "version": manifest["adapter"]["version"],
        },
        "task_types": copy.deepcopy(manifest["task_types"]),
        "parameter_schema": copy.deepcopy(manifest["parameter_schema"]),
        "inputs": copy.deepcopy(manifest["inputs"]),
        "outputs": copy.deepcopy(manifest["outputs"]),
        "resource_limits": copy.deepcopy(manifest["resource_limits"]),
    }


def _public_node(node: Mapping[str, Any]) -> dict[str, Any]:
    # Node mutation keys are an internal Adapter boundary, not browser state.
    return {
        key: copy.deepcopy(node[key])
        for key in (
            "node_id",
            "algorithm",
            "inputs",
            "outputs",
            "dependencies",
            "parameters",
            "resources",
            "side_effects",
            "risks",
            "acceptance_criteria",
        )
    }


def _public_artifact(manifest: Mapping[str, Any]) -> dict[str, Any]:
    projected = copy.deepcopy(dict(manifest))
    # The Adapter location starts with its private Worker job identifier.  The
    # browser gets only a stable logical identity; bytes remain available only
    # through GuidedWorkbench.read_artifact(task_id, artifact_id).
    projected["location"] = {
        "relative_path": f"{projected['task_id']}/{projected['artifact_id']}"
    }
    extensions = projected.get("extensions")
    if isinstance(extensions, dict):
        adapter_detail = extensions.get("org.agent_rpc.adapter")
        if isinstance(adapter_detail, Mapping):
            public_detail = copy.deepcopy(dict(adapter_detail))
            public_detail.pop("worker_job_id", None)
            if public_detail:
                extensions["org.agent_rpc.adapter"] = public_detail
            else:
                extensions.pop("org.agent_rpc.adapter", None)
    return projected


class GuidedWorkbench:
    """Deterministic Guided facade for one authenticated project session."""

    def __init__(
        self,
        task_service: TaskService,
        registry_service: RegistryService,
        *,
        project_id: str,
        principal_id: str,
        clock: Callable[[], str] = _utc_now,
    ) -> None:
        if not isinstance(project_id, str) or OPAQUE_ID.fullmatch(project_id) is None:
            raise WorkbenchValidationError(
                "INVALID_SESSION_SCOPE", ["project_id must be a v1 opaque identifier"]
            )
        if not isinstance(principal_id, str) or OPAQUE_ID.fullmatch(principal_id) is None:
            raise WorkbenchValidationError(
                "INVALID_SESSION_SCOPE", ["principal_id must be a v1 opaque identifier"]
            )
        self._tasks = task_service
        self._registry = registry_service
        self._project_id = project_id
        self._principal_id = principal_id
        self._clock = clock

    @property
    def _scope(self) -> dict[str, str]:
        return {
            "project_id": self._project_id,
            "principal_id": self._principal_id,
        }

    def _mutation_key(self, stage: str, key: str) -> str:
        if (
            not isinstance(key, str)
            or not key
            or len(key) > 255
            or any(ord(character) < 0x20 or ord(character) == 0x7F for character in key)
        ):
            raise WorkbenchValidationError(
                "INVALID_IDEMPOTENCY_KEY",
                ["key must contain 1-255 characters and no control characters"],
            )
        return f"workbench:{stage}:{_stable_digest(self._project_id, self._principal_id, key)}"

    def _call(self, function: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        try:
            return function(*args, **kwargs)
        except WorkbenchError:
            raise
        except (TaskNotFound, RegistryNotFound) as error:
            raise WorkbenchNotFound("NOT_FOUND", [str(error)]) from error
        except (TaskIdempotencyConflict,) as error:
            raise WorkbenchConflict("IDEMPOTENCY_CONFLICT", [str(error)]) from error
        except TaskConflict as error:
            raise WorkbenchConflict("TASK_CONFLICT", [str(error)]) from error
        except TaskValidationError as error:
            raise WorkbenchValidationError(error.code, list(error.errors)) from error
        except RegistryValidationError as error:
            raise WorkbenchValidationError(error.code, list(error.errors)) from error
        except RegistryConflict as error:
            raise WorkbenchConflict("REGISTRY_CONFLICT", [str(error)]) from error
        except TaskDispatchError as error:
            raise WorkbenchRuntimeError(error.code, [str(error)]) from error
        except (RegistryCorruption,) as error:
            raise WorkbenchRuntimeError("REGISTRY_CORRUPTION", [str(error)]) from error
        except RegistryServiceError as error:
            raise WorkbenchRuntimeError("REGISTRY_UNAVAILABLE", [str(error)]) from error
        except TaskServiceError as error:
            raise WorkbenchRuntimeError("TASK_SERVICE_UNAVAILABLE", [str(error)]) from error
        except TaskStoreError as error:
            raise WorkbenchRuntimeError("TASK_STORE_UNAVAILABLE", [str(error)]) from error

    def session_capabilities(self) -> dict[str, Any]:
        return {
            "mode": "guided",
            "scope": {
                "project_id": self._project_id,
                "principal_id": self._principal_id,
            },
            "task_type": TASK_TYPE,
            "dataset": {"id": DATASET_ID, "version": DATASET_VERSION},
            "algorithm": {"id": ALGORITHM_ID, "version": ALGORITHM_VERSION},
            "form": {
                "fields": sorted(FORM_FIELDS),
                "presets": sorted(PRESETS),
                "devices": sorted(DEVICES),
                "iterations": {"minimum": 1, "maximum": MAX_FWI_ITERATIONS},
                "seed": {"minimum": 0, "maximum": 2147483647},
                "optimizers": sorted(OPTIMIZERS),
                "learning_rate": {
                    "representation": "decimal_string",
                    "scale": LEARNING_RATE_SCALE,
                    "bounds": {
                        optimizer: {
                            "minimum": format(bounds[0], "f"),
                            "maximum": format(bounds[1], "f"),
                        }
                        for optimizer, bounds in sorted(LEARNING_RATE_BOUNDS.items())
                    },
                },
                "gradient_clip_quantile": {
                    "value": GRADIENT_CLIP_QUANTILE,
                    "editable": False,
                },
                "optimization_profiles": copy.deepcopy(list(OPTIMIZATION_PROFILES)),
            },
            "features": {
                "approval_required": True,
                "abandon_pre_runtime": True,
                "permanent_delete_from_trash": True,
                "startup_dispatch_recovery": False,
                "startup_receipt_recovery": False,
                "startup_status_catchup": False,
                "supervised_runtime_scheduling": True,
                "continuous_status_supervision": True,
                "supervisor_leases": True,
                "running_cancel": True,
                "runtime_timeout": True,
                "positive_receipt_reconciliation": True,
                "automatic_reconciliation": False,
                "streaming_events": False,
            },
            "capabilities": {
                "cancel": True,
                # `retry` remains the browser/manual mutation capability.  The
                # bounded automatic policy is projected separately so clients
                # cannot mistake an internal Supervisor action for a POST API.
                "retry": False,
                "manual_retry": False,
                "finite_automatic_retry": {
                    "max_attempts": 2,
                    "max_concurrent_attempts": 1,
                    "pre_running_launch_failure": True,
                    "worker_exit": False,
                },
                "sse": False,
                "startup_dispatch_recovery": False,
                "startup_receipt_recovery": False,
                "startup_status_catchup": False,
                "supervised_runtime_scheduling": True,
                "continuous_status_supervision": True,
                "supervisor_leases": True,
                "positive_receipt_reconciliation": True,
                "automatic_reconciliation": False,
                "dag": False,
            },
        }

    def recover_runtime_on_startup(self, max_tasks: int = 10000) -> Any:
        """Run the bounded, read-only pre-lease inventory in this fixed scope.

        This is an internal composition hook, not a browser-triggered mutation.
        First dispatch, evidence projection, receipt adoption, and status
        catch-up begin only after the RuntimeSupervisor owns the active term.
        """

        if type(max_tasks) is not int or not 1 <= max_tasks <= 10000:
            raise WorkbenchValidationError(
                "INVALID_RECOVERY_LIMIT",
                ["max_tasks must be an integer from 1 to 10000"],
            )
        return self._call(
            self._tasks.recover_runtime_on_startup,
            max_tasks=max_tasks,
            **self._scope,
        )

    def list_catalog(self) -> dict[str, Any]:
        datasets = self._call(
            self._registry.list_datasets,
            project_id=self._project_id,
            principal_id=self._principal_id,
            permission="execute",
        )
        algorithms = self._call(self._registry.list_algorithms, allowlisted_only=True)
        return {
            "datasets": [
                _public_dataset(dataset)
                for dataset in datasets
                if dataset.get("id") == DATASET_ID
                and dataset.get("version") == DATASET_VERSION
                and dataset.get("data_type") == "velocity_model_2d"
            ],
            "algorithms": [
                _public_manifest(manifest)
                for manifest in algorithms
                if manifest.get("id") == ALGORITHM_ID
                and manifest.get("version") == ALGORITHM_VERSION
                and TASK_TYPE in manifest.get("task_types", [])
                and manifest.get("security", {}).get("allowlisted") is True
            ],
        }

    def _validated_form(
        self, form: Mapping[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], bool]:
        if not isinstance(form, Mapping):
            raise WorkbenchValidationError(
                "INVALID_FORM", ["form must be a JSON object"]
            )
        keys = set(form)
        legacy_form = keys == LEGACY_FORM_FIELDS
        unknown = sorted(str(key) for key in keys - FORM_FIELDS)
        missing = sorted(FORM_FIELDS - keys)
        if keys not in (FORM_FIELDS, LEGACY_FORM_FIELDS):
            errors = []
            if missing:
                errors.append("missing fields: " + ", ".join(missing))
            if unknown:
                errors.append("unknown fields: " + ", ".join(unknown))
            raise WorkbenchValidationError("INVALID_FORM_FIELDS", errors)

        expanded = {key: copy.deepcopy(value) for key, value in form.items()}
        if legacy_form:
            expanded.update(optimizer="adam", learning_rate="10")

        goal = expanded["goal"]
        if not isinstance(goal, str) or not goal.strip() or len(goal) > 2000:
            raise WorkbenchValidationError(
                "INVALID_GOAL", ["goal must contain 1-2000 non-blank characters"]
            )
        try:
            goal.encode("utf-8")
        except UnicodeEncodeError as error:
            raise WorkbenchValidationError(
                "INVALID_GOAL", ["goal must contain valid Unicode text"]
            ) from error
        if (
            expanded["dataset_id"] != DATASET_ID
            or expanded["dataset_version"] != DATASET_VERSION
        ):
            raise WorkbenchValidationError(
                "DATASET_UNSUPPORTED",
                [f"P1 Guided supports only {DATASET_ID}@{DATASET_VERSION}"],
            )
        if not isinstance(expanded["preset"], str) or expanded["preset"] not in PRESETS:
            raise WorkbenchValidationError(
                "PRESET_UNSUPPORTED", ["preset must be fwi_smoke or fwi_demo"]
            )
        if not isinstance(expanded["device"], str) or expanded["device"] not in DEVICES:
            raise WorkbenchValidationError(
                "DEVICE_UNSUPPORTED", ["device must be cpu or cuda"]
            )
        iterations = expanded["iterations"]
        if (
            type(iterations) is not int
            or not 1 <= iterations <= MAX_FWI_ITERATIONS
        ):
            raise WorkbenchValidationError(
                "ITERATIONS_OUT_OF_RANGE",
                [f"iterations must be an integer from 1 to {MAX_FWI_ITERATIONS}"],
            )
        seed = expanded["seed"]
        if type(seed) is not int or not 0 <= seed <= 2147483647:
            raise WorkbenchValidationError(
                "SEED_OUT_OF_RANGE",
                ["seed must be an integer from 0 to 2147483647"],
            )
        optimizer = expanded["optimizer"]
        if not isinstance(optimizer, str) or optimizer not in OPTIMIZERS:
            raise WorkbenchValidationError(
                "OPTIMIZER_UNSUPPORTED", ["optimizer must be adam or sgd"]
            )
        learning_rate_text = expanded["learning_rate"]
        if (
            not isinstance(learning_rate_text, str)
            or LEARNING_RATE_INPUT.fullmatch(learning_rate_text) is None
        ):
            raise WorkbenchValidationError(
                "LEARNING_RATE_INVALID",
                ["learning_rate must be a plain positive decimal with at most 3 places"],
            )
        try:
            learning_rate = Decimal(learning_rate_text)
        except InvalidOperation as error:
            raise WorkbenchValidationError(
                "LEARNING_RATE_INVALID", ["learning_rate is not a finite decimal"]
            ) from error
        minimum, maximum = LEARNING_RATE_BOUNDS[optimizer]
        scaled = learning_rate * LEARNING_RATE_SCALE
        if (
            not learning_rate.is_finite()
            or not minimum <= learning_rate <= maximum
            or scaled != scaled.to_integral_value()
        ):
            raise WorkbenchValidationError(
                "LEARNING_RATE_OUT_OF_RANGE",
                [
                    f"{optimizer} learning_rate must be in "
                    f"{format(minimum, 'f')}..{format(maximum, 'f')} "
                    "with at most 0.001 precision"
                ],
            )

        dataset = self._call(
            self._registry.get_dataset,
            project_id=self._project_id,
            principal_id=self._principal_id,
            dataset_id=DATASET_ID,
            version=DATASET_VERSION,
            permission="execute",
        )
        manifest = self._call(
            self._registry.get_algorithm,
            algorithm_id=ALGORITHM_ID,
            version=ALGORITHM_VERSION,
            require_allowlisted=True,
        )
        if (
            dataset.get("data_type") != "velocity_model_2d"
            or TASK_TYPE not in manifest.get("task_types", [])
            or manifest.get("security", {}).get("allowlisted") is not True
        ):
            raise WorkbenchRuntimeError(
                "GUIDED_CAPABILITY_UNAVAILABLE",
                ["the fixed Dataset/Algorithm registration is not executable"],
            )
        normalized = {
            field: copy.deepcopy(expanded[field]) for field in sorted(FORM_FIELDS)
        }
        normalized["learning_rate"] = format(learning_rate.normalize(), "f")
        normalized["learning_rate_milli"] = int(scaled)
        return normalized, dataset, manifest, legacy_form

    @staticmethod
    def _resources(form: Mapping[str, Any], manifest: Mapping[str, Any]) -> dict[str, Any]:
        # The browser selects only the device.  Counts and safety ceilings are
        # a deterministic server policy bounded by the registered manifest.
        limits = manifest["resource_limits"]
        device = form["device"]
        wall_time = min(
            int(limits["max_wall_time_seconds"]),
            (600 if device == "cpu" else 300)
            + int(form["iterations"]) * (60 if device == "cpu" else 30),
        )
        return {
            "device": device,
            "gpu_count": 1 if device == "cuda" else 0,
            "cpu_cores": min(4, int(limits["max_cpu_cores"])),
            "memory_mb": min(8192, int(limits["max_memory_mb"])),
            "wall_time_seconds": wall_time,
        }

    @staticmethod
    def _optimization_suggestions(form: Mapping[str, Any]) -> list[str]:
        optimizer = form["optimizer"]
        learning_rate = form["learning_rate"]
        if optimizer == "adam" and learning_rate == "10":
            guidance = (
                "Adam learning_rate=10 with gradient_clip_quantile=0.98 is the "
                "verified fixed-Marmousi baseline and the recommended starting point."
            )
        elif optimizer == "adam" and learning_rate == "2":
            guidance = (
                "Adam learning_rate=2 is a conservative smoke starting point; its "
                "micro-test evidence is not a long-run convergence guarantee."
            )
        elif optimizer == "sgd":
            guidance = (
                "SGD learning_rate=10000000 passed a fixed-Marmousi CUDA two-update "
                "finite/model-update calibration. It remains experimental and is "
                "not a convergence recommendation."
            )
        else:
            guidance = (
                "This custom Adam learning rate differs from the verified baseline; "
                "inspect the loss curve and model-update metrics before drawing conclusions."
            )
        return [
            guidance,
            "Review the fixed dataset, optimizer, learning rate, resources, and "
            "synthetic-workflow limits before approval.",
        ]

    def _draft(
        self,
        *,
        form: Mapping[str, Any],
        dataset: Mapping[str, Any],
        manifest: Mapping[str, Any],
        draft_id: str,
        revision: int,
    ) -> dict[str, Any]:
        return {
            "schema_version": "1.1.0",
            "draft_id": draft_id,
            "revision": revision,
            "status": "AwaitingApproval",
            "goal": form["goal"],
            "task_type": TASK_TYPE,
            "datasets": [copy.deepcopy(dict(dataset))],
            "algorithm": {"id": manifest["id"], "version": manifest["version"]},
            "parameters": {
                "preset": form["preset"],
                "device": form["device"],
                "iterations": form["iterations"],
                "seed": form["seed"],
                "optimizer": form["optimizer"],
                "learning_rate_milli": form["learning_rate_milli"],
            },
            "resources": self._resources(form, manifest),
            "missing_fields": [],
            "suggestions": self._optimization_suggestions(form),
            "confidence": {
                "intent": 1.0,
                "parameters": 1.0,
                "datasets": 1.0,
                "explanation": (
                    "All executable values came from the validated Guided form "
                    "and Registry snapshots."
                ),
            },
            "extensions": {},
        }

    def _legacy_draft_candidates(
        self,
        *,
        form: Mapping[str, Any],
        dataset: Mapping[str, Any],
        draft_id: str,
        revision: int,
    ) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        """Rebuild exact immutable seven-field Workbench request documents.

        The returned drafts are read-only replay candidates.  New mutations
        always use :meth:`_draft` and the current Algorithm version.
        """

        registered = self._call(
            self._registry.list_algorithms, allowlisted_only=True
        )
        manifests = {
            manifest.get("version"): manifest
            for manifest in registered
            if manifest.get("id") == ALGORITHM_ID
            and manifest.get("version") in LEGACY_ALGORITHM_VERSIONS
            and TASK_TYPE in manifest.get("task_types", [])
            and manifest.get("security", {}).get("allowlisted") is True
        }
        candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for version in LEGACY_ALGORITHM_VERSIONS:
            manifest = manifests.get(version)
            if manifest is None:
                continue
            parameters = {
                "preset": form["preset"],
                "device": form["device"],
                "iterations": form["iterations"],
                "seed": form["seed"],
            }
            # A changed seven-field request can exceed an old Algorithm's own
            # immutable bounds.  It is not a candidate for that old request,
            # but can still be a valid new current-version mutation.
            if not Draft7Validator(manifest["parameter_schema"]).is_valid(parameters):
                continue
            draft = {
                "schema_version": "1.0.0",
                "draft_id": draft_id,
                "revision": revision,
                "status": "AwaitingApproval",
                "goal": form["goal"],
                "task_type": TASK_TYPE,
                "datasets": [copy.deepcopy(dict(dataset))],
                "algorithm": {"id": manifest["id"], "version": manifest["version"]},
                "parameters": parameters,
                "resources": self._resources(form, manifest),
                "missing_fields": [],
                "suggestions": [
                    "Review the fixed dataset, parameters, resources, and "
                    "synthetic-workflow limits before approval."
                ],
                "confidence": {
                    "intent": 1.0,
                    "parameters": 1.0,
                    "datasets": 1.0,
                    "explanation": (
                        "All executable values came from the validated Guided form "
                        "and Registry snapshots."
                    ),
                },
                "extensions": {},
            }
            candidates.append((draft, manifest))
        return candidates

    def _historical_optimizer_draft_candidates(
        self,
        *,
        form: Mapping[str, Any],
        dataset: Mapping[str, Any],
        draft_id: str,
        revision: int,
    ) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        """Rebuild exact immutable six-parameter Workbench drafts for replay.

        Algorithm 1.2 and 1.3 accepted the optimizer-aware form now used by
        the browser.  Their mutation-ledger hashes remain valid after a 1.4
        upgrade, so both nine-field requests and expanded seven-field requests
        need read-only candidates.  A new mutation still always uses 1.4.
        """

        registered = self._call(
            self._registry.list_algorithms, allowlisted_only=True
        )
        manifests = {
            manifest.get("version"): manifest
            for manifest in registered
            if manifest.get("id") == ALGORITHM_ID
            and manifest.get("version")
            in HISTORICAL_OPTIMIZER_ALGORITHM_VERSIONS
            and TASK_TYPE in manifest.get("task_types", [])
            and manifest.get("security", {}).get("allowlisted") is True
        }
        parameters = {
            "preset": form["preset"],
            "device": form["device"],
            "iterations": form["iterations"],
            "seed": form["seed"],
            "optimizer": form["optimizer"],
            "learning_rate_milli": form["learning_rate_milli"],
        }
        candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for version in HISTORICAL_OPTIMIZER_ALGORITHM_VERSIONS:
            manifest = manifests.get(version)
            if manifest is None:
                continue
            # Old immutable bounds remain authoritative for determining
            # whether this request could have produced that historical hash.
            if not Draft7Validator(manifest["parameter_schema"]).is_valid(parameters):
                continue
            candidates.append(
                (
                    self._draft(
                        form=form,
                        dataset=dataset,
                        manifest=manifest,
                        draft_id=draft_id,
                        revision=revision,
                    ),
                    manifest,
                )
            )
        return candidates

    def _plan(
        self,
        *,
        task_id: str,
        draft: Mapping[str, Any],
        manifest: Mapping[str, Any],
        request_key: str,
        created_at: str,
    ) -> dict[str, Any]:
        revision = draft["revision"]
        plan_id = _stable_id(
            "plan",
            self._project_id,
            self._principal_id,
            task_id,
            revision,
            request_key,
        )
        node_key = _stable_id(
            "node",
            self._project_id,
            self._principal_id,
            task_id,
            revision,
            request_key,
            draft["parameters"],
            draft["resources"],
        )
        dataset = draft["datasets"][0]
        schema_version = draft.get("schema_version")
        if schema_version not in {"1.0.0", "1.1.0"}:
            raise WorkbenchRuntimeError(
                "SERVICE_RESPONSE_INVALID",
                ["draft schema version cannot be used to compose a plan"],
            )
        plan = {
            "schema_version": schema_version,
            "plan_id": plan_id,
            "draft": {"draft_id": draft["draft_id"], "revision": revision},
            "task_type": TASK_TYPE,
            "nodes": [
                {
                    "node_id": NODE_ID,
                    "algorithm": copy.deepcopy(draft["algorithm"]),
                    "inputs": [
                        {
                            "port": manifest["inputs"][0]["port"],
                            "dataset": _identity(dataset),
                        }
                    ],
                    "outputs": copy.deepcopy(manifest["outputs"]),
                    "dependencies": [],
                    "parameters": copy.deepcopy(draft["parameters"]),
                    "resources": copy.deepcopy(draft["resources"]),
                    "side_effects": copy.deepcopy(manifest["security"]["side_effects"]),
                    "idempotency_key": node_key,
                    "risks": [
                        {
                            "code": "synthetic_baseline",
                            "severity": "medium",
                            "mitigation": (
                                "Treat the result as bounded workflow evidence, "
                                "not a general scientific conclusion."
                            ),
                        }
                    ],
                    "acceptance_criteria": [
                        {
                            "id": "validated_artifacts",
                            "description": (
                                "The fixed Adapter validates all returned "
                                "artifacts and finite metrics."
                            ),
                            "required": True,
                        }
                    ],
                }
            ],
            "missing_fields": [],
            "plan_hash": "sha256:" + "0" * 64,
            "created_at": created_at,
            "extensions": {},
        }
        plan["plan_hash"] = compute_plan_hash(plan)
        return plan

    def _persist_plan(
        self,
        *,
        snapshot: TaskSnapshot,
        manifest: Mapping[str, Any],
        request_key: str,
        mutation_key: str,
    ) -> TaskSnapshot:
        plan = self._plan(
            task_id=snapshot.task_id,
            draft=snapshot.draft,
            manifest=manifest,
            request_key=request_key,
            created_at=snapshot.updated_at,
        )
        if (
            snapshot.plan is not None
            and snapshot.plan.get("plan_id") == plan["plan_id"]
            and snapshot.plan.get("draft") == plan["draft"]
        ):
            # A lost response may return the current aggregate after this plan
            # was already persisted.  Reuse its original created_at and exact
            # bytes so the mutation-ledger request hash remains replayable.
            plan = copy.deepcopy(snapshot.plan)
        result = self._call(
            self._tasks.persist_plan,
            task_id=snapshot.task_id,
            plan=plan,
            idempotency_key=mutation_key,
            **self._scope,
        )
        return _value(result, "snapshot", result)

    def create_task(self, form: Mapping[str, Any], key: str) -> dict[str, Any]:
        normalized, dataset, manifest, legacy_form = self._validated_form(form)
        create_key = self._mutation_key("create", key)
        draft_id = _stable_id(
            "draft",
            self._project_id,
            self._principal_id,
            1,
            key,
        )
        draft = self._draft(
            form=normalized,
            dataset=dataset,
            manifest=manifest,
            draft_id=draft_id,
            revision=1,
        )
        compatible_manifests = {manifest["version"]: manifest}
        historical_candidates = self._historical_optimizer_draft_candidates(
            form=normalized,
            dataset=dataset,
            draft_id=draft_id,
            revision=1,
        )
        compatible_candidates = historical_candidates
        if legacy_form:
            legacy_candidates = self._legacy_draft_candidates(
                form=normalized,
                dataset=dataset,
                draft_id=draft_id,
                revision=1,
            )
            compatible_candidates = legacy_candidates + compatible_candidates
        compatible_manifests.update(
            {
                candidate_manifest["version"]: candidate_manifest
                for _, candidate_manifest in compatible_candidates
            }
        )
        replay = self._call(
            self._tasks.lookup_compatible_create_task,
            drafts=[candidate for candidate, _ in compatible_candidates] + [draft],
            idempotency_key=create_key,
            **self._scope,
        )
        if replay is not None:
            snapshot = _value(replay, "snapshot", replay)
            original_revision = (
                snapshot.draft.get("draft_id") == draft_id
                and snapshot.draft.get("revision") == 1
            )
            if (
                original_revision
                and snapshot.status == "AwaitingApproval"
                and snapshot.plan is None
            ):
                replay_version = snapshot.draft.get("algorithm", {}).get("version")
                replay_manifest = compatible_manifests.get(replay_version)
                if replay_manifest is None:
                    raise WorkbenchRuntimeError(
                        "SERVICE_RESPONSE_INVALID",
                        ["replayed draft has no compatible immutable manifest"],
                    )
                snapshot = self._persist_plan(
                    snapshot=snapshot,
                    manifest=replay_manifest,
                    request_key=key,
                    mutation_key=self._mutation_key("create-plan", key),
                )
            result = self._project(snapshot)
            result["replayed"] = True
            return result
        created = self._call(
            self._tasks.create_task,
            draft=draft,
            idempotency_key=create_key,
            **self._scope,
        )
        snapshot = _value(created, "snapshot", created)
        create_replayed = bool(_value(created, "replayed", False))
        original_revision = (
            snapshot.draft.get("draft_id") == draft_id
            and snapshot.draft.get("revision") == 1
        )
        if not create_replayed or (
            original_revision
            and snapshot.status == "AwaitingApproval"
            and snapshot.plan is None
        ):
            snapshot = self._persist_plan(
                snapshot=snapshot,
                manifest=manifest,
                request_key=key,
                mutation_key=self._mutation_key("create-plan", key),
            )
        result = self._project(snapshot)
        result["replayed"] = create_replayed
        return result

    def revise_task(
        self,
        task_id: str,
        expected_revision: int,
        form: Mapping[str, Any],
        key: str,
    ) -> dict[str, Any]:
        if type(expected_revision) is not int or expected_revision < 1:
            raise WorkbenchValidationError(
                "INVALID_REVISION", ["expected_revision must be a positive integer"]
            )
        normalized, dataset, manifest, legacy_form = self._validated_form(form)
        current = self._call(self._tasks.get_task, task_id, **self._scope)
        current_revision = current.draft["revision"]
        target_revision = expected_revision + 1
        replay_candidate = current_revision >= target_revision
        draft = self._draft(
            form=normalized,
            dataset=dataset,
            manifest=manifest,
            draft_id=current.draft["draft_id"],
            revision=target_revision,
        )
        revised = None
        compatible_replayed = False
        compatible_manifests = {manifest["version"]: manifest}
        historical_candidates = self._historical_optimizer_draft_candidates(
            form=normalized,
            dataset=dataset,
            draft_id=current.draft["draft_id"],
            revision=target_revision,
        )
        compatible_candidates = historical_candidates
        if legacy_form:
            legacy_candidates = self._legacy_draft_candidates(
                form=normalized,
                dataset=dataset,
                draft_id=current.draft["draft_id"],
                revision=target_revision,
            )
            compatible_candidates = legacy_candidates + compatible_candidates
        compatible_manifests.update(
            {
                candidate_manifest["version"]: candidate_manifest
                for _, candidate_manifest in compatible_candidates
            }
        )
        revised = self._call(
            self._tasks.lookup_compatible_draft_revision,
            task_id=task_id,
            expected_revision=expected_revision,
            drafts=[candidate for candidate, _ in compatible_candidates] + [draft],
            idempotency_key=self._mutation_key("revise", key),
            **self._scope,
        )
        compatible_replayed = revised is not None
        if revised is None:
            revised = self._call(
                self._tasks.revise_draft,
                task_id=task_id,
                expected_revision=expected_revision,
                draft=draft,
                idempotency_key=self._mutation_key("revise", key),
                **self._scope,
            )
        snapshot = _value(revised, "snapshot", revised)
        returned_revision = snapshot.draft.get("revision")
        if returned_revision == target_revision:
            plan_version = snapshot.draft.get("algorithm", {}).get("version")
            plan_manifest = compatible_manifests.get(plan_version)
            if plan_manifest is None:
                raise WorkbenchRuntimeError(
                    "SERVICE_RESPONSE_INVALID",
                    ["revised draft has no compatible immutable manifest"],
                )
            snapshot = self._persist_plan(
                snapshot=snapshot,
                manifest=plan_manifest,
                request_key=key,
                mutation_key=self._mutation_key("revise-plan", key),
            )
        elif type(returned_revision) is not int or returned_revision < target_revision:
            raise WorkbenchRuntimeError(
                "SERVICE_RESPONSE_INVALID",
                ["revision replay returned an older task aggregate"],
            )
        result = self._project(snapshot)
        result["replayed"] = compatible_replayed or bool(
            _value(revised, "replayed", replay_candidate)
        )
        return result

    def _approval(
        self, *, snapshot: TaskSnapshot, request_key: str, decided_at: str
    ) -> dict[str, Any]:
        if snapshot.plan is None:
            raise WorkbenchConflict("PLAN_REQUIRED", ["task has no current plan"])
        decided = _timestamp(decided_at, field="clock")
        dataset = snapshot.draft["datasets"][0]
        resources = copy.deepcopy(snapshot.draft["resources"])
        return {
            "schema_version": "1.1.0",
            "approval_id": _stable_id(
                "approval",
                self._project_id,
                self._principal_id,
                snapshot.task_id,
                snapshot.draft["revision"],
                request_key,
                snapshot.plan["plan_hash"],
            ),
            "plan_id": snapshot.plan["plan_id"],
            "plan_hash": snapshot.plan["plan_hash"],
            "decision": "approved",
            "actor": {"type": "user", "id": self._principal_id},
            "scope": {
                "datasets": [_identity(dataset)],
                "algorithms": [copy.deepcopy(snapshot.draft["algorithm"])],
                "resource_limits": resources,
                "side_effects": copy.deepcopy(
                    snapshot.plan["nodes"][0]["side_effects"]
                ),
                "max_tasks": 1,
                "retry_policy": {
                    "max_attempts": 2,
                    "max_concurrent_attempts": 1,
                    "max_cumulative_attempt_wall_time_seconds": 2
                    * resources["wall_time_seconds"],
                    "retryable_failure_classes": [
                        "pre_running_launch_failure",
                        "worker_exit",
                    ],
                },
            },
            "decided_at": _format_timestamp(decided),
            "expires_at": _format_timestamp(decided + timedelta(hours=1)),
            "extensions": {},
        }

    def approve_and_submit(
        self, task_id: str, plan_hash: str, key: str
    ) -> dict[str, Any]:
        if not isinstance(plan_hash, str) or SHA256.fullmatch(plan_hash) is None:
            raise WorkbenchValidationError(
                "INVALID_PLAN_HASH", ["plan_hash must be a sha256 identity"]
            )
        # Validate the key before taking the clock or performing any mutation.
        approval_key = self._mutation_key("approve", key)
        submit_key = self._mutation_key("submit", key)
        current = self._call(self._tasks.get_task, task_id, **self._scope)
        if current.plan is None or current.plan["plan_hash"] != plan_hash:
            raise WorkbenchConflict(
                "PLAN_HASH_CONFLICT", ["plan_hash does not identify the current plan"]
            )
        expected_approval_id = _stable_id(
            "approval",
            self._project_id,
            self._principal_id,
            current.task_id,
            current.draft["revision"],
            key,
            plan_hash,
        )
        if (
            current.approval is not None
            and current.approval.get("approval_id") == expected_approval_id
            and current.approval.get("plan_hash") == plan_hash
        ):
            approval = copy.deepcopy(current.approval)
        else:
            approval = self._approval(
                snapshot=current, request_key=key, decided_at=self._clock()
            )
        try:
            approved = self._call(
                self._tasks.persist_approval,
                task_id=task_id,
                approval=approval,
                idempotency_key=approval_key,
                **self._scope,
            )
        except WorkbenchConflict as error:
            # Two first-use requests can sample different clock values before
            # either approval commits.  Recover only if the winner persisted
            # the exact deterministic approval identity for this key/plan;
            # replaying its immutable bytes then matches the durable request
            # hash.  Every other conflict remains closed.
            if error.code != "IDEMPOTENCY_CONFLICT":
                raise
            converged = self._call(self._tasks.get_task, task_id, **self._scope)
            persisted = converged.approval
            if (
                persisted is None
                or persisted.get("approval_id") != expected_approval_id
                or persisted.get("plan_hash") != plan_hash
                or converged.plan is None
                or converged.plan.get("plan_hash") != plan_hash
                or persisted.get("plan_id") != converged.plan.get("plan_id")
            ):
                raise
            approval = copy.deepcopy(persisted)
            approved = self._call(
                self._tasks.persist_approval,
                task_id=task_id,
                approval=approval,
                idempotency_key=approval_key,
                **self._scope,
            )
        approved_snapshot = _value(approved, "snapshot", approved)
        submitted = self._call(
            self._tasks.submit_task,
            task_id=task_id,
            approval_id=approved_snapshot.approval["approval_id"],
            idempotency_key=submit_key,
            **self._scope,
        )
        snapshot = _value(submitted, "snapshot")
        intent = _value(submitted, "intent")
        result = self._project(snapshot, intent=intent)
        result.update(
            {
                "submitted": True,
                "replayed": bool(_value(submitted, "replayed", False)),
                "dispatch_attempted": bool(
                    _value(submitted, "dispatch_attempted", False)
                ),
            }
        )
        return result

    def abandon_task(self, task_id: str, key: str) -> dict[str, Any]:
        result = self._call(
            self._tasks.abandon_task,
            task_id=task_id,
            idempotency_key=self._mutation_key("abandon", key),
            **self._scope,
        )
        snapshot = _value(result, "snapshot", result)
        response = self._project(snapshot)
        response["replayed"] = bool(_value(result, "replayed", False))
        return response

    def cancel_task(
        self, task_id: str, key: str, reason: str
    ) -> dict[str, Any]:
        if reason != "user_requested":
            raise WorkbenchValidationError(
                "INVALID_CANCEL_REASON", ["reason must be user_requested"]
            )
        result = self._call(
            self._tasks.cancel_task,
            task_id=task_id,
            reason=reason,
            idempotency_key=self._mutation_key("cancel", key),
            **self._scope,
        )
        snapshot = _value(result, "snapshot", result)
        intent = self._call(
            self._tasks.get_dispatch_intent, task_id, **self._scope
        )
        response = self._project(snapshot, intent=intent)
        response["replayed"] = bool(_value(result, "replayed", False))
        return response

    def _project(
        self,
        snapshot: TaskSnapshot,
        *,
        intent: DispatchIntentSnapshot | Mapping[str, Any] | None = None,
        adapter_status: Any = None,
    ) -> dict[str, Any]:
        draft = snapshot.draft
        plan = snapshot.plan
        approval = snapshot.approval
        dispatch = None
        if intent is not None:
            reconciliation = _value(intent, "reconciliation")
            dispatch = {
                "state": _value(intent, "state"),
                "failure_code": _value(intent, "failure_code"),
                "created_at": _value(intent, "created_at"),
                "dispatch_claimed_at": _value(intent, "dispatch_claimed_at"),
                "outcome_recorded_at": _value(intent, "outcome_recorded_at"),
                "reconciliation": (
                    None
                    if reconciliation is None
                    else _public_dispatch_reconciliation(reconciliation)
                ),
            }
        status = _as_mapping(adapter_status)
        if status is not None:
            for internal in ("job_id", "handle", "submission_id", "relative_path"):
                status.pop(internal, None)
        cancellation = snapshot.cancellation
        cancellation_projection = (
            None
            if cancellation is None
            else {
                "state": cancellation.state,
                "reason": cancellation.reason,
                "requested_at": cancellation.requested_at,
                "resolved_at": cancellation.resolved_at,
                "failure_code": None,
            }
        )
        timeout = getattr(snapshot, "timeout", None)
        timeout_projection = (
            None
            if timeout is None
            else {
                "state": timeout.state,
                "wall_time_seconds": timeout.wall_time_seconds,
                "started_at": timeout.started_at,
                "deadline_at": timeout.deadline_at,
                "resolved_at": timeout.resolved_at,
                "failure_code": timeout.failure_code,
                "terminal_status": timeout.terminal_status,
            }
        )
        can_cancel = False
        can_cancel_task = getattr(self._tasks, "can_cancel_task", None)
        timeout_allows_cancel = timeout is None or timeout.state == "armed"
        if (
            cancellation is None
            and timeout_allows_cancel
            and callable(can_cancel_task)
        ):
            can_cancel = bool(
                self._call(
                    can_cancel_task,
                    snapshot.task_id,
                    **self._scope,
                )
            )
        return {
            "task_id": snapshot.task_id,
            "status": snapshot.status,
            "draft": {
                "draft_id": draft["draft_id"],
                "revision": draft["revision"],
                "status": draft["status"],
                "goal": draft["goal"],
                "task_type": draft["task_type"],
                "dataset": _public_dataset(draft["datasets"][0]),
                "algorithm": copy.deepcopy(draft["algorithm"]),
                "parameters": copy.deepcopy(draft["parameters"]),
                "resources": copy.deepcopy(draft["resources"]),
                "missing_fields": copy.deepcopy(draft["missing_fields"]),
                "suggestions": copy.deepcopy(draft["suggestions"]),
            },
            "plan": (
                None
                if plan is None
                else {
                    "plan_id": plan["plan_id"],
                    "plan_hash": plan["plan_hash"],
                    "draft": copy.deepcopy(plan["draft"]),
                    "task_type": plan["task_type"],
                    "nodes": [_public_node(node) for node in plan["nodes"]],
                    "created_at": plan["created_at"],
                }
            ),
            "approval": (
                None
                if approval is None
                else {
                    "approval_id": approval["approval_id"],
                    "plan_id": approval["plan_id"],
                    "plan_hash": approval["plan_hash"],
                    "decision": approval["decision"],
                    "decided_at": approval["decided_at"],
                    "expires_at": approval["expires_at"],
                }
            ),
            "dispatch": dispatch,
            "runtime_status": status,
            "can_cancel": can_cancel,
            "cancellation": cancellation_projection,
            "timeout": timeout_projection,
            "created_at": snapshot.created_at,
            "updated_at": snapshot.updated_at,
            "visibility_revision": snapshot.visibility_revision,
            "trashed_at": snapshot.trashed_at,
        }

    def get_task(self, task_id: str, refresh: bool = True) -> dict[str, Any]:
        if type(refresh) is not bool:
            raise WorkbenchValidationError(
                "INVALID_REFRESH", ["refresh must be a boolean"]
            )
        if refresh:
            result = self._call(
                self._tasks.refresh_runtime_status, task_id=task_id, **self._scope
            )
            return self._project(
                _value(result, "snapshot"),
                intent=_value(result, "intent"),
                adapter_status=_value(result, "adapter_status"),
            )
        snapshot = self._call(self._tasks.get_task, task_id, **self._scope)
        intent = self._call(
            self._tasks.get_dispatch_intent, task_id, **self._scope
        )
        return self._project(snapshot, intent=intent)

    def list_tasks(
        self, cursor: str | None = None, limit: int = 20, view: str = "active"
    ) -> dict[str, Any]:
        """Return a bounded discovery page without touching runtime adapters."""

        if type(limit) is not int or not 1 <= limit <= 50:
            raise WorkbenchValidationError(
                "INVALID_TASK_LIST_LIMIT", ["limit must be an integer from 1 to 50"]
            )
        if view not in TASK_LIST_VIEWS:
            raise WorkbenchValidationError(
                "INVALID_TASK_LIST_VIEW", ["view must be active or trash"]
            )
        raw_cursor = None if cursor is None else _decode_task_cursor(cursor, view)
        page = self._call(
            self._tasks.list_tasks,
            cursor=raw_cursor,
            limit=limit,
            view=view,
            **self._scope,
        )
        snapshots = _value(page, "snapshots")
        next_cursor = _value(page, "next_cursor")
        if not isinstance(snapshots, (list, tuple)) or (
            next_cursor is not None
            and (not isinstance(next_cursor, str) or OPAQUE_ID.fullmatch(next_cursor) is None)
        ):
            raise WorkbenchRuntimeError(
                "SERVICE_RESPONSE_INVALID", ["task list service returned an invalid page"]
            )
        tasks: list[dict[str, Any]] = []
        for snapshot in snapshots:
            if (
                not isinstance(snapshot, TaskSnapshot)
                or snapshot.project_id != self._project_id
                or snapshot.principal_id != self._principal_id
            ):
                raise WorkbenchRuntimeError(
                    "SERVICE_RESPONSE_INVALID", ["task list crossed its bound scope"]
                )
            draft = snapshot.draft
            parameters = draft.get("parameters", {})
            tasks.append(
                {
                    "task_id": snapshot.task_id,
                    "status": snapshot.status,
                    "goal": draft.get("goal", ""),
                    "algorithm": copy.deepcopy(draft.get("algorithm")),
                    "preset": parameters.get("preset"),
                    "device": parameters.get("device"),
                    "iterations": parameters.get("iterations"),
                    "seed": parameters.get("seed"),
                    "optimizer": parameters.get("optimizer"),
                    "learning_rate_milli": parameters.get("learning_rate_milli"),
                    "wall_time_seconds": draft.get("resources", {}).get(
                        "wall_time_seconds"
                    ),
                    "created_at": snapshot.created_at,
                    "updated_at": snapshot.updated_at,
                    "visibility_revision": snapshot.visibility_revision,
                    "trashed_at": snapshot.trashed_at,
                    "purge_state": (
                        "pending" if snapshot.purge_id is not None else None
                    ),
                    "purge_requested_at": snapshot.purge_requested_at,
                    # Discovery stays a bounded SQLite-only read.  The detail
                    # projection performs the exact Adapter capability probe
                    # before it exposes the mutating action.
                    "can_cancel": False,
                    "cancellation": (
                        None
                        if snapshot.cancellation is None
                        else {
                            "state": snapshot.cancellation.state,
                            "reason": snapshot.cancellation.reason,
                            "requested_at": snapshot.cancellation.requested_at,
                            "resolved_at": snapshot.cancellation.resolved_at,
                            "failure_code": None,
                        }
                    ),
                    "timeout": (
                        None
                        if getattr(snapshot, "timeout", None) is None
                        else {
                            "state": snapshot.timeout.state,
                            "wall_time_seconds": snapshot.timeout.wall_time_seconds,
                            "started_at": snapshot.timeout.started_at,
                            "deadline_at": snapshot.timeout.deadline_at,
                            "resolved_at": snapshot.timeout.resolved_at,
                            "failure_code": snapshot.timeout.failure_code,
                            "terminal_status": snapshot.timeout.terminal_status,
                        }
                    ),
                }
            )
        return {
            "tasks": tasks,
            "next_cursor": (
                None
                if next_cursor is None
                else _encode_task_cursor(next_cursor, view)
            ),
        }

    def _change_task_visibility(
        self,
        task_id: str,
        expected_visibility_revision: int,
        key: str,
        *,
        restore: bool,
    ) -> dict[str, Any]:
        if (
            type(expected_visibility_revision) is not int
            or not 0 <= expected_visibility_revision <= 2**63 - 1
        ):
            raise WorkbenchValidationError(
                "INVALID_VISIBILITY_REVISION",
                ["expected_visibility_revision must be a non-negative integer"],
            )
        stage = "restore" if restore else "trash"
        function = self._tasks.restore_task if restore else self._tasks.trash_task
        result = self._call(
            function,
            task_id=task_id,
            expected_visibility_revision=expected_visibility_revision,
            idempotency_key=self._mutation_key(stage, key),
            **self._scope,
        )
        snapshot = _value(result, "snapshot", result)
        response = self._project(snapshot)
        response["replayed"] = bool(_value(result, "replayed", False))
        return response

    def trash_task(
        self, task_id: str, expected_visibility_revision: int, key: str
    ) -> dict[str, Any]:
        return self._change_task_visibility(
            task_id,
            expected_visibility_revision,
            key,
            restore=False,
        )

    def restore_task(
        self, task_id: str, expected_visibility_revision: int, key: str
    ) -> dict[str, Any]:
        return self._change_task_visibility(
            task_id,
            expected_visibility_revision,
            key,
            restore=True,
        )

    def purge_task(
        self, task_id: str, expected_visibility_revision: int, key: str
    ) -> dict[str, Any]:
        if (
            type(expected_visibility_revision) is not int
            or not 0 <= expected_visibility_revision <= 2**63 - 1
        ):
            raise WorkbenchValidationError(
                "INVALID_VISIBILITY_REVISION",
                ["expected_visibility_revision must be a non-negative integer"],
            )
        result = self._call(
            self._tasks.purge_task,
            task_id=task_id,
            expected_visibility_revision=expected_visibility_revision,
            idempotency_key=self._mutation_key("purge", key),
            **self._scope,
        )
        response = {
            field: _value(result, field)
            for field in (
                "task_id",
                "purge_id",
                "purge_state",
                "purged_at",
                "local_run_state",
                "audit_retained",
                "replayed",
            )
        }
        if (
            response["task_id"] != task_id
            or not isinstance(response["purge_id"], str)
            or OPAQUE_ID.fullmatch(response["purge_id"]) is None
            or response["purge_state"] != "purged"
            or not isinstance(response["purged_at"], str)
            or response["local_run_state"] not in {"deleted", "not_created"}
            or response["audit_retained"] is not True
            or type(response["replayed"]) is not bool
        ):
            raise WorkbenchRuntimeError(
                "SERVICE_RESPONSE_INVALID",
                ["task purge service returned an invalid outcome"],
            )
        _timestamp(response["purged_at"], field="purged_at")
        return response

    def list_events(
        self, task_id: str, after_sequence: int = 0, limit: int = 100
    ) -> list[dict[str, Any]]:
        events = self._call(
            self._tasks.list_run_events,
            task_id,
            after_sequence=after_sequence,
            limit=limit,
            **self._scope,
        )
        projected: list[dict[str, Any]] = []
        for event in events:
            value = copy.deepcopy(event)
            extensions = value.get("extensions")
            if isinstance(extensions, dict):
                # The canonical exhaustion audit binds internal intent,
                # attempt, observation, and private Adapter proof identities.
                # Browser/API consumers need only the public retry_exhausted
                # error code; never project that internal proof extension.
                extensions.pop("org.agent_rpc.retry_exhaustion", None)
                adapter_detail = extensions.get("org.agent_rpc.adapter_status")
                if isinstance(adapter_detail, dict):
                    adapter_detail.pop("job_id", None)
            projected.append(value)
        return projected

    def list_artifacts(self, task_id: str) -> list[dict[str, Any]]:
        manifests = self._call(
            self._tasks.collect_artifacts, task_id=task_id, **self._scope
        )
        return [_public_artifact(manifest) for manifest in manifests]

    def read_artifact(
        self, task_id: str, artifact_id: str
    ) -> tuple[dict[str, Any], bytes]:
        if not isinstance(artifact_id, str) or OPAQUE_ID.fullmatch(artifact_id) is None:
            raise WorkbenchValidationError(
                "INVALID_ARTIFACT_ID", ["artifact_id must be a v1 opaque identifier"]
            )
        result = self._call(
            self._tasks.read_artifact,
            task_id=task_id,
            artifact_id=artifact_id,
            **self._scope,
        )
        if not isinstance(result, tuple) or len(result) != 2:
            raise WorkbenchRuntimeError(
                "ARTIFACT_RESPONSE_INVALID", ["artifact service returned an invalid response"]
            )
        manifest, data = result
        if not isinstance(manifest, Mapping) or not isinstance(data, bytes):
            raise WorkbenchRuntimeError(
                "ARTIFACT_RESPONSE_INVALID", ["artifact service returned an invalid response"]
            )
        return _public_artifact(manifest), data
