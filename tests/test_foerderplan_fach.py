"""Fächer eines Förderplans: Mehrfachzuordnung und Automatik über den Bogen."""

import os
import re
import shutil
import tempfile
import unittest

from werkzeug.security import generate_password_hash

from app import create_app
from extensions import db
from foerderkurs import aktiver_plan_im_fach
from models import (
    Bogen,
    Fach,
    Foerdergrundlage,
    Foerderinhalt,
    Foerderplan,
    Item,
    Schueler,
    SystemKonfiguration,
    User,
    UserKlassenzuordnung,
)

CSRF_RE = re.compile(r'name="_csrf_token"\s+value="([^"]+)"')


class FoerderplanFachTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='km_planfach_')
        database_uri = os.environ.get('TEST_DATABASE_URL') or f"sqlite:///{self.tmpdir}/test.db"
        self.app = create_app({
            'TESTING': True, 'SECRET_KEY': 'test', 'SQLALCHEMY_DATABASE_URI': database_uri,
            'UPLOAD_FOLDER': os.path.join(self.tmpdir, 'uploads'),
            'PROTECTED_UPLOAD_FOLDER': os.path.join(self.tmpdir, 'protected_uploads'),
        })
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            admin = User(username='admin', password_hash=generate_password_hash('pass'), role='admin',
                         vorname='Adele', nachname='Admin')
            klara = User(username='klara', password_hash=generate_password_hash('pass'), role='teacher',
                         vorname='Klara', nachname='Klasse')
            db.session.add_all([admin, klara, SystemKonfiguration(schuljahr='2026/2027')])
            db.session.flush()
            db.session.add(UserKlassenzuordnung(user_id=klara.id, klasse='3a', rolle='klassenleitung'))
            anna = Schueler(vorname='Anna', nachname='Kind', klasse='3a', jahrgang=3)
            db.session.add(anna)
            db.session.flush()
            db.session.add(Foerdergrundlage(schueler_id=anna.id, besondere_staerken='Liest gern'))

            deutsch = Fach(name='Deutsch', sort_order=0)
            mathe = Fach(name='Mathematik', sort_order=1)
            db.session.add_all([deutsch, mathe])
            db.session.flush()
            bogen = Bogen(titel='Deutsch 3', fach_id=deutsch.id)
            ohne_fach = Bogen(titel='Arbeitsverhalten')
            db.session.add_all([bogen, ohne_fach])
            db.session.flush()
            item = Item(bogen_id=bogen.id, text='liest flüssig', bereich='Lesen')
            frei = Item(bogen_id=ohne_fach.id, text='arbeitet ausdauernd', bereich='Ausdauer')
            db.session.add_all([item, frei])
            db.session.commit()

            self.anna_id = anna.id
            self.fach_ids = {'deutsch': deutsch.id, 'mathe': mathe.id}
            self.bogen_id = bogen.id
            self.item_ids = {'lesen': item.id, 'frei': frei.id}

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

    def _anlegen(self, token, **daten):
        felder = {'_csrf_token': token, 'titel': 'Plan', 'foerderziel': ['Lesen'],
                  'ist_zustand': ['x'], 'soll_zustand': ['y'], 'massnahmen': ['z'], 'item_id': ['']}
        felder.update(daten)
        return self.client.post(f'/foerderplan/neu/{self.anna_id}', data=felder, follow_redirects=True)

    def test_plan_can_cover_several_subjects(self):
        token = self._login()
        self._anlegen(token, fach_id=[str(self.fach_ids['deutsch']), str(self.fach_ids['mathe'])])
        with self.app.app_context():
            plan = Foerderplan.query.one()
            self.assertEqual(['Deutsch', 'Mathematik'], plan.fach_namen)
            # Beide Förderkurse gelten damit als abgedeckt.
            self.assertIsNotNone(aktiver_plan_im_fach(self.anna_id, self.fach_ids['deutsch']))
            self.assertIsNotNone(aktiver_plan_im_fach(self.anna_id, self.fach_ids['mathe']))

    def test_subject_comes_from_the_sheet_of_the_chosen_competence(self):
        token = self._login()
        # Kein Fach angekreuzt - das Ziel stammt aber aus dem Deutschbogen.
        self._anlegen(token, item_id=[str(self.item_ids['lesen'])])
        with self.app.app_context():
            plan = Foerderplan.query.one()
            self.assertEqual(['Deutsch'], plan.fach_namen)
            self.assertEqual(self.item_ids['lesen'], Foerderinhalt.query.one().item_id)

    def test_sheet_without_subject_changes_nothing(self):
        token = self._login()
        self._anlegen(token, item_id=[str(self.item_ids['frei'])])
        with self.app.app_context():
            self.assertEqual([], Foerderplan.query.one().fach_namen)

    def test_editing_adds_the_subject_of_a_new_goal(self):
        token = self._login()
        self._anlegen(token, fach_id=[str(self.fach_ids['mathe'])])
        with self.app.app_context():
            plan_id = Foerderplan.query.one().id
        seite = self.client.get(f'/foerderplan/edit/{plan_id}')
        marke = re.search(r'name="concurrency_token" value="([^"]+)"', seite.get_data(as_text=True))
        self.client.post(f'/foerderplan/edit/{plan_id}', data={
            '_csrf_token': token, 'concurrency_token': marke.group(1) if marke else '',
            'titel': 'Plan', 'fach_id': [str(self.fach_ids['mathe'])],
            'foerderziel': ['Lesen', 'Rechnen'], 'ist_zustand': ['a', 'b'],
            'soll_zustand': ['c', 'd'], 'massnahmen': ['e', 'f'],
            'item_id': [str(self.item_ids['lesen']), ''],
        }, follow_redirects=True)
        with self.app.app_context():
            plan = db.session.get(Foerderplan, plan_id)
            self.assertEqual(['Deutsch', 'Mathematik'], plan.fach_namen)

    def test_admin_assigns_a_subject_to_a_sheet(self):
        token = self._login('admin')
        self.client.post(f'/admin/bogen/edit/{self.bogen_id}', data={
            '_csrf_token': token, 'titel': 'Deutsch 3', 'foerderempfehlung': '1',
            'fach_id': str(self.fach_ids['mathe']),
        }, follow_redirects=True)
        with self.app.app_context():
            self.assertEqual(self.fach_ids['mathe'], db.session.get(Bogen, self.bogen_id).fach_id)


if __name__ == '__main__':
    unittest.main()
