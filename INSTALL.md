# Installation von KompetenzKompass

Diese Anleitung beschreibt die Einrichtung fuer Entwicklung, lokalen Schulbetrieb und produktiven Serverbetrieb.

## 1. Voraussetzungen

Benoetigt werden:

- Python 3.12 oder kompatibel
- `pip`
- empfohlen: virtuelle Umgebung mit `venv`
- optional: PostgreSQL
- optional: LibreOffice fuer PDF-Exporte

## 2. Projekt vorbereiten

### Linux

```bash
cd /pfad/zum/KompetenzKompass
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Windows PowerShell

```powershell
cd "C:\Pfad\zu\KompetenzKompass"
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Falls PowerShell die Aktivierung blockiert:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

## 3. Grundkonfiguration

Die App verwendet Umgebungsvariablen.

Wichtige Variablen:

- `SECRET_KEY`
- `DATABASE_URL`
- `SESSION_COOKIE_NAME`
- `REMEMBER_COOKIE_NAME`
- `FLASK_DEBUG`

Beispiel:

```env
SECRET_KEY=ein-langer-geheimer-wert
DATABASE_URL=
SESSION_COOKIE_NAME=kompetenzkompass_session
REMEMBER_COOKIE_NAME=kompetenzkompass_remember
FLASK_DEBUG=0
```

Wenn `DATABASE_URL` nicht gesetzt ist, verwendet die App automatisch SQLite.

## 4. Entwicklungsbetrieb

Minimalstart:

```bash
SECRET_KEY="ein-langer-geheimer-wert" FLASK_DEBUG=1 python app.py
```

Alternativ mit vorhandener Startdatei:

```bash
./start.sh
```

## 5. Datenbank

### SQLite

Die SQLite-Datenbank liegt je nach Bestand typischerweise hier:

- `schule.db` im Projektordner
- oder `instance/schule.db`

SQLite ist fuer kleine Installationen und lokale Tests geeignet.

### PostgreSQL

Beispiel fuer `DATABASE_URL`:

```env
DATABASE_URL=postgresql://USER:PASSWORT@127.0.0.1:5432/kompetenz_manager
```

Hinweis:

- `postgres://...` wird intern auf `postgresql://...` normalisiert.

#### PostgreSQL unter Linux anlegen

