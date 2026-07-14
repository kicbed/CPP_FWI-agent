from __future__ import annotations

import unittest

import numpy as np

from fwi_worker.acquisition import build_acquisition
from fwi_worker.config import resolve_config
from fwi_worker.deepwave_2d import forward_model, small_model_gradient_check
from fwi_worker.inversion import run_inversion
from fwi_worker.model_io import load_model


class DeepwaveSmokeTest(unittest.TestCase):
    def test_compact_cpu_forward_is_finite_and_nonzero(self) -> None:
        config = resolve_config(
            {
                "preset": "homogeneous_smoke",
                "homogeneous_shape": [24, 40],
                "n_receivers": 8,
                "nt": 260,
                "pml_width": 8,
            }
        )
        loaded = load_model(config)
        geometry = build_acquisition(config, loaded.velocity.shape)
        result = forward_model(loaded.velocity, config, geometry)
        self.assertEqual(result.receiver_amplitudes.shape, (1, 8, 260))
        self.assertTrue(np.isfinite(result.receiver_amplitudes).all())
        self.assertGreater(np.count_nonzero(result.receiver_amplitudes), 0)

    def test_small_model_directional_derivative(self) -> None:
        result = small_model_gradient_check("cpu")
        self.assertTrue(result["passed"], result)
        self.assertLess(result["relative_error"], 5e-3)

    def test_tiny_one_update_inversion_is_finite_and_changes_model(self) -> None:
        config = resolve_config(
            {
                "preset": "homogeneous_smoke",
                "homogeneous_shape": [20, 32],
                "n_receivers": 6,
                "nt": 220,
                "pml_width": 6,
                "iterations": 1,
                "learning_rate": 2.0,
            }
        )
        loaded = load_model(config)
        geometry = build_acquisition(config, loaded.velocity.shape)
        observed = forward_model(
            loaded.velocity, config, geometry
        ).receiver_amplitudes
        initial = np.full_like(loaded.velocity, 1900.0)
        result = run_inversion(initial, observed, config, geometry)
        self.assertEqual(len(result.losses), 2)
        self.assertTrue(np.isfinite(result.inverted_velocity).all())
        self.assertTrue(np.isfinite(result.predicted_data).all())
        self.assertGreater(result.model_update_relative_l2, 0.0)
        self.assertFalse(np.array_equal(result.inverted_velocity, initial))


if __name__ == "__main__":
    unittest.main()
