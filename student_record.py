"""Die vollständige Schülerakte als ein Dokument.

Exporte gab es bisher einzeln: Förderplan, Fördergrundlage, Arbeitsplan,
Gesprächsprotokoll. Was fehlte, war alles zu einem Kind in einem Dokument.
Gebraucht wird das an zwei Stellen: beim Schulwechsel braucht die aufnehmende
Schule die Förderhistorie, und bei einer Auskunft nach Art. 15 DSGVO muss
herausgegeben werden, was gespeichert ist.

Deshalb ohne Begrenzung: die Schülerakte in der Oberfläche zeigt die letzten
zwölf Beobachtungen und Elternkontakte, hier steht alles. Und ohne
Schuljahresfilter - die Auskunft umfasst den gesamten Bestand, nicht das
laufende Schuljahr.

Nicht enthalten ist das Änderungsprotokoll der Ereignisse. Es hält fest, welche
Lehrkraft wann etwas bearbeitet hat, und ist damit Verfahrensdokumentation
über die Beschäftigten, nicht Inhalt über das Kind.
"""

from extensions import db
from models import (
    Beobachtung,
    Bogen,
    Elternberatung,
    Elternkontakt,
    ErziehungsEreignis,
    Foerderplan,
    Item,
    WorkPlan,
)

# Die Skala der Anwendung: 1 bis 4, angezeigt als Symbol.
WERT_SYMBOLE = {1: '-', 2: 'o', 3: '+', 4: '++'}

FOERDERGRUNDLAGE_FELDER = (
    ('besondere_staerken', 'Besondere Stärken'),
    ('vorrangiger_foerderbedarf', 'Vorrangiger Förderbedarf'),
    ('besonderheiten_entwicklung', 'Besonderheiten der Entwicklung'),
    ('wichtige_informationen', 'Wichtige Informationen'),
    ('absprachen_mit_eltern', 'Absprachen mit den Eltern'),
)

FOERDERINHALT_FELDER = (
    ('foerderziel', 'Förderziel'),
    ('ist_zustand', 'Ist-Zustand'),
    ('soll_zustand', 'Soll-Zustand'),
    ('massnahmen', 'Maßnahmen'),
    ('evaluation_text', 'Evaluation'),
)

KONTAKT_FELDER = (
    ('kontaktform', 'Kontaktform'),
    ('teilnehmende', 'Teilnehmende'),
    ('gespraechsanlass', 'Anlass'),
    ('mitteilung', 'Mitteilung'),
    ('besprochenes', 'Besprochenes'),
    ('vereinbarungen_schule', 'Vereinbarungen Schule'),
    ('vereinbarungen_eltern', 'Vereinbarungen Eltern'),
    ('naechste_schritte', 'Nächste Schritte'),
)

EREIGNIS_FELDER = (
    ('beschreibung', 'Beschreibung'),
    ('child_statement', 'Aussage des Kindes'),
    ('others_statement', 'Aussagen weiterer Beteiligter'),
    ('consequence_notes', 'Anmerkungen zur Konsequenz'),
)


def _datum(wert):
    return wert.strftime('%d.%m.%Y') if wert else '–'


def _datum_zeit(wert):
    return wert.strftime('%d.%m.%Y %H:%M') if wert else '–'


def _wert(beobachtung):
    if beobachtung.wert is None:
        return '–'
    symbol = WERT_SYMBOLE.get(beobachtung.wert, '?')
    return f'{symbol} ({beobachtung.wert})'


def _text(wert):
    wert = (wert or '').strip()
    return wert or '–'


def _gefuellte_felder(objekt, felder):
    """Nur die Felder, die tatsaechlich etwas enthalten."""
    return [
        (bezeichnung, (getattr(objekt, name) or '').strip())
        for name, bezeichnung in felder
        if (getattr(objekt, name) or '').strip()
    ]


