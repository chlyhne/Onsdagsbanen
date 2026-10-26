"""Dimensional 2D forces and reverse wall shear on the original wall edges."""
from pathlib import Path
import json

import meshio
import numpy as np
from scipy.spatial import cKDTree

from cfd.case import case_chord_trim
from cfd.rans_mesh import FOOT, camber_profile
from cfd.section_2d import wall_markers
from cfd.storage import case_file


def edge_force(xy, edges, cp, cf, q):
    """Force on a closed body per metre of vertical span, independent of winding."""
    p = xy[edges]
    delta = p[:, 1] - p[:, 0]
    winding = np.sign(np.sum(p[:, 0, 0]*p[:, 1, 1] - p[:, 1, 0]*p[:, 0, 1]))
    if not winding or not np.isfinite(cp[edges]).all() or not np.isfinite(cf[edges]).all():
        raise ValueError('Incomplete or degenerate closed wall')
    outward = winding * np.column_stack((delta[:, 1], -delta[:, 0]))
    length = np.linalg.norm(delta, axis=1)
    pressure = -q * np.sum(cp[edges].mean(axis=1)[:, None] * outward, axis=0)
    friction = q * np.sum(cf[edges].mean(axis=1) * length[:, None], axis=0)
    return pressure, friction


def loads(case):
    case = Path(case)
    geometry = json.loads(case_file(case, 'geometry.json').read_text())
    manifest = json.loads(case_file(case, 'section_case.json').read_text())
    mesh = meshio.read(case_file(case, 'yacht.msh'))
    surface = meshio.read(case_file(case, 'surface.vtk'))
    distance, index = cKDTree(mesh.points[:, :2]).query(surface.points[:, :2])
    if np.max(distance) > 2e-5:
        raise ValueError('Surface coordinates do not match the 2D mesh')
    cp, cf = np.full(len(mesh.points), np.nan), np.full((len(mesh.points), 2), np.nan)
    cp[index] = surface.point_data['Pressure_Coefficient'].reshape(-1)
    cf[index] = surface.point_data['Skin_Friction_Coefficient'][:, :2]
    velocity = np.asarray(manifest['reference_velocity_m_s'])[:2]
    q = .5 * 1.225 * float(velocity @ velocity)
    total = np.zeros(2)
    bodies = {}
    for name, tag in wall_markers(geometry).items():
        edges = np.concatenate([block.data[labels == tag] for block, labels in
                               zip(mesh.cells, mesh.cell_data['gmsh:physical']) if block.type == 'line'])
        pressure, friction = edge_force(mesh.points[:, :2], edges, cp, cf, q)
        force = pressure + friction
        total += force
        body = {'force_N_per_m': force.tolist(), 'pressure_N_per_m': pressure.tolist(),
                'friction_N_per_m': friction.tolist()}
        if name in ('main', 'jib'):
            i = [s['name'] for s in geometry['sail_shapes']].index(name)
            sail, shape = geometry['sail_definitions'][i], geometry['sail_shapes'][i]
            height = manifest['height_m']
            f = (height - FOOT) / sail[2]
            chord = sail[1]*(1-.96*f)
            leading = sail[0]*(1-f) + shape['head_luff_x_m']*f
            angle = np.radians(case_chord_trim(geometry, i, height))
            centers = mesh.points[edges, :2].mean(axis=1)
            u = -(centers[:, 0]-leading)*np.cos(angle) + centers[:, 1]*np.sin(angle)
            slope = camber_profile(np.clip(u/chord, 0, 1), shape)[1]
            tangent = np.column_stack((-np.cos(angle)+slope*np.sin(angle),
                                       np.sin(angle)+slope*np.cos(angle))) / np.sqrt(1+slope**2)[:, None]
            cft = np.sum(cf[edges].mean(axis=1)*tangent, axis=1)
            length = np.linalg.norm(np.diff(mesh.points[edges, :2], axis=1)[:, 0], axis=1)
            # Ignore finite-thickness end caps, whose chordwise extent is zero.
            edge_u = -(mesh.points[edges, 0]-leading)*np.cos(angle) + mesh.points[edges, 1]*np.sin(angle)
            skin = np.abs(np.diff(edge_u, axis=1)[:, 0]) > 1e-10
            body['reverse_wall_shear_length_percent'] = float(100*np.sum(length[skin & (cft < -1e-5)])/np.sum(length[skin]))
        bodies[name] = body
    history = np.genfromtxt(case_file(case, 'history.csv'), delimiter=',', names=True, deletechars=' "')
    monitored = q * np.array([np.atleast_1d(history[n])[-1] for n in ('CFx', 'CFy')])
    error = np.linalg.norm(total-monitored)/max(np.linalg.norm(monitored), 1.)
    result = {'height_m': manifest['height_m'], 'dynamic_pressure_Pa': q,
              'force_N_per_m': total.tolist(), 'bodies': bodies,
              'history_force_N_per_m': monitored.tolist(),
              'relative_force_integration_error': float(error), 'force_integration_passed': bool(error < .01),
              'axes': {'x': 'forward', 'y': 'port'},
              'reverse_flow_note': 'Reverse wall-shear length is a separation indicator, not full separation topology.'}
    return result
