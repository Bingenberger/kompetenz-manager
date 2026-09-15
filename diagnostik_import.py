"""Import von Diagnostik-Ergebnissen aus den Auswertungsmappen der Schule.

Erkannt werden drei Formate, so wie sie in der Schule im Einsatz sind:

- HSP-Auswertungsmappe (XLSX): Blatt "Gesamt" mit Vorname, Name und je
  Kennwert einer Spalte Rohwert (W, GT, AS, OS, MS, WÜ) mit folgenden
  Spalten PR… und T….
- ELFE-II-Auswertungstabelle (XLSX): Blatt "Daten", Gruppenzeile
  (Wortverständnis, Satzverständnis, Textverständnis, Gesamtauswertung) über
  einer Zeile RW / T-W / PR.
- SLS-Auswertungstabelle (ODS oder XLSX): Name, Vorname w / Vorname m, RW, LQ
  (kein Prozentrang - das SLS weist keinen aus).

Die Mappen rechnen Normwerte per Formel aus. Gelesen werden die zuletzt
gespeicherten Ergebnisse dieser Formeln - deshalb muss eine Datei nach dem
Ausfüllen einmal in Excel oder LibreOffice gespeichert worden sein.

Die Normtabellen in den Mappen werden nicht gelesen und nicht übernommen.
"""

import math
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from defusedxml import ElementTree

from openpyxl import load_workbook

ERLAUBTE_ENDUNGEN = ('.xlsx', '.ods')
MAX_SPALTEN = 256
MAX_ZEILEN = 2000

HSP_KENNWERTE = {
    'W': 'Wörter richtig',
    'GT': 'Graphemtreffer',
    'AS': 'Alphabetische Strategie',
    'OS': 'Orthografische Strategie',
    'MS': 'Morphematische Strategie',
    'WÜ': 'Wortübergreifende Strategie',
}
ELFE_GRUPPEN = {
    'wortverständnis': 'Wortverständnis',
    'satzverständnis': 'Satzverständnis',
    'textverständnis': 'Textverständnis',
    'gesamtauswertung': 'Gesamt',
    'gesamt': 'Gesamt',
}


class ImportFehler(ValueError):
    """Die Datei lässt sich nicht lesen oder hat kein bekanntes Format."""


@dataclass
class ImportZeile:
    nachname: str
    vorname: str
    # Kennwertname -> {wertart: Zahl}
    werte: dict = field(default_factory=dict)
    zeile: int = 0

    @property
    def name(self):
        return f'{self.vorname} {self.nachname}'.strip()


@dataclass
class ImportDatei:
    format: str             # 'hsp' | 'elfe' | 'sls'
    verfahren: str          # Suchbegriff für das Verfahren im Katalog
    jahrgang: int = None
    halbjahr: str = None
    zeilen: list = field(default_factory=list)
    kennwerte: list = field(default_factory=list)
    ohne_namen: int = 0
    hinweise: list = field(default_factory=list)


# ----------------------------------------------------------------------
# Tabellen lesen
# ----------------------------------------------------------------------

def lese_tabellen(dateiname, inhalt):
    """Blattname -> Liste von Zeilen (Listen von Zellwerten)."""
    endung = (dateiname or '').lower().rsplit('.', 1)[-1]
    try:
        if endung == 'xlsx':
            return _lese_xlsx(inhalt)
        if endung == 'ods':
            return _lese_ods(inhalt)
    except ImportFehler:
        raise
    except Exception as fehler:  # noqa: BLE001 - kaputte Datei verständlich melden
        raise ImportFehler(f'Die Datei ließ sich nicht lesen ({type(fehler).__name__}).') from fehler
    raise ImportFehler('Bitte eine XLSX- oder ODS-Datei hochladen.')


def _lese_xlsx(inhalt):
    mappe = load_workbook(BytesIO(inhalt), data_only=True, read_only=True)
    blaetter = {}
    for blatt in mappe.worksheets:
        zeilen = []
        for zeile in blatt.iter_rows(max_row=MAX_ZEILEN, max_col=MAX_SPALTEN, values_only=True):
            zeilen.append(list(zeile))
        blaetter[blatt.title] = zeilen
    mappe.close()
    return blaetter


