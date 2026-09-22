"""Hospitationen der Schulleitung: anlegen, Kinder auswählen, Notizen festhalten.

Gespeichert wird feldweise wie in der Förderkonferenz (static/js/konferenz.js).
Jede Route prüft selbst, dass hier nur die Schulleitung schreibt.
"""

from datetime import date

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from db_utils import get_or_404_session
from extensions import db
from hospitation import (
    HOSPITATION_FELDER,
    KIND_FELDER,
    darf_hospitieren,
    kinder_der_klasse,
    schreibe_feld,
)
from klassenzugriff import zugaengliche_klassen
from models import Hospitation, HospitationKind, KONFERENZ_STUFEN
from time_utils import utc_now

hospitation_bp = Blueprint('hospitation', __name__)


def _nur_schulleitung():
    if not darf_hospitieren(current_user):
        abort(403)


def _hospitation_oder_404(hospitation_id):
    _nur_schulleitung()
    return get_or_404_session(Hospitation, hospitation_id)


@hospitation_bp.route('/hospitation')
@login_required
def liste():
    _nur_schulleitung()
    hospitationen = Hospitation.query.order_by(Hospitation.datum.desc(), Hospitation.id.desc()).all()
    return render_template(
        'hospitation_liste.html',
        hospitationen=hospitationen,
        klassen=zugaengliche_klassen(current_user),
        heute=utc_now().date(),
        stufen=KONFERENZ_STUFEN,
    )


@hospitation_bp.route('/hospitation/neu', methods=['POST'])
@login_required
def neu():
    _nur_schulleitung()
    klasse = (request.form.get('klasse') or '').strip()
    try:
        datum = date.fromisoformat((request.form.get('datum') or '').strip())
    except ValueError:
        datum = utc_now().date()
    if klasse not in zugaengliche_klassen(current_user):
        flash('Bitte eine Klasse auswählen.')
        return redirect(url_for('hospitation.liste'))
    hospitation = Hospitation(
        datum=datum, klasse=klasse, user_id=current_user.id,
        anlass=(request.form.get('anlass') or '').strip() or None,
    )
    db.session.add(hospitation)
    db.session.commit()
    return redirect(url_for('hospitation.bearbeiten', hospitation_id=hospitation.id))


@hospitation_bp.route('/hospitation/<int:hospitation_id>')
@login_required
def bearbeiten(hospitation_id):
    hospitation = _hospitation_oder_404(hospitation_id)
    beobachtet = {eintrag.schueler_id for eintrag in hospitation.kinder}
    eintraege = sorted(hospitation.kinder, key=lambda e: ((e.schueler.nachname or '').lower(), (e.schueler.vorname or '').lower()))
    return render_template(
        'hospitation_bearbeiten.html',
        hospitation=hospitation,
        eintraege=eintraege,
        weitere=[kind for kind in kinder_der_klasse(hospitation.klasse) if kind.id not in beobachtet],
        stufen=KONFERENZ_STUFEN,
        stufen_reihenfolge=('A', 'B', 'C'),
    )


@hospitation_bp.route('/hospitation/<int:hospitation_id>/kinder', methods=['POST'])
@login_required
def kinder(hospitation_id):
    """Kinder zur Beobachtung hinzufügen oder eines entfernen."""
    hospitation = _hospitation_oder_404(hospitation_id)
    entfernen = request.form.get('entfernen', type=int)
    if entfernen:
        eintrag = db.session.get(HospitationKind, entfernen)
        if eintrag and eintrag.hospitation_id == hospitation.id:
            db.session.delete(eintrag)
    else:
        erlaubt = {kind.id for kind in kinder_der_klasse(hospitation.klasse)}
        vorhanden = {eintrag.schueler_id for eintrag in hospitation.kinder}
        for roh in request.form.getlist('schueler_id'):
            if roh.isdigit() and int(roh) in erlaubt and int(roh) not in vorhanden:
                db.session.add(HospitationKind(hospitation_id=hospitation.id, schueler_id=int(roh)))
    hospitation.bearbeitet_am = utc_now()
    db.session.commit()
    return redirect(url_for('hospitation.bearbeiten', hospitation_id=hospitation.id))


@hospitation_bp.route('/hospitation/<int:hospitation_id>/feld', methods=['POST'])
@login_required
def feld(hospitation_id):
    hospitation = _hospitation_oder_404(hospitation_id)
    daten = request.get_json(silent=True) or request.form
    name = (daten.get('feld') or '').strip()
    if name not in HOSPITATION_FELDER:
        return jsonify({'ok': False, 'fehler': 'Unbekanntes Feld.'}), 400
    schreibe_feld(hospitation, name, daten.get('wert'))
    db.session.commit()
    return jsonify({'ok': True, 'bearbeitet_am': hospitation.bearbeitet_am.isoformat()})


@hospitation_bp.route('/hospitation/<int:hospitation_id>/kind/<int:eintrag_id>/feld', methods=['POST'])
@login_required
def kind_feld(hospitation_id, eintrag_id):
    hospitation = _hospitation_oder_404(hospitation_id)
    eintrag = get_or_404_session(HospitationKind, eintrag_id)
    if eintrag.hospitation_id != hospitation.id:
        abort(404)
    daten = request.get_json(silent=True) or request.form
    name = (daten.get('feld') or '').strip()
    if name not in KIND_FELDER:
        return jsonify({'ok': False, 'fehler': 'Unbekanntes Feld.'}), 400
    schreibe_feld(eintrag, name, daten.get('wert'))
    db.session.commit()
    return jsonify({'ok': True, 'bearbeitet_am': eintrag.bearbeitet_am.isoformat()})


@hospitation_bp.route('/hospitation/<int:hospitation_id>/loeschen', methods=['POST'])
@login_required
def loeschen(hospitation_id):
    hospitation = _hospitation_oder_404(hospitation_id)
    db.session.delete(hospitation)
    db.session.commit()
    flash('Hospitation gelöscht.')
    return redirect(url_for('hospitation.liste'))


def register_hospitation_routes(app):
    app.register_blueprint(hospitation_bp)
