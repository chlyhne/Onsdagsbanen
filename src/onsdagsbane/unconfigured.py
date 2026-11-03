"""Persist actual observations before a course's calculation setup is known."""
from datetime import datetime
import secrets
from . import service
from .store import audit, get


def editable(db,round_id):
    if get(db,'rounds',round_id)['completed']:
        raise ValueError('Ræset er afsluttet. Genåbn måltagningen først.')


def key(data):
    value=str(data.get('request_id',''))
    if not 16<=len(value)<=100:raise ValueError('Registreringen mangler en gyldig nøgle.')
    return value


def capture(db,round_id,data,user):
    editable(db,round_id)
    request=key(data)
    old=db.execute('SELECT * FROM round_captures WHERE request_id=?',(request,)).fetchone()
    if old:
        if old['round_id']!=round_id:raise ValueError('Tidsstemplet tilhører et andet ræs.')
        return {**dict(old),'id':-old['id'],'race_id':None}
    stamp=service.instant(data['instant']) if data.get('instant') else datetime.now(service.TZ)
    from . import reference_clock as clock
    stamp=clock.incoming(db,round_id,data,stamp)
    identity=db.execute('INSERT INTO round_captures(round_id,instant,request_id) VALUES(?,?,?)',(round_id,stamp.isoformat(),request)).lastrowid
    clock.record_capture(db,round_id,-identity,bool(data.get('automatic')))
    audit(db,user,'round.capture',{'round_id':round_id,'instant':stamp.isoformat()})
    return {'id':-identity,'race_id':None,'instant':stamp.isoformat(),'request_id':request}


def usable_capture(db,round_id,identity):
    if identity<0:
        row=db.execute('SELECT * FROM round_captures WHERE id=?',(-identity,)).fetchone()
        if not row or row['used'] or row['round_id']!=round_id:
            raise ValueError('Tidsstemplet er allerede brugt eller tilhører et andet ræs.')
        return {**dict(row),'id':identity,'race_id':None}
    row=get(db,'captures',identity)
    from .timing import round_id_for_race
    if row['used'] or round_id_for_race(db,row['race_id'])!=round_id:
        raise ValueError('Tidsstemplet er allerede brugt eller tilhører et andet ræs.')
    return row


def consume(db,identity):
    table='round_captures' if identity<0 else 'captures'
    db.execute(f'UPDATE {table} SET used=1 WHERE id=?',(abs(identity),))


def discard(db,round_id,data,user):
    editable(db,round_id)
    cap=usable_capture(db,round_id,int(data['id']))
    consume(db,cap['id'])
    audit(db,user,'round.capture.discard',{'round_id':round_id,'id':cap['id']})
    return {'saved':True}


def finish(db,round_id,data,user,action='finish'):
    editable(db,round_id)
    boat_id=int(data['boat_id'])
    entry=db.execute('SELECT * FROM participations WHERE round_id=? AND boat_id=?',(round_id,boat_id)).fetchone()
    if not entry:raise ValueError('Båden er ikke tilmeldt dette ræs.')
    request=key(data) if action!='undo' else None
    if request:
        repeated=db.execute('SELECT round_id,boat_id FROM pending_finishes WHERE request_id=? UNION ALL SELECT c.round_id,f.boat_id FROM finishes f JOIN races r ON r.id=f.race_id JOIN round_courses c ON c.id=r.round_course_id WHERE f.request_id=?',(request,request)).fetchone()
        if repeated:
            if repeated['round_id']!=round_id or repeated['boat_id']!=boat_id:raise ValueError('Registreringen tilhører en anden båd eller et andet ræs.')
            return {'saved':True}
    if entry['race_id']:
        race_id=entry['race_id']
        if action in ('edit','undo'):service.undo(db,race_id,boat_id,user,data['revision'])
        return service.finish(db,race_id,data,user) if action!='undo' else {'saved':True}
    old=db.execute('SELECT * FROM pending_finishes WHERE round_id=? AND boat_id=?',(round_id,boat_id)).fetchone()
    if action in ('edit','undo'):
        if not old or old['revision']!=int(data['revision']):raise ValueError('Registreringen er ændret. Åbn Ret igen.')
        if action=='undo':
            from .reference_clock import forget_finish
            forget_finish(db,round_id,boat_id)
            db.execute('DELETE FROM pending_finishes WHERE round_id=? AND boat_id=?',(round_id,boat_id))
            audit(db,user,'round.finish.undo',dict(old))
            return {'saved':True}
    if old and action!='edit':raise ValueError('Båden er allerede registreret. Brug Ret eller Fortryd.')
    status=data.get('status','FIN')
    if status not in service.PENALTIES|{'FIN','DUTY'}:raise ValueError('Ukendt resultatstatus.')
    penalty=service.extra_handicap_value(data.get('penalty',0))
    if status!='FIN' and penalty:raise ValueError('Straftid kræver en måltid.')
    cap=usable_capture(db,round_id,int(data['capture_id'])) if data.get('capture_id') else None
    instant=data.get('instant') or (cap['instant'] if cap else None)
    if status=='FIN' and not instant:raise ValueError('Angiv en måltid.')
    stamp=service.instant(instant).isoformat() if status=='FIN' else None
    from . import reference_clock as clock
    automatic=bool(data.get('automatic',clock.capture_automatic(db,cap['id']) if cap else False))
    if stamp:
        clock_data={**data,'automatic':automatic}
        if cap and not data.get('instant'):clock_data['clock_offset']=clock.offset(db,round_id)
        stamp=clock.incoming(db,round_id,clock_data,service.instant(stamp)).isoformat()
    sequence=old['sequence'] if old else db.execute('SELECT coalesce(max(sequence),0)+1 FROM pending_finishes WHERE round_id=?',(round_id,)).fetchone()[0]
    if old:db.execute('DELETE FROM pending_finishes WHERE round_id=? AND boat_id=?',(round_id,boat_id))
    clock.forget_finish(db,round_id,boat_id)
    db.execute('INSERT INTO pending_finishes(round_id,boat_id,status,instant,penalty,sequence,request_id,revision) VALUES(?,?,?,?,?,?,?,?)',(round_id,boat_id,status,stamp,penalty,sequence,request,secrets.randbelow(2**50)+1))
    if cap:consume(db,cap['id'])
    clock.record_finish(db,round_id,boat_id,automatic and status=='FIN')
    audit(db,user,'round.finish.'+action,{'round_id':round_id,**data})
    return {'saved':True}


def apply(db,round_id,race_id,user):
    """Attach saved observations to the configured course without changing their instants."""
    for row in list(db.execute('SELECT f.* FROM pending_finishes f JOIN participations p ON p.round_id=f.round_id AND p.boat_id=f.boat_id WHERE f.round_id=? AND p.race_id=? ORDER BY f.sequence',(round_id,race_id))):
        from . import reference_clock as clock
        automatic=bool(db.execute('SELECT 1 FROM clock_sources WHERE round_id=? AND boat_id=?',(round_id,row['boat_id'])).fetchone())
        service.finish(db,race_id,{**dict(row),'automatic':automatic,'clock_offset':clock.offset(db,round_id)},user)
        db.execute('DELETE FROM pending_finishes WHERE round_id=? AND boat_id=?',(round_id,row['boat_id']))
