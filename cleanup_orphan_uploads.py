#!/usr/bin/env python3
"""Findet Upload-Dateien ohne Datenbankbezug und entfernt sie auf Wunsch.

Bis einschliesslich der Version, die das Loeschen von Dateien nachgezogen hat,
entfernte kein Loeschpfad der Anwendung eine hochgeladene Datei. In gewachsenen
Installationen liegen deshalb Fotos und PDFs, auf die kein Datensatz mehr
zeigt: nicht erreichbar, nicht loeschbar, aber personenbezogen.

Der Lauf berichtet standardmaessig nur. Geloescht wird ausschliesslich mit
--delete und nach Rueckfrage.

    python cleanup_orphan_uploads.py              # nur berichten
    python cleanup_orphan_uploads.py --delete     # nach Rueckfrage loeschen
    python cleanup_orphan_uploads.py --delete --yes

Wichtig: Der Lauf braucht dieselbe DATABASE_URL wie der Dienst. Zeigt er auf
eine andere oder leere Datenbank, erscheint jede Datei als verwaist. Das
Skript prueft das und bricht in dem Fall ab.
"""

import argparse
import re
import sys

from app import app, db
from models import Beobachtung, ErziehungsEreignisAnhang, WorkPlanTaskAttachment
from uploads import get_legacy_upload_root, get_protected_upload_root, normalize_upload_path


PASSWORD_IN_URL = re.compile(r'(://[^:/@]+):[^@]*@')


def mask_url(url):
    """Entfernt das Passwort aus einer Verbindungs-URL."""
    return PASSWORD_IN_URL.sub(r'\1:***@', str(url or ''))


def masked_database_url():
    return mask_url(db.engine.url)


def referenced_paths():
    """Alle Upload-Pfade, auf die ein Datensatz zeigt - normalisiert."""
    quellen = (
        (Beobachtung, 'foto_pfad', 'Beobachtungsfotos'),
        (ErziehungsEreignisAnhang, 'file_path', 'Ereignisanhänge'),
        (WorkPlanTaskAttachment, 'file_path', 'Aufgabenfotos'),
    )

    pfade = set()
    pro_quelle = {}
    for model, spalte, label in quellen:
        werte = [
            row[0] for row in
            db.session.query(getattr(model, spalte)).filter(getattr(model, spalte).isnot(None)).all()
        ]
        normalisiert = {normalize_upload_path(wert) for wert in werte}
        normalisiert.discard(None)
        pro_quelle[label] = len(normalisiert)
        pfade |= normalisiert

    return pfade, pro_quelle


def files_on_disk(root):
    """Relative Pfade aller Dateien unter einer Upload-Wurzel."""
    if not root.exists():
        return {}
    gefunden = {}
    for pfad in sorted(root.rglob('*')):
        if pfad.is_file():
            gefunden[pfad.relative_to(root).as_posix()] = pfad
    return gefunden


def human_size(anzahl_bytes):
    einheiten = ['B', 'KB', 'MB', 'GB']
    wert = float(anzahl_bytes)
    for einheit in einheiten:
        if wert < 1024 or einheit == einheiten[-1]:
            return f'{wert:.1f} {einheit}' if einheit != 'B' else f'{int(wert)} B'
        wert /= 1024
    return f'{wert:.1f} GB'


