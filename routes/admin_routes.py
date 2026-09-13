from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for
from sqlalchemy import func
from flask_login import current_user, login_required
from werkzeug.security import generate_password_hash

import benachrichtigungen as bn
from benachrichtigungen import MAIL_TAKTE
from mail_versand import mail_konfiguration, sende_mail
from routes.auth_routes import normalize_email
from authz import admin_required
from db_utils import get_or_404_session
from extensions import db
from models import (
    Beobachtung,
    Bogen,
    BogenJahrgang,
    ClassTaskTemplateCompetency,
    ErziehungsEreignis,
    ErziehungsEreignisKategorie,
    ErziehungsEreignisKonsequenz,
    ErziehungsEreignisVorlage,
    ErziehungsKonsequenz,
    ErziehungsOrt,
    Item,
    Klasse,
    Notification,
    Schueler,
    Schuljahreswechsel,
    SystemKonfiguration,
    User,
    UserKlassenzuordnung,
    WorkPlanTaskCompetency,
)
from school_year import (
    default_school_year_start,
    next_school_year,
    normalize_school_year,
    serialize_ids,
)
from transition_plan import (
    AKTION_ARCHIVIEREN,
    AKTION_ENTFERNEN,
    AKTION_INDIVIDUELL,
    AKTION_UNVERAENDERT,
    AKTION_VERSETZEN,
    class_universe,
    classes_to_age,
    invalid_targets,
    plan_assignment,
    plan_student,
)
from jahrgang import (
    JAHRGAENGE,
    backfill_student_jahrgaenge,
    ensure_klasse,
    grade_from_name,
    normalize_jahrgaenge,
    resolve_student_jahrgang,
    set_klassen_jahrgaenge,
    sync_klassen,
)
from retention import archived_students, overdue_ids, retention_years, summarize
from student_selection import get_distinct_klassen
from time_utils import utc_now
from uploads import loesche_upload_datei


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


@admin_bp.route('/admin/erziehung')
@admin_required(redirect_endpoint='system.index', message=None)
def admin_erziehung_dashboard():
    return render_template('admin_erziehung_dashboard.html')


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


def _pool_entry_usage(kind, entry):
    """Beschreibt, wodurch ein Pool-Eintrag gebunden ist.

    Gibt None zurueck, wenn der Eintrag frei geloescht werden kann, sonst einen
    Satzteil fuer die Rueckmeldung.
    """
    if kind == 'kategorien':
        anzahl = ErziehungsEreignisVorlage.query.filter_by(category_id=entry.id).count()
        if not anzahl:
            return None
        wort = 'Ereignis' if anzahl == 1 else 'Ereignisse'
        return f'{anzahl} {wort} im Pool sind dieser Kategorie zugeordnet'

    if kind == 'ereignisse':
        anzahl = ErziehungsEreignis.query.filter_by(event_template_id=entry.id).count()
    elif kind == 'orte':
        anzahl = ErziehungsEreignis.query.filter_by(ort_id=entry.id).count()
    elif kind == 'konsequenzen':
        anzahl = ErziehungsEreignisKonsequenz.query.filter_by(consequence_id=entry.id).count()
    else:
        return None

    if not anzahl:
        return None
    wort = 'Ereignis' if anzahl == 1 else 'Ereignissen'
    return f'wird in {anzahl} dokumentierten {wort} verwendet'


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

    # Ein gebundener Eintrag laesst sich nicht loeschen, ohne dokumentierte
    # Ereignisse zu beschaedigen. Das Datenmodell sieht dafuer "inaktiv" vor:
    # der Eintrag verschwindet aus den Auswahllisten, bestehende Ereignisse
    # bleiben lesbar.
    usage = _pool_entry_usage(kind, entry)
    if usage:
        hinweis = (
            f'„{entry.name}" kann nicht gelöscht werden: {usage}. '
            'Setzen Sie den Eintrag stattdessen auf „inaktiv" – dann erscheint er '
            'in neuen Ereignissen nicht mehr, bleibt in bestehenden aber lesbar.'
        )
        flash(hinweis)
        return redirect(url_for('admin.admin_erziehung_pool', kind=kind))

    name = entry.name
    db.session.delete(entry)
    db.session.commit()
    flash(f'„{name}" gelöscht.')
    return redirect(url_for('admin.admin_erziehung_pool', kind=kind))


