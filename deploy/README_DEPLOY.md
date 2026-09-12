# Updates über Git

Der produktive Server wird nicht mehr per Dateikopie aktualisiert, sondern zieht
seinen Stand aus dem GitHub-Repository. Ausgelöst wird das immer **auf dem
Server** (Pull-Prinzip) — GitHub braucht keinerlei Zugriff auf die Schulserver.

Repository: <https://github.com/Bingenberger/kompetenz-manager>

## Überblick

| Skript | Zweck | Häufigkeit |
| --- | --- | --- |
| `deploy/install_service.sh` | Erstinstallation als systemd-Dienst | einmalig |
| `deploy/adopt_git.sh` | bestehende, manuell kopierte Installation auf Git umstellen | einmalig |
| `deploy/update.sh` | Update einspielen | bei jedem Release |

---

## 1. Einmalige Umstellung einer bestehenden Installation

Auf dem Produktivserver, im App-Verzeichnis, **als Besitzer des Verzeichnisses**
(nicht als root):

```bash
cd /pfad/zur/app
bash deploy/adopt_git.sh
```

Das Skript

1. legt einen vollständigen Snapshot des Verzeichnisses unter `backups/` an
   (Code, Datenbank, Uploads, Konfiguration — ohne `venv/`),
2. richtet `.git` ein und setzt `HEAD` auf `origin/main`,
3. **verändert dabei keine einzige Datei im Arbeitsbaum**,
4. zeigt anschließend über `git status`, wo der Server vom Repository abweicht.

Danach entscheidest du, welcher Stand gilt:

```bash
# A) Repository-Stand übernehmen (Regelfall)
git diff                 # prüfen, was verloren geht
git checkout -f -- .
bash deploy/update.sh

# B) Serverstand behalten und ins Repository überführen
git add -A && git commit -m "Serverstand übernommen"
git push origin main
```

Datenbank, Uploads, `venv/` und `.env.local` sind über `.gitignore` ausgenommen
und werden in keinem Fall angefasst.

---

## 2. Laufende Updates

```bash
cd /pfad/zur/app
bash deploy/update.sh
```

Ablauf:

1. **Vorbedingungen** — Git-Arbeitskopie vorhanden, Arbeitsbaum sauber, richtiger
   Benutzer, `venv/` vorhanden.
2. **Repository abfragen** — zeigt aktuellen und Ziel-Commit, die eingehenden
   Commits und den Diffstat, dann Rückfrage.
3. **Backup** — `backup_external.sh` sichert Datenbank (SQLite oder PostgreSQL)
   und Uploads nach `backups/`.
4. **Fast-Forward** auf `origin/main`.
5. **Abhängigkeiten** — `pip install -r requirements.txt`, aber nur wenn sich
   `requirements.txt` tatsächlich geändert hat.
6. **Schema-Migration** — `update_db.py`.
7. **Regressionstests** — laufen gegen eine temporäre SQLite-Datenbank und
   berühren die Produktivdaten nicht.
8. **Neustart + Health-Check** — `systemctl restart` und HTTP-Prüfung gegen
   `/login`.

### Automatischer Rollback

Schlägt ab Schritt 4 etwas fehl — Migration, Tests, Neustart oder Health-Check —
setzt das Skript den Code automatisch auf den vorherigen Commit zurück,
installiert die alten Abhängigkeiten und startet den Dienst wieder.

Schema-Änderungen werden dabei **nicht** zurückgenommen. `update_db.py` ergänzt
nur Tabellen und Spalten und ist deshalb mit dem älteren Code verträglich. Muss
die Datenbank dennoch zurück, liegt das Backup aus Schritt 3 unter `backups/`.

### Optionen

```bash
bash deploy/update.sh --dry-run      # nur anzeigen, was käme
bash deploy/update.sh --yes          # ohne Rückfrage (z. B. für Cron)
bash deploy/update.sh --ref v1.4.0   # auf einen bestimmten Tag aktualisieren
bash deploy/update.sh --skip-tests   # Tests überspringen (nicht empfohlen)
bash deploy/update.sh --allow-dirty  # lokale Serveränderungen verwerfen
bash deploy/update.sh --no-restart   # ohne Dienstneustart
```

Anpassbar per Umgebungsvariable:

| Variable | Standard |
| --- | --- |
| `APP_NAME` / `SERVICE_NAME` | `kompetenzkompass` |
| `ENV_FILE` | `/etc/kompetenzkompass/kompetenzkompass.env` |
| `BRANCH` | `main` |
| `HEALTH_URL` | `http://127.0.0.1:8008/login` |

Beispiel für eine abweichende Instanz:

```bash
SERVICE_NAME=kompetenz-manager HEALTH_URL=http://127.0.0.1:5001/login \
  bash deploy/update.sh
```

---

## 3. Benutzer und Rechte

`update.sh` muss als **Besitzer des App-Verzeichnisses** laufen, sonst entstehen
Dateien mit falschem Eigentümer und der Dienst kann sie später nicht mehr lesen.
Das Skript bricht in dem Fall mit einem Hinweis ab.

Für `systemctl restart` wird `sudo` verwendet. Damit das ohne Passwortabfrage
funktioniert, kann eine gezielte sudo-Regel hinterlegt werden
(`sudo visudo -f /etc/sudoers.d/kompetenzkompass`):

```
appbenutzer ALL=(root) NOPASSWD: /bin/systemctl restart kompetenzkompass.service
```

---

## 4. Release-Ablauf

Auf dem Entwicklungsrechner:

```bash
git add -A
git commit -m "Kurze Beschreibung der Änderung"
git push origin main
```

Optional einen Tag setzen, um auf dem Server gezielt darauf aktualisieren zu
können:

```bash
git tag -a v1.4.0 -m "Schuljahreswechsel"
git push origin v1.4.0
```

Auf dem Server:

```bash
cd /pfad/zur/app
bash deploy/update.sh --dry-run   # Sichtprüfung
bash deploy/update.sh
```

---

## 5. Manueller Rückweg

Falls ein Update im Nachhinein zurückgenommen werden soll:

```bash
cd /pfad/zur/app
git log --oneline -10                 # Ziel-Commit heraussuchen
bash deploy/update.sh --ref <commit>  # Fast-Forward-only, geht nur vorwärts
```

Für einen echten Rückschritt auf einen älteren Commit:

```bash
git reset --hard <commit>
./venv/bin/pip install -r requirements.txt
./venv/bin/python update_db.py
sudo systemctl restart kompetenzkompass
```

Muss zusätzlich die Datenbank zurück, das passende Verzeichnis unter `backups/`
verwenden (`manifest.txt` nennt Zeitpunkt und Datenbanktyp).
