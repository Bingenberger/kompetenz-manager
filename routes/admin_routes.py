from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required
from werkzeug.security import generate_password_hash

from authz import admin_required
from db_utils import get_or_404_session
from extensions import db
from models import (
    Beobachtung,
    Bogen,
    ErziehungsEreignisKategorie,
    ErziehungsEreignisVorlage,
    ErziehungsKonsequenz,
    ErziehungsOrt,
    Item,
    Schueler,
    SystemKonfiguration,
    User,
    UserKlassenzuordnung,
)
from student_selection import get_distinct_klassen


admin_bp = Blueprint('admin', __name__)


def _safe_next_url(candidate, fallback_url):
    value = (candidate or '').strip()
    if value.startswith('/') and not value.startswith('//'):
        return value
    return fallback_url


def _parse_optional_date(value):
    value = (value or '').strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except ValueError:
        return None


def _get_system_konfiguration():
    eintrag = SystemKonfiguration.query.first()
    if not eintrag:
        eintrag = SystemKonfiguration()
        db.session.add(eintrag)
        db.session.commit()
    return eintrag


@admin_bp.route('/admin')
@admin_required(redirect_endpoint='system.index', message='Zugriff verweigert. Nur der Administrator darf den Admin-Bereich öffnen.')
def admin_dashboard():
    return render_template('admin_dashboard.html', settings=_get_system_konfiguration())


def _erziehung_pool_config(kind):
    mapping = {
        'kategorien': {
            'model': ErziehungsEreignisKategorie,
            'title': 'Ereigniskategorien',
            'name_label': 'Kategoriename',
            'has_category': False,
        },
        'ereignisse': {
            'model': ErziehungsEreignisVorlage,
            'title': 'Ereignispool',
            'name_label': 'Ereignis',
            'has_category': True,
        },
        'konsequenzen': {
            'model': ErziehungsKonsequenz,
            'title': 'Konsequenzen',
            'name_label': 'Konsequenz',
            'has_category': False,
        },
        'orte': {
            'model': ErziehungsOrt,
            'title': 'Orte',
            'name_label': 'Ort',
            'has_category': False,
        },
    }
    return mapping.get(kind)


@admin_bp.route('/admin/erziehung')
@admin_required(redirect_endpoint='system.index', message=None)
def admin_erziehung_dashboard():
    return render_template('admin_erziehung_dashboard.html')


@admin_bp.route('/admin/erziehung/<string:kind>', methods=['GET', 'POST'])
@admin_required(redirect_endpoint='admin.admin_erziehung_dashboard', message=None)
def admin_erziehung_pool(kind):
    cfg = _erziehung_pool_config(kind)
    if not cfg:
        return redirect(url_for('admin.admin_erziehung_dashboard'))

    model = cfg['model']
    entry_id = (request.args.get('edit') or request.form.get('entry_id') or '').strip()
    entry = db.session.get(model, int(entry_id)) if entry_id.isdigit() else None

    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        sort_order = int((request.form.get('sort_order') or '0').strip() or 0)
        is_active = request.form.get('is_active') == '1'
        category = None

        if cfg['has_category']:
            category_raw = (request.form.get('category_id') or '').strip()
            category = db.session.get(ErziehungsEreignisKategorie, int(category_raw)) if category_raw.isdigit() else None

        if not name:
            flash('Bitte einen Namen eingeben.')
        elif cfg['has_category'] and not category:
            flash('Bitte eine Kategorie auswählen.')
        else:
            is_new = not entry
            if not entry:
                entry = model()
            entry.name = name
            entry.sort_order = sort_order
            entry.is_active = is_active

            if cfg['has_category']:
                entry.category_id = category.id

            if is_new:
                db.session.add(entry)
            db.session.commit()
            flash('Eintrag gespeichert.')
            return redirect(url_for('admin.admin_erziehung_pool', kind=kind))

    categories = ErziehungsEreignisKategorie.query.order_by(ErziehungsEreignisKategorie.sort_order.asc(), ErziehungsEreignisKategorie.name.asc()).all()
    entries_query = model.query
    if cfg['has_category']:
        entries_query = entries_query.join(ErziehungsEreignisKategorie).order_by(
            ErziehungsEreignisKategorie.sort_order.asc(),
            ErziehungsEreignisKategorie.name.asc(),
            model.sort_order.asc(),
            model.name.asc(),
        )
    else:
        entries_query = entries_query.order_by(model.sort_order.asc(), model.name.asc())
    entries = entries_query.all()
    return render_template('admin_erziehung_pool.html', cfg=cfg, entries=entries, entry=entry, categories=categories, kind=kind)


