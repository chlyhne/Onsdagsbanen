"""Participation application operations."""
from . import common
from datetime import timedelta
from decimal import Decimal
import json
from omvendt_respit.starts import Boat, calculate_starts
from ..domain import RATINGS, number, gph, rating_value
from ..store import audit, get
from .. import hierarchy, fleet

def boats(db, series_id):
    result = [dict(r) for r in db.execute('SELECT b.*,p.name,p.skipper,p.sail_number,p.boat_type,p.club,p.current_handicap_set_id,h.ratings,h.extra_handicap FROM boats b JOIN boat_profiles p ON p.id=b.profile_id JOIN handicap_sets h ON h.id=p.current_handicap_set_id WHERE b.series_id=? ORDER BY b.course,b.class_name,p.name', (series_id,))]
    for row in result:
        row['ratings'] = json.loads(row['ratings'])
        row['gph'] = gph(row['ratings'])
    return result

def rows(db, race_id):
    result = [dict(r) for r in db.execute("SELECT e.*,b.profile_id,p.name,p.skipper,p.sail_number,p.boat_type,r.course,r.rating_key,h.ratings handicap_ratings,h.extra_handicap,coalesce(f.status,'DNC') status,f.instant,f.elapsed,f.corrected,f.sequence,f.note,f.source_points,f.source_discarded,coalesce(f.penalty,0) penalty FROM entries e JOIN races r ON r.id=e.race_id JOIN boats b ON b.id=e.boat_id JOIN boat_profiles p ON p.id=b.profile_id JOIN handicap_sets h ON h.id=e.handicap_set_id LEFT JOIN finishes f ON f.race_id=e.race_id AND f.boat_id=e.boat_id WHERE e.race_id=?", (race_id,))]
    for row in result:
        ratings = json.loads(row.pop('handicap_ratings'))
        row['rating'] = rating_value(ratings, row['rating_key'])
        if row['rating'] is None:
            row['rating'] = ratings.get(row['rating_key'])
        row['handicap_set'] = {'id': row['handicap_set_id'], 'profile_id': row['profile_id'], 'ratings': ratings, 'extra_handicap': row['extra_handicap']}
    pending = any((row['rating'] is None for row in result)) and db.execute('SELECT mode FROM races WHERE id=?', (race_id,)).fetchone()[0] == 'pursuit'
    for row in result:
        row['start_ready'] = not pending
        if pending:
            row['start'] = None
    return result

def replace_roster(db, race_id):
    race = get(db, 'races', race_id)
    if race['published'] or db.execute("SELECT 1 FROM finishes WHERE race_id=? AND status<>'DUTY'", (race_id,)).fetchone():
        raise ValueError('Deltagerlisten kan kun ændres før resultatindtastningen begynder.')
    fleet_by_id = {b['id']: b for b in boats(db, race['series_id'])}
    selected = [{**fleet_by_id[r['boat_id']], 'heat_name': r['name']} for r in db.execute('SELECT m.boat_id,h.name FROM heat_boats m JOIN heats h ON h.id=m.heat_id WHERE h.round_course_id=? ORDER BY m.boat_id', (race['round_course_id'],))]
    if len({b['id'] for b in selected}) != len(selected):
        raise ValueError('En båd må kun deltage i ét løb på samme bane i et ræs.')
    snapshots = {row['boat_id']: row for row in rows(db, race_id)}
    from ..compatibility import issues_for, HandicapError
    round_id=db.execute('SELECT round_id FROM round_courses WHERE id=?',(race['round_course_id'],)).fetchone()[0]
    issues=issues_for(db,[b['id'] for b in selected],race['rating_key'],round_id,race_id)
    if issues:raise HandicapError(issues)
    for boat in selected:
        old = snapshots.get(boat['id'])
        boat['rating'] = old['rating'] if old else rating_value(boat['ratings'], race['rating_key'])
        boat['set_id'] = old['handicap_set_id'] if old else boat['current_handicap_set_id']
        boat['extra_handicap'] = old['extra_handicap'] if old else boat['extra_handicap']
    missing = [b['name'] for b in selected if b['rating'] is None]
    if missing:
        db.execute('UPDATE races SET warning=? WHERE id=?', ('Mangler ' + race['rating_key'] + ': ' + ', '.join(missing), race_id))
    elif race['warning'].startswith('Mangler '):
        db.execute("UPDATE races SET warning='' WHERE id=?", (race_id,))
    schedule = {}
    if race['mode'] == 'pursuit' and selected and (not missing):
        fleet = [Boat(str(b['id']), b['skipper'], b['name'], b['sail_number'], b['heat_name'], Decimal(str(b['rating']))) for b in selected]
        schedule = {int(s.boat.identity): (s.time + timedelta(seconds=next((b['extra_handicap'] for b in selected if b['id'] == int(s.boat.identity))))).isoformat() for s in calculate_starts(fleet, Decimal(str(race['distance'])), common.instant(race['start']))}
    selected_ids = {b['id'] for b in selected}
    for old in snapshots.values():
        if old['boat_id'] not in selected_ids:
            if old['sequence'] is not None:
                raise ValueError('Fjern først bådens registrerede status før tilmeldingen ændres.')
            db.execute('DELETE FROM entries WHERE race_id=? AND boat_id=?', (race_id, old['boat_id']))
    for boat in selected:
        db.execute('INSERT INTO entries(race_id,boat_id,class_name,start,distance,wind_category,handicap_set_id) VALUES(?,?,?,?,?,?,?)', (race_id, boat['id'], boat['heat_name'], schedule.get(boat['id'], race['start']), race['distance'], '' if race['rating_key'] == 'GPH' else race['rating_key'][-1], boat['set_id']))

