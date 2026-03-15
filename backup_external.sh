#!/usr/bin/env bash
set -euo pipefail

# Externes Backup-Skript für den Kompetenz-Manager
# - unterstützt SQLite und PostgreSQL
# - sichert DB + Uploads/Fotos
# - geeignet für Cron / systemd timer

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

# Optional lokale Konfiguration laden (enthält z. B. DATABASE_URL)
if [[ -f ".env.local" ]]; then
  # shellcheck disable=SC1091
  source ".env.local"
fi

BACKUP_ROOT="${BACKUP_ROOT:-$APP_DIR/backups}"
TIMESTAMP="$(date +%Y-%m-%d_%H-%M-%S)"
HOST_TAG="${HOST_TAG:-$(hostname -s 2>/dev/null || echo host)}"
TARGET_DIR="$BACKUP_ROOT/${HOST_TAG}_${TIMESTAMP}"
KEEP_DAYS="${KEEP_DAYS:-14}"

mkdir -p "$TARGET_DIR"

log() {
  printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"
}

normalize_db_url() {
  local raw="${1:-}"
  if [[ "$raw" == postgres://* ]]; then
    printf 'postgresql://%s\n' "${raw#postgres://}"
  else
    printf '%s\n' "$raw"
  fi
}

default_sqlite_path() {
  if [[ -f "$APP_DIR/schule.db" ]]; then
    printf '%s\n' "$APP_DIR/schule.db"
  else
    printf '%s\n' "$APP_DIR/instance/schule.db"
  fi
}

sqlite_path_from_uri() {
  local uri="$1"
  # Unterstützt sqlite:////abs/pfad oder sqlite:///relativer/pfad
  if [[ "$uri" =~ ^sqlite:////(.+)$ ]]; then
    printf '/%s\n' "${BASH_REMATCH[1]}"
    return 0
  fi
  if [[ "$uri" =~ ^sqlite:///(.+)$ ]]; then
    local p="${BASH_REMATCH[1]}"
    if [[ "$p" = /* ]]; then
      printf '%s\n' "$p"
    else
      printf '%s\n' "$APP_DIR/$p"
    fi
    return 0
  fi
  return 1
}

backup_sqlite() {
  local sqlite_path="$1"
  if [[ ! -f "$sqlite_path" ]]; then
    echo "SQLite-Datei nicht gefunden: $sqlite_path" >&2
    return 1
  fi

  local out="$TARGET_DIR/sqlite_schule.db"
  if command -v sqlite3 >/dev/null 2>&1; then
    log "SQLite-Dump via sqlite3 .backup"
    sqlite3 "$sqlite_path" ".backup '$out'"
  else
    log "sqlite3 nicht gefunden, kopiere DB-Datei direkt"
    cp -a "$sqlite_path" "$out"
  fi

  gzip -f "$out"
}

backup_postgres() {
  local db_url="$1"
  local out="$TARGET_DIR/postgres_dump.sql"
  if ! command -v pg_dump >/dev/null 2>&1; then
    echo "pg_dump nicht gefunden. Bitte postgresql-client installieren." >&2
    return 1
  fi

  log "PostgreSQL-Dump via pg_dump"
  # --no-owner/--no-privileges vereinfacht Restore auf anderem System
  pg_dump --no-owner --no-privileges --format=plain --file="$out" "$db_url"
  gzip -f "$out"
}

backup_uploads() {
  local uploads_dir="$APP_DIR/static/uploads"
  local protected_uploads_dir="$APP_DIR/instance/protected_uploads"
  if [[ -d "$uploads_dir" ]]; then
    log "Sichere Legacy-Uploads/Fotos"
    tar -czf "$TARGET_DIR/uploads_legacy.tar.gz" -C "$APP_DIR/static" uploads
  fi
  if [[ -d "$protected_uploads_dir" ]]; then
    log "Sichere geschützte Uploads/Fotos"
    tar -czf "$TARGET_DIR/uploads_protected.tar.gz" -C "$APP_DIR/instance" protected_uploads
  fi
  if [[ ! -d "$uploads_dir" && ! -d "$protected_uploads_dir" ]]; then
    log "Keine Upload-Ordner gefunden, überspringe"
  fi
}

write_manifest() {
  local db_mode="$1"
  local db_ref="$2"
  cat > "$TARGET_DIR/manifest.txt" <<EOF
created_at=$(date --iso-8601=seconds)
app_dir=$APP_DIR
db_mode=$db_mode
db_reference=$db_ref
host=$HOST_TAG
EOF
}

cleanup_old_backups() {
  if [[ -z "$KEEP_DAYS" ]]; then
    return 0
  fi
  if [[ ! "$KEEP_DAYS" =~ ^[0-9]+$ ]]; then
    log "KEEP_DAYS ist nicht numerisch ($KEEP_DAYS), überspringe Cleanup"
    return 0
  fi
  log "Lösche Backups älter als $KEEP_DAYS Tage (falls vorhanden)"
  find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -mtime "+$KEEP_DAYS" -print -exec rm -rf {} +
}

main() {
  local raw_db_url="${DATABASE_URL:-}"
  local db_url
  db_url="$(normalize_db_url "$raw_db_url")"

  log "Backup-Ziel: $TARGET_DIR"

  if [[ -n "$db_url" && "$db_url" == postgresql*://* ]]; then
    backup_postgres "$db_url"
    write_manifest "postgresql" "$db_url"
  else
    local sqlite_path
    if [[ -n "$db_url" && "$db_url" == sqlite:* ]]; then
      sqlite_path="$(sqlite_path_from_uri "$db_url")"
    else
      sqlite_path="$(default_sqlite_path)"
    fi
    backup_sqlite "$sqlite_path"
    write_manifest "sqlite" "$sqlite_path"
  fi

  backup_uploads

  log "Backup abgeschlossen"
  log "Inhalt:"
  ls -lh "$TARGET_DIR"

  cleanup_old_backups
}

main "$@"
