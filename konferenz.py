"""Förderkonferenz: Phasen, Rechte, Zusammenführung der Daten, Protokoll.

Ablauf: Die Schulleitung legt eine Konferenz für einen Jahrgang an. Bis zur
Konferenz tragen die Klassenleitungen ihre Vorschläge für die Kinder ihrer
Klasse ein. In der Konferenz führt die Schulleitung durch sieben Phasen und
hält die Beschlüsse fest. Danach ist die Konferenz schreibgeschützt; die
Beschlüsse erscheinen als Aufgaben, und die Zeugniskonferenz evaluiert sie.

Geschrieben wird feldweise (siehe routes/konferenz_routes.py): jedes Feld für
sich, damit nichts verloren geht, wenn ein Gerät abstürzt oder das WLAN wackelt.
Dieses Modul kennt deshalb zu jedem Feld, wer es wann ändern darf.
"""

from datetime import date, timedelta

from competency_matrix import LEVEL_LABELS
from diagnostik import STUFEN as DIAGNOSTIK_STUFEN, risikogrenzen, verlauf as diagnostik_verlauf
from elternberatung import LEVEL_FARBEN, diagnostik_kontext, kompetenz_uebersicht
from extensions import db
from hospitation import empfehlungen as hospitation_empfehlungen, sichtbare_eintraege as hospitation_eintraege
from klassenzugriff import darf_kind_sehen, sichtbare_elternkontakte, sichtbare_ereignisse
from models import (
    Beobachtung,
    Bogen,
    Elternkontakt,
    ErziehungsEreignis,
    Foerderangaben,
    Foerderkonferenz,
    FoerderkonferenzKind,
    FoerderkonferenzLog,
    FoerderkonferenzTeilnahme,
    Foerderplan,
    Item,
    KONFERENZ_STUFEN,
    Schueler,
    User,
    WorkPlan,
)
from school_year import active_school_year_start
from student_selection import get_user_klassenkontext
from time_utils import utc_now
from transition_plan import effective_jahrgang

STUFEN = KONFERENZ_STUFEN
STUFEN_REIHENFOLGE = ('A', 'B', 'C')

# (Nummer, Name, Richtzeit in Minuten, Zeit je Kind in Minuten, Beschreibung)
PHASEN = [
    (1, 'Einstieg', 5, 0, 'Teilnehmende, Moderation und Protokoll'),
    (2, 'Jahrgangsblick', 10, 0, 'Diagnostik und Klassenübersichten, gemeinsame Muster'),
    (3, 'A-Block', 15, 0, 'Vorschläge „Weiterführen“ sammelbestätigen'),
    (4, 'B-Kinder', 0, 2, 'Je Kind: Frage, Beschluss, verantwortlich, Wiedervorlage'),
    (5, 'Vertiefte Beratung', 0, 9, 'C-Kinder in sechs Schritten'),
    (6, 'Ressourcen', 10, 0, 'Förderkurse bestätigen, jahrgangsweite Maßnahmen'),
    (7, 'Abschluss', 5, 0, 'Beschlüsse prüfen und Konferenz abschließen'),
]
PHASEN_NAMEN = {nummer: name for nummer, name, _, _, _ in PHASEN}
ERSTE_PHASE, LETZTE_PHASE = 1, len(PHASEN)

# Die sechs Schritte der vertieften Beratung (Phase 5) - Feld je Schritt.
BERATUNGSSCHRITTE = [
    ('staerke', 'Stärke', 'Was kann das Kind, worauf lässt sich aufbauen?'),
    ('fragestellung', 'Fragestellung', 'Was genau wollen wir klären?'),
    ('daten_notiz', 'Daten', 'Was sagen Beobachtungen, Diagnostik und Elterngespräche?'),
    ('bisherige_massnahmen', 'Bisherige Maßnahmen', 'Was wurde bisher versucht?'),
    ('wirkung', 'Wirkung', 'Was hat gewirkt, was nicht?'),
    ('beschluss', 'Entscheidung', 'Was tun wir, wer macht es, bis wann?'),
]

