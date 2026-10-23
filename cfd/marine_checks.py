"""Post-simulation atmospheric-profile preservation; never called by plots."""
import numpy as np


def background_profile(points, native, profile, reference_speed, centers=None):
    """Compare actual fluid nodes in four columns away from the boat wake.

    No interpolation error or artificial reference-field clipping. These
    finite distances can still contain physical induction from the yacht.
    """
    centers = centers if centers is not None else [(25., -25.), (0., -30.), (-35., -25.), (-65., 5.)]
    z = points[:, 2]
    height = (z >= 1.15) & (z <= 11.65)
    regions = []; masks = []
    for x, y in centers:
        mask = height & ((points[:, 0] - x) ** 2 + (points[:, 1] - y) ** 2 <= 7. ** 2)
        masks.append(mask)
        regions.append({"center_xy_m": [x, y], "radius_m": 7., "nodes": int(mask.sum())})
    mask = np.logical_or.reduce(masks)
    if not mask.any():
        return {"passed": False, "reason": "No background-profile sampling nodes", "regions": regions}
    from cfd.marine_inflow import initial_values
    expected = initial_values(points[mask], profile)
    sampled = native[mask]
    velocity = sampled[:, 4:7] * reference_speed
    k = sampled[:, 7] * reference_speed ** 2
    omega = sampled[:, 8] * reference_speed
    u = expected[:, 1:4]
    speed_error = abs(np.linalg.norm(velocity, axis=1) / np.linalg.norm(u, axis=1) - 1)
    angle = abs(np.degrees(np.arctan2(u[:, 0] * velocity[:, 1] - u[:, 1] * velocity[:, 0],
                                      np.sum(u[:, :2] * velocity[:, :2], axis=1))))
    errors = {"relative_speed": speed_error, "angle_deg": angle,
              "relative_k": abs(k / expected[:, 4] - 1), "relative_omega": abs(omega / expected[:, 5] - 1)}
    limits = {"relative_speed": .01, "angle_deg": .1, "relative_k": .05, "relative_omega": .05}
    statistics = {name: {"p50": float(np.median(a)), "p95": float(np.percentile(a, 95)), "max": float(np.max(a))}
                  for name, a in errors.items()}
    checks = {name: statistics[name]["max"] <= limit for name, limit in limits.items()}
    checks["all_four_columns_sampled"] = all(r["nodes"] > 0 for r in regions)
    return {"passed": all(checks.values()), "checks": checks, "limits": limits, "changes": statistics,
            "nodes": int(mask.sum()), "height_range_m": [1.15, 11.65], "regions": regions,
            "reference": "prescribed analytical atmosphere",
            "interpretation": "Post-simulation difference from the reference at real mesh nodes, outside the central wake. "
            "This includes physical yacht induction and differences in discretization; not a grid-independence study."}
