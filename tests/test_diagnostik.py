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
    diagramme,
    grenze_text,
    ergaenze_sls_testplan,
    lege_vorbelegung_an,
    skalen_im_verlauf,
    sls_ohne_prozentrang,
    stufen_baender,
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
        self.assertEqual(
            ['Graphemtreffer', 'Wörter richtig', 'Alphabetische Strategie', 'Orthografische Strategie',
             'Morphematische Strategie', 'Wortübergreifende Strategie'],
            [k.name for k in hsp1.kennwerte if k.risiko],
        )
        elfe = self._testform('ELFE II')
        self.assertEqual(['Gesamt'], [k.name for k in elfe.kennwerte if k.risiko])
        gesamt = [k for k in self._testform('ELFE II').kennwerte if k.leitwert]
        self.assertEqual(['Gesamt'], [k.name for k in gesamt])
        self.assertEqual(['rohwert', 'prozentrang', 't_wert'], gesamt[0].wertarten)

    def test_plan_for_grade_skips_inactive_forms(self):
        self.assertEqual({'HSP 1+', 'SLS 1-4'}, {z.testform.name for z in zeitpunkte_fuer(1)})
        self.assertEqual({'HSP 2', 'SLS 1-4'}, {z.testform.name for z in zeitpunkte_fuer(2, 'mitte')})
        self.assertEqual(['ende'], [z.halbjahr for z in zeitpunkte_fuer(1) if z.testform.name == 'SLS 1-4'])
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
        # Der angezeigte PR kommt aus den Leitwerten ...
        self.assertEqual(14, auswertung.niedrigster_prozentrang)
        self.assertTrue(auswertung.leitwerte[1].abgeleitet)
        # ... die Stufe beruecksichtigt auch die Strategien.
        self.assertEqual(STUFE_DEUTLICH, auswertung.stufe, 'Strategie-PR 2 zaehlt nicht fuer das Risiko')
        self.assertEqual(['Alphabetische Strategie'], [w.kennwert.name for w in auswertung.ausloeser])

    def test_values_without_risk_flag_do_not_count(self):
        testform = self._testform('HSP 3')
        for kennwert in testform.kennwerte:
            if kennwert.name == 'Orthografische Strategie':
                kennwert.risiko = False
        db.session.commit()
        ergebnis = self._ergebnis(
            'HSP 3', '2025/2026', 'ende',
            **{'Graphemtreffer': {'prozentrang': 60}, 'Wörter richtig': {'prozentrang': 55},
               'Orthografische Strategie': {'prozentrang': 3}},
        )
        auswertung = auswerten(ergebnis, risikogrenzen())
        self.assertIsNone(auswertung.stufe)
        self.assertEqual([], auswertung.ausloeser)

    def test_strategy_alone_can_make_a_result_count(self):
        """Nur Strategiewerte eingetragen: Stufe ja, angezeigter PR nein."""
        ergebnis = self._ergebnis('HSP 2', '2025/2026', 'ende', **{'Morphematische Strategie': {'prozentrang': 20}})
        auswertung = auswerten(ergebnis, risikogrenzen())
        self.assertEqual(STUFE_BEOBACHTEN, auswertung.stufe)
        self.assertIsNone(auswertung.niedrigster_prozentrang)
        self.assertTrue(auswertung.hat_werte)

    def test_reading_history_spans_sls_and_elfe(self):
        self._ergebnis('SLS 1-4', '2024/2025', 'ende', Leseleistung={'rohwert': 20, 'lesequotient': 82})
        self._ergebnis('ELFE II', '2025/2026', 'ende', Gesamt={'rohwert': 60, 't_wert': 48, 'prozentrang': 42})
        self._ergebnis('SLS 1-4', '2023/2024', 'ende', Leseleistung={'rohwert': 30, 'lesequotient': 95})

        bereiche = verlauf(self.kind, risikogrenzen())
        lesen = next(b for b in bereiche if b['bereich'] == 'Lesen')
        self.assertEqual(['Ende 2023/2024', 'Ende 2024/2025', 'Ende 2025/2026'], [label for _, label in lesen['zeiten']])
        self.assertEqual('PR 42', lesen['aktuell'].schwaechster_leitwert.anzeige)
        # Von LQ zu PR gibt es keinen Trend - die Skalen sind nicht vergleichbar.
        self.assertIsNone(lesen['trend'])
        self.assertEqual({'Leseleistung (SLS 1-4)', 'Gesamt (ELFE II)'}, set(lesen['reihen']))
        self.assertEqual(['lq', 'pr'], skalen_im_verlauf(lesen))

        lq = diagramm(lesen, 'lq')
        self.assertEqual(['Ende 2023/2024', 'Ende 2024/2025'], [p['label'] for p in lq['achse']])
        self.assertEqual(['Leseleistung (SLS 1-4)'], [r['name'] for r in lq['reihen']])
        punkte = lq['reihen'][0]['punkte']
        self.assertEqual([95, 82], [p['wert'] for p in punkte])
        self.assertEqual([None, None], [p['pr'] for p in punkte])
        self.assertGreater(punkte[1]['y'], punkte[0]['y'])
        self.assertEqual(lq['oben'], round(lq['y'](150), 1))
        self.assertEqual(lq['unten'], round(lq['y'](30), 1), 'Werte unter der Skala liegen am Rand')

        pr = diagramm(lesen, 'pr')
        self.assertEqual(['Ende 2025/2026'], [p['label'] for p in pr['achse']])
        self.assertEqual(pr['oben'], round(pr['y'](100), 1))
        self.assertEqual(['lq', 'pr'], [d['geometrie']['skala'] for d in diagramme(lesen, risikogrenzen())])

    def test_hsp_chart_shows_strategies_as_additional_lines(self):
        self._ergebnis('HSP 2', '2024/2025', 'ende', **{
            'Graphemtreffer': {'prozentrang': 40}, 'Wörter richtig': {'prozentrang': 35},
            'Alphabetische Strategie': {'prozentrang': 30}, 'Orthografische Strategie': {'t_wert': 45}})
        self._ergebnis('HSP 3', '2025/2026', 'ende', **{
            'Graphemtreffer': {'prozentrang': 12}, 'Wörter richtig': {'prozentrang': 38},
            'Alphabetische Strategie': {'prozentrang': 20}, 'Morphematische Strategie': {'prozentrang': 8}})

        rechtschreiben = next(b for b in verlauf(self.kind, risikogrenzen()) if b['bereich'] == 'Rechtschreiben')
        # Der angezeigte Leitwert und der Trend kommen weiter nur aus den Leitwerten.
        self.assertEqual('PR 12', rechtschreiben['aktuell'].schwaechster_leitwert.anzeige)
        self.assertEqual('schlechter', rechtschreiben['trend'])   # niedrigster Leitwert 35 -> 12
        geometrie = diagramm(rechtschreiben)
        namen = [(r['name'], r['art']) for r in geometrie['reihen']]
        self.assertEqual([
            ('Graphemtreffer (HSP)', 'leitwert'), ('Wörter richtig (HSP)', 'leitwert'),
            ('Alphabetische Strategie (HSP)', 'risiko'), ('Orthografische Strategie (HSP)', 'risiko'),
            ('Morphematische Strategie (HSP)', 'risiko'),
        ], namen)
        alphabetisch = geometrie['reihen'][2]
        self.assertEqual([30, 20], [p['wert'] for p in alphabetisch['punkte']])
        orthografisch = geometrie['reihen'][3]
        self.assertEqual([(31, True)], [(p['wert'], p['abgeleitet']) for p in orthografisch['punkte']])   # aus T 45

    def test_sls_is_rated_by_lesequotient_not_percentile(self):
        grenzen = risikogrenzen()
        self.assertEqual({'beobachten': 89, 'auffaellig': 79, 'deutlich': 69}, grenzen.lq)
        erwartet = {90: None, 89: STUFE_BEOBACHTEN, 80: STUFE_BEOBACHTEN, 79: STUFE_AUFFAELLIG, 69: STUFE_DEUTLICH}
        for jahr, (lq, stufe) in enumerate(erwartet.items(), start=2019):
            # Ein versehentlich eingetragener PR zaehlt nicht.
            ergebnis = self._ergebnis('SLS 1-4', f'{jahr}/{jahr + 1}', 'ende',
                                      Leseleistung={'rohwert': 10, 'lesequotient': lq, 'prozentrang': 50})
            auswertung = auswerten(ergebnis, grenzen)
            self.assertEqual(stufe, auswertung.stufe, f'LQ {lq}')
            self.assertEqual(f'LQ {lq}', auswertung.schwaechster_leitwert.anzeige)
            self.assertIsNone(auswertung.niedrigster_prozentrang)
            self.assertFalse(auswertung.leitwerte[0].abgeleitet)
        self.assertEqual(['rohwert', 'lesequotient'], self._testform('SLS 1-4').kennwerte[0].wertarten)
        self.assertEqual('lq', self._testform('SLS 1-4').kennwerte[0].skala)

    def test_lq_limits_follow_configuration_and_trend_stays_on_lq(self):
        config = SystemKonfiguration.query.first() or SystemKonfiguration()
        db.session.add(config)
        db.session.commit()
        config.diagnostik_lq_beobachten, config.diagnostik_lq_auffaellig, config.diagnostik_lq_deutlich = None, 84, 74
        db.session.commit()
        grenzen = risikogrenzen()
        self.assertEqual({'auffaellig': 84, 'deutlich': 74}, grenzen.lq)
        self.assertEqual('bis LQ 84', grenze_text(grenzen, 'lq'))

        self._ergebnis('SLS 1-4', '2023/2024', 'ende', Leseleistung={'lesequotient': 100})
        self._ergebnis('SLS 1-4', '2024/2025', 'ende', Leseleistung={'lesequotient': 84})
        lesen = next(b for b in verlauf(self.kind, grenzen) if b['bereich'] == 'Lesen')
        self.assertEqual('schlechter', lesen['trend'])
        self.assertEqual(STUFE_AUFFAELLIG, lesen['aktuell'].stufe)

        geometrie = diagramm(lesen, 'lq')
        baender = stufen_baender(geometrie, grenzen)
        self.assertEqual(['deutlich', 'auffaellig'], [b['stufe'] for b in baender])
        self.assertEqual(['bis LQ 74', 'bis LQ 84'], [b['text'] for b in baender])
        # "bis 84" endet an der Linie 85, das unterste Band beginnt am Rand der Skala.
        self.assertEqual(round(geometrie['y'](85), 1), baender[1]['y'])
        self.assertEqual(geometrie['unten'], round(baender[0]['y'] + baender[0]['hoehe'], 1))

    def test_sls_plan_gets_middle_of_grade_two_only_if_unchanged(self):
        testform = self._testform('SLS 1-4')
        mitte2 = next(z for z in testform.zeitpunkte if (z.jahrgang, z.halbjahr) == (2, 'mitte'))
        db.session.delete(mitte2)
        db.session.commit()
        self.assertEqual(1, ergaenze_sls_testplan())
        db.session.commit()
        self.assertEqual({(1, 'ende'), (2, 'mitte'), (2, 'ende')}, {(z.jahrgang, z.halbjahr) for z in testform.zeitpunkte})
        self.assertEqual(0, ergaenze_sls_testplan())

        # Von der Verwaltung geaendert: bleibt, wie es ist.
        for z in list(testform.zeitpunkte):
            if z.jahrgang == 2:
                db.session.delete(z)
        db.session.commit()
        self.assertEqual(0, ergaenze_sls_testplan())

    def test_migration_removes_percentile_from_sls(self):
        kennwert = self._testform('SLS 1-4').kennwerte[0]
        kennwert.prozentrang = True
        db.session.commit()
        self.assertEqual('pr', kennwert.skala)
        self.assertEqual(1, sls_ohne_prozentrang())
        db.session.commit()
        self.assertEqual(['rohwert', 'lesequotient'], kennwert.wertarten)
        self.assertEqual(0, sls_ohne_prozentrang())

    def test_results_are_deleted_with_the_child(self):
        self._ergebnis('HSP 3', '2025/2026', 'ende', Graphemtreffer={'prozentrang': 50})
        db.session.delete(self.kind)
        db.session.commit()
        self.assertEqual(0, DiagnostikErgebnis.query.count())
        self.assertEqual(0, DiagnostikWert.query.count())


if __name__ == '__main__':
    unittest.main()
