"""Prepare a small, self-contained plotting snapshot after a CFD calculation.

The renderer needs only slice.npz and plot_context.json. No solver or
convergence checks are run when rendering that snapshot.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from types import SimpleNamespace

import numpy as np


def status_text(report,geometry):
    if report.get('preliminary'):
        iteration=report.get('snapshot_iteration')
        label=f'Foreløbigt checkpoint · iteration {iteration}' if iteration is not None else 'Foreløbigt checkpoint'
        return label+' · IKKE dokumenteret konvergeret'
    status='Kontrolkrav opfyldt' if report['checks_passed'] else 'IKKE dokumenteret konvergeret — foreløbig beregning'
    if not report.get('field_stability',{}).get('passed',False):
        status+=' · vindkortenes stabilitet er ikke dokumenteret'
    if geometry.get('geometry_version',1)<2:
        status+=' · tidligere model med utilstrækkeligt grænselagsnet'
    elif not report.get('wall_resolution',{}).get('walls_passed',False):
        status+=' · grænselagets y⁺-krav er ikke opfyldt'
    if report.get('marine_profile_preservation',{}).get('passed') is False:
        status+=' · baggrundsprofilens bevarelse er ikke dokumenteret'
    return status


def cache_existing_slice(case):
    """Package an already prepared slice and its saved analysis; no CFD reads."""
    from scipy.spatial import ConvexHull
    from cfd.case import BOAT_LENGTH,SAIL_FOOT_HEIGHT,case_sails,case_section

    case=Path(case)
    report=json.loads((case/'report.json').read_text(encoding='utf8'))
    geometry=json.loads((case/'geometry.json').read_text(encoding='utf8'))
    height=report['parameters']['height']
    hull=np.asarray(geometry['hull'])
    sections=[case_section(geometry,i,height).tolist()
              for i,sail in enumerate(case_sails(geometry))
              if 0<=(height-SAIL_FOOT_HEIGHT)/sail[2]<=1]
    context={'format_version':1,'slice_file':'slice.npz','height_m':height,
             'boat_length_m':BOAT_LENGTH,'wind':report['wind'],
             'freestream_turbulence_percent':report['turbulence']['freestream_percent'],
             'sail_alignment':report['sail_alignment'],
             'hull_outline':hull[ConvexHull(hull).vertices].tolist(),
             'sail_sections':sections,'status_text':status_text(report,geometry),
             'checks_passed':report['checks_passed'],
             'preliminary':report.get('preliminary',False),
             'snapshot_iteration':report.get('snapshot_iteration'),
             'checkpoint_file':report.get('checkpoint_file'),
             'analysis_note':'Saved post-simulation checks; rendering does not repeat them'}
    (case/'plot_context.json').write_text(json.dumps(context,indent=2),encoding='utf8')
    return context


def load_plot_data(case):
    """Read only the compact snapshot. No raw CFD files or validation work."""
    case=Path(case)
    try:
        context=json.loads((case/'plot_context.json').read_text(encoding='utf8'))
        with np.load(case/context['slice_file'],allow_pickle=False) as saved:
            arrays={key:saved[key] for key in saved.files}
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            'Plot data is missing. Prepare it once with '
            f'python -m cfd.plot_data --case "{case}" --from-slice '
            '(existing slice.npz and report.json required).') from exc
    return context,arrays


def prepare_plot_data(args,out,freestream,analyse=True,*,preliminary=False,checkpoint=None):
    import meshio
    from cfd.solver import VERSION
    from cfd.case import (KNOT_TO_M_S,ENTRY_CHORD_FRACTION,case_sails,
        case_chord_trim,case_entry_angle,wind_angle_from_velocity)
    from cfd.plot_wind import distance_from_boat
    from scipy.spatial import ConvexHull
    from cfd.compare_snapshots import verify_saved_fields
    if preliminary:
        analyse=False
    if checkpoint is not None and not preliminary:
        raise ValueError('An explicit checkpoint requires preliminary=True; full checks use the canonical solution')
    restart_file=Path(checkpoint) if checkpoint is not None else out/'restart.dat'
    match=re.fullmatch(r'restart_(\d+)\.dat',restart_file.name)
    snapshot_iteration=int(match[1]) if match else None
    previous=None if analyse or preliminary else json.loads((out/"report.json").read_text(encoding="utf8"))
    marine=None;profile=None
    if (out/'marine_case.json').exists():
        from cfd.marine_rans import read_profile
        marine=json.loads((out/'marine_case.json').read_text(encoding='utf8'))
        profile=read_profile(marine)
        # Native units are tied to the distant top ambient, not this slice.
        freestream=np.asarray(marine['native_reference_velocity_m_s'])
    unchecked={'passed':False,'performed':False,'reason':'Preliminary checkpoint preview; no full convergence checks requested'}
    field_stability=unchecked.copy() if preliminary else verify_saved_fields(out,args.height) if analyse else previous["field_stability"]

    # Keep micrometre prism geometry and field precision in the maps. SU2's
    # legacy ASCII VTK rounds coordinates to six digits after the decimal
    # in scientific notation; use the binary restart on the original mesh.
    if restart_file.exists() and (out/'yacht.msh').exists():
        from cfd.transfer_solution import read_restart
        fields,native=read_restart(restart_file)
        mesh=meshio.read(out/'yacht.msh')
        expected=['x','y','z','Pressure','Velocity_x','Velocity_y','Velocity_z','Turb_Kin_Energy','Omega']
        if fields[:9]!=expected or not np.allclose(native[:,:3],mesh.points,rtol=0,atol=1e-10):
            raise RuntimeError('Binary solution does not match the original mesh')
        mesh.point_data={'Velocity':native[:,4:7],'Turb_Kin_Energy':native[:,7]}
        plot_source='SU2 binary restart with original mesh coordinates'
    else:
        if checkpoint is not None:
            raise FileNotFoundError(f'Checkpoint or original mesh is missing: {restart_file}')
        paths=list(out.glob("flow*.vtu"))+list(out.glob("flow*.vtk"))
        if not paths:
            raise RuntimeError("SU2 produced no volume solution; inspect solver.log")
        mesh=meshio.read(max(paths,key=lambda p:p.stat().st_mtime))
        plot_source='SU2 volume export'
    print("Solution fields:",list(mesh.point_data),flush=True)
    key=next((k for k in mesh.point_data if k.lower()=="velocity"),None)
    if key is None:
        raise RuntimeError(f"No velocity vector in {list(mesh.point_data)}")
    # SU2 INC_NONDIM=INITIAL_VALUES exports velocity normalised by |U_inf|.
    apparent_speed=float(np.linalg.norm(freestream))
    apparent_angle=wind_angle_from_velocity(freestream)
    slice_wind=np.asarray(profile.at(args.height)['apparent_velocity_m_s']) if profile else freestream
    slice_speed=float(np.linalg.norm(slice_wind))
    slice_angle=wind_angle_from_velocity(slice_wind)
    vel=mesh.point_data[key]*apparent_speed
    tke_key=next((k for k in mesh.point_data if k.lower()=="turb_kin_energy"),None)
    if tke_key is None:
        raise RuntimeError("SST output has no turbulent kinetic energy field")
    # The k field uses U_ref squared under INC_NONDIM=INITIAL_VALUES.
    tke=mesh.point_data[tke_key].reshape(-1)*apparent_speed**2
    if not np.isfinite(tke).all() or tke.min()<-1e-12:
        raise RuntimeError("Invalid turbulent kinetic energy; refusing to plot")
    from cfd.rans_slice import horizontal_slice
    xy,values,triangles=horizontal_slice(mesh,np.column_stack((vel,tke)),args.height)
    uvw=values[:,:3];slice_tke=np.maximum(values[:,3],0.)
    baseline_velocity=np.broadcast_to(slice_wind,uvw.shape)
    baseline_speed=np.linalg.norm(baseline_velocity,axis=1)
    if np.any(baseline_speed<=0):raise ValueError('Zero-speed free-flow baseline at the plot height')
    turbulence=100*np.sqrt(2*slice_tke/3)/baseline_speed
    horizontal=np.linalg.norm(uvw[:,:2],axis=1)
    angle=np.degrees(np.arctan2(baseline_velocity[:,0]*uvw[:,1]-baseline_velocity[:,1]*uvw[:,0],
                               np.sum(uvw[:,:2]*baseline_velocity[:,:2],axis=1)))
    angle=np.ma.masked_where(horizontal<.1*slice_speed,angle)
    delta=np.linalg.norm(uvw,axis=1)-baseline_speed
    if not np.isfinite(uvw).all():
        raise RuntimeError("Non-finite solver velocities; refusing to plot")
    geom=json.loads((out/"geometry.json").read_text())
    sails=case_sails(geom)
    alignment={name:{"chord_trim_deg":float(case_chord_trim(geom,index,args.height)),
                     "reference_trim_at_4m_deg":float(sail[3]),
                     "entry_reference_chord_fraction":ENTRY_CHORD_FRACTION,
                     "entry_angle_deg":case_entry_angle(geom,index,args.height),
                     "awa_minus_entry_deg":slice_angle-case_entry_angle(geom,index,args.height)}
               for index,(name,sail) in enumerate(zip(("main","jib"),sails))}
    hull=np.asarray(geom["hull"])
    hull_outline=hull[ConvexHull(hull).vertices]
    # The boat's projected footprint includes the sails at this height.
    # Exclude its complete one-metre neighbourhood from all scale extrema.
    boat_distance=distance_from_boat(xy,hull_outline)
    outside_boat=boat_distance>0
    color_reference=boat_distance>=1.
    hist=np.genfromtxt(out/"history.csv",delimiter=",",names=True,deletechars=' "')
    hist=np.atleast_1d(hist)
    if snapshot_iteration is not None:
        hist=hist[hist['Inner_Iter']<=snapshot_iteration]
        if not len(hist) or hist['Inner_Iter'][-1]!=snapshot_iteration:
            raise ValueError('History has no row matching the selected checkpoint iteration')
    names=hist.dtype.names
    residual_names=[n for n in names if n.startswith("rms")]
    last={n:float(np.atleast_1d(hist[n])[-1]) for n in names}
    checks=None;wall_resolution=None
    if preliminary:
        checks=unchecked.copy()
        converged=False
        wall_resolution=unchecked.copy()
    elif not analyse:
        checks=previous.get("convergence")
        converged=previous["converged_residual_target"]
        wall_resolution=previous.get("wall_resolution")
    elif geom.get("geometry_version",1)>=2:
        from cfd.rans_checks import convergence,surface_resolution
        checks=convergence(out,hist)
        converged=checks['passed']
        wall_resolution=surface_resolution(out)
    else:
        converged=len(residual_names)==6 and all(last[n]<=-6 for n in residual_names)
    approved=bool(converged and wall_resolution and wall_resolution.get('walls_passed',False)
                  and field_stability['passed'])
    background=None
    if marine:
        if preliminary:
            background=unchecked.copy()
        elif analyse:
            from cfd.marine_checks import background_profile
            background=background_profile(mesh.points,native,profile,apparent_speed,
                centers=marine.get('background_profile_centers_xy_m'))
        else:
            background=previous.get('marine_profile_preservation',{'passed':False,'reason':'No saved post-simulation check'})
        approved=approved and background['passed']
    slice_edges=np.linalg.norm(xy[triangles]-xy[np.roll(triangles,1,axis=1)],axis=2)
    report={"solver":"SU2 "+VERSION,"model":"3D steady INC_RANS SST", "converged_residual_target":converged,
            "plot_data_source":plot_source,
            "checks_passed":approved,"field_stability":field_stability,
            "preliminary":preliminary,"snapshot_iteration":snapshot_iteration,
            "checkpoint_file":str(restart_file.resolve()) if restart_file.exists() else None,
            "convergence":checks,"wall_resolution":wall_resolution,
            "sail_alignment":alignment,
            "entry_angle_reference":{"chord_fraction":ENTRY_CHORD_FRACTION,
                                     "method":"camber-line secant from leading edge to 5% of projected chord",
                                     "geometry_version":geom.get("geometry_version",1)},
            "color_scales":{},
            "wind":{"true_speed_m_s":profile.at(args.height)['true_speed_m_s'] if profile else args.wind_speed,"true_angle_deg":args.wind_angle,
                    "boat_speed_knots":args.boat_speed,"boat_speed_m_s":args.boat_speed*KNOT_TO_M_S,
                    "apparent_velocity_m_s":slice_wind.tolist(),"apparent_speed_m_s":slice_speed,
                    "apparent_angle_deg":slice_angle},
            "slice_resolution":{"triangles":len(triangles),
                                "median_max_edge_m":float(np.median(slice_edges.max(axis=1)))},
            "turbulence":{"quantity":"100 * sqrt(2*k/3) / |U_infinity|",
                          "units":"percent of undisturbed apparent wind speed",
                          "interpretation":"modelled RMS fluctuation under isotropy assumption; not resolved gusts",
                          "freestream_percent":100*profile.turbulence_at(args.height)['intensity_relative_to_apparent_speed'] if profile else 100*getattr(args,"turbulence_intensity",.03),
                          "inflow_length_scale_m":getattr(args,'turbulence_length',None),
                          "tke_range_m2_s2":[float(slice_tke.min()),float(slice_tke.max())]},
            "last_history":last,"parameters":vars(args),"velocity_range_m_s":[float(np.linalg.norm(uvw,axis=1).min()),float(np.linalg.norm(uvw,axis=1).max())],
            "limitations":["Idealised original geometry, not a measured yacht or validated sail shape",
                            "No mesh-independence study; actual sail y+ is reported separately",
                            "Discretization and turbulence settings are recorded in numerics",
                            "Steady one-tack solution; no tacking manoeuvre, heel, waves or sail deformation",
                            "Uniform inflow; slip sea; aerodynamic air domain only",
                            "Modelled turbulent kinetic energy, not time-resolved gusts",
                            "Horizontal slice, not depth-averaged field"]}
    if marine:
        report['marine_profile_preservation']=background
        report['wind'].update(profile=marine['profile'],true_speed_reference_m_s=args.wind_speed,
                              true_speed_reference_height_m=10.,native_reference_speed_m_s=apparent_speed)
        report['limitations'][4]='Prescribed neutral marine shear; resolved effective rough flat sea moving relative to boat; air domain only'
        report['limitations'].extend(['Equivalent sandgrain roughness is an engineering estimate, not calibrated to this sea state',
                                      'Distant top uses ambient farfield; SST diffusive omega flux is approximate; inspect post-simulation profile preservation'])
        report['force_reference']={'height_m':marine['coefficient_reference_height_m'],
                                   'velocity_m_s':freestream.tolist(),'speed_m_s':apparent_speed}
    settings={line.split('=',1)[0].strip():line.split('=',1)[1].split('%',1)[0].strip()
              for line in (out/'yacht.cfg').read_text().splitlines()
              if '=' in line and not line.lstrip().startswith('%')}
    report['numerics']={key:settings.get(key) for key in (
        'SST_OPTIONS','NUM_METHOD_GRAD','NUM_METHOD_GRAD_RECON','MUSCL_FLOW','SLOPE_LIMITER_FLOW',
        'CONV_NUM_METHOD_TURB','MUSCL_TURB','MARKER_WALL_FUNCTIONS','INC_NONDIM',
        'FREESTREAM_TURBULENCEINTENSITY','FREESTREAM_TURB2LAMVISCRATIO')}
    report['numerical_controls']={key:settings.get(key) for key in (
        'CFL_NUMBER','CFL_ADAPT','CFL_ADAPT_PARAM','CFL_REDUCTION_TURB','MAX_UPDATE_SST','LINEAR_SOLVER_ERROR','LINEAR_SOLVER_ITER')}
    wind_axis=freestream[:2]/np.linalg.norm(freestream[:2])
    lift_axis=np.array([wind_axis[1],-wind_axis[0]])
    force=np.array([last['CFx'],last['CFy']])
    drag=float(force@wind_axis);lift=float(force@lift_axis)
    q_area=.5*float(settings['INC_DENSITY_INIT'])*apparent_speed**2*float(settings['REF_AREA'])
    report['whole_model_forces']={
        'scope':'All monitored hull, mast and sail surfaces; not isolated sail forces',
        'horizontal_lift_coefficient':lift,'drag_coefficient':drag,
        'horizontal_lift_to_drag':abs(lift)/drag if drag>0 else None,
        'horizontal_lift_N':lift*q_area,'drag_N':drag*q_area,
        'drive_N':float(force[0]*q_area),'sideforce_to_port_N':float(force[1]*q_area),
        'projection':'CFx, CFy projected parallel and perpendicular to horizontal apparent wind; raw SU2 CL is vertical in this coordinate system'}
    if preliminary:
        report['limitations'].append('Unconverged checkpoint preview; full post-simulation checks were not performed')
    elif not analyse:
        if previous.get('surface_loading'):
            report['surface_loading']=previous['surface_loading']
    elif geom.get('geometry_version',1)>=2:
        from cfd.rans_checks import surface_loading
        report['surface_loading']=surface_loading(out,freestream,
            reference_area=float(settings['REF_AREA']),density=float(settings['INC_DENSITY_INIT']))
        if not report['surface_loading']['integration_check']['passed']:
            raise RuntimeError('Integrated surface forces disagree with the native SU2 force history')
    if geom.get('geometry_version',1)<2:
        report['limitations'].append('Legacy tetrahedral sail boundary layer is underresolved')
    if geom.get('geometry_version',1)>=2 and (out/'airfoil_validation.json').exists():
        report['airfoil_validation']=json.loads((out/'airfoil_validation.json').read_text())
    report["parameters"]={key:str(value) if isinstance(value,Path) else value for key,value in vars(args).items()}
    (out/"report.json").write_text(json.dumps(report,indent=2),encoding="utf8")
    np.savez_compressed(out/"slice.npz",xy=xy,velocity=uvw,triangles=triangles,
                        angle_deg=angle.filled(np.nan),speed_delta=delta,tke=slice_tke,turbulence_percent=turbulence,
                        outside_boat=outside_boat,distance_from_boat_m=boat_distance,
                        color_scale_reference_mask=color_reference)
    cache_existing_slice(out)
    print(f"Prepared plot data: {out}",flush=True)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case',type=Path,default=Path('cfd/output'))
    parser.add_argument('--from-slice',action='store_true',
                        help='Package an existing slice and saved analysis without rereading CFD')
    parser.add_argument('--height',type=float,help='Height for a new slice of the stored 3D solution')
    parser.add_argument('--preliminary',action='store_true',
                        help='Extract a clearly labelled checkpoint preview without full post-simulation checks')
    parser.add_argument('--checkpoint',type=Path,help='Explicit checkpoint path; requires --preliminary')
    args=parser.parse_args()
    if args.checkpoint is not None and not args.preliminary:
        parser.error('--checkpoint requires --preliminary')
    if args.from_slice:
        if args.preliminary or args.checkpoint is not None:parser.error('--from-slice only reuses the saved snapshot')
        if args.height is not None:parser.error('--height requires preparing a new slice')
        cache_existing_slice(args.case)
    else:
        from cfd.case import apparent_wind
        parameters=json.loads((args.case/'run_parameters.json').read_text(encoding='utf8'))
        settings=SimpleNamespace(**parameters)
        if args.height is not None:settings.height=args.height
        # Preparing another view reuses the saved post-simulation analysis.
        prepare_plot_data(settings,args.case,apparent_wind(settings),analyse=False,
                          preliminary=args.preliminary,checkpoint=args.checkpoint)
    print(f'Plot data ready: {args.case / "slice.npz"} + plot_context.json',flush=True)


if __name__=='__main__':
    main()
