"""Förderkonferenz: Vorbereitung, Moderation, Abschluss, Evaluation.

Geschrieben wird feldweise über /konferenz/<id>/feld und
/konferenz/<id>/kind/<eintrag>/feld - die Oberfläche speichert jede Eingabe
für sich (static/js/konferenz.js). Jede dieser Routen prüft die Rechte selbst;
die Oberfläche blendet nur zusätzlich aus, was nicht erlaubt ist.
"""

from datetime import date, datetime

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from db_utils import get_or_404_session
from diagnostik import risikogrenzen
from extensions import db
from jahrgang import JAHRGAENGE
from konferenz import (
    BERATUNGSSCHRITTE,
    EVAL_WIRKSAM,
    KIND_FELDER,
    KIND_FELDER_VORSCHLAG,
    KONFERENZ_FELDER,
    LETZTE_PHASE,
    MASSNAHMEN,
    PHASEN,
    PHASEN_NAMEN,
    STUFEN,
    STUFEN_REIHENFOLGE,
    abgleich_je_kind,
    darf_feld_schreiben,
    darf_lesen,
    darf_moderieren,
    dateiname,
    eigene_kinder,
    ergaenze_kinder,
    erstelle_konferenz,
    ist_klassenleitung,
    kind_kontext,
    kinder_der_phase,
    offen_in_phase,
    offene_beschluesse,
    phasen_uebersicht,
    protokoll_bloecke,
    protokolliere,
    pruefe_vollstaendigkeit,
    richtzeit,
    schreibe_feld,
    setze_anwesenheit,
    sichtbare_kinder,
    sortiere,
    teilnehmende,
    zaehle_stufen,
)
from models import Foerderkonferenz, FoerderkonferenzKind, SystemKonfiguration, User
from odt_export import build_odt_document, convert_odt_bytes_to_pdf
from school_year import normalize_school_year
from time_utils import utc_now

konferenz_bp = Blueprint('konferenz', __name__)


def _konferenz_oder_404(konferenz_id):
    return get_or_404_session(Foerderkonferenz, konferenz_id)


def _lesen_oder_403(konferenz_id):
    konferenz = _konferenz_oder_404(konferenz_id)
    if not darf_lesen(current_user, konferenz):
        abort(403)
    return konferenz


def _moderieren_oder_403(konferenz_id):
    konferenz = _konferenz_oder_404(konferenz_id)
    if not darf_moderieren(current_user, konferenz):
        abort(403)
    return konferenz


def _aktuelles_schuljahr():
    config = SystemKonfiguration.query.first()
    return config.schuljahr if config and config.schuljahr else None


def _lehrkraefte():
    return User.query.order_by(User.nachname, User.vorname, User.username).all()


def _safe_next_url(candidate, fallback_url):
    wert = (candidate or '').strip()
    return wert if wert.startswith('/') and not wert.startswith('//') else fallback_url


# ----------------------------------------------------------------------
# Übersicht und Anlegen
# ----------------------------------------------------------------------

@konferenz_bp.route('/konferenz')
@login_required
def liste():
    konferenzen = Foerderkonferenz.query.order_by(
        Foerderkonferenz.schuljahr.desc(), Foerderkonferenz.termin.desc().nullslast(), Foerderkonferenz.id.desc(),
    ).all()
    sichtbar = []
    for konferenz in konferenzen:
        if not darf_lesen(current_user, konferenz):
            continue
        eigene = eigene_kinder(current_user, konferenz)
        sichtbar.append({
            'konferenz': konferenz,
            'zaehler': zaehle_stufen(konferenz),
            'anzahl': len(konferenz.kinder),
            'eigene': len(eigene),
            'eigene_offen': sum(1 for e in eigene if not e.vorschlag_stufe),
        })
    return render_template(
        'konferenz_liste.html',
        zeilen=sichtbar,
        stufen=STUFEN,
        darf_anlegen=darf_moderieren(current_user),
        jahrgaenge=JAHRGAENGE,
        schuljahr=_aktuelles_schuljahr(),
        heute=utc_now().date(),
        aufgaben=offene_beschluesse(current_user),
    )