_ODS = {
    'table': 'urn:oasis:names:tc:opendocument:xmlns:table:1.0',
    'office': 'urn:oasis:names:tc:opendocument:xmlns:office:1.0',
    'text': 'urn:oasis:names:tc:opendocument:xmlns:text:1.0',
}


def _lese_ods(inhalt):
    tabelle = '{%s}' % _ODS['table']
    office = '{%s}' % _ODS['office']
    try:
        with zipfile.ZipFile(BytesIO(inhalt)) as archiv:
            wurzel = ElementTree.fromstring(archiv.read('content.xml'))
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as fehler:
        raise ImportFehler('Die ODS-Datei ist beschädigt.') from fehler

    blaetter = {}
    for blatt in wurzel.iter(tabelle + 'table'):
        zeilen = []
        for zeile in blatt.iter(tabelle + 'table-row'):
            werte = []
            for zelle in zeile:
                if zelle.tag not in (tabelle + 'table-cell', tabelle + 'covered-table-cell'):
                    continue
                anzahl = int(zelle.get(tabelle + 'number-columns-repeated', '1'))
                wert = _ods_zellwert(zelle, office)
                werte.extend([wert] * min(anzahl, max(0, MAX_SPALTEN - len(werte))))
            while werte and werte[-1] is None:
                werte.pop()
            wiederholt = int(zeile.get(tabelle + 'number-rows-repeated', '1'))
            # Lange leere Wiederholungen sind nur Formatierung bis zum Blattende.
            for _ in range(1 if not werte else min(wiederholt, MAX_ZEILEN)):
                if len(zeilen) >= MAX_ZEILEN:
                    break
                zeilen.append(list(werte))
        blaetter[blatt.get(tabelle + 'name')] = zeilen
    return blaetter


def _ods_zellwert(zelle, office):
    typ = zelle.get(office + 'value-type')
    if typ in ('float', 'percentage', 'currency'):
        try:
            return float(zelle.get(office + 'value'))
        except (TypeError, ValueError):
            return None
    text = '\n'.join(''.join(absatz.itertext()) for absatz in zelle.findall('text:p', _ODS))
    return text or None


# ----------------------------------------------------------------------
# Hilfen
# ----------------------------------------------------------------------

def _text(wert):
    if wert is None:
        return ''
    if isinstance(wert, float) and wert.is_integer():
        wert = int(wert)
    return ' '.join(str(wert).split())


def zahl(wert):
    """Ganze Zahl aus einer Zelle; kaufmännisch gerundet. Fehlerwerte -> None."""
    if wert is None or isinstance(wert, bool):
        return None
    if isinstance(wert, (int, float)):
        if isinstance(wert, float) and (math.isnan(wert) or math.isinf(wert)):
            return None
        return int(math.floor(wert + 0.5))
    text = str(wert).strip().replace(',', '.')
    if not text or text.startswith('#'):
        return None
    try:
        return int(math.floor(float(text) + 0.5))
    except ValueError:
        return None


def normalisiere_name(text):
    """Für den Namensvergleich: klein, ohne Akzente, Umlaute ausgeschrieben."""
    text = _text(text).casefold()
    for alt, neu in (('ä', 'ae'), ('ö', 'oe'), ('ü', 'ue'), ('ß', 'ss')):
        text = text.replace(alt, neu)
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(zeichen for zeichen in text if not unicodedata.combining(zeichen))
    return re.sub(r'[^a-z0-9]+', ' ', text).strip()


def _finde_kopfzeile(zeilen, pflicht, max_zeile=15):
    """(Zeilenindex, {normalisierter Kopf: Spaltenindex}) der ersten passenden Zeile."""
    for index, zeile in enumerate(zeilen[:max_zeile]):
        koepfe = {}
        for spalte, wert in enumerate(zeile):
            kopf = _text(wert)
            if kopf and kopf.casefold() not in koepfe:
                koepfe[kopf.casefold()] = spalte
        if all(any(k == p or k.startswith(p) for k in koepfe) for p in pflicht):
            return index, koepfe
    return None, None


