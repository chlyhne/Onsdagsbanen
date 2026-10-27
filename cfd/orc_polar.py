"""Local linearisation of an interpolated ORC polar; no CFD simulation."""
import json
from pathlib import Path

import numpy as np

from cfd.plot_wind import VMG_FACTOR, HEADER_WEIGHT_KNOTS_PER_DEG, KNOTS_PER_M_S

POLAR_PATH = Path(__file__).parent/'reference/x332_orc_2019.json'


def boat_speed(polar, wind_knots, angle_deg):
    """Interpolate wind first, then angle; include the interpolated beat point.

    No extrapolation below the certificate's beat angle or outside wind limits.
    """
    winds = np.asarray(polar['true_wind_knots'])
    if not winds[0] <= wind_knots <= winds[-1]:
        raise ValueError('Wind outside the ORC polar range')
    beat_angle = np.interp(wind_knots, winds, polar['beat_angle_deg'])
    beat_vmg = np.interp(wind_knots, winds, polar['beat_vmg_knots'])
    angles = np.r_[beat_angle, polar['true_wind_angle_deg']]
    if not angles[0] <= angle_deg <= angles[-1]:
        raise ValueError('Angle outside the interpolated ORC polar range')
    speeds = np.r_[beat_vmg/np.cos(np.radians(beat_angle)),
                   [np.interp(wind_knots, winds, row) for row in polar['boat_speed_knots']]]
    return float(np.interp(angle_deg, angles, speeds))


def linearisation(wind_knots=5*KNOTS_PER_M_S, angle_deg=41., polar=None):
    polar = polar or json.loads(POLAR_PATH.read_text(encoding='utf-8'))
    speed = boat_speed(polar, wind_knots, angle_deg)
    dw, da = .001, .001
    d_wind = (boat_speed(polar, wind_knots+dw, angle_deg)-boat_speed(polar, wind_knots-dw, angle_deg))/(2*dw)
    d_angle = (boat_speed(polar, wind_knots, angle_deg+da)-boat_speed(polar, wind_knots, angle_deg-da))/(2*da)
    return {'true_wind_knots': float(wind_knots), 'true_wind_angle_deg': float(angle_deg),
            'boat_speed_knots': speed, 'vmg_knots': float(speed*np.cos(np.radians(angle_deg))),
            'd_boat_speed_d_tws': d_wind, 'd_boat_speed_d_twa': d_angle,
            'percent_per_true_wind_knot': 100*d_wind/speed,
            'percent_per_true_header_degree': -100*d_angle/speed,
            'course_assumption': 'Hold the original course; interpolate polar boat speed at local TWS/TWA. VMG is projected toward the original upwind direction. The fixed cosine cancels from the percentage ratio.',
            'interpolation': 'Linear in true wind at each tabulated row, then linear in angle including the wind-interpolated beat point',
            'derivative_step_wind_knots': dw, 'derivative_step_angle_deg': da,
            'polar': polar}


def true_wind_perturbations(speed_delta_m_s, header_deg, heights, profile):
    """First-order Cartesian conversion of moving-frame polar perturbations.

    Adding the source boat's fixed velocity to both local and reference wind
    leaves their Cartesian perturbation unchanged. Project it on the true-wind
    basis, instead of applying true-wind polar derivatives to apparent wind.
    Vertical velocity has no first-order contribution at the horizontal baseline.
    """
    speed_delta_m_s, header_deg, heights = np.broadcast_arrays(speed_delta_m_s, header_deg, heights)
    apparent = np.asarray(profile.at(heights.ravel())['apparent_velocity_m_s'])[:, :2]
    true = apparent + [profile.boat_speed_knots/KNOTS_PER_M_S, 0.]
    a_speed = np.linalg.norm(apparent, axis=1)
    t_speed = np.linalg.norm(true, axis=1)
    ea = apparent/a_speed[:, None]
    na = np.column_stack((-ea[:, 1], ea[:, 0]))
    change = ea*speed_delta_m_s.ravel()[:, None] + na*(a_speed*np.radians(header_deg.ravel()))[:, None]
    et = true/t_speed[:, None]
    wind = np.sum(et*change, axis=1)*KNOTS_PER_M_S
    header = np.degrees((et[:, 0]*change[:, 1]-et[:, 1]*change[:, 0])/t_speed)
    return wind.reshape(heights.shape), header.reshape(heights.shape)


def percent_fields(cached_speed, cached_direction, heights, profile, coefficients):
    """Recover centred physical perturbations, then apply polar sensitivities.

    Inverting old display factors does not reuse its empirical VMG model or
    independently centred combined score. The new components add exactly.
    """
    speed_delta = np.asarray(cached_speed)/(VMG_FACTOR*KNOTS_PER_M_S)
    apparent_header = -np.asarray(cached_direction)/(VMG_FACTOR*HEADER_WEIGHT_KNOTS_PER_DEG)
    wind, header = true_wind_perturbations(speed_delta, apparent_header, heights, profile)
    speed = coefficients['percent_per_true_wind_knot']*wind
    direction = coefficients['percent_per_true_header_degree']*header
    return {'speed': speed, 'direction': direction, 'goodness': speed+direction}
