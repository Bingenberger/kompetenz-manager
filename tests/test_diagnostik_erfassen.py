"""Tests fuer die Eingabe von Diagnostik-Ergebnissen."""

import os
import re
import shutil
import tempfile
import unittest
from datetime import date

from werkzeug.security import generate_password_hash

from app import create_app
from diagnostik import lege_vorbelegung_an
from extensions import db
from jahrgang import ensure_klasse
from models import (
    DiagnostikErgebnis,
    DiagnostikTestform,
    DiagnostikWert,
    Schueler,
    SystemKonfiguration,
    User,
    UserKlassenzuordnung,
)

CSRF_RE = re.compile(r'name="_csrf_token"\s+value="([^"]+)"')


class DiagnostikErfassenTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='km_diagnostik_erfassen_')
        database_uri = os.environ.get('TEST_DATABASE_URL') or f"sqlite:///{self.tmpdir}/test.db"
        self.app = create_app({
            'TESTING': True, 'SECRET_KEY': 'test', 'SQLALCHEMY_DATABASE_URI': database_uri,
            'UPLOAD_FOLDER': os.path.join(self.tmpdir, 'uploads'),
            'PROTECTED_UPLOAD_FOLDER': os.path.join(self.tmpdir, 'protected_uploads'),
        })
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            lehrkraft = User(username='klara', vorname='Klara', password_hash=generate_password_hash('pass'), role='teacher')
            fremd = User(username='fremd', password_hash=generate_password_hash('pass'), role='teacher')
            db.session.add_all([lehrkraft, fremd, SystemKonfiguration(schuljahr='2025/2026')])
            db.session.flush()
            db.session.add(UserKlassenzuordnung(user_id=lehrkraft.id, klasse='3a', rolle='klassenleitung'))
            db.session.add(UserKlassenzuordnung(user_id=fremd.id, klasse='1b', rolle='klassenleitung'))
            ensure_klasse('3a')
            ensure_klasse('1b')
            self.kinder = {}
            for vorname, klasse in [('Anna', '3a'), ('Ben', '3a'), ('Cem', '3a'), ('Dina', '1b')]:
                kind = Schueler(vorname=vorname, nachname='Test', klasse=klasse)
                db.session.add(kind)
                db.session.flush()
                self.kinder[vorname] = kind.id
            lege_vorbelegung_an()
            db.session.commit()
            testform = DiagnostikTestform.query.filter_by(name='HSP 3').one()
            self.hsp3 = testform.id
            self.kw = {k.name: k.id for k in testform.kennwerte}

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _login(self, username='klara'):
        page = self.client.get('/login')
        token = CSRF_RE.search(page.get_data(as_text=True)).group(1)
        self.client.post('/login', data={'username': username, 'password': 'pass', '_csrf_token': token})

    def _raster_pfad(self, **extra):
        parameter = {'klasse': '3a', 'testform_id': self.hsp3, 'schuljahr': '2025/2026', 'halbjahr': 'ende'}
        parameter.update(extra)
        return '/diagnostik/erfassen?' + '&'.join(f'{k}={v}' for k, v in parameter.items())

    def _feld(self, kind, kennwert, art):
        return f'w_{self.kinder[kind]}_{self.kw[kennwert]}_{art}'

    def _speichern(self, werte, datum='2026-06-20', **extra):
        pfad = self._raster_pfad(**extra)
        token = CSRF_RE.search(self.client.get(pfad).get_data(as_text=True)).group(1)
        daten = {'_csrf_token': token, 'datum': datum}
        daten.update(werte)
        return self.client.post(pfad, data=daten, follow_redirects=True)

    # ------------------------------------------------------------------

    def test_selection_offers_own_classes_and_plan(self):
        self._login()
        html = self.client.get('/diagnostik/erfassen?klasse=3a&halbjahr=ende').get_data(as_text=True)
        self.assertIn('<option value="3a" selected>', html)
        self.assertNotIn('<option value="1b"', html)
        self.assertIn('HSP 3 · Ende Klasse 3', html)
        self.assertIn('ELFE II · Ende Klasse 3', html)

    def test_other_class_is_forbidden(self):
        self._login()
        self.assertEqual(403, self.client.get('/diagnostik/erfassen?klasse=1b').status_code)
        self.assertEqual(403, self.client.get(f'/diagnostik/erfassen?schueler_id={self.kinder["Dina"]}').status_code)

    def test_grid_saves_values_and_prefills_them(self):
        self._login()
        html = self.client.get(self._raster_pfad()).get_data(as_text=True)
        self.assertIn('Graphemtreffer', html)
        self.assertIn(self._feld('Anna', 'Graphemtreffer', 'prozentrang'), html)

        response = self._speichern({
            self._feld('Anna', 'Graphemtreffer', 'rohwert'): '210',
            self._feld('Anna', 'Graphemtreffer', 'prozentrang'): '45',
            self._feld('Anna', 'Wörter richtig', 't_wert'): '38',
            self._feld('Ben', 'Graphemtreffer', 'prozentrang'): '8',
            'bemerkung_%d' % self.kinder['Ben']: 'nach Krankheit',
        })
        self.assertIn('2 Ergebnis(se) gespeichert', response.get_data(as_text=True))

        with self.app.app_context():
            anna = DiagnostikErgebnis.query.filter_by(schueler_id=self.kinder['Anna']).one()
            self.assertEqual((date(2026, 6, 20), 3, 'ende', '2025/2026'), (anna.datum, anna.jahrgang, anna.halbjahr, anna.schuljahr))
            gt = anna.wert_fuer(self.kw['Graphemtreffer'])
            self.assertEqual((210, 45, None), (gt.rohwert, gt.prozentrang, gt.t_wert))
            self.assertEqual(2, len(anna.werte))
            self.assertEqual(0, DiagnostikErgebnis.query.filter_by(schueler_id=self.kinder['Cem']).count())

        html = self.client.get(self._raster_pfad()).get_data(as_text=True)
        self.assertRegex(html, rf'name="{self._feld("Anna", "Graphemtreffer", "rohwert")}" value="210"')
        self.assertIn('value="nach Krankheit"', html)
        self.assertIn('text-bg-danger" title="niedrigster PR 8">deutlich auffällig', html)  # Ben
        self.assertIn('text-bg-warning" title="niedrigster PR 12">auffällig', html)        # Anna: T 38

    def test_past_school_years_can_be_selected_with_plan_of_that_time(self):
        with self.app.app_context():
            for kind_id in self.kinder.values():
                db.session.get(Schueler, kind_id).jahrgang = 3
            db.session.commit()
        self._login()
        html = self.client.get('/diagnostik/erfassen?klasse=3a&schuljahr=2023%2F2024&halbjahr=ende').get_data(as_text=True)
        for jahr in ('2025/2026', '2024/2025', '2023/2024', '2020/2021'):
            self.assertIn(f'<option value="{jahr}"', html)
        self.assertIn('2023/2024 (Nachtrag)', html)
        # Zwei Jahre zurueck war die 3a eine erste Klasse.
        self.assertIn('HSP 1+ · Ende Klasse 1', html)
        self.assertIn('SLS 1-4 · Ende Klasse 1', html)
        self.assertNotIn('HSP 3 · Ende Klasse 3', html)

    def test_past_entry_stores_grade_of_that_time_and_requires_matching_date(self):
        with self.app.app_context():
            db.session.get(Schueler, self.kinder['Anna']).jahrgang = 3
            db.session.commit()
            hsp1 = DiagnostikTestform.query.filter_by(name='HSP 1+').one()
            hsp1_id, gt = hsp1.id, next(k.id for k in hsp1.kennwerte if k.name == 'Graphemtreffer')
        self._login()
        pfad = f'/diagnostik/erfassen?klasse=3a&testform_id={hsp1_id}&schuljahr=2023%2F2024&halbjahr=ende'
        html = self.client.get(pfad).get_data(as_text=True)
        self.assertIn('Nachtrag für das Schuljahr 2023/2024', html)
        self.assertIn('Jg. 1 damals', html)
        self.assertRegex(html, r'name="datum" id="d-datum" class="form-control\s*"\s+value=""', 'heutiges Datum beim Nachtrag vorbelegt')
        self.assertIn('min="2023-08-01" max="2024-07-31"', html)

        feld = f'w_{self.kinder["Anna"]}_{gt}_prozentrang'
        token = CSRF_RE.search(html).group(1)
        antwort = self.client.post(pfad, data={'_csrf_token': token, 'datum': '', feld: '40'}, follow_redirects=True)
        self.assertIn('Bitte das Datum der Durchführung angeben', antwort.get_data(as_text=True))
        antwort = self.client.post(pfad, data={'_csrf_token': token, 'datum': '2025-06-20', feld: '40'}, follow_redirects=True)
        self.assertIn('liegt nicht im Schuljahr 2023/2024', antwort.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(0, DiagnostikErgebnis.query.count())

        self.client.post(pfad, data={'_csrf_token': token, 'datum': '2024-06-20', feld: '40'})
        with self.app.app_context():
            ergebnis = DiagnostikErgebnis.query.one()
            self.assertEqual(('2023/2024', 1, date(2024, 6, 20)), (ergebnis.schuljahr, ergebnis.jahrgang, ergebnis.datum))

    def test_row_date_overrides_common_date(self):
        self._login()
        self._speichern({
            self._feld('Anna', 'Graphemtreffer', 'prozentrang'): '45',
            self._feld('Ben', 'Graphemtreffer', 'prozentrang'): '50',
            f'datum_{self.kinder["Ben"]}': '2026-06-25',
        })
        with self.app.app_context():
            daten = {e.schueler_id: e.datum for e in DiagnostikErgebnis.query.all()}
        self.assertEqual(date(2026, 6, 20), daten[self.kinder['Anna']])
        self.assertEqual(date(2026, 6, 25), daten[self.kinder['Ben']])
        html = self.client.get(self._raster_pfad()).get_data(as_text=True)
        self.assertIn(f'name="datum_{self.kinder["Ben"]}" value="2026-06-25"', html)
        self.assertIn(f'name="datum_{self.kinder["Anna"]}" value=""', html)

    def test_out_of_range_saves_nothing(self):
        self._login()
        response = self._speichern({
            self._feld('Anna', 'Graphemtreffer', 'prozentrang'): '45',
            self._feld('Ben', 'Graphemtreffer', 'prozentrang'): '140',
        })
        html = response.get_data(as_text=True)
        self.assertIn('Es wurde nichts gespeichert', html)
        self.assertIn('is-invalid', html)
        self.assertIn('value="45"', html, 'Eingaben gehen verloren')
        with self.app.app_context():
            self.assertEqual(0, DiagnostikErgebnis.query.count())

    def test_clearing_a_row_removes_the_result_and_single_values(self):
        self._login()
        self._speichern({
            self._feld('Anna', 'Graphemtreffer', 'prozentrang'): '45',
            self._feld('Anna', 'Wörter richtig', 'prozentrang'): '30',
            self._feld('Ben', 'Graphemtreffer', 'prozentrang'): '50',
        })
        response = self._speichern({
            self._feld('Anna', 'Graphemtreffer', 'prozentrang'): '47',
        })
        self.assertIn('1 geleert und entfernt', response.get_data(as_text=True))
        with self.app.app_context():
            anna = DiagnostikErgebnis.query.filter_by(schueler_id=self.kinder['Anna']).one()
            self.assertEqual([(self.kw['Graphemtreffer'], 47)], [(w.kennwert_id, w.prozentrang) for w in anna.werte])
            self.assertEqual(0, DiagnostikErgebnis.query.filter_by(schueler_id=self.kinder['Ben']).count())
            self.assertEqual(1, DiagnostikWert.query.count())

    def test_single_child_mode_touches_only_that_child(self):
        self._login()
        self._speichern({self._feld('Ben', 'Graphemtreffer', 'prozentrang'): '50'})
        response = self._speichern(
            {self._feld('Anna', 'Graphemtreffer', 'prozentrang'): '60'},
            klasse='', schueler_id=self.kinder['Anna'],
        )
        self.assertIn('1 Ergebnis(se) gespeichert', response.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(2, DiagnostikErgebnis.query.count(), 'Ergebnis von Ben entfernt')

    def test_result_can_be_deleted_only_with_access(self):
        self._login()
        self._speichern({self._feld('Anna', 'Graphemtreffer', 'prozentrang'): '50'})
        with self.app.app_context():
            ergebnis_id = DiagnostikErgebnis.query.one().id

        self.client.post('/logout', data={'_csrf_token': CSRF_RE.search(self.client.get('/').get_data(as_text=True)).group(1)})
        self._login('fremd')
        token = CSRF_RE.search(self.client.get('/').get_data(as_text=True)).group(1)
        self.assertEqual(403, self.client.post(f'/diagnostik/ergebnis/{ergebnis_id}/loeschen', data={'_csrf_token': token}).status_code)

        self.client.post('/logout', data={'_csrf_token': token})
        self._login()
        token = CSRF_RE.search(self.client.get('/').get_data(as_text=True)).group(1)
        self.client.post(f'/diagnostik/ergebnis/{ergebnis_id}/loeschen', data={'_csrf_token': token})
        with self.app.app_context():
            self.assertEqual(0, DiagnostikErgebnis.query.count())


if __name__ == '__main__':
    unittest.main()
