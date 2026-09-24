"""Förderkurse: Katalog in der Verwaltung, Zuweisung der Kinder, offene Förderpläne."""

from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from authz import admin_required
from db_utils import get_or_404_session
from extensions import db
from foerderkurs import (
    ERINNERUNG_ABSTAND_TAGE,
    aktiver_plan_im_fach,
    aktuelles_schuljahr,
    beende,
    darf_zuweisen,
    faecher,
    kinder_ohne_kurs,
    kurse,
    laufende_teilnahmen,
    offene_plaene,
    setze_jahrgaenge,
    trage_ein,
)
from jahrgang import JAHRGAENGE
from klassenzugriff import darf_kind_sehen
from models import Fach, Foerderkurs, FoerderkursTeilnahme, Schueler, User
from school_year import normalize_school_year
from time_utils import utc_now

foerderkurs_bp = Blueprint('foerderkurs', __name__)

ADMIN_MELDUNG = 'Zugriff verweigert. Nur der Administrator darf Förderkurse pflegen.'


def _safe_next_url(candidate, fallback_url):
    wert = (candidate or '').strip()
    return wert if wert.startswith('/') and not wert.startswith('//') else fallback_url


# ----------------------------------------------------------------------
# Verwaltung: Fächer und Kurse
# ----------------------------------------------------------------------

@foerderkurs_bp.route('/admin/foerderkurse')
@admin_required(redirect_endpoint='admin.admin_dashboard', message=ADMIN_MELDUNG)
def admin_katalog():
    schuljahr = aktuelles_schuljahr()
    alle = Foerderkurs.query.join(Fach).order_by(Fach.sort_order, Fach.name, Foerderkurs.name).all()
    return render_template(
        'admin_foerderkurse.html',
        faecher=faecher(nur_aktive=False),
        kurse=alle,
        teilnehmer={kurs.id: len(laufende_teilnahmen(kurs=kurs)) for kurs in alle},
        jahrgaenge=JAHRGAENGE,
        schuljahr=schuljahr,
        lehrkraefte=User.query.order_by(User.nachname, User.vorname, User.username).all(),
    )


@foerderkurs_bp.route('/admin/faecher', methods=['POST'])
@admin_required(redirect_endpoint='admin.admin_dashboard', message=ADMIN_MELDUNG)
def admin_fach():
    aktion = (request.form.get('aktion') or 'neu').strip()
    if aktion == 'neu':
        name = (request.form.get('name') or '').strip()
        if not name:
            flash('Bitte einen Namen für das Fach eingeben.')
        elif Fach.query.filter(db.func.lower(Fach.name) == name.lower()).first():
            flash('Dieses Fach gibt es schon.')
        else:
            db.session.add(Fach(name=name, sort_order=len(faecher(nur_aktive=False))))
            db.session.commit()
            flash(f'Fach „{name}“ angelegt.')
    else:
        fach = get_or_404_session(Fach, request.form.get('fach_id', type=int))
        if aktion == 'umbenennen':
            name = (request.form.get('name') or '').strip()
            if name:
                fach.name = name
        elif aktion == 'deaktivieren':
            fach.is_active = not fach.is_active
        elif aktion == 'loeschen':
            if Foerderkurs.query.filter_by(fach_id=fach.id).first():
                flash('Das Fach wird noch von einem Kurs genutzt und bleibt erhalten.')
                return redirect(url_for('foerderkurs.admin_katalog'))
            db.session.delete(fach)
        db.session.commit()
    return redirect(url_for('foerderkurs.admin_katalog'))


@foerderkurs_bp.route('/admin/foerderkurse/neu', methods=['POST'])
@foerderkurs_bp.route('/admin/foerderkurse/<int:kurs_id>', methods=['POST'])
@admin_required(redirect_endpoint='admin.admin_dashboard', message=ADMIN_MELDUNG)
def admin_kurs(kurs_id=None):
    kurs = get_or_404_session(Foerderkurs, kurs_id) if kurs_id else None
    if (request.form.get('aktion') or '').strip() == 'loeschen' and kurs:
        if laufende_teilnahmen(kurs=kurs):
            kurs.is_active = False
            flash('Der Kurs hat noch Teilnahmen und wurde stattdessen deaktiviert.')
        else:
            db.session.delete(kurs)
            flash('Kurs gelöscht.')
        db.session.commit()
        return redirect(url_for('foerderkurs.admin_katalog'))

    name = (request.form.get('name') or '').strip()
    fach = db.session.get(Fach, request.form.get('fach_id', type=int) or 0)
    if not name or not fach:
        flash('Bitte Name und Fach angeben.')
        return redirect(url_for('foerderkurs.admin_katalog'))

    if kurs is None:
        kurs = Foerderkurs(name=name, fach_id=fach.id)
        db.session.add(kurs)
        db.session.flush()
    kurs.name = name
    kurs.fach_id = fach.id
    kurs.schuljahr = normalize_school_year(request.form.get('schuljahr')) if (request.form.get('schuljahr') or '').strip() else None
    kurs.zeit = (request.form.get('zeit') or '').strip() or None
    kurs.notiz = (request.form.get('notiz') or '').strip() or None
    kurs.leitung_user_id = request.form.get('leitung_user_id', type=int) or None
    kurs.is_active = request.form.get('is_active') == '1'
    setze_jahrgaenge(kurs, [j for j in request.form.getlist('jahrgaenge') if j.isdigit()])
    db.session.commit()
    flash(f'Kurs „{kurs.name}“ gespeichert.')
    return redirect(url_for('foerderkurs.admin_katalog'))


