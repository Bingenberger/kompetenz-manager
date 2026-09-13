"""Tests fuer Klassenuebersicht und Schuelerakte der Diagnostik."""

import os
import re
import shutil
import tempfile
import unittest

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


class DiagnostikAuswertungTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='km_diagnostik_auswertung_')
        database_uri = os.environ.get('TEST_DATABASE_URL') or f"sqlite:///{self.tmpdir}/test.db"
        self.app = create_app({
            'TESTING': True, 'SECRET_KEY': 'test', 'SQLALCHEMY_DATABASE_URI': database_uri,
            'UPLOAD_FOLDER': os.path.join(self.tmpdir, 'uploads'),
            'PROTECTED_UPLOAD_FOLDER': os.path.join(self.tmpdir, 'protected_uploads'),
        })
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            klara = User(username='klara', password_hash=generate_password_hash('pass'), role='teacher')
            fremd = User(username='fremd', password_hash=generate_password_hash('pass'), role='teacher')
            db.session.add_all([klara, fremd, SystemKonfiguration(schuljahr='2025/2026')])
            db.session.flush()
            db.session.add(UserKlassenzuordnung(user_id=klara.id, klasse='3a', rolle='klassenleitung'))
            db.session.add(UserKlassenzuordnung(user_id=fremd.id, klasse='1b', rolle='klassenleitung'))
            ensure_klasse('3a')
            lege_vorbelegung_an()
            self.kinder = {}
            for vorname in ('Anna', 'Ben'):
                kind = Schueler(vorname=vorname, nachname='Test', klasse='3a', jahrgang=3)
                db.session.add(kind)
                db.session.flush()
                self.kinder[vorname] = kind.id
            db.session.commit()

            # Anna: Lesen SLS PR 40 (Kl. 2), ELFE PR 12 (Kl. 3) -> auffaellig, schlechter
            self._ergebnis('Anna', 'SLS 1-4', '2024/2025', 'ende', Leseleistung={'rohwert': 25, 'prozentrang': 40})
            self._ergebnis('Anna', 'ELFE II', '2025/2026', 'ende', Gesamt={'rohwert': 30, 't_wert': 38})
            # Ben: Rechtschreiben HSP 3 PR 60 -> unauffaellig
            self._ergebnis('Ben', 'HSP 3', '2025/2026', 'ende', **{'Graphemtreffer': {'prozentrang': 60}, 'Wörter richtig': {'prozentrang': 55}})

    def _ergebnis(self, kind, testform_name, schuljahr, halbjahr, **werte):
        testform = DiagnostikTestform.query.filter_by(name=testform_name).one()
        ergebnis = DiagnostikErgebnis(
            schueler_id=self.kinder[kind], testform_id=testform.id,
            schuljahr=schuljahr, halbjahr=halbjahr, bemerkung=f'Notiz {testform_name}',
        )
        for kennwert in testform.kennwerte:
            if kennwert.name in werte:
                ergebnis.werte.append(DiagnostikWert(kennwert_id=kennwert.id, **werte[kennwert.name]))
        db.session.add(ergebnis)
        db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _login(self, username='klara'):
        page = self.client.get('/login')
        token = CSRF_RE.search(page.get_data(as_text=True)).group(1)
        self.client.post('/login', data={'username': username, 'password': 'pass', '_csrf_token': token})

    # ------------------------------------------------------------------
    # Klassenuebersicht
    # ------------------------------------------------------------------

    def test_overview_defaults_to_own_class_and_shows_levels(self):
        self._login()
        html = self.client.get('/diagnostik').get_data(as_text=True)
        self.assertIn('Übersicht Klasse 3a', html)
        self.assertIn('<th>Lesen</th>', html)
        self.assertIn('<th>Rechtschreiben</th>', html)
        self.assertIn('PR 12', html)                   # Anna, aus T 38 abgeleitet
        self.assertIn('title="schlechter als zuvor"', html)
        self.assertIn('PR 55', html)                   # Ben, niedrigster Leitwert
        self.assertIn('1 auffällig', html)
        self.assertIn('0 deutlich auffällig', html)

    def test_overview_lists_open_tests_from_plan(self):
        self._login()
        html = self.client.get('/diagnostik?klasse=3a').get_data(as_text=True)
        zeile_anna = html[html.index('Test, Anna'):html.index('Test, Ben')]
        # Offene Tests sind Links mit "Testform · Halbjahr</a>".
        self.assertIn('HSP 3 · Ende</a>', zeile_anna)
        self.assertNotIn('ELFE II · Ende</a>', zeile_anna)
        zeile_ben = html[html.index('Test, Ben'):]
        self.assertIn('ELFE II · Ende</a>', zeile_ben)
        self.assertNotIn('HSP 3 · Ende</a>', zeile_ben)

    def test_risk_filter_hides_unremarkable_children(self):
        self._login()
        html = self.client.get('/diagnostik?klasse=3a&risiko=1').get_data(as_text=True)
        self.assertIn('Test, Anna', html)
        self.assertNotIn('Test, Ben', html)

    def test_overview_of_other_class_is_forbidden(self):
        self._login()
        self.assertEqual(403, self.client.get('/diagnostik?klasse=1b').status_code)

    # ------------------------------------------------------------------
    # Schuelerakte
    # ------------------------------------------------------------------

    def test_student_record_shows_history_chart_and_values(self):
        self._login()
        html = self.client.get(f'/schuelerakte?schueler_id={self.kinder["Anna"]}').get_data(as_text=True)
        self.assertIn('id="akte-diagnostik-pane"', html)
        self.assertIn('<svg', html)
        self.assertIn('Gesamt (ELFE II): PR 12 (abgeleitet)', html)
        self.assertIn('Leseleistung (SLS 1-4): PR 40', html)
        self.assertIn('RW 30 · T 38', html)
        self.assertIn('Notiz ELFE II', html)
        self.assertIn('/diagnostik/ergebnis/', html)  # Loeschen fuer die Klassenleitung
        self.assertIn("window.location.hash + '-pane'", html)

    def test_archived_student_record_is_read_only(self):
        with self.app.app_context():
            db.session.get(Schueler, self.kinder['Anna']).is_active = False
            db.session.commit()
        self._login()
        html = self.client.get(f'/schuelerakte?schueler_id={self.kinder["Anna"]}').get_data(as_text=True)
        pane = html[html.index('id="akte-diagnostik-pane"'):html.index('id="akte-erziehung-pane"')]
        self.assertIn('RW 30 · T 38', pane)
        self.assertNotIn('/diagnostik/ergebnis/', pane)
        self.assertNotIn('Ergebnis eintragen', pane)

    def test_dashboard_links_to_overview(self):
        self._login()
        html = self.client.get('/').get_data(as_text=True)
        self.assertIn('/diagnostik"', html)
        self.assertIn('/diagnostik/erfassen', html)


if __name__ == '__main__':
    unittest.main()
