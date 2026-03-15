# KompetenzKompass

KompetenzKompass ist eine Flask-Anwendung fuer Grundschulen und andere schulische Kontexte, in denen Beobachtungen, Foerderplanung, Arbeitsplaene, Elternkontakte und paedagogische Fallarbeit an einem Ort dokumentiert werden sollen.

Die Anwendung ist fuer den praktischen Schulalltag gebaut: klassische Server-Templates statt SPA-Komplexitaet, strukturierte Arbeitsablaeufe fuer Lehrkraefte und Exporte fuer Gespraeche, Dokumentation und Weiterarbeit.

## Funktionsumfang

- Beobachtungsboegen verwalten und ausfuellen
- Schnelleintraege, Klassendurchlaeufe und komplette Bogen-Erfassung
- Berichte pro Kind und Beobachtungsbogen
- Elternkontakte als Notiz oder Protokoll dokumentieren
- Elterngespraeche vorbereiten und durchfuehren
- Foerderplaene anlegen, evaluieren und fortschreiben
- Individuelle Arbeitsplaene mit Aufgabenbibliothek, Evaluation und Export
- Schuelerakte als gebuendelte Uebersicht pro Kind
- Erzieherische Ereignisse mit Konsequenzen, Zustaendigkeit, Anhaengen und Journal
- PDF- und ODT-Exporte an mehreren Stellen

## Module

### Beobachtung und Dokumentation

- Beobachtungsboegen (`Bogen`, `Item`)
- Einzelbeobachtungen mit Kommentar und Foto
- Verlaufsauswertung und Berichtsansichten

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

## Technik

- Python 3
- Flask
- Flask-Login
- Flask-SQLAlchemy
- SQLite oder PostgreSQL
- Bootstrap-basierte Server-Templates
- LibreOffice fuer PDF-Konvertierung aus ODT

## Screens und Daten

Die Anwendung verarbeitet personenbezogene Schuldaten. Fuer einen produktiven Betrieb sollte sie daher nur mit sauberer Konfiguration und abgesichertem Deployment eingesetzt werden.

Bereits umgesetzt:

- geschuetzte Medienauslieferung fuer aktuelle Uploads
- Rate-Limit fuer den Login
- Rollenmodell mit `admin` und `teacher`
- Cookie- und Reverse-Proxy-Haertung
- Service-Deployment mit `gunicorn`, Logging und Backups

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

## Hinweise fuer ein GitHub-Repository

Nicht versioniert werden sollten insbesondere:

- produktive `.env`-Dateien
- Datenbanken und Dumps
- Uploads
- lokale Backups
- Logdateien

Die `.gitignore` im Repository ist darauf abgestimmt.

## Lizenz und Weitergabe

Falls das Projekt veroeffentlicht wird, sollte noch bewusst entschieden werden:

- unter welcher Lizenz der Code stehen soll
- ob Beispieldaten oder Screenshots anonymisiert werden muessen
- welche Deploy-Hinweise fuer externe Nutzer relevant sind
