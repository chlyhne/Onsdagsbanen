"""Compact preparation keeps physics/resolution and never invokes a solver."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from cfd.prepare_compact import prepare_compact
from cfd.marine_checks import background_profile
from cfd.marine_inflow import marine_profile, initial_values


class CompactTests(unittest.TestCase):
    def test_committed_trim_is_sufficient_for_fresh_3d_preparation(self):
        source=Path(__file__).resolve().parents[1]/'trimmed_reference'
        geometry=json.loads((source/'geometry.json').read_text())
        result=json.loads((source/'trim_result.json').read_text())
        force=json.loads((source/'section_force.json').read_text())
        report=json.loads((source/force['report']).read_text())
        self.assertTrue(result['best']['checks_passed'])
        self.assertTrue(force['checks_passed'] and report['checks_passed'])
        np.testing.assert_allclose([s[3] for s in geometry['sail_definitions']],result['best']['parameters'])
        for shape in geometry['sail_shapes']:
            self.assertEqual((shape['camber'],shape['draft_position'],shape['twist_deg']),(.12,.45,2.))
        with tempfile.TemporaryDirectory() as folder:
            case=Path(folder)/'fresh_3d'
            def prepared(base,target,**kwargs):
                target.mkdir()
                return {'status':'prepared_not_simulated'}
            with patch('cfd.prepare_compact.make_mesh') as mesh, \
                 patch('cfd.prepare_compact.prepare',side_effect=prepared) as prepare, \
                 patch('subprocess.Popen',side_effect=AssertionError('Unexpected CFD')):
                prepare_compact(source,case)
            self.assertEqual(mesh.call_args.args[5],geometry['sail_definitions'])
            self.assertEqual(mesh.call_args.kwargs['shapes'],geometry['sail_shapes'])
            self.assertEqual(prepare.call_args.kwargs['wind'],5.)
            self.assertEqual(prepare.call_args.kwargs['angle'],41.)
            self.assertEqual(prepare.call_args.kwargs['boat_speed'],6.)

    def test_preparation_maps_plot_bounds_and_retains_source_physics(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'old';source.mkdir();case=root/'new'
            metadata={'mesh_size':.22,'wind_angle':29.9,'wake_size':.55,'sail_size':.04,
                      'boundary_layers':{'first_node_spacing_m':1.2e-5,'layers':30,'growth':1.2455979758326061}}
            geometry={'sail_definitions':[[0,3.7,10.5,12],[3.4,2.8,8.5,20]],'sail_shapes':[{},{}]}
            profile=marine_profile()
            for name,data in [('mesh_metadata.json',metadata),('geometry.json',geometry),
                              ('marine_case.json',{'profile':profile.metadata(),'coefficient_reference_height_m':84.})]:
                (source/name).write_text(json.dumps(data))
            for name in ('yacht.cfg','airfoil_validation.json'):(source/name).write_text('source')
            def prepared(base,target,**kwargs):
                target.mkdir();return {'status':'prepared_not_simulated'}
            with patch('cfd.prepare_compact.make_mesh') as mesh, \
                 patch('cfd.prepare_compact.prepare',side_effect=prepared) as prepare, \
                 patch('subprocess.Popen',side_effect=AssertionError('Unexpected CFD')):
                manifest=prepare_compact(source,case)
            self.assertEqual(mesh.call_args.kwargs['domain_min'],[-50.,-50.,0.])
            self.assertEqual(mesh.call_args.kwargs['domain_max'],[35.,40.,28.])
            self.assertEqual(mesh.call_args.kwargs['first_layer'],1.2e-5)
            self.assertEqual(prepare.call_args.kwargs['wind'],profile.reference_speed_m_s)
            self.assertEqual(manifest['status'],'prepared_not_simulated')
            centers=manifest['background_profile_centers_xy_m']
            self.assertTrue(all(-50<x<15 and -30<y<40 for x,y in centers))

    def test_new_background_columns_sample_the_same_analytical_atmosphere(self):
        centers=[[8.,-22.],[0.,-22.],[-35.,-22.],[-42.,5.]]
        points=np.array([[x,y,z] for x,y in centers for z in (2.,4.,10.)])
        profile=marine_profile();scale=profile.at(84.)['apparent_speed_m_s']
        values=initial_values(points,profile)
        native=np.column_stack((points,values))
        native[:,4:7]/=scale;native[:,7]/=scale**2;native[:,8]/=scale
        self.assertTrue(background_profile(points,native,profile,scale,centers=centers)['passed'])


if __name__=='__main__':unittest.main()
