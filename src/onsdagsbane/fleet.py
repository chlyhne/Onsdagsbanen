"""Shared boat identities and series registrations. Handicap values live in sets."""
import json
import hashlib
from .store import get, audit
from .domain import gph
from . import handicaps

FIELDS=('name','skipper','sail_number','boat_type','club')


def create_profile(db,data,ratings,extra):
    identity=db.execute('INSERT INTO boat_profiles(name,skipper,sail_number,boat_type,club) VALUES(?,?,?,?,?)',tuple(data.get(key,'') for key in FIELDS)).lastrowid
    set_id=handicaps.for_values(db,identity,ratings,extra)
    db.execute('UPDATE boat_profiles SET current_handicap_set_id=? WHERE id=?',(set_id,identity))
    return identity


def profile(db,identity):
    row=db.execute('SELECT p.*,h.ratings,h.extra_handicap FROM boat_profiles p JOIN handicap_sets h ON h.id=p.current_handicap_set_id WHERE p.id=?',(identity,)).fetchone()
    if not row:raise ValueError('Båden findes ikke.')
    item=dict(row)
    item['token']=hashlib.sha256(json.dumps(item,sort_keys=True).encode()).hexdigest()
    item['ratings']=json.loads(item['ratings'])
    return item


def race_history(db,identity):
    profile(db,identity)
    return [dict(row) for row in db.execute('''
        SELECT r.id race_id,r.label,r.start,r.course,r.rating_key,
               e.boat_id,e.class_name,e.handicap_set_id,s.id series_id,s.name series_name
        FROM entries e JOIN boats b ON b.id=e.boat_id
        JOIN races r ON r.id=e.race_id JOIN series s ON s.id=r.series_id
        WHERE b.profile_id=? ORDER BY s.year DESC,r.start DESC,r.id DESC
    ''',(identity,))]


def save_profile(db,data,user,identity=None):
    from .domain import RATINGS, number
    from .service import extra_handicap_value
    before=profile(db,identity) if identity else None
    if before and data.get('token')!=before['token']:raise ValueError('Båden er ændret. Åbn redigeringen igen.')
    fields={key:str(data.get(key,'')).strip() for key in FIELDS}
    if not all(fields[k] for k in FIELDS if k!='club') or any(len(v)>160 for v in fields.values()):
        raise ValueError('Udfyld bådnavn, skipper, sejlnummer og bådtype, højst 160 tegn pr. felt.')
    if db.execute('SELECT 1 FROM boat_profiles WHERE id<>? AND trim(name)=? COLLATE NOCASE AND trim(sail_number)=? COLLATE NOCASE',(identity or -1,fields['name'],fields['sail_number'])).fetchone():
        raise ValueError('En anden båd har allerede dette navn og sejlnummer.')
    ratings=data.get('ratings',{})
    if not isinstance(ratings,dict):raise ValueError('Udfyld gyldige handicaptal.')
    ratings={key:number(ratings.get(key),optional=True) for key in RATINGS}
    extra=extra_handicap_value(data.get('extra_handicap',0))
    if identity:
        from .compatibility import validate_profile, supplement
        validate_profile(db,identity,ratings)
        if data.get('repair'):
            supplement(db,identity,ratings,data['repair'],user)
        set_id=handicaps.for_values(db,identity,ratings,extra)
        db.execute('UPDATE boat_profiles SET name=?,skipper=?,sail_number=?,boat_type=?,club=?,current_handicap_set_id=? WHERE id=?',(*(fields[k] for k in FIELDS),set_id,identity))
    else:
        identity=create_profile(db,fields,ratings,extra)
        set_id=profile(db,identity)['current_handicap_set_id']
    audit(db,user,'boat.profile.update' if before else 'boat.profile.create',{'profile_id':identity,'before':before,'fields':fields,'handicap_set_id':set_id})
    return identity


def update_profile(db,identity,data,user):
    return save_profile(db,data,user,identity)


def unregister(db,registration_id,user):
    registration=get(db,'boats',registration_id)
    if db.execute('SELECT 1 FROM participations WHERE boat_id=?',(registration_id,)).fetchone():
        raise ValueError('Fjern først båden fra seriens ræs enkeltvis. Båden er stadig tilmeldt et ræs.')
    db.execute('DELETE FROM boats WHERE id=?',(registration_id,))
    audit(db,user,'boat.unregister',registration)


