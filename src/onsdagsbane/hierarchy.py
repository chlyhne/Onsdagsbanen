"""Planned series rounds, courses and fleets, independently of race results."""
import re
from .store import audit, get

COURSES = ('stor', 'lille')
DEFAULT_FLEETS = {'stor':['Stor bane 1','Stor bane 2'], 'lille':['Lille bane 1','Lille bane 2']}


def labels(value):
    if isinstance(value,str):
        match = re.fullmatch(r'\s*R?(\d+)\s*[-–]\s*R?(\d+)\s*',value,re.I)
        if match:
            first,last = map(int,match.groups())
            if not 1 <= first <= last <= 200:
                raise ValueError('Angiv et interval som R1–R18, højst R200.')
            value = [f'R{i}' for i in range(first,last+1)]
        else:
            value = re.split(r'[,\n]+',value)
    if not isinstance(value,list) or not 1 <= len(value) <= 200 or any(not isinstance(v,str) for v in value):
        raise ValueError('Udfyld rækkens ræs, fx R1–R18 eller ét navn pr. linje.')
    value = [v.strip() for v in value]
    if any(not v or len(v)>60 for v in value) or len({v.casefold() for v in value}) != len(value):
        raise ValueError('Ræs skal have forskellige navne på højst 60 tegn.')
    return value


def fleet_names(value):
    if isinstance(value,str):value=re.split(r'[,\n]+',value)
    if not isinstance(value,list) or not 1 <= len(value) <= 20 or any(not isinstance(v,str) for v in value):
        raise ValueError('Angiv mindst ét løb på hver bane, ét navn pr. linje.')
    value=[v.strip() for v in value]
    if any(not v or len(v)>160 for v in value) or len({v.casefold() for v in value})!=len(value):
        raise ValueError('Løbene på en bane skal have forskellige navne på højst 160 tegn.')
    return value


def templates(db,series_id):
    result={}
    for course in COURSES:
        names=[r[0] for r in db.execute('SELECT DISTINCT h.name FROM heats h JOIN round_courses c ON c.id=h.round_course_id JOIN rounds r ON r.id=c.round_id WHERE r.series_id=? AND c.course=? ORDER BY h.name',(series_id,course))]
        result[course]=names or [r[0] for r in db.execute('SELECT DISTINCT class_name FROM boats WHERE series_id=? AND course=? ORDER BY class_name',(series_id,course))] or DEFAULT_FLEETS[course]
    return result


def ensure_round(db,series_id,label,fleets=None,start_times=None):
    found=db.execute('SELECT id FROM rounds WHERE series_id=? AND label=?',(series_id,label)).fetchone()
    if found:return found['id']
    from .planning import start_times as normalize_starts
    times=normalize_starts(get(db,'series',series_id),start_times)
    position=db.execute('SELECT coalesce(max(position),0)+1 FROM rounds WHERE series_id=?',(series_id,)).fetchone()[0]
    identity=db.execute('INSERT INTO rounds(series_id,label,position) VALUES(?,?,?)',(series_id,label,position)).lastrowid
    for course in COURSES:
        course_id=db.execute('INSERT INTO round_courses(round_id,course,start_time) VALUES(?,?,?)',(identity,course,times[course])).lastrowid
        for index,name in enumerate((fleets or templates(db,series_id))[course]):
            heat_id=db.execute('INSERT INTO heats(round_course_id,name,position) VALUES(?,?,?)',(course_id,name,index)).lastrowid
            db.execute('INSERT INTO heat_boats(heat_id,boat_id) SELECT ?,id FROM boats WHERE series_id=? AND course=? AND class_name=?',(heat_id,series_id,course,name))
    return identity


def create_rounds(db,series_id,value,user,fleets=None,start_times=None):
    get(db,'series',series_id)
    names=labels(value)
    existing={r[0].casefold() for r in db.execute('SELECT label FROM rounds WHERE series_id=?',(series_id,))}
    if any(name.casefold() in existing for name in names):
        raise ValueError('Et eller flere af disse ræs findes allerede i serien.')
    layout={c:fleet_names((fleets or templates(db,series_id))[c]) for c in COURSES}
    identities=[ensure_round(db,series_id,name,layout,start_times) for name in names]
    audit(db,user,'rounds.create',{'series_id':series_id,'labels':names})
    return identities


