"""Schuljahresübergreifende Bögen, etwa zum Übergang in Klasse 5 (Jahrgang 3 und 4)."""

import os
import re
import shutil
import tempfile
import unittest
from datetime import date, datetime

from werkzeug.security import generate_password_hash

from app import create_app
from beobachtungszeitraum import beginn_fuer
from competency_matrix import build_matrix
from extensions import db
from models import Beobachtung, Bogen, BogenJahrgang, Item, Schueler, SystemKonfiguration, User, UserKlassenzuordnung
from report_material import collect_material
from routes.erfassung_routes import _build_bogen_entries_for_student

CSRF_RE = re.compile(r'name="_csrf_token"\s+value="([^"]+)"')


class UebergreifenderBogenTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='km_uebergreifend_')
        database_uri = os.environ.get('TEST_DATABASE_URL') or f"sqlite:///{self.tmpdir}/test.db"
        self.app = create_app({
            'TESTING': True, 'SECRET_KEY': 'test', 'SQLALCHEMY_DATABASE_URI': database_uri,
            'UPLOAD_FOLDER': os.path.join(self.tmpdir, 'uploads'),
            'PROTECTED_UPLOAD_FOLDER': os.path.join(self.tmpdir, 'protected_uploads'),
        })
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            admin = User(username='admin', password_hash=generate_password_hash('pass'), role='admin')
            klara = User(username='klara', password_hash=generate_password_hash('pass'), role='teacher')
            db.session.add_all([admin, klara, SystemKonfiguration(schuljahr='2026/2027', schuljahr_beginn=date(2026, 8, 1))])
            db.session.flush()
            db.session.add(UserKlassenzuordnung(user_id=klara.id, klasse='4a', rolle='klassenleitung'))
            vierer = Schueler(vorname='Vera', nachname='Vier', klasse='4a', jahrgang=4)
            dreier = Schueler(vorname='Dora', nachname='Drei', klasse='3a', jahrgang=3)
            db.session.add_all([vierer, dreier])
            uebergang = Bogen(titel='Übergang Klasse 5', schuljahresuebergreifend=True)
            deutsch = Bogen(titel='Deutsch')
            db.session.add_all([uebergang, deutsch])
            db.session.flush()
            db.session.add_all([BogenJahrgang(bogen_id=uebergang.id, jahrgang=3),
                                BogenJahrgang(bogen_id=uebergang.id, jahrgang=4)])
            item_u = Item(bogen_id=uebergang.id, bereich='Arbeitsverhalten', text='arbeitet selbstständig')
            item_d = Item(bogen_id=deutsch.id, bereich='Lesen', text='liest flüssig')
            db.session.add_all([item_u, item_d])
            db.session.flush()
            # Vorjahr (Klasse 3) und laufendes Jahr (Klasse 4)
            for item, wert, datum in ((item_u, 2, datetime(2025, 11, 3)), (item_u, 4, datetime(2026, 10, 1)),
                                      (item_d, 1, datetime(2025, 11, 3)), (item_d, 3, datetime(2026, 10, 1))):
                db.session.add(Beobachtung(schueler_id=vierer.id, item_id=item.id, wert=wert, datum=datum,
                                           kommentar=f'Notiz {datum.year}'))
            db.session.commit()
            self.ids = {'vera': vierer.id, 'dora': dreier.id, 'uebergang': uebergang.id, 'deutsch': deutsch.id,
                        'item_u': item_u.id, 'item_d': item_d.id}

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_period_starts_with_the_first_grade_of_the_sheet(self):
        with self.app.app_context():
            uebergang = db.session.get(Bogen, self.ids['uebergang'])
            deutsch = db.session.get(Bogen, self.ids['deutsch'])
            vera = db.session.get(Schueler, self.ids['vera'])
            dora = db.session.get(Schueler, self.ids['dora'])
            self.assertEqual(date(2025, 8, 1), beginn_fuer(uebergang, vera))    # Klasse 4: seit Klasse 3
            self.assertEqual(date(2026, 8, 1), beginn_fuer(uebergang, dora))    # Klasse 3: laufendes Jahr
            self.assertEqual(date(2025, 8, 1), beginn_fuer(uebergang))          # ganze Klasse
            self.assertEqual(date(2026, 8, 1), beginn_fuer(deutsch, vera))      # normaler Bogen
            uebergang.jahrgang_zuordnungen.clear()
            self.assertIsNone(beginn_fuer(uebergang, vera))                     # ohne Jahrgänge: alles

    def test_elternberatung_and_record_include_last_year(self):
        with self.app.app_context():
            zeilen = {row['bogen'].titel: row for row in _build_bogen_entries_for_student(self.ids['vera'])['bogen_rows']}
            uebergang = zeilen['Übergang Klasse 5']['item_rows'][0]
            deutsch = zeilen['Deutsch']['item_rows'][0]
            self.assertEqual((2, 3.0), (uebergang['anzahl'], uebergang['durchschnitt']))
            self.assertEqual((1, 3.0), (deutsch['anzahl'], deutsch['durchschnitt']))

    def test_class_matrix_and_report_material_include_last_year(self):
        with self.app.app_context():
            uebergang = db.session.get(Bogen, self.ids['uebergang'])
            matrix = build_matrix('4a', uebergang)
            self.assertEqual(2, matrix['cells'][(self.ids['vera'], self.ids['item_u'])]['count'])
            matrix = build_matrix('4a', db.session.get(Bogen, self.ids['deutsch']))
            self.assertEqual(1, matrix['cells'][(self.ids['vera'], self.ids['item_d'])]['count'])

            material = collect_material(db.session.get(Schueler, self.ids['vera']))
            texte = str(material['boegen'])
            self.assertIn('Notiz 2025', texte)          # aus dem Übergangsbogen
            self.assertEqual(3, material['beobachtungen_anzahl'])

    def test_admin_sets_flag_and_elternberatung_shows_it(self):
        self.client.get('/login')
        seite = self.client.get('/login')
        token = CSRF_RE.search(seite.get_data(as_text=True)).group(1)
        self.client.post('/login', data={'username': 'admin', 'password': 'pass', '_csrf_token': token})
        token = CSRF_RE.search(self.client.get('/').get_data(as_text=True)).group(1)
        html = self.client.get(f'/admin/bogen/edit/{self.ids["deutsch"]}').get_data(as_text=True)
        self.assertIn('name="schuljahresuebergreifend"', html)
        self.client.post(f'/admin/bogen/edit/{self.ids["deutsch"]}', data={
            '_csrf_token': token, 'titel': 'Deutsch', 'schuljahresuebergreifend': '1',
        })
        with self.app.app_context():
            self.assertTrue(db.session.get(Bogen, self.ids['deutsch']).schuljahresuebergreifend)
        html = self.client.get(f'/erfassen/elternberatung?schueler_id={self.ids["vera"]}&tab=dropdown').get_data(as_text=True)
        self.assertIn('seit Klasse 3', html)


if __name__ == '__main__':
    unittest.main()
