"""Standardisierte Diagnostik: Katalog (Verwaltung), Eingabe und Auswertung."""

import json
from collections import Counter
from datetime import date, datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from authz import admin_required
from db_utils import get_or_404_session
from diagnostik import (
    HALBJAHRE,
    STUFEN,
    WERTART_BEREICH,
    WERTARTEN,
    aktuelles_halbjahr,
    auswerten,
    datum_im_schuljahr,
    jahre_zurueck,
    jahrgang_im_schuljahr,
    schuljahr_auswahl,
    schuljahr_zeitraum,
    UNVERAENDERT,
    klassen_uebersicht,
    risikogrenzen,
    speichere_ergebnis,
    zeitlabel,
    zeitpunkte_fuer,
)
from diagnostik_import import ImportDatei, ImportFehler, ImportZeile, lese_import, ordne_kinder_zu
from extensions import db
from jahrgang import JAHRGAENGE, klassen_jahrgaenge
from school_year import normalize_school_year
from student_selection import get_distinct_klassen, get_user_klassenkontext
from transition_plan import effective_jahrgang
from models import (
    DiagnostikErgebnis,
    DiagnostikKennwert,
    DiagnostikTestform,
    DiagnostikVerfahren,
    DiagnostikWert,
    DiagnostikZeitpunkt,
    Schueler,
    SystemKonfiguration,
)

diagnostik_bp = Blueprint('diagnostik', __name__)

ADMIN_MELDUNG = 'Zugriff verweigert. Nur der Administrator darf den Diagnostik-Katalog pflegen.'


def _konfiguration():
    config = SystemKonfiguration.query.first()
    if not config:
        config = SystemKonfiguration()
        db.session.add(config)
        db.session.flush()
    return config


def _int_oder_none(raw, minimum=None, maximum=None):
    """(Zahl oder None, gültig?) - leer ist gültig und ergibt None."""
    text = (raw or '').strip()
    if not text:
        return None, True
    try:
        zahl = int(text)
    except ValueError:
        return None, False
    if (minimum is not None and zahl < minimum) or (maximum is not None and zahl > maximum):
        return None, False
    return zahl, True


def _anzahl_ergebnisse(testform_ids):
    if not testform_ids:
        return 0
    return DiagnostikErgebnis.query.filter(DiagnostikErgebnis.testform_id.in_(testform_ids)).count()


# ----------------------------------------------------------------------
# Katalog: Übersicht und Risikogrenzen
# ----------------------------------------------------------------------

@diagnostik_bp.route('/admin/diagnostik')
@admin_required(redirect_endpoint='admin.admin_dashboard', message=ADMIN_MELDUNG)
def admin_katalog():
    verfahren = DiagnostikVerfahren.query.order_by(DiagnostikVerfahren.sort_order, DiagnostikVerfahren.name).all()
    ergebnisse_je_testform = dict(
        db.session.query(DiagnostikErgebnis.testform_id, db.func.count(DiagnostikErgebnis.id))
        .group_by(DiagnostikErgebnis.testform_id).all()
    )
    return render_template(
        'admin_diagnostik.html',
        verfahren=verfahren,
        grenzen=risikogrenzen(SystemKonfiguration.query.first()),
        stufen=STUFEN,
        ergebnisse_je_testform=ergebnisse_je_testform,
        wertart_kuerzel={schluessel: kuerzel for schluessel, _, kuerzel, _, _ in WERTARTEN},
    )


@diagnostik_bp.route('/admin/diagnostik/grenzen', methods=['POST'])
@admin_required(redirect_endpoint='admin.admin_dashboard', message=ADMIN_MELDUNG)
def admin_grenzen():
    werte = {}
    for stufe in ('beobachten', 'auffaellig', 'deutlich'):
        zahl, gueltig = _int_oder_none(request.form.get(stufe), 0, 100)
        if not gueltig:
            flash('Die Grenzen müssen Prozentränge zwischen 0 und 100 sein.')
            return redirect(url_for('diagnostik.admin_katalog'))
        werte[stufe] = zahl
    gesetzt = [werte[s] for s in ('deutlich', 'auffaellig', 'beobachten') if werte[s] is not None]
    if gesetzt != sorted(gesetzt) or len(set(gesetzt)) != len(gesetzt):
        flash('Die Grenzen müssen aufsteigen: deutlich auffällig < auffällig < beobachten.')
        return redirect(url_for('diagnostik.admin_katalog'))
    config = _konfiguration()
    config.diagnostik_pr_beobachten = werte['beobachten']
    config.diagnostik_pr_auffaellig = werte['auffaellig']
    config.diagnostik_pr_deutlich = werte['deutlich']
    db.session.commit()
    flash('Risikogrenzen gespeichert.')
    return redirect(url_for('diagnostik.admin_katalog'))


# ----------------------------------------------------------------------
# Verfahren
# ----------------------------------------------------------------------

