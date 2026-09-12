"""Tests fuer die Klassenuebersicht (Kinder mal Kompetenzen).

Die Ansicht soll Unterrichtsentscheidungen tragen: Woran ansetzen, wer braucht
Aufmerksamkeit, wo fehlen Beobachtungen. Entsprechend wird hier vor allem
gerechnet - ein falsches Mittel waere ein Fehler, den niemand bemerkt.
"""

import os
import re
import shutil
import tempfile
import unittest
from datetime import datetime

from werkzeug.security import generate_password_hash

from app import create_app
from competency_matrix import (
    build_matrix,
    competency_level,
    students_needing_attention,
    weakest_items,
)
from extensions import db
from models import (
    Beobachtung,
    Bogen,
    Item,
    Schueler,
    SystemKonfiguration,
    User,
    UserKlassenzuordnung,
)

CSRF_RE = re.compile(r'name="_csrf_token"\s+value="([^"]+)"')

SCHULJAHR_BEGINN = datetime(2026, 8, 1)
IM_SCHULJAHR = datetime(2026, 9, 15)
VORJAHR = datetime(2025, 9, 15)


class CompetencyMatrixTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='km_matrix_test_')
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
                schuljahr='2026/2027', schuljahr_beginn=SCHULJAHR_BEGINN.date(),
            ))
            lehrkraft = User(
                username='lehrkraft',
                password_hash=generate_password_hash('lehrpass'),
                role='teacher',
            )
            db.session.add(lehrkraft)
            db.session.flush()
            db.session.add(UserKlassenzuordnung(
                user_id=lehrkraft.id, klasse='3a', rolle='klassenleitung',
            ))
            self._seed()
            db.session.commit()
            self.bogen_id = self.bogen.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Aufbau
    # ------------------------------------------------------------------

    def _seed(self):
        self.bogen = Bogen(titel='Sozialverhalten')
        anderer_bogen = Bogen(titel='Mathematik')
        db.session.add_all([self.bogen, anderer_bogen])
        db.session.flush()

        # Reihenfolge der Bereiche ist alphabetisch: Arbeit vor Konflikt.
        self.item_konzentration = Item(bogen_id=self.bogen.id, bereich='Arbeit', text='Arbeitet konzentriert')
        self.item_aufraeumen = Item(bogen_id=self.bogen.id, bereich='Arbeit', text='Räumt auf')
        self.item_streit = Item(bogen_id=self.bogen.id, bereich='Konflikt', text='Löst Streit friedlich')
        self.fremdes_item = Item(bogen_id=anderer_bogen.id, bereich='Zahlen', text='Zählt bis 20')
        db.session.add_all([
            self.item_konzentration, self.item_aufraeumen, self.item_streit, self.fremdes_item,
        ])

        self.anna = Schueler(vorname='Anna', nachname='Abt', klasse='3a')
        self.ben = Schueler(vorname='Ben', nachname='Bauer', klasse='3a')
        self.archiviert = Schueler(
            vorname='Emil', nachname='Ehemals', klasse='3a', is_active=False,
        )
        self.andere_klasse = Schueler(vorname='Zoe', nachname='Zander', klasse='4b')
        db.session.add_all([self.anna, self.ben, self.archiviert, self.andere_klasse])
        db.session.flush()

    def _beobachtung(self, schueler, item, wert, datum=IM_SCHULJAHR):
        db.session.add(Beobachtung(
            schueler_id=schueler.id, item_id=item.id, wert=wert, datum=datum,
        ))

    def _matrix(self, include_archived=False):
        bogen = db.session.get(Bogen, self.bogen_id)
        return build_matrix('3a', bogen, include_archived=include_archived)

    # ------------------------------------------------------------------
    # Stufeneinteilung
    # ------------------------------------------------------------------

    def test_level_thresholds_match_the_single_report(self):
        self.assertEqual('stark', competency_level(4.0))
        self.assertEqual('stark', competency_level(3.5))
        self.assertEqual('sicher', competency_level(3.4))
        self.assertEqual('sicher', competency_level(2.5))
        self.assertEqual('teils', competency_level(2.4))
        self.assertEqual('teils', competency_level(1.5))
        self.assertEqual('schwach', competency_level(1.4))
        self.assertEqual('schwach', competency_level(1.0))
        self.assertIsNone(competency_level(None))

    # ------------------------------------------------------------------
    # Zellen
    # ------------------------------------------------------------------

    def test_cell_shows_average_and_count(self):
        with self.app.app_context():
            anna = db.session.merge(self.anna)
            item = db.session.merge(self.item_streit)
            self._beobachtung(anna, item, 2)
            self._beobachtung(anna, item, 3)
            db.session.commit()

            matrix = self._matrix()
            zelle = matrix['cells'][(anna.id, item.id)]
            self.assertEqual(2.5, zelle['average'])
            self.assertEqual(2, zelle['count'])
            self.assertEqual('sicher', zelle['level'])

    def test_cells_without_observations_are_absent(self):
        with self.app.app_context():
            matrix = self._matrix()
            self.assertEqual({}, matrix['cells'])
            self.assertEqual(0, matrix['summary']['coverage'])
            # 2 aktive Kinder mal 3 Kompetenzen des Bogens
            self.assertEqual(6, matrix['summary']['cells_total'])

    def test_previous_school_year_is_excluded(self):
        with self.app.app_context():
            anna = db.session.merge(self.anna)
            item = db.session.merge(self.item_streit)
            self._beobachtung(anna, item, 4)
            self._beobachtung(anna, item, 1, datum=VORJAHR)
            db.session.commit()

            zelle = self._matrix()['cells'][(anna.id, item.id)]
            self.assertEqual(4.0, zelle['average'], 'Eintrag aus dem Vorjahr wurde mitgerechnet')
            self.assertEqual(1, zelle['count'])

    def test_other_bogen_and_other_class_do_not_leak_in(self):
        with self.app.app_context():
            anna = db.session.merge(self.anna)
            zoe = db.session.merge(self.andere_klasse)
            fremd = db.session.merge(self.fremdes_item)
            item = db.session.merge(self.item_streit)
            self._beobachtung(anna, fremd, 1)
            self._beobachtung(zoe, item, 1)
            db.session.commit()

            matrix = self._matrix()
            self.assertEqual({}, matrix['cells'])
            self.assertEqual(
                ['Abt', 'Bauer'],
                [s.nachname for s in matrix['students']],
                'Fremde Klasse erscheint in der Übersicht',
            )

    # ------------------------------------------------------------------
    # Zeilen- und Spaltenmittel
    # ------------------------------------------------------------------

    def test_row_average_uses_all_values_not_the_mean_of_means(self):
        """Zwei Eintraege bei einer Kompetenz wiegen mehr als einer bei einer anderen.

        Mittel der Mittel ergaebe (4 + 1) / 2 = 2.5. Richtig ist das Mittel
        aller Werte: (4 + 4 + 1) / 3 = 3.0.
        """
        with self.app.app_context():
            anna = db.session.merge(self.anna)
            konzentration = db.session.merge(self.item_konzentration)
            streit = db.session.merge(self.item_streit)
            self._beobachtung(anna, konzentration, 4)
            self._beobachtung(anna, konzentration, 4)
            self._beobachtung(anna, streit, 1)
            db.session.commit()

            matrix = self._matrix()
            self.assertEqual(3.0, matrix['student_totals'][anna.id]['average'])
            self.assertEqual(3, matrix['student_totals'][anna.id]['count'])

    def test_column_average_covers_the_whole_class(self):
        with self.app.app_context():
            anna = db.session.merge(self.anna)
            ben = db.session.merge(self.ben)
            item = db.session.merge(self.item_streit)
            self._beobachtung(anna, item, 4)
            self._beobachtung(ben, item, 1)
            self._beobachtung(ben, item, 1)
            db.session.commit()

            gesamt = self._matrix()['item_totals'][item.id]
            self.assertEqual(2.0, gesamt['average'])
            self.assertEqual(3, gesamt['count'])

    def test_gaps_are_counted_per_row_and_column(self):
        with self.app.app_context():
            anna = db.session.merge(self.anna)
            item = db.session.merge(self.item_streit)
            self._beobachtung(anna, item, 3)
            db.session.commit()

            matrix = self._matrix()
            # Anna: 3 Kompetenzen, eine beobachtet
            self.assertEqual(2, matrix['student_totals'][anna.id]['gaps'])
            # Ben: nichts beobachtet
            ben = db.session.merge(self.ben)
            self.assertEqual(3, matrix['student_totals'][ben.id]['gaps'])
            # Diese Kompetenz: ein Kind von zwei fehlt
            self.assertEqual(1, matrix['item_totals'][item.id]['gaps'])

    def test_coverage_counts_filled_cells(self):
        with self.app.app_context():
            anna = db.session.merge(self.anna)
            for item in (self.item_streit, self.item_konzentration, self.item_aufraeumen):
                self._beobachtung(anna, db.session.merge(item), 3)
            db.session.commit()

            summary = self._matrix()['summary']
            self.assertEqual(3, summary['cells_filled'])
            self.assertEqual(6, summary['cells_total'])
            self.assertEqual(50, summary['coverage'])

    # ------------------------------------------------------------------
    # Aufbau der Spalten
    # ------------------------------------------------------------------

    def test_competencies_are_grouped_by_bereich_in_column_order(self):
        with self.app.app_context():
            matrix = self._matrix()
            self.assertEqual(
                ['Arbeit', 'Konflikt'],
                [gruppe['name'] for gruppe in matrix['bereiche']],
            )
            gruppengroessen = [len(gruppe['competencies']) for gruppe in matrix['bereiche']]
            self.assertEqual([2, 1], gruppengroessen)

            # Die flache Spaltenliste muss zur Gruppierung passen, sonst stehen
            # Werte unter der falschen Überschrift.
            aus_gruppen = [
                item.id for gruppe in matrix['bereiche'] for item in gruppe['competencies']
            ]
            self.assertEqual([item.id for item in matrix['competencies']], aus_gruppen)

    def test_matrix_has_no_items_key_that_jinja_would_shadow(self):
        """matrix.items waere in Jinja die Dict-Methode, nicht die Spaltenliste."""
        with self.app.app_context():
            matrix = self._matrix()
            self.assertNotIn('items', matrix)
            self.assertIn('competencies', matrix)
            for gruppe in matrix['bereiche']:
                self.assertNotIn('items', gruppe)

    # ------------------------------------------------------------------
    # Archivierte Kinder
    # ------------------------------------------------------------------

    def test_archived_students_are_hidden_unless_requested(self):
        with self.app.app_context():
            ohne = self._matrix()
            self.assertNotIn('Ehemals', [s.nachname for s in ohne['students']])

            mit = self._matrix(include_archived=True)
            self.assertIn('Ehemals', [s.nachname for s in mit['students']])
            self.assertEqual(9, mit['summary']['cells_total'])

    # ------------------------------------------------------------------
    # Auswertungen
    # ------------------------------------------------------------------

    def test_weakest_items_are_sorted_and_skip_unobserved(self):
        with self.app.app_context():
            anna = db.session.merge(self.anna)
            self._beobachtung(anna, db.session.merge(self.item_streit), 1)
            self._beobachtung(anna, db.session.merge(self.item_konzentration), 3)
            db.session.commit()

            matrix = self._matrix()
            ergebnis = weakest_items(matrix)
            self.assertEqual(
                ['Löst Streit friedlich', 'Arbeitet konzentriert'],
                [item.text for item, _ in ergebnis],
            )
            self.assertNotIn(
                'Räumt auf', [item.text for item, _ in ergebnis],
                'Kompetenz ohne Beobachtung als Schwäche gemeldet',
            )

    def test_students_needing_attention_are_sorted_and_skip_unobserved(self):
        with self.app.app_context():
            anna = db.session.merge(self.anna)
            ben = db.session.merge(self.ben)
            item = db.session.merge(self.item_streit)
            self._beobachtung(anna, item, 4)
            self._beobachtung(ben, item, 1)
            db.session.commit()

            ergebnis = students_needing_attention(self._matrix())
            self.assertEqual(['Bauer', 'Abt'], [s.nachname for s, _ in ergebnis])

    def test_evaluations_are_empty_without_observations(self):
        with self.app.app_context():
            matrix = self._matrix()
            self.assertEqual([], weakest_items(matrix))
            self.assertEqual([], students_needing_attention(matrix))

    # ------------------------------------------------------------------
    # Route
    # ------------------------------------------------------------------

    def _login(self):
        page = self.client.get('/login')
        token = CSRF_RE.search(page.get_data(as_text=True)).group(1)
        self.assertEqual(
            302,
            self.client.post('/login', data={
                'username': 'lehrkraft', 'password': 'lehrpass', '_csrf_token': token,
            }).status_code,
        )

    def test_route_requires_login(self):
        response = self.client.get('/report/matrix')
        self.assertEqual(302, response.status_code)
        self.assertIn('/login', response.headers['Location'])

    def test_route_preselects_the_teachers_own_class(self):
        self._login()
        response = self.client.get('/report/matrix')
        self.assertEqual(200, response.status_code)
        self.assertIn('value="3a" selected', response.get_data(as_text=True))

    def test_route_defaults_to_the_bogen_with_the_most_observations(self):
        """Der alphabetisch erste Bogen ist "Mathematik" und hier ohne Daten."""
        with self.app.app_context():
            anna = db.session.merge(self.anna)
            self._beobachtung(anna, db.session.merge(self.item_streit), 3)
            db.session.commit()

        self._login()
        html = self.client.get('/report/matrix').get_data(as_text=True)
        self.assertIn(
            f'value="{self.bogen_id}" selected', html,
            'Voreinstellung fiel auf einen Bogen ohne Beobachtungen zurück',
        )

    def test_route_renders_values_and_links(self):
        with self.app.app_context():
            anna = db.session.merge(self.anna)
            item = db.session.merge(self.item_streit)
            self._beobachtung(anna, item, 2)
            self._beobachtung(anna, item, 3)
            db.session.commit()
            anna_id, item_id = anna.id, item.id

        self._login()
        html = self.client.get('/report/matrix').get_data(as_text=True)

        self.assertIn('2.5', html)
        self.assertIn(f'schueler_id={anna_id}&amp;bogen_id={self.bogen_id}', html)
        # Leere Zellen fuehren in den vorbelegten Schnelleintrag.
        self.assertIn('/erfassen/einzel?', html)
        self.assertIn(f'item_id={item_id}', html)

    def test_route_falls_back_to_a_valid_class_and_bogen(self):
        self._login()
        response = self.client.get('/report/matrix?klasse=gibtsnicht&bogen_id=999999')
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn('value="3a" selected', html)
        self.assertIn('Sozialverhalten', html)

    def test_route_reports_a_bogen_without_competencies(self):
        with self.app.app_context():
            leer = Bogen(titel='Leerer Bogen')
            db.session.add(leer)
            db.session.commit()
            leer_id = leer.id

        self._login()
        html = self.client.get(f'/report/matrix?bogen_id={leer_id}').get_data(as_text=True)
        self.assertIn('enthält noch keine Kompetenzen', html)

    def test_route_reports_a_class_without_active_children(self):
        with self.app.app_context():
            for schueler in Schueler.query.filter(Schueler.klasse == '3a').all():
                schueler.is_active = False
            db.session.commit()

        self._login()
        html = self.client.get('/report/matrix?klasse=3a').get_data(as_text=True)
        self.assertIn('keine', html)
        self.assertIn('Kinder eingetragen', html)

    def test_requested_class_is_kept_even_when_all_children_are_archived(self):
        """Die Klasse darf nicht stillschweigend gegen eine andere getauscht werden."""
        with self.app.app_context():
            for schueler in Schueler.query.filter(Schueler.klasse == '3a').all():
                schueler.is_active = False
            db.session.commit()

        self._login()
        html = self.client.get('/report/matrix?klasse=3a').get_data(as_text=True)
        self.assertIn('value="3a" selected', html)
        self.assertNotIn('value="4b" selected', html)
        self.assertIn('Archivierte Kinder mitzeigen', html)

    def test_archived_only_class_is_shown_with_the_toggle(self):
        with self.app.app_context():
            for schueler in Schueler.query.filter(Schueler.klasse == '3a').all():
                schueler.is_active = False
            anna = db.session.merge(self.anna)
            self._beobachtung(anna, db.session.merge(self.item_streit), 3)
            db.session.commit()

        self._login()
        html = self.client.get(
            f'/report/matrix?klasse=3a&bogen_id={self.bogen_id}&show=archived'
        ).get_data(as_text=True)
        self.assertIn('Abt', html, 'Archiviertes Kind fehlt trotz Schalter')
        self.assertIn('archiviert', html)

    def test_dashboard_links_to_the_matrix(self):
        self._login()
        html = self.client.get('/').get_data(as_text=True)
        self.assertIn('/report/matrix', html)


if __name__ == '__main__':
    unittest.main()
