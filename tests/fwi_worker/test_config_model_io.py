from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from fwi_worker.config import resolve_config
from fwi_worker.model_io import (
    load_model,
    make_initial_model,
    read_and_validate_sidecar,
)


class ConfigAndModelIOTest(unittest.TestCase):
    def test_marmousi_preset_has_required_defaults(self) -> None:
        config = resolve_config({"preset": "fwi_smoke", "device": "cpu"})
        self.assertEqual(config.model_id, "marmousi_94_288")
        self.assertEqual(config.iterations, 2)
        self.assertEqual(config.source_frequency_hz, 8.0)
        self.assertEqual(config.nt, 2000)
        self.assertEqual(config.accuracy, 4)
        self.assertEqual(config.pml_width, 20)
        self.assertEqual(config.n_shots, 3)
        self.assertEqual(config.n_receivers, 96)
        self.assertAlmostEqual(config.courant_number, 0.7778174593)

    def test_explicit_inversion_iteration_count_is_bounded(self) -> None:
        for preset in ("fwi_smoke", "fwi_demo"):
            for iterations in (1, 50, 100):
                with self.subTest(preset=preset, iterations=iterations):
                    config = resolve_config(
                        {
                            "preset": preset,
                            "device": "cpu",
                            "iterations": iterations,
                        }
                    )
                    self.assertEqual(config.iterations, iterations)

            for iterations in (-1, 0, 101):
                with self.subTest(preset=preset, iterations=iterations):
                    with self.assertRaises(ValueError):
                        resolve_config(
                            {
                                "preset": preset,
                                "device": "cpu",
                                "iterations": iterations,
                            }
                        )

    def test_iteration_count_requires_a_strict_integer(self) -> None:
        for iterations in ("50", 50.0, True, None):
            with self.subTest(iterations=iterations):
                with self.assertRaises(ValueError):
                    resolve_config(
                        {
                            "preset": "fwi_demo",
                            "device": "cpu",
                            "iterations": iterations,
                        }
                    )

    def test_forward_only_presets_require_zero_iterations(self) -> None:
        for preset in ("marmousi_94_288_demo", "forward"):
            with self.subTest(preset=preset):
                self.assertEqual(
                    resolve_config({"preset": preset, "device": "cpu"}).iterations,
                    0,
                )
                with self.assertRaises(ValueError):
                    resolve_config(
                        {"preset": preset, "device": "cpu", "iterations": 1}
                    )

        # The internal homogeneous preset doubles as the tiny numerical
        # gradient/update test bed; it is not exposed by the MCP/Web surface.
        self.assertEqual(
            resolve_config(
                {"preset": "homogeneous_smoke", "device": "cpu", "iterations": 1}
            ).iterations,
            1,
        )

    def test_unknown_fields_are_rejected(self) -> None:
        with self.assertRaisesRegex(Exception, "extra"):
            resolve_config({"preset": "forward", "shell_command": "rm -rf /"})

    def test_real_sidecar_and_both_model_hashes_validate(self) -> None:
        config = resolve_config({"preset": "forward", "device": "cpu"})
        metadata = read_and_validate_sidecar(config)
        self.assertEqual(metadata["shape"], [94, 288])
        self.assertEqual(metadata["axis_order"], ["z", "x"])
        self.assertEqual(metadata["compute_dtype"], "float32")

    def test_model_shape_dtype_and_velocity_range(self) -> None:
        config = resolve_config({"preset": "forward", "device": "cpu"})
        loaded = load_model(config)
        self.assertEqual(loaded.velocity.shape, (94, 288))
        self.assertEqual(loaded.velocity.dtype, np.float32)
        self.assertEqual(float(loaded.velocity.min()), 1500.0)
        self.assertEqual(float(loaded.velocity.max()), 5500.0)

    def test_slowness_smoothing_preserves_top_row_and_bounds(self) -> None:
        config = resolve_config({"preset": "forward", "device": "cpu"})
        true = load_model(config).velocity
        initial = make_initial_model(true, config)
        np.testing.assert_array_equal(initial[0], true[0])
        self.assertEqual(initial.shape, true.shape)
        self.assertEqual(initial.dtype, np.float32)
        self.assertGreaterEqual(float(initial.min()), 1500.0)
        self.assertLessEqual(float(initial.max()), 5500.0)
        self.assertFalse(np.array_equal(initial, true))


if __name__ == "__main__":
    unittest.main()
