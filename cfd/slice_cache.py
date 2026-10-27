"""Precompute detailed horizontal cuts from saved native cells; never run CFD."""
import argparse
import base64
import gzip
import json
from pathlib import Path
import re

import numpy as np

from cfd.storage import case_file
from cfd.separation_3d import sha


def slice_axis(low, high, near_low, near_high, fine=.05, outer=.2):
    def segment(a, b, step):
        return np.linspace(a, b, int(np.ceil((b-a)/step))+1)
    return np.unique(np.r_[segment(low, near_low, outer),
                           segment(near_low, near_high, fine),
                           segment(near_high, high, outer)])


def sample_layers(grid, x, y, heights, profile, info):
    import pyvista as pv
    from vtkmodules.vtkCommonDataModel import vtkStaticCellLocator
    from cfd.wind_viewer_3d import field_scores, FIELDS
    locator = vtkStaticCellLocator()
    locator.SetDataSet(grid)
    locator.BuildLocator()
    scores = {field: [] for field in FIELDS}
    for height in heights:
        plane = pv.RectilinearGrid(x, y, np.array([height]))
        sampled = plane.sample(grid, tolerance=1e-8, locator=locator,
                               pass_cell_data=False, pass_point_data=False,
                               snap_to_closest_point=False)
        velocity = np.asarray(sampled['velocity']).copy()
        velocity[~np.asarray(sampled['vtkValidPointMask'], dtype=bool)] = np.nan
        offsets = {key: float(np.interp(height, info['source_z'], values))
                   for key, values in info['height_offsets'].items()}
        layer = field_scores(np.asarray(plane.points), velocity, profile, offsets)
        for field in FIELDS:
            scores[field].append(layer[field].reshape(len(y), len(x)).astype('<f4'))
        print(f'Cached detailed slice at {height:g} m ({len(x)*len(y):,} points)', flush=True)
    return {key: np.stack(value) for key, value in scores.items()}


def write_script(folder, info, arrays):
    payload = {'x': arrays['x'].tolist(), 'y': arrays['y'].tolist(),
               'z': arrays['z'].tolist(), 'signature': info['signature'],
               'fields': {field: base64.b64encode(gzip.compress(
                   np.asarray(arrays[field], dtype='<f4').tobytes(), compresslevel=6)).decode('ascii')
                   for field in ('goodness', 'speed', 'direction')}}
    (Path(folder)/'wind_slices.js').write_text('window.windSliceCache=' +
        json.dumps(payload, separators=(',', ':')) + ';\n', encoding='utf-8')


def prepare(case, folder):
    from cfd.volume_data import native_velocity, fluid_grid
    from cfd.marine_rans import read_profile
    from cfd.wind_viewer_3d import FIELDS
    case, folder = Path(case), Path(folder)
    html = (folder/'wind_3d.html').read_text(encoding='utf-8')
    payload = json.loads(re.search(r'const payload=(.*);', html)[1])
    info = payload['info']
    hashes = {name: sha(case_file(case, name)) for name in ('geometry.json', 'yacht.msh', 'restart.dat')}
    if hashes != info['source_sha256']:
        raise ValueError('Saved native solution differs from the viewer; refusing to mix datasets')
    signature = {'source': hashes, 'height_offsets': info['height_offsets'],
                 'bounds': info['bounds_m'], 'z': payload['axes']['z'],
                 'fine_spacing_m': .05, 'outer_spacing_m': .2, 'version': 1}
    meta = folder/'wind_slices.json'
    archive = folder/'wind_slices.npz'
    if meta.exists() and archive.exists() and json.loads(meta.read_text())['signature'] == signature:
        with np.load(archive) as saved:
            arrays = dict(saved)
        print(f'Reusing detailed slice cache: {folder}', flush=True)
    else:
        mesh, velocity, manifest, _ = native_velocity(case)
        grid = fluid_grid(mesh, {'velocity': velocity})
        del mesh, velocity
        x = slice_axis(*info['bounds_m'][0], -6., 6.)
        y = slice_axis(*info['bounds_m'][1], -3., 3.)
        heights = np.asarray(payload['axes']['z'])
        layer_info = {**info, 'source_z': payload['axes']['z']}
        arrays = {'x': x, 'y': y, 'z': heights,
                  **sample_layers(grid, x, y, heights, read_profile(manifest), layer_info)}
        np.savez_compressed(archive, **arrays)
        meta.write_text(json.dumps({'signature': signature, 'solver_run': False,
            'method': 'Direct interpolation in original fluid cells at every cached height; no interpolation of the coarse viewer field',
            'dimensions': [len(x), len(y), len(heights)], 'fields': list(FIELDS)}, indent=2), encoding='utf-8')
    write_script(folder, {'signature': signature}, arrays)
    return arrays


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', type=Path, required=True)
    parser.add_argument('--viewer-dir', type=Path, required=True)
    args = parser.parse_args()
    prepare(args.case, args.viewer_dir)


if __name__ == '__main__':
    main()
