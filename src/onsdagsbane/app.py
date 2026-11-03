from datetime import datetime
from http import HTTPStatus
import io
import json
import logging
import mimetypes
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
import hmac
from urllib.parse import parse_qs
from urllib.error import URLError, HTTPError

from . import auth, service
from .store import Store, get, audit
from .importer import seed
from .redress import apply_redress
from . import hierarchy, calculations, timing, planning, fleet, handicaps

STATIC = Path(__file__).parent / 'static'
LOG = logging.getLogger(__name__)


class Application:
    def __init__(self, db_path=None, base_url=None, demo=False, initialize=True, preview_admin=False):
        self.store = Store(db_path or os.getenv('ONSDAGSBANE_DB','build/onsdagsbane/app.sqlite3'))
        self.base_url = (base_url or os.getenv('BASE_URL') or os.getenv('RENDER_EXTERNAL_URL') or 'http://localhost:8090').rstrip('/')
        if not re.fullmatch(r'https?://[^/]+',self.base_url):
            raise ValueError('BASE_URL skal være et origin, fx https://onsdagsbanen.dk')
        self.demo = demo
        self.preview_admin = preview_admin
        self.preview_login_token = secrets.token_urlsafe(32) if preview_admin else None
        self.preview_expires = time.time()+7200
        self.preview_sessions = {}
        if initialize:
            seed(self.store)

    def __call__(self,environ,start_response):
        try:
            status,headers,body = self.dispatch(environ)
        except PermissionError as exc:
            status,headers,body = self.json({'error':str(exc)},403)
        except (ValueError,KeyError,TypeError) as exc:
            status,headers,body = self.json({'error':str(exc)},400)
        except sqlite3.IntegrityError:
            status,headers,body = self.json({'error':'Posten findes allerede eller bryder en datakontrol. Opdatér siden.'},409)
        except (URLError,HTTPError):
            status,headers,body = self.json({'error':'Loginudbyderen kunne ikke kontaktes. Prøv igen.'},502)
        except Exception:
            LOG.exception('Request failed')
            status,headers,body = self.json({'error':'Der opstod en serverfejl. Ingen delvise ændringer er gemt.'},500)
        headers.extend([('X-Content-Type-Options','nosniff'),('Referrer-Policy','same-origin'),
            ('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"),
            ('X-Frame-Options','DENY'),('Cache-Control','no-store')])
        start_response(f'{status} {HTTPStatus(status).phrase}',headers)
        return [body]

    @staticmethod
    def json(data,status=200):
        return status,[('Content-Type','application/json; charset=utf-8')],json.dumps(data,ensure_ascii=False,allow_nan=False).encode()

    def dispatch(self,env):
        path,method = env.get('PATH_INFO','/'),env.get('REQUEST_METHOD','GET')
        params = {k:v[-1] for k,v in parse_qs(env.get('QUERY_STRING','')).items()}
        if method=='GET' and path=='/healthz':
            with self.store.connect() as db:
                db.execute('SELECT 1')
            return self.json({'ok':True})
        if method=='GET' and (path=='/' or path in ('/app.js','/fleet-commander.js','/boat-management.js','/style.css')):
            filename = STATIC / ('index.html' if path=='/' else path[1:])
            return 200,[('Content-Type',mimetypes.guess_type(filename)[0]+'; charset=utf-8')],filename.read_bytes()
        if not path.startswith(('/api/','/auth/')):
            return self.json({'error':'Siden findes ikke.'},404)
        if method not in ('GET','POST'):
            return self.json({'error':'Metoden er ikke tilladt.'},405)
        data = {}
        if method=='POST':
            origin = env.get('HTTP_ORIGIN')
            if origin and origin != self.base_url:
                raise PermissionError('Ugyldig forespørgselsoprindelse.')
            if 'application/json' not in env.get('CONTENT_TYPE',''):
                raise ValueError('Forventede JSON.')
            length = int(env.get('CONTENT_LENGTH') or 0)
            if not 0 < length <= 100000:
                raise ValueError('Forespørgslen har en ugyldig størrelse.')
            data = json.loads(env['wsgi.input'].read(length))
            if not isinstance(data,dict):
                raise ValueError('Forventede et objekt.')
        with self.store.connect(write=method=='POST' or path.startswith('/auth/')) as db:
            user = auth.current_user(db,env)
            preview_token = auth.cookie_value(env,'ob_preview')
            preview_session = self.preview_sessions.get(auth.digest(preview_token)) if preview_token else None
            if self.preview_admin and preview_session and preview_session['expires']>time.time() and env.get('REMOTE_ADDR') in ('127.0.0.1','::1'):
                user = {'id':None,'provider':'preview','subject':'temporary-admin',
                    'email':auth.OWNER,'name':'Midlertidig administrator',
                    'role':'admin','approved':1,'csrf':preview_session['csrf']}
            can_approve = auth.can_approve(user) or bool(self.preview_admin and user and user['provider']=='preview')
            if path=='/auth/preview-admin' and self.preview_admin:
                if method!='GET' or env.get('REMOTE_ADDR') not in ('127.0.0.1','::1') or time.time()>self.preview_expires or not hmac.compare_digest(params.get('token',''),self.preview_login_token):
                    raise PermissionError('Det midlertidige preview-login er ugyldigt eller udløbet.')
                token = secrets.token_urlsafe(40)
                self.preview_sessions[auth.digest(token)] = {'csrf':secrets.token_urlsafe(32),'expires':self.preview_expires}
                audit(db,None,'preview.login',{'expires':self.preview_expires})
                return 303,[('Location','/'),auth.cookie('ob_preview',token,self.base_url,max(0,int(self.preview_expires-time.time())))],b''
            if self.demo:
                user = {'id':None,'provider':'demo','subject':'demo','email':'demo@localhost','name':'Lokal demonstration','role':'admin','approved':1,'csrf':'local-demo'}
            if path.startswith('/auth/'):
                if self.demo:
                    raise ValueError('Login er slået fra i den isolerede demonstration.')
                match = re.fullmatch(r'/auth/(google|facebook)(/callback)?',path)
                if not match or method!='GET':
                    raise ValueError('Ugyldig loginadresse.')
                if match[2]:
                    headers = auth.login_callback(db,match[1],params,env,self.base_url)
                    return 303,[('Location','/'),*headers],b''
                url,header = auth.login_start(db,match[1],self.base_url)
                return 303,[('Location',url),header],b''
            if method=='POST':
                auth.authorize(user)
                if not __import__('hmac').compare_digest(env.get('HTTP_X_CSRF_TOKEN',''),user['csrf']):
                    raise PermissionError('Sikkerhedstoken mangler. Opdatér siden.')
            if path=='/api/state' and method=='GET':
                series = [dict(r) for r in db.execute('SELECT * FROM series ORDER BY year DESC,id DESC')]
                staff = bool(user and user['approved'])
                published_series={r[0] for r in db.execute('SELECT DISTINCT series_id FROM races WHERE published=1')}
                default_series=next((s['id'] for s in series if staff or s['id'] in published_series),series[0]['id'] if series else 0)
                series_id = int(params.get('series_id') or default_series)
                # Schedules are public before racing; draft finishes remain private.
                races = [dict(r) for r in db.execute('SELECT * FROM races WHERE series_id=? ORDER BY start DESC,id DESC',(series_id,))]
                marks = service.read_marks(service.ROOT / 'omvendt_respit/resources/marks.csv')
                return self.json({'series':series,'series_id':series_id,'boats':service.boats(db,series_id),
                    'races':races,'rounds':hierarchy.structure(db,series_id),'marks':[vars(m) for m in marks.values()], 'user':user,
                    'can_approve':can_approve,'demo':self.demo,'server_time':datetime.now(service.TZ).isoformat(),
                    'preview_login_path':('/auth/preview-admin?token='+self.preview_login_token) if self.preview_admin and time.time()<self.preview_expires and env.get('REMOTE_ADDR') in ('127.0.0.1','::1') else None,
                    'providers':{p:bool(os.getenv(p.upper()+'_CLIENT_ID') and os.getenv(p.upper()+'_CLIENT_SECRET')) for p in ('google','facebook')},
                    'source':json.loads(db.execute("SELECT value FROM metadata WHERE key='seed'").fetchone()[0]) if series and db.execute("SELECT 1 FROM metadata WHERE key='seed'").fetchone() else None})
            if path=='/api/results' and method=='GET':
                drafts = params.get('drafts')=='1'
                if drafts:
                    auth.authorize(user)
                return self.json(service.result_view(db,int(params['series_id']),params['course'],params.get('class_name',''),drafts))
            if path=='/api/geometry' and method=='POST':
                return self.json(service.geometry(data['route']))
            if path=='/api/logout' and method=='POST':
                db.execute('DELETE FROM sessions WHERE token=?',(auth.digest(auth.cookie_value(env,'ob_session')),))
                if preview_token:
                    self.preview_sessions.pop(auth.digest(preview_token),None)
                return 200,[('Content-Type','application/json'),auth.cookie('ob_session','',self.base_url,0),auth.cookie('ob_preview','',self.base_url,0)],b'{}'
            if path=='/api/series/settings' and method=='POST':
                auth.authorize(user,admin=True)
                service.update_series(db,int(data['series_id']),data,user)
                return self.json({'saved':True})
            if path=='/api/series' and method=='POST':
                auth.authorize(user,admin=True)
                return self.json({'id':service.create_series(db,data,user)})
            round_action=re.fullmatch(r'/api/rounds/(\d+)/(timing|complete|reopen|publish)',path)
            if round_action:
                auth.authorize(user)
                round_id,action=int(round_action[1]),round_action[2]
                if method=='GET' and action=='timing':return self.json(timing.detail(db,round_id))
                if method=='POST' and action in ('complete','reopen','publish'):
                    return self.json(getattr(timing,action)(db,round_id,data,user))
            if path=='/api/rounds/starts' and method=='POST':
                auth.authorize(user,admin=True)
                planning.update_round(db,int(data['round_id']),data,user)
                return self.json({'saved':True})
            if path=='/api/boats/handicaps' and method=='GET':
                auth.authorize(user,admin=True)
                return self.json(handicaps.owned(db,int(params['id'])))
            if path=='/api/boats/catalog' and method=='GET':
                auth.authorize(user)
                return self.json(fleet.catalog(db,int(params['series_id'])))
            if path=='/api/boats/enroll' and method=='POST':
                auth.authorize(user,admin=True)
                return self.json({'ids':fleet.enroll(db,int(data['series_id']),data,user)})
            if path=='/api/rounds/handicap-system' and method=='POST':
                auth.authorize(user,admin=True)
                hierarchy.set_system(db,int(data['round_id']),data['handicap_system'],user)
                return self.json({'saved':True})
            if path=='/api/rounds' and method=='POST':
                auth.authorize(user,admin=True)
                return self.json({'ids':hierarchy.create_rounds(db,int(data['series_id']),data['round_labels'],user,start_times=data.get('start_times'))})
            if path=='/api/heats/boats/move' and method=='POST':
                auth.authorize(user,admin=True)
                result=hierarchy.move_profiles(db,int(data['heat_id']),data,user)
                series_id=result['series_id']
                return self.json({**result,'data':{'boats':service.boats(db,series_id),'rounds':hierarchy.structure(db,series_id),
                    'races':[dict(r) for r in db.execute('SELECT * FROM races WHERE series_id=? ORDER BY start DESC,id DESC',(series_id,))]},
                    'catalog':fleet.catalog(db,series_id)})
            if path in ('/api/heats/boats/preview','/api/heats/boats') and method=='POST':
                auth.authorize(user,admin=True)
                operation=hierarchy.preview_profile_members if path.endswith('/preview') else hierarchy.profile_members
                return self.json(operation(db,int(data['heat_id']),data,user))
            if path=='/api/heats/members/preview' and method=='POST':
                auth.authorize(user,admin=True)
                plan=hierarchy.member_plan(db,int(data['heat_id']),data)
                return self.json({'changes':plan['changes'],'token':plan['token']})
            if path=='/api/heats/members' and method=='POST':
                auth.authorize(user,admin=True)
                return self.json({'saved':True,**hierarchy.members(db,int(data['heat_id']),data,user)})
            if path in ('/api/boat-profiles','/api/boat-profiles/delete-preview','/api/boat-profiles/delete'):
                auth.authorize(user,admin=True)
                if method=='GET' and path=='/api/boat-profiles':
                    return self.json(fleet.profile(db,int(params['id'])))
                if method=='GET' and path.endswith('/delete-preview'):
                    return self.json(fleet.deletion_preview(db,int(params['id'])))
                if method=='POST' and path in ('/api/boat-profiles','/api/boat-profiles/delete'):
                    operation=fleet.delete_profile if path.endswith('/delete') else fleet.update_profile
                    operation(db,int(data['id']),data,user)
                    return self.json({'saved':True})
                return self.json({'error':'Metoden er ikke tilladt.'},405)
            if path=='/api/boats' and method=='POST':
                auth.authorize(user,admin=True)
                return self.json({'id':service.save_boat(db,data,user,data.get('id'))})
            if path=='/api/boats/recalculation-preview' and method=='POST':
                auth.authorize(user,admin=True)
                return self.json(service.recalculate_boat(db,int(data['id']),data['ratings'],service.extra_handicap_value(data.get('extra_handicap',0)),data,user))
            if path=='/api/boats/changes' and method=='GET':
                auth.authorize(user,admin=True)
                return self.json([dict(r) for r in db.execute("SELECT a.at,a.payload,u.name FROM audit a LEFT JOIN users u ON u.id=a.user_id WHERE a.action='boat.save' AND json_extract(a.payload,'$.id') IN (SELECT b.id FROM boats b WHERE b.profile_id=(SELECT profile_id FROM boats WHERE id=?)) ORDER BY a.id DESC",(int(params['id']),))])
            if path=='/api/boats/history' and method=='GET':
                auth.authorize(user,admin=True)
                return self.json([{**dict(r),'rating':next(e['rating'] for e in service.rows(db,r['id']) if e['boat_id']==int(params['id']))} for r in db.execute('SELECT r.id,r.label,r.start,r.mode,r.rating_key,r.revision,r.published,e.handicap_set_id,h.extra_handicap FROM races r JOIN entries e ON e.race_id=r.id JOIN handicap_sets h ON h.id=e.handicap_set_id WHERE e.boat_id=? ORDER BY r.start,r.id',(int(params['id']),))])
            if path=='/api/races' and method=='POST':
                auth.authorize(user,admin=True)
                return self.json({'id':service.create_race(db,data,user)})
            if path=='/api/series/reference' and method=='POST':
                auth.authorize(user,admin=True)
                service.series_reference(db,int(data['series_id']),data,user)
                return self.json({'saved':True})
            if path=='/api/race-reference' and method=='POST':
                auth.authorize(user,admin=True)
                service.set_reference(db,int(data['race_id']),data['reference'],user,data['revision'])
                return self.json({'saved':True})
            if path=='/api/day-settings' and method=='POST':
                auth.authorize(user,admin=True)
                return self.json(service.day_settings(db,data,user))
            if path=='/api/users':
                if not can_approve:
                    raise PermissionError('Kun hummesse@gmail.com logget ind via Google kan godkende og administrere brugere.')
                if method=='GET':
                    return self.json([dict(r) for r in db.execute('SELECT * FROM users ORDER BY approved,name')])
                target = get(db,'users',int(data['id']))
                if auth.can_approve(target):
                    raise ValueError('Ejerens adgang kan ikke ændres.')
                if data.get('role') not in ('admin','recorder') or type(data.get('approved')) is not bool:
                    raise ValueError('Vælg rolle og godkendelse.')
                db.execute('UPDATE users SET role=?,approved=? WHERE id=?',(data['role'],int(data['approved']),target['id']))
                db.execute('DELETE FROM sessions WHERE user_id=?',(target['id'],))
                audit(db,user,'user.approve',data)
                return self.json({'saved':True})
            if path=='/api/audit' and method=='GET':
                auth.authorize(user,admin=True)
                return self.json([dict(r) for r in db.execute('SELECT a.*,u.name FROM audit a LEFT JOIN users u ON a.user_id=u.id ORDER BY a.id DESC LIMIT 200')])
            if path=='/api/pdf' and method=='GET':
                return self.pdf(db,params)
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
                    return self.json({'race':race,'entries':entries,'captures':[dict(r) for r in db.execute('SELECT * FROM captures WHERE race_id=? AND used=0 ORDER BY id',(race_id,))] if not startlist and user and user['approved'] else []})
                if method=='POST':
                    if action in ('calculation-preview','recalculate'):
                        auth.authorize(user,admin=True)
                        return self.json(calculations.recalculate(db,race_id,data,user,apply=action=='recalculate'))
                    if action=='capture':
                        return self.json(service.capture(db,race_id,data,user))
                    if action=='discard-capture':
                        service.editable(db,race_id)
                        cap = get(db,'captures',int(data['id']))
                        if cap['race_id']!=race_id or cap['used']:
                            raise ValueError('Tidsstemplet kan ikke kasseres.')
                        db.execute('UPDATE captures SET used=1 WHERE id=?',(cap['id'],))
                        audit(db,user,'capture.discard',cap)
                        return self.json({'saved':True})
                    if action=='finish':
                        return self.json(service.finish(db,race_id,data,user))
                    if action=='undo':
                        service.undo(db,race_id,int(data['boat_id']),user,data['revision'])
                        return self.json({'saved':True})
                    if action=='edit':
                        if not str(data.get('note','')).strip():
                            raise ValueError('Skriv årsagen til rettelsen.')
                        service.undo(db,race_id,int(data['boat_id']),user,data['revision'])
                        return self.json(service.finish(db,race_id,data,user))
                    if action=='publish':
                        service.publish(db,race_id,data,user)
                        return self.json({'saved':True})
                    if action=='redress':
                        auth.authorize(user,admin=True)
                        return self.json(apply_redress(db,race_id,int(data['boat_id']),user,str(data['request_id'])))
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
                        return self.json({'saved':True})
            return self.json({'error':'Siden findes ikke.'},404)

    def pdf(self,db,params):
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import SimpleDocTemplate,Paragraph,Table,TableStyle,Spacer
        from xml.sax.saxutils import escape
        view = service.result_view(db,int(params['series_id']),params['course'],params.get('class_name',''))
        stream = io.BytesIO()
        styles = getSampleStyleSheet()
        title = get(db,'series',int(params['series_id']))['name']+' · '+params.get('class_name',params['course']+' bane')
        story = [Paragraph(escape(title),styles['Title']),Paragraph('Offentliggjorte resultater · laveste point vinder',styles['Normal']),Spacer(1,12)]
        rows = [['Pl.','Båd / skipper','Sejlnr.','Point']]
        for row in view['overall']:
            rows.append([row['rank'],Paragraph(escape(row['name']+' / '+row['skipper']),styles['Normal']),row['sail_number'],f"{row['total']:g}"])
        table = Table(rows,colWidths=[30,295,80,50],repeatRows=1)
        table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#132e41')),('TEXTCOLOR',(0,0),(-1,0),colors.white),('BOTTOMPADDING',(0,0),(-1,-1),8),('VALIGN',(0,0),(-1,-1),'TOP'),('LINEBELOW',(0,0),(-1,-1),.3,colors.lightgrey)]))
        story.append(table)
        selected = params.get('race_id')
        if selected:
            race = next((r for r in view['races'] if r['id']==int(selected)),None)
            if not race:
                raise ValueError('Sejladsen er ikke offentliggjort.')
            story.extend([Spacer(1,20),Paragraph(escape(race['label'])+' · '+race['start'][:10],styles['Heading2'])])
            data = [['Pl.','Båd','Sejltid (s)','Beregnet (s)','Point']]
            for row in view['daily'][race['id']]:
                data.append([row['rank'] or row['status'],Paragraph(escape(row['name']),styles['Normal']),row['elapsed'] if row['elapsed'] is not None else '–',row['corrected'] if row['corrected'] is not None else '–',row['points']])
            story.append(Table(data,repeatRows=1))
        SimpleDocTemplate(stream,title=title).build(story)
        return 200,[('Content-Type','application/pdf'),('Content-Disposition','attachment; filename="Onsdagsbanen.pdf"')],stream.getvalue()


def create_app():
    return Application()
