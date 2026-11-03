"""Canonical boat-owned handicap objects, including unused historical sets."""
import json
from datetime import datetime, timezone
from .domain import RATINGS, gph, number


def for_values(db,profile_id,ratings,extra_handicap):
    if not profile_id or not db.execute('SELECT 1 FROM boat_profiles WHERE id=?',(profile_id,)).fetchone():
        raise ValueError('Et handicapsæt skal tilhøre en båd.')
    values={key:number(ratings.get(key),optional=True) for key in RATINGS}
    values.update({key:number(value,optional=True) for key,value in ratings.items() if key not in RATINGS})
    if gph(values) is not None:values.pop('GPH',None)
    encoded=json.dumps(values,sort_keys=True,separators=(',',':'))
    found=db.execute('SELECT id FROM handicap_sets WHERE profile_id=? AND ratings=? AND extra_handicap=?',(profile_id,encoded,extra_handicap)).fetchone()
    if found:return found[0]
    return db.execute('INSERT INTO handicap_sets(profile_id,ratings,extra_handicap,created_at) VALUES(?,?,?,?)',(profile_id,encoded,extra_handicap,datetime.now(timezone.utc).isoformat())).lastrowid


def migrate(db):
    for row in list(db.execute('SELECT e.*,b.profile_id,r.rating_key FROM entries e JOIN boats b ON b.id=e.boat_id JOIN races r ON r.id=e.race_id WHERE e.handicap_set_id IS NULL')):
        row=dict(row)
        ratings=json.loads(row.get('ratings_snapshot') or '{}')
        ratings.setdefault(row['rating_key'],row.get('rating'))
        identity=for_values(db,row['profile_id'],ratings,row['extra_handicap'])
        db.execute('UPDATE entries SET handicap_set_id=? WHERE race_id=? AND boat_id=?',(identity,row['race_id'],row['boat_id']))


def owned(db,boat_id):
    boat=db.execute('SELECT profile_id FROM boats WHERE id=?',(boat_id,)).fetchone()
    if not boat:raise ValueError('Båden findes ikke.')
    result=[]
    for row in db.execute('SELECT * FROM handicap_sets WHERE profile_id=? ORDER BY id DESC',(boat[0],)):
        item=dict(row);item['ratings']=json.loads(item['ratings']);item['gph']=gph(item['ratings'])
        item['races']=[dict(r) for r in db.execute('SELECT r.id,r.label,r.course,e.class_name,s.name series_name FROM entries e JOIN races r ON r.id=e.race_id JOIN series s ON s.id=r.series_id WHERE e.handicap_set_id=? ORDER BY r.start,r.id',(item['id'],))]
        result.append(item)
    return result
