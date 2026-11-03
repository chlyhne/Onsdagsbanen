"""Boats application operations."""
from . import common, participation, timing
from datetime import timedelta
from decimal import Decimal
import json
from omvendt_respit.starts import Boat, calculate_starts
from ..domain import RATINGS, correction, number, rating_value
from ..store import audit, get
from .. import hierarchy, fleet, handicaps

def save_boat(db, data, user, identity=None):
    series = get(db, 'series', int(data['series_id']))
    values = [str(data.get(k, '')).strip() for k in ('class_name', 'course', 'name', 'skipper', 'sail_number', 'boat_type', 'club')]
    if not all(values[:6]) or any((len(x) > 160 for x in values)) or values[1] not in ('lille', 'stor'):
        raise ValueError('Udfyld løb, bane, navn, skipper, sejlnummer og bådtype.')
    ratings = {key: number(data.get('ratings', {}).get(key), optional=True) for key in RATINGS}
    extra = common.extra_handicap_value(data.get('extra_handicap', 0))
    existing = get(db, 'boats', identity) if identity else None
    if existing and data.get('handicap_set_id'):
        selected_set = db.execute('SELECT * FROM handicap_sets WHERE id=? AND profile_id=?', (int(data['handicap_set_id']), existing['profile_id'])).fetchone()
        if not selected_set:
            raise ValueError('Handicapsættet skal tilhøre denne båd.')
        ratings = json.loads(selected_set['ratings'])
        extra = selected_set['extra_handicap']
    if existing:
        if existing['series_id'] != series['id']:
            raise ValueError('En båd kan ikke flyttes til en anden serie.')
        if data.get('recalculate'):
            recalculate_boat(db, identity, ratings, extra, data, user, apply=True)
        profile_id = existing['profile_id']
        fleet.save_profile(db,{**dict(zip(fleet.FIELDS,values[2:])),'ratings':ratings,'extra_handicap':extra,'token':fleet.profile(db,profile_id)['token']},user,profile_id)
        set_id=fleet.profile(db,profile_id)['current_handicap_set_id']
        db.execute('UPDATE boats SET class_name=?,course=? WHERE id=?', (*values[:2], identity))
    else:
        profile_id = fleet.save_profile(db,{**dict(zip(fleet.FIELDS,values[2:])),'ratings':ratings,'extra_handicap':extra},user)
        identity = db.execute('INSERT INTO boats(series_id,class_name,course,profile_id) VALUES(?,?,?,?)', (series['id'], values[0], values[1], profile_id)).lastrowid
        set_id = db.execute('SELECT current_handicap_set_id FROM boat_profiles WHERE id=?', (profile_id,)).fetchone()[0]
    if not existing or (existing['course'], existing['class_name']) != (values[1], values[0]):
        hierarchy.attach_boat(db, identity)
    for pending in list(db.execute("SELECT r.id,r.rating_key FROM races r JOIN round_courses c ON c.id=r.round_course_id JOIN rounds p ON p.id=c.round_id WHERE r.series_id=? AND r.published=0 AND p.completed=0 AND NOT EXISTS(SELECT 1 FROM finishes f WHERE f.race_id=r.id AND f.status<>'DUTY')", (series['id'],))):
        old_entry = next((r for r in participation.rows(db, pending['id']) if r['boat_id'] == identity), None)
        if old_entry and old_entry['rating'] is None and (rating_value(ratings, pending['rating_key']) is not None):
            db.execute('UPDATE entries SET handicap_set_id=? WHERE race_id=? AND boat_id=?', (set_id, pending['id'], identity))
            participation.replace_roster(db, pending['id'])
    audit(db, user, 'boat.save', {'id': identity, **data, 'before': existing, 'profile_id': profile_id, 'handicap_set_id': set_id})
    return identity

