# Kompetenz-Manager

Flask-Anwendung zur Erfassung von Beobachtungen, Berichten und Förderplänen im Schulkontext.

## Was die App fachlich macht

Die App unterstützt Lehrkräfte dabei, Beobachtungen zu einzelnen Kindern systematisch zu dokumentieren, auszuwerten und daraus Fördermaßnahmen abzuleiten. Sie bündelt dafür mehrere Arbeitsschritte in einer Oberfläche:

- Beobachtungsbögen verwalten und ausfüllen
- Einzelbeobachtungen mit Kommentar/Foto erfassen
- Elternkontakte als Kurznotiz oder Gesprächsprotokoll dokumentieren
- Fördergrundlagen und Förderpläne erstellen/evaluieren
- Schülerakte mit den wichtigsten Informationen pro Kind anzeigen
- To-do-Hinweise rund um Elternsprechtage, fehlende Beobachtungen und Evaluationen anzeigen

### Zentrale Datenobjekte

- `Schueler`: Stammdaten eines Kindes (Name, Klasse, optional Geburtsdatum)
- `Bogen` und `Item`: Beobachtungsbögen mit einzelnen Beobachtungskriterien
- `Beobachtung`: Bewertung eines Items (inkl. Datum, Anlass, Kommentar, optional Foto)
- `Elternkontakt`: Dokumentation von Elternkommunikation (Notiz oder Protokoll)
- `Foerdergrundlage`: Grundlagenblatt mit Stärken/Förderbedarf/Absprachen
- `Foerderplan` + `Foerderinhalt`: Förderplan mit Zielen, Maßnahmen und Evaluation
- `User` + `UserKlassenzuordnung`: Benutzerverwaltung inkl. Klassenleitung/Fachklassen
- `SystemKonfiguration`: Schuljahr und Elternsprechtag-Termine

### Rollen und Zugriff

- Anmeldung ist erforderlich (Login via Flask-Login).
- Der Administrator ist aktuell technisch über den Benutzernamen `admin` definiert.
- Admin-Funktionen:
  - Benutzer anlegen/bearbeiten/löschen
  - Klassenzuordnungen für Lehrkräfte pflegen (Klassenleitung/Fachklassen)
  - Schülerstammdaten verwalten
  - Beobachtungsbögen und Items verwalten
  - Systemeinstellungen (Schuljahr, Elternsprechtage) pflegen
- Lehrkräfte arbeiten primär mit den ihnen zugeordneten Klassen; diese Zuordnung beeinflusst Auswahlhilfen, Dashboard und To-dos.

### Typischer Arbeitsablauf in der App

1. Admin richtet Benutzer, Klassen, Schüler und Beobachtungsbögen ein (oder importiert Daten aus Excel).
2. Lehrkräfte erfassen Beobachtungen:
   - als Einzelbeobachtung
   - als kompletter Bogen für ein Kind
   - als Reihenabfrage (ein Item nacheinander für viele Kinder)
   - als Multi-Erfassung (mehrere Items für eine Klasse)
3. In Berichten werden Beobachtungen pro Kind/Bogen historisch angezeigt (inkl. Durchschnittswert und Einträge).
4. Bei Bedarf werden Elternkontakte dokumentiert (Notiz/Protokoll) und exportiert.
5. Aus Beobachtungen werden Fördergrundlagen und anschließend Förderpläne erstellt.
6. Förderpläne werden später evaluiert und als erledigt/weiterzuführen dokumentiert.

### Beobachtungserfassung (pädagogische Dokumentation)

Die App unterstützt mehrere Eingabeformen, damit sie im Unterrichtsalltag flexibel nutzbar ist:

- `Einzel`: gezielte Erfassung eines Beobachtungskriteriums mit optionalem Kommentar und Foto
- `Bogen für ein Kind`: mehrere Items eines Bogens in einem Schritt ausfüllen
- `Reihe`: ein Item wird für alle Kinder einer Klasse nacheinander bewertet (schnell im Unterricht)
- `Multi`: mehrere ausgewählte Items werden für eine Klasse sequenziell abgefragt

Zusätzliche Details:

- Beobachtungen speichern Datum/Zeit und optional einen Anlass.
- Bilduploads werden unter `static/uploads` abgelegt.
- Bewertungswerte werden im Bericht als Skala dargestellt (u. a. `-`, `o`, `+`, `++`).

