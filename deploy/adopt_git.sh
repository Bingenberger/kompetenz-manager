#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# adopt_git.sh
#
# Ueberfuehrt ein manuell kopiertes Installationsverzeichnis einmalig in eine
# Git-Arbeitskopie, damit kuenftige Updates ueber deploy/update.sh laufen.
#
# Sicherheitsprinzip: Der Arbeitsbaum wird NICHT veraendert. Es wird nur ein
# .git-Verzeichnis angelegt und HEAD auf origin/<branch> gesetzt. Anschliessend
# zeigt "git status" exakt, wo die Installation vom Repository abweicht.
# Datenbank, Uploads, venv und .env.local sind per .gitignore ausgenommen und
# werden zu keinem Zeitpunkt angefasst.
# ---------------------------------------------------------------------------

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_URL="${REPO_URL:-https://github.com/Bingenberger/kompetenz-manager.git}"
BRANCH="${BRANCH:-main}"
BACKUP_ROOT="${BACKUP_ROOT:-}"
SKIP_SNAPSHOT=0

usage() {
  cat <<'USAGE'
Verwendung: bash deploy/adopt_git.sh [Optionen]

Optionen:
  --app-dir <pfad>   Installationsverzeichnis (Standard: Elternordner dieses Skripts).
                     Noetig, wenn das Skript ausserhalb der Installation liegt,
                     z. B. nach dem Download nach /tmp.
  --repo <url>       Repository-URL (Standard: oeffentliches GitHub-Repo)
  --branch <name>    Branch (Standard: main)
  --skip-snapshot    Kein Voll-Snapshot des Verzeichnisses anlegen (nicht empfohlen)
  -h, --help         Diese Hilfe

Das Skript ist einmalig auszufuehren, als Besitzer des App-Verzeichnisses.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --app-dir) APP_DIR="$(cd "${2:?--app-dir benoetigt einen Pfad}" && pwd)"; shift 2 ;;
    --repo) REPO_URL="${2:?--repo benoetigt eine URL}"; shift 2 ;;
    --branch) BRANCH="${2:?--branch benoetigt einen Namen}"; shift 2 ;;
    --skip-snapshot) SKIP_SNAPSHOT=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unbekannte Option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

BACKUP_ROOT="${BACKUP_ROOT:-$APP_DIR/backups}"

log()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
fail() { printf '\n\033[31mFEHLER: %s\033[0m\n' "$*" >&2; exit 1; }

command -v git >/dev/null 2>&1 || fail "git ist nicht installiert (apt install git)."

OWNER="$(stat -c '%U' "$APP_DIR")"
if [[ "$(id -un)" != "$OWNER" ]]; then
  fail "Bitte als Besitzer des App-Verzeichnisses ausfuehren ($OWNER), nicht als $(id -un).
       Beispiel: sudo -u $OWNER bash $APP_DIR/deploy/adopt_git.sh"
fi

if [[ -e "$APP_DIR/.git" ]]; then
  fail "In $APP_DIR existiert bereits ein .git-Verzeichnis.
       Dieses Skript ist nur fuer die einmalige Erstumstellung gedacht.
       Fuer Updates: bash deploy/update.sh"
fi

log "Ausgangslage"
info "App-Verzeichnis: $APP_DIR"
info "Repository:      $REPO_URL"
info "Branch:          $BRANCH"
info "Benutzer:        $(id -un)"

# --- 1. Voll-Snapshot als Sicherheitsnetz ----------------------------------
if [[ "$SKIP_SNAPSHOT" -eq 0 ]]; then
  log "Snapshot des kompletten Verzeichnisses anlegen"
  mkdir -p "$BACKUP_ROOT"
  SNAPSHOT="$BACKUP_ROOT/pre-git-adoption_$(date +%Y-%m-%d_%H-%M-%S).tar.gz"
  tar -czf "$SNAPSHOT" \
    -C "$(dirname "$APP_DIR")" \
    --exclude="$(basename "$APP_DIR")/venv" \
    --exclude="$(basename "$APP_DIR")/.venv" \
    --exclude="$(basename "$APP_DIR")/backups" \
    --exclude="$(basename "$APP_DIR")/__pycache__" \
    --exclude="*/__pycache__" \
    "$(basename "$APP_DIR")"
  info "Snapshot: $SNAPSHOT ($(du -h "$SNAPSHOT" | cut -f1))"
  info "Enthaelt Code, Datenbank, Uploads und Konfiguration (ohne venv/backups)."
