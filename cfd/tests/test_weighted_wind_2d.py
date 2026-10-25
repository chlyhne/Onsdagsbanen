"""Exact chord-area weights, height integration and missing-layer handling."""
import unittest

import numpy as np

from cfd.case import SAILS
from cfd.rans_mesh import FOOT
from cfd.weighted_wind_2d import average_layers, height_quadrature, sail_chords


class WeightedWindTests(unittest.TestCase):
    def setUp(self):
        self.geometry = {"sail_definitions": SAILS}
        self.z = np.arange(.5, 15.51, .5)

    def test_local_chords_and_zero_weight_outside_each_sail(self):
        chords = sail_chords(self.geometry, [FOOT - .01, FOOT, 4., 10., 12.])
        np.testing.assert_allclose(chords[0], [0, 0])
        np.testing.assert_allclose(chords[1], [3.7, 2.8])
        np.testing.assert_allclose(chords[2], [3.7 * (1 - .96 * (4 - FOOT) / 10.5),
                                             2.8 * (1 - .96 * (4 - FOOT) / 8.5)])
        self.assertGreater(chords[3, 0], 0)
        self.assertEqual(chords[3, 1], 0)
        np.testing.assert_allclose(chords[4], 0)

    def test_quadrature_preserves_analytic_area_at_partial_foot_and_head_layers(self):
        heights, dz, chords, weights = height_quadrature(self.geometry, self.z)
        np.testing.assert_allclose(np.sum(chords * dz[:, None], axis=0), [20.202, 12.376], atol=1e-12)
        self.assertAlmostEqual(weights.sum(), 32.578, places=12)
        self.assertGreater(heights.min(), FOOT)
        self.assertLess(heights.max(), FOOT + 10.5)

    def test_constant_field_is_preserved_without_recentering(self):
        heights, dz, chords, weights = height_quadrature(self.geometry, self.z)
        values = np.full((len(self.z), 2, 3), .17)
        averaged, coverage = average_layers(values, self.z, heights, weights)
        np.testing.assert_allclose(averaged, .17, atol=1e-14)
        np.testing.assert_allclose(coverage, 1., atol=1e-14)

    def test_linear_height_field_has_exact_chord_area_centroid_on_irregular_layers(self):
        z = np.array([0., 1.2, 2.1, 5.7, 9.2, 13.])
        heights, dz, chords, weights = height_quadrature(self.geometry, z)
        average, coverage = average_layers(z[:, None], z, heights, weights)
        areas = np.array([.52 * s[1] * s[2] for s in SAILS])
        centroids = np.array([FOOT + s[2] * (.5 - .96 / 3) / (1 - .96 / 2) for s in SAILS])
        expected = np.sum(areas * centroids) / areas.sum()
        self.assertAlmostEqual(average[0], expected, places=12)
        self.assertAlmostEqual(np.sum(weights * heights) / weights.sum(), expected, places=12)

    def test_layers_above_the_sails_do_not_contribute(self):
        heights, dz, chords, weights = height_quadrature(self.geometry, self.z)
        values = np.ones((len(self.z), 1))
        values[self.z >= 12.5] = np.nan
        average, coverage = average_layers(values, self.z, heights, weights)
        self.assertAlmostEqual(average[0], 1., places=12)
        self.assertAlmostEqual(coverage[0], 1., places=12)

    def test_missing_active_layer_is_masked_without_redistributing_its_weight(self):
        heights, dz, chords, weights = height_quadrature(self.geometry, self.z)
        values = np.ones((len(self.z), 2))
        values[7, 0] = np.nan
        average, coverage = average_layers(values, self.z, heights, weights)
        self.assertTrue(np.isnan(average[0]))
        self.assertLess(coverage[0], 1.)
        self.assertAlmostEqual(average[1], 1., places=12)

    def test_insufficient_vertical_data_is_not_extrapolated(self):
        with self.assertRaisesRegex(ValueError, "cover the full sail height"):
            height_quadrature(self.geometry, [2., 4., 8.])


if __name__ == "__main__":
    unittest.main()
