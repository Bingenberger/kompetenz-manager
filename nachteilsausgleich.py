"""Nachteilsausgleich: Typen, Notenschutz und die Regeln drumherum.

Bisher war der Nachteilsausgleich ein Haken in den Förderangaben - man sah,
dass es einen gibt, aber nicht welchen. Jetzt steht je Kind und Schuljahr,
was beschlossen wurde: zeitliche Anpassung, Hilfsmittel, didaktische und
räumliche Anpassung, jeweils mit eigenem Text. Dazu kann die Note in den
Teilbereichen Lesen und Rechtschreiben ausgesetzt werden (Notenschutz).

Der Haken in den Förderangaben bleibt als Kennzeichen für die
Stufenauswertung - er wird aber nicht mehr von Hand gesetzt, sondern folgt
diesem Eintrag.
"""

from extensions import db
from klassenzugriff import darf_kind_sehen, zugaengliche_klassen
from models import (
    Foerderangaben,
    Nachteilsausgleich,
    NachteilsausgleichMassnahme,
    Schueler,
    SystemKonfiguration,
)
from time_utils import utc_now

# (Schluessel, Beschriftung, Beispiele fuer das Formular)
TYPEN = [
    ('zeit', 'Zeitliche Anpassung',
     'z. B. verlängerte Arbeitszeit, Pause während einer Arbeit, weniger Aufgaben in derselben Zeit'),
    ('hilfsmittel', 'Hilfsmittel',
     'z. B. Lesepfeil, Anlauttabelle, Wörterbuch, Vorlesen der Aufgabenstellung, Rechenhilfen'),
    ('didaktisch', 'Didaktische Anpassung',
     'z. B. größere Schrift, vergrößerte Vorlage, mündlich statt schriftlich, andere Aufgabenform'),
    ('raum', 'Räumliche Anpassung',
     'z. B. Einzelplatz, ruhiger Nebenraum, Sitzplatz vorn'),
]
TYP_LABEL = {schluessel: label for schluessel, label, _ in TYPEN}

# Teilbereiche, in denen die Note ausgesetzt werden kann.
NOTENSCHUTZ = [
    ('notenschutz_lesen', 'Lesen'),
    ('notenschutz_rechtschreiben', 'Rechtschreiben'),
]


def aktuelles_schuljahr():
    config = SystemKonfiguration.query.first()
    return config.schuljahr if config and config.schuljahr else None


def fuer_kind(schueler_id):
    """Alle Einträge eines Kindes, neuestes Schuljahr zuerst."""
    return (
        Nachteilsausgleich.query
        .filter(Nachteilsausgleich.schueler_id == schueler_id)
        .order_by(Nachteilsausgleich.schuljahr.desc(), Nachteilsausgleich.id.desc())
        .all()
    )


def eintrag_fuer(schueler_id, schuljahr):
    """Der Eintrag dieses Schuljahres, auch wenn er beendet wurde."""
    if not schuljahr:
        return None
    return Nachteilsausgleich.query.filter_by(
        schueler_id=schueler_id, schuljahr=schuljahr).first()


def aktiver(schueler_id, schuljahr=None):
    """Der laufende Eintrag des Schuljahres - sonst None."""
    eintrag = eintrag_fuer(schueler_id, schuljahr or aktuelles_schuljahr())
    return eintrag if eintrag and eintrag.laeuft else None


def hat_nachteilsausgleich(schueler_id, schuljahr):
    return aktiver(schueler_id, schuljahr) is not None


def typen_von(eintrag):
    """Beschriftungen der Typen, zu denen etwas eingetragen ist."""
    vorhanden = {massnahme.typ for massnahme in eintrag.massnahmen}
    return [label for schluessel, label, _ in TYPEN if schluessel in vorhanden]


def notenschutz_von(eintrag):
    return [label for feld, label in NOTENSCHUTZ if getattr(eintrag, feld)]


