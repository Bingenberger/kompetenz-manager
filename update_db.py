from sqlalchemy import text

from app import app, db


def _sqlite_add_missing_user_name_columns():
    engine = db.engine
    if engine.url.get_backend_name() != "sqlite":
        return

    with engine.connect() as conn:
        user_exists = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='user'")
        ).first()
        if not user_exists:
            return

        columns = {
            row["name"]
            for row in conn.execute(text("PRAGMA table_info(user)")).mappings().all()
        }

        if "vorname" not in columns:
            conn.execute(text("ALTER TABLE user ADD COLUMN vorname VARCHAR(100)"))
            print("Spalte 'user.vorname' wurde ergänzt.")

        if "nachname" not in columns:
            conn.execute(text("ALTER TABLE user ADD COLUMN nachname VARCHAR(100)"))
            print("Spalte 'user.nachname' wurde ergänzt.")

        conn.commit()


def _sqlite_add_missing_foerderplan_creator_column():
    engine = db.engine
    if engine.url.get_backend_name() != "sqlite":
        return

    with engine.connect() as conn:
        table_exists = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='foerderplan'")
        ).first()
        if not table_exists:
            return

        columns = {
            row["name"]
            for row in conn.execute(text("PRAGMA table_info(foerderplan)")).mappings().all()
        }
        if "creator_user_id" not in columns:
            conn.execute(text("ALTER TABLE foerderplan ADD COLUMN creator_user_id INTEGER"))
            print("Spalte 'foerderplan.creator_user_id' wurde ergänzt.")
            # Bestehende Pläne ohne Ersteller auf Admin (id=1) zurücksetzen, falls vorhanden.
            conn.execute(
                text(
                    "UPDATE foerderplan SET creator_user_id = 1 "
                    "WHERE creator_user_id IS NULL AND EXISTS (SELECT 1 FROM user WHERE id = 1)"
                )
            )
        conn.commit()


def _sqlite_add_missing_schueler_geburtsdatum_column():
    engine = db.engine
    if engine.url.get_backend_name() != "sqlite":
        return

    with engine.connect() as conn:
        table_exists = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schueler'")
        ).first()
        if not table_exists:
            return

        columns = {
            row["name"]
            for row in conn.execute(text("PRAGMA table_info(schueler)")).mappings().all()
        }
        if "geburtsdatum" not in columns:
            conn.execute(text("ALTER TABLE schueler ADD COLUMN geburtsdatum DATE"))
            print("Spalte 'schueler.geburtsdatum' wurde ergänzt.")
        conn.commit()


def _sqlite_add_missing_workplan_weeks_column():
    engine = db.engine
    if engine.url.get_backend_name() != "sqlite":
        return

    with engine.connect() as conn:
        table_exists = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='system_konfiguration'")
        ).first()
        if not table_exists:
            return

        columns = {
            row["name"]
            for row in conn.execute(text("PRAGMA table_info(system_konfiguration)")).mappings().all()
        }
        if "workplan_suggestions_weeks" not in columns:
            conn.execute(
                text("ALTER TABLE system_konfiguration ADD COLUMN workplan_suggestions_weeks INTEGER NOT NULL DEFAULT 12")
            )
            print("Spalte 'system_konfiguration.workplan_suggestions_weeks' wurde ergänzt.")
        conn.commit()


def _postgres_add_missing_workplan_weeks_column():
    engine = db.engine
    if engine.url.get_backend_name() not in {"postgresql", "postgres"}:
        return

    with engine.connect() as conn:
        table_exists = conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'system_konfiguration'"
            )
        ).first()
        if not table_exists:
            return

        column_exists = conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "AND table_name = 'system_konfiguration' "
                "AND column_name = 'workplan_suggestions_weeks'"
            )
        ).first()
        if not column_exists:
            conn.execute(
                text(
                    "ALTER TABLE public.system_konfiguration "
                    "ADD COLUMN workplan_suggestions_weeks INTEGER NOT NULL DEFAULT 12"
                )
            )
            print("Spalte 'system_konfiguration.workplan_suggestions_weeks' wurde für PostgreSQL ergänzt.")
        conn.commit()


def _sqlite_add_missing_workplan_task_columns():
    engine = db.engine
    if engine.url.get_backend_name() != "sqlite":
        return

    with engine.connect() as conn:
        table_exists = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='work_plan_task'")
        ).first()
        if not table_exists:
            return

        columns = {
            row["name"]
            for row in conn.execute(text("PRAGMA table_info(work_plan_task)")).mappings().all()
        }
        if "learning_area" not in columns:
            conn.execute(text("ALTER TABLE work_plan_task ADD COLUMN learning_area VARCHAR(50)"))
            print("Spalte 'work_plan_task.learning_area' wurde ergänzt.")
        if "icon_name" not in columns:
            conn.execute(text("ALTER TABLE work_plan_task ADD COLUMN icon_name VARCHAR(50)"))
            print("Spalte 'work_plan_task.icon_name' wurde ergänzt.")
        if "materials" not in columns:
            conn.execute(text("ALTER TABLE work_plan_task ADD COLUMN materials TEXT"))
            print("Spalte 'work_plan_task.materials' wurde ergänzt.")
        if "child_goal" not in columns:
            conn.execute(text("ALTER TABLE work_plan_task ADD COLUMN child_goal TEXT"))
            print("Spalte 'work_plan_task.child_goal' wurde ergänzt.")
        conn.commit()


