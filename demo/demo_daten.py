#!/usr/bin/env python3
"""Demodaten für den KompetenzKompass.

Baut eine vollständig gefüllte SQLite-Datenbank mit frei erfundenen Daten:
Kinder, Kompetenzbögen, Beobachtungen, Diagnostik, Förderpläne, Förderkurse,
Nachteilsausgleich, Elternkontakte, Ereignisse, eine Förderkonferenz und
Hospitationen. Gedacht für Schulungen, Screenshots und Videos - damit nie mit
echten Schuldaten vorgeführt werden muss.

    python3 demo/demo_daten.py --db instance/demo.db --neu

Die Daten sind deterministisch (fester Zufallsstartwert): derselbe Aufruf
liefert dieselbe Datenbank, eine Aufnahme lässt sich also wiederholen. Die
Zeitachse richtet sich nach dem heutigen Tag, damit die App "aktuell" wirkt.

Alle Konten haben das Passwort "demo".
"""

import argparse
import random
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from werkzeug.security import generate_password_hash  # noqa: E402

PASSWORT = 'demo'
ZUFALL = random.Random(2026)

# ----------------------------------------------------------------------
# Personen
# ----------------------------------------------------------------------

# (Benutzername, Vorname, Nachname, Rolle, Klassenleitung, Fachklassen)
BENUTZER = [
    ('admin', 'Martina', 'Adler', 'admin', None, []),
    ('wagner', 'Sabine', 'Wagner', 'schulleitung', None, []),
    ('klein', 'Thomas', 'Klein', 'foerderpaedagogik', None, []),
    ('sommer', 'Klara', 'Sommer', 'teacher', '3a', ['3b']),
    ('weber', 'Jonas', 'Weber', 'teacher', '3b', ['3a']),
    ('lang', 'Mira', 'Lang', 'teacher', '4a', ['3a', '3b']),
    ('hoffmann', 'Paul', 'Hoffmann', 'teacher', '2a', ['4a']),
    ('kern', 'Lisa', 'Kern', 'teacher', '1a', []),
]

# Die Klasse 3a ist die "Videoklasse": hier ist alles ausführlich gefüllt.
# (Vorname, Nachname, Niveau, Merkmale)
KLASSE_3A = [
    ('Mia', 'Berger', 'stark', {'stern'}),
    ('Ben', 'Kraus', 'schwach', {'plan', 'lesekurs', 'nta', 'konferenz_c', 'diagnostik_schwach'}),
    ('Leon', 'Sander', 'mittel', {'konferenz_b', 'diagnostik_mittel'}),
    ('Emilia', 'Voss', 'stark', {'stern', 'arbeitsplan'}),
    ('Noah', 'Brandt', 'schwach', {'plan', 'rechenkurs', 'konferenz_b', 'ereignis'}),
    ('Lina', 'Schuster', 'mittel', {'elterngespraech'}),
    ('Jonas', 'Peters', 'schwach', {'lesekurs', 'konferenz_c', 'diagnostik_schwach'}),
    ('Sophie', 'Hartmann', 'mittel', {'plan', 'nta'}),
    ('Luis', 'Fischer', 'stark', set()),
    ('Ida', 'Krüger', 'mittel', {'ereignis'}),
]

VORNAMEN = [
    'Amelie', 'Anton', 'Carla', 'David', 'Elias', 'Ella', 'Emil', 'Fiona', 'Greta', 'Hannes',
    'Hanna', 'Henri', 'Isabel', 'Jakob', 'Johanna', 'Julian', 'Karl', 'Lena', 'Levi', 'Lotta',
    'Malte', 'Marie', 'Mats', 'Nele', 'Nils', 'Oskar', 'Paula', 'Pepe', 'Romy', 'Samuel',
    'Selma', 'Theo', 'Tilda', 'Valentin', 'Vera', 'Yannik', 'Zoe', 'Arne', 'Bela', 'Clara',
]
NACHNAMEN = [
    'Ahrens', 'Baumann', 'Clausen', 'Dietz', 'Ebert', 'Falk', 'Gruber', 'Haas', 'Iversen',
    'Jansen', 'Kaiser', 'Lorenz', 'Möller', 'Neumann', 'Ortmann', 'Prinz', 'Quast', 'Reinke',
    'Stein', 'Thiel', 'Ullrich', 'Vogt', 'Walter', 'Zimmer', 'Bauer', 'Dorn', 'Engel', 'Fuchs',
    'Gerber', 'Hansen', 'Jung', 'Kuhn', 'Linde', 'Marx', 'Nolte', 'Pohl', 'Rieger', 'Sauer',
]

NIVEAUS = ('stark', 'mittel', 'schwach')
# Gewichte für die Werte 1 bis 4 je Niveau.
WERT_GEWICHTE = {
    'stark': (1, 2, 6, 5),
    'mittel': (2, 5, 6, 2),
    'schwach': (6, 5, 2, 1),
}

# ----------------------------------------------------------------------
# Kompetenzbögen
# ----------------------------------------------------------------------

# (Titel, Jahrgänge, Fach, Pflicht, Förderempfehlung, übergreifend, {Bereich: [Items]})
BOEGEN = [
    ('Deutsch 3/4', [3, 4], 'Deutsch', True, True, False, {
        'Lesen': ['liest altersgemäße Texte flüssig vor',
                  'entnimmt einem Text gezielt Informationen',
                  'gibt den Inhalt eines Textes wieder',
                  'liest in der Freiarbeit selbstständig'],
        'Rechtschreiben': ['schreibt lautgetreue Wörter richtig',
                           'wendet die Groß- und Kleinschreibung an',
                           'nutzt das Wörterbuch zur Kontrolle',
                           'verlängert Wörter zur Ableitung'],
        'Texte schreiben': ['schreibt zu einem Bild eine zusammenhängende Geschichte',
                            'gliedert einen Text in Einleitung, Hauptteil, Schluss',
                            'überarbeitet einen eigenen Text nach Hinweisen'],
        'Sprache untersuchen': ['erkennt Wortarten (Nomen, Verb, Adjektiv)',
                                'bildet Sätze in der richtigen Zeitform'],
    }),
    ('Mathematik 3/4', [3, 4], 'Mathematik', True, True, False, {
        'Zahlen und Operationen': ['rechnet im Zahlenraum bis 1000 sicher',
                                   'beherrscht das kleine Einmaleins',
                                   'nutzt halbschriftliche Rechenwege',
                                   'rechnet schriftlich mit Übertrag'],
        'Größen und Messen': ['schätzt und misst Längen',
                              'rechnet mit Geldbeträgen',
                              'liest die Uhr und rechnet mit Zeitspannen'],
        'Raum und Form': ['erkennt und benennt geometrische Körper',
                          'zeichnet mit Lineal und Geodreieck sauber'],
        'Sachaufgaben': ['entnimmt einer Sachaufgabe die nötigen Angaben',
                         'erklärt den eigenen Rechenweg'],
    }),
    ('Arbeits- und Sozialverhalten', [1, 2, 3, 4], None, True, True, False, {
        'Arbeitsverhalten': ['arbeitet ausdauernd an einer Aufgabe',
                             'beginnt selbstständig mit der Arbeit',
                             'hält die Arbeitszeit ein',
                             'hat Material vollständig dabei'],
        'Sozialverhalten': ['hält vereinbarte Regeln ein',
                            'arbeitet mit anderen Kindern zusammen',
                            'löst Streit ohne Gewalt',
                            'hilft anderen Kindern'],
    }),
    ('Deutsch 1/2', [1, 2], 'Deutsch', True, True, False, {
        'Lesen': ['liest Silben und kurze Wörter',
                  'liest einen kurzen Text sinnentnehmend'],
        'Schreiben': ['schreibt lautgetreue Wörter',
                      'schreibt in Druckschrift formklar'],
        'Sprechen und Zuhören': ['erzählt verständlich von Erlebnissen',
                                 'hört anderen Kindern zu'],
    }),
    ('Mathematik 1/2', [1, 2], 'Mathematik', True, True, False, {
        'Zahlen und Operationen': ['zählt sicher bis 100',
                                   'rechnet im Zahlenraum bis 20 ohne Material',
                                   'zerlegt Zahlen flexibel'],
        'Raum und Form': ['legt und beschreibt Muster'],
    }),
    ('Übergang Klasse 5', [3, 4], None, False, False, True, {
        'Selbstständigkeit': ['organisiert das eigene Material',
                              'erledigt Hausaufgaben zuverlässig',
                              'arbeitet über längere Zeit allein'],
        'Lernentwicklung': ['findet eigene Lernwege',
                            'geht mit Fehlern konstruktiv um'],
        'Gespräch mit den Eltern': ['Einschätzung der Eltern liegt vor',
                                    'Empfehlung wurde besprochen'],
    }),
]

