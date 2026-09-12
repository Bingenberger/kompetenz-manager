from datetime import datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from extensions import db
from models import (
    Elternkontakt,
    ErziehungsEreignis,
    ErziehungsEreignisAnhang,
    ErziehungsEreignisBetroffenesKind,
    ErziehungsEreignisElternkontakt,
    ErziehungsEreignisKategorie,
    ErziehungsEreignisKonsequenz,
    ErziehungsEreignisLog,
    ErziehungsEreignisVorlage,
    ErziehungsKonsequenz,
    ErziehungsOrt,
    Notification,
    Schueler,
    User,
)
from student_selection import get_prioritized_students_for_user, get_tabbed_student_selection_for_user
from time_utils import utc_now
from uploads import (
    loesche_upload_dateien,
    speichere_upload_erziehung_anhang,
)


erziehung_bp = Blueprint('erziehung', __name__)


def _safe_next_url(candidate, fallback_url):
    value = (candidate or '').strip()
    if value.startswith('/') and not value.startswith('//'):
        return value
    return fallback_url


def _is_admin(user):
    return bool(user and getattr(user, 'username', None) == 'admin')


def _get_accessible_students(user):
    return get_prioritized_students_for_user(user, include_archived=True)


def _get_accessible_student_ids(user):
    return {student.id for student in _get_accessible_students(user)}


def _can_access_student(user, student_id):
    if _is_admin(user):
        return db.session.get(Schueler, student_id)
    return next((student for student in _get_accessible_students(user) if student.id == student_id), None)


def _get_event_or_404(event_id):
    event = db.session.get(ErziehungsEreignis, event_id)
    if not event:
        abort(404)
    if _is_admin(current_user):
        return event
    if event.student_id not in _get_accessible_student_ids(current_user):
        abort(403)
    return event


def _parse_date_field(name, default=None):
    raw = (request.form.get(name) or '').strip()
    if not raw:
        return default
    try:
        return datetime.strptime(raw, '%Y-%m-%d').date()
    except ValueError:
        return None


def _teacher_choices():
    query = User.query.order_by(User.vorname.asc(), User.nachname.asc(), User.username.asc())
    return query.all()


def _group_students_for_multi_select(students):
    grouped = {}
    for student in students:
        label = (student.klasse or 'Ohne Klasse').strip() or 'Ohne Klasse'
        grouped.setdefault(label, []).append(student)
    return [
        {
            'label': label,
            'students': sorted(entries, key=lambda s: ((s.nachname or '').lower(), (s.vorname or '').lower())),
        }
        for label, entries in sorted(grouped.items(), key=lambda item: item[0].lower())
    ]


def _pool_context():
    categories = (
        ErziehungsEreignisKategorie.query
        .filter_by(is_active=True)
        .order_by(ErziehungsEreignisKategorie.sort_order.asc(), ErziehungsEreignisKategorie.name.asc())
        .all()
    )
    templates = (
        ErziehungsEreignisVorlage.query
        .filter_by(is_active=True)
        .order_by(ErziehungsEreignisVorlage.sort_order.asc(), ErziehungsEreignisVorlage.name.asc())
        .all()
    )
    templates_by_category = {}
    for category in categories:
        templates_by_category[category.id] = [tpl for tpl in templates if tpl.category_id == category.id]

    orte = (
        ErziehungsOrt.query
        .filter_by(is_active=True)
        .order_by(ErziehungsOrt.sort_order.asc(), ErziehungsOrt.name.asc())
        .all()
    )
    konsequenzen = (
        ErziehungsKonsequenz.query
        .filter_by(is_active=True)
        .order_by(ErziehungsKonsequenz.sort_order.asc(), ErziehungsKonsequenz.name.asc())
        .all()
    )
    return {
        'categories': categories,
        'templates_by_category': templates_by_category,
        'orte': orte,
        'konsequenzen': konsequenzen,
        'teacher_choices': _teacher_choices(),
    }


def _selected_ids(entries, attr_name):
    return {getattr(entry, attr_name) for entry in entries}


