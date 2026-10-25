"""Validated native 3D velocity and interpolation on the original fluid cells."""
from pathlib import Path
import json

import meshio
import numpy as np

from cfd.storage import case_file
from cfd.transfer_solution import read_restart


def native_velocity(case):
    case = Path(case)
    manifest = json.loads(case_file(case, "marine_case.json").read_text())
    geometry = json.loads(case_file(case, "geometry.json").read_text())
    fields, data = read_restart(case_file(case, "restart.dat"))
    if fields[:7] != ["x", "y", "z", "Pressure", "Velocity_x", "Velocity_y", "Velocity_z"]:
        raise ValueError("A full three-dimensional restart is required")
    mesh = meshio.read(case_file(case, "yacht.msh"))
    if len(mesh.points) != len(data) or not np.allclose(mesh.points, data[:, :3], rtol=0, atol=1e-10):
        raise ValueError("Native restart does not match the original mesh")
    config = {line.split("=", 1)[0].strip(): line.split("=", 1)[1].strip()
              for line in case_file(case, "yacht.cfg").read_text().splitlines()
              if "=" in line and not line.lstrip().startswith("%")}
    scale = float(manifest["native_reference_speed_m_s"])
    configured = np.fromstring(config["INC_VELOCITY_INIT"].strip("()"), sep=",")
    if config.get("INC_NONDIM") != "INITIAL_VALUES" or not np.isclose(np.linalg.norm(configured), scale, rtol=1e-12):
        raise ValueError("Native velocity reference does not match configuration")
    velocity = np.asarray(data[:, 4:7]) * scale
    if not np.isfinite(velocity).all():
        raise ValueError("Non-finite native velocity")
    return mesh, velocity, manifest, geometry


def fluid_grid(mesh, point_fields):
    """Convert meshio volume cells, including its VTK wedge node permutation."""
    import pyvista as pv

    types = {"tetra": pv.CellType.TETRA, "wedge": pv.CellType.WEDGE,
             "pyramid": pv.CellType.PYRAMID, "hexahedron": pv.CellType.HEXAHEDRON}
    cells, labels = [], []
    for block in mesh.cells:
        if block.type not in types:
            continue
        nodes = block.data
        if block.type == "wedge":
            nodes = nodes[:, [0, 2, 1, 3, 5, 4]]
        cells.append(np.column_stack((np.full(len(nodes), nodes.shape[1]), nodes)).ravel())
        labels.append(np.full(len(nodes), types[block.type], dtype=np.uint8))
    if not cells:
        raise ValueError("No 3D fluid volume cells")
    grid = pv.UnstructuredGrid(np.concatenate(cells), np.concatenate(labels), mesh.points)
    for name, values in point_fields.items():
        grid.point_data[name] = values
    return grid


def sample_grid(mesh, velocity, bounds, spacing):
    """Interpolate velocity in actual cells; leave solids/outside points NaN.

    Returned arrays use VTK ordering: x fastest, then y, then z.
    """
    import pyvista as pv

    bounds = np.asarray(bounds, dtype=float)
    spacing = np.broadcast_to(np.asarray(spacing, dtype=float), (3,))
    if bounds.shape != (3, 2) or np.any(bounds[:, 1] <= bounds[:, 0]) or np.any(spacing <= 0):
        raise ValueError("Require increasing bounds and positive spacing")
    dimensions = np.floor(np.diff(bounds, axis=1).ravel() / spacing + 1e-9).astype(int) + 1
    image = pv.ImageData(dimensions=dimensions, spacing=spacing, origin=bounds[:, 0])
    grid = fluid_grid(mesh, {"velocity": velocity})
    sampled = image.sample(grid, tolerance=1e-8, locator="static_cell",
                           pass_cell_data=False, pass_point_data=False, snap_to_closest_point=False)
    valid = np.asarray(sampled["vtkValidPointMask"], dtype=bool)
    result = np.asarray(sampled["velocity"]).copy()
    result[~valid] = np.nan
    axes = [bounds[i, 0] + np.arange(dimensions[i]) * spacing[i] for i in range(3)]
    return axes, np.asarray(image.points), result, valid
