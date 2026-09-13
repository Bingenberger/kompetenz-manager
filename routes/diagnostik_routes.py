"""Standardisierte Diagnostik: Katalog (Verwaltung), Eingabe und Auswertung."""

from flask import Blueprint, flash, redirect, render_template, request, url_for

from authz import admin_required
from db_utils import get_or_404_session
from diagnostik import HALBJAHRE, STUFEN, WERTARTEN, risikogrenzen
from extensions import db
from jahrgang import JAHRGAENGE
from models import (
    DiagnostikErgebnis,
    DiagnostikKennwert,
    DiagnostikTestform,
    DiagnostikVerfahren,
    DiagnostikWert,
    DiagnostikZeitpunkt,
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


def register_diagnostik_routes(app):
    app.register_blueprint(diagnostik_bp)
