"""Standardisierte Diagnostik: Auswertung, Risikostufen und Verlauf.

Verglichen wird auf zwei Skalen:

- Prozentrang (PR) für Tests, die ihn ausweisen (HSP, ELFE II). Fehlt er, wird
  er aus dem T-Wert (Mittel 50, Streuung 10) über die Normalverteilung
  abgeleitet und als abgeleitet gekennzeichnet.
- Lesequotient (LQ) für Tests, die nur Rohwert und LQ liefern (SLS). Ein
  Kennwert gehört zu dieser Skala, wenn er einen LQ, aber keinen PR hat. Die
  Schule ordnet den LQ nach eigenen Grenzen ein (unter 90 unterdurchschnittlich,
  unter 80 schwach, unter 70 sehr schwach); daraus werden eigene Risikogrenzen,
  und ein PR wird für diese Tests weder angezeigt noch errechnet.

Ergebnisse unterschiedlicher Skalen werden nicht gegeneinander verrechnet:
kein Trend von LQ zu PR, und das Diagramm zeigt je Skala eine eigene Grafik.
"""

from dataclasses import dataclass, field
from datetime import date
from math import erf, sqrt

from extensions import db
from models import (
    DiagnostikErgebnis,
    DiagnostikKennwert,
    DiagnostikTestform,
    DiagnostikVerfahren,
    DiagnostikWert,
    DiagnostikZeitpunkt,
    SystemKonfiguration,
)
from jahrgang import grade_from_name, name_for_grade
from transition_plan import effective_jahrgang

HALBJAHRE = {'mitte': 'Mitte', 'ende': 'Ende'}

# (Schlüssel, Bezeichnung, Kürzel, kleinster, größter zulässiger Wert)
WERTARTEN = [
    ('rohwert', 'Rohwert', 'RW', 0, 9999),
    ('prozentrang', 'Prozentrang', 'PR', 0, 100),
    ('t_wert', 'T-Wert', 'T', 10, 90),
    ('lesequotient', 'Lesequotient', 'LQ', 40, 160),
]
WERTART_KUERZEL = {schluessel: kuerzel for schluessel, _, kuerzel, _, _ in WERTARTEN}
WERTART_BEREICH = {schluessel: (minimum, maximum) for schluessel, _, _, minimum, maximum in WERTARTEN}

# Risikostufen, schwerste zuerst.
STUFE_DEUTLICH = 'deutlich'
STUFE_AUFFAELLIG = 'auffaellig'
STUFE_BEOBACHTEN = 'beobachten'
STUFEN = {
    STUFE_DEUTLICH: ('deutlich auffällig', 'danger'),
    STUFE_AUFFAELLIG: ('auffällig', 'warning'),
    STUFE_BEOBACHTEN: ('beobachten', 'info'),
}
STANDARD_GRENZEN = {STUFE_BEOBACHTEN: 25, STUFE_AUFFAELLIG: 16, STUFE_DEUTLICH: 10}
# LQ-Grenzen (einschließlich) nach der Auswertungstabelle SLS der Schule:
# unter 90 unterdurchschnittlich, unter 80 schwach, unter 70 sehr schwach.
STANDARD_GRENZEN_LQ = {STUFE_BEOBACHTEN: 89, STUFE_AUFFAELLIG: 79, STUFE_DEUTLICH: 69}

SKALA_PR = 'pr'
SKALA_LQ = 'lq'
SKALEN = {
    SKALA_PR: {'kuerzel': 'PR', 'name': 'Prozentrang', 'min': 0, 'max': 100, 'ticks': [0, 25, 50, 75, 100]},
    SKALA_LQ: {'kuerzel': 'LQ', 'name': 'Lesequotient', 'min': 50, 'max': 150, 'ticks': [50, 70, 90, 110, 130, 150]},
}

# Ab dieser Veränderung (PR- bzw. LQ-Punkte) gilt ein Verlauf als Bewegung.
# Beim LQ sind zehn Punkte etwa zwei Messfehler des SLS und eine Stufe der
# Auswertungstabelle - kleinere Schwankungen sind kein Fortschritt.
TREND_SCHWELLE = 10


# ----------------------------------------------------------------------
# Werte und Stufen
# ----------------------------------------------------------------------

def _normalverteilung(z):
    return 0.5 * (1 + erf(z / sqrt(2)))


def prozentrang_aus(wert):
    """(Prozentrang, abgeleitet?) für einen DiagnostikWert, sonst (None, False)."""
    if wert is None:
        return None, False
    if wert.prozentrang is not None:
        return wert.prozentrang, False
    if wert.t_wert is not None:
        z = (wert.t_wert - 50) / 10
    elif wert.lesequotient is not None:
        z = (wert.lesequotient - 100) / 15
    else:
        return None, False
    return max(0, min(100, round(_normalverteilung(z) * 100))), True