@admin_bp.route('/admin/erziehung/<string:kind>/deaktivieren/<int:entry_id>', methods=['POST'])
@admin_required(redirect_endpoint='admin.admin_erziehung_dashboard', message=None)
def admin_erziehung_pool_deactivate(kind, entry_id):
    cfg = _erziehung_pool_config(kind)
    if not cfg:
        return redirect(url_for('admin.admin_erziehung_dashboard'))

    entry = db.session.get(cfg['model'], entry_id)
    if not entry:
        flash('Eintrag nicht gefunden.')
        return redirect(url_for('admin.admin_erziehung_pool', kind=kind))

    if entry.is_active:
        entry.is_active = False
        db.session.commit()
        flash(f'„{entry.name}" ist jetzt inaktiv und wird in neuen Ereignissen nicht mehr angeboten.')
    else:
        flash(f'„{entry.name}" ist bereits inaktiv.')

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

        if settings.schuljahr and schuljahr != settings.schuljahr:
            flash('Bitte verwenden Sie für eine Änderung des Schuljahres den Schuljahreswechsel.')
            return render_template('admin_system_settings.html', settings=settings, next_url=next_url)
        if schuljahr and not normalize_school_year(schuljahr):
            flash('Das Schuljahr muss das Format JJJJ/JJJJ haben, z. B. 2026/2027.')
            return render_template('admin_system_settings.html', settings=settings, next_url=next_url)
        aufbewahrung_raw = (request.form.get('aufbewahrung_jahre') or '').strip()
        if aufbewahrung_raw:
            if not aufbewahrung_raw.isdigit() or not 1 <= int(aufbewahrung_raw) <= 100:
                flash('Die Aufbewahrungsfrist muss eine Zahl zwischen 1 und 100 Jahren sein.')
                return render_template('admin_system_settings.html', settings=settings, next_url=next_url)
            aufbewahrung = int(aufbewahrung_raw)
        else:
            aufbewahrung = None

        settings.schuljahr = schuljahr
        settings.elternsprechtag_1 = esp1
        settings.elternsprechtag_2 = esp2
        settings.aufbewahrung_jahre = aufbewahrung
        db.session.commit()
        flash('Grundeinstellungen gespeichert.')
        return redirect(_safe_next_url(next_url, url_for('admin.admin_system_settings')))

    return render_template('admin_system_settings.html', settings=settings, next_url=next_url)


