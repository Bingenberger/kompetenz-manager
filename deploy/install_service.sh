#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="${APP_NAME:-kompetenzkompass}"
SERVICE_NAME="${SERVICE_NAME:-$APP_NAME}"
LOG_DIR="${LOG_DIR:-/var/log/$APP_NAME}"
ENV_DIR="${ENV_DIR:-/etc/$APP_NAME}"
ENV_FILE="${ENV_FILE:-$ENV_DIR/$APP_NAME.env}"
CRON_FILE="${CRON_FILE:-/etc/cron.d/$APP_NAME-backup}"
LOGROTATE_FILE="${LOGROTATE_FILE:-/etc/logrotate.d/$APP_NAME}"
WRAPPER_PATH="${WRAPPER_PATH:-/usr/local/bin/$APP_NAME-backup}"
BIND_HOST="${BIND_HOST:-127.0.0.1}"
BIND_PORT="${BIND_PORT:-8008}"
WORKERS="${WORKERS:-3}"
BACKUP_HOUR="${BACKUP_HOUR:-2}"
BACKUP_MINUTE="${BACKUP_MINUTE:-15}"
KEEP_DAYS="${KEEP_DAYS:-14}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Bitte als root ausführen." >&2
  exit 1
fi

detect_owner() {
  local owner
  owner="$(stat -c '%U' "$APP_DIR")"
  if [[ "$owner" == "root" || -z "$owner" ]]; then
    printf '%s\n' "$APP_NAME"
  else
    printf '%s\n' "$owner"
  fi
}

detect_group() {
  local group
  group="$(stat -c '%G' "$APP_DIR")"
  if [[ "$group" == "root" || -z "$group" ]]; then
    printf '%s\n' "$APP_NAME"
  else
    printf '%s\n' "$group"
  fi
}

APP_USER="${APP_USER:-$(detect_owner)}"
APP_GROUP="${APP_GROUP:-$(detect_group)}"

if ! getent group "$APP_GROUP" >/dev/null; then
  groupadd --system "$APP_GROUP"
fi

if ! id "$APP_USER" >/dev/null 2>&1; then
  useradd --system --gid "$APP_GROUP" --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
fi

mkdir -p "$LOG_DIR" "$ENV_DIR" "$APP_DIR/backups" "$APP_DIR/instance" "$APP_DIR/static/uploads"
chown -R "$APP_USER:$APP_GROUP" "$LOG_DIR" "$APP_DIR/backups" "$APP_DIR/instance" "$APP_DIR/static/uploads"

if [[ ! -x "$APP_DIR/venv/bin/python" ]]; then
  echo "Kein venv unter $APP_DIR/venv gefunden. Bitte zuerst die virtuelle Umgebung anlegen." >&2
  exit 1
fi

"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

if [[ ! -f "$ENV_FILE" ]]; then
  cat > "$ENV_FILE" <<EOF
SECRET_KEY=bitte-aendern
DATABASE_URL=
SESSION_COOKIE_NAME=${APP_NAME}_session
REMEMBER_COOKIE_NAME=${APP_NAME}_remember
FLASK_DEBUG=0
SESSION_COOKIE_SECURE=1
REMEMBER_COOKIE_SECURE=1
SESSION_COOKIE_SAMESITE=Lax
REMEMBER_COOKIE_SAMESITE=Lax
PREFERRED_URL_SCHEME=https
TRUST_REVERSE_PROXY=1
PROXY_FIX_X_FOR=1
PROXY_FIX_X_PROTO=1
PROXY_FIX_X_HOST=1
BACKUP_ROOT=$APP_DIR/backups
KEEP_DAYS=$KEEP_DAYS
HOST_TAG=\$(hostname -s)
EOF
fi

chown root:"$APP_GROUP" "$ENV_FILE"
chmod 640 "$ENV_FILE"

cat > "/etc/systemd/system/$SERVICE_NAME.service" <<EOF
[Unit]
Description=KompetenzKompass Gunicorn Service
After=network.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_GROUP
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
RuntimeDirectory=$APP_NAME
UMask=0027
NoNewPrivileges=true
PrivateTmp=true
ProtectControlGroups=true
ProtectKernelModules=true
ProtectKernelTunables=true
RestrictSUIDSGID=true
LockPersonality=true
ReadWritePaths=$APP_DIR $LOG_DIR /run/$APP_NAME
ExecStartPre=$APP_DIR/venv/bin/python $APP_DIR/update_db.py
ExecStart=$APP_DIR/venv/bin/gunicorn --workers $WORKERS --bind $BIND_HOST:$BIND_PORT --access-logfile $LOG_DIR/gunicorn-access.log --error-logfile $LOG_DIR/gunicorn-error.log --capture-output app:app
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=$APP_NAME

[Install]
WantedBy=multi-user.target
EOF

cat > "$WRAPPER_PATH" <<EOF
#!/usr/bin/env bash
set -euo pipefail

cd "$APP_DIR"
set -a
source "$ENV_FILE"
set +a

exec "$APP_DIR/backup_external.sh" >> "$LOG_DIR/backup.log" 2>&1
EOF

chmod 755 "$WRAPPER_PATH"

cat > "$CRON_FILE" <<EOF
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
$BACKUP_MINUTE $BACKUP_HOUR * * * $APP_USER $WRAPPER_PATH
EOF

chmod 644 "$CRON_FILE"

cat > "$LOGROTATE_FILE" <<EOF
$LOG_DIR/*.log {
    daily
    rotate 14
    compress
    missingok
    notifempty
    copytruncate
    create 0640 $APP_USER $APP_GROUP
}
EOF

systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME.service"

echo
echo "Installation abgeschlossen."
echo "Service:        $SERVICE_NAME.service"
echo "App-URL intern: http://$BIND_HOST:$BIND_PORT"
echo "Env-Datei:      $ENV_FILE"
echo "Logs:           $LOG_DIR"
echo "Backup-Cron:    $CRON_FILE"
echo
echo "Wichtige nächste Schritte:"
echo "1. SECRET_KEY in $ENV_FILE setzen"
echo "2. Optional DATABASE_URL in $ENV_FILE setzen"
echo "3. Service prüfen: systemctl status $SERVICE_NAME"
echo "4. Logs prüfen: journalctl -u $SERVICE_NAME -n 100"