@diagnostik_bp.route('/admin/diagnostik/verfahren/neu', methods=['GET', 'POST'], defaults={'verfahren_id': None})
@diagnostik_bp.route('/admin/diagnostik/verfahren/<int:verfahren_id>', methods=['GET', 'POST'])
@admin_required(redirect_endpoint='admin.admin_dashboard', message=ADMIN_MELDUNG)
def admin_verfahren(verfahren_id):
    verfahren = get_or_404_session(DiagnostikVerfahren, verfahren_id) if verfahren_id else DiagnostikVerfahren()

    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        bereich = (request.form.get('bereich') or '').strip()
        doppelt = DiagnostikVerfahren.query.filter(
            DiagnostikVerfahren.name == name, DiagnostikVerfahren.id != (verfahren.id or 0),
        ).first()
        if not name or not bereich:
            flash('Bitte Name und Lernbereich angeben.')
        elif doppelt:
            flash(f'Ein Verfahren „{name}“ gibt es bereits.')
        else:
            verfahren.name = name[:100]
            verfahren.bereich = bereich[:50]
            verfahren.beschreibung = (request.form.get('beschreibung') or '').strip() or None
            verfahren.is_active = request.form.get('is_active') == '1'
            if not verfahren.id:
                verfahren.sort_order = (db.session.query(db.func.max(DiagnostikVerfahren.sort_order)).scalar() or 0) + 1
                db.session.add(verfahren)
            db.session.commit()
            flash(f'Verfahren „{verfahren.name}“ gespeichert.')
            return redirect(url_for('diagnostik.admin_verfahren', verfahren_id=verfahren.id))

    bereiche = sorted({b for (b,) in db.session.query(DiagnostikVerfahren.bereich).distinct()} | {'Lesen', 'Rechtschreiben', 'Mathematik'})
    return render_template(
        'admin_diagnostik_verfahren.html',
        verfahren=verfahren,
        bereiche=bereiche,
        ergebnisse=_anzahl_ergebnisse([t.id for t in verfahren.testformen]) if verfahren.id else 0,
    )


@diagnostik_bp.route('/admin/diagnostik/verfahren/<int:verfahren_id>/loeschen', methods=['POST'])
@admin_required(redirect_endpoint='admin.admin_dashboard', message=ADMIN_MELDUNG)
def admin_verfahren_loeschen(verfahren_id):
    verfahren = get_or_404_session(DiagnostikVerfahren, verfahren_id)
    anzahl = _anzahl_ergebnisse([t.id for t in verfahren.testformen])
    if anzahl:
        flash(
            f'„{verfahren.name}“ hat {anzahl} eingetragene Ergebnis(se) und kann nicht gelöscht werden. '
            'Stattdessen deaktivieren – dann erscheint es nicht mehr bei der Eingabe.'
        )
        return redirect(url_for('diagnostik.admin_verfahren', verfahren_id=verfahren.id))
    name = verfahren.name
    db.session.delete(verfahren)
    db.session.commit()
    flash(f'Verfahren „{name}“ gelöscht.')
    return redirect(url_for('diagnostik.admin_katalog'))


# ----------------------------------------------------------------------
# Testformen mit Kennwerten und Testplan
# ----------------------------------------------------------------------

NEUE_KENNWERT_ZEILEN = 2


@diagnostik_bp.route('/admin/diagnostik/verfahren/<int:verfahren_id>/testform/neu', methods=['GET', 'POST'], defaults={'testform_id': None})
@diagnostik_bp.route('/admin/diagnostik/verfahren/<int:verfahren_id>/testform/<int:testform_id>', methods=['GET', 'POST'])
@admin_required(redirect_endpoint='admin.admin_dashboard', message=ADMIN_MELDUNG)
def admin_testform(verfahren_id, testform_id):
    verfahren = get_or_404_session(DiagnostikVerfahren, verfahren_id)
    if testform_id:
        testform = get_or_404_session(DiagnostikTestform, testform_id)
        if testform.verfahren_id != verfahren.id:
            return redirect(url_for('diagnostik.admin_verfahren', verfahren_id=verfahren.id))
    else:
        testform = DiagnostikTestform(verfahren_id=verfahren.id)

    if request.method == 'POST':
        fehler = _speichere_testform(verfahren, testform)
        if not fehler:
            db.session.commit()
            flash(f'Testform „{testform.name}“ gespeichert.')
            return redirect(url_for('diagnostik.admin_testform', verfahren_id=verfahren.id, testform_id=testform.id))
        db.session.rollback()
        for meldung in fehler:
            flash(meldung)
        if testform_id:
            testform = db.session.get(DiagnostikTestform, testform_id)
        else:
            testform = DiagnostikTestform(verfahren_id=verfahren.id)

    benutzte_kennwerte = set()
    if testform.id:
        benutzte_kennwerte = {
            kennwert_id for (kennwert_id,) in db.session.query(DiagnostikWert.kennwert_id)
            .filter(DiagnostikWert.kennwert_id.in_([k.id for k in testform.kennwerte] or [0])).distinct()
        }
    return render_template(
        'admin_diagnostik_testform.html',
        verfahren=verfahren,
        testform=testform,
        jahrgaenge=JAHRGAENGE,
        halbjahre=HALBJAHRE,
        geplant={(z.jahrgang, z.halbjahr) for z in testform.zeitpunkte} if testform.id else set(),
        wertarten=WERTARTEN,
        benutzte_kennwerte=benutzte_kennwerte,
        neue_zeilen=range(NEUE_KENNWERT_ZEILEN),
        ergebnisse=_anzahl_ergebnisse([testform.id]) if testform.id else 0,
    )


