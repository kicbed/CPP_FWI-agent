from __future__ import annotations

import copy
import dataclasses
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from unittest.mock import patch

from scientific_runtime.fwi_adapter import (
    AdapterUnavailable,
    DeepwaveAdapter,
)
from scientific_runtime.task_dispatcher import DeepwaveTaskDispatcher, DispatchError
from scientific_runtime.task_store import DispatchIntentSnapshot
from worker_launch_control import (
    ParentLaunchLease,
    WorkerHeartbeat,
    binding_from_submission_record,
)


NOW = "2026-07-15T13:00:00Z"
PLAN_HASH = "sha256:" + "d" * 64
DATASET_HASH = "sha256:" + "a" * 64
ENVIRONMENT_HASH = "sha256:" + "b" * 64


def algorithm() -> dict[str, str]:
    return {"id": "deepwave.acoustic_fwi", "version": "1.5.0"}


def dataset() -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "id": "marmousi_94_288",
        "version": "1.0.0",
        "content_hash": DATASET_HASH,
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


class IdentityProvider:
    def __init__(self, value: dict[str, Any]) -> None:
        self.value = copy.deepcopy(value)

    def __call__(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return copy.deepcopy(self.value)


class DeviceValidator:
    def __call__(self, device: str) -> dict[str, Any]:
        return {
            "device": device,
            "device_name": "synthetic-test-cpu",
            "compute_capability": None,
        }


class FingerprintFactory:
    def __call__(self, *_args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "provenance_mode": "development",
            "algorithm": algorithm(),
            "adapter_version": "1.5.0",
            "source": {"identity_complete": False, "dirty": None},
            "environment": {"environment_lock_hash": ENVIRONMENT_HASH},
            "runtime": {
                "python": "test-python",
                "pytorch": "test-pytorch",
                "deepwave": "test-deepwave",
                "cuda": None,
            },
            "seed": kwargs["seed"],
            "hardware": {
                "device": kwargs["device"],
                "device_name": "synthetic-test-cpu",
                "compute_capability": None,
            },
            "normalized_config_hash": kwargs["normalized_config_hash"],
            "input_hashes": list(kwargs["input_hashes"]),
            "determinism": {
                "requested": False,
                "framework_deterministic": False,
                "flags": {},
                "known_nondeterminism": ["synthetic test runtime"],
            },
        }


class FakeLauncher:
    def __init__(self) -> None:
        self.calls: list[dict[str, Path]] = []
        self.lock = threading.Lock()

    def launch(self, **kwargs: Any) -> int:
        with self.lock:
            self.calls.append(
                {
                    "run_dir": Path(kwargs["run_dir"]),
                    "config_path": Path(kwargs["config_path"]),
                }
            )
        return 4242


class ScientificRuntimeFWIPurgeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.run_root = self.base / "runs"
        self.run_root.mkdir(mode=0o700)
        self.launcher = FakeLauncher()
        identity = dataset()
        self.adapter = DeepwaveAdapter(
            run_root=self.run_root,
            launcher=self.launcher,
            dataset_identity_provider=IdentityProvider(identity),
            registry_snapshot_provider=IdentityProvider(identity),
            device_validator=DeviceValidator(),
            fingerprint_factory=FingerprintFactory(),
            clock=lambda: NOW,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def request(self, task_id: str = "task-purge-1") -> dict[str, Any]:
        return {
            "task_id": task_id,
            "node_id": "invert",
            "plan_hash": PLAN_HASH,
            "idempotency_key": f"{task_id}:invert:0001",
            "project_id": "project-1",
            "principal_id": "user-1",
            "algorithm": algorithm(),
            "dataset": dataset(),
            "task_type": "acoustic_fwi_2d",
            "parameters": {
                "preset": "fwi_smoke",
                "device": "cpu",
                "iterations": 2,
                "seed": 2026,
                "optimizer": "adam",
                "learning_rate_milli": 10_000,
            },
            "resources": {
                "device": "cpu",
                "gpu_count": 0,
                "cpu_cores": 4,
                "memory_mb": 8192,
                "wall_time_seconds": 1800,
            },
        }

    def submit(
        self,
        task_id: str = "task-purge-1",
        *,
        initialize_fence: bool = True,
    ) -> tuple[Any, Path]:
        handle = self.adapter.submit(**self.request(task_id))
        run_dir = self.launcher.calls[-1]["run_dir"]
        if initialize_fence:
            lease = ParentLaunchLease.acquire(
                self.run_root, run_dir, max_active=1
            )
            lease.abort()
        return handle, run_dir

    def record_path(self, handle: Any) -> Path:
        name = handle.submission_id.removeprefix("submission-") + ".json"
        return (
            self.run_root
            / ".scientific-runtime-adapter-v1"
            / "submissions"
            / name
        )

    def lock_path(self, handle: Any) -> Path:
        name = handle.submission_id.removeprefix("submission-") + ".json.lock"
        return (
            self.run_root
            / ".scientific-runtime-adapter-v1"
            / "locks"
            / name
        )

    def write_status(self, run_dir: Path, status: str) -> None:
        config = json.loads(
            (run_dir / "config.original.json").read_text(encoding="utf-8")
        )
        if status == "succeeded":
            stage, iteration = "complete", 2
        elif status == "failed":
            stage, iteration = "failed", 0
        elif status == "running":
            stage, iteration = "invert", 1
        else:
            stage, iteration = "queued", 0
        (run_dir / "status.json").write_text(
            json.dumps(
                {
                    "job_id": config["job_id"],
                    "status": status,
                    "stage": stage,
                    "iteration": iteration,
                    "total_iterations": 2,
                    "message": f"synthetic {status}",
                    "updated_at": NOW,
                }
            ),
            encoding="utf-8",
        )

    def make_terminal_tree(
        self, *, task_id: str = "task-purge-1", status: str = "succeeded"
    ) -> tuple[Any, Path]:
        handle, run_dir = self.submit(task_id)
        nested = run_dir / "nested" / "deeper"
        nested.mkdir(parents=True)
        (run_dir / "root-output.bin").write_bytes(b"root")
        (nested / "result.bin").write_bytes(b"result")
        self.write_status(run_dir, status)
        return handle, run_dir

    def intent(self, handle: Any, *, state: str = "dispatched") -> DispatchIntentSnapshot:
        request = self.request(handle.task_id)
        request["normalized_config_hash"] = handle.fingerprint[
            "normalized_config_hash"
        ]
        return DispatchIntentSnapshot(
            intent_id="dispatch-purge-1",
            task_id=handle.task_id,
            plan_id="plan-purge-1",
            plan_hash=handle.plan_hash,
            approval_id="approval-purge-1",
            node_id=handle.node_id,
            node_idempotency_key=handle.idempotency_key,
            adapter_id="fwi.deepwave_adapter",
            adapter_version=handle.adapter_version,
            request=request,
            request_hash="sha256:" + "e" * 64,
            queue_fingerprint=copy.deepcopy(handle.fingerprint),
            state=state,
            handle=handle.as_dict() if state == "dispatched" else None,
            failure_code=None,
            created_at=NOW,
            dispatch_claimed_at=NOW,
            outcome_recorded_at=NOW if state == "dispatched" else None,
        )

    def test_terminal_purge_is_fd_relative_idempotent_and_keeps_control_state(self) -> None:
        handle, run_dir = self.make_terminal_tree()
        outside = self.base / "outside.txt"
        outside.write_text("keep", encoding="utf-8")
        (run_dir / "nested" / "outside-link").symlink_to(outside)

        first = self.adapter.purge(handle, purge_id="purge-operation-1").as_dict()
        self.assertEqual(
            first,
            {
                "task_id": handle.task_id,
                "purge_id": "purge-operation-1",
                "local_run_state": "deleted",
                "replayed": False,
            },
        )
        self.assertFalse(run_dir.exists())
        self.assertEqual(outside.read_text(encoding="utf-8"), "keep")
        record = json.loads(self.record_path(handle).read_text(encoding="utf-8"))
        self.assertEqual(record["launch_state"], "purged")
        self.assertEqual(record["purge_id"], "purge-operation-1")
        self.assertTrue(self.lock_path(handle).is_file())

        second = self.adapter.purge(handle, purge_id="purge-operation-1").as_dict()
        self.assertEqual(second, first | {"replayed": True})
        with self.assertRaisesRegex(RuntimeError, "PURGE_IDEMPOTENCY_CONFLICT"):
            self.adapter.purge(handle, purge_id="purge-operation-2")

    def test_current_v1_4_private_schema_v1_0_remains_purgeable(self) -> None:
        handle, run_dir = self.make_terminal_tree(
            task_id="task-purge-current-private-v1"
        )
        record_path = self.record_path(handle)
        record = json.loads(record_path.read_text(encoding="utf-8"))
        self.assertEqual(record["adapter_version"], "1.5.0")
        record["schema_version"] = "1.0.0"
        record.pop("launch_attempt")
        self.adapter._write_submission(record_path, record)
        result = self.adapter.purge(
            handle, purge_id="purge-current-private-v1"
        ).as_dict()
        self.assertEqual(result["local_run_state"], "deleted")
        self.assertFalse(run_dir.exists())

    def test_failed_is_terminal_but_queued_running_and_missing_are_rejected(self) -> None:
        failed, failed_dir = self.make_terminal_tree(
            task_id="task-purge-failed", status="failed"
        )
        self.adapter.purge(failed, purge_id="purge-failed")
        self.assertFalse(failed_dir.exists())

        for suffix, status in (("queued", "queued"), ("running", "running")):
            with self.subTest(status=status):
                handle, run_dir = self.submit(f"task-purge-{suffix}")
                self.write_status(run_dir, status)
                with self.assertRaisesRegex(
                    RuntimeError, "PURGE_REQUIRES_TERMINAL_STATUS"
                ):
                    self.adapter.purge(handle, purge_id=f"purge-{suffix}")
                self.assertTrue(run_dir.is_dir())
                record = json.loads(
                    self.record_path(handle).read_text(encoding="utf-8")
                )
                self.assertEqual(record["launch_state"], "launched")

        missing, missing_dir = self.submit("task-purge-missing")
        shutil.rmtree(missing_dir)
        with self.assertRaisesRegex(RuntimeError, "ADAPTER_STATUS_INVALID"):
            self.adapter.purge(missing, purge_id="purge-missing")
        record = json.loads(self.record_path(missing).read_text(encoding="utf-8"))
        self.assertEqual(record["launch_state"], "launched")

    def test_purging_tombstone_resumes_partial_or_missing_directory(self) -> None:
        handle, run_dir = self.make_terminal_tree(task_id="task-purge-resume")
        record_path = self.record_path(handle)
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record.update(launch_state="purging", purge_id="purge-resume")
        self.adapter._write_submission(record_path, record)
        (run_dir / "root-output.bin").unlink()

        result = self.adapter.purge(handle, purge_id="purge-resume").as_dict()
        self.assertTrue(result["replayed"])
        self.assertFalse(run_dir.exists())

        handle2, run_dir2 = self.make_terminal_tree(task_id="task-purge-gone")
        record_path2 = self.record_path(handle2)
        record2 = json.loads(record_path2.read_text(encoding="utf-8"))
        record2.update(launch_state="purging", purge_id="purge-gone")
        self.adapter._write_submission(record_path2, record2)
        shutil.rmtree(run_dir2)
        result2 = self.adapter.purge(handle2, purge_id="purge-gone").as_dict()
        self.assertTrue(result2["replayed"])
        self.assertEqual(
            json.loads(record_path2.read_text(encoding="utf-8"))["launch_state"],
            "purged",
        )

    def test_delete_then_receipt_write_failure_recovers_on_retry(self) -> None:
        handle, run_dir = self.make_terminal_tree(task_id="task-purge-crash")
        real_write = self.adapter._write_submission
        failed_once = False

        def fail_final_write(path: Path, record: dict[str, Any]) -> None:
            nonlocal failed_once
            if record.get("launch_state") == "purged" and not failed_once:
                failed_once = True
                raise AdapterUnavailable("ADAPTER_STATE_UNAVAILABLE: synthetic crash")
            real_write(path, record)

        with patch.object(self.adapter, "_write_submission", side_effect=fail_final_write):
            with self.assertRaisesRegex(RuntimeError, "ADAPTER_STATE_UNAVAILABLE"):
                self.adapter.purge(handle, purge_id="purge-crash")
        self.assertFalse(run_dir.exists())
        self.assertEqual(
            json.loads(self.record_path(handle).read_text(encoding="utf-8"))[
                "launch_state"
            ],
            "purging",
        )
        replay = self.adapter.purge(handle, purge_id="purge-crash").as_dict()
        self.assertTrue(replay["replayed"])

    def test_job_symlink_is_never_followed(self) -> None:
        handle, run_dir = self.make_terminal_tree(task_id="task-purge-symlink")
        record_path = self.record_path(handle)
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record.update(launch_state="purging", purge_id="purge-symlink")
        self.adapter._write_submission(record_path, record)
        saved = self.base / "saved-job"
        run_dir.rename(saved)
        outside = self.base / "outside-dir"
        outside.mkdir()
        sentinel = outside / "sentinel.txt"
        sentinel.write_text("keep", encoding="utf-8")
        run_dir.symlink_to(outside, target_is_directory=True)

        with self.assertRaisesRegex(RuntimeError, "PURGE_LOCAL_RUN_UNAVAILABLE"):
            self.adapter.purge(handle, purge_id="purge-symlink")
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
        self.assertTrue(run_dir.is_symlink())
        self.assertEqual(
            json.loads(record_path.read_text(encoding="utf-8"))["launch_state"],
            "purging",
        )

    def test_concurrent_purge_is_serialized_by_submission_lock(self) -> None:
        handle, run_dir = self.make_terminal_tree(task_id="task-purge-race")

        def purge(_index: int) -> dict[str, Any]:
            return self.adapter.purge(handle, purge_id="purge-race").as_dict()

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(purge, range(8)))
        self.assertFalse(run_dir.exists())
        self.assertEqual(sum(not item["replayed"] for item in results), 1)
        self.assertTrue(all(item["local_run_state"] == "deleted" for item in results))

    def test_purge_waits_for_terminal_worker_to_release_execution_fence(self) -> None:
        handle, run_dir = self.submit(
            "task-purge-active", initialize_fence=False
        )
        self.write_status(run_dir, "succeeded")
        record = json.loads(
            self.record_path(handle).read_text(encoding="utf-8")
        )
        binding = binding_from_submission_record(record)
        lease = ParentLaunchLease.acquire(
            self.run_root, run_dir, max_active=1
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
            with self.assertRaisesRegex(
                RuntimeError, "PURGE_WORKER_STILL_ACTIVE"
            ):
                self.adapter.purge(handle, purge_id="purge-active")
            self.assertTrue(run_dir.is_dir())
            current = json.loads(
                self.record_path(handle).read_text(encoding="utf-8")
            )
            self.assertEqual(current["launch_state"], "launched")
        finally:
            heartbeat.stop("succeeded")

        result = self.adapter.purge(
            handle, purge_id="purge-active"
        ).as_dict()
        self.assertEqual(result["local_run_state"], "deleted")
        time.sleep(0.05)
        self.assertFalse(run_dir.exists())

    def test_dispatcher_accepts_only_durable_bound_dispatched_intent(self) -> None:
        handle, run_dir = self.make_terminal_tree(task_id="task-purge-dispatch")
        dispatcher = DeepwaveTaskDispatcher(self.adapter)
        intent = self.intent(handle)
        result = dispatcher.purge(intent, purge_id="purge-dispatch")
        self.assertEqual(
            result,
            {
                "task_id": handle.task_id,
                "purge_id": "purge-dispatch",
                "local_run_state": "deleted",
                "replayed": False,
            },
        )
        self.assertFalse(run_dir.exists())
        self.assertNotIn("job_id", result)
        self.assertNotIn("path", json.dumps(result))

        pending = dataclasses.replace(intent, state="pending", handle=None)
        with self.assertRaises(DispatchError) as raised:
            dispatcher.purge(pending, purge_id="purge-pending")
        self.assertEqual(raised.exception.code, "DISPATCH_RECEIPT_UNAVAILABLE")

        forged_handle = copy.deepcopy(intent.handle)
        assert forged_handle is not None
        forged_handle["task_id"] = "task-other"
        forged = dataclasses.replace(intent, handle=forged_handle)
        with self.assertRaises(DispatchError) as raised:
            dispatcher.purge(forged, purge_id="purge-forged")
        self.assertEqual(raised.exception.code, "DISPATCH_RECEIPT_INVALID")

    def test_purge_surface_has_no_path_or_job_identifier_parameter(self) -> None:
        import inspect

        self.assertEqual(
            list(inspect.signature(DeepwaveAdapter.purge).parameters),
            ["self", "handle", "purge_id"],
        )
        self.assertEqual(
            list(inspect.signature(DeepwaveTaskDispatcher.purge).parameters),
            ["self", "intent", "purge_id"],
        )
        handle, _ = self.make_terminal_tree(task_id="task-purge-invalid-id")
        with self.assertRaisesRegex(RuntimeError, "PURGE_ID_INVALID"):
            self.adapter.purge(handle, purge_id="../outside")


if __name__ == "__main__":
    unittest.main()
