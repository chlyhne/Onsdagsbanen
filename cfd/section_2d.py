"""Independent horizontal 2D SST RANS through the saved yacht geometry.

python -m cfd.section_2d prepare --case cfd/cases/section_2d
python -m cfd.section_2d run --case cfd/cases/section_2d
python -m cfd.section_2d analyse --case cfd/cases/section_2d
Preparation and plotting never start the solver.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from cfd.case import BOAT_LENGTH, SAIL_FOOT_HEIGHT, case_section
from cfd.marine_rans import overrides, read_profile, sha
from cfd.storage import case_file, create_case

REFERENCE = Path(__file__).resolve().parent / 'reference'
MARKERS = {'farfield': 10, 'mast': 12, 'main': 13, 'jib': 14}
LIMITATIONS = [
    'Independent 2D RANS, not a slice extracted from the 3D solution.',
    'No vertical velocity, tip vortices, spanwise transport, hull or sea boundary.',
    'Wind shear and sail twist are sampled at the selected height only.',
    'Forces describe one metre of span, not the complete yacht.',
    'Mesh independence and agreement with the 3D model have not been established.',
]


def save_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False), encoding='utf8')


def boat_offsets(geometry):
    offsets = np.asarray(geometry.get('boat_offsets_xy_m', [[0., 0.]]), dtype=float)
    if offsets.ndim != 2 or offsets.shape[1] != 2 or not len(offsets) or not np.isfinite(offsets).all():
        raise ValueError('Boat offsets must be finite (x, y) coordinates')
    return offsets


def active_sails(geometry, height=None):
    height = geometry.get('section_height_m') if height is None else height
    return [i for i, sail in enumerate(geometry['sail_definitions'])
            if height is None or SAIL_FOOT_HEIGHT <= height <= SAIL_FOOT_HEIGHT + sail[2]]


def wall_markers(geometry, height=None):
    active = {geometry['sail_shapes'][i]['name'] for i in active_sails(geometry, height)} if geometry else {'main', 'jib'}
    return {name if i == 0 else f'{name}_{i + 1}': tag + 10 * i
            for i in range(len(boat_offsets(geometry)))
            for name, tag in MARKERS.items() if name == 'mast' or name in active}


def add_second_boat(geometry, wind_angle, aft_lengths=.75, windward_beams=2.):
    """Parallel boats; offsets are centre-to-centre along/athwart the hull."""
    if not np.isfinite([aft_lengths, windward_beams, wind_angle]).all() or aft_lengths < 0 or windward_beams <= 0:
        raise ValueError('Require a nonnegative aft distance and positive windward distance')
    side = np.sin(np.radians(wind_angle))
    if abs(side) < 1e-8:
        raise ValueError('Windward side is undefined when the wind is along the centreline')
    hull = np.asarray(geometry['hull'])
    length, beam = np.ptp(hull, axis=0)
    offset = [-float(aft_lengths * length), -float(np.sign(side) * windward_beams * beam)]
    return {**geometry, 'boat_offsets_xy_m': [[0., 0.], offset],
            'boat_spacing': {'aft_lengths': aft_lengths, 'windward_beams': windward_beams,
                             'boat_length_m': float(length), 'boat_beam_m': float(beam),
                             'reference': 'Centre-to-centre; +x forward, +y port; no rotation'}}


def plot_geometry(geometry, height):
    offsets = boat_offsets(geometry)
    hull = np.asarray(geometry['hull'])
    sections = [case_section(geometry, i, height) for i in active_sails(geometry, height)]
    return {'hull_outline': hull.tolist(),
            'hull_outlines': [(hull + offset).tolist() for offset in offsets],
            'sail_sections': [(section + np.r_[offset, 0.]).tolist()
                              for offset in offsets for section in sections],
            'boat_offsets_xy_m': offsets.tolist()}


def mesh_2d(case, geometry, height, size=2., sail_size=.035, threads=2):
    """Closed sail skins and mast, with wall-normal quad layers in real 2D."""
    import gmsh
    gmsh.initialize()
    try:
        gmsh.option.setNumber('General.Terminal', 0)
        gmsh.option.setNumber('General.NumThreads', threads)
        gmsh.model.add('horizontal_sail_section')
        geo = gmsh.model.geo
        corners = [geo.addPoint(x, y, 0., size) for x, y in
                   ((-50., -50.), (35., -50.), (35., 40.), (-50., 40.))]
        outer = [geo.addLine(corners[i], corners[(i + 1) % 4]) for i in range(4)]
        holes = []; walls = {}
        markers = {'farfield': MARKERS['farfield'], **wall_markers(geometry, height)}
        for boat, offset in enumerate(boat_offsets(geometry)):
            def wall_name(name):
                return name if boat == 0 else f'{name}_{boat + 1}'
            for i, shape in enumerate(geometry['sail_shapes']):
                if i not in active_sails(geometry, height):
                    continue
                xy = case_section(geometry, i, height)[:, :2] + offset
                nodes = [geo.addPoint(x, y, 0., sail_size) for x, y in xy]
                half = len(nodes) // 2
                curves = [geo.addSpline(nodes[:half]), geo.addLine(nodes[half - 1], nodes[half]),
                          geo.addSpline(nodes[half:]), geo.addLine(nodes[-1], nodes[0])]
                # Preserve finite-thickness tips and identical resolution on
                # both boats, with chordwise clustering near each tip.
                for curve in (curves[0], curves[2]):
                    geo.mesh.setTransfiniteCurve(curve, max(97, int(np.ptp(xy[:, 0]) / sail_size)), 'Bump', .05)
                for curve in (curves[1], curves[3]):
                    geo.mesh.setTransfiniteCurve(curve, 2)
                holes.append(geo.addCurveLoop(curves))
                walls[wall_name(shape['name'])] = curves
            ox, oy = offset
            center = geo.addPoint(ox + .15, oy, 0., sail_size)
            ring = [geo.addPoint(ox + .15 + .075 * np.cos(a), oy + .075 * np.sin(a), 0., sail_size / 2)
                    for a in np.arange(4) * np.pi / 2]
            mast = [geo.addCircleArc(ring[i], center, ring[(i + 1) % 4]) for i in range(4)]
            walls[wall_name('mast')] = mast
            holes.append(geo.addCurveLoop(mast))
        surface = geo.addPlaneSurface([geo.addCurveLoop(outer), *holes])
        geo.synchronize()
        for name, curves in {'farfield': outer, **walls}.items():
            gmsh.model.addPhysicalGroup(1, curves, markers[name])
            gmsh.model.setPhysicalName(1, markers[name], name)
        gmsh.model.addPhysicalGroup(2, [surface], 1)
        gmsh.model.setPhysicalName(2, 1, 'air')
        wall_curves = [curve for curves in walls.values() for curve in curves]
        distance = gmsh.model.mesh.field.add('Distance')
        gmsh.model.mesh.field.setNumbers(distance, 'CurvesList', wall_curves)
        gmsh.model.mesh.field.setNumber(distance, 'Sampling', 1000)
        threshold = gmsh.model.mesh.field.add('Threshold')
        for key, value in {'InField': distance, 'SizeMin': sail_size, 'SizeMax': size,
                           'DistMin': .15, 'DistMax': 12.}.items():
            gmsh.model.mesh.field.setNumber(threshold, key, value)
        gmsh.model.mesh.field.setAsBackgroundMesh(threshold)
        layer = gmsh.model.mesh.field.add('BoundaryLayer')
        gmsh.model.mesh.field.setNumbers(layer, 'CurvesList', wall_curves)
        first, growth, count = 12e-6, 1.2, 30
        for key, value in {'Size': first, 'Ratio': growth,
                           'Thickness': sum(first * growth**i for i in range(count)),
                           'Quads': 1}.items():
            gmsh.model.mesh.field.setNumber(layer, key, value)
        gmsh.model.mesh.field.setAsBoundaryLayer(layer)
        gmsh.option.setNumber('Mesh.Algorithm', 6)
        gmsh.option.setNumber('Mesh.MeshSizeFromCurvature', 0)
        gmsh.option.setNumber('Mesh.MeshSizeExtendFromBoundary', 0)
        gmsh.model.mesh.generate(2)
        _, element_tags, element_nodes = gmsh.model.mesh.getElements(2)
        qualities = gmsh.model.mesh.getElementQualities(np.concatenate(element_tags), 'minDetJac')
        if not len(qualities) or np.min(qualities) <= 0:
            raise RuntimeError('Invalid 2D mesh: nonpositive cell Jacobian')
        gmsh.option.setNumber('Mesh.MshFileVersion', 2.2)
        gmsh.write(str(case_file(case, 'yacht.msh')))
        gmsh.write(str(case_file(case, 'yacht.su2')))
        return {'dimensions': 2, 'points': len(np.unique(np.concatenate(element_nodes))),
                'cells': len(qualities), 'minimum_jacobian': float(np.min(qualities)),
                'domain_min_m': [-50., -50.], 'domain_max_m': [35., 40.],
                'mesh_size_m': size, 'sail_size_m': sail_size,
                'boundary_layers': {'first_spacing_m': first, 'growth': growth,
                                    'requested_layers': count}, 'markers': markers}
    finally:
        gmsh.finalize()


def configuration(case, profile, height, iterations=3000, restart=False):
    from cfd.solver import configuration as base_configuration
    wind = profile.at(height)
    turbulence = profile.turbulence_at(height)
    args = SimpleNamespace(wind_speed=wind['true_speed_m_s'], wind_angle=profile.true_angle_deg,
                           boat_speed=profile.boat_speed_knots, iterations=iterations, restart=restart)
    velocity = base_configuration(args, case_file(case, 'yacht.cfg').parent)
    text = (case_file(case, 'yacht.cfg')).read_text(encoding='ascii')
    text = text.replace('steady 3D apparent-wind', 'steady 2D horizontal-section')
    geometry_file = case_file(case, 'geometry.json')
    geometry = json.loads(geometry_file.read_text(encoding='utf8')) if geometry_file.exists() else {}
    markers = wall_markers(geometry)
    walls = '(' + ', '.join(markers) + ')'
    values = {
        'MARKER_HEATFLUX': '(' + ', '.join(f'{name}, 0.0' for name in markers) + ')', 'MARKER_EULER': None,
        'MARKER_MONITORING': walls, 'MARKER_PLOTTING': walls,
        'REF_AREA': '1.0', 'REF_LENGTH': '1.0',
        'FREESTREAM_TURBULENCEINTENSITY': str(turbulence['intensity_relative_to_apparent_speed']),
        'FREESTREAM_TURB2LAMVISCRATIO': str(turbulence['eddy_viscosity_m2_s'] / (1.81e-5 / 1.225)),
        'CONV_FIELD': '(RMS_PRESSURE, RMS_VELOCITY-X, RMS_VELOCITY-Y, FORCE_X, FORCE_Y)',
        # Our post-run SST checks use separate absolute/relative tolerances.
        # Native flow/force convergence alone must not end a continuation
        # before the turbulence equations and saved fields can settle.
        'CONV_STARTITER': str(iterations + 1),
        'SCREEN_OUTPUT': '(INNER_ITER, RMS_PRESSURE, RMS_VELOCITY-X, RMS_VELOCITY-Y, RMS_TKE, RMS_DISSIPATION, FORCE_X, FORCE_Y)',
        'SCREEN_WRT_FREQ_INNER': '1', 'MAX_UPDATE_SST': '0.2',
    }
    if (case / 'input').is_dir():
        values.update(MESH_FILENAME='input/yacht.su2', SOLUTION_FILENAME='checkpoints/restart',
                      RESTART_FILENAME='checkpoints/restart', CONV_FILENAME='history/history',
                      VOLUME_FILENAME='fields/flow', SURFACE_FILENAME='fields/surface')
    (case_file(case, 'yacht.cfg')).write_text(overrides(text, values), encoding='ascii')
    return velocity


def prepare(source, case, height=4., wind_speed=None, wind_angle=None, boat_speed=None,
            mesh_size=2., sail_size=.035, threads=2, *, second_boat=False,
            aft_lengths=.75, windward_beams=2.):
    source, case = Path(source).resolve(), Path(case).resolve()
    if case.exists():
        raise ValueError('Choose a new case directory; existing meshes/results are preserved')
    geometry = json.loads((case_file(source, 'geometry.json')).read_text(encoding='utf8'))
    source_manifest = case_file(source, 'marine_case.json')
    if not source_manifest.exists():
        source_manifest = case_file(source, 'section_case.json')
    manifest = json.loads(source_manifest.read_text(encoding='utf8'))
    profile = read_profile(manifest)
    changes = {key: value for key, value in
               (('reference_speed_m_s', wind_speed), ('true_angle_deg', wind_angle),
                ('boat_speed_knots', boat_speed)) if value is not None}
    profile = replace(profile, **changes)
    if profile.kind != 'neutral_log':
        raise ValueError('The source must provide a neutral_log atmospheric turbulence profile')
    if not np.isfinite([height, mesh_size, sail_size, profile.reference_speed_m_s,
                        profile.true_angle_deg, profile.boat_speed_knots]).all():
        raise ValueError('Height, wind and mesh settings must be finite')
    top = max(SAIL_FOOT_HEIGHT + sail[2] for sail in geometry['sail_definitions'])
    if not SAIL_FOOT_HEIGHT <= height <= top:
        raise ValueError(f'Height must intersect a sail: {SAIL_FOOT_HEIGHT:g} to {top:g} m')
    if not mesh_size > 0 or not sail_size > 0 or threads < 1:
        raise ValueError('Mesh sizes and threads must be positive')
    if [s['name'] for s in geometry['sail_shapes']] != ['main', 'jib']:
        raise ValueError('Expected saved main and jib geometry')
    if second_boat:
        geometry = add_second_boat(geometry, profile.true_angle_deg, aft_lengths, windward_beams)
    geometry['section_height_m'] = float(height)
    create_case(case)
    metadata = mesh_2d(case / 'input', geometry, height, mesh_size, sail_size, threads)
    save_json(case_file(case, 'geometry.json'), geometry)
    save_json(case_file(case, 'mesh_metadata.json'), metadata)
    velocity = configuration(case, profile, height)
    result = {'model': '2D steady INC_RANS SST V1994m', 'status': 'prepared_not_simulated',
              'solver_started': False, 'source_case': str(source), 'height_m': height,
              'profile': profile.metadata(), 'reference_velocity_m_s': velocity.tolist(),
              'boat_offsets_xy_m': boat_offsets(geometry).tolist(),
              'boat_spacing': geometry.get('boat_spacing'),
              'mesh_sha256': sha(case_file(case, 'yacht.su2')), 'limitations': LIMITATIONS}
    save_json(case_file(case, 'section_case.json'), result)
    print(f'2D case ready: {case}; {metadata["points"]} points; no CFD started', flush=True)
    return result


def triangles_from_mesh(mesh):
    triangles = []
    for block in mesh.cells:
        if block.type == 'triangle':
            triangles.append(block.data)
        elif block.type == 'quad':
            triangles.extend((block.data[:, [0, 1, 2]], block.data[:, [0, 2, 3]]))
    if not triangles:
        raise ValueError('No two-dimensional fluid cells')
    result = np.concatenate(triangles).copy()
    p = mesh.points[result, :2]
    determinant = ((p[:, 1, 0] - p[:, 0, 0]) * (p[:, 2, 1] - p[:, 0, 1])
                   - (p[:, 1, 1] - p[:, 0, 1]) * (p[:, 2, 0] - p[:, 0, 0]))
    if np.any(determinant == 0):
        raise ValueError('Degenerate 2D fluid cells')
    reverse = determinant < 0
    result[reverse] = result[reverse][:, [0, 2, 1]]
    return result


def field_stability(case, manifest, names, native, last_iteration):
    """Compare current-run fields over at least 100 stationary iterations."""
    from cfd.compare_snapshots import comparison_frames
    from cfd.transfer_solution import read_restart

    started = manifest.get('started_at_utc')
    pair = comparison_frames(case, datetime.fromisoformat(started).timestamp(), last_iteration) if started else None
    if pair is None:
        return {'passed': False, 'reason': 'Need two current-run snapshots at least 100 iterations apart'}
    before, after = pair
    if sha(after[1]) != sha(case_file(case, 'restart.dat')):
        return {'passed': False, 'reason': 'Latest snapshot does not match the current solution'}
    old_names, old = read_restart(before[1])
    coordinates = [names.index(name) for name in ('x', 'y')]
    if old_names != names or old.shape != native.shape or not np.array_equal(old[:, coordinates], native[:, coordinates]):
        raise ValueError('Snapshots do not use the same 2D mesh and fields')
    speed = np.linalg.norm(manifest['reference_velocity_m_s'])
    indices = [names.index(name) for name in ('Velocity_x', 'Velocity_y')]
    u0, u1 = old[:, indices] * speed, native[:, indices] * speed
    k0, k1 = old[:, names.index('Turb_Kin_Energy')], native[:, names.index('Turb_Kin_Energy')]
    if not np.isfinite(old).all() or np.any(k0 < 0):
        raise ValueError('Invalid earlier 2D snapshot')
    xy = native[:, coordinates]
    visible = (xy[:, 0] >= -50) & (xy[:, 0] <= 15) & (xy[:, 1] >= -30) & (xy[:, 1] <= 40)
    direction = visible & (np.linalg.norm(u0, axis=1) > .1 * speed) & (np.linalg.norm(u1, axis=1) > .1 * speed)
    angle = np.degrees(np.arctan2(u0[:, 0] * u1[:, 1] - u0[:, 1] * u1[:, 0], np.sum(u0 * u1, axis=1)))
    changes = {'velocity_vector_m_s': float(np.max(np.linalg.norm(u1 - u0, axis=1)[visible])),
               'direction_deg': float(np.max(np.abs(angle[direction]))) if direction.any() else None,
               'turbulence_percentage_points': float(np.max(np.abs(100 * (np.sqrt(2 * np.maximum(k1, 0) / 3) - np.sqrt(2 * k0 / 3)))[visible]))}
    limits = {'velocity_vector_m_s': .01, 'direction_deg': .1, 'turbulence_percentage_points': .1}
    checks = {key: value is not None and value < limits[key] for key, value in changes.items()}
    return {'before_sha256': sha(before[1]), 'after_sha256': sha(after[1]),
            'native_iterations': [before[0], after[0]], 'iteration_window': after[0] - before[0],
            'maximum_changes': changes, 'maximum_change_limits': limits, 'checks': checks,
            'passed': all(checks.values()), 'scope': 'All fluid in the displayed viewport; direction excludes near-zero speeds'}


def analyse(case):
    """Check a stored 2D solution and save the existing wind renderer's format."""
    import meshio
    from scipy.spatial import cKDTree
    from cfd.rans_checks import convergence
    from cfd.transfer_solution import read_restart

    case = Path(case)
    manifest = json.loads((case_file(case, 'section_case.json')).read_text(encoding='utf8'))
    geometry = json.loads((case_file(case, 'geometry.json')).read_text(encoding='utf8'))
    mesh = meshio.read(case_file(case, 'yacht.msh'))
    names, native = read_restart(case_file(case, 'restart.dat'))
    required = ['x', 'y', 'Pressure', 'Velocity_x', 'Velocity_y', 'Turb_Kin_Energy', 'Omega']
    if any(name not in names for name in required) or len(native) != len(mesh.points):
        raise ValueError('Expected a 2D incompressible SST restart on this mesh')
    column = lambda name: native[:, names.index(name)]
    if not np.allclose(np.column_stack((column('x'), column('y'))), mesh.points[:, :2], rtol=0, atol=1e-10):
        raise ValueError('Binary solution does not match the original 2D mesh')
    reference = np.asarray(manifest['reference_velocity_m_s'])[:2]
    speed = np.linalg.norm(reference)
    uv = np.column_stack((column('Velocity_x'), column('Velocity_y'))) * speed
    k = column('Turb_Kin_Energy') * speed**2
    if not np.isfinite(uv).all() or not np.isfinite(k).all() or np.any(k < -1e-12):
        raise ValueError('Invalid velocity or turbulent kinetic energy')
    local_speed = np.linalg.norm(uv, axis=1)
    angle = np.degrees(np.arctan2(reference[0] * uv[:, 1] - reference[1] * uv[:, 0], uv @ reference))
    angle[local_speed < .1 * speed] = np.nan
    np.savez_compressed(case_file(case, 'slice.npz'), xy=mesh.points[:, :2], triangles=triangles_from_mesh(mesh),
                        speed_delta=local_speed - speed, angle_deg=angle,
                        turbulence_percent=100 * np.sqrt(2 * np.maximum(k, 0) / 3) / speed)
    history = np.genfromtxt(case_file(case, 'history.csv'), delimiter=',', names=True, deletechars=' "')
    numerical = convergence(case, history, dimensions=2)
    surface = meshio.read(case_file(case, 'surface.vtk'))
    distance, index = cKDTree(mesh.points[:, :2]).query(surface.points[:, :2])
    if distance.max() > 2e-5:
        raise ValueError('Surface output does not match the 2D mesh')
    yplus = np.full(len(mesh.points), np.nan)
    yplus[index] = surface.point_data['Y_Plus'].reshape(-1)
    walls = {}
    for name, tag in wall_markers(geometry).items():
        edges = np.concatenate([block.data[tags == tag] for block, tags in
                                zip(mesh.cells, mesh.cell_data['gmsh:physical']) if block.type == 'line'])
        values = yplus[np.unique(edges)]
        valid = bool(len(values) and np.isfinite(values).all())
        walls[name] = {'p95_yplus': float(np.percentile(values, 95)) if valid else None,
                       'max_yplus': float(values.max()) if valid else None,
                       'passed': bool(valid and np.percentile(values, 95) < 2 and values.max() < 5)}
    last_iteration = int(np.atleast_1d(history['Inner_Iter'])[-1])
    stability = field_stability(case, manifest, names, native, last_iteration)
    passed = numerical['passed'] and stability['passed'] and all(wall['passed'] for wall in walls.values())
    report = {'model': manifest['model'], 'height_m': manifest['height_m'],
              'convergence': numerical, 'wall_resolution': walls, 'checks_passed': passed,
              'field_stability': stability, 'iterations': last_iteration + 1,
              'boat_offsets_xy_m': boat_offsets(geometry).tolist(),
              'iterations_scope': 'Last solver invocation; restart iterations are not cumulative',
              'limitations': LIMITATIONS}
    save_json(case_file(case, 'report.json'), report)
    profile = read_profile(manifest)
    count = len(boat_offsets(geometry))
    context = {'format_version': 1, 'slice_file': 'slice.npz', 'height_m': manifest['height_m'],
               'boat_length_m': BOAT_LENGTH, 'wind': {'true_angle_deg': profile.true_angle_deg},
               **plot_geometry(geometry, manifest['height_m']),
               'title_suffix': ' · 2D' + (f' · {count} både' if count > 1 else ''),
               'checks_passed': passed, 'status_text': '2D kontrolkrav opfyldt' if passed else '2D: IKKE dokumenteret konvergeret',
               'analysis_note': 'Horizontal 2D RANS; hull footprint is a visual reference only.'}
    save_json(case_file(case, 'plot_context.json'), context)
    manifest['status'] = 'checks_passed' if passed else 'checks_failed'
    save_json(case_file(case, 'section_case.json'), manifest)
    return report


