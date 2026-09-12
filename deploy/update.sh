#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# update.sh
#
# Aktualisiert eine produktive Installation aus dem Git-Repository.
#
# Ablauf:
#   1. Vorbedingungen pruefen (Git-Repo, sauberer Arbeitsbaum, Remote erreichbar)
#   2. Backup von Datenbank und Uploads (backup_external.sh)
#   3. git fetch + Anzeige der eingehenden Commits, Rueckfrage
#   4. Fast-Forward auf origin/<branch>
#   5. Abhaengigkeiten nachziehen, wenn requirements.txt sich geaendert hat
#   6. Schema-Migration (update_db.py)
#   7. Regressionstests (isolierte Test-DB, beruehrt die Produktivdaten nicht)
#   8. Dienst neu starten und Health-Check
#
# Schlaegt ein Schritt ab Punkt 4 fehl, wird der Codestand automatisch auf den
# vorherigen Commit zurueckgerollt und der Dienst wieder gestartet.
# ---------------------------------------------------------------------------

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="${APP_NAME:-kompetenzkompass}"
SERVICE_NAME="${SERVICE_NAME:-$APP_NAME}"
ENV_FILE="${ENV_FILE:-/etc/$APP_NAME/$APP_NAME.env}"
BRANCH="${BRANCH:-main}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8008/login}"
HEALTH_RETRIES="${HEALTH_RETRIES:-10}"
HEALTH_DELAY="${HEALTH_DELAY:-2}"

ASSUME_YES=0
SKIP_TESTS=0
SKIP_BACKUP=0
ALLOW_DIRTY=0
NO_RESTART=0
DRY_RUN=0
FORCE=0
TARGET_REF=""

usage() {
  cat <<'USAGE'
Verwendung: bash deploy/update.sh [Optionen]

Optionen:
  -y, --yes           Ohne Rueckfrage durchlaufen (fuer Automatisierung)
  -n, --dry-run       Nur anzeigen, was aktualisiert wuerde; nichts veraendern
  -f, --force         Auch durchlaufen, wenn der Codestand bereits aktuell ist.
                      Fuehrt Backup, update_db.py, Tests und Neustart erneut aus -
                      z. B. nach einem manuellen Checkout.
      --ref <ref>     Auf einen bestimmten Commit/Tag aktualisieren statt auf HEAD des Branch
      --branch <name> Branch (Standard: main)
      --skip-tests    Regressionstests ueberspringen (nicht empfohlen)
      --skip-backup   Backup ueberspringen (nicht empfohlen)
      --allow-dirty   Trotz lokaler Aenderungen fortfahren (diese gehen verloren)
      --no-restart    Dienst nicht neu starten
  -h, --help          Diese Hilfe

Umgebungsvariablen: APP_NAME, SERVICE_NAME, ENV_FILE, BRANCH, HEALTH_URL
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -y|--yes) ASSUME_YES=1; shift ;;
    -n|--dry-run) DRY_RUN=1; shift ;;
    -f|--force) FORCE=1; shift ;;
    --ref) TARGET_REF="${2:?--ref benoetigt einen Commit/Tag}"; shift 2 ;;
    --branch) BRANCH="${2:?--branch benoetigt einen Namen}"; shift 2 ;;
    --skip-tests) SKIP_TESTS=1; shift ;;
    --skip-backup) SKIP_BACKUP=1; shift ;;
    --allow-dirty) ALLOW_DIRTY=1; shift ;;
    --no-restart) NO_RESTART=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unbekannte Option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

log()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '\033[33m    %s\033[0m\n' "$*"; }
ok()   { printf '\033[32m    %s\033[0m\n' "$*"; }
fail() { printf '\n\033[31mFEHLER: %s\033[0m\n' "$*" >&2; exit 1; }

# Laedt die Instanzkonfiguration in die Umgebung.
# Reihenfolge: .env.local zuerst, danach die systemd-Env-Datei - so gewinnt die
# Quelle, aus der der Dienst tatsaechlich startet, wenn beide vorhanden sind.
# Gibt die DATABASE_URL ohne Passwort aus, damit sie gefahrlos in Terminal und
# Logdateien landen kann.
masked_db_url() {
  local url="${DATABASE_URL:-}"
  if [[ -z "$url" ]]; then
    printf '(nicht gesetzt - SQLite-Standard)'
    return 0
  fi
  printf '%s' "$url" | sed -E 's#(://[^:/@]+):[^@]*@#\1:***@#'
}

