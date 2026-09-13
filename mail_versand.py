"""E-Mail-Versand der Benachrichtigungen.

Der Versand läuft nicht in der Anfrage, die eine Benachrichtigung auslöst,
sondern in einem eigenen Lauf (benachrichtigungen_senden.py, per Cron). So
bremst ein langsamer oder gestörter Mailserver niemanden beim Speichern, und
was nicht zugestellt werden konnte, versucht der nächste Lauf erneut.

Jede Benachrichtigung trägt `mailed_at`: gesetzt, sobald sie erledigt ist -
verschickt oder bewusst übergangen (keine Adresse, Takt "aus", schon gelesen,
zu alt). Offen bleibt nur, was noch verschickt werden soll.

Konfiguration über Umgebungsvariablen (siehe deploy/README_DEPLOY.md):
MAIL_SERVER, MAIL_PORT, MAIL_SECURITY (starttls|ssl|none), MAIL_USERNAME,
MAIL_PASSWORD, MAIL_FROM, APP_BASE_URL.
"""

import logging
import smtplib
import ssl
from datetime import timedelta
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from flask import current_app

from extensions import db
from models import Notification, User
from time_utils import utc_now

log = logging.getLogger(__name__)

# Aeltere offene Benachrichtigungen werden nicht mehr verschickt - etwa nach
# einer laengeren Stoerung oder wenn der Versand erst spaeter eingerichtet wird.
HOECHSTALTER = timedelta(days=7)


class MailNichtKonfiguriert(RuntimeError):
    pass


# Schreibweisen, die in Env-Dateien vorkommen. systemd nimmt - anders als die
# Shell - einen Kommentar hinter dem Wert mit in den Wert auf ("ssl  # 465");
# deshalb wird alles ab '#' abgeschnitten, bevor verglichen wird.
SICHERHEIT_ALIASSE = {
    'starttls': 'starttls', 'tls': 'starttls', 'start_tls': 'starttls', 'start-tls': 'starttls',
    'ssl': 'ssl', 'ssl/tls': 'ssl', 'ssl_tls': 'ssl', 'smtps': 'ssl', 'implicit': 'ssl',
    'none': 'none', 'plain': 'none', 'keine': 'none', 'off': 'none', '': 'starttls',
}


def _wert(config, schluessel):
    """Env-Wert ohne Anführungszeichen und ohne Kommentar hinter dem Wert."""
    roh = str(config.get(schluessel) or '')
    roh = roh.split('#', 1)[0].strip()
    if len(roh) >= 2 and roh[0] == roh[-1] and roh[0] in ('"', "'"):
        roh = roh[1:-1].strip()
    return roh


def mail_konfiguration(config=None):
    """Die SMTP-Einstellungen oder None, wenn kein Server eingetragen ist."""
    config = config if config is not None else current_app.config
    server = _wert(config, 'MAIL_SERVER')
    if not server:
        return None
    sicherheit_roh = str(config.get('MAIL_SECURITY') or '').strip()
    sicherheit = SICHERHEIT_ALIASSE.get(_wert(config, 'MAIL_SECURITY').lower())
    sicherheit_unbekannt = sicherheit is None
    if sicherheit_unbekannt:
        sicherheit = 'starttls'
    standard_port = {'starttls': 587, 'ssl': 465, 'none': 25}[sicherheit]
    try:
        port = int(_wert(config, 'MAIL_PORT') or standard_port)
    except (TypeError, ValueError):
        port = standard_port
    benutzer = _wert(config, 'MAIL_USERNAME')
    return {
        'server': server,
        'port': port,
        'sicherheit': sicherheit,
        'sicherheit_roh': sicherheit_roh,
        'sicherheit_unbekannt': sicherheit_unbekannt,
        # Ein falsches Paar - SSL auf 587 oder STARTTLS auf 465 - haengt beim
        # Handshake, statt einen klaren Fehler zu liefern.
        'port_passt_nicht': (sicherheit == 'ssl' and port == 587) or (sicherheit == 'starttls' and port == 465),
        'benutzer': benutzer,
        'passwort': config.get('MAIL_PASSWORD') or '',
        'absender': _wert(config, 'MAIL_FROM') or benutzer,
        'basis_url': _wert(config, 'APP_BASE_URL').rstrip('/'),
    }


def sende_mail(an, betreff, text, konfiguration=None):
    """Verschickt eine Text-E-Mail. Wirft bei jedem Fehler."""
    konfiguration = konfiguration or mail_konfiguration()
    if not konfiguration:
        raise MailNichtKonfiguriert('Kein Mailserver eingetragen (MAIL_SERVER).')

    nachricht = EmailMessage()
    nachricht['Subject'] = betreff
    nachricht['From'] = konfiguration['absender']
    nachricht['To'] = an
    nachricht['Date'] = formatdate(localtime=True)
    nachricht['Message-ID'] = make_msgid(domain=konfiguration['absender'].rpartition('@')[2] or None)
    nachricht['Auto-Submitted'] = 'auto-generated'
    nachricht.set_content(text)

    # Tests und lokale Entwicklung: Nachrichten sammeln statt verschicken.
    postausgang = current_app.config.get('MAIL_OUTBOX')
    if postausgang is not None:
        postausgang.append(nachricht)
        return

    kontext = ssl.create_default_context()
    if konfiguration['sicherheit'] == 'ssl':
        verbindung = smtplib.SMTP_SSL(konfiguration['server'], konfiguration['port'], timeout=30, context=kontext)
    else:
        verbindung = smtplib.SMTP(konfiguration['server'], konfiguration['port'], timeout=30)
    with verbindung:
        if konfiguration['sicherheit'] == 'starttls':
            verbindung.starttls(context=kontext)
        if konfiguration['benutzer']:
            verbindung.login(konfiguration['benutzer'], konfiguration['passwort'])
        verbindung.send_message(nachricht)


