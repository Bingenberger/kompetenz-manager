"""Inhalte für das Elterngespräch.

Eltern wollen keine Beobachtungstabellen lesen. Sie wollen wissen: Wo steht
mein Kind gut, wo gibt es Entwicklungspotenzial? Dafür fasst dieses Modul die
Beobachtungen je Bogen und Bereich zusammen, holt die Diagnostik in derselben
Form wie die Schülerakte und listet die Ereignisse des Schuljahres.

Die Stufen (sicher beherrscht ... reicht noch nicht) sind dieselben wie im
Einzelbericht und in der Klassenübersicht (competency_matrix).
"""

from competency_matrix import LEVEL_LABELS, competency_level
from competency_trend import compute_trend
from diagnostik import (
    STUFEN,
    diagramme,
    risikogrenzen,
    verlauf,
    werte_zeilen,
)
from klassenzugriff import sichtbare_ereignisse
from models import ErziehungsEreignis
from school_year import active_school_year_start

# CSS-Klassen in includes/elternberatung_inhalt.html - vier Stufen, vier Farben der Palette.
LEVEL_FARBEN = {'stark': 'ls-stark', 'sicher': 'ls-sicher', 'teils': 'ls-teils', 'schwach': 'ls-schwach'}
LEVEL_REIHENFOLGE = ('stark', 'sicher', 'teils', 'schwach')
# Elternfreundliche Überschriften für die beiden Spalten je Bereich.
STAERKEN = ('stark', 'sicher')
POTENZIAL = ('teils', 'schwach')


def _mittel(werte):
    werte = [w for w in werte if w is not None]
    return round(sum(werte) / len(werte), 1) if werte else None


def _item_wert(item_row):
    """Mittelwert des laufenden Schuljahres, None ohne bewertete Einträge."""
    return item_row['durchschnitt'] if item_row['durchschnitt_color'] != 'secondary' else None


def kompetenz_uebersicht(bogen_rows):
    """Je Bogen: Stufe gesamt, je Bereich Stufe, Stärken und Entwicklungsfelder.

    Baut auf den Zeilen von _build_bogen_entries_for_student auf, damit die
    Übersicht und die Detailtabellen darunter dieselben Zahlen zeigen.
    """
    uebersicht = []
    for row in bogen_rows:
        bereiche = {}
        for item_row in row['item_rows']:
            wert = _item_wert(item_row)
            if wert is None:
                continue
            eintrag = {
                'item': item_row['item'],
                'wert': wert,
                'level': competency_level(wert),
                'anzahl': item_row['anzahl'],
                'trend': compute_trend(item_row['eintraege']),
            }
            bereiche.setdefault(item_row['item'].bereich or 'Allgemein', []).append(eintrag)
        if not bereiche:
            continue

        bereich_liste = []
        for name, items in bereiche.items():
            mittel = _mittel(i['wert'] for i in items)
            items.sort(key=lambda i: (-i['wert'], i['item'].text.lower()))
            bereich_liste.append({
                'name': name,
                'mittel': mittel,
                'level': competency_level(mittel),
                'breite': round(mittel / 4 * 100) if mittel else 0,
                'staerken': [i for i in items if i['level'] in STAERKEN],
                'potenzial': [i for i in items if i['level'] in POTENZIAL],
                'anzahl': len(items),
            })
        bereich_liste.sort(key=lambda b: b['name'].lower())
        alle = [i for b in bereich_liste for i in (b['staerken'] + b['potenzial'])]
        mittel = _mittel(i['wert'] for i in alle)
        uebersicht.append({
            'bogen': row['bogen'],
            'mittel': mittel,
            'level': competency_level(mittel),
            'bereiche': bereich_liste,
            'anzahl_items': len(alle),
            'anzahl_staerken': sum(len(b['staerken']) for b in bereich_liste),
            'anzahl_potenzial': sum(len(b['potenzial']) for b in bereich_liste),
        })
    return uebersicht


def diagnostik_kontext(schueler):
    """Verlauf, Diagramm und Ergebniszeilen je Lernbereich - wie in der Akte."""
    grenzen = risikogrenzen()
    bereiche = []
    for eintrag in verlauf(schueler, grenzen):
        bereiche.append({
            'eintrag': eintrag,
            'diagramme': diagramme(eintrag, grenzen),
            'zeilen': [(a, werte_zeilen(a.ergebnis)) for a in reversed(eintrag['auswertungen'])],
        })
    return bereiche


def ereignisse_im_schuljahr(schueler, limit=12, user=None):
    """Ereignisse des laufenden Schuljahres, neueste zuerst; ohne Schuljahr alle.

    Mit user nur die Ereignisse, die diese Lehrkraft sehen darf.
    """
    query = ErziehungsEreignis.query.filter(ErziehungsEreignis.student_id == schueler.id)
    if user is not None:
        query = sichtbare_ereignisse(query, user)
    beginn = active_school_year_start()
    if beginn:
        query = query.filter(ErziehungsEreignis.datum >= beginn)
    return query.order_by(ErziehungsEreignis.datum.desc(), ErziehungsEreignis.id.desc()).limit(limit).all()


def beratungs_kontext(schueler, bogen_rows, vertrauliches=True, user=None):
    """Alles, was die Vorlage für die Inhalte des Gesprächs braucht.

    vertrauliches: Diagnostik und Ereignisse sind an die Klasse gebunden -
    sie sieht nur, wer das Kind auch dort sehen darf (Klassenleitung,
    Fachlehrkraft, Verwaltung). Ereignisse, die die Lehrkraft selbst angelegt
    hat oder fuer die sie zustaendig ist, sieht sie trotzdem (user).
    """
    return {
        'uebersicht': kompetenz_uebersicht(bogen_rows),
        'level_labels': LEVEL_LABELS,
        'level_farben': LEVEL_FARBEN,
        'diagnostik': diagnostik_kontext(schueler) if vertrauliches else [],
        'diagnostik_stufen': STUFEN,
        'ereignisse': ereignisse_im_schuljahr(schueler, user=user) if (vertrauliches or user is not None) else [],
        'vertrauliches': vertrauliches,
    }
