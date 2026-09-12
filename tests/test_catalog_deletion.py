"""Regressionstests fuer das Loeschen von Katalogdaten.

Boegen, Kompetenzen und die Erziehungs-Pools sind keine personenbezogenen
Daten, sondern die Struktur, auf die sich die Dokumentation bezieht. Ein
Loeschversuch endete bisher in einem IntegrityError und damit in einer
Fehlerseite, sobald irgendetwas darauf verwies.

Gewollt ist stattdessen: solange etwas daran haengt, wird nicht geloescht,
und die Rueckmeldung nennt den Grund. Bei den Pools gibt es zusaetzlich
"inaktiv" als vorgesehenen Weg, einen Eintrag ausser Dienst zu stellen.
"""

import os
import re
import shutil
import tempfile
import unittest

from werkzeug.security import generate_password_hash

from app import create_app
from extensions import db
from models import (
    Beobachtung,
    Bogen,
    ClassTaskLibrary,
    ClassTaskTemplate,
    ClassTaskTemplateCompetency,
    ErziehungsEreignis,
    ErziehungsEreignisKategorie,
    ErziehungsEreignisKonsequenz,
    ErziehungsEreignisVorlage,
    ErziehungsKonsequenz,
    ErziehungsOrt,
    Item,
    Schueler,
    User,
    WorkPlan,
    WorkPlanTask,
    WorkPlanTaskCompetency,
)

CSRF_RE = re.compile(r'name="_csrf_token"\s+value="([^"]+)"')


class CatalogDeletionTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='km_catalog_test_')
        database_uri = os.environ.get('TEST_DATABASE_URL') or f"sqlite:///{self.tmpdir}/test.db"

        self.app = create_app({
            'TESTING': True,
            'SECRET_KEY': 'test-secret',
            'SQLALCHEMY_DATABASE_URI': database_uri,
            'UPLOAD_FOLDER': os.path.join(self.tmpdir, 'uploads'),
            'PROTECTED_UPLOAD_FOLDER': os.path.join(self.tmpdir, 'protected_uploads'),
        })
        self.client = self.app.test_client()

        with self.app.app_context():
            db.create_all()
            db.session.add(User(
                username='admin',
                password_hash=generate_password_hash('adminpass'),
                role='admin',
            ))
            schueler = Schueler(vorname='Max', nachname='Muster', klasse='3a')
            db.session.add(schueler)
            db.session.commit()
            self.schueler_id = schueler.id
        self._login()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Hilfen
    # ------------------------------------------------------------------

    def _login(self):
        page = self.client.get('/login')
        token = CSRF_RE.search(page.get_data(as_text=True)).group(1)
        response = self.client.post('/login', data={
            'username': 'admin', 'password': 'adminpass', '_csrf_token': token,
        })
        self.assertEqual(response.status_code, 302)

    def _post(self, url):
        """Sendet einen POST mit gueltigem CSRF-Token und folgt der Umleitung."""
        page = self.client.get('/admin/boegen')
        token = CSRF_RE.search(page.get_data(as_text=True)).group(1)
        return self.client.post(url, data={'_csrf_token': token}, follow_redirects=True)

    def _make_bogen_with_item(self):
        bogen = Bogen(titel='Sozialverhalten')
        db.session.add(bogen)
        db.session.flush()
        item = Item(bogen_id=bogen.id, bereich='Konflikt', text='Loest Streit friedlich')
        db.session.add(item)
        db.session.commit()
        return bogen.id, item.id

    def _make_workplan_task(self, item_id):
        admin = User.query.filter_by(username='admin').first()
        plan = WorkPlan(
            student_id=self.schueler_id,
            created_by_user_id=admin.id,
            period_start=db.func.current_date(),
            period_end=db.func.current_date(),
        )
        db.session.add(plan)
        db.session.flush()
        task = WorkPlanTask(work_plan_id=plan.id, title='Aufgabe')
        db.session.add(task)
        db.session.flush()
        db.session.add(WorkPlanTaskCompetency(task_id=task.id, item_id=item_id))
        db.session.commit()

    def _make_library_template(self, item_id):
        library = ClassTaskLibrary(class_name='3a', name='Bibliothek')
        db.session.add(library)
        db.session.flush()
        template = ClassTaskTemplate(library_id=library.id, title='Vorlage')
        db.session.add(template)
        db.session.flush()
        db.session.add(ClassTaskTemplateCompetency(template_id=template.id, item_id=item_id))
        db.session.commit()

    def _make_pool_with_event(self):
        kategorie = ErziehungsEreignisKategorie(name='Konflikt')
        ort = ErziehungsOrt(name='Schulhof')
        konsequenz = ErziehungsKonsequenz(name='Gespraech')
        db.session.add_all([kategorie, ort, konsequenz])
        db.session.flush()
        vorlage = ErziehungsEreignisVorlage(category_id=kategorie.id, name='Streit')
        db.session.add(vorlage)
        db.session.flush()
        ereignis = ErziehungsEreignis(
            student_id=self.schueler_id,
            event_template_id=vorlage.id,
            ort_id=ort.id,
            beschreibung='Vorfall',
        )
        db.session.add(ereignis)
        db.session.flush()
        db.session.add(ErziehungsEreignisKonsequenz(
            event_id=ereignis.id, consequence_id=konsequenz.id,
        ))
        db.session.commit()
        return {
            'kategorie': kategorie.id,
            'vorlage': vorlage.id,
            'ort': ort.id,
            'konsequenz': konsequenz.id,
        }

    # ------------------------------------------------------------------
    # Kompetenzen
    # ------------------------------------------------------------------

    def test_item_with_observations_is_not_deleted(self):
        with self.app.app_context():
            _, item_id = self._make_bogen_with_item()
            db.session.add(Beobachtung(schueler_id=self.schueler_id, item_id=item_id, wert=3))
            db.session.commit()

        response = self._post(f'/admin/item/delete/{item_id}')
        self.assertEqual(response.status_code, 200)
        text = response.get_data(as_text=True)
        self.assertIn('kann nicht gelöscht werden', text)
        self.assertIn('1 Beobachtung', text)
        self.assertIn('1 Kind', text)

        with self.app.app_context():
            self.assertIsNotNone(db.session.get(Item, item_id))
            self.assertEqual(Beobachtung.query.count(), 1)

    def test_item_used_in_workplan_is_not_deleted(self):
        with self.app.app_context():
            _, item_id = self._make_bogen_with_item()
            self._make_workplan_task(item_id)

        response = self._post(f'/admin/item/delete/{item_id}')
        text = response.get_data(as_text=True)
        self.assertIn('kann nicht gelöscht werden', text)
        self.assertIn('Arbeitsplänen', text)

        with self.app.app_context():
            self.assertIsNotNone(db.session.get(Item, item_id))
            self.assertEqual(WorkPlanTaskCompetency.query.count(), 1)

    def test_item_used_in_task_library_is_not_deleted(self):
        with self.app.app_context():
            _, item_id = self._make_bogen_with_item()
            self._make_library_template(item_id)

        response = self._post(f'/admin/item/delete/{item_id}')
        text = response.get_data(as_text=True)
        self.assertIn('kann nicht gelöscht werden', text)
        self.assertIn('Aufgabenbibliotheken', text)

        with self.app.app_context():
            self.assertIsNotNone(db.session.get(Item, item_id))

    def test_unused_item_is_deleted(self):
        with self.app.app_context():
            _, item_id = self._make_bogen_with_item()

        response = self._post(f'/admin/item/delete/{item_id}')
        self.assertIn('gelöscht', response.get_data(as_text=True))

        with self.app.app_context():
            self.assertIsNone(db.session.get(Item, item_id))

    # ------------------------------------------------------------------
    # Boegen
    # ------------------------------------------------------------------

    def test_bogen_with_observations_is_not_deleted(self):
        with self.app.app_context():
            bogen_id, item_id = self._make_bogen_with_item()
            db.session.add(Beobachtung(schueler_id=self.schueler_id, item_id=item_id, wert=2))
            db.session.commit()

        response = self._post(f'/admin/bogen/delete/{bogen_id}')
        text = response.get_data(as_text=True)
        self.assertIn('kann nicht gelöscht werden', text)
        self.assertIn('1 Beobachtung', text)

        with self.app.app_context():
            self.assertIsNotNone(db.session.get(Bogen, bogen_id))
            self.assertIsNotNone(db.session.get(Item, item_id))
            self.assertEqual(Beobachtung.query.count(), 1)

    def test_unused_bogen_is_deleted_with_its_items(self):
        with self.app.app_context():
            bogen_id, item_id = self._make_bogen_with_item()

        response = self._post(f'/admin/bogen/delete/{bogen_id}')
        self.assertIn('gelöscht', response.get_data(as_text=True))

        with self.app.app_context():
            self.assertIsNone(db.session.get(Bogen, bogen_id))
            self.assertIsNone(db.session.get(Item, item_id), 'Kompetenz des Bogens blieb stehen')

    # ------------------------------------------------------------------
    # Erziehungs-Pools
    # ------------------------------------------------------------------

    def test_pool_entries_in_use_are_not_deleted(self):
        with self.app.app_context():
            ids = self._make_pool_with_event()

        faelle = [
            ('orte', ids['ort'], ErziehungsOrt),
            ('ereignisse', ids['vorlage'], ErziehungsEreignisVorlage),
            ('kategorien', ids['kategorie'], ErziehungsEreignisKategorie),
            ('konsequenzen', ids['konsequenz'], ErziehungsKonsequenz),
        ]

        for kind, entry_id, model in faelle:
            with self.subTest(kind=kind):
                response = self._post(f'/admin/erziehung/{kind}/delete/{entry_id}')
                text = response.get_data(as_text=True)
                self.assertIn('kann nicht gelöscht werden', text)
                self.assertIn('inaktiv', text, 'Hinweis auf den vorgesehenen Weg fehlt')
                with self.app.app_context():
                    self.assertIsNotNone(db.session.get(model, entry_id))

        with self.app.app_context():
            self.assertEqual(ErziehungsEreignis.query.count(), 1, 'Ereignis wurde beschädigt')

    def test_unused_pool_entry_is_deleted(self):
        with self.app.app_context():
            ort = ErziehungsOrt(name='Turnhalle')
            db.session.add(ort)
            db.session.commit()
            ort_id = ort.id

        response = self._post(f'/admin/erziehung/orte/delete/{ort_id}')
        self.assertIn('gelöscht', response.get_data(as_text=True))

        with self.app.app_context():
            self.assertIsNone(db.session.get(ErziehungsOrt, ort_id))

    def test_pool_entry_can_be_deactivated(self):
        with self.app.app_context():
            ids = self._make_pool_with_event()

        response = self._post(f'/admin/erziehung/orte/deaktivieren/{ids["ort"]}')
        self.assertIn('inaktiv', response.get_data(as_text=True))

        with self.app.app_context():
            ort = db.session.get(ErziehungsOrt, ids['ort'])
            self.assertFalse(ort.is_active)
            self.assertEqual(ErziehungsEreignis.query.count(), 1, 'Ereignis blieb nicht lesbar')

    def test_deactivation_requires_admin(self):
        with self.app.app_context():
            ids = self._make_pool_with_event()
            db.session.add(User(
                username='lehrkraft',
                password_hash=generate_password_hash('lehrpass'),
                role='teacher',
            ))
            db.session.commit()

        self.client.post('/logout')
        page = self.client.get('/login')
        token = CSRF_RE.search(page.get_data(as_text=True)).group(1)
        self.client.post('/login', data={
            'username': 'lehrkraft', 'password': 'lehrpass', '_csrf_token': token,
        })
        self.client.post(
            f'/admin/erziehung/orte/deaktivieren/{ids["ort"]}',
            data={'_csrf_token': token},
        )

        with self.app.app_context():
            ort = db.session.get(ErziehungsOrt, ids['ort'])
            self.assertTrue(ort.is_active, 'Eine Lehrkraft konnte einen Pool-Eintrag deaktivieren')


if __name__ == '__main__':
    unittest.main()
