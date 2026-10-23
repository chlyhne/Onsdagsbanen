"""Shared wind and saved-case geometry helpers."""
import numpy as np

BOAT_LENGTH=10.
KNOT_TO_M_S=1852/3600
SAIL_FOOT_HEIGHT=1.15
SAILS=[(0.,3.7,10.5,12.),(3.4,2.8,8.5,20.)]
ENTRY_CHORD_FRACTION=.05

def apparent_wind(args):
    """Air velocity relative to a boat translating along +x: U_true - V_boat."""
    a=np.radians(args.wind_angle)
    return np.array([-args.wind_speed*np.cos(a)-args.boat_speed*KNOT_TO_M_S,
                     args.wind_speed*np.sin(a),0.])


def wind_angle_from_velocity(velocity):
    return float(np.degrees(np.arctan2(velocity[1],-velocity[0])))


def case_sails(geometry):
    return geometry["sail_definitions"]


def case_section(geometry,index,height):
    from cfd.rans_mesh import section
    return section(case_sails(geometry)[index],height,geometry["sail_shapes"][index])


def case_entry_angle(geometry,index,height):
    from cfd.rans_mesh import entry_angle
    return entry_angle(case_sails(geometry)[index],height,
                       geometry["sail_shapes"][index],ENTRY_CHORD_FRACTION)


def case_chord_trim(geometry,index,height):
    sail=case_sails(geometry)[index]
    shape=geometry["sail_shapes"][index]
    return sail[3]+shape["twist_deg"]*(height-shape["trim_height_m"])/sail[2]
