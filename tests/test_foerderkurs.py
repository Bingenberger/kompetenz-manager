"""Förderkurse: Katalog, Teilnahmen und die Regel „Kurs braucht Förderplan im Fach“."""

import os
import re
import shutil
import tempfile
import unittest
from datetime import date, timedelta

from werkzeug.security import generate_password_hash

from app import create_app
from benachrichtigungen import FOERDERKURS_OHNE_PLAN, erinnere_an_foerderkurse
from extensions import db
from foerderkurs import ERINNERUNG_ABSTAND_TAGE, faellige_erinnerungen, kurse_fuer_kind, offene_plaene, trage_ein
from konferenz import erstelle_konferenz, schreibe_feld
from models import (
    Fach,
    Foerdergrundlage,
    Foerderkurs,
    FoerderkursJahrgang,
    FoerderkursTeilnahme,
    Foerderplan,
    Notification,
    Schueler,
    SystemKonfiguration,
    User,
    UserKlassenzuordnung,
)
from time_utils import utc_now

CSRF_RE = re.compile(r'name="_csrf_token"\s+value="([^"]+)"')


class FoerderkursTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='km_foerderkurs_')
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
                      for name, rolle in (('admin', 'admin'), ('klara', 'teacher'), ('kai', 'teacher'),
                                          ('leitung', 'schulleitung'))}
            db.session.add_all(list(nutzer.values()) + [SystemKonfiguration(schuljahr='2026/2027')])
            db.session.flush()
            db.session.add_all([
                UserKlassenzuordnung(user_id=nutzer['klara'].id, klasse='3a', rolle='klassenleitung'),
                UserKlassenzuordnung(user_id=nutzer['kai'].id, klasse='4a', rolle='klassenleitung'),
            ])
            kinder = {}
            for vorname, klasse, jahrgang in (('Anna', '3a', 3), ('Ben', '3a', 3), ('Vera', '4a', 4)):
                kinder[vorname] = Schueler(vorname=vorname, nachname='Kind', klasse=klasse, jahrgang=jahrgang)
            db.session.add_all(kinder.values())
            db.session.flush()
            db.session.add(Foerdergrundlage(schueler_id=kinder['Anna'].id, besondere_staerken='Erzählt gern'))
            deutsch = Fach(name='Deutsch', sort_order=0)
            mathe = Fach(name='Mathematik', sort_order=1)
            db.session.add_all([deutsch, mathe])
            db.session.flush()
            lesekurs = Foerderkurs(name='Lesekurs', fach_id=deutsch.id, schuljahr='2026/2027',
                                   zeit='Mo 3. Stunde', leitung_user_id=nutzer['kai'].id)
            rechnen = Foerderkurs(name='Rechenkurs', fach_id=mathe.id)
            db.session.add_all([lesekurs, rechnen])
            db.session.flush()
            db.session.add_all([FoerderkursJahrgang(kurs_id=lesekurs.id, jahrgang=3),
                                FoerderkursJahrgang(kurs_id=rechnen.id, jahrgang=4)])
            db.session.commit()
            self.ids = {name: kind.id for name, kind in kinder.items()}
            self.user_ids = {name: user.id for name, user in nutzer.items()}
            self.fach_ids = {'deutsch': deutsch.id, 'mathe': mathe.id}
            self.kurs_ids = {'lesen': lesekurs.id, 'rechnen': rechnen.id}

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
        return CSRF_RE.search(self.client.get('/foerderkurse').get_data(as_text=True)).group(1)

    def _eintragen(self, kurs='lesen', kind='Anna'):
        return self.client.post(f'/foerderkurse/{self.kurs_ids[kurs]}/teilnahme',
                                data={'_csrf_token': self._token(), 'schueler_id': str(self.ids[kind])})

    def _plan(self, kind='Anna', fach='deutsch', status='aktiv'):
        with self.app.app_context():
            plan = Foerderplan(schueler_id=self.ids[kind], titel='Lesen üben', status=status,
                               datum_erstellung=utc_now().date())
            plan.faecher = [db.session.get(Fach, self.fach_ids[fach])]
            db.session.add(plan)
            db.session.commit()

    # ------------------------------------------------------------------ Katalog

    def test_only_admin_maintains_catalog(self):
        self._login('klara')
        antwort = self.client.get('/admin/foerderkurse', follow_redirects=True)
        self.assertIn('Zugriff verweigert', antwort.get_data(as_text=True))

        token = self._login('admin')
        html = self.client.get('/admin/foerderkurse').get_data(as_text=True)
        self.assertIn('Lesekurs', html)
        self.client.post('/admin/faecher', data={'_csrf_token': token, 'aktion': 'neu', 'name': 'Englisch'})
        self.client.post('/admin/foerderkurse/neu', data={
            '_csrf_token': token, 'name': 'Sprachkurs', 'fach_id': self.fach_ids['deutsch'],
            'jahrgaenge': ['3', '4'], 'is_active': '1', 'schuljahr': '2026/2027',
        })
        with self.app.app_context():
            self.assertEqual(3, Fach.query.count())
            kurs = Foerderkurs.query.filter_by(name='Sprachkurs').one()
            self.assertEqual([3, 4], kurs.jahrgaenge)

    def test_courses_are_offered_by_year_group(self):
        with self.app.app_context():
            anna = db.session.get(Schueler, self.ids['Anna'])
            vera = db.session.get(Schueler, self.ids['Vera'])
            self.assertEqual(['Lesekurs'], [k.name for k in kurse_fuer_kind(anna)])
            self.assertEqual(['Rechenkurs'], [k.name for k in kurse_fuer_kind(vera)])

    # ------------------------------------------------------------------ Teilnahmen

    def test_teachers_enrol_only_their_own_children(self):
        self._login('klara')
        self.assertEqual(302, self._eintragen().status_code)
        with self.app.app_context():
            self.assertEqual(1, FoerderkursTeilnahme.query.filter_by(bis=None).count())
        # Fremdes Kind: verboten.
        self.assertEqual(403, self.client.post(f'/foerderkurse/{self.kurs_ids["rechnen"]}/teilnahme',
                                               data={'_csrf_token': self._token(), 'schueler_id': str(self.ids['Vera'])}).status_code)
        # Die Schulleitung darf überall eintragen.
        self._login('leitung')
        self.assertEqual(302, self.client.post(f'/foerderkurse/{self.kurs_ids["rechnen"]}/teilnahme',
                                               data={'_csrf_token': self._token(), 'schueler_id': str(self.ids['Vera'])}).status_code)
        with self.app.app_context():
            teilnahme = FoerderkursTeilnahme.query.filter_by(schueler_id=self.ids['Vera']).one()
            eintrag_id = teilnahme.id
        antwort = self.client.post(f'/foerderkurse/{self.kurs_ids["rechnen"]}/teilnahme',
                                   data={'_csrf_token': self._token(), 'beenden': str(eintrag_id)}, follow_redirects=True)
        self.assertIn('beendet', antwort.get_data(as_text=True))
        with self.app.app_context():
            self.assertIsNotNone(db.session.get(FoerderkursTeilnahme, eintrag_id).bis)

    # ------------------------------------------------------------------ Regel und Erinnerung

    def test_missing_plan_shows_up_everywhere(self):
        self._login('klara')
        self._eintragen()
        with self.app.app_context():
            klara = db.session.get(User, self.user_ids['klara'])
            offen = offene_plaene(klara)
            self.assertEqual(1, len(offen))
            self.assertEqual('Lesekurs', offen[0].kurs.name)

        self.assertIn('Anna Kind', self.client.get('/foerderkurse/ohne-plan').get_data(as_text=True))
        self.assertIn('Förderplan fehlt: Anna Kind', self.client.get('/').get_data(as_text=True))
        self.assertIn('Förderplan fehlt', self.client.get(f'/schuelerakte?schueler_id={self.ids["Anna"]}').get_data(as_text=True))

        # Ein Plan im falschen Fach hilft nicht, einer im richtigen schon.
        self._plan(fach='mathe')
        with self.app.app_context():
            self.assertEqual(1, len(offene_plaene(db.session.get(User, self.user_ids['klara']))))
        self._plan(fach='deutsch')
        with self.app.app_context():
            self.assertEqual([], offene_plaene(db.session.get(User, self.user_ids['klara'])))
        self.assertIn('Förderplan vorhanden', self.client.get(f'/schuelerakte?schueler_id={self.ids["Anna"]}').get_data(as_text=True))

    def test_reminder_repeats_after_the_interval(self):
        self._login('klara')
        self._eintragen()
        with self.app.app_context():
            self.assertEqual(1, len(faellige_erinnerungen()))
            # Klassenleitung der 3a und Kursleitung bekommen die Erinnerung.
            self.assertEqual(2, erinnere_an_foerderkurse())
            db.session.commit()
            empfaenger = {n.user_id for n in Notification.query.filter_by(kind=FOERDERKURS_OHNE_PLAN).all()}
            self.assertEqual({self.user_ids['klara'], self.user_ids['kai']}, empfaenger)
            # Kurz danach nicht erneut …
            self.assertEqual(0, erinnere_an_foerderkurse())
            teilnahme = FoerderkursTeilnahme.query.one()
            teilnahme.erinnert_am = utc_now().date() - timedelta(days=ERINNERUNG_ABSTAND_TAGE + 1)
            db.session.commit()
            # … nach dem Abstand schon.
            self.assertEqual(2, erinnere_an_foerderkurse())
            db.session.commit()

        self._plan(fach='deutsch')
        with self.app.app_context():
            self.assertEqual(0, erinnere_an_foerderkurse())

    def test_plan_wizard_keeps_the_subject(self):
        token = self._login('klara')
        seite = self.client.get(f'/foerderplan/neu/{self.ids["Anna"]}?fach_id={self.fach_ids["deutsch"]}')
        self.assertEqual(200, seite.status_code)
        self.assertIn('Fach', seite.get_data(as_text=True))
        self.client.post(f'/foerderplan/neu/{self.ids["Anna"]}', data={
            '_csrf_token': token, 'titel': 'Lesen', 'fach_id': str(self.fach_ids['deutsch']),
            'foerderziel': ['Silben'], 'ist_zustand': ['x'], 'soll_zustand': ['y'], 'massnahmen': ['z'],
        })
        with self.app.app_context():
            plan = Foerderplan.query.one()
            self.assertEqual([self.fach_ids['deutsch']], [fach.id for fach in plan.faecher])

    # ------------------------------------------------------------------ Konferenz

    def test_conference_choice_enrols_the_child(self):
        with self.app.app_context():
            leitung = db.session.get(User, self.user_ids['leitung'])
            konferenz = erstelle_konferenz('2026/2027', 3, 'Herbst', date(2026, 11, 10), leitung)
            db.session.commit()
            eintrag = next(e for e in konferenz.kinder if e.schueler_id == self.ids['Anna'])
            schreibe_feld(eintrag, 'foerderkurs_id', str(self.kurs_ids['lesen']), leitung)
            db.session.commit()
            self.assertTrue(eintrag.massnahme_foerderkurs)
            self.assertEqual('Lesekurs', eintrag.foerderkurs_name)
            teilnahme = FoerderkursTeilnahme.query.filter_by(schueler_id=self.ids['Anna'], bis=None).one()
            self.assertEqual(self.kurs_ids['lesen'], teilnahme.kurs_id)
            konferenz_id, eintrag_id = konferenz.id, eintrag.id

        self._login('leitung')
        html = self.client.get(f'/konferenz/{konferenz_id}/phase/6').get_data(as_text=True)
        self.assertIn('Lesekurs (Deutsch)', html)

    def test_deleting_a_child_removes_its_enrolments(self):
        self._login('klara')
        self._eintragen()
        with self.app.app_context():
            db.session.delete(db.session.get(Schueler, self.ids['Anna']))
            db.session.commit()
            self.assertEqual(0, FoerderkursTeilnahme.query.count())
            self.assertEqual(2, Foerderkurs.query.count())


if __name__ == '__main__':
    unittest.main()
