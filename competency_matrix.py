"""Klassenuebersicht: Kinder mal Kompetenzen in einer Tabelle.

Die uebrigen Auswertungen der Anwendung sehen ein Kind an. Unterricht wird
aber fuer eine Klasse geplant, und dafuer fehlte die Gegenrichtung: Wo steht
meine 3a bei dieser Kompetenz? Wer hat hier noch keine Beobachtung?

Die Werte entsprechen dem, was der Einzelbericht zeigt - Mittelwert der
Beobachtungen im laufenden Schuljahr. Aeltere Eintraege bleiben unberuecksichtigt,
sonst vermischte die Uebersicht den Stand von heute mit dem von vorletztem Jahr.
"""

from sqlalchemy import func

from extensions import db
from models import Beobachtung, Item, Schueler
from school_year import observation_period_start


# Dieselben Schwellen wie im Einzelbericht (templates/report_view.html), damit
# eine Kompetenz nicht in zwei Ansichten unterschiedlich einsortiert wird.
LEVELS = (
    (3.5, 'stark'),
    (2.5, 'sicher'),
    (1.5, 'teils'),
)
LEVEL_FALLBACK = 'schwach'

LEVEL_LABELS = {
    'stark': 'sicher beherrscht',
    'sicher': 'überwiegend sicher',
    'teils': 'teilweise',
    'schwach': 'reicht noch nicht',
}


def competency_level(average):
    """Ordnet einen Mittelwert einer der vier Stufen zu."""
    if average is None:
        return None
    for schwelle, name in LEVELS:
        if average >= schwelle:
            return name
    return LEVEL_FALLBACK


def _summary(total_sum, total_count):
    if not total_count:
        return {'average': None, 'count': 0, 'level': None}
    average = round(total_sum / total_count, 1)
    return {'average': average, 'count': total_count, 'level': competency_level(average)}


def classes_with_students(include_archived=False):
    """Klassen, in denen Kinder eingetragen sind.

    student_selection.get_distinct_klassen() beruecksichtigt nur aktive Kinder.
    Fuer die Klassenuebersicht ist das zu eng: eine Klasse, deren Kinder alle
    archiviert sind, waere sonst nicht mehr auswaehlbar - also gerade der Fall,
    fuer den der Archiv-Schalter gedacht ist.
    """
    query = Schueler.query.with_entities(Schueler.klasse).filter(Schueler.klasse.isnot(None))
    if not include_archived:
        query = query.filter(Schueler.is_active.is_(True))
    klassen = {(row[0] or '').strip() for row in query.distinct().all()}
    klassen.discard('')
    return sorted(klassen, key=lambda wert: wert.lower())


def students_in_class(klasse, include_archived=False):
    query = Schueler.query.filter(Schueler.klasse == klasse)
    if not include_archived:
        query = query.filter(Schueler.is_active.is_(True))
    return query.order_by(Schueler.nachname.asc(), Schueler.vorname.asc()).all()


def most_documented_bogen(klasse, boegen, include_archived=False):
    """Der Bogen, zu dem fuer diese Klasse am meisten erfasst ist.

    Als Voreinstellung besser als der alphabetisch erste: eine Lehrkraft will
    den Bogen sehen, an dem sie arbeitet, nicht den mit dem fruehsten Titel.
    Ohne jede Beobachtung bleibt es beim ersten.
    """
    if not boegen:
        return None

    students = students_in_class(klasse, include_archived=include_archived)
    if not students:
        return boegen[0]

    query = (
        db.session.query(Item.bogen_id, func.count(Beobachtung.id))
        .join(Beobachtung, Beobachtung.item_id == Item.id)
        .filter(Beobachtung.schueler_id.in_([student.id for student in students]))
    )
    grenze = observation_period_start()
    if grenze:
        query = query.filter(Beobachtung.datum >= grenze)

    anzahl_je_bogen = dict(query.group_by(Item.bogen_id).all())
    if not anzahl_je_bogen:
        return boegen[0]

    return max(boegen, key=lambda bogen: anzahl_je_bogen.get(bogen.id, 0))


