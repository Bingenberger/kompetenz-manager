from datetime import datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from extensions import db
from competency_trend import compute_trend, summarize
from competency_matrix import (
    build_matrix,
    classes_with_students,
    most_documented_bogen,
    students_needing_attention,
    weakest_items,
)
from models import Beobachtung, Bogen, Item, Schueler
from school_year import active_school_year_start
from student_selection import (
    get_grouped_student_choices_for_user,
    get_prioritized_students_for_user,
    get_user_klassenkontext,
)
from uploads import loesche_upload_dateien


report_bp = Blueprint('report', __name__)
REPORT_UNDO_SESSION_KEY = 'report_last_deleted_beobachtung'


def _safe_next_url(candidate, fallback_url):
    value = (candidate or '').strip()
    if value.startswith('/') and not value.startswith('//'):
        return value
    return fallback_url


@report_bp.route('/report/schueler', methods=['GET'])
@login_required
def report_schueler():
    s_id = (request.args.get('schueler_id') or '').strip()
    b_id = (request.args.get('bogen_id') or '').strip()
    next_url = (request.args.get('next') or '').strip()

    if not s_id or not b_id:
        return render_template(
            'report_select.html',
            schueler=get_prioritized_students_for_user(current_user, include_archived=True),
            schueler_groups=get_grouped_student_choices_for_user(current_user, include_archived=True),
            boegen=Bogen.query.all(),
            selected_s_id=s_id,
            selected_b_id=b_id,
            next_url=next_url,
        )

    schueler = db.session.get(Schueler, int(s_id))
    bogen = db.session.get(Bogen, int(b_id))
    items = Item.query.filter_by(bogen_id=b_id).all()

    symbol_map = {1: '-', 2: 'o', 3: '+', 4: '++'}
    color_map = {1: 'danger', 2: 'warning', 3: 'success', 4: 'success'}

    # Eine Abfrage für alle Kompetenzen statt einer je Kompetenz, und das
    # Schuljahr einmal statt in jedem Schleifendurchlauf.
    school_year_start = active_school_year_start()
    alle_eintraege = (
        Beobachtung.query
        .filter(
            Beobachtung.schueler_id == int(s_id),
            Beobachtung.item_id.in_([item.id for item in items]),
        )
        .order_by(Beobachtung.datum.desc())
        .all()
    ) if items else []

    eintraege_je_item = {}
    for eintrag in alle_eintraege:
        eintraege_je_item.setdefault(eintrag.item_id, []).append(eintrag)

    report_data = []
    for item in items:
        eintraege = eintraege_je_item.get(item.id, [])
        aktuelle_eintraege = [
            entry for entry in eintraege
            if not school_year_start or (entry.datum and entry.datum.date() >= school_year_start)
        ]
        werte = [e.wert for e in aktuelle_eintraege if e.wert is not None]
        durchschnitt = round(sum(werte) / len(werte), 1) if werte else 0

        report_data.append({
            'item': item,
            'eintraege': eintraege,
            'anzahl': len(aktuelle_eintraege),
            'durchschnitt': durchschnitt,
            'trend': compute_trend(aktuelle_eintraege),
        })

    trend_summary = summarize([zeile['trend'] for zeile in report_data])

    jetzt = datetime.now().strftime("%d.%m.%Y %H:%M")

    return render_template(
        'report_view.html',
        schueler=schueler,
        bogen=bogen,
        report_data=report_data,
        symbol_map=symbol_map,
        color_map=color_map,
        trend_summary=trend_summary,
        now=jetzt,
        next_url=next_url,
    )


def _default_klasse_for_user(user, klassen):
    """Eigene Klasse zuerst, dann Fachklasse, sonst die erste vorhandene."""
    kontext = get_user_klassenkontext(user)
    if kontext['klassenleitung'] in klassen:
        return kontext['klassenleitung']
    for klasse in sorted(kontext['fachklassen'], key=lambda wert: wert.lower()):
        if klasse in klassen:
            return klasse
    return klassen[0] if klassen else ''


@report_bp.route('/report/matrix')
@login_required
def report_matrix():
    boegen = Bogen.query.order_by(Bogen.titel.asc()).all()

    show_archived = request.args.get('show') == 'archived'
    klasse = (request.args.get('klasse') or '').strip()
    bogen_id = (request.args.get('bogen_id') or '').strip()

    klassen = classes_with_students(include_archived=show_archived)
    # Eine ausdruecklich angefragte Klasse wird nicht stillschweigend getauscht,
    # auch wenn dort nur archivierte Kinder liegen - die Ansicht sagt dann, dass
    # der Archiv-Schalter fehlt, statt eine andere Klasse zu zeigen.
    if klasse and klasse not in klassen and klasse in classes_with_students(include_archived=True):
        klassen = sorted(set(klassen) | {klasse}, key=lambda wert: wert.lower())
    if klasse not in klassen:
        klasse = _default_klasse_for_user(current_user, klassen)

    bogen = None
    if bogen_id.isdigit():
        bogen = db.session.get(Bogen, int(bogen_id))
    if bogen is None and klasse:
        bogen = most_documented_bogen(klasse, boegen, include_archived=show_archived)
    if bogen is None and boegen:
        bogen = boegen[0]

    matrix = None
    if klasse and bogen:
        matrix = build_matrix(klasse, bogen, include_archived=show_archived)

    return render_template(
        'report_matrix.html',
        klassen=klassen,
        boegen=boegen,
        klasse=klasse,
        bogen=bogen,
        show_archived=show_archived,
        matrix=matrix,
        weakest=weakest_items(matrix) if matrix else [],
        attention=students_needing_attention(matrix) if matrix else [],
    )


