from datetime import timedelta

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

import re

from benachrichtigungen import ARTEN, MAIL_TAKTE, abbestellte_arten, normalisiere_takt
from csrf_protection import clear_csrf_token, rotate_csrf_token
from extensions import db
from mail_versand import mail_konfiguration
from models import AuthRateLimit, BenachrichtigungAbbestellt, User, UserKlassenzuordnung
from time_utils import utc_now

auth_bp = Blueprint('auth', __name__)

# Bewusst schlicht: ob eine Adresse wirklich existiert, zeigt erst die Zustellung.
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def normalize_email(raw):
    """(Adresse oder None, Fehlertext oder None)."""
    value = (raw or '').strip()
    if not value:
        return None, None
    if len(value) > 255 or not EMAIL_RE.match(value):
        return None, f'„{value}“ ist keine gültige E-Mail-Adresse.'
    return value, None


def _rate_limit_window():
    return timedelta(minutes=max(1, int(current_app.config.get('LOGIN_RATE_LIMIT_WINDOW_MINUTES', 10))))


def _rate_limit_lockout():
    return timedelta(minutes=max(1, int(current_app.config.get('LOGIN_RATE_LIMIT_LOCKOUT_MINUTES', 15))))


def _rate_limit_ip_threshold():
    return max(3, int(current_app.config.get('LOGIN_RATE_LIMIT_IP_ATTEMPTS', 10)))


def _rate_limit_user_threshold():
    return max(3, int(current_app.config.get('LOGIN_RATE_LIMIT_USER_ATTEMPTS', 5)))


def _client_ip():
    return (request.remote_addr or 'unknown').strip().lower()


def _scope_key(scope, raw_value):
    return f'{scope}:{(raw_value or "").strip().lower()}'


def _load_bucket(scope, raw_value):
    bucket = AuthRateLimit.query.filter_by(scope=scope, scope_key=_scope_key(scope, raw_value)).first()
    if not bucket:
        bucket = AuthRateLimit(scope=scope, scope_key=_scope_key(scope, raw_value))
        db.session.add(bucket)
        db.session.flush()
    return bucket


def _normalize_bucket_window(bucket, now, window):
    if bucket.last_failed_at and bucket.last_failed_at < (now - window):
        bucket.failure_count = 0
        bucket.first_failed_at = None
        bucket.last_failed_at = None
        bucket.locked_until = None


def _active_lock(username):
    now = utc_now()
    window = _rate_limit_window()
    username = (username or '').strip()
    active_lock = None

    for scope, raw_value in (('ip', _client_ip()), ('username', username)):
        if not raw_value:
            continue
        bucket = AuthRateLimit.query.filter_by(scope=scope, scope_key=_scope_key(scope, raw_value)).first()
        if not bucket:
            continue
        _normalize_bucket_window(bucket, now, window)
        if bucket.locked_until and bucket.locked_until > now:
            if not active_lock or bucket.locked_until > active_lock:
                active_lock = bucket.locked_until

    db.session.commit()
    return active_lock


def _record_failed_login(username):
    now = utc_now()
    window = _rate_limit_window()
    lockout = _rate_limit_lockout()
    username = (username or '').strip()

    for scope, raw_value, threshold in (
        ('ip', _client_ip(), _rate_limit_ip_threshold()),
        ('username', username, _rate_limit_user_threshold()),
    ):
        if not raw_value:
            continue
        bucket = _load_bucket(scope, raw_value)
        _normalize_bucket_window(bucket, now, window)
        if bucket.failure_count <= 0:
            bucket.first_failed_at = now
        bucket.failure_count = (bucket.failure_count or 0) + 1
        bucket.last_failed_at = now
        if bucket.failure_count >= threshold:
            bucket.locked_until = now + lockout

    db.session.commit()


def _clear_login_failures(username):
    username = (username or '').strip()
    for scope, raw_value in (('ip', _client_ip()), ('username', username)):
        if not raw_value:
            continue
        bucket = AuthRateLimit.query.filter_by(scope=scope, scope_key=_scope_key(scope, raw_value)).first()
        if bucket:
            db.session.delete(bucket)
    db.session.commit()


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''

        locked_until = _active_lock(username)
        if locked_until:
            flash('Zu viele Anmeldeversuche. Bitte später erneut versuchen.')
            return render_template('login.html'), 429

        user = User.query.filter_by(username=username).first()

        if user and check_password_hash(user.password_hash, password):
            _clear_login_failures(username)
            login_user(user)
            rotate_csrf_token()
            return redirect(url_for('system.index'))

        _record_failed_login(username)
        flash('Benutzername oder Passwort falsch!')

    return render_template('login.html')