KOMMENTARE = {
    1: ['braucht noch viel Unterstützung', 'gelingt nur mit Hilfe', 'Material hilft, ohne geht es nicht',
        'bricht die Aufgabe oft ab', 'braucht eine Rückmeldung nach jedem Schritt'],
    2: ['wechselhaft, je nach Tagesform', 'gelingt mit Erinnerung', 'braucht noch Übung',
        'mit Partnerhilfe deutlich besser'],
    3: ['klappt zuverlässig', 'arbeitet ruhig und sicher', 'hat sich deutlich gesteigert',
        'kann den Weg erklären'],
    4: ['erklärt es anderen Kindern', 'löst auch schwierige Aufgaben', 'sehr sicher',
        'findet eigene Lösungswege'],
}
ANLAESSE = ['Freiarbeit', 'Lernzeit', 'Partnerarbeit', 'Test', 'Beobachtung im Unterricht', 'Präsentation']


def schuljahr_zu(jahr):
    return f'{jahr}/{jahr + 1}'


def schuljahr_start(heute):
    """Das Jahr, in dem das laufende Schuljahr begonnen hat."""
    return heute.year if heute.month >= 8 else heute.year - 1


# ----------------------------------------------------------------------
# Aufbau
# ----------------------------------------------------------------------

class Demo:
    """Sammelt beim Aufbau, was die späteren Schritte brauchen."""

    def __init__(self, heute):
        self.heute = heute
        self.start_jahr = schuljahr_start(heute)
        self.schuljahr = schuljahr_zu(self.start_jahr)
        self.voriges_schuljahr = schuljahr_zu(self.start_jahr - 1)
        self.schuljahr_beginn = date(self.start_jahr, 8, 12)
        self.user = {}          # Benutzername -> User
        self.kinder = {}        # "Vorname Nachname" -> Schueler
        self.merkmale = {}      # schueler_id -> Merkmale
        self.klassen_kinder = {}  # Klassenname -> [Schueler]
        self.items = {}         # (Bogentitel, Bereich, Text) -> Item
        self.boegen = {}        # Titel -> Bogen
        self.faecher = {}       # Name -> Fach
        self.kurse = {}         # Name -> Foerderkurs

    # -- kleine Helfer ------------------------------------------------

    def tage_her(self, tage, stunde=10):
        return datetime.combine(self.heute - timedelta(days=tage), time(stunde, 15))

    def im_schuljahr(self, anteil):
        """Ein Datum zwischen Schuljahresbeginn und heute (0.0 bis 1.0)."""
        spanne = (self.heute - self.schuljahr_beginn).days
        return self.schuljahr_beginn + timedelta(days=int(spanne * anteil))

    def kind(self, name):
        return self.kinder[name]

    def hat(self, kind, merkmal):
        return merkmal in self.merkmale.get(kind.id, set())

    def kinder_mit(self, merkmal):
        return [kind for kind in self.kinder.values() if self.hat(kind, merkmal)]


def _konfiguration(demo):
    from models import SystemKonfiguration
    from extensions import db

    db.session.add(SystemKonfiguration(
        schuljahr=demo.schuljahr,
        schuljahr_beginn=demo.schuljahr_beginn,
        elternsprechtag_1=demo.schuljahr_beginn + timedelta(days=80),
        elternsprechtag_2=demo.schuljahr_beginn + timedelta(days=250),
        workplan_suggestions_weeks=12,
        aufbewahrung_jahre=5,
    ))
    db.session.flush()


def _benutzer(demo):
    from extensions import db
    from models import User, UserKlassenzuordnung

    for benutzername, vorname, nachname, rolle, klassenleitung, fachklassen in BENUTZER:
        user = User(
            username=benutzername, vorname=vorname, nachname=nachname, role=rolle,
            password_hash=generate_password_hash(PASSWORT),
            email=f'{benutzername}@schule.example', mail_takt='taeglich',
        )
        db.session.add(user)
        db.session.flush()
        demo.user[benutzername] = user
        if klassenleitung:
            db.session.add(UserKlassenzuordnung(user_id=user.id, klasse=klassenleitung, rolle='klassenleitung'))
        for klasse in fachklassen:
            db.session.add(UserKlassenzuordnung(user_id=user.id, klasse=klasse, rolle='fach'))
    db.session.flush()


def _klassen_und_kinder(demo):
    from extensions import db
    from jahrgang import ensure_klasse, set_klassen_jahrgaenge
    from models import Schueler

    vornamen = list(VORNAMEN)
    nachnamen = list(NACHNAMEN)
    ZUFALL.shuffle(vornamen)
    ZUFALL.shuffle(nachnamen)

    plan = [('1a', 1, 8), ('2a', 2, 9), ('3a', 3, len(KLASSE_3A)), ('3b', 3, 9), ('4a', 4, 9)]
    for klassenname, jahrgang, _ in plan:
        klasse = ensure_klasse(klassenname)
        set_klassen_jahrgaenge(klasse, [jahrgang])
    db.session.flush()

    for klassenname, jahrgang, anzahl in plan:
        demo.klassen_kinder[klassenname] = []
        for nummer in range(anzahl):
            if klassenname == '3a':
                vorname, nachname, niveau, merkmale = KLASSE_3A[nummer]
            else:
                vorname, nachname = vornamen.pop(), nachnamen.pop()
                niveau = ZUFALL.choices(NIVEAUS, weights=(3, 5, 2))[0]
                merkmale = set()
                if niveau == 'schwach' and ZUFALL.random() < 0.6:
                    merkmale.add('plan')
                if ZUFALL.random() < 0.15:
                    merkmale.add('stern')
            geburtsjahr = demo.start_jahr - 5 - jahrgang
            kind = Schueler(
                vorname=vorname, nachname=nachname, klasse=klassenname, jahrgang=jahrgang,
                geburtsdatum=date(geburtsjahr, ZUFALL.randint(1, 12), ZUFALL.randint(1, 28)),
                is_active=True,
            )
            db.session.add(kind)
            db.session.flush()
            kind.niveau = niveau
            demo.kinder[f'{vorname} {nachname}'] = kind
            demo.merkmale[kind.id] = set(merkmale) | {f'niveau:{niveau}'}
            demo.klassen_kinder[klassenname].append(kind)
    db.session.flush()