def report(delete=False, assume_yes=False, force=False, limit=20, flask_app=None):
    # flask_app dient Tests; im Betrieb gilt die Anwendung aus app.py.
    with (flask_app or app).app_context():
        print(f'Datenbank: {masked_database_url()}')

        bezogen, pro_quelle = referenced_paths()
        for label, anzahl in pro_quelle.items():
            print(f'  {label}: {anzahl} referenzierte Datei(en)')
        print(f'  Referenziert insgesamt: {len(bezogen)}')
        print()

        wurzeln = [
            ('geschützt', get_protected_upload_root()),
            ('Legacy', get_legacy_upload_root()),
        ]

        verwaist = []
        dateien_gesamt = 0
        vorhandene_pfade = set()

        for label, root in wurzeln:
            dateien = files_on_disk(root)
            dateien_gesamt += len(dateien)
            vorhandene_pfade |= set(dateien)
            ohne_bezug = [(rel, pfad) for rel, pfad in dateien.items() if rel not in bezogen]
            verwaist.extend((label, rel, pfad) for rel, pfad in ohne_bezug)
            print(f'{label}: {root}')
            print(f'  Dateien: {len(dateien)}, davon ohne Datenbankbezug: {len(ohne_bezug)}')

        print()

        # Schutz gegen den gefaehrlichsten Bedienfehler: zeigt DATABASE_URL auf
        # eine andere oder leere Datenbank, sieht jede Datei verwaist aus.
        if dateien_gesamt and not bezogen:
            print('ABBRUCH: Es liegen Dateien vor, aber die Datenbank enthält keinen einzigen')
            print('Verweis darauf. Das deutet auf eine falsche oder leere DATABASE_URL hin.')
            print('Bitte die Verbindung prüfen. Mit --force lässt sich der Lauf erzwingen.')
            if not force:
                return 2
            print('--force gesetzt, es wird fortgesetzt.')
            print()

        fehlend = sorted(bezogen - vorhandene_pfade)
        if fehlend:
            print(f'Hinweis: {len(fehlend)} Datensatz/Datensätze zeigen auf eine fehlende Datei.')
            for rel in fehlend[:limit]:
                print(f'  fehlt: {rel}')
            if len(fehlend) > limit:
                print(f'  ... und {len(fehlend) - limit} weitere')
            print('Diese werden von diesem Lauf nicht angefasst.')
            print()

        if not verwaist:
            print('Keine verwaisten Dateien gefunden.')
            return 0

        gesamt_bytes = sum(pfad.stat().st_size for _, _, pfad in verwaist)
        print(f'Verwaiste Dateien: {len(verwaist)} ({human_size(gesamt_bytes)})')
        for label, rel, pfad in verwaist[:limit]:
            print(f'  [{label}] {rel} ({human_size(pfad.stat().st_size)})')
        if len(verwaist) > limit:
            print(f'  ... und {len(verwaist) - limit} weitere')
        print()

        if not delete:
            print('Es wurde nichts verändert. Zum Löschen: --delete')
            return 0

        if not assume_yes:
            if not sys.stdin.isatty():
                print('ABBRUCH: Keine Eingabe möglich. Für einen unbeaufsichtigten Lauf --yes setzen.')
                return 2
            antwort = input(f'{len(verwaist)} Datei(en) endgültig löschen? [j/N] ').strip().lower()
            if antwort not in {'j', 'ja', 'y', 'yes'}:
                print('Abgebrochen, es wurde nichts verändert.')
                return 0

        geloescht = 0
        fehlgeschlagen = 0
        for _, rel, pfad in verwaist:
            try:
                pfad.unlink()
                geloescht += 1
            except OSError as fehler:
                print(f'  Fehler bei {rel}: {fehler}')
                fehlgeschlagen += 1

        print(f'Gelöscht: {geloescht} Datei(en) ({human_size(gesamt_bytes)}).')
        if fehlgeschlagen:
            print(f'Fehlgeschlagen: {fehlgeschlagen}')
            return 1
        return 0


def main():
    parser = argparse.ArgumentParser(
        description='Findet Upload-Dateien ohne Datenbankbezug und entfernt sie auf Wunsch.',
    )
    parser.add_argument(
        '--delete',
        action='store_true',
        help='Verwaiste Dateien löschen. Ohne diese Option wird nur berichtet.',
    )
    parser.add_argument(
        '--yes',
        action='store_true',
        help='Rückfrage überspringen. Nur zusammen mit --delete sinnvoll.',
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='Auch fortsetzen, wenn die Datenbank keinen einzigen Dateiverweis enthält.',
    )
    parser.add_argument(
        '--limit',
        type=int,
        default=20,
        help='Wie viele Dateien einzeln aufgelistet werden (Standard: 20).',
    )
    args = parser.parse_args()
    raise SystemExit(report(
        delete=args.delete,
        assume_yes=args.yes,
        force=args.force,
        limit=args.limit,
    ))


if __name__ == '__main__':
    main()
