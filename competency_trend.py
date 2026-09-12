"""Hat sich etwas bewegt?

Der Bericht zeigte pro Kompetenz einen Mittelwert über das Schuljahr. Die Frage,
die in jedem Förderplangespräch zuerst kommt, beantwortet das nicht: Ist das
Kind vorangekommen? Ein Mittelwert von 2,5 kann aus zwei Vieren und zwei Einsen
entstehen oder aus vier stabilen Mittelwerten - pädagogisch ist das nicht
dasselbe.

Verglichen wird deshalb die frühere mit der späteren Hälfte der Beobachtungen.
Geteilt wird nach Anzahl, nicht nach Datum: Beobachtungen entstehen
unregelmäßig, und eine Zeitachse mit einem Eintrag im ersten und acht im
zweiten Halbjahr ergäbe einen Vergleich, der nichts trägt.
"""

# Die Skala reicht von 1 bis 4. Eine halbe Stufe ist die kleinste Veränderung,
# die noch als Bewegung durchgeht - darunter ist es Rauschen einzelner Tage.
SCHWELLE = 0.5

MINDESTENS = 2

RICHTUNG_LABELS = {
    'verbessert': 'verbessert',
    'stabil': 'unverändert',
    'verschlechtert': 'zurückgegangen',
}


def _mittel(werte):
    return round(sum(werte) / len(werte), 1) if werte else None


def compute_trend(entries):
    """Vergleicht die frühere mit der späteren Hälfte der Beobachtungen.

    `entries` sind Beobachtungen eines Kindes zu einer Kompetenz, in beliebiger
    Reihenfolge; sortiert wird hier. Gibt None zurück, wenn zu wenig vorliegt,
    um überhaupt etwas zu sagen.
    """
    bewertet = [eintrag for eintrag in entries if eintrag.wert is not None]
    if len(bewertet) < MINDESTENS:
        return None

    bewertet.sort(key=lambda eintrag: (eintrag.datum is None, eintrag.datum))
    haelfte = len(bewertet) // 2

    # Bei ungerader Anzahl bleibt der mittlere Eintrag außen vor: er gehört zu
    # keiner der beiden Hälften und würde eine Seite bevorzugen.
    frueher = [eintrag.wert for eintrag in bewertet[:haelfte]]
    spaeter = [eintrag.wert for eintrag in bewertet[len(bewertet) - haelfte:]]

    mittel_frueher = _mittel(frueher)
    mittel_spaeter = _mittel(spaeter)
    differenz = round(mittel_spaeter - mittel_frueher, 1)

    if differenz >= SCHWELLE:
        richtung = 'verbessert'
    elif differenz <= -SCHWELLE:
        richtung = 'verschlechtert'
    else:
        richtung = 'stabil'

    return {
        'richtung': richtung,
        'label': RICHTUNG_LABELS[richtung],
        'frueher': mittel_frueher,
        'spaeter': mittel_spaeter,
        'differenz': differenz,
        'anzahl_frueher': len(frueher),
        'anzahl_spaeter': len(spaeter),
        'von': bewertet[0].datum,
        'bis': bewertet[-1].datum,
    }


def summarize(trends):
    """Zählt die Richtungen über alle Kompetenzen eines Bogens."""
    gezaehlt = {'verbessert': 0, 'stabil': 0, 'verschlechtert': 0, 'ohne': 0}
    for trend in trends:
        gezaehlt[trend['richtung'] if trend else 'ohne'] += 1
    gezaehlt['bewertet'] = (
        gezaehlt['verbessert'] + gezaehlt['stabil'] + gezaehlt['verschlechtert']
    )
    return gezaehlt
