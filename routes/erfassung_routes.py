from datetime import datetime
import hashlib
import json

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, session, url_for
from flask_login import current_user, login_required

from extensions import db
from models import Beobachtung, Bogen, Elternkontakt, Item, Schueler
from odt_export import convert_odt_bytes_to_pdf, render_odt_from_ott_template
from student_selection import (
    get_distinct_klassen,
    get_grouped_student_choices_for_user,
    get_prioritized_students_for_user,
    get_user_klassenkontext,
)
from time_utils import utc_now
from uploads import speichere_upload_bild


erfassung_bp = Blueprint('erfassung', __name__)
ELTERNKONTAKT_PROTOKOLL_TEMPLATE = 'odt_templates/Protokoll_EG.ott'


def _elternkontakt_concurrency_token(kontakt):
    if not kontakt:
        return ''
    payload = {
        'id': kontakt.id,
        'datum': kontakt.datum.isoformat() if kontakt.datum else '',
        'schueler_id': kontakt.schueler_id,
        'user_id': kontakt.user_id,
        'eintrag_typ': kontakt.eintrag_typ or '',
        'kontaktform': kontakt.kontaktform or '',
        'betreff': kontakt.betreff or '',
        'mitteilung': kontakt.mitteilung or '',
        'teilnehmende': kontakt.teilnehmende or '',
        'gespraechsanlass': kontakt.gespraechsanlass or '',
        'besprochenes': kontakt.besprochenes or '',
        'vereinbarungen_schule': kontakt.vereinbarungen_schule or '',
        'vereinbarungen_eltern': kontakt.vereinbarungen_eltern or '',
        'naechste_schritte': kontakt.naechste_schritte or '',
        'naechster_termin': kontakt.naechster_termin.isoformat() if kontakt.naechster_termin else '',
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _elternkontakt_concurrency_conflict(kontakt):
    submitted = (request.form.get('concurrency_token') or '').strip()
    current = _elternkontakt_concurrency_token(kontakt)
    if not submitted:
        return False
    return submitted != current


def _safe_next_url(candidate, fallback_url):
    value = (candidate or '').strip()
    if value.startswith('/') and not value.startswith('//'):
        return value
    return fallback_url


@erfassung_bp.route('/erfassen/reihe/start', methods=['GET', 'POST'])
@login_required
def reihe_start():
    next_url = (request.args.get('next') or request.form.get('next') or '').strip()
    if request.method == 'POST':
        klasse = (request.form.get('klasse') or '').strip()
        item_id = request.form.get('item_id')
        anlass = request.form.get('anlass')
        datum_str = request.form.get('datum')

        if not klasse:
            flash('Bitte eine Klasse auswählen.')
            return redirect(request.url)

        schueler = (
            Schueler.query
            .filter_by(klasse=klasse)
            .order_by(Schueler.nachname, Schueler.vorname)
            .all()
        )
        if not schueler:
            flash(f'Keine Kinder in Klasse {klasse} gefunden.')
            return redirect(request.url)

        schueler_ids = [s.id for s in schueler]

        session['reihe_klasse'] = klasse
        session['reihe_item_id'] = item_id
        session['reihe_anlass'] = anlass
        session['reihe_datum'] = datum_str
        session['reihe_ids'] = schueler_ids
        session['reihe_index'] = 0

        return redirect(url_for('erfassung.reihe_schueler'))

    klassenkontext = get_user_klassenkontext(current_user)
    default_klasse = klassenkontext["klassenleitung"] or ''
    return render_template(
        'reihe_start.html',
        boegen=Bogen.query.all(),
        klassen=get_distinct_klassen(),
        default_klasse=default_klasse,
        next_url=next_url,
    )


@erfassung_bp.route('/erfassen/reihe/schueler')
@login_required
def reihe_schueler():
    if 'reihe_ids' not in session or session['reihe_index'] >= len(session['reihe_ids']):
        flash('Reihenabfrage beendet!')
        return redirect(url_for('system.index'))

    current_student_id = session['reihe_ids'][session['reihe_index']]
    schueler = db.session.get(Schueler, int(current_student_id))
    item = db.session.get(Item, int(session['reihe_item_id']))
    progress = f"{session['reihe_index'] + 1} / {len(session['reihe_ids'])}"

    return render_template(
        'reihe_student.html',
        schueler=schueler,
        item=item,
        anlass=session['reihe_anlass'],
        klasse=session.get('reihe_klasse'),
        progress=progress,
    )


@erfassung_bp.route('/erfassen/reihe/next', methods=['POST'])
@login_required
def reihe_next():
    action = request.form.get('action')

    if action == 'speichern':
        wert = request.form.get('wert')
        kommentar = request.form.get('kommentar')
        foto = request.files.get('foto')
        filename = speichere_upload_bild(foto)

        datum_obj = (
            datetime.strptime(session['reihe_datum'], '%Y-%m-%d')
            if session['reihe_datum']
            else utc_now()
        )

        current_id = session['reihe_ids'][session['reihe_index']]
        entry = Beobachtung(
            schueler_id=current_id,
            item_id=session['reihe_item_id'],
            wert=int(wert) if wert else None,
            kommentar=kommentar,
            foto_pfad=filename,
            anlass=session['reihe_anlass'],
            datum=datum_obj,
        )
        db.session.add(entry)
        db.session.commit()

    session['reihe_index'] += 1
    return redirect(url_for('erfassung.reihe_schueler'))


@erfassung_bp.route('/erfassen/schueler', methods=['GET', 'POST'])
@login_required
def erfassen_schueler():
    s_id = request.args.get('schueler_id')
    b_id = request.args.get('bogen_id')
    next_url = (request.args.get('next') or request.form.get('next') or '').strip()

    if not s_id or not b_id:
        return render_template(
            'batch_schueler.html',
            step=1,
            schueler=get_prioritized_students_for_user(current_user),
            schueler_groups=get_grouped_student_choices_for_user(current_user),
            boegen=Bogen.query.all(),
            next_url=next_url,
        )

    schueler = db.session.get(Schueler, int(s_id))
    items = Item.query.filter_by(bogen_id=b_id).all()

    if request.method == 'POST':
        for i in items:
            wert = request.form.get(f'wert_{i.id}')
            if wert:
                b = Beobachtung(schueler_id=schueler.id, item_id=i.id, wert=int(wert))
                db.session.add(b)
        db.session.commit()
        flash('Bogen gespeichert!')
        return redirect(_safe_next_url(next_url, url_for('system.index')))

    return render_template('batch_schueler.html', step=2, schueler=schueler, items=items, next_url=next_url)


@erfassung_bp.route('/erfassen/einzel', methods=['GET', 'POST'])
@login_required
def erfassen_einzel():
    next_url = (request.args.get('next') or request.form.get('next') or '').strip()
    if request.method == 'POST':
        foto = request.files.get('foto')
        filename = speichere_upload_bild(foto)

        b = Beobachtung(
            schueler_id=request.form.get('schueler_id'),
            item_id=request.form.get('item_id'),
            wert=request.form.get('wert'),
            kommentar=request.form.get('kommentar'),
            foto_pfad=filename,
        )
        db.session.add(b)
        db.session.commit()
        flash('Beobachtung gespeichert!')
        return redirect(_safe_next_url(next_url, url_for('system.index')))

    return render_template(
        'einzel.html',
        schueler=get_prioritized_students_for_user(current_user),
        schueler_groups=get_grouped_student_choices_for_user(current_user),
        boegen=Bogen.query.all(),
        next_url=next_url,
    )


@erfassung_bp.route('/erfassen/multi/start', methods=['GET', 'POST'])
@login_required
def multi_start():
    next_url = (request.args.get('next') or request.form.get('next') or '').strip()
    if request.method == 'POST':
        klasse = (request.form.get('klasse') or '').strip()
        selected_items = request.form.getlist('item_ids')

        if not klasse:
            flash("Bitte eine Klasse auswählen!")
            return redirect(request.url)

        if not selected_items:
            flash("Bitte mindestens eine Kompetenz auswählen!")
            return redirect(request.url)

        anlass = request.form.get('anlass')
        datum_str = request.form.get('datum')

        schueler = (
            Schueler.query
            .filter_by(klasse=klasse)
            .order_by(Schueler.nachname, Schueler.vorname)
            .all()
        )
        if not schueler:
            flash(f'Keine Kinder in Klasse {klasse} gefunden.')
            return redirect(request.url)

        schueler_ids = [s.id for s in schueler]

        session['multi_klasse'] = klasse
        session['multi_item_ids'] = selected_items
        session['multi_anlass'] = anlass
        session['multi_datum'] = datum_str
        session['multi_student_ids'] = schueler_ids
        session['multi_index'] = 0

        return redirect(url_for('erfassung.multi_schueler'))

    klassenkontext = get_user_klassenkontext(current_user)
    default_klasse = klassenkontext["klassenleitung"] or ''
    return render_template(
        'multi_start.html',
        boegen=Bogen.query.all(),
        klassen=get_distinct_klassen(),
        default_klasse=default_klasse,
        next_url=next_url,
    )


@erfassung_bp.route('/erfassen/multi/schueler')
@login_required
def multi_schueler():
    if 'multi_student_ids' not in session or session['multi_index'] >= len(session['multi_student_ids']):
        flash('Multi-Erfassung abgeschlossen!')
        return redirect(url_for('system.index'))

    s_id = session['multi_student_ids'][session['multi_index']]
    schueler = db.session.get(Schueler, int(s_id))
    item_ids = session['multi_item_ids']
    items = Item.query.filter(Item.id.in_(item_ids)).all()
    progress = f"{session['multi_index'] + 1} / {len(session['multi_student_ids'])}"

    return render_template(
        'multi_student.html',
        schueler=schueler,
        items=items,
        anlass=session['multi_anlass'],
        klasse=session.get('multi_klasse'),
        progress=progress,
    )


@erfassung_bp.route('/erfassen/multi/next', methods=['POST'])
@login_required
def multi_next():
    action = request.form.get('action')

    if action == 'speichern':
        item_ids = session['multi_item_ids']
        s_id = session['multi_student_ids'][session['multi_index']]
        datum_obj = (
            datetime.strptime(session['multi_datum'], '%Y-%m-%d')
            if session['multi_datum']
            else utc_now()
        )

        for i_id in item_ids:
            wert = request.form.get(f'wert_{i_id}')
            kommentar = request.form.get(f'kommentar_{i_id}')
            foto = request.files.get(f'foto_{i_id}')
            filename = speichere_upload_bild(foto)

            if wert:
                entry = Beobachtung(
                    schueler_id=s_id,
                    item_id=i_id,
                    wert=int(wert),
                    kommentar=kommentar,
                    foto_pfad=filename,
                    anlass=session['multi_anlass'],
                    datum=datum_obj,
                )
                db.session.add(entry)

        db.session.commit()

    session['multi_index'] += 1
    return redirect(url_for('erfassung.multi_schueler'))


@erfassung_bp.route('/erfassen/elternkontakte')
@login_required
def elternkontakte_start():
    selected_s_id = (request.args.get('schueler_id') or '').strip()
    schueler = get_prioritized_students_for_user(current_user)
    recent_contacts_query = Elternkontakt.query.join(Schueler).order_by(Elternkontakt.datum.desc())
    if selected_s_id:
        try:
            recent_contacts_query = recent_contacts_query.filter(Elternkontakt.schueler_id == int(selected_s_id))
        except ValueError:
            selected_s_id = ''
    recent_contacts = recent_contacts_query.limit(24).all()
    return render_template(
        'elternkontakte_start.html',
        schueler=schueler,
        schueler_groups=get_grouped_student_choices_for_user(current_user),
        recent_contacts=recent_contacts,
        selected_s_id=selected_s_id,
    )


def _parse_optional_datetime(date_str):
    if not date_str:
        return utc_now()
    try:
        return datetime.strptime(date_str, '%Y-%m-%d')
    except ValueError:
        return utc_now()


def _parse_optional_date(date_str):
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return None


def _format_german_date(value):
    if not value:
        return ''
    return value.strftime('%d.%m.%Y')


def _format_html_date(value):
    if not value:
        return ''
    if hasattr(value, 'date'):
        return value.strftime('%Y-%m-%d')
    return value.strftime('%Y-%m-%d')


def _elternkontakt_form_values_from_obj(kontakt):
    return {
        'schueler_id': str(kontakt.schueler_id or ''),
        'datum': _format_html_date(kontakt.datum),
        'kontaktform': kontakt.kontaktform or '',
        'betreff': kontakt.betreff or '',
        'mitteilung': kontakt.mitteilung or '',
        'teilnehmende': kontakt.teilnehmende or '',
        'gespraechsanlass': kontakt.gespraechsanlass or '',
        'besprochenes': kontakt.besprochenes or '',
        'vereinbarungen_schule': kontakt.vereinbarungen_schule or '',
        'vereinbarungen_eltern': kontakt.vereinbarungen_eltern or '',
        'naechste_schritte': kontakt.naechste_schritte or '',
        'naechster_termin': _format_html_date(kontakt.naechster_termin),
        'kurzfassung': kontakt.mitteilung or '',
    }


def _can_edit_elternkontakt(kontakt):
    # Protokolle nur Ersteller oder Admin bearbeitbar; Notizen koennen von allen eingeloggt bearbeitet werden.
    if kontakt.eintrag_typ != 'protokoll':
        return True
    if current_user.username == 'admin':
        return True
    return bool(kontakt.user_id and kontakt.user_id == current_user.id)


def _build_elternkontakt_protokoll_export_payload(kontakt):
    schueler = kontakt.schueler
    if not schueler:
        raise ValueError('Zum Protokoll ist kein Kind zugeordnet.')

    placeholder_map = {
        "Kind": f"{schueler.vorname or ''} {schueler.nachname or ''}".strip() or f"{schueler.nachname}, {schueler.vorname}",
        "Datum": _format_german_date(kontakt.datum),
        "Teilnehmende": kontakt.teilnehmende or "",
        "Gesprächsanlass": kontakt.gespraechsanlass or "",
        "Besprochenes": kontakt.besprochenes or "",
        "Vereinbarung Schule": kontakt.vereinbarungen_schule or "",
        "Vereinbarung Eltern": kontakt.vereinbarungen_eltern or "",
        "Nächste Schritte": kontakt.naechste_schritte or "",
    }

    odt_buffer = render_odt_from_ott_template(ELTERNKONTAKT_PROTOKOLL_TEMPLATE, placeholder_map)
    datum_part = _format_german_date(kontakt.datum).replace('.', '-')
    safe_nachname = (schueler.nachname or 'kind').replace(' ', '_')
    filename_stem = f"Elterngespraech_{safe_nachname}_{datum_part or 'protokoll'}"
    return odt_buffer, filename_stem


@erfassung_bp.route('/erfassen/elternkontakte/notiz', methods=['GET', 'POST'])
@login_required
def elternkontakt_notiz():
    schueler_liste = get_prioritized_students_for_user(current_user)
    next_url = (request.args.get('next') or request.form.get('next') or '').strip()

    if request.method == 'POST':
        schueler_id = request.form.get('schueler_id')
        kontaktform = request.form.get('kontaktform')
        betreff = request.form.get('betreff')
        mitteilung = request.form.get('mitteilung')
        datum_obj = _parse_optional_datetime(request.form.get('datum'))

        if not schueler_id or not kontaktform or not mitteilung:
            flash('Bitte Schüler, Kontaktform und Mitteilung ausfüllen.')
            return render_template(
                'elternkontakt_notiz.html',
                schueler=schueler_liste,
                schueler_groups=get_grouped_student_choices_for_user(current_user),
                selected_s_id=schueler_id,
                next_url=next_url,
            )

        kontakt = Elternkontakt(
            schueler_id=int(schueler_id),
            user_id=current_user.id,
            eintrag_typ='notiz',
            kontaktform=kontaktform,
            betreff=betreff,
            mitteilung=mitteilung,
            datum=datum_obj,
        )
        db.session.add(kontakt)
        db.session.commit()
        flash('Elternkontakt-Notiz gespeichert.')
        return redirect(_safe_next_url(next_url, url_for('erfassung.elternkontakte_start')))

    return render_template(
        'elternkontakt_notiz.html',
        schueler=schueler_liste,
        schueler_groups=get_grouped_student_choices_for_user(current_user),
        selected_s_id=request.args.get('schueler_id'),
        next_url=next_url,
    )


@erfassung_bp.route('/erfassen/elternkontakte/protokoll', methods=['GET', 'POST'])
@login_required
def elternkontakt_protokoll():
    schueler_liste = get_prioritized_students_for_user(current_user)
    next_url = (request.args.get('next') or request.form.get('next') or '').strip()

    if request.method == 'POST':
        schueler_id = request.form.get('schueler_id')
        datum_obj = _parse_optional_datetime(request.form.get('datum'))

        if not schueler_id:
            flash('Bitte ein Kind auswählen.')
            return render_template(
                'elternkontakt_protokoll.html',
                schueler=schueler_liste,
                schueler_groups=get_grouped_student_choices_for_user(current_user),
                selected_s_id=schueler_id,
                next_url=next_url,
            )

        kontakt = Elternkontakt(
            schueler_id=int(schueler_id),
            user_id=current_user.id,
            eintrag_typ='protokoll',
            kontaktform=request.form.get('kontaktform') or 'Elterngespräch',
            betreff=request.form.get('betreff'),
            mitteilung=request.form.get('kurzfassung'),
            teilnehmende=request.form.get('teilnehmende'),
            gespraechsanlass=request.form.get('gespraechsanlass'),
            besprochenes=request.form.get('besprochenes'),
            vereinbarungen_schule=request.form.get('vereinbarungen_schule'),
            vereinbarungen_eltern=request.form.get('vereinbarungen_eltern'),
            naechste_schritte=request.form.get('naechste_schritte'),
            naechster_termin=_parse_optional_date(request.form.get('naechster_termin')),
            datum=datum_obj,
        )
        db.session.add(kontakt)
        db.session.commit()
        flash('Elterngesprächsprotokoll gespeichert.')
        return redirect(_safe_next_url(next_url, url_for('erfassung.elternkontakte_start')))

    return render_template(
        'elternkontakt_protokoll.html',
        schueler=schueler_liste,
        schueler_groups=get_grouped_student_choices_for_user(current_user),
        selected_s_id=request.args.get('schueler_id'),
        next_url=next_url,
    )


@erfassung_bp.route('/erfassen/elternkontakte/view/<int:kontakt_id>')
@login_required
def elternkontakt_view(kontakt_id):
    kontakt = db.session.get(Elternkontakt, kontakt_id)
    if not kontakt:
        abort(404)
    return render_template(
        'elternkontakt_view.html',
        kontakt=kontakt,
        can_edit=_can_edit_elternkontakt(kontakt),
        next_url=(request.args.get('next') or '').strip(),
    )


@erfassung_bp.route('/erfassen/elternkontakte/edit/<int:kontakt_id>', methods=['GET', 'POST'])
@login_required
def elternkontakt_edit(kontakt_id):
    kontakt = db.session.get(Elternkontakt, kontakt_id)
    if not kontakt:
        abort(404)
    next_url = (request.args.get('next') or request.form.get('next') or '').strip()

    if not _can_edit_elternkontakt(kontakt):
        flash('Dieses Protokoll kann nur von der erstellenden Lehrkraft bearbeitet werden.')
        return redirect(url_for('erfassung.elternkontakt_view', kontakt_id=kontakt.id, next=next_url) if next_url else url_for('erfassung.elternkontakt_view', kontakt_id=kontakt.id))

    schueler_liste = get_prioritized_students_for_user(current_user)

    if request.method == 'POST':
        if _elternkontakt_concurrency_conflict(kontakt):
            flash('Dieser Elternkontakt wurde zwischenzeitlich geändert. Bitte die Bearbeitungsseite neu öffnen und Änderungen prüfen.')
            return redirect(url_for('erfassung.elternkontakt_edit', kontakt_id=kontakt.id, next=next_url) if next_url else url_for('erfassung.elternkontakt_edit', kontakt_id=kontakt.id))
        original_concurrency_token = _elternkontakt_concurrency_token(kontakt)
        schueler_id = request.form.get('schueler_id')
        if not schueler_id:
            flash('Bitte ein Kind auswählen.')
            template = 'elternkontakt_protokoll.html' if kontakt.eintrag_typ == 'protokoll' else 'elternkontakt_notiz.html'
            return render_template(
                template,
                schueler=schueler_liste,
                schueler_groups=get_grouped_student_choices_for_user(current_user),
                selected_s_id=schueler_id,
                form_values=request.form,
                is_edit_mode=True,
                kontakt=kontakt,
                concurrency_token=original_concurrency_token,
                next_url=next_url,
            )

        kontakt.schueler_id = int(schueler_id)
        kontakt.datum = _parse_optional_datetime(request.form.get('datum'))
        kontakt.kontaktform = request.form.get('kontaktform')
        kontakt.betreff = request.form.get('betreff')

        if kontakt.eintrag_typ == 'notiz':
            kontakt.mitteilung = request.form.get('mitteilung')
            if not kontakt.kontaktform or not kontakt.mitteilung:
                flash('Bitte Kontaktform und Mitteilung ausfüllen.')
                return render_template(
                    'elternkontakt_notiz.html',
                    schueler=schueler_liste,
                    schueler_groups=get_grouped_student_choices_for_user(current_user),
                    selected_s_id=schueler_id,
                    form_values=request.form,
                    is_edit_mode=True,
                    kontakt=kontakt,
                    concurrency_token=original_concurrency_token,
                    next_url=next_url,
                )
        else:
            kontakt.mitteilung = request.form.get('kurzfassung')
            kontakt.teilnehmende = request.form.get('teilnehmende')
            kontakt.gespraechsanlass = request.form.get('gespraechsanlass')
            kontakt.besprochenes = request.form.get('besprochenes')
            kontakt.vereinbarungen_schule = request.form.get('vereinbarungen_schule')
            kontakt.vereinbarungen_eltern = request.form.get('vereinbarungen_eltern')
            kontakt.naechste_schritte = request.form.get('naechste_schritte')
            kontakt.naechster_termin = _parse_optional_date(request.form.get('naechster_termin'))

        db.session.commit()
        flash('Elternkontakt aktualisiert.')
        return redirect(_safe_next_url(next_url, url_for('erfassung.elternkontakt_view', kontakt_id=kontakt.id)))

    template = 'elternkontakt_protokoll.html' if kontakt.eintrag_typ == 'protokoll' else 'elternkontakt_notiz.html'
    return render_template(
        template,
        schueler=schueler_liste,
        schueler_groups=get_grouped_student_choices_for_user(current_user),
        selected_s_id=str(kontakt.schueler_id),
        form_values=_elternkontakt_form_values_from_obj(kontakt),
        is_edit_mode=True,
        kontakt=kontakt,
        concurrency_token=_elternkontakt_concurrency_token(kontakt),
        next_url=next_url,
    )


@erfassung_bp.route('/erfassen/elternkontakte/delete/<int:kontakt_id>', methods=['POST'])
@login_required
def elternkontakt_delete(kontakt_id):
    kontakt = db.session.get(Elternkontakt, kontakt_id)
    if not kontakt:
        abort(404)

    next_url = (request.form.get('next') or request.args.get('next') or '').strip()
    if not _can_edit_elternkontakt(kontakt):
        flash('Dieser Elternkontakt kann nicht gelöscht werden.')
        return redirect(url_for('erfassung.elternkontakt_view', kontakt_id=kontakt.id, next=next_url) if next_url else url_for('erfassung.elternkontakt_view', kontakt_id=kontakt.id))

    label = 'Protokoll' if kontakt.eintrag_typ == 'protokoll' else 'Notiz'
    db.session.delete(kontakt)
    db.session.commit()
    flash(f'{label} gelöscht.')
    return redirect(_safe_next_url(next_url, url_for('erfassung.elternkontakte_start')))


@erfassung_bp.route('/erfassen/elternkontakte/protokoll/export/odt/<int:kontakt_id>')
@login_required
def elternkontakt_protokoll_export_odt(kontakt_id):
    kontakt = db.session.get(Elternkontakt, kontakt_id)
    if not kontakt:
        abort(404)
    next_url = (request.args.get('next') or '').strip()
    if kontakt.eintrag_typ != 'protokoll':
        flash('ODT-Export ist nur für Protokolle verfügbar.')
        return redirect(_safe_next_url(next_url, url_for('erfassung.elternkontakte_start')))

    try:
        odt_buffer, filename_stem = _build_elternkontakt_protokoll_export_payload(kontakt)
    except FileNotFoundError:
        flash('Die ODT-Vorlage für Elterngesprächsprotokolle wurde nicht gefunden.')
        return redirect(_safe_next_url(next_url, url_for('erfassung.elternkontakte_start')))
    except ValueError as exc:
        flash(str(exc))
        return redirect(_safe_next_url(next_url, url_for('erfassung.elternkontakte_start')))

    return send_file(
        odt_buffer,
        as_attachment=True,
        download_name=f'{filename_stem}.odt',
        mimetype='application/vnd.oasis.opendocument.text',
    )


@erfassung_bp.route('/erfassen/elternkontakte/protokoll/export/pdf/<int:kontakt_id>')
@login_required
def elternkontakt_protokoll_export_pdf(kontakt_id):
    kontakt = db.session.get(Elternkontakt, kontakt_id)
    if not kontakt:
        abort(404)
    next_url = (request.args.get('next') or '').strip()
    if kontakt.eintrag_typ != 'protokoll':
        flash('PDF-Export ist nur für Protokolle verfügbar.')
        return redirect(_safe_next_url(next_url, url_for('erfassung.elternkontakte_start')))

    try:
        odt_buffer, filename_stem = _build_elternkontakt_protokoll_export_payload(kontakt)
        pdf_buffer = convert_odt_bytes_to_pdf(odt_buffer)
    except FileNotFoundError:
        flash('Die ODT-Vorlage für Elterngesprächsprotokolle wurde nicht gefunden.')
        return redirect(_safe_next_url(next_url, url_for('erfassung.elternkontakte_start')))
    except ValueError as exc:
        flash(str(exc))
        return redirect(_safe_next_url(next_url, url_for('erfassung.elternkontakte_start')))
    except RuntimeError as exc:
        flash(f'PDF-Export fehlgeschlagen: {exc}')
        return redirect(_safe_next_url(next_url, url_for('erfassung.elternkontakte_start')))

    return send_file(
        pdf_buffer,
        as_attachment=True,
        download_name=f'{filename_stem}.pdf',
        mimetype='application/pdf',
    )


def register_erfassung_routes(app):
    app.register_blueprint(erfassung_bp)