def _speichere_testform(verfahren, testform):
    """Übernimmt das Formular in die Testform. Gibt Fehlermeldungen zurück."""
    name = (request.form.get('name') or '').strip()
    if not name:
        return ['Bitte einen Namen für die Testform angeben.']
    testform.name = name[:100]
    testform.is_active = request.form.get('is_active') == '1'
    if not testform.id:
        testform.verfahren_id = verfahren.id
        testform.sort_order = len(verfahren.testformen)
        db.session.add(testform)
        db.session.flush()

    # Testplan
    gewuenscht = set()
    for eintrag in request.form.getlist('zeitpunkte'):
        jahrgang, _, halbjahr = eintrag.partition(':')
        if jahrgang.isdigit() and int(jahrgang) in JAHRGAENGE and halbjahr in HALBJAHRE:
            gewuenscht.add((int(jahrgang), halbjahr))
    for zeitpunkt in list(testform.zeitpunkte):
        if (zeitpunkt.jahrgang, zeitpunkt.halbjahr) not in gewuenscht:
            testform.zeitpunkte.remove(zeitpunkt)
    vorhanden = {(z.jahrgang, z.halbjahr) for z in testform.zeitpunkte}
    for jahrgang, halbjahr in sorted(gewuenscht - vorhanden):
        testform.zeitpunkte.append(DiagnostikZeitpunkt(jahrgang=jahrgang, halbjahr=halbjahr))

    # Kennwerte
    fehler = []
    benutzt = {
        kennwert_id for (kennwert_id,) in db.session.query(DiagnostikWert.kennwert_id)
        .filter(DiagnostikWert.kennwert_id.in_([k.id for k in testform.kennwerte] or [0])).distinct()
    }
    for kennwert in list(testform.kennwerte):
        praefix = f'kennwert_{kennwert.id}_'
        if request.form.get(praefix + 'loeschen') == '1':
            if kennwert.id in benutzt:
                fehler.append(f'„{kennwert.name}“ hat eingetragene Werte und kann nicht gelöscht werden.')
                continue
            testform.kennwerte.remove(kennwert)
            continue
        kennwert_name = (request.form.get(praefix + 'name') or '').strip()
        if not kennwert_name:
            fehler.append('Ein Kennwert braucht einen Namen.')
            continue
        _uebernimm_kennwert(kennwert, praefix, kennwert_name, fehler)

    for index in range(NEUE_KENNWERT_ZEILEN):
        praefix = f'neu_{index}_'
        kennwert_name = (request.form.get(praefix + 'name') or '').strip()
        if not kennwert_name:
            continue
        kennwert = DiagnostikKennwert(sort_order=len(testform.kennwerte))
        if _uebernimm_kennwert(kennwert, praefix, kennwert_name, fehler):
            testform.kennwerte.append(kennwert)

    if not testform.kennwerte and not fehler:
        fehler.append('Eine Testform braucht mindestens einen Kennwert.')
    return fehler


def _uebernimm_kennwert(kennwert, praefix, name, fehler):
    arten = {schluessel: request.form.get(praefix + schluessel) == '1' for schluessel, *_ in WERTARTEN}
    if not any(arten.values()):
        fehler.append(f'„{name}“: bitte mindestens eine Wertart ankreuzen.')
        return False
    kennwert.name = name[:100]
    for schluessel, aktiv in arten.items():
        setattr(kennwert, schluessel, aktiv)
    kennwert.leitwert = request.form.get(praefix + 'leitwert') == '1'
    kennwert.risiko = request.form.get(praefix + 'risiko') == '1'
    position, gueltig = _int_oder_none(request.form.get(praefix + 'sort_order'), 0, 999)
    if gueltig and position is not None:
        kennwert.sort_order = position
    return True


@diagnostik_bp.route('/admin/diagnostik/testform/<int:testform_id>/loeschen', methods=['POST'])
@admin_required(redirect_endpoint='admin.admin_dashboard', message=ADMIN_MELDUNG)
def admin_testform_loeschen(testform_id):
    testform = get_or_404_session(DiagnostikTestform, testform_id)
    verfahren_id = testform.verfahren_id
    anzahl = _anzahl_ergebnisse([testform.id])
    if anzahl:
        flash(
            f'„{testform.name}“ hat {anzahl} eingetragene Ergebnis(se) und kann nicht gelöscht werden. '
            'Stattdessen deaktivieren.'
        )
        return redirect(url_for('diagnostik.admin_testform', verfahren_id=verfahren_id, testform_id=testform.id))
    name = testform.name
    db.session.delete(testform)
    db.session.commit()
    flash(f'Testform „{name}“ gelöscht.')
    return redirect(url_for('diagnostik.admin_verfahren', verfahren_id=verfahren_id))


# ----------------------------------------------------------------------
# Eingabe für Lehrkräfte
# ----------------------------------------------------------------------

