"""Common application operations."""
from datetime import datetime
import math
from pathlib import Path
from zoneinfo import ZoneInfo
import re
from omvendt_respit.course import read_marks, calculate_course, rounding_sides
ROOT = Path(__file__).resolve().parents[3]
TZ = ZoneInfo('Europe/Copenhagen')


def instant(value):
    if not re.fullmatch('\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}(?::\\d{2}(?:\\.\\d{1,6})?)?(?:Z|[+-]\\d{2}:\\d{2})?', str(value)):
        raise ValueError('Angiv dato og klokkeslæt i tidsfeltet. Tekst og ugyldige tider kan ikke gemmes.')
    try:
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (ValueError, TypeError):
        raise ValueError('Tidspunktet er ugyldigt.') from None
    if result.tzinfo is None:
        result = result.replace(tzinfo=TZ)
    return result.astimezone(TZ)

def geometry(route):
    if not isinstance(route, list) or len(route) > 100:
        raise ValueError('Vælg op til 100 rundingsmærker.')
    course = calculate_course(read_marks(ROOT / 'omvendt_respit/resources/marks.csv'), route)
    return {'distance': float(course.distance_nm), 'legs': [{'from': leg.start.name, 'to': leg.end.name, 'distance': float(leg.distance_nm)} for leg in course.legs], 'rounding_marks': [{'name': mark.name, 'side': side} for mark, side in zip(course.rounding_marks, rounding_sides(course))]}


def course_distance(route,value=None):
    from ..domain import number
    if not isinstance(route,list):raise ValueError('Vælg en mærkerækkefølge eller angiv en distance.')
    return geometry(route)['distance'] if route else number(value)


def rounding_conflicts(courses):
    sides={'stor':{},'lille':{}}
    for course in courses:
        if course['course'] not in sides:continue
        for mark in course.get('rounding_marks',[]):
            if mark['side'] in ('port','starboard'):
                sides[course['course']].setdefault(mark['name'],set()).add(mark['side'])
    # Keep all visits to a repeated mark: a later opposite rounding matters too.
    return [{'name':name,**{course:[side for side in ('port','starboard') if side in marks[name]] for course,marks in sides.items()}}
            for name in sorted(sides['stor'].keys() & sides['lille'].keys())
            if any(a!=b for a in sides['stor'][name] for b in sides['lille'][name])]

def extra_handicap_value(value):
    try:
        result = float(str(value).replace(',', '.'))
    except (ValueError, TypeError):
        raise ValueError('Tidstillæg skal være et antal hele sekunder fra 0 til 3600.') from None
    if not math.isfinite(result) or result < 0 or result > 3600 or (result != int(result)):
        raise ValueError('Tidstillæg skal være et antal hele sekunder fra 0 til 3600.')
    return int(result)
