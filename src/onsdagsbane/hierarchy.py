"""Planned series rounds, courses and fleets, independently of race results."""
import re
from uuid import uuid4
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
    previous=db.execute('SELECT id FROM rounds WHERE series_id=? ORDER BY position DESC,id DESC LIMIT 1',(series_id,)).fetchone()
    identity=db.execute('INSERT INTO rounds(uid,series_id,label,position) VALUES(?,?,?,?)',(str(uuid4()),series_id,label,position)).lastrowid
    for course in COURSES:
        course_id=db.execute('INSERT INTO round_courses(round_id,course,start_time) VALUES(?,?,?)',(identity,course,times[course])).lastrowid
        for index,name in enumerate((fleets or templates(db,series_id))[course]):
            heat_id=db.execute('INSERT INTO heats(round_course_id,name,position) VALUES(?,?,?)',(course_id,name,index)).lastrowid
            if previous:
                db.execute('INSERT INTO heat_boats(heat_id,boat_id) SELECT ?,m.boat_id FROM heat_boats m JOIN heats h ON h.id=m.heat_id JOIN round_courses c ON c.id=h.round_course_id WHERE c.round_id=? AND c.course=? AND h.name=?',(heat_id,previous['id'],course,name))
            else:
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
    from .compatibility import validate_round
    for identity in identities:
        validate_round(db,identity,get(db,'rounds',identity)['handicap_system'])
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
        from .compatibility import validate_destination
        validate_destination(db,course['round_id'],boat['course'],[boat_id])
        db.execute('DELETE FROM heat_boats WHERE boat_id=? AND heat_id IN (SELECT h.id FROM heats h JOIN round_courses c ON c.id=h.round_course_id WHERE c.round_id=?)',(boat_id,course['round_id']))
        db.execute('INSERT OR IGNORE INTO heats(round_course_id,name,position) VALUES(?,?,0)',(course['id'],boat['class_name']))
        heat_id=db.execute('SELECT id FROM heats WHERE round_course_id=? AND name=?',(course['id'],boat['class_name'])).fetchone()[0]
        db.execute('INSERT OR IGNORE INTO heat_boats(heat_id,boat_id) VALUES(?,?)',(heat_id,boat_id))
        configured=db.execute('SELECT id FROM races WHERE round_course_id=?',(course['id'],)).fetchone()
        if configured:
            from .service import replace_roster,changed
            replace_roster(db,configured['id']);changed(db,configured['id'])


