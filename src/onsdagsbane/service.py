from datetime import datetime, timedelta
from decimal import Decimal
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo
import re

from omvendt_respit.course import read_marks, calculate_course, rounding_sides
from omvendt_respit.starts import Boat, calculate_starts
from .domain import RATINGS, PENALTIES, correction, daily, number, overall, rounded, gph, rating_value
from .store import audit, get
from . import hierarchy, planning, fleet, handicaps

ROOT = Path(__file__).resolve().parents[2]
TZ = ZoneInfo('Europe/Copenhagen')


def instant(value):
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})?',str(value)):
        raise ValueError('Angiv dato og klokkeslæt i tidsfeltet. Tekst og ugyldige tider kan ikke gemmes.')
    try:
        result = datetime.fromisoformat(str(value).replace('Z','+00:00'))
    except (ValueError, TypeError):
        raise ValueError('Tidspunktet er ugyldigt.') from None
    if result.tzinfo is None:
        result = result.replace(tzinfo=TZ)
    return result.astimezone(TZ)


def geometry(route):
    if not isinstance(route, list) or len(route) > 100:
        raise ValueError('Vælg op til 100 rundingsmærker.')
    course = calculate_course(read_marks(ROOT / 'omvendt_respit/resources/marks.csv'), route)
    return {'distance':float(course.distance_nm),
            'legs':[{'from':leg.start.name, 'to':leg.end.name, 'distance':float(leg.distance_nm)} for leg in course.legs],
            'rounding_marks':[{'name':mark.name,'side':side} for mark,side in zip(course.rounding_marks,rounding_sides(course))]}


def boats(db, series_id):
    result=[dict(r) for r in db.execute('SELECT b.*,p.name,p.skipper,p.sail_number,p.boat_type,p.club,p.current_handicap_set_id,h.ratings,h.extra_handicap FROM boats b JOIN boat_profiles p ON p.id=b.profile_id JOIN handicap_sets h ON h.id=p.current_handicap_set_id WHERE b.series_id=? ORDER BY b.course,b.class_name,p.name',(series_id,))]
    for row in result:
        row['ratings']=json.loads(row['ratings']);row['gph']=gph(row['ratings'])
    return result


def rows(db, race_id):
    result=[dict(r) for r in db.execute("SELECT e.*,b.profile_id,p.name,p.skipper,p.sail_number,p.boat_type,r.course,r.rating_key,h.ratings handicap_ratings,h.extra_handicap,coalesce(f.status,'DNC') status,f.instant,f.elapsed,f.corrected,f.sequence,f.note,f.source_points,f.source_discarded,coalesce(f.penalty,0) penalty FROM entries e JOIN races r ON r.id=e.race_id JOIN boats b ON b.id=e.boat_id JOIN boat_profiles p ON p.id=b.profile_id JOIN handicap_sets h ON h.id=e.handicap_set_id LEFT JOIN finishes f ON f.race_id=e.race_id AND f.boat_id=e.boat_id WHERE e.race_id=?",(race_id,))]
    for row in result:
        ratings=json.loads(row.pop('handicap_ratings'))
        row['rating']=rating_value(ratings,row['rating_key'])
        if row['rating'] is None:row['rating']=ratings.get(row['rating_key'])
        row['handicap_set']={'id':row['handicap_set_id'],'profile_id':row['profile_id'],'ratings':ratings,'extra_handicap':row['extra_handicap']}
    pending=any(row['rating'] is None for row in result) and db.execute('SELECT mode FROM races WHERE id=?',(race_id,)).fetchone()[0]=='pursuit'
    for row in result:
        row['start_ready']=not pending
        if pending:row['start']=None
    return result


def create_series(db, data, user):
    name = str(data.get('name','')).strip()
    year = int(data['year'])
    thresholds = data.get('discards',[])
    if not name or len(name) > 100 or not 2020 <= year <= 2200:
        raise ValueError('Angiv serienavn og et gyldigt årstal.')
    if not isinstance(thresholds,list) or any(type(t) is not int or t < 2 for t in thresholds) or len(set(thresholds)) != len(thresholds):
        raise ValueError('Fratræk skal være forskellige antal sejladser, mindst 2.')
    cur = db.execute('INSERT INTO series(name,year,discards,reference) VALUES(?,?,?,?)',(name,year,json.dumps(sorted(thresholds)),number(data.get('reference',1000))))
    times=planning.defaults({},data)
    db.execute('UPDATE series SET default_start_stor=?,default_start_lille=? WHERE id=?',(times['stor'],times['lille'],cur.lastrowid))
    hierarchy.create_rounds(db,cur.lastrowid,data.get('round_labels','R1-R18'),user,data.get('fleets'))
    audit(db,user,'series.create',data)
    return cur.lastrowid


def update_series(db,identity,data,user):
    before=get(db,'series',identity)
    name=str(data.get('name',before['name'])).strip()
    year=int(data.get('year',before['year']))
    thresholds=data.get('discards',json.loads(before['discards']))
    if not name or len(name)>100 or not 2020<=year<=2200:
        raise ValueError('Angiv serienavn og et gyldigt årstal.')
    if not isinstance(thresholds,list) or any(type(t) is not int or t<2 for t in thresholds) or len(set(thresholds))!=len(thresholds):
        raise ValueError('Fratræk skal være forskellige antal sejladser, mindst 2.')
    if year!=before['year'] and db.execute('SELECT 1 FROM races WHERE series_id=?',(identity,)).fetchone():
        raise ValueError('Seriens årstal kan kun ændres, før der er indstillet sejladser. Eksisterende datoer flyttes ikke automatisk.')
    reference=number(data.get('reference',before['reference']))
    times=planning.defaults(before,data)
    db.execute('UPDATE series SET name=?,year=?,discards=?,reference=?,default_start_stor=?,default_start_lille=? WHERE id=?',(name,year,json.dumps(sorted(thresholds)),reference,times['stor'],times['lille'],identity))
    audit(db,user,'series.update',{'id':identity,'before':before,'name':name,'year':year,'discards':sorted(thresholds),'reference':reference,'start_times':times})


