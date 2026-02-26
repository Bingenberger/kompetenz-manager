import os
import secrets
from datetime import timedelta

import pandas as pd
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func
from werkzeug.security import generate_password_hash

from extensions import db
from models import Bogen, Beobachtung, Elternkontakt, Foerderplan, Item, Schueler, SystemKonfiguration, User
from student_selection import get_grouped_student_choices_for_user, get_prioritized_students_for_user, get_user_klassenkontext
from time_utils import utc_now

system_bp = Blueprint('system', __name__)


def _parse_import_date(value):
    if value is None or pd.isna(value):
        return None

    # Excel dates often arrive as pandas Timestamp / datetime objects.
    if hasattr(value, 'date'):
        return value.date()

    text_value = str(value).strip()
    if not text_value:
        return None

    parsed = pd.to_datetime(text_value, dayfirst=True, errors='coerce')
    if pd.isna(parsed):
        return None
    return parsed.date()


def _get_system_konfiguration():
    return SystemKonfiguration.query.first()


def _get_dashboard_fokus_klasse(user):
    klassenkontext = get_user_klassenkontext(user)
    klassenleitung = klassenkontext.get('klassenleitung')
    fachklassen = sorted(klassenkontext.get('fachklassen') or set(), key=lambda x: x.lower())
    fokus_klasse = klassenleitung or (fachklassen[0] if fachklassen else None)
    fokus_typ = 'klassenleitung' if klassenleitung else ('fach' if fokus_klasse else None)
    return fokus_klasse, fokus_typ


def _get_elternsprechtag_fenster(config, heute):
    termine = []
    if config:
        if config.elternsprechtag_1:
            tage = (config.elternsprechtag_1 - heute).days
            termine.append(("Elternsprechtag 1", config.elternsprechtag_1, tage))
        if config.elternsprechtag_2:
            tage = (config.elternsprechtag_2 - heute).days
            termine.append(("Elternsprechtag 2", config.elternsprechtag_2, tage))
    return termine


def _todo_sort_key(todo):
    priority = int(todo.get('priority', 99))
    due_date = todo.get('due_date')
    due_ord = due_date.toordinal() if due_date else 999999999
    title = (todo.get('title') or '').lower()
    return (priority, due_ord, title)

