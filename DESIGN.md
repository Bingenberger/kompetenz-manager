# Design-Guide (Kompetenz-Manager)

Kurze Referenz für das aktuelle Look-and-Feel, damit neue Seiten konsistent gestaltet werden.

## Farbpalette

Primärpalette (Projektvorgabe):

- `#f94144` Strawberry Red
- `#f3722c` Atomic Tangerine
- `#f8961e` Carrot Orange
- `#f9844a` Coral Glow
- `#f9c74f` Tuscan Sun
- `#90be6d` Willow Green
- `#43aa8b` Seagrass
- `#4d908e` Dark Cyan
- `#577590` Blue Slate
- `#277da1` Cerulean

Zentrale CSS-Variablen liegen in `templates/base.html` (`:root`).

## Bootstrap-Mapping (projektweit überschrieben)

- `primary` -> `Cerulean` (`#277da1`)
- `info` -> `Dark Cyan` (`#4d908e`)
- `success` -> `Seagrass` (`#43aa8b`)
- `warning` -> `Tuscan Sun` (`#f9c74f`)
- `danger` -> `Strawberry Red` (`#f94144`)
- `dark` -> `Blue Slate` (`#577590`)

## Typografie

- Headlines / Navigation: `Sora`
- Fließtext / Formulare / Tabellen: `Nunito`

## Wiederverwendbare Layout-Klassen

Definiert in `templates/base.html`:

- `surface-panel`: Primärer Inhaltscontainer mit Verlauf, Border und Shadow
- `toolbar-shell`: Kopf-/Toolbar-Container für Listen- und Admin-Seiten
- `toolbar-actions`: Aktionsbereich innerhalb der Toolbar
- `table-panel`: Einheitlicher Tabellencontainer
- `table-toolbar`: Kopfzeile über Tabellen (Titel + Zähler/Actions)
- `section-label`: Kleine Bereichskennung über Überschriften
- `soft-divider`: Dezente horizontale Trennlinie
- `fade-in`, `fade-in-delay-*`: optionale Einstiegsanimationen

## Bewertungs-UI (zentralisiert)

Wiederverwendbare Includes:

- `templates/includes/rating_scale_symbol.html`
  - Für `- / o / + / ++`
- `templates/includes/rating_scale_numeric.html`
  - Für `1 / 2 / 3 / 4`

Typische Parameter (`set` in Templates vor `include`):

- `rating_name`
- `rating_id_prefix`
- `rating_layout_class`
- `rating_required`
- optional: `rating_label_extra_class`, `rating_value_class`, `rating_show_hints`

## Icon-Standard (lokale Bootstrap Icons)

Quelle:

- Lokales SVG-Sprite: `static/vendor/bootstrap-icons/bootstrap-icons.svg`
- Jinja-Makro: `templates/includes/icons.html` (`bi(...)`)

Verwendung:

- Normale Buttons mit Text: `icon-btn`
- Reine Icon-Buttons: `icon-only-btn`
- Icon-Größen werden zentral in `templates/base.html` nach Button-Größe normiert

Empfohlenes Mapping (Aktion -> Icon):

- Starten -> `play-circle`
- Speichern -> `floppy`
- Abbrechen -> `x-circle`
- Zurück -> `arrow-left-circle`
- Löschen -> `trash`
- Bearbeiten -> `pencil-square`
- Anlegen / Neu -> `plus-circle`
- Import Excel -> `file-earmark-excel`
- Hochladen -> `cloud-upload`
- Drucken -> `printer`
- Ansehen -> `eye`
- Evaluieren / Formularbearbeitung -> `journal-text`
- Verwaltung / Einstellungen -> `gear`
- Benutzer / Profil -> `person` / `person-circle`
- Gruppe / Kollegium -> `people`
- Schüler / Schule -> `mortarboard` / `building`
- Bögen / Checklisten -> `card-checklist`

Beispiel:

```jinja2
{% from "includes/icons.html" import bi %}
<a class="btn btn-primary icon-btn" href="#">
  {{ bi('floppy', 'bi-icon') }} <span>Speichern</span>
</a>
```

## Responsive Leitlinien

- Mobile zuerst bei Aktionsgruppen: Buttons umbrechen lassen (`flex-wrap`)
- Toolbars auf kleinen Screens vollbreitige Buttons verwenden
- Bewertungsraster auf Mobilgeräten von 4 auf 2 Spalten reduzieren
- Tabellen in `table-panel` möglichst mit `table-responsive` einsetzen
- `btn-lg` auf Mobilgeräten visuell kompakter halten (in `base.html` bereits angepasst)

## Seitenmuster (empfohlen)

1. Oben `section-label` + `h2` + kurze Erklärung
2. Inhalt in `surface-panel` oder `table-panel`
3. Primäraktion links, Sekundäraktion (`Zurück/Abbrechen`) daneben
4. Kritische Aktionen als `btn-outline-danger`

## Druckansicht

- `report_view.html` enthält eine eigene Druckoptimierung (`@media print`)
- Für neue druckbare Seiten:
  - Schatten entfernen
  - kontrastreiche Texte
  - kompakte Tabellenabstände
  - Farbbadges im Druck auf neutrale Darstellung reduzieren
