"""HTTP API adapters: authorization and mapping onto domain operations."""
from datetime import datetime
import json, os, re, time
from . import auth, service, hierarchy, calculations, timing, planning, fleet, handicaps
from .store import get, audit
from .redress import apply_redress

def dispatch(app,db,user,can_approve,path,method,params,data,env):
    if method=='POST' and re.fullmatch(r'/api/(?:rounds|races)/\d+/capture',path):
        auth.authorize(user)
        return app.json({'error':'Tider uden båd er fjernet. Vælg en båd og tryk Måltag.'},410)
    clock_action=re.fullmatch(r'/api/rounds/(\d+)/clock',path)
    if clock_action and method=='POST':
        auth.authorize(user)
        from .reference_clock import synchronize
        return app.json(synchronize(db,int(clock_action[1]),data,user))
    observation=re.fullmatch(r'/api/rounds/(\d+)/(capture|discard-capture|finish|edit|undo)',path)
    if observation and method=='POST':
        auth.authorize(user)
        from . import unconfigured
        round_id,action=int(observation[1]),observation[2]
        result=unconfigured.capture(db,round_id,data,user) if action=='capture' else unconfigured.discard(db,round_id,data,user) if action=='discard-capture' else unconfigured.finish(db,round_id,data,user,action)
        return app.json(result)
    preview_token=auth.cookie_value(env,'ob_preview')
    from . import workspaces
    workspace=re.fullmatch(r'/api/workspaces/(series|rounds)/([\w-]+)(/history)?',path)
    if workspace and method=='GET':
        auth.authorize(user,admin=bool(workspace[3]))
        payload=workspaces.series_workspace(db,int(workspace[2])) if workspace[1]=='series' else workspaces.round_workspace(db,workspace[2],history=bool(workspace[3]))
        return app.json(payload)
    unregister=re.fullmatch(r'/api/series/registrations/(\d+)/remove',path)
    if unregister and method=='POST':
        auth.authorize(user,admin=True)
        fleet.unregister(db,int(unregister[1]),user)
        return app.json({'saved':True})
    if path=='/api/state' and method=='GET':
        series = [dict(r) for r in db.execute('SELECT * FROM series ORDER BY year DESC,id DESC')]
        staff = bool(user and user['approved'])
        published_series={r[0] for r in db.execute('SELECT DISTINCT series_id FROM races WHERE published=1')}
        default_series=next((s['id'] for s in series if staff or s['id'] in published_series),series[0]['id'] if series else 0)
        series_id = int(params.get('series_id') or default_series)
        # Schedules are public before racing; draft finishes remain private.
        races = [dict(r) for r in db.execute('SELECT * FROM races WHERE series_id=? ORDER BY start DESC,id DESC',(series_id,))]
        marks = service.read_marks(service.ROOT / 'omvendt_respit/resources/marks.csv')
        return app.json({'series':series,'series_id':series_id,'boats':service.boats(db,series_id),
            'races':races,'rounds':hierarchy.structure(db,series_id),'marks':[vars(m) for m in marks.values()], 'user':user,
            'can_approve':can_approve,'demo':app.demo,'server_time':datetime.now(service.TZ).isoformat(),
            'preview_login_path':('/auth/preview-admin?token='+app.preview_login_token) if app.preview_admin and time.time()<app.preview_expires and env.get('REMOTE_ADDR') in ('127.0.0.1','::1') else None,
            'providers':{p:bool(os.getenv(p.upper()+'_CLIENT_ID') and os.getenv(p.upper()+'_CLIENT_SECRET')) for p in ('google','facebook')},
            'source':json.loads(db.execute("SELECT value FROM metadata WHERE key='seed'").fetchone()[0]) if series and db.execute("SELECT 1 FROM metadata WHERE key='seed'").fetchone() else None})
    if path=='/api/results' and method=='GET':
        drafts = params.get('drafts')=='1'
        if drafts:
            auth.authorize(user)
        return app.json(service.result_view(db,int(params['series_id']),params['course'],params.get('class_name',''),drafts))
    if path=='/api/geometry' and method=='POST':
        return app.json(service.geometry(data['route']))
    if path=='/api/logout' and method=='POST':
        db.execute('DELETE FROM sessions WHERE token=?',(auth.digest(auth.cookie_value(env,'ob_session')),))
        if preview_token:
            app.preview_sessions.pop(auth.digest(preview_token),None)
        return 200,[('Content-Type','application/json'),auth.cookie('ob_session','',app.base_url,0),auth.cookie('ob_preview','',app.base_url,0)],b'{}'
    if path=='/api/series/settings' and method=='POST':
        auth.authorize(user,admin=True)
        service.update_series(db,int(data['series_id']),data,user)
        return app.json({'saved':True})
    if path=='/api/series' and method=='POST':
        auth.authorize(user,admin=True)
        return app.json({'id':service.create_series(db,data,user)})
    round_action=re.fullmatch(r'/api/rounds/(\d+)/(timing|complete|reopen|publish)',path)
    if round_action:
        auth.authorize(user)
        round_id,action=int(round_action[1]),round_action[2]
        if method=='GET' and action=='timing':return app.json(timing.detail(db,round_id))
        if method=='POST' and action in ('complete','reopen','publish'):
            return app.json(getattr(timing,action)(db,round_id,data,user))
    if path=='/api/rounds/starts' and method=='POST':
        auth.authorize(user,admin=True)
        planning.update_round(db,int(data['round_id']),data,user)
        return app.json({'saved':True})
    if path=='/api/boats/handicaps' and method=='GET':
        auth.authorize(user,admin=True)
        return app.json(handicaps.owned(db,int(params['id'])))
    if path=='/api/boats/catalog' and method=='GET':
        auth.authorize(user)
        return app.json(fleet.catalog(db,int(params['series_id'])))
    if path=='/api/boats/enroll' and method=='POST':
        auth.authorize(user,admin=True)
        return app.json({'ids':fleet.enroll(db,int(data['series_id']),data,user)})
    if path=='/api/rounds/handicap-system' and method=='POST':
        auth.authorize(user,admin=True)
        hierarchy.set_system(db,int(data['round_id']),data['handicap_system'],user)
        return app.json({'saved':True})
    if path=='/api/rounds' and method=='POST':
        auth.authorize(user,admin=True)
        return app.json({'ids':hierarchy.create_rounds(db,int(data['series_id']),data['round_labels'],user,start_times=data.get('start_times'))})
    if path=='/api/heats/boats/move' and method=='POST':
        auth.authorize(user,admin=True)
        result=hierarchy.move_profiles(db,int(data['heat_id']),data,user)
        series_id=result['series_id']
        return app.json({**result,'data':{'boats':service.boats(db,series_id),'rounds':hierarchy.structure(db,series_id),
            'races':[dict(r) for r in db.execute('SELECT * FROM races WHERE series_id=? ORDER BY start DESC,id DESC',(series_id,))]},
            'catalog':fleet.catalog(db,series_id)})
    if path in ('/api/heats/boats/preview','/api/heats/boats') and method=='POST':
        auth.authorize(user,admin=True)
        operation=hierarchy.preview_profile_members if path.endswith('/preview') else hierarchy.profile_members
        return app.json(operation(db,int(data['heat_id']),data,user))
    if path=='/api/heats/members/preview' and method=='POST':
        auth.authorize(user,admin=True)
        plan=hierarchy.member_plan(db,int(data['heat_id']),data)
        return app.json({'changes':plan['changes'],'token':plan['token']})
    if path=='/api/heats/members' and method=='POST':
        auth.authorize(user,admin=True)
        return app.json({'saved':True,**hierarchy.members(db,int(data['heat_id']),data,user)})
    if path in ('/api/boat-profiles','/api/boat-profiles/delete-preview','/api/boat-profiles/delete'):
        auth.authorize(user,admin=True)
        if method=='GET' and path=='/api/boat-profiles':
            return app.json(fleet.profile(db,int(params['id'])) if params.get('id') else fleet.catalog(db,-1))
        if method=='GET' and path.endswith('/delete-preview'):
            return app.json(fleet.deletion_preview(db,int(params['id'])))
        if method=='POST' and path in ('/api/boat-profiles','/api/boat-profiles/delete'):
            if path.endswith('/delete'):
                fleet.delete_profile(db,int(data['id']),data,user)
                return app.json({'saved':True})
            identity=fleet.save_profile(db,data,user,int(data['id']) if data.get('id') else None)
            return app.json({'saved':True,'id':identity})
        return app.json({'error':'Metoden er ikke tilladt.'},405)
    if path=='/api/boats' and method=='POST':
        auth.authorize(user,admin=True)
        return app.json({'id':service.save_boat(db,data,user,data.get('id'))})
    if path=='/api/boats/recalculation-preview' and method=='POST':
        auth.authorize(user,admin=True)
        return app.json(service.recalculate_boat(db,int(data['id']),data['ratings'],service.extra_handicap_value(data.get('extra_handicap',0)),data,user))
    if path=='/api/boats/changes' and method=='GET':
        auth.authorize(user,admin=True)
        return app.json([dict(r) for r in db.execute("SELECT a.at,a.payload,u.name FROM audit a LEFT JOIN users u ON u.id=a.user_id WHERE a.action='boat.save' AND json_extract(a.payload,'$.id') IN (SELECT b.id FROM boats b WHERE b.profile_id=(SELECT profile_id FROM boats WHERE id=?)) ORDER BY a.id DESC",(int(params['id']),))])
    if path=='/api/boats/history' and method=='GET':
        auth.authorize(user,admin=True)
        return app.json([{**dict(r),'rating':next(e['rating'] for e in service.rows(db,r['id']) if e['boat_id']==int(params['id']))} for r in db.execute('SELECT r.id,r.label,r.start,r.mode,r.rating_key,r.revision,r.published,e.handicap_set_id,h.extra_handicap FROM races r JOIN entries e ON e.race_id=r.id JOIN handicap_sets h ON h.id=e.handicap_set_id WHERE e.boat_id=? ORDER BY r.start,r.id',(int(params['id']),))])
    if path=='/api/races' and method=='POST':
        auth.authorize(user,admin=True)
        return app.json({'id':service.create_race(db,data,user)})
    if path=='/api/series/reference' and method=='POST':
        auth.authorize(user,admin=True)
        service.series_reference(db,int(data['series_id']),data,user)
        return app.json({'saved':True})
    if path=='/api/race-reference' and method=='POST':
        auth.authorize(user,admin=True)
        service.set_reference(db,int(data['race_id']),data['reference'],user,data['revision'])
        return app.json({'saved':True})
    if path=='/api/day-settings' and method=='POST':
        auth.authorize(user,admin=True)
        return app.json(service.day_settings(db,data,user))
    if path=='/api/users':
        if not can_approve:
            raise PermissionError('Kun hummesse@gmail.com logget ind via Google kan godkende og administrere brugere.')
        if method=='GET':
            return app.json([dict(r) for r in db.execute('SELECT * FROM users ORDER BY approved,name')])
        target = get(db,'users',int(data['id']))
        if auth.can_approve(target):
            raise ValueError('Ejerens adgang kan ikke ændres.')
        if data.get('role') not in ('admin','recorder') or type(data.get('approved')) is not bool:
            raise ValueError('Vælg rolle og godkendelse.')
        db.execute('UPDATE users SET role=?,approved=? WHERE id=?',(data['role'],int(data['approved']),target['id']))
        db.execute('DELETE FROM sessions WHERE user_id=?',(target['id'],))
        audit(db,user,'user.approve',data)
        return app.json({'saved':True})
    if path=='/api/audit' and method=='GET':
        auth.authorize(user,admin=True)
        return app.json([dict(r) for r in db.execute('SELECT a.*,u.name FROM audit a LEFT JOIN users u ON a.user_id=u.id ORDER BY a.id DESC LIMIT 200')])
    if path=='/api/pdf' and method=='GET':
        return app.pdf(db,params)
    match = re.fullmatch(r'/api/races/(\d+)(?:/(capture|discard-capture|finish|undo|edit|publish|redress|reorder|calculation-preview|recalculate))?',path)
    if match:
        race_id,action = int(match[1]),match[2]
        race = get(db,'races',race_id)
        if method=='GET' and not action:
            startlist = params.get('startlist')=='1'
            if not race['published'] and not startlist:
                auth.authorize(user)
            entries = service.rows(db,race_id)
            if startlist:
                entries = [{k:v for k,v in r.items() if k not in {'status','instant','elapsed','corrected','sequence','note','source_points','source_discarded','penalty'}} for r in entries]
            return app.json({'race':race,'entries':entries,'captures':[dict(r) for r in db.execute('SELECT * FROM captures WHERE race_id=? AND used=0 ORDER BY id',(race_id,))] if not startlist and user and user['approved'] else []})
        if method=='POST':
            if action in ('calculation-preview','recalculate'):
                auth.authorize(user,admin=True)
                return app.json(calculations.recalculate(db,race_id,data,user,apply=action=='recalculate'))
            if action=='capture':
                return app.json(service.capture(db,race_id,data,user))
            if action=='discard-capture':
                service.editable(db,race_id)
                cap = get(db,'captures',int(data['id']))
                if cap['race_id']!=race_id or cap['used']:
                    raise ValueError('Tidsstemplet kan ikke kasseres.')
                db.execute('UPDATE captures SET used=1 WHERE id=?',(cap['id'],))
                audit(db,user,'capture.discard',cap)
                return app.json({'saved':True})
            if action=='finish':
                return app.json(service.finish(db,race_id,data,user))
            if action=='undo':
                service.undo(db,race_id,int(data['boat_id']),user,data['revision'])
                return app.json({'saved':True})
            if action=='edit':
                if not str(data.get('note','')).strip():
                    raise ValueError('Skriv årsagen til rettelsen.')
                service.undo(db,race_id,int(data['boat_id']),user,data['revision'])
                return app.json(service.finish(db,race_id,data,user))
            if action=='publish':
                service.publish(db,race_id,data,user)
                return app.json({'saved':True})
            if action=='redress':
                auth.authorize(user,admin=True)
                return app.json(apply_redress(db,race_id,int(data['boat_id']),user,str(data['request_id'])))
            if action=='reorder':
                service.editable(db,race_id,data['revision'])
                if race['mode']!='pursuit':
                    raise ValueError('Kun jagtstarter har en målgangsrækkefølge.')
                if db.execute("SELECT 1 FROM finishes WHERE race_id=? AND status='FIN' AND penalty>0",(race_id,)).fetchone():
                    raise ValueError('Rækkefølgen er beregnet med straftid. Ret måltider eller straftid i stedet for at flytte bådene manuelt.')
                current = [r['boat_id'] for r in db.execute("SELECT boat_id FROM finishes WHERE race_id=? AND status='FIN'",(race_id,))]
                order = data['boat_ids']
                if sorted(order)!=sorted(current) or len(set(order))!=len(order):
                    raise ValueError('Rækkefølgen skal indeholde alle målgangsbåde præcis én gang.')
                for i,boat_id in enumerate(order,1):
                    db.execute('UPDATE finishes SET sequence=? WHERE race_id=? AND boat_id=?',(i,race_id,boat_id))
                db.execute("DELETE FROM finishes WHERE race_id=? AND status='RDG'",(race_id,))
                service.changed(db,race_id)
                audit(db,user,'finish.reorder',{'race_id':race_id,'boat_ids':order})
                return app.json({'saved':True})
    return app.json({'error':'Siden findes ikke.'},404)