def _event_snapshot(event):
    return {
        'student_id': event.student_id,
        'datum': event.datum.isoformat() if event.datum else '',
        'event_template_id': event.event_template_id,
        'ort_id': event.ort_id,
        'status': event.status or '',
        'assigned_user_id': event.assigned_user_id,
        'beschreibung': event.beschreibung or '',
        'consequence_notes': event.consequence_notes or '',
        'child_statement': event.child_statement or '',
        'others_statement': event.others_statement or '',
        'affected_ids': sorted(row.student_id for row in event.affected_students),
        'consequence_ids': sorted(row.consequence_id for row in event.selected_consequences),
        'contact_ids': sorted(row.kontakt_id for row in event.linked_parent_contacts),
        'attachment_names': sorted((row.original_name or row.file_path or '') for row in event.attachments),
    }


def _name_for_student(student_id):
    student = db.session.get(Schueler, student_id) if student_id else None
    if not student:
        return '-'
    return f"{student.vorname or ''} {student.nachname or ''}".strip() or f"Kind #{student.id}"


def _name_for_template(template_id):
    template = db.session.get(ErziehungsEreignisVorlage, template_id) if template_id else None
    return template.name if template else '-'


def _name_for_place(ort_id):
    ort = db.session.get(ErziehungsOrt, ort_id) if ort_id else None
    return ort.name if ort else '-'


def _name_for_user(user_id):
    user = db.session.get(User, user_id) if user_id else None
    return user.display_name if user else '-'


def _names_for_students(student_ids):
    if not student_ids:
        return 'keine'
    return ', '.join(_name_for_student(student_id) for student_id in student_ids)


def _format_log_text(value, limit=180):
    text = (value or '').strip()
    if not text:
        return 'leer'
    compact = ' '.join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[:limit - 1] + '...'


def _names_for_consequences(consequence_ids):
    if not consequence_ids:
        return 'keine'
    names = []
    for consequence_id in consequence_ids:
        consequence = db.session.get(ErziehungsKonsequenz, consequence_id)
        names.append(consequence.name if consequence else f'#{consequence_id}')
    return ', '.join(names)


def _titles_for_contacts(contact_ids):
    if not contact_ids:
        return 'keine'
    labels = []
    for contact_id in contact_ids:
        kontakt = db.session.get(Elternkontakt, contact_id)
        if not kontakt:
            labels.append(f'#{contact_id}')
            continue
        label = f"{kontakt.datum.strftime('%d.%m.%Y')} {kontakt.eintrag_typ}"
        if kontakt.betreff:
            label += f" ({kontakt.betreff})"
        labels.append(label)
    return ', '.join(labels)


def _append_log(event, action, details):
    db.session.add(
        ErziehungsEreignisLog(
            event_id=event.id,
            user_id=getattr(current_user, 'id', None),
            action=action,
            details=details,
        )
    )


def _create_assignment_notification(event, assigned_user_id):
    if not assigned_user_id or assigned_user_id == getattr(current_user, 'id', None):
        return
    assigned_user = db.session.get(User, assigned_user_id)
    if not assigned_user:
        return
    student_name = _name_for_student(event.student_id)
    event_name = _name_for_template(event.event_template_id)
    db.session.add(
        Notification(
            user_id=assigned_user_id,
            title=f'Neuer zugewiesener Fall: {student_name}',
            message=f'{event_name} vom {event.datum.strftime("%d.%m.%Y")} wurde Ihnen zugewiesen.',
            target_url=url_for('erziehung.erziehung_view', event_id=event.id),
        )
    )


def _describe_event_creation(event):
    parts = [
        f"Kind: {_name_for_student(event.student_id)}",
        f"Ereignis: {_name_for_template(event.event_template_id)}",
        f"Ort: {_name_for_place(event.ort_id)}",
        f"Status: {event.status}",
    ]
    if event.assigned_user_id:
        parts.append(f"Zuständigkeit: {_name_for_user(event.assigned_user_id)}")
    if event.selected_consequences:
        parts.append(f"Konsequenzen: {_names_for_consequences([row.consequence_id for row in event.selected_consequences])}")
    if event.affected_students:
        parts.append(f"Betroffene Kinder: {_names_for_students([row.student_id for row in event.affected_students])}")
    if event.linked_parent_contacts:
        parts.append(f"Elternkontakte: {_titles_for_contacts([row.kontakt_id for row in event.linked_parent_contacts])}")
    if event.attachments:
        parts.append(f"Anhänge: {', '.join((row.original_name or row.file_path or '') for row in event.attachments)}")
    return '; '.join(parts)


