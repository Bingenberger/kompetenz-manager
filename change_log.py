"""Gemeinsame Grundlage für Änderungsprotokolle.

Für erzieherische Ereignisse gab es von Anfang an ein Journal: wer wann was
geändert hat. Förderpläne, Elternkontakte und Gesprächsprotokolle liessen sich
dagegen spurlos ändern — gerade bei einem Protokoll, auf das sich später jemand
beruft, ist das unschön.

Dieses Modul stellt bereit, was alle drei brauchen: einen Vorher-Zustand
festhalten, ihn mit dem Nachher vergleichen und die Änderung in einen Satz
fassen. Die Journal-Tabellen selbst bleiben je Gegenstand eigene Modelle mit
Fremdschlüssel — so nimmt das Löschen eines Förderplans sein Journal mit,
statt verwaiste Zeilen zu hinterlassen.

Festgehalten wird der alte wie der neue Wert, lange Texte gekürzt. Das folgt
dem Vorbild bei den Ereignissen: Bei einem Protokoll ist gerade die Frage
"was stand vorher da" der Grund, warum es ein Journal gibt.
"""

TEXT_LIMIT = 180


def format_value(value, limit=TEXT_LIMIT):
    """Bereitet einen Wert für das Journal auf.

    Mehrzeiliges wird zu einer Zeile, Langes gekürzt - ein Journal soll
    überflogen werden können, nicht den Datensatz ein zweites Mal speichern.
    """
    if value is None or value == '':
        return 'leer'
    if hasattr(value, 'strftime'):
        return value.strftime('%d.%m.%Y')

    text = ' '.join(str(value).split())
    if not text:
        return 'leer'
    if len(text) <= limit:
        return text
    return text[:limit - 1] + '…'


def snapshot(objekt, felder):
    """Hält die beobachteten Felder eines Objekts fest.

    Vor der Änderung aufrufen; danach mit describe() vergleichen.
    """
    return {name: getattr(objekt, name, None) for name, _ in felder}


def describe(before, after, felder):
    """Beschreibt, was sich geändert hat.

    Gibt einen leeren Text zurück, wenn nichts anders ist - dann gehört auch
    kein Journaleintrag geschrieben.
    """
    aenderungen = []
    for name, label in felder:
        alt = format_value(before.get(name))
        neu = format_value(after.get(name))
        # Verglichen wird die aufbereitete Fassung, nicht der Rohwert. Sonst
        # entstuenden Eintraege wie "12.09.2026 → 12.09.2026", weil sich beim
        # Speichern die Mikrosekunden eines Zeitstempels geaendert haben. Eine
        # Aenderung, die im Journal nicht zu sehen ist, gehoert nicht hinein.
        if alt == neu:
            continue
        aenderungen.append(f'{label}: {alt} → {neu}')
    return '; '.join(aenderungen)


def describe_creation(objekt, felder):
    """Beschreibt einen neu angelegten Datensatz über seine gefüllten Felder."""
    teile = []
    for name, label in felder:
        wert = getattr(objekt, name, None)
        if wert in (None, ''):
            continue
        teile.append(f'{label}: {format_value(wert)}')
    return '; '.join(teile)
