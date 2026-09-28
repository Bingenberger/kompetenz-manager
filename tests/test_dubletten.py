"""Abgleich beim Import und das Zusammenführen doppelter Kinder."""

import os
import re
import shutil
import tempfile
import unittest
from datetime import date
from io import BytesIO

from openpyxl import Workbook
from werkzeug.security import generate_password_hash

from app import create_app
from extensions import db
from models import (
    Beobachtung,
    Bogen,
    Elternkontakt,
    Foerderangaben,
    Foerdergrundlage,
    Foerderplan,
    Item,
    Klasse,
    KlasseJahrgang,
    Schueler,
    SystemKonfiguration,
    User,
)
from schueler_abgleich import (
    AKTUALISIERT,
    ARCHIVIERT,
    DOPPELT_IN_DATEI,
    MEHRDEUTIG,
    NEU,
    VORHANDEN,
    dubletten_gruppen,
    fuehre_zusammen,
    plane_import,
)

CSRF_RE = re.compile(r'name="_csrf_token"\s+value="([^"]+)"')


def xlsx(zeilen):
    mappe = Workbook()
    blatt = mappe.active
    for zeile in zeilen:
        blatt.append(zeile)
    puffer = BytesIO()
    mappe.save(puffer)
    puffer.seek(0)
    return puffer


class DublettenTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='km_dubletten_')
        uri = os.environ.get('TEST_DATABASE_URL') or f"sqlite:///{self.tmpdir}/test.db"
        self.app = create_app({
            'TESTING': True, 'SECRET_KEY': 'test', 'SQLALCHEMY_DATABASE_URI': uri,
            'UPLOAD_FOLDER': os.path.join(self.tmpdir, 'uploads'),
            'PROTECTED_UPLOAD_FOLDER': os.path.join(self.tmpdir, 'protected_uploads'),
        })
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            db.session.add_all([
                User(username='admin', password_hash=generate_password_hash('pass'), role='admin'),
                User(username='klara', password_hash=generate_password_hash('pass'), role='teacher'),
                SystemKonfiguration(schuljahr='2026/2027'),
            ])
            klasse = Klasse(name='3a')
            db.session.add(klasse)
            db.session.flush()
            db.session.add(KlasseJahrgang(klasse_id=klasse.id, jahrgang=3))
            db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _login(self, username='admin'):
        self.client = self.app.test_client()
        token = CSRF_RE.search(self.client.get('/login').get_data(as_text=True)).group(1)
        self.client.post('/login', data={'username': username, 'password': 'pass', '_csrf_token': token},
                         follow_redirects=True)
        return CSRF_RE.search(self.client.get('/').get_data(as_text=True)).group(1)

    def _kind(self, vorname='Anna', nachname='Berg', klasse='3a', geburtsdatum=None, aktiv=True):
        kind = Schueler(vorname=vorname, nachname=nachname, klasse=klasse, jahrgang=3,
                        geburtsdatum=geburtsdatum, is_active=aktiv)
        db.session.add(kind)
        db.session.flush()
        return kind

    def _importiere(self, token, zeilen, **optionen):
        """Datei hochladen, Vorschau holen, Plan übernehmen."""
        daten = {'_csrf_token': token, 'file': (xlsx(zeilen), 'klasse.xlsx')}
        vorschau = self.client.post('/import/schueler', data=daten,
                                    content_type='multipart/form-data')
        html = vorschau.get_data(as_text=True)
        plan = re.search(r'name="plan" value="([^"]*)"', html)
        self.assertIsNotNone(plan, 'Vorschau ohne Plan')
        import html as html_modul
        formular = {'_csrf_token': token, 'aktion': 'uebernehmen',
                    'plan': html_modul.unescape(plan.group(1))}
        formular.update(optionen)
        return self.client.post('/import/schueler', data=formular, follow_redirects=True), html

    KOPF = ['Vorname', 'Nachname', 'Klasse', 'Geburtsdatum']

    # ------------------------------------------------------------------ Import

    def test_second_import_of_the_same_list_creates_nobody(self):
        token = self._login()
        zeilen = [self.KOPF,
                  ['Anna', 'Berg', '3a', '04.07.2018'],
                  ['Ben', 'Klein', '3a', '11.09.2018']]
        self._importiere(token, zeilen)
        with self.app.app_context():
            self.assertEqual(2, Schueler.query.count())

        antwort, vorschau = self._importiere(token, zeilen)
        self.assertIn('bereits vorhanden', vorschau)
        with self.app.app_context():
            self.assertEqual(2, Schueler.query.count(), 'zweiter Import hat Dubletten angelegt')
        self.assertIn('0 neu angelegt', antwort.get_data(as_text=True))

    def test_changed_class_is_updated_instead_of_duplicated(self):
        token = self._login()
        self._importiere(token, [self.KOPF, ['Anna', 'Berg', '3a', '04.07.2018']])
        antwort, vorschau = self._importiere(token, [self.KOPF, ['Anna', 'Berg', '3b', '04.07.2018']],
                                             aktualisieren='1')
        self.assertIn('wird aktualisiert', vorschau)
        with self.app.app_context():
            kind = Schueler.query.one()
            self.assertEqual('3b', kind.klasse)

    def test_same_name_different_birthday_is_a_second_child(self):
        token = self._login()
        self._importiere(token, [self.KOPF, ['Lena', 'Schmidt', '3a', '04.07.2018']])
        self._importiere(token, [self.KOPF, ['Lena', 'Schmidt', '3a', '19.02.2018']])
        with self.app.app_context():
            self.assertEqual(2, Schueler.query.count())

    def test_teachers_may_not_import(self):
        token = self._login('klara')
        antwort = self.client.post('/import/schueler', data={
            '_csrf_token': token, 'file': (xlsx([self.KOPF, ['Anna', 'Berg', '3a', '']]), 'k.xlsx'),
        }, content_type='multipart/form-data', follow_redirects=True)
        self.assertIn('Zugriff verweigert', antwort.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(0, Schueler.query.count())

    def test_plan_marks_duplicates_in_file_and_in_stock(self):
        with self.app.app_context():
            self._kind('Anna', 'Berg')
            self._kind('Anna', 'Berg')  # Altbestand, schon doppelt
            db.session.commit()
            plan = plane_import([
                {'vorname': 'Anna', 'nachname': 'Berg', 'klasse': '3a', 'geburtsdatum': None},
                {'vorname': 'Ben', 'nachname': 'Klein', 'klasse': '3a', 'geburtsdatum': None},
                {'vorname': 'Ben', 'nachname': 'Klein', 'klasse': '3a', 'geburtsdatum': None},
                {'vorname': '', 'nachname': 'Ohnename', 'klasse': '3a', 'geburtsdatum': None},
            ])
            self.assertEqual([MEHRDEUTIG, NEU, DOPPELT_IN_DATEI, 'unvollstaendig'],
                             [eintrag['status'] for eintrag in plan])

    def test_archived_namesake_is_skipped_unless_asked(self):
        with self.app.app_context():
            self._kind('Tim', 'Sommer', aktiv=False)
            db.session.commit()
            plan = plane_import([{'vorname': 'Tim', 'nachname': 'Sommer', 'klasse': '3a',
                                  'geburtsdatum': None}])
            self.assertEqual(ARCHIVIERT, plan[0]['status'])

    # ------------------------------------------------------------------ Dubletten

    def test_groups_keep_the_oldest_and_respect_birthdays(self):
        with self.app.app_context():
            erste = self._kind('Anna', 'Berg', geburtsdatum=date(2018, 7, 4))
            zweite = self._kind('anna', ' Berg ', geburtsdatum=None)
            self._kind('Anna', 'Berg', geburtsdatum=date(2017, 1, 2))
            db.session.commit()
            gruppen = dubletten_gruppen()
            self.assertEqual(1, len(gruppen))
            self.assertEqual(erste.id, gruppen[0]['original'].id)
            self.assertEqual([zweite.id], [kind.id for kind in gruppen[0]['dubletten']])

    def test_merging_moves_the_data_and_resolves_conflicts(self):
        with self.app.app_context():
            bogen = Bogen(titel='Deutsch')
            db.session.add(bogen)
            db.session.flush()
            item = Item(bogen_id=bogen.id, bereich='Lesen', text='liest flüssig')
            db.session.add(item)
            db.session.flush()

            alt = self._kind('Anna', 'Berg')
            neu = self._kind('Anna', 'Berg')
            db.session.add_all([
                Beobachtung(schueler_id=alt.id, item_id=item.id, wert=2),
                Beobachtung(schueler_id=neu.id, item_id=item.id, wert=3),
                Foerderplan(schueler_id=neu.id, titel='Lesen', status='aktiv'),
                Elternkontakt(schueler_id=neu.id, eintrag_typ='notiz', betreff='Anruf'),
                Foerdergrundlage(schueler_id=alt.id, besondere_staerken='Erzählt gern'),
                Foerdergrundlage(schueler_id=neu.id, vorrangiger_foerderbedarf='Lesen'),
                Foerderangaben(schueler_id=alt.id, schuljahr='2026/2027', foerderkurs=True),
                Foerderangaben(schueler_id=neu.id, schuljahr='2026/2027', externe_foerderung=True),
            ])
            db.session.commit()
            alt_id, neu_id = alt.id, neu.id

            bericht = fuehre_zusammen(db.session.get(Schueler, alt_id), db.session.get(Schueler, neu_id))
            db.session.commit()

            self.assertEqual(2, Beobachtung.query.filter_by(schueler_id=alt_id).count())
            self.assertEqual(1, Foerderplan.query.filter_by(schueler_id=alt_id).count())
            self.assertEqual(1, Elternkontakt.query.filter_by(schueler_id=alt_id).count())
            # Das ältere Grundlagenblatt bleibt, sein leeres Feld wird ergänzt.
            blatt = Foerdergrundlage.query.filter_by(schueler_id=alt_id).one()
            self.assertEqual('Erzählt gern', blatt.besondere_staerken)
            self.assertEqual('Lesen', blatt.vorrangiger_foerderbedarf)
            # Förderangaben gibt es je Schuljahr nur einmal - die jüngere fällt weg.
            angaben = Foerderangaben.query.filter_by(schueler_id=alt_id).one()
            self.assertTrue(angaben.foerderkurs)
            self.assertFalse(angaben.externe_foerderung)
            self.assertEqual(0, Beobachtung.query.filter_by(schueler_id=neu_id).count())
            self.assertIn('Beobachtungen', bericht['verschoben'])
            self.assertIn('Förderangaben', bericht['verworfen'])

    def test_cleanup_page_merges_and_deletes_the_younger_entry(self):
        with self.app.app_context():
            bogen = Bogen(titel='Deutsch')
            db.session.add(bogen)
            db.session.flush()
            item = Item(bogen_id=bogen.id, bereich='Lesen', text='liest flüssig')
            db.session.add(item)
            db.session.flush()
            alt = self._kind('Anna', 'Berg')
            neu = self._kind('Anna', 'Berg')
            db.session.add(Beobachtung(schueler_id=neu.id, item_id=item.id, wert=1))
            db.session.commit()
            alt_id = alt.id

        token = self._login()
        seite = self.client.get('/admin/dubletten').get_data(as_text=True)
        self.assertIn('Berg, Anna', seite)

        antwort = self.client.post('/admin/dubletten', data={'_csrf_token': token, 'aktion': 'alle'},
                                   follow_redirects=True)
        self.assertIn('1 Dublette(n) zusammengeführt', antwort.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(1, Schueler.query.count())
            self.assertEqual(1, Beobachtung.query.filter_by(schueler_id=alt_id).count())


if __name__ == '__main__':
    unittest.main()
