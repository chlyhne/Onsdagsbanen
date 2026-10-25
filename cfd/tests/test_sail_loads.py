"""Analytic pressure loads on a closed unit cube check sign and SI scaling."""
import unittest

import numpy as np

from cfd.sail_loads import integrated_forces


class SailLoadTests(unittest.TestCase):
    def setUp(self):
        self.points = np.array([[0., 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
                                [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]])
        self.faces = np.array([[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7],
                               [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5],
                               [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]])
        self.zero_cf = np.zeros_like(self.points)

    def test_uniform_pressure_has_zero_net_force(self):
        result = integrated_forces(self.points, self.faces, np.full(8, 3.), self.zero_cf, 44.)
        np.testing.assert_allclose(result["total_N"], [0, 0, 0], atol=1e-12)

    def test_linear_pressure_force_is_negative_gradient_times_volume(self):
        gradient = np.array([2., -3., 4.])
        cp = self.points @ gradient
        for faces in (self.faces, self.faces[:, ::-1]):
            with self.subTest(reversed_winding=bool(faces is not self.faces)):
                result = integrated_forces(self.points, faces, cp, self.zero_cf, 17.)
                np.testing.assert_allclose(result["pressure_N"], -17 * gradient, atol=1e-12)
                np.testing.assert_allclose(result["total_N"], -17 * gradient, atol=1e-12)

    def test_positive_forward_force_and_pressure_friction_split(self):
        cf = np.tile([.01, .02, -.03], (8, 1))
        result = integrated_forces(self.points, self.faces, -self.points[:, 0], cf, 10.)
        np.testing.assert_allclose(result["pressure_N"], [10., 0, 0], atol=1e-12)
        np.testing.assert_allclose(result["friction_N"], [.6, 1.2, -1.8], atol=1e-12)
        np.testing.assert_allclose(result["total_N"], [10.6, 1.2, -1.8], atol=1e-12)

    def test_incomplete_pressure_is_rejected(self):
        cp = np.ones(8)
        cp[0] = np.nan
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            integrated_forces(self.points, self.faces, cp, self.zero_cf, 44.)


if __name__ == "__main__":
    unittest.main()
