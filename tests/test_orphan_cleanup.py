"""Tests fuer cleanup_orphan_uploads.py.

Der Lauf raeumt Dateien auf, die kein Datensatz mehr referenziert. Das ist
naturgemaess gefaehrlich: ein Fehler loescht Fotos, die noch gebraucht werden.
Geprueft wird deshalb beides - dass Verwaistes gefunden und Referenziertes
verschont wird - sowie die Schutzschwelle gegen eine falsche DATABASE_URL.
"""

import io
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import cleanup_orphan_uploads as cleanup
from app import create_app
from extensions import db
from models import (
    Beobachtung,
    Bogen,
    ErziehungsEreignis,
    ErziehungsEreignisAnhang,
    ErziehungsEreignisKategorie,
    ErziehungsEreignisVorlage,
    ErziehungsOrt,
    Item,
    Schueler,
    User,
    WorkPlan,
    WorkPlanTask,
    WorkPlanTaskAttachment,
)


class OrphanCleanupTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='km_orphan_test_')
        self.protected_dir = os.path.join(self.tmpdir, 'protected_uploads')
        self.legacy_dir = os.path.join(self.tmpdir, 'legacy_uploads')
        os.makedirs(self.protected_dir, exist_ok=True)
        os.makedirs(self.legacy_dir, exist_ok=True)

        database_uri = os.environ.get('TEST_DATABASE_URL') or f"sqlite:///{self.tmpdir}/test.db"
        self.app = create_app({
            'TESTING': True,
            'SECRET_KEY': 'test-secret',
            'SQLALCHEMY_DATABASE_URI': database_uri,
            'UPLOAD_FOLDER': os.path.join(self.tmpdir, 'uploads'),
            'PROTECTED_UPLOAD_FOLDER': self.protected_dir,
        })

        # Die Legacy-Wurzel haengt am root_path der Anwendung und zeigt sonst auf
        # das echte static/uploads des Projekts.
        self.legacy_patch = patch.object(
            cleanup, 'get_legacy_upload_root', lambda: Path(self.legacy_dir),
        )
        self.legacy_patch.start()

        with self.app.app_context():
            db.create_all()
            self._seed()

    def tearDown(self):
        self.legacy_patch.stop()
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Aufbau
    # ------------------------------------------------------------------

    def _write(self, root, rel_path, inhalt=b'testinhalt'):
        absolute = os.path.join(root, rel_path)
        os.makedirs(os.path.dirname(absolute), exist_ok=True)
        with open(absolute, 'wb') as handle:
            handle.write(inhalt)
        return rel_path

    def _seed(self):
        """Drei referenzierte Dateien, drei verwaiste, ein fehlender Verweis."""
        admin = User(username='admin', password_hash='x', role='admin')
        schueler = Schueler(vorname='Max', nachname='Muster', klasse='3a')
        bogen = Bogen(titel='Sozialverhalten')
        kategorie = ErziehungsEreignisKategorie(name='Konflikt')
        ort = ErziehungsOrt(name='Schulhof')
        db.session.add_all([admin, schueler, bogen, kategorie, ort])
        db.session.flush()

        item = Item(bogen_id=bogen.id, bereich='Konflikt', text='Loest Streit friedlich')
        vorlage = ErziehungsEreignisVorlage(category_id=kategorie.id, name='Streit')
        db.session.add_all([item, vorlage])
        db.session.flush()

        # Referenziert: Beobachtungsfoto im geschuetzten Ordner
        self.ref_foto = self._write(self.protected_dir, 'referenziert-foto.jpg')
        db.session.add(Beobachtung(
            schueler_id=schueler.id, item_id=item.id, wert=3, foto_pfad=self.ref_foto,
        ))

        # Referenziert: Ereignisanhang im Unterordner
        ereignis = ErziehungsEreignis(
            student_id=schueler.id, event_template_id=vorlage.id, ort_id=ort.id,
            beschreibung='Vorfall',
        )
        db.session.add(ereignis)
        db.session.flush()
        self.ref_anhang = self._write(self.protected_dir, 'erziehung/referenziert-anhang.pdf')
        db.session.add(ErziehungsEreignisAnhang(
            event_id=ereignis.id, file_path=self.ref_anhang, original_name='a.pdf',
        ))

        # Referenziert: Aufgabenfoto
        plan = WorkPlan(
            student_id=schueler.id, created_by_user_id=admin.id,
            period_start=db.func.current_date(), period_end=db.func.current_date(),
        )
        db.session.add(plan)
        db.session.flush()
        task = WorkPlanTask(work_plan_id=plan.id, title='Lesen')
        db.session.add(task)
        db.session.flush()
        self.ref_aufgabe = self._write(self.protected_dir, 'workplan/referenziert-aufgabe.jpg')
        db.session.add(WorkPlanTaskAttachment(task_id=task.id, file_path=self.ref_aufgabe))

        # Verweis ohne Datei auf der Platte
        db.session.add(Beobachtung(
            schueler_id=schueler.id, item_id=item.id, wert=2, foto_pfad='verschwunden.jpg',
        ))

        db.session.commit()

        # Verwaist: nichts zeigt darauf
        self.orphan_protected = self._write(self.protected_dir, 'verwaist-eins.jpg')
        self.orphan_sub = self._write(self.protected_dir, 'workplan/verwaist-zwei.jpg')
        self.orphan_legacy = self._write(self.legacy_dir, 'verwaist-legacy.jpg')

    # ------------------------------------------------------------------
    # Hilfen
    # ------------------------------------------------------------------

    def _run(self, **kwargs):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = cleanup.report(flask_app=self.app, **kwargs)
        return code, buffer.getvalue()

    def _exists(self, root, rel_path):
        return os.path.isfile(os.path.join(root, rel_path))

    def _assert_referenced_intact(self):
        self.assertTrue(self._exists(self.protected_dir, self.ref_foto),
                        'Referenziertes Beobachtungsfoto wurde gelöscht')
        self.assertTrue(self._exists(self.protected_dir, self.ref_anhang),
                        'Referenzierter Ereignisanhang wurde gelöscht')
        self.assertTrue(self._exists(self.protected_dir, self.ref_aufgabe),
                        'Referenziertes Aufgabenfoto wurde gelöscht')

    # ------------------------------------------------------------------
    # Bericht
    # ------------------------------------------------------------------

    def test_report_finds_orphans_without_changing_anything(self):
        code, ausgabe = self._run()
        self.assertEqual(code, 0)
        self.assertIn('Verwaiste Dateien: 3', ausgabe)
        self.assertIn('verwaist-eins.jpg', ausgabe)
        self.assertIn('workplan/verwaist-zwei.jpg', ausgabe)
        self.assertIn('verwaist-legacy.jpg', ausgabe)
        self.assertIn('Es wurde nichts verändert', ausgabe)

        # Nichts angefasst - auch die verwaisten Dateien liegen noch.
        self.assertTrue(self._exists(self.protected_dir, self.orphan_protected))
        self.assertTrue(self._exists(self.legacy_dir, self.orphan_legacy))
        self._assert_referenced_intact()

    def test_report_lists_records_pointing_at_missing_files(self):
        _, ausgabe = self._run()
        self.assertIn('zeigen auf eine fehlende Datei', ausgabe)
        self.assertIn('verschwunden.jpg', ausgabe)
        self.assertIn('nicht angefasst', ausgabe)

    def test_report_does_not_flag_referenced_files(self):
        _, ausgabe = self._run()
        for referenziert in (self.ref_foto, self.ref_anhang, self.ref_aufgabe):
            self.assertNotIn(
                f'[geschützt] {referenziert}', ausgabe,
                f'{referenziert} wurde fälschlich als verwaist gemeldet',
            )

    # ------------------------------------------------------------------
    # Loeschen
    # ------------------------------------------------------------------

    def test_delete_removes_only_orphans(self):
        code, ausgabe = self._run(delete=True, assume_yes=True)
        self.assertEqual(code, 0)
        self.assertIn('Gelöscht: 3 Datei(en)', ausgabe)

        self.assertFalse(self._exists(self.protected_dir, self.orphan_protected))
        self.assertFalse(self._exists(self.protected_dir, self.orphan_sub))
        self.assertFalse(self._exists(self.legacy_dir, self.orphan_legacy))
        self._assert_referenced_intact()

    def test_delete_without_confirmation_is_refused_when_not_interactive(self):
        with patch('sys.stdin.isatty', return_value=False):
            code, ausgabe = self._run(delete=True)
        self.assertEqual(code, 2)
        self.assertIn('Keine Eingabe möglich', ausgabe)
        self.assertTrue(self._exists(self.protected_dir, self.orphan_protected),
                        'Ohne Bestätigung wurde trotzdem gelöscht')

    def test_declining_the_prompt_changes_nothing(self):
        with patch('sys.stdin.isatty', return_value=True), patch('builtins.input', return_value='n'):
            code, ausgabe = self._run(delete=True)
        self.assertEqual(code, 0)
        self.assertIn('Abgebrochen', ausgabe)
        self.assertTrue(self._exists(self.protected_dir, self.orphan_protected))

    def test_confirming_the_prompt_deletes(self):
        with patch('sys.stdin.isatty', return_value=True), patch('builtins.input', return_value='j'):
            code, _ = self._run(delete=True)
        self.assertEqual(code, 0)
        self.assertFalse(self._exists(self.protected_dir, self.orphan_protected))
        self._assert_referenced_intact()

    # ------------------------------------------------------------------
    # Schutzschwelle
    # ------------------------------------------------------------------

    def test_empty_database_aborts_instead_of_deleting_everything(self):
        """Der gefaehrlichste Bedienfehler: Lauf gegen die falsche Datenbank."""
        with self.app.app_context():
            Beobachtung.query.delete()
            ErziehungsEreignisAnhang.query.delete()
            WorkPlanTaskAttachment.query.delete()
            db.session.commit()

        code, ausgabe = self._run(delete=True, assume_yes=True)
        self.assertEqual(code, 2)
        self.assertIn('ABBRUCH', ausgabe)
        self.assertIn('DATABASE_URL', ausgabe)

        # Sämtliche Dateien liegen noch, obwohl sie jetzt alle unreferenziert sind.
        self.assertTrue(self._exists(self.protected_dir, self.ref_foto))
        self.assertTrue(self._exists(self.protected_dir, self.ref_anhang))
        self.assertTrue(self._exists(self.protected_dir, self.orphan_protected))

    def test_force_overrides_the_empty_database_guard(self):
        with self.app.app_context():
            Beobachtung.query.delete()
            ErziehungsEreignisAnhang.query.delete()
            WorkPlanTaskAttachment.query.delete()
            db.session.commit()

        code, ausgabe = self._run(delete=True, assume_yes=True, force=True)
        self.assertEqual(code, 0)
        self.assertIn('--force gesetzt', ausgabe)
        self.assertFalse(self._exists(self.protected_dir, self.ref_foto),
                         'Mit --force sollte auch die vormals referenzierte Datei fallen')

    # ------------------------------------------------------------------
    # Pfadnormalisierung
    # ------------------------------------------------------------------

    def test_legacy_style_reference_with_uploads_prefix_is_recognised(self):
        """Aeltere Bestaende speichern den Pfad mitunter mit uploads/-Praefix."""
        with self.app.app_context():
            beobachtung = Beobachtung.query.filter_by(foto_pfad=self.ref_foto).first()
            beobachtung.foto_pfad = f'uploads/{self.ref_foto}'
            db.session.commit()

        code, ausgabe = self._run(delete=True, assume_yes=True)
        self.assertEqual(code, 0)
        self.assertTrue(
            self._exists(self.protected_dir, self.ref_foto),
            'Verweis mit uploads/-Präfix wurde nicht erkannt, Datei fälschlich gelöscht',
        )
        self.assertIn('Verwaiste Dateien: 3', ausgabe)

    def test_mask_url_hides_the_password(self):
        maskiert = cleanup.mask_url('postgresql://kompetenz_user:geheim@127.0.0.1:5432/db')
        self.assertNotIn('geheim', maskiert)
        self.assertEqual('postgresql://kompetenz_user:***@127.0.0.1:5432/db', maskiert)

    def test_mask_url_leaves_sqlite_paths_alone(self):
        pfad = 'sqlite:////home/kompkomp/instance/schule.db'
        self.assertEqual(pfad, cleanup.mask_url(pfad))

    def test_report_output_never_contains_the_password(self):
        _, ausgabe = self._run()
        self.assertIn('Datenbank:', ausgabe)
        self.assertNotIn('@', ausgabe.splitlines()[0].split('Datenbank:')[1].replace('***@', ''))


if __name__ == '__main__':
    unittest.main()