@konferenz_bp.route('/konferenz/neu', methods=['POST'])
@login_required
def neu():
    if not darf_moderieren(current_user):
        abort(403)
    schuljahr = normalize_school_year(request.form.get('schuljahr')) or _aktuelles_schuljahr()
    try:
        jahrgang = int(request.form.get('jahrgang') or 0)
    except ValueError:
        jahrgang = 0
    titel = (request.form.get('titel') or '').strip()
    termin_roh = (request.form.get('termin') or '').strip()
    try:
        termin = date.fromisoformat(termin_roh) if termin_roh else None
    except ValueError:
        termin = None

    if jahrgang not in JAHRGAENGE or not schuljahr:
        flash('Bitte Schuljahr und Jahrgangsstufe angeben.')
        return redirect(url_for('konferenz.liste'))
    if not titel:
        titel = f'Förderkonferenz Jahrgang {jahrgang} ({schuljahr})'

    konferenz = erstelle_konferenz(schuljahr, jahrgang, titel, termin, current_user)
    db.session.commit()
    if not konferenz.kinder:
        flash(f'Im Jahrgang {jahrgang} sind keine aktiven Kinder eingetragen.')
    flash(f'{titel} angelegt: {len(konferenz.kinder)} Kind(er).')
    return redirect(url_for('konferenz.start', konferenz_id=konferenz.id))


@konferenz_bp.route('/konferenz/<int:konferenz_id>')
@login_required
def start(konferenz_id):
    konferenz = _lesen_oder_403(konferenz_id)
    eigene = eigene_kinder(current_user, konferenz)
    return render_template(
        'konferenz_start.html',
        konferenz=konferenz,
        phasen=phasen_uebersicht(konferenz),
        zaehler=zaehle_stufen(konferenz),
        kinder=sichtbare_kinder(current_user, konferenz),
        eigene=eigene,
        eigene_offen=sum(1 for e in eigene if not e.vorschlag_stufe),
        stufen=STUFEN,
        moderiert=darf_moderieren(current_user),
        hinweise=pruefe_vollstaendigkeit(konferenz) if darf_moderieren(current_user) else [],
        heute=utc_now().date(),
    )


@konferenz_bp.route('/konferenz/<int:konferenz_id>/loeschen', methods=['POST'])
@login_required
def loeschen(konferenz_id):
    """Löscht die Konferenz mit allen Einträgen, Teilnahmen und dem Protokoll."""
    konferenz = _moderieren_oder_403(konferenz_id)
    if (request.form.get('bestaetigung') or '').strip() != 'LÖSCHEN':
        flash('Zum Löschen bitte LÖSCHEN in das Feld eintragen.')
        return redirect(url_for('konferenz.start', konferenz_id=konferenz.id))
    titel = konferenz.titel
    db.session.delete(konferenz)
    db.session.commit()
    flash(f'„{titel}“ wurde mit allen Einträgen gelöscht.')
    return redirect(url_for('konferenz.liste'))


@konferenz_bp.route('/konferenz/<int:konferenz_id>/stufen')
@login_required
def stufen_erfassen(konferenz_id):
    """Alle Kinder auf einer Seite - für Stufen, die auf Papier vorbereitet wurden."""
    konferenz = _moderieren_oder_403(konferenz_id)
    eintraege = sortiere(konferenz.kinder)
    klassen = {}
    for eintrag in eintraege:
        klassen.setdefault(eintrag.klasse or '–', []).append(eintrag)
    return render_template(
        'konferenz_stufen.html',
        konferenz=konferenz,
        klassen=sorted(klassen.items()),
        phasen=phasen_uebersicht(konferenz),
        zaehler=zaehle_stufen(konferenz),
        stufen=STUFEN,
        stufen_reihenfolge=STUFEN_REIHENFOLGE,
        schreibbar=not konferenz.abgeschlossen,
    )


@konferenz_bp.route('/konferenz/<int:konferenz_id>/kinder-ergaenzen', methods=['POST'])
@login_required
def kinder_ergaenzen(konferenz_id):
    konferenz = _moderieren_oder_403(konferenz_id)
    if konferenz.abgeschlossen:
        abort(403)
    anzahl = ergaenze_kinder(konferenz)
    db.session.commit()
    flash(f'{anzahl} Kind(er) ergänzt.' if anzahl else 'Es sind alle Kinder des Jahrgangs eingetragen.')
    return redirect(url_for('konferenz.start', konferenz_id=konferenz.id))


# ----------------------------------------------------------------------
# Vorbereitung durch die Klassenleitung
# ----------------------------------------------------------------------

@konferenz_bp.route('/konferenz/<int:konferenz_id>/vorbereitung')
@login_required
def vorbereitung(konferenz_id):
    konferenz = _lesen_oder_403(konferenz_id)
    eintraege = eigene_kinder(current_user, konferenz)
    if not eintraege and darf_moderieren(current_user):
        eintraege = sichtbare_kinder(current_user, konferenz)
    if not eintraege:
        flash('In dieser Konferenz sind keine Kinder Ihrer Klasse.')
        return redirect(url_for('konferenz.start', konferenz_id=konferenz.id))
    return render_template(
        'konferenz_vorbereitung.html',
        konferenz=konferenz,
        eintraege=eintraege,
        stufen=STUFEN,
        stufen_reihenfolge=STUFEN_REIHENFOLGE,
        schreibbar=not konferenz.abgeschlossen,
    )


