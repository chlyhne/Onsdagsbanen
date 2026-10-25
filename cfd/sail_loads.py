"""Reproduce sail drive and wall-shear checks from a saved 3D SU2 result.

Pressure and skin-friction coefficients are integrated on the original mesh,
then checked against the saved solver force history. No CFD is launched.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import meshio
import numpy as np

from cfd.rans_checks import marked_triangles
from cfd.rans_mesh import FOOT
from cfd.separation_3d import sail_coordinates, sha
from cfd.storage import case_file, result_dir
from cfd.transfer_solution import read_restart


def surface_geometry(points, faces):
    xyz = points[faces]
    vector = .5 * np.cross(xyz[:, 1] - xyz[:, 0], xyz[:, 2] - xyz[:, 0])
    area = np.linalg.norm(vector, axis=1)
    orientation = np.sign(np.sum(xyz[:, 0] * np.cross(xyz[:, 1], xyz[:, 2])))
    if not orientation or not np.isfinite(area).all() or np.any(area <= 0):
        raise ValueError("Invalid body surface or orientation")
    return xyz.mean(axis=1), orientation * vector, area


def integrated_forces(points, faces, cp, cf, dynamic_pressure):
    """Force ON the body, in newtons; +x is forward, +y is port."""
    _, vector, area = surface_geometry(points, faces)
    if not np.isfinite(cp[faces]).all() or not np.isfinite(cf[faces]).all():
        raise ValueError("Incomplete wall pressure or friction")
    pressure = -dynamic_pressure * np.sum(cp[faces].mean(axis=1)[:, None] * vector, axis=0)
    friction = dynamic_pressure * np.sum(cf[faces].mean(axis=1) * area[:, None], axis=0)
    return {"pressure_N": pressure.tolist(), "friction_N": friction.tolist(),
            "total_N": (pressure + friction).tolist()}


def wall_shear_sides(points, faces, cp, cf, sail, shape, cf_tolerance=1e-5):
    centers, vector, area = surface_geometry(points, faces)
    f = np.clip((centers[:, 2] - FOOT) / sail[2], 0, 1)
    a = np.radians(sail[3] + shape["twist_deg"] * (f - (shape["trim_height_m"] - FOOT) / sail[2]))
    # End caps have their normals along the chord/span. Exclude them from
    # the comparison of the two broad skins using normal alignment.
    normal_alignment = (vector[:, 0] * np.sin(a) + vector[:, 1] * np.cos(a)) / area
    cft, _ = sail_coordinates(centers, cf[faces].mean(axis=1), sail, shape)
    result = {}
    for name, sign in (("leeward", 1), ("windward", -1)):
        skin = sign * normal_alignment > .5
        total_area = float(area[skin].sum())
        if total_area <= 0:
            raise ValueError(f"No {name} skin triangles")
        reversed_area = float(area[skin & (cft < -cf_tolerance)].sum())
        result[name] = {"skin_area_m2": total_area,
                        "reversed_wall_shear_area_m2": reversed_area,
                        "reversed_wall_shear_area_percent": 100 * reversed_area / total_area,
                        "area_mean_pressure_coefficient": float(np.average(cp[faces].mean(axis=1)[skin], weights=area[skin])),
                        "minimum_tangent_skin_friction_coefficient": float(cft[skin].min())}
    return result


def analyse(case, output):
    case, output = Path(case), Path(output)
    geometry = json.loads(case_file(case, "geometry.json").read_text())
    manifest = json.loads(case_file(case, "marine_case.json").read_text())
    saved_report = json.loads(case_file(case, "report.json").read_text())
    cfg = {line.split("=", 1)[0].strip(): line.split("=", 1)[1].strip()
           for line in case_file(case, "yacht.cfg").read_text().splitlines()
           if "=" in line and not line.lstrip().startswith("%")}
    if cfg.get("INC_NONDIM") != "INITIAL_VALUES":
        raise ValueError("This diagnostic expects INC_NONDIM=INITIAL_VALUES")
    density = float(cfg["INC_DENSITY_INIT"])
    ref_area = float(cfg["REF_AREA"])
    scale = manifest["native_reference_speed_m_s"]
    configured = np.fromstring(cfg["INC_VELOCITY_INIT"].strip("()"), sep=",")
    if not np.isclose(np.linalg.norm(configured), scale, rtol=1e-12):
        raise ValueError("Reference velocity mismatch")
    q = .5 * density * scale**2
    fields, native = read_restart(case_file(case, "restart.dat"))
    if fields[:3] != ["x", "y", "z"]:
        raise ValueError("A full 3D restart is required")
    mesh = meshio.read(case_file(case, "yacht.msh"))
    if len(native) != len(mesh.points) or not np.allclose(native[:, :3], mesh.points, rtol=0, atol=1e-10):
        raise ValueError("Restart does not match original mesh")
    cp = native[:, fields.index("Pressure_Coefficient")]
    cf = native[:, [fields.index("Skin_Friction_Coefficient_" + axis) for axis in ("x", "y", "z")]]
    result = {"case": str(case), "solver_run": False,
              "method": "Pressure and friction surface integrals using original mesh and native double-precision coefficients",
              "axes": {"x": "forward", "y": "port", "z": "up"},
              "scope": "Aerodynamic force only; no underwater hull/keel resistance or speed equilibrium",
              "reference_speed_m_s": scale, "density_kg_m3": density,
              "dynamic_pressure_Pa": q, "reference_area_m2": ref_area,
              "wall_shear_criterion": "Local camber-tangent C_f < -1e-5; area-weighted broad skins, end caps excluded",
              "wall_shear_interpretation": "Reverse wall shear supports a local separation indication; it is not the full 3D separation topology",
              "checks_passed": bool(saved_report["checks_passed"]),
              "saved_convergence": saved_report["convergence"],
              "saved_background_profile_check": saved_report["marine_profile_preservation"],
              "bodies": {}, "wall_shear": {},
              "source_sha256": {name: sha(case_file(case, name)) for name in
                                ("yacht.msh", "restart.dat", "geometry.json", "yacht.cfg", "history.csv", "report.json")}}
    for name, marker in (("hull", 12), ("main", 13), ("jib", 14)):
        faces = marked_triangles(mesh, marker)
        result["bodies"][name] = integrated_forces(mesh.points, faces, cp, cf, q)
        if name in ("main", "jib"):
            index = ("main", "jib").index(name)
            result["wall_shear"][name] = wall_shear_sides(mesh.points, faces, cp, cf,
                geometry["sail_definitions"][index], geometry["sail_shapes"][index])
    combined = np.sum([result["bodies"][n]["total_N"] for n in ("main", "jib")], axis=0)
    total = combined + result["bodies"]["hull"]["total_N"]
    history = np.atleast_1d(np.genfromtxt(case_file(case, "history.csv"), delimiter=",", names=True, deletechars=' "'))
    native_force = np.array([history[name][-1] for name in ("CFx", "CFy", "CFz")]) * q * ref_area
    error = float(np.linalg.norm(total - native_force) / max(np.linalg.norm(native_force), 1e-12))
    result.update(sails_combined_N=combined.tolist(), total_aerodynamic_N=total.tolist(),
                  native_solver_force_N=native_force.tolist(), iteration=float(history["Inner_Iter"][-1]),
                  integration_check={"relative_vector_error": error, "tolerance": .02, "passed": error < .02})
    output.mkdir(parents=True, exist_ok=True)
    (output / "sail_loads.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if not result["integration_check"]["passed"]:
        raise ValueError("Surface integral disagrees with native solver forces; inspect sail_loads.json")
    save_plot(result, output / "sail_loads.png")
    save_markdown(result, output / "sail_loads.md")
    return result


def save_plot(result, target):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    labels = ["Storsejl", "Forsejl", "Begge sejl", "Skrog + mast", "Samlet luftkraft"]
    values = [result["bodies"]["main"]["total_N"][0], result["bodies"]["jib"]["total_N"][0],
              result["sails_combined_N"][0], result["bodies"]["hull"]["total_N"][0], result["total_aerodynamic_N"][0]]
    fig, ax = plt.subplots(figsize=(10, 5.5), layout="constrained")
    bars = ax.barh(labels, values, color=["#4d8ea7", "#bd9550", "#367c65", "#87949c", "#27485e"])
    ax.bar_label(bars, labels=[f"{v:+.1f} N" for v in values], padding=8)
    ax.invert_yaxis()
    ax.axvline(0, color="#526672", linewidth=.8)
    ax.set(xlabel="Kraft i bådens fremadretning [N]", title="Trækker sejlene? · kræfter fra det gemte 3D-resultat", xlim=(-210, 420))
    ax.spines[["top", "right"]].set_visible(False)
    fig.text(.5, -.015, "Foreløbig beregning · baggrundsprofilens kontrol fejler · vandmodstand er ikke med",
             ha="center", fontsize=10, color="#76542e")
    fig.savefig(target, dpi=180, bbox_inches="tight")
    plt.close(fig)


def save_markdown(result, target):
    lines = ["# Sejlkræfter og kontrol af begge sider", "",
             f"Gemte 3D-data, iteration {result['iteration']:g}. Ingen ny simulering.", "",
             "Positive fremdriftskræfter peger mod stævnen. Tallene er luftkræfter; vandmodstand indgår ikke.", "",
             "| Del | Fremdrift [N] | Sidekraft mod bagbord [N] | Vertikal kraft [N] |",
             "|---|---:|---:|---:|"]
    for label, vector in (("Storsejl", result["bodies"]["main"]["total_N"]),
                          ("Forsejl", result["bodies"]["jib"]["total_N"]),
                          ("Begge sejl", result["sails_combined_N"]),
                          ("Skrog og mast", result["bodies"]["hull"]["total_N"]),
                          ("Samlet luftkraft", result["total_aerodynamic_N"])):
        lines.append(f"| {label} | {vector[0]:.2f} | {vector[1]:.2f} | {vector[2]:.2f} |")
    lines.extend(["", "## Omvendt vægfriktion", "", "Kriterium: lokal tangential C_f < −0,00001. Arealvægtet; smalle endeflader udeladt.", "",
                  "| Sejl | Side | Areal med omvendt vægfriktion [m²] | Andel af siden [%] | Middel-Cp |",
                  "|---|---|---:|---:|---:|"])
    for name, skins in result["wall_shear"].items():
        sail_label = "Storsejl" if name == "main" else "Forsejl"
        for side, data in skins.items():
            label = "Læside" if side == "leeward" else "Luvside"
            lines.append(f"| {sail_label} | {label} | {data['reversed_wall_shear_area_m2']:.5f} | "
                         f"{data['reversed_wall_shear_area_percent']:.3f} | {data['area_mean_pressure_coefficient']:.4f} |")
    error = result["integration_check"]["relative_vector_error"]
    lines.extend(["", f"Den integrerede kraft afviger {100*error:.4f} % fra SU2's gemte samlede kraftvektor.", "",
                  "## Fortolkning", "",
                  "Omvendt vægfriktion og tilbagestrømning er lokale indikatorer på separation; de beskriver ikke hele 3D-separationens topologi.", "",
                  "De gemte residual-, kraftstabilitets-, feltstabilitets- og vægkontroller består. Kontrollen af den omgivende vind- og turbulensprofil fejler. "
                  "Afvigelsen indeholder både fysisk induktion fra båden og numeriske ændringer; den kan ikke alene tilskrives en fejl i indløbet. "
                  "Kræfter og separation skal derfor stadig behandles som foreløbige modelresultater.", "",
                  "Kildestier, SHA-256, tryk-/friktionsbidrag og den gemte baggrundskontrol findes i `sail_loads.json`.", ""])
    target.write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, default=Path("cfd/cases/marine_3d_padded_5ms"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or result_dir(args.case) / "diagnostics"
    result = analyse(args.case, output)
    for name, data in result["bodies"].items():
        print(name, "force [N]", data["total_N"], flush=True)
    print("Integration check:", result["integration_check"], flush=True)
    print("Wall shear:", result["wall_shear"], flush=True)
    print(f"Saved report: {output / 'sail_loads.md'}")


if __name__ == "__main__":
    main()
