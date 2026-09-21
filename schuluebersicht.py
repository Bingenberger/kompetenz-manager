"""Schulübersicht der Diagnostik für die Schulleitung.

Zwei Fragen, die sich eine Schulleitung stellt:

1. Erfassungsstand: Welche Tests sind in welcher Klasse bisher gelaufen und
   eingetragen - über alle Schuljahre, gemessen am Testplan. Erwartet werden
   die Kinder, deren damaliger Jahrgang den Test vorsah. Was darüber hinaus
   eingetragen ist, erscheint ebenfalls.
2. Klassendurchschnitt: Wie steht eine Klasse in einem Kennwert, und wie hat
   sie sich entwickelt? Gruppiert wird nach der heutigen Klasse der Kinder -
   die Linie der 3a zeigt also, wie sich genau diese Kinder seit Klasse 1
   entwickelt haben, auch wenn sie damals 1a hießen.

Gemittelt wird der Vergleichswert der Diagnostik: der Prozentrang (bei
fehlendem PR aus dem T-Wert abgeleitet), beim SLS der Lesequotient.
"""

from collections import defaultdict
from datetime import date

from diagnostik import (
    SKALA_PR,
    SKALEN,
    grenzen_fuer,
    jahrgang_im_schuljahr,
    klasse_im_schuljahr,
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
# Erfassungsstand über alle Schuljahre
# ----------------------------------------------------------------------

# In diesen Zeiträumen wird ein Test zur Mitte bzw. am Ende geschrieben (Monat, Tag).
# Davor gilt er als geplant, darin als fällig, danach fehlt er, wenn nichts eingetragen ist.
TESTFENSTER = {'mitte': ((1, 1), (3, 31)), 'ende': ((5, 1), (7, 31))}

STATUS = {
    'vollstaendig': ('vollständig', 'success'),
    'teilweise': ('teilweise', 'warning'),
    'offen': ('jetzt fällig', 'info'),
    'fehlt': ('nicht eingetragen', 'danger'),
    'geplant': ('geplant', 'secondary'),
    'zusaetzlich': ('nicht im Testplan', 'primary'),
}


def zeit_status(schuljahr, halbjahr, heute):
    """'zukunft', 'laufend' oder 'vorbei' für einen Testzeitpunkt."""
    try:
        jahr = int(schuljahr[:4]) + 1
    except (TypeError, ValueError):
        return 'vorbei'
    (von_monat, von_tag), (bis_monat, bis_tag) = TESTFENSTER.get(halbjahr, TESTFENSTER['ende'])
    if heute < date(jahr, von_monat, von_tag):
        return 'zukunft'
    if heute <= date(jahr, bis_monat, bis_tag):
        return 'laufend'
    return 'vorbei'


def _status(test, zeit):
    erwartet, anzahl = test['erwartet'], test['anzahl']
    if erwartet == 0:
        return 'zusaetzlich'
    if anzahl >= erwartet:
        return 'vollstaendig'
    if anzahl > 0:
        return 'teilweise'
    return {'zukunft': 'geplant', 'laufend': 'offen'}.get(zeit, 'fehlt')


def erfassungsmatrix(aktuelles_schuljahr, heute=None):
    """Je heutiger Klasse und Testzeitpunkt: welche Tests laufen sollten und eingetragen sind.

    Gezählt werden die Kinder, die heute in der Klasse sind - auch für die
    Jahre davor, als die Klasse noch anders hieß (aus der 3a war die 2a).
    Erwartet sind die Kinder, deren damaliger Jahrgang den Test im Testplan
    hat. Kinder, die die Schule verlassen haben, sind nicht enthalten.

    Rückgabe: (Zeiten, Zeilen) - Zeiten als [(Zeitschlüssel, Label)],
    Zeilen als [{'klasse', 'kinder', 'zellen': {Zeitschlüssel: [Test]},
    'damals': {Zeitschlüssel: Klassenname}}].
    """
    heute = heute or date.today()
    try:
        beginn = int((aktuelles_schuljahr or '')[:4])
    except ValueError:
        return [], []
    zeiten = {}
    zeilen = []
    for name, kinder in sorted(_aktive_kinder_je_klasse().items(), key=lambda p: _klassen_sortierung(p[0])):
        zellen = defaultdict(dict)
        damals = defaultdict(set)

        def test_fuer(schluessel, testform, schuljahr, halbjahr):
            return zellen[schluessel].setdefault(testform.id, {
                'testform': testform, 'schuljahr': schuljahr, 'halbjahr': halbjahr,
                'erwartet': 0, 'anzahl': 0, 'datum_bis': None,
            })

        for kind in kinder:
            for zurueck in range(0, 4):
                schuljahr = f'{beginn - zurueck}/{beginn - zurueck + 1}'
                jahrgang = jahrgang_im_schuljahr(kind, schuljahr, aktuelles_schuljahr)
                if jahrgang is None:
                    continue
                for zeitpunkt in zeitpunkte_fuer(jahrgang):
                    schluessel = zeitschluessel(schuljahr, zeitpunkt.halbjahr)
                    zeiten[schluessel] = zeitlabel(schuljahr, zeitpunkt.halbjahr)
                    test_fuer(schluessel, zeitpunkt.testform, schuljahr, zeitpunkt.halbjahr)['erwartet'] += 1
                    klasse_damals = klasse_im_schuljahr(kind, jahrgang)
                    if klasse_damals:
                        damals[schluessel].add(klasse_damals)
            for ergebnis in kind.diagnostik_ergebnisse:
                schluessel = zeitschluessel(ergebnis.schuljahr, ergebnis.halbjahr)
                zeiten[schluessel] = zeitlabel(ergebnis.schuljahr, ergebnis.halbjahr)
                test = test_fuer(schluessel, ergebnis.testform, ergebnis.schuljahr, ergebnis.halbjahr)
                test['anzahl'] += 1
                if ergebnis.datum and (test['datum_bis'] is None or ergebnis.datum > test['datum_bis']):
                    test['datum_bis'] = ergebnis.datum
                if ergebnis.klasse:
                    damals[schluessel].add(ergebnis.klasse)

        fertige_zellen = {}
        for schluessel, tests in zellen.items():
            liste = sorted(tests.values(), key=lambda t: _test_sortierung(t['testform'], t['halbjahr']))
            for test in liste:
                test['status'] = _status(test, zeit_status(test['schuljahr'], test['halbjahr'], heute))
            fertige_zellen[schluessel] = liste
        zeilen.append({
            'klasse': name,
            'kinder': len(kinder),
            'zellen': fertige_zellen,
            'damals': {s: ', '.join(sorted(namen)) for s, namen in damals.items() if namen != {name}},
        })
    return sorted(zeiten.items()), zeilen


def zaehle_status(zeilen):
    """Wie viele Tests je Status - für die Legende."""
    zaehler = {status: 0 for status in STATUS}
    for zeile in zeilen:
        for tests in zeile['zellen'].values():
            for test in tests:
                zaehler[test['status']] += 1
    return zaehler


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
