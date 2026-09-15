# KompetenzKompass – Funktionsbeschreibung

Diese Datei beschreibt, was die Web-App **KompetenzKompass** kann. Sie richtet sich
an Menschen und Chatbots, die Abläufe an der Schule planen und dabei wissen müssen,
welche Informationen die App bereits bereithält, was sie automatisch erledigt und
wo ihre Grenzen liegen.

Stand: September 2026.

---

## 1. Überblick

KompetenzKompass ist eine Web-App für eine **Grundschule (Jahrgänge 1 bis 4)**. Lehrkräfte
dokumentieren darin an einem Ort:

- Beobachtungen zu Kompetenzen (anhand von Beobachtungsbögen)
- Ergebnisse standardisierter Tests (HSP, SLS, ELFE II)
- Förderpläne mit Zielen, Maßnahmen und Evaluation
- individuelle Arbeitspläne für Kinder
- Elternkontakte, Gesprächsprotokolle und Elternberatungen
- erzieherische Ereignisse (Vorfälle, Konsequenzen, Zuständigkeiten)

Aus diesen Daten erzeugt die App Übersichten, Hinweise auf Handlungsbedarf,
Benachrichtigungen und Dokumente (ODT und PDF).

Die App läuft auf einem schuleigenen Server, ohne externe Dienste. Sie ist über den
Browser erreichbar, auch auf Tablet und Smartphone.

---

## 2. Rollen und wer was sieht

### Rollen

- **Verwaltung (Admin):** sieht und bearbeitet alles, pflegt Stammdaten und Einstellungen.
- **Lehrkraft:** arbeitet mit den Kindern. Jede Lehrkraft kann einer Klasse als
  **Klassenleitung** und beliebig vielen Klassen als **Fachlehrkraft** zugeordnet sein.

### Sichtbarkeit

| Inhalt | Wer sieht es |
|---|---|
| Kinderliste, Beobachtungen, Berichte, Klassenübersicht | alle Lehrkräfte |
| Förderpläne, Grundlagenblatt, Arbeitspläne | Klassenleitung und Fachlehrkräfte der Klasse des Kindes |
| Diagnostik-Ergebnisse und Förderangaben | Klassenleitung und Fachlehrkräfte der Klasse des Kindes |
| Elternkontakte (Notiz, Protokoll) und Elternberatungen | wer den Eintrag angelegt hat, dazu Klassenleitung und Fachlehrkräfte des Kindes |
| Erzieherische Ereignisse | wer es angelegt hat, wer als zuständig eingetragen ist, Klassenleitung und Fachlehrkräfte des Kindes oder eines betroffenen Kindes |
| Alles | Verwaltung |

**Eintragen darf jede Lehrkraft für jedes Kind** – etwa bei Vertretungsunterricht oder
Vorfällen in der Pause. Den eigenen Eintrag sieht sie danach immer.

Archivierte Kinder (nach Klasse 4 oder Schulwechsel) bleiben mit allen Daten
lesbar, bis die Verwaltung sie nach Ablauf der Aufbewahrungsfrist löscht.

---

## 3. Grundbegriffe

- **Kind:** Vorname, Nachname, Klasse, Jahrgang (1–4), Geburtsdatum, aktiv oder archiviert.
- **Klasse:** z. B. „3a“ oder frei benannt („Füchse“); kann jahrgangsübergreifend sein.
- **Schuljahr:** läuft vom 1. August bis 31. Juli; das laufende Schuljahr und sein
  Beginn sind in den Grundeinstellungen hinterlegt. Viele Auswertungen beziehen sich
  nur auf das laufende Schuljahr.
- **Beobachtungsbogen:** eine Sammlung von Kompetenzen (Items), gegliedert in
  Bereiche. Beispiel: Bogen „Deutsch“, Bereich „Lesen“, Item „liest kurze Sätze
  sinnentnehmend“. Bögen können bestimmten Jahrgängen zugeordnet und als
  **Pflichtbogen** oder **optionaler Bogen** markiert sein.
- **Bewertungsskala** für Beobachtungen:

  | Wert | Symbol | Bedeutung |
  |---|---|---|
  | 1 | − | reicht noch nicht |
  | 2 | o | teilweise / wechselhaft |
  | 3 | + | überwiegend sicher |
  | 4 | ++ | sicher beherrscht |

  Aus Mittelwerten bildet die App vier **Lernstandsstufen**: *sicher beherrscht*,
  *überwiegend sicher*, *teilweise*, *reicht noch nicht*.