def member_plan(db,heat_id,data):
    """Plan fleet moves without changing recorded timing or handicap snapshots."""
    import hashlib
    import json
    heat=db.execute('SELECT h.*,c.course,c.round_id,r.series_id,r.position round_position FROM heats h JOIN round_courses c ON c.id=h.round_course_id JOIN rounds r ON r.id=c.round_id WHERE h.id=?',(heat_id,)).fetchone()
    if not heat:raise ValueError('Løbet findes ikke.')
    selected=data.get('boat_ids',[])
    if not isinstance(selected,list) or any(type(v) is not int for v in selected) or len(set(selected))!=len(selected):
        raise ValueError('Vælg både uden gentagelser.')
    eligible={r[0] for r in db.execute('SELECT id FROM boats WHERE series_id=?',(heat['series_id'],))}
    if not set(selected)<=eligible:raise ValueError('Bådene skal tilhøre denne serie.')
    scope=data.get('scope','series')
    if scope not in ('round','series','forward','backward'):raise ValueError('Vælg hele serien, fremover, bagudrettet eller dette ræs.')
    rounds=[dict(r) for r in db.execute('SELECT * FROM rounds WHERE series_id=? ORDER BY position,id',(heat['series_id'],))]
    targets=[r for r in rounds if scope=='series' or (scope=='round' and r['id']==heat['round_id']) or (scope=='forward' and r['position']>=heat['round_position']) or (scope=='backward' and r['position']<=heat['round_position'])]
    changes=[]; operations=[]
    for parent in targets:
        target=db.execute('SELECT h.* FROM heats h JOIN round_courses c ON c.id=h.round_course_id WHERE c.round_id=? AND c.course=? AND h.name=?',(parent['id'],heat['course'],heat['name'])).fetchone()
        if not target:raise ValueError(f"Løbet {heat['name']} mangler i {parent['label']}.")
        assignments=[dict(r) for r in db.execute('SELECT m.boat_id,h.id heat_id,h.name,c.id course_id,c.course FROM heat_boats m JOIN heats h ON h.id=m.heat_id JOIN round_courses c ON c.id=h.round_course_id WHERE c.round_id=?',(parent['id'],))]
        current={r['boat_id'] for r in assignments if r['heat_id']==target['id']}
        moves=[r for r in assignments if r['boat_id'] in selected and r['heat_id']!=target['id']]
        added=set(selected)-current; removed=current-set(selected)
        if not added and not removed:continue
        from .compatibility import validate_destination
        validate_destination(db,parent['id'],heat['course'],selected)
        pending={r[0] for r in db.execute('SELECT boat_id FROM pending_finishes WHERE round_id=?',(parent['id'],))}
        if removed & pending:
            raise ValueError(f"{parent['label']}: Flyt først båden tilbage til ikke måltaget, før den afmeldes fra ræset.")
        races=[dict(r) for r in db.execute('SELECT r.* FROM races r JOIN round_courses c ON c.id=r.round_course_id WHERE c.round_id=?',(parent['id'],))]
        affected={target['round_course_id']}|{r['course_id'] for r in moves}
        historical=[]
        for race in races:
            if race['round_course_id'] not in affected:continue
            locked=bool(parent['completed'] or race['published'] or db.execute('SELECT 1 FROM finishes WHERE race_id=?',(race['id'],)).fetchone() or db.execute('SELECT 1 FROM captures WHERE race_id=?',(race['id'],)).fetchone())
            if locked:
                if any(m['course']!=heat['course'] for m in moves):
                    raise ValueError(f"{parent['label']}: Både med registreringer kan flyttes mellem løb på samme bane. Baneskift skal ske før måltagning.")
                entries={r[0] for r in db.execute('SELECT boat_id FROM entries WHERE race_id=?',(race['id'],))}
                planned_here={m['boat_id'] for m in assignments if m['course']==race['course']}
                if removed or not set(selected)<=entries|planned_here:
                    raise ValueError(f"{parent['label']}: Deltagere med historik kan ikke afmeldes eller tilføjes her. Flyt i stedet båden ved at vælge den i det nye løb på samme bane.")
                historical.append(race['id'])
        changes.append({'round_id':parent['id'],'label':parent['label'],'added':len(added),'removed':len(removed),'historical':bool(historical)})
        operations.append({'heat_id':target['id'],'round_id':parent['id'],'affected':sorted(affected),'historical':historical})
    # Include current memberships, race revisions and saved observations in the
    # preview token so an intervening edit cannot be overwritten unnoticed.
    snapshot={table:[tuple(r) for r in db.execute(query,(heat['series_id'],))] for table,query in {
        'members':'SELECT m.* FROM heat_boats m JOIN heats h ON h.id=m.heat_id JOIN round_courses c ON c.id=h.round_course_id JOIN rounds p ON p.id=c.round_id WHERE p.series_id=? ORDER BY m.heat_id,m.boat_id',
        'rounds':'SELECT * FROM rounds WHERE series_id=? ORDER BY id',
        'races':'SELECT * FROM races WHERE series_id=? ORDER BY id',
        'entries':'SELECT e.* FROM entries e JOIN races r ON r.id=e.race_id WHERE r.series_id=? ORDER BY e.race_id,e.boat_id',
        'finishes':'SELECT f.* FROM finishes f JOIN races r ON r.id=f.race_id WHERE r.series_id=? ORDER BY f.race_id,f.boat_id',
        'captures':'SELECT c.* FROM captures c JOIN races r ON r.id=c.race_id WHERE r.series_id=? ORDER BY c.id',
        'pending':'SELECT f.* FROM pending_finishes f JOIN rounds p ON p.id=f.round_id WHERE p.series_id=? ORDER BY f.round_id,f.boat_id',
        'round_captures':'SELECT c.* FROM round_captures c JOIN rounds p ON p.id=c.round_id WHERE p.series_id=? ORDER BY c.id',
    }.items()}
    token=hashlib.sha256(json.dumps([heat_id,scope,sorted(selected),snapshot],sort_keys=True).encode()).hexdigest()
    return {'changes':changes,'token':token,'operations':operations,'series_id':heat['series_id'],'course':heat['course'],'name':heat['name'],'scope':scope,'selected':selected}


