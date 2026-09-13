#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# install_benachrichtigungen.sh
#
# Richtet den Versand der Benachrichtigungs-E-Mails per Cron ein (einmalig,
# als root). Legt an:
#   - /usr/local/bin/<app>-benachrichtigungen   (Wrapper, laedt die Env-Datei)
#   - /etc/cron.d/<app>-benachrichtigungen      (alle 5 Minuten + taeglich)
#
# Die Zugangsdaten zum Mailserver gehoeren in die Env-Datei des Dienstes
# (MAIL_SERVER, MAIL_PORT, MAIL_SECURITY, MAIL_USERNAME, MAIL_PASSWORD,
# MAIL_FROM, APP_BASE_URL) - siehe deploy/README_DEPLOY.md.
# Mehrfaches Ausfuehren ist unschaedlich.
# ---------------------------------------------------------------------------

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="${APP_NAME:-kompetenzkompass}"
LOG_DIR="${LOG_DIR:-/var/log/$APP_NAME}"
ENV_FILE="${ENV_FILE:-/etc/$APP_NAME/$APP_NAME.env}"
CRON_FILE="${CRON_FILE:-/etc/cron.d/$APP_NAME-benachrichtigungen}"
WRAPPER_PATH="${WRAPPER_PATH:-/usr/local/bin/$APP_NAME-benachrichtigungen}"
# Sammelmail: Uhrzeit und Wochentage im Cron-Format (Standard Mo-Fr 15:00).
DAILY_HOUR="${DAILY_HOUR:-15}"
DAILY_MINUTE="${DAILY_MINUTE:-0}"
DAILY_WEEKDAYS="${DAILY_WEEKDAYS:-1-5}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Bitte als root ausführen." >&2
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Env-Datei $ENV_FILE nicht gefunden (ENV_FILE setzen)." >&2
  exit 1
fi

# Der Dienstbenutzer aus der systemd-Unit, sonst der Besitzer von instance/.
APP_USER="${APP_USER:-$(systemctl show -p User --value "$APP_NAME.service" 2>/dev/null || true)}"
if [[ -z "$APP_USER" ]]; then
  APP_USER="$(stat -c '%U' "$APP_DIR/instance")"
fi

mkdir -p "$LOG_DIR"
touch "$LOG_DIR/benachrichtigungen.log"
chown "$APP_USER" "$LOG_DIR/benachrichtigungen.log"

cat > "$WRAPPER_PATH" <<WRAPPER
#!/usr/bin/env bash
set -euo pipefail

cd "$APP_DIR"
set -a
source "$ENV_FILE"
set +a

# Ein Lauf zur Zeit: haengt der Mailserver, soll der naechste Lauf nicht
# dieselben Benachrichtigungen ein zweites Mal verschicken.
exec flock -n "$APP_DIR/instance/.benachrichtigungen.lock" \\
  "$APP_DIR/venv/bin/python" "$APP_DIR/benachrichtigungen_senden.py" "\$@" >> "$LOG_DIR/benachrichtigungen.log" 2>&1
WRAPPER
chmod 755 "$WRAPPER_PATH"

cat > "$CRON_FILE" <<CRON
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
*/5 * * * * $APP_USER $WRAPPER_PATH sofort
$DAILY_MINUTE $DAILY_HOUR * * $DAILY_WEEKDAYS $APP_USER $WRAPPER_PATH taeglich
CRON
chmod 644 "$CRON_FILE"

echo "Benachrichtigungsversand eingerichtet."
echo "Cron:     $CRON_FILE (Dienstbenutzer: $APP_USER)"
echo "Log:      $LOG_DIR/benachrichtigungen.log"
if ! grep -q '^MAIL_SERVER=.\+' "$ENV_FILE"; then
  echo
  echo "Hinweis: In $ENV_FILE ist noch kein MAIL_SERVER eingetragen."
  echo "Bis dahin erscheinen Benachrichtigungen nur unter der Glocke."
fi
echo
echo "Probelauf:  sudo -u $APP_USER $WRAPPER_PATH sofort && tail -n 3 $LOG_DIR/benachrichtigungen.log"