def structure(db,series_id):
    rounds=[dict(r) for r in db.execute('SELECT * FROM rounds WHERE series_id=? ORDER BY position,id',(series_id,))]
    for item in rounds:
        item['courses']=[]
        for course in db.execute('SELECT c.*,r.id race_id FROM round_courses c LEFT JOIN races r ON r.round_course_id=c.id WHERE c.round_id=? ORDER BY c.course DESC',(item['id'],)):
            child=dict(course)
            child['heats']=[dict(r) for r in db.execute('SELECT * FROM heats WHERE round_course_id=? ORDER BY position,id',(child['id'],))]
            for heat in child['heats']:
                heat['boat_ids']=[r[0] for r in db.execute('SELECT boat_id FROM heat_boats WHERE heat_id=? ORDER BY boat_id',(heat['id'],))]
            item['courses'].append(child)
    return rounds


def migrate_legacy(db):
    # Only attach IDs to existing calculations: times, scoring and snapshots stay intact.
    for race in list(db.execute('SELECT * FROM races WHERE round_course_id IS NULL ORDER BY series_id,start,id')):
        round_id=ensure_round(db,race['series_id'],race['label'],templates(db,race['series_id']))
        course_id=db.execute('SELECT id FROM round_courses WHERE round_id=? AND course=?',(round_id,race['course'])).fetchone()[0]
        db.execute('UPDATE races SET round_course_id=? WHERE id=?',(course_id,race['id']))
        system='gph' if race['rating_key']=='GPH' else 'updown' if race['rating_key'].startswith('TAUD') else 'circle'
        db.execute('UPDATE rounds SET handicap_system=? WHERE id=?',(system,round_id))
        for entry in db.execute('SELECT boat_id,class_name FROM entries WHERE race_id=?',(race['id'],)):
            db.execute('INSERT OR IGNORE INTO heats(round_course_id,name,position) VALUES(?,?,0)',(course_id,entry['class_name']))
            heat_id=db.execute('SELECT id FROM heats WHERE round_course_id=? AND name=?',(course_id,entry['class_name'])).fetchone()[0]
            db.execute('DELETE FROM heat_boats WHERE boat_id=? AND heat_id IN (SELECT h.id FROM heats h JOIN round_courses c ON c.id=h.round_course_id WHERE c.round_id=?)',(entry['boat_id'],round_id))
            db.execute('INSERT OR IGNORE INTO heat_boats(heat_id,boat_id) VALUES(?,?)',(heat_id,entry['boat_id']))

    for series in db.execute('SELECT id FROM series WHERE NOT EXISTS (SELECT 1 FROM rounds WHERE rounds.series_id=series.id)'):
        for index in range(1,19):ensure_round(db,series['id'],f'R{index}')


def attach_boat(db,boat_id):
    boat=get(db,'boats',boat_id)
    # Registration is independent of stored results and handicap snapshots.
    for course in db.execute('SELECT c.id,c.round_id FROM round_courses c JOIN rounds r ON r.id=c.round_id WHERE r.series_id=? AND c.course=?',(boat['series_id'],boat['course'])):
        if db.execute('SELECT 1 FROM races r JOIN round_courses c ON c.id=r.round_course_id WHERE c.round_id=? AND (r.published=1 OR EXISTS(SELECT 1 FROM finishes f WHERE f.race_id=r.id AND f.status<>\'DUTY\'))',(course['round_id'],)).fetchone():
            continue
        db.execute('DELETE FROM heat_boats WHERE boat_id=? AND heat_id IN (SELECT h.id FROM heats h JOIN round_courses c ON c.id=h.round_course_id WHERE c.round_id=?)',(boat_id,course['round_id']))
        db.execute('INSERT OR IGNORE INTO heats(round_course_id,name,position) VALUES(?,?,0)',(course['id'],boat['class_name']))
        heat_id=db.execute('SELECT id FROM heats WHERE round_course_id=? AND name=?',(course['id'],boat['class_name'])).fetchone()[0]
        db.execute('INSERT OR IGNORE INTO heat_boats(heat_id,boat_id) VALUES(?,?)',(heat_id,boat_id))
        configured=db.execute('SELECT id FROM races WHERE round_course_id=?',(course['id'],)).fetchone()
        if configured:
            from .service import replace_roster,changed
            replace_roster(db,configured['id']);changed(db,configured['id'])


