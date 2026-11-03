"""Read models for series and race workspaces, independent of page rendering."""
import json
from .store import get
from . import hierarchy, timing, service


def round_status(db,round_id):
    planned=get(db,'rounds',round_id)
    races=timing.course_races(db,round_id)
    observations=db.execute('SELECT count(*) FROM finishes f JOIN races r ON r.id=f.race_id JOIN round_courses c ON c.id=r.round_course_id WHERE c.round_id=?',(round_id,)).fetchone()[0]
    observations+=db.execute('SELECT count(*) FROM pending_finishes WHERE round_id=?',(round_id,)).fetchone()[0]
    phase='finished' if planned['completed'] else 'live' if observations else 'ready' if races else 'planned'
    return {'phase':phase,'published':bool(races) and all(r['published'] for r in races),'participants':db.execute('SELECT count(*) FROM participations WHERE round_id=?',(round_id,)).fetchone()[0]}


def series_workspace(db,series_id):
    rounds=hierarchy.structure(db,series_id)
    for item in rounds:item['status']=round_status(db,item['id'])
    boats=service.boats(db,series_id)
    for boat in boats:boat['round_count']=db.execute('SELECT count(*) FROM participations WHERE boat_id=?',(boat['id'],)).fetchone()[0]
    return {'series':get(db,'series',series_id),'rounds':rounds,'registrations':boats}


def round_workspace(db,uid,history=False):
    row=db.execute('SELECT id FROM rounds WHERE uid=?',(uid,)).fetchone()
    if not row:raise ValueError('Ræset findes ikke.')
    result=timing.detail(db,row['id'])
    result['status']=round_status(db,row['id'])
    result['series']=get(db,'series',result['round']['series_id'])
    if history:
        race_ids={r['id'] for r in result['courses']}
        heat_ids={r[0] for r in db.execute('SELECT h.id FROM heats h JOIN round_courses c ON c.id=h.round_course_id WHERE c.round_id=?',(row['id'],))}
        def relevant(value):
            if isinstance(value,list):return any(relevant(v) for v in value)
            if not isinstance(value,dict):return False
            return value.get('round_id')==row['id'] or value.get('race_id') in race_ids or value.get('heat_id') in heat_ids or any(relevant(v) for v in value.values() if isinstance(v,(dict,list)))
        result['history']=[dict(r) for r in db.execute('SELECT a.*,u.name FROM audit a LEFT JOIN users u ON u.id=a.user_id ORDER BY a.id DESC') if relevant(json.loads(r['payload']))][:100]
    return result