def deletion_preview(db,identity):
    item=profile(db,identity)
    registrations=[dict(r) for r in db.execute('SELECT b.id,s.id series_id,s.name FROM boats b JOIN series s ON s.id=b.series_id WHERE b.profile_id=? ORDER BY b.id',(identity,))]
    memberships=[tuple(r) for r in db.execute('SELECT m.* FROM heat_boats m JOIN boats b ON b.id=m.boat_id WHERE b.profile_id=? ORDER BY m.heat_id,m.boat_id',(identity,))]
    entries=[tuple(r) for r in db.execute('SELECT e.* FROM entries e JOIN boats b ON b.id=e.boat_id WHERE b.profile_id=? ORDER BY e.race_id,e.boat_id',(identity,))]
    rounds=[dict(r) for r in db.execute('''SELECT p.id round_id,p.uid,p.label,p.position,s.id series_id,s.name series_name,c.course AS course,h.name class_name
        FROM heat_boats m JOIN boats b ON b.id=m.boat_id JOIN heats h ON h.id=m.heat_id JOIN round_courses c ON c.id=h.round_course_id JOIN rounds p ON p.id=c.round_id JOIN series s ON s.id=p.series_id WHERE b.profile_id=?
        UNION SELECT p.id,p.uid,p.label,p.position,s.id,s.name,r.course,e.class_name
        FROM entries e JOIN boats b ON b.id=e.boat_id JOIN races r ON r.id=e.race_id JOIN round_courses c ON c.id=r.round_course_id JOIN rounds p ON p.id=c.round_id JOIN series s ON s.id=p.series_id WHERE b.profile_id=?
        ORDER BY series_name,position,round_id,course,class_name''',(identity,identity))]
    blocked=bool(memberships or entries)
    token=hashlib.sha256(json.dumps([item['token'],registrations,memberships,entries,rounds],sort_keys=True).encode()).hexdigest()
    return {'id':identity,'name':item['name'],'series':registrations,'rounds':rounds,'can_delete':not blocked,'token':token,'reason':'Båden kan først slettes fra systemet, når den er fjernet fra alle ræs i alle serier. Gå selv ind i de enkelte ræs og fjern båden via løbsinddelingen.' if blocked else ''}


def delete_profile(db,identity,data,user):
    preview=deletion_preview(db,identity)
    if not preview['can_delete']:raise ValueError(preview['reason'])
    if data.get('confirm') is not True or data.get('token')!=preview['token']:
        raise ValueError('Gennemgå sletningen igen. Bådens tilmeldinger kan være ændret.')
    # Deliberately no cascade: any race membership or saved entry blocks deletion.
    db.execute('DELETE FROM boats WHERE profile_id=?',(identity,))
    db.execute('UPDATE boat_profiles SET current_handicap_set_id=NULL WHERE id=?',(identity,))
    db.execute('DELETE FROM handicap_sets WHERE profile_id=?',(identity,))
    db.execute('DELETE FROM boat_profiles WHERE id=?',(identity,))
    audit(db,user,'boat.profile.delete',preview)


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
        item['assignments']=[dict(r) for r in db.execute('SELECT p.id round_id,p.uid round_uid,p.series_id,p.label,h.id heat_id,h.name heat_name,c.course FROM heat_boats m JOIN boats b ON b.id=m.boat_id JOIN heats h ON h.id=m.heat_id JOIN round_courses c ON c.id=h.round_course_id JOIN rounds p ON p.id=c.round_id WHERE b.profile_id=? ORDER BY p.id,h.id',(item['id'],))]
        history=list(db.execute('SELECT c.round_id FROM entries e JOIN boats b ON b.id=e.boat_id JOIN races r ON r.id=e.race_id JOIN round_courses c ON c.id=r.round_course_id WHERE b.profile_id=?',(item['id'],)))
        item['saved_race_count']=len(history)
        item['round_count']=len({r['round_id'] for r in item['assignments']}|{r['round_id'] for r in history})
        result.append(item)
    return result


def enroll(db,series_id,data,user,attach=True):
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
        if attach:attach_boat(db,boat_id)
        added.append(boat_id)
    audit(db,user,'boats.enroll',{'series_id':series_id,'profile_ids':ids,'boat_ids':added,'course':course,'class_name':name})
    return added