def zugaengliche_klassen(user):
    """Klassen, deren Ergebnisse eine Lehrkraft eintragen und sehen darf.

    Namen ohne Leerzeichen am Rand: Formulare kürzen ihre Eingabe, und ein
    gespeichertes "1c " darf deshalb nicht zu einer Ablehnung von "1c" führen.
    """
    if user.is_admin:
        namen = get_distinct_klassen()
    else:
        kontext = get_user_klassenkontext(user)
        namen = set(kontext.get('fachklassen') or set())
        if kontext.get('klassenleitung'):
            namen.add(kontext['klassenleitung'])
    return sorted({(name or '').strip() for name in namen} - {''}, key=str.lower)


def darf_kind_sehen(user, schueler):
    if not schueler:
        return False
    if user.is_admin:
        return True
    return bool((schueler.klasse or '').strip()) and schueler.klasse.strip() in zugaengliche_klassen(user)


def _kinder_der_klasse(klasse):
    """Aktive Kinder einer Klasse; der gespeicherte Name wird ohne Randleerzeichen verglichen."""
    return (
        Schueler.query
        .filter(func.trim(Schueler.klasse) == klasse, Schueler.is_active.is_(True))
        .order_by(Schueler.nachname, Schueler.vorname)
        .all()
    )


def _kein_zugriff(klasse=None, schueler=None):
    """403 mit Erklärung statt einer leeren Fehlerseite."""
    return render_template(
        'diagnostik_kein_zugriff.html',
        klasse=klasse,
        schueler=schueler,
        klassen=zugaengliche_klassen(current_user),
    ), 403


def _aktive_testformen():
    return (
        DiagnostikTestform.query
        .join(DiagnostikVerfahren)
        .filter(DiagnostikTestform.is_active.is_(True), DiagnostikVerfahren.is_active.is_(True))
        .order_by(DiagnostikVerfahren.sort_order, DiagnostikTestform.sort_order)
        .all()
    )


def _schuljahr_auswahl(aktuell, gewaehlt=None):
    return schuljahr_auswahl(aktuell, gewaehlt)


def _datumsfehler(datum_roh, schuljahr):
    """(Datum, Fehlertext) - leeres Feld ist kein Fehler, ergibt aber kein Datum."""
    if not (datum_roh or '').strip():
        return None, None
    datum = _parse_datum(datum_roh)
    if not datum:
        return None, 'Das Testdatum ist ungültig.'
    if not datum_im_schuljahr(datum, schuljahr):
        von, bis = schuljahr_zeitraum(schuljahr)
        return None, (
            f'Das Testdatum {datum.strftime("%d.%m.%Y")} liegt nicht im Schuljahr {schuljahr} '
            f'({von.strftime("%d.%m.%Y")} bis {bis.strftime("%d.%m.%Y")}).'
        )
    return datum, None


def _parse_datum(raw):
    try:
        return datetime.strptime((raw or '').strip(), '%Y-%m-%d').date()
    except ValueError:
        return None


