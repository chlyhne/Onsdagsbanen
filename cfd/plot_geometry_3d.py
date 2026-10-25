"""Transparent geometry preview from a saved 3D case; never runs CFD.

This deliberately does not infer a separation volume from a horizontal slice.
The original volume solution is required before such a volume can be plotted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from cfd.rans_mesh import FOOT, section
from cfd.storage import case_file, result_dir


COLORS = {"Skrog": "#578ea9", "Mast": "#536774",
          "Storsejl": "#60a4bb", "Forsejl": "#b49a68"}
NOTE = ("Geometrivisning · ingen separationsblob. "
        "Blobben kræver det oprindelige 3D-strømningsfelt.")


def grid_triangles(rows, columns):
    a = np.arange((rows - 1) * columns).reshape(rows - 1, columns)[:, :-1].ravel()
    return np.vstack((np.column_stack((a, a + 1, a + columns)),
                      np.column_stack((a + 1, a + columns + 1, a + columns))))


def geometry_surfaces(geometry):
    """Saved sails as thin membranes; hull and mast use the original CAD sizes."""
    if geometry.get("geometry_version") != 4:
        raise ValueError("This preview requires the saved version 4 3D geometry")
    surfaces = []
    outline = np.asarray(geometry["hull"], dtype=float)
    n = len(outline)
    points = np.vstack([np.column_stack((outline, np.full(n, z))) for z in (-.35, .85)])
    faces = []
    for i in range(n):
        j = (i + 1) % n
        faces.extend(((i, j, n + i), (j, n + j, n + i)))
    # The original hull polygon is convex, so these fans preserve its caps.
    for i in range(1, n - 1):
        faces.extend(((0, i + 1, i), (n, n + i, n + i + 1)))
    outlines = [np.vstack((points[:n], points[0])),
                np.vstack((points[n:], points[n]))]
    surfaces.append(("Skrog", points, np.asarray(faces), outlines))

    angle = np.linspace(0, 2 * np.pi, 33)
    mast = np.array([np.column_stack((.15 + .075 * np.cos(angle),
                                     .075 * np.sin(angle), np.full(len(angle), z)))
                     for z in (.5, 12.)])
    surfaces.append(("Mast", mast.reshape(-1, 3), grid_triangles(*mast.shape[:2]), []))

    names = {"main": "Storsejl", "jib": "Forsejl"}
    for sail, shape in zip(geometry["sail_definitions"], geometry["sail_shapes"], strict=True):
        # Average the exact two skins rather than inventing a planar triangle.
        # Thickness (~0.1% chord) is omitted only for transparent rendering.
        rows = []
        for height in np.linspace(FOOT, FOOT + sail[2], 49):
            skin = section(sail, height, shape, n=65)
            rows.append((skin[:65] + skin[65:][::-1]) / 2)
        grid = np.asarray(rows)
        boundary = np.vstack((grid[0], grid[1:, -1], grid[-1, -2::-1],
                              grid[-2::-1, 0]))
        surfaces.append((names[shape["name"]], grid.reshape(-1, 3),
                         grid_triangles(*grid.shape[:2]), [boundary]))
    return surfaces


def interactive_figure(surfaces):
    import plotly.graph_objects as go

    fig = go.Figure()
    for name, points, faces, outlines in surfaces:
        fig.add_trace(go.Mesh3d(
            x=points[:, 0], y=points[:, 1], z=points[:, 2],
            i=faces[:, 0], j=faces[:, 1], k=faces[:, 2], name=name,
            legendgroup=name, showlegend=True, color=COLORS[name], opacity=.24,
            hovertemplate=name + "<br>x=%{x:.2f} m<br>y=%{y:.2f} m<br>z=%{z:.2f} m<extra></extra>",
            lighting=dict(ambient=.7, diffuse=.6, specular=.1)))
        for line in outlines:
            fig.add_trace(go.Scatter3d(
                x=line[:, 0], y=line[:, 1], z=line[:, 2], mode="lines",
                line=dict(color=COLORS[name], width=3), opacity=.8,
                legendgroup=name, showlegend=False, hoverinfo="skip"))
    cameras = {
        "Skråt": dict(x=-1.5, y=2.2, z=.9),
        "Fra siden": dict(x=0, y=2.7, z=.15),
        "Agterfra": dict(x=-2.7, y=0, z=.15),
        "Ovenfra": dict(x=0, y=0, z=2.7),
    }
    fig.update_layout(
        title=dict(text="Gennemsigtig båd og sejl · eksisterende 3D-geometri", x=.04),
        font=dict(family="Arial", color="#253b49"), paper_bgcolor="#f6f8fa",
        margin=dict(l=0, r=0, t=100, b=80),
        scene=dict(aspectmode="data", bgcolor="#f6f8fa",
                   xaxis=dict(title="x · mod stævnen [m]", range=[-5.7, 5.7]),
                   yaxis=dict(title="y · bagbord [m]", range=[-2.7, 2.7]),
                   zaxis=dict(title="Højde [m]", range=[-.6, 12.3]),
                   camera=dict(eye=cameras["Skråt"], projection=dict(type="orthographic"))),
        legend=dict(orientation="h", x=.04, y=1.02, groupclick="togglegroup"),
        updatemenus=[dict(type="buttons", direction="right", x=.54, y=1.06,
                         buttons=[dict(label=label, method="relayout",
                                       args=[{"scene.camera.eye": eye}])
                                  for label, eye in cameras.items()])],
        annotations=[dict(text=NOTE + "<br>Træk for at dreje · rul for at zoome.",
                          xref="paper", yref="paper", x=.02, y=-.07,
                          showarrow=False, align="left", font=dict(size=13))])
    return fig


def save_png(surfaces, target):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    fig = plt.figure(figsize=(10, 9), facecolor="#f6f8fa")
    ax = fig.add_subplot(projection="3d", facecolor="#f6f8fa")
    for name, points, faces, outlines in surfaces:
        ax.add_collection3d(Poly3DCollection(
            points[faces], facecolor=COLORS[name], alpha=.22, edgecolor="none",
            linewidth=0, zsort="average"))
        for line in outlines:
            ax.plot(*line.T, color=COLORS[name], linewidth=.8, alpha=.85)
    ax.set(xlim=(-5.7, 5.7), ylim=(-2.7, 2.7), zlim=(-.6, 12.3),
           xlabel="Mod stævnen [m]", ylabel="Bagbord [m]", zlabel="Højde [m]")
    ax.set_box_aspect((11.4, 5.4, 12.9))
    ax.set_proj_type("ortho")
    ax.view_init(elev=15, azim=125)
    for axis in (ax.xaxis, ax.yaxis, ax.zaxis):
        axis.pane.fill = False
        axis._axinfo["grid"]["color"] = (.6, .67, .72, .15)
    fig.suptitle("Gennemsigtig båd og sejl", fontsize=20, color="#253b49", y=.95)
    fig.text(.5, .90, "Sejlenes krumning og twist fra den gemte 3D-geometri",
             ha="center", fontsize=11, color="#536774")
    fig.text(.5, .065, "Geometrivisning · ingen separationsblob",
             ha="center", fontsize=11, color="#76542e")
    fig.text(.5, .038, "En 3D-blob kræver det oprindelige 3D-strømningsfelt.",
             ha="center", fontsize=10, color="#536774")
    fig.savefig(target, dpi=180, facecolor=fig.get_facecolor())
    plt.close(fig)


def render(case, output=None):
    case = Path(case)
    geometry_path = case_file(case, "geometry.json")
    surfaces = geometry_surfaces(json.loads(geometry_path.read_text(encoding="utf-8")))
    output = Path(output) if output else result_dir(case) / "visualizations"
    output.mkdir(parents=True, exist_ok=True)
    interactive_figure(surfaces).write_html(
        output / "geometry_3d.html", include_plotlyjs=True,
        config={"responsive": True, "displaylogo": False, "scrollZoom": True},
        default_height="95vh")
    save_png(surfaces, output / "geometry_3d.png")
    metadata = {
        "kind": "geometry_preview", "separation_volume_shown": False,
        "reason": "Geometry only; no flow field read. A slice cannot define a 3D separation volume",
        "geometry_source": str(geometry_path),
        "geometry_sha256": hashlib.sha256(geometry_path.read_bytes()).hexdigest(),
        "rendering": "Saved curved, twisted sail midsurfaces; original extruded hull and mast",
        "solver_run": False,
    }
    (output / "geometry_3d.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, default=Path("cfd/results/v1_5ms"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = render(args.case, args.output)
    print(f"Geometry preview: {output / 'geometry_3d.html'}")
    print("No separation volume: the original 3D flow field is required.")


if __name__ == "__main__":
    main()
