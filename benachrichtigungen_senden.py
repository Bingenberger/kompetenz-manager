"""Versandlauf für Benachrichtigungen - wird per Cron aufgerufen.

    python benachrichtigungen_senden.py sofort     # alle fünf Minuten
    python benachrichtigungen_senden.py taeglich   # einmal am Tag

Der tägliche Lauf legt zuerst die zeitgesteuerten Erinnerungen an (etwa
Elterntermine) und verschickt dann die Sammelmails. Ist kein Mailserver
eingerichtet, entstehen die Erinnerungen trotzdem - unter der Glocke.

Einrichtung: deploy/install_benachrichtigungen.sh
"""

import argparse
import sys

from app import app
from benachrichtigungen import erinnere_an_elterntermine
from extensions import db
from mail_versand import MailNichtKonfiguriert, versende
from time_utils import utc_now


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('takt', choices=['sofort', 'taeglich'])
    args = parser.parse_args(argv)

    basis_url = app.config.get('APP_BASE_URL') or 'http://localhost'
    # url_for braucht einen Anfragekontext; die Links bleiben relativ und
    # bekommen beim Versand APP_BASE_URL vorangestellt.
    with app.test_request_context('/', base_url=basis_url):
        zeit = utc_now().strftime('%Y-%m-%d %H:%M')
        if args.takt == 'taeglich':
            erinnerungen = erinnere_an_elterntermine()
            db.session.commit()
            print(f'{zeit} Erinnerungen angelegt: {erinnerungen}')
        try:
            ergebnis = versende(args.takt)
        except MailNichtKonfiguriert:
            print(f'{zeit} Kein Mailserver eingerichtet (MAIL_SERVER) - nur Glocke.')
            return 0
        print(
            f"{zeit} {args.takt}: {ergebnis['mails']} Mail(s) mit {ergebnis['benachrichtigungen']} "
            f"Benachrichtigung(en), {ergebnis['uebergangen']} ohne Versand erledigt, "
            f"{ergebnis['fehler']} Fehler"
        )
        return 1 if ergebnis['fehler'] else 0


if __name__ == '__main__':
    sys.exit(main())
