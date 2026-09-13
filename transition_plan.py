"""Planung des Schuljahreswechsels anhand der Jahrgänge.

Bisher las der Wechsel die Klassenstufe aus der Ziffer am Anfang des
Klassennamens. Jetzt entscheidet der Jahrgang: Jahrgang 4 wird archiviert,
alle anderen steigen um eins. Wohin ein Kind dabei wandert, hängt von der Art
seiner Klasse ab:

- Klasse mit Stufennamen ("3a"): die Kinder wandern nach "4a". Die Klasse
  selbst behält ihren Jahrgang, der ja im Namen steht.
- Frei benannte Klasse mit einem Jahrgang ("Füchse", 3): die Gruppe bleibt
  zusammen und wird Jahrgang 4.
- Jahrgangsübergreifende Klasse ("Blau", 1 und 2): Kinder, deren neuer
  Jahrgang noch dazugehört, bleiben. Die übrigen brauchen eine Zielklasse -
  wer die Klasse verlässt, entscheidet ein Mensch, nicht diese Planung.

Kinder ohne bestimmbaren Jahrgang bleiben unverändert und werden gemeldet.

Das Modul plant nur und verändert nichts. Die Route führt den Plan aus.
"""

from jahrgang import (
    ABSCHLUSSJAHRGANG,
    grade_from_name,
    name_for_grade,
    single_jahrgang,
)
from models import Klasse

AKTION_VERSETZEN = 'promote'
AKTION_INDIVIDUELL = 'individual'
AKTION_ARCHIVIEREN = 'archive'
AKTION_UNVERAENDERT = 'unchanged'

AKTION_ENTFERNEN = 'remove'


def effective_jahrgang(schueler):
    """Der wirksame Jahrgang eines Kindes.

    Der gespeicherte Wert, sonst der eindeutige Jahrgang seiner Klasse - dieselbe
    Regel wie bei der Übernahme der Bestandsdaten. So wird ein Kind in "3a" auch
    dann richtig versetzt, wenn sein Jahrgang noch nicht eingetragen ist.
    """
    return schueler.jahrgang or single_jahrgang(schueler.klasse)


def _post_transition_jahrgaenge(klasse):
    """Die Jahrgänge einer Klasse nach dem Wechsel."""
    stufen = klasse.jahrgaenge
    if grade_from_name(klasse.name) is not None:
        return stufen  # Stufenname: fest
    if len(stufen) == 1 and stufen[0] < ABSCHLUSSJAHRGANG:
        return [stufen[0] + 1]  # Gruppe altert
    return stufen  # gemischt, Abschlussgruppe oder ohne Jahrgang: unverändert


def class_universe():
    """Alle möglichen Zielklassen mit ihren Jahrgängen nach dem Wechsel.

    Enthält neben den bestehenden Klassen auch die Stufennamen, in die versetzt
    wird, aber noch keine Klasse existiert - aus "3a" wird "4a", auch wenn es
    "4a" noch nicht gibt.
    """
    universum = {}
    for klasse in Klasse.query.all():
        universum[klasse.name] = _post_transition_jahrgaenge(klasse)

    for name in list(universum):
        stufe = grade_from_name(name)
        if stufe is not None and stufe < ABSCHLUSSJAHRGANG:
            ziel = name_for_grade(name, stufe + 1)
            universum.setdefault(ziel, [stufe + 1])
    return universum


def options_for(universum, jahrgang):
    """Zielklassen, die nach dem Wechsel diesen Jahrgang führen."""
    if jahrgang is None:
        return []
    return sorted(
        (name for name, stufen in universum.items() if jahrgang in stufen),
        key=lambda name: ((grade_from_name(name) or 99), name.lower()),
    )


