"""VMG signs, height references, solid holes and original-cell interpolation."""
import unittest

import meshio
import numpy as np

from cfd.plot_wind import delta_vmg, VMG_FACTOR, HEADER_WEIGHT_KNOTS_PER_DEG, KNOTS_PER_M_S
from cfd.volume_data import sample_grid
from cfd.wind_profile import WindProfile
from cfd.wind_viewer_3d import field_scores, height_references, iso_meshes, palette


class WindViewerTests(unittest.TestCase):
    def setUp(self):
        self.zero = {"speed_offset_m_s": 0., "angle_offset_deg": 0., "goodness_offset_knots": 0.}

    def test_uniform_speed_gain_and_header_have_the_same_sign_as_2d(self):
        profile = WindProfile(reference_speed_m_s=10., true_angle_deg=0., boat_speed_knots=0., kind="uniform")
        points = np.array([[0., 0, 4], [1., 0, 4]])
        angle = np.radians(5.)
        values = field_scores(points, [[-11., 0, 0], [-10 * np.cos(angle), -10 * np.sin(angle), 0]], profile, self.zero)
        np.testing.assert_allclose(values["goodness"], [delta_vmg(1., 0.), delta_vmg(0., 5.)], atol=1e-12)
        self.assertGreater(values["goodness"][0], 0)
        self.assertLess(values["goodness"][1], 0)

    def test_undisturbed_height_dependent_wind_has_no_spurious_score(self):
        profile = WindProfile(reference_speed_m_s=5., boat_speed_knots=6.)
        points = np.column_stack((np.zeros((4, 2)), [1., 4., 10., 15.]))
        values = field_scores(points, profile.at(points[:, 2])["apparent_velocity_m_s"], profile, self.zero)
        for field in values.values():
            np.testing.assert_allclose(field, 0., atol=1e-12)

    def test_stagnation_has_no_direction_but_retains_speed_loss(self):
        values = field_scores([[0., 0, 4]], [[0., 0, 0]], WindProfile(), self.zero)
        self.assertTrue(np.isnan(values["goodness"][0]))
        self.assertTrue(np.isnan(values["direction"][0]))
        self.assertLess(values["speed"][0], 0)

    def test_height_medians_keep_the_exact_2d_anchor(self):
        axes = [np.arange(3.), np.arange(3.), np.array([3.5, 4., 4.5])]
        pattern = np.repeat([1., 2., 3.], 9)
        raw = {"speed": pattern * VMG_FACTOR * KNOTS_PER_M_S,
               "direction": -pattern * VMG_FACTOR * HEADER_WEIGHT_KNOTS_PER_DEG,
               "goodness": pattern}
        ref = {"speed_offset_m_s": .4, "angle_offset_deg": 2., "goodness_offset_knots": .3}
        hull = [[-10., -10], [-9., -10], [-9., -9], [-10., -9]]
        result = height_references(axes, raw, hull, ref, 4.)
        for name, value in ref.items():
            np.testing.assert_allclose(result[name], np.array([-1., 0, 1]) + value, atol=1e-12)

    def test_actual_cells_interpolate_affine_velocity_and_preserve_outside_holes(self):
        for kind, points in (("tetra", np.array([[0., 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]])),
                             ("wedge", np.array([[0., 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [0, 1, 1]]))):
            with self.subTest(kind=kind):
                mesh = meshio.Mesh(points, [(kind, [np.arange(len(points))])])
                velocity = points * 2 + [1., -2., 3.]
                axes, positions, sampled, valid = sample_grid(mesh, velocity, [[0., 1.]] * 3, .25)
                np.testing.assert_allclose(sampled[valid], positions[valid] * 2 + [1., -2., 3.], atol=1e-10)
                self.assertTrue(valid.any())
                self.assertTrue((~valid).any())
                self.assertTrue(np.isnan(sampled[~valid]).all())

    def test_isosurface_geometry_matches_its_scalar_instead_of_extruding_a_slice(self):
        axes = [np.linspace(0., 1., 5)] * 3
        z, y, x = np.meshgrid(axes[2], axes[1], axes[0], indexing="ij")
        values = (x - .5).ravel()
        mesh, levels = iso_meshes(axes, {"goodness": values}, {"maximum": .6})
        points = mesh["goodness_iso_points"]
        self.assertGreater(len(points), 0)
        np.testing.assert_allclose(points[:, 0] - .5, mesh["goodness_iso_values"], atol=1e-7)
        self.assertGreater(np.ptp(points[:, 2]), .9)

    def test_palette_retains_red_negative_blue_positive_and_white_neutral(self):
        colors = palette({"minimum": -.6, "maximum": .6, "white_band": .01})
        self.assertEqual(colors[0][1], "#67001f")
        self.assertEqual(colors[-1][1], "#053061")
        self.assertEqual(colors[2][1], "#ffffff")
        self.assertEqual(colors[3][1], "#ffffff")
        self.assertLess(colors[2][0], .5)
        self.assertGreater(colors[3][0], .5)


if __name__ == "__main__":
    unittest.main()
