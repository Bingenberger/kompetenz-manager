"""Tests fuer den Entwicklungsverlauf im Bericht.

Der Verlauf soll eine Frage beantworten, die ueber Foerderplaene entscheidet:
Ist das Kind vorangekommen? Eine falsche Richtung waere schlimmer als gar
keine Angabe - sie wuerde in ein Elterngespraech getragen.
"""

import os
import re
import shutil
import tempfile
import unittest
from datetime import date, datetime

from werkzeug.security import generate_password_hash

from app import create_app
from competency_trend import MINDESTENS, SCHWELLE, compute_trend, summarize
from extensions import db
from models import Beobachtung, Bogen, Item, Schueler, SystemKonfiguration, User

CSRF_RE = re.compile(r'name="_csrf_token"\s+value="([^"]+)"')


class Eintrag:
    """Leichtgewichtiger Ersatz fuer Beobachtung - der Verlauf braucht nur zwei Felder."""

    def __init__(self, wert, tag):
        self.wert = wert
        self.datum = datetime(2026, 9, tag) if tag else None


class CompetencyTrendTestCase(unittest.TestCase):
    # ------------------------------------------------------------------
    # Berechnung
    # ------------------------------------------------------------------

    def test_too_few_entries_yield_no_trend(self):
        self.assertIsNone(compute_trend([]))
        self.assertIsNone(compute_trend([Eintrag(3, 1)]))
        self.assertEqual(2, MINDESTENS)

    def test_entries_without_a_value_do_not_count(self):
        self.assertIsNone(compute_trend([Eintrag(None, 1), Eintrag(3, 2)]))

    def test_improvement_is_recognised(self):
        trend = compute_trend([Eintrag(1, 1), Eintrag(4, 2)])
        self.assertEqual('verbessert', trend['richtung'])
        self.assertEqual(1.0, trend['frueher'])
        self.assertEqual(4.0, trend['spaeter'])
        self.assertEqual(3.0, trend['differenz'])

    def test_decline_is_recognised(self):
        trend = compute_trend([Eintrag(4, 1), Eintrag(1, 2)])
        self.assertEqual('verschlechtert', trend['richtung'])
        self.assertEqual(-3.0, trend['differenz'])

    def test_small_changes_count_as_stable(self):
        """Unter einer halben Stufe ist es Rauschen einzelner Tage."""
        trend = compute_trend([Eintrag(3, 1), Eintrag(3, 2)])
        self.assertEqual('stabil', trend['richtung'])
        self.assertEqual(0.0, trend['differenz'])

    def test_threshold_is_inclusive(self):
        # Genau eine halbe Stufe gilt schon als Bewegung.
        trend = compute_trend([Eintrag(2, 1), Eintrag(2, 2), Eintrag(2, 3), Eintrag(3, 4)])
        self.assertEqual(0.5, trend['differenz'])
        self.assertEqual('verbessert', trend['richtung'])
        self.assertEqual(0.5, SCHWELLE)

    def test_order_of_input_does_not_matter(self):
        """Der Bericht liefert absteigend sortiert - der Verlauf sortiert selbst."""
        aufsteigend = compute_trend([Eintrag(1, 1), Eintrag(2, 2), Eintrag(4, 3), Eintrag(4, 4)])
        absteigend = compute_trend([Eintrag(4, 4), Eintrag(4, 3), Eintrag(2, 2), Eintrag(1, 1)])
        self.assertEqual(aufsteigend, absteigend)
        self.assertEqual('verbessert', aufsteigend['richtung'])

    def test_middle_entry_is_left_out_when_count_is_odd(self):
        """Sonst bekaeme eine der beiden Haelften ein Uebergewicht."""
        trend = compute_trend([Eintrag(1, 1), Eintrag(4, 2), Eintrag(3, 3)])
        self.assertEqual(1, trend['anzahl_frueher'])
        self.assertEqual(1, trend['anzahl_spaeter'])
        self.assertEqual(1.0, trend['frueher'], 'erster Eintrag')
        self.assertEqual(3.0, trend['spaeter'], 'letzter Eintrag')

    def test_halves_are_split_by_count_not_by_date(self):
        """Ein Eintrag im September, sieben im Juni - nach Datum waere der
        Vergleich eine Haelfte mit einem einzigen Wert."""
        eintraege = [Eintrag(1, 1)] + [Eintrag(4, tag) for tag in range(20, 27)]
        trend = compute_trend(eintraege)
        self.assertEqual(4, trend['anzahl_frueher'])
        self.assertEqual(4, trend['anzahl_spaeter'])

    def test_first_and_last_date_are_reported(self):
        trend = compute_trend([Eintrag(2, 5), Eintrag(3, 1), Eintrag(4, 9)])
        self.assertEqual(datetime(2026, 9, 1), trend['von'])
        self.assertEqual(datetime(2026, 9, 9), trend['bis'])

    def test_entries_without_a_date_do_not_crash_the_sort(self):
        trend = compute_trend([Eintrag(2, None), Eintrag(4, 1)])
        self.assertIsNotNone(trend)

    # ------------------------------------------------------------------
    # Zusammenfassung
    # ------------------------------------------------------------------

    def test_summary_counts_every_direction(self):
        trends = [
            compute_trend([Eintrag(1, 1), Eintrag(4, 2)]),
            compute_trend([Eintrag(4, 1), Eintrag(1, 2)]),
            compute_trend([Eintrag(3, 1), Eintrag(3, 2)]),
            None,
        ]
        gezaehlt = summarize(trends)
        self.assertEqual(1, gezaehlt['verbessert'])
        self.assertEqual(1, gezaehlt['verschlechtert'])
        self.assertEqual(1, gezaehlt['stabil'])
        self.assertEqual(1, gezaehlt['ohne'])
        self.assertEqual(3, gezaehlt['bewertet'])

    def test_summary_of_nothing_is_all_zero(self):
        gezaehlt = summarize([])
        self.assertEqual(0, gezaehlt['bewertet'])
        self.assertEqual(0, gezaehlt['ohne'])


