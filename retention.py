"""Aufbewahrungsfristen für archivierte Kinder.

Der Schuljahreswechsel archiviert Viertklässler sauber über `is_active` und
`archived_at` — löscht aber nie. Nach wenigen Jahren enthält die Datenbank
vollständige Förderakten von Kindern, die längst an weiterführenden Schulen
sind.

Dieses Modul rechnet nur. Es löscht nichts und es entscheidet nichts: **die
Frist gibt das Landesrecht vor, nicht diese Software.** Ohne eingetragene Frist
meldet die Anwendung gar nichts als überfällig — sie soll die Rechtslage
sichtbar und vollziehbar machen, nicht eine erfinden.

Gelöscht wird ausschließlich auf ausdrückliche Bestätigung eines Menschen.
Eine automatische Löschung von Schülerakten wäre der falsche Weg: sie liefe
unbeaufsichtigt, wäre nicht rückgängig zu machen und würde im Zweifel im
Sommer zuschlagen, wenn niemand hinsieht.
"""

from datetime import date

from models import Schueler

STATUS_FAELLIG = 'faellig'
STATUS_LAEUFT = 'laeuft'
STATUS_UNBEKANNT = 'unbekannt'

STATUS_LABELS = {
    STATUS_FAELLIG: 'Frist abgelaufen',
    STATUS_LAEUFT: 'Frist läuft',
    STATUS_UNBEKANNT: 'Archivierungsdatum fehlt',
}


def retention_years(config):
    """Die eingestellte Frist in Jahren, oder None wenn keine gesetzt ist."""
    if not config:
        return None
    jahre = config.aufbewahrung_jahre
    return jahre if jahre and jahre > 0 else None


def due_date(archived_at, jahre):
    """Wann die Frist für einen archivierten Datensatz abläuft.

    Gerechnet wird auf den Kalendertag genau. Der 29. Februar wird auf den
    28. gelegt, sonst gäbe es Datensätze, deren Frist nur alle vier Jahre
    abläuft.
    """
    if not archived_at or not jahre:
        return None

    tag = archived_at.date() if hasattr(archived_at, 'date') else archived_at
    try:
        return tag.replace(year=tag.year + jahre)
    except ValueError:
        return tag.replace(year=tag.year + jahre, day=28)


def _status(faellig_am, heute):
    if faellig_am is None:
        return STATUS_UNBEKANNT
    return STATUS_FAELLIG if faellig_am <= heute else STATUS_LAEUFT


def archived_students(config, heute=None):
    """Alle archivierten Kinder mit ihrem Fristenstand.

    Sortiert nach Dringlichkeit: überfällig zuerst, darin das älteste oben.
    Kinder ohne Archivierungsdatum stehen am Ende - sie brauchen eine
    Entscheidung von Hand.
    """
    heute = heute or date.today()
    jahre = retention_years(config)

    kinder = (
        Schueler.query
        .filter(Schueler.is_active.is_(False))
        .order_by(Schueler.nachname.asc(), Schueler.vorname.asc())
        .all()
    )

    zeilen = []
    for kind in kinder:
        faellig_am = due_date(kind.archived_at, jahre)
        status = _status(faellig_am, heute) if jahre else STATUS_LAEUFT
        if jahre is None:
            # Ohne Frist gibt es kein "faellig" - nur eine Liste des Bestands.
            status = STATUS_UNBEKANNT if not kind.archived_at else STATUS_LAEUFT
        zeilen.append({
            'schueler': kind,
            'archiviert_am': kind.archived_at,
            'faellig_am': faellig_am,
            'status': status,
            'label': STATUS_LABELS[status],
            'tage_ueberfaellig': (heute - faellig_am).days if faellig_am and faellig_am <= heute else None,
        })

    rang = {STATUS_FAELLIG: 0, STATUS_LAEUFT: 1, STATUS_UNBEKANNT: 2}
    zeilen.sort(key=lambda zeile: (
        rang[zeile['status']],
        zeile['faellig_am'] or date.max,
        (zeile['schueler'].nachname or '').lower(),
    ))
    return zeilen


def overdue_ids(zeilen):
    """Die IDs der Kinder, deren Frist abgelaufen ist."""
    return {
        zeile['schueler'].id for zeile in zeilen
        if zeile['status'] == STATUS_FAELLIG
    }


def summarize(zeilen):
    gezaehlt = {STATUS_FAELLIG: 0, STATUS_LAEUFT: 0, STATUS_UNBEKANNT: 0}
    for zeile in zeilen:
        gezaehlt[zeile['status']] += 1
    gezaehlt['gesamt'] = len(zeilen)
    return gezaehlt
