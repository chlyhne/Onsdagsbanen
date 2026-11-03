"""Explicit historical corrections using the saved race basis, never current boats."""
import hashlib
import json
from .domain import RATINGS, number, correction, gph
from .service import rows, extra_handicap_value, invalidate_redress, changed
from .store import get, audit
from . import handicaps


def recalculate(db,race_id,data,user,apply=False):
    race=get(db,'races',race_id)
    entries=rows(db,race_id)
    if not entries:raise ValueError('Sejladsen har ingen gemte deltagere.')
    boat_id=int(data['boat_id']) if data.get('boat_id') else None
    target=next((r for r in entries if r['boat_id']==boat_id),None)
    if boat_id and not target:raise ValueError('Båden var ikke tilmeldt denne sejlads. Vælg blandt de historiske deltagere.')
    note=str(data.get('note','')).strip()
    if not note or len(note)>500:raise ValueError('Beskriv årsagen til genberegningen, højst 500 tegn.')
    snapshot=None;rating=extra=None
    if target:
        snapshot=dict(target['handicap_set']['ratings'])
        selected_set=None
        if data.get('handicap_set_id'):
            selected_set=db.execute('SELECT * FROM handicap_sets WHERE id=? AND profile_id=?',(int(data['handicap_set_id']),target['profile_id'])).fetchone()
            if not selected_set:raise ValueError('Handicapsættet skal tilhøre denne båd.')
            snapshot=json.loads(selected_set['ratings'])
        provided={} if selected_set else data.get('ratings',{})
        if not isinstance(provided,dict):raise ValueError('Ugyldige historiske handicaptal.')
        for key in RATINGS:
            if key in provided:snapshot[key]=number(provided[key],optional=True)
        if race['rating_key']=='GPH':
            rating=gph(snapshot) if provided or selected_set else target['rating']
            if rating is None:raise ValueError('Historisk GPH kræver TACIL, TACIM og TACIH fra denne sejlads.')
        else:
            rating=number(data.get('rating',snapshot.get(race['rating_key'],target['rating'])))
        snapshot[race['rating_key']]=rating
        extra=selected_set['extra_handicap'] if selected_set else extra_handicap_value(data.get('extra_handicap',target['extra_handicap']))
    changes=[]
    for entry in entries:
        selected=entry['boat_id']==boat_id
        new_rating=rating if selected else entry['rating']
        new_extra=extra if selected else entry['extra_handicap']
        corrected=None
        if entry['status']=='FIN':
            if entry['elapsed'] is None:raise ValueError('Genberegning kræver gemte måltider. En jagtstarts rækkefølge alene kan ikke omregnes til tider.')
            if new_rating is None:raise ValueError(f"{entry['name']} mangler historisk handicap. Nutidens stamdata bruges ikke som erstatning.")
            corrected=correction(entry['elapsed'],new_rating,entry['distance'],race['reference'])+new_extra+entry['penalty']
        changes.append({'boat_id':entry['boat_id'],'name':entry['name'],'class_name':entry['class_name'],'status':entry['status'],
            'old_rating':entry['rating'],'rating':new_rating,'old_extra_handicap':entry['extra_handicap'],'extra_handicap':new_extra,
            'distance':entry['distance'],'start':entry['start'],'old_corrected':entry['corrected'],'corrected':corrected})
    basis={'race_id':race_id,'revision':race['revision'],'reference':race['reference'],'changes':changes,'snapshot':snapshot,'previous_snapshot':dict(target['handicap_set']['ratings']) if target else None,'note':note}
    token=hashlib.sha256(json.dumps(basis,sort_keys=True).encode()).hexdigest()
    if apply:
        if data.get('token')!=token:raise ValueError('Forhåndsvis genberegningen igen. Grundlaget kan være ændret.')
        if target:
            profile=db.execute('SELECT profile_id FROM boats WHERE id=?',(boat_id,)).fetchone()[0]
            set_id=handicaps.for_values(db,profile,snapshot,extra)
            db.execute('UPDATE entries SET handicap_set_id=? WHERE race_id=? AND boat_id=?',(set_id,race_id,boat_id))
        for change in changes:
            if change['status']=='FIN':
                db.execute('UPDATE finishes SET corrected=? WHERE race_id=? AND boat_id=?',(change['corrected'],race_id,change['boat_id']))
        invalidate_redress(db,race_id)
        if db.execute("SELECT 1 FROM finishes WHERE race_id=? AND status='DUTY'",(race_id,)).fetchone():
            db.execute('UPDATE rounds SET completed=0,completed_at=NULL WHERE id=(SELECT round_id FROM round_courses WHERE id=?)',(race['round_course_id'],))
        db.execute('UPDATE races SET published=0 WHERE id=?',(race_id,))
        changed(db,race_id)
        audit(db,user,'race.recalculate',{**basis,'boat_id':boat_id})
    return {'changes':changes,'token':token,'notice':'Kun denne sejlads genberegnes. Gemte deltagere, starttider og måltider bevares. Stamdata og andre sejladser ændres ikke. Resultaterne bliver en kladde til ny offentliggørelse.'}
