"""Jahrgänge von Klassen, Kindern und Beobachtungsbögen.

Jahrgang heisst hier Klassenstufe, 1 bis 4. Eine Klasse hat einen oder – wenn
jahrgangsübergreifend unterrichtet wird – mehrere Jahrgänge, ein Kind genau
einen.

Bisher gab es den Jahrgang nur indirekt: der Schuljahreswechsel las ihn aus der
Ziffer am Anfang des Klassennamens. Das bleibt für Namen wie "3a" die Regel,
denn dort ist der Name die Stufe - die Kinder wandern jedes Jahr von "3a" nach
"4a". Für solche Klassen ergibt sich der Jahrgang aus dem Namen und ist nicht
frei wählbar; sonst trüge "3a" womöglich Jahrgang 2, und niemand wüsste mehr,
wohin versetzt wird.

Frei benannte Klassen ("Füchse", "Blau") bekommen ihre Jahrgänge von Hand. Nur
sie können jahrgangsübergreifend sein.
"""

import re

from extensions import db
from models import Bogen, ClassTaskLibrary, Klasse, KlasseJahrgang, Schueler, UserKlassenzuordnung

JAHRGAENGE = (1, 2, 3, 4)
ABSCHLUSSJAHRGANG = max(JAHRGAENGE)

# Ziffer 1 bis 4, danach nur Buchstaben: "3a", "4b", "1". Nicht "1/2" - das ist
# erkennbar eine jahrgangsübergreifende Klasse und bekommt ihre Jahrgänge frei.
STUFENNAME = re.compile(r'^([1-4])([A-Za-zÄÖÜäöüß]*)$')


def grade_from_name(name):
    """Der Jahrgang, den ein Klassenname vorgibt, oder None bei freien Namen."""
    treffer = STUFENNAME.match((name or '').strip())
    return int(treffer.group(1)) if treffer else None


def name_encodes_grade(name):
    return grade_from_name(name) is not None


def name_for_grade(name, jahrgang):
    """Der Klassenname für einen anderen Jahrgang: "3a" wird mit 4 zu "4a".

    Nur für Namen, die ihre Stufe tragen; bei freien Namen None.
    """
    treffer = STUFENNAME.match((name or '').strip())
    if not treffer:
        return None
    return f'{jahrgang}{treffer.group(2)}'


def normalize_jahrgaenge(werte):
    """Bereinigt eine Eingabe zu einer sortierten Menge gültiger Jahrgänge."""
    ergebnis = set()
    for wert in werte or []:
        try:
            zahl = int(wert)
        except (TypeError, ValueError):
            continue
        if zahl in JAHRGAENGE:
            ergebnis.add(zahl)
    return sorted(ergebnis)


# ---------------------------------------------------------------------------
# Klassen
# ---------------------------------------------------------------------------

def get_klasse(name):
    name = (name or '').strip()
    if not name:
        return None
    return Klasse.query.filter_by(name=name).first()


def ensure_klasse(name):
    """Liefert die Klasse zu einem Namen und legt sie bei Bedarf an.

    Eine neu angelegte Klasse mit Stufennamen bekommt ihren Jahrgang gleich mit.
    Committet nicht - das bleibt dem Aufrufer.
    """
    name = (name or '').strip()
    if not name:
        return None

    klasse = get_klasse(name)
    if klasse:
        return klasse

    klasse = Klasse(name=name)
    db.session.add(klasse)
    db.session.flush()
    stufe = grade_from_name(name)
    if stufe:
        db.session.add(KlasseJahrgang(klasse_id=klasse.id, jahrgang=stufe))
        db.session.flush()
    return klasse


