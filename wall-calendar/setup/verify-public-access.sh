#!/usr/bin/env bash
# Checks a published wall calendar from the outside.
#
#   ./verify-public-access.sh calendar.yourdomain.com [ACCESS_CODE]
#
# Run it from somewhere that is NOT your home network -- a phone on mobile
# data, a VPS, a friend's wifi. From inside the house you may reach the app
# directly and prove nothing about what the internet can see.
#
# The check that matters: an unauthenticated request must not return the
# calendar. Exit code is non-zero if anything is exposed.
set -uo pipefail

HOST="${1:-}"
CODE="${2:-}"

green() { printf '\033[1;32m%s\033[0m %s\n' "PASS" "$*"; }
red()   { printf '\033[1;31m%s\033[0m %s\n' "FAIL" "$*"; }
amber() { printf '\033[1;33m%s\033[0m %s\n' "WARN" "$*"; }
info()  { printf '\033[1;34m%s\033[0m %s\n' "----" "$*"; }

if [[ -z "$HOST" ]]; then
  echo "Usage: $0 <hostname> [access-code]" >&2
  echo "   e.g. $0 calendar.example.com aVeryLongRandomString" >&2
  exit 2
fi
case "$HOST" in
  http://*|https://*) BASE="${HOST%%/}" ;;          # explicit scheme wins
  *)                  BASE="https://${HOST%%/*}" ;; # bare hostname -> https
esac
FAILURES=0

# status:headers in one request, so an Access redirect can be recognised.
probe() {
  curl -sS -o /tmp/vpa.body -D /tmp/vpa.head -w '%{http_code}' \
       --max-time 20 "$@" 2>/dev/null || echo 000
}
is_access_challenge() {
  grep -qiE '^(location:.*cloudflareaccess\.com|cf-access|set-cookie: *CF_Auth)' /tmp/vpa.head \
    || grep -qiE 'cloudflareaccess\.com|Sign in with' /tmp/vpa.body 2>/dev/null
}

echo
info "Checking ${BASE}"
echo

# --- 1. TLS and reachability ----------------------------------------------

HTTP=$(probe "$BASE/api/health")
if [[ "$HTTP" == "000" ]]; then
  red "Cannot reach $BASE at all."
  echo "     DNS not propagated, tunnel down, or no public hostname configured."
  echo "     On the tunnel host: systemctl status cloudflared"
  exit 1
fi
case "$BASE" in
  https://*) green "Reachable over HTTPS (certificate accepted)." ;;
  *)         amber "Reachable over plain HTTP -- fine for a pre-check on the LAN," ;
             echo  "     but the published hostname must be https." ;;
esac

# --- 2. THE important one: unauthenticated access --------------------------

STATUS=$(probe "$BASE/api/status")
if [[ "$STATUS" == "200" ]]; then
  red "An unauthenticated request returned your calendar data (HTTP 200)."
  echo
  echo "     This is the loopback trap: the tunnel connects to the app from"
  echo "     127.0.0.1, which the app trusts. Anyone with the URL can read"
  echo "     and change the calendar right now."
  echo
  echo "     Fix on the calendar host:"
  echo "         sudo ./setup/harden-for-public.sh"
  echo
  FAILURES=$((FAILURES + 1))
elif is_access_challenge; then
  green "Cloudflare Access is challenging unauthenticated requests (HTTP $STATUS)."
  green "The app is not directly reachable from the internet."
elif [[ "$STATUS" == "401" ]]; then
  green "Unauthenticated requests are refused by the app (HTTP 401)."
  amber "No Cloudflare Access challenge detected."
  echo "     The calendar is behind one shared code that cannot be revoked"
  echo "     per person. Consider adding an Access policy -- it is free and"
  echo "     it means strangers never reach the app at all."
  echo "     See docs/public-access.md, step 3."
elif [[ "$STATUS" == "429" ]]; then
  amber "Rate limited (HTTP 429) -- earlier attempts tripped the throttle."
  echo "     Wait five minutes and re-run to get a clean reading."
else
  amber "Unauthenticated request returned HTTP $STATUS (not 200, so not exposed)."
fi

# --- 3. the panel page itself ---------------------------------------------

REMOTE=$(probe "$BASE/remote")
if [[ "$REMOTE" == "200" ]] && ! is_access_challenge; then
  if grep -q 'Access code' /tmp/vpa.body 2>/dev/null; then
    amber "The remote page is publicly served (it asks for a code, but is visible)."
    echo "     Harmless on its own -- the page holds no data until it has the"
    echo "     code -- but Access would keep strangers off it entirely."
  fi
fi

# --- 4. does the code still work through the tunnel? -----------------------

if [[ -n "$CODE" ]]; then
  echo
  info "Checking the access code works through the tunnel"
  WITH=$(probe -H "X-Wall-Token: ${CODE}" "$BASE/api/status")
  if [[ "$WITH" == "200" ]]; then
    green "The access code is accepted (HTTP 200)."
  elif is_access_challenge; then
    amber "Cloudflare Access blocks it before the app sees the code (HTTP $WITH)."
    echo "     Expected: Access needs an interactive login or a service token."
    echo "     For the iPhone Shortcut, add a service-token policy for"
    echo "     /api/import/message -- docs/public-access.md, step 4."
  elif [[ "$WITH" == "401" ]]; then
    red "The access code was rejected (HTTP 401)."
    echo "     It does not match remote.token in config.yaml on the calendar host."
    FAILURES=$((FAILURES + 1))
  else
    amber "Unexpected HTTP $WITH with the code."
  fi
else
  echo
  info "No access code given -- skipping the authenticated check."
  echo "     Re-run with the code as the second argument to test it end to end."
fi

# --- 5. hygiene ------------------------------------------------------------

echo
info "Response headers"
probe "$BASE/remote" >/dev/null
for header in "x-frame-options" "x-content-type-options" "referrer-policy"; do
  if grep -qi "^${header}:" /tmp/vpa.head; then
    green "$(grep -i "^${header}:" /tmp/vpa.head | head -1 | tr -d '\r')"
  else
    amber "${header} missing (harmless behind Access; expected if a proxy strips it)."
  fi
done

rm -f /tmp/vpa.body /tmp/vpa.head

echo
if [[ $FAILURES -gt 0 ]]; then
  red "$FAILURES problem(s) found. Do not leave this published until they are fixed."
  exit 1
fi
green "No exposure found."
echo
echo "Worth repeating this after any change to the tunnel, the Access policy,"
echo "or config.yaml -- it is the only check that sees what the internet sees."
