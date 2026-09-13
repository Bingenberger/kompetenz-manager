"""Standardisierte Diagnostik: Auswertung, Risikostufen und Verlauf.

Verglichen wird über den Prozentrang. Er ist bei allen Verfahren vorhanden oder
aus dem Normwert ableitbar und macht Ergebnisse verschiedener Tests
vergleichbar - etwa wenn der Lesetest in Klasse 3 vom SLS zu ELFE II wechselt.

Fehlt der Prozentrang, wird er aus T-Wert (Mittel 50, Streuung 10) oder
Lesequotient (Mittel 100, Streuung 15) über die Normalverteilung abgeleitet.
Das ist die Definition dieser Skalen, keine Normtabelle - der Wert wird aber
als abgeleitet gekennzeichnet, weil die Tabelle eines Tests davon um ein, zwei
Punkte abweichen kann.
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

# Ab dieser Veränderung des Prozentrangs gilt ein Verlauf als Bewegung.
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


def risikogrenzen(config=None):
    """Stufe -> Prozentrang-Grenze (einschließlich); ausgeschaltete fehlen."""
    config = config if config is not None else SystemKonfiguration.query.first()
    if config is None:
        return dict(STANDARD_GRENZEN)
    grenzen = {
        STUFE_BEOBACHTEN: config.diagnostik_pr_beobachten,
        STUFE_AUFFAELLIG: config.diagnostik_pr_auffaellig,
        STUFE_DEUTLICH: config.diagnostik_pr_deutlich,
    }
    return {stufe: grenze for stufe, grenze in grenzen.items() if grenze is not None}


def stufe_fuer(prozentrang, grenzen):
    """Die schwerste Stufe, deren Grenze der Prozentrang erreicht."""
    if prozentrang is None:
        return None
    for stufe in (STUFE_DEUTLICH, STUFE_AUFFAELLIG, STUFE_BEOBACHTEN):
        grenze = grenzen.get(stufe)
        if grenze is not None and prozentrang <= grenze:
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
    prozentrang: int
    abgeleitet: bool
    stufe: str


@dataclass
class Auswertung:
    ergebnis: DiagnostikErgebnis
    leitwerte: list = field(default_factory=list)

    @property
    def stufe(self):
        return schwerste_stufe({leitwert.stufe for leitwert in self.leitwerte})

    @property
    def niedrigster_prozentrang(self):
        werte = [leitwert.prozentrang for leitwert in self.leitwerte]
        return min(werte) if werte else None


def auswerten(ergebnis, grenzen):
    """Prozentränge und Stufen der Leitwerte eines Ergebnisses."""
    auswertung = Auswertung(ergebnis)
    for kennwert in ergebnis.testform.kennwerte:
        if not kennwert.leitwert:
            continue
        prozentrang, abgeleitet = prozentrang_aus(ergebnis.wert_fuer(kennwert.id))
        if prozentrang is None:
            continue
        auswertung.leitwerte.append(
            Leitwert(kennwert, prozentrang, abgeleitet, stufe_fuer(prozentrang, grenzen))
        )
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
      'reihen' (Kennwertname -> [(zeitschlüssel, PR, abgeleitet, stufe)]),
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
        eintrag = bereiche.setdefault(bereich, {'bereich': bereich, 'auswertungen': [], 'reihen': {}, 'zeiten': {}})
        auswertung = auswerten(ergebnis, grenzen)
        eintrag['auswertungen'].append(auswertung)
        schluessel = zeitschluessel(ergebnis.schuljahr, ergebnis.halbjahr)
        eintrag['zeiten'][schluessel] = zeitlabel(ergebnis.schuljahr, ergebnis.halbjahr)
        for leitwert in auswertung.leitwerte:
            reihe = eintrag['reihen'].setdefault(
                f'{leitwert.kennwert.name} ({ergebnis.testform.verfahren.name})', []
            )
            reihe.append((schluessel, leitwert.prozentrang, leitwert.abgeleitet, leitwert.stufe))

    ergebnis_liste = []
    for eintrag in bereiche.values():
        eintrag['zeiten'] = sorted(eintrag['zeiten'].items())
        mit_werten = [a for a in eintrag['auswertungen'] if a.leitwerte]
        eintrag['aktuell'] = mit_werten[-1] if mit_werten else None
        vorher = mit_werten[-2] if len(mit_werten) > 1 else None
        eintrag['trend'] = trend(
            vorher.niedrigster_prozentrang if vorher else None,
            eintrag['aktuell'].niedrigster_prozentrang if eintrag['aktuell'] else None,
        )
        ergebnis_liste.append(eintrag)
    return sorted(ergebnis_liste, key=lambda e: e['bereich'].lower())


def diagramm(eintrag, breite=560, hoehe=220):
    """Geometrie für ein Liniendiagramm der Prozentränge eines Bereichs.

    Gibt Koordinaten zurück; das SVG baut die Vorlage. y läuft von PR 100 oben
    bis 0 unten.
    """
    # Seitlich Platz für die halbe Breite einer Achsenbeschriftung ("Mitte 2025/2026").
    rand_links, rand_rechts, rand_oben, rand_unten = 64, 52, 12, 34
    zeiten = eintrag['zeiten']
    innen_b = breite - rand_links - rand_rechts
    innen_h = hoehe - rand_oben - rand_unten
    schritt = innen_b / max(1, len(zeiten) - 1)
    x_von = {schluessel: rand_links + (i * schritt if len(zeiten) > 1 else innen_b / 2) for i, (schluessel, _) in enumerate(zeiten)}

    def y(prozentrang):
        return rand_oben + innen_h * (1 - prozentrang / 100)

    reihen = []
    for name, punkte in eintrag['reihen'].items():
        koordinaten = [
            {'x': round(x_von[s], 1), 'y': round(y(pr), 1), 'pr': pr, 'abgeleitet': abgeleitet, 'stufe': stufe}
            for s, pr, abgeleitet, stufe in punkte
        ]
        reihen.append({'name': name, 'punkte': koordinaten,
                       'pfad': ' '.join(f"{p['x']},{p['y']}" for p in koordinaten)})
    return {
        'breite': breite, 'hoehe': hoehe,
        'links': rand_links, 'rechts': breite - rand_rechts,
        'oben': rand_oben, 'unten': hoehe - rand_unten,
        'y': y,
        'achse': [{'x': round(x_von[s], 1), 'label': label} for s, label in zeiten],
        'reihen': reihen,
    }


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
    baender = []
    untergrenze = 0
    for stufe in (STUFE_DEUTLICH, STUFE_AUFFAELLIG, STUFE_BEOBACHTEN):
        grenze = grenzen.get(stufe)
        if grenze is None or grenze <= untergrenze:
            continue
        oben = round(geometrie['y'](grenze), 1)
        unten = round(geometrie['y'](untergrenze), 1)
        baender.append({'stufe': stufe, 'y': oben, 'hoehe': round(unten - oben, 1), 'grenze': grenze})
        untergrenze = grenze
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

def _kennwert(name, rw=True, pr=True, t=False, lq=False, leit=False):
    return {'name': name, 'rohwert': rw, 'prozentrang': pr, 't_wert': t, 'lesequotient': lq, 'leitwert': leit}


# Wie in den HSP-Auswertungsmappen der Schule: jeder Kennwert mit Rohwert
# (bei Strategien die Zahl der Lupenstellen), Prozentrang und T-Wert.
HSP_STRATEGIEN = ['Alphabetische Strategie', 'Orthografische Strategie', 'Morphematische Strategie', 'Wortübergreifende Strategie']
HSP_KENNWERTE = [
    _kennwert('Graphemtreffer', t=True, leit=True),
    _kennwert('Wörter richtig', t=True, leit=True),
] + [_kennwert(name, t=True) for name in HSP_STRATEGIEN]

# Testplan nach den Auswertungsmappen: HSP zur Mitte und am Ende jeder Klasse.
HSP_TESTPLAN = {
    'HSP 1+': [(1, 'mitte'), (1, 'ende')],
    'HSP 2': [(2, 'mitte'), (2, 'ende')],
    'HSP 3': [(3, 'mitte'), (3, 'ende')],
    'HSP 4-5': [(4, 'mitte'), (4, 'ende')],
}

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
            ('SLS 1-4', [_kennwert('Leseleistung', lq=True, leit=True)], [(1, 'ende'), (2, 'ende')]),
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