@auth_bp.route('/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    clear_csrf_token()
    flash('Erfolgreich ausgeloggt.')
    return redirect(url_for('auth.login'))


@auth_bp.route('/change_password', methods=['GET', 'POST'])
@login_required
def change_password():
    # Rueckwaertskompatibel: leitet auf das neue Usermenue weiter.
    return redirect(url_for('auth.user_menu'))


@auth_bp.route('/konto', methods=['GET', 'POST'])
@login_required
def user_menu():
    if request.method == 'POST':
        form_action = request.form.get('form_action')

        if form_action == 'profile':
            current_user.vorname = (request.form.get('vorname') or '').strip() or None
            current_user.nachname = (request.form.get('nachname') or '').strip() or None
            db.session.commit()
            flash('Profil gespeichert.')
            return redirect(url_for('auth.user_menu'))

        if form_action == 'benachrichtigungen':
            email, fehler = normalize_email(request.form.get('email'))
            if fehler:
                flash(fehler)
                return redirect(url_for('auth.user_menu') + '#benachrichtigungen')
            current_user.email = email
            current_user.mail_takt = normalisiere_takt(request.form.get('mail_takt'))
            gewuenscht = set(request.form.getlist('arten'))
            BenachrichtigungAbbestellt.query.filter_by(user_id=current_user.id).delete()
            for art in ARTEN:
                if art not in gewuenscht:
                    db.session.add(BenachrichtigungAbbestellt(user_id=current_user.id, art=art))
            db.session.commit()
            flash('Benachrichtigungen gespeichert.')
            return redirect(url_for('auth.user_menu') + '#benachrichtigungen')

        if form_action == 'password':
            altes_pw = request.form.get('altes_pw')
            neues_pw = request.form.get('neues_pw')
            neues_pw_wdh = request.form.get('neues_pw_wdh')

            if not check_password_hash(current_user.password_hash, altes_pw):
                flash('Das alte Passwort ist falsch!')
                return redirect(url_for('auth.user_menu'))

            if neues_pw != neues_pw_wdh:
                flash('Die neuen Passwörter stimmen nicht überein!')
                return redirect(url_for('auth.user_menu'))

            current_user.password_hash = generate_password_hash(neues_pw)
            db.session.commit()
            flash('Passwort erfolgreich geändert!')
            return redirect(url_for('auth.user_menu'))

    zuordnungen = UserKlassenzuordnung.query.filter_by(user_id=current_user.id).all()
    klassenleitung = next((z.klasse for z in zuordnungen if z.rolle == 'klassenleitung'), None)
    fachklassen = sorted([z.klasse for z in zuordnungen if z.rolle == 'fach'], key=lambda x: x.lower())

    return render_template(
        'user_menu.html',
        klassenleitung=klassenleitung,
        fachklassen=fachklassen,
        arten=ARTEN,
        abbestellt=abbestellte_arten(current_user.id),
        mail_takte=MAIL_TAKTE,
        mail_aktiv=mail_konfiguration() is not None,
    )


def register_auth_routes(app):
    app.config.setdefault('LOGIN_RATE_LIMIT_WINDOW_MINUTES', int(app.config.get('LOGIN_RATE_LIMIT_WINDOW_MINUTES', 10)))
    app.config.setdefault('LOGIN_RATE_LIMIT_LOCKOUT_MINUTES', int(app.config.get('LOGIN_RATE_LIMIT_LOCKOUT_MINUTES', 15)))
    app.config.setdefault('LOGIN_RATE_LIMIT_IP_ATTEMPTS', int(app.config.get('LOGIN_RATE_LIMIT_IP_ATTEMPTS', 10)))
    app.config.setdefault('LOGIN_RATE_LIMIT_USER_ATTEMPTS', int(app.config.get('LOGIN_RATE_LIMIT_USER_ATTEMPTS', 5)))
    app.register_blueprint(auth_bp)