class ReportViewTrendTestCase(unittest.TestCase):
    """Der Verlauf im Bericht, samt der Abfragen dahinter."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='km_trend_test_')
        database_uri = os.environ.get('TEST_DATABASE_URL') or f"sqlite:///{self.tmpdir}/test.db"

        self.app = create_app({
            'TESTING': True,
            'SECRET_KEY': 'test-secret',
            'SQLALCHEMY_DATABASE_URI': database_uri,
            'UPLOAD_FOLDER': os.path.join(self.tmpdir, 'uploads'),
            'PROTECTED_UPLOAD_FOLDER': os.path.join(self.tmpdir, 'protected_uploads'),
        })
        self.client = self.app.test_client()

        with self.app.app_context():
            db.create_all()
            db.session.add(SystemKonfiguration(
                schuljahr='2026/2027', schuljahr_beginn=date(2026, 8, 1),
            ))
            db.session.add(User(
                username='admin', password_hash=generate_password_hash('adminpass'),
                role='admin',
            ))
            kind = Schueler(vorname='Anna', nachname='Abt', klasse='3a')
            bogen = Bogen(titel='Sozialverhalten')
            db.session.add_all([kind, bogen])
            db.session.flush()
            self.kind_id, self.bogen_id = kind.id, bogen.id

            self.items = []
            for bereich, text in [('Konflikt', 'Löst Streit friedlich'),
                                  ('Arbeit', 'Arbeitet konzentriert'),
                                  ('Arbeit', 'Räumt auf')]:
                item = Item(bogen_id=bogen.id, bereich=bereich, text=text)
                db.session.add(item)
                self.items.append(item)
            db.session.flush()
            self.item_ids = [item.id for item in self.items]

            # Erste Kompetenz: deutlich verbessert
            for tag, wert in [(1, 1), (2, 1), (20, 4), (21, 4)]:
                db.session.add(Beobachtung(
                    schueler_id=kind.id, item_id=self.item_ids[0], wert=wert,
                    datum=datetime(2026, 9, tag),
                ))
            # Zweite Kompetenz: zurückgegangen
            for tag, wert in [(1, 4), (20, 1)]:
                db.session.add(Beobachtung(
                    schueler_id=kind.id, item_id=self.item_ids[1], wert=wert,
                    datum=datetime(2026, 9, tag),
                ))
            # Dritte Kompetenz: nur ein Eintrag, kein Verlauf möglich
            db.session.add(Beobachtung(
                schueler_id=kind.id, item_id=self.item_ids[2], wert=3,
                datum=datetime(2026, 9, 5),
            ))
            db.session.commit()

        page = self.client.get('/login')
        token = CSRF_RE.search(page.get_data(as_text=True)).group(1)
        self.client.post('/login', data={
            'username': 'admin', 'password': 'adminpass', '_csrf_token': token,
        })

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _report(self):
        return self.client.get(
            f'/report/schueler?schueler_id={self.kind_id}&bogen_id={self.bogen_id}'
        )

    def test_report_shows_directions_and_summary(self):
        html = self._report().get_data(as_text=True)
        self.assertEqual(200, self._report().status_code)
        self.assertIn('verbessert', html)
        self.assertIn('zurückgegangen', html)
        self.assertIn('1 ohne Verlauf', html)

    def test_previous_school_year_does_not_enter_the_trend(self):
        with self.app.app_context():
            # Ein sehr guter Wert aus dem Vorjahr wuerde die fruehe Haelfte heben
            # und die Verbesserung verschwinden lassen.
            db.session.add(Beobachtung(
                schueler_id=self.kind_id, item_id=self.item_ids[0], wert=4,
                datum=datetime(2025, 9, 1),
            ))
            db.session.commit()

        html = self._report().get_data(as_text=True)
        self.assertIn('verbessert', html)

    def test_report_uses_one_query_for_all_competencies(self):
        """Vorher: zwei Abfragen je Kompetenz, dazu das Schuljahr in der Schleife."""
        from sqlalchemy import event
        from extensions import db as datenbank

        gezaehlt = []

        with self.app.app_context():
            engine = datenbank.engine

        def mitzaehlen(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith('SELECT'):
                gezaehlt.append(statement)

        event.listen(engine, 'before_cursor_execute', mitzaehlen)
        try:
            self._report()
        finally:
            event.remove(engine, 'before_cursor_execute', mitzaehlen)

        beobachtungs_abfragen = [
            statement for statement in gezaehlt
            if 'FROM beobachtung' in statement
        ]
        self.assertEqual(
            1, len(beobachtungs_abfragen),
            f'{len(beobachtungs_abfragen)} Abfragen auf beobachtung statt einer:\n'
            + '\n'.join(beobachtungs_abfragen),
        )

        konfigurations_abfragen = [
            statement for statement in gezaehlt
            if 'FROM system_konfiguration' in statement
        ]
        self.assertLessEqual(
            len(konfigurations_abfragen), 1,
            'Das Schuljahr wird mehrfach abgefragt statt einmal',
        )


if __name__ == '__main__':
    unittest.main()
