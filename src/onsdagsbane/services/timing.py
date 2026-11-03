"""Timing application operations."""
from . import common, participation
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from ..domain import PENALTIES, correction, rounded
from ..store import audit, get
TZ = ZoneInfo('Europe/Copenhagen')


def editable(db, race_id, revision=None, allow_completed=False):
    race = get(db, 'races', race_id)
    if not allow_completed:
        closed = db.execute('SELECT p.completed FROM rounds p JOIN round_courses c ON c.round_id=p.id WHERE c.id=?', (race['round_course_id'],)).fetchone()
        if closed and closed[0]:
            raise ValueError('Ræset er afsluttet. Genåbn måltagningen for begge baner først.')
    if race['published']:
        raise ValueError('Sejladsen er offentliggjort. Åbn den for rettelser først.')
    if revision is not None and int(revision) != race['revision']:
        raise ValueError('En anden bruger har ændret sejladsen. Opdatér siden og prøv igen.')
    return race

def invalidate_redress(db, race_id):
    db.execute("UPDATE finishes SET status='DUTY',instant=NULL,elapsed=NULL,corrected=NULL,note='Dommertjans · afventer beregning efter sejladsen',source_points=NULL,source_discarded=NULL WHERE race_id=? AND status='RDG'", (race_id,))

def changed(db, race_id):
    db.execute('UPDATE races SET revision=revision+1 WHERE id=?', (race_id,))
    db.execute('UPDATE finishes SET source_points=NULL,source_discarded=NULL WHERE race_id=?', (race_id,))

def capture(db, race_id, data, user, now=None):
    request_id = str(data.get('request_id', ''))
    if not 16 <= len(request_id) <= 100:
        raise ValueError('Registreringen mangler en gyldig nøgle.')
    old = db.execute('SELECT * FROM captures WHERE request_id=?', (request_id,)).fetchone()
    if old:
        if old['race_id'] != race_id:
            raise ValueError('Registreringen tilhører en anden sejlads.')
        return dict(old)
    race = editable(db, race_id)
    stamp = common.instant(data['instant']) if data.get('instant') else now or datetime.now(TZ)
    from .. import timing as rounds, reference_clock as clock
    round_id=rounds.round_id_for_race(db,race_id)
    stamp=clock.incoming(db,round_id,data,stamp)
    if stamp < common.instant(race['start']):
        raise ValueError('Sejladsen er ikke startet endnu.')
    if (stamp - common.instant(race['start'])).total_seconds() > 86400:
        raise ValueError('Tiden skal ligge inden for 24 timer efter start. Kontrollér dato og klokkeslæt.')
    cur = db.execute('INSERT INTO captures(race_id,instant,request_id) VALUES(?,?,?)', (race_id, stamp.isoformat(), request_id))
    clock.record_capture(db,round_id,cur.lastrowid,bool(data.get('automatic')))
    audit(db, user, 'capture', {'race_id': race_id, 'id': cur.lastrowid, 'instant': stamp.isoformat()})
    return get(db, 'captures', cur.lastrowid)

def usable_capture(db, race_id, capture_id):
    if capture_id < 0:
        from .. import timing, unconfigured
        return unconfigured.usable_capture(db, timing.round_id_for_race(db,race_id), capture_id)
    cap = get(db, 'captures', capture_id)
    same_round = db.execute('SELECT 1 FROM races a JOIN round_courses ac ON ac.id=a.round_course_id JOIN races b ON b.id=? JOIN round_courses bc ON bc.id=b.round_course_id WHERE a.id=? AND ac.round_id=bc.round_id', (race_id, cap['race_id'])).fetchone()
    if not same_round or cap['used']:
        raise ValueError('Tidsstemplet er allerede brugt eller tilhører et andet ræs.')
    return cap