@diagnostik_bp.route('/diagnostik/erfassen', methods=['GET', 'POST'])
@login_required
def erfassen():
    config = SystemKonfiguration.query.first()
    schuljahr_aktuell = config.schuljahr if config and config.schuljahr else None
    klassen = zugaengliche_klassen(current_user)

    klasse = (request.values.get('klasse') or '').strip()
    schueler_id = (request.values.get('schueler_id') or '').strip()
    einzelkind = None
    if schueler_id.isdigit():
        einzelkind = get_or_404_session(Schueler, int(schueler_id))
        if not darf_kind_sehen(current_user, einzelkind):
            return _kein_zugriff(schueler=einzelkind)
        klasse = (einzelkind.klasse or '').strip()
    elif klasse and klasse not in klassen:
        return _kein_zugriff(klasse=klasse)

    testform = None
    testform_id = (request.values.get('testform_id') or '').strip()
    if testform_id.isdigit():
        testform = get_or_404_session(DiagnostikTestform, int(testform_id))

    schuljahr = normalize_school_year(request.values.get('schuljahr')) or schuljahr_aktuell
    halbjahr = request.values.get('halbjahr') if request.values.get('halbjahr') in HALBJAHRE else aktuelles_halbjahr()

    # Schritt 1: Klasse und Test wählen
    if not testform or not (klasse or einzelkind) or not schuljahr:
        if not schuljahr_aktuell:
            flash('Bitte zuerst in der Verwaltung das aktuelle Schuljahr festlegen.')
        if einzelkind:
            jahrgaenge_der_klasse = [j for j in [effective_jahrgang(einzelkind)] if j]
        else:
            jahrgaenge_der_klasse = klassen_jahrgaenge(klasse) if klasse else []
        # Beim Nachtragen gilt der Testplan des Jahrgangs, den die Kinder damals hatten.
        zurueck = jahre_zurueck(schuljahr, schuljahr_aktuell) if schuljahr and schuljahr_aktuell else 0
        vorschlaege = []
        for jahrgang in jahrgaenge_der_klasse:
            vorschlaege.extend(zeitpunkte_fuer(jahrgang - zurueck))
        return render_template(
            'diagnostik_auswahl.html',
            klassen=klassen,
            klasse=klasse,
            einzelkind=einzelkind,
            testformen=_aktive_testformen(),
            vorschlaege=vorschlaege,
            schuljahre=_schuljahr_auswahl(schuljahr_aktuell, schuljahr),
            schuljahr=schuljahr,
            schuljahr_aktuell=schuljahr_aktuell,
            halbjahre=HALBJAHRE,
            halbjahr=halbjahr,
        )

    # Schritt 2: Tabelle
    if einzelkind:
        kinder = [einzelkind]
    else:
        kinder = _kinder_der_klasse(klasse)
    vorhanden = {
        ergebnis.schueler_id: ergebnis
        for ergebnis in DiagnostikErgebnis.query.filter(
            DiagnostikErgebnis.testform_id == testform.id,
            DiagnostikErgebnis.schuljahr == schuljahr,
            DiagnostikErgebnis.halbjahr == halbjahr,
            DiagnostikErgebnis.schueler_id.in_([k.id for k in kinder] or [0]),
        ).all()
    }
    kennwerte = testform.kennwerte
    datum = None
    datum_roh = ''
    eingaben = {}
    fehler = set()
    meldungen = []

    if request.method == 'POST':
        datum_roh = (request.form.get('datum') or '').strip()
        datum, datum_meldung = _datumsfehler(datum_roh, schuljahr)
        if datum_meldung:
            meldungen.append(datum_meldung)
            fehler.add('datum')
        zeilen = []
        for kind in kinder:
            werte = {}
            for kennwert in kennwerte:
                for art in kennwert.wertarten:
                    feld = f'w_{kind.id}_{kennwert.id}_{art}'
                    roh = (request.form.get(feld) or '').strip()
                    eingaben[feld] = roh
                    minimum, maximum = WERTART_BEREICH[art]
                    zahl, gueltig = _int_oder_none(roh, minimum, maximum)
                    if not gueltig:
                        fehler.add(feld)
                    elif zahl is not None:
                        werte[(kennwert.id, art)] = zahl
            bemerkung = (request.form.get(f'bemerkung_{kind.id}') or '').strip()
            eingaben[f'bemerkung_{kind.id}'] = bemerkung
            # Eigenes Datum für Kinder, die an einem anderen Tag getestet wurden.
            zeilen_datum_roh = (request.form.get(f'datum_{kind.id}') or '').strip()
            eingaben[f'datum_{kind.id}'] = zeilen_datum_roh
            zeilen_datum, zeilen_meldung = _datumsfehler(zeilen_datum_roh, schuljahr)
            if zeilen_meldung:
                fehler.add(f'datum_{kind.id}')
                if zeilen_meldung not in meldungen:
                    meldungen.append(f'{kind.vorname} {kind.nachname}: {zeilen_meldung}')
            zeilen.append((kind, werte, bemerkung, zeilen_datum))

        werte_fehler = [f for f in fehler if f.startswith('w_')]
        ohne_datum = [kind for kind, werte, _, zeilen_datum in zeilen if werte and not (zeilen_datum or datum)]
        if werte_fehler:
            meldungen.insert(0, f'{len(werte_fehler)} Eingabe(n) liegen außerhalb des zulässigen Bereichs und sind markiert.')
        if ohne_datum and 'datum' not in fehler:
            meldungen.append('Bitte das Datum der Durchführung angeben.')
            fehler.add('datum')
        if meldungen:
            flash(' '.join(meldungen) + ' Es wurde nichts gespeichert.')
        else:
            gespeichert = entfernt = 0
            for kind, werte, bemerkung, zeilen_datum in zeilen:
                ergebnis = vorhanden.get(kind.id)
                if not werte:
                    if ergebnis:
                        db.session.delete(ergebnis)
                        entfernt += 1
                    continue
                speichere_ergebnis(
                    kind, testform, schuljahr, halbjahr, werte, current_user.id,
                    datum=zeilen_datum or datum, bemerkung=bemerkung,
                    aktuelles_schuljahr=schuljahr_aktuell,
                )
                gespeichert += 1
            db.session.commit()
            teile = [f'{gespeichert} Ergebnis(se) gespeichert']
            if entfernt:
                teile.append(f'{entfernt} geleert und entfernt')
            flash(f'{testform.name}, {zeitlabel(schuljahr, halbjahr)}: ' + ', '.join(teile) + '.')
            ziel = {'testform_id': testform.id, 'schuljahr': schuljahr, 'halbjahr': halbjahr}
            if einzelkind:
                ziel['schueler_id'] = einzelkind.id
            else:
                ziel['klasse'] = klasse
            return redirect(url_for('diagnostik.erfassen', **ziel))

    if request.method == 'GET':
        # Gemeinsames Datum: das häufigste der vorhandenen Ergebnisse. Ohne
        # Ergebnisse im laufenden Schuljahr heute - beim Nachtragen bleibt das
        # Feld leer, damit niemand versehentlich das heutige Datum übernimmt.
        daten = Counter(e.datum for e in vorhanden.values() if e.datum)
        if daten:
            datum = daten.most_common(1)[0][0]
        elif datum_im_schuljahr(date.today(), schuljahr):
            datum = date.today()
        datum_roh = datum.isoformat() if datum else ''
        for kind in kinder:
            ergebnis = vorhanden.get(kind.id)
            if not ergebnis:
                continue
            if ergebnis.datum and ergebnis.datum != datum:
                eingaben[f'datum_{kind.id}'] = ergebnis.datum.isoformat()
            eingaben[f'bemerkung_{kind.id}'] = ergebnis.bemerkung or ''
            for wert in ergebnis.werte:
                for art in ('rohwert', 'prozentrang', 't_wert', 'lesequotient'):
                    if getattr(wert, art) is not None:
                        eingaben[f'w_{kind.id}_{wert.kennwert_id}_{art}'] = getattr(wert, art)

    grenzen = risikogrenzen(config)
    stufen_je_kind = {
        kind_id: auswerten(ergebnis, grenzen) for kind_id, ergebnis in vorhanden.items()
    } if request.method == 'GET' else {}
    geplante_jahrgaenge = {z.jahrgang for z in testform.zeitpunkte if z.halbjahr == halbjahr}
    von, bis = schuljahr_zeitraum(schuljahr)
    return render_template(
        'diagnostik_erfassen.html',
        testform=testform,
        kennwerte=kennwerte,
        kinder=kinder,
        klasse=klasse,
        einzelkind=einzelkind,
        schuljahr=schuljahr,
        halbjahr=halbjahr,
        halbjahre=HALBJAHRE,
        datum_roh=datum_roh,
        datum_von=von,
        datum_bis=bis,
        nachtrag=bool(schuljahr_aktuell and schuljahr != schuljahr_aktuell),
        eingaben=eingaben,
        fehler=fehler,
        vorhanden=vorhanden,
        auswertungen=stufen_je_kind,
        stufen=STUFEN,
        wertarten={schluessel: (label, kuerzel, minimum, maximum) for schluessel, label, kuerzel, minimum, maximum in WERTARTEN},
        geplante_jahrgaenge=geplante_jahrgaenge,
        jahrgang_damals=lambda kind: jahrgang_im_schuljahr(kind, schuljahr, schuljahr_aktuell or schuljahr),
    )