def create_race(db, data, user):
    series = get(db, 'series', int(data['series_id']))
    course, mode = (data['course'], data['mode'])
    system = data.get('handicap_system')
    if system is None and 'rating_key' not in data and data.get('round_id'):
        system = get(db, 'rounds', int(data['round_id']))['handicap_system']
    wind = data.get('wind_category', 'M')
    if system is not None:
        if system not in ('circle', 'gph', 'updown'):
            raise ValueError('Vælg cirkelmål, GPH eller op/ned-mål.')
        if system == 'gph' and data.get('wind_category'):
            raise ValueError('Vindkategori kan ikke indstilles ved GPH.')
        if system != 'gph' and wind not in ('L', 'M', 'H'):
            raise ValueError('Vælg en vindkategori.')
        key = 'GPH' if system == 'gph' else ('TACI' if system == 'circle' else 'TAUD') + wind
    else:
        key = data.get('rating_key', 'TACIM')
        system = 'gph' if key == 'GPH' else 'updown' if key.startswith('TAUD') else 'circle'
    if key == 'GPH' and data.get('wind_category'):
        raise ValueError('Vindkategori kan ikke indstilles ved GPH.')
    if course not in ('lille', 'stor') or mode not in ('normal', 'pursuit') or key not in (*RATINGS, 'GPH'):
        raise ValueError('Vælg bane, startform og DH-tal.')
    if data.get('round_id'):
        planned = get(db, 'rounds', int(data['round_id']))
        if planned['series_id'] != series['id']:
            raise ValueError('Ræset tilhører en anden serie.')
        label = planned['label']
        round_id = planned['id']
    else:
        label = str(data.get('label', '')).strip()
        if not label or len(label) > 60:
            raise ValueError('Vælg et ræs i serien.')
        round_id = hierarchy.ensure_round(db, series['id'], label)
    if get(db, 'rounds', round_id)['completed']:
        raise ValueError('Ræset er afsluttet. Genåbn begge baner før opsætningen ændres.')
    for sibling in db.execute('SELECT r.rating_key FROM races r JOIN round_courses c ON c.id=r.round_course_id WHERE c.round_id=?', (round_id,)):
        sibling_system = 'gph' if sibling['rating_key'] == 'GPH' else 'updown' if sibling['rating_key'].startswith('TAUD') else 'circle'
        if sibling_system != system:
            raise ValueError('Beregningsmålet gælder hele ræset. Den anden bane bruger allerede et andet mål.')
    db.execute('UPDATE rounds SET handicap_system=? WHERE id=?', (system, round_id))
    course_id = db.execute('SELECT id FROM round_courses WHERE round_id=? AND course=?', (round_id, course)).fetchone()[0]
    if db.execute('SELECT 1 FROM races WHERE round_course_id=?', (course_id,)).fetchone():
        raise ValueError('Denne bane i ræset er allerede indstillet. Vælg den eksisterende sejlads.')
    planned = get(db, 'rounds', round_id)
    scheduled = db.execute('SELECT start_time FROM round_courses WHERE id=?', (course_id,)).fetchone()[0]
    start = common.instant(data.get('start') or (planned['race_date'] + 'T' + scheduled if planned['race_date'] else ''))
    if start.year != series['year']:
        raise ValueError('Datoen skal ligge i seriens årstal.')
    distance = common.geometry(data['route'])['distance']
    if 'boat_ids' in data:
        selected_ids = data['boat_ids']
        if not isinstance(selected_ids, list) or not selected_ids or any((type(v) is not int for v in selected_ids)) or (len(set(selected_ids)) != len(selected_ids)):
            raise ValueError('Vælg mindst én deltager uden gentagelser.')
        selected = [b for b in boats(db, series['id']) if b['id'] in selected_ids and b['course'] == course]
        if len(selected) != len(selected_ids):
            raise ValueError('Alle deltagere skal tilhøre denne serie og bane.')
        assignments = {r['boat_id']: r['name'] for r in db.execute('SELECT m.boat_id,h.name FROM heat_boats m JOIN heats h ON h.id=m.heat_id WHERE h.round_course_id=?', (course_id,))}
        db.execute('DELETE FROM heat_boats WHERE heat_id IN (SELECT id FROM heats WHERE round_course_id=?) AND boat_id NOT IN (SELECT value FROM json_each(?))', (course_id,json.dumps(selected_ids)))
        for boat in selected:
            name = assignments.get(boat['id'], boat['class_name'])
            db.execute('INSERT OR IGNORE INTO heats(round_course_id,name,position) VALUES(?,?,0)', (course_id, name))
            heat_id = db.execute('SELECT id FROM heats WHERE round_course_id=? AND name=?', (course_id, name)).fetchone()[0]
            db.execute('INSERT INTO heat_boats(heat_id,boat_id) VALUES(?,?)', (heat_id, boat['id']))
    reference = number(data.get('reference', series['reference']))
    identity = db.execute('INSERT INTO races(series_id,label,course,mode,start,route,distance,rating_key,reference,round_course_id) VALUES(?,?,?,?,?,?,?,?,?,?)', (series['id'], label, course, mode, start.isoformat(), json.dumps(data['route']), distance, key, reference, course_id)).lastrowid
    replace_roster(db, identity)
    from ..unconfigured import apply
    apply(db, round_id, identity, user)
    db.execute('UPDATE round_courses SET start_time=? WHERE id=?', (start.strftime('%H:%M:%S'), course_id))
    db.execute('UPDATE rounds SET race_date=coalesce(race_date,?) WHERE id=?', (start.date().isoformat(), round_id))
    audit(db, user, 'race.create', {'id': identity, **data})
    return identity
