"""SU2 8.5 discovery/installation and numerical-reference configuration."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile
import urllib.request
import zipfile
import numpy as np
from cfd.case import apparent_wind

VERSION="8.5.0"
TOOLS=Path(__file__).resolve().parents[1]/".cfd-tools/SU2"

def solver_path(install=False):
    found = shutil.which("SU2_CFD") or shutil.which("SU2_CFD.exe")
    if found:
        return Path(found)
    matches = list(TOOLS.rglob("SU2_CFD.exe")) + list(TOOLS.rglob("SU2_CFD"))
    if matches:
        return matches[0]
    if not install:
        raise RuntimeError("SU2 missing: run python -m cfd.solver --install or add SU2_CFD to PATH")
    if sys.platform != "win32":
        raise RuntimeError("Automatic installation supports Windows; install SU2 on PATH on other systems")
    TOOLS.mkdir(parents=True, exist_ok=True)
    downloads = TOOLS.parent / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    archive = downloads / f"SU2-v{VERSION}-win64-omp.zip"
    url = f"https://github.com/su2code/SU2/releases/download/v{VERSION}/SU2-v{VERSION}-win64-omp.zip"
    print(f"Downloading {url}", flush=True)
    urllib.request.urlretrieve(url, archive)
    with tempfile.TemporaryDirectory(prefix="su2-install-", dir=downloads) as staging:
        with zipfile.ZipFile(archive) as z:
            z.extractall(staging)
        # The official Windows release contains another zip. Keep both
        # download archives and extraction files outside the installed tools.
        nested_archives = list(Path(staging).glob("win64*.zip"))
        if nested_archives:
            for nested in nested_archives:
                with zipfile.ZipFile(nested) as z:
                    z.extractall(TOOLS / "bin")
        else:
            shutil.copytree(staging, TOOLS / "bin", dirs_exist_ok=True)
    return solver_path()


def configuration(args, out):
    freestream=apparent_wind(args)
    u,v=freestream[:2]
    metadata=out/"mesh_metadata.json"
    layered=metadata.exists() and json.loads(metadata.read_text()).get("geometry_version",1)>=2
    heatflux="(hull, 0.0, main, 0.0, jib, 0.0)" if layered else "(boat, 0.0)"
    surfaces="(hull, main, jib)" if layered else "(boat)"
    turbulence_convergence="" if getattr(args,'restart',False) else ", REL_RMS_TKE, REL_RMS_DISSIPATION"
    intensity=getattr(args,'turbulence_intensity',.03)
    length=getattr(args,'turbulence_length',None)
    # In unstrained freestream SST has nu_t = k/omega. Specify an actual
    # inflow length instead of pairing 3% intensity with an arbitrary ratio
    # of 10, which corresponds to sub-millimetre eddies and rapid decay.
    k=1.5*(np.linalg.norm(freestream)*intensity)**2
    viscosity_ratio=10. if length is None else .09**.25*np.sqrt(k)*length/(1.81e-5/1.225)
    text=f"""% SU2 {VERSION}: steady 3D apparent-wind SST RANS, SI units
SOLVER= INC_RANS
KIND_TURB_MODEL= SST
SST_OPTIONS= V1994m
MATH_PROBLEM= DIRECT
RESTART_SOL= {"YES" if getattr(args,'restart',False) or getattr(args,'initial_solution',None) else "NO"}
SOLUTION_FILENAME= restart
RESTART_FILENAME= restart
INC_DENSITY_MODEL= CONSTANT
INC_NONDIM= INITIAL_VALUES
INC_DENSITY_INIT= 1.225
INC_VELOCITY_INIT= ({u}, {v}, 0.0)
INC_ENERGY_EQUATION= NO
VISCOSITY_MODEL= CONSTANT_VISCOSITY
MU_CONSTANT= 1.81E-5
FREESTREAM_TURBULENCEINTENSITY= {intensity}
FREESTREAM_TURB2LAMVISCRATIO= {viscosity_ratio}
MARKER_HEATFLUX= {heatflux}
MARKER_EULER= (sea)
MARKER_FAR= (farfield)
MARKER_MONITORING= {surfaces}
MARKER_PLOTTING= {surfaces}
REF_LENGTH= 10.0
REF_AREA= 40.0
NUM_METHOD_GRAD= {getattr(args,'gradient','GREEN_GAUSS')}
NUM_METHOD_GRAD_RECON= LEAST_SQUARES
CFL_NUMBER= {getattr(args,'initial_cfl',.2)}
CFL_ADAPT= {"NO" if getattr(args,'no_cfl_adapt',False) else "YES"}
CFL_ADAPT_PARAM= (0.5, 1.05, {getattr(args,'cfl_min',.2)}, {getattr(args,'cfl_max',30.)}, 0.1)
CFL_REDUCTION_TURB= {getattr(args,'turbulence_cfl_factor',1.)}
MAX_UPDATE_SST= {getattr(args,'max_update_sst',1.)}
LINEAR_SOLVER= FGMRES
LINEAR_SOLVER_PREC= ILU
LINEAR_SOLVER_ERROR= {getattr(args,'linear_error',.001)}
LINEAR_SOLVER_ITER= {getattr(args,'linear_iterations',40)}
CONV_NUM_METHOD_FLOW= FDS
MUSCL_FLOW= YES
SLOPE_LIMITER_FLOW= {getattr(args,'flow_limiter','NONE')}
TIME_DISCRE_FLOW= EULER_IMPLICIT
CONV_NUM_METHOD_TURB= BOUNDED_SCALAR
MUSCL_TURB= NO
TIME_DISCRE_TURB= EULER_IMPLICIT
ITER= {args.iterations}
CONV_FIELD= (RMS_PRESSURE, RMS_VELOCITY-X, RMS_VELOCITY-Y, RMS_VELOCITY-Z{turbulence_convergence}, FORCE_X, FORCE_Y)
CONV_RESIDUAL_MINVAL= -6
CONV_STARTITER= 20
CONV_CAUCHY_ELEMS= 200
CONV_CAUCHY_EPS= 2e-6
MESH_FILENAME= yacht.su2
MESH_FORMAT= SU2
TABULAR_FORMAT= CSV
CONV_FILENAME= history
VOLUME_FILENAME= flow
SURFACE_FILENAME= surface
OUTPUT_FILES= (RESTART, PARAVIEW_ASCII, SURFACE_PARAVIEW_ASCII)
OUTPUT_WRT_FREQ= ({getattr(args,'checkpoint_every',100)}, {args.iterations}, {getattr(args,'checkpoint_every',100)})
WRT_RESTART_COMPACT= {"NO" if getattr(args,'diagnostic_fields',False) else "YES"}
WRT_RESTART_OVERWRITE= NO
VOLUME_OUTPUT= (COORDINATES, SOLUTION, PRIMITIVE{", RESIDUAL, TIMESTEP" if getattr(args,'diagnostic_fields',False) else ""})
SCREEN_OUTPUT= (INNER_ITER, RMS_PRESSURE, RMS_VELOCITY-X, RMS_VELOCITY-Y, RMS_TKE, RMS_DISSIPATION)
SCREEN_WRT_FREQ_INNER= 25
HISTORY_OUTPUT= (ITER, RMS_RES, REL_RMS_RES, AERO_COEFF, LINSOL, CFL_NUMBER)
"""
    (out/"yacht.cfg").write_text(text,encoding="ascii")
    return freestream



def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install',action='store_true')
    args=parser.parse_args()
    print(solver_path(install=args.install))


if __name__=='__main__':
    main()
