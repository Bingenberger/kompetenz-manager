"""Tests fuer den Excel-Import von Schuelern und Beobachtungsboegen.

Geschrieben vor dem Umstieg von pandas auf openpyxl, damit der Tausch messbar
verhaltensgleich ist: dieselben Tests laufen gegen beide Fassungen.

Der Import ist der Weg, auf dem eine Schule ihre Klassenlisten hereinbekommt.
Ein Fehler darin faellt erst auf, wenn die Namen schon in der Datenbank stehen.
"""

import os
import re
import shutil
import tempfile
import unittest
from datetime import date, datetime
from io import BytesIO

from openpyxl import Workbook
from werkzeug.security import generate_password_hash

from app import create_app
from extensions import db
from models import Bogen, Item, Schueler, User

CSRF_RE = re.compile(r'name="_csrf_token"\s+value="([^"]+)"')


def xlsx_bytes(rows):
    """Baut eine .xlsx-Datei aus einer Liste von Zeilen (erste Zeile = Kopf)."""
    workbook = Workbook()
    blatt = workbook.active
    for zeile in rows:
        blatt.append(zeile)
    puffer = BytesIO()
    workbook.save(puffer)
    puffer.seek(0)
    return puffer


class ExcelImportTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='km_import_test_')
        database_uri = os.environ.get('TEST_DATABASE_URL') or f"sqlite:///{self.tmpdir}/test.db"

        self.app = create_app({
            'TESTING': True,
            'SECRET_KEY': 'test-secret',
            'SQLALCHEMY_DATABASE_URI': database_uri,
            'UPLOAD_FOLDER': os.path.join(self.tmpdir, 'uploads'),
            'PROTECTED_UPLOAD_FOLDER': os.path.join(self.tmpdir, 'protected_uploads'),
        })
        self.client = self.app.test_client()

        with self.app.app_context():
            db.create_all()
            db.session.add(User(
                username='admin',
                password_hash=generate_password_hash('adminpass'),
                role='admin',
            ))
            db.session.commit()
        self._login()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _login(self):
        page = self.client.get('/login')
        token = CSRF_RE.search(page.get_data(as_text=True)).group(1)
        self.assertEqual(302, self.client.post('/login', data={
            'username': 'admin', 'password': 'adminpass', '_csrf_token': token,
        }).status_code)

    def _upload(self, typ, rows, filename='liste.xlsx'):
        page = self.client.get(f'/import/{typ}')
        token = CSRF_RE.search(page.get_data(as_text=True)).group(1)
        return self.client.post(
            f'/import/{typ}',
            data={'file': (xlsx_bytes(rows), filename), '_csrf_token': token},
            content_type='multipart/form-data',
            follow_redirects=True,
        )

    # ------------------------------------------------------------------
    # Schueler
    # ------------------------------------------------------------------

    def test_imports_students(self):
        response = self._upload('schueler', [
            ['Vorname', 'Nachname', 'Klasse', 'Geburtsdatum'],
            ['Anna', 'Abt', '3a', date(2018, 7, 4)],
            ['Ben', 'Bauer', '3a', date(2017, 12, 24)],
        ])
        self.assertEqual(200, response.status_code)

        with self.app.app_context():
            kinder = Schueler.query.order_by(Schueler.nachname).all()
            self.assertEqual(['Abt', 'Bauer'], [k.nachname for k in kinder])
            self.assertEqual(['Anna', 'Ben'], [k.vorname for k in kinder])
            self.assertEqual(['3a', '3a'], [k.klasse for k in kinder])
            self.assertEqual(date(2018, 7, 4), kinder[0].geburtsdatum)

    def test_date_as_real_date_cell(self):
        self._upload('schueler', [
            ['Vorname', 'Nachname', 'Klasse', 'Geburtsdatum'],
            ['Anna', 'Abt', '3a', datetime(2018, 7, 4, 0, 0)],
        ])
        with self.app.app_context():
            self.assertEqual(date(2018, 7, 4), Schueler.query.one().geburtsdatum)

    def test_date_as_german_text(self):
        self._upload('schueler', [
            ['Vorname', 'Nachname', 'Klasse', 'Geburtsdatum'],
            ['Anna', 'Abt', '3a', '04.07.2018'],
        ])
        with self.app.app_context():
            self.assertEqual(date(2018, 7, 4), Schueler.query.one().geburtsdatum)

    def test_date_as_iso_text(self):
        """ISO-Datum in einer Textzelle.

        Die pandas-Fassung las "2018-07-04" mit dayfirst=True als 7. April -
        Tag und Monat vertauscht, ohne Hinweis. Exporte aus anderen Systemen
        liefern genau dieses Format.
        """
        self._upload('schueler', [
            ['Vorname', 'Nachname', 'Klasse', 'Geburtsdatum'],
            ['Anna', 'Abt', '3a', '2018-07-04'],
        ])
        with self.app.app_context():
            self.assertEqual(date(2018, 7, 4), Schueler.query.one().geburtsdatum)

    def test_empty_date_stays_empty(self):
        self._upload('schueler', [
            ['Vorname', 'Nachname', 'Klasse', 'Geburtsdatum'],
            ['Anna', 'Abt', '3a', None],
        ])
        with self.app.app_context():
            self.assertIsNone(Schueler.query.one().geburtsdatum)

    def test_unparsable_date_does_not_break_the_import(self):
        """Ein unleserliches Datum darf nicht die ganze Klassenliste verhindern."""
        self._upload('schueler', [
            ['Vorname', 'Nachname', 'Klasse', 'Geburtsdatum'],
            ['Anna', 'Abt', '3a', 'kein Datum'],
        ])
        with self.app.app_context():
            kind = Schueler.query.one()
            self.assertEqual('Abt', kind.nachname)
            self.assertIsNone(kind.geburtsdatum)

    def test_missing_columns_are_reported_and_nothing_is_imported(self):
        response = self._upload('schueler', [
            ['Vorname', 'Nachname'],
            ['Anna', 'Abt'],
        ])
        html = response.get_data(as_text=True)
        self.assertIn('Fehlende Spalten', html)
        self.assertIn('Klasse', html)
        self.assertIn('Geburtsdatum', html)
        with self.app.app_context():
            self.assertEqual(0, Schueler.query.count())

    # ------------------------------------------------------------------
    # Boegen
    # ------------------------------------------------------------------

    def test_imports_bogen_with_items(self):
        self._upload('bogen', [
            ['Bogen', 'Bereich', 'Item'],
            ['Sozialverhalten', 'Konflikt', 'Löst Streit friedlich'],
            ['Sozialverhalten', 'Arbeit', 'Arbeitet konzentriert'],
            ['Mathematik', 'Zahlen', 'Zählt bis 20'],
        ])
        with self.app.app_context():
            self.assertEqual(
                ['Mathematik', 'Sozialverhalten'],
                sorted(b.titel for b in Bogen.query.all()),
            )
            sozial = Bogen.query.filter_by(titel='Sozialverhalten').one()
            self.assertEqual(2, Item.query.filter_by(bogen_id=sozial.id).count())
            self.assertEqual(
                ['Arbeit', 'Konflikt'],
                sorted(i.bereich for i in Item.query.filter_by(bogen_id=sozial.id)),
            )

    def test_existing_bogen_is_reused_not_duplicated(self):
        with self.app.app_context():
            db.session.add(Bogen(titel='Sozialverhalten'))
            db.session.commit()

        self._upload('bogen', [
            ['Bogen', 'Bereich', 'Item'],
            ['Sozialverhalten', 'Konflikt', 'Löst Streit friedlich'],
        ])
        with self.app.app_context():
            self.assertEqual(1, Bogen.query.filter_by(titel='Sozialverhalten').count())
            self.assertEqual(1, Item.query.count())

    def test_bogen_missing_columns_are_reported(self):
        response = self._upload('bogen', [
            ['Bogen', 'Bereich'],
            ['Sozialverhalten', 'Konflikt'],
        ])
        self.assertIn('Fehlende Spalten', response.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(0, Bogen.query.count())

    # ------------------------------------------------------------------
    # Randfaelle der Datei
    # ------------------------------------------------------------------

    def test_trailing_empty_rows_are_skipped(self):
        """Tabellen aus der Praxis haben oft leere Zeilen am Ende."""
        self._upload('schueler', [
            ['Vorname', 'Nachname', 'Klasse', 'Geburtsdatum'],
            ['Anna', 'Abt', '3a', date(2018, 7, 4)],
            [None, None, None, None],
            [None, None, None, None],
        ])
        with self.app.app_context():
            self.assertEqual(1, Schueler.query.count())

    def test_numeric_class_becomes_text(self):
        """Eine Klasse "4" liest Excel als Zahl - in der Datenbank steht Text."""
        self._upload('schueler', [
            ['Vorname', 'Nachname', 'Klasse', 'Geburtsdatum'],
            ['Anna', 'Abt', 4, date(2018, 7, 4)],
        ])
        with self.app.app_context():
            self.assertEqual('4', Schueler.query.one().klasse)

    def test_non_xlsx_file_is_ignored(self):
        page = self.client.get('/import/schueler')
        token = CSRF_RE.search(page.get_data(as_text=True)).group(1)
        response = self.client.post(
            '/import/schueler',
            data={'file': (BytesIO(b'kein Excel'), 'liste.csv'), '_csrf_token': token},
            content_type='multipart/form-data',
            follow_redirects=True,
        )
        self.assertEqual(200, response.status_code)
        with self.app.app_context():
            self.assertEqual(0, Schueler.query.count())

    def test_import_requires_login(self):
        # Eigener Client: der angemeldete aus setUp laesst sich nicht ohne
        # frisches CSRF-Token abmelden.
        anonym = self.app.test_client()
        response = anonym.get('/import/schueler')
        self.assertEqual(302, response.status_code)
        self.assertIn('/login', response.headers['Location'])


if __name__ == '__main__':
    unittest.main()
