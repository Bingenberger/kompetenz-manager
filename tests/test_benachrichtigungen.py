"""Tests fuer Benachrichtigungen: wer wovon erfaehrt, Einstellungen und Mailversand."""

import os
import re
import shutil
import tempfile
import unittest
from datetime import date, timedelta
from unittest import mock

from werkzeug.datastructures import MultiDict
from werkzeug.security import generate_password_hash

import benachrichtigungen as bn
import mail_versand
from app import create_app
from extensions import db
from models import (
    BenachrichtigungAbbestellt,
    Elternkontakt,
    ErziehungsEreignis,
    ErziehungsEreignisKategorie,
    ErziehungsEreignisVorlage,
    ErziehungsOrt,
    Foerdergrundlage,
    Foerderplan,
    Notification,
    Schueler,
    User,
    UserKlassenzuordnung,
)
from time_utils import utc_now

CSRF_RE = re.compile(r'name="_csrf_token"\s+value="([^"]+)"')


class BenachrichtigungenTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='km_benachrichtigungen_')
        database_uri = os.environ.get('TEST_DATABASE_URL') or f"sqlite:///{self.tmpdir}/test.db"
        self.outbox = []
        self.app = create_app({
            'TESTING': True,
            'SECRET_KEY': 'test-secret',
            'SQLALCHEMY_DATABASE_URI': database_uri,
            'UPLOAD_FOLDER': os.path.join(self.tmpdir, 'uploads'),
            'PROTECTED_UPLOAD_FOLDER': os.path.join(self.tmpdir, 'protected_uploads'),
            'MAIL_SERVER': 'smtp.test',
            'MAIL_FROM': 'kompass@schule.test',
            'APP_BASE_URL': 'https://kompass.schule.test',
            'MAIL_OUTBOX': self.outbox,
        })
        self.client = self.app.test_client()

        with self.app.app_context():
            db.create_all()
            self.ids = {}
            for username, vorname, rolle in [
                ('admin', 'Ada', None),
                ('klassenleitung', 'Klara', 'klassenleitung'),
                ('fach', 'Fritz', 'fach'),
                ('andere', 'Otto', None),
            ]:
                user = User(
                    username=username, vorname=vorname, nachname='Test',
                    password_hash=generate_password_hash('pass'),
                    role='admin' if username == 'admin' else 'teacher',
                )
                db.session.add(user)
                db.session.flush()
                if rolle:
                    db.session.add(UserKlassenzuordnung(user_id=user.id, klasse='3a', rolle=rolle))
                self.ids[username] = user.id

            kind = Schueler(vorname='Anna', nachname='Abt', klasse='3a', jahrgang=3)
            db.session.add(kind)
            db.session.flush()
            db.session.add(Foerdergrundlage(schueler_id=kind.id, besondere_staerken='ausdauernd'))
            kategorie = ErziehungsEreignisKategorie(name='Hinweis', sort_order=1, is_active=True)
            db.session.add(kategorie)
            db.session.flush()
            vorlage = ErziehungsEreignisVorlage(category_id=kategorie.id, name='Streit', sort_order=1, is_active=True)
            ort = ErziehungsOrt(name='Pausenhof', sort_order=1, is_active=True)
            db.session.add_all([vorlage, ort])
            db.session.commit()
            self.kind_id = kind.id
            self.vorlage_id = vorlage.id
            self.ort_id = ort.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Hilfen
    # ------------------------------------------------------------------

    def _login(self, username):
        self.client.post('/logout', data={'_csrf_token': self._token('/')})
        page = self.client.get('/login')
        token = CSRF_RE.search(page.get_data(as_text=True)).group(1)
        response = self.client.post('/login', data={
            'username': username, 'password': 'pass', '_csrf_token': token,
        })
        self.assertEqual(302, response.status_code)

    def _token(self, pfad):
        treffer = CSRF_RE.search(self.client.get(pfad).get_data(as_text=True))
        return treffer.group(1) if treffer else ''

    def _benachrichtigungen(self, username, art=None):
        with self.app.app_context():
            query = Notification.query.filter_by(user_id=self.ids[username])
            if art:
                query = query.filter_by(kind=art)
            return [(n.kind, n.title, n.message) for n in query.order_by(Notification.id).all()]

    def _ereignis_anlegen(self, zugewiesen=None, beschreibung='Streit in der Pause'):
        pfad = f'/erziehung/neu?schueler_id={self.kind_id}'
        daten = [
            ('_csrf_token', self._token(pfad)),
            ('schueler_id', str(self.kind_id)),
            ('datum', '2026-09-14'),
            ('status', 'offen'),
            ('event_template_id', str(self.vorlage_id)),
            ('ort_id', str(self.ort_id)),
            ('beschreibung', beschreibung),
        ]
        if zugewiesen:
            daten.append(('assigned_user_id', str(self.ids[zugewiesen])))
        response = self.client.post('/erziehung/neu', data=MultiDict(daten))
        self.assertEqual(302, response.status_code)
        with self.app.app_context():
            return ErziehungsEreignis.query.order_by(ErziehungsEreignis.id.desc()).first().id

    def _plan_anlegen(self, titel='Lesen'):
        pfad = f'/foerderplan/neu/{self.kind_id}'
        self.client.post(pfad, data={
            'titel': titel, 'foerderziel': ['flüssig lesen'], 'ist_zustand': ['stockt'],
            'soll_zustand': ['liest flüssig'], 'massnahmen': ['Lautlesen'],
            '_csrf_token': self._token(pfad),
        })
        with self.app.app_context():
            return Foerderplan.query.filter_by(titel=titel).one().id

    # ------------------------------------------------------------------
    # Erzieherische Arbeit
    # ------------------------------------------------------------------

    def test_event_by_other_teacher_notifies_class_teacher(self):
        self._login('fach')
        self._ereignis_anlegen()
        eintraege = self._benachrichtigungen('klassenleitung')
        self.assertEqual(1, len(eintraege))
        art, titel, text = eintraege[0]
        self.assertEqual(bn.ERZIEHUNG_NEU, art)
        self.assertIn('Anna Abt (3a)', titel)
        self.assertIn('Streit', text)
        self.assertIn('Fritz Test', text)
        self.assertEqual([], self._benachrichtigungen('fach'), 'Auslösende Lehrkraft benachrichtigt')

    def test_class_teacher_is_not_notified_about_own_event(self):
        self._login('klassenleitung')
        self._ereignis_anlegen()
        self.assertEqual([], self._benachrichtigungen('klassenleitung'))

    def test_assigned_class_teacher_gets_one_notification(self):
        self._login('fach')
        self._ereignis_anlegen(zugewiesen='klassenleitung')
        eintraege = self._benachrichtigungen('klassenleitung')
        self.assertEqual([bn.ERZIEHUNG_ZUGEWIESEN], [e[0] for e in eintraege])

    def test_event_update_notifies_class_teacher_and_creator(self):
        self._login('fach')
        event_id = self._ereignis_anlegen()
        self._login('admin')
        pfad = f'/erziehung/{event_id}/bearbeiten'
        response = self.client.post(pfad, data=MultiDict([
            ('_csrf_token', self._token(pfad)),
            ('schueler_id', str(self.kind_id)),
            ('datum', '2026-09-14'),
            ('status', 'abgeschlossen'),
            ('event_template_id', str(self.vorlage_id)),
            ('ort_id', str(self.ort_id)),
            ('beschreibung', 'Streit in der Pause'),
        ]))
        self.assertEqual(302, response.status_code)

        for username in ('klassenleitung', 'fach'):
            eintraege = self._benachrichtigungen(username, bn.ERZIEHUNG_UPDATE)
            self.assertEqual(1, len(eintraege), username)
            self.assertIn('Status', eintraege[0][2])
        self.assertEqual([], self._benachrichtigungen('admin'))

    def test_saving_an_event_unchanged_notifies_nobody(self):
        self._login('fach')
        event_id = self._ereignis_anlegen()
        with self.app.app_context():
            Notification.query.delete()
            db.session.commit()
        pfad = f'/erziehung/{event_id}/bearbeiten'
        self.client.post(pfad, data=MultiDict([
            ('_csrf_token', self._token(pfad)),
            ('schueler_id', str(self.kind_id)),
            ('datum', '2026-09-14'),
            ('status', 'offen'),
            ('event_template_id', str(self.vorlage_id)),
            ('ort_id', str(self.ort_id)),
            ('beschreibung', 'Streit in der Pause'),
        ]))
        self.assertEqual([], self._benachrichtigungen('klassenleitung'))

    def test_unsubscribed_kind_is_not_created(self):
        with self.app.app_context():
            db.session.add(BenachrichtigungAbbestellt(user_id=self.ids['klassenleitung'], art=bn.ERZIEHUNG_NEU))
            db.session.commit()
        self._login('fach')
        self._ereignis_anlegen()
        self.assertEqual([], self._benachrichtigungen('klassenleitung'))

    # ------------------------------------------------------------------
    # Foerderplaene, Elternkontakte, Verwaltung
    # ------------------------------------------------------------------

    def test_plan_by_subject_teacher_notifies_class_teacher(self):
        self._login('fach')
        self._plan_anlegen()
        eintraege = self._benachrichtigungen('klassenleitung', bn.FOERDERPLAN_NEU)
        self.assertEqual(1, len(eintraege))
        self.assertIn('„Lesen“', eintraege[0][2])

    def test_plan_evaluation_notifies_creator(self):
        self._login('fach')
        plan_id = self._plan_anlegen()
        self._login('klassenleitung')
        pfad = f'/foerderplan/evaluate/{plan_id}'
        seite = self.client.get(pfad).get_data(as_text=True)
        daten = {'_csrf_token': CSRF_RE.search(seite).group(1), 'plan_status': 'aktiv'}
        treffer = re.search(r'name="concurrency_token"\s+value="([^"]*)"', seite)
        if treffer:
            daten['concurrency_token'] = treffer.group(1)
        self.client.post(pfad, data=daten)
        eintraege = self._benachrichtigungen('fach', bn.FOERDERPLAN_UPDATE)
        self.assertEqual(1, len(eintraege))
        self.assertIn('evaluiert', eintraege[0][1])

    def test_parent_contact_notifies_class_teacher(self):
        self._login('fach')
        pfad = '/erfassen/elternkontakte/notiz'
        self.client.post(pfad, data={
            '_csrf_token': self._token(pfad), 'schueler_id': str(self.kind_id),
            'kontaktform': 'Telefonat', 'betreff': 'Hausaufgaben', 'mitteilung': 'Mutter rief an.',
        })
        eintraege = self._benachrichtigungen('klassenleitung', bn.ELTERNKONTAKT_NEU)
        self.assertEqual(1, len(eintraege))
        self.assertIn('Hausaufgaben', eintraege[0][2])

    def test_new_class_assignment_notifies_teacher(self):
        self._login('admin')
        pfad = f'/admin/users/assignments/{self.ids["andere"]}'
        seite = self.client.get(pfad)
        self.client.post(pfad, data={
            '_csrf_token': CSRF_RE.search(seite.get_data(as_text=True)).group(1),
            'klassenleitung_klasse': '', 'fachklassen': ['3a'],
        })
        eintraege = self._benachrichtigungen('andere', bn.KLASSE_ZUGEORDNET)
        self.assertEqual(1, len(eintraege))
        self.assertIn('Fachlehrkraft in 3a', eintraege[0][2])

    def test_student_moving_class_notifies_new_class_teacher(self):
        with self.app.app_context():
            kind = Schueler(vorname='Ben', nachname='Neu', klasse='2b', jahrgang=2)
            db.session.add(kind)
            db.session.commit()
            kind_id = kind.id
        self._login('admin')
        pfad = f'/admin/student/edit/{kind_id}'
        self.client.post(pfad, data={
            '_csrf_token': self._token(pfad), 'vorname': 'Ben', 'nachname': 'Neu',
            'klasse': '3a', 'jahrgang': '3', 'is_active': '1',
        })
        eintraege = self._benachrichtigungen('klassenleitung', bn.KIND_NEU_IN_KLASSE)
        self.assertEqual(1, len(eintraege))
        self.assertIn('Bisher in Klasse 2b', eintraege[0][2])

    def test_deleting_a_user_removes_their_notifications(self):
        self._login('klassenleitung')
        self._ereignis_anlegen(zugewiesen='fach')
        with self.app.app_context():
            db.session.add(BenachrichtigungAbbestellt(user_id=self.ids['fach'], art=bn.ELTERNTERMIN))
            db.session.commit()
        self._login('admin')
        self.client.post(f'/admin/users/delete/{self.ids["fach"]}', data={'_csrf_token': self._token('/admin/users')})
        with self.app.app_context():
            self.assertIsNone(db.session.get(User, self.ids['fach']))
            self.assertEqual(0, Notification.query.filter_by(user_id=self.ids['fach']).count())

    # ------------------------------------------------------------------
    # Kontoeinstellungen
    # ------------------------------------------------------------------

    def test_account_settings_save_email_rhythm_and_kinds(self):
        self._login('klassenleitung')
        seite = self.client.get('/konto').get_data(as_text=True)
        self.assertIn('Glocke und E-Mail', seite)
        arten = [art for art in bn.ARTEN if art != bn.ELTERNKONTAKT_NEU]
        self.client.post('/konto', data={
            '_csrf_token': CSRF_RE.search(seite).group(1), 'form_action': 'benachrichtigungen',
            'email': 'klara@schule.test', 'mail_takt': 'sofort', 'arten': arten,
        })
        with self.app.app_context():
            user = db.session.get(User, self.ids['klassenleitung'])
            self.assertEqual('klara@schule.test', user.email)
            self.assertEqual('sofort', user.mail_takt)
            self.assertEqual({bn.ELTERNKONTAKT_NEU}, bn.abbestellte_arten(user.id))

    def test_account_settings_reject_invalid_email(self):
        self._login('klassenleitung')
        response = self.client.post('/konto', data={
            '_csrf_token': self._token('/konto'), 'form_action': 'benachrichtigungen',
            'email': 'keine-adresse', 'mail_takt': 'sofort', 'arten': list(bn.ARTEN),
        }, follow_redirects=True)
        self.assertIn('keine gültige E-Mail-Adresse', response.get_data(as_text=True))
        with self.app.app_context():
            self.assertIsNone(db.session.get(User, self.ids['klassenleitung']).email)

    # ------------------------------------------------------------------
    # Mailversand
    # ------------------------------------------------------------------

    def _setze_mail(self, username, email, takt):
        with self.app.app_context():
            user = db.session.get(User, self.ids[username])
            user.email = email
            user.mail_takt = takt
            db.session.commit()

    def _notiz(self, username, titel, **felder):
        with self.app.app_context():
            n = Notification(user_id=self.ids[username], title=titel, message='Text', target_url='/erziehung/1', **felder)
            db.session.add(n)
            db.session.commit()
            return n.id

    def _versende(self, takt):
        with self.app.test_request_context('/'):
            return mail_versand.versende(takt)

    def test_immediate_run_sends_one_mail_per_teacher(self):
        self._setze_mail('klassenleitung', 'klara@schule.test', 'sofort')
        self._setze_mail('fach', 'fritz@schule.test', 'taeglich')
        erste = self._notiz('klassenleitung', 'Neues Ereignis: Anna Abt (3a)')
        self._notiz('klassenleitung', 'Neuer Förderplan: Anna Abt (3a)')
        self._notiz('fach', 'Ereignis aktualisiert')

        ergebnis = self._versende('sofort')

        self.assertEqual(1, ergebnis['mails'])
        self.assertEqual(1, len(self.outbox))
        mail = self.outbox[0]
        self.assertEqual('klara@schule.test', mail['To'])
        self.assertEqual('KompetenzKompass: 2 neue Benachrichtigungen', mail['Subject'])
        inhalt = mail.get_content()
        self.assertIn('Anna Abt (3a)', inhalt)
        self.assertIn('https://kompass.schule.test/erziehung/1', inhalt)
        with self.app.app_context():
            self.assertIsNotNone(db.session.get(Notification, erste).mailed_at)
            offen = Notification.query.filter_by(user_id=self.ids['fach'], mailed_at=None).count()
            self.assertEqual(1, offen, 'Tägliche Lehrkraft im Sofort-Lauf bedient')

        # Kein zweiter Versand derselben Benachrichtigungen.
        self._versende('sofort')
        self.assertEqual(1, len(self.outbox))

        self._versende('taeglich')
        self.assertEqual(2, len(self.outbox))
        self.assertEqual('KompetenzKompass: Ereignis aktualisiert', self.outbox[1]['Subject'])

    def test_read_old_and_mailless_notifications_are_not_sent(self):
        self._setze_mail('klassenleitung', 'klara@schule.test', 'sofort')
        gelesen = self._notiz('klassenleitung', 'Schon gesehen', is_read=True)
        alt = self._notiz('klassenleitung', 'Uralt', created_at=utc_now() - timedelta(days=8))
        ohne_adresse = self._notiz('andere', 'Ohne Adresse')

        ergebnis = self._versende('sofort')

        self.assertEqual([], self.outbox)
        self.assertEqual(3, ergebnis['uebergangen'])
        with self.app.app_context():
            for notiz_id in (gelesen, alt, ohne_adresse):
                self.assertIsNotNone(db.session.get(Notification, notiz_id).mailed_at)

    def test_failed_delivery_stays_open_for_the_next_run(self):
        self._setze_mail('klassenleitung', 'klara@schule.test', 'sofort')
        notiz_id = self._notiz('klassenleitung', 'Wichtig')
        with mock.patch.object(mail_versand, 'sende_mail', side_effect=OSError('Server weg')), \
                self.assertLogs('mail_versand', level='ERROR'):
            ergebnis = self._versende('sofort')
        self.assertEqual(1, ergebnis['fehler'])
        with self.app.app_context():
            self.assertIsNone(db.session.get(Notification, notiz_id).mailed_at)

        self._versende('sofort')
        self.assertEqual(1, len(self.outbox))

    def test_mail_security_tolerates_env_file_quirks(self):
        from mail_versand import mail_konfiguration
        # systemd nimmt einen Kommentar hinter dem Wert mit in den Wert auf.
        k = mail_konfiguration({'MAIL_SERVER': 'smtp.test', 'MAIL_SECURITY': 'ssl        # starttls | ssl | none', 'MAIL_PORT': '465'})
        self.assertEqual(('ssl', 465, False, False), (k['sicherheit'], k['port'], k['sicherheit_unbekannt'], k['port_passt_nicht']))
        k = mail_konfiguration({'MAIL_SERVER': '"smtp.test"', 'MAIL_SECURITY': '"SSL/TLS"', 'MAIL_PORT': '"465"', 'MAIL_FROM': '"Kompass <k@test>"'})
        self.assertEqual(('smtp.test', 'ssl', 465, 'Kompass <k@test>'), (k['server'], k['sicherheit'], k['port'], k['absender']))
        k = mail_konfiguration({'MAIL_SERVER': 'smtp.test', 'MAIL_SECURITY': 'tls'})
        self.assertEqual(('starttls', 587), (k['sicherheit'], k['port']))
        k = mail_konfiguration({'MAIL_SERVER': 'smtp.test', 'MAIL_SECURITY': 'ssl', 'MAIL_PORT': '587'})
        self.assertTrue(k['port_passt_nicht'])
        k = mail_konfiguration({'MAIL_SERVER': 'smtp.test', 'MAIL_SECURITY': 'verschluesselt'})
        self.assertEqual(('starttls', True, 'verschluesselt'), (k['sicherheit'], k['sicherheit_unbekannt'], k['sicherheit_roh']))

    def test_admin_mail_page_warns_about_unknown_security_value(self):
        self.app.config['MAIL_SECURITY'] = 'ssl  # 465'
        self.app.config['MAIL_PORT'] = '587'
        self._login('admin')
        html = self.client.get('/admin/benachrichtigungen').get_data(as_text=True)
        self.assertIn('SSL/TLS', html)
        self.assertIn('gelesen als „ssl  # 465“', html)
        self.assertIn('Port 587 passt nicht dazu', html)
        self.app.config['MAIL_SECURITY'] = 'verschluesselt'
        html = self.client.get('/admin/benachrichtigungen').get_data(as_text=True)
        self.assertIn('ist kein bekannter Wert', html)

    def test_run_without_mail_server_raises(self):
        self.app.config['MAIL_SERVER'] = ''
        with self.assertRaises(mail_versand.MailNichtKonfiguriert):
            self._versende('sofort')

    # ------------------------------------------------------------------
    # Erinnerungen
    # ------------------------------------------------------------------

    def test_parent_appointment_reminder_is_sent_once_per_date(self):
        heute = date(2026, 9, 14)
        with self.app.app_context():
            kontakt = Elternkontakt(
                schueler_id=self.kind_id, user_id=self.ids['fach'], eintrag_typ='protokoll',
                kontaktform='Elterngespräch', betreff='Zeugnis', naechster_termin=heute + timedelta(days=2),
            )
            db.session.add(kontakt)
            db.session.commit()
            kontakt_id = kontakt.id

        def lauf(tag):
            with self.app.test_request_context('/'):
                anzahl = bn.erinnere_an_elterntermine(tag)
                db.session.commit()
                return anzahl

        self.assertEqual(2, lauf(heute))  # Klassenleitung und protokollierende Lehrkraft
        self.assertEqual(0, lauf(heute + timedelta(days=1)))
        self.assertIn('Elterntermin am 16.09.2026', self._benachrichtigungen('fach', bn.ELTERNTERMIN)[0][1])

        with self.app.app_context():
            db.session.get(Elternkontakt, kontakt_id).naechster_termin = heute + timedelta(days=3)
            db.session.commit()
        self.assertEqual(2, lauf(heute + timedelta(days=1)))

    def test_reminder_outside_window_is_not_sent(self):
        heute = date(2026, 9, 14)
        with self.app.app_context():
            db.session.add(Elternkontakt(
                schueler_id=self.kind_id, user_id=self.ids['fach'], eintrag_typ='protokoll',
                naechster_termin=heute + timedelta(days=10),
            ))
            db.session.commit()
        with self.app.test_request_context('/'):
            self.assertEqual(0, bn.erinnere_an_elterntermine(heute))

    # ------------------------------------------------------------------
    # Verwaltung: Status und Testmail
    # ------------------------------------------------------------------

    def test_admin_mail_page_sends_test_mail(self):
        self._setze_mail('admin', 'ada@schule.test', 'taeglich')
        self._login('admin')
        seite = self.client.get('/admin/benachrichtigungen').get_data(as_text=True)
        self.assertIn('smtp.test', seite)
        self.assertNotIn('MAIL_PASSWORD=', seite)
        response = self.client.post('/admin/benachrichtigungen', data={
            '_csrf_token': CSRF_RE.search(seite).group(1),
        }, follow_redirects=True)
        self.assertIn('Testmail an ada@schule.test verschickt', response.get_data(as_text=True))
        self.assertEqual('KompetenzKompass: Testmail', self.outbox[0]['Subject'])

    def test_new_user_can_be_created_with_email(self):
        self._login('admin')
        self.client.post('/admin/users', data={
            '_csrf_token': self._token('/admin/users'), 'username': 'neu', 'vorname': 'Nora',
            'nachname': 'Neu', 'email': 'nora@schule.test', 'password': 'startpass', 'role': 'teacher',
        })
        with self.app.app_context():
            self.assertEqual('nora@schule.test', User.query.filter_by(username='neu').one().email)
        html = self.client.get('/admin/users').get_data(as_text=True)
        self.assertIn('nora@schule.test', html)
        self.assertNotIn('Name bearbeiten', html)
        self.assertIn('>Bearbeiten</span>', html)
        # Filter: "andere" und das neue Konto haben noch keine Klasse.
        self.assertIn('Ohne Klasse <span class="badge text-bg-warning ms-1">2</span>', html)
        self.assertRegex(html, r'data-suche="[^"]*nora@schule.test[^"]*"')
        self.assertLess(html.index('Nora Neu'), html.index('Otto Test'), 'nicht nach Nachnamen sortiert')

    def test_invalid_email_on_creation_keeps_the_input(self):
        self._login('admin')
        response = self.client.post('/admin/users', data={
            '_csrf_token': self._token('/admin/users'), 'username': 'neu', 'vorname': 'Nora',
            'email': 'kaputt', 'password': 'startpass', 'role': 'admin',
        }, follow_redirects=True)
        html = response.get_data(as_text=True)
        self.assertIn('keine gültige E-Mail-Adresse', html)
        self.assertIn('value="neu"', html)
        self.assertIn('value="Nora"', html)
        self.assertRegex(html, r'<option value="admin"\s+selected>')
        with self.app.app_context():
            self.assertIsNone(User.query.filter_by(username='neu').first())

    def test_user_edit_page_shows_profile_classes_and_password(self):
        self._setze_mail('klassenleitung', 'klara@schule.test', 'sofort')
        self._login('admin')
        html = self.client.get(f'/admin/users/edit/{self.ids["klassenleitung"]}').get_data(as_text=True)
        self.assertIn('value="klara@schule.test"', html)
        self.assertIn('Sofort (nach wenigen Minuten)', html)
        self.assertIn('Klassen zuordnen', html)
        self.assertIn('>3a</span>', html)
        self.assertIn('id="passwort"', html)
        self.assertIn('class="col-lg-7"', html)

    def test_admin_mail_page_requires_admin(self):
        self._login('klassenleitung')
        response = self.client.get('/admin/benachrichtigungen', follow_redirects=True)
        self.assertIn('Zugriff verweigert', response.get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()
