# Kompetenz-Manager: isolierter Standalone-Betrieb

Dieser Stand betreibt die bestehende Flask-App mit zwei Gunicorn-Webworkern,
SQLite und einem separaten Benachrichtigungsworker. Die fachlichen Rollen und
UserKlassenzuordnung bleiben maßgeblich. Interne Beobachtungen sind nach dem
bisherigen Fachmodell schulweit sichtbar; insbesondere Diagnostik und weitere
klassengebundene Bereiche behalten ihre feineren Zugriffsregeln.

## Neue lokale Installation

Voraussetzungen: Docker Engine und Compose v2, amd64. Aus dem Repositorywurzel:

```sh
python3 prepare_secret.py
export KM_SECRET_FILE="$PWD/.private/session-key"
export APP_BASE_URL=http://localhost:8008
export SESSION_COOKIE_SECURE=0
docker compose build app
docker compose run --rm --no-deps app python -m km_migrate
docker compose run --rm --no-deps app python km_setup.py persoenlicher.name
docker compose up -d --wait
```

Der Erstzugang fragt das Passwort am echten lokalen Terminal zweimal verdeckt
ab (mindestens zwölf Zeichen) und verweigert sich bei vorhandenen Benutzern.
Kein Standardname/-passwort, kein HTTP-Bootstrap und keine Demo-Konten.
Die bisherigen /setup- und repair.py-Wege geben keine Credentials mehr aus.
Migration und Erstadministration werden bei normalem Start nicht wiederholt.

prepare_secret.py erzeugt nur ein neues privates Verzeichnis .private (0700).
Die einzelne projizierte Datei ist darin 0444; private Masterdateien separat
schützen und sichern. SECRET_KEY bleibt unterstützt, alternativ SECRET_KEY_FILE;
ebenso DATABASE_URL/DATABASE_URL_FILE, jeweils nur eine Quelle.
Compose übernimmt DATABASE_URL, standardmäßig SQLite im eigenen Volume.
Der optionale PostgreSQL-Codepfad ist nicht in dieser Containerabnahme geprüft.

Nur 127.0.0.1:8008 wird veröffentlicht. Bei anderem KM_PORT auch APP_BASE_URL
anpassen. Für TLS APP_BASE_URL auf die externe Origin setzen und
SESSION_COOKIE_SECURE=1 verwenden. TRUSTED_PROXY_IPS enthält ausschließlich die
tatsächlichen Proxyadressen. Der alte Schalter TRUST_REVERSE_PROXY=1 allein
genügt nicht mehr. App- und Mail-Konfiguration verwenden weiter die bestehenden
Namen MAIL_SERVER/PORT/SECURITY/USERNAME/PASSWORD/FROM und APP_BASE_URL.
Compose konfiguriert keinen externen SMTP-Upstream.

## Schema und Bestand

Revision 0001 prüft Release-Prüfsumme und Tabellen-/Spalteninventar.
Liveness ist unabhängig von Schema und SMTP, Readiness prüft die eigene DB.
Es gibt keine Schemaänderungen in Web-/Worker-Startpfaden oder HTTP-Routen.
Ein privates Intent-Journal macht einen tatsächlich unterbrochenen Erstlauf
fortsetzbar. Eine beschädigte oder abweichende Revision wird nicht überschrieben.

Ein vollständiges Schema des geprüften Ausgangsstands kann ohne Änderung von
Fachzeilen, IDs, Rollen, Klassennamen oder Zuordnungen übernommen werden.
Die zusätzliche Sitzungstabelle enthält keine Fachbenutzerkopien.
Unbekannte/ältere unvollständige Schemata werden vor Änderungen abgewiesen.
Die früheren automatischen Backfills aus update_db.py laufen nicht implizit;
ihre historische Hilfslogik ist für einen separat geprüften Migrationsplan
erhalten. Echte Altinstallationen wurden nicht übernommen. Vor Adoption
konsistent sichern und mit einer Kopie testen.

## Sitzungen und Dokumente

