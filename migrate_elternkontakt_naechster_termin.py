import re
import sqlite3
from datetime import datetime
from pathlib import Path

from app import app


ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _resolve_db_path() -> Path:
    # Flask legt relative SQLite-Dateien bei Flask-SQLAlchemy im instance-Ordner ab.
    return Path(app.instance_path) / "schule.db"


def _parse_legacy_date(value):
    if value is None:
        return None, None

    text = str(value).strip()
    if not text:
        return None, None

    if ISO_DATE_RE.match(text):
        return text, None

    for fmt in ("%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat(), None
        except ValueError:
            continue

    # Nicht maschinell konvertierbar: als Hinweis erhalten.
    return None, text


def main():
    db_path = _resolve_db_path()
    print(f"Verwende Datenbank: {db_path}")

    if not db_path.exists():
        print("Datenbank nicht gefunden. Abbruch.")
        return

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row

    try:
        cur = con.cursor()
        table_exists = cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='elternkontakt'"
        ).fetchone()
        if not table_exists:
            print("Tabelle 'elternkontakt' existiert noch nicht. Nichts zu migrieren.")
            print("Falls nötig zuerst: python update_db.py")
            return

        columns = cur.execute("PRAGMA table_info(elternkontakt)").fetchall()
        col_map = {row["name"]: row for row in columns}

        if "naechster_termin" not in col_map:
            print("Spalte 'naechster_termin' nicht vorhanden. Nichts zu migrieren.")
            return

        current_type = (col_map["naechster_termin"]["type"] or "").upper()
        print(f"Aktueller Spaltentyp naechster_termin: {current_type or '(leer)'}")

        if current_type == "DATE":
            print("Spaltentyp ist bereits DATE. Keine Migration erforderlich.")
            return

        rows = cur.execute(
            """
            SELECT
                id, datum, schueler_id, user_id, eintrag_typ, kontaktform, betreff, mitteilung,
                teilnehmende, gespraechsanlass, besprochenes,
                vereinbarungen_schule, vereinbarungen_eltern, naechste_schritte, naechster_termin
            FROM elternkontakt
            ORDER BY id
            """
        ).fetchall()

        print(f"Zu migrierende Datensätze: {len(rows)}")

        cur.execute("PRAGMA foreign_keys=OFF")
        cur.execute("BEGIN")

        cur.execute("ALTER TABLE elternkontakt RENAME TO elternkontakt_old_mig")

        cur.execute(
            """
            CREATE TABLE elternkontakt (
                id INTEGER PRIMARY KEY,
                datum DATETIME,
                schueler_id INTEGER NOT NULL,
                user_id INTEGER,
                eintrag_typ VARCHAR(20) NOT NULL,
                kontaktform VARCHAR(50),
                betreff VARCHAR(200),
                mitteilung TEXT,
                teilnehmende TEXT,
                gespraechsanlass TEXT,
                besprochenes TEXT,
                vereinbarungen_schule TEXT,
                vereinbarungen_eltern TEXT,
                naechste_schritte TEXT,
                naechster_termin DATE,
                FOREIGN KEY(schueler_id) REFERENCES schueler (id),
                FOREIGN KEY(user_id) REFERENCES user (id)
            )
            """
        )

        converted_count = 0
        preserved_hint_count = 0

        for row in rows:
            parsed_date, legacy_hint = _parse_legacy_date(row["naechster_termin"])

            next_steps = row["naechste_schritte"]
            if legacy_hint:
                hint = f"Migrierter Termin-Hinweis (nicht als Datum interpretierbar): {legacy_hint}"
                next_steps = f"{next_steps}\n\n{hint}" if next_steps else hint
                preserved_hint_count += 1
            elif parsed_date:
                converted_count += 1

            cur.execute(
                """
                INSERT INTO elternkontakt (
                    id, datum, schueler_id, user_id, eintrag_typ, kontaktform, betreff, mitteilung,
                    teilnehmende, gespraechsanlass, besprochenes,
                    vereinbarungen_schule, vereinbarungen_eltern, naechste_schritte, naechster_termin
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    row["datum"],
                    row["schueler_id"],
                    row["user_id"],
                    row["eintrag_typ"],
                    row["kontaktform"],
                    row["betreff"],
                    row["mitteilung"],
                    row["teilnehmende"],
                    row["gespraechsanlass"],
                    row["besprochenes"],
                    row["vereinbarungen_schule"],
                    row["vereinbarungen_eltern"],
                    next_steps,
                    parsed_date,
                ),
            )

        cur.execute("DROP TABLE elternkontakt_old_mig")
        cur.execute("COMMIT")
        cur.execute("PRAGMA foreign_keys=ON")

        print("Migration erfolgreich.")
        print(f"Konvertierte Termine: {converted_count}")
        print(f"Nicht konvertierbare Termintexte als Hinweis erhalten: {preserved_hint_count}")

    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    with app.app_context():
        main()