MASSNAHMEN = [
    ('massnahme_foerderkurs', 'Förderkurs'),
    ('massnahme_nachteilsausgleich', 'Nachteilsausgleich'),
    ('massnahme_externe_foerderung', 'externe Förderung'),
    ('massnahme_foerderplan', 'Förderplan'),
    ('massnahme_elterngespraech', 'Elterngespräch'),
    ('massnahme_diagnostik', 'Diagnostik'),
]

EVAL_WIRKSAM = {'ja': 'wirksam', 'teilweise': 'teilweise wirksam', 'nein': 'nicht wirksam'}

# Aufgaben aus Beschlüssen erscheinen so viele Tage vor der Frist.
VORLAUF_TAGE = 7


# ----------------------------------------------------------------------
# Felder und Rechte
# ----------------------------------------------------------------------

# Feld -> Typ. Nur diese Felder lassen sich über die Speicher-Schnittstelle ändern.
KONFERENZ_FELDER = {
    'titel': 'text', 'termin': 'datum', 'gaeste': 'text',
    'moderation_user_id': 'user', 'protokoll_user_id': 'user',
    'notiz_muster': 'text', 'notiz_ressourcen': 'text', 'massnahmen_jahrgang': 'text',
    'aktuelle_phase': 'zahl', 'aktuelles_kind_id': 'kind',
}
KIND_FELDER_VORSCHLAG = {
    'vorschlag_stufe': 'stufe', 'vorschlag_stern': 'bool',
    'vorschlag_frage': 'text', 'vorschlag_beratung': 'bool',
}
KIND_FELDER_KONFERENZ = {
    'stufe': 'stufe', 'stern': 'bool',
    'staerke': 'text', 'fragestellung': 'text', 'daten_notiz': 'text',
    'bisherige_massnahmen': 'text', 'wirkung': 'text', 'beschluss': 'text',
    'verantwortlich_user_id': 'user', 'ueberpruefung_am': 'datum',
    'foerderkurs_name': 'text', 'foerderkurs_bestaetigt': 'bool', 'massnahmen_notiz': 'text',
    **{feld: 'bool' for feld, _ in MASSNAHMEN},
}
KIND_FELDER_EVALUATION = {
    'eval_umgesetzt': 'text', 'eval_wirksam': 'text', 'eval_stufe_neu': 'stufe', 'eval_notiz': 'text',
}
KIND_FELDER = {**KIND_FELDER_VORSCHLAG, **KIND_FELDER_KONFERENZ, **KIND_FELDER_EVALUATION}


def ist_klassenleitung(user, klasse):
    kontext = get_user_klassenkontext(user)
    leitung = (kontext.get('klassenleitung') or '').strip()
    return bool(leitung) and leitung == (klasse or '').strip()


def darf_moderieren(user, konferenz=None):
    """Nur die Schulleitung (und die Verwaltung) führt eine Konferenz."""
    return bool(getattr(user, 'ist_schulleitung', False))


def darf_lesen(user, konferenz):
    """Schulleitung immer; sonst, wer mindestens ein Kind der Konferenz sehen darf."""
    if darf_moderieren(user):
        return True
    return any(darf_kind_sehen(user, eintrag.schueler) for eintrag in konferenz.kinder)


def sichtbare_kinder(user, konferenz):
    """Die Einträge, die diese Person sehen darf - für die Schulleitung alle."""
    if darf_moderieren(user):
        eintraege = list(konferenz.kinder)
    else:
        eintraege = [e for e in konferenz.kinder if darf_kind_sehen(user, e.schueler)]
    return sortiere(eintraege)


def eigene_kinder(user, konferenz):
    """Die Kinder der eigenen Klasse - für die Vorbereitung."""
    return sortiere([e for e in konferenz.kinder if ist_klassenleitung(user, e.klasse)])


def sortiere(eintraege):
    return sorted(eintraege, key=lambda e: ((e.klasse or '').lower(),
                                            (e.schueler.nachname or '').lower(),
                                            (e.schueler.vorname or '').lower()))


