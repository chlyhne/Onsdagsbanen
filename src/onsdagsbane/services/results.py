"""Results application operations."""
from . import common, participation
import json
from ..domain import daily, overall
from ..store import get
from .. import fleet

def result_view(db, series_id, course, class_name='', drafts=False):
    class_ids = {r[0] for r in db.execute('SELECT m.boat_id FROM heat_boats m JOIN heats h ON h.id=m.heat_id JOIN round_courses c ON c.id=h.round_course_id JOIN rounds p ON p.id=c.round_id WHERE p.series_id=? AND c.course=? AND h.name=? UNION SELECT e.boat_id FROM entries e JOIN races r ON r.id=e.race_id WHERE r.series_id=? AND r.course=? AND e.class_name=?', (series_id, course, class_name, series_id, course, class_name))} if class_name else set()
    course_ids = {r[0] for r in db.execute('SELECT m.boat_id FROM heat_boats m JOIN heats h ON h.id=m.heat_id JOIN round_courses c ON c.id=h.round_course_id JOIN rounds p ON p.id=c.round_id WHERE p.series_id=? AND c.course=? UNION SELECT e.boat_id FROM entries e JOIN races r ON r.id=e.race_id WHERE r.series_id=? AND r.course=?', (series_id, course, series_id, course))}
    fleet = [b for b in participation.boats(db, series_id) if b['course'] == course and (not class_name or b['class_name'] == class_name) or b['id'] in (class_ids if class_name else course_ids)]
    class_by_boat = {}
    for member in db.execute('SELECT m.boat_id,h.name FROM heat_boats m JOIN heats h ON h.id=m.heat_id JOIN round_courses c ON c.id=h.round_course_id JOIN rounds p ON p.id=c.round_id WHERE p.series_id=? AND c.course=? ORDER BY p.position,h.position', (series_id, course)):
        class_by_boat.setdefault(member['boat_id'], [])
        if member['name'] not in class_by_boat[member['boat_id']]:
            class_by_boat[member['boat_id']].append(member['name'])
    fleet = [{**boat, 'course': course, 'class_name': class_name or ' / '.join(class_by_boat.get(boat['id'], [boat['class_name']]))} for boat in fleet]
    races = [dict(r) for r in db.execute('SELECT * FROM races WHERE series_id=? AND course=? ORDER BY start,id', (series_id, course)) if drafts or r['published']]
    results = {}
    for race in races:
        route = json.loads(race['route'])
        race['rounding_marks'] = common.geometry(route)['rounding_marks'] if route else []
        data = [r for r in participation.rows(db, race['id']) if not class_name or r['class_name'] == class_name]
        present = {r['boat_id'] for r in data}
        for boat in fleet:
            if boat['id'] in present:
                continue
            same_class = next((r for r in data if r['class_name'] == boat['class_name']), None)
            data.append({'boat_id': boat['id'], 'class_name': class_name or boat['class_name'], 'rating': None, 'start': same_class['start'] if same_class else race['start'], 'distance': same_class['distance'] if same_class else race['distance'], 'extra_handicap': 0, 'penalty': 0, 'wind_category': same_class['wind_category'] if same_class else '' if race['rating_key'] == 'GPH' else race['rating_key'][-1], 'name': boat['name'], 'skipper': boat['skipper'], 'sail_number': boat['sail_number'], 'boat_type': boat['boat_type'], 'course': course, 'status': 'DNC', 'instant': None, 'elapsed': None, 'corrected': None, 'sequence': None, 'note': 'Konsolideret fra seriens deltagerliste; ingen registrering i denne sejlads.', 'source_points': None, 'source_discarded': None})
        day_races = [dict(r) for r in db.execute('SELECT id FROM races WHERE series_id=? AND substr(start,1,10)=? AND (published=1 OR ?)', (series_id, race['start'][:10], int(drafts)))]
        wind = {r['wind_category'] for other in day_races for r in participation.rows(db, other['id']) if r['wind_category']}
        distances = {r['distance'] for r in participation.rows(db, race['id'])}
        warnings = []
        if len(distances) > 1:
            warnings.append('Løbene på banen har forskellige distancer: ' + ', '.join((f'{v:g}' for v in sorted(distances))) + ' sm. Samlet resultat er vejledende.')
        if len(wind) > 1:
            warnings.append('Dagens løb bruger forskellige vindkategorier på tværs af lille og stor bane.')
        race['warning'] = ' '.join(filter(None, [race['warning'], *warnings]))
        results[race['id']] = daily(data, pursuit=race['mode'] == 'pursuit')
    settings = get(db, 'series', series_id)
    references = sorted({r['reference'] for r in db.execute('SELECT reference,published FROM races WHERE series_id=?', (series_id,)) if drafts or r['published']})
    return {'races': races, 'daily': results, 'overall': overall(fleet, races, results, json.loads(settings['discards'])) if races else [], 'discards': json.loads(settings['discards']), 'references': references, 'reference_warning': len(references) > 1, 'source_scoring': False}
