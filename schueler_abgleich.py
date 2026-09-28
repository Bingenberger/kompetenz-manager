"""Abgleich von Kinderdaten: Import ohne Dubletten, Dubletten bereinigen.

Der Import legte bisher jede Zeile als neues Kind an. Wird dieselbe
Klassenliste zweimal eingelesen, steht jedes Kind zweimal in der Datenbank -
und die Beobachtungen verteilen sich danach auf beide Einträge. Dieses Modul
vergleicht vor dem Import mit dem Bestand und führt bestehende Dubletten
wieder zusammen.

Verglichen wird über den normalisierten Namen. Das Geburtsdatum entscheidet,
sobald es auf beiden Seiten steht: gleiche Namen mit verschiedenen
Geburtstagen sind zwei Kinder, nicht eines. Fehlt es auf einer Seite,
widerspricht es nicht - in vielen Klassenlisten steht keines.

Beim Zusammenführen gewinnt immer der ältere Eintrag (die kleinere ID): Er ist
der, auf den die übrigen Daten schon zeigen. Alles, was am jüngeren hängt,
wandert zu ihm; nur was dort nicht zweimal stehen darf (Grundlagenblatt,
Diagnostik-Ergebnis, Förderangaben, Nachteilsausgleich, Konferenz- und
Hospitationszeile), wird verworfen, wenn der ältere es schon hat.
"""

import json
from datetime import date

from extensions import db
from jahrgang import ensure_klasse, resolve_student_jahrgang
from models import (
    Beobachtung,
    DiagnostikErgebnis,
    Elternberatung,
    Elternkontakt,
    ErziehungsEreignis,
    ErziehungsEreignisBetroffenesKind,
    Foerderangaben,
    Foerdergrundlage,
    Foerderkonferenz,
    FoerderkonferenzKind,
    FoerderkursTeilnahme,
    Foerderplan,
    HospitationKind,
    Nachteilsausgleich,
    Schueler,
    WorkPlan,
)

# Status einer geplanten Importzeile.
NEU = 'neu'
VORHANDEN = 'vorhanden'
AKTUALISIERT = 'aktualisiert'
MEHRDEUTIG = 'mehrdeutig'
DOPPELT_IN_DATEI = 'doppelt_in_datei'
ARCHIVIERT = 'archiviert'
UNVOLLSTAENDIG = 'unvollstaendig'

STATUS_LABEL = {
    NEU: 'neu',
    VORHANDEN: 'bereits vorhanden',
    AKTUALISIERT: 'wird aktualisiert',
    MEHRDEUTIG: 'mehrere Treffer',
    DOPPELT_IN_DATEI: 'doppelt in der Datei',
    ARCHIVIERT: 'archiviert',
    UNVOLLSTAENDIG: 'unvollständig',
}


# ----------------------------------------------------------------------
# Namen vergleichen
# ----------------------------------------------------------------------

def normalisiert(wert):
    """Kleinschreibung, einfache Leerzeichen - sonst bleibt der Name, wie er ist."""
    return ' '.join((wert or '').split()).casefold()


def namensschluessel(vorname, nachname):
    return (normalisiert(vorname), normalisiert(nachname))


def passt_geburtsdatum(eines, anderes):
    """Zwei Geburtsdaten passen, solange keines dem anderen widerspricht."""
    return eines is None or anderes is None or eines == anderes


def kinder_nach_namen(nur_aktive=False):
    """Namensschlüssel -> Kinder, nach ID sortiert. Ein Durchgang für alle Zeilen."""
    query = Schueler.query
    if nur_aktive:
        query = query.filter(Schueler.is_active.is_(True))
    verzeichnis = {}
    for kind in query.order_by(Schueler.id.asc()).all():
        verzeichnis.setdefault(namensschluessel(kind.vorname, kind.nachname), []).append(kind)
    return verzeichnis


