#!/usr/bin/env bash
# Prepares the calendar to be published through a tunnel or reverse proxy.
# Run on the machine running walldisplay.
#
# The panel's own browser skips the access code because it connects from
# 127.0.0.1. A tunnel connector connects from 127.0.0.1 too -- so publishing
# without this leaves every request from the internet looking local. This
# script closes that, and fixes up the kiosk so the panel still works after.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="$DIR/config.yaml"
KIOSK_UNIT="/etc/systemd/system/walldisplay-kiosk.service"

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!! \033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mxx \033[0m %s\n' "$*" >&2; exit 1; }

[[ -f "$CONFIG" ]] || die "No config.yaml at $CONFIG"
[[ $EUID -eq 0 ]] || die "Run with sudo (it edits a systemd unit and restarts services)."

PY=$([[ -x "$DIR/.venv/bin/python" ]] && echo "$DIR/.venv/bin/python" || echo python3)
# edit_config is imported as setup.edit_config, so run from the project root.
cd "$DIR"

# --- 1. a token must exist, and be a real one ------------------------------

TOKEN=$("$PY" - "$CONFIG" <<'EOF'
import sys, yaml
print((yaml.safe_load(open(sys.argv[1])) or {}).get("remote", {}).get("token") or "")
EOF
)

if [[ -z "$TOKEN" || ${#TOKEN} -lt 16 ]]; then
  warn "No usable remote.token in config.yaml -- generating one."
  TOKEN=$("$PY" -c "import secrets; print(secrets.token_urlsafe(24))")
  "$PY" - "$CONFIG" "$TOKEN" <<'EOF'
import sys
sys.path.insert(0, ".")
from setup.edit_config import set_in_remote
set_in_remote(sys.argv[1], "token", f'"{sys.argv[2]}"')
EOF
  info "Generated access code: $TOKEN"
fi

# --- 2. stop trusting loopback --------------------------------------------

info "Setting remote.trust_loopback: false"
"$PY" - "$CONFIG" <<'EOF'
import sys
sys.path.insert(0, ".")
from setup.edit_config import set_in_remote
set_in_remote(sys.argv[1], "trust_loopback", "false")
EOF

"$PY" - "$CONFIG" <<'EOF'
import sys, yaml
remote = (yaml.safe_load(open(sys.argv[1])) or {}).get("remote", {})
assert remote.get("trust_loopback") is False, "trust_loopback did not take"
assert remote.get("token"), "no token after hardening"
print("    config.yaml is valid and hardened")
EOF

# --- 3. the kiosk now needs the code too ----------------------------------

if [[ -f "$KIOSK_UNIT" ]]; then
  PORT=$(sed -n 's/^[[:space:]]*port:[[:space:]]*\([0-9]*\).*/\1/p' "$CONFIG" | head -1)
  PORT="${PORT:-8080}"
  KIOSK_URL="http://127.0.0.1:${PORT}/?token=${TOKEN}"

  info "Pointing the kiosk at $KIOSK_URL"
  cp -n "$KIOSK_UNIT" "${KIOSK_UNIT}.bak" 2>/dev/null || true
  if grep -q '^Environment=WALLDISPLAY_URL=' "$KIOSK_UNIT"; then
    sed -i "s|^Environment=WALLDISPLAY_URL=.*|Environment=WALLDISPLAY_URL=${KIOSK_URL}|" "$KIOSK_UNIT"
  else
    sed -i "/^\[Service\]/a Environment=WALLDISPLAY_URL=${KIOSK_URL}" "$KIOSK_UNIT"
  fi
  systemctl daemon-reload
else
  warn "No kiosk unit at $KIOSK_UNIT (server-only install?)."
  warn "If a panel points at this server, give it the code once:"
  warn "    http://<server>:8080/?token=${TOKEN}"
fi

# --- 4. restart and re-check ----------------------------------------------

info "Restarting services"
systemctl restart walldisplay
systemctl is-active --quiet walldisplay || die "walldisplay did not come back: journalctl -u walldisplay -n 50"
[[ -f "$KIOSK_UNIT" ]] && systemctl restart walldisplay-kiosk 2>/dev/null || true

sleep 2
PORT=$(sed -n 's/^[[:space:]]*port:[[:space:]]*\([0-9]*\).*/\1/p' "$CONFIG" | head -1)
PORT="${PORT:-8080}"

# The exact shape a tunnel produces: local peer, forwarded header. Must 401.
CODE=$(curl -s -o /dev/null -w '%{http_code}' \
  -H 'X-Forwarded-For: 203.0.113.9' "http://127.0.0.1:${PORT}/api/status" || echo 000)

echo
if [[ "$CODE" == "401" ]]; then
  printf '\033[1;32mOK\033[0m  A proxied request without the code is refused (401).\n'
else
  printf '\033[1;31mFAIL\033[0m  Expected 401 for a proxied request, got %s.\n' "$CODE"
  die "Do not publish this yet. Check remote.token and remote.trust_loopback in config.yaml."
fi

cat <<DONE

Access code (needed by the remote page, the Shortcut, and the panel):

    $TOKEN

Next: publish the hostname, put Cloudflare Access in front of it, then check
from outside the house -- ideally on mobile data, not your own wifi:

    ./setup/verify-public-access.sh calendar.yourdomain.com "$TOKEN"

DONE