def save_boat(db,data,user,identity=None):
    series=get(db,'series',int(data['series_id']))
    values=[str(data.get(k,'')).strip() for k in ('class_name','course','name','skipper','sail_number','boat_type','club')]
    if not all(values[:6]) or any(len(x)>160 for x in values) or values[1] not in ('lille','stor'):raise ValueError('Udfyld løb, bane, navn, skipper, sejlnummer og bådtype.')
    ratings={key:number(data.get('ratings',{}).get(key),optional=True) for key in RATINGS}
    extra=extra_handicap_value(data.get('extra_handicap',0))
    existing=get(db,'boats',identity) if identity else None
    if existing and data.get('handicap_set_id'):
        selected_set=db.execute('SELECT * FROM handicap_sets WHERE id=? AND profile_id=?',(int(data['handicap_set_id']),existing['profile_id'])).fetchone()
        if not selected_set:raise ValueError('Handicapsættet skal tilhøre denne båd.')
        ratings=json.loads(selected_set['ratings']);extra=selected_set['extra_handicap']
    if existing:
        if existing['series_id']!=series['id']:raise ValueError('En båd kan ikke flyttes til en anden serie.')
        if data.get('recalculate'):recalculate_boat(db,identity,ratings,extra,data,user,apply=True)
        profile_id=existing['profile_id']
        set_id=handicaps.for_values(db,profile_id,ratings,extra)
        db.execute('UPDATE boat_profiles SET name=?,skipper=?,sail_number=?,boat_type=?,club=?,current_handicap_set_id=? WHERE id=?',(*values[2:],set_id,profile_id))
        db.execute('UPDATE boats SET class_name=?,course=? WHERE id=?',(*values[:2],identity))
    else:
        if db.execute('SELECT 1 FROM boat_profiles WHERE trim(name)=trim(?) COLLATE NOCASE AND trim(sail_number)=trim(?) COLLATE NOCASE',(values[2],values[4])).fetchone():raise ValueError('Båden findes allerede. Brug Tilføj fra fælles bådliste.')
        profile_id=fleet.create_profile(db,dict(zip(fleet.FIELDS,values[2:])),ratings,extra)
        identity=db.execute('INSERT INTO boats(series_id,class_name,course,profile_id) VALUES(?,?,?,?)',(series['id'],values[0],values[1],profile_id)).lastrowid
        set_id=db.execute('SELECT current_handicap_set_id FROM boat_profiles WHERE id=?',(profile_id,)).fetchone()[0]
    if not existing or (existing['course'],existing['class_name'])!=(values[1],values[0]):hierarchy.attach_boat(db,identity)
    for pending in list(db.execute('SELECT r.id,r.rating_key FROM races r JOIN round_courses c ON c.id=r.round_course_id JOIN rounds p ON p.id=c.round_id WHERE r.series_id=? AND r.published=0 AND p.completed=0 AND NOT EXISTS(SELECT 1 FROM finishes f WHERE f.race_id=r.id AND f.status<>\'DUTY\')',(series['id'],))):
        old_entry=next((r for r in rows(db,pending['id']) if r['boat_id']==identity),None)
        if old_entry and old_entry['rating'] is None and rating_value(ratings,pending['rating_key']) is not None:
            db.execute('UPDATE entries SET handicap_set_id=? WHERE race_id=? AND boat_id=?',(set_id,pending['id'],identity));replace_roster(db,pending['id'])
    audit(db,user,'boat.save',{'id':identity,**data,'before':existing,'profile_id':profile_id,'handicap_set_id':set_id})
    return identity


def extra_handicap_value(value):
    try:
        result = float(str(value).replace(',','.'))
    except (ValueError,TypeError):
        raise ValueError('Tidstillæg skal være et antal hele sekunder fra 0 til 3600.') from None
    if not math.isfinite(result) or result<0 or result>3600 or result!=int(result):
        raise ValueError('Tidstillæg skal være et antal hele sekunder fra 0 til 3600.')
    return int(result)


