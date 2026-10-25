"""Atmospheric shear and initial-field physics checks; no solver."""
import json
from pathlib import Path
import tempfile
import unittest
import numpy as np
from cfd.marine_inflow import coare_neutral_roughness, initial_values, inlet_values, marine_profile, write_initial_field

class AtmosphericPhysicsTests(unittest.TestCase):
    def test_matches_reference_and_water_velocity_in_boat_frame(self):
        p = marine_profile()
        self.assertAlmostEqual(p.at(10.)["true_speed_m_s"], 8.)
        self.assertAlmostEqual(p.at(10.)["apparent_angle_deg"], 29.908220517279403)
        np.testing.assert_allclose(p.at(0.)["apparent_velocity_m_s"], [-6 * 1852 / 3600, 0, 0])

    def test_shear_changes_apparent_direction_by_translation(self):
        p = marine_profile()
        w = p.at([1.15, 4., 10.])
        self.assertTrue(np.all(np.diff(w["apparent_angle_deg"]) > 0))
        stationary = marine_profile(boat_speed_knots=0.)
        np.testing.assert_allclose(stationary.at([1.15, 4., 10.])["apparent_angle_deg"], 41.)
        np.testing.assert_allclose(p.turbulence_at([1., 10.])["k_m2_s2"], stationary.turbulence_at([1., 10.])["k_m2_s2"])

    def test_neutral_momentum_and_k_production_balance(self):
        p = marine_profile(); z = np.array([1.15, 4., 10., 20.])
        t = p.turbulence_at(z)
        derivative = p.friction_velocity_m_s / (.41 * (z + p.roughness_length_m))
        viscosity = np.asarray(t["eddy_viscosity_m2_s"])
        np.testing.assert_allclose(viscosity * derivative, p.friction_velocity_m_s ** 2)
        production = viscosity * derivative ** 2
        destruction = .09 * np.asarray(t["k_m2_s2"]) * t["omega_s_inv"]
        np.testing.assert_allclose(production, destruction)
        # Does not assert an exact solution of SST's full blended omega PDE.

    def test_charnock_viscous_and_log_law_use_one_friction_velocity(self):
        r = coare_neutral_roughness()
        u = r["friction_velocity_m_s"]
        self.assertAlmostEqual(r["roughness_length_m"], .011 * u ** 2 / 9.81 + .11 * r["kinematic_viscosity_m2_s"] / u)
        self.assertGreater(r["roughness_length_m"], 1e-5)
        self.assertLess(r["roughness_length_m"], .001)

    def test_initial_and_inlet_fields_are_identical_physically(self):
        xyz = np.array([[5., 0., z] for z in [1.15, 4., 10., 28.]])
        p = marine_profile(); initial = initial_values(xyz, p); inlet = inlet_values(xyz, p)
        np.testing.assert_allclose(inlet[:, 1:2] * inlet[:, 2:5], initial[:, 1:4])
        np.testing.assert_array_equal(inlet[:, 5:7], initial[:, 4:6])
        self.assertGreater(np.ptp(initial[:, 1]), .5)

    def test_native_initial_field_roundtrip_and_wall_frame(self):
        import meshio
        from cfd.transfer_solution import read_restart
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            points = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 4.]], dtype=float)
            mesh = meshio.Mesh(points, [("tetra", [[0, 1, 2, 3]]), ("triangle", [[0, 1, 2]])],
                               cell_data={"gmsh:physical": [np.array([1]), np.array([11])],
                                          "gmsh:geometrical": [np.array([1]), np.array([11])]})
            meshio.write(folder / "test.msh", mesh, file_format="gmsh22", binary=True)
            p = marine_profile(); result = write_initial_field(folder / "test.msh", folder / "start.dat", p)
            names, raw = read_restart(folder / "start.dat")
            scale = result["nondimensional_reference_speed_m_s"]
            np.testing.assert_array_equal(raw[:, :3], points)
            np.testing.assert_allclose(raw[:3, 4] * scale, -6 * 1852 / 3600)
            self.assertTrue(np.all(raw[:3, 7] == 0))
            expected = initial_values(points[3:], p)[0]
            np.testing.assert_allclose(raw[3, 4:7] * scale, expected[1:4])
            self.assertAlmostEqual(raw[3, 7] * scale ** 2, expected[4])
            self.assertAlmostEqual(raw[3, 8] * scale, expected[5])
            del raw  # Release the Windows memory map before TemporaryDirectory cleanup.