@admin_bp.route('/admin/schuljahreswechsel', methods=['GET', 'POST'])
@admin_required(redirect_endpoint='admin.admin_dashboard', message=None)
def admin_school_year_transition():
    settings = _get_system_konfiguration()

    # Klassen, die bisher nur als Name existieren, fuer die Planung anlegen.
    if sync_klassen():
        db.session.commit()

    active_students = (
        Schueler.query
        .filter(Schueler.is_active.is_(True))
        .order_by(Schueler.klasse.asc(), Schueler.nachname.asc(), Schueler.vorname.asc())
        .all()
    )
    klassen = {klasse.name: klasse for klasse in Klasse.query.all()}
    universum = class_universe()

    proposed_year = next_school_year(settings.schuljahr)
    new_year = (request.form.get('neues_schuljahr') or proposed_year).strip()
    start_raw = (request.form.get('schuljahr_beginn') or '').strip()
    proposed_start = default_school_year_start(new_year)
    start_date = _parse_optional_date(start_raw) if start_raw else proposed_start

    valid_ids = {student.id for student in active_students}
    repeater_ids = {
        int(value) for value in request.form.getlist('wiederholer_ids') if value.isdigit()
    } & valid_ids
    # Zielklassen fuer alle Kinder einlesen, nicht nur fuer Wiederholer: auch wer
    # eine jahrgangsuebergreifende Klasse verlaesst, braucht eine.
    individual_targets = {
        student.id: (request.form.get(f'individual_target_{student.id}') or '').strip()
        for student in active_students
    }

    rows = [
        plan_student(
            student,
            student.id in repeater_ids,
            individual_targets.get(student.id),
            universum,
            klassen,
        )
        for student in active_students
    ]
    counts = {AKTION_VERSETZEN: 0, AKTION_INDIVIDUELL: 0, AKTION_ARCHIVIEREN: 0, AKTION_UNVERAENDERT: 0}
    for row in rows:
        counts[row['action']] += 1

    assignment_rows = [
        plan_assignment(assignment, klassen)
        for assignment in UserKlassenzuordnung.query.order_by(UserKlassenzuordnung.klasse).all()
    ]
    assignment_counts = {AKTION_VERSETZEN: 0, AKTION_ENTFERNEN: 0, AKTION_UNVERAENDERT: 0}
    for assignment_row in assignment_rows:
        assignment_counts[assignment_row['action']] += 1

    if request.method == 'POST' and request.form.get('action') == 'execute':
        normalized_year = normalize_school_year(new_year)
        if invalid_targets(rows):
            flash(
                'Bitte für jedes markierte Kind eine Zielklasse wählen, die den '
                'passenden Jahrgang führt.'
            )
        elif not normalized_year:
            flash('Das neue Schuljahr muss das Format JJJJ/JJJJ haben, z. B. 2026/2027.')
        elif not start_date:
            flash('Bitte einen gültigen Beginn des neuen Schuljahres angeben.')
        elif (request.form.get('bisheriges_schuljahr') or '') != (settings.schuljahr or ''):
            flash('Das aktuelle Schuljahr wurde zwischenzeitlich geändert. Bitte Vorschau neu laden.')
            return redirect(url_for('admin.admin_school_year_transition'))
        elif normalized_year == (settings.schuljahr or ''):
            flash('Das neue Schuljahr muss sich vom aktuellen Schuljahr unterscheiden.')
        else:
            # Gruppen mit freiem Namen altern gemeinsam. Die Plaene oben sind
            # bereits mit den Jahrgaengen nach dem Wechsel gerechnet.
            for klasse in classes_to_age(klassen):
                set_klassen_jahrgaenge(klasse, [klasse.jahrgaenge[0] + 1])

            for row in rows:
                student = row['student']
                if row['action'] in {AKTION_VERSETZEN, AKTION_INDIVIDUELL}:
                    ensure_klasse(row['target_class'])
                    student.klasse = row['target_class']
                    student.jahrgang = row['new_jahrgang']
                elif row['action'] == AKTION_ARCHIVIEREN:
                    student.is_active = False
                    student.archived_at = utc_now()
                    # Den wirksamen Jahrgang festschreiben, auch wenn er bisher
                    # nur aus der Klasse abgeleitet war.
                    student.jahrgang = row['jahrgang']

            kept_assignments = {}
            assignments_to_delete = []
            for assignment_row in assignment_rows:
                assignment = assignment_row['assignment']
                target_class = assignment_row['target_class']
                if assignment_row['action'] == AKTION_ENTFERNEN:
                    assignments_to_delete.append(assignment)
                    continue
                key = (assignment.user_id, target_class, assignment.rolle)
                if key in kept_assignments:
                    assignments_to_delete.append(assignment)
                else:
                    kept_assignments[key] = assignment

            # Zwischennamen verhindern Kollisionen mit der Eindeutigkeitsregel,
            # wenn etwa "3a" zu "4a" wird, waehrend "4a" noch belegt ist.
            for assignment_row in assignment_rows:
                assignment_row['assignment'].klasse = f'__schuljahreswechsel_{assignment_row["assignment"].id}'
            db.session.flush()
            for assignment in assignments_to_delete:
                db.session.delete(assignment)
            for (_user_id, target_class, _role), assignment in kept_assignments.items():
                assignment.klasse = target_class
                ensure_klasse(target_class)

            change = Schuljahreswechsel(
                altes_schuljahr=settings.schuljahr,
                neues_schuljahr=normalized_year,
                schuljahr_beginn=start_date,
                wiederholer_ids=serialize_ids(repeater_ids),
                versetzt_anzahl=counts[AKTION_VERSETZEN],
                archiviert_anzahl=counts[AKTION_ARCHIVIEREN],
                unveraendert_anzahl=counts[AKTION_INDIVIDUELL] + counts[AKTION_UNVERAENDERT],
                zuordnungen_versetzt=assignment_counts[AKTION_VERSETZEN],
                zuordnungen_entfernt=assignment_counts[AKTION_ENTFERNEN],
                created_by_user_id=current_user.id,
            )
            settings.schuljahr = normalized_year
            settings.schuljahr_beginn = start_date
            db.session.add(change)
            db.session.commit()
            flash(
                f'Schuljahreswechsel abgeschlossen: {counts[AKTION_VERSETZEN]} versetzt, '
                f'{counts[AKTION_INDIVIDUELL]} individuell zugeordnet, '
                f'{counts[AKTION_ARCHIVIEREN]} archiviert.'
            )
            return redirect(url_for('admin.admin_school_year_transition'))

    history = Schuljahreswechsel.query.order_by(Schuljahreswechsel.created_at.desc()).limit(10).all()
    return render_template(
        'admin_school_year_transition.html',
        settings=settings,
        rows=rows,
        counts=counts,
        new_year=new_year,
        start_date=start_date,
        repeater_ids=repeater_ids,
        individual_targets=individual_targets,
        assignment_rows=assignment_rows,
        assignment_counts=assignment_counts,
        history=history,
    )


