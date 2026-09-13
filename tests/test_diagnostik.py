"""Tests fuer die Auswertungslogik der Diagnostik (ohne Oberflaeche)."""

import os
import shutil
import tempfile
import unittest
from datetime import date

from app import create_app
from diagnostik import (
    STUFE_AUFFAELLIG,
    STUFE_BEOBACHTEN,
    STUFE_DEUTLICH,
    auswerten,
    diagramm,
    lege_vorbelegung_an,
    prozentrang_aus,
    risikogrenzen,
    stufe_fuer,
    trend,
    verlauf,
    zeitpunkte_fuer,
)
from extensions import db
from models import (
    DiagnostikErgebnis,
    DiagnostikTestform,
    DiagnostikVerfahren,
    DiagnostikWert,
    Schueler,
    SystemKonfiguration,
)


class Wert:
    def __init__(self, prozentrang=None, t_wert=None, lesequotient=None):
        self.prozentrang, self.t_wert, self.lesequotient = prozentrang, t_wert, lesequotient


class DiagnostikLogikTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='km_diagnostik_')
        database_uri = os.environ.get('TEST_DATABASE_URL') or f"sqlite:///{self.tmpdir}/test.db"
        self.app = create_app({
            'TESTING': True, 'SECRET_KEY': 'test', 'SQLALCHEMY_DATABASE_URI': database_uri,
            'UPLOAD_FOLDER': os.path.join(self.tmpdir, 'uploads'),
            'PROTECTED_UPLOAD_FOLDER': os.path.join(self.tmpdir, 'protected_uploads'),
        })
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        lege_vorbelegung_an()
        db.session.commit()
        self.kind = Schueler(vorname='Anna', nachname='Abt', klasse='3a', jahrgang=3)
        db.session.add(self.kind)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _testform(self, name):
        return DiagnostikTestform.query.filter_by(name=name).one()

    def _ergebnis(self, testform_name, schuljahr, halbjahr, **werte_nach_kennwert):
        testform = self._testform(testform_name)
        ergebnis = DiagnostikErgebnis(
            schueler_id=self.kind.id, testform_id=testform.id, schuljahr=schuljahr, halbjahr=halbjahr,
        )
        for kennwert in testform.kennwerte:
            if kennwert.name in werte_nach_kennwert:
                ergebnis.werte.append(DiagnostikWert(kennwert_id=kennwert.id, **werte_nach_kennwert[kennwert.name]))
        db.session.add(ergebnis)
        db.session.commit()
        return ergebnis

    # Werte und Stufen ---------------------------------------------------

    def test_percentile_is_taken_or_derived(self):
        self.assertEqual((37, False), prozentrang_aus(Wert(prozentrang=37, t_wert=40)))
        self.assertEqual((16, True), prozentrang_aus(Wert(t_wert=40)))
        self.assertEqual((50, True), prozentrang_aus(Wert(t_wert=50)))
        self.assertEqual((16, True), prozentrang_aus(Wert(lesequotient=85)))
        self.assertEqual((2, True), prozentrang_aus(Wert(lesequotient=70)))
        self.assertEqual((None, False), prozentrang_aus(Wert()))
        self.assertEqual((None, False), prozentrang_aus(None))

    def test_levels_follow_inclusive_limits(self):
        grenzen = {STUFE_BEOBACHTEN: 25, STUFE_AUFFAELLIG: 16, STUFE_DEUTLICH: 10}
        self.assertIsNone(stufe_fuer(26, grenzen))
        self.assertEqual(STUFE_BEOBACHTEN, stufe_fuer(25, grenzen))
        self.assertEqual(STUFE_AUFFAELLIG, stufe_fuer(16, grenzen))
        self.assertEqual(STUFE_DEUTLICH, stufe_fuer(10, grenzen))
        self.assertEqual(STUFE_DEUTLICH, stufe_fuer(0, grenzen))
        self.assertIsNone(stufe_fuer(None, grenzen))
        # Ausgeschaltete Stufe faellt weg.
        self.assertIsNone(stufe_fuer(20, {STUFE_AUFFAELLIG: 16, STUFE_DEUTLICH: 10}))

    def test_limits_come_from_configuration(self):
        self.assertEqual({'beobachten': 25, 'auffaellig': 16, 'deutlich': 10}, risikogrenzen())
        config = SystemKonfiguration()
        db.session.add(config)
        db.session.commit()
        self.assertEqual({'beobachten': 25, 'auffaellig': 16, 'deutlich': 10}, risikogrenzen())
        # Wie in der Verwaltung: bestehende Konfiguration aendern, Stufe leeren.
        config.diagnostik_pr_beobachten = None
        config.diagnostik_pr_auffaellig = 20
        config.diagnostik_pr_deutlich = 5
        db.session.commit()
        self.assertEqual({'auffaellig': 20, 'deutlich': 5}, risikogrenzen())

    def test_trend_needs_ten_points(self):
        self.assertEqual('besser', trend(20, 30))
        self.assertEqual('schlechter', trend(40, 30))
        self.assertEqual('gleich', trend(40, 33))
        self.assertIsNone(trend(None, 30))

    # Vorbelegung und Plan ------------------------------------------------

    def test_preset_is_created_once(self):
        self.assertEqual(0, lege_vorbelegung_an())
        self.assertEqual(['ELFE II', 'HSP', 'SLS 1-4'], sorted(v.name for v in DiagnostikVerfahren.query.all()))
        hsp1 = self._testform('HSP 1+')
        self.assertEqual(['Mitte Klasse 1', 'Ende Klasse 1'], [z.label for z in hsp1.zeitpunkte_sortiert])
        self.assertEqual(['Graphemtreffer', 'Wörter richtig'], [k.name for k in hsp1.kennwerte if k.leitwert])
        gesamt = [k for k in self._testform('ELFE II').kennwerte if k.leitwert]
        self.assertEqual(['Gesamt'], [k.name for k in gesamt])
        self.assertEqual(['rohwert', 'prozentrang', 't_wert'], gesamt[0].wertarten)

    def test_plan_for_grade_skips_inactive_forms(self):
        self.assertEqual({'HSP 1+', 'SLS 1-4'}, {z.testform.name for z in zeitpunkte_fuer(1)})
        self.assertEqual(['HSP 1+'], [z.testform.name for z in zeitpunkte_fuer(1, 'mitte')])
        self._testform('SLS 1-4').is_active = False
        db.session.commit()
        self.assertEqual({'HSP 1+'}, {z.testform.name for z in zeitpunkte_fuer(1)})
        self.assertEqual([], zeitpunkte_fuer(None))

    # Auswertung und Verlauf ----------------------------------------------

    def test_evaluation_uses_lead_values_and_worst_level(self):
        ergebnis = self._ergebnis(
            'HSP 3', '2025/2026', 'ende',
            **{'Graphemtreffer': {'rohwert': 180, 'prozentrang': 30},
               'Wörter richtig': {'rohwert': 12, 't_wert': 39},
               'Alphabetische Strategie': {'prozentrang': 2}},
        )
        auswertung = auswerten(ergebnis, risikogrenzen())
        self.assertEqual(['Graphemtreffer', 'Wörter richtig'], [l.kennwert.name for l in auswertung.leitwerte])
        self.assertEqual(14, auswertung.niedrigster_prozentrang)
        self.assertTrue(auswertung.leitwerte[1].abgeleitet)
        self.assertEqual(STUFE_AUFFAELLIG, auswertung.stufe, 'Strategiewert darf nicht zaehlen')

    def test_reading_history_spans_sls_and_elfe(self):
        self._ergebnis('SLS 1-4', '2024/2025', 'ende', Leseleistung={'rohwert': 20, 'lesequotient': 82})
        self._ergebnis('ELFE II', '2025/2026', 'ende', Gesamt={'rohwert': 60, 't_wert': 48, 'prozentrang': 42})
        self._ergebnis('SLS 1-4', '2023/2024', 'ende', Leseleistung={'rohwert': 8, 'prozentrang': 20})

        bereiche = verlauf(self.kind, risikogrenzen())
        lesen = next(b for b in bereiche if b['bereich'] == 'Lesen')
        self.assertEqual(['Ende 2023/2024', 'Ende 2024/2025', 'Ende 2025/2026'], [label for _, label in lesen['zeiten']])
        self.assertEqual(42, lesen['aktuell'].niedrigster_prozentrang)
        self.assertEqual('besser', lesen['trend'])
        self.assertEqual({'Leseleistung (SLS 1-4)', 'Gesamt (ELFE II)'}, set(lesen['reihen']))

        geometrie = diagramm(lesen)
        self.assertEqual(3, len(geometrie['achse']))
        sls = next(r for r in geometrie['reihen'] if r['name'].startswith('Leseleistung'))
        self.assertEqual(2, len(sls['punkte']))
        # LQ 82 (PR 12) liegt tiefer im Diagramm als PR 20.
        self.assertEqual([20, 12], [p['pr'] for p in sls['punkte']])
        self.assertGreater(sls['punkte'][1]['y'], sls['punkte'][0]['y'])
        self.assertEqual(geometrie['oben'], round(geometrie['y'](100), 1))

    def test_results_are_deleted_with_the_child(self):
        self._ergebnis('HSP 3', '2025/2026', 'ende', Graphemtreffer={'prozentrang': 50})
        db.session.delete(self.kind)
        db.session.commit()
        self.assertEqual(0, DiagnostikErgebnis.query.count())
        self.assertEqual(0, DiagnostikWert.query.count())


if __name__ == '__main__':
    unittest.main()
