# Running the server on Railway

Railway builds the `Dockerfile` in this folder and gives the calendar a public
HTTPS URL. No tunnel, no Proxmox, no port forwarding — but read the trade-offs
first, because they land on the person using the panel, not on you.

## What you give up

**The wall stops working when your home internet does.** Today the server runs
in the house: feeds are cached on disk, so a broken connection still leaves a
panel showing today's appointments. With the server on Railway, the panel is
just a browser pointing at the internet — lose the line and the wall goes
blank. For someone who relies on it to know when the doctor is, that is the
whole point of the thing failing.

**Appointments and photos live on someone else's disk.** Medical appointments,
a home address, family pictures. Railway is a reputable host, but it is a third
party, and this is the part that is hard to undo once uploaded.

**It costs money continuously** — a small always-on service plus a volume.

A middle path that keeps both: run the server at home as usual, and use
Railway only if you want the caregiver page reachable without a tunnel. If you
do put the panel on Railway, keep the panel's own device on wired ethernet, so
the connection is at least as reliable as the house allows.

## Setup

### 1. Point Railway at the repo

New project → Deploy from GitHub repo. Railway picks up `railway.json` and
`Dockerfile` from the repository root and needs nothing else.

If this project sits inside a larger repo rather than at its own root, set
**Settings → Source → Root Directory** to the folder holding this file's
parent (e.g. `wall-calendar`), or Railway will try to build the repo root and
fail to find the Dockerfile.

`railway.json` sets the
healthcheck to `/api/health`, which is unauthenticated on purpose — it reports
liveness and never content.

### 2. A volume, or you will lose the photos

**Railway wipes the container filesystem on every deploy.** Without a volume,
every uploaded photo and every appointment you typed in disappears the next
time you push a commit. This is the step people skip.

Service → **Variables → Volumes → New Volume**, mount path `/data`. Then set:

```
WALLDISPLAY_DATA_DIR=/data
WALLDISPLAY_PHOTO_DIR=/data/photos
```

Both live under the one volume because Railway allows one volume per service.
`walldisplay.db` (your typed appointments, medication ticks) and the feed cache
land in `/data`; photos in `/data/photos`. Directories are created on boot.

Keep **replicas at 1**. SQLite and a volume both assume a single writer, and
`railway.json` pins it there.

### 3. An access code — the app will not start without one

Railway sets `RAILWAY_ENVIRONMENT`, which the app reads as "this URL is the
front door". Two things change automatically:

- Loopback is no longer trusted. Railway's router reaches the app over
  loopback, so trusting it would give every visitor the panel's own
  unauthenticated access — the same trap as a Cloudflare Tunnel, described in
  [public-access.md](public-access.md).
- Starting without an access code becomes a **hard failure**, not a warning.
  On a LAN, no token means "no ceremony needed"; on a public URL the identical
  behaviour publishes the medical appointments and hands strangers a delete
  button.

Generate one and set it:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(24))"
```

```
WALLDISPLAY_TOKEN=<that string>
```

If the deploy crash-loops, read the logs — it says exactly this in plain words.

### 4. The rest of the config

There is no `config.yaml` on Railway. Either set individual variables:

| Variable | Effect |
|---|---|
| `TZ` or `WALLDISPLAY_TIMEZONE` | e.g. `America/New_York` — set this, times are wrong otherwise |
| `WALLDISPLAY_TOKEN` | access code (required here) |
| `WALLDISPLAY_DATA_DIR` | database + feed cache |
| `WALLDISPLAY_PHOTO_DIR` | uploaded photos |
| `WALLDISPLAY_DEVICE_NAME` | title on the caregiver page |
| `WALLDISPLAY_THEME` | `light` (default) or `dark` |
| `WALLDISPLAY_TEXT_SCALE` | e.g. `1.2` for larger type |
| `WALLDISPLAY_DEFAULT_MODE` | `photos` or `calendar` |
| `ANTHROPIC_API_KEY` | appointment extraction, if the importer is on |
| `PORT` | set by Railway; do not set it yourself |

…or paste an entire config as one variable, `WALLDISPLAY_CONFIG_YAML`, which is
the only way to configure lists like calendar feeds, birthdays and medications:

```yaml
timezone: America/New_York
calendars:
  - {name: Work, url: "https://example.com/work.ics"}
  - {name: Medical, url: "https://example.com/medical.ics", color: "#b02a45"}
birthdays:
  - {name: Margaret, date: 1954-03-12}
medications:
  - {name: Morning pills, times: ["08:00"], notes: With food}
```

Precedence, weakest first: defaults → `config.yaml` (absent here) →
`WALLDISPLAY_CONFIG_YAML` → individual variables. So a single variable can
override one line of the blob without retyping it.

### 5. Presence

`presence.backend` must stay `http`. A mmWave or PIR sensor is wired to the
device on the wall, which is no longer where the server runs — that device
POSTs to `/api/presence` with the access code instead. `serial` and `gpio` are
meaningless in a container and will simply never fire.

### 6. Point the panel at it

Service → **Settings → Networking → Generate Domain**, then on the panel:

```ini
Environment=WALLDISPLAY_URL=https://your-app.up.railway.app/?token=YOUR_TOKEN
```

The page stores the code and strips it from the URL. The panel needs the token
because on Railway nothing is local — including the panel.

## Checking it

The same external check works, since it only cares what the internet sees:

```bash
./setup/verify-public-access.sh your-app.up.railway.app <ACCESS_CODE>
```

It will report the access code as the only thing standing in front of the app.
That is true, and it is the trade Railway makes: `harden-for-public.sh` and the
tunnel scripts do not apply here, because Railway is the tunnel.

If you want per-person access that can be revoked — worth it for a calendar
holding medical appointments — put a custom domain on Cloudflare, proxy it to
the Railway URL, and add a Cloudflare Access policy in front. Steps 3 and 4 of
[public-access.md](public-access.md) apply unchanged from there.

## Things that will bite

**App sleeping.** If your plan sleeps idle services, the first request after a
quiet spell waits for a cold start. On a wall panel that reads as the calendar
being broken. Turn sleeping off for this service.

**Redeploys drop the WebSocket.** The panel falls back to polling `/api/state`
every 10 seconds and reconnects on its own, so a deploy shows up as a brief
pause rather than a dead screen.

**Volume backups are yours to arrange.** Railway does not snapshot volumes on
the lower plans. The database is one SQLite file; copy it somewhere
occasionally if the typed-in appointments matter.
