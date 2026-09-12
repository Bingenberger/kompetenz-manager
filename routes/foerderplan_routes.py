from datetime import datetime, timedelta
import hashlib
import json

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from db_utils import get_or_404_session
from change_log import describe, describe_creation, snapshot
from extensions import db
from models import (
    Beobachtung,
    Bogen,
    Foerdergrundlage,
    Foerderinhalt,
    Foerderplan,
    FoerderplanLog,
    Item,
    Schueler,
    SystemKonfiguration,
)
from odt_export import (
    convert_odt_bytes_to_pdf,
    merge_odt_documents,
    render_odt_from_ott_template,
    render_odt_from_ott_template_with_repeat_blocks,
)
from student_selection import (
    get_grouped_student_choices_for_user,
    get_prioritized_students_for_user,
    get_tabbed_student_selection_for_user,
    get_user_klassenkontext,
)
from school_year import observation_period_start
from time_utils import utc_now


foerderplan_bp = Blueprint('foerderplan', __name__)

# Beobachtete Felder des Plankopfs. Die Foerderinhalte werden beim Speichern
# ersetzt statt geaendert - fuer sie wird gezaehlt, nicht verglichen.
PLAN_FELDER = (
    ('titel', 'Titel'),
    ('status', 'Status'),
    ('datum_evaluation', 'Evaluationsdatum'),
)


def _plan_log(plan, action, details):
    """Schreibt einen Journaleintrag, wenn es etwas zu berichten gibt."""
    if not details:
        return
    db.session.add(FoerderplanLog(
        plan_id=plan.id,
        user_id=getattr(current_user, 'id', None),
        action=action,
        details=details,
    ))


def _inhalte_beschreibung(plan):
    # Direkt abfragen statt ueber plan.inhalte: beim Speichern werden die
    # Inhalte per Massenloeschung ersetzt, die geladene Beziehung waere danach
    # veraltet und die Aenderung bliebe unbemerkt.
    ziele = [
        (foerderziel or '').strip()
        for (foerderziel,) in db.session.query(Foerderinhalt.foerderziel)
        .filter(Foerderinhalt.plan_id == plan.id)
        .order_by(Foerderinhalt.id.asc())
        .all()
        if (foerderziel or '').strip()
    ]
    if not ziele:
        return 'keine Förderbereiche'
    return f"{len(ziele)} Förderbereich(e): " + '; '.join(ziele)
FOERDERPLAN_TEMPLATE = 'odt_templates/Foerderplan-Vorlage.ott'
FOERDERGRUNDLAGE_TEMPLATE = 'odt_templates/Deckblatt_Foerderplan.ott'


def _teacher_can_access_student(user, student):
    if not user or not student:
        return False
    if user.is_admin or not student.is_active:
        return True

    kontext = get_user_klassenkontext(user)
    klassenleitung = kontext.get('klassenleitung')
    fachklassen = kontext.get('fachklassen') or set()
    if not student.klasse:
        return False
    return student.klasse == klassenleitung or student.klasse in fachklassen


def _ensure_student_access_or_403(student_id):
    student = get_or_404_session(Schueler, int(student_id))
    if not _teacher_can_access_student(current_user, student):
        abort(403)
    return student


def _foerderplan_query_for_user(user):
    query = Foerderplan.query
    if user.is_admin:
        return query
    accessible_student_ids = [
        student.id
        for student in get_prioritized_students_for_user(user, include_archived=True)
        if _teacher_can_access_student(user, student)
    ]
    if not accessible_student_ids:
        return query.filter(Foerderplan.id.is_(None))
    return query.filter(Foerderplan.schueler_id.in_(accessible_student_ids))


def _ensure_plan_access_or_404(plan_id):
    plan = get_or_404_session(Foerderplan, plan_id)
    if current_user.is_admin:
        return plan
    if not _teacher_can_access_student(current_user, plan.schueler):
        abort(404)
    return plan


def _can_manage_foerderplan(plan):
    return bool(plan and _teacher_can_access_student(current_user, plan.schueler))


def _format_german_date(value):
    if not value:
        return ''
    if isinstance(value, datetime):
        return value.strftime('%d.%m.%Y')
    return value.strftime('%d.%m.%Y')