@report_bp.route('/report/beobachtung/delete/<int:beobachtung_id>', methods=['POST'])
@login_required
def report_beobachtung_delete(beobachtung_id):
    eintrag = db.session.get(Beobachtung, beobachtung_id)
    if not eintrag:
        abort(404)

    schueler_id = eintrag.schueler_id
    item = db.session.get(Item, eintrag.item_id) if eintrag.item_id else None
    bogen_id = item.bogen_id if item else None

    next_url = (request.form.get('next') or '').strip()

    # Das Foto des Eintrags bleibt zunaechst liegen, damit "Wiederherstellen"
    # einen vollstaendigen Datensatz zurueckbringt. Erreichbar ist immer nur der
    # letzte geloeschte Eintrag - das Foto des davor verdraengten kann weg.
    superseded = session.get(REPORT_UNDO_SESSION_KEY) or {}
    superseded_foto = superseded.get('foto_pfad')
    # Vor dem Commit festhalten: danach ist der Eintrag abgelöst.
    aktuelles_foto = eintrag.foto_pfad

    session[REPORT_UNDO_SESSION_KEY] = {
        'datum': eintrag.datum.isoformat() if eintrag.datum else None,
        'wert': eintrag.wert,
        'kommentar': eintrag.kommentar,
        'foto_pfad': eintrag.foto_pfad,
        'anlass': eintrag.anlass,
        'schueler_id': eintrag.schueler_id,
        'item_id': eintrag.item_id,
        'next': next_url,
    }

    db.session.delete(eintrag)
    db.session.commit()

    if superseded_foto and superseded_foto != aktuelles_foto:
        loesche_upload_dateien([superseded_foto])

    flash('Beobachtungseintrag gelöscht.')

    if next_url:
        return redirect(_safe_next_url(next_url, url_for('report.report_schueler')))

    if schueler_id and bogen_id:
        return redirect(url_for('report.report_schueler', schueler_id=schueler_id, bogen_id=bogen_id))
    return redirect(url_for('report.report_schueler'))


@report_bp.route('/report/beobachtung/undo-delete', methods=['POST'])
@login_required
def report_beobachtung_undo_delete():
    payload = session.pop(REPORT_UNDO_SESSION_KEY, None)
    if not payload:
        flash('Kein Beobachtungseintrag zum Wiederherstellen vorhanden.')
        return redirect(url_for('report.report_schueler'))

    datum_raw = payload.get('datum')
    datum_obj = None
    if datum_raw:
        try:
            datum_obj = datetime.fromisoformat(datum_raw)
        except ValueError:
            datum_obj = None

    # Kind oder Kompetenz koennen zwischenzeitlich entfernt worden sein. Ohne
    # diese Pruefung scheitert das Wiederherstellen am Fremdschluessel.
    schueler_id = payload.get('schueler_id')
    item_id = payload.get('item_id')
    if schueler_id and not db.session.get(Schueler, schueler_id):
        flash('Das Kind zu diesem Eintrag existiert nicht mehr. Wiederherstellen ist nicht möglich.')
        return redirect(url_for('report.report_schueler'))
    if item_id and not db.session.get(Item, item_id):
        flash('Die Kompetenz zu diesem Eintrag existiert nicht mehr. Wiederherstellen ist nicht möglich.')
        return redirect(url_for('report.report_schueler'))

    eintrag = Beobachtung(
        datum=datum_obj,
        wert=payload.get('wert'),
        kommentar=payload.get('kommentar'),
        foto_pfad=payload.get('foto_pfad'),
        anlass=payload.get('anlass'),
        schueler_id=payload.get('schueler_id'),
        item_id=payload.get('item_id'),
    )
    db.session.add(eintrag)
    db.session.commit()
    flash('Beobachtungseintrag wiederhergestellt.')

    next_url = (payload.get('next') or '').strip()
    if next_url:
        return redirect(_safe_next_url(next_url, url_for('report.report_schueler')))

    if eintrag.schueler_id and eintrag.item_id:
        item = db.session.get(Item, eintrag.item_id)
        if item and item.bogen_id:
            return redirect(url_for('report.report_schueler', schueler_id=eintrag.schueler_id, bogen_id=item.bogen_id))
    return redirect(url_for('report.report_schueler'))


def register_report_routes(app):
    app.register_blueprint(report_bp)