def set_klassen_jahrgaenge(klasse, jahrgaenge):
    """Setzt die Jahrgänge einer frei benannten Klasse.

    Klassen mit Stufennamen behalten den Jahrgang aus ihrem Namen; ein Versuch,
    ihn zu ändern, wird mit ValueError abgewiesen.
    """
    neue = normalize_jahrgaenge(jahrgaenge)
    stufe = grade_from_name(klasse.name)
    if stufe is not None:
        if neue and neue != [stufe]:
            raise ValueError(
                f'Die Klasse „{klasse.name}" trägt ihren Jahrgang im Namen ({stufe}). '
                'Für abweichende oder mehrere Jahrgänge bitte einen freien Namen wählen.'
            )
        neue = [stufe]

    klasse.jahrgang_zuordnungen = [
        KlasseJahrgang(jahrgang=jahrgang) for jahrgang in neue
    ]
    db.session.flush()
    return neue


def klassen_jahrgaenge(name):
    klasse = get_klasse(name)
    if klasse:
        return klasse.jahrgaenge
    stufe = grade_from_name(name)
    return [stufe] if stufe else []


def single_jahrgang(name):
    """Der Jahrgang einer Klasse, wenn sie genau einen hat, sonst None."""
    stufen = klassen_jahrgaenge(name)
    return stufen[0] if len(stufen) == 1 else None


def referenced_class_names():
    """Alle Klassennamen, auf die irgendwo verwiesen wird."""
    namen = set()
    for (name,) in db.session.query(Schueler.klasse).distinct():
        namen.add(name)
    for (name,) in db.session.query(UserKlassenzuordnung.klasse).distinct():
        namen.add(name)
    for (name,) in db.session.query(ClassTaskLibrary.class_name).distinct():
        namen.add(name)
    return sorted(
        {(name or '').strip() for name in namen} - {''},
        key=lambda wert: wert.lower(),
    )


def sync_klassen():
    """Legt für jeden verwendeten Klassennamen eine Klasse an. Idempotent."""
    angelegt = []
    for name in referenced_class_names():
        if not get_klasse(name):
            ensure_klasse(name)
            angelegt.append(name)
    return angelegt


# ---------------------------------------------------------------------------
# Kinder
# ---------------------------------------------------------------------------

def resolve_student_jahrgang(klassen_name, gewuenscht=None):
    """Bestimmt den Jahrgang eines Kindes zu seiner Klasse.

    Hat die Klasse genau einen Jahrgang, ist er gesetzt - eine abweichende
    Eingabe wird ignoriert. Hat sie mehrere, muss die Eingabe einer davon sein.
    Hat sie keinen, gilt die Eingabe, sofern sie ein gültiger Jahrgang ist.

    Gibt (jahrgang, fehlermeldung) zurück.
    """
    stufen = klassen_jahrgaenge(klassen_name)
    gewuenscht = normalize_jahrgaenge([gewuenscht])
    gewuenscht = gewuenscht[0] if gewuenscht else None

    if len(stufen) == 1:
        return stufen[0], None
    if len(stufen) > 1:
        if gewuenscht in stufen:
            return gewuenscht, None
        return None, (
            f'Die Klasse „{klassen_name}" ist jahrgangsübergreifend '
            f'({", ".join(str(s) for s in stufen)}). Bitte einen dieser Jahrgänge wählen.'
        )
    return gewuenscht, None


def backfill_student_jahrgaenge():
    """Setzt fehlende Jahrgänge von Kindern aus ihrer Klasse. Idempotent.

    Überschreibt nie einen gesetzten Wert.
    """
    gesetzt = 0
    for schueler in Schueler.query.filter(Schueler.jahrgang.is_(None)).all():
        stufe = single_jahrgang(schueler.klasse)
        if stufe:
            schueler.jahrgang = stufe
            gesetzt += 1
    return gesetzt


# ---------------------------------------------------------------------------
# Bögen
# ---------------------------------------------------------------------------

def boegen_fuer_jahrgang(jahrgang, boegen=None):
    """Die Bögen, die für ein Kind dieses Jahrgangs angeboten werden.

    Ohne bekannten Jahrgang werden alle angeboten - lieber ein Bogen zu viel in
    der Auswahl als eine Beobachtung, die sich nicht erfassen lässt.
    """
    boegen = boegen if boegen is not None else Bogen.query.order_by(Bogen.titel.asc()).all()
    if jahrgang is None:
        return list(boegen)
    return [bogen for bogen in boegen if bogen.gilt_fuer(jahrgang)]