def darf_feld_schreiben(user, konferenz, feld, eintrag=None):
    """Wer darf dieses Feld jetzt ändern?

    Die Schulleitung schreibt alles, solange die Konferenz nicht abgeschlossen
    ist. Die Klassenleitung schreibt die Vorschlagsfelder für Kinder ihrer
    Klasse, solange die Konferenz nicht abgeschlossen ist. Danach niemand.
    """
    if konferenz.abgeschlossen:
        return False
    if darf_moderieren(user):
        return True
    if eintrag is None:
        return False
    if feld in KIND_FELDER_VORSCHLAG and ist_klassenleitung(user, eintrag.klasse):
        return True
    return False


def wert_aus(typ, roh):
    """Wandelt den Wert aus dem Formular in den Datentyp der Spalte."""
    if typ == 'bool':
        if isinstance(roh, bool):
            return roh
        return str(roh).strip().lower() in ('1', 'true', 'ja', 'on')
    text = '' if roh is None else str(roh).strip()
    if typ == 'text':
        return text or None
    if typ == 'stufe':
        wert = text.upper()
        return wert if wert in STUFEN else None
    if typ == 'zahl':
        try:
            return int(text)
        except ValueError:
            return None
    if typ == 'user':
        try:
            nummer = int(text)
        except ValueError:
            return None
        return nummer if db.session.get(User, nummer) else None
    if typ == 'kind':
        try:
            nummer = int(text)
        except ValueError:
            return None
        return nummer if db.session.get(Schueler, nummer) else None
    if typ == 'datum':
        if not text:
            return None
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None
    return text or None


def schreibe_feld(objekt, feld, roh, user):
    """Setzt ein Feld und führt die Spuren nach. Committet nicht."""
    felder = KIND_FELDER if isinstance(objekt, FoerderkonferenzKind) else KONFERENZ_FELDER
    typ = felder[feld]
    wert = wert_aus(typ, roh)
    setattr(objekt, feld, wert)
    jetzt = utc_now()
    if isinstance(objekt, FoerderkonferenzKind):
        objekt.bearbeitet_am = jetzt
        objekt.bearbeitet_von_user_id = getattr(user, 'id', None)
        if feld in KIND_FELDER_VORSCHLAG:
            objekt.vorschlag_von_user_id = getattr(user, 'id', None)
            objekt.vorschlag_am = jetzt
        if feld in KIND_FELDER_EVALUATION:
            objekt.eval_am = jetzt
            objekt.eval_von_user_id = getattr(user, 'id', None)
    else:
        objekt.bearbeitet_am = jetzt
    return wert


# ----------------------------------------------------------------------
# Konferenz anlegen und führen
# ----------------------------------------------------------------------

def kinder_des_jahrgangs(jahrgang):
    kinder = [
        kind for kind in Schueler.query.filter(Schueler.is_active.is_(True)).all()
        if effective_jahrgang(kind) == jahrgang
    ]
    return sorted(kinder, key=lambda k: ((k.klasse or '').lower(), (k.nachname or '').lower(), (k.vorname or '').lower()))


def erstelle_konferenz(schuljahr, jahrgang, titel, termin, user):
    """Legt die Konferenz mit einer Zeile je Kind des Jahrgangs an. Committet nicht."""
    konferenz = Foerderkonferenz(
        schuljahr=schuljahr, jahrgang=jahrgang, titel=titel, termin=termin,
        erstellt_von_user_id=getattr(user, 'id', None), moderation_user_id=getattr(user, 'id', None),
    )
    db.session.add(konferenz)
    db.session.flush()
    for kind in kinder_des_jahrgangs(jahrgang):
        db.session.add(FoerderkonferenzKind(
            konferenz_id=konferenz.id, schueler_id=kind.id, klasse=(kind.klasse or '').strip() or None,
        ))
    protokolliere(konferenz, user, 'angelegt', f'Jahrgang {jahrgang}, {schuljahr}')
    return konferenz


