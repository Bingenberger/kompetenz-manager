# Kompetenz-Manager

Flask-Anwendung zur Erfassung von Beobachtungen, Berichten und Förderplänen im Schulkontext.

## Voraussetzungen

- Python 3.12 (oder kompatibel)
- vorhandenes `venv/` im Projektordner (optional, aber empfohlen)

## Installation

```bash
cd "/home/emrichschule/GGScloud/Schulverwaltung/Documents/Apps/Kompetenz-Manager"
source venv/bin/activate
pip install -r requirements.txt
```

## App starten (Entwicklung)

```bash
SECRET_KEY="ein-langer-zufaelliger-wert" FLASK_DEBUG=1 python app.py
```

Dann im Browser öffnen:

- `http://127.0.0.1:5000`

### Wenn Port 5000 belegt ist

```bash
PORT=5001 SECRET_KEY="ein-langer-zufaelliger-wert" FLASK_DEBUG=1 python app.py
```

Dann:

- `http://127.0.0.1:5001`

## Datenbank / Erstinitialisierung

### Datenbank-Backend wählen (SQLite oder PostgreSQL)

Standardmäßig nutzt die App SQLite (automatisch `schule.db` oder `instance/schule.db`).

Für PostgreSQL einfach `DATABASE_URL` setzen:

```bash
export DATABASE_URL="postgresql://USER:PASSWORT@HOST:5432/kompetenz_manager"
```

Hinweis:
- `postgres://...` wird ebenfalls akzeptiert und intern auf `postgresql://...` umgestellt.
- Für PostgreSQL bitte vorher `pip install -r requirements.txt` ausführen (enthält `psycopg`).

### Bestehende Datenbank erweitern (fehlende Tabellen anlegen)

```bash
python update_db.py
```

### Migration: SQLite -> PostgreSQL (komplette Datenübernahme)

1. Leere PostgreSQL-Datenbank anlegen
2. Ziel-URL setzen
3. Migrationsskript starten

```bash
export TARGET_DATABASE_URL="postgresql://USER:PASSWORT@HOST:5432/kompetenz_manager"
python migrate_sqlite_to_postgres.py
```

Optional: Quelle explizit setzen (falls nicht `schule.db` / `instance/schule.db`)

```bash
SOURCE_SQLITE_PATH="/pfad/zur/schule.db" TARGET_DATABASE_URL="postgresql://..." python migrate_sqlite_to_postgres.py
```

Danach die App mit PostgreSQL starten:

```bash
DATABASE_URL="postgresql://USER:PASSWORT@HOST:5432/kompetenz_manager" SECRET_KEY="..." python app.py
```

### Spezielle Migration: `Elternkontakt.naechster_termin` auf Datum umstellen

Nur nötig bei bereits vorhandener `elternkontakt`-Tabelle aus einer älteren Version.

```bash
python migrate_elternkontakt_naechster_termin.py
```

Hinweis:
- Das Skript versucht alte Termintexte zu Datumswerten umzuwandeln (`YYYY-MM-DD`, `DD.MM.YYYY`).
- Nicht konvertierbare Texte werden als Hinweis in `naechste_schritte` erhalten.

### Admin-Passwort setzen/zurücksetzen

```bash
python repair.py
```

Optional mit festem Passwort:

```bash
REPAIR_ADMIN_PASSWORD="MeinPasswort123" python repair.py
```

### `/setup`-Route (nur lokal, standardmäßig deaktiviert)

Nur für lokale Initialisierung auf dem Server:

```bash
ALLOW_SETUP_ROUTE=1 SETUP_ADMIN_PASSWORD="MeinStartPasswort123" SECRET_KEY="ein-langer-zufaelliger-wert" FLASK_DEBUG=1 python app.py
```

Dann lokal aufrufen:

```bash
curl http://127.0.0.1:5000/setup
```

## Tests

### Syntaxcheck

```bash
python3 -m py_compile app.py authz.py routes/*.py models.py extensions.py csrf_protection.py uploads.py db_utils.py time_utils.py tests/test_critical_flows.py
```

### Unit-/Integrationstests (kritische Flows)

```bash
venv/bin/python -m unittest tests/test_critical_flows.py
```

Aktuell abgedeckt:

- Login erfolgreich
- Admin kann Benutzer löschen
- Nicht-Admin darf Schülergrunddaten nicht löschen
- CSRF ohne Token wird blockiert
- Förderplan erstellen + evaluieren

## Projektstruktur (kurz)

- `app.py`: App-Factory (`create_app`) + Bootstrap
- `routes/`: Blueprints/Routenmodule (`auth`, `system`, `admin`, `erfassung`, `report`, `foerderplan`)
- `models.py`: SQLAlchemy-Modelle
- `extensions.py`: `db`, `login_manager`
- `csrf_protection.py`: CSRF-Schutz für Formulare
- `authz.py`: Rechte-Helfer (`admin_required`)
- `uploads.py`: Bild-Upload/Komprimierung
- `tests/`: automatisierte Tests

## UI / Design

- `DESIGN.md`: Farbpalette, wiederverwendbare UI-Klassen, Bewertungs-Komponenten (`templates/includes/*`) und Responsive-Leitlinien

## Hinweise

- Die App nutzt aktuell SQLite (`instance/schule.db`).
- CSRF-Schutz ist für klassische HTML-Formulare aktiv.
- Für Produktion `SECRET_KEY` immer explizit setzen und `FLASK_DEBUG` deaktivieren.