class Grenzen(dict):
    """Stufe -> Prozentrang-Grenze; die LQ-Grenzen hängen als .lq daran."""

    def __init__(self, pr, lq=None):
        super().__init__(pr)
        self.lq = dict(STANDARD_GRENZEN_LQ if lq is None else lq)

    def fuer(self, skala):
        return self.lq if skala == SKALA_LQ else self


def grenzen_fuer(grenzen, skala):
    """Die Grenzen einer Skala - auch für ein schlichtes dict (nur PR)."""
    if skala == SKALA_LQ:
        return getattr(grenzen, 'lq', STANDARD_GRENZEN_LQ)
    return grenzen


def risikogrenzen(config=None):
    """Stufe -> Grenze (einschließlich) je Skala; ausgeschaltete Stufen fehlen."""
    config = config if config is not None else SystemKonfiguration.query.first()
    if config is None:
        return Grenzen(STANDARD_GRENZEN)
    pr = {
        STUFE_BEOBACHTEN: config.diagnostik_pr_beobachten,
        STUFE_AUFFAELLIG: config.diagnostik_pr_auffaellig,
        STUFE_DEUTLICH: config.diagnostik_pr_deutlich,
    }
    lq = {
        STUFE_BEOBACHTEN: config.diagnostik_lq_beobachten,
        STUFE_AUFFAELLIG: config.diagnostik_lq_auffaellig,
        STUFE_DEUTLICH: config.diagnostik_lq_deutlich,
    }
    return Grenzen(
        {stufe: grenze for stufe, grenze in pr.items() if grenze is not None},
        {stufe: grenze for stufe, grenze in lq.items() if grenze is not None},
    )


def grenze_text(grenzen, skala=SKALA_PR):
    """'bis PR 25' - die weiteste eingeschaltete Grenze einer Skala."""
    werte = grenzen_fuer(grenzen, skala)
    grenze = werte.get(STUFE_BEOBACHTEN) or werte.get(STUFE_AUFFAELLIG) or werte.get(STUFE_DEUTLICH)
    return f'bis {SKALEN[skala]["kuerzel"]} {grenze}' if grenze is not None else None


def vergleichswert(kennwert, wert):
    """(Skala, Wert, abgeleitet?) eines eingetragenen Werts, sonst (Skala, None, False)."""
    skala = kennwert.skala
    if skala == SKALA_LQ:
        lq = wert.lesequotient if wert is not None else None
        return skala, lq, False
    prozentrang, abgeleitet = prozentrang_aus(wert)
    return skala, prozentrang, abgeleitet


def stufe_fuer(wert, grenzen):
    """Die schwerste Stufe, deren Grenze der Wert erreicht (PR oder LQ - je nach Grenzen)."""
    if wert is None:
        return None
    for stufe in (STUFE_DEUTLICH, STUFE_AUFFAELLIG, STUFE_BEOBACHTEN):
        grenze = grenzen.get(stufe)
        if grenze is not None and wert <= grenze:
            return stufe
    return None


def schwerste_stufe(stufen):
    for stufe in (STUFE_DEUTLICH, STUFE_AUFFAELLIG, STUFE_BEOBACHTEN):
        if stufe in stufen:
            return stufe
    return None


@dataclass
class Leitwert:
    kennwert: DiagnostikKennwert
    wert: int
    abgeleitet: bool
    stufe: str
    skala: str = SKALA_PR

    @property
    def prozentrang(self):
        return self.wert if self.skala == SKALA_PR else None

    @property
    def kuerzel(self):
        return SKALEN[self.skala]['kuerzel']

    @property
    def anzeige(self):
        """'PR 12' oder 'LQ 85'."""
        return f'{self.kuerzel} {self.wert}'


@dataclass
class Auswertung:
    ergebnis: DiagnostikErgebnis
    # Leitwerte: Verlauf und angezeigter Prozentrang
    leitwerte: list = field(default_factory=list)
    # Risikowerte: bestimmen die Stufe
    risikowerte: list = field(default_factory=list)

    @property
    def stufe(self):
        return schwerste_stufe({wert.stufe for wert in self.risikowerte})

    @property
    def ausloeser(self):
        """Die Kennwerte, die die Stufe verursachen, niedrigster Prozentrang zuerst."""
        stufe = self.stufe
        if not stufe:
            return []
        return sorted((w for w in self.risikowerte if w.stufe == stufe), key=lambda w: (w.skala, w.wert))

    @property
    def hat_werte(self):
        return bool(self.leitwerte or self.risikowerte)

    @property
    def niedrigster_prozentrang(self):
        werte = [leitwert.wert for leitwert in self.leitwerte if leitwert.skala == SKALA_PR]
        return min(werte) if werte else None

    @property
    def schwaechster_leitwert(self):
        """Der niedrigste Leitwert - Prozentränge vor Lesequotienten, falls beides vorkommt."""
        for skala in (SKALA_PR, SKALA_LQ):
            werte = [leitwert for leitwert in self.leitwerte if leitwert.skala == skala]
            if werte:
                return min(werte, key=lambda w: w.wert)
        return None


