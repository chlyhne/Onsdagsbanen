"""Add a resolved sea and distant atmosphere to an existing fine boat mesh.

No sail nodes are moved. The original flat sea becomes an internal layer
interface: a monotone vertical map makes room below it, inside the hull's
vertical-sided part. Extrusion fills that space with conforming prisms/hexes.
"""
from collections import defaultdict
import json
from pathlib import Path

import numpy as np

MARKERS = {"sea": 11, "hull": 12, "main": 13, "jib": 14,
           "inlet_xmax": 15, "inlet_ymin": 16,
           "outlet_xmin": 17, "outlet_ymax": 18, "atmosphere_top": 19}
VOLUME = {"tetra": 10, "wedge": 13, "hexahedron": 12, "pyramid": 14}


def plane_tag(points, lower, upper):
    for axis, value, name in ((0, lower[0], "outlet_xmin"), (0, upper[0], "inlet_xmax"),
                              (1, lower[1], "inlet_ymin"), (1, upper[1], "outlet_ymax"),
                              (2, upper[2], "atmosphere_top")):
        if np.allclose(points[:, axis], value, atol=1e-8, rtol=0):
            return MARKERS[name]
    raise ValueError("Outer boundary face is not on a box plane")


def _surface_edges(blocks):
    edges = np.concatenate([np.stack((b, np.roll(b, -1, axis=1)), axis=-1).reshape(-1, 2)
                            for b in blocks if len(b)])
    unique, count = np.unique(np.sort(edges, axis=1), axis=0, return_counts=True)
    if np.any(count > 2):
        raise ValueError("Surface patch has nonmanifold edges")
    return unique[count == 1]


def augment(mesh, first=1.2e-5, layers=45, growth=1.18, transition_height=.8, top_height=84., top_layers=20):
    import meshio
    old = np.asarray(mesh.points)
    lower, upper = old.min(axis=0), old.max(axis=0)
    heights = first * growth ** np.arange(layers)
    total = float(heights.sum())
    if lower[2] < -1e-8 or not 0 < total < transition_height / 2 or top_height <= upper[2]:
        raise ValueError("Invalid sea-layer/transition/top dimensions")
    # Ensure the source hull is vertical wherever the monotone map acts.
    # For the current model, its deck is at z=.85 and sails start at z=1.15.
    points = old.copy()
    z = np.maximum(points[:, 2], 0.)
    mask = z < transition_height
    points[mask, 2] = z[mask] + total * (1 - z[mask] / transition_height) ** 2
    arrays = [points]; count_points = len(points)
    cells = []; labels = []; patches = defaultdict(list)
    physical = mesh.cell_data.get("gmsh:physical", mesh.cell_data.get("tag"))
    if physical is None:
        raise ValueError("Original physical boundary markers are required")
    sails = [b.data[np.isin(t,[13,14])].reshape(-1) for b,t in zip(mesh.cells,physical)
             if b.type in ('triangle','quad')]
    if sails:
        sail_nodes=np.unique(np.concatenate(sails))
        if len(sail_nodes) and np.any(old[sail_nodes,2]<transition_height):
            raise ValueError('Sea transition would move sail nodes')
    for block, tag in zip(mesh.cells, physical):
        if block.type in VOLUME:
            cells.append((block.type, block.data.copy())); labels.append(tag.copy())
        elif block.type in ("triangle", "quad"):
            for name, marker in (("sea", 11), ("hull", 12), ("main", 13), ("jib", 14)):
                selected = block.data[tag == marker]
                if len(selected):
                    if name == "sea":
                        patches["sea"].append((block.type, selected))
                    else:
                        cells.append((block.type, selected)); labels.append(np.full(len(selected), marker))
            for face in block.data[tag == 10]:
                marker = plane_tag(old[face], lower, upper)
                if marker == MARKERS["atmosphere_top"]:
                    patches["top"].append((block.type, face[None, :]))
                else:
                    cells.append((block.type, face[None, :])); labels.append(np.array([marker]))
    if not patches["sea"] or not patches["top"]:
        raise ValueError("Expected original sea and atmosphere surfaces")

    def extrude(patch, z_levels, existing_is_top):
        nonlocal count_points
        # Group many original boundary entries before making new cells.
        blocks = [(kind, np.concatenate([f for k, f in patches[patch] if k == kind]))
                  for kind in ("triangle", "quad") if any(k == kind for k, _ in patches[patch])]
        used = np.unique(np.concatenate([b.reshape(-1) for _, b in blocks]))
        local = np.full(len(old), -1, dtype=np.int64); local[used] = np.arange(len(used))
        levels = []
        for i, height in enumerate(z_levels):
            is_existing = (existing_is_top and i == len(z_levels) - 1) or (not existing_is_top and i == 0)
            if is_existing:
                levels.append(used)
            else:
                p = old[used].copy(); p[:, 2] = height
                arrays.append(p)
                levels.append(np.arange(count_points, count_points + len(p))); count_points += len(p)
        for kind, faces in blocks:
            f = local[faces].copy()
            xy = old[faces, :2]
            area = np.sum(xy[:, :, 0] * np.roll(xy[:, :, 1], -1, axis=1) - xy[:, :, 1] * np.roll(xy[:, :, 0], -1, axis=1), axis=1)
            f[area < 0] = f[area < 0, ::-1]
            volume_kind = "wedge" if kind == "triangle" else "hexahedron"
            volume = np.concatenate([np.column_stack((low[f], high[f])) for low, high in zip(levels[:-1], levels[1:])])
            cells.append((volume_kind, volume)); labels.append(np.ones(len(volume), dtype=int))
            boundary_level = levels[0] if existing_is_top else levels[-1]
            # All external faces use outward orientation from the fluid.
            wall = boundary_level[f[:, ::-1] if existing_is_top else f]
            cells.append((kind, wall)); labels.append(np.full(len(wall), MARKERS["sea" if patch == "sea" else "atmosphere_top"]))
        edge = _surface_edges([faces for _, faces in blocks])
        edge_local = local[edge]
        for side in edge_local:
            original_edge = used[side]
            p = old[original_edge]
            try:
                marker = plane_tag(p, lower, upper)
            except ValueError:
                if patch != "sea":
                    raise ValueError("Unexpected internal edge in top plane")
                marker = MARKERS["hull"]
            # Orient side quads away from their adjacent source surface cell.
            centers = []
            for _, faces in blocks:
                selected = faces[np.sum(np.isin(faces, original_edge), axis=1) == 2]
                if len(selected):
                    centers.append(old[selected].mean(axis=1))
            if sum(map(len, centers)) != 1:
                raise ValueError("Could not identify unique extrusion-edge owner")
            center = np.concatenate(centers)[0]
            outward = p.mean(axis=0) - center
            v = p[1] - p[0]
            if np.dot(np.cross(v, [0., 0., 1.]), outward) < 0:
                side = side[::-1]
            quads = np.array([[low[side[0]], low[side[1]], high[side[1]], high[side[0]]]
                              for low, high in zip(levels[:-1], levels[1:])])
            cells.append(("quad", quads)); labels.append(np.full(len(quads), marker))
        return len(used)

    sea_nodes = extrude("sea", np.r_[0., np.cumsum(heights)], True)
    top_z = upper[2] + (top_height - upper[2]) * np.expm1(np.linspace(0, np.log(4.), top_layers + 1)) / 3
    top_nodes = extrude("top", top_z, False)
    # Merge equal cell types to keep mesh writing and later queries efficient.
    kinds = list(dict.fromkeys(k for k, _ in cells))
    merged = [(k, np.concatenate([c for kind, c in cells if kind == k])) for k in kinds]
    tags = [np.concatenate([t for (kind, _), t in zip(cells, labels) if kind == k]) for k in kinds]
    field_data = {name: np.array([tag, 2]) for name, tag in MARKERS.items()}
    field_data["air"] = np.array([1, 3])
    result = meshio.Mesh(np.concatenate(arrays), merged, cell_data={"tag": tags}, field_data=field_data)
    metadata = {"method": "existing fine boat mesh + conforming sea extrusion + distant top extrusion",
        "sea": {"first_node_spacing_m": first, "layers": layers, "growth": growth, "total_thickness_m": total,
                "surface_nodes": sea_nodes, "bulk_transition_cutoff_m": transition_height,
                "minimum_continuous_vertical_map_derivative": 1 - 2 * total / transition_height},
        "atmosphere_top_m": top_height, "top_layers": top_layers, "top_interface_nodes": top_nodes,
        "original_nodes": len(old), "nodes": len(result.points),
        "elements": {b.type:len(b.data) for b in result.cells},
        "original_sail_nodes_unchanged": True}
    return result, metadata


