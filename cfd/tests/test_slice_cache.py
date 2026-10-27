"""Detailed cuts sample native cells and preserve holes and score calibration."""
import base64
import gzip
import json
from pathlib import Path
import tempfile
import unittest

import meshio
import numpy as np

from cfd.slice_cache import sample_layers, slice_axis, write_script
from cfd.volume_data import fluid_grid
from cfd.wind_profile import WindProfile
from cfd.wind_viewer_3d import field_scores


class SliceCacheTests(unittest.TestCase):
    def test_nonuniform_axes_have_five_cm_detail_and_no_large_gaps(self):
        axis = slice_axis(-50, 15, -6, 6)
        self.assertAlmostEqual(axis[0], -50)
        self.assertAlmostEqual(axis[-1], 15)
        self.assertTrue(np.all(np.diff(axis)>0))
        self.assertLessEqual(np.max(np.diff(axis)), .2+1e-12)
        np.testing.assert_allclose(np.diff(axis[(axis>=-6)&(axis<=6)]), .05)

    def test_native_interpolation_preserves_affine_field_and_outside_mask(self):
        points = np.array([[0.,0,0], [1.,0,0], [0.,1,0], [0.,0,1]])
        mesh = meshio.Mesh(points, [('tetra', [[0,1,2,3]])])
        velocity = points*.2 + [-5.,1.,0.]
        grid = fluid_grid(mesh, {'velocity': velocity})
        profile = WindProfile(reference_speed_m_s=5., boat_speed_knots=0., kind='uniform')
        info = {'source_z': [0.,1.], 'height_offsets': {
            'speed_offset_m_s': [0.,0.], 'angle_offset_deg': [0.,0.], 'goodness_offset_knots': [.1,.3]}}
        arrays = sample_layers(grid, np.array([.1,2.]), np.array([.1]), [.2,.4], profile, info)
        for index, z in enumerate([.2,.4]):
            point = np.array([[.1,.1,z]])
            expected = field_scores(point, point*.2+[-5.,1.,0.], profile,
                {'speed_offset_m_s': 0., 'angle_offset_deg': 0., 'goodness_offset_knots': .1+.2*z})
            for field in arrays:
                self.assertAlmostEqual(float(arrays[field][index,0,0]), expected[field][0], places=6)
                self.assertTrue(np.isnan(arrays[field][index,0,1]))

    def test_compressed_sidecar_roundtrip_preserves_values_and_nan(self):
        with tempfile.TemporaryDirectory() as directory:
            values = np.array([[[.123,np.nan]], [[-.4,.2]]], dtype='f4')
            arrays = {'x':np.array([0.,1.]), 'y':np.array([0.]), 'z':np.array([1.,2.]),
                      **{field: values for field in ('goodness','speed','direction')}}
            write_script(directory, {'signature': {'version': 1}}, arrays)
            text = (Path(directory)/'wind_slices.js').read_text()
            payload = json.loads(text.split('=',1)[1].rstrip(';\n'))
            for data in payload['fields'].values():
                decoded = np.frombuffer(gzip.decompress(base64.b64decode(data)), dtype='<f4')
                np.testing.assert_array_equal(decoded.reshape(values.shape), values)
