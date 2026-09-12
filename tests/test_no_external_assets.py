"""Die Oberflaeche darf nichts aus dem Internet nachladen.

Die Anwendung verarbeitet personenbezogene Daten von Kindern. Ein
eingebundenes CDN uebermittelt bei jedem Seitenaufruf die IP-Adresse der
Lehrkraft an einen Dritten - bei Google Fonts genau die Konstellation, die das
LG Muenchen I im Januar 2022 als Verstoss gewertet hat. Ausserdem macht es die
Anwendung vom Internet abhaengig, obwohl Server und Datenbank im Haus stehen.

Bootstrap und die Schriften liegen deshalb unter static/vendor. Dieser Test
haelt den Zustand fest, damit eine spaetere Aenderung nicht unbemerkt wieder
ein CDN einbindet.
"""

import hashlib
import re
import unittest
from pathlib import Path

PROJEKT = Path(__file__).resolve().parent.parent

# Verweise auf w3.org sind Namensraum-Bezeichner in SVG und XML, keine Ladevorgaenge.
ERLAUBT = re.compile(r'https?://(?:www\.)?w3\.org/')

# Nur Attribute, die den Browser wirklich etwas laden lassen.
LADEND = re.compile(
    r'(?:src|href)\s*=\s*["\'](?P<url>(?:https?:)?//[^"\']+)["\']',
    re.IGNORECASE,
)

CSS_LADEND = re.compile(r'url\(\s*["\']?(?P<url>(?:https?:)?//[^)"\']+)', re.IGNORECASE)


class NoExternalAssetsTestCase(unittest.TestCase):
    def _dateien(self, *muster):
        for m in muster:
            for pfad in sorted(PROJEKT.glob(m)):
                if 'vendor' in pfad.parts and pfad.suffix in {'.txt'}:
                    continue
                yield pfad

    def test_templates_load_nothing_from_the_internet(self):
        treffer = []
        for pfad in self._dateien('templates/**/*.html'):
            inhalt = pfad.read_text(encoding='utf-8')
            for match in LADEND.finditer(inhalt):
                url = match.group('url')
                if not ERLAUBT.search(url):
                    treffer.append(f'{pfad.relative_to(PROJEKT)}: {url}')

        self.assertEqual(
            [], treffer,
            'Templates laden aus dem Internet:\n  ' + '\n  '.join(treffer),
        )

    def test_local_stylesheets_load_nothing_from_the_internet(self):
        treffer = []
        for pfad in self._dateien('static/**/*.css'):
            inhalt = pfad.read_text(encoding='utf-8')
            for match in CSS_LADEND.finditer(inhalt):
                url = match.group('url')
                if not ERLAUBT.search(url):
                    treffer.append(f'{pfad.relative_to(PROJEKT)}: {url}')

        self.assertEqual(
            [], treffer,
            'Stylesheets laden aus dem Internet:\n  ' + '\n  '.join(treffer),
        )

    def test_vendor_assets_are_present(self):
        """Ohne die Dateien waere die Oberflaeche unformatiert."""
        erwartet = [
            'static/vendor/bootstrap/bootstrap.min.css',
            'static/vendor/bootstrap/bootstrap.bundle.min.js',
            'static/vendor/bootstrap/LICENSE',
            'static/vendor/fonts/fonts.css',
            'static/vendor/fonts/OFL-nunito.txt',
            'static/vendor/fonts/OFL-sora.txt',
            'static/vendor/bootstrap-icons/bootstrap-icons.svg',
        ]
        fehlend = [rel for rel in erwartet if not (PROJEKT / rel).is_file()]
        self.assertEqual([], fehlend, f'Ausgelieferte Dateien fehlen: {fehlend}')

    def test_every_declared_font_file_exists(self):
        """fonts.css darf nur auf Dateien zeigen, die auch vorliegen."""
        fonts_css = PROJEKT / 'static/vendor/fonts/fonts.css'
        inhalt = fonts_css.read_text(encoding='utf-8')
        deklariert = re.findall(r"url\('([^']+)'\)", inhalt)
        self.assertTrue(deklariert, 'fonts.css deklariert keine Schriftdatei')

        fehlend = [
            name for name in deklariert
            if not (fonts_css.parent / name).is_file()
        ]
        self.assertEqual([], fehlend, f'In fonts.css deklariert, aber nicht vorhanden: {fehlend}')

    def test_declared_fonts_are_valid_woff2(self):
        fonts_dir = PROJEKT / 'static/vendor/fonts'
        dateien = sorted(fonts_dir.glob('*.woff2'))
        self.assertTrue(dateien, 'Keine Schriftdateien gefunden')
        for pfad in dateien:
            with open(pfad, 'rb') as handle:
                self.assertEqual(
                    b'wOF2', handle.read(4),
                    f'{pfad.name} ist keine gültige WOFF2-Datei',
                )

    def test_both_font_families_are_covered(self):
        """base.html setzt Nunito fuer Text und Sora fuer Ueberschriften."""
        inhalt = (PROJEKT / 'static/vendor/fonts/fonts.css').read_text(encoding='utf-8')
        familien = set(re.findall(r"font-family:\s*'([^']+)'", inhalt))
        self.assertIn('Nunito', familien)
        self.assertIn('Sora', familien)

    def test_variable_fonts_declare_a_weight_range(self):
        """Nunito und Sora sind variable Schriften.

        Eine variable Datei unter einem festen font-weight einzubinden ist ein
        stiller Fehler: der Browser variiert die Achse dann nicht, und alle
        Ueberschriften wirken gleich stark. Deklariert werden muss der Bereich.
        """
        inhalt = (PROJEKT / 'static/vendor/fonts/fonts.css').read_text(encoding='utf-8')
        bloecke = re.findall(r'@font-face\s*\{([^}]*)\}', inhalt)
        self.assertTrue(bloecke, 'fonts.css enthält keinen @font-face-Block')

        for block in bloecke:
            familie = re.search(r"font-family:\s*'([^']+)'", block).group(1)
            gewicht = re.search(r'font-weight:\s*([^;]+);', block).group(1).strip()
            self.assertRegex(
                gewicht, r'^\d+\s+\d+$',
                f'{familie} deklariert font-weight "{gewicht}" statt eines Bereichs',
            )

    def test_each_family_and_subset_appears_once(self):
        """Eine variable Datei je Familie und Zeichensatz - keine Duplikate.

        Die erste Umsetzung fragte diskrete Schnitte an und erhielt viermal
        dieselbe Datei unter verschiedenen Namen.
        """
        fonts_dir = PROJEKT / 'static/vendor/fonts'
        inhalt = (fonts_dir / 'fonts.css').read_text(encoding='utf-8')
        dateien = re.findall(r"url\('([^']+)'\)", inhalt)
        self.assertEqual(
            sorted(dateien), sorted(set(dateien)),
            'Dieselbe Schriftdatei wird mehrfach eingebunden',
        )

        inhalte = {}
        for pfad in sorted(fonts_dir.glob('*.woff2')):
            digest = hashlib.sha256(pfad.read_bytes()).hexdigest()
            inhalte.setdefault(digest, []).append(pfad.name)
        doppelt = {d: n for d, n in inhalte.items() if len(n) > 1}
        self.assertEqual({}, doppelt, f'Byte-identische Schriftdateien: {list(doppelt.values())}')


if __name__ == '__main__':
    unittest.main()
