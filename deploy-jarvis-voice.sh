#!/usr/bin/env bash
#
# deploy-jarvis-voice.sh
# -------------------------------------------------------------------
# Creates a lightweight Debian 12 VM on Proxmox that runs the three
# local voice engines for a "Hey Jarvis" Home Assistant Assist pipeline:
#
#   - faster-whisper   (speech-to-text)  -> tcp 10300   [CPU, no GPU needed]
#   - Piper            (text-to-speech)  -> tcp 10200
#   - openWakeWord     (wake word)       -> tcp 10400   [pre-loads "hey_jarvis"]
#
# Your EXISTING Home Assistant (Marshall01) stays the brain. After this
# runs, you point HA at this VM's IP via three Wyoming integrations.
# The LLM reasoning is CLOUD (Anthropic/OpenAI) and is configured in the
# HA UI, not here (it needs your secret API key + a config-flow click).
#
# WHAT THIS SCRIPT DOES:
#   * downloads the Debian 12 cloud image (if missing)
#   * creates a cloud-init VM
#   * installs Docker + brings up the 3 Wyoming containers on first boot
#   * sets them to auto-restart on reboot
#
# WHAT IT DOES NOT DO (these are ~5 min of clicks in the HA UI afterward):
#   * enter your cloud LLM API key
#   * wire the Wyoming integrations / build the Assist pipeline
#   * expose entities or set the Jarvis personality prompt
#   (All four are listed in jarvis-setup-notes.md.)
#
# REQUIREMENTS:
#   * Run as root ON THE PROXMOX HOST (not inside a VM).
#   * Proxmox VE 8.x or newer (uses `import-from`).
#   * "Snippets" content type enabled on the `local` storage
#     (Datacenter > Storage > local > Edit > Content > tick "Snippets").
#
# NOTE: This has NOT been tested in your specific environment. Review the
# CONFIG block, then test with a throwaway VMID first.
# -------------------------------------------------------------------

set -euo pipefail

############################ EDIT THESE ############################
VMID="910"                       # an unused VM ID
VM_NAME="jarvis-voice"
STORAGE="local-lvm"              # Proxmox storage pool for the VM disk
BRIDGE="vmbr0"                   # your network bridge
CORES="4"                        # Whisper on CPU likes cores; 4 is a good start
RAM_MB="4096"
DISK_GB="20"

# Login for the new VM (so you can SSH in if needed)
CIUSER="jarvis"
CIPASSWORD="changeme-please"     # change this
SSH_PUBKEY=""                    # optional: paste a public key string to enable key login

# Networking: DHCP by default. For a static IP use e.g.:
#   IPCONFIG="ip=192.168.1.50/24,gw=192.168.1.1"
IPCONFIG="ip=dhcp"

# ---- Voice engine tuning ----
# Whisper model (CPU-friendly options): tiny-int8 < base-int8 < small-int8 < medium-int8
#   small-int8 = good accuracy/speed balance on CPU. Drop to base-int8 if replies feel slow.
WHISPER_MODEL="small-int8"
WHISPER_LANG="en"

# Piper voice. en_GB voices give that dry British-butler feel. Alternatives:
#   en_GB-northern_english_male-medium , en_GB-alba-medium , en_US-ryan-high
PIPER_VOICE="en_GB-alan-medium"

# Wake word preloaded by openWakeWord. "hey_jarvis" ships built in.
WAKEWORD="hey_jarvis"

# Snippet (cloud-init user-data) storage + path
SNIPPET_STORAGE="local"
SNIPPET_DIR="/var/lib/vz/snippets"
###################################################################

IMG_URL="https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-genericcloud-amd64.qcow2"
IMG_DIR="/var/lib/vz/template/iso"
IMG_FILE="${IMG_DIR}/debian-12-genericcloud-amd64.qcow2"
SNIPPET_FILE="${SNIPPET_DIR}/${VM_NAME}-user.yaml"

echo "==> [1/6] Preparing directories"
mkdir -p "$IMG_DIR" "$SNIPPET_DIR"

