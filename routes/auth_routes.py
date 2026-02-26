from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from csrf_protection import clear_csrf_token, rotate_csrf_token
from extensions import db
from models import User, UserKlassenzuordnung

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        user = User.query.filter_by(username=username).first()

        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            rotate_csrf_token()
            return redirect(url_for('system.index'))

        flash('Benutzername oder Passwort falsch!')

    return render_template('login.html')

@auth_bp.route('/logout')
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
    )


def register_auth_routes(app):
    app.register_blueprint(auth_bp)
