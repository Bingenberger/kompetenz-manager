"""Nachteilsausgleich: Übersicht, Formular je Kind und Schuljahr, Notenschutz."""

from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from db_utils import get_or_404_session
from extensions import db
from klassenzugriff import darf_kind_sehen
from models import Nachteilsausgleich, Schueler
from nachteilsausgleich import (
    NOTENSCHUTZ,
    TYPEN,
    aktuelles_schuljahr,
    beende,
    darf_bearbeiten,
    darf_loeschen,
    eintrag_fuer,
    fuer_kind,
    kinder_ohne_eintrag,
    kurzfassung,
    loesche,
    nimm_zurueck,
    speichere,
    uebernimm,
    uebersicht,
)
from school_year import next_school_year, normalize_school_year

nachteilsausgleich_bp = Blueprint('nachteilsausgleich', __name__)


def _safe_next_url(candidate, fallback_url):
    wert = (candidate or '').strip()
    return wert if wert.startswith('/') and not wert.startswith('//') else fallback_url


def _datum(feld):
    roh = (request.form.get(feld) or '').strip()
    try:
        return date.fromisoformat(roh) if roh else None
    except ValueError:
        return None


def _kind_oder_403(schueler_id):
    schueler = get_or_404_session(Schueler, schueler_id)
    if not darf_kind_sehen(current_user, schueler):
        abort(403)
    return schueler


@nachteilsausgleich_bp.route('/nachteilsausgleich')
@login_required
def liste():
    schuljahr = normalize_school_year(request.args.get('schuljahr')) or aktuelles_schuljahr()
    alle = request.args.get('alle') == '1'
    eintraege = uebersicht(current_user, schuljahr, nur_laufende=not alle)
    return render_template(
        'nachteilsausgleich_liste.html',
        eintraege=eintraege,
        schuljahr=schuljahr,
        alle=alle,
        kurzfassung=kurzfassung,
        ohne_eintrag=kinder_ohne_eintrag(current_user, schuljahr) if schuljahr else [],
        typen=TYPEN,
    )


@nachteilsausgleich_bp.route('/nachteilsausgleich/kind/<int:schueler_id>', methods=['GET', 'POST'])
@login_required
def bearbeiten(schueler_id):
    schueler = _kind_oder_403(schueler_id)
    if not darf_bearbeiten(current_user, schueler):
        abort(403)
    next_url = (request.args.get('next') or request.form.get('next') or '').strip()
    schuljahr = normalize_school_year(request.values.get('schuljahr')) or aktuelles_schuljahr()
    if not schuljahr:
        flash('Bitte zuerst das Schuljahr in der Verwaltung setzen.')
        return redirect(url_for('system.schuelerakte', schueler_id=schueler.id))

    eintrag = eintrag_fuer(schueler.id, schuljahr)

    if request.method == 'POST':
        massnahmen = [(typ, request.form.get(f'massnahme_{typ}')) for typ, _, _ in TYPEN]
        eintrag = speichere(schueler, schuljahr, {
            'beschluss_am': _datum('beschluss_am'),
            'grundlage': request.form.get('grundlage'),
            'eltern_informiert_am': _datum('eltern_informiert_am'),
            'notenschutz_lesen': request.form.get('notenschutz_lesen') == '1',
            'notenschutz_rechtschreiben': request.form.get('notenschutz_rechtschreiben') == '1',
            'notenschutz_beschluss_am': _datum('notenschutz_beschluss_am'),
            'notiz': request.form.get('notiz'),
            'massnahmen': massnahmen,
        }, current_user)
        # Ein Eintrag ohne jede Angabe ist keiner.
        if eintrag.leer:
            loesche(eintrag, current_user)
            db.session.commit()
            flash(f'Nachteilsausgleich {schuljahr} für {schueler.vorname} entfernt.')
        else:
            db.session.commit()
            flash(f'Nachteilsausgleich {schuljahr} für {schueler.vorname} gespeichert.')
        return redirect(_safe_next_url(next_url, url_for('nachteilsausgleich.liste')))

    return render_template(
        'nachteilsausgleich_form.html',
        schueler=schueler,
        eintrag=eintrag,
        schuljahr=schuljahr,
        typen=TYPEN,
        notenschutz=NOTENSCHUTZ,
        frueher=[e for e in fuer_kind(schueler.id) if e.schuljahr != schuljahr],
        kurzfassung=kurzfassung,
        next_url=next_url,
    )


@nachteilsausgleich_bp.route('/nachteilsausgleich/<int:eintrag_id>/aktion', methods=['POST'])
@login_required
def aktion(eintrag_id):
    eintrag = get_or_404_session(Nachteilsausgleich, eintrag_id)
    schueler = _kind_oder_403(eintrag.schueler_id)
    if not darf_bearbeiten(current_user, schueler):
        abort(403)
    was = (request.form.get('aktion') or '').strip()
    ziel = _safe_next_url(request.form.get('next'), url_for('nachteilsausgleich.liste'))

    if was == 'beenden':
        beende(eintrag, current_user, _datum('beendet_am'))
        flash(f'Nachteilsausgleich für {schueler.vorname} beendet.')
    elif was == 'fortsetzen':
        nimm_zurueck(eintrag, current_user)
        flash(f'Nachteilsausgleich für {schueler.vorname} läuft weiter.')
    elif was == 'uebernehmen':
        neues = normalize_school_year(request.form.get('schuljahr')) or next_school_year(eintrag.schuljahr)
        kopie = uebernimm(eintrag, neues, current_user)
        if kopie is None:
            flash(f'Für {neues} gibt es bereits einen Eintrag.')
        else:
            db.session.commit()
            flash(f'Nachteilsausgleich nach {neues} übernommen – bitte Beschlussdatum ergänzen.')
            return redirect(url_for('nachteilsausgleich.bearbeiten', schueler_id=schueler.id,
                                    schuljahr=neues, next=ziel))
    elif was == 'loeschen':
        if not darf_loeschen(current_user, eintrag):
            flash('Löschen darf, wer den Eintrag angelegt hat – dazu die Schulleitung.')
            return redirect(ziel)
        loesche(eintrag, current_user)
        flash(f'Nachteilsausgleich für {schueler.vorname} gelöscht.')
    db.session.commit()
    return redirect(ziel)


def register_nachteilsausgleich_routes(app):
    app.register_blueprint(nachteilsausgleich_bp)