def run(case, threads=2, iterations=3000, restart=False, *, plot=True):
    from cfd.solver import solver_path
    from cfd.solver_progress import run_solver
    from cfd.rans_checks import archive_history, convergence
    from cfd.plot_wind import render

    if threads < 1 or iterations < 1:
        raise ValueError('Threads and iterations must be positive')
    case = Path(case).resolve()
    manifest = json.loads((case_file(case, 'section_case.json')).read_text(encoding='utf8'))
    if sha(case_file(case, 'yacht.su2')) != manifest['mesh_sha256']:
        raise ValueError('Prepared mesh changed; prepare a new case')
    has_result = (case_file(case, 'restart.dat')).exists()
    if restart and not has_result:
        raise ValueError('No restart.dat to continue')
    if not restart and any(case_file(case, name).exists() for name in ('restart.dat', 'history.csv', 'solver.log')):
        raise ValueError('Solver output already exists; use --restart or prepare a new case')
    executable = solver_path()
    if restart and (case_file(case, 'history.csv')).exists():
        history = np.genfromtxt(case_file(case, 'history.csv'), delimiter=',', names=True, deletechars=' "')
        convergence(case, history, dimensions=2)
        archive_history(case)
    configuration(case, read_profile(manifest), manifest['height_m'], iterations, restart)
    manifest.pop('finished_at_utc', None)
    manifest.pop('solver_error', None)
    manifest.update(status='running', solver_started=True, started_at_utc=datetime.now(timezone.utc).isoformat())
    save_json(case_file(case, 'section_case.json'), manifest)
    environment = os.environ.copy()
    environment.update(OMP_NUM_THREADS=str(threads), OMP_WAIT_POLICY='PASSIVE')
    try:
        run_solver([str(executable), str(case_file(case, 'yacht.cfg').relative_to(case)), '-t', str(threads)], cwd=case,
                   env=environment, iterations=iterations)
    except BaseException as error:
        manifest.update(status='failed', solver_error=f'{type(error).__name__}: {error}')
        save_json(case_file(case, 'section_case.json'), manifest)
        raise
    manifest['status'] = 'solver_finished'
    manifest['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
    save_json(case_file(case, 'section_case.json'), manifest)
    report = analyse(case)
    if plot:
        render(case)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'run', 'analyse', 'plot'))
    parser.add_argument('--case', type=Path, default=Path('cfd/cases/section_2d'))
    parser.add_argument('--source', type=Path, default=REFERENCE)
    parser.add_argument('--height', type=float, default=4.)
    parser.add_argument('--wind-speed', type=float, help='True wind at the source reference height, m/s')
    parser.add_argument('--wind-angle', type=float, help='True wind angle from starboard, degrees')
    parser.add_argument('--boat-speed', type=float, help='Boat speed, knots')
    parser.add_argument('--mesh-size', type=float, default=2.)
    parser.add_argument('--sail-size', type=float, default=.035)
    parser.add_argument('--threads', type=int, default=2)
    parser.add_argument('--iterations', type=int, default=3000)
    parser.add_argument('--restart', action='store_true')
    parser.add_argument('--second-boat', action='store_true', help='Add an identical, parallel windward boat')
    parser.add_argument('--aft-lengths', type=float, default=.75, help='Second boat aft, in boat lengths, centre-to-centre')
    parser.add_argument('--windward-beams', type=float, default=2., help='Second boat windward, in hull beams, centre-to-centre')
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare(args.source, args.case, args.height, args.wind_speed, args.wind_angle,
                args.boat_speed, args.mesh_size, args.sail_size, args.threads,
                second_boat=args.second_boat, aft_lengths=args.aft_lengths, windward_beams=args.windward_beams)
    elif args.action == 'run':
        if not run(args.case, args.threads, args.iterations, args.restart)['checks_passed']:
            raise SystemExit('2D solver finished; checks failed. Inspect report.json; plots were preserved.')
    elif args.action == 'analyse':
        if not analyse(args.case)['checks_passed']:
            raise SystemExit('2D checks failed; inspect report.json')
    else:
        from cfd.plot_wind import render
        render(args.case)


if __name__ == '__main__':
    main()
