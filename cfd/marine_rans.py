"""Prepare or explicitly run the complete marine 3D case.

Preparation never invokes SU2. Run is a separate, explicit command.
python -m cfd.marine_rans prepare
python -m cfd.marine_rans run --case cfd/output/marine_3d --threads 8
"""
import argparse
import csv
from dataclasses import fields
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace

import numpy as np

from cfd.marine_inflow import marine_profile, inlet_values, write_initial_field
from cfd.marine_mesh import MARKERS
from cfd.solver_progress import run_solver
from cfd.wind_profile import WindProfile

CONVERGENCE_SCREEN_OUTPUT = "(INNER_ITER, RMS_PRESSURE, RMS_VELOCITY-X, RMS_VELOCITY-Y, " \
    "RMS_VELOCITY-Z, RMS_TKE, RMS_DISSIPATION, FORCE_X, FORCE_Y)"


def read_profile(manifest):
    return WindProfile(**{f.name: manifest["profile"][f.name] for f in fields(WindProfile)})


def sha(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def overrides(text, values):
    result = []; seen = set()
    for line in text.splitlines():
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("%") else None
        if key in values:
            seen.add(key)
            if values[key] is not None:
                result.append(f"{key}= {values[key]}")
        else:
            result.append(line)
    result.extend(f"{k}= {v}" for k, v in values.items() if k not in seen and v is not None)
    return "\n".join(result) + "\n"


def configuration(template, profile, top_height, iterations=3000):
    top = profile.at(top_height); t = profile.turbulence_at(top_height)
    velocity = np.asarray(top["apparent_velocity_m_s"])
    speed = top["apparent_speed_m_s"]
    direction = velocity / speed
    inlet = ", ".join(f"{name}, 288.15, {speed:.16g}, {direction[0]:.16g}, {direction[1]:.16g}, 0.0"
                      for name in ("inlet_xmax", "inlet_ymin"))
    values = {"RESTART_SOL": "YES", "DISCARD_INFILES": "YES", "SOLUTION_FILENAME": "initial_atmosphere", "RESTART_FILENAME": "restart",
        "INC_NONDIM": "INITIAL_VALUES", "INC_VELOCITY_INIT": f"({velocity[0]:.16g}, {velocity[1]:.16g}, 0.0)",
        "FREESTREAM_TURBULENCEINTENSITY": f"{np.sqrt(2 * t['k_m2_s2'] / 3) / speed:.16g}",
        "FREESTREAM_TURB2LAMVISCRATIO": f"{t['k_m2_s2'] / t['omega_s_inv'] / (1.81e-5 / 1.225):.16g}",
        "MARKER_HEATFLUX": "(hull, 0.0, main, 0.0, jib, 0.0, sea, 0.0)",
        "MARKER_EULER": None, "MARKER_FAR": "(atmosphere_top)",
        "MARKER_INLET": f"({inlet})", "INC_INLET_TYPE": "(VELOCITY_INLET, VELOCITY_INLET)",
        "MARKER_OUTLET": "(outlet_xmin, 0.0, outlet_ymax, 0.0)",
        "INC_OUTLET_TYPE": "(PRESSURE_OUTLET, PRESSURE_OUTLET)",
        "SPECIFIED_INLET_PROFILE": "YES", "INLET_FILENAME": "inlet_atmosphere.dat",
        "INLET_USE_NORMAL": "NO",
        "WALL_ROUGHNESS": f"(sea, {30 * profile.roughness_length_m:.16g})",
        "KIND_ROUGHSST_MODEL": "WILCOX1998", "MARKER_WALL_FUNCTIONS": None,
        "MARKER_MOVING": "(sea)", "SURFACE_MOVEMENT": "(MOVING_WALL)",
        "SURFACE_TRANSLATION_RATE": f"({-profile.boat_speed_knots * 1852 / 3600:.16g}, 0.0, 0.0)",
        "SURFACE_ROTATION_RATE": "(0.0, 0.0, 0.0)", "SURFACE_MOTION_ORIGIN": "(0.0, 0.0, 0.0)",
        "GRID_MOVEMENT": "NONE", "TRANSLATION_RATE": "(0.0, 0.0, 0.0)",
        "MARKER_MONITORING": "(hull, main, jib)", "MARKER_PLOTTING": "(hull, main, jib, sea)",
        "CFL_NUMBER": "0.2", "CFL_ADAPT": "YES", "CFL_ADAPT_PARAM": "(0.5, 1.05, 0.2, 1000.0, 0.1)",
        "ITER": str(iterations), "LINEAR_SOLVER_ITER": "80", "LINEAR_SOLVER_ERROR": "0.01",
        "OUTPUT_WRT_FREQ": f"(100, {iterations}, 100)", "WRT_RESTART_COMPACT": "NO",
        "SCREEN_OUTPUT": CONVERGENCE_SCREEN_OUTPUT, "SCREEN_WRT_FREQ_INNER": "1",
        "CONV_FIELD": "(RMS_PRESSURE, RMS_VELOCITY-X, RMS_VELOCITY-Y, RMS_VELOCITY-Z, FORCE_X, FORCE_Y)"}
    return overrides(template, values)


def write_inlets(mesh, output, profile):
    groups = {}
    for name in ("inlet_xmax", "inlet_ymin"):
        groups[name] = np.unique(np.concatenate([b.data[t == MARKERS[name]].reshape(-1)
            for b, t in zip(mesh.cells, mesh.cell_data["tag"]) if b.type in ("triangle", "quad")]))
    with Path(output).open("w", encoding="ascii") as f:
        f.write("NMARK= 2\n")
        for name, nodes in groups.items():
            points = mesh.points[nodes]
            data = inlet_values(points, profile)
            data[np.isclose(points[:, 2], 0., atol=1e-12), 5] = 0.
            f.write(f"MARKER_TAG= {name}\nNROW= {len(nodes)}\nNCOL= 10\n")
            f.write("# COORD-X COORD-Y COORD-Z TEMPERATURE VELOCITY NORMAL-X NORMAL-Y NORMAL-Z TKE OMEGA\n")
            np.savetxt(f, np.column_stack((points, data)), fmt="%.16e")
    return {name: len(nodes) for name, nodes in groups.items()}


def prepare(source, case, wind=8., angle=41., boat_speed=6., iterations=3000, top=84., roughness_length=None):
    import meshio
    from cfd.marine_mesh import augment, check, write_su2
    source, case = Path(source).resolve(), Path(case).resolve()
    if source == case or any((case / name).exists() for name in ("marine_case.json", "history.csv", "restart.dat")):
        raise ValueError("Use a new case directory; completed/prepared cases are preserved")
    case.mkdir(parents=True, exist_ok=True)
    profile = marine_profile(wind, angle, boat_speed)
    if roughness_length is not None:
        from dataclasses import replace
        profile = replace(profile, roughness_length_m=roughness_length)
    print("Preparing resolved sea and elevated atmosphere; no solver", flush=True)
    original = meshio.read(source / "yacht.msh")
    mesh, metadata = augment(original, top_height=top)
    del original
    gm = meshio.Mesh(mesh.points, mesh.cells,
        cell_data={"gmsh:physical": mesh.cell_data["tag"], "gmsh:geometrical": mesh.cell_data["tag"]},
        field_data=mesh.field_data)
    meshio.write(case / "yacht.msh", gm, file_format="gmsh22", binary=True)
    print("Checking all hybrid cell Jacobians and closed boundaries", flush=True)
    metadata["checks"] = check(mesh, case)
    print("Writing complete SU2 mesh and nodal inlet data", flush=True)
    write_su2(case / "yacht.su2", mesh)
    inlet_counts = write_inlets(mesh, case / "inlet_atmosphere.dat", profile)
    geometry = json.loads((source / "geometry.json").read_text(encoding="utf8"))
    (case / "geometry.json").write_text(json.dumps(geometry, indent=2), encoding="utf8")
    top_velocity = profile.at(top)["apparent_velocity_m_s"]
    scale = float(np.linalg.norm(top_velocity))
    (case / "yacht.cfg").write_text(configuration((source / "yacht.cfg").read_text(), profile, top, iterations), encoding="ascii")
    mesh_info = json.loads((source / "mesh_metadata.json").read_text())
    mesh_info.update(marine_mesh=metadata, domain_min=mesh.points.min(axis=0).tolist(),
                     domain_max=mesh.points.max(axis=0).tolist(), nodes=len(mesh.points),elements=metadata['elements'])
    mesh_info['checks'].update(minimum_element_jacobian=metadata['checks']['minimum_element_jacobian'],
                              boundary_nonmanifold_edges=0)
    mesh_info["wall_treatment"]["sea"] = "resolved rough SST WILCOX1998; first spacing 12 micrometres"
    (case / "mesh_metadata.json").write_text(json.dumps(mesh_info, indent=2), encoding="utf8")
    del gm, mesh
    print("Writing the height-dependent native initial field", flush=True)
    initial = write_initial_field(case / "yacht.msh", case / "initial_atmosphere.dat", profile, reference_speed=scale)
    validation = source / "airfoil_validation.json"
    if not validation.exists():
        validation = source / "validation/naca0012_resolved/validation.json"
    if not validation.exists() or not json.loads(validation.read_text()).get("passed"):
        raise ValueError("Existing SST numerical reference is missing or failed; no solver will run")
    validated=json.loads(validation.read_text()).get('configuration',{})
    prepared_cfg={line.split('=',1)[0].strip():line.split('=',1)[1].strip()
                  for line in (case/'yacht.cfg').read_text().splitlines() if '=' in line and not line.lstrip().startswith('%')}
    for key in ('SST_OPTIONS','NUM_METHOD_GRAD','NUM_METHOD_GRAD_RECON','MUSCL_FLOW','SLOPE_LIMITER_FLOW','CONV_NUM_METHOD_TURB','MUSCL_TURB'):
        if validated.get(key)!=prepared_cfg.get(key):
            raise ValueError(f'Prepared spatial scheme differs from existing numerical reference: {key}')
    shutil.copy2(validation, case / "airfoil_validation.json")
    manifest = {"status": "prepared_not_simulated", "solver_started": False, "source_case": str(source),
        "profile": profile.metadata(), "significant_wave_height_context_m": .4, "wave_geometry": False,
        "mesh": metadata, "inlet_nodes": inlet_counts, "initial_field": initial,
        "native_reference_velocity_m_s": top_velocity, "native_reference_speed_m_s": scale,
        "coefficient_reference_height_m": top,
        "coefficient_reference_note": "SU2 INITIAL_VALUES uses top ambient wind for nondimensionalization and force coefficients. "
            "Dimensional forces use that same reference; each wind plot uses its own height's undisturbed profile.",
        "roughness_length_m": profile.roughness_length_m,
        "equivalent_sandgrain_roughness_m": 30 * profile.roughness_length_m,
        "roughness_mapping": "ks=30*z0 is an engineering estimate, not an empirical calibration of SU2 rough SST",
            "top_boundary": f"Farfield ambient equals the prescribed profile at the distant {top:g} m top. Momentum viscous "
            "flux uses computed gradients. Native SST farfield omits explicit diffusive omega flux; "
            "profile preservation is measured in the sail-height region after the run, not asserted a priori.",
        "prepared_checks": {"mesh": metadata["checks"], "initial_and_inlet_profile": "one common dimensional profile function"},
        "post_simulation_checks": "Residuals, boat forces, all wall y+, snapshot stability, free-field U/k/omega profile preservation",
        "solver_execution_tested": False}
    manifest["mesh_sha256"] = sha(case / "yacht.su2")
    (case / "marine_case.json").write_text(json.dumps(manifest, indent=2), encoding="utf8")
    print(f"Prepared {case}; solver has not been started", flush=True)
    return manifest


def settings(case, manifest, iterations, threads):
    geometry = json.loads((case / "geometry.json").read_text(encoding="utf8"))
    p = read_profile(manifest)
    return SimpleNamespace(output=str(case), wind_speed=p.reference_speed_m_s, wind_angle=p.true_angle_deg,
        boat_speed=p.boat_speed_knots, height=4., iterations=iterations, threads=threads,
        turbulence_intensity=float(p.turbulence_at(10.)["intensity_relative_to_apparent_speed"]), turbulence_length=None,
        main_trim=geometry["sail_definitions"][0][3], jib_trim=geometry["sail_definitions"][1][3],
        main_twist=geometry["sail_shapes"][0]["twist_deg"], jib_twist=geometry["sail_shapes"][1]["twist_deg"],
        marine_profile=manifest["profile"], gradient="GREEN_GAUSS", flow_limiter="NONE",
        apparent_speed=manifest["native_reference_speed_m_s"],
        apparent_angle=float(np.degrees(np.arctan2(manifest["native_reference_velocity_m_s"][1], -manifest["native_reference_velocity_m_s"][0]))),
        mesh_sha256=manifest["mesh_sha256"])


def history_has_rows(case):
    """A history header is written during SU2 startup, before any iteration."""
    history = case / "history.csv"
    if not history.exists():
        return False
    with history.open(encoding="utf-8-sig", newline="") as source:
        rows = csv.reader(source)
        next(rows, None)
        return any(any(value.strip() for value in row) for row in rows)


def archive_failed_start(case):
    """Preserve startup diagnostics before a retry can overwrite them."""
    names = ("history.csv", "solver.log", "run_parameters.json", "marine_case.json", "yacht.cfg")
    if not any((case / name).exists() for name in names[:3]):
        return None
    folder = case / "failed_starts" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    folder.mkdir(parents=True)
    for name in names:
        if (case / name).exists():
            shutil.copy2(case / name, folder / name)
    print(f"Archived previous startup attempt: {folder}", flush=True)
    return str(folder)


def run(case, threads=8, iterations=3000, restart=False, fresh=False):
    """Only this explicit entry point invokes the expensive solver."""
    from cfd.solver import solver_path
    case = Path(case).resolve()
    manifest = json.loads((case / "marine_case.json").read_text(encoding="utf8"))
    config = case / "yacht.cfg"
    if fresh and restart:
        raise ValueError('--fresh and --restart are mutually exclusive')
    if manifest["mesh_sha256"] != sha(case / "yacht.su2"):
        raise ValueError("Prepared mesh changed; prepare a new case")
    initial_name='initial_atmosphere'
    if fresh:
        names=('history.csv','solver.log','run_parameters.json','residual_reference.json',
               'history_before_restart.csv','field_stability.json','report.json','slice.npz',
               'plot_context.json','plot_scales.json','surface.vtk','flow.vtk',
               'vindhastighed.png','vinddrejning.png','turbulens.png','godhed.png')
        paths={case/name for name in names if (case/name).exists()}
        paths.update(case.glob('restart*.dat'))
        if paths:
            archived=case/'previous_runs'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            archived.mkdir(parents=True)
            shutil.copy2(case/'marine_case.json',archived/'marine_case.json')
            shutil.copy2(config,archived/'yacht.cfg')
            for path in paths:shutil.move(str(path),archived/path.name)
            print(f'Previous yacht results archived: {archived}',flush=True)
    has_rows = history_has_rows(case)
    has_checkpoint = (case / "restart.dat").exists()
    snapshots = list(case.glob("restart_*.dat"))
    if (has_rows or snapshots) and not has_checkpoint:
        raise ValueError("Iteration results exist but restart.dat is missing. Preserve this case; "
                         "restore a saved restart_*.dat checkpoint as restart.dat, or prepare a new case.")
    if has_checkpoint and not restart:
        raise ValueError("Saved solver state exists; explicitly use --restart to continue it")
    if restart and not has_checkpoint:
        print("No completed iterations or checkpoint; starting from the prepared initial field.", flush=True)
        restart = False
    previous_start = archive_failed_start(case) if not restart else None
    cfg = overrides(config.read_text(), {"SOLUTION_FILENAME": "restart" if restart else initial_name,
        "ITER": str(iterations), "OUTPUT_WRT_FREQ": f"(100, {iterations}, 100)",
        "SCREEN_WRT_FREQ_INNER": "1", "SCREEN_OUTPUT": CONVERGENCE_SCREEN_OUTPUT})
    config.write_text(cfg, encoding="ascii")
    args = settings(case, manifest, iterations, threads)
    parameters = vars(args).copy()
    parameters.update(started_at_utc=datetime.now(timezone.utc).isoformat(), status="running",
                      marine_case=str(case / "marine_case.json"), checkpoint_every=100,
                      initial_solution='restart.dat' if restart else initial_name+'.dat')
    if previous_start:
        parameters["previous_startup_attempt"] = previous_start
    if restart and has_rows:
        from cfd.rans_checks import archive_history, convergence
        history = np.genfromtxt(case / "history.csv", delimiter=",", names=True, deletechars=' "')
        convergence(case, history)
        parameters["previous_history"] = archive_history(case)
    (case / "run_parameters.json").write_text(json.dumps(parameters, indent=2), encoding="utf8")
    manifest.update(status="running", solver_started=True)
    (case / "marine_case.json").write_text(json.dumps(manifest, indent=2), encoding="utf8")
    environment = os.environ.copy(); environment["OMP_NUM_THREADS"] = str(threads)
    environment.setdefault("OMP_WAIT_POLICY", "PASSIVE")
    print(f"Starting marine 3D RANS in {case}; full log: {case / 'solver.log'}",flush=True)
    try:
        run_solver([str(solver_path()), "yacht.cfg", "-t", str(threads)], cwd=case,
                   env=environment, iterations=iterations)
    except BaseException as error:
        parameters["status"] = "failed"
        parameters["solver_error"] = f"{type(error).__name__}: {error}"
        if hasattr(error, "returncode"):
            parameters["solver_returncode"] = error.returncode
        (case / "run_parameters.json").write_text(json.dumps(parameters, indent=2), encoding="utf8")
        manifest['status']='failed'
        (case / 'marine_case.json').write_text(json.dumps(manifest,indent=2),encoding='utf8')
        raise
    parameters.update(status="solver_finished", finished_at_utc=datetime.now(timezone.utc).isoformat())
    (case / "run_parameters.json").write_text(json.dumps(parameters, indent=2), encoding="utf8")
    manifest.update(status='solver_finished',solver_execution_tested=True)
    (case / "marine_case.json").write_text(json.dumps(manifest, indent=2), encoding="utf8")
    print("Efterbehandling: kontrollerer resultatet og laver plots ...", flush=True)
    analyse(case)
    print("Færdig: simulering, kontrol og plots er afsluttet.", flush=True)


def analyse(case):
    """Postprocess saved solver results separately, without launching SU2."""
    from cfd.plot_data import prepare_plot_data
    from cfd.plot_wind import render
    case=Path(case).resolve()
    manifest=json.loads((case/'marine_case.json').read_text(encoding='utf8'))
    parameters=json.loads((case/'run_parameters.json').read_text(encoding='utf8'))
    args=SimpleNamespace(**parameters)
    report=prepare_plot_data(args,case,np.asarray(manifest['native_reference_velocity_m_s']))
    render(case)
    parameters.update(checks_passed=report['checks_passed'],status='converged' if report['checks_passed'] else 'checks_failed')
    manifest['status']=parameters['status']
    (case/'run_parameters.json').write_text(json.dumps(parameters,indent=2),encoding='utf8')
    (case/'marine_case.json').write_text(json.dumps(manifest,indent=2),encoding='utf8')
    if not report["checks_passed"]:
        raise SystemExit("Solver finished; post-simulation checks failed. Inspect report.json and free-field profile checks.")
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("prepare", "run", "analyse", "plot"))
    p.add_argument("--case", type=Path, default=Path("cfd/output/marine_3d_padded_5ms"))
    p.add_argument("--source", type=Path, default=Path("cfd/output"))
    p.add_argument("--threads", type=int, default=8)
    p.add_argument("--iterations", type=int, default=3000)
    p.add_argument("--restart", action="store_true")
    p.add_argument('--fresh',action='store_true',help='Archive existing yacht results and start from the prepared initial field')
    args = p.parse_args()
    if args.threads < 1 or args.iterations < 200:
        p.error("Require positive threads and at least 200 iterations")
    if args.action == "prepare":
        prepare(args.source, args.case, iterations=args.iterations)
    elif args.action == "run":
        run(args.case, args.threads, args.iterations, args.restart, args.fresh)
    elif args.action == 'analyse':
        analyse(args.case)
    else:
        from cfd.plot_wind import render
        render(args.case)


if __name__ == "__main__":
    main()