@admin_bp.route('/admin/users', methods=['GET', 'POST'])
@admin_required(
    redirect_endpoint='admin.admin_dashboard',
    message='Zugriff verweigert. Nur der Administrator darf Benutzer verwalten.'
)
def admin_users():
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        vorname = (request.form.get('vorname') or '').strip() or None
        nachname = (request.form.get('nachname') or '').strip() or None
        password = request.form.get('password')
        role = (request.form.get('role') or 'teacher').strip().lower()
        if role not in {'admin', 'teacher'}:
            role = 'teacher'
        email, email_fehler = normalize_email(request.form.get('email'))

        if not username:
            flash('Bitte einen Benutzernamen eingeben.')
        elif User.query.filter_by(username=username).first():
            flash('Benutzername existiert bereits!')
        elif email_fehler:
            flash(email_fehler)
        else:
            hashed_pw = generate_password_hash(password)
            new_user = User(
                username=username, vorname=vorname, nachname=nachname, email=email,
                password_hash=hashed_pw, role=role,
            )
            db.session.add(new_user)
            db.session.commit()
            flash(f'Benutzer {username} angelegt.')
            return redirect(url_for('admin.admin_users'))

    users = sorted(
        User.query.all(),
        key=lambda u: ((u.nachname or u.username or '').lower(), (u.vorname or '').lower(), (u.username or '').lower()),
    )
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

    # Nach einem Fehler beim Anlegen die Eingaben stehen lassen (ohne Passwort).
    eingaben = request.form if request.method == 'POST' else {}
    ohne_klasse = sum(1 for u in users if u.id not in zuordnungen_by_user and u.username != 'admin')
    return render_template(
        'admin_users.html', users=users, zuordnungen_by_user=zuordnungen_by_user, eingaben=eingaben,
        anzahl_admins=sum(1 for u in users if u.is_admin),
        anzahl_ohne_klasse=ohne_klasse,
        anzahl_ohne_email=sum(1 for u in users if not u.email),
    )


@admin_bp.route('/admin/users/delete/<int:user_id>', methods=['POST'])
@admin_required(redirect_endpoint='system.index', message=None)
def admin_user_delete(user_id):
    user_to_delete = get_or_404_session(User, user_id)

    if user_to_delete.is_admin:
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
        email, email_fehler = normalize_email(request.form.get('email'))
        if email_fehler:
            flash(email_fehler)
            return _render_user_edit(user, next_url)
        user.email = email
        requested_role = (request.form.get('role') or user.role or 'teacher').strip().lower()
        if requested_role not in {'admin', 'teacher'}:
            requested_role = user.role or 'teacher'

        if user.is_admin and requested_role != 'admin':
            admin_count = User.query.filter_by(role='admin').count()
            if admin_count <= 1:
                flash('Der letzte Administrator kann nicht auf Lehrkraft zurückgesetzt werden.')
                return _render_user_edit(user, next_url)

        user.role = requested_role
        db.session.commit()
        flash(f'Benutzerdaten für {user.username} gespeichert.')
        return redirect(_safe_next_url(next_url, url_for('admin.admin_users')))

    return _render_user_edit(user, next_url)


def _render_user_edit(user, next_url):
    zuordnungen = UserKlassenzuordnung.query.filter_by(user_id=user.id).all()
    return render_template(
        'admin_user_edit.html',
        user=user,
        next_url=next_url,
        klassenleitung=next((z.klasse for z in zuordnungen if z.rolle == 'klassenleitung'), None),
        fachklassen=sorted({z.klasse for z in zuordnungen if z.rolle == 'fach'}, key=str.lower),
        mail_takte=MAIL_TAKTE,
        mail_aktiv=mail_konfiguration() is not None,
    )


@admin_bp.route('/admin/users/reset-password/<int:user_id>', methods=['POST'])
@admin_required(redirect_endpoint='admin.admin_dashboard', message=None)
def admin_user_reset_password(user_id):
    user = get_or_404_session(User, user_id)
    next_url = (request.form.get('next') or '').strip()
    new_password = request.form.get('new_password') or ''
    new_password_repeat = request.form.get('new_password_repeat') or ''

    if len(new_password) < 8:
        flash('Das neue Passwort muss mindestens 8 Zeichen lang sein.')
        return redirect(url_for('admin.admin_user_edit', user_id=user.id, next=next_url) + '#passwort')
    if new_password != new_password_repeat:
        flash('Die neuen Passwörter stimmen nicht überein.')
        return redirect(url_for('admin.admin_user_edit', user_id=user.id, next=next_url) + '#passwort')

    user.password_hash = generate_password_hash(new_password)
    db.session.commit()
    flash(f'Passwort für {user.username} wurde zurückgesetzt.')
    return redirect(_safe_next_url(next_url, url_for('admin.admin_users')))

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

        bisher = {
            (z.klasse, z.rolle) for z in UserKlassenzuordnung.query.filter_by(user_id=user.id).all()
        }
        UserKlassenzuordnung.query.filter_by(user_id=user.id).delete()
        neu = ([(klassenleitung_klasse, 'klassenleitung')] if klassenleitung_klasse else []) + [
            (klasse, 'fach') for klasse in fachklassen
        ]
        hinzu = [eintrag for eintrag in neu if eintrag not in bisher]
        if hinzu:
            bn.benachrichtige(
                bn.KLASSE_ZUGEORDNET,
                [user.id],
                'Neue Klassenzuordnung',
                text='; '.join(
                    f'{"Klassenleitung" if rolle == "klassenleitung" else "Fachlehrkraft"} in {klasse}'
                    for klasse, rolle in hinzu
                ) + f'{bn.von_wem()}.',
                ziel=url_for('auth.user_menu'),
            )
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


