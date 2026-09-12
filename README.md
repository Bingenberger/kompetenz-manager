# KompetenzKompass

KompetenzKompass ist eine Flask-Anwendung fuer Grundschulen und andere schulische Kontexte, in denen Beobachtungen, Foerderplanung, Arbeitsplaene, Elternkontakte und paedagogische Fallarbeit an einem Ort dokumentiert werden sollen.

Die Anwendung ist fuer den praktischen Schulalltag gebaut: klassische Server-Templates statt SPA-Komplexitaet, strukturierte Arbeitsablaeufe fuer Lehrkraefte und Exporte fuer Gespraeche, Dokumentation und Weiterarbeit.

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
- PDF- und ODT-Exporte an mehreren Stellen
- Vollstaendige Schuelerakte als ein Dokument, fuer Schulwechsel und Auskunft nach Art. 15 DSGVO

## Module

### Beobachtung und Dokumentation

- Beobachtungsboegen (`Bogen`, `Item`)
- Einzelbeobachtungen mit Kommentar und Foto
- Verlaufsauswertung und Berichtsansichten
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

### Export der Gesamtakte

- Alle zu einem Kind gespeicherten Inhalte in einem ODT- oder PDF-Dokument
- Ohne Begrenzung und ohne Schuljahresfilter: die Ansicht zeigt die letzten Eintraege, der Auszug alle
- Grunddaten, Foerdergrundlage, Beobachtungen je Bogen, Foerderplaene, Arbeitsplaene, Elternkontakte, Beratungen und Ereignisse
- Uploads werden als vorhanden vermerkt, nicht eingebettet
- Nicht enthalten: das Aenderungsprotokoll der Ereignisse, das Bearbeitungen von Lehrkraeften festhaelt

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
- Cookie- und Reverse-Proxy-Haertung
- Service-Deployment mit `gunicorn`, Logging und Backups
- Oberflaeche ohne externe Ressourcen: Bootstrap und Schriften werden lokal ausgeliefert, es gehen keine Daten an Dritte und die Anwendung funktioniert ohne Internetverbindung

## Schnellstart fuer Entwicklung

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
SECRET_KEY="bitte-setzen" FLASK_DEBUG=1 python app.py
```

Alternativ:

```bash
./start.sh
```

Wenn `DATABASE_URL` nicht gesetzt ist, verwendet die App automatisch SQLite.

## Installation

Die ausfuehrliche Installationsanleitung steht in:

- [INSTALL.md](INSTALL.md)

Dort enthalten:

- Entwicklungsbetrieb
- SQLite und PostgreSQL
- Setup und Erstinitialisierung
- Serverbetrieb als Service
- Reverse Proxy und Sicherheitsvariablen
- Migration bestehender Uploads

## Produktion als Service

Fuer Linux-Server liegt ein Installationsskript bei:

- [deploy/install_service.sh](deploy/install_service.sh)

Die zugehoerige Betriebsdokumentation steht in:

- [deploy/README_SERVICE.md](deploy/README_SERVICE.md)

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

## Updates bestehender Instanzen

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
