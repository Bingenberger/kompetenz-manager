"""Standardisierte Diagnostik: Katalog (Verwaltung), Eingabe und Auswertung."""

from datetime import date, datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from authz import admin_required
from db_utils import get_or_404_session
from diagnostik import (
    HALBJAHRE,
    STUFEN,
    WERTART_BEREICH,
    WERTARTEN,
    aktuelles_halbjahr,
    auswerten,
    klassen_uebersicht,
    risikogrenzen,
    zeitlabel,
    zeitpunkte_fuer,
)
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
    """Klassen, deren Ergebnisse eine Lehrkraft eintragen und sehen darf."""
    if user.is_admin:
        return get_distinct_klassen()
    kontext = get_user_klassenkontext(user)
    klassen = set(kontext.get('fachklassen') or set())
    if kontext.get('klassenleitung'):
        klassen.add(kontext['klassenleitung'])
    return sorted(klassen, key=str.lower)


def darf_kind_sehen(user, schueler):
    if not schueler:
        return False
    if user.is_admin:
        return True
    return bool(schueler.klasse) and schueler.klasse in zugaengliche_klassen(user)


def _aktive_testformen():
    return (
        DiagnostikTestform.query
        .join(DiagnostikVerfahren)
        .filter(DiagnostikTestform.is_active.is_(True), DiagnostikVerfahren.is_active.is_(True))
        .order_by(DiagnostikVerfahren.sort_order, DiagnostikTestform.sort_order)
        .all()
    )


def _schuljahr_auswahl(aktuell):
    """Aktuelles und vorheriges Schuljahr - für Nachträge."""
    jahre = []
    if aktuell:
        beginn = int(aktuell[:4])
        jahre = [aktuell, f'{beginn - 1}/{beginn}']
    return jahre


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
            abort(403)
        klasse = einzelkind.klasse or ''
    elif klasse and klasse not in klassen:
        abort(403)

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
        vorschlaege = []
        for jahrgang in jahrgaenge_der_klasse:
            vorschlaege.extend(zeitpunkte_fuer(jahrgang))
        return render_template(
            'diagnostik_auswahl.html',
            klassen=klassen,
            klasse=klasse,
            einzelkind=einzelkind,
            testformen=_aktive_testformen(),
            vorschlaege=vorschlaege,
            schuljahre=_schuljahr_auswahl(schuljahr_aktuell),
            schuljahr=schuljahr,
            halbjahre=HALBJAHRE,
            halbjahr=halbjahr,
        )

    # Schritt 2: Tabelle
    if einzelkind:
        kinder = [einzelkind]
    else:
        kinder = (
            Schueler.query
            .filter(Schueler.klasse == klasse, Schueler.is_active.is_(True))
            .order_by(Schueler.nachname, Schueler.vorname)
            .all()
        )
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
    datum = _parse_datum(request.form.get('datum')) if request.method == 'POST' else None
    eingaben = {}
    fehler = set()

    if request.method == 'POST':
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
            zeilen.append((kind, werte, bemerkung))

        if fehler:
            flash(f'{len(fehler)} Eingabe(n) liegen außerhalb des zulässigen Bereichs und sind markiert. Es wurde nichts gespeichert.')
        elif not datum:
            flash('Bitte das Testdatum angeben.')
        else:
            gespeichert = entfernt = 0
            for kind, werte, bemerkung in zeilen:
                ergebnis = vorhanden.get(kind.id)
                if not werte:
                    if ergebnis:
                        db.session.delete(ergebnis)
                        entfernt += 1
                    continue
                if not ergebnis:
                    ergebnis = DiagnostikErgebnis(
                        schueler_id=kind.id, testform_id=testform.id,
                        schuljahr=schuljahr, halbjahr=halbjahr,
                    )
                    db.session.add(ergebnis)
                ergebnis.datum = datum
                ergebnis.jahrgang = effective_jahrgang(kind)
                ergebnis.bemerkung = bemerkung or None
                ergebnis.erfasst_von_user_id = current_user.id
                for kennwert in kennwerte:
                    wert = ergebnis.wert_fuer(kennwert.id) if ergebnis.id else None
                    neue = {art: werte.get((kennwert.id, art)) for art in kennwert.wertarten}
                    if not any(v is not None for v in neue.values()):
                        if wert:
                            ergebnis.werte.remove(wert)
                        continue
                    if not wert:
                        wert = DiagnostikWert(kennwert_id=kennwert.id)
                        ergebnis.werte.append(wert)
                    for art in ('rohwert', 'prozentrang', 't_wert', 'lesequotient'):
                        setattr(wert, art, neue.get(art))
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
        for kind in kinder:
            ergebnis = vorhanden.get(kind.id)
            if not ergebnis:
                continue
            datum = datum or ergebnis.datum
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
        datum=datum or date.today(),
        eingaben=eingaben,
        fehler=fehler,
        vorhanden=vorhanden,
        auswertungen=stufen_je_kind,
        stufen=STUFEN,
        wertarten={schluessel: (label, kuerzel, minimum, maximum) for schluessel, label, kuerzel, minimum, maximum in WERTARTEN},
        geplante_jahrgaenge=geplante_jahrgaenge,
        effective_jahrgang=effective_jahrgang,
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
        abort(403)
    nur_risiko = request.args.get('risiko') == '1'

    bereiche, zeilen, zaehler = [], [], {stufe: 0 for stufe in STUFEN}
    if klasse:
        kinder = (
            Schueler.query
            .filter(Schueler.klasse == klasse, Schueler.is_active.is_(True))
            .order_by(Schueler.nachname, Schueler.vorname)
            .all()
        )
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


def register_diagnostik_routes(app):
    app.register_blueprint(diagnostik_bp)