def ergaenze_kinder(konferenz):
    """Kinder, die seit dem Anlegen dazugekommen sind, nachtragen. Committet nicht."""
    vorhanden = {eintrag.schueler_id for eintrag in konferenz.kinder}
    neu = 0
    for kind in kinder_des_jahrgangs(konferenz.jahrgang):
        if kind.id not in vorhanden:
            db.session.add(FoerderkonferenzKind(
                konferenz_id=konferenz.id, schueler_id=kind.id, klasse=(kind.klasse or '').strip() or None,
            ))
            neu += 1
    return neu


def protokolliere(konferenz, user, aktion, details=None):
    db.session.add(FoerderkonferenzLog(
        konferenz_id=konferenz.id, user_id=getattr(user, 'id', None), aktion=aktion, details=details,
    ))


def teilnehmende(konferenz):
    """Alle Lehrkräfte mit Anwesenheitsstand, Klassenleitungen des Jahrgangs zuerst."""
    stand = {t.user_id: t.anwesend for t in konferenz.teilnahmen}
    klassen = {(e.klasse or '').strip() for e in konferenz.kinder}
    reihen = []
    for user in User.query.order_by(User.nachname, User.vorname, User.username).all():
        kontext = get_user_klassenkontext(user)
        leitung = (kontext.get('klassenleitung') or '').strip()
        reihen.append({
            'user': user,
            'anwesend': stand.get(user.id, False),
            'klassenleitung': leitung if leitung in klassen else None,
        })
    return sorted(reihen, key=lambda r: (r['klassenleitung'] is None, r['klassenleitung'] or '',
                                         (r['user'].nachname or r['user'].username or '').lower()))


def setze_anwesenheit(konferenz, user_id, anwesend):
    eintrag = FoerderkonferenzTeilnahme.query.filter_by(konferenz_id=konferenz.id, user_id=user_id).first()
    if not eintrag:
        eintrag = FoerderkonferenzTeilnahme(konferenz_id=konferenz.id, user_id=user_id)
        db.session.add(eintrag)
    eintrag.anwesend = bool(anwesend)
    konferenz.bearbeitet_am = utc_now()
    return eintrag


def kinder_der_phase(konferenz, phase, user=None):
    """Die Kinder, die in dieser Phase besprochen werden."""
    eintraege = sichtbare_kinder(user, konferenz) if user is not None else sortiere(konferenz.kinder)
    if phase == 3:
        return [e for e in eintraege if (e.stufe or e.vorschlag_stufe) == 'A']
    if phase == 4:
        return [e for e in eintraege if (e.stufe or e.vorschlag_stufe) == 'B']
    if phase == 5:
        return [e for e in eintraege if (e.stufe or e.vorschlag_stufe) == 'C']
    return eintraege


def offen_in_phase(konferenz, phase):
    """Wie viele Kinder in dieser Phase noch etwas brauchen."""
    kinder = kinder_der_phase(konferenz, phase)
    if phase == 3:
        return sum(1 for e in kinder if not e.stufe)
    if phase in (4, 5):
        return sum(1 for e in kinder if e.beschluss_offen)
    return sum(1 for e in konferenz.kinder if not e.stufe) if phase == 7 else 0


def richtzeit(konferenz, phase):
    """Richtzeit einer Phase in Minuten - bei B und C je Kind gerechnet."""
    nummer, _, grund, je_kind, _ = PHASEN[phase - 1]
    return grund + je_kind * len(kinder_der_phase(konferenz, nummer))


def phasen_uebersicht(konferenz):
    return [
        {
            'nummer': nummer, 'name': name, 'beschreibung': beschreibung,
            'richtzeit': richtzeit(konferenz, nummer),
            'offen': offen_in_phase(konferenz, nummer),
            'aktiv': konferenz.aktuelle_phase == nummer,
            'kinder': len(kinder_der_phase(konferenz, nummer)) if nummer in (3, 4, 5) else None,
        }
        for nummer, name, _, _, beschreibung in PHASEN
    ]