def _niveau(demo, kind):
    for merkmal in demo.merkmale.get(kind.id, ()):
        if merkmal.startswith('niveau:'):
            return merkmal.split(':', 1)[1]
    return 'mittel'


def _boegen(demo):
    from extensions import db
    from models import Bogen, BogenJahrgang, Item

    for titel, jahrgaenge, fach, pflicht, empfehlung, uebergreifend, bereiche in BOEGEN:
        bogen = Bogen(
            titel=titel, pflicht=pflicht, foerderempfehlung=empfehlung,
            schuljahresuebergreifend=uebergreifend,
            fach_id=demo.faecher[fach].id if fach else None,
        )
        db.session.add(bogen)
        db.session.flush()
        for jahrgang in jahrgaenge:
            db.session.add(BogenJahrgang(bogen_id=bogen.id, jahrgang=jahrgang))
        for bereich, texte in bereiche.items():
            for text in texte:
                item = Item(bogen_id=bogen.id, bereich=bereich, text=text)
                db.session.add(item)
                db.session.flush()
                demo.items[(titel, bereich, text)] = item
        demo.boegen[titel] = bogen
    db.session.flush()


def _boegen_fuer(demo, kind):
    """Die Bögen, die für den Jahrgang des Kindes gelten."""
    passend = []
    for titel, jahrgaenge, *_ in BOEGEN:
        if kind.jahrgang in jahrgaenge:
            passend.append(demo.boegen[titel])
    return passend


def _beobachtungen(demo):
    from extensions import db
    from models import Beobachtung, Item

    lehrkraft_je_klasse = {
        klassenleitung: demo.user[benutzername]
        for benutzername, _, _, _, klassenleitung, _ in BENUTZER if klassenleitung
    }
    anzahl = 0
    for kind in demo.kinder.values():
        niveau = _niveau(demo, kind)
        gewichte = WERT_GEWICHTE[niveau]
        schwach_in = set()
        if demo.hat(kind, 'plan') or niveau == 'schwach':
            # Ein Bereich, in dem es durchgehend "-" gibt: daraus entstehen die
            # Vorschläge im Förderplan-Assistenten.
            schwach_in = {'Lesen', 'Rechtschreiben'} if kind.id % 2 else {'Zahlen und Operationen'}
        for bogen in _boegen_fuer(demo, kind):
            if bogen.titel == 'Übergang Klasse 5' and kind.jahrgang != 4:
                continue
            items = Item.query.filter_by(bogen_id=bogen.id).all()
            for item in items:
                if ZUFALL.random() < 0.25:
                    continue
                for nummer in range(ZUFALL.randint(1, 3)):
                    if item.bereich in schwach_in:
                        wert = 1 if ZUFALL.random() < 0.8 else 2
                    else:
                        wert = ZUFALL.choices((1, 2, 3, 4), weights=gewichte)[0]
                    anteil = ZUFALL.uniform(0.05, 0.98)
                    datum = datetime.combine(demo.im_schuljahr(anteil), time(ZUFALL.randint(8, 13), 30))
                    kommentar = ZUFALL.choice(KOMMENTARE[wert]) if ZUFALL.random() < 0.35 else None
                    db.session.add(Beobachtung(
                        schueler_id=kind.id, item_id=item.id, wert=wert, datum=datum,
                        kommentar=kommentar,
                        anlass=ZUFALL.choice(ANLAESSE) if ZUFALL.random() < 0.4 else None,
                    ))
                    anzahl += 1
    db.session.flush()
    return anzahl


# ----------------------------------------------------------------------
# Diagnostik
# ----------------------------------------------------------------------

def _prozentrang(niveau, streuung=8):
    mitte = {'stark': 72, 'mittel': 45, 'schwach': 14}[niveau]
    return max(1, min(99, int(ZUFALL.gauss(mitte, streuung))))


def _t_wert(prozentrang):
    """Grobe Umrechnung - für die Demo reicht der plausible Zusammenhang."""
    return max(25, min(75, int(40 + prozentrang / 5)))


def _diagnostik(demo):
    from diagnostik import lege_vorbelegung_an, schaerfe_vorbelegung_nach, speichere_ergebnis
    from extensions import db
    from models import DiagnostikTestform, DiagnostikZeitpunkt

    lege_vorbelegung_an()
    db.session.flush()
    schaerfe_vorbelegung_nach()
    db.session.flush()

    plan = {}
    for zeitpunkt in DiagnostikZeitpunkt.query.all():
        plan.setdefault(zeitpunkt.jahrgang, []).append((zeitpunkt.testform, zeitpunkt.halbjahr))

    erfasser = demo.user['klein'].id
    anzahl = 0
    for kind in demo.kinder.values():
        niveau = _niveau(demo, kind)
        if demo.hat(kind, 'diagnostik_schwach'):
            niveau = 'schwach'
        elif demo.hat(kind, 'diagnostik_mittel'):
            niveau = 'mittel'
        for jahrgang in range(1, (kind.jahrgang or 1) + 1):
            jahre_zurueck = kind.jahrgang - jahrgang
            beginn = demo.start_jahr - jahre_zurueck
            schuljahr = schuljahr_zu(beginn)
            for testform, halbjahr in plan.get(jahrgang, []):
                datum = date(beginn + 1, 2, 12) if halbjahr == 'mitte' else date(beginn + 1, 6, 16)
                if datum > demo.heute:
                    continue
                # Nicht jedes Kind hat jeden Test mitgeschrieben.
                if ZUFALL.random() < 0.12:
                    continue
                werte = {}
                for kennwert in testform.kennwerte:
                    pr = _prozentrang(niveau)
                    for art in kennwert.wertarten:
                        if art == 'rohwert':
                            werte[(kennwert.id, art)] = max(1, int(pr / 2) + ZUFALL.randint(8, 30))
                        elif art == 'prozentrang':
                            werte[(kennwert.id, art)] = pr
                        elif art == 't_wert':
                            werte[(kennwert.id, art)] = _t_wert(pr)
                        elif art == 'lesequotient':
                            werte[(kennwert.id, art)] = max(50, min(130, int(75 + pr / 2)))
                speichere_ergebnis(
                    kind, testform, schuljahr, halbjahr, werte, erfasser,
                    datum=datum, aktuelles_schuljahr=demo.schuljahr,
                )
                anzahl += 1
    db.session.flush()
    return anzahl


