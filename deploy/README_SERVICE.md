# Produktionsinstallation als Service

Dieses Deploy-Set installiert KompetenzKompass als `systemd`-Service mit:

- `gunicorn` statt Flask-Entwicklungsserver
- Logdateien unter `/var/log/kompetenzkompass`
- zusätzlichem Journal-Logging über `systemd`
- täglichem Backup per `cron`
- Logrotation per `logrotate`

## Voraussetzungen

- Linux mit `systemd`
- root-Rechte für die Installation
- vorhandenes Projektverzeichnis inkl. `venv/`
- für PostgreSQL-Backups: `pg_dump`

## Installation

Im Projektordner:

```bash
sudo bash deploy/install_service.sh
```

Das Skript:

1. legt bei Bedarf einen Service-Benutzer an
2. installiert Python-Abhängigkeiten inkl. `gunicorn`
3. erzeugt eine Env-Datei unter `/etc/kompetenzkompass/kompetenzkompass.env`
4. richtet den `systemd`-Service ein
5. richtet ein tägliches Backup per `cron` ein
6. aktiviert `logrotate` für die erzeugten Logdateien

## Wichtige Pfade

- Service-Datei: `/etc/systemd/system/kompetenzkompass.service`
- Env-Datei: `/etc/kompetenzkompass/kompetenzkompass.env`
- Logs: `/var/log/kompetenzkompass`
- Backup-Cron: `/etc/cron.d/kompetenzkompass-backup`
- Backup-Wrapper: `/usr/local/bin/kompetenzkompass-backup`

## Wichtige Einstellungen

Die Env-Datei solltest du direkt nach der Installation prüfen:

```env
SECRET_KEY=bitte-aendern
DATABASE_URL=
SESSION_COOKIE_NAME=kompetenzkompass_session
REMEMBER_COOKIE_NAME=kompetenzkompass_remember
SESSION_COOKIE_SECURE=1
REMEMBER_COOKIE_SECURE=1
SESSION_COOKIE_SAMESITE=Lax
REMEMBER_COOKIE_SAMESITE=Lax
PREFERRED_URL_SCHEME=https
TRUST_REVERSE_PROXY=1
PROXY_FIX_X_FOR=1
PROXY_FIX_X_PROTO=1
PROXY_FIX_X_HOST=1
BACKUP_ROOT=/pfad/zur/app/backups
KEEP_DAYS=14
```

## Service prüfen

```bash
systemctl status kompetenzkompass
journalctl -u kompetenzkompass -n 100
tail -n 100 /var/log/kompetenzkompass/gunicorn-error.log
tail -n 100 /var/log/kompetenzkompass/gunicorn-access.log
```

## Backup prüfen

Manuell testen:

```bash
sudo /usr/local/bin/kompetenzkompass-backup
tail -n 100 /var/log/kompetenzkompass/backup.log
```

## Reverse Proxy

Der Service lauscht intern standardmäßig auf `127.0.0.1:8008`.
Für Netzwerk- oder Internetbetrieb sollte davor ein Reverse Proxy wie `nginx` oder `caddy` mit HTTPS stehen.
