"""Local personal first administrator, no default identity or printed password."""
import fcntl,getpass,os,sys
from pathlib import Path
def main():
    # Refuse headless invocations before touching the database.
    with open('/dev/tty','w',buffering=1) as terminal:
        if not terminal.isatty():raise RuntimeError()
        username=sys.argv[1] if len(sys.argv)>1 else ''
        if not username.strip() or len(username)>100:raise RuntimeError()
        password=getpass.getpass('Persönliches Passwort (mindestens 12 Zeichen): ',stream=terminal)
        again=getpass.getpass('Wiederholen: ',stream=terminal)
        if len(password)<12 or len(password)>128 or password!=again:raise RuntimeError()
        from app import app
        from extensions import db
        from models import User
        from km_migrate import ready
        from werkzeug.security import generate_password_hash
        from sqlalchemy import text
        with app.app_context():
            directory=Path(app.instance_path)
            with (directory/'.setup.lock').open('a+') as lock:
                os.chmod(lock.name,0o600);fcntl.flock(lock,fcntl.LOCK_EX)
                if not ready():raise RuntimeError()
                db.session.execute(text('BEGIN IMMEDIATE'))
                if User.query.count():raise RuntimeError()
                db.session.add(User(username=username,role='admin',password_hash=generate_password_hash(password)))
                db.session.commit()
        terminal.write('Persönlicher Erstadmin angelegt.\n')
if __name__=='__main__':
    try:main()
    except Exception:raise SystemExit('Lokale Einrichtung fehlgeschlagen; Terminal, Migration und vorhandene Konten prüfen.')
