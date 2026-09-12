"""Regressionstests fuer das vollstaendige Loeschen eines Schuelers.

Hintergrund: admin_student_delete entfernte nur Beobachtungen und den
Schuelerdatensatz. Je nach Datenlage brach die Loeschung mit einem
IntegrityError ab oder hinterliess verwaiste Foerderplaene mit
personenbezogenen Beschreibungen - bei gleichzeitiger Meldung, es seien
"alle zugehoerigen Daten" geloescht worden.
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
    Elternberatung,
    Elternkontakt,
    ErziehungsEreignis,
    ErziehungsEreignisAnhang,
    ErziehungsEreignisBetroffenesKind,
    ErziehungsEreignisKategorie,
    ErziehungsEreignisElternkontakt,
    ErziehungsEreignisKonsequenz,
    ErziehungsEreignisLog,
    ErziehungsEreignisVorlage,
    ErziehungsKonsequenz,
    ErziehungsOrt,
    Foerdergrundlage,
    Foerderinhalt,
    Foerderplan,
    Item,
    Schueler,
    User,
    WorkPlan,
    WorkPlanTask,
    WorkPlanTaskAttachment,
    WorkPlanTaskCompetency,
    WorkPlanTaskEvaluation,
)

CSRF_RE = re.compile(r'name="_csrf_token"\s+value="([^"]+)"')


class StudentDeletionTestCase(unittest.TestCase):
    """Legt ein vollstaendig dokumentiertes Kind an und loescht es ueber die Route."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='km_delete_test_')
        self.db_path = os.path.join(self.tmpdir, 'test.db')
        self.upload_dir = os.path.join(self.tmpdir, 'uploads')
        self.protected_dir = os.path.join(self.tmpdir, 'protected_uploads')

        # TEST_DATABASE_URL erlaubt denselben Testlauf gegen PostgreSQL, wie es
        # produktiv eingesetzt wird. Nur mit einer Wegwerf-Datenbank verwenden:
        # tearDown ruft drop_all().
        database_uri = os.environ.get('TEST_DATABASE_URL') or f"sqlite:///{self.db_path}"

        self.app = create_app({
            'TESTING': True,
            'SECRET_KEY': 'test-secret',
            'SQLALCHEMY_DATABASE_URI': database_uri,
            'UPLOAD_FOLDER': self.upload_dir,
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
            db.session.commit()
            self.ziel_id, self.andere_id = self._build_documented_students()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Aufbau
    # ------------------------------------------------------------------

    def _write_upload(self, rel_path):
        """Legt eine Datei im geschuetzten Upload-Ordner an und gibt den relativen Pfad zurueck."""
        absolute = os.path.join(self.protected_dir, rel_path)
        os.makedirs(os.path.dirname(absolute), exist_ok=True)
        with open(absolute, 'wb') as handle:
            handle.write(b'testinhalt')
        return rel_path

    def _build_documented_students(self):
        """Ein Kind mit Daten in jedem Modul, plus ein zweites Kind als Kontrollgruppe."""
        ziel = Schueler(vorname='Max', nachname='Muster', klasse='3a')
        andere = Schueler(vorname='Lisa', nachname='Lustig', klasse='3a')
        db.session.add_all([ziel, andere])
        db.session.flush()

        bogen = Bogen(titel='Sozialverhalten')
        db.session.add(bogen)
        db.session.flush()
        item = Item(bogen_id=bogen.id, bereich='Konflikt', text='Loest Streit friedlich')
        db.session.add(item)
        db.session.flush()

        # Beobachtung mit Foto
        self.foto_rel = self._write_upload('beobachtung-foto.jpg')
        db.session.add(Beobachtung(
            schueler_id=ziel.id,
            item_id=item.id,
            wert=2,
            kommentar='vertraulicher Kommentar',
            foto_pfad=self.foto_rel,
        ))

        # Foerdergrundlage und Foerderplan mit Inhalt
        db.session.add(Foerdergrundlage(schueler_id=ziel.id, besondere_staerken='ausdauernd'))
        plan = Foerderplan(schueler_id=ziel.id, titel='Konzentration')
        db.session.add(plan)
        db.session.flush()
        db.session.add(Foerderinhalt(
            plan_id=plan.id,
            foerderziel='Ziel',
            ist_zustand='sensible Beschreibung',
        ))

        # Elternkontakte und Beratung
        notiz = Elternkontakt(
            schueler_id=ziel.id,
            eintrag_typ='notiz',
            betreff='Telefonat',
            mitteilung='vertraulicher Inhalt',
        )
        protokoll = Elternkontakt(
            schueler_id=ziel.id,
            eintrag_typ='protokoll',
            betreff='Elterngespraech',
            besprochenes='sensibler Gespraechsinhalt',
        )
        db.session.add_all([notiz, protokoll])
        db.session.add(Elternberatung(schueler_id=ziel.id, anlass='Beratungsanlass'))
        db.session.flush()

        # Erzieherisches Ereignis mit allen Verknuepfungen
        kategorie = ErziehungsEreignisKategorie(name='Konflikt')
        ort = ErziehungsOrt(name='Schulhof')
        konsequenz = ErziehungsKonsequenz(name='Gespraech')
        db.session.add_all([kategorie, ort, konsequenz])
        db.session.flush()
        vorlage = ErziehungsEreignisVorlage(category_id=kategorie.id, name='Streit')
        db.session.add(vorlage)
        db.session.flush()

        ereignis = ErziehungsEreignis(
            student_id=ziel.id,
            event_template_id=vorlage.id,
            ort_id=ort.id,
            beschreibung='Vorfall mit personenbezogener Schilderung',
        )
        db.session.add(ereignis)
        db.session.flush()
        db.session.add(ErziehungsEreignisLog(event_id=ereignis.id, action='created'))
        db.session.add(ErziehungsEreignisKonsequenz(event_id=ereignis.id, consequence_id=konsequenz.id))
        db.session.add(ErziehungsEreignisElternkontakt(event_id=ereignis.id, kontakt_id=notiz.id))
        # Das zweite Kind ist als betroffenes Kind mit dem Ereignis verknuepft.
        db.session.add(ErziehungsEreignisBetroffenesKind(event_id=ereignis.id, student_id=andere.id))
        self.anhang_rel = self._write_upload('erziehung/anhang.pdf')
        db.session.add(ErziehungsEreignisAnhang(
            event_id=ereignis.id,
            file_path=self.anhang_rel,
            original_name='anhang.pdf',
        ))

        # Ereignis des zweiten Kindes, bei dem das Zielkind betroffen ist
        gegenereignis = ErziehungsEreignis(
            student_id=andere.id,
            event_template_id=vorlage.id,
            ort_id=ort.id,
            beschreibung='Vorfall des anderen Kindes',
        )
        db.session.add(gegenereignis)
        db.session.flush()
        db.session.add(ErziehungsEreignisBetroffenesKind(event_id=gegenereignis.id, student_id=ziel.id))

        # Arbeitsplan mit Aufgabe, Kompetenzbezug, Foto und Evaluation
        admin = User.query.filter_by(username='admin').first()
        arbeitsplan = WorkPlan(
            student_id=ziel.id,
            created_by_user_id=admin.id,
            period_start=db.func.current_date(),
            period_end=db.func.current_date(),
        )
        db.session.add(arbeitsplan)
        db.session.flush()
        aufgabe = WorkPlanTask(work_plan_id=arbeitsplan.id, title='Lesen ueben')
        db.session.add(aufgabe)
        db.session.flush()
        db.session.add(WorkPlanTaskCompetency(task_id=aufgabe.id, item_id=item.id))
        db.session.add(WorkPlanTaskEvaluation(task_id=aufgabe.id, rating='teilweise'))
        self.plan_foto_rel = self._write_upload('workplan/aufgabe.jpg')
        db.session.add(WorkPlanTaskAttachment(task_id=aufgabe.id, file_path=self.plan_foto_rel))

        # Kontrollgruppe: eigene Daten des zweiten Kindes
        db.session.add(Foerderplan(schueler_id=andere.id, titel='Plan des anderen Kindes'))
        db.session.add(Elternkontakt(
            schueler_id=andere.id,
            eintrag_typ='notiz',
            betreff='Notiz des anderen Kindes',
        ))

        db.session.commit()
        return ziel.id, andere.id

    # ------------------------------------------------------------------
    # Hilfen
    # ------------------------------------------------------------------

    def _login_admin(self):
        login_page = self.client.get('/login')
        token = CSRF_RE.search(login_page.get_data(as_text=True)).group(1)
        response = self.client.post(
            '/login',
            data={'username': 'admin', 'password': 'adminpass', '_csrf_token': token},
        )
        self.assertEqual(response.status_code, 302)

    def _delete_student(self, student_id):
        page = self.client.get('/admin/students')
        token = CSRF_RE.search(page.get_data(as_text=True)).group(1)
        return self.client.post(
            f'/admin/student/delete/{student_id}',
            data={'_csrf_token': token},
            follow_redirects=True,
        )

    def _upload_exists(self, rel_path):
        return os.path.isfile(os.path.join(self.protected_dir, rel_path))

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    def test_deleting_documented_student_removes_every_related_record(self):
        self._login_admin()
        response = self._delete_student(self.ziel_id)
        self.assertEqual(response.status_code, 200)

        with self.app.app_context():
            self.assertIsNone(db.session.get(Schueler, self.ziel_id))

            # Kein Datensatz darf mit Bezug auf das Kind zuruegbleiben ...
            self.assertEqual(Beobachtung.query.filter_by(schueler_id=self.ziel_id).count(), 0)
            self.assertEqual(Foerdergrundlage.query.filter_by(schueler_id=self.ziel_id).count(), 0)
            self.assertEqual(Foerderplan.query.filter_by(schueler_id=self.ziel_id).count(), 0)
            self.assertEqual(Elternkontakt.query.filter_by(schueler_id=self.ziel_id).count(), 0)
            self.assertEqual(Elternberatung.query.filter_by(schueler_id=self.ziel_id).count(), 0)
            self.assertEqual(ErziehungsEreignis.query.filter_by(student_id=self.ziel_id).count(), 0)
            self.assertEqual(WorkPlan.query.filter_by(student_id=self.ziel_id).count(), 0)
            self.assertEqual(
                ErziehungsEreignisBetroffenesKind.query.filter_by(student_id=self.ziel_id).count(),
                0,
                'Verknuepfung als betroffenes Kind im Ereignis eines anderen Kindes blieb stehen',
            )

            # ... und auch nicht ohne Bezug (verwaist mit schueler_id = NULL).
            self.assertEqual(
                Foerderplan.query.filter_by(schueler_id=None).count(),
                0,
                'Verwaister Foerderplan ohne Schuelerbezug zurueckgeblieben',
            )
            self.assertEqual(
                Foerderinhalt.query.count(),
                0,
                'Foerderinhalt mit sensibler Beschreibung zurueckgeblieben',
            )

    def test_deleting_student_removes_dependent_child_records(self):
        """Enkel-Datensaetze haengen an Ereignis und Arbeitsplan, nicht am Kind."""
        self._login_admin()
        self._delete_student(self.ziel_id)

        with self.app.app_context():
            # Das Ereignis des Zielkindes ist weg, damit auch sein Journal und seine Anhaenge.
            self.assertEqual(ErziehungsEreignisLog.query.count(), 0)
            self.assertEqual(ErziehungsEreignisKonsequenz.query.count(), 0)
            self.assertEqual(ErziehungsEreignisElternkontakt.query.count(), 0)
            self.assertEqual(ErziehungsEreignisAnhang.query.count(), 0)

            self.assertEqual(WorkPlanTask.query.count(), 0)
            self.assertEqual(WorkPlanTaskCompetency.query.count(), 0)
            self.assertEqual(WorkPlanTaskEvaluation.query.count(), 0)
            self.assertEqual(WorkPlanTaskAttachment.query.count(), 0)

    def test_deleting_student_removes_uploaded_files(self):
        self._login_admin()
        self.assertTrue(self._upload_exists(self.foto_rel))
        self.assertTrue(self._upload_exists(self.anhang_rel))
        self.assertTrue(self._upload_exists(self.plan_foto_rel))

        self._delete_student(self.ziel_id)

        self.assertFalse(self._upload_exists(self.foto_rel), 'Beobachtungsfoto blieb auf der Platte')
        self.assertFalse(self._upload_exists(self.anhang_rel), 'Ereignisanhang blieb auf der Platte')
        self.assertFalse(self._upload_exists(self.plan_foto_rel), 'Aufgabenfoto blieb auf der Platte')

    def test_deleting_student_keeps_other_students_data(self):
        self._login_admin()
        self._delete_student(self.ziel_id)

        with self.app.app_context():
            andere = db.session.get(Schueler, self.andere_id)
            self.assertIsNotNone(andere, 'Das zweite Kind wurde mitgeloescht')
            self.assertEqual(Foerderplan.query.filter_by(schueler_id=self.andere_id).count(), 1)
            self.assertEqual(Elternkontakt.query.filter_by(schueler_id=self.andere_id).count(), 1)
            self.assertEqual(
                ErziehungsEreignis.query.filter_by(student_id=self.andere_id).count(),
                1,
                'Das Ereignis des zweiten Kindes wurde mitgeloescht',
            )

    def test_student_deletion_requires_admin(self):
        with self.app.app_context():
            db.session.add(User(
                username='lehrkraft',
                password_hash=generate_password_hash('lehrpass'),
                role='teacher',
            ))
            db.session.commit()

        login_page = self.client.get('/login')
        token = CSRF_RE.search(login_page.get_data(as_text=True)).group(1)
        self.client.post('/login', data={
            'username': 'lehrkraft', 'password': 'lehrpass', '_csrf_token': token,
        })

        self.client.post(f'/admin/student/delete/{self.ziel_id}', data={'_csrf_token': token})

        with self.app.app_context():
            self.assertIsNotNone(
                db.session.get(Schueler, self.ziel_id),
                'Eine Lehrkraft konnte Schuelergrunddaten loeschen',
            )


if __name__ == '__main__':
    unittest.main()
