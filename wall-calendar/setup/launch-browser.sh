#!/usr/bin/env bash
# Launched by cage as the single kiosk application.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
URL="${WALLDISPLAY_URL:-http://127.0.0.1:8080}"
PROFILE="${WALLDISPLAY_PROFILE:-$DIR/data/chrome-profile}"
ROTATE="${WALLDISPLAY_ROTATE:-}"   # 90 / 180 / 270 for a portrait-mounted panel

mkdir -p "$PROFILE"

# Rotation has to happen after the compositor is up, so it runs in the
# background against the now-live Wayland socket.
if [[ -n "$ROTATE" ]] && command -v wlr-randr >/dev/null 2>&1; then
  (
    sleep 2
    output=$(wlr-randr | awk 'NR==1 {print $1}')
    wlr-randr --output "$output" --transform "$ROTATE" || true
  ) &
fi

# Wait for the server before opening the page, so the first paint is the
# calendar rather than a connection error the display would keep showing.
for _ in $(seq 1 60); do
  if curl -sf -o /dev/null "$URL/api/health"; then break; fi
  sleep 1
done

for candidate in chromium chromium-browser google-chrome; do
  if command -v "$candidate" >/dev/null 2>&1; then BROWSER="$candidate"; break; fi
done
: "${BROWSER:?no chromium binary found -- install chromium}"

exec "$BROWSER" \
  --kiosk \
  --app="$URL" \
  --user-data-dir="$PROFILE" \
  --ozone-platform=wayland \
  --enable-features=OverlayScrollbar \
  --noerrdialogs \
  --disable-infobars \
  --disable-session-crashed-bubble \
  --disable-features=TranslateUI \
  --hide-scrollbars \
  --autoplay-policy=no-user-gesture-required \
  --check-for-update-interval=31536000 \
  --disable-pinch \
  --overscroll-history-navigation=0
