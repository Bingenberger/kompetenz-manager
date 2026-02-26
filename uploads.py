import os
import uuid

from flask import current_app
from PIL import Image, ImageOps


def komprimiere_und_speichere(file_storage, ziel_pfad):
    """
    Nimmt ein hochgeladenes Bild, korrigiert die Drehung,
    verkleinert es auf max 1024px Kantenlänge und speichert es als JPG.
    """
    try:
        img = Image.open(file_storage)
        img = ImageOps.exif_transpose(img)

        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        img.thumbnail((1024, 1024))
        img.save(ziel_pfad, "JPEG", quality=80, optimize=True)
        return True
    except Exception as e:
        print(f"Fehler beim Komprimieren: {e}")
        return False


def speichere_upload_bild(file_storage):
    """Speichert ein hochgeladenes Bild unter einem kollisionssicheren JPG-Dateinamen."""
    if not file_storage or file_storage.filename == '':
        return None

    filename = f"{uuid.uuid4().hex}.jpg"
    speicher_pfad = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
    if komprimiere_und_speichere(file_storage, speicher_pfad):
        return filename
    return None
