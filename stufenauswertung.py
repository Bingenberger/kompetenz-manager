"""Stufenauswertung: Ergebnisse einer Testung über die ganze Jahrgangsstufe.

Nachgebaut nach den Auswertungsdokumenten der Schule (HSP, SLS, ELFE II):

1. Kopf mit Schuljahr, Zeitpunkt und Jahrgangsstufe
2. Durchschnittswerte je Klasse und für den Jahrgang
3. Liste der auffälligen Kinder mit Werten, Tendenz zum vorigen Test und
   Förderangaben (Nachteilsausgleich, Förderkurs, Förderplan, externe
   Förderung, Schwerpunkt)

"Auffällig" richtet sich nach den Risikostufen der Anwendung - wer eine Stufe
hat, steht in der Liste. Die Tendenz vergleicht mit dem vorigen Ergebnis
desselben Kennwerts im selben Verfahren.
"""

from dataclasses import dataclass, field
from datetime import date

from diagnostik import (
    FOERDER_KUERZEL,
    STUFE_AUFFAELLIG,
    STUFE_BEOBACHTEN,
    STUFE_DEUTLICH,
    STUFEN,
    TREND_SCHWELLE,
    auswerten,
    foerderangaben_fuer,
    prozentrang_aus,
    schuljahr_zeitraum,
    zeitschluessel,
)
from models import DiagnostikErgebnis, DiagnostikTestform, Foerderplan, Schueler

KUERZEL = {
    'Graphemtreffer': 'GT',
    'Wörter richtig': 'WR',
    'Alphabetische Strategie': 'AS',
    'Orthografische Strategie': 'OS',
    'Morphematische Strategie': 'MS',
    'Wortübergreifende Strategie': 'WÜ',
    'Wortverständnis': 'WV',
    'Satzverständnis': 'SV',
    'Textverständnis': 'TV',
    'Gesamt': 'Gesamt',
    'Leseleistung': 'Lesen',
}
WERTART_KURZ = {'rohwert': 'RW', 'prozentrang': 'PR', 't_wert': 'T', 'lesequotient': 'LQ'}
# Pfeile, die auch Liberation Sans und Arial enthalten (↗/↘ fehlen dort).
TENDENZ_ZEICHEN = {'besser': '↑', 'gleich': '→', 'schlechter': '↓'}
STUFEN_REIHENFOLGE = (STUFE_DEUTLICH, STUFE_AUFFAELLIG, STUFE_BEOBACHTEN)


def kuerzel(name):
    if name in KUERZEL:
        return KUERZEL[name]
    woerter = [w for w in name.split() if w]
    return ''.join(w[0] for w in woerter).upper() if len(woerter) > 1 else name


@dataclass
class DurchschnittSpalte:
    kennwert: str
    art: str

    @property
    def label(self):
        return f'Ø {WERTART_KURZ[self.art]} {kuerzel(self.kennwert)}'


@dataclass
class KlassenZeile:
    klasse: str
    anzahl: int
    durchschnitte: list
    stufen: dict


@dataclass
class ListenZeile:
    schueler: Schueler
    klasse: str
    stufe: str
    werte: list            # [(Kennwert, PR, abgeleitet, Tendenz)] je Risikospalte
    foerderplan: bool
    angaben: object = None  # Foerderangaben oder None
    ausloeser: list = field(default_factory=list)


def _mittel(werte):
    werte = [w for w in werte if w is not None]
    return round(sum(werte) / len(werte), 1) if werte else None


def _haupt_normwert(kennwert):
    for art in ('t_wert', 'lesequotient', 'prozentrang'):
        if getattr(kennwert, art):
            return art
    return None


