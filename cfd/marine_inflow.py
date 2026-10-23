"""Consistent neutral marine wind shear, turbulence and native initialization."""
import json
from pathlib import Path
import numpy as np
from cfd.wind_profile import WindProfile

def coare_neutral_roughness(wind_10m=8., viscosity=1.81e-5 / 1.225):
    """Neutral momentum subset of COARE 3.0; not the full COARE algorithm.

    Charnock + viscous term and a neutral log profile solved consistently.
    No assumed wave period, no conversion Hs=z0, no surface current.
    """
    from scipy.optimize import brentq
    if not 0 < wind_10m <= 30:
        raise ValueError("Supported wind range is 0 < U10 <= 30 m/s")
    alpha = .011 + .007 * np.clip((wind_10m - 10.) / 8., 0, 1)
    def z0(ustar):
        return alpha * ustar ** 2 / 9.81 + .11 * viscosity / ustar
    ustar = brentq(lambda u: u / .41 * np.log1p(10. / z0(u)) - wind_10m,
                  .0001, 2.)
    return {"roughness_length_m": float(z0(ustar)), "friction_velocity_m_s": float(ustar),
            "charnock_alpha": float(alpha), "kinematic_viscosity_m2_s": viscosity}


def marine_profile(wind_10m=8., true_angle=41., boat_speed_knots=6.):
    rough = coare_neutral_roughness(wind_10m)
    return WindProfile(reference_speed_m_s=wind_10m, roughness_length_m=rough["roughness_length_m"],
                       true_angle_deg=true_angle, boat_speed_knots=boat_speed_knots)


def initial_values(points, profile):
    """Dimensional [pressure, Ux, Uy, Uz, k, omega] for arbitrary volume nodes.

    Wall velocities/turbulence must subsequently be imposed for each marker;
    the water moves at -boat speed and all boat walls are fixed in this frame.
    """
    points = np.asarray(points)
    z = np.maximum(points[:, 2], 0.)
    wind, turbulence = profile.at(z), profile.turbulence_at(z)
    return np.column_stack((np.zeros(len(z)), wind["apparent_velocity_m_s"],
                            turbulence["k_m2_s2"], turbulence["omega_s_inv"]))


def inlet_values(points, profile, temperature=288.15):
    """Dimensional SU2 incompressible inlet columns after coordinates.

    Temperature, speed, unit direction, k, omega. SU2 8.5 BC_Inlet converts
    file k/omega to solver units. Uses the same profile as initialization.
    """
    values = initial_values(points, profile)
    velocity = values[:, 1:4]
    speed = np.linalg.norm(velocity, axis=1)
    if np.any(speed == 0):
        raise ValueError("Zero-speed inlet requires a separate boundary prescription")
    return np.column_stack((np.full(len(speed), temperature), speed,
                            velocity / speed[:, None], values[:, 4:6]))


def write_initial_field(mesh_path, output, profile, reference_speed=None):
    """Write all native nodes, without running or modifying any CFD case.

    Uses SU2 8.5 INC_NONDIM=INITIAL_VALUES, fixed 1 m length reference.
    The same function can be used again when the sea-layer mesh is ready.
    """
    import meshio
    import hashlib
    mesh_path, output = Path(mesh_path), Path(output)
    if output.suffix != ".dat" or output.exists():
        raise ValueError("Use a new .dat destination; existing fields are never overwritten")
    mesh = meshio.read(mesh_path)
    if mesh.points.shape[1] != 3:
        raise ValueError("Need a 3D mesh")
    values = initial_values(mesh.points, profile)
    scale = reference_speed or profile.at(profile.reference_height_m)["apparent_speed_m_s"]
    values[:, 1:4] /= scale; values[:, 4] /= scale ** 2; values[:, 5] /= scale
    tags = mesh.cell_data["gmsh:physical"]
    for label in (11, 12, 13, 14):
        groups = [c.data[tag == label] for c, tag in zip(mesh.cells, tags) if c.type in ("triangle", "quad")]
        if not groups:
            continue
        nodes = np.unique(np.concatenate([g.reshape(-1) for g in groups]))
        values[nodes, 1:4] = 0.
        if label == 11:
            values[nodes, 1] = -profile.boat_speed_knots * 1852 / 3600 / scale
        values[nodes, 4] = 0.  # resolved WILCOX1998 wall k condition
    fields = ["x", "y", "z", "Pressure", "Velocity_x", "Velocity_y", "Velocity_z", "Turb_Kin_Energy", "Omega"]
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as f:
        np.array([535532, len(fields), len(mesh.points), 0, 0], dtype=np.int32).tofile(f)
        for field in fields:
            f.write(field.encode("ascii").ljust(33, b"\0"))
        np.column_stack((mesh.points, values)).astype(np.float64).tofile(f)
    metadata = {"mesh": str(mesh_path.resolve()), "nodes": len(mesh.points), "profile": profile.metadata(),
        "purpose": "Initial guess on this exact mesh; not a CFD result or numerical equilibrium proof.",
        "wall_initialization": "Velocity fixed on boat, -boat speed on sea, k=0. Omega uses analytic start estimate; "
        "the solver must enforce its wall closure. Mesh wall resolution is recorded separately.",
        "nondimensional_reference_speed_m_s": scale,
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}
    output.with_suffix(".json").write_text(json.dumps(metadata, indent=2), encoding="utf8")
    return metadata
