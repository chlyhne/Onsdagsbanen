"""Show resolved reverse flow next to the leeward sail faces in saved 3D CFD.

Reads the original mesh and binary SU2 restart. No solver, extrapolation from
a slice, geometric inflation or smoothing is used. Reverse flow is an
indicator of separation, not a reconstruction of every separated streamline.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import meshio
import numpy as np

from cfd.plot_geometry_3d import COLORS, geometry_surfaces, interactive_figure
from cfd.rans_mesh import FOOT
from cfd.storage import case_file, result_dir
from cfd.transfer_solution import read_restart

VOLUME_TYPES = {"tetra", "wedge", "pyramid", "hexahedron"}
BLOB_COLORS = {"main": "#df4d26", "jib": "#ed961f"}
WINDWARD_COLORS = {"main": "#236eca", "jib": "#149aa5"}
NAMES = {"main": "storsejl", "jib": "forsejl"}


def sail_coordinates(points, velocity, sail, shape, side=1):
    """Chordwise tangent velocity and a signed search margin in metres.

    Side +1 is the leeward face for this saved starboard inflow; -1 is the
    opposite face. The margin restricts the search to that face, within the actual
    foot/head and leading/trailing edges, up to 0.75 local chords away.
    The near-face limit does not change the velocity threshold.
    """
    if side not in (-1, 1):
        raise ValueError("side must be +1 or -1")
    z = points[:, 2]
    f = np.clip((z - FOOT) / sail[2], 0, 1)
    chord = sail[1] * (1 - .96 * f)
    angle = np.radians(sail[3] + shape["twist_deg"] *
                       (f - (shape["trim_height_m"] - FOOT) / sail[2]))
    leading = sail[0] * (1 - f) + shape["head_luff_x_m"] * f
    u = -(points[:, 0] - leading) * np.cos(angle) + points[:, 1] * np.sin(angle)
    v = (points[:, 0] - leading) * np.sin(angle) + points[:, 1] * np.cos(angle)
    t = np.clip(u / chord, 0, 1)
    slope = 4 * shape["camber"] * (1 - 2 * t)
    clearance = side * (v - 4 * shape["camber"] * chord * t * (1 - t))
    tangent_x = -np.cos(angle) + slope * np.sin(angle)
    tangent_y = np.sin(angle) + slope * np.cos(angle)
    ut = (velocity[:, 0] * tangent_x + velocity[:, 1] * tangent_y) / np.sqrt(1 + slope**2)
    margin = np.minimum.reduce((u, chord - u, clearance, .75 * chord - clearance,
                                z - FOOT, FOOT + sail[2] - z))
    return ut, margin


def reverse_volume(mesh, ut, margin, reverse_speed):
    """Clip the original hybrid cells, first by velocity, then by location."""
    import pyvista as pv

    if reverse_speed <= 0 or not np.isfinite(reverse_speed):
        raise ValueError("reverse_speed must be finite and positive, in m/s")
    cells = []
    # Retain every cell which can intersect both half-spaces under nodal
    # interpolation, even when their intersection has no original vertex.
    for block in mesh.cells:
        if block.type not in VOLUME_TYPES:
            continue
        for start in range(0, len(block.data), 200_000):
            nodes = block.data[start:start + 200_000]
            keep = ((ut[nodes] < -reverse_speed).any(axis=1) &
                    (margin[nodes] >= 0).any(axis=1))
            if keep.any():
                cells.append((block.type, nodes[keep]))
    if not cells:
        return pv.PolyData(), 0.
    used = np.unique(np.concatenate([c.ravel() for _, c in cells]))
    mapping = np.full(len(mesh.points), -1, dtype=np.int64)
    mapping[used] = np.arange(len(used))
    vtk_types = {"tetra": pv.CellType.TETRA, "wedge": pv.CellType.WEDGE,
                 "pyramid": pv.CellType.PYRAMID, "hexahedron": pv.CellType.HEXAHEDRON}
    connectivity, types = [], []
    for kind, nodes in cells:
        mapped = mapping[nodes]
        # Meshio and VTK have opposite wedge node order. PyVista 0.49.1's
        # from_meshio omits this permutation, so build the VTK grid explicitly.
        if kind == "wedge":
            mapped = mapped[:, [0, 2, 1, 3, 5, 4]]
        connectivity.append(np.column_stack((np.full(len(mapped), mapped.shape[1]), mapped)).ravel())
        types.append(np.full(len(mapped), vtk_types[kind], dtype=np.uint8))
    grid = pv.UnstructuredGrid(np.concatenate(connectivity), np.concatenate(types), mesh.points[used])
    grid["tangent_velocity_m_s"] = ut[used]
    grid["search_margin_m"] = margin[used]
    # Use a conforming linear decomposition of the retained hybrid cells.
    grid = grid.triangulate()
    volume = grid.clip_scalar(scalars="tangent_velocity_m_s", value=-reverse_speed, invert=True)
    if not volume.n_cells:
        return pv.PolyData(), 0.
    volume = volume.clip_scalar(scalars="search_margin_m", value=0., invert=False)
    if not volume.n_cells:
        return pv.PolyData(), 0.
    sizes = volume.compute_cell_sizes(length=False, area=False, volume=True)["Volume"]
    if not np.isfinite(sizes).all() or np.any(sizes < -1e-16):
        raise ValueError(f"Invalid clipped cell volumes: minimum={sizes.min():g}, "
                         f"negative={np.count_nonzero(sizes < -1e-16)}/{len(sizes)}")
    surface = volume.extract_surface(algorithm="dataset_surface").triangulate().clean(
        tolerance=1e-10, absolute=True)
    return surface, float(np.maximum(sizes, 0).sum())


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def cache_prefix(sides):
    return "separation_3d" + {"leeward": "", "windward": "_windward", "both": "_both_sides"}[sides]


def region_style(key, statistics):
    sail = statistics.get("sail", key)
    side = statistics.get("side", "leeward")
    colors = BLOB_COLORS if side == "leeward" else WINDWARD_COLORS
    label = NAMES[sail] + (" · læside" if side == "leeward" else " · luvside")
    return colors[sail], label, side


def prepare(case, output, reverse_speed=.05, sides="leeward"):
    case, output = Path(case), Path(output)
    geometry_path = case_file(case, "geometry.json")
    geometry = json.loads(geometry_path.read_text())
    geometry_surfaces(geometry)  # Validate the saved geometry version.
    manifest = json.loads(case_file(case, "marine_case.json").read_text())
    report = json.loads(case_file(case, "report.json").read_text())
    fields, native = read_restart(case_file(case, "restart.dat"))
    expected = ["x", "y", "z", "Pressure", "Velocity_x", "Velocity_y", "Velocity_z"]
    if fields[:7] != expected:
        raise ValueError("A full 3D restart with x/y/z velocity is required")
    print("Reading original hybrid mesh", flush=True)
    mesh = meshio.read(case_file(case, "yacht.msh"))
    if native.shape[0] != len(mesh.points) or not np.allclose(native[:, :3], mesh.points, rtol=0, atol=1e-10):
        raise ValueError("Restart coordinates do not match the original mesh")
    if not any(block.type in VOLUME_TYPES for block in mesh.cells):
        raise ValueError("The input must have 3D volume cells; a 2D slice is insufficient")
    cfg = {line.split("=", 1)[0].strip(): line.split("=", 1)[1].strip()
           for line in case_file(case, "yacht.cfg").read_text().splitlines()
           if "=" in line and not line.lstrip().startswith("%")}
    if cfg.get("INC_NONDIM") != "INITIAL_VALUES":
        raise ValueError("The native velocity scale requires INC_NONDIM=INITIAL_VALUES")
    scale = manifest["native_reference_speed_m_s"]
    configured_velocity = np.fromstring(cfg["INC_VELOCITY_INIT"].strip("()"), sep=",")
    if not np.isclose(np.linalg.norm(configured_velocity), scale, rtol=1e-12):
        raise ValueError("Velocity reference in configuration differs from the saved manifest")
    velocity = np.asarray(native[:, 4:7]) * scale
    if not np.isfinite(velocity).all():
        raise ValueError("Non-finite volume velocity")
    # This geometry's leeward face is +v. Refuse a wind for which that choice
    # would silently show the other side. The test uses the chord normal.
    wind = np.asarray(manifest["native_reference_velocity_m_s"])
    arrays = {}
    info = {"kind": "3D reverse-flow volume", "solver_run": False,
            "case": str(case), "reverse_speed_m_s": reverse_speed,
            "criterion": "u_t < -reverse_speed, with u_t projected onto the local horizontal camber tangent",
            "interpretation": "Reverse flow near selected sail faces; indicator of separation, not the full separated-flow region",
            "sides": sides,
            "region": "Selected camber side(s), foot <= z <= head, 0 <= chord fraction <= 1, clearance <= 0.75 local chord",
            "method": "Conforming tetrahedral decomposition of original hybrid fluid cells, then linear clipping; no smoothing, extrusion or thickness scaling",
            "geometry": geometry, "native_reference_speed_m_s": scale,
            "iteration": report.get("last_history", {}).get("Inner_Iter"),
            "checks_passed": bool(report.get("checks_passed")), "sails": {},
            "verification": {"convergence_passed": report.get("convergence", {}).get("passed"),
                             "field_stability_passed": report.get("field_stability", {}).get("passed"),
                             "walls_passed": report.get("wall_resolution", {}).get("walls_passed"),
                             "background_profile_passed": report.get("marine_profile_preservation", {}).get("passed")},
            "source_sha256": {name: sha(case_file(case, name))
                              for name in ("geometry.json", "yacht.msh", "restart.dat", "report.json")}}
    for sail, shape in zip(geometry["sail_definitions"], geometry["sail_shapes"], strict=True):
        name = shape["name"]
        a = np.radians(sail[3])
        if np.dot(wind[:2], [np.sin(a), np.cos(a)]) <= 0:
            raise ValueError("This inflow does not have the expected positive-camber leeward side")
        selected = [("leeward", 1), ("windward", -1)] if sides == "both" else [(sides, 1 if sides == "leeward" else -1)]
        for side_name, sign in selected:
            key = name if sign == 1 else name + "_windward"
            ut, margin = sail_coordinates(mesh.points, velocity, sail, shape, sign)
            count = int(np.count_nonzero((ut < -reverse_speed) & (margin > 0)))
            print(f"Extracting {key}: {count} reverse-flow nodes", flush=True)
            surface, volume = reverse_volume(mesh, ut, margin, reverse_speed)
            triangles = surface.faces.reshape(-1, 4)[:, 1:] if surface.n_cells else np.empty((0, 3), dtype=int)
            arrays[key + "_points"] = np.asarray(surface.points)
            arrays[key + "_triangles"] = triangles
            arrays[key + "_velocity_t_m_s"] = np.asarray(surface["tangent_velocity_m_s"]) if surface.n_points else np.empty(0)
            info["sails"][key] = {"sail": name, "side": side_name, "reverse_node_count": count,
                                  "volume_m3": volume, "surface_points": surface.n_points,
                                  "surface_triangles": len(triangles), "open_edges": surface.n_open_edges,
                                  "bounds_m": [surface.points.min(axis=0).tolist(), surface.points.max(axis=0).tolist()] if surface.n_points else None}
            print(key, info["sails"][key], flush=True)
    output.mkdir(parents=True, exist_ok=True)
    prefix = cache_prefix(sides)
    np.savez_compressed(output / (prefix + ".npz"), **arrays)
    (output / (prefix + ".json")).write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    return info, arrays


def render(info, arrays, output):
    import plotly.graph_objects as go

    output = Path(output)
    surfaces = geometry_surfaces(info["geometry"])
    fig = interactive_figure(surfaces)
    fig.update_traces(opacity=.16, selector=dict(type="mesh3d"))
    side_title = {"both": "på begge sider af sejlene", "leeward": "på sejlenes læside", "windward": "på sejlenes luvside"}[info.get("sides", "leeward")]
    fig.update_layout(title=dict(text=f"Tilbagestrømning {side_title} · eksisterende 3D-resultat",
                                 y=.98, yanchor="top", font=dict(size=21)),
                      margin=dict(l=0, r=0, t=110, b=155),
                      legend=dict(x=.02, y=-.025, yanchor="top", orientation="h"))
    visibility_sides = [None] * len(fig.data)
    for key, statistics in info["sails"].items():
        color, label, side = region_style(key, statistics)
        points, faces = arrays[key + "_points"], arrays[key + "_triangles"]
        if not len(faces):
            continue
        fig.add_trace(go.Mesh3d(
            x=points[:, 0], y=points[:, 1], z=points[:, 2],
            i=faces[:, 0], j=faces[:, 1], k=faces[:, 2], opacity=.95,
            color=color, name=label,
            showlegend=True, legendgroup="reverse_" + key,
            lighting=dict(ambient=.65, diffuse=.65, specular=.15),
            hovertemplate="Beregnet tilbagestrømning<br>x=%{x:.3f} m<br>y=%{y:.3f} m<br>z=%{z:.3f} m<extra></extra>"))
        visibility_sides.append(side)
    threshold = info["reverse_speed_m_s"]
    status = "Kontrolkrav opfyldt" if info["checks_passed"] else "Foreløbigt resultat · samlede kontrolkrav ikke opfyldt"
    color_note = {"both": "Rød/orange: læside · blå/turkis: luvside", "leeward": "Rød/orange: læside",
                  "windward": "Blå/turkis: luvside"}[info.get("sides", "leeward")]
    fig.layout.annotations[0].text = (
        f"{color_note} · uₜ &lt; −{threshold:g} m/s · indikator på separation"
        f"<br>{status} · iteration {info['iteration']:g}. Træk for at dreje; rul for at zoome.")
    fig.layout.annotations[0].y = -.13
    # Detail at 4 m is an axis crop, not a geometric enlargement of the blob.
    # Copy the camera buttons: reusing live Plotly objects while replacing
    # their parent menu otherwise overwrites the new detail-button labels.
    buttons = [button.to_plotly_json() for button in fig.layout.updatemenus[0].buttons]
    overview = {"scene.xaxis.range": [-5.7, 5.7], "scene.yaxis.range": [-2.7, 2.7],
                "scene.zaxis.range": [-.6, 12.3]}
    detail = {"scene.xaxis.range": [-.45, .15], "scene.yaxis.range": [-.05, .25],
              "scene.zaxis.range": [3.6, 4.4]}
    fig.layout.updatemenus = [dict(type="buttons", direction="right", x=.02, y=1.045,
        buttons=[dict(label="Hele båden", method="relayout", args=[overview]),
                 dict(label="Detalje ved 4 m", method="relayout", args=[detail]), *buttons])]
    if info.get("sides") == "both":
        side_buttons = [dict(label=label, method="restyle", args=[{"visible": [
            entry is None or selection is None or entry == selection for entry in visibility_sides]}])
            for label, selection in (("Begge sider", None), ("Kun læside", "leeward"), ("Kun luvside", "windward"))]
        fig.layout.updatemenus = [*fig.layout.updatemenus,
                                  dict(type="buttons", direction="right", x=.98, xanchor="right", y=1.045, buttons=side_buttons)]
    prefix = cache_prefix(info.get("sides", "leeward"))
    fig.write_html(output / (prefix + ".html"), include_plotlyjs=True,
                   config={"responsive": True, "displaylogo": False, "scrollZoom": True}, default_height="95vh")
    save_png(surfaces, info, arrays, output / (prefix + ".png"))


def save_png(surfaces, info, arrays, target):
    import pyvista as pv

    plot = pv.Plotter(off_screen=True, window_size=(1800, 1450), shape=(1, 2), border=False)
    plot.set_background("#f6f8fa", all_renderers=True)
    for panel in (0, 1):
        plot.subplot(0, panel)
        for name, points, faces, outlines in surfaces:
            mesh = pv.PolyData(points, np.column_stack((np.full(len(faces), 3), faces)).ravel())
            plot.add_mesh(mesh, color=COLORS[name], opacity=.14, smooth_shading=False)
            for line in outlines:
                plot.add_lines(line, color=COLORS[name], width=1, connected=True)
        for key, statistics in info["sails"].items():
            color, label, side = region_style(key, statistics)
            points, faces = arrays[key + "_points"], arrays[key + "_triangles"]
            if len(faces):
                mesh = pv.PolyData(points, np.column_stack((np.full(len(faces), 3), faces)).ravel())
                plot.add_mesh(mesh, color=color, opacity=1, smooth_shading=True,
                              ambient=.65, diffuse=.35)
        plot.enable_parallel_projection()
        plot.enable_depth_peeling(number_of_peels=80)
        if panel == 0:
            plot.camera_position = [(-14, 24, 12), (0, 0, 5.5), (0, 0, 1)]
            plot.camera.parallel_scale = 7.8
            plot.add_text("Gennemsigtig båd og sejl", position=(25, 1380), font_size=16, color="#253b49")
            color_note = {"both": "Rød/orange: læside · blå/turkis: luvside", "leeward": "Rød/orange: læside",
                          "windward": "Blå/turkis: luvside"}[info.get("sides", "leeward")]
            plot.add_text(color_note, position=(25, 80), font_size=11, color="#76542e")
            status = "Kontrolkrav opfyldt" if info["checks_passed"] else "Foreløbigt resultat · samlede kontrolkrav ikke opfyldt"
            plot.add_text(status, position=(25, 35), font_size=10, color="#536774")
        else:
            plot.camera_position = [(-.8, .3, 4.08), (-.025, .014, 4), (0, 0, 1)]
            plot.camera.parallel_scale = .12
            plot.add_text("Storsejlets læside ved 4 m", position=(25, 1380), font_size=14, color="#253b49")
            plot.add_text(f"Indikator på separation: u_t < −{info['reverse_speed_m_s']:g} m/s",
                          position=(25, 80), font_size=11, color="#76542e")
            plot.add_text("Korrekt målestok · billedhøjde 24 cm", position=(25, 35), font_size=10, color="#536774")
        plot.reset_camera_clipping_range()
    plot.screenshot(target)
    plot.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, default=Path("cfd/cases/marine_3d_padded_5ms"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--reverse-speed", type=float, default=.05, help="Minimum reverse tangent velocity magnitude, m/s")
    parser.add_argument("--sides", choices=("leeward", "windward", "both"), default="leeward")
    parser.add_argument("--from-cache", action="store_true", help="Render compact surfaces without reading the full CFD field")
    args = parser.parse_args()
    output = args.output or result_dir(args.case) / "visualizations"
    prefix = cache_prefix(args.sides)
    if args.from_cache:
        info = json.loads((output / (prefix + ".json")).read_text())
        with np.load(output / (prefix + ".npz"), allow_pickle=False) as saved:
            arrays = dict(saved)
    else:
        info, arrays = prepare(args.case, output, args.reverse_speed, args.sides)
    render(info, arrays, output)
    print(f"3D visualization: {output / (prefix + '.html')}", flush=True)


if __name__ == "__main__":
    main()