def zaehle_stufen(konferenz):
    zaehler = {stufe: 0 for stufe in STUFEN_REIHENFOLGE}
    zaehler['ohne'] = 0
    zaehler['stern'] = 0
    for eintrag in konferenz.kinder:
        if eintrag.stufe in zaehler:
            zaehler[eintrag.stufe] += 1
        else:
            zaehler['ohne'] += 1
        if eintrag.stern:
            zaehler['stern'] += 1
    return zaehler


def pruefe_vollstaendigkeit(konferenz):
    """Hinweise für den Abschluss - halten den Abschluss aber nicht auf."""
    hinweise = []
    ohne_stufe = [e for e in sortiere(konferenz.kinder) if not e.stufe]
    if ohne_stufe:
        namen = ', '.join(f'{e.schueler.vorname} {e.schueler.nachname}' for e in ohne_stufe[:5])
        hinweise.append(f'{len(ohne_stufe)} Kind(er) ohne Stufe: {namen}' + (' …' if len(ohne_stufe) > 5 else ''))
    offen = [e for e in sortiere(konferenz.kinder) if e.beschluss_offen]
    if offen:
        namen = ', '.join(f'{e.schueler.vorname} {e.schueler.nachname}' for e in offen[:5])
        hinweise.append(f'{len(offen)} B-/C-Kind(er) ohne Beschluss, Zuständigkeit oder Datum: {namen}'
                        + (' …' if len(offen) > 5 else ''))
    kurse = [e for e in konferenz.kinder if e.massnahme_foerderkurs and not e.foerderkurs_bestaetigt]
    if kurse:
        hinweise.append(f'{len(kurse)} Förderkurs-Teilnahme(n) noch nicht bestätigt (Phase 6).')
    return hinweise


# ----------------------------------------------------------------------
# Abgleich der A-Vorschläge mit Diagnostik und Beobachtungen
# ----------------------------------------------------------------------

# Ab so vielen Kompetenzen mit "reicht noch nicht" im Schuljahr wird gewarnt.
SCHWACHE_ITEMS = 4


def abgleich(eintrag, grenzen=None):
    """Warnungen, wenn ein A-Vorschlag den Daten widerspricht.

    Geprüft werden die Diagnostik (Risikostufe), die Beobachtungen der letzten
    Monate und ein laufender Förderplan.
    """
    if (eintrag.stufe or eintrag.vorschlag_stufe) != 'A':
        return []
    schueler = eintrag.schueler
    grenzen = grenzen if grenzen is not None else risikogrenzen()
    hinweise = []

    for bereich in diagnostik_verlauf(schueler, grenzen):
        aktuell = bereich['aktuell']
        if aktuell and aktuell.stufe:
            werte = ', '.join(w.anzeige for w in aktuell.ausloeser) or (
                aktuell.schwaechster_leitwert.anzeige if aktuell.schwaechster_leitwert else '')
            hinweise.append(
                f'Diagnostik {bereich["bereich"]}: {DIAGNOSTIK_STUFEN[aktuell.stufe][0]}'
                + (f' ({werte})' if werte else '')
            )

    beginn = active_school_year_start()
    query = (
        db.session.query(Beobachtung.item_id)
        .join(Item, Beobachtung.item_id == Item.id)
        .join(Bogen, Item.bogen_id == Bogen.id)
        .filter(Beobachtung.schueler_id == schueler.id, Beobachtung.wert == 1,
                Bogen.foerderempfehlung.is_(True))
    )
    if beginn:
        query = query.filter(Beobachtung.datum >= beginn)
    schwache = {row[0] for row in query.all()}
    if len(schwache) >= SCHWACHE_ITEMS:
        bereiche = {
            (item.bereich or 'Allgemein')
            for item in Item.query.filter(Item.id.in_(schwache)).all()
        }
        hinweise.append(f'{len(schwache)} Kompetenzen mit „reicht noch nicht“ ({", ".join(sorted(bereiche)[:3])})')

    if Foerderplan.query.filter_by(schueler_id=schueler.id, status='aktiv').first():
        hinweise.append('Es läuft ein Förderplan.')

    hospitation = hospitation_empfehlungen({schueler.id}, eintrag.konferenz.schuljahr).get(schueler.id)
    if hospitation and hospitation.empfehlung_stufe in ('B', 'C'):
        hinweise.append(f'Hospitation am {hospitation.hospitation.datum.strftime("%d.%m.%Y")}: '
                        f'Empfehlung {hospitation.empfehlung_stufe}')
    return hinweise


