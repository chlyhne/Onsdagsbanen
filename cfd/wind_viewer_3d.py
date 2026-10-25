"""Offline red/blue 3D wind viewer with the saved 2D plot's VMG calibration."""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

import numpy as np

from cfd.marine_rans import read_profile
from cfd.plot_data import load_plot_data
from cfd.plot_geometry_3d import geometry_surfaces, interactive_figure
from cfd.plot_wind import (VMG_FACTOR, HEADER_WEIGHT_KNOTS_PER_DEG, KNOTS_PER_M_S,
                           delta_vmg, distance_from_boat)
from cfd.separation_3d import sha
from cfd.storage import case_file
from cfd.volume_data import native_velocity, sample_grid


FIELDS = ("goodness", "speed", "direction")


def calibration(scales):
    common = scales["godhed.png"]
    for key, expected in (("vmg_factor", VMG_FACTOR), ("header_weight_knots_per_deg", HEADER_WEIGHT_KNOTS_PER_DEG),
                          ("speed_conversion_knots_per_m_s", KNOTS_PER_M_S)):
        if not np.isclose(common[key], expected, rtol=1e-12):
            raise ValueError("Saved 2D calibration differs from current VMG formula: " + key)
    return {"minimum": common["vmin"], "maximum": common["vmax"],
            "white_band": common["white_band_relative_to_median_knots"][1],
            "speed_offset_m_s": scales["vindhastighed.png"]["subtracted_offset"],
            "angle_offset_deg": scales["vinddrejning.png"]["subtracted_offset"],
            "goodness_offset_knots": common["subtracted_offset"]}


def field_scores(points, velocity, profile, reference):
    """Same nonlinear wind quantities and centering as plot_wind, in knots.

    Background wind and presentation offsets may follow height. The exact
    saved 4 m medians are retained at the reference height.
    """
    baseline = np.asarray(profile.at(np.asarray(points)[:, 2])["apparent_velocity_m_s"])
    velocity = np.asarray(velocity)
    speed_delta = np.linalg.norm(velocity, axis=1) - np.linalg.norm(baseline, axis=1)
    angle = np.degrees(np.arctan2(baseline[:, 0] * velocity[:, 1] - baseline[:, 1] * velocity[:, 0],
                                 np.sum(baseline[:, :2] * velocity[:, :2], axis=1)))
    # Direction is undefined in near-stagnant cells, as in the saved 2D plots.
    angle[np.linalg.norm(velocity[:, :2], axis=1) < .1 * np.linalg.norm(baseline, axis=1)] = np.nan
    centered_angle = (angle - reference["angle_offset_deg"] + 180) % 360 - 180
    return {"goodness": delta_vmg(speed_delta, angle) - reference["goodness_offset_knots"],
            "speed": delta_vmg(speed_delta - reference["speed_offset_m_s"], 0.),
            "direction": delta_vmg(0., centered_angle)}


def height_references(axes, raw_scores, hull, reference, reference_height):
    """Uniform-area XY cell medians, anchored to the exact saved 4 m offsets."""
    nx, ny, nz = map(len, axes)
    x, y = np.meshgrid((axes[0][:-1] + axes[0][1:]) / 2, (axes[1][:-1] + axes[1][1:]) / 2)
    exterior = distance_from_boat(np.column_stack((x.ravel(), y.ravel())), hull).reshape(x.shape) >= 1.
    values = {"speed_offset_m_s": raw_scores["speed"] / (VMG_FACTOR * KNOTS_PER_M_S),
              "angle_offset_deg": -raw_scores["direction"] / (VMG_FACTOR * HEADER_WEIGHT_KNOTS_PER_DEG),
              "goodness_offset_knots": raw_scores["goodness"]}
    medians = {}
    for name, value in values.items():
        grid = value.reshape(nz, ny, nx)
        centers = (grid[:, :-1, :-1] + grid[:, 1:, :-1] + grid[:, :-1, 1:] + grid[:, 1:, 1:]) / 4
        median = np.nanmedian(np.where(exterior, centers, np.nan).reshape(nz, -1), axis=1)
        if not np.isfinite(median).all():
            raise ValueError("No valid exterior area for a height's neutral reference")
        anchor = np.interp(reference_height, axes[2], median)
        medians[name] = median - anchor + reference[name]
    return medians


