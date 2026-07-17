from __future__ import annotations

import copy
import dataclasses
import fcntl
import hashlib
import io
import json
import multiprocessing
import os
import shutil
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np
import scientific_runtime.fwi_adapter as fwi_adapter_module
from PIL import Image

from scientific_runtime import (
    DeepwaveTaskDispatcher,
    DispatchDeferred,
    DispatchError,
    DispatchIntentSnapshot,
    RegistryService,
    RetryExhaustionCleanupProof,
    SQLiteTaskStore,
    TaskConflict,
    TaskService,
    TaskSnapshot,
    TaskStoreConflict,
)
from scientific_runtime.fwi_adapter import (
    AdapterDispatchNotStartedProof,
    AdapterExistingDispatchReceiptProof,
    AdapterIdempotencyConflict,
    AdapterManagedCancelProof,
    AdapterManagedTimeoutProof,
    AdapterPurgeError,
    AdapterUnavailable,
    DeepwaveAdapter,
    SafeSubprocessWorkerLauncher,
)
from scientific_runtime.task_dispatcher import (
    DispatchNotStartedProof,
    DispatchReconciliationDeferred,
    DispatchReceiptProbe,
)
from scientific_runtime_contracts import compute_plan_hash, schema_errors
from worker_launch_control import (
    LaunchAttemptBinding,
    ParentLaunchLease,
    WorkerCancellationRequested,
    WorkerHeartbeat,
    WorkerWallTimeExceeded,
    binding_from_submission_record,
    record_worker_exit,
    read_worker_cancel_evidence,
    read_worker_attempt_evidence,
    read_worker_stop_evidence,
    stage_launch_attempt,
)
from tests.test_scientific_runtime_contracts import (
    approval_decision as contract_approval_decision,
    optimizer_plan_graph as contract_optimizer_plan_graph,
    optimizer_task_draft as contract_optimizer_task_draft,
)


NOW = "2026-07-15T06:00:00Z"
HASH_DATASET = "sha256:" + "a" * 64
HASH_ENVIRONMENT = "sha256:" + "b" * 64
HASH_CONFIG = "sha256:" + "c" * 64
PLAN_HASH = "sha256:" + "d" * 64


def algorithm_identity() -> dict[str, Any]:
    return {"id": "deepwave.acoustic_fwi", "version": "1.5.0"}


def dataset_ref(*, content_hash: str = HASH_DATASET) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "id": "marmousi_94_288",
        "version": "1.0.0",
        "content_hash": content_hash,
        "data_type": "velocity_model_2d",
        "immutable": True,
        "metadata": {
            "shape": [94, 288],
            "dtype": "float32",
            "axis_order": ["z", "x"],
            "units": "m/s",
            "physics": "2d_acoustic_constant_density",
            "parameter": "vp",
            "grid_spacing_m": {"dx": 10, "dz": 10},
            "value_range": {"minimum": 1500, "maximum": 5500},
        },
        "lineage": [],
        "access_scope": {
            "project_id": "project-1",
            "principals": ["user-1"],
            "permissions": ["read", "execute"],
        },
        "extensions": {},
    }


def parameters() -> dict[str, Any]:
    return {
        "preset": "fwi_smoke",
        "device": "cpu",
        "iterations": 2,
        "seed": 2026,
        "optimizer": "adam",
        "learning_rate_milli": 10_000,
    }


def resources() -> dict[str, Any]:
    return {
        "device": "cpu",
        "gpu_count": 0,
        "cpu_cores": 4,
        "memory_mb": 8192,
        "wall_time_seconds": 1800,
    }


def development_fingerprint() -> dict[str, Any]:
    return {
        "provenance_mode": "development",
        "algorithm": algorithm_identity(),
        "adapter_version": "1.5.0",
        "source": {"identity_complete": False, "dirty": None},
        "environment": {"environment_lock_hash": HASH_ENVIRONMENT},
        "runtime": {
            "python": "test-python",
            "pytorch": "test-pytorch",
            "deepwave": "test-deepwave",
            "cuda": None,
        },
        "seed": 2026,
        "hardware": {
            "device": "cpu",
            "device_name": "synthetic-test-cpu",
            "compute_capability": None,
        },
        "normalized_config_hash": HASH_CONFIG,
        "input_hashes": [HASH_DATASET],
        "determinism": {
            "requested": False,
            "framework_deterministic": False,
            "flags": {},
            "known_nondeterminism": [
                "The legacy Worker does not yet enable deterministic execution."
            ],
        },
    }


def _plain(value: Any) -> Any:
    if hasattr(value, "as_dict"):
        return _plain(value.as_dict())
    if dataclasses.is_dataclass(value):
        return _plain(dataclasses.asdict(value))
    if isinstance(value, Enum):
        return _plain(value.value)
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _status_name(value: Any) -> str:
    document = _plain(value)
    if not isinstance(document, dict):
        raise AssertionError(f"status result must be an object, got {document!r}")
    state = document.get("status", document.get("state"))
    if not isinstance(state, str):
        raise AssertionError(f"status result has no string state: {document!r}")
    return state.lower()


def _artifacts(value: Any) -> list[dict[str, Any]]:
    document = _plain(value)
    if isinstance(document, list):
        artifacts = document
    elif isinstance(document, dict):
        artifacts = document.get("artifacts", document.get("manifests"))
        if artifacts is None and "artifact_id" in document:
            artifacts = [document]
    else:
        artifacts = None
    if not isinstance(artifacts, list) or not all(
        isinstance(item, dict) for item in artifacts
    ):
        raise AssertionError(
            "collect must return ArtifactManifest objects under artifacts/manifests"
        )
    return artifacts


class FakeLauncher:
    def __init__(self, *, delay_seconds: float = 0.0) -> None:
        self.delay_seconds = delay_seconds
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def launch(
        self,
        *,
        command: str,
        config_path: Path,
        run_dir: Path,
        run_root: Path,
        wall_time_seconds: int,
    ) -> int:
        call = {
            "command": command,
            "config_path": Path(config_path),
            "run_dir": Path(run_dir),
            "run_root": Path(run_root),
            "wall_time_seconds": wall_time_seconds,
        }
        with self._lock:
            self.calls.append(call)
        if self.delay_seconds:
            # Widen the first-submit race without involving a real process.
            time.sleep(self.delay_seconds)
        return 4242


class FailingLauncher(FakeLauncher):
    def launch(self, **kwargs: Any) -> int:
        super().launch(**kwargs)
        raise OSError("synthetic launch failure")


class CapacityDeferredLauncher(FakeLauncher):
    def launch(self, **kwargs: Any) -> int:
        super().launch(**kwargs)
        raise AdapterUnavailable("ADAPTER_CONCURRENCY_LIMIT")


class StoppedThenSuccessfulSafeLauncher(SafeSubprocessWorkerLauncher):
    """Production-class launcher with one exact pre-Popen stopped failure."""

    def __init__(self, *, defer_retry: bool = False) -> None:
        super().__init__(
            python_executable=fwi_adapter_module.DEFAULT_WORKER_PYTHON
        )
        self.calls = 0
        self.defer_retry = defer_retry

    def _launch_once(self, **kwargs: Any) -> int:
        self.calls += 1
        if self.calls == 1:
            lease = ParentLaunchLease.acquire(
                kwargs["run_root"],
                kwargs["run_dir"],
                max_active=self._max_active,
            )
            lease.abort()
            raise AdapterUnavailable(
                "WORKER_LAUNCH_FAILED: synthetic stopped infrastructure failure"
            )
        if self.defer_retry:
            raise AdapterUnavailable(
                "ADAPTER_CONCURRENCY_LIMIT: synthetic retry deferral"
            )
        return 4242


class StoppedTwiceSafeLauncher(StoppedThenSuccessfulSafeLauncher):
    """Production-class launcher proving both finite attempts stopped."""

    def _launch_once(self, **kwargs: Any) -> int:
        self.calls += 1
        lease = ParentLaunchLease.acquire(
            kwargs["run_root"],
            kwargs["run_dir"],
            max_active=self._max_active,
        )
        lease.abort()
        raise AdapterUnavailable(
            "WORKER_LAUNCH_FAILED: synthetic stopped infrastructure failure"
        )


class FileMarkerLauncher:
    def __init__(self, marker: Path) -> None:
        self.marker = marker

    def launch(self, **kwargs: Any) -> int:
        descriptor = os.open(
            self.marker, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600
        )
        try:
            os.write(descriptor, b"launch\n")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return 9876


class DatasetIdentityProvider:
    def __init__(self, dataset: dict[str, Any]) -> None:
        self.dataset = copy.deepcopy(dataset)
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((args, kwargs))
        return copy.deepcopy(self.dataset)


class DeviceValidator:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, device: str) -> dict[str, Any]:
        self.calls.append(device)
        if device not in {"cpu", "cuda"}:
            raise RuntimeError("DEVICE_UNAVAILABLE")
        return {
            "device": device,
            "device_name": "synthetic-test-device",
            "compute_capability": None,
        }


class FingerprintFactory:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((args, kwargs))
        value = development_fingerprint()
        normalized_hash = kwargs.get("normalized_config_hash")
        if isinstance(normalized_hash, str):
            value["normalized_config_hash"] = normalized_hash
        input_hashes = kwargs.get("input_hashes")
        if isinstance(input_hashes, (list, tuple)):
            value["input_hashes"] = list(input_hashes)
        seed = kwargs.get("seed")
        if isinstance(seed, int) and not isinstance(seed, bool):
            value["seed"] = seed
        device = kwargs.get("device")
        if device in {"cpu", "cuda"}:
            value["hardware"]["device"] = device
        return value


def _cross_process_submit(
    run_root: str,
    marker: str,
    start: Any,
    results: Any,
) -> None:
    dataset = dataset_ref()
    adapter = DeepwaveAdapter(
        run_root=Path(run_root),
        launcher=FileMarkerLauncher(Path(marker)),
        dataset_identity_provider=DatasetIdentityProvider(dataset),
        registry_snapshot_provider=DatasetIdentityProvider(dataset),
        device_validator=DeviceValidator(),
        fingerprint_factory=FingerprintFactory(),
        clock=lambda: NOW,
    )
    start.wait(timeout=10)
    try:
        handle = adapter.submit(
            project_id="project-1",
            principal_id="user-1",
            task_id="task-process-race",
            node_id="invert",
            plan_hash=PLAN_HASH,
            idempotency_key="task-process-race:invert:0001",
            algorithm=algorithm_identity(),
            dataset=dataset,
            task_type="acoustic_fwi_2d",
            parameters=parameters(),
            resources=resources(),
        )
        results.put(("ok", _plain(handle)))
    except Exception as error:  # pragma: no cover - asserted in parent
        results.put(("error", f"{type(error).__name__}: {error}"))


class ScientificRuntimeFWIAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.run_root = self.base / "runs"
        self.run_root.mkdir(mode=0o700)
        self.dataset = dataset_ref()
        self.dataset_provider = DatasetIdentityProvider(self.dataset)
        self.registry_provider = DatasetIdentityProvider(self.dataset)
        self.device_validator = DeviceValidator()
        self.fingerprint_factory = FingerprintFactory()
        self.launcher = FakeLauncher()
        self.adapter = self.make_adapter()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def make_adapter(
        self,
        *,
        run_root: Path | None = None,
        launcher: FakeLauncher | None = None,
        clock: Any = None,
    ) -> DeepwaveAdapter:
        return DeepwaveAdapter(
            run_root=run_root or self.run_root,
            launcher=launcher or self.launcher,
            dataset_identity_provider=self.dataset_provider,
            registry_snapshot_provider=self.registry_provider,
            device_validator=self.device_validator,
            fingerprint_factory=self.fingerprint_factory,
            clock=clock or (lambda: NOW),
        )

    def execution_kwargs(self) -> dict[str, Any]:
        return {
            "project_id": "project-1",
            "principal_id": "user-1",
            "algorithm": algorithm_identity(),
            "dataset": copy.deepcopy(self.dataset),
            "task_type": "acoustic_fwi_2d",
            "parameters": parameters(),
            "resources": resources(),
        }

    def submit_kwargs(
        self,
        *,
        task_id: str = "task-001",
        node_id: str = "invert",
        idempotency_key: str = "task-001:invert:0001",
    ) -> dict[str, Any]:
        return {
            **self.execution_kwargs(),
            "task_id": task_id,
            "node_id": node_id,
            "plan_hash": PLAN_HASH,
            "idempotency_key": idempotency_key,
        }

    def root_snapshot(self) -> list[str]:
        return sorted(
            str(path.relative_to(self.run_root))
            for path in self.run_root.rglob("*")
        )

    def submission_record_path(self, handle: Any) -> Path:
        name = handle.submission_id.removeprefix("submission-") + ".json"
        return (
            self.run_root
            / ".scientific-runtime-adapter-v1"
            / "submissions"
            / name
        )

    def stopped_retry_exhaustion_fixture(
        self, *, task_id: str
    ) -> tuple[DeepwaveAdapter, dict[str, Any], dict[str, Any], Path]:
        """Create two exact stopped attempts plus a path-free Store-like token."""

        launcher = StoppedTwiceSafeLauncher()
        adapter = self.make_adapter(launcher=launcher)
        request = self.submit_kwargs(
            task_id=task_id,
            idempotency_key=f"{task_id}:invert:0001",
        )
        with self.assertRaises(AdapterUnavailable):
            adapter.submit(**copy.deepcopy(request))
        first = adapter.probe_pre_running_retry(**copy.deepcopy(request))
        authorization = {
            "schema_version": "1.0.0",
            "intent_id": f"intent-{task_id}",
            "previous_attempt_id": first.previous_attempt_id,
            "previous_observation_sequence": 1,
            "failure_kind": "pre_running_launch_failure",
            "private_proof_hash": first.private_proof_hash,
            "next_attempt_number": 2,
            "authorized_at": "2026-07-15T06:00:01Z",
        }
        with self.assertRaises(AdapterUnavailable):
            adapter.retry_pre_running(
                **copy.deepcopy(request), authorization=authorization
            )
        second = adapter.probe_pre_running_retry_exhaustion(
            **copy.deepcopy(request)
        )
        submission_id = second.evidence["submission_id"]
        record_path = (
            self.run_root
            / fwi_adapter_module.CONTROL_DIRECTORY
            / "submissions"
            / f"{submission_id.removeprefix('submission-')}.json"
        )
        payload = {
            "schema_version": "1.0.0",
            "purge_id": f"purge-{task_id}",
            "intent_id": authorization["intent_id"],
            "task_id": task_id,
            "project_id": "project-1",
            "principal_id": "user-1",
            "approval_id": f"approval-{task_id}",
            "attempt_id": second.previous_attempt_id,
            "attempt_number": 2,
            "observation_sequence": 2,
            "evidence": copy.deepcopy(second.evidence),
            "evidence_hash": fwi_adapter_module._sha256_document(
                second.evidence
            ),
            "private_schema_version": "1.2.0",
            "private_proof_hash": second.private_proof_hash,
            "failure_kind": "pre_running_launch_failure",
            "previous_attempt_id": first.previous_attempt_id,
            "previous_observation_sequence": 1,
            "previous_private_proof_hash": first.private_proof_hash,
            "retry_reserved_at": authorization["authorized_at"],
            "terminal_event_sequence": 2,
            "terminal_event_hash": "sha256:" + "f" * 64,
            "exhausted_at": "2026-07-15T06:00:02Z",
        }
        token = {
            **payload,
            "proof_hash": fwi_adapter_module._sha256_document(payload),
        }
        return adapter, request, token, record_path

    @staticmethod
    def rebound_cleanup_token(
        token: dict[str, Any], **changes: Any
    ) -> dict[str, Any]:
        value = copy.deepcopy(token)
        value.update(changes)
        value.pop("proof_hash", None)
        value["proof_hash"] = fwi_adapter_module._sha256_document(value)
        return value

    def assert_input_rejected(
        self,
        kwargs: dict[str, Any],
        *,
        code: str | None = None,
    ) -> None:
        with self.assertRaises((ValueError, RuntimeError)) as raised:
            self.adapter.validate(**kwargs)
        if code is not None:
            self.assertIn(code, str(raised.exception))

    def submit_and_run_dir(
        self,
        *,
        task_id: str = "task-001",
        node_id: str = "invert",
        idempotency_key: str = "task-001:invert:0001",
    ) -> tuple[Any, Path]:
        handle = self.adapter.submit(
            **self.submit_kwargs(
                task_id=task_id,
                node_id=node_id,
                idempotency_key=idempotency_key,
            )
        )
        self.assertTrue(self.launcher.calls)
        return handle, self.launcher.calls[-1]["run_dir"]

    def start_exact_worker(
        self, handle: Any, run_dir: Path
    ) -> tuple[LaunchAttemptBinding, WorkerHeartbeat]:
        record = json.loads(
            self.submission_record_path(handle).read_text(encoding="utf-8")
        )
        binding = binding_from_submission_record(record)
        lease = ParentLaunchLease.acquire(
            self.run_root, run_dir, max_active=2
        )
        lease.mark_spawned(os.getpid())
        heartbeat = WorkerHeartbeat(
            run_root=self.run_root,
            run_dir=run_dir,
            attempt_id=binding.attempt_id,
            attempt_fd=os.dup(lease.attempt_fd),
            capacity_fd=os.dup(lease.capacity_fd),
            interval_seconds=0.02,
            cancel_grace_seconds=300.0,
            wall_time_seconds=record["resources"]["wall_time_seconds"],
            hard_exit=lambda _code: None,
        )
        lease.close_parent()
        heartbeat.start()
        return binding, heartbeat

    def dispatched_intent(self, handle: Any) -> DispatchIntentSnapshot:
        return DispatchIntentSnapshot(
            intent_id=f"dispatch-{handle.task_id}",
            task_id=handle.task_id,
            plan_id=f"plan-{handle.task_id}",
            plan_hash=handle.plan_hash,
            approval_id=f"approval-{handle.task_id}",
            node_id=handle.node_id,
            node_idempotency_key=handle.idempotency_key,
            adapter_id="fwi.deepwave_adapter",
            adapter_version=handle.adapter_version,
            request={"algorithm": copy.deepcopy(handle.algorithm)},
            request_hash="sha256:" + "e" * 64,
            queue_fingerprint=copy.deepcopy(handle.fingerprint),
            state="dispatched",
            handle=handle.as_dict(),
            failure_code=None,
            created_at=NOW,
            dispatch_claimed_at=NOW,
            outcome_recorded_at=NOW,
        )

    def reconciliation_intent(
        self, handle: Any, request: dict[str, Any]
    ) -> DispatchIntentSnapshot:
        durable_request = copy.deepcopy(request)
        durable_request["normalized_config_hash"] = handle.fingerprint[
            "normalized_config_hash"
        ]
        return DispatchIntentSnapshot(
            intent_id=f"dispatch-{handle.task_id}",
            task_id=handle.task_id,
            plan_id=f"plan-{handle.task_id}",
            plan_hash=handle.plan_hash,
            approval_id=f"approval-{handle.task_id}",
            node_id=handle.node_id,
            node_idempotency_key=handle.idempotency_key,
            adapter_id="fwi.deepwave_adapter",
            adapter_version=handle.adapter_version,
            request=durable_request,
            request_hash="sha256:" + "e" * 64,
            queue_fingerprint=copy.deepcopy(handle.fingerprint),
            state="reconciliation_required",
            handle=None,
            failure_code="SUBMISSION_RECONCILIATION_REQUIRED",
            created_at=NOW,
            dispatch_claimed_at=NOW,
            outcome_recorded_at=NOW,
        )

    def stopped_reconciliation_fixture(
        self, *, task_id: str
    ) -> tuple[
        DeepwaveAdapter,
        dict[str, Any],
        Path,
        Path,
        Any,
    ]:
        """Create one exact current attempt stopped before ready."""

        launcher = StoppedThenSuccessfulSafeLauncher()
        adapter = self.make_adapter(launcher=launcher)
        request = self.submit_kwargs(
            task_id=task_id,
            idempotency_key=f"{task_id}:invert:0001",
        )
        with self.assertRaises(AdapterUnavailable) as stopped:
            adapter.submit(**copy.deepcopy(request))
        self.assertEqual(stopped.exception.code, "WORKER_LAUNCH_FAILED")
        submission_id = adapter._submission_id(
            request["task_id"],
            request["plan_hash"],
            request["idempotency_key"],
        )
        record_path = (
            self.run_root
            / fwi_adapter_module.CONTROL_DIRECTORY
            / "submissions"
            / f"{submission_id.removeprefix('submission-')}.json"
        )
        record = json.loads(record_path.read_text(encoding="utf-8"))
        handle = adapter._handle_from_record(record)
        ticket_path = self.run_root / record["job_id"] / ".worker-launch.json"
        self.assertEqual(launcher.calls, 1)
        return adapter, request, record_path, ticket_path, handle

    def write_status(self, run_dir: Path, status: str) -> None:
        config = json.loads(
            (run_dir / "config.original.json").read_text(encoding="utf-8")
        )
        value = {
            "job_id": config["job_id"],
            "status": status,
            "stage": "complete" if status == "succeeded" else status,
            "iteration": 2 if status == "succeeded" else 0,
            "total_iterations": 2,
            "message": f"synthetic {status}",
            "updated_at": NOW,
        }
        if status == "succeeded":
            value["manifest_url"] = (
                f"/fwi-artifacts/{config['job_id']}/manifest.json"
            )
        (run_dir / "status.json").write_text(
            json.dumps(value), encoding="utf-8"
        )

    def write_success_artifacts(self, run_dir: Path) -> dict[str, Path]:
        models = run_dir / "models"
        figures = run_dir / "figures"
        models.mkdir(exist_ok=True)
        figures.mkdir(exist_ok=True)

        inverted_path = models / "inverted.npy"
        velocity = np.linspace(
            1500.0, 5500.0, 94 * 288, dtype=np.float32
        ).reshape(94, 288)
        with inverted_path.open("wb") as stream:
            np.save(stream, velocity, allow_pickle=False)

        loss_path = run_dir / "loss.csv"
        loss_path.write_text(
            "iteration,frequency_hz,loss\n"
            "0,8,1\n"
            "1,8,0.75\n"
            "2,8,0.5\n",
            encoding="utf-8",
        )
        config = json.loads(
            (run_dir / "config.original.json").read_text(encoding="utf-8")
        )
        metrics = {
            "iterations": 2,
            "initial_loss": 1.0,
            "final_loss": 0.5,
            "loss_reduction_fraction": 0.5,
            "initial_model_relative_l2": 0.25,
            "final_model_relative_l2": 0.1,
            "observed_predicted_relative_l2": 0.2,
            "model_update_relative_l2": 0.15,
            "nan_count": 0,
            "inf_count": 0,
            "elapsed_seconds": 1.25,
            "device": "cpu",
            "device_name": "synthetic-test-cpu",
            "torch_version": "test-pytorch",
            "deepwave_version": "test-deepwave",
            "optimizer": config.get("optimizer", "adam"),
            "learning_rate": config.get("learning_rate", 10.0),
            "gradient_clip_quantile": config.get(
                "gradient_clip_quantile", 0.98
            ),
            # These real legacy shapes are deliberately non-scalar.  The
            # standard ArtifactManifest metrics field must not copy them.
            "model_shape": [94, 288],
            "gradient_clip_values": [0.1, 0.05],
        }
        (run_dir / "metrics.json").write_text(
            json.dumps(metrics), encoding="utf-8"
        )

        figure_specs = (
            ("true_model_figure", "true_model", (1440, 608)),
            ("initial_model_figure", "initial_model", (1440, 608)),
            ("inverted_model_figure", "inverted_model", (1440, 608)),
            ("model_error_figure", "model_error", (1440, 608)),
            ("shot_gathers_figure", "shot_gathers", (2160, 800)),
            ("loss_curve_figure", "loss_curve", (1120, 720)),
        )
        figure_paths: dict[str, Path] = {}
        for index, (port, figure_id, size) in enumerate(figure_specs, start=1):
            path = figures / f"{figure_id}.png"
            Image.new(
                "RGBA",
                size,
                (20 * index, 255 - 20 * index, 80 + 10 * index, 255),
            ).save(path, format="PNG")
            figure_paths[port] = path
        legacy_manifest = {
            "type": "fwi_result",
            "schema_version": "1",
            "job_id": config["job_id"],
            "status": "succeeded",
            "model_id": "marmousi_94_288",
            "physics": "2d_acoustic_constant_density",
            "parameter": "vp",
            "true_model_known": True,
            "observed_data_origin": "synthetic fixture",
            "inversion_propagator": "Deepwave scalar",
            "command": "invert",
            "summary": "synthetic adapter fixture",
            "failure_reason": None,
            "disclaimer": "test-only synthetic result",
            "metrics": metrics,
            "plot_details": {},
            # Every path/title/url below is deliberately untrusted.  Adapter
            # 1.4 must use only its fixed figure specification and real bytes.
            "figures": [
                {
                    "id": figure_id,
                    "title": f"untrusted {figure_id}",
                    "path": f"/private/untrusted/{figure_id}.png",
                    "url": (
                        f"/fwi-artifacts/{config['job_id']}"
                        f"/untrusted/{figure_id}.png"
                    ),
                    "mime_type": "image/png",
                }
                for _, figure_id, _ in figure_specs
            ],
        }
        (run_dir / "manifest.json").write_text(
            json.dumps(legacy_manifest), encoding="utf-8"
        )
        self.write_status(run_dir, "succeeded")
        return {
            "inverted_model": inverted_path,
            "loss": loss_path,
            **figure_paths,
        }

    def test_validate_and_estimate_are_strict_and_side_effect_free(self) -> None:
        before = self.root_snapshot()
        valid = self.execution_kwargs()
        self.adapter.validate(**copy.deepcopy(valid))
        demo_cuda = copy.deepcopy(valid)
        demo_cuda["parameters"].update(
            {"preset": "fwi_demo", "device": "cuda", "iterations": 10000}
        )
        demo_cuda["resources"].update({"device": "cuda", "gpu_count": 1})
        self.adapter.validate(**demo_cuda)

        # The local probe proves immutable physical identity, not ACL.  A real
        # Registry scope must be accepted even though the probe's placeholder
        # access_scope differs.
        physical = dataset_ref()
        physical["access_scope"] = {
            "project_id": "adapter-validation",
            "principals": ["adapter-validation"],
            "permissions": ["read", "execute"],
        }
        split_boundary = DeepwaveAdapter(
            run_root=self.run_root,
            launcher=FakeLauncher(),
            dataset_identity_provider=DatasetIdentityProvider(physical),
            registry_snapshot_provider=self.registry_provider,
            device_validator=self.device_validator,
            fingerprint_factory=self.fingerprint_factory,
            clock=lambda: NOW,
        )
        split_boundary.validate(**copy.deepcopy(valid))
        unbound_registry = DeepwaveAdapter(
            run_root=self.run_root,
            launcher=FakeLauncher(),
            dataset_identity_provider=DatasetIdentityProvider(physical),
            device_validator=self.device_validator,
            fingerprint_factory=self.fingerprint_factory,
            clock=lambda: NOW,
        )
        with self.assertRaisesRegex(
            RuntimeError, "REGISTRY_SNAPSHOT_PROVIDER_REQUIRED"
        ):
            unbound_registry.validate(**copy.deepcopy(valid))
        first_estimate = _plain(self.adapter.estimate(**copy.deepcopy(valid)))
        second_estimate = _plain(self.adapter.estimate(**copy.deepcopy(valid)))
        self.assertEqual(first_estimate, second_estimate)
        self.assertEqual(self.root_snapshot(), before)
        self.assertEqual(self.launcher.calls, [])

        forward = copy.deepcopy(valid)
        forward["task_type"] = "acoustic_forward_2d"
        forward["parameters"] = {
            "preset": "forward",
            "device": "cpu",
            "iterations": 0,
            "seed": 2026,
        }
        self.assert_input_rejected(
            forward, code="TASK_TYPE_UNSUPPORTED_IN_P1"
        )

        invalid_cases: dict[str, dict[str, Any]] = {}
        wrong_algorithm = copy.deepcopy(valid)
        wrong_algorithm["algorithm"]["version"] = "2.0.0"
        invalid_cases["unregistered algorithm version"] = wrong_algorithm

        path_injection = copy.deepcopy(valid)
        path_injection["dataset"]["server_path"] = "/etc/passwd"
        invalid_cases["dataset path injection"] = path_injection

        dataset_hash_drift = copy.deepcopy(valid)
        dataset_hash_drift["dataset"]["content_hash"] = "sha256:" + "f" * 64
        invalid_cases["dataset identity drift"] = dataset_hash_drift

        wrong_project = copy.deepcopy(valid)
        wrong_project["project_id"] = "project-attacker"
        invalid_cases["current project mismatch"] = wrong_project

        wrong_principal = copy.deepcopy(valid)
        wrong_principal["principal_id"] = "user-attacker"
        invalid_cases["current principal mismatch"] = wrong_principal

        forged_scope = copy.deepcopy(valid)
        forged_scope["project_id"] = "project-attacker"
        forged_scope["principal_id"] = "user-attacker"
        forged_scope["dataset"]["access_scope"].update(
            {
                "project_id": "project-attacker",
                "principals": ["user-attacker"],
            }
        )
        invalid_cases["scope differs from trusted registry snapshot"] = forged_scope

        extra_parameter = copy.deepcopy(valid)
        extra_parameter["parameters"]["shell"] = "rm -rf /"
        invalid_cases["extra parameter"] = extra_parameter

        resource_mismatch = copy.deepcopy(valid)
        resource_mismatch["resources"].update(
            {"device": "cuda", "gpu_count": 1}
        )
        invalid_cases["parameter resource mismatch"] = resource_mismatch

        excessive_resources = copy.deepcopy(valid)
        excessive_resources["resources"]["cpu_cores"] = 17
        invalid_cases["algorithm resource limit"] = excessive_resources

        invalid_seed = copy.deepcopy(valid)
        invalid_seed["parameters"]["seed"] = True
        invalid_cases["boolean seed"] = invalid_seed

        for value in (0, 10001, "2", 2.0, True):
            invalid = copy.deepcopy(valid)
            invalid["parameters"]["iterations"] = value
            invalid_cases[f"invalid iterations {value!r}"] = invalid

        unsupported_preset = copy.deepcopy(valid)
        unsupported_preset["parameters"].update(
            {"preset": "forward", "iterations": 0}
        )
        invalid_cases["forward preset on fwi task"] = unsupported_preset

        for label, invalid in invalid_cases.items():
            with self.subTest(label=label):
                self.assert_input_rejected(invalid)
        self.assertEqual(self.root_snapshot(), before)
        self.assertEqual(self.launcher.calls, [])

    def test_optimizer_specific_bounds_and_worker_config_conversion(self) -> None:
        before = self.root_snapshot()
        valid_cases = (
            ("adam", 100, 0.1),
            ("adam", 100_000, 100.0),
            ("sgd", 100_000_000, 100_000.0),
            ("sgd", 1_000_000_000_000, 1_000_000_000.0),
        )
        for optimizer, learning_rate_milli, expected_rate in valid_cases:
            with self.subTest(
                optimizer=optimizer, learning_rate_milli=learning_rate_milli
            ):
                request = self.execution_kwargs()
                request["parameters"].update(
                    {
                        "optimizer": optimizer,
                        "learning_rate_milli": learning_rate_milli,
                    }
                )
                validated = self.adapter.validate(**request)
                self.assertEqual(validated.parameters["optimizer"], optimizer)
                self.assertEqual(
                    validated.parameters["learning_rate_milli"], learning_rate_milli
                )
                self.assertEqual(validated.worker_config["optimizer"], optimizer)
                self.assertEqual(
                    validated.worker_config["learning_rate"], expected_rate
                )
                self.assertEqual(
                    validated.worker_config["gradient_clip_quantile"], 0.98
                )

        invalid_cases = (
            ("adam", 99),
            ("adam", 100_001),
            ("sgd", 99_999_999),
            ("sgd", 1_000_000_000_001),
            ("adam", True),
            ("adam", 10_000.0),
            ("adam", "10000"),
            ("lbfgs", 10_000),
        )
        for optimizer, learning_rate_milli in invalid_cases:
            with self.subTest(
                invalid_optimizer=optimizer,
                invalid_learning_rate_milli=learning_rate_milli,
            ):
                request = self.execution_kwargs()
                request["parameters"].update(
                    {
                        "optimizer": optimizer,
                        "learning_rate_milli": learning_rate_milli,
                    }
                )
                self.assert_input_rejected(request, code="PARAMETERS_INVALID")

        for missing in ("optimizer", "learning_rate_milli"):
            with self.subTest(missing=missing):
                request = self.execution_kwargs()
                request["parameters"].pop(missing)
                self.assert_input_rejected(request, code="PARAMETERS_INVALID")

        self.assertEqual(self.root_snapshot(), before)
        self.assertEqual(self.launcher.calls, [])

    def test_submit_writes_fixed_worker_config_and_pathless_handle(self) -> None:
        handle, run_dir = self.submit_and_run_dir()
        self.assertEqual(len(self.launcher.calls), 1)
        call = self.launcher.calls[0]
        self.assertEqual(call["command"], "invert")
        self.assertEqual(call["run_root"].resolve(), self.run_root.resolve())
        self.assertEqual(call["run_dir"].parent, self.run_root.resolve())
        self.assertEqual(
            call["config_path"], call["run_dir"] / "config.original.json"
        )

        config = json.loads(
            call["config_path"].read_text(encoding="utf-8")
        )
        self.assertEqual(
            config,
            {
                "job_id": config["job_id"],
                "model_id": "marmousi_94_288",
                "preset": "fwi_smoke",
                "device": "cpu",
                "iterations": 2,
                "seed": 2026,
                "optimizer": "adam",
                "learning_rate": 10.0,
                "gradient_clip_quantile": 0.98,
            },
        )
        handle_document = _plain(handle)
        self.assertIsInstance(handle_document, dict)
        self.assertEqual(handle_document["job_id"], config["job_id"])
        self.assertEqual(handle_document["task_id"], "task-001")
        self.assertEqual(handle_document["node_id"], "invert")
        self.assertEqual(
            handle_document["idempotency_key"], "task-001:invert:0001"
        )
        private_record = json.loads(
            self.submission_record_path(handle).read_text(encoding="utf-8")
        )
        self.assertEqual(handle_document["fingerprint"], private_record["fingerprint"])
        encoded_handle = json.dumps(handle_document, sort_keys=True)
        self.assertNotIn(str(self.run_root), encoded_handle)
        self.assertNotIn("run_dir", handle_document)
        self.assertNotIn("config_path", handle_document)
        self.assertEqual(_status_name(self.adapter.status(handle)), "queued")
        self.assertEqual(run_dir, call["run_dir"])

    def test_lookup_existing_handle_adopts_only_an_exact_launched_record(self) -> None:
        request = self.submit_kwargs(
            task_id="task-receipt-lookup",
            idempotency_key="task-receipt-lookup:invert:0001",
        )
        before_missing = self.root_snapshot()
        with self.assertRaises(RuntimeError) as missing:
            self.adapter.lookup_existing_handle(**copy.deepcopy(request))
        self.assertEqual(
            getattr(missing.exception, "code", None),
            "ADAPTER_SUBMISSION_NOT_FOUND",
        )
        self.assertNotIn(str(self.run_root), str(missing.exception))
        self.assertEqual(self.root_snapshot(), before_missing)
        self.assertEqual(self.launcher.calls, [])

        launched = self.adapter.submit(**copy.deepcopy(request))
        replacement_launcher = FakeLauncher()
        reopened = self.make_adapter(launcher=replacement_launcher)
        before_lookup = self.root_snapshot()
        readiness_counts = (
            len(self.dataset_provider.calls),
            len(self.registry_provider.calls),
            len(self.device_validator.calls),
            len(self.fingerprint_factory.calls),
        )
        recovered = reopened.lookup_existing_handle(**copy.deepcopy(request))
        self.assertEqual(_plain(recovered), _plain(launched))
        self.assertEqual(replacement_launcher.calls, [])
        self.assertEqual(self.root_snapshot(), before_lookup)
        self.assertEqual(
            (
                len(self.dataset_provider.calls),
                len(self.registry_provider.calls),
                len(self.device_validator.calls),
                len(self.fingerprint_factory.calls),
            ),
            readiness_counts,
        )

        changed = copy.deepcopy(request)
        changed["parameters"]["iterations"] = 3
        with self.assertRaises(RuntimeError) as conflict:
            reopened.lookup_existing_handle(**changed)
        self.assertEqual(
            getattr(conflict.exception, "code", None),
            "ADAPTER_IDEMPOTENCY_CONFLICT",
        )
        self.assertEqual(replacement_launcher.calls, [])

    def test_lookup_existing_handle_defers_nonlaunched_states_without_launch(self) -> None:
        expected_codes = {
            "preparing": "ADAPTER_SUBMISSION_PREPARING",
            "launching": "ADAPTER_SUBMISSION_LAUNCH_AMBIGUOUS",
            "failed": "WORKER_LAUNCH_FAILED",
        }
        replacement_launcher = FakeLauncher()
        reopened = self.make_adapter(launcher=replacement_launcher)
        for index, (launch_state, expected_code) in enumerate(
            expected_codes.items(), start=1
        ):
            with self.subTest(launch_state=launch_state):
                request = self.submit_kwargs(
                    task_id=f"task-receipt-state-{index}",
                    idempotency_key=(
                        f"task-receipt-state-{index}:invert:0001"
                    ),
                )
                handle = self.adapter.submit(**copy.deepcopy(request))
                record_path = self.submission_record_path(handle)
                record = json.loads(record_path.read_text(encoding="utf-8"))
                record["launch_state"] = launch_state
                self.adapter._write_submission(record_path, record)
                before_lookup = self.root_snapshot()

                with self.assertRaises(RuntimeError) as raised:
                    reopened.lookup_existing_handle(**copy.deepcopy(request))
                self.assertEqual(
                    getattr(raised.exception, "code", None), expected_code
                )
                self.assertNotIn(str(self.run_root), str(raised.exception))
                self.assertEqual(self.root_snapshot(), before_lookup)
                self.assertEqual(replacement_launcher.calls, [])

    def test_lookup_promotes_only_the_exact_fenced_ready_attempt(self) -> None:
        request = self.submit_kwargs(
            task_id="task-fenced-ready",
            idempotency_key="task-fenced-ready:invert:0001",
        )
        launched = self.adapter.submit(**copy.deepcopy(request))
        record_path = self.submission_record_path(launched)
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["launch_state"] = "launching"
        self.adapter._write_submission(record_path, record)
        binding = binding_from_submission_record(record)
        run_dir = self.run_root / launched.job_id

        lease = ParentLaunchLease.acquire(
            self.run_root, run_dir, max_active=2
        )
        lease.mark_spawned(os.getpid())
        heartbeat = WorkerHeartbeat(
            run_root=self.run_root,
            run_dir=run_dir,
            attempt_id=binding.attempt_id,
            attempt_fd=os.dup(lease.attempt_fd),
            capacity_fd=os.dup(lease.capacity_fd),
            interval_seconds=0.02,
        )
        lease.close_parent()
        heartbeat.start()
        try:
            replacement_launcher = FakeLauncher()
            reopened = self.make_adapter(launcher=replacement_launcher)
            observed = reopened.observe_existing_worker_attempt(
                **copy.deepcopy(request)
            )
            self.assertEqual(observed["handle"], launched.as_dict())
            self.assertEqual(
                observed["evidence"]["attempt_id"], binding.attempt_id
            )
            self.assertEqual(
                observed["evidence"]["heartbeat"]["state"], "running"
            )
            self.assertNotIn(str(self.run_root), json.dumps(observed))
            recovered = reopened.lookup_existing_handle(
                **copy.deepcopy(request)
            )
            self.assertEqual(_plain(recovered), _plain(launched))
            self.assertEqual(replacement_launcher.calls, [])
            promoted = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(promoted["launch_state"], "launched")
            self.assertEqual(
                promoted["launch_attempt"], record["launch_attempt"]
            )
        finally:
            heartbeat.stop("succeeded")

    def test_reconciliation_probe_promotes_managed_ready_without_launch(
        self,
    ) -> None:
        request = self.submit_kwargs(
            task_id="task-reconcile-managed-ready",
            idempotency_key="task-reconcile-managed-ready:invert:0001",
        )
        handle = self.adapter.submit(**copy.deepcopy(request))
        record_path = self.submission_record_path(handle)
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["launch_state"] = "launching"
        self.adapter._write_submission(record_path, record)
        run_dir = self.run_root / handle.job_id
        _, heartbeat = self.start_exact_worker(handle, run_dir)
        try:
            replacement_launcher = FakeLauncher()
            dispatcher = DeepwaveTaskDispatcher(
                self.make_adapter(launcher=replacement_launcher)
            )
            intent = self.reconciliation_intent(handle, request)
            before_record = record_path.read_bytes()
            before_paths = self.root_snapshot()

            proof = dispatcher.probe_existing_dispatch_receipt(intent)

            self.assertEqual(proof.evidence_kind, "managed_worker_receipt")
            self.assertEqual(proof.handle, handle.as_dict())
            self.assertEqual(proof.private_schema_version, "1.1.0")
            self.assertIsNone(proof.receipt_record_hash)
            self.assertIsNotNone(proof.evidence)
            assert proof.evidence is not None
            self.assertEqual(proof.evidence["ticket"]["state"], "spawned")
            self.assertIsNotNone(proof.evidence["ready"])
            self.assertIsNotNone(proof.evidence["heartbeat"])
            promoted = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertNotEqual(record_path.read_bytes(), before_record)
            self.assertEqual(promoted["launch_state"], "launched")
            self.assertEqual(promoted["launch_attempt"], record["launch_attempt"])
            self.assertEqual(self.root_snapshot(), before_paths)
            self.assertEqual(replacement_launcher.calls, [])
            matrix_proof = dispatcher.probe_dispatch_reconciliation(intent)
            self.assertIsInstance(matrix_proof, DispatchReceiptProbe)
            assert isinstance(matrix_proof, DispatchReceiptProbe)
            self.assertEqual(matrix_proof.handle, handle.as_dict())
            self.assertEqual(
                matrix_proof.evidence_kind, "managed_worker_receipt"
            )
        finally:
            heartbeat.stop("succeeded")

    def test_reconciliation_probe_reads_only_launched_private_v1_receipt(
        self,
    ) -> None:
        request = self.submit_kwargs(
            task_id="task-reconcile-private-v1",
            idempotency_key="task-reconcile-private-v1:invert:0001",
        )
        handle = self.adapter.submit(**copy.deepcopy(request))
        record_path = self.submission_record_path(handle)
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["schema_version"] = "1.0.0"
        record.pop("launch_attempt")
        self.adapter._write_submission(record_path, record)
        replacement_launcher = FakeLauncher()
        dispatcher = DeepwaveTaskDispatcher(
            self.make_adapter(launcher=replacement_launcher)
        )
        intent = self.reconciliation_intent(handle, request)
        before_record = record_path.read_bytes()
        before_paths = self.root_snapshot()

        proof = dispatcher.probe_existing_dispatch_receipt(intent)

        self.assertEqual(proof.evidence_kind, "private_receipt")
        self.assertEqual(proof.handle, handle.as_dict())
        self.assertIsNone(proof.evidence)
        self.assertEqual(proof.private_schema_version, "1.0.0")
        self.assertEqual(proof.receipt_record_hash, record["record_hash"])
        self.assertEqual(record_path.read_bytes(), before_record)
        self.assertEqual(self.root_snapshot(), before_paths)
        self.assertEqual(replacement_launcher.calls, [])
        matrix_proof = dispatcher.probe_dispatch_reconciliation(intent)
        self.assertIsInstance(matrix_proof, DispatchReceiptProbe)
        assert isinstance(matrix_proof, DispatchReceiptProbe)
        self.assertEqual(matrix_proof.evidence_kind, "private_receipt")
        self.assertEqual(matrix_proof.handle, handle.as_dict())

    def test_reconciliation_probe_defers_nonpositive_evidence_without_launch(
        self,
    ) -> None:
        request = self.submit_kwargs(
            task_id="task-reconcile-staged",
            idempotency_key="task-reconcile-staged:invert:0001",
        )
        handle = self.adapter.submit(**copy.deepcopy(request))
        replacement_launcher = FakeLauncher()
        reopened = self.make_adapter(launcher=replacement_launcher)
        dispatcher = DeepwaveTaskDispatcher(reopened)
        intent = self.reconciliation_intent(handle, request)
        record_path = self.submission_record_path(handle)
        before_record = record_path.read_bytes()
        before_paths = self.root_snapshot()

        with self.assertRaises(DispatchDeferred) as staged:
            dispatcher.probe_existing_dispatch_receipt(intent)
        self.assertEqual(staged.exception.code, "DISPATCH_RECEIPT_NOT_READY")
        self.assertEqual(record_path.read_bytes(), before_record)
        self.assertEqual(self.root_snapshot(), before_paths)

        with patch.object(
            reopened,
            "probe_existing_dispatch_receipt",
            side_effect=AdapterUnavailable(
                "ADAPTER_SUBMISSION_BUSY: submission lock is held"
            ),
        ), self.assertRaises(DispatchDeferred) as busy:
            dispatcher.probe_existing_dispatch_receipt(intent)
        self.assertEqual(busy.exception.code, "ADAPTER_SUBMISSION_BUSY")

        stopped_evidence = {
            "ticket": {"state": "spawned"},
            "ready": {"record_hash": "sha256:" + "a" * 64},
            "heartbeat": {"state": "stopped"},
            "submission_id": handle.submission_id,
            "job_id": handle.job_id,
            "request_hash": handle.request_hash,
        }
        with patch.object(
            reopened,
            "probe_existing_dispatch_receipt",
            return_value=AdapterExistingDispatchReceiptProof(
                evidence_kind="managed_worker_receipt",
                handle=handle,
                private_schema_version="1.1.0",
                receipt_record_hash=None,
                worker_evidence=stopped_evidence,
            ),
        ), self.assertRaises(DispatchDeferred) as stopped:
            dispatcher.probe_existing_dispatch_receipt(intent)
        self.assertEqual(stopped.exception.code, "DISPATCH_RECEIPT_PROBE_INVALID")

        resolved = dataclasses.replace(
            intent,
            state="dispatched",
            handle=handle.as_dict(),
            failure_code=None,
        )
        with patch.object(
            reopened,
            "probe_existing_dispatch_receipt",
        ) as adapter_probe, self.assertRaises(DispatchDeferred) as history:
            dispatcher.probe_existing_dispatch_receipt(resolved)
        self.assertEqual(
            history.exception.code, "DISPATCH_RECEIPT_PROBE_UNSUPPORTED"
        )
        adapter_probe.assert_not_called()
        self.assertEqual(replacement_launcher.calls, [])

    def test_reconciliation_matrix_proves_all_idle_pre_running_ticket_states(
        self,
    ) -> None:
        ticket_states = {
            "staged": (None, None, None),
            "leased": (0, 1, None),
            "spawned": (0, 1, 4242),
            "failed": (0, 1, None),
        }
        for ticket_state, projection in ticket_states.items():
            with self.subTest(ticket_state=ticket_state):
                task_id = f"task-reconcile-negative-{ticket_state}"
                (
                    adapter,
                    request,
                    record_path,
                    ticket_path,
                    handle,
                ) = self.stopped_reconciliation_fixture(task_id=task_id)
                record = json.loads(record_path.read_text(encoding="utf-8"))
                record.pop("launch_failure")
                adapter._write_submission(record_path, record)
                ticket = json.loads(ticket_path.read_text(encoding="utf-8"))
                ticket.update(
                    {
                        "state": ticket_state,
                        "capacity_slot": projection[0],
                        "capacity_generation": projection[1],
                        "worker_pid": projection[2],
                    }
                )
                ticket.pop("record_hash")
                ticket["record_hash"] = fwi_adapter_module._sha256_document(
                    ticket
                )
                ticket_path.write_text(json.dumps(ticket), encoding="utf-8")
                replacement_launcher = FakeLauncher()
                reopened = self.make_adapter(launcher=replacement_launcher)
                dispatcher = DeepwaveTaskDispatcher(reopened)
                intent = self.reconciliation_intent(handle, request)
                before_record = record_path.read_bytes()
                before_paths = self.root_snapshot()

                with (
                    patch.object(reopened, "submit") as submit,
                    patch.object(reopened, "retry_pre_running") as retry_pre,
                    patch.object(reopened, "retry_worker_exit") as retry_exit,
                ):
                    proof = dispatcher.probe_dispatch_reconciliation(intent)

                self.assertIsInstance(proof, DispatchNotStartedProof)
                assert isinstance(proof, DispatchNotStartedProof)
                self.assertEqual(proof.result, "not_dispatched")
                self.assertEqual(
                    proof.evidence_kind, "managed_pre_running_failure"
                )
                self.assertEqual(proof.adapter_version, "1.5.0")
                self.assertEqual(proof.private_schema_version, "1.2.0")
                self.assertEqual(
                    proof.private_record_hash, record["record_hash"]
                )
                self.assertEqual(proof.attempt_number, 1)
                self.assertEqual(proof.evidence["ticket"]["state"], ticket_state)
                self.assertIsNone(proof.evidence["ready"])
                self.assertIsNone(proof.evidence["heartbeat"])
                evidence_hash = fwi_adapter_module._sha256_document(
                    proof.evidence
                )
                expected_proof_hash = fwi_adapter_module._sha256_document(
                    {
                        "schema_version": "1.0.0",
                        "result": "not_dispatched",
                        "evidence_kind": "managed_pre_running_failure",
                        "adapter_version": "1.5.0",
                        "private_schema_version": "1.2.0",
                        "private_record_hash": record["record_hash"],
                        "attempt_id": proof.attempt_id,
                        "attempt_number": 1,
                        "evidence_hash": evidence_hash,
                    }
                )
                self.assertEqual(proof.private_proof_hash, expected_proof_hash)
                self.assertNotIn(
                    str(self.run_root), json.dumps(dataclasses.asdict(proof))
                )
                self.assertEqual(record_path.read_bytes(), before_record)
                self.assertEqual(self.root_snapshot(), before_paths)
                self.assertEqual(replacement_launcher.calls, [])
                submit.assert_not_called()
                retry_pre.assert_not_called()
                retry_exit.assert_not_called()

    def test_reconciliation_matrix_supports_historical_v1_4_stopped_proof(
        self,
    ) -> None:
        (
            adapter,
            request,
            record_path,
            ticket_path,
            _handle,
        ) = self.stopped_reconciliation_fixture(
            task_id="task-reconcile-negative-v1-4"
        )
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["schema_version"] = "1.1.0"
        record.pop("attempt_history")
        record.pop("launch_failure")
        record["adapter_version"] = "1.4.0"
        record["algorithm"]["version"] = "1.4.0"
        record["fingerprint"]["adapter_version"] = "1.4.0"
        record["fingerprint"]["algorithm"]["version"] = "1.4.0"
        historical_config_hash = "sha256:" + hashlib.sha256(
            b"historical-v1.4-reconciliation"
        ).hexdigest()
        record["normalized_config_hash"] = historical_config_hash
        record["fingerprint"][
            "normalized_config_hash"
        ] = historical_config_hash
        record["request_hash"] = fwi_adapter_module._sha256_document(
            adapter._record_request_payload(record)
        )
        historical_binding = LaunchAttemptBinding(
            submission_id=record["submission_id"],
            attempt_id=record["launch_attempt"]["attempt_id"],
            attempt_number=1,
            job_id=record["job_id"],
            request_hash=record["request_hash"],
            created_at=record["created_at"],
        )
        record["launch_attempt"] = historical_binding.record()
        adapter._write_submission(record_path, record)
        ticket = json.loads(ticket_path.read_text(encoding="utf-8"))
        ticket["request_hash"] = historical_binding.request_hash
        ticket["binding_hash"] = historical_binding.binding_hash
        ticket.pop("record_hash")
        ticket["record_hash"] = fwi_adapter_module._sha256_document(ticket)
        ticket_path.write_text(json.dumps(ticket), encoding="utf-8")
        request["algorithm"]["version"] = "1.4.0"
        historical_handle = adapter._handle_from_record(record)
        replacement_launcher = FakeLauncher()
        reopened = self.make_adapter(launcher=replacement_launcher)
        dispatcher = DeepwaveTaskDispatcher(reopened)
        intent = self.reconciliation_intent(historical_handle, request)
        before_record = record_path.read_bytes()
        before_paths = self.root_snapshot()

        proof = dispatcher.probe_dispatch_reconciliation(intent)

        self.assertIsInstance(proof, DispatchNotStartedProof)
        assert isinstance(proof, DispatchNotStartedProof)
        self.assertEqual(proof.adapter_version, "1.4.0")
        self.assertEqual(proof.private_schema_version, "1.1.0")
        self.assertEqual(proof.private_record_hash, record["record_hash"])
        self.assertEqual(proof.evidence["attempt_id"], historical_binding.attempt_id)
        self.assertEqual(record_path.read_bytes(), before_record)
        self.assertEqual(self.root_snapshot(), before_paths)
        self.assertEqual(replacement_launcher.calls, [])

    def test_reconciliation_matrix_marks_active_fence_transient_without_launch(
        self,
    ) -> None:
        (
            _adapter,
            request,
            record_path,
            _ticket_path,
            handle,
        ) = self.stopped_reconciliation_fixture(
            task_id="task-reconcile-active-fence"
        )
        record = json.loads(record_path.read_text(encoding="utf-8"))
        fence_path = (
            self.run_root
            / fwi_adapter_module.CONTROL_DIRECTORY
            / "worker-capacity"
            / "attempts"
            / f"{record['submission_id']}.lock"
        )
        descriptor = os.open(fence_path, os.O_RDWR)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        replacement_launcher = FakeLauncher()
        dispatcher = DeepwaveTaskDispatcher(
            self.make_adapter(launcher=replacement_launcher)
        )
        intent = self.reconciliation_intent(handle, request)
        before_record = record_path.read_bytes()
        before_paths = self.root_snapshot()
        try:
            result = dispatcher.probe_dispatch_reconciliation(intent)
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

        self.assertEqual(
            result,
            DispatchReconciliationDeferred(
                classification="transient",
                failure_code="WORKER_ATTEMPT_BUSY",
            ),
        )
        self.assertEqual(record_path.read_bytes(), before_record)
        self.assertEqual(self.root_snapshot(), before_paths)
        self.assertEqual(replacement_launcher.calls, [])

    def test_reconciliation_matrix_fails_closed_for_uncertain_states(
        self,
    ) -> None:
        request = self.submit_kwargs(
            task_id="task-reconcile-uncertain-unfenced",
            idempotency_key="task-reconcile-uncertain-unfenced:invert:0001",
        )
        handle = self.adapter.submit(**copy.deepcopy(request))
        replacement_launcher = FakeLauncher()
        reopened = self.make_adapter(launcher=replacement_launcher)
        dispatcher = DeepwaveTaskDispatcher(reopened)
        intent = self.reconciliation_intent(handle, request)
        before_paths = self.root_snapshot()

        unfenced = dispatcher.probe_dispatch_reconciliation(intent)

        self.assertIsInstance(unfenced, DispatchReconciliationDeferred)
        assert isinstance(unfenced, DispatchReconciliationDeferred)
        self.assertEqual(unfenced.classification, "uncertain")
        self.assertEqual(
            unfenced.failure_code, "DISPATCH_RECONCILIATION_UNCERTAIN"
        )
        self.assertEqual(self.root_snapshot(), before_paths)

        old_request = copy.deepcopy(intent.request)
        old_request["algorithm"]["version"] = "1.3.0"
        old_fingerprint = copy.deepcopy(intent.queue_fingerprint)
        old_fingerprint["adapter_version"] = "1.3.0"
        old_fingerprint["algorithm"]["version"] = "1.3.0"
        old_intent = dataclasses.replace(
            intent,
            adapter_version="1.3.0",
            request=old_request,
            queue_fingerprint=old_fingerprint,
        )
        with patch.object(reopened, "probe_dispatch_reconciliation") as probe:
            unsupported = dispatcher.probe_dispatch_reconciliation(old_intent)
        self.assertEqual(
            unsupported,
            DispatchReconciliationDeferred(
                classification="uncertain",
                failure_code="DISPATCH_RECONCILIATION_UNSUPPORTED",
            ),
        )
        probe.assert_not_called()
        self.assertEqual(replacement_launcher.calls, [])

    def test_reconciliation_matrix_rejects_tampered_negative_proof(
        self,
    ) -> None:
        (
            _adapter,
            request,
            _record_path,
            _ticket_path,
            handle,
        ) = self.stopped_reconciliation_fixture(
            task_id="task-reconcile-negative-tamper"
        )
        replacement_launcher = FakeLauncher()
        reopened = self.make_adapter(launcher=replacement_launcher)
        adapter_request = copy.deepcopy(request)
        normalized_config_hash = handle.fingerprint["normalized_config_hash"]
        adapter_proof = reopened.probe_dispatch_reconciliation(
            **adapter_request,
            normalized_config_hash=normalized_config_hash,
        )
        self.assertIsInstance(adapter_proof, AdapterDispatchNotStartedProof)
        assert isinstance(adapter_proof, AdapterDispatchNotStartedProof)
        tampered = dataclasses.replace(
            adapter_proof,
            private_proof_hash="sha256:" + "f" * 64,
        )
        dispatcher = DeepwaveTaskDispatcher(reopened)
        intent = self.reconciliation_intent(handle, request)

        with patch.object(
            reopened,
            "probe_dispatch_reconciliation",
            return_value=tampered,
        ):
            result = dispatcher.probe_dispatch_reconciliation(intent)

        self.assertEqual(
            result,
            DispatchReconciliationDeferred(
                classification="uncertain",
                failure_code="DISPATCH_RECONCILIATION_PROBE_INVALID",
            ),
        )

        foreign_evidence = copy.deepcopy(adapter_proof.evidence)
        foreign_submission_id = "submission-" + "f" * 64
        foreign_request_hash = "sha256:" + "e" * 64
        foreign_job_id = (
            foreign_evidence["job_id"].rsplit("-", 1)[0]
            + "-"
            + hashlib.sha256(foreign_submission_id.encode("utf-8")).hexdigest()[:12]
        )
        foreign_evidence.update(
            {
                "submission_id": foreign_submission_id,
                "job_id": foreign_job_id,
                "request_hash": foreign_request_hash,
            }
        )
        binding_payload = {
            key: foreign_evidence[key]
            for key in (
                "schema_version",
                "submission_id",
                "attempt_id",
                "attempt_number",
                "job_id",
                "request_hash",
                "created_at",
            )
        }
        foreign_evidence["binding_hash"] = (
            fwi_adapter_module._sha256_document(binding_payload)
        )
        foreign_ticket = foreign_evidence["ticket"]
        foreign_ticket["record_hash"] = fwi_adapter_module._sha256_document(
            {
                **binding_payload,
                "binding_hash": foreign_evidence["binding_hash"],
                **{
                    key: foreign_ticket[key]
                    for key in (
                        "state",
                        "capacity_slot",
                        "capacity_generation",
                        "worker_pid",
                        "updated_at",
                    )
                },
            }
        )
        foreign_evidence_hash = fwi_adapter_module._sha256_document(
            foreign_evidence
        )
        foreign_proof = dataclasses.replace(
            adapter_proof,
            evidence=foreign_evidence,
            private_proof_hash=fwi_adapter_module._sha256_document(
                {
                    "schema_version": "1.0.0",
                    "result": "not_dispatched",
                    "evidence_kind": "managed_pre_running_failure",
                    "adapter_version": adapter_proof.adapter_version,
                    "private_schema_version": adapter_proof.private_schema_version,
                    "private_record_hash": adapter_proof.private_record_hash,
                    "attempt_id": adapter_proof.attempt_id,
                    "attempt_number": adapter_proof.attempt_number,
                    "evidence_hash": foreign_evidence_hash,
                }
            ),
        )
        with patch.object(
            reopened,
            "probe_dispatch_reconciliation",
            return_value=foreign_proof,
        ):
            cross_bound = dispatcher.probe_dispatch_reconciliation(intent)
        self.assertEqual(
            cross_bound,
            DispatchReconciliationDeferred(
                classification="uncertain",
                failure_code="DISPATCH_RECONCILIATION_PROBE_INVALID",
            ),
        )
        self.assertEqual(replacement_launcher.calls, [])

    def test_preparing_observation_does_not_recreate_a_missing_job_directory(
        self,
    ) -> None:
        request = self.submit_kwargs(
            task_id="task-preparing-observation",
            idempotency_key="task-preparing-observation:invert:0001",
        )
        handle = self.adapter.submit(**copy.deepcopy(request))
        record_path = self.submission_record_path(handle)
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["launch_state"] = "preparing"
        self.adapter._write_submission(record_path, record)
        run_dir = self.run_root / handle.job_id
        shutil.rmtree(run_dir)

        replacement_launcher = FakeLauncher()
        reopened = self.make_adapter(launcher=replacement_launcher)
        with self.assertRaises(RuntimeError) as raised:
            reopened.observe_existing_worker_attempt(**copy.deepcopy(request))
        self.assertEqual(
            getattr(raised.exception, "code", None),
            "WORKER_EVIDENCE_NOT_READY",
        )
        self.assertFalse(run_dir.exists())
        self.assertEqual(replacement_launcher.calls, [])

    def test_lookup_existing_handle_rejects_malformed_and_symlink_records(self) -> None:
        replacement_launcher = FakeLauncher()
        reopened = self.make_adapter(launcher=replacement_launcher)

        malformed_request = self.submit_kwargs(
            task_id="task-receipt-malformed",
            idempotency_key="task-receipt-malformed:invert:0001",
        )
        malformed_handle = self.adapter.submit(
            **copy.deepcopy(malformed_request)
        )
        malformed_path = self.submission_record_path(malformed_handle)
        malformed_path.write_text("{not-json", encoding="utf-8")
        with self.assertRaises(RuntimeError) as malformed:
            reopened.lookup_existing_handle(
                **copy.deepcopy(malformed_request)
            )
        self.assertEqual(
            getattr(malformed.exception, "code", None),
            "ADAPTER_SUBMISSION_INVALID",
        )
        self.assertNotIn(str(malformed_path), str(malformed.exception))

        symlink_request = self.submit_kwargs(
            task_id="task-receipt-symlink",
            idempotency_key="task-receipt-symlink:invert:0001",
        )
        symlink_handle = self.adapter.submit(**copy.deepcopy(symlink_request))
        symlink_path = self.submission_record_path(symlink_handle)
        outside = self.base / "outside-submission.json"
        outside.write_text("{}", encoding="utf-8")
        symlink_path.unlink()
        symlink_path.symlink_to(outside)
        with self.assertRaises(RuntimeError) as symlinked:
            reopened.lookup_existing_handle(**copy.deepcopy(symlink_request))
        self.assertEqual(
            getattr(symlinked.exception, "code", None),
            "ADAPTER_SUBMISSION_INVALID",
        )
        self.assertNotIn(str(outside), str(symlinked.exception))
        self.assertEqual(replacement_launcher.calls, [])

    def test_submit_converts_sgd_milli_units_in_private_worker_config(self) -> None:
        request = self.submit_kwargs(
            task_id="task-sgd-config",
            idempotency_key="task-sgd-config:invert:0001",
        )
        request["parameters"].update(
            {"optimizer": "sgd", "learning_rate_milli": 100_000_000}
        )
        handle = self.adapter.submit(**request)
        self.assertEqual(handle.algorithm, algorithm_identity())
        config_path = self.launcher.calls[-1]["config_path"]
        config = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(
            config,
            {
                "job_id": config["job_id"],
                "model_id": "marmousi_94_288",
                "preset": "fwi_smoke",
                "device": "cpu",
                "iterations": 2,
                "seed": 2026,
                "optimizer": "sgd",
                "learning_rate": 100_000.0,
                "gradient_clip_quantile": 0.98,
            },
        )

    def test_fixed_task_dispatcher_maps_snapshot_to_adapter_and_receipt(self) -> None:
        dataset = copy.deepcopy(self.dataset)
        snapshot = TaskSnapshot(
            task_id="task-bridge-001",
            project_id="project-1",
            principal_id="user-1",
            status="AwaitingApproval",
            draft={"datasets": [dataset]},
            plan={
                "plan_id": "plan-bridge-001",
                "plan_hash": PLAN_HASH,
                "task_type": "acoustic_fwi_2d",
                "nodes": [
                    {
                        "node_id": "invert",
                        "algorithm": algorithm_identity(),
                        "inputs": [
                            {
                                "port": "model",
                                "dataset": {
                                    key: dataset[key]
                                    for key in (
                                        "id",
                                        "version",
                                        "content_hash",
                                        "data_type",
                                    )
                                },
                            }
                        ],
                        "parameters": parameters(),
                        "resources": resources(),
                        "idempotency_key": "task-bridge-001:invert:0001",
                    }
                ],
            },
            approval=None,
            created_at=NOW,
            updated_at=NOW,
        )
        bridge = DeepwaveTaskDispatcher(self.adapter)
        prepared = bridge.prepare(snapshot)
        self.assertEqual(prepared.adapter_id, "fwi.deepwave_adapter")
        self.assertEqual(prepared.request["task_id"], snapshot.task_id)
        self.assertEqual(
            prepared.request["normalized_config_hash"],
            prepared.queue_fingerprint["normalized_config_hash"],
        )
        intent = DispatchIntentSnapshot(
            intent_id="dispatch-bridge-001",
            task_id=snapshot.task_id,
            plan_id=snapshot.plan["plan_id"],
            plan_hash=snapshot.plan["plan_hash"],
            approval_id="approval-bridge-001",
            node_id="invert",
            node_idempotency_key="task-bridge-001:invert:0001",
            adapter_id=prepared.adapter_id,
            adapter_version=prepared.adapter_version,
            request=prepared.request,
            request_hash="sha256:" + "e" * 64,
            queue_fingerprint=prepared.queue_fingerprint,
            state="dispatching",
            handle=None,
            failure_code=None,
            created_at=NOW,
            dispatch_claimed_at=NOW,
            outcome_recorded_at=None,
        )
        handle = bridge.dispatch(intent)
        self.assertEqual(handle["task_id"], snapshot.task_id)
        self.assertEqual(
            handle["fingerprint"]["normalized_config_hash"],
            prepared.request["normalized_config_hash"],
        )
        self.assertEqual(handle["fingerprint"]["input_hashes"], [HASH_DATASET])
        self.assertEqual(len(self.launcher.calls), 1)
        self.assertEqual(bridge.dispatch(intent), handle)
        self.assertEqual(len(self.launcher.calls), 1)

        recovery_launcher = FakeLauncher()
        recovery_bridge = DeepwaveTaskDispatcher(
            self.make_adapter(launcher=recovery_launcher)
        )
        self.assertEqual(recovery_bridge.recover_existing_receipt(intent), handle)
        self.assertEqual(recovery_launcher.calls, [])
        observed = recovery_bridge.observe_existing_worker_attempt(intent)
        self.assertIsNone(observed["handle"])
        self.assertEqual(observed["evidence"]["ticket"]["state"], "staged")
        self.assertIsNone(observed["evidence"]["ready"])
        self.assertEqual(recovery_launcher.calls, [])

        drift_hash = "sha256:" + "f" * 64
        drifted_request = copy.deepcopy(intent.request)
        drifted_request["normalized_config_hash"] = drift_hash
        drifted_fingerprint = copy.deepcopy(intent.queue_fingerprint)
        drifted_fingerprint["normalized_config_hash"] = drift_hash
        drifted = dataclasses.replace(
            intent,
            request=drifted_request,
            queue_fingerprint=drifted_fingerprint,
        )
        with self.assertRaises(DispatchError) as drift:
            recovery_bridge.recover_existing_receipt(drifted)
        self.assertEqual(drift.exception.code, "DISPATCH_FINGERPRINT_DRIFT")
        self.assertEqual(recovery_launcher.calls, [])

        invalid = dataclasses.replace(intent, adapter_id="untrusted.dynamic")
        with self.assertRaises(DispatchError) as raised:
            bridge.dispatch(invalid)
        self.assertEqual(raised.exception.code, "DISPATCH_INTENT_INVALID")

        with patch.object(
            self.adapter,
            "submit",
            side_effect=AdapterUnavailable("ADAPTER_CONCURRENCY_LIMIT"),
        ), self.assertRaises(DispatchDeferred) as deferred:
            bridge.dispatch(intent)
        self.assertEqual(deferred.exception.code, "ADAPTER_CONCURRENCY_LIMIT")
        with patch.object(
            self.adapter,
            "submit",
            side_effect=AdapterUnavailable("SUBMISSION_LAUNCH_PENDING"),
        ), self.assertRaises(DispatchDeferred) as deferred:
            bridge.dispatch(intent)
        self.assertEqual(deferred.exception.code, "SUBMISSION_LAUNCH_PENDING")
        with patch.object(
            self.adapter,
            "submit",
            side_effect=AdapterUnavailable(
                "SUBMISSION_RECONCILIATION_REQUIRED"
            ),
        ), self.assertRaises(DispatchError) as legacy:
            bridge.dispatch(intent)
        self.assertNotIsInstance(legacy.exception, DispatchDeferred)
        self.assertEqual(
            legacy.exception.code, "SUBMISSION_RECONCILIATION_REQUIRED"
        )

    def test_task_service_startup_adopts_real_adapter_lost_receipt(self) -> None:
        database_path = self.base / "receipt-recovery.sqlite3"
        store = SQLiteTaskStore(database_path)
        registry = RegistryService(store, clock=lambda: NOW)
        registry.register_dataset(dataset=copy.deepcopy(self.dataset))
        registry.register_algorithm(
            manifest=fwi_adapter_module.load_deepwave_manifest()
        )

        def registered_adapter(
            current_registry: RegistryService, launcher: FakeLauncher
        ) -> DeepwaveAdapter:
            def registry_snapshot_provider(
                *,
                project_id: str,
                principal_id: str,
                dataset_id: str,
                dataset_version: str,
            ) -> dict[str, Any]:
                return current_registry.get_dataset(
                    project_id=project_id,
                    principal_id=principal_id,
                    dataset_id=dataset_id,
                    version=dataset_version,
                    permission="execute",
                )

            return DeepwaveAdapter(
                run_root=self.run_root,
                launcher=launcher,
                dataset_identity_provider=self.dataset_provider,
                registry_snapshot_provider=registry_snapshot_provider,
                device_validator=self.device_validator,
                fingerprint_factory=self.fingerprint_factory,
                clock=lambda: NOW,
            )

        initial_adapter = registered_adapter(registry, self.launcher)
        service = TaskService(
            store,
            task_id_factory=lambda: "task-real-receipt-recovery",
            clock=lambda: NOW,
            dispatcher=DeepwaveTaskDispatcher(initial_adapter),
        )
        draft = contract_optimizer_task_draft()
        draft["draft_id"] = "draft-real-receipt-recovery"
        draft["datasets"] = [copy.deepcopy(self.dataset)]
        draft["algorithm"] = algorithm_identity()
        draft["parameters"] = parameters()
        draft["resources"] = resources()
        task_id = service.create_task(
            project_id="project-1",
            principal_id="user-1",
            draft=draft,
            idempotency_key="create-real-receipt-recovery",
        ).snapshot.task_id

        plan = contract_optimizer_plan_graph()
        plan["plan_id"] = "plan-real-receipt-recovery"
        plan["draft"] = {"draft_id": draft["draft_id"], "revision": 1}
        node = plan["nodes"][0]
        node["algorithm"] = algorithm_identity()
        node["inputs"][0]["dataset"] = {
            key: self.dataset[key]
            for key in ("id", "version", "content_hash", "data_type")
        }
        node["parameters"] = parameters()
        node["resources"] = resources()
        node["idempotency_key"] = (
            "task-real-receipt-recovery:invert:0001"
        )
        plan["plan_hash"] = compute_plan_hash(plan)
        service.persist_plan(
            task_id=task_id,
            project_id="project-1",
            principal_id="user-1",
            plan=plan,
        )

        approval = contract_approval_decision(plan)
        approval["approval_id"] = "approval-real-receipt-recovery"
        approval["scope"]["datasets"] = [
            {
                key: self.dataset[key]
                for key in ("id", "version", "content_hash", "data_type")
            }
        ]
        approval["scope"]["algorithms"] = [algorithm_identity()]
        approval["scope"]["resource_limits"] = resources()
        approval["decided_at"] = "2026-07-15T05:59:00Z"
        approval["expires_at"] = "2026-07-15T07:00:00Z"
        service.persist_approval(
            task_id=task_id,
            project_id="project-1",
            principal_id="user-1",
            approval=approval,
        )

        submitted = service.submit_task(
            task_id=task_id,
            project_id="project-1",
            principal_id="user-1",
            approval_id=approval["approval_id"],
            idempotency_key="submit-real-receipt-recovery",
        )
        self.assertEqual(submitted.intent.state, "pending")
        acquisition = service.acquire_runtime_supervisor_lease(
            project_id="project-1",
            principal_id="user-1",
            owner_id="lost-receipt-owner",
            lease_seconds=30,
        )
        original_record_success = store.record_supervised_worker_observation

        def lose_sqlite_receipt(**_kwargs: Any) -> Any:
            raise TaskStoreConflict("simulated post-launch receipt loss")

        store.record_supervised_worker_observation = lose_sqlite_receipt
        try:
            with self.assertRaises(TaskConflict):
                service.schedule_runtime_dispatch(
                    task_id,
                    project_id="project-1",
                    principal_id="user-1",
                    supervisor_lease=acquisition.lease,
                )
        finally:
            store.record_supervised_worker_observation = original_record_success
        service.release_runtime_supervisor_lease(acquisition.lease)

        self.assertEqual(len(self.launcher.calls), 1)
        lost_intent = store.get_dispatch_intent(task_id)
        self.assertIsNotNone(lost_intent)
        self.assertEqual(lost_intent.state, "dispatching")
        self.assertIsNone(lost_intent.handle)

        run_dir = self.launcher.calls[0]["run_dir"]
        worker_config = json.loads(
            (run_dir / "config.original.json").read_text(encoding="utf-8")
        )
        (run_dir / "status.json").write_text(
            json.dumps(
                {
                    "job_id": worker_config["job_id"],
                    "status": "running",
                    "stage": "invert",
                    "iteration": 1,
                    "total_iterations": 2,
                    "message": "synthetic recovery progress",
                    "updated_at": NOW,
                }
            ),
            encoding="utf-8",
        )

        # Recreate the narrower controller-crash window: the child crossed its
        # inherited fence/ready barrier, but the Adapter record still says
        # ``launching`` and SQLite has no receipt.
        record_path = next(
            (self.run_root / fwi_adapter_module.CONTROL_DIRECTORY / "submissions").glob(
                "*.json"
            )
        )
        private_record = json.loads(record_path.read_text(encoding="utf-8"))
        private_record["launch_state"] = "launching"
        initial_adapter._write_submission(record_path, private_record)
        launch_binding = binding_from_submission_record(private_record)
        launch_lease = ParentLaunchLease.acquire(
            self.run_root, run_dir, max_active=2
        )
        launch_lease.mark_spawned(os.getpid())
        recovery_heartbeat = WorkerHeartbeat(
            run_root=self.run_root,
            run_dir=run_dir,
            attempt_id=launch_binding.attempt_id,
            attempt_fd=os.dup(launch_lease.attempt_fd),
            capacity_fd=os.dup(launch_lease.capacity_fd),
            interval_seconds=0.02,
        )
        launch_lease.close_parent()
        recovery_heartbeat.start()

        reopened_store = SQLiteTaskStore(database_path)
        reopened_registry = RegistryService(reopened_store, clock=lambda: NOW)
        replacement_launcher = FakeLauncher()
        reopened_service = TaskService(
            reopened_store,
            clock=lambda: NOW,
            dispatcher=DeepwaveTaskDispatcher(
                registered_adapter(reopened_registry, replacement_launcher)
            ),
        )
        try:
            recovered = reopened_service.recover_runtime_on_startup(
                "project-1", "user-1"
            )
            resumed_lease = reopened_service.acquire_runtime_supervisor_lease(
                project_id="project-1",
                principal_id="user-1",
                owner_id="reopened-receipt-owner",
                lease_seconds=30,
            ).lease
            scheduled = reopened_service.schedule_runtime_dispatch(
                task_id,
                project_id="project-1",
                principal_id="user-1",
                supervisor_lease=resumed_lease,
            )
            reopened_service.refresh_runtime_status(
                task_id,
                project_id="project-1",
                principal_id="user-1",
                supervisor_lease=resumed_lease,
            )
            reopened_service.release_runtime_supervisor_lease(resumed_lease)
        finally:
            recovery_heartbeat.stop("succeeded")

        self.assertEqual(recovered.receipt_recovery_attempted_task_ids, ())
        self.assertEqual(recovered.receipt_recovered_task_ids, ())
        self.assertEqual(recovered.status_refreshed_task_ids, ())
        self.assertEqual(
            recovered.dispatching_deferred,
            ((task_id, "SUPERVISED_DISPATCH_REQUIRED"),),
        )
        self.assertEqual(recovered.status_refresh_failures, ())
        self.assertEqual(scheduled.intent.state, "dispatched")
        self.assertTrue(scheduled.adopted)
        self.assertEqual(replacement_launcher.calls, [])
        adopted = reopened_store.get_dispatch_intent(task_id)
        self.assertIsNotNone(adopted)
        self.assertEqual(adopted.state, "dispatched")
        self.assertIsNotNone(adopted.handle)
        self.assertEqual(reopened_store.get_task(task_id).status, "Running")
        self.assertEqual(
            [
                event["event_type"]
                for event in reopened_service.list_run_events(
                    task_id,
                    project_id="project-1",
                    principal_id="user-1",
                )
            ],
            ["task_queued", "node_started", "node_progress"],
        )

    def test_submit_is_idempotent_sequentially_and_across_instances(self) -> None:
        request = self.submit_kwargs()
        first = self.adapter.submit(**copy.deepcopy(request))
        reordered = copy.deepcopy(request)
        reordered["parameters"] = dict(
            reversed(list(reordered["parameters"].items()))
        )
        reordered["resources"] = dict(
            reversed(list(reordered["resources"].items()))
        )
        second = self.adapter.submit(**reordered)
        self.assertEqual(_plain(first), _plain(second))
        self.assertEqual(len(self.launcher.calls), 1)

        replay_launcher = FakeLauncher()
        reopened = self.make_adapter(launcher=replay_launcher)
        third = reopened.submit(**copy.deepcopy(request))
        self.assertEqual(_plain(first), _plain(third))
        self.assertEqual(replay_launcher.calls, [])

        changed = copy.deepcopy(request)
        changed["parameters"]["iterations"] = 3
        with self.assertRaises((ValueError, RuntimeError)):
            reopened.submit(**changed)

        changed_identity = copy.deepcopy(request)
        changed_identity["node_id"] = "different-node"
        with self.assertRaises((ValueError, RuntimeError)):
            reopened.submit(**changed_identity)

        # Node keys are scoped to one immutable plan. A new plan hash and a new
        # task therefore receive independent scopes even when the same
        # syntactically valid key is reused.
        new_plan = copy.deepcopy(request)
        new_plan["plan_hash"] = "sha256:" + "e" * 64
        reopened.submit(**new_plan)
        other_task = copy.deepcopy(request)
        other_task["task_id"] = "task-002"
        reopened.submit(**other_task)
        self.assertEqual(len(replay_launcher.calls), 2)
        self.assertEqual(len(self.launcher.calls), 1)

    def test_submit_is_idempotent_under_concurrent_first_use(self) -> None:
        concurrent_root = self.base / "concurrent-runs"
        concurrent_root.mkdir(mode=0o700)
        launcher = FakeLauncher(delay_seconds=0.05)
        adapter = self.make_adapter(run_root=concurrent_root, launcher=launcher)
        request = self.submit_kwargs(
            task_id="task-concurrent",
            idempotency_key="task-concurrent:invert:0001",
        )
        barrier = threading.Barrier(8)

        def submit(_: int) -> dict[str, Any]:
            barrier.wait(timeout=5)
            return _plain(adapter.submit(**copy.deepcopy(request)))

        with ThreadPoolExecutor(max_workers=8) as executor:
            handles = list(executor.map(submit, range(8)))
        self.assertEqual(len(launcher.calls), 1)
        self.assertEqual(handles, [handles[0]] * 8)

    def test_submit_is_idempotent_across_processes(self) -> None:
        process_root = self.base / "process-runs"
        marker = self.base / "process-launches.log"
        context = multiprocessing.get_context("fork")
        start = context.Event()
        results = context.Queue()
        processes = [
            context.Process(
                target=_cross_process_submit,
                args=(str(process_root), str(marker), start, results),
            )
            for _ in range(4)
        ]
        for process in processes:
            process.start()
        start.set()
        for process in processes:
            process.join(timeout=15)
            self.assertFalse(process.is_alive())
            self.assertEqual(process.exitcode, 0)
        values = [results.get(timeout=2) for _ in processes]
        self.assertEqual([kind for kind, _ in values], ["ok"] * len(processes))
        handles = [value for _, value in values]
        self.assertEqual(handles, [handles[0]] * len(handles))
        self.assertEqual(marker.read_text(encoding="utf-8"), "launch\n")

    def test_replay_skips_live_readiness_and_failed_launch_is_sticky(self) -> None:
        request = self.submit_kwargs(
            task_id="task-readiness",
            idempotency_key="task-readiness:invert:0001",
        )
        first = self.adapter.submit(**copy.deepcopy(request))

        def unavailable(*args: Any, **kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("synthetic live dependency outage")

        replay_launcher = FakeLauncher()
        replay = DeepwaveAdapter(
            run_root=self.run_root,
            launcher=replay_launcher,
            dataset_identity_provider=unavailable,
            registry_snapshot_provider=unavailable,
            device_validator=unavailable,
            fingerprint_factory=unavailable,
            clock=lambda: NOW,
        )
        self.assertEqual(
            _plain(replay.submit(**copy.deepcopy(request))), _plain(first)
        )
        self.assertEqual(replay_launcher.calls, [])

        failed_root = self.base / "failed-runs"
        failed_root.mkdir(mode=0o700)
        failing_launcher = FailingLauncher()
        failing = self.make_adapter(
            run_root=failed_root, launcher=failing_launcher
        )
        failed_request = self.submit_kwargs(
            task_id="task-failed-launch",
            idempotency_key="task-failed-launch:invert:0001",
        )
        with self.assertRaisesRegex(RuntimeError, "WORKER_LAUNCH_FAILED"):
            failing.submit(**copy.deepcopy(failed_request))
        replacement_launcher = FakeLauncher()
        reopened = self.make_adapter(
            run_root=failed_root, launcher=replacement_launcher
        )
        with self.assertRaisesRegex(RuntimeError, "WORKER_LAUNCH_FAILED"):
            reopened.submit(**copy.deepcopy(failed_request))
        self.assertEqual(len(failing_launcher.calls), 1)
        self.assertEqual(replacement_launcher.calls, [])

    def test_exact_stopped_failure_appends_one_deterministic_retry(self) -> None:
        retry_root = self.base / "retry-runs"
        retry_root.mkdir(mode=0o700)
        launcher = StoppedThenSuccessfulSafeLauncher()
        adapter = self.make_adapter(run_root=retry_root, launcher=launcher)
        request = self.submit_kwargs(
            task_id="task-exact-stopped-retry",
            idempotency_key="task-exact-stopped-retry:invert:0001",
        )

        with self.assertRaises(AdapterUnavailable) as stopped:
            adapter.submit(**copy.deepcopy(request))
        self.assertEqual(stopped.exception.code, "WORKER_LAUNCH_FAILED")
        proof = adapter.probe_pre_running_retry(**copy.deepcopy(request))
        self.assertEqual(proof.previous_attempt_number, 1)
        self.assertEqual(proof.private_schema_version, "1.2.0")
        self.assertEqual(proof.evidence["ticket"]["state"], "failed")

        authorization = {
            "schema_version": "1.0.0",
            "intent_id": "intent-exact-stopped-retry",
            "previous_attempt_id": proof.previous_attempt_id,
            "previous_observation_sequence": 1,
            "failure_kind": "pre_running_launch_failure",
            "private_proof_hash": proof.private_proof_hash,
            "next_attempt_number": 2,
            "authorized_at": "2026-07-15T06:00:01Z",
        }
        handle = adapter.retry_pre_running(
            **copy.deepcopy(request), authorization=authorization
        )
        self.assertEqual(launcher.calls, 2)
        record_path = (
            retry_root
            / ".scientific-runtime-adapter-v1"
            / "submissions"
            / f"{handle.submission_id.removeprefix('submission-')}.json"
        )
        record = adapter._read_submission(record_path)
        current = binding_from_submission_record(record)
        self.assertEqual(record["schema_version"], "1.2.0")
        self.assertEqual(current.attempt_number, 2)
        self.assertEqual(len(record["attempt_history"]), 1)
        self.assertEqual(
            record["attempt_history"][0]["launch_attempt"]["attempt_id"],
            proof.previous_attempt_id,
        )
        self.assertEqual(
            record["attempt_history"][0]["launch_failure"]["proof_hash"],
            proof.private_proof_hash,
        )

        replay = adapter.retry_pre_running(
            **copy.deepcopy(request), authorization=copy.deepcopy(authorization)
        )
        self.assertEqual(replay.as_dict(), handle.as_dict())
        self.assertEqual(launcher.calls, 2)
        with self.assertRaises(AdapterUnavailable) as exhausted:
            adapter.probe_pre_running_retry(**copy.deepcopy(request))
        self.assertEqual(exhausted.exception.code, "WORKER_RETRY_UNSUPPORTED")

    def test_attempt_two_exhaustion_probe_is_exact_and_read_only(self) -> None:
        launcher = StoppedTwiceSafeLauncher()
        adapter = self.make_adapter(launcher=launcher)
        request = self.submit_kwargs(
            task_id="task-exact-stopped-exhaustion",
            idempotency_key="task-exact-stopped-exhaustion:invert:0001",
        )

        with self.assertRaises(AdapterUnavailable) as first_stopped:
            adapter.submit(**copy.deepcopy(request))
        self.assertEqual(first_stopped.exception.code, "WORKER_LAUNCH_FAILED")
        first = adapter.probe_pre_running_retry(**copy.deepcopy(request))
        authorization = {
            "schema_version": "1.0.0",
            "intent_id": "intent-exact-stopped-exhaustion",
            "previous_attempt_id": first.previous_attempt_id,
            "previous_observation_sequence": 1,
            "failure_kind": "pre_running_launch_failure",
            "private_proof_hash": first.private_proof_hash,
            "next_attempt_number": 2,
            "authorized_at": "2026-07-15T06:00:01Z",
        }
        with self.assertRaises(AdapterUnavailable) as second_stopped:
            adapter.retry_pre_running(
                **copy.deepcopy(request), authorization=authorization
            )
        self.assertEqual(second_stopped.exception.code, "WORKER_LAUNCH_FAILED")
        self.assertEqual(launcher.calls, 2)

        record_path = next(
            (
                self.run_root
                / fwi_adapter_module.CONTROL_DIRECTORY
                / "submissions"
            ).glob("*.json")
        )
        original_record = adapter._read_submission(record_path)
        second_binding = binding_from_submission_record(original_record)
        self.assertEqual(original_record["schema_version"], "1.2.0")
        self.assertEqual(original_record["launch_state"], "failed")
        self.assertEqual(second_binding.attempt_number, 2)
        self.assertEqual(len(original_record["attempt_history"]), 1)

        def private_snapshot() -> dict[str, tuple[str, int, bytes | str | None]]:
            snapshot: dict[str, tuple[str, int, bytes | str | None]] = {}
            for path in sorted(self.run_root.rglob("*")):
                relative = str(path.relative_to(self.run_root))
                mode = path.lstat().st_mode & 0o777
                if path.is_symlink():
                    snapshot[relative] = ("symlink", mode, os.readlink(path))
                elif path.is_file():
                    snapshot[relative] = ("file", mode, path.read_bytes())
                else:
                    snapshot[relative] = ("directory", mode, None)
            return snapshot

        before_exact_probe = private_snapshot()
        exhausted = adapter.probe_pre_running_retry_exhaustion(
            **copy.deepcopy(request)
        )
        self.assertEqual(private_snapshot(), before_exact_probe)
        self.assertEqual(exhausted.previous_attempt_number, 2)
        self.assertEqual(exhausted.previous_attempt_id, second_binding.attempt_id)
        self.assertEqual(exhausted.private_schema_version, "1.2.0")
        self.assertEqual(
            exhausted.private_proof_hash,
            original_record["launch_failure"]["proof_hash"],
        )
        self.assertEqual(exhausted.evidence["ticket"]["state"], "failed")

        before_wrong_attempt = private_snapshot()
        with self.assertRaises(AdapterUnavailable) as wrong_attempt:
            adapter.probe_pre_running_retry(**copy.deepcopy(request))
        self.assertEqual(wrong_attempt.exception.code, "WORKER_RETRY_UNSUPPORTED")
        self.assertEqual(private_snapshot(), before_wrong_attempt)

        mismatched_record = copy.deepcopy(original_record)
        mismatch = mismatched_record["launch_failure"]
        mismatch["ticket_record_hash"] = "sha256:" + "e" * 64
        mismatch["proof_hash"] = fwi_adapter_module._sha256_document(
            {
                key: copy.deepcopy(value)
                for key, value in mismatch.items()
                if key != "proof_hash"
            }
        )
        adapter._write_submission(record_path, mismatched_record)
        before_mismatch_probe = private_snapshot()
        try:
            with self.assertRaisesRegex(
                RuntimeError, "stopped proof differs from Worker evidence"
            ):
                adapter.probe_pre_running_retry_exhaustion(
                    **copy.deepcopy(request)
                )
            self.assertEqual(private_snapshot(), before_mismatch_probe)
        finally:
            adapter._write_submission(record_path, original_record)
        self.assertEqual(private_snapshot(), before_exact_probe)

    def test_pre_running_retry_attempt_two_worker_exit_has_exact_proof(
        self,
    ) -> None:
        launcher = StoppedThenSuccessfulSafeLauncher()
        adapter = self.make_adapter(launcher=launcher)
        request = self.submit_kwargs(
            task_id="task-b1-attempt-two-worker-exit",
            idempotency_key="task-b1-attempt-two-worker-exit:invert:0001",
        )
        with self.assertRaises(AdapterUnavailable):
            adapter.submit(**copy.deepcopy(request))
        first = adapter.probe_pre_running_retry(**copy.deepcopy(request))
        authorization = {
            "schema_version": "1.0.0",
            "intent_id": "intent-b1-attempt-two-worker-exit",
            "previous_attempt_id": first.previous_attempt_id,
            "previous_observation_sequence": 1,
            "failure_kind": "pre_running_launch_failure",
            "private_proof_hash": first.private_proof_hash,
            "next_attempt_number": 2,
            "authorized_at": "2026-07-15T06:00:01Z",
        }
        handle = adapter.retry_pre_running(
            **copy.deepcopy(request), authorization=authorization
        )
        run_dir = self.run_root / handle.job_id
        binding, heartbeat = self.start_exact_worker(handle, run_dir)
        heartbeat._stop.set()
        assert heartbeat._thread is not None
        heartbeat._thread.join(2.0)
        self.assertFalse(heartbeat._thread.is_alive())
        heartbeat._close_descriptors()
        pre_status = json.loads(
            (run_dir / "status.json").read_text(encoding="utf-8")
        )
        post_status = {
            **pre_status,
            "status": "failed",
            "stage": "worker_exit",
            "message": "FWI worker exited with code -9",
            "updated_at": "2026-07-15T06:00:02Z",
        }
        exit_evidence = record_worker_exit(
            self.run_root,
            run_dir,
            binding,
            return_code=-9,
            pre_status=pre_status,
            post_status=post_status,
        )

        exhausted = adapter.probe_worker_exit_retry_exhaustion(
            **copy.deepcopy(request)
        )
        self.assertEqual(exhausted.previous_attempt_number, 2)
        self.assertEqual(exhausted.private_schema_version, "1.2.0")
        self.assertEqual(exhausted.private_proof_hash, exit_evidence.record_hash)
        self.assertEqual(exhausted.exit_evidence, exit_evidence.as_dict())

    def test_retry_exhaustion_purge_deletes_both_attempts_and_replays(self) -> None:
        adapter, request, token, record_path = (
            self.stopped_retry_exhaustion_fixture(
                task_id="task-retry-exhaustion-purge"
            )
        )
        record = adapter._read_submission(record_path)
        job_dirs = [
            self.run_root / record["attempt_history"][0]["job_id"],
            self.run_root / record["job_id"],
        ]
        unrelated = self.run_root / "unrelated-retained"
        unrelated.mkdir()
        (unrelated / "sentinel.txt").write_text("keep", encoding="utf-8")

        first = adapter.purge_retry_exhausted(
            **copy.deepcopy(request),
            purge_id=token["purge_id"],
            exhaustion=copy.deepcopy(token),
        ).as_dict()
        self.assertEqual(first["local_run_state"], "deleted")
        self.assertFalse(first["replayed"])
        self.assertTrue(all(not path.exists() for path in job_dirs))
        self.assertEqual(
            (unrelated / "sentinel.txt").read_text(encoding="utf-8"), "keep"
        )
        purged_record = adapter._read_submission(record_path)
        self.assertEqual(purged_record["launch_state"], "purged")
        self.assertEqual(purged_record["purge_id"], token["purge_id"])
        self.assertEqual(
            purged_record["launch_failure"]["proof_hash"],
            token["private_proof_hash"],
        )

        replay = adapter.purge_retry_exhausted(
            **copy.deepcopy(request),
            purge_id=token["purge_id"],
            exhaustion=copy.deepcopy(token),
        ).as_dict()
        self.assertTrue(replay["replayed"])
        conflicting = self.rebound_cleanup_token(
            token, purge_id="purge-retry-exhaustion-other"
        )
        with self.assertRaisesRegex(RuntimeError, "PURGE_IDEMPOTENCY_CONFLICT"):
            adapter.purge_retry_exhausted(
                **copy.deepcopy(request),
                purge_id=conflicting["purge_id"],
                exhaustion=conflicting,
            )

    def test_worker_exit_then_pre_running_exhaustion_purges_exact_chain(
        self,
    ) -> None:
        task_id = "task-worker-exit-mixed-exhaustion-purge"
        request = self.submit_kwargs(
            task_id=task_id,
            idempotency_key=f"{task_id}:invert:0001",
        )
        first_adapter = self.make_adapter()
        first_handle = first_adapter.submit(**copy.deepcopy(request))
        first_dir = self.launcher.calls[-1]["run_dir"]
        first_binding, heartbeat = self.start_exact_worker(
            first_handle, first_dir
        )
        heartbeat._stop.set()
        assert heartbeat._thread is not None
        heartbeat._thread.join(2.0)
        self.assertFalse(heartbeat._thread.is_alive())
        self.assertIsNone(heartbeat._failure)
        heartbeat._close_descriptors()

        pre_status = json.loads(
            (first_dir / "status.json").read_text(encoding="utf-8")
        )
        post_status = {
            **pre_status,
            "status": "failed",
            "stage": "worker_exit",
            "message": "FWI worker exited with code -9",
            "updated_at": "2026-07-15T06:00:01Z",
        }
        exit_evidence = record_worker_exit(
            self.run_root,
            first_dir,
            first_binding,
            return_code=-9,
            pre_status=pre_status,
            post_status=post_status,
        )

        stopped_launcher = StoppedTwiceSafeLauncher()
        adapter = self.make_adapter(launcher=stopped_launcher)
        first = adapter.probe_worker_exit_retry(**copy.deepcopy(request))
        self.assertEqual(first.private_proof_hash, exit_evidence.record_hash)
        authorization = {
            "schema_version": "1.0.0",
            "intent_id": f"intent-{task_id}",
            "previous_attempt_id": first.previous_attempt_id,
            "previous_observation_sequence": 1,
            "failure_kind": "worker_exit",
            "private_proof_hash": first.private_proof_hash,
            "next_attempt_number": 2,
            "authorized_at": "2026-07-15T06:00:02Z",
        }
        with self.assertRaises(AdapterUnavailable) as second_stopped:
            adapter.retry_worker_exit(
                **copy.deepcopy(request), authorization=authorization
            )
        self.assertEqual(second_stopped.exception.code, "WORKER_LAUNCH_FAILED")
        second = adapter.probe_pre_running_retry_exhaustion(
            **copy.deepcopy(request)
        )
        self.assertEqual(second.private_schema_version, "1.3.0")

        payload = {
            "schema_version": "1.1.0",
            "purge_id": f"purge-{task_id}",
            "intent_id": authorization["intent_id"],
            "task_id": task_id,
            "project_id": "project-1",
            "principal_id": "user-1",
            "approval_id": f"approval-{task_id}",
            "attempt_id": second.previous_attempt_id,
            "attempt_number": 2,
            "observation_sequence": 2,
            "evidence": copy.deepcopy(second.evidence),
            "evidence_hash": fwi_adapter_module._sha256_document(
                second.evidence
            ),
            "private_schema_version": "1.3.0",
            "private_proof_hash": second.private_proof_hash,
            "failure_kind": "pre_running_launch_failure",
            "previous_attempt_id": first.previous_attempt_id,
            "previous_observation_sequence": 1,
            "previous_private_proof_hash": first.private_proof_hash,
            "previous_failure_kind": "worker_exit",
            "previous_private_schema_version": first.private_schema_version,
            "retry_reserved_at": authorization["authorized_at"],
            "terminal_event_sequence": 2,
            "terminal_event_hash": "sha256:" + "f" * 64,
            "exhausted_at": "2026-07-15T06:00:03Z",
        }
        token = {
            **payload,
            "proof_hash": fwi_adapter_module._sha256_document(payload),
        }
        record_path = self.submission_record_path(first_handle)
        record = adapter._read_submission(record_path)
        job_dirs = [
            self.run_root / record["attempt_history"][0]["job_id"],
            self.run_root / record["job_id"],
        ]

        result = adapter.purge_retry_exhausted(
            **copy.deepcopy(request),
            purge_id=token["purge_id"],
            exhaustion=copy.deepcopy(token),
        ).as_dict()
        self.assertEqual(result["local_run_state"], "deleted")
        self.assertFalse(result["replayed"])
        self.assertTrue(all(not path.exists() for path in job_dirs))
        self.assertEqual(
            adapter._read_submission(record_path)["launch_state"], "purged"
        )

    def test_dispatcher_consumes_only_store_typed_retry_exhaustion_proof(
        self,
    ) -> None:
        adapter, request, token, record_path = (
            self.stopped_retry_exhaustion_fixture(
                task_id="task-retry-exhaustion-dispatcher-purge"
            )
        )
        record = adapter._read_submission(record_path)
        durable_request = copy.deepcopy(request)
        durable_request["normalized_config_hash"] = record[
            "normalized_config_hash"
        ]
        intent = DispatchIntentSnapshot(
            intent_id=token["intent_id"],
            task_id=token["task_id"],
            plan_id="plan-retry-exhaustion-dispatcher-purge",
            plan_hash=request["plan_hash"],
            approval_id=token["approval_id"],
            node_id=request["node_id"],
            node_idempotency_key=request["idempotency_key"],
            adapter_id="fwi.deepwave_adapter",
            adapter_version="1.5.0",
            request=durable_request,
            request_hash="sha256:" + "e" * 64,
            queue_fingerprint=copy.deepcopy(record["fingerprint"]),
            state="retry_exhausted",
            handle=None,
            failure_code="WORKER_RETRY_EXHAUSTED",
            created_at=NOW,
            dispatch_claimed_at=NOW,
            outcome_recorded_at=token["exhausted_at"],
        )
        proof = RetryExhaustionCleanupProof(
            **{
                key: copy.deepcopy(token[key])
                for key in (
                    "purge_id",
                    "intent_id",
                    "task_id",
                    "project_id",
                    "principal_id",
                    "approval_id",
                    "attempt_id",
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
                )
            }
        )
        dispatcher = DeepwaveTaskDispatcher(adapter)
        result = dispatcher.purge_retry_exhausted(
            intent, purge_id=token["purge_id"], exhaustion=proof
        )
        self.assertEqual(result["local_run_state"], "deleted")
        self.assertFalse(result["replayed"])
        self.assertFalse((self.run_root / record["job_id"]).exists())
        self.assertFalse(
            (self.run_root / record["attempt_history"][0]["job_id"]).exists()
        )
        with self.assertRaises(DispatchError) as untyped:
            dispatcher.purge_retry_exhausted(
                intent,
                purge_id=token["purge_id"],
                exhaustion=token,  # type: ignore[arg-type]
            )
        self.assertEqual(
            untyped.exception.code, "WORKER_RETRY_EXHAUSTION_PURGE_INVALID"
        )

    def test_retry_exhaustion_purge_mismatches_fail_before_tombstone(self) -> None:
        cases = (
            "proof",
            "attempt",
            "request",
            "history",
            "sidecar",
        )
        for case in cases:
            with self.subTest(case=case):
                task_id = f"task-exhaustion-mismatch-{case}"
                adapter, request, token, record_path = (
                    self.stopped_retry_exhaustion_fixture(task_id=task_id)
                )
                original = adapter._read_submission(record_path)
                current_dir = self.run_root / original["job_id"]
                prior_dir = (
                    self.run_root / original["attempt_history"][0]["job_id"]
                )
                supplied_request = copy.deepcopy(request)
                supplied_token = copy.deepcopy(token)
                if case == "proof":
                    supplied_token = self.rebound_cleanup_token(
                        token, private_proof_hash="sha256:" + "0" * 64
                    )
                elif case == "attempt":
                    supplied_token = self.rebound_cleanup_token(
                        token, attempt_id="attempt-" + "0" * 32
                    )
                elif case == "request":
                    supplied_request["node_id"] = "other"
                elif case == "history":
                    changed = copy.deepcopy(original)
                    changed["retry_authorization"][
                        "previous_observation_sequence"
                    ] = 2
                    adapter._write_submission(record_path, changed)
                else:
                    (current_dir / ".worker-ready.json").write_text(
                        "{}", encoding="utf-8"
                    )
                    os.chmod(current_dir / ".worker-ready.json", 0o600)

                with self.assertRaises(RuntimeError):
                    adapter.purge_retry_exhausted(
                        **supplied_request,
                        purge_id=supplied_token["purge_id"],
                        exhaustion=supplied_token,
                    )
                persisted = adapter._read_submission(record_path)
                self.assertEqual(persisted["launch_state"], "failed")
                self.assertNotIn("purge_id", persisted)
                self.assertTrue(current_dir.is_dir())
                self.assertTrue(prior_dir.is_dir())

    def test_retry_exhaustion_purge_busy_fence_mutates_nothing(self) -> None:
        adapter, request, token, record_path = (
            self.stopped_retry_exhaustion_fixture(
                task_id="task-retry-exhaustion-busy"
            )
        )
        record = adapter._read_submission(record_path)
        attempt_lock = (
            self.run_root
            / fwi_adapter_module.CONTROL_DIRECTORY
            / "worker-capacity"
            / "attempts"
            / f"{record['submission_id']}.lock"
        )
        descriptor = os.open(attempt_lock, os.O_RDWR)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        try:
            with self.assertRaisesRegex(RuntimeError, "PURGE_WORKER_STILL_ACTIVE"):
                adapter.purge_retry_exhausted(
                    **copy.deepcopy(request),
                    purge_id=token["purge_id"],
                    exhaustion=copy.deepcopy(token),
                )
            unchanged = adapter._read_submission(record_path)
            self.assertEqual(unchanged["launch_state"], "failed")
            self.assertNotIn("purge_id", unchanged)
            self.assertTrue((self.run_root / unchanged["job_id"]).is_dir())
            self.assertTrue(
                (
                    self.run_root
                    / unchanged["attempt_history"][0]["job_id"]
                ).is_dir()
            )
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def test_retry_exhaustion_purge_resumes_after_partial_delete(self) -> None:
        adapter, request, token, record_path = (
            self.stopped_retry_exhaustion_fixture(
                task_id="task-retry-exhaustion-partial"
            )
        )
        original = adapter._read_submission(record_path)
        job_dirs = [
            self.run_root / original["attempt_history"][0]["job_id"],
            self.run_root / original["job_id"],
        ]
        real_purge = adapter._purge_job_directory
        calls = 0

        def fail_second(record: dict[str, Any]) -> bool:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise AdapterPurgeError(
                    "PURGE_LOCAL_RUN_UNAVAILABLE: synthetic partial delete"
                )
            return real_purge(record)

        with patch.object(
            adapter, "_purge_job_directory", side_effect=fail_second
        ):
            with self.assertRaisesRegex(RuntimeError, "synthetic partial delete"):
                adapter.purge_retry_exhausted(
                    **copy.deepcopy(request),
                    purge_id=token["purge_id"],
                    exhaustion=copy.deepcopy(token),
                )
        partial = adapter._read_submission(record_path)
        self.assertEqual(partial["launch_state"], "purging")
        self.assertFalse(job_dirs[0].exists())
        self.assertTrue(job_dirs[1].is_dir())

        replay = adapter.purge_retry_exhausted(
            **copy.deepcopy(request),
            purge_id=token["purge_id"],
            exhaustion=copy.deepcopy(token),
        ).as_dict()
        self.assertTrue(replay["replayed"])
        self.assertTrue(all(not path.exists() for path in job_dirs))
        self.assertEqual(
            adapter._read_submission(record_path)["launch_state"], "purged"
        )

    def test_retry_exhaustion_purge_resumes_mid_directory_partial_unlink(
        self,
    ) -> None:
        adapter, request, token, record_path = (
            self.stopped_retry_exhaustion_fixture(
                task_id="task-retry-exhaustion-partial-directory"
            )
        )
        original = adapter._read_submission(record_path)
        prior_dir = self.run_root / original["attempt_history"][0]["job_id"]
        current_dir = self.run_root / original["job_id"]

        def unlink_then_crash(record: dict[str, Any]) -> bool:
            target = self.run_root / record["job_id"] / ".worker-launch.json"
            target.unlink()
            raise AdapterPurgeError(
                "PURGE_LOCAL_RUN_UNAVAILABLE: synthetic mid-directory crash"
            )

        with patch.object(
            adapter, "_purge_job_directory", side_effect=unlink_then_crash
        ):
            with self.assertRaisesRegex(RuntimeError, "mid-directory crash"):
                adapter.purge_retry_exhausted(
                    **copy.deepcopy(request),
                    purge_id=token["purge_id"],
                    exhaustion=copy.deepcopy(token),
                )
        partial = adapter._read_submission(record_path)
        self.assertEqual(partial["launch_state"], "purging")
        self.assertTrue(prior_dir.is_dir())
        self.assertFalse((prior_dir / ".worker-launch.json").exists())
        self.assertTrue(current_dir.is_dir())

        replay = adapter.purge_retry_exhausted(
            **copy.deepcopy(request),
            purge_id=token["purge_id"],
            exhaustion=copy.deepcopy(token),
        ).as_dict()
        self.assertTrue(replay["replayed"])
        self.assertFalse(prior_dir.exists())
        self.assertFalse(current_dir.exists())
        self.assertEqual(
            adapter._read_submission(record_path)["launch_state"], "purged"
        )

    def test_submit_cannot_resume_a_durably_authorized_attempt_two(self) -> None:
        retry_root = self.base / "retry-submit-guard-runs"
        retry_root.mkdir(mode=0o700)
        launcher = StoppedThenSuccessfulSafeLauncher(defer_retry=True)
        adapter = self.make_adapter(run_root=retry_root, launcher=launcher)
        request = self.submit_kwargs(
            task_id="task-retry-submit-guard",
            idempotency_key="task-retry-submit-guard:invert:0001",
        )

        with self.assertRaises(AdapterUnavailable):
            adapter.submit(**copy.deepcopy(request))
        proof = adapter.probe_pre_running_retry(**copy.deepcopy(request))
        authorization = {
            "schema_version": "1.0.0",
            "intent_id": "intent-retry-submit-guard",
            "previous_attempt_id": proof.previous_attempt_id,
            "previous_observation_sequence": 1,
            "failure_kind": "pre_running_launch_failure",
            "private_proof_hash": proof.private_proof_hash,
            "next_attempt_number": 2,
            "authorized_at": "2026-07-15T06:00:01Z",
        }
        with self.assertRaises(AdapterUnavailable) as deferred:
            adapter.retry_pre_running(
                **copy.deepcopy(request), authorization=authorization
            )
        self.assertEqual(deferred.exception.code, "ADAPTER_CONCURRENCY_LIMIT")
        self.assertEqual(launcher.calls, 2)

        with self.assertRaises(AdapterUnavailable) as denied:
            adapter.submit(**copy.deepcopy(request))
        self.assertEqual(
            denied.exception.code, "WORKER_RETRY_AUTHORIZATION_REQUIRED"
        )
        self.assertEqual(launcher.calls, 2)

        record_path = next(
            (
                retry_root
                / fwi_adapter_module.CONTROL_DIRECTORY
                / "submissions"
            ).glob("*.json")
        )
        record = adapter._read_submission(record_path)
        self.assertEqual(record["launch_state"], "preparing")
        self.assertEqual(
            binding_from_submission_record(record).attempt_number, 2
        )

    def test_only_exact_staged_submission_is_resumed_without_new_attempt(self) -> None:
        for index, launch_state in enumerate(("preparing", "launching"), start=1):
            with self.subTest(launch_state=launch_state):
                request = self.submit_kwargs(
                    task_id=f"task-incomplete-{index}",
                    idempotency_key=f"task-incomplete-{index}:invert:0001",
                )
                handle = self.adapter.submit(**copy.deepcopy(request))
                record_path = self.submission_record_path(handle)
                record = json.loads(record_path.read_text(encoding="utf-8"))
                record["launch_state"] = launch_state
                self.adapter._write_submission(record_path, record)

                replacement_launcher = FakeLauncher()
                reopened = self.make_adapter(launcher=replacement_launcher)
                resumed = reopened.submit(**copy.deepcopy(request))
                self.assertEqual(_plain(resumed), _plain(handle))
                self.assertEqual(len(replacement_launcher.calls), 1)
                resumed_record = json.loads(
                    record_path.read_text(encoding="utf-8")
                )
                self.assertEqual(
                    resumed_record["launch_attempt"], record["launch_attempt"]
                )
                self.assertEqual(resumed_record["job_id"], record["job_id"])

    def test_capacity_deferred_submission_resumes_same_staged_attempt(self) -> None:
        capacity_launcher = CapacityDeferredLauncher()
        deferred_adapter = self.make_adapter(launcher=capacity_launcher)
        request = self.submit_kwargs(
            task_id="task-capacity-resume",
            idempotency_key="task-capacity-resume:invert:0001",
        )

        with self.assertRaises(AdapterUnavailable) as raised:
            deferred_adapter.submit(**copy.deepcopy(request))
        self.assertEqual(raised.exception.code, "ADAPTER_CONCURRENCY_LIMIT")
        self.assertEqual(len(capacity_launcher.calls), 1)

        records = list(
            (
                self.run_root
                / fwi_adapter_module.CONTROL_DIRECTORY
                / "submissions"
            ).glob("*.json")
        )
        self.assertEqual(len(records), 1)
        original = json.loads(records[0].read_text(encoding="utf-8"))
        self.assertEqual(original["launch_state"], "preparing")
        run_dir = capacity_launcher.calls[0]["run_dir"]
        binding = binding_from_submission_record(original)
        evidence = read_worker_attempt_evidence(
            self.run_root, run_dir, binding
        )
        self.assertIsNotNone(evidence)
        assert evidence is not None
        self.assertEqual(evidence.ticket_state, "staged")
        self.assertIsNone(evidence.capacity_slot)
        self.assertIsNone(evidence.capacity_generation)
        self.assertIsNone(evidence.ticket_worker_pid)
        self.assertFalse(evidence.ready)
        self.assertIsNone(evidence.heartbeat_record_hash)

        replacement_launcher = FakeLauncher()
        reopened = self.make_adapter(launcher=replacement_launcher)
        resumed = reopened.submit(**copy.deepcopy(request))
        self.assertEqual(len(replacement_launcher.calls), 1)
        self.assertEqual(replacement_launcher.calls[0]["run_dir"], run_dir)
        current = json.loads(records[0].read_text(encoding="utf-8"))
        self.assertEqual(current["launch_state"], "launched")
        self.assertEqual(current["launch_attempt"], original["launch_attempt"])
        self.assertEqual(current["job_id"], original["job_id"])
        self.assertEqual(resumed.job_id, original["job_id"])
        self.assertEqual(
            [path.name for path in self.run_root.iterdir() if not path.name.startswith(".")],
            [original["job_id"]],
        )

    def test_capacity_reset_crash_resumes_exact_launching_staged_attempt(
        self,
    ) -> None:
        capacity_launcher = CapacityDeferredLauncher()
        deferred_adapter = self.make_adapter(launcher=capacity_launcher)
        request = self.submit_kwargs(
            task_id="task-capacity-reset-crash",
            idempotency_key="task-capacity-reset-crash:invert:0001",
        )
        write_submission = deferred_adapter._write_submission
        writes = 0

        def crash_before_preparing_reset(path: Path, record: dict[str, Any]) -> None:
            nonlocal writes
            writes += 1
            if writes == 3:
                raise OSError("synthetic reset crash")
            write_submission(path, record)

        with patch.object(
            deferred_adapter,
            "_write_submission",
            side_effect=crash_before_preparing_reset,
        ), self.assertRaisesRegex(OSError, "synthetic reset crash"):
            deferred_adapter.submit(**copy.deepcopy(request))

        record_path = next(
            (
                self.run_root
                / fwi_adapter_module.CONTROL_DIRECTORY
                / "submissions"
            ).glob("*.json")
        )
        original = json.loads(record_path.read_text(encoding="utf-8"))
        self.assertEqual(original["launch_state"], "launching")
        run_dir = capacity_launcher.calls[0]["run_dir"]
        evidence = read_worker_attempt_evidence(
            self.run_root,
            run_dir,
            binding_from_submission_record(original),
        )
        self.assertIsNotNone(evidence)
        assert evidence is not None
        self.assertEqual(evidence.ticket_state, "staged")
        self.assertFalse(evidence.ready)

        replacement_launcher = FakeLauncher()
        reopened = self.make_adapter(launcher=replacement_launcher)
        resumed = reopened.submit(**copy.deepcopy(request))
        self.assertEqual(len(replacement_launcher.calls), 1)
        current = json.loads(record_path.read_text(encoding="utf-8"))
        self.assertEqual(current["launch_state"], "launched")
        self.assertEqual(current["launch_attempt"], original["launch_attempt"])
        self.assertEqual(resumed.job_id, original["job_id"])

    def test_status_maps_four_states_and_rejects_corruption(self) -> None:
        for index, state in enumerate(
            ("queued", "running", "succeeded", "failed"), start=1
        ):
            with self.subTest(state=state):
                handle, run_dir = self.submit_and_run_dir(
                    task_id=f"task-state-{index}",
                    idempotency_key=f"task-state-{index}:invert:0001",
                )
                self.write_status(run_dir, state)
                self.assertEqual(_status_name(self.adapter.status(handle)), state)

        corruptions = (
            "missing",
            "malformed",
            "wrong_identity",
            "unknown",
            "contradictory_success",
            "fifo",
            "symlink",
        )
        for index, corruption in enumerate(corruptions, start=1):
            with self.subTest(corruption=corruption):
                handle, run_dir = self.submit_and_run_dir(
                    task_id=f"task-corrupt-{index}",
                    idempotency_key=f"task-corrupt-{index}:invert:0001",
                )
                status_path = run_dir / "status.json"
                if corruption == "missing":
                    status_path.unlink()
                elif corruption == "malformed":
                    status_path.write_text("{not-json", encoding="utf-8")
                elif corruption == "wrong_identity":
                    status_path.write_text(
                        json.dumps({"job_id": "another-job", "status": "running"}),
                        encoding="utf-8",
                    )
                elif corruption == "unknown":
                    config = json.loads(
                        (run_dir / "config.original.json").read_text(encoding="utf-8")
                    )
                    status_path.write_text(
                        json.dumps(
                            {"job_id": config["job_id"], "status": "complete"}
                        ),
                        encoding="utf-8",
                    )
                elif corruption == "contradictory_success":
                    config = json.loads(
                        (run_dir / "config.original.json").read_text(encoding="utf-8")
                    )
                    status_path.write_text(
                        json.dumps(
                            {
                                "job_id": config["job_id"],
                                "status": "succeeded",
                                "stage": "queued",
                                "iteration": 0,
                                "total_iterations": 2,
                                "message": "contradictory success",
                                "updated_at": NOW,
                            }
                        ),
                        encoding="utf-8",
                    )
                elif corruption == "fifo":
                    status_path.unlink()
                    os.mkfifo(status_path, mode=0o600)
                else:
                    outside = self.base / f"outside-status-{index}.json"
                    outside.write_text(
                        json.dumps({"job_id": "outside", "status": "succeeded"}),
                        encoding="utf-8",
                    )
                    status_path.unlink()
                    status_path.symlink_to(outside)
                with self.assertRaises((ValueError, RuntimeError, OSError)):
                    self.adapter.status(handle)

    def test_receipt_bindings_are_exact_version_pairs(self) -> None:
        self.assertEqual(
            fwi_adapter_module.SUPPORTED_RECEIPT_BINDINGS,
            frozenset(
                {
                    ("1.0.0", "1.0.0"),
                    ("1.1.0", "1.1.0"),
                    ("1.2.0", "1.2.0"),
                    ("1.3.0", "1.3.0"),
                    ("1.4.0", "1.4.0"),
                    ("1.5.0", "1.5.0"),
                }
            ),
        )

    def test_historical_v1_4_receipt_collects_and_reads_all_declared_outputs(
        self,
    ) -> None:
        handle, run_dir = self.submit_and_run_dir(
            task_id="task-historical-v1-4-artifacts",
            idempotency_key="task-historical-v1-4-artifacts:invert:0001",
        )
        expected_paths = self.write_success_artifacts(run_dir)
        record_path = self.submission_record_path(handle)
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["algorithm"]["version"] = "1.4.0"
        record["adapter_version"] = "1.4.0"
        record["fingerprint"]["algorithm"]["version"] = "1.4.0"
        record["fingerprint"]["adapter_version"] = "1.4.0"
        record["request_hash"] = fwi_adapter_module._sha256_document(
            self.adapter._record_request_payload(record)
        )
        historical_binding = LaunchAttemptBinding(
            submission_id=record["submission_id"],
            attempt_id=record["launch_attempt"]["attempt_id"],
            attempt_number=record["launch_attempt"]["attempt_number"],
            job_id=record["job_id"],
            request_hash=record["request_hash"],
            created_at=record["created_at"],
        )
        record["launch_attempt"] = historical_binding.record()
        self.adapter._write_submission(record_path, record)
        historical_handle = self.adapter._handle_from_record(record)

        manifests = self.adapter.collect(historical_handle)
        self.assertEqual(len(manifests), 8)
        self.assertEqual(
            {
                manifest["extensions"]["org.agent_rpc.adapter"][
                    "output_port"
                ]
                for manifest in manifests
            },
            set(expected_paths),
        )
        for manifest in manifests:
            with self.subTest(artifact_id=manifest["artifact_id"]):
                returned, data = self.adapter.read_artifact(
                    historical_handle, manifest["artifact_id"]
                )
                port = returned["extensions"]["org.agent_rpc.adapter"][
                    "output_port"
                ]
                self.assertEqual(returned, manifest)
                self.assertEqual(data, expected_paths[port].read_bytes())
                self.assertEqual(
                    returned["lineage"]["algorithm"]["version"], "1.4.0"
                )
                self.assertEqual(
                    returned["fingerprint"]["adapter_version"], "1.4.0"
                )

    def test_historical_v1_4_managed_receipt_retains_cancel_and_timeout_probes(
        self,
    ) -> None:
        handle, run_dir = self.submit_and_run_dir(
            task_id="task-historical-v1-4-control",
            idempotency_key="task-historical-v1-4-control:invert:0001",
        )
        record_path = self.submission_record_path(handle)
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["algorithm"]["version"] = "1.4.0"
        record["adapter_version"] = "1.4.0"
        record["fingerprint"]["algorithm"]["version"] = "1.4.0"
        record["fingerprint"]["adapter_version"] = "1.4.0"
        record["request_hash"] = fwi_adapter_module._sha256_document(
            self.adapter._record_request_payload(record)
        )
        historical_binding = LaunchAttemptBinding(
            submission_id=record["submission_id"],
            attempt_id=record["launch_attempt"]["attempt_id"],
            attempt_number=record["launch_attempt"]["attempt_number"],
            job_id=record["job_id"],
            request_hash=record["request_hash"],
            created_at=record["created_at"],
        )
        record["launch_attempt"] = historical_binding.record()
        self.adapter._write_submission(record_path, record)

        ticket_path = run_dir / ".worker-launch.json"
        ticket = json.loads(ticket_path.read_text(encoding="utf-8"))
        ticket["request_hash"] = record["request_hash"]
        ticket["binding_hash"] = historical_binding.binding_hash
        ticket.pop("record_hash")
        ticket["record_hash"] = fwi_adapter_module._sha256_document(ticket)
        ticket_path.write_text(json.dumps(ticket), encoding="utf-8")
        ticket_path.chmod(0o600)

        historical_handle = self.adapter._handle_from_record(record)
        binding, heartbeat = self.start_exact_worker(
            historical_handle, run_dir
        )
        try:
            self.assertTrue(
                self.adapter.supports_exact_cancel(
                    historical_handle, attempt_id=binding.attempt_id
                )
            )
            timeout = self.adapter.supports_exact_timeout(
                historical_handle, binding.attempt_id
            )
            self.assertIsNotNone(timeout)
            assert timeout is not None
            self.assertEqual(timeout["private_schema_version"], "1.1.0")
            self.assertEqual(timeout["attempt_id"], binding.attempt_id)

            cancellation = self.adapter.cancel(
                historical_handle,
                cancel_id="cancel-historical-v1-4-control-1",
                attempt_id=binding.attempt_id,
                reason="user_requested",
            )
            self.assertEqual(cancellation.state, "requested")
            for _ in range(200):
                cancel_evidence = read_worker_cancel_evidence(
                    self.run_root, binding
                )
                if cancel_evidence.acknowledged:
                    break
                time.sleep(0.01)
            self.assertTrue(cancel_evidence.acknowledged)
            self.write_status(run_dir, "cancelled")
            with self.assertRaises(WorkerCancellationRequested):
                heartbeat.raise_if_cancel_requested()
            heartbeat.stop("stopped")
            heartbeat = None
            self.assertEqual(
                self.adapter.status(historical_handle).status, "Cancelled"
            )
            purged = self.adapter.purge(
                historical_handle,
                purge_id="purge-historical-v1-4-control-1",
            )
            self.assertEqual(purged.local_run_state, "deleted")
            self.assertFalse(run_dir.exists())
        finally:
            if heartbeat is not None:
                heartbeat.stop("succeeded")

    def test_current_dispatcher_reads_true_v1_0_through_v1_3_receipts(self) -> None:
        dispatcher = DeepwaveTaskDispatcher(self.adapter)
        last_intent: DispatchIntentSnapshot | None = None
        last_record: dict[str, Any] | None = None
        last_record_path: Path | None = None

        for index, legacy_version in enumerate(
            ("1.0.0", "1.1.0", "1.2.0", "1.3.0"), start=1
        ):
            with self.subTest(legacy_version=legacy_version):
                handle, run_dir = self.submit_and_run_dir(
                    task_id=f"task-legacy-{index}",
                    idempotency_key=f"task-legacy-{index}:invert:0001",
                )
                self.write_success_artifacts(run_dir)

                # Real 1.0/1.1 receipts predate the six-field optimizer
                # contract.  Their public parameters and private Worker config
                # omit optimizer controls and resolve to the historical
                # Adam/10.0/0.98 defaults only inside the read path.  Version
                # 1.2 already carries those controls and must retain them.
                config_path = run_dir / "config.original.json"
                legacy_config = json.loads(config_path.read_text(encoding="utf-8"))
                if legacy_version in {"1.0.0", "1.1.0"}:
                    for field in (
                        "optimizer",
                        "learning_rate",
                        "gradient_clip_quantile",
                    ):
                        legacy_config.pop(field)
                config_path.write_text(json.dumps(legacy_config), encoding="utf-8")

                record_path = self.submission_record_path(handle)
                record = json.loads(record_path.read_text(encoding="utf-8"))
                record["algorithm"]["version"] = legacy_version
                record["adapter_version"] = legacy_version
                record["fingerprint"]["algorithm"]["version"] = legacy_version
                record["fingerprint"]["adapter_version"] = legacy_version
                if legacy_version in {"1.0.0", "1.1.0"}:
                    record["parameters"].pop("optimizer")
                    record["parameters"].pop("learning_rate_milli")
                    for field in (
                        "optimizer",
                        "learning_rate",
                        "gradient_clip_quantile",
                    ):
                        record["worker_config"].pop(field)
                record["request_hash"] = fwi_adapter_module._sha256_document(
                    self.adapter._record_request_payload(record)
                )
                # Historical receipts predate the staged launch-control
                # schema.  Keep the fixture byte-shape faithful instead of
                # carrying a stale current-attempt binding into old versions.
                record["schema_version"] = "1.0.0"
                record.pop("launch_attempt")
                self.adapter._write_submission(record_path, record)
                legacy_handle = self.adapter._handle_from_record(record)

                intent = DispatchIntentSnapshot(
                    intent_id=f"dispatch-legacy-{index}",
                    task_id=record["task_id"],
                    plan_id=f"plan-legacy-{index}",
                    plan_hash=record["plan_hash"],
                    approval_id=f"approval-legacy-{index}",
                    node_id=record["node_id"],
                    node_idempotency_key=record["idempotency_key"],
                    adapter_id="fwi.deepwave_adapter",
                    adapter_version=legacy_version,
                    request={},
                    request_hash="sha256:" + "e" * 64,
                    queue_fingerprint=copy.deepcopy(record["fingerprint"]),
                    state="dispatched",
                    handle=legacy_handle.as_dict(),
                    failure_code=None,
                    created_at=NOW,
                    dispatch_claimed_at=NOW,
                    outcome_recorded_at=NOW,
                )
                self.assertEqual(dispatcher.status(intent)["status"], "Succeeded")
                with self.assertRaises(DispatchError) as unavailable:
                    dispatcher.observe_existing_worker_attempt(intent)
                self.assertEqual(
                    unavailable.exception.code, "WORKER_EVIDENCE_UNAVAILABLE"
                )
                artifacts = dispatcher.collect(intent)
                self.assertEqual(len(artifacts), 2)
                self.assertEqual(artifacts[0]["metrics"]["optimizer"], "adam")
                self.assertEqual(artifacts[0]["metrics"]["learning_rate"], 10.0)
                self.assertEqual(
                    artifacts[0]["metrics"]["gradient_clip_quantile"], 0.98
                )
                with patch.object(
                    self.adapter, "collect", wraps=self.adapter.collect
                ) as collect_once:
                    collected, artifact, data = dispatcher.read_artifact(
                        intent, artifacts[0]["artifact_id"]
                    )
                self.assertEqual(collect_once.call_count, 1)
                self.assertEqual(collected, artifacts)
                self.assertEqual(
                    artifact["fingerprint"]["adapter_version"], legacy_version
                )
                self.assertEqual(
                    artifact["lineage"]["algorithm"]["version"], legacy_version
                )
                self.assertEqual(len(data), artifact["size_bytes"])

                mismatched_version = "1.4.0"
                mismatched_intent = dataclasses.replace(
                    intent, adapter_version=mismatched_version
                )
                with self.assertRaisesRegex(
                    DispatchError, "DISPATCH_RECEIPT_INVALID"
                ):
                    dispatcher.status(mismatched_intent)
                last_intent = intent
                last_record = record
                last_record_path = record_path

        assert last_intent is not None
        dispatching_legacy = dataclasses.replace(
            last_intent, state="dispatching", handle=None
        )
        with self.assertRaisesRegex(DispatchError, "DISPATCH_INTENT_INVALID"):
            dispatcher.dispatch(dispatching_legacy)

        assert last_record is not None and last_record_path is not None
        last_record["adapter_version"] = "1.0.0"
        last_record["fingerprint"]["adapter_version"] = "1.0.0"
        self.adapter._write_submission(last_record_path, last_record)
        mixed_handle = self.adapter._handle_from_record(last_record)
        with self.assertRaisesRegex(
            RuntimeError, "private record version is unsupported"
        ):
            self.adapter._read_submission(last_record_path)
        with self.assertRaisesRegex(RuntimeError, "ADAPTER_HANDLE_INVALID"):
            self.adapter.status(mixed_handle)

    def test_current_v1_5_receipt_reopens_private_schema_v1_0(self) -> None:
        request = self.submit_kwargs(
            task_id="task-current-private-v1",
            idempotency_key="task-current-private-v1:invert:0001",
        )
        handle = self.adapter.submit(**copy.deepcopy(request))
        run_dir = self.launcher.calls[-1]["run_dir"]
        self.write_success_artifacts(run_dir)
        record_path = self.submission_record_path(handle)
        record = json.loads(record_path.read_text(encoding="utf-8"))
        self.assertEqual(record["adapter_version"], "1.5.0")
        current_binding = binding_from_submission_record(record)
        record["schema_version"] = "1.0.0"
        record.pop("launch_attempt")
        self.adapter._write_submission(record_path, record)

        replacement_launcher = FakeLauncher()
        reopened = self.make_adapter(launcher=replacement_launcher)
        recovered = reopened.lookup_existing_handle(**copy.deepcopy(request))
        self.assertEqual(recovered.as_dict(), handle.as_dict())
        proof = reopened.lookup_existing_private_receipt(
            **copy.deepcopy(request)
        )
        self.assertEqual(proof.handle.as_dict(), handle.as_dict())
        self.assertEqual(proof.private_schema_version, "1.0.0")
        self.assertEqual(
            proof.receipt_record_hash,
            json.loads(record_path.read_text(encoding="utf-8"))["record_hash"],
        )
        self.assertFalse(
            reopened.supports_exact_cancel(
                recovered, attempt_id=current_binding.attempt_id
            )
        )
        cancel = reopened.cancel(
            recovered,
            cancel_id="cancel-private-v1-deferred-1",
            attempt_id=current_binding.attempt_id,
            reason="user_requested",
        )
        self.assertIsInstance(cancel, AdapterManagedCancelProof)
        self.assertEqual(cancel.state, "deferred")
        self.assertEqual(cancel.code, "CANCEL_MANAGED_ATTEMPT_UNAVAILABLE")
        cancel_dir = (
            self.run_root
            / fwi_adapter_module.CONTROL_DIRECTORY
            / "worker-cancel"
        )
        self.assertFalse(
            (cancel_dir / f"{current_binding.attempt_id}.request.json").exists()
        )
        with self.assertRaises(AdapterUnavailable) as unavailable:
            reopened.observe_existing_worker_attempt(**copy.deepcopy(request))
        self.assertEqual(
            unavailable.exception.code, "WORKER_EVIDENCE_UNAVAILABLE"
        )
        self.assertEqual(reopened.status(recovered).status, "Succeeded")
        artifacts = reopened.collect(recovered)
        self.assertEqual(len(artifacts), 8)
        self.assertEqual(
            sum(artifact["artifact_type"] == "figure" for artifact in artifacts),
            6,
        )
        self.assertEqual(replacement_launcher.calls, [])

    def test_cancel_is_a_stable_p1_noop(self) -> None:
        handle, _ = self.submit_and_run_dir()
        first = _plain(self.adapter.cancel(handle))
        second = _plain(self.adapter.cancel(handle))
        self.assertEqual(first, second)
        encoded = json.dumps(first, sort_keys=True)
        self.assertIn("CANCEL_NOT_SUPPORTED", encoded)
        self.assertEqual(_status_name(self.adapter.status(handle)), "queued")
        self.assertEqual(len(self.launcher.calls), 1)

    def test_dispatcher_cancel_is_exact_idempotent_and_fence_proven(self) -> None:
        handle, run_dir = self.submit_and_run_dir(
            task_id="task-managed-cancel",
            idempotency_key="task-managed-cancel:invert:0001",
        )
        binding, heartbeat = self.start_exact_worker(handle, run_dir)
        dispatcher = DeepwaveTaskDispatcher(self.adapter)
        intent = self.dispatched_intent(handle)
        request_id = "cancel-managed-request-1"
        wrong_attempt = "attempt-" + "f" * 32
        try:
            self.assertTrue(
                dispatcher.supports_exact_cancel(
                    intent, attempt_id=binding.attempt_id
                )
            )
            self.assertFalse(
                dispatcher.supports_exact_cancel(
                    intent, attempt_id=wrong_attempt
                )
            )
            mismatch = dispatcher.cancel(
                intent,
                request_id="cancel-wrong-attempt-1",
                attempt_id=wrong_attempt,
                reason="user_requested",
            )
            self.assertEqual(mismatch["state"], "deferred")
            self.assertEqual(mismatch["code"], "CANCEL_ATTEMPT_MISMATCH")
            self.assertFalse(
                read_worker_cancel_evidence(
                    self.run_root, binding
                ).requested
            )

            requested = dispatcher.cancel(
                intent,
                request_id=request_id,
                attempt_id=binding.attempt_id,
                reason="user_requested",
            )
            self.assertEqual(requested["state"], "requested")
            self.assertEqual(requested["reason"], "user_requested")
            self.assertFalse(requested["replayed"])
            self.assertNotIn(str(self.run_root), json.dumps(requested))

            for _ in range(200):
                evidence = read_worker_cancel_evidence(
                    self.run_root, binding
                )
                if evidence.acknowledged:
                    break
                time.sleep(0.01)
            self.assertTrue(evidence.acknowledged)
            self.write_status(run_dir, "cancelled")
            with self.assertRaises(RuntimeError):
                self.adapter.status(handle)
            pending = dispatcher.cancel(
                intent,
                request_id=request_id,
                attempt_id=binding.attempt_id,
                reason="user_requested",
            )
            self.assertEqual(pending["state"], "pending")
            self.assertTrue(pending["replayed"])
            with self.assertRaises(AdapterIdempotencyConflict):
                self.adapter.cancel(
                    handle,
                    cancel_id="cancel-conflicting-request-2",
                    attempt_id=binding.attempt_id,
                    reason="user_requested",
                )

            with self.assertRaises(WorkerCancellationRequested):
                heartbeat.raise_if_cancel_requested()
            heartbeat.stop("stopped")
            heartbeat = None
            self.assertEqual(self.adapter.status(handle).status, "Cancelled")
            completed = dispatcher.cancel(
                intent,
                request_id=request_id,
                attempt_id=binding.attempt_id,
                reason="user_requested",
            )
            self.assertEqual(completed["state"], "cancelled")
            self.assertEqual(completed["terminal_status"], "Cancelled")
            self.assertEqual(_status_name(self.adapter.status(handle)), "cancelled")
            replay = dispatcher.cancel(
                intent,
                request_id=request_id,
                attempt_id=binding.attempt_id,
                reason="user_requested",
            )
            self.assertEqual(replay["state"], "cancelled")
            self.assertTrue(replay["replayed"])

            cancel_dir = (
                self.run_root
                / fwi_adapter_module.CONTROL_DIRECTORY
                / "worker-stop"
            )
            interrupted_temp = cancel_dir / (
                f".{binding.attempt_id}.request.json.interrupted"
            )
            interrupted_temp.write_text("inert unpublished bytes", encoding="utf-8")
            interrupted_temp.chmod(0o600)
            purged = self.adapter.purge(
                handle, purge_id="purge-managed-cancel-1"
            )
            self.assertEqual(purged.local_run_state, "deleted")
            self.assertFalse(run_dir.exists())
            self.assertEqual(
                list(cancel_dir.glob(f"{binding.attempt_id}.*")), []
            )
            self.assertFalse(interrupted_temp.exists())
        finally:
            if heartbeat is not None:
                heartbeat.stop("stopped")

    def test_current_schema_without_worker_capability_defers_without_request(
        self,
    ) -> None:
        handle, run_dir = self.submit_and_run_dir(
            task_id="task-no-cancel-capability",
            idempotency_key="task-no-cancel-capability:invert:0001",
        )
        record = json.loads(
            self.submission_record_path(handle).read_text(encoding="utf-8")
        )
        binding = binding_from_submission_record(record)
        lease = ParentLaunchLease.acquire(
            self.run_root, run_dir, max_active=2
        )
        lease.mark_spawned(os.getpid())
        heartbeat = WorkerHeartbeat(
            run_root=self.run_root,
            run_dir=run_dir,
            attempt_id=binding.attempt_id,
            attempt_fd=os.dup(lease.attempt_fd),
            capacity_fd=os.dup(lease.capacity_fd),
            interval_seconds=60.0,
        )
        lease.close_parent()
        heartbeat.start()
        # Let the current heartbeat enter its long wait, then remove the
        # capability to model a still-running pre-capability schema-1.1 Worker.
        time.sleep(0.05)
        cancel_dir = (
            self.run_root
            / fwi_adapter_module.CONTROL_DIRECTORY
            / "worker-stop"
        )
        capability = cancel_dir / f"{binding.attempt_id}.capability.json"
        capability.unlink()
        intent = self.dispatched_intent(handle)
        dispatcher = DeepwaveTaskDispatcher(self.adapter)
        try:
            self.assertFalse(
                dispatcher.supports_exact_cancel(
                    intent, attempt_id=binding.attempt_id
                )
            )
            deferred = dispatcher.cancel(
                intent,
                request_id="cancel-no-capability-1",
                attempt_id=binding.attempt_id,
                reason="user_requested",
            )
            self.assertEqual(deferred["state"], "deferred")
            self.assertEqual(
                deferred["code"], "CANCEL_WORKER_CAPABILITY_UNAVAILABLE"
            )
            self.assertFalse(
                (cancel_dir / f"{binding.attempt_id}.request.json").exists()
            )
        finally:
            heartbeat.stop("succeeded")

    def test_exact_timeout_capability_and_idle_fence_prove_wall_time_failure(
        self,
    ) -> None:
        clock_value = ["2026-07-17T00:00:00Z"]
        adapter = self.make_adapter(clock=lambda: clock_value[0])
        handle = adapter.submit(
            **self.submit_kwargs(
                task_id="task-managed-timeout",
                idempotency_key="task-managed-timeout:invert:0001",
            )
        )
        run_dir = self.launcher.calls[-1]["run_dir"]
        binding, heartbeat = self.start_exact_worker(handle, run_dir)
        wall_time_seconds = resources()["wall_time_seconds"]
        started_at = "2026-07-17T00:00:01.123456Z"
        deadline_at = "2026-07-17T00:30:01.123456Z"
        timeout_id = "timeout-managed-request-1"
        try:
            capability = adapter.supports_exact_timeout(
                handle, binding.attempt_id
            )
            self.assertIsNotNone(capability)
            assert capability is not None
            self.assertEqual(
                set(capability),
                {
                    "schema_version",
                    "attempt_id",
                    "binding_hash",
                    "capability_record_hash",
                    "supported_reasons",
                    "private_schema_version",
                    "proof_hash",
                },
            )
            self.assertEqual(capability["schema_version"], "2.0.0")
            self.assertEqual(
                capability["supported_reasons"],
                ["user_requested", "wall_time_exceeded"],
            )

            # Avoid a real 30-minute wait while preserving the Worker's
            # independent monotonic early-ack check.
            assert heartbeat._started_monotonic is not None
            heartbeat._started_monotonic -= wall_time_seconds
            clock_value[0] = deadline_at
            first = adapter.timeout(
                handle,
                timeout_id,
                binding.attempt_id,
                wall_time_seconds,
                started_at,
                deadline_at,
            )
            self.assertIsInstance(first, AdapterManagedTimeoutProof)
            self.assertEqual(first.state, "requested")
            self.assertEqual(first.code, "TIMEOUT_REQUESTED")

            for _ in range(200):
                evidence = read_worker_stop_evidence(
                    self.run_root, binding
                )
                if evidence.acknowledged:
                    break
                time.sleep(0.01)
            self.assertTrue(evidence.acknowledged)
            self.assertEqual(evidence.reason, "wall_time_exceeded")
            self.assertEqual(
                evidence.ready_record_hash, first.ready_record_hash
            )
            with self.assertRaises(WorkerWallTimeExceeded):
                heartbeat.raise_if_cancel_requested()
            heartbeat.stop("stopped")
            SafeSubprocessWorkerLauncher._mark_unexpected_exit(
                run_dir,
                76,
                run_root=self.run_root,
                launch_binding=binding,
            )
            reaped_status = json.loads(
                (run_dir / "status.json").read_text(encoding="utf-8")
            )
            self.assertEqual(reaped_status["status"], "failed")
            self.assertEqual(
                reaped_status["failure_code"], "WALL_TIME_EXCEEDED"
            )

            completed = adapter.timeout(
                handle,
                timeout_id,
                binding.attempt_id,
                wall_time_seconds,
                started_at,
                deadline_at,
            )
            self.assertEqual(completed.state, "timed_out")
            self.assertEqual(completed.code, "TIMEOUT_COMPLETED")
            self.assertEqual(
                set(completed.as_dict()),
                {
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
                },
            )
            self.assertEqual(completed.terminal_status, "Failed")
            self.assertEqual(
                completed.terminal_failure_code, "WALL_TIME_EXCEEDED"
            )
            self.assertIsNotNone(completed.ready_record_hash)
            self.assertEqual(
                completed.capability_record_hash,
                evidence.capability_record_hash,
            )
            status = json.loads((run_dir / "status.json").read_text())
            self.assertEqual(status["status"], "failed")
            self.assertEqual(status["failure_code"], "WALL_TIME_EXCEEDED")
        finally:
            try:
                heartbeat.stop("stopped")
            except Exception:
                pass

    def test_natural_terminal_wins_exact_timeout_with_armed_window_hashes(
        self,
    ) -> None:
        clock_value = [NOW]
        adapter = self.make_adapter(clock=lambda: clock_value[0])
        handle = adapter.submit(
            **self.submit_kwargs(
                task_id="task-natural-terminal-timeout",
                idempotency_key="task-natural-terminal-timeout:invert:0001",
            )
        )
        run_dir = self.launcher.calls[-1]["run_dir"]
        binding, heartbeat = self.start_exact_worker(handle, run_dir)
        wall_time_seconds = resources()["wall_time_seconds"]
        attempt = read_worker_attempt_evidence(
            self.run_root, run_dir, binding
        )
        self.assertIsNotNone(attempt)
        assert attempt is not None
        assert attempt.ready_started_at is not None
        assert attempt.ready_record_hash is not None
        started = datetime.fromisoformat(
            attempt.ready_started_at.replace("Z", "+00:00")
        ) + timedelta(microseconds=1)
        deadline = started + timedelta(seconds=wall_time_seconds)
        started_at = started.isoformat().replace("+00:00", "Z")
        deadline_at = deadline.isoformat().replace("+00:00", "Z")
        evidence = read_worker_stop_evidence(self.run_root, binding)
        try:
            self.write_success_artifacts(run_dir)
            heartbeat.stop("succeeded")
            heartbeat = None
            clock_value[0] = deadline_at
            proof = adapter.timeout(
                handle,
                "timeout-after-natural-success-1",
                binding.attempt_id,
                wall_time_seconds,
                started_at,
                deadline_at,
            )
            self.assertEqual(proof.state, "terminal_won")
            self.assertEqual(proof.code, "TIMEOUT_TERMINAL_WON")
            self.assertEqual(proof.terminal_status, "Succeeded")
            self.assertIsNone(proof.terminal_failure_code)
            self.assertEqual(
                proof.ready_record_hash, attempt.ready_record_hash
            )
            self.assertEqual(
                proof.capability_record_hash,
                evidence.capability_record_hash,
            )
            self.assertIsNone(proof.request_record_hash)
            self.assertEqual(adapter.status(handle).status, "Succeeded")
            request_path = (
                self.run_root
                / fwi_adapter_module.CONTROL_DIRECTORY
                / "worker-stop"
                / f"{binding.attempt_id}.request.json"
            )
            self.assertFalse(request_path.exists())
        finally:
            if heartbeat is not None:
                heartbeat.stop("succeeded")

    def test_timeout_store_clock_may_precede_worker_ready_clock(self) -> None:
        clock_value = [NOW]
        adapter = self.make_adapter(clock=lambda: clock_value[0])
        handle = adapter.submit(
            **self.submit_kwargs(
                task_id="task-cross-clock-timeout",
                idempotency_key="task-cross-clock-timeout:invert:0001",
            )
        )
        run_dir = self.launcher.calls[-1]["run_dir"]
        binding, heartbeat = self.start_exact_worker(handle, run_dir)
        wall_time_seconds = resources()["wall_time_seconds"]
        attempt = read_worker_attempt_evidence(
            self.run_root, run_dir, binding
        )
        self.assertIsNotNone(attempt)
        assert attempt is not None
        assert attempt.ready_started_at is not None
        assert attempt.ready_record_hash is not None
        ready_time = datetime.fromisoformat(
            attempt.ready_started_at.replace("Z", "+00:00")
        )
        observed_time = ready_time - timedelta(seconds=60)
        deadline = observed_time + timedelta(seconds=wall_time_seconds)
        started_at = observed_time.isoformat().replace("+00:00", "Z")
        deadline_at = deadline.isoformat().replace("+00:00", "Z")
        self.assertLess(observed_time, ready_time)
        try:
            clock_value[0] = deadline_at
            requested = adapter.timeout(
                handle,
                "timeout-cross-clock-request-1",
                binding.attempt_id,
                wall_time_seconds,
                started_at,
                deadline_at,
            )
            self.assertEqual(requested.state, "requested")
            self.assertEqual(requested.code, "TIMEOUT_REQUESTED")
            self.assertEqual(
                requested.ready_record_hash, attempt.ready_record_hash
            )
            time.sleep(0.08)
            evidence = read_worker_stop_evidence(
                self.run_root, binding
            )
            self.assertTrue(evidence.requested)
            self.assertFalse(evidence.acknowledged)
            self.assertEqual(evidence.started_at, started_at)
            self.assertEqual(
                evidence.ready_record_hash, attempt.ready_record_hash
            )

            assert heartbeat._started_monotonic is not None
            heartbeat._started_monotonic -= wall_time_seconds
            with self.assertRaises(WorkerWallTimeExceeded):
                heartbeat.raise_if_cancel_requested()
            heartbeat.stop("stopped")
            heartbeat = None
        finally:
            if heartbeat is not None:
                heartbeat.stop("stopped")

    def test_ordinary_failure_wins_exact_timeout_without_failure_code(
        self,
    ) -> None:
        clock_value = [NOW]
        adapter = self.make_adapter(clock=lambda: clock_value[0])
        handle = adapter.submit(
            **self.submit_kwargs(
                task_id="task-natural-failure-timeout",
                idempotency_key="task-natural-failure-timeout:invert:0001",
            )
        )
        run_dir = self.launcher.calls[-1]["run_dir"]
        binding, heartbeat = self.start_exact_worker(handle, run_dir)
        wall_time_seconds = resources()["wall_time_seconds"]
        attempt = read_worker_attempt_evidence(
            self.run_root, run_dir, binding
        )
        self.assertIsNotNone(attempt)
        assert attempt is not None
        assert attempt.ready_started_at is not None
        assert attempt.ready_record_hash is not None
        started = datetime.fromisoformat(
            attempt.ready_started_at.replace("Z", "+00:00")
        ) + timedelta(microseconds=1)
        deadline = started + timedelta(seconds=wall_time_seconds)
        started_at = started.isoformat().replace("+00:00", "Z")
        deadline_at = deadline.isoformat().replace("+00:00", "Z")
        evidence = read_worker_stop_evidence(self.run_root, binding)
        try:
            self.write_status(run_dir, "failed")
            heartbeat.stop("failed")
            heartbeat = None
            clock_value[0] = deadline_at
            proof = adapter.timeout(
                handle,
                "timeout-after-natural-failure-1",
                binding.attempt_id,
                wall_time_seconds,
                started_at,
                deadline_at,
            )
            self.assertEqual(proof.state, "terminal_won")
            self.assertEqual(proof.code, "TIMEOUT_TERMINAL_WON")
            self.assertEqual(proof.terminal_status, "Failed")
            self.assertIsNone(proof.terminal_failure_code)
            self.assertEqual(
                proof.ready_record_hash, attempt.ready_record_hash
            )
            self.assertEqual(
                proof.capability_record_hash,
                evidence.capability_record_hash,
            )
            self.assertIsNone(proof.request_record_hash)
            self.assertEqual(adapter.status(handle).status, "Failed")
        finally:
            if heartbeat is not None:
                heartbeat.stop("failed")

    def test_natural_terminal_wins_without_creating_cancel_request(self) -> None:
        handle, run_dir = self.submit_and_run_dir(
            task_id="task-natural-terminal-cancel",
            idempotency_key="task-natural-terminal-cancel:invert:0001",
        )
        record = json.loads(
            self.submission_record_path(handle).read_text(encoding="utf-8")
        )
        binding = binding_from_submission_record(record)
        self.write_success_artifacts(run_dir)
        proof = self.adapter.cancel(
            handle,
            cancel_id="cancel-after-success-1",
            attempt_id=binding.attempt_id,
            reason="user_requested",
        )
        self.assertIsInstance(proof, AdapterManagedCancelProof)
        document = proof.as_dict()
        self.assertEqual(document["state"], "terminal_won")
        self.assertEqual(document["terminal_status"], "Succeeded")
        with self.assertRaisesRegex(
            RuntimeError, "WORKER_CANCEL_UNSUPPORTED"
        ):
            read_worker_cancel_evidence(self.run_root, binding)
        cancel_dir = (
            self.run_root
            / fwi_adapter_module.CONTROL_DIRECTORY
            / "worker-cancel"
        )
        self.assertFalse(
            (cancel_dir / f"{binding.attempt_id}.request.json").exists()
        )
        self.assertEqual(self.adapter.status(handle).status, "Succeeded")

    def test_natural_success_after_cancel_request_is_never_overwritten(self) -> None:
        handle, run_dir = self.submit_and_run_dir(
            task_id="task-natural-terminal-race",
            idempotency_key="task-natural-terminal-race:invert:0001",
        )
        binding, heartbeat = self.start_exact_worker(handle, run_dir)
        dispatcher = DeepwaveTaskDispatcher(self.adapter)
        intent = self.dispatched_intent(handle)
        request_id = "cancel-natural-terminal-race-1"
        try:
            requested = dispatcher.cancel(
                intent,
                request_id=request_id,
                attempt_id=binding.attempt_id,
                reason="user_requested",
            )
            self.assertEqual(requested["state"], "requested")
            self.write_success_artifacts(run_dir)
            heartbeat.stop("succeeded")
            heartbeat = None
            terminal = dispatcher.cancel(
                intent,
                request_id=request_id,
                attempt_id=binding.attempt_id,
                reason="user_requested",
            )
            self.assertEqual(terminal["state"], "terminal_won")
            self.assertEqual(terminal["terminal_status"], "Succeeded")
            self.assertEqual(self.adapter.status(handle).status, "Succeeded")
            status = json.loads(
                (run_dir / "status.json").read_text(encoding="utf-8")
            )
            self.assertEqual(status["status"], "succeeded")
        finally:
            if heartbeat is not None:
                heartbeat.stop("succeeded")

    def test_status_redacts_worker_errors_and_cancel_ignores_status_corruption(self) -> None:
        handle, run_dir = self.submit_and_run_dir(
            task_id="task-redaction",
            idempotency_key="task-redaction:invert:0001",
        )
        self.write_status(run_dir, "failed")
        status_path = run_dir / "status.json"
        value = json.loads(status_path.read_text(encoding="utf-8"))
        value["message"] = "FileNotFoundError: /root/fwi-data/private/model.npy"
        status_path.write_text(json.dumps(value), encoding="utf-8")
        public = _plain(self.adapter.status(handle))
        self.assertEqual(public["message"], "FWI Worker reported a failure")
        self.assertNotIn("/root/", json.dumps(public))

        status_path.unlink()
        first = _plain(self.adapter.cancel(handle))
        second = _plain(self.adapter.cancel(handle))
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "Unsupported")

    def test_private_submission_integrity_fails_closed(self) -> None:
        handle, _ = self.submit_and_run_dir(
            task_id="task-record-integrity",
            idempotency_key="task-record-integrity:invert:0001",
        )
        record_path = self.submission_record_path(handle)
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["fingerprint"]["environment"]["environment_lock_hash"] = (
            "sha256:" + "f" * 64
        )
        record_path.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaises(RuntimeError) as raised:
            self.adapter.status(handle)
        self.assertEqual(
            getattr(raised.exception, "code", None), "ADAPTER_SUBMISSION_INVALID"
        )

    def test_collect_rejects_semantically_inconsistent_outputs(self) -> None:
        metric_mutations = {
            "iterations_string": ("iterations", "bad"),
            "nan_count_string": ("nan_count", "bad"),
            "device_path": ("device", "/private/device"),
            "loss_mismatch": ("initial_loss", 123.0),
            "optimizer_mismatch": ("optimizer", "sgd"),
            "learning_rate_mismatch": ("learning_rate", 2.0),
            "learning_rate_boolean": ("learning_rate", True),
            "gradient_clip_mismatch": ("gradient_clip_quantile", 0.95),
            "gradient_clip_boolean": ("gradient_clip_quantile", True),
        }
        for index, (label, (field, replacement)) in enumerate(
            metric_mutations.items(), start=1
        ):
            with self.subTest(label=label):
                handle, run_dir = self.submit_and_run_dir(
                    task_id=f"task-metrics-{index}",
                    idempotency_key=f"task-metrics-{index}:invert:0001",
                )
                self.write_success_artifacts(run_dir)
                metrics_path = run_dir / "metrics.json"
                metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
                metrics[field] = replacement
                metrics_path.write_text(json.dumps(metrics), encoding="utf-8")
                manifest_path = run_dir / "manifest.json"
                legacy = json.loads(manifest_path.read_text(encoding="utf-8"))
                legacy["metrics"] = metrics
                manifest_path.write_text(json.dumps(legacy), encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, "ADAPTER_ARTIFACT_INVALID"):
                    self.adapter.collect(handle)

        handle, run_dir = self.submit_and_run_dir(
            task_id="task-npy-header",
            idempotency_key="task-npy-header:invert:0001",
        )
        self.write_success_artifacts(run_dir)
        malicious = io.BytesIO()
        np.lib.format.write_array_header_1_0(
            malicious,
            {
                "descr": np.dtype(np.float32).str,
                "fortran_order": False,
                "shape": (2**30, 2**30),
            },
        )
        (run_dir / "models" / "inverted.npy").write_bytes(malicious.getvalue())
        with self.assertRaisesRegex(RuntimeError, "ADAPTER_ARTIFACT_INVALID"):
            self.adapter.collect(handle)

        handle, run_dir = self.submit_and_run_dir(
            task_id="task-frequency-drift",
            idempotency_key="task-frequency-drift:invert:0001",
        )
        self.write_success_artifacts(run_dir)
        (run_dir / "loss.csv").write_text(
            "iteration,frequency_hz,loss\n"
            "0,1,1\n"
            "1,999,0.75\n"
            "2,3.14159,0.5\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(RuntimeError, "ADAPTER_ARTIFACT_INVALID"):
            self.adapter.collect(handle)

        handle, run_dir = self.submit_and_run_dir(
            task_id="task-config-drift",
            idempotency_key="task-config-drift:invert:0001",
        )
        self.write_success_artifacts(run_dir)
        config_path = run_dir / "config.original.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["seed"] = 999
        config_path.write_text(json.dumps(config), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "ADAPTER_ARTIFACT_INVALID"):
            self.adapter.collect(handle)

    def test_collect_recomputes_eight_safe_schema_valid_standard_artifacts(self) -> None:
        handle, run_dir = self.submit_and_run_dir()
        expected_paths = self.write_success_artifacts(run_dir)
        first = _plain(self.adapter.collect(handle))
        second = _plain(self.adapter.collect(handle))
        self.assertEqual(first, second)
        manifests = _artifacts(first)
        self.assertEqual(len(manifests), 8)
        self.assertEqual([value["display"]["order"] for value in manifests], list(range(8)))

        by_port: dict[str, dict[str, Any]] = {}
        for manifest in manifests:
            self.assertEqual(
                schema_errors("artifact-manifest.schema.json", manifest), [], manifest
            )
            port = manifest["extensions"]["org.agent_rpc.adapter"]["output_port"]
            self.assertNotIn(port, by_port)
            by_port[port] = manifest
            encoded = json.dumps(manifest, sort_keys=True)
            self.assertNotIn(str(self.run_root), encoded)
            self.assertNotIn("/private/untrusted", encoded)
            self.assertNotIn("/untrusted/", encoded)
            self.assertNotIn("untrusted ", encoded)
            location = manifest["location"]
            location_value = location.get("relative_path", location.get("url"))
            self.assertIsInstance(location_value, str)
            self.assertNotIn("..", Path(location_value).parts)
            self.assertEqual(manifest["lineage"]["plan_hash"], PLAN_HASH)
            self.assertEqual(
                manifest["fingerprint"]["provenance_mode"], "development"
            )
            self.assertEqual(manifest["metrics"]["optimizer"], "adam")
            self.assertEqual(manifest["metrics"]["learning_rate"], 10.0)
            self.assertEqual(
                manifest["metrics"]["gradient_clip_quantile"], 0.98
            )
            self.assertNotIn("gradient_clip_values", manifest["metrics"])

        self.assertEqual(set(by_port), set(expected_paths))
        figure_sizes = {
            "true_model_figure": (1440, 608),
            "initial_model_figure": (1440, 608),
            "inverted_model_figure": (1440, 608),
            "model_error_figure": (1440, 608),
            "shot_gathers_figure": (2160, 800),
            "loss_curve_figure": (1120, 720),
        }
        for port, path in expected_paths.items():
            with self.subTest(port=port):
                manifest = by_port[port]
                self.assertEqual(
                    manifest["content_hash"],
                    "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
                )
                self.assertEqual(manifest["size_bytes"], path.stat().st_size)
                if port in figure_sizes:
                    self.assertEqual(manifest["artifact_type"], "figure")
                    self.assertEqual(manifest["media_type"], "image/png")
                    self.assertEqual(manifest["display"]["component"], "image")
                    width_px, height_px = figure_sizes[port]
                    figure = manifest["extensions"]["org.agent_rpc.figure"]
                    self.assertEqual(
                        (figure["width_px"], figure["height_px"]),
                        (width_px, height_px),
                    )
                    self.assertRegex(figure["figure_id"], r"^[a-z][a-z0-9_]+$")
                returned, data = self.adapter.read_artifact(
                    handle, manifest["artifact_id"]
                )
                self.assertEqual(returned, manifest)
                self.assertEqual(data, path.read_bytes())

    def test_collect_rejects_invalid_or_unbounded_png_outputs(self) -> None:
        cases = ("missing", "truncated", "wrong_dimensions", "wrong_mode", "oversized")
        for index, corruption in enumerate(cases, start=1):
            with self.subTest(corruption=corruption):
                handle, run_dir = self.submit_and_run_dir(
                    task_id=f"task-png-{index}",
                    idempotency_key=f"task-png-{index}:invert:0001",
                )
                self.write_success_artifacts(run_dir)
                target = run_dir / "figures" / "true_model.png"
                if corruption == "missing":
                    target.unlink()
                elif corruption == "truncated":
                    target.write_bytes(target.read_bytes()[:64])
                elif corruption == "wrong_dimensions":
                    Image.new("RGBA", (1, 1), (0, 0, 0, 255)).save(
                        target, format="PNG"
                    )
                elif corruption == "wrong_mode":
                    Image.new("RGB", (1440, 608), (0, 0, 0)).save(
                        target, format="PNG"
                    )
                else:
                    target.write_bytes(
                        b"\x89PNG\r\n\x1a\n"
                        + b"x" * (fwi_adapter_module.MAX_PNG_BYTES + 1)
                    )
                with self.assertRaisesRegex(RuntimeError, "ADAPTER_ARTIFACT_INVALID"):
                    self.adapter.collect(handle)

    def test_collect_rejects_primary_and_png_artifact_symlinks(self) -> None:
        for index, relative_path in enumerate(
            (
                Path("models/inverted.npy"),
                Path("loss.csv"),
                Path("figures/true_model.png"),
            ),
            start=1,
        ):
            with self.subTest(relative_path=str(relative_path)):
                handle, run_dir = self.submit_and_run_dir(
                    task_id=f"task-symlink-{index}",
                    idempotency_key=f"task-symlink-{index}:invert:0001",
                )
                self.write_success_artifacts(run_dir)
                target = run_dir / relative_path
                outside = self.base / f"outside-artifact-{index}"
                outside.write_bytes(target.read_bytes())
                target.unlink()
                target.symlink_to(outside)
                with self.assertRaises((ValueError, RuntimeError, OSError)):
                    self.adapter.collect(handle)

        handle, run_dir = self.submit_and_run_dir(
            task_id="task-artifact-fifo",
            idempotency_key="task-artifact-fifo:invert:0001",
        )
        self.write_success_artifacts(run_dir)
        fifo = run_dir / "models" / "inverted.npy"
        fifo.unlink()
        os.mkfifo(fifo, mode=0o600)
        started = time.monotonic()
        with self.assertRaises((ValueError, RuntimeError, OSError)):
            self.adapter.collect(handle)
        self.assertLess(time.monotonic() - started, 1.0)

        handle, run_dir = self.submit_and_run_dir(
            task_id="task-png-fifo",
            idempotency_key="task-png-fifo:invert:0001",
        )
        self.write_success_artifacts(run_dir)
        fifo = run_dir / "figures" / "true_model.png"
        fifo.unlink()
        os.mkfifo(fifo, mode=0o600)
        started = time.monotonic()
        with self.assertRaises((ValueError, RuntimeError, OSError)):
            self.adapter.collect(handle)
        self.assertLess(time.monotonic() - started, 1.0)

    def test_manifest_binding_run_root_and_safe_launcher_fail_closed(self) -> None:
        drifted = copy.deepcopy(self.adapter._manifest)
        drifted["outputs"][0]["port"] = "renamed_output"
        with patch(
            "scientific_runtime.fwi_adapter.load_deepwave_manifest",
            return_value=drifted,
        ), self.assertRaisesRegex(RuntimeError, "ADAPTER_MANIFEST_MISMATCH"):
            self.make_adapter()

        unsafe_root = self.base / "unsafe-runs"
        unsafe_root.mkdir(mode=0o777)
        unsafe_root.chmod(0o777)
        with self.assertRaisesRegex((ValueError, RuntimeError), "RUN_ROOT_INVALID"):
            self.make_adapter(run_root=unsafe_root)

        with self.assertRaisesRegex(RuntimeError, "WORKER_RUNTIME_MISMATCH"):
            DeepwaveAdapter(
                run_root=self.run_root,
                launcher=SafeSubprocessWorkerLauncher(
                    python_executable=Path("/usr/bin/python3")
                ),
                dataset_identity_provider=self.dataset_provider,
                registry_snapshot_provider=self.registry_provider,
                device_validator=self.device_validator,
                fingerprint_factory=self.fingerprint_factory,
                clock=lambda: NOW,
            )

        launcher_root = self.base / "launcher-runs"
        launcher_root.mkdir(mode=0o700)
        run_dir = launcher_root / "fwi-20260715T060000Z-abcdef123456"
        run_dir.mkdir(mode=0o700)
        config_path = run_dir / "config.original.json"
        config_path.write_text("{}", encoding="utf-8")
        (run_dir / "status.json").write_text(
            json.dumps(
                {
                    "job_id": run_dir.name,
                    "status": "queued",
                    "stage": "queued",
                    "iteration": 0,
                    "total_iterations": 1,
                    "message": "queued",
                    "updated_at": NOW,
                }
            ),
            encoding="utf-8",
        )
        launch_binding = LaunchAttemptBinding(
            submission_id="submission-" + "1" * 64,
            attempt_id="attempt-" + "2" * 32,
            attempt_number=1,
            job_id=run_dir.name,
            request_hash="sha256:" + "3" * 64,
            created_at=NOW,
        )
        stage_launch_attempt(launcher_root, run_dir, launch_binding)

        allow_exit = threading.Event()
        reaped = threading.Event()

        class SyntheticProcess:
            pid = 4321

            def wait(self) -> int:
                allow_exit.wait(1.0)
                reaped.set()
                return 0

            def poll(self) -> None:
                return None

            def terminate(self) -> None:
                return None

        launcher = SafeSubprocessWorkerLauncher(
            python_executable=Path("/usr/bin/python3")
        )
        with patch.dict(
            "os.environ",
            {
                "ADAPTER_TEST_SECRET": "must-not-propagate",
                "CUDA_VISIBLE_DEVICES": "0",
            },
            clear=True,
        ), patch(
            "scientific_runtime.fwi_adapter.subprocess.Popen",
            return_value=SyntheticProcess(),
        ) as popen, patch(
            "scientific_runtime.fwi_adapter.worker_attempt_started",
            return_value=True,
        ) as started:
            launcher.launch(
                command="invert",
                config_path=config_path,
                run_dir=run_dir,
                run_root=launcher_root,
            )
            argv = popen.call_args.args[0]
            options = popen.call_args.kwargs
            attempt_fd, capacity_fd = options["pass_fds"]
            self.assertEqual(
                argv,
                [
                    "/usr/bin/python3",
                    "-m",
                    "worker_launch_bootstrap",
                    "--command",
                    "invert",
                    "--config",
                    str(config_path),
                    "--run-dir",
                    str(run_dir),
                    "--run-root",
                    str(launcher_root),
                    "--wall-time-seconds",
                    "86400",
                    "--launch-attempt-id",
                    launch_binding.attempt_id,
                    "--launch-attempt-fd",
                    str(attempt_fd),
                    "--capacity-lease-fd",
                    str(capacity_fd),
                ],
            )
            self.assertIs(options["shell"], False)
            self.assertIs(options["close_fds"], True)
            self.assertEqual(len(options["pass_fds"]), 2)
            self.assertNotIn("ADAPTER_TEST_SECRET", options["env"])
            self.assertEqual(options["env"]["CUDA_VISIBLE_DEVICES"], "0")
            self.assertEqual(started.call_count, 1)
            allow_exit.set()
            self.assertTrue(reaped.wait(1.0))
            current = json.loads(
                (run_dir / "status.json").read_text(encoding="utf-8")
            )
        # Exit code zero without a terminal Worker document is ambiguous, not
        # an exact retryable worker_exit failure.
        self.assertEqual(current.get("stage"), "queued")
        current.update(
            {
                "status": "succeeded",
                "stage": "complete",
                "iteration": 1,
                "total_iterations": 1,
                "message": "synthetic success before nonzero exit",
            }
        )
        (run_dir / "status.json").write_text(
            json.dumps(current), encoding="utf-8"
        )
        SafeSubprocessWorkerLauncher._mark_unexpected_exit(run_dir, 3)
        corrected = json.loads(
            (run_dir / "status.json").read_text(encoding="utf-8")
        )
        self.assertEqual(corrected["status"], "succeeded")
        self.assertEqual(corrected["stage"], "complete")

    def test_run_root_swap_race_is_rejected_by_openat_boundary(self) -> None:
        race_root = self.base / "race-target"
        race_root.mkdir(mode=0o700)
        saved_root = self.base / "race-target-original"
        outside = self.base / "race-outside"
        outside.mkdir(mode=0o777)
        outside.chmod(0o777)
        real_open = fwi_adapter_module.os.open
        swapped = False

        def racing_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
            nonlocal swapped
            if (
                path == race_root.name
                and kwargs.get("dir_fd") is not None
                and not swapped
            ):
                swapped = True
                race_root.rename(saved_root)
                race_root.symlink_to(outside, target_is_directory=True)
            return real_open(path, flags, *args, **kwargs)

        with patch.object(
            fwi_adapter_module.os, "open", side_effect=racing_open
        ), self.assertRaisesRegex((ValueError, RuntimeError), "RUN_ROOT_INVALID"):
            fwi_adapter_module._validate_run_root(race_root, create=False)
        self.assertTrue(swapped)

    def test_launcher_timeout_stays_ambiguous_without_second_evidence_read(self) -> None:
        launcher_root = self.base / "ambiguous-launcher-runs"
        launcher_root.mkdir(mode=0o700)
        run_dir = launcher_root / "fwi-20260715T060001Z-abcdef123456"
        run_dir.mkdir(mode=0o700)
        config_path = run_dir / "config.original.json"
        config_path.write_text("{}", encoding="utf-8")
        (run_dir / "status.json").write_text(
            json.dumps(
                {
                    "job_id": run_dir.name,
                    "status": "queued",
                    "stage": "queued",
                    "iteration": 0,
                    "total_iterations": 1,
                    "message": "queued",
                    "updated_at": NOW,
                }
            ),
            encoding="utf-8",
        )
        binding = LaunchAttemptBinding(
            submission_id="submission-" + "4" * 64,
            attempt_id="attempt-" + "5" * 32,
            attempt_number=1,
            job_id=run_dir.name,
            request_hash="sha256:" + "6" * 64,
            created_at=NOW,
        )
        stage_launch_attempt(launcher_root, run_dir, binding)
        allow_exit = threading.Event()

        class SyntheticProcess:
            pid = 5432
            terminated = False

            def wait(self) -> int:
                allow_exit.wait(1.0)
                return 0

            def poll(self) -> None:
                return None

            def terminate(self) -> None:
                self.terminated = True

        process = SyntheticProcess()
        launcher = SafeSubprocessWorkerLauncher(
            python_executable=fwi_adapter_module.DEFAULT_WORKER_PYTHON,
            start_timeout_seconds=0.01,
        )
        with patch(
            "scientific_runtime.fwi_adapter.subprocess.Popen",
            return_value=process,
        ), patch(
            "scientific_runtime.fwi_adapter.worker_attempt_started",
            return_value=False,
        ) as started, patch(
            "scientific_runtime.fwi_adapter.time.monotonic",
            side_effect=(0.0, 1.0),
        ), self.assertRaisesRegex(
            RuntimeError, "SUBMISSION_LAUNCH_PENDING"
        ):
            launcher.launch(
                command="invert",
                config_path=config_path,
                run_dir=run_dir,
                run_root=launcher_root,
            )
        self.assertEqual(started.call_count, 1)
        self.assertFalse(process.terminated)
        allow_exit.set()
        for _ in range(100):
            with SafeSubprocessWorkerLauncher._state_lock:
                if SafeSubprocessWorkerLauncher._process_active == 0:
                    break
            time.sleep(0.005)
        self.assertEqual(SafeSubprocessWorkerLauncher._process_active, 0)

    def test_ready_attempt_with_reaper_start_failure_is_not_retryable(self) -> None:
        launcher = SafeSubprocessWorkerLauncher(
            python_executable=fwi_adapter_module.DEFAULT_WORKER_PYTHON
        )
        adapter = self.make_adapter(launcher=launcher)
        request = self.submit_kwargs(
            task_id="task-ready-reaper-failure",
            idempotency_key="task-ready-reaper-failure:invert:0001",
        )
        inherited_fds: tuple[int, int] | None = None
        worker_run_root: Path | None = None
        worker_run_dir: Path | None = None
        worker_attempt_id: str | None = None
        heartbeat: WorkerHeartbeat | None = None

        class SyntheticProcess:
            pid = os.getpid()
            terminated = False

            def poll(self) -> int | None:
                return -15 if self.terminated else None

            def terminate(self) -> None:
                nonlocal heartbeat
                self.terminated = True
                if heartbeat is not None:
                    heartbeat.stop("stopped")

            def wait(self, timeout: float | None = None) -> int:
                if not self.terminated:
                    raise AssertionError("synthetic process was not terminated")
                return -15

            def kill(self) -> None:
                self.terminate()

        process = SyntheticProcess()

        def synthetic_popen(arguments: list[str], **options: Any) -> SyntheticProcess:
            nonlocal inherited_fds, worker_run_root, worker_run_dir
            nonlocal worker_attempt_id
            inherited_fds = tuple(
                os.dup(descriptor) for descriptor in options["pass_fds"]
            )
            worker_run_root = Path(
                arguments[arguments.index("--run-root") + 1]
            )
            worker_run_dir = Path(
                arguments[arguments.index("--run-dir") + 1]
            )
            worker_attempt_id = arguments[
                arguments.index("--launch-attempt-id") + 1
            ]
            return process

        def publish_ready(*_arguments: Any, **_options: Any) -> bool:
            nonlocal heartbeat
            assert inherited_fds is not None
            assert worker_run_root is not None
            assert worker_run_dir is not None
            assert worker_attempt_id is not None
            if heartbeat is None:
                heartbeat = WorkerHeartbeat(
                    run_root=worker_run_root,
                    run_dir=worker_run_dir,
                    attempt_id=worker_attempt_id,
                    attempt_fd=inherited_fds[0],
                    capacity_fd=inherited_fds[1],
                    interval_seconds=10.0,
                )
                heartbeat.start()
            return True

        class FailingReaperThread:
            def __init__(self, **_options: Any) -> None:
                pass

            def start(self) -> None:
                raise RuntimeError("synthetic reaper start failure")

        class ReaperThreading:
            Thread = FailingReaperThread

        try:
            with patch(
                "scientific_runtime.fwi_adapter.subprocess.Popen",
                side_effect=synthetic_popen,
            ), patch(
                "scientific_runtime.fwi_adapter.worker_attempt_started",
                side_effect=publish_ready,
            ), patch.object(
                fwi_adapter_module, "threading", ReaperThreading
            ), self.assertRaises(AdapterUnavailable) as unavailable:
                adapter.submit(**copy.deepcopy(request))
            self.assertEqual(
                unavailable.exception.code,
                "SUBMISSION_RECONCILIATION_REQUIRED",
            )
            self.assertTrue(process.terminated)

            record_path = next(
                (
                    self.run_root
                    / fwi_adapter_module.CONTROL_DIRECTORY
                    / "submissions"
                ).glob("*.json")
            )
            record = adapter._read_submission(record_path)
            self.assertEqual(record["launch_state"], "launching")
            self.assertNotIn("launch_failure", record)
            assert worker_run_dir is not None
            self.assertTrue((worker_run_dir / ".worker-ready.json").is_file())
            with self.assertRaises(AdapterUnavailable) as unsupported:
                adapter.probe_pre_running_retry(**copy.deepcopy(request))
            self.assertEqual(
                unsupported.exception.code, "WORKER_RETRY_UNSUPPORTED"
            )
        finally:
            if heartbeat is not None and not process.terminated:
                heartbeat.stop("stopped")
        self.assertEqual(SafeSubprocessWorkerLauncher._process_active, 0)

    def test_launcher_releases_local_slot_when_abort_cleanup_fails(self) -> None:
        launcher_root = self.base / "abort-failure-runs"
        launcher_root.mkdir(mode=0o700)
        run_dir = launcher_root / "fwi-20260715T060002Z-abcdef123456"
        run_dir.mkdir(mode=0o700)
        config_path = run_dir / "config.original.json"
        config_path.write_text("{}", encoding="utf-8")

        class FailingAbortLease:
            child_arguments: list[str] = []
            pass_fds: tuple[int, ...] = ()

            def abort(self) -> None:
                raise RuntimeError("synthetic abort failure")

        launcher = SafeSubprocessWorkerLauncher(
            python_executable=Path("/usr/bin/python3")
        )
        with patch(
            "scientific_runtime.fwi_adapter.ParentLaunchLease.acquire",
            return_value=FailingAbortLease(),
        ), patch(
            "scientific_runtime.fwi_adapter.subprocess.Popen",
            side_effect=OSError("synthetic Popen failure"),
        ), self.assertRaises(AdapterUnavailable) as stopped:
            launcher.launch(
                command="invert",
                config_path=config_path,
                run_dir=run_dir,
                run_root=launcher_root,
            )
        self.assertEqual(stopped.exception.code, "WORKER_LAUNCH_FAILED")
        self.assertEqual(SafeSubprocessWorkerLauncher._process_active, 0)

    def test_adapter_timeout_keeps_launching_record_and_queued_status(self) -> None:
        allow_exit = threading.Event()

        class SyntheticProcess:
            pid = 6543

            def wait(self) -> int:
                allow_exit.wait(1.0)
                return 0

            def poll(self) -> None:
                return None

            def terminate(self) -> None:
                raise AssertionError("ambiguous timeout must not terminate child")

        launcher = SafeSubprocessWorkerLauncher(
            python_executable=fwi_adapter_module.DEFAULT_WORKER_PYTHON,
            start_timeout_seconds=0.01,
        )
        adapter = self.make_adapter(launcher=launcher)
        request = self.submit_kwargs(
            task_id="task-launch-pending",
            idempotency_key="task-launch-pending:invert:0001",
        )
        with patch(
            "scientific_runtime.fwi_adapter.subprocess.Popen",
            return_value=SyntheticProcess(),
        ), patch(
            "scientific_runtime.fwi_adapter.worker_attempt_started",
            return_value=False,
        ) as started, patch(
            "scientific_runtime.fwi_adapter.time.monotonic",
            side_effect=(0.0, 1.0),
        ), self.assertRaisesRegex(
            RuntimeError, "SUBMISSION_LAUNCH_PENDING"
        ):
            adapter.submit(**copy.deepcopy(request))
        self.assertEqual(started.call_count, 1)

        record_path = next(
            (
                self.run_root
                / fwi_adapter_module.CONTROL_DIRECTORY
                / "submissions"
            ).glob("*.json")
        )
        record = json.loads(record_path.read_text(encoding="utf-8"))
        self.assertEqual(record["launch_state"], "launching")
        run_dir = self.run_root / record["job_id"]
        status = json.loads(
            (run_dir / "status.json").read_text(encoding="utf-8")
        )
        self.assertEqual(status["status"], "queued")
        self.assertEqual(status["stage"], "queued")

        allow_exit.set()
        for _ in range(100):
            with SafeSubprocessWorkerLauncher._state_lock:
                if SafeSubprocessWorkerLauncher._process_active == 0:
                    break
            time.sleep(0.005)
        self.assertEqual(SafeSubprocessWorkerLauncher._process_active, 0)
        with patch(
            "scientific_runtime.fwi_adapter.subprocess.Popen"
        ) as replacement, self.assertRaisesRegex(
            RuntimeError, "SUBMISSION_LAUNCH_PENDING"
        ):
            adapter.submit(**copy.deepcopy(request))
        replacement.assert_not_called()

    def test_default_fixed_venv_probe_is_path_free_and_read_only(self) -> None:
        before = self.root_snapshot()
        dataset = fwi_adapter_module._default_dataset_identity_provider()
        self.assertEqual(schema_errors("dataset-ref.schema.json", dataset), [])
        self.assertNotIn("/root/fwi-data", json.dumps(dataset, sort_keys=True))
        adapter = DeepwaveAdapter(
            run_root=self.run_root,
            registry_snapshot_provider=DatasetIdentityProvider(dataset),
        )
        validated = adapter.validate(
            project_id="adapter-validation",
            principal_id="adapter-validation",
            algorithm=algorithm_identity(),
            dataset=dataset,
            task_type="acoustic_fwi_2d",
            parameters={
                "preset": "fwi_smoke",
                "device": "cpu",
                "iterations": 1,
                "seed": 2026,
                "optimizer": "adam",
                "learning_rate_milli": 10_000,
            },
            resources={
                "device": "cpu",
                "gpu_count": 0,
                "cpu_cores": 1,
                "memory_mb": 1024,
                "wall_time_seconds": 1800,
            },
        )
        self.assertRegex(
            validated.device_details["development_environment_snapshot_hash"],
            r"^sha256:[0-9a-f]{64}$",
        )
        self.assertEqual(self.root_snapshot(), before)


if __name__ == "__main__":
    unittest.main()
