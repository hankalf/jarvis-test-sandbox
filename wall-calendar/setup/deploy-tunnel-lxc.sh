#!/usr/bin/env bash
# Creates an unprivileged Debian LXC on Proxmox to run the Cloudflare Tunnel
# connector. Run this ON THE PROXMOX HOST.
#
# The connector needs no privileges and no inbound ports -- it dials out. Its
# own container keeps it away from whatever else is on the host, so a problem
# in one is not automatically a problem in the other.
#
# Follows the house rules in ../../CLAUDE.md: pre-flight checks first, every
# destructive command shown and confirmed before it runs, never an existing
# CTID, never anything belonging to another guest.
set -euo pipefail

CTID="${CTID:-}"                       # blank -> pick the first free one >= 200
HOSTNAME_="${HOSTNAME_:-cf-tunnel}"
STORAGE="${STORAGE:-local-lvm}"
TEMPLATE_STORAGE="${TEMPLATE_STORAGE:-local}"
BRIDGE="${BRIDGE:-vmbr0}"
MEMORY_MB="${MEMORY_MB:-512}"
DISK_GB="${DISK_GB:-4}"
CORES="${CORES:-1}"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!! \033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mxx \033[0m %s\n' "$*" >&2; exit 1; }

# Every state-changing command goes through this. Nothing runs unseen.
run() {
  printf '\n\033[1mAbout to run:\033[0m\n    %s\n' "$*"
  read -r -p "Run it? [y/N] " reply </dev/tty
  [[ "${reply,,}" == "y" ]] || die "Stopped at your request. Nothing was changed."
  "$@"
}

# --- pre-flight -----------------------------------------------------------

command -v pct >/dev/null || die "pct not found -- run this on the Proxmox host, not in a guest."
[[ $EUID -eq 0 ]] || die "Run as root on the Proxmox host."

info "Pre-flight checks"

pvesm status >/dev/null 2>&1 || die "pvesm status failed."
if ! pvesm status | awk 'NR>1 {print $1}' | grep -qx "$STORAGE"; then
  pvesm status | awk 'NR>1 {print "    " $1 " (" $2 ")"}'
  die "Storage '$STORAGE' not found. Set STORAGE= to one of the above."
fi
echo "    storage:  $STORAGE"

if ! grep -qE "^(auto|iface)\s+${BRIDGE}\b" /etc/network/interfaces 2>/dev/null \
   && ! ip -br link show "$BRIDGE" >/dev/null 2>&1; then
  die "Bridge '$BRIDGE' not found. Set BRIDGE= to your bridge (see: ip -br link)."
fi
echo "    bridge:   $BRIDGE"

# Existing guests are listed so it is obvious nothing here collides with them.
mapfile -t USED < <(pct list 2>/dev/null | awk 'NR>1 {print $1}'; qm list 2>/dev/null | awk 'NR>1 {print $1}')
if [[ -z "$CTID" ]]; then
  for candidate in $(seq 200 299); do
    if [[ ! " ${USED[*]} " =~ " ${candidate} " ]]; then CTID="$candidate"; break; fi
  done
  [[ -n "$CTID" ]] || die "No free CTID between 200 and 299. Set CTID= yourself."
elif [[ " ${USED[*]} " =~ " ${CTID} " ]]; then
  die "CTID $CTID is already in use. Pick another -- this script will not reuse an ID."
fi
echo "    new CTID: $CTID  (existing guests: ${USED[*]:-none})"

TEMPLATE=$(pveam list "$TEMPLATE_STORAGE" 2>/dev/null \
  | awk '/debian-12-standard/ {print $1}' | sort | tail -1 || true)
if [[ -z "$TEMPLATE" ]]; then
  warn "No debian-12-standard template found on '$TEMPLATE_STORAGE'."
  AVAILABLE=$(pveam available --section system | awk '/debian-12-standard/ {print $2}' | sort | tail -1)
  [[ -n "$AVAILABLE" ]] || die "Could not find a Debian 12 template to download."
  run pveam download "$TEMPLATE_STORAGE" "$AVAILABLE"
  TEMPLATE=$(pveam list "$TEMPLATE_STORAGE" | awk '/debian-12-standard/ {print $1}' | sort | tail -1)
fi
echo "    template: $TEMPLATE"

echo
bold "Plan"
cat <<PLAN
    Create unprivileged LXC $CTID ($HOSTNAME_)
      ${CORES} core, ${MEMORY_MB} MB RAM, ${DISK_GB} GB on $STORAGE
      network: DHCP on $BRIDGE
      nesting off, no mounts, no privileges -- it only makes outbound HTTPS

    Nothing else on this host is touched. No existing guest is modified.
PLAN

read -r -p $'\nProceed? [y/N] ' go </dev/tty
[[ "${go,,}" == "y" ]] || die "Stopped. Nothing was changed."

# --- create ---------------------------------------------------------------

run pct create "$CTID" "$TEMPLATE" \
  --hostname "$HOSTNAME_" \
  --cores "$CORES" \
  --memory "$MEMORY_MB" \
  --swap 256 \
  --rootfs "${STORAGE}:${DISK_GB}" \
  --net0 "name=eth0,bridge=${BRIDGE},ip=dhcp" \
  --unprivileged 1 \
  --features nesting=0 \
  --onboot 1 \
  --description "Cloudflare Tunnel connector for the wall calendar"

run pct start "$CTID"

info "Waiting for the container to get an address"
for _ in $(seq 1 30); do
  IP=$(pct exec "$CTID" -- hostname -I 2>/dev/null | awk '{print $1}' || true)
  [[ -n "${IP:-}" ]] && break
  sleep 2
done
[[ -n "${IP:-}" ]] || warn "No address yet; check with: pct exec $CTID -- ip a"

info "Installing prerequisites inside the container"
run pct exec "$CTID" -- bash -lc \
  "apt-get update -qq && apt-get install -y -qq curl ca-certificates gnupg lsb-release"

cat <<DONE

$(bold "Container $CTID is up${IP:+ at $IP}.")

Next, in this order:

  1. Create the tunnel in the Cloudflare dashboard and copy its token:
     Zero Trust -> Networks -> Tunnels -> Create a tunnel -> Cloudflared
     (see docs/public-access.md for the public-hostname settings)

  2. Install the connector in the container:
       pct exec $CTID -- bash -lc 'bash -s' < setup/install-tunnel.sh
     or push the script in and run it with the token:
       pct push $CTID setup/install-tunnel.sh /root/install-tunnel.sh
       pct exec $CTID -- bash /root/install-tunnel.sh <TUNNEL_TOKEN>

  3. Add a Cloudflare Access policy in front of the hostname. Do not skip
     this -- without it the calendar is behind one shared string.

  4. Harden the calendar host and verify from outside:
       ./setup/harden-for-public.sh
       ./setup/verify-public-access.sh calendar.yourdomain.com <ACCESS_TOKEN>

DONE