def iso_meshes(axes, scores, reference):
    """Native-valued contour surfaces; exclude cells touching missing samples."""
    import pyvista as pv

    nx, ny, nz = map(len, axes)
    spacing = [float(a[1] - a[0]) for a in axes]
    image = pv.ImageData(dimensions=(nx, ny, nz), spacing=spacing, origin=[a[0] for a in axes])
    limit = reference["maximum"]
    magnitudes = sorted(set(v for v in (.025, .05, .1, .2, .4, limit) if v <= limit))
    levels = [-v for v in reversed(magnitudes)] + magnitudes
    result = {}
    for field, values in scores.items():
        valid = np.isfinite(values).reshape(nz, ny, nx)
        valid_cells = np.ones((nz - 1, ny - 1, nx - 1), dtype=bool)
        for k in (0, 1):
            for j in (0, 1):
                for i in (0, 1):
                    valid_cells &= valid[k:k + nz - 1, j:j + ny - 1, i:i + nx - 1]
        image["score"] = values
        grid = image.extract_cells(np.flatnonzero(valid_cells.ravel()))
        surface = grid.contour(levels, scalars="score").triangulate()
        result[field + "_iso_points"] = np.asarray(surface.points).astype(np.float32)
        result[field + "_iso_faces"] = surface.faces.reshape(-1, 4)[:, 1:].astype(np.int32)
        result[field + "_iso_values"] = np.asarray(surface["score"]).astype(np.float32)
        print(f"{field}: {surface.n_cells:,} contour triangles", flush=True)
    return result, levels


def palette(reference):
    low, high, white = reference["minimum"], reference["maximum"], reference["white_band"]
    left = float(np.clip((-white - low) / (high - low), 0, 1))
    right = float(np.clip((white - low) / (high - low), 0, 1))
    return [[0., "#67001f"], [left / 2, "#ef8a62"], [left, "#ffffff"],
            [right, "#ffffff"], [(1 + right) / 2, "#67a9cf"], [1., "#053061"]]