def finish(db, race_id, data, user):
    request_id = str(data.get('request_id', ''))
    if not 16 <= len(request_id) <= 100:
        raise ValueError('Registreringen mangler en gyldig nøgle.')
    old = db.execute('SELECT * FROM finishes WHERE request_id=?', (request_id,)).fetchone()
    if old:
        if old['race_id'] != race_id or old['boat_id'] != int(data['boat_id']):
            raise ValueError('Registreringen tilhører en anden båd eller sejlads.')
        return dict(old)
    race = editable(db, race_id)
    boat_id = int(data['boat_id'])
    entry = next((row for row in participation.rows(db, race_id) if row['boat_id'] == boat_id), None)
    if not entry:
        raise ValueError('Båden er ikke tilmeldt denne sejlads.')
    if db.execute('SELECT 1 FROM finishes WHERE race_id=? AND boat_id=?', (race_id, boat_id)).fetchone():
        raise ValueError('Båden er allerede registreret. Brug Ret eller Fortryd.')
    status = data.get('status', 'FIN')
    if status == 'DUTY' and (not user or not user.get('approved')):
        raise PermissionError('Log ind som godkendt bruger for at markere dommertjans.')
    if status not in PENALTIES | {'FIN', 'DUTY'}:
        raise ValueError('Ukendt resultatstatus.')
    penalty = common.extra_handicap_value(data.get('penalty', 0))
    if status != 'FIN' and penalty:
        raise ValueError('Straftid kan kun knyttes til en tidsmålt målgang.')
    elapsed = corrected = stamp = None
    cap = None
    if status == 'DUTY' and data.get('capture_id'):
        cap = usable_capture(db, race_id, int(data['capture_id']))
    if status == 'FIN' and (not entry['start_ready']):
        from ..compatibility import issues_for, HandicapError
        from ..timing import round_id_for_race
        raise HandicapError(issues_for(db,[r['boat_id'] for r in participation.rows(db,race_id)],race['rating_key'],round_id_for_race(db,race_id),race_id))
    if status == 'FIN':
        if data.get('capture_id'):
            cap = usable_capture(db, race_id, int(data['capture_id']))
            stamp = common.instant(data['instant']) if data.get('instant') else common.instant(cap['instant'])
        elif data.get('instant'):
            stamp = common.instant(data['instant'])
        elif race['mode'] != 'pursuit':
            raise ValueError('Angiv en måltid eller tag tiden nu.')
        if stamp:
            from .. import timing as rounds, reference_clock as clock
            round_id=rounds.round_id_for_race(db,race_id)
            automatic=bool(data.get('automatic',clock.capture_automatic(db,cap['id']) if cap else False))
            clock_data={**data,'automatic':automatic}
            if cap and not data.get('instant'):clock_data['clock_offset']=clock.offset(db,round_id)
            stamp=clock.incoming(db,round_id,clock_data,stamp)
            elapsed = rounded((stamp - common.instant(entry['start'])).total_seconds())
            if elapsed <= 0 or elapsed > 86400:
                raise ValueError('Måltiden skal ligge efter bådens start og inden for 24 timer. Kontrollér dato og klokkeslæt.')
            if entry['rating'] is None:
                from ..compatibility import issues_for, HandicapError
                raise HandicapError(issues_for(db,[boat_id],race['rating_key'],round_id,race_id))
            corrected = correction(elapsed, entry['rating'], entry['distance'], race['reference']) + entry['extra_handicap'] + penalty
    if penalty and (not stamp):
        raise ValueError('Straftid kræver en måltid.')
    if race['mode'] == 'pursuit' and status == 'FIN':
        other = [r for r in participation.rows(db, race_id) if r['status'] == 'FIN']
        if (penalty or any((r['penalty'] for r in other))) and (not stamp or any((not r['instant'] for r in other))):
            raise ValueError('Straftid ved jagtstart kræver måltider for alle målgangsbåde.')
    sequence = db.execute('SELECT coalesce(max(sequence),0)+1 FROM finishes WHERE race_id=?', (race_id,)).fetchone()[0]
    db.execute('INSERT INTO finishes(race_id,boat_id,status,instant,elapsed,corrected,sequence,note,request_id,penalty) VALUES(?,?,?,?,?,?,?,?,?,?)', (race_id, boat_id, status, stamp.isoformat() if stamp else None, elapsed, corrected, sequence, str(data.get('note', ''))[:500], request_id, penalty))
    if stamp:
        clock.record_finish(db,round_id,boat_id,automatic)
    if cap:
        from ..unconfigured import consume
        consume(db,cap['id'])
    if status != 'DUTY':
        invalidate_redress(db, race_id)
    if race['mode'] == 'pursuit':
        finished = [r for r in participation.rows(db, race_id) if r['status'] == 'FIN']
        if finished and all((r['instant'] for r in finished)):
            for i, row in enumerate(sorted(finished, key=lambda r: (common.instant(r['instant']) + timedelta(seconds=r['penalty']), r['sequence'])), 1):
                db.execute('UPDATE finishes SET sequence=? WHERE race_id=? AND boat_id=?', (i, race_id, row['boat_id']))
    changed(db, race_id)
    audit(db, user, 'finish.add', {'race_id': race_id, **data})
    return {'saved': True}

def undo(db, race_id, boat_id, user, revision):
    row = db.execute('SELECT * FROM finishes WHERE race_id=? AND boat_id=?', (race_id, boat_id)).fetchone()
    if not row:
        raise ValueError('Resultatet findes ikke længere.')
    editable(db, race_id, revision, allow_completed=row['status'] == 'RDG')
    audit(db, user, 'finish.undo', dict(row))
    from ..timing import round_id_for_race
    from ..reference_clock import forget_finish
    forget_finish(db,round_id_for_race(db,race_id),boat_id)
    db.execute('DELETE FROM finishes WHERE race_id=? AND boat_id=?', (race_id, boat_id))
    if row['status'] != 'RDG':
        invalidate_redress(db, race_id)
    changed(db, race_id)

def publish(db, race_id, data, user):
    race = get(db, 'races', race_id)
    if race['revision'] != int(data['revision']):
        raise ValueError('Sejladsen er ændret. Gennemgå resultaterne igen.')
    value = bool(data['published'])
    if value:
        roster = participation.rows(db, race_id)
        if any((r['status'] == 'DUTY' for r in roster)):
            raise ValueError('Afslut ræset for at beregne dommertid automatisk før offentliggørelse.')
        if not roster or not any((r['sequence'] is not None for r in roster)):
            raise ValueError('Der skal være mindst ét registreret resultat.')
        if db.execute('SELECT 1 FROM captures WHERE race_id=? AND used=0', (race_id,)).fetchone():
            raise ValueError('Der er tidsstempler uden båd. Tildel eller kassér dem før offentliggørelse.')
        if any((r['sequence'] is None for r in roster)) and (not data.get('confirm_dnc')):
            raise ValueError('Bekræft at både uden resultat får DNC.')
    db.execute('UPDATE races SET published=?,revision=revision+1 WHERE id=?', (int(value), race_id))
    audit(db, user, 'race.publish', {'race_id': race_id, 'published': value})