def _klassen_mit_jahrgaengen():
    """Name -> Jahrgaenge fuer alle bekannten Klassen, fuer Hinweise im Formular."""
    return {klasse.name: klasse.jahrgaenge for klasse in Klasse.query.all()}


@admin_bp.route('/admin/klassen')
@admin_required(
    redirect_endpoint='admin.admin_dashboard',
    message='Zugriff verweigert. Nur der Administrator darf Klassen verwalten.',
)
def admin_klassen():
    # Klassen, die bisher nur als Name an Kindern standen, sichtbar machen.
    if sync_klassen():
        db.session.commit()

    zeilen = []
    for klasse in sorted(Klasse.query.all(), key=lambda k: (
        (k.jahrgaenge or [99])[0], k.name.lower(),
    )):
        kinder = Schueler.query.filter(
            Schueler.klasse == klasse.name, Schueler.is_active.is_(True),
        ).all()
        stufen = klasse.jahrgaenge
        # Kinder, deren Jahrgang nicht zur Klasse passt oder fehlt - sie
        # brauchen eine Entscheidung, bevor der Schuljahreswechsel laeuft.
        unpassend = [
            kind for kind in kinder
            if kind.jahrgang is None or (stufen and kind.jahrgang not in stufen)
        ]
        zeilen.append({
            'klasse': klasse,
            'stufen': stufen,
            'name_bestimmt': grade_from_name(klasse.name) is not None,
            'kinder': len(kinder),
            'unpassend': unpassend,
        })

    return render_template(
        'admin_klassen.html',
        zeilen=zeilen,
        jahrgaenge=JAHRGAENGE,
        unpassend_gesamt=sum(len(zeile['unpassend']) for zeile in zeilen),
    )


@admin_bp.route('/admin/klassen/<int:klasse_id>', methods=['POST'])
@admin_required(
    redirect_endpoint='admin.admin_dashboard',
    message='Zugriff verweigert. Nur der Administrator darf Klassen verwalten.',
)
def admin_klasse_jahrgaenge(klasse_id):
    klasse = get_or_404_session(Klasse, klasse_id)
    try:
        stufen = set_klassen_jahrgaenge(klasse, request.form.getlist('jahrgaenge'))
    except ValueError as fehler:
        db.session.rollback()
        flash(str(fehler))
        return redirect(url_for('admin.admin_klassen'))

    # Nur leere Jahrgaenge der Kinder nachtragen, nie vorhandene ueberschreiben.
    nachgetragen = backfill_student_jahrgaenge()
    db.session.commit()

    if stufen:
        meldung = f'Klasse „{klasse.name}": Jahrgang {", ".join(str(j) for j in stufen)}.'
    else:
        meldung = f'Klasse „{klasse.name}": kein Jahrgang eingetragen.'
    if nachgetragen:
        meldung += f' Bei {nachgetragen} Kind(ern) wurde der Jahrgang übernommen.'
    flash(meldung)
    return redirect(url_for('admin.admin_klassen'))


@admin_bp.route('/admin/students')
@admin_required(redirect_endpoint='system.index', message='Zugriff verweigert. Nur der Administrator darf Schülergrunddaten verwalten.')
def admin_students():
    show_archived = request.args.get('show') == 'archived'
    schueler = (
        Schueler.query
        .filter(Schueler.is_active.is_(not show_archived))
        .order_by(Schueler.klasse, Schueler.nachname, Schueler.vorname)
        .all()
    )
    return render_template('admin_students.html', schueler=schueler, show_archived=show_archived)


