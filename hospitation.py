"""Hospitationen der Schulleitung.

Anlassbezogen und meist im Vorfeld der Förderkonferenz - gelegentlich noch
bevor die Klassenleitung ihre Einschätzung abgibt. Eine Hospitation gehört zu
einer Klasse und einem Tag; beobachtet werden nur die Kinder, um die es geht.

Sichtbarkeit: Schreiben und lesen darf die Schulleitung (und die Verwaltung).
Gibt sie eine Hospitation frei, sehen sie auch alle, die das Kind ohnehin
klassengebunden sehen (Klassenleitung, Fachlehrkräfte, Förderpädagogik).
"""

from datetime import date

from extensions import db
from klassenzugriff import darf_kind_sehen
from models import Hospitation, HospitationKind, KONFERENZ_STUFEN, Schueler
from school_year import normalize_school_year
from time_utils import utc_now

HOSPITATION_FELDER = {'datum': 'datum', 'anlass': 'text', 'notiz': 'text', 'freigegeben': 'bool'}
KIND_FELDER = {'beobachtung': 'text', 'empfehlung_stufe': 'stufe', 'stern': 'bool'}


def darf_hospitieren(user):
    return bool(getattr(user, 'ist_schulleitung', False))


def wert_aus(typ, roh):
    if typ == 'bool':
        return str(roh).strip().lower() in ('1', 'true', 'ja', 'on')
    text = '' if roh is None else str(roh).strip()
    if typ == 'stufe':
        return text.upper() if text.upper() in KONFERENZ_STUFEN else None
    if typ == 'datum':
        try:
            return date.fromisoformat(text) if text else None
        except ValueError:
            return None
    return text or None


def schreibe_feld(objekt, feld, roh):
    felder = KIND_FELDER if isinstance(objekt, HospitationKind) else HOSPITATION_FELDER
    wert = wert_aus(felder[feld], roh)
    if feld == 'datum' and wert is None:
        return objekt.datum          # ein Datum muss bleiben
    setattr(objekt, feld, wert)
    objekt.bearbeitet_am = utc_now()
    return wert


def kinder_der_klasse(klasse):
    return (
        Schueler.query
        .filter(db.func.trim(Schueler.klasse) == (klasse or '').strip(), Schueler.is_active.is_(True))
        .order_by(Schueler.nachname, Schueler.vorname)
        .all()
    )


def schuljahr_von(datum):
    """'2026/2027' für ein Datum - das Schuljahr beginnt am 1. August."""
    beginn = datum.year if datum.month >= 8 else datum.year - 1
    return f'{beginn}/{beginn + 1}'


def sichtbare_eintraege(schueler, user, schuljahr=None):
    """Hospitationsnotizen zu einem Kind, die diese Person sehen darf, neueste zuerst."""
    query = HospitationKind.query.join(Hospitation).filter(HospitationKind.schueler_id == schueler.id)
    if not darf_hospitieren(user):
        if not darf_kind_sehen(user, schueler):
            return []
        query = query.filter(Hospitation.freigegeben.is_(True))
    eintraege = query.order_by(Hospitation.datum.desc(), Hospitation.id.desc()).all()
    if schuljahr:
        schuljahr = normalize_school_year(schuljahr) or schuljahr
        eintraege = [e for e in eintraege if schuljahr_von(e.hospitation.datum) == schuljahr]
    return eintraege


def empfehlungen(schueler_ids, schuljahr):
    """schueler_id -> jüngste Stufen-Empfehlung aus Hospitationen des Schuljahres."""
    if not schueler_ids:
        return {}
    ergebnis = {}
    eintraege = (
        HospitationKind.query.join(Hospitation)
        .filter(HospitationKind.schueler_id.in_(list(schueler_ids)), HospitationKind.empfehlung_stufe.isnot(None))
        .order_by(Hospitation.datum.asc(), Hospitation.id.asc())
        .all()
    )
    for eintrag in eintraege:
        if schuljahr_von(eintrag.hospitation.datum) == schuljahr:
            ergebnis[eintrag.schueler_id] = eintrag
    return ergebnis