def _foerderplan_im_schuljahr(schueler_ids, schuljahr):
    """IDs der Kinder mit einem Förderplan, der im Schuljahr galt."""
    beginn, ende = schuljahr_zeitraum(schuljahr)
    if not beginn or not schueler_ids:
        return set()
    plaene = Foerderplan.query.filter(Foerderplan.schueler_id.in_(list(schueler_ids))).all()
    ids = set()
    for plan in plaene:
        erstellt = plan.datum_erstellung
        if hasattr(erstellt, 'date'):
            erstellt = erstellt.date()
        if erstellt and erstellt > ende:
            continue
        evaluiert = plan.datum_evaluation
        if plan.status == 'aktiv' or evaluiert is None or evaluiert >= beginn:
            ids.add(plan.schueler_id)
    return ids


def _vorheriger_prozentrang(kind_ergebnisse, aktuell, kennwert_name, verfahren_id):
    schluessel = zeitschluessel(aktuell.schuljahr, aktuell.halbjahr)
    frueher = [
        e for e in kind_ergebnisse
        if e.id != aktuell.id
        and e.testform.verfahren_id == verfahren_id
        and zeitschluessel(e.schuljahr, e.halbjahr) < schluessel
    ]
    for ergebnis in sorted(frueher, key=lambda e: zeitschluessel(e.schuljahr, e.halbjahr), reverse=True):
        kennwert = next((k for k in ergebnis.testform.kennwerte if k.name == kennwert_name), None)
        if kennwert:
            prozentrang, _ = prozentrang_aus(ergebnis.wert_fuer(kennwert.id))
            if prozentrang is not None:
                return prozentrang
    return None


def _tendenz(frueher, jetzt):
    if frueher is None or jetzt is None:
        return None
    differenz = jetzt - frueher
    if differenz >= TREND_SCHWELLE:
        return 'besser'
    if differenz <= -TREND_SCHWELLE:
        return 'schlechter'
    return 'gleich'


