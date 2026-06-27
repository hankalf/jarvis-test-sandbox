#!/usr/bin/env bash
#
# deploy-jarvis-hassos-sandbox.sh
# -------------------------------------------------------------------
# Creates a FRESH, ISOLATED Home Assistant OS VM on Proxmox to use as a
# throwaway sandbox for the Jarvis project. Does NOT touch Marshall01.
#
# Use this as the "brain" while testing: point it at the voice-services VM
# (from deploy-jarvis-voice.sh), wire up the Assist pipeline, break things
# freely, and roll back with a Proxmox snapshot. When you're happy, you can
# replicate the config on Marshall01 (or just keep using this one).
#
# WHAT THIS DOES:
#   * resolves the latest Home Assistant OS release (or a version you pin)
#   * downloads + extracts the official KVM/qcow2 image
#   * creates a UEFI (OVMF) q35 VM with an EFI disk, imports the HAOS disk,
#     sets boot order, and starts it
#
# AFTER IT BOOTS: browse to http://<vm-ip>:8123 to onboard Home Assistant,
# then follow jarvis-setup-notes.md (the 4 UI steps) against THIS instance.
#
# REQUIREMENTS:
#   * Run as root ON THE PROXMOX HOST.
#   * Proxmox VE 8.x / 9.x (uses `import-from`).
#   * Internet access on the host (to fetch the HAOS image).
#
# Tip: the trusted community one-liner does the same thing interactively:
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/community-scripts/ProxmoxVE/main/vm/haos-vm.sh)"
# This script is the same idea, pinned and non-interactive for repeatability.
#
# NOTE: untested in your specific environment — review CONFIG, then run.
# -------------------------------------------------------------------

set -euo pipefail

############################ EDIT THESE ############################
VMID="911"                       # an unused VM ID (Marshall01 keeps its own)
VM_NAME="jarvis-hass-sandbox"
STORAGE="local-lvm"              # storage pool for the VM + EFI disk
BRIDGE="vmbr0"                   # network bridge
CORES="2"                        # HAOS itself is light; 2 is the recommended min
RAM_MB="4096"
EXTRA_DISK_GB="0"                # grow the 32GB image by this much (0 = leave as-is)

# Pin a version like "17.1" to override auto-detect, or leave blank for latest.
HAOS_VERSION=""
###################################################################

IMG_DIR="/var/lib/vz/template/iso"
mkdir -p "$IMG_DIR"

echo "==> [1/5] Resolving Home Assistant OS version"
if [[ -z "$HAOS_VERSION" ]]; then
  HAOS_VERSION="$(curl -fsSL https://api.github.com/repos/home-assistant/operating-system/releases/latest \
    | grep -oP '"tag_name":\s*"\K[^"]+')"
fi
[[ -n "$HAOS_VERSION" ]] || { echo "Could not determine HAOS version; set HAOS_VERSION manually."; exit 1; }
echo "    using HAOS ${HAOS_VERSION}"

XZ_FILE="${IMG_DIR}/haos_ova-${HAOS_VERSION}.qcow2.xz"
QCOW_FILE="${IMG_DIR}/haos_ova-${HAOS_VERSION}.qcow2"
URL="https://github.com/home-assistant/operating-system/releases/download/${HAOS_VERSION}/haos_ova-${HAOS_VERSION}.qcow2.xz"

echo "==> [2/5] Fetching + extracting image (if needed)"
if [[ ! -f "$QCOW_FILE" ]]; then
  [[ -f "$XZ_FILE" ]] || curl -fSL -o "$XZ_FILE" "$URL"
  unxz -k "$XZ_FILE"
else
  echo "    already present: $QCOW_FILE"
fi

echo "==> [3/5] Creating VM ${VMID} (${VM_NAME}) — q35 / UEFI"
# --cpu host is the most compatible on a single node. If the VM refuses to
# start with a CPU-feature warning, change it to x86-64-v2-AES or kvm64.
qm create "$VMID" \
  --name "$VM_NAME" \
  --machine q35 \
  --bios ovmf \
  --memory "$RAM_MB" \
  --cores "$CORES" \
  --cpu host \
  --ostype l26 \
  --scsihw virtio-scsi-single \
  --net0 "virtio,bridge=${BRIDGE}" \
  --efidisk0 "${STORAGE}:0,efitype=4m,pre-enroll-keys=0" \
  --agent enabled=1 \
  --onboot 1

echo "==> [4/5] Importing HAOS disk + boot order"
qm set "$VMID" --scsi0 "${STORAGE}:0,import-from=${QCOW_FILE},discard=on"
qm set "$VMID" --boot order=scsi0

if [[ "${EXTRA_DISK_GB}" != "0" ]]; then
  echo "    growing disk by ${EXTRA_DISK_GB}G (HAOS expands its data partition on boot)"
  qm resize "$VMID" scsi0 "+${EXTRA_DISK_GB}G"
fi

echo "==> [5/5] Starting VM"
qm start "$VMID"

cat <<DONE

-------------------------------------------------------------------
Done. Sandbox Home Assistant VM ${VMID} (${VM_NAME}) is booting.

Give it a few minutes, then find its IP:
    qm guest cmd ${VMID} network-get-interfaces
    (or check your router's DHCP list)

Onboard at:  http://<that-ip>:8123

TEST PLAN (nothing here touches Marshall01):
  1. Onboard HA, create an account.
  2. Take a Proxmox snapshot now (Options > Snapshots) so you can roll back.
  3. Follow jarvis-setup-notes.md against THIS instance:
       - add the 3 Wyoming integrations -> your voice-services VM IP
       - add the cloud brain (Anthropic / OpenAI) with your API key
       - build the "hey_jarvis" Assist pipeline + paste the personality prompt
  4. Break things freely. Roll back to the snapshot if needed.
-------------------------------------------------------------------
DONE