def _foerderangaben(demo):
    from extensions import db
    from models import Foerderangaben

    for kind in demo.kinder.values():
        if not (demo.hat(kind, 'plan') or demo.hat(kind, 'lesekurs') or demo.hat(kind, 'rechenkurs')):
            continue
        db.session.add(Foerderangaben(
            schueler_id=kind.id, schuljahr=demo.schuljahr,
            foerderkurs=demo.hat(kind, 'lesekurs') or demo.hat(kind, 'rechenkurs'),
            externe_foerderung=ZUFALL.random() < 0.3,
            foerderschwerpunkt=ZUFALL.choice([
                'Lesegenauigkeit und Lesetempo', 'Lautgetreues Schreiben',
                'Zahlverständnis im Hunderterraum', 'Ausdauer in der Freiarbeit',
            ]),
            updated_by_user_id=demo.user['sommer'].id,
        ))
    db.session.flush()


# ----------------------------------------------------------------------
# Förderung
# ----------------------------------------------------------------------

def _faecher_und_kurse(demo):
    from extensions import db
    from foerderkurs import trage_ein
    from models import Fach, Foerderkurs, FoerderkursJahrgang

    for position, name in enumerate(['Deutsch', 'Mathematik', 'Sachunterricht']):
        fach = Fach(name=name, sort_order=position)
        db.session.add(fach)
        db.session.flush()
        demo.faecher[name] = fach

    kurse = [
        ('Lesekurs 3/4', 'Deutsch', [3, 4], 'Mo 3. Stunde', 'lang'),
        ('Rechtschreibwerkstatt', 'Deutsch', [4], 'Do 5. Stunde', 'weber'),
        ('Rechenkurs 2/3', 'Mathematik', [2, 3], 'Di 4. Stunde', 'hoffmann'),
        ('Sprachförderung 1/2', 'Deutsch', [1, 2], 'Mi 2. Stunde', 'kern'),
    ]
    for name, fach, jahrgaenge, zeit, leitung in kurse:
        kurs = Foerderkurs(
            name=name, fach_id=demo.faecher[fach].id, schuljahr=demo.schuljahr,
            zeit=zeit, leitung_user_id=demo.user[leitung].id, is_active=True,
        )
        db.session.add(kurs)
        db.session.flush()
        for jahrgang in jahrgaenge:
            db.session.add(FoerderkursJahrgang(kurs_id=kurs.id, jahrgang=jahrgang))
        demo.kurse[name] = kurs
    db.session.flush()

    seit = demo.schuljahr_beginn + timedelta(days=21)
    for kind in demo.kinder.values():
        kurs = None
        if demo.hat(kind, 'lesekurs'):
            kurs = demo.kurse['Lesekurs 3/4']
        elif demo.hat(kind, 'rechenkurs'):
            kurs = demo.kurse['Rechenkurs 2/3']
        elif demo.hat(kind, 'plan') and kind.jahrgang in (1, 2) and ZUFALL.random() < 0.7:
            kurs = demo.kurse['Sprachförderung 1/2']
        elif demo.hat(kind, 'plan') and kind.jahrgang == 4 and ZUFALL.random() < 0.5:
            kurs = demo.kurse['Rechtschreibwerkstatt']
        if kurs is None:
            continue
        teilnahme = trage_ein(kurs, kind, demo.user['lang'])
        if teilnahme is not None:
            teilnahme.seit = seit
    db.session.flush()


FOERDERZIELE = {
    'Lesen': (
        'Deutsch 3/4 | Lesen | liest altersgemäße Texte flüssig vor',
        'Liest stockend, verliert bei längeren Sätzen die Zeile. Silbenweises Lesen gelingt.',
        'Liest einen geübten Text aus dem Lesebuch flüssig und sinngestaltend vor.',
        'Tägliches Lautlesetandem (10 Minuten), Lesepfeil, Texte in Silbenschrift, '
        'Lesekurs Deutsch am Montag.',
    ),
    'Rechtschreiben': (
        'Deutsch 3/4 | Rechtschreiben | schreibt lautgetreue Wörter richtig',
        'Schreibt lautgetreue Wörter häufig unvollständig, lässt Endungen weg.',
        'Schreibt die Wörter der Grundwortschatzliste 1 lautgetreu richtig.',
        'Arbeit mit der Anlauttabelle, Silbenbögen, wöchentlicher Wortdiktat-Check, '
        'Rechtschreibgespräch am Freitag.',
    ),
    'Mathematik': (
        'Mathematik 3/4 | Zahlen und Operationen | rechnet im Zahlenraum bis 1000 sicher',
        'Rechnet zählend, braucht bei Zehnerübergängen Material.',
        'Löst Aufgaben bis 100 ohne Material und erklärt den Rechenweg.',
        'Arbeit am Rechenstrich, Blitzrechenübungen (5 Minuten täglich), '
        'Rechenkurs am Dienstag.',
    ),
    'Arbeitsverhalten': (
        'Arbeits- und Sozialverhalten | Arbeitsverhalten | arbeitet ausdauernd an einer Aufgabe',
        'Beginnt spät, unterbricht die Arbeit oft nach wenigen Minuten.',
        'Arbeitet 15 Minuten ohne Unterbrechung an einer Aufgabe.',
        'Arbeitsplan mit drei sichtbaren Schritten, Sanduhr auf dem Tisch, '
        'Rückmeldung nach jedem Schritt.',
    ),
}


