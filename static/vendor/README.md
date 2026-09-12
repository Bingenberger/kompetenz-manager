# Lokal ausgelieferte Fremdbestandteile

Die Oberfläche lädt nichts aus dem Internet. Alles, was der Browser braucht,
liegt hier. Grund: Die Anwendung verarbeitet personenbezogene Daten von
Kindern, und ein eingebundenes CDN übermittelt bei jedem Seitenaufruf die
IP-Adresse der Lehrkraft an einen Dritten. Nebeneffekt: Die Anwendung
funktioniert auch, wenn die Internetverbindung der Schule ausfällt.

`tests/test_no_external_assets.py` hält diesen Zustand fest.

## Inhalt

| Ordner | Inhalt | Lizenz |
| --- | --- | --- |
| `bootstrap/` | Bootstrap 5.3.0, CSS und JS-Bundle | MIT (`LICENSE`) |
| `bootstrap-icons/` | Icon-Sprite als SVG | MIT |
| `fonts/` | Nunito und Sora als WOFF2, Zeichensätze `latin` und `latin-ext` | SIL OFL 1.1 (`OFL-*.txt`) |

Die Schriftschnitte entsprechen dem, was die Oberfläche nutzt: Nunito in 400,
600, 700 und 800 für Text, Sora in 500, 600, 700 und 800 für Überschriften.
Fehlt ein Schnitt, rechnet der Browser ihn sich aus und die Schrift wirkt
verwaschen.

## Bootstrap aktualisieren

Version in beiden Zeilen anpassen, dann:

```bash
BS=5.3.3
curl -sS -o static/vendor/bootstrap/bootstrap.min.css \
  "https://cdn.jsdelivr.net/npm/bootstrap@${BS}/dist/css/bootstrap.min.css"
curl -sS -o static/vendor/bootstrap/bootstrap.bundle.min.js \
  "https://cdn.jsdelivr.net/npm/bootstrap@${BS}/dist/js/bootstrap.bundle.min.js"
curl -sS -o static/vendor/bootstrap/LICENSE \
  "https://cdn.jsdelivr.net/npm/bootstrap@${BS}/LICENSE"

# Verweise auf Source Maps entfernen, die nicht mitgeliefert werden
sed -i 's|/\*# sourceMappingURL=[^*]*\*/||g' static/vendor/bootstrap/bootstrap.min.css
sed -i 's|//# sourceMappingURL=.*$||g' static/vendor/bootstrap/bootstrap.bundle.min.js
```

Anschließend die Oberfläche durchklicken: ein Sprung über eine Hauptversion
kann Klassennamen ändern.

## Schriften aktualisieren oder ergänzen

Erzeugt von `fetch_webfonts.py` im Projektordner:

```bash
python fetch_webfonts.py --check    # nur prüfen, ob es Neueres gibt
python fetch_webfonts.py            # holen und fonts.css neu schreiben
python -m unittest tests.test_no_external_assets
```

Nunito und Sora sind **variable Schriften**: eine Datei deckt den gesamten
Gewichtsbereich ab (Nunito 200–1000, Sora 100–800). Deshalb liegt je Familie
und Zeichensatz genau eine Datei vor, und `fonts.css` deklariert das Gewicht
als Bereich.

Das ist keine Feinheit, sondern der Unterschied zwischen funktionierend und
kaputt: Bindet man eine variable Datei unter einem festen `font-weight` ein,
variiert der Browser die Achse nicht — alle Überschriften wirken dann gleich
stark. `tests/test_no_external_assets.py` prüft beides, den Bereich und die
Abwesenheit byte-identischer Duplikate.

Weitere Familien oder Zeichensätze: `FAMILIEN` bzw. `ZEICHENSAETZE` in
`fetch_webfonts.py` erweitern. Der Gewichtsbereich einer Familie steht in ihrer
Beschreibung bei Google Fonts; eine falsche Angabe lehnt die API mit einem
Fehler ab.
