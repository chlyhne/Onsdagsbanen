"""Incidence definitions and hole-preserving probes of saved CFD triangles."""
import unittest

import numpy as np

from cfd.case import SAILS, case_entry_angle
from cfd.jib_entry import angle_difference, friction_recoveries, slice_sampler
from cfd.rans_mesh import SHAPES, _skin_points


class JibEntryTests(unittest.TestCase):
    def test_entry_secant_and_exact_tangent_are_distinct_and_correct(self):
        geometry = {"sail_definitions": SAILS, "sail_shapes": SHAPES}
        sail, shape = SAILS[1], SHAPES[1]
        p0 = _skin_points(sail, 4., 0., shape, 0)[0]
        p5 = _skin_points(sail, 4., .05, shape, 0)[0]
        secant = np.degrees(np.arctan2(p5[1] - p0[1], -(p5[0] - p0[0])))
        self.assertAlmostEqual(secant, case_entry_angle(geometry, 1, 4.), places=12)
        near = _skin_points(sail, 4., 1e-7, shape, 0)[0] - p0
        tangent = np.degrees(np.arctan2(near[1], -near[0]))
        self.assertAlmostEqual(tangent, sail[3] + np.degrees(np.arctan(4 * shape["camber"])), places=5)

    def test_signed_angle_and_wrap(self):
        np.testing.assert_allclose(angle_difference([24., 179., -179.], [36., -179., 179.]), [-12., -2., 2.])

    def test_velocity_interpolation_preserves_holes_and_units(self):
        arrays = {"xy": np.array([[0., 0], [1, 0], [0, 1], [2, 0], [3, 0], [2, 1]]),
                  "triangles": np.array([[0, 1, 2], [3, 4, 5]]),
                  "velocity": np.array([[-2., 1, .5], [-1, 1, .5], [-2, 2, .5],
                                        [0, 1, .5], [1, 1, .5], [0, 2, .5]])}
        sample = slice_sampler({"height_m": 4.}, arrays, 4.)
        values = sample([[.2, .2, 4.], [1.5, .1, 4.]])
        np.testing.assert_allclose(values[0], [-1.8, 1.2, .5], atol=1e-12)
        self.assertTrue(np.isnan(values[1]).all())
        with self.assertRaisesRegex(ValueError, "horizontal plane"):
            sample([[.2, .2, 5.]])
        with self.assertRaisesRegex(ValueError, "height differs"):
            slice_sampler({"height_m": 4.}, arrays, 5.)

    def test_wall_friction_crossing_preserves_side_and_height(self):
        rows = [{"height_m": 4., "side": "leeward", "chord_fraction": .05, "Cf_t": -.002},
                {"height_m": 4., "side": "leeward", "chord_fraction": .1, "Cf_t": .003},
                {"height_m": 4., "side": "windward", "chord_fraction": .05, "Cf_t": .001},
                {"height_m": 6., "side": "leeward", "chord_fraction": .05, "Cf_t": -.002}]
        recovered = friction_recoveries(rows)
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0]["side"], "leeward")
        self.assertEqual(recovered[0]["height_m"], 4.)
        self.assertAlmostEqual(recovered[0]["negative_to_positive_Cf_x_c"], .07)


if __name__ == "__main__":
    unittest.main()
