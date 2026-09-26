#!/usr/bin/env bash
# Startet den KompetenzKompass lokal mit Demodaten - getrennt von der echten
# Datenbank, eigene Cookies, eigener Port. Für Schulungen und Videos.
#
#   bash demo/start_demo.sh          # startet (baut die Datenbank beim ersten Mal)
#   bash demo/start_demo.sh --neu    # Demodaten neu aufbauen und starten
set -euo pipefail

cd "$(dirname "$0")/.."
PROJEKT="$PWD"

if [[ -x "venv/bin/python" ]]; then
  PYTHON="$PROJEKT/venv/bin/python"
else
  PYTHON="$(command -v python3)"
fi

DEMO_DB="${DEMO_DB:-$PROJEKT/instance/demo.db}"
NEU=0
[[ "${1:-}" == "--neu" ]] && NEU=1

if [[ $NEU -eq 1 || ! -f "$DEMO_DB" ]]; then
  echo "Baue Demodaten in $DEMO_DB ..."
  "$PYTHON" demo/demo_daten.py --db "$DEMO_DB" --neu
fi

# Eigene Datenbank, eigene Sitzung - die Produktivinstanz bleibt unberührt.
export DATABASE_URL="sqlite:///$DEMO_DB"
export SECRET_KEY="${SECRET_KEY:-demo-schluessel-nur-fuer-vorfuehrungen}"
export SESSION_COOKIE_NAME="kompetenz_demo_session"
export REMEMBER_COOKIE_NAME="kompetenz_demo_remember"
# Lokal über http: die Cookies dürfen nicht auf https bestehen.
export SESSION_COOKIE_SECURE=0
export REMEMBER_COOKIE_SECURE=0
export PREFERRED_URL_SCHEME=http
export PROTECTED_UPLOAD_FOLDER="${PROTECTED_UPLOAD_FOLDER:-$PROJEKT/instance/demo_protected_uploads}"
export FLASK_HOST="${FLASK_HOST:-127.0.0.1}"
export PORT="${PORT:-5055}"
# Kein Mailversand aus der Demo.
unset MAIL_SERVER MAIL_USERNAME MAIL_PASSWORD MAIL_FROM 2>/dev/null || true

echo
echo "KompetenzKompass (Demo) läuft gleich auf http://$FLASK_HOST:$PORT"
echo "Anmeldung: sommer (Lehrkraft) · wagner (Schulleitung) · klein (Förderpädagogik) · admin"
echo "Passwort für alle Konten: demo"
echo
exec "$PYTHON" app.py
