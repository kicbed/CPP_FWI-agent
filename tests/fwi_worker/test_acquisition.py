from __future__ import annotations

import unittest

import numpy as np

from fwi_worker.acquisition import build_acquisition, meters_to_grid_index
from fwi_worker.config import resolve_config


class AcquisitionTest(unittest.TestCase):
    def test_meter_coordinate_rounds_to_integer_grid_index(self) -> None:
        self.assertEqual(meters_to_grid_index(20.0, 10.0, 94, label="depth"), 2)
        self.assertEqual(meters_to_grid_index(24.9, 10.0, 94, label="depth"), 2)
        self.assertEqual(meters_to_grid_index(25.1, 10.0, 94, label="depth"), 3)

    def test_out_of_bounds_coordinate_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "out-of-bounds"):
            meters_to_grid_index(940.0, 10.0, 94, label="depth")
        with self.assertRaisesRegex(ValueError, "out-of-bounds"):
            meters_to_grid_index(-10.0, 10.0, 94, label="depth")

    def test_marmousi_geometry_shape_uniqueness_and_bounds(self) -> None:
        config = resolve_config({"preset": "forward", "device": "cpu"})
        geometry = build_acquisition(config, (94, 288))
        self.assertEqual(geometry.source_locations.shape, (3, 1, 2))
        self.assertEqual(geometry.receiver_locations.shape, (3, 96, 2))
        self.assertTrue(np.all(geometry.source_locations[..., 0] == 2))
        self.assertTrue(np.all(geometry.receiver_locations[..., 0] == 2))
        self.assertGreater(int(geometry.source_locations[..., 1].min()), 0)
        self.assertLess(int(geometry.source_locations[..., 1].max()), 287)
        self.assertEqual(np.unique(geometry.receiver_locations[0, :, 1]).size, 96)
        np.testing.assert_array_equal(
            geometry.receiver_locations[0], geometry.receiver_locations[1]
        )

    def test_single_source_is_centered(self) -> None:
        config = resolve_config({"preset": "homogeneous_smoke"})
        geometry = build_acquisition(config, config.homogeneous_shape)
        source_x = int(geometry.source_locations[0, 0, 1])
        self.assertLessEqual(abs(source_x - config.homogeneous_shape[1] // 2), 1)


if __name__ == "__main__":
    unittest.main()
