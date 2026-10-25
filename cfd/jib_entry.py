"""Saved-result diagnostics of jib entry incidence and downstream wall shear.

Incidence is defined in the horizontal section: AWA minus profile direction.
The actual 3D solution's saved horizontal slice supplies local velocity;
the original mesh and native checkpoint supply the wall shear. No CFD runs.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import meshio
import numpy as np

from cfd.case import case_chord_trim, case_entry_angle, wind_angle_from_velocity
from cfd.marine_rans import read_profile
from cfd.plot_data import load_plot_data
from cfd.rans_checks import marked_triangles
from cfd.rans_mesh import FOOT, _skin_points
from cfd.sail_loads import surface_geometry
from cfd.separation_3d import sail_coordinates, sha
from cfd.storage import case_file
from cfd.transfer_solution import read_restart


def angle_difference(awa, profile_direction):
    return (np.asarray(awa) - profile_direction + 180) % 360 - 180


def slice_sampler(context, arrays, height):
    from matplotlib.tri import Triangulation, LinearTriInterpolator

    if not np.isclose(context["height_m"], height, rtol=0, atol=1e-9):
        raise ValueError("Saved slice height differs from requested height")
    xy = arrays["xy"]
    tri = Triangulation(xy[:, 0], xy[:, 1], arrays["triangles"])
    interpolators = [LinearTriInterpolator(tri, arrays["velocity"][:, i]) for i in range(3)]

    def sample(points):
        points = np.asarray(points)
        if not np.allclose(points[:, 2], height, rtol=0, atol=1e-9):
            raise ValueError("Velocity sampling must remain in the saved horizontal plane")
        # Use the saved fluid triangles, preserving solid holes. No new
        # Delaunay mesh and no extrapolation through the sail interior.
        return np.column_stack([np.ma.filled(f(points[:, 0], points[:, 1]), np.nan) for f in interpolators])
    return sample


def wall_section(mesh, native, fields, sail, shape, height):
    import pyvista as pv

    faces = marked_triangles(mesh, 14)
    centers, outward, area = surface_geometry(mesh.points, faces)
    a = np.radians(sail[3] + shape["twist_deg"] * (centers[:, 2] - shape["trim_height_m"]) / sail[2])
    alignment = (outward[:, 0] * np.sin(a) + outward[:, 1] * np.cos(a)) / area
    sides = np.where(alignment > .5, 1, np.where(alignment < -.5, -1, 0))
    used, mapped = np.unique(faces, return_inverse=True)
    wall = pv.PolyData(mesh.points[used], np.column_stack((np.full(len(faces), 3), mapped.reshape(-1, 3))).ravel())
    wall["Cf"] = native[used][:, [fields.index("Skin_Friction_Coefficient_" + axis) for axis in ("x", "y", "z")]]
    wall["Cp"] = native[used, fields.index("Pressure_Coefficient")]
    wall.cell_data["side"] = sides
    cut = wall.slice(normal=(0, 0, 1), origin=(0, 0, height))
    lines = cut.lines.reshape(-1, 3)
    if not len(lines) or not (lines[:, 0] == 2).all():
        raise ValueError("No linear wall intersections at this height")
    edges = lines[:, 1:]
    points = cut.points[edges].mean(axis=1)
    cft, _ = sail_coordinates(points, cut["Cf"][edges].mean(axis=1), sail, shape)
    leading = _skin_points(sail, height, 0., shape, 0)[0]
    trim = np.radians(sail[3] + shape["twist_deg"] * (height - shape["trim_height_m"]) / sail[2])
    chord = sail[1] * (1 - .96 * (height - FOOT) / sail[2])
    t = ((points[:, 0] - leading[0]) * -np.cos(trim) + points[:, 1] * np.sin(trim)) / chord
    cp = cut["Cp"][edges].mean(axis=1)
    length = np.linalg.norm(np.diff(cut.points[edges], axis=1)[:, 0], axis=1)
    records = []
    for i in np.argsort(t):
        if cut.cell_data["side"][i] == 0:
            continue
        records.append({"height_m": height, "side": "leeward" if cut.cell_data["side"][i] == 1 else "windward",
                        "chord_fraction": float(t[i]), "Cf_t": float(cft[i]), "Cp": float(cp[i]), "segment_length_m": float(length[i])})
    return records


def write_csv(path, records):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)


def friction_recoveries(sections):
    """Approximate negative-to-positive crossings between wall segment centres."""
    result = []
    for height in sorted({r["height_m"] for r in sections}):
        for side in ("leeward", "windward"):
            rows = sorted((r for r in sections if r["height_m"] == height and r["side"] == side),
                          key=lambda r: r["chord_fraction"])
            for p, q in zip(rows, rows[1:]):
                if p["Cf_t"] < 0 <= q["Cf_t"]:
                    t = p["chord_fraction"] - p["Cf_t"] * (q["chord_fraction"] - p["chord_fraction"]) / (q["Cf_t"] - p["Cf_t"])
                    result.append({"height_m": height, "side": side, "negative_to_positive_Cf_x_c": t})
    return result


def analyse(case, output, height=4.):
    case, output = Path(case), Path(output)
    geometry = json.loads(case_file(case, "geometry.json").read_text())
    sail, shape = geometry["sail_definitions"][1], geometry["sail_shapes"][1]
    manifest = json.loads(case_file(case, "marine_case.json").read_text())
    saved_report = json.loads(case_file(case, "report.json").read_text())
    profile = read_profile(manifest)
    context, arrays = load_plot_data(case)
    sample = slice_sampler(context, arrays, height)
    leading = _skin_points(sail, height, 0., shape, 0)[0]
    chord = sail[1] * (1 - .96 * (height - FOOT) / sail[2])
    trim = case_chord_trim(geometry, 1, height)
    tangent = trim + np.degrees(np.arctan(4 * shape["camber"]))
    secant = case_entry_angle(geometry, 1, height)
    ambient = np.asarray(profile.at(height)["apparent_velocity_m_s"])
    ambient_awa = wind_angle_from_velocity(ambient)
    distances = chord * np.array([.01, .025, .05, .1, .25, .5, 1., 2.])
    # An approach ray opposite the undisturbed horizontal apparent velocity.
    upstream = -ambient.copy(); upstream[2] = 0.; upstream /= np.linalg.norm(upstream)
    queries = leading + distances[:, None] * upstream
    velocity = sample(queries)
    awa = np.degrees(np.arctan2(velocity[:, 1], -velocity[:, 0]))
    probes = [{"upstream_distance_m": float(d), "upstream_distance_chords": float(d / chord),
               "x_m": float(p[0]), "y_m": float(p[1]), "z_m": height,
               "velocity_x_m_s": float(v[0]), "velocity_y_m_s": float(v[1]), "velocity_z_m_s": float(v[2]),
               "AWA_deg": float(w), "alpha_to_chord_deg": float(angle_difference(w, trim)),
               "alpha_to_entry_secant_deg": float(angle_difference(w, secant)),
               "alpha_to_entry_tangent_deg": float(angle_difference(w, tangent))}
              for d, p, v, w in zip(distances, queries, velocity, awa, strict=True)]
    fields, native = read_restart(case_file(case, "restart.dat"))
    mesh = meshio.read(case_file(case, "yacht.msh"))
    if fields[:3] != ["x", "y", "z"] or len(native) != len(mesh.points) or not np.allclose(native[:, :3], mesh.points, rtol=0, atol=1e-10):
        raise ValueError("Native 3D restart does not match the original mesh")
    sections = []
    for h in sorted(h for h in set([1.5, 2., height, 6., 8., 9.3]) if FOOT < h < FOOT + sail[2]):
        sections.extend(wall_section(mesh, native, fields, sail, shape, h))
    # Sample horizontal velocity profiles normal to the actual analytic skin,
    # at the cached height. Invalid solid/outside samples remain NaN.
    profiles = []
    a = np.radians(trim)
    for t in (.005, .01, .025, .05, .1, .25, .5, .75, .95):
        slope = 4 * shape["camber"] * (1 - 2 * t)
        normal = np.array([np.sin(a) + slope * np.cos(a), np.cos(a) - slope * np.sin(a), 0.])
        normal /= np.linalg.norm(normal)
        for side, sign in (("leeward", 1), ("windward", -1)):
            skin = _skin_points(sail, height, t, shape, sign)[0]
            offsets = np.array([.0001, .0005, .001, .002, .005, .01, .02, .05, .1, .2])
            points = skin + sign * offsets[:, None] * normal
            sampled = sample(points)
            ut, _ = sail_coordinates(points, sampled, sail, shape, sign)
            for n, v, u in zip(offsets, sampled, ut, strict=True):
                profiles.append({"height_m": height, "side": side, "chord_fraction": t, "normal_offset_m": float(n),
                                 "velocity_t_m_s": float(u), "velocity_z_m_s": float(v[2]), "valid_fluid_sample": bool(np.isfinite(v).all())})
    info = {"case": str(case), "solver_run": False, "height_m": height,
            "angle_definition": "Horizontal AWA minus profile direction; negative means apparent wind angle is smaller than the entry direction",
            "geometry_unchanged": True, "chord_m": chord, "chord_trim_deg": trim,
            "entry_exact_tangent_deg": tangent, "entry_first_5_percent_secant_deg": secant,
            "reference_AWA_deg": ambient_awa,
            "reference_alpha_chord_deg": float(angle_difference(ambient_awa, trim)),
            "reference_alpha_entry_secant_deg": float(angle_difference(ambient_awa, secant)),
            "reference_alpha_entry_tangent_deg": float(angle_difference(ambient_awa, tangent)),
            "local_approach_probes": probes,
            "wall_friction_recoveries": friction_recoveries(sections),
            "iteration": saved_report.get("last_history", {}).get("Inner_Iter"),
            "checks_passed": bool(saved_report.get("checks_passed")),
            "background_profile_check_passed": saved_report.get("marine_profile_preservation", {}).get("passed"),
            "limitations": ["Local incidence depends on probe location; velocity on the no-slip wall is zero",
                            "Local upstream velocity includes the sail's own induction; it is not an independent free-stream incidence",
                            "Horizontal slice of a 3D solution, not a span-normal 3D incidence definition",
                            "Forward downstream wall shear alone does not exclude crossflow separation",
                            "Inspect the stored background-profile check before quantitative physical interpretation"],
            "source_sha256": {name: sha(case_file(case, name)) for name in
                              ("geometry.json", "restart.dat", "yacht.msh", "slice.npz", "plot_context.json", "report.json")}}
    output.mkdir(parents=True, exist_ok=True)
    (output / "jib_entry.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    write_csv(output / "entry_probes.csv", probes)
    write_csv(output / "wall_sections.csv", sections)
    write_csv(output / "normal_velocity_profiles.csv", profiles)
    save_plot(info, sections, profiles, output / "jib_entry.png")
    save_markdown(info, output / "jib_entry.md")
    print(json.dumps({k: v for k, v in info.items() if k != "source_sha256"}, ensure_ascii=False, indent=2), flush=True)
    for h in sorted({r["height_m"] for r in sections}):
        for side in ("leeward", "windward"):
            selected = [r for r in sections if r["height_m"] == h and r["side"] == side and r["Cf_t"] < -1e-5]
            print('reverse wall shear',h,side,'chord range',
                  (min(r['chord_fraction'] for r in selected),max(r['chord_fraction'] for r in selected)) if selected else None)
    return info, sections, profiles


def save_markdown(info, target):
    lines = ["# Fokkens indgang og strømningen længere tilbage", "",
             f"Vandret snit ved {info['height_m']:g} m, uændret geometri, gemt 3D-resultat fra iteration {info['iteration']:g}.", "",
             "## Vinkeldefinition", "",
             "Her er α = AWA − profilretning, målt i det vandrette snit. En negativ indgangsvinkel betyder, at AWA er mindre end indgangsretningen.", "",
             f"Lokal korde: {info['chord_m']:.4f} m. Kordetrim: {info['chord_trim_deg']:.2f}°. "
             f"Forligets eksakte tangent: {info['entry_exact_tangent_deg']:.2f}°. Sekant over de første 5 %: {info['entry_first_5_percent_secant_deg']:.2f}°.", "",
             f"Den foreskrevne referencevind har AWA {info['reference_AWA_deg']:.2f}°: "
             f"α til korden = {info['reference_alpha_chord_deg']:+.2f}°, "
             f"α til 5 %-indgangen = {info['reference_alpha_entry_secant_deg']:+.2f}°, "
             f"α til tangenten = {info['reference_alpha_entry_tangent_deg']:+.2f}°.", "",
             "## Lokal vind foran forliget", "",
             "Punkterne ligger på en ret tilgangslinje modsat referencevinden. Værdierne indeholder også sejlenes egen induktion og er ikke en uforstyrret, effektiv indløbsvinkel.", "",
             "| Afstand foran forlig [m] | Afstand [c] | Lokal AWA [°] | α til 5 %-indgangen [°] | α til tangenten [°] |",
             "|---:|---:|---:|---:|---:|"]
    for row in info["local_approach_probes"]:
        lines.append(f"| {row['upstream_distance_m']:.4f} | {row['upstream_distance_chords']:.3f} | {row['AWA_deg']:.2f} | "
                     f"{row['alpha_to_entry_secant_deg']:+.2f} | {row['alpha_to_entry_tangent_deg']:+.2f} |")
    lines.extend(["", "## Vægfriktionens genopretning", "",
                  "Lineært anslået skift fra negativ til positiv tangential vægfriktion mellem segmentcentrene. Det er et tegn på lokal genhæftning i dette snit, ikke et bevis for fuld tilhæftning i tre dimensioner.", "",
                  "| Højde [m] | Side | Skift ved x/c |", "|---:|---|---:|"])
    for row in info["wall_friction_recoveries"]:
        label = "Læside" if row["side"] == "leeward" else "Luvside"
        lines.append(f"| {row['height_m']:g} | {label} | {row['negative_to_positive_Cf_x_c']:.4f} |")
    lines.extend(["", "## Begrænsninger", "",
                  "Tilbagestrømningsvolumenerne viser ikke nødvendigvis hele den separerede region. I 3D skal også tværstrømning og overfladestrømlinjer vurderes. "
                  "Hastighedsprofilerne her er prøver i ét vandret snit. Der ekstrapoleres ikke gennem sejlet eller uden for de gemte fluidtrekanter.", "",
                  f"Samlede kontrolkrav opfyldt: {info['checks_passed']}. Baggrundsprofilens kontrol opfyldt: {info['background_profile_check_passed']}.", "",
                  "## Genbrug", "",
                  "Kør `python -m cfd.jib_entry` fra projektroden. Programmet læser den importerede sag og gemmer figur, JSON og alle prøver som CSV. "
                  "Kildestier og SHA-256 findes i JSON-filen. Højden skal matche sagens gemte `slice.npz`.", ""])
    target.write_text("\n".join(lines), encoding="utf-8")


def save_plot(info, sections, profiles, target):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 2, figsize=(13, 9), layout="constrained")
    probes = info["local_approach_probes"]
    ax = axes[0, 0]
    ax.plot([r["upstream_distance_chords"] for r in probes], [r["AWA_deg"] for r in probes], "o-", label="Lokalt 3D-resultat")
    ax.axhline(info["reference_AWA_deg"], color="#526672", linestyle="--", label="Referencevind")
    ax.axhline(info["entry_exact_tangent_deg"], color="#b36c24", linestyle=":", label="Forligets tangent")
    ax.set(xscale="log", xlabel="Afstand foran forlig [lokale kordelængder]", ylabel="Vinkel fra bådens centerlinje [°]", title=f"Vindretning foran fokken ved {info['height_m']:g} m")
    ax.legend(fontsize=8)
    ax = axes[0, 1]
    for side, color, label in (("leeward", "#df4d26", "Læside"), ("windward", "#236eca", "Luvside")):
        rows = [r for r in sections if r["height_m"] == info["height_m"] and r["side"] == side]
        ax.plot([r["chord_fraction"] for r in rows], [r["Cf_t"] for r in rows], ".-", markersize=3, linewidth=.8, label=label, color=color)
    ax.axhline(0, color="black", linewidth=.7)
    ax.set(xlabel="Afstand fra forlig [x/c]", ylabel="Tangential vægfriktion C_f,t", title="Vægfriktion langs hele profilet")
    ax.legend(fontsize=8)
    ax = axes[1, 0]
    for side, color, label in (("leeward", "#df4d26", "Læside"), ("windward", "#236eca", "Luvside")):
        rows = [r for r in sections if r["height_m"] == info["height_m"] and r["side"] == side]
        ax.plot([r["chord_fraction"] for r in rows], [r["Cp"] for r in rows], ".-", markersize=3, linewidth=.8, label=label, color=color)
    ax.set(xlabel="Afstand fra forlig [x/c]", ylabel="Trykkoefficient C_p", title="Trykfordeling i samme snit")
    ax.invert_yaxis()
    ax.legend(fontsize=8)
    ax = axes[1, 1]
    for t in (.005, .025, .1, .5, .95):
        rows = [r for r in profiles if r["side"] == "leeward" and r["chord_fraction"] == t and r["valid_fluid_sample"]]
        ax.plot([r["velocity_t_m_s"] for r in rows], [r["normal_offset_m"] * 1000 for r in rows], "o-", markersize=3, label=f"x/c={t:g}")
    ax.axvline(0, color="black", linewidth=.7)
    ax.set(yscale="log", xlabel="Hastighed langs profilet [m/s]", ylabel="Afstand fra læside [mm]", title="Hastighedsprofiler fra det gemte 3D-snit")
    ax.legend(fontsize=8)
    fig.suptitle("Fokkens indgang og strømningen længere tilbage · uændret sejlgeometri", fontsize=15)
    fig.savefig(target, dpi=170)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, default=Path("cfd/cases/marine_3d_padded_5ms"))
    parser.add_argument("--output", type=Path, default=Path("cfd/results/v1_5ms/diagnostics/jib_entry"))
    parser.add_argument("--height", type=float, default=4.)
    args = parser.parse_args()
    analyse(args.case, args.output, args.height)


if __name__ == "__main__":
    main()
