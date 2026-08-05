# Wall Calendar Display

A touch-screen wall panel that shows your work schedule and appointments, and
sits as a digital picture frame when nobody's near it. Walk up, it switches to
the calendar. Walk away, it goes back to photos.

Runs on a Raspberry Pi 5 or a small x86 mini PC. No cloud service, no account,
no subscription — it reads your existing calendars over their standard iCal
feeds and everything else stays on the box.

## What it does

- **Today view** — today's schedule beside an agenda of what's coming up. This is
  what you see when you walk up to it.
- **Week view** — a proper timed grid, laid out so a week of shifts is readable
  from across the room. Overlapping events sit side by side.
- **Month view** — the whole month; tap a day to jump to it.
- **Photo frame** — full-bleed slideshow with a slow pan, a drifting clock, and
  your next appointment in the corner.
- **Add appointments on the panel** — big touch targets, native date and time
  pickers.
- **Quiet hours** — blanks overnight, but presence still wakes it.

Calendars sync one-way and read-only: your phone stays the place you edit things,
and the panel can't corrupt them. Events added on the panel live in its own
local database.

## Quick start

On the Pi or mini PC, with a fresh Raspberry Pi OS / Debian / Ubuntu install:

```bash
git clone <this repo> && cd jarvis-test-sandbox/wall-calendar
sudo ./setup/install.sh
```

Then edit `config.yaml` — the only part you must fill in is your calendar feed
URLs — and reboot:

```bash
nano config.yaml
sudo systemctl restart walldisplay && sudo reboot
```

The panel comes up full-screen on boot. Open `http://<its-ip>:8080` from your
laptop to check it without walking over.

`--server` installs the backend only, if the screen is a different machine.

### Getting your calendar feed URLs

| Source | Where |
|---|---|
| Google Calendar | Settings → *your calendar* → **Secret address in iCal format** |
| iCloud | Right-click calendar → Share Calendar → Public Calendar (copy the link) |
| Outlook / Microsoft 365 | Settings → Calendar → Shared calendars → Publish |

Treat these URLs as passwords — anyone holding one can read that calendar.

### Photos

Drop images into `photos/`. They're picked up within 15 minutes, no restart. Any
mix of JPEG, PNG, WebP, or AVIF works. Landscape images suit a landscape panel;
if you mount it portrait, portrait photos.

## Configuration

Everything lives in `config.yaml` — see `config.example.yaml`, which is commented
in full. The parts you're most likely to touch:

```yaml
timezone: America/New_York        # set this correctly; everything keys off it

display:
  default_mode: photos            # what it rests on: photos | calendar
  idle_timeout_seconds: 90        # how long after you leave before it drifts back
  photo_interval_seconds: 45
  quiet_hours: { enabled: true, start: "23:00", end: "06:30" }

presence:
  backend: http                   # none | http | gpio | serial
```

### Presence

How the panel knows you're standing there. Pick one:

| Backend | What it needs | Notes |
|---|---|---|
| `http` | Nothing | Touch wakes it; Home Assistant or any script can `POST /api/presence`. **Start here.** |
| `serial` | LD2410 mmWave, ~$8 | The good one — detects a *stationary* person, so it doesn't blank while you're reading it. |
| `gpio` | PIR sensor, ~$2 | Pi only. Motion only, so it blanks if you stand still. |
| `none` | Nothing | Calendar always on, no photo mode. |

Details, wiring, and a Home Assistant automation: [docs/hardware.md](docs/hardware.md).

## Hardware

Full bill of materials and the Pi-vs-mini-PC tradeoff:
[docs/hardware.md](docs/hardware.md). The short version — a 24" touch monitor, an
N100 mini PC or Pi 5, an LD2410 sensor, and a VESA mount, for roughly $400–600
depending mostly on the monitor.

## Auto-importing appointments from email and texts

Designed for, not built. The database, the API, and the UI already handle
imported events — they show up dashed and amber with "Needs confirming" until you
tap them — so the importer is a separate small program.

The design, including why iPhone texts are the hard case and what actually works
there, is in [docs/phase2-appointment-import.md](docs/phase2-appointment-import.md).

## Running it as a container instead

If the always-on box is elsewhere (a NAS, the Proxmox host) and the Pi is only
the screen:

```bash
docker compose up -d                          # on the server
WALLDISPLAY_URL=http://server:8080 ./setup/install.sh   # on the panel
```

Presence then has to be the `http` backend — a wired sensor only works on the
machine it's plugged into.

## Security

**There is no authentication.** Anyone who can reach port 8080 can read your
calendar and write events to it. That's a deliberate tradeoff for a device on
your home LAN, and it's what makes the Home Assistant integration trivial.

Do not port-forward it. If you need to reach it from outside the house, put it on
Tailscale or a WireGuard tunnel. If you build the email/SMS importer, read the
authentication section of that doc first.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp config.example.yaml config.yaml
.venv/bin/python -m uvicorn app.main:app --reload --port 8080
.venv/bin/python -m pytest tests/ -q
```

The frontend is plain HTML, CSS, and JavaScript with no build step — edit and
reload. That's deliberate: this runs unattended for months behind a monitor, and
a toolchain that needs updating is one more thing that can rot.

### Layout

```
app/
  main.py        FastAPI app, REST + WebSocket
  config.py      YAML config with defaults
  calendars.py   ICS fetching, recurrence expansion
  store.py       SQLite for locally-owned events
  presence.py    Presence backends (GPIO, mmWave serial, HTTP)
  state.py       photos ↔ calendar ↔ off state machine
web/             The kiosk page
setup/           Installer, systemd units, kiosk launcher
tests/           pytest suite
```

### API

| Endpoint | Purpose |
|---|---|
| `GET /api/events?days=14&back=1` | Merged feed + local events |
| `POST /api/events` | Add an event (see the phase-2 doc for the import fields) |
| `DELETE /api/events/{id}` | Delete a local event |
| `POST /api/events/{id}/confirm` | Confirm an imported event |
| `GET /api/state` · `POST /api/state/mode` | Read / override display mode |
| `POST /api/presence` | Report presence — any sensor, any script |
| `GET /api/photos` · `GET /api/settings` · `GET /api/health` | |
| `WS /ws` | Push: mode changes, calendar refreshes |

## Troubleshooting

**Calendar is empty.** `curl localhost:8080/api/health` — the `feeds` array gives
a per-feed error. Usually a placeholder URL still in `config.yaml`, or an iCloud
calendar that was never actually published.

**Times are wrong.** `timezone` in `config.yaml` is what the display uses; the OS
timezone is independent. Set both (`sudo timedatectl set-timezone ...`).

**Screen is blank on boot.** `journalctl -u walldisplay-kiosk -f`. Usually the
user isn't in the `video`/`input`/`render` groups — the installer adds them, but
group membership needs a reboot to take effect.

**Portrait mount is sideways.** Add `Environment=WALLDISPLAY_ROTATE=90` to
`/etc/systemd/system/walldisplay-kiosk.service`, then
`sudo systemctl daemon-reload && sudo systemctl restart walldisplay-kiosk`. Touch
input rotates separately — a libinput `CalibrationMatrix` quirk file for a 90°
rotation is `0 -1 1 1 0 0`.

**Presence never triggers.** `POST /api/presence` by hand first
(`curl -X POST localhost:8080/api/presence`) to confirm the panel side works,
then debug the sensor. For `serial`, check the user is in `dialout` and that
`/dev/ttyUSB0` is the right device.

**Photos don't appear.** Check the file extension is one of the supported ones and
that the `walldisplay` user can read them (`ls -l photos/`).