@system_bp.route('/')
@login_required
def index():
    klassenkontext = get_user_klassenkontext(current_user)
    klassenleitung = klassenkontext.get('klassenleitung')
    fachklassen = sorted(klassenkontext.get('fachklassen') or set(), key=lambda x: x.lower())

    fokus_klasse = klassenleitung or (fachklassen[0] if fachklassen else None)
    fokus_typ = 'klassenleitung' if klassenleitung else ('fach' if fokus_klasse else None)

    stats = {
        'kinder_in_klasse': 0,
        'beobachtungsboegen_ausgefuellt': 0,
        'aktive_foerderplaene': 0,
    }

    todos = []
    heute = utc_now().date()
    in_14_tagen = heute + timedelta(days=14)
    in_12_wochen_datetime = utc_now() - timedelta(weeks=12)
    config = _get_system_konfiguration()

    if fokus_klasse:
        klasse_kinder = (
            Schueler.query
            .filter_by(klasse=fokus_klasse)
            .order_by(Schueler.nachname.asc(), Schueler.vorname.asc())
            .all()
        )
        stats['kinder_in_klasse'] = len(klasse_kinder)

        stats['beobachtungsboegen_ausgefuellt'] = (
            Beobachtung.query
            .join(Schueler, Beobachtung.schueler_id == Schueler.id)
            .join(Item, Beobachtung.item_id == Item.id)
            .filter(Schueler.klasse == fokus_klasse)
            .with_entities(Beobachtung.schueler_id, Item.bogen_id)
            .distinct()
            .count()
        )

        stats['aktive_foerderplaene'] = (
            Foerderplan.query
            .join(Schueler, Foerderplan.schueler_id == Schueler.id)
            .filter(
                Schueler.klasse == fokus_klasse,
                Foerderplan.status == 'aktiv',
            )
            .count()
        )

        aktive_plan_schueler_ids = {
            row[0]
            for row in (
                Foerderplan.query
                .join(Schueler, Foerderplan.schueler_id == Schueler.id)
                .filter(
                    Schueler.klasse == fokus_klasse,
                    Foerderplan.status == 'aktiv',
                )
                .with_entities(Foerderplan.schueler_id)
                .distinct()
                .all()
            )
        }

        elternsprechtage = []
        if config:
            if config.elternsprechtag_1:
                elternsprechtage.append(("Elternsprechtag 1", config.elternsprechtag_1))
            if config.elternsprechtag_2:
                elternsprechtage.append(("Elternsprechtag 2", config.elternsprechtag_2))

        hat_4_wochen_fenster = False
        hat_2_wochen_fenster = False
        aktive_esp_termine = []
        for label, termin in elternsprechtage:
            tage_bis = (termin - heute).days
            if 1 <= tage_bis <= 28:
                hat_4_wochen_fenster = True
                aktive_esp_termine.append((label, termin, tage_bis))
            if 1 <= tage_bis <= 14:
                hat_2_wochen_fenster = True

        if hat_4_wochen_fenster:
            for label, termin, _tage_bis in aktive_esp_termine:
                if stats['aktive_foerderplaene'] > 0:
                    todos.append({
                        'title': f'{label}: aktive Förderpläne evaluieren',
                        'detail': (
                            f"{stats['aktive_foerderplaene']} aktive Förderpläne in Klasse {fokus_klasse} "
                            f"vor dem Termin am {termin.strftime('%d.%m.%Y')} prüfen."
                        ),
                        'variant': 'warning',
                        'url': url_for('system.todo_foerderplan_evaluationen'),
                        'priority': 30,
                        'due_date': termin,
                    })

            minus_rows = (
                db.session.query(
                    Beobachtung.schueler_id.label('schueler_id'),
                    Item.bogen_id.label('bogen_id'),
                    func.count(func.distinct(Beobachtung.item_id)).label('minus_items'),
                )
                .join(Schueler, Beobachtung.schueler_id == Schueler.id)
                .join(Item, Beobachtung.item_id == Item.id)
                .filter(
                    Schueler.klasse == fokus_klasse,
                    Beobachtung.wert == 1,
                    Beobachtung.datum >= in_12_wochen_datetime,
                )
                .group_by(Beobachtung.schueler_id, Item.bogen_id)
                .all()
            )

            foerderplan_kandidaten_ids = {
                row.schueler_id
                for row in minus_rows
                if (row.minus_items or 0) >= 4 and row.schueler_id not in aktive_plan_schueler_ids
            }

            if foerderplan_kandidaten_ids:
                kandidaten = [s for s in klasse_kinder if s.id in foerderplan_kandidaten_ids]
                beispiele = ', '.join(f'{s.vorname} {s.nachname}'.strip() for s in kandidaten[:3])
                beispiel_text = f' Beispiele: {beispiele}.' if beispiele else ''
                for label, termin, _tage_bis in aktive_esp_termine:
                    todos.append({
                        'title': f'{label}: Förderplan-Kandidaten prüfen',
                        'detail': (
                            f"{len(foerderplan_kandidaten_ids)} Kind(er) mit mind. 4x '-' in einem Bogen "
                            f"(letzte 12 Wochen) ohne aktiven Förderplan. Termin: {termin.strftime('%d.%m.%Y')}."
                            f"{beispiel_text}"
                        ),
                        'variant': 'danger',
                        'url': url_for('system.todo_foerderplan_kandidaten'),
                        'priority': 20,
                        'due_date': termin,
                    })

        if hat_2_wochen_fenster and klasse_kinder:
            schueler_mit_aktuellem_bogen_ids = {
                row[0]
                for row in (
                    Beobachtung.query
                    .join(Schueler, Beobachtung.schueler_id == Schueler.id)
                    .filter(
                        Schueler.klasse == fokus_klasse,
                        Beobachtung.datum >= in_12_wochen_datetime,
                    )
                    .with_entities(Beobachtung.schueler_id)
                    .distinct()
                    .all()
                )
            }
            fehlende_aktuelle_boegen = [
                s for s in klasse_kinder if s.id not in schueler_mit_aktuellem_bogen_ids
            ]
            if fehlende_aktuelle_boegen:
                naechster_termin_2w = min(
                    (termin for _label, termin, tage in aktive_esp_termine if 1 <= tage <= 14),
                    default=None,
                )
                beispiele = ', '.join(
                    f'{s.vorname} {s.nachname}'.strip() for s in fehlende_aktuelle_boegen[:3]
                )
                todos.append({
                    'title': 'Beobachtungsbögen aktualisieren',
                    'detail': (
                        f"{len(fehlende_aktuelle_boegen)} Kind(er) ohne Beobachtungsbogen-Eintrag in den letzten 12 Wochen"
                        + (f" vor dem Elternsprechtag am {naechster_termin_2w.strftime('%d.%m.%Y')}" if naechster_termin_2w else '')
                        + (f". Beispiele: {beispiele}." if beispiele else '.')
                    ),
                    'variant': 'info',
                    'url': url_for('system.todo_beobachtungsboegen_fehlend'),
                    'priority': 40,
                    'due_date': naechster_termin_2w,
                })

        ueberfaellige_evals = (
            Foerderplan.query
            .join(Schueler, Foerderplan.schueler_id == Schueler.id)
            .filter(
                Schueler.klasse == fokus_klasse,
                Foerderplan.status == 'aktiv',
                Foerderplan.datum_evaluation.isnot(None),
                Foerderplan.datum_evaluation < heute,
            )
            .count()
        )
        if ueberfaellige_evals:
            todos.append({
                'title': f'{ueberfaellige_evals} Förderplan-Evaluation(en) überfällig',
                'detail': f'Klasse {fokus_klasse} bitte priorisiert prüfen.',
                'variant': 'danger',
                'url': url_for('system.todo_foerderplan_evaluationen'),
                'priority': 10,
                'due_date': heute,
            })

        anstehende_evals = (
            Foerderplan.query
            .join(Schueler, Foerderplan.schueler_id == Schueler.id)
            .filter(
                Schueler.klasse == fokus_klasse,
                Foerderplan.status == 'aktiv',
                Foerderplan.datum_evaluation.isnot(None),
                Foerderplan.datum_evaluation >= heute,
                Foerderplan.datum_evaluation <= in_14_tagen,
            )
            .order_by(Foerderplan.datum_evaluation.asc())
            .limit(3)
            .all()
        )
        for plan in anstehende_evals:
            schueler_name = f"{plan.schueler.vorname or ''} {plan.schueler.nachname or ''}".strip() or 'Kind'
            todos.append({
                'title': f'Evaluation Förderplan: {schueler_name}',
                'detail': f"Fällig bis {plan.datum_evaluation.strftime('%d.%m.%Y')}",
                'variant': 'warning',
                'url': url_for('system.todo_foerderplan_evaluationen'),
                'priority': 25,
                'due_date': plan.datum_evaluation,
            })

        naechste_termine = (
            Elternkontakt.query
            .join(Schueler, Elternkontakt.schueler_id == Schueler.id)
            .filter(
                Schueler.klasse == fokus_klasse,
                Elternkontakt.naechster_termin.isnot(None),
                Elternkontakt.naechster_termin >= heute,
                Elternkontakt.naechster_termin <= in_14_tagen,
            )
            .order_by(Elternkontakt.naechster_termin.asc())
            .limit(2)
            .all()
        )
        for kontakt in naechste_termine:
            schueler_name = f"{kontakt.schueler.vorname or ''} {kontakt.schueler.nachname or ''}".strip() or 'Kind'
            todos.append({
                'title': f'Elternkontakt nachverfolgen: {schueler_name}',
                'detail': f"Termin am {kontakt.naechster_termin.strftime('%d.%m.%Y')}",
                'variant': 'info',
                'url': url_for('system.todo_elternkontakt_erinnerungen'),
                'priority': 35,
                'due_date': kontakt.naechster_termin,
            })

        if stats['beobachtungsboegen_ausgefuellt'] == 0:
            todos.append({
                'title': 'Erste Beobachtungsbögen erfassen',
                'detail': f'Für Klasse {fokus_klasse} liegen noch keine ausgefüllten Bögen vor.',
                'variant': 'success',
                'url': url_for('erfassung.erfassen_schueler'),
                'priority': 50,
            })
    else:
        todos.append({
            'title': 'Klasse zuordnen lassen',
            'detail': 'Für personalisierte Statistik bitte eine Klassenleitung oder Fachklasse hinterlegen.',
            'variant': 'info',
            'url': url_for('auth.user_menu'),
            'priority': 5,
        })

    if not todos:
        todos.append({
            'title': 'Aktuell keine dringenden Aufgaben',
            'detail': 'In den nächsten 14 Tagen sind keine Termine oder Evaluationen hinterlegt.',
            'variant': 'success',
            'url': url_for('system.schuelerakte'),
            'priority': 99,
        })

    todos.sort(key=_todo_sort_key)

    return render_template(
        'index.html',
        teacher_name=current_user.display_name,
        hero_klasse=fokus_klasse,
        hero_klasse_typ=fokus_typ,
        hero_stats=stats,
        hero_todos=todos[:8],
        school_year=(config.schuljahr if config else None),
    )


