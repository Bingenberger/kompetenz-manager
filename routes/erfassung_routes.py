from datetime import datetime
import hashlib
import json

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, session, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from extensions import db
from models import Beobachtung, Bogen, Elternberatung, Elternkontakt, Foerderplan, Item, Schueler
from odt_export import convert_odt_bytes_to_pdf, render_odt_from_ott_template
from school_year import active_school_year_start
from student_selection import (
    get_distinct_klassen,
    get_grouped_student_choices_for_user,
    get_prioritized_students_for_user,
    get_tabbed_student_selection_for_user,
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


def _build_bogen_entries_for_student(student_id):
    boegen = (
        Bogen.query
        .join(Item, Item.bogen_id == Bogen.id)
        .join(Beobachtung, Beobachtung.item_id == Item.id)
        .filter(Beobachtung.schueler_id == student_id)
        .distinct()
        .order_by(Bogen.titel.asc())
        .all()
    )
    symbol_map = {1: '-', 2: 'o', 3: '+', 4: '++'}
    color_map = {1: 'danger', 2: 'warning', 3: 'success', 4: 'success'}
    bogen_rows = []

    for bogen in boegen:
        item_rows = []
        items = Item.query.filter_by(bogen_id=bogen.id).order_by(Item.bereich.asc(), Item.text.asc()).all()
        for item in items:
            eintraege = (
                Beobachtung.query
                .filter_by(schueler_id=student_id, item_id=item.id)
                .order_by(Beobachtung.datum.desc())
                .all()
            )
            if not eintraege:
                continue
            school_year_start = active_school_year_start()
            aktuelle_eintraege = [
                entry for entry in eintraege
                if not school_year_start or (entry.datum and entry.datum.date() >= school_year_start)
            ]
            werte = [e.wert for e in aktuelle_eintraege if e.wert is not None]
            durchschnitt = round(sum(werte) / len(werte), 1) if werte else 0
            rounded_score = min(4, max(1, int(durchschnitt + 0.5))) if werte else None
            item_rows.append({
                'item': item,
                'eintraege': eintraege,
                'anzahl': len(aktuelle_eintraege),
                'durchschnitt': durchschnitt,
                'durchschnitt_symbol': symbol_map.get(rounded_score, '-') if rounded_score else '-',
                'durchschnitt_color': color_map.get(rounded_score, 'secondary') if rounded_score else 'secondary',
            })

        if item_rows:
            bogen_rows.append({
                'bogen': bogen,
                'item_rows': item_rows,
                'item_count': len(item_rows),
                'entry_count': sum(row['anzahl'] for row in item_rows),
            })

    return {
        'bogen_rows': bogen_rows,
        'symbol_map': symbol_map,
        'color_map': color_map,
    }


def _get_consultation_plan_context(student_id):
    active_plan = (
        Foerderplan.query
        .filter(
            Foerderplan.schueler_id == student_id,
            Foerderplan.status == 'aktiv',
        )
        .order_by(Foerderplan.datum_erstellung.desc(), Foerderplan.id.desc())
        .first()
    )
    last_evaluated_plan = (
        Foerderplan.query
        .filter(
            Foerderplan.schueler_id == student_id,
            Foerderplan.datum_evaluation.isnot(None),
        )
        .order_by(Foerderplan.datum_evaluation.desc(), Foerderplan.id.desc())
        .first()
    )
    if active_plan and last_evaluated_plan and active_plan.id == last_evaluated_plan.id:
        last_evaluated_plan = None
    return {
        'active_plan': active_plan,
        'last_evaluated_plan': last_evaluated_plan,
    }


@erfassung_bp.route('/erfassen/reihe/start', methods=['GET', 'POST'])
@login_required
def reihe_start():
    next_url = (request.args.get('next') or request.form.get('next') or '').strip()
    if request.method == 'POST':
        klasse = (request.form.get('klasse') or '').strip()
        item_id = (request.form.get('item_id') or '').strip()
        anlass = request.form.get('anlass')
        datum_str = request.form.get('datum')

        if not klasse:
            flash('Bitte eine Klasse auswählen.')
            return redirect(request.url)
        if not item_id:
            flash('Bitte zuerst Bogen, Bereich und Kompetenz auswählen.')
            return redirect(request.url)

        schueler = (
            Schueler.query
            .filter(Schueler.klasse == klasse, Schueler.is_active.is_(True))
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
    boegen = Bogen.query.order_by(Bogen.titel.asc()).all()
    return render_template(
        'reihe_start.html',
        boegen=boegen,
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
    tab = (request.args.get('tab') or '').strip()
    start_requested = (request.args.get('start') or request.form.get('start') or '').strip() == '1'
    next_url = (request.args.get('next') or request.form.get('next') or '').strip()

    if not s_id or not b_id or not start_requested:
        selection = get_tabbed_student_selection_for_user(
            current_user,
            selected_s_id=s_id,
            requested_tab=tab,
            auto_select_first=False,
        )
        selected_bogen = None
        selected_b_id = (request.args.get('bogen_id') or '').strip()
        if selected_b_id:
            try:
                selected_bogen = db.session.get(Bogen, int(selected_b_id))
            except (TypeError, ValueError):
                selected_bogen = None
                selected_b_id = ''

        bogen_stats = None
        if selection['selected_student'] and selected_bogen:
            total_entries = (
                db.session.query(func.count(Beobachtung.id))
                .join(Item, Beobachtung.item_id == Item.id)
                .filter(
                    Beobachtung.schueler_id == selection['selected_student'].id,
                    Item.bogen_id == selected_bogen.id,
                )
                .scalar()
            ) or 0

            item_count = (
                db.session.query(func.count(Item.id))
                .filter(Item.bogen_id == selected_bogen.id)
                .scalar()
            ) or 0

            last_complete_day = None
            if item_count > 0:
                last_complete_day = (
                    db.session.query(func.date(Beobachtung.datum).label('d'))
                    .join(Item, Beobachtung.item_id == Item.id)
                    .filter(
                        Beobachtung.schueler_id == selection['selected_student'].id,
                        Item.bogen_id == selected_bogen.id,
                        Beobachtung.anlass == 'ganzer Bogen',
                    )
                    .group_by(func.date(Beobachtung.datum))
                    .having(func.count(func.distinct(Beobachtung.item_id)) >= item_count)
                    .order_by(func.date(Beobachtung.datum).desc())
                    .first()
                )

            bogen_stats = {
                'total_entries': int(total_entries),
                'last_complete_day': (last_complete_day[0] if last_complete_day else None),
            }
            raw_day = bogen_stats['last_complete_day']
            if raw_day:
                try:
                    if hasattr(raw_day, 'strftime'):
                        bogen_stats['last_complete_day_display'] = raw_day.strftime('%d.%m.%Y')
                    else:
                        bogen_stats['last_complete_day_display'] = datetime.strptime(str(raw_day), '%Y-%m-%d').strftime('%d.%m.%Y')
                except (TypeError, ValueError):
                    bogen_stats['last_complete_day_display'] = str(raw_day)
            else:
                bogen_stats['last_complete_day_display'] = None

        return render_template(
            'batch_schueler.html',
            step=1,
            schueler=selection['students'],
            schueler_groups=selection['groups'],
            tab_definitions=selection['tab_definitions'],
            active_tab=selection['active_tab'],
            selected_s_id=selection['selected_s_id'],
            selected_student=selection['selected_student'],
            boegen=Bogen.query.order_by(Bogen.titel.asc()).all(),
            selected_b_id=selected_b_id,
            selected_bogen=selected_bogen,
            bogen_stats=bogen_stats,
            next_url=next_url,
        )

    schueler = db.session.get(Schueler, int(s_id))
    items = Item.query.filter_by(bogen_id=b_id).all()

    if request.method == 'POST':
        for i in items:
            wert = request.form.get(f'wert_{i.id}')
            if wert:
                b = Beobachtung(
                    schueler_id=schueler.id,
                    item_id=i.id,
                    wert=int(wert),
                    anlass='ganzer Bogen',
                )
                db.session.add(b)
        db.session.commit()
        flash('Bogen gespeichert!')
        return redirect(_safe_next_url(next_url, url_for('system.index')))

    return render_template('batch_schueler.html', step=2, schueler=schueler, items=items, next_url=next_url)


@erfassung_bp.route('/erfassen/einzel', methods=['GET', 'POST'])
@login_required
def erfassen_einzel():
    tab = (request.args.get('tab') or '').strip()
    next_url = (request.args.get('next') or request.form.get('next') or '').strip()
    selected_bogen_id = (request.args.get('bogen_id') or request.form.get('bogen_id') or '').strip()
    selected_bereich = (request.args.get('bereich') or request.form.get('bereich') or '').strip()
    selected_item_id = (request.args.get('item_id') or request.form.get('item_id') or '').strip()
    if request.method == 'POST':
        schueler_id = (request.form.get('schueler_id') or '').strip()
        item_id = (request.form.get('item_id') or '').strip()
        wert = (request.form.get('wert') or '').strip()
        if not schueler_id or not item_id or not wert:
            flash('Bitte Kind, Kompetenz und Bewertung auswählen.')
            return redirect(url_for(
                'erfassung.erfassen_einzel',
                tab=(request.form.get('tab') or tab or None),
                schueler_id=schueler_id or None,
                bogen_id=(request.form.get('bogen_id') or '').strip() or None,
                bereich=(request.form.get('bereich') or '').strip() or None,
                item_id=item_id or None,
                next=next_url or None,
            ))

        foto = request.files.get('foto')
        filename = speichere_upload_bild(foto)

        b = Beobachtung(
            schueler_id=schueler_id,
            item_id=item_id,
            wert=wert,
            kommentar=request.form.get('kommentar'),
            foto_pfad=filename,
        )
        db.session.add(b)
        db.session.commit()
        flash('Beobachtung gespeichert!')
        return redirect(_safe_next_url(next_url, url_for('system.index')))

    selection = get_tabbed_student_selection_for_user(
        current_user,
        selected_s_id=(request.args.get('schueler_id') or '').strip(),
        requested_tab=tab,
        auto_select_first=False,
    )

    boegen = Bogen.query.order_by(Bogen.titel.asc()).all()
    selected_bogen = None
    if selected_bogen_id:
        try:
            b_id_int = int(selected_bogen_id)
            selected_bogen = next((b for b in boegen if b.id == b_id_int), None)
            if not selected_bogen:
                selected_bogen_id = ''
        except ValueError:
            selected_bogen_id = ''

    competency_items = []
    if selected_bogen:
        competency_items = sorted(selected_bogen.items, key=lambda x: ((x.bereich or '').lower(), (x.text or '').lower()))
    bereiche = sorted({(i.bereich or '').strip() for i in competency_items if (i.bereich or '').strip()}, key=lambda x: x.lower())
    if selected_bereich and selected_bereich not in bereiche:
        selected_bereich = ''

    filtered_competency_items = competency_items
    if selected_bereich:
        filtered_competency_items = [i for i in competency_items if (i.bereich or '').strip() == selected_bereich]

    selected_item = None
    if selected_item_id and filtered_competency_items:
        try:
            i_id_int = int(selected_item_id)
            selected_item = next((i for i in filtered_competency_items if i.id == i_id_int), None)
            if not selected_item:
                selected_item_id = ''
        except ValueError:
            selected_item_id = ''

    return render_template(
        'einzel.html',
        schueler=selection['students'],
        schueler_groups=selection['groups'],
        tab_definitions=selection['tab_definitions'],
        active_tab=selection['active_tab'],
        selected_student=selection['selected_student'],
        selected_s_id=selection['selected_s_id'],
        boegen=boegen,
        selected_bogen_id=selected_bogen_id,
        selected_bogen=selected_bogen,
        competency_items=filtered_competency_items,
        bereiche=bereiche,
        selected_bereich=selected_bereich,
        selected_item_id=selected_item_id,
        selected_item=selected_item,
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
            .filter(Schueler.klasse == klasse, Schueler.is_active.is_(True))
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
    tab = (request.args.get('tab') or '').strip()
    selection = get_tabbed_student_selection_for_user(
        current_user,
        selected_s_id=(request.args.get('schueler_id') or '').strip(),
        requested_tab=tab,
        auto_select_first=False,
    )
    selected_s_id = selection['selected_s_id']
    schueler = selection['students']
    recent_contacts_query = Elternkontakt.query.join(Schueler).order_by(Elternkontakt.datum.desc())
    recent_consultations_query = Elternberatung.query.join(Schueler).order_by(Elternberatung.datum.desc(), Elternberatung.id.desc())
    if selected_s_id:
        try:
            recent_contacts_query = recent_contacts_query.filter(Elternkontakt.schueler_id == int(selected_s_id))
            recent_consultations_query = recent_consultations_query.filter(Elternberatung.schueler_id == int(selected_s_id))
        except ValueError:
            selected_s_id = ''
    recent_contacts = recent_contacts_query.limit(24).all()
    recent_consultations = recent_consultations_query.limit(24).all()
    recent_entries = []
    for kontakt in recent_contacts:
        recent_entries.append({
            'kind': 'kontakt',
            'sort_date': kontakt.datum,
            'obj': kontakt,
        })
    for beratung in recent_consultations:
        recent_entries.append({
            'kind': 'beratung',
            'sort_date': beratung.datum,
            'obj': beratung,
        })
    def _normalize_sort_date(value):
        if value is None:
            return utc_now()
        if isinstance(value, datetime):
            return value
        return datetime.combine(value, datetime.min.time())

    recent_entries.sort(
        key=lambda row: (
            _normalize_sort_date(row['sort_date']),
            getattr(row['obj'], 'id', 0),
        ),
        reverse=True,
    )
    recent_entries = recent_entries[:24]
    return render_template(
        'elternkontakte_start.html',
        schueler=schueler,
        schueler_groups=selection['groups'],
        tab_definitions=selection['tab_definitions'],
        active_tab=selection['active_tab'],
        selected_student=selection['selected_student'],
        recent_entries=recent_entries,
        selected_s_id=selected_s_id,
    )


@erfassung_bp.route('/erfassen/elternberatung', methods=['GET', 'POST'])
@login_required
def elternberatung():
    next_url = (request.args.get('next') or request.form.get('next') or '').strip()
    selected_s_id = (request.args.get('schueler_id') or request.form.get('schueler_id') or '').strip()
    tab = (request.args.get('tab') or request.form.get('tab') or '').strip()

    selection = get_tabbed_student_selection_for_user(
        current_user,
        selected_s_id=selected_s_id,
        requested_tab=tab,
        auto_select_first=False,
    )
    selected_student = selection['selected_student']

    if request.method == 'POST':
        datum_raw = (request.form.get('datum') or '').strip()
        anlass = (request.form.get('anlass') or '').strip()
        weitere_beratungspunkte = (request.form.get('weitere_beratungspunkte') or '').strip()
        vereinbarungen = (request.form.get('vereinbarungen') or '').strip()

        if not selected_student:
            flash('Bitte zuerst ein Kind auswählen.')
        elif not datum_raw:
            flash('Bitte ein Datum für das Elterngespräch eingeben.')
        else:
            try:
                datum_obj = datetime.strptime(datum_raw, '%Y-%m-%d').date()
            except ValueError:
                flash('Das Datum ist ungültig.')
            else:
                beratung = Elternberatung(
                    schueler_id=selected_student.id,
                    user_id=getattr(current_user, 'id', None),
                    datum=datum_obj,
                    anlass=anlass,
                    weitere_beratungspunkte=weitere_beratungspunkte,
                    vereinbarungen=vereinbarungen,
                )
                db.session.add(beratung)
                db.session.commit()
                flash('Dokumentation zum Elterngespräch gespeichert.')
                return redirect(url_for('erfassung.elternberatung_view', beratung_id=beratung.id, next=next_url))

    bogen_context = {'bogen_rows': [], 'symbol_map': {}, 'color_map': {}}
    plan_context = {'active_plan': None, 'last_evaluated_plan': None}
    if selected_student:
        bogen_context = _build_bogen_entries_for_student(selected_student.id)
        plan_context = _get_consultation_plan_context(selected_student.id)

    return render_template(
        'elternberatung_form.html',
        selected_student=selected_student,
        tab_definitions=selection['tab_definitions'],
        active_tab=selection['active_tab'],
        selected_s_id=selection['selected_s_id'],
        schueler=selection['students'],
        schueler_groups=selection['groups'],
        form_values={
            'datum': request.form.get('datum') or utc_now().date().isoformat(),
            'anlass': request.form.get('anlass') or '',
            'weitere_beratungspunkte': request.form.get('weitere_beratungspunkte') or '',
            'vereinbarungen': request.form.get('vereinbarungen') or '',
        },
        bogen_rows=bogen_context['bogen_rows'],
        symbol_map=bogen_context['symbol_map'],
        color_map=bogen_context['color_map'],
        active_plan=plan_context['active_plan'],
        last_evaluated_plan=plan_context['last_evaluated_plan'],
        next_url=next_url,
    )


@erfassung_bp.route('/erfassen/elternberatung/view/<int:beratung_id>')
@login_required
def elternberatung_view(beratung_id):
    next_url = (request.args.get('next') or '').strip()
    beratung = db.session.get(Elternberatung, beratung_id)
    if not beratung:
        abort(404)

    bogen_context = _build_bogen_entries_for_student(beratung.schueler_id)
    plan_context = _get_consultation_plan_context(beratung.schueler_id)

    return render_template(
        'elternberatung_view.html',
        beratung=beratung,
        selected_student=beratung.schueler,
        bogen_rows=bogen_context['bogen_rows'],
        symbol_map=bogen_context['symbol_map'],
        color_map=bogen_context['color_map'],
        active_plan=plan_context['active_plan'],
        last_evaluated_plan=plan_context['last_evaluated_plan'],
        next_url=next_url,
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
    if current_user.is_admin:
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
    overlay_mode = (request.args.get('overlay') or request.form.get('overlay') or '').strip() == '1'

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
                overlay_mode=overlay_mode,
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
        if overlay_mode:
            return render_template('overlay_close.html', payload={'type': 'parent-contact-created', 'kontaktId': kontakt.id})
        return redirect(_safe_next_url(next_url, url_for('erfassung.elternkontakte_start')))

    return render_template(
        'elternkontakt_notiz.html',
        schueler=schueler_liste,
        schueler_groups=get_grouped_student_choices_for_user(current_user),
        selected_s_id=request.args.get('schueler_id'),
        next_url=next_url,
        overlay_mode=overlay_mode,
    )


@erfassung_bp.route('/erfassen/elternkontakte/protokoll', methods=['GET', 'POST'])
@login_required
def elternkontakt_protokoll():
    schueler_liste = get_prioritized_students_for_user(current_user)
    next_url = (request.args.get('next') or request.form.get('next') or '').strip()
    overlay_mode = (request.args.get('overlay') or request.form.get('overlay') or '').strip() == '1'

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
                overlay_mode=overlay_mode,
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
        if overlay_mode:
            return render_template('overlay_close.html', payload={'type': 'parent-contact-created', 'kontaktId': kontakt.id})
        return redirect(_safe_next_url(next_url, url_for('erfassung.elternkontakte_start')))

    return render_template(
        'elternkontakt_protokoll.html',
        schueler=schueler_liste,
        schueler_groups=get_grouped_student_choices_for_user(current_user),
        selected_s_id=request.args.get('schueler_id'),
        next_url=next_url,
        overlay_mode=overlay_mode,
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
        overlay_mode=(request.args.get('overlay') or '').strip() == '1',
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
