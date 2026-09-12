"""Regressionstests: geloeschte Datensaetze lassen keine Dateien zurueck.

Bisher entfernte kein Loeschpfad der Anwendung eine hochgeladene Datei. Auch
das Entfernen eines Anhangs loeschte nur den Datenbankeintrag, die Datei blieb
unter instance/protected_uploads liegen - auf Dauer eine wachsende Sammlung
personenbezogener Fotos und PDFs ohne Bezug und ohne Zugriffsweg.

Sonderfall Beobachtungsfoto: dort gibt es "Wiederherstellen". Das Foto darf
deshalb nicht sofort verschwinden, sonst kommt ein Eintrag ohne Bild zurueck.
Erreichbar ist immer nur die letzte Loeschung; das Foto der davor
verdraengten kann weg.
"""

import os
import re
import shutil
import tempfile
import unittest
import warnings

from werkzeug.security import generate_password_hash

from app import create_app
from extensions import db
from models import (
    Beobachtung,
    Bogen,
    ErziehungsEreignis,
    ErziehungsEreignisAnhang,
    ErziehungsEreignisKategorie,
    ErziehungsEreignisVorlage,
    ErziehungsOrt,
    Item,
    Schueler,
    User,
    WorkPlan,
    WorkPlanTask,
    WorkPlanTaskAttachment,
)

CSRF_RE = re.compile(r'name="_csrf_token"\s+value="([^"]+)"')


class UploadCleanupTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='km_upload_test_')
        self.protected_dir = os.path.join(self.tmpdir, 'protected_uploads')
        database_uri = os.environ.get('TEST_DATABASE_URL') or f"sqlite:///{self.tmpdir}/test.db"

        self.app = create_app({
            'TESTING': True,
            'SECRET_KEY': 'test-secret',
            'SQLALCHEMY_DATABASE_URI': database_uri,
            'UPLOAD_FOLDER': os.path.join(self.tmpdir, 'uploads'),
            'PROTECTED_UPLOAD_FOLDER': self.protected_dir,
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
            bogen = Bogen(titel='Sozialverhalten')
            db.session.add_all([schueler, bogen])
            db.session.flush()
            item = Item(bogen_id=bogen.id, bereich='Konflikt', text='Loest Streit friedlich')
            db.session.add(item)
            db.session.commit()
            self.schueler_id = schueler.id
            self.item_id = item.id
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
        self.assertEqual(
            self.client.post('/login', data={
                'username': 'admin', 'password': 'adminpass', '_csrf_token': token,
            }).status_code,
            302,
        )

    def _token(self):
        page = self.client.get('/admin/boegen')
        return CSRF_RE.search(page.get_data(as_text=True)).group(1)

    def _write_upload(self, rel_path):
        absolute = os.path.join(self.protected_dir, rel_path)
        os.makedirs(os.path.dirname(absolute), exist_ok=True)
        with open(absolute, 'wb') as handle:
            handle.write(b'testinhalt')
        return rel_path

    def _exists(self, rel_path):
        return os.path.isfile(os.path.join(self.protected_dir, rel_path))

    def _make_event_with_attachment(self):
        kategorie = ErziehungsEreignisKategorie(name='Konflikt')
        ort = ErziehungsOrt(name='Schulhof')
        db.session.add_all([kategorie, ort])
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
        rel = self._write_upload(f'erziehung/anhang-{ereignis.id}.pdf')
        anhang = ErziehungsEreignisAnhang(
            event_id=ereignis.id, file_path=rel, original_name='anhang.pdf',
        )
        db.session.add(anhang)
        db.session.commit()
        return ereignis.id, anhang.id, rel

    def _make_plan_with_attachment(self):
        admin = User.query.filter_by(username='admin').first()
        plan = WorkPlan(
            student_id=self.schueler_id,
            created_by_user_id=admin.id,
            period_start=db.func.current_date(),
            period_end=db.func.current_date(),
        )
        db.session.add(plan)
        db.session.flush()
        task = WorkPlanTask(work_plan_id=plan.id, title='Lesen ueben')
        db.session.add(task)
        db.session.flush()
        rel = self._write_upload(f'workplan/foto-{task.id}.jpg')
        anhang = WorkPlanTaskAttachment(task_id=task.id, file_path=rel)
        db.session.add(anhang)
        db.session.commit()
        return plan.id, task.id, anhang.id, rel

    # ------------------------------------------------------------------
    # Erzieherische Ereignisse
    # ------------------------------------------------------------------

    def test_deleting_event_attachment_removes_file(self):
        with self.app.app_context():
            event_id, attachment_id, rel = self._make_event_with_attachment()

        self.assertTrue(self._exists(rel))
        self.client.post(
            f'/erziehung/{event_id}/anhang/{attachment_id}/delete',
            data={'_csrf_token': self._token()},
            follow_redirects=True,
        )
        self.assertFalse(self._exists(rel), 'Anhangsdatei blieb liegen')
        with self.app.app_context():
            self.assertEqual(ErziehungsEreignisAnhang.query.count(), 0)

    def test_deleting_event_removes_attachment_files(self):
        with self.app.app_context():
            event_id, _, rel = self._make_event_with_attachment()

        self.assertTrue(self._exists(rel))
        self.client.post(
            f'/erziehung/{event_id}/delete',
            data={'_csrf_token': self._token()},
            follow_redirects=True,
        )
        self.assertFalse(self._exists(rel), 'Anhang des Ereignisses blieb liegen')
        with self.app.app_context():
            self.assertIsNone(db.session.get(ErziehungsEreignis, event_id))

    def test_deleting_event_does_not_warn_about_unmatched_rows(self):
        """Kindsaetze nicht doppelt loeschen.

        Journal, Konsequenzen und Anhaenge haengen per Kaskade am Ereignis. Sie
        zusaetzlich per Massenloeschung zu entfernen liess SQLAlchemy anschliessend
        Zeilen loeschen wollen, die es nicht mehr gab:

            SAWarning: DELETE statement on table 'erziehungs_ereignis_anhang'
            expected to delete 1 row(s); 0 were matched.

        Funktional harmlos, aber ein Hinweis auf doppelte Arbeit - und in einem
        Testlauf auf dem Produktivserver schlicht Rauschen.
        """
        with self.app.app_context():
            event_id, _, _ = self._make_event_with_attachment()

        with warnings.catch_warnings(record=True) as gesammelt:
            warnings.simplefilter('always')
            self.client.post(
                f'/erziehung/{event_id}/delete',
                data={'_csrf_token': self._token()},
                follow_redirects=True,
            )

        auffaellig = [
            str(eintrag.message) for eintrag in gesammelt
            if 'expected to delete' in str(eintrag.message)
        ]
        self.assertEqual([], auffaellig, 'Kindsätze werden doppelt gelöscht')

    # ------------------------------------------------------------------
    # Arbeitsplaene
    # ------------------------------------------------------------------

    def test_deleting_task_attachment_removes_file(self):
        with self.app.app_context():
            plan_id, task_id, attachment_id, rel = self._make_plan_with_attachment()

        self.assertTrue(self._exists(rel))
        self.client.post(
            f'/api/work-plans/{plan_id}/tasks/{task_id}/attachments/{attachment_id}',
            data={'_csrf_token': self._token()},
        )
        self.assertFalse(self._exists(rel), 'Aufgabenfoto blieb liegen')

    def test_deleting_task_removes_attachment_files(self):
        with self.app.app_context():
            plan_id, task_id, _, rel = self._make_plan_with_attachment()

        self.client.post(
            f'/api/work-plans/{plan_id}/tasks/{task_id}/delete',
            data={'_csrf_token': self._token()},
        )
        self.assertFalse(self._exists(rel), 'Foto der gelöschten Aufgabe blieb liegen')
        with self.app.app_context():
            self.assertIsNone(db.session.get(WorkPlanTask, task_id))

    def test_deleting_plan_via_page_removes_attachment_files(self):
        with self.app.app_context():
            plan_id, _, _, rel = self._make_plan_with_attachment()

        self.client.post(
            f'/arbeitsplan/{plan_id}/delete',
            data={'_csrf_token': self._token()},
            follow_redirects=True,
        )
        self.assertFalse(self._exists(rel), 'Foto des gelöschten Arbeitsplans blieb liegen')
        with self.app.app_context():
            self.assertIsNone(db.session.get(WorkPlan, plan_id))

    def test_deleting_plan_via_api_removes_attachment_files(self):
        with self.app.app_context():
            plan_id, _, _, rel = self._make_plan_with_attachment()

        self.client.delete(
            f'/api/work-plans/{plan_id}',
            data={'_csrf_token': self._token()},
        )
        self.assertFalse(self._exists(rel), 'Foto des gelöschten Arbeitsplans blieb liegen')

    # ------------------------------------------------------------------
    # Beobachtungsfotos: Rueckgaengig hat Vorrang
    # ------------------------------------------------------------------

    def _delete_observation(self, beobachtung_id):
        return self.client.post(
            f'/report/beobachtung/delete/{beobachtung_id}',
            data={'_csrf_token': self._token()},
            follow_redirects=True,
        )

    def _add_observation(self, rel_path):
        beobachtung = Beobachtung(
            schueler_id=self.schueler_id,
            item_id=self.item_id,
            wert=3,
            foto_pfad=rel_path,
        )
        db.session.add(beobachtung)
        db.session.commit()
        return beobachtung.id

    def test_observation_photo_survives_until_undo_window_closes(self):
        with self.app.app_context():
            erste = self._add_observation(self._write_upload('foto-eins.jpg'))
            zweite = self._add_observation(self._write_upload('foto-zwei.jpg'))

        self._delete_observation(erste)
        self.assertTrue(
            self._exists('foto-eins.jpg'),
            'Foto wurde entfernt, obwohl Wiederherstellen noch möglich ist',
        )

        # Die zweite Löschung verdrängt die erste aus dem Rückgängig-Speicher.
        self._delete_observation(zweite)
        self.assertFalse(
            self._exists('foto-eins.jpg'),
            'Foto der verdrängten Löschung blieb liegen',
        )
        self.assertTrue(
            self._exists('foto-zwei.jpg'),
            'Foto der letzten Löschung wurde zu früh entfernt',
        )

    def test_undo_restores_observation_with_its_photo(self):
        with self.app.app_context():
            beobachtung_id = self._add_observation(self._write_upload('foto-undo.jpg'))

        self._delete_observation(beobachtung_id)
        self.client.post(
            '/report/beobachtung/undo-delete',
            data={'_csrf_token': self._token()},
            follow_redirects=True,
        )

        self.assertTrue(self._exists('foto-undo.jpg'), 'Foto fehlt nach dem Wiederherstellen')
        with self.app.app_context():
            wieder = Beobachtung.query.one()
            self.assertEqual(wieder.foto_pfad, 'foto-undo.jpg')

    def test_undo_is_refused_when_the_student_is_gone(self):
        """Nach dem Löschen des Kindes darf Wiederherstellen nicht in einen Fehler laufen."""
        with self.app.app_context():
            beobachtung_id = self._add_observation(self._write_upload('foto-weg.jpg'))

        self._delete_observation(beobachtung_id)
        self.client.post(
            f'/admin/student/delete/{self.schueler_id}',
            data={'_csrf_token': self._token()},
            follow_redirects=True,
        )

        response = self.client.post(
            '/report/beobachtung/undo-delete',
            data={'_csrf_token': self._token()},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200, 'Wiederherstellen endete in einem Fehler')
        self.assertIn('existiert nicht mehr', response.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(Beobachtung.query.count(), 0)


if __name__ == '__main__':
    unittest.main()