# ----------------------------------------------------------------------
# Moderation: Phasen
# ----------------------------------------------------------------------

@konferenz_bp.route('/konferenz/<int:konferenz_id>/phase/<int:phase>')
@login_required
def phase(konferenz_id, phase):
    konferenz = _konferenz_oder_404(konferenz_id)
    if not darf_moderieren(current_user, konferenz):
        abort(403)
    if phase < 1 or phase > LETZTE_PHASE:
        abort(404)

    kinder = kinder_der_phase(konferenz, phase)
    aktuelles = None
    gewaehlt = request.args.get('kind', type=int)
    if kinder:
        aktuelles = next((e for e in kinder if e.id == gewaehlt), None)
        if aktuelles is None and konferenz.aktuelles_kind_id:
            aktuelles = next((e for e in kinder if e.schueler_id == konferenz.aktuelles_kind_id), None)
        aktuelles = aktuelles or kinder[0]

    # Merken, wo die Moderation steht - beim naechsten Oeffnen geht es hier weiter.
    if not konferenz.abgeschlossen:
        konferenz.aktuelle_phase = phase
        if aktuelles and phase in (4, 5):
            konferenz.aktuelles_kind_id = aktuelles.schueler_id
        db.session.commit()

    kontext = kind_kontext(aktuelles, current_user) if aktuelles and phase in (4, 5) else None
    a_block = {}
    if phase == 3:
        a_kinder = kinder
        warnungen = abgleich_je_kind(a_kinder, risikogrenzen())
        klassen = {}
        for eintrag in a_kinder:
            klassen.setdefault(eintrag.klasse or '–', []).append(eintrag)
        a_block = {'klassen': sorted(klassen.items()), 'warnungen': warnungen}

    return render_template(
        'konferenz_phase.html',
        konferenz=konferenz,
        phase=phase,
        phasen=phasen_uebersicht(konferenz),
        phase_name=PHASEN_NAMEN[phase],
        richtzeit=richtzeit(konferenz, phase),
        offen=offen_in_phase(konferenz, phase),
        kinder=kinder,
        aktuelles=aktuelles,
        kontext=kontext,
        a_block=a_block,
        alle_kinder=sortiere(konferenz.kinder),
        teilnehmende=teilnehmende(konferenz) if phase == 1 else [],
        lehrkraefte=_lehrkraefte(),
        stufen=STUFEN,
        stufen_reihenfolge=STUFEN_REIHENFOLGE,
        schritte=BERATUNGSSCHRITTE,
        massnahmen=MASSNAHMEN,
        zaehler=zaehle_stufen(konferenz),
        hinweise=pruefe_vollstaendigkeit(konferenz),
        kurse=[e for e in sortiere(konferenz.kinder) if e.massnahme_foerderkurs],
        beschluesse=[e for e in sortiere(konferenz.kinder) if e.stufe in ('B', 'C')],
        schreibbar=not konferenz.abgeschlossen,
        letzte_phase=LETZTE_PHASE,
    )


# ----------------------------------------------------------------------
# Feldweises Speichern
# ----------------------------------------------------------------------

def _antwort(objekt, feld, wert, konflikt=False, bekannt=None):
    return jsonify({
        'ok': True,
        'feld': feld,
        'wert': wert.isoformat() if isinstance(wert, (date, datetime)) else wert,
        'bearbeitet_am': objekt.bearbeitet_am.isoformat() if objekt.bearbeitet_am else None,
        'gespeichert_um': utc_now().strftime('%H:%M'),
        'konflikt': konflikt,
        'bekannt_am': bekannt,
    })


def _konflikt(objekt, bekannt_am):
    """True, wenn die Zeile seit dem letzten Stand dieses Geräts geändert wurde."""
    if not bekannt_am or not objekt.bearbeitet_am:
        return False
    try:
        bekannt = datetime.fromisoformat(bekannt_am)
    except ValueError:
        return False
    if bekannt.tzinfo is not None:
        bekannt = bekannt.replace(tzinfo=None)
    gespeichert = objekt.bearbeitet_am.replace(tzinfo=None) if objekt.bearbeitet_am.tzinfo else objekt.bearbeitet_am
    return (gespeichert - bekannt).total_seconds() > 1


def _daten():
    return request.get_json(silent=True) or request.form