def recalculate_boat(db, boat_id, ratings, extra_handicap, data, user, apply=False):
    """Explicit, previewed correction of snapshot ratings, never a side effect."""
    import hashlib
    ratings = {key: number(ratings.get(key), optional=True) for key in RATINGS}
    history = [{**dict(r), 'old_rating': next((e for e in participation.rows(db, r['id']) if e['boat_id'] == boat_id))['rating'], 'old_extra_handicap': next((e for e in participation.rows(db, r['id']) if e['boat_id'] == boat_id))['extra_handicap']} for r in db.execute('SELECT r.*,e.distance entry_distance FROM entries e JOIN races r ON r.id=e.race_id WHERE e.boat_id=? ORDER BY r.start,r.id', (boat_id,))]
    chosen = data.get('race_ids')
    if chosen is not None:
        if not isinstance(chosen, list) or not chosen or any((type(v) is not int for v in chosen)) or (not set(chosen) <= {r['id'] for r in history}):
            raise ValueError('Vælg mindst ét af bådens historiske ræs til genberegning.')
        history = [r for r in history if r['id'] in chosen]
    changes = []
    for race in history:
        key = race['rating_key']
        if key == 'import':
            key = data.get('historical_rating_keys', {}).get(str(race['id']))
            if key not in RATINGS:
                raise ValueError(f"Vælg udtrykkeligt DH-tal for den importerede sejlads {race['label']} ({race['start'][:10]}).")
        rating = rating_value(ratings, key)
        if rating is None:
            raise ValueError(f"{key} mangler til {race['label']}. Historikken er ikke ændret.")
        entries = participation.rows(db, race['id'])
        target = next((r for r in entries if r['boat_id'] == boat_id))
        timed = [r for r in entries if r['status'] == 'FIN']
        if race['mode'] == 'pursuit' and any((r['corrected'] is None for r in timed)):
            raise ValueError(f"{race['label']}: Historisk genberegning af jagtstart kræver måltider; rækkefølge alene kan ikke omregnes.")
        corrected = correction(target['elapsed'], rating, race['entry_distance'], race['reference']) + extra_handicap + target['penalty'] if target['elapsed'] is not None and target['status'] == 'FIN' else None
        changes.append({'race_id': race['id'], 'label': race['label'], 'date': race['start'][:10], 'published': bool(race['published']), 'revision': race['revision'], 'rating_key': key, 'old_rating': race['old_rating'], 'rating': rating, 'old_extra_handicap': race['old_extra_handicap'], 'extra_handicap': extra_handicap, 'old_corrected': target['corrected'], 'corrected': corrected, 'mode': race['mode'], 'redress_removed': any((r['status'] == 'RDG' for r in entries))})
    token = hashlib.sha256(json.dumps({'boat_id': boat_id, 'changes': changes}, sort_keys=True).encode()).hexdigest()
    if apply:
        if data.get('recalculation_token') != token:
            raise ValueError('Genberegningen skal først forhåndsvises. Data kan være ændret siden sidst.')
        for change in changes:
            race = get(db, 'races', change['race_id'])
            applied_ratings = {**ratings, 'import': change['rating']} if race['rating_key'] == 'import' else ratings
            set_id = handicaps.for_values(db, get(db, 'boats', boat_id)['profile_id'], applied_ratings, extra_handicap)
            db.execute('UPDATE entries SET handicap_set_id=? WHERE race_id=? AND boat_id=?', (set_id, race['id'], boat_id))
            db.execute("UPDATE finishes SET corrected=? WHERE race_id=? AND boat_id=? AND status='FIN'", (change['corrected'], race['id'], boat_id))
            timing.invalidate_redress(db, race['id'])
            if db.execute("SELECT 1 FROM finishes WHERE race_id=? AND status='DUTY'", (race['id'],)).fetchone():
                db.execute('UPDATE rounds SET completed=0,completed_at=NULL WHERE id=(SELECT round_id FROM round_courses WHERE id=?)', (race['round_course_id'],))
            db.execute('UPDATE finishes SET source_points=NULL,source_discarded=NULL WHERE race_id=?', (race['id'],))
            if race['mode'] == 'pursuit':
                fleet = participation.rows(db, race['id'])
                finished = [r for r in fleet if r['status'] == 'FIN']
                if finished:
                    for i, row in enumerate(sorted(finished, key=lambda r: (r['corrected'], r['boat_id'])), 1):
                        db.execute('UPDATE finishes SET sequence=? WHERE race_id=? AND boat_id=?', (i, race['id'], row['boat_id']))
                else:
                    selected = [Boat(str(r['boat_id']), r['skipper'], r['name'], r['sail_number'], r['class_name'], Decimal(str(r['rating']))) for r in fleet]
                    for start in calculate_starts(selected, Decimal(str(race['distance'])), common.instant(race['start'])):
                        extra = next((r['extra_handicap'] for r in fleet if r['boat_id'] == int(start.boat.identity)))
                        db.execute('UPDATE entries SET start=? WHERE race_id=? AND boat_id=?', ((start.time + timedelta(seconds=extra)).isoformat(), race['id'], int(start.boat.identity)))
            db.execute('UPDATE races SET published=0,revision=revision+1 WHERE id=?', (race['id'],))
        audit(db, user, 'boat.recalculate', {'boat_id': boat_id, 'changes': changes})
    return {'changes': changes, 'token': token, 'notice': 'Berørte sejladser bliver kladder igen. Redress fjernes og skal genberegnes. Faktiske måltider og historiske starttidspunkter bevares.'}
