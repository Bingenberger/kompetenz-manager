"""Bounded, single-process notifications runner; persistent app queue remains authoritative."""
import fcntl,logging,os,signal,time
from pathlib import Path
from datetime import datetime,timezone
from app import app
from km_migrate import ready
from benachrichtigungen_senden import main as send
from extensions import db
logging.getLogger('mail_versand').disabled=True
stopping=False
def stop(*_):
    global stopping
    stopping=True
for signum in (signal.SIGINT,signal.SIGTERM):signal.signal(signum,stop)
def main():
    with app.app_context():
        with (Path(app.instance_path)/'.mail.lock').open('a+') as lock:
            os.chmod(lock.name,0o600)
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise SystemExit('Notification worker already running')
            while not stopping:
                try:
                    if ready():
                        send(['sofort'])
                        today=datetime.now(timezone.utc).date().isoformat()
                        row=db.session.execute(db.text("SELECT value FROM km_runtime_state WHERE name='daily'")).scalar()
                        if row!=today:
                            if send(['taeglich'])==0:
                                db.session.execute(db.text("INSERT INTO km_runtime_state(name,value) VALUES('daily',:v) ON CONFLICT(name) DO UPDATE SET value=:v"),{'v':today})
                                db.session.commit()
                except Exception:
                    db.session.rollback()
                    print('Notification cycle deferred; no private data logged.',flush=True)
                for _ in range(300):
                    if stopping:break
                    time.sleep(1)
if __name__=='__main__':main()
