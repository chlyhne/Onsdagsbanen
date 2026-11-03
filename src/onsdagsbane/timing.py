"""One timing session and one completion state for both courses in a round."""
from datetime import datetime
from .store import get, audit
from . import service


def course_races(db,round_id):
    return [dict(r) for r in db.execute('SELECT r.* FROM races r JOIN round_courses c ON c.id=r.round_course_id WHERE c.round_id=? ORDER BY r.id',(round_id,))]


def round_id_for_race(db,race_id):
    row=db.execute('SELECT c.round_id FROM races r JOIN round_courses c ON c.id=r.round_course_id WHERE r.id=?',(race_id,)).fetchone()
    if not row:raise ValueError('Sejladsen mangler et tilknyttet ræs.')
    return row[0]


def detail(db,round_id):
    planned=get(db,'rounds',round_id)
    courses=course_races(db,round_id)
    entries,captures=[],[]
    for race in courses:
        entries.extend({**row,'race_id':race['id'],'race_mode':race['mode'],'race_revision':race['revision'],'published':race['published']} for row in service.rows(db,race['id']))
        captures.extend(dict(r) for r in db.execute('SELECT * FROM captures WHERE race_id=? AND used=0 ORDER BY id',(race['id'],)))
    if len({row['boat_id'] for row in entries})!=len(entries):raise ValueError('Samme båd er tilmeldt begge baner i ræset. Ret tilmeldingerne før måltagning.')
    configured={r['course'] for r in courses}
    missing=[r[0] for r in db.execute('SELECT DISTINCT c.course FROM round_courses c JOIN heats h ON h.round_course_id=c.id JOIN heat_boats m ON m.heat_id=h.id WHERE c.round_id=?',(round_id,)) if r[0] not in configured]
    base=min([r for r in courses if not r['published']] or courses,key=lambda r:(service.instant(r['start']),r['id'])) if courses else {}
    race={**base,'round_id':round_id,'label':planned['label'],'completed':planned['completed'],
          'published':bool(courses) and all(r['published'] for r in courses),'course':'begge',
          'start':min((r['start'] for r in courses),default=None)}
    return {'round':planned,'race':race,'courses':courses,'entries':entries,'captures':sorted(captures,key=lambda c:(c['instant'],c['id'])),'unconfigured_courses':missing}


def check_revisions(courses,revisions):
    if not isinstance(revisions,dict) or any(revisions.get(str(r['id']))!=r['revision'] for r in courses):
        raise ValueError('Resultaterne på en af banerne er ændret. Opdatér og gennemgå dem igen.')


def complete(db,round_id,data,user):
    planned=get(db,'rounds',round_id)
    if planned['completed']:return {'completed':True,'dnc':[]}
    state=detail(db,round_id)
    check_revisions(state['courses'],data.get('revisions'))
    if not state['courses']:raise ValueError('Indstil mindst én bane før ræset afsluttes.')
    if state['unconfigured_courses']:
        names=['Stor bane' if c=='stor' else 'Lille bane' for c in state['unconfigured_courses']]
        raise ValueError('Der er tilmeldte både på en bane uden start og opsætning: '+', '.join(names)+'. Indstil banen før afslutning.')
    if state['captures']:raise ValueError('Tildel eller kassér alle tidsstempler uden båd før ræset afsluttes.')
    if any(service.instant(r['start'])>datetime.now(service.TZ) for r in state['courses']):raise ValueError('Ræset kan først afsluttes, når begge baner er startet. Kontrollér datoen.')
    added=[]
    for race in state['courses']:
        sequence=db.execute('SELECT coalesce(max(sequence),0) FROM finishes WHERE race_id=?',(race['id'],)).fetchone()[0]
        for row in state['entries']:
            if row['race_id']!=race['id'] or row['sequence'] is not None:continue
            sequence+=1
            db.execute("INSERT INTO finishes(race_id,boat_id,status,sequence,note,request_id,auto_dnc) VALUES(?,?,'DNC',?,?,?,1)",(race['id'],row['boat_id'],sequence,'Automatisk DNC ved afslutning af begge baner',f"complete:{round_id}:{race['id']}:{row['boat_id']}"))
            added.append({'boat_id':row['boat_id'],'name':row['name'],'course':race['course']})
        service.changed(db,race['id'])
    db.execute('UPDATE rounds SET completed=1,completed_at=? WHERE id=?',(datetime.now(service.TZ).isoformat(),round_id))
    computed=[]
    from .redress import apply_redress
    for row in state['entries']:
        if row['status']!='DUTY':continue
        try:
            result=apply_redress(db,row['race_id'],row['boat_id'],user,f"duty:{round_id}:{row['boat_id']}:{datetime.now().timestamp()}")
        except ValueError as error:
            raise ValueError(f"Dommertid for {row['name']} kunne ikke beregnes: {error} Ræset er ikke afsluttet.") from None
        computed.append({'boat_id':row['boat_id'],'name':row['name'],**result})
    audit(db,user,'round.complete',{'round_id':round_id,'dnc':added,'race_ids':[r['id'] for r in state['courses']]})
    return {'completed':True,'dnc':added,'redress':computed}


def reopen(db,round_id,data,user):
    courses=course_races(db,round_id)
    check_revisions(courses,data.get('revisions'))
    for race in courses:
        db.execute("DELETE FROM finishes WHERE race_id=? AND status='DNC' AND auto_dnc=1",(race['id'],))
        service.invalidate_redress(db,race['id'])
        db.execute('UPDATE races SET published=0 WHERE id=?',(race['id'],))
        service.changed(db,race['id'])
    db.execute('UPDATE rounds SET completed=0,completed_at=NULL WHERE id=?',(round_id,))
    audit(db,user,'round.reopen',{'round_id':round_id})
    return {'completed':False}


def publish(db,round_id,data,user):
    state=detail(db,round_id)
    check_revisions(state['courses'],data.get('revisions'))
    if any(r['sequence'] is None for r in state['entries']) and not data.get('confirm_dnc'):
        raise ValueError('Afslut ræset, eller bekræft at resterende både får DNC.')
    if not state['round']['completed']:complete(db,round_id,data,user)
    for race in course_races(db,round_id):
        service.publish(db,race['id'],{'published':True,'revision':race['revision']},user)
    audit(db,user,'round.publish',{'round_id':round_id})
    return {'published':True}


def migrate(db):
    # Published legacy results already represent finished racing; no dates are invented.
    db.execute('UPDATE rounds SET completed=1 WHERE completed=0 AND EXISTS(SELECT 1 FROM round_courses c JOIN races r ON r.round_course_id=c.id WHERE c.round_id=rounds.id) AND NOT EXISTS(SELECT 1 FROM round_courses c JOIN races r ON r.round_course_id=c.id WHERE c.round_id=rounds.id AND r.published=0)')