@diagnostik_bp.route('/diagnostik/ergebnis/<int:ergebnis_id>/loeschen', methods=['POST'])
@login_required
def ergebnis_loeschen(ergebnis_id):
    ergebnis = get_or_404_session(DiagnostikErgebnis, ergebnis_id)
    if not darf_kind_sehen(current_user, ergebnis.schueler):
        abort(403)
    schueler_id = ergebnis.schueler_id
    beschreibung = f'{ergebnis.testform.name}, {zeitlabel(ergebnis.schuljahr, ergebnis.halbjahr)}'
    db.session.delete(ergebnis)
    db.session.commit()
    flash(f'Ergebnis {beschreibung} gelöscht.')
    ziel = (request.form.get('next') or '').strip()
    if ziel.startswith('/') and not ziel.startswith('//'):
        return redirect(ziel)
    return redirect(url_for('system.schuelerakte', schueler_id=schueler_id))


# ----------------------------------------------------------------------
# Auswertung: Klassenübersicht
# ----------------------------------------------------------------------

@diagnostik_bp.route('/diagnostik')
@login_required
def uebersicht():
    config = SystemKonfiguration.query.first()
    schuljahr = config.schuljahr if config else None
    klassen = zugaengliche_klassen(current_user)
    klasse = (request.args.get('klasse') or '').strip()
    if not klasse and klassen:
        kontext = get_user_klassenkontext(current_user)
        klasse = kontext.get('klassenleitung') if kontext.get('klassenleitung') in klassen else klassen[0]
    if klasse and klasse not in klassen:
        return _kein_zugriff(klasse=klasse)
    nur_risiko = request.args.get('risiko') == '1'

    bereiche, zeilen, zaehler = [], [], {stufe: 0 for stufe in STUFEN}
    if klasse:
        kinder = _kinder_der_klasse(klasse)
        bereiche, zeilen = klassen_uebersicht(kinder, schuljahr, risikogrenzen(config))
        for zeile in zeilen:
            if zeile['stufe']:
                zaehler[zeile['stufe']] += 1
        if nur_risiko:
            zeilen = [zeile for zeile in zeilen if zeile['stufe']]

    return render_template(
        'diagnostik_uebersicht.html',
        klassen=klassen,
        klasse=klasse,
        schuljahr=schuljahr,
        bereiche=bereiche,
        zeilen=zeilen,
        zaehler=zaehler,
        nur_risiko=nur_risiko,
        stufen=STUFEN,
        halbjahre=HALBJAHRE,
    )


# ----------------------------------------------------------------------
# Import aus Auswertungsmappen
# ----------------------------------------------------------------------

MAX_IMPORT_BYTES = 8 * 1024 * 1024


def _import_kinder(klasse):
    return _kinder_der_klasse(klasse)


def _passende_testformen(suchbegriff):
    begriff = (suchbegriff or '').casefold()
    return [t for t in _aktive_testformen() if begriff and begriff in t.verfahren.name.casefold()]


def _vorgeschlagene_testform(kandidaten, jahrgang, halbjahr):
    for testform in kandidaten:
        if any(z.jahrgang == jahrgang and z.halbjahr == halbjahr for z in testform.zeitpunkte):
            return testform
    for testform in kandidaten:
        if any(z.jahrgang == jahrgang for z in testform.zeitpunkte):
            return testform
    return kandidaten[0] if kandidaten else None


