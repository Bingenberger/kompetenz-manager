"""Tests fuer Jahrgaenge von Klassen, Kindern und Boegen.

Jahrgang heisst Klassenstufe 1 bis 4. Eine Klasse hat einen oder mehrere, ein
Kind genau einen. Bei Klassennamen wie "3a" ergibt sich der Jahrgang aus dem
Namen und ist fest, frei benannte Klassen bekommen ihre Jahrgaenge von Hand.
"""

import os
import shutil
import tempfile
import unittest

from app import create_app
from extensions import db
from jahrgang import (
    backfill_student_jahrgaenge,
    boegen_fuer_jahrgang,
    ensure_klasse,
    grade_from_name,
    klassen_jahrgaenge,
    name_for_grade,
    normalize_jahrgaenge,
    resolve_student_jahrgang,
    set_klassen_jahrgaenge,
    single_jahrgang,
    sync_klassen,
)
from models import (
    Bogen,
    BogenJahrgang,
    ClassTaskLibrary,
    Klasse,
    KlasseJahrgang,
    Schueler,
    User,
    UserKlassenzuordnung,
)


class NamingRulesTestCase(unittest.TestCase):
    """Die Namensregeln fuer sich - ohne Datenbank."""

    def test_grade_from_name(self):
        self.assertEqual(3, grade_from_name('3a'))
        self.assertEqual(4, grade_from_name('4b'))
        self.assertEqual(1, grade_from_name('1'))
        self.assertEqual(2, grade_from_name(' 2c '))

    def test_free_names_carry_no_grade(self):
        for name in ('Füchse', 'Blau', '1/2', '3a/b', '5a', '0a', 'a3', '', None):
            with self.subTest(name=name):
                self.assertIsNone(grade_from_name(name))

    def test_mixed_name_with_slash_is_not_a_single_grade(self):
        """"1/2" ist erkennbar jahrgangsuebergreifend, nicht Stufe 1."""
        self.assertIsNone(grade_from_name('1/2'))

    def test_name_for_grade(self):
        self.assertEqual('4a', name_for_grade('3a', 4))
        self.assertEqual('2', name_for_grade('1', 2))
        self.assertIsNone(name_for_grade('Füchse', 4))

    def test_normalize_jahrgaenge(self):
        self.assertEqual([1, 2], normalize_jahrgaenge(['2', '1', '2', 'x', '9', None]))
        self.assertEqual([], normalize_jahrgaenge(None))


class JahrgangDataTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='km_jahrgang_test_')
        database_uri = os.environ.get('TEST_DATABASE_URL') or f"sqlite:///{self.tmpdir}/test.db"
        self.app = create_app({
            'TESTING': True,
            'SECRET_KEY': 'test-secret',
            'SQLALCHEMY_DATABASE_URI': database_uri,
            'UPLOAD_FOLDER': os.path.join(self.tmpdir, 'uploads'),
            'PROTECTED_UPLOAD_FOLDER': os.path.join(self.tmpdir, 'protected_uploads'),
        })
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Klassen
    # ------------------------------------------------------------------

    def test_new_class_with_grade_name_gets_its_grade(self):
        klasse = ensure_klasse('3a')
        self.assertEqual([3], klasse.jahrgaenge)

    def test_new_class_with_free_name_gets_no_grade(self):
        klasse = ensure_klasse('Füchse')
        self.assertEqual([], klasse.jahrgaenge)

    def test_ensure_klasse_does_not_duplicate(self):
        erste = ensure_klasse('3a')
        zweite = ensure_klasse('3a')
        self.assertEqual(erste.id, zweite.id)
        self.assertEqual(1, Klasse.query.count())

    def test_free_class_can_be_mixed(self):
        klasse = ensure_klasse('Blau')
        set_klassen_jahrgaenge(klasse, ['1', '2'])
        self.assertEqual([1, 2], klasse.jahrgaenge)
        self.assertTrue(klasse.ist_jahrgangsuebergreifend)

    def test_grade_named_class_keeps_its_grade(self):
        klasse = ensure_klasse('3a')
        with self.assertRaises(ValueError):
            set_klassen_jahrgaenge(klasse, [2])
        with self.assertRaises(ValueError):
            set_klassen_jahrgaenge(klasse, [3, 4])
        self.assertEqual([3], klasse.jahrgaenge)

    def test_setting_the_matching_grade_on_a_grade_named_class_is_fine(self):
        klasse = ensure_klasse('3a')
        self.assertEqual([3], set_klassen_jahrgaenge(klasse, [3]))
        self.assertEqual([3], set_klassen_jahrgaenge(klasse, []))

    def test_replacing_jahrgaenge_leaves_no_stale_rows(self):
        klasse = ensure_klasse('Blau')
        set_klassen_jahrgaenge(klasse, [1, 2])
        set_klassen_jahrgaenge(klasse, [3])
        db.session.commit()
        self.assertEqual([3], klasse.jahrgaenge)
        self.assertEqual(1, KlasseJahrgang.query.count())

    def test_single_jahrgang(self):
        set_klassen_jahrgaenge(ensure_klasse('Blau'), [1, 2])
        ensure_klasse('3a')
        self.assertEqual(3, single_jahrgang('3a'))
        self.assertIsNone(single_jahrgang('Blau'))
        # Unbekannte Klasse mit Stufennamen: Jahrgang aus dem Namen
        self.assertEqual(2, single_jahrgang('2c'))
        self.assertIsNone(single_jahrgang('Unbekannt'))

    def test_sync_creates_classes_from_every_reference(self):
        db.session.add_all([
            Schueler(vorname='A', nachname='A', klasse='3a'),
            Schueler(vorname='B', nachname='B', klasse='Füchse'),
            Schueler(vorname='C', nachname='C', klasse=None),
        ])
        user = User(username='l', password_hash='x')
        db.session.add(user)
        db.session.flush()
        db.session.add(UserKlassenzuordnung(user_id=user.id, klasse='2b', rolle='fach'))
        db.session.add(ClassTaskLibrary(class_name='4c', name='Bibliothek'))
        db.session.commit()

        angelegt = sync_klassen()
        self.assertEqual(['2b', '3a', '4c', 'Füchse'], sorted(angelegt))
        self.assertEqual([], sync_klassen(), 'Zweiter Lauf legt erneut an')

    # ------------------------------------------------------------------
    # Kinder
    # ------------------------------------------------------------------

    def test_backfill_takes_the_grade_from_single_grade_classes(self):
        ensure_klasse('3a')
        set_klassen_jahrgaenge(ensure_klasse('Blau'), [1, 2])
        db.session.add_all([
            Schueler(vorname='A', nachname='A', klasse='3a'),
            Schueler(vorname='B', nachname='B', klasse='Blau'),
            Schueler(vorname='C', nachname='C', klasse='Füchse'),
        ])
        db.session.commit()

        self.assertEqual(1, backfill_student_jahrgaenge())
        nach_name = {s.vorname: s.jahrgang for s in Schueler.query.all()}
        self.assertEqual(3, nach_name['A'])
        self.assertIsNone(nach_name['B'], 'Bei gemischter Klasse darf nicht geraten werden')
        self.assertIsNone(nach_name['C'])

    def test_backfill_never_overwrites(self):
        ensure_klasse('3a')
        db.session.add(Schueler(vorname='A', nachname='A', klasse='3a', jahrgang=2))
        db.session.commit()
        backfill_student_jahrgaenge()
        self.assertEqual(2, Schueler.query.one().jahrgang)

    def test_resolve_in_single_grade_class_ignores_input(self):
        ensure_klasse('3a')
        self.assertEqual((3, None), resolve_student_jahrgang('3a', '1'))
        self.assertEqual((3, None), resolve_student_jahrgang('3a', None))

    def test_resolve_in_mixed_class_requires_one_of_its_grades(self):
        set_klassen_jahrgaenge(ensure_klasse('Blau'), [1, 2])
        self.assertEqual((2, None), resolve_student_jahrgang('Blau', '2'))

        jahrgang, fehler = resolve_student_jahrgang('Blau', '3')
        self.assertIsNone(jahrgang)
        self.assertIn('jahrgangsübergreifend', fehler)

        jahrgang, fehler = resolve_student_jahrgang('Blau', None)
        self.assertIsNone(jahrgang)
        self.assertIsNotNone(fehler)

    def test_resolve_without_class_grades_takes_valid_input(self):
        ensure_klasse('Füchse')
        self.assertEqual((4, None), resolve_student_jahrgang('Füchse', '4'))
        self.assertEqual((None, None), resolve_student_jahrgang('Füchse', '7'))

    # ------------------------------------------------------------------
    # Boegen
    # ------------------------------------------------------------------

    def test_bogen_without_assignment_applies_to_all(self):
        bogen = Bogen(titel='Allgemein')
        db.session.add(bogen)
        db.session.commit()
        for stufe in (1, 2, 3, 4):
            self.assertTrue(bogen.gilt_fuer(stufe))

    def test_bogen_with_assignment_applies_only_there(self):
        bogen = Bogen(titel='Schreiben ab 3')
        db.session.add(bogen)
        db.session.flush()
        db.session.add_all([
            BogenJahrgang(bogen_id=bogen.id, jahrgang=3),
            BogenJahrgang(bogen_id=bogen.id, jahrgang=4),
        ])
        db.session.commit()
        self.assertEqual([3, 4], bogen.jahrgaenge)
        self.assertFalse(bogen.gilt_fuer(1))
        self.assertTrue(bogen.gilt_fuer(3))

    def test_boegen_fuer_jahrgang(self):
        allgemein = Bogen(titel='Allgemein')
        anfang = Bogen(titel='Anfangsunterricht')
        db.session.add_all([allgemein, anfang])
        db.session.flush()
        db.session.add(BogenJahrgang(bogen_id=anfang.id, jahrgang=1))
        db.session.commit()

        self.assertEqual(
            ['Allgemein', 'Anfangsunterricht'],
            [b.titel for b in boegen_fuer_jahrgang(1)],
        )
        self.assertEqual(['Allgemein'], [b.titel for b in boegen_fuer_jahrgang(3)])

    def test_unknown_jahrgang_offers_every_bogen(self):
        """Lieber ein Bogen zu viel als eine Beobachtung, die sich nicht erfassen laesst."""
        anfang = Bogen(titel='Anfangsunterricht')
        db.session.add(anfang)
        db.session.flush()
        db.session.add(BogenJahrgang(bogen_id=anfang.id, jahrgang=1))
        db.session.commit()
        self.assertEqual(['Anfangsunterricht'], [b.titel for b in boegen_fuer_jahrgang(None)])

    def test_deleting_a_bogen_removes_its_assignments(self):
        bogen = Bogen(titel='Weg')
        db.session.add(bogen)
        db.session.flush()
        db.session.add(BogenJahrgang(bogen_id=bogen.id, jahrgang=2))
        db.session.commit()
        db.session.delete(bogen)
        db.session.commit()
        self.assertEqual(0, BogenJahrgang.query.count())


if __name__ == '__main__':
    unittest.main()