@system_bp.route('/schuelerakte')
@login_required
def schuelerakte():
    schueler_liste = get_prioritized_students_for_user(current_user)
    schueler_groups = get_grouped_student_choices_for_user(current_user)

    selected_s_id = (request.args.get('schueler_id') or '').strip()
    if not selected_s_id and schueler_liste:
        selected_s_id = str(schueler_liste[0].id)

    selected_student = None
    if selected_s_id:
        try:
            s_id_int = int(selected_s_id)
            selected_student = next((s for s in schueler_liste if s.id == s_id_int), None)
            if not selected_student:
                selected_s_id = ''
        except ValueError:
            selected_s_id = ''

    foerderplaene = []
    elternkontakte = []
    bogen_summaries = []
    recent_beobachtungen = []

    if selected_student:
        foerderplaene = (
            Foerderplan.query
            .filter(Foerderplan.schueler_id == selected_student.id)
            .order_by(Foerderplan.datum_erstellung.desc())
            .all()
        )

        elternkontakte = (
            Elternkontakt.query
            .filter(Elternkontakt.schueler_id == selected_student.id)
            .order_by(Elternkontakt.datum.desc())
            .limit(12)
            .all()
        )

        bogen_rows = (
            db.session.query(
                Bogen.id.label('bogen_id'),
                Bogen.titel.label('bogen_titel'),
                func.count(Beobachtung.id).label('anzahl_eintraege'),
                func.max(Beobachtung.datum).label('letzte_beobachtung'),
            )
            .select_from(Beobachtung)
            .join(Item, Beobachtung.item_id == Item.id)
            .join(Bogen, Item.bogen_id == Bogen.id)
            .filter(Beobachtung.schueler_id == selected_student.id)
            .group_by(Bogen.id, Bogen.titel)
            .order_by(func.max(Beobachtung.datum).desc())
            .all()
        )
        bogen_summaries = list(bogen_rows)

        beobachtungs_rows = (
            db.session.query(Beobachtung, Item.text, Bogen.titel)
            .join(Item, Beobachtung.item_id == Item.id)
            .join(Bogen, Item.bogen_id == Bogen.id)
            .filter(Beobachtung.schueler_id == selected_student.id)
            .order_by(Beobachtung.datum.desc())
            .limit(12)
            .all()
        )
        for beobachtung, item_text, bogen_titel in beobachtungs_rows:
            recent_beobachtungen.append({
                'beobachtung': beobachtung,
                'item_text': item_text,
                'bogen_titel': bogen_titel,
            })

    return render_template(
        'schuelerakte.html',
        schueler=schueler_liste,
        schueler_groups=schueler_groups,
        selected_s_id=selected_s_id,
        selected_student=selected_student,
        foerderplaene=foerderplaene,
        elternkontakte=elternkontakte,
        bogen_summaries=bogen_summaries,
        recent_beobachtungen=recent_beobachtungen,
    )


