# Installationsanleitung (Windows & Linux)

Diese Anleitung beschreibt die Installation der App für:

- `Linux` mit `SQLite`
- `Linux` mit `PostgreSQL`
- `Windows` mit `SQLite`
- `Windows` mit `PostgreSQL`

Zusätzlich enthalten:

- Erstinitialisierung (`/setup` oder `repair.py`)
- Migration von `SQLite` nach `PostgreSQL`

## 1. Voraussetzungen

Benötigt:

- Python `3.12` (oder kompatibel)
- `pip`
- Projektordner mit dem Quellcode

Optional/je nach Setup:

- `venv` (empfohlen)
- PostgreSQL-Server (nur für PostgreSQL-Betrieb)
- `pg_dump` / `psql` (für Backup/Migration/Tests mit PostgreSQL)

## 2. Projekt vorbereiten

Die Anleitung geht davon aus, dass du das Projekt in einem **eigenen Ordner** eingerichtet hast (z. B. per ZIP-Entpacken oder Git-Clone).

Beispielordner:

- Linux: `~/apps/Kompetenz-Manager`
- Windows: `C:\Apps\Kompetenz-Manager`

### Linux (bash)

```bash
cd ~/apps/Kompetenz-Manager
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Windows (PowerShell)

```powershell
cd "C:\Pfad\zum\Kompetenz-Manager"
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Hinweis:

- Wenn PowerShell die Aktivierung blockiert, einmalig (als Nutzer) ausführen:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

## 3. Grundkonfiguration (für alle Varianten)

Die App nutzt Umgebungsvariablen.

Wichtige Variablen:

- `SECRET_KEY` (Pflicht für stabilen Betrieb)
- `PORT` (optional, Standard meist `5000`)
- `SESSION_COOKIE_NAME` (empfohlen eindeutig pro Instanz)
- `REMEMBER_COOKIE_NAME` (empfohlen eindeutig pro Instanz)
- `DATABASE_URL` (nur für PostgreSQL; wenn nicht gesetzt -> SQLite)

### Beispiel Linux (`.env.local`)

```bash
export PORT=5000
export SECRET_KEY='ein-langer-zufaelliger-geheimer-schluessel'
export SESSION_COOKIE_NAME='kompetenz_manager_session'
export REMEMBER_COOKIE_NAME='kompetenz_manager_remember'
```

### Beispiel Windows (PowerShell, temporär)

```powershell
$env:PORT = "5000"
$env:SECRET_KEY = "ein-langer-zufaelliger-geheimer-schluessel"
$env:SESSION_COOKIE_NAME = "kompetenz_manager_session"
$env:REMEMBER_COOKIE_NAME = "kompetenz_manager_remember"
```

## 4. Variante A: Betrieb mit SQLite (einfacher Start)

Wenn `DATABASE_URL` **nicht** gesetzt ist, nutzt die App automatisch SQLite.

Die DB liegt standardmäßig in:

- `schule.db` (Projektordner), falls vorhanden
- sonst `instance/schule.db`

### Linux starten

```bash
source .venv/bin/activate
./start.sh
```

Alternativ:

```bash
SECRET_KEY="..." python3 app.py
```

### Windows starten (PowerShell)

```powershell
.\.venv\Scripts\Activate.ps1
$env:SECRET_KEY = "..."
python .\app.py
```

### Erstinitialisierung (Admin anlegen)

#### Option 1: Setup-Route (einfach)

Linux (bash):

```bash
export ALLOW_SETUP_ROUTE=1
export SETUP_ADMIN_PASSWORD='MeinStartPasswort123'
./start.sh
```

Windows (PowerShell):

```powershell
$env:ALLOW_SETUP_ROUTE = "1"
$env:SETUP_ADMIN_PASSWORD = "MeinStartPasswort123"
python .\app.py
```

Dann lokal im Browser aufrufen:

- `http://127.0.0.1:5000/setup` (oder dein Port)

Danach:

- als `admin` einloggen
- `ALLOW_SETUP_ROUTE` wieder entfernen/deaktivieren

#### Option 2: Admin via Script anlegen/zurücksetzen

Linux:

```bash
export REPAIR_ADMIN_PASSWORD='MeinPasswort123'
python3 repair.py
```

Windows (PowerShell):

```powershell
$env:REPAIR_ADMIN_PASSWORD = "MeinPasswort123"
python .\repair.py
```

## 5. Variante B: Betrieb mit PostgreSQL

Die App unterstützt PostgreSQL über `DATABASE_URL`.

Beispiel:

```text
postgresql://USER:PASSWORT@HOST:5432/kompetenz_manager
```

Hinweis:

- `postgres://...` wird ebenfalls akzeptiert (intern normalisiert).

### 5.1 PostgreSQL einrichten (Linux)

Beispiel Ubuntu/Debian:

```bash
sudo apt update
sudo apt install postgresql postgresql-contrib
sudo systemctl status postgresql
```

Datenbank + Benutzer anlegen:

```bash
sudo -u postgres psql
```

In `psql`:

```sql
CREATE USER kompetenz_user WITH PASSWORD 'SEHR_STARKES_PASSWORT';
CREATE DATABASE kompetenz_manager OWNER kompetenz_user;
GRANT ALL PRIVILEGES ON DATABASE kompetenz_manager TO kompetenz_user;
\q
```

Verbindung testen:

```bash
psql "postgresql://kompetenz_user:SEHR_STARKES_PASSWORT@127.0.0.1:5432/kompetenz_manager" -c "SELECT version();"
```

### 5.2 PostgreSQL einrichten (Windows)

Empfohlen:

- PostgreSQL über den offiziellen Installer installieren
- `pgAdmin`/`psql` optional mitinstallieren

Danach Datenbank und Benutzer anlegen (z. B. via `psql` oder pgAdmin):

```sql
CREATE USER kompetenz_user WITH PASSWORD 'SEHR_STARKES_PASSWORT';
CREATE DATABASE kompetenz_manager OWNER kompetenz_user;
GRANT ALL PRIVILEGES ON DATABASE kompetenz_manager TO kompetenz_user;
```

### 5.3 App auf PostgreSQL konfigurieren

#### Linux (`.env.local`)

```bash
export PORT=5000
export SECRET_KEY='ein-langer-zufaelliger-geheimer-schluessel'
export SESSION_COOKIE_NAME='kompetenz_manager_session'
export REMEMBER_COOKIE_NAME='kompetenz_manager_remember'
export DATABASE_URL='postgresql://kompetenz_user:SEHR_STARKES_PASSWORT@127.0.0.1:5432/kompetenz_manager'
```

#### Windows (PowerShell)

```powershell
$env:SECRET_KEY = "ein-langer-zufaelliger-geheimer-schluessel"
$env:SESSION_COOKIE_NAME = "kompetenz_manager_session"
$env:REMEMBER_COOKIE_NAME = "kompetenz_manager_remember"
$env:DATABASE_URL = "postgresql://kompetenz_user:SEHR_STARKES_PASSWORT@127.0.0.1:5432/kompetenz_manager"
```

### 5.4 App starten (PostgreSQL)

Linux:

```bash
source .venv/bin/activate
./start.sh
```

Windows:

```powershell
.\.venv\Scripts\Activate.ps1
python .\app.py
```

Erstinitialisierung (`admin`) erfolgt wie bei SQLite über `/setup` oder `repair.py`.

## 6. Migration von SQLite nach PostgreSQL (bestehende Daten übernehmen)

Die App enthält ein Migrationsskript:

- `migrate_sqlite_to_postgres.py`

Es kopiert:

- Schüler
- Beobachtungen
- Förderpläne
- Elternkontakte
- Grundlagenblätter
- Benutzer
- weitere Tabellen mit IDs/Beziehungen

### Voraussetzungen

- Ziel-PostgreSQL-Datenbank ist leer
- `pip install -r requirements.txt` wurde ausgeführt