def collect_record(schueler):
    """Traegt alles zusammen, was zu einem Kind gespeichert ist."""
    beobachtungen = (
        db.session.query(Beobachtung, Item, Bogen)
        .join(Item, Beobachtung.item_id == Item.id)
        .join(Bogen, Item.bogen_id == Bogen.id)
        .filter(Beobachtung.schueler_id == schueler.id)
        .order_by(Bogen.titel.asc(), Item.bereich.asc(), Beobachtung.datum.asc())
        .all()
    )

    # Nach Bogen buendeln, Reihenfolge der Abfrage beibehalten.
    boegen = []
    for beobachtung, item, bogen in beobachtungen:
        if not boegen or boegen[-1]['titel'] != bogen.titel:
            boegen.append({'titel': bogen.titel, 'zeilen': []})
        boegen[-1]['zeilen'].append({
            'datum': _datum(beobachtung.datum),
            'bereich': _text(item.bereich),
            'kompetenz': _text(item.text),
            'wert': _wert(beobachtung),
            'kommentar': _text(beobachtung.kommentar),
            'anlass': (beobachtung.anlass or '').strip(),
            'hat_foto': bool(beobachtung.foto_pfad),
        })

    return {
        'schueler': schueler,
        'grundlage': schueler.foerdergrundlage,
        'boegen': boegen,
        'beobachtungen_anzahl': len(beobachtungen),
        'foerderplaene': (
            Foerderplan.query
            .filter(Foerderplan.schueler_id == schueler.id)
            .order_by(Foerderplan.datum_erstellung.asc(), Foerderplan.id.asc())
            .all()
        ),
        'arbeitsplaene': (
            WorkPlan.query
            .filter(WorkPlan.student_id == schueler.id)
            .order_by(WorkPlan.period_start.asc(), WorkPlan.created_at.asc())
            .all()
        ),
        'elternkontakte': (
            Elternkontakt.query
            .filter(Elternkontakt.schueler_id == schueler.id)
            .order_by(Elternkontakt.datum.asc())
            .all()
        ),
        'beratungen': (
            Elternberatung.query
            .filter(Elternberatung.schueler_id == schueler.id)
            .order_by(Elternberatung.datum.asc())
            .all()
        ),
        'ereignisse': (
            ErziehungsEreignis.query
            .filter(ErziehungsEreignis.student_id == schueler.id)
            .order_by(ErziehungsEreignis.datum.asc(), ErziehungsEreignis.id.asc())
            .all()
        ),
    }


def _block_grunddaten(record, erzeugt_am):
    schueler = record['schueler']
    name = f"{schueler.nachname}, {schueler.vorname}".strip(', ')
    zeilen = [
        ('Name', f'{_text(schueler.vorname)} {_text(schueler.nachname)}'),
        ('Klasse', _text(schueler.klasse)),
        ('Geburtsdatum', _datum(schueler.geburtsdatum)),
    ]
    if schueler.is_active:
        zeilen.append(('Status', 'aktiv'))
    else:
        zeilen.append(('Status', f'archiviert am {_datum(schueler.archived_at)}'))

    return [
        {'type': 'paragraph', 'style': 'Titel', 'text': f'Schülerakte: {name}'},
        {'type': 'paragraph', 'style': 'Klein',
         'text': f'Vollständiger Auszug aller gespeicherten Daten · erzeugt am {erzeugt_am}'},
        {'type': 'heading', 'level': 1, 'text': 'Grunddaten'},
        {'type': 'fields', 'rows': zeilen},
    ]


def _block_grundlage(record):
    grundlage = record['grundlage']
    if not grundlage:
        return []

    zeilen = _gefuellte_felder(grundlage, FOERDERGRUNDLAGE_FELDER)
    if not zeilen:
        return []

    return [
        {'type': 'heading', 'level': 1, 'text': 'Fördergrundlage'},
        {'type': 'paragraph', 'style': 'Klein',
         'text': f'Zuletzt aktualisiert am {_datum_zeit(grundlage.zuletzt_aktualisiert_am)}'},
        {'type': 'fields', 'rows': zeilen},
    ]


def _block_beobachtungen(record):
    if not record['boegen']:
        return [
            {'type': 'heading', 'level': 1, 'text': 'Beobachtungen'},
            {'type': 'paragraph', 'text': 'Keine Beobachtungen erfasst.'},
        ]

    blocks = [
        {'type': 'heading', 'level': 1, 'text': 'Beobachtungen'},
        {'type': 'paragraph', 'style': 'Klein',
         'text': f"{record['beobachtungen_anzahl']} Einträge, Skala 1 bis 4 "
                 '(- reicht noch nicht, o teilweise, + gut, ++ sehr gut)'},
    ]
    for bogen in record['boegen']:
        zeilen = []
        for zeile in bogen['zeilen']:
            kommentar = zeile['kommentar']
            zusatz = []
            if zeile['anlass']:
                zusatz.append(f"Anlass: {zeile['anlass']}")
            if zeile['hat_foto']:
                zusatz.append('Foto vorhanden')
            if zusatz:
                kommentar = f"{kommentar}\n({'; '.join(zusatz)})" if kommentar != '–' \
                    else f"({'; '.join(zusatz)})"
            zeilen.append([
                zeile['datum'], zeile['bereich'], zeile['kompetenz'], zeile['wert'], kommentar,
            ])
        blocks.append({'type': 'heading', 'level': 2, 'text': bogen['titel']})
        blocks.append({
            'type': 'table',
            'head': ['Datum', 'Bereich', 'Kompetenz', 'Wert', 'Kommentar'],
            'rows': zeilen,
        })
    return blocks


