"""Tests fuer Aufbewahrungsfristen und die Loeschung nach Ablauf.

Hier wird unwiderruflich geloescht - vollstaendige Foerderakten von Kindern.
Entsprechend liegt das Gewicht auf dem, was nicht passieren darf: dass ohne
eingetragene Frist etwas als faellig gilt, dass eine laufende Frist geloescht
wird, dass eine veraltete Formularauswahl durchschlaegt, oder dass eine
Lehrkraft ohne Adminrechte an die Funktion kommt.
"""

import os
import re
import shutil
import tempfile
import unittest
from datetime import date, datetime, timedelta

from werkzeug.security import generate_password_hash

from app import create_app
from extensions import db
from models import (
    Beobachtung,
    Bogen,
    Elternkontakt,
    Foerderplan,
    Item,
    Schueler,
    SystemKonfiguration,
    User,
)
from retention import (
    STATUS_FAELLIG,
    STATUS_LAEUFT,
    STATUS_UNBEKANNT,
    archived_students,
    due_date,
    overdue_ids,
    retention_years,
    summarize,
)

CSRF_RE = re.compile(r'name="_csrf_token"\s+value="([^"]+)"')

HEUTE = date(2026, 9, 12)


class RetentionCalculationTestCase(unittest.TestCase):
    """Die Rechnung fuer sich - ohne Datenbank."""

    def test_due_date_adds_years(self):
        self.assertEqual(date(2031, 7, 31), due_date(date(2026, 7, 31), 5))
        self.assertEqual(date(2036, 7, 31), due_date(date(2026, 7, 31), 10))

    def test_due_date_accepts_datetime(self):
        self.assertEqual(date(2031, 7, 31), due_date(datetime(2026, 7, 31, 14, 30), 5))

    def test_leap_day_does_not_vanish(self):
        """Sonst liefe die Frist eines am 29. Februar archivierten Kindes
        nur alle vier Jahre ab."""
        self.assertEqual(date(2029, 2, 28), due_date(date(2024, 2, 29), 5))

    def test_no_date_no_due_date(self):
        self.assertIsNone(due_date(None, 5))
        self.assertIsNone(due_date(date(2026, 7, 31), None))
        self.assertIsNone(due_date(date(2026, 7, 31), 0))

    def test_retention_years_treats_zero_and_none_as_off(self):
        class Konfiguration:
            def __init__(self, jahre):
                self.aufbewahrung_jahre = jahre

        self.assertEqual(5, retention_years(Konfiguration(5)))
        self.assertIsNone(retention_years(Konfiguration(0)))
        self.assertIsNone(retention_years(Konfiguration(None)))
        self.assertIsNone(retention_years(None))


class RetentionViewTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='km_retention_test_')
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
        db.session.add_all([
            User(username='admin', password_hash=generate_password_hash('adminpass'), role='admin'),
            User(username='lehrkraft', password_hash=generate_password_hash('lehrpass'), role='teacher'),
        ])
        self.config = SystemKonfiguration(schuljahr='2026/2027', aufbewahrung_jahre=5)
        db.session.add(self.config)

        bogen = Bogen(titel='Sozialverhalten')
        db.session.add(bogen)
        db.session.flush()
        item = Item(bogen_id=bogen.id, bereich='Konflikt', text='Löst Streit friedlich')
        db.session.add(item)
        db.session.flush()

        # Frist deutlich abgelaufen (vor 6 Jahren archiviert, Frist 5)
        self.abgelaufen = Schueler(
            vorname='Alt', nachname='Abgelaufen', klasse='4a', is_active=False,
            archived_at=datetime(2020, 7, 31),
        )
        # Frist laeuft noch (vor 2 Jahren archiviert)
        self.laeuft = Schueler(
            vorname='Neu', nachname='Laeuft', klasse='4b', is_active=False,
            archived_at=datetime(2024, 7, 31),
        )
        # Archiviert, aber ohne Datum
        self.ohne_datum = Schueler(
            vorname='Kein', nachname='Datum', klasse='4c', is_active=False, archived_at=None,
        )
        # Aktiv - darf nirgends auftauchen
        self.aktiv = Schueler(vorname='Aktiv', nachname='Kind', klasse='3a')
        db.session.add_all([self.abgelaufen, self.laeuft, self.ohne_datum, self.aktiv])
        db.session.flush()

        # Dem abgelaufenen Kind Daten geben, damit die Loeschung etwas zu tun hat.
        db.session.add(Beobachtung(
            schueler_id=self.abgelaufen.id, item_id=item.id, wert=3,
            kommentar='alte Notiz', datum=datetime(2020, 3, 1),
        ))
        db.session.add(Foerderplan(schueler_id=self.abgelaufen.id, titel='Alter Plan'))
        db.session.add(Elternkontakt(
            schueler_id=self.abgelaufen.id, eintrag_typ='notiz', betreff='alter Kontakt',
        ))

    def _login(self, username='admin', password='adminpass'):
        page = self.client.get('/login')
        token = CSRF_RE.search(page.get_data(as_text=True)).group(1)
        return self.client.post('/login', data={
            'username': username, 'password': password, '_csrf_token': token,
        })

    def _zeilen(self):
        config = SystemKonfiguration.query.first()
        return archived_students(config, heute=HEUTE)

    # ------------------------------------------------------------------
    # Einstufung
    # ------------------------------------------------------------------

    def test_only_archived_children_appear(self):
        with self.app.app_context():
            namen = [zeile['schueler'].nachname for zeile in self._zeilen()]
            self.assertNotIn('Kind', namen, 'Ein aktives Kind steht in der Fristenliste')
            self.assertEqual(3, len(namen))

    def test_statuses_are_assigned_correctly(self):
        with self.app.app_context():
            nach_name = {z['schueler'].nachname: z for z in self._zeilen()}
            self.assertEqual(STATUS_FAELLIG, nach_name['Abgelaufen']['status'])
            self.assertEqual(STATUS_LAEUFT, nach_name['Laeuft']['status'])
            self.assertEqual(STATUS_UNBEKANNT, nach_name['Datum']['status'])

    def test_overdue_first_in_the_list(self):
        with self.app.app_context():
            self.assertEqual('Abgelaufen', self._zeilen()[0]['schueler'].nachname)

    def test_due_date_and_overdue_days_are_reported(self):
        with self.app.app_context():
            zeile = next(z for z in self._zeilen() if z['schueler'].nachname == 'Abgelaufen')
            self.assertEqual(date(2025, 7, 31), zeile['faellig_am'])
            self.assertEqual((HEUTE - date(2025, 7, 31)).days, zeile['tage_ueberfaellig'])

    def test_without_a_configured_period_nothing_is_overdue(self):
        """Die Frist gibt das Landesrecht vor - ohne Eintrag meldet die
        Anwendung nichts als faellig."""
        with self.app.app_context():
            config = SystemKonfiguration.query.first()
            config.aufbewahrung_jahre = None
            db.session.commit()

            zeilen = archived_students(config, heute=HEUTE)
            self.assertEqual(set(), overdue_ids(zeilen))
            self.assertEqual(0, summarize(zeilen)[STATUS_FAELLIG])

    def test_summary_counts_every_status(self):
        with self.app.app_context():
            gezaehlt = summarize(self._zeilen())
            self.assertEqual(1, gezaehlt[STATUS_FAELLIG])
            self.assertEqual(1, gezaehlt[STATUS_LAEUFT])
            self.assertEqual(1, gezaehlt[STATUS_UNBEKANNT])
            self.assertEqual(3, gezaehlt['gesamt'])

    # ------------------------------------------------------------------
    # Ansicht
    # ------------------------------------------------------------------

    def test_view_requires_admin(self):
        self._login('lehrkraft', 'lehrpass')
        response = self.client.get('/admin/aufbewahrung', follow_redirects=True)
        self.assertIn('Zugriff verweigert', response.get_data(as_text=True))

    def test_view_lists_children_and_scope(self):
        self._login()
        html = self.client.get('/admin/aufbewahrung').get_data(as_text=True)
        self.assertIn('Abgelaufen', html)
        self.assertIn('Laeuft', html)
        self.assertIn('Frist abgelaufen', html)
        # Der Umfang der Daten steht dabei, damit klar ist, was fiele.
        self.assertIn('1 Beobachtungen', html)
        self.assertIn('1 Förderpläne', html)

    def test_only_overdue_rows_offer_a_checkbox(self):
        self._login()
        with self.app.app_context():
            abgelaufen_id = Schueler.query.filter_by(nachname='Abgelaufen').one().id
            laeuft_id = Schueler.query.filter_by(nachname='Laeuft').one().id

        html = self.client.get('/admin/aufbewahrung').get_data(as_text=True)
        self.assertIn(f'name="schueler_ids" value="{abgelaufen_id}"', html)
        self.assertNotIn(f'name="schueler_ids" value="{laeuft_id}"', html)

    def test_view_explains_a_missing_period(self):
        with self.app.app_context():
            SystemKonfiguration.query.first().aufbewahrung_jahre = None
            db.session.commit()

        self._login()
        html = self.client.get('/admin/aufbewahrung').get_data(as_text=True)
        self.assertIn('Keine Aufbewahrungsfrist eingetragen', html)

    # ------------------------------------------------------------------
    # Loeschung
    # ------------------------------------------------------------------

    def _delete(self, ids):
        page = self.client.get('/admin/aufbewahrung')
        token = CSRF_RE.search(page.get_data(as_text=True)).group(1)
        return self.client.post(
            '/admin/aufbewahrung/loeschen',
            data={'schueler_ids': [str(i) for i in ids], '_csrf_token': token},
            follow_redirects=True,
        )

    def test_deleting_an_overdue_record_removes_everything(self):
        self._login()
        with self.app.app_context():
            ziel_id = Schueler.query.filter_by(nachname='Abgelaufen').one().id

        response = self._delete([ziel_id])
        self.assertIn('endgültig gelöscht', response.get_data(as_text=True))

        with self.app.app_context():
            self.assertIsNone(db.session.get(Schueler, ziel_id))
            self.assertEqual(0, Beobachtung.query.filter_by(schueler_id=ziel_id).count())
            self.assertEqual(0, Foerderplan.query.filter_by(schueler_id=ziel_id).count())
            self.assertEqual(0, Elternkontakt.query.filter_by(schueler_id=ziel_id).count())

    def test_a_running_period_is_never_deleted(self):
        """Der gefaehrlichste Fall: eine manipulierte oder veraltete Auswahl."""
        self._login()
        with self.app.app_context():
            laeuft_id = Schueler.query.filter_by(nachname='Laeuft').one().id

        response = self._delete([laeuft_id])
        self.assertIn('übersprungen', response.get_data(as_text=True))

        with self.app.app_context():
            self.assertIsNotNone(db.session.get(Schueler, laeuft_id))

    def test_a_record_without_a_date_is_never_deleted(self):
        self._login()
        with self.app.app_context():
            ohne_id = Schueler.query.filter_by(nachname='Datum').one().id

        self._delete([ohne_id])
        with self.app.app_context():
            self.assertIsNotNone(db.session.get(Schueler, ohne_id))

    def test_an_active_child_is_never_deleted(self):
        self._login()
        with self.app.app_context():
            aktiv_id = Schueler.query.filter_by(nachname='Kind').one().id

        self._delete([aktiv_id])
        with self.app.app_context():
            self.assertIsNotNone(db.session.get(Schueler, aktiv_id))

    def test_mixed_selection_deletes_only_the_overdue_one(self):
        self._login()
        with self.app.app_context():
            abgelaufen_id = Schueler.query.filter_by(nachname='Abgelaufen').one().id
            laeuft_id = Schueler.query.filter_by(nachname='Laeuft').one().id

        self._delete([abgelaufen_id, laeuft_id])
        with self.app.app_context():
            self.assertIsNone(db.session.get(Schueler, abgelaufen_id))
            self.assertIsNotNone(db.session.get(Schueler, laeuft_id))

    def test_without_a_configured_period_nothing_can_be_deleted(self):
        with self.app.app_context():
            SystemKonfiguration.query.first().aufbewahrung_jahre = None
            db.session.commit()
            ziel_id = Schueler.query.filter_by(nachname='Abgelaufen').one().id

        self._login()
        self._delete([ziel_id])
        with self.app.app_context():
            self.assertIsNotNone(
                db.session.get(Schueler, ziel_id),
                'Ohne eingetragene Frist wurde trotzdem gelöscht',
            )

    def test_empty_selection_is_reported(self):
        self._login()
        response = self._delete([])
        self.assertIn('kein Datensatz ausgewählt', response.get_data(as_text=True))

    def test_deletion_requires_admin(self):
        self._login('lehrkraft', 'lehrpass')
        with self.app.app_context():
            ziel_id = Schueler.query.filter_by(nachname='Abgelaufen').one().id

        page = self.client.get('/')
        token = CSRF_RE.search(page.get_data(as_text=True)).group(1)
        self.client.post('/admin/aufbewahrung/loeschen', data={
            'schueler_ids': [str(ziel_id)], '_csrf_token': token,
        }, follow_redirects=True)

        with self.app.app_context():
            self.assertIsNotNone(
                db.session.get(Schueler, ziel_id),
                'Eine Lehrkraft konnte einen Datensatz löschen',
            )

    # ------------------------------------------------------------------
    # Einstellung
    # ------------------------------------------------------------------

    def test_period_can_be_configured(self):
        self._login()
        page = self.client.get('/admin/system-settings')
        token = CSRF_RE.search(page.get_data(as_text=True)).group(1)
        self.client.post('/admin/system-settings', data={
            'schuljahr': '2026/2027', 'aufbewahrung_jahre': '7', '_csrf_token': token,
        }, follow_redirects=True)

        with self.app.app_context():
            self.assertEqual(7, SystemKonfiguration.query.first().aufbewahrung_jahre)

    def test_period_can_be_cleared(self):
        self._login()
        page = self.client.get('/admin/system-settings')
        token = CSRF_RE.search(page.get_data(as_text=True)).group(1)
        self.client.post('/admin/system-settings', data={
            'schuljahr': '2026/2027', 'aufbewahrung_jahre': '', '_csrf_token': token,
        }, follow_redirects=True)

        with self.app.app_context():
            self.assertIsNone(SystemKonfiguration.query.first().aufbewahrung_jahre)

    def test_invalid_period_is_rejected(self):
        self._login()
        page = self.client.get('/admin/system-settings')
        token = CSRF_RE.search(page.get_data(as_text=True)).group(1)
        response = self.client.post('/admin/system-settings', data={
            'schuljahr': '2026/2027', 'aufbewahrung_jahre': 'fünf', '_csrf_token': token,
        })
        self.assertIn('zwischen 1 und 100', response.get_data(as_text=True))

        with self.app.app_context():
            self.assertEqual(5, SystemKonfiguration.query.first().aufbewahrung_jahre)


if __name__ == '__main__':
    unittest.main()
