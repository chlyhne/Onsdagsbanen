"""Chord-weighted vertical average of the saved 3D wind layers.

F_bar(x,y) = integral F(x,y,z) * [c_main(z)+c_jib(z)] dz /
             integral [c_main(z)+c_jib(z)] dz.

The saved field is interpolated linearly between heights. Two-point Gauss
quadrature integrates its product with each linear chord exactly, splitting
at every sampled height and sail foot/head. No CFD or new centering runs.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np

from cfd.case import case_section
from cfd.plot_wind import DPI, FIGURE_SIZE, FIELD_FILES, VMG_CONTOUR_STEP_KNOTS, top_view
from cfd.rans_mesh import FOOT
from cfd.separation_3d import sha
from cfd.storage import case_file
from cfd.wind_viewer_3d import FIELDS, palette


def sail_chords(geometry, heights):
    """Straight local chord of every sail; zero below the foot/above its head."""
    heights = np.asarray(heights, dtype=float)
    result = []
    for _, chord, span, _ in geometry["sail_definitions"]:
        f = (heights - FOOT) / span
        result.append(np.where((f >= 0) & (f <= 1), chord * (1 - .96 * f), 0.))
    return np.stack(result, axis=-1)


def height_quadrature(geometry, source_z):
    source_z = np.asarray(source_z, dtype=float)
    head = max(FOOT + sail[2] for sail in geometry["sail_definitions"])
    if source_z.ndim != 1 or len(source_z) < 2 or np.any(np.diff(source_z) <= 0):
        raise ValueError("Source heights must be strictly increasing")
    if source_z[0] > FOOT or source_z[-1] < head:
        raise ValueError("Saved 3D layers must cover the full sail height; no extrapolation")
    cuts = np.unique(np.r_[FOOT, head, source_z[(source_z > FOOT) & (source_z < head)],
                           [FOOT + sail[2] for sail in geometry["sail_definitions"]]])
    lower, upper = cuts[:-1], cuts[1:]
    mid, half = (lower + upper) / 2, (upper - lower) / 2
    heights = np.column_stack((mid - half / np.sqrt(3), mid + half / np.sqrt(3))).ravel()
    dz = np.repeat(half, 2)
    chords = sail_chords(geometry, heights)
    weights = chords.sum(axis=1) * dz
    return heights, dz, chords, weights


def average_layers(values, source_z, heights, weights):
    """Average scores, preserving undefined columns instead of filling zeros."""
    values = np.asarray(values, dtype=float)
    if values.shape[0] != len(source_z):
        raise ValueError("Layer data shape does not match source heights")
    numerator = np.zeros(values.shape[1:], dtype=float)
    covered = np.zeros_like(numerator)
    total = float(np.sum(weights))
    if total <= 0:
        raise ValueError("No positive sail weights")
    for z, weight in zip(heights, weights, strict=True):
        if weight <= 0:
            continue
        if z < source_z[0] or z > source_z[-1]:
            raise ValueError("Cannot extrapolate outside saved heights")
        high = int(np.clip(np.searchsorted(source_z, z, side="right"), 1, len(source_z) - 1))
        low = high - 1
        f = (z - source_z[low]) / (source_z[high] - source_z[low])
        layer = values[low] if f <= 1e-14 else values[high] if f >= 1 - 1e-14 else (1 - f) * values[low] + f * values[high]
        valid = np.isfinite(layer)
        numerator += np.where(valid, layer, 0.) * weight
        covered += valid * weight
    coverage = covered / total
    # A full-column average must include the entire active sail-height range.
    # Missing active layers stay masked; their weights are never redistributed.
    average = np.where(np.isclose(coverage, 1., rtol=0., atol=1e-10), numerator / total, np.nan)
    return average, coverage


def prepare(source, output):
    source, output = Path(source), Path(output)
    info = json.loads((source / "wind_3d.json").read_text())
    geometry = info["geometry"]
    context = json.loads(case_file(info["snapshot"], "plot_context.json").read_text())
    with np.load(source / "wind_3d.npz", allow_pickle=False) as saved:
        x, y, z = (saved[key] for key in ("x", "y", "z"))
        heights, dz, chords, weights = height_quadrature(geometry, z)
        arrays = {"x": x, "y": y, "source_z": z, "quadrature_z": heights,
                  "quadrature_dz": dz, "sail_chords_m": chords, "area_weights_m2": weights}
        for field in FIELDS:
            values = saved[field].reshape(len(z), len(y), len(x))
            arrays[field], arrays[field + "_coverage"] = average_layers(values, z, heights, weights)
    sail_areas = np.sum(chords * dz[:, None], axis=0)
    expected_areas = np.array([.52 * sail[1] * sail[2] for sail in geometry["sail_definitions"]])
    if not np.allclose(sail_areas, expected_areas, rtol=1e-12, atol=1e-12):
        raise ValueError("Height weights do not reproduce the analytic chord areas")
    total_area = float(weights.sum())
    metadata = {"source": str(source), "solver_run": False, "geometry": geometry,
                "formula": "integral F(x,y,z)*(c_main(z)+c_jib(z)) dz / integral (c_main(z)+c_jib(z)) dz",
                "averaged_quantity": "The displayed VMG score of each layer, before color mapping; not velocity vectors or RGB colors",
                "height_range_m": [FOOT, max(FOOT + s[2] for s in geometry["sail_definitions"])],
                "vertical_interpolation": "Linear between saved 3D layers",
                "quadrature": "Two-point Gauss on intervals split at sampled heights and every sail foot/head",
                "total_chord_area_m2": total_area,
                "sail_chord_areas_m2": {shape["name"]: float(area) for shape, area in zip(geometry["sail_shapes"], sail_areas, strict=True)},
                "weighted_mean_height_m": float(np.sum(weights * heights) / total_area),
                "calibration": info["calibration"], "palette": info["palette"],
                "centering": "No new median subtracted; average of the viewer's height-centered layer values",
                "missing_data": "Mask columns with any missing active height weight; no zero filling or redistribution",
                "sail_outline_reference_height_m": info["reference_height_m"],
                "true_wind_angle_deg": context["wind"]["true_angle_deg"],
                "iteration": info["iteration"], "checks_passed": info["checks_passed"],
                "source_sha256": {name: sha(source / name) for name in ("wind_3d.json", "wind_3d.npz")}}
    output.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(output / "weighted_wind.npz", **arrays)
    (output / "weighted_wind.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    with (output / "height_weights.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["height_m", "dz_quadrature_m", *[s["name"] + "_chord_m" for s in geometry["sail_shapes"]],
                         "total_chord_m", "area_weight_m2", "normalized_weight"])
        for height, step, lengths, weight in zip(heights, dz, chords, weights, strict=True):
            writer.writerow([height, step, *lengths, sum(lengths), weight, weight / total_area])
    print(json.dumps({key: metadata[key] for key in ("height_range_m", "total_chord_area_m2", "sail_chord_areas_m2", "weighted_mean_height_m")}, indent=2), flush=True)
    return metadata, arrays


def render(metadata, arrays, target, field="goodness", *, title=None, english=False, percentage=None):
    if percentage is None:
        percentage = metadata.get('display_units') == 'percent'
    if percentage:
        english = True
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patheffects as path_effects
    from matplotlib.colors import LinearSegmentedColormap, Normalize
    from matplotlib.patches import Polygon
    from matplotlib.ticker import FuncFormatter

    ref = metadata["calibration"]
    cmap = LinearSegmentedColormap.from_list("same_2d_scale", palette(ref), N=4097)
    norm = Normalize(ref["minimum"], ref["maximum"], clip=True)
    x, y = np.meshgrid(arrays["x"], arrays["y"])
    px, py = -y, x
    titles = {"goodness": "Ændring i VMG", "speed": "Vindhastighed", "direction": "Skræller og rummer"}
    score = np.ma.masked_invalid(arrays[field])
    fig, ax = plt.subplots(figsize=FIGURE_SIZE, layout="constrained")
    image = ax.pcolormesh(px, py, score, shading="gouraud", cmap=cmap, norm=norm)
    if Path(target).suffix.lower() == '.svg':
        image.set_rasterized(True)
    ax.contourf(px, py, score, levels=[-ref["white_band"], ref["white_band"]], colors=["white"], zorder=2)
    contour_step = metadata.get('contour_step', 2. if percentage else VMG_CONTOUR_STEP_KNOTS)
    levels = np.arange(contour_step, ref["maximum"] + 1e-12, contour_step)
    levels = np.r_[-levels[::-1], levels]
    active = levels[(levels > score.min()) & (levels < score.max())]
    if len(active):
        contours = ax.contour(px, py, score, levels=active,
                              colors=["#a44b42" if v < 0 else "#427b9b" for v in active], linewidths=.7, zorder=3)
        contours.set_path_effects([path_effects.withStroke(linewidth=1.4, foreground="white")])
        ax.clabel(contours, fmt=lambda v: f"{v:+.0f}%" if percentage else f"{v:+.2f} knots" if english else f"{v:+.2f} knob".replace(".", ","), fontsize=9, inline=True)
    geometry = metadata["geometry"]
    hull = np.asarray(geometry["hull"])
    ax.add_patch(Polygon(top_view(hull), facecolor="#b8b8b8", edgecolor="#142b3b", linewidth=1.7, zorder=5,
                         path_effects=[path_effects.withStroke(linewidth=3.5, foreground="white")]))
    for index in range(len(geometry["sail_definitions"])):
        section = case_section(geometry, index, metadata["sail_outline_reference_height_m"])
        ax.add_patch(Polygon(top_view(section), facecolor="black", edgecolor="black", linewidth=1.3, zorder=7,
                             path_effects=[path_effects.withStroke(linewidth=2.7, foreground="white")]))
    # Same direction and placement as the existing map's true-wind arrow.
    twa = np.radians(metadata["true_wind_angle_deg"])
    ax.quiver(23, 5, -7 * np.sin(twa), -7 * np.cos(twa), angles="xy", scale_units="xy", scale=1, color="black", zorder=6)
    ax.text(13, -4, "True wind" if english else "Sand vind", fontsize=15)
    bar = fig.colorbar(image, ax=ax, orientation="horizontal", fraction=.04, pad=.025, shrink=.8,
                       ticks=np.linspace(norm.vmin, norm.vmax, 5))
    bar.ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: ("0" if abs(v) < 1e-10 else f"{v:+.2f}") if english else ("0" if abs(v) < 1e-10 else f"{v:+.2f}").replace(".", ",")))
    bar.ax.tick_params(labelsize=14, length=0, pad=8)
    bar.set_label("Estimated change in VMG [%] · red = worse · blue = better" if percentage else "Estimated change in VMG [knots] · red = worse · blue = better" if english else "Ændring i VMG [knob] · rød = dårligere · blå = bedre", fontsize=15, labelpad=10)
    bar.outline.set_visible(False)
    ax.set(xlim=(-40, 30), ylim=(-50, 15), aspect="equal")
    ax.set_title(title or f"{titles[field]} · vægtet over sejlhøjden", fontsize=22, pad=12)
    ax.set_axis_off()
    fig.canvas.draw()
    fig.savefig(target, dpi=DPI)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("cfd/results/v1_5ms/visualizations"))
    parser.add_argument("--output", type=Path, default=Path("cfd/results/v1_5ms/weighted_2d"))
    parser.add_argument("--from-cache", action="store_true")
    args = parser.parse_args()
    if args.from_cache:
        metadata = json.loads((args.output / "weighted_wind.json").read_text())
        with np.load(args.output / "weighted_wind.npz", allow_pickle=False) as saved:
            arrays = dict(saved)
    else:
        metadata, arrays = prepare(args.source, args.output)
    for field in FIELDS:
        target = args.output / FIELD_FILES[field]
        render(metadata, arrays, target, field=field)
        print(f"Saved combined 2D map: {target}", flush=True)


if __name__ == "__main__":
    main()
