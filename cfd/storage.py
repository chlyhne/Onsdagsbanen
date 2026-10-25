"""Separate durable case input/results from solver state and diagnostic files.

Old flat cases and published plotting snapshots remain readable.
"""
from pathlib import Path

INPUT = {'geometry.json', 'mesh_metadata.json', 'yacht.msh', 'yacht.su2', 'yacht.cfg',
         'section_case.json', 'marine_case.json', 'airfoil_validation.json',
         'initial_atmosphere.dat', 'initial_atmosphere.json', 'inlet_atmosphere.dat', 'archive_source.json'}
RESULTS = {'report.json', 'slice.npz', 'plot_context.json', 'plot_scales.json', 'field_stability.json'}
HISTORY = {'history.csv', 'history_before_restart.csv', 'residual_reference.json', 'history_segments'}


def create_case(case):
    case = Path(case)
    for name in ('input', 'checkpoints', 'fields', 'history', 'logs', 'results'):
        (case / name).mkdir(parents=True, exist_ok=True)
    return case


def case_file(case, name):
    case, name = Path(case), str(name)
    if not (case / 'input').is_dir():
        return case / name
    if name in INPUT:
        folder = 'input'
    elif name.startswith('restart') and name.endswith('.dat'):
        folder = 'checkpoints'
    elif name in HISTORY:
        folder = 'history'
    elif name in RESULTS or name.endswith('.png'):
        folder = 'results'
    elif name.startswith(('flow', 'surface')) and name.endswith(('.vtk', '.vtu', '.meta')):
        folder = 'fields'
    else:
        folder = 'logs'
    return case / folder / name


def result_dir(case):
    case = Path(case)
    return case / 'results' if (case / 'input').is_dir() else case
