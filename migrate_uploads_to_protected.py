#!/usr/bin/env python3
import argparse
import shutil
from pathlib import Path

from app import app
from uploads import get_legacy_upload_root, get_protected_upload_root


def migrate(copy_only=True):
    with app.app_context():
        legacy_root = get_legacy_upload_root()
        protected_root = get_protected_upload_root()
        protected_root.mkdir(parents=True, exist_ok=True)

        if not legacy_root.exists():
            print(f"Kein Legacy-Upload-Ordner gefunden: {legacy_root}")
            return 0

        copied = 0
        skipped = 0
        moved = 0

        for source in sorted(legacy_root.rglob('*')):
            if not source.is_file():
                continue
            rel_path = source.relative_to(legacy_root)
            target = protected_root / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)

            if target.exists():
                skipped += 1
                continue

            if copy_only:
                shutil.copy2(source, target)
                copied += 1
            else:
                shutil.move(str(source), str(target))
                moved += 1

        mode = 'Kopiert' if copy_only else 'Verschoben'
        changed = copied if copy_only else moved
        print(f"{mode}: {changed}")
        print(f"Übersprungen (bereits vorhanden): {skipped}")
        print(f"Legacy: {legacy_root}")
        print(f"Protected: {protected_root}")
        return 0


def main():
    parser = argparse.ArgumentParser(description='Migriert bestehende Uploads in den geschützten Upload-Ordner.')
    parser.add_argument(
        '--move',
        action='store_true',
        help='Dateien verschieben statt nur kopieren. Standard ist Kopieren.',
    )
    args = parser.parse_args()
    raise SystemExit(migrate(copy_only=not args.move))


if __name__ == '__main__':
    main()