# ----------------------------------------------------------------------
# Kurse und Teilnahmen
# ----------------------------------------------------------------------

@foerderkurs_bp.route('/foerderkurse')
@login_required
def liste():
    schuljahr = aktuelles_schuljahr()
    alle = kurse(schuljahr)
    teilnahmen = {}
    for kurs in alle:
        sichtbar = [t for t in laufende_teilnahmen(kurs=kurs) if darf_kind_sehen(current_user, t.schueler)]
        teilnahmen[kurs.id] = sorted(sichtbar, key=lambda t: ((t.schueler.klasse or '').lower(),
                                                              (t.schueler.nachname or '').lower()))
    return render_template(
        'foerderkurs_liste.html',
        kurse=alle,
        teilnahmen=teilnahmen,
        schuljahr=schuljahr,
        offen=offene_plaene(current_user),
        plan_im_fach=aktiver_plan_im_fach,
    )


@foerderkurs_bp.route('/foerderkurse/<int:kurs_id>')
@login_required
def kurs(kurs_id):
    kurs = get_or_404_session(Foerderkurs, kurs_id)
    teilnahmen = [t for t in laufende_teilnahmen(kurs=kurs) if darf_kind_sehen(current_user, t.schueler)]
    frueher = [
        t for t in FoerderkursTeilnahme.query.filter(
            FoerderkursTeilnahme.kurs_id == kurs.id, FoerderkursTeilnahme.bis.isnot(None)).all()
        if darf_kind_sehen(current_user, t.schueler)
    ]
    return render_template(
        'foerderkurs_kurs.html',
        kurs=kurs,
        teilnahmen=sorted(teilnahmen, key=lambda t: ((t.schueler.klasse or '').lower(), (t.schueler.nachname or '').lower())),
        frueher=sorted(frueher, key=lambda t: t.bis or date.min, reverse=True),
        moegliche=[kind for kind in kinder_ohne_kurs(kurs) if darf_kind_sehen(current_user, kind)],
        plan_im_fach=aktiver_plan_im_fach,
        heute=utc_now().date(),
    )


@foerderkurs_bp.route('/foerderkurse/<int:kurs_id>/teilnahme', methods=['POST'])
@login_required
def teilnahme(kurs_id):
    kurs = get_or_404_session(Foerderkurs, kurs_id)
    beenden = request.form.get('beenden', type=int)
    if beenden:
        eintrag = get_or_404_session(FoerderkursTeilnahme, beenden)
        if eintrag.kurs_id != kurs.id or not darf_zuweisen(current_user, eintrag.schueler):
            abort(403)
        beende(eintrag)
        db.session.commit()
        flash(f'Teilnahme von {eintrag.schueler.vorname} beendet.')
        return redirect(_safe_next_url(request.form.get('next'), url_for('foerderkurs.kurs', kurs_id=kurs.id)))

    anzahl = 0
    for roh in request.form.getlist('schueler_id'):
        if not roh.isdigit():
            continue
        kind = db.session.get(Schueler, int(roh))
        if not kind or not darf_zuweisen(current_user, kind):
            abort(403)
        trage_ein(kurs, kind, current_user)
        anzahl += 1
    db.session.commit()
    flash(f'{anzahl} Kind(er) in den Kurs aufgenommen.' if anzahl else 'Kein Kind ausgewählt.')
    return redirect(_safe_next_url(request.form.get('next'), url_for('foerderkurs.kurs', kurs_id=kurs.id)))


@foerderkurs_bp.route('/foerderkurse/ohne-plan')
@login_required
def ohne_plan():
    return render_template(
        'foerderkurs_ohne_plan.html',
        eintraege=offene_plaene(current_user),
        abstand=ERINNERUNG_ABSTAND_TAGE,
    )


def register_foerderkurs_routes(app):
    app.register_blueprint(foerderkurs_bp)