---

## 4. Bereiche der App

Die Kopfzeile hat sieben Bereiche: **Start, Kinder, Erfassen, Auswertung, Förderung,
Ereignisse, Diagnostik**. Dazu kommen die Glocke (Benachrichtigungen), das Konto und
das Zahnrad (Verwaltung). Ein einmal gewähltes Kind bleibt beim Seitenwechsel
ausgewählt.

### 4.1 Start

Persönliche Startseite der Lehrkraft für ihre Klasse:

- **Schnellzugriffe** auf häufige Aufgaben
- **Kennzahlen** der eigenen Klasse (z. B. ausgefüllte Bögen, aktive Förder- und
  Arbeitspläne, offene erzieherische Fälle)
- **Aufgabenliste** mit Handlungsbedarf:
  - **Förderplan-Kandidaten:** Kinder ohne aktiven Förderplan, die in den letzten
    zwölf Wochen in mindestens vier Kompetenzen eines Bogens mit „−“ beobachtet
    wurden
  - **Fällige Förderplan-Evaluationen** (nach Evaluationsdatum)
  - **Beobachtungsbögen aktualisieren:** Kinder ohne Beobachtung in den letzten
    zwölf Wochen, mit Hinweis auf einen Elternsprechtag in den nächsten zwei Wochen
  - In den vier Wochen vor einem eingetragenen **Elternsprechtag** weisen die Listen
    der Förderplan-Kandidaten und Evaluationen auf den Termin hin
  - **Anstehende Elterntermine** (vereinbarte Folgetermine aus Gesprächsprotokollen,
    nächste 14 Tage)
  - **Offene erzieherische Fälle**, besonders solche ohne Zuständigkeit
- **Kinder der Klasse** als Reihe zum schnellen Öffnen der Schülerakte

### 4.2 Kinder – Schülerakte

Die Schülerakte bündelt alles zu einem Kind:

- **Kopf:** Name, Klasse, Geburtstag; Menü „Neu“ (Beobachtung, Förderplan,
  Arbeitsplan, Elternkontakt, Ereignis, Diagnostik) und Export der Gesamtakte
- **Reiter:**
  - *Bögen:* ausgefüllte Beobachtungsbögen mit Anzahl und letztem Eintrag, jüngste Beobachtungen
  - *Förderung:* Grundlagenblatt, Förderpläne, Förderangaben des Schuljahres
  - *Arbeitspläne*
  - *Eltern:* Notizen, Protokolle, Beratungen
  - *Diagnostik:* Testverlauf je Lernbereich mit Diagramm und Risikostufen
  - *Ereignisse*
- **Gesamtakte als ODT/PDF:** alle gespeicherten Inhalte des Kindes ohne
  Schuljahresfilter – Grunddaten, Grundlagenblatt, alle Beobachtungen je Bogen,
  Förderpläne, Arbeitspläne, Elternkontakte, Beratungen, Ereignisse. Gedacht für
  Schulwechsel und Auskunftsersuchen nach Art. 15 DSGVO. Enthält nur, was die
  exportierende Lehrkraft sehen darf.

**Suche** (Kopfzeile, Taste `/`): findet Kinder (Name, Klasse), Kompetenzen und Bögen;
mehrere Begriffe werden mit UND verknüpft; ein einzelner Treffer öffnet die Akte.

### 4.3 Erfassen – Beobachtungen und Elternkontakte

**Beobachtungen** (jeweils mit Wert 1–4, optional Kommentar, Foto und Anlass):

- **Schnelleintrag:** ein Kind, eine Kompetenz – für den Moment im Unterricht
- **Ganzer Bogen:** alle Kompetenzen eines Bogens für ein Kind
- **Klassen-Durchlauf:** eine Kompetenz nacheinander für alle Kinder einer Klasse
- **Mehrere Items:** mehrere ausgewählte Kompetenzen nacheinander für mehrere Kinder

**Elternkontakte:**

