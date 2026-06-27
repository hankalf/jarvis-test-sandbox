# Project: "Hey Jarvis" self-hosted voice assistant on Proxmox

You are running on (or with access to) a Proxmox VE host. Your job is to help
deploy a local voice-assistant stack using the two scripts in this folder.

## HARD CONSTRAINTS — read first
- There is an EXISTING production Home Assistant VM on this host named
  **Marshall01**. **Do NOT modify, stop, snapshot-delete, or touch it** in any
  way. Everything you create must be NEW and isolated.
- Use **fresh, unused VM IDs**. Confirm they're free with `qm list` before creating.
- **Ask before every `qm` command** and anything destructive. Never reuse an
  existing VM ID. Show me the command and wait for approval.

## What's in this folder
- `deploy-jarvis-hassos-sandbox.sh` — creates a throwaway Home Assistant OS
  sandbox VM (default VMID 911) to use as the test "brain". Run this FIRST.
- `deploy-jarvis-voice.sh` — creates a small Debian VM (default VMID 910)
  running Whisper (STT), Piper (TTS), and openWakeWord ("hey_jarvis") as
  Docker/Wyoming services. Run this SECOND.
- `jarvis-setup-notes.md` — the manual Home Assistant UI steps that come after
  the VMs exist (wiring + cloud LLM API key + personality prompt). Not scriptable.

## Pre-flight checks BEFORE running either script
Run these and reconcile the script variables to reality:
1. **Storage pool name** (scripts default to `local-lvm`):
   `pvesm status`  — confirm the pool exists; edit `STORAGE=` if different.
2. **Network bridge** (scripts default to `vmbr0`):
   `ip -br link` or `cat /etc/network/interfaces` — edit `BRIDGE=` if different.
3. **Free VM IDs**: `qm list` — pick unused IDs; edit `VMID=` in each script.
4. **Snippets enabled** (the voice script writes a cloud-init snippet to
   `local:snippets`): check `cat /etc/pve/storage.cfg` for a storage with
   `content` including `snippets`. If none, enable it (e.g. add `snippets` to
   the `local` storage content types) BEFORE running the voice script.
5. **Proxmox version**: `pveversion` — scripts use `import-from` (needs PVE 8+).

## Run order
1. Pre-flight checks above.
2. `bash deploy-jarvis-hassos-sandbox.sh` → wait for boot →
   `qm guest cmd <VMID> network-get-interfaces` to get its IP → confirm
   `http://<ip>:8123` loads → take a Proxmox snapshot of it.
3. `bash deploy-jarvis-voice.sh` → wait ~3-5 min for first boot (Docker pull) →
   `ssh jarvis@<voice-vm-ip>` then `docker ps` should show wyoming-whisper,
   wyoming-piper, wyoming-openwakeword (all "Up").
4. Report both VM IPs and container status back to me. Then I do the manual HA
   UI steps from `jarvis-setup-notes.md`.

## Notes
- These scripts were logic-validated (syntax + rendered cloud-init YAML) but
  NOT run against real hardware, so the pre-flight checks matter.
- No GPU on this host: the cloud LLM is the brain (added later in the HA UI);
  only Whisper runs locally on CPU. Keep the Whisper model at `small-int8` or
  drop to `base-int8` if responses feel slow.
- If `qm create` fails, the cause is almost always a storage/bridge name
  mismatch or snippets not enabled — fix the variable and retry.