def members(db,heat_id,data,user):
    heat=db.execute('SELECT h.*,c.course,c.round_id,r.series_id FROM heats h JOIN round_courses c ON c.id=h.round_course_id JOIN rounds r ON r.id=c.round_id WHERE h.id=?',(heat_id,)).fetchone()
    if not heat:raise ValueError('Løbet findes ikke.')
    selected=data.get('boat_ids',[])
    if not isinstance(selected,list) or any(type(v) is not int for v in selected) or len(set(selected))!=len(selected):
        raise ValueError('Vælg både uden gentagelser.')
    eligible={r[0] for r in db.execute('SELECT id FROM boats WHERE series_id=?',(heat['series_id'],))}
    if not set(selected)<=eligible:raise ValueError('Bådene skal tilhøre denne serie.')
    scope=data.get('scope','round')
    if scope not in ('round','series'):raise ValueError('Vælg dette ræs eller hele serien.')
    targets=[dict(heat)] if scope=='round' else [dict(r) for r in db.execute('SELECT h.* FROM heats h JOIN round_courses c ON c.id=h.round_course_id JOIN rounds r ON r.id=c.round_id WHERE r.series_id=? AND c.course=? AND h.name=?',(heat['series_id'],heat['course'],heat['name']))]
    for target in targets:
        parent=db.execute('SELECT p.completed FROM rounds p JOIN round_courses c ON c.round_id=p.id WHERE c.id=?',(target['round_course_id'],)).fetchone()
        if parent and parent[0]:
            current={r[0] for r in db.execute('SELECT boat_id FROM heat_boats WHERE heat_id=?',(target['id'],))}
            if target['id']==heat_id and current!=set(selected):raise ValueError('Ræset er afsluttet. Genåbn begge baner før tilmeldinger ændres.')
            continue
        race=db.execute('SELECT * FROM races WHERE round_course_id=?',(target['round_course_id'],)).fetchone()
        if race and (race['published'] or db.execute("SELECT 1 FROM finishes WHERE race_id=? AND status<>'DUTY'",(race['id'],)).fetchone()):
            current={r[0] for r in db.execute('SELECT boat_id FROM heat_boats WHERE heat_id=?',(target['id'],))}
            if target['id']==heat_id and current!=set(selected):
                raise ValueError('Løbet har allerede resultater. Tilmeldinger ændres i et ræs, hvor måltagningen ikke er begyndt.')
            continue
        for boat_id in selected:
            conflict=db.execute('SELECT h.name FROM heat_boats b JOIN heats h ON h.id=b.heat_id JOIN round_courses c ON c.id=h.round_course_id WHERE b.boat_id=? AND c.round_id=(SELECT round_id FROM round_courses WHERE id=?) AND h.id<>?',(boat_id,target['round_course_id'],target['id'])).fetchone()
            if conflict:raise ValueError(f"Båden er allerede i løbet {conflict['name']} denne aften. Fjern tilmeldingen dér før flytning.")
        db.execute('DELETE FROM heat_boats WHERE heat_id=?',(target['id'],))
        db.executemany('INSERT INTO heat_boats(heat_id,boat_id) VALUES(?,?)',[(target['id'],bid) for bid in selected])
        if race and not db.execute("SELECT 1 FROM finishes WHERE race_id=? AND status<>'DUTY'",(race['id'],)).fetchone():
            # Course configuration follows the edited fleet until timing starts.
            from .service import replace_roster, changed
            replace_roster(db,race['id'])
            changed(db,race['id'])
    audit(db,user,'heat.members',{'heat_id':heat_id,'scope':scope,'boat_ids':selected})


def set_system(db,round_id,system,user):
    get(db,'rounds',round_id)
    if system not in ('circle','gph','updown'):raise ValueError('Vælg cirkelmål, GPH eller op/ned-mål.')
    from .domain import rating_value
    from .service import replace_roster, changed
    import json
    for race in list(db.execute('SELECT r.* FROM races r JOIN round_courses c ON c.id=r.round_course_id WHERE c.round_id=?',(round_id,))):
        if race['published'] or db.execute("SELECT 1 FROM finishes WHERE race_id=? AND status<>'DUTY'",(race['id'],)).fetchone():
            raise ValueError('Beregningsmålet ændres før resultatindtastningen. Historiske resultater kræver en særskilt, forhåndsvist rettelse.')
        wind=race['rating_key'][-1] if race['rating_key'][-1] in ('L','M','H') and race['rating_key']!='GPH' else 'M'
        key='GPH' if system=='gph' else ('TACI' if system=='circle' else 'TAUD')+wind
        from .service import rows
        for entry in rows(db,race['id']):
            value=rating_value(entry['handicap_set']['ratings'],key)
            if value is None:raise ValueError(f'{key} mangler i det gemte grundlag. Ingen baner er ændret.')
        db.execute('UPDATE races SET rating_key=? WHERE id=?',(key,race['id']))
        replace_roster(db,race['id']);changed(db,race['id'])
    db.execute('UPDATE rounds SET handicap_system=? WHERE id=?',(system,round_id))
    audit(db,user,'round.handicap_system',{'round_id':round_id,'handicap_system':system})
