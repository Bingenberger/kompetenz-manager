"""Runtime contracts using fresh processes and isolated SQLite stores."""
import json,os,subprocess,sys
from pathlib import Path
import pytest
from werkzeug.exceptions import TooManyRequests

def environment(tmp_path):
    directory=tmp_path/'instance';directory.mkdir()
    return os.environ|{'DATABASE_URL':'sqlite:///'+str(directory/'schule.db'),'KM_INSTANCE_PATH':str(directory)}
def execute(env,program):
    return subprocess.run([sys.executable,'-c',program],env=env,capture_output=True,text=True,timeout=45)
def test_migration_resumes_after_actual_process_exit_and_preserves_ids(tmp_path):
    env=environment(tmp_path)
    program="""from app import app
from extensions import db
from km_migrate import migrate
import os
original=db.create_all
def interrupted():
    original()
    os._exit(17)
db.create_all=interrupted
with app.app_context():migrate()
"""
    assert execute(env,program).returncode==17
    result=execute(env,"from app import app\nfrom km_migrate import migrate,ready\nwith app.app_context():migrate();assert ready()")
    assert result.returncode==0,result.stderr
    program="""from app import app
from extensions import db
from models import User,Schueler,UserKlassenzuordnung
from km_migrate import migrate
from sqlalchemy import text
with app.app_context():
 db.session.add(User(id=42,username='existing-personal',password_hash='preserved-hash',role='teacher'))
 db.session.add(Schueler(id=57,vorname='Synthetic',nachname='Child',klasse='3a'))
 db.session.add(UserKlassenzuordnung(id=29,user_id=42,klasse='3a',rolle='fach'))
 db.session.commit()
 migrate()
 assert db.session.get(User,42).password_hash=='preserved-hash'
 assert db.session.get(Schueler,57).klasse=='3a'
 assert db.session.get(UserKlassenzuordnung,29).user_id==42
"""
    result=execute(env,program);assert result.returncode==0,result.stderr

def test_complete_legacy_schema_adoption_does_not_rewrite_business_data(tmp_path):
    env=environment(tmp_path)
    program="""from app import app
from extensions import db
from models import User,Schueler
from km_migrate import migrate,ready
from sqlalchemy import text
with app.app_context():
 db.create_all()
 db.session.execute(text('DROP TABLE runtime_session'))
 db.session.add(User(id=84,username='legacy-personal',password_hash='unchanged',role='schulleitung'))
 db.session.add(Schueler(id=85,vorname='Synthetic',nachname='Legacy',klasse=' 3a '))
 db.session.commit()
 migrate()
 assert ready()
 assert db.session.get(User,84).role=='schulleitung'
 assert db.session.get(Schueler,85).klasse==' 3a '
"""
    result=execute(env,program);assert result.returncode==0,result.stderr

def test_unknown_legacy_schema_refused_without_rewriting(tmp_path):
    env=environment(tmp_path)
    program="""import sqlite3,os
p=os.environ['DATABASE_URL'].replace('sqlite:///','')
with sqlite3.connect(p) as c:c.execute('CREATE TABLE unfamiliar (id INTEGER PRIMARY KEY)');c.execute('INSERT INTO unfamiliar VALUES (73)')
from app import app
from km_migrate import migrate
with app.app_context():
 try:migrate()
 except RuntimeError:pass
 else:raise AssertionError('Unknown schema accepted')
with sqlite3.connect(p) as c:
 assert c.execute('SELECT * FROM unfamiliar').fetchall()==[(73,)]
 assert c.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]==1
"""
    result=execute(env,program);assert result.returncode==0,result.stderr

def test_slot_is_shared_across_separate_processes():
    from km_export import export_slot
    with export_slot():
        result=subprocess.run([sys.executable,'-c',"""from km_export import export_slot
from werkzeug.exceptions import TooManyRequests
try:
 with export_slot():raise AssertionError('Second export accepted')
except TooManyRequests:pass
"""],capture_output=True,text=True,timeout=10)
        assert result.returncode==0,result.stderr
    with export_slot():pass


def test_password_reset_between_check_and_session_creation_denies_login(tmp_path):
    env=environment(tmp_path)
    program="""from app import app
from extensions import db
from models import User
from km_migrate import migrate
from km_runtime import RuntimeSession
from werkzeug.security import generate_password_hash
import re
import routes.auth_routes as auth
with app.app_context():
 migrate()
 db.session.add(User(username='race-personal',password_hash=generate_password_hash('old-password-123'),role='teacher'))
 db.session.commit()
original=auth._clear_login_failures
def reset(username):
 original(username)
 user=User.query.filter_by(username=username).one()
 user.password_hash=generate_password_hash('new-password-456')
 db.session.commit()
auth._clear_login_failures=reset
client=app.test_client()
html=client.get('/login').data.decode()
token=re.search(r'name="_csrf_token"\s+value="([^"]+)"',html)[1]
response=client.post('/login',data={'username':'race-personal','password':'old-password-123','_csrf_token':token})
assert response.status_code==401
with app.app_context():assert RuntimeSession.query.count()==0
"""
    result=execute(env,program);assert result.returncode==0,result.stderr
