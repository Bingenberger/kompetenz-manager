"""Tests fuer die Pflege des Diagnostik-Katalogs in der Verwaltung."""

import os
import re
import shutil
import tempfile
import unittest

from werkzeug.security import generate_password_hash

from app import create_app
from diagnostik import lege_vorbelegung_an, risikogrenzen
from extensions import db
from models import (
    DiagnostikErgebnis,
    DiagnostikKennwert,
    DiagnostikTestform,
    DiagnostikVerfahren,
    DiagnostikWert,
    Schueler,
    SystemKonfiguration,
    User,
)

CSRF_RE = re.compile(r'name="_csrf_token"\s+value="([^"]+)"')


class DiagnostikKatalogTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='km_diagnostik_katalog_')
        database_uri = os.environ.get('TEST_DATABASE_URL') or f"sqlite:///{self.tmpdir}/test.db"
        self.app = create_app({
            'TESTING': True, 'SECRET_KEY': 'test', 'SQLALCHEMY_DATABASE_URI': database_uri,
            'UPLOAD_FOLDER': os.path.join(self.tmpdir, 'uploads'),
            'PROTECTED_UPLOAD_FOLDER': os.path.join(self.tmpdir, 'protected_uploads'),
        })
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            db.session.add_all([
                User(username='admin', password_hash=generate_password_hash('pass'), role='admin'),
                User(username='lehrkraft', password_hash=generate_password_hash('pass'), role='teacher'),
                SystemKonfiguration(schuljahr='2025/2026'),
            ])
            lege_vorbelegung_an()
            db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _login(self, username='admin'):
        page = self.client.get('/login')
        token = CSRF_RE.search(page.get_data(as_text=True)).group(1)
        self.client.post('/login', data={'username': username, 'password': 'pass', '_csrf_token': token})

    def _token(self, pfad):
        return CSRF_RE.search(self.client.get(pfad).get_data(as_text=True)).group(1)

    def _ids(self, testform_name):
        with self.app.app_context():
            testform = DiagnostikTestform.query.filter_by(name=testform_name).one()
            return testform.verfahren_id, testform.id, {k.name: k.id for k in testform.kennwerte}

    def _formular_fuer(self, testform_id):
        """Das Formular einer bestehenden Testform, wie es der Browser abschicken wuerde."""
        with self.app.app_context():
            testform = db.session.get(DiagnostikTestform, testform_id)
            daten = {'name': testform.name, 'is_active': '1',
                     'zeitpunkte': [f'{z.jahrgang}:{z.halbjahr}' for z in testform.zeitpunkte]}
            for k in testform.kennwerte:
                p = f'kennwert_{k.id}_'
                daten[p + 'name'] = k.name
                daten[p + 'sort_order'] = str(k.sort_order)
                for art in ('rohwert', 'prozentrang', 't_wert', 'lesequotient', 'leitwert', 'risiko'):
                    if getattr(k, art):
                        daten[p + art] = '1'
            return daten

    # ------------------------------------------------------------------

    def test_catalog_requires_admin(self):
        self._login('lehrkraft')
        response = self.client.get('/admin/diagnostik', follow_redirects=True)
        self.assertIn('Zugriff verweigert', response.get_data(as_text=True))

    def test_catalog_lists_preset(self):
        self._login()
        html = self.client.get('/admin/diagnostik').get_data(as_text=True)
        for text in ('HSP 1+', 'SLS 1-4', 'ELFE II', 'Mitte Klasse 1', 'Wortverständnis', '(RW, PR, T)'):
            self.assertIn(text, html)
        self.assertIn('value="25"', html)
        self.assertIn(url_fragment := '/admin/diagnostik/grenzen', html, url_fragment)

    def test_limits_are_validated_and_saved(self):
        self._login()
        pfad = '/admin/diagnostik/grenzen'
        response = self.client.post(pfad, data={
            '_csrf_token': self._token('/admin/diagnostik'), 'beobachten': '10', 'auffaellig': '16', 'deutlich': '5',
        }, follow_redirects=True)
        self.assertIn('müssen aufsteigen', response.get_data(as_text=True))

        self.client.post(pfad, data={
            '_csrf_token': self._token('/admin/diagnostik'), 'beobachten': '', 'auffaellig': '20', 'deutlich': '8',
        })
        with self.app.app_context():
            self.assertEqual({'auffaellig': 20, 'deutlich': 8}, risikogrenzen())
            self.assertEqual({'beobachten': 89, 'auffaellig': 79, 'deutlich': 69}, risikogrenzen().lq,
                             'ohne LQ-Felder bleiben die LQ-Grenzen')

    def test_lq_limits_are_shown_validated_and_saved(self):
        self._login()
        html = self.client.get('/admin/diagnostik').get_data(as_text=True)
        self.assertIn('name="lq_beobachten"', html)
        self.assertIn('value="89"', html)
        pfad = '/admin/diagnostik/grenzen'
        basis = {'beobachten': '25', 'auffaellig': '16', 'deutlich': '10'}
        antwort = self.client.post(pfad, data={'_csrf_token': self._token('/admin/diagnostik'), **basis,
                                               'lq_beobachten': '89', 'lq_auffaellig': '200', 'lq_deutlich': '69'},
                                   follow_redirects=True)
        self.assertIn('Lesequotienten zwischen 40 und 160', antwort.get_data(as_text=True))
        antwort = self.client.post(pfad, data={'_csrf_token': self._token('/admin/diagnostik'), **basis,
                                               'lq_beobachten': '70', 'lq_auffaellig': '80', 'lq_deutlich': '69'},
                                   follow_redirects=True)
        self.assertIn('müssen aufsteigen', antwort.get_data(as_text=True))
        self.client.post(pfad, data={'_csrf_token': self._token('/admin/diagnostik'), **basis,
                                     'lq_beobachten': '84', 'lq_auffaellig': '', 'lq_deutlich': '74'})
        with self.app.app_context():
            grenzen = risikogrenzen()
            self.assertEqual({'beobachten': 84, 'deutlich': 74}, grenzen.lq)
            self.assertEqual({'beobachten': 25, 'auffaellig': 16, 'deutlich': 10}, dict(grenzen))

    def test_new_procedure_and_test_form(self):
        self._login()
        self.client.post('/admin/diagnostik/verfahren/neu', data={
            '_csrf_token': self._token('/admin/diagnostik/verfahren/neu'),
            'name': 'DEMAT', 'bereich': 'Mathematik', 'is_active': '1',
        })
        with self.app.app_context():
            verfahren_id = DiagnostikVerfahren.query.filter_by(name='DEMAT').one().id

        pfad = f'/admin/diagnostik/verfahren/{verfahren_id}/testform/neu'
        self.assertEqual(200, self.client.get(pfad).status_code)
        self.client.post(pfad, data={
            '_csrf_token': self._token(pfad), 'name': 'DEMAT 2+', 'is_active': '1',
            'zeitpunkte': ['2:ende', '3:mitte', '9:ende'],
            'neu_0_name': 'Gesamt', 'neu_0_rohwert': '1', 'neu_0_prozentrang': '1', 'neu_0_leitwert': '1',
            'neu_1_name': '',
        })
        with self.app.app_context():
            testform = DiagnostikTestform.query.filter_by(name='DEMAT 2+').one()
            self.assertEqual(verfahren_id, testform.verfahren_id)
            self.assertEqual(['Ende Klasse 2', 'Mitte Klasse 3'], [z.label for z in testform.zeitpunkte_sortiert])
            self.assertEqual([('Gesamt', True)], [(k.name, k.leitwert) for k in testform.kennwerte])

    def test_duplicate_procedure_name_is_rejected(self):
        self._login()
        response = self.client.post('/admin/diagnostik/verfahren/neu', data={
            '_csrf_token': self._token('/admin/diagnostik/verfahren/neu'), 'name': 'HSP', 'bereich': 'Rechtschreiben',
        }, follow_redirects=True)
        self.assertIn('gibt es bereits', response.get_data(as_text=True))

    def test_editing_plan_and_values_of_a_test_form(self):
        verfahren_id, testform_id, kennwerte = self._ids('HSP 2')
        self._login()
        pfad = f'/admin/diagnostik/verfahren/{verfahren_id}/testform/{testform_id}'
        daten = self._formular_fuer(testform_id)
        daten['_csrf_token'] = self._token(pfad)
        daten['zeitpunkte'] = ['2:mitte', '2:ende']
        daten[f'kennwert_{kennwerte["Alphabetische Strategie"]}_loeschen'] = '1'
        daten[f'kennwert_{kennwerte["Graphemtreffer"]}_name'] = 'Graphemtreffer (GT)'
        self.client.post(pfad, data=daten)

        with self.app.app_context():
            testform = db.session.get(DiagnostikTestform, testform_id)
            self.assertEqual(['Mitte Klasse 2', 'Ende Klasse 2'], [z.label for z in testform.zeitpunkte_sortiert])
            namen = [k.name for k in testform.kennwerte]
            self.assertNotIn('Alphabetische Strategie', namen)
            self.assertIn('Graphemtreffer (GT)', namen)

    def test_risk_flag_can_be_set_and_cleared(self):
        verfahren_id, testform_id, kennwerte = self._ids('ELFE II')
        self._login()
        pfad = f'/admin/diagnostik/verfahren/{verfahren_id}/testform/{testform_id}'
        self.assertIn('name="kennwert_%d_risiko"' % kennwerte['Textverständnis'], self.client.get(pfad).get_data(as_text=True))

        daten = self._formular_fuer(testform_id)
        daten['_csrf_token'] = self._token(pfad)
        daten[f'kennwert_{kennwerte["Textverständnis"]}_risiko'] = '1'
        daten.pop(f'kennwert_{kennwerte["Gesamt"]}_risiko')
        self.client.post(pfad, data=daten)
        with self.app.app_context():
            self.assertTrue(db.session.get(DiagnostikKennwert, kennwerte['Textverständnis']).risiko)
            gesamt = db.session.get(DiagnostikKennwert, kennwerte['Gesamt'])
            self.assertFalse(gesamt.risiko)
            self.assertTrue(gesamt.leitwert, 'Leitwert haengt am Risiko-Schalter')
        self.assertIn('>Risiko</span>', self.client.get('/admin/diagnostik').get_data(as_text=True))

    def test_value_without_any_kind_is_rejected(self):
        verfahren_id, testform_id, kennwerte = self._ids('HSP 3')
        self._login()
        pfad = f'/admin/diagnostik/verfahren/{verfahren_id}/testform/{testform_id}'
        daten = self._formular_fuer(testform_id)
        daten['_csrf_token'] = self._token(pfad)
        p = f'kennwert_{kennwerte["Graphemtreffer"]}_'
        for art in ('rohwert', 'prozentrang', 't_wert', 'lesequotient'):
            daten.pop(p + art, None)
        daten['name'] = 'Umbenannt'
        response = self.client.post(pfad, data=daten, follow_redirects=True)
        self.assertIn('mindestens eine Wertart', response.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual('HSP 3', db.session.get(DiagnostikTestform, testform_id).name, 'Teilweise gespeichert')

    def test_used_items_cannot_be_deleted(self):
        verfahren_id, testform_id, kennwerte = self._ids('HSP 3')
        with self.app.app_context():
            kind = Schueler(vorname='Anna', nachname='Abt', klasse='3a', jahrgang=3)
            db.session.add(kind)
            db.session.flush()
            ergebnis = DiagnostikErgebnis(schueler_id=kind.id, testform_id=testform_id, schuljahr='2025/2026', halbjahr='ende')
            ergebnis.werte.append(DiagnostikWert(kennwert_id=kennwerte['Graphemtreffer'], prozentrang=40))
            db.session.add(ergebnis)
            db.session.commit()
        self._login()

        pfad = f'/admin/diagnostik/verfahren/{verfahren_id}/testform/{testform_id}'
        daten = self._formular_fuer(testform_id)
        daten['_csrf_token'] = self._token(pfad)
        daten[f'kennwert_{kennwerte["Graphemtreffer"]}_loeschen'] = '1'
        response = self.client.post(pfad, data=daten, follow_redirects=True)
        self.assertIn('hat eingetragene Werte', response.get_data(as_text=True))

        response = self.client.post(f'/admin/diagnostik/testform/{testform_id}/loeschen', data={
            '_csrf_token': self._token('/admin/diagnostik'),
        }, follow_redirects=True)
        self.assertIn('kann nicht gelöscht werden', response.get_data(as_text=True))

        response = self.client.post(f'/admin/diagnostik/verfahren/{verfahren_id}/loeschen', data={
            '_csrf_token': self._token('/admin/diagnostik'),
        }, follow_redirects=True)
        self.assertIn('kann nicht gelöscht werden', response.get_data(as_text=True))
        with self.app.app_context():
            self.assertIsNotNone(db.session.get(DiagnostikKennwert, kennwerte['Graphemtreffer']))
            self.assertIsNotNone(db.session.get(DiagnostikVerfahren, verfahren_id))

    def test_unused_procedure_is_deleted_with_its_forms(self):
        verfahren_id, testform_id, _ = self._ids('SLS 1-4')
        self._login()
        self.client.post(f'/admin/diagnostik/verfahren/{verfahren_id}/loeschen', data={
            '_csrf_token': self._token('/admin/diagnostik'),
        })
        with self.app.app_context():
            self.assertIsNone(db.session.get(DiagnostikVerfahren, verfahren_id))
            self.assertIsNone(db.session.get(DiagnostikTestform, testform_id))

    def test_dashboard_links_to_catalog(self):
        self._login()
        self.assertIn('/admin/diagnostik', self.client.get('/admin').get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()
