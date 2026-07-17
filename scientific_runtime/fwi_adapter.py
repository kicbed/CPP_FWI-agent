"""Controlled P1.2a Algorithm Adapter for the fixed Deepwave FWI baseline.

The adapter deliberately supports only the single-node ``acoustic_fwi_2d``
slice.  The packaged v1 manifest also describes a legacy forward operation,
but that Worker writes the initial model to ``models/inverted.npy``.  Exposing
that file as an inverted-model output would be scientifically misleading, so
the standard adapter keeps forward unavailable until its output contract is
versioned correctly.  The existing MCP forward entry point remains unchanged.

This module is not a scheduler or a second task database.  SQLite remains the
authoritative task state.  The small, private index below exists only to make
Worker submission idempotent across adapter instances.  It never scans the run
root for executable work, and an incomplete submission is left for the P2
reconciliation design rather than being launched again speculatively.
"""

from __future__ import annotations

import copy
import contextlib
import csv
import fcntl
import hashlib
import io
import json
import math
import os
import re
import secrets
import selectors
import stat
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Protocol

from jsonschema import Draft7Validator
from PIL import Image

from scientific_runtime_contracts import schema_errors
from worker_launch_control import (
    CONTROL_DIRECTORY,
    LaunchAttemptBinding,
    ParentLaunchLease,
    WorkerAttemptEvidence,
    WorkerCancelEvidence,
    WorkerCheckpointEvidence,
    WorkerControlError,
    WorkerExitEvidence,
    WORKER_EXIT_NAME,
    WorkerStopEvidence,
    binding_from_submission_record,
    hold_idle_execution_fence,
    mark_launch_failed,
    purge_worker_cancel_control,
    read_pre_running_attempt_evidence,
    read_worker_cancel_capability,
    read_worker_cancel_evidence,
    read_worker_checkpoint_evidence,
    read_worker_stop_capability,
    read_worker_stop_evidence,
    read_worker_exit_evidence,
    read_worker_attempt_evidence,
    request_worker_cancel,
    request_worker_checkpoint_resume,
    request_worker_stop,
    record_worker_exit,
    stage_launch_attempt,
    worker_attempt_started,
)

from .fwi_registry import (
    DEEPWAVE_ALGORITHM_ID,
    DEEPWAVE_ALGORITHM_VERSION,
    load_deepwave_manifest,
)


ALGORITHM_ID = DEEPWAVE_ALGORITHM_ID
ALGORITHM_VERSION = DEEPWAVE_ALGORITHM_VERSION
ADAPTER_VERSION = "1.6.0"
SUPPORTED_RECEIPT_BINDINGS = frozenset(
    {
        ("1.0.0", "1.0.0"),
        ("1.1.0", "1.1.0"),
        ("1.2.0", "1.2.0"),
        ("1.3.0", "1.3.0"),
        ("1.4.0", "1.4.0"),
        ("1.5.0", "1.5.0"),
        (ALGORITHM_VERSION, ADAPTER_VERSION),
    }
)
SUPPORTED_ADAPTER_VERSIONS = frozenset(
    adapter_version for _, adapter_version in SUPPORTED_RECEIPT_BINDINGS
)
SUPPORTED_MANAGED_REQUEST_VERSIONS = frozenset(
    {"1.4.0", "1.5.0", ALGORITHM_VERSION}
)
_EXACT_NEGATIVE_RECONCILIATION_VERSIONS = frozenset(
    {
        ("1.4.0", "1.1.0"),
        ("1.5.0", "1.2.0"),
        ("1.6.0", "1.2.0"),
    }
)
_TRANSIENT_RECONCILIATION_CODES = frozenset(
    {"ADAPTER_SUBMISSION_BUSY", "WORKER_ATTEMPT_BUSY"}
)
LOGICAL_ENTRYPOINT = "fwi.deepwave_adapter"
MODEL_ID = "marmousi_94_288"
BOUND_MANIFEST_HASH = (
    "sha256:0e4bf7e1eca41b4ef8590c61ce469652e74b711906360bbd3c0e50e34d0689e7"
)
GRADIENT_CLIP_QUANTILE = 0.98
ADAM_LEARNING_RATE_MILLI_RANGE = (100, 100_000)
SGD_LEARNING_RATE_MILLI_RANGE = (100_000_000, 1_000_000_000_000)
MAX_JSON_BYTES = 8 * 1024 * 1024
# The only standard P1 output is a 94 x 288 float32 array (~106 KiB).  Keep
# enough room for the NPY header without permitting a Worker-controlled shape
# declaration to turn collection into a large-memory operation.
MAX_NPY_BYTES = 128 * 1024
MAX_CSV_BYTES = 8 * 1024 * 1024
MAX_PNG_BYTES = 8 * 1024 * 1024
MAX_PROBE_OUTPUT_BYTES = 1024 * 1024
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKER_PYTHON = Path("/root/.venvs/cpp-fwi-agent/bin/python")
PROBE_SLOTS = threading.BoundedSemaphore(value=2)

OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
PLAN_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
NODE_IDEMPOTENCY_KEY = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$"
)
JOB_ID = re.compile(r"^fwi-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$")
MANAGED_ATTEMPT_ID = re.compile(r"^attempt-[0-9a-f]{32}$")
MANAGED_SUBMISSION_ID = re.compile(r"^submission-[0-9a-f]{64}$")
MANAGED_CHECKPOINT_ID = re.compile(r"^checkpoint-[0-9a-f]{32}$")
MANAGED_RESUME_ID = re.compile(r"^resume-[0-9a-f]{32}$")

# These plots are fixed Algorithm 1.4 outputs, not paths copied from the
# legacy Worker manifest.  Dimensions follow the version-bound Matplotlib
# figure sizes and 160 DPI used by ``fwi_worker.plots``.  A plot layout change
# therefore requires another Algorithm/Adapter version instead of silently
# changing an immutable result contract.
FIGURE_ARTIFACT_SPECS = (
    {
        "port": "true_model_figure",
        "figure_id": "true_model",
        "title": "True Velocity Model",
        "relative_path": "figures/true_model.png",
        "width_px": 1440,
        "height_px": 608,
        "order": 2,
    },
    {
        "port": "initial_model_figure",
        "figure_id": "initial_model",
        "title": "Smoothed Initial Velocity Model",
        "relative_path": "figures/initial_model.png",
        "width_px": 1440,
        "height_px": 608,
        "order": 3,
    },
    {
        "port": "inverted_model_figure",
        "figure_id": "inverted_model",
        "title": "Inverted Velocity Model",
        "relative_path": "figures/inverted_model.png",
        "width_px": 1440,
        "height_px": 608,
        "order": 4,
    },
    {
        "port": "model_error_figure",
        "figure_id": "model_error",
        "title": "Velocity Model Error",
        "relative_path": "figures/model_error.png",
        "width_px": 1440,
        "height_px": 608,
        "order": 5,
    },
    {
        "port": "shot_gathers_figure",
        "figure_id": "shot_gathers",
        "title": "Observed, Predicted, and Residual Shot Gathers",
        "relative_path": "figures/shot_gathers.png",
        "width_px": 2160,
        "height_px": 800,
        "order": 6,
    },
    {
        "port": "loss_curve_figure",
        "figure_id": "loss_curve",
        "title": "L2 Waveform Residual Loss",
        "relative_path": "figures/loss_curve.png",
        "width_px": 1120,
        "height_px": 720,
        "order": 7,
    },
)


def is_supported_receipt_binding(
    algorithm: Mapping[str, Any],
    adapter_version: Any,
    fingerprint: Mapping[str, Any],
) -> bool:
    """Accept only immutable Algorithm/Adapter pairs emitted by P1."""

    if (
        not isinstance(algorithm, Mapping)
        or set(algorithm) != {"id", "version"}
        or algorithm.get("id") != ALGORITHM_ID
        or not isinstance(adapter_version, str)
        or (algorithm.get("version"), adapter_version)
        not in SUPPORTED_RECEIPT_BINDINGS
        or not isinstance(fingerprint, Mapping)
        or fingerprint.get("algorithm") != dict(algorithm)
        or fingerprint.get("adapter_version") != adapter_version
    ):
        return False
    return True


def _is_supported_managed_record(record: Mapping[str, Any]) -> bool:
    """Recognize exact managed-control receipts retained across 1.6 rollout."""

    version = record.get("adapter_version")
    return (
        (version == "1.4.0" and record.get("schema_version") == "1.1.0")
        or (
            version in {"1.5.0", "1.6.0"}
            and record.get("schema_version") in {"1.1.0", "1.2.0", "1.3.0"}
        )
    )


def _is_supported_managed_control_record(
    handle: "AdapterHandle", record: Mapping[str, Any]
) -> bool:
    """Retain exact control for active 1.4 tasks while 1.5 is current."""

    return (
        _is_supported_managed_record(record)
        and record.get("adapter_version") == handle.adapter_version
        and record.get("algorithm") == handle.algorithm
    )


class AdapterError(RuntimeError):
    """Base class for stable Deepwave Adapter failures."""

    def __init__(self, message: str):
        prefix = message.split(":", 1)[0]
        self.code = (
            prefix
            if re.fullmatch(r"[A-Z][A-Z0-9_]*", prefix)
            else "ADAPTER_ERROR"
        )
        super().__init__(message)


class AdapterValidationError(AdapterError, ValueError):
    """The typed request is invalid or outside the P1 capability boundary."""

    def __init__(self, code: str, errors: list[str] | tuple[str, ...]):
        self.code = code
        self.errors = tuple(errors)
        super().__init__(f"{code}: {'; '.join(self.errors)}")


class AdapterIdempotencyConflict(AdapterError):
    """An idempotency key is already bound to another immutable request."""


class AdapterHandleError(AdapterError, ValueError):
    """A handle is malformed, unknown, or inconsistent with private state."""


class AdapterStatusError(AdapterError):
    """Worker status evidence is missing or malformed."""


class AdapterArtifactError(AdapterError):
    """A Worker artifact is unavailable, unsafe, or semantically invalid."""


class AdapterPurgeError(AdapterError):
    """A local Worker run cannot be safely and durably purged."""


class AdapterUnavailable(AdapterError):
    """A trusted runtime dependency or launch operation is unavailable."""


class _AdapterLaunchAmbiguous(AdapterUnavailable):
    """A child may exist but has not crossed the fenced ready barrier."""


class _AdapterLaunchStopped(AdapterUnavailable):
    """The production launcher proved that no pre-ready child remains."""


def _is_orphaned_checkpoint_waiting(
    error: AdapterStatusError, *, idle_fence_held: bool = False
) -> bool:
    """Recognize only the stopped-Worker checkpoint crash window."""

    cause = error.__cause__
    return (
        isinstance(cause, WorkerControlError)
        and (
            cause.code == "WORKER_CHECKPOINT_ORPHANED"
            or (
                idle_fence_held
                and cause.code == "WORKER_CHECKPOINT_PENDING"
            )
        )
    )


@dataclass(frozen=True)
class AdapterValidation:
    project_id: str
    principal_id: str
    algorithm: dict[str, str]
    dataset: dict[str, Any]
    dataset_access_scope: dict[str, Any]
    task_type: str
    parameters: dict[str, Any]
    resources: dict[str, Any]
    command: str
    worker_config: dict[str, Any]
    normalized_config_hash: str
    device_details: dict[str, Any]
    fingerprint: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return copy.deepcopy(
            {
                "project_id": self.project_id,
                "principal_id": self.principal_id,
                "algorithm": self.algorithm,
                "dataset": self.dataset,
                "dataset_access_scope": self.dataset_access_scope,
                "task_type": self.task_type,
                "parameters": self.parameters,
                "resources": self.resources,
                "command": self.command,
                "worker_config": self.worker_config,
                "normalized_config_hash": self.normalized_config_hash,
                "device_details": self.device_details,
                "fingerprint": self.fingerprint,
            }
        )


@dataclass(frozen=True)
class AdapterEstimate:
    normalized_config_hash: str
    requested_resources: dict[str, Any]
    policy_limits: dict[str, Any]
    estimated_wall_time_seconds: None
    basis: str
    limitations: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "normalized_config_hash": self.normalized_config_hash,
            "requested_resources": copy.deepcopy(self.requested_resources),
            "policy_limits": copy.deepcopy(self.policy_limits),
            "estimated_wall_time_seconds": self.estimated_wall_time_seconds,
            "basis": self.basis,
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True)
class AdapterHandle:
    submission_id: str
    task_id: str
    node_id: str
    job_id: str
    idempotency_key: str
    plan_hash: str
    request_hash: str
    algorithm: dict[str, str]
    fingerprint: dict[str, Any]
    adapter_version: str = ADAPTER_VERSION

    def as_dict(self) -> dict[str, Any]:
        return copy.deepcopy(
            {
                "submission_id": self.submission_id,
                "task_id": self.task_id,
                "node_id": self.node_id,
                "job_id": self.job_id,
                "idempotency_key": self.idempotency_key,
                "plan_hash": self.plan_hash,
                "request_hash": self.request_hash,
                "algorithm": self.algorithm,
                "fingerprint": self.fingerprint,
                "adapter_version": self.adapter_version,
            }
        )


@dataclass(frozen=True)
class AdapterPrivateReceiptProof:
    """Exact launched receipt proof read under the private submission lock."""

    handle: AdapterHandle
    private_schema_version: str
    receipt_record_hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "handle": self.handle.as_dict(),
            "private_schema_version": self.private_schema_version,
            "receipt_record_hash": self.receipt_record_hash,
        }


@dataclass(frozen=True)
class AdapterExistingDispatchReceiptProof:
    """Proof of one exact, already-positive dispatch receipt."""

    evidence_kind: str
    handle: AdapterHandle
    private_schema_version: str | None
    receipt_record_hash: str | None
    worker_evidence: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "evidence_kind": self.evidence_kind,
            "handle": self.handle.as_dict(),
            "private_schema_version": self.private_schema_version,
            "receipt_record_hash": self.receipt_record_hash,
            "evidence": (
                None
                if self.worker_evidence is None
                else copy.deepcopy(self.worker_evidence)
            ),
        }


@dataclass(frozen=True)
class AdapterDispatchNotStartedProof:
    """Exact path-free proof that dispatch never crossed the ready barrier."""

    result: Literal["not_dispatched"]
    evidence_kind: Literal["managed_pre_running_failure"]
    adapter_version: str
    private_schema_version: str
    private_record_hash: str
    private_proof_hash: str
    attempt_id: str
    attempt_number: int
    evidence: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "result": self.result,
            "evidence_kind": self.evidence_kind,
            "adapter_version": self.adapter_version,
            "private_schema_version": self.private_schema_version,
            "private_record_hash": self.private_record_hash,
            "private_proof_hash": self.private_proof_hash,
            "attempt_id": self.attempt_id,
            "attempt_number": self.attempt_number,
            "evidence": copy.deepcopy(self.evidence),
        }


@dataclass(frozen=True)
class AdapterReconciliationDeferred:
    """Typed fail-closed result for a transient or uncertain private state."""

    classification: Literal["transient", "uncertain"]
    failure_code: str

    def as_dict(self) -> dict[str, str]:
        return {
            "classification": self.classification,
            "failure_code": self.failure_code,
        }


@dataclass(frozen=True)
class AdapterPreRunningRetryProof:
    """Exact stopped-attempt proof; it never authorizes a state change."""

    failure_kind: str
    previous_attempt_id: str
    previous_attempt_number: int
    private_schema_version: str
    private_proof_hash: str
    evidence: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "failure_kind": self.failure_kind,
            "previous_attempt_id": self.previous_attempt_id,
            "previous_attempt_number": self.previous_attempt_number,
            "private_schema_version": self.private_schema_version,
            "private_proof_hash": self.private_proof_hash,
            "evidence": copy.deepcopy(self.evidence),
        }


@dataclass(frozen=True)
class AdapterWorkerExitRetryProof:
    """Exact post-ready Worker exit proof; it never launches by itself."""

    failure_kind: str
    previous_attempt_id: str
    previous_attempt_number: int
    private_schema_version: str
    private_proof_hash: str
    evidence: dict[str, Any]
    exit_evidence: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "failure_kind": self.failure_kind,
            "previous_attempt_id": self.previous_attempt_id,
            "previous_attempt_number": self.previous_attempt_number,
            "private_schema_version": self.private_schema_version,
            "private_proof_hash": self.private_proof_hash,
            "evidence": copy.deepcopy(self.evidence),
            "exit_evidence": copy.deepcopy(self.exit_evidence),
        }


@dataclass(frozen=True)
class AdapterCheckpointProof:
    """Path-bounded proof for the sole same-live-attempt checkpoint."""

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
class AdapterStatus:
    job_id: str
    task_id: str
    node_id: str
    status: str
    stage: str
    completed: int
    total: int
    message: str
    updated_at: str
    terminal: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "task_id": self.task_id,
            "node_id": self.node_id,
            "status": self.status,
            "stage": self.stage,
            "completed": self.completed,
            "total": self.total,
            "message": self.message,
            "updated_at": self.updated_at,
            "terminal": self.terminal,
        }


@dataclass(frozen=True)
class AdapterCancelResult:
    supported: bool
    accepted: bool
    code: str
    status: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "supported": self.supported,
            "accepted": self.accepted,
            "code": self.code,
            "status": self.status,
        }


@dataclass(frozen=True)
class AdapterManagedCancelProof:
    """Path-free proof for one exact current managed-Worker cancellation."""

    task_id: str
    cancel_id: str
    reason: str
    state: str
    code: str
    attempt_id: str | None
    capability_record_hash: str | None
    request_record_hash: str | None
    acknowledgement_record_hash: str | None
    terminal_status: str | None
    local_run_state: str
    replayed: bool
    receipt_record_hash: str
    proof_hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0.0",
            "task_id": self.task_id,
            "request_id": self.cancel_id,
            "reason": self.reason,
            "state": self.state,
            "code": self.code,
            "attempt_id": self.attempt_id,
            "capability_record_hash": self.capability_record_hash,
            "request_record_hash": self.request_record_hash,
            "acknowledgement_record_hash": self.acknowledgement_record_hash,
            "terminal_status": self.terminal_status,
            "local_run_state": self.local_run_state,
            "replayed": self.replayed,
            "receipt_record_hash": self.receipt_record_hash,
            "proof_hash": self.proof_hash,
        }


def _managed_cancel_proof(
    *,
    task_id: str,
    cancel_id: str,
    reason: str,
    state: str,
    code: str,
    attempt_id: str | None,
    evidence: WorkerCancelEvidence | None,
    terminal_status: str | None,
    replayed: bool,
    receipt_record_hash: str,
) -> AdapterManagedCancelProof:
    payload = {
        "schema_version": "1.0.0",
        "task_id": task_id,
        "request_id": cancel_id,
        "reason": reason,
        "state": state,
        "code": code,
        "attempt_id": attempt_id,
        "capability_record_hash": (
            None if evidence is None else evidence.capability_record_hash
        ),
        "request_record_hash": (
            None if evidence is None else evidence.request_record_hash
        ),
        "acknowledgement_record_hash": (
            None if evidence is None else evidence.acknowledgement_record_hash
        ),
        "terminal_status": terminal_status,
        "local_run_state": "retained",
        "replayed": replayed,
        "receipt_record_hash": receipt_record_hash,
    }
    return AdapterManagedCancelProof(
        task_id=task_id,
        cancel_id=cancel_id,
        reason=reason,
        state=state,
        code=code,
        attempt_id=attempt_id,
        capability_record_hash=payload["capability_record_hash"],
        request_record_hash=payload["request_record_hash"],
        acknowledgement_record_hash=payload[
            "acknowledgement_record_hash"
        ],
        terminal_status=terminal_status,
        local_run_state="retained",
        replayed=replayed,
        receipt_record_hash=receipt_record_hash,
        proof_hash=_sha256_document(payload),
    )


@dataclass(frozen=True)
class AdapterManagedTimeoutProof:
    """Path-free proof for one exact current managed-Worker timeout."""

    task_id: str
    timeout_id: str
    reason: str
    state: str
    code: str
    attempt_id: str | None
    wall_time_seconds: int
    started_at: str
    deadline_at: str
    ready_record_hash: str | None
    capability_record_hash: str | None
    request_record_hash: str | None
    acknowledgement_record_hash: str | None
    terminal_status: str | None
    terminal_failure_code: str | None
    local_run_state: str
    replayed: bool
    receipt_record_hash: str
    proof_hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0.0",
            "task_id": self.task_id,
            "request_id": self.timeout_id,
            "reason": self.reason,
            "state": self.state,
            "code": self.code,
            "attempt_id": self.attempt_id,
            "wall_time_seconds": self.wall_time_seconds,
            "started_at": self.started_at,
            "deadline_at": self.deadline_at,
            "ready_record_hash": self.ready_record_hash,
            "capability_record_hash": self.capability_record_hash,
            "request_record_hash": self.request_record_hash,
            "acknowledgement_record_hash": self.acknowledgement_record_hash,
            "terminal_status": self.terminal_status,
            "terminal_failure_code": self.terminal_failure_code,
            "local_run_state": self.local_run_state,
            "replayed": self.replayed,
            "receipt_record_hash": self.receipt_record_hash,
            "proof_hash": self.proof_hash,
        }


def _managed_timeout_proof(
    *,
    task_id: str,
    timeout_id: str,
    state: str,
    code: str,
    attempt_id: str | None,
    wall_time_seconds: int,
    started_at: str,
    deadline_at: str,
    ready_record_hash: str | None,
    evidence: WorkerStopEvidence | None,
    terminal_status: str | None,
    terminal_failure_code: str | None,
    replayed: bool,
    receipt_record_hash: str,
) -> AdapterManagedTimeoutProof:
    if state in {"timed_out", "terminal_won"} and (
        ready_record_hash is None
        or evidence is None
        or (
            evidence.requested
            and evidence.ready_record_hash != ready_record_hash
        )
    ):
        raise AdapterHandleError(
            "ADAPTER_TIMEOUT_INVALID: terminal proof lacks armed Worker hashes"
        )
    if state == "timed_out" and (
        code != "TIMEOUT_COMPLETED"
        or terminal_status != "Failed"
        or terminal_failure_code != "WALL_TIME_EXCEEDED"
    ):
        raise AdapterHandleError(
            "ADAPTER_TIMEOUT_INVALID: timeout terminal proof is inconsistent"
        )
    if state == "terminal_won" and (
        code != "TIMEOUT_TERMINAL_WON"
        or terminal_status not in {"Succeeded", "Failed"}
        or terminal_failure_code is not None
    ):
        raise AdapterHandleError(
            "ADAPTER_TIMEOUT_INVALID: natural terminal proof is inconsistent"
        )
    payload = {
        "schema_version": "1.0.0",
        "task_id": task_id,
        "request_id": timeout_id,
        "reason": "wall_time_exceeded",
        "state": state,
        "code": code,
        "attempt_id": attempt_id,
        "wall_time_seconds": wall_time_seconds,
        "started_at": started_at,
        "deadline_at": deadline_at,
        "ready_record_hash": ready_record_hash,
        "capability_record_hash": (
            None if evidence is None else evidence.capability_record_hash
        ),
        "request_record_hash": (
            None if evidence is None else evidence.request_record_hash
        ),
        "acknowledgement_record_hash": (
            None if evidence is None else evidence.acknowledgement_record_hash
        ),
        "terminal_status": terminal_status,
        "terminal_failure_code": terminal_failure_code,
        "local_run_state": "retained",
        "replayed": replayed,
        "receipt_record_hash": receipt_record_hash,
    }
    return AdapterManagedTimeoutProof(
        task_id=task_id,
        timeout_id=timeout_id,
        reason="wall_time_exceeded",
        state=state,
        code=code,
        attempt_id=attempt_id,
        wall_time_seconds=wall_time_seconds,
        started_at=started_at,
        deadline_at=deadline_at,
        ready_record_hash=ready_record_hash,
        capability_record_hash=payload["capability_record_hash"],
        request_record_hash=payload["request_record_hash"],
        acknowledgement_record_hash=payload[
            "acknowledgement_record_hash"
        ],
        terminal_status=terminal_status,
        terminal_failure_code=terminal_failure_code,
        local_run_state="retained",
        replayed=replayed,
        receipt_record_hash=receipt_record_hash,
        proof_hash=_sha256_document(payload),
    )


@dataclass(frozen=True)
class AdapterPurgeResult:
    task_id: str
    purge_id: str
    local_run_state: str
    replayed: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "purge_id": self.purge_id,
            "local_run_state": self.local_run_state,
            "replayed": self.replayed,
        }


class WorkerLauncher(Protocol):
    """Trusted, non-user-selectable launcher boundary used by submit()."""

    def launch(
        self,
        *,
        command: str,
        config_path: Path,
        run_dir: Path,
        run_root: Path,
        wall_time_seconds: int = 86_400,
        checkpoint_capable: bool = False,
    ) -> Any: ...


def _sanitized_worker_environment(
    python_executable: Path, *, run_root: Path | None = None
) -> dict[str, str]:
    environment = {
        "PYTHONPATH": str(PROJECT_ROOT),
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PATH": (
            f"{python_executable.parent}:"
            "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        ),
        "HOME": "/root",
        "LANG": "C.UTF-8",
    }
    if run_root is not None:
        environment["FWI_RUN_ROOT"] = str(run_root)
    for name in (
        "CUDA_VISIBLE_DEVICES",
        "NVIDIA_VISIBLE_DEVICES",
        "NVIDIA_DRIVER_CAPABILITIES",
        "LD_LIBRARY_PATH",
        "TMPDIR",
        "OMP_NUM_THREADS",
    ):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    return environment


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _parse_timestamp(value: str, *, code: str) -> datetime:
    if not isinstance(value, str):
        raise AdapterStatusError(f"{code}: timestamp must be a string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise AdapterStatusError(f"{code}: timestamp is not RFC3339") from error
    if parsed.tzinfo is None:
        raise AdapterStatusError(f"{code}: timestamp must include an offset")
    return parsed.astimezone(timezone.utc)


def _stable_json_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise AdapterValidationError(
            "ADAPTER_REQUEST_INVALID", [f"request is not canonical JSON: {error}"]
        ) from error


def _sha256_document(value: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_stable_json_bytes(value)).hexdigest()


DIRECTORY_OPEN_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)


def _absolute_path(value: Path) -> Path:
    requested = Path(value).expanduser()
    if (
        not requested.is_absolute()
        or ".." in requested.parts
        or requested.name in {"", ".", ".."}
    ):
        raise OSError("path must be absolute and normalized beneath a named entry")
    return Path(os.path.normpath(str(requested)))


def _open_directory_fd(path: Path) -> int:
    """Open one absolute directory inode without following any path symlink."""

    candidate = _absolute_path(path)
    descriptor = os.open("/", DIRECTORY_OPEN_FLAGS)
    try:
        for part in candidate.parts[1:]:
            next_descriptor = os.open(
                part, DIRECTORY_OPEN_FLAGS, dir_fd=descriptor
            )
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _safe_parent_fd(path: Path) -> tuple[Path, int]:
    candidate = _absolute_path(path)
    if candidate.parent == candidate:
        raise OSError("path has no usable parent")
    return candidate, _open_directory_fd(candidate.parent)


def _parent_allows_owned_child(parent_status: os.stat_result) -> bool:
    mode = stat.S_IMODE(parent_status.st_mode)
    if parent_status.st_uid not in {0, os.geteuid()}:
        return False
    if not mode & 0o022:
        return True
    # Root-owned sticky directories such as /tmp cannot have an euid-owned
    # child renamed by another unprivileged user.
    return parent_status.st_uid == 0 and bool(mode & stat.S_ISVTX)


def _validate_run_root(value: Path, *, create: bool) -> Path:
    try:
        candidate = _absolute_path(value)
    except OSError as error:
        raise AdapterValidationError(
            "RUN_ROOT_INVALID", ["run root must be an absolute non-symlink path"]
        ) from error
    if candidate == Path("/") or candidate.parent == Path("/"):
        raise AdapterValidationError(
            "RUN_ROOT_INVALID", ["run root must be a dedicated nested directory"]
        )
    forbidden = tuple(
        Path(item)
        for item in (
            "/etc",
            "/usr",
            "/bin",
            "/sbin",
            "/lib",
            "/lib32",
            "/lib64",
            "/boot",
            "/proc",
            "/sys",
            "/dev",
            "/run",
        )
    )
    if any(candidate == root or _is_relative_to(candidate, root) for root in forbidden):
        raise AdapterValidationError(
            "RUN_ROOT_INVALID", ["run root overlaps a sensitive system directory"]
        )
    project_root = PROJECT_ROOT.resolve(strict=True)
    home = Path.home().resolve(strict=True)
    if (
        candidate == Path("/var")
        or candidate == project_root
        or _is_relative_to(candidate, project_root)
        or _is_relative_to(project_root, candidate)
        or candidate == home
        or _is_relative_to(home, candidate)
    ):
        raise AdapterValidationError(
            "RUN_ROOT_INVALID", ["run root overlaps a protected directory"]
        )
    parent_descriptor = -1
    root_descriptor = -1
    try:
        _, parent_descriptor = _safe_parent_fd(candidate)
        parent_status = os.fstat(parent_descriptor)
        if not _parent_allows_owned_child(parent_status):
            raise OSError("run root parent does not protect an owned child")
        try:
            root_descriptor = os.open(
                candidate.name, DIRECTORY_OPEN_FLAGS, dir_fd=parent_descriptor
            )
        except FileNotFoundError:
            if not create:
                return candidate
            try:
                os.mkdir(candidate.name, mode=0o700, dir_fd=parent_descriptor)
                os.fsync(parent_descriptor)
            except FileExistsError:
                # A concurrent first submit may have created the same root.
                # Re-open through the already trusted parent FD and apply the
                # same owner/mode checks; never accept a symlink replacement.
                pass
            root_descriptor = os.open(
                candidate.name, DIRECTORY_OPEN_FLAGS, dir_fd=parent_descriptor
            )
        root_status = os.fstat(root_descriptor)
        if (
            not stat.S_ISDIR(root_status.st_mode)
            or root_status.st_uid != os.geteuid()
            or stat.S_IMODE(root_status.st_mode) & 0o022
        ):
            raise OSError("run root ownership or permissions are unsafe")
    except OSError as error:
        raise AdapterValidationError(
            "RUN_ROOT_INVALID",
            ["run root must be an owned, protected, non-symlink directory"],
        ) from error
    finally:
        if root_descriptor >= 0:
            os.close(root_descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)
    return candidate


def _ensure_private_directory(path: Path) -> Path:
    parent_descriptor = -1
    directory_descriptor = -1
    try:
        candidate, parent_descriptor = _safe_parent_fd(path)
        try:
            os.mkdir(candidate.name, mode=0o700, dir_fd=parent_descriptor)
            os.fsync(parent_descriptor)
        except FileExistsError:
            pass
        directory_descriptor = os.open(
            candidate.name, DIRECTORY_OPEN_FLAGS, dir_fd=parent_descriptor
        )
        link_status = os.fstat(directory_descriptor)
    except OSError as error:
        raise AdapterUnavailable(
            f"ADAPTER_STATE_UNAVAILABLE: cannot create {path.name}"
        ) from error
    finally:
        if directory_descriptor >= 0:
            os.close(directory_descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)
    if (
        not stat.S_ISDIR(link_status.st_mode)
        or link_status.st_uid != os.geteuid()
        or stat.S_IMODE(link_status.st_mode) & 0o077
    ):
        raise AdapterUnavailable(
            f"ADAPTER_STATE_UNAVAILABLE: {path.name} is not a private owned directory"
        )
    return candidate


def _create_private_directory(path: Path) -> Path:
    parent_descriptor = -1
    directory_descriptor = -1
    try:
        candidate, parent_descriptor = _safe_parent_fd(path)
        os.mkdir(candidate.name, mode=0o700, dir_fd=parent_descriptor)
        os.fsync(parent_descriptor)
        directory_descriptor = os.open(
            candidate.name, DIRECTORY_OPEN_FLAGS, dir_fd=parent_descriptor
        )
        value = os.fstat(directory_descriptor)
        if (
            not stat.S_ISDIR(value.st_mode)
            or value.st_uid != os.geteuid()
            or stat.S_IMODE(value.st_mode) & 0o077
        ):
            raise OSError("new directory is not private")
        os.fsync(directory_descriptor)
        return candidate
    finally:
        if directory_descriptor >= 0:
            os.close(directory_descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)


def _require_private_directory(path: Path, *, parent: Path) -> Path:
    if path.parent != parent:
        raise AdapterHandleError(
            f"ADAPTER_HANDLE_INVALID: {path.name} escaped its parent"
        )
    parent_descriptor = -1
    directory_descriptor = -1
    try:
        candidate, parent_descriptor = _safe_parent_fd(path)
        directory_descriptor = os.open(
            candidate.name, DIRECTORY_OPEN_FLAGS, dir_fd=parent_descriptor
        )
        link_status = os.fstat(directory_descriptor)
        if (
            not stat.S_ISDIR(link_status.st_mode)
            or link_status.st_uid != os.geteuid()
            or stat.S_IMODE(link_status.st_mode) & 0o077
        ):
            raise AdapterHandleError(
                f"ADAPTER_HANDLE_INVALID: {path.name} is not a private regular directory"
            )
    except OSError as error:
        raise AdapterHandleError(
            f"ADAPTER_HANDLE_INVALID: {path.name} is unavailable"
        ) from error
    finally:
        if directory_descriptor >= 0:
            os.close(directory_descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)
    return candidate