def finde_kinder(vorname, nachname, geburtsdatum=None, verzeichnis=None, nur_aktive=False):
    """Bestandskinder, die zu diesem Namen (und Geburtsdatum) passen."""
    verzeichnis = verzeichnis if verzeichnis is not None else kinder_nach_namen(nur_aktive)
    treffer = verzeichnis.get(namensschluessel(vorname, nachname), [])
    return [kind for kind in treffer if passt_geburtsdatum(kind.geburtsdatum, geburtsdatum)]


# ----------------------------------------------------------------------
# Import planen und ausführen
# ----------------------------------------------------------------------

def plane_import(zeilen):
    """Vergleicht die Zeilen einer Klassenliste mit dem Bestand.

    zeilen: dicts mit vorname, nachname, klasse, jahrgang (Text oder None),
    geburtsdatum (date oder None). Gibt je Zeile einen Plan zurück; angelegt
    oder geändert wird noch nichts.
    """
    verzeichnis = kinder_nach_namen()
    gesehen = {}
    plan = []
    for nummer, zeile in enumerate(zeilen, start=1):
        vorname = (zeile.get('vorname') or '').strip()
        nachname = (zeile.get('nachname') or '').strip()
        klasse = (zeile.get('klasse') or '').strip()
        geburtsdatum = zeile.get('geburtsdatum')
        eintrag = {
            'zeile': nummer,
            'vorname': vorname,
            'nachname': nachname,
            'klasse': klasse,
            'jahrgang_text': (zeile.get('jahrgang') or '').strip() or None,
            'geburtsdatum': geburtsdatum,
            'kind_id': None,
            'aenderungen': [],
            'hinweis': None,
        }

        if not vorname or not nachname:
            eintrag['status'] = UNVOLLSTAENDIG
            eintrag['hinweis'] = 'Vor- oder Nachname fehlt.'
            plan.append(eintrag)
            continue

        schluessel = (namensschluessel(vorname, nachname), geburtsdatum)
        if schluessel in gesehen:
            eintrag['status'] = DOPPELT_IN_DATEI
            eintrag['hinweis'] = f'Steht schon in Zeile {gesehen[schluessel]}.'
            plan.append(eintrag)
            continue
        gesehen[schluessel] = nummer

        treffer = finde_kinder(vorname, nachname, geburtsdatum, verzeichnis=verzeichnis)
        aktive = [kind for kind in treffer if kind.is_active]
        if len(aktive) > 1:
            eintrag['status'] = MEHRDEUTIG
            eintrag['kind_id'] = aktive[0].id
            eintrag['hinweis'] = (f'{len(aktive)} Kinder mit diesem Namen - bitte zuerst '
                                  'die Dubletten bereinigen.')
        elif aktive:
            kind = aktive[0]
            eintrag['kind_id'] = kind.id
            eintrag['aenderungen'] = _abweichungen(kind, klasse, geburtsdatum)
            eintrag['status'] = AKTUALISIERT if eintrag['aenderungen'] else VORHANDEN
        elif treffer:
            kind = treffer[0]
            eintrag['status'] = ARCHIVIERT
            eintrag['kind_id'] = kind.id
            eintrag['hinweis'] = 'Gleicher Name ist archiviert.'
        else:
            eintrag['status'] = NEU
        plan.append(eintrag)
    return plan


def plan_als_json(plan):
    """Der Plan für das Formular der Vorschau - Daten als ISO-Text."""
    return json.dumps([
        {**eintrag,
         'geburtsdatum': eintrag['geburtsdatum'].isoformat() if eintrag.get('geburtsdatum') else None}
        for eintrag in plan
    ], ensure_ascii=False)


def plan_aus_json(text):
    """Liest den Plan aus dem Formular zurück. Fehlerhafte Eingabe ergibt nichts."""
    try:
        roh = json.loads(text or '[]')
    except (TypeError, ValueError):
        return []
    if not isinstance(roh, list):
        return []
    plan = []
    for eintrag in roh:
        if not isinstance(eintrag, dict) or eintrag.get('status') not in STATUS_LABEL:
            continue
        datum = eintrag.get('geburtsdatum')
        try:
            eintrag['geburtsdatum'] = date.fromisoformat(datum) if datum else None
        except (TypeError, ValueError):
            eintrag['geburtsdatum'] = None
        plan.append(eintrag)
    return plan


