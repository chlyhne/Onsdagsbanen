"""Cheap preliminary convergence from existing scalar output, never field files."""
import argparse
from collections import deque
import csv
import json
import math
from pathlib import Path

from cfd.rans_checks import (FLOW_LOG10_MAX, FORCE_NORMALIZATION_FLOOR,
    FORCE_RELATIVE_RANGE_MAX, FORCE_WINDOW, TURBULENCE_ABSOLUTE_LOG10_MAX,
    TURBULENCE_REDUCTION_ORDERS)


class ConvergenceProgress:
    def __init__(self, reference=None):
        self.fixed_reference = reference is not None
        self.reference = dict(reference or {})
        self.latest = {}
        self.iteration = None
        self.forces = deque(maxlen=FORCE_WINDOW)

    @classmethod
    def from_case(cls, case):
        path = Path(case) / 'residual_reference.json'
        return cls(json.loads(path.read_text()) if path.exists() else None)

    def update(self, row):
        iteration = row.get('Inner_Iter')
        if iteration is None or not math.isfinite(iteration) or iteration != int(iteration):
            return
        iteration = int(iteration)
        if iteration == self.iteration:
            return
        if self.iteration is None or iteration != self.iteration + 1:
            self.forces.clear()
        self.iteration = iteration
        self.latest = row
        if not self.fixed_reference:
            for name in TURBULENCE_ABSOLUTE_LOG10_MAX:
                value = row.get(name)
                if value is not None and math.isfinite(value):
                    self.reference[name] = max(self.reference.get(name, value), value)
        if all(name in row and math.isfinite(row[name]) for name in ('CFx', 'CFy')):
            self.forces.append((row['CFx'], row['CFy']))
        else:
            self.forces.clear()

    def status(self):
        def ratio(names, limit):
            values = [self.latest.get(name) for name in names]
            if any(value is None or not math.isfinite(value) for value in values):
                return None
            gap = max(values) - limit
            return 10.**gap if gap < 300 else math.inf

        ratios = {'flow': ratio(('rms[P]', 'rms[U]', 'rms[V]', 'rms[W]'), FLOW_LOG10_MAX)}
        for label, name in (('k', 'rms[k]'), ('omega', 'rms[w]')):
            reference = self.reference.get(name)
            limit = max(TURBULENCE_ABSOLUTE_LOG10_MAX[name], reference - TURBULENCE_REDUCTION_ORDERS) \
                if reference is not None else None
            ratios[label] = ratio((name,), limit) if limit is not None else None
        force_range = None
        if len(self.forces) == FORCE_WINDOW:
            ranges = []
            for values in zip(*self.forces):
                mean = math.fsum(values) / FORCE_WINDOW
                ranges.append((max(values) - min(values)) / max(abs(mean), FORCE_NORMALIZATION_FLOOR))
            force_range = max(ranges)
        checks = {name: value <= 1 if value is not None else None for name, value in ratios.items()}
        checks['forces'] = force_range < FORCE_RELATIVE_RANGE_MAX if force_range is not None else None
        return {'iteration': self.iteration, 'residual_ratios': ratios,
                'force_relative_range': force_range, 'force_samples': len(self.forces),
                'checks': checks, 'passed_count': sum(value is True for value in checks.values())}

    def summary(self):
        status = self.status()
        residuals = ' | '.join(f"{name}={value:.2g}x" if value is not None else f"{name}=afventer"
                               for name, value in status['residual_ratios'].items())
        variation = status['force_relative_range']
        forces = f"kraftvariation={100 * variation:.3g}% (<{100 * FORCE_RELATIVE_RANGE_MAX:g}%)" \
            if variation is not None else f"kraefter=afventer ({len(self.forces)}/{FORCE_WINDOW} iterationer)"
        return f"Konvergens (foreloebig): {status['passed_count']}/4 krav | {residuals} | {forces}"


def from_history(case):
    """Read only the small scalar history, including a possibly incomplete last line."""
    monitor = ConvergenceProgress.from_case(case)
    with (Path(case) / 'history.csv').open(encoding='utf-8-sig', newline='') as source:
        reader = csv.reader(source, skipinitialspace=True)
        names = [name.strip() for name in next(reader, [])]
        for values in reader:
            if len(values) != len(names):
                continue
            try:
                monitor.update(dict(zip(names, map(float, values))))
            except ValueError:
                continue
    return monitor


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', type=Path, default=Path('cfd/output/marine_3d'))
    args = parser.parse_args()
    monitor = from_history(args.case)
    print(f"Seneste skrevne historik: iteration {monitor.iteration}")
    print(monitor.summary())
    print('Residualtal <=1x opfylder graensen. Foreloebig status; vindfeltets stabilitet er ikke kontrolleret.')


if __name__ == '__main__':
    main()
