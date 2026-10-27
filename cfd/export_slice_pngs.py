"""Export comparison PNGs from detailed cached cuts, with one fixed colour scale."""
import argparse
import json
from pathlib import Path

import numpy as np

from cfd.separation_3d import sha
from cfd.storage import case_file
from cfd.weighted_wind_2d import render


def export(source, output, heights):
    source, output = Path(source), Path(output)
    info = json.loads((source/'wind_3d.json').read_text(encoding='utf-8'))
    cache_info = json.loads((source/'wind_slices.json').read_text(encoding='utf-8'))
    if cache_info['signature']['source'] != info['source_sha256']:
        raise ValueError('Slice cache does not match this wind result')
    context = json.loads(case_file(info['snapshot'], 'plot_context.json').read_text(encoding='utf-8'))
    metadata = {'geometry': info['geometry'], 'calibration': info['calibration'],
                'true_wind_angle_deg': context['wind']['true_angle_deg']}
    output.mkdir(parents=True, exist_ok=True)
    files = []
    with np.load(source/'wind_slices.npz', allow_pickle=False) as saved:
        for height in heights:
            matches = np.flatnonzero(np.isclose(saved['z'], height, rtol=0, atol=1e-9))
            if len(matches) != 1:
                raise ValueError(f'No cached cut at {height:g} m')
            arrays = {'x': saved['x'], 'y': saved['y'], 'goodness': saved['goodness'][matches[0]]}
            target = output/f'slice-{height:g}m.png'
            render({**metadata, 'sail_outline_reference_height_m': height}, arrays, target,
                   title=f'Estimated change in VMG · horizontal slice at {height:g} m', english=True)
            files.append({'file': target.name, 'height_m': height, 'sha256': sha(target)})
            print(f'Saved {target}', flush=True)
    provenance = {'dataset': Path(info['case']).name, 'solver_run': False, 'height_averaged': False,
                  'calibration': info['calibration'], 'source': str(source),
                  'slice_cache_sha256': sha(source/'wind_slices.npz'),
                  'source_sha256': info['source_sha256'], 'files': files,
                  'centering': 'Inherited height-dependent viewer references; no additional recentering',
                  'checks_passed': info['checks_passed'], 'iteration': info['iteration']}
    (output/'slice-provenance.json').write_text(json.dumps(provenance, indent=2)+'\n', encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--heights', type=float, nargs='+', default=[2., 8.])
    args = parser.parse_args()
    export(args.source, args.output, args.heights)
