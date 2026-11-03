"""One definition of the handicap required by a participation."""
import json
from .domain import rating_value


class HandicapError(ValueError):
    def __init__(self,issues):
        self.issues=issues
        super().__init__('; '.join(f"{i['name']} mangler {'historisk ' if i['race_id'] else ''}{', '.join(i['required_fields'])} til {i['round_label']} ({i['required_key']})" for i in issues))


def required_fields(key):
    return ('TACIL','TACIM','TACIH') if key=='GPH' else (key,)


def system_key(system,wind='M'):
    return 'GPH' if system=='gph' else ('TAUD' if system=='updown' else 'TACI')+wind


def issues_for(db,boat_ids,key,round_id,race_id=None,profile_ratings=None):
    label=db.execute('SELECT label FROM rounds WHERE id=?',(round_id,)).fetchone()[0]
    issues=[]
    for boat_id in boat_ids:
        boat=db.execute('SELECT b.profile_id,p.name,h.ratings FROM boats b JOIN boat_profiles p ON p.id=b.profile_id JOIN handicap_sets h ON h.id=p.current_handicap_set_id WHERE b.id=?',(boat_id,)).fetchone()
        if not boat:raise ValueError('Båden findes ikke.')
        entry=db.execute('SELECT e.handicap_set_id,h.ratings FROM entries e JOIN handicap_sets h ON h.id=e.handicap_set_id WHERE e.race_id=? AND e.boat_id=?',(race_id,boat_id)).fetchone() if race_id else None
        ratings=json.loads((entry or boat)['ratings'])
        if not entry and profile_ratings and boat['profile_id'] in profile_ratings:ratings=profile_ratings[boat['profile_id']]
        if rating_value(ratings,key) is not None:continue
        missing=[f for f in required_fields(key) if ratings.get(f) is None]
        issues.append({'profile_id':boat['profile_id'],'boat_id':boat_id,'name':boat['name'],'round_id':round_id,'round_label':label,'race_id':race_id if entry else None,'handicap_set_id':entry['handicap_set_id'] if entry else None,'required_key':key,'required_fields':missing,'ratings':ratings})
    return issues


def validate_destination(db,round_id,course,boat_ids):
    target=db.execute('SELECT p.handicap_system,r.id race_id,r.rating_key FROM rounds p JOIN round_courses c ON c.round_id=p.id LEFT JOIN races r ON r.round_course_id=c.id WHERE p.id=? AND c.course=?',(round_id,course)).fetchone()
    issues=issues_for(db,boat_ids,target['rating_key'] or system_key(target['handicap_system']),round_id,target['race_id'])
    if issues:raise HandicapError(issues)


def validate_round(db,round_id,system):
    issues=[]
    for course in db.execute('SELECT c.id,r.id race_id,r.rating_key FROM round_courses c LEFT JOIN races r ON r.round_course_id=c.id WHERE c.round_id=?',(round_id,)):
        old_key=course['rating_key'] or ''
        wind=old_key[-1] if old_key and old_key!='GPH' and old_key[-1] in ('L','M','H') else 'M'
        boats=[r[0] for r in db.execute('SELECT p.boat_id FROM heat_boats p JOIN heats h ON h.id=p.heat_id WHERE h.round_course_id=?',(course['id'],))]
        issues.extend(issues_for(db,boats,system_key(system,wind),round_id,course['race_id']))
    if issues:raise HandicapError(issues)


def validate_profile(db,profile_id,ratings):
    issues=[]
    for row in db.execute('SELECT p.round_id,p.boat_id,r.handicap_system FROM participations p JOIN boats b ON b.id=p.boat_id JOIN rounds r ON r.id=p.round_id WHERE b.profile_id=? AND p.race_id IS NULL',(profile_id,)):
        issues.extend(issues_for(db,[row['boat_id']],system_key(row['handicap_system']),row['round_id'],profile_ratings={profile_id:ratings}))
    if issues:raise HandicapError(issues[:1])


def supplement(db,profile_id,ratings,context,user):
    """Explicitly fill missing snapshot values; never replace a historical value."""
    from . import handicaps
    from .store import audit
    entry=db.execute('SELECT e.*,h.ratings,h.extra_handicap,r.rating_key,b.profile_id FROM entries e JOIN handicap_sets h ON h.id=e.handicap_set_id JOIN races r ON r.id=e.race_id JOIN boats b ON b.id=e.boat_id WHERE e.race_id=? AND e.boat_id=?',(int(context['race_id']),int(context['boat_id']))).fetchone()
    if not entry or entry['profile_id']!=profile_id or entry['handicap_set_id']!=int(context['handicap_set_id']):raise ValueError('Deltagelsen er ændret. Gennemgå handicapfejlen igen.')
    before=json.loads(entry['ratings']);after=dict(before)
    for field in required_fields(context['required_key']):
        if after.get(field) is None:after[field]=ratings.get(field)
    if rating_value(after,context['required_key']) is None:raise ValueError('Udfyld de manglende handicaptal først.')
    old_value=rating_value(before,entry['rating_key']) or before.get(entry['rating_key'])
    new_value=rating_value(after,entry['rating_key']) or after.get(entry['rating_key'])
    if old_value!=new_value and db.execute("SELECT 1 FROM finishes WHERE race_id=? AND boat_id=? AND status='FIN'",(entry['race_id'],entry['boat_id'])).fetchone():raise ValueError('Denne rettelse ændrer det anvendte historiske handicap. Brug ræsets Beregningsgrundlag og forhåndsvis genberegningen.')
    identity=handicaps.for_values(db,profile_id,after,entry['extra_handicap'])
    db.execute('UPDATE entries SET handicap_set_id=? WHERE race_id=? AND boat_id=?',(identity,entry['race_id'],entry['boat_id']))
    audit(db,user,'handicap.supplement',{'race_id':entry['race_id'],'boat_id':entry['boat_id'],'before_set':entry['handicap_set_id'],'after_set':identity,'required_key':context['required_key']})
