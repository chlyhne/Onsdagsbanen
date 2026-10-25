"""The structured layout preserves native solver paths and independent plots."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import io
import zipfile

import numpy as np

from cfd.compare_snapshots import comparison_frames
from cfd.convergence_progress import ConvergenceProgress
from cfd.marine_rans import read_profile
from cfd.plot_data import load_plot_data
from cfd.section_2d import REFERENCE, configuration
from cfd.storage import case_file, create_case


class StorageTests(unittest.TestCase):
    def test_installer_keeps_download_archives_outside_installed_binaries(self):
        from cfd import solver
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, 'w') as archive:
            archive.writestr('bin/SU2_CFD.exe', b'test executable; never run')
        def download(url, destination):
            with zipfile.ZipFile(destination, 'w') as archive:
                archive.writestr('win64-omp.zip', inner.getvalue())
        with tempfile.TemporaryDirectory() as folder:
            installed = Path(folder) / 'SU2'
            with patch.object(solver, 'TOOLS', installed), patch.object(solver.sys, 'platform', 'win32'), \
                 patch.object(solver.shutil, 'which', return_value=None), \
                 patch.object(solver.urllib.request, 'urlretrieve', side_effect=download):
                executable = solver.solver_path(install=True)
            self.assertTrue(executable.is_file())
            self.assertEqual(list(installed.rglob('*.zip')), [])
            self.assertEqual(len(list((installed.parent / 'downloads').glob('*.zip'))), 1)

    def test_native_config_and_checkpoint_discovery_use_case_subfolders(self):
        with tempfile.TemporaryDirectory() as folder:
            case = create_case(folder)
            case_file(case, 'section_case.json').write_text('{}')
            profile = read_profile(json.loads((REFERENCE / 'marine_case.json').read_text()))
            configuration(case, profile, 4., iterations=3, restart=True)
            config = case_file(case, 'yacht.cfg').read_text()
            for value in ('MESH_FILENAME= input/yacht.su2', 'SOLUTION_FILENAME= checkpoints/restart',
                          'RESTART_FILENAME= checkpoints/restart', 'CONV_FILENAME= history/history',
                          'SURFACE_FILENAME= fields/surface'):
                self.assertIn(value, config)
            self.assertFalse((case / 'yacht.cfg').exists())
            for iteration in (100, 200):
                case_file(case, f'restart_{iteration:06d}.dat').write_bytes(b'stored snapshot')
            before, after = comparison_frames(case, 0, 200)
            self.assertEqual((before[0], after[0]), (100, 200))
            self.assertEqual(before[1].parent, case / 'checkpoints')
            self.assertEqual(ConvergenceProgress.from_case(case).dimensions, 2)

    def test_plot_snapshot_loads_from_results_without_solver_state(self):
        with tempfile.TemporaryDirectory() as folder:
            case = create_case(folder)
            context = {'slice_file': 'slice.npz', 'height_m': 4.}
            case_file(case, 'plot_context.json').write_text(json.dumps(context))
            np.savez_compressed(case_file(case, 'slice.npz'), velocity=np.array([[1., 2.]]))
            for source in (case, case / 'results'):
                loaded, fields = load_plot_data(source)
                self.assertEqual(loaded, context)
                np.testing.assert_array_equal(fields['velocity'], [[1., 2.]])
            self.assertEqual(list((case / 'checkpoints').iterdir()), [])


if __name__ == '__main__':
    unittest.main()
