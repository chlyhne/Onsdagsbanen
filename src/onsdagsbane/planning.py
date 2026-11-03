"""Series start-time defaults and independent start times for each round."""
from datetime import date, datetime
import re
from .store import get, audit

DEFAULT_STARTS={'stor':'18:30:00','lille':'18:35:00'}


def clock(value):
    raw=str(value).strip().replace('.',':').replace(',',':')
    if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?',raw):
        raise ValueError('Starttider skal være gyldige klokkeslæt som HH:MM:SS.')
    return raw if len(raw)==8 else raw+':00'


def defaults(series,data=None):
    data=data or {}
    return {course:clock(data.get('default_start_'+course,series.get('default_start_'+course,DEFAULT_STARTS[course]))) for course in DEFAULT_STARTS}


def start_times(series,provided=None):
    provided=provided or {}
    if not isinstance(provided,dict):raise ValueError('Angiv starttid for begge baner.')
    return {course:clock(provided.get(course,defaults(series)[course])) for course in DEFAULT_STARTS}


def migrate(db):
    from .service import instant
    for course in list(db.execute('SELECT c.id,c.course,c.start_time,r.start,p.race_date,p.id round_id,s.default_start_stor,s.default_start_lille FROM round_courses c JOIN rounds p ON p.id=c.round_id JOIN series s ON s.id=p.series_id LEFT JOIN races r ON r.round_course_id=c.id')):
        actual=instant(course['start']) if course['start'] else None
        if not course['start_time']:
            value=actual.strftime('%H:%M:%S') if actual else course['default_start_'+course['course']]
            db.execute('UPDATE round_courses SET start_time=? WHERE id=?',(value,course['id']))
        if actual and not course['race_date']:
            db.execute('UPDATE rounds SET race_date=? WHERE id=? AND race_date IS NULL',(actual.date().isoformat(),course['round_id']))


def update_round(db,round_id,data,user):
    from .service import replace_roster, changed, instant
    before=get(db,'rounds',round_id)
    if before['completed']:raise ValueError('Ræset er afsluttet. Starttider ændres før måltagningen begynder.')
    series=get(db,'series',before['series_id'])
    existing={r['course']:r['start_time'] for r in db.execute('SELECT course,start_time FROM round_courses WHERE round_id=?',(round_id,))}
    times=start_times(series,{**existing,**data.get('start_times',{})})
    day=data.get('race_date',before.get('race_date')) or None
    if day:
        try:parsed=date.fromisoformat(day)
        except (ValueError,TypeError):raise ValueError('Angiv en gyldig dato.') from None
        if parsed.year!=series['year']:raise ValueError('Datoen skal ligge i seriens årstal.')
    courses=list(db.execute('SELECT c.*,r.id race_id,r.published FROM round_courses c LEFT JOIN races r ON r.round_course_id=c.id WHERE c.round_id=?',(round_id,)))
    for course in courses:
        if not course['race_id']:continue
        if not day:raise ValueError('En indstillet sejlads skal have en dato.')
        if course['published'] or db.execute("SELECT 1 FROM finishes WHERE race_id=? AND status<>'DUTY'",(course['race_id'],)).fetchone() or db.execute('SELECT 1 FROM captures WHERE race_id=? AND used=0',(course['race_id'],)).fetchone():
            raise ValueError('Starttider kan kun ændres før måltagningen begynder. Gemte måltider og historiske starttider bevares.')
    for course in courses:
        db.execute('UPDATE round_courses SET start_time=? WHERE id=?',(times[course['course']],course['id']))
        if course['race_id']:
            stamp=instant(day+'T'+times[course['course']]).isoformat()
            db.execute('UPDATE races SET start=? WHERE id=?',(stamp,course['race_id']))
            replace_roster(db,course['race_id']);changed(db,course['race_id'])
    db.execute('UPDATE rounds SET race_date=? WHERE id=?',(day,round_id))
    audit(db,user,'round.starts',{'round_id':round_id,'before':{'race_date':before.get('race_date'),'start_times':existing},'race_date':day,'start_times':times})
