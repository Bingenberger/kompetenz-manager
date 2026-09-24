"""Förderkurse: Katalog, Teilnahmen und die Regel zum Förderplan.

Die Verwaltung pflegt Fächer und dazu Förderkurse je Jahrgang. Lehrkräfte
weisen Kinder einem Kurs zu - wer ein Kind sehen darf, darf es auch eintragen.

Die Regel: Wer in einem Förderkurs ist, braucht im selben Fach einen aktiven
Förderplan. Fehlt er, erscheint das als Aufgabe auf der Startseite, in einer
eigenen Liste und - alle paar Tage erneut - als Benachrichtigung bei der
Klassenleitung und der Kursleitung.
"""

from datetime import timedelta

from extensions import db
from klassenzugriff import darf_kind_sehen
from models import Fach, Foerderkurs, FoerderkursJahrgang, FoerderkursTeilnahme, Foerderplan, Schueler, SystemKonfiguration
from student_selection import get_user_klassenkontext
from time_utils import utc_now
from transition_plan import effective_jahrgang

# So oft wird an einen fehlenden Förderplan erinnert.
ERINNERUNG_ABSTAND_TAGE = 14


def aktuelles_schuljahr():
    config = SystemKonfiguration.query.first()
    return config.schuljahr if config and config.schuljahr else None


def faecher(nur_aktive=True):
    query = Fach.query
    if nur_aktive:
        query = query.filter(Fach.is_active.is_(True))
    return query.order_by(Fach.sort_order, Fach.name).all()


def kurse(schuljahr=None, jahrgang=None, nur_aktive=True):
    """Kurse des Schuljahres - Kurse ohne Schuljahr laufen immer mit."""
    query = Foerderkurs.query.join(Fach)
    if nur_aktive:
        query = query.filter(Foerderkurs.is_active.is_(True))
    if schuljahr:
        query = query.filter(db.or_(Foerderkurs.schuljahr == schuljahr, Foerderkurs.schuljahr.is_(None)))
    liste = query.order_by(Fach.sort_order, Fach.name, Foerderkurs.name).all()
    if jahrgang is not None:
        liste = [kurs for kurs in liste if kurs.gilt_fuer(jahrgang)]
    return liste


def kurse_fuer_kind(schueler, schuljahr=None):
    """Kurse, die für dieses Kind in Frage kommen (Jahrgang des Kindes)."""
    return kurse(schuljahr or aktuelles_schuljahr(), effective_jahrgang(schueler))


def setze_jahrgaenge(kurs, jahrgaenge):
    FoerderkursJahrgang.query.filter_by(kurs_id=kurs.id).delete()
    for jahrgang in sorted({int(j) for j in jahrgaenge}):
        db.session.add(FoerderkursJahrgang(kurs_id=kurs.id, jahrgang=jahrgang))


# ----------------------------------------------------------------------
# Teilnahmen
# ----------------------------------------------------------------------

def laufende_teilnahmen(schueler=None, kurs=None):
    query = FoerderkursTeilnahme.query.filter(FoerderkursTeilnahme.bis.is_(None))
    if schueler is not None:
        query = query.filter(FoerderkursTeilnahme.schueler_id == schueler.id)
    if kurs is not None:
        query = query.filter(FoerderkursTeilnahme.kurs_id == kurs.id)
    return query.all()


def trage_ein(kurs, schueler, user=None, notiz=None):
    """Nimmt ein Kind in den Kurs auf, falls es nicht schon läuft. Committet nicht."""
    vorhanden = FoerderkursTeilnahme.query.filter_by(
        kurs_id=kurs.id, schueler_id=schueler.id, bis=None).first()
    if vorhanden:
        return vorhanden
    teilnahme = FoerderkursTeilnahme(
        kurs_id=kurs.id, schueler_id=schueler.id, notiz=notiz,
        eingetragen_von_user_id=getattr(user, 'id', None),
    )
    db.session.add(teilnahme)
    return teilnahme