def recalculate_boat(db,boat_id,ratings,extra_handicap,data,user,apply=False):
    """Explicit, previewed correction of snapshot ratings, never a side effect."""
    import hashlib
    ratings = {key:number(ratings.get(key),optional=True) for key in RATINGS}
    history=[{**dict(r),'old_rating':next(e for e in rows(db,r['id']) if e['boat_id']==boat_id)['rating'],'old_extra_handicap':next(e for e in rows(db,r['id']) if e['boat_id']==boat_id)['extra_handicap']} for r in db.execute('SELECT r.*,e.distance entry_distance FROM entries e JOIN races r ON r.id=e.race_id WHERE e.boat_id=? ORDER BY r.start,r.id',(boat_id,))]
    chosen=data.get('race_ids')
    if chosen is not None:
        if not isinstance(chosen,list) or not chosen or any(type(v) is not int for v in chosen) or not set(chosen)<={r['id'] for r in history}:
            raise ValueError('Vælg mindst ét af bådens historiske ræs til genberegning.')
        history=[r for r in history if r['id'] in chosen]
    changes = []
    for race in history:
        key = race['rating_key']
        if key=='import':
            key = data.get('historical_rating_keys',{}).get(str(race['id']))
            if key not in RATINGS:
                raise ValueError(f"Vælg udtrykkeligt DH-tal for den importerede sejlads {race['label']} ({race['start'][:10]}).")
        rating = rating_value(ratings,key)
        if rating is None:
            raise ValueError(f"{key} mangler til {race['label']}. Historikken er ikke ændret.")
        entries = rows(db,race['id'])
        target = next(r for r in entries if r['boat_id']==boat_id)
        timed = [r for r in entries if r['status']=='FIN']
        if race['mode']=='pursuit' and any(r['corrected'] is None for r in timed):
            raise ValueError(f"{race['label']}: Historisk genberegning af jagtstart kræver måltider; rækkefølge alene kan ikke omregnes.")
        corrected = correction(target['elapsed'],rating,race['entry_distance'],race['reference'])+extra_handicap+target['penalty'] if target['elapsed'] is not None and target['status']=='FIN' else None
        changes.append({'race_id':race['id'],'label':race['label'],'date':race['start'][:10],'published':bool(race['published']),
            'revision':race['revision'],'rating_key':key,'old_rating':race['old_rating'],'rating':rating,
            'old_extra_handicap':race['old_extra_handicap'],'extra_handicap':extra_handicap,'old_corrected':target['corrected'],
            'corrected':corrected,'mode':race['mode'],'redress_removed':any(r['status']=='RDG' for r in entries)})
    token = hashlib.sha256(json.dumps({'boat_id':boat_id,'changes':changes},sort_keys=True).encode()).hexdigest()
    if apply:
        if data.get('recalculation_token')!=token:
            raise ValueError('Genberegningen skal først forhåndsvises. Data kan være ændret siden sidst.')
        for change in changes:
            race = get(db,'races',change['race_id'])
            applied_ratings={**ratings,'import':change['rating']} if race['rating_key']=='import' else ratings
            set_id=handicaps.for_values(db,get(db,'boats',boat_id)['profile_id'],applied_ratings,extra_handicap)
            db.execute('UPDATE entries SET handicap_set_id=? WHERE race_id=? AND boat_id=?',(set_id,race['id'],boat_id))
            db.execute("UPDATE finishes SET corrected=? WHERE race_id=? AND boat_id=? AND status='FIN'",(change['corrected'],race['id'],boat_id))
            invalidate_redress(db,race['id'])
            if db.execute("SELECT 1 FROM finishes WHERE race_id=? AND status='DUTY'",(race['id'],)).fetchone():
                db.execute('UPDATE rounds SET completed=0,completed_at=NULL WHERE id=(SELECT round_id FROM round_courses WHERE id=?)',(race['round_course_id'],))
            db.execute('UPDATE finishes SET source_points=NULL,source_discarded=NULL WHERE race_id=?',(race['id'],))
            if race['mode']=='pursuit':
                fleet = rows(db,race['id'])
                finished = [r for r in fleet if r['status']=='FIN']
                if finished:
                    for i,row in enumerate(sorted(finished,key=lambda r:(r['corrected'],r['boat_id'])),1):
                        db.execute('UPDATE finishes SET sequence=? WHERE race_id=? AND boat_id=?',(i,race['id'],row['boat_id']))
                else:
                    # No actual finishes: regenerate the planned start schedule.
                    selected = [Boat(str(r['boat_id']),r['skipper'],r['name'],r['sail_number'],r['class_name'],Decimal(str(r['rating']))) for r in fleet]
                    for start in calculate_starts(selected,Decimal(str(race['distance'])),instant(race['start'])):
                        extra = next(r['extra_handicap'] for r in fleet if r['boat_id']==int(start.boat.identity))
                        db.execute('UPDATE entries SET start=? WHERE race_id=? AND boat_id=?',((start.time+timedelta(seconds=extra)).isoformat(),race['id'],int(start.boat.identity)))
            # Published corrections go back to draft for explicit republication.
            db.execute('UPDATE races SET published=0,revision=revision+1 WHERE id=?',(race['id'],))
        audit(db,user,'boat.recalculate',{'boat_id':boat_id,'changes':changes})
    return {'changes':changes,'token':token,'notice':'Berørte sejladser bliver kladder igen. Redress fjernes og skal genberegnes. Faktiske måltider og historiske starttidspunkter bevares.'}