def _describe_event_changes(before, after):
    new_attachments = [name for name in after['attachment_names'] if name not in before['attachment_names']]
    removed_attachments = [name for name in before['attachment_names'] if name not in after['attachment_names']]

    status_changes = []
    if before['status'] != after['status']:
        status_changes.append(f"Status: {before['status'] or '-'} -> {after['status'] or '-'}")

    content_changes = []
    content_specs = [
        ('student_id', 'Kind', _name_for_student),
        ('datum', 'Datum', lambda value: value or '-'),
        ('event_template_id', 'Ereignis', _name_for_template),
        ('ort_id', 'Ort', _name_for_place),
        ('assigned_user_id', 'Zuständigkeit', _name_for_user),
        ('beschreibung', 'Beschreibung', _format_log_text),
        ('consequence_notes', 'Konsequenzbeschreibung', _format_log_text),
        ('child_statement', 'Stellungnahme Kind', _format_log_text),
        ('others_statement', 'Stellungnahmen weiterer Beteiligter', _format_log_text),
    ]
    for key, label, formatter in content_specs:
        if before[key] != after[key]:
            content_changes.append(f"{label}: {formatter(before[key])} -> {formatter(after[key])}")

    link_changes = []
    if before['affected_ids'] != after['affected_ids']:
        link_changes.append(f"Betroffene Kinder: {_names_for_students(before['affected_ids'])} -> {_names_for_students(after['affected_ids'])}")
    if before['consequence_ids'] != after['consequence_ids']:
        link_changes.append(f"Konsequenzen: {_names_for_consequences(before['consequence_ids'])} -> {_names_for_consequences(after['consequence_ids'])}")
    if before['contact_ids'] != after['contact_ids']:
        link_changes.append(f"Elternkontakte: {_titles_for_contacts(before['contact_ids'])} -> {_titles_for_contacts(after['contact_ids'])}")

    attachment_changes = []
    if new_attachments:
        attachment_changes.append(f"Hinzugefügt: {', '.join(new_attachments)}")
    if removed_attachments:
        attachment_changes.append(f"Entfernt: {', '.join(removed_attachments)}")

    return {
        'status_changed': '; '.join(status_changes),
        'content_changed': '; '.join(content_changes),
        'links_changed': '; '.join(link_changes),
        'attachments_changed': '; '.join(attachment_changes),
    }


def _child_contacts_for_student(student_id):
    return (
        Elternkontakt.query
        .filter(Elternkontakt.schueler_id == student_id)
        .order_by(Elternkontakt.datum.desc())
        .limit(40)
        .all()
    )


def _apply_event_form(event, selected_student):
    event.datum = _parse_date_field('datum', default=utc_now().date())
    event.beschreibung = (request.form.get('beschreibung') or '').strip()
    event.status = (request.form.get('status') or 'offen').strip() or 'offen'
    event.consequence_notes = (request.form.get('consequence_notes') or '').strip() or None
    event.child_statement = (request.form.get('child_statement') or '').strip() or None
    event.others_statement = (request.form.get('others_statement') or '').strip() or None
    event.student_id = selected_student.id

    template_raw = (request.form.get('event_template_id') or '').strip()
    ort_raw = (request.form.get('ort_id') or '').strip()
    assigned_raw = (request.form.get('assigned_user_id') or '').strip()

    event.event_template_id = int(template_raw) if template_raw.isdigit() else None
    event.ort_id = int(ort_raw) if ort_raw.isdigit() else None
    event.assigned_user_id = int(assigned_raw) if assigned_raw.isdigit() else None


def _validate_event_form(selected_student):
    errors = []
    if not selected_student:
        errors.append('Bitte zuerst ein Kind auswählen.')
    if _parse_date_field('datum') is None:
        errors.append('Bitte ein gültiges Datum angeben.')
    if not (request.form.get('event_template_id') or '').strip().isdigit():
        errors.append('Bitte ein Ereignis auswählen.')
    if not (request.form.get('ort_id') or '').strip().isdigit():
        errors.append('Bitte einen Ort auswählen.')
    if not (request.form.get('beschreibung') or '').strip():
        errors.append('Bitte eine Beschreibung erfassen.')
    return errors


