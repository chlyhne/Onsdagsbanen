"""Create and revise a course using the same atomic autosave operation."""
import json
from .store import get
from .services import common, corrections, participation


def preview(db,data):
    round_id=int(data['round_id'])
    get(db,'rounds',round_id)
    course=data['course']
    if course not in ('stor','lille'):raise ValueError('Vælg stor eller lille bane.')
    result=common.geometry(data['route'])
    courses=[{'course':course,'rounding_marks':result['rounding_marks']}]
    for row in db.execute('SELECT r.course,r.route FROM races r JOIN round_courses c ON c.id=r.round_course_id WHERE c.round_id=? AND r.course<>?',(round_id,course)):
        route=json.loads(row['route'])
        courses.append({'course':row['course'],'rounding_marks':common.geometry(route)['rounding_marks'] if route else []})
    return {**result,'rounding_conflicts':common.rounding_conflicts(courses)}


def save(db, data, user):
    round_id = int(data['round_id'])
    planned = get(db, 'rounds', round_id)
    if planned['series_id'] != int(data['series_id']):
        raise ValueError('Ræset tilhører en anden serie.')
    course = data['course']
    if course not in ('stor', 'lille'):
        raise ValueError('Vælg stor eller lille bane.')
    race = db.execute('SELECT r.* FROM races r JOIN round_courses c ON c.id=r.round_course_id WHERE c.round_id=? AND c.course=?', (round_id, course)).fetchone()
    if race is None:
        if data.get('revision') is not None:
            raise ValueError('Banen er ændret. Åbn baneindstillingen igen.')
        identity = participation.create_race(db, data, user)
        return {'race': get(db, 'races', identity)}
    race = dict(race)
    # Once created, date, starts and the shared calculation system use their
    # existing operations. Autosaving route/wind must never replace these.
    for key in ('start', 'mode', 'reference'):
        value = data.get(key, race[key])
        if key == 'start': value = common.instant(value).isoformat()
        if key == 'reference': value = float(str(value).replace(',', '.'))
        if value != race[key]:
            raise ValueError('Dato, startform og reference ændres særskilt fra den indstillede bane.')
    if data.get('handicap_system', planned['handicap_system']) != planned['handicap_system']:
        raise ValueError('Beregningsmålet gælder begge baner og ændres i ræsets opsætning.')
    wind = data.get('wind_category', '')
    if race['rating_key'] == 'GPH':
        if wind: raise ValueError('Vindkategori kan ikke indstilles ved GPH.')
        key = 'GPH'
    else:
        if wind not in ('L', 'M', 'H'): raise ValueError('Vælg en vindkategori.')
        key = race['rating_key'][:4] + wind
    route = data.get('route', json.loads(race['route']))
    if not isinstance(route, list): raise ValueError('Vælg en mærkerækkefølge.')
    distance=common.course_distance(route,data.get('distance',race['distance'] if not json.loads(race['route']) else None))
    if route == json.loads(race['route']) and key == race['rating_key'] and distance == race['distance']:
        return {'race': race}
    if planned['completed']:
        raise ValueError('Ræset er afsluttet. Genåbn ræset før banen ændres.')
    if race['revision'] != data.get('revision'):
        raise ValueError('Sejladsen er ændret. Åbn baneindstillingen igen før du retter den.')
    settings = {'series_id': planned['series_id'], 'date': race['start'][:10],
                'course': course, 'route': route, 'wind_category': wind}
    manual_distance=distance if not route else None
    preview = corrections.day_settings(db, settings, user, race_id=race['id'],distance_override=manual_distance)
    corrections.day_settings(db, {**settings, 'apply': True, 'token': preview['token']}, user, race_id=race['id'],distance_override=manual_distance)
    return {'race': get(db, 'races', race['id'])}