def write_su2(path, mesh):
    with Path(path).open("wb") as f:
        count = sum(len(b.data) for b in mesh.cells if b.type in VOLUME)
        f.write(f"NDIME= 3\nNELEM= {count}\n".encode())
        for b in mesh.cells:
            if b.type in VOLUME:
                np.savetxt(f, np.column_stack((np.full(len(b.data), VOLUME[b.type]), b.data)), fmt="%d")
        f.write(f"NPOIN= {len(mesh.points)}\n".encode()); np.savetxt(f, mesh.points, fmt="%.16e")
        f.write(f"NMARK= {len(MARKERS)}\n".encode())
        for name, marker in MARKERS.items():
            entries = [(b.type, b.data[t == marker]) for b, t in zip(mesh.cells, mesh.cell_data["tag"])
                       if b.type in ("triangle", "quad")]
            n = sum(len(c) for _, c in entries)
            if not n:
                raise ValueError(f"Empty boundary: {name}")
            f.write(f"MARKER_TAG= {name}\nMARKER_ELEMS= {n}\n".encode())
            for kind, c in entries:
                np.savetxt(f, np.column_stack((np.full(len(c), 5 if kind == "triangle" else 9), c)), fmt="%d")


def check(mesh, output):
    """Closed boundary, positive hybrid Jacobians, and retained core quality."""
    import gmsh
    boundary = [b.data for b in mesh.cells if b.type in ("triangle", "quad")]
    all_edges = np.concatenate([np.stack((b, np.roll(b, -1, axis=1)), axis=-1).reshape(-1, 2) for b in boundary])
    _, counts = np.unique(np.sort(all_edges, axis=1), axis=0, return_counts=True)
    if np.any(counts != 2):
        raise ValueError("Augmented boundary is not closed")
    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.open(str(Path(output) / "yacht.msh"))
        _, ids, _ = gmsh.model.mesh.getElements(3)
        determinant = gmsh.model.mesh.getElementQualities(np.concatenate(ids), "minDetJac")
        if not np.isfinite(determinant).all() or np.min(determinant) <= 0:
            raise ValueError("Marine extrusion/vertical map produced nonpositive cell Jacobians")
        tet_ids = gmsh.model.mesh.getElementsByType(4)[0]
        quality = gmsh.model.mesh.getElementQualities(tet_ids, "minSICN") if len(tet_ids) else np.array([1.])
        if np.min(quality) < .001:
            raise ValueError("Marine transformation reduced tetrahedral quality below 0.001")
        return {"closed_boundary": True, "minimum_element_jacobian": float(np.min(determinant)),
                "tetrahedral_minSICN": float(np.min(quality)), "solver_started": False}
    finally:
        gmsh.finalize()
