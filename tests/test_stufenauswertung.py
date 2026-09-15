"""Tests fuer die Stufenauswertung (Durchschnitte, auffaellige Kinder, Export)."""

import os
import re
import shutil
import struct
import tempfile
import unittest
import zipfile
import zlib
from datetime import date
from io import BytesIO
from unittest import mock

from werkzeug.security import generate_password_hash

from app import create_app
from diagnostik import lege_vorbelegung_an, risikogrenzen, speichere_ergebnis
from extensions import db
from models import (
    DiagnostikTestform,
    DiagnostikVerfahren,
    Foerderangaben,
    Foerderplan,
    Schueler,
    SystemKonfiguration,
    User,
    UserKlassenzuordnung,
)
from stufenauswertung import erstelle_auswertung, legende

CSRF_RE = re.compile(r'name="_csrf_token"\s+value="([^"]+)"')


def png(breite=40, hoehe=20):
    def chunk(art, daten):
        return struct.pack('>I', len(daten)) + art + daten + struct.pack('>I', zlib.crc32(art + daten) & 0xffffffff)
    roh = b''.join(b'\x00' + b'\xff\x00\x00' * breite for _ in range(hoehe))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', breite, hoehe, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(roh)) + chunk(b'IEND', b''))


class StufenauswertungTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='km_stufenauswertung_')
        database_uri = os.environ.get('TEST_DATABASE_URL') or f"sqlite:///{self.tmpdir}/test.db"
        self.app = create_app({
            'TESTING': True, 'SECRET_KEY': 'test', 'SQLALCHEMY_DATABASE_URI': database_uri,
            'UPLOAD_FOLDER': os.path.join(self.tmpdir, 'uploads'),
            'PROTECTED_UPLOAD_FOLDER': os.path.join(self.tmpdir, 'protected_uploads'),
        })
        self.app.instance_path = os.path.join(self.tmpdir, 'instance')
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            admin = User(username='admin', password_hash=generate_password_hash('pass'), role='admin')
            klara = User(username='klara', password_hash=generate_password_hash('pass'), role='teacher')
            db.session.add_all([admin, klara, SystemKonfiguration(schuljahr='2025/2026')])
            db.session.flush()
            db.session.add(UserKlassenzuordnung(user_id=klara.id, klasse='3a', rolle='klassenleitung'))
            lege_vorbelegung_an()
            self.kinder = {}
            for vorname, klasse in (('Anna', '3a'), ('Ben', '3a'), ('Cem', '3b')):
                kind = Schueler(vorname=vorname, nachname='Test', klasse=klasse, jahrgang=3)
                db.session.add(kind)
                db.session.flush()
                self.kinder[vorname] = kind
            db.session.commit()

            hsp2 = DiagnostikTestform.query.filter_by(name='HSP 2').one()
            hsp3 = DiagnostikTestform.query.filter_by(name='HSP 3').one()

            def werte(testform, **pr_nach_name):
                ids = {k.name: k.id for k in testform.kennwerte}
                return {(ids[name], art): zahl for name, eintrag in pr_nach_name.items() for art, zahl in eintrag.items()}

            gt, wr, ms = 'Graphemtreffer', 'Wörter richtig', 'Morphematische Strategie'
            # Vorjahr (Jahrgang 2) fuer die Tendenz
            speichere_ergebnis(self.kinder['Anna'], hsp2, '2024/2025', 'ende',
                               werte(hsp2, **{gt: {'prozentrang': 40}, wr: {'prozentrang': 35}, ms: {'prozentrang': 30}}), None,
                               datum=date(2025, 6, 20))
            # Aktuell
            speichere_ergebnis(self.kinder['Anna'], hsp3, '2025/2026', 'ende',
                               werte(hsp3, **{gt: {'rohwert': 150, 'prozentrang': 12, 't_wert': 38},
                                              wr: {'rohwert': 20, 'prozentrang': 38, 't_wert': 47},
                                              ms: {'rohwert': 5, 'prozentrang': 8, 't_wert': 36}}), None,
                               datum=date(2026, 6, 15))
            speichere_ergebnis(self.kinder['Ben'], hsp3, '2025/2026', 'ende',
                               werte(hsp3, **{gt: {'rohwert': 190, 'prozentrang': 70, 't_wert': 55},
                                              wr: {'rohwert': 30, 'prozentrang': 60, 't_wert': 53}}), None,
                               datum=date(2026, 6, 15))
            speichere_ergebnis(self.kinder['Cem'], hsp3, '2025/2026', 'ende',
                               werte(hsp3, **{gt: {'rohwert': 170, 'prozentrang': 20, 't_wert': 42},
                                              wr: {'rohwert': 25, 'prozentrang': 45, 't_wert': 49}}), None,
                               datum=date(2026, 6, 17))
            db.session.add(Foerderplan(schueler_id=self.kinder['Anna'].id, titel='Rechtschreiben',
                                       datum_erstellung=date(2025, 10, 1), status='aktiv'))
            db.session.add(Foerderangaben(schueler_id=self.kinder['Anna'].id, schuljahr='2025/2026',
                                          nachteilsausgleich=True, foerderschwerpunkt='Silben schwingen'))
            db.session.commit()
            self.kind_ids = {name: kind.id for name, kind in self.kinder.items()}
            self.hsp_id = DiagnostikVerfahren.query.filter_by(name='HSP').one().id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _login(self, username='admin'):
        page = self.client.get('/login')
        token = CSRF_RE.search(page.get_data(as_text=True)).group(1)
        self.client.post('/login', data={'username': username, 'password': 'pass', '_csrf_token': token})

    def _pfad(self, basis='/diagnostik/stufenauswertung'):
        return f'{basis}?verfahren_id={self.hsp_id}&schuljahr=2025%2F2026&halbjahr=ende&jahrgang=3'

    # ------------------------------------------------------------------
    # Logik
    # ------------------------------------------------------------------

    def test_class_averages_levels_and_list(self):
        with self.app.app_context():
            verfahren = db.session.get(DiagnostikVerfahren, self.hsp_id)
            a = erstelle_auswertung(verfahren, '2025/2026', 'ende', 3, risikogrenzen())
        self.assertEqual(3, a['anzahl'])
        self.assertEqual(['HSP 3'], a['testformen'])
        self.assertEqual((date(2026, 6, 15), date(2026, 6, 17)), (a['datum_von'], a['datum_bis']))
        self.assertEqual(['Ø RW GT', 'Ø T GT', 'Ø RW WR', 'Ø T WR'], [s.label for s in a['spalten']][:4])
        self.assertIn('Ø T MS', [s.label for s in a['spalten']])

        klasse_a = a['klassen'][0]
        self.assertEqual(('3a', 2), (klasse_a.klasse, klasse_a.anzahl))
        self.assertEqual(170.0, klasse_a.durchschnitte[0])          # Ø RW GT von 150 und 190
        self.assertEqual({'deutlich': 1, 'auffaellig': 0, 'beobachten': 0}, klasse_a.stufen)
        self.assertEqual('Jahrgang 3', a['gesamt'].klasse)
        self.assertEqual({'deutlich': 1, 'auffaellig': 0, 'beobachten': 1}, a['gesamt'].stufen)

        self.assertEqual(['Anna', 'Cem'], [z.schueler.vorname for z in a['liste']])
        anna = a['liste'][0]
        werte = {name: (pr, tendenz) for name, pr, _, tendenz in anna.werte}
        self.assertEqual((12, 'schlechter'), werte['Graphemtreffer'])   # 40 -> 12
        self.assertEqual((38, 'gleich'), werte['Wörter richtig'])       # 35 -> 38
        self.assertEqual((8, 'schlechter'), werte['Morphematische Strategie'])
        self.assertTrue(anna.foerderplan)
        self.assertTrue(anna.angaben.nachteilsausgleich)
        self.assertFalse(a['liste'][1].foerderplan)
        self.assertIn('MS = Morphematische Strategie', legende(a))

    def test_sls_report_lists_lesequotient_without_percentile(self):
        with self.app.app_context():
            sls = DiagnostikTestform.query.filter_by(name='SLS 1-4').one()
            leseleistung = sls.kennwerte[0].id
            for name, lq_vorjahr, lq in (('Anna', 100, 78), ('Ben', 95, 104), ('Cem', None, 88)):
                kind = db.session.get(Schueler, self.kind_ids[name])
                if lq_vorjahr:
                    speichere_ergebnis(kind, sls, '2024/2025', 'ende', {(leseleistung, 'lesequotient'): lq_vorjahr}, None,
                                       datum=date(2025, 6, 20))
                speichere_ergebnis(kind, sls, '2025/2026', 'ende',
                                   {(leseleistung, 'rohwert'): 20, (leseleistung, 'lesequotient'): lq}, None,
                                   datum=date(2026, 6, 15))
            db.session.commit()
            verfahren = sls.verfahren
            a = erstelle_auswertung(verfahren, '2025/2026', 'ende', 3, risikogrenzen())
            self.assertEqual(['Ø RW Lesen', 'Ø LQ Lesen'], [s.label for s in a['spalten']])
            self.assertEqual(['Anna', 'Cem'], [z.schueler.vorname for z in a['liste']])
            self.assertEqual([('Leseleistung', 78, False, 'schlechter')], a['liste'][0].werte)   # 100 -> 78
            self.assertEqual(['auffaellig', 'beobachten'], [z.stufe for z in a['liste']])
            self.assertEqual({'Leseleistung': 'LQ'}, a['spalten_kuerzel'])
            self.assertEqual('bis LQ 89', a['grenze_text'])
            self.assertIn('Lesequotient mindestens 10 Punkte', legende(a))
            self.assertNotIn('Prozentrang', legende(a))
            verfahren_id = verfahren.id

        self._login()
        html = self.client.get(f'/diagnostik/stufenauswertung?verfahren_id={verfahren_id}&schuljahr=2025%2F2026&halbjahr=ende&jahrgang=3').get_data(as_text=True)
        self.assertIn('Alle Kinder mit einer Risikostufe (bis LQ 89)', html)
        self.assertIn('(LQ)</span>', html)
        antwort = self.client.get(f'/diagnostik/stufenauswertung/export/odt?verfahren_id={verfahren_id}&schuljahr=2025%2F2026&halbjahr=ende&jahrgang=3')
        inhalt = self._odt_inhalt(antwort)['content.xml'].decode()
        for text in ('mindestens ein Teilbereich bis LQ 89', 'Lesen (LQ)', '78 ↓'):
            self.assertIn(text, inhalt)
        self.assertNotIn('bis PR', inhalt)

    def test_teacher_sees_only_own_classes(self):
        with self.app.app_context():
            verfahren = db.session.get(DiagnostikVerfahren, self.hsp_id)
            a = erstelle_auswertung(verfahren, '2025/2026', 'ende', 3, risikogrenzen(), erlaubte_klassen={'3a'})
        self.assertEqual(2, a['anzahl'])
        self.assertEqual(['3a'], [z.klasse for z in a['klassen']])

    # ------------------------------------------------------------------
    # Seite
    # ------------------------------------------------------------------

    def test_page_shows_report_and_teacher_restriction(self):
        self._login()
        html = self.client.get(self._pfad()).get_data(as_text=True)
        self.assertIn('Auswertung HSP', html)
        self.assertIn('Auffällige Kinder (2)', html)
        self.assertIn('Test, Cem', html)
        self.assertIn('↓', html)
        self.assertIn('Silben schwingen', html)

        self.client.post('/logout', data={'_csrf_token': CSRF_RE.search(html).group(1)})
        self._login('klara')
        html = self.client.get(self._pfad()).get_data(as_text=True)
        self.assertIn('nur Kinder Ihrer Klassen', html)
        self.assertNotIn('Test, Cem', html)

    def test_bulk_saving_of_foerderangaben_respects_access(self):
        self._login('klara')
        html = self.client.get(self._pfad()).get_data(as_text=True)
        anna, cem = self.kind_ids['Anna'], self.kind_ids['Cem']
        self.client.post(self._pfad(), data={
            '_csrf_token': CSRF_RE.search(html).group(1),
            'verfahren_id': self.hsp_id, 'schuljahr': '2025/2026', 'halbjahr': 'ende', 'jahrgang': '3',
            'kinder': [str(anna), str(cem)],
            f'foerderkurs_{anna}': '1', f'foerderschwerpunkt_{anna}': 'Ableiten',
            f'externe_foerderung_{cem}': '1',
        })
        with self.app.app_context():
            angaben = {f.schueler_id: f for f in Foerderangaben.query.all()}
            self.assertEqual((False, True, 'Ableiten'),
                             (angaben[anna].nachteilsausgleich, angaben[anna].foerderkurs, angaben[anna].foerderschwerpunkt))
            self.assertNotIn(cem, angaben, 'Kind einer fremden Klasse bearbeitet')

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def _odt_inhalt(self, antwort):
        with zipfile.ZipFile(BytesIO(antwort.data)) as archiv:
            return {name: archiv.read(name) for name in archiv.namelist()}

    def test_odt_export_contains_tables_and_is_landscape(self):
        self._login()
        antwort = self.client.get(self._pfad('/diagnostik/stufenauswertung/export/odt'))
        self.assertEqual(200, antwort.status_code)
        self.assertIn('Stufenauswertung_HSP_2025-2026_Ende_Jg3.odt', antwort.headers['Content-Disposition'])
        dateien = self._odt_inhalt(antwort)
        inhalt = dateien['content.xml'].decode()
        for text in ('Auswertung HSP', 'Durchschnittswerte', 'Test, Anna', 'Silben schwingen', '12 ↓', 'Ø RW GT', 'Legende:'):
            self.assertIn(text, inhalt)
        self.assertNotIn('Test, Ben', inhalt, 'unauffaelliges Kind in der Liste')
        self.assertIn('print-orientation="landscape"', dateien['styles.xml'].decode())
        self.assertFalse(any(name.startswith('Pictures/') for name in dateien))

    def test_logo_upload_is_embedded_and_validated(self):
        self._login()
        token = CSRF_RE.search(self.client.get('/admin/diagnostik').get_data(as_text=True)).group(1)
        antwort = self.client.post('/admin/diagnostik/logo', data={'_csrf_token': token, 'logo': (BytesIO(b'kein bild'), 'x.png')},
                                   content_type='multipart/form-data', follow_redirects=True)
        self.assertIn('PNG- oder JPEG-Datei', antwort.get_data(as_text=True))

        antwort = self.client.post('/admin/diagnostik/logo', data={'_csrf_token': token, 'logo': (BytesIO(png()), 'logo.png')},
                                   content_type='multipart/form-data', follow_redirects=True)
        self.assertIn('Logo ist hinterlegt', antwort.get_data(as_text=True))

        dateien = self._odt_inhalt(self.client.get(self._pfad('/diagnostik/stufenauswertung/export/odt')))
        self.assertIn('Pictures/bild1.png', dateien)
        self.assertIn('Pictures/bild1.png', dateien['META-INF/manifest.xml'].decode())
        self.assertIn('svg:width="4.40cm" svg:height="2.20cm"', dateien['content.xml'].decode())

        self.client.post('/admin/diagnostik/logo', data={'_csrf_token': token, 'aktion': 'entfernen'})
        dateien = self._odt_inhalt(self.client.get(self._pfad('/diagnostik/stufenauswertung/export/odt')))
        self.assertNotIn('Pictures/bild1.png', dateien)

    def test_pdf_export_uses_converter_and_handles_failure(self):
        self._login()
        with mock.patch('routes.diagnostik_routes.convert_odt_bytes_to_pdf', return_value=BytesIO(b'%PDF-1.4')):
            antwort = self.client.get(self._pfad('/diagnostik/stufenauswertung/export/pdf'))
        self.assertEqual('application/pdf', antwort.mimetype)
        with mock.patch('routes.diagnostik_routes.convert_odt_bytes_to_pdf', side_effect=RuntimeError('kein LibreOffice')):
            antwort = self.client.get(self._pfad('/diagnostik/stufenauswertung/export/pdf'), follow_redirects=True)
        self.assertIn('PDF-Export fehlgeschlagen', antwort.get_data(as_text=True))

    def test_empty_selection(self):
        self._login()
        pfad = f'/diagnostik/stufenauswertung?verfahren_id={self.hsp_id}&schuljahr=2025%2F2026&halbjahr=mitte&jahrgang=3'
        self.assertIn('keine Ergebnisse eingetragen', self.client.get(pfad).get_data(as_text=True))
        antwort = self.client.get(pfad.replace('/stufenauswertung?', '/stufenauswertung/export/odt?'), follow_redirects=True)
        self.assertIn('keine Ergebnisse', antwort.get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()
