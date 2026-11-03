"""Private runtime configuration, readiness and revocable local sessions."""
import hashlib,ipaddress,os,secrets,time
from datetime import timedelta
from pathlib import Path
from flask import abort,current_app,request,session,g
from sqlalchemy import text
from werkzeug.middleware.proxy_fix import ProxyFix
from extensions import db
from models import User
from time_utils import utc_now

class RuntimeSession(db.Model):
    id_hash=db.Column(db.String(64),primary_key=True)
    user_id=db.Column(db.Integer,db.ForeignKey('user.id',ondelete='CASCADE'),nullable=False,index=True)
    version=db.Column(db.String(64),nullable=False)
    expires_at=db.Column(db.DateTime,nullable=False,index=True)

def private_value(name):
    value=os.environ.get(name)
    path=os.environ.get(name+'_FILE')
    if value is not None and path:raise RuntimeError('Credential has two sources: '+name)
    return Path(path).read_text().strip() if path else value

def version(user):
    return hashlib.sha256((str(user.id)+'\0'+str(user.role)+'\0'+str(user.password_hash)).encode()).hexdigest()

def issue(user,expected_version):
    if current_app.testing:return
    if db.engine.dialect.name=='sqlite':db.session.execute(text('BEGIN IMMEDIATE'))
    else:db.session.execute(db.select(User).where(User.id==user.id).with_for_update()).scalar_one()
    db.session.refresh(user)
    if version(user)!=expected_version:abort(401)
    RuntimeSession.query.filter(RuntimeSession.expires_at<=utc_now()).delete()
    if RuntimeSession.query.filter_by(user_id=user.id).count()>=20:abort(429)
    token=secrets.token_urlsafe(32)
    db.session.add(RuntimeSession(id_hash=hashlib.sha256(token.encode()).hexdigest(),user_id=user.id,
        version=version(user),expires_at=utc_now()+timedelta(hours=8)))
    db.session.commit()
    session['_km_sid']=token;session.permanent=True

def valid(user):
    if current_app.testing:return True
    token=session.get('_km_sid')
    if not isinstance(token,str) or len(token)>100:return False
    row=db.session.get(RuntimeSession,hashlib.sha256(token.encode()).hexdigest())
    return bool(row and row.user_id==user.id and row.version==version(user) and row.expires_at>utc_now())

def revoke():
    token=session.pop('_km_sid',None)
    if isinstance(token,str):
        RuntimeSession.query.filter_by(id_hash=hashlib.sha256(token.encode()).hexdigest()).delete()
        db.session.commit()

class TrustedProxy:
    def __init__(self,app,addresses):
        self.app=app;self.addresses={str(ipaddress.ip_address(value)) for value in addresses}
        self.forwarded=ProxyFix(app,x_for=1,x_proto=1,x_host=1,x_port=0,x_prefix=0)
    def __call__(self,environ,start_response):
        if environ.get('REMOTE_ADDR') in self.addresses:return self.forwarded(environ,start_response)
        clean=dict(environ)
        for key in tuple(clean):
            if key.startswith('HTTP_X_FORWARDED_') or key in ('HTTP_FORWARDED','HTTP_X_REMOTE_USER','HTTP_REMOTE_USER'):
                clean.pop(key,None)
        return self.app(clean,start_response)

def install(app):
    app.config['PERMANENT_SESSION_LIFETIME']=timedelta(hours=8)
    if os.environ.get('TRUST_REVERSE_PROXY')=='1' and not os.environ.get('TRUSTED_PROXY_IPS'):
        raise RuntimeError('Explicit TRUSTED_PROXY_IPS required')
    addresses=[x.strip() for x in os.environ.get('TRUSTED_PROXY_IPS','').split(',') if x.strip()]
    app.wsgi_app=TrustedProxy(app.wsgi_app,addresses)
    @app.before_request
    def gate():
        if app.testing:return
        if request.path.startswith('/static/uploads/') or request.path=='/setup':abort(403)
        if request.path in ('/health/live','/health/ready'):return
        from km_migrate import ready
        if not ready():abort(503)
        if request.method not in ('GET','HEAD','OPTIONS') and request.headers.get('Origin'):
            if request.headers['Origin']!=app.config.get('APP_BASE_URL','').rstrip('/'):abort(403)
        if 'pdf' in request.path.lower() or request.args.get('format')=='pdf':
            from km_export import export_slot
            manager=export_slot();manager.__enter__();g.km_export=manager
    @app.teardown_request
    def release(_error):
        manager=g.pop('km_export',None)
        if manager:manager.__exit__(None,None,None)
    @app.get('/health/live')
    def live():return {'status':'ok'}
    @app.get('/health/ready')
    def health():
        from km_migrate import ready
        healthy=ready()
        return {'status':'ok' if healthy else 'not-ready'},200 if healthy else 503
    @app.errorhandler(Exception)
    def failed(error):
        from werkzeug.exceptions import HTTPException
        if isinstance(error,HTTPException):return error
        db.session.rollback()
        return 'Dienst derzeit nicht verfügbar.',503
    @app.errorhandler(503)
    def unavailable(_error):return 'Dienst derzeit nicht bereit.',503
