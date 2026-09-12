"""Eine Suche über die Dinge, zu denen man navigiert.

Jeder Weg zu einem Kind führte bisher über Auswahllisten und Reiter. Bei einer
Klasse geht das; wer mehrere Klassen betreut, klickt sich durch. Gesucht wird
deshalb nach dem, was Einstiegspunkte sind:

- Kinder, von dort aus erreicht man Förderplanung, Arbeitspläne, Elternkontakte
  und Ereignisse über die Schülerakte
- Kompetenzen, um eine Beobachtung zu erfassen, ohne den Bogen durchzublättern
- Beobachtungsbögen

Mehrere Begriffe werden mit UND verknüpft: "abt 3a" findet Abt in Klasse 3a,
"anna abt" findet sie unabhängig von der Reihenfolge der Namen.

Zur Schreibweise: ILIKE ignoriert Groß- und Kleinschreibung. Bei Umlauten
gilt das auf PostgreSQL - produktiv im Einsatz - vollständig, unter SQLite nur
für ASCII: "müller" findet dort "Müller", "MÜLLER" nicht. Diakritika werden
nirgends ignoriert, "celine" findet "Céline" also nicht. Dafür bräuchte es die
unaccent-Erweiterung von PostgreSQL oder eine eigene Suchspalte.
"""

from sqlalchemy import func

from models import Bogen, Item, Schueler
from student_selection import get_user_klassenkontext

# Mehr Treffer als diese trägt eine Ergebnisliste nicht; wer so unspezifisch
# sucht, braucht eine andere Eingabe, nicht mehr Zeilen.
LIMIT_PER_GROUP = 25
MIN_QUERY_LENGTH = 2

# LIKE-Sonderzeichen, die sonst als Muster wirken statt als Text.
LIKE_ESCAPE = '\\'


def _escape_like(value):
    for zeichen in (LIKE_ESCAPE, '%', '_'):
        value = value.replace(zeichen, LIKE_ESCAPE + zeichen)
    return value


def tokenize(query):
    """Zerlegt die Eingabe in Suchbegriffe."""
    return [teil for teil in (query or '').split() if teil.strip()]


def _all_tokens_match(expression, tokens):
    """Bedingung: jeder Begriff kommt im durchsuchbaren Text vor."""
    return [
        expression.ilike(f'%{_escape_like(token)}%', escape=LIKE_ESCAPE)
        for token in tokens
    ]


def _searchable(*columns):
    """Verbindet Spalten zu einem durchsuchbaren Text.

    coalesce ist nicht optional: in SQL ergibt jede Verkettung mit NULL wieder
    NULL, und ein Kind ohne eingetragene Klasse waere damit nie zu finden.
    """
    teile = []
    for spalte in columns:
        if teile:
            teile.append(' ')
        teile.append(func.coalesce(spalte, ''))
    ausdruck = teile[0]
    for teil in teile[1:]:
        ausdruck = ausdruck + teil
    return ausdruck


def _student_priority(schueler, kontext):
    if kontext['klassenleitung'] and schueler.klasse == kontext['klassenleitung']:
        return 0
    if schueler.klasse in kontext['fachklassen']:
        return 1
    return 2


def find_students(user, tokens, include_archived=True, limit=LIMIT_PER_GROUP):
    """Kinder nach Vorname, Nachname und Klasse.

    Archivierte Kinder sind absichtlich dabei: wer nach einem Namen sucht, will
    ihn finden, auch wenn das Kind die Schule verlassen hat. Die Ergebnisliste
    weist sie aus.
    """
    if not tokens:
        return []

    # Vor- und Nachname zusammen durchsuchen, damit "anna abt" trifft.
    durchsuchbar = _searchable(Schueler.vorname, Schueler.nachname, Schueler.klasse)
    query = Schueler.query.filter(*_all_tokens_match(durchsuchbar, tokens))
    if not include_archived:
        query = query.filter(Schueler.is_active.is_(True))

    treffer = query.limit(limit * 2).all()

    # Die eigene Klasse zuerst - dieselbe Rangfolge wie in den Auswahllisten.
    kontext = get_user_klassenkontext(user)
    treffer.sort(
        key=lambda schueler: (
            not schueler.is_active,
            _student_priority(schueler, kontext),
            (schueler.nachname or '').lower(),
            (schueler.vorname or '').lower(),
        )
    )
    return treffer[:limit]


def find_competencies(tokens, limit=LIMIT_PER_GROUP):
    """Kompetenzen nach Text und Bereich, mit ihrem Bogen."""
    if not tokens:
        return []

    durchsuchbar = _searchable(Item.text, Item.bereich)
    return (
        Item.query
        .join(Bogen, Item.bogen_id == Bogen.id)
        .filter(*_all_tokens_match(durchsuchbar, tokens))
        .order_by(Bogen.titel.asc(), Item.bereich.asc(), Item.text.asc())
        .limit(limit)
        .all()
    )


def find_boegen(tokens, limit=LIMIT_PER_GROUP):
    if not tokens:
        return []

    return (
        Bogen.query
        .filter(*_all_tokens_match(_searchable(Bogen.titel), tokens))
        .order_by(Bogen.titel.asc())
        .limit(limit)
        .all()
    )


def search(user, query, include_archived=True):
    """Sucht in allen Gruppen und meldet, was gefunden wurde.

    `too_short` unterscheidet "nichts eingegeben" von "nichts gefunden" - sonst
    meldet die Ansicht bei einem einzelnen Buchstaben ein leeres Ergebnis,
    obwohl gar nicht gesucht wurde.
    """
    tokens = tokenize(query)
    zu_kurz = bool(tokens) and max(len(token) for token in tokens) < MIN_QUERY_LENGTH

    if not tokens or zu_kurz:
        return {
            'query': query or '',
            'tokens': tokens,
            'students': [],
            'competencies': [],
            'boegen': [],
            'total': 0,
            'too_short': zu_kurz,
        }

    students = find_students(user, tokens, include_archived=include_archived)
    competencies = find_competencies(tokens)
    boegen = find_boegen(tokens)

    return {
        'query': query,
        'tokens': tokens,
        'students': students,
        'competencies': competencies,
        'boegen': boegen,
        'total': len(students) + len(competencies) + len(boegen),
        'too_short': False,
    }