def abgleich_je_kind(eintraege, grenzen=None):
    grenzen = grenzen if grenzen is not None else risikogrenzen()
    return {eintrag.id: abgleich(eintrag, grenzen) for eintrag in eintraege}


# ----------------------------------------------------------------------
# Alles zu einem Kind - für die Beamer-Ansicht
# ----------------------------------------------------------------------

def kind_kontext(eintrag, user):
    """Führt zusammen, was in der Konferenz über ein Kind zu sehen ist."""
    from routes.erfassung_routes import _build_bogen_entries_for_student

    schueler = eintrag.schueler
    bogen_context = _build_bogen_entries_for_student(schueler.id)
    aktiver_plan = (
        Foerderplan.query.filter_by(schueler_id=schueler.id, status='aktiv')
        .order_by(Foerderplan.datum_erstellung.desc(), Foerderplan.id.desc()).first()
    )
    letzter_plan = (
        Foerderplan.query
        .filter(Foerderplan.schueler_id == schueler.id, Foerderplan.datum_evaluation.isnot(None))
        .order_by(Foerderplan.datum_evaluation.desc(), Foerderplan.id.desc()).first()
    )
    config_schuljahr = eintrag.konferenz.schuljahr
    return {
        'eintrag': eintrag,
        'schueler': schueler,
        'grundlage': schueler.foerdergrundlage,
        'uebersicht': kompetenz_uebersicht(bogen_context['bogen_rows']),
        'level_labels': LEVEL_LABELS,
        'level_farben': LEVEL_FARBEN,
        'diagnostik': diagnostik_kontext(schueler),
        'diagnostik_stufen': DIAGNOSTIK_STUFEN,
        'aktiver_plan': aktiver_plan,
        'letzter_plan': letzter_plan,
        'arbeitsplaene': (
            WorkPlan.query.filter_by(student_id=schueler.id)
            .order_by(WorkPlan.period_start.desc()).limit(3).all()
        ),
        'foerderangaben': Foerderangaben.query.filter_by(
            schueler_id=schueler.id, schuljahr=config_schuljahr).first(),
        'elternkontakte': (
            sichtbare_elternkontakte(Elternkontakt.query, user)
            .filter(Elternkontakt.schueler_id == schueler.id)
            .order_by(Elternkontakt.datum.desc()).limit(4).all()
        ),
        'ereignisse': (
            sichtbare_ereignisse(ErziehungsEreignis.query, user)
            .filter(ErziehungsEreignis.student_id == schueler.id)
            .order_by(ErziehungsEreignis.datum.desc()).limit(4).all()
        ),
        'fruehere': fruehere_eintraege(eintrag),
        'hospitationen': hospitation_eintraege(schueler, user, eintrag.konferenz.schuljahr),
        'abgleich': abgleich(eintrag),
    }


def fruehere_eintraege(eintrag, limit=3):
    """Stufen und Beschlüsse früherer Konferenzen desselben Kindes."""
    return (
        FoerderkonferenzKind.query
        .join(Foerderkonferenz)
        .filter(
            FoerderkonferenzKind.schueler_id == eintrag.schueler_id,
            FoerderkonferenzKind.konferenz_id != eintrag.konferenz_id,
            Foerderkonferenz.status == 'abgeschlossen',
        )
        .order_by(Foerderkonferenz.termin.desc().nullslast(), Foerderkonferenz.id.desc())
        .limit(limit)
        .all()
    )


