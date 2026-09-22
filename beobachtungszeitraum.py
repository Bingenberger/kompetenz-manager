"""Ab wann Beobachtungen eines Bogens zählen.

Normalerweise zählt das laufende Schuljahr: Mittelwerte, Lernstand und
Zeugnismaterial beginnen nach dem Schuljahreswechsel von vorn.

Ein schuljahresübergreifender Bogen (Bogen.schuljahresuebergreifend) zählt
dagegen seit dem Schuljahr, in dem das Kind im ersten Jahrgang des Bogens war.
Beispiel: Bogen „Übergang in Klasse 5“ für Jahrgang 3 und 4 - für ein Kind in
Klasse 4 zählen die Einträge seit Beginn des vorigen Schuljahres, für ein Kind
in Klasse 3 die des laufenden. Ohne Jahrgangszuordnung zählt alles.
"""

from datetime import date, datetime, time

from school_year import active_school_year_start
from transition_plan import effective_jahrgang


def _jahre_zurueck(beginn, jahre):
    try:
        return beginn.replace(year=beginn.year - jahre)
    except ValueError:                      # 29. Februar
        return date(beginn.year - jahre, beginn.month, 28)


def beginn_fuer(bogen, schueler=None):
    """Datum, ab dem Beobachtungen dieses Bogens für dieses Kind zählen (None: alle)."""
    start = active_school_year_start()
    if not start or not getattr(bogen, 'schuljahresuebergreifend', False):
        return start
    jahrgaenge = bogen.jahrgaenge
    if not jahrgaenge:
        return None
    jahrgang = effective_jahrgang(schueler) if schueler is not None else None
    if jahrgang is None:
        # Ohne Kind (etwa für eine ganze Klasse): den ganzen Zeitraum des Bogens.
        zurueck = max(jahrgaenge) - min(jahrgaenge)
    else:
        zurueck = max(0, jahrgang - min(jahrgaenge))
    return _jahre_zurueck(start, zurueck)


def zeitgrenze_fuer(bogen, schueler=None):
    """Wie beginn_fuer, als datetime für den Vergleich mit Beobachtung.datum."""
    beginn = beginn_fuer(bogen, schueler)
    return datetime.combine(beginn, time.min) if beginn else None


def zaehlt(beobachtung, bogen, schueler=None, grenze=None):
    """True, wenn die Beobachtung im Zeitraum des Bogens liegt."""
    grenze = grenze if grenze is not None else zeitgrenze_fuer(bogen, schueler)
    return grenze is None or (beobachtung.datum is not None and beobachtung.datum >= grenze)