@admin_bp.route('/admin/erziehung/<string:kind>/delete/<int:entry_id>', methods=['POST'])
@admin_required(redirect_endpoint='admin.admin_erziehung_dashboard', message=None)
def admin_erziehung_pool_delete(kind, entry_id):
    cfg = _erziehung_pool_config(kind)
    if not cfg:
        return redirect(url_for('admin.admin_erziehung_dashboard'))

    entry = db.session.get(cfg['model'], entry_id)
    if not entry:
        flash('Eintrag nicht gefunden.')
        return redirect(url_for('admin.admin_erziehung_pool', kind=kind))

    db.session.delete(entry)
    db.session.commit()
    flash('Eintrag gelöscht.')
    return redirect(url_for('admin.admin_erziehung_pool', kind=kind))


@admin_bp.route('/admin/system-settings', methods=['GET', 'POST'])
@admin_required(redirect_endpoint='admin.admin_dashboard', message=None)
def admin_system_settings():
    next_url = (request.args.get('next') or request.form.get('next') or '').strip()
    settings = _get_system_konfiguration()

    if request.method == 'POST':
        schuljahr = (request.form.get('schuljahr') or '').strip() or None
        esp1_raw = request.form.get('elternsprechtag_1')
        esp2_raw = request.form.get('elternsprechtag_2')

        esp1 = _parse_optional_date(esp1_raw) if (esp1_raw or '').strip() else None
        esp2 = _parse_optional_date(esp2_raw) if (esp2_raw or '').strip() else None

        if (esp1_raw or '').strip() and esp1 is None:
            flash('Beginn Elternsprechtag 1 ist kein gültiges Datum.')
            return render_template('admin_system_settings.html', settings=settings, next_url=next_url)
        if (esp2_raw or '').strip() and esp2 is None:
            flash('Beginn Elternsprechtag 2 ist kein gültiges Datum.')
            return render_template('admin_system_settings.html', settings=settings, next_url=next_url)

        settings.schuljahr = schuljahr
        settings.elternsprechtag_1 = esp1
        settings.elternsprechtag_2 = esp2
        db.session.commit()
        flash('Grundeinstellungen gespeichert.')
        return redirect(_safe_next_url(next_url, url_for('admin.admin_system_settings')))

    return render_template('admin_system_settings.html', settings=settings, next_url=next_url)


@admin_bp.route('/admin/users', methods=['GET', 'POST'])
@admin_required(
    redirect_endpoint='admin.admin_dashboard',
    message='Zugriff verweigert. Nur der Administrator darf Benutzer verwalten.'
)
def admin_users():
    if request.method == 'POST':
        username = request.form.get('username')
        vorname = (request.form.get('vorname') or '').strip() or None
        nachname = (request.form.get('nachname') or '').strip() or None
        password = request.form.get('password')

        if User.query.filter_by(username=username).first():
            flash('Benutzername existiert bereits!')
        else:
            hashed_pw = generate_password_hash(password)
            new_user = User(username=username, vorname=vorname, nachname=nachname, password_hash=hashed_pw)
            db.session.add(new_user)
            db.session.commit()
            flash(f'Benutzer {username} angelegt.')
            return redirect(url_for('admin.admin_users'))

    users = User.query.order_by(User.username).all()
    zuordnungen = UserKlassenzuordnung.query.all()
    zuordnungen_by_user = {}
    for z in zuordnungen:
        eintrag = zuordnungen_by_user.setdefault(z.user_id, {"klassenleitung": None, "fachklassen": []})
        if z.rolle == 'klassenleitung':
            eintrag["klassenleitung"] = z.klasse
        elif z.rolle == 'fach':
            eintrag["fachklassen"].append(z.klasse)
    for data in zuordnungen_by_user.values():
        data["fachklassen"] = sorted(set(data["fachklassen"]), key=lambda x: x.lower())

    return render_template('admin_users.html', users=users, zuordnungen_by_user=zuordnungen_by_user)


@admin_bp.route('/admin/users/delete/<int:user_id>', methods=['POST'])
@admin_required(redirect_endpoint='system.index', message=None)
def admin_user_delete(user_id):
    user_to_delete = get_or_404_session(User, user_id)

    if user_to_delete.username == 'admin':
        flash('Der Haupt-Administrator kann nicht gelöscht werden.')
    else:
        UserKlassenzuordnung.query.filter_by(user_id=user_to_delete.id).delete()
        db.session.delete(user_to_delete)
        db.session.commit()
        flash(f'Benutzer {user_to_delete.username} gelöscht.')

    return redirect(url_for('admin.admin_users'))


