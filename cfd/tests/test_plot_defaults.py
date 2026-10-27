"""Default PNG export averages saved fields without invoking a solver."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cfd.plot_wind import render


class PlotDefaultTests(unittest.TestCase):
    def test_default_routes_all_pngs_through_height_average(self):
        with tempfile.TemporaryDirectory() as directory:
            case = Path(directory)

            def calibration(*args, **kwargs):
                (Path(kwargs['output_dir']) / 'plot_scales.json').write_text('{}')

            metadata = {'height_range_m': [.75, 11.25]}
            with patch('cfd.plot_wind.render_slice', side_effect=calibration), \
                 patch('cfd.wind_viewer_3d.prepare') as volume, \
                 patch('cfd.weighted_wind_2d.prepare', return_value=(metadata, {})) as average, \
                 patch('cfd.weighted_wind_2d.render') as draw:
                result = render(case)
            volume.assert_called_once_with(case, case, case / 'visualizations')
            average.assert_called_once_with(case / 'visualizations', case)
            self.assertEqual({call.kwargs['field'] for call in draw.call_args_list},
                             {'speed', 'direction', 'goodness'})
            self.assertFalse(result['solver_run'])
            self.assertEqual(len(result['files']), 3)

    def test_slice_requires_explicit_opt_in(self):
        with patch('cfd.plot_wind.render_slice', return_value='slice') as draw:
            self.assertEqual(render('case', height=4, averaged=False), 'slice')
            draw.assert_called_once_with('case', 'all', None, 4)

    def test_height_cannot_silently_override_average(self):
        with self.assertRaisesRegex(ValueError, 'height applies only to a slice'):
            render('case', height=4)
