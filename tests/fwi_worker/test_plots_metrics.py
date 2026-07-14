from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from fwi_worker.acquisition import build_acquisition
from fwi_worker.config import resolve_config
from fwi_worker.metrics import calculate_metrics, relative_l2
from fwi_worker.plots import generate_all_plots, model_extent_km


class PlotsAndMetricsTest(unittest.TestCase):
    def test_all_plots_decode_and_use_common_model_limits(self) -> None:
        config = resolve_config(
            {
                "preset": "homogeneous_smoke",
                "homogeneous_shape": [12, 20],
                "n_receivers": 5,
                "nt": 30,
            }
        )
        shape = config.homogeneous_shape
        true = np.linspace(1500, 5500, shape[0] * shape[1], dtype=np.float32).reshape(
            shape
        )
        initial = np.full(shape, 2500.0, dtype=np.float32)
        inverted = np.full(shape, 2600.0, dtype=np.float32)
        observed = np.zeros((1, 5, 30), dtype=np.float32)
        observed[0, :, 10] = 1.0
        predicted = observed * 0.8
        residual = predicted - observed
        geometry = build_acquisition(config, shape)
        metadata = {
            "shape": list(shape),
            "axis_order": ["z", "x"],
            "x_cell_extent_m": [0.0, shape[1] * config.dx_m],
            "z_cell_extent_m": [0.0, shape[0] * config.dz_m],
        }
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            (run_dir / "figures").mkdir()
            figures, details = generate_all_plots(
                run_dir=run_dir,
                true_velocity=true,
                initial_velocity=initial,
                inverted_velocity=inverted,
                observed=observed,
                predicted=predicted,
                residual=residual,
                losses=[1.0, 0.5],
                metadata=metadata,
                config=config,
                geometry=geometry,
            )
            self.assertEqual(len(figures), 6)
            self.assertEqual(details["model_vmin_mps"], 1500.0)
            self.assertEqual(details["model_vmax_mps"], 5500.0)
            self.assertEqual(details["interpolation"], "nearest")
            self.assertEqual(details["model_extent_km"], [0.0, 0.2, 0.12, 0.0])
            self.assertEqual(details["shot_gather_clipping"]["percentile"], 99.0)
            for figure in figures:
                path = run_dir / figure["relative_path"]
                self.assertGreater(path.stat().st_size, 0)

    def test_marmousi_cell_extent_is_exact(self) -> None:
        config = resolve_config({"preset": "forward", "device": "cpu"})
        metadata = {
            "x_cell_extent_m": [0.0, 2880.0],
            "z_cell_extent_m": [0.0, 940.0],
        }
        self.assertEqual(
            model_extent_km(metadata, (94, 288), config),
            [0.0, 2.88, 0.94, 0.0],
        )

    def test_metrics_include_required_values_and_no_nonfinite_counts(self) -> None:
        config = resolve_config(
            {
                "preset": "homogeneous_smoke",
                "homogeneous_shape": [8, 8],
                "n_receivers": 2,
                "nt": 4,
            }
        )
        true = np.full((8, 8), 2000.0, dtype=np.float32)
        initial = np.full((8, 8), 1900.0, dtype=np.float32)
        inverted = np.full((8, 8), 1950.0, dtype=np.float32)
        observed = np.ones((1, 2, 4), dtype=np.float32)
        predicted = observed * 0.75
        result = calculate_metrics(
            config=config,
            true_velocity=true,
            initial_velocity=initial,
            inverted_velocity=inverted,
            observed_data=observed,
            predicted_data=predicted,
            losses=[1.0, 0.25],
            elapsed_seconds=1.5,
            model_update_relative_l2=0.01,
            gradient_clip_values=[0.1],
            first_arrival_check=None,
        )
        self.assertEqual(result["loss_reduction_fraction"], 0.75)
        self.assertAlmostEqual(result["initial_model_relative_l2"], 0.05)
        self.assertAlmostEqual(result["final_model_relative_l2"], 0.025)
        self.assertEqual(result["nan_count"], 0)
        self.assertEqual(result["inf_count"], 0)
        self.assertEqual(result["model_shape"], [8, 8])


if __name__ == "__main__":
    unittest.main()