def _grundlagen_und_plaene(demo):
    from extensions import db
    import foerderplan_fach
    from models import Foerderplan, Foerderinhalt, Foerdergrundlage, FoerderplanLog

    staerken = ['Erzählt gern und lebendig', 'Hilft anderen Kindern zuverlässig',
                'Große Ausdauer beim Bauen und Konstruieren', 'Bewegt sich gern und sicher',
                'Merkt sich Sachwissen sehr gut']
    bedarfe = ['Lesegenauigkeit und Lesetempo', 'Lautgetreues Schreiben',
               'Zahlverständnis im Hunderterraum', 'Ausdauer und Selbstorganisation']

    anzahl = 0
    for kind in demo.kinder.values():
        if not demo.hat(kind, 'plan'):
            continue
        db.session.add(Foerdergrundlage(
            schueler_id=kind.id,
            besondere_staerken=ZUFALL.choice(staerken),
            vorrangiger_foerderbedarf=ZUFALL.choice(bedarfe),
            wichtige_informationen='Trägt eine Brille, sitzt deshalb vorn.' if ZUFALL.random() < 0.3 else None,
            absprachen_mit_eltern='Eltern üben dreimal pro Woche 10 Minuten Lesen.' if ZUFALL.random() < 0.5 else None,
        ))
        db.session.flush()

        schluessel = ['Lesen', 'Rechtschreiben'] if kind.id % 2 else ['Mathematik', 'Arbeitsverhalten']
        plan = Foerderplan(
            schueler_id=kind.id, creator_user_id=demo.user['sommer'].id,
            titel=f'Förderplan {kind.vorname} – {demo.schuljahr}',
            datum_erstellung=demo.schuljahr_beginn + timedelta(days=ZUFALL.randint(20, 45)),
            datum_evaluation=demo.heute + timedelta(days=ZUFALL.choice([-9, 12, 25, 40])),
            status='aktiv',
        )
        db.session.add(plan)
        db.session.flush()
        item_ids = []
        for name in schluessel:
            ziel, ist, soll, massnahme = FOERDERZIELE[name]
            titel, bereich, text = [teil.strip() for teil in ziel.split('|')]
            item = demo.items.get((titel, bereich, text))
            db.session.add(Foerderinhalt(
                plan_id=plan.id, item_id=item.id if item else None,
                foerderziel=ziel, ist_zustand=ist, soll_zustand=soll, massnahmen=massnahme,
                status_id=0,
            ))
            if item:
                item_ids.append(item.id)
        foerderplan_fach.aktualisiere(plan, [], item_ids)
        db.session.add(FoerderplanLog(
            plan_id=plan.id, user_id=demo.user['sommer'].id, action='created',
            details='Förderplan angelegt (Demodaten)',
            created_at=datetime.combine(plan.datum_erstellung, time(15, 0)),
        ))
        anzahl += 1

        # Ein abgeschlossener Plan aus dem Vorjahr - damit die Evaluation zu sehen ist.
        if ZUFALL.random() < 0.6:
            alt = Foerderplan(
                schueler_id=kind.id, creator_user_id=demo.user['sommer'].id,
                titel=f'Förderplan {kind.vorname} – {demo.voriges_schuljahr}',
                datum_erstellung=date(demo.start_jahr - 1, 9, 20),
                datum_evaluation=date(demo.start_jahr, 6, 10),
                status='geschlossen',
            )
            db.session.add(alt)
            db.session.flush()
            ziel, ist, soll, massnahme = FOERDERZIELE[schluessel[0]]
            db.session.add(Foerderinhalt(
                plan_id=alt.id, foerderziel=ziel, ist_zustand=ist, soll_zustand=soll,
                massnahmen=massnahme, status_id=ZUFALL.choice([1, 2]),
                evaluation_text='Das Ziel wurde in Teilen erreicht; das Lesetempo hat sich gesteigert.',
            ))
            anzahl += 1
    db.session.flush()
    return anzahl


def _nachteilsausgleich(demo):
    from extensions import db
    from nachteilsausgleich import speichere

    vorlagen = [
        {
            'beschluss_am': demo.schuljahr_beginn + timedelta(days=30),
            'grundlage': 'LRS laut HSP und Beobachtung im Unterricht; Beschluss der Klassenkonferenz.',
            'eltern_informiert_am': demo.schuljahr_beginn + timedelta(days=34),
            'notenschutz_rechtschreiben': True,
            'notenschutz_beschluss_am': demo.schuljahr_beginn + timedelta(days=30),
            'massnahmen': [
                ('zeit', 'Bis zu 10 Minuten mehr Zeit bei schriftlichen Arbeiten, Pause nach 20 Minuten.'),
                ('hilfsmittel', 'Lesepfeil, Anlauttabelle; Aufgabenstellungen werden vorgelesen.'),
                ('raum', 'Sitzplatz vorn, bei Klassenarbeiten Nebenraum.'),
            ],
            'notiz': 'Wird in der Zeugniskonferenz erneut besprochen.',
        },
        {
            'beschluss_am': demo.schuljahr_beginn + timedelta(days=45),
            'grundlage': 'Ärztliches Attest (Feinmotorik).',
            'massnahmen': [
                ('zeit', 'Verlängerte Arbeitszeit bei Schreibaufgaben.'),
                ('didaktisch', 'Arbeitsblätter in größerer Schrift, weniger Aufgaben je Seite.'),
            ],
        },
    ]
    kinder = demo.kinder_mit('nta')
    for kind, daten in zip(kinder, vorlagen):
        speichere(kind, demo.schuljahr, daten, demo.user['wagner'])
    db.session.flush()
    return len(kinder[:len(vorlagen)])


# ----------------------------------------------------------------------
# Arbeitspläne
# ----------------------------------------------------------------------

AUFGABEN = [
    ('Lesetandem mit Partnerkind', 'Deutsch', 'book',
     'Lies zehn Minuten mit deinem Tandemkind. Das Tandemkind liest zuerst, du liest nach.',
     'Lesebuch S. 24, Sanduhr', 'Ich lese den Text am Ende flüssig vor.'),
    ('Wörter mit Silbenbögen', 'Deutsch', 'pencil',
     'Schreibe die zwölf Wörter ab und male die Silbenbögen darunter.',
     'Arbeitsblatt „Silben“, roter und blauer Stift', 'Ich höre die Silben und schreibe sie richtig.'),
    ('Blitzrechnen bis 100', 'Mathematik', 'calculator',
     'Rechne die erste Spalte auf Zeit. Notiere, wie viele Aufgaben du geschafft hast.',
     'Rechenkartei, Sanduhr', 'Ich rechne schneller als beim letzten Mal.'),
    ('Muster legen und beschreiben', 'Mathematik', 'shapes',
     'Lege das Muster mit Plättchen nach und setze es fort. Beschreibe es einem Kind.',
     'Wendeplättchen', 'Ich erkläre das Muster mit eigenen Worten.'),
    ('Forscherfrage der Woche', 'Sachunterricht', 'search',
     'Notiere drei Dinge, die du über den Igel herausgefunden hast.',
     'Sachbuch, Forscherheft', 'Ich finde Antworten in einem Sachtext.'),
]


def _arbeitsplaene(demo):
    from extensions import db
    from models import (
        ClassTaskLibrary, ClassTaskTemplate, ClassTaskTemplateCompetency,
        WorkPlan, WorkPlanTask, WorkPlanTaskCompetency, WorkPlanTaskEvaluation,
    )

    bibliothek = ClassTaskLibrary(
        class_name='3a', name='Aufgaben 3a', created_by_user_id=demo.user['sommer'].id)
    db.session.add(bibliothek)
    db.session.flush()
    lese_item = demo.items[('Deutsch 3/4', 'Lesen', 'liest altersgemäße Texte flüssig vor')]
    for titel, bereich, symbol, anleitung, material, ziel in AUFGABEN:
        vorlage = ClassTaskTemplate(
            library_id=bibliothek.id, title=titel, learning_area=bereich, icon_name=symbol,
            instructions=anleitung, materials=material,
            diff_hints='Leichter: nur die Hälfte der Aufgaben. Schwerer: eigene Aufgaben erfinden.',
        )
        db.session.add(vorlage)
        db.session.flush()
        if bereich == 'Deutsch':
            db.session.add(ClassTaskTemplateCompetency(template_id=vorlage.id, item_id=lese_item.id))

    anzahl = 0
    kandidaten = demo.klassen_kinder['3a'][:4]
    for nummer, kind in enumerate(kandidaten):
        laufend = nummer < 2
        start = demo.heute - timedelta(days=7 if laufend else 35)
        plan = WorkPlan(
            student_id=kind.id, created_by_user_id=demo.user['sommer'].id,
            period_start=start, period_end=start + timedelta(days=13),
            status='aktiv' if laufend else 'geschlossen',
            notes_for_child='Du schaffst das Schritt für Schritt.',
            notes_for_teacher='Beim Lesen die Zeit stoppen.' if laufend else 'Plan ausgewertet.',
        )
        db.session.add(plan)
        db.session.flush()
        for position, (titel, bereich, symbol, anleitung, material, ziel) in enumerate(AUFGABEN[:4]):
            aufgabe = WorkPlanTask(
                work_plan_id=plan.id, title=titel, learning_area=bereich, icon_name=symbol,
                instructions=anleitung, materials=material, child_goal=ziel,
                source_type='library', sort_order=position,
            )
            db.session.add(aufgabe)
            db.session.flush()
            if bereich == 'Deutsch':
                db.session.add(WorkPlanTaskCompetency(task_id=aufgabe.id, item_id=lese_item.id))
            if not laufend:
                db.session.add(WorkPlanTaskEvaluation(
                    task_id=aufgabe.id,
                    rating=ZUFALL.choice(['good', 'good', 'partial', 'bad']),
                    comment=ZUFALL.choice(['Hat gut geklappt.', 'Brauchte Hilfe beim Start.',
                                           'Zeit war zu knapp.', 'Sehr selbstständig gearbeitet.']),
                    evaluated_at=datetime.combine(plan.period_end, time(12, 0)),
                ))
        anzahl += 1
    db.session.flush()
    return anzahl


