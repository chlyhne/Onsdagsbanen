"""Check velocity direction and actual clipped 3D volume on analytic fields."""
import unittest

import meshio
import numpy as np

from cfd.rans_mesh import SHAPES, _skin_points
from cfd.case import SAILS
from cfd.separation_3d import reverse_volume, sail_coordinates


class SeparationVolumeTests(unittest.TestCase):
    def test_tetrahedron_volume_and_boundary_follow_velocity_threshold(self):
        points = np.array([[0., 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]])
        mesh = meshio.Mesh(points, [("tetra", [[0, 1, 2, 3]])])
        surface, volume = reverse_volume(mesh, points[:, 0] - .5, np.ones(4), .05)
        self.assertAlmostEqual(volume, (1 - .55**3) / 6, places=12)
        self.assertAlmostEqual(surface.points[:, 0].max(), .45, places=12)
        self.assertEqual(surface.n_open_edges, 0)
        self.assertTrue((surface["tangent_velocity_m_s"] <= -.05 + 1e-12).all())

    def test_geographic_clip_does_not_override_velocity_clip(self):
        points = np.array([[0., 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]])
        mesh = meshio.Mesh(points, [("tetra", [[0, 1, 2, 3]])])
        surface, volume = reverse_volume(mesh, points[:, 0] - .5, .2 - points[:, 0], .05)
        self.assertAlmostEqual(volume, (1 - .8**3) / 6, places=12)
        self.assertAlmostEqual(surface.points[:, 0].max(), .2, places=12)
        self.assertEqual(surface.n_open_edges, 0)

    def test_hybrid_prism_and_hex_cells_keep_correct_volume(self):
        wedge = np.array([[0., 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [0, 1, 1]])
        cube = np.array([[0., 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
                         [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]])
        for kind, points, expected in (("wedge", wedge, (1 - .55**2) / 2),
                                       ("hexahedron", cube, .45)):
            with self.subTest(kind=kind):
                mesh = meshio.Mesh(points, [(kind, [np.arange(len(points))])])
                surface, volume = reverse_volume(mesh, points[:, 0] - .5, np.ones(len(points)), .05)
                self.assertAlmostEqual(volume, expected, places=12)
                self.assertEqual(surface.n_open_edges, 0)

    def test_forward_and_weak_reverse_flow_produce_no_blob(self):
        points = np.array([[0., 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]])
        mesh = meshio.Mesh(points, [("tetra", [[0, 1, 2, 3]])])
        for speed in (2., 0., -.01):
            surface, volume = reverse_volume(mesh, np.full(4, speed), np.ones(4), .05)
            self.assertEqual(surface.n_cells, 0)
            self.assertEqual(volume, 0.)

    def test_tangent_projection_and_leeward_side_match_saved_curved_sail(self):
        sail, shape, height, t = SAILS[0], SHAPES[0], 4., .35
        point = _skin_points(sail, height, t, shape, 0)[0]
        angle = np.radians(sail[3])
        normal = np.array([np.sin(angle), np.cos(angle), 0.])
        slope = 4 * shape["camber"] * (1 - 2 * t)
        tangent = np.array([-np.cos(angle) + slope * np.sin(angle),
                            np.sin(angle) + slope * np.cos(angle), 0.])
        tangent /= np.linalg.norm(tangent)
        points = np.array([point + .01 * normal, point - .01 * normal])
        # A normal translation keeps the original chord coordinate unchanged.
        ut, margin = sail_coordinates(points, np.array([3 * tangent, -2 * tangent]), sail, shape)
        np.testing.assert_allclose(ut, [3., -2.], atol=1e-12)
        self.assertGreater(margin[0], 0)
        self.assertLess(margin[1], 0)

    def test_other_side_changes_search_side_without_reversing_flow_direction(self):
        sail, shape = SAILS[0], SHAPES[0]
        points = np.vstack((_skin_points(sail, 4., .5, shape, 1),
                            _skin_points(sail, 4., .5, shape, -1)))
        angle = np.radians(sail[3])
        flow = np.tile([-3 * np.cos(angle), 3 * np.sin(angle), 0], (2, 1))
        lee_ut, lee_margin = sail_coordinates(points, flow, sail, shape, 1)
        wind_ut, wind_margin = sail_coordinates(points, flow, sail, shape, -1)
        np.testing.assert_allclose(lee_ut, [3, 3], atol=1e-12)
        np.testing.assert_allclose(wind_ut, lee_ut, atol=1e-12)
        self.assertGreater(lee_margin[0], 0)
        self.assertLess(lee_margin[1], 0)
        self.assertLess(wind_margin[0], 0)
        self.assertGreater(wind_margin[1], 0)


if __name__ == "__main__":
    unittest.main()