def _abweichungen(kind, klasse, geburtsdatum):
    """Was sich gegenüber dem Bestand ändern würde - als lesbare Liste."""
    aenderungen = []
    if klasse and (kind.klasse or '').strip() != klasse:
        aenderungen.append(f'Klasse {kind.klasse or "–"} → {klasse}')
    if geburtsdatum and kind.geburtsdatum != geburtsdatum:
        alt = kind.geburtsdatum.strftime('%d.%m.%Y') if kind.geburtsdatum else '–'
        aenderungen.append(f'Geburtsdatum {alt} → {geburtsdatum.strftime("%d.%m.%Y")}')
    return aenderungen


def fuehre_import_aus(plan, aktualisieren=True, archivierte_reaktivieren=False):
    """Legt an und aktualisiert nach Plan. Committet nicht.

    Gibt eine Zählung zurück: angelegt, aktualisiert, unveraendert,
    reaktiviert, uebersprungen, ohne_jahrgang.
    """
    zahlen = {'angelegt': 0, 'aktualisiert': 0, 'unveraendert': 0, 'reaktiviert': 0,
              'uebersprungen': 0, 'ohne_jahrgang': 0}

    for eintrag in plan:
        status = eintrag.get('status')
        if status in (UNVOLLSTAENDIG, DOPPELT_IN_DATEI, MEHRDEUTIG):
            zahlen['uebersprungen'] += 1
            continue
        if status == ARCHIVIERT and not archivierte_reaktivieren:
            zahlen['uebersprungen'] += 1
            continue
        if status == VORHANDEN and not eintrag.get('aenderungen'):
            zahlen['unveraendert'] += 1
            continue

        klasse = (eintrag.get('klasse') or '').strip()
        if klasse:
            ensure_klasse(klasse)
            db.session.flush()
        jahrgang, _fehler = resolve_student_jahrgang(klasse, eintrag.get('jahrgang_text'))
        if jahrgang is None:
            zahlen['ohne_jahrgang'] += 1

        if status == NEU:
            db.session.add(Schueler(
                vorname=eintrag['vorname'], nachname=eintrag['nachname'],
                klasse=klasse, jahrgang=jahrgang, geburtsdatum=eintrag.get('geburtsdatum'),
            ))
            zahlen['angelegt'] += 1
            continue

        kind = db.session.get(Schueler, eintrag.get('kind_id'))
        if kind is None:
            zahlen['uebersprungen'] += 1
            continue
        if status == ARCHIVIERT:
            kind.is_active = True
            kind.archived_at = None
            zahlen['reaktiviert'] += 1
        elif not aktualisieren:
            zahlen['unveraendert'] += 1
            continue
        else:
            zahlen['aktualisiert'] += 1
        if klasse:
            kind.klasse = klasse
            kind.jahrgang = jahrgang
        if eintrag.get('geburtsdatum'):
            kind.geburtsdatum = eintrag['geburtsdatum']
    return zahlen


# ----------------------------------------------------------------------
# Dubletten finden
# ----------------------------------------------------------------------

def dubletten_gruppen():
    """Gruppen gleichnamiger Kinder, älteste ID zuerst.

    Innerhalb eines Namens trennen widersprüchliche Geburtsdaten: Zwei Kinder
    „Lena Schmidt" mit verschiedenen Geburtstagen sind keine Dublette.
    """
    gruppen = []
    for kinder in kinder_nach_namen().values():
        eimer = []  # [(geburtsdatum oder None, [Kinder])]
        for kind in kinder:
            for stelle, (datum, liste) in enumerate(eimer):
                if passt_geburtsdatum(datum, kind.geburtsdatum):
                    liste.append(kind)
                    if datum is None and kind.geburtsdatum is not None:
                        eimer[stelle] = (kind.geburtsdatum, liste)
                    break
            else:
                eimer.append((kind.geburtsdatum, [kind]))
        for _datum, liste in eimer:
            if len(liste) > 1:
                original, *dubletten = sorted(liste, key=lambda k: k.id)
                gruppen.append({
                    'original': original,
                    'dubletten': dubletten,
                    'umfang': {kind.id: datenumfang(kind) for kind in liste},
                })
    return sorted(gruppen, key=lambda g: ((g['original'].nachname or '').lower(),
                                          (g['original'].vorname or '').lower()))


