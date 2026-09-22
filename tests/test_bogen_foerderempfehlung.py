"""Bögen lassen sich von den Förderplan-Empfehlungen ausnehmen."""

import os
import re
import shutil
import tempfile
import unittest
from datetime import date, timedelta

from werkzeug.security import generate_password_hash

from app import create_app
from extensions import db
from konferenz import abgleich, erstelle_konferenz
from models import Beobachtung, Bogen, Foerdergrundlage, Item, Schueler, SystemKonfiguration, User, UserKlassenzuordnung
from time_utils import utc_now

CSRF_RE = re.compile(r'name="_csrf_token"\s+value="([^"]+)"')


class FoerderempfehlungTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='km_foerderempfehlung_')
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
            leitung = User(username='leitung', password_hash=generate_password_hash('pass'), role='schulleitung')
            db.session.add_all([admin, klara, leitung, SystemKonfiguration(schuljahr='2026/2027',
                                                                           schuljahr_beginn=date(2026, 8, 1))])
            db.session.flush()
            db.session.add(UserKlassenzuordnung(user_id=klara.id, klasse='4a', rolle='klassenleitung'))
            kind = Schueler(vorname='Vera', nachname='Vier', klasse='4a', jahrgang=4)
            db.session.add(kind)
            bogen = Bogen(titel='Übergang Klasse 5')
            db.session.add(bogen)
            db.session.flush()
            db.session.add(Foerdergrundlage(schueler_id=kind.id, besondere_staerken='Erzählt gern'))
            for nummer in range(5):
                item = Item(bogen_id=bogen.id, bereich='Arbeitsverhalten', text=f'Übergangskompetenz {nummer}')
                db.session.add(item)
                db.session.flush()
                db.session.add(Beobachtung(schueler_id=kind.id, item_id=item.id, wert=1,
                                           datum=utc_now() - timedelta(days=3)))
            konferenz = erstelle_konferenz('2026/2027', 4, 'Herbst', date(2026, 11, 10), leitung)
            konferenz.kinder[0].vorschlag_stufe = 'A'
            db.session.commit()
            self.kind_id, self.bogen_id, self.eintrag_id = kind.id, bogen.id, konferenz.kinder[0].id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _login(self, username):
        self.client = self.app.test_client()
        seite = self.client.get('/login')
        token = CSRF_RE.search(seite.get_data(as_text=True)).group(1)
        self.client.post('/login', data={'username': username, 'password': 'pass', '_csrf_token': token})
        return CSRF_RE.search(self.client.get('/').get_data(as_text=True)).group(1)

    def _ausschalten(self):
        with self.app.app_context():
            db.session.get(Bogen, self.bogen_id).foerderempfehlung = False
            db.session.commit()

    def test_new_sheets_count_by_default(self):
        with self.app.app_context():
            self.assertTrue(db.session.get(Bogen, self.bogen_id).foerderempfehlung)

    def test_candidates_ignore_excluded_sheet(self):
        self._login('klara')
        self.assertIn('Vier', self.client.get('/todo/foerderplan-kandidaten').get_data(as_text=True))
        self._ausschalten()
        self.assertNotIn('Vera Vier', self.client.get('/todo/foerderplan-kandidaten').get_data(as_text=True))
        self.assertNotIn('Förderplan-Kandidat', self.client.get('/').get_data(as_text=True))

    def test_plan_wizard_ignores_excluded_sheet(self):
        self._login('klara')
        # Vorschlagskarten tragen "Vorschlag aus Beobachtung"; die Auswahl aller
        # Kompetenzen zum freien Hinzufügen bleibt davon unberührt.
        self.assertIn('Vorschlag aus Beobachtung', self.client.get(f'/foerderplan/neu/{self.kind_id}').get_data(as_text=True))
        self._ausschalten()
        self.assertNotIn('Vorschlag aus Beobachtung', self.client.get(f'/foerderplan/neu/{self.kind_id}').get_data(as_text=True))

    def test_conference_a_block_ignores_excluded_sheet(self):
        from models import FoerderkonferenzKind
        with self.app.app_context():
            eintrag = db.session.get(FoerderkonferenzKind, self.eintrag_id)
            self.assertTrue(any('reicht noch nicht' in h for h in abgleich(eintrag)))
        self._ausschalten()
        with self.app.app_context():
            eintrag = db.session.get(FoerderkonferenzKind, self.eintrag_id)
            self.assertFalse(any('reicht noch nicht' in h for h in abgleich(eintrag)))

    def test_admin_switch(self):
        token = self._login('admin')
        html = self.client.get(f'/admin/bogen/edit/{self.bogen_id}').get_data(as_text=True)
        self.assertIn('name="foerderempfehlung"', html)
        self.client.post(f'/admin/bogen/edit/{self.bogen_id}', data={'_csrf_token': token, 'titel': 'Übergang Klasse 5'})
        with self.app.app_context():
            self.assertFalse(db.session.get(Bogen, self.bogen_id).foerderempfehlung)
        self.assertIn('ohne Förderempfehlung', self.client.get('/admin/boegen').get_data(as_text=True))
        self.client.post(f'/admin/bogen/edit/{self.bogen_id}', data={'_csrf_token': token, 'titel': 'Übergang Klasse 5',
                                                                     'foerderempfehlung': '1'})
        with self.app.app_context():
            self.assertTrue(db.session.get(Bogen, self.bogen_id).foerderempfehlung)


if __name__ == '__main__':
    unittest.main()