def _import_payload(datei):
    return json.dumps({
        'format': datei.format,
        'verfahren': datei.verfahren,
        'kennwerte': datei.kennwerte,
        'hinweise': datei.hinweise,
        'ohne_namen': datei.ohne_namen,
        'zeilen': [
            {'vorname': z.vorname, 'nachname': z.nachname, 'zeile': z.zeile, 'werte': z.werte}
            for z in datei.zeilen
        ],
    }, ensure_ascii=False)


def _datei_aus_payload(roh):
    try:
        daten = json.loads(roh or '')
        datei = ImportDatei(
            format=daten['format'], verfahren=daten['verfahren'], kennwerte=list(daten['kennwerte']),
            hinweise=list(daten.get('hinweise') or []), ohne_namen=int(daten.get('ohne_namen') or 0),
        )
        for zeile in daten['zeilen']:
            werte = {
                str(kennwert): {str(art): int(zahl) for art, zahl in arten.items() if art in WERTART_BEREICH}
                for kennwert, arten in (zeile.get('werte') or {}).items()
            }
            datei.zeilen.append(ImportZeile(
                vorname=str(zeile.get('vorname') or ''), nachname=str(zeile.get('nachname') or ''),
                zeile=int(zeile.get('zeile') or 0), werte=werte,
            ))
        return datei
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