@system_bp.route('/todo/elternkontakte')
@login_required
def todo_elternkontakt_erinnerungen():
    heute = utc_now().date()
    in_14_tagen = heute + timedelta(days=14)
    fokus_klasse, _ = _get_dashboard_fokus_klasse(current_user)
    if not fokus_klasse:
        flash('Keine Klasse zugeordnet.')
        return redirect(url_for('system.index'))

    termine = (
        Elternkontakt.query
        .join(Schueler, Elternkontakt.schueler_id == Schueler.id)
        .filter(
            Schueler.klasse == fokus_klasse,
            Elternkontakt.naechster_termin.isnot(None),
            Elternkontakt.naechster_termin >= heute,
            Elternkontakt.naechster_termin <= in_14_tagen,
        )
        .order_by(Elternkontakt.naechster_termin.asc())
        .all()
    )

    rows = []
    for kontakt in termine:
        last_protocol = (
            Elternkontakt.query
            .filter(
                Elternkontakt.schueler_id == kontakt.schueler_id,
                Elternkontakt.eintrag_typ == 'protokoll',
            )
            .order_by(Elternkontakt.datum.desc())
            .first()
        )
        rows.append({
            'kontakt': kontakt,
            'last_protocol': last_protocol,
        })

    return render_template(
        'todo_elternkontakte.html',
        fokus_klasse=fokus_klasse,
        rows=rows,
        heute=heute,
    )