def auswerten(ergebnis, grenzen):
    """Prozentränge und Stufen der Leitwerte eines Ergebnisses."""
    auswertung = Auswertung(ergebnis)
    for kennwert in ergebnis.testform.kennwerte:
        if not (kennwert.leitwert or kennwert.risiko):
            continue
        skala, vergleich, abgeleitet = vergleichswert(kennwert, ergebnis.wert_fuer(kennwert.id))
        if vergleich is None:
            continue
        wert = Leitwert(
            kennwert, vergleich, abgeleitet,
            stufe_fuer(vergleich, grenzen_fuer(grenzen, skala)) if kennwert.risiko else None,
            skala,
        )
        if kennwert.leitwert:
            auswertung.leitwerte.append(wert)
        if kennwert.risiko:
            auswertung.risikowerte.append(wert)
    return auswertung


# ----------------------------------------------------------------------
# Zeit
# ----------------------------------------------------------------------

def zeitschluessel(schuljahr, halbjahr):
    """Sortierschlüssel: Schuljahr, dann Mitte vor Ende."""
    try:
        beginn = int((schuljahr or '')[:4])
    except ValueError:
        beginn = 0
    return beginn, 0 if halbjahr == 'mitte' else 1


def zeitlabel(schuljahr, halbjahr):
    return f'{HALBJAHRE.get(halbjahr, halbjahr)} {schuljahr}'


def aktuelles_halbjahr(heute=None):
    """Vorschlag für die Eingabe: Mitte im Winter, sonst Ende."""
    heute = heute or date.today()
    return 'mitte' if heute.month in (11, 12, 1, 2, 3) else 'ende'


# Nachträge reichen so weit zurück, dass ein Kind am Ende der Grundschulzeit
# auch mit einer Wiederholung seine Tests ab Klasse 1 bekommt.
SCHULJAHRE_ZURUECK = 5


def schuljahr_auswahl(aktuell, gewaehlt=None):
    """Aktuelles Schuljahr und die fünf davor, neueste zuerst."""
    jahre = []
    if aktuell and aktuell[:4].isdigit():
        beginn = int(aktuell[:4])
        jahre = [f'{beginn - n}/{beginn - n + 1}' for n in range(SCHULJAHRE_ZURUECK + 1)]
    if gewaehlt and gewaehlt not in jahre:
        jahre.append(gewaehlt)
    return jahre


def schuljahr_zeitraum(schuljahr):
    """(1. August, 31. Juli) eines Schuljahres - großzügig, damit auch Tests
    kurz vor den Sommerferien oder direkt nach dem Start hineinpassen."""
    if not schuljahr or not schuljahr[:4].isdigit():
        return None, None
    beginn = int(schuljahr[:4])
    return date(beginn, 8, 1), date(beginn + 1, 7, 31)


def datum_im_schuljahr(datum, schuljahr):
    von, bis = schuljahr_zeitraum(schuljahr)
    return bool(datum and von and von <= datum <= bis)


def jahre_zurueck(schuljahr, aktuelles_schuljahr):
    try:
        return int(aktuelles_schuljahr[:4]) - int(schuljahr[:4])
    except (TypeError, ValueError):
        return 0


def jahrgang_im_schuljahr(kind, schuljahr, aktuelles_schuljahr):
    """Der Jahrgang, den ein Kind im angegebenen Schuljahr hatte.

    Zurückgerechnet vom heutigen Jahrgang: Wer jetzt in Klasse 4 ist, war vor
    drei Jahren in Klasse 1. Eine Wiederholung kennt die Anwendung nicht - bei
    einem Wiederholer liegt der Wert um ein Jahr daneben. Außerhalb von 1 bis 4
    gibt es keinen Jahrgang.
    """
    heute = effective_jahrgang(kind)
    if heute is None:
        return None
    damals = heute - jahre_zurueck(schuljahr, aktuelles_schuljahr)
    return damals if 1 <= damals <= 4 else None


def klasse_im_schuljahr(kind, jahrgang_damals):
    """Die Klasse, in der ein Kind damals war.

    Für Klassen mit Stufennamen zurückgerechnet ("3c" mit Jahrgang 1 wird
    "1c"), sonst die heutige Klasse - bei freien Namen wie "Füchse" wandert
    die Gruppe als Ganzes.
    """
    heute = (kind.klasse or '').strip() or None
    if heute and jahrgang_damals and grade_from_name(heute) is not None:
        return name_for_grade(heute, jahrgang_damals)
    return heute


