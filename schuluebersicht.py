"""Schulübersicht der Diagnostik für die Schulleitung.

Zwei Fragen, die sich eine Schulleitung stellt:

1. Erfassungsstand: Welche Klasse hat im Schuljahr welche Tests eingetragen?
   Gemessen am Testplan - erwartet werden die Kinder des passenden Jahrgangs.
   Was darüber hinaus eingetragen ist, erscheint ebenfalls.
2. Klassendurchschnitt: Wie steht eine Klasse in einem Kennwert, und wie hat
   sie sich entwickelt? Gruppiert wird nach der heutigen Klasse der Kinder -
   die Linie der 3a zeigt also, wie sich genau diese Kinder seit Klasse 1
   entwickelt haben, auch wenn sie damals 1a hießen.

Gemittelt wird der Vergleichswert der Diagnostik: der Prozentrang (bei
fehlendem PR aus dem T-Wert abgeleitet), beim SLS der Lesequotient.
"""

from collections import defaultdict

from diagnostik import (
    SKALA_PR,
    SKALEN,
    grenzen_fuer,
    stufe_fuer,
    vergleichswert,
    zeitlabel,
    zeitpunkte_fuer,
    zeitschluessel,
)
from jahrgang import grade_from_name
from models import DiagnostikErgebnis, DiagnostikTestform, DiagnostikVerfahren, Schueler
from transition_plan import effective_jahrgang


def _klassen_sortierung(name):
    stufe = grade_from_name(name)
    return (stufe if stufe is not None else 99, name.lower())


def _aktive_kinder_je_klasse():
    klassen = defaultdict(list)
    for kind in Schueler.query.filter(Schueler.is_active.is_(True)).all():
        name = (kind.klasse or '').strip()
        if name:
            klassen[name].append(kind)
    return klassen


def _test_sortierung(testform, halbjahr):
    return (testform.verfahren.sort_order, testform.verfahren.name.lower(), testform.sort_order,
            testform.name.lower(), 0 if halbjahr == 'mitte' else 1)


# ----------------------------------------------------------------------
# Erfassungsstand
# ----------------------------------------------------------------------

def erfassungsstand(schuljahr, aktuelles_schuljahr):
    """Je Klasse die Tests des Schuljahres mit Anzahl eingetragen / erwartet.

    Für das laufende Schuljahr kennt die Anwendung Klassen und Jahrgänge und
    kann sagen, wie viele Ergebnisse der Testplan erwartet. Für frühere
    Schuljahre zählt sie nur, was eingetragen ist - nach der Klasse zum
    Testzeitpunkt.
    """
    laufend = schuljahr == aktuelles_schuljahr
    zeilen = {}

    def zeile(name):
        return zeilen.setdefault(name, {'klasse': name, 'kinder': None, 'tests': {}})

    if laufend:
        for name, kinder in _aktive_kinder_je_klasse().items():
            eintrag = zeile(name)
            eintrag['kinder'] = len(kinder)
            for kind in kinder:
                for zeitpunkt in zeitpunkte_fuer(effective_jahrgang(kind)):
                    test = eintrag['tests'].setdefault((zeitpunkt.testform_id, zeitpunkt.halbjahr), {
                        'testform': zeitpunkt.testform, 'halbjahr': zeitpunkt.halbjahr,
                        'erwartet': 0, 'anzahl': 0, 'datum_bis': None,
                    })
                    test['erwartet'] += 1

    ergebnisse = DiagnostikErgebnis.query.filter(DiagnostikErgebnis.schuljahr == schuljahr).all()
    for ergebnis in ergebnisse:
        if laufend and ergebnis.schueler and ergebnis.schueler.is_active:
            name = (ergebnis.schueler.klasse or '').strip()
        else:
            name = (ergebnis.klasse or (ergebnis.schueler.klasse if ergebnis.schueler else '') or '').strip()
        if not name:
            continue
        test = zeile(name)['tests'].setdefault((ergebnis.testform_id, ergebnis.halbjahr), {
            'testform': ergebnis.testform, 'halbjahr': ergebnis.halbjahr,
            'erwartet': 0 if laufend else None, 'anzahl': 0, 'datum_bis': None,
        })
        test['anzahl'] += 1
        if ergebnis.datum and (test['datum_bis'] is None or ergebnis.datum > test['datum_bis']):
            test['datum_bis'] = ergebnis.datum

    ergebnis_liste = []
    for eintrag in sorted(zeilen.values(), key=lambda z: _klassen_sortierung(z['klasse'])):
        tests = sorted(eintrag['tests'].values(), key=lambda t: _test_sortierung(t['testform'], t['halbjahr']))
        for test in tests:
            test['status'] = _status(test)
        eintrag['tests'] = tests
        ergebnis_liste.append(eintrag)
    return ergebnis_liste


def _status(test):
    erwartet, anzahl = test['erwartet'], test['anzahl']
    if erwartet is None:
        return 'erfasst'          # früheres Schuljahr: nur Anzahl bekannt
    if erwartet == 0:
        return 'zusaetzlich'      # nicht im Testplan, trotzdem eingetragen
    if anzahl == 0:
        return 'offen'
    if anzahl >= erwartet:
        return 'vollstaendig'
    return 'teilweise'


