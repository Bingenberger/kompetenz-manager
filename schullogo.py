"""Schullogo für erzeugte Dokumente (etwa die Stufenauswertung).

Das Logo gehört der Schule, nicht dem Programm: Es liegt im instance-Ordner
der Installation und wird über die Verwaltung hochgeladen - nicht im
Repository.
"""

import os
import struct

from flask import current_app

MAX_BYTES = 2 * 1024 * 1024
TYPEN = {'png': 'image/png', 'jpg': 'image/jpeg'}


def _pfad(endung):
    return os.path.join(current_app.instance_path, f'schullogo.{endung}')


def erkenne_bild(daten):
    """(Endung, Breite, Höhe) für PNG und JPEG, sonst None."""
    if daten[:8] == b'\x89PNG\r\n\x1a\n' and len(daten) >= 24:
        breite, hoehe = struct.unpack('>II', daten[16:24])
        return 'png', breite, hoehe
    if daten[:2] == b'\xff\xd8':
        position = 2
        while position + 9 < len(daten):
            if daten[position] != 0xFF:
                position += 1
                continue
            marker = daten[position + 1]
            laenge = struct.unpack('>H', daten[position + 2:position + 4])[0]
            if marker in (0xC0, 0xC1, 0xC2):
                hoehe, breite = struct.unpack('>HH', daten[position + 5:position + 9])
                return 'jpg', breite, hoehe
            position += 2 + laenge
    return None


def speichere(daten):
    """Speichert das Logo. Gibt eine Fehlermeldung zurück oder None."""
    if len(daten) > MAX_BYTES:
        return 'Das Logo darf höchstens 2 MB groß sein.'
    erkannt = erkenne_bild(daten)
    if not erkannt or not erkannt[1] or not erkannt[2]:
        return 'Bitte ein Logo als PNG- oder JPEG-Datei hochladen.'
    entferne()
    os.makedirs(current_app.instance_path, exist_ok=True)
    with open(_pfad(erkannt[0]), 'wb') as datei:
        datei.write(daten)
    return None


def entferne():
    for endung in TYPEN:
        try:
            os.remove(_pfad(endung))
        except FileNotFoundError:
            pass


def lade():
    """dict mit data, mime, width_cm, height_cm (Höhe 2,2 cm) oder None."""
    for endung, mime in TYPEN.items():
        pfad = _pfad(endung)
        if os.path.exists(pfad):
            with open(pfad, 'rb') as datei:
                daten = datei.read()
            erkannt = erkenne_bild(daten)
            if not erkannt:
                return None
            _, breite, hoehe = erkannt
            hoehe_cm = 2.2
            return {'data': daten, 'mime': mime, 'height_cm': hoehe_cm, 'width_cm': round(hoehe_cm * breite / hoehe, 2)}
    return None