def members(db,heat_id,data,user):
    from .service import replace_roster, changed
    plan=member_plan(db,heat_id,data)
    if data.get('token') is not None or any(c['historical'] for c in plan['changes']):
        if data.get('token')!=plan['token']:raise ValueError('Forhåndsvis løbsinddelingen igen. Grundlaget kan være ændret.')
    for op in plan['operations']:
        # A selection moves boats out of their former heat in the same round.
        # Change the canonical membership in place so saved observations never
        # lose their participation while a boat moves between heats.
        for boat_id in plan['selected']:
            updated=db.execute('UPDATE participations SET heat_id=? WHERE round_id=? AND boat_id=?',(op['heat_id'],op['round_id'],boat_id)).rowcount
            if not updated:db.execute('INSERT INTO heat_boats(heat_id,boat_id) VALUES(?,?)',(op['heat_id'],boat_id))
        import json
        db.execute('DELETE FROM heat_boats WHERE heat_id=? AND boat_id NOT IN (SELECT value FROM json_each(?))',(op['heat_id'],json.dumps(plan['selected'])))
        for course_id in op['affected']:
            race=db.execute('SELECT id FROM races WHERE round_course_id=?',(course_id,)).fetchone()
            if not race:continue
            if race['id'] in op['historical']:
                for boat_id in plan['selected']:
                    db.execute('UPDATE entries SET class_name=? WHERE race_id=? AND boat_id=?',(plan['name'],race['id'],boat_id))
                # Only classification/points change; finish records and all
                # historical starts, distances and handicap set IDs are retained.
                db.execute('UPDATE races SET published=0 WHERE id=?',(race['id'],))
            else:
                replace_roster(db,race['id'])
                from .unconfigured import apply
                apply(db,op['round_id'],race['id'],user)
            changed(db,race['id'])
    # The last planned round supplies the default for new rounds and registrations.
    last=db.execute('SELECT id FROM rounds WHERE series_id=? ORDER BY position DESC,id DESC LIMIT 1',(plan['series_id'],)).fetchone()
    if last and any(op['round_id']==last['id'] for op in plan['operations']):
        for boat_id in plan['selected']:
            db.execute('UPDATE boats SET course=?,class_name=? WHERE id=?',(plan['course'],plan['name'],boat_id))
    audit(db,user,'heat.members',{'heat_id':heat_id,'scope':plan['scope'],'boat_ids':plan['selected'],'changes':plan['changes']})
    return {'changes':plan['changes']}


def profile_members(db,heat_id,data,user,preview=False):
    """Reuse shared boats and assign the chosen range in one transaction."""
    from . import fleet
    profiles=data.get('profile_ids',[])
    if not isinstance(profiles,list) or any(type(v) is not int for v in profiles) or len(set(profiles))!=len(profiles):
        raise ValueError('Vælg både uden gentagelser.')
    heat=db.execute('SELECT h.name,c.course,p.series_id FROM heats h JOIN round_courses c ON c.id=h.round_course_id JOIN rounds p ON p.id=c.round_id WHERE h.id=?',(heat_id,)).fetchone()
    if not heat:raise ValueError('Løbet findes ikke.')
    existing={r['profile_id']:r['id'] for r in db.execute('SELECT id,profile_id FROM boats WHERE series_id=?',(heat['series_id'],))}
    missing=[identity for identity in profiles if identity not in existing]
    if missing:
        added=fleet.enroll(db,heat['series_id'],{'profile_ids':missing,'course':heat['course'],'class_name':heat['name']},user,attach=False)
        existing.update(zip(missing,added))
    assignment={**data,'boat_ids':[existing[identity] for identity in profiles]}
    if preview:
        plan=member_plan(db,heat_id,assignment)
        return {'changes':plan['changes'],'token':plan['token'],'reused':len(missing)}
    if not data.get('token'):raise ValueError('Forhåndsvis løbsinddelingen før lagring.')
    return {**members(db,heat_id,assignment,user),'reused':len(missing)}


