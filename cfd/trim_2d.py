"""Two-sheeting-angle search using one paired-sail 2D RANS section.

No command in this module generates a 3D mesh or starts a 3D solver.
prepare -> run (resumable) -> export (small repository inputs for the 3D PC).
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from copy import deepcopy
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

import numpy as np
from scipy.optimize import minimize

from cfd.case import SAIL_FOOT_HEIGHT, case_chord_trim
from cfd.marine_rans import read_profile
from cfd.section_2d import REFERENCE, prepare as prepare_section, run as run_section
from cfd.section_loads import loads
from cfd.storage import case_file

PARAMETERS = ('main_foot_deg', 'jib_foot_deg')
TOTAL_TWIST_DEG = 2.
INITIAL_JIB_AOA_DEG = 9.
INITIAL_MAIN_TIGHTER_DEG = 3.
SIMPLEX_STEP_DEG = 3.
ANGLE_TOLERANCE_DEG = 1.
RELATIVE_FORCE_TOLERANCE = .01


class BudgetExpired(Exception):
    """Stop between solver invocations, retaining all completed sections."""


def check_deadline(deadline):
    if deadline is not None and time.monotonic() >= deadline:
        raise BudgetExpired()


def stamp():
    return datetime.now(timezone.utc).isoformat()


def save(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n', encoding='utf8')
    temp.replace(path)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf8'))


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def parameter_bounds(geometry):
    """Both sheeting angles range from 0 to 30 degrees at the sail foot."""
    spans = np.asarray([s[2] for s in geometry['sail_definitions']], dtype=float)
    if spans.shape != (2,) or not np.isfinite(spans).all() or np.any(spans <= 0):
        raise ValueError('Expected two finite, positive sail heights')
    return np.array([[0., 30.], [0., 30.]])


def trimmed_geometry(original, parameters):
    """Fixed 12% depth at 45% chord and 2 degrees of total opening twist."""
    values = np.asarray(parameters, dtype=float)
    if values.shape != (2,) or not np.isfinite(values).all():
        raise ValueError('Expected two finite sheeting angles')
    bounds = parameter_bounds(original)
    if np.any(values < bounds[:, 0]-1e-12) or np.any(values > bounds[:, 1]+1e-12):
        raise ValueError('Sheeting angles must be between 0 and 30 degrees')
    values = np.clip(values, bounds[:, 0], bounds[:, 1])
    geometry = deepcopy(original)
    geometry.pop('section_height_m', None)
    if [s['name'] for s in geometry['sail_shapes']] != ['main', 'jib']:
        raise ValueError('Expected main and jib in that order')
    if geometry.get('boat_offsets_xy_m', [[0., 0.]]) != [[0., 0.]]:
        raise ValueError('Trim one rig, not multiple boats')
    for angle, sail, shape in zip(values, geometry['sail_definitions'], geometry['sail_shapes']):
        sail[3] = float(angle)
        shape.update(camber=.12, draft_position=.45, trim_height_m=SAIL_FOOT_HEIGHT,
                     twist_deg=TOTAL_TWIST_DEG)
    geometry['model'] = '12% depth at 45% chord; fixed 2-degree total twist; single-section sheeting study'
    return geometry


def representative_height(geometry):
    """Sail-area centroid of the saved linearly tapered main and jib."""
    parameter_bounds(geometry)
    sails = np.asarray(geometry['sail_definitions'], dtype=float)
    areas = .52*sails[:, 1]*sails[:, 2]
    if not np.isfinite(areas).all() or np.any(areas <= 0):
        raise ValueError('Expected positive sail areas')
    first_moments = .18*sails[:, 1]*sails[:, 2]**2
    return float(SAIL_FOOT_HEIGHT + np.sum(first_moments)/np.sum(areas))


def initial_angles(geometry, profile, height):
    """Seed chord AoA against ambient apparent wind, compensating fixed twist.

    The main starts 3 degrees tighter than the jib at this section. Its actual
    inflow, including the jib's influence, is calculated by the paired solver.
    """
    awa = float(read_profile(profile).at(height)['apparent_angle_deg'])
    jib_angle = awa - INITIAL_JIB_AOA_DEG
    section_angles = [jib_angle-INITIAL_MAIN_TIGHTER_DEG, jib_angle]
    foot_angles = [angle - TOTAL_TWIST_DEG*(height-SAIL_FOOT_HEIGHT)/sail[2]
                   for angle, sail in zip(section_angles, geometry['sail_definitions'])]
    bounds = parameter_bounds(geometry)
    if not np.isfinite(foot_angles).all() or np.any(foot_angles < bounds[:, 0]) or np.any(foot_angles > bounds[:, 1]):
        raise ValueError('The requested initial AoA and main offset do not fit the 0–30 degree sheeting bounds')
    return foot_angles


def starting_simplex(initial, bounds):
    """Three nearby trims for a two-dimensional search, kept inside bounds."""
    simplex = np.tile(np.asarray(initial, dtype=float), (len(PARAMETERS)+1, 1))
    for i in range(len(PARAMETERS)):
        sign = 1. if initial[i]+SIMPLEX_STEP_DEG <= bounds[i, 1] else -1.
        simplex[i+1, i] += sign*SIMPLEX_STEP_DEG
    return simplex


def section_forces(height, result):
    """Keep section forces in N/m; do not manufacture a whole-rig force."""
    if not np.isclose(height, result['height_m'], rtol=0, atol=1e-9):
        raise ValueError('Force height does not match the requested section')
    force = np.asarray(result['force_N_per_m'], dtype=float)
    if force.shape != (2,) or not np.isfinite(force).all():
        raise ValueError('Invalid dimensional section force')
    return {'drive_N_per_m': float(force[0]), 'side_force_N_per_m': float(force[1])}


def implementation_hash():
    return fingerprint({path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                        for path in sorted(Path(__file__).parent.glob('*.py'))})


def prepare_study(source, study, *, iterations=3500, continuations=1, threads=2,
                  sail_size=.06, height=None, initial_trim=None):
    source, study = Path(source), Path(study)
    if study.exists():
        raise ValueError('Choose a new study directory; use run to resume an existing study')
    if iterations < 300 or continuations < 0 or threads < 1 or not sail_size > 0:
        raise ValueError('Require >=300 iterations, nonnegative continuations, positive threads/mesh size')
    geometry = read(case_file(source, 'geometry.json'))
    profile = read(case_file(source, 'marine_case.json'))
    bounds = parameter_bounds(geometry)
    height = representative_height(geometry) if height is None else float(height)
    top = SAIL_FOOT_HEIGHT + min(s[2] for s in geometry['sail_definitions'])
    if not np.isfinite(height) or not SAIL_FOOT_HEIGHT < height < top:
        raise ValueError('The selected section must intersect both sails between foot and head')
    if initial_trim is None:
        initial = initial_angles(geometry, profile, height)
    else:
        seed = read(case_file(Path(initial_trim), 'geometry.json'))
        initial = [float(case_chord_trim(seed, i, SAIL_FOOT_HEIGHT)) for i in range(2)]
        trimmed_geometry(geometry, initial)  # Validate before creating any files.
    simplex = starting_simplex(initial, bounds)
    geometry = trimmed_geometry(geometry, initial)
    study.mkdir(parents=True)
    (study/'source').mkdir()
    save(study/'source/geometry.json', geometry)
    save(study/'source/marine_case.json', profile)
    # These small inputs allow prepare_compact to construct a NEW 3D mesh on
    # the other computer. Old mesh/restart files must never accompany new sails.
    for name in ('mesh_metadata.json', 'yacht.cfg', 'airfoil_validation.json'):
        path = case_file(source, name)
        shutil.copy2(path if path.exists() else REFERENCE/name, study/'source'/name)
    config = {'format_version': 3, 'created_at_utc': stamp(),
              'parameters': list(PARAMETERS), 'initial': initial, 'bounds': bounds.tolist(),
              'foot_angle_bounds_deg': [0., 30.], 'total_twist_deg': TOTAL_TWIST_DEG,
              'initial_simplex': simplex.tolist(),
              'initial_trim_source': str(initial_trim) if initial_trim is not None else None,
              'initial_jib_aoa_deg': INITIAL_JIB_AOA_DEG if initial_trim is None else None,
              'initial_main_tighter_deg': INITIAL_MAIN_TIGHTER_DEG if initial_trim is None else None,
              'aoa_reference': 'Chord relative to undisturbed apparent wind at the selected section',
              'angle_tolerance_deg': ANGLE_TOLERANCE_DEG,
              'relative_force_tolerance': RELATIVE_FORCE_TOLERANCE,
              'camber': .12, 'draft_position': .45, 'height_m': height,
              'iterations': iterations, 'continuations': continuations,
              'threads': threads, 'sail_size_m': sail_size,
              'objective': 'Maximise forward force per metre in one section at fixed speed and heading',
              'search_method': 'Bounded Nelder-Mead around the seeded trim; coarse local search, not a precise or global optimum',
              'limitations': ['2D sections have no spanwise induction or tip vortices.',
                             'No keel drag, heel equilibrium or speed/VMG optimisation.',
                             'One representative section is used; whole-rig forces are not estimated.',
                             'Published sail shapes are plausibility references, not validation.'],
              'implementation_sha256': implementation_hash()}
    config['inputs_sha256'] = fingerprint({'geometry': geometry, 'profile': profile})
    config['configuration_sha256'] = fingerprint(config)
    save(study/'study.json', config)
    save(study/'search.json', {'status': 'prepared', 'evaluations': [],
         'best': None, 'created_at_utc': stamp()})
    print(f'Prepared {study}: two sheeting angles, fixed 2-degree twist, one section at {height:.3f} m; no solver started.', flush=True)
    return config


def verify_inputs(study, config):
    if config.get('format_version') != 3:
        raise ValueError('This study uses an older optimisation setup; prepare a new study')
    frozen = dict(config)
    signature = frozen.pop('configuration_sha256')
    if fingerprint(frozen) != signature:
        raise ValueError('Study configuration changed; prepare a new study instead of mixing evaluations')
    inputs = {'geometry': read(study/'source/geometry.json'), 'profile': read(study/'source/marine_case.json')}
    if fingerprint(inputs) != config['inputs_sha256'] or implementation_hash() != config['implementation_sha256']:
        raise ValueError('Study inputs or solver code changed; prepare a new study instead of reusing its cache')


def section_key(geometry, profile, point, config):
    # Include both sail geometries and the fixed section wind/height.
    return fingerprint({'sails': [geometry['sail_definitions'][i] for i in point['active_sails']],
                        'shapes': [geometry['sail_shapes'][i] for i in point['active_sails']],
                        'profile': profile, 'height': point['height_m'],
                        'solver': config['implementation_sha256'],
                        'iterations': config['iterations'], 'continuations': config['continuations'],
                        'sail_size': config['sail_size_m']})[:20]


def evaluate_section(study, config, geometry, profile, point, *, deadline=None):
    key = section_key(geometry, profile, point, config)
    folder = study/'sections'/key
    result_file = folder/'force.json'
    if result_file.exists():
        return read(result_file)
    check_deadline(deadline)
    folder.mkdir(parents=True, exist_ok=True)
    source, case = folder/'source', folder/'case'
    source.mkdir(exist_ok=True)
    save(source/'geometry.json', geometry)
    save(source/'marine_case.json', profile)
    with (folder/'progress.log').open('a', encoding='utf8') as output, redirect_stdout(output):
        if case.exists() and (not case_file(case, 'section_case.json').exists() or
                (read(case_file(case, 'section_case.json')).get('solver_started') and
                 not case_file(case, 'restart.dat').exists())):
            # Keep incomplete work for inspection. A solver interrupted before
            # its first checkpoint cannot restart from an absent solution.
            case.rename(folder/f'case_incomplete_{time.time_ns()}')
        if not case.exists():
            prepare_section(source, case, height=point['height_m'],
                            sail_size=config['sail_size_m'], threads=config['threads'])
        manifest = read(case_file(case, 'section_case.json'))
        # The mesh is deterministic but interrupted preparation/solver output
        # cannot silently become an accepted force observation.
        restart = case_file(case, 'restart.dat').exists()
        if manifest.get('status') == 'solver_finished' or manifest.get('status') in ('checks_passed', 'checks_failed'):
            report = read(case_file(case, 'report.json')) if case_file(case, 'report.json').exists() else None
        else:
            report = None
        attempts = read(folder/'attempts.json') if (folder/'attempts.json').exists() else []
        for attempt in range(len(attempts), config['continuations']+1):
            if report and report['checks_passed']:
                break
            check_deadline(deadline)
            print(f'Height {point["height_m"]:.3f} m, attempt {attempt+1}', flush=True)
            started = time.monotonic()
            report = run_section(case, threads=config['threads'], iterations=config['iterations'],
                                 restart=restart, plot=False)
            attempts.append({'completed_at_utc': stamp(), 'seconds': time.monotonic()-started,
                             'checks_passed': report['checks_passed']})
            save(folder/'attempts.json', attempts)
            restart = True
        result = loads(case)
        result['checks_passed'] = bool(report and report['checks_passed'] and result['force_integration_passed'])
        result['report'] = str(case_file(case, 'report.json').relative_to(study))
        result['attempts'] = attempts
        save(result_file, result)
    return result


def evaluate(study, config, values, *, deadline=None):
    geometry = trimmed_geometry(read(study/'source/geometry.json'), values)
    profile = read(study/'source/marine_case.json')
    point = {'height_m': config['height_m'], 'active_sails': [0, 1]}
    print(f'  paired-sail section at {point["height_m"]:.3f} m', flush=True)
    try:
        result = evaluate_section(study, config, geometry, profile, point, deadline=deadline)
        forces = section_forces(point['height_m'], result)
    except (ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        return {'parameters': [float(v) for v in values], 'checks_passed': False,
                'forces': None, 'error': f'{type(error).__name__}: {error}',
                'failed_height_m': point['height_m'], 'completed_at_utc': stamp()}
    return {'parameters': [float(v) for v in values], 'checks_passed': result['checks_passed'],
            'forces': forces, 'section_keys': [section_key(geometry, profile, point, config)],
            'completed_at_utc': stamp()}


def write_evaluations(study, state):
    """A small readable table alongside the complete resumable JSON state."""
    temp = study/'evaluations.csv.tmp'
    with temp.open('w', newline='', encoding='utf8') as output:
        writer = csv.DictWriter(output, fieldnames=[*PARAMETERS, 'drive_N_per_m', 'side_force_N_per_m',
                                'checks_passed', 'error'])
        writer.writeheader()
        for result in state['evaluations']:
            writer.writerow({**dict(zip(PARAMETERS, result['parameters'])),
                             **(result['forces'] or {}), 'checks_passed': result['checks_passed'],
                             'error': result.get('error', '')})
    temp.replace(study/'evaluations.csv')


def export_best(study, destination):
    study, destination = Path(study), Path(destination)
    config, state = read(study/'study.json'), read(study/'search.json')
    verify_inputs(study, config)
    if state['best'] is None or not state['best']['checks_passed']:
        raise ValueError('No candidate has passed the section checks; there is no accepted geometry to export')
    if destination.exists():
        raise ValueError('Choose a new export path; previous trim inputs are preserved')
    destination.mkdir(parents=True)
    geometry = trimmed_geometry(read(study/'source/geometry.json'), state['best']['parameters'])
    save(destination/'geometry.json', geometry)
    for name in ('marine_case.json', 'mesh_metadata.json', 'yacht.cfg', 'airfoil_validation.json'):
        shutil.copy2(study/'source'/name, destination/name)
    save(destination/'trim_result.json', {'search_status': state['status'], 'best': state['best'],
                                        'study': config, 'exported_at_utc': stamp(),
                                        'requires_3d_validation': True, 'solver_3d_run': False})
    for key in state['best'].get('section_keys', []):
        force_file = study/'sections'/key/'force.json'
        if force_file.exists():
            force = read(force_file)
            report = study/force['report']
            force['report'] = 'section_report.json'
            save(destination/'section_force.json', force)
            shutil.copy2(report, destination/'section_report.json')
    repo = Path(__file__).resolve().parents[1]
    try:
        source_path = destination.resolve().relative_to(repo).as_posix()
    except ValueError:
        source_path = 'PATH_TO_EXPORTED_INPUTS'
    write_handoff_readme(destination, source_path)
    print(f'Exported repository inputs to {destination}; add, commit and push this folder.', flush=True)


def write_handoff_readme(destination, source_path='cfd/trimmed_reference'):
    status = read(Path(destination)/'trim_result.json')['search_status']
    instructions = f'''# Selected trim for the 3D run

These small input files belong in the Onsdagsbanen repository. The selected
sheeting angles and fixed 2-degree twist are already in geometry.json.
trim_result.json records the optimisation settings and accepted best result;
section_force.json and section_report.json contain its 2D checks when available.
Search status: `{status}`. This is a ballpark trim pending 3D validation.
Section forces are in N/m, not whole-rig forces.

After pulling the repository on the 3D PC, run from its root:

```powershell
git pull
python -m pip install -r cfd/requirements.txt
python -m cfd.solver --install
python -m cfd.prepare_compact --source {source_path} --case cfd/output/trimmed_3d --threads 8
python -m cfd.marine_rans run --case cfd/output/trimmed_3d --threads 8 --iterations 3000
```

Adjust the thread count for that PC. SU2 automatic installation is for Windows;
other systems need SU2_CFD on PATH. Use a fresh case path if trimmed_3d already
exists. Mesh preparation and the solver are separate; no 3D result is included.
Inspect convergence, loading, separation and background wind-profile checks
before interpreting the final result.
'''
    (Path(destination)/'README.md').write_text(instructions, encoding='utf8')


def run_search(study, max_evaluations=12, max_hours=8.):
    study = Path(study)
    if max_evaluations < 1 or not np.isfinite(max_hours) or max_hours <= 0:
        raise ValueError('Positive evaluation and wall-time budgets required')
    config = read(study/'study.json')
    verify_inputs(study, config)
    from cfd.solver import solver_path
    solver_path()
    lock = study/'search.lock'
    try:
        lock.mkdir()
    except FileExistsError:
        raise RuntimeError(f'{lock} exists; ensure no search is running before removing a stale lock') from None
    try:
        save(lock/'owner.json', {'pid': os.getpid(), 'started_at_utc': stamp()})
        state = read(study/'search.json')
        if state['status'] == 'ballpark_found':
            print('Ballpark search already finished.', flush=True)
            return state
        deadline = time.monotonic() + max_hours*3600
        key = lambda values: tuple(np.round(values, 10))
        previous = {key(r['parameters']): r for r in state['evaluations']}

        def objective(values):
            point = key(values)
            if point in previous:
                result = previous[point]
            else:
                if len(state['evaluations']) >= max_evaluations:
                    raise BudgetExpired('evaluation_budget')
                check_deadline(deadline)
                state.update(status='running', active_parameters=values.tolist(), updated_at_utc=stamp())
                save(study/'search.json', state)
                print(f'Evaluation {len(state["evaluations"])+1}/{max_evaluations}: {dict(zip(PARAMETERS, values))}', flush=True)
                result = evaluate(study, config, values, deadline=deadline)
                previous[point] = result
                state['evaluations'].append(result)
                if result['checks_passed']:
                    drive = result['forces']['drive_N_per_m']
                    state.setdefault('force_scale_N_per_m', max(abs(drive), 1.))
                    if state['best'] is None or drive > state['best']['forces']['drive_N_per_m']:
                        state['best'] = result
                        save(study/'best_geometry.json', trimmed_geometry(read(study/'source/geometry.json'), result['parameters']))
                state.pop('active_parameters', None)
                drive_text = f'{result["forces"]["drive_N_per_m"]:.2f} N/m' if result['forces'] else 'unavailable'
                print(f'  drive={drive_text}; accepted={result["checks_passed"]}', flush=True)
                save(study/'search.json', state)
                write_evaluations(study, state)
            if not result['checks_passed']:
                return 1e6
            return -result['forces']['drive_N_per_m']/state['force_scale_N_per_m']

        try:
            # A deterministic replay reads previously evaluated candidates from
            # cache. Resuming does not spend another CFD run on those points.
            result = minimize(objective, config['initial'], method='Nelder-Mead',
                              bounds=np.asarray(config['bounds']),
                              options={'initial_simplex': np.asarray(config['initial_simplex']),
                                       'xatol': config['angle_tolerance_deg'],
                                       'fatol': config['relative_force_tolerance'],
                                       'maxfev': max(100, 8*max_evaluations)})
            state['status'] = ('ballpark_found' if result.success else 'budget_exhausted') if state['best'] else 'no_accepted_candidate'
            state['optimizer_message'] = str(result.message)
            state['simplex_iterations'] = int(result.nit)
        except BudgetExpired as error:
            state['status'] = 'budget_exhausted'
            state['stop_reason'] = str(error) or 'wall_time_budget'
        state['updated_at_utc'] = stamp()
        save(study/'search.json', state)
        write_evaluations(study, state)
        return state
    except BaseException:
        if (study/'search.json').exists():
            state = read(study/'search.json')
            state['status'] = 'interrupted'
            save(study/'search.json', state)
        raise
    finally:
        (lock/'owner.json').unlink(missing_ok=True)
        lock.rmdir()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'run', 'status', 'export'))
    parser.add_argument('--study', type=Path, default=Path('cfd/cases/trim_2d_fixed_twist'))
    parser.add_argument('--source', type=Path, default=REFERENCE)
    parser.add_argument('--initial-trim', type=Path, help='Seed sheeting angles from this previous trim directory (prepare only)')
    parser.add_argument('--iterations', type=int, default=3500)
    parser.add_argument('--continuations', type=int, default=1)
    parser.add_argument('--threads', type=int, default=2)
    parser.add_argument('--sail-size', type=float, default=.06)
    parser.add_argument('--height', type=float, help='One paired-sail section height; defaults to the sail-area centroid')
    parser.add_argument('--max-evaluations', type=int, default=12)
    parser.add_argument('--max-hours', type=float, default=8.)
    parser.add_argument('--destination', type=Path)
    parser.add_argument('--export-to', type=Path, help='After run, save the best accepted candidate as repository inputs for the 3D PC')
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare_study(args.source, args.study, iterations=args.iterations, continuations=args.continuations,
                      threads=args.threads, sail_size=args.sail_size,
                      height=args.height, initial_trim=args.initial_trim)
    elif args.action == 'run':
        state = run_search(args.study, args.max_evaluations, args.max_hours)
        print(json.dumps({'status': state['status'], 'evaluations': len(state['evaluations']), 'best': state['best']}, indent=2))
        if args.export_to:
            if state['best'] is None:
                print('No candidate passed all checks; no 3D input folder was created.')
            else:
                export_best(args.study, args.export_to)
    elif args.action == 'status':
        state = read(args.study/'search.json')
        names = read(args.study/'study.json')['parameters']
        print(json.dumps({'status': state['status'], 'evaluations': len(state['evaluations']),
                          'active_parameters': dict(zip(names, state.get('active_parameters', []))),
                          'best_parameters': dict(zip(names, state['best']['parameters'])) if state['best'] else None,
                          'best_forces': state['best']['forces'] if state['best'] else None}, indent=2))
    else:
        if args.destination is None:
            parser.error('export requires --destination')
        export_best(args.study, args.destination)


if __name__ == '__main__':
    main()
