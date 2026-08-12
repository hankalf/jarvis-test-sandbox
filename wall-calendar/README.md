# Wall Calendar Display

A touch-screen wall panel that shows appointments in large, plain type, and
sits as a digital picture frame when nobody's near it. Walk up, it switches to
the calendar. Walk away, it goes back to photos.

Designed to be readable by an older person from across a room, and checkable by
family from a phone. Family can see that it's working and send photos to it
without touching the device.

Everything runs **on the device** — a Raspberry Pi 5 or a small x86 mini PC.
No cloud service, no account, no subscription. It reads your existing calendars
over their standard iCal feeds and keeps a local copy, so it keeps working
through an internet outage.

## Designed for older eyes

The defaults are not a typical dashboard, on purpose:

- **Light theme.** Thin light-on-dark text haloes badly for aging eyes,
  especially after cataract surgery. Dark is available if the room is dim.
- **Large type everywhere**, scaled by one number in the config
  (`text_scale: 1.5` makes the whole panel half again bigger). Nothing on the
  panel is smaller than about 1rem, and secondary "dim" text still clears
  WCAG AAA contrast rather than fading to grey.
- **The day of the week in large type.** For someone whose days run together
  this is often the single most useful thing on the wall.
- **One thing at a time.** The default screen answers "what's next?" with a
  single big card, then today and tomorrow underneath. Not a grid.
- **A one-screen mode.** `show_week_month: false` removes the Week and Month
  buttons entirely, leaving nothing to navigate and no way to get lost.
- **Plain words** — "Happening now", "in about 2 hours", "Nothing planned
  today" — instead of icons and abbreviations.
- **Optional reduced motion**, if the slow photo pan is distracting.

## What it does

- **Today view** — a big "what's next" card, then the rest of today and
  tomorrow. This is what you see when you walk up to it.
- **Week view** — a proper timed grid, laid out so a week of shifts is readable
  from across the room. Overlapping events sit side by side.
- **Month view** — the whole month; tap a day to jump to it.
- **Photo frame** — full-bleed slideshow with a slow pan, a drifting clock, and
  your next appointment in the corner.
- **Add appointments on the panel** — big touch targets, native date and time
  pickers.
- **Quiet hours** — blanks overnight, but presence still wakes it.
- **Remote page for family** — check it's alive, see what's coming up, send
  photos, add an appointment, all from a phone.
- **Keeps working offline** — the last good copy of each calendar is saved to
  disk, so a power cut plus a dead router doesn't leave a blank wall.
- **Photo captions** — "Margaret's graduation, June 2019" beside the clock,
  which turns the frame from wallpaper into something that prompts a memory.
- **Time to get ready** — shortly before an appointment with somewhere to be,
  the whole screen becomes that one thing.
- **Medication reminders** — doses appear with a big *Mark as taken* button;
  family can see from their phone whether today's were ticked off.
- **Birthdays** — listed in `config.yaml`, shown every year with the age.

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

Two ways in:

- **From a phone or laptop** — open `http://<its-ip>:8080/remote`, tap
  **Add photos**. iPhone HEIC is fine; everything is rotated upright, resized,
  and converted on the way in. This is the way family should use.
- **Straight onto the device** — drop files into `photos/`. Picked up within
  15 minutes, no restart.

Landscape images suit a landscape panel; if you mount it portrait, portrait
photos.

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

accessibility:
  theme: light                    # light | dark
  text_scale: 1.0                 # 1.25 or 1.5 if they squint
  simple_view: true               # the big "what's next" screen
  show_week_month: true           # false = one screen, no navigation
  reduce_motion: false

remote:
  token: ""                       # REQUIRED for phone access; see below
  device_name: "Mom's Wall Calendar"

presence:
  backend: http                   # none | http | gpio | serial
```

Changes take effect on `sudo systemctl restart walldisplay`.

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

## Photo captions, medication, birthdays

All optional, all off until you configure them.

**Captions** are typed under each photo on the remote page and shown beside the
clock in picture-frame mode.

**Birthdays** go in `config.yaml` and recur every year; give a full year of
birth and it shows the age ("Margaret turns 72"), or just `MM-DD` for the name
alone. 29 February falls back to the 28th in non-leap years rather than
disappearing for three years at a time.

```yaml
birthdays:
  - {name: Margaret, date: "1954-03-12"}
  - {name: Tom, date: "06-04", note: "Ring him in the morning"}

medications:
  - {name: "Morning pills", times: ["08:00"], notes: "With food"}
  - {name: "Blood pressure tablet", times: ["08:00", "20:00"]}