def erstelle_auswertung(verfahren, schuljahr, halbjahr, jahrgang, grenzen, erlaubte_klassen=None):
    """Alle Daten einer Stufenauswertung.

    erlaubte_klassen: Für Lehrkräfte die Namen ihrer heutigen Klassen - dann
    enthält die Auswertung nur deren Kinder. None für die Verwaltung.
    """
    ergebnisse = (
        DiagnostikErgebnis.query
        .join(DiagnostikTestform)
        .filter(
            DiagnostikTestform.verfahren_id == verfahren.id,
            DiagnostikErgebnis.schuljahr == schuljahr,
            DiagnostikErgebnis.halbjahr == halbjahr,
            DiagnostikErgebnis.jahrgang == jahrgang,
        )
        .all()
    )
    if erlaubte_klassen is not None:
        ergebnisse = [e for e in ergebnisse if (e.schueler.klasse or '').strip() in erlaubte_klassen]
    ergebnisse.sort(key=lambda e: ((e.klasse or '').lower(), (e.schueler.nachname or '').lower(), (e.schueler.vorname or '').lower()))

    # Kennwerte in der Reihenfolge der (ersten) Testform, ergänzt um weitere.
    kennwerte = {}
    for ergebnis in ergebnisse:
        for kennwert in ergebnis.testform.kennwerte:
            kennwerte.setdefault(kennwert.name, kennwert)
    kennwert_liste = list(kennwerte.values())

    spalten = []
    for kennwert in kennwert_liste:
        if kennwert.leitwert and kennwert.rohwert:
            spalten.append(DurchschnittSpalte(kennwert.name, 'rohwert'))
        haupt = _haupt_normwert(kennwert)
        if haupt:
            spalten.append(DurchschnittSpalte(kennwert.name, haupt))
    risikospalten = [k.name for k in kennwert_liste if k.risiko]

    auswertungen = {e.id: auswerten(e, grenzen) for e in ergebnisse}

    def wert(ergebnis, spalte):
        kennwert = next((k for k in ergebnis.testform.kennwerte if k.name == spalte.kennwert), None)
        eintrag = ergebnis.wert_fuer(kennwert.id) if kennwert else None
        return getattr(eintrag, spalte.art) if eintrag else None

    def zeile(name, gruppe):
        stufen = {stufe: 0 for stufe in STUFEN_REIHENFOLGE}
        for ergebnis in gruppe:
            stufe = auswertungen[ergebnis.id].stufe
            if stufe:
                stufen[stufe] += 1
        return KlassenZeile(
            klasse=name,
            anzahl=len(gruppe),
            durchschnitte=[_mittel(wert(e, spalte) for e in gruppe) for spalte in spalten],
            stufen=stufen,
        )

    klassen = {}
    for ergebnis in ergebnisse:
        klassen.setdefault(ergebnis.klasse or '–', []).append(ergebnis)
    klassen_zeilen = [zeile(name, gruppe) for name, gruppe in sorted(klassen.items(), key=lambda p: p[0].lower())]
    gesamt = zeile(f'Jahrgang {jahrgang}', ergebnisse) if ergebnisse else None

    auffaellig = [e for e in ergebnisse if auswertungen[e.id].stufe]
    kind_ids = {e.schueler_id for e in auffaellig}
    angaben = foerderangaben_fuer(kind_ids, schuljahr)
    mit_foerderplan = _foerderplan_im_schuljahr(kind_ids, schuljahr)
    liste = []
    for ergebnis in auffaellig:
        auswertung = auswertungen[ergebnis.id]
        werte = []
        for name in risikospalten:
            kennwert = next((k for k in ergebnis.testform.kennwerte if k.name == name), None)
            prozentrang, abgeleitet = prozentrang_aus(ergebnis.wert_fuer(kennwert.id)) if kennwert else (None, False)
            frueher = _vorheriger_prozentrang(ergebnis.schueler.diagnostik_ergebnisse, ergebnis, name, verfahren.id)
            werte.append((name, prozentrang, abgeleitet, _tendenz(frueher, prozentrang)))
        liste.append(ListenZeile(
            schueler=ergebnis.schueler,
            klasse=ergebnis.klasse or '–',
            stufe=auswertung.stufe,
            werte=werte,
            foerderplan=ergebnis.schueler_id in mit_foerderplan,
            angaben=angaben.get(ergebnis.schueler_id),
            ausloeser=auswertung.ausloeser,
        ))

    daten = sorted(e.datum for e in ergebnisse if e.datum)
    return {
        'verfahren': verfahren,
        'schuljahr': schuljahr,
        'halbjahr': halbjahr,
        'jahrgang': jahrgang,
        'testformen': sorted({e.testform.name for e in ergebnisse}),
        'anzahl': len(ergebnisse),
        'datum_von': daten[0] if daten else None,
        'datum_bis': daten[-1] if daten else None,
        'spalten': spalten,
        'klassen': klassen_zeilen,
        'gesamt': gesamt,
        'risikospalten': risikospalten,
        'liste': liste,
        'kuerzel': kuerzel,
        'stufen': STUFEN,
        'stufen_reihenfolge': STUFEN_REIHENFOLGE,
        'foerder_kuerzel': FOERDER_KUERZEL,
        'tendenz_zeichen': TENDENZ_ZEICHEN,
        'grenzen': grenzen,
        'erstellt_am': date.today(),
    }


def legende(auswertung):
    teile = [f'{kuerzel(name)} = {name}' for name in auswertung['risikospalten'] if kuerzel(name) != name]
    teile += [f'{kurz} = {lang}' for _, kurz, lang in FOERDER_KUERZEL]
    teile.append('FP = Förderplan')
    teile.append('↑ / → / ↓ = Prozentrang mindestens 10 Punkte höher / etwa gleich / mindestens 10 Punkte niedriger als beim vorigen Test')
    teile.append('* = Prozentrang aus T-Wert oder Lesequotient abgeleitet')
    return 'Legende: ' + ', '.join(teile)