@konferenz_bp.route('/konferenz/<int:konferenz_id>/feld', methods=['POST'])
@login_required
def konferenz_feld(konferenz_id):
    konferenz = _konferenz_oder_404(konferenz_id)
    daten = _daten()
    feld = (daten.get('feld') or '').strip()
    if feld not in KONFERENZ_FELDER:
        return jsonify({'ok': False, 'fehler': 'Unbekanntes Feld.'}), 400
    if not darf_feld_schreiben(current_user, konferenz, feld):
        return jsonify({'ok': False, 'fehler': 'Keine Berechtigung.'}), 403
    konflikt = _konflikt(konferenz, daten.get('bekannt_am'))
    wert = schreibe_feld(konferenz, feld, daten.get('wert'), current_user)
    db.session.commit()
    return _antwort(konferenz, feld, wert, konflikt)


@konferenz_bp.route('/konferenz/<int:konferenz_id>/kind/<int:eintrag_id>/feld', methods=['POST'])
@login_required
def kind_feld(konferenz_id, eintrag_id):
    konferenz = _konferenz_oder_404(konferenz_id)
    eintrag = get_or_404_session(FoerderkonferenzKind, eintrag_id)
    if eintrag.konferenz_id != konferenz.id:
        abort(404)
    daten = _daten()
    feld = (daten.get('feld') or '').strip()
    if feld not in KIND_FELDER:
        return jsonify({'ok': False, 'fehler': 'Unbekanntes Feld.'}), 400
    if not darf_feld_schreiben(current_user, konferenz, feld, eintrag):
        return jsonify({'ok': False, 'fehler': 'Keine Berechtigung.'}), 403
    konflikt = _konflikt(eintrag, daten.get('bekannt_am'))
    wert = schreibe_feld(eintrag, feld, daten.get('wert'), current_user)
    db.session.commit()
    return _antwort(eintrag, feld, wert, konflikt)


@konferenz_bp.route('/konferenz/<int:konferenz_id>/teilnahme', methods=['POST'])
@login_required
def teilnahme(konferenz_id):
    konferenz = _konferenz_oder_404(konferenz_id)
    if not darf_moderieren(current_user, konferenz) or konferenz.abgeschlossen:
        return jsonify({'ok': False, 'fehler': 'Keine Berechtigung.'}), 403
    daten = _daten()
    try:
        user_id = int(daten.get('user_id'))
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'fehler': 'Unbekannte Person.'}), 400
    anwesend = str(daten.get('wert')).strip().lower() in ('1', 'true', 'ja', 'on')
    setze_anwesenheit(konferenz, user_id, anwesend)
    db.session.commit()
    return _antwort(konferenz, 'teilnahme', anwesend)


@konferenz_bp.route('/konferenz/<int:konferenz_id>/a-block', methods=['POST'])
@login_required
def a_block_bestaetigen(konferenz_id):
    """Sammelbestätigung: alle A-Vorschläge einer Klasse werden Stufe A."""
    konferenz = _moderieren_oder_403(konferenz_id)
    if konferenz.abgeschlossen:
        abort(403)
    klasse = (request.form.get('klasse') or '').strip()
    anzahl = 0
    for eintrag in kinder_der_phase(konferenz, 3):
        if klasse and (eintrag.klasse or '–') != klasse:
            continue
        if eintrag.stufe != 'A':
            eintrag.stufe = 'A'
            eintrag.bearbeitet_am = utc_now()
            eintrag.bearbeitet_von_user_id = current_user.id
            anzahl += 1
    db.session.commit()
    flash(f'{anzahl} Kind(er) auf A bestätigt.' if anzahl else 'Es war nichts offen.')
    return redirect(url_for('konferenz.phase', konferenz_id=konferenz.id, phase=3))


# ----------------------------------------------------------------------
# Status
# ----------------------------------------------------------------------