def trend(frueher, spaeter):
    if frueher is None or spaeter is None:
        return None
    differenz = spaeter - frueher
    if differenz >= TREND_SCHWELLE:
        return 'besser'
    if differenz <= -TREND_SCHWELLE:
        return 'schlechter'
    return 'gleich'


# ----------------------------------------------------------------------
# Verlauf eines Kindes
# ----------------------------------------------------------------------

def verlauf(schueler, grenzen):
    """Die Ergebnisse eines Kindes nach Lernbereich, chronologisch.

    Rückgabe: Liste von dicts je Bereich mit
      'bereich', 'auswertungen' (chronologisch),
      'reihen' (Kennwertname -> [(zeitschlüssel, Wert, abgeleitet, stufe)]),
      'skalen' (Kennwertname -> 'pr' oder 'lq'),
      'zeiten' (sortierte Zeitschlüssel mit Label),
      'aktuell' (letzte Auswertung), 'trend'.
    """
    ergebnisse = sorted(
        schueler.diagnostik_ergebnisse,
        key=lambda e: (zeitschluessel(e.schuljahr, e.halbjahr), e.datum or date.min, e.id),
    )
    bereiche = {}
    for ergebnis in ergebnisse:
        bereich = ergebnis.testform.verfahren.bereich
        eintrag = bereiche.setdefault(bereich, {'bereich': bereich, 'auswertungen': [], 'reihen': {}, 'skalen': {}, 'zeiten': {}})
        auswertung = auswerten(ergebnis, grenzen)
        eintrag['auswertungen'].append(auswertung)
        schluessel = zeitschluessel(ergebnis.schuljahr, ergebnis.halbjahr)
        eintrag['zeiten'][schluessel] = zeitlabel(ergebnis.schuljahr, ergebnis.halbjahr)
        for leitwert in auswertung.leitwerte:
            name = f'{leitwert.kennwert.name} ({ergebnis.testform.verfahren.name})'
            eintrag['reihen'].setdefault(name, []).append((schluessel, leitwert.wert, leitwert.abgeleitet, leitwert.stufe))
            eintrag['skalen'][name] = leitwert.skala

    ergebnis_liste = []
    for eintrag in bereiche.values():
        eintrag['zeiten'] = sorted(eintrag['zeiten'].items())
        mit_werten = [a for a in eintrag['auswertungen'] if a.hat_werte]
        eintrag['aktuell'] = mit_werten[-1] if mit_werten else None
        vorher = mit_werten[-2] if len(mit_werten) > 1 else None
        jetzt = eintrag['aktuell'].schwaechster_leitwert if eintrag['aktuell'] else None
        frueher = vorher.schwaechster_leitwert if vorher else None
        # Nur auf derselben Skala vergleichbar: vom SLS (LQ) zu ELFE II (PR) gibt es keinen Trend.
        vergleichbar = jetzt is not None and frueher is not None and jetzt.skala == frueher.skala
        eintrag['trend'] = trend(frueher.wert, jetzt.wert) if vergleichbar else None
        ergebnis_liste.append(eintrag)
    return sorted(ergebnis_liste, key=lambda e: e['bereich'].lower())


def skalen_im_verlauf(eintrag):
    """Die Skalen eines Bereichs in der Reihenfolge ihres ersten Auftretens."""
    erste = {}
    for name, punkte in eintrag['reihen'].items():
        skala = eintrag.get('skalen', {}).get(name, SKALA_PR)
        beginn = min(p[0] for p in punkte)
        erste[skala] = min(erste.get(skala, beginn), beginn)
    return sorted(erste, key=lambda skala: erste[skala])


