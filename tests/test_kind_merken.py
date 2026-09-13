"""Tests: das zuletzt gewaehlte Kind bleibt beim Seitenwechsel gewaehlt."""

import os
import re
import shutil
import tempfile
import unittest

from werkzeug.security import generate_password_hash

from app import create_app
from extensions import db
from models import Schueler, SystemKonfiguration, User, UserKlassenzuordnung

CSRF_RE = re.compile(r'name="_csrf_token"\s+value="([^"]+)"')


class KindMerkenTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='km_kind_merken_')
        database_uri = os.environ.get('TEST_DATABASE_URL') or f"sqlite:///{self.tmpdir}/test.db"
        self.app = create_app({
            'TESTING': True, 'SECRET_KEY': 'test', 'SQLALCHEMY_DATABASE_URI': database_uri,
            'UPLOAD_FOLDER': os.path.join(self.tmpdir, 'uploads'),
            'PROTECTED_UPLOAD_FOLDER': os.path.join(self.tmpdir, 'protected_uploads'),
        })
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            klara = User(username='klara', password_hash=generate_password_hash('pass'), role='teacher')
            db.session.add_all([klara, SystemKonfiguration(schuljahr='2026/2027')])
            db.session.flush()
            db.session.add(UserKlassenzuordnung(user_id=klara.id, klasse='3a', rolle='klassenleitung'))
            db.session.add(UserKlassenzuordnung(user_id=klara.id, klasse='4b', rolle='fach'))
            self.ids = {}
            for vorname, klasse in (('Anna', '3a'), ('Ben', '3a'), ('Cem', '4b')):
                kind = Schueler(vorname=vorname, nachname='Test', klasse=klasse)
                db.session.add(kind)
                db.session.flush()
                self.ids[vorname] = kind.id
            db.session.commit()
        page = self.client.get('/login')
        token = CSRF_RE.search(page.get_data(as_text=True)).group(1)
        self.client.post('/login', data={'username': 'klara', 'password': 'pass', '_csrf_token': token})

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _chip(self, html):
        treffer = re.search(r'<span class="kind-chip".*?</span>\s*<small>', html, re.S)
        return re.sub(r'<[^>]+>', '', treffer.group(0)).strip() if treffer else None

    # ------------------------------------------------------------------

    def test_child_stays_selected_across_pages(self):
        self.client.get(f'/schuelerakte?schueler_id={self.ids["Ben"]}')
        html = self.client.get('/erfassen/einzel').get_data(as_text=True)
        self.assertIn('Ben Test (3a)', html)               # Schritt 2 mit vorbelegtem Kind
        html = self.client.get('/erfassen/elternkontakte').get_data(as_text=True)
        self.assertIn('Ausgewähltes Kind: Ben Test', html)
        self.assertIn('<input type="hidden" name="schueler_id" value="%d">' % self.ids['Ben'], html)
        html = self.client.get('/foerderplan/list').get_data(as_text=True)
        self.assertIn('Ben Test', self._chip(html))

    def test_chip_in_header_and_clearing(self):
        html = self.client.get(f'/schuelerakte?schueler_id={self.ids["Anna"]}').get_data(as_text=True)
        self.assertEqual('Anna Test', self._chip(html))
        self.assertIn('/kind/abwaehlen', html)

        token = CSRF_RE.search(html).group(1)
        antwort = self.client.post('/kind/abwaehlen', data={'_csrf_token': token, 'next': '/erfassen/einzel'})
        self.assertEqual('/erfassen/einzel', antwort.headers['Location'])
        html = self.client.get('/erfassen/einzel').get_data(as_text=True)
        self.assertIsNone(self._chip(html))
        self.assertIn('1. Kind auswählen', html)
        self.assertNotIn('Kind wechseln', html)

    def test_explicit_empty_parameter_clears_memory(self):
        self.client.get(f'/schuelerakte?schueler_id={self.ids["Anna"]}')
        html = self.client.get('/erfassen/einzel?tab=own&schueler_id=').get_data(as_text=True)
        self.assertNotIn('Kind wechseln', html)
        self.assertIsNone(self._chip(html))

    def test_switching_tab_does_not_force_the_remembered_child(self):
        self.client.get(f'/schuelerakte?schueler_id={self.ids["Anna"]}')
        html = self.client.get('/erfassen/einzel?tab=fach-4b').get_data(as_text=True)
        self.assertNotIn('Anna Test (3a)', html)
        self.assertIn('Cem Test', html)
        # Das Gedaechtnis bleibt trotzdem erhalten.
        self.assertEqual('Anna Test', self._chip(html))

    def test_record_opens_remembered_child_instead_of_first(self):
        self.client.get(f'/schuelerakte?schueler_id={self.ids["Ben"]}')
        html = self.client.get('/schuelerakte').get_data(as_text=True)
        self.assertIn('<h3 class="mb-0">Ben Test</h3>', html)

    def test_diagnostics_remembers_too(self):
        self.client.get(f'/diagnostik/erfassen?schueler_id={self.ids["Cem"]}')
        self.assertEqual('Cem Test', self._chip(self.client.get('/').get_data(as_text=True)))

    def test_archived_child_is_forgotten_in_header(self):
        self.client.get(f'/schuelerakte?schueler_id={self.ids["Anna"]}')
        with self.app.app_context():
            db.session.get(Schueler, self.ids['Anna']).is_active = False
            db.session.commit()
        self.assertIsNone(self._chip(self.client.get('/').get_data(as_text=True)))

    def test_memory_is_per_session(self):
        self.client.get(f'/schuelerakte?schueler_id={self.ids["Anna"]}')
        anderer = self.app.test_client()
        page = anderer.get('/login')
        token = CSRF_RE.search(page.get_data(as_text=True)).group(1)
        anderer.post('/login', data={'username': 'klara', 'password': 'pass', '_csrf_token': token})
        self.assertIsNone(self._chip(anderer.get('/').get_data(as_text=True)))


if __name__ == '__main__':
    unittest.main()