# ----------------------------------------------------------------------
# Elternkontakte und Beratungen
# ----------------------------------------------------------------------

def _elternkontakte(demo):
    from extensions import db
    from models import Elternberatung, Elternkontakt, ElternkontaktLog

    notizen = [
        ('Kurz-Kontakt', 'Tür-und-Angel-Gespräch',
         'Mutter berichtet, dass das Lesen zu Hause jetzt jeden Abend geübt wird.'),
        ('Telefonat', 'Rückmeldung zur Lernentwicklung',
         'Kurzer Anruf: Der Arbeitsplan wird zu Hause gut angenommen.'),
        ('Mail', 'Nachfrage zum Schwimmunterricht',
         'Eltern fragen nach dem Termin des Schwimmfests. Antwort verschickt.'),
        ('Elternmitteilung', 'Material fehlt häufig',
         'Hinweis an die Eltern, dass Federmappe und Lesebuch oft fehlen.'),
    ]
    kinder = list(demo.kinder.values())
    anzahl = 0
    for nummer, (form, betreff, text) in enumerate(notizen):
        kind = kinder[(nummer * 7) % len(kinder)]
        user = demo.user['sommer'] if kind.klasse == '3a' else demo.user['weber']
        kontakt = Elternkontakt(
            schueler_id=kind.id, user_id=user.id, eintrag_typ='notiz', kontaktform=form,
            betreff=betreff, mitteilung=text,
            datum=demo.tage_her(ZUFALL.randint(5, 40)),
        )
        db.session.add(kontakt)
        anzahl += 1

    protokolle = [
        ('Lernentwicklungsgespräch',
         'Klassenleitung, Mutter, Kind',
         'Halbjahresgespräch zur Lernentwicklung, besonders im Lesen.',
         'Die Lesegeschwindigkeit hat sich seit dem Sommer gesteigert. Rechtschreibung bleibt '
         'schwierig, vor allem bei Wörtern mit Dehnung.',
         'Lautlesetandem wird fortgeführt, das Kind nimmt am Lesekurs teil.',
         'Zu Hause wird dreimal pro Woche 10 Minuten laut gelesen.',
         'Wiedervorlage in acht Wochen, dann Rückmeldung per Telefon.'),
        ('Gespräch zum Nachteilsausgleich',
         'Schulleitung, Klassenleitung, beide Eltern',
         'Beschluss der Klassenkonferenz zum Nachteilsausgleich erläutern.',
         'Die Maßnahmen wurden vorgestellt: mehr Zeit, Lesepfeil, Nebenraum bei Arbeiten. '
         'Die Rechtschreibnote wird ausgesetzt.',
         'Die Maßnahmen gelten ab sofort und werden in der Zeugniskonferenz überprüft.',
         'Eltern melden zurück, wie das Kind die Hausaufgaben schafft.',
         'Überprüfung in der Zeugniskonferenz im Januar.'),
    ]
    fokus = demo.kinder_mit('nta') + demo.kinder_mit('elterngespraech')
    for nummer, daten in enumerate(protokolle):
        kind = fokus[nummer % len(fokus)] if fokus else kinder[nummer]
        betreff, teilnehmende, anlass, besprochen, schule, eltern, schritte = daten
        kontakt = Elternkontakt(
            schueler_id=kind.id, user_id=demo.user['sommer'].id, eintrag_typ='protokoll',
            kontaktform='Gespräch', betreff=betreff, teilnehmende=teilnehmende,
            gespraechsanlass=anlass, besprochenes=besprochen,
            vereinbarungen_schule=schule, vereinbarungen_eltern=eltern, naechste_schritte=schritte,
            naechster_termin=demo.heute + timedelta(days=5 + nummer * 9),
            datum=demo.tage_her(ZUFALL.randint(6, 25), stunde=15),
        )
        db.session.add(kontakt)
        db.session.flush()
        db.session.add(ElternkontaktLog(
            kontakt_id=kontakt.id, user_id=demo.user['sommer'].id, action='created',
            details='Protokoll angelegt (Demodaten)', created_at=kontakt.datum,
        ))
        anzahl += 1

    for kind in demo.klassen_kinder['3a'][:3]:
        db.session.add(Elternberatung(
            schueler_id=kind.id, user_id=demo.user['sommer'].id,
            datum=demo.heute - timedelta(days=ZUFALL.randint(10, 30)),
            anlass='Elternsprechtag: Rückmeldung zum Lernstand in Deutsch und Mathematik.',
            weitere_beratungspunkte='Arbeitsverhalten in der Freiarbeit, Umgang mit Fehlern.',
            vereinbarungen='Lesezeiten zu Hause festhalten, Rückmeldung in vier Wochen.',
        ))
        anzahl += 1
    db.session.flush()
    return anzahl


# ----------------------------------------------------------------------
# Ereignisse
# ----------------------------------------------------------------------

EREIGNIS_KATALOG = {
    'Regelverstoß': ['Streit auf dem Schulhof', 'Beleidigung', 'Sachbeschädigung',
                     'Unterricht wiederholt gestört'],
    'Gefährdung': ['Körperliche Auseinandersetzung', 'Gefährliches Verhalten im Treppenhaus'],
    'Positives': ['Streit selbst geschlichtet', 'Hilfe für ein jüngeres Kind'],
}
ORTE = ['Klassenraum', 'Schulhof', 'Flur', 'Turnhalle', 'Mensa', 'Schulweg']
KONSEQUENZEN = ['Gespräch mit dem Kind', 'Entschuldigung', 'Eltern informiert',
                'Nacharbeit in der Pause', 'Wiedergutmachung vereinbart']