def replace_roster(db,race_id):
    race=get(db,'races',race_id)
    if race['published'] or db.execute("SELECT 1 FROM finishes WHERE race_id=? AND status<>'DUTY'",(race_id,)).fetchone():
        raise ValueError('Deltagerlisten kan kun ændres før resultatindtastningen begynder.')
    fleet_by_id={b['id']:b for b in boats(db,race['series_id'])}
    selected=[{**fleet_by_id[r['boat_id']],'heat_name':r['name']} for r in db.execute('SELECT m.boat_id,h.name FROM heat_boats m JOIN heats h ON h.id=m.heat_id WHERE h.round_course_id=? ORDER BY m.boat_id',(race['round_course_id'],))]
    if len({b['id'] for b in selected})!=len(selected):raise ValueError('En båd må kun deltage i ét løb på samme bane i et ræs.')
    snapshots={row['boat_id']:row for row in rows(db,race_id)}
    for boat in selected:
        old=snapshots.get(boat['id'])
        boat['rating']=old['rating'] if old else rating_value(boat['ratings'],race['rating_key'])
        boat['set_id']=old['handicap_set_id'] if old else boat['current_handicap_set_id']
        boat['extra_handicap']=old['extra_handicap'] if old else boat['extra_handicap']
    missing=[b['name'] for b in selected if b['rating'] is None]
    if missing:db.execute('UPDATE races SET warning=? WHERE id=?',('Mangler '+race['rating_key']+': '+', '.join(missing),race_id))
    elif race['warning'].startswith('Mangler '):db.execute("UPDATE races SET warning='' WHERE id=?",(race_id,))
    schedule={}
    if race['mode']=='pursuit' and selected and not missing:
        fleet=[Boat(str(b['id']),b['skipper'],b['name'],b['sail_number'],b['heat_name'],Decimal(str(b['rating']))) for b in selected]
        schedule={int(s.boat.identity):(s.time+timedelta(seconds=next(b['extra_handicap'] for b in selected if b['id']==int(s.boat.identity)))).isoformat() for s in calculate_starts(fleet,Decimal(str(race['distance'])),instant(race['start']))}
    selected_ids={b['id'] for b in selected}
    for old in snapshots.values():
        if old['boat_id'] not in selected_ids:
            if old['sequence'] is not None:raise ValueError('Fjern først bådens registrerede status før tilmeldingen ændres.')
            db.execute('DELETE FROM entries WHERE race_id=? AND boat_id=?',(race_id,old['boat_id']))
    for boat in selected:
        db.execute('INSERT INTO entries(race_id,boat_id,class_name,start,distance,wind_category,handicap_set_id) VALUES(?,?,?,?,?,?,?) ON CONFLICT(race_id,boat_id) DO UPDATE SET class_name=excluded.class_name,start=excluded.start,distance=excluded.distance,wind_category=excluded.wind_category,handicap_set_id=excluded.handicap_set_id',(race_id,boat['id'],boat['heat_name'],schedule.get(boat['id'],race['start']),race['distance'],'' if race['rating_key']=='GPH' else race['rating_key'][-1],boat['set_id']))



def create_race(db, data, user):
    series=get(db,'series',int(data['series_id']))
    course,mode=data['course'],data['mode']
    system=data.get('handicap_system')
    if system is None and 'rating_key' not in data and data.get('round_id'):
        system=get(db,'rounds',int(data['round_id']))['handicap_system']
    wind=data.get('wind_category','M')
    if system is not None:
        if system not in ('circle','gph','updown'):raise ValueError('Vælg cirkelmål, GPH eller op/ned-mål.')
        if system=='gph' and data.get('wind_category'):raise ValueError('Vindkategori kan ikke indstilles ved GPH.')
        if system!='gph' and wind not in ('L','M','H'):raise ValueError('Vælg en vindkategori.')
        key='GPH' if system=='gph' else ('TACI' if system=='circle' else 'TAUD')+wind
    else:
        key=data.get('rating_key','TACIM')
        system='gph' if key=='GPH' else 'updown' if key.startswith('TAUD') else 'circle'
    if key=='GPH' and data.get('wind_category'):raise ValueError('Vindkategori kan ikke indstilles ved GPH.')
    if course not in ('lille','stor') or mode not in ('normal','pursuit') or key not in (*RATINGS,'GPH'):
        raise ValueError('Vælg bane, startform og DH-tal.')
    if data.get('round_id'):
        planned=get(db,'rounds',int(data['round_id']))
        if planned['series_id']!=series['id']:raise ValueError('Ræset tilhører en anden serie.')
        label=planned['label'];round_id=planned['id']
    else:
        # Keep compatibility with imports and earlier clients. The UI selects a planned round.
        label=str(data.get('label','')).strip()
        if not label or len(label)>60:raise ValueError('Vælg et ræs i serien.')
        round_id=hierarchy.ensure_round(db,series['id'],label)
    if get(db,'rounds',round_id)['completed']:raise ValueError('Ræset er afsluttet. Genåbn begge baner før opsætningen ændres.')
    for sibling in db.execute('SELECT r.rating_key FROM races r JOIN round_courses c ON c.id=r.round_course_id WHERE c.round_id=?',(round_id,)):
        sibling_system='gph' if sibling['rating_key']=='GPH' else 'updown' if sibling['rating_key'].startswith('TAUD') else 'circle'
        if sibling_system!=system:raise ValueError('Beregningsmålet gælder hele ræset. Den anden bane bruger allerede et andet mål.')
    db.execute('UPDATE rounds SET handicap_system=? WHERE id=?',(system,round_id))
    course_id=db.execute('SELECT id FROM round_courses WHERE round_id=? AND course=?',(round_id,course)).fetchone()[0]
    if db.execute('SELECT 1 FROM races WHERE round_course_id=?',(course_id,)).fetchone():
        raise ValueError('Denne bane i ræset er allerede indstillet. Vælg den eksisterende sejlads.')
    planned=get(db,'rounds',round_id)
    scheduled=db.execute('SELECT start_time FROM round_courses WHERE id=?',(course_id,)).fetchone()[0]
    start=instant(data.get('start') or ((planned['race_date']+'T'+scheduled) if planned['race_date'] else ''))
    if start.year!=series['year']:raise ValueError('Datoen skal ligge i seriens årstal.')
    distance=geometry(data['route'])['distance']
    if 'boat_ids' in data:
        selected_ids=data['boat_ids']
        if not isinstance(selected_ids,list) or not selected_ids or any(type(v) is not int for v in selected_ids) or len(set(selected_ids))!=len(selected_ids):raise ValueError('Vælg mindst én deltager uden gentagelser.')
        selected=[b for b in boats(db,series['id']) if b['id'] in selected_ids and b['course']==course]
        if len(selected)!=len(selected_ids):raise ValueError('Alle deltagere skal tilhøre denne serie og bane.')
        assignments={r['boat_id']:r['name'] for r in db.execute('SELECT m.boat_id,h.name FROM heat_boats m JOIN heats h ON h.id=m.heat_id WHERE h.round_course_id=?',(course_id,))}
        db.execute('DELETE FROM heat_boats WHERE heat_id IN (SELECT id FROM heats WHERE round_course_id=?)',(course_id,))
        for boat in selected:
            name=assignments.get(boat['id'],boat['class_name'])
            db.execute('INSERT OR IGNORE INTO heats(round_course_id,name,position) VALUES(?,?,0)',(course_id,name))
            heat_id=db.execute('SELECT id FROM heats WHERE round_course_id=? AND name=?',(course_id,name)).fetchone()[0]
            db.execute('INSERT INTO heat_boats(heat_id,boat_id) VALUES(?,?)',(heat_id,boat['id']))
    reference=number(data.get('reference',series['reference']))
    identity=db.execute('INSERT INTO races(series_id,label,course,mode,start,route,distance,rating_key,reference,round_course_id) VALUES(?,?,?,?,?,?,?,?,?,?)',(series['id'],label,course,mode,start.isoformat(),json.dumps(data['route']),distance,key,reference,course_id)).lastrowid
    replace_roster(db,identity)
    db.execute('UPDATE round_courses SET start_time=? WHERE id=?',(start.strftime('%H:%M:%S'),course_id))
    db.execute('UPDATE rounds SET race_date=coalesce(race_date,?) WHERE id=?',(start.date().isoformat(),round_id))
    audit(db,user,'race.create',{'id':identity,**data})
    return identity


