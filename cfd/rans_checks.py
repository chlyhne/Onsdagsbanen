"""Residual, force-stability and wall-resolution checks with explicit criteria."""
from __future__ import annotations

import json
import shutil
import numpy as np

FLOW_LOG10_MAX = -6.
TURBULENCE_REDUCTION_ORDERS = 6.
TURBULENCE_ABSOLUTE_LOG10_MAX = {'rms[k]': -11., 'rms[w]': -5.}
FORCE_WINDOW = 200
FORCE_RELATIVE_RANGE_MAX = .001
FORCE_NORMALIZATION_FLOOR = .01


def archive_history(out):
    folder=out/'history_segments';folder.mkdir(exist_ok=True)
    name=f'{len(list(folder.glob("*.csv"))):03d}'
    shutil.copy2(out/'history.csv',folder/f'{name}.csv')
    if (out/'run_parameters.json').exists():
        shutil.copy2(out/'run_parameters.json',folder/f'{name}.json')
    shutil.copy2(out/'history.csv',out/'history_before_restart.csv')
    return str(folder/f'{name}.csv')


def convergence(out,history,dimensions=3):
    names=history.dtype.names
    def column(name):
        return np.atleast_1d(history[name])
    reference_file=out/'residual_reference.json'
    if reference_file.exists():
        reference=json.loads(reference_file.read_text())
    else:
        source=history
        previous=out/'history_before_restart.csv'
        if previous.exists():
            source=np.genfromtxt(previous,delimiter=',',names=True,deletechars=' "')
        reference={name:float(np.max(np.atleast_1d(source[name]))) for name in ('rms[k]','rms[w]')}
        reference_file.write_text(json.dumps(reference,indent=2),encoding='utf8')
    flow=['rms[P]','rms[U]','rms[V]']+(['rms[W]'] if dimensions==3 else [])
    last={name:float(column(name)[-1]) for name in names}
    relative={name:last[name]-reference[name] for name in ('rms[k]','rms[w]')}
    # A transferred field may already have very small residuals. Requiring
    # another six orders from that arbitrary initial guess can reject an
    # already accurate solution. Accept a fixed, small absolute tolerance
    # per equation as well, without changing the stored relative reference.
    absolute=TURBULENCE_ABSOLUTE_LOG10_MAX.copy()
    turbulence_checks={name:{'relative_passed':relative[name]<=-TURBULENCE_REDUCTION_ORDERS,
                            'absolute_passed':last[name]<=absolute[name],
                            'passed':relative[name]<=-TURBULENCE_REDUCTION_ORDERS or last[name]<=absolute[name]}
                       for name in absolute}
    stability={}
    for name in ('CFx','CFy'):
        values=column(name)[-FORCE_WINDOW:]
        stability[name]=float(np.ptp(values)/max(abs(values.mean()),FORCE_NORMALIZATION_FLOOR))
    checks={'flow_rms_below_1e_6':all(last[n]<=FLOW_LOG10_MAX for n in flow),
            'turbulence_residuals_converged':all(v['passed'] for v in turbulence_checks.values()),
            'forces_stable_0_1_percent_over_200_iterations':len(column('Inner_Iter'))>=FORCE_WINDOW and all(v<FORCE_RELATIVE_RANGE_MAX for v in stability.values())}
    return {'criteria':{'flow_rms_log10_max':FLOW_LOG10_MAX,'turbulence_residual_reduction_orders':TURBULENCE_REDUCTION_ORDERS,
                        'turbulence_absolute_rms_log10_max':absolute,
                        'turbulence_rule':'Each equation must satisfy relative reduction OR its absolute tolerance',
                        'force_window':FORCE_WINDOW,'force_relative_range_max':FORCE_RELATIVE_RANGE_MAX,
                        'force_normalization_floor':FORCE_NORMALIZATION_FLOOR},
            'turbulence_residual_reference_log10':reference,'turbulence_relative_residual_log10':relative,
            'turbulence_residual_checks':turbulence_checks,
            'force_relative_ranges':stability,'checks':checks,'passed':all(checks.values())}


def marked_triangles(mesh, tag):
    """Include hybrid-mesh wall quads in surface integrals and wall checks."""
    faces=[]
    for block,labels in zip(mesh.cells,mesh.cell_data['gmsh:physical']):
        selected=block.data[labels==tag]
        if block.type=='triangle':faces.append(selected)
        elif block.type=='quad':faces.extend((selected[:,[0,1,2]],selected[:,[0,2,3]]))
    return np.concatenate(faces)


