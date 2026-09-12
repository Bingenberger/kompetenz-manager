"""Tests fuer die Suche.

Die Suche ist der kuerzeste Weg zu einem Kind und wird entsprechend oft
benutzt. Geprueft wird deshalb nicht nur, dass etwas gefunden wird, sondern
auch die Faelle, in denen eine Suche stillschweigend falsch antwortet: leere
Spalten, LIKE-Sonderzeichen in der Eingabe, mehrere Begriffe.
"""

import os
import re
import shutil
import tempfile
import unittest

from werkzeug.security import generate_password_hash

from app import create_app
from extensions import db
from models import Bogen, Item, Schueler, User, UserKlassenzuordnung
from search import search, tokenize

CSRF_RE = re.compile(r'name="_csrf_token"\s+value="([^"]+)"')


class SearchTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='km_search_test_')
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
            self._seed()
            db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _seed(self):
        lehrkraft = User(
            username='lehrkraft',
            password_hash=generate_password_hash('lehrpass'),
            role='teacher',
        )
        db.session.add(lehrkraft)
        db.session.flush()
        db.session.add(UserKlassenzuordnung(
            user_id=lehrkraft.id, klasse='4b', rolle='klassenleitung',
        ))
        self.user_id = lehrkraft.id

        bogen = Bogen(titel='Sozialverhalten')
        anderer = Bogen(titel='Mathematik')
        db.session.add_all([bogen, anderer])
        db.session.flush()
        db.session.add_all([
            Item(bogen_id=bogen.id, bereich='Konflikt', text='Loest Streit friedlich'),
            Item(bogen_id=bogen.id, bereich='Arbeit', text='Arbeitet konzentriert'),
            Item(bogen_id=anderer.id, bereich='Zahlen', text='Zaehlt bis 20'),
            # Kompetenz ohne Bereich: die Verkettung darf nicht NULL werden.
            Item(bogen_id=anderer.id, bereich=None, text='Ohne Bereich erfasst'),
        ])

        db.session.add_all([
            Schueler(vorname='Anna', nachname='Abt', klasse='3a'),
            Schueler(vorname='Anna', nachname='Berger', klasse='4b'),
            Schueler(vorname='Bea', nachname='Bauer', klasse='4b'),
            Schueler(vorname='Emil', nachname='Ehemals', klasse='3a', is_active=False),
            # Kind ohne Klasse: die Verkettung darf nicht NULL werden.
            Schueler(vorname='Ohne', nachname='Klasse', klasse=None),
            # Prozentzeichen im Namen, um die Maskierung zu pruefen.
            Schueler(vorname='Ben', nachname='100%Test', klasse='4a'),
        ])
        self.bogen_id = bogen.id

    def _search(self, query, **kwargs):
        user = db.session.get(User, self.user_id)
        return search(user, query, **kwargs)

    def _names(self, ergebnis):
        return [schueler.nachname for schueler in ergebnis['students']]

    # ------------------------------------------------------------------
    # Zerlegung
    # ------------------------------------------------------------------

    def test_tokenize_splits_and_drops_empty(self):
        self.assertEqual(['abt', '3a'], tokenize('  abt   3a '))
        self.assertEqual([], tokenize(''))
        self.assertEqual([], tokenize('   '))
        self.assertEqual([], tokenize(None))

    # ------------------------------------------------------------------
    # Kinder
    # ------------------------------------------------------------------

    def test_finds_children_by_last_name(self):
        with self.app.app_context():
            self.assertEqual(['Abt'], self._names(self._search('abt')))

    def test_finds_children_by_first_name(self):
        with self.app.app_context():
            self.assertEqual(['Abt', 'Berger'], sorted(self._names(self._search('anna'))))

    def test_finds_children_by_class(self):
        with self.app.app_context():
            self.assertEqual(['Bauer', 'Berger'], sorted(self._names(self._search('4b'))))

    def test_all_terms_must_match(self):
        with self.app.app_context():
            self.assertEqual(['Berger'], self._names(self._search('anna 4b')))
            self.assertEqual([], self._names(self._search('anna 9z')))

    def test_term_order_does_not_matter(self):
        with self.app.app_context():
            self.assertEqual(
                self._names(self._search('anna berger')),
                self._names(self._search('berger anna')),
            )
            self.assertEqual(['Berger'], self._names(self._search('berger anna')))

    def test_child_without_a_class_is_still_found(self):
        """Verkettung mit NULL ergibt in SQL NULL - ohne coalesce unfindbar."""
        with self.app.app_context():
            self.assertEqual(['Klasse'], self._names(self._search('ohne klasse')))

    def test_lowercase_finds_capitalised_umlaut_names(self):
        """Der Alltagsfall: klein getippt, gross geschrieben gespeichert."""
        with self.app.app_context():
            db.session.add(Schueler(vorname='Lena', nachname='Müller', klasse='3a'))
            db.session.commit()
            self.assertEqual(['Müller'], self._names(self._search('müller')))
            self.assertEqual(['Müller'], self._names(self._search('Müller')))

    # ------------------------------------------------------------------
    # Archivierte Kinder
    # ------------------------------------------------------------------

    def test_archived_children_are_found_but_marked(self):
        with self.app.app_context():
            ergebnis = self._search('ehemals')
            self.assertEqual(['Ehemals'], self._names(ergebnis))
            self.assertFalse(ergebnis['students'][0].is_active)

    def test_archived_children_can_be_excluded(self):
        with self.app.app_context():
            self.assertEqual([], self._names(self._search('ehemals', include_archived=False)))

    def test_archived_children_sort_after_active_ones(self):
        with self.app.app_context():
            db.session.add(Schueler(vorname='Aktiv', nachname='Ehemals', klasse='3a'))
            db.session.commit()
            ergebnis = self._search('ehemals')
            self.assertEqual(
                [True, False],
                [schueler.is_active for schueler in ergebnis['students']],
                'Archiviertes Kind steht vor einem aktiven',
            )

    def test_own_class_comes_first(self):
        """Klassenleitung ist 4b - dieselbe Rangfolge wie in den Auswahllisten."""
        with self.app.app_context():
            db.session.add_all([
                Schueler(vorname='Tom', nachname='Zzz', klasse='4b'),
                Schueler(vorname='Tom', nachname='Aaa', klasse='1a'),
            ])
            db.session.commit()
            ergebnis = self._search('tom')
            self.assertEqual(
                ['4b', '1a'],
                [schueler.klasse for schueler in ergebnis['students']],
                'Eigene Klasse steht nicht vorn',
            )

    # ------------------------------------------------------------------
    # LIKE-Sonderzeichen
    # ------------------------------------------------------------------

    def test_percent_is_text_not_a_wildcard(self):
        with self.app.app_context():
            # Ohne Maskierung wuerde "%a" jedes Kind mit einem a finden.
            self.assertEqual([], self._names(self._search('%a')))
            # Als Text im Namen muss es dagegen treffen.
            self.assertEqual(['100%Test'], self._names(self._search('100%')))

    def test_underscore_is_text_not_a_wildcard(self):
        with self.app.app_context():
            # Ohne Maskierung wuerde "__" auf zwei beliebige Zeichen passen.
            self.assertEqual([], self._names(self._search('__')))

    def test_backslash_does_not_break_the_query(self):
        with self.app.app_context():
            ergebnis = self._search('ab\\c')
            self.assertEqual([], self._names(ergebnis))

    # ------------------------------------------------------------------
    # Kompetenzen und Boegen
    # ------------------------------------------------------------------

    def test_finds_competencies_by_text(self):
        with self.app.app_context():
            ergebnis = self._search('streit')
            self.assertEqual(['Loest Streit friedlich'], [i.text for i in ergebnis['competencies']])

    def test_finds_competencies_by_bereich(self):
        with self.app.app_context():
            ergebnis = self._search('konflikt')
            self.assertEqual(['Loest Streit friedlich'], [i.text for i in ergebnis['competencies']])

    def test_competency_without_bereich_is_still_found(self):
        with self.app.app_context():
            ergebnis = self._search('ohne bereich')
            self.assertIn('Ohne Bereich erfasst', [i.text for i in ergebnis['competencies']])

    def test_finds_boegen_by_title(self):
        with self.app.app_context():
            ergebnis = self._search('mathematik')
            self.assertEqual(['Mathematik'], [b.titel for b in ergebnis['boegen']])

    def test_one_query_can_hit_several_groups(self):
        with self.app.app_context():
            bogen = db.session.get(Bogen, self.bogen_id)
            db.session.add(Item(
                bogen_id=bogen.id, bereich='Gruppe', text='Arbeitet im Sozialverband',
            ))
            db.session.add(Schueler(vorname='Sozial', nachname='Sonderfall', klasse='2a'))
            db.session.commit()

            ergebnis = self._search('sozial')
            self.assertEqual(['Sonderfall'], self._names(ergebnis))
            self.assertEqual(
                ['Arbeitet im Sozialverband'], [i.text for i in ergebnis['competencies']],
            )
            self.assertEqual(['Sozialverhalten'], [b.titel for b in ergebnis['boegen']])
            self.assertEqual(3, ergebnis['total'])

    # ------------------------------------------------------------------
    # Leere und zu kurze Eingaben
    # ------------------------------------------------------------------

    def test_empty_query_searches_nothing(self):
        with self.app.app_context():
            ergebnis = self._search('')
            self.assertEqual(0, ergebnis['total'])
            self.assertFalse(ergebnis['too_short'])

    def test_single_character_is_reported_as_too_short(self):
        with self.app.app_context():
            ergebnis = self._search('a')
            self.assertTrue(ergebnis['too_short'])
            self.assertEqual(0, ergebnis['total'])

    def test_two_characters_are_searched(self):
        with self.app.app_context():
            ergebnis = self._search('be')
            self.assertFalse(ergebnis['too_short'])
            self.assertIn('Berger', self._names(ergebnis))

    # ------------------------------------------------------------------
    # Route
    # ------------------------------------------------------------------

    def _login(self):
        page = self.client.get('/login')
        token = CSRF_RE.search(page.get_data(as_text=True)).group(1)
        self.assertEqual(302, self.client.post('/login', data={
            'username': 'lehrkraft', 'password': 'lehrpass', '_csrf_token': token,
        }).status_code)

    def test_route_requires_login(self):
        response = self.client.get('/suche?q=abt')
        self.assertEqual(302, response.status_code)
        self.assertIn('/login', response.headers['Location'])

    def test_search_field_is_in_the_navbar(self):
        self._login()
        self.assertIn('id="nav-suche"', self.client.get('/').get_data(as_text=True))

    def test_single_child_hit_jumps_straight_to_the_record(self):
        self._login()
        response = self.client.get('/suche?q=abt')
        self.assertEqual(302, response.status_code)
        self.assertIn('/schuelerakte?schueler_id=', response.headers['Location'])

    def test_several_hits_show_the_result_list(self):
        self._login()
        response = self.client.get('/suche?q=anna')
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn('Abt', html)
        self.assertIn('Berger', html)

    def test_route_reports_nothing_found(self):
        self._login()
        html = self.client.get('/suche?q=xyznichts').get_data(as_text=True)
        self.assertIn('wurde nichts gefunden', html)

    def test_route_reports_a_too_short_query(self):
        self._login()
        html = self.client.get('/suche?q=a').get_data(as_text=True)
        self.assertIn('mindestens zwei Zeichen', html)

    def test_route_without_a_query_explains_the_search(self):
        self._login()
        html = self.client.get('/suche').get_data(as_text=True)
        self.assertIn('Gesucht wird nach Kindern', html)

    def test_competency_hit_links_to_prefilled_quick_entry(self):
        self._login()
        html = self.client.get('/suche?q=streit').get_data(as_text=True)
        self.assertIn('/erfassen/einzel?', html)
        self.assertIn('bereich=Konflikt', html)


if __name__ == '__main__':
    unittest.main()