def diagramm(eintrag, skala=SKALA_PR, breite=560, hoehe=220):
    """Geometrie für ein Liniendiagramm der Werte einer Skala eines Bereichs.

    Gibt Koordinaten zurück; das SVG baut die Vorlage. y läuft vom höchsten Wert
    der Skala oben zum niedrigsten unten; Werte außerhalb liegen am Rand.
    """
    skalen = eintrag.get('skalen', {})
    reihen_der_skala = {
        name: punkte for name, punkte in eintrag['reihen'].items()
        if skalen.get(name, SKALA_PR) == skala
    }
    benutzte_zeiten = {p[0] for punkte in reihen_der_skala.values() for p in punkte}
    zeiten = [(s, label) for s, label in eintrag['zeiten'] if s in benutzte_zeiten]
    info = SKALEN[skala]
    minimum, maximum = info['min'], info['max']

    # Seitlich Platz für die halbe Breite einer Achsenbeschriftung ("Mitte 2025/2026").
    rand_links, rand_rechts, rand_oben, rand_unten = 64, 52, 12, 34
    innen_b = breite - rand_links - rand_rechts
    innen_h = hoehe - rand_oben - rand_unten
    schritt = innen_b / max(1, len(zeiten) - 1)
    x_von = {schluessel: rand_links + (i * schritt if len(zeiten) > 1 else innen_b / 2) for i, (schluessel, _) in enumerate(zeiten)}

    def y(wert):
        wert = max(minimum, min(maximum, wert))
        return rand_oben + innen_h * (1 - (wert - minimum) / (maximum - minimum))

    reihen = []
    for name, punkte in reihen_der_skala.items():
        koordinaten = [
            {'x': round(x_von[s], 1), 'y': round(y(wert), 1), 'wert': wert, 'pr': wert if skala == SKALA_PR else None,
             'abgeleitet': abgeleitet, 'stufe': stufe}
            for s, wert, abgeleitet, stufe in punkte
        ]
        reihen.append({'name': name, 'punkte': koordinaten,
                       'pfad': ' '.join(f"{p['x']},{p['y']}" for p in koordinaten)})
    return {
        'breite': breite, 'hoehe': hoehe,
        'links': rand_links, 'rechts': breite - rand_rechts,
        'oben': rand_oben, 'unten': hoehe - rand_unten,
        'y': y,
        'skala': skala, 'kuerzel': info['kuerzel'], 'skala_name': info['name'],
        'minimum': minimum, 'maximum': maximum, 'ticks': info['ticks'],
        'achse': [{'x': round(x_von[s], 1), 'label': label} for s, label in zeiten],
        'reihen': reihen,
        'abgeleitet': any(p['abgeleitet'] for r in reihen for p in r['punkte']),
    }


def diagramme(eintrag, grenzen):
    """Je Skala eines Bereichs: Geometrie und Stufenbänder."""
    ergebnis = []
    for skala in skalen_im_verlauf(eintrag):
        geometrie = diagramm(eintrag, skala)
        ergebnis.append({'geometrie': geometrie, 'baender': stufen_baender(geometrie, grenzen)})
    return ergebnis


def werte_zeilen(ergebnis):
    """Eingetragene Werte zum Anzeigen: [(Kennwert, 'RW 210 · PR 45')]."""
    zeilen = []
    for kennwert in ergebnis.testform.kennwerte:
        wert = ergebnis.wert_fuer(kennwert.id)
        if not wert:
            continue
        teile = [
            f'{WERTART_KUERZEL[art]} {getattr(wert, art)}'
            for art in ('rohwert', 'prozentrang', 't_wert', 'lesequotient')
            if getattr(wert, art) is not None
        ]
        if teile:
            zeilen.append((kennwert, ' · '.join(teile)))
    return zeilen


def stufen_baender(geometrie, grenzen):
    """Farbige Bänder der Risikostufen fürs Diagramm, von unten nach oben."""
    skala = geometrie.get('skala', SKALA_PR)
    werte = grenzen_fuer(grenzen, skala)
    baender = []
    untergrenze = geometrie.get('minimum', 0)
    for stufe in (STUFE_DEUTLICH, STUFE_AUFFAELLIG, STUFE_BEOBACHTEN):
        grenze = werte.get(stufe)
        # LQ-Grenzen sind ganze Zahlen einschließlich - "bis 89" endet an der 90.
        kante = grenze + 1 if grenze is not None and skala == SKALA_LQ else grenze
        if grenze is None or kante <= untergrenze:
            continue
        oben = round(geometrie['y'](kante), 1)
        unten = round(geometrie['y'](untergrenze), 1)
        baender.append({'stufe': stufe, 'y': oben, 'hoehe': round(unten - oben, 1), 'grenze': grenze,
                        'text': f'bis {SKALEN[skala]["kuerzel"]} {grenze}'})
        untergrenze = kante
    return baender


def klassen_uebersicht(kinder, schuljahr, grenzen):
    """Je Kind: Stand je Lernbereich, schwerste Stufe und offene Tests laut Plan."""
    bereiche = set()
    zeilen = []
    for kind in kinder:
        verlaeufe = {eintrag['bereich']: eintrag for eintrag in verlauf(kind, grenzen)}
        bereiche.update(verlaeufe)
        aktuelle_stufen = {
            eintrag['aktuell'].stufe for eintrag in verlaeufe.values() if eintrag['aktuell']
        }
        jahrgang = effective_jahrgang(kind)
        erledigt = {
            (ergebnis.testform_id, ergebnis.halbjahr)
            for ergebnis in kind.diagnostik_ergebnisse if ergebnis.schuljahr == schuljahr
        }
        offen = [
            zeitpunkt for zeitpunkt in zeitpunkte_fuer(jahrgang)
            if (zeitpunkt.testform_id, zeitpunkt.halbjahr) not in erledigt
        ]
        zeilen.append({
            'kind': kind,
            'jahrgang': jahrgang,
            'bereiche': verlaeufe,
            'stufe': schwerste_stufe(aktuelle_stufen),
            'offen': offen,
        })
    return sorted(bereiche, key=str.lower), zeilen