def _postgres_add_missing_workplan_task_columns():
    engine = db.engine
    if engine.url.get_backend_name() not in {"postgresql", "postgres"}:
        return

    with engine.connect() as conn:
        table_exists = conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'work_plan_task'"
            )
        ).first()
        if not table_exists:
            return

        existing = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'work_plan_task'"
                )
            ).all()
        }
        if "learning_area" not in existing:
            conn.execute(text("ALTER TABLE public.work_plan_task ADD COLUMN learning_area VARCHAR(50)"))
            print("Spalte 'work_plan_task.learning_area' wurde für PostgreSQL ergänzt.")
        if "icon_name" not in existing:
            conn.execute(text("ALTER TABLE public.work_plan_task ADD COLUMN icon_name VARCHAR(50)"))
            print("Spalte 'work_plan_task.icon_name' wurde für PostgreSQL ergänzt.")
        if "materials" not in existing:
            conn.execute(text("ALTER TABLE public.work_plan_task ADD COLUMN materials TEXT"))
            print("Spalte 'work_plan_task.materials' wurde für PostgreSQL ergänzt.")
        if "child_goal" not in existing:
            conn.execute(text("ALTER TABLE public.work_plan_task ADD COLUMN child_goal TEXT"))
            print("Spalte 'work_plan_task.child_goal' wurde für PostgreSQL ergänzt.")
        conn.commit()


def _sqlite_add_missing_library_template_columns():
    engine = db.engine
    if engine.url.get_backend_name() != "sqlite":
        return

    with engine.connect() as conn:
        table_exists = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='class_task_template'")
        ).first()
        if not table_exists:
            return

        columns = {
            row["name"]
            for row in conn.execute(text("PRAGMA table_info(class_task_template)")).mappings().all()
        }
        if "learning_area" not in columns:
            conn.execute(text("ALTER TABLE class_task_template ADD COLUMN learning_area VARCHAR(50)"))
            print("Spalte 'class_task_template.learning_area' wurde ergänzt.")
        if "icon_name" not in columns:
            conn.execute(text("ALTER TABLE class_task_template ADD COLUMN icon_name VARCHAR(50)"))
            print("Spalte 'class_task_template.icon_name' wurde ergänzt.")
        conn.commit()


def _postgres_add_missing_library_template_columns():
    engine = db.engine
    if engine.url.get_backend_name() not in {"postgresql", "postgres"}:
        return

    with engine.connect() as conn:
        table_exists = conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'class_task_template'"
            )
        ).first()
        if not table_exists:
            return

        existing = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'class_task_template'"
                )
            ).all()
        }
        if "learning_area" not in existing:
            conn.execute(text("ALTER TABLE public.class_task_template ADD COLUMN learning_area VARCHAR(50)"))
            print("Spalte 'class_task_template.learning_area' wurde für PostgreSQL ergänzt.")
        if "icon_name" not in existing:
            conn.execute(text("ALTER TABLE public.class_task_template ADD COLUMN icon_name VARCHAR(50)"))
            print("Spalte 'class_task_template.icon_name' wurde für PostgreSQL ergänzt.")
        conn.commit()

# Wir aktivieren den "App Context", damit wir Zugriff auf die DB-Konfiguration haben
with app.app_context():
    print("--- Starte Datenbank-Update ---")
    print("Prüfe auf neue Tabellen...")
    
    # Dieser Befehl ist sicher: Er erstellt nur Tabellen, die FEHLEN.
    # Bestehende Tabellen (User, Schueler, Beobachtungen) bleiben unberührt!
    db.create_all()
    _sqlite_add_missing_user_name_columns()
    _sqlite_add_missing_foerderplan_creator_column()
    _sqlite_add_missing_schueler_geburtsdatum_column()
    _sqlite_add_missing_workplan_weeks_column()
    _sqlite_add_missing_workplan_task_columns()
    _sqlite_add_missing_library_template_columns()
    _postgres_add_missing_workplan_weeks_column()
    _postgres_add_missing_workplan_task_columns()
    _postgres_add_missing_library_template_columns()
    
    print("--- FERTIG! Die Datenbank wurde erweitert. ---")
    print("Ihre alten Daten sind sicher.")