def kurzfassung(eintrag):
    """Eine Zeile für Listen, Schülerakte und Konferenz."""
    if eintrag is None:
        return ''
    teile = typen_von(eintrag)
    faecher = notenschutz_von(eintrag)
    if faecher:
        teile.append('Note ausgesetzt: ' + ', '.join(faecher))
    text = ' · '.join(teile) or 'ohne Angaben'
    if not eintrag.laeuft:
        text += f' (beendet am {eintrag.beendet_am:%d.%m.%Y})'
    return text


def darf_bearbeiten(user, schueler):
    """Wer das Kind sehen darf, darf den Nachteilsausgleich pflegen."""
    return darf_kind_sehen(user, schueler)


def darf_loeschen(user, eintrag):
    """Löschen darf, wer den Eintrag angelegt hat - dazu Schulleitung und Admin."""
    return bool(user and (user.ist_schulleitung or eintrag.created_by_user_id == user.id))


def speichere(schueler, schuljahr, daten, user):
    """Legt den Eintrag des Schuljahres an oder schreibt ihn fort. Committet nicht.

    daten: beschluss_am, grundlage, eltern_informiert_am, notenschutz_lesen,
    notenschutz_rechtschreiben, notenschutz_beschluss_am, notiz und
    massnahmen ([(typ, beschreibung), ...]).
    """
    eintrag = eintrag_fuer(schueler.id, schuljahr)
    if eintrag is None:
        eintrag = Nachteilsausgleich(schueler_id=schueler.id, schuljahr=schuljahr,
                                     created_by_user_id=getattr(user, 'id', None))
        db.session.add(eintrag)
    eintrag.beschluss_am = daten.get('beschluss_am')
    eintrag.grundlage = (daten.get('grundlage') or '').strip() or None
    eintrag.eltern_informiert_am = daten.get('eltern_informiert_am')
    eintrag.notenschutz_lesen = bool(daten.get('notenschutz_lesen'))
    eintrag.notenschutz_rechtschreiben = bool(daten.get('notenschutz_rechtschreiben'))
    eintrag.notenschutz_beschluss_am = daten.get('notenschutz_beschluss_am')
    eintrag.notiz = (daten.get('notiz') or '').strip() or None
    eintrag.updated_by_user_id = getattr(user, 'id', None)

    setze_massnahmen(eintrag, daten.get('massnahmen') or [])
    db.session.flush()
    synchronisiere_foerderangaben(schueler.id, schuljahr, getattr(user, 'id', None))
    return eintrag


def setze_massnahmen(eintrag, massnahmen):
    """Ersetzt die Maßnahmen; leere Texte fallen weg."""
    eintrag.massnahmen = []
    db.session.flush()
    reihenfolge = {schluessel: nummer for nummer, (schluessel, _, _) in enumerate(TYPEN)}
    for typ, beschreibung in massnahmen:
        text = (beschreibung or '').strip()
        if typ not in TYP_LABEL or not text:
            continue
        eintrag.massnahmen.append(NachteilsausgleichMassnahme(
            typ=typ, beschreibung=text, sort_order=reihenfolge.get(typ, 99)))
    return eintrag.massnahmen


def beende(eintrag, user, datum=None):
    """Der Ausgleich läuft nicht mehr - der Eintrag bleibt als Beleg stehen."""
    eintrag.beendet_am = datum or utc_now().date()
    eintrag.updated_by_user_id = getattr(user, 'id', None)
    db.session.flush()
    synchronisiere_foerderangaben(eintrag.schueler_id, eintrag.schuljahr, getattr(user, 'id', None))
    return eintrag


def nimm_zurueck(eintrag, user):
    """Beendung rückgängig machen."""
    eintrag.beendet_am = None
    eintrag.updated_by_user_id = getattr(user, 'id', None)
    db.session.flush()
    synchronisiere_foerderangaben(eintrag.schueler_id, eintrag.schuljahr, getattr(user, 'id', None))
    return eintrag