@admin_bp.route('/admin/student/edit/<int:s_id>', methods=['GET', 'POST'])
@admin_required(
    redirect_endpoint='admin.admin_students',
    message='Zugriff verweigert. Nur der Administrator darf Schülergrunddaten bearbeiten.'
)
def admin_student_edit(s_id):
    schueler = get_or_404_session(Schueler, s_id)
    next_url = (request.args.get('next') or request.form.get('next') or '').strip()

    if request.method == 'POST':
        bisherige_klasse = schueler.klasse
        schueler.vorname = request.form.get('vorname')
        schueler.nachname = request.form.get('nachname')
        schueler.klasse = (request.form.get('klasse') or '').strip()
        geburtsdatum_raw = request.form.get('geburtsdatum')
        if (geburtsdatum_raw or '').strip() and _parse_optional_date(geburtsdatum_raw) is None:
            flash('Geburtsdatum hat kein gültiges Format.')
            return render_template(
                'admin_student_edit.html', s=schueler, next_url=next_url,
                jahrgaenge=JAHRGAENGE, klassen=_klassen_mit_jahrgaengen(),
            )

        # Klasse anlegen, falls neu, und den Jahrgang daran pruefen.
        ensure_klasse(schueler.klasse)
        jahrgang, fehler = resolve_student_jahrgang(
            schueler.klasse, request.form.get('jahrgang'),
        )
        if fehler:
            db.session.rollback()
            flash(fehler)
            return render_template(
                'admin_student_edit.html', s=schueler, next_url=next_url,
                jahrgaenge=JAHRGAENGE, klassen=_klassen_mit_jahrgaengen(),
            )
        schueler.jahrgang = jahrgang
        schueler.geburtsdatum = _parse_optional_date(geburtsdatum_raw)
        requested_active = request.form.get("is_active") == "1"
        if requested_active != schueler.is_active:
            schueler.is_active = requested_active
            schueler.archived_at = None if requested_active else utc_now()
        if schueler.is_active and schueler.klasse and schueler.klasse != bisherige_klasse:
            bn.benachrichtige(
                bn.KIND_NEU_IN_KLASSE,
                bn.klassenleitungen([schueler.klasse]),
                f'Neu in Klasse {schueler.klasse}: {schueler.vorname} {schueler.nachname}',
                text=f'Bisher in Klasse {bisherige_klasse}.' if bisherige_klasse else None,
                ziel=url_for('system.schuelerakte', schueler_id=schueler.id),
            )
        db.session.commit()
        flash('Schülerdaten aktualisiert.')
        return redirect(_safe_next_url(next_url, url_for('admin.admin_students')))

    return render_template(
        'admin_student_edit.html', s=schueler, next_url=next_url,
        jahrgaenge=JAHRGAENGE, klassen=_klassen_mit_jahrgaengen(),
    )


def _collect_student_upload_paths(schueler):
    """Sammelt alle Uploads, die zu einem Kind gehören.

    Muss vor dem Löschen laufen: danach sind die Datensätze fort, die auf die
    Dateien zeigen, und die Dateien wären nicht mehr auffindbar.
    """
    paths = []

    for beobachtung in schueler.beobachtungen:
        if beobachtung.foto_pfad:
            paths.append(beobachtung.foto_pfad)

    for ereignis in schueler.erziehungsereignisse:
        for anhang in ereignis.attachments:
            if anhang.file_path:
                paths.append(anhang.file_path)

    for plan in schueler.work_plans:
        for aufgabe in plan.tasks:
            for anhang in aufgabe.attachments:
                if anhang.file_path:
                    paths.append(anhang.file_path)

    return paths


def student_record_counts(schueler):
    """Umfang der Daten eines Kindes - fuer Rueckmeldung und Vorschau."""
    return {
        'Beobachtungen': len(schueler.beobachtungen),
        'Förderpläne': len(schueler.foerderplaene),
        'Elternkontakte': len(schueler.elternkontakte),
        'Beratungen': len(schueler.elternberatungen),
        'Ereignisse': len(schueler.erziehungsereignisse),
        'Arbeitspläne': len(schueler.work_plans),
    }


def _delete_student_completely(schueler):
    """Loescht ein Kind mit allem, was daran haengt, und meldet den Umfang.

    Gemeinsame Grundlage fuer die Einzelloeschung in der Schuelerverwaltung und
    die Loeschung abgelaufener Fristen. Der Commit passiert hier; die Dateien
    werden erst danach entfernt.
    """
    name = f'{schueler.vorname} {schueler.nachname}'.strip()
    counts = student_record_counts(schueler)
    upload_paths = _collect_student_upload_paths(schueler)

    # Die Beziehungen von Schueler tragen delete-orphan-Kaskaden, das Löschen des
    # Kindes nimmt Förderplanung, Elternkontakte, Ereignisse und Arbeitspläne mit.
    db.session.delete(schueler)
    db.session.commit()

    # Dateien erst nach erfolgreichem Commit entfernen: bricht die Transaktion ab,
    # bleiben die Datensätze bestehen und dürfen ihre Dateien nicht verloren haben.
    deleted_files = sum(1 for path in upload_paths if loesche_upload_datei(path))

    return {
        'name': name,
        'counts': counts,
        'dateien_gesamt': len(upload_paths),
        'dateien_geloescht': deleted_files,
    }