@system_bp.route('/todo/foerderplan-evaluationen')
@login_required
def todo_foerderplan_evaluationen():
    heute = utc_now().date()
    fokus_klasse, _ = _get_dashboard_fokus_klasse(current_user)
    if not fokus_klasse:
        flash('Keine Klasse zugeordnet.')
        return redirect(url_for('system.index'))

    config = _get_system_konfiguration()
    termine = _get_elternsprechtag_fenster(config, heute)
    aktive_4w = [t for t in termine if 1 <= t[2] <= 28]

    plaene = (
        Foerderplan.query
        .join(Schueler, Foerderplan.schueler_id == Schueler.id)
        .filter(
            Schueler.klasse == fokus_klasse,
            Foerderplan.status == 'aktiv',
        )
        .order_by(Schueler.nachname.asc(), Schueler.vorname.asc(), Foerderplan.datum_erstellung.desc())
        .all()
    )

    return render_template(
        'todo_foerderplan_evaluationen.html',
        fokus_klasse=fokus_klasse,
        plaene=plaene,
        aktive_elternsprechtage=aktive_4w,
        heute=heute,
    )


@system_bp.route('/todo/foerderplan-kandidaten')
@login_required
def todo_foerderplan_kandidaten():
    heute = utc_now().date()
    fokus_klasse, _ = _get_dashboard_fokus_klasse(current_user)
    if not fokus_klasse:
        flash('Keine Klasse zugeordnet.')
        return redirect(url_for('system.index'))

    in_12_wochen_datetime = utc_now() - timedelta(weeks=12)
    config = _get_system_konfiguration()
    aktive_4w = [t for t in _get_elternsprechtag_fenster(config, heute) if 1 <= t[2] <= 28]

    aktive_plan_schueler_ids = {
        row[0]
        for row in (
            Foerderplan.query
            .join(Schueler, Foerderplan.schueler_id == Schueler.id)
            .filter(Schueler.klasse == fokus_klasse, Foerderplan.status == 'aktiv')
            .with_entities(Foerderplan.schueler_id)
            .distinct()
            .all()
        )
    }

    minus_rows = (
        db.session.query(
            Beobachtung.schueler_id.label('schueler_id'),
            Item.bogen_id.label('bogen_id'),
            func.count(func.distinct(Beobachtung.item_id)).label('minus_items'),
        )
        .join(Schueler, Beobachtung.schueler_id == Schueler.id)
        .join(Item, Beobachtung.item_id == Item.id)
        .filter(
            Schueler.klasse == fokus_klasse,
            Beobachtung.wert == 1,
            Beobachtung.datum >= in_12_wochen_datetime,
        )
        .group_by(Beobachtung.schueler_id, Item.bogen_id)
        .all()
    )

    bogens = {b.id: b for b in Bogen.query.all()}
    schueler_map = {
        s.id: s for s in Schueler.query.filter_by(klasse=fokus_klasse).all()
    }

    kandidaten = {}
    for row in minus_rows:
        if (row.minus_items or 0) < 4 or row.schueler_id in aktive_plan_schueler_ids:
            continue
        s = schueler_map.get(row.schueler_id)
        if not s:
            continue
        eintrag = kandidaten.setdefault(s.id, {'schueler': s, 'boegen': []})
        eintrag['boegen'].append({
            'bogen_id': row.bogen_id,
            'bogen_titel': getattr(bogens.get(row.bogen_id), 'titel', f'Bogen {row.bogen_id}'),
            'minus_items': int(row.minus_items or 0),
        })

    kandidat_rows = sorted(
        kandidaten.values(),
        key=lambda r: ((r['schueler'].nachname or '').lower(), (r['schueler'].vorname or '').lower()),
    )

    return render_template(
        'todo_foerderplan_kandidaten.html',
        fokus_klasse=fokus_klasse,
        kandidat_rows=kandidat_rows,
        aktive_elternsprechtage=aktive_4w,
    )


