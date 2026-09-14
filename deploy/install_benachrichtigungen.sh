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
# Sammelmail: Uhrzeit und Wochentage (Standard Mo-Fr 15:00 deutscher Zeit).
# Die Uhrzeit gilt in DAILY_TZ, nicht in der Zeitzone des Servers - viele
# Server laufen auf UTC, und die Sommerzeit wuerde die Mail sonst verschieben.
DAILY_HOUR="${DAILY_HOUR:-15}"
DAILY_MINUTE="${DAILY_MINUTE:-0}"
DAILY_WEEKDAYS="${DAILY_WEEKDAYS:-1-5}"
DAILY_TZ="${DAILY_TZ:-Europe/Berlin}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Bitte als root ausführen." >&2
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Env-Datei $ENV_FILE nicht gefunden (ENV_FILE setzen)." >&2
  exit 1
fi

if ! TZ="$DAILY_TZ" date >/dev/null 2>&1 || [[ ! -e "/usr/share/zoneinfo/$DAILY_TZ" ]]; then
  echo "Zeitzone $DAILY_TZ unbekannt (DAILY_TZ setzen, z. B. Europe/Berlin)." >&2
  exit 1
fi
if ! [[ "$DAILY_HOUR" =~ ^[0-9]+$ && "$DAILY_HOUR" -le 23 && "$DAILY_MINUTE" =~ ^[0-9]+$ && "$DAILY_MINUTE" -le 59 ]]; then
  echo "DAILY_HOUR (0-23) und DAILY_MINUTE (0-59) als Zahlen angeben." >&2
  exit 1
fi
DAILY_HOUR=$((10#$DAILY_HOUR))
DAILY_MINUTE=$((10#$DAILY_MINUTE))

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

# Alles ins Protokoll - auch Fehler, bevor Python ueberhaupt startet.
exec >> "$LOG_DIR/benachrichtigungen.log" 2>&1

takt="\${1:-}"
jetzt() { date '+%Y-%m-%d %H:%M'; }

# Cron ruft "taeglich-geplant" stuendlich auf; gearbeitet wird nur in der
# eingestellten Stunde deutscher Zeit (Sommer- wie Winterzeit). Von Hand
# aufgerufen laeuft "taeglich" sofort.
if [[ "\$takt" == taeglich-geplant ]]; then
  if [[ "\$(TZ=$DAILY_TZ date +%-H)" != "$DAILY_HOUR" ]]; then
    exit 0
  fi
  takt=taeglich
  set -- taeglich
fi

cd "$APP_DIR"

# Env-Datei so lesen, wie systemd sie liest: KEY=VALUE je Zeile, keine
# Shell-Auswertung. Ein Passwort mit \$, Leerzeichen oder # bleibt so, wie es
# dasteht, und Dienst und Versandlauf sehen dieselben Werte.
while IFS= read -r zeile || [[ -n "\$zeile" ]]; do
  zeile="\${zeile%\$'\\r'}"
  [[ "\$zeile" =~ ^[[:space:]]*(#|\$) ]] && continue
  [[ "\$zeile" =~ ^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)\$ ]] || continue
  name="\${BASH_REMATCH[2]}"
  wert="\${BASH_REMATCH[3]}"
  if [[ "\$wert" =~ ^\"(.*)\"[[:space:]]*\$ || "\$wert" =~ ^\'(.*)\'[[:space:]]*\$ ]]; then
    wert="\${BASH_REMATCH[1]}"
  fi
  export "\$name=\$wert"
done < "$ENV_FILE"

# Ein Lauf zur Zeit, damit nichts doppelt verschickt wird. Der Fuenf-Minuten-
# Lauf gibt auf, wenn gerade einer arbeitet (sonst stapeln sich Laeufe bei
# einem haengenden Mailserver). Der taegliche Lauf wartet - er startet zur
# selben Minute wie ein Fuenf-Minuten-Lauf und fiele sonst regelmaessig aus.
if [[ "\$takt" == taeglich ]]; then
  sperre=(-w 1800)
else
  sperre=(-n)
fi
status=0
flock "\${sperre[@]}" "$APP_DIR/instance/.benachrichtigungen.lock" \\
  "$APP_DIR/venv/bin/python" "$APP_DIR/benachrichtigungen_senden.py" "\$@" || status=\$?
if [[ "\$status" -ne 0 && "\$takt" == taeglich ]]; then
  echo "\$(jetzt) taeglich: Lauf fehlgeschlagen oder Sperre nicht frei (Status \$status)"
fi
exit "\$status"
WRAPPER
chmod 755 "$WRAPPER_PATH"

cat > "$CRON_FILE" <<CRON
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
*/5 * * * * $APP_USER $WRAPPER_PATH sofort
# Stuendlich - der Wrapper arbeitet nur um $DAILY_HOUR Uhr ($DAILY_TZ).
$DAILY_MINUTE * * * $DAILY_WEEKDAYS $APP_USER $WRAPPER_PATH taeglich-geplant
CRON
chmod 644 "$CRON_FILE"

echo "Benachrichtigungsversand eingerichtet."
echo "Cron:     $CRON_FILE (Dienstbenutzer: $APP_USER)"
echo "Täglich:  $(printf '%02d:%02d' "$DAILY_HOUR" "$DAILY_MINUTE") Uhr ($DAILY_TZ), Wochentage $DAILY_WEEKDAYS"
echo "Log:      $LOG_DIR/benachrichtigungen.log"
if ! grep -q '^MAIL_SERVER=.\+' "$ENV_FILE"; then
  echo
  echo "Hinweis: In $ENV_FILE ist noch kein MAIL_SERVER eingetragen."
  echo "Bis dahin erscheinen Benachrichtigungen nur unter der Glocke."
fi
echo
echo "Probelauf:  sudo -u $APP_USER $WRAPPER_PATH sofort && tail -n 3 $LOG_DIR/benachrichtigungen.log"
echo "Sammelmail jetzt von Hand:  sudo -u $APP_USER $WRAPPER_PATH taeglich"
