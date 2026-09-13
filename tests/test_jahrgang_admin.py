"""Tests fuer die Pflege von Jahrgaengen in der Verwaltung und beim Import."""

import os
import re
import shutil
import tempfile
import unittest
from datetime import date
from io import BytesIO

from openpyxl import Workbook
from werkzeug.security import generate_password_hash

from app import create_app
from extensions import db
from jahrgang import ensure_klasse, set_klassen_jahrgaenge
from models import Bogen, BogenJahrgang, Klasse, Schueler, User

CSRF_RE = re.compile(r'name="_csrf_token"\s+value="([^"]+)"')


class JahrgangAdminTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='km_jahrgang_admin_')
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
            db.session.add_all([
                User(username='admin', password_hash=generate_password_hash('adminpass'), role='admin'),
                User(username='lehrkraft', password_hash=generate_password_hash('lehrpass'), role='teacher'),
            ])
            db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _login(self, username='admin', password='adminpass'):
        page = self.client.get('/login')
        token = CSRF_RE.search(page.get_data(as_text=True)).group(1)
        self.client.post('/login', data={
            'username': username, 'password': password, '_csrf_token': token,
        })

    def _token(self, pfad):
        return CSRF_RE.search(self.client.get(pfad).get_data(as_text=True)).group(1)

    def _kind(self, klasse, jahrgang=None, vorname='Anna'):
        with self.app.app_context():
            kind = Schueler(vorname=vorname, nachname='Abt', klasse=klasse, jahrgang=jahrgang)
            db.session.add(kind)
            db.session.commit()
            return kind.id

    # ------------------------------------------------------------------
    # Kind bearbeiten
    # ------------------------------------------------------------------

    def _save_student(self, kind_id, klasse, jahrgang=''):
        pfad = f'/admin/student/edit/{kind_id}'
        return self.client.post(pfad, data={
            'vorname': 'Anna', 'nachname': 'Abt', 'klasse': klasse,
            'jahrgang': jahrgang, 'is_active': '1', '_csrf_token': self._token(pfad),
        }, follow_redirects=True)

    def test_student_in_grade_named_class_gets_that_grade(self):
        self._login()
        kind_id = self._kind('2a')
        # Selbst eine abweichende Eingabe wird durch den Klassenjahrgang ersetzt.
        self._save_student(kind_id, '3a', jahrgang='1')
        with self.app.app_context():
            kind = db.session.get(Schueler, kind_id)
            self.assertEqual('3a', kind.klasse)
            self.assertEqual(3, kind.jahrgang)
            self.assertIsNotNone(Klasse.query.filter_by(name='3a').first(), 'Klasse nicht angelegt')

    def test_student_in_mixed_class_needs_one_of_its_grades(self):
        with self.app.app_context():
            set_klassen_jahrgaenge(ensure_klasse('Blau'), [1, 2])
            db.session.commit()
        self._login()
        kind_id = self._kind('Blau')

        response = self._save_student(kind_id, 'Blau', jahrgang='3')
        self.assertIn('jahrgangsübergreifend', response.get_data(as_text=True))
        with self.app.app_context():
            self.assertIsNone(db.session.get(Schueler, kind_id).jahrgang)

        self._save_student(kind_id, 'Blau', jahrgang='2')
        with self.app.app_context():
            self.assertEqual(2, db.session.get(Schueler, kind_id).jahrgang)

    def test_student_edit_page_shows_the_class_hint(self):
        self._login()
        kind_id = self._kind('3a', jahrgang=3)
        with self.app.app_context():
            ensure_klasse('3a')
            db.session.commit()
        html = self.client.get(f'/admin/student/edit/{kind_id}').get_data(as_text=True)
        self.assertIn('name="jahrgang"', html)
        self.assertIn('hat Jahrgang 3', html)

    def test_student_list_shows_the_grade(self):
        self._login()
        self._kind('3a', jahrgang=3)
        html = self.client.get('/admin/students').get_data(as_text=True)
        self.assertIn('<th>Jahrgang</th>', html)

    # ------------------------------------------------------------------
    # Klassenuebersicht
    # ------------------------------------------------------------------

    def test_class_page_requires_admin(self):
        self._login('lehrkraft', 'lehrpass')
        response = self.client.get('/admin/klassen', follow_redirects=True)
        self.assertIn('Zugriff verweigert', response.get_data(as_text=True))

    def test_class_page_creates_missing_classes(self):
        self._kind('3a', jahrgang=3)
        self._kind('Füchse', vorname='Ben')
        self._login()
        html = self.client.get('/admin/klassen').get_data(as_text=True)
        self.assertIn('3a', html)
        self.assertIn('Füchse', html)
        with self.app.app_context():
            self.assertEqual({'3a', 'Füchse'}, {k.name for k in Klasse.query.all()})

    def test_grade_named_class_is_read_only(self):
        with self.app.app_context():
            ensure_klasse('3a')
            db.session.commit()
        self._login()
        html = self.client.get('/admin/klassen').get_data(as_text=True)
        self.assertIn('aus dem Namen', html)

    def test_free_class_grades_can_be_set_and_fill_empty_student_grades(self):
        kind_id = self._kind('Füchse')
        with self.app.app_context():
            klasse_id = ensure_klasse('Füchse').id
            db.session.commit()

        self._login()
        self.client.post(f'/admin/klassen/{klasse_id}', data={
            'jahrgaenge': ['2'], '_csrf_token': self._token('/admin/klassen'),
        }, follow_redirects=True)

        with self.app.app_context():
            self.assertEqual([2], db.session.get(Klasse, klasse_id).jahrgaenge)
            self.assertEqual(2, db.session.get(Schueler, kind_id).jahrgang)

    def test_changing_class_grades_never_overwrites_a_student_grade(self):
        """Wird aus einer gemischten Klasse eine mit einem Jahrgang, bleibt der
        Jahrgang der Kinder stehen - die Abweichung wird angezeigt, nicht behoben."""
        with self.app.app_context():
            klasse = ensure_klasse('Blau')
            set_klassen_jahrgaenge(klasse, [1, 2])
            db.session.commit()
            klasse_id = klasse.id
        kind_id = self._kind('Blau', jahrgang=1)

        self._login()
        self.client.post(f'/admin/klassen/{klasse_id}', data={
            'jahrgaenge': ['2'], '_csrf_token': self._token('/admin/klassen'),
        }, follow_redirects=True)

        with self.app.app_context():
            self.assertEqual(1, db.session.get(Schueler, kind_id).jahrgang)

        html = self.client.get('/admin/klassen').get_data(as_text=True)
        self.assertIn('ohne passenden Jahrgang', html)

    def test_grade_named_class_rejects_a_different_grade(self):
        with self.app.app_context():
            klasse_id = ensure_klasse('3a').id
            db.session.commit()
        self._login()
        response = self.client.post(f'/admin/klassen/{klasse_id}', data={
            'jahrgaenge': ['2'], '_csrf_token': self._token('/admin/klassen'),
        }, follow_redirects=True)
        self.assertIn('trägt ihren Jahrgang im Namen', response.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual([3], db.session.get(Klasse, klasse_id).jahrgaenge)

    def test_students_without_matching_grade_are_listed(self):
        self._kind('Füchse')
        self._login()
        html = self.client.get('/admin/klassen').get_data(as_text=True)
        self.assertIn('Jahrgang fehlt oder passt nicht', html)
        self.assertIn('Anna Abt', html)

    # ------------------------------------------------------------------
    # Bogen
    # ------------------------------------------------------------------

    def test_bogen_grades_can_be_set_and_cleared(self):
        with self.app.app_context():
            bogen = Bogen(titel='Schreiben')
            db.session.add(bogen)
            db.session.commit()
            bogen_id = bogen.id

        self._login()
        pfad = f'/admin/bogen/edit/{bogen_id}'
        self.client.post(pfad, data={
            'titel': 'Schreiben', 'jahrgaenge': ['3', '4'], '_csrf_token': self._token(pfad),
        }, follow_redirects=True)
        with self.app.app_context():
            self.assertEqual([3, 4], db.session.get(Bogen, bogen_id).jahrgaenge)

        self.client.post(pfad, data={
            'titel': 'Schreiben', '_csrf_token': self._token(pfad),
        }, follow_redirects=True)
        with self.app.app_context():
            self.assertEqual([], db.session.get(Bogen, bogen_id).jahrgaenge)
            self.assertEqual(0, BogenJahrgang.query.count())

    def test_new_bogen_with_grades(self):
        self._login()
        self.client.post('/admin/bogen/new', data={
            'titel': 'Anfangsunterricht', 'jahrgaenge': ['1'],
            '_csrf_token': self._token('/admin/bogen/new'),
        }, follow_redirects=True)
        with self.app.app_context():
            self.assertEqual([1], Bogen.query.filter_by(titel='Anfangsunterricht').one().jahrgaenge)

    def test_bogen_edit_page_offers_grades(self):
        self._login()
        html = self.client.get('/admin/bogen/new').get_data(as_text=True)
        self.assertIn('name="jahrgaenge"', html)
        self.assertIn('Ohne Auswahl gilt er für alle', html)

    # ------------------------------------------------------------------
    # Import
    # ------------------------------------------------------------------

    def _import(self, rows):
        workbook = Workbook()
        for zeile in rows:
            workbook.active.append(zeile)
        puffer = BytesIO()
        workbook.save(puffer)
        puffer.seek(0)
        return self.client.post('/import/schueler', data={
            'file': (puffer, 'liste.xlsx'), '_csrf_token': self._token('/import/schueler'),
        }, content_type='multipart/form-data', follow_redirects=True)

    def test_import_takes_grade_from_class_name(self):
        self._login()
        self._import([
            ['Vorname', 'Nachname', 'Klasse', 'Geburtsdatum'],
            ['Anna', 'Abt', '3a', date(2018, 7, 4)],
        ])
        with self.app.app_context():
            self.assertEqual(3, Schueler.query.one().jahrgang)
            self.assertIsNotNone(Klasse.query.filter_by(name='3a').first())

    def test_import_reads_optional_grade_column_for_free_classes(self):
        self._login()
        self._import([
            ['Vorname', 'Nachname', 'Klasse', 'Geburtsdatum', 'Jahrgang'],
            ['Anna', 'Abt', 'Füchse', date(2018, 7, 4), 2],
        ])
        with self.app.app_context():
            self.assertEqual(2, Schueler.query.one().jahrgang)

    def test_import_reports_children_without_grade(self):
        self._login()
        response = self._import([
            ['Vorname', 'Nachname', 'Klasse', 'Geburtsdatum'],
            ['Anna', 'Abt', 'Füchse', date(2018, 7, 4)],
        ])
        self.assertIn('ohne Jahrgang importiert', response.get_data(as_text=True))
        with self.app.app_context():
            self.assertIsNone(Schueler.query.one().jahrgang)


if __name__ == '__main__':
    unittest.main()
