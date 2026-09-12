"""Tests fuer das Zeugnismaterial.

Die Sammlung soll beim Schreiben von Zeugnissen auf dem Tisch liegen. Zwei
Eigenschaften entscheiden darueber, ob sie das leistet: sie muss auf das
laufende Schuljahr begrenzt sein - ein Zeugnis bewertet kein vergangenes - und
die Notizen der Lehrkraft muessen vollstaendig und in zeitlicher Folge
erscheinen, denn daraus entsteht der Text.
"""

import os
import re
import shutil
import tempfile
import unittest
import zipfile
from datetime import date, datetime
from io import BytesIO

from werkzeug.security import generate_password_hash

from app import create_app
from extensions import db
from models import (
    Beobachtung,
    Bogen,
    Foerdergrundlage,
    Foerderinhalt,
    Foerderplan,
    Item,
    Schueler,
    SystemKonfiguration,
    User,
    UserKlassenzuordnung,
)
from odt_export import build_odt_document
from report_material import (
    class_filename_stem,
    collect_material,
    filename_stem,
    material_blocks,
    students_in_class,
)

CSRF_RE = re.compile(r'name="_csrf_token"\s+value="([^"]+)"')
SOFFICE = shutil.which('soffice') or shutil.which('libreoffice')
PDFINFO = shutil.which('pdfinfo')


class ReportMaterialTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='km_material_test_')
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
            self._seed()
            db.session.commit()
        self._login()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _seed(self):
        admin = User(
            username='admin', password_hash=generate_password_hash('adminpass'), role='admin',
        )
        db.session.add(admin)
        db.session.flush()
        db.session.add(UserKlassenzuordnung(user_id=admin.id, klasse='3a', rolle='klassenleitung'))

        sozial = Bogen(titel='Sozialverhalten')
        mathe = Bogen(titel='Mathematik')
        db.session.add_all([sozial, mathe])
        db.session.flush()

        self.streit = Item(bogen_id=sozial.id, bereich='Konfliktverhalten', text='Löst Streit friedlich')
        self.konzentration = Item(bogen_id=sozial.id, bereich='Arbeitsverhalten', text='Arbeitet konzentriert')
        self.zahlen = Item(bogen_id=mathe.id, bereich='Zahlenraum', text='Zählt bis 20')
        db.session.add_all([self.streit, self.konzentration, self.zahlen])
        db.session.flush()

        self.anna = Schueler(vorname='Anna', nachname='Abt', klasse='3a')
        self.ben = Schueler(vorname='Ben', nachname='Bauer', klasse='3a')
        self.leer = Schueler(vorname='Ohne', nachname='Daten', klasse='3a')
        self.archiviert = Schueler(vorname='Emil', nachname='Ehemals', klasse='3a', is_active=False)
        db.session.add_all([self.anna, self.ben, self.leer, self.archiviert])
        db.session.flush()

        db.session.add(Foerdergrundlage(
            schueler_id=self.anna.id, besondere_staerken='sehr hilfsbereit',
        ))

        # Eine Entwicklung mit Notizen, chronologisch aufsteigend.
        for tag, wert, kommentar in [
            (2, 1, 'brauchte Hilfe beim Schlichten'),
            (10, 3, 'hat den Streit selbst geschlichtet'),
            (18, 4, 'vermittelt inzwischen bei anderen'),
            (20, 4, None),
        ]:
            db.session.add(Beobachtung(
                schueler_id=self.anna.id, item_id=self.streit.id, wert=wert,
                kommentar=kommentar, datum=datetime(2026, 9, tag),
                anlass='Pause' if tag == 10 else None,
            ))
        db.session.add(Beobachtung(
            schueler_id=self.anna.id, item_id=self.konzentration.id, wert=2,
            kommentar='bricht nach fünf Minuten ab', datum=datetime(2026, 9, 3),
        ))
        db.session.add(Beobachtung(
            schueler_id=self.anna.id, item_id=self.zahlen.id, wert=3,
            datum=datetime(2026, 9, 4),
        ))
        # Aus dem Vorjahr - gehoert nicht in ein Zeugnis dieses Jahres.
        db.session.add(Beobachtung(
            schueler_id=self.anna.id, item_id=self.streit.id, wert=4,
            kommentar='ALTER EINTRAG', datum=datetime(2025, 9, 1),
        ))

        plan = Foerderplan(
            schueler_id=self.anna.id, titel='Konflikte', datum_erstellung=date(2026, 9, 1),
        )
        alter_plan = Foerderplan(
            schueler_id=self.anna.id, titel='Alter Plan', datum_erstellung=date(2025, 9, 1),
        )
        db.session.add_all([plan, alter_plan])
        db.session.flush()
        db.session.add(Foerderinhalt(
            plan_id=plan.id, foerderziel='Streit ohne Hilfe klären',
            evaluation_text='deutliche Fortschritte',
        ))

    def _login(self):
        page = self.client.get('/login')
        token = CSRF_RE.search(page.get_data(as_text=True)).group(1)
        self.assertEqual(302, self.client.post('/login', data={
            'username': 'admin', 'password': 'adminpass', '_csrf_token': token,
        }).status_code)

    def _material(self, schueler_attr='anna'):
        schueler = db.session.merge(getattr(self, schueler_attr))
        return collect_material(schueler)

    # ------------------------------------------------------------------
    # Sammlung
    # ------------------------------------------------------------------

    def test_previous_school_year_is_excluded(self):
        with self.app.app_context():
            material = self._material()
            alle_notizen = [
                kommentar['text']
                for bogen in material['boegen']
                for bereich in bogen['bereiche']
                for kompetenz in bereich['kompetenzen']
                for kommentar in kompetenz['kommentare']
            ]
            self.assertNotIn('ALTER EINTRAG', alle_notizen)
            self.assertEqual(6, material['beobachtungen_anzahl'])

    def test_grouped_by_bogen_then_bereich(self):
        with self.app.app_context():
            material = self._material()
            self.assertEqual(
                ['Mathematik', 'Sozialverhalten'],
                [bogen['titel'] for bogen in material['boegen']],
            )
            sozial = next(b for b in material['boegen'] if b['titel'] == 'Sozialverhalten')
            self.assertEqual(
                ['Arbeitsverhalten', 'Konfliktverhalten'],
                [bereich['name'] for bereich in sozial['bereiche']],
            )

    def test_notes_are_collected_in_chronological_order(self):
        """Die Abfolge ist der Ertrag - daraus liest man die Entwicklung."""
        with self.app.app_context():
            material = self._material()
            sozial = next(b for b in material['boegen'] if b['titel'] == 'Sozialverhalten')
            konflikt = next(b for b in sozial['bereiche'] if b['name'] == 'Konfliktverhalten')
            kompetenz = konflikt['kompetenzen'][0]

            self.assertEqual(
                [
                    'brauchte Hilfe beim Schlichten',
                    'hat den Streit selbst geschlichtet',
                    'vermittelt inzwischen bei anderen',
                ],
                [kommentar['text'] for kommentar in kompetenz['kommentare']],
            )

    def test_entries_without_a_note_count_but_do_not_appear_as_notes(self):
        with self.app.app_context():
            material = self._material()
            sozial = next(b for b in material['boegen'] if b['titel'] == 'Sozialverhalten')
            konflikt = next(b for b in sozial['bereiche'] if b['name'] == 'Konfliktverhalten')
            kompetenz = konflikt['kompetenzen'][0]

            self.assertEqual(4, kompetenz['anzahl'], 'Alle vier Bewertungen zaehlen')
            self.assertEqual(3, len(kompetenz['kommentare']), 'Nur drei tragen eine Notiz')

    def test_note_keeps_symbol_and_anlass(self):
        with self.app.app_context():
            material = self._material()
            sozial = next(b for b in material['boegen'] if b['titel'] == 'Sozialverhalten')
            konflikt = next(b for b in sozial['bereiche'] if b['name'] == 'Konfliktverhalten')
            zweite = konflikt['kompetenzen'][0]['kommentare'][1]
            self.assertEqual('+', zweite['symbol'])
            self.assertEqual('Pause', zweite['anlass'])

    def test_averages_on_all_three_levels(self):
        with self.app.app_context():
            material = self._material()
            sozial = next(b for b in material['boegen'] if b['titel'] == 'Sozialverhalten')
            konflikt = next(b for b in sozial['bereiche'] if b['name'] == 'Konfliktverhalten')

            self.assertEqual(3.0, konflikt['kompetenzen'][0]['durchschnitt'])
            self.assertEqual(3.0, konflikt['durchschnitt'])
            # Sozialverhalten: vier Konflikt-Werte plus eine Zwei im Arbeitsverhalten
            self.assertEqual(2.8, sozial['durchschnitt'])

    def test_trend_is_attached(self):
        with self.app.app_context():
            material = self._material()
            sozial = next(b for b in material['boegen'] if b['titel'] == 'Sozialverhalten')
            konflikt = next(b for b in sozial['bereiche'] if b['name'] == 'Konfliktverhalten')
            self.assertEqual('verbessert', konflikt['kompetenzen'][0]['trend']['richtung'])

    def test_weakest_competency_comes_first(self):
        """Was Erwaehnung braucht, soll oben stehen."""
        with self.app.app_context():
            material = self._material()
            sozial = next(b for b in material['boegen'] if b['titel'] == 'Sozialverhalten')
            arbeitsverhalten = next(
                b for b in sozial['bereiche'] if b['name'] == 'Arbeitsverhalten'
            )
            # Innerhalb eines Bereichs mit mehreren Kompetenzen zuerst die schwaechste
            werte = [k['durchschnitt'] for k in arbeitsverhalten['kompetenzen']]
            self.assertEqual(sorted(werte), werte)

    def test_only_this_years_foerderplaene(self):
        with self.app.app_context():
            material = self._material()
            self.assertEqual(['Konflikte'], [p.titel for p in material['foerderplaene']])

    def test_child_without_observations(self):
        with self.app.app_context():
            material = self._material('leer')
            self.assertEqual([], material['boegen'])
            self.assertEqual(0, material['beobachtungen_anzahl'])
            self.assertEqual(0, material['kommentare_anzahl'])

    def test_class_list_skips_archived_children(self):
        with self.app.app_context():
            namen = [s.nachname for s in students_in_class('3a')]
            self.assertEqual(['Abt', 'Bauer', 'Daten'], namen)
            self.assertNotIn('Ehemals', namen)

    # ------------------------------------------------------------------
    # Dokument
    # ------------------------------------------------------------------

    def _odt_text(self, odt_bytes):
        import xml.etree.ElementTree as ET
        with zipfile.ZipFile(BytesIO(odt_bytes)) as archiv:
            wurzel = ET.fromstring(archiv.read('content.xml'))
        return ' '.join(wurzel.itertext())

    def test_document_contains_the_notes(self):
        with self.app.app_context():
            blocks = material_blocks(self._material(), '12.09.2026')
            text = self._odt_text(build_odt_document(blocks).getvalue())

        self.assertIn('Zeugnismaterial: Anna Abt', text)
        self.assertIn('sehr hilfsbereit', text)
        self.assertIn('hat den Streit selbst geschlichtet', text)
        self.assertIn('Streit ohne Hilfe klären', text)
        self.assertNotIn('ALTER EINTRAG', text)

    def test_page_break_only_between_children(self):
        with self.app.app_context():
            ohne = material_blocks(self._material(), '12.09.2026', mit_seitenumbruch=False)
            mit = material_blocks(self._material(), '12.09.2026', mit_seitenumbruch=True)

        self.assertNotEqual('pagebreak', ohne[0]['type'])
        self.assertEqual('pagebreak', mit[0]['type'])

    def test_filenames_are_safe(self):
        with self.app.app_context():
            anna = db.session.merge(self.anna)
            self.assertEqual(
                'Zeugnismaterial_Abt_Anna_2026-09-12',
                filename_stem(anna, date(2026, 9, 12)),
            )
        self.assertEqual(
            'Zeugnismaterial_Klasse_3a_2026-09-12',
            class_filename_stem('3a', date(2026, 9, 12)),
        )

    # ------------------------------------------------------------------
    # Routen
    # ------------------------------------------------------------------

    def test_view_requires_login(self):
        anonym = self.app.test_client()
        response = anonym.get('/report/zeugnismaterial')
        self.assertEqual(302, response.status_code)
        self.assertIn('/login', response.headers['Location'])

    def test_view_preselects_own_class_and_first_child(self):
        html = self.client.get('/report/zeugnismaterial').get_data(as_text=True)
        self.assertIn('value="3a" selected', html)
        self.assertIn('Anna', html)
        self.assertIn('hat den Streit selbst geschlichtet', html)

    def test_view_shows_every_child_of_the_class(self):
        html = self.client.get('/report/zeugnismaterial?klasse=3a').get_data(as_text=True)
        for name in ('Abt', 'Bauer', 'Daten'):
            self.assertIn(name, html)

    def test_odt_export_for_one_child(self):
        with self.app.app_context():
            anna_id = db.session.merge(self.anna).id

        response = self.client.get(f'/report/zeugnismaterial/export/odt?schueler_id={anna_id}')
        self.assertEqual(200, response.status_code)
        self.assertEqual(b'PK', response.data[:2])
        self.assertIn('Zeugnismaterial_Abt_Anna', response.headers['Content-Disposition'])
        self.assertIn('hat den Streit selbst geschlichtet', self._odt_text(response.data))

    def test_odt_export_for_a_whole_class_has_one_break_per_child(self):
        response = self.client.get('/report/zeugnismaterial/export/odt?klasse=3a')
        self.assertEqual(200, response.status_code)

        with zipfile.ZipFile(BytesIO(response.data)) as archiv:
            content = archiv.read('content.xml').decode()
        # Drei aktive Kinder, zwei Umbrueche - vor dem ersten keiner.
        self.assertEqual(2, content.count('text:style-name="Seitenumbruch"'))

        text = self._odt_text(response.data)
        for name in ('Anna Abt', 'Ben Bauer', 'Ohne Daten'):
            self.assertIn(name, text)

    def test_unknown_format_is_rejected(self):
        self.assertEqual(404, self.client.get(
            '/report/zeugnismaterial/export/docx?klasse=3a',
        ).status_code)

    def test_export_without_target_is_rejected(self):
        self.assertEqual(404, self.client.get(
            '/report/zeugnismaterial/export/odt',
        ).status_code)

    def test_export_of_an_empty_class_is_reported(self):
        with self.app.app_context():
            for schueler in Schueler.query.filter(Schueler.klasse == '3a').all():
                schueler.is_active = False
            db.session.commit()

        response = self.client.get(
            '/report/zeugnismaterial/export/odt?klasse=3a', follow_redirects=True,
        )
        self.assertIn('keine aktiven Kinder', response.get_data(as_text=True))

    def test_dashboard_links_to_the_material(self):
        self.assertIn('/report/zeugnismaterial', self.client.get('/').get_data(as_text=True))

    @unittest.skipUnless(SOFFICE, 'LibreOffice nicht installiert')
    def test_pdf_export_is_a_real_pdf(self):
        response = self.client.get('/report/zeugnismaterial/export/pdf?klasse=3a')
        self.assertEqual(200, response.status_code)
        self.assertEqual('application/pdf', response.headers['Content-Type'])
        self.assertEqual(b'%PDF', response.data[:4])
        self.assertGreater(len(response.data), 2000)

    @unittest.skipUnless(SOFFICE and PDFINFO, 'LibreOffice oder pdfinfo fehlt')
    def test_pdf_has_one_page_per_child(self):
        """Der Klassensatz soll sich blattweise verteilen lassen.

        Die Umbrueche selbst sind ueber das ODT geprueft; hier zaehlt, dass sie
        die Wandlung ueberstehen.
        """
        import subprocess

        response = self.client.get('/report/zeugnismaterial/export/pdf?klasse=3a')
        pfad = os.path.join(self.tmpdir, 'klassensatz.pdf')
        with open(pfad, 'wb') as datei:
            datei.write(response.data)

        ausgabe = subprocess.run(
            [PDFINFO, pfad], capture_output=True, text=True, timeout=60,
        ).stdout
        seiten = int(re.search(r'Pages:\s+(\d+)', ausgabe).group(1))
        self.assertEqual(3, seiten, f'3 aktive Kinder, aber {seiten} Seiten')


if __name__ == '__main__':
    unittest.main()
