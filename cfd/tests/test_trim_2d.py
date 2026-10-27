"""Fixed-twist geometry, single-section forces, search recovery and handoff checks."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import meshio
import numpy as np

from cfd.case import case_chord_trim
from cfd.rans_mesh import FOOT, camber_profile, _skin_points, entry_angle
from cfd.section_2d import REFERENCE, configuration, mesh_2d, plot_geometry, wall_markers
from cfd.marine_rans import read_profile
from cfd.section_loads import edge_force
from cfd.trim_2d import (BudgetExpired, PARAMETERS, evaluate, export_best, representative_height,
                        section_forces, prepare_study, read, run_search, save,
                        section_key, trimmed_geometry, verify_inputs, parameter_bounds, initial_angles, starting_simplex)


def original():
    return read(REFERENCE/'geometry.json')


INITIAL = initial_angles(original(), read(REFERENCE/'marine_case.json'), representative_height(original()))


class ShapeTests(unittest.TestCase):
    def test_prepare_seeds_previous_trim_and_builds_simplex_around_it(self):
        seed = Path('cfd/trimmed_reference')
        expected = [case_chord_trim(read(seed/'geometry.json'), i, FOOT) for i in range(2)]
        with tempfile.TemporaryDirectory() as directory:
            study = Path(directory)/'study'
            config = prepare_study(REFERENCE, study, initial_trim=seed)
            np.testing.assert_allclose(config['initial'], expected)
            np.testing.assert_allclose(config['initial_simplex'][0], expected)
            np.testing.assert_allclose(config['initial_simplex'][1], [expected[0]+3., expected[1]])
            np.testing.assert_allclose(config['initial_simplex'][2], [expected[0], expected[1]+3.])
            self.assertEqual(config['initial_trim_source'], str(seed))
            self.assertIsNone(config['initial_jib_aoa_deg'])
            verify_inputs(study, config)

    def test_seed_gives_nine_degree_jib_chord_aoa_and_main_three_degrees_tighter(self):
        g=original()
        profile=read(REFERENCE/'marine_case.json')
        for height in (2.,representative_height(g),7.):
            initial=initial_angles(g,profile,height)
            shaped=trimmed_geometry(g,initial)
            main=case_chord_trim(shaped,0,height)
            jib=case_chord_trim(shaped,1,height)
            awa=read_profile(profile).at(height)['apparent_angle_deg']
            self.assertAlmostEqual(awa-jib,9.)
            self.assertAlmostEqual(jib-main,3.)
            self.assertLess(initial[0],initial[1])
            for shape in shaped['sail_shapes']:
                self.assertEqual(shape['twist_deg'],2.)
        changed=deepcopy(profile)
        changed['profile']['reference_speed_m_s']=7.
        self.assertNotEqual(initial_angles(g,changed,4.52),initial_angles(g,profile,4.52))

    def test_initial_simplex_is_small_and_feasible_even_at_angle_bounds(self):
        bounds=parameter_bounds(original())
        for initial in ([0.,0.],[30.,30.],INITIAL):
            simplex=starting_simplex(initial,bounds)
            self.assertEqual(simplex.shape,(3,2))
            self.assertTrue(np.all(simplex>=0.))
            self.assertTrue(np.all(simplex<=30.))
            self.assertEqual(len(np.unique(simplex,axis=0)),3)
            self.assertEqual(np.max(np.abs(simplex-np.array(initial))),3.)

    def test_two_sheeting_angles_and_fixed_total_twist_for_different_sail_heights(self):
        g=original()
        np.testing.assert_allclose(parameter_bounds(g),[[0.,30.],[0.,30.]])
        maximum=trimmed_geometry(g,[30.,30.])
        for i,(sail,shape) in enumerate(zip(maximum['sail_definitions'],maximum['sail_shapes'])):
            self.assertEqual(shape['twist_deg'],2.)
            self.assertAlmostEqual(case_chord_trim(maximum,i,FOOT),30.)
            self.assertAlmostEqual(case_chord_trim(maximum,i,FOOT+sail[2]),32.)
            self.assertAlmostEqual(case_chord_trim(maximum,i,FOOT+sail[2]/2),31.)
        zero=trimmed_geometry(g,[0.,0.])
        self.assertEqual([s['twist_deg'] for s in zero['sail_shapes']],[2.,2.])
        for i in range(2):
            for value in (-.01,30.01):
                invalid=np.array(INITIAL)
                invalid[i]=value
                with self.assertRaisesRegex(ValueError,'between 0 and 30'):
                    trimmed_geometry(g,invalid)
        with self.assertRaisesRegex(ValueError,'two finite'):
            trimmed_geometry(g,[5.,.6,12.,.8])

    def test_max_depth_at_45_percent_in_every_section(self):
        geometry = trimmed_geometry(original(), [4., 12.])
        for i, (sail, shape) in enumerate(zip(geometry['sail_definitions'], geometry['sail_shapes'])):
            for fraction in (0., .5, 1.):
                height = FOOT+fraction*sail[2]
                p0 = _skin_points(sail, height, 0., shape, 0)[0]
                p1 = _skin_points(sail, height, 1., shape, 0)[0]
                peak = _skin_points(sail, height, .45, shape, 0)[0]
                chord = p1-p0
                tangent = chord/np.linalg.norm(chord)
                offset = peak-p0
                self.assertAlmostEqual(offset@tangent/np.linalg.norm(chord), .45)
                self.assertAlmostEqual(np.linalg.norm(offset-(offset@tangent)*tangent)/np.linalg.norm(chord), .12)
                self.assertAlmostEqual(case_chord_trim(geometry, i, height), [4.,12.][i]+2.*(height-FOOT)/sail[2])
        np.testing.assert_allclose(camber_profile([0., .45, 1.], geometry['sail_shapes'][0])[0], [0., .12, 0.], atol=1e-15)
        self.assertEqual(float(camber_profile(.45, geometry['sail_shapes'][0])[1]), 0.)

    def test_old_geometry_is_unchanged_and_entry_diagnostic_matches_new_profile(self):
        g = original()
        before = deepcopy(g)
        trimmed_geometry(g, INITIAL)
        self.assertEqual(g, before)
        shape = g['sail_shapes'][0]
        t = np.linspace(0., 1., 101)
        depth, slope = camber_profile(t, shape)
        np.testing.assert_allclose(depth, 4*.08*t*(1-t), atol=1e-16)
        np.testing.assert_allclose(slope, 4*.08*(1-2*t), atol=1e-16)
        shape = {**shape, 'camber': .12, 'draft_position': .45}
        sail = g['sail_definitions'][0]
        p0 = _skin_points(sail, 4., 0., shape, 0)[0]
        p1 = _skin_points(sail, 4., .05, shape, 0)[0]
        angle = np.degrees(np.arctan2(p1[1]-p0[1], -(p1[0]-p0[0])))
        self.assertAlmostEqual(angle, entry_angle(sail, 4., shape), places=12)
        for p in (0., 1., float('nan')):
            with self.assertRaises(ValueError):
                camber_profile(.5, {**shape, 'draft_position': p})

    def test_upper_mainsail_has_no_phantom_jib_in_mesh_or_configuration(self):
        g = trimmed_geometry(original(), INITIAL)
        g['section_height_m'] = 10.5
        profile = read_profile(read(REFERENCE/'marine_case.json'))
        with tempfile.TemporaryDirectory() as folder:
            case = Path(folder)
            save(case/'geometry.json', g)
            metadata = mesh_2d(case, g, 10.5, sail_size=.06)
            mesh = meshio.read(case/'yacht.msh')
            self.assertEqual(metadata['markers'], {'farfield':10,'mast':12,'main':13})
            tags = np.concatenate([tags for block,tags in zip(mesh.cells,mesh.cell_data['gmsh:physical']) if block.type=='line'])
            self.assertEqual(set(tags), {10,12,13})
            configuration(case, profile, 10.5)
            self.assertIn('MARKER_MONITORING= (mast, main)', (case/'yacht.cfg').read_text())
            self.assertEqual(len(plot_geometry(g,10.5)['sail_sections']),1)


class ForceTests(unittest.TestCase):
    def test_constant_pressure_cancels_and_friction_has_correct_length_and_winding(self):
        xy=np.array([[0.,0.],[2.,0.],[2.,1.],[0.,1.]])
        edges=np.array([[0,1],[1,2],[2,3],[3,0]])
        cp=np.ones(4)*7
        cf=np.tile([.1,-.2],(4,1))
        for e in (edges,edges[:,::-1]):
            pressure,friction=edge_force(xy,e,cp,cf,q=20.)
            np.testing.assert_allclose(pressure,[0.,0.],atol=1e-12)
            np.testing.assert_allclose(friction,[12.,-24.],atol=1e-12)
        cp=xy[:,0]
        pressure,_=edge_force(xy,edges,cp,cf*0,q=20.)
        np.testing.assert_allclose(pressure,[-40.,0.],atol=1e-12)

    def test_one_section_reports_force_per_metre_without_height_or_chord_weighting(self):
        result={'height_m':4.52,'force_N_per_m':[120.,270.]}
        forces=section_forces(4.52,result)
        self.assertEqual(forces,{'drive_N_per_m':120.,'side_force_N_per_m':270.})
        with self.assertRaisesRegex(ValueError,'height'):
            section_forces(5.,result)
        with self.assertRaisesRegex(ValueError,'Invalid'):
            section_forces(4.52,{**result,'force_N_per_m':[float('nan'),270.]})

    def test_representative_height_matches_area_centroid_and_intersects_both_sails(self):
        g=original()
        z=representative_height(g)
        self.assertAlmostEqual(z,4.521615814353244)
        self.assertGreater(z,FOOT)
        self.assertLess(z,FOOT+min(s[2] for s in g['sail_definitions']))



class SearchTests(unittest.TestCase):
    def make_study(self, path):
        with redirect_stdout(io.StringIO()):
            return prepare_study(REFERENCE,path,iterations=3500)

    def test_resume_budget_and_joint_force_objective(self):
        target=np.array(INITIAL)+np.array([3.,3.])
        calls=[]
        def fake(study,config,values,**kwargs):
            calls.append(tuple(values))
            objective=1000.-np.sum(((np.asarray(values)-target)/[3.,3.])**2)
            return {'parameters':list(values),'checks_passed':True,'forces':{'drive_N_per_m':float(objective)},'completed_at_utc':'test'}
        with tempfile.TemporaryDirectory() as folder:
            study=Path(folder)/'study'
            self.make_study(study)
            with patch('cfd.trim_2d.evaluate',side_effect=fake),patch('cfd.solver.solver_path',return_value=Path('fake')),redirect_stdout(io.StringIO()):
                first=run_search(study,max_evaluations=1)
                self.assertEqual(first['status'],'budget_exhausted')
                final=run_search(study,max_evaluations=200)
            self.assertEqual(final['status'],'ballpark_found')
            np.testing.assert_allclose(final['best']['parameters'],target)
            self.assertEqual(len(calls),len(set(calls)))
            self.assertFalse((study/'search.lock').exists())
            self.assertEqual(len((study/'evaluations.csv').read_text().splitlines()),len(calls)+1)

    def test_coarse_search_limits_expensive_evaluations_and_reuses_them_on_resume(self):
        calls=[]
        target=np.array(INITIAL)+[1.7,-1.2]
        def fake(study,config,values,**kwargs):
            calls.append(tuple(values))
            drive=50.-np.sum((np.asarray(values)-target)**2)
            return {'parameters':list(values),'checks_passed':True,'forces':{'drive_N_per_m':float(drive)}}
        with tempfile.TemporaryDirectory() as folder:
            study=Path(folder)/'study';self.make_study(study)
            with patch('cfd.trim_2d.evaluate',side_effect=fake),patch('cfd.solver.solver_path',return_value=Path('fake')),redirect_stdout(io.StringIO()):
                first=run_search(study,max_evaluations=4)
                self.assertEqual(len(calls),4)
                result=run_search(study,max_evaluations=12)
            self.assertLessEqual(len(calls),12)
            self.assertEqual(len(calls),len(set(calls)))
            self.assertGreater(result['best']['forces']['drive_N_per_m'],49.)
            config=read(study/'study.json')
            self.assertEqual(config['angle_tolerance_deg'],1.)
            self.assertEqual(config['relative_force_tolerance'],.01)

    def test_time_budget_preserves_pending_candidate_and_releases_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            study=Path(folder)/'study';self.make_study(study)
            with patch('cfd.trim_2d.evaluate',side_effect=BudgetExpired),patch('cfd.solver.solver_path',return_value=Path('fake')),redirect_stdout(io.StringIO()):
                result=run_search(study)
            self.assertEqual(result['status'],'budget_exhausted')
            self.assertEqual(result['active_parameters'],INITIAL)
            self.assertEqual(result['evaluations'],[])
            self.assertFalse((study/'search.lock').exists())

    def test_altered_numerical_settings_cannot_mix_with_existing_evaluations(self):
        with tempfile.TemporaryDirectory() as folder:
            study=Path(folder)/'study';config=self.make_study(study)
            config['iterations']+=500
            with self.assertRaisesRegex(ValueError,'configuration changed'):
                verify_inputs(study,config)

    def test_existing_search_lock_prevents_a_second_writer(self):
        with tempfile.TemporaryDirectory() as folder:
            study=Path(folder)/'study';self.make_study(study)
            (study/'search.lock').mkdir()
            save(study/'search.lock/owner.json',{'pid':123})
            with patch('cfd.solver.solver_path',return_value=Path('fake')):
                with self.assertRaisesRegex(RuntimeError,'ensure no search'):
                    run_search(study)
            self.assertEqual(read(study/'search.lock/owner.json'),{'pid':123})

    def test_rejected_candidate_cannot_become_best_and_changed_inputs_cannot_reuse_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            study=Path(folder)/'study'
            config=self.make_study(study)
            rejected={'parameters':INITIAL,'checks_passed':False,'forces':{'drive_N_per_m':1e10}}
            with patch('cfd.trim_2d.evaluate',return_value=rejected),patch('cfd.solver.solver_path',return_value=Path('fake')),redirect_stdout(io.StringIO()):
                result=run_search(study,max_evaluations=1)
            self.assertIsNone(result['best'])
            with self.assertRaisesRegex(ValueError,'No candidate'):
                export_best(study,Path(folder)/'export')
            g=read(study/'source/geometry.json');g['sail_shapes'][0]['camber']=.2
            save(study/'source/geometry.json',g)
            with self.assertRaisesRegex(ValueError,'changed'):
                verify_inputs(study,config)

    def test_numerical_failure_is_recorded_without_fake_force(self):
        with tempfile.TemporaryDirectory() as folder:
            study=Path(folder)/'study';config=self.make_study(study)
            with patch('cfd.trim_2d.evaluate_section',side_effect=ValueError('invalid mesh')),redirect_stdout(io.StringIO()):
                result=evaluate(study,config,INITIAL)
            self.assertFalse(result['checks_passed'])
            self.assertIsNone(result['forces'])
            self.assertIn('invalid mesh',result['error'])

    def test_each_candidate_evaluates_exactly_one_paired_section(self):
        with tempfile.TemporaryDirectory() as folder:
            study=Path(folder)/'study';config=self.make_study(study)
            packet={'height_m':config['height_m'],'force_N_per_m':[120.,270.],'checks_passed':True}
            with patch('cfd.trim_2d.evaluate_section',return_value=packet) as section,redirect_stdout(io.StringIO()):
                result=evaluate(study,config,INITIAL)
            section.assert_called_once()
            point=section.call_args.args[4]
            self.assertEqual(point,{'height_m':config['height_m'],'active_sails':[0,1]})
            self.assertEqual(result['forces']['drive_N_per_m'],120.)
            self.assertEqual(len(result['section_keys']),1)

    def test_section_cache_depends_on_both_sheeting_angles_and_height(self):
        with tempfile.TemporaryDirectory() as folder:
            study=Path(folder)/'study';config=self.make_study(study)
            a=trimmed_geometry(original(),INITIAL)
            profile=read(study/'source/marine_case.json')
            point={'height_m':config['height_m'],'active_sails':[0,1]}
            initial_key=section_key(a,profile,point,config)
            for angles in ([8.,12.],[5.,15.]):
                b=trimmed_geometry(original(),angles)
                self.assertNotEqual(initial_key,section_key(b,profile,point,config))
            self.assertNotEqual(initial_key,section_key(a,profile,{**point,'height_m':5.},config))

    def test_prepare_rejects_a_section_without_both_sails_before_writing_inputs(self):
        with tempfile.TemporaryDirectory() as folder:
            for height in (0.,10.5,float('nan')):
                study=Path(folder)/'study'
                with self.assertRaisesRegex(ValueError,'both sails'):
                    prepare_study(REFERENCE,study,height=height)
                self.assertFalse(study.exists())

    def test_old_four_parameter_study_cannot_run_with_new_objective(self):
        with tempfile.TemporaryDirectory() as folder:
            study=Path(folder)/'study';config=self.make_study(study)
            config['format_version']=1
            with self.assertRaisesRegex(ValueError,'older optimisation'):
                verify_inputs(study,config)

    def test_repository_export_contains_matching_inputs_without_duplicate_code_or_archive(self):
        with tempfile.TemporaryDirectory() as folder:
            study=Path(folder)/'study';self.make_study(study)
            values=[8.,15.]
            state=read(study/'search.json')
            state.update(status='budget_exhausted',best={'parameters':values,'checks_passed':True,'forces':{'drive_N_per_m':1000.}})
            save(study/'search.json',state)
            target=Path(folder)/'handoff'
            with redirect_stdout(io.StringIO()):export_best(study,target)
            self.assertEqual(read(target/'geometry.json'),trimmed_geometry(read(study/'source/geometry.json'),values))
            self.assertFalse((target/'code').exists())
            self.assertFalse(target.with_suffix('.zip').exists())
            self.assertIn('git pull',(target/'README.md').read_text())
            result=read(target/'trim_result.json')
            self.assertTrue(result['requires_3d_validation'])
            self.assertFalse(result['solver_3d_run'])
            self.assertFalse(any(p.suffix in ('.su2','.msh','.dat') for p in target.rglob('*')))
            with self.assertRaisesRegex(ValueError,'preserved'):
                export_best(study,target)


if __name__=='__main__':
    unittest.main()