# ----------------------------------------------------------------------
# Speichern
# ----------------------------------------------------------------------

UNVERAENDERT = object()
WERTART_SCHLUESSEL = ('rohwert', 'prozentrang', 't_wert', 'lesequotient')


def speichere_ergebnis(kind, testform, schuljahr, halbjahr, werte, user_id,
                       datum=UNVERAENDERT, bemerkung=UNVERAENDERT, nur_kennwerte=None,
                       aktuelles_schuljahr=None):
    """Legt ein Ergebnis an oder aktualisiert es. Gibt (Ergebnis, neu?) zurück.

    werte: {(kennwert_id, wertart): Zahl}. Kennwerte der Testform ohne Eintrag
    in `werte` werden geleert - außer `nur_kennwerte` begrenzt die Änderung
    auf bestimmte Kennwerte (der Import fasst nur an, was die Datei enthält).
    Committet nicht.
    """
    ergebnis = DiagnostikErgebnis.query.filter_by(
        schueler_id=kind.id, testform_id=testform.id, schuljahr=schuljahr, halbjahr=halbjahr,
    ).first()
    neu = ergebnis is None
    if neu:
        ergebnis = DiagnostikErgebnis(
            schueler_id=kind.id, testform_id=testform.id, schuljahr=schuljahr, halbjahr=halbjahr,
        )
        db.session.add(ergebnis)
    if datum is not UNVERAENDERT:
        ergebnis.datum = datum
    if bemerkung is not UNVERAENDERT:
        ergebnis.bemerkung = bemerkung or None
    if aktuelles_schuljahr is None:
        config = SystemKonfiguration.query.first()
        aktuelles_schuljahr = config.schuljahr if config else None
    ergebnis.jahrgang = jahrgang_im_schuljahr(kind, schuljahr, aktuelles_schuljahr or schuljahr)
    ergebnis.klasse = klasse_im_schuljahr(kind, ergebnis.jahrgang)
    ergebnis.erfasst_von_user_id = user_id

    for kennwert in testform.kennwerte:
        if nur_kennwerte is not None and kennwert.id not in nur_kennwerte:
            continue
        neue = {art: werte.get((kennwert.id, art)) for art in kennwert.wertarten}
        wert = None if neu else ergebnis.wert_fuer(kennwert.id)
        if not any(v is not None for v in neue.values()):
            if wert:
                ergebnis.werte.remove(wert)
            continue
        if not wert:
            wert = DiagnostikWert(kennwert_id=kennwert.id)
            ergebnis.werte.append(wert)
        for art in WERTART_SCHLUESSEL:
            setattr(wert, art, neue.get(art))
    return ergebnis, neu


# ----------------------------------------------------------------------
# Testplan
# ----------------------------------------------------------------------

def zeitpunkte_fuer(jahrgang, halbjahr=None):
    """Aktive Testplan-Einträge eines Jahrgangs (optional nur ein Halbjahr)."""
    if jahrgang is None:
        return []
    query = (
        DiagnostikZeitpunkt.query
        .join(DiagnostikTestform)
        .join(DiagnostikVerfahren)
        .filter(
            DiagnostikZeitpunkt.jahrgang == jahrgang,
            DiagnostikTestform.is_active.is_(True),
            DiagnostikVerfahren.is_active.is_(True),
        )
    )
    if halbjahr:
        query = query.filter(DiagnostikZeitpunkt.halbjahr == halbjahr)
    return sorted(
        query.all(),
        key=lambda z: (z.testform.verfahren.sort_order, z.testform.sort_order, 0 if z.halbjahr == 'mitte' else 1),
    )


# ----------------------------------------------------------------------
# Vorbelegung
# ----------------------------------------------------------------------

def _kennwert(name, rw=True, pr=True, t=False, lq=False, leit=False, risiko=None):
    return {'name': name, 'rohwert': rw, 'prozentrang': pr, 't_wert': t, 'lesequotient': lq,
            'leitwert': leit, 'risiko': leit if risiko is None else risiko}


# Wie in den HSP-Auswertungsmappen der Schule: jeder Kennwert mit Rohwert
# (bei Strategien die Zahl der Lupenstellen), Prozentrang und T-Wert.
HSP_STRATEGIEN = ['Alphabetische Strategie', 'Orthografische Strategie', 'Morphematische Strategie', 'Wortübergreifende Strategie']
HSP_KENNWERTE = [
    _kennwert('Graphemtreffer', t=True, leit=True),
    _kennwert('Wörter richtig', t=True, leit=True),
] + [_kennwert(name, t=True, risiko=True) for name in HSP_STRATEGIEN]