- **Notiz:** kurzer Kontakt (Kurz-Kontakt, Elternmitteilung, Telefonat, Mail) mit Betreff und Mitteilung
- **Gesprächsprotokoll** nach Schulvorlage: Teilnehmende, Gesprächsanlass, Besprochenes,
  Vereinbarungen Schule, Vereinbarungen Eltern, nächste Schritte, nächster Termin.
  Export als ODT/PDF. Nur die Verfasserin/der Verfasser (und die Verwaltung) darf ein
  Protokoll ändern oder löschen.
- **Elternberatung:** Vorbereitung und Dokumentation eines Elterngesprächs. Die Seite
  zeigt zum Kind automatisch:
  - **Lernstand je Bogen und Bereich** in elternverständlicher Form mit Balken,
    „Das klappt gut“ und „Daran arbeiten wir“
  - **Diagnostik-Ergebnisse** mit Verlauf
  - **Ereignisse** des laufenden Schuljahres
  - **aktiver Förderplan** und zuletzt evaluierter Förderplan
  - Details je Bogen zum Aufklappen
  - Eingabefelder: Datum, Anlass, weitere Beratungspunkte, Vereinbarungen

Alle Elternkontakte führen ein **Änderungsprotokoll** (wer hat wann was geändert).

### 4.4 Auswertung

- **Klassenübersicht (Matrix):** alle Kinder einer Klasse × alle Kompetenzen eines
  Bogens, mit Mittelwert je Kind und je Kompetenz, Lernstandsstufe, Erfassungsstand
  und direktem Sprung zum Nachtragen fehlender Beobachtungen. Archiv-Klassen abrufbar.
- **Bericht je Kind:** alle Beobachtungen eines Bogens mit Mittelwerten je Kompetenz,
  Bereich und Bogen sowie dem **Entwicklungsverlauf** (verbessert / unverändert /
  zurückgegangen – Vergleich der früheren mit der späteren Hälfte der Beobachtungen,
  Schwelle eine halbe Stufe).
- **Zeugnismaterial:** je Kind die Beobachtungen des Schuljahres nach Bogen und
  Bereich, im Vordergrund die Kommentare in zeitlicher Folge, dazu Mittelwerte,
  Entwicklungsverlauf, besondere Stärken und Förderziele des Schuljahres. Export als
  ODT/PDF, einzeln oder als Klassensatz. Bewusst Material, kein fertiger Zeugnistext.

### 4.5 Förderung

**Grundlagenblatt (Fördergrundlage)** – je Kind ein Blatt, Voraussetzung für einen Förderplan:

- besondere Stärken
- vorrangiger Förderbedarf
- Besonderheiten der Entwicklung
- wichtige Informationen (z. B. medizinisch)
- Absprachen mit den Eltern
- Export als ODT/PDF

**Förderplan:**

- Titel, Erstellungsdatum, Evaluationsdatum, Status (*aktiv* / *geschlossen*)
- beliebig viele **Förderziele**, je Ziel: Ist-Zustand, Soll-Zustand, Maßnahmen
- **Vorschläge beim Anlegen:**
  - Ziele aus dem zuletzt evaluierten Plan, die *nicht erreicht / weiterzuführen* sind
    (mit altem Ist-Zustand und alten Maßnahmen)
  - Kompetenzen, die in den letzten rund vier Monaten fast durchgehend mit „−“
    beobachtet wurden (mit Beobachtungsdatum, Durchschnitt und Kommentar als Ist-Text)
- **Pro Kind nur ein aktiver Förderplan.** Ein neuer Plan setzt voraus, dass der
  bestehende evaluiert und geschlossen ist.
- **Evaluation:** je Ziel Status *erreicht*, *nicht erreicht / weiterführen* oder
  *offen* plus Evaluationstext; danach Plan schließen. Weiterzuführende Ziele
  erscheinen beim nächsten Plan als Vorschlag.
- Export als ODT/PDF, Änderungsprotokoll, Benachrichtigung der Klassenleitung bei
  neuem, geändertem oder evaluiertem Plan

**Arbeitspläne** (individuelle Wochen-/Zeitraumpläne für das Kind):

- Zeitraum, Status (*in Planung* / *aktiv* / *geschlossen*), Hinweise für das Kind
  und für die Lehrkraft
