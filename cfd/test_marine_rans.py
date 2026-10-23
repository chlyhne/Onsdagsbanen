"""Marine coupling tests use analytical fields and mocked solver execution."""
from io import StringIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import meshio
import numpy as np

from cfd.marine_inflow import initial_values, marine_profile
from cfd.marine_mesh import augment
from cfd.marine_rans import configuration, read_profile, run, sha, write_inlets


def box():
    xy=np.array([[0.,0.],[2.,0.],[2.,2.],[0.,2.]])
    points=np.concatenate([np.column_stack((xy,np.full(4,z))) for z in (0.,.4,1.2)])
    hexes=np.array([np.arange(8),np.arange(4,12)])
    faces=[[3,2,1,0],[8,9,10,11]]
    tags=[11,10]
    for j in (0,4):
        for a,b in ((0,1),(1,2),(2,3),(3,0)):
            faces.append([j+a,j+b,j+b+4,j+a+4]);tags.append(10)
    return meshio.Mesh(points,[('hexahedron',hexes),('quad',faces)],
                       cell_data={'gmsh:physical':[np.ones(2,dtype=int),np.array(tags)]})


class MarineMeshTests(unittest.TestCase):
    def test_extrusion_preserves_volume_closes_boundaries_and_resolves_sea(self):
        original=box()
        mesh,meta=augment(original,first=1e-4,layers=8,growth=1.2,top_height=3.,top_layers=3)
        np.testing.assert_array_equal(mesh.points[8:12],original.points[8:12])
        volume=0.
        for b in mesh.cells:
            if b.type!='hexahedron':continue
            p=mesh.points[b.data]
            # These box cells have horizontal rectangular sections.
            volume+=np.sum((p[:,1,0]-p[:,0,0])*(p[:,3,1]-p[:,0,1])*(p[:,4,2]-p[:,0,2]))
        self.assertAlmostEqual(volume,12.,places=12)
        boundary=[b.data for b in mesh.cells if b.type in ('triangle','quad')]
        edges=np.concatenate([np.stack((f,np.roll(f,-1,axis=1)),axis=-1).reshape(-1,2) for f in boundary])
        _,counts=np.unique(np.sort(edges,axis=1),axis=0,return_counts=True)
        self.assertTrue(np.all(counts==2))
        z=np.unique(mesh.points[:,2])
        self.assertAlmostEqual(z[1]-z[0],1e-4,places=12)
        self.assertGreater(meta['sea']['minimum_continuous_vertical_map_derivative'],0)

    def test_inlets_match_the_initial_profile_with_dimensional_turbulence(self):
        mesh,_=augment(box(),first=1e-4,layers=8,growth=1.2,top_height=3.,top_layers=3)
        profile=marine_profile()
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'inlet.dat';write_inlets(mesh,path,profile)
            lines=path.read_text().splitlines();index=1
            for marker in range(2):
                n=int(lines[index+1].split('=')[1]);index+=4
                data=np.loadtxt(StringIO('\n'.join(lines[index:index+n])));index+=n
                expected=initial_values(data[:,:3],profile)
                np.testing.assert_allclose(data[:,4,None]*data[:,5:8],expected[:,1:4],atol=1e-13)
                nonwall=data[:,2]>1e-12
                np.testing.assert_allclose(data[nonwall,8:10],expected[nonwall,4:6],atol=1e-13)