def build_matrix(klasse, bogen, include_archived=False):
    """Baut die Matrix fuer eine Klasse und einen Beobachtungsbogen.

    Eine Abfrage fuer alle Zellen. Die Mittelwerte werden aus Summe und Anzahl
    gebildet, damit Zeilen- und Spaltenmittel exakt sind und nicht als Mittel
    von Mittelwerten verzerren.
    """
    students = students_in_class(klasse, include_archived=include_archived)
    items = (
        Item.query
        .filter(Item.bogen_id == bogen.id)
        .order_by(Item.bereich.asc(), Item.id.asc())
        .all()
    )

    cells = {}
    per_student = {student.id: [0, 0] for student in students}
    per_item = {item.id: [0, 0] for item in items}
    gesamt = [0, 0]

    if students and items:
        query = (
            db.session.query(
                Beobachtung.schueler_id,
                Beobachtung.item_id,
                func.sum(Beobachtung.wert),
                func.count(Beobachtung.id),
            )
            .filter(
                Beobachtung.schueler_id.in_([student.id for student in students]),
                Beobachtung.item_id.in_([item.id for item in items]),
                Beobachtung.wert.isnot(None),
            )
        )
        grenze = observation_period_start()
        if grenze:
            query = query.filter(Beobachtung.datum >= grenze)

        for schueler_id, item_id, summe, anzahl in query.group_by(
            Beobachtung.schueler_id, Beobachtung.item_id,
        ).all():
            if not anzahl:
                continue
            summe = float(summe or 0)
            average = round(summe / anzahl, 1)
            cells[(schueler_id, item_id)] = {
                'average': average,
                'count': anzahl,
                'level': competency_level(average),
            }
            per_student[schueler_id][0] += summe
            per_student[schueler_id][1] += anzahl
            per_item[item_id][0] += summe
            per_item[item_id][1] += anzahl
            gesamt[0] += summe
            gesamt[1] += anzahl

    # Bereiche in Spaltengruppen buendeln, Reihenfolge wie bei den Items.
    bereiche = []
    for item in items:
        name = (item.bereich or '').strip() or 'Ohne Bereich'
        if not bereiche or bereiche[-1]['name'] != name:
            # Schluessel absichtlich nicht 'items': in Jinja loest .items auf die
            # Dict-Methode auf, nicht auf den Schluessel.
            bereiche.append({'name': name, 'competencies': []})
        bereiche[-1]['competencies'].append(item)

    student_totals = {}
    for student in students:
        summe, anzahl = per_student[student.id]
        eintrag = _summary(summe, anzahl)
        eintrag['gaps'] = sum(
            1 for item in items if (student.id, item.id) not in cells
        )
        student_totals[student.id] = eintrag

    item_totals = {}
    for item in items:
        summe, anzahl = per_item[item.id]
        eintrag = _summary(summe, anzahl)
        eintrag['gaps'] = sum(
            1 for student in students if (student.id, item.id) not in cells
        )
        item_totals[item.id] = eintrag

    zellen_gesamt = len(students) * len(items)
    uebersicht = _summary(*gesamt)
    uebersicht['cells_filled'] = len(cells)
    uebersicht['cells_total'] = zellen_gesamt
    uebersicht['coverage'] = (
        round(100 * len(cells) / zellen_gesamt) if zellen_gesamt else 0
    )

    return {
        'klasse': klasse,
        'bogen': bogen,
        'students': students,
        # 'competencies' statt 'items': in Jinja wuerde matrix.items die
        # Dict-Methode liefern und nicht die Kompetenzliste.
        'competencies': items,
        'bereiche': bereiche,
        'cells': cells,
        'student_totals': student_totals,
        'item_totals': item_totals,
        'summary': uebersicht,
    }


def weakest_items(matrix, limit=3):
    """Die Kompetenzen mit dem niedrigsten Klassenmittel.

    Beantwortet die Frage, die eine Klassenuebersicht ueberhaupt erst nuetzlich
    macht: Woran muss die naechste Stunde ansetzen? Kompetenzen ohne
    Beobachtungen bleiben aussen vor - sie sind keine Schwaeche, sondern eine
    Luecke in der Dokumentation.
    """
    bewertet = [
        (item, matrix['item_totals'][item.id])
        for item in matrix['competencies']
        if matrix['item_totals'][item.id]['count']
    ]
    bewertet.sort(key=lambda paar: paar[1]['average'])
    return bewertet[:limit]


def students_needing_attention(matrix, limit=3):
    """Kinder mit dem niedrigsten Mittel ueber den gesamten Bogen."""
    bewertet = [
        (student, matrix['student_totals'][student.id])
        for student in matrix['students']
        if matrix['student_totals'][student.id]['count']
    ]
    bewertet.sort(key=lambda paar: paar[1]['average'])
    return bewertet[:limit]
