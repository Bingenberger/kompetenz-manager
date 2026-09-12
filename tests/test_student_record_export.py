"""Tests fuer den Export der vollstaendigen Schuelerakte.

Der Export dient zwei Zwecken, die beide Vollstaendigkeit verlangen: der
Uebergabe beim Schulwechsel und der Auskunft nach Art. 15 DSGVO. Geprueft wird
deshalb vor allem, dass nichts fehlt - insbesondere nicht die Begrenzungen, die
die Ansicht in der Oberflaeche sinnvollerweise hat.

Das erzeugte ODT wird ausgepackt und sein content.xml geparst. Ein Dokument,
das kein Leser oeffnet, waere als Auskunft wertlos.
"""

import os
import re
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime
from io import BytesIO

from werkzeug.security import generate_password_hash

from app import create_app
from extensions import db
from models import (
    Beobachtung,
    Bogen,
    Elternberatung,
    Elternkontakt,
    ErziehungsEreignis,
    ErziehungsEreignisAnhang,
    ErziehungsEreignisBetroffenesKind,
    ErziehungsEreignisKategorie,
    ErziehungsEreignisKonsequenz,
    ErziehungsEreignisVorlage,
    ErziehungsKonsequenz,
    ErziehungsOrt,
    Foerdergrundlage,
    Foerderinhalt,
    Foerderplan,
    Item,
    Schueler,
    SystemKonfiguration,
    User,
    WorkPlan,
    WorkPlanTask,
    WorkPlanTaskEvaluation,
)
from odt_export import ODT_MIMETYPE, build_odt_document
from student_record import collect_record, filename_stem, record_blocks

CSRF_RE = re.compile(r'name="_csrf_token"\s+value="([^"]+)"')

SOFFICE = shutil.which('soffice') or shutil.which('libreoffice')


def odt_text(odt_bytes):
    """Der sichtbare Text des Dokuments, aus content.xml gelesen."""
    with zipfile.ZipFile(BytesIO(odt_bytes)) as archiv:
        content = archiv.read('content.xml')
    wurzel = ET.fromstring(content)
    return ' '.join(teil for teil in wurzel.itertext())


class StudentRecordExportTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='km_record_test_')
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
            # Ein gesetztes Schuljahr darf den Export nicht beschneiden.
            db.session.add(SystemKonfiguration(
                schuljahr='2026/2027', schuljahr_beginn=date(2026, 8, 1),
            ))
            self._seed()
            db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Aufbau
    # ------------------------------------------------------------------

    def _seed(self):
        self.admin = User(
            username='admin', password_hash=generate_password_hash('adminpass'),
            role='admin', vorname='Rita', nachname='Rektor',
        )
        db.session.add(self.admin)
        db.session.flush()

        kind = Schueler(
            vorname='Lena', nachname='Müller', klasse='3a', geburtsdatum=date(2018, 7, 4),
        )
        mitbetroffen = Schueler(vorname='Tom', nachname='Zeuge', klasse='3a')
        leeres_kind = Schueler(vorname='Ohne', nachname='Daten', klasse='4a')
        db.session.add_all([kind, mitbetroffen, leeres_kind])
        db.session.flush()
        self.kind_id = kind.id
        self.leer_id = leeres_kind.id

        db.session.add(Foerdergrundlage(
            schueler_id=kind.id,
            besondere_staerken='ausdauernd',
            vorrangiger_foerderbedarf='Konzentration',
        ))

        bogen = Bogen(titel='Sozialverhalten')
        db.session.add(bogen)
        db.session.flush()
        item = Item(bogen_id=bogen.id, bereich='Konflikt', text='Löst Streit friedlich')
        db.session.add(item)
        db.session.flush()
        self.item_id = item.id

        # 15 Beobachtungen: mehr als die 12, die die Ansicht in der Oberflaeche zeigt.
        for tag in range(1, 16):
            db.session.add(Beobachtung(
                schueler_id=kind.id, item_id=item.id, wert=(tag % 4) + 1,
                kommentar=f'Beobachtung Nummer {tag}', datum=datetime(2026, 9, tag),
            ))
        # Aus dem Vorjahr: gehoert in eine Auskunft, nicht in den Jahresbericht.
        db.session.add(Beobachtung(
            schueler_id=kind.id, item_id=item.id, wert=1,
            kommentar='Eintrag aus dem Vorjahr', datum=datetime(2025, 3, 1),
        ))
        # Sonderzeichen, die als XML zerbrechen wuerden.
        db.session.add(Beobachtung(
            schueler_id=kind.id, item_id=item.id, wert=2,
            kommentar='Klammer <wichtig> & "Anführung"', datum=datetime(2026, 9, 20),
        ))

        plan = Foerderplan(
            schueler_id=kind.id, titel='Konzentration', creator_user_id=self.admin.id,
            datum_erstellung=date(2026, 8, 20), status='aktiv',
        )
        db.session.add(plan)
        db.session.flush()
        db.session.add(Foerderinhalt(
            plan_id=plan.id, foerderziel='Länger bei einer Sache bleiben',
            ist_zustand='bricht nach fünf Minuten ab', massnahmen='Sanduhr',
        ))

        arbeitsplan = WorkPlan(
            student_id=kind.id, created_by_user_id=self.admin.id, status='aktiv',
            period_start=date(2026, 9, 1), period_end=date(2026, 9, 7),
            notes_for_child='Du schaffst das',
        )
        db.session.add(arbeitsplan)
        db.session.flush()
        aufgabe = WorkPlanTask(
            work_plan_id=arbeitsplan.id, title='Lesen üben',
            learning_area='Deutsch', instructions='Seite zwölf lesen',
        )
        db.session.add(aufgabe)
        db.session.flush()
        db.session.add(WorkPlanTaskEvaluation(
            task_id=aufgabe.id, rating='teilweise', comment='braucht Hilfe',
        ))

        db.session.add(Elternkontakt(
            schueler_id=kind.id, user_id=self.admin.id, eintrag_typ='protokoll',
            betreff='Elterngespräch', besprochenes='Konzentration',
            vereinbarungen_eltern='feste Lernzeit', datum=datetime(2026, 9, 5),
            naechster_termin=date(2026, 11, 1),
        ))
        db.session.add(Elternberatung(
            schueler_id=kind.id, user_id=self.admin.id, datum=date(2026, 9, 6),
            anlass='Beratung zur Konzentration',
        ))

        kategorie = ErziehungsEreignisKategorie(name='Konfliktverhalten')
        ort = ErziehungsOrt(name='Schulhof')
        konsequenz = ErziehungsKonsequenz(name='Gespräch geführt')
        db.session.add_all([kategorie, ort, konsequenz])
        db.session.flush()
        vorlage = ErziehungsEreignisVorlage(category_id=kategorie.id, name='Streit')
        db.session.add(vorlage)
        db.session.flush()
        ereignis = ErziehungsEreignis(
            student_id=kind.id, event_template_id=vorlage.id, ort_id=ort.id,
            beschreibung='Streit um den Ball', child_statement='war nicht ich',
            assigned_user_id=self.admin.id, datum=date(2026, 9, 3),
        )
        db.session.add(ereignis)
        db.session.flush()
        db.session.add(ErziehungsEreignisKonsequenz(
            event_id=ereignis.id, consequence_id=konsequenz.id,
        ))
        db.session.add(ErziehungsEreignisBetroffenesKind(
            event_id=ereignis.id, student_id=mitbetroffen.id,
        ))
        db.session.add(ErziehungsEreignisAnhang(
            event_id=ereignis.id, file_path='erziehung/a.pdf', original_name='Notiz.pdf',
        ))

    def _document(self, student_id):
        schueler = db.session.get(Schueler, student_id)
        record = collect_record(schueler)
        blocks = record_blocks(record, '12.09.2026')
        return build_odt_document(blocks).getvalue()

    def _login(self):
        page = self.client.get('/login')
        token = CSRF_RE.search(page.get_data(as_text=True)).group(1)
        self.assertEqual(302, self.client.post('/login', data={
            'username': 'admin', 'password': 'adminpass', '_csrf_token': token,
        }).status_code)

    # ------------------------------------------------------------------
    # Aufbau der ODT-Datei
    # ------------------------------------------------------------------

    def test_odt_is_a_readable_package(self):
        with self.app.app_context():
            odt = self._document(self.kind_id)

        with zipfile.ZipFile(BytesIO(odt)) as archiv:
            namen = archiv.namelist()
            self.assertEqual(
                'mimetype', namen[0],
                'mimetype muss der erste Eintrag sein, sonst wird das Format nicht erkannt',
            )
            self.assertEqual(
                zipfile.ZIP_STORED, archiv.getinfo('mimetype').compress_type,
                'mimetype muss unkomprimiert abgelegt sein',
            )
            self.assertEqual(ODT_MIMETYPE, archiv.read('mimetype').decode())
            for pflicht in ('META-INF/manifest.xml', 'content.xml', 'styles.xml'):
                self.assertIn(pflicht, namen)
            self.assertIsNone(archiv.testzip(), 'Archiv ist beschädigt')

    def test_content_and_styles_are_wellformed_xml(self):
        with self.app.app_context():
            odt = self._document(self.kind_id)

        with zipfile.ZipFile(BytesIO(odt)) as archiv:
            for teil in ('content.xml', 'styles.xml', 'META-INF/manifest.xml'):
                try:
                    ET.fromstring(archiv.read(teil))
                except ET.ParseError as fehler:
                    self.fail(f'{teil} ist kein gültiges XML: {fehler}')

    def test_special_characters_are_escaped_not_lost(self):
        with self.app.app_context():
            text = odt_text(self._document(self.kind_id))

        self.assertIn('Klammer <wichtig> & "Anführung"', text)

    def test_line_breaks_become_odf_breaks(self):
        odt = build_odt_document([
            {'type': 'paragraph', 'text': 'erste Zeile\nzweite Zeile'},
        ]).getvalue()
        with zipfile.ZipFile(BytesIO(odt)) as archiv:
            content = archiv.read('content.xml').decode()
        self.assertIn('<text:line-break/>', content)

    def test_unknown_block_type_is_rejected(self):
        with self.assertRaises(ValueError):
            build_odt_document([{'type': 'gibtsnicht'}])

    # ------------------------------------------------------------------
    # Vollstaendigkeit
    # ------------------------------------------------------------------

    def test_every_section_appears(self):
        with self.app.app_context():
            text = odt_text(self._document(self.kind_id))

        for abschnitt in (
            'Grunddaten', 'Fördergrundlage', 'Beobachtungen', 'Förderpläne',
            'Arbeitspläne', 'Elternkontakte', 'Elternberatungen',
            'Erzieherische Ereignisse', 'Hinweise zu diesem Auszug',
        ):
            self.assertIn(abschnitt, text, f'Abschnitt „{abschnitt}" fehlt')

    def test_all_observations_are_included_not_only_the_recent_ones(self):
        """Die Ansicht zeigt zwölf, der Auszug muss alle enthalten."""
        with self.app.app_context():
            text = odt_text(self._document(self.kind_id))

        for nummer in range(1, 16):
            self.assertIn(
                f'Beobachtung Nummer {nummer}', text,
                f'Beobachtung {nummer} fehlt im Auszug',
            )

    def test_previous_school_years_are_included(self):
        """Eine Auskunft umfasst den Bestand, nicht das laufende Schuljahr."""
        with self.app.app_context():
            text = odt_text(self._document(self.kind_id))

        self.assertIn('Eintrag aus dem Vorjahr', text)

    def test_content_of_every_module_is_present(self):
        with self.app.app_context():
            text = odt_text(self._document(self.kind_id))

        erwartet = [
            'Lena', 'Müller', '3a', '04.07.2018',          # Grunddaten
            'ausdauernd',                                    # Fördergrundlage
            'Löst Streit friedlich',                         # Kompetenz
            'Länger bei einer Sache bleiben', 'Sanduhr',     # Förderplan
            'Lesen üben', 'Seite zwölf lesen', 'braucht Hilfe',  # Arbeitsplan
            'Elterngespräch', 'feste Lernzeit', '01.11.2026',    # Elternkontakt
            'Beratung zur Konzentration',                    # Beratung
            'Streit um den Ball', 'war nicht ich',           # Ereignis
            'Gespräch geführt', 'Tom Zeuge', 'Notiz.pdf',    # Ereignis-Verknüpfungen
            'Rita Rektor',                                   # dokumentierende Lehrkraft
        ]
        fehlend = [wert for wert in erwartet if wert not in text]
        self.assertEqual([], fehlend, f'Im Auszug fehlen: {fehlend}')

    def test_observation_value_is_shown_as_symbol_and_number(self):
        with self.app.app_context():
            text = odt_text(self._document(self.kind_id))
        self.assertIn('(2)', text)
        self.assertIn('Skala 1 bis 4', text)

    def test_photo_and_attachment_are_noted_as_present(self):
        with self.app.app_context():
            beobachtung = Beobachtung.query.filter_by(schueler_id=self.kind_id).first()
            beobachtung.foto_pfad = 'irgendein-foto.jpg'
            db.session.commit()
            text = odt_text(self._document(self.kind_id))

        self.assertIn('Foto vorhanden', text)
        self.assertIn('Notiz.pdf', text)
        self.assertIn('nicht eingebettet', text, 'Der Hinweis zu Anhängen fehlt')

    def test_child_without_any_data_yields_a_usable_document(self):
        with self.app.app_context():
            text = odt_text(self._document(self.leer_id))

        self.assertIn('Ohne', text)
        self.assertIn('Keine Beobachtungen erfasst', text)
        self.assertIn('Keine Förderpläne angelegt', text)
        self.assertIn('Keine Arbeitspläne angelegt', text)
        self.assertIn('Keine Elternkontakte dokumentiert', text)
        self.assertIn('Keine Ereignisse dokumentiert', text)

    def test_archived_children_are_marked(self):
        with self.app.app_context():
            kind = db.session.get(Schueler, self.kind_id)
            kind.is_active = False
            kind.archived_at = datetime(2026, 7, 31)
            db.session.commit()
            text = odt_text(self._document(self.kind_id))

        self.assertIn('archiviert am 31.07.2026', text)

    # ------------------------------------------------------------------
    # Dateiname
    # ------------------------------------------------------------------

    def test_filename_is_safe_and_dated(self):
        """Umlaute bleiben erhalten, Werkzeug kodiert sie im Header.

        'ü'.isalnum() ist in Python True, der Umlaut ueberlebt die Bereinigung
        also - und der Content-Disposition-Header traegt zusaetzlich eine
        ASCII-Fassung fuer aeltere Clients.
        """
        with self.app.app_context():
            kind = db.session.get(Schueler, self.kind_id)
            self.assertEqual(
                'Schuelerakte_Müller_Lena_2026-09-12',
                filename_stem(kind, date(2026, 9, 12)),
            )

    def test_filename_replaces_characters_that_break_paths(self):
        with self.app.app_context():
            kind = Schueler(vorname='A/B', nachname='C:D E', klasse='1a')
            db.session.add(kind)
            db.session.commit()
            stem = filename_stem(kind, date(2026, 9, 12))
            for zeichen in '/:\\ ':
                self.assertNotIn(zeichen, stem)

    def test_filename_survives_missing_names(self):
        with self.app.app_context():
            kind = Schueler(vorname=None, nachname=None, klasse='1a')
            db.session.add(kind)
            db.session.commit()
            stem = filename_stem(kind, date(2026, 9, 12))
            self.assertTrue(stem.startswith('Schuelerakte_Kind_'))
            self.assertNotIn('/', stem)

    # ------------------------------------------------------------------
    # Routen
    # ------------------------------------------------------------------

    def test_export_requires_login(self):
        for pfad in ('odt', 'pdf'):
            response = self.client.get(f'/schuelerakte/export/{pfad}/{self.kind_id}')
            self.assertEqual(302, response.status_code)
            self.assertIn('/login', response.headers['Location'])

    def test_odt_route_delivers_a_download(self):
        self._login()
        response = self.client.get(f'/schuelerakte/export/odt/{self.kind_id}')
        self.assertEqual(200, response.status_code)
        self.assertEqual(ODT_MIMETYPE, response.headers['Content-Type'])
        self.assertIn('attachment', response.headers['Content-Disposition'])
        verfuegung = response.headers['Content-Disposition']
        # Werkzeug liefert beides: ASCII-Rueckfall und UTF-8-Fassung.
        self.assertIn('filename=Schuelerakte_Muller_Lena', verfuegung)
        self.assertIn("filename*=UTF-8''Schuelerakte_M%C3%BCller_Lena", verfuegung)
        self.assertEqual(b'PK', response.data[:2], 'Antwort ist kein ZIP-Paket')

    def test_unknown_student_yields_404(self):
        self._login()
        self.assertEqual(404, self.client.get('/schuelerakte/export/odt/999999').status_code)

    def test_record_page_offers_both_exports(self):
        self._login()
        html = self.client.get(f'/schuelerakte?schueler_id={self.kind_id}').get_data(as_text=True)
        self.assertIn(f'/schuelerakte/export/pdf/{self.kind_id}', html)
        self.assertIn(f'/schuelerakte/export/odt/{self.kind_id}', html)

    @unittest.skipUnless(SOFFICE, 'LibreOffice nicht installiert')
    def test_pdf_route_delivers_a_real_pdf(self):
        self._login()
        response = self.client.get(f'/schuelerakte/export/pdf/{self.kind_id}')
        self.assertEqual(200, response.status_code)
        self.assertEqual('application/pdf', response.headers['Content-Type'])
        self.assertEqual(b'%PDF', response.data[:4], 'Antwort beginnt nicht mit %PDF')
        self.assertGreater(len(response.data), 2000)


if __name__ == '__main__':
    unittest.main()