def _unlink_directory_contents(directory_fd: int, *, expected_device: int) -> None:
    """Remove one already-open tree without following descendant symlinks."""

    for name in sorted(os.listdir(directory_fd)):
        if (
            not isinstance(name, str)
            or name in {"", ".", ".."}
            or "/" in name
            or "\x00" in name
        ):
            raise OSError("directory contains an invalid entry name")
        entry_status = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if stat.S_ISDIR(entry_status.st_mode):
            child_fd = -1
            try:
                child_fd = os.open(name, DIRECTORY_OPEN_FLAGS, dir_fd=directory_fd)
                opened_status = os.fstat(child_fd)
                if (
                    not stat.S_ISDIR(opened_status.st_mode)
                    or opened_status.st_dev != expected_device
                    or (opened_status.st_dev, opened_status.st_ino)
                    != (entry_status.st_dev, entry_status.st_ino)
                ):
                    raise OSError("directory entry changed or crossed a mount boundary")
                _unlink_directory_contents(
                    child_fd, expected_device=expected_device
                )
            finally:
                if child_fd >= 0:
                    os.close(child_fd)
            os.rmdir(name, dir_fd=directory_fd)
        else:
            # This unlinks a symlink as an object; it never follows its target.
            os.unlink(name, dir_fd=directory_fd)
    os.fsync(directory_fd)


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    directory_descriptor = -1
    descriptor = -1
    temp_name = ""
    try:
        candidate, directory_descriptor = _safe_parent_fd(path)
        for _ in range(100):
            temp_name = f".{candidate.name}.{os.urandom(16).hex()}"
            try:
                descriptor = os.open(
                    temp_name,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0),
                    0o600,
                    dir_fd=directory_descriptor,
                )
                break
            except FileExistsError:
                continue
        if descriptor < 0:
            raise AdapterUnavailable(
                "ADAPTER_STATE_UNAVAILABLE: cannot allocate an atomic state file"
            )
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(
            temp_name,
            candidate.name,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
        )
        temp_name = ""
        os.fsync(directory_descriptor)
    except AdapterError:
        raise
    except (OSError, TypeError, ValueError) as error:
        raise AdapterUnavailable(
            "ADAPTER_STATE_UNAVAILABLE: atomic state write failed"
        ) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temp_name:
            try:
                os.unlink(temp_name, dir_fd=directory_descriptor)
            except (FileNotFoundError, OSError):
                pass
        if directory_descriptor >= 0:
            os.close(directory_descriptor)


def _read_json_file(
    path: Path,
    *,
    code: str,
    max_bytes: int = MAX_JSON_BYTES,
    private: bool = False,
) -> dict[str, Any]:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    directory_descriptor = -1
    try:
        candidate, directory_descriptor = _safe_parent_fd(path)
        descriptor = os.open(candidate.name, flags, dir_fd=directory_descriptor)
    except OSError as error:
        raise AdapterStatusError(f"{code}: JSON file is unavailable") from error
    finally:
        if directory_descriptor >= 0:
            os.close(directory_descriptor)
    try:
        file_status = os.fstat(descriptor)
        if (
            not stat.S_ISREG(file_status.st_mode)
            or file_status.st_size > max_bytes
            or (
                private
                and (
                    file_status.st_uid != os.geteuid()
                    or stat.S_IMODE(file_status.st_mode) & 0o077
                )
            )
        ):
            raise AdapterStatusError(f"{code}: JSON file is not a bounded regular file")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > max_bytes:
            raise AdapterStatusError(f"{code}: JSON file is too large")
    finally:
        os.close(descriptor)
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AdapterStatusError(f"{code}: JSON file is malformed") from error
    if not isinstance(value, dict):
        raise AdapterStatusError(f"{code}: JSON root must be an object")
    return value


class SafeSubprocessWorkerLauncher:
    """Fixed argv launcher with inherited attempt and capacity fences.

    The in-process counter remains a cheap local guard.  The authoritative
    Adapter-managed, same-host capacity boundary is a set of private ``flock``
    slots beneath the run root.  Both the selected slot and the unique attempt
    lock are inherited by the Worker, so a control-process exit cannot free
    Adapter capacity or permit a duplicate managed launch while the numerical
    process is still alive.  Standalone and legacy MCP jobs remain outside this
    deliberately bounded slice.
    """

    _state_lock = threading.Lock()
    _process_active = 0

    def __init__(
        self,
        *,
        python_executable: Path | None = None,
        project_root: Path = PROJECT_ROOT,
        max_active: int = 2,
        start_timeout_seconds: float = 15.0,
    ) -> None:
        self._python = Path(python_executable or DEFAULT_WORKER_PYTHON)
        self._project_root = Path(project_root).resolve(strict=True)
        if (
            not self._python.is_absolute()
            or not self._python.is_file()
            or not os.access(self._python, os.X_OK)
        ):
            raise AdapterUnavailable(
                "WORKER_PYTHON_UNAVAILABLE: configured Python is not executable"
            )
        if type(max_active) is not int or not 1 <= max_active <= 64:
            raise ValueError("max_active must be an integer from 1 to 64")
        if (
            isinstance(start_timeout_seconds, bool)
            or not isinstance(start_timeout_seconds, (int, float))
            or not 0 < float(start_timeout_seconds) <= 300.0
        ):
            raise ValueError("start_timeout_seconds must be from 0 to 300 seconds")
        self._max_active = max_active
        self._start_timeout_seconds = float(start_timeout_seconds)

    @property
    def python_executable(self) -> Path:
        """Return the fixed runtime used for the numerical child."""

        return self._python

    def _reserve(self) -> None:
        with self._state_lock:
            if type(self)._process_active >= self._max_active:
                raise AdapterUnavailable(
                    "ADAPTER_CONCURRENCY_LIMIT: process-local Worker limit reached"
                )
            type(self)._process_active += 1

    def _release(self) -> None:
        with self._state_lock:
            type(self)._process_active = max(0, type(self)._process_active - 1)

    def _child_environment(self, run_root: Path) -> dict[str, str]:
        return _sanitized_worker_environment(self._python, run_root=run_root)

    @staticmethod
    def _mark_unexpected_exit(
        run_dir: Path,
        return_code: int,
        *,
        run_root: Path | None = None,
        launch_binding: LaunchAttemptBinding | None = None,
    ) -> None:
        acknowledged_stop_reason: str | None = None
        if run_root is not None or launch_binding is not None:
            if run_root is None or launch_binding is None:
                return
            try:
                if not worker_attempt_started(run_root, run_dir, launch_binding):
                    return
            except WorkerControlError:
                # A stale/corrupt attempt must never let an old reaper mutate a
                # newer attempt's status evidence.
                return
            try:
                try:
                    stop_evidence = read_worker_stop_evidence(
                        run_root, launch_binding
                    )
                    stop_reason = stop_evidence.reason
                    stop_acknowledged = stop_evidence.acknowledged
                except WorkerControlError as error:
                    if error.code != "WORKER_STOP_UNSUPPORTED":
                        raise
                    cancel_evidence = read_worker_cancel_evidence(
                        run_root, launch_binding
                    )
                    stop_reason = cancel_evidence.reason
                    stop_acknowledged = cancel_evidence.acknowledged
                attempt_evidence = read_worker_attempt_evidence(
                    run_root, run_dir, launch_binding
                )
                if (
                    stop_acknowledged
                    and attempt_evidence is not None
                    and attempt_evidence.heartbeat_state == "stopped"
                ):
                    acknowledged_stop_reason = stop_reason
            except (FileNotFoundError, WorkerControlError):
                acknowledged_stop_reason = None
        status_path = run_dir / "status.json"

        def write_terminal(*, stop_reason: str | None) -> None:
            try:
                value = _read_json_file(
                    status_path, code="WORKER_STATUS_INVALID"
                )
                # Natural or already-finalized terminal evidence always wins,
                # even if a later process exit code is nonzero.
                if value.get("status") in {"succeeded", "failed", "cancelled"}:
                    return
                if stop_reason == "user_requested":
                    update = {
                        "status": "cancelled",
                        "stage": "cancelled",
                        "message": "FWI Worker cancellation completed",
                    }
                elif stop_reason == "wall_time_exceeded":
                    update = {
                        "status": "failed",
                        "stage": "failed",
                        "message": "FWI Worker wall time exceeded",
                        "failure_code": "WALL_TIME_EXCEEDED",
                    }
                else:
                    if (
                        run_root is None
                        or launch_binding is None
                        or return_code in {0, 75, 76}
                    ):
                        return
                    update = {
                        "status": "failed",
                        "stage": "worker_exit",
                        "message": f"FWI worker exited with code {return_code}",
                    }
                post_status = {**value, **update, "updated_at": _utc_now()}
                if stop_reason is None:
                    try:
                        existing_exit = read_worker_exit_evidence(
                            run_root, run_dir, launch_binding
                        )
                    except WorkerControlError as error:
                        if error.code != "WORKER_EXIT_MISSING":
                            raise
                        record_worker_exit(
                            run_root,
                            run_dir,
                            launch_binding,
                            return_code=return_code,
                            pre_status=value,
                            post_status=post_status,
                            observed_at=post_status["updated_at"],
                        )
                    else:
                        if existing_exit.return_code != return_code:
                            return
                        post_status = {
                            **value,
                            "status": "failed",
                            "stage": "worker_exit",
                            "message": (
                                "FWI worker exited with code "
                                f"{existing_exit.return_code}"
                            ),
                            "updated_at": existing_exit.observed_at,
                        }
                        if (
                            _sha256_document(post_status)
                            != existing_exit.post_status_hash
                        ):
                            return
                    # The evidence API owns the receipt/status arbitration and
                    # has already installed this exact post document.
                    return
                _atomic_write_json(status_path, post_status)
            except Exception:
                # Status is Worker evidence, while SQLite remains task truth.  A
                # malformed/missing file is surfaced by status() rather than
                # being replaced with invented success evidence.
                return

        if acknowledged_stop_reason is not None:
            assert run_root is not None and launch_binding is not None
            try:
                with hold_idle_execution_fence(run_root, launch_binding):
                    write_terminal(stop_reason=acknowledged_stop_reason)
            except WorkerControlError:
                # A descendant or still-exiting Worker retained the exact
                # execution fence.  Leave finalization to a later observer.
                return
        else:
            write_terminal(stop_reason=None)

    @staticmethod
    def _terminate_before_ready(process: subprocess.Popen[Any]) -> bool:
        """Return true only after proving the pre-ready child has exited."""

        try:
            if process.poll() is not None:
                return True
            process.terminate()
            try:
                process.wait(timeout=5.0)
                return True
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5.0)
                return True
        except Exception:
            try:
                return process.poll() is not None
            except Exception:
                return False

    def launch(
        self,
        *,
        command: str,
        config_path: Path,
        run_dir: Path,
        run_root: Path,
        wall_time_seconds: int = 86_400,
        checkpoint_capable: bool = False,
    ) -> int:
        """Expose only explicitly stopped failures as retry candidates."""

        try:
            return self._launch_once(
                command=command,
                config_path=config_path,
                run_dir=run_dir,
                run_root=run_root,
                wall_time_seconds=wall_time_seconds,
                checkpoint_capable=checkpoint_capable,
            )
        except _AdapterLaunchAmbiguous:
            raise
        except AdapterUnavailable as error:
            if error.code == "WORKER_LAUNCH_FAILED":
                raise _AdapterLaunchStopped(str(error)) from error
            raise
        except OSError as error:
            raise _AdapterLaunchStopped(
                f"WORKER_LAUNCH_FAILED: {type(error).__name__}"
            ) from error

    def _launch_once(
        self,
        *,
        command: str,
        config_path: Path,
        run_dir: Path,
        run_root: Path,
        wall_time_seconds: int = 86_400,
        checkpoint_capable: bool = False,
    ) -> int:
        if command != "invert":
            raise AdapterValidationError(
                "TASK_TYPE_UNSUPPORTED_IN_P1",
                ["the standard P1 Adapter launches only inversion"],
            )
        if type(checkpoint_capable) is not bool:
            raise AdapterValidationError(
                "CHECKPOINT_CAPABILITY_INVALID",
                ["checkpoint capability must be an immutable boolean"],
            )
        self._reserve()
        lease: ParentLaunchLease | None = None
        process: subprocess.Popen[Any] | None = None
        launch_binding: LaunchAttemptBinding | None = None
        log_path = run_dir / "run.log"
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_APPEND
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        descriptor = -1
        directory_descriptor = -1
        try:
            lease = ParentLaunchLease.acquire(
                run_root,
                run_dir,
                max_active=self._max_active,
            )
            candidate, directory_descriptor = _safe_parent_fd(log_path)
            descriptor = os.open(
                candidate.name, flags, 0o600, dir_fd=directory_descriptor
            )
            argv = [
                str(self._python),
                "-m",
                "worker_launch_bootstrap",
                "--command",
                "invert",
                "--config",
                str(config_path),
                "--run-dir",
                str(run_dir),
                "--run-root",
                str(run_root),
                "--wall-time-seconds",
                str(wall_time_seconds),
                *(
                    ["--checkpoint-after-first-update"]
                    if checkpoint_capable
                    else []
                ),
                *lease.child_arguments,
            ]
            process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=descriptor,
                stderr=subprocess.STDOUT,
                cwd=str(self._project_root),
                env=self._child_environment(run_root),
                close_fds=True,
                pass_fds=lease.pass_fds,
                shell=False,
            )
            try:
                lease.mark_spawned(int(process.pid))
            except Exception as error:
                if not self._terminate_before_ready(process):
                    lease.close_parent()
                    lease = None
                    raise _AdapterLaunchAmbiguous(
                        "SUBMISSION_LAUNCH_PENDING: spawned Worker could not be stopped safely"
                    ) from error
                raise
            launch_binding = lease.binding
            lease.close_parent()
            lease = None
        except Exception as error:
            try:
                if lease is not None:
                    try:
                        lease.abort()
                    except Exception:
                        # abort() closes both parent descriptors in its own
                        # finally block.  Preserve the initiating failure while
                        # always releasing the cheap process-local reservation.
                        pass
            finally:
                self._release()
            if isinstance(error, WorkerControlError):
                raise AdapterUnavailable(error.code) from error
            raise
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if directory_descriptor >= 0:
                os.close(directory_descriptor)

        if process is None:
            self._release()
            raise AdapterUnavailable(
                "WORKER_LAUNCH_FAILED: subprocess did not return a process"
            )
        if launch_binding is None:
            stopped = self._terminate_before_ready(process)
            self._release()
            if not stopped:
                raise _AdapterLaunchAmbiguous(
                    "SUBMISSION_LAUNCH_PENDING: Worker identity is uncertain"
                )
            raise AdapterUnavailable(
                "WORKER_LAUNCH_FAILED: staged launch binding was lost"
            )

        deadline = time.monotonic() + self._start_timeout_seconds
        ready = False
        while True:
            try:
                if worker_attempt_started(run_root, run_dir, launch_binding):
                    ready = True
                    break
            except WorkerControlError as error:
                stopped = self._terminate_before_ready(process)
                self._release()
                if not stopped:
                    raise _AdapterLaunchAmbiguous(
                        "SUBMISSION_LAUNCH_PENDING: Worker evidence is uncertain"
                    ) from error
                raise AdapterUnavailable(error.code) from error
            return_code = process.poll()
            if return_code is not None:
                self._mark_unexpected_exit(run_dir, int(return_code))
                self._release()
                raise AdapterUnavailable(
                    "WORKER_LAUNCH_FAILED: Worker exited before fenced readiness"
                )
            if time.monotonic() >= deadline:
                break
            time.sleep(0.02)

        def reap() -> None:
            try:
                return_code = process.wait()
                self._mark_unexpected_exit(
                    run_dir,
                    return_code,
                    run_root=run_root,
                    launch_binding=launch_binding,
                )
            finally:
                self._release()

        try:
            threading.Thread(target=reap, name="fwi-adapter-reaper", daemon=True).start()
        except Exception as error:
            stopped = self._terminate_before_ready(process)
            self._release()
            if not stopped:
                raise _AdapterLaunchAmbiguous(
                    "SUBMISSION_LAUNCH_PENDING: Worker reaper could not be established"
                ) from error
            raise _AdapterLaunchStopped(
                "WORKER_LAUNCH_FAILED: Worker reaper could not be established"
            ) from error
        if not ready:
            raise _AdapterLaunchAmbiguous(
                "SUBMISSION_LAUNCH_PENDING: Worker readiness is not yet durable"
            )
        return int(process.pid)


