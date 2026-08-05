# Hardware

## The short version

| Part | What to get | Rough cost |
|---|---|---|
| Compute | Intel N100 mini PC **or** Raspberry Pi 5 (4GB) | $130–170 / $60–80 |
| Display | 24" or 27" touch monitor, or a non-touch monitor + IR touch overlay | $200–400 |
| Presence sensor | HLK-LD2410C mmWave module + USB-TTL adapter | $8–15 |
| Mounting | VESA low-profile wall mount, short right-angle HDMI + power cables | $30–60 |
| Power | One recessed outlet behind the panel, or in-wall rated extension | $20–40 |

Total lands around **$400–600** depending mostly on the monitor.

## Mini PC stick vs Raspberry Pi 5

Both run this fine. The differences that actually matter on a wall:

**Intel N100 mini PC** — the safer pick.
- Chromium compositing is smooth at 1080p and fine at 1440p. Photo crossfades never stutter.
- x86 means every package just works, including anything you add later.
- Usually fanless in this class, but check — a fan on a hallway wall is audible at night.
- Draws ~6–10W idle.

**Raspberry Pi 5** — cheaper, smaller, more fiddling.
- 4GB is enough; the 8GB model buys you nothing here.
- Comfortable at 1080p. At 1440p+ the Ken Burns photo animation can judder — set
  `photo_interval_seconds` higher or drop the animation if it bothers you.
- **Use an NVMe HAT or a good A2 SD card.** Cheap SD cards are the single most
  common cause of a wall panel that dies after eight months.
- Active cooler recommended; it's near-silent and the Pi 5 throttles without one.
- Draws ~4–7W idle.

Avoid the Pi 4 for this unless you already own one — browser compositing with a
full-screen photo slideshow is where it runs out of headroom.

### Stick PCs specifically
The HDMI-stick form factor (Atom/Celeron, 2–4GB, eMMC) is tempting for the
cabling but tends to be underpowered and thermally throttled in a wall cavity.
A small N100 box VESA-mounted behind the monitor is the better version of the
same idea.

## Display

**Touch monitor.** Look for one that reports as a USB HID touch device — those
work with zero configuration on both platforms. Capacitive is nicer to use;
infrared is cheaper and works through a glass cover, but picks up dust and
misfires more.

**Cheaper route:** a normal monitor plus a stick-on IR touch frame. Also HID,
also driverless, roughly $60–100 for a 24".

**Portrait mounting** suits an agenda-heavy calendar well — the Today view stacks
into a single column automatically. Set `WALLDISPLAY_ROTATE=90` (or 270) in
`/etc/systemd/system/walldisplay-kiosk.service` and rotate the touch input to
match; see the README's troubleshooting section.

**Brightness** matters more than resolution. A hallway panel at full brightness
is unpleasant at night — most monitors let you set a low default in their OSD,
and quiet hours handle the rest.

## Presence sensor

**HLK-LD2410C (mmWave), ~$8.** The right choice. It detects a stationary person,
so the calendar doesn't blank while you're standing there reading it — the exact
failure mode that makes PIR-based panels annoying. Wire TX/RX/5V/GND to a
USB-TTL adapter, plug it into the Pi or the mini PC, set:

```yaml
presence:
  backend: serial
  serial: { port: /dev/ttyUSB0, baud: 256000 }
```

Mount it near the screen edge with a clear line of sight, not behind metal. The
default sensitivity picks up someone at 3–4m; the vendor's Windows tool or the
`ld2410` Python package can narrow it if it triggers on hallway traffic.

**PIR (HC-SR501), ~$2.** Only detects motion, so it drops you back to photos
while you stand still. Fine if the panel is somewhere you pass rather than
linger. Pi only (GPIO):

```yaml
presence:
  backend: gpio
  gpio: { pin: 17 }
```

**No sensor at all.** Set `backend: http` and let touch wake it — tapping the
screen switches to the calendar, and it drifts back to photos when you leave.
This is a perfectly reasonable place to start; add a sensor later if the tapping
gets old.

**Home Assistant.** If you already have a presence sensor in that room, skip the
hardware entirely — keep `backend: http` and add an automation:

```yaml
automation:
  - alias: Wall panel presence
    trigger:
      - platform: state
        entity_id: binary_sensor.hallway_motion
        to: "on"
    action:
      - service: rest_command.wallpanel_presence

rest_command:
  wallpanel_presence:
    url: "http://192.168.1.50:8080/api/presence"
    method: POST
    content_type: application/json
    payload: '{"source": "home-assistant"}'
```

## Wiring and mounting notes

- Get **one** cable run to the panel if you can. A recessed outlet behind the
  monitor plus a short right-angle HDMI keeps the mount flush.
- USB from the monitor (touch) to the compute box is easy to forget — a touch
  monitor needs both HDMI *and* USB.
- Leave the compute box accessible. VESA-sandwiched between monitor and wall
  looks great until you need the USB port.
- If it's on Wi-Fi, check signal at the mounting spot before you drill. A wall
  panel that drops off nightly is worse than no panel.