ENV_SOURCES=""
load_env() {
  ENV_SOURCES=""
  if [[ -r "$APP_DIR/.env.local" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$APP_DIR/.env.local"
    set +a
    ENV_SOURCES=".env.local"
  fi
  if [[ -r "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
    ENV_SOURCES="${ENV_SOURCES:+$ENV_SOURCES, }$ENV_FILE"
  fi
  printf '%s' "$ENV_SOURCES"
}

cd "$APP_DIR"

# --- Schritt 1: Vorbedingungen --------------------------------------------
log "Vorbedingungen pruefen"

command -v git >/dev/null 2>&1 || fail "git ist nicht installiert."

if [[ ! -d "$APP_DIR/.git" ]]; then
  fail "$APP_DIR ist keine Git-Arbeitskopie.
       Erstumstellung einer manuell kopierten Installation:
           bash deploy/adopt_git.sh"
fi

OWNER="$(stat -c '%U' "$APP_DIR")"
if [[ "$(id -un)" != "$OWNER" ]]; then
  fail "Bitte als Besitzer des App-Verzeichnisses ausfuehren ($OWNER), nicht als $(id -un).
       Andernfalls entstehen Dateien mit falschem Eigentuemer.
       Beispiel: sudo -u $OWNER bash $APP_DIR/deploy/update.sh"
fi

PYTHON="$APP_DIR/venv/bin/python"
PIP="$APP_DIR/venv/bin/pip"
[[ -x "$PYTHON" ]] || fail "Kein venv unter $APP_DIR/venv gefunden."

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$CURRENT_BRANCH" != "$BRANCH" ]]; then
  warn "Arbeitskopie steht auf '$CURRENT_BRANCH', erwartet wurde '$BRANCH'."
fi

# Nur Aenderungen an versionierten Dateien blockieren. Unversionierte Dateien
# (Importlisten, Exporte, lokale Notizen) liegen auf Produktivservern regelmaessig
# im Projektordner und stehen einem Update nicht im Weg: ein Fast-Forward fasst
# sie nicht an, und kollidiert eine eingehende Datei mit einer davon, bricht git
# von sich aus ab.
DIRTY_TRACKED="$(git status --porcelain --untracked-files=no)"
if [[ -n "$DIRTY_TRACKED" ]]; then
  if [[ "$ALLOW_DIRTY" -eq 1 ]]; then
    warn "Aenderungen an versionierten Dateien - werden durch --allow-dirty verworfen:"
    printf '%s\n' "$DIRTY_TRACKED" | sed 's/^/    /'
  else
    printf '\n\033[31mFEHLER: Aenderungen an versionierten Dateien:\033[0m\n' >&2
    printf '%s\n' "$DIRTY_TRACKED" | sed 's/^/    /' >&2
    cat >&2 <<'HINT'

    Auf einem Produktivserver sollte der Arbeitsbaum sauber sein.
    Moeglichkeiten:
      - Aenderungen sichern und verwerfen:  git stash
      - Aenderungen uebernehmen:            git add -A && git commit && git push
      - bewusst verwerfen:                  bash deploy/update.sh --allow-dirty
HINT
    exit 1
  fi
fi

UNTRACKED_LIST="$(git ls-files --others --exclude-standard)"
if [[ -n "$UNTRACKED_LIST" ]]; then
  UNTRACKED_COUNT="$(printf '%s\n' "$UNTRACKED_LIST" | wc -l)"
  info "$UNTRACKED_COUNT unversionierte Datei(en) im Projektordner - bleiben unangetastet."
fi

ok "Arbeitskopie: $APP_DIR (Besitzer $OWNER)"

# --- Schritt 2: Remote abfragen -------------------------------------------
log "Repository abfragen"
git fetch --prune origin || fail "git fetch fehlgeschlagen. Netzwerk/Repository pruefen."

PREV_SHA="$(git rev-parse HEAD)"
if [[ -n "$TARGET_REF" ]]; then
  TARGET_SHA="$(git rev-parse --verify "$TARGET_REF^{commit}" 2>/dev/null)" \
    || fail "Ref '$TARGET_REF' nicht gefunden."
else
  TARGET_SHA="$(git rev-parse --verify "origin/$BRANCH^{commit}" 2>/dev/null)" \
    || fail "Branch 'origin/$BRANCH' nicht gefunden."
fi

info "Aktuell:  $(git rev-parse --short "$PREV_SHA")  $(git log -1 --pretty=%s "$PREV_SHA")"
info "Ziel:     $(git rev-parse --short "$TARGET_SHA")  $(git log -1 --pretty=%s "$TARGET_SHA")"

ALREADY_CURRENT=0
if [[ "$PREV_SHA" == "$TARGET_SHA" ]]; then
  if [[ "$FORCE" -eq 0 ]]; then
    ok "Die Installation ist bereits aktuell. Nichts zu tun."
    info "Falls Migration, Tests und Neustart trotzdem laufen sollen: --force"
    exit 0
  fi
  ALREADY_CURRENT=1
  info "Codestand ist bereits aktuell - laufe wegen --force trotzdem durch."
fi

if [[ "$ALREADY_CURRENT" -eq 0 ]] && ! git merge-base --is-ancestor "$PREV_SHA" "$TARGET_SHA"; then
  fail "Ziel ist kein direkter Nachfolger des aktuellen Stands (kein Fast-Forward moeglich).
       Vermutlich wurde die Historie umgeschrieben oder es gibt lokale Commits.
       Bitte manuell pruefen: git log --oneline --graph --all"
fi

REQS_CHANGED=0
if [[ "$ALREADY_CURRENT" -eq 1 ]]; then
  REQS_CHANGED=1
  info "Abhaengigkeiten werden wegen --force sicherheitshalber geprueft."
else
  log "Eingehende Aenderungen"
  git log --oneline --no-merges "$PREV_SHA..$TARGET_SHA" | sed 's/^/    /'
  printf '\n'
  git diff --stat "$PREV_SHA" "$TARGET_SHA" | tail -n 20 | sed 's/^/    /'

  if [[ -n "$(git diff --name-only "$PREV_SHA" "$TARGET_SHA" -- requirements.txt)" ]]; then
    REQS_CHANGED=1
    warn "requirements.txt hat sich geaendert - Abhaengigkeiten werden nachinstalliert."
  fi
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  printf '\n'
  ok "Dry-Run: es wurde nichts veraendert."
  exit 0
fi

if [[ "$ASSUME_YES" -eq 0 ]]; then
  printf '\n'
  read -r -p "    Update jetzt einspielen? [j/N] " answer
  case "$answer" in
    j|J|y|Y|ja|Ja) ;;
    *) info "Abgebrochen."; exit 0 ;;
  esac
fi

# --- Schritt 3: Backup ----------------------------------------------------
if [[ "$SKIP_BACKUP" -eq 1 ]]; then
  warn "Backup uebersprungen (--skip-backup)."
else
  log "Backup von Datenbank und Uploads"
  load_env >/dev/null
  if [[ -n "$ENV_SOURCES" ]]; then
    info "Konfiguration aus: $ENV_SOURCES"
    info "Datenbank: $(masked_db_url)"
  else
    warn "Weder $APP_DIR/.env.local noch $ENV_FILE lesbar."
    warn "Backup und Migration wuerden auf die Standard-SQLite-Datei zugreifen."
    warn "Bei einer PostgreSQL-Instanz waere das die falsche Datenbank."
    if [[ "$ASSUME_YES" -eq 0 ]]; then
      read -r -p "    Trotzdem fortfahren? [j/N] " env_answer
      case "$env_answer" in
        j|J|y|Y|ja|Ja) ;;
        *) fail "Abgebrochen. Bitte ENV_FILE setzen oder .env.local bereitstellen." ;;
      esac
    fi
  fi
  bash "$APP_DIR/backup_external.sh" || fail "Backup fehlgeschlagen. Update abgebrochen."
  ok "Backup abgeschlossen."