STATUS = {
    'vollstaendig': ('vollständig', 'success'),
    'teilweise': ('teilweise', 'warning'),
    'offen': ('noch nicht eingetragen', 'secondary'),
    'zusaetzlich': ('nicht im Testplan', 'info'),
    'erfasst': ('eingetragen', 'primary'),
}


# ----------------------------------------------------------------------
# Klassendurchschnitt
# ----------------------------------------------------------------------

def kennwert_auswahl(verfahren):
    """Kennwerte eines Verfahrens mit Normwert, Leitwerte zuerst: [(Name, Leitwert?)]."""
    namen = {}
    for testform in sorted(verfahren.testformen, key=lambda t: t.sort_order):
        for kennwert in sorted(testform.kennwerte, key=lambda k: k.sort_order):
            if not (kennwert.prozentrang or kennwert.t_wert or kennwert.lesequotient):
                continue
            namen[kennwert.name] = namen.get(kennwert.name, False) or kennwert.leitwert
    return sorted(namen.items(), key=lambda paar: not paar[1])


def verfahren_mit_ergebnissen():
    ids = {
        row[0] for row in DiagnostikErgebnis.query
        .join(DiagnostikTestform)
        .with_entities(DiagnostikTestform.verfahren_id)
        .distinct()
        .all()
    }
    return (
        DiagnostikVerfahren.query
        .filter(DiagnostikVerfahren.id.in_(ids) if ids else DiagnostikVerfahren.id.is_(None))
        .order_by(DiagnostikVerfahren.sort_order, DiagnostikVerfahren.name)
        .all()
    )


def klassendurchschnitt(verfahren, kennwert_name, grenzen, jahrgang=None):
    """Mittelwert des Kennwerts je heutiger Klasse und Testzeitpunkt.

    jahrgang: nur Klassen, in denen heute Kinder dieses Jahrgangs sind.
    Rückgabe: dict mit 'skala', 'zeiten' [(Zeitschlüssel, Label)],
    'klassen' [{'klasse', 'werte': {Zeitschlüssel: {'mittel', 'anzahl', 'risiko'}}}]
    und 'verlauf' - im Format von diagnostik.verlauf, damit das Diagramm der
    Schülerakte es zeichnen kann.
    """
    skala = None
    werte = defaultdict(lambda: defaultdict(list))   # Klasse -> Zeitschlüssel -> [(Wert, Stufe)]
    zeiten = {}
    for name, kinder in _aktive_kinder_je_klasse().items():
        if jahrgang is not None and jahrgang not in {effective_jahrgang(kind) for kind in kinder}:
            continue
        for kind in kinder:
            for ergebnis in kind.diagnostik_ergebnisse:
                if ergebnis.testform.verfahren_id != verfahren.id:
                    continue
                kennwert = next((k for k in ergebnis.testform.kennwerte if k.name == kennwert_name), None)
                if kennwert is None:
                    continue
                kennwert_skala, wert, _ = vergleichswert(kennwert, ergebnis.wert_fuer(kennwert.id))
                if wert is None:
                    continue
                skala = skala or kennwert_skala
                if kennwert_skala != skala:
                    continue
                schluessel = zeitschluessel(ergebnis.schuljahr, ergebnis.halbjahr)
                zeiten[schluessel] = zeitlabel(ergebnis.schuljahr, ergebnis.halbjahr)
                stufe = stufe_fuer(wert, grenzen_fuer(grenzen, skala)) if kennwert.risiko else None
                werte[name][schluessel].append((wert, stufe))

    skala = skala or SKALA_PR
    klassen = []
    for name in sorted(werte, key=_klassen_sortierung):
        zeitreihe = {}
        for schluessel, eintraege in werte[name].items():
            zahlen = [wert for wert, _ in eintraege]
            zeitreihe[schluessel] = {
                'mittel': round(sum(zahlen) / len(zahlen), 1),
                'anzahl': len(zahlen),
                'risiko': sum(1 for _, stufe in eintraege if stufe),
            }
        klassen.append({'klasse': name, 'werte': zeitreihe})

    sortierte_zeiten = sorted(zeiten.items())
    verlauf = {
        'bereich': verfahren.bereich,
        'zeiten': sortierte_zeiten,
        'reihen': {
            k['klasse']: [(s, k['werte'][s]['mittel'], False, None) for s, _ in sortierte_zeiten if s in k['werte']]
            for k in klassen
        },
        'skalen': {k['klasse']: skala for k in klassen},
        'arten': {},
    }
    return {
        'skala': skala,
        'kuerzel': SKALEN[skala]['kuerzel'],
        'skala_name': SKALEN[skala]['name'],
        'zeiten': sortierte_zeiten,
        'klassen': klassen,
        'verlauf': verlauf,
    }


def jahrgaenge_mit_klassen():
    """Jahrgänge, in denen heute Kinder sind."""
    jahrgaenge = set()
    for kinder in _aktive_kinder_je_klasse().values():
        jahrgaenge.update(j for j in (effective_jahrgang(kind) for kind in kinder) if j)
    return sorted(jahrgaenge)
