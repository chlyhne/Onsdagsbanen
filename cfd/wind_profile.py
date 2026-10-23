"""Explicit atmospheric assumptions for horizontal sail sections (SI units)."""
from dataclasses import asdict, dataclass

import numpy as np

KNOT = 1852 / 3600


@dataclass(frozen=True)
class WindProfile:
    reference_speed_m_s: float = 8.0
    reference_height_m: float = 10.0
    roughness_length_m: float = 0.0002
    true_angle_deg: float = 41.0
    boat_speed_knots: float = 6.0
    kind: str = "neutral_log"

    def __post_init__(self):
        if self.kind not in ("neutral_log", "uniform"):
            raise ValueError("Profile must be neutral_log or uniform")
        if not 0 < self.roughness_length_m < self.reference_height_m:
            raise ValueError("Require 0 < z0 < reference height")
        if self.reference_speed_m_s <= 0 or self.boat_speed_knots < 0:
            raise ValueError("Require positive wind speed and nonnegative boat speed")

    def at(self, height):
        z = np.asarray(height, dtype=float)
        if np.any(z < 0):
            raise ValueError("Height must be above mean sea level")
        speed = np.full_like(z, self.reference_speed_m_s)
        if self.kind == "neutral_log":
            speed *= np.log1p(z / self.roughness_length_m) / np.log1p(
                self.reference_height_m / self.roughness_length_m)
        a = np.radians(self.true_angle_deg)
        ux = -speed * np.cos(a) - self.boat_speed_knots * KNOT
        uy = speed * np.sin(a)
        return {
            "height_m": z.tolist(), "true_speed_m_s": speed.tolist(),
            "apparent_velocity_m_s": np.stack((ux, uy, np.zeros_like(z)), axis=-1).tolist(),
            "apparent_speed_m_s": np.hypot(ux, uy).tolist(),
            "apparent_angle_deg": np.degrees(np.arctan2(uy, -ux)).tolist(),
        }

    @property
    def friction_velocity_m_s(self):
        if self.kind != "neutral_log":
            raise ValueError("Uniform wind has no atmospheric equilibrium profile")
        return .41 * self.reference_speed_m_s / np.log1p(self.reference_height_m / self.roughness_length_m)

    def turbulence_at(self, height):
        """Neutral surface-layer k/omega estimates, not an exact SST solution.

        beta*=0.09 and kappa=0.41 agree with the pinned SST constants.
        Near-wall blending, molecular diffusion and boundary conditions still
        require an empty-domain preservation check after an approved CFD run.
        Translation changes the mean velocity, not k or omega.
        """
        z = np.asarray(height, dtype=float)
        if np.any(z < 0):
            raise ValueError("Height must be nonnegative")
        ustar = self.friction_velocity_m_s
        k = np.full_like(z, ustar ** 2 / np.sqrt(.09))
        omega = ustar / (np.sqrt(.09) * .41 * (z + self.roughness_length_m))
        speed = np.asarray(self.at(z)["apparent_speed_m_s"])
        return {"k_m2_s2": k.tolist(), "omega_s_inv": omega.tolist(),
                "eddy_viscosity_m2_s": (k / omega).tolist(),
                "intensity_relative_to_apparent_speed": (np.sqrt(2 * k / 3) / speed).tolist()}

    def metadata(self):
        return {**asdict(self), "true_direction_varies_with_height": False,
                "assumptions": "Neutral atmosphere; prescribed z0, no waves/stability/veer model. "
                "Apparent wind includes boat translation at every height.",
                "source": "https://wasp.dtu.dk/support/frequently-asked-questions/wasp-faq/Wind-shear-exponents"}
