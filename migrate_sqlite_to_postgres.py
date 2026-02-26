from pathlib import Path
import os
import sys

from sqlalchemy import MetaData, create_engine, inspect, select, text

from app import create_app
from extensions import db


def _normalize_db_url(raw_url):
    if not raw_url:
        return None
    url = raw_url.strip()
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://"):]
    return url


def _detect_sqlite_path():
    env_path = (os.environ.get("SOURCE_SQLITE_PATH") or "").strip()
    candidates = [Path(env_path)] if env_path else []
    candidates.extend([Path("schule.db"), Path("instance/schule.db")])
    for p in candidates:
        if p and p.exists():
            return p
    return candidates[0] if candidates else Path("schule.db")


def _is_target_empty(engine):
    insp = inspect(engine)
    with engine.connect() as conn:
        for table_name in insp.get_table_names():
            row = conn.execute(text(f'SELECT 1 FROM "{table_name}" LIMIT 1'))
            if row.first():
                return False, table_name
    return True, None


def _reset_postgres_sequences(engine, table_names):
    with engine.begin() as conn:
        for table_name in table_names:
            seq_name = conn.execute(
                text("SELECT pg_get_serial_sequence(:table_name, 'id')"),
                {"table_name": table_name},
            ).scalar()
            if not seq_name:
                continue
            max_id = conn.execute(text(f'SELECT COALESCE(MAX(id), 0) FROM "{table_name}"')).scalar() or 0
            if max_id <= 0:
                continue
            conn.execute(text("SELECT setval(:seq_name, :max_id, true)"), {"seq_name": seq_name, "max_id": max_id})


def main():
    target_url = _normalize_db_url(os.environ.get("TARGET_DATABASE_URL") or os.environ.get("DATABASE_URL"))
    if not target_url:
        print("Fehler: TARGET_DATABASE_URL oder DATABASE_URL muss gesetzt sein (PostgreSQL).", file=sys.stderr)
        sys.exit(1)
    if not target_url.startswith("postgresql"):
        print("Fehler: Ziel muss PostgreSQL sein (DATABASE_URL beginnt nicht mit postgresql://).", file=sys.stderr)
        sys.exit(1)

    sqlite_path = _detect_sqlite_path()
    if not sqlite_path.exists():
        print(f"Fehler: SQLite-Datei nicht gefunden: {sqlite_path}", file=sys.stderr)
        sys.exit(1)

    source_engine = create_engine(f"sqlite:///{sqlite_path}")
    target_app = create_app({"SQLALCHEMY_DATABASE_URI": target_url})

    target_label = target_url.split("@")[-1] if "@" in target_url else "postgresql://<ohne-hostanzeige>"
    print(f"Quelle (SQLite): {sqlite_path}")
    print(f"Ziel (PostgreSQL): {target_label}")

    with target_app.app_context():
        db.create_all()
        target_engine = db.engine

        empty, nonempty_table = _is_target_empty(target_engine)
        if not empty:
            print(
                f"Fehler: Ziel-Datenbank ist nicht leer (erste belegte Tabelle: {nonempty_table}). "
                "Bitte leere Datenbank verwenden.",
                file=sys.stderr,
            )
            sys.exit(1)

        source_meta = MetaData()
        source_meta.reflect(bind=source_engine)

        target_tables = [t for t in db.metadata.sorted_tables if t.name in source_meta.tables]
        if not target_tables:
            print("Keine gemeinsamen Tabellen gefunden. Abbruch.", file=sys.stderr)
            sys.exit(1)

        print("Starte Migration ...")
        with source_engine.connect() as src_conn, target_engine.begin() as dst_conn:
            for table in target_tables:
                src_table = source_meta.tables[table.name]
                rows = src_conn.execute(select(src_table)).mappings().all()
                if not rows:
                    print(f"- {table.name}: 0 Zeilen")
                    continue
                payload = [dict(row) for row in rows]
                dst_conn.execute(table.insert(), payload)
                print(f"- {table.name}: {len(payload)} Zeilen")

        _reset_postgres_sequences(target_engine, [t.name for t in target_tables])
        print("Sequenzen aktualisiert.")
        print("Migration abgeschlossen.")


if __name__ == "__main__":
    main()