class MarinePhysicsTests(unittest.TestCase):
    def test_top_ambient_and_native_nondimensionalization_agree(self):
        profile=marine_profile();z=84.
        text=configuration('SOLVER= INC_RANS\nINC_VELOCITY_INIT=(1,0,0)\nMARKER_EULER=(sea)\n',profile,z)
        cfg={line.split('=',1)[0].strip():line.split('=',1)[1].strip() for line in text.splitlines() if '=' in line}
        velocity=np.fromstring(cfg['INC_VELOCITY_INIT'].strip('()'),sep=',')
        np.testing.assert_allclose(velocity,profile.at(z)['apparent_velocity_m_s'])
        speed=np.linalg.norm(velocity);k=1.5*(speed*float(cfg['FREESTREAM_TURBULENCEINTENSITY']))**2
        omega=k/(float(cfg['FREESTREAM_TURB2LAMVISCRATIO'])*(1.81e-5/1.225))
        self.assertAlmostEqual(k,profile.turbulence_at(z)['k_m2_s2'])
        self.assertAlmostEqual(omega,profile.turbulence_at(z)['omega_s_inv'])
        self.assertNotIn('MARKER_EULER',cfg)
        self.assertEqual(cfg['MARKER_MOVING'],'(sea)')
        self.assertNotIn('sea',cfg['MARKER_MONITORING'])
        for field in ('RMS_VELOCITY-Z', 'FORCE_X', 'FORCE_Y'):
            self.assertIn(field,cfg['SCREEN_OUTPUT'])

    def test_background_check_detects_shear_loss(self):
        from cfd.marine_checks import background_profile
        profile=marine_profile();scale=profile.at(84.)['apparent_speed_m_s']
        points=np.array([[x,y,z] for x,y in [(25.,-25.),(0.,-30.),(-35.,-25.),(-65.,5.)] for z in (2.,4.,10.)])
        values=initial_values(points,profile)
        native=np.column_stack((points,values))
        native[:,4:7]/=scale;native[:,7]/=scale**2;native[:,8]/=scale
        self.assertTrue(background_profile(points,native,profile,scale)['passed'])
        native[:,4:7]=np.asarray(profile.at(10.)['apparent_velocity_m_s'])/scale
        self.assertFalse(background_profile(points,native,profile,scale)['passed'])

    def test_uniform_background_is_white_at_slice_height_despite_top_reference(self):
        from cfd.plot_data import prepare_plot_data,load_plot_data
        from cfd.rans_mesh import SHAPES
        from cfd.case import SAILS
        profile=marine_profile();top=profile.at(84.);scale=top['apparent_speed_m_s']
        wind=np.asarray(profile.at(4.)['apparent_velocity_m_s']);k=profile.turbulence_at(4.)['k_m2_s2']
        points=np.array([[-10.,-10.,0.],[10.,-10.,0.],[0.,10.,0.],[0.,0.,8.]])
        mesh=meshio.Mesh(points,[('tetra',[[0,1,2,3]])])
        names=['x','y','z','Pressure','Velocity_x','Velocity_y','Velocity_z','Turb_Kin_Energy','Omega']
        native=np.zeros((4,9));native[:,:3]=points;native[:,4:7]=wind/scale;native[:,7]=k/scale**2;native[:,8]=1.
        settings=SimpleNamespace(height=4.,wind_speed=8.,wind_angle=41.,boat_speed=6.,turbulence_length=None)
        with tempfile.TemporaryDirectory() as folder:
            case=Path(folder);(case/'restart.dat').touch();(case/'yacht.msh').touch()
            (case/'marine_case.json').write_text(json.dumps({'profile':profile.metadata(),
                'native_reference_velocity_m_s':top['apparent_velocity_m_s'],'coefficient_reference_height_m':84.}))
            (case/'geometry.json').write_text(json.dumps({'geometry_version':4,'sail_definitions':SAILS,'sail_shapes':SHAPES,
                'hull':[[-5,-1],[5,-1],[5,1],[-5,1]]}))
            (case/'yacht.cfg').write_text('INC_DENSITY_INIT=1.225\nREF_AREA=40\n')
            (case/'history.csv').write_text('Inner_Iter,rms[P],rms[U],rms[V],rms[W],rms[k],rms[w],CFx,CFy\n0,-8,-8,-8,-8,-12,-6,.2,1\n')
            with patch('meshio.read',return_value=mesh),patch('cfd.transfer_solution.read_restart',return_value=(names,native)), \
                 patch('cfd.compare_snapshots.verify_saved_fields',return_value={'passed':True}), \
                 patch('cfd.rans_checks.convergence',return_value={'passed':True}), \
                 patch('cfd.rans_checks.surface_resolution',return_value={'walls_passed':True}), \
                 patch('cfd.rans_checks.surface_loading',return_value={'integration_check':{'passed':True}}), \
                 patch('cfd.marine_checks.background_profile',return_value={'passed':True}):
                report=prepare_plot_data(settings,case,np.asarray(top['apparent_velocity_m_s']))
            context,arrays=load_plot_data(case)
            np.testing.assert_allclose(arrays['speed_delta'],0.,atol=1e-13)
            np.testing.assert_allclose(arrays['angle_deg'],0.,atol=1e-13)
            np.testing.assert_allclose(arrays['turbulence_percent'],context['freestream_turbulence_percent'])
            self.assertTrue(report['checks_passed'])


