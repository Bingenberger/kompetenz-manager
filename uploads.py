import os
import shutil
import uuid
from pathlib import Path

from flask import current_app
from PIL import Image, ImageOps

ALLOWED_WORKPLAN_MIMETYPES = {'image/jpeg', 'image/png', 'image/webp'}
WORKPLAN_MAX_UPLOAD_BYTES = 5 * 1024 * 1024
ALLOWED_ERZIEHUNG_MIMETYPES = {'image/jpeg', 'image/png', 'image/webp', 'application/pdf'}
ERZIEHUNG_MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def _normalize_relative_upload_path(rel_path):
    raw = (rel_path or '').replace('\\', '/').strip().lstrip('/')
    if raw.startswith('uploads/'):
        raw = raw[len('uploads/'):]
    normalized = os.path.normpath(raw).replace('\\', '/')
    if normalized in {'', '.', '..'} or normalized.startswith('../'):
        return None
    return normalized


def get_legacy_upload_root():
    return Path(current_app.root_path) / 'static' / 'uploads'


def get_protected_upload_root():
    configured = current_app.config.get('PROTECTED_UPLOAD_FOLDER')
    if configured:
        return Path(configured)
    return Path(current_app.instance_path) / 'protected_uploads'


def ensure_upload_roots():
    get_legacy_upload_root().mkdir(parents=True, exist_ok=True)
    get_protected_upload_root().mkdir(parents=True, exist_ok=True)


def protected_upload_path_for_rel(rel_path):
    normalized = _normalize_relative_upload_path(rel_path)
    if not normalized:
        return None
    return get_protected_upload_root() / normalized


def legacy_upload_path_for_rel(rel_path):
    normalized = _normalize_relative_upload_path(rel_path)
    if not normalized:
        return None
    return get_legacy_upload_root() / normalized


def resolve_existing_upload_path(rel_path):
    normalized = _normalize_relative_upload_path(rel_path)
    if not normalized:
        return None

    protected = protected_upload_path_for_rel(normalized)
    if protected and protected.is_file():
        return protected

    legacy = legacy_upload_path_for_rel(normalized)
    if legacy and legacy.is_file():
        return legacy

    return None


def loesche_upload_datei(rel_path):
    """Entfernt eine hochgeladene Datei von der Platte.

    Gibt True zurueck, wenn eine Datei entfernt wurde. Fehlt sie bereits oder
    laesst der Pfad sich nicht aufloesen, ist das kein Fehler: Ziel ist, dass die
    Datei danach nicht mehr existiert.
    """
    absolute = resolve_existing_upload_path(rel_path)
    if not absolute:
        return False
    try:
        absolute.unlink()
        return True
    except OSError as error:
        current_app.logger.warning('Upload konnte nicht geloescht werden (%s): %s', rel_path, error)
        return False


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

    ensure_upload_roots()
    filename = f"{uuid.uuid4().hex}.jpg"
    speicher_pfad = get_protected_upload_root() / filename
    if komprimiere_und_speichere(file_storage, str(speicher_pfad)):
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

    ensure_upload_roots()
    rel_dir = 'workplan'
    abs_dir = get_protected_upload_root() / rel_dir
    abs_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{uuid.uuid4().hex}.jpg"
    abs_path = abs_dir / filename
    if komprimiere_und_speichere(file_storage, str(abs_path)):
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

    ensure_upload_roots()
    rel_dir = 'erziehung'
    abs_dir = get_protected_upload_root() / rel_dir
    abs_dir.mkdir(parents=True, exist_ok=True)

    if mimetype == 'application/pdf':
        filename = f"{uuid.uuid4().hex}.pdf"
        abs_path = abs_dir / filename
        file_storage.stream.seek(0)
        with open(abs_path, 'wb') as target:
            shutil.copyfileobj(file_storage.stream, target)
        return f"{rel_dir}/{filename}", mimetype

    filename = f"{uuid.uuid4().hex}.jpg"
    abs_path = abs_dir / filename
    if komprimiere_und_speichere(file_storage, str(abs_path)):
        return f"{rel_dir}/{filename}", 'image/jpeg'
    return None, None