def _wert_rechts_von(zeilen, beschriftung, max_zeile=10):
    for zeile in zeilen[:max_zeile]:
        for spalte, wert in enumerate(zeile):
            if _text(wert).casefold().rstrip(':') == beschriftung:
                for rechts in zeile[spalte + 1:]:
                    if _text(rechts):
                        return rechts
    return None


def _wert_unter(zeilen, beschriftung, max_zeile=10):
    for index, zeile in enumerate(zeilen[:max_zeile]):
        for spalte, wert in enumerate(zeile):
            if _text(wert).casefold() == beschriftung and index + 1 < len(zeilen):
                darunter = zeilen[index + 1]
                return darunter[spalte] if spalte < len(darunter) else None
    return None


def _zelle(zeile, spalte):
    return zeile[spalte] if spalte is not None and spalte < len(zeile) else None


# ----------------------------------------------------------------------
# Formate
# ----------------------------------------------------------------------

def lese_import(dateiname, inhalt):
    """Erkennt das Format und liest die Ergebnisse. Wirft ImportFehler."""
    blaetter = lese_tabellen(dateiname, inhalt)
    for leser in (_lese_hsp, _lese_elfe, _lese_sls):
        datei = leser(blaetter, dateiname or '')
        if datei:
            _pruefe_formelergebnisse(datei)
            return datei
    raise ImportFehler(
        'Das Format wurde nicht erkannt. Unterstützt werden die HSP-Auswertungsmappe '
        '(Blatt „Gesamt“), die ELFE-II-Auswertungstabelle (Blatt „Daten“) und die '
        'SLS-Auswertungstabelle.'
    )


def _zeitpunkt_aus_dateiname(dateiname):
    treffer = re.search(r'(mitte|ende)\D{0,3}(\d)', dateiname, re.IGNORECASE)
    if not treffer:
        return None, None
    return int(treffer.group(2)), treffer.group(1).lower()


def _lese_hsp(blaetter, dateiname):
    zeilen = blaetter.get('Gesamt')
    if not zeilen:
        return None
    kopf_index, koepfe = _finde_kopfzeile(zeilen, ['vorname', 'name', 'gt'])
    if kopf_index is None:
        return None

    kopfzeile = zeilen[kopf_index]
    spalten = {}  # Kennwert -> {wertart: spalte}
    aktuell = None
    for spalte, wert in enumerate(kopfzeile):
        kopf = _text(wert)
        if kopf.upper() in HSP_KENNWERTE:
            aktuell = HSP_KENNWERTE[kopf.upper()]
            spalten[aktuell] = {'rohwert': spalte}
        elif aktuell and re.fullmatch(r'PR\d*', kopf, re.IGNORECASE):
            spalten[aktuell].setdefault('prozentrang', spalte)
        elif aktuell and re.fullmatch(r'T[A-Z]?\d*', kopf, re.IGNORECASE):
            spalten[aktuell].setdefault('t_wert', spalte)
        elif kopf:
            aktuell = None

    jahrgang, halbjahr = _zeitpunkt_aus_dateiname(dateiname)
    datei = ImportDatei(format='hsp', verfahren='HSP', jahrgang=jahrgang, halbjahr=halbjahr,
                        kennwerte=list(spalten))
    vorname_spalte = koepfe.get('vorname')
    name_spalte = koepfe.get('name', koepfe.get('nachname'))
    _lies_zeilen(datei, zeilen[kopf_index + 1:], kopf_index + 2, spalten,
                 lambda z: (_text(_zelle(z, name_spalte)), _text(_zelle(z, vorname_spalte))))
    return datei