# Testplan nach den Auswertungsmappen: HSP zur Mitte und am Ende jeder Klasse.
HSP_TESTPLAN = {
    'HSP 1+': [(1, 'mitte'), (1, 'ende')],
    'HSP 2': [(2, 'mitte'), (2, 'ende')],
    'HSP 3': [(3, 'mitte'), (3, 'ende')],
    'HSP 4-5': [(4, 'mitte'), (4, 'ende')],
}

# SLS an der Schule: Ende Klasse 1, Mitte und Ende Klasse 2.
SLS_TESTPLAN = [(1, 'ende'), (2, 'mitte'), (2, 'ende')]
# So stand der Plan in der ersten Vorbelegung - nur ein unveränderter Plan wird ergänzt.
SLS_TESTPLAN_ALT = {(1, 'ende'), (2, 'ende')}

VORBELEGUNG = [
    {
        'name': 'HSP', 'bereich': 'Rechtschreiben',
        'beschreibung': 'Hamburger Schreib-Probe. Strategiewerte nur eintragen, soweit die Testform sie ausweist.',
        'testformen': [
            (name, HSP_KENNWERTE, HSP_TESTPLAN[name]) for name in HSP_TESTPLAN
        ],
    },
    {
        'name': 'SLS 1-4', 'bereich': 'Lesen',
        'beschreibung': 'Salzburger Lesescreening für die Klassenstufen 1–4. Rohwert: richtig beurteilte Sätze.',
        'testformen': [
            ('SLS 1-4', [_kennwert('Leseleistung', pr=False, lq=True, leit=True)], SLS_TESTPLAN),
        ],
    },
    {
        'name': 'ELFE II', 'bereich': 'Lesen',
        'beschreibung': 'Leseverständnistest für Erst- bis Siebtklässler.',
        'testformen': [
            ('ELFE II', [
                _kennwert('Wortverständnis', t=True),
                _kennwert('Satzverständnis', t=True),
                _kennwert('Textverständnis', t=True),
                _kennwert('Gesamt', t=True, leit=True),
            ], [(3, 'ende'), (4, 'ende')]),
        ],
    },
]


def lege_vorbelegung_an():
    """Legt HSP, SLS 1-4 und ELFE II an - nur, wenn der Katalog leer ist.

    Gibt die Anzahl angelegter Verfahren zurück. Committet nicht.
    """
    if DiagnostikVerfahren.query.first() is not None:
        return 0
    for position, daten in enumerate(VORBELEGUNG):
        verfahren = DiagnostikVerfahren(
            name=daten['name'], bereich=daten['bereich'],
            beschreibung=daten['beschreibung'], sort_order=position,
        )
        for form_position, (name, kennwerte, zeitpunkte) in enumerate(daten['testformen']):
            testform = DiagnostikTestform(name=name, sort_order=form_position)
            testform.kennwerte = [
                DiagnostikKennwert(sort_order=i, **kennwert) for i, kennwert in enumerate(kennwerte)
            ]
            testform.zeitpunkte = [
                DiagnostikZeitpunkt(jahrgang=jahrgang, halbjahr=halbjahr) for jahrgang, halbjahr in zeitpunkte
            ]
            verfahren.testformen.append(testform)
        db.session.add(verfahren)
    return len(VORBELEGUNG)


def schaerfe_vorbelegung_nach():
    """Gleicht die HSP-Vorbelegung an die Auswertungsmappen der Schule an.

    Strategien bekommen Rohwert und T-Wert, der Testplan die Mitte jeder Klasse.
    Angefasst werden nur Testformen der Vorbelegung, zu denen es noch keine
    Ergebnisse gibt - was die Verwaltung schon genutzt hat, bleibt, wie es ist.
    Idempotent; gibt die Zahl geänderter Testformen zurück. Committet nicht.
    """
    geaendert = 0
    verfahren = DiagnostikVerfahren.query.filter_by(name='HSP').first()
    if not verfahren:
        return 0
    for testform in verfahren.testformen:
        if testform.name not in HSP_TESTPLAN:
            continue
        if DiagnostikErgebnis.query.filter_by(testform_id=testform.id).first():
            continue
        vorher = geaendert
        for kennwert in testform.kennwerte:
            if kennwert.name in HSP_STRATEGIEN and not (kennwert.rohwert and kennwert.t_wert):
                kennwert.rohwert = True
                kennwert.t_wert = True
                geaendert = vorher + 1
        vorhanden = {(z.jahrgang, z.halbjahr) for z in testform.zeitpunkte}
        for jahrgang, halbjahr in HSP_TESTPLAN[testform.name]:
            if (jahrgang, halbjahr) not in vorhanden:
                testform.zeitpunkte.append(DiagnostikZeitpunkt(jahrgang=jahrgang, halbjahr=halbjahr))
                geaendert = vorher + 1
    return geaendert