def _block_foerderplaene(record):
    plaene = record['foerderplaene']
    blocks = [{'type': 'heading', 'level': 1, 'text': 'Förderpläne'}]
    if not plaene:
        blocks.append({'type': 'paragraph', 'text': 'Keine Förderpläne angelegt.'})
        return blocks

    for plan in plaene:
        blocks.append({'type': 'heading', 'level': 2,
                       'text': f'{_text(plan.titel)} ({_datum(plan.datum_erstellung)})'})
        kopf = [
            ('Erstellt am', _datum(plan.datum_erstellung)),
            ('Status', _text(plan.status)),
        ]
        if plan.datum_evaluation:
            kopf.append(('Evaluiert am', _datum(plan.datum_evaluation)))
        if plan.creator:
            kopf.append(('Angelegt von', plan.creator.display_name))
        blocks.append({'type': 'fields', 'rows': kopf})

        for nummer, inhalt in enumerate(plan.inhalte, start=1):
            zeilen = _gefuellte_felder(inhalt, FOERDERINHALT_FELDER)
            if not zeilen:
                continue
            blocks.append({'type': 'paragraph', 'style': 'Klein', 'text': f'Förderbereich {nummer}'})
            blocks.append({'type': 'fields', 'rows': zeilen})
    return blocks


def _block_arbeitsplaene(record):
    plaene = record['arbeitsplaene']
    blocks = [{'type': 'heading', 'level': 1, 'text': 'Arbeitspläne'}]
    if not plaene:
        blocks.append({'type': 'paragraph', 'text': 'Keine Arbeitspläne angelegt.'})
        return blocks

    for plan in plaene:
        zeitraum = f'{_datum(plan.period_start)} bis {_datum(plan.period_end)}'
        blocks.append({'type': 'heading', 'level': 2, 'text': f'Arbeitsplan {zeitraum}'})
        kopf = [('Zeitraum', zeitraum), ('Status', _text(plan.status))]
        if plan.creator:
            kopf.append(('Angelegt von', plan.creator.display_name))
        if (plan.notes_for_child or '').strip():
            kopf.append(('Hinweis für das Kind', plan.notes_for_child.strip()))
        if (plan.notes_for_teacher or '').strip():
            kopf.append(('Hinweis für die Lehrkraft', plan.notes_for_teacher.strip()))
        blocks.append({'type': 'fields', 'rows': kopf})

        if plan.tasks:
            zeilen = []
            for aufgabe in sorted(plan.tasks, key=lambda a: (a.sort_order, a.created_at)):
                bewertung = '–'
                if aufgabe.evaluation:
                    bewertung = _text(aufgabe.evaluation.rating)
                    if (aufgabe.evaluation.comment or '').strip():
                        bewertung += f'\n{aufgabe.evaluation.comment.strip()}'
                zeilen.append([
                    _text(aufgabe.title),
                    _text(aufgabe.learning_area),
                    _text(aufgabe.instructions),
                    bewertung,
                ])
            blocks.append({
                'type': 'table',
                'head': ['Aufgabe', 'Lernbereich', 'Auftrag', 'Bewertung'],
                'rows': zeilen,
            })
    return blocks


def _block_elternkontakte(record):
    kontakte = record['elternkontakte']
    blocks = [{'type': 'heading', 'level': 1, 'text': 'Elternkontakte'}]
    if not kontakte:
        blocks.append({'type': 'paragraph', 'text': 'Keine Elternkontakte dokumentiert.'})
    for kontakt in kontakte:
        art = 'Gesprächsprotokoll' if kontakt.eintrag_typ == 'protokoll' else 'Notiz'
        betreff = _text(kontakt.betreff)
        blocks.append({'type': 'heading', 'level': 2,
                       'text': f'{_datum(kontakt.datum)} · {art}: {betreff}'})
        zeilen = _gefuellte_felder(kontakt, KONTAKT_FELDER)
        if kontakt.naechster_termin:
            zeilen.append(('Nächster Termin', _datum(kontakt.naechster_termin)))
        if kontakt.user:
            zeilen.append(('Dokumentiert von', kontakt.user.display_name))
        blocks.append({'type': 'fields', 'rows': zeilen or [('Inhalt', '–')]})

    beratungen = record['beratungen']
    if beratungen:
        blocks.append({'type': 'heading', 'level': 1, 'text': 'Elternberatungen'})
        for beratung in beratungen:
            blocks.append({'type': 'heading', 'level': 2, 'text': _datum(beratung.datum)})
            zeilen = _gefuellte_felder(beratung, (
                ('anlass', 'Anlass'),
                ('weitere_beratungspunkte', 'Weitere Beratungspunkte'),
                ('vereinbarungen', 'Vereinbarungen'),
            ))
            if beratung.user:
                zeilen.append(('Dokumentiert von', beratung.user.display_name))
            blocks.append({'type': 'fields', 'rows': zeilen or [('Inhalt', '–')]})
    return blocks