def _lese_elfe(blaetter, dateiname):
    zeilen = blaetter.get('Daten')
    if not zeilen:
        return None
    kopf_index, koepfe = _finde_kopfzeile(zeilen, ['name', 'vorname', 'rw'])
    if kopf_index is None or kopf_index == 0:
        return None

    gruppen_zeile = zeilen[kopf_index - 1]
    spalten = {}
    aktuell = None
    for spalte, wert in enumerate(zeilen[kopf_index]):
        gruppe = _text(_zelle(gruppen_zeile, spalte)).casefold()
        if gruppe:
            aktuell = ELFE_GRUPPEN.get(gruppe)
        if not aktuell:
            continue
        unterkopf = _text(wert).upper().replace(' ', '')
        art = {'RW': 'rohwert', 'T-W': 't_wert', 'TW': 't_wert', 'T': 't_wert', 'PR': 'prozentrang'}.get(unterkopf)
        if art:
            spalten.setdefault(aktuell, {}).setdefault(art, spalte)
    if not spalten:
        return None

    datei = ImportDatei(format='elfe', verfahren='ELFE', kennwerte=list(spalten))
    datei.jahrgang = zahl(_wert_unter(zeilen, 'schuljahr'))
    monat = re.match(r'\s*(\d+)', _text(_wert_unter(zeilen, 'schulmonat')))
    if monat:
        # ELFE II rechnet in Schulmonaten ab August: bis zum 8. Monat eher die
        # Mitte des Schuljahres, danach das Ende.
        datei.halbjahr = 'mitte' if int(monat.group(1)) <= 8 else 'ende'
    if not datei.jahrgang:
        datei.jahrgang, datei.halbjahr = _zeitpunkt_aus_dateiname(dateiname)
    datei.hinweise.append('„UT“ (Summe der T-Werte) wird nicht übernommen.')
    name_spalte = koepfe.get('name')
    vorname_spalte = koepfe.get('vorname')
    _lies_zeilen(datei, zeilen[kopf_index + 1:], kopf_index + 2, spalten,
                 lambda z: (_text(_zelle(z, name_spalte)), _text(_zelle(z, vorname_spalte))))
    return datei


def _lese_sls(blaetter, dateiname):
    for zeilen in blaetter.values():
        kopf_index, koepfe = _finde_kopfzeile(zeilen, ['name', 'rw', 'lq'])
        if kopf_index is None:
            continue
        # Das SLS weist nur Rohwert und Lesequotient aus. Eine PR-Spalte, die
        # jemand ergänzt hat, wird bewusst nicht gelesen.
        spalten = {'Leseleistung': {'rohwert': koepfe['rw'], 'lesequotient': koepfe['lq']}}
        datei = ImportDatei(format='sls', verfahren='SLS', kennwerte=list(spalten))
        datei.hinweise.append('Das SLS liefert Rohwert und Lesequotient; eingestuft wird über den LQ, einen Prozentrang gibt es nicht.')
        datei.jahrgang = zahl(_wert_rechts_von(zeilen, 'klassenstufe'))
        zeitpunkt = _text(_wert_rechts_von(zeilen, 'zeitpunkt') or _wert_rechts_von(zeilen, 'zeipunkt')).casefold()
        datei.halbjahr = zeitpunkt if zeitpunkt in ('mitte', 'ende') else None
        if not datei.jahrgang:
            datei.jahrgang, datei.halbjahr = _zeitpunkt_aus_dateiname(dateiname)
        vorname_spalten = [spalte for kopf, spalte in koepfe.items() if kopf.startswith('vorname')]
        name_spalte = koepfe.get('name', koepfe.get('nachname'))

        def namen(zeile):
            vorname = next((_text(_zelle(zeile, s)) for s in vorname_spalten if _text(_zelle(zeile, s))), '')
            return _text(_zelle(zeile, name_spalte)), vorname

        _lies_zeilen(datei, zeilen[kopf_index + 1:], kopf_index + 2, spalten, namen)
        return datei
    return None