def editable(db, race_id, revision=None, allow_completed=False):
    race = get(db,'races',race_id)
    if not allow_completed:
        closed=db.execute('SELECT p.completed FROM rounds p JOIN round_courses c ON c.round_id=p.id WHERE c.id=?',(race['round_course_id'],)).fetchone()
        if closed and closed[0]:raise ValueError('Ræset er afsluttet. Genåbn måltagningen for begge baner først.')
    if race['published']:
        raise ValueError('Sejladsen er offentliggjort. Åbn den for rettelser først.')
    if revision is not None and int(revision) != race['revision']:
        raise ValueError('En anden bruger har ændret sejladsen. Opdatér siden og prøv igen.')
    return race


def invalidate_redress(db,race_id):
    # Keep the duty designation when the observations or handicap setup change.
    db.execute("UPDATE finishes SET status='DUTY',instant=NULL,elapsed=NULL,corrected=NULL,note='Dommertjans · afventer beregning efter sejladsen',source_points=NULL,source_discarded=NULL WHERE race_id=? AND status='RDG'",(race_id,))


def changed(db,race_id):
    db.execute('UPDATE races SET revision=revision+1 WHERE id=?',(race_id,))
    db.execute('UPDATE finishes SET source_points=NULL,source_discarded=NULL WHERE race_id=?',(race_id,))


def capture(db,race_id,data,user,now=None):
    request_id = str(data.get('request_id',''))
    if not 16 <= len(request_id) <= 100:
        raise ValueError('Registreringen mangler en gyldig nøgle.')
    old = db.execute('SELECT * FROM captures WHERE request_id=?',(request_id,)).fetchone()
    if old:
        if old['race_id'] != race_id:
            raise ValueError('Registreringen tilhører en anden sejlads.')
        return dict(old)
    race = editable(db,race_id)
    stamp = instant(data['instant']) if data.get('instant') else now or datetime.now(TZ)
    if stamp < instant(race['start']):
        raise ValueError('Sejladsen er ikke startet endnu.')
    if (stamp-instant(race['start'])).total_seconds()>86400:
        raise ValueError('Tiden skal ligge inden for 24 timer efter start. Kontrollér dato og klokkeslæt.')
    cur = db.execute('INSERT INTO captures(race_id,instant,request_id) VALUES(?,?,?)',(race_id,stamp.isoformat(),request_id))
    audit(db,user,'capture',{'race_id':race_id,'id':cur.lastrowid,'instant':stamp.isoformat()})
    return get(db,'captures',cur.lastrowid)


def usable_capture(db,race_id,capture_id):
    cap=get(db,'captures',capture_id)
    same_round=db.execute('SELECT 1 FROM races a JOIN round_courses ac ON ac.id=a.round_course_id JOIN races b ON b.id=? JOIN round_courses bc ON bc.id=b.round_course_id WHERE a.id=? AND ac.round_id=bc.round_id',(race_id,cap['race_id'])).fetchone()
    if not same_round or cap['used']:raise ValueError('Tidsstemplet er allerede brugt eller tilhører et andet ræs.')
    return cap