def _ereignisse(demo):
    from extensions import db
    from models import (
        ErziehungsEreignis, ErziehungsEreignisBetroffenesKind, ErziehungsEreignisKategorie,
        ErziehungsEreignisKonsequenz, ErziehungsEreignisLog, ErziehungsEreignisVorlage,
        ErziehungsKonsequenz, ErziehungsOrt,
    )

    vorlagen = []
    for position, (kategorie_name, namen) in enumerate(EREIGNIS_KATALOG.items()):
        kategorie = ErziehungsEreignisKategorie(name=kategorie_name, sort_order=position)
        db.session.add(kategorie)
        db.session.flush()
        for stelle, name in enumerate(namen):
            vorlage = ErziehungsEreignisVorlage(category_id=kategorie.id, name=name, sort_order=stelle)
            db.session.add(vorlage)
            db.session.flush()
            vorlagen.append(vorlage)
    orte = []
    for position, name in enumerate(ORTE):
        ort = ErziehungsOrt(name=name, sort_order=position)
        db.session.add(ort)
        db.session.flush()
        orte.append(ort)
    konsequenzen = []
    for position, name in enumerate(KONSEQUENZEN):
        konsequenz = ErziehungsKonsequenz(name=name, sort_order=position)
        db.session.add(konsequenz)
        db.session.flush()
        konsequenzen.append(konsequenz)

    beschreibungen = [
        'In der großen Pause kam es zu einem Streit um den Fußball. Es wurde geschubst.',
        'Im Unterricht wiederholt dazwischengerufen, trotz zweier Ermahnungen.',
        'Hat einem Kind aus der ersten Klasse geholfen, das seine Jacke nicht finden konnte.',
        'Im Treppenhaus gerannt und ein anderes Kind angerempelt.',
        'Streit zwischen zwei Kindern selbst geschlichtet und eine Lösung gefunden.',
    ]
    # Jedes Ereignis bei einem anderen Kind - sonst stapeln sich auf der
    # Startseite mehrere offene Fälle desselben Kindes.
    kinder = demo.kinder_mit('ereignis') + [
        kind for kind in demo.klassen_kinder['3a'] + demo.klassen_kinder['3b']
        if not demo.hat(kind, 'ereignis')
    ]
    alle = list(demo.kinder.values())
    anzahl = 0
    for nummer, beschreibung in enumerate(beschreibungen):
        kind = kinder[nummer % len(kinder)]
        ersteller = demo.user['sommer'] if kind.klasse == '3a' else demo.user['weber']
        ereignis = ErziehungsEreignis(
            student_id=kind.id,
            event_template_id=vorlagen[nummer % len(vorlagen)].id,
            ort_id=orte[nummer % len(orte)].id,
            datum=demo.heute - timedelta(days=4 + nummer * 6),
            beschreibung=beschreibung,
            status='abgeschlossen' if nummer % 2 else 'offen',
            child_statement='„Er hat zuerst geschubst.“' if nummer % 2 == 0 else None,
            consequence_notes='Gespräch am selben Tag geführt.' if nummer % 2 else None,
            assigned_user_id=demo.user['wagner'].id if nummer == 1 else ersteller.id,
            created_by_user_id=ersteller.id,
        )
        db.session.add(ereignis)
        db.session.flush()
        db.session.add(ErziehungsEreignisKonsequenz(
            event_id=ereignis.id, consequence_id=konsequenzen[nummer % len(konsequenzen)].id))
        if nummer % 2 == 0:
            beteiligt = alle[(nummer * 5 + 3) % len(alle)]
            if beteiligt.id != kind.id:
                db.session.add(ErziehungsEreignisBetroffenesKind(
                    event_id=ereignis.id, student_id=beteiligt.id))
        db.session.add(ErziehungsEreignisLog(
            event_id=ereignis.id, user_id=ersteller.id, action='created',
            details='Ereignis angelegt (Demodaten)',
        ))
        anzahl += 1
    db.session.flush()
    return anzahl


# ----------------------------------------------------------------------
# Förderkonferenz und Hospitationen
# ----------------------------------------------------------------------

def _konferenzen(demo):
    from extensions import db
    from konferenz import erstelle_konferenz
    from models import FoerderkonferenzTeilnahme

    leitung = demo.user['wagner']

    # Die abgeschlossene Konferenz des vorigen Schuljahres.
    alt = erstelle_konferenz(
        demo.voriges_schuljahr, 3, 'Förderkonferenz Jahrgang 3 (Herbst)',
        date(demo.start_jahr - 1, 11, 12), leitung)
    alt.status = 'abgeschlossen'
    alt.aktuelle_phase = 7
    alt.protokoll_user_id = demo.user['lang'].id
    alt.notiz_muster = 'Auffällig viele Kinder mit Schwierigkeiten beim sinnentnehmenden Lesen.'
    alt.notiz_ressourcen = 'Lesekurs mit zwei Gruppen, Unterstützung durch die Förderpädagogik.'
    alt.massnahmen_jahrgang = 'Lautlesetandems in allen Klassen des Jahrgangs, feste Lesezeit täglich.'
    alt.abgeschlossen_am = datetime(demo.start_jahr - 1, 11, 12, 17, 30)
    db.session.flush()
    for eintrag in alt.kinder:
        eintrag.stufe = ZUFALL.choices(('A', 'B', 'C'), weights=(6, 3, 2))[0]
        eintrag.stern = ZUFALL.random() < 0.15
        if eintrag.stufe in ('B', 'C'):
            eintrag.beschluss = 'Teilnahme am Lesekurs, Überprüfung in der Zeugniskonferenz.'
            eintrag.verantwortlich_user_id = demo.user['sommer'].id
            eintrag.ueberpruefung_am = date(demo.start_jahr, 1, 28)
            eintrag.massnahme_foerderkurs = True
            eintrag.eval_umgesetzt = 'ja'
            eintrag.eval_wirksam = ZUFALL.choice(['ja', 'teilweise'])
            eintrag.eval_am = datetime(demo.start_jahr, 1, 28, 16, 0)
            eintrag.eval_von_user_id = leitung.id
            # Ausgewertet heißt erledigt - sonst stünde die Wiedervorlage aus
            # dem vorigen Schuljahr heute noch auf der Startseite.
            eintrag.erledigt_am = date(demo.start_jahr, 1, 28)

    # Die kommende Konferenz: Klassenleitungen haben vorbereitet.
    termin = demo.heute + timedelta(days=12)
    aktuell = erstelle_konferenz(
        demo.schuljahr, 3, 'Förderkonferenz Jahrgang 3 (Herbst)', termin, leitung)
    aktuell.status = 'geplant'
    aktuell.protokoll_user_id = demo.user['lang'].id
    aktuell.gaeste = 'Frau Klein (Förderpädagogik)'
    db.session.flush()
    for benutzername in ('sommer', 'weber', 'lang', 'klein'):
        db.session.add(FoerderkonferenzTeilnahme(
            konferenz_id=aktuell.id, user_id=demo.user[benutzername].id, anwesend=False))

    fragen = [
        'Reicht der Lesekurs oder brauchen wir mehr?',
        'Wie kommt das Kind mit der neuen Sitzordnung zurecht?',
        'Sollten wir die Diagnostik wiederholen?',
    ]
    for eintrag in aktuell.kinder:
        kind = eintrag.schueler
        if demo.hat(kind, 'konferenz_c'):
            stufe = 'C'
        elif demo.hat(kind, 'konferenz_b'):
            stufe = 'B'
        else:
            stufe = ZUFALL.choices(('A', 'B'), weights=(8, 2))[0]
        eintrag.vorschlag_stufe = stufe
        eintrag.vorschlag_stern = demo.hat(kind, 'stern')
        eintrag.vorschlag_beratung = stufe == 'C'
        eintrag.vorschlag_frage = ZUFALL.choice(fragen) if stufe in ('B', 'C') else None
        eintrag.vorschlag_von_user_id = (
            demo.user['sommer'].id if kind.klasse == '3a' else demo.user['weber'].id)
        eintrag.vorschlag_am = datetime.combine(demo.heute - timedelta(days=3), time(16, 20))
    db.session.flush()
    return 2