def eintraege_fuer_kind(schueler, user):
    """Konferenzeinträge eines Kindes für Schülerakte und Gesamtakte."""
    if user is not None and not darf_kind_sehen(user, schueler):
        return []
    return (
        FoerderkonferenzKind.query
        .join(Foerderkonferenz)
        .filter(FoerderkonferenzKind.schueler_id == schueler.id, Foerderkonferenz.status == 'abgeschlossen')
        .order_by(Foerderkonferenz.termin.desc().nullslast(), Foerderkonferenz.id.desc())
        .all()
    )


# ----------------------------------------------------------------------
# Aufgaben aus den Beschlüssen
# ----------------------------------------------------------------------

def offene_beschluesse(user, heute=None, vorlauf=VORLAUF_TAGE, alle=False):
    """Beschlüsse mit Frist, für die diese Person zuständig ist.

    Zuständig ist, wer im Beschluss steht, und die Klassenleitung des Kindes.
    Gezeigt werden sie ab `vorlauf` Tagen vor der Frist (alle=True: unabhängig
    davon), bis sie als erledigt abgehakt sind.
    """
    heute = heute or utc_now().date()
    kontext = get_user_klassenkontext(user)
    eigene_klasse = (kontext.get('klassenleitung') or '').strip()
    query = (
        FoerderkonferenzKind.query
        .join(Foerderkonferenz)
        .filter(
            Foerderkonferenz.status == 'abgeschlossen',
            FoerderkonferenzKind.erledigt_am.is_(None),
            FoerderkonferenzKind.ueberpruefung_am.isnot(None),
            FoerderkonferenzKind.stufe.in_(('B', 'C')),
        )
    )
    if not alle:
        query = query.filter(FoerderkonferenzKind.ueberpruefung_am <= heute + timedelta(days=vorlauf))
    eintraege = [
        eintrag for eintrag in query.order_by(FoerderkonferenzKind.ueberpruefung_am.asc()).all()
        if eintrag.verantwortlich_user_id == getattr(user, 'id', None)
        or (eigene_klasse and (eintrag.klasse or '').strip() == eigene_klasse)
        or (eigene_klasse and (eintrag.schueler.klasse or '').strip() == eigene_klasse)
    ]
    return eintraege


def beschluss_label(eintrag):
    return 'Wiedervorlage' if eintrag.stufe == 'B' else 'Konferenzbeschluss'


# ----------------------------------------------------------------------
# Protokoll
# ----------------------------------------------------------------------

def _datum(wert):
    return wert.strftime('%d.%m.%Y') if wert else '–'


