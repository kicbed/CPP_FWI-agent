from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from fwi_worker.artifacts import (
    artifact_url,
    build_manifest,
    prepare_run_dir,
    save_npy,
)
from fwi_worker.job_state import JobState


class StateAndArtifactsTest(unittest.TestCase):
    def test_status_transitions_and_lab_compatible_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            state = JobState(run_dir, "job-123")
            state.update("queued", "queued", 0, 2, "waiting")
            state.update("running", "invert", 0, 2, "working")
            state.append_iteration(1, 8.0, 0.25)
            state.update("succeeded", "complete", 2, 2, "done")
            status = json.loads((run_dir / "status.json").read_text())
            self.assertEqual(status["status"], "succeeded")
            log = (run_dir / "run.log").read_text()
            self.assertIn("ITER 1 FREQ 8 LOSS 0.25", log)
            self.assertIn("STATUS succeeded", log)
            lines = (run_dir / "progress.jsonl").read_text().splitlines()
            self.assertGreaterEqual(len(lines), 4)
            for line in lines:
                self.assertIsInstance(json.loads(line), dict)

    def test_terminal_status_cannot_return_to_running(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = JobState(Path(directory), "job-123")
            state.update("running", "work", 0, 0, "working")
            state.update("failed", "failed", 0, 0, "bad")
            with self.assertRaisesRegex(ValueError, "invalid status transition"):
                state.update("running", "retry", 0, 0, "retrying")

    def test_cancelled_is_terminal_from_queued_or_running(self) -> None:
        for initial in ("queued", "running"):
            with self.subTest(initial=initial), tempfile.TemporaryDirectory() as directory:
                state = JobState(Path(directory), "job-123")
                state.update(initial, initial, 0, 2, initial)
                state.update("cancelled", "cancelled", 0, 2, "cancelled")
                self.assertEqual(state.read()["status"], "cancelled")
                with self.assertRaisesRegex(ValueError, "invalid status transition"):
                    state.update("running", "retry", 0, 2, "retrying")

    def test_run_directory_is_confined_to_configured_root(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            with patch.dict(os.environ, {"FWI_RUN_ROOT": root}):
                run_dir, job_id = prepare_run_dir(
                    str(Path(root) / "safe-job"), "safe-job"
                )
                self.assertEqual(job_id, "safe-job")
                self.assertTrue(run_dir.is_dir())
                with self.assertRaisesRegex(ValueError, "child of FWI_RUN_ROOT"):
                    prepare_run_dir("/tmp/escaped-job", "escaped-job")

    def test_existing_matching_queued_directory_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            queued = Path(root) / "queued-job"
            queued.mkdir()
            (queued / "status.json").write_text(
                json.dumps({"job_id": "queued-job", "status": "queued"}),
                encoding="utf-8",
            )
            (queued / "config.original.json").write_text("{}", encoding="utf-8")
            with patch.dict(os.environ, {"FWI_RUN_ROOT": root}):
                run_dir, job_id = prepare_run_dir(str(queued), "queued-job")
            self.assertEqual(job_id, "queued-job")
            self.assertEqual(run_dir, queued)
            self.assertTrue((queued / "models").is_dir())

    def test_only_managed_bootstrap_accepts_private_launch_sidecars(self) -> None:
        control_names = (
            ".worker-launch.json",
            ".worker-ready.json",
            ".worker-heartbeat.json",
        )
        with tempfile.TemporaryDirectory() as root:
            queued = Path(root) / "managed-job"
            queued.mkdir()
            (queued / "status.json").write_text(
                json.dumps({"job_id": "managed-job", "status": "queued"}),
                encoding="utf-8",
            )
            (queued / "config.original.json").write_text("{}", encoding="utf-8")
            for name in control_names:
                path = queued / name
                path.write_text("{}", encoding="utf-8")
                path.chmod(0o600)
            with patch.dict(os.environ, {"FWI_RUN_ROOT": root}):
                with self.assertRaisesRegex(ValueError, "unexpected pre-existing"):
                    prepare_run_dir(str(queued), "managed-job")
                run_dir, job_id = prepare_run_dir(
                    str(queued), "managed-job", managed_launch=True
                )
            self.assertEqual((run_dir, job_id), (queued, "managed-job"))

        for unsafe_kind in ("symlink", "fifo", "permissions"):
            with self.subTest(unsafe_kind=unsafe_kind), tempfile.TemporaryDirectory() as root:
                queued = Path(root) / "managed-job"
                queued.mkdir()
                (queued / "status.json").write_text(
                    json.dumps({"job_id": "managed-job", "status": "queued"}),
                    encoding="utf-8",
                )
                (queued / "config.original.json").write_text(
                    "{}", encoding="utf-8"
                )
                control = queued / ".worker-launch.json"
                if unsafe_kind == "symlink":
                    outside = Path(root) / "outside.json"
                    outside.write_text("{}", encoding="utf-8")
                    control.symlink_to(outside)
                elif unsafe_kind == "fifo":
                    os.mkfifo(control, mode=0o600)
                else:
                    control.write_text("{}", encoding="utf-8")
                    control.chmod(0o644)
                with patch.dict(os.environ, {"FWI_RUN_ROOT": root}):
                    with self.assertRaises(ValueError):
                        prepare_run_dir(
                            str(queued), "managed-job", managed_launch=True
                        )

    def test_existing_succeeded_directory_cannot_be_reused(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            succeeded = Path(root) / "done-job"
            succeeded.mkdir()
            (succeeded / "status.json").write_text(
                json.dumps({"job_id": "done-job", "status": "succeeded"}),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"FWI_RUN_ROOT": root}):
                with self.assertRaisesRegex(ValueError, "matching queued job"):
                    prepare_run_dir(str(succeeded), "done-job")

    def test_queued_symlink_entries_and_artifact_target_are_rejected(self) -> None:
        for symlink_name in ("config.original.json", "run.log", "models"):
            with self.subTest(symlink_name=symlink_name), tempfile.TemporaryDirectory() as root:
                queued = Path(root) / "queued-job"
                queued.mkdir()
                (queued / "status.json").write_text(
                    json.dumps({"job_id": "queued-job", "status": "queued"}),
                    encoding="utf-8",
                )
                outside = Path(root) / "outside"
                if symlink_name == "models":
                    outside.mkdir()
                    (queued / "config.original.json").write_text("{}", encoding="utf-8")
                else:
                    outside.write_text("outside", encoding="utf-8")
                    if symlink_name != "config.original.json":
                        (queued / "config.original.json").write_text("{}", encoding="utf-8")
                (queued / symlink_name).symlink_to(outside, target_is_directory=outside.is_dir())
                with patch.dict(os.environ, {"FWI_RUN_ROOT": root}):
                    with self.assertRaises(ValueError):
                        prepare_run_dir(str(queued), "queued-job")

        with tempfile.TemporaryDirectory() as root:
            with patch.dict(os.environ, {"FWI_RUN_ROOT": root}):
                run_dir, _ = prepare_run_dir(str(Path(root) / "new-job"), "new-job")
                outside = Path(root) / "outside.npy"
                outside.write_bytes(b"unchanged")
                target = run_dir / "models" / "model.npy"
                target.symlink_to(outside)
                with self.assertRaises(FileExistsError):
                    save_npy(target, np.ones((2, 2), dtype=np.float32))
                self.assertEqual(outside.read_bytes(), b"unchanged")

    def test_manifest_references_real_pngs_and_safe_urls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            figure_path = run_dir / "figures" / "true_model.png"
            figure_path.parent.mkdir()
            figure_path.write_bytes(b"not decoded by manifest helper")
            manifest = build_manifest(
                run_dir=run_dir,
                job_id="job-123",
                model_id="marmousi_94_288",
                metrics={"initial_loss": 1.0},
                figures=[
                    {
                        "id": "true_model",
                        "title": "真实速度模型",
                        "relative_path": "figures/true_model.png",
                    }
                ],
                plot_details={},
                command="invert",
            )
            self.assertEqual(manifest["type"], "fwi_result")
            self.assertEqual(
                manifest["figures"][0]["url"],
                "/fwi-artifacts/job-123/figures/true_model.png",
            )
            self.assertIn("逆犯罪验证", manifest["disclaimer"])
            with self.assertRaises(ValueError):
                artifact_url("job-123", "../secret")


if __name__ == "__main__":
    unittest.main()