@konferenz_bp.route('/konferenz/<int:konferenz_id>/status', methods=['POST'])
@login_required
def status(konferenz_id):
    konferenz = _moderieren_oder_403(konferenz_id)
    aktion = (request.form.get('aktion') or '').strip()
    if aktion == 'starten' and konferenz.status == 'geplant':
        konferenz.status = 'laufend'
        konferenz.aktuelle_phase = konferenz.aktuelle_phase or 1
        protokolliere(konferenz, current_user, 'gestartet')
        db.session.commit()
        return redirect(url_for('konferenz.phase', konferenz_id=konferenz.id, phase=konferenz.aktuelle_phase))
    if aktion == 'abschliessen' and konferenz.status != 'abgeschlossen':
        konferenz.status = 'abgeschlossen'
        konferenz.abgeschlossen_am = utc_now()
        hinweise = pruefe_vollstaendigkeit(konferenz)
        protokolliere(konferenz, current_user, 'abgeschlossen', '; '.join(hinweise) or None)
        db.session.commit()
        flash('Konferenz abgeschlossen. Die Beschlüsse erscheinen jetzt als Aufgaben.')
        return redirect(url_for('konferenz.start', konferenz_id=konferenz.id))
    if aktion == 'oeffnen' and konferenz.status == 'abgeschlossen':
        grund = (request.form.get('grund') or '').strip()
        konferenz.status = 'laufend'
        konferenz.abgeschlossen_am = None
        protokolliere(konferenz, current_user, 'wieder geöffnet', grund or None)
        db.session.commit()
        flash('Konferenz wieder geöffnet. Der Vorgang steht im Protokoll.')
        return redirect(url_for('konferenz.start', konferenz_id=konferenz.id))
    flash('Diese Aktion ist im aktuellen Status nicht möglich.')
    return redirect(url_for('konferenz.start', konferenz_id=konferenz.id))


# ----------------------------------------------------------------------
# Aufgaben aus Beschlüssen
# ----------------------------------------------------------------------

@konferenz_bp.route('/konferenz/aufgaben')
@login_required
def aufgaben():
    alle = request.args.get('alle') == '1'
    eintraege = offene_beschluesse(current_user, alle=alle)
    return render_template(
        'konferenz_aufgaben.html',
        eintraege=eintraege,
        alle=alle,
        stufen=STUFEN,
        heute=utc_now().date(),
    )


@konferenz_bp.route('/konferenz/beschluss/<int:eintrag_id>/erledigt', methods=['POST'])
@login_required
def beschluss_erledigt(eintrag_id):
    eintrag = get_or_404_session(FoerderkonferenzKind, eintrag_id)
    zustaendig = (
        eintrag.verantwortlich_user_id == current_user.id
        or ist_klassenleitung(current_user, eintrag.klasse)
        or ist_klassenleitung(current_user, eintrag.schueler.klasse)
        or darf_moderieren(current_user)
    )
    if not zustaendig:
        abort(403)
    eintrag.erledigt_am = None if request.form.get('aktion') == 'offen' else utc_now().date()
    db.session.commit()
    return redirect(_safe_next_url(request.form.get('next'), url_for('konferenz.aufgaben')))


# ----------------------------------------------------------------------
# Evaluation in der Zeugniskonferenz
# ----------------------------------------------------------------------

@konferenz_bp.route('/konferenz/<int:konferenz_id>/evaluation')
@login_required
def evaluation(konferenz_id):
    konferenz = _lesen_oder_403(konferenz_id)
    eintraege = [e for e in sichtbare_kinder(current_user, konferenz) if e.stufe in ('B', 'C')]
    return render_template(
        'konferenz_evaluation.html',
        konferenz=konferenz,
        eintraege=eintraege,
        stufen=STUFEN,
        stufen_reihenfolge=STUFEN_REIHENFOLGE,
        wirksam=EVAL_WIRKSAM,
        schreibbar=darf_moderieren(current_user) and not konferenz.abgeschlossen,
        moderiert=darf_moderieren(current_user),
    )


# ----------------------------------------------------------------------
# Protokoll
# ----------------------------------------------------------------------

@konferenz_bp.route('/konferenz/<int:konferenz_id>/export/<string:format>')
@login_required
def export(konferenz_id, format):
    konferenz = _lesen_oder_403(konferenz_id)
    if format not in ('odt', 'pdf'):
        abort(404)
    anonym = request.args.get('fassung') == 'anonym'
    if not anonym and not darf_moderieren(current_user, konferenz):
        # Die interne Fassung nennt alle Kinder des Jahrgangs.
        abort(403)
    puffer = build_odt_document(protokoll_bloecke(konferenz, anonym=anonym))
    stamm = dateiname(konferenz, anonym)
    if format == 'odt':
        return send_file(puffer, as_attachment=True, download_name=f'{stamm}.odt',
                         mimetype='application/vnd.oasis.opendocument.text')
    try:
        pdf = convert_odt_bytes_to_pdf(puffer)
    except RuntimeError as exc:
        flash(f'PDF-Export fehlgeschlagen: {exc}. Der ODT-Export funktioniert unabhängig davon.')
        return redirect(url_for('konferenz.start', konferenz_id=konferenz.id))
    return send_file(pdf, as_attachment=True, download_name=f'{stamm}.pdf', mimetype='application/pdf')


def register_konferenz_routes(app):
    app.register_blueprint(konferenz_bp)
