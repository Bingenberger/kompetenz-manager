"""Klassenbezogener Zugriff auf ein Kind.

Die meisten Seiten zeigen alle Kinder der Schule. Was an die Klasse gebunden
ist - Diagnostik, Ereignisse, Foerderplaene - sieht nur, wer das Kind auch
unterrichtet: Klassenleitung, Fachlehrkraft oder die Verwaltung.
"""

from student_selection import get_distinct_klassen, get_user_klassenkontext


def zugaengliche_klassen(user):
    """Klassen, deren Kinder eine Lehrkraft in den klassengebundenen Bereichen sieht.

    Namen ohne Leerzeichen am Rand: Formulare kuerzen ihre Eingabe, und ein
    gespeichertes "1c " darf deshalb nicht zu einer Ablehnung von "1c" fuehren.
    """
    if user.is_admin:
        namen = get_distinct_klassen()
    else:
        kontext = get_user_klassenkontext(user)
        namen = set(kontext.get('fachklassen') or set())
        if kontext.get('klassenleitung'):
            namen.add(kontext['klassenleitung'])
    return sorted({(name or '').strip() for name in namen} - {''}, key=str.lower)


def darf_kind_sehen(user, schueler):
    if not schueler:
        return False
    if user.is_admin:
        return True
    return bool((schueler.klasse or '').strip()) and schueler.klasse.strip() in zugaengliche_klassen(user)
