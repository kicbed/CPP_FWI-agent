from __future__ import annotations

import base64
import copy
import dataclasses
import hashlib
import io
import json
import multiprocessing
import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np
import scientific_runtime.fwi_adapter as fwi_adapter_module

from scientific_runtime.fwi_adapter import (
    DeepwaveAdapter,
    SafeSubprocessWorkerLauncher,
)
from scientific_runtime_contracts import schema_errors


NOW = "2026-07-15T06:00:00Z"
HASH_DATASET = "sha256:" + "a" * 64
HASH_ENVIRONMENT = "sha256:" + "b" * 64
HASH_CONFIG = "sha256:" + "c" * 64
PLAN_HASH = "sha256:" + "d" * 64


def algorithm_identity() -> dict[str, Any]:
    return {"id": "deepwave.acoustic_fwi", "version": "1.0.0"}


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
        "adapter_version": "1.0.0",
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
    ) -> int:
        call = {
            "command": command,
            "config_path": Path(config_path),
            "run_dir": Path(run_dir),
            "run_root": Path(run_root),
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
    ) -> DeepwaveAdapter:
        return DeepwaveAdapter(
            run_root=run_root or self.run_root,
            launcher=launcher or self.launcher,
            dataset_identity_provider=self.dataset_provider,
            registry_snapshot_provider=self.registry_provider,
            device_validator=self.device_validator,
            fingerprint_factory=self.fingerprint_factory,
            clock=lambda: NOW,
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
            # These real legacy shapes are deliberately non-scalar.  The
            # standard ArtifactManifest metrics field must not copy them.
            "model_shape": [94, 288],
            "gradient_clip_values": [0.1, 0.05],
        }
        (run_dir / "metrics.json").write_text(
            json.dumps(metrics), encoding="utf-8"
        )

        png_path = figures / "true_model.png"
        png_path.write_bytes(
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0l"
                "EQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
            )
        )
        config = json.loads(
            (run_dir / "config.original.json").read_text(encoding="utf-8")
        )
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
            "figures": [
                {
                    "id": "true_model",
                    "title": "True model",
                    # This is the unsafe legacy field collect must ignore.
                    "path": "/private/untrusted/true_model.png",
                    "url": (
                        f"/fwi-artifacts/{config['job_id']}"
                        "/figures/true_model.png"
                    ),
                    "mime_type": "image/png",
                }
            ],
        }
        (run_dir / "manifest.json").write_text(
            json.dumps(legacy_manifest), encoding="utf-8"
        )
        self.write_status(run_dir, "succeeded")
        return {
            "inverted_velocity_model_2d": inverted_path,
            "loss_curve": loss_path,
        }

    def test_validate_and_estimate_are_strict_and_side_effect_free(self) -> None:
        before = self.root_snapshot()
        valid = self.execution_kwargs()
        self.adapter.validate(**copy.deepcopy(valid))
        demo_cuda = copy.deepcopy(valid)
        demo_cuda["parameters"].update(
            {"preset": "fwi_demo", "device": "cuda", "iterations": 100}
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

        for value in (0, 101, "2", 2.0, True):
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
        encoded_handle = json.dumps(handle_document, sort_keys=True)
        self.assertNotIn(str(self.run_root), encoded_handle)
        self.assertNotIn("run_dir", handle_document)
        self.assertNotIn("config_path", handle_document)
        self.assertEqual(_status_name(self.adapter.status(handle)), "queued")
        self.assertEqual(run_dir, call["run_dir"])

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

    def test_incomplete_submission_requires_reconciliation_without_relaunch(self) -> None:
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
                with self.assertRaisesRegex(
                    RuntimeError, "SUBMISSION_RECONCILIATION_REQUIRED"
                ):
                    reopened.submit(**copy.deepcopy(request))
                self.assertEqual(replacement_launcher.calls, [])

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

    def test_cancel_is_a_stable_p1_noop(self) -> None:
        handle, _ = self.submit_and_run_dir()
        first = _plain(self.adapter.cancel(handle))
        second = _plain(self.adapter.cancel(handle))
        self.assertEqual(first, second)
        encoded = json.dumps(first, sort_keys=True)
        self.assertIn("CANCEL_NOT_SUPPORTED", encoded)
        self.assertEqual(_status_name(self.adapter.status(handle)), "queued")
        self.assertEqual(len(self.launcher.calls), 1)

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

    def test_collect_recomputes_safe_schema_valid_primary_artifacts(self) -> None:
        handle, run_dir = self.submit_and_run_dir()
        expected_paths = self.write_success_artifacts(run_dir)
        first = _plain(self.adapter.collect(handle))
        second = _plain(self.adapter.collect(handle))
        self.assertEqual(first, second)
        manifests = _artifacts(first)

        by_type: dict[str, list[dict[str, Any]]] = {}
        for manifest in manifests:
            self.assertEqual(
                schema_errors("artifact-manifest.schema.json", manifest), [], manifest
            )
            by_type.setdefault(manifest["artifact_type"], []).append(manifest)
            encoded = json.dumps(manifest, sort_keys=True)
            self.assertNotIn(str(self.run_root), encoded)
            self.assertNotIn("/private/untrusted", encoded)
            location = manifest["location"]
            location_value = location.get("relative_path", location.get("url"))
            self.assertIsInstance(location_value, str)
            self.assertNotIn("..", Path(location_value).parts)
            self.assertEqual(manifest["lineage"]["plan_hash"], PLAN_HASH)
            self.assertEqual(
                manifest["fingerprint"]["provenance_mode"], "development"
            )

        for artifact_type, path in expected_paths.items():
            with self.subTest(artifact_type=artifact_type):
                self.assertEqual(len(by_type.get(artifact_type, [])), 1)
                manifest = by_type[artifact_type][0]
                self.assertEqual(
                    manifest["content_hash"],
                    "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
                )
                self.assertEqual(manifest["size_bytes"], path.stat().st_size)

    def test_collect_rejects_primary_artifact_symlinks(self) -> None:
        for index, relative_path in enumerate(
            (Path("models/inverted.npy"), Path("loss.csv")), start=1
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

        class SyntheticProcess:
            pid = 4321

            def wait(self) -> int:
                return 0

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
        ) as popen:
            launcher.launch(
                command="invert",
                config_path=config_path,
                run_dir=run_dir,
                run_root=launcher_root,
            )
        argv = popen.call_args.args[0]
        options = popen.call_args.kwargs
        self.assertEqual(
            argv,
            [
                "/usr/bin/python3",
                "-m",
                "fwi_worker",
                "invert",
                "--config",
                str(config_path),
                "--run-dir",
                str(run_dir),
            ],
        )
        self.assertIs(options["shell"], False)
        self.assertNotIn("ADAPTER_TEST_SECRET", options["env"])
        self.assertEqual(options["env"]["CUDA_VISIBLE_DEVICES"], "0")
        for _ in range(100):
            current = json.loads(
                (run_dir / "status.json").read_text(encoding="utf-8")
            )
            if current.get("stage") == "worker_exit":
                break
            time.sleep(0.005)
        self.assertEqual(current.get("stage"), "worker_exit")
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
        self.assertEqual(corrected["status"], "failed")
        self.assertEqual(corrected["stage"], "worker_exit")

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
