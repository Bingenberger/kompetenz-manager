"""Rollen Förderpädagogik und Schulleitung, Schulübersicht der Diagnostik."""

import os
import re
import shutil
import tempfile
import unittest
from datetime import date

from werkzeug.security import generate_password_hash

from app import create_app
from diagnostik import lege_vorbelegung_an, risikogrenzen, speichere_ergebnis
from extensions import db
from klassenzugriff import darf_kind_sehen, zugaengliche_klassen
from models import (
    DiagnostikTestform,
    DiagnostikVerfahren,
    Elternkontakt,
    ErziehungsEreignis,
    ErziehungsEreignisKategorie,
    ErziehungsEreignisVorlage,
    ErziehungsOrt,
    Foerderplan,
    Schueler,
    SystemKonfiguration,
    User,
    UserKlassenzuordnung,
)
from schuluebersicht import erfassungsstand, kennwert_auswahl, klassendurchschnitt

CSRF_RE = re.compile(r'name="_csrf_token"\s+value="([^"]+)"')


class RollenTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='km_rollen_')
        database_uri = os.environ.get('TEST_DATABASE_URL') or f"sqlite:///{self.tmpdir}/test.db"
        self.app = create_app({
            'TESTING': True, 'SECRET_KEY': 'test', 'SQLALCHEMY_DATABASE_URI': database_uri,
            'UPLOAD_FOLDER': os.path.join(self.tmpdir, 'uploads'),
            'PROTECTED_UPLOAD_FOLDER': os.path.join(self.tmpdir, 'protected_uploads'),
        })
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            nutzer = {}
            for name, rolle in (('lehrer', 'teacher'), ('foerder', 'foerderpaedagogik'),
                                ('leitung', 'schulleitung'), ('admin', 'admin'), ('klara', 'teacher')):
                nutzer[name] = User(username=name, password_hash=generate_password_hash('pass'), role=rolle)
            db.session.add_all(list(nutzer.values()) + [SystemKonfiguration(schuljahr='2025/2026')])
            db.session.flush()
            db.session.add(UserKlassenzuordnung(user_id=nutzer['klara'].id, klasse='3a', rolle='klassenleitung'))
            lege_vorbelegung_an()
            kinder = {}
            for vorname, klasse, jahrgang in (('Anna', '3a', 3), ('Ben', '3a', 3), ('Cem', '3b', 3), ('Dila', '1a', 1)):
                kinder[vorname] = Schueler(vorname=vorname, nachname='Test', klasse=klasse, jahrgang=jahrgang)
            db.session.add_all(kinder.values())
            db.session.flush()

            def tf(name):
                return DiagnostikTestform.query.filter_by(name=name).one()

            def ids(testform):
                return {k.name: k.id for k in testform.kennwerte}

            hsp2, hsp3, sls = tf('HSP 2'), tf('HSP 3'), tf('SLS 1-4')
            # 3a: HSP 2 Ende im Vorjahr, HSP 3 Mitte jetzt nur fuer Anna
            for vorname, gt_vorjahr in (('Anna', 40), ('Ben', 60)):
                speichere_ergebnis(kinder[vorname], hsp2, '2024/2025', 'ende',
                                   {(ids(hsp2)['Graphemtreffer'], 'prozentrang'): gt_vorjahr}, None, datum=date(2025, 6, 10))
            speichere_ergebnis(kinder['Anna'], hsp3, '2025/2026', 'mitte',
                               {(ids(hsp3)['Graphemtreffer'], 'prozentrang'): 12}, None, datum=date(2026, 2, 10))
            # 3b: HSP 3 Mitte vollstaendig
            speichere_ergebnis(kinder['Cem'], hsp3, '2025/2026', 'mitte',
                               {(ids(hsp3)['Graphemtreffer'], 'prozentrang'): 70}, None, datum=date(2026, 2, 12))
            # 3a: SLS zusaetzlich (nicht im Plan fuer Jahrgang 3)
            speichere_ergebnis(kinder['Ben'], sls, '2025/2026', 'mitte',
                               {(ids(sls)['Leseleistung'], 'lesequotient'): 84}, None, datum=date(2026, 2, 11))
            # 1a: SLS im Vorjahr als Lesequotient
            speichere_ergebnis(kinder['Dila'], sls, '2024/2025', 'ende',
                               {(ids(sls)['Leseleistung'], 'lesequotient'): 95}, None, datum=date(2025, 6, 12))

            kat = ErziehungsEreignisKategorie(name='Hinweis', sort_order=1, is_active=True)
            ort = ErziehungsOrt(name='Hof', sort_order=1, is_active=True)
            db.session.add_all([kat, ort])
            db.session.flush()
            vorlage = ErziehungsEreignisVorlage(category_id=kat.id, name='Streit', sort_order=1, is_active=True)
            db.session.add(vorlage)
            db.session.flush()
            ereignis = ErziehungsEreignis(student_id=kinder['Anna'].id, event_template_id=vorlage.id, ort_id=ort.id,
                                          beschreibung='Vorfall', status='offen', created_by_user_id=nutzer['klara'].id)
            kontakt = Elternkontakt(schueler_id=kinder['Anna'].id, user_id=nutzer['klara'].id, eintrag_typ='protokoll',
                                    kontaktform='Telefonat', betreff='Protokoll Anna', mitteilung='x')
            plan = Foerderplan(schueler_id=kinder['Anna'].id, titel='Lesen üben', status='aktiv')
            db.session.add_all([ereignis, kontakt, plan])
            db.session.commit()
            self.ids = {n: k.id for n, k in kinder.items()}
            self.ereignis_id, self.kontakt_id, self.plan_id = ereignis.id, kontakt.id, plan.id
            self.hsp_id = DiagnostikVerfahren.query.filter_by(name='HSP').one().id
            self.sls_id = DiagnostikVerfahren.query.filter_by(name='SLS 1-4').one().id

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

    # ------------------------------------------------------------------ Rollen

    def test_role_properties(self):
        with self.app.app_context():
            erwartet = {'lehrer': (False, False, False), 'foerder': (True, False, False),
                        'leitung': (True, True, False), 'admin': (True, True, True)}
            for name, (alle, leitung, admin) in erwartet.items():
                user = self._user(name)
                self.assertEqual((alle, leitung, admin), (user.sieht_alle_kinder, user.ist_schulleitung, user.is_admin), name)
            anna = db.session.get(Schueler, self.ids['Anna'])
            self.assertTrue(darf_kind_sehen(self._user('foerder'), anna))
            self.assertFalse(darf_kind_sehen(self._user('lehrer'), anna))
            self.assertEqual(['1a', '3a', '3b'], zugaengliche_klassen(self._user('foerder')))

    def test_cross_class_roles_open_class_bound_pages(self):
        seiten = [
            f'/erziehung/{self.ereignis_id}',
            f'/erfassen/elternkontakte/view/{self.kontakt_id}',
            f'/foerderplan/view/{self.plan_id}',
            f'/diagnostik/erfassen?schueler_id={self.ids["Anna"]}',
        ]
        for name in ('foerder', 'leitung'):
            self._login(name)
            for pfad in seiten:
                self.assertEqual(200, self.client.get(pfad).status_code, f'{name}: {pfad}')
            html = self.client.get(f'/schuelerakte?schueler_id={self.ids["Anna"]}').get_data(as_text=True)
            self.assertIn('Protokoll Anna', html)
            self.assertNotIn('Klasse zuordnen lassen', self.client.get('/').get_data(as_text=True))
        self._login('lehrer')
        self.assertEqual(403, self.client.get(f'/erziehung/{self.ereignis_id}').status_code)
        self.assertEqual(403, self.client.get(f'/erfassen/elternkontakte/view/{self.kontakt_id}').status_code)
        self.assertIn('Klasse zuordnen lassen', self.client.get('/').get_data(as_text=True))

    def test_cross_class_roles_have_no_admin_rights(self):
        for name in ('foerder', 'leitung'):
            self._login(name)
            antwort = self.client.get('/admin/users', follow_redirects=True)
            self.assertIn('Zugriff verweigert', antwort.get_data(as_text=True))

    def test_admin_assigns_new_roles(self):
        token = self._login('admin')
        html = self.client.get('/admin/users').get_data(as_text=True)
        self.assertIn('value="foerderpaedagogik"', html)
        self.assertIn('value="schulleitung"', html)
        self.client.post('/admin/users', data={'_csrf_token': token, 'username': 'fp', 'password': 'startpass1',
                                               'role': 'foerderpaedagogik'})
        self.client.post('/admin/users', data={'_csrf_token': token, 'username': 'boese', 'password': 'startpass1',
                                               'role': 'superuser'})
        with self.app.app_context():
            self.assertEqual('foerderpaedagogik', self._user('fp').role)
            self.assertEqual('teacher', self._user('boese').role)
            lehrer_id = self._user('lehrer').id
        self.client.post(f'/admin/users/edit/{lehrer_id}', data={'_csrf_token': token, 'role': 'schulleitung'})
        with self.app.app_context():
            self.assertEqual('schulleitung', self._user('lehrer').role)
        html = self.client.get('/admin/users').get_data(as_text=True)
        self.assertIn('Förderpädagogik</span>', html)
        self.assertIn('Schulleitung</span>', html)

    # ------------------------------------------------------------------ Schulübersicht

    def test_school_overview_only_for_leadership(self):
        self._login('klara')
        self.assertNotIn('Schulübersicht', self.client.get('/diagnostik').get_data(as_text=True))
        antwort = self.client.get('/diagnostik/schule')
        self.assertEqual(302, antwort.status_code)
        self._login('foerder')
        self.assertEqual(302, self.client.get('/diagnostik/schule').status_code)
        for name in ('leitung', 'admin'):
            self._login(name)
            self.assertIn('Schulübersicht', self.client.get('/diagnostik').get_data(as_text=True))
            self.assertEqual(200, self.client.get('/diagnostik/schule').status_code)

    def test_capture_status_per_class(self):
        with self.app.app_context():
            stand = {z['klasse']: z for z in erfassungsstand('2025/2026', '2025/2026')}
            self.assertEqual(['1a', '3a', '3b'], list(stand))
            tests_3a = {(t['testform'].name, t['halbjahr']): (t['anzahl'], t['erwartet'], t['status']) for t in stand['3a']['tests']}
            self.assertEqual((1, 2, 'teilweise'), tests_3a[('HSP 3', 'mitte')])
            self.assertEqual((0, 2, 'offen'), tests_3a[('HSP 3', 'ende')])
            self.assertEqual((1, 0, 'zusaetzlich'), tests_3a[('SLS 1-4', 'mitte')])
            tests_3b = {(t['testform'].name, t['halbjahr']): t['status'] for t in stand['3b']['tests']}
            self.assertEqual('vollstaendig', tests_3b[('HSP 3', 'mitte')])
            self.assertEqual(2, stand['3a']['kinder'])

            # Früheres Schuljahr: nur gezählt, nach der Klasse zum Testzeitpunkt.
            frueher = {z['klasse']: z for z in erfassungsstand('2024/2025', '2025/2026')}
            self.assertEqual({'2a', '0a'} & set(frueher), {'2a'})
            self.assertEqual([(2, None, 'erfasst')], [(t['anzahl'], t['erwartet'], t['status']) for t in frueher['2a']['tests']])

    def test_class_average_follows_todays_class(self):
        with self.app.app_context():
            hsp = db.session.get(DiagnostikVerfahren, self.hsp_id)
            self.assertEqual('Graphemtreffer', kennwert_auswahl(hsp)[0][0])
            d = klassendurchschnitt(hsp, 'Graphemtreffer', risikogrenzen())
            self.assertEqual('pr', d['skala'])
            self.assertEqual(['Ende 2024/2025', 'Mitte 2025/2026'], [label for _, label in d['zeiten']])
            klassen = {k['klasse']: k['werte'] for k in d['klassen']}
            self.assertEqual(['3a', '3b'], list(klassen))
            vorjahr, jetzt = [s for s, _ in d['zeiten']]
            self.assertEqual({'mittel': 50.0, 'anzahl': 2, 'risiko': 0}, klassen['3a'][vorjahr])
            self.assertEqual({'mittel': 12.0, 'anzahl': 1, 'risiko': 1}, klassen['3a'][jetzt])
            self.assertEqual(['3a', '3b'], list(d['verlauf']['reihen']))

            sls = db.session.get(DiagnostikVerfahren, self.sls_id)
            d = klassendurchschnitt(sls, 'Leseleistung', risikogrenzen(), jahrgang=1)
            self.assertEqual(('lq', ['1a']), (d['skala'], [k['klasse'] for k in d['klassen']]))

    def test_school_overview_page_renders_status_and_chart(self):
        self._login('leitung')
        html = self.client.get('/diagnostik/schule').get_data(as_text=True)
        for text in ('Erfassungsstand', 'HSP 3 · Mitte', '1 / 2', 'nicht im Testplan', 'Klassendurchschnitt im Verlauf', '<svg', '50,0', '12,0'):
            self.assertIn(text, html)
        html = self.client.get(f'/diagnostik/schule?verfahren_id={self.sls_id}&jahrgang=1').get_data(as_text=True)
        self.assertIn('des Lesequotienten', html)
        self.assertIn('95,0', html)
        html = self.client.get('/diagnostik/schule?schuljahr=2024/2025').get_data(as_text=True)
        self.assertIn('Früheres Schuljahr', html)


if __name__ == '__main__':
    unittest.main()
