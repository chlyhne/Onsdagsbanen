"""2D geometry, units and result preservation; no CFD solver is started."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import meshio
import numpy as np

from cfd.marine_rans import read_profile, sha
from cfd.section_2d import (REFERENCE, add_second_boat, analyse, configuration,
                          field_stability, mesh_2d, plot_geometry, prepare, run,
                          triangles_from_mesh)


class SectionTests(unittest.TestCase):
    def test_second_boat_is_identical_aft_and_on_the_windward_side(self):
        original = json.loads((REFERENCE / 'geometry.json').read_text())
        for angle, expected in ((41., [-7.5, -6.]), (-41., [-7.5, 6.])):
            geometry = add_second_boat(original, angle)
            self.assertEqual(geometry['boat_offsets_xy_m'], [[0., 0.], expected])
            self.assertEqual(geometry['boat_spacing']['boat_beam_m'], 3.)
            hulls = plot_geometry(geometry, 4.)
            np.testing.assert_allclose(np.asarray(hulls['hull_outlines'][1]) - hulls['hull_outlines'][0],
                                       np.tile(expected, (len(original['hull']), 1)), atol=1e-14)
            for a, b in ((0, 2), (1, 3)):
                delta = np.asarray(hulls['sail_sections'][b]) - hulls['sail_sections'][a]
                np.testing.assert_allclose(delta, np.tile([*expected, 0.], (len(delta), 1)), atol=1e-14)
            self.assertAlmostEqual(geometry['boat_spacing']['boat_length_m'] - abs(expected[0]), 2.5)
        self.assertNotIn('boat_offsets_xy_m', original)

    def test_all_six_surfaces_are_no_slip_and_monitored(self):
        geometry = add_second_boat(json.loads((REFERENCE / 'geometry.json').read_text()), 41.)
        profile = read_profile(json.loads((REFERENCE / 'marine_case.json').read_text()))
        with tempfile.TemporaryDirectory() as folder:
            case = Path(folder)
            (case / 'geometry.json').write_text(json.dumps(geometry))
            configuration(case, profile, 4.)
            config = (case / 'yacht.cfg').read_text()
            self.assertIn('MARKER_HEATFLUX= (mast, 0.0, main, 0.0, jib, 0.0, mast_2, 0.0, main_2, 0.0, jib_2, 0.0)', config)
            self.assertIn('MARKER_MONITORING= (mast, main, jib, mast_2, main_2, jib_2)', config)

    def test_pair_mesh_contains_six_holes_and_two_translated_masts(self):
        geometry = add_second_boat(json.loads((REFERENCE / 'geometry.json').read_text()), 41.)
        with tempfile.TemporaryDirectory() as folder:
            case = Path(folder)
            metadata = mesh_2d(case, geometry, 4.)
            mesh = meshio.read(case / 'yacht.msh')
            self.assertEqual(metadata['points'], len(mesh.points))
            edges = np.concatenate([block.data for block in mesh.cells if block.type == 'line'])
            tags = np.concatenate([tags for block, tags in zip(mesh.cells, mesh.cell_data['gmsh:physical'])
                                   if block.type == 'line'])
            self.assertEqual(set(tags), {10, 12, 13, 14, 22, 23, 24})
            for tag, center in ((12, [.15, 0.]), (22, [-7.35, -6.])):
                xy = mesh.points[np.unique(edges[tags == tag]), :2]
                np.testing.assert_allclose(np.linalg.norm(xy - center, axis=1), .075, atol=1e-10)
            # The fluid triangulation has six solid holes plus its exterior.
            tri = triangles_from_mesh(mesh)
            vertices = len(np.unique(tri))
            fluid_edges = np.sort(np.concatenate([tri[:, [0, 1]], tri[:, [1, 2]], tri[:, [2, 0]]]), axis=1)
            edge_count = len(np.unique(fluid_edges, axis=0))
            self.assertEqual(vertices - edge_count + len(tri), 1 - 6)

    def test_colour_reference_excludes_both_hulls_but_keeps_the_gap(self):
        from cfd.plot_wind import distance_from_boats
        geometry = add_second_boat(json.loads((REFERENCE / 'geometry.json').read_text()), 41.)
        hulls = plot_geometry(geometry, 4.)['hull_outlines']
        distances = distance_from_boats(np.array([[0., 0.], [-7.5, -6.], [-3.75, -3.]]), hulls)
        np.testing.assert_allclose(distances[:2], 0.)
        self.assertGreater(distances[2], 1.)

    def test_setup_samples_local_shear_and_uses_only_2d_boundaries(self):
        profile = read_profile(json.loads((REFERENCE / 'marine_case.json').read_text()))
        with tempfile.TemporaryDirectory() as folder:
            case = Path(folder)
            velocity = configuration(case, profile, 4.)
            np.testing.assert_allclose(velocity, profile.at(4.)['apparent_velocity_m_s'])
            self.assertLess(np.linalg.norm(velocity), profile.at(10.)['apparent_speed_m_s'])
            config = dict(line.split('=', 1) for line in (case / 'yacht.cfg').read_text().splitlines()
                          if '=' in line and not line.startswith('%'))
            config = {key.strip(): value.strip() for key, value in config.items()}
            self.assertEqual(config['MARKER_HEATFLUX'], '(mast, 0.0, main, 0.0, jib, 0.0)')
            self.assertNotIn('RMS_VELOCITY-Z', config['CONV_FIELD'])
            self.assertNotIn('MARKER_EULER', config)
            self.assertEqual(config['RESTART_SOL'], 'NO')
            self.assertGreater(int(config['CONV_STARTITER']), int(config['ITER']))
            self.assertAlmostEqual(float(config['FREESTREAM_TURB2LAMVISCRATIO']),
                                   profile.turbulence_at(4.)['eddy_viscosity_m2_s'] / (1.81e-5 / 1.225))

    def test_invalid_height_and_existing_case_do_not_touch_mesh(self):
        with tempfile.TemporaryDirectory() as folder:
            case = Path(folder) / 'new'
            with patch('cfd.section_2d.mesh_2d', side_effect=AssertionError('Unexpected mesh')):
                for height in (0., 12., float('nan')):
                    with self.assertRaises(ValueError):
                        prepare(REFERENCE, case, height)
                    self.assertFalse(case.exists())
                case.mkdir()
                (case / 'keep').write_text('previous results')
                with self.assertRaises(ValueError):
                    prepare(REFERENCE, case)
                self.assertEqual((case / 'keep').read_text(), 'previous results')

    def test_quad_conversion_preserves_fluid_area_and_connectivity(self):
        # Separate cells leave a real gap; no Delaunay filling of solid regions.
        points = np.array([[0, 0], [1, 0], [1, 1], [0, 1], [3, 0], [4, 0], [3, 1]])
        mesh = meshio.Mesh(points, [('quad', [[0, 3, 2, 1]]), ('triangle', [[4, 5, 6]])])
        triangles = triangles_from_mesh(mesh)
        p = points[triangles]
        twice_area = ((p[:, 1, 0] - p[:, 0, 0]) * (p[:, 2, 1] - p[:, 0, 1])
                      - (p[:, 1, 1] - p[:, 0, 1]) * (p[:, 2, 0] - p[:, 0, 0]))
        self.assertTrue(np.all(twice_area > 0))
        self.assertAlmostEqual(twice_area.sum() / 2, 1.5)
        self.assertTrue(all(np.all(t < 4) or np.all(t >= 4) for t in triangles))

    def test_run_refuses_overwrite_and_missing_restart_before_solver(self):
        with tempfile.TemporaryDirectory() as folder:
            case = Path(folder)
            (case / 'yacht.su2').write_text('mesh')
            (case / 'section_case.json').write_text(json.dumps({'mesh_sha256': sha(case / 'yacht.su2')}))
            with patch('cfd.solver.solver_path', side_effect=AssertionError('Unexpected solver')):
                with self.assertRaisesRegex(ValueError, 'No restart'):
                    run(case, restart=True)
                (case / 'history.csv').write_text('previous results')
                with self.assertRaisesRegex(ValueError, 'already exists'):
                    run(case)
                self.assertEqual((case / 'history.csv').read_text(), 'previous results')

    def test_saved_uniform_field_has_zero_disturbance_in_physical_units(self):
        profile = read_profile(json.loads((REFERENCE / 'marine_case.json').read_text()))
        velocity = np.asarray(profile.at(4.)['apparent_velocity_m_s'])[:2]
        speed = np.linalg.norm(velocity)
        points = np.array([[0., 0., 0.], [1., 0., 0.], [1., 1., 0.], [0., 1., 0.]])
        mesh = meshio.Mesh(points, [('quad', [[0, 1, 2, 3]]), ('line', [[0, 1], [1, 2], [2, 3]])],
                           cell_data={'gmsh:physical': [np.array([1]), np.array([12, 13, 14])]})
        surface = meshio.Mesh(points, [('line', [[0, 1], [1, 2], [2, 3]])],
                              point_data={'Y_Plus': np.ones(4)})
        names = ['x', 'y', 'Pressure', 'Velocity_x', 'Velocity_y', 'Turb_Kin_Energy', 'Omega']
        native = np.zeros((4, len(names)))
        native[:, :2] = points[:, :2]
        native[:, 3:5] = velocity / speed
        native[:, 5] = .02 / speed**2
        native[:, 6] = 1.
        with tempfile.TemporaryDirectory() as folder:
            case = Path(folder)
            manifest = {'model': '2D steady INC_RANS SST V1994m', 'height_m': 4.,
                        'profile': profile.metadata(), 'reference_velocity_m_s': [*velocity, 0.]}
            (case / 'section_case.json').write_text(json.dumps(manifest))
            (case / 'geometry.json').write_bytes((REFERENCE / 'geometry.json').read_bytes())
            (case / 'history.csv').write_text('"Inner_Iter","rms[P]","rms[U]","rms[V]","rms[k]","rms[w]","CFx","CFy"\n'
                                             '0,-2,-2,-2,-3,1,0.2,1\n')
            with patch('meshio.read', side_effect=[mesh, surface]), \
                 patch('cfd.transfer_solution.read_restart', return_value=(names, native)):
                report = analyse(case)
            self.assertFalse(report['checks_passed'])  # One iteration cannot establish convergence.
            self.assertTrue(all(w['passed'] for w in report['wall_resolution'].values()))
            with np.load(case / 'slice.npz') as data:
                np.testing.assert_allclose(data['speed_delta'], 0., atol=1e-14)
                np.testing.assert_allclose(data['angle_deg'], 0., atol=1e-14)
                np.testing.assert_allclose(data['turbulence_percent'], 100 * np.sqrt(2 * .02 / 3) / speed)
            self.assertEqual(json.loads((case / 'plot_context.json').read_text())['title_suffix'], ' · 2D')

    def test_field_stability_rejects_changing_fields_and_requires_a_full_window(self):
        names = ['x', 'y', 'Velocity_x', 'Velocity_y', 'Turb_Kin_Energy']
        native = np.array([[0., 0., -.8, .6, .01], [1., 1., -.8, .6, .01]])
        old = native.copy()
        manifest = {'started_at_utc': (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat(),
                    'reference_velocity_m_s': [-8., 6., 0.]}
        with tempfile.TemporaryDirectory() as folder:
            case = Path(folder)
            (case / 'restart.dat').write_bytes(b'current')
            (case / 'restart_000200.dat').write_bytes(b'current')
            self.assertFalse(field_stability(case, manifest, names, native, 200)['passed'])
            (case / 'restart_000100.dat').write_bytes(b'previous')
            with patch('cfd.transfer_solution.read_restart', return_value=(names, old)):
                self.assertTrue(field_stability(case, manifest, names, native, 200)['passed'])
                old[:, 2] -= .1
                self.assertFalse(field_stability(case, manifest, names, native, 200)['passed'])
                old[0, 0] = 2.
                with self.assertRaisesRegex(ValueError, 'same 2D mesh'):
                    field_stability(case, manifest, names, native, 200)


if __name__ == '__main__':
    unittest.main()