@diagnostik_bp.route('/diagnostik/import', methods=['GET', 'POST'])
@login_required
def importieren():
    config = SystemKonfiguration.query.first()
    schuljahr_aktuell = config.schuljahr if config and config.schuljahr else None
    klassen = zugaengliche_klassen(current_user)
    klasse = (request.form.get('klasse') or request.args.get('klasse') or '').strip()
    if klasse and klasse not in klassen:
        return _kein_zugriff(klasse=klasse)
    aktion = request.form.get('aktion') if request.method == 'POST' else None

    def formular(**fehler_kontext):
        return render_template(
            'diagnostik_import.html', schritt='hochladen', klassen=klassen, klasse=klasse,
            schuljahre=_schuljahr_auswahl(schuljahr_aktuell), schuljahr=schuljahr_aktuell,
            schuljahr_aktuell=schuljahr_aktuell, **fehler_kontext,
        )

    if not aktion:
        return formular()

    if not klasse:
        flash('Bitte eine Klasse wählen.')
        return formular()

    # Datei lesen (erster Schritt) oder aus der Vorschau übernehmen
    if aktion == 'hochladen':
        upload = request.files.get('datei')
        if not upload or not upload.filename:
            flash('Bitte eine Datei auswählen.')
            return formular()
        inhalt = upload.read(MAX_IMPORT_BYTES + 1)
        if len(inhalt) > MAX_IMPORT_BYTES:
            flash('Die Datei ist zu groß (höchstens 8 MB).')
            return formular()
        try:
            datei = lese_import(upload.filename, inhalt)
        except ImportFehler as fehler:
            flash(str(fehler))
            return formular()
        dateiname = upload.filename
    else:
        datei = _datei_aus_payload(request.form.get('payload'))
        if datei is None:
            flash('Die Vorschau ist abgelaufen oder beschädigt. Bitte die Datei erneut hochladen.')
            return formular()
        dateiname = request.form.get('dateiname') or ''

    kinder = _import_kinder(klasse)
    kinder_nach_id = {kind.id: kind for kind in kinder}
    kandidaten = _passende_testformen(datei.verfahren)
    if not kandidaten:
        flash(f'Im Diagnostik-Katalog gibt es kein aktives Verfahren „{datei.verfahren}“.')
        return formular()

    if aktion == 'hochladen':
        halbjahr = datei.halbjahr or aktuelles_halbjahr()
        schuljahr = normalize_school_year(request.form.get('schuljahr')) or schuljahr_aktuell
        jahrgang = datei.jahrgang
        if not jahrgang:
            heute = next(iter(klassen_jahrgaenge(klasse)), None)
            jahrgang = heute - jahre_zurueck(schuljahr, schuljahr_aktuell) if heute and schuljahr and schuljahr_aktuell else heute
        testform = _vorgeschlagene_testform(kandidaten, jahrgang, halbjahr)
        datum_roh = request.form.get('datum') or ''
        automatisch = ordne_kinder_zu(datei.zeilen, kinder)
        zuordnung = {i: (kind.id if kind else None) for i, kind in automatisch.items()}
    else:
        halbjahr = request.form.get('halbjahr') if request.form.get('halbjahr') in HALBJAHRE else aktuelles_halbjahr()
        testform_id = (request.form.get('testform_id') or '').strip()
        testform = next((t for t in kandidaten if str(t.id) == testform_id), kandidaten[0])
        schuljahr = normalize_school_year(request.form.get('schuljahr')) or schuljahr_aktuell
        datum_roh = request.form.get('datum') or ''
        zuordnung = {}
        for index in range(len(datei.zeilen)):
            wahl = (request.form.get(f'kind_{index}') or '').strip()
            zuordnung[index] = int(wahl) if wahl.isdigit() and int(wahl) in kinder_nach_id else None

    datum, datum_meldung = _datumsfehler(datum_roh, schuljahr)
    zeilen_daten = {}
    zeilen_datum_fehler = set()
    for index in range(len(datei.zeilen)):
        roh = (request.form.get(f'datum_{index}') or '').strip() if aktion != 'hochladen' else ''
        zeilen_daten[index] = roh
        if roh:
            wert, meldung = _datumsfehler(roh, schuljahr)
            if meldung:
                zeilen_datum_fehler.add(index)
    kennwert_nach_name = {k.name.casefold(): k for k in testform.kennwerte}
    zuordnung_kennwerte = {name: kennwert_nach_name.get(name.casefold()) for name in datei.kennwerte}

    vorhandene = {
        e.schueler_id for e in DiagnostikErgebnis.query.filter(
            DiagnostikErgebnis.testform_id == testform.id,
            DiagnostikErgebnis.schuljahr == schuljahr,
            DiagnostikErgebnis.halbjahr == halbjahr,
            DiagnostikErgebnis.schueler_id.in_(list(kinder_nach_id) or [0]),
        ).all()
    }

    if aktion == 'speichern':
        doppelt = [kind_id for kind_id in zuordnung.values() if kind_id and list(zuordnung.values()).count(kind_id) > 1]
        if not schuljahr:
            flash('Bitte ein Schuljahr wählen.')
        elif datum_meldung:
            flash(datum_meldung)
        elif not datum and any(zuordnung.get(i) and not zeilen_daten.get(i) for i in range(len(datei.zeilen))):
            flash('Bitte das Datum der Durchführung angeben.')
        elif zeilen_datum_fehler:
            flash(f'{len(zeilen_datum_fehler)} abweichende(s) Datum/Daten liegen nicht im Schuljahr {schuljahr} und sind markiert.')
        elif doppelt:
            namen = sorted({f'{kinder_nach_id[k].vorname} {kinder_nach_id[k].nachname}' for k in doppelt})
            flash(f'Mehrere Zeilen sind demselben Kind zugeordnet: {", ".join(namen)}. Bitte korrigieren.')
        else:
            gespeichert = neu_angelegt = verworfen = 0
            nur = {k.id for k in zuordnung_kennwerte.values() if k}
            for index, zeile in enumerate(datei.zeilen):
                kind = kinder_nach_id.get(zuordnung.get(index))
                if not kind:
                    continue
                werte = {}
                for name, arten in zeile.werte.items():
                    kennwert = zuordnung_kennwerte.get(name)
                    if not kennwert:
                        continue
                    for art, zahl_wert in arten.items():
                        minimum, maximum = WERTART_BEREICH[art]
                        if art not in kennwert.wertarten:
                            continue
                        if minimum <= zahl_wert <= maximum:
                            werte[(kennwert.id, art)] = zahl_wert
                        else:
                            verworfen += 1
                if not werte:
                    continue
                _, neu = speichere_ergebnis(
                    kind, testform, schuljahr, halbjahr, werte, current_user.id,
                    datum=_parse_datum(zeilen_daten[index]) if zeilen_daten.get(index) else datum,
                    nur_kennwerte=nur,
                    aktuelles_schuljahr=schuljahr_aktuell,
                )
                gespeichert += 1
                neu_angelegt += 1 if neu else 0
            db.session.commit()
            meldung = (
                f'Import {testform.name}, {zeitlabel(schuljahr, halbjahr)}: {gespeichert} Ergebnis(se) übernommen, '
                f'davon {neu_angelegt} neu und {gespeichert - neu_angelegt} aktualisiert.'
            )
            if verworfen:
                meldung += f' {verworfen} Wert(e) außerhalb des zulässigen Bereichs wurden nicht übernommen.'
            flash(meldung)
            return redirect(url_for('diagnostik.uebersicht', klasse=klasse))

    zuordnete_ids = [kind_id for kind_id in zuordnung.values() if kind_id]
    return render_template(
        'diagnostik_import.html',
        schritt='vorschau',
        klassen=klassen,
        klasse=klasse,
        datei=datei,
        dateiname=dateiname,
        payload=_import_payload(datei),
        kandidaten=kandidaten,
        testform=testform,
        schuljahre=_schuljahr_auswahl(schuljahr_aktuell, schuljahr),
        schuljahr=schuljahr,
        schuljahr_aktuell=schuljahr_aktuell,
        halbjahre=HALBJAHRE,
        halbjahr=halbjahr,
        datum=datum_roh,
        datum_ungueltig=bool(datum_meldung),
        zeilen_daten=zeilen_daten,
        zeilen_datum_fehler=zeilen_datum_fehler,
        kinder=kinder,
        zuordnung=zuordnung,
        zuordnung_kennwerte=zuordnung_kennwerte,
        vorhandene=vorhandene,
        doppelte={k for k in zuordnete_ids if zuordnete_ids.count(k) > 1},
        nicht_zugeordnet=[kind for kind in kinder if kind.id not in zuordnete_ids],
        wertart_kuerzel={schluessel: kuerzel for schluessel, _, kuerzel, _, _ in WERTARTEN},
    )


def register_diagnostik_routes(app):
    app.register_blueprint(diagnostik_bp)
