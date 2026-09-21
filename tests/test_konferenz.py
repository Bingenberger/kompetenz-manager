"""Förderkonferenz: Rechte, feldweises Speichern, Phasen, Abschluss, Aufgaben."""

import os
import re
import shutil
import tempfile
import unittest
import zipfile
from datetime import date, timedelta
from io import BytesIO

from werkzeug.security import generate_password_hash

from app import create_app
from diagnostik import lege_vorbelegung_an, speichere_ergebnis
from extensions import db
from konferenz import erstelle_konferenz, offene_beschluesse, pruefe_vollstaendigkeit
from models import (
    Beobachtung,
    Bogen,
    DiagnostikTestform,
    Foerderkonferenz,
    FoerderkonferenzKind,
    FoerderkonferenzLog,
    Item,
    Schueler,
    SystemKonfiguration,
    User,
    UserKlassenzuordnung,
)
from student_record import collect_record
from time_utils import utc_now

CSRF_RE = re.compile(r'name="_csrf_token"\s+value="([^"]+)"')


class KonferenzTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='km_konferenz_')
        database_uri = os.environ.get('TEST_DATABASE_URL') or f"sqlite:///{self.tmpdir}/test.db"
        self.app = create_app({
            'TESTING': True, 'SECRET_KEY': 'test', 'SQLALCHEMY_DATABASE_URI': database_uri,
            'UPLOAD_FOLDER': os.path.join(self.tmpdir, 'uploads'),
            'PROTECTED_UPLOAD_FOLDER': os.path.join(self.tmpdir, 'protected_uploads'),
        })
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            nutzer = {}
            for name, rolle in (('leitung', 'schulleitung'), ('klara', 'teacher'), ('kai', 'teacher'),
                                ('fachl', 'teacher'), ('foerder', 'foerderpaedagogik')):
                nutzer[name] = User(username=name, password_hash=generate_password_hash('pass'),
                                    role=rolle, vorname=name.capitalize(), nachname='Test')
            db.session.add_all(list(nutzer.values()) + [SystemKonfiguration(schuljahr='2026/2027')])
            db.session.flush()
            db.session.add_all([
                UserKlassenzuordnung(user_id=nutzer['klara'].id, klasse='3a', rolle='klassenleitung'),
                UserKlassenzuordnung(user_id=nutzer['kai'].id, klasse='3b', rolle='klassenleitung'),
                UserKlassenzuordnung(user_id=nutzer['fachl'].id, klasse='3a', rolle='fach'),
            ])
            lege_vorbelegung_an()
            kinder = {}
            for vorname, klasse, jahrgang in (('Anna', '3a', 3), ('Ben', '3a', 3), ('Cem', '3b', 3), ('Dila', '1a', 1)):
                kinder[vorname] = Schueler(vorname=vorname, nachname=f'{klasse.upper()}kind', klasse=klasse, jahrgang=jahrgang)
            db.session.add_all(kinder.values())
            db.session.flush()

            # Anna hat eine auffaellige Diagnostik und viele schwache Beobachtungen:
            # ein A-Vorschlag muss dazu eine Warnung erzeugen.
            hsp3 = DiagnostikTestform.query.filter_by(name='HSP 3').one()
            kennwerte = {k.name: k.id for k in hsp3.kennwerte}
            speichere_ergebnis(kinder['Anna'], hsp3, '2026/2027', 'mitte',
                               {(kennwerte['Graphemtreffer'], 'prozentrang'): 8}, nutzer['leitung'].id,
                               datum=date(2027, 2, 10))
            bogen = Bogen(titel='Deutsch')
            db.session.add(bogen)
            db.session.flush()
            for nummer in range(5):
                item = Item(bogen_id=bogen.id, bereich='Schreiben', text=f'Kompetenz {nummer}')
                db.session.add(item)
                db.session.flush()
                db.session.add(Beobachtung(schueler_id=kinder['Anna'].id, item_id=item.id, wert=1,
                                           datum=utc_now()))

            konferenz = erstelle_konferenz('2026/2027', 3, 'Herbstkonferenz Jahrgang 3', date(2026, 11, 12),
                                           nutzer['leitung'])
            db.session.commit()
            self.konferenz_id = konferenz.id
            self.ids = {name: kind.id for name, kind in kinder.items()}
            self.user_ids = {name: user.id for name, user in nutzer.items()}
            self.eintraege = {
                eintrag.schueler.vorname: eintrag.id for eintrag in konferenz.kinder
            }

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------ Hilfen

    def _login(self, username):
        self.client = self.app.test_client()
        seite = self.client.get('/login')
        token = CSRF_RE.search(seite.get_data(as_text=True)).group(1)
        self.client.post('/login', data={'username': username, 'password': 'pass', '_csrf_token': token})
        return CSRF_RE.search(self.client.get('/').get_data(as_text=True)).group(1)

    def _token(self):
        return CSRF_RE.search(self.client.get('/konferenz').get_data(as_text=True)).group(1)

    def _speichern(self, eintrag_id, feld, wert, bekannt_am=None):
        return self.client.post(
            f'/konferenz/{self.konferenz_id}/kind/{eintrag_id}/feld',
            json={'feld': feld, 'wert': wert, 'bekannt_am': bekannt_am},
            headers={'X-CSRF-Token': self._token()},
        )

    def _eintrag(self, vorname):
        return db.session.get(FoerderkonferenzKind, self.eintraege[vorname])

    def _konferenz(self):
        return db.session.get(Foerderkonferenz, self.konferenz_id)

    # ------------------------------------------------------------------ Anlegen und Rechte

    def test_conference_contains_the_whole_year_group(self):
        with self.app.app_context():
            konferenz = self._konferenz()
            self.assertEqual({'Anna', 'Ben', 'Cem'}, {e.schueler.vorname for e in konferenz.kinder})
            self.assertEqual({'3a', '3b'}, {e.klasse for e in konferenz.kinder})
            self.assertEqual('geplant', konferenz.status)

    def test_only_leadership_creates_and_moderates(self):
        token = self._login('klara')
        antwort = self.client.post('/konferenz/neu', data={'_csrf_token': token, 'schuljahr': '2026/2027',
                                                           'jahrgang': '4', 'titel': 'Heimlich'})
        self.assertEqual(403, antwort.status_code)
        self.assertEqual(403, self.client.get(f'/konferenz/{self.konferenz_id}/phase/1').status_code)
        self.assertEqual(200, self.client.get(f'/konferenz/{self.konferenz_id}').status_code)

        self._login('leitung')
        self.assertEqual(200, self.client.get(f'/konferenz/{self.konferenz_id}/phase/1').status_code)
        token = self._token()
        antwort = self.client.post('/konferenz/neu', data={'_csrf_token': token, 'schuljahr': '2026/2027',
                                                           'jahrgang': '1', 'titel': 'Jahrgang 1'},
                                   follow_redirects=True)
        self.assertIn('Jahrgang 1', antwort.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(2, Foerderkonferenz.query.count())

    def test_class_teacher_writes_only_own_proposals(self):
        self._login('klara')
        antwort = self._speichern(self.eintraege['Anna'], 'vorschlag_stufe', 'B')
        self.assertEqual(200, antwort.status_code)
        self.assertTrue(antwort.get_json()['ok'])
        # Fremde Klasse und Konferenzfelder bleiben gesperrt.
        self.assertEqual(403, self._speichern(self.eintraege['Cem'], 'vorschlag_stufe', 'A').status_code)
        self.assertEqual(403, self._speichern(self.eintraege['Anna'], 'stufe', 'A').status_code)
        self.assertEqual(400, self._speichern(self.eintraege['Anna'], 'geheim', 'x').status_code)
        with self.app.app_context():
            eintrag = self._eintrag('Anna')
            self.assertEqual('B', eintrag.vorschlag_stufe)
            self.assertEqual(self.user_ids['klara'], eintrag.vorschlag_von_user_id)
            self.assertIsNone(eintrag.stufe)

    def test_teacher_without_children_cannot_read(self):
        with self.app.app_context():
            UserKlassenzuordnung.query.filter_by(user_id=self.user_ids['kai']).delete()
            db.session.commit()
        self._login('kai')
        self.assertEqual(403, self.client.get(f'/konferenz/{self.konferenz_id}').status_code)
        self._login('foerder')
        self.assertEqual(200, self.client.get(f'/konferenz/{self.konferenz_id}').status_code)
        self.assertEqual(403, self.client.get(f'/konferenz/{self.konferenz_id}/phase/1').status_code)

    # ------------------------------------------------------------------ Speichern

    def test_field_save_reports_time_and_conflicts(self):
        self._login('leitung')
        erste = self._speichern(self.eintraege['Anna'], 'beschluss', 'Lesetraining').get_json()
        self.assertTrue(erste['ok'])
        self.assertTrue(erste['bearbeitet_am'])
        # Zweites Gerät kennt nur den alten Stand -> Hinweis, aber gespeichert wird trotzdem.
        alt = (utc_now() - timedelta(minutes=5)).isoformat()
        zweite = self._speichern(self.eintraege['Anna'], 'beschluss', 'Anderer Text', bekannt_am=alt).get_json()
        self.assertTrue(zweite['konflikt'])
        ohne = self._speichern(self.eintraege['Anna'], 'beschluss', 'Dritter Text',
                               bekannt_am=zweite['bearbeitet_am']).get_json()
        self.assertFalse(ohne['konflikt'])
        with self.app.app_context():
            self.assertEqual('Dritter Text', self._eintrag('Anna').beschluss)

        # Datum, Person und Schalter werden typgerecht gespeichert.
        self._speichern(self.eintraege['Anna'], 'ueberpruefung_am', '2027-01-20')
        self._speichern(self.eintraege['Anna'], 'verantwortlich_user_id', str(self.user_ids['klara']))
        self._speichern(self.eintraege['Anna'], 'massnahme_foerderkurs', '1')
        self._speichern(self.eintraege['Anna'], 'stern', '')
        with self.app.app_context():
            eintrag = self._eintrag('Anna')
            self.assertEqual(date(2027, 1, 20), eintrag.ueberpruefung_am)
            self.assertEqual(self.user_ids['klara'], eintrag.verantwortlich_user_id)
            self.assertTrue(eintrag.massnahme_foerderkurs)
            self.assertFalse(eintrag.stern)

    def test_conference_resumes_where_it_was_left(self):
        self._login('leitung')
        self.client.post(f'/konferenz/{self.konferenz_id}/status',
                         data={'_csrf_token': self._token(), 'aktion': 'starten'})
        self._speichern(self.eintraege['Anna'], 'stufe', 'C')
        self.client.get(f'/konferenz/{self.konferenz_id}/phase/5')
        with self.app.app_context():
            konferenz = self._konferenz()
            self.assertEqual(5, konferenz.aktuelle_phase)
            self.assertEqual(self.ids['Anna'], konferenz.aktuelles_kind_id)
        html = self.client.get(f'/konferenz/{self.konferenz_id}').get_data(as_text=True)
        self.assertIn('Weiter in Phase 5', html)

    # ------------------------------------------------------------------ Phasen

    def test_a_block_warns_when_data_disagrees(self):
        self._login('klara')
        self._speichern(self.eintraege['Anna'], 'vorschlag_stufe', 'A')
        self._speichern(self.eintraege['Ben'], 'vorschlag_stufe', 'A')
        self._login('leitung')
        html = self.client.get(f'/konferenz/{self.konferenz_id}/phase/3').get_data(as_text=True)
        self.assertIn('Anna', html)
        self.assertIn('deutlich auffällig', html)
        self.assertIn('Kompetenzen mit „reicht noch nicht“', html)

        antwort = self.client.post(f'/konferenz/{self.konferenz_id}/a-block',
                                   data={'_csrf_token': self._token(), 'klasse': '3a'}, follow_redirects=True)
        self.assertIn('2 Kind(er) auf A bestätigt', antwort.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual('A', self._eintrag('Anna').stufe)
            self.assertEqual('A', self._eintrag('Ben').stufe)
            self.assertIsNone(self._eintrag('Cem').stufe)

    def test_child_view_shows_merged_data_and_keyboard_hint(self):
        self._login('leitung')
        self._speichern(self.eintraege['Anna'], 'stufe', 'B')
        html = self.client.get(f'/konferenz/{self.konferenz_id}/phase/4').get_data(as_text=True)
        self.assertIn('Anna 3Akind', html)
        self.assertNotIn('Ben 3Akind', html)                # nur das aktuelle Kind
        self.assertIn('Lernstand', html)
        self.assertIn('Diagnostik', html)
        self.assertIn('data-stufe-taste="B"', html)
        self.assertIn('Tastatur:', html)

    def test_phase_six_lists_course_participants(self):
        self._login('leitung')
        self._speichern(self.eintraege['Cem'], 'stufe', 'C')
        self._speichern(self.eintraege['Cem'], 'massnahme_foerderkurs', '1')
        self._speichern(self.eintraege['Cem'], 'foerderkurs_name', 'Lesekurs Mo/Do')
        html = self.client.get(f'/konferenz/{self.konferenz_id}/phase/6').get_data(as_text=True)
        self.assertIn('Lesekurs Mo/Do', html)
        with self.app.app_context():
            self.assertIn('Förderkurs-Teilnahme', ' '.join(pruefe_vollstaendigkeit(self._konferenz())))

    # ------------------------------------------------------------------ Abschluss

    def test_closing_warns_but_allows_and_locks(self):
        self._login('leitung')
        self._speichern(self.eintraege['Anna'], 'stufe', 'B')
        with self.app.app_context():
            hinweise = ' '.join(pruefe_vollstaendigkeit(self._konferenz()))
            self.assertIn('ohne Stufe', hinweise)
            self.assertIn('ohne Beschluss', hinweise)

        html = self.client.get(f'/konferenz/{self.konferenz_id}/phase/7').get_data(as_text=True)
        self.assertIn('Der Abschluss ist trotzdem möglich', html)
        self.client.post(f'/konferenz/{self.konferenz_id}/status',
                         data={'_csrf_token': self._token(), 'aktion': 'abschliessen'})
        with self.app.app_context():
            konferenz = self._konferenz()
            self.assertEqual('abgeschlossen', konferenz.status)
            self.assertIsNotNone(konferenz.abgeschlossen_am)
        self.assertEqual(403, self._speichern(self.eintraege['Anna'], 'beschluss', 'zu spät').status_code)

        self.client.post(f'/konferenz/{self.konferenz_id}/status',
                         data={'_csrf_token': self._token(), 'aktion': 'oeffnen', 'grund': 'Tippfehler'})
        with self.app.app_context():
            konferenz = self._konferenz()
            self.assertEqual('laufend', konferenz.status)
            aktionen = [(log.aktion, log.details) for log in konferenz.logs]
            self.assertIn('wieder geöffnet', [a for a, _ in aktionen])
            self.assertIn('Tippfehler', [d for _, d in aktionen])
        self.assertEqual(200, self._speichern(self.eintraege['Anna'], 'beschluss', 'jetzt wieder').status_code)

    def _beschluss_vorbereiten(self, frist, verantwortlich='klara'):
        self._login('leitung')
        self._speichern(self.eintraege['Anna'], 'stufe', 'B')
        self._speichern(self.eintraege['Anna'], 'beschluss', 'Wiedervorlage Lesen')
        self._speichern(self.eintraege['Anna'], 'verantwortlich_user_id', str(self.user_ids[verantwortlich]))
        self._speichern(self.eintraege['Anna'], 'ueberpruefung_am', frist.isoformat())
        self.client.post(f'/konferenz/{self.konferenz_id}/status',
                         data={'_csrf_token': self._token(), 'aktion': 'abschliessen'})

    def test_decisions_become_tasks_for_responsible_and_class_teacher(self):
        heute = utc_now().date()
        self._beschluss_vorbereiten(heute + timedelta(days=3), verantwortlich='fachl')
        with self.app.app_context():
            fachl = db.session.get(User, self.user_ids['fachl'])
            klara = db.session.get(User, self.user_ids['klara'])
            kai = db.session.get(User, self.user_ids['kai'])
            self.assertEqual(1, len(offene_beschluesse(fachl)))     # zuständig
            self.assertEqual(1, len(offene_beschluesse(klara)))     # Klassenleitung
            self.assertEqual(0, len(offene_beschluesse(kai)))       # andere Klasse

        self._login('klara')
        start = self.client.get('/').get_data(as_text=True)
        self.assertIn('Wiedervorlage: Anna', start)
        seite = self.client.get('/konferenz/aufgaben').get_data(as_text=True)
        self.assertIn('Wiedervorlage Lesen', seite)
        self.client.post(f'/konferenz/beschluss/{self.eintraege["Anna"]}/erledigt',
                         data={'_csrf_token': self._token()})
        with self.app.app_context():
            self.assertIsNotNone(self._eintrag('Anna').erledigt_am)
            klara = db.session.get(User, self.user_ids['klara'])
            self.assertEqual(0, len(offene_beschluesse(klara)))

    def test_tasks_appear_only_shortly_before_the_deadline(self):
        heute = utc_now().date()
        self._beschluss_vorbereiten(heute + timedelta(days=30))
        with self.app.app_context():
            klara = db.session.get(User, self.user_ids['klara'])
            self.assertEqual(0, len(offene_beschluesse(klara)))
            self.assertEqual(1, len(offene_beschluesse(klara, alle=True)))

    # ------------------------------------------------------------------ Nach der Konferenz

    def test_protocol_has_internal_and_anonymous_version(self):
        self._beschluss_vorbereiten(utc_now().date() + timedelta(days=5))
        antwort = self.client.get(f'/konferenz/{self.konferenz_id}/export/odt')
        self.assertEqual(200, antwort.status_code)
        with zipfile.ZipFile(BytesIO(antwort.data)) as archiv:
            intern = archiv.read('content.xml').decode()
        self.assertIn('Anna', intern)
        self.assertIn('Wiedervorlage Lesen', intern)
        self.assertIn('Herbstkonferenz Jahrgang 3', intern)

        antwort = self.client.get(f'/konferenz/{self.konferenz_id}/export/odt?fassung=anonym')
        with zipfile.ZipFile(BytesIO(antwort.data)) as archiv:
            anonym = archiv.read('content.xml').decode()
        self.assertNotIn('Anna', anonym)
        self.assertIn('Ergebnis des Jahrgangs', anonym)
        self.assertIn('keine Namen', anonym)

        # Die Klassenleitung bekommt nur die anonyme Fassung.
        self._login('klara')
        self.assertEqual(403, self.client.get(f'/konferenz/{self.konferenz_id}/export/odt').status_code)
        self.assertEqual(200, self.client.get(f'/konferenz/{self.konferenz_id}/export/odt?fassung=anonym').status_code)

    def test_entry_appears_in_student_record_and_export(self):
        self._beschluss_vorbereiten(utc_now().date() + timedelta(days=5))
        self._login('klara')
        html = self.client.get(f'/schuelerakte?schueler_id={self.ids["Anna"]}').get_data(as_text=True)
        self.assertIn('Herbstkonferenz Jahrgang 3', html)
        self.assertIn('Wiedervorlage Lesen', html)
        with self.app.app_context():
            anna = db.session.get(Schueler, self.ids['Anna'])
            klara = db.session.get(User, self.user_ids['klara'])
            akte = collect_record(anna, klara)
            self.assertEqual(1, len(akte['konferenzen']))
            from student_record import record_blocks
            texte = ' '.join(str(block) for block in record_blocks(akte, '01.01.2027'))
            self.assertIn('Förderkonferenzen', texte)
            self.assertIn('Wiedervorlage Lesen', texte)

    def test_evaluation_of_decisions(self):
        self._beschluss_vorbereiten(utc_now().date() + timedelta(days=5))
        self._login('leitung')
        html = self.client.get(f'/konferenz/{self.konferenz_id}/evaluation').get_data(as_text=True)
        self.assertIn('Anna', html)
        self.assertIn('Wiedervorlage Lesen', html)
        # Abgeschlossene Konferenz: erst wieder öffnen, dann eintragen.
        self.assertEqual(403, self._speichern(self.eintraege['Anna'], 'eval_wirksam', 'teilweise').status_code)
        self.client.post(f'/konferenz/{self.konferenz_id}/status',
                         data={'_csrf_token': self._token(), 'aktion': 'oeffnen'})
        self._speichern(self.eintraege['Anna'], 'eval_umgesetzt', 'ja')
        self._speichern(self.eintraege['Anna'], 'eval_wirksam', 'teilweise')
        self._speichern(self.eintraege['Anna'], 'eval_stufe_neu', 'C')
        with self.app.app_context():
            eintrag = self._eintrag('Anna')
            self.assertEqual(('ja', 'teilweise', 'C'), (eintrag.eval_umgesetzt, eintrag.eval_wirksam, eintrag.eval_stufe_neu))
            self.assertEqual(self.user_ids['leitung'], eintrag.eval_von_user_id)

    def test_conference_can_be_deleted_completely(self):
        self._login('klara')
        self.assertEqual(403, self.client.post(f'/konferenz/{self.konferenz_id}/loeschen',
                                               data={'_csrf_token': self._token(), 'bestaetigung': 'LÖSCHEN'}).status_code)
        self._beschluss_vorbereiten(utc_now().date() + timedelta(days=3))
        # Ohne Bestätigung passiert nichts.
        antwort = self.client.post(f'/konferenz/{self.konferenz_id}/loeschen',
                                   data={'_csrf_token': self._token(), 'bestaetigung': 'ja'}, follow_redirects=True)
        self.assertIn('LÖSCHEN in das Feld', antwort.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(1, Foerderkonferenz.query.count())

        antwort = self.client.post(f'/konferenz/{self.konferenz_id}/loeschen',
                                   data={'_csrf_token': self._token(), 'bestaetigung': 'LÖSCHEN'}, follow_redirects=True)
        self.assertIn('mit allen Einträgen gelöscht', antwort.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(0, Foerderkonferenz.query.count())
            self.assertEqual(0, FoerderkonferenzKind.query.count())
            self.assertEqual(0, FoerderkonferenzLog.query.count())
            self.assertEqual(4, Schueler.query.count())
            klara = db.session.get(User, self.user_ids['klara'])
            self.assertEqual([], offene_beschluesse(klara, alle=True))

    def test_levels_can_be_entered_together_during_the_conference(self):
        self._login('klara')
        self.assertEqual(403, self.client.get(f'/konferenz/{self.konferenz_id}/stufen').status_code)

        self._login('leitung')
        html = self.client.get(f'/konferenz/{self.konferenz_id}/stufen').get_data(as_text=True)
        for text in ('Stufen gemeinsam eintragen', 'Klasse 3a', 'Klasse 3b', 'Anna', 'Cem', 'data-zeile'):
            self.assertIn(text, html)
        # Ohne jeden Vorschlag: Stufe direkt setzen.
        self._speichern(self.eintraege['Anna'], 'stufe', 'C')
        self._speichern(self.eintraege['Ben'], 'stufe', 'B')
        self._speichern(self.eintraege['Cem'], 'stufe', 'A')
        self._speichern(self.eintraege['Ben'], 'fragestellung', 'Konzentration')
        phase4 = self.client.get(f'/konferenz/{self.konferenz_id}/phase/4').get_data(as_text=True)
        self.assertIn('Ben 3Akind', phase4)
        phase5 = self.client.get(f'/konferenz/{self.konferenz_id}/phase/5').get_data(as_text=True)
        self.assertIn('Anna 3Akind', phase5)
        phase3 = self.client.get(f'/konferenz/{self.konferenz_id}/phase/3').get_data(as_text=True)
        self.assertIn('Cem', phase3)
        html = self.client.get(f'/konferenz/{self.konferenz_id}/stufen').get_data(as_text=True)
        self.assertIn('data-zaehler="ohne">0<', html)

    def test_deleting_a_child_removes_its_conference_rows(self):
        with self.app.app_context():
            anna = db.session.get(Schueler, self.ids['Anna'])
            db.session.delete(anna)
            db.session.commit()
            self.assertEqual(2, FoerderkonferenzKind.query.count())
            self.assertEqual(1, Foerderkonferenz.query.count())
            self.assertTrue(FoerderkonferenzLog.query.count() >= 1)


if __name__ == '__main__':
    unittest.main()