- **Aufgaben** mit Titel, Lernbereich, Symbol, Anleitung, Material, Ziel für das Kind,
  Bildanhängen und Bezug zu Kompetenzen aus den Bögen
- **Vorschläge:** schwache Kompetenzen der letzten Wochen, zuletzt mit „o“ oder „−“
  beobachtete Kompetenzen, Ziele des aktiven Förderplans, zur Wiederholung
  vorgemerkte Aufgaben, passende Vorlagen aus der Bibliothek
- **Aufgabenbibliothek** je Klasse mit wiederverwendbaren Vorlagen (inkl.
  Differenzierungshinweisen)
- **Evaluation** je Aufgabe (*gut erledigt* / *teilweise* / *nicht gelungen*, Kommentar,
  erneut vorschlagen); aus der Evaluation kann direkt eine Beobachtung entstehen
- Export als kindgerechtes ODT/PDF, auch als PDF für die ganze Klasse

### 4.6 Ereignisse (erzieherische Arbeit)

- Ereignis zu einem Kind: Datum, Ereignisart (aus einem Katalog mit Kategorien), Ort,
  Beschreibung, Aussage des Kindes, Aussagen anderer, Konsequenzen (aus einem Katalog)
  mit Notizen, Status *offen* / *abgeschlossen*
- **betroffene Kinder** zusätzlich zum Hauptkind
- **Zuständigkeit:** Zuweisung an eine Lehrkraft (mit Benachrichtigung)
- Verknüpfung mit Elternkontakten des Kindes
- Dateianhänge
- **Journal** aller Änderungen
- Übersicht mit Filter nach Kind und Status

Kategorien, Ereignisarten, Orte und Konsequenzen pflegt die Verwaltung.

### 4.7 Diagnostik (standardisierte Tests)

Hinterlegte Verfahren (von der Verwaltung als Katalog pflegbar, erweiterbar):

| Verfahren | Lernbereich | Kennwerte | Vorbelegter Testplan |
|---|---|---|---|
| **HSP** (Hamburger Schreib-Probe; Testformen 1+, 2, 3, 4-5) | Rechtschreiben | Graphemtreffer, Wörter richtig, Strategien: alphabetisch, orthografisch, morphematisch, wortübergreifend (je Rohwert, Prozentrang, T-Wert) | Klasse 1–4, jeweils Mitte und Ende des Schuljahres |
| **SLS 1–4** (Salzburger Lese-Screening) | Lesen | Leseleistung (Rohwert und Lesequotient – das SLS weist keinen Prozentrang aus) | Ende Klasse 1, Mitte und Ende Klasse 2 |
| **ELFE II** (Leseverständnistest) | Lesen | Wort-, Satz-, Textverständnis und Gesamt (Rohwert, Prozentrang, T-Wert) | Ende Klasse 3 und 4 |

- Der **Testplan** legt fest, welche Testform in welchem Jahrgang zur Mitte oder am
  Ende des Schuljahres vorgesehen ist; die Verwaltung kann ihn ändern. SLS und ELFE II
  bilden gemeinsam den Verlauf im Lernbereich Lesen.
- **Eintragen:** klassenweise oder für ein Kind, mit Rohwert und Normwerten aus dem
  Auswertungsbogen (Prozentrang, T-Wert oder Lesequotient), Testdatum (Pflicht) und
  Bemerkung. Die App rechnet keine Normtabellen nach. Fehlt bei HSP oder ELFE II der
  Prozentrang, leitet sie ihn aus dem T-Wert ab und kennzeichnet das.
- **Nachtragen** von Ergebnissen früherer Schuljahre ist möglich.
- **Import** aus den Tabellen der Schule (XLSX/ODS).
- **Risikostufen** (Grenzen einstellbar, Standard). HSP und ELFE II werden über den
  Prozentrang eingestuft, das SLS über den Lesequotienten – nach der Auswertungstabelle
  der Schule (unter 90 unterdurchschnittlich, unter 80 schwach, unter 70 sehr schwach):

  | Stufe | Prozentrang (HSP, ELFE II) | Lesequotient (SLS) |
  |---|---|---|
  | beobachten | ≤ 25 | ≤ 89 |
  | auffällig | ≤ 16 | ≤ 79 |
  | deutlich auffällig | ≤ 10 | ≤ 69 |

  Bei der HSP zählen auch die Prozentränge der Strategien.
