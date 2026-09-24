"""Benachrichtigungen: wer wann wovon erfährt.

Eine Benachrichtigung landet zuerst unter der Glocke. Ob und wann daraus eine
E-Mail wird, entscheidet der Versandlauf (mail_versand.py) nach dem Takt, den
jede Lehrkraft in ihrem Konto wählt.

Grundregeln:
- Wer etwas selbst getan hat, wird darüber nicht benachrichtigt.
- Jede Art lässt sich im Konto abbestellen; sie erscheint dann weder unter der
  Glocke noch in einer E-Mail.
- Wer aus mehreren Gründen betroffen ist (etwa Klassenleitung und zuständig),
  bekommt trotzdem nur eine Benachrichtigung je Vorgang.
"""

from datetime import timedelta

from flask import has_request_context, url_for
from flask_login import current_user

from extensions import db
from models import (
    BenachrichtigungAbbestellt,
    Elternkontakt,
    Notification,
    Schueler,
    User,
    UserKlassenzuordnung,
)
from time_utils import utc_now

ERZIEHUNG_NEU = 'erziehung_neu'
ERZIEHUNG_UPDATE = 'erziehung_update'
ERZIEHUNG_ZUGEWIESEN = 'erziehung_zugewiesen'
FOERDERPLAN_NEU = 'foerderplan_neu'
FOERDERPLAN_UPDATE = 'foerderplan_update'
ELTERNKONTAKT_NEU = 'elternkontakt_neu'
ELTERNTERMIN = 'elterntermin'
KIND_NEU_IN_KLASSE = 'kind_neu_in_klasse'
KLASSE_ZUGEORDNET = 'klasse_zugeordnet'
FOERDERKURS_OHNE_PLAN = 'foerderkurs_ohne_plan'

# Reihenfolge = Anzeige in den Kontoeinstellungen.
ARTEN = {
    ERZIEHUNG_NEU: (
        'Neues Ereignis in der erzieherischen Arbeit',
        'Eine andere Lehrkraft trägt ein Ereignis zu einem Kind Ihrer Klasse ein.',
    ),
    ERZIEHUNG_UPDATE: (
        'Ereignis aktualisiert',
        'Ein Ereignis zu einem Kind Ihrer Klasse, das Sie angelegt haben oder für das '
        'Sie zuständig sind, wird geändert.',
    ),
    ERZIEHUNG_ZUGEWIESEN: (
        'Ereignis zugewiesen',
        'Ihnen wird die Zuständigkeit für ein Ereignis übertragen.',
    ),
    FOERDERPLAN_NEU: (
        'Neuer Förderplan',
        'Eine andere Lehrkraft legt einen Förderplan für ein Kind Ihrer Klasse an.',
    ),
    FOERDERPLAN_UPDATE: (
        'Förderplan bearbeitet oder evaluiert',
        'Ein Förderplan zu einem Kind Ihrer Klasse oder einer, den Sie angelegt haben, '
        'wird geändert oder evaluiert.',
    ),
    ELTERNKONTAKT_NEU: (
        'Neuer Elternkontakt',
        'Eine andere Lehrkraft trägt eine Notiz oder ein Gesprächsprotokoll zu einem '
        'Kind Ihrer Klasse ein.',
    ),
    ELTERNTERMIN: (
        'Anstehender Elterntermin',
        'Erinnerung, wenn der vereinbarte nächste Termin aus einem Elterngespräch in '
        'den nächsten drei Tagen liegt.',
    ),
    KIND_NEU_IN_KLASSE: (
        'Kind neu in Ihrer Klasse',
        'Ein Kind wechselt in eine Klasse, die Sie leiten.',
    ),
    KLASSE_ZUGEORDNET: (
        'Neue Klassenzuordnung',
        'Sie werden einer Klasse als Klassenleitung oder Fachlehrkraft zugeordnet.',
    ),
    FOERDERKURS_OHNE_PLAN: (
        'Förderkurs ohne Förderplan',
        'Ein Kind Ihrer Klasse oder Ihres Kurses besucht einen Förderkurs, hat aber '
        'keinen aktiven Förderplan im selben Fach. Die Erinnerung wiederholt sich.',
    ),
}

MAIL_TAKTE = {
    'sofort': 'Sofort (nach wenigen Minuten)',
    'taeglich': 'Einmal täglich gesammelt',
    'aus': 'Keine E-Mails',
}
MAIL_TAKT_STANDARD = 'taeglich'


def normalisiere_takt(wert):
    wert = (wert or '').strip().lower()
    return wert if wert in MAIL_TAKTE else MAIL_TAKT_STANDARD

ERINNERUNG_VORLAUF_TAGE = 3


def aktuelle_user_id():
    """Die ausloesende Lehrkraft - ausserhalb einer Anfrage (Versandlauf) keine."""
    if not has_request_context():
        return None
    return getattr(current_user, 'id', None)


def kind_name(schueler):
    if not schueler:
        return 'Unbekanntes Kind'
    name = f'{schueler.vorname} {schueler.nachname}'.strip()
    return f'{name} ({schueler.klasse})' if schueler.klasse else name


def klassenleitungen(klassen_namen):
    """User-IDs der Klassenleitungen dieser Klassen."""
    namen = {name for name in klassen_namen if name}
    if not namen:
        return set()
    return {
        zuordnung.user_id
        for zuordnung in UserKlassenzuordnung.query.filter(
            UserKlassenzuordnung.rolle == 'klassenleitung',
            UserKlassenzuordnung.klasse.in_(namen),
        ).all()
    }