```bash
sudo apt update
sudo apt install postgresql postgresql-contrib
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

#### PostgreSQL unter Windows

Empfohlen:

- PostgreSQL ueber den offiziellen Installer installieren
- Datenbank und Benutzer anschliessend per `psql` oder `pgAdmin` anlegen

## 6. Erstinitialisierung

### Setup-Route

Die Setup-Route ist nur fuer die Erstinitialisierung gedacht. In aktuellen Versionen gilt:

- nur lokal am Server
- zusaetzlich mit Token

Beispiel:

```env
ALLOW_SETUP_ROUTE=1
SETUP_ROUTE_TOKEN=ein-zufaelliger-token
SETUP_ADMIN_PASSWORD=StartPasswort123!
```

Aufruf lokal am Server:

```text
http://127.0.0.1:5000/setup?token=ein-zufaelliger-token
```

Danach `ALLOW_SETUP_ROUTE` wieder entfernen.

### Admin-Passwort reparieren

Fuer bestehende Installationen kann das Admin-Passwort ueber `repair.py` oder per einmaligem Python-Skript im App-Kontext zurueckgesetzt werden.

## 7. Produktionsbetrieb als Service

Fuer Linux-Server liegt ein Installationsskript bei:

```bash
sudo bash deploy/install_service.sh
```

Das Skript richtet ein:

- `gunicorn`
- `systemd`
- Logdateien unter `/var/log/kompetenzkompass`
- taegliche Backups per `cron`
- `logrotate`

Weitere Details:

- [deploy/README_SERVICE.md](deploy/README_SERVICE.md)

## 8. Empfohlene Sicherheitsvariablen fuer Internetbetrieb

Bei Betrieb hinter einem Reverse Proxy:

```env
SESSION_COOKIE_SECURE=1
REMEMBER_COOKIE_SECURE=1
SESSION_COOKIE_SAMESITE=Lax
REMEMBER_COOKIE_SAMESITE=Lax
PREFERRED_URL_SCHEME=https
TRUST_REVERSE_PROXY=1
PROXY_FIX_X_FOR=1
PROXY_FIX_X_PROTO=1
PROXY_FIX_X_HOST=1
```

Zusaetzlich empfohlen:

- `FLASK_DEBUG=0`
- `SECRET_KEY` fest und lang setzen
- `ALLOW_SETUP_ROUTE` im Normalbetrieb nicht setzen

## 9. Reverse Proxy und LAN-Betrieb

Typisches Setup:

- App auf einer VM oder einem internen Server
- Reverse Proxy auf einem zweiten Server
- externer Zugriff nur ueber HTTPS am Proxy
- App-Port in der Firewall nur fuer den Proxyserver freigeben

Der Service kann intern z. B. auf folgenden Adressen lauschen:

- `127.0.0.1:8008`
- oder auf der LAN-IP der App-VM

Fuer Reverse-Proxy-Betrieb muessen die Proxy-Header korrekt gesetzt und die obigen Proxy-Variablen in der Env-Datei aktiviert sein.

## 10. Uploads und geschuetzte Medien

Neuere Versionen speichern Uploads geschuetzt unter:

- `instance/protected_uploads`

Aeltere Installationen koennen noch Dateien in `static/uploads` enthalten.

Fuer die Uebernahme gibt es:

```bash
python migrate_uploads_to_protected.py
```

Optional:

```bash
python migrate_uploads_to_protected.py --move
```

### Verwaiste Upload-Dateien

Bis zur Nachbesserung der Loeschpfade entfernte die Anwendung keine hochgeladene Datei. In gewachsenen Instanzen liegen deshalb Fotos und PDFs ohne Datenbankbezug.

Bericht (veraendert nichts):

```bash
python cleanup_orphan_uploads.py
```

Loeschen nach Rueckfrage:

```bash
python cleanup_orphan_uploads.py --delete
```

Optionen: `--yes` ueberspringt die Rueckfrage, `--limit` steuert die Laenge der Auflistung. Der Lauf muss mit derselben `DATABASE_URL` laufen wie der Dienst; enthaelt die Datenbank keinen einzigen Dateiverweis, bricht er ab.

## 11. Migration von SQLite nach PostgreSQL

Das Projekt enthaelt ein Migrationsskript:

- `migrate_sqlite_to_postgres.py`

Beispiel unter Linux:

```bash
source .venv/bin/activate
export TARGET_DATABASE_URL="postgresql://kompetenz_user:PASS@127.0.0.1:5432/kompetenz_manager"
python3 migrate_sqlite_to_postgres.py
```

Optional mit expliziter SQLite-Quelle:

```bash
SOURCE_SQLITE_PATH="/pfad/zur/schule.db" TARGET_DATABASE_URL="postgresql://..." python3 migrate_sqlite_to_postgres.py
```

## 12. Backups

Das Projekt enthaelt ein Backup-Skript:

- `backup_external.sh`

Es sichert:

- SQLite oder PostgreSQL
- Legacy-Uploads unter `static/uploads`
- geschuetzte Uploads unter `instance/protected_uploads`

## 12a. Upgrade für den Schuljahreswechsel

Vor dem Einspielen auf einer bestehenden Instanz zuerst ein Datenbank- und Upload-Backup erstellen. Danach im aktualisierten Projektverzeichnis:

```bash
source venv/bin/activate
python update_db.py
python -m unittest tests.test_critical_flows tests.test_workplan_flows -q
sudo systemctl restart kompetenzkompass
```

`update_db.py` ergänzt die Archivfelder für Schüler, den Beginn des aktiven Schuljahres und die Protokolltabelle für Schuljahreswechsel. Das Skript ist wiederholt ausführbar und unterstützt SQLite sowie PostgreSQL. Der Wechsel selbst wird anschließend im Adminbereich unter **Schuljahr & Termine -> Schuljahreswechsel** vorbereitet und bestätigt.


## 13. Tests

Vor Deployments empfohlen:

```bash
./venv/bin/python -m unittest tests.test_critical_flows -v
./venv/bin/python -m unittest tests.test_workplan_flows -v
```

## 14. Updates aus dem Git-Repository

Produktive Instanzen werden nicht per Dateikopie aktualisiert, sondern ziehen ihren Stand aus dem Repository. Das Update wird immer auf dem Server ausgeloest, GitHub braucht keinen Zugriff auf den Server.

Einmalige Umstellung einer bestehenden, manuell kopierten Installation:

```bash
cd /pfad/zur/app
bash deploy/adopt_git.sh
```

Das Skript legt einen Voll-Snapshot an, richtet die Git-Arbeitskopie ein, laesst den Arbeitsbaum unveraendert und zeigt anschliessend die Abweichungen zum Repository.

Laufende Updates:

```bash
bash deploy/update.sh --dry-run   # anzeigen, was kaeme
bash deploy/update.sh             # einspielen
```

Der Ablauf umfasst Backup, Fast-Forward-Update, Abhaengigkeiten, `update_db.py`, Regressionstests, Dienstneustart und Health-Check, mit automatischem Rollback des Codestands bei Fehlern.

Ausfuehrliche Beschreibung:

- [deploy/README_DEPLOY.md](deploy/README_DEPLOY.md)

## 15. Wichtige Dateien und Verzeichnisse

- `app.py`: App-Konfiguration
- `update_db.py`: ergaenzt fehlende Tabellen und Spalten
- `repair.py`: Reparatur- und Hilfsskript
- `migrate_sqlite_to_postgres.py`: Datenmigration
- `migrate_uploads_to_protected.py`: Upload-Migration
- `cleanup_orphan_uploads.py`: entfernt Uploads ohne Datenbankbezug
- `competency_matrix.py`: Berechnung der Klassenuebersicht
- `deploy/`: Service- und Betriebsdateien
- `static/vendor/`: lokal ausgelieferte Fremdbestandteile (Bootstrap, Schriften) inkl. Anleitung zum Aktualisieren
- `odt_templates/`: Exportvorlagen

## 16. Hinweise fuer Repository und Weitergabe

Nicht ins Repository gehoeren typischerweise:

- produktive `.env`-Dateien
- Datenbanken
- Upload-Dateien
- lokale Backups
- SQL-Dumps
- Log-Dateien

Die `.gitignore` im Projekt deckt diese Faelle ab.
