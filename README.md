# KompetenzKompass

KompetenzKompass ist eine Flask-Anwendung fuer Grundschulen und andere schulische Kontexte, in denen Beobachtungen, Foerderplanung, Arbeitsplaene, Elternkontakte und paedagogische Fallarbeit an einem Ort dokumentiert werden sollen.

Die Anwendung ist fuer den praktischen Schulalltag gebaut: klassische Server-Templates statt SPA-Komplexitaet, strukturierte Arbeitsablaeufe fuer Lehrkraefte und Exporte fuer Gespraeche, Dokumentation und Weiterarbeit.

<!-- schulplattform:installation -->
## Installation

### Im Rahmen der Schulplattform

Empfohlen für den Schulserver: Die [Schulplattform](https://github.com/Bingenberger/schulplattform) installiert diese App zusammen mit den
anderen Schul-Apps auf einem leeren Ubuntu-24.04-Server, mit eigener Subdomain, TLS-Zertifikat, Mailversand,
Sicherung und Dashboard. Sie verwendet das signierte Release-Image `ghcr.io/bingenberger/kompetenz-manager`
und die unveränderte `docker-compose.yml` dieses Repositorys plus eine Override-Datei.

1. In der Plattformkonfiguration (`./install.sh configure` bzw. `/etc/schulplattform/platform.toml`)
   unter `[apps.kompetenz-manager]` `enabled = true` und den Hostnamen setzen, z. B. `kompetenz.ihre-schule.de`,
   und beim DNS-Provider einen A-Eintrag auf den Server anlegen.
2. `./install.sh apply …` installiert die App. Migration, Proxy, Zertifikat und Sicherung übernimmt die Plattform.
3. `./install.sh app-setup kompetenz-manager` legt die persönliche Erstadministration an (`python km_setup.py NAME`, Passwort am Terminal).
4. Daten vom bisherigen Server übernehmen: `./install.sh import kompetenz-manager …` nach dem
   [Umzugs-Runbook](https://github.com/Bingenberger/schulplattform/blob/main/docs/betrieb/umzug.md).

### Standalone mit Docker Compose

Ohne Plattform, z. B. zum Ausprobieren oder für einen eigenen Server mit eigenem Proxy.
Benötigt Docker Engine mit Compose v2 (amd64). Die App lauscht danach nur auf `http://127.0.0.1:8008`.

```sh
python3 prepare_secret.py
export KM_SECRET_FILE="$PWD/.private/session-key"
export APP_BASE_URL=http://localhost:8008 SESSION_COOKIE_SECURE=0   # nur lokal ohne TLS
docker compose build app
docker compose run --rm --no-deps app python -m km_migrate
docker compose run --rm --no-deps app python km_setup.py persoenlicher.name
docker compose up -d --wait
```

Statt selbst zu bauen, kann das veröffentlichte Image verwendet werden (dann `docker compose build` weglassen):

```sh
docker pull ghcr.io/bingenberger/kompetenz-manager:0.1.0 && docker tag ghcr.io/bingenberger/kompetenz-manager:0.1.0 kompetenz-manager:0.1.0-dev
```
<!-- /schulplattform:installation -->

## Funktionsumfang

- Beobachtungsboegen verwalten und ausfuellen
- Schnelleintraege, Klassendurchlaeufe und komplette Bogen-Erfassung
- Berichte pro Kind und Beobachtungsbogen
- Klassenuebersicht als Matrix: alle Kinder einer Klasse mal alle Kompetenzen eines Bogens
- Elternkontakte als Notiz oder Protokoll dokumentieren
- Elterngespraeche vorbereiten und durchfuehren
- Foerderplaene anlegen, evaluieren und fortschreiben
- Individuelle Arbeitsplaene mit Aufgabenbibliothek, Evaluation und Export
- Schuelerakte als gebuendelte Uebersicht pro Kind
- Suche in der Navigationsleiste ueber Kinder, Kompetenzen und Boegen
- Erzieherische Ereignisse mit Konsequenzen, Zustaendigkeit, Anhaengen und Journal
- Aenderungsprotokoll auch fuer Foerderplaene und Elternkontakte
- PDF- und ODT-Exporte an mehreren Stellen
- Zeugnismaterial als Sammlung der Beobachtungen und Notizen eines Schuljahres
- Vollstaendige Schuelerakte als ein Dokument, fuer Schulwechsel und Auskunft nach Art. 15 DSGVO

## Module

### Beobachtung und Dokumentation

- Beobachtungsboegen (`Bogen`, `Item`)
- Einzelbeobachtungen mit Kommentar und Foto
- Verlaufsauswertung und Berichtsansichten
- Entwicklungsverlauf je Kompetenz (`competency_trend.py`): Vergleich der frueheren mit der spaeteren Haelfte der Beobachtungen, mit Richtung und Zusammenfassung ueber den Bogen
- Klassenuebersicht (`competency_matrix.py`): Kinder mal Kompetenzen mit Klassen- und Kindmittel, Erfassungsstand und direktem Sprung in den Schnelleintrag fuer fehlende Beobachtungen

### Elternkontakte

- Elternnotizen
- Gespraechsprotokolle
- Elterngespraeche / Beratungsgespraeche

### Foerderplanung

- Foerdergrundlagen
- Foerderplaene mit Evaluation
- Uebernahme weiterzufuehrender Ziele

### Arbeitsplaene

- Individuelle Arbeitsplaene pro Kind
- Aufgaben mit Kompetenzbezug
- Aufgabenbibliothek
- ODT- und PDF-Export

### Erzieherische Arbeit

- Ereignispool mit Kategorien, Orten und Konsequenzen
- Zuweisung an Lehrkraefte
- Verknuepfung mit Elternkontakten
- Aenderungsprotokoll pro Ereignis

### Schuelerakte

- Gebuendelte Sicht auf Beobachtungen, Foerderplanung, Arbeitsplaene, Elternkontakte und Ereignisse

### Zeugnismaterial

- Pro Kind die Beobachtungen des laufenden Schuljahres, nach Bogen und Bereich gegliedert
- Im Vordergrund die Notizen der Lehrkraft in zeitlicher Folge - daraus entsteht der Zeugnissatz
- Mittelwerte je Kompetenz, Bereich und Bogen, dazu der Entwicklungsverlauf
- Besondere Staerken und Foerderziele des Schuljahres
- Export als ODT oder PDF, einzeln oder als Klassensatz mit einem Kind je Seite

### Export der Gesamtakte

- Alle zu einem Kind gespeicherten Inhalte in einem ODT- oder PDF-Dokument
- Ohne Begrenzung und ohne Schuljahresfilter: die Ansicht zeigt die letzten Eintraege, der Auszug alle
- Grunddaten, Foerdergrundlage, Beobachtungen je Bogen, Foerderplaene, Arbeitsplaene, Elternkontakte, Beratungen und Ereignisse
- Uploads werden als vorhanden vermerkt, nicht eingebettet
- Nicht enthalten: das Aenderungsprotokoll der Ereignisse, das Bearbeitungen von Lehrkraeften festhaelt

### Aenderungsprotokoll

- Foerderplaene, Elternkontakte und erzieherische Ereignisse fuehren ein Journal: wer wann was geaendert hat
- Festgehalten wird der vorherige wie der neue Wert, lange Texte gekuerzt
- Ein Speichern ohne Aenderung schreibt keinen Eintrag
- Das Journal haengt am Datensatz und verschwindet mit ihm

### Aufbewahrung

- Frist in Jahren nach der Archivierung, einstellbar in den Grundeinstellungen
- Uebersicht aller archivierten Kinder mit Ablaufdatum und Umfang der gespeicherten Daten
- Loeschung ausschliesslich nach ausdruecklicher Auswahl, nie automatisch; laufende Fristen lassen sich nicht loeschen
- Ohne eingetragene Frist meldet die Anwendung nichts als ueberfaellig - die Frist gibt das Landesrecht vor

### Suche

- Feld in der Navigationsleiste, Tastenkuerzel `/`
- Kinder nach Vorname, Nachname und Klasse; Kompetenzen nach Text und Bereich; Beobachtungsboegen nach Titel
- Mehrere Begriffe werden mit UND verknuepft (`abt 3a`)
- Ein einzelner Treffer springt direkt in die Schuelerakte
- Archivierte Kinder werden gefunden und als solche ausgewiesen

## Technik

- Python 3
- Flask
- Flask-Login
- Flask-SQLAlchemy
- SQLite oder PostgreSQL
- Bootstrap-basierte Server-Templates (lokal ausgeliefert, siehe `static/vendor/`)
- LibreOffice fuer PDF-Konvertierung aus ODT

## Screens und Daten

Die Anwendung verarbeitet personenbezogene Schuldaten. Fuer einen produktiven Betrieb sollte sie daher nur mit sauberer Konfiguration und abgesichertem Deployment eingesetzt werden.

Bereits umgesetzt:

- geschuetzte Medienauslieferung fuer aktuelle Uploads
- Rate-Limit fuer den Login
- Rollenmodell mit `admin` und `teacher`
- Aufbewahrungsfristen fuer archivierte Kinder mit Loeschung nach ausdruecklicher Bestaetigung
- Cookie- und Reverse-Proxy-Haertung
- Service-Deployment mit `gunicorn`, Logging und Backups
- Oberflaeche ohne externe Ressourcen: Bootstrap und Schriften werden lokal ausgeliefert, es gehen keine Daten an Dritte und die Anwendung funktioniert ohne Internetverbindung

## Installation und Betrieb

Der isolierte Standalone-Stack, die explizite Migration, persönliche lokale
Erstadministration und Sicherung sind in [PLATFORM.md](PLATFORM.md) beschrieben.
Der HTTP-Setup und alte Passwort-Reparaturweg sind geschlossen. Es gibt keine
Standardkonten. Bestehende Service-/Updatewerkzeuge sind historische Ausgangspunkte;
ein produktiver Wechsel benötigt den dort beschriebenen Adoptionstest.

## Migration bestehender Uploads

Aeltere Instanzen koennen noch Dateien in `static/uploads` enthalten. Diese lassen sich in den geschuetzten Upload-Ordner uebernehmen:

```bash
python migrate_uploads_to_protected.py
```

Optional mit Verschieben statt Kopieren:

```bash
python migrate_uploads_to_protected.py --move
```

## Verwaiste Upload-Dateien aufraeumen

Aeltere Instanzen enthalten Fotos und PDFs, auf die kein Datensatz mehr zeigt: bis zur Nachbesserung der Loeschpfade entfernte die Anwendung keine Dateien. Der Aufraeumlauf berichtet zuerst nur:

```bash
python cleanup_orphan_uploads.py
```

Geloescht wird ausschliesslich mit `--delete` und nach Rueckfrage:

```bash
python cleanup_orphan_uploads.py --delete
```

Der Lauf braucht dieselbe `DATABASE_URL` wie der Dienst. Zeigt er auf eine leere Datenbank, bricht er ab, statt alle Dateien als verwaist zu behandeln.

## Updates bestehender Instanzen (historischer nativer Weg)

> Gilt nur für bestehende native Installationen. Neue Installationen laufen über die Plattform oder
> Docker Compose (siehe „Installation“); dort ersetzt `python -m km_migrate` das frühere `update_db.py`.

Produktive Installationen aktualisieren sich aus dem Git-Repository heraus. Ausgeloest wird das Update auf dem Server selbst:

```bash
cd /pfad/zur/app
bash deploy/update.sh
```

Das Skript sichert Datenbank und Uploads, holt den neuen Stand, zieht bei Bedarf Abhaengigkeiten nach, fuehrt `update_db.py` aus, startet die Regressionstests, startet den Dienst neu und prueft ihn per Health-Check. Schlaegt ein Schritt fehl, wird der Codestand automatisch zurueckgerollt.

Eine bisher manuell kopierte Installation wird einmalig umgestellt mit:

```bash
bash deploy/adopt_git.sh
```

Details, Optionen und Rollback-Wege:

- [deploy/README_DEPLOY.md](deploy/README_DEPLOY.md)

Datenbankaenderungen sind fuer bestehende SQLite- und PostgreSQL-Instanzen ausgelegt.


## Tests

```bash
./venv/bin/python -m unittest tests.test_critical_flows -v
./venv/bin/python -m unittest tests.test_workplan_flows -v
```

## Projektstruktur

- `app.py`: zentrale App-Konfiguration
- `models.py`: Datenmodelle
- `routes/`: Fachmodule und UI-Routen
- `templates/`: Jinja-Templates
- `static/`: statische Assets
- `odt_templates/`: Exportvorlagen
- `deploy/`: Service- und Betriebsdateien
- `tests/`: automatisierte Regressionstests
