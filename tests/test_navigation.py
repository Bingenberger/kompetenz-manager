"""Tests fuer die Bereichsnavigation und die neue Startseite."""

import os
import re
import shutil
import tempfile
import unittest

from werkzeug.security import generate_password_hash

from app import create_app
from extensions import db
from models import Schueler, SystemKonfiguration, User, UserKlassenzuordnung
from navigation import BEREICHE, aktiver_bereich

CSRF_RE = re.compile(r'name="_csrf_token"\s+value="([^"]+)"')
BEREICH_RE = re.compile(r'<div class="bereichsnav w-100".*?</div>', re.S)
UNTER_RE = re.compile(r'<nav class="unternav".*?</nav>', re.S)


class NavigationTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='km_navigation_')
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
            klara = User(username='klara', vorname='Klara', nachname='Klasse',
                         password_hash=generate_password_hash('pass'), role='teacher')
            ohne = User(username='ohne', password_hash=generate_password_hash('pass'), role='teacher')
            db.session.add_all([admin, klara, ohne, SystemKonfiguration(schuljahr='2026/2027')])
            db.session.flush()
            db.session.add(UserKlassenzuordnung(user_id=klara.id, klasse='3a', rolle='klassenleitung'))
            db.session.add(UserKlassenzuordnung(user_id=klara.id, klasse='4b', rolle='fach'))
            db.session.add(Schueler(vorname='Anna', nachname='Abt', klasse='3a', jahrgang=3))
            db.session.add(Schueler(vorname='Ben', nachname='Berg', klasse='3a', jahrgang=3))
            db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _login(self, username='klara'):
        page = self.client.get('/login')
        token = CSRF_RE.search(page.get_data(as_text=True)).group(1)
        self.client.post('/login', data={'username': username, 'password': 'pass', '_csrf_token': token})

    @staticmethod
    def _aktiv(html):
        """(aktiver Bereich, aktiver Unterpunkt) aus der gerenderten Seite."""
        bereich = BEREICH_RE.search(html)
        aktiv = re.search(r'class="aktiv"[^>]*>.*?<span>([^<]+)</span>', bereich.group(0), re.S) if bereich else None
        unter = UNTER_RE.search(html)
        unter_aktiv = re.search(r'class="aktiv"[^>]*>([^<]+)</a>', unter.group(0)) if unter else None
        return (aktiv.group(1) if aktiv else None, unter_aktiv.group(1) if unter_aktiv else None)

    # ------------------------------------------------------------------

    def test_every_area_and_subitem_resolves(self):
        with self.app.test_request_context('/'):
            from flask import url_for
            for _, _, _, ziel, punkte in BEREICHE:
                url_for(ziel)
                for _, punkt_ziel, _ in punkte:
                    url_for(punkt_ziel)

    def test_endpoint_to_area(self):
        self.assertEqual('start', aktiver_bereich('system.index'))
        self.assertEqual('start', aktiver_bereich('system.todo_elternkontakt_erinnerungen'))
        self.assertEqual('kinder', aktiver_bereich('system.schuelerakte'))
        self.assertEqual('erfassen', aktiver_bereich('erfassung.elternkontakt_notiz'))
        self.assertEqual('foerderung', aktiver_bereich('workplan.workplan_list_page'))
        self.assertEqual('ereignisse', aktiver_bereich('erziehung.erziehung_view'))
        self.assertEqual('diagnostik', aktiver_bereich('diagnostik.stufenauswertung'))
        self.assertIsNone(aktiver_bereich('diagnostik.admin_katalog'))
        self.assertIsNone(aktiver_bereich('admin.admin_users'))
        self.assertIsNone(aktiver_bereich(None))

    def test_pages_mark_their_area_and_subitem(self):
        self._login()
        self.assertEqual(('Start', None), self._aktiv(self.client.get('/').get_data(as_text=True)))
        self.assertEqual(('Kinder', None), self._aktiv(self.client.get('/schuelerakte').get_data(as_text=True)))
        self.assertEqual(('Erfassen', 'Elternkontakte'),
                         self._aktiv(self.client.get('/erfassen/elternkontakte/notiz').get_data(as_text=True)))
        self.assertEqual(('Auswertung', 'Klassenübersicht'), self._aktiv(self.client.get('/report/matrix').get_data(as_text=True)))
        self.assertEqual(('Förderung', 'Arbeitspläne'), self._aktiv(self.client.get('/arbeitsplaene').get_data(as_text=True)))
        self.assertEqual(('Ereignisse', 'Übersicht'), self._aktiv(self.client.get('/erziehung').get_data(as_text=True)))
        self.assertEqual(('Ereignisse', 'Neues Ereignis'), self._aktiv(self.client.get('/erziehung/neu').get_data(as_text=True)))
        self.assertEqual(('Diagnostik', 'Import'), self._aktiv(self.client.get('/diagnostik/import').get_data(as_text=True)))

    def test_admin_pages_have_navigation_without_active_area(self):
        self._login('admin')
        html = self.client.get('/admin/users').get_data(as_text=True)
        self.assertEqual((None, None), self._aktiv(html))
        self.assertIn('class="bereichsnav', html)
        self.assertNotIn('class="unternav', html)

    def test_login_page_has_no_navigation(self):
        html = self.client.get('/login').get_data(as_text=True)
        self.assertNotIn('bereichsnav', html)
        self.assertNotIn('fussnav', html)

    def test_mobile_bar_lists_all_areas(self):
        self._login()
        html = self.client.get('/').get_data(as_text=True)
        fuss = re.search(r'<nav class="fussnav".*?</nav>', html, re.S).group(0)
        for label in ('Start', 'Kinder', 'Erfassen', 'Auswertung', 'Förderung', 'Ereignisse', 'Diagnostik'):
            self.assertIn(f'<span>{label}</span>', fuss)

    # ------------------------------------------------------------------
    # Startseite
    # ------------------------------------------------------------------

    def test_start_page_is_lean(self):
        self._login()
        html = self.client.get('/').get_data(as_text=True)
        self.assertIn('Willkommen, Klara Klasse', html)
        self.assertRegex(html, r'Klasse 3a\s*· Schuljahr 2026/2027')
        for text in ('Beobachtung eintragen', 'Kind öffnen', 'Klassenübersicht', 'Nächste Aufgaben', 'Meine Klasse 3a'):
            self.assertIn(text, html)
        # Die Kinder der eigenen Klasse fuehren in die Akte.
        self.assertIn('Anna Abt', html)
        self.assertRegex(html, r'href="/schuelerakte\?schueler_id=\d+">Ben Berg</a>')
        self.assertIn('Fachunterricht: <a href="/schuelerakte?tab=fach-4b">4b</a>', html)
        # Die alten Karten sind weg.
        for alt in ('Multi-Item Durchlauf', 'Modul öffnen', 'Arbeitsplanbereich öffnen', 'HSP, Lesescreening'):
            self.assertNotIn(alt, html)

    def test_start_page_without_class(self):
        self._login('ohne')
        html = self.client.get('/').get_data(as_text=True)
        self.assertIn('Keine Klasse zugeordnet', html)
        self.assertIn('sobald die Verwaltung Ihnen eine Klasse zugeordnet hat', html)
        self.assertNotIn('Meine Klasse', html)


if __name__ == '__main__':
    unittest.main()