@admin_bp.route('/admin/users/edit/<int:user_id>', methods=['GET', 'POST'])
@admin_required(redirect_endpoint='admin.admin_dashboard', message=None)
def admin_user_edit(user_id):
    user = get_or_404_session(User, user_id)
    next_url = (request.args.get('next') or request.form.get('next') or '').strip()

    if request.method == 'POST':
        user.vorname = (request.form.get('vorname') or '').strip() or None
        user.nachname = (request.form.get('nachname') or '').strip() or None
        db.session.commit()
        flash(f'Klarname für {user.username} gespeichert.')
        return redirect(_safe_next_url(next_url, url_for('admin.admin_users')))

    return render_template('admin_user_edit.html', user=user, next_url=next_url)


@admin_bp.route('/admin/users/assignments/<int:user_id>', methods=['GET', 'POST'])
@admin_required(redirect_endpoint='admin.admin_dashboard', message=None)
def admin_user_assignments(user_id):
    user = get_or_404_session(User, user_id)
    klassen = get_distinct_klassen()
    next_url = (request.args.get('next') or request.form.get('next') or '').strip()

    if request.method == 'POST':
        klassenleitung_klasse = (request.form.get('klassenleitung_klasse') or '').strip()
        fachklassen = [k.strip() for k in request.form.getlist('fachklassen') if k.strip()]

        # Doppelte/Überschneidung bereinigen
        fachklassen = sorted(set(fachklassen), key=lambda x: x.lower())
        if klassenleitung_klasse and klassenleitung_klasse in fachklassen:
            fachklassen.remove(klassenleitung_klasse)

        if klassenleitung_klasse:
            conflict = (
                UserKlassenzuordnung.query
                .filter(
                    UserKlassenzuordnung.rolle == 'klassenleitung',
                    UserKlassenzuordnung.klasse == klassenleitung_klasse,
                    UserKlassenzuordnung.user_id != user.id,
                )
                .first()
            )
            if conflict:
                conflict_user = db.session.get(User, conflict.user_id)
                flash(
                    f'Klasse {klassenleitung_klasse} ist bereits als Klassenleitung '
                    f'{conflict_user.username if conflict_user else "einer anderen Lehrkraft"} zugeordnet.'
                )
                current_zuordnungen = UserKlassenzuordnung.query.filter_by(user_id=user.id).all()
                return render_template(
                    'admin_user_assignments.html',
                    user=user,
                    klassen=klassen,
                    current_zuordnungen=current_zuordnungen,
                    next_url=next_url,
                )

        UserKlassenzuordnung.query.filter_by(user_id=user.id).delete()
        if klassenleitung_klasse:
            db.session.add(UserKlassenzuordnung(user_id=user.id, klasse=klassenleitung_klasse, rolle='klassenleitung'))
        for klasse in fachklassen:
            db.session.add(UserKlassenzuordnung(user_id=user.id, klasse=klasse, rolle='fach'))
        db.session.commit()
        flash(f'Klassenzuordnungen für {user.username} gespeichert.')
        return redirect(_safe_next_url(next_url, url_for('admin.admin_users')))

    current_zuordnungen = UserKlassenzuordnung.query.filter_by(user_id=user.id).all()
    return render_template(
        'admin_user_assignments.html',
        user=user,
        klassen=klassen,
        current_zuordnungen=current_zuordnungen,
        next_url=next_url,
    )


@admin_bp.route('/admin/students')
@admin_required(redirect_endpoint='system.index', message='Zugriff verweigert. Nur der Administrator darf Schülergrunddaten verwalten.')
def admin_students():
    schueler = Schueler.query.order_by(Schueler.klasse, Schueler.nachname).all()
    return render_template('admin_students.html', schueler=schueler)


@admin_bp.route('/admin/student/edit/<int:s_id>', methods=['GET', 'POST'])
@admin_required(
    redirect_endpoint='admin.admin_students',
    message='Zugriff verweigert. Nur der Administrator darf Schülergrunddaten bearbeiten.'
)
def admin_student_edit(s_id):
    schueler = get_or_404_session(Schueler, s_id)
    next_url = (request.args.get('next') or request.form.get('next') or '').strip()

    if request.method == 'POST':
        schueler.vorname = request.form.get('vorname')
        schueler.nachname = request.form.get('nachname')
        schueler.klasse = request.form.get('klasse')
        geburtsdatum_raw = request.form.get('geburtsdatum')
        if (geburtsdatum_raw or '').strip() and _parse_optional_date(geburtsdatum_raw) is None:
            flash('Geburtsdatum hat kein gültiges Format.')
            return render_template('admin_student_edit.html', s=schueler, next_url=next_url)
        schueler.geburtsdatum = _parse_optional_date(geburtsdatum_raw)
        db.session.commit()
        flash('Schülerdaten aktualisiert.')
        return redirect(_safe_next_url(next_url, url_for('admin.admin_students')))

    return render_template('admin_student_edit.html', s=schueler, next_url=next_url)