def _link(basis_url, pfad):
    if not pfad:
        return None
    if pfad.startswith(('http://', 'https://')):
        return pfad
    return f'{basis_url}{pfad}' if basis_url else None


def mail_inhalt(user, benachrichtigungen, basis_url):
    """(Betreff, Text) für eine oder mehrere Benachrichtigungen."""
    if len(benachrichtigungen) == 1:
        betreff = f'KompetenzKompass: {benachrichtigungen[0].title}'
    else:
        betreff = f'KompetenzKompass: {len(benachrichtigungen)} neue Benachrichtigungen'

    zeilen = [f'Hallo {user.display_name},', '']
    if len(benachrichtigungen) == 1:
        zeilen.append('es gibt eine neue Benachrichtigung im KompetenzKompass:')
    else:
        zeilen.append(f'es gibt {len(benachrichtigungen)} neue Benachrichtigungen im KompetenzKompass:')
    zeilen.append('')

    for benachrichtigung in benachrichtigungen:
        zeilen.append(f'- {benachrichtigung.title}')
        if benachrichtigung.message:
            zeilen.append(f'  {benachrichtigung.message}')
        link = _link(basis_url, benachrichtigung.target_url)
        if link:
            zeilen.append(f'  {link}')
        zeilen.append('')

    zeilen.append('--')
    zeilen.append('Diese E-Mail wurde automatisch verschickt.')
    konto = _link(basis_url, '/konto')
    if konto:
        zeilen.append(f'Welche Benachrichtigungen Sie wann erhalten, stellen Sie hier ein: {konto}')
    else:
        zeilen.append('Welche Benachrichtigungen Sie wann erhalten, stellen Sie in Ihrem Konto ein.')
    return betreff, '\n'.join(zeilen)


def versende(takt, jetzt=None):
    """Verschickt die offenen Benachrichtigungen aller Lehrkräfte mit diesem Takt.

    Gibt ein dict mit den Zählern zurück. Fehler beim Versand an eine Lehrkraft
    halten die übrigen nicht auf; ihre Benachrichtigungen bleiben offen.
    """
    jetzt = jetzt or utc_now()
    ergebnis = {'mails': 0, 'benachrichtigungen': 0, 'uebergangen': 0, 'fehler': 0}

    # Erst aufraeumen, was ohnehin nie verschickt wird - unabhaengig vom Takt
    # dieses Laufs, damit sich nichts ansammelt.
    ergebnis['uebergangen'] += _erledige_ohne_versand(jetzt)

    konfiguration = mail_konfiguration()
    if not konfiguration:
        raise MailNichtKonfiguriert('Kein Mailserver eingetragen (MAIL_SERVER).')

    lehrkraefte = (
        User.query
        .filter(User.mail_takt == takt, User.email.isnot(None), User.email != '')
        .order_by(User.id)
        .all()
    )
    for user in lehrkraefte:
        offen = (
            Notification.query
            .filter(Notification.user_id == user.id, Notification.mailed_at.is_(None))
            .order_by(Notification.created_at.asc(), Notification.id.asc())
            .all()
        )
        if not offen:
            continue
        betreff, text = mail_inhalt(user, offen, konfiguration['basis_url'])
        try:
            sende_mail(user.email, betreff, text, konfiguration)
        except Exception:  # noqa: BLE001 - jeder Fehler: spaeter erneut versuchen
            log.exception('Benachrichtigungsmail an Benutzer %s fehlgeschlagen', user.id)
            db.session.rollback()
            ergebnis['fehler'] += 1
            continue
        for benachrichtigung in offen:
            benachrichtigung.mailed_at = jetzt
        db.session.commit()
        ergebnis['mails'] += 1
        ergebnis['benachrichtigungen'] += len(offen)
    return ergebnis


def _erledige_ohne_versand(jetzt):
    """Markiert offene Benachrichtigungen, die keine E-Mail werden."""
    grenze = jetzt - HOECHSTALTER
    ohne_mail = [
        user.id for user in User.query.filter(
            db.or_(User.email.is_(None), User.email == '', User.mail_takt == 'aus')
        ).all()
    ]
    bedingung = db.or_(
        Notification.is_read.is_(True),
        Notification.created_at < grenze,
        Notification.user_id.in_(ohne_mail) if ohne_mail else db.false(),
    )
    anzahl = (
        Notification.query
        .filter(Notification.mailed_at.is_(None), bedingung)
        .update({Notification.mailed_at: jetzt}, synchronize_session=False)
    )
    db.session.commit()
    return anzahl