echo "==> [2/6] Fetching Debian 12 cloud image (if needed)"
if [[ ! -f "$IMG_FILE" ]]; then
  curl -fSL -o "$IMG_FILE" "$IMG_URL"
else
  echo "    already present: $IMG_FILE"
fi

echo "==> [3/6] Writing cloud-init user-data snippet"
cat > "$SNIPPET_FILE" <<EOF
#cloud-config
hostname: ${VM_NAME}
manage_etc_hosts: true
users:
  - name: ${CIUSER}
    groups: [sudo]
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    lock_passwd: false
$( [[ -n "$SSH_PUBKEY" ]] && echo "    ssh_authorized_keys:
      - ${SSH_PUBKEY}" )
ssh_pwauth: true
chpasswd:
  expire: false
  users:
    - name: ${CIUSER}
      password: ${CIPASSWORD}
      type: text
package_update: true
packages:
  - ca-certificates
  - curl
  - qemu-guest-agent
write_files:
  - path: /opt/jarvis/docker-compose.yml
    permissions: '0644'
    content: |
      services:
        whisper:
          image: rhasspy/wyoming-whisper:latest
          container_name: wyoming-whisper
          command: --model ${WHISPER_MODEL} --language ${WHISPER_LANG} --uri tcp://0.0.0.0:10300 --data-dir /data --download-dir /data --beam-size 1
          volumes:
            - ./whisper-data:/data
          ports:
            - "10300:10300"
          restart: unless-stopped
        piper:
          image: rhasspy/wyoming-piper:latest
          container_name: wyoming-piper
          command: --voice ${PIPER_VOICE} --uri tcp://0.0.0.0:10200 --data-dir /data --download-dir /data
          volumes:
            - ./piper-data:/data
          ports:
            - "10200:10200"
          restart: unless-stopped
        openwakeword:
          image: rhasspy/wyoming-openwakeword:latest
          container_name: wyoming-openwakeword
          command: --preload-model ${WAKEWORD} --uri tcp://0.0.0.0:10400
          restart: unless-stopped
          ports:
            - "10400:10400"
runcmd:
  - systemctl enable --now qemu-guest-agent
  - curl -fsSL https://get.docker.com | sh
  - usermod -aG docker ${CIUSER}
  - mkdir -p /opt/jarvis/whisper-data /opt/jarvis/piper-data
  - cd /opt/jarvis && docker compose up -d
EOF

echo "==> [4/6] Creating VM ${VMID} (${VM_NAME})"
qm create "$VMID" \
  --name "$VM_NAME" \
  --memory "$RAM_MB" \
  --cores "$CORES" \
  --cpu host \
  --net0 "virtio,bridge=${BRIDGE}" \
  --scsihw virtio-scsi-single \
  --ostype l26 \
  --agent enabled=1

echo "==> [5/6] Importing disk + cloud-init config"
qm set "$VMID" --scsi0 "${STORAGE}:0,import-from=${IMG_FILE},discard=on"
qm set "$VMID" --ide2 "${STORAGE}:cloudinit"
qm set "$VMID" --boot order=scsi0
qm set "$VMID" --serial0 socket --vga serial0
qm set "$VMID" --ipconfig0 "$IPCONFIG"
qm set "$VMID" --cicustom "user=${SNIPPET_STORAGE}:snippets/${VM_NAME}-user.yaml"
qm disk resize "$VMID" scsi0 "${DISK_GB}G"

echo "==> [6/6] Starting VM"
qm start "$VMID"

cat <<DONE

-------------------------------------------------------------------
Done. VM ${VMID} (${VM_NAME}) is booting.

First boot installs Docker and pulls the three voice images, so give it
~3-5 minutes (longer on first Whisper model download) before it answers.

Find its IP:        qm guest cmd ${VMID} network-get-interfaces
                    (or check your router / Proxmox summary)

Verify services:    ssh ${CIUSER}@<that-ip>  then:
                    docker ps
                    (you should see wyoming-whisper / -piper / -openwakeword)

NEXT: open jarvis-setup-notes.md and do the 4 quick HA UI steps to wire
this into Home Assistant on Marshall01 and add the cloud brain.
-------------------------------------------------------------------
DONE