def datenumfang(schueler):
    """Was an einem Kind hängt - für die Anzeige vor dem Zusammenführen."""
    return {
        'Beobachtungen': Beobachtung.query.filter_by(schueler_id=schueler.id).count(),
        'Förderpläne': Foerderplan.query.filter_by(schueler_id=schueler.id).count(),
        'Grundlagenblatt': Foerdergrundlage.query.filter_by(schueler_id=schueler.id).count(),
        'Elternkontakte': Elternkontakt.query.filter_by(schueler_id=schueler.id).count(),
        'Beratungen': Elternberatung.query.filter_by(schueler_id=schueler.id).count(),
        'Ereignisse': ErziehungsEreignis.query.filter_by(student_id=schueler.id).count(),
        'Arbeitspläne': WorkPlan.query.filter_by(student_id=schueler.id).count(),
        'Diagnostik': DiagnostikErgebnis.query.filter_by(schueler_id=schueler.id).count(),
        'Förderangaben': Foerderangaben.query.filter_by(schueler_id=schueler.id).count(),
        'Nachteilsausgleich': Nachteilsausgleich.query.filter_by(schueler_id=schueler.id).count(),
        'Konferenzen': FoerderkonferenzKind.query.filter_by(schueler_id=schueler.id).count(),
        'Hospitationen': HospitationKind.query.filter_by(schueler_id=schueler.id).count(),
        'Förderkurse': FoerderkursTeilnahme.query.filter_by(schueler_id=schueler.id).count(),
    }


def ist_leer(schueler):
    return not any(datenumfang(schueler).values())


# ----------------------------------------------------------------------
# Zusammenführen
# ----------------------------------------------------------------------

def _verschiebe(modell, spalte, dublette_id, original_id):
    """Hängt alle Zeilen eines Modells an das ältere Kind um."""
    anzahl = (modell.query.filter(getattr(modell, spalte) == dublette_id)
              .update({spalte: original_id}, synchronize_session=False))
    return anzahl


def _loese_konflikte(modell, spalte, dublette, original, schluessel):
    """Löscht Zeilen der Dublette, die das Original so schon hat.

    schluessel: Funktion, die aus einer Zeile ihren Eindeutigkeits-Schlüssel
    macht. Gelöscht wird über die ORM, damit die Kaskaden greifen (etwa die
    Maßnahmen eines Nachteilsausgleichs).
    """
    vorhanden = {schluessel(zeile) for zeile
                 in modell.query.filter(getattr(modell, spalte) == original.id).all()}
    verworfen = 0
    for zeile in modell.query.filter(getattr(modell, spalte) == dublette.id).all():
        if schluessel(zeile) in vorhanden:
            db.session.delete(zeile)
            verworfen += 1
    if verworfen:
        db.session.flush()
    return verworfen


def _ergaenze_grundlagenblatt(original_blatt, dublette_blatt):
    """Füllt leere Felder des älteren Grundlagenblatts aus dem jüngeren."""
    felder = ('besondere_staerken', 'vorrangiger_foerderbedarf', 'besonderheiten_entwicklung',
              'wichtige_informationen', 'absprachen_mit_eltern')
    ergaenzt = 0
    for feld in felder:
        alt = (getattr(original_blatt, feld) or '').strip()
        neu = (getattr(dublette_blatt, feld) or '').strip()
        if neu and not alt:
            setattr(original_blatt, feld, neu)
            ergaenzt += 1
    return ergaenzt


