"""Scalar-only diagnostics must agree with the existing post-run criteria."""
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from cfd.convergence_progress import ConvergenceProgress, from_history
from cfd.rans_checks import convergence


def row(iteration, **changes):
    values = {'Inner_Iter': float(iteration), 'rms[P]': -8., 'rms[U]': -8.,
              'rms[V]': -8., 'rms[W]': -8., 'rms[k]': -11.5, 'rms[w]': -5.5,
              'CFx': .2, 'CFy': 1.}
    values.update(changes)
    return values


class ConvergenceProgressTests(unittest.TestCase):
    def test_2d_case_does_not_wait_for_vertical_velocity(self):
        with tempfile.TemporaryDirectory() as folder:
            case = Path(folder)
            (case/'section_case.json').write_text('{}')
            monitor = ConvergenceProgress.from_case(case)
            sample = row(0)
            del sample['rms[W]']
            monitor.update(sample)
            self.assertTrue(monitor.status()['checks']['flow'])
            self.assertEqual(monitor.dimensions, 2)
            (case/'section_case.json').unlink()
            monitor = ConvergenceProgress.from_case(case)
            monitor.update(sample)
            self.assertIsNone(monitor.status()['checks']['flow'])

    def test_matches_postprocessing_for_cold_and_warm_references(self):
        for reference in ({'rms[k]': -3., 'rms[w]': 2.},
                          {'rms[k]': -10., 'rms[w]': -5.}):
            for changes in ({}, {'rms[W]': -4.}, {'rms[k]': -7.},
                            {'rms[w]': -2.}, {'CFy': 1.02}):
                with self.subTest(reference=reference, changes=changes), tempfile.TemporaryDirectory() as folder:
                    case = Path(folder)
                    (case/'residual_reference.json').write_text(json.dumps(reference))
                    monitor = ConvergenceProgress.from_case(case)
                    rows = [row(i, **(changes if i == 199 else {})) for i in range(200)]
                    names = list(rows[0])
                    history = np.array([tuple(sample[name] for name in names) for sample in rows],
                                       dtype=[(name, float) for name in names])
                    for sample in rows:
                        monitor.update(sample)
                    expected = convergence(case, history)
                    checks = monitor.status()['checks']
                    self.assertEqual(checks['flow'], expected['checks']['flow_rms_below_1e_6'])
                    for label, name in (('k', 'rms[k]'), ('omega', 'rms[w]')):
                        self.assertEqual(checks[label], expected['turbulence_residual_checks'][name]['passed'])
                    self.assertEqual(checks['forces'], expected['checks']['forces_stable_0_1_percent_over_200_iterations'])

    def test_missing_values_and_short_or_sparse_windows_are_not_passed(self):
        monitor = ConvergenceProgress()
        monitor.update({'Inner_Iter': 0., 'rms[P]': -8.})
        self.assertIsNone(monitor.status()['checks']['flow'])
        self.assertIsNone(monitor.status()['checks']['forces'])
        for i in range(1, 200):
            monitor.update(row(i))
        self.assertIsNone(monitor.status()['checks']['forces'])
        monitor.update(row(200))
        self.assertTrue(monitor.status()['checks']['forces'])
        monitor.update(row(225))
        self.assertEqual(monitor.status()['force_samples'], 1)
        self.assertIsNone(monitor.status()['checks']['forces'])

    def test_reference_tracks_worst_residual_without_a_saved_reference(self):
        monitor = ConvergenceProgress()
        monitor.update(row(0, **{'rms[k]': -2., 'rms[w]': 2.}))
        monitor.update(row(1, **{'rms[k]': -8., 'rms[w]': -4.}))
        self.assertTrue(monitor.status()['checks']['k'])
        self.assertTrue(monitor.status()['checks']['omega'])
        self.assertAlmostEqual(monitor.status()['residual_ratios']['k'], 1.)

    def test_nonfinite_residual_does_not_pass_or_crash(self):
        monitor = ConvergenceProgress()
        monitor.update(row(0, **{'rms[W]': float('nan'), 'rms[k]': 1000.}))
        self.assertIsNone(monitor.status()['checks']['flow'])
        monitor.update(row(1, **{'rms[k]': 1000.}))
        fixed = ConvergenceProgress({'rms[k]': -10., 'rms[w]': -5.})
        fixed.update(row(0, **{'rms[k]': 1000.}))
        self.assertFalse(fixed.status()['checks']['k'])
        self.assertIn('k=infx', fixed.summary())

    def test_history_reader_handles_header_spacing_and_partial_final_line(self):
        with tempfile.TemporaryDirectory() as folder:
            case = Path(folder)
            history = '"Inner_Iter", "rms[P]", "rms[k]", "rms[w]"\n0,-8,-3,2\n1,-8,-9,-5\n2,-8,'
            (case/'history.csv').write_text(history)
            monitor = from_history(case)
            self.assertEqual(monitor.iteration, 1)
            self.assertTrue(monitor.status()['checks']['k'])
            self.assertIsNone(monitor.status()['checks']['flow'])


if __name__ == '__main__':
    unittest.main()