def _hospitationen(demo):
    from extensions import db
    from models import Hospitation, HospitationKind

    leitung = demo.user['wagner']
    daten = [
        ('3a', 6, 'Vorbereitung der Förderkonferenz: Lesen in der Freiarbeit.', True),
        ('3b', 18, 'Anlassbezogen nach Rückmeldung der Klassenleitung.', False),
    ]
    anzahl = 0
    for klasse, tage, anlass, freigegeben in daten:
        hospitation = Hospitation(
            datum=demo.heute - timedelta(days=tage), klasse=klasse, anlass=anlass,
            notiz='Ruhige Arbeitsatmosphäre, klare Struktur, viele Kinder arbeiten selbstständig.',
            freigegeben=freigegeben, user_id=leitung.id,
        )
        db.session.add(hospitation)
        db.session.flush()
        for kind in demo.klassen_kinder[klasse][:4]:
            db.session.add(HospitationKind(
                hospitation_id=hospitation.id, schueler_id=kind.id,
                beobachtung=ZUFALL.choice([
                    'Beginnt zügig, braucht nach zehn Minuten eine Rückmeldung.',
                    'Liest leise mit, meldet sich nicht von allein.',
                    'Arbeitet konzentriert, hilft dem Nachbarkind.',
                    'Verliert beim Lesen häufig die Zeile.',
                ]),
                empfehlung_stufe='C' if demo.hat(kind, 'konferenz_c') else (
                    'B' if demo.hat(kind, 'konferenz_b') else 'A'),
                stern=demo.hat(kind, 'stern'),
            ))
        anzahl += 1
    db.session.flush()
    return anzahl


def _benachrichtigungen(demo):
    from extensions import db
    from models import Notification

    eintraege = [
        ('sommer', 'foerderkurs_ohne_plan', 'Förderkurs ohne Förderplan',
         'Lesekurs 3/4 – es fehlt ein aktiver Förderplan im Fach Deutsch.', '/foerderkurse/ohne-plan', False),
        ('sommer', 'elterntermin', 'Termin in fünf Tagen',
         'Gesprächstermin aus dem Protokoll „Lernentwicklungsgespräch“.', '/elternkontakte', False),
        ('wagner', 'erziehung_zugewiesen', 'Ereignis zur Bearbeitung',
         'Ihnen wurde ein Ereignis zugewiesen.', '/erziehung', False),
        ('lang', 'foerderplan_neu', 'Neuer Förderplan',
         'Für ein Kind Ihrer Klasse wurde ein Förderplan angelegt.', '/foerderplan', True),
    ]
    for benutzername, art, titel, text, ziel, gelesen in eintraege:
        db.session.add(Notification(
            user_id=demo.user[benutzername].id, kind=art, title=titel, message=text,
            target_url=ziel, is_read=gelesen,
            created_at=demo.tage_her(ZUFALL.randint(1, 4)),
        ))
    db.session.flush()
    return len(eintraege)


# ----------------------------------------------------------------------
# Hauptprogramm
# ----------------------------------------------------------------------

def baue(db_pfad, heute=None):
    """Legt die Demodatenbank an und füllt sie. Gibt eine Zusammenfassung zurück."""
    from app import create_app
    from extensions import db

    pfad = Path(db_pfad).resolve()
    pfad.parent.mkdir(parents=True, exist_ok=True)
    app = create_app({
        'SQLALCHEMY_DATABASE_URI': f'sqlite:///{pfad}',
        'PROTECTED_UPLOAD_FOLDER': str(pfad.parent / 'demo_protected_uploads'),
    })
    demo = Demo(heute or date.today())
    zahlen = {}
    with app.app_context():
        db.drop_all()
        db.create_all()
        _konfiguration(demo)
        _benutzer(demo)
        _klassen_und_kinder(demo)
        _faecher_und_kurse(demo)
        _boegen(demo)
        zahlen['Beobachtungen'] = _beobachtungen(demo)
        zahlen['Diagnostik-Ergebnisse'] = _diagnostik(demo)
        zahlen['Förderpläne'] = _grundlagen_und_plaene(demo)
        _foerderangaben(demo)
        zahlen['Nachteilsausgleich'] = _nachteilsausgleich(demo)
        zahlen['Arbeitspläne'] = _arbeitsplaene(demo)
        zahlen['Elternkontakte'] = _elternkontakte(demo)
        zahlen['Ereignisse'] = _ereignisse(demo)
        zahlen['Förderkonferenzen'] = _konferenzen(demo)
        zahlen['Hospitationen'] = _hospitationen(demo)
        zahlen['Benachrichtigungen'] = _benachrichtigungen(demo)
        db.session.commit()
        zahlen['Kinder'] = len(demo.kinder)
        zahlen['Benutzerkonten'] = len(demo.user)
    return pfad, zahlen


def main():
    zerleger = argparse.ArgumentParser(description='Demodaten für den KompetenzKompass erzeugen.')
    zerleger.add_argument('--db', default=str(BASE_DIR / 'instance' / 'demo.db'),
                          help='Pfad der Demodatenbank (Vorgabe: instance/demo.db)')
    zerleger.add_argument('--neu', action='store_true',
                          help='Vorhandene Datei ohne Rückfrage überschreiben')
    argumente = zerleger.parse_args()

    pfad = Path(argumente.db)
    if pfad.exists() and not argumente.neu:
        antwort = input(f'{pfad} gibt es schon. Neu aufbauen? [j/N] ').strip().lower()
        if antwort not in ('j', 'ja', 'y'):
            print('Abgebrochen.')
            return 1
    if pfad.exists():
        pfad.unlink()
    for zusatz in ('-wal', '-shm'):
        begleiter = Path(str(pfad) + zusatz)
        if begleiter.exists():
            begleiter.unlink()

    pfad, zahlen = baue(pfad)
    print(f'Demodatenbank angelegt: {pfad}')
    for name, anzahl in zahlen.items():
        print(f'  {name}: {anzahl}')
    print(f'\nAnmeldung: {", ".join(sorted(BENUTZER[i][0] for i in range(len(BENUTZER))))}'
          f' – Passwort für alle: {PASSWORT}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