@system_bp.route('/todo/beobachtungsboegen-fehlend')
@login_required
def todo_beobachtungsboegen_fehlend():
    heute = utc_now().date()
    fokus_klasse, _ = _get_dashboard_fokus_klasse(current_user)
    if not fokus_klasse:
        flash('Keine Klasse zugeordnet.')
        return redirect(url_for('system.index'))

    in_12_wochen_datetime = utc_now() - timedelta(weeks=12)
    config = _get_system_konfiguration()
    aktive_2w = [t for t in _get_elternsprechtag_fenster(config, heute) if 1 <= t[2] <= 14]

    kinder = (
        Schueler.query
        .filter_by(klasse=fokus_klasse)
        .order_by(Schueler.nachname.asc(), Schueler.vorname.asc())
        .all()
    )
    boegen = Bogen.query.order_by(Bogen.titel.asc()).all()

    aktuelle_kombis = {
        (row[0], row[1])
        for row in (
            Beobachtung.query
            .join(Schueler, Beobachtung.schueler_id == Schueler.id)
            .join(Item, Beobachtung.item_id == Item.id)
            .filter(
                Schueler.klasse == fokus_klasse,
                Beobachtung.datum >= in_12_wochen_datetime,
            )
            .with_entities(Beobachtung.schueler_id, Item.bogen_id)
            .distinct()
            .all()
        )
    }

    rows = []
    for s in kinder:
        fehlende_boegen = []
        for b in boegen:
            if (s.id, b.id) not in aktuelle_kombis:
                fehlende_boegen.append(b)
        if fehlende_boegen:
            rows.append({'schueler': s, 'fehlende_boegen': fehlende_boegen})

    return render_template(
        'todo_beobachtungsboegen_fehlend.html',
        fokus_klasse=fokus_klasse,
        rows=rows,
        aktive_elternsprechtage=aktive_2w,
    )