class MarineExecutionTests(unittest.TestCase):
    def make_case(self, case):
        p = marine_profile(); top = p.at(84.)
        (case/'yacht.su2').write_text('mesh')
        manifest = {'profile':p.metadata(),'native_reference_velocity_m_s':top['apparent_velocity_m_s'],
                    'native_reference_speed_m_s':top['apparent_speed_m_s'],'mesh_sha256':sha(case/'yacht.su2')}
        (case/'marine_case.json').write_text(json.dumps(manifest))
        (case/'yacht.cfg').write_text('SOLUTION_FILENAME=initial_atmosphere\nITER=3000\n')
        (case/'geometry.json').write_text(json.dumps({'sail_definitions':[[0,3.7,10.5,12.],[3.4,2.8,8.5,20.]],
            'sail_shapes':[{'twist_deg':6.},{'twist_deg':8.}]}))

    def test_prepare_and_plot_commands_never_launch_solver(self):
        from cfd.marine_rans import main
        with patch('subprocess.Popen',side_effect=AssertionError('Unexpected CFD')):
            with patch.object(sys,'argv',['marine_rans','prepare']),patch('cfd.marine_rans.prepare') as prepare:
                main();prepare.assert_called_once()
            with patch.object(sys,'argv',['marine_rans','plot']),patch('cfd.plot_wind.render') as render:
                main();render.assert_called_once()

    def test_explicit_run_connects_solver_and_postprocessing(self):
        with tempfile.TemporaryDirectory() as directory:
            case=Path(directory);self.make_case(case)
            with patch('cfd.solver.solver_path',return_value=Path('dummy_SU2')), \
                 patch('cfd.marine_rans.run_solver') as solver, \
                 patch('cfd.plot_data.prepare_plot_data',return_value={'checks_passed':True}) as analysis, \
                 patch('cfd.plot_wind.render') as render:
                run(case,threads=3,iterations=600)
                self.assertEqual(solver.call_args.args[0],['dummy_SU2','yacht.cfg','-t','3'])
                self.assertIn('RMS_VELOCITY-Z', (case/'yacht.cfg').read_text())
                analysis.assert_called_once();render.assert_called_once_with(case)
            self.assertEqual(json.loads((case/'run_parameters.json').read_text())['status'],'converged')

    def test_retry_header_only_history_preserves_failed_start(self):
        for restart in (False, True):
            with self.subTest(restart=restart), tempfile.TemporaryDirectory() as directory:
                case=Path(directory);self.make_case(case)
                originals={'history.csv':'"Inner_Iter","rms[P]"\n\n',
                           'solver.log':'Stopped during geometry preprocessing\n',
                           'run_parameters.json':'{"status":"failed"}'}
                for name,text in originals.items():
                    (case/name).write_text(text)
                originals['yacht.cfg']=(case/'yacht.cfg').read_text()
                originals['marine_case.json']=(case/'marine_case.json').read_text()
                with patch('cfd.solver.solver_path',return_value=Path('dummy_SU2')), \
                     patch('cfd.marine_rans.run_solver') as solver, patch('cfd.marine_rans.analyse'):
                    run(case,iterations=600,restart=restart)
                    solver.assert_called_once()
                parameters=json.loads((case/'run_parameters.json').read_text())
                archived=Path(parameters['previous_startup_attempt'])
                for name,text in originals.items():
                    self.assertEqual((archived/name).read_text(),text)
                self.assertIn('SOLUTION_FILENAME= initial_atmosphere',(case/'yacht.cfg').read_text())

    def test_real_iteration_history_without_checkpoint_is_preserved(self):
        for restart in (False,True):
            with self.subTest(restart=restart), tempfile.TemporaryDirectory() as directory:
                case=Path(directory);self.make_case(case)
                history='Inner_Iter,rms[P]\n0,-4\n'
                (case/'history.csv').write_text(history)
                with patch('cfd.marine_rans.run_solver') as solver:
                    with self.assertRaisesRegex(ValueError,'restart.dat is missing'):
                        run(case,restart=restart)
                    solver.assert_not_called()
                self.assertEqual((case/'history.csv').read_text(),history)
                self.assertFalse((case/'failed_starts').exists())

    def test_checkpoint_without_history_still_requires_explicit_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            case=Path(directory);self.make_case(case)
            (case/'restart.dat').write_text('saved field')
            with patch('cfd.marine_rans.run_solver') as solver:
                with self.assertRaisesRegex(ValueError,'explicitly use --restart'):
                    run(case)
                solver.assert_not_called()
            with patch('cfd.solver.solver_path',return_value=Path('dummy_SU2')), \
                 patch('cfd.marine_rans.run_solver') as solver, patch('cfd.marine_rans.analyse'):
                run(case,restart=True)
                solver.assert_called_once()
            self.assertIn('SOLUTION_FILENAME= restart',(case/'yacht.cfg').read_text())

    def test_iteration_snapshot_alone_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            case=Path(directory);self.make_case(case)
            checkpoint=case/'restart_000100.dat';checkpoint.write_text('saved field')
            with patch('cfd.marine_rans.run_solver') as solver:
                with self.assertRaisesRegex(ValueError,'restart.dat is missing'):
                    run(case)
                solver.assert_not_called()
            self.assertEqual(checkpoint.read_text(),'saved field')

    def test_solver_failure_records_returncode(self):
        with tempfile.TemporaryDirectory() as directory:
            case=Path(directory);self.make_case(case)
            error=subprocess.CalledProcessError(7,['dummy_SU2'])
            with patch('cfd.solver.solver_path',return_value=Path('dummy_SU2')), \
                 patch('cfd.marine_rans.run_solver',side_effect=error), \
                 patch('cfd.marine_rans.analyse') as analyse:
                with self.assertRaises(subprocess.CalledProcessError):
                    run(case)
                analyse.assert_not_called()
            parameters=json.loads((case/'run_parameters.json').read_text())
            self.assertEqual(parameters['status'],'failed')
            self.assertEqual(parameters['solver_returncode'],7)
            self.assertIn('CalledProcessError',parameters['solver_error'])


if __name__=='__main__':unittest.main()
