from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fwi_worker.__main__ import run_worker
from worker_launch_control import (
    WorkerCancellationRequested,
    WorkerWallTimeExceeded,
)


class WorkerFailureArtifactsTest(unittest.TestCase):
    def test_cooperative_cancel_bypasses_failure_artifact_path(self) -> None:
        config_path = Path(__file__).parent / "fixtures" / "homogeneous_cuda.json"

        def cancel() -> None:
            raise WorkerCancellationRequested(
                "cancel-worker-checkpoint-1", "user_requested"
            )

        with tempfile.TemporaryDirectory() as root, patch.dict(
            os.environ, {"FWI_RUN_ROOT": root}
        ):
            with self.assertRaises(WorkerCancellationRequested):
                run_worker(
                    "forward",
                    str(config_path),
                    None,
                    cancel_check=cancel,
                )
            jobs = list(Path(root).iterdir())
            self.assertEqual(len(jobs), 1)
            run_dir = jobs[0]
            self.assertFalse((run_dir / "metrics.json").exists())
            self.assertFalse((run_dir / "manifest.json").exists())
            status = json.loads((run_dir / "status.json").read_text())
            self.assertEqual(status["status"], "cancelled")
            self.assertEqual(status["stage"], "cancelled")

    def test_device_failure_writes_structured_partial_result(self) -> None:
        config_path = Path(__file__).parent / "fixtures" / "homogeneous_cuda.json"
        with tempfile.TemporaryDirectory() as root, patch.dict(
            os.environ, {"FWI_RUN_ROOT": root}
        ), patch(
            "fwi_worker.__main__.validate_device",
            side_effect=RuntimeError("simulated unavailable device"),
        ):
            with self.assertRaisesRegex(RuntimeError, "simulated unavailable device"):
                run_worker("forward", str(config_path), None)
            jobs = list(Path(root).iterdir())
            self.assertEqual(len(jobs), 1)
            run_dir = jobs[0]
            for name in (
                "config.original.json",
                "config.resolved.json",
                "environment.json",
                "status.json",
                "progress.jsonl",
                "run.log",
                "loss.csv",
                "metrics.json",
                "manifest.json",
            ):
                self.assertTrue((run_dir / name).is_file(), name)
            status = json.loads((run_dir / "status.json").read_text())
            metrics = json.loads((run_dir / "metrics.json").read_text())
            manifest = json.loads((run_dir / "manifest.json").read_text())
            self.assertEqual(status["status"], "failed")
            self.assertTrue(metrics["partial"])
            self.assertEqual(manifest["status"], "failed")
            self.assertIn("simulated unavailable device", manifest["failure_reason"])

    def test_cooperative_timeout_is_failed_without_failure_artifacts(self) -> None:
        config_path = Path(__file__).parent / "fixtures" / "homogeneous_cuda.json"

        def timeout() -> None:
            raise WorkerWallTimeExceeded(
                "timeout-worker-checkpoint-1", "wall_time_exceeded"
            )

        with tempfile.TemporaryDirectory() as root, patch.dict(
            os.environ, {"FWI_RUN_ROOT": root}
        ):
            with self.assertRaises(WorkerWallTimeExceeded):
                run_worker(
                    "forward",
                    str(config_path),
                    None,
                    cancel_check=timeout,
                )
            jobs = list(Path(root).iterdir())
            self.assertEqual(len(jobs), 1)
            run_dir = jobs[0]
            self.assertFalse((run_dir / "metrics.json").exists())
            self.assertFalse((run_dir / "manifest.json").exists())
            status = json.loads((run_dir / "status.json").read_text())
            self.assertEqual(status["status"], "failed")
            self.assertEqual(status["stage"], "failed")
            self.assertEqual(status["failure_code"], "WALL_TIME_EXCEEDED")


if __name__ == "__main__":
    unittest.main()