def _replace_relations(event, selected_student):
    ErziehungsEreignisBetroffenesKind.query.filter_by(event_id=event.id).delete()
    ErziehungsEreignisKonsequenz.query.filter_by(event_id=event.id).delete()
    ErziehungsEreignisElternkontakt.query.filter_by(event_id=event.id).delete()

    affected_ids = []
    for raw in request.form.getlist('affected_student_ids'):
        if raw.isdigit():
            student_id = int(raw)
            if student_id != selected_student.id and _can_access_student(current_user, student_id):
                affected_ids.append(student_id)
    for student_id in sorted(set(affected_ids)):
        db.session.add(ErziehungsEreignisBetroffenesKind(event_id=event.id, student_id=student_id))

    consequence_ids = []
    for raw in request.form.getlist('konsequenz_ids'):
        if raw.isdigit():
            consequence_ids.append(int(raw))
    for consequence_id in sorted(set(consequence_ids)):
        db.session.add(ErziehungsEreignisKonsequenz(event_id=event.id, consequence_id=consequence_id))

    linked_contact_ids = []
    for raw in request.form.getlist('elternkontakt_ids'):
        if raw.isdigit():
            kontakt_id = int(raw)
            kontakt = db.session.get(Elternkontakt, kontakt_id)
            if kontakt and kontakt.schueler_id == selected_student.id:
                linked_contact_ids.append(kontakt_id)
    for kontakt_id in sorted(set(linked_contact_ids)):
        db.session.add(ErziehungsEreignisElternkontakt(event_id=event.id, kontakt_id=kontakt_id))


def _store_attachments(event):
    added_names = []
    uploads = request.files.getlist('attachments')
    for upload in uploads:
        if not upload or not getattr(upload, 'filename', ''):
            continue
        file_path, mime_type = speichere_upload_erziehung_anhang(upload)
        if not file_path:
            flash(f'Anhang "{upload.filename}" konnte nicht gespeichert werden. Erlaubt sind JPG, PNG, WEBP oder PDF bis 10 MB.')
            continue
        db.session.add(
            ErziehungsEreignisAnhang(
                event_id=event.id,
                file_path=file_path,
                original_name=upload.filename,
                mime_type=mime_type,
            )
        )
        added_names.append(upload.filename)
    return added_names


def _form_page_context(selected_student, active_tab, event=None):
    selection = get_tabbed_student_selection_for_user(
        current_user,
        selected_s_id=str(selected_student.id) if selected_student else (request.values.get('schueler_id') or '').strip(),
        requested_tab=(request.values.get('tab') or active_tab or '').strip(),
        auto_select_first=False,
    )
    if selected_student and not selection['selected_student']:
        selection['selected_student'] = selected_student
        selection['selected_s_id'] = str(selected_student.id)

    accessible_students = _get_accessible_students(current_user)
    child_contacts = []
    if selection['selected_student']:
        child_contacts = _child_contacts_for_student(selection['selected_student'].id)

    preselected_affected_ids = _selected_ids(event.affected_students, 'student_id') if event else set()
    preselected_consequence_ids = _selected_ids(event.selected_consequences, 'consequence_id') if event else set()
    preselected_contact_ids = _selected_ids(event.linked_parent_contacts, 'kontakt_id') if event else set()

    return {
        **selection,
        'schueler_groups': selection['groups'],
        **_pool_context(),
        'child_contacts': child_contacts,
        'affected_student_choices': [s for s in accessible_students if not selection['selected_student'] or s.id != selection['selected_student'].id],
        'affected_student_groups': _group_students_for_multi_select([s for s in accessible_students if not selection['selected_student'] or s.id != selection['selected_student'].id]),
        'preselected_affected_ids': preselected_affected_ids,
        'preselected_consequence_ids': preselected_consequence_ids,
        'preselected_contact_ids': preselected_contact_ids,
    }


@erziehung_bp.route('/erziehung/elternkontakte-fragment')
@login_required
def erziehung_parent_contacts_fragment():
    student_raw = (request.args.get('schueler_id') or '').strip()
    selected_ids = {value for value in request.args.getlist('selected') if value.isdigit()}
    if not student_raw.isdigit():
        return render_template('includes/erziehung_parent_contacts_list.html', child_contacts=[], selected_contact_ids=selected_ids)

    student = _can_access_student(current_user, int(student_raw))
    if not student:
        abort(403)

    return render_template(
        'includes/erziehung_parent_contacts_list.html',
        child_contacts=_child_contacts_for_student(student.id),
        selected_contact_ids=selected_ids,
    )


