"""Material für Zeugnisberichte.

Zweimal im Jahr sitzt eine Grundschullehrkraft an fünfundzwanzig
Zeugnisberichten — und die Beobachtungen, die genau das belegen sollen, liegen
in dieser Anwendung. Bisher hiess das: pro Kind den Bericht öffnen, durch die
Kompetenzen blättern, die Kommentare heraussuchen.

Bewusst eine Materialsammlung, kein fertiger Text. Formulieren bleibt die
Aufgabe der Lehrkraft; das Zusammentragen nicht.

Die Gliederung folgt dem Zeugnis, nicht der Datenbank: Bogen als Abschnitt,
Bereich als Unterpunkt. Und im Vordergrund stehen die Kommentare — der
Mittelwert sagt, wo ein Kind steht, aber der Satz "hat den Streit selbst
geschlichtet" ist das, woraus ein Zeugnissatz wird.
"""

from competency_trend import compute_trend
from extensions import db
from models import Beobachtung, Bogen, Foerderplan, Item, Schueler
from school_year import active_school_year_start, observation_period_start

WERT_SYMBOLE = {1: '-', 2: 'o', 3: '+', 4: '++'}


def _symbol(wert):
    return WERT_SYMBOLE.get(wert, '?') if wert is not None else '–'


def _mittel(werte):
    return round(sum(werte) / len(werte), 1) if werte else None


def collect_material(schueler):
    """Traegt die Beobachtungen des laufenden Schuljahres nach Bogen und Bereich zusammen."""
    # Fuer die Spaltenabfrage die Zeitgrenze, fuer den Datumsvergleich der
    # Foerderplaene das Datum - Beobachtung.datum ist DateTime, datum_erstellung Date.
    grenze = active_school_year_start()
    zeitgrenze = observation_period_start()

    query = (
        db.session.query(Beobachtung, Item, Bogen)
        .join(Item, Beobachtung.item_id == Item.id)
        .join(Bogen, Item.bogen_id == Bogen.id)
        .filter(Beobachtung.schueler_id == schueler.id)
    )
    if zeitgrenze:
        query = query.filter(Beobachtung.datum >= zeitgrenze)

    zeilen = query.order_by(
        Bogen.titel.asc(), Item.bereich.asc(), Item.text.asc(), Beobachtung.datum.asc(),
    ).all()

    # Nach Bogen, darin nach Bereich, darin nach Kompetenz buendeln.
    struktur = {}
    for beobachtung, item, bogen in zeilen:
        bereich = (item.bereich or '').strip() or 'Ohne Bereich'
        bogen_eintrag = struktur.setdefault(bogen.titel, {})
        bereich_eintrag = bogen_eintrag.setdefault(bereich, {})
        bereich_eintrag.setdefault(item.id, {'item': item, 'beobachtungen': []})
        bereich_eintrag[item.id]['beobachtungen'].append(beobachtung)

    boegen = []
    for bogen_titel, bereiche_roh in struktur.items():
        bereiche = []
        bogen_werte = []

        for bereich_name, kompetenzen_roh in bereiche_roh.items():
            kompetenzen = []
            bereich_werte = []

            for eintrag in kompetenzen_roh.values():
                beobachtungen = eintrag['beobachtungen']
                werte = [b.wert for b in beobachtungen if b.wert is not None]
                bereich_werte.extend(werte)

                # Die Kommentare sind der eigentliche Ertrag: jeder ist ein Satz,
                # den jemand beim Beobachten aufgeschrieben hat.
                kommentare = [
                    {
                        'datum': b.datum,
                        'symbol': _symbol(b.wert),
                        'wert': b.wert,
                        'text': b.kommentar.strip(),
                        'anlass': (b.anlass or '').strip(),
                    }
                    for b in beobachtungen
                    if (b.kommentar or '').strip()
                ]

                kompetenzen.append({
                    'item': eintrag['item'],
                    'anzahl': len(beobachtungen),
                    'durchschnitt': _mittel(werte),
                    'trend': compute_trend(beobachtungen),
                    'kommentare': kommentare,
                })

            kompetenzen.sort(key=lambda k: (k['durchschnitt'] is None, k['durchschnitt'] or 0))
            bereiche.append({
                'name': bereich_name,
                'kompetenzen': kompetenzen,
                'durchschnitt': _mittel(bereich_werte),
                'anzahl': len(bereich_werte),
                'kommentare_anzahl': sum(len(k['kommentare']) for k in kompetenzen),
            })
            bogen_werte.extend(bereich_werte)

        bereiche.sort(key=lambda b: b['name'].lower())
        boegen.append({
            'titel': bogen_titel,
            'bereiche': bereiche,
            'durchschnitt': _mittel(bogen_werte),
            'anzahl': len(bogen_werte),
        })

    boegen.sort(key=lambda b: b['titel'].lower())

    foerderplaene = (
        Foerderplan.query
        .filter(Foerderplan.schueler_id == schueler.id)
        .order_by(Foerderplan.datum_erstellung.desc())
        .all()
    )
    if grenze:
        foerderplaene = [
            plan for plan in foerderplaene
            if not plan.datum_erstellung or plan.datum_erstellung >= grenze
        ]

    return {
        'schueler': schueler,
        'grundlage': schueler.foerdergrundlage,
        'boegen': boegen,
        'foerderplaene': foerderplaene,
        'schuljahr_ab': grenze,
        'beobachtungen_anzahl': len(zeilen),
        'kommentare_anzahl': sum(
            bereich['kommentare_anzahl']
            for bogen in boegen for bereich in bogen['bereiche']
        ),
    }