### Linux (bash)

```bash
source .venv/bin/activate
export TARGET_DATABASE_URL="postgresql://kompetenz_user:PASS@127.0.0.1:5432/kompetenz_manager"
python3 migrate_sqlite_to_postgres.py
```

Optional mit expliziter SQLite-Quelle:

```bash
SOURCE_SQLITE_PATH="/pfad/zur/schule.db" TARGET_DATABASE_URL="postgresql://..." python3 migrate_sqlite_to_postgres.py
```

### Windows (PowerShell)

```powershell
.\.venv\Scripts\Activate.ps1
$env:TARGET_DATABASE_URL = "postgresql://kompetenz_user:PASS@127.0.0.1:5432/kompetenz_manager"
python .\migrate_sqlite_to_postgres.py
```

Optional:

```powershell
$env:SOURCE_SQLITE_PATH = "C:\Pfad\zur\schule.db"
python .\migrate_sqlite_to_postgres.py
```

Nach erfolgreicher Migration:

- `DATABASE_URL` setzen
- App normal starten

## 7. Datenbank-Update bei SQLite (bestehende Instanzen)

Wenn neue Tabellen/Felder hinzugekommen sind:

Linux:

```bash
python3 update_db.py
```

Windows:

```powershell
python .\update_db.py
```

## 8. Mehrere Instanzen parallel betreiben (optional)

Wichtig pro Instanz:

- anderer `PORT`
- anderer `SECRET_KEY`
- anderer `SESSION_COOKIE_NAME`
- anderer `REMEMBER_COOKIE_NAME`

Beispiel:

- Instanz 1: `5000`, `kompetenz_manager_1_session`
- Instanz 2: `5001`, `kompetenz_manager_2_session`

Bei SQLite hat jede kopierte Instanz i. d. R. ihre eigene DB-Datei.

Bei PostgreSQL kannst du wählen:

- gemeinsame Datenbank (selten sinnvoll)
- eigene Datenbank pro Instanz (meist sinnvoller)

## 9. Typische Fehler und Lösungen

### Fehler: `sqlite3.OperationalError: unable to open database file`

Ursachen:

- falsches Startverzeichnis
- fehlender Schreibzugriff
- fehlender `instance/`-Ordner

Hinweis:

- Die App nutzt inzwischen robuste absolute SQLite-Fallbackpfade und legt `instance/` beim Start an.

### Fehler: `ModuleNotFoundError: No module named 'psycopg2'`

Ursache:

- PostgreSQL-Treiber fehlt im aktiven `venv`

Lösung:

```bash
pip install -r requirements.txt
```

### Fehler: Login klappt nach Neustart nicht mehr

Ursache:

- `SECRET_KEY` nicht fest gesetzt

Lösung:

- festen `SECRET_KEY` als Umgebungsvariable setzen

### Zwei Apps loggen sich gegenseitig aus

Ursache:

- gleiche Cookie-Namen auf `localhost`

Lösung:

- pro Instanz eigene Werte für:
  - `SESSION_COOKIE_NAME`
  - `REMEMBER_COOKIE_NAME`

## 10. Empfohlene erste Funktionstests

Nach der Installation kurz prüfen:

1. Login mit `admin`
2. Schülerliste / Import
3. Beobachtung erfassen (Schnelleintrag oder Ganzer Bogen)
4. Bericht öffnen
5. Förderplan erstellen / anzeigen
6. Elternkontakt-Protokoll anlegen

## 11. Sicherheitshinweis (wichtig bei Netzwerk/Internetbetrieb)

Für lokalen Testbetrieb reicht die Installation oben.

Wenn die App im Netzwerk oder Internet erreichbar sein soll:

- `SECRET_KEY` zwingend setzen
- `FLASK_DEBUG` deaktivieren
- besser `gunicorn` + Reverse Proxy (`nginx`/`caddy`)
- HTTPS verwenden
- Setup-Route deaktiviert lassen (`ALLOW_SETUP_ROUTE` nicht setzen)