```

**Medication** doses appear on the panel with a large *Mark as taken* button,
and the remote page summarises the day: how many were ticked off, how many are
past their time, when the next one is due.

> This is a memory aid, not a medical device. Nothing alarms, escalates, or
> notifies anyone. An untouched tick means *nobody pressed the button*, which
> is not the same as *the medicine wasn't taken* — don't build a care plan on
> the assumption that it is.

**Time to get ready** takes over the screen before an appointment that has a
location, or a dose that's due — `display.leaving_soon` in `config.yaml`. The
chime is off by default; a wall that unexpectedly makes a noise is startling in
a way a changed picture isn't, and it can't be silenced from across the room.

## Work schedules

**Add a work schedule** on the remote page takes a shift pattern — pick the
days, a start time, a length, how many weeks — and puts every shift on the
panel in one go. Each shift is an ordinary event afterwards: a swapped day can
be deleted on its own ("Delete this one"), or the whole pattern at once
("Delete all repeats") when the roster changes.

## Auto-importing appointments from email and texts

Built, ships disabled. Enable `importer:` in `config.yaml` and it will pull
appointments out of a labelled Gmail folder and out of texts forwarded by an
iPhone Shortcut, using Claude to do the reading. Everything imported shows up
dashed and amber with "Needs confirming" until a person taps Confirm — an
extractor misreading a reschedule and silently writing a wrong date into a
medical calendar would be worse than no automation at all.

It starts in `dry_run` mode: a week of watching what it *would* have added,
logged to `data/import-log.jsonl`, before you let it touch the calendar. Setup,
the iPhone Shortcut recipe, and why texts are the hard case:
[docs/phase2-appointment-import.md](docs/phase2-appointment-import.md).

## Running it as a container instead

If the always-on box is elsewhere (a NAS, the Proxmox host) and the Pi is only
the screen:

```bash
docker compose up -d                          # on the server
WALLDISPLAY_URL=http://server:8080 ./setup/install.sh   # on the panel
```

Presence then has to be the `http` backend — a wired sensor only works on the
machine it's plugged into.

## Checking on it remotely

`http://<its-ip>:8080/remote` is a phone-shaped page for whoever looks after the
person using the panel. It shows whether the display is awake, whether the
calendars are still syncing, how much disk is left, what's coming up, and lets
you send photos or add an appointment.

Set an access code first — without one the page is refused from every device
except the panel itself:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(24))"   # paste into remote.token
sudo systemctl restart walldisplay
```

Two ways to reach it from outside the house, neither involving a port forward:

- **Tailscale** — simplest, private, but everyone needs the app installed.
  [docs/remote-access.md](docs/remote-access.md).
- **A custom URL like `calendar.yourdomain.com`, no VPN app** — a Cloudflare
  Tunnel with Cloudflare Access in front. [docs/public-access.md](docs/public-access.md).
- **Hosted, no hardware at all** — the server can run on Railway instead of in
  the house. [docs/railway.md](docs/railway.md). Note the trade: the panel then
  depends on your home internet being up, where today it does not.

If you publish it publicly, read the first section of that second document:
a tunnel connector talks to the app from 127.0.0.1, and the panel trusts
127.0.0.1, so `remote.trust_loopback: false` is not optional there. Four
scripts do the setup:

```bash
./setup/deploy-tunnel-lxc.sh                      # Proxmox host: connector container
./setup/install-tunnel.sh <TUNNEL_TOKEN>          # in that container: cloudflared
sudo ./setup/harden-for-public.sh                 # calendar host: closes the loopback hole
./setup/verify-public-access.sh calendar.example.com <CODE>   # from outside your network
```

Run the third one before you publish the hostname, and the fourth from a phone
on mobile data — it's the only check that sees what the internet sees.

## Security

The panel's own browser is trusted because it connects over loopback — touching
the screen is already physical access. Everything arriving over the network is
checked against `remote.token`:

| | Without a token set | With a token set |
|---|---|---|
| Reading the calendar over the LAN | allowed | allowed |
| Adding/deleting events over the LAN | allowed | needs the token |
| `/remote`, photo upload/delete, status | device only | needs the token |
| On the panel itself | always allowed | always allowed |

Reads stay open either way, so a Home Assistant dashboard or a spare tablet can
show the same calendar without credentials. **Set a token if anyone but you is
on the network**, and don't port-forward the panel — use Tailscale.

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
  auth.py        Token guard (loopback is always trusted)
  config.py      YAML config with defaults
  calendars.py   ICS fetching, recurrence expansion, on-disk cache
  photos.py      Upload normalising: EXIF rotation, resize, HEIC
  store.py       SQLite for locally-owned events
  presence.py    Presence backends (GPIO, mmWave serial, HTTP)
  state.py       photos ↔ calendar ↔ off state machine
web/             The kiosk page (index) and the remote page (remote)
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
| `POST /api/photos` | Upload photos (multipart, batch) — token required |
| `DELETE /api/photos/{name}` | Remove a photo — token required |
| `GET /api/status` | Everything the remote page shows — token required |
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
