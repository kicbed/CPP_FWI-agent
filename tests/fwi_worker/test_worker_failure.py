from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fwi_worker.__main__ import run_worker


class WorkerFailureArtifactsTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
