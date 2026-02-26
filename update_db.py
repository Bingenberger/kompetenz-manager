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
    
    print("--- FERTIG! Die Datenbank wurde erweitert. ---")
    print("Ihre alten Daten sind sicher.")