def _block_ereignisse(record):
    ereignisse = record['ereignisse']
    blocks = [{'type': 'heading', 'level': 1, 'text': 'Erzieherische Ereignisse'}]
    if not ereignisse:
        blocks.append({'type': 'paragraph', 'text': 'Keine Ereignisse dokumentiert.'})
        return blocks

    for ereignis in ereignisse:
        vorlage = ereignis.event_template.name if ereignis.event_template else '–'
        blocks.append({'type': 'heading', 'level': 2,
                       'text': f'{_datum(ereignis.datum)} · {vorlage}'})
        zeilen = [
            ('Ort', ereignis.ort.name if ereignis.ort else '–'),
            ('Status', _text(ereignis.status)),
        ]
        if ereignis.event_template and ereignis.event_template.category:
            zeilen.insert(0, ('Kategorie', ereignis.event_template.category.name))
        zeilen.extend(_gefuellte_felder(ereignis, EREIGNIS_FELDER))

        konsequenzen = [
            verknuepfung.consequence.name
            for verknuepfung in ereignis.selected_consequences
            if verknuepfung.consequence
        ]
        if konsequenzen:
            zeilen.append(('Konsequenzen', ', '.join(sorted(konsequenzen))))

        mitbetroffene = [
            f'{eintrag.student.vorname} {eintrag.student.nachname}'.strip()
            for eintrag in ereignis.affected_students
            if eintrag.student
        ]
        if mitbetroffene:
            zeilen.append(('Weitere betroffene Kinder', ', '.join(sorted(mitbetroffene))))

        if ereignis.assigned_user:
            zeilen.append(('Zuständig', ereignis.assigned_user.display_name))
        if ereignis.attachments:
            zeilen.append((
                'Anhänge',
                ', '.join(
                    anhang.original_name or anhang.file_path
                    for anhang in ereignis.attachments
                ),
            ))
        blocks.append({'type': 'fields', 'rows': zeilen})
    return blocks


def record_blocks(record, erzeugt_am):
    """Setzt die Abschnitte zu einem Dokument zusammen."""
    blocks = []
    blocks.extend(_block_grunddaten(record, erzeugt_am))
    blocks.extend(_block_grundlage(record))
    blocks.extend(_block_beobachtungen(record))
    blocks.extend(_block_foerderplaene(record))
    blocks.extend(_block_arbeitsplaene(record))
    blocks.extend(_block_elternkontakte(record))
    blocks.extend(_block_ereignisse(record))
    blocks.append({'type': 'heading', 'level': 1, 'text': 'Hinweise zu diesem Auszug'})
    blocks.append({
        'type': 'paragraph', 'style': 'Klein',
        'text': 'Der Auszug enthält alle zu diesem Kind gespeicherten Inhalte ohne '
                'Beschränkung auf ein Schuljahr. Hochgeladene Fotos und Anhänge sind '
                'als vorhanden vermerkt, aber nicht eingebettet; sie können auf '
                'Anforderung einzeln herausgegeben werden. Nicht enthalten ist das '
                'Änderungsprotokoll der Ereignisse, das festhält, welche Lehrkraft '
                'wann etwas bearbeitet hat.',
    })
    return blocks


def filename_stem(schueler, erzeugt_am_datum):
    teile = [
        (schueler.nachname or '').strip(),
        (schueler.vorname or '').strip(),
    ]
    name = '_'.join(teil for teil in teile if teil) or f'Kind_{schueler.id}'
    sauber = ''.join(
        zeichen if zeichen.isalnum() or zeichen in '-_' else '_' for zeichen in name
    )
    return f'Schuelerakte_{sauber}_{erzeugt_am_datum:%Y-%m-%d}'