@erziehung_bp.route('/erziehung')
@login_required
def erziehung_list():
    next_url = (request.args.get('next') or '').strip()
    selection = get_tabbed_student_selection_for_user(
        current_user,
        selected_s_id=(request.args.get('schueler_id') or '').strip(),
        requested_tab=(request.args.get('tab') or '').strip(),
        auto_select_first=True,
        include_archived=True,
    )
    selected_student = selection['selected_student']
    status_filter = (request.args.get('status') or '').strip()

    query = (
        ErziehungsEreignis.query
        .join(Schueler, ErziehungsEreignis.student_id == Schueler.id)
        .order_by(ErziehungsEreignis.datum.desc(), ErziehungsEreignis.id.desc())
    )
    if not _is_admin(current_user):
        query = query.filter(ErziehungsEreignis.student_id.in_(_get_accessible_student_ids(current_user)))
    if selected_student:
        query = query.filter(ErziehungsEreignis.student_id == selected_student.id)
    if status_filter in {'offen', 'abgeschlossen'}:
        query = query.filter(ErziehungsEreignis.status == status_filter)
    events = query.all()

    return render_template(
        'erziehung_list.html',
        events=events,
        status_filter=status_filter,
        next_url=next_url,
        schueler_groups=selection['groups'],
        **selection,
    )


@erziehung_bp.route('/erziehung/neu', methods=['GET', 'POST'])
@login_required
def erziehung_new():
    next_url = (request.args.get('next') or request.form.get('next') or '').strip()
    selected_s_id = (request.values.get('schueler_id') or '').strip()
    selected_student = _can_access_student(current_user, int(selected_s_id)) if selected_s_id.isdigit() else None

    if request.method == 'POST':
        if (request.form.get('form_action') or '').strip() == 'select-student':
            context = _form_page_context(selected_student, active_tab=(request.values.get('tab') or '').strip())
            return render_template(
                'erziehung_form.html',
                event=None,
                next_url=next_url,
                form_action=url_for('erziehung.erziehung_new'),
                today=utc_now().date().isoformat(),
                **context,
            )
        errors = _validate_event_form(selected_student)
        if not errors:
            event = ErziehungsEreignis(created_by_user_id=current_user.id)
            _apply_event_form(event, selected_student)
            db.session.add(event)
            db.session.flush()
            _replace_relations(event, selected_student)
            _store_attachments(event)
            _append_log(event, 'created', _describe_event_creation(event))
            _create_assignment_notification(event, event.assigned_user_id)
            db.session.commit()
            flash('Ereignis gespeichert.')
            return redirect(url_for('erziehung.erziehung_view', event_id=event.id, next=_safe_next_url(next_url, url_for('erziehung.erziehung_list'))))
        for error in errors:
            flash(error)

    context = _form_page_context(selected_student, active_tab=(request.values.get('tab') or '').strip())
    return render_template(
        'erziehung_form.html',
        event=None,
        next_url=next_url,
        form_action=url_for('erziehung.erziehung_new'),
        today=utc_now().date().isoformat(),
        **context,
    )


@erziehung_bp.route('/erziehung/<int:event_id>')
@login_required
def erziehung_view(event_id):
    event = _get_event_or_404(event_id)
    next_url = (request.args.get('next') or '').strip()
    return render_template('erziehung_view.html', event=event, next_url=next_url)


