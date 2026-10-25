"""The plotting path must work independently of CFD and its checks."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from cfd.plot_data import load_plot_data


class PlotPipelineTests(unittest.TestCase):
    def test_preliminary_checkpoint_uses_matching_history_without_full_checks(self):
        import meshio
        from cfd.plot_data import prepare_plot_data
        from cfd.rans_mesh import SHAPES
        from cfd.case import SAILS,apparent_wind
        settings=SimpleNamespace(height=4.,wind_speed=8.,wind_angle=41.,boat_speed=6.,
                                 turbulence_intensity=.03,turbulence_length=.5)
        wind=apparent_wind(settings)
        points=np.array([[-10.,-10.,0.],[10.,-10.,0.],[0.,10.,0.],[0.,0.,8.]])
        mesh=meshio.Mesh(points,[('tetra',[[0,1,2,3]])])
        fields=['x','y','z','Pressure','Velocity_x','Velocity_y','Velocity_z','Turb_Kin_Energy','Omega']
        native=np.zeros((4,9));native[:,:3]=points;native[:,4:7]=wind/np.linalg.norm(wind)
        native[:,7]=.00135;native[:,8]=1.
        with tempfile.TemporaryDirectory() as folder:
            case=Path(folder);checkpoint=case/'restart_000300.dat'
            checkpoint.write_bytes(b'original saved field');(case/'yacht.msh').touch()
            (case/'run_parameters.json').write_text('{"status":"failed"}')
            (case/'geometry.json').write_text(json.dumps({'geometry_version':4,
                'sail_definitions':SAILS,'sail_shapes':SHAPES,
                'hull':[[-5,-1],[5,-1],[5,1],[-5,1]]}))
            (case/'yacht.cfg').write_text('INC_DENSITY_INIT=1.225\nREF_AREA=40\n')
            (case/'history.csv').write_text('Inner_Iter,rms[P],rms[U],rms[V],rms[W],rms[k],rms[w],CFx,CFy\n'
                '300,-7,-7,-7,-7,-8,-3,.2,1\n333,-8,-8,-8,-8,-9,-4,.5,2\n')
            with patch('meshio.read',return_value=mesh), \
                 patch('cfd.transfer_solution.read_restart',return_value=(fields,native)) as read, \
                 patch('cfd.compare_snapshots.verify_saved_fields',side_effect=AssertionError('Unexpected field check')), \
                 patch('cfd.rans_checks.convergence',side_effect=AssertionError('Unexpected convergence check')), \
                 patch('cfd.rans_checks.surface_resolution',side_effect=AssertionError('Unexpected wall check')), \
                 patch('cfd.rans_checks.surface_loading',side_effect=AssertionError('Unexpected surface check')), \
                 patch('cfd.marine_checks.background_profile',side_effect=AssertionError('Unexpected background check')), \
                 patch('subprocess.Popen',side_effect=AssertionError('Unexpected solver')):
                report=prepare_plot_data(settings,case,wind,preliminary=True,checkpoint=checkpoint)
                read.assert_called_once_with(checkpoint)
            context,arrays=load_plot_data(case)
            self.assertFalse(context['checks_passed'])
            self.assertIn('iteration 300',context['status_text'])
            self.assertTrue(context['preliminary'])
            self.assertEqual(report['last_history']['Inner_Iter'],300)
            self.assertAlmostEqual(report['whole_model_forces']['drive_N'],.2*.5*1.225*np.linalg.norm(wind)**2*40)
            self.assertFalse(report['field_stability']['performed'])
            self.assertGreater(len(arrays['triangles']),0)
            self.assertEqual(checkpoint.read_bytes(),b'original saved field')
            self.assertEqual((case/'run_parameters.json').read_text(),'{"status":"failed"}')

    def test_post_simulation_preparation_saves_analysis_and_plot_snapshot(self):
        import meshio
        from cfd.plot_data import prepare_plot_data
        from cfd.rans_mesh import SHAPES
        from cfd.case import SAILS,apparent_wind
        settings=SimpleNamespace(height=4.,wind_speed=8.,wind_angle=41.,boat_speed=6.,
                                 turbulence_intensity=.03,turbulence_length=.5)
        wind=apparent_wind(settings)
        points=np.array([[-10.,-10.,0.],[10.,-10.,0.],[0.,10.,0.],[0.,0.,8.]])
        mesh=meshio.Mesh(points,[('tetra',[[0,1,2,3]])])
        fields=['x','y','z','Pressure','Velocity_x','Velocity_y','Velocity_z','Turb_Kin_Energy','Omega']
        native=np.zeros((4,9));native[:,:3]=points;native[:,4:7]=wind/np.linalg.norm(wind)
        native[:,7]=.00135;native[:,8]=1.
        with tempfile.TemporaryDirectory() as folder:
            case=Path(folder)
            (case/'restart.dat').touch();(case/'yacht.msh').touch()
            (case/'geometry.json').write_text(json.dumps({'geometry_version':4,
                'sail_definitions':SAILS,'sail_shapes':SHAPES,
                'hull':[[-5,-1],[5,-1],[5,1],[-5,1]]}))
            (case/'yacht.cfg').write_text('INC_DENSITY_INIT=1.225\nREF_AREA=40\n')
            (case/'history.csv').write_text('Inner_Iter,rms[P],rms[U],rms[V],rms[W],rms[k],rms[w],CFx,CFy\n0,-8,-8,-8,-8,-12,-6,.2,1\n')
            with patch('meshio.read',return_value=mesh), \
                 patch('cfd.transfer_solution.read_restart',return_value=(fields,native)), \
                 patch('cfd.compare_snapshots.verify_saved_fields',return_value={'passed':True}) as stability, \
                 patch('cfd.rans_checks.convergence',return_value={'passed':True}) as convergence, \
                 patch('cfd.rans_checks.surface_resolution',return_value={'walls_passed':True}) as resolution, \
                 patch('cfd.rans_checks.surface_loading',return_value={'integration_check':{'passed':True}}) as loading:
                prepare_plot_data(settings,case,wind)
                for check in (stability,convergence,resolution,loading):check.assert_called_once()
            context,arrays=load_plot_data(case)
            self.assertTrue(context['checks_passed'])
            self.assertEqual(context['height_m'],4.)
            np.testing.assert_allclose(arrays['turbulence_percent'],3.)
            self.assertTrue((case/'report.json').exists())

    def test_snapshot_loads_without_any_cfd_files(self):
        with tempfile.TemporaryDirectory() as folder:
            case=Path(folder)
            context={'slice_file':'slice.npz','height_m':4.}
            (case/'plot_context.json').write_text(json.dumps(context))
            velocity=np.array([[1.,2.,3.]])
            np.savez_compressed(case/'slice.npz',velocity=velocity)
            loaded,arrays=load_plot_data(case)
            self.assertEqual(loaded,context)
            np.testing.assert_array_equal(arrays['velocity'],velocity)
            self.assertEqual(len(list(case.iterdir())),2)



if __name__=='__main__':
    unittest.main()
