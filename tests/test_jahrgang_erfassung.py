"""Tests: beim Erfassen erscheinen nur die Boegen, die zum Jahrgang passen.

Der Jahrgang filtert das Angebot, er sperrt nichts - ein ueber einen Link
angefragter Bogen bleibt waehlbar, damit Verknuepfungen nicht ins Leere laufen.
Bestandsdaten zu anderen Boegen bleiben ohnehin sichtbar.
"""

import os
import re
import shutil
import tempfile
import unittest

from werkzeug.security import generate_password_hash

from app import create_app
from extensions import db
from jahrgang import ensure_klasse, set_klassen_jahrgaenge
from models import Bogen, BogenJahrgang, Item, Schueler, User, UserKlassenzuordnung

CSRF_RE = re.compile(r'name="_csrf_token"\s+value="([^"]+)"')


class JahrgangErfassungTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='km_jahrgang_erfassung_')
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
            lehrkraft = User(
                username='admin', password_hash=generate_password_hash('adminpass'), role='admin',
            )
            db.session.add(lehrkraft)
            db.session.flush()
            db.session.add(UserKlassenzuordnung(user_id=lehrkraft.id, klasse='3a', rolle='klassenleitung'))

            self.ids = {}
            for titel, stufen in [('Allgemein', []), ('Anfangsunterricht', [1]), ('Aufsatz', [3, 4])]:
                bogen = Bogen(titel=titel)
                db.session.add(bogen)
                db.session.flush()
                db.session.add(Item(bogen_id=bogen.id, bereich='B', text=f'Kompetenz {titel}'))
                for stufe in stufen:
                    db.session.add(BogenJahrgang(bogen_id=bogen.id, jahrgang=stufe))
                self.ids[titel] = bogen.id

            ensure_klasse('3a')
            ensure_klasse('1a')
            set_klassen_jahrgaenge(ensure_klasse('Füchse'), [])
            drittklaessler = Schueler(vorname='Anna', nachname='Drei', klasse='3a', jahrgang=3)
            ohne = Schueler(vorname='Ohne', nachname='Jahrgang', klasse='Füchse', jahrgang=None)
            db.session.add_all([drittklaessler, ohne])
            db.session.commit()
            self.drei_id = drittklaessler.id
            self.ohne_id = ohne.id

        page = self.client.get('/login')
        token = CSRF_RE.search(page.get_data(as_text=True)).group(1)
        self.client.post('/login', data={
            'username': 'admin', 'password': 'adminpass', '_csrf_token': token,
        })

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _bogen_optionen(self, html):
        """Die Bogen-Titel, die im Formular als Auswahl angeboten werden."""
        return {titel for titel in self.ids if f'>{titel}<' in html}

    # ------------------------------------------------------------------
    # Schnelleintrag
    # ------------------------------------------------------------------

    def test_quick_entry_offers_only_matching_boegen(self):
        html = self.client.get(f'/erfassen/einzel?schueler_id={self.drei_id}').get_data(as_text=True)
        angeboten = self._bogen_optionen(html)
        self.assertIn('Allgemein', angeboten)
        self.assertIn('Aufsatz', angeboten)
        self.assertNotIn('Anfangsunterricht', angeboten, 'Bogen für Jahrgang 1 bei einem Drittklässler')

    def test_quick_entry_without_grade_offers_every_bogen(self):
        html = self.client.get(f'/erfassen/einzel?schueler_id={self.ohne_id}').get_data(as_text=True)
        self.assertEqual({'Allgemein', 'Anfangsunterricht', 'Aufsatz'}, self._bogen_optionen(html))

    def test_explicitly_requested_bogen_stays_available(self):
        """Ein Link mit bogen_id darf nicht ins Leere laufen."""
        bogen_id = self.ids['Anfangsunterricht']
        html = self.client.get(
            f'/erfassen/einzel?schueler_id={self.drei_id}&bogen_id={bogen_id}'
        ).get_data(as_text=True)
        self.assertIn('Kompetenz Anfangsunterricht', html)

    # ------------------------------------------------------------------
    # Ganzer Bogen
    # ------------------------------------------------------------------

    def test_whole_sheet_offers_only_matching_boegen(self):
        html = self.client.get(f'/erfassen/schueler?schueler_id={self.drei_id}').get_data(as_text=True)
        angeboten = self._bogen_optionen(html)
        self.assertIn('Aufsatz', angeboten)
        self.assertNotIn('Anfangsunterricht', angeboten)

    # ------------------------------------------------------------------
    # Klassenbezogene Ablaeufe: Filter im Formular
    # ------------------------------------------------------------------

    def test_class_run_marks_boegen_and_classes_with_grades(self):
        html = self.client.get('/erfassen/reihe/start').get_data(as_text=True)
        # Alle Boegen werden ausgeliefert, das Formular blendet passend aus.
        self.assertEqual({'Allgemein', 'Anfangsunterricht', 'Aufsatz'}, self._bogen_optionen(html))
        self.assertIn(f'value="{self.ids["Aufsatz"]}" data-jahrgaenge="3,4"', html)
        self.assertIn(f'value="{self.ids["Allgemein"]}" data-jahrgaenge=""', html)
        self.assertIn('value="3a" data-jahrgaenge="3"', html)
        self.assertIn('function bogenPasst', html)

    def test_multi_run_marks_boegen_and_classes_with_grades(self):
        html = self.client.get('/erfassen/multi/start').get_data(as_text=True)
        self.assertIn('data-jahrgaenge="1"', html)
        self.assertIn('value="3a" data-jahrgaenge="3"', html)
        self.assertIn('multi-bogen', html)
        self.assertIn('function bogenPasst', html)

    # ------------------------------------------------------------------
    # Bestand bleibt sichtbar
    # ------------------------------------------------------------------

    def test_existing_observations_on_other_boegen_stay_visible(self):
        """Der Filter gilt fuer neue Eintraege, nicht fuer die Historie."""
        from datetime import datetime
        from models import Beobachtung

        with self.app.app_context():
            item = Item.query.filter_by(bogen_id=self.ids['Anfangsunterricht']).first()
            db.session.add(Beobachtung(
                schueler_id=self.drei_id, item_id=item.id, wert=3,
                kommentar='aus der ersten Klasse', datum=datetime(2024, 3, 1),
            ))
            db.session.commit()

        html = self.client.get(
            f'/report/schueler?schueler_id={self.drei_id}&bogen_id={self.ids["Anfangsunterricht"]}'
        ).get_data(as_text=True)
        self.assertIn('aus der ersten Klasse', html)


if __name__ == '__main__':
    unittest.main()
