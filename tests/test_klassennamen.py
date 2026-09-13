"""Tests: Klassennamen mit Leerzeichen am Rand ("1c ").

Auf dem Produktivserver fuehrte ein gespeichertes "1c " zu einem 403 in der
Diagnostik: das Formular kuerzte die Eingabe zu "1c", die Berechtigungsliste
enthielt aber "1c ".
"""

import os
import re
import shutil
import tempfile
import unittest

from werkzeug.security import generate_password_hash

from app import create_app
from diagnostik import lege_vorbelegung_an
from extensions import db
from jahrgang import bereinige_klassennamen, set_klassen_jahrgaenge
from models import (
    ClassTaskLibrary,
    DiagnostikTestform,
    Klasse,
    Schueler,
    SystemKonfiguration,
    User,
    UserKlassenzuordnung,
)

CSRF_RE = re.compile(r'name="_csrf_token"\s+value="([^"]+)"')


class KlassennamenTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='km_klassennamen_')
        database_uri = os.environ.get('TEST_DATABASE_URL') or f"sqlite:///{self.tmpdir}/test.db"
        self.app = create_app({
            'TESTING': True, 'SECRET_KEY': 'test', 'SQLALCHEMY_DATABASE_URI': database_uri,
            'UPLOAD_FOLDER': os.path.join(self.tmpdir, 'uploads'),
            'PROTECTED_UPLOAD_FOLDER': os.path.join(self.tmpdir, 'protected_uploads'),
        })
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            lehrkraft = User(username='klara', password_hash=generate_password_hash('pass'), role='teacher')
            admin = User(username='admin', password_hash=generate_password_hash('pass'), role='admin')
            db.session.add_all([lehrkraft, admin, SystemKonfiguration(schuljahr='2026/2027')])
            db.session.flush()
            # Altbestand wie auf dem Server: Klasse mit Leerzeichen am Ende.
            db.session.add(UserKlassenzuordnung(user_id=lehrkraft.id, klasse='1c ', rolle='klassenleitung'))
            db.session.add(Schueler(vorname='Anna', nachname='Abt', klasse='1c ', jahrgang=1))
            db.session.add(Schueler(vorname='Ben', nachname='Berg', klasse='1c', jahrgang=1))
            lege_vorbelegung_an()
            db.session.commit()
            self.lehrkraft_id = lehrkraft.id
            self.admin_id = admin.id
            self.hsp = DiagnostikTestform.query.filter_by(name='HSP 1+').one().id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _login(self, username):
        page = self.client.get('/login')
        token = CSRF_RE.search(page.get_data(as_text=True)).group(1)
        self.client.post('/login', data={'username': username, 'password': 'pass', '_csrf_token': token})

    # ------------------------------------------------------------------
    # Diagnostik vor der Bereinigung
    # ------------------------------------------------------------------

    def test_selection_with_trailing_space_is_allowed_for_teacher(self):
        self._login('klara')
        # Genau die URL vom Server: das "+" steht fuer das Leerzeichen.
        response = self.client.get('/diagnostik/erfassen?klasse=1c+&testform_id=&schuljahr=2026%2F2027&halbjahr=ende')
        self.assertEqual(200, response.status_code)
        self.assertIn('<option value="1c" selected>', response.get_data(as_text=True))

        html = self.client.get(
            f'/diagnostik/erfassen?klasse=1c+&testform_id={self.hsp}&schuljahr=2026%2F2027&halbjahr=ende'
        ).get_data(as_text=True)
        self.assertIn('Abt, Anna', html)
        self.assertIn('Berg, Ben', html)

    def test_overview_and_import_find_children_with_trailing_space(self):
        self._login('admin')
        html = self.client.get('/diagnostik?klasse=1c').get_data(as_text=True)
        self.assertIn('Abt, Anna', html)
        self.assertIn('Berg, Ben', html)
        self.assertEqual(200, self.client.get('/diagnostik/import?klasse=1c+').status_code)

    def test_denial_explains_which_class(self):
        self._login('klara')
        response = self.client.get('/diagnostik/erfassen?klasse=4b')
        self.assertEqual(403, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn('Die Klasse „4b“ ist Ihrem Konto nicht zugeordnet', html)
        self.assertIn('Ihre Klassen: 1c.', html)

    # ------------------------------------------------------------------
    # Bereinigung (update_db.py)
    # ------------------------------------------------------------------

    def test_cleanup_trims_names_and_merges_duplicates(self):
        with self.app.app_context():
            lehrkraft = db.session.get(User, self.lehrkraft_id)
            # Dieselbe Zuordnung einmal sauber, einmal mit Leerzeichen vorne.
            db.session.add(UserKlassenzuordnung(user_id=lehrkraft.id, klasse='1c', rolle='fach'))
            db.session.add(UserKlassenzuordnung(user_id=lehrkraft.id, klasse=' 1c', rolle='fach'))
            db.session.add(ClassTaskLibrary(class_name='1c  ', name='Aufgaben'))
            sauber = Klasse(name='1c')
            schmutzig = Klasse(name='1c ')
            db.session.add_all([sauber, schmutzig])
            db.session.flush()
            set_klassen_jahrgaenge(schmutzig, [1])
            db.session.add(Klasse(name='2a '))
            db.session.commit()

            self.assertGreater(bereinige_klassennamen(), 0)
            db.session.commit()
            self.assertEqual(0, bereinige_klassennamen(), 'nicht idempotent')

            self.assertEqual({'1c'}, {k for (k,) in db.session.query(Schueler.klasse).distinct()})
            self.assertEqual(
                [('1c', 'fach'), ('1c', 'klassenleitung')],
                sorted((z.klasse, z.rolle) for z in UserKlassenzuordnung.query.all()),
            )
            self.assertEqual('1c', ClassTaskLibrary.query.one().class_name)
            self.assertEqual(['1c', '2a'], sorted(k.name for k in Klasse.query.all()))
            self.assertEqual([1], Klasse.query.filter_by(name='1c').one().jahrgaenge, 'Jahrgang ging beim Zusammenfuehren verloren')


if __name__ == '__main__':
    unittest.main()
