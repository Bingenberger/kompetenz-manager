# Demoinstanz des KompetenzKompass

Eine lokal laufende Kopie der App mit **frei erfundenen Daten** – für Schulungen,
Screenshots und Videoaufnahmen. Kein Datensatz stammt aus einer echten Schule,
und die Demo fasst die Produktivdatenbank nicht an: eigene Datei, eigene
Sitzungs-Cookies, eigener Port, kein Mailversand.

## Starten

```bash
bash demo/start_demo.sh          # baut beim ersten Mal die Daten und startet
bash demo/start_demo.sh --neu    # Daten verwerfen und frisch aufbauen
```

Danach im Browser: <http://127.0.0.1:5055>

Die Datenbank liegt in `instance/demo.db` (nicht im Repository). Anderer Ort
oder Port:

```bash
DEMO_DB=/tmp/demo.db PORT=5060 bash demo/start_demo.sh
```

Nur die Daten erzeugen, ohne zu starten:

```bash
python3 demo/demo_daten.py --db instance/demo.db --neu
```

## Konten

Passwort für **alle** Konten: `demo`

| Konto | Person | Rolle | Klassen |
|---|---|---|---|
| `sommer` | Klara Sommer | Lehrkraft | Klassenleitung 3a, Fachunterricht 3b |
| `weber` | Jonas Weber | Lehrkraft | Klassenleitung 3b, Fachunterricht 3a |
| `lang` | Mira Lang | Lehrkraft | Klassenleitung 4a, Fachunterricht 3a/3b |
| `hoffmann` | Paul Hoffmann | Lehrkraft | Klassenleitung 2a |
| `kern` | Lisa Kern | Lehrkraft | Klassenleitung 1a |
| `klein` | Thomas Klein | Förderpädagogik | alle Kinder |
| `wagner` | Sabine Wagner | Schulleitung | alle Kinder, Schulübersicht, Hospitationen |
| `admin` | Martina Adler | Admin | Verwaltung, Kataloge, Benutzer |

## Was in den Daten steckt

- **45 Kinder** in fünf Klassen (1a, 2a, 3a, 3b, 4a). Die **3a ist die Videoklasse**:
  dort ist alles ausführlich gefüllt.
- **Sechs Kompetenzbögen** (Deutsch und Mathematik je 1/2 und 3/4, Arbeits- und
  Sozialverhalten, Übergang Klasse 5) mit rund 1800 Beobachtungen im laufenden
  Schuljahr, viele mit Kommentar.
- **Diagnostik** (HSP, SLS, ELFE II) über mehrere Schuljahre zurück – dadurch sind
  Verlaufsgrafiken, Stufenauswertung und die Schulübersicht gefüllt.
- **Förderpläne** mit Zielen aus den Bögen, einige aus dem Vorjahr evaluiert, drei
  Evaluationen überfällig (stehen als Aufgabe auf der Startseite).
- **Förderkurse** mit Teilnahmen – darunter bewusst ein Kind ohne passenden
  Förderplan, damit die Erinnerung sichtbar ist.
- **Nachteilsausgleich** für zwei Kinder, einmal mit ausgesetzter Rechtschreibnote.
- **Arbeitspläne** (zwei laufend, zwei ausgewertet) und eine Aufgabenbibliothek der 3a.
- **Elternkontakte**, Gesprächsprotokolle mit Folgeterminen und Elternberatungen.
- **Ereignisse** mit Katalog (Kategorien, Orte, Konsequenzen), offen und abgeschlossen.
- **Förderkonferenz**: eine abgeschlossene aus dem Vorjahr samt Evaluation und eine
  kommende, für die die Klassenleitungen ihre A/B/C-Vorschläge schon eingetragen
  haben – gut geeignet, um den Konferenzmodus vorzuführen.
- **Hospitationen** der Schulleitung, eine davon freigegeben.

## Vorschlag für einen Ablauf im Video

1. Anmeldung als `sommer`: Startseite mit Aufgaben und der eigenen Klasse.
2. *Erfassen → Schnelleintrag*: eine Beobachtung eintragen.
3. *Kinder*: Schülerakte von **Ben Kraus** – Bögen, Förderung, Diagnostik-Verlauf.
4. *Förderung → Förderpläne*: Assistent öffnen, Vorschläge aus den Beobachtungen zeigen.
5. *Förderung → Förderkurse*: „Förderkurs ohne Förderplan“.
6. *Förderung → Nachteilsausgleich*: Eintrag mit Maßnahmen und Notenschutz.
7. Abmelden, Anmeldung als `wagner`: *Diagnostik → Schulübersicht* und
   *Förderung → Förderkonferenz* (Konferenz starten, Beamer-Ansicht).

## Hinweise

- Die Daten sind **deterministisch**: derselbe Aufbau ergibt dieselbe Datenbank,
  eine Aufnahme lässt sich also wiederholen.
- Die Zeitachse richtet sich nach dem heutigen Tag – die Demo wirkt immer aktuell.
- Alle Namen sind erfunden. Ähnlichkeiten mit Kindern der Schule wären Zufall;
  vor einer Veröffentlichung trotzdem kurz über die Namensliste schauen.