def finish(db,race_id,data,user):
    request_id = str(data.get('request_id',''))
    if not 16 <= len(request_id) <= 100:
        raise ValueError('Registreringen mangler en gyldig nøgle.')
    old = db.execute('SELECT * FROM finishes WHERE request_id=?',(request_id,)).fetchone()
    if old:
        if old['race_id'] != race_id or old['boat_id'] != int(data['boat_id']):
            raise ValueError('Registreringen tilhører en anden båd eller sejlads.')
        return dict(old)
    race = editable(db,race_id)
    boat_id = int(data['boat_id'])
    entry=next((row for row in rows(db,race_id) if row['boat_id']==boat_id),None)
    if not entry:
        raise ValueError('Båden er ikke tilmeldt denne sejlads.')
    if db.execute('SELECT 1 FROM finishes WHERE race_id=? AND boat_id=?',(race_id,boat_id)).fetchone():
        raise ValueError('Båden er allerede registreret. Brug Ret eller Fortryd.')
    status = data.get('status','FIN')
    if status=='DUTY' and (not user or not user.get('approved')):
        raise PermissionError('Log ind som godkendt bruger for at markere dommertjans.')
    if status not in PENALTIES | {'FIN','DUTY'}:
        raise ValueError('Ukendt resultatstatus.')
    penalty = extra_handicap_value(data.get('penalty',0))
    if status!='FIN' and penalty:
        raise ValueError('Straftid kan kun knyttes til en tidsmålt målgang.')
    elapsed = corrected = stamp = None
    cap = None
    if status=='DUTY' and data.get('capture_id'):cap=usable_capture(db,race_id,int(data['capture_id']))
    if status=='FIN' and not entry['start_ready']:raise ValueError('Jagtstartens starttider kan først beregnes, når alle nødvendige handicaptal er udfyldt.')
    if status == 'FIN':
        if data.get('capture_id'):
            cap=usable_capture(db,race_id,int(data['capture_id']))
            stamp = instant(data['instant']) if data.get('instant') else instant(cap['instant'])
        elif data.get('instant'):
            stamp = instant(data['instant'])
        elif race['mode'] != 'pursuit':
            raise ValueError('Angiv en måltid eller tag tiden nu.')
        if stamp:
            elapsed = rounded((stamp - instant(entry['start'])).total_seconds())
            if elapsed <= 0 or elapsed > 86400:
                raise ValueError('Måltiden skal ligge efter bådens start og inden for 24 timer. Kontrollér dato og klokkeslæt.')
            if entry['rating'] is None:
                raise ValueError(f"Båden mangler det valgte handicap ({race['rating_key']}).")
            corrected = correction(elapsed,entry['rating'],entry['distance'],race['reference']) + entry['extra_handicap'] + penalty
    if penalty and not stamp:
        raise ValueError('Straftid kræver en måltid.')
    if race['mode']=='pursuit' and status=='FIN':
        other = [r for r in rows(db,race_id) if r['status']=='FIN']
        if (penalty or any(r['penalty'] for r in other)) and (not stamp or any(not r['instant'] for r in other)):
            raise ValueError('Straftid ved jagtstart kræver måltider for alle målgangsbåde.')
    sequence = db.execute('SELECT coalesce(max(sequence),0)+1 FROM finishes WHERE race_id=?',(race_id,)).fetchone()[0]
    db.execute('INSERT INTO finishes(race_id,boat_id,status,instant,elapsed,corrected,sequence,note,request_id,penalty) VALUES(?,?,?,?,?,?,?,?,?,?)',(race_id,boat_id,status,stamp.isoformat() if stamp else None,elapsed,corrected,sequence,str(data.get('note',''))[:500],request_id,penalty))
    if cap:
        db.execute('UPDATE captures SET used=1 WHERE id=?',(cap['id'],))
    # Marking duty does not change observations; it does not run the model.
    if status!='DUTY':
        invalidate_redress(db,race_id)
    if race['mode']=='pursuit':
        finished = [r for r in rows(db,race_id) if r['status']=='FIN']
        if finished and all(r['instant'] for r in finished):
            for i,row in enumerate(sorted(finished,key=lambda r:(instant(r['instant'])+timedelta(seconds=r['penalty']),r['sequence'])),1):
                db.execute('UPDATE finishes SET sequence=? WHERE race_id=? AND boat_id=?',(i,race_id,row['boat_id']))
    changed(db,race_id)
    audit(db,user,'finish.add',{'race_id':race_id,**data})
    return {'saved':True}


def undo(db,race_id,boat_id,user,revision):
    row = db.execute('SELECT * FROM finishes WHERE race_id=? AND boat_id=?',(race_id,boat_id)).fetchone()
    if not row:
        raise ValueError('Resultatet findes ikke længere.')
    editable(db,race_id,revision,allow_completed=row['status']=='RDG')
    audit(db,user,'finish.undo',dict(row))
    db.execute('DELETE FROM finishes WHERE race_id=? AND boat_id=?',(race_id,boat_id))
    if row['status'] != 'RDG':
        invalidate_redress(db,race_id)
    changed(db,race_id)


def publish(db,race_id,data,user):
    race = get(db,'races',race_id)
    if race['revision'] != int(data['revision']):
        raise ValueError('Sejladsen er ændret. Gennemgå resultaterne igen.')
    value = bool(data['published'])
    if value:
        roster = rows(db,race_id)
        if any(r['status']=='DUTY' for r in roster):
            raise ValueError('Afslut ræset for at beregne dommertid automatisk før offentliggørelse.')
        if not roster or not any(r['sequence'] is not None for r in roster):
            raise ValueError('Der skal være mindst ét registreret resultat.')
        if db.execute('SELECT 1 FROM captures WHERE race_id=? AND used=0',(race_id,)).fetchone():
            raise ValueError('Der er tidsstempler uden båd. Tildel eller kassér dem før offentliggørelse.')
        if any(r['sequence'] is None for r in roster) and not data.get('confirm_dnc'):
            raise ValueError('Bekræft at både uden resultat får DNC.')
    db.execute('UPDATE races SET published=?,revision=revision+1 WHERE id=?',(int(value),race_id))
    audit(db,user,'race.publish',{'race_id':race_id,'published':value})