def material_blocks(material, erzeugt_am, mit_seitenumbruch=False):
    """Wandelt die Sammlung in Bloecke fuer build_odt_document()."""
    schueler = material['schueler']
    name = f'{schueler.vorname} {schueler.nachname}'.strip()

    blocks = []
    if mit_seitenumbruch:
        blocks.append({'type': 'pagebreak'})

    blocks.append({'type': 'paragraph', 'style': 'Titel', 'text': f'Zeugnismaterial: {name}'})
    zeitraum = (
        f"Schuljahr ab {material['schuljahr_ab']:%d.%m.%Y}"
        if material['schuljahr_ab'] else 'gesamter Bestand'
    )
    blocks.append({
        'type': 'paragraph', 'style': 'Klein',
        'text': f"Klasse {schueler.klasse or '–'} · {zeitraum} · "
                f"{material['beobachtungen_anzahl']} Beobachtungen, "
                f"davon {material['kommentare_anzahl']} mit Kommentar · "
                f'erzeugt am {erzeugt_am}',
    })

    if material['grundlage'] and (material['grundlage'].besondere_staerken or '').strip():
        blocks.append({'type': 'heading', 'level': 1, 'text': 'Besondere Stärken'})
        blocks.append({
            'type': 'paragraph',
            'text': material['grundlage'].besondere_staerken.strip(),
        })

    if not material['boegen']:
        blocks.append({'type': 'heading', 'level': 1, 'text': 'Beobachtungen'})
        blocks.append({
            'type': 'paragraph',
            'text': 'Im laufenden Schuljahr wurden noch keine Beobachtungen erfasst.',
        })

    for bogen in material['boegen']:
        kopf = bogen['titel']
        if bogen['durchschnitt'] is not None:
            kopf += f" (Ø {bogen['durchschnitt']} aus {bogen['anzahl']} Bewertungen)"
        blocks.append({'type': 'heading', 'level': 1, 'text': kopf})

        for bereich in bogen['bereiche']:
            unterkopf = bereich['name']
            if bereich['durchschnitt'] is not None:
                unterkopf += f" — Ø {bereich['durchschnitt']}"
            blocks.append({'type': 'heading', 'level': 2, 'text': unterkopf})

            zeilen = []
            for kompetenz in bereich['kompetenzen']:
                trend = kompetenz['trend']
                verlauf = trend['label'] if trend else '–'
                zeilen.append([
                    kompetenz['item'].text,
                    str(kompetenz['durchschnitt']) if kompetenz['durchschnitt'] is not None else '–',
                    str(kompetenz['anzahl']),
                    verlauf,
                ])
            blocks.append({
                'type': 'table',
                'head': ['Kompetenz', 'Ø', 'Einträge', 'Verlauf'],
                'rows': zeilen,
            })

            # Die Kommentare als Fliesstext darunter: daraus entsteht der Zeugnissatz.
            for kompetenz in bereich['kompetenzen']:
                if not kompetenz['kommentare']:
                    continue
                blocks.append({
                    'type': 'paragraph', 'style': 'Klein',
                    'text': f"Notizen zu „{kompetenz['item'].text}“",
                })
                for kommentar in kompetenz['kommentare']:
                    datum = kommentar['datum'].strftime('%d.%m.') if kommentar['datum'] else ''
                    zusatz = f" · {kommentar['anlass']}" if kommentar['anlass'] else ''
                    blocks.append({
                        'type': 'paragraph',
                        'text': f"{datum} [{kommentar['symbol']}]{zusatz}: {kommentar['text']}",
                    })

    if material['foerderplaene']:
        blocks.append({'type': 'heading', 'level': 1, 'text': 'Förderziele dieses Schuljahres'})
        for plan in material['foerderplaene']:
            titel = (plan.titel or 'Förderplan').strip()
            if plan.datum_erstellung:
                titel += f' ({plan.datum_erstellung:%d.%m.%Y})'
            blocks.append({'type': 'heading', 'level': 2, 'text': titel})
            zeilen = []
            for inhalt in plan.inhalte:
                zeilen.append([
                    (inhalt.foerderziel or '–').strip(),
                    (inhalt.evaluation_text or '–').strip(),
                ])
            if zeilen:
                blocks.append({
                    'type': 'table',
                    'head': ['Förderziel', 'Evaluation'],
                    'rows': zeilen,
                })

    return blocks


def filename_stem(schueler, erzeugt_am_datum):
    teile = [(schueler.nachname or '').strip(), (schueler.vorname or '').strip()]
    name = '_'.join(teil for teil in teile if teil) or f'Kind_{schueler.id}'
    sauber = ''.join(z if z.isalnum() or z in '-_' else '_' for z in name)
    return f'Zeugnismaterial_{sauber}_{erzeugt_am_datum:%Y-%m-%d}'


def class_filename_stem(klasse, erzeugt_am_datum):
    sauber = ''.join(z if z.isalnum() or z in '-_' else '_' for z in (klasse or 'Klasse'))
    return f'Zeugnismaterial_Klasse_{sauber}_{erzeugt_am_datum:%Y-%m-%d}'


def students_in_class(klasse):
    return (
        Schueler.query
        .filter(Schueler.klasse == klasse, Schueler.is_active.is_(True))
        .order_by(Schueler.nachname.asc(), Schueler.vorname.asc())
        .all()
    )