@admin_bp.route('/admin/student/delete/<int:s_id>', methods=['POST'])
@admin_required(
    redirect_endpoint='admin.admin_students',
    message='Zugriff verweigert. Nur der Administrator darf Schülergrunddaten löschen.'
)
def admin_student_delete(s_id):
    schueler = get_or_404_session(Schueler, s_id)
    ergebnis = _delete_student_completely(schueler)

    details = ', '.join(
        f'{anzahl} {label}' for label, anzahl in ergebnis['counts'].items() if anzahl
    )
    message = f"{ergebnis['name']} wurde gelöscht."
    if details:
        message += f' Mitgelöscht: {details}.'
    if ergebnis['dateien_gesamt']:
        message += (
            f" Dateien entfernt: {ergebnis['dateien_geloescht']}"
            f" von {ergebnis['dateien_gesamt']}."
        )
    flash(message)

    return redirect(url_for('admin.admin_students'))


@admin_bp.route('/admin/benachrichtigungen', methods=['GET', 'POST'])
@admin_required(
    redirect_endpoint='admin.admin_dashboard',
    message='Zugriff verweigert. Nur der Administrator darf den E-Mail-Versand einsehen.'
)
def admin_mail():
    konfiguration = mail_konfiguration()

    if request.method == 'POST':
        if not konfiguration:
            flash('Der E-Mail-Versand ist nicht eingerichtet.')
        elif not current_user.email:
            flash('Für eine Testmail zuerst im Konto eine eigene E-Mail-Adresse eintragen.')
        else:
            try:
                sende_mail(
                    current_user.email,
                    'KompetenzKompass: Testmail',
                    f'Hallo {current_user.display_name},\n\n'
                    'diese Testmail zeigt: Der KompetenzKompass kann E-Mails verschicken.\n',
                    konfiguration,
                )
            except Exception as fehler:  # noqa: BLE001 - dem Admin zeigen, was schiefging
                flash(f'Testmail fehlgeschlagen: {type(fehler).__name__}: {fehler}')
            else:
                flash(f'Testmail an {current_user.email} verschickt.')
        return redirect(url_for('admin.admin_mail'))

    offen = (
        Notification.query
        .join(User, Notification.user_id == User.id)
        .filter(Notification.mailed_at.is_(None), User.email.isnot(None), User.email != '')
    )
    lehrkraefte = User.query.order_by(User.nachname.asc(), User.vorname.asc(), User.username.asc()).all()
    return render_template(
        'admin_mail.html',
        konfiguration=konfiguration,
        lehrkraefte=lehrkraefte,
        mail_takte=MAIL_TAKTE,
        offen_sofort=offen.filter(User.mail_takt == 'sofort').order_by(Notification.created_at.asc()).first(),
        offen_anzahl=offen.count(),
        jetzt=utc_now(),
    )


@admin_bp.route('/admin/aufbewahrung')
@admin_required(
    redirect_endpoint='admin.admin_dashboard',
    message='Zugriff verweigert. Nur der Administrator darf Aufbewahrungsfristen einsehen.'
)
def admin_retention():
    config = _get_system_konfiguration()
    zeilen = archived_students(config)
    for zeile in zeilen:
        zeile['counts'] = student_record_counts(zeile['schueler'])

    return render_template(
        'admin_retention.html',
        jahre=retention_years(config),
        zeilen=zeilen,
        zusammenfassung=summarize(zeilen),
    )


@admin_bp.route('/admin/aufbewahrung/loeschen', methods=['POST'])
@admin_required(
    redirect_endpoint='admin.admin_dashboard',
    message='Zugriff verweigert. Nur der Administrator darf Schülergrunddaten löschen.'
)
def admin_retention_delete():
    config = _get_system_konfiguration()
    zeilen = archived_students(config)
    faellige = overdue_ids(zeilen)

    gewaehlt = {
        int(wert) for wert in request.form.getlist('schueler_ids') if wert.isdigit()
    }
    if not gewaehlt:
        flash('Es wurde kein Datensatz ausgewählt.')
        return redirect(url_for('admin.admin_retention'))

    # Serverseitig erneut prüfen: die Auswahl stammt aus einem Formular, das
    # inzwischen veraltet sein kann, und nur abgelaufene Fristen dürfen fallen.
    zu_loeschen = gewaehlt & faellige
    uebersprungen = len(gewaehlt - faellige)

    geloescht = []
    for schueler_id in sorted(zu_loeschen):
        schueler = db.session.get(Schueler, schueler_id)
        if schueler:
            geloescht.append(_delete_student_completely(schueler))

    if geloescht:
        namen = ', '.join(eintrag['name'] for eintrag in geloescht)
        dateien = sum(eintrag['dateien_geloescht'] for eintrag in geloescht)
        flash(
            f'{len(geloescht)} Datensatz/Datensätze endgültig gelöscht: {namen}.'
            + (f' Dabei {dateien} Datei(en) entfernt.' if dateien else '')
        )
    if uebersprungen:
        flash(
            f'{uebersprungen} Auswahl(en) übersprungen: die Frist ist dort nicht '
            'abgelaufen. Bitte die Liste neu laden.'
        )

    return redirect(url_for('admin.admin_retention'))