def result_view(db,series_id,course,class_name='',drafts=False):
    class_ids={r[0] for r in db.execute('SELECT m.boat_id FROM heat_boats m JOIN heats h ON h.id=m.heat_id JOIN round_courses c ON c.id=h.round_course_id JOIN rounds p ON p.id=c.round_id WHERE p.series_id=? AND c.course=? AND h.name=? UNION SELECT e.boat_id FROM entries e JOIN races r ON r.id=e.race_id WHERE r.series_id=? AND r.course=? AND e.class_name=?',(series_id,course,class_name,series_id,course,class_name))} if class_name else set()
    course_ids={r[0] for r in db.execute('SELECT m.boat_id FROM heat_boats m JOIN heats h ON h.id=m.heat_id JOIN round_courses c ON c.id=h.round_course_id JOIN rounds p ON p.id=c.round_id WHERE p.series_id=? AND c.course=? UNION SELECT e.boat_id FROM entries e JOIN races r ON r.id=e.race_id WHERE r.series_id=? AND r.course=?',(series_id,course,series_id,course))}
    fleet = [b for b in boats(db,series_id) if ((b['course']==course and (not class_name or b['class_name']==class_name)) or b['id'] in (class_ids if class_name else course_ids))]
    class_by_boat={}
    for member in db.execute('SELECT m.boat_id,h.name FROM heat_boats m JOIN heats h ON h.id=m.heat_id JOIN round_courses c ON c.id=h.round_course_id JOIN rounds p ON p.id=c.round_id WHERE p.series_id=? AND c.course=? ORDER BY p.position,h.position',(series_id,course)):
        class_by_boat.setdefault(member['boat_id'],[])
        if member['name'] not in class_by_boat[member['boat_id']]:class_by_boat[member['boat_id']].append(member['name'])
    fleet=[{**boat,'course':course,'class_name':class_name or ' / '.join(class_by_boat.get(boat['id'],[boat['class_name']]))} for boat in fleet]
    races = [dict(r) for r in db.execute('SELECT * FROM races WHERE series_id=? AND course=? ORDER BY start,id',(series_id,course)) if drafts or r['published']]
    results = {}
    for race in races:
        route = json.loads(race['route'])
        race['rounding_marks'] = geometry(route)['rounding_marks'] if route else []
        data = [r for r in rows(db,race['id']) if not class_name or r['class_name'] == class_name]
        # Series entrants count in every race, including before a late entry
        # appeared in the source. Missing source rows are DNC, never zero points.
        present = {r['boat_id'] for r in data}
        for boat in fleet:
            if boat['id'] in present:
                continue
            same_class = next((r for r in data if r['class_name']==boat['class_name']),None)
            data.append({'boat_id':boat['id'],'class_name':class_name or boat['class_name'],
                'rating':None,'start':same_class['start'] if same_class else race['start'],
                'distance':same_class['distance'] if same_class else race['distance'],
                'extra_handicap':0,'penalty':0,
                'wind_category':same_class['wind_category'] if same_class else ('' if race['rating_key']=='GPH' else race['rating_key'][-1]),
                'name':boat['name'],'skipper':boat['skipper'],'sail_number':boat['sail_number'],
                'boat_type':boat['boat_type'],'course':course,
                'status':'DNC','instant':None,'elapsed':None,'corrected':None,'sequence':None,
                'note':'Konsolideret fra seriens deltagerliste; ingen registrering i denne sejlads.',
                'source_points':None,'source_discarded':None})
        day_races = [dict(r) for r in db.execute('SELECT id FROM races WHERE series_id=? AND substr(start,1,10)=? AND (published=1 OR ?)',(series_id,race['start'][:10],int(drafts)))]
        wind = {r['wind_category'] for other in day_races for r in rows(db,other['id']) if r['wind_category']}
        distances = {r['distance'] for r in rows(db,race['id'])}
        warnings = []
        if len(distances)>1:
            warnings.append('Løbene på banen har forskellige distancer: '+', '.join(f'{v:g}' for v in sorted(distances))+' sm. Samlet resultat er vejledende.')
        if len(wind)>1:
            warnings.append('Dagens løb bruger forskellige vindkategorier på tværs af lille og stor bane.')
        race['warning'] = ' '.join(filter(None,[race['warning'],*warnings]))
        results[race['id']] = daily(data,pursuit=race['mode']=='pursuit')
    settings = get(db,'series',series_id)
    references = sorted({r['reference'] for r in db.execute('SELECT reference,published FROM races WHERE series_id=?',(series_id,)) if drafts or r['published']})
    return {'races':races,'daily':results,'overall':overall(fleet,races,results,json.loads(settings['discards'])) if races else [],'discards':json.loads(settings['discards']), 'references':references,'reference_warning':len(references)>1,'source_scoring':False}


def set_reference(db,race_id,value,user,revision):
    race = get(db,'races',race_id)
    if race['revision']!=int(revision):
        raise ValueError('Sejladsen er ændret. Opdatér siden før referenceskift.')
    value = number(value)
    # Apply the same integer shift to every existing result over the same distance.
    # This preserves ties and ordering as well as the original source rounding.
    for entry in rows(db,race_id):
        shift = rounded((Decimal(str(value))-Decimal(str(race['reference'])))*Decimal(str(entry['distance'])))
        db.execute('UPDATE finishes SET corrected=corrected+? WHERE race_id=? AND boat_id=? AND corrected IS NOT NULL',(shift,race_id,entry['boat_id']))
    db.execute('UPDATE races SET reference=?,revision=revision+1 WHERE id=?',(value,race_id))
    audit(db,user,'race.reference',{'race_id':race_id,'old':race['reference'],'new':value})


def series_reference(db,series_id,data,user):
    get(db,'series',series_id)
    value = number(data['reference'])
    history = [dict(r) for r in db.execute('SELECT id,revision FROM races WHERE series_id=?',(series_id,))]
    if data.get('apply_all'):
        revisions = data.get('revisions',{})
        if any(revisions.get(str(r['id']))!=r['revision'] for r in history):
            raise ValueError('En sejlads er ændret eller tilføjet. Opdatér før ensretning af referencer.')
        for row in history:
            set_reference(db,row['id'],value,user,row['revision'])
    db.execute('UPDATE series SET reference=? WHERE id=?',(value,series_id))
    audit(db,user,'series.reference',{'series_id':series_id,'reference':value,'apply_all':bool(data.get('apply_all'))})