fi

# --- Rollback-Vorbereitung ------------------------------------------------
ROLLBACK_ARMED=0

restart_service() {
  if ! command -v systemctl >/dev/null 2>&1; then
    warn "systemctl nicht verfuegbar - Dienst bitte manuell neu starten."
    return 0
  fi
  if ! systemctl list-unit-files "$SERVICE_NAME.service" --no-legend 2>/dev/null | grep -q .; then
    warn "Unit $SERVICE_NAME.service nicht gefunden - Dienst bitte manuell neu starten."
    return 0
  fi
  if [[ "$(id -u)" -eq 0 ]]; then
    systemctl restart "$SERVICE_NAME.service"
  else
    sudo -n systemctl restart "$SERVICE_NAME.service" 2>/dev/null \
      || sudo systemctl restart "$SERVICE_NAME.service"
  fi
}

rollback() {
  [[ "$ROLLBACK_ARMED" -eq 1 ]] || return 0
  ROLLBACK_ARMED=0
  printf '\n\033[33m==> Rollback auf %s\033[0m\n' "$(git rev-parse --short "$PREV_SHA")"
  load_env >/dev/null
  git reset --hard "$PREV_SHA" >/dev/null 2>&1 || warn "git reset fehlgeschlagen."
  if [[ "$REQS_CHANGED" -eq 1 ]]; then
    "$PIP" install -q -r "$APP_DIR/requirements.txt" || warn "pip-Rollback fehlgeschlagen."
  fi
  "$PYTHON" "$APP_DIR/update_db.py" >/dev/null 2>&1 || warn "update_db.py beim Rollback fehlgeschlagen."
  restart_service || warn "Dienst-Neustart beim Rollback fehlgeschlagen."
  cat >&2 <<ROLLBACKMSG

    Der Codestand wurde zurueckgerollt.
    Hinweis: Schema-Aenderungen aus update_db.py werden dabei NICHT rueckgaengig
    gemacht (sie sind additiv und mit dem alten Code vertraeglich). Falls die
    Datenbank dennoch zurueckgesetzt werden muss, liegt das Backup unter
    ${BACKUP_ROOT:-$APP_DIR/backups}.

ROLLBACKMSG
}

