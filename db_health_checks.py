from sqlalchemy import text

from extensions import db


def warn_if_elternkontakt_migration_needed():
    """Checks legacy SQLite schema and prints a startup warning if migration is needed."""
    engine = db.engine
    if engine.url.get_backend_name() != "sqlite":
        return

    try:
        with engine.connect() as conn:
            table_exists = conn.execute(
                text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='elternkontakt'")
            ).first()
            if not table_exists:
                return

            pragma_rows = conn.execute(text("PRAGMA table_info(elternkontakt)")).mappings().all()
            col = next((row for row in pragma_rows if row["name"] == "naechster_termin"), None)
            if not col:
                return

            col_type = (col.get("type") or "").upper()
            if col_type == "DATE":
                return

            print()
            print("! DB-Hinweis: Migration empfohlen")
            print("  Tabelle 'elternkontakt' verwendet fuer 'naechster_termin' noch nicht den Typ DATE.")
            print(f"  Aktueller Typ: {col_type or '(leer)'}")
            print("  Bitte einmal ausfuehren:")
            print("    python migrate_elternkontakt_naechster_termin.py")
            print()
    except Exception as exc:
        # Startup should continue even if the diagnostic check fails.
        print(f"DB-Check Warnung (ignorierbar): {exc}")


def warn_if_user_name_columns_missing():
    """Checks legacy SQLite schema and prints a startup warning if user name columns are missing."""
    engine = db.engine
    if engine.url.get_backend_name() != "sqlite":
        return

    try:
        with engine.connect() as conn:
            table_exists = conn.execute(
                text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='user'")
            ).first()
            if not table_exists:
                return

            pragma_rows = conn.execute(text("PRAGMA table_info(user)")).mappings().all()
            names = {row["name"] for row in pragma_rows}
            missing = [col for col in ("vorname", "nachname") if col not in names]
            if not missing:
                return

            print()
            print("! DB-Hinweis: Update empfohlen")
            print("  Tabelle 'user' hat noch nicht alle Namensspalten fuer das Benutzermenue.")
            print(f"  Fehlend: {', '.join(missing)}")
            print("  Bitte einmal ausfuehren:")
            print("    python update_db.py")
            print()
    except Exception as exc:
        print(f"DB-Check Warnung (ignorierbar): {exc}")


def warn_if_foerderplan_creator_column_missing():
    """Checks legacy SQLite schema and warns if foerderplan.creator_user_id is missing."""
    engine = db.engine
    if engine.url.get_backend_name() != "sqlite":
        return

    try:
        with engine.connect() as conn:
            table_exists = conn.execute(
                text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='foerderplan'")
            ).first()
            if not table_exists:
                return

            pragma_rows = conn.execute(text("PRAGMA table_info(foerderplan)")).mappings().all()
            names = {row["name"] for row in pragma_rows}
            if "creator_user_id" in names:
                return

            print()
            print("! DB-Hinweis: Update empfohlen")
            print("  Tabelle 'foerderplan' hat noch keine Spalte 'creator_user_id'.")
            print("  Die Filterung der Förderplan-Liste nach Ersteller/Klassenleitung ist sonst unvollständig.")
            print("  Bitte einmal ausfuehren:")
            print("    python update_db.py")
            print()
    except Exception as exc:
        print(f"DB-Check Warnung (ignorierbar): {exc}")