def protokoll_bloecke(konferenz, anonym=False):
    """Blöcke für den ODT-Export: intern mit Kindern, anonym ohne Namen."""
    zaehler = zaehle_stufen(konferenz)
    anwesend = [t.user.display_name for t in konferenz.teilnahmen if t.anwesend and t.user]
    bloecke = [
        {'type': 'paragraph', 'style': 'Titel', 'text': konferenz.titel},
        {'type': 'fields', 'rows': [
            ('Schuljahr', konferenz.schuljahr),
            ('Jahrgangsstufe', str(konferenz.jahrgang)),
            ('Termin', _datum(konferenz.termin)),
            ('Moderation', konferenz.moderation.display_name if konferenz.moderation else '–'),
            ('Protokoll', konferenz.protokoll.display_name if konferenz.protokoll else '–'),
            ('Teilnehmende', ', '.join(sorted(anwesend)) or '–'),
            ('Gäste', konferenz.gaeste or '–'),
            ('Fassung', 'anonym' if anonym else 'intern'),
        ]},
        {'type': 'heading', 'level': 1, 'text': 'Ergebnis des Jahrgangs'},
        {'type': 'table',
         'head': ['Kinder', 'A – Weiterführen', 'B – Genauer hinsehen', 'C – Handeln', 'ohne Stufe', 'Stärken ★'],
         'rows': [[str(len(konferenz.kinder)), str(zaehler['A']), str(zaehler['B']), str(zaehler['C']),
                   str(zaehler['ohne']), str(zaehler['stern'])]]},
    ]
    if konferenz.notiz_muster:
        bloecke.append({'type': 'heading', 'level': 1, 'text': 'Muster im Jahrgang'})
        bloecke.append({'type': 'paragraph', 'text': konferenz.notiz_muster})
    if konferenz.notiz_ressourcen or konferenz.massnahmen_jahrgang:
        bloecke.append({'type': 'heading', 'level': 1, 'text': 'Ressourcen und Schulentwicklung'})
        if konferenz.massnahmen_jahrgang:
            bloecke.append({'type': 'paragraph', 'text': konferenz.massnahmen_jahrgang})
        if konferenz.notiz_ressourcen:
            bloecke.append({'type': 'paragraph', 'text': konferenz.notiz_ressourcen})

    kurse = [e for e in sortiere(konferenz.kinder) if e.massnahme_foerderkurs]
    if kurse:
        bloecke.append({'type': 'heading', 'level': 1, 'text': 'Förderkurse'})
        if anonym:
            namen = {}
            for eintrag in kurse:
                namen.setdefault(eintrag.foerderkurs_name or 'ohne Bezeichnung', 0)
                namen[eintrag.foerderkurs_name or 'ohne Bezeichnung'] += 1
            bloecke.append({'type': 'table', 'head': ['Kurs', 'Kinder'],
                            'rows': [[name, str(anzahl)] for name, anzahl in sorted(namen.items())]})
        else:
            bloecke.append({'type': 'table', 'head': ['Kind', 'Klasse', 'Kurs', 'bestätigt'],
                            'rows': [[f'{e.schueler.nachname}, {e.schueler.vorname}', e.klasse or '–',
                                      e.foerderkurs_name or '–', 'ja' if e.foerderkurs_bestaetigt else 'nein']
                                     for e in kurse]})

    if anonym:
        bloecke.append({'type': 'paragraph', 'style': 'Klein',
                        'text': 'Diese Fassung enthält keine Namen von Kindern.'})
    else:
        beschluesse = [e for e in sortiere(konferenz.kinder) if e.stufe in ('B', 'C')]
        bloecke.append({'type': 'heading', 'level': 1, 'text': 'Beschlüsse'})
        if beschluesse:
            bloecke.append({'type': 'table',
                            'head': ['Kind', 'Klasse', 'Stufe', 'Beschluss', 'Verantwortlich', 'Bis'],
                            'widths': [3.4, 1.4, 1.2, 6.0, 2.6, 1.8],
                            'rows': [[f'{e.schueler.nachname}, {e.schueler.vorname}', e.klasse or '–',
                                      e.stufe or '–', (e.beschluss or '–'),
                                      e.verantwortlich.display_name if e.verantwortlich else '–',
                                      _datum(e.ueberpruefung_am)] for e in beschluesse]})
        else:
            bloecke.append({'type': 'paragraph', 'text': 'Keine Beschlüsse zu B- oder C-Kindern.'})

        staerken = [e for e in sortiere(konferenz.kinder) if e.stern]
        if staerken:
            bloecke.append({'type': 'heading', 'level': 1, 'text': 'Besondere Stärken'})
            bloecke.append({'type': 'table', 'head': ['Kind', 'Klasse', 'Stärke'],
                            'rows': [[f'{e.schueler.nachname}, {e.schueler.vorname}', e.klasse or '–',
                                      e.staerke or e.vorschlag_frage or '–'] for e in staerken]})

    bloecke.append({'type': 'paragraph', 'style': 'Klein',
                    'text': f'Erstellt am {utc_now().strftime("%d.%m.%Y")} mit KompetenzKompass.'})
    return bloecke


def dateiname(konferenz, anonym):
    teil = 'anonym' if anonym else 'intern'
    jahr = (konferenz.schuljahr or '').replace('/', '-')
    return f'Foerderkonferenz_Jg{konferenz.jahrgang}_{jahr}_{teil}'