@admin_bp.route('/admin/student/delete/<int:s_id>', methods=['POST'])
@admin_required(
    redirect_endpoint='admin.admin_students',
    message='Zugriff verweigert. Nur der Administrator darf Schülergrunddaten löschen.'
)
def admin_student_delete(s_id):
    schueler = get_or_404_session(Schueler, s_id)

    Beobachtung.query.filter_by(schueler_id=s_id).delete()
    db.session.delete(schueler)
    db.session.commit()

    flash(f'{schueler.vorname} {schueler.nachname} und alle zugehörigen Daten wurden gelöscht.')
    return redirect(url_for('admin.admin_students'))


@admin_bp.route('/admin/boegen')
@admin_required(redirect_endpoint='system.index', message=None)
def admin_boegen():
    boegen = Bogen.query.all()
    return render_template('admin_boegen.html', boegen=boegen)


@admin_bp.route('/admin/bogen/edit/<int:b_id>', methods=['GET', 'POST'])
@admin_bp.route('/admin/bogen/new', defaults={'b_id': None}, methods=['GET', 'POST'])
@admin_required(redirect_endpoint='system.index', message=None)
def admin_bogen_edit(b_id):
    next_url = (request.args.get('next') or request.form.get('next') or '').strip()
    if b_id:
        bogen = get_or_404_session(Bogen, b_id)
        titel_prefix = "Bogen bearbeiten"
    else:
        bogen = Bogen()
        titel_prefix = "Neuen Bogen anlegen"

    if request.method == 'POST':
        bogen.titel = request.form.get('titel')

        if not b_id:
            db.session.add(bogen)

        db.session.commit()
        flash(f'Bogen "{bogen.titel}" gespeichert.')
        return redirect(_safe_next_url(next_url, url_for('admin.admin_boegen')))

    return render_template('admin_bogen_edit.html', bogen=bogen, titel_prefix=titel_prefix, next_url=next_url)


@admin_bp.route('/admin/bogen/delete/<int:b_id>', methods=['POST'])
@admin_required(redirect_endpoint='system.index', message=None)
def admin_bogen_delete(b_id):
    bogen = get_or_404_session(Bogen, b_id)

    items = Item.query.filter_by(bogen_id=b_id).all()
    for item in items:
        Beobachtung.query.filter_by(item_id=item.id).delete()
        db.session.delete(item)

    db.session.delete(bogen)
    db.session.commit()
    flash(f'Bogen "{bogen.titel}" und alle zugehörigen Items/Daten gelöscht.')
    return redirect(url_for('admin.admin_boegen'))


@admin_bp.route('/admin/bogen/<int:b_id>/items')
@admin_required(redirect_endpoint='system.index', message=None)
def admin_items(b_id):
    bogen = get_or_404_session(Bogen, b_id)
    items = Item.query.filter_by(bogen_id=b_id).all()
    return render_template('admin_items.html', bogen=bogen, items=items)


@admin_bp.route('/admin/item/edit/<int:i_id>', defaults={'b_id': None}, methods=['GET', 'POST'])
@admin_bp.route('/admin/bogen/<int:b_id>/item/new', defaults={'i_id': None}, methods=['GET', 'POST'])
@admin_required(redirect_endpoint='system.index', message=None)
def admin_item_edit(b_id, i_id):
    next_url = (request.args.get('next') or request.form.get('next') or '').strip()
    if i_id:
        item = get_or_404_session(Item, i_id)
        bogen = item.bogen
        titel_prefix = "Item bearbeiten"
    else:
        bogen = get_or_404_session(Bogen, b_id)
        item = Item(bogen_id=b_id)
        titel_prefix = "Neues Item anlegen"

    if request.method == 'POST':
        item.text = request.form.get('text')
        item.bereich = request.form.get('bereich')

        if not i_id:
            db.session.add(item)

        db.session.commit()
        flash('Item gespeichert.')
        return redirect(_safe_next_url(next_url, url_for('admin.admin_items', b_id=bogen.id)))

    return render_template('admin_item_edit.html', item=item, bogen=bogen, titel_prefix=titel_prefix, next_url=next_url)


@admin_bp.route('/admin/item/delete/<int:i_id>', methods=['POST'])
@admin_required(redirect_endpoint='system.index', message=None)
def admin_item_delete(i_id):
    item = get_or_404_session(Item, i_id)
    bogen_id = item.bogen_id

    Beobachtung.query.filter_by(item_id=i_id).delete()

    db.session.delete(item)
    db.session.commit()
    flash('Item gelöscht.')
    return redirect(url_for('admin.admin_items', b_id=bogen_id))


def register_admin_routes(app):
    app.register_blueprint(admin_bp)
