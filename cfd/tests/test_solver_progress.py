"""Progress checks use tiny Python processes, never a CFD solver."""
from io import StringIO
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from cfd.solver_progress import run_solver


class SolverProgressTests(unittest.TestCase):
    def test_live_convergence_is_reported_without_reading_field_files(self):
        output = StringIO()
        names = ['Inner_Iter', 'rms[P]', 'rms[U]', 'rms[V]', 'rms[W]', 'rms[k]', 'rms[w]', 'CFx', 'CFy']
        lines = ['| ' + ' | '.join(names) + ' |\n']
        lines += [f'| {i} | -8 | -8 | -8 | -8 | -12 | -6 | .2 | 1 |\n' for i in range(200)]

        def live_lines():
            yield from lines
            self.assertIn('Konvergens (foreloebig):', output.getvalue())

        process = MagicMock()
        process.__enter__.return_value = process
        process.stdout = live_lines()
        process.wait.return_value = 0
        with tempfile.TemporaryDirectory() as directory, \
             patch('cfd.solver_progress.subprocess.Popen', return_value=process):
            run_solver(['dummy_SU2'], cwd=Path(directory), env=None, iterations=300, stream=output)
        self.assertIn('4/4 krav', output.getvalue())
        self.assertIn('kraftvariation=0% (<0.1%)', output.getvalue())
        self.assertLessEqual(output.getvalue().count('Konvergens (foreloebig):'), 10)

    def test_progress_is_visible_before_the_next_solver_row(self):
        output = StringIO()
        lines = ["| Inner_Iter | rms[P] |\n", "| 0 | -3.0 |\n",
                 "| 49 | -5.0 |\n", "All convergence criteria satisfied.\n"]

        def live_lines():
            yield from lines[:3]
            self.assertIn("50.0 % (50/100)", output.getvalue())
            yield lines[3]

        process = MagicMock()
        process.__enter__.return_value = process
        process.stdout = live_lines()
        process.wait.return_value = 0
        with tempfile.TemporaryDirectory() as directory, \
             patch('cfd.solver_progress.subprocess.Popen', return_value=process):
            case = Path(directory)
            run_solver(['dummy_SU2'], cwd=case, env=None, iterations=100, stream=output)
            self.assertEqual((case/'solver.log').read_text(), ''.join(lines))
        self.assertIn("0.0 % (0/100)", output.getvalue())
        self.assertNotIn("100.0 %", output.getvalue())

    def test_real_process_is_logged_and_reaches_iteration_limit(self):
        output = StringIO()
        lines = ["| 99 | -3 |", "| Inner_Iter | rms[P] |", "| 99 | -8 |"]
        code = 'print(' + repr('\n'.join(lines)) + ')'
        with tempfile.TemporaryDirectory() as directory:
            case = Path(directory)
            run_solver([sys.executable, '-u', '-c', code], cwd=case, env=None,
                       iterations=100, stream=output)
            self.assertEqual((case/'solver.log').read_text().splitlines(), lines)
        self.assertEqual(output.getvalue().count("100.0 % (100/100)"), 1)

    def test_failed_process_keeps_log_and_does_not_claim_completion(self):
        output = StringIO()
        with tempfile.TemporaryDirectory() as directory:
            case = Path(directory)
            with self.assertRaises(subprocess.CalledProcessError) as failure:
                run_solver([sys.executable, '-u', '-c',
                            'import sys; print("solver error", file=sys.stderr); sys.exit(7)'],
                           cwd=case, env=None, iterations=100, stream=output)
            self.assertEqual(failure.exception.returncode, 7)
            self.assertIn('solver error', (case/'solver.log').read_text())
        self.assertNotIn('Solver afsluttet', output.getvalue())

    def test_interrupt_stops_child_process(self):
        def interrupted():
            yield "| Inner_Iter | rms[P] |\n"
            raise KeyboardInterrupt

        process = MagicMock()
        process.__enter__.return_value = process
        process.stdout = interrupted()
        process.poll.return_value = None
        with tempfile.TemporaryDirectory() as directory, \
             patch('cfd.solver_progress.subprocess.Popen', return_value=process):
            with self.assertRaises(KeyboardInterrupt):
                run_solver(['dummy_SU2'], cwd=Path(directory), env=None,
                           iterations=100, stream=StringIO())
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=5)


if __name__ == '__main__':
    unittest.main()