def preview_profile_members(db,heat_id,data,user):
    # Allocate provisional registrations to validate the exact save operation,
    # then roll everything back, including enrollment and audit records.
    db.execute('SAVEPOINT fleet_preview')
    try:
        return profile_members(db,heat_id,data,user,preview=True)
    finally:
        db.execute('ROLLBACK TO fleet_preview')
        db.execute('RELEASE fleet_preview')


def move_profiles(db,heat_id,data,user):
    """Apply arrow deltas to current rosters, atomically across the chosen range."""
    profiles=data.get('profile_ids',[])
    if not isinstance(profiles,list) or not profiles or any(type(v) is not int for v in profiles) or len(set(profiles))!=len(profiles):
        raise ValueError('Markér mindst én båd uden gentagelser.')
    action=data.get('action')
    if action not in ('add','remove'):raise ValueError('Vælg tilføj eller fjern.')
    heat=db.execute('SELECT h.name,c.course,p.series_id,p.position,p.id round_id FROM heats h JOIN round_courses c ON c.id=h.round_course_id JOIN rounds p ON p.id=c.round_id WHERE h.id=?',(heat_id,)).fetchone()
    if not heat:raise ValueError('Løbet findes ikke.')
    scope=data.get('scope','series')
    if scope not in ('series','round','forward','backward'):raise ValueError('Vælg hele serien, fremover, bagudrettet eller dette ræs.')
    if action=='remove' and scope!='round':
        raise ValueError('Vælg ét konkret ræs. Både skal fjernes fra ræs enkeltvis.')
    include_assigned=data.get('include_assigned',False)
    if type(include_assigned) is not bool:raise ValueError('Angiv om allerede fordelte både skal medtages.')
    targets=list(db.execute('SELECT h.id,p.id round_id,p.position FROM heats h JOIN round_courses c ON c.id=h.round_course_id JOIN rounds p ON p.id=c.round_id WHERE p.series_id=? AND c.course=? AND h.name=? ORDER BY p.position,p.id',(heat['series_id'],heat['course'],heat['name'])))
    targets=[t for t in targets if scope=='series' or (scope=='round' and t['round_id']==heat['round_id']) or (scope=='forward' and t['position']>=heat['position']) or (scope=='backward' and t['position']<=heat['position'])]
    if action=='add' and not include_assigned:
        # Use actual round IDs. Labels and dates never establish identity.
        for target in targets:
            for row in db.execute('SELECT b.profile_id,h.name FROM heat_boats m JOIN boats b ON b.id=m.boat_id JOIN heats h ON h.id=m.heat_id JOIN round_courses c ON c.id=h.round_course_id WHERE c.round_id=? AND h.id<>?',(target['round_id'],target['id'])):
                if row['profile_id'] in profiles:
                    raise ValueError(f"Båden er allerede fordelt til {row['name']} i et af de valgte ræs. Markér ‘Medtag allerede fordelte både’ for at flytte den.")
    changes=[];reused=0
    for target in targets:
        current={r[0] for r in db.execute('SELECT b.profile_id FROM heat_boats m JOIN boats b ON b.id=m.boat_id WHERE m.heat_id=?',(target['id'],))}
        desired=current|set(profiles) if action=='add' else current-set(profiles)
        if desired==current:continue
        assignment={'profile_ids':sorted(desired),'scope':'round'}
        preview=preview_profile_members(db,target['id'],assignment,user)
        result=profile_members(db,target['id'],{**assignment,'token':preview['token']},user)
        changes.extend(result['changes']);reused+=result['reused']
    audit(db,user,'heat.move',{'heat_id':heat_id,'scope':scope,'action':action,'include_assigned':include_assigned,'profile_ids':profiles,'changes':changes})
    return {'changes':changes,'reused':reused,'series_id':heat['series_id']}


def set_system(db,round_id,system,user):
    get(db,'rounds',round_id)
    if system not in ('circle','gph','updown'):raise ValueError('Vælg cirkelmål, GPH eller op/ned-mål.')
    from .compatibility import validate_round
    validate_round(db,round_id,system)
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
