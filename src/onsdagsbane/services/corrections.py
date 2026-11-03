"""Corrections application operations."""
from . import common, participation, timing
from datetime import timedelta
from decimal import Decimal
import json
import re
from omvendt_respit.starts import Boat, calculate_starts
from ..domain import RATINGS, correction, number, rounded
from ..store import audit, get

def set_reference(db, race_id, value, user, revision):
    race = get(db, 'races', race_id)
    if race['revision'] != int(revision):
        raise ValueError('Sejladsen er ændret. Opdatér siden før referenceskift.')
    value = number(value)
    for entry in participation.rows(db, race_id):
        shift = rounded((Decimal(str(value)) - Decimal(str(race['reference']))) * Decimal(str(entry['distance'])))
        db.execute('UPDATE finishes SET corrected=corrected+? WHERE race_id=? AND boat_id=? AND corrected IS NOT NULL', (shift, race_id, entry['boat_id']))
    db.execute('UPDATE races SET reference=?,revision=revision+1 WHERE id=?', (value, race_id))
    audit(db, user, 'race.reference', {'race_id': race_id, 'old': race['reference'], 'new': value})

def day_settings(db, data, user):
    """Preview, then explicitly align course distance and the day's wind category."""
    import hashlib
    day = str(data['date'])
    if not re.fullmatch('\\d{4}-\\d{2}-\\d{2}', day) or data['course'] not in ('lille', 'stor'):
        raise ValueError('Vælg en gyldig sejladsdag og bane.')
    wind = data.get('wind_category', '')
    if wind not in ('', 'L', 'M', 'H'):
        raise ValueError('Ukendt vindkategori.')
    route = data.get('route', [])
    distance = common.geometry(route)['distance'] if route else None
    if not wind and distance is None:
        raise ValueError('Vælg en vindkategori eller en mærkerækkefølge.')
    races = [dict(r) for r in db.execute('SELECT * FROM races WHERE series_id=? AND substr(start,1,10)=? ORDER BY id', (int(data['series_id']), day))]
    if data.get('round_uid'):
        course_ids={r[0] for r in db.execute('SELECT c.id FROM round_courses c JOIN rounds p ON p.id=c.round_id WHERE p.uid=? AND p.series_id=?',(data['round_uid'],int(data['series_id'])))}
        if not course_ids:raise ValueError('Ræset tilhører ikke den valgte serie.')
        races=[r for r in races if r['round_course_id'] in course_ids]
    if wind and races and all((r['rating_key'] == 'GPH' for r in races)):
        raise ValueError('Vindkategori kan ikke indstilles ved GPH.')
    changes = []
    for race in races:
        effective_wind = wind if race['rating_key'] != 'GPH' else ''
        if race['rating_key'] == 'GPH' and distance is None:
            continue
        if not effective_wind and race['course'] != data['course']:
            continue
        key = race['rating_key'][:4] + effective_wind if effective_wind else race['rating_key']
        if key not in (*RATINGS, 'GPH'):
            raise ValueError('Sejladsen mangler en kendt DH-kategori.')
        entries = participation.rows(db, race['id'])
        from ..compatibility import issues_for, HandicapError
        round_id=db.execute('SELECT round_id FROM round_courses WHERE id=?',(race['round_course_id'],)).fetchone()[0]
        issues=issues_for(db,[e['boat_id'] for e in entries],key,round_id,race['id'])
        if issues:raise HandicapError(issues)
        if race['mode'] == 'pursuit' and any((r['status'] == 'FIN' and (not r['instant']) for r in entries)):
            raise ValueError(f"{race['label']}: Jagtstartens opsætning kan kun genberegnes efter målgang, når der findes måltider.")
        updates = []
        for entry in entries:
            rating = (entry['handicap_set']['ratings'].get(key) if key != race['rating_key'] else entry['rating']) if effective_wind else entry['rating']
            if rating is None:
                raise ValueError(f"{entry['name']} mangler et historisk {key} for {race['label']}. Ret beregningsgrundlaget for den gamle sejlads først. Nutidens stamdata bruges ikke som erstatning.")
            new_distance = distance if distance is not None and race['course'] == data['course'] else entry['distance']
            corrected = correction(entry['elapsed'], rating, new_distance, race['reference']) + entry['extra_handicap'] + entry['penalty'] if entry['status'] == 'FIN' and entry['elapsed'] is not None else None
            updates.append({'boat_id': entry['boat_id'], 'name': entry['name'], 'class_name': entry['class_name'], 'old_distance': entry['distance'], 'distance': new_distance, 'old_wind': entry['wind_category'], 'wind': effective_wind or entry['wind_category'], 'old_rating': entry['rating'], 'rating': rating, 'old_corrected': entry['corrected'], 'corrected': corrected})
        changes.append({'race_id': race['id'], 'label': race['label'], 'course': race['course'], 'revision': race['revision'], 'rating_key': key, 'distance':distance if distance is not None and race['course']==data['course'] else race['distance'], 'updates': updates})
    if not changes:
        raise ValueError('Der findes ingen sejladser på den valgte dag.')
    token = hashlib.sha256(json.dumps(changes, sort_keys=True).encode()).hexdigest()
    if data.get('apply'):
        if data.get('token') != token:
            raise ValueError('Forhåndsvis ændringerne igen. Sejladsdata kan være ændret.')
        for change in changes:
            race_id = change['race_id']
            for update in change['updates']:
                db.execute('UPDATE entries SET distance=?,wind_category=? WHERE race_id=? AND boat_id=?', (update['distance'], update['wind'], race_id, update['boat_id']))
                db.execute("UPDATE finishes SET corrected=? WHERE race_id=? AND boat_id=? AND status='FIN'", (update['corrected'], race_id, update['boat_id']))
            timing.invalidate_redress(db, race_id)
            db.execute('UPDATE finishes SET source_points=NULL,source_discarded=NULL WHERE race_id=?', (race_id,))
            race = get(db, 'races', race_id)
            if race['mode'] == 'pursuit' and change['updates']:
                entries = participation.rows(db, race_id)
                finished = [r for r in entries if r['status'] == 'FIN']
                if finished:
                    for i, row in enumerate(sorted(finished, key=lambda r: (r['corrected'], r['boat_id'])), 1):
                        db.execute('UPDATE finishes SET sequence=? WHERE race_id=? AND boat_id=?', (i, race_id, row['boat_id']))
                else:
                    fleet_for_start = [Boat(str(r['boat_id']), r['skipper'], r['name'], r['sail_number'], r['class_name'], Decimal(str(r['rating']))) for r in entries]
                    new_distance = change['updates'][0]['distance']
                    for start in calculate_starts(fleet_for_start, Decimal(str(new_distance)), common.instant(race['start'])):
                        extra_handicap = next((r['extra_handicap'] for r in entries if r['boat_id'] == int(start.boat.identity)))
                        db.execute('UPDATE entries SET start=? WHERE race_id=? AND boat_id=?', ((start.time + timedelta(seconds=extra_handicap)).isoformat(), race_id, int(start.boat.identity)))
            new_route = json.dumps(route) if route and race['course'] == data['course'] else race['route']
            new_distance = change['updates'][0]['distance'] if change['updates'] else change['distance']
            warning = '' if len({r['start'] for r in participation.rows(db, race_id)}) == 1 else 'Løbene har forskellige starttider; samlet visning er vejledende.'
            db.execute('UPDATE races SET rating_key=?,distance=?,route=?,published=0,revision=revision+1,warning=? WHERE id=?', (change['rating_key'], new_distance, new_route, warning, race_id))
        audit(db, user, 'day.settings', {'date': day, 'changes': changes})
    return {'changes': changes, 'token': token, 'notice': 'Ændringer genberegner dagens tider, fjerner redress og gør de berørte sejladser til kladder. Kontrollér og offentliggør dem igen.'}
