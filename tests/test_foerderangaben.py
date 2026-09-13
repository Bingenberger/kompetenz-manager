"""Tests: Foerderangaben je Kind und Schuljahr, Klasse zum Testzeitpunkt."""

import os
import re
import shutil
import tempfile
import unittest

from werkzeug.security import generate_password_hash

from app import create_app
from diagnostik import (
    ergaenze_klassen_der_ergebnisse,
    klasse_im_schuljahr,
    lege_vorbelegung_an,
    speichere_ergebnis,
)
from extensions import db
from models import (
    DiagnostikErgebnis,
    DiagnostikTestform,
    Foerderangaben,
    Schueler,
    SystemKonfiguration,
    User,
    UserKlassenzuordnung,
)

CSRF_RE = re.compile(r'name="_csrf_token"\s+value="([^"]+)"')


class FoerderangabenTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='km_foerderangaben_')
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
            fremd = User(username='fremd', password_hash=generate_password_hash('pass'), role='teacher')
            db.session.add_all([klara, fremd, SystemKonfiguration(schuljahr='2025/2026')])
            db.session.flush()
            db.session.add(UserKlassenzuordnung(user_id=klara.id, klasse='3c', rolle='klassenleitung'))
            db.session.add(UserKlassenzuordnung(user_id=fremd.id, klasse='1b', rolle='klassenleitung'))
            kind = Schueler(vorname='Anna', nachname='Abt', klasse='3c', jahrgang=3)
            db.session.add(kind)
            lege_vorbelegung_an()
            db.session.commit()
            self.kind_id = kind.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _login(self, username='klara'):
        page = self.client.get('/login')
        token = CSRF_RE.search(page.get_data(as_text=True)).group(1)
        self.client.post('/login', data={'username': username, 'password': 'pass', '_csrf_token': token})

    def _token(self):
        return CSRF_RE.search(self.client.get('/').get_data(as_text=True)).group(1)

    def _speichern(self, **felder):
        daten = {'_csrf_token': self._token(), 'schuljahr': '2025/2026'}
        daten.update(felder)
        return self.client.post(f'/diagnostik/foerderangaben/{self.kind_id}', data=daten, follow_redirects=True)

    # ------------------------------------------------------------------

    def test_saving_updating_and_clearing(self):
        self._login()
        antwort = self._speichern(nachteilsausgleich='1', foerderschwerpunkt='Lautgetreues Schreiben')
        self.assertIn('Förderangaben 2025/2026 für Anna Abt gespeichert', antwort.get_data(as_text=True))
        with self.app.app_context():
            eintrag = Foerderangaben.query.one()
            self.assertEqual((True, False, False, 'Lautgetreues Schreiben'),
                             (eintrag.nachteilsausgleich, eintrag.foerderkurs, eintrag.externe_foerderung, eintrag.foerderschwerpunkt))

        self._speichern(foerderkurs='1', anmerkungen='Brille')
        with self.app.app_context():
            eintrag = Foerderangaben.query.one()
            self.assertEqual((False, True, None, 'Brille'),
                             (eintrag.nachteilsausgleich, eintrag.foerderkurs, eintrag.foerderschwerpunkt, eintrag.anmerkungen))

        self._speichern()
        with self.app.app_context():
            self.assertEqual(0, Foerderangaben.query.count(), 'leere Angaben bleiben stehen')

    def test_other_teacher_cannot_save(self):
        self._login('fremd')
        antwort = self._speichern(foerderkurs='1')
        self.assertEqual(403, antwort.status_code)
        with self.app.app_context():
            self.assertEqual(0, Foerderangaben.query.count())

    def test_student_record_shows_form_and_earlier_years(self):
        with self.app.app_context():
            db.session.add(Foerderangaben(schueler_id=self.kind_id, schuljahr='2024/2025',
                                          externe_foerderung=True, foerderschwerpunkt='Lesen'))
            db.session.commit()
        self._login()
        self._speichern(nachteilsausgleich='1')
        html = self.client.get(f'/schuelerakte?schueler_id={self.kind_id}').get_data(as_text=True)
        self.assertIn('Förderangaben 2025/2026', html)
        self.assertRegex(html, r'name="nachteilsausgleich" value="1" id="fa-nta"\s+checked')
        self.assertIn('<strong>2024/2025:</strong>', html)
        self.assertIn('externe Förderung', html)

    def test_angaben_are_deleted_with_the_child(self):
        with self.app.app_context():
            db.session.add(Foerderangaben(schueler_id=self.kind_id, schuljahr='2025/2026', foerderkurs=True))
            db.session.commit()
            db.session.delete(db.session.get(Schueler, self.kind_id))
            db.session.commit()
            self.assertEqual(0, Foerderangaben.query.count())

    # ------------------------------------------------------------------
    # Klasse zum Testzeitpunkt
    # ------------------------------------------------------------------

    def test_class_at_time_of_test(self):
        class Kind:
            def __init__(self, klasse):
                self.klasse = klasse
        self.assertEqual('1c', klasse_im_schuljahr(Kind('3c'), 1))
        self.assertEqual('3c', klasse_im_schuljahr(Kind('3c '), 3))
        self.assertEqual('Füchse', klasse_im_schuljahr(Kind('Füchse'), 1))
        self.assertIsNone(klasse_im_schuljahr(Kind(''), 2))

    def test_saving_a_result_stores_class_and_backfill_is_idempotent(self):
        with self.app.app_context():
            kind = db.session.get(Schueler, self.kind_id)
            testform = DiagnostikTestform.query.filter_by(name='HSP 1+').one()
            ergebnis, _ = speichere_ergebnis(kind, testform, '2023/2024', 'ende', {}, None)
            db.session.commit()
            self.assertEqual((1, '1c'), (ergebnis.jahrgang, ergebnis.klasse))

            alt = DiagnostikErgebnis(schueler_id=kind.id, testform_id=testform.id, schuljahr='2024/2025',
                                     halbjahr='mitte', jahrgang=2)
            db.session.add(alt)
            db.session.commit()
            self.assertEqual(1, ergaenze_klassen_der_ergebnisse())
            db.session.commit()
            self.assertEqual('2c', alt.klasse)
            self.assertEqual(0, ergaenze_klassen_der_ergebnisse())


if __name__ == '__main__':
    unittest.main()