def plan_student(schueler, ist_wiederholer, gewaehltes_ziel, universum, klassen):
    """Plant den Wechsel für ein Kind.

    Gibt ein dict mit action, target_class, new_jahrgang, needs_target,
    target_options und reason zurück.
    """
    jahrgang = effective_jahrgang(schueler)
    klasse_name = schueler.klasse
    zeile = {
        'student': schueler,
        'jahrgang': jahrgang,
        'needs_target': False,
        'reason': None,
        # Fuer den Wiederholer-Schalter immer berechnet: Klassen, die den
        # bisherigen Jahrgang auch nach dem Wechsel fuehren.
        'repeater_options': options_for(universum, jahrgang),
    }

    if ist_wiederholer:
        zeile.update({
            'action': AKTION_INDIVIDUELL,
            'target_class': gewaehltes_ziel or None,
            'new_jahrgang': jahrgang,
            'needs_target': True,
            'target_options': zeile['repeater_options'],
            'reason': 'wiederholt',
        })
        return zeile

    if jahrgang is None:
        zeile.update({
            'action': AKTION_UNVERAENDERT,
            'target_class': klasse_name,
            'new_jahrgang': None,
            'target_options': [],
            'reason': 'Jahrgang fehlt',
        })
        return zeile

    if jahrgang >= ABSCHLUSSJAHRGANG:
        zeile.update({
            'action': AKTION_ARCHIVIEREN,
            'target_class': None,
            'new_jahrgang': jahrgang,
            'target_options': [],
        })
        return zeile

    neuer_jahrgang = jahrgang + 1

    if grade_from_name(klasse_name) is not None:
        zeile.update({
            'action': AKTION_VERSETZEN,
            'target_class': name_for_grade(klasse_name, neuer_jahrgang),
            'new_jahrgang': neuer_jahrgang,
            'target_options': zeile['repeater_options'],
        })
        return zeile

    klasse = klassen.get(klasse_name)
    stufen = klasse.jahrgaenge if klasse else []

    if len(stufen) <= 1 or neuer_jahrgang in stufen:
        # Gruppe mit einem Jahrgang altert gemeinsam; in einer gemischten
        # Klasse bleibt, wer weiter dazugehoert.
        zeile.update({
            'action': AKTION_VERSETZEN,
            'target_class': klasse_name,
            'new_jahrgang': neuer_jahrgang,
            'target_options': zeile['repeater_options'],
        })
        return zeile

    # Gemischte Klasse, zu der der neue Jahrgang nicht mehr gehoert.
    zeile.update({
        'action': AKTION_INDIVIDUELL,
        'target_class': gewaehltes_ziel or None,
        'new_jahrgang': neuer_jahrgang,
        'needs_target': True,
        'target_options': options_for(universum, neuer_jahrgang),
        'reason': (
            f'verlässt {klasse_name}: Jahrgang {neuer_jahrgang} gehört nicht zur Klasse'
        ),
    })
    return zeile


def plan_assignment(zuordnung, klassen):
    """Plant, was aus einer Lehrkraft-Zuordnung wird."""
    name = zuordnung.klasse
    stufe = grade_from_name(name)

    if stufe is not None:
        if stufe >= ABSCHLUSSJAHRGANG:
            return {'assignment': zuordnung, 'action': AKTION_ENTFERNEN, 'target_class': None}
        return {
            'assignment': zuordnung,
            'action': AKTION_VERSETZEN,
            'target_class': name_for_grade(name, stufe + 1),
        }

    klasse = klassen.get(name)
    stufen = klasse.jahrgaenge if klasse else []
    if len(stufen) == 1 and stufen[0] >= ABSCHLUSSJAHRGANG:
        # Die Gruppe verlaesst die Schule, die Zuordnung faellt weg.
        return {'assignment': zuordnung, 'action': AKTION_ENTFERNEN, 'target_class': None}
    return {'assignment': zuordnung, 'action': AKTION_UNVERAENDERT, 'target_class': name}


def classes_to_age(klassen):
    """Frei benannte Klassen mit einem Jahrgang unter 4 - sie altern als Gruppe."""
    return [
        klasse for klasse in klassen.values()
        if grade_from_name(klasse.name) is None
        and len(klasse.jahrgaenge) == 1
        and klasse.jahrgaenge[0] < ABSCHLUSSJAHRGANG
    ]


def invalid_targets(zeilen):
    """Zeilen, die eine Zielklasse brauchen, aber keine gültige haben."""
    return [
        zeile for zeile in zeilen
        if zeile['needs_target']
        and (not zeile['target_class'] or zeile['target_class'] not in zeile['target_options'])
    ]
