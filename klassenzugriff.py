"""Klassenbezogener Zugriff auf ein Kind.

Die meisten Seiten zeigen alle Kinder der Schule. Was an die Klasse gebunden
ist - Diagnostik, Ereignisse, Elternkontakte - sieht nur, wer das Kind auch
unterrichtet: Klassenleitung, Fachlehrkraft oder die Verwaltung. Eigene
Eintraege sieht jede Lehrkraft immer.
"""

from sqlalchemy import func, or_, select

from models import Elternkontakt, ErziehungsEreignis, ErziehungsEreignisBetroffenesKind, Schueler
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


# ----------------------------------------------------------------------
# Elternkontakte, Elternberatungen und Ereignisse
#
# Lehrkraefte sehen, was sie selbst angelegt haben, und alles zu Kindern
# ihrer Klassen (Klassenleitung oder Fachunterricht). Bei Ereignissen zaehlt
# ausserdem, wer zustaendig ist, und ein betroffenes Kind aus der eigenen
# Klasse. Anlegen duerfen weiterhin alle fuer jedes Kind.
# ----------------------------------------------------------------------

def _kinder_der_klassen(user):
    """Unterabfrage: IDs der Kinder in den Klassen der Lehrkraft."""
    return select(Schueler.id).where(func.trim(Schueler.klasse).in_(zugaengliche_klassen(user)))


def sichtbare_elternkontakte(query, user, modell=None):
    """Schraenkt eine Abfrage auf Elternkontakt oder Elternberatung ein."""
    modell = modell or Elternkontakt
    if user.is_admin:
        return query
    return query.filter(or_(modell.user_id == user.id, modell.schueler_id.in_(_kinder_der_klassen(user))))


def darf_elternkontakt_sehen(user, eintrag):
    """Fuer Elternkontakt (Notiz, Protokoll) und Elternberatung."""
    if not eintrag:
        return False
    if user.is_admin or (eintrag.user_id and eintrag.user_id == user.id):
        return True
    return darf_kind_sehen(user, eintrag.schueler)


def sichtbare_ereignisse(query, user):
    if user.is_admin:
        return query
    kinder = _kinder_der_klassen(user)
    betroffen = select(ErziehungsEreignisBetroffenesKind.event_id).where(
        ErziehungsEreignisBetroffenesKind.student_id.in_(kinder)
    )
    return query.filter(or_(
        ErziehungsEreignis.created_by_user_id == user.id,
        ErziehungsEreignis.assigned_user_id == user.id,
        ErziehungsEreignis.student_id.in_(kinder),
        ErziehungsEreignis.id.in_(betroffen),
    ))


def darf_ereignis_sehen(user, ereignis):
    if not ereignis:
        return False
    if user.is_admin or user.id in {ereignis.created_by_user_id, ereignis.assigned_user_id}:
        return True
    if darf_kind_sehen(user, ereignis.student):
        return True
    return any(darf_kind_sehen(user, zeile.student) for zeile in ereignis.affected_students)
