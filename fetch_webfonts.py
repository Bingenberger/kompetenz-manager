#!/usr/bin/env python3
"""Laedt die Webfonts der Oberflaeche nach static/vendor/fonts.

Die Anwendung bindet keine Schriften von Google ein: das wuerde bei jedem
Seitenaufruf die IP-Adresse der Lehrkraft an einen Dritten uebermitteln und die
Oberflaeche von der Internetverbindung der Schule abhaengig machen. Dieses
Skript holt die Dateien einmalig und schreibt eine fonts.css mit relativen
Pfaden.

Nunito und Sora sind variable Schriften: eine Datei deckt alle Schnitte ab.
Deshalb wird der Gewichtsbereich angefragt (wght@200..1000) und im
@font-face als Bereich deklariert. Eine variable Datei unter einem festen
font-weight einzubinden waere falsch - der Browser variiert die Achse dann
nicht und alle Texte wirken gleich stark.

    python fetch_webfonts.py            # laedt und schreibt fonts.css
    python fetch_webfonts.py --check    # prueft nur, ob etwas Neueres vorliegt

Danach: python -m unittest tests.test_no_external_assets
"""

import argparse
import hashlib
import re
import sys
import urllib.request
from pathlib import Path

# Familie -> Gewichtsbereich der Variablen-Achse, wie Google ihn ausweist.
FAMILIEN = {
    'Nunito': '200..1000',
    'Sora': '100..800',
}

# Fuer eine deutsche Schule genuegen diese Zeichensaetze. latin-ext deckt Namen
# mit ost- und mitteleuropaeischer Diakritik ab (ł, ő, ć, š ...).
ZEICHENSAETZE = {'latin', 'latin-ext'}

# Ohne Browser-Kennung liefert Google TTF statt WOFF2.
USER_AGENT = (
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/120.0.0.0 Safari/537.36'
)

ZIEL = Path(__file__).resolve().parent / 'static' / 'vendor' / 'fonts'

KOPF = """/* Lokal ausgelieferte Schriften - keine Verbindung zu Google.
 *
 * Erzeugt von fetch_webfonts.py. Nicht von Hand bearbeiten.
 * Nunito und Sora stehen unter der SIL Open Font License 1.1,
 * siehe OFL-nunito.txt und OFL-sora.txt.
 *
 * Variable Schriften: eine Datei je Zeichensatz, Gewicht als Bereich
 * deklariert, damit der Browser die Achse variiert.
 */
"""


def hole(url):
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    return urllib.request.urlopen(request, timeout=30).read()


def css_url():
    familien = '&'.join(
        f'family={name}:wght@{bereich}' for name, bereich in FAMILIEN.items()
    )
    return f'https://fonts.googleapis.com/css2?{familien}&display=swap'


def parse_faces(css):
    """Liest (Zeichensatz, Familie, Gewichtsbereich, URL, unicode-range) je Block."""
    faces = []
    muster = re.compile(r'/\*\s*([\w-]+)\s*\*/\s*@font-face\s*\{([^}]*)\}')
    for zeichensatz, block in muster.findall(css):
        if zeichensatz not in ZEICHENSAETZE:
            continue
        familie = re.search(r"font-family:\s*'([^']+)'", block).group(1)
        gewicht = re.search(r'font-weight:\s*([^;]+);', block).group(1).strip()
        url = re.search(r"url\(([^)]+)\)\s*format\('woff2'\)", block).group(1)
        bereich = re.search(r'unicode-range:\s*([^;]+);', block)
        faces.append({
            'zeichensatz': zeichensatz,
            'familie': familie,
            'gewicht': gewicht,
            'url': url,
            'unicode_range': bereich.group(1).strip() if bereich else None,
        })
    return faces


def dateiname(face):
    return f"{face['familie'].lower().replace(' ', '-')}-{face['zeichensatz']}.woff2"


def baue_css(faces):
    teile = [KOPF]
    for face in faces:
        zeilen = [
            '@font-face {',
            f"  font-family: '{face['familie']}';",
            '  font-style: normal;',
            f"  font-weight: {face['gewicht']};",
            '  font-display: swap;',
            f"  src: url('{dateiname(face)}') format('woff2');",
        ]
        if face['unicode_range']:
            zeilen.append(f"  unicode-range: {face['unicode_range']};")
        zeilen.append('}')
        teile.append(
            f"\n/* {face['familie']} {face['gewicht']} - {face['zeichensatz']} */\n"
            + '\n'.join(zeilen)
        )
    return '\n'.join(teile) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        '--check',
        action='store_true',
        help='Nur prüfen, ob sich die Dateien geändert haben. Schreibt nichts.',
    )
    args = parser.parse_args()

    print(f'Abfrage: {css_url()}')
    try:
        css = hole(css_url()).decode('utf-8')
    except Exception as fehler:
        print(f'FEHLER: Google Fonts nicht erreichbar: {fehler}')
        return 1

    faces = parse_faces(css)
    if not faces:
        print('FEHLER: Keine passenden @font-face-Blöcke erhalten.')
        return 1

    erwartet = len(FAMILIEN) * len(ZEICHENSAETZE)
    if len(faces) != erwartet:
        print(f'WARNUNG: {len(faces)} Blöcke erhalten, {erwartet} erwartet.')

    ZIEL.mkdir(parents=True, exist_ok=True)
    geaendert = []

    for face in faces:
        name = dateiname(face)
        daten = hole(face['url'])
        if not daten.startswith(b'wOF2'):
            print(f'FEHLER: {name} ist keine WOFF2-Datei.')
            return 1

        pfad = ZIEL / name
        neu = hashlib.sha256(daten).hexdigest()
        alt = hashlib.sha256(pfad.read_bytes()).hexdigest() if pfad.is_file() else None

        zustand = 'unverändert' if neu == alt else ('neu' if alt is None else 'geändert')
        if zustand != 'unverändert':
            geaendert.append(name)
        print(f"  {name:28} {face['gewicht']:>10}  {len(daten):>7} B  {zustand}")

        if not args.check:
            pfad.write_bytes(daten)

    css_pfad = ZIEL / 'fonts.css'
    neues_css = baue_css(faces)
    css_geaendert = not css_pfad.is_file() or css_pfad.read_text(encoding='utf-8') != neues_css
    if css_geaendert:
        geaendert.append('fonts.css')
    if not args.check:
        css_pfad.write_text(neues_css, encoding='utf-8')

    print()
    if args.check:
        if geaendert:
            print('Änderungen vorhanden: ' + ', '.join(geaendert))
            print('Zum Übernehmen ohne --check erneut ausführen.')
            return 1
        print('Alles aktuell.')
        return 0

    print(f'{len(faces)} Schriftdatei(en) und fonts.css geschrieben nach {ZIEL}')
    if geaendert:
        print('Geändert: ' + ', '.join(geaendert))
    else:
        print('Inhaltlich unverändert.')
    print('Bitte prüfen: python -m unittest tests.test_no_external_assets')
    return 0


if __name__ == '__main__':
    sys.exit(main())
