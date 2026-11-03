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
from .compatibility import HandicapError

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
        except HandicapError as exc:
            status,headers,body = self.json({'error':str(exc),'handicap_issues':exc.issues},400)
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
        page_route=bool(re.fullmatch(r'/(?:results|today|series(?:/\d+(?:/(?:assignments|boats|results|settings))?)?|rounds/[\w-]+(?:/(?:setup|starts|timing|results|history))?|boats|admin)?',path))
        if method=='GET' and (page_route or path.endswith(('.js','.css'))):
            filename = (STATIC / ('index.html' if page_route else path.lstrip('/'))).resolve()
            if not filename.is_relative_to(STATIC.resolve()) or not filename.is_file():
                return self.json({'error':'Filen findes ikke.'},404)
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
            from .api import dispatch
            return dispatch(self,db,user,can_approve,path,method,params,data,env)

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