def fuehre_zusammen(original, dublette):
    """Hängt alles vom jüngeren Kind an das ältere um. Committet nicht.

    Gibt zurück, was verschoben und was als Doppelung verworfen wurde.
    """
    verschoben = {}
    verworfen = {}

    def merke(ziel, bezeichnung, anzahl):
        if anzahl:
            ziel[bezeichnung] = ziel.get(bezeichnung, 0) + anzahl

    # Grundlagenblatt: Das ältere bleibt, leere Felder werden ergänzt.
    blatt_original = Foerdergrundlage.query.filter_by(schueler_id=original.id).first()
    blatt_dublette = Foerdergrundlage.query.filter_by(schueler_id=dublette.id).first()
    if blatt_dublette is not None:
        if blatt_original is None:
            blatt_dublette.schueler_id = original.id
            merke(verschoben, 'Grundlagenblatt', 1)
        else:
            _ergaenze_grundlagenblatt(blatt_original, blatt_dublette)
            db.session.delete(blatt_dublette)
            merke(verworfen, 'Grundlagenblatt', 1)
        db.session.flush()

    # Zeilen, die je Kind nur einmal existieren dürfen.
    eindeutig = [
        (DiagnostikErgebnis, 'schueler_id', 'Diagnostik',
         lambda z: (z.testform_id, z.schuljahr, z.halbjahr)),
        (Foerderangaben, 'schueler_id', 'Förderangaben', lambda z: z.schuljahr),
        (Nachteilsausgleich, 'schueler_id', 'Nachteilsausgleich', lambda z: z.schuljahr),
        (FoerderkonferenzKind, 'schueler_id', 'Konferenzen', lambda z: z.konferenz_id),
        (HospitationKind, 'schueler_id', 'Hospitationen', lambda z: z.hospitation_id),
    ]
    for modell, spalte, bezeichnung, schluessel in eindeutig:
        merke(verworfen, bezeichnung, _loese_konflikte(modell, spalte, dublette, original, schluessel))
        merke(verschoben, bezeichnung, _verschiebe(modell, spalte, dublette.id, original.id))

    # Ein Kind kann in einem Ereignis nicht zweimal betroffen sein - und nicht
    # zugleich Hauptkind und betroffenes Kind.
    schon_betroffen = {
        zeile.event_id for zeile
        in ErziehungsEreignisBetroffenesKind.query.filter_by(student_id=original.id).all()
    }
    eigene_ereignisse = {
        ereignis.id for ereignis
        in ErziehungsEreignis.query.filter_by(student_id=original.id).all()
    }
    for zeile in ErziehungsEreignisBetroffenesKind.query.filter_by(student_id=dublette.id).all():
        if zeile.event_id in schon_betroffen or zeile.event_id in eigene_ereignisse:
            db.session.delete(zeile)
            merke(verworfen, 'Betroffene Kinder', 1)
        else:
            zeile.student_id = original.id
            merke(verschoben, 'Betroffene Kinder', 1)
    db.session.flush()

    # Alles Übrige wandert unverändert mit.
    einfach = [
        (Beobachtung, 'schueler_id', 'Beobachtungen'),
        (Foerderplan, 'schueler_id', 'Förderpläne'),
        (Elternkontakt, 'schueler_id', 'Elternkontakte'),
        (Elternberatung, 'schueler_id', 'Beratungen'),
        (ErziehungsEreignis, 'student_id', 'Ereignisse'),
        (WorkPlan, 'student_id', 'Arbeitspläne'),
        (FoerderkursTeilnahme, 'schueler_id', 'Förderkurse'),
        (Foerderkonferenz, 'aktuelles_kind_id', 'Konferenz-Marke'),
    ]
    for modell, spalte, bezeichnung in einfach:
        merke(verschoben, bezeichnung, _verschiebe(modell, spalte, dublette.id, original.id))

    db.session.flush()
    db.session.expire_all()
    return {'verschoben': verschoben, 'verworfen': verworfen}