- **Übersicht** je Klasse und **Verlauf je Kind** mit Diagramm über die Schuljahre –
  je Skala eine eigene Grafik (Lesen: SLS als Lesequotient, ELFE II als Prozentrang);
  Tendenz zum vorigen Test derselben Skala (ab 10 Punkten Unterschied).
- **Förderangaben je Kind und Schuljahr:** Nachteilsausgleich (NTA), Förderkurs (FK),
  externe Förderung (EF), Förderschwerpunkt, Anmerkungen. Ob ein aktiver Förderplan
  besteht, ergänzt die App selbst.
- **Stufenauswertung** für einen ganzen Jahrgang und Testzeitpunkt, als ODT/PDF mit
  Schullogo: Durchschnittswerte je Klasse und Jahrgang, Liste aller Kinder mit
  Risikostufe samt Werten, Tendenz und Förderangaben.

---

## 5. Benachrichtigungen

Benachrichtigungen erscheinen unter der **Glocke** und auf Wunsch per **E-Mail**
(mit vollem Namen des Kindes). Jede Lehrkraft wählt im Konto den Takt – *sofort*,
*einmal täglich gesammelt* (Mo–Fr 15 Uhr) oder *keine E-Mails* – und kann einzelne
Arten abbestellen. Wer etwas selbst tut, wird darüber nicht benachrichtigt.

| Art | Wer wird benachrichtigt |
|---|---|
| Neues Ereignis | Klassenleitungen des Kindes und der betroffenen Kinder |
| Ereignis aktualisiert | Klassenleitungen, anlegende und zuständige Lehrkraft |
| Ereignis zugewiesen | die neu zuständige Lehrkraft |
| Neuer Förderplan | Klassenleitung |
| Förderplan bearbeitet oder evaluiert | Klassenleitung, Ersteller |
| Neuer Elternkontakt | Klassenleitung |
| Anstehender Elterntermin (in drei Tagen) | Klassenleitung und Verfasser des Protokolls |
| Kind neu in der Klasse | Klassenleitung |
| Neue Klassenzuordnung | die zugeordnete Lehrkraft |

---

## 6. Verwaltung (Zahnrad)

- **Benutzer:** anlegen, E-Mail, Rolle, Passwort zurücksetzen, Klassenzuordnungen
  (Klassenleitung / Fach)
- **Kinder und Klassen:** bearbeiten, Jahrgänge der Klassen, Import von Kindern und
  Bögen aus Tabellen
- **Beobachtungsbögen:** Bögen, Bereiche, Items, Jahrgangszuordnung, Pflicht/optional
- **Grundeinstellungen:** Schuljahr und Beginn, zwei **Elternsprechtage** (steuern
  Hinweise auf der Startseite), Zeitfenster für Arbeitsplan-Vorschläge,
  Aufbewahrungsfrist
- **Schuljahreswechsel:** Kinder steigen einen Jahrgang auf, Jahrgang 4 wird
  archiviert, Wiederholer lassen sich ausnehmen, Klassenzuordnungen der Lehrkräfte
  wandern mit; mit Vorschau
- **Erzieherische Kataloge:** Kategorien, Ereignisarten, Orte, Konsequenzen
- **Diagnostik-Katalog:** Verfahren, Testformen, Kennwerte, Testplan, Risikogrenzen, Schullogo
- **E-Mail-Versand:** Anzeige der Einstellungen, Testmail
- **Aufbewahrung:** archivierte Kinder mit Ablaufdatum; Löschung nur nach
  ausdrücklicher Auswahl

---

## 7. Dokumente, die die App erzeugt

| Dokument | Format | Inhalt |
|---|---|---|
| Grundlagenblatt | ODT, PDF | Stärken, Förderbedarf, Entwicklung, Informationen, Elternabsprachen |
| Förderplan | ODT, PDF | Ziele mit Ist, Soll, Maßnahmen, Evaluation |
| Gesprächsprotokoll | ODT, PDF | Schulvorlage für Elterngespräche |
| Arbeitsplan | ODT, PDF | kindgerechter Plan; auch als Klassensatz |
| Zeugnismaterial | ODT, PDF | Beobachtungen, Kommentare, Mittelwerte je Kind oder Klasse |
| Stufenauswertung Diagnostik | ODT, PDF | Jahrgangsübersicht mit auffälligen Kindern und Förderangaben |
| Gesamtakte | ODT, PDF | alles zu einem Kind |

