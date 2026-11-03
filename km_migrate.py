"""Explicit, locked schema baseline. No legacy data rewrite, seed or account creation."""
import fcntl,hashlib,json,os
from pathlib import Path
from sqlalchemy import inspect,text
from flask import current_app
from extensions import db
REVISION='0001'
def checksum():
    root=Path(__file__).resolve().parent
    return hashlib.sha256(b'km-baseline-0001\0'+(root/'models.py').read_bytes()+(root/'km_runtime.py').read_bytes()).hexdigest()
def schema_matches():
    inspector=inspect(db.engine)
    names=set(inspector.get_table_names())
    expected=set(db.metadata.tables)
    if not {'km_schema','km_runtime_state'}<=names:return False
    if names-{'km_schema','km_runtime_state'}!=expected:return False
    for table in db.metadata.sorted_tables:
        if {row['name'] for row in inspector.get_columns(table.name)}!={c.name for c in table.columns}:return False
    return True
def ready():
    try:
        with db.engine.connect() as connection:
            rows=connection.execute(text('SELECT revision,checksum FROM km_schema')).all()
        return rows==[(REVISION,checksum())] and schema_matches()
    except Exception:
        db.session.rollback()
        return False
def migrate():
    if db.engine.dialect.name not in ('sqlite','postgresql'):raise RuntimeError('Unsupported database')
    directory=Path(current_app.instance_path)
    directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    with (directory/'.schema.lock').open('a+') as lock:
        os.chmod(lock.name,0o600);fcntl.flock(lock,fcntl.LOCK_EX)
        if db.engine.dialect.name=='postgresql':
            with db.engine.connect() as connection:
                connection.execute(text('SELECT pg_advisory_lock(76401983)'))
                try:baseline()
                finally:connection.execute(text('SELECT pg_advisory_unlock(76401983)'))
        else:baseline()
def baseline():
    names=set(inspect(db.engine).get_table_names())
    intent=Path(current_app.instance_path)/'.schema-intent.json'
    marker={'revision':REVISION,'checksum':checksum()}
    recovering=intent.is_file() and json.loads(intent.read_text())==marker
    if 'km_schema' in names and ready():
        if intent.exists():intent.unlink()
        return
    if 'km_schema' in names and not recovering:
        raise RuntimeError('Schema revision or inventory mismatch')
    expected=set(db.metadata.tables)
    business=expected-{'runtime_session'}
    allowed=expected|{'km_schema','km_runtime_state'} if recovering else business
    if names and (not names<=allowed or (not recovering and names!=business)):
        raise RuntimeError('Legacy schema requires reviewed migration')
    inspector=inspect(db.engine)
    for table in db.metadata.sorted_tables:
        if table.name not in names:continue
        if {x['name'] for x in inspector.get_columns(table.name)}!={c.name for c in table.columns}:
            raise RuntimeError('Legacy columns require reviewed migration')
    if not recovering:
        temporary=intent.with_suffix('.tmp')
        with temporary.open('w') as stream:
            os.chmod(temporary,0o600);json.dump(marker,stream);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,intent)
        fd=os.open(intent.parent,os.O_DIRECTORY);os.fsync(fd);os.close(fd)
    db.create_all()
    with db.engine.begin() as connection:
        connection.execute(text('CREATE TABLE IF NOT EXISTS km_runtime_state (name VARCHAR(100) PRIMARY KEY, value VARCHAR(100) NOT NULL)'))
        connection.execute(text('CREATE TABLE IF NOT EXISTS km_schema (revision VARCHAR(20) PRIMARY KEY, checksum VARCHAR(64) NOT NULL)'))
        rows=connection.execute(text('SELECT revision,checksum FROM km_schema')).all()
        if rows and rows!=[(REVISION,checksum())]:raise RuntimeError('Existing revision mismatch')
        if not rows:connection.execute(text('INSERT INTO km_schema (revision,checksum) VALUES (:r,:c)'),{'r':REVISION,'c':checksum()})
    if not ready():raise RuntimeError('Schema verification failed')
    intent.unlink()
def main():
    from app import app
    with app.app_context():migrate()
    print('Schema 0001 verified; accounts and business data preserved.')
if __name__=='__main__':
    try:main()
    except Exception:raise SystemExit('Migration failed; schema/adoption must be reviewed.')
