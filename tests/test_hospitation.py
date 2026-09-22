"""Hospitationen der Schulleitung und ihre Einbindung in die Förderkonferenz."""

import os
import re
import shutil
import tempfile
import unittest
from datetime import date

from werkzeug.security import generate_password_hash

from app import create_app
from extensions import db
from hospitation import schuljahr_von, sichtbare_eintraege
from konferenz import abgleich, erstelle_konferenz
from models import (
    FoerderkonferenzKind,
    Hospitation,
    HospitationKind,
    Schueler,
    SystemKonfiguration,
    User,
    UserKlassenzuordnung,
)
from student_record import collect_record, record_blocks

CSRF_RE = re.compile(r'name="_csrf_token"\s+value="([^"]+)"')


class HospitationTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='km_hospitation_')
        database_uri = os.environ.get('TEST_DATABASE_URL') or f"sqlite:///{self.tmpdir}/test.db"
        self.app = create_app({
            'TESTING': True, 'SECRET_KEY': 'test', 'SQLALCHEMY_DATABASE_URI': database_uri,
            'UPLOAD_FOLDER': os.path.join(self.tmpdir, 'uploads'),
            'PROTECTED_UPLOAD_FOLDER': os.path.join(self.tmpdir, 'protected_uploads'),
        })
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            nutzer = {name: User(username=name, password_hash=generate_password_hash('pass'), role=rolle)
                      for name, rolle in (('leitung', 'schulleitung'), ('klara', 'teacher'), ('kai', 'teacher'))}
            db.session.add_all(list(nutzer.values()) + [SystemKonfiguration(schuljahr='2026/2027')])
            db.session.flush()
            db.session.add_all([
                UserKlassenzuordnung(user_id=nutzer['klara'].id, klasse='3a', rolle='klassenleitung'),
                UserKlassenzuordnung(user_id=nutzer['kai'].id, klasse='3b', rolle='klassenleitung'),
            ])
            kinder = {}
            for vorname, klasse in (('Anna', '3a'), ('Ben', '3a'), ('Cem', '3b')):
                kinder[vorname] = Schueler(vorname=vorname, nachname='Test', klasse=klasse, jahrgang=3)
            db.session.add_all(kinder.values())
            db.session.commit()
            self.ids = {name: kind.id for name, kind in kinder.items()}
            self.user_ids = {name: user.id for name, user in nutzer.items()}

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

    def _token(self):
        return CSRF_RE.search(self.client.get('/').get_data(as_text=True)).group(1)

    def _hospitation_anlegen(self):
        """Schulleitung hospitiert in der 3a und beobachtet Anna."""
        token = self._login('leitung')
        antwort = self.client.post('/hospitation/neu', data={
            '_csrf_token': token, 'klasse': '3a', 'datum': '2026-10-05', 'anlass': 'Mathematik, Arbeitsverhalten',
        })
        self.assertEqual(302, antwort.status_code)
        with self.app.app_context():
            hospitation = Hospitation.query.one()
            hospitation_id = hospitation.id
        # Cem ist nicht in der 3a und wird nicht übernommen.
        self.client.post(f'/hospitation/{hospitation_id}/kinder', data={
            '_csrf_token': self._token(), 'schueler_id': [str(self.ids['Anna']), str(self.ids['Cem'])],
        })
        with self.app.app_context():
            eintrag = HospitationKind.query.one()
            self.assertEqual(self.ids['Anna'], eintrag.schueler_id)
            eintrag_id = eintrag.id
        for feld, wert in (('beobachtung', 'Arbeitet nur mit Begleitung'), ('empfehlung_stufe', 'C'), ('stern', '1')):
            antwort = self.client.post(f'/hospitation/{hospitation_id}/kind/{eintrag_id}/feld',
                                       json={'feld': feld, 'wert': wert}, headers={'X-CSRF-Token': self._token()})
            self.assertTrue(antwort.get_json()['ok'])
        return hospitation_id

    # ------------------------------------------------------------------

    def test_only_leadership_uses_hospitations(self):
        token = self._login('klara')
        self.assertEqual(403, self.client.get('/hospitation').status_code)
        self.assertEqual(403, self.client.post('/hospitation/neu', data={'_csrf_token': token, 'klasse': '3a'}).status_code)
        self.assertNotIn('Hospitationen', self.client.get('/foerderplan/list').get_data(as_text=True))
        self._login('leitung')
        self.assertEqual(200, self.client.get('/hospitation').status_code)
        self.assertIn('Hospitationen', self.client.get('/foerderplan/list').get_data(as_text=True))

    def test_notes_are_saved_per_child(self):
        hospitation_id = self._hospitation_anlegen()
        with self.app.app_context():
            eintrag = HospitationKind.query.one()
            self.assertEqual(('Arbeitet nur mit Begleitung', 'C', True),
                             (eintrag.beobachtung, eintrag.empfehlung_stufe, eintrag.stern))
        html = self.client.get(f'/hospitation/{hospitation_id}').get_data(as_text=True)
        self.assertIn('Arbeitet nur mit Begleitung', html)
        self.assertIn('Ben Test', html)          # noch auswählbar
        self._login('klara')
        self.assertEqual(403, self.client.get(f'/hospitation/{hospitation_id}').status_code)

    def test_visibility_follows_release(self):
        hospitation_id = self._hospitation_anlegen()
        with self.app.app_context():
            anna = db.session.get(Schueler, self.ids['Anna'])
            klara = db.session.get(User, self.user_ids['klara'])
            kai = db.session.get(User, self.user_ids['kai'])
            leitung = db.session.get(User, self.user_ids['leitung'])
            self.assertEqual(1, len(sichtbare_eintraege(anna, leitung)))
            self.assertEqual([], sichtbare_eintraege(anna, klara))

        self.client.post(f'/hospitation/{hospitation_id}/feld', json={'feld': 'freigegeben', 'wert': '1'},
                         headers={'X-CSRF-Token': self._token()})
        with self.app.app_context():
            anna = db.session.get(Schueler, self.ids['Anna'])
            self.assertEqual(1, len(sichtbare_eintraege(anna, db.session.get(User, self.user_ids['klara']))))
            self.assertEqual([], sichtbare_eintraege(anna, db.session.get(User, self.user_ids['kai'])))
        self._login('klara')
        self.assertIn('Arbeitet nur mit Begleitung',
                      self.client.get(f'/schuelerakte?schueler_id={self.ids["Anna"]}').get_data(as_text=True))

    def test_conference_shows_hospitation_and_warns_on_a(self):
        self._hospitation_anlegen()
        with self.app.app_context():
            leitung = db.session.get(User, self.user_ids['leitung'])
            konferenz = erstelle_konferenz('2026/2027', 3, 'Herbst', date(2026, 11, 10), leitung)
            anna = next(e for e in konferenz.kinder if e.schueler_id == self.ids['Anna'])
            anna.vorschlag_stufe = 'A'
            db.session.commit()
            self.assertIn('Hospitation am 05.10.2026: Empfehlung C', ' '.join(abgleich(anna)))
            konferenz_id, eintrag_id = konferenz.id, anna.id
            anna.stufe = 'C'
            db.session.commit()

        html = self.client.get(f'/konferenz/{konferenz_id}/phase/5?kind={eintrag_id}').get_data(as_text=True)
        self.assertIn('Hospitation 05.10.2026', html)
        self.assertIn('Arbeitet nur mit Begleitung', html)
        html = self.client.get(f'/konferenz/{konferenz_id}/stufen').get_data(as_text=True)
        self.assertIn('05.10.', html)

        # Die Klassenleitung sieht die Hospitation erst nach Freigabe.
        self._login('klara')
        html = self.client.get(f'/konferenz/{konferenz_id}/vorbereitung').get_data(as_text=True)
        self.assertNotIn('05.10.', html)

    def test_full_record_and_deletion(self):
        hospitation_id = self._hospitation_anlegen()
        with self.app.app_context():
            anna = db.session.get(Schueler, self.ids['Anna'])
            akte = collect_record(anna, db.session.get(User, self.user_ids['leitung']))
            texte = ' '.join(str(block) for block in record_blocks(akte, '01.01.2027'))
            self.assertIn('Hospitationen der Schulleitung', texte)
            self.assertIn('Arbeitet nur mit Begleitung', texte)
            self.assertEqual('2026/2027', schuljahr_von(date(2026, 10, 5)))
            self.assertEqual('2025/2026', schuljahr_von(date(2026, 7, 5)))
        self.client.post(f'/hospitation/{hospitation_id}/loeschen', data={'_csrf_token': self._token()})
        with self.app.app_context():
            self.assertEqual(0, Hospitation.query.count())
            self.assertEqual(0, HospitationKind.query.count())
            self.assertEqual(3, Schueler.query.count())


if __name__ == '__main__':
    unittest.main()
