# Checking on the panel from somewhere else

The point of this page is that whoever looks after the person using the display
can answer three questions from their phone, without phoning to ask:

- Is it still working?
- Does she know about Thursday's appointment?
- Can I send the photos from Sunday?

Open `http://<panel-ip>:8080/remote`.

## Set an access code first

Until `remote.token` is set, the remote page is refused from every device except
the panel itself. That's deliberate — a wall display with no keyboard has to be
usable before it can be locked down, but it shouldn't be reachable from a
neighbour's phone in the meantime.

On the device:

```bash
cd ~/jarvis-test-sandbox/wall-calendar
python3 -c "import secrets; print(secrets.token_urlsafe(24))"
nano config.yaml          # paste it into remote.token
sudo systemctl restart walldisplay
```

Then open `/remote` on a phone and paste the code once; it's kept in that
browser. To share with a sibling, send them the code — or a link with it
embedded (`http://…/remote?token=…`), which the page strips from the URL bar
after storing it.

`Sign out` on the page forgets the code on that device. To revoke everyone,
change `remote.token` and restart.

## Reaching it from outside the house

**Do not forward a port to it.** The panel holds a calendar full of medical
appointments and someone's home address, and a token on a plain HTTP connection
is not enough protection on the open internet.

**Tailscale** is the right answer here. It's free for personal use, puts the
panel and your phone on a private network, and needs no router configuration:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --ssh
sudo tailscale set --auto-update
```

Then the panel is reachable at `http://<machine-name>:8080/remote` from any
device signed into your tailnet, from anywhere. Use **Share** in the Tailscale
admin console to give a sibling access to just that one machine without adding
them to everything else.

`--ssh` is worth having: if the panel ever needs attention, you can get a shell
without asking someone to plug in a keyboard.

WireGuard directly is equivalent if you already run it. A Cloudflare Tunnel also
works but puts a third party in front of the medical calendar — Tailscale keeps
the traffic between your own devices.

## What the page shows

| | Means |
|---|---|
| **Display** | Showing photos, showing calendar, or off for quiet hours |
| **Someone nearby** | Whether presence has fired recently — a quick sign of life |
| **Calendars** | `n syncing`, or a warning if a feed is failing or serving a saved copy |
| **Photos / storage / disk free** | Whether the card is filling up |
| **Running for** | Time since the service last restarted — a number that keeps resetting means it's crashing |

"Showing a saved copy" is the one worth understanding: the panel keeps the last
good download of each calendar on disk, so it carries on displaying appointments
through an internet outage. The wall looks normal; only this page tells you the
feed has gone stale. If it persists for more than a day, the feed URL has
probably been revoked — regenerate it in Google or iCloud and update
`config.yaml`.

## Sending photos

**Add photos** takes a batch straight from a phone's camera roll. HEIC from an
iPhone is converted, rotation is applied, and anything huge is scaled down to
2560px on the long edge — a 12-megapixel photo would otherwise make the panel
work hard every time the slideshow ticks.

Bad files in a batch are skipped and named rather than failing the whole upload,
so twenty holiday photos with one screenshot of a PDF in the middle still get
nineteen photos onto the wall.

The panel picks up new photos immediately — no restart, and no need to be
standing in front of it.

## Adding an appointment

Appointments added here appear on the panel straight away, labelled so they're
distinguishable from the synced calendars. They live in the panel's own database
and don't write back to Google or iCloud.

If you'd rather everything came from one place, add it to the shared Google or
iCloud calendar instead and let it sync — the panel picks it up on the next
refresh, within 15 minutes.

## When something looks wrong

**Page won't load at all.** The device is off, off the network, or the service
has stopped. If you have Tailscale SSH: `sudo systemctl status walldisplay`.

**"Access code rejected".** The token in `config.yaml` changed, or you're typing
a different one. There's no lockout — try again.

**Calendars showing a saved copy.** See above; usually a revoked feed URL.

**Disk free getting low.** Photos are the only thing that grows. Delete some
from the grid; the × on each thumbnail removes it from the display immediately.
