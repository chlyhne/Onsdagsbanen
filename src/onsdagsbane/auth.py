"""Server-side authorization-code login; no passwords or client-controlled roles."""
import base64
import hashlib
import hmac
from http.cookies import SimpleCookie
import json
import os
import secrets
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .store import audit

OWNER = 'hummesse@gmail.com'


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def cookie_value(environ, name):
    cookies = SimpleCookie()
    try:
        cookies.load(environ.get('HTTP_COOKIE',''))
        return cookies[name].value if name in cookies else ''
    except Exception:
        return ''


def cookie(name,value,base_url,max_age):
    return ('Set-Cookie',f'{name}={value}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}' + ('; Secure' if base_url.startswith('https://') else ''))


def current_user(db,environ):
    token = cookie_value(environ,'ob_session')
    if not token:
        return None
    row = db.execute('SELECT u.*,s.csrf FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires>?',(digest(token),time.time())).fetchone()
    return dict(row) if row else None


def can_approve(user):
    return bool(user and user['provider']=='google' and user['email'].casefold()==OWNER and user['approved'] and user['role']=='admin')


def authorize(user, admin=False):
    if not user or not user['approved'] or (admin and user['role']!='admin'):
        raise PermissionError('Du skal være godkendt administrator.' if admin else 'Du skal logge ind og være godkendt for at indtaste resultater.')


def provider_config(provider):
    if provider not in ('google','facebook'):
        raise ValueError('Ukendt loginudbyder.')
    prefix = provider.upper()
    client_id, secret = os.getenv(prefix+'_CLIENT_ID'),os.getenv(prefix+'_CLIENT_SECRET')
    if not client_id or not secret:
        raise ValueError(f'{provider.title()}-login er ikke konfigureret endnu. Administrator skal tilføje appnøgler på serveren.')
    return client_id,secret


def login_start(db,provider,base_url):
    client_id,_ = provider_config(provider)
    state,binding,verifier = secrets.token_urlsafe(32),secrets.token_urlsafe(32),secrets.token_urlsafe(48)
    db.execute('DELETE FROM oauth WHERE expires<?',(time.time(),))
    db.execute('INSERT INTO oauth VALUES(?,?,?,?,?)',(digest(state),provider,digest(binding),verifier,time.time()+600))
    params = {'client_id':client_id,'redirect_uri':f'{base_url}/auth/{provider}/callback','response_type':'code','state':state}
    if provider=='google':
        params.update(scope='openid email profile',code_challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('='),code_challenge_method='S256')
        url = 'https://accounts.google.com/o/oauth2/v2/auth'
    else:
        params.update(scope='email,public_profile')
        url = 'https://www.facebook.com/dialog/oauth'
    return url+'?'+urlencode(params), cookie('ob_oauth',binding,base_url,600)


def remote_json(url,data=None,headers=None):
    request = Request(url,data=urlencode(data).encode() if data else None,headers=headers or {})
    with urlopen(request,timeout=20) as response:
        return json.loads(response.read())


def login_callback(db,provider,params,environ,base_url):
    client_id, secret = provider_config(provider)
    state = params.get('state','')
    pending = db.execute('SELECT * FROM oauth WHERE state=?',(digest(state),)).fetchone()
    if not pending or pending['provider']!=provider or pending['expires']<time.time() or not hmac.compare_digest(pending['binding'],digest(cookie_value(environ,'ob_oauth'))):
        raise ValueError('Loginforsøget er udløbet eller ugyldigt. Start forfra.')
    db.execute('DELETE FROM oauth WHERE state=?',(digest(state),))
    if params.get('error') or not params.get('code'):
        raise ValueError('Login blev afbrudt. Prøv igen.')
    token_data = {'client_id':client_id,'client_secret':secret,'code':params['code'],'redirect_uri':f'{base_url}/auth/{provider}/callback'}
    if provider=='google':
        token_data.update(grant_type='authorization_code',code_verifier=pending['verifier'])
        token = remote_json('https://oauth2.googleapis.com/token',token_data)['access_token']
        info = remote_json('https://openidconnect.googleapis.com/v1/userinfo',headers={'Authorization':'Bearer '+token})
        if info.get('email_verified') is not True:
            raise ValueError('Google-kontoens mailadresse skal være bekræftet.')
        subject = info['sub']
    else:
        version = os.getenv('FACEBOOK_GRAPH_VERSION','v25.0')
        if not __import__('re').fullmatch(r'v\d+\.\d+',version):
            raise ValueError('Facebook API-versionen er ugyldig.')
        token = remote_json(f'https://graph.facebook.com/{version}/oauth/access_token',token_data)['access_token']
        proof = hmac.new(secret.encode(),token.encode(),hashlib.sha256).hexdigest()
        info = remote_json(f'https://graph.facebook.com/{version}/me?'+urlencode({'fields':'id,name,email','appsecret_proof':proof}),headers={'Authorization':'Bearer '+token})
        subject = info['id']
    email = str(info.get('email','')).strip().lower()
    if not email:
        raise ValueError('Loginudbyderen skal dele din mailadresse, så kontoen kan godkendes.')
    owner = provider=='google' and email==OWNER
    db.execute('INSERT INTO users(provider,subject,email,name,role,approved) VALUES(?,?,?,?,?,?) ON CONFLICT(provider,subject) DO UPDATE SET email=excluded.email,name=excluded.name',(provider,subject,email,info.get('name') or email,'admin' if owner else 'recorder',int(owner)))
    user = dict(db.execute('SELECT * FROM users WHERE provider=? AND subject=?',(provider,subject)).fetchone())
    token,csrf = secrets.token_urlsafe(40),secrets.token_urlsafe(32)
    old = cookie_value(environ,'ob_session')
    db.execute('DELETE FROM sessions WHERE token=? OR expires<?',(digest(old),time.time()))
    db.execute('INSERT INTO sessions VALUES(?,?,?,?)',(digest(token),user['id'],csrf,time.time()+43200))
    audit(db,user,'login',{'provider':provider})
    return [cookie('ob_session',token,base_url,43200),cookie('ob_oauth','',base_url,0)]