### Berichte und Schülerakte

- Die Berichtsansicht zeigt für ein Kind und einen ausgewählten Bogen:
  - alle Einträge je Item (chronologisch)
  - Anzahl der Einträge
  - Durchschnittswert pro Item
- Beobachtungseinträge können gelöscht werden.
- Ein zuletzt gelöschter Beobachtungseintrag kann direkt wiederhergestellt werden (Undo über Session).
- Die Schülerakte bündelt pro Kind:
  - Förderpläne
  - Elternkontakte (letzte Einträge)
  - zusammengefasste Bögen mit letzter Beobachtung
  - letzte Beobachtungen über alle Bögen hinweg

### Elternkontakte (Notiz und Protokoll)

Es gibt zwei Typen von Elternkontakten:

- `notiz`: kurze Dokumentation (z. B. Telefonat, Mail, Kurzgespräch)
- `protokoll`: strukturierter Gesprächseintrag mit Teilnehmenden, Anlass, Besprochenem, Vereinbarungen und nächstem Termin

Funktionen:

- Erstellen, ansehen, bearbeiten, löschen
- Termin-Nachverfolgung über `naechster_termin`
- Export von Protokollen als ODT und PDF
- Konfliktschutz bei gleichzeitiger Bearbeitung (Concurrency-Token)

### Förderplanung

Die Förderplanung ist als Workflow umgesetzt:

- Zuerst wird ein `Foerdergrundlage`-Blatt gepflegt (Stärken, Förderbedarf, Entwicklung, wichtige Infos, Elternabsprachen).
- Danach kann ein Förderplan erstellt werden.
- Ein aktiver Plan ohne Evaluation blockiert die Erstellung eines weiteren aktiven Plans für dasselbe Kind.
- Förderpläne enthalten mehrere Förderinhalte mit:
  - Förderziel
  - Ist-Zustand
  - Soll-Zustand
  - Maßnahmen
  - Evaluationsfeld / Status
- Förderpläne und Grundlagenblätter können als ODT/PDF exportiert werden (inkl. kombinierter Export mit Grundlagenblatt).
- Auch hier gibt es einen Konfliktschutz bei paralleler Bearbeitung.

### Dashboard und To-do-Logik

Die Startseite ist kein statischer Einstieg, sondern ein arbeitsbezogenes Dashboard pro Lehrkraft/Klasse. Angezeigt werden u. a.:

- Anzahl Kinder in der Fokusklasse
- Anzahl ausgefüllter Beobachtungsbögen
- Anzahl aktiver Förderpläne
- priorisierte To-dos

Die To-dos berücksichtigen unter anderem:

- anstehende/überfällige Förderplan-Evaluationen
- Förderplan-Kandidaten (z. B. auffällige Häufung negativer Beobachtungen in einem Zeitraum)
- fehlende aktuelle Beobachtungsbogen-Einträge vor Elternsprechtagen
- anstehende Elternkontakt-Nachverfolgungen

Zusätzlich gibt es eigene To-do-Seiten mit Detaillisten für diese Bereiche.

### Import und Erstbefüllung

- Excel-Import für Schüler (`Vorname`, `Nachname`, `Klasse`, `Geburtsdatum`)
- Excel-Import für Beobachtungsbögen (`Bogen`, `Bereich`, `Item`)
- optionale `/setup`-Route für lokale Erstinitialisierung (deaktiviert per Default)
- `repair.py` zum Setzen/Zurücksetzen des Admin-Passworts

### Technische Eigenschaften (relevant im Betrieb)

- SQLite standardmäßig, PostgreSQL optional via `DATABASE_URL`
- automatisches `db.create_all()` beim Start (keine Alembic-Migrationen)
- CSRF-Schutz für klassische Formulare
- Upload-Ordner wird beim Start automatisch angelegt
- SQLite wird mit sinnvollen PRAGMA-Einstellungen für robusteren Parallelbetrieb initialisiert (z. B. WAL, Foreign Keys)

## Voraussetzungen

- Python 3.12 (oder kompatibel)
- vorhandenes `venv/` im Projektordner (optional, aber empfohlen)

## Installation

```bash
cd "/pfad/zum/Kompetenz-Manager"
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
