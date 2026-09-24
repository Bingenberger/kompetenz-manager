"""Nachteilsausgleich: Typen, Notenschutz, Rechte und der Haken in den Förderangaben."""

import os
import re
import shutil
import tempfile
import unittest
from datetime import date

from werkzeug.security import generate_password_hash

from app import create_app
from extensions import db
from models import (
    Foerderangaben,
    Nachteilsausgleich,
    Schueler,
    SystemKonfiguration,
    User,
    UserKlassenzuordnung,
)
from nachteilsausgleich import aktiver, kurzfassung, uebersicht

CSRF_RE = re.compile(r'name="_csrf_token"\s+value="([^"]+)"')


class NachteilsausgleichTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='km_nta_')
        database_uri = os.environ.get('TEST_DATABASE_URL') or f"sqlite:///{self.tmpdir}/test.db"
        self.app = create_app({
            'TESTING': True, 'SECRET_KEY': 'test', 'SQLALCHEMY_DATABASE_URI': database_uri,
            'UPLOAD_FOLDER': os.path.join(self.tmpdir, 'uploads'),
            'PROTECTED_UPLOAD_FOLDER': os.path.join(self.tmpdir, 'protected_uploads'),
        })
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            nutzer = {name: User(username=name, password_hash=generate_password_hash('pass'), role=rolle,
                                 vorname=name.capitalize(), nachname='Test')
                      for name, rolle in (('klara', 'teacher'), ('fremd', 'teacher'),
                                          ('leitung', 'schulleitung'))}
            db.session.add_all(list(nutzer.values()) + [SystemKonfiguration(schuljahr='2026/2027')])
            db.session.flush()
            db.session.add_all([
                UserKlassenzuordnung(user_id=nutzer['klara'].id, klasse='3a', rolle='klassenleitung'),
                UserKlassenzuordnung(user_id=nutzer['fremd'].id, klasse='1b', rolle='klassenleitung'),
            ])
            anna = Schueler(vorname='Anna', nachname='Kind', klasse='3a', jahrgang=3)
            db.session.add(anna)
            db.session.commit()
            self.anna_id = anna.id
            self.user_ids = {name: user.id for name, user in nutzer.items()}

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _login(self, username='klara'):
        self.client = self.app.test_client()
        token = CSRF_RE.search(self.client.get('/login').get_data(as_text=True)).group(1)
        self.client.post('/login', data={'username': username, 'password': 'pass', '_csrf_token': token})
        return CSRF_RE.search(self.client.get('/').get_data(as_text=True)).group(1)

    def _speichern(self, token, **felder):
        daten = {'_csrf_token': token, 'schuljahr': '2026/2027'}
        daten.update(felder)
        return self.client.post(f'/nachteilsausgleich/kind/{self.anna_id}', data=daten, follow_redirects=True)

    # ------------------------------------------------------------------

    def test_types_and_note_protection_are_saved(self):
        token = self._login()
        self._speichern(
            token,
            beschluss_am='2026-09-15',
            grundlage='LRS laut HSP 09/2026',
            massnahme_zeit='10 Minuten mehr Zeit',
            massnahme_hilfsmittel='Lesepfeil, Anlauttabelle',
            massnahme_didaktisch='   ',
            massnahme_raum='Sitzplatz vorn',
            notenschutz_rechtschreiben='1',
            notenschutz_beschluss_am='2026-09-15',
        )
        with self.app.app_context():
            eintrag = Nachteilsausgleich.query.one()
            self.assertEqual(date(2026, 9, 15), eintrag.beschluss_am)
            # Leere Felder werden nicht zur Maßnahme.
            self.assertEqual([('zeit', '10 Minuten mehr Zeit'),
                              ('hilfsmittel', 'Lesepfeil, Anlauttabelle'),
                              ('raum', 'Sitzplatz vorn')],
                             [(m.typ, m.beschreibung) for m in eintrag.massnahmen])
            self.assertTrue(eintrag.notenschutz_rechtschreiben)
            self.assertFalse(eintrag.notenschutz_lesen)
            self.assertIn('Note ausgesetzt: Rechtschreiben', kurzfassung(eintrag))

    def test_the_flag_in_foerderangaben_follows_the_entry(self):
        token = self._login()
        self._speichern(token, massnahme_zeit='mehr Zeit')
        with self.app.app_context():
            self.assertTrue(Foerderangaben.query.one().nachteilsausgleich)
            eintrag_id = Nachteilsausgleich.query.one().id

        # Beenden nimmt den Haken zurück; die leeren Angaben verschwinden.
        self.client.post(f'/nachteilsausgleich/{eintrag_id}/aktion',
                         data={'_csrf_token': token, 'aktion': 'beenden'}, follow_redirects=True)
        with self.app.app_context():
            self.assertEqual(0, Foerderangaben.query.count())
            self.assertIsNone(aktiver(self.anna_id, '2026/2027'))
            self.assertIsNotNone(Nachteilsausgleich.query.one().beendet_am)

    def test_empty_form_removes_the_entry(self):
        token = self._login()
        self._speichern(token, massnahme_zeit='mehr Zeit')
        self._speichern(token)
        with self.app.app_context():
            self.assertEqual(0, Nachteilsausgleich.query.count())

    def test_carrying_over_into_the_next_school_year(self):
        token = self._login()
        self._speichern(token, beschluss_am='2026-09-15', massnahme_zeit='mehr Zeit',
                        notenschutz_lesen='1')
        with self.app.app_context():
            eintrag_id = Nachteilsausgleich.query.one().id
        self.client.post(f'/nachteilsausgleich/{eintrag_id}/aktion',
                         data={'_csrf_token': token, 'aktion': 'uebernehmen'}, follow_redirects=True)
        with self.app.app_context():
            kopie = Nachteilsausgleich.query.filter_by(schuljahr='2027/2028').one()
            self.assertEqual(['mehr Zeit'], [m.beschreibung for m in kopie.massnahmen])
            self.assertTrue(kopie.notenschutz_lesen)
            # Die Klassenkonferenz beschließt neu.
            self.assertIsNone(kopie.beschluss_am)

    def test_other_teacher_has_no_access(self):
        token = self._login('klara')
        self._speichern(token, massnahme_zeit='mehr Zeit')
        fremd_token = self._login('fremd')
        self.assertEqual(403, self.client.get(f'/nachteilsausgleich/kind/{self.anna_id}').status_code)
        self.assertEqual(403, self._speichern(fremd_token, massnahme_zeit='andere Zeit').status_code)
        with self.app.app_context():
            self.assertEqual(['mehr Zeit'],
                             [m.beschreibung for m in Nachteilsausgleich.query.one().massnahmen])

    def test_list_shows_only_visible_children(self):
        token = self._login('klara')
        self._speichern(token, massnahme_hilfsmittel='Lesepfeil')
        html = self.client.get('/nachteilsausgleich').get_data(as_text=True)
        self.assertIn('Kind, Anna', html)
        self.assertIn('Hilfsmittel', html)

        self._login('fremd')
        html = self.client.get('/nachteilsausgleich').get_data(as_text=True)
        self.assertNotIn('Kind, Anna', html)
        with self.app.app_context():
            leitung = db.session.get(User, self.user_ids['leitung'])
            self.assertEqual(1, len(uebersicht(leitung, '2026/2027')))

    def test_student_record_and_report_material_show_the_entry(self):
        token = self._login()
        self._speichern(token, massnahme_zeit='10 Minuten mehr Zeit', notenschutz_rechtschreiben='1')
        akte = self.client.get(f'/schuelerakte?schueler_id={self.anna_id}').get_data(as_text=True)
        self.assertIn('10 Minuten mehr Zeit', akte)
        self.assertIn('Note ausgesetzt', akte)
        material = self.client.get(f'/report/zeugnismaterial?schueler_id={self.anna_id}').get_data(as_text=True)
        self.assertIn('Nachteilsausgleich', material)
        self.assertIn('10 Minuten mehr Zeit', material)

    def test_deleting_the_child_takes_the_entry(self):
        token = self._login()
        self._speichern(token, massnahme_zeit='mehr Zeit')
        with self.app.app_context():
            db.session.delete(db.session.get(Schueler, self.anna_id))
            db.session.commit()
            self.assertEqual(0, Nachteilsausgleich.query.count())


if __name__ == '__main__':
    unittest.main()