def _fixed_worker_probe(*arguments: str) -> dict[str, Any]:
    python = DEFAULT_WORKER_PYTHON
    if not python.is_file() or not os.access(python, os.X_OK):
        raise AdapterUnavailable(
            "WORKER_PYTHON_UNAVAILABLE: fixed FWI Python is not executable"
        )
    if not PROBE_SLOTS.acquire(timeout=5):
        raise AdapterUnavailable(
            "WORKER_PROBE_BUSY: fixed runtime probe capacity is exhausted"
        )
    process: subprocess.Popen[bytes] | None = None
    output = b""
    try:
        process = subprocess.Popen(
            [str(python), "-m", "fwi_worker.adapter_probe", *arguments],
            cwd=PROJECT_ROOT,
            env=_sanitized_worker_environment(python),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            shell=False,
        )
        if process.stdout is None:
            raise OSError("probe stdout pipe is unavailable")
        descriptor = process.stdout.fileno()
        os.set_blocking(descriptor, False)
        chunks: list[bytes] = []
        total = 0
        deadline = time.monotonic() + 60
        with selectors.DefaultSelector() as selector:
            selector.register(descriptor, selectors.EVENT_READ)
            while True:
                remaining_time = deadline - time.monotonic()
                if remaining_time <= 0:
                    raise subprocess.TimeoutExpired(process.args, 60)
                events = selector.select(timeout=remaining_time)
                if not events:
                    raise subprocess.TimeoutExpired(process.args, 60)
                chunk = os.read(
                    descriptor,
                    min(64 * 1024, MAX_PROBE_OUTPUT_BYTES + 1 - total),
                )
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > MAX_PROBE_OUTPUT_BYTES:
                    raise AdapterUnavailable(
                        "WORKER_PROBE_INVALID: probe output is too large"
                    )
        output = b"".join(chunks)
        remaining_time = max(0.001, deadline - time.monotonic())
        return_code = process.wait(timeout=remaining_time)
    except AdapterError:
        raise
    except subprocess.TimeoutExpired as error:
        raise AdapterUnavailable(
            "WORKER_PROBE_TIMEOUT: fixed runtime probe exceeded 60 seconds"
        ) from error
    except (OSError, subprocess.SubprocessError) as error:
        raise AdapterUnavailable(
            "WORKER_PROBE_UNAVAILABLE: fixed runtime probe could not start"
        ) from error
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        if process is not None and process.stdout is not None:
            process.stdout.close()
        PROBE_SLOTS.release()
    if return_code != 0:
        raise AdapterUnavailable(
            "WORKER_PROBE_FAILED: fixed runtime rejected the requested evidence probe"
        )
    try:
        value = json.loads(output.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AdapterUnavailable("WORKER_PROBE_INVALID: probe output is malformed") from error
    if not isinstance(value, dict):
        raise AdapterUnavailable("WORKER_PROBE_INVALID: probe output must be an object")
    return value


def _default_dataset_identity_provider() -> dict[str, Any]:
    value = _fixed_worker_probe("dataset").get("dataset")
    if not isinstance(value, dict):
        raise AdapterUnavailable("WORKER_PROBE_INVALID: dataset evidence is missing")
    return value


def _default_device_validator(device: str) -> dict[str, Any]:
    value = _fixed_worker_probe("runtime", "--device", device).get("device_details")
    if not isinstance(value, dict) or value.get("device") != device:
        raise AdapterUnavailable("WORKER_PROBE_INVALID: device evidence is missing")
    return value


def _git_source_evidence() -> dict[str, Any]:
    source: dict[str, Any] = {"identity_complete": False, "dirty": None}
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        tree = subprocess.run(
            ["git", "rev-parse", "HEAD^{tree}"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        porcelain = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
        if re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", commit):
            source["git_commit"] = commit
        if re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", tree):
            source["git_tree"] = tree
        source["dirty"] = bool(porcelain)
    except (OSError, subprocess.SubprocessError):
        pass
    return source


def _default_fingerprint_factory(
    *,
    algorithm: Mapping[str, str],
    normalized_config_hash: str,
    input_hashes: list[str],
    seed: int,
    device: str,
    device_details: Mapping[str, Any],
) -> dict[str, Any]:
    # The legacy child does not enable deterministic algorithms.  Never copy
    # a potentially different flag from the control process into its evidence.
    deterministic = False
    cudnn_deterministic = False
    known = [
        "The legacy Worker records seed but does not consume it in the numerical path.",
        "Bitwise equality across library, driver, CPU, or GPU versions is not promised.",
        "The environment hash is an installed-package snapshot, not a rebuildable lock.",
    ]
    return {
        "provenance_mode": "development",
        "algorithm": dict(algorithm),
        "adapter_version": ADAPTER_VERSION,
        "source": _git_source_evidence(),
        "environment": {
            "environment_lock_hash": device_details[
                "development_environment_snapshot_hash"
            ]
        },
        "runtime": copy.deepcopy(device_details["runtime"]),
        "seed": seed,
        "hardware": {
            "device": device,
            "device_name": str(device_details.get("device_name") or device),
            "compute_capability": device_details.get("compute_capability"),
        },
        "normalized_config_hash": normalized_config_hash,
        "input_hashes": list(input_hashes),
        "determinism": {
            "requested": False,
            "framework_deterministic": deterministic,
            "flags": {
                "torch_deterministic_algorithms": deterministic,
                "cudnn_deterministic": cudnn_deterministic,
            },
            "known_nondeterminism": known,
        },
    }


class DeepwaveAdapter:
    """Algorithm Adapter v1 for one fixed, registered Marmousi FWI node."""

    def __init__(
        self,
        *,
        run_root: Path | str | None = None,
        launcher: WorkerLauncher | None = None,
        dataset_identity_provider: Callable[[], Mapping[str, Any]] = (
            _default_dataset_identity_provider
        ),
        registry_snapshot_provider: Callable[..., Mapping[str, Any]] | None = None,
        device_validator: Callable[[str], Mapping[str, Any] | None] = (
            _default_device_validator
        ),
        fingerprint_factory: Callable[..., Mapping[str, Any]] = (
            _default_fingerprint_factory
        ),
        clock: Callable[[], str] = _utc_now,
    ) -> None:
        configured = Path(
            run_root
            if run_root is not None
            else os.environ.get("FWI_RUN_ROOT", "/root/fwi-runs")
        )
        # Validation is read-only when the deployment root already exists.
        self._run_root = _validate_run_root(configured, create=False)
        self._launcher = launcher or SafeSubprocessWorkerLauncher()
        if (
            isinstance(self._launcher, SafeSubprocessWorkerLauncher)
            # A venv and the system interpreter can resolve to the same binary
            # while selecting different sys.prefix/site-packages.  Bind the
            # exact configured venv entry path, not only its symlink target.
            and self._launcher.python_executable != DEFAULT_WORKER_PYTHON
        ):
            raise AdapterUnavailable(
                "WORKER_RUNTIME_MISMATCH: Adapter evidence and Worker must use the fixed FWI runtime"
            )
        self._dataset_identity_provider = dataset_identity_provider
        self._registry_snapshot_provider = registry_snapshot_provider
        self._device_validator = device_validator
        self._fingerprint_factory = fingerprint_factory
        self._clock = clock
        self._manifest = load_deepwave_manifest()
        if _sha256_document(self._manifest) != BOUND_MANIFEST_HASH:
            raise AdapterUnavailable(
                "ADAPTER_MANIFEST_MISMATCH: Adapter v1 is not bound to this manifest"
            )

    @staticmethod
    def _validate_algorithm(
        algorithm: Mapping[str, Any], *, allow_historical_managed: bool = False
    ) -> dict[str, str]:
        if not isinstance(algorithm, Mapping) or set(algorithm) != {"id", "version"}:
            raise AdapterValidationError(
                "ALGORITHM_IDENTITY_INVALID",
                ["algorithm must contain only id and version"],
            )
        value = {"id": algorithm.get("id"), "version": algorithm.get("version")}
        expected_versions = (
            SUPPORTED_MANAGED_REQUEST_VERSIONS
            if allow_historical_managed
            else frozenset({ALGORITHM_VERSION})
        )
        if value["id"] != ALGORITHM_ID or value["version"] not in expected_versions:
            raise AdapterValidationError(
                "ALGORITHM_VERSION_UNAVAILABLE",
                [
                    f"Adapter is bound to {ALGORITHM_ID}@{ALGORITHM_VERSION}"
                    if not allow_historical_managed
                    else "Adapter cannot reopen this managed Algorithm version"
                ],
            )
        return value  # type: ignore[return-value]

    def _validate_dataset(
        self,
        dataset: Mapping[str, Any],
        *,
        project_id: str,
        principal_id: str,
        verify_local: bool,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if not isinstance(dataset, Mapping):
            raise AdapterValidationError(
                "DATASET_INVALID", ["dataset must be a DatasetRef object"]
            )
        supplied = copy.deepcopy(dict(dataset))
        errors = schema_errors("dataset-ref.schema.json", supplied)
        if errors:
            raise AdapterValidationError("DATASET_INVALID", errors)
        scope = supplied["access_scope"]
        if (
            scope["project_id"] != project_id
            or principal_id not in scope["principals"]
            or "execute" not in scope["permissions"]
        ):
            raise AdapterValidationError(
                "DATASET_ACCESS_DENIED",
                ["current project/principal must have registered execute access"],
            )
        if (
            supplied["id"] != MODEL_ID
            or supplied["version"] != "1.0.0"
            or supplied["data_type"] != "velocity_model_2d"
        ):
            raise AdapterValidationError(
                "DATASET_IDENTITY_MISMATCH",
                ["P1 Adapter is bound to marmousi_94_288@1.0.0"],
            )
        identity = {
                key: supplied[key]
                for key in ("id", "version", "content_hash", "data_type")
            }
        access_scope = copy.deepcopy(scope)
        if not verify_local:
            return identity, access_scope
        if self._registry_snapshot_provider is None:
            raise AdapterUnavailable(
                "REGISTRY_SNAPSHOT_PROVIDER_REQUIRED: first execution must bind a server-resolved DatasetRef"
            )
        try:
            registered_value = copy.deepcopy(
                dict(
                    self._registry_snapshot_provider(
                        project_id=project_id,
                        principal_id=principal_id,
                        dataset_id=supplied["id"],
                        dataset_version=supplied["version"],
                    )
                )
            )
        except Exception as error:
            raise AdapterUnavailable(
                f"REGISTRY_SNAPSHOT_UNAVAILABLE: {type(error).__name__}"
            ) from error
        registered_errors = schema_errors("dataset-ref.schema.json", registered_value)
        if registered_errors:
            raise AdapterUnavailable(
                "REGISTRY_SNAPSHOT_INVALID: trusted registry value is not a DatasetRef"
            )
        if supplied != registered_value:
            raise AdapterValidationError(
                "DATASET_REGISTRY_MISMATCH",
                ["DatasetRef differs from the server-resolved Registry snapshot"],
            )
        try:
            trusted_value = copy.deepcopy(dict(self._dataset_identity_provider()))
        except Exception as error:
            raise AdapterUnavailable(
                f"DATASET_VERIFICATION_UNAVAILABLE: {type(error).__name__}"
            ) from error
        trusted_errors = schema_errors("dataset-ref.schema.json", trusted_value)
        if trusted_errors:
            raise AdapterUnavailable(
                "DATASET_VERIFICATION_INVALID: trusted identity is not a DatasetRef"
            )
        core_fields = (
            "schema_version",
            "id",
            "version",
            "content_hash",
            "data_type",
            "immutable",
            "metadata",
            "lineage",
            "extensions",
        )
        mismatches = [field for field in core_fields if supplied[field] != trusted_value[field]]
        if mismatches:
            raise AdapterValidationError(
                "DATASET_IDENTITY_MISMATCH",
                ["registered dataset differs from verified local input: " + ", ".join(mismatches)],
            )
        return identity, access_scope

    def _validate_parameters(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(parameters, Mapping):
            raise AdapterValidationError(
                "PARAMETERS_INVALID", ["parameters must be an object"]
            )
        value = copy.deepcopy(dict(parameters))
        errors = sorted(
            self._manifest_parameter_validator().iter_errors(value),
            key=lambda error: (list(error.absolute_path), error.message),
        )
        rendered = [
            "/" + "/".join(str(part) for part in error.absolute_path) + ": " + error.message
            for error in errors
        ]
        if rendered:
            raise AdapterValidationError("PARAMETERS_INVALID", rendered)
        if type(value["seed"]) is not int or not 0 <= value["seed"] <= 2_147_483_647:
            raise AdapterValidationError(
                "PARAMETERS_INVALID", ["seed must be a strict integer in 0..2147483647"]
            )
        if type(value["iterations"]) is not int:
            raise AdapterValidationError(
                "PARAMETERS_INVALID", ["iterations must be a strict integer"]
            )
        if value["preset"] not in {"fwi_smoke", "fwi_demo"}:
            raise AdapterValidationError(
                "TASK_TYPE_UNSUPPORTED_IN_P1",
                ["P1 standard Adapter supports only inversion presets"],
            )
        maximum = self._manifest["parameter_schema"]["properties"]["iterations"][
            "maximum"
        ]
        if not 1 <= value["iterations"] <= maximum:
            raise AdapterValidationError(
                "PARAMETERS_INVALID",
                [f"inversion iterations must be in 1..{maximum}"],
            )
        optimizer = value.get("optimizer")
        if optimizer not in {"adam", "sgd"}:
            raise AdapterValidationError(
                "PARAMETERS_INVALID", ["optimizer must be adam or sgd"]
            )
        learning_rate_milli = value.get("learning_rate_milli")
        bounds = (
            ADAM_LEARNING_RATE_MILLI_RANGE
            if optimizer == "adam"
            else SGD_LEARNING_RATE_MILLI_RANGE
        )
        if (
            type(learning_rate_milli) is not int
            or not bounds[0] <= learning_rate_milli <= bounds[1]
        ):
            raise AdapterValidationError(
                "PARAMETERS_INVALID",
                [
                    f"{optimizer} learning_rate_milli must be a strict integer "
                    f"in {bounds[0]}..{bounds[1]}"
                ],
            )
        return value

    def _manifest_parameter_validator(self) -> Draft7Validator:
        return Draft7Validator(self._manifest["parameter_schema"])

    def _validate_resources(
        self, resources: Mapping[str, Any], *, device: str
    ) -> dict[str, Any]:
        expected = {
            "device",
            "gpu_count",
            "cpu_cores",
            "memory_mb",
            "wall_time_seconds",
        }
        if not isinstance(resources, Mapping) or set(resources) != expected:
            raise AdapterValidationError(
                "RESOURCES_INVALID",
                ["resources must contain exactly the v1 resource fields"],
            )
        value = copy.deepcopy(dict(resources))
        if value["device"] != device:
            raise AdapterValidationError(
                "RESOURCE_DEVICE_MISMATCH",
                ["resource device must equal the parameter device"],
            )
        limits = self._manifest["resource_limits"]
        integer_fields = {
            "gpu_count": (0, limits["max_gpu_count"]),
            "cpu_cores": (1, limits["max_cpu_cores"]),
            "memory_mb": (256, limits["max_memory_mb"]),
            "wall_time_seconds": (1, limits["max_wall_time_seconds"]),
        }
        if value["device"] not in limits["devices"]:
            raise AdapterValidationError(
                "RESOURCE_UNSUPPORTED", ["device is not declared by the manifest"]
            )
        for field, (minimum, maximum) in integer_fields.items():
            item = value[field]
            if type(item) is not int or not minimum <= item <= maximum:
                raise AdapterValidationError(
                    "RESOURCE_LIMIT_EXCEEDED",
                    [f"{field} must be a strict integer in {minimum}..{maximum}"],
                )
        expected_gpu = 1 if value["device"] == "cuda" else 0
        if value["gpu_count"] != expected_gpu:
            raise AdapterValidationError(
                "RESOURCE_DEVICE_MISMATCH",
                [f"{value['device']} requires gpu_count={expected_gpu}"],
            )
        return value

    def _validate_request(
        self,
        *,
        project_id: str,
        principal_id: str,
        algorithm: Mapping[str, Any],
        dataset: Mapping[str, Any],
        task_type: str,
        parameters: Mapping[str, Any],
        resources: Mapping[str, Any],
        verify_runtime: bool,
        allow_historical_managed: bool = False,
    ) -> AdapterValidation:
        for name, value in (("project_id", project_id), ("principal_id", principal_id)):
            if not isinstance(value, str) or OPAQUE_ID.fullmatch(value) is None:
                raise AdapterValidationError(
                    "AUTH_SCOPE_INVALID", [f"{name} must be a v1 opaque identifier"]
                )
        algorithm_value = self._validate_algorithm(
            algorithm,
            allow_historical_managed=allow_historical_managed,
        )
        if task_type != "acoustic_fwi_2d":
            raise AdapterValidationError(
                "TASK_TYPE_UNSUPPORTED_IN_P1",
                ["P1 standard Adapter supports only acoustic_fwi_2d"],
            )
        dataset_identity, dataset_access_scope = self._validate_dataset(
            dataset,
            project_id=project_id,
            principal_id=principal_id,
            verify_local=verify_runtime,
        )
        parameter_value = self._validate_parameters(parameters)
        resource_value = self._validate_resources(
            resources, device=parameter_value["device"]
        )
        device_details: dict[str, Any] = {}
        if verify_runtime:
            try:
                details = self._device_validator(parameter_value["device"])
            except Exception as error:
                raise AdapterUnavailable(
                    f"DEVICE_UNAVAILABLE: {type(error).__name__}: {error}"
                ) from error
            device_details = copy.deepcopy(dict(details or {}))

        # Keep the control-plane validator importable without the numerical
        # environment.  The six public parameters are the only caller-
        # controlled Worker config.  The hash-bound plan carries the learning
        # rate as integer milli-units; only this private boundary converts it
        # to the finite float consumed by PyTorch.  Other numerical defaults
        # remain fixed by versioned adapter/Worker source.
        normalized_material = {
            # Every retained managed version is an exact Algorithm/Adapter
            # pair.  Reconstruct historical request hashes from that immutable
            # pair; only new submissions pass the current-version-only path.
            "adapter_version": algorithm_value["version"],
            "project_id": project_id,
            "principal_id": principal_id,
            "algorithm": algorithm_value,
            "dataset": dataset_identity,
            "dataset_access_scope": dataset_access_scope,
            "task_type": task_type,
            "parameters": parameter_value,
        }
        normalized_hash = _sha256_document(normalized_material)
        worker_config = {
            "model_id": MODEL_ID,
            "preset": parameter_value["preset"],
            "device": parameter_value["device"],
            "iterations": parameter_value["iterations"],
            "seed": parameter_value["seed"],
            "optimizer": parameter_value["optimizer"],
            "learning_rate": parameter_value["learning_rate_milli"] / 1000.0,
            "gradient_clip_quantile": GRADIENT_CLIP_QUANTILE,
        }
        return AdapterValidation(
            project_id=project_id,
            principal_id=principal_id,
            algorithm=algorithm_value,
            dataset=dataset_identity,
            dataset_access_scope=dataset_access_scope,
            task_type=task_type,
            parameters=parameter_value,
            resources=resource_value,
            command="invert",
            worker_config=worker_config,
            normalized_config_hash=normalized_hash,
            device_details=device_details,
        )

    def validate(
        self,
        *,
        project_id: str,
        principal_id: str,
        algorithm: Mapping[str, Any],
        dataset: Mapping[str, Any],
        task_type: str,
        parameters: Mapping[str, Any],
        resources: Mapping[str, Any],
    ) -> AdapterValidation:
        validated = self._validate_request(
            project_id=project_id,
            principal_id=principal_id,
            algorithm=algorithm,
            dataset=dataset,
            task_type=task_type,
            parameters=parameters,
            resources=resources,
            verify_runtime=True,
        )
        fingerprint = self._validate_fingerprint(
            self._fingerprint_factory(
                algorithm=validated.algorithm,
                normalized_config_hash=validated.normalized_config_hash,
                input_hashes=[validated.dataset["content_hash"]],
                seed=validated.parameters["seed"],
                device=validated.parameters["device"],
                device_details=validated.device_details,
            ),
            validated=validated,
        )
        return AdapterValidation(
            **{
                **validated.as_dict(),
                "fingerprint": fingerprint,
            }
        )

    def _validate_managed_runtime_request(
        self,
        *,
        project_id: str,
        principal_id: str,
        algorithm: Mapping[str, Any],
        dataset: Mapping[str, Any],
        task_type: str,
        parameters: Mapping[str, Any],
        resources: Mapping[str, Any],
    ) -> AdapterValidation:
        """Revalidate a retained managed request without minting new identity."""

        return self._validate_request(
            project_id=project_id,
            principal_id=principal_id,
            algorithm=algorithm,
            dataset=dataset,
            task_type=task_type,
            parameters=parameters,
            resources=resources,
            verify_runtime=True,
            allow_historical_managed=True,
        )

    def estimate(self, **kwargs: Any) -> AdapterEstimate:
        validated = self.validate(**kwargs)
        return AdapterEstimate(
            normalized_config_hash=validated.normalized_config_hash,
            requested_resources=copy.deepcopy(validated.resources),
            policy_limits=copy.deepcopy(self._manifest["resource_limits"]),
            estimated_wall_time_seconds=None,
            basis="manifest_policy_limits_only",
            limitations=(
                "No calibrated runtime model is available in P1.2a.",
                "CPU, memory, and wall-time values are policy caps, not OS isolation guarantees.",
            ),
        )

    @staticmethod
    def _validate_submit_identity(
        *, task_id: str, node_id: str, plan_hash: str, idempotency_key: str
    ) -> None:
        if not isinstance(task_id, str) or OPAQUE_ID.fullmatch(task_id) is None:
            raise AdapterValidationError(
                "TASK_ID_INVALID", ["task_id must be a v1 opaque identifier"]
            )
        if not isinstance(node_id, str) or IDENTIFIER.fullmatch(node_id) is None:
            raise AdapterValidationError(
                "NODE_ID_INVALID", ["node_id must be a v1 identifier"]
            )
        if not isinstance(plan_hash, str) or PLAN_HASH.fullmatch(plan_hash) is None:
            raise AdapterValidationError(
                "PLAN_HASH_INVALID", ["plan_hash must be a lowercase SHA-256 identity"]
            )
        if (
            not isinstance(idempotency_key, str)
            or NODE_IDEMPOTENCY_KEY.fullmatch(idempotency_key) is None
        ):
            raise AdapterValidationError(
                "IDEMPOTENCY_KEY_INVALID",
                ["idempotency_key must match the PlanGraph node-key contract"],
            )

    def _control_paths(self) -> tuple[Path, Path]:
        root = _validate_run_root(self._run_root, create=True)
        control = _ensure_private_directory(root / CONTROL_DIRECTORY)
        submissions = _ensure_private_directory(control / "submissions")
        locks = _ensure_private_directory(control / "locks")
        return submissions, locks

    @staticmethod
    def _submission_id(task_id: str, plan_hash: str, idempotency_key: str) -> str:
        material = _stable_json_bytes(
            {
                "task_id": task_id,
                "plan_hash": plan_hash,
                "idempotency_key": idempotency_key,
            }
        )
        digest = hashlib.sha256(material).hexdigest()
        return f"submission-{digest}"

    def _job_id(self, submission_id: str, created_at: str) -> str:
        parsed = _parse_timestamp(created_at, code="CLOCK_INVALID")
        stamp = parsed.strftime("%Y%m%dT%H%M%SZ")
        suffix = hashlib.sha256(submission_id.encode("utf-8")).hexdigest()[:12]
        return f"fwi-{stamp}-{suffix}"

    @staticmethod
    def _retry_job_id(
        submission_id: str, authorized_at: str, private_proof_hash: str
    ) -> str:
        parsed = _parse_timestamp(authorized_at, code="CLOCK_INVALID")
        if PLAN_HASH.fullmatch(private_proof_hash) is None:
            raise AdapterHandleError(
                "ADAPTER_RETRY_INVALID: private proof hash is invalid"
            )
        stamp = parsed.strftime("%Y%m%dT%H%M%SZ")
        suffix = hashlib.sha256(
            _stable_json_bytes(
                {
                    "submission_id": submission_id,
                    "attempt_number": 2,
                    "private_proof_hash": private_proof_hash,
                }
            )
        ).hexdigest()[:12]
        return f"fwi-{stamp}-{suffix}"

    def _expected_job_id(self, record: Mapping[str, Any]) -> str:
        if record.get("schema_version") == "1.0.0":
            return self._job_id(record["submission_id"], record["created_at"])
        binding = binding_from_submission_record(record)
        if binding.attempt_number == 1:
            return self._job_id(record["submission_id"], record["created_at"])
        authorization = record.get("retry_authorization")
        if not isinstance(authorization, Mapping):
            raise AdapterHandleError(
                "ADAPTER_SUBMISSION_INVALID: retry authorization is missing"
            )
        return self._retry_job_id(
            record["submission_id"],
            authorization.get("authorized_at"),
            authorization.get("private_proof_hash"),
        )

    @staticmethod
    def _failure_proof_payload(
        record: Mapping[str, Any], evidence: WorkerAttemptEvidence
    ) -> dict[str, Any]:
        return {
            "schema_version": "1.0.0",
            "failure_kind": "pre_running_launch_failure",
            "submission_id": record["submission_id"],
            "attempt_id": evidence.attempt_id,
            "attempt_number": evidence.attempt_number,
            "job_id": evidence.job_id,
            "request_hash": evidence.request_hash,
            "binding_hash": evidence.binding_hash,
            "ticket_record_hash": evidence.ticket_record_hash,
        }

    @staticmethod
    def _failure_proof(
        record: Mapping[str, Any], evidence: WorkerAttemptEvidence
    ) -> dict[str, Any]:
        payload = DeepwaveAdapter._failure_proof_payload(record, evidence)
        return {**payload, "proof_hash": _sha256_document(payload)}

    @staticmethod
    def _request_payload(
        *,
        submission_id: str,
        task_id: str,
        node_id: str,
        plan_hash: str,
        idempotency_key: str,
        validated: AdapterValidation,
    ) -> dict[str, Any]:
        return {
            "submission_id": submission_id,
            "task_id": task_id,
            "node_id": node_id,
            "plan_hash": plan_hash,
            "idempotency_key": idempotency_key,
            "project_id": validated.project_id,
            "principal_id": validated.principal_id,
            "algorithm": validated.algorithm,
            "dataset": validated.dataset,
            "dataset_access_scope": validated.dataset_access_scope,
            "task_type": validated.task_type,
            "parameters": validated.parameters,
            "resources": validated.resources,
            "normalized_config_hash": validated.normalized_config_hash,
        }

    @staticmethod
    def _record_request_payload(record: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: copy.deepcopy(record[key])
            for key in (
                "submission_id",
                "task_id",
                "node_id",
                "plan_hash",
                "idempotency_key",
                "project_id",
                "principal_id",
                "algorithm",
                "dataset",
                "dataset_access_scope",
                "task_type",
                "parameters",
                "resources",
                "normalized_config_hash",
            )
        }

    def _retry_request_material(
        self,
        *,
        task_id: str,
        node_id: str,
        plan_hash: str,
        idempotency_key: str,
        project_id: str,
        principal_id: str,
        algorithm: Mapping[str, Any],
        dataset: Mapping[str, Any],
        task_type: str,
        parameters: Mapping[str, Any],
        resources: Mapping[str, Any],
    ) -> tuple[
        AdapterValidation,
        str,
        dict[str, Any],
        str,
        Path,
        Path,
    ]:
        self._validate_submit_identity(
            task_id=task_id,
            node_id=node_id,
            plan_hash=plan_hash,
            idempotency_key=idempotency_key,
        )
        validated = self._validate_request(
            project_id=project_id,
            principal_id=principal_id,
            algorithm=algorithm,
            dataset=dataset,
            task_type=task_type,
            parameters=parameters,
            resources=resources,
            verify_runtime=False,
            allow_historical_managed=True,
        )
        submission_id = self._submission_id(task_id, plan_hash, idempotency_key)
        request_payload = self._request_payload(
            submission_id=submission_id,
            task_id=task_id,
            node_id=node_id,
            plan_hash=plan_hash,
            idempotency_key=idempotency_key,
            validated=validated,
        )
        request_hash = _sha256_document(request_payload)
        index_name = submission_id.removeprefix("submission-") + ".json"
        control = self._run_root / CONTROL_DIRECTORY
        return (
            validated,
            submission_id,
            request_payload,
            request_hash,
            control / "submissions" / index_name,
            control / "locks" / (index_name + ".lock"),
        )

    @staticmethod
    def _handle_from_record(record: Mapping[str, Any]) -> AdapterHandle:
        return AdapterHandle(
            submission_id=record["submission_id"],
            task_id=record["task_id"],
            node_id=record["node_id"],
            job_id=record["job_id"],
            idempotency_key=record["idempotency_key"],
            plan_hash=record["plan_hash"],
            request_hash=record["request_hash"],
            algorithm=copy.deepcopy(record["algorithm"]),
            fingerprint=copy.deepcopy(record["fingerprint"]),
            adapter_version=record["adapter_version"],
        )

    @staticmethod
    def _record_integrity_payload(record: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: copy.deepcopy(value)
            for key, value in record.items()
            if key != "record_hash"
        }

    @staticmethod
    def _write_submission(path: Path, record: dict[str, Any]) -> None:
        record["record_hash"] = _sha256_document(
            DeepwaveAdapter._record_integrity_payload(record)
        )
        _atomic_write_json(path, record)

    @staticmethod
    def _validate_failure_document(
        failure: Any, binding: LaunchAttemptBinding
    ) -> None:
        required = {
            "schema_version",
            "failure_kind",
            "submission_id",
            "attempt_id",
            "attempt_number",
            "job_id",
            "request_hash",
            "binding_hash",
            "ticket_record_hash",
            "proof_hash",
        }
        if not isinstance(failure, Mapping) or set(failure) != required:
            raise AdapterHandleError(
                "ADAPTER_SUBMISSION_INVALID: launch failure proof is invalid"
            )
        payload = {key: copy.deepcopy(failure[key]) for key in required - {"proof_hash"}}
        if (
            failure.get("schema_version") != "1.0.0"
            or failure.get("failure_kind") != "pre_running_launch_failure"
            or failure.get("submission_id") != binding.submission_id
            or failure.get("attempt_id") != binding.attempt_id
            or failure.get("attempt_number") != binding.attempt_number
            or failure.get("job_id") != binding.job_id
            or failure.get("request_hash") != binding.request_hash
            or failure.get("binding_hash") != binding.binding_hash
            or PLAN_HASH.fullmatch(str(failure.get("ticket_record_hash"))) is None
            or _sha256_document(payload) != failure.get("proof_hash")
        ):
            raise AdapterHandleError(
                "ADAPTER_SUBMISSION_INVALID: launch failure proof changed"
            )

    @staticmethod
    def _validate_retry_authorization_document(
        value: Any, *, expected_failure_kind: str = "pre_running_launch_failure"
    ) -> dict[str, Any]:
        required = {
            "schema_version",
            "intent_id",
            "previous_attempt_id",
            "previous_observation_sequence",
            "failure_kind",
            "private_proof_hash",
            "next_attempt_number",
            "authorized_at",
        }
        if not isinstance(value, Mapping) or set(value) != required:
            raise AdapterHandleError(
                "ADAPTER_SUBMISSION_INVALID: retry authorization is invalid"
            )
        result = copy.deepcopy(dict(value))
        if (
            result.get("schema_version") != "1.0.0"
            or not isinstance(result.get("intent_id"), str)
            or OPAQUE_ID.fullmatch(result["intent_id"]) is None
            or not isinstance(result.get("previous_attempt_id"), str)
            or MANAGED_ATTEMPT_ID.fullmatch(result["previous_attempt_id"]) is None
            or type(result.get("previous_observation_sequence")) is not int
            or result["previous_observation_sequence"] < 1
            or result.get("failure_kind") != expected_failure_kind
            or not isinstance(result.get("private_proof_hash"), str)
            or PLAN_HASH.fullmatch(result["private_proof_hash"]) is None
            or result.get("next_attempt_number") != 2
        ):
            raise AdapterHandleError(
                "ADAPTER_SUBMISSION_INVALID: retry authorization fields changed"
            )
        _parse_timestamp(result.get("authorized_at"), code="ADAPTER_RETRY_INVALID")
        return result

    @staticmethod
    def _validate_worker_exit_document(
        evidence: Any, binding: LaunchAttemptBinding
    ) -> dict[str, Any]:
        required = {
            "schema_version",
            "submission_id",
            "attempt_id",
            "attempt_number",
            "job_id",
            "request_hash",
            "binding_hash",
            "created_at",
            "ticket_record_hash",
            "ready_record_hash",
            "heartbeat_sequence",
            "heartbeat_state",
            "heartbeat_record_hash",
            "pre_status_hash",
            "post_status_hash",
            "return_code",
            "observed_at",
            "record_hash",
        }
        if not isinstance(evidence, Mapping) or set(evidence) != required:
            raise AdapterHandleError(
                "ADAPTER_SUBMISSION_INVALID: Worker exit proof is invalid"
            )
        result = copy.deepcopy(dict(evidence))
        payload = {key: result[key] for key in required - {"record_hash"}}
        if (
            result.get("schema_version") != "1.0.0"
            or result.get("submission_id") != binding.submission_id
            or result.get("attempt_id") != binding.attempt_id
            or result.get("attempt_number") != binding.attempt_number
            or result.get("job_id") != binding.job_id
            or result.get("request_hash") != binding.request_hash
            or result.get("binding_hash") != binding.binding_hash
            or result.get("created_at") != binding.created_at
            or any(
                PLAN_HASH.fullmatch(str(result.get(field))) is None
                for field in (
                    "ticket_record_hash",
                    "ready_record_hash",
                    "heartbeat_record_hash",
                    "pre_status_hash",
                    "post_status_hash",
                    "record_hash",
                )
            )
            or type(result.get("heartbeat_sequence")) is not int
            or result["heartbeat_sequence"] < 1
            or result.get("heartbeat_state") != "running"
            or type(result.get("return_code")) is not int
            or result["return_code"] in {0, 75, 76}
            or _sha256_document(payload) != result["record_hash"]
        ):
            raise AdapterHandleError(
                "ADAPTER_SUBMISSION_INVALID: Worker exit proof changed"
            )
        _parse_timestamp(result.get("observed_at"), code="ADAPTER_RETRY_INVALID")
        return result

    @staticmethod
    def _validate_retry_exhaustion_cleanup_document(
        value: Any,
    ) -> dict[str, Any]:
        """Validate the path-free Store token used only by exhausted purge."""

        common = {
            "schema_version",
            "purge_id",
            "intent_id",
            "task_id",
            "project_id",
            "principal_id",
            "approval_id",
            "attempt_id",
            "attempt_number",
            "observation_sequence",
            "evidence",
            "evidence_hash",
            "private_schema_version",
            "private_proof_hash",
            "failure_kind",
            "previous_attempt_id",
            "previous_observation_sequence",
            "previous_private_proof_hash",
            "retry_reserved_at",
            "terminal_event_sequence",
            "terminal_event_hash",
            "exhausted_at",
            "proof_hash",
        }
        if not isinstance(value, Mapping):
            raise AdapterPurgeError(
                "WORKER_RETRY_EXHAUSTION_PURGE_INVALID: cleanup proof is invalid"
            )
        result = copy.deepcopy(dict(value))
        schema_version = result.get("schema_version")
        worker_exit_lineage = schema_version == "1.1.0"
        required = common | (
            {"previous_failure_kind", "previous_private_schema_version"}
            if worker_exit_lineage
            else set()
        )
        if schema_version not in {"1.0.0", "1.1.0"} or set(result) != required:
            raise AdapterPurgeError(
                "WORKER_RETRY_EXHAUSTION_PURGE_INVALID: cleanup proof is invalid"
            )
        payload = {
            key: copy.deepcopy(result[key])
            for key in required - {"proof_hash"}
        }
        evidence = result.get("evidence")
        ticket = evidence.get("ticket") if isinstance(evidence, Mapping) else None
        if (
            any(
                not isinstance(result.get(key), str)
                or OPAQUE_ID.fullmatch(result[key]) is None
                for key in (
                    "purge_id",
                    "intent_id",
                    "task_id",
                    "project_id",
                    "principal_id",
                    "approval_id",
                )
            )
            or not isinstance(result.get("attempt_id"), str)
            or MANAGED_ATTEMPT_ID.fullmatch(result["attempt_id"]) is None
            or result.get("attempt_number") != 2
            or type(result.get("observation_sequence")) is not int
            or result["observation_sequence"] < 1
            or not isinstance(evidence, Mapping)
            or not isinstance(ticket, Mapping)
            or evidence.get("attempt_id") != result["attempt_id"]
            or evidence.get("attempt_number") != 2
            or ticket.get("state") != "failed"
            or ticket.get("worker_pid") is not None
            or evidence.get("ready") is not None
            or evidence.get("heartbeat") is not None
            or not isinstance(result.get("evidence_hash"), str)
            or PLAN_HASH.fullmatch(result["evidence_hash"]) is None
            or _sha256_document(evidence) != result["evidence_hash"]
            or result.get("private_schema_version")
            != ("1.3.0" if worker_exit_lineage else "1.2.0")
            or not isinstance(result.get("private_proof_hash"), str)
            or PLAN_HASH.fullmatch(result["private_proof_hash"]) is None
            or result.get("failure_kind") != "pre_running_launch_failure"
            or not isinstance(result.get("previous_attempt_id"), str)
            or MANAGED_ATTEMPT_ID.fullmatch(result["previous_attempt_id"]) is None
            or type(result.get("previous_observation_sequence")) is not int
            or result["previous_observation_sequence"] < 1
            or not isinstance(result.get("previous_private_proof_hash"), str)
            or PLAN_HASH.fullmatch(result["previous_private_proof_hash"]) is None
            or type(result.get("terminal_event_sequence")) is not int
            or result["terminal_event_sequence"] < 1
            or not isinstance(result.get("terminal_event_hash"), str)
            or PLAN_HASH.fullmatch(result["terminal_event_hash"]) is None
            or not isinstance(result.get("proof_hash"), str)
            or PLAN_HASH.fullmatch(result["proof_hash"]) is None
            or _sha256_document(payload) != result["proof_hash"]
        ):
            raise AdapterPurgeError(
                "WORKER_RETRY_EXHAUSTION_PURGE_INVALID: cleanup proof changed"
            )
        if worker_exit_lineage:
            if (
                result.get("previous_failure_kind") != "worker_exit"
                or result.get("previous_private_schema_version")
                not in {"1.1.0", "1.2.0"}
            ):
                raise AdapterPurgeError(
                    "WORKER_RETRY_EXHAUSTION_PURGE_INVALID: cleanup lineage changed"
                )
        failure_payload = {
            "schema_version": "1.0.0",
            "failure_kind": "pre_running_launch_failure",
            "submission_id": evidence.get("submission_id"),
            "attempt_id": evidence.get("attempt_id"),
            "attempt_number": evidence.get("attempt_number"),
            "job_id": evidence.get("job_id"),
            "request_hash": evidence.get("request_hash"),
            "binding_hash": evidence.get("binding_hash"),
            "ticket_record_hash": ticket.get("record_hash"),
        }
        if _sha256_document(failure_payload) != result["private_proof_hash"]:
            raise AdapterPurgeError(
                "WORKER_RETRY_EXHAUSTION_PURGE_INVALID: private proof changed"
            )
        _parse_timestamp(
            result.get("retry_reserved_at"), code="ADAPTER_PURGE_INVALID"
        )
        _parse_timestamp(result.get("exhausted_at"), code="ADAPTER_PURGE_INVALID")
        return result

    @staticmethod
    def _read_submission(path: Path) -> dict[str, Any]:
        try:
            value = _read_json_file(
                path, code="ADAPTER_SUBMISSION_INVALID", private=True
            )
        except AdapterStatusError as error:
            raise AdapterHandleError(str(error)) from error
        required = {
            "schema_version",
            "submission_id",
            "task_id",
            "node_id",
            "job_id",
            "idempotency_key",
            "project_id",
            "principal_id",
            "request_hash",
            "plan_hash",
            "algorithm",
            "adapter_version",
            "dataset",
            "dataset_access_scope",
            "task_type",
            "parameters",
            "resources",
            "worker_config",
            "normalized_config_hash",
            "fingerprint",
            "created_at",
            "launch_state",
            "record_hash",
        }
        schema_version = value.get("schema_version")
        if schema_version in {"1.1.0", "1.2.0", "1.3.0"}:
            required.add("launch_attempt")
        if schema_version in {"1.2.0", "1.3.0"}:
            required.add("attempt_history")
            if "retry_authorization" in value:
                required.add("retry_authorization")
            if "launch_failure" in value:
                required.add("launch_failure")
        launch_state = value.get("launch_state")
        if launch_state in {"purging", "purged"}:
            required.add("purge_id")
        if set(value) != required:
            raise AdapterHandleError(
                "ADAPTER_SUBMISSION_INVALID: private record fields are inconsistent"
            )
        if (
            schema_version not in {"1.0.0", "1.1.0", "1.2.0", "1.3.0"}
            or not is_supported_receipt_binding(
                value["algorithm"],
                value["adapter_version"],
                value["fingerprint"],
            )
        ):
            raise AdapterHandleError(
                "ADAPTER_SUBMISSION_INVALID: private record version is unsupported"
            )
        if schema_version in {"1.1.0", "1.2.0", "1.3.0"}:
            try:
                binding = binding_from_submission_record(value)
            except WorkerControlError as error:
                raise AdapterHandleError(
                    "ADAPTER_SUBMISSION_INVALID: launch attempt binding is invalid"
                ) from error
            if schema_version == "1.1.0" and binding.attempt_number != 1:
                raise AdapterHandleError(
                    "ADAPTER_SUBMISSION_INVALID: legacy managed receipt has a retry"
                )
            if schema_version == "1.2.0":
                history = value.get("attempt_history")
                if not isinstance(history, list) or len(history) != binding.attempt_number - 1:
                    raise AdapterHandleError(
                        "ADAPTER_SUBMISSION_INVALID: retry history is not contiguous"
                    )
                if binding.attempt_number not in {1, 2}:
                    raise AdapterHandleError(
                        "ADAPTER_SUBMISSION_INVALID: retry budget was exceeded"
                    )
                if binding.attempt_number == 1:
                    if "retry_authorization" in value or history:
                        raise AdapterHandleError(
                            "ADAPTER_SUBMISSION_INVALID: first attempt has retry state"
                        )
                else:
                    authorization = DeepwaveAdapter._validate_retry_authorization_document(
                        value.get("retry_authorization")
                    )
                    if len(history) != 1 or set(history[0]) != {
                        "submission_id",
                        "job_id",
                        "request_hash",
                        "created_at",
                        "launch_attempt",
                        "launch_failure",
                        "retired_at",
                    }:
                        raise AdapterHandleError(
                            "ADAPTER_SUBMISSION_INVALID: prior attempt history is invalid"
                        )
                    try:
                        prior = binding_from_submission_record(history[0])
                    except WorkerControlError as error:
                        raise AdapterHandleError(
                            "ADAPTER_SUBMISSION_INVALID: prior attempt binding is invalid"
                        ) from error
                    DeepwaveAdapter._validate_failure_document(
                        history[0]["launch_failure"], prior
                    )
                    _parse_timestamp(
                        history[0]["retired_at"], code="ADAPTER_RETRY_INVALID"
                    )
                    if (
                        prior.submission_id != binding.submission_id
                        or prior.request_hash != binding.request_hash
                        or prior.attempt_number != 1
                        or authorization["previous_attempt_id"] != prior.attempt_id
                        or authorization["private_proof_hash"]
                        != history[0]["launch_failure"]["proof_hash"]
                        or history[0]["retired_at"] != authorization["authorized_at"]
                    ):
                        raise AdapterHandleError(
                            "ADAPTER_SUBMISSION_INVALID: retry chain changed"
                        )
                if "launch_failure" in value:
                    if launch_state not in {"failed", "purging", "purged"}:
                        raise AdapterHandleError(
                            "ADAPTER_SUBMISSION_INVALID: nonfailed attempt has failure proof"
                        )
                    if (
                        launch_state in {"purging", "purged"}
                        and binding.attempt_number != 2
                    ):
                        raise AdapterHandleError(
                            "ADAPTER_SUBMISSION_INVALID: purge failure chain is invalid"
                        )
                    DeepwaveAdapter._validate_failure_document(
                        value["launch_failure"], binding
                    )
            if schema_version == "1.3.0":
                history = value.get("attempt_history")
                if binding.attempt_number != 2 or not isinstance(history, list):
                    raise AdapterHandleError(
                        "ADAPTER_SUBMISSION_INVALID: Worker-exit retry budget changed"
                    )
                authorization = DeepwaveAdapter._validate_retry_authorization_document(
                    value.get("retry_authorization"),
                    expected_failure_kind="worker_exit",
                )
                expected_history = {
                    "submission_id",
                    "job_id",
                    "request_hash",
                    "created_at",
                    "launch_attempt",
                    "worker_exit",
                    "retired_at",
                }
                if len(history) != 1 or set(history[0]) != expected_history:
                    raise AdapterHandleError(
                        "ADAPTER_SUBMISSION_INVALID: Worker-exit retry history is invalid"
                    )
                try:
                    prior = binding_from_submission_record(history[0])
                except WorkerControlError as error:
                    raise AdapterHandleError(
                        "ADAPTER_SUBMISSION_INVALID: prior attempt binding is invalid"
                    ) from error
                exit_evidence = DeepwaveAdapter._validate_worker_exit_document(
                    history[0]["worker_exit"], prior
                )
                _parse_timestamp(
                    history[0]["retired_at"], code="ADAPTER_RETRY_INVALID"
                )
                if (
                    prior.submission_id != binding.submission_id
                    or prior.request_hash != binding.request_hash
                    or prior.attempt_number != 1
                    or authorization["previous_attempt_id"] != prior.attempt_id
                    or authorization["private_proof_hash"]
                    != exit_evidence["record_hash"]
                    or history[0]["retired_at"] != authorization["authorized_at"]
                ):
                    raise AdapterHandleError(
                        "ADAPTER_SUBMISSION_INVALID: Worker-exit retry chain changed"
                    )
                if "launch_failure" in value:
                    if launch_state not in {"failed", "purging", "purged"}:
                        raise AdapterHandleError(
                            "ADAPTER_SUBMISSION_INVALID: nonfailed attempt has failure proof"
                        )
                    DeepwaveAdapter._validate_failure_document(
                        value["launch_failure"], binding
                    )
        if launch_state not in {
            "preparing",
            "launching",
            "launched",
            "failed",
            "purging",
            "purged",
        }:
            raise AdapterHandleError(
                "ADAPTER_SUBMISSION_INVALID: launch state is unknown"
            )
        if launch_state in {"purging", "purged"} and (
            not isinstance(value.get("purge_id"), str)
            or OPAQUE_ID.fullmatch(value["purge_id"]) is None
        ):
            raise AdapterHandleError(
                "ADAPTER_SUBMISSION_INVALID: purge identity is invalid"
            )
        if _sha256_document(DeepwaveAdapter._record_request_payload(value)) != value["request_hash"]:
            raise AdapterHandleError(
                "ADAPTER_SUBMISSION_INVALID: request hash no longer matches"
            )
        if (
            _sha256_document(DeepwaveAdapter._record_integrity_payload(value))
            != value["record_hash"]
        ):
            raise AdapterHandleError(
                "ADAPTER_SUBMISSION_INVALID: private record integrity check failed"
            )
        return value

    @staticmethod
    def _lock_submission(
        lock_path: Path,
        *,
        create: bool = True,
        timeout_seconds: float | None = None,
    ):
        class SubmissionLock:
            def __enter__(self_nonlocal):
                flags = (
                    (os.O_RDWR | os.O_CREAT if create else os.O_RDONLY)
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0)
                )
                directory_descriptor = -1
                try:
                    candidate, directory_descriptor = _safe_parent_fd(lock_path)
                    self_nonlocal.descriptor = os.open(
                        candidate.name,
                        flags,
                        0o600,
                        dir_fd=directory_descriptor,
                    )
                    lock_status = os.fstat(self_nonlocal.descriptor)
                    if (
                        not stat.S_ISREG(lock_status.st_mode)
                        or lock_status.st_uid != os.geteuid()
                        or stat.S_IMODE(lock_status.st_mode) & 0o077
                    ):
                        raise OSError("submission lock is not private")
                    if timeout_seconds is None:
                        fcntl.flock(self_nonlocal.descriptor, fcntl.LOCK_EX)
                    else:
                        deadline: float | None = None
                        while True:
                            try:
                                fcntl.flock(
                                    self_nonlocal.descriptor,
                                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                                )
                                break
                            except BlockingIOError as error:
                                now = time.monotonic()
                                if deadline is None:
                                    deadline = now + timeout_seconds
                                if now >= deadline:
                                    os.close(self_nonlocal.descriptor)
                                    self_nonlocal.descriptor = -1
                                    raise AdapterUnavailable(
                                        "ADAPTER_SUBMISSION_BUSY: submission lock is held"
                                    ) from error
                                time.sleep(0.01)
                except OSError as error:
                    descriptor = getattr(self_nonlocal, "descriptor", -1)
                    if descriptor >= 0:
                        os.close(descriptor)
                    if not create and isinstance(error, FileNotFoundError):
                        raise AdapterUnavailable(
                            "ADAPTER_SUBMISSION_NOT_FOUND: no private submission record exists"
                        ) from error
                    raise AdapterUnavailable(
                        "ADAPTER_STATE_UNAVAILABLE: cannot lock submission"
                    ) from error
                finally:
                    if directory_descriptor >= 0:
                        os.close(directory_descriptor)
                return self_nonlocal

            def __exit__(self_nonlocal, exc_type, exc, traceback):
                fcntl.flock(self_nonlocal.descriptor, fcntl.LOCK_UN)
                os.close(self_nonlocal.descriptor)

        return SubmissionLock()

    def observe_existing_worker_attempt(
        self,
        *,
        task_id: str,
        node_id: str,
        plan_hash: str,
        idempotency_key: str,
        project_id: str,
        principal_id: str,
        algorithm: Mapping[str, Any],
        dataset: Mapping[str, Any],
        task_type: str,
        parameters: Mapping[str, Any],
        resources: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Read one exact managed attempt without launching or scanning.

        A ready ``launching`` attempt may be promoted to the already existing
        immutable handle while the submission lock is held.  Heartbeat age is
        deliberately not interpreted as a replacement or liveness signal.
        """

        self._validate_submit_identity(
            task_id=task_id,
            node_id=node_id,
            plan_hash=plan_hash,
            idempotency_key=idempotency_key,
        )
        validated = self._validate_request(
            project_id=project_id,
            principal_id=principal_id,
            algorithm=algorithm,
            dataset=dataset,
            task_type=task_type,
            parameters=parameters,
            resources=resources,
            verify_runtime=False,
            allow_historical_managed=True,
        )
        submission_id = self._submission_id(task_id, plan_hash, idempotency_key)
        request_payload = self._request_payload(
            submission_id=submission_id,
            task_id=task_id,
            node_id=node_id,
            plan_hash=plan_hash,
            idempotency_key=idempotency_key,
            validated=validated,
        )
        request_hash = _sha256_document(request_payload)
        index_name = submission_id.removeprefix("submission-") + ".json"
        control = self._run_root / CONTROL_DIRECTORY
        index_path = control / "submissions" / index_name
        lock_path = control / "locks" / (index_name + ".lock")

        with self._lock_submission(
            lock_path, create=False, timeout_seconds=5.0
        ):
            if not index_path.exists() and not index_path.is_symlink():
                raise AdapterUnavailable(
                    "ADAPTER_SUBMISSION_NOT_FOUND: no private submission record exists"
                )
            record = self._read_submission(index_path)
            if (
                record["adapter_version"] != validated.algorithm["version"]
                or record["algorithm"] != validated.algorithm
            ):
                raise AdapterUnavailable(
                    "ADAPTER_SUBMISSION_VERSION_UNSUPPORTED: private receipt is not current"
                )
            if (
                record["submission_id"] != submission_id
                or record["request_hash"] != request_hash
                or self._record_request_payload(record) != request_payload
            ):
                raise AdapterIdempotencyConflict(
                    "ADAPTER_IDEMPOTENCY_CONFLICT: key is bound to another request"
                )
            try:
                expected_job_id = self._expected_job_id(record)
                self._validate_fingerprint(record["fingerprint"], validated=validated)
            except AdapterError as error:
                raise AdapterHandleError(
                    "ADAPTER_SUBMISSION_INVALID: private receipt binding is invalid"
                ) from error
            if record["job_id"] != expected_job_id:
                raise AdapterHandleError(
                    "ADAPTER_SUBMISSION_INVALID: private job identity is invalid"
                )
            if record["schema_version"] not in {"1.1.0", "1.2.0", "1.3.0"}:
                raise AdapterUnavailable(
                    "WORKER_EVIDENCE_UNAVAILABLE: private receipt has no managed attempt"
                )
            if record["launch_state"] in {"purging", "purged"}:
                raise AdapterUnavailable(
                    "WORKER_EVIDENCE_UNAVAILABLE: private receipt is being purged"
                )
            job_dir = self._run_root / record["job_id"]
            if (
                record["launch_state"] == "preparing"
                and not job_dir.exists()
                and not job_dir.is_symlink()
            ):
                raise AdapterUnavailable(
                    "WORKER_EVIDENCE_NOT_READY: managed job directory was not staged"
                )
            try:
                binding = binding_from_submission_record(record)
                evidence = read_worker_attempt_evidence(
                    self._run_root,
                    job_dir,
                    binding,
                )
            except (FileNotFoundError, WorkerControlError) as error:
                raise AdapterHandleError(
                    "ADAPTER_SUBMISSION_INVALID: Worker attempt evidence is invalid"
                ) from error
            if evidence is None:
                raise AdapterUnavailable(
                    "WORKER_EVIDENCE_NOT_READY: managed launch ticket is unavailable"
                )
            if (
                evidence.heartbeat_state == "waiting"
                and validated.algorithm
                != {"id": ALGORITHM_ID, "version": "1.6.0"}
            ):
                raise AdapterHandleError(
                    "ADAPTER_SUBMISSION_INVALID: checkpoint Waiting is not supported by this receipt"
                )

            handle: AdapterHandle | None = None
            launch_state = record["launch_state"]
            if evidence.started:
                if launch_state == "launching":
                    record["launch_state"] = "launched"
                    self._write_submission(index_path, record)
                    launch_state = "launched"
                if launch_state == "launched":
                    handle = self._handle_from_record(record)
                else:
                    raise AdapterHandleError(
                        "ADAPTER_SUBMISSION_INVALID: Worker evidence conflicts with launch state"
                    )
            return {
                "evidence": evidence.as_dict(),
                "handle": None if handle is None else handle.as_dict(),
            }

    def probe_pre_running_retry(
        self,
        *,
        task_id: str,
        node_id: str,
        plan_hash: str,
        idempotency_key: str,
        project_id: str,
        principal_id: str,
        algorithm: Mapping[str, Any],
        dataset: Mapping[str, Any],
        task_type: str,
        parameters: Mapping[str, Any],
        resources: Mapping[str, Any],
    ) -> AdapterPreRunningRetryProof:
        """Prove attempt 1 stopped before ready without mutating private state."""

        return self._probe_pre_running_failure(
            expected_attempt_number=1,
            task_id=task_id,
            node_id=node_id,
            plan_hash=plan_hash,
            idempotency_key=idempotency_key,
            project_id=project_id,
            principal_id=principal_id,
            algorithm=algorithm,
            dataset=dataset,
            task_type=task_type,
            parameters=parameters,
            resources=resources,
        )

    def probe_pre_running_retry_exhaustion(
        self,
        *,
        task_id: str,
        node_id: str,
        plan_hash: str,
        idempotency_key: str,
        project_id: str,
        principal_id: str,
        algorithm: Mapping[str, Any],
        dataset: Mapping[str, Any],
        task_type: str,
        parameters: Mapping[str, Any],
        resources: Mapping[str, Any],
    ) -> AdapterPreRunningRetryProof:
        """Prove exact attempt 2 also stopped before ready, without mutation."""

        return self._probe_pre_running_failure(
            expected_attempt_number=2,
            task_id=task_id,
            node_id=node_id,
            plan_hash=plan_hash,
            idempotency_key=idempotency_key,
            project_id=project_id,
            principal_id=principal_id,
            algorithm=algorithm,
            dataset=dataset,
            task_type=task_type,
            parameters=parameters,
            resources=resources,
        )

    def probe_worker_exit_retry(
        self,
        *,
        task_id: str,
        node_id: str,
        plan_hash: str,
        idempotency_key: str,
        project_id: str,
        principal_id: str,
        algorithm: Mapping[str, Any],
        dataset: Mapping[str, Any],
        task_type: str,
        parameters: Mapping[str, Any],
        resources: Mapping[str, Any],
    ) -> AdapterWorkerExitRetryProof:
        """Prove exact attempt 1 exited after ready without stop control."""

        return self._probe_worker_exit_failure(
            expected_attempt_number=1,
            task_id=task_id,
            node_id=node_id,
            plan_hash=plan_hash,
            idempotency_key=idempotency_key,
            project_id=project_id,
            principal_id=principal_id,
            algorithm=algorithm,
            dataset=dataset,
            task_type=task_type,
            parameters=parameters,
            resources=resources,
        )

    def probe_worker_exit_retry_exhaustion(
        self,
        *,
        task_id: str,
        node_id: str,
        plan_hash: str,
        idempotency_key: str,
        project_id: str,
        principal_id: str,
        algorithm: Mapping[str, Any],
        dataset: Mapping[str, Any],
        task_type: str,
        parameters: Mapping[str, Any],
        resources: Mapping[str, Any],
    ) -> AdapterWorkerExitRetryProof:
        """Prove exact attempt 2 also exited after ready, without attempt 3."""

        return self._probe_worker_exit_failure(
            expected_attempt_number=2,
            task_id=task_id,
            node_id=node_id,
            plan_hash=plan_hash,
            idempotency_key=idempotency_key,
            project_id=project_id,
            principal_id=principal_id,
            algorithm=algorithm,
            dataset=dataset,
            task_type=task_type,
            parameters=parameters,
            resources=resources,
        )

    def _probe_worker_exit_failure(
        self,
        *,
        expected_attempt_number: int,
        task_id: str,
        node_id: str,
        plan_hash: str,
        idempotency_key: str,
        project_id: str,
        principal_id: str,
        algorithm: Mapping[str, Any],
        dataset: Mapping[str, Any],
        task_type: str,
        parameters: Mapping[str, Any],
        resources: Mapping[str, Any],
    ) -> AdapterWorkerExitRetryProof:
        """Read one receipt-first post-ready exit under the submission lock."""

        if expected_attempt_number not in {1, 2}:
            raise AdapterUnavailable(
                "WORKER_RETRY_UNSUPPORTED: attempt is outside the finite budget"
            )
        (
            validated,
            submission_id,
            request_payload,
            request_hash,
            index_path,
            lock_path,
        ) = self._retry_request_material(
            task_id=task_id,
            node_id=node_id,
            plan_hash=plan_hash,
            idempotency_key=idempotency_key,
            project_id=project_id,
            principal_id=principal_id,
            algorithm=algorithm,
            dataset=dataset,
            task_type=task_type,
            parameters=parameters,
            resources=resources,
        )
        with self._lock_submission(lock_path, create=False, timeout_seconds=5.0):
            record = self._read_submission(index_path)
            expected_schemas = (
                {"1.1.0", "1.2.0"}
                if expected_attempt_number == 1
                else {"1.2.0", "1.3.0"}
            )
            if (
                record["schema_version"] not in expected_schemas
                or record["adapter_version"] != validated.algorithm["version"]
                or record["algorithm"] != validated.algorithm
                or record["submission_id"] != submission_id
                or record["request_hash"] != request_hash
                or self._record_request_payload(record) != request_payload
                or record["launch_state"] != "launched"
            ):
                raise AdapterUnavailable(
                    "WORKER_RETRY_UNSUPPORTED: no exact post-ready exit exists"
                )
            try:
                binding = binding_from_submission_record(record)
                self._validate_fingerprint(record["fingerprint"], validated=validated)
                if (
                    binding.attempt_number != expected_attempt_number
                    or record["job_id"] != self._expected_job_id(record)
                ):
                    raise AdapterHandleError(
                        "ADAPTER_SUBMISSION_INVALID: failed attempt identity changed"
                    )
                job_dir = self._run_root / record["job_id"]
                attempt = read_worker_attempt_evidence(
                    self._run_root, job_dir, binding
                )
                exit_evidence = read_worker_exit_evidence(
                    self._run_root, job_dir, binding
                )
                status = _read_json_file(
                    job_dir / "status.json", code="ADAPTER_STATUS_INVALID"
                )
                attempt_document = None if attempt is None else attempt.as_dict()
                private_document = exit_evidence.as_dict()
                if (
                    attempt is None
                    or attempt.ticket_state != "spawned"
                    or not attempt.ready
                    or attempt.heartbeat_state != "running"
                    or status.get("job_id") != binding.job_id
                    or status.get("status") != "failed"
                    or status.get("stage") != "worker_exit"
                    or status.get("failure_code") is not None
                    or _sha256_document(status) != exit_evidence.post_status_hash
                    or exit_evidence.return_code in {0, 75, 76}
                ):
                    raise WorkerControlError(
                        "WORKER_EXIT_UNSAFE: exact post-ready proof is unavailable"
                    )
                self._validate_worker_exit_document(private_document, binding)
            except (FileNotFoundError, WorkerControlError) as error:
                raise AdapterUnavailable(
                    "WORKER_EXIT_UNSAFE: exact post-ready proof is unavailable"
                ) from error
            return AdapterWorkerExitRetryProof(
                failure_kind="worker_exit",
                previous_attempt_id=binding.attempt_id,
                previous_attempt_number=expected_attempt_number,
                private_schema_version=record["schema_version"],
                private_proof_hash=exit_evidence.record_hash,
                evidence=copy.deepcopy(attempt_document),
                exit_evidence=copy.deepcopy(private_document),
            )

    def _probe_pre_running_failure(
        self,
        *,
        expected_attempt_number: int,
        task_id: str,
        node_id: str,
        plan_hash: str,
        idempotency_key: str,
        project_id: str,
        principal_id: str,
        algorithm: Mapping[str, Any],
        dataset: Mapping[str, Any],
        task_type: str,
        parameters: Mapping[str, Any],
        resources: Mapping[str, Any],
    ) -> AdapterPreRunningRetryProof:
        """Read one current exact private failure under its idle fence."""

        if expected_attempt_number not in {1, 2}:
            raise AdapterUnavailable(
                "WORKER_RETRY_UNSUPPORTED: attempt is outside the finite budget"
            )

        (
            validated,
            submission_id,
            request_payload,
            request_hash,
            index_path,
            lock_path,
        ) = self._retry_request_material(
            task_id=task_id,
            node_id=node_id,
            plan_hash=plan_hash,
            idempotency_key=idempotency_key,
            project_id=project_id,
            principal_id=principal_id,
            algorithm=algorithm,
            dataset=dataset,
            task_type=task_type,
            parameters=parameters,
            resources=resources,
        )
        with self._lock_submission(lock_path, create=False, timeout_seconds=5.0):
            if not index_path.exists() and not index_path.is_symlink():
                raise AdapterUnavailable(
                    "ADAPTER_SUBMISSION_NOT_FOUND: no private submission record exists"
                )
            record = self._read_submission(index_path)
            expected_schema_versions = (
                {"1.2.0"}
                if expected_attempt_number == 1
                else {"1.2.0", "1.3.0"}
            )
            if (
                record["schema_version"] not in expected_schema_versions
                or record["adapter_version"] != validated.algorithm["version"]
                or record["algorithm"] != validated.algorithm
                or record["submission_id"] != submission_id
                or record["request_hash"] != request_hash
                or self._record_request_payload(record) != request_payload
                or record["launch_state"] != "failed"
                or (
                    expected_attempt_number == 1
                    and (
                        record.get("attempt_history") != []
                        or "retry_authorization" in record
                    )
                )
                or (
                    expected_attempt_number == 2
                    and (
                        not isinstance(record.get("attempt_history"), list)
                        or len(record["attempt_history"]) != 1
                        or "retry_authorization" not in record
                    )
                )
            ):
                raise AdapterUnavailable(
                    "WORKER_RETRY_UNSUPPORTED: no exact stopped attempt exists"
                )
            binding = binding_from_submission_record(record)
            if (
                binding.attempt_number != expected_attempt_number
                or record["job_id"] != self._expected_job_id(record)
            ):
                raise AdapterHandleError(
                    "ADAPTER_SUBMISSION_INVALID: failed attempt identity changed"
                )
            self._validate_fingerprint(record["fingerprint"], validated=validated)
            try:
                with hold_idle_execution_fence(self._run_root, binding):
                    evidence = read_pre_running_attempt_evidence(
                        self._run_root,
                        self._run_root / record["job_id"],
                        binding,
                    )
                    if (
                        evidence is None
                        or evidence.ticket_state != "failed"
                        or evidence.ticket_worker_pid is not None
                    ):
                        raise WorkerControlError(
                            "WORKER_RETRY_UNSAFE: attempt is not exactly pre-running"
                        )
                    expected_failure = self._failure_proof(record, evidence)
                    self._validate_failure_document(
                        record.get("launch_failure"), binding
                    )
                    if record["launch_failure"] != expected_failure:
                        raise AdapterHandleError(
                            "ADAPTER_SUBMISSION_INVALID: stopped proof differs "
                            "from Worker evidence"
                        )
                    return AdapterPreRunningRetryProof(
                        failure_kind="pre_running_launch_failure",
                        previous_attempt_id=binding.attempt_id,
                        previous_attempt_number=expected_attempt_number,
                        private_schema_version=record["schema_version"],
                        private_proof_hash=expected_failure["proof_hash"],
                        evidence=evidence.as_dict(),
                    )
            except WorkerControlError as error:
                raise AdapterUnavailable(
                    "WORKER_RETRY_UNSAFE: exact pre-running proof is unavailable"
                ) from error

    def retry_pre_running(
        self,
        *,
        authorization: Mapping[str, Any],
        task_id: str,
        node_id: str,
        plan_hash: str,
        idempotency_key: str,
        project_id: str,
        principal_id: str,
        algorithm: Mapping[str, Any],
        dataset: Mapping[str, Any],
        task_type: str,
        parameters: Mapping[str, Any],
        resources: Mapping[str, Any],
    ) -> AdapterHandle:
        """Append and launch deterministic attempt 2 after SQLite authorization."""

        token = self._validate_retry_authorization_document(authorization)
        (
            validated,
            submission_id,
            request_payload,
            request_hash,
            index_path,
            lock_path,
        ) = self._retry_request_material(
            task_id=task_id,
            node_id=node_id,
            plan_hash=plan_hash,
            idempotency_key=idempotency_key,
            project_id=project_id,
            principal_id=principal_id,
            algorithm=algorithm,
            dataset=dataset,
            task_type=task_type,
            parameters=parameters,
            resources=resources,
        )
        with self._lock_submission(lock_path, create=False, timeout_seconds=5.0):
            record = self._read_submission(index_path)
            if (
                record["schema_version"] != "1.2.0"
                or record["adapter_version"] != validated.algorithm["version"]
                or record["algorithm"] != validated.algorithm
                or record["submission_id"] != submission_id
                or record["request_hash"] != request_hash
                or self._record_request_payload(record) != request_payload
            ):
                raise AdapterUnavailable(
                    "WORKER_RETRY_UNSUPPORTED: private submission is not retryable"
                )
            current = binding_from_submission_record(record)
            if current.attempt_number == 2:
                stored = self._validate_retry_authorization_document(
                    record.get("retry_authorization")
                )
                if stored != token:
                    raise AdapterIdempotencyConflict(
                        "ADAPTER_RETRY_CONFLICT: retry authorization changed"
                    )
                if record["launch_state"] == "launched":
                    return self._handle_from_record(record)
                if record["launch_state"] in {"preparing", "launching"}:
                    live = self._validate_managed_runtime_request(
                        project_id=project_id,
                        principal_id=principal_id,
                        algorithm=algorithm,
                        dataset=dataset,
                        task_type=task_type,
                        parameters=parameters,
                        resources=resources,
                    )
                    return self._resume_staged_submission(
                        index_path=index_path, record=record, validated=live
                    )
                raise AdapterUnavailable(
                    "WORKER_RETRY_EXHAUSTED: second attempt is terminal"
                )
            if (
                current.attempt_number != 1
                or record["launch_state"] != "failed"
                or record.get("attempt_history") != []
                or token["previous_attempt_id"] != current.attempt_id
                or token["private_proof_hash"]
                != record.get("launch_failure", {}).get("proof_hash")
            ):
                raise AdapterUnavailable(
                    "WORKER_RETRY_UNSAFE: authorization does not bind attempt 1"
                )
            old_job_dir = self._run_root / record["job_id"]
            evidence = read_worker_attempt_evidence(
                self._run_root, old_job_dir, current
            )
            if (
                evidence is None
                or evidence.ticket_state != "failed"
                or evidence.ticket_worker_pid is not None
                or evidence.ready
                or evidence.heartbeat_record_hash is not None
                or self._failure_proof(record, evidence) != record.get("launch_failure")
            ):
                raise AdapterUnavailable(
                    "WORKER_RETRY_UNSAFE: stopped proof no longer matches"
                )
            live = self._validate_managed_runtime_request(
                project_id=project_id,
                principal_id=principal_id,
                algorithm=algorithm,
                dataset=dataset,
                task_type=task_type,
                parameters=parameters,
                resources=resources,
            )
            if live.normalized_config_hash != validated.normalized_config_hash:
                raise AdapterUnavailable(
                    "ADAPTER_VALIDATION_DRIFT: live validation changed retry identity"
                )
            created_at = token["authorized_at"]
            job_id = self._retry_job_id(
                submission_id, created_at, token["private_proof_hash"]
            )
            attempt_material = _stable_json_bytes(
                {
                    "submission_id": submission_id,
                    "attempt_number": 2,
                    "private_proof_hash": token["private_proof_hash"],
                }
            )
            retry_binding = LaunchAttemptBinding(
                submission_id=submission_id,
                attempt_id="attempt-" + hashlib.sha256(attempt_material).hexdigest()[:32],
                attempt_number=2,
                job_id=job_id,
                request_hash=request_hash,
                created_at=created_at,
            )
            expected_ticket_payload = {
                **retry_binding.payload(),
                "binding_hash": retry_binding.binding_hash,
                "state": "staged",
                "capacity_slot": None,
                "capacity_generation": None,
                "worker_pid": None,
                "updated_at": created_at,
            }
            expected_ticket = {
                **expected_ticket_payload,
                "record_hash": _sha256_document(expected_ticket_payload),
            }
            expected_config = {"job_id": job_id, **live.worker_config}
            expected_status = {
                "job_id": job_id,
                "status": "queued",
                "stage": "queued",
                "iteration": 0,
                "total_iterations": live.parameters["iterations"],
                "message": "FWI Adapter job queued",
                "updated_at": created_at,
            }
            retry_job_dir = self._run_root / job_id
            try:
                with hold_idle_execution_fence(self._run_root, current):
                    fenced_evidence = read_pre_running_attempt_evidence(
                        self._run_root, old_job_dir, current
                    )
                    if (
                        fenced_evidence is None
                        or fenced_evidence.ticket_state != "failed"
                        or fenced_evidence.ticket_worker_pid is not None
                        or self._failure_proof(record, fenced_evidence)
                        != record.get("launch_failure")
                    ):
                        raise WorkerControlError(
                            "WORKER_RETRY_UNSAFE: stopped proof no longer matches"
                        )
                    try:
                        retry_job_dir = _create_private_directory(retry_job_dir)
                    except FileExistsError:
                        retry_job_dir = _require_private_directory(
                            retry_job_dir, parent=self._run_root
                        )
                        descriptor = _open_directory_fd(retry_job_dir)
                        try:
                            unexpected = set(os.listdir(descriptor)) - {
                                ".worker-launch.json",
                                "config.original.json",
                                "status.json",
                            }
                        finally:
                            os.close(descriptor)
                        if unexpected:
                            raise AdapterHandleError(
                                "ADAPTER_SUBMISSION_INVALID: retry directory is not staged"
                            )
                        for name, expected, private in (
                            (".worker-launch.json", expected_ticket, True),
                            ("config.original.json", expected_config, False),
                            ("status.json", expected_status, False),
                        ):
                            path = retry_job_dir / name
                            if path.exists() or path.is_symlink():
                                try:
                                    existing = _read_json_file(
                                        path,
                                        code="ADAPTER_SUBMISSION_INVALID",
                                        private=private,
                                    )
                                except AdapterStatusError as error:
                                    raise AdapterHandleError(str(error)) from error
                                if existing != expected:
                                    raise AdapterHandleError(
                                        "ADAPTER_SUBMISSION_INVALID: retry staging changed"
                                    )
                    stage_launch_attempt(
                        self._run_root, retry_job_dir, retry_binding
                    )
                    _atomic_write_json(
                        retry_job_dir / "config.original.json",
                        expected_config,
                    )
                    _atomic_write_json(
                        retry_job_dir / "status.json",
                        expected_status,
                    )
                    record["attempt_history"] = [
                        {
                            "submission_id": record["submission_id"],
                            "job_id": record["job_id"],
                            "request_hash": record["request_hash"],
                            "created_at": record["created_at"],
                            "launch_attempt": copy.deepcopy(record["launch_attempt"]),
                            "launch_failure": copy.deepcopy(record["launch_failure"]),
                            "retired_at": created_at,
                        }
                    ]
                    record["retry_authorization"] = copy.deepcopy(token)
                    record.pop("launch_failure", None)
                    record.update(
                        {
                            "job_id": job_id,
                            "created_at": created_at,
                            "launch_attempt": retry_binding.record(),
                            "launch_state": "preparing",
                        }
                    )
                    self._write_submission(index_path, record)
            except WorkerControlError as error:
                raise AdapterUnavailable(error.code) from error
            return self._launch_prepared_submission(
                index_path=index_path,
                record=record,
                validated=live,
                job_dir=retry_job_dir,
                launch_binding=retry_binding,
            )

    def retry_worker_exit(
        self,
        *,
        authorization: Mapping[str, Any],
        task_id: str,
        node_id: str,
        plan_hash: str,
        idempotency_key: str,
        project_id: str,
        principal_id: str,
        algorithm: Mapping[str, Any],
        dataset: Mapping[str, Any],
        task_type: str,
        parameters: Mapping[str, Any],
        resources: Mapping[str, Any],
    ) -> AdapterHandle:
        """Append and launch attempt 2 from one exact post-ready exit receipt."""

        token = self._validate_retry_authorization_document(
            authorization, expected_failure_kind="worker_exit"
        )
        (
            validated,
            submission_id,
            request_payload,
            request_hash,
            index_path,
            lock_path,
        ) = self._retry_request_material(
            task_id=task_id,
            node_id=node_id,
            plan_hash=plan_hash,
            idempotency_key=idempotency_key,
            project_id=project_id,
            principal_id=principal_id,
            algorithm=algorithm,
            dataset=dataset,
            task_type=task_type,
            parameters=parameters,
            resources=resources,
        )
        with self._lock_submission(lock_path, create=False, timeout_seconds=5.0):
            record = self._read_submission(index_path)
            if (
                record["adapter_version"] != validated.algorithm["version"]
                or record["algorithm"] != validated.algorithm
                or record["submission_id"] != submission_id
                or record["request_hash"] != request_hash
                or self._record_request_payload(record) != request_payload
            ):
                raise AdapterUnavailable(
                    "WORKER_RETRY_UNSUPPORTED: private submission is not retryable"
                )
            current = binding_from_submission_record(record)
            if current.attempt_number == 2:
                if record["schema_version"] != "1.3.0":
                    raise AdapterUnavailable(
                        "WORKER_RETRY_UNSUPPORTED: attempt 2 has another retry lineage"
                    )
                stored = self._validate_retry_authorization_document(
                    record.get("retry_authorization"),
                    expected_failure_kind="worker_exit",
                )
                if stored != token:
                    raise AdapterIdempotencyConflict(
                        "ADAPTER_RETRY_CONFLICT: retry authorization changed"
                    )
                if record["launch_state"] == "launched":
                    return self._handle_from_record(record)
                if record["launch_state"] in {"preparing", "launching"}:
                    live = self._validate_managed_runtime_request(
                        project_id=project_id,
                        principal_id=principal_id,
                        algorithm=algorithm,
                        dataset=dataset,
                        task_type=task_type,
                        parameters=parameters,
                        resources=resources,
                    )
                    return self._resume_staged_submission(
                        index_path=index_path, record=record, validated=live
                    )
                raise AdapterUnavailable(
                    "WORKER_RETRY_EXHAUSTED: second attempt is terminal"
                )
            if (
                current.attempt_number != 1
                or record["schema_version"] not in {"1.1.0", "1.2.0"}
                or record["launch_state"] != "launched"
                or record.get("attempt_history", []) != []
                or "retry_authorization" in record
                or token["previous_attempt_id"] != current.attempt_id
            ):
                raise AdapterUnavailable(
                    "WORKER_RETRY_UNSAFE: authorization does not bind attempt 1"
                )
            old_job_dir = self._run_root / record["job_id"]
            try:
                attempt = read_worker_attempt_evidence(
                    self._run_root, old_job_dir, current
                )
                exit_evidence = read_worker_exit_evidence(
                    self._run_root, old_job_dir, current
                )
                status = _read_json_file(
                    old_job_dir / "status.json", code="ADAPTER_STATUS_INVALID"
                )
            except (FileNotFoundError, WorkerControlError) as error:
                raise AdapterUnavailable(
                    "WORKER_RETRY_UNSAFE: stopped proof no longer matches"
                ) from error
            if (
                attempt is None
                or attempt.ticket_state != "spawned"
                or not attempt.ready
                or attempt.heartbeat_state != "running"
                or status.get("status") != "failed"
                or status.get("stage") != "worker_exit"
                or _sha256_document(status) != exit_evidence.post_status_hash
                or token["private_proof_hash"] != exit_evidence.record_hash
            ):
                raise AdapterUnavailable(
                    "WORKER_RETRY_UNSAFE: stopped proof no longer matches"
                )
            live = self._validate_managed_runtime_request(
                project_id=project_id,
                principal_id=principal_id,
                algorithm=algorithm,
                dataset=dataset,
                task_type=task_type,
                parameters=parameters,
                resources=resources,
            )
            if live.normalized_config_hash != validated.normalized_config_hash:
                raise AdapterUnavailable(
                    "ADAPTER_VALIDATION_DRIFT: live validation changed retry identity"
                )
            created_at = token["authorized_at"]
            job_id = self._retry_job_id(
                submission_id, created_at, token["private_proof_hash"]
            )
            attempt_material = _stable_json_bytes(
                {
                    "submission_id": submission_id,
                    "attempt_number": 2,
                    "private_proof_hash": token["private_proof_hash"],
                }
            )
            retry_binding = LaunchAttemptBinding(
                submission_id=submission_id,
                attempt_id="attempt-"
                + hashlib.sha256(attempt_material).hexdigest()[:32],
                attempt_number=2,
                job_id=job_id,
                request_hash=request_hash,
                created_at=created_at,
            )
            expected_ticket_payload = {
                **retry_binding.payload(),
                "binding_hash": retry_binding.binding_hash,
                "state": "staged",
                "capacity_slot": None,
                "capacity_generation": None,
                "worker_pid": None,
                "updated_at": created_at,
            }
            expected_ticket = {
                **expected_ticket_payload,
                "record_hash": _sha256_document(expected_ticket_payload),
            }
            expected_config = {"job_id": job_id, **live.worker_config}
            expected_status = {
                "job_id": job_id,
                "status": "queued",
                "stage": "queued",
                "iteration": 0,
                "total_iterations": live.parameters["iterations"],
                "message": "FWI Adapter job queued",
                "updated_at": created_at,
            }
            retry_job_dir = self._run_root / job_id
            try:
                with hold_idle_execution_fence(self._run_root, current):
                    try:
                        retry_job_dir = _create_private_directory(retry_job_dir)
                    except FileExistsError:
                        retry_job_dir = _require_private_directory(
                            retry_job_dir, parent=self._run_root
                        )
                        descriptor = _open_directory_fd(retry_job_dir)
                        try:
                            unexpected = set(os.listdir(descriptor)) - {
                                ".worker-launch.json",
                                "config.original.json",
                                "status.json",
                            }
                        finally:
                            os.close(descriptor)
                        if unexpected:
                            raise AdapterHandleError(
                                "ADAPTER_SUBMISSION_INVALID: retry directory is not staged"
                            )
                        for name, expected, private in (
                            (".worker-launch.json", expected_ticket, True),
                            ("config.original.json", expected_config, False),
                            ("status.json", expected_status, False),
                        ):
                            path = retry_job_dir / name
                            if path.exists() or path.is_symlink():
                                existing = _read_json_file(
                                    path,
                                    code="ADAPTER_SUBMISSION_INVALID",
                                    private=private,
                                )
                                if existing != expected:
                                    raise AdapterHandleError(
                                        "ADAPTER_SUBMISSION_INVALID: retry staging changed"
                                    )
                    stage_launch_attempt(
                        self._run_root, retry_job_dir, retry_binding
                    )
                    _atomic_write_json(
                        retry_job_dir / "config.original.json", expected_config
                    )
                    _atomic_write_json(
                        retry_job_dir / "status.json", expected_status
                    )
                    record["schema_version"] = "1.3.0"
                    record["attempt_history"] = [
                        {
                            "submission_id": record["submission_id"],
                            "job_id": record["job_id"],
                            "request_hash": record["request_hash"],
                            "created_at": record["created_at"],
                            "launch_attempt": copy.deepcopy(record["launch_attempt"]),
                            "worker_exit": exit_evidence.as_dict(),
                            "retired_at": created_at,
                        }
                    ]
                    record["retry_authorization"] = copy.deepcopy(token)
                    record.pop("launch_failure", None)
                    record.update(
                        {
                            "job_id": job_id,
                            "created_at": created_at,
                            "launch_attempt": retry_binding.record(),
                            "launch_state": "preparing",
                        }
                    )
                    self._write_submission(index_path, record)
            except WorkerControlError as error:
                raise AdapterUnavailable(error.code) from error
            return self._launch_prepared_submission(
                index_path=index_path,
                record=record,
                validated=live,
                job_dir=retry_job_dir,
                launch_binding=retry_binding,
            )

    @staticmethod
    def _reconciliation_deferred(
        error: AdapterError | WorkerControlError,
    ) -> AdapterReconciliationDeferred:
        """Convert private failures to one stable fail-closed matrix arm."""

        code = error.code
        return AdapterReconciliationDeferred(
            classification=(
                "transient"
                if code in _TRANSIENT_RECONCILIATION_CODES
                else "uncertain"
            ),
            failure_code=code,
        )

    @staticmethod
    def _validate_reconciliation_fingerprint(
        record: Mapping[str, Any], *, normalized_config_hash: str
    ) -> None:
        """Validate a retained 1.4/1.5 fingerprint without live revalidation."""

        fingerprint = record.get("fingerprint")
        if not isinstance(fingerprint, Mapping):
            raise AdapterHandleError(
                "FINGERPRINT_INVALID: reconciliation fingerprint is missing"
            )
        value = copy.deepcopy(dict(fingerprint))
        event = {
            "schema_version": "1.0.0",
            "event_id": "adapter-reconciliation-fingerprint-validation",
            "sequence": 1,
            "task_id": "adapter-reconciliation-fingerprint-validation",
            "node_id": "invert",
            "event_type": "node_started",
            "task_status": "Running",
            "occurred_at": "2026-01-01T00:00:00Z",
            "fingerprint": value,
            "extensions": {},
        }
        errors = schema_errors("run-event.schema.json", event)
        dataset = record.get("dataset")
        parameters = record.get("parameters")
        resources = record.get("resources")
        if (
            errors
            or not isinstance(dataset, Mapping)
            or not isinstance(parameters, Mapping)
            or not isinstance(resources, Mapping)
            or not is_supported_receipt_binding(
                record.get("algorithm"),
                record.get("adapter_version"),
                value,
            )
            or value.get("provenance_mode") != "development"
            or not isinstance(value.get("source"), Mapping)
            or value["source"].get("identity_complete") is not False
            or value.get("seed") != parameters.get("seed")
            or not isinstance(value.get("hardware"), Mapping)
            or value["hardware"].get("device") != resources.get("device")
            or value.get("normalized_config_hash") != normalized_config_hash
            or value.get("input_hashes") != [dataset.get("content_hash")]
        ):
            raise AdapterHandleError(
                "FINGERPRINT_INVALID: retained reconciliation fingerprint changed"
            )

    def _probe_dispatch_reconciliation(
        self,
        *,
        task_id: str,
        node_id: str,
        plan_hash: str,
        idempotency_key: str,
        project_id: str,
        principal_id: str,
        algorithm: Mapping[str, Any],
        dataset: Mapping[str, Any],
        task_type: str,
        parameters: Mapping[str, Any],
        resources: Mapping[str, Any],
        normalized_config_hash: str,
    ) -> AdapterExistingDispatchReceiptProof | AdapterDispatchNotStartedProof:
        """Read one exact positive or exact pre-running negative result."""

        self._validate_submit_identity(
            task_id=task_id,
            node_id=node_id,
            plan_hash=plan_hash,
            idempotency_key=idempotency_key,
        )
        if (
            not isinstance(project_id, str)
            or OPAQUE_ID.fullmatch(project_id) is None
            or not isinstance(principal_id, str)
            or OPAQUE_ID.fullmatch(principal_id) is None
            or not isinstance(algorithm, Mapping)
            or set(algorithm) != {"id", "version"}
            or algorithm.get("id") != ALGORITHM_ID
            or algorithm.get("version") not in {"1.4.0", "1.5.0", "1.6.0"}
            or not isinstance(dataset, Mapping)
            or not isinstance(dataset.get("access_scope"), Mapping)
            or not isinstance(parameters, Mapping)
            or not isinstance(resources, Mapping)
            or not isinstance(normalized_config_hash, str)
            or PLAN_HASH.fullmatch(normalized_config_hash) is None
        ):
            raise AdapterUnavailable(
                "DISPATCH_RECONCILIATION_UNSUPPORTED: immutable request is unsupported"
            )

        adapter_version = algorithm["version"]
        submission_id = self._submission_id(
            task_id, plan_hash, idempotency_key
        )
        dataset_identity = {
            key: copy.deepcopy(dataset.get(key))
            for key in ("id", "version", "content_hash", "data_type")
        }
        request_payload = {
            "submission_id": submission_id,
            "task_id": task_id,
            "node_id": node_id,
            "plan_hash": plan_hash,
            "idempotency_key": idempotency_key,
            "project_id": project_id,
            "principal_id": principal_id,
            "algorithm": copy.deepcopy(dict(algorithm)),
            "dataset": dataset_identity,
            "dataset_access_scope": copy.deepcopy(dict(dataset["access_scope"])),
            "task_type": task_type,
            "parameters": copy.deepcopy(dict(parameters)),
            "resources": copy.deepcopy(dict(resources)),
            "normalized_config_hash": normalized_config_hash,
        }
        request_hash = _sha256_document(request_payload)
        index_name = submission_id.removeprefix("submission-") + ".json"
        control = self._run_root / CONTROL_DIRECTORY
        index_path = control / "submissions" / index_name
        lock_path = control / "locks" / (index_name + ".lock")

        with self._lock_submission(
            lock_path, create=False, timeout_seconds=5.0
        ):
            if not index_path.exists() and not index_path.is_symlink():
                raise AdapterUnavailable(
                    "ADAPTER_SUBMISSION_NOT_FOUND: no private submission record exists"
                )
            record = self._read_submission(index_path)
            if (
                record.get("adapter_version") != adapter_version
                or record.get("algorithm") != dict(algorithm)
                or record.get("submission_id") != submission_id
                or record.get("request_hash") != request_hash
                or self._record_request_payload(record) != request_payload
            ):
                raise AdapterIdempotencyConflict(
                    "ADAPTER_IDEMPOTENCY_CONFLICT: reconciliation request changed"
                )
            self._validate_reconciliation_fingerprint(
                record, normalized_config_hash=normalized_config_hash
            )
            if record.get("job_id") != self._expected_job_id(record):
                raise AdapterHandleError(
                    "ADAPTER_SUBMISSION_INVALID: private job identity is invalid"
                )

            launch_state = record["launch_state"]
            private_schema_version = record["schema_version"]
            if private_schema_version == "1.0.0":
                if launch_state != "launched":
                    raise AdapterUnavailable(
                        "DISPATCH_RECONCILIATION_UNCERTAIN: unmanaged receipt is not positive"
                    )
                return AdapterExistingDispatchReceiptProof(
                    evidence_kind="private_receipt",
                    handle=self._handle_from_record(record),
                    private_schema_version=private_schema_version,
                    receipt_record_hash=record["record_hash"],
                    worker_evidence=None,
                )
            if private_schema_version not in {"1.1.0", "1.2.0"}:
                raise AdapterUnavailable(
                    "DISPATCH_RECONCILIATION_UNSUPPORTED: managed schema is unsupported"
                )
            if launch_state in {"purging", "purged"}:
                raise AdapterUnavailable(
                    "DISPATCH_RECONCILIATION_UNCERTAIN: private receipt is being purged"
                )

            try:
                binding = binding_from_submission_record(record)
                if (
                    binding.attempt_number != 1
                    or binding.job_id != record["job_id"]
                ):
                    raise WorkerControlError(
                        "WORKER_CONTROL_INVALID: reconciliation attempt changed"
                    )
                job_dir = self._run_root / record["job_id"]
                evidence = read_worker_attempt_evidence(
                    self._run_root, job_dir, binding
                )
            except FileNotFoundError as error:
                raise AdapterUnavailable(
                    "WORKER_EVIDENCE_UNAVAILABLE: managed job evidence is missing"
                ) from error
            except WorkerControlError:
                raise

            if evidence is not None and evidence.started:
                allowed_heartbeat_states = {
                    "running",
                    "succeeded",
                    "failed",
                }
                if adapter_version == "1.6.0":
                    allowed_heartbeat_states.add("waiting")
                if (
                    launch_state not in {"launching", "launched"}
                    or evidence.heartbeat_state
                    not in allowed_heartbeat_states
                ):
                    raise AdapterUnavailable(
                        "DISPATCH_RECONCILIATION_UNCERTAIN: started evidence conflicts with private state"
                    )
                if launch_state == "launching":
                    record["launch_state"] = "launched"
                    self._write_submission(index_path, record)
                return AdapterExistingDispatchReceiptProof(
                    evidence_kind="managed_worker_receipt",
                    handle=self._handle_from_record(record),
                    private_schema_version="1.1.0",
                    receipt_record_hash=None,
                    worker_evidence=evidence.as_dict(),
                )

            if launch_state not in {"preparing", "launching", "failed"}:
                raise AdapterUnavailable(
                    "DISPATCH_RECONCILIATION_UNCERTAIN: private state contradicts no-start evidence"
                )
            if (
                adapter_version,
                private_schema_version,
            ) not in _EXACT_NEGATIVE_RECONCILIATION_VERSIONS:
                raise AdapterUnavailable(
                    "DISPATCH_RECONCILIATION_UNSUPPORTED: no exact negative proof exists for this version"
                )

            with hold_idle_execution_fence(self._run_root, binding):
                stopped = read_pre_running_attempt_evidence(
                    self._run_root, job_dir, binding
                )
                if stopped is None:
                    raise WorkerControlError(
                        "WORKER_RECONCILIATION_UNSAFE: managed launch ticket is unavailable"
                    )
                evidence_document = stopped.as_dict()
                evidence_hash = _sha256_document(evidence_document)
                private_record_hash = record["record_hash"]
                proof_payload = {
                    "schema_version": "1.0.0",
                    "result": "not_dispatched",
                    "evidence_kind": "managed_pre_running_failure",
                    "adapter_version": adapter_version,
                    "private_schema_version": private_schema_version,
                    "private_record_hash": private_record_hash,
                    "attempt_id": binding.attempt_id,
                    "attempt_number": binding.attempt_number,
                    "evidence_hash": evidence_hash,
                }
                return AdapterDispatchNotStartedProof(
                    result="not_dispatched",
                    evidence_kind="managed_pre_running_failure",
                    adapter_version=adapter_version,
                    private_schema_version=private_schema_version,
                    private_record_hash=private_record_hash,
                    private_proof_hash=_sha256_document(proof_payload),
                    attempt_id=binding.attempt_id,
                    attempt_number=binding.attempt_number,
                    evidence=evidence_document,
                )

    def probe_dispatch_reconciliation(
        self, **request: Any
    ) -> (
        AdapterExistingDispatchReceiptProof
        | AdapterDispatchNotStartedProof
        | AdapterReconciliationDeferred
    ):
        """Return one closed positive/negative/transient/uncertain result."""

        try:
            return self._probe_dispatch_reconciliation(**request)
        except (AdapterError, WorkerControlError) as error:
            return self._reconciliation_deferred(error)
        except (FileNotFoundError, OSError):
            return AdapterReconciliationDeferred(
                classification="uncertain",
                failure_code="DISPATCH_RECONCILIATION_UNAVAILABLE",
            )
        except Exception:
            return AdapterReconciliationDeferred(
                classification="uncertain",
                failure_code="DISPATCH_RECONCILIATION_UNAVAILABLE",
            )

    def probe_existing_dispatch_receipt(
        self, **request: Any
    ) -> AdapterExistingDispatchReceiptProof:
        """Probe one exact positive receipt without launching a Worker.

        Only an exact managed ready/heartbeat chain or a launched private 1.0
        receipt is positive.  Every missing, incomplete, ambiguous, busy,
        purged, or malformed state remains an explicit Adapter error for the
        control plane to keep as action-required reconciliation.
        """

        try:
            observed = self.observe_existing_worker_attempt(
                **request,
            )
        except AdapterUnavailable as error:
            if error.code != "WORKER_EVIDENCE_UNAVAILABLE":
                raise
        else:
            evidence = observed.get("evidence")
            handle_value = observed.get("handle")
            ready = evidence.get("ready") if isinstance(evidence, Mapping) else None
            heartbeat = (
                evidence.get("heartbeat") if isinstance(evidence, Mapping) else None
            )
            if (
                isinstance(handle_value, Mapping)
                and isinstance(ready, Mapping)
                and isinstance(heartbeat, Mapping)
            ):
                try:
                    handle = AdapterHandle(**copy.deepcopy(dict(handle_value)))
                except (TypeError, ValueError) as error:
                    raise AdapterHandleError(
                        "ADAPTER_SUBMISSION_INVALID: managed receipt handle is invalid"
                    ) from error
                return AdapterExistingDispatchReceiptProof(
                    evidence_kind="managed_worker_receipt",
                    handle=handle,
                    private_schema_version=(
                        "1.2.0"
                        if evidence.get("attempt_number") == 2
                        else "1.1.0"
                    ),
                    receipt_record_hash=None,
                    worker_evidence=copy.deepcopy(dict(evidence)),
                )
            raise AdapterUnavailable(
                "DISPATCH_RECEIPT_NOT_READY: managed receipt is not ready"
            )

        private = self.lookup_existing_private_receipt(**request)
        return AdapterExistingDispatchReceiptProof(
            evidence_kind="private_receipt",
            handle=private.handle,
            private_schema_version=private.private_schema_version,
            receipt_record_hash=private.receipt_record_hash,
            worker_evidence=None,
        )

    def _lookup_existing_receipt(
        self,
        *,
        task_id: str,
        node_id: str,
        plan_hash: str,
        idempotency_key: str,
        project_id: str,
        principal_id: str,
        algorithm: Mapping[str, Any],
        dataset: Mapping[str, Any],
        task_type: str,
        parameters: Mapping[str, Any],
        resources: Mapping[str, Any],
        allow_managed_promotion: bool,
    ) -> tuple[dict[str, Any], AdapterHandle]:
        """Read one exact current-version receipt under its submission lock.

        This path derives the private record name from immutable request
        identity and serializes with ``submit`` on the already-existing lock.
        Managed promotion is an explicit caller choice; neither mode launches,
        guesses a PID, nor scans the run root.
        """

        self._validate_submit_identity(
            task_id=task_id,
            node_id=node_id,
            plan_hash=plan_hash,
            idempotency_key=idempotency_key,
        )
        validated = self._validate_request(
            project_id=project_id,
            principal_id=principal_id,
            algorithm=algorithm,
            dataset=dataset,
            task_type=task_type,
            parameters=parameters,
            resources=resources,
            verify_runtime=False,
            allow_historical_managed=True,
        )
        submission_id = self._submission_id(
            task_id, plan_hash, idempotency_key
        )
        request_payload = self._request_payload(
            submission_id=submission_id,
            task_id=task_id,
            node_id=node_id,
            plan_hash=plan_hash,
            idempotency_key=idempotency_key,
            validated=validated,
        )
        request_hash = _sha256_document(request_payload)
        index_name = submission_id.removeprefix("submission-") + ".json"
        control = self._run_root / CONTROL_DIRECTORY
        index_path = control / "submissions" / index_name
        lock_path = control / "locks" / (index_name + ".lock")

        with self._lock_submission(
            lock_path, create=False, timeout_seconds=5.0
        ):
            if not index_path.exists() and not index_path.is_symlink():
                raise AdapterUnavailable(
                    "ADAPTER_SUBMISSION_NOT_FOUND: no private submission record exists"
                )
            record = self._read_submission(index_path)
            if (
                record["adapter_version"] != validated.algorithm["version"]
                or record["algorithm"] != validated.algorithm
            ):
                raise AdapterUnavailable(
                    "ADAPTER_SUBMISSION_VERSION_UNSUPPORTED: private receipt is not current"
                )
            if (
                record["submission_id"] != submission_id
                or record["request_hash"] != request_hash
                or self._record_request_payload(record) != request_payload
            ):
                raise AdapterIdempotencyConflict(
                    "ADAPTER_IDEMPOTENCY_CONFLICT: key is bound to another request"
                )
            try:
                expected_job_id = self._expected_job_id(record)
                self._validate_fingerprint(
                    record["fingerprint"], validated=validated
                )
            except AdapterError as error:
                raise AdapterHandleError(
                    "ADAPTER_SUBMISSION_INVALID: private receipt binding is invalid"
                ) from error
            if record["job_id"] != expected_job_id:
                raise AdapterHandleError(
                    "ADAPTER_SUBMISSION_INVALID: private job identity is invalid"
                )

            launch_state = record["launch_state"]
            if launch_state == "launched":
                return copy.deepcopy(record), self._handle_from_record(record)
            if launch_state == "preparing":
                raise AdapterUnavailable(
                    "ADAPTER_SUBMISSION_PREPARING: Worker launch was not reached"
                )
            if launch_state == "launching":
                if allow_managed_promotion and record["schema_version"] in {
                    "1.1.0",
                    "1.2.0",
                }:
                    try:
                        binding = binding_from_submission_record(record)
                        started = worker_attempt_started(
                            self._run_root,
                            self._run_root / record["job_id"],
                            binding,
                        )
                    except WorkerControlError as error:
                        raise AdapterHandleError(
                            "ADAPTER_SUBMISSION_INVALID: Worker attempt evidence is invalid"
                        ) from error
                    if started:
                        record["launch_state"] = "launched"
                        self._write_submission(index_path, record)
                        return copy.deepcopy(record), self._handle_from_record(record)
                raise AdapterUnavailable(
                    "ADAPTER_SUBMISSION_LAUNCH_AMBIGUOUS: Worker launch outcome is unknown"
                )
            if launch_state == "failed":
                raise AdapterUnavailable(
                    "WORKER_LAUNCH_FAILED: prior launch failed and P1 does not retry"
                )
            if launch_state == "purging":
                raise AdapterUnavailable(
                    "ADAPTER_SUBMISSION_PURGING: private receipt is being purged"
                )
            raise AdapterUnavailable(
                "ADAPTER_SUBMISSION_PURGED: private receipt was purged"
            )

    def lookup_existing_handle(self, **request: Any) -> AdapterHandle:
        """Read or fence-promote one exact current-version Worker receipt."""

        _, handle = self._lookup_existing_receipt(
            **request,
            allow_managed_promotion=True,
        )
        return handle

    def lookup_existing_private_receipt(
        self, **request: Any
    ) -> AdapterPrivateReceiptProof:
        """Prove an exact launched legacy-private receipt without mutation."""

        record, handle = self._lookup_existing_receipt(
            **request,
            allow_managed_promotion=False,
        )
        if record["schema_version"] != "1.0.0":
            raise AdapterUnavailable(
                "PRIVATE_RECEIPT_PROOF_UNAVAILABLE: receipt has managed evidence"
            )
        return AdapterPrivateReceiptProof(
            handle=handle,
            private_schema_version=record["schema_version"],
            receipt_record_hash=record["record_hash"],
        )

    @staticmethod
    def _validate_fingerprint(
        fingerprint: Mapping[str, Any], *, validated: AdapterValidation
    ) -> dict[str, Any]:
        value = copy.deepcopy(dict(fingerprint))
        event = {
            "schema_version": "1.0.0",
            "event_id": "adapter-fingerprint-validation",
            "sequence": 1,
            "task_id": "adapter-fingerprint-validation",
            "node_id": "invert",
            "event_type": "node_started",
            "task_status": "Running",
            "occurred_at": "2026-01-01T00:00:00Z",
            "fingerprint": value,
            "extensions": {},
        }
        errors = schema_errors("run-event.schema.json", event)
        if errors:
            raise AdapterUnavailable(
                "FINGERPRINT_INVALID: " + "; ".join(errors)
            )
        mismatches: list[str] = []
        if value["provenance_mode"] != "development":
            mismatches.append("P1.2a fingerprint must be development mode")
        if value["source"]["identity_complete"] is not False:
            mismatches.append("P1.2a source identity must remain explicitly incomplete")
        if value["algorithm"] != validated.algorithm:
            mismatches.append("algorithm")
        expected_adapter_version = validated.algorithm["version"]
        if (
            value["adapter_version"] != expected_adapter_version
            or not is_supported_receipt_binding(
                validated.algorithm,
                expected_adapter_version,
                value,
            )
        ):
            mismatches.append("adapter_version")
        if value["seed"] != validated.parameters["seed"]:
            mismatches.append("seed")
        if value["hardware"]["device"] != validated.parameters["device"]:
            mismatches.append("device")
        if value["normalized_config_hash"] != validated.normalized_config_hash:
            mismatches.append("normalized_config_hash")
        if value["input_hashes"] != [validated.dataset["content_hash"]]:
            mismatches.append("input_hashes")
        if mismatches:
            raise AdapterUnavailable(
                "FINGERPRINT_INVALID: fingerprint differs from the validated request: "
                + ", ".join(mismatches)
            )
        return value

    def _record_pre_ready_launch_failure(
        self,
        *,
        index_path: Path,
        record: dict[str, Any],
        validated: AdapterValidation,
        job_dir: Path,
        launch_binding: LaunchAttemptBinding,
        stopped_by_production_launcher: bool,
    ) -> bool:
        """Persist failure; add retry proof only after exact stopped/idle evidence."""

        failure: dict[str, Any] | None = None
        try:
            if stopped_by_production_launcher:
                with hold_idle_execution_fence(self._run_root, launch_binding):
                    prior = read_pre_running_attempt_evidence(
                        self._run_root, job_dir, launch_binding
                    )
                    if prior is None:
                        raise WorkerControlError(
                            "WORKER_RETRY_UNSAFE: launch ticket is unavailable"
                        )
                    mark_launch_failed(job_dir, launch_binding)
                    evidence = read_pre_running_attempt_evidence(
                        self._run_root, job_dir, launch_binding
                    )
                    if (
                        evidence is None
                        or evidence.ticket_state != "failed"
                        or evidence.ticket_worker_pid is not None
                        or evidence.ready
                        or evidence.heartbeat_record_hash is not None
                    ):
                        raise WorkerControlError(
                            "WORKER_RETRY_UNSAFE: failed attempt is not pre-running"
                        )
                    failure = self._failure_proof(record, evidence)
            else:
                prior = read_pre_running_attempt_evidence(
                    self._run_root, job_dir, launch_binding
                )
                if prior is None:
                    raise WorkerControlError(
                        "WORKER_RETRY_UNSAFE: launch ticket is unavailable"
                    )
                mark_launch_failed(job_dir, launch_binding)
                evidence = read_pre_running_attempt_evidence(
                    self._run_root, job_dir, launch_binding
                )
                if (
                    evidence is None
                    or evidence.ticket_state != "failed"
                    or evidence.ticket_worker_pid is not None
                    or evidence.ready
                    or evidence.heartbeat_record_hash is not None
                ):
                    raise WorkerControlError(
                        "WORKER_RETRY_UNSAFE: failed attempt is not pre-running"
                    )
        except (FileNotFoundError, OSError, WorkerControlError):
            return False
        record["launch_state"] = "failed"
        record.pop("launch_failure", None)
        if failure is not None:
            if record["schema_version"] == "1.1.0":
                record["schema_version"] = "1.2.0"
                record["attempt_history"] = []
            record["launch_failure"] = failure
        self._write_submission(index_path, record)
        _atomic_write_json(
            job_dir / "status.json",
            {
                "job_id": record["job_id"],
                "status": "failed",
                "stage": "submit",
                "iteration": 0,
                "total_iterations": validated.parameters["iterations"],
                "message": "FWI worker could not be started",
                "updated_at": self._clock(),
            },
        )
        return True

    def _launch_prepared_submission(
        self,
        *,
        index_path: Path,
        record: dict[str, Any],
        validated: AdapterValidation,
        job_dir: Path,
        launch_binding: LaunchAttemptBinding,
    ) -> AdapterHandle:
        """Launch one exact staged attempt while its submission lock is held."""

        try:
            record["launch_state"] = "launching"
            self._write_submission(index_path, record)
            self._launcher.launch(
                command=validated.command,
                config_path=job_dir / "config.original.json",
                run_dir=job_dir,
                run_root=self._run_root,
                wall_time_seconds=validated.resources["wall_time_seconds"],
                checkpoint_capable=(
                    record.get("adapter_version") == "1.6.0"
                    and record.get("algorithm")
                    == {"id": ALGORITHM_ID, "version": "1.6.0"}
                ),
            )
        except _AdapterLaunchAmbiguous as error:
            # Popen may have succeeded and the child retains both kernel
            # fences.  Keep ``launching`` so exact observation can adopt it.
            raise AdapterUnavailable(str(error)) from error
        except _AdapterLaunchStopped as error:
            recorded = self._record_pre_ready_launch_failure(
                index_path=index_path,
                record=record,
                validated=validated,
                job_dir=job_dir,
                launch_binding=launch_binding,
                stopped_by_production_launcher=True,
            )
            if not recorded:
                raise AdapterUnavailable(
                    "SUBMISSION_RECONCILIATION_REQUIRED: stopped launch proof is unavailable"
                ) from error
            raise AdapterUnavailable("WORKER_LAUNCH_FAILED: stopped before ready") from error
        except AdapterUnavailable as error:
            if error.code == "ADAPTER_CONCURRENCY_LIMIT":
                # The exact staged attempt remains the queue entry.  A later
                # active Supervisor term may retry this same pre-Popen attempt.
                record["launch_state"] = "preparing"
                self._write_submission(index_path, record)
                raise
            raise
        except Exception as error:
            if isinstance(self._launcher, SafeSubprocessWorkerLauncher):
                raise AdapterUnavailable(
                    "SUBMISSION_RECONCILIATION_REQUIRED: production launch state is uncertain"
                ) from error
            recorded = self._record_pre_ready_launch_failure(
                index_path=index_path,
                record=record,
                validated=validated,
                job_dir=job_dir,
                launch_binding=launch_binding,
                stopped_by_production_launcher=False,
            )
            if not recorded:
                raise AdapterUnavailable(
                    "SUBMISSION_RECONCILIATION_REQUIRED: launch failure state is uncertain"
                ) from error
            raise AdapterUnavailable(
                f"WORKER_LAUNCH_FAILED: {type(error).__name__}"
            ) from error
        record["launch_state"] = "launched"
        self._write_submission(index_path, record)
        return self._handle_from_record(record)

    def _resume_staged_submission(
        self,
        *,
        index_path: Path,
        record: dict[str, Any],
        validated: AdapterValidation,
    ) -> AdapterHandle:
        """Resume only a complete, exact, pre-Popen managed attempt."""

        if (
            record["schema_version"] not in {"1.1.0", "1.2.0", "1.3.0"}
            or record["adapter_version"] != validated.algorithm["version"]
            or record["algorithm"] != validated.algorithm
            or record["launch_state"] not in {"preparing", "launching"}
        ):
            raise AdapterUnavailable(
                "SUBMISSION_RECONCILIATION_REQUIRED: incomplete submission is not resumable"
            )
        expected_job_id = self._expected_job_id(record)
        if record["job_id"] != expected_job_id:
            raise AdapterHandleError(
                "ADAPTER_SUBMISSION_INVALID: private job identity is invalid"
            )
        fingerprint = self._validate_fingerprint(
            record["fingerprint"], validated=validated
        )
        if (
            record["worker_config"] != validated.worker_config
            or fingerprint != record["fingerprint"]
        ):
            raise AdapterHandleError(
                "ADAPTER_SUBMISSION_INVALID: prepared runtime identity changed"
            )
        job_dir = self._job_directory(record)
        try:
            launch_binding = binding_from_submission_record(record)
            evidence = read_worker_attempt_evidence(
                self._run_root, job_dir, launch_binding
            )
        except (FileNotFoundError, WorkerControlError) as error:
            raise AdapterHandleError(
                "ADAPTER_SUBMISSION_INVALID: staged Worker evidence is invalid"
            ) from error
        if (
            evidence is None
            or evidence.ticket_state != "staged"
            or evidence.capacity_slot is not None
            or evidence.capacity_generation is not None
            or evidence.ticket_worker_pid is not None
            or evidence.ready
            or evidence.heartbeat_record_hash is not None
        ):
            raise AdapterUnavailable(
                "SUBMISSION_LAUNCH_PENDING: prepared attempt is not safely resumable"
            )
        expected_config = {"job_id": record["job_id"], **validated.worker_config}
        expected_status = {
            "job_id": record["job_id"],
            "status": "queued",
            "stage": "queued",
            "iteration": 0,
            "total_iterations": validated.parameters["iterations"],
            "message": "FWI Adapter job queued",
            "updated_at": record["created_at"],
        }
        try:
            stored_config = _read_json_file(
                job_dir / "config.original.json", code="WORKER_CONFIG_INVALID"
            )
            stored_status = _read_json_file(
                job_dir / "status.json", code="WORKER_STATUS_INVALID"
            )
        except AdapterStatusError as error:
            raise AdapterHandleError(
                "ADAPTER_SUBMISSION_INVALID: prepared job evidence is incomplete"
            ) from error
        if stored_config != expected_config or stored_status != expected_status:
            raise AdapterHandleError(
                "ADAPTER_SUBMISSION_INVALID: prepared job evidence changed"
            )
        return self._launch_prepared_submission(
            index_path=index_path,
            record=record,
            validated=validated,
            job_dir=job_dir,
            launch_binding=launch_binding,
        )

    def submit(
        self,
        *,
        task_id: str,
        node_id: str,
        plan_hash: str,
        idempotency_key: str,
        project_id: str,
        principal_id: str,
        algorithm: Mapping[str, Any],
        dataset: Mapping[str, Any],
        task_type: str,
        parameters: Mapping[str, Any],
        resources: Mapping[str, Any],
    ) -> AdapterHandle:
        self._validate_submit_identity(
            task_id=task_id,
            node_id=node_id,
            plan_hash=plan_hash,
            idempotency_key=idempotency_key,
        )
        normalized = self._validate_request(
            project_id=project_id,
            principal_id=principal_id,
            algorithm=algorithm,
            dataset=dataset,
            task_type=task_type,
            parameters=parameters,
            resources=resources,
            verify_runtime=False,
        )
        # P0/SQLite scope node keys to an immutable plan, not globally and not
        # merely to a node label.  A changed node under the same plan/key is a
        # conflict; a genuinely new plan hash receives an independent scope.
        submission_id = self._submission_id(task_id, plan_hash, idempotency_key)
        request_payload = self._request_payload(
            submission_id=submission_id,
            task_id=task_id,
            node_id=node_id,
            plan_hash=plan_hash,
            idempotency_key=idempotency_key,
            validated=normalized,
        )
        request_hash = _sha256_document(request_payload)
        submissions, locks = self._control_paths()
        index_name = submission_id.removeprefix("submission-") + ".json"
        index_path = submissions / index_name
        lock_path = locks / (index_name + ".lock")

        def validate_live_request() -> AdapterValidation:
            value = self.validate(
                project_id=project_id,
                principal_id=principal_id,
                algorithm=algorithm,
                dataset=dataset,
                task_type=task_type,
                parameters=parameters,
                resources=resources,
            )
            if value.normalized_config_hash != normalized.normalized_config_hash:
                raise AdapterUnavailable(
                    "ADAPTER_VALIDATION_DRIFT: live validation changed request identity"
                )
            return value

        with self._lock_submission(lock_path, timeout_seconds=5.0):
            if index_path.exists() or index_path.is_symlink():
                record = self._read_submission(index_path)
                if record["submission_id"] != submission_id or record["request_hash"] != request_hash:
                    raise AdapterIdempotencyConflict(
                        "ADAPTER_IDEMPOTENCY_CONFLICT: key is bound to another request"
                    )
                state = record["launch_state"]
                if state == "launched":
                    return self._handle_from_record(record)
                if state == "failed":
                    raise AdapterUnavailable(
                        "WORKER_LAUNCH_FAILED: prior launch failed and P1 does not retry"
                    )
                if state in {"preparing", "launching"}:
                    if (
                        record["schema_version"] == "1.2.0"
                        and binding_from_submission_record(record).attempt_number == 2
                    ):
                        raise AdapterUnavailable(
                            "WORKER_RETRY_AUTHORIZATION_REQUIRED: attempt 2 must "
                            "resume through its durable retry authorization"
                        )
                    return self._resume_staged_submission(
                        index_path=index_path,
                        record=record,
                        validated=validate_live_request(),
                    )
                raise AdapterUnavailable(
                    "SUBMISSION_RECONCILIATION_REQUIRED: incomplete P1 submission is not relaunched"
                )

            # Readiness is deliberately evaluated only for a first submission.
            # A byte-identical replay must remain able to recover its handle if
            # the GPU or model mount later becomes temporarily unavailable.
            validated = validate_live_request()
            created_at = self._clock()
            _parse_timestamp(created_at, code="CLOCK_INVALID")
            job_id = self._job_id(submission_id, created_at)
            if validated.fingerprint is None:
                raise AdapterUnavailable(
                    "FINGERPRINT_INVALID: live validation returned no fingerprint"
                )
            fingerprint = copy.deepcopy(validated.fingerprint)
            launch_binding = LaunchAttemptBinding(
                submission_id=submission_id,
                attempt_id=f"attempt-{secrets.token_hex(16)}",
                attempt_number=1,
                job_id=job_id,
                request_hash=request_hash,
                created_at=created_at,
            )
            record: dict[str, Any] = {
                "schema_version": "1.1.0",
                **request_payload,
                "job_id": job_id,
                "request_hash": request_hash,
                "adapter_version": ADAPTER_VERSION,
                "worker_config": copy.deepcopy(validated.worker_config),
                "fingerprint": fingerprint,
                "created_at": created_at,
                "launch_attempt": launch_binding.record(),
                "launch_state": "preparing",
            }
            self._write_submission(index_path, record)
            job_dir = self._run_root / job_id
            try:
                job_dir = _create_private_directory(job_dir)
            except OSError as error:
                raise AdapterUnavailable(
                    "JOB_DIRECTORY_CONFLICT: cannot create a unique direct job directory"
                ) from error
            if job_dir.parent != self._run_root:
                raise AdapterUnavailable(
                    "JOB_DIRECTORY_INVALID: job directory escaped the run root"
                )
            # Publish the managed marker before queued status/config make this
            # directory reusable.  The legacy CLI rejects the sidecar, closing
            # the interval in which it could accidentally enter this job.
            try:
                stage_launch_attempt(
                    self._run_root,
                    job_dir,
                    launch_binding,
                )
            except WorkerControlError as error:
                record["launch_state"] = "failed"
                self._write_submission(index_path, record)
                raise AdapterUnavailable(error.code) from error
            worker_config = {"job_id": job_id, **validated.worker_config}
            _atomic_write_json(job_dir / "config.original.json", worker_config)
            _atomic_write_json(
                job_dir / "status.json",
                {
                    "job_id": job_id,
                    "status": "queued",
                    "stage": "queued",
                    "iteration": 0,
                    "total_iterations": validated.parameters["iterations"],
                    "message": "FWI Adapter job queued",
                    "updated_at": created_at,
                },
            )
            return self._launch_prepared_submission(
                index_path=index_path,
                record=record,
                validated=validated,
                job_dir=job_dir,
                launch_binding=launch_binding,
            )

    def _record_for_handle(
        self,
        handle: AdapterHandle,
        *,
        allowed_launch_states: frozenset[str] = frozenset({"launched"}),
    ) -> dict[str, Any]:
        if not isinstance(handle, AdapterHandle):
            raise AdapterHandleError(
                "ADAPTER_HANDLE_INVALID: expected an AdapterHandle"
            )
        if MANAGED_SUBMISSION_ID.fullmatch(handle.submission_id) is None:
            raise AdapterHandleError(
                "ADAPTER_HANDLE_INVALID: submission identity is malformed"
            )
        if (
            not handle.submission_id.startswith("submission-")
            or not JOB_ID.fullmatch(handle.job_id)
            or PLAN_HASH.fullmatch(handle.plan_hash) is None
            or not is_supported_receipt_binding(
                handle.algorithm,
                handle.adapter_version,
                handle.fingerprint,
            )
            or self._submission_id(
                handle.task_id,
                handle.plan_hash,
                handle.idempotency_key,
            )
            != handle.submission_id
        ):
            raise AdapterHandleError(
                "ADAPTER_HANDLE_INVALID: handle fields are malformed"
            )
        root = _validate_run_root(self._run_root, create=False)
        control_root = _require_private_directory(
            root / CONTROL_DIRECTORY, parent=root
        )
        control = _require_private_directory(
            control_root / "submissions", parent=control_root
        )
        index_name = handle.submission_id.removeprefix("submission-") + ".json"
        record = self._read_submission(control / index_name)
        if self._handle_from_record(record) != handle:
            raise AdapterHandleError(
                "ADAPTER_HANDLE_INVALID: handle does not match its private record"
            )
        if record["launch_state"] not in allowed_launch_states:
            raise AdapterHandleError(
                "ADAPTER_HANDLE_INVALID: submission state does not permit this operation"
            )
        return record

    def _checkpoint_submission_lock_path(self, handle: AdapterHandle) -> Path:
        if not isinstance(handle, AdapterHandle):
            raise AdapterHandleError(
                "ADAPTER_HANDLE_INVALID: expected an AdapterHandle"
            )
        if MANAGED_SUBMISSION_ID.fullmatch(handle.submission_id) is None:
            raise AdapterHandleError(
                "ADAPTER_HANDLE_INVALID: submission identity is malformed"
            )
        root = _validate_run_root(self._run_root, create=False)
        control = _require_private_directory(
            root / CONTROL_DIRECTORY, parent=root
        )
        locks = _require_private_directory(control / "locks", parent=control)
        index_name = handle.submission_id.removeprefix("submission-") + ".json"
        return locks / (index_name + ".lock")

    @staticmethod
    def _checkpoint_proof_from_evidence(
        handle: AdapterHandle,
        record: Mapping[str, Any],
        evidence: WorkerCheckpointEvidence,
        *,
        state: Literal["waiting", "requested", "resumed"] | None = None,
    ) -> AdapterCheckpointProof:
        selected_state = evidence.state if state is None else state
        if selected_state == "waiting":
            resume_id = None
            resume_request_hash = None
            resume_ack_hash = None
            resume_acknowledged_at = None
            checkpoint_proof_hash = None
        else:
            resume_id = evidence.resume_id
            resume_request_hash = evidence.resume_request_record_hash
            resume_ack_hash = evidence.resume_acknowledgement_record_hash
            resume_acknowledged_at = evidence.resume_acknowledged_at
            checkpoint_proof_hash = evidence.checkpoint_proof_hash
        if (
            selected_state == "waiting"
            and any(
                value is not None
                for value in (
                    resume_id,
                    resume_request_hash,
                    resume_ack_hash,
                    resume_acknowledged_at,
                )
            )
        ):
            raise AdapterHandleError(
                "CHECKPOINT_EVIDENCE_INVALID: waiting resume fields are invalid"
            )
        if (
            selected_state == "requested"
            and (
                checkpoint_proof_hash is None
                or PLAN_HASH.fullmatch(checkpoint_proof_hash) is None
                or
                resume_id is None
                or resume_request_hash is None
                or resume_ack_hash is not None
                or resume_acknowledged_at is not None
            )
        ):
            raise AdapterHandleError(
                "CHECKPOINT_EVIDENCE_INVALID: requested resume fields are invalid"
            )
        if selected_state == "resumed" and (
            checkpoint_proof_hash is None
            or PLAN_HASH.fullmatch(checkpoint_proof_hash) is None
            or any(
                value is None
                for value in (
                    resume_id,
                    resume_request_hash,
                    resume_ack_hash,
                    resume_acknowledged_at,
                )
            )
        ):
            raise AdapterHandleError(
                "CHECKPOINT_EVIDENCE_INVALID: resumed fields are incomplete"
            )
        payload = {
            "schema_version": "1.0.0",
            "task_id": handle.task_id,
            "node_id": handle.node_id,
            "submission_id": handle.submission_id,
            "attempt_id": evidence.attempt_id,
            "attempt_number": evidence.attempt_number,
            "checkpoint_id": evidence.checkpoint_id,
            "checkpoint_index": evidence.checkpoint_index,
            "completed_updates": evidence.completed_updates,
            "binding_hash": evidence.binding_hash,
            "submission_receipt_record_hash": record["record_hash"],
            "ready_record_hash": evidence.ready_record_hash,
            "checkpoint_manifest_relative_path": evidence.manifest_relative_path,
            "checkpoint_manifest_size_bytes": evidence.manifest_size_bytes,
            "checkpoint_manifest_hash": evidence.manifest_hash,
            "checkpoint_receipt_record_hash": evidence.checkpoint_record_hash,
            "checkpoint_proof_hash": checkpoint_proof_hash,
            "checkpoint_created_at": evidence.checkpoint_created_at,
            "state": selected_state,
            "resume_id": resume_id,
            "resume_request_record_hash": resume_request_hash,
            "resume_acknowledgement_record_hash": resume_ack_hash,
            "resume_acknowledged_at": resume_acknowledged_at,
        }
        return AdapterCheckpointProof(
            task_id=payload["task_id"],
            node_id=payload["node_id"],
            submission_id=payload["submission_id"],
            attempt_id=payload["attempt_id"],
            attempt_number=payload["attempt_number"],
            checkpoint_id=payload["checkpoint_id"],
            checkpoint_index=payload["checkpoint_index"],
            completed_updates=payload["completed_updates"],
            binding_hash=payload["binding_hash"],
            submission_receipt_record_hash=payload[
                "submission_receipt_record_hash"
            ],
            ready_record_hash=payload["ready_record_hash"],
            checkpoint_manifest_relative_path=payload[
                "checkpoint_manifest_relative_path"
            ],
            checkpoint_manifest_size_bytes=payload[
                "checkpoint_manifest_size_bytes"
            ],
            checkpoint_manifest_hash=payload["checkpoint_manifest_hash"],
            checkpoint_receipt_record_hash=payload[
                "checkpoint_receipt_record_hash"
            ],
            checkpoint_proof_hash=checkpoint_proof_hash,
            checkpoint_created_at=payload["checkpoint_created_at"],
            state=selected_state,
            resume_id=resume_id,
            resume_request_record_hash=resume_request_hash,
            resume_acknowledgement_record_hash=resume_ack_hash,
            resume_acknowledged_at=resume_acknowledged_at,
            proof_hash=_sha256_document(payload),
        )

    @staticmethod
    def _checkpoint_capability_is_current(
        handle: AdapterHandle, record: Mapping[str, Any]
    ) -> bool:
        return (
            handle.adapter_version == "1.6.0"
            and handle.algorithm
            == {"id": ALGORITHM_ID, "version": "1.6.0"}
            and record.get("adapter_version") == "1.6.0"
            and record.get("algorithm") == handle.algorithm
            and _is_supported_managed_control_record(handle, record)
        )

    @staticmethod
    def _checkpoint_control_error(error: WorkerControlError) -> AdapterError:
        if error.code == "WORKER_CHECKPOINT_PENDING":
            return AdapterUnavailable(
                "CHECKPOINT_EVIDENCE_PENDING: checkpoint publication is incomplete"
            )
        if error.code in {
            "WORKER_CHECKPOINT_ORPHANED",
            "WORKER_CHECKPOINT_INVALID",
            "WORKER_RESUME_INVALID",
        }:
            return AdapterUnavailable(
                "CHECKPOINT_ACTION_REQUIRED: checkpoint evidence is ambiguous"
            )
        if error.code in {
            "WORKER_RESUME_CONFLICT",
            "WORKER_RESUME_REPLAY",
        }:
            return AdapterIdempotencyConflict(
                "CHECKPOINT_RESUME_CONFLICT: resume identity changed"
            )
        return AdapterUnavailable(f"{error.code}: checkpoint control failed")

    def _read_checkpoint_locked(
        self, handle: AdapterHandle
    ) -> tuple[dict[str, Any], WorkerCheckpointEvidence | None]:
        record = self._record_for_handle(handle)
        if not self._checkpoint_capability_is_current(handle, record):
            raise AdapterUnavailable(
                "CHECKPOINT_CAPABILITY_UNAVAILABLE: receipt is not immutable 1.6"
            )
        binding = binding_from_submission_record(record)
        try:
            evidence = read_worker_checkpoint_evidence(
                self._run_root, self._job_directory(record), binding
            )
        except WorkerControlError as error:
            raise self._checkpoint_control_error(error) from error
        return record, evidence

    def probe_runtime_checkpoint(
        self, handle: AdapterHandle
    ) -> AdapterCheckpointProof | None:
        """Probe the sole checkpoint under the exact submission lock."""

        lock_path = self._checkpoint_submission_lock_path(handle)
        with self._lock_submission(
            lock_path, create=False, timeout_seconds=5.0
        ):
            record, evidence = self._read_checkpoint_locked(handle)
            if evidence is None:
                return None
            return self._checkpoint_proof_from_evidence(
                handle, record, evidence
            )

    @staticmethod
    def _validate_checkpoint_resume_authorization(
        value: Mapping[str, Any],
        handle: AdapterHandle,
        waiting: AdapterCheckpointProof,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
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
        if not isinstance(value, Mapping) or set(value) != required:
            raise AdapterHandleError(
                "CHECKPOINT_RESUME_AUTHORIZATION_INVALID: fields are invalid"
            )
        token = copy.deepcopy(dict(value))
        if (
            token.get("schema_version") != "1.0.0"
            or not isinstance(token.get("intent_id"), str)
            or OPAQUE_ID.fullmatch(token["intent_id"]) is None
            or token.get("task_id") != handle.task_id
            or token.get("node_id") != handle.node_id
            or token.get("submission_id") != handle.submission_id
            or token.get("attempt_id") != waiting.attempt_id
            or token.get("attempt_number") != waiting.attempt_number
            or token.get("checkpoint_id") != waiting.checkpoint_id
            or token.get("checkpoint_manifest_hash")
            != waiting.checkpoint_manifest_hash
            or token.get("checkpoint_receipt_record_hash")
            != waiting.checkpoint_receipt_record_hash
            or token.get("checkpoint_proof_hash") != waiting.proof_hash
            or MANAGED_RESUME_ID.fullmatch(token.get("resume_id", "")) is None
            or PLAN_HASH.fullmatch(
                token.get("resume_request_record_hash", "")
            )
            is None
        ):
            raise AdapterHandleError(
                "CHECKPOINT_RESUME_AUTHORIZATION_INVALID: binding changed"
            )
        _parse_timestamp(
            token.get("authorized_at"), code="CHECKPOINT_RESUME_AUTHORIZATION_INVALID"
        )
        request_payload = {
            "schema_version": "1.0.0",
            "resume_id": token["resume_id"],
            "submission_id": token["submission_id"],
            "attempt_id": token["attempt_id"],
            "attempt_number": token["attempt_number"],
            "checkpoint_id": token["checkpoint_id"],
            "checkpoint_manifest_hash": token["checkpoint_manifest_hash"],
            "checkpoint_receipt_record_hash": token[
                "checkpoint_receipt_record_hash"
            ],
            "checkpoint_proof_hash": token["checkpoint_proof_hash"],
            "authorized_at": token["authorized_at"],
        }
        request_document = {
            **request_payload,
            "record_hash": _sha256_document(request_payload),
        }
        if (
            request_document["record_hash"]
            != token["resume_request_record_hash"]
        ):
            raise AdapterHandleError(
                "CHECKPOINT_RESUME_AUTHORIZATION_INVALID: request hash changed"
            )
        return token, request_document

    def resume_runtime_checkpoint(
        self,
        handle: AdapterHandle,
        *,
        authorization: Mapping[str, Any],
    ) -> AdapterCheckpointProof:
        """Resume only the exact live Worker; this path has no launcher call."""

        lock_path = self._checkpoint_submission_lock_path(handle)
        with self._lock_submission(
            lock_path, create=False, timeout_seconds=5.0
        ):
            record, evidence = self._read_checkpoint_locked(handle)
            if evidence is None:
                raise AdapterUnavailable(
                    "CHECKPOINT_NOT_WAITING: checkpoint barrier was not reached"
                )
            waiting = self._checkpoint_proof_from_evidence(
                handle, record, evidence, state="waiting"
            )
            token, request_document = self._validate_checkpoint_resume_authorization(
                authorization, handle, waiting
            )
            if evidence.state in {"requested", "resumed"} and (
                evidence.resume_id != token["resume_id"]
                or evidence.checkpoint_proof_hash != token["checkpoint_proof_hash"]
                or evidence.resume_request_record_hash
                != token["resume_request_record_hash"]
            ):
                raise AdapterIdempotencyConflict(
                    "CHECKPOINT_RESUME_CONFLICT: existing resume request changed"
                )
            binding = binding_from_submission_record(record)
            try:
                resumed = request_worker_checkpoint_resume(
                    self._run_root,
                    self._job_directory(record),
                    binding,
                    request_document=request_document,
                )
            except WorkerControlError as error:
                raise self._checkpoint_control_error(error) from error
            return self._checkpoint_proof_from_evidence(
                handle, record, resumed
            )

    def _job_directory(self, record: Mapping[str, Any]) -> Path:
        job_id = record["job_id"]
        if not isinstance(job_id, str) or JOB_ID.fullmatch(job_id) is None:
            raise AdapterHandleError("ADAPTER_HANDLE_INVALID: job identity is malformed")
        unresolved = self._run_root / job_id
        if unresolved.parent != self._run_root:
            raise AdapterStatusError(
                "ADAPTER_STATUS_INVALID: job directory escaped the run root"
            )
        descriptor = -1
        try:
            descriptor = _open_directory_fd(unresolved)
            link_status = os.fstat(descriptor)
        except OSError as error:
            raise AdapterStatusError("ADAPTER_STATUS_INVALID: job directory is missing") from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if (
            not stat.S_ISDIR(link_status.st_mode)
            or link_status.st_uid != os.geteuid()
            or stat.S_IMODE(link_status.st_mode) & 0o022
        ):
            raise AdapterStatusError(
                "ADAPTER_STATUS_INVALID: job directory ownership or permissions are unsafe"
            )
        return unresolved

    def _status_for_record(
        self,
        record: Mapping[str, Any],
        *,
        cancellation_fence_held: bool = False,
        allow_pending_cancel: bool = False,
        timeout_fence_held: bool = False,
        allow_pending_timeout: bool = False,
    ) -> AdapterStatus:
        job_dir = self._job_directory(record)
        value = _read_json_file(job_dir / "status.json", code="ADAPTER_STATUS_INVALID")
        exit_receipt_path = job_dir / WORKER_EXIT_NAME
        if (
            value.get("status") in {"queued", "running", "waiting"}
            and _is_supported_managed_record(record)
            and not cancellation_fence_held
            and not timeout_fence_held
            and (exit_receipt_path.exists() or exit_receipt_path.is_symlink())
        ):
            try:
                binding = binding_from_submission_record(record)
                read_worker_exit_evidence(self._run_root, job_dir, binding)
                value = _read_json_file(
                    job_dir / "status.json", code="ADAPTER_STATUS_INVALID"
                )
            except (FileNotFoundError, WorkerControlError) as error:
                raise AdapterStatusError(
                    "ADAPTER_STATUS_INVALID: Worker exit receipt is invalid"
                ) from error
        if value.get("job_id") != record["job_id"]:
            raise AdapterStatusError(
                "ADAPTER_STATUS_INVALID: status identity does not match the handle"
            )
        worker_status = value.get("status")
        mapping = {
            "queued": "Queued",
            "running": "Running",
            "waiting": "Waiting",
            "succeeded": "Succeeded",
            "failed": "Failed",
            "cancelled": "Cancelled",
        }
        if worker_status not in mapping:
            raise AdapterStatusError(
                "ADAPTER_STATUS_INVALID: Worker status is unknown"
            )
        for field in ("stage", "message", "updated_at"):
            if not isinstance(value.get(field), str):
                raise AdapterStatusError(
                    f"ADAPTER_STATUS_INVALID: {field} must be a string"
                )
        allowed_stages = {
            "queued",
            "running",
            "validate_model",
            "generate_observed",
            "gradient_check",
            "invert",
            "checkpoint_wait",
            "plot",
            "complete",
            "failed",
            "worker_exit",
            "cancelled",
        }
        if value["stage"] not in allowed_stages or len(value["message"]) > 1000:
            raise AdapterStatusError(
                "ADAPTER_STATUS_INVALID: Worker stage or message is outside the v1 contract"
            )
        completed = value.get("iteration")
        total = value.get("total_iterations")
        if (
            type(completed) is not int
            or type(total) is not int
            or completed < 0
            or total < 0
            or completed > total
        ):
            raise AdapterStatusError(
                "ADAPTER_STATUS_INVALID: progress counters are invalid"
            )
        if total != record["parameters"]["iterations"]:
            raise AdapterStatusError(
                "ADAPTER_STATUS_INVALID: total iterations differ from the request"
            )
        combinations_valid = (
            (
                worker_status == "queued"
                and value["stage"] == "queued"
                and completed == 0
            )
            or (
                worker_status == "running"
                and value["stage"]
                not in {
                    "queued",
                    "complete",
                    "failed",
                    "worker_exit",
                    "cancelled",
                    "checkpoint_wait",
                }
            )
            or (
                worker_status == "waiting"
                and value["stage"] == "checkpoint_wait"
                and completed == 1
            )
            or (
                worker_status == "succeeded"
                and value["stage"] == "complete"
                and completed == total
            )
            or (
                worker_status == "failed"
                and value["stage"] in {"failed", "worker_exit"}
            )
            or (
                worker_status == "cancelled"
                and value["stage"] == "cancelled"
            )
        )
        if not combinations_valid:
            raise AdapterStatusError(
                "ADAPTER_STATUS_INVALID: status, stage, and progress contradict one another"
            )
        if worker_status == "waiting":
            if not (
                record.get("adapter_version") == "1.6.0"
                and record.get("algorithm")
                == {"id": ALGORITHM_ID, "version": "1.6.0"}
            ):
                raise AdapterStatusError(
                    "ADAPTER_STATUS_INVALID: Waiting is not supported by this receipt"
                )
            try:
                checkpoint = read_worker_checkpoint_evidence(
                    self._run_root,
                    job_dir,
                    binding_from_submission_record(record),
                )
            except WorkerControlError as error:
                raise AdapterStatusError(
                    "ADAPTER_STATUS_INVALID: checkpoint Waiting proof is invalid"
                ) from error
            if (
                checkpoint is None
                or checkpoint.state not in {"waiting", "requested"}
                or value.get("checkpoint_id") != checkpoint.checkpoint_id
                or value.get("checkpoint_record_hash")
                != checkpoint.checkpoint_record_hash
            ):
                raise AdapterStatusError(
                    "ADAPTER_STATUS_INVALID: checkpoint Waiting proof changed"
                )
        failure_code = value.get("failure_code")
        if failure_code is not None and (
            worker_status != "failed" or failure_code != "WALL_TIME_EXCEEDED"
        ):
            raise AdapterStatusError(
                "ADAPTER_STATUS_INVALID: Worker failure code is invalid"
            )
        pending_cancel = False
        if worker_status == "cancelled":
            if not _is_supported_managed_record(record):
                raise AdapterStatusError(
                    "ADAPTER_STATUS_INVALID: cancellation proof is unavailable"
                )

            def prove_cancelled() -> None:
                binding = binding_from_submission_record(record)
                evidence = read_worker_cancel_evidence(
                    self._run_root, binding
                )
                attempt = read_worker_attempt_evidence(
                    self._run_root, job_dir, binding
                )
                capability = read_worker_cancel_capability(
                    self._run_root, binding
                )
                current = _read_json_file(
                    job_dir / "status.json", code="ADAPTER_STATUS_INVALID"
                )
                if (
                    current != value
                    or capability is None
                    or evidence.reason != "user_requested"
                    or not evidence.requested
                    or not evidence.acknowledged
                    or attempt is None
                    or attempt.ticket_state != "spawned"
                    or not attempt.ready
                    or attempt.heartbeat_state != "stopped"
                    or capability["worker_pid"] != attempt.ticket_worker_pid
                    or capability["worker_pid"] != attempt.ready_worker_pid
                    or capability["capacity_slot"] != attempt.capacity_slot
                    or capability["capacity_generation"]
                    != attempt.capacity_generation
                ):
                    raise WorkerControlError(
                        "WORKER_CANCEL_INVALID: terminal cancellation is unproven"
                    )

            try:
                if cancellation_fence_held:
                    prove_cancelled()
                else:
                    binding = binding_from_submission_record(record)
                    with hold_idle_execution_fence(self._run_root, binding):
                        prove_cancelled()
            except (FileNotFoundError, WorkerControlError, AdapterStatusError) as error:
                if allow_pending_cancel:
                    pending_cancel = True
                else:
                    raise AdapterStatusError(
                        "ADAPTER_STATUS_INVALID: cancellation is not terminal"
                    ) from error
        pending_timeout = False
        if worker_status == "failed" and failure_code == "WALL_TIME_EXCEEDED":
            if not _is_supported_managed_record(record):
                raise AdapterStatusError(
                    "ADAPTER_STATUS_INVALID: timeout proof is unavailable"
                )

            def prove_timed_out() -> None:
                binding = binding_from_submission_record(record)
                evidence = read_worker_stop_evidence(self._run_root, binding)
                attempt = read_worker_attempt_evidence(
                    self._run_root, job_dir, binding
                )
                capability = read_worker_stop_capability(
                    self._run_root, binding
                )
                current = _read_json_file(
                    job_dir / "status.json", code="ADAPTER_STATUS_INVALID"
                )
                if (
                    current != value
                    or capability is None
                    or evidence.reason != "wall_time_exceeded"
                    or not evidence.requested
                    or not evidence.acknowledged
                    or evidence.wall_time_seconds
                    != record["resources"]["wall_time_seconds"]
                    or attempt is None
                    or attempt.ticket_state != "spawned"
                    or not attempt.ready
                    or attempt.ready_record_hash is None
                    or evidence.ready_record_hash
                    != attempt.ready_record_hash
                    or attempt.heartbeat_state != "stopped"
                    or capability["record_hash"]
                    != evidence.capability_record_hash
                    or capability["worker_pid"] != attempt.ticket_worker_pid
                    or capability["worker_pid"] != attempt.ready_worker_pid
                    or capability["capacity_slot"] != attempt.capacity_slot
                    or capability["capacity_generation"]
                    != attempt.capacity_generation
                    or capability["wall_time_seconds"]
                    != record["resources"]["wall_time_seconds"]
                ):
                    raise WorkerControlError(
                        "WORKER_STOP_INVALID: terminal timeout is unproven"
                    )

            try:
                if timeout_fence_held:
                    prove_timed_out()
                else:
                    binding = binding_from_submission_record(record)
                    with hold_idle_execution_fence(self._run_root, binding):
                        prove_timed_out()
            except (FileNotFoundError, WorkerControlError, AdapterStatusError) as error:
                if allow_pending_timeout:
                    pending_timeout = True
                else:
                    raise AdapterStatusError(
                        "ADAPTER_STATUS_INVALID: timeout is not terminal"
                    ) from error
        _parse_timestamp(value["updated_at"], code="ADAPTER_STATUS_INVALID")
        pending_stop = pending_cancel or pending_timeout
        status = "Running" if pending_stop else mapping[worker_status]
        controlled_messages = {
            "Queued": "FWI job is queued",
            "Running": f"FWI job is running ({value['stage']})",
            "Waiting": "FWI job is waiting at a durable checkpoint",
            "Succeeded": "FWI job succeeded",
            "Failed": "FWI Worker reported a failure",
            "Cancelled": "FWI job was cancelled",
        }
        return AdapterStatus(
            job_id=record["job_id"],
            task_id=record["task_id"],
            node_id=record["node_id"],
            status=status,
            stage=value["stage"],
            completed=completed,
            total=total,
            # Worker exception text is retained in the private run directory.
            # It is never promoted into a standard event/status surface because
            # legacy exceptions can contain server-side paths.
            message=controlled_messages[status],
            updated_at=value["updated_at"],
            terminal=(
                not pending_stop
                and status in {"Succeeded", "Failed", "Cancelled"}
            ),
        )

    def status(self, handle: AdapterHandle) -> AdapterStatus:
        record = self._record_for_handle(handle)
        return self._status_for_record(record)

    def _purge_job_directory(self, record: Mapping[str, Any]) -> bool:
        """Delete the receipt-bound direct child using only held directory FDs."""

        job_id = record.get("job_id")
        if not isinstance(job_id, str) or JOB_ID.fullmatch(job_id) is None:
            raise AdapterPurgeError(
                "PURGE_RECEIPT_INVALID: job identity is malformed"
            )
        root = _validate_run_root(self._run_root, create=False)
        root_fd = -1
        job_fd = -1
        try:
            root_fd = _open_directory_fd(root)
            root_status = os.fstat(root_fd)
            if (
                not stat.S_ISDIR(root_status.st_mode)
                or root_status.st_uid != os.geteuid()
                or stat.S_IMODE(root_status.st_mode) & 0o022
            ):
                raise OSError("run root ownership or permissions are unsafe")
            try:
                job_fd = os.open(job_id, DIRECTORY_OPEN_FLAGS, dir_fd=root_fd)
            except FileNotFoundError:
                return False
            job_status = os.fstat(job_fd)
            if (
                not stat.S_ISDIR(job_status.st_mode)
                or job_status.st_uid != os.geteuid()
                or stat.S_IMODE(job_status.st_mode) & 0o022
                or job_status.st_dev != root_status.st_dev
            ):
                raise OSError("job directory identity or permissions are unsafe")
            _unlink_directory_contents(
                job_fd, expected_device=root_status.st_dev
            )
            os.close(job_fd)
            job_fd = -1
            os.rmdir(job_id, dir_fd=root_fd)
            os.fsync(root_fd)
            return True
        except AdapterError:
            raise
        except OSError as error:
            raise AdapterPurgeError(
                "PURGE_LOCAL_RUN_UNAVAILABLE: controlled deletion failed"
            ) from error
        finally:
            if job_fd >= 0:
                os.close(job_fd)
            if root_fd >= 0:
                os.close(root_fd)

    def purge_retry_exhausted(
        self,
        *,
        exhaustion: Mapping[str, Any],
        purge_id: str,
        task_id: str,
        node_id: str,
        plan_hash: str,
        idempotency_key: str,
        project_id: str,
        principal_id: str,
        algorithm: Mapping[str, Any],
        dataset: Mapping[str, Any],
        task_type: str,
        parameters: Mapping[str, Any],
        resources: Mapping[str, Any],
    ) -> AdapterPurgeResult:
        """Delete an exact two-attempt stopped chain with no launched handle."""

        if not isinstance(purge_id, str) or OPAQUE_ID.fullmatch(purge_id) is None:
            raise AdapterPurgeError(
                "PURGE_ID_INVALID: purge_id must be a v1 opaque identifier"
            )
        proof = self._validate_retry_exhaustion_cleanup_document(exhaustion)
        if (
            proof["purge_id"] != purge_id
            or proof["task_id"] != task_id
            or proof["project_id"] != project_id
            or proof["principal_id"] != principal_id
        ):
            raise AdapterPurgeError(
                "WORKER_RETRY_EXHAUSTION_PURGE_INVALID: cleanup scope changed"
            )
        (
            validated,
            submission_id,
            request_payload,
            request_hash,
            index_path,
            lock_path,
        ) = self._retry_request_material(
            task_id=task_id,
            node_id=node_id,
            plan_hash=plan_hash,
            idempotency_key=idempotency_key,
            project_id=project_id,
            principal_id=principal_id,
            algorithm=algorithm,
            dataset=dataset,
            task_type=task_type,
            parameters=parameters,
            resources=resources,
        )
        proof_evidence = proof["evidence"]
        worker_exit_lineage = proof["schema_version"] == "1.1.0"
        if (
            proof_evidence.get("submission_id") != submission_id
            or proof_evidence.get("request_hash") != request_hash
        ):
            raise AdapterPurgeError(
                "WORKER_RETRY_EXHAUSTION_PURGE_INVALID: cleanup request changed"
            )

        with self._lock_submission(
            lock_path, create=False, timeout_seconds=5.0
        ):
            if not index_path.exists() and not index_path.is_symlink():
                raise AdapterPurgeError(
                    "WORKER_RETRY_EXHAUSTION_PURGE_INVALID: private submission is missing"
                )
            record = self._read_submission(index_path)
            if (
                record["schema_version"]
                != ("1.3.0" if worker_exit_lineage else "1.2.0")
                or record["adapter_version"] != validated.algorithm["version"]
                or record["algorithm"] != validated.algorithm
                or record["submission_id"] != submission_id
                or record["task_id"] != task_id
                or record["project_id"] != project_id
                or record["principal_id"] != principal_id
                or record["request_hash"] != request_hash
                or self._record_request_payload(record) != request_payload
                or record["launch_state"] not in {"failed", "purging", "purged"}
            ):
                raise AdapterPurgeError(
                    "WORKER_RETRY_EXHAUSTION_PURGE_INVALID: private submission changed"
                )
            self._validate_fingerprint(record["fingerprint"], validated=validated)
            current = binding_from_submission_record(record)
            history = record.get("attempt_history")
            authorization = self._validate_retry_authorization_document(
                record.get("retry_authorization"),
                expected_failure_kind=(
                    "worker_exit"
                    if worker_exit_lineage
                    else "pre_running_launch_failure"
                ),
            )
            if not isinstance(history, list) or len(history) != 1:
                raise AdapterPurgeError(
                    "WORKER_RETRY_EXHAUSTION_PURGE_INVALID: retry history changed"
                )
            prior_record = history[0]
            try:
                prior = binding_from_submission_record(prior_record)
            except WorkerControlError as error:
                raise AdapterPurgeError(
                    "WORKER_RETRY_EXHAUSTION_PURGE_INVALID: prior attempt changed"
                ) from error
            if (
                current.attempt_number != 2
                or current.attempt_id != proof["attempt_id"]
                or current.submission_id != submission_id
                or current.request_hash != request_hash
                or record["job_id"] != self._expected_job_id(record)
                or prior.attempt_number != 1
                or prior.attempt_id != proof["previous_attempt_id"]
                or prior.submission_id != submission_id
                or prior.request_hash != request_hash
                or prior.job_id == current.job_id
                or authorization["intent_id"] != proof["intent_id"]
                or authorization["previous_attempt_id"] != prior.attempt_id
                or authorization["previous_observation_sequence"]
                != proof["previous_observation_sequence"]
                or authorization["private_proof_hash"]
                != proof["previous_private_proof_hash"]
                or authorization["authorized_at"] != proof["retry_reserved_at"]
                or (
                    prior_record[
                        "worker_exit" if worker_exit_lineage else "launch_failure"
                    ]["record_hash" if worker_exit_lineage else "proof_hash"]
                    != proof["previous_private_proof_hash"]
                )
                or record.get("launch_failure", {}).get("proof_hash")
                != proof["private_proof_hash"]
            ):
                raise AdapterPurgeError(
                    "WORKER_RETRY_EXHAUSTION_PURGE_INVALID: retry lineage changed"
                )
            if worker_exit_lineage:
                self._validate_worker_exit_document(
                    prior_record.get("worker_exit"), prior
                )
            else:
                self._validate_failure_document(
                    prior_record.get("launch_failure"), prior
                )
            self._validate_failure_document(record.get("launch_failure"), current)

            launch_state = record["launch_state"]
            replayed = launch_state != "failed"
            if launch_state in {"purging", "purged"}:
                if record.get("purge_id") != purge_id:
                    raise AdapterIdempotencyConflict(
                        "PURGE_IDEMPOTENCY_CONFLICT: retry exhaustion is bound to another purge"
                    )
                if launch_state == "purged":
                    return AdapterPurgeResult(
                        task_id=task_id,
                        purge_id=purge_id,
                        local_run_state="deleted",
                        replayed=True,
                    )

            def read_exact_failure(
                attempt_record: Mapping[str, Any],
                binding: LaunchAttemptBinding,
            ) -> WorkerAttemptEvidence:
                job_dir = self._run_root / binding.job_id
                if not job_dir.exists() and not job_dir.is_symlink():
                    raise AdapterPurgeError(
                        "WORKER_RETRY_EXHAUSTION_PURGE_INVALID: stopped attempt is missing"
                    )
                try:
                    evidence = read_pre_running_attempt_evidence(
                        self._run_root, job_dir, binding
                    )
                except (FileNotFoundError, WorkerControlError) as error:
                    raise AdapterPurgeError(
                        "WORKER_RETRY_EXHAUSTION_PURGE_INVALID: stopped attempt proof is unavailable"
                    ) from error
                expected_failure = attempt_record.get("launch_failure")
                if (
                    evidence is None
                    or evidence.ticket_state != "failed"
                    or evidence.ticket_worker_pid is not None
                    or evidence.ready
                    or evidence.heartbeat_record_hash is not None
                    or self._failure_proof(attempt_record, evidence)
                    != expected_failure
                ):
                    raise AdapterPurgeError(
                        "WORKER_RETRY_EXHAUSTION_PURGE_INVALID: stopped attempt proof changed"
                    )
                return evidence

            def read_exact_worker_exit(
                attempt_record: Mapping[str, Any],
                binding: LaunchAttemptBinding,
            ) -> WorkerExitEvidence:
                job_dir = self._run_root / binding.job_id
                if not job_dir.exists() and not job_dir.is_symlink():
                    raise AdapterPurgeError(
                        "WORKER_RETRY_EXHAUSTION_PURGE_INVALID: stopped attempt is missing"
                    )
                try:
                    evidence = read_worker_exit_evidence(
                        self._run_root, job_dir, binding
                    )
                except (FileNotFoundError, WorkerControlError) as error:
                    raise AdapterPurgeError(
                        "WORKER_RETRY_EXHAUSTION_PURGE_INVALID: Worker-exit proof is unavailable"
                    ) from error
                expected_exit = attempt_record.get("worker_exit")
                if (
                    evidence.as_dict() != expected_exit
                    or evidence.record_hash
                    != proof["previous_private_proof_hash"]
                ):
                    raise AdapterPurgeError(
                        "WORKER_RETRY_EXHAUSTION_PURGE_INVALID: Worker-exit proof changed"
                    )
                return evidence

            try:
                # Both attempts share one stable submission execution lock.
                # The first transition checks both complete proofs before its
                # tombstone or any delete.  Once that same-purge tombstone is
                # durable, replay trusts the already-validated immutable
                # record/token and may finish partially deleted directories.
                prior_exit = (
                    read_exact_worker_exit(prior_record, prior)
                    if launch_state == "failed" and worker_exit_lineage
                    else None
                )
                with hold_idle_execution_fence(self._run_root, current):
                    if launch_state == "failed":
                        prior_evidence = (
                            None
                            if worker_exit_lineage
                            else read_exact_failure(prior_record, prior)
                        )
                        current_evidence = read_exact_failure(record, current)
                        if (
                            current_evidence.as_dict() != proof_evidence
                            or _sha256_document(current_evidence.as_dict())
                            != proof["evidence_hash"]
                        ):
                            raise AdapterPurgeError(
                                "WORKER_RETRY_EXHAUSTION_PURGE_INVALID: SQLite evidence differs from private state"
                            )
                        if (
                            prior_exit is not None
                            and prior_exit.record_hash
                            != proof["previous_private_proof_hash"]
                        ) or (
                            prior_evidence is not None
                            and self._failure_proof(prior_record, prior_evidence)[
                                "proof_hash"
                            ]
                            != proof["previous_private_proof_hash"]
                        ):
                            raise AdapterPurgeError(
                                "WORKER_RETRY_EXHAUSTION_PURGE_INVALID: prior private proof changed"
                            )
                        record["launch_state"] = "purging"
                        record["purge_id"] = purge_id
                        self._write_submission(index_path, record)

                    for binding in (prior, current):
                        self._purge_job_directory({"job_id": binding.job_id})
                        purge_worker_cancel_control(self._run_root, binding)
                    record["launch_state"] = "purged"
                    self._write_submission(index_path, record)
            except WorkerControlError as error:
                if error.code == "WORKER_ATTEMPT_BUSY":
                    raise AdapterPurgeError(
                        "PURGE_WORKER_STILL_ACTIVE: fenced Worker has not released its execution lease"
                    ) from error
                raise AdapterPurgeError(
                    "PURGE_WORKER_FENCE_INVALID: execution fence or control cleanup is unavailable"
                ) from error
            return AdapterPurgeResult(
                task_id=task_id,
                purge_id=purge_id,
                local_run_state="deleted",
                replayed=replayed,
            )

    def purge(
        self, handle: AdapterHandle, *, purge_id: str
    ) -> AdapterPurgeResult:
        """Permanently remove one receipt-bound terminal local Worker run."""

        if not isinstance(purge_id, str) or OPAQUE_ID.fullmatch(purge_id) is None:
            raise AdapterPurgeError(
                "PURGE_ID_INVALID: purge_id must be a v1 opaque identifier"
            )
        purge_states = frozenset({"launched", "purging", "purged"})
        # Validate before deriving a control filename.  The record is re-read
        # after taking the per-submission lock, which is the authoritative view.
        self._record_for_handle(
            handle, allowed_launch_states=purge_states
        )
        submissions, locks = self._control_paths()
        index_name = handle.submission_id.removeprefix("submission-") + ".json"
        index_path = submissions / index_name
        lock_path = locks / (index_name + ".lock")
        with self._lock_submission(lock_path):
            record = self._record_for_handle(
                handle, allowed_launch_states=purge_states
            )
            if "launch_failure" in record:
                raise AdapterPurgeError(
                    "PURGE_RECEIPT_INVALID: retry exhaustion requires Store proof"
                )
            launch_state = record["launch_state"]
            replayed = launch_state != "launched"
            if launch_state in {"purging", "purged"}:
                if record.get("purge_id") != purge_id:
                    raise AdapterIdempotencyConflict(
                        "PURGE_IDEMPOTENCY_CONFLICT: receipt is bound to another purge"
                    )
                if launch_state == "purged":
                    return AdapterPurgeResult(
                        task_id=handle.task_id,
                        purge_id=purge_id,
                        local_run_state="deleted",
                        replayed=True,
                    )
            try:
                fence = contextlib.nullcontext()
                managed_bindings: list[LaunchAttemptBinding] = []
                if record["schema_version"] in {"1.1.0", "1.2.0", "1.3.0"}:
                    binding = binding_from_submission_record(record)
                    managed_bindings = [
                        *(
                            binding_from_submission_record(item)
                            for item in record.get("attempt_history", [])
                        ),
                        binding,
                    ]
                    fence = hold_idle_execution_fence(self._run_root, binding)
                with fence:
                    if launch_state == "launched":
                        observed = self._status_for_record(
                            record,
                            cancellation_fence_held=True,
                            timeout_fence_held=True,
                        )
                        if not observed.terminal or observed.status not in {
                            "Succeeded",
                            "Failed",
                            "Cancelled",
                        }:
                            raise AdapterPurgeError(
                                "PURGE_REQUIRES_TERMINAL_STATUS: Worker is not succeeded or failed"
                            )
                        record["launch_state"] = "purging"
                        record["purge_id"] = purge_id
                        self._write_submission(index_path, record)

                    # A missing directory is accepted only after the durable
                    # purging tombstone exists.  Holding the stable execution
                    # fence prevents a terminal heartbeat from racing delete.
                    if managed_bindings:
                        for managed_binding in managed_bindings:
                            self._purge_job_directory(
                                {"job_id": managed_binding.job_id}
                            )
                            purge_worker_cancel_control(
                                self._run_root,
                                managed_binding,
                            )
                    else:
                        self._purge_job_directory(record)
                    record["launch_state"] = "purged"
                    self._write_submission(index_path, record)
            except WorkerControlError as error:
                if error.code == "WORKER_ATTEMPT_BUSY":
                    raise AdapterPurgeError(
                        "PURGE_WORKER_STILL_ACTIVE: fenced Worker has not released its execution lease"
                    ) from error
                raise AdapterPurgeError(
                    "PURGE_WORKER_FENCE_INVALID: execution fence or control cleanup is unavailable"
                ) from error
            return AdapterPurgeResult(
                task_id=handle.task_id,
                purge_id=purge_id,
                local_run_state="deleted",
                replayed=replayed,
            )

    def supports_exact_timeout(
        self, handle: AdapterHandle, attempt_id: str
    ) -> dict[str, Any] | None:
        """Return a path-free proof that the exact live Worker supports timeout."""

        if (
            not isinstance(attempt_id, str)
            or MANAGED_ATTEMPT_ID.fullmatch(attempt_id) is None
        ):
            raise AdapterValidationError(
                "TIMEOUT_REQUEST_INVALID",
                ["attempt_id is not an exact managed attempt identifier"],
            )
        initial = self._record_for_handle(handle)
        if not _is_supported_managed_control_record(handle, initial):
            return None
        _, locks = self._control_paths()
        index_name = handle.submission_id.removeprefix("submission-") + ".json"
        lock_path = locks / (index_name + ".lock")
        with self._lock_submission(lock_path, timeout_seconds=5.0):
            record = self._record_for_handle(handle)
            if not _is_supported_managed_control_record(handle, record):
                return None
            try:
                binding = binding_from_submission_record(record)
                if binding.attempt_id != attempt_id:
                    return None
                job_dir = self._job_directory(record)
                attempt = read_worker_attempt_evidence(
                    self._run_root, job_dir, binding
                )
                capability = read_worker_stop_capability(
                    self._run_root, binding
                )
                if (
                    attempt is None
                    or attempt.ticket_state != "spawned"
                    or not attempt.ready
                    or attempt.heartbeat_state not in {"running", "waiting"}
                    or capability is None
                    or capability.get("supported_reasons")
                    != ["user_requested", "wall_time_exceeded"]
                    or capability.get("wall_time_seconds")
                    != record["resources"]["wall_time_seconds"]
                ):
                    return None
                if (
                    capability["worker_pid"] != attempt.ticket_worker_pid
                    or capability["worker_pid"] != attempt.ready_worker_pid
                    or capability["capacity_slot"] != attempt.capacity_slot
                    or capability["capacity_generation"]
                    != attempt.capacity_generation
                ):
                    raise WorkerControlError(
                        "WORKER_STOP_INVALID: capability Worker identity changed"
                    )
                stop_evidence = read_worker_stop_evidence(
                    self._run_root, binding
                )
                if (
                    stop_evidence.requested
                    and stop_evidence.ready_record_hash
                    != attempt.ready_record_hash
                ):
                    raise WorkerControlError(
                        "WORKER_STOP_INVALID: timeout ready receipt changed"
                    )
            except (FileNotFoundError, WorkerControlError) as error:
                raise AdapterHandleError(
                    "ADAPTER_TIMEOUT_INVALID: exact timeout capability is invalid"
                ) from error
            payload = {
                "schema_version": "2.0.0",
                "attempt_id": binding.attempt_id,
                "binding_hash": binding.binding_hash,
                "capability_record_hash": capability["record_hash"],
                "supported_reasons": [
                    "user_requested",
                    "wall_time_exceeded",
                ],
                "private_schema_version": record["schema_version"],
            }
            return {**payload, "proof_hash": _sha256_document(payload)}

    def supports_exact_cancel(
        self, handle: AdapterHandle, *, attempt_id: str
    ) -> bool:
        """Read-only proof that the exact live Worker supports self-cancel.

        This probe never creates a capability or request.  The capability is
        append-only and is published only by the Worker after it has validated
        both inherited kernel fences, so a positive result remains meaningful
        across the Store admission transaction that follows.
        """

        if (
            not isinstance(attempt_id, str)
            or MANAGED_ATTEMPT_ID.fullmatch(attempt_id) is None
        ):
            raise AdapterValidationError(
                "CANCEL_REQUEST_INVALID",
                ["attempt_id is not an exact managed attempt identifier"],
            )
        initial = self._record_for_handle(handle)
        if not _is_supported_managed_control_record(handle, initial):
            return False
        _, locks = self._control_paths()
        index_name = handle.submission_id.removeprefix("submission-") + ".json"
        lock_path = locks / (index_name + ".lock")
        with self._lock_submission(lock_path, timeout_seconds=5.0):
            record = self._record_for_handle(handle)
            if not _is_supported_managed_control_record(handle, record):
                return False
            try:
                binding = binding_from_submission_record(record)
                if binding.attempt_id != attempt_id:
                    return False
                job_dir = self._job_directory(record)
                attempt = read_worker_attempt_evidence(
                    self._run_root, job_dir, binding
                )
                if (
                    attempt is None
                    or attempt.ticket_state != "spawned"
                    or not attempt.ready
                    or attempt.heartbeat_state not in {"running", "waiting"}
                ):
                    return False
                capability = read_worker_cancel_capability(
                    self._run_root, binding
                )
                if capability is None:
                    return False
                if (
                    capability["worker_pid"] != attempt.ticket_worker_pid
                    or capability["worker_pid"] != attempt.ready_worker_pid
                    or capability["capacity_slot"] != attempt.capacity_slot
                    or capability["capacity_generation"]
                    != attempt.capacity_generation
                ):
                    raise WorkerControlError(
                        "WORKER_CANCEL_INVALID: capability Worker identity changed"
                    )
                # Validate any already-published request/ack chain as well;
                # capability probing must fail closed on partial corruption.
                read_worker_cancel_evidence(self._run_root, binding)
            except (FileNotFoundError, WorkerControlError) as error:
                raise AdapterHandleError(
                    "ADAPTER_CANCEL_INVALID: exact cancellation capability is invalid"
                ) from error
            return True

    def cancel(
        self,
        handle: AdapterHandle,
        *,
        cancel_id: str | None = None,
        attempt_id: str | None = None,
        reason: str = "user_requested",
    ) -> AdapterCancelResult | AdapterManagedCancelProof:
        """Request or finalize one exact current managed-Worker cancellation.

        Persisted Worker PIDs are deliberately never signalled.  The Adapter
        publishes an append-only request which only the exact Worker holding the
        inherited kernel fences can acknowledge.  Terminal Cancelled is written
        only while the same stable execution fence is proven idle.
        """

        if cancel_id is None:
            # Preserve the original six-method compatibility surface for callers
            # that have not supplied the durable P2 cancellation identity.
            self._record_for_handle(handle)
            return AdapterCancelResult(
                supported=False,
                accepted=False,
                code="CANCEL_NOT_SUPPORTED_WITHOUT_ID",
                status="Unsupported",
            )
        if (
            OPAQUE_ID.fullmatch(cancel_id) is None
            or not isinstance(attempt_id, str)
            or MANAGED_ATTEMPT_ID.fullmatch(attempt_id) is None
            or reason != "user_requested"
        ):
            raise AdapterValidationError(
                "CANCEL_REQUEST_INVALID",
                ["cancel_id, attempt_id, or cancellation reason is invalid"],
            )
        initial = self._record_for_handle(handle)
        if not _is_supported_managed_control_record(handle, initial):
            return _managed_cancel_proof(
                task_id=handle.task_id,
                cancel_id=cancel_id,
                reason=reason,
                state="deferred",
                code="CANCEL_MANAGED_ATTEMPT_UNAVAILABLE",
                attempt_id=attempt_id,
                evidence=None,
                terminal_status=None,
                replayed=False,
                receipt_record_hash=initial["record_hash"],
            )

        submissions, locks = self._control_paths()
        index_name = handle.submission_id.removeprefix("submission-") + ".json"
        index_path = submissions / index_name
        lock_path = locks / (index_name + ".lock")
        with self._lock_submission(lock_path, timeout_seconds=5.0):
            record = self._record_for_handle(handle)
            if record["schema_version"] not in {"1.1.0", "1.2.0", "1.3.0"}:
                return _managed_cancel_proof(
                    task_id=handle.task_id,
                    cancel_id=cancel_id,
                    reason=reason,
                    state="deferred",
                    code="CANCEL_MANAGED_ATTEMPT_UNAVAILABLE",
                    attempt_id=attempt_id,
                    evidence=None,
                    terminal_status=None,
                    replayed=False,
                    receipt_record_hash=record["record_hash"],
                )
            try:
                binding = binding_from_submission_record(record)
                job_dir = self._job_directory(record)
            except WorkerControlError as error:
                raise AdapterHandleError(
                    "ADAPTER_CANCEL_INVALID: managed attempt evidence is invalid"
                ) from error
            try:
                observed = self._status_for_record(
                    record, allow_pending_cancel=True
                )
            except AdapterStatusError as error:
                if _is_orphaned_checkpoint_waiting(error):
                    observed = None
                else:
                    raise AdapterHandleError(
                        "ADAPTER_CANCEL_INVALID: managed attempt evidence is invalid"
                    ) from error
            except WorkerControlError as error:
                raise AdapterHandleError(
                    "ADAPTER_CANCEL_INVALID: managed attempt evidence is invalid"
                ) from error
            if binding.attempt_id != attempt_id:
                return _managed_cancel_proof(
                    task_id=handle.task_id,
                    cancel_id=cancel_id,
                    reason=reason,
                    state="deferred",
                    code="CANCEL_ATTEMPT_MISMATCH",
                    attempt_id=attempt_id,
                    evidence=None,
                    terminal_status=None,
                    replayed=False,
                    receipt_record_hash=record["record_hash"],
                )

            # A natural terminal written before request publication always
            # wins, and needs no cancellation capability.  Raw Cancelled is
            # not included here: _status_for_record proves its exact
            # request/ack/stopped/idle chain before exposing it.
            if (
                observed is not None
                and observed.terminal
                and observed.status in {"Succeeded", "Failed"}
            ):
                terminal_evidence: WorkerCancelEvidence | None = None
                try:
                    terminal_evidence = read_worker_cancel_evidence(
                        self._run_root, binding
                    )
                except WorkerControlError as error:
                    if error.code != "WORKER_CANCEL_UNSUPPORTED":
                        raise AdapterHandleError(
                            "ADAPTER_CANCEL_INVALID: cancellation control is invalid"
                        ) from error
                except FileNotFoundError as error:
                    raise AdapterHandleError(
                        "ADAPTER_CANCEL_INVALID: cancellation control is invalid"
                    ) from error
                if terminal_evidence is not None and terminal_evidence.requested and (
                    terminal_evidence.cancel_id != cancel_id
                    or terminal_evidence.reason != reason
                ):
                    raise AdapterIdempotencyConflict(
                        "CANCEL_IDEMPOTENCY_CONFLICT: attempt has another cancellation"
                    )
                return _managed_cancel_proof(
                    task_id=handle.task_id,
                    cancel_id=cancel_id,
                    reason=reason,
                    state="terminal_won",
                    code="CANCEL_TERMINAL_WON",
                    attempt_id=binding.attempt_id,
                    evidence=terminal_evidence,
                    terminal_status=observed.status,
                    replayed=(
                        terminal_evidence is not None
                        and terminal_evidence.requested
                    ),
                    receipt_record_hash=record["record_hash"],
                )

            try:
                existing_cancel = read_worker_cancel_evidence(
                    self._run_root, binding
                )
            except WorkerControlError as error:
                if error.code == "WORKER_CANCEL_UNSUPPORTED":
                    return _managed_cancel_proof(
                        task_id=handle.task_id,
                        cancel_id=cancel_id,
                        reason=reason,
                        state="deferred",
                        code="CANCEL_WORKER_CAPABILITY_UNAVAILABLE",
                        attempt_id=binding.attempt_id,
                        evidence=None,
                        terminal_status=None,
                        replayed=False,
                        receipt_record_hash=record["record_hash"],
                    )
                raise AdapterHandleError(
                    "ADAPTER_CANCEL_INVALID: cancellation control is invalid"
                ) from error
            except FileNotFoundError as error:
                raise AdapterHandleError(
                    "ADAPTER_CANCEL_INVALID: cancellation control is invalid"
                ) from error
            if existing_cancel.requested and (
                existing_cancel.cancel_id != cancel_id
                or existing_cancel.reason != reason
            ):
                raise AdapterIdempotencyConflict(
                    "CANCEL_IDEMPOTENCY_CONFLICT: attempt has another cancellation"
                )

            if observed is not None and observed.terminal:
                if observed.status == "Cancelled":
                    if (
                        existing_cancel is None
                        or existing_cancel.cancel_id != cancel_id
                        or not existing_cancel.acknowledged
                    ):
                        return _managed_cancel_proof(
                            task_id=handle.task_id,
                            cancel_id=cancel_id,
                            reason=reason,
                            state="deferred",
                            code="CANCEL_TERMINAL_PROOF_UNAVAILABLE",
                            attempt_id=binding.attempt_id,
                            evidence=existing_cancel,
                            terminal_status="Cancelled",
                            replayed=True,
                            receipt_record_hash=record["record_hash"],
                        )
                    return _managed_cancel_proof(
                        task_id=handle.task_id,
                        cancel_id=cancel_id,
                        reason=reason,
                        state="cancelled",
                        code="CANCEL_COMPLETED",
                        attempt_id=binding.attempt_id,
                        evidence=existing_cancel,
                        terminal_status="Cancelled",
                        replayed=True,
                        receipt_record_hash=record["record_hash"],
                    )
                raise AdapterHandleError(
                    "ADAPTER_CANCEL_INVALID: terminal cancellation state is invalid"
                )

            try:
                attempt = read_worker_attempt_evidence(
                    self._run_root, job_dir, binding
                )
            except (FileNotFoundError, WorkerControlError) as error:
                raise AdapterHandleError(
                    "ADAPTER_CANCEL_INVALID: Worker attempt evidence is invalid"
                ) from error
            if (
                attempt is None
                or attempt.ticket_state != "spawned"
                or not attempt.ready
                or (
                    attempt.heartbeat_state
                    not in (
                        {"running", "waiting", "stopped"}
                        if existing_cancel.requested
                        else {"running", "waiting"}
                    )
                )
            ):
                return _managed_cancel_proof(
                    task_id=handle.task_id,
                    cancel_id=cancel_id,
                    reason=reason,
                    state="deferred",
                    code="CANCEL_WORKER_NOT_RUNNING",
                    attempt_id=binding.attempt_id,
                    evidence=existing_cancel,
                    terminal_status=None,
                    replayed=existing_cancel is not None
                    and existing_cancel.requested,
                    receipt_record_hash=record["record_hash"],
                )
            capability = read_worker_cancel_capability(
                self._run_root, binding
            )
            if (
                capability is None
                or capability["worker_pid"] != attempt.ticket_worker_pid
                or capability["worker_pid"] != attempt.ready_worker_pid
                or capability["capacity_slot"] != attempt.capacity_slot
                or capability["capacity_generation"]
                != attempt.capacity_generation
            ):
                raise AdapterHandleError(
                    "ADAPTER_CANCEL_INVALID: capability Worker identity changed"
                )

            try:
                cancel_evidence, replayed = request_worker_cancel(
                    self._run_root,
                    binding,
                    cancel_id=cancel_id,
                    reason=reason,
                    requested_at=self._clock(),
                )
            except WorkerControlError as error:
                if error.code == "WORKER_CANCEL_CONFLICT":
                    raise AdapterIdempotencyConflict(
                        "CANCEL_IDEMPOTENCY_CONFLICT: attempt has another cancellation"
                    ) from error
                raise AdapterHandleError(
                    "ADAPTER_CANCEL_INVALID: cancellation control is invalid"
                ) from error

            try:
                with hold_idle_execution_fence(self._run_root, binding):
                    # The Worker can finish naturally after request publication.
                    # Re-read terminal status inside the idle fence so success or
                    # failure can never be overwritten by cancellation.
                    try:
                        terminal = self._status_for_record(
                            record, cancellation_fence_held=True
                        )
                    except AdapterStatusError as error:
                        if _is_orphaned_checkpoint_waiting(
                            error, idle_fence_held=True
                        ):
                            terminal = None
                        else:
                            raise AdapterHandleError(
                                "ADAPTER_CANCEL_INVALID: terminal status is invalid"
                            ) from error
                    if (
                        terminal is not None
                        and terminal.terminal
                        and terminal.status != "Cancelled"
                    ):
                        return _managed_cancel_proof(
                            task_id=handle.task_id,
                            cancel_id=cancel_id,
                            reason=reason,
                            state="terminal_won",
                            code="CANCEL_TERMINAL_WON",
                            attempt_id=binding.attempt_id,
                            evidence=cancel_evidence,
                            terminal_status=terminal.status,
                            replayed=replayed,
                            receipt_record_hash=record["record_hash"],
                        )
                    cancel_evidence = read_worker_cancel_evidence(
                        self._run_root, binding
                    )
                    attempt = read_worker_attempt_evidence(
                        self._run_root, job_dir, binding
                    )
                    capability = read_worker_cancel_capability(
                        self._run_root, binding
                    )
                    if (
                        not cancel_evidence.acknowledged
                        or cancel_evidence.cancel_id != cancel_id
                        or attempt is None
                        or attempt.heartbeat_state != "stopped"
                        or capability is None
                        or capability["worker_pid"]
                        != attempt.ticket_worker_pid
                        or capability["worker_pid"]
                        != attempt.ready_worker_pid
                        or capability["capacity_slot"]
                        != attempt.capacity_slot
                        or capability["capacity_generation"]
                        != attempt.capacity_generation
                    ):
                        return _managed_cancel_proof(
                            task_id=handle.task_id,
                            cancel_id=cancel_id,
                            reason=reason,
                            state="deferred",
                            code="CANCEL_EXIT_UNPROVEN",
                            attempt_id=binding.attempt_id,
                            evidence=cancel_evidence,
                            terminal_status=None,
                            replayed=replayed,
                            receipt_record_hash=record["record_hash"],
                        )
                    value = _read_json_file(
                        job_dir / "status.json", code="WORKER_STATUS_INVALID"
                    )
                    if value.get("status") not in {
                        "queued",
                        "running",
                        "waiting",
                        "cancelled",
                    }:
                        return _managed_cancel_proof(
                            task_id=handle.task_id,
                            cancel_id=cancel_id,
                            reason=reason,
                            state="terminal_won",
                            code="CANCEL_TERMINAL_WON",
                            attempt_id=binding.attempt_id,
                            evidence=cancel_evidence,
                            terminal_status=self._status_for_record(
                                record, cancellation_fence_held=True
                            ).status,
                            replayed=replayed,
                            receipt_record_hash=record["record_hash"],
                        )
                    if value.get("status") != "cancelled":
                        value.update(
                            {
                                "status": "cancelled",
                                "stage": "cancelled",
                                "message": "FWI Worker cancellation completed",
                                "updated_at": self._clock(),
                            }
                        )
                        _atomic_write_json(job_dir / "status.json", value)
                    return _managed_cancel_proof(
                        task_id=handle.task_id,
                        cancel_id=cancel_id,
                        reason=reason,
                        state="cancelled",
                        code="CANCEL_COMPLETED",
                        attempt_id=binding.attempt_id,
                        evidence=cancel_evidence,
                        terminal_status="Cancelled",
                        replayed=replayed,
                        receipt_record_hash=record["record_hash"],
                    )
            except WorkerControlError as error:
                if error.code != "WORKER_ATTEMPT_BUSY":
                    raise AdapterHandleError(
                        "ADAPTER_CANCEL_INVALID: execution fence is invalid"
                    ) from error
                return _managed_cancel_proof(
                    task_id=handle.task_id,
                    cancel_id=cancel_id,
                    reason=reason,
                    state="pending" if replayed else "requested",
                    code=("CANCEL_PENDING" if replayed else "CANCEL_REQUESTED"),
                    attempt_id=binding.attempt_id,
                    evidence=cancel_evidence,
                    terminal_status=None,
                    replayed=replayed,
                    receipt_record_hash=record["record_hash"],
                )

    def timeout(
        self,
        handle: AdapterHandle,
        timeout_id: str,
        attempt_id: str,
        wall_time_seconds: int,
        started_at: str,
        deadline_at: str,
    ) -> AdapterManagedTimeoutProof:
        """Request or finalize one exact v2 Worker wall-time failure."""

        if (
            not isinstance(timeout_id, str)
            or OPAQUE_ID.fullmatch(timeout_id) is None
            or not isinstance(attempt_id, str)
            or MANAGED_ATTEMPT_ID.fullmatch(attempt_id) is None
            or type(wall_time_seconds) is not int
            or not 1 <= wall_time_seconds <= 86_400
        ):
            raise AdapterValidationError(
                "TIMEOUT_REQUEST_INVALID",
                ["timeout_id, attempt_id, or wall time is invalid"],
            )
        try:
            started = _parse_timestamp(started_at, code="TIMEOUT_REQUEST_INVALID")
            deadline = _parse_timestamp(deadline_at, code="TIMEOUT_REQUEST_INVALID")
        except AdapterStatusError as error:
            raise AdapterValidationError(
                "TIMEOUT_REQUEST_INVALID", ["timeout window is invalid"]
            ) from error
        if deadline - started != timedelta(seconds=wall_time_seconds):
            raise AdapterValidationError(
                "TIMEOUT_REQUEST_INVALID",
                ["deadline must equal started_at plus wall_time_seconds"],
            )

        initial = self._record_for_handle(handle)
        if not _is_supported_managed_control_record(handle, initial):
            return _managed_timeout_proof(
                task_id=handle.task_id,
                timeout_id=timeout_id,
                state="deferred",
                code="TIMEOUT_MANAGED_ATTEMPT_UNAVAILABLE",
                attempt_id=attempt_id,
                wall_time_seconds=wall_time_seconds,
                started_at=started_at,
                deadline_at=deadline_at,
                ready_record_hash=None,
                evidence=None,
                terminal_status=None,
                terminal_failure_code=None,
                replayed=False,
                receipt_record_hash=initial["record_hash"],
            )

        _, locks = self._control_paths()
        index_name = handle.submission_id.removeprefix("submission-") + ".json"
        lock_path = locks / (index_name + ".lock")
        with self._lock_submission(lock_path, timeout_seconds=5.0):
            record = self._record_for_handle(handle)
            if record["schema_version"] not in {"1.1.0", "1.2.0", "1.3.0"}:
                return _managed_timeout_proof(
                    task_id=handle.task_id,
                    timeout_id=timeout_id,
                    state="deferred",
                    code="TIMEOUT_MANAGED_ATTEMPT_UNAVAILABLE",
                    attempt_id=attempt_id,
                    wall_time_seconds=wall_time_seconds,
                    started_at=started_at,
                    deadline_at=deadline_at,
                    ready_record_hash=None,
                    evidence=None,
                    terminal_status=None,
                    terminal_failure_code=None,
                    replayed=False,
                    receipt_record_hash=record["record_hash"],
                )
            if record["resources"].get("wall_time_seconds") != wall_time_seconds:
                raise AdapterHandleError(
                    "ADAPTER_TIMEOUT_INVALID: timeout differs from immutable resources"
                )
            try:
                binding = binding_from_submission_record(record)
                job_dir = self._job_directory(record)
            except WorkerControlError as error:
                raise AdapterHandleError(
                    "ADAPTER_TIMEOUT_INVALID: managed attempt evidence is invalid"
                ) from error
            try:
                observed = self._status_for_record(
                    record, allow_pending_timeout=True
                )
            except AdapterStatusError as error:
                if _is_orphaned_checkpoint_waiting(error):
                    observed = None
                else:
                    raise AdapterHandleError(
                        "ADAPTER_TIMEOUT_INVALID: managed attempt evidence is invalid"
                    ) from error
            except WorkerControlError as error:
                raise AdapterHandleError(
                    "ADAPTER_TIMEOUT_INVALID: managed attempt evidence is invalid"
                ) from error
            if binding.attempt_id != attempt_id:
                return _managed_timeout_proof(
                    task_id=handle.task_id,
                    timeout_id=timeout_id,
                    state="deferred",
                    code="TIMEOUT_ATTEMPT_MISMATCH",
                    attempt_id=attempt_id,
                    wall_time_seconds=wall_time_seconds,
                    started_at=started_at,
                    deadline_at=deadline_at,
                    ready_record_hash=None,
                    evidence=None,
                    terminal_status=None,
                    terminal_failure_code=None,
                    replayed=False,
                    receipt_record_hash=record["record_hash"],
                )

            try:
                existing = read_worker_stop_evidence(self._run_root, binding)
            except WorkerControlError as error:
                if error.code == "WORKER_STOP_UNSUPPORTED":
                    existing = None
                else:
                    raise AdapterHandleError(
                        "ADAPTER_TIMEOUT_INVALID: stop control is invalid"
                    ) from error
            if existing is not None and existing.requested and (
                existing.request_id != timeout_id
                or existing.reason != "wall_time_exceeded"
                or existing.wall_time_seconds != wall_time_seconds
                or existing.started_at != started_at
                or existing.deadline_at != deadline_at
            ):
                raise AdapterIdempotencyConflict(
                    "TIMEOUT_IDEMPOTENCY_CONFLICT: attempt has another stop request"
                )
            try:
                window_attempt = read_worker_attempt_evidence(
                    self._run_root, job_dir, binding
                )
                window_capability = read_worker_stop_capability(
                    self._run_root, binding
                )
            except (FileNotFoundError, WorkerControlError) as error:
                raise AdapterHandleError(
                    "ADAPTER_TIMEOUT_INVALID: timeout window evidence is invalid"
                ) from error
            if (
                existing is not None
                and existing.requested
                and (
                    window_attempt is None
                    or existing.ready_record_hash
                    != window_attempt.ready_record_hash
                )
            ):
                raise AdapterHandleError(
                    "ADAPTER_TIMEOUT_INVALID: timeout ready receipt changed"
                )
            window_valid = (
                window_attempt is not None
                and window_attempt.ticket_state == "spawned"
                and window_attempt.ready
                and window_attempt.ready_record_hash is not None
                and window_capability is not None
                and existing is not None
                and (
                    not existing.requested
                    or existing.ready_record_hash
                    == window_attempt.ready_record_hash
                )
                and window_capability["record_hash"]
                == existing.capability_record_hash
                and window_capability["worker_pid"]
                == window_attempt.ticket_worker_pid
                and window_capability["worker_pid"]
                == window_attempt.ready_worker_pid
                and window_capability["capacity_slot"]
                == window_attempt.capacity_slot
                and window_capability["capacity_generation"]
                == window_attempt.capacity_generation
                and window_capability["wall_time_seconds"]
                == wall_time_seconds
            )

            # A natural terminal is authoritative even if a timeout request was
            # already published.  WALL_TIME_EXCEEDED is deliberately excluded:
            # it is terminal only after the exact stop/fence proof below.
            raw_status = _read_json_file(
                job_dir / "status.json", code="WORKER_STATUS_INVALID"
            )
            raw_failure_code = raw_status.get("failure_code")
            if observed is not None and observed.terminal and (
                observed.status == "Succeeded"
                or (
                    observed.status == "Failed"
                    and raw_failure_code != "WALL_TIME_EXCEEDED"
                )
            ):
                if not window_valid:
                    return _managed_timeout_proof(
                        task_id=handle.task_id,
                        timeout_id=timeout_id,
                        state="deferred",
                        code="TIMEOUT_WORKER_CAPABILITY_UNAVAILABLE",
                        attempt_id=binding.attempt_id,
                        wall_time_seconds=wall_time_seconds,
                        started_at=started_at,
                        deadline_at=deadline_at,
                        ready_record_hash=(
                            None
                            if window_attempt is None
                            else window_attempt.ready_record_hash
                        ),
                        evidence=existing,
                        terminal_status=None,
                        terminal_failure_code=None,
                        replayed=existing is not None and existing.requested,
                        receipt_record_hash=record["record_hash"],
                    )
                return _managed_timeout_proof(
                    task_id=handle.task_id,
                    timeout_id=timeout_id,
                    state="terminal_won",
                    code="TIMEOUT_TERMINAL_WON",
                    attempt_id=binding.attempt_id,
                    wall_time_seconds=wall_time_seconds,
                    started_at=started_at,
                    deadline_at=deadline_at,
                    ready_record_hash=window_attempt.ready_record_hash,
                    evidence=existing,
                    terminal_status=observed.status,
                    terminal_failure_code=None,
                    replayed=existing is not None and existing.requested,
                    receipt_record_hash=record["record_hash"],
                )

            now_text = self._clock()
            try:
                now = _parse_timestamp(now_text, code="CLOCK_INVALID")
            except AdapterStatusError as error:
                raise AdapterUnavailable("CLOCK_INVALID") from error
            if now < deadline:
                return _managed_timeout_proof(
                    task_id=handle.task_id,
                    timeout_id=timeout_id,
                    state="deferred",
                    code="TIMEOUT_NOT_DUE",
                    attempt_id=binding.attempt_id,
                    wall_time_seconds=wall_time_seconds,
                    started_at=started_at,
                    deadline_at=deadline_at,
                    ready_record_hash=(
                        None
                        if window_attempt is None
                        else window_attempt.ready_record_hash
                    ),
                    evidence=existing,
                    terminal_status=None,
                    terminal_failure_code=None,
                    replayed=existing is not None and existing.requested,
                    receipt_record_hash=record["record_hash"],
                )

            try:
                attempt = read_worker_attempt_evidence(
                    self._run_root, job_dir, binding
                )
            except (FileNotFoundError, WorkerControlError) as error:
                raise AdapterHandleError(
                    "ADAPTER_TIMEOUT_INVALID: Worker attempt evidence is invalid"
                ) from error
            if (
                attempt is None
                or attempt.ticket_state != "spawned"
                or not attempt.ready
                or attempt.heartbeat_state
                not in (
                    {"running", "waiting", "stopped"}
                    if existing and existing.requested
                    else {"running", "waiting"}
                )
            ):
                return _managed_timeout_proof(
                    task_id=handle.task_id,
                    timeout_id=timeout_id,
                    state="deferred",
                    code="TIMEOUT_WORKER_NOT_RUNNING",
                    attempt_id=binding.attempt_id,
                    wall_time_seconds=wall_time_seconds,
                    started_at=started_at,
                    deadline_at=deadline_at,
                    ready_record_hash=(
                        None if attempt is None else attempt.ready_record_hash
                    ),
                    evidence=existing,
                    terminal_status=None,
                    terminal_failure_code=None,
                    replayed=existing is not None and existing.requested,
                    receipt_record_hash=record["record_hash"],
                )
            capability = read_worker_stop_capability(self._run_root, binding)
            if capability is None:
                return _managed_timeout_proof(
                    task_id=handle.task_id,
                    timeout_id=timeout_id,
                    state="deferred",
                    code="TIMEOUT_WORKER_CAPABILITY_UNAVAILABLE",
                    attempt_id=binding.attempt_id,
                    wall_time_seconds=wall_time_seconds,
                    started_at=started_at,
                    deadline_at=deadline_at,
                    ready_record_hash=attempt.ready_record_hash,
                    evidence=None,
                    terminal_status=None,
                    terminal_failure_code=None,
                    replayed=False,
                    receipt_record_hash=record["record_hash"],
                )
            if (
                capability["worker_pid"] != attempt.ticket_worker_pid
                or capability["worker_pid"] != attempt.ready_worker_pid
                or capability["capacity_slot"] != attempt.capacity_slot
                or capability["capacity_generation"] != attempt.capacity_generation
                or capability["wall_time_seconds"] != wall_time_seconds
            ):
                raise AdapterHandleError(
                    "ADAPTER_TIMEOUT_INVALID: capability Worker identity changed"
                )

            try:
                evidence, replayed = request_worker_stop(
                    self._run_root,
                    binding,
                    request_id=timeout_id,
                    reason="wall_time_exceeded",
                    requested_at=now_text,
                    wall_time_seconds=wall_time_seconds,
                    started_at=started_at,
                    deadline_at=deadline_at,
                    ready_record_hash=attempt.ready_record_hash,
                )
            except WorkerControlError as error:
                if error.code == "WORKER_STOP_CONFLICT":
                    raise AdapterIdempotencyConflict(
                        "TIMEOUT_IDEMPOTENCY_CONFLICT: attempt has another stop request"
                    ) from error
                raise AdapterHandleError(
                    "ADAPTER_TIMEOUT_INVALID: stop control is invalid"
                ) from error

            try:
                with hold_idle_execution_fence(self._run_root, binding):
                    raw_status = _read_json_file(
                        job_dir / "status.json", code="WORKER_STATUS_INVALID"
                    )
                    if raw_status.get("status") == "waiting":
                        try:
                            self._status_for_record(
                                record, timeout_fence_held=True
                            )
                        except AdapterStatusError as error:
                            if not _is_orphaned_checkpoint_waiting(
                                error, idle_fence_held=True
                            ):
                                raise AdapterHandleError(
                                    "ADAPTER_TIMEOUT_INVALID: terminal status is invalid"
                                ) from error
                    if raw_status.get("status") in {"succeeded", "failed"} and raw_status.get(
                        "failure_code"
                    ) != "WALL_TIME_EXCEEDED":
                        terminal = self._status_for_record(record)
                        return _managed_timeout_proof(
                            task_id=handle.task_id,
                            timeout_id=timeout_id,
                            state="terminal_won",
                            code="TIMEOUT_TERMINAL_WON",
                            attempt_id=binding.attempt_id,
                            wall_time_seconds=wall_time_seconds,
                            started_at=started_at,
                            deadline_at=deadline_at,
                            ready_record_hash=attempt.ready_record_hash,
                            evidence=evidence,
                            terminal_status=terminal.status,
                            terminal_failure_code=None,
                            replayed=replayed,
                            receipt_record_hash=record["record_hash"],
                        )
                    evidence = read_worker_stop_evidence(
                        self._run_root, binding
                    )
                    attempt = read_worker_attempt_evidence(
                        self._run_root, job_dir, binding
                    )
                    capability = read_worker_stop_capability(
                        self._run_root, binding
                    )
                    if (
                        evidence.request_id != timeout_id
                        or evidence.reason != "wall_time_exceeded"
                        or evidence.wall_time_seconds != wall_time_seconds
                        or evidence.started_at != started_at
                        or evidence.deadline_at != deadline_at
                        or not evidence.acknowledged
                        or attempt is None
                        or attempt.ticket_state != "spawned"
                        or not attempt.ready
                        or attempt.heartbeat_state != "stopped"
                        or attempt.ready_record_hash is None
                        or evidence.ready_record_hash
                        != attempt.ready_record_hash
                        or capability is None
                        or capability["record_hash"]
                        != evidence.capability_record_hash
                        or capability["worker_pid"] != attempt.ticket_worker_pid
                        or capability["worker_pid"] != attempt.ready_worker_pid
                        or capability["capacity_slot"] != attempt.capacity_slot
                        or capability["capacity_generation"]
                        != attempt.capacity_generation
                        or capability["wall_time_seconds"]
                        != wall_time_seconds
                    ):
                        return _managed_timeout_proof(
                            task_id=handle.task_id,
                            timeout_id=timeout_id,
                            state="deferred",
                            code="TIMEOUT_EXIT_UNPROVEN",
                            attempt_id=binding.attempt_id,
                            wall_time_seconds=wall_time_seconds,
                            started_at=started_at,
                            deadline_at=deadline_at,
                            ready_record_hash=(
                                None if attempt is None else attempt.ready_record_hash
                            ),
                            evidence=evidence,
                            terminal_status=None,
                            terminal_failure_code=None,
                            replayed=replayed,
                            receipt_record_hash=record["record_hash"],
                        )
                    if raw_status.get("status") not in {
                        "queued",
                        "running",
                        "waiting",
                        "failed",
                    }:
                        raise AdapterHandleError(
                            "ADAPTER_TIMEOUT_INVALID: terminal timeout state is invalid"
                        )
                    if (
                        raw_status.get("status") != "failed"
                        or raw_status.get("failure_code")
                        != "WALL_TIME_EXCEEDED"
                    ):
                        raw_status.update(
                            {
                                "status": "failed",
                                "stage": "failed",
                                "message": "FWI Worker wall time exceeded",
                                "failure_code": "WALL_TIME_EXCEEDED",
                                "updated_at": self._clock(),
                            }
                        )
                        _atomic_write_json(job_dir / "status.json", raw_status)
                    terminal = self._status_for_record(
                        record, timeout_fence_held=True
                    )
                    if terminal.status != "Failed":
                        raise AdapterHandleError(
                            "ADAPTER_TIMEOUT_INVALID: timeout status was not durable"
                        )
                    return _managed_timeout_proof(
                        task_id=handle.task_id,
                        timeout_id=timeout_id,
                        state="timed_out",
                        code="TIMEOUT_COMPLETED",
                        attempt_id=binding.attempt_id,
                        wall_time_seconds=wall_time_seconds,
                        started_at=started_at,
                        deadline_at=deadline_at,
                        ready_record_hash=attempt.ready_record_hash,
                        evidence=evidence,
                        terminal_status="Failed",
                        terminal_failure_code="WALL_TIME_EXCEEDED",
                        replayed=replayed,
                        receipt_record_hash=record["record_hash"],
                    )
            except WorkerControlError as error:
                if error.code != "WORKER_ATTEMPT_BUSY":
                    raise AdapterHandleError(
                        "ADAPTER_TIMEOUT_INVALID: execution fence is invalid"
                    ) from error
                return _managed_timeout_proof(
                    task_id=handle.task_id,
                    timeout_id=timeout_id,
                    state="pending" if replayed else "requested",
                    code=("TIMEOUT_PENDING" if replayed else "TIMEOUT_REQUESTED"),
                    attempt_id=binding.attempt_id,
                    wall_time_seconds=wall_time_seconds,
                    started_at=started_at,
                    deadline_at=deadline_at,
                    ready_record_hash=attempt.ready_record_hash,
                    evidence=evidence,
                    terminal_status=None,
                    terminal_failure_code=None,
                    replayed=replayed,
                    receipt_record_hash=record["record_hash"],
                )

    @staticmethod
    def _read_artifact_bytes(
        job_dir: Path, relative_path: str, *, max_bytes: int
    ) -> bytes:
        relative = Path(relative_path)
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            raise AdapterArtifactError(
                "ADAPTER_ARTIFACT_INVALID: artifact path is not a safe relative path"
            )
        directory_flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            directory_descriptor = _open_directory_fd(job_dir)
        except OSError as error:
            raise AdapterArtifactError(
                "ADAPTER_ARTIFACT_INVALID: job directory is unavailable"
            ) from error
        try:
            for part in relative.parts[:-1]:
                try:
                    next_descriptor = os.open(
                        part, directory_flags, dir_fd=directory_descriptor
                    )
                except OSError as error:
                    raise AdapterArtifactError(
                        "ADAPTER_ARTIFACT_INVALID: artifact parent is unavailable"
                    ) from error
                os.close(directory_descriptor)
                directory_descriptor = next_descriptor
            flags = (
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0)
                | getattr(os, "O_CLOEXEC", 0)
            )
            try:
                descriptor = os.open(
                    relative.parts[-1], flags, dir_fd=directory_descriptor
                )
            except OSError as error:
                raise AdapterArtifactError(
                    "ADAPTER_ARTIFACT_INVALID: artifact is unavailable"
                ) from error
        finally:
            os.close(directory_descriptor)
        try:
            file_status = os.fstat(descriptor)
            if not stat.S_ISREG(file_status.st_mode) or file_status.st_size > max_bytes:
                raise AdapterArtifactError(
                    "ADAPTER_ARTIFACT_INVALID: artifact is not a bounded regular file"
                )
            chunks: list[bytes] = []
            remaining = max_bytes + 1
            while remaining > 0:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            if len(data) > max_bytes:
                raise AdapterArtifactError(
                    "ADAPTER_ARTIFACT_INVALID: artifact is too large"
                )
            return data
        finally:
            os.close(descriptor)

    @staticmethod
    def _validate_npy(
        data: bytes, *, shape: tuple[int, int]
    ) -> tuple[float, float]:
        import numpy as np

        stream = io.BytesIO(data)
        try:
            version = np.lib.format.read_magic(stream)
            if version != (1, 0):
                raise ValueError("only the fixed NPY v1 header is accepted")
            declared_shape, fortran_order, dtype = (
                np.lib.format.read_array_header_1_0(stream)
            )
        except Exception as error:
            raise AdapterArtifactError(
                "ADAPTER_ARTIFACT_INVALID: inverted model is not a safe NPY"
            ) from error
        payload_offset = stream.tell()
        expected_payload_bytes = math.prod(shape) * np.dtype(np.float32).itemsize
        if (
            declared_shape != shape
            or fortran_order
            or dtype != np.dtype(np.float32)
            or len(data) != payload_offset + expected_payload_bytes
        ):
            raise AdapterArtifactError(
                "ADAPTER_ARTIFACT_INVALID: inverted model shape, dtype, order, or size is wrong"
            )
        # Header and exact byte count are fixed before interpreting the payload;
        # frombuffer is a bounded view and cannot allocate from a declared shape.
        value = np.frombuffer(
            data,
            dtype=np.float32,
            count=math.prod(shape),
            offset=payload_offset,
        ).reshape(shape)
        if not np.isfinite(value).all():
            raise AdapterArtifactError(
                "ADAPTER_ARTIFACT_INVALID: inverted model contains NaN or Inf"
            )
        minimum = float(value.min())
        maximum = float(value.max())
        if minimum < 1500.0 or maximum > 5500.0:
            raise AdapterArtifactError(
                "ADAPTER_ARTIFACT_INVALID: inverted velocity is outside the fixed physical bounds"
            )
        return minimum, maximum

    @staticmethod
    def _validate_png(
        data: bytes, *, width_px: int, height_px: int
    ) -> None:
        """Fully decode one fixed Matplotlib PNG without trusting metadata."""

        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise AdapterArtifactError(
                "ADAPTER_ARTIFACT_INVALID: figure is not a PNG"
            )
        expected_size = (width_px, height_px)
        try:
            # ``verify`` checks the complete PNG structure and chunk checksums
            # without decoding pixels.  Reopening and loading then proves that
            # the bounded image payload itself is decodable.
            with Image.open(io.BytesIO(data)) as image:
                if (
                    image.format != "PNG"
                    or image.size != expected_size
                    or image.mode != "RGBA"
                ):
                    raise AdapterArtifactError(
                        "ADAPTER_ARTIFACT_INVALID: figure format, dimensions, or mode are wrong"
                    )
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                if (
                    image.format != "PNG"
                    or image.size != expected_size
                    or image.mode != "RGBA"
                ):
                    raise AdapterArtifactError(
                        "ADAPTER_ARTIFACT_INVALID: decoded figure identity changed"
                    )
                image.load()
        except AdapterArtifactError:
            raise
        except Exception as error:
            raise AdapterArtifactError(
                "ADAPTER_ARTIFACT_INVALID: figure is not a fully decodable bounded PNG"
            ) from error

    @staticmethod
    def _validate_loss_csv(
        data: bytes, *, iterations: int, expected_frequency_hz: float
    ) -> list[float]:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise AdapterArtifactError(
                "ADAPTER_ARTIFACT_INVALID: loss curve is not UTF-8"
            ) from error
        reader = csv.reader(io.StringIO(text, newline=""))
        rows = list(reader)
        if not rows or rows[0] != ["iteration", "frequency_hz", "loss"]:
            raise AdapterArtifactError(
                "ADAPTER_ARTIFACT_INVALID: loss curve header is invalid"
            )
        values = rows[1:]
        if len(values) != iterations + 1:
            raise AdapterArtifactError(
                "ADAPTER_ARTIFACT_INVALID: loss curve row count is invalid"
            )
        losses: list[float] = []
        for index, row in enumerate(values):
            if len(row) != 3:
                raise AdapterArtifactError(
                    "ADAPTER_ARTIFACT_INVALID: loss curve row is malformed"
                )
            try:
                row_index = int(row[0])
                frequency = float(row[1])
                loss = float(row[2])
            except ValueError as error:
                raise AdapterArtifactError(
                    "ADAPTER_ARTIFACT_INVALID: loss curve contains non-numeric data"
                ) from error
            if (
                row_index != index
                or not math.isfinite(frequency)
                or frequency <= 0
                or not math.isclose(
                    frequency,
                    expected_frequency_hz,
                    rel_tol=0.0,
                    abs_tol=1e-12,
                )
                or not math.isfinite(loss)
                or loss < 0
            ):
                raise AdapterArtifactError(
                    "ADAPTER_ARTIFACT_INVALID: loss curve values are invalid"
                )
            losses.append(loss)
        return losses

    @staticmethod
    def _scalar_metrics(
        value: Mapping[str, Any],
        *,
        iterations: int,
        device: str,
        optimizer: str,
        learning_rate: float,
        losses: list[float],
        fingerprint: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise AdapterArtifactError(
                "ADAPTER_ARTIFACT_INVALID: metrics root must be an object"
            )
        integer_fields = ("iterations", "nan_count", "inf_count")
        nonnegative_fields = (
            "initial_loss",
            "final_loss",
            "initial_model_relative_l2",
            "final_model_relative_l2",
            "observed_predicted_relative_l2",
            "model_update_relative_l2",
            "elapsed_seconds",
        )
        finite_fields = ("loss_reduction_fraction",)
        text_fields = ("device_name", "torch_version", "deepwave_version")
        required = {
            *integer_fields,
            *nonnegative_fields,
            *finite_fields,
            *text_fields,
            "device",
            "optimizer",
            "learning_rate",
            "gradient_clip_quantile",
        }
        if any(field not in value for field in required):
            raise AdapterArtifactError(
                "ADAPTER_ARTIFACT_INVALID: required structured metrics are missing"
            )
        result: dict[str, Any] = {}
        for field in integer_fields:
            item = value[field]
            if type(item) is not int or item < 0:
                raise AdapterArtifactError(
                    f"ADAPTER_ARTIFACT_INVALID: {field} must be a nonnegative integer"
                )
            result[field] = item
        if result["iterations"] != iterations or result["nan_count"] or result["inf_count"]:
            raise AdapterArtifactError(
                "ADAPTER_ARTIFACT_INVALID: metric iterations/nonfinite counts contradict success"
            )
        for field in (*nonnegative_fields, *finite_fields):
            item = value[field]
            if (
                isinstance(item, bool)
                or not isinstance(item, (int, float))
                or not math.isfinite(float(item))
                or (field in nonnegative_fields and item < 0)
            ):
                raise AdapterArtifactError(
                    f"ADAPTER_ARTIFACT_INVALID: {field} must be a bounded finite number"
                )
            result[field] = item
        if value["device"] != device:
            raise AdapterArtifactError(
                "ADAPTER_ARTIFACT_INVALID: metrics device differs from the request"
            )
        result["device"] = device
        reported_learning_rate = value["learning_rate"]
        reported_clip_quantile = value["gradient_clip_quantile"]
        if (
            value["optimizer"] != optimizer
            or isinstance(reported_learning_rate, bool)
            or not isinstance(reported_learning_rate, (int, float))
            or not math.isfinite(float(reported_learning_rate))
            or not math.isclose(
                float(reported_learning_rate), learning_rate, rel_tol=0.0, abs_tol=1e-12
            )
            or isinstance(reported_clip_quantile, bool)
            or not isinstance(reported_clip_quantile, (int, float))
            or not math.isfinite(float(reported_clip_quantile))
            or not math.isclose(
                float(reported_clip_quantile),
                GRADIENT_CLIP_QUANTILE,
                rel_tol=0.0,
                abs_tol=1e-12,
            )
        ):
            raise AdapterArtifactError(
                "ADAPTER_ARTIFACT_INVALID: optimizer metrics differ from the frozen request"
            )
        result["optimizer"] = optimizer
        result["learning_rate"] = float(reported_learning_rate)
        result["gradient_clip_quantile"] = float(reported_clip_quantile)
        for field in text_fields:
            item = value[field]
            limit = 200 if field == "device_name" else 128
            if (
                not isinstance(item, str)
                or not 1 <= len(item) <= limit
                or any(character in item for character in ("/", "\\", "\n", "\r", "\x00"))
            ):
                raise AdapterArtifactError(
                    f"ADAPTER_ARTIFACT_INVALID: {field} is not a safe bounded label"
                )
            result[field] = item
        expected_reduction = (
            (losses[0] - losses[-1]) / losses[0]
            if losses[0] > 0
            else (0.0 if losses[-1] == 0 else float("-inf"))
        )
        comparisons = {
            "initial_loss": losses[0],
            "final_loss": losses[-1],
            "loss_reduction_fraction": expected_reduction,
        }
        if any(
            not math.isclose(
                float(result[field]), expected, rel_tol=1e-9, abs_tol=1e-12
            )
            for field, expected in comparisons.items()
        ):
            raise AdapterArtifactError(
                "ADAPTER_ARTIFACT_INVALID: loss metrics differ from the validated CSV"
            )
        runtime = fingerprint.get("runtime")
        hardware = fingerprint.get("hardware")
        if (
            not isinstance(runtime, Mapping)
            or not isinstance(hardware, Mapping)
            or result["torch_version"] != runtime.get("pytorch")
            or result["deepwave_version"] != runtime.get("deepwave")
            or result["device_name"] != hardware.get("device_name")
            or result["device"] != hardware.get("device")
        ):
            raise AdapterArtifactError(
                "ADAPTER_ARTIFACT_INVALID: runtime metrics differ from the frozen fingerprint"
            )
        return result

    @staticmethod
    def _artifact_manifest(
        *,
        record: Mapping[str, Any],
        port: str,
        artifact_type: str,
        media_type: str,
        relative_path: str,
        data: bytes,
        created_at: str,
        metrics: Mapping[str, Any],
        component: str,
        title: str,
        order: int,
        public_extensions: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        content_hash = "sha256:" + hashlib.sha256(data).hexdigest()
        artifact_identity = _sha256_document(
            {
                "task_id": record["task_id"],
                "node_id": record["node_id"],
                "port": port,
                "content_hash": content_hash,
            }
        ).removeprefix("sha256:")
        extensions: dict[str, Any] = {
            "org.agent_rpc.adapter": {
                "output_port": port,
                "worker_job_id": record["job_id"],
            }
        }
        if public_extensions is not None:
            for namespace, detail in public_extensions.items():
                if namespace == "org.agent_rpc.adapter" or not isinstance(
                    detail, Mapping
                ):
                    raise AdapterArtifactError(
                        "ADAPTER_ARTIFACT_INVALID: generated artifact extensions are invalid"
                    )
                extensions[namespace] = copy.deepcopy(dict(detail))
        value = {
            "schema_version": "1.0.0",
            "artifact_id": f"artifact-{artifact_identity[:32]}",
            "task_id": record["task_id"],
            "node_id": record["node_id"],
            "artifact_type": artifact_type,
            "media_type": media_type,
            "location": {
                "relative_path": f"{record['job_id']}/{relative_path}"
            },
            "content_hash": content_hash,
            "size_bytes": len(data),
            "created_at": created_at,
            "metrics": copy.deepcopy(dict(metrics)),
            "display": {"component": component, "title": title, "order": order},
            "fingerprint": copy.deepcopy(record["fingerprint"]),
            "lineage": {
                "plan_hash": record["plan_hash"],
                "algorithm": copy.deepcopy(record["algorithm"]),
                "inputs": [copy.deepcopy(record["dataset"])],
            },
            "extensions": extensions,
        }
        errors = schema_errors("artifact-manifest.schema.json", value)
        if errors:
            raise AdapterArtifactError(
                "ADAPTER_ARTIFACT_INVALID: generated manifest failed its schema: "
                + "; ".join(errors)
            )
        return value

    def collect(self, handle: AdapterHandle) -> list[dict[str, Any]]:
        record = self._record_for_handle(handle)
        current = self.status(handle)
        if current.status != "Succeeded":
            raise AdapterArtifactError(
                "RESULT_NOT_READY: artifacts are available only after success"
            )
        job_dir = self._job_directory(record)
        try:
            config_document = _read_json_file(
                job_dir / "config.original.json", code="ADAPTER_ARTIFACT_INVALID"
            )
            legacy_manifest = _read_json_file(
                job_dir / "manifest.json", code="ADAPTER_ARTIFACT_INVALID"
            )
            metrics_document = _read_json_file(
                job_dir / "metrics.json", code="ADAPTER_ARTIFACT_INVALID"
            )
        except AdapterStatusError as error:
            raise AdapterArtifactError(str(error)) from error
        if (
            config_document != {"job_id": record["job_id"], **record["worker_config"]}
            or legacy_manifest.get("schema_version") != "1"
            or legacy_manifest.get("type") != "fwi_result"
            or legacy_manifest.get("job_id") != record["job_id"]
            or legacy_manifest.get("status") != "succeeded"
            or legacy_manifest.get("command") != "invert"
            or legacy_manifest.get("model_id") != MODEL_ID
            or legacy_manifest.get("physics") != "2d_acoustic_constant_density"
            or legacy_manifest.get("parameter") != "vp"
            or legacy_manifest.get("metrics") != metrics_document
        ):
            raise AdapterArtifactError(
                "ADAPTER_ARTIFACT_INVALID: legacy result identity is inconsistent"
            )
        inverted = self._read_artifact_bytes(
            job_dir, "models/inverted.npy", max_bytes=MAX_NPY_BYTES
        )
        loss = self._read_artifact_bytes(job_dir, "loss.csv", max_bytes=MAX_CSV_BYTES)
        shape = tuple(int(item) for item in record["dataset"].get("shape", []))
        # The public lineage identity intentionally excludes metadata.  Shape
        # comes from the fixed, verified baseline in this adapter version.
        if shape != (94, 288):
            shape = (94, 288)
        self._validate_npy(inverted, shape=shape)
        losses = self._validate_loss_csv(
            loss,
            iterations=record["parameters"]["iterations"],
            expected_frequency_hz=8.0,
        )
        if (
            record["parameters"]["preset"] == "fwi_demo"
            and losses[-1] >= losses[0]
        ):
            raise AdapterArtifactError(
                "ADAPTER_ARTIFACT_INVALID: fwi_demo did not reduce its objective"
            )
        # Adapter 1.2 adds public optimizer controls.  Persisted 1.0/1.1
        # receipts intentionally retain their four-field parameter document,
        # so collection must verify them against the historical worker
        # defaults without mutating their signed lineage.
        optimizer = record["parameters"].get("optimizer", "adam")
        learning_rate_milli = record["parameters"].get(
            "learning_rate_milli", 10_000
        )
        metrics = self._scalar_metrics(
            metrics_document,
            iterations=record["parameters"]["iterations"],
            device=record["parameters"]["device"],
            optimizer=optimizer,
            learning_rate=learning_rate_milli / 1000.0,
            losses=losses,
            fingerprint=record["fingerprint"],
        )
        artifacts = [
            self._artifact_manifest(
                record=record,
                port="inverted_model",
                artifact_type="inverted_velocity_model_2d",
                media_type="application/x-npy",
                relative_path="models/inverted.npy",
                data=inverted,
                created_at=current.updated_at,
                metrics=metrics,
                component="download",
                title="Inverted velocity model",
                order=0,
            ),
            self._artifact_manifest(
                record=record,
                port="loss",
                artifact_type="loss_curve",
                media_type="text/csv",
                relative_path="loss.csv",
                data=loss,
                created_at=current.updated_at,
                metrics=metrics,
                component="line_chart",
                title="FWI loss curve",
                order=1,
            ),
        ]
        # Algorithm/Adapter 1.0--1.3 promised only the two primary numerical
        # outputs.  Their immutable receipts remain readable as that exact
        # pair even though the legacy Worker happened to write PNG files too.
        if record["algorithm"]["version"] not in {"1.4.0", "1.5.0", "1.6.0"}:
            return artifacts

        # Algorithm 1.4/1.5 promote the six fixed Worker plots to declared,
        # hash-bound standard outputs.  Never consume legacy_manifest.figure
        # path/url fields: all paths, identities, titles, dimensions, and
        # ordering come from the Adapter's immutable allowlist above.
        for spec in FIGURE_ARTIFACT_SPECS:
            data = self._read_artifact_bytes(
                job_dir,
                spec["relative_path"],
                max_bytes=MAX_PNG_BYTES,
            )
            self._validate_png(
                data,
                width_px=spec["width_px"],
                height_px=spec["height_px"],
            )
            artifacts.append(
                self._artifact_manifest(
                    record=record,
                    port=spec["port"],
                    artifact_type="figure",
                    media_type="image/png",
                    relative_path=spec["relative_path"],
                    data=data,
                    created_at=current.updated_at,
                    metrics=metrics,
                    component="image",
                    title=spec["title"],
                    order=spec["order"],
                    public_extensions={
                        "org.agent_rpc.figure": {
                            "figure_id": spec["figure_id"],
                            "width_px": spec["width_px"],
                            "height_px": spec["height_px"],
                        }
                    },
                )
            )
        return artifacts

    def collect_and_read_artifact(
        self, handle: AdapterHandle, artifact_id: str
    ) -> tuple[list[dict[str, Any]], dict[str, Any], bytes]:
        """Collect once, then return one hash-revalidated artifact payload."""

        if (
            not isinstance(artifact_id, str)
            or re.fullmatch(r"artifact-[0-9a-f]{32}", artifact_id) is None
        ):
            raise AdapterArtifactError(
                "ARTIFACT_ID_INVALID: artifact identity is malformed"
            )
        manifests = self.collect(handle)
        manifest = next(
            (value for value in manifests if value.get("artifact_id") == artifact_id),
            None,
        )
        if manifest is None:
            raise AdapterArtifactError(
                "ARTIFACT_NOT_FOUND: artifact identity is not part of this task"
            )
        record = self._record_for_handle(handle)
        location = manifest.get("location")
        relative_path = (
            location.get("relative_path") if isinstance(location, Mapping) else None
        )
        prefix = f"{record['job_id']}/"
        if not isinstance(relative_path, str) or not relative_path.startswith(prefix):
            raise AdapterArtifactError(
                "ADAPTER_ARTIFACT_INVALID: artifact location is not task-bound"
            )
        worker_relative_path = relative_path[len(prefix):]
        media_type = manifest.get("media_type")
        maximum = {
            "application/x-npy": MAX_NPY_BYTES,
            "text/csv": MAX_CSV_BYTES,
            "image/png": MAX_PNG_BYTES,
        }.get(media_type)
        if maximum is None:
            raise AdapterArtifactError(
                "ADAPTER_ARTIFACT_INVALID: artifact media type is unsupported"
            )
        data = self._read_artifact_bytes(
            self._job_directory(record), worker_relative_path, max_bytes=maximum
        )
        content_hash = "sha256:" + hashlib.sha256(data).hexdigest()
        if (
            len(data) != manifest.get("size_bytes")
            or content_hash != manifest.get("content_hash")
        ):
            raise AdapterArtifactError(
                "ADAPTER_ARTIFACT_INVALID: artifact changed during validated access"
            )
        return copy.deepcopy(manifests), copy.deepcopy(manifest), data

    def read_artifact(
        self, handle: AdapterHandle, artifact_id: str
    ) -> tuple[dict[str, Any], bytes]:
        """Return one revalidated standard artifact without trusting a path."""

        _, manifest, data = self.collect_and_read_artifact(handle, artifact_id)
        return manifest, data


__all__ = [
    "AdapterArtifactError",
    "AdapterCancelResult",
    "AdapterError",
    "AdapterEstimate",
    "AdapterHandle",
    "AdapterHandleError",
    "AdapterIdempotencyConflict",
    "AdapterManagedCancelProof",
    "AdapterManagedTimeoutProof",
    "AdapterPurgeError",
    "AdapterPurgeResult",
    "AdapterStatus",
    "AdapterStatusError",
    "AdapterUnavailable",
    "AdapterValidation",
    "AdapterValidationError",
    "DeepwaveAdapter",
    "SafeSubprocessWorkerLauncher",
]