@admin_bp.route('/admin/boegen')
@admin_required(redirect_endpoint='system.index', message=None)
def admin_boegen():
    boegen = Bogen.query.order_by(Bogen.pflicht.desc(), Bogen.titel.asc()).all()
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
        bogen.pflicht = request.form.get('pflicht') == '1'

        if not b_id:
            db.session.add(bogen)

        # Keine Auswahl heisst: gilt fuer alle Jahrgaenge.
        bogen.jahrgang_zuordnungen = [
            BogenJahrgang(jahrgang=jahrgang)
            for jahrgang in normalize_jahrgaenge(request.form.getlist('jahrgaenge'))
        ]

        db.session.commit()
        flash(f'Bogen "{bogen.titel}" gespeichert.')
        return redirect(_safe_next_url(next_url, url_for('admin.admin_boegen')))

    return render_template(
        'admin_bogen_edit.html', bogen=bogen, titel_prefix=titel_prefix, next_url=next_url,
        jahrgaenge=JAHRGAENGE,
    )


def _item_dependencies(item_ids):
    """Zaehlt, was an einer Menge von Kompetenzen haengt.

    Liefert eine Liste lesbarer Angaben; ist sie leer, kann geloescht werden.
    """
    item_ids = list(item_ids)
    if not item_ids:
        return []

    angaben = []

    beobachtungen = Beobachtung.query.filter(Beobachtung.item_id.in_(item_ids)).count()
    if beobachtungen:
        kinder = (
            db.session.query(func.count(func.distinct(Beobachtung.schueler_id)))
            .filter(Beobachtung.item_id.in_(item_ids))
            .scalar()
        ) or 0
        wort = 'Beobachtung' if beobachtungen == 1 else 'Beobachtungen'
        angabe = f'{beobachtungen} {wort}'
        if kinder:
            angabe += f' zu {kinder} {"Kind" if kinder == 1 else "Kindern"}'
        angaben.append(angabe)

    aufgaben = WorkPlanTaskCompetency.query.filter(
        WorkPlanTaskCompetency.item_id.in_(item_ids)
    ).count()
    if aufgaben:
        wort = 'Aufgabe' if aufgaben == 1 else 'Aufgaben'
        angaben.append(f'{aufgaben} {wort} in Arbeitsplänen')

    vorlagen = ClassTaskTemplateCompetency.query.filter(
        ClassTaskTemplateCompetency.item_id.in_(item_ids)
    ).count()
    if vorlagen:
        wort = 'Vorlage' if vorlagen == 1 else 'Vorlagen'
        angaben.append(f'{vorlagen} {wort} in Aufgabenbibliotheken')

    return angaben


@admin_bp.route('/admin/bogen/delete/<int:b_id>', methods=['POST'])
@admin_required(redirect_endpoint='system.index', message=None)
def admin_bogen_delete(b_id):
    bogen = get_or_404_session(Bogen, b_id)

    item_ids = [row[0] for row in db.session.query(Item.id).filter(Item.bogen_id == b_id).all()]

    # Ein Bogen sammelt die Dokumentation eines ganzen Schuljahres. Solange daran
    # etwas haengt, wird nicht geloescht - die Beobachtungen aller Kinder zu
    # diesem Bogen waeren sonst mit einem Klick fort.
    angaben = _item_dependencies(item_ids)
    if angaben:
        flash(
            f'Bogen „{bogen.titel}" kann nicht gelöscht werden. '
            f'Daran hängen noch: {", ".join(angaben)}.'
        )
        return redirect(url_for('admin.admin_boegen'))

    titel = bogen.titel
    for item in Item.query.filter_by(bogen_id=b_id).all():
        db.session.delete(item)
    db.session.delete(bogen)
    db.session.commit()

    anzahl = len(item_ids)
    if anzahl:
        wort = 'Kompetenz' if anzahl == 1 else 'Kompetenzen'
        flash(f'Bogen „{titel}" mit {anzahl} {wort} gelöscht.')
    else:
        flash(f'Bogen „{titel}" gelöscht.')
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

    angaben = _item_dependencies([item.id])
    if angaben:
        flash(
            f'Kompetenz „{item.text}" kann nicht gelöscht werden. '
            f'Daran hängen noch: {", ".join(angaben)}.'
        )
        return redirect(url_for('admin.admin_items', b_id=bogen_id))

    text = item.text
    db.session.delete(item)
    db.session.commit()
    flash(f'Kompetenz „{text}" gelöscht.')
    return redirect(url_for('admin.admin_items', b_id=bogen_id))


def register_admin_routes(app):
    app.register_blueprint(admin_bp)
