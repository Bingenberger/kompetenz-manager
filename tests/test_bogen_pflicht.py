"""Tests: Pflichtboegen stehen beim Erfassen vorn, optionale dahinter.

Gibt es keinen Pflichtbogen, bleibt die Auswahl eine einfache Liste wie bisher.
"""

import os
import re
import shutil
import tempfile
import unittest

from werkzeug.security import generate_password_hash

from app import create_app
from extensions import db
from jahrgang import ensure_klasse
from models import Bogen, BogenJahrgang, Item, Schueler, User, UserKlassenzuordnung

CSRF_RE = re.compile(r'name="_csrf_token"\s+value="([^"]+)"')


class BogenPflichtTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='km_bogen_pflicht_')
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
            admin = User(username='admin', password_hash=generate_password_hash('adminpass'), role='admin')
            db.session.add(admin)
            db.session.flush()
            db.session.add(UserKlassenzuordnung(user_id=admin.id, klasse='3a', rolle='klassenleitung'))
            ensure_klasse('3a')
            kind = Schueler(vorname='Anna', nachname='Drei', klasse='3a', jahrgang=3)
            db.session.add(kind)
            db.session.commit()
            self.kind_id = kind.id

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

    def _bogen(self, titel, pflicht=False, jahrgaenge=()):
        with self.app.app_context():
            bogen = Bogen(titel=titel, pflicht=pflicht)
            db.session.add(bogen)
            db.session.flush()
            db.session.add(Item(bogen_id=bogen.id, bereich='B', text=f'Kompetenz {titel}'))
            for stufe in jahrgaenge:
                db.session.add(BogenJahrgang(bogen_id=bogen.id, jahrgang=stufe))
            db.session.commit()
            return bogen.id

    def _token(self, pfad):
        return CSRF_RE.search(self.client.get(pfad).get_data(as_text=True)).group(1)

    @staticmethod
    def _teile(html):
        """(vor "Weitere Boegen", dahinter) - trennt die beiden Gruppen."""
        vorn, _, hinten = html.partition('Weitere Bögen')
        return vorn, hinten

    # ------------------------------------------------------------------
    # Verwaltung
    # ------------------------------------------------------------------

    def test_new_bogen_is_optional_by_default(self):
        with self.app.app_context():
            bogen = Bogen(titel='Neu')
            db.session.add(bogen)
            db.session.commit()
            self.assertFalse(bogen.pflicht)

    def test_pflicht_can_be_set_and_cleared(self):
        bogen_id = self._bogen('Lesen')
        pfad = f'/admin/bogen/edit/{bogen_id}'
        self.assertIn('name="pflicht"', self.client.get(pfad).get_data(as_text=True))

        self.client.post(pfad, data={
            'titel': 'Lesen', 'pflicht': '1', '_csrf_token': self._token(pfad),
        }, follow_redirects=True)
        with self.app.app_context():
            self.assertTrue(db.session.get(Bogen, bogen_id).pflicht)

        self.client.post(pfad, data={
            'titel': 'Lesen', '_csrf_token': self._token(pfad),
        }, follow_redirects=True)
        with self.app.app_context():
            self.assertFalse(db.session.get(Bogen, bogen_id).pflicht)

    def test_admin_list_shows_kind_and_puts_pflicht_first(self):
        self._bogen('Aaa optional')
        self._bogen('Zzz Pflicht', pflicht=True, jahrgaenge=[3, 4])
        html = self.client.get('/admin/boegen').get_data(as_text=True)
        self.assertLess(html.index('Zzz Pflicht'), html.index('Aaa optional'))
        self.assertIn('>Pflicht</span>', html)
        self.assertIn('>optional</span>', html)
        self.assertIn('3, 4', html)

    # ------------------------------------------------------------------
    # Erfassen
    # ------------------------------------------------------------------

    def test_quick_entry_puts_pflicht_first_and_hides_optional(self):
        self._bogen('Aaa optional')
        self._bogen('Zzz Pflicht', pflicht=True)
        html = self.client.get(f'/erfassen/einzel?schueler_id={self.kind_id}').get_data(as_text=True)
        self.assertIn('Pflichtbögen', html)
        vorn, hinten = self._teile(html)
        self.assertIn('>Zzz Pflicht<', vorn)
        self.assertNotIn('>Aaa optional<', vorn)
        self.assertIn('>Aaa optional<', hinten)
        # Zugeklappt, solange nichts daraus gewaehlt ist.
        self.assertRegex(html, r'<details class="bogen-weitere mb-3"\s*>')
        # Auch das Dropdown ist gruppiert.
        self.assertIn('<optgroup label="Pflichtbögen">', html)

    def test_without_pflicht_the_list_stays_flat(self):
        self._bogen('Lesen')
        self._bogen('Schreiben')
        html = self.client.get(f'/erfassen/einzel?schueler_id={self.kind_id}').get_data(as_text=True)
        self.assertNotIn('Weitere Bögen', html)
        self.assertNotIn('Pflichtbögen', html)
        self.assertNotIn('<optgroup', html)
        self.assertIn('>Lesen<', html)
        self.assertIn('>Schreiben<', html)

    def test_pflicht_for_other_grade_does_not_create_groups(self):
        """Der Jahrgangsfilter wirkt zuerst: ein Pflichtbogen fuer Jahrgang 1
        macht die Auswahl eines Drittklaesslers nicht zweistufig."""
        self._bogen('Anfangsunterricht', pflicht=True, jahrgaenge=[1])
        self._bogen('Lesen')
        html = self.client.get(f'/erfassen/einzel?schueler_id={self.kind_id}').get_data(as_text=True)
        self.assertNotIn('Weitere Bögen', html)
        self.assertNotIn('Anfangsunterricht', html)

    def test_whole_sheet_opens_optional_group_when_its_bogen_is_selected(self):
        optional_id = self._bogen('Aaa optional')
        self._bogen('Zzz Pflicht', pflicht=True)
        html = self.client.get(
            f'/erfassen/schueler?schueler_id={self.kind_id}&bogen_id={optional_id}'
        ).get_data(as_text=True)
        self.assertRegex(html, r'<details class="bogen-weitere mb-3"\s*open>')

        html = self.client.get(f'/erfassen/schueler?schueler_id={self.kind_id}').get_data(as_text=True)
        self.assertRegex(html, r'<details class="bogen-weitere mb-3"\s*>')

    def test_class_run_groups_select_options(self):
        self._bogen('Aaa optional')
        self._bogen('Zzz Pflicht', pflicht=True)
        html = self.client.get('/erfassen/reihe/start').get_data(as_text=True)
        pflicht = html.index('<optgroup label="Pflichtbögen">')
        weitere = html.index('<optgroup label="Weitere Bögen">')
        self.assertLess(pflicht, html.index('>Zzz Pflicht<'))
        self.assertLess(html.index('>Zzz Pflicht<'), weitere)
        self.assertLess(weitere, html.index('>Aaa optional<'))

    def test_multi_run_groups_accordion(self):
        self._bogen('Aaa optional')
        self._bogen('Zzz Pflicht', pflicht=True)
        html = self.client.get('/erfassen/multi/start').get_data(as_text=True)
        vorn, hinten = self._teile(html)
        self.assertIn('Kompetenz Zzz Pflicht', vorn)
        self.assertIn('Kompetenz Aaa optional', hinten)
        self.assertIn('function gruppenAnpassen', html)


if __name__ == '__main__':
    unittest.main()