else
  info "Snapshot uebersprungen (--skip-snapshot)."
fi

# --- 2. Git-Repo anlegen, ohne den Arbeitsbaum zu beruehren ----------------
log "Git-Arbeitskopie einrichten"
git -C "$APP_DIR" init -q
git -C "$APP_DIR" symbolic-ref HEAD "refs/heads/$BRANCH"
git -C "$APP_DIR" remote add origin "$REPO_URL"

info "Hole $BRANCH von origin ..."
git -C "$APP_DIR" fetch -q origin "$BRANCH"

git -C "$APP_DIR" update-ref "refs/heads/$BRANCH" FETCH_HEAD
git -C "$APP_DIR" branch -q --set-upstream-to "origin/$BRANCH" "$BRANCH"

# Index aus HEAD laden, Arbeitsbaum bleibt unveraendert.
git -C "$APP_DIR" reset -q

HEAD_SHA="$(git -C "$APP_DIR" rev-parse --short HEAD)"
HEAD_SUBJ="$(git -C "$APP_DIR" log -1 --pretty=%s)"
info "HEAD steht jetzt auf $HEAD_SHA ($HEAD_SUBJ)"

# --- 3. Abweichungen berichten --------------------------------------------
log "Abgleich Installation <-> Repository"
DIFF_OUT="$(git -C "$APP_DIR" status --porcelain)"

if [[ -z "$DIFF_OUT" ]]; then
  printf '\033[32m    Keine Abweichungen. Die Installation entspricht exakt %s.\033[0m\n' "$BRANCH"
  printf '\n    Umstellung abgeschlossen. Kuenftige Updates:\n\n'
  printf '        bash %s/deploy/update.sh\n\n' "$APP_DIR"
  exit 0
fi

# Versionierte Konfigurations-/Geheimnisdateien gesondert hervorheben: ein
# spaeteres "git checkout -f" wuerde sie mit dem Repository-Stand ueberschreiben.
CONFIG_HITS="$(git -C "$APP_DIR" diff --name-only \
  | grep -Ei '(^|/)\.env|\.key$|\.pem$|secrets?\.' || true)"
if [[ -n "$CONFIG_HITS" ]]; then
  printf '\n\033[31m    ACHTUNG: versionierte Konfigurationsdatei(en) weichen ab:\033[0m\n'
  printf '%s\n' "$CONFIG_HITS" | sed 's/^/        /'
  printf '\033[31m    Ein "git checkout -f" wuerde diese mit dem Repository-Stand\n'
  printf '    ueberschreiben (Datenbank, Port, SECRET_KEY). Vorher sichern:\033[0m\n'
  while IFS= read -r f; do
    [[ -n "$f" ]] && printf '        cp -a "%s" ~/"%s.backup"\n' "$f" "$(basename "$f")"
  done <<< "$CONFIG_HITS"
  printf '\n    Nach dem Checkout zurueckspielen und Rechte setzen (chmod 600).\n\n'
fi

CHANGED="$(git -C "$APP_DIR" diff --name-only | wc -l)"
UNTRACKED="$(git -C "$APP_DIR" ls-files --others --exclude-standard | wc -l)"

printf '\033[33m    %s versionierte Datei(en) weichen ab, %s unversionierte Datei(en) zusaetzlich.\033[0m\n\n' \
  "$CHANGED" "$UNTRACKED"
git -C "$APP_DIR" status --short | sed 's/^/    /'

cat <<NEXT

    Das ist normal, wenn auf dem Server Hotfixes direkt bearbeitet wurden
    oder die Installation aelter/neuer als der Branch ist.

    Naechster Schritt - eine der beiden Varianten:

    A) Serverstand verwerfen, Repository-Stand uebernehmen (Regelfall):

           cd $APP_DIR
           git diff                 # vorher pruefen, was verloren geht
           git checkout -f -- .     # versionierte Dateien zuruecksetzen
           bash deploy/update.sh    # Migration, Tests, Neustart

    B) Serverstand behalten und ins Repository ueberfuehren:

           cd $APP_DIR
           git add -A && git commit -m "Serverstand uebernommen"
           git push origin $BRANCH

    Datenbank, Uploads, venv und .env.local sind in beiden Faellen nicht betroffen.
    Vollstaendiger Rueckweg: der Snapshot unter $BACKUP_ROOT.

NEXT