def surface_resolution(out,surface_file=None):
    """Use actual mesh markers, not geometric guesses, to identify each sail."""
    import meshio
    from scipy.spatial import cKDTree
    surface=meshio.read(surface_file or out/'surface.vtk')
    mesh=meshio.read(out/'yacht.msh')
    distance,index=cKDTree(mesh.points).query(surface.points)
    if distance.max()>2e-5:
        raise RuntimeError('Surface output does not match the case mesh')
    yp=surface.point_data['Y_Plus'].reshape(-1)
    # ASCII SU2 VTK has limited coordinate precision; k-d tree membership
    # suffices because prism wall nodes have separate positions from the core.
    values=np.full(len(mesh.points),np.nan);values[index]=yp
    result={}
    walls=[('main',13),('jib',14),('hull',12)]
    if (out/'marine_case.json').exists():walls.append(('sea',11))
    for name,tag in walls:
        faces=marked_triangles(mesh,tag)
        xyz=mesh.points[faces]
        area=.5*np.linalg.norm(np.cross(xyz[:,1]-xyz[:,0],xyz[:,2]-xyz[:,0]),axis=1)
        y=values[faces].mean(axis=1)
        if not np.isfinite(y).all():
            raise RuntimeError(f'No wall output on every {name} face')
        order=np.argsort(y);cumulative=np.cumsum(area[order])/area.sum()
        quantile=lambda q:float(np.interp(q,cumulative,y[order]))
        result[name]={'area_m2':float(area.sum()),'mean_yplus':float(np.average(y,weights=area)),
                      'p50_yplus':quantile(.5),'p95_yplus':quantile(.95),
                      'max_yplus':float(values[np.unique(faces)].max()),'max_face_average_yplus':float(y.max()),
                      'area_fraction_yplus_above_5':float(np.average(y>5,weights=area))}
    result['sails_passed']=all(result[n]['p95_yplus']<2 and result[n]['max_yplus']<5 for n in ('main','jib'))
    result['walls_passed']=all(result[n]['p95_yplus']<2 and result[n]['max_yplus']<5 for n,_ in walls)
    return result


def surface_loading(out,freestream,reference_area=40.,density=1.225,surface_file=None,history_file=None):
    """Integrate each body's pressure and skin friction; check against SU2."""
    import meshio
    from scipy.spatial import cKDTree
    surface=meshio.read(surface_file or out/'surface.vtk')
    mesh=meshio.read(out/'yacht.msh')
    distance,index=cKDTree(mesh.points).query(surface.points)
    if distance.max()>2e-5:raise RuntimeError('Surface loading mesh mismatch')
    cp=np.full(len(mesh.points),np.nan)
    cf=np.full((len(mesh.points),3),np.nan)
    cp[index]=surface.point_data['Pressure_Coefficient'].reshape(-1)
    cf[index]=surface.point_data['Skin_Friction_Coefficient']
    wind=np.asarray(freestream);unit=wind[:2]/np.linalg.norm(wind[:2])
    lift_axis=np.array([unit[1],-unit[0]])
    q_area=.5*density*np.dot(wind,wind)*reference_area
    result={'reference_area_m2':float(reference_area),
            'dynamic_pressure_Pa':float(q_area/reference_area)};total=np.zeros(3)
    for name,tag in (('hull',12),('main',13),('jib',14)):
        faces=marked_triangles(mesh,tag)
        p=mesh.points[faces]
        area_vector=.5*np.cross(p[:,1]-p[:,0],p[:,2]-p[:,0])
        # The hull is open at z=0; its missing cap has zero signed-volume
        # contribution, so this orientation test also applies to it.
        orientation=np.sign(np.sum(p[:,0]*np.cross(p[:,1],p[:,2])))
        if orientation==0 or not np.isfinite(cp[faces]).all() or not np.isfinite(cf[faces]).all():
            raise RuntimeError(f'Incomplete surface loading on {name}')
        pressure=-orientation*np.sum(cp[faces].mean(axis=1)[:,None]*area_vector,axis=0)/reference_area
        friction=np.sum(cf[faces].mean(axis=1)*np.linalg.norm(area_vector,axis=1)[:,None],axis=0)/reference_area
        force=pressure+friction;total+=force
        drag=float(force[:2]@unit);lift=float(force[:2]@lift_axis)
        result[name]={'pressure_coefficient_vector':pressure.tolist(),
                      'friction_coefficient_vector':friction.tolist(),
                      'coefficient_vector':force.tolist(),
                      'horizontal_lift_coefficient':lift,'drag_coefficient':drag,
                      'horizontal_lift_to_drag':abs(lift)/drag if drag>0 else None,
                      'drive_N':float(force[0]*q_area),
                      'horizontal_lift_N':lift*q_area,'drag_N':drag*q_area}
    history=np.genfromtxt(history_file or out/'history.csv',delimiter=',',names=True,deletechars=' "')
    native=np.array([float(np.atleast_1d(history[n])[-1]) for n in ('CFx','CFy','CFz')])
    error=float(np.linalg.norm(total-native)/max(np.linalg.norm(native),1e-12))
    result['integration_check']={'summed_coefficient_vector':total.tolist(),
                                 'native_SU2_coefficient_vector':native.tolist(),
                                 'relative_vector_error':error,'tolerance':.02,'passed':error<.02}
    return result