def ergaenze_sls_testplan():
    """Ergänzt im SLS-Testplan die Mitte von Klasse 2.

    Nur wenn der Plan noch genau der ersten Vorbelegung entspricht (Ende 1 und
    Ende 2) - hat die Verwaltung ihn geändert, bleibt er, wie er ist. Idempotent;
    gibt die Zahl ergänzter Zeitpunkte zurück. Committet nicht.
    """
    testform = (
        DiagnostikTestform.query.join(DiagnostikVerfahren)
        .filter(DiagnostikVerfahren.name == 'SLS 1-4', DiagnostikTestform.name == 'SLS 1-4')
        .first()
    )
    if not testform:
        return 0
    vorhanden = {(z.jahrgang, z.halbjahr) for z in testform.zeitpunkte}
    if vorhanden != SLS_TESTPLAN_ALT:
        return 0
    neu = [(jahrgang, halbjahr) for jahrgang, halbjahr in SLS_TESTPLAN if (jahrgang, halbjahr) not in vorhanden]
    for jahrgang, halbjahr in neu:
        testform.zeitpunkte.append(DiagnostikZeitpunkt(jahrgang=jahrgang, halbjahr=halbjahr))
    return len(neu)


def sls_ohne_prozentrang():
    """Das SLS weist nur Rohwert und Lesequotient aus - kein Prozentrang.

    Nimmt den PR aus den Kennwerten der SLS-Vorbelegung, die einen LQ haben.
    Eingetragene Prozentränge bleiben in der Datenbank, zählen aber nicht mehr
    und verschwinden beim nächsten Speichern des Ergebnisses. Idempotent; gibt
    die Zahl geänderter Kennwerte zurück. Committet nicht.
    """
    verfahren = DiagnostikVerfahren.query.filter_by(name='SLS 1-4').first()
    if not verfahren:
        return 0
    geaendert = 0
    for testform in verfahren.testformen:
        for kennwert in testform.kennwerte:
            if kennwert.lesequotient and kennwert.prozentrang:
                kennwert.prozentrang = False
                geaendert += 1
    return geaendert


def ergaenze_klassen_der_ergebnisse():
    """Füllt die Klasse zum Testzeitpunkt bei Ergebnissen, die sie noch nicht haben.

    Idempotent; überschreibt nichts. Committet nicht.
    """
    anzahl = 0
    for ergebnis in DiagnostikErgebnis.query.filter(DiagnostikErgebnis.klasse.is_(None)).all():
        klasse = klasse_im_schuljahr(ergebnis.schueler, ergebnis.jahrgang)
        if klasse:
            ergebnis.klasse = klasse
            anzahl += 1
    return anzahl


# ----------------------------------------------------------------------
# Förderangaben (Nachteilsausgleich, Förderkurs, externe Förderung)
# ----------------------------------------------------------------------

FOERDER_KUERZEL = [
    ('nachteilsausgleich', 'NTA', 'Nachteilsausgleich'),
    ('foerderkurs', 'FK', 'Förderkurs'),
    ('externe_foerderung', 'EF', 'externe Förderung'),
]


def foerderangaben_fuer(schueler_ids, schuljahr):
    """schueler_id -> Foerderangaben des Schuljahres (nur vorhandene)."""
    from models import Foerderangaben
    if not schueler_ids or not schuljahr:
        return {}
    return {
        eintrag.schueler_id: eintrag
        for eintrag in Foerderangaben.query.filter(
            Foerderangaben.schueler_id.in_(list(schueler_ids)), Foerderangaben.schuljahr == schuljahr,
        ).all()
    }


def speichere_foerderangaben(schueler_id, schuljahr, daten, user_id):
    """Legt an, aktualisiert oder entfernt (wenn alles leer) die Angaben. Committet nicht.

    daten: dict mit nachteilsausgleich, foerderkurs, externe_foerderung (bool),
    foerderschwerpunkt, anmerkungen (Text).
    """
    from models import Foerderangaben
    eintrag = Foerderangaben.query.filter_by(schueler_id=schueler_id, schuljahr=schuljahr).first()
    werte = {
        'nachteilsausgleich': bool(daten.get('nachteilsausgleich')),
        'foerderkurs': bool(daten.get('foerderkurs')),
        'externe_foerderung': bool(daten.get('externe_foerderung')),
        'foerderschwerpunkt': (daten.get('foerderschwerpunkt') or '').strip() or None,
        'anmerkungen': (daten.get('anmerkungen') or '').strip() or None,
    }
    if not any(werte.values()):
        if eintrag:
            db.session.delete(eintrag)
        return None
    if not eintrag:
        eintrag = Foerderangaben(schueler_id=schueler_id, schuljahr=schuljahr)
        db.session.add(eintrag)
    for feld, wert in werte.items():
        setattr(eintrag, feld, wert)
    eintrag.updated_by_user_id = user_id
    return eintrag