---

## 8. Was die App (noch) nicht kann

Diese Grenzen sind wichtig, wenn Abläufe um die App herum geplant werden:

- **Keine eigene Förderkonferenz-Funktion:** Es gibt keinen Datensatz „Konferenz“,
  keine Tagesordnung, keine Konferenzliste mit mehreren Kindern und kein
  Konferenzprotokoll. Ergebnisse einer Konferenz lassen sich heute nur über
  vorhandene Stellen festhalten (Förderplan, Förderangaben, Grundlagenblatt,
  Protokoll, Ereignis).
- **Keine Terminplanung oder Kalender** (außer den zwei Elternsprechtagen und dem
  „nächsten Termin“ im Gesprächsprotokoll).
- **Keine Aufgabenverwaltung zwischen Lehrkräften** (außer der Zuständigkeit bei Ereignissen).
- **Kein Zugang für Eltern oder Kinder**; Informationen gehen nur als Dokument hinaus.
- **Keine Nachrichten oder Chat** zwischen Lehrkräften; nur Benachrichtigungen.
- **Keine Beteiligung externer Fachkräfte** (Sonderpädagogik, Schulsozialarbeit,
  Therapeuten) mit eigenem Konto außer als normale Lehrkraft-Benutzer.
- **Keine Normberechnung** für Tests: Normwerte werden vom Auswertungsbogen übernommen.
- **Keine automatischen Formulierungen** für Zeugnisse oder Förderpläne; die App
  sammelt und schlägt vor, formulieren muss die Lehrkraft.
- **Förderangaben** sind einfache Merkmale je Schuljahr (NTA, Förderkurs, externe
  Förderung, Schwerpunkt, Anmerkungen), keine eigenen Verfahren mit Fristen.

---

## 9. Hinweise für die Planung von Förderkonferenzen

Informationen, die zur Vorbereitung einer Förderkonferenz bereits in der App liegen:

- **Welche Kinder?** Förderplan-Kandidaten auf der Startseite, Kinder mit Risikostufe
  in der Diagnostik-Übersicht und in der Stufenauswertung, Kinder mit Lernstand
  „reicht noch nicht“ in der Klassenübersicht, Kinder mit fälliger
  Förderplan-Evaluation.
- **Lernstand und Entwicklung:** Bericht je Kind mit Entwicklungsverlauf,
  Klassenübersicht, Zeugnismaterial.
- **Testergebnisse:** Diagnostik-Verlauf je Kind, Stufenauswertung je Jahrgang.
- **Bisherige Förderung:** Grundlagenblatt, aktueller und frühere Förderpläne mit
  Evaluation, Förderangaben (NTA, Förderkurs, externe Förderung), Arbeitspläne mit
  Evaluation.
- **Elternperspektive:** Elternkontakte, Gesprächsprotokolle mit Vereinbarungen,
  Elternberatungen.
- **Verhalten und soziale Situation:** erzieherische Ereignisse mit Konsequenzen.
- **Gesamtbild eines Kindes:** Schülerakte oder Gesamtakte als Dokument.

Ergebnisse einer Förderkonferenz können derzeit so in die App zurückfließen:

- neue oder fortgeschriebene **Förderpläne** (Ziele, Maßnahmen, Evaluationsdatum)
- **Förderangaben** des Schuljahres aktualisieren (NTA, Förderkurs, externe Förderung, Schwerpunkt)
- **Grundlagenblatt** ergänzen (Förderbedarf, Absprachen mit Eltern)
- **Arbeitspläne** für die Umsetzung im Unterricht
- **Gesprächsprotokoll** für ein anschließendes Elterngespräch; der vereinbarte
  Folgetermin erzeugt eine Erinnerung
- Benachrichtigungen informieren die Klassenleitung automatisch über neue und
  geänderte Förderpläne

Mögliche Erweiterungen der App (z. B. ein Konferenz-Datensatz mit Kinderliste,
Teilnehmenden, Beschlüssen und Zuständigkeiten) sind technisch umsetzbar und können
im Konzept als Wunsch formuliert werden.
