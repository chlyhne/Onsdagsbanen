"""Check the same incompressible SST solver against the TMR NACA0012 case."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from types import SimpleNamespace
import urllib.request

import meshio
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from cfd.solver import configuration, solver_path
from cfd.rans_checks import convergence,archive_history

TMR="https://tmbwg.github.io/turbmodels/"
MESH_URL="https://raw.githubusercontent.com/su2code/Tutorials/master/incompressible_flow/Inc_Turbulent_NACA0012/n0012_897-257.su2"


def download(url,path):
    if not path.exists():
        urllib.request.urlretrieve(url,path)
    return {"url":url,"sha256":hashlib.sha256(path.read_bytes()).hexdigest()}


def prepare(out,alpha,iterations,mesh_url,multigrid=0,gradient="GREEN_GAUSS",intensity=None,viscosity_ratio=None,restart=False,cfl_max=30.,flow_limiter='NONE'):
    out.mkdir(parents=True,exist_ok=True)
    provenance={"mesh":download(mesh_url,out/"yacht.su2")}
    for name in ("clcd","cp","cf"):
        provenance[name]=download(TMR+f"NACA0012_validation/n0012{name}_cfl3d_sst.dat",out/f"reference_{name}.dat")
    settings=SimpleNamespace(wind_angle=alpha,wind_speed=52.157,boat_speed=0,iterations=iterations,restart=restart)
    configuration(settings,out)
    a=np.radians(alpha)
    overrides={"INC_VELOCITY_INIT":f"({52.157*np.cos(a)}, {52.157*np.sin(a)}, 0.0)",
               "INC_DENSITY_INIT":"2.13163","MU_CONSTANT":"1.853e-5",
               "REF_LENGTH":"1.0","REF_AREA":"1.0","SST_OPTIONS":"V1994m",
               "FREESTREAM_TURBULENCEINTENSITY":str(np.sqrt(2/3*9e-9/.15**2)),
               "FREESTREAM_TURB2LAMVISCRATIO":"0.009",
               "MARKER_HEATFLUX":"(airfoil, 0.0)","MARKER_EULER":None,
               "MARKER_MONITORING":"(airfoil)","MARKER_PLOTTING":"(airfoil)",
               "CONV_FIELD":"(RMS_PRESSURE, RMS_VELOCITY-X, RMS_VELOCITY-Y"+(", REL_RMS_TKE, REL_RMS_DISSIPATION" if not restart else "")+", FORCE_X, FORCE_Y)",
               "CONV_RESIDUAL_MINVAL":"-6","SLOPE_LIMITER_FLOW":flow_limiter,
               "SCREEN_OUTPUT":"(INNER_ITER, RMS_PRESSURE, RMS_VELOCITY-X, RMS_VELOCITY-Y, RMS_TKE, RMS_DISSIPATION)",
               "CFL_NUMBER":"0.2","CFL_ADAPT":"YES","CFL_ADAPT_PARAM":f"(0.5, 1.1, 0.2, {cfl_max}, 0.1)",
               "RESTART_SOL":"YES" if restart else "NO",
               "VOLUME_OUTPUT":"(COORDINATES, SOLUTION, PRIMITIVE, TIMESTEP)",
               "LINEAR_SOLVER_ERROR":"0.001","LINEAR_SOLVER_ITER":"40"}
    if multigrid:
        overrides.update(MGLEVEL=str(multigrid),MGCYCLE="V_CYCLE",
                         MG_PRE_SMOOTH="("+", ".join(str(min(i+1,3)) for i in range(multigrid+1))+")",
                         MG_POST_SMOOTH="("+", ".join("0" for _ in range(multigrid+1))+")",
                         MG_CORRECTION_SMOOTH="("+", ".join("0" for _ in range(multigrid+1))+")")
    overrides["NUM_METHOD_GRAD"]=gradient
    if intensity is not None:
        overrides["FREESTREAM_TURBULENCEINTENSITY"]=str(intensity)
    if viscosity_ratio is not None:
        overrides["FREESTREAM_TURB2LAMVISCRATIO"]=str(viscosity_ratio)
    path=out/"yacht.cfg"
    text=path.read_text()
    for key,value in overrides.items():
        replacement="" if value is None else f"{key}= {value}"
        pattern=rf"^{re.escape(key)}=.*$"
        if re.search(pattern,text,re.M):
            text=re.sub(pattern,replacement,text,flags=re.M)
        elif value is not None:
            text+=replacement+"\n"
    path.write_text(text,encoding="ascii")
    (out/"provenance.json").write_text(json.dumps(provenance,indent=2),encoding="utf8")


def analyse(out,alpha):
    h=np.genfromtxt(out/"history.csv",delimiter=",",names=True,deletechars=' "')
    last={key:float(np.atleast_1d(h[key])[-1]) for key in h.dtype.names}
    residuals={k:v for k,v in last.items() if k.startswith("rms")}
    a=np.radians(alpha)
    cf=np.array([last["CFx"],last["CFy"]])
    lift=float(cf@np.array([-np.sin(a),np.cos(a)]))
    drag=float(cf@np.array([np.cos(a),np.sin(a)]))
    reference=np.loadtxt([line for line in (out/"reference_clcd.dat").read_text().splitlines()
                          if line.strip() and not line.lstrip().startswith(('#','!','variables','zone'))])
    row=reference[np.argmin(abs(reference[:,0]-alpha))]
    if not np.isclose(row[0],alpha):
        raise ValueError("No matching reference angle")
    surface=meshio.read(out/"surface.vtk")
    yp=surface.point_data["Y_Plus"].reshape(-1)
    lift_error=abs(lift-row[1])/abs(row[1]) if abs(row[1])>.001 else abs(lift-row[1])
    drag_error=abs(drag-row[2])/row[2]
    numerical=convergence(out,h,dimensions=2)
    checks={"numerical_convergence":numerical['passed'],
            "lift_within_5_percent_or_0_001_at_zero":bool(lift_error<(.001 if abs(row[1])<=.001 else .05)),
            "drag_within_10_percent":bool(drag_error<.1),
            "wall_yplus_p95_below_2":bool(np.percentile(yp,95)<2),
            "wall_yplus_max_below_5":bool(yp.max()<5)}
    result={"case":"NACA0012, incompressible SST V1994m, Re=6e6", "alpha_deg":alpha,
            "solver_version":"SU2 8.5.0",
            "reference_source":TMR+"naca0012_val_sst.html",
            "reference_is":"CFL3D SSTm numerical reference at Mach 0.15; our solver is incompressible",
            "lift_coefficient":lift,"drag_coefficient":drag,
            "reference_lift_coefficient":float(row[1]),"reference_drag_coefficient":float(row[2]),
            "lift_relative_error_or_absolute_at_zero":float(lift_error),"drag_relative_error":float(drag_error),
            "wall_yplus":{"p50":float(np.median(yp)),"p95":float(np.percentile(yp,95)),"max":float(yp.max())},
            "residuals":residuals,"iterations":int(last["Inner_Iter"])+1,
            "iterations_scope":"Last solver invocation; restart iterations are not cumulative",
            "convergence":numerical,
            "checks":checks,"passed":all(checks.values())}
    result['configuration']={line.split('=',1)[0].strip():line.split('=',1)[1].strip()
                             for line in (out/'yacht.cfg').read_text().splitlines()
                             if '=' in line and not line.lstrip().startswith('%')}
    plot_validation(out,alpha,surface,h)
    (out/"validation.json").write_text(json.dumps(result,indent=2),encoding="utf8")
    print(json.dumps(result,indent=2))
    return result


def plot_validation(out,alpha,surface,history):
    """Expose the local loading and skin friction as well as integrated forces."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,3,figsize=(15,4.5),layout='constrained')
    for ax,name,field in ((axes[0],'cp','Pressure_Coefficient'),
                          (axes[1],'cf','Skin_Friction_Coefficient')):
        active=False;points=[]
        def draw_reference():
            if points:
                data=np.asarray(points)
                ax.plot(data[:,0],data[:,1],color='black',lw=1,alpha=.7)
        for line in (out/f'reference_{name}.dat').read_text().splitlines():
            if line.lower().startswith('zone'):
                draw_reference();points=[]
                match=re.search(r'alpha=\s*([-+0-9.]+)',line)
                active=bool(match and np.isclose(float(match.group(1)),alpha))
            elif active and line.strip() and not line.lstrip().startswith('#'):
                try:points.append([float(v) for v in line.split()])
                except ValueError:pass
        draw_reference()
        values=surface.point_data[field]
        if name!='cf':values=values.reshape(-1)
        for positive,color,label in ((True,'tab:blue','SU2 upper'),(False,'tab:orange','SU2 lower')):
            mask=surface.points[:,1]>=0 if positive else surface.points[:,1]<0
            order=np.argsort(surface.points[mask,0])
            coordinates=surface.points[mask,:2][order]
            plotted=values[mask][order]
            if name=='cf':
                tangent=np.gradient(coordinates,axis=0)
                tangent/=np.linalg.norm(tangent,axis=1)[:,None]
                plotted=np.sum(plotted[:,:2]*tangent,axis=1)
            ax.plot(coordinates[:,0],plotted,color=color,lw=1,label=label)
        ax.plot([],[],color='black',label='CFL3D SSTm reference')
        ax.set(xlabel='x/c',ylabel=name.upper(),xlim=(0,1));ax.grid(alpha=.2)
    axes[0].invert_yaxis();axes[0].legend(fontsize=8)
    for name in ('rms[P]','rms[U]','rms[V]','rms[k]','rms[w]'):
        axes[2].plot(np.atleast_1d(history['Inner_Iter']),np.atleast_1d(history[name]),label=name)
    axes[2].set(xlabel='Iteration (current run)',ylabel='log10 RMS residual');axes[2].grid(alpha=.2)
    axes[2].legend(fontsize=8)
    fig.suptitle(f'NACA0012, alpha={alpha:g} deg, Re=6e6: incompressible SU2 vs M=0.15 CFL3D')
    fig.savefig(out/'validation.png',dpi=180);plt.close(fig)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,default=ROOT/"cfd/output/validation/naca0012_resolved")
    parser.add_argument("--alpha",type=float,default=10.)
    parser.add_argument("--iterations",type=int,default=3000)
    parser.add_argument("--threads",type=int,default=4)
    parser.add_argument("--cfl-max",type=float,default=30.)
    parser.add_argument('--flow-limiter',choices=('VENKATAKRISHNAN','NONE'),default='NONE')
    parser.add_argument("--mesh-url",default=MESH_URL)
    parser.add_argument("--multigrid",type=int,default=0)
    parser.add_argument("--gradient",choices=("WEIGHTED_LEAST_SQUARES","GREEN_GAUSS"),default="GREEN_GAUSS")
    parser.add_argument("--turbulence-intensity",type=float)
    parser.add_argument("--viscosity-ratio",type=float)
    parser.add_argument("--analyse-only",action="store_true")
    parser.add_argument("--restart",action="store_true")
    args=parser.parse_args()
    out=args.output.resolve()
    if not args.analyse_only:
        if args.restart and (out/"history.csv").exists():
            h=np.genfromtxt(out/'history.csv',delimiter=',',names=True,deletechars=' "')
            convergence(out,h,dimensions=2)
            archive_history(out)
        prepare(out,args.alpha,args.iterations,args.mesh_url,args.multigrid,args.gradient,args.turbulence_intensity,args.viscosity_ratio,args.restart,args.cfl_max,args.flow_limiter)
        env=os.environ.copy();env["OMP_NUM_THREADS"]=str(args.threads);env["OMP_WAIT_POLICY"]="PASSIVE"
        with (out/"solver.log").open("w") as log:
            subprocess.run([str(solver_path()),"yacht.cfg","-t",str(args.threads)],cwd=out,env=env,
                           stdout=log,stderr=subprocess.STDOUT,check=True)
    analyse(out,args.alpha)


if __name__=="__main__":
    main()
