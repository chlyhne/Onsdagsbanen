"""Series application operations."""
from . import corrections
import json
from ..domain import number
from ..store import audit, get
from .. import hierarchy, planning

def create_series(db, data, user):
    name = str(data.get('name', '')).strip()
    year = int(data['year'])
    thresholds = data.get('discards', [])
    if not name or len(name) > 100 or (not 2020 <= year <= 2200):
        raise ValueError('Angiv serienavn og et gyldigt årstal.')
    if not isinstance(thresholds, list) or any((type(t) is not int or t < 2 for t in thresholds)) or len(set(thresholds)) != len(thresholds):
        raise ValueError('Fratræk skal være forskellige antal sejladser, mindst 2.')
    cur = db.execute('INSERT INTO series(name,year,discards,reference) VALUES(?,?,?,?)', (name, year, json.dumps(sorted(thresholds)), number(data.get('reference', 1000))))
    times = planning.defaults({}, data)
    db.execute('UPDATE series SET default_start_stor=?,default_start_lille=? WHERE id=?', (times['stor'], times['lille'], cur.lastrowid))
    hierarchy.create_rounds(db, cur.lastrowid, data.get('round_labels', 'R1-R18'), user, data.get('fleets'))
    audit(db, user, 'series.create', data)
    return cur.lastrowid

def update_series(db, identity, data, user):
    before = get(db, 'series', identity)
    name = str(data.get('name', before['name'])).strip()
    year = int(data.get('year', before['year']))
    thresholds = data.get('discards', json.loads(before['discards']))
    if not name or len(name) > 100 or (not 2020 <= year <= 2200):
        raise ValueError('Angiv serienavn og et gyldigt årstal.')
    if not isinstance(thresholds, list) or any((type(t) is not int or t < 2 for t in thresholds)) or len(set(thresholds)) != len(thresholds):
        raise ValueError('Fratræk skal være forskellige antal sejladser, mindst 2.')
    reference = number(data.get('reference', before['reference']))
    times = planning.defaults(before, data)
    db.execute('UPDATE series SET name=?,year=?,discards=?,reference=?,default_start_stor=?,default_start_lille=? WHERE id=?', (name, year, json.dumps(sorted(thresholds)), reference, times['stor'], times['lille'], identity))
    audit(db, user, 'series.update', {'id': identity, 'before': before, 'name': name, 'year': year, 'discards': sorted(thresholds), 'reference': reference, 'start_times': times})

def series_reference(db, series_id, data, user):
    get(db, 'series', series_id)
    value = number(data['reference'])
    history = [dict(r) for r in db.execute('SELECT id,revision FROM races WHERE series_id=?', (series_id,))]
    if data.get('apply_all'):
        revisions = data.get('revisions', {})
        if any((revisions.get(str(r['id'])) != r['revision'] for r in history)):
            raise ValueError('En sejlads er ændret eller tilføjet. Opdatér før ensretning af referencer.')
        for row in history:
            corrections.set_reference(db, row['id'], value, user, row['revision'])
    db.execute('UPDATE series SET reference=? WHERE id=?', (value, series_id))
    audit(db, user, 'series.reference', {'series_id': series_id, 'reference': value, 'apply_all': bool(data.get('apply_all'))})
