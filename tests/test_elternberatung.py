"""Tests fuer die Inhalte des Elterngespraechs: Lernstand, Diagnostik, Ereignisse."""

import os
import re
import shutil
import tempfile
import unittest
from datetime import date, datetime

from werkzeug.security import generate_password_hash

from app import create_app
from diagnostik import lege_vorbelegung_an, speichere_ergebnis
from elternberatung import kompetenz_uebersicht
from extensions import db
from models import (
    Beobachtung,
    Bogen,
    DiagnostikTestform,
    Elternberatung,
    ErziehungsEreignis,
    ErziehungsEreignisKategorie,
    ErziehungsEreignisVorlage,
    ErziehungsOrt,
    Item,
    Schueler,
    SystemKonfiguration,
    User,
    UserKlassenzuordnung,
)

CSRF_RE = re.compile(r'name="_csrf_token"\s+value="([^"]+)"')


class ElternberatungTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='km_elternberatung_')
        database_uri = os.environ.get('TEST_DATABASE_URL') or f"sqlite:///{self.tmpdir}/test.db"
        self.app = create_app({
            'TESTING': True, 'SECRET_KEY': 'test', 'SQLALCHEMY_DATABASE_URI': database_uri,
            'UPLOAD_FOLDER': os.path.join(self.tmpdir, 'uploads'),
            'PROTECTED_UPLOAD_FOLDER': os.path.join(self.tmpdir, 'protected_uploads'),
        })
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            klara = User(username='klara', password_hash=generate_password_hash('pass'), role='teacher')
            db.session.add_all([klara, SystemKonfiguration(schuljahr='2026/2027', schuljahr_beginn=date(2026, 8, 1))])
            db.session.flush()
            db.session.add(UserKlassenzuordnung(user_id=klara.id, klasse='3a', rolle='klassenleitung'))
            kind = Schueler(vorname='Anna', nachname='Abt', klasse='3a', jahrgang=3)
            db.session.add(kind)
            lege_vorbelegung_an()
            db.session.flush()
            self.kind_id = kind.id

            bogen = Bogen(titel='Deutsch')
            db.session.add(bogen)
            db.session.flush()
            items = {
                'fluessig': Item(bogen_id=bogen.id, bereich='Lesen', text='Liest flüssig'),
                'sinn': Item(bogen_id=bogen.id, bereich='Lesen', text='Erfasst den Sinn'),
                'laut': Item(bogen_id=bogen.id, bereich='Schreiben', text='Schreibt lautgetreu'),
                'alt': Item(bogen_id=bogen.id, bereich='Schreiben', text='Nur im Vorjahr beobachtet'),
            }
            db.session.add_all(items.values())
            db.session.flush()
            beobachtungen = [
                (items['fluessig'], 4, datetime(2026, 9, 1)), (items['fluessig'], 4, datetime(2026, 9, 8)),
                (items['sinn'], 1, datetime(2026, 9, 1)), (items['sinn'], 3, datetime(2026, 9, 10)),  # verbessert
                (items['laut'], 2, datetime(2026, 9, 2)),
                (items['alt'], 4, datetime(2026, 3, 1)),  # vor Schuljahresbeginn
            ]
            for item, wert, datum in beobachtungen:
                db.session.add(Beobachtung(schueler_id=kind.id, item_id=item.id, wert=wert, datum=datum, anlass='Test'))

            testform = DiagnostikTestform.query.filter_by(name='HSP 3').one()
            gt = next(k.id for k in testform.kennwerte if k.name == 'Graphemtreffer')
            speichere_ergebnis(kind, testform, '2026/2027', 'mitte', {(gt, 'prozentrang'): 12, (gt, 'rohwert'): 150}, klara.id,
                               datum=date(2027, 1, 20), aktuelles_schuljahr='2026/2027')

            kat = ErziehungsEreignisKategorie(name='Hinweis', sort_order=1, is_active=True)
            db.session.add(kat)
            db.session.flush()
            vorlage = ErziehungsEreignisVorlage(category_id=kat.id, name='Streit auf dem Hof', sort_order=1, is_active=True)
            ort = ErziehungsOrt(name='Hof', sort_order=1, is_active=True)
            db.session.add_all([vorlage, ort])
            db.session.flush()
            db.session.add(ErziehungsEreignis(student_id=kind.id, event_template_id=vorlage.id, ort_id=ort.id,
                                              datum=date(2026, 9, 5), beschreibung='Rangelei in der Pause', status='offen'))
            db.session.add(ErziehungsEreignis(student_id=kind.id, event_template_id=vorlage.id, ort_id=ort.id,
                                              datum=date(2026, 5, 5), beschreibung='Altes Ereignis', status='abgeschlossen'))
            db.session.commit()
        page = self.client.get('/login')
        token = CSRF_RE.search(page.get_data(as_text=True)).group(1)
        self.client.post('/login', data={'username': 'klara', 'password': 'pass', '_csrf_token': token})

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------

    def test_overview_groups_by_area_and_sorts_into_columns(self):
        from routes.erfassung_routes import _build_bogen_entries_for_student
        with self.app.app_context():
            uebersicht = kompetenz_uebersicht(_build_bogen_entries_for_student(self.kind_id)['bogen_rows'])
        self.assertEqual(1, len(uebersicht))
        deutsch = uebersicht[0]
        self.assertEqual('Deutsch', deutsch.bogen.titel if hasattr(deutsch, 'bogen') else deutsch['bogen'].titel)
        bereiche = {b['name']: b for b in deutsch['bereiche']}
        self.assertEqual(['Lesen', 'Schreiben'], sorted(bereiche))

        lesen = bereiche['Lesen']
        self.assertEqual(3.0, lesen['mittel'])           # (4 + 2) / 2
        self.assertEqual('sicher', lesen['level'])
        self.assertEqual(['Liest flüssig'], [i['item'].text for i in lesen['staerken']])
        self.assertEqual(['Erfasst den Sinn'], [i['item'].text for i in lesen['potenzial']])
        self.assertEqual('verbessert', lesen['potenzial'][0]['trend']['richtung'])

        schreiben = bereiche['Schreiben']
        self.assertEqual(['Schreibt lautgetreu'], [i['item'].text for i in schreiben['potenzial']],
                         'Beobachtung aus dem Vorjahr zaehlt mit')
        self.assertEqual('teils', schreiben['level'])
        self.assertEqual((3, 1, 2), (deutsch['anzahl_items'], deutsch['anzahl_staerken'], deutsch['anzahl_potenzial']))

    def test_preparation_page_shows_all_sections(self):
        html = self.client.get(f'/erfassen/elternberatung?schueler_id={self.kind_id}').get_data(as_text=True)
        self.assertIn('Wo Anna steht', html)
        self.assertIn('Das klappt gut', html)
        self.assertIn('Daran arbeiten wir', html)
        self.assertIn('Liest flüssig', html)
        self.assertIn('überwiegend sicher', html)
        # Diagnostik mit Diagramm und Einordnung
        self.assertIn('Standardisierte Tests', html)
        self.assertIn('Rechtschreiben', html)
        self.assertIn('auffällig', html)
        self.assertIn('<svg', html)
        # Ereignisse nur aus dem laufenden Schuljahr
        self.assertIn('Ereignisse im Schuljahr', html)
        self.assertIn('Rangelei in der Pause', html)
        self.assertNotIn('Altes Ereignis', html)
        # Detailtabellen und Foerderplanung bleiben
        self.assertIn('Alle Einträge im Detail', html)
        self.assertIn('Aktiver Förderplan', html)

    def test_saved_consultation_shows_the_same_content(self):
        pfad = f'/erfassen/elternberatung?schueler_id={self.kind_id}'
        token = CSRF_RE.search(self.client.get(pfad).get_data(as_text=True)).group(1)
        antwort = self.client.post(pfad, data={
            '_csrf_token': token, 'schueler_id': str(self.kind_id), 'datum': '2026-11-20',
            'anlass': 'Elternsprechtag', 'vereinbarungen': 'Täglich 10 Minuten lesen',
        })
        self.assertEqual(302, antwort.status_code)
        with self.app.app_context():
            beratung_id = Elternberatung.query.one().id
        html = self.client.get(f'/erfassen/elternberatung/view/{beratung_id}').get_data(as_text=True)
        self.assertIn('Täglich 10 Minuten lesen', html)
        for text in ('Wo Anna steht', 'Standardisierte Tests', 'Ereignisse im Schuljahr', 'Alle Einträge im Detail'):
            self.assertIn(text, html)
        # Eindeutige IDs: Vorlage nutzt das Praefix "view"
        self.assertIn('id="viewBogenAccordion"', html)

    def test_page_without_child_has_no_sections(self):
        html = self.client.get('/erfassen/elternberatung?schueler_id=').get_data(as_text=True)
        self.assertNotIn('Wo ', html.split('Elterngespräch vorbereiten')[-1][:200])
        self.assertNotIn('Standardisierte Tests', html)


if __name__ == '__main__':
    unittest.main()