PLATZHALTER = {'0', 'vorname', 'nachname', 'name', 'vorname:', 'name:'}
# Nur ganze Wörter - "Summerer" oder "Schnittger" sind Nachnamen, keine Summenzeilen.
SAMMELZEILE = re.compile(r'\b(durchschnitt|schnitt|mittelwert|summe|anzahl|median)\b', re.IGNORECASE)


def _ist_kein_name(text):
    """Platzhalter der Vorlagen ("VORNAME", 0 aus leeren Formeln) und Durchschnittszeilen."""
    return not text or text.casefold() in PLATZHALTER or bool(SAMMELZEILE.search(text))


def _lies_zeilen(datei, zeilen, erste_zeilennummer, spalten, namen):
    for nummer, zeile in enumerate(zeilen, start=erste_zeilennummer):
        nachname, vorname = namen(zeile)
        if SAMMELZEILE.search(f'{vorname} {nachname}'):
            continue
        nachname = '' if _ist_kein_name(nachname) else nachname
        vorname = '' if _ist_kein_name(vorname) else vorname
        werte = {}
        for kennwert, arten in spalten.items():
            eintrag = {art: zahl(_zelle(zeile, spalte)) for art, spalte in arten.items()}
            eintrag = {art: wert for art, wert in eintrag.items() if wert is not None}
            if eintrag:
                werte[kennwert] = eintrag
        if not nachname and not vorname:
            # Vorlagen rechnen auch für leere Zeilen Normwerte aus - ohne Namen
            # zählt eine Zeile nur, wenn jemand einen Rohwert eingetragen hat.
            if any('rohwert' in eintrag for eintrag in werte.values()) and datei.format == 'sls':
                datei.ohne_namen += 1
            continue
        datei.zeilen.append(ImportZeile(nachname=nachname, vorname=vorname, werte=werte, zeile=nummer))


def _pruefe_formelergebnisse(datei):
    if not datei.zeilen and datei.format in ('hsp', 'elfe'):
        # In der HSP-Mappe sind selbst die Namen Formeln: ohne gespeicherte
        # Ergebnisse sieht die Datei leer aus.
        datei.hinweise.append(
            'Es wurden keine Kinder mit Namen gefunden. Ist die Mappe ausgefüllt, bitte einmal in '
            'Excel oder LibreOffice öffnen und speichern – erst dann enthält sie die berechneten Werte.'
        )
    elif datei.zeilen and not any(zeile.werte for zeile in datei.zeilen):
        datei.hinweise.append(
            'Zu den Namen wurden keine Werte gefunden. Wurde die Datei nach dem Ausfüllen in '
            'Excel oder LibreOffice gespeichert? Erst dann enthält sie die berechneten Werte.'
        )


# ----------------------------------------------------------------------
# Zuordnung zu Kindern
# ----------------------------------------------------------------------

def ordne_kinder_zu(zeilen, kinder):
    """Zeilenindex -> Kind (oder None). Eindeutige Treffer über Vor- und Nachname.

    Stimmt nur der Vorname und ist er in der Klasse eindeutig, wird ebenfalls
    zugeordnet - Auswertungsmappen enthalten oft nur Rufnamen oder Kürzel.
    """
    nach_vollname = {}
    nach_vorname = {}
    for kind in kinder:
        nach_vollname.setdefault((normalisiere_name(kind.vorname), normalisiere_name(kind.nachname)), []).append(kind)
        nach_vorname.setdefault(normalisiere_name(kind.vorname), []).append(kind)

    zuordnung = {}
    vergeben = set()
    for index, zeile in enumerate(zeilen):
        schluessel = (normalisiere_name(zeile.vorname), normalisiere_name(zeile.nachname))
        treffer = nach_vollname.get(schluessel, [])
        if len(treffer) != 1 and not zeile.nachname:
            treffer = nach_vorname.get(schluessel[0], [])
        kind = treffer[0] if len(treffer) == 1 else None
        if kind and kind.id in vergeben:
            kind = None
        zuordnung[index] = kind
        if kind:
            vergeben.add(kind.id)
    return zuordnung
