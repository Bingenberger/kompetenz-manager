"""Tests fuer das Aenderungsprotokoll von Foerderplaenen und Elternkontakten.

Fuer erzieherische Ereignisse gab es von Anfang an ein Journal. Foerderplaene
und Gespraechsprotokolle liessen sich spurlos aendern - gerade bei einem
Protokoll, auf das sich spaeter jemand beruft, ist das unschoen.

Geprueft wird, dass jede Aenderung eine Spur hinterlaesst, dass diese Spur den
vorherigen Stand nennt, und dass das Journal mit seinem Gegenstand verschwindet
statt als verwaiste Zeile zurueckzubleiben.
"""

import os
import re
import shutil
import tempfile
import unittest
from datetime import date, datetime

from werkzeug.security import generate_password_hash

from app import create_app
from change_log import describe, describe_creation, format_value, snapshot
from extensions import db
from models import (
    Elternkontakt,
    ElternkontaktLog,
    Foerdergrundlage,
    Foerderinhalt,
    Foerderplan,
    FoerderplanLog,
    Schueler,
    User,
    UserKlassenzuordnung,
)

CSRF_RE = re.compile(r'name="_csrf_token"\s+value="([^"]+)"')


class ChangeLogHelpersTestCase(unittest.TestCase):
    """Die Beschreibung fuer sich - ohne Datenbank."""

    FELDER = (('titel', 'Titel'), ('status', 'Status'))

    class Objekt:
        def __init__(self, titel=None, status=None):
            self.titel = titel
            self.status = status

    def test_format_value_handles_empty(self):
        self.assertEqual('leer', format_value(None))
        self.assertEqual('leer', format_value(''))
        self.assertEqual('leer', format_value('   '))

    def test_format_value_collapses_whitespace(self):
        self.assertEqual('a b c', format_value('a\n  b\t c'))

    def test_format_value_shortens_long_text(self):
        lang = 'x' * 500
        gekuerzt = format_value(lang)
        self.assertEqual(180, len(gekuerzt))
        self.assertTrue(gekuerzt.endswith('…'))

    def test_format_value_formats_dates(self):
        self.assertEqual('04.07.2018', format_value(date(2018, 7, 4)))
        self.assertEqual('04.07.2018', format_value(datetime(2018, 7, 4, 9, 30)))

    def test_describe_reports_old_and_new(self):
        vorher = snapshot(self.Objekt(titel='Alt', status='aktiv'), self.FELDER)
        nachher = snapshot(self.Objekt(titel='Neu', status='aktiv'), self.FELDER)
        self.assertEqual('Titel: Alt → Neu', describe(vorher, nachher, self.FELDER))

    def test_describe_is_empty_when_nothing_changed(self):
        zustand = snapshot(self.Objekt(titel='Gleich', status='aktiv'), self.FELDER)
        self.assertEqual('', describe(zustand, dict(zustand), self.FELDER))

    def test_describe_lists_several_changes(self):
        vorher = snapshot(self.Objekt(titel='A', status='aktiv'), self.FELDER)
        nachher = snapshot(self.Objekt(titel='B', status='beendet'), self.FELDER)
        beschreibung = describe(vorher, nachher, self.FELDER)
        self.assertIn('Titel: A → B', beschreibung)
        self.assertIn('Status: aktiv → beendet', beschreibung)

    def test_describe_creation_skips_empty_fields(self):
        beschreibung = describe_creation(self.Objekt(titel='Nur Titel'), self.FELDER)
        self.assertEqual('Titel: Nur Titel', beschreibung)


class ChangeLogRoutesTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='km_changelog_test_')
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
            lehrkraft = User(
                username='lehrkraft', password_hash=generate_password_hash('lehrpass'),
                role='teacher', vorname='Rita', nachname='Rektor',
            )
            db.session.add(lehrkraft)
            db.session.flush()
            # Ohne Klassenzuordnung verwehrt die Foerderplanung den Zugriff (403).
            db.session.add(UserKlassenzuordnung(
                user_id=lehrkraft.id, klasse='3a', rolle='klassenleitung',
            ))
            kind = Schueler(vorname='Anna', nachname='Abt', klasse='3a')
            db.session.add(kind)
            db.session.flush()
            # Ohne Grundlagenblatt leitet die Foerderplanung erst dorthin um.
            db.session.add(Foerdergrundlage(
                schueler_id=kind.id, besondere_staerken='ausdauernd',
            ))
            db.session.commit()
            self.kind_id = kind.id
        self._login()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _login(self):
        page = self.client.get('/login')
        token = CSRF_RE.search(page.get_data(as_text=True)).group(1)
        self.assertEqual(302, self.client.post('/login', data={
            'username': 'lehrkraft', 'password': 'lehrpass', '_csrf_token': token,
        }).status_code)

    def _token(self, pfad):
        page = self.client.get(pfad)
        return CSRF_RE.search(page.get_data(as_text=True)).group(1)

    # ------------------------------------------------------------------
    # Foerderplaene
    # ------------------------------------------------------------------

    def _create_plan(self, titel='Konzentration', ziel='Länger bei einer Sache bleiben'):
        pfad = f'/foerderplan/neu/{self.kind_id}'
        token = self._token(pfad)
        self.client.post(pfad, data={
            'titel': titel,
            'foerderziel': [ziel],
            'ist_zustand': ['bricht ab'],
            'soll_zustand': ['bleibt dabei'],
            'massnahmen': ['Sanduhr'],
            '_csrf_token': token,
        }, follow_redirects=True)
        with self.app.app_context():
            return Foerderplan.query.filter_by(titel=titel).one().id

    def test_creating_a_plan_is_logged(self):
        plan_id = self._create_plan()
        with self.app.app_context():
            eintraege = FoerderplanLog.query.filter_by(plan_id=plan_id).all()
            self.assertEqual(1, len(eintraege))
            self.assertEqual('created', eintraege[0].action)
            self.assertIn('Konzentration', eintraege[0].details)
            self.assertIn('Länger bei einer Sache bleiben', eintraege[0].details)

    def test_the_log_names_the_author(self):
        plan_id = self._create_plan()
        with self.app.app_context():
            eintrag = FoerderplanLog.query.filter_by(plan_id=plan_id).one()
            self.assertIsNotNone(eintrag.user)
            self.assertEqual('Rita Rektor', eintrag.user.display_name)
            self.assertIsNotNone(eintrag.created_at)

    def test_editing_a_plan_records_the_previous_state(self):
        plan_id = self._create_plan()
        pfad = f'/foerderplan/edit/{plan_id}'
        token = self._token(pfad)
        self.client.post(pfad, data={
            'titel': 'Konzentration überarbeitet',
            'status': 'aktiv',
            'foerderziel': ['Länger bei einer Sache bleiben'],
            'ist_zustand': ['bricht ab'],
            'soll_zustand': ['bleibt dabei'],
            'massnahmen': ['Sanduhr'],
            '_csrf_token': token,
        }, follow_redirects=True)

        with self.app.app_context():
            aenderung = FoerderplanLog.query.filter_by(
                plan_id=plan_id, action='updated',
            ).one()
            self.assertIn('Konzentration → Konzentration überarbeitet', aenderung.details)

    def test_changed_foerderbereiche_are_recorded(self):
        plan_id = self._create_plan()
        pfad = f'/foerderplan/edit/{plan_id}'
        token = self._token(pfad)
        self.client.post(pfad, data={
            'titel': 'Konzentration',
            'status': 'aktiv',
            'foerderziel': ['Ganz neues Ziel'],
            'ist_zustand': [''],
            'soll_zustand': [''],
            'massnahmen': [''],
            '_csrf_token': token,
        }, follow_redirects=True)

        with self.app.app_context():
            aenderung = FoerderplanLog.query.filter_by(
                plan_id=plan_id, action='updated',
            ).one()
            self.assertIn('Förderbereiche', aenderung.details)
            self.assertIn('Ganz neues Ziel', aenderung.details)

    def test_saving_without_a_change_writes_no_entry(self):
        """Ein Journal voller Leereintraege waere unbrauchbar."""
        plan_id = self._create_plan()
        pfad = f'/foerderplan/edit/{plan_id}'
        token = self._token(pfad)
        self.client.post(pfad, data={
            'titel': 'Konzentration',
            'status': 'aktiv',
            'foerderziel': ['Länger bei einer Sache bleiben'],
            'ist_zustand': ['bricht ab'],
            'soll_zustand': ['bleibt dabei'],
            'massnahmen': ['Sanduhr'],
            '_csrf_token': token,
        }, follow_redirects=True)

        with self.app.app_context():
            self.assertEqual(
                0, FoerderplanLog.query.filter_by(plan_id=plan_id, action='updated').count(),
            )

    def test_evaluation_is_logged(self):
        plan_id = self._create_plan()
        with self.app.app_context():
            inhalt_id = Foerderinhalt.query.filter_by(plan_id=plan_id).first().id

        pfad = f'/foerderplan/evaluate/{plan_id}'
        token = self._token(pfad)
        self.client.post(pfad, data={
            'plan_status': 'beendet',
            f'status_{inhalt_id}': '2',
            f'evaluation_{inhalt_id}': 'Ziel erreicht',
            '_csrf_token': token,
        }, follow_redirects=True)

        with self.app.app_context():
            eintraege = FoerderplanLog.query.filter_by(
                plan_id=plan_id, action='evaluated',
            ).all()
            self.assertEqual(1, len(eintraege), 'Die Evaluation wurde nicht protokolliert')
            self.assertIn('bewertet', eintraege[0].details)

    def test_log_is_shown_in_the_plan_view(self):
        plan_id = self._create_plan()
        html = self.client.get(f'/foerderplan/view/{plan_id}').get_data(as_text=True)
        self.assertIn('Änderungsprotokoll', html)
        self.assertIn('Angelegt', html)
        self.assertIn('Rita Rektor', html)

    def test_deleting_a_plan_takes_its_log(self):
        """Sonst blieben verwaiste Journalzeilen zurueck."""
        plan_id = self._create_plan()
        with self.app.app_context():
            self.assertEqual(1, FoerderplanLog.query.filter_by(plan_id=plan_id).count())

        pfad = f'/foerderplan/delete/{plan_id}'
        token = self._token(f'/foerderplan/view/{plan_id}')
        self.client.post(pfad, data={'_csrf_token': token}, follow_redirects=True)

        with self.app.app_context():
            self.assertIsNone(db.session.get(Foerderplan, plan_id))
            self.assertEqual(0, FoerderplanLog.query.count())

    # ------------------------------------------------------------------
    # Elternkontakte
    # ------------------------------------------------------------------

    def _create_notiz(self, betreff='Telefonat', mitteilung='kurz gesprochen'):
        pfad = '/erfassen/elternkontakte/notiz'
        token = self._token(pfad)
        self.client.post(pfad, data={
            'schueler_id': str(self.kind_id),
            'datum': '2026-09-05T10:00',
            'kontaktform': 'Telefonat',
            'betreff': betreff,
            'mitteilung': mitteilung,
            '_csrf_token': token,
        }, follow_redirects=True)
        with self.app.app_context():
            return Elternkontakt.query.filter_by(betreff=betreff).one().id

    def test_creating_a_note_is_logged(self):
        kontakt_id = self._create_notiz()
        with self.app.app_context():
            eintrag = ElternkontaktLog.query.filter_by(kontakt_id=kontakt_id).one()
            self.assertEqual('created', eintrag.action)
            self.assertIn('Telefonat', eintrag.details)
            self.assertIn('kurz gesprochen', eintrag.details)

    def test_editing_a_note_records_the_previous_text(self):
        """Der Kern der Sache: was stand vorher da."""
        kontakt_id = self._create_notiz()
        pfad = f'/erfassen/elternkontakte/edit/{kontakt_id}'
        token = self._token(pfad)
        self.client.post(pfad, data={
            'schueler_id': str(self.kind_id),
            'datum': '2026-09-05T10:00',
            'kontaktform': 'Telefonat',
            'betreff': 'Telefonat',
            'mitteilung': 'ausführlich gesprochen',
            '_csrf_token': token,
        }, follow_redirects=True)

        with self.app.app_context():
            aenderung = ElternkontaktLog.query.filter_by(
                kontakt_id=kontakt_id, action='updated',
            ).one()
            self.assertIn('kurz gesprochen → ausführlich gesprochen', aenderung.details)

    def test_note_saved_unchanged_writes_no_entry(self):
        kontakt_id = self._create_notiz()
        # Die Anlage setzt das Datum selbst; fuer ein wirklich unveraendertes
        # Speichern muss der gespeicherte Stand zurueckgeschickt werden.
        with self.app.app_context():
            kontakt = db.session.get(Elternkontakt, kontakt_id)
            gespeichertes_datum = kontakt.datum.strftime('%Y-%m-%dT%H:%M')

        pfad = f'/erfassen/elternkontakte/edit/{kontakt_id}'
        token = self._token(pfad)
        self.client.post(pfad, data={
            'schueler_id': str(self.kind_id),
            'datum': gespeichertes_datum,
            'kontaktform': 'Telefonat',
            'betreff': 'Telefonat',
            'mitteilung': 'kurz gesprochen',
            '_csrf_token': token,
        }, follow_redirects=True)

        with self.app.app_context():
            self.assertEqual(0, ElternkontaktLog.query.filter_by(
                kontakt_id=kontakt_id, action='updated',
            ).count())

    def test_log_is_shown_in_the_contact_view(self):
        kontakt_id = self._create_notiz()
        html = self.client.get(
            f'/erfassen/elternkontakte/view/{kontakt_id}',
        ).get_data(as_text=True)
        self.assertIn('Änderungsprotokoll', html)
        self.assertIn('Angelegt', html)

    def test_deleting_a_contact_takes_its_log(self):
        kontakt_id = self._create_notiz()
        pfad = f'/erfassen/elternkontakte/delete/{kontakt_id}'
        token = self._token(f'/erfassen/elternkontakte/view/{kontakt_id}')
        self.client.post(pfad, data={'_csrf_token': token}, follow_redirects=True)

        with self.app.app_context():
            self.assertIsNone(db.session.get(Elternkontakt, kontakt_id))
            self.assertEqual(0, ElternkontaktLog.query.count())

    def test_deleting_a_student_takes_the_logs_too(self):
        """Die Loeschung eines Kindes darf keine Journalzeilen zuruecklassen."""
        self._create_notiz()
        self._create_plan()

        with self.app.app_context():
            self.assertEqual(1, ElternkontaktLog.query.count())
            self.assertEqual(1, FoerderplanLog.query.count())

            kind = db.session.get(Schueler, self.kind_id)
            db.session.delete(kind)
            db.session.commit()

            self.assertEqual(0, ElternkontaktLog.query.count())
            self.assertEqual(0, FoerderplanLog.query.count())


if __name__ == '__main__':
    unittest.main()
