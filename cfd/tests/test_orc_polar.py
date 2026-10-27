"""Polar interpolation, percentage derivatives and true/apparent wind frames."""
import json
import unittest

import numpy as np

from cfd.orc_polar import POLAR_PATH, boat_speed, linearisation, percent_fields, true_wind_perturbations
from cfd.plot_wind import KNOTS_PER_M_S, VMG_FACTOR, HEADER_WEIGHT_KNOTS_PER_DEG
from cfd.wind_profile import WindProfile


class ORCPolarTests(unittest.TestCase):
    def setUp(self):
        self.polar = json.loads(POLAR_PATH.read_text())

    def test_interpolator_matches_certificate_points_and_beat_vmg(self):
        for j, wind in enumerate(self.polar['true_wind_knots']):
            for i, angle in enumerate(self.polar['true_wind_angle_deg']):
                self.assertAlmostEqual(boat_speed(self.polar, wind, angle), self.polar['boat_speed_knots'][i][j])
            angle = self.polar['beat_angle_deg'][j]
            self.assertAlmostEqual(boat_speed(self.polar, wind, angle)*np.cos(np.radians(angle)), self.polar['beat_vmg_knots'][j])

    def test_local_percentage_agrees_with_interpolated_polar_for_small_changes(self):
        coefficients = linearisation()
        u, angle = coefficients['true_wind_knots'], coefficients['true_wind_angle_deg']
        for du, header in ((.0001, 0), (0, .0001), (.0001, -.0001)):
            exact = 100*(boat_speed(self.polar, u+du, angle-header)/coefficients['boat_speed_knots']-1)
            linear = coefficients['percent_per_true_wind_knot']*du + coefficients['percent_per_true_header_degree']*header
            self.assertAlmostEqual(exact, linear, places=7)
        self.assertGreater(coefficients['percent_per_true_wind_knot'], 0)
        self.assertLess(coefficients['percent_per_true_header_degree'], 0)

    def test_rejects_extrapolation_below_beat_or_beyond_wind_range(self):
        with self.assertRaises(ValueError):boat_speed(self.polar, 5, 52)
        with self.assertRaises(ValueError):boat_speed(self.polar, 10, 30)

    def test_true_wind_frame_recovers_known_cartesian_perturbations(self):
        profile = WindProfile(reference_speed_m_s=5., boat_speed_knots=6., true_angle_deg=41.)
        heights = np.array([2., 8.])
        apparent = np.asarray(profile.at(heights)['apparent_velocity_m_s'])[:, :2]
        true = apparent + [6/KNOTS_PER_M_S, 0]
        ea = apparent/np.linalg.norm(apparent, axis=1)[:, None]
        et = true/np.linalg.norm(true, axis=1)[:, None]
        nt = np.column_stack((-et[:, 1], et[:, 0]))
        for delta in (et*.1, nt*(np.linalg.norm(true, axis=1)*np.radians(.2))[:, None]):
            speed = np.sum(ea*delta, axis=1)
            header = np.degrees((ea[:, 0]*delta[:, 1]-ea[:, 1]*delta[:, 0])/np.linalg.norm(apparent, axis=1))
            dw, dh = true_wind_perturbations(speed, header, heights, profile)
            np.testing.assert_allclose(dw, np.sum(et*delta, axis=1)*KNOTS_PER_M_S, atol=1e-12)
            np.testing.assert_allclose(dh, np.degrees((et[:, 0]*delta[:, 1]-et[:, 1]*delta[:, 0])/np.linalg.norm(true, axis=1)), atol=1e-12)
            fields = percent_fields(speed*VMG_FACTOR*KNOTS_PER_M_S, -header*VMG_FACTOR*HEADER_WEIGHT_KNOTS_PER_DEG, heights, profile, linearisation())
            np.testing.assert_allclose(fields['goodness'], fields['speed']+fields['direction'])

    def test_zero_change_is_zero_and_missing_data_stays_missing(self):
        fields = percent_fields([0., np.nan], [0., np.nan], [2., 8.], WindProfile(), linearisation())
        for values in fields.values():
            self.assertEqual(values[0], 0.)
            self.assertTrue(np.isnan(values[1]))