Lokale Anmeldung bleibt erhalten. Das signierte HttpOnly-/SameSite-Lax-Cookie
enthält eine zufällige Sitzungskennung; die DB speichert nur deren Hash mit
Acht-Stunden-Ablauf und Credential-Fingerabdruck. Logout, Passwort-/Rollenwechsel
und Kontolöschung entziehen damit bestehende Sitzungen. Alte Cookies ohne neue
Kennung erfordern erneute Anmeldung. Keine zentrale Sperrung oder SSO.

Bestehendes CSRF bleibt aktiv; vorhandene Origin-Header müssen zusätzlich zur
konfigurierten APP_BASE_URL passen. Direkte statische Uploadpfade sind gesperrt.
Auslieferung erfolgt über vorhandene authentifizierte Medienrouten mit den
jeweiligen Fachrechten.

Ein nicht wartender Dateilock begrenzt PDF-Konvertierungen über beide Worker
und lokale Jobs. Bei Belegung erhält der Client 429. Bei Sammel-PDFs hält der
HTTP-Pfad den Slot über die gesamte Anfrage; Konvertierungshelfer verwenden
denselben Lock. LibreOffice-Prozessgruppen werden nach höchstens 20 Sekunden
beendet, ODT-Eingaben sind auf 16 MiB und einzelne PDF-Ausgaben auf 64 MiB
begrenzt. pdfunite hat ebenfalls ein Zeitlimit. Große Sammellisten/Importe und
ODT-Erzeugung benötigen noch eine vollständige Mengen-/Lastabnahme.

Das Produktionsimage verteilt keine schuleigenen ODT-Vorlagen. Das Volume
/app/odt_templates startet leer; benötigte eigene Vorlagen ausdrücklich
bereitstellen und sichern. Dynamisch erzeugte ODTs und Gesamtakten-PDFs bleiben
auch ohne diese Vorlagen nutzbar. Die vorhandene Fachsuite verwendet die
Repositoryvorlagen nur im privaten Testimage. Rechteprüfung bleibt offen.

## Benachrichtigungen und Sicherung

Der separate Worker verwendet die vorhandene dauerhafte Notification-/mailed_at-
Logik. Ein Instanzlock verhindert parallele Worker auf demselben Volume.
Sofortzyklen laufen alle fünf Minuten, tägliche Erinnerungen separat mit
persistiertem Tagesmarker. Versandläufe begrenzen Nutzer-/Nachrichtenmengen.
Fehlgeschlagene Zustellungen bleiben offen. Versand ist mindestens einmal:
ein Absturz nach SMTP-Annahme vor DB-Commit kann Duplikate erzeugen.
Native Cronjobs nicht parallel zum Containerworker betreiben; für manuelle
Versandjobs den Worker zuerst anhalten. Keine vollständige Queue-/Alarmoberfläche
oder Zustellgarantie. Mailausfall blockiert keine normalen Webanfragen.

UID 10001 besitzt /app/instance (DB, private Uploads, Schullogo, Workerzustand)
und /app/odt_templates. Rootdateisystem schreibgeschützt, begrenztes tmpfs und
Ressourcenlimits. Vor einer gemeinsamen DB-/Dateisicherung beide Schreibdienste
stoppen. SQLite über die Backup-API sichern, keine Kopie einer laufenden DB.
Uploads, Logo, Vorlagen und Secret separat berücksichtigen. Restore nur in
leere isolierte Volumes; danach weder Seed noch Migration erneut ausführen.
Produktiv alte Sitzungen nach einem Restore ausdrücklich widerrufen.

Die App wurde mit Fach-/Negativtests, echter Terminaleinrichtung, PDF,
Mail-Sink-Ausfall, Restore, lokalem Domainrückfall und Lebenszyklus geprüft.
Keine Ubuntu-/öffentliche DNS-/ACME-, ARM64-, Offsite- oder Kapazitätsabnahme.
Basisimage und Python-Wheels sind festgelegt; Debian-Paketquellen sind noch
kein dauerhaft eingefrorener Distributionssnapshot. Keine Imageveröffentlichung
oder projektweite Lizenzdeklaration.
