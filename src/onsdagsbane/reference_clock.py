"""A round's shared offset from the server clock to the tower's reference clock."""
from datetime import timedelta
import math
import secrets
from . import service
from .domain import correction, rounded
from .store import audit, get


def offset(db,round_id):
    row=db.execute('SELECT offset FROM round_clocks WHERE round_id=?',(round_id,)).fetchone()
    return row[0] if row else 0


def incoming(db,round_id,data,stamp):
    """Late automatic requests are normalized to the latest shared offset."""
    if not data.get('automatic'):return stamp
    current=offset(db,round_id)
    previous=float(data.get('clock_offset',current if data.get('instant') else 0))
    if not math.isfinite(previous):raise ValueError('Ugyldig urkorrektion.')
    return stamp+timedelta(seconds=current-previous)


def record_finish(db,round_id,boat_id,automatic):
    if automatic:db.execute('INSERT OR IGNORE INTO clock_sources(round_id,boat_id) VALUES(?,?)',(round_id,boat_id))


def forget_finish(db,round_id,boat_id):
    db.execute('DELETE FROM clock_sources WHERE round_id=? AND boat_id=?',(round_id,boat_id))


def record_capture(db,round_id,identity,automatic):
    if automatic:db.execute('INSERT OR IGNORE INTO clock_capture_sources(capture_id,round_id) VALUES(?,?)',(identity,round_id))


def capture_automatic(db,identity):
    return bool(db.execute('SELECT 1 FROM clock_capture_sources WHERE capture_id=?',(identity,)).fetchone())


def synchronize(db,round_id,data,user):
    from .timing import course_races
    planned=get(db,'rounds',round_id)
    courses=course_races(db,round_id)
    if planned['completed'] or any(r['published'] for r in courses):raise ValueError('Genåbn ræset, før uret synkroniseres.')
    before=offset(db,round_id)
    desired=float(data['offset'])
    if not math.isfinite(desired) or abs(desired)>86400:raise ValueError('Ugyldig urkorrektion.')
    if float(data['previous_offset'])!=before:raise ValueError('En anden bruger har synkroniseret uret. Opdatér siden og prøv igen.')
    delta=desired-before
    def shifted(value):return (service.instant(value)+timedelta(seconds=delta)).isoformat()
    changed_courses=set()
    for race in courses:
        for row in service.rows(db,race['id']):
            if not row['instant'] or not db.execute('SELECT 1 FROM clock_sources WHERE round_id=? AND boat_id=?',(round_id,row['boat_id'])).fetchone():continue
            stamp=shifted(row['instant'])
            elapsed=rounded((service.instant(stamp)-service.instant(row['start'])).total_seconds())
            if not 0<elapsed<=86400:raise ValueError(f"Urkorrektionen placerer {row['name']} uden for sejladsens tidsrum. Ingen tider er ændret.")
            corrected=correction(elapsed,row['rating'],row['distance'],race['reference'])+row['extra_handicap']+row['penalty']
            db.execute('UPDATE finishes SET instant=?,elapsed=?,corrected=? WHERE race_id=? AND boat_id=?',(stamp,elapsed,corrected,race['id'],row['boat_id']))
            changed_courses.add(race['id'])
    for row in list(db.execute('SELECT f.* FROM pending_finishes f JOIN clock_sources s ON s.round_id=f.round_id AND s.boat_id=f.boat_id WHERE f.round_id=? AND f.instant IS NOT NULL',(round_id,))):
        db.execute('UPDATE pending_finishes SET instant=?,revision=? WHERE round_id=? AND boat_id=?',(shifted(row['instant']),secrets.randbelow(2**50)+1,round_id,row['boat_id']))
    for row in list(db.execute('SELECT * FROM clock_capture_sources WHERE round_id=?',(round_id,))):
        table='round_captures' if row['capture_id']<0 else 'captures'
        value=db.execute(f'SELECT instant FROM {table} WHERE id=?',(abs(row['capture_id']),)).fetchone()
        if value:db.execute(f'UPDATE {table} SET instant=? WHERE id=?',(shifted(value['instant']),abs(row['capture_id'])))
    for race in courses:
        if race['id'] not in changed_courses:continue
        service.invalidate_redress(db,race['id'])
        if race['mode']=='pursuit':
            finished=[r for r in service.rows(db,race['id']) if r['status']=='FIN']
            if all(r['instant'] for r in finished):
                for sequence,row in enumerate(sorted(finished,key=lambda r:(service.instant(r['instant'])+timedelta(seconds=r['penalty']),r['sequence'])),1):
                    db.execute('UPDATE finishes SET sequence=? WHERE race_id=? AND boat_id=?',(sequence,race['id'],row['boat_id']))
        service.changed(db,race['id'])
    db.execute('INSERT INTO round_clocks(round_id,offset) VALUES(?,?) ON CONFLICT(round_id) DO UPDATE SET offset=excluded.offset',(round_id,desired))
    audit(db,user,'round.clock',{'round_id':round_id,'before':before,'offset':desired,'delta':delta,'race_ids':sorted(changed_courses)})
    return {'offset':desired,'delta':delta}