def klassenleitungen_fuer_kinder(kinder):
    return klassenleitungen(kind.klasse for kind in kinder if kind)


def abbestellte_arten(user_id):
    return {
        eintrag.art
        for eintrag in BenachrichtigungAbbestellt.query.filter_by(user_id=user_id).all()
    }


def benachrichtige(art, empfaenger, titel, text=None, ziel=None, ausloeser_id=None, ausser=()):
    """Legt Benachrichtigungen an und gibt die tatsächlichen Empfänger zurück.

    ausloeser_id: wer den Vorgang ausgelöst hat - bekommt nichts. Ohne Angabe die
    angemeldete Lehrkraft. ausser: bereits auf anderem Weg Benachrichtigte.
    Schreibt in die Session, committet nicht - das tut der Aufrufer mit seinem
    eigentlichen Vorgang, damit beides gemeinsam gespeichert wird oder keins.
    """
    if art not in ARTEN:
        raise ValueError(f'Unbekannte Benachrichtigungsart: {art}')
    if ausloeser_id is None:
        ausloeser_id = aktuelle_user_id()

    ids = {int(user_id) for user_id in empfaenger if user_id}
    ids -= {ausloeser_id}
    ids -= set(ausser)
    if not ids:
        return set()

    abbestellt = {
        eintrag.user_id
        for eintrag in BenachrichtigungAbbestellt.query.filter(
            BenachrichtigungAbbestellt.art == art,
            BenachrichtigungAbbestellt.user_id.in_(ids),
        ).all()
    }
    vorhanden = {user.id for user in User.query.filter(User.id.in_(ids)).all()}

    zugestellt = set()
    for user_id in sorted((ids & vorhanden) - abbestellt):
        db.session.add(Notification(
            user_id=user_id,
            kind=art,
            title=titel[:200],
            message=text,
            target_url=ziel,
        ))
        zugestellt.add(user_id)
    return zugestellt


def ausloeser_name():
    if not has_request_context() or not getattr(current_user, 'is_authenticated', False):
        return None
    return current_user.display_name


def von_wem():
    name = ausloeser_name()
    return f' – von {name}' if name else ''


# ----------------------------------------------------------------------
# Zeitgesteuerte Erinnerungen (laufen im taeglichen Versandlauf)
# ----------------------------------------------------------------------

def erinnere_an_foerderkurse(heute=None):
    """Erinnert an Förderkurskinder ohne aktiven Förderplan im selben Fach.

    Wiederholt sich alle paar Tage (foerderkurs.ERINNERUNG_ABSTAND_TAGE), bis der
    Plan da ist oder die Teilnahme endet. Empfänger sind die Klassenleitung des
    Kindes und die Leitung des Kurses.
    """
    from foerderkurs import faellige_erinnerungen, zustaendige_user_ids

    heute = heute or utc_now().date()
    anzahl = 0
    for teilnahme in faellige_erinnerungen(heute):
        kurs = teilnahme.kurs
        anzahl += len(benachrichtige(
            FOERDERKURS_OHNE_PLAN,
            zustaendige_user_ids(teilnahme),
            f'Förderkurs ohne Förderplan: {kind_name(teilnahme.schueler)}',
            text=f'{kurs.name} ({kurs.fach.name}) – es fehlt ein aktiver Förderplan im Fach {kurs.fach.name}.',
            ziel=url_for('foerderkurs.ohne_plan') if has_request_context() else '/foerderkurse/ohne-plan',
            ausloeser_id=0,
        ))
        teilnahme.erinnert_am = heute
    return anzahl


def erinnere_an_elterntermine(heute=None):
    """Erinnert an vereinbarte Folgetermine aus Elterngesprächen.

    Der Termin muss in den nächsten drei Tagen liegen. Damit ein ausgefallener
    Lauf nichts verschluckt, gilt das ganze Fenster. Am Kontakt steht, für
    welches Termindatum schon erinnert wurde - so kommt die Erinnerung einmal,
    auch wenn jemand sie unter der Glocke löscht, und neu, wenn sich der Termin
    verschiebt.
    """
    heute = heute or utc_now().date()
    bis = heute + timedelta(days=ERINNERUNG_VORLAUF_TAGE)
    kontakte = (
        Elternkontakt.query
        .join(Schueler, Elternkontakt.schueler_id == Schueler.id)
        .filter(
            Schueler.is_active.is_(True),
            Elternkontakt.naechster_termin.isnot(None),
            Elternkontakt.naechster_termin >= heute,
            Elternkontakt.naechster_termin <= bis,
        )
        .all()
    )

    anzahl = 0
    for kontakt in kontakte:
        if kontakt.erinnert_fuer_termin == kontakt.naechster_termin:
            continue
        empfaenger = klassenleitungen_fuer_kinder([kontakt.schueler]) | {kontakt.user_id}
        anzahl += len(benachrichtige(
            ELTERNTERMIN,
            empfaenger,
            f'Elterntermin am {kontakt.naechster_termin.strftime("%d.%m.%Y")}: {kind_name(kontakt.schueler)}',
            text=kontakt.betreff or kontakt.kontaktform or None,
            ziel=url_for('erfassung.elternkontakt_view', kontakt_id=kontakt.id),
            ausloeser_id=0,
        ))
        kontakt.erinnert_fuer_termin = kontakt.naechster_termin
    return anzahl