def uebernimm(eintrag, schuljahr, user):
    """Kopiert den Eintrag in ein anderes Schuljahr - Beschlüsse gelten neu.

    Übernommen werden Maßnahmen, Notenschutz und Grundlage; die Daten der
    Beschlüsse bleiben leer, denn die Klassenkonferenz entscheidet erneut.
    """
    if not schuljahr or eintrag_fuer(eintrag.schueler_id, schuljahr):
        return None
    kopie = Nachteilsausgleich(
        schueler_id=eintrag.schueler_id, schuljahr=schuljahr,
        grundlage=eintrag.grundlage, notiz=eintrag.notiz,
        notenschutz_lesen=eintrag.notenschutz_lesen,
        notenschutz_rechtschreiben=eintrag.notenschutz_rechtschreiben,
        created_by_user_id=getattr(user, 'id', None),
        updated_by_user_id=getattr(user, 'id', None),
    )
    db.session.add(kopie)
    kopie.massnahmen = [
        NachteilsausgleichMassnahme(typ=massnahme.typ, beschreibung=massnahme.beschreibung,
                                    sort_order=massnahme.sort_order)
        for massnahme in eintrag.massnahmen
    ]
    db.session.flush()
    synchronisiere_foerderangaben(kopie.schueler_id, schuljahr, getattr(user, 'id', None))
    return kopie


def loesche(eintrag, user=None):
    schueler_id, schuljahr = eintrag.schueler_id, eintrag.schuljahr
    db.session.delete(eintrag)
    db.session.flush()
    synchronisiere_foerderangaben(schueler_id, schuljahr, getattr(user, 'id', None))


def synchronisiere_foerderangaben(schueler_id, schuljahr, user_id=None):
    """Der Haken "Nachteilsausgleich" in den Förderangaben folgt dem Eintrag."""
    laeuft = hat_nachteilsausgleich(schueler_id, schuljahr)
    angaben = Foerderangaben.query.filter_by(schueler_id=schueler_id, schuljahr=schuljahr).first()
    if laeuft:
        if angaben is None:
            angaben = Foerderangaben(schueler_id=schueler_id, schuljahr=schuljahr)
            db.session.add(angaben)
        if not angaben.nachteilsausgleich:
            angaben.nachteilsausgleich = True
            angaben.updated_by_user_id = user_id
    elif angaben is not None and angaben.nachteilsausgleich:
        angaben.nachteilsausgleich = False
        angaben.updated_by_user_id = user_id
        db.session.flush()
        if angaben.leer:
            db.session.delete(angaben)
    db.session.flush()
    return laeuft


def uebersicht(user, schuljahr=None, nur_laufende=True):
    """Sichtbare Einträge eines Schuljahres, nach Klasse und Name sortiert."""
    query = Nachteilsausgleich.query.join(Schueler)
    if schuljahr:
        query = query.filter(Nachteilsausgleich.schuljahr == schuljahr)
    if nur_laufende:
        query = query.filter(Nachteilsausgleich.beendet_am.is_(None))
    eintraege = [
        eintrag for eintrag in query.filter(Schueler.is_active.is_(True)).all()
        if darf_kind_sehen(user, eintrag.schueler)
    ]
    return sorted(eintraege, key=lambda e: ((e.schueler.klasse or '').lower(),
                                            (e.schueler.nachname or '').lower(),
                                            (e.schueler.vorname or '').lower()))


def kinder_ohne_eintrag(user, schuljahr):
    """Kinder, die diese Person sehen darf und die noch keinen Eintrag haben."""
    query = Schueler.query.filter(Schueler.is_active.is_(True))
    if not user.sieht_alle_kinder:
        query = query.filter(Schueler.klasse.in_(zugaengliche_klassen(user) or ['']))
    vorhanden = {
        zeile[0] for zeile in
        db.session.query(Nachteilsausgleich.schueler_id)
        .filter(Nachteilsausgleich.schuljahr == schuljahr).all()
    }
    kinder = [kind for kind in query.all()
              if kind.id not in vorhanden and darf_kind_sehen(user, kind)]
    return sorted(kinder, key=lambda k: ((k.klasse or '').lower(), (k.nachname or '').lower(),
                                         (k.vorname or '').lower()))