trap 'rc=$?; if [[ $rc -ne 0 ]]; then rollback; fi' EXIT

# --- Schritt 4: Code aktualisieren ----------------------------------------
log "Codestand aktualisieren"
ROLLBACK_ARMED=1
if [[ "$ALLOW_DIRTY" -eq 1 ]]; then
  git checkout -f -- . 
fi
if [[ "$ALREADY_CURRENT" -eq 0 ]]; then
  git merge --ff-only "$TARGET_SHA" >/dev/null
fi
ok "Jetzt auf $(git rev-parse --short HEAD): $(git log -1 --pretty=%s)"

# --- Schritt 5: Abhaengigkeiten -------------------------------------------
if [[ "$REQS_CHANGED" -eq 1 ]]; then
  log "Python-Abhaengigkeiten installieren"
  "$PIP" install -r "$APP_DIR/requirements.txt"
  ok "Abhaengigkeiten aktuell."
else
  info "requirements.txt unveraendert - pip-Schritt uebersprungen."
fi

# --- Schritt 6: Schema-Migration ------------------------------------------
log "Datenbankschema nachziehen (update_db.py)"
load_env >/dev/null
info "Datenbank: $(masked_db_url)"
"$PYTHON" "$APP_DIR/update_db.py"
ok "Schema aktuell."

# --- Schritt 7: Tests -----------------------------------------------------
if [[ "$SKIP_TESTS" -eq 1 ]]; then
  warn "Regressionstests uebersprungen (--skip-tests)."
else
  log "Regressionstests (isolierte Test-Datenbank)"
  "$PYTHON" -m unittest discover -s "$APP_DIR/tests" -t "$APP_DIR" -p 'test_*.py'
  ok "Tests bestanden."
fi

# --- Schritt 8: Dienst neu starten + Health-Check -------------------------
if [[ "$NO_RESTART" -eq 1 ]]; then
  warn "Neustart uebersprungen (--no-restart). Aenderungen sind noch nicht aktiv."
else
  log "Dienst neu starten"
  restart_service
  ok "$SERVICE_NAME neu gestartet."

  log "Health-Check ($HEALTH_URL)"
  if ! command -v curl >/dev/null 2>&1; then
    warn "curl nicht installiert - Health-Check uebersprungen."
  else
    healthy=0
    for attempt in $(seq 1 "$HEALTH_RETRIES"); do
      code="$(curl -fsS -o /dev/null -w '%{http_code}' --max-time 10 "$HEALTH_URL" 2>/dev/null || true)"
      if [[ "$code" == "200" ]]; then
        healthy=1
        ok "HTTP 200 nach $attempt Versuch(en)."
        break
      fi
      info "Versuch $attempt/$HEALTH_RETRIES: HTTP ${code:-keine Antwort} - warte ${HEALTH_DELAY}s ..."
      sleep "$HEALTH_DELAY"
    done
    if [[ "$healthy" -ne 1 ]]; then
      printf '\n\033[31m    Health-Check fehlgeschlagen.\033[0m\n' >&2
      journalctl -u "$SERVICE_NAME" -n 30 --no-pager 2>/dev/null | sed 's/^/    /' >&2 || true
      exit 1
    fi
  fi
fi

ROLLBACK_ARMED=0
trap - EXIT

log "Update abgeschlossen"
info "Version:  $(git rev-parse --short HEAD)  $(git log -1 --pretty=%s)"
info "Status:   systemctl status $SERVICE_NAME"
info "Logs:     journalctl -u $SERVICE_NAME -n 100"
printf '\n'
