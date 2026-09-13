"""Tests: der Schuljahreswechsel richtet sich nach dem Jahrgang.

Klassen wie "3a" wandern nach "4a", Gruppen mit freiem Namen altern gemeinsam,
wer eine jahrgangsuebergreifende Klasse verlaesst, braucht eine Zielklasse, und
Kinder ohne bestimmbaren Jahrgang bleiben unveraendert.
"""

import os
import re
import shutil
import tempfile
import unittest

from werkzeug.datastructures import MultiDict
from werkzeug.security import generate_password_hash

from app import create_app
from extensions import db
from jahrgang import ensure_klasse, set_klassen_jahrgaenge
from models import Klasse, Schueler, Schuljahreswechsel, SystemKonfiguration, User, UserKlassenzuordnung

CSRF_RE = re.compile(r'name="_csrf_token"\s+value="([^"]+)"')


class TransitionPlanTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='km_transition_plan_')
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
            db.session.add_all([
                User(username='admin', password_hash=generate_password_hash('adminpass'), role='admin'),
                SystemKonfiguration(schuljahr='2025/2026'),
            ])
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

    def _klasse(self, name, jahrgaenge=None):
        with self.app.app_context():
            klasse = ensure_klasse(name)
            if jahrgaenge is not None:
                set_klassen_jahrgaenge(klasse, jahrgaenge)
            db.session.commit()

    def _kind(self, klasse, jahrgang=None, vorname='Anna'):
        with self.app.app_context():
            kind = Schueler(vorname=vorname, nachname='Abt', klasse=klasse, jahrgang=jahrgang)
            db.session.add(kind)
            db.session.commit()
            return kind.id

    def _wechsel(self, extra=()):
        page = self.client.get('/admin/schuljahreswechsel')
        token = CSRF_RE.search(page.get_data(as_text=True)).group(1)
        return self.client.post('/admin/schuljahreswechsel', data=MultiDict([
            ('_csrf_token', token),
            ('action', 'execute'),
            ('bisheriges_schuljahr', '2025/2026'),
            ('neues_schuljahr', '2026/2027'),
            ('schuljahr_beginn', '2026-08-01'),
            *extra,
        ]), follow_redirects=True)

    def _stand(self, kind_id):
        with self.app.app_context():
            kind = db.session.get(Schueler, kind_id)
            return kind.klasse, kind.jahrgang, kind.is_active

    def _wechsel_erfolgt(self):
        with self.app.app_context():
            return Schuljahreswechsel.query.count() == 1

    # ------------------------------------------------------------------
    # Klassen mit Stufennamen
    # ------------------------------------------------------------------

    def test_grade_named_class_moves_up_and_sets_grade(self):
        kind_id = self._kind('3a', jahrgang=3)
        self._wechsel()
        self.assertEqual(('4a', 4, True), self._stand(kind_id))
        with self.app.app_context():
            self.assertIsNotNone(Klasse.query.filter_by(name='4a').first())
            self.assertEqual([3], Klasse.query.filter_by(name='3a').one().jahrgaenge)

    def test_grade_is_derived_from_class_when_not_stored(self):
        kind_id = self._kind('2b')
        self._wechsel()
        self.assertEqual(('3b', 3, True), self._stand(kind_id))

    def test_fourth_grade_is_archived_with_grade_kept(self):
        kind_id = self._kind('4a')
        self._wechsel()
        klasse, jahrgang, aktiv = self._stand(kind_id)
        self.assertFalse(aktiv)
        self.assertEqual(('4a', 4), (klasse, jahrgang))

    # ------------------------------------------------------------------
    # Frei benannte Klassen
    # ------------------------------------------------------------------

    def test_free_named_group_stays_together_and_ages(self):
        self._klasse('Füchse', [2])
        kind_id = self._kind('Füchse', jahrgang=2)
        with self.app.app_context():
            lehrkraft = User.query.filter_by(username='admin').one()
            db.session.add(UserKlassenzuordnung(user_id=lehrkraft.id, klasse='Füchse', rolle='klassenleitung'))
            db.session.commit()

        self._wechsel()

        self.assertEqual(('Füchse', 3, True), self._stand(kind_id))
        with self.app.app_context():
            self.assertEqual([3], Klasse.query.filter_by(name='Füchse').one().jahrgaenge)
            self.assertEqual(['Füchse'], [z.klasse for z in UserKlassenzuordnung.query.all()])

    def test_free_named_final_group_is_archived_and_assignment_removed(self):
        self._klasse('Eulen', [4])
        kind_id = self._kind('Eulen', jahrgang=4)
        with self.app.app_context():
            lehrkraft = User.query.filter_by(username='admin').one()
            db.session.add(UserKlassenzuordnung(user_id=lehrkraft.id, klasse='Eulen', rolle='klassenleitung'))
            db.session.commit()

        self._wechsel()

        self.assertFalse(self._stand(kind_id)[2])
        with self.app.app_context():
            self.assertEqual(0, UserKlassenzuordnung.query.count())

    # ------------------------------------------------------------------
    # Jahrgangsuebergreifende Klassen
    # ------------------------------------------------------------------

    def test_mixed_class_keeps_children_whose_new_grade_belongs(self):
        self._klasse('Blau', [1, 2])
        kind_id = self._kind('Blau', jahrgang=1)
        self._wechsel()
        self.assertEqual(('Blau', 2, True), self._stand(kind_id))
        with self.app.app_context():
            self.assertEqual([1, 2], Klasse.query.filter_by(name='Blau').one().jahrgaenge)

    def test_mixed_class_leaver_needs_a_target(self):
        self._klasse('Blau', [1, 2])
        self._klasse('Rot', [3, 4])
        kind_id = self._kind('Blau', jahrgang=2)

        seite = self.client.get('/admin/schuljahreswechsel').get_data(as_text=True)
        self.assertIn('verlässt Blau: Jahrgang 3 gehört nicht zur Klasse', seite)
        self.assertIn(f'name="individual_target_{kind_id}"', seite)

        antwort = self._wechsel().get_data(as_text=True)
        self.assertIn('Zielklasse wählen', antwort)
        self.assertFalse(self._wechsel_erfolgt(), 'Wechsel ohne Zielklasse ausgeführt')
        self.assertEqual(('Blau', 2, True), self._stand(kind_id))

        # Eine Klasse, die Jahrgang 3 nicht fuehrt, wird abgelehnt.
        self._wechsel([(f'individual_target_{kind_id}', 'Blau')])
        self.assertFalse(self._wechsel_erfolgt())

        self._wechsel([(f'individual_target_{kind_id}', 'Rot')])
        self.assertTrue(self._wechsel_erfolgt())
        self.assertEqual(('Rot', 3, True), self._stand(kind_id))

    def test_leaver_target_options_include_promoted_grade_named_classes(self):
        self._klasse('Blau', [1, 2])
        self._klasse('2a')
        kind_id = self._kind('Blau', jahrgang=2)
        seite = self.client.get('/admin/schuljahreswechsel').get_data(as_text=True)
        auswahl = re.search(
            rf'name="individual_target_{kind_id}".*?</select>', seite, re.S,
        ).group(0)
        # "3a" gibt es noch nicht, entsteht aber aus "2a".
        self.assertIn('value="3a"', auswahl)
        self.assertNotIn('value="Blau"', auswahl)

    # ------------------------------------------------------------------
    # Wiederholer und fehlende Jahrgaenge
    # ------------------------------------------------------------------

    def test_repeater_keeps_grade_in_chosen_class(self):
        self._klasse('Füchse', [2])
        self._klasse('2a')
        kind_id = self._kind('Füchse', jahrgang=2)
        self._wechsel([('wiederholer_ids', str(kind_id)), (f'individual_target_{kind_id}', '2a')])
        self.assertEqual(('2a', 2, True), self._stand(kind_id))

    def test_repeater_cannot_stay_in_group_that_ages(self):
        self._klasse('Füchse', [2])
        kind_id = self._kind('Füchse', jahrgang=2)
        self._wechsel([('wiederholer_ids', str(kind_id)), (f'individual_target_{kind_id}', 'Füchse')])
        self.assertFalse(self._wechsel_erfolgt())

    def test_child_without_grade_stays_unchanged(self):
        self._klasse('Wolken', [])
        kind_id = self._kind('Wolken')
        seite = self.client.get('/admin/schuljahreswechsel').get_data(as_text=True)
        self.assertIn('unverändert – Jahrgang fehlt', seite)
        self.assertIn('Klassen und Jahrgänge', seite)

        self._wechsel()
        self.assertTrue(self._wechsel_erfolgt())
        self.assertEqual(('Wolken', None, True), self._stand(kind_id))


if __name__ == '__main__':
    unittest.main()
