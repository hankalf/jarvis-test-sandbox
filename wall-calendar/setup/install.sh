#!/usr/bin/env bash
# Installs the wall calendar display on Raspberry Pi OS or Debian/Ubuntu x86.
#
#   sudo ./setup/install.sh            # server + kiosk browser
#   sudo ./setup/install.sh --server   # server only (e.g. running it on a NAS)
#
# Safe to re-run: it upgrades in place and never overwrites config.yaml.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WITH_KIOSK=1
[[ "${1:-}" == "--server" ]] && WITH_KIOSK=0

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo: sudo $0 $*" >&2
  exit 1
fi

# The service runs as the human who owns the checkout, not root.
RUN_USER="${SUDO_USER:-$(stat -c '%U' "$DIR")}"
RUN_UID="$(id -u "$RUN_USER")"

say() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }

say "Installing for user '$RUN_USER' from $DIR"

say "Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
PACKAGES=(python3 python3-venv python3-dev curl ca-certificates)
if [[ $WITH_KIOSK -eq 1 ]]; then
  PACKAGES+=(cage wlr-randr fonts-dejavu-core)
  # Raspberry Pi OS names it chromium-browser; Debian and Ubuntu use chromium.
  if apt-cache show chromium >/dev/null 2>&1; then
    PACKAGES+=(chromium)
  else
    PACKAGES+=(chromium-browser)
  fi
fi
apt-get install -y -qq "${PACKAGES[@]}"

say "Creating the Python environment"
sudo -u "$RUN_USER" python3 -m venv "$DIR/.venv"
sudo -u "$RUN_USER" "$DIR/.venv/bin/pip" install -q --upgrade pip
sudo -u "$RUN_USER" "$DIR/.venv/bin/pip" install -q -r "$DIR/requirements.txt"

# Presence backends pull in hardware libraries only where they can work.
BACKEND=$(sed -n 's/^[[:space:]]*backend:[[:space:]]*\([a-z]*\).*/\1/p' "$DIR/config.yaml" 2>/dev/null | head -1 || true)
case "$BACKEND" in
  serial) say "Installing pyserial for the mmWave presence sensor"
          sudo -u "$RUN_USER" "$DIR/.venv/bin/pip" install -q pyserial
          usermod -aG dialout "$RUN_USER" ;;
  gpio)   say "Installing gpiozero for the GPIO presence sensor"
          sudo -u "$RUN_USER" "$DIR/.venv/bin/pip" install -q gpiozero lgpio
          usermod -aG gpio "$RUN_USER" 2>/dev/null || true ;;
esac

if [[ ! -f "$DIR/config.yaml" ]]; then
  say "Creating config.yaml from the example (edit it before this is useful)"
  cp "$DIR/config.example.yaml" "$DIR/config.yaml"
  chown "$RUN_USER" "$DIR/config.yaml"
fi
sudo -u "$RUN_USER" mkdir -p "$DIR/photos" "$DIR/data"

HOST=$(sed -n 's/^[[:space:]]*host:[[:space:]]*\(.*\)/\1/p' "$DIR/config.yaml" | head -1)
PORT=$(sed -n 's/^[[:space:]]*port:[[:space:]]*\([0-9]*\).*/\1/p' "$DIR/config.yaml" | head -1)
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8080}"

install_unit() {
  local name="$1"
  sed -e "s|__DIR__|$DIR|g" -e "s|__USER__|$RUN_USER|g" -e "s|__UID__|$RUN_UID|g" \
      -e "s|__HOST__|$HOST|g" -e "s|__PORT__|$PORT|g" \
      "$DIR/setup/$name" > "/etc/systemd/system/$name"
}

say "Installing systemd units"
chmod +x "$DIR/setup/launch-browser.sh"
install_unit walldisplay.service
[[ $WITH_KIOSK -eq 1 ]] && install_unit walldisplay-kiosk.service

systemctl daemon-reload
systemctl enable --now walldisplay.service

if [[ $WITH_KIOSK -eq 1 ]]; then
  usermod -aG video,input,render "$RUN_USER" 2>/dev/null || true
  # cage owns tty1, so nothing else may try to draw a login prompt there.
  systemctl disable --now getty@tty1.service 2>/dev/null || true
  systemctl set-default graphical.target
  systemctl enable walldisplay-kiosk.service
fi

say "Done"
cat <<EOF

  Server:  http://$(hostname -I 2>/dev/null | awk '{print $1}'):$PORT
  Config:  $DIR/config.yaml    (calendar feed URLs go here)
  Photos:  $DIR/photos/        (drop images in, no restart needed)

  Logs:    journalctl -u walldisplay -f
$( [[ $WITH_KIOSK -eq 1 ]] && echo "  Kiosk:   journalctl -u walldisplay-kiosk -f" )

  Next: edit config.yaml, then 'sudo systemctl restart walldisplay'$( [[ $WITH_KIOSK -eq 1 ]] && echo " && reboot" ).
EOF