def _foerdergrundlage_form_values_from_obj(grundlage):
    if not grundlage:
        return {
            'besondere_staerken': '',
            'vorrangiger_foerderbedarf': '',
            'besonderheiten_entwicklung': '',
            'wichtige_informationen': '',
            'absprachen_mit_eltern': '',
        }
    return {
        'besondere_staerken': grundlage.besondere_staerken or '',
        'vorrangiger_foerderbedarf': grundlage.vorrangiger_foerderbedarf or '',
        'besonderheiten_entwicklung': grundlage.besonderheiten_entwicklung or '',
        'wichtige_informationen': grundlage.wichtige_informationen or '',
        'absprachen_mit_eltern': grundlage.absprachen_mit_eltern or '',
    }


def _safe_next_url(candidate, fallback_url):
    value = (candidate or '').strip()
    if value.startswith('/') and not value.startswith('//'):
        return value
    return fallback_url


def _foerderplan_concurrency_token(plan):
    if not plan:
        return ''
    payload = {
        'plan': {
            'id': plan.id,
            'titel': plan.titel or '',
            'status': plan.status or '',
            'datum_erstellung': plan.datum_erstellung.isoformat() if plan.datum_erstellung else '',
            'datum_evaluation': plan.datum_evaluation.isoformat() if plan.datum_evaluation else '',
        },
        'inhalte': [
            {
                'id': inhalt.id,
                'foerderziel': inhalt.foerderziel or '',
                'ist_zustand': inhalt.ist_zustand or '',
                'soll_zustand': inhalt.soll_zustand or '',
                'massnahmen': inhalt.massnahmen or '',
                'evaluation_text': inhalt.evaluation_text or '',
                'status_id': inhalt.status_id,
            }
            for inhalt in sorted(plan.inhalte, key=lambda x: (x.id or 0))
        ],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _foerderplan_concurrency_conflict(plan):
    submitted = (request.form.get('concurrency_token') or '').strip()
    current = _foerderplan_concurrency_token(plan)
    if not submitted:
        return False
    return submitted != current


def _build_foerderplan_export_payload(plan, include_grundlagenblatt=False):
    schueler = plan.schueler
    if not schueler:
        raise ValueError('Zum Förderplan ist kein Kind zugeordnet.')
    settings = SystemKonfiguration.query.order_by(SystemKonfiguration.id.asc()).first()

    repeat_rows = []
    for inhalt in plan.inhalte:
        repeat_rows.append({
            'Foerderziel': inhalt.foerderziel or '',
            'Ist-Zustand': inhalt.ist_zustand or '',
            'Soll-Zustand': inhalt.soll_zustand or '',
            'Maßnahmen': inhalt.massnahmen or '',
        })

    if not repeat_rows:
        repeat_rows.append({
            'Foerderziel': '',
            'Ist-Zustand': '',
            'Soll-Zustand': '',
            'Maßnahmen': '',
        })

    placeholder_map = {
        'Name': f'{schueler.vorname or ""} {schueler.nachname or ""}'.strip(),
        'Klasse': schueler.klasse or '',
        'Geburtsdatum': _format_german_date(schueler.geburtsdatum),
        'Schuljahr': (settings.schuljahr if settings else '') or '',
        'Ersteller': (getattr(plan.creator, 'display_name', None) if getattr(plan, 'creator', None) else None)
        or getattr(current_user, 'display_name', None)
        or current_user.username,
        'Erstellt am': _format_german_date(plan.datum_erstellung),
    }

    plan_odt_buffer = render_odt_from_ott_template_with_repeat_blocks(
        FOERDERPLAN_TEMPLATE,
        placeholder_map=placeholder_map,
        repeat_blocks={'FOERDERINHALTE': repeat_rows},
    )
    datum_part = _format_german_date(plan.datum_erstellung).replace('.', '-')
    safe_nachname = (schueler.nachname or 'kind').replace(' ', '_')
    if include_grundlagenblatt:
        grundlage = getattr(schueler, 'foerdergrundlage', None)
        grundlage_odt_buffer, _ = _build_foerdergrundlage_export_payload(schueler, grundlage)
        odt_buffer = merge_odt_documents(grundlage_odt_buffer, plan_odt_buffer)
        filename_stem = f'Foerderplan_mit_Grundlagenblatt_{safe_nachname}_{datum_part or "plan"}'
        return odt_buffer, filename_stem

    filename_stem = f'Foerderplan_{safe_nachname}_{datum_part or "plan"}'
    return plan_odt_buffer, filename_stem


def _build_foerdergrundlage_export_payload(schueler, grundlage):
    if not schueler:
        raise ValueError('Zum Grundlagenblatt ist kein Kind zugeordnet.')
    if not grundlage:
        raise ValueError('Für dieses Kind ist noch kein Grundlagenblatt vorhanden.')

    settings = SystemKonfiguration.query.order_by(SystemKonfiguration.id.asc()).first()
    export_datum = utc_now().date()
    placeholder_map = {
        'Schüler': f'{schueler.vorname or ""} {schueler.nachname or ""}'.strip(),
        'Geburtsdatum': _format_german_date(schueler.geburtsdatum),
        'Schuljahr': (settings.schuljahr if settings else '') or '',
        'Ersteller': getattr(current_user, 'display_name', None) or current_user.username,
        'Klasse': schueler.klasse or '',
        'Erstellungsdatum': _format_german_date(export_datum),
        'Staerken': grundlage.besondere_staerken or '',
        # Vorlage enthält aktuell den Platzhalter mit Tippfehler "...bedar" (ohne f).
        'vorrangiger_Foerderbedar': grundlage.vorrangiger_foerderbedarf or '',
        'Entwicklung': grundlage.besonderheiten_entwicklung or '',
        'medizinische_Auffaelligkeiten': grundlage.wichtige_informationen or '',
        'Zusammenarbeit': grundlage.absprachen_mit_eltern or '',
    }

    odt_buffer = render_odt_from_ott_template(FOERDERGRUNDLAGE_TEMPLATE, placeholder_map)
    safe_nachname = (schueler.nachname or 'kind').replace(' ', '_')
    filename_stem = f'Grundlagenblatt_{safe_nachname}_{export_datum.strftime("%Y-%m-%d")}'
    return odt_buffer, filename_stem


@foerderplan_bp.route('/foerderplan/neu/<int:s_id>', methods=['GET', 'POST'])
@login_required
def foerderplan_neu(s_id):
    schueler = _ensure_student_access_or_403(s_id)
    next_url = (request.args.get('next') or request.form.get('next') or '').strip()
    if not schueler.foerdergrundlage:
        flash('Bitte zuerst das Grundlagenblatt für dieses Kind ausfüllen.')
        return redirect(url_for('foerderplan.foerdergrundlage_edit', s_id=schueler.id, next=url_for('foerderplan.foerderplan_neu', s_id=schueler.id, next=next_url) if next_url else url_for('foerderplan.foerderplan_neu', s_id=schueler.id)))

    aktiver_plan = (
        Foerderplan.query
        .filter(
            Foerderplan.schueler_id == schueler.id,
            Foerderplan.status == 'aktiv',
        )
        .order_by(Foerderplan.datum_erstellung.desc(), Foerderplan.id.desc())
        .first()
    )
    if aktiver_plan:
        flash(
            'Für dieses Kind existiert bereits ein aktiver Förderplan. '
            'Bitte zuerst den bestehenden Plan evaluieren und schließen.'
        )
        return redirect(url_for('foerderplan.foerderplan_evaluate', p_id=aktiver_plan.id))

    if request.method == 'POST':
        titel = request.form.get('titel')

        neuer_plan = Foerderplan(
            schueler_id=schueler.id,
            creator_user_id=current_user.id,
            titel=titel,
            datum_erstellung=utc_now(),
            status='aktiv'
        )
        db.session.add(neuer_plan)
        db.session.flush()

        ziele = request.form.getlist('foerderziel')
        ist_zustaende = request.form.getlist('ist_zustand')
        soll_zustaende = request.form.getlist('soll_zustand')
        massnahmen = request.form.getlist('massnahmen')

        for i in range(len(ziele)):
            if ziele[i].strip():
                inhalt = Foerderinhalt(
                    plan_id=neuer_plan.id,
                    foerderziel=ziele[i],
                    ist_zustand=ist_zustaende[i],
                    soll_zustand=soll_zustaende[i],
                    massnahmen=massnahmen[i],
                    status_id=0
                )
                db.session.add(inhalt)

        db.session.flush()
        _plan_log(neuer_plan, 'created', '; '.join(filter(None, [
            describe_creation(neuer_plan, PLAN_FELDER),
            _inhalte_beschreibung(neuer_plan),
        ])))
        db.session.commit()
        flash(f'Förderplan "{titel}" erfolgreich angelegt!')
        return redirect(_safe_next_url(next_url, url_for('foerderplan.foerderplan_view', p_id=neuer_plan.id)))

    stichtag = observation_period_start(utc_now() - timedelta(days=120))
    beobachtungen = Beobachtung.query.filter(
        Beobachtung.schueler_id == s_id,
        Beobachtung.datum >= stichtag
    ).order_by(Beobachtung.datum.desc()).all()

    unique_obs = {}
    obs_by_item = {}
    for obs in beobachtungen:
        obs_by_item.setdefault(obs.item_id, []).append(obs)
        if obs.item_id not in unique_obs:
            unique_obs[obs.item_id] = obs

    vorschlaege = []
    existing_titles = {}

    letzter_eval_plan = (
        Foerderplan.query
        .filter(
            Foerderplan.schueler_id == s_id,
            Foerderplan.datum_evaluation.isnot(None),
        )
        .order_by(Foerderplan.datum_evaluation.desc(), Foerderplan.datum_erstellung.desc())
        .first()
    )
    if letzter_eval_plan:
        for inhalt in letzter_eval_plan.inhalte:
            if inhalt.status_id == 2:
                altes_ziel = {
                    'bereich': inhalt.foerderziel or '',
                    'soll': inhalt.soll_zustand or '',
                    'ist': f"Alter Ist-Zustand:\n{inhalt.ist_zustand or ''}",
                    'massnahme': f"Alte Maßnahmen:\n{inhalt.massnahmen or ''}",
                    'source_label': 'Übernahme aus letztem evaluierten Förderplan',
                }

                vorschlaege.append(altes_ziel)
                existing_titles[inhalt.foerderziel] = len(vorschlaege) - 1

    noten_text = {1: "reicht noch nicht aus (-)", 2: "ist wechselhaft (o)", 3: "gut", 4: "sehr gut"}

    for item_id, obs in unique_obs.items():
        item_observations = [entry for entry in obs_by_item.get(item_id, []) if entry.wert is not None]
        if not item_observations:
            continue

        avg_rating = sum(entry.wert for entry in item_observations) / len(item_observations)
        if avg_rating < 1.2:
            item = db.session.get(Item, int(item_id))
            if not item or not item.bogen:
                continue

            ist_text = (
                f"Beobachtung vom {obs.datum.strftime('%d.%m.')}: "
                f"Die Leistung {noten_text.get(obs.wert, '')}. Durchschnitt im Zeitraum: {avg_rating:.1f}."
            )
            if obs.kommentar:
                ist_text += f" Anmerkung: {obs.kommentar}"

            titel_generated = f"{item.bogen.titel} | {item.bereich} | {item.text}"

            if titel_generated in existing_titles:
                idx = existing_titles[titel_generated]
                vorschlaege[idx]['ist'] += f"\n\n--- Aktuelle Beobachtung ---\n{ist_text}"
            else:
                vorschlaege.append({
                    'bereich': titel_generated,
                    'soll': '',
                    'ist': ist_text,
                    'massnahme': '',
                    'source_label': 'Vorschlag aus Beobachtung',
                })

    all_boegen = Bogen.query.all()

    return render_template(
        'foerderplan_wizard.html',
        schueler=schueler,
        grundlage=schueler.foerdergrundlage,
        vorschlaege=vorschlaege,
        all_boegen=all_boegen,
        now=utc_now(),
        is_edit_mode=False,
        next_url=next_url,
    )


@foerderplan_bp.route('/foerderplan/grundlagen/<int:s_id>', methods=['GET', 'POST'])
@login_required
def foerdergrundlage_edit(s_id):
    schueler = _ensure_student_access_or_403(s_id)
    grundlage = schueler.foerdergrundlage or Foerdergrundlage(schueler_id=schueler.id)
    is_new = grundlage.id is None
    next_url = (request.args.get('next') or request.form.get('next') or '').strip()

    if request.method == 'POST':
        grundlage.besondere_staerken = (request.form.get('besondere_staerken') or '').strip() or None
        grundlage.vorrangiger_foerderbedarf = (request.form.get('vorrangiger_foerderbedarf') or '').strip() or None
        grundlage.besonderheiten_entwicklung = (request.form.get('besonderheiten_entwicklung') or '').strip() or None
        grundlage.wichtige_informationen = (request.form.get('wichtige_informationen') or '').strip() or None
        grundlage.absprachen_mit_eltern = (request.form.get('absprachen_mit_eltern') or '').strip() or None

        if is_new and not (
            grundlage.besondere_staerken
            or grundlage.vorrangiger_foerderbedarf
            or grundlage.besonderheiten_entwicklung
            or grundlage.wichtige_informationen
            or grundlage.absprachen_mit_eltern
        ):
            flash('Bitte mindestens eine Angabe im Grundlagenblatt erfassen.')
            return render_template(
                'foerdergrundlage_form.html',
                schueler=schueler,
                grundlage=grundlage,
                form_values=_foerdergrundlage_form_values_from_obj(grundlage),
                is_edit_mode=not is_new,
                next_url=next_url,
            )

        if is_new:
            db.session.add(grundlage)
        db.session.commit()
        flash('Grundlagenblatt gespeichert.')

        if next_url and next_url.startswith('/'):
            return redirect(next_url)
        return redirect(url_for('foerderplan.foerderplan_list', schueler_id=schueler.id))

    return render_template(
        'foerdergrundlage_form.html',
        schueler=schueler,
        grundlage=grundlage if not is_new else None,
        form_values=_foerdergrundlage_form_values_from_obj(grundlage),
        is_edit_mode=not is_new,
        next_url=next_url,
    )


@foerderplan_bp.route('/foerderplan/grundlagen/export/odt/<int:s_id>')
@login_required
def foerdergrundlage_export_odt(s_id):
    schueler = _ensure_student_access_or_403(s_id)

    try:
        odt_buffer, filename_stem = _build_foerdergrundlage_export_payload(
            schueler,
            getattr(schueler, 'foerdergrundlage', None),
        )
    except FileNotFoundError:
        flash('Die ODT-Vorlage für das Grundlagenblatt wurde nicht gefunden.')
        return redirect(url_for('foerderplan.foerdergrundlage_edit', s_id=s_id))
    except ValueError as exc:
        flash(str(exc))
        return redirect(url_for('foerderplan.foerdergrundlage_edit', s_id=s_id))

    return send_file(
        odt_buffer,
        as_attachment=True,
        download_name=f'{filename_stem}.odt',
        mimetype='application/vnd.oasis.opendocument.text',
    )


@foerderplan_bp.route('/foerderplan/grundlagen/export/pdf/<int:s_id>')
@login_required
def foerdergrundlage_export_pdf(s_id):
    schueler = _ensure_student_access_or_403(s_id)

    try:
        odt_buffer, filename_stem = _build_foerdergrundlage_export_payload(
            schueler,
            getattr(schueler, 'foerdergrundlage', None),
        )
        pdf_buffer = convert_odt_bytes_to_pdf(odt_buffer)
    except FileNotFoundError:
        flash('Die ODT-Vorlage für das Grundlagenblatt wurde nicht gefunden.')
        return redirect(url_for('foerderplan.foerdergrundlage_edit', s_id=s_id))
    except ValueError as exc:
        flash(str(exc))
        return redirect(url_for('foerderplan.foerdergrundlage_edit', s_id=s_id))
    except RuntimeError as exc:
        flash(f'PDF-Export fehlgeschlagen: {exc}')
        return redirect(url_for('foerderplan.foerdergrundlage_edit', s_id=s_id))

    return send_file(
        pdf_buffer,
        as_attachment=True,
        download_name=f'{filename_stem}.pdf',
        mimetype='application/pdf',
    )


@foerderplan_bp.route('/foerderplan/select_student')
@login_required
def foerderplan_select_student():
    schueler = [s for s in get_prioritized_students_for_user(current_user) if _teacher_can_access_student(current_user, s)]
    return render_template(
        'foerderplan_select.html',
        schueler=schueler,
        schueler_groups=[
            {
                'key': group['key'],
                'label': group['label'],
                'students': [s for s in group['students'] if _teacher_can_access_student(current_user, s)],
            }
            for group in get_grouped_student_choices_for_user(current_user)
            if [s for s in group['students'] if _teacher_can_access_student(current_user, s)]
        ],
    )


@foerderplan_bp.route('/foerderplan/list')
@login_required
def foerderplan_list():
    selection = get_tabbed_student_selection_for_user(
        current_user,
        selected_s_id=(request.args.get('schueler_id') or '').strip(),
        requested_tab=(request.args.get('tab') or '').strip(),
        auto_select_first=False,
        include_archived=True,
    )
    filter_info = {
        "is_admin": current_user.is_admin,
        "klassenleitung": None,
        "fachklassen": [],
        "selected_s_id": selection["selected_s_id"],
        "selected_schueler": selection["selected_student"],
    }
    query = _foerderplan_query_for_user(current_user).join(Schueler)
    if not filter_info["is_admin"]:
        klassenkontext = get_user_klassenkontext(current_user)
        filter_info["klassenleitung"] = klassenkontext.get('klassenleitung')
        filter_info["fachklassen"] = sorted(klassenkontext.get('fachklassen') or set(), key=lambda x: x.lower())

    selected_s_id = selection["selected_s_id"]
    if selected_s_id:
        try:
            s_id_int = int(selected_s_id)
            query = query.filter(Foerderplan.schueler_id == s_id_int)
        except ValueError:
            filter_info["selected_s_id"] = ""
            filter_info["selected_schueler"] = None

    plaene = query.order_by(Foerderplan.datum_erstellung.desc()).all()
    return render_template(
        'foerderplan_list.html',
        plaene=plaene,
        filter_info=filter_info,
        tab_definitions=selection['tab_definitions'],
        active_tab=selection['active_tab'],
        selected_s_id=selection['selected_s_id'],
        selected_student=selection['selected_student'],
        schueler=selection['students'],
        schueler_groups=selection['groups'],
    )


@foerderplan_bp.route('/foerderplan/view/<int:p_id>')
@login_required
def foerderplan_view(p_id):
    plan = _ensure_plan_access_or_404(p_id)
    next_url = (request.args.get('next') or '').strip()
    return render_template(
        'foerderplan_view.html',
        plan=plan,
        can_manage=_can_manage_foerderplan(plan),
        grundlage=getattr(plan.schueler, 'foerdergrundlage', None),
        next_url=next_url,
    )


@foerderplan_bp.route('/foerderplan/edit/<int:p_id>', methods=['GET', 'POST'])
@login_required
def foerderplan_edit(p_id):
    plan = _ensure_plan_access_or_404(p_id)
    next_url = (request.args.get('next') or request.form.get('next') or '').strip()
    if not _can_manage_foerderplan(plan):
        flash('Dieser Förderplan kann nur vom Ersteller oder vom Admin bearbeitet werden.')
        return redirect(url_for('foerderplan.foerderplan_view', p_id=plan.id, next=next_url) if next_url else url_for('foerderplan.foerderplan_view', p_id=plan.id))
    schueler = plan.schueler

    if request.method == 'POST':
        if _foerderplan_concurrency_conflict(plan):
            flash('Der Förderplan wurde zwischenzeitlich von jemand anderem geändert. Bitte Ansicht neu laden und Änderungen prüfen.')
            return redirect(url_for('foerderplan.foerderplan_edit', p_id=plan.id, next=next_url) if next_url else url_for('foerderplan.foerderplan_edit', p_id=plan.id))
        # Zustand festhalten, bevor irgendetwas ueberschrieben wird.
        vorher = snapshot(plan, PLAN_FELDER)
        vorher_inhalte = _inhalte_beschreibung(plan)

        plan.titel = request.form.get('titel')

        Foerderinhalt.query.filter_by(plan_id=plan.id).delete()

        ziele = request.form.getlist('foerderziel')
        ist_zustaende = request.form.getlist('ist_zustand')
        soll_zustaende = request.form.getlist('soll_zustand')
        massnahmen = request.form.getlist('massnahmen')

        for i in range(len(ziele)):
            if (ziele[i] or '').strip():
                db.session.add(Foerderinhalt(
                    plan_id=plan.id,
                    foerderziel=ziele[i],
                    ist_zustand=ist_zustaende[i] if i < len(ist_zustaende) else '',
                    soll_zustand=soll_zustaende[i] if i < len(soll_zustaende) else '',
                    massnahmen=massnahmen[i] if i < len(massnahmen) else '',
                    status_id=0,
                ))

        db.session.flush()
        teile = [describe(vorher, snapshot(plan, PLAN_FELDER), PLAN_FELDER)]
        nachher_inhalte = _inhalte_beschreibung(plan)
        if vorher_inhalte != nachher_inhalte:
            teile.append(f'Förderbereiche: {vorher_inhalte} → {nachher_inhalte}')
        _plan_log(plan, 'updated', '; '.join(filter(None, teile)))

        db.session.commit()
        flash(f'Förderplan "{plan.titel}" aktualisiert.')
        return redirect(_safe_next_url(next_url, url_for('foerderplan.foerderplan_view', p_id=plan.id)))

    vorschlaege = []
    for inhalt in plan.inhalte:
        vorschlaege.append({
            'bereich': inhalt.foerderziel or '',
            'ist': inhalt.ist_zustand or '',
            'soll': inhalt.soll_zustand or '',
            'massnahme': inhalt.massnahmen or '',
            'source_label': 'Bestehender Förderplaninhalt',
        })

    if not vorschlaege:
        vorschlaege.append({'bereich': '', 'ist': '', 'soll': '', 'massnahme': '', 'source_label': 'Leere Zeile'})

    return render_template(
        'foerderplan_wizard.html',
        schueler=schueler,
        grundlage=schueler.foerdergrundlage,
        vorschlaege=vorschlaege,
        all_boegen=Bogen.query.all(),
        now=utc_now(),
        is_edit_mode=True,
        plan=plan,
        concurrency_token=_foerderplan_concurrency_token(plan),
        next_url=next_url,
    )


@foerderplan_bp.route('/foerderplan/delete/<int:p_id>', methods=['POST'])
@login_required
def foerderplan_delete(p_id):
    plan = _ensure_plan_access_or_404(p_id)
    next_url = (request.form.get('next') or request.args.get('next') or '').strip()
    if not _can_manage_foerderplan(plan):
        flash('Dieser Förderplan kann nur vom Ersteller oder vom Admin gelöscht werden.')
        return redirect(url_for('foerderplan.foerderplan_view', p_id=plan.id, next=next_url) if next_url else url_for('foerderplan.foerderplan_view', p_id=plan.id))

    titel = plan.titel
    db.session.delete(plan)
    db.session.commit()
    flash(f'Förderplan "{titel}" gelöscht.')
    return redirect(_safe_next_url(next_url, url_for('foerderplan.foerderplan_list')))


@foerderplan_bp.route('/foerderplan/export/odt/<int:p_id>')
@login_required
def foerderplan_export_odt(p_id):
    plan = _ensure_plan_access_or_404(p_id)

    include_grundlagenblatt = (request.args.get('mit_grundlagenblatt') or '').strip() in {'1', 'true', 'ja'}
    next_url = (request.args.get('next') or '').strip()

    try:
        odt_buffer, filename_stem = _build_foerderplan_export_payload(
            plan,
            include_grundlagenblatt=include_grundlagenblatt,
        )
    except FileNotFoundError:
        if include_grundlagenblatt:
            flash('Eine ODT-Vorlage für Förderplan oder Grundlagenblatt wurde nicht gefunden.')
        else:
            flash('Die ODT-Vorlage für Förderpläne wurde nicht gefunden.')
        return redirect(_safe_next_url(next_url, url_for('foerderplan.foerderplan_view', p_id=p_id)))
    except ValueError as exc:
        flash(str(exc))
        return redirect(_safe_next_url(next_url, url_for('foerderplan.foerderplan_view', p_id=p_id)))

    return send_file(
        odt_buffer,
        as_attachment=True,
        download_name=f'{filename_stem}.odt',
        mimetype='application/vnd.oasis.opendocument.text',
    )


@foerderplan_bp.route('/foerderplan/export/pdf/<int:p_id>')
@login_required
def foerderplan_export_pdf(p_id):
    plan = _ensure_plan_access_or_404(p_id)

    include_grundlagenblatt = (request.args.get('mit_grundlagenblatt') or '').strip() in {'1', 'true', 'ja'}
    next_url = (request.args.get('next') or '').strip()

    try:
        odt_buffer, filename_stem = _build_foerderplan_export_payload(
            plan,
            include_grundlagenblatt=include_grundlagenblatt,
        )
        pdf_buffer = convert_odt_bytes_to_pdf(odt_buffer)
    except FileNotFoundError:
        if include_grundlagenblatt:
            flash('Eine ODT-Vorlage für Förderplan oder Grundlagenblatt wurde nicht gefunden.')
        else:
            flash('Die ODT-Vorlage für Förderpläne wurde nicht gefunden.')
        return redirect(_safe_next_url(next_url, url_for('foerderplan.foerderplan_view', p_id=p_id)))
    except ValueError as exc:
        flash(str(exc))
        return redirect(_safe_next_url(next_url, url_for('foerderplan.foerderplan_view', p_id=p_id)))
    except RuntimeError as exc:
        flash(f'PDF-Export fehlgeschlagen: {exc}')
        return redirect(_safe_next_url(next_url, url_for('foerderplan.foerderplan_view', p_id=p_id)))

    return send_file(
        pdf_buffer,
        as_attachment=True,
        download_name=f'{filename_stem}.pdf',
        mimetype='application/pdf',
    )


@foerderplan_bp.route('/foerderplan/evaluate/<int:p_id>', methods=['GET', 'POST'])
@login_required
def foerderplan_evaluate(p_id):
    plan = _ensure_plan_access_or_404(p_id)
    next_url = (request.args.get('next') or request.form.get('next') or '').strip()

    if request.method == 'POST':
        if _foerderplan_concurrency_conflict(plan):
            flash('Der Förderplan wurde zwischenzeitlich geändert. Bitte die Evaluationsmaske neu öffnen und die Eingaben erneut prüfen.')
            return redirect(url_for('foerderplan.foerderplan_evaluate', p_id=plan.id, next=next_url) if next_url else url_for('foerderplan.foerderplan_evaluate', p_id=plan.id))
        original_concurrency_token = _foerderplan_concurrency_token(plan)
        requested_plan_status = request.form.get('plan_status')
        submitted_inhalte = []
        for inhalt in plan.inhalte:
            neuer_status_raw = request.form.get(f'status_{inhalt.id}')
            eval_text = request.form.get(f'eval_{inhalt.id}')
            try:
                neuer_status = int(neuer_status_raw) if neuer_status_raw is not None else inhalt.status_id
            except ValueError:
                neuer_status = inhalt.status_id
            if neuer_status not in (1, 2):
                neuer_status = 2
            submitted_inhalte.append((inhalt, neuer_status, eval_text))

        if requested_plan_status == 'geschlossen':
            offene_ziele = [inhalt.foerderziel for inhalt, status, _ in submitted_inhalte if status not in (1, 2)]
            if offene_ziele:
                # Eingaben im Formular sichtbar halten, ohne zu speichern.
                plan.status = requested_plan_status
                for inhalt, status, eval_text in submitted_inhalte:
                    inhalt.status_id = status
                    inhalt.evaluation_text = eval_text
                flash(
                    'Ein Förderplan kann nur geschlossen werden, wenn alle Ziele als '
                    '"Ziel erreicht" oder "Nicht erreicht / Weiterführen" markiert sind.'
                )
                return render_template(
                    'foerderplan_evaluate.html',
                    plan=plan,
                    concurrency_token=original_concurrency_token,
                    next_url=next_url,
                )

        vorher = snapshot(plan, PLAN_FELDER)
        plan.status = requested_plan_status
        plan.datum_evaluation = utc_now()

        bewertet = 0
        for inhalt, neuer_status, eval_text in submitted_inhalte:
            if inhalt.status_id != neuer_status or (inhalt.evaluation_text or '') != (eval_text or ''):
                bewertet += 1
            inhalt.status_id = neuer_status
            inhalt.evaluation_text = eval_text

        teile = [describe(vorher, snapshot(plan, PLAN_FELDER), PLAN_FELDER)]
        if bewertet:
            teile.append(f'{bewertet} Förderbereich(e) bewertet')
        _plan_log(plan, 'evaluated', '; '.join(filter(None, teile)) or 'ohne Änderung gespeichert')

        db.session.commit()
        flash('Förderplan evaluiert und gespeichert.')
        return redirect(_safe_next_url(next_url, url_for('foerderplan.foerderplan_list')))

    return render_template(
        'foerderplan_evaluate.html',
        plan=plan,
        concurrency_token=_foerderplan_concurrency_token(plan),
        next_url=next_url,
    )


def register_foerderplan_routes(app):
    app.register_blueprint(foerderplan_bp)
