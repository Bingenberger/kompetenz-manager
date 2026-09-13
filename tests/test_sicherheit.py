"""Tests fuer die Schutzmassnahmen aus dem Sicherheitscheck."""

import os
import re
import shutil
import tempfile
import unittest
from io import BytesIO

from werkzeug.security import generate_password_hash

from app import create_app
from extensions import db
from klassenzugriff import darf_kind_sehen
from models import (
    ErziehungsEreignis,
    ErziehungsEreignisKategorie,
    ErziehungsEreignisVorlage,
    ErziehungsOrt,
    Schueler,
    SystemKonfiguration,
    User,
    UserKlassenzuordnung,
)
from uploads import speichere_upload_bild

CSRF_RE = re.compile(r'name="_csrf_token"\s+value="([^"]+)"')


class SicherheitTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='km_sicherheit_')
        database_uri = os.environ.get('TEST_DATABASE_URL') or f"sqlite:///{self.tmpdir}/test.db"
        self.app = create_app({
            'TESTING': True, 'SECRET_KEY': 'test', 'SQLALCHEMY_DATABASE_URI': database_uri,
            'UPLOAD_FOLDER': os.path.join(self.tmpdir, 'uploads'),
            'PROTECTED_UPLOAD_FOLDER': os.path.join(self.tmpdir, 'protected_uploads'),
            'MAX_CONTENT_LENGTH': 2000,
        })
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            klara = User(username='klara', password_hash=generate_password_hash('pass'), role='teacher')
            fremd = User(username='fremd', password_hash=generate_password_hash('pass'), role='teacher')
            admin = User(username='admin', password_hash=generate_password_hash('pass'), role='admin')
            db.session.add_all([klara, fremd, admin, SystemKonfiguration(schuljahr='2026/2027')])
            db.session.flush()
            db.session.add(UserKlassenzuordnung(user_id=klara.id, klasse='3a', rolle='klassenleitung'))
            db.session.add(UserKlassenzuordnung(user_id=fremd.id, klasse='1b', rolle='klassenleitung'))
            kind = Schueler(vorname='Anna', nachname='Abt', klasse='3a', jahrgang=3)
            db.session.add(kind)
            db.session.flush()
            kat = ErziehungsEreignisKategorie(name='Hinweis', sort_order=1, is_active=True)
            db.session.add(kat)
            db.session.flush()
            vorlage = ErziehungsEreignisVorlage(category_id=kat.id, name='Streit', sort_order=1, is_active=True)
            ort = ErziehungsOrt(name='Hof', sort_order=1, is_active=True)
            db.session.add_all([vorlage, ort])
            db.session.flush()
            db.session.add(ErziehungsEreignis(student_id=kind.id, event_template_id=vorlage.id, ort_id=ort.id,
                                              beschreibung='Vertraulicher Vorfall', status='offen'))
            db.session.commit()
            self.kind_id = kind.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _login(self, username):
        page = self.client.get('/login')
        token = CSRF_RE.search(page.get_data(as_text=True)).group(1)
        self.client.post('/login', data={'username': username, 'password': 'pass', '_csrf_token': token})
        return CSRF_RE.search(self.client.get('/').get_data(as_text=True)).group(1)

    # ------------------------------------------------------------------

    def test_security_headers_are_set(self):
        antwort = self.client.get('/login')
        self.assertEqual('nosniff', antwort.headers['X-Content-Type-Options'])
        self.assertEqual('SAMEORIGIN', antwort.headers['X-Frame-Options'])
        self.assertEqual('strict-origin-when-cross-origin', antwort.headers['Referrer-Policy'])
        self.assertIn('camera=()', antwort.headers['Permissions-Policy'])

    def test_oversized_request_is_rejected_with_explanation(self):
        token = self._login('klara')
        antwort = self.client.post('/import/schueler', data={
            '_csrf_token': token, 'file': (BytesIO(b'x' * 5000), 'gross.xlsx'),
        }, content_type='multipart/form-data')
        self.assertEqual(413, antwort.status_code)
        self.assertIn('Datei zu groß', antwort.get_data(as_text=True))

    def test_csrf_applies_to_put_and_delete(self):
        self._login('klara')
        self.assertEqual(400, self.client.put('/api/work-plans/irgendwas', json={}).status_code)
        self.assertEqual(400, self.client.delete('/api/work-plans/irgendwas').status_code)

    def test_short_passwords_are_rejected(self):
        token = self._login('klara')
        antwort = self.client.post('/konto', data={
            '_csrf_token': token, 'form_action': 'password', 'altes_pw': 'pass',
            'neues_pw': 'kurz', 'neues_pw_wdh': 'kurz',
        }, follow_redirects=True)
        self.assertIn('mindestens 8 Zeichen', antwort.get_data(as_text=True))
        with self.app.app_context():
            from werkzeug.security import check_password_hash
            self.assertTrue(check_password_hash(User.query.filter_by(username='klara').one().password_hash, 'pass'))

        token = self._login('admin')
        antwort = self.client.post('/admin/users', data={
            '_csrf_token': token, 'username': 'neu', 'password': '1234567', 'role': 'teacher',
        }, follow_redirects=True)
        self.assertIn('mindestens 8 Zeichen', antwort.get_data(as_text=True))
        with self.app.app_context():
            self.assertIsNone(User.query.filter_by(username='neu').first())

    def test_photo_upload_rejects_wrong_type_and_size(self):
        from werkzeug.datastructures import FileStorage
        with self.app.app_context():
            self.assertIsNone(speichere_upload_bild(FileStorage(BytesIO(b'%PDF-1.4'), 'x.pdf', content_type='application/pdf')))
            zu_gross = FileStorage(BytesIO(b'\xff\xd8' + b'0' * (8 * 1024 * 1024 + 1)), 'x.jpg', content_type='image/jpeg')
            self.assertIsNone(speichere_upload_bild(zu_gross))

    def test_consultation_hides_class_bound_data_from_other_teachers(self):
        self._login('fremd')
        html = self.client.get(f'/erfassen/elternberatung?schueler_id={self.kind_id}').get_data(as_text=True)
        self.assertNotIn('Vertraulicher Vorfall', html)
        self.assertIn('Ereignisse sehen Klassenleitung und Fachlehrkräfte des Kindes', html)
        self.assertIn('Diagnostik-Ergebnisse sehen Klassenleitung', html)

        self.client.post('/logout', data={'_csrf_token': CSRF_RE.search(html).group(1)})
        self._login('klara')
        html = self.client.get(f'/erfassen/elternberatung?schueler_id={self.kind_id}').get_data(as_text=True)
        self.assertIn('Vertraulicher Vorfall', html)

    def test_class_access_helper(self):
        with self.app.app_context():
            kind = db.session.get(Schueler, self.kind_id)
            self.assertTrue(darf_kind_sehen(User.query.filter_by(username='klara').one(), kind))
            self.assertFalse(darf_kind_sehen(User.query.filter_by(username='fremd').one(), kind))
            self.assertTrue(darf_kind_sehen(User.query.filter_by(username='admin').one(), kind))

    def test_open_redirect_is_blocked_on_foundation_sheet(self):
        token = self._login('klara')
        antwort = self.client.post(f'/foerderplan/grundlagen/{self.kind_id}', data={
            '_csrf_token': token, 'besondere_staerken': 'x', 'next': '//boese.example/pfad',
        })
        self.assertEqual(302, antwort.status_code)
        self.assertNotIn('boese.example', antwort.headers['Location'])

    def test_malicious_ods_is_rejected_not_parsed(self):
        import zipfile
        from diagnostik_import import ImportFehler, lese_import
        bombe = ('<?xml version="1.0"?><!DOCTYPE a [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>'
                 '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
                 'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
                 'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"><office:body><office:spreadsheet>'
                 '<table:table table:name="x"><table:table-row><table:table-cell><text:p>&b;</text:p></table:table-cell>'
                 '</table:table-row></table:table></office:spreadsheet></office:body></office:document-content>')
        puffer = BytesIO()
        with zipfile.ZipFile(puffer, 'w') as archiv:
            archiv.writestr('content.xml', bombe)
        with self.assertRaises(ImportFehler):
            lese_import('x.ods', puffer.getvalue())


if __name__ == '__main__':
    unittest.main()