def day_settings(db,data,user):
    """Preview, then explicitly align course distance and the day's wind category."""
    import hashlib
    day = str(data['date'])
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',day) or data['course'] not in ('lille','stor'):
        raise ValueError('Vælg en gyldig sejladsdag og bane.')
    wind = data.get('wind_category','')
    if wind not in ('','L','M','H'):
        raise ValueError('Ukendt vindkategori.')
    route = data.get('route',[])
    distance = geometry(route)['distance'] if route else None
    if not wind and distance is None:
        raise ValueError('Vælg en vindkategori eller en mærkerækkefølge.')
    races = [dict(r) for r in db.execute('SELECT * FROM races WHERE series_id=? AND substr(start,1,10)=? ORDER BY id',(int(data['series_id']),day))]
    if wind and races and all(r['rating_key']=='GPH' for r in races):
        raise ValueError('Vindkategori kan ikke indstilles ved GPH.')
    changes = []
    for race in races:
        effective_wind=wind if race['rating_key']!='GPH' else ''
        if race['rating_key']=='GPH' and distance is None:continue
        if not effective_wind and race['course']!=data['course']:
            continue
        key = race['rating_key'][:4]+effective_wind if effective_wind else race['rating_key']
        if key not in (*RATINGS,'GPH'):
            raise ValueError('Sejladsen mangler en kendt DH-kategori.')
        entries = rows(db,race['id'])
        if race['mode']=='pursuit' and any(r['status']=='FIN' and not r['instant'] for r in entries):
            raise ValueError(f"{race['label']}: Jagtstartens opsætning kan kun genberegnes efter målgang, når der findes måltider.")
        updates = []
        for entry in entries:
            rating = (entry['handicap_set']['ratings'].get(key) if key!=race['rating_key'] else entry['rating']) if effective_wind else entry['rating']
            if rating is None:
                raise ValueError(f"{entry['name']} mangler et historisk {key} for {race['label']}. Ret beregningsgrundlaget for den gamle sejlads først. Nutidens stamdata bruges ikke som erstatning.")
            new_distance = distance if distance is not None and race['course']==data['course'] else entry['distance']
            corrected = correction(entry['elapsed'],rating,new_distance,race['reference'])+entry['extra_handicap']+entry['penalty'] if entry['status']=='FIN' and entry['elapsed'] is not None else None
            updates.append({'boat_id':entry['boat_id'],'name':entry['name'],'class_name':entry['class_name'],
                'old_distance':entry['distance'],'distance':new_distance,'old_wind':entry['wind_category'],'wind':effective_wind or entry['wind_category'],
                'old_rating':entry['rating'],'rating':rating,'old_corrected':entry['corrected'],'corrected':corrected})
        changes.append({'race_id':race['id'],'label':race['label'],'course':race['course'],'revision':race['revision'],'rating_key':key,'updates':updates})
    if not changes:
        raise ValueError('Der findes ingen sejladser på den valgte dag.')
    token = hashlib.sha256(json.dumps(changes,sort_keys=True).encode()).hexdigest()
    if data.get('apply'):
        if data.get('token')!=token:
            raise ValueError('Forhåndsvis ændringerne igen. Sejladsdata kan være ændret.')
        for change in changes:
            race_id = change['race_id']
            for update in change['updates']:
                db.execute('UPDATE entries SET distance=?,wind_category=? WHERE race_id=? AND boat_id=?',(update['distance'],update['wind'],race_id,update['boat_id']))
                db.execute("UPDATE finishes SET corrected=? WHERE race_id=? AND boat_id=? AND status='FIN'",(update['corrected'],race_id,update['boat_id']))
            invalidate_redress(db,race_id)
            db.execute('UPDATE finishes SET source_points=NULL,source_discarded=NULL WHERE race_id=?',(race_id,))
            race = get(db,'races',race_id)
            if race['mode']=='pursuit':
                entries = rows(db,race_id)
                finished = [r for r in entries if r['status']=='FIN']
                if finished:
                    for i,row in enumerate(sorted(finished,key=lambda r:(r['corrected'],r['boat_id'])),1):
                        db.execute('UPDATE finishes SET sequence=? WHERE race_id=? AND boat_id=?',(i,race_id,row['boat_id']))
                else:
                    fleet_for_start = [Boat(str(r['boat_id']),r['skipper'],r['name'],r['sail_number'],r['class_name'],Decimal(str(r['rating']))) for r in entries]
                    new_distance = change['updates'][0]['distance']
                    for start in calculate_starts(fleet_for_start,Decimal(str(new_distance)),instant(race['start'])):
                        extra_handicap = next(r['extra_handicap'] for r in entries if r['boat_id']==int(start.boat.identity))
                        db.execute('UPDATE entries SET start=? WHERE race_id=? AND boat_id=?',((start.time+timedelta(seconds=extra_handicap)).isoformat(),race_id,int(start.boat.identity)))
            new_route = json.dumps(route) if route and race['course']==data['course'] else race['route']
            new_distance = change['updates'][0]['distance']
            warning = '' if len({r['start'] for r in rows(db,race_id)})==1 else 'Løbene har forskellige starttider; samlet visning er vejledende.'
            db.execute('UPDATE races SET rating_key=?,distance=?,route=?,published=0,revision=revision+1,warning=? WHERE id=?',(change['rating_key'],new_distance,new_route,warning,race_id))
        audit(db,user,'day.settings',{'date':day,'changes':changes})
    return {'changes':changes,'token':token,'notice':'Ændringer genberegner dagens tider, fjerner redress og gør de berørte sejladser til kladder. Kontrollér og offentliggør dem igen.'}