@system_bp.route('/import/<typ>', methods=['GET', 'POST'])
@login_required
def data_import(typ):
    next_url = (request.args.get('next') or request.form.get('next') or '').strip()
    if request.method == 'POST':
        file = request.files['file']
        if file.filename.endswith('.xlsx'):
            df = pd.read_excel(file)
            required_columns = {
                'schueler': ['Vorname', 'Nachname', 'Klasse', 'Geburtsdatum'],
                'bogen': ['Bogen', 'Bereich', 'Item'],
            }.get(typ, [])
            missing_columns = [col for col in required_columns if col not in df.columns]
            if missing_columns:
                flash('Fehlende Spalten in Excel-Datei: ' + ', '.join(missing_columns))
                return redirect(url_for('system.data_import', typ=typ, next=next_url) if next_url else url_for('system.data_import', typ=typ))
            if typ == 'schueler':
                for _, row in df.iterrows():
                    s = Schueler(
                        vorname=row['Vorname'],
                        nachname=row['Nachname'],
                        klasse=row['Klasse'],
                        geburtsdatum=_parse_import_date(row.get('Geburtsdatum')),
                    )
                    db.session.add(s)
            elif typ == 'bogen':
                for _, row in df.iterrows():
                    bogen_titel = row['Bogen']
                    exist_bogen = Bogen.query.filter_by(titel=bogen_titel).first()
                    if not exist_bogen:
                        exist_bogen = Bogen(titel=bogen_titel)
                        db.session.add(exist_bogen)
                        db.session.flush()

                    item = Item(bogen_id=exist_bogen.id, bereich=row['Bereich'], text=row['Item'])
                    db.session.add(item)

            db.session.commit()
            flash(f'{typ.capitalize()} erfolgreich importiert!')
            if next_url.startswith('/') and not next_url.startswith('//'):
                return redirect(next_url)
            return redirect(url_for('system.index'))
    return render_template('import.html', typ=typ, next_url=next_url)

@system_bp.route('/setup')
def setup():
    if os.environ.get('ALLOW_SETUP_ROUTE') != '1':
        return "Setup-Route ist deaktiviert.", 403

    if request.remote_addr not in {'127.0.0.1', '::1'}:
        return "Setup ist nur lokal auf dem Server erlaubt.", 403

    db.create_all()
    if not User.query.filter_by(username='admin').first():
        setup_password = os.environ.get('SETUP_ADMIN_PASSWORD') or secrets.token_urlsafe(12)
        hashed_pw = generate_password_hash(setup_password)
        admin = User(username='admin', password_hash=hashed_pw)
        db.session.add(admin)
        db.session.commit()
        return (
            "Datenbank & Admin-User angelegt! Temporäres Passwort: "
            f"<strong>{setup_password}</strong> "
            f"<a href='{url_for('auth.login')}'>Zum Login</a>"
        )

    if not Schueler.query.first():
        db.session.add(Schueler(vorname="Max", nachname="Muster", klasse="4a"))
        db.session.add(Schueler(vorname="Lisa", nachname="Lustig", klasse="4a"))

        b = Bogen(titel="Sozialverhalten")
        db.session.add(b)
        db.session.flush()
        db.session.add(Item(bogen_id=b.id, bereich="Konflikt", text="Löst Streit friedlich"))
        db.session.add(Item(bogen_id=b.id, bereich="Arbeit", text="Arbeitet konzentriert"))

        db.session.commit()
        return f"Datenbank erstellt und Testdaten angelegt! <a href='{url_for('system.index')}'>Zum Start</a>"

    return f"Datenbank existiert schon. <a href='{url_for('system.index')}'>Zum Start</a>"


def register_system_routes(app):
    app.register_blueprint(system_bp)
