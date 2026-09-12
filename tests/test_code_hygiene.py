"""Eigenschaften des Quelltexts, die stillschweigend verloren gehen.

Anders als die uebrigen Tests prueft dieser nicht Verhalten, sondern den Code
selbst - fuer Dinge, die niemandem auffallen, bis sie Jahre spaeter brechen.

Geprueft wird ueber den Syntaxbaum, nicht per Textsuche: time_utils.py nennt
den veralteten Aufruf in seinem Docstring, um zu erklaeren, was es ersetzt. Ein
Textmuster wuerde das als Verstoss melden.
"""

import ast
import unittest
from pathlib import Path

PROJEKT = Path(__file__).resolve().parent.parent

AUSGENOMMEN = {'venv', '.venv', '__pycache__', '.git', 'node_modules'}


def python_dateien():
    for pfad in sorted(PROJEKT.rglob('*.py')):
        if AUSGENOMMEN & set(pfad.parts):
            continue
        yield pfad


def aufrufe_von(pfad, name):
    """Zeilennummern, in denen eine Methode dieses Namens aufgerufen wird."""
    try:
        baum = ast.parse(pfad.read_text(encoding='utf-8'), filename=str(pfad))
    except SyntaxError as fehler:
        raise AssertionError(f'{pfad} ist kein gültiges Python: {fehler}') from fehler

    return [
        knoten.lineno
        for knoten in ast.walk(baum)
        if isinstance(knoten, ast.Call)
        and isinstance(knoten.func, ast.Attribute)
        and knoten.func.attr == name
    ]


class CodeHygieneTestCase(unittest.TestCase):
    def test_no_deprecated_utcnow(self):
        """utcnow() ist nicht nur veraltet, sondern zur Entfernung vorgesehen.

        time_utils.utc_now() liefert dieselbe naive UTC-Zeit und passt damit zum
        bestehenden Schema.
        """
        treffer = [
            f'{pfad.relative_to(PROJEKT)}:{zeile}'
            for pfad in python_dateien()
            for zeile in aufrufe_von(pfad, 'utcnow')
        ]

        self.assertEqual(
            [], treffer,
            'Veralteter utcnow-Aufruf statt time_utils.utc_now():\n  '
            + '\n  '.join(treffer),
        )

    def test_every_python_file_parses(self):
        """Nebenprodukt der Pruefung oben, aber eigenstaendig nuetzlich."""
        for pfad in python_dateien():
            aufrufe_von(pfad, 'utcnow')


if __name__ == '__main__':
    unittest.main()