@erziehung_bp.route('/erziehung/<int:event_id>/bearbeiten', methods=['GET', 'POST'])
@login_required
def erziehung_edit(event_id):
    event = _get_event_or_404(event_id)
    next_url = (request.args.get('next') or request.form.get('next') or '').strip()
    selected_student = _can_access_student(current_user, event.student_id)

    if request.method == 'POST':
        selected_s_id = (request.form.get('schueler_id') or '').strip()
        selected_student = _can_access_student(current_user, int(selected_s_id)) if selected_s_id.isdigit() else None
        if (request.form.get('form_action') or '').strip() == 'select-student':
            context = _form_page_context(selected_student, active_tab=(request.values.get('tab') or '').strip(), event=event)
            return render_template(
                'erziehung_form.html',
                event=event,
                next_url=next_url,
                form_action=url_for('erziehung.erziehung_edit', event_id=event.id),
                today=utc_now().date().isoformat(),
                **context,
            )
        errors = _validate_event_form(selected_student)
        if not errors:
            before = _event_snapshot(event)
            _apply_event_form(event, selected_student)
            _replace_relations(event, selected_student)
            _store_attachments(event)
            db.session.flush()
            # Nach dem Ersetzen von Relationstabellen die Sammlungen neu laden,
            # damit das Änderungslog den tatsächlichen neuen Zustand vergleicht.
            db.session.expire(event, ['affected_students', 'selected_consequences', 'linked_parent_contacts', 'attachments'])
            after = _event_snapshot(event)
            changes = _describe_event_changes(before, after)
            if changes['status_changed']:
                _append_log(event, 'status_changed', changes['status_changed'])
            if changes['content_changed']:
                _append_log(event, 'content_changed', changes['content_changed'])
            if changes['links_changed']:
                _append_log(event, 'links_changed', changes['links_changed'])
            if changes['attachments_changed']:
                _append_log(event, 'attachment_added', changes['attachments_changed'])
            if before['assigned_user_id'] != after['assigned_user_id']:
                _create_assignment_notification(event, event.assigned_user_id)
            db.session.commit()
            flash('Ereignis aktualisiert.')
            return redirect(url_for('erziehung.erziehung_view', event_id=event.id, next=_safe_next_url(next_url, url_for('erziehung.erziehung_list'))))
        for error in errors:
            flash(error)

    context = _form_page_context(selected_student, active_tab=(request.values.get('tab') or '').strip(), event=event)
    return render_template(
        'erziehung_form.html',
        event=event,
        next_url=next_url,
        form_action=url_for('erziehung.erziehung_edit', event_id=event.id),
        today=utc_now().date().isoformat(),
        **context,
    )


@erziehung_bp.route('/erziehung/<int:event_id>/anhang/<int:attachment_id>/delete', methods=['POST'])
@login_required
def erziehung_attachment_delete(event_id, attachment_id):
    event = _get_event_or_404(event_id)
    attachment = db.session.get(ErziehungsEreignisAnhang, attachment_id)
    next_url = (request.args.get('next') or request.form.get('next') or '').strip()
    if not attachment or attachment.event_id != event.id:
        flash('Anhang nicht gefunden.')
        return redirect(url_for('erziehung.erziehung_edit', event_id=event.id, next=next_url))

    attachment_name = attachment.original_name or attachment.file_path
    file_path = attachment.file_path
    db.session.delete(attachment)
    _append_log(event, 'attachment_deleted', f'Anhang gelöscht: {attachment_name}')
    db.session.commit()

    loesche_upload_dateien([file_path])
    flash('Anhang gelöscht.')
    return redirect(url_for('erziehung.erziehung_edit', event_id=event.id, next=next_url))


@erziehung_bp.route('/erziehung/<int:event_id>/delete', methods=['POST'])
@login_required
def erziehung_delete(event_id):
    event = _get_event_or_404(event_id)
    next_url = (request.args.get('next') or request.form.get('next') or '').strip()

    # Vor dem Löschen erfassen: danach sind die Datensätze fort, die auf die
    # Dateien zeigen, und die Anhänge wären nicht mehr auffindbar.
    attachment_paths = [
        anhang.file_path for anhang in event.attachments if anhang.file_path
    ]

    # Journal, betroffene Kinder, Konsequenzen, Elternkontakt-Verknüpfungen und
    # Anhänge hängen per delete-orphan-Kaskade am Ereignis. Sie vorher einzeln
    # per Massenlöschung zu entfernen ist nicht nur überflüssig: die Kaskade
    # wollte dieselben Zeilen danach noch einmal löschen und SQLAlchemy warnte
    # über nicht getroffene Zeilen.
    db.session.delete(event)
    db.session.commit()

    loesche_upload_dateien(attachment_paths)
    flash('Ereignis gelöscht.')
    return redirect(_safe_next_url(next_url, url_for('erziehung.erziehung_list')))


def register_erziehung_routes(app):
    app.register_blueprint(erziehung_bp)