def beende(teilnahme, datum=None):
    teilnahme.bis = datum or utc_now().date()
    return teilnahme


def darf_zuweisen(user, schueler):
    """Wer das Kind sehen darf, darf es auch in einen Kurs eintragen."""
    return darf_kind_sehen(user, schueler)


# ----------------------------------------------------------------------
# Regel: Kurs braucht einen Förderplan im selben Fach
# ----------------------------------------------------------------------

def aktiver_plan_im_fach(schueler_id, fach_id):
    return (
        Foerderplan.query
        .filter(Foerderplan.schueler_id == schueler_id,
                Foerderplan.status == 'aktiv',
                Foerderplan.fach_id == fach_id)
        .order_by(Foerderplan.datum_erstellung.desc(), Foerderplan.id.desc())
        .first()
    )


def offene_plaene(user=None, nur_eigene_klasse=False):
    """Laufende Teilnahmen ohne aktiven Förderplan im Fach des Kurses.

    user: schränkt auf das ein, was die Person sehen darf. nur_eigene_klasse:
    zusätzlich auf die eigene Klassenleitung und die eigenen Kurse.
    """
    eintraege = []
    kontext = get_user_klassenkontext(user) if user is not None else {}
    eigene_klasse = (kontext.get('klassenleitung') or '').strip()
    for teilnahme in (
        FoerderkursTeilnahme.query
        .join(Foerderkurs)
        .filter(FoerderkursTeilnahme.bis.is_(None), Foerderkurs.is_active.is_(True))
        .all()
    ):
        schueler = teilnahme.schueler
        if not schueler or not schueler.is_active:
            continue
        if user is not None and not darf_kind_sehen(user, schueler):
            continue
        if nur_eigene_klasse and user is not None:
            eigene = (schueler.klasse or '').strip() == eigene_klasse and eigene_klasse
            leitet = teilnahme.kurs.leitung_user_id == getattr(user, 'id', None)
            if not (eigene or leitet):
                continue
        if aktiver_plan_im_fach(schueler.id, teilnahme.kurs.fach_id) is None:
            eintraege.append(teilnahme)
    return sorted(eintraege, key=lambda t: ((t.schueler.klasse or '').lower(),
                                            (t.schueler.nachname or '').lower(),
                                            (t.schueler.vorname or '').lower()))


def zustaendige_user_ids(teilnahme):
    """Klassenleitung des Kindes und Leitung des Kurses."""
    from models import UserKlassenzuordnung

    klasse = (teilnahme.schueler.klasse or '').strip()
    ids = set()
    if klasse:
        ids.update(
            zuordnung.user_id for zuordnung in UserKlassenzuordnung.query.filter(
                UserKlassenzuordnung.rolle == 'klassenleitung',
                UserKlassenzuordnung.klasse == klasse,
            ).all()
        )
    if teilnahme.kurs.leitung_user_id:
        ids.add(teilnahme.kurs.leitung_user_id)
    return ids


def faellige_erinnerungen(heute=None, abstand=ERINNERUNG_ABSTAND_TAGE):
    """Teilnahmen ohne Förderplan, an die wieder erinnert werden soll."""
    heute = heute or utc_now().date()
    grenze = heute - timedelta(days=abstand)
    return [
        teilnahme for teilnahme in offene_plaene()
        if teilnahme.erinnert_am is None or teilnahme.erinnert_am <= grenze
    ]


def kinder_ohne_kurs(kurs, schuljahr=None):
    """Kinder der passenden Jahrgänge, die noch nicht im Kurs sind."""
    drin = {t.schueler_id for t in laufende_teilnahmen(kurs=kurs)}
    kinder = []
    for kind in Schueler.query.filter(Schueler.is_active.is_(True)).all():
        if kind.id in drin:
            continue
        if kurs.jahrgaenge and effective_jahrgang(kind) not in kurs.jahrgaenge:
            continue
        kinder.append(kind)
    return sorted(kinder, key=lambda k: ((k.klasse or '').lower(), (k.nachname or '').lower(), (k.vorname or '').lower()))
