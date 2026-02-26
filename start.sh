#!/usr/bin/env bash
set -euo pipefail

# Optional: lokale Overrides aus Datei laden (nicht versioniert)
if [[ -f ".env.local" ]]; then
  # shellcheck disable=SC1091
  source ".env.local"
fi

export FLASK_HOST="${FLASK_HOST:-127.0.0.1}"
export PORT="${PORT:-5001}"

# WICHTIG: pro Instanz eigene Cookie-Namen, damit es keine Logout-Kollisionen gibt.
export SESSION_COOKIE_NAME="${SESSION_COOKIE_NAME:-kompetenz_manager_session}"
export REMEMBER_COOKIE_NAME="${REMEMBER_COOKIE_NAME:-kompetenz_manager_remember}"

# WICHTIG: für stabile Sessions einen festen SECRET_KEY setzen (z. B. in .env.local).
if [[ -z "${SECRET_KEY:-}" ]]; then
  echo "Hinweis: SECRET_KEY ist nicht gesetzt. Sessions werden nach Neustart ungültig."
fi

exec python3 app.py
