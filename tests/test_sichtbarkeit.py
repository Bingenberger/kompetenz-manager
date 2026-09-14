"""Wer welche Elternkontakte, Elternberatungen und Ereignisse sieht.

Regel: eigene Eintraege, dazu alles zu Kindern der eigenen Klassen
(Klassenleitung oder Fachunterricht). Bei Ereignissen zusaetzlich die, fuer
die man zustaendig ist, und die mit einem betroffenen Kind der eigenen Klasse.
"""

import os
import re
import shutil
import tempfile
import unittest
from datetime import date

from werkzeug.security import generate_password_hash

from app import create_app
from extensions import db
from klassenzugriff import sichtbare_elternkontakte, sichtbare_ereignisse
from models import (
    Elternberatung,
    Elternkontakt,
    ErziehungsEreignis,
    ErziehungsEreignisBetroffenesKind,
    ErziehungsEreignisElternkontakt,
    ErziehungsEreignisKategorie,
    ErziehungsEreignisVorlage,
    ErziehungsOrt,
    Schueler,
    SystemKonfiguration,
    User,
    UserKlassenzuordnung,
)
from student_record import collect_record

CSRF_RE = re.compile(r'name="_csrf_token"\s+value="([^"]+)"')


class SichtbarkeitTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='km_sichtbarkeit_')
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
                      for name, rolle in [('klara', 'teacher'), ('fachl', 'teacher'), ('fremd', 'teacher'), ('admin', 'admin')]}
            db.session.add_all(list(nutzer.values()) + [SystemKonfiguration(schuljahr='2026/2027')])
            db.session.flush()
            db.session.add_all([
                # Klassenname mit Leerzeichen am Rand, wie er in alten Daten vorkommt.
                UserKlassenzuordnung(user_id=nutzer['klara'].id, klasse='3a', rolle='klassenleitung'),
                UserKlassenzuordnung(user_id=nutzer['fachl'].id, klasse='3a', rolle='fach'),
                UserKlassenzuordnung(user_id=nutzer['fremd'].id, klasse='1b', rolle='klassenleitung'),
            ])
            anna = Schueler(vorname='Anna', nachname='Abt', klasse='3a ', jahrgang=3)
            ben = Schueler(vorname='Ben', nachname='Berg', klasse='1b', jahrgang=1)
            db.session.add_all([anna, ben])
            db.session.flush()

            def kontakt(kind, autor, betreff, typ='protokoll'):
                eintrag = Elternkontakt(schueler_id=kind.id, user_id=nutzer[autor].id, eintrag_typ=typ,
                                        kontaktform='Telefonat', betreff=betreff, mitteilung='x')
                db.session.add(eintrag)
                return eintrag

            self.k_anna = kontakt(anna, 'klara', 'Protokoll Anna Klara')
            self.k_ben_klara = kontakt(ben, 'klara', 'Protokoll Ben Klara')
            self.k_ben_fremd = kontakt(ben, 'fremd', 'Notiz Ben Fremd', typ='notiz')
            beratung = Elternberatung(schueler_id=ben.id, user_id=nutzer['fremd'].id, datum=date(2026, 9, 1),
                                      anlass='Beratung Ben Fremd')
            db.session.add(beratung)

            kat = ErziehungsEreignisKategorie(name='Hinweis', sort_order=1, is_active=True)
            ort = ErziehungsOrt(name='Hof', sort_order=1, is_active=True)
            db.session.add_all([kat, ort])
            db.session.flush()

            def ereignis(kind, name, **extra):
                vorlage = ErziehungsEreignisVorlage(category_id=kat.id, name=name, sort_order=1, is_active=True)
                db.session.add(vorlage)
                db.session.flush()
                eintrag = ErziehungsEreignis(student_id=kind.id, event_template_id=vorlage.id, ort_id=ort.id,
                                             beschreibung=f'Beschreibung {name}', status='offen',
                                             created_by_user_id=nutzer['fremd'].id, datum=date(2026, 9, 2), **extra)
                db.session.add(eintrag)
                db.session.flush()
                return eintrag

            self.e_anna = ereignis(anna, 'VorfallAnna')
            self.e_ben = ereignis(ben, 'VorfallBen')
            self.e_zugewiesen = ereignis(ben, 'ZugewiesenKlara', assigned_user_id=nutzer['klara'].id)
            self.e_betroffen = ereignis(ben, 'BetroffenAnna')
            db.session.add(ErziehungsEreignisBetroffenesKind(event_id=self.e_betroffen.id, student_id=anna.id))
            db.session.add(ErziehungsEreignisElternkontakt(event_id=self.e_anna.id, kontakt_id=self.k_anna.id))
            db.session.commit()

            self.ids = {
                'anna': anna.id, 'ben': ben.id, 'beratung': beratung.id, 'ort': ort.id,
                'k_anna': self.k_anna.id, 'k_ben_klara': self.k_ben_klara.id, 'k_ben_fremd': self.k_ben_fremd.id,
                'e_anna': self.e_anna.id, 'e_ben': self.e_ben.id,
                'e_zugewiesen': self.e_zugewiesen.id, 'e_betroffen': self.e_betroffen.id,
                'vorlage_anna': self.e_anna.event_template_id,
            }

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _login(self, username):
        self.client = self.app.test_client()
        page = self.client.get('/login')
        token = CSRF_RE.search(page.get_data(as_text=True)).group(1)
        self.client.post('/login', data={'username': username, 'password': 'pass', '_csrf_token': token})
        return CSRF_RE.search(self.client.get('/').get_data(as_text=True)).group(1)

    def _user(self, name):
        return User.query.filter_by(username=name).one()

    # ------------------------------------------------------------------ Regel

    def test_contacts_visible_to_author_and_class_teachers(self):
        with self.app.app_context():
            def sichtbar(name):
                return {k.betreff for k in sichtbare_elternkontakte(Elternkontakt.query, self._user(name)).all()}

            self.assertEqual({'Protokoll Anna Klara', 'Protokoll Ben Klara'}, sichtbar('klara'))
            self.assertEqual({'Protokoll Anna Klara'}, sichtbar('fachl'))
            self.assertEqual({'Protokoll Ben Klara', 'Notiz Ben Fremd'}, sichtbar('fremd'))
            self.assertEqual(3, len(sichtbar('admin')))

            beratungen = sichtbare_elternkontakte(Elternberatung.query, self._user('klara'), Elternberatung).all()
            self.assertEqual([], beratungen)

    def test_events_visible_to_creator_assignee_and_class_teachers(self):
        with self.app.app_context():
            def sichtbar(name):
                return {e.event_template.name for e in sichtbare_ereignisse(ErziehungsEreignis.query, self._user(name)).all()}

            self.assertEqual({'VorfallAnna', 'ZugewiesenKlara', 'BetroffenAnna'}, sichtbar('klara'))
            self.assertEqual({'VorfallAnna', 'BetroffenAnna'}, sichtbar('fachl'))
            self.assertEqual(4, len(sichtbar('fremd')))
            self.assertEqual(4, len(sichtbar('admin')))

    # ------------------------------------------------------------------ Seiten

    def test_contact_pages_refuse_foreign_entries(self):
        self._login('klara')
        self.assertEqual(200, self.client.get(f"/erfassen/elternkontakte/view/{self.ids['k_anna']}").status_code)
        self.assertEqual(200, self.client.get(f"/erfassen/elternkontakte/view/{self.ids['k_ben_klara']}").status_code)
        for pfad in (
            f"/erfassen/elternkontakte/view/{self.ids['k_ben_fremd']}",
            f"/erfassen/elternkontakte/edit/{self.ids['k_ben_fremd']}",
            f"/erfassen/elternberatung/view/{self.ids['beratung']}",
        ):
            self.assertEqual(403, self.client.get(pfad).status_code, pfad)

        token = self._login('fachl')
        self.assertEqual(403, self.client.get(f"/erfassen/elternkontakte/view/{self.ids['k_ben_klara']}").status_code)
        self.assertEqual(403, self.client.get(
            f"/erfassen/elternkontakte/protokoll/export/odt/{self.ids['k_ben_klara']}").status_code)
        antwort = self.client.post(f"/erfassen/elternkontakte/delete/{self.ids['k_ben_fremd']}", data={'_csrf_token': token})
        self.assertEqual(403, antwort.status_code)

    def test_contact_list_shows_only_visible_entries(self):
        self._login('fachl')
        html = self.client.get('/erfassen/elternkontakte?schueler_id=').get_data(as_text=True)
        self.assertIn('Protokoll Anna Klara', html)
        self.assertNotIn('Protokoll Ben Klara', html)
        self.assertNotIn('Notiz Ben Fremd', html)
        self.assertNotIn('Beratung Ben Fremd', html)

        self._login('admin')
        html = self.client.get('/erfassen/elternkontakte?schueler_id=').get_data(as_text=True)
        self.assertIn('Notiz Ben Fremd', html)
        self.assertIn('Beratung Ben Fremd', html)

    def test_event_pages_refuse_foreign_events(self):
        self._login('klara')
        self.assertEqual(403, self.client.get(f"/erziehung/{self.ids['e_ben']}").status_code)
        self.assertEqual(403, self.client.get(f"/erziehung/{self.ids['e_ben']}/bearbeiten").status_code)
        for schluessel in ('e_anna', 'e_zugewiesen', 'e_betroffen'):
            self.assertEqual(200, self.client.get(f"/erziehung/{self.ids[schluessel]}").status_code, schluessel)

        html = self.client.get(f"/erziehung?schueler_id={self.ids['ben']}").get_data(as_text=True)
        self.assertIn('ZugewiesenKlara', html)
        self.assertIn('BetroffenAnna', html)
        self.assertNotIn('VorfallBen', html)

    def test_student_record_and_export_are_filtered(self):
        self._login('klara')
        html = self.client.get(f"/schuelerakte?schueler_id={self.ids['ben']}").get_data(as_text=True)
        self.assertIn('Protokoll Ben Klara', html)
        self.assertNotIn('Notiz Ben Fremd', html)
        self.assertIn('ZugewiesenKlara', html)
        self.assertNotIn('VorfallBen', html)

        with self.app.app_context():
            ben = db.session.get(Schueler, self.ids['ben'])
            akte = collect_record(ben, self._user('klara'))
            self.assertEqual(['Protokoll Ben Klara'], [k.betreff for k in akte['elternkontakte']])
            self.assertEqual([], akte['beratungen'])
            self.assertEqual({'ZugewiesenKlara', 'BetroffenAnna'}, {e.event_template.name for e in akte['ereignisse']})
            self.assertEqual(3, len(collect_record(ben, self._user('admin'))['ereignisse']))

    def test_editing_event_keeps_links_the_editor_cannot_see(self):
        token = self._login('fremd')
        html = self.client.get(f"/erziehung/{self.ids['e_anna']}").get_data(as_text=True)
        self.assertNotIn('Protokoll Anna Klara', html)
        self.assertIn('nur für Klassenleitung und Fachlehrkräfte des Kindes sichtbar', html)

        antwort = self.client.post(f"/erziehung/{self.ids['e_anna']}/bearbeiten", data={
            '_csrf_token': token, 'schueler_id': self.ids['anna'], 'datum': '2026-09-02',
            'event_template_id': self.ids['vorlage_anna'], 'ort_id': self.ids['ort'],
            'beschreibung': 'Geändert', 'status': 'offen',
        })
        self.assertEqual(302, antwort.status_code)
        with self.app.app_context():
            links = ErziehungsEreignisElternkontakt.query.filter_by(event_id=self.ids['e_anna']).all()
            self.assertEqual([self.ids['k_anna']], [link.kontakt_id for link in links])
            self.assertEqual('Geändert', db.session.get(ErziehungsEreignis, self.ids['e_anna']).beschreibung)


if __name__ == '__main__':
    unittest.main()
