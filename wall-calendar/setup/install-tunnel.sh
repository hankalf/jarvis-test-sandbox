#!/usr/bin/env bash
# Installs the Cloudflare Tunnel connector. Run INSIDE the tunnel container
# (or on any Debian/Ubuntu host that can reach the calendar).
#
#   ./install-tunnel.sh <TUNNEL_TOKEN>
#
# The token comes from the Cloudflare dashboard when you create the tunnel:
# Zero Trust -> Networks -> Tunnels -> Create a tunnel -> Cloudflared. It is a
# long string starting "eyJ". Treat it as a password: anyone holding it can
# register a connector for your tunnel.
set -euo pipefail

TOKEN="${1:-${CLOUDFLARE_TUNNEL_TOKEN:-}}"

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mxx \033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Run as root (or with sudo)."

if [[ -z "$TOKEN" ]]; then
  cat >&2 <<USAGE
Usage: $0 <TUNNEL_TOKEN>

Get the token from the Cloudflare dashboard:
  Zero Trust -> Networks -> Tunnels -> Create a tunnel -> Cloudflared
It appears in the install command they show you, after "--token".
USAGE
  exit 1
fi

# A pasted dashboard command sometimes brings the flag along; be forgiving.
TOKEN="${TOKEN#--token }"
TOKEN="${TOKEN#--token=}"
[[ "$TOKEN" == eyJ* ]] || die "That does not look like a tunnel token (they start with 'eyJ')."

info "Adding the Cloudflare package repository"
install -d -m 0755 /usr/share/keyrings
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
  | tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
chmod 0644 /usr/share/keyrings/cloudflare-main.gpg

CODENAME=$(. /etc/os-release && echo "${VERSION_CODENAME:-bookworm}")
echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared ${CODENAME} main" \
  > /etc/apt/sources.list.d/cloudflared.list

info "Installing cloudflared"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq cloudflared

# Re-running should be harmless: replacing the service is how you rotate a
# token or repoint a connector.
if systemctl list-unit-files 2>/dev/null | grep -q '^cloudflared\.service'; then
  info "A connector is already installed; replacing it with this token"
  cloudflared service uninstall || true
fi

info "Registering the connector"
cloudflared service install "$TOKEN"

systemctl enable --now cloudflared
sleep 3

if systemctl is-active --quiet cloudflared; then
  info "Connector is running"
else
  die "cloudflared did not start. Check: journalctl -u cloudflared -n 50"
fi

cat <<DONE

$(printf '\033[1m%s\033[0m' "Connected.")

  Status:  systemctl status cloudflared
  Logs:    journalctl -u cloudflared -f

The tunnel should now show a healthy connector in the Cloudflare dashboard.

Still to do, and the order matters:

  1. Public hostname on the tunnel (dashboard):
       Subdomain    calendar
       Domain       yourdomain.com
       Service      HTTP  ->  <calendar-host-ip>:8080

  2. Cloudflare Access in front of that hostname. Without it the calendar is
     protected by one shared code that cannot be revoked per person.

  3. On the calendar host:  ./setup/harden-for-public.sh
     A tunnel connects to the app from 127.0.0.1, which the app trusts. That
     script closes the gap; skipping it leaves the calendar open.

  4. From a phone on mobile data:
       ./setup/verify-public-access.sh calendar.yourdomain.com <ACCESS_TOKEN>

DONE