def prepare(case, snapshot, output, spacing=.5):
    case, snapshot, output = Path(case), Path(snapshot), Path(output)
    scales = json.loads(case_file(snapshot, "plot_scales.json").read_text())
    ref = calibration(scales)
    context, plane = load_plot_data(snapshot)
    mesh, velocity, manifest, geometry = native_velocity(case)
    if geometry != json.loads(case_file(snapshot, "geometry.json").read_text()):
        raise ValueError("2D snapshot geometry differs from the 3D case")
    if not np.isclose(context["wind"]["native_reference_speed_m_s"], manifest["native_reference_speed_m_s"]):
        raise ValueError("2D snapshot velocity reference differs from the 3D case")
    print("Sampling the original 3D fluid cells", flush=True)
    # Same horizontal viewport as the 2D plot: plot coordinates are (-y,x).
    bounds = [[-50., 15.], [-30., 40.], [.5, 15.5]]
    axes, points, sampled, valid = sample_grid(mesh, velocity, bounds, spacing)
    del mesh, velocity
    profile = read_profile(manifest)
    zero = {"speed_offset_m_s": 0., "angle_offset_deg": 0., "goodness_offset_knots": 0.}
    raw_scores = field_scores(points, sampled, profile, zero)
    offsets = height_references(axes, raw_scores, geometry["hull"], ref, context["height_m"])
    point_ref = {key: np.repeat(value, len(axes[0]) * len(axes[1])) for key, value in offsets.items()}
    scores = field_scores(points, sampled, profile, point_ref)
    contours, levels = iso_meshes(axes, scores, ref)
    plane_points = np.column_stack((plane["xy"], np.full(len(plane["xy"]), context["height_m"])))
    plane_scores = field_scores(plane_points, plane["velocity"], profile, ref)
    # Confirm that recalculation at 4 m reproduces the exact cached wind map.
    expected = {
        "goodness": delta_vmg(plane["speed_delta"], plane["angle_deg"]) - ref["goodness_offset_knots"],
        "speed": delta_vmg(plane["speed_delta"] - ref["speed_offset_m_s"], 0.),
        "direction": delta_vmg(0., (plane["angle_deg"] - ref["angle_offset_deg"] + 180) % 360 - 180)}
    errors = {}
    for field in FIELDS:
        finite = np.isfinite(expected[field]) & np.isfinite(plane_scores[field])
        errors[field] = float(np.max(np.abs(expected[field][finite] - plane_scores[field][finite])))
        if errors[field] > 1e-9:
            raise ValueError("Recalculated field differs from the saved 2D map: " + field)
    saved_report = json.loads(case_file(case, "report.json").read_text())
    info = {"case": str(case), "snapshot": str(snapshot), "solver_run": False,
            "geometry": geometry, "calibration": ref, "palette": palette(ref),
            "bounds_m": bounds, "spacing_m": spacing, "dimensions": [len(a) for a in axes],
            "ordering": "x fastest, then y, then z", "valid_grid_points": int(valid.sum()),
            "reference_height_m": context["height_m"], "matching_2d_max_error_knots": errors,
            "background_reference": "Height-dependent prescribed wind profile",
            "presentation_zero": "Height-dependent uniform-area XY-cell medians outside the 1 m hull neighbourhood, anchored to exact 2D medians at 4 m",
            "height_offsets": {key: value.tolist() for key, value in offsets.items()},
            "contour_levels_knots": levels,
            "method": "Velocity interpolated within original hybrid fluid cells, then speed/direction/VMG calculated; no nearest-point filling or extrusion",
            "iteration": saved_report["last_history"]["Inner_Iter"], "checks_passed": saved_report["checks_passed"],
            "source_sha256": {name: sha(case_file(case, name)) for name in ("geometry.json", "yacht.msh", "restart.dat")},
            "snapshot_sha256": {name: sha(case_file(snapshot, name)) for name in ("plot_scales.json", "slice.npz", "plot_context.json")}}
    arrays = {"x": axes[0], "y": axes[1], "z": axes[2], "valid": valid,
              "plane_points": plane_points, "plane_triangles": plane["triangles"],
              **{field: value.astype(np.float32) for field, value in scores.items()},
              **{"plane_" + field: value.astype(np.float32) for field, value in plane_scores.items()}, **contours}
    output.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(output / "wind_3d.npz", **arrays)
    (output / "wind_3d.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    print(f"Cached {len(points):,} grid points; exact 2D agreement: {errors}", flush=True)
    return info, arrays


def encode(values, dtype="<f4"):
    return base64.b64encode(np.ascontiguousarray(values, dtype=dtype).tobytes()).decode("ascii")


def render(info, arrays, output):
    from plotly.offline import get_plotlyjs
    import plotly.io as pio

    figure = interactive_figure(geometry_surfaces(info["geometry"]))
    geometry_traces = json.loads(pio.to_json(figure))["data"]
    for trace in geometry_traces:
        trace["showlegend"] = False
        if trace["type"] == "mesh3d":
            trace["opacity"] = .24
    payload = {"info": info, "geometry": geometry_traces,
               "axes": {key: arrays[key].tolist() for key in ("x", "y", "z")},
               "fields": {field: encode(arrays[field]) for field in FIELDS},
               "isosurfaces": {field: {"points": encode(arrays[field + "_iso_points"]),
                                       "faces": encode(arrays[field + "_iso_faces"], "<i4"),
                                       "values": encode(arrays[field + "_iso_values"])} for field in FIELDS},
               "planeFields": {field: encode(arrays["plane_" + field]) for field in FIELDS},
               "planePoints": encode(arrays["plane_points"]),
               "planeTriangles": encode(arrays["plane_triangles"], "<i4")}
    template = (Path(__file__).parent / "viewer_templates" / "wind_3d.html").read_text()
    html = template.replace("__PLOTLY_JS__", get_plotlyjs()).replace(
        "__PAYLOAD__", json.dumps(payload, separators=(",", ":")).replace("<", "\\u003c"))
    Path(output).mkdir(parents=True, exist_ok=True)
    (Path(output) / "wind_3d.html").write_text(html, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, default=Path("cfd/cases/marine_3d_padded_5ms"))
    parser.add_argument("--snapshot", type=Path, default=Path("cfd/results/v1_5ms"))
    parser.add_argument("--output", type=Path, default=Path("cfd/results/v1_5ms/visualizations"))
    parser.add_argument("--spacing", type=float, default=.5, help="Display grid spacing in metres")
    parser.add_argument("--from-cache", action="store_true")
    args = parser.parse_args()
    if args.spacing <= 0 or not np.isfinite(args.spacing):
        parser.error("spacing must be positive and finite")
    if args.from_cache:
        info = json.loads((args.output / "wind_3d.json").read_text())
        with np.load(args.output / "wind_3d.npz", allow_pickle=False) as saved:
            arrays = dict(saved)
    else:
        info, arrays = prepare(args.case, args.snapshot, args.output, args.spacing)
    render(info, arrays, args.output)
    print(f"Viewer ready: {args.output / 'wind_3d.html'}", flush=True)


if __name__ == "__main__":
    main()
