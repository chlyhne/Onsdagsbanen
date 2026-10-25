"""The wind-speed workflow must reuse its mesh and initialize physical shear."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import meshio
import numpy as np

from cfd.marine_inflow import initial_values, marine_profile
from cfd.marine_rans import read_profile, sha
from cfd.run_wind import prepare
from cfd.transfer_solution import read_restart


class WindCaseTests(unittest.TestCase):
    def make_source(self, root):
        source=root/'source'
        source.mkdir()
        points=np.array([[0,0,0],[1,0,0],[0,1,0],[0,0,4],[.2,.2,2.]],dtype=float)
        mesh=meshio.Mesh(points,[('tetra',[[0,1,2,3]]),
            ('triangle',[[0,1,2],[0,1,3],[0,2,3],[1,2,3]])],
            cell_data={'gmsh:physical':[np.array([10]),np.array([11,15,16,12])],
                       'gmsh:geometrical':[np.array([10]),np.array([11,15,16,12])]})
        meshio.write(source/'yacht.msh',mesh,file_format='gmsh22',binary=True)
        (source/'yacht.su2').write_text('unchanged mesh')
        p=marine_profile(8.)
        manifest={'profile':p.metadata(),'coefficient_reference_height_m':84.,
                  'mesh_sha256':sha(source/'yacht.su2'),'free_flow':{'obsolete':True}}
        (source/'marine_case.json').write_text(json.dumps(manifest))
        for name in ('geometry.json','mesh_metadata.json'):
            (source/name).write_text('{}')
        (source/'airfoil_validation.json').write_text('{"passed":true}')
        (source/'yacht.cfg').write_text('SOLVER= INC_RANS\nSOLUTION_FILENAME= restart\n')
        return source,points,p

    def test_new_speed_keeps_mesh_and_roughness_and_rebuilds_consistent_shear(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            source,points,original=self.make_source(root)
            case=root/'five'
            with patch('subprocess.Popen',side_effect=AssertionError('Unexpected solver')):
                prepare(source,case,5.,3000)
            manifest=json.loads((case/'marine_case.json').read_text())
            profile=read_profile(manifest)
            self.assertEqual(profile.reference_speed_m_s,5.)
            self.assertEqual(profile.roughness_length_m,original.roughness_length_m)
            self.assertEqual(profile.true_angle_deg,original.true_angle_deg)
            self.assertEqual(profile.boat_speed_knots,original.boat_speed_knots)
            self.assertNotIn('free_flow',manifest)
            self.assertEqual(sha(source/'yacht.msh'),sha(case/'yacht.msh'))
            self.assertEqual(sha(case/'yacht.su2'),manifest['mesh_sha256'])
            fields,values=read_restart(case/'initial_atmosphere.dat')
            np.testing.assert_allclose(values[:,:3],points)
            scale=manifest['native_reference_speed_m_s']
            expected=initial_values(points[[4]],profile)[0]
            expected[1:4]/=scale
            expected[4]/=scale**2
            expected[5]/=scale
            np.testing.assert_allclose(values[4,3:9],expected)
            del values  # Release the memory-mapped file before Windows cleanup.
            self.assertIn('SOLUTION_FILENAME= initial_atmosphere',(case/'yacht.cfg').read_text())
            self.assertFalse((case/'restart.dat').exists())
            with self.assertRaisesRegex(ValueError,'new case directory'):
                prepare(source,case,5.,3000)

    def test_bad_wind_does_not_create_case(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for speed in (0.,-1.,float('nan')):
                with self.assertRaisesRegex(ValueError,'positive and finite'):
                    prepare(root/'absent_source',root/'new',speed,3000)
                self.assertFalse((root/'new').exists())
