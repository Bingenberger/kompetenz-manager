import os
import re
import shutil
import tempfile
import unittest
import zipfile
from datetime import date
from io import BytesIO
from unittest.mock import patch

from werkzeug.datastructures import MultiDict
from werkzeug.security import generate_password_hash

from app import create_app
from extensions import db
from models import Elternkontakt, Foerdergrundlage, Foerderinhalt, Foerderplan, Schueler, User, UserKlassenzuordnung


CSRF_RE = re.compile(r'name="_csrf_token"\s+value="([^"]+)"')


class CriticalFlowsTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='km_test_')
        self.db_path = os.path.join(self.tmpdir, 'test.db')
        self.upload_dir = os.path.join(self.tmpdir, 'uploads')

        self.app = create_app({
            'TESTING': True,
            'SECRET_KEY': 'test-secret',
            'SQLALCHEMY_DATABASE_URI': f"sqlite:///{self.db_path}",
            'UPLOAD_FOLDER': self.upload_dir,
        })
        self.client = self.app.test_client()

        with self.app.app_context():
            db.create_all()
            db.session.add(User(username='admin', password_hash=generate_password_hash('adminpass')))
            db.session.add(User(username='kollege', password_hash=generate_password_hash('kollegepass')))
            schueler = Schueler(vorname='Max', nachname='Test', klasse='4a')
            db.session.add(schueler)
            db.session.flush()
            db.session.add(Foerdergrundlage(schueler_id=schueler.id, besondere_staerken='Test'))
            db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _get_csrf(self, response):
        html = response.get_data(as_text=True)
        match = CSRF_RE.search(html)
        self.assertIsNotNone(match, "CSRF-Token nicht im Formular gefunden")
        return match.group(1)

    def _login(self, username, password):
        login_page = self.client.get('/login')
        self.assertEqual(login_page.status_code, 200)
        token = self._get_csrf(login_page)
        response = self.client.post(
            '/login',
            data={'username': username, 'password': password, '_csrf_token': token},
            follow_redirects=False,
        )
        return response

    def test_login_success(self):
        response = self._login('admin', 'adminpass')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers['Location'].endswith('/'))

    def test_admin_can_delete_user(self):
        login_response = self._login('admin', 'adminpass')
        self.assertEqual(login_response.status_code, 302)

        users_page = self.client.get('/admin/users')
        self.assertEqual(users_page.status_code, 200)
        token = self._get_csrf(users_page)

        with self.app.app_context():
            user = User.query.filter_by(username='kollege').first()
            self.assertIsNotNone(user)
            user_id = user.id

        delete_response = self.client.post(
            f'/admin/users/delete/{user_id}',
            data={'_csrf_token': token},
            follow_redirects=True,
        )
        self.assertEqual(delete_response.status_code, 200)

        with self.app.app_context():
            self.assertIsNone(User.query.filter_by(username='kollege').first())

    def test_admin_can_edit_user_real_name(self):
        login_response = self._login('admin', 'adminpass')
        self.assertEqual(login_response.status_code, 302)

        with self.app.app_context():
            kollege = User.query.filter_by(username='kollege').first()
            self.assertIsNotNone(kollege)
            user_id = kollege.id

        edit_page = self.client.get(f'/admin/users/edit/{user_id}')
        self.assertEqual(edit_page.status_code, 200)
        token = self._get_csrf(edit_page)

        response = self.client.post(
            f'/admin/users/edit/{user_id}',
            data={
                '_csrf_token': token,
                'vorname': 'Karin',
                'nachname': 'Beispiel',
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers['Location'].endswith('/admin/users'))

        with self.app.app_context():
            kollege = db.session.get(User, user_id)
            self.assertEqual(kollege.vorname, 'Karin')
            self.assertEqual(kollege.nachname, 'Beispiel')

    def test_non_admin_cannot_delete_student_masterdata(self):
        login_response = self._login('kollege', 'kollegepass')
        self.assertEqual(login_response.status_code, 302)

        token_page = self.client.get('/konto')
        self.assertEqual(token_page.status_code, 200)
        token = self._get_csrf(token_page)

        with self.app.app_context():
            schueler = Schueler.query.filter_by(vorname='Max', nachname='Test').first()
            self.assertIsNotNone(schueler)
            schueler_id = schueler.id

        response = self.client.post(
            f'/admin/student/delete/{schueler_id}',
            data={'_csrf_token': token},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/students', response.headers['Location'])

        with self.app.app_context():
            self.assertIsNotNone(db.session.get(Schueler, schueler_id))

    def test_non_admin_cannot_access_admin_dashboard(self):
        login_response = self._login('kollege', 'kollegepass')
        self.assertEqual(login_response.status_code, 302)

        response = self.client.get('/admin', follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers['Location'].endswith('/'))

    def test_csrf_missing_token_is_rejected(self):
        login_response = self._login('admin', 'adminpass')
        self.assertEqual(login_response.status_code, 302)

        with self.app.app_context():
            user = User.query.filter_by(username='kollege').first()
            self.assertIsNotNone(user)
            user_id = user.id

        response = self.client.post(
            f'/admin/users/delete/{user_id}',
            data={},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 400)

        with self.app.app_context():
            self.assertIsNotNone(db.session.get(User, user_id))

    def test_user_menu_can_update_real_name_and_shows_assignments(self):
        login_response = self._login('kollege', 'kollegepass')
        self.assertEqual(login_response.status_code, 302)

        with self.app.app_context():
            kollege = User.query.filter_by(username='kollege').first()
            db.session.add_all([
                UserKlassenzuordnung(user_id=kollege.id, klasse='4a', rolle='klassenleitung'),
                UserKlassenzuordnung(user_id=kollege.id, klasse='4b', rolle='fach'),
                UserKlassenzuordnung(user_id=kollege.id, klasse='3c', rolle='fach'),
            ])
            db.session.commit()

        konto_page = self.client.get('/konto')
        self.assertEqual(konto_page.status_code, 200)
        html = konto_page.get_data(as_text=True)
        self.assertIn('Klassenlehrkraft', html)
        self.assertIn('4a', html)
        self.assertIn('4b', html)
        self.assertIn('3c', html)
        token = self._get_csrf(konto_page)

        save_response = self.client.post(
            '/konto',
            data={
                '_csrf_token': token,
                'form_action': 'profile',
                'vorname': 'Klara',
                'nachname': 'Mueller',
            },
            follow_redirects=False,
        )
        self.assertEqual(save_response.status_code, 302)
        self.assertTrue(save_response.headers['Location'].endswith('/konto'))

        with self.app.app_context():
            kollege = User.query.filter_by(username='kollege').first()
            self.assertEqual(kollege.vorname, 'Klara')
            self.assertEqual(kollege.nachname, 'Mueller')

        konto_page_after = self.client.get('/konto')
        self.assertEqual(konto_page_after.status_code, 200)
        self.assertIn('Klara Mueller', konto_page_after.get_data(as_text=True))

    def test_foerderplan_create_and_evaluate(self):
        login_response = self._login('admin', 'adminpass')
        self.assertEqual(login_response.status_code, 302)

        with self.app.app_context():
            schueler = Schueler.query.filter_by(vorname='Max', nachname='Test').first()
            self.assertIsNotNone(schueler)
            schueler_id = schueler.id

        create_page = self.client.get(f'/foerderplan/neu/{schueler_id}')
        self.assertEqual(create_page.status_code, 200)
        create_token = self._get_csrf(create_page)

        create_data = MultiDict([
            ('_csrf_token', create_token),
            ('titel', 'Förderplan Test'),
            ('foerderziel', 'Lesekompetenz stärken'),
            ('ist_zustand', 'Liest noch langsam.'),
            ('soll_zustand', 'Liest flüssiger kurze Texte.'),
            ('massnahmen', 'Tägliche Lesezeit 10 Minuten.'),
        ])
        create_response = self.client.post(
            f'/foerderplan/neu/{schueler_id}',
            data=create_data,
            follow_redirects=False,
        )
        self.assertEqual(create_response.status_code, 302)

        with self.app.app_context():
            plan = Foerderplan.query.filter_by(titel='Förderplan Test').first()
            self.assertIsNotNone(plan)
            self.assertEqual(len(plan.inhalte), 1)
            plan_id = plan.id
            inhalt_id = plan.inhalte[0].id

        eval_page = self.client.get(f'/foerderplan/evaluate/{plan_id}')
        self.assertEqual(eval_page.status_code, 200)
        eval_token = self._get_csrf(eval_page)

        eval_response = self.client.post(
            f'/foerderplan/evaluate/{plan_id}',
            data={
                '_csrf_token': eval_token,
                'plan_status': 'geschlossen',
                f'status_{inhalt_id}': '1',
                f'eval_{inhalt_id}': 'Ziel erreicht.',
            },
            follow_redirects=False,
        )
        self.assertEqual(eval_response.status_code, 302)

        with self.app.app_context():
            plan = db.session.get(Foerderplan, plan_id)
            self.assertEqual(plan.status, 'geschlossen')
            self.assertIsNotNone(plan.datum_evaluation)
            self.assertEqual(plan.inhalte[0].status_id, 1)
            self.assertEqual(plan.inhalte[0].evaluation_text, 'Ziel erreicht.')

    def test_new_foerderplan_redirects_to_evaluation_if_active_plan_not_evaluated_exists(self):
        login_response = self._login('admin', 'adminpass')
        self.assertEqual(login_response.status_code, 302)

        with self.app.app_context():
            schueler = Schueler.query.filter_by(vorname='Max', nachname='Test').first()
            schueler_id = schueler.id
            plan = Foerderplan(
                schueler_id=schueler_id,
                creator_user_id=1,
                titel='Offener Plan',
                status='aktiv',
                datum_erstellung=date(2026, 2, 23),
                datum_evaluation=None,
            )
            db.session.add(plan)
            db.session.commit()
            plan_id = plan.id

        response = self.client.get(f'/foerderplan/neu/{schueler_id}', follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn(f'/foerderplan/evaluate/{plan_id}', response.headers['Location'])

    def test_foerderplan_cannot_be_closed_when_goal_statuses_are_still_open(self):
        login_response = self._login('admin', 'adminpass')
        self.assertEqual(login_response.status_code, 302)

        with self.app.app_context():
            schueler = Schueler.query.filter_by(vorname='Max', nachname='Test').first()
            plan = Foerderplan(
                schueler_id=schueler.id,
                creator_user_id=1,
                titel='Plan offen',
                status='aktiv',
                datum_erstellung=date(2026, 2, 23),
            )
            db.session.add(plan)
            db.session.flush()
            inhalt = Foerderinhalt(
                plan_id=plan.id,
                foerderziel='Lesen',
                ist_zustand='Ist',
                soll_zustand='Soll',
                massnahmen='Massnahmen',
                status_id=0,
            )
            db.session.add(inhalt)
            db.session.commit()
            plan_id = plan.id
            inhalt_id = inhalt.id

        eval_page = self.client.get(f'/foerderplan/evaluate/{plan_id}')
        self.assertEqual(eval_page.status_code, 200)
        token = self._get_csrf(eval_page)

        response = self.client.post(
            f'/foerderplan/evaluate/{plan_id}',
            data={
                '_csrf_token': token,
                'plan_status': 'geschlossen',
                f'status_{inhalt_id}': '0',
                f'eval_{inhalt_id}': 'Noch in Arbeit.',
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('nur geschlossen werden', response.get_data(as_text=True))

        with self.app.app_context():
            plan = db.session.get(Foerderplan, plan_id)
            self.assertEqual(plan.status, 'aktiv')
            self.assertIsNone(plan.datum_evaluation)

    def test_foerderplan_odt_export_downloads_filled_document(self):
        login_response = self._login('admin', 'adminpass')
        self.assertEqual(login_response.status_code, 302)

        with self.app.app_context():
            schueler = Schueler.query.filter_by(vorname='Max', nachname='Test').first()
            plan = Foerderplan(
                schueler_id=schueler.id,
                titel='FP Export',
                status='aktiv',
                datum_erstellung=date(2026, 2, 23),
            )
            db.session.add(plan)
            db.session.flush()
            db.session.add_all([
                Foerderinhalt(
                    plan_id=plan.id,
                    foerderziel='Lesefluessigkeit',
                    ist_zustand='Liest langsam.',
                    soll_zustand='Liest kurze Texte fluessig.',
                    massnahmen='Taegliche Lesezeit 10 Minuten.',
                ),
                Foerderinhalt(
                    plan_id=plan.id,
                    foerderziel='Schreibsicherheit',
                    ist_zustand='Viele Auslassungen.',
                    soll_zustand='Einfachere Woerter sicher schreiben.',
                    massnahmen='Wortliste und Abschreibtraining.',
                ),
            ])
            db.session.commit()
            plan_id = plan.id

        response = self.client.get(f'/foerderplan/export/odt/{plan_id}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'application/vnd.oasis.opendocument.text')
        self.assertIn('.odt', response.headers.get('Content-Disposition', ''))

        with tempfile.NamedTemporaryFile(suffix='.odt') as tmp:
            tmp.write(response.data)
            tmp.flush()
            with zipfile.ZipFile(tmp.name, 'r') as zf:
                content_xml = zf.read('content.xml').decode('utf-8')

        self.assertIn('Max Test', content_xml)
        self.assertIn('Lesefluessigkeit', content_xml)
        self.assertIn('Schreibsicherheit', content_xml)
        self.assertNotIn('{{#FOERDERINHALTE}}', content_xml)
        self.assertNotIn('{{/FOERDERINHALTE}}', content_xml)

    def test_foerderplan_pdf_export_downloads_pdf(self):
        login_response = self._login('admin', 'adminpass')
        self.assertEqual(login_response.status_code, 302)

        with self.app.app_context():
            schueler = Schueler.query.filter_by(vorname='Max', nachname='Test').first()
            plan = Foerderplan(
                schueler_id=schueler.id,
                titel='FP Export PDF',
                status='aktiv',
                datum_erstellung=date(2026, 2, 23),
            )
            db.session.add(plan)
            db.session.flush()
            db.session.add(Foerderinhalt(
                plan_id=plan.id,
                foerderziel='Lesen',
                ist_zustand='Ist',
                soll_zustand='Soll',
                massnahmen='Massnahmen',
            ))
            db.session.commit()
            plan_id = plan.id

        with patch('routes.foerderplan_routes.convert_odt_bytes_to_pdf', return_value=BytesIO(b'%PDF-1.7\\n%mock\\n')):
            response = self.client.get(f'/foerderplan/export/pdf/{plan_id}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'application/pdf')
        self.assertIn('.pdf', response.headers.get('Content-Disposition', ''))
        self.assertTrue(response.data.startswith(b'%PDF'))

    def test_foerderplan_list_shows_own_or_classlead_plans_and_hides_evaluate_for_closed(self):
        with self.app.app_context():
            admin = User.query.filter_by(username='admin').first()
            kollege = User.query.filter_by(username='kollege').first()
            s1 = Schueler.query.filter_by(vorname='Max', nachname='Test').first()
            s2 = Schueler(vorname='Mia', nachname='Klasse4a', klasse='4a')
            s3 = Schueler(vorname='Noah', nachname='Klasse4b', klasse='4b')
            db.session.add_all([s2, s3])
            db.session.flush()

            db.session.add(UserKlassenzuordnung(user_id=kollege.id, klasse='4a', rolle='klassenleitung'))

            plan_visible = Foerderplan(
                schueler_id=s2.id,
                creator_user_id=kollege.id,
                titel='Mein Plan 4a',
                datum_erstellung=date(2026, 2, 23),
                status='geschlossen',
            )
            plan_own_other_class = Foerderplan(
                schueler_id=s3.id,
                creator_user_id=kollege.id,
                titel='Mein Plan 4b',
                datum_erstellung=date(2026, 2, 23),
                status='aktiv',
            )
            plan_wrong_creator = Foerderplan(
                schueler_id=s1.id,
                creator_user_id=admin.id,
                titel='Admin Plan',
                datum_erstellung=date(2026, 2, 23),
                status='aktiv',
            )
            db.session.add_all([plan_visible, plan_own_other_class, plan_wrong_creator])
            db.session.commit()

        login_response = self._login('kollege', 'kollegepass')
        self.assertEqual(login_response.status_code, 302)

        response = self.client.get('/foerderplan/list')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)

        self.assertIn('Mein Plan 4a', html)
        self.assertIn('Mein Plan 4b', html)   # eigener Plan außerhalb der Klassenleitungsklasse
        self.assertIn('Admin Plan', html)     # Klassenleitungsklasse (Max Test ist 4a)

        row_start = html.find('Mein Plan 4a')
        self.assertNotEqual(row_start, -1)
        row_end = html.find('</tr>', row_start)
        self.assertNotIn('Evaluieren', html[row_start:row_end])

    def test_admin_can_delete_foerderplan(self):
        login_response = self._login('admin', 'adminpass')
        self.assertEqual(login_response.status_code, 302)

        with self.app.app_context():
            schueler = Schueler.query.filter_by(vorname='Max', nachname='Test').first()
            plan = Foerderplan(
                schueler_id=schueler.id,
                creator_user_id=1,
                titel='Loeschbarer Plan',
                status='aktiv',
                datum_erstellung=date(2026, 2, 23),
            )
            db.session.add(plan)
            db.session.commit()
            plan_id = plan.id

        view_page = self.client.get(f'/foerderplan/view/{plan_id}')
        self.assertEqual(view_page.status_code, 200)
        token = self._get_csrf(view_page)

        resp = self.client.post(
            f'/foerderplan/delete/{plan_id}',
            data={'_csrf_token': token},
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/foerderplan/list', resp.headers['Location'])

        with self.app.app_context():
            self.assertIsNone(db.session.get(Foerderplan, plan_id))

    def test_elternkontakte_notiz_and_protokoll_can_be_created(self):
        login_response = self._login('admin', 'adminpass')
        self.assertEqual(login_response.status_code, 302)

        with self.app.app_context():
            schueler = Schueler.query.filter_by(vorname='Max', nachname='Test').first()
            self.assertIsNotNone(schueler)
            schueler_id = schueler.id

        notiz_page = self.client.get(f'/erfassen/elternkontakte/notiz?schueler_id={schueler_id}')
        self.assertEqual(notiz_page.status_code, 200)
        notiz_token = self._get_csrf(notiz_page)

        notiz_response = self.client.post(
            '/erfassen/elternkontakte/notiz',
            data={
                '_csrf_token': notiz_token,
                'schueler_id': str(schueler_id),
                'datum': '2026-02-23',
                'kontaktform': 'Telefonat',
                'betreff': 'Hausaufgaben',
                'mitteilung': 'Rueckmeldung zu Hausaufgaben und Lernstand gegeben.',
            },
            follow_redirects=False,
        )
        self.assertEqual(notiz_response.status_code, 302)
        self.assertIn('/erfassen/elternkontakte', notiz_response.headers['Location'])

        protokoll_page = self.client.get(f'/erfassen/elternkontakte/protokoll?schueler_id={schueler_id}')
        self.assertEqual(protokoll_page.status_code, 200)
        protokoll_token = self._get_csrf(protokoll_page)

        protokoll_response = self.client.post(
            '/erfassen/elternkontakte/protokoll',
            data={
                '_csrf_token': protokoll_token,
                'schueler_id': str(schueler_id),
                'datum': '2026-02-23',
                'kontaktform': 'Elterngespräch',
                'betreff': 'Lernentwicklung',
                'teilnehmende': 'Mutter, Klassenleitung',
                'gespraechsanlass': 'Beratung zur Leseentwicklung',
                'besprochenes': 'Lesefluessigkeit, Uebungszeiten und Motivation besprochen.',
                'vereinbarungen_schule': 'Woechentliche Rueckmeldung im Leseheft.',
                'vereinbarungen_eltern': 'Taegliche Lesezeit 10 Minuten.',
                'naechste_schritte': 'Rueckblick in vier Wochen.',
                'naechster_termin': '2026-03-23',
                'kurzfassung': 'Gemeinsame Vereinbarungen zur Leseförderung getroffen.',
            },
            follow_redirects=False,
        )
        self.assertEqual(protokoll_response.status_code, 302)
        self.assertIn('/erfassen/elternkontakte', protokoll_response.headers['Location'])

        with self.app.app_context():
            eintraege = Elternkontakt.query.order_by(Elternkontakt.id.asc()).all()
            self.assertEqual(len(eintraege), 2)
            self.assertEqual(eintraege[0].eintrag_typ, 'notiz')
            self.assertEqual(eintraege[0].kontaktform, 'Telefonat')
            self.assertEqual(eintraege[1].eintrag_typ, 'protokoll')
            self.assertEqual(eintraege[1].teilnehmende, 'Mutter, Klassenleitung')
            self.assertEqual(eintraege[1].naechster_termin, date(2026, 3, 23))

    def test_elternkontakt_protokoll_odt_export_downloads_filled_document(self):
        login_response = self._login('admin', 'adminpass')
        self.assertEqual(login_response.status_code, 302)

        with self.app.app_context():
            schueler = Schueler.query.filter_by(vorname='Max', nachname='Test').first()
            self.assertIsNotNone(schueler)
            kontakt = Elternkontakt(
                schueler_id=schueler.id,
                user_id=1,
                eintrag_typ='protokoll',
                kontaktform='Elterngespräch',
                betreff='Lernentwicklung',
                teilnehmende='Mutter, Klassenleitung',
                gespraechsanlass='Beratung zur Leseentwicklung',
                besprochenes='Lesefluessigkeit und Uebungszeiten',
                vereinbarungen_schule='Woechentliche Rueckmeldung',
                vereinbarungen_eltern='Taegliche Lesezeit 10 Minuten',
                naechste_schritte='Rueckblick in vier Wochen',
            )
            db.session.add(kontakt)
            db.session.commit()
            kontakt_id = kontakt.id

        response = self.client.get(f'/erfassen/elternkontakte/protokoll/export/odt/{kontakt_id}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'application/vnd.oasis.opendocument.text')
        self.assertIn('.odt', response.headers.get('Content-Disposition', ''))

        odt_bytes = response.data
        with tempfile.NamedTemporaryFile(suffix='.odt') as tmp:
            tmp.write(odt_bytes)
            tmp.flush()
            with zipfile.ZipFile(tmp.name, 'r') as zf:
                mimetype = zf.read('mimetype').decode('utf-8')
                content_xml = zf.read('content.xml').decode('utf-8')

        self.assertEqual(mimetype, 'application/vnd.oasis.opendocument.text')
        self.assertIn('Max Test', content_xml)
        self.assertIn('Mutter, Klassenleitung', content_xml)
        self.assertIn('Beratung zur Leseentwicklung', content_xml)
        self.assertNotIn('{{Teilnehmende}}', content_xml)

    def test_elternkontakt_protokoll_pdf_export_downloads_pdf(self):
        login_response = self._login('admin', 'adminpass')
        self.assertEqual(login_response.status_code, 302)

        with self.app.app_context():
            schueler = Schueler.query.filter_by(vorname='Max', nachname='Test').first()
            self.assertIsNotNone(schueler)
            kontakt = Elternkontakt(
                schueler_id=schueler.id,
                user_id=1,
                eintrag_typ='protokoll',
                kontaktform='Elterngespräch',
                teilnehmende='Mutter, Klassenleitung',
                gespraechsanlass='Beratung zur Leseentwicklung',
                besprochenes='Lesefluessigkeit und Uebungszeiten',
                vereinbarungen_schule='Woechentliche Rueckmeldung',
                vereinbarungen_eltern='Taegliche Lesezeit 10 Minuten',
                naechste_schritte='Rueckblick in vier Wochen',
            )
            db.session.add(kontakt)
            db.session.commit()
            kontakt_id = kontakt.id

        with patch('routes.erfassung_routes.convert_odt_bytes_to_pdf', return_value=BytesIO(b'%PDF-1.7\\n%mock\\n')):
            response = self.client.get(f'/erfassen/elternkontakte/protokoll/export/pdf/{kontakt_id}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'application/pdf')
        self.assertIn('.pdf', response.headers.get('Content-Disposition', ''))
        self.assertTrue(response.data.startswith(b'%PDF'))

    def test_elternkontakt_view_and_owner_can_edit_protokoll(self):
        login_response = self._login('admin', 'adminpass')
        self.assertEqual(login_response.status_code, 302)

        with self.app.app_context():
            schueler = Schueler.query.filter_by(vorname='Max', nachname='Test').first()
            admin = User.query.filter_by(username='admin').first()
            kontakt = Elternkontakt(
                schueler_id=schueler.id,
                user_id=admin.id,
                eintrag_typ='protokoll',
                kontaktform='Elterngespräch',
                betreff='Lernentwicklung',
                teilnehmende='Mutter, Klassenleitung',
                gespraechsanlass='Beratung zur Leseentwicklung',
                besprochenes='Alt',
                vereinbarungen_schule='Alt Schule',
                vereinbarungen_eltern='Alt Eltern',
                naechste_schritte='Alt Schritte',
            )
            db.session.add(kontakt)
            db.session.commit()
            kontakt_id = kontakt.id

        view_response = self.client.get(f'/erfassen/elternkontakte/view/{kontakt_id}')
        self.assertEqual(view_response.status_code, 200)
        self.assertIn('Lernentwicklung', view_response.get_data(as_text=True))

        edit_page = self.client.get(f'/erfassen/elternkontakte/edit/{kontakt_id}')
        self.assertEqual(edit_page.status_code, 200)
        token = self._get_csrf(edit_page)

        edit_response = self.client.post(
            f'/erfassen/elternkontakte/edit/{kontakt_id}',
            data={
                '_csrf_token': token,
                'schueler_id': '1',
                'datum': '2026-02-23',
                'kontaktform': 'Elterngespräch',
                'betreff': 'Lernentwicklung Update',
                'teilnehmende': 'Mutter, Klassenleitung',
                'gespraechsanlass': 'Beratung zur Leseentwicklung',
                'besprochenes': 'Neu',
                'vereinbarungen_schule': 'Neu Schule',
                'vereinbarungen_eltern': 'Neu Eltern',
                'naechste_schritte': 'Neu Schritte',
                'naechster_termin': '',
                'kurzfassung': 'Kurz',
            },
            follow_redirects=False,
        )
        self.assertEqual(edit_response.status_code, 302)
        self.assertIn(f'/erfassen/elternkontakte/view/{kontakt_id}', edit_response.headers['Location'])

        with self.app.app_context():
            kontakt = db.session.get(Elternkontakt, kontakt_id)
            self.assertEqual(kontakt.betreff, 'Lernentwicklung Update')
            self.assertEqual(kontakt.besprochenes, 'Neu')

    def test_non_creator_cannot_edit_protokoll(self):
        with self.app.app_context():
            schueler = Schueler.query.filter_by(vorname='Max', nachname='Test').first()
            admin = User.query.filter_by(username='admin').first()
            kontakt = Elternkontakt(
                schueler_id=schueler.id,
                user_id=admin.id,
                eintrag_typ='protokoll',
                kontaktform='Elterngespräch',
                teilnehmende='Mutter',
                gespraechsanlass='A',
                besprochenes='B',
                vereinbarungen_schule='C',
                vereinbarungen_eltern='D',
                naechste_schritte='E',
            )
            db.session.add(kontakt)
            db.session.commit()
            kontakt_id = kontakt.id

        login_response = self._login('kollege', 'kollegepass')
        self.assertEqual(login_response.status_code, 302)

        response = self.client.get(f'/erfassen/elternkontakte/edit/{kontakt_id}', follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn(f'/erfassen/elternkontakte/view/{kontakt_id}', response.headers['Location'])

    def test_owner_can_delete_protokoll(self):
        login_response = self._login('admin', 'adminpass')
        self.assertEqual(login_response.status_code, 302)

        with self.app.app_context():
            schueler = Schueler.query.filter_by(vorname='Max', nachname='Test').first()
            admin = User.query.filter_by(username='admin').first()
            kontakt = Elternkontakt(
                schueler_id=schueler.id,
                user_id=admin.id,
                eintrag_typ='protokoll',
                kontaktform='Elterngespräch',
                teilnehmende='Mutter',
                gespraechsanlass='A',
                besprochenes='B',
                vereinbarungen_schule='C',
                vereinbarungen_eltern='D',
                naechste_schritte='E',
            )
            db.session.add(kontakt)
            db.session.commit()
            kontakt_id = kontakt.id

        page = self.client.get(f'/erfassen/elternkontakte/view/{kontakt_id}')
        self.assertEqual(page.status_code, 200)
        token = self._get_csrf(page)
        delete_response = self.client.post(
            f'/erfassen/elternkontakte/delete/{kontakt_id}',
            data={'_csrf_token': token},
            follow_redirects=False,
        )
        self.assertEqual(delete_response.status_code, 302)
        self.assertTrue(delete_response.headers['Location'].endswith('/erfassen/elternkontakte'))

        with self.app.app_context():
            self.assertIsNone(db.session.get(Elternkontakt, kontakt_id))

    def test_non_creator_cannot_delete_protokoll(self):
        with self.app.app_context():
            schueler = Schueler.query.filter_by(vorname='Max', nachname='Test').first()
            admin = User.query.filter_by(username='admin').first()
            kontakt = Elternkontakt(
                schueler_id=schueler.id,
                user_id=admin.id,
                eintrag_typ='protokoll',
                kontaktform='Elterngespräch',
                teilnehmende='Mutter',
                gespraechsanlass='A',
                besprochenes='B',
                vereinbarungen_schule='C',
                vereinbarungen_eltern='D',
                naechste_schritte='E',
            )
            db.session.add(kontakt)
            db.session.commit()
            kontakt_id = kontakt.id

        login_response = self._login('kollege', 'kollegepass')
        self.assertEqual(login_response.status_code, 302)

        view_page = self.client.get(f'/erfassen/elternkontakte/view/{kontakt_id}')
        self.assertEqual(view_page.status_code, 200)
        token_page = self.client.get('/konto')
        self.assertEqual(token_page.status_code, 200)
        token = self._get_csrf(token_page)
        response = self.client.post(
            f'/erfassen/elternkontakte/delete/{kontakt_id}',
            data={'_csrf_token': token},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(f'/erfassen/elternkontakte/view/{kontakt_id}', response.headers['Location'])

        with self.app.app_context():
            self.assertIsNotNone(db.session.get(Elternkontakt, kontakt_id))

    def test_student_dropdown_prioritizes_own_and_fachklassen(self):
        login_response = self._login('kollege', 'kollegepass')
        self.assertEqual(login_response.status_code, 302)

        with self.app.app_context():
            kollege = User.query.filter_by(username='kollege').first()
            db.session.add_all([
                Schueler(vorname='Anna', nachname='Eigene', klasse='4a'),
                Schueler(vorname='Berta', nachname='Fach', klasse='4b'),
                Schueler(vorname='Clara', nachname='Andere', klasse='1c'),
                UserKlassenzuordnung(user_id=kollege.id, klasse='4a', rolle='klassenleitung'),
                UserKlassenzuordnung(user_id=kollege.id, klasse='4b', rolle='fach'),
            ])
            db.session.commit()

        response = self.client.get('/erfassen/einzel')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)

        pos_eigene = html.find('Eigene, Anna')
        pos_fach = html.find('Fach, Berta')
        pos_andere = html.find('Andere, Clara')

        self.assertTrue(pos_eigene != -1 and pos_fach != -1 and pos_andere != -1)
        self.assertLess(pos_eigene, pos_fach)
        self.assertLess(pos_fach, pos_andere)


if __name__ == '__main__':
    unittest.main()
