"""Shared boat identities and series registrations. Handicap values live in sets."""
import json
from .store import get, audit
from .domain import gph
from . import handicaps

FIELDS=('name','skipper','sail_number','boat_type','club')


def create_profile(db,data,ratings,extra):
    identity=db.execute('INSERT INTO boat_profiles(name,skipper,sail_number,boat_type,club) VALUES(?,?,?,?,?)',tuple(data.get(key,'') for key in FIELDS)).lastrowid
    set_id=handicaps.for_values(db,identity,ratings,extra)
    db.execute('UPDATE boat_profiles SET current_handicap_set_id=? WHERE id=?',(set_id,identity))
    return identity


def migrate(db):
    legacy_boats='ratings' in {r['name'] for r in db.execute('PRAGMA table_info(boats)')}
    legacy_profiles='ratings' in {r['name'] for r in db.execute('PRAGMA table_info(boat_profiles)')}
    for row in list(db.execute('SELECT * FROM boat_profiles WHERE current_handicap_set_id IS NULL')):
        if legacy_profiles:
            set_id=handicaps.for_values(db,row['id'],json.loads(row['ratings']),row['extra_handicap'])
            db.execute('UPDATE boat_profiles SET current_handicap_set_id=? WHERE id=?',(set_id,row['id']))
    if not legacy_boats:return
    for row in list(db.execute('SELECT * FROM boats WHERE profile_id IS NULL')):
        boat=dict(row)
        found=db.execute('SELECT id FROM boat_profiles WHERE trim(name)=trim(?) COLLATE NOCASE AND trim(sail_number)=trim(?) COLLATE NOCASE',(boat['name'],boat['sail_number'])).fetchone() if boat['sail_number'].strip() else None
        profile_id=found['id'] if found and not db.execute('SELECT 1 FROM boats WHERE series_id=? AND profile_id=?',(boat['series_id'],found['id'])).fetchone() else create_profile(db,boat,json.loads(boat['ratings']),boat['extra_handicap'])
        db.execute('UPDATE boats SET profile_id=? WHERE id=?',(profile_id,boat['id']))


def catalog(db,series_id):
    result=[]
    for row in db.execute('SELECT p.*,h.ratings,h.extra_handicap,b.id registration_id FROM boat_profiles p JOIN handicap_sets h ON h.id=p.current_handicap_set_id LEFT JOIN boats b ON b.profile_id=p.id AND b.series_id=? ORDER BY p.name,p.sail_number',(series_id,)):
        item=dict(row);item['ratings']=json.loads(item['ratings']);item['gph']=gph(item['ratings'])
        item['series']=[dict(r) for r in db.execute('SELECT s.id,s.name,b.class_name,b.course FROM boats b JOIN series s ON s.id=b.series_id WHERE b.profile_id=? ORDER BY s.year DESC,s.id DESC',(item['id'],))]
        result.append(item)
    return result


def enroll(db,series_id,data,user):
    from .hierarchy import attach_boat
    get(db,'series',series_id)
    ids=data.get('profile_ids',[])
    if not isinstance(ids,list) or not ids or any(type(v) is not int for v in ids) or len(set(ids))!=len(ids):raise ValueError('Vælg mindst én båd uden gentagelser.')
    course=data.get('course');name=str(data.get('class_name','')).strip()
    if course not in ('stor','lille') or not db.execute('SELECT 1 FROM heats h JOIN round_courses c ON c.id=h.round_course_id JOIN rounds r ON r.id=c.round_id WHERE r.series_id=? AND c.course=? AND h.name=?',(series_id,course,name)).fetchone():raise ValueError('Vælg en bane og et løb i den valgte serie.')
    added=[]
    for identity in ids:
        if not db.execute('SELECT 1 FROM boat_profiles WHERE id=?',(identity,)).fetchone():raise ValueError('Båden findes ikke i den fælles bådliste.')
        if db.execute('SELECT 1 FROM boats WHERE series_id=? AND profile_id=?',(series_id,identity)).fetchone():raise ValueError('En af bådene er allerede tilmeldt denne serie.')
        boat_id=db.execute('INSERT INTO boats(series_id,course,class_name,profile_id) VALUES(?,?,?,?)',(series_id,course,name,identity)).lastrowid
        attach_boat(db,boat_id);added.append(boat_id)
    audit(db,user,'boats.enroll',{'series_id':series_id,'profile_ids':ids,'boat_ids':added,'course':course,'class_name':name})
    return added
