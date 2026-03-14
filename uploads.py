import os
import shutil
import uuid

from flask import current_app
from PIL import Image, ImageOps

ALLOWED_WORKPLAN_MIMETYPES = {'image/jpeg', 'image/png', 'image/webp'}
WORKPLAN_MAX_UPLOAD_BYTES = 5 * 1024 * 1024
ALLOWED_ERZIEHUNG_MIMETYPES = {'image/jpeg', 'image/png', 'image/webp', 'application/pdf'}
ERZIEHUNG_MAX_UPLOAD_BYTES = 10 * 1024 * 1024


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


def speichere_upload_workplan_bild(file_storage):
    """Speichert ein Aufgabenfoto als JPG im Unterordner workplan/ inkl. Basisschutz."""
    if not file_storage or file_storage.filename == '':
        return None

    mimetype = (file_storage.mimetype or '').lower().strip()
    if mimetype not in ALLOWED_WORKPLAN_MIMETYPES:
        return None

    file_storage.stream.seek(0, os.SEEK_END)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)
    if size > WORKPLAN_MAX_UPLOAD_BYTES:
        return None

    rel_dir = 'workplan'
    abs_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], rel_dir)
    os.makedirs(abs_dir, exist_ok=True)

    filename = f"{uuid.uuid4().hex}.jpg"
    abs_path = os.path.join(abs_dir, filename)
    if komprimiere_und_speichere(file_storage, abs_path):
        return f"{rel_dir}/{filename}"
    return None


def speichere_upload_erziehung_anhang(file_storage):
    """Speichert Bild/PDF für das Modul erzieherische Arbeit im Unterordner erziehung/."""
    if not file_storage or file_storage.filename == '':
        return None, None

    mimetype = (file_storage.mimetype or '').lower().strip()
    if mimetype not in ALLOWED_ERZIEHUNG_MIMETYPES:
        return None, None

    file_storage.stream.seek(0, os.SEEK_END)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)
    if size > ERZIEHUNG_MAX_UPLOAD_BYTES:
        return None, None

    rel_dir = 'erziehung'
    abs_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], rel_dir)
    os.makedirs(abs_dir, exist_ok=True)

    if mimetype == 'application/pdf':
        filename = f"{uuid.uuid4().hex}.pdf"
        abs_path = os.path.join(abs_dir, filename)
        file_storage.stream.seek(0)
        with open(abs_path, 'wb') as target:
            shutil.copyfileobj(file_storage.stream, target)
        return f"{rel_dir}/{filename}", mimetype

    filename = f"{uuid.uuid4().hex}.jpg"
    abs_path = os.path.join(abs_dir, filename)
    if komprimiere_und_speichere(file_storage, abs_path):
        return f"{rel_dir}/{filename}", 'image/jpeg'
    return None, None
