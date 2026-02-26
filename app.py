import os
import sqlite3
from pathlib import Path
from flask import Flask
from sqlalchemy import event
from sqlalchemy.engine import Engine

from csrf_protection import register_csrf
from db_health_checks import (
    warn_if_elternkontakt_migration_needed,
    warn_if_foerderplan_creator_column_missing,
    warn_if_user_name_columns_missing,
)
from extensions import db, login_manager
from models import User
from routes.admin_routes import register_admin_routes
from routes.auth_routes import register_auth_routes
from routes.erfassung_routes import register_erfassung_routes
from routes.foerderplan_routes import register_foerderplan_routes
from routes.report_routes import register_report_routes
from routes.system_routes import register_system_routes


BASE_DIR = Path(__file__).resolve().parent


@event.listens_for(Engine, "connect")
def _sqlite_connection_pragmas(dbapi_connection, connection_record):  # pragma: no cover
    """
    Harden local SQLite usage a bit for concurrent school use.
    Applies only to sqlite3 connections, not PostgreSQL.
    """
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


def _normalized_database_url(raw_url):
    if not raw_url:
        return None
    url = raw_url.strip()
    # Kompatibilität für ältere URLs/Hosting-Umgebungen
    if url.startswith('postgres://'):
        return 'postgresql://' + url[len('postgres://'):]
    return url


def _default_sqlite_uri():
    # Rückwärtskompatibel: erst lokale Datei im Projektordner, sonst instance/schule.db
    project_db = BASE_DIR / 'schule.db'
    if project_db.exists():
        return f"sqlite:///{project_db}"
    instance_db = BASE_DIR / 'instance' / 'schule.db'
    return f"sqlite:///{instance_db}"


def create_app(config_overrides=None):
    app = Flask(__name__)
    # Falls kein SECRET_KEY gesetzt ist, wird ein temporärer Key erzeugt (Sessions werden nach Neustart ungültig)
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY') or os.urandom(32).hex()
    # Cookies sind domainbasiert (nicht portbasiert). Eigene Namen vermeiden Kollisionen mit anderen Flask-Apps auf localhost.
    app.config['SESSION_COOKIE_NAME'] = os.environ.get('SESSION_COOKIE_NAME', 'kompetenz_manager_session')
    app.config['REMEMBER_COOKIE_NAME'] = os.environ.get('REMEMBER_COOKIE_NAME', 'kompetenz_manager_remember')
    app.config['SQLALCHEMY_DATABASE_URI'] = _normalized_database_url(os.environ.get('DATABASE_URL')) or _default_sqlite_uri()
    app.config['UPLOAD_FOLDER'] = 'static/uploads'

    if config_overrides:
        app.config.update(config_overrides)

    engine_options = {'pool_pre_ping': True}
    if str(app.config.get('SQLALCHEMY_DATABASE_URI', '')).startswith('sqlite:'):
        engine_options['connect_args'] = {'timeout': 5}
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = engine_options

    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'

    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(app.instance_path, exist_ok=True)

    db.init_app(app)
    register_csrf(app)

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    register_auth_routes(app)
    register_admin_routes(app)
    register_erfassung_routes(app)
    register_report_routes(app)
    register_foerderplan_routes(app)
    register_system_routes(app)

    return app


app = create_app()

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        warn_if_elternkontakt_migration_needed()
        warn_if_user_name_columns_missing()
        warn_if_foerderplan_creator_column_missing()
    app.run(
        debug=os.environ.get('FLASK_DEBUG') == '1',
        host=os.environ.get('FLASK_HOST', '127.0.0.1'),
        port=int(os.environ.get('PORT', '5000'))
    )
