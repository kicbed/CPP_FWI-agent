from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import torch

from fwi_worker.acquisition import AcquisitionGeometry
from fwi_worker.checkpoint import save_checkpoint_payload
from fwi_worker.config import resolve_config
from fwi_worker.inversion import InversionCheckpointState, run_inversion
from worker_launch_control import LaunchAttemptBinding


class CheckpointPayloadTest(unittest.TestCase):
    def config(self):
        return resolve_config(
            {
                "preset": "fwi_smoke",
                "device": "cpu",
                "iterations": 2,
                "n_shots": 1,
                "n_receivers": 2,
                "shot_batch_size": 1,
                "nt": 3,
            }
        )

    @staticmethod
    def optimizer_state(config):
        velocity = torch.nn.Parameter(
            torch.full((2, 3), 2000.0, dtype=torch.float32)
        )
        optimizer = torch.optim.Adam([velocity], lr=config.learning_rate)
        optimizer.zero_grad(set_to_none=True)
        velocity.square().sum().backward()
        optimizer.step()
        return InversionCheckpointState(
            completed_updates=1,
            next_state_index=1,
            velocity=velocity,
            optimizer=optimizer,
            losses=(1.25,),
            gradient_clip_values=(0.5,),
        )

    def test_payload_is_finite_bounded_append_only_and_no_pickle(self) -> None:
        config = self.config()
        binding = LaunchAttemptBinding(
            submission_id="submission-" + "a" * 64,
            attempt_id="attempt-" + "b" * 32,
            attempt_number=1,
            job_id="fwi-20260717T010203Z-123456789abc",
            request_hash="sha256:" + "c" * 64,
            created_at="2026-07-17T01:02:03Z",
        )
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / binding.job_id
            run_dir.mkdir(mode=0o700)
            evidence = save_checkpoint_payload(
                run_dir=run_dir,
                binding=binding,
                config=config,
                checkpoint=self.optimizer_state(config),
                clock=lambda: "2026-07-17T01:02:04Z",
            )
            checkpoint_dir = run_dir / "checkpoints" / evidence.checkpoint_id
            manifest_path = run_dir / evidence.manifest_relative_path
            manifest_bytes = manifest_path.read_bytes()
            manifest = json.loads(manifest_bytes)
            self.assertEqual(len(manifest_bytes), evidence.manifest_size_bytes)
            self.assertEqual(manifest["completed_updates"], 1)
            self.assertEqual(manifest["next_state_index"], 1)
            self.assertEqual(manifest["optimizer"]["step"], 1)
            self.assertEqual(
                set(manifest["optimizer"]["state"]),
                {"exp_avg", "exp_avg_sq"},
            )
            self.assertEqual(
                sorted(path.suffix for path in checkpoint_dir.iterdir()),
                [".json", ".npy", ".npy", ".npy", ".npy", ".npy"],
            )
            for path in checkpoint_dir.glob("*.npy"):
                value = np.load(path, allow_pickle=False)
                self.assertTrue(np.isfinite(value).all())
                self.assertNotEqual(value.dtype.kind, "O")
            with self.assertRaises(FileExistsError):
                save_checkpoint_payload(
                    run_dir=run_dir,
                    binding=binding,
                    config=config,
                    checkpoint=self.optimizer_state(config),
                )

    def test_inversion_enters_barrier_exactly_after_first_update(self) -> None:
        config = self.config()
        initial = np.full((2, 3), 2000.0, dtype=np.float32)
        observed = np.ones(
            (config.n_shots, config.n_receivers, config.nt), dtype=np.float32
        )
        geometry = AcquisitionGeometry(
            source_locations=np.zeros((1, 1, 2), dtype=np.int64),
            receiver_locations=np.zeros((1, 2, 2), dtype=np.int64),
            source_x_m=np.zeros(1, dtype=np.float32),
            receiver_x_m=np.zeros(2, dtype=np.float32),
        )
        checkpoints: list[tuple[int, int, int, int]] = []

        def simulate(velocity, batch_config, batch_geometry, *, wavelet):
            return velocity.mean() * torch.ones(
                (
                    batch_config.n_shots,
                    batch_config.n_receivers,
                    batch_config.nt,
                ),
                dtype=torch.float32,
            )

        def checkpoint(state: InversionCheckpointState) -> None:
            raw_step = state.optimizer.state[state.velocity]["step"]
            checkpoints.append(
                (
                    state.completed_updates,
                    state.next_state_index,
                    len(state.losses),
                    int(float(raw_step)),
                )
            )

        with (
            patch("fwi_worker.inversion.validate_device", return_value=torch.device("cpu")),
            patch("fwi_worker.inversion.make_source_wavelet", return_value=None),
            patch("fwi_worker.inversion.simulate_tensor", side_effect=simulate),
        ):
            result = run_inversion(
                initial,
                observed,
                config,
                geometry,
                checkpoint=checkpoint,
            )
        self.assertEqual(checkpoints, [(1, 1, 1, 1)])
        self.assertEqual(len(result.losses), config.iterations + 1)


if __name__ == "__main__":
    unittest.main()
