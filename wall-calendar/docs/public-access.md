# A custom URL, reachable from anywhere, without a VPN

Yes — `calendar.yourdomain.com` in any browser, no VPN app, no port forwarding,
no exposing your home IP. A **Cloudflare Tunnel** makes an outbound-only
connection from a container on your Proxmox host to Cloudflare, and Cloudflare
publishes the hostname. Nothing needs to be open on your router.

The important part is what goes *in front of it*. Read the next section before
the setup.

## Read this first: the tunnel breaks the app's own auth

The panel's browser skips the access code because it connects from 127.0.0.1 —
touching the screen is already physical access. **A tunnel connector also
connects to 127.0.0.1.** Left alone, that means every request arriving from the
internet looks local, and the token is never asked for.

The app now detects this: any request carrying a proxy header
(`X-Forwarded-For`, `CF-Connecting-IP`, and similar) is refused the loopback
shortcut and must present the token. Belt and braces, turn the shortcut off
entirely when you publish:

```yaml
remote:
  trust_loopback: false     # the panel is on this host, so nothing is "local"
```

With `trust_loopback: false`, **the panel's own browser needs the token too**.
Point the kiosk at `http://127.0.0.1:8080/?token=YOUR_TOKEN` once — it stores
the code and strips it from the URL. In
`/etc/systemd/system/walldisplay-kiosk.service`:

```ini
Environment=WALLDISPLAY_URL=http://127.0.0.1:8080/?token=YOUR_TOKEN
```

`setup/harden-for-public.sh` does both of those and then verifies the result —
step 5 below. Read this section anyway, so you know what it's protecting you
from if it ever needs undoing.

## Don't rely on the access code alone

The token is a single shared string, it appears in URLs you send people, and
there is no way to revoke one person's copy. That is fine on a LAN. On a public
hostname holding medical appointments, a home address, and family photos, it is
the only thing between the internet and all of it.

So put **Cloudflare Access** in front. It's free for up to 50 users, and it
means the app isn't publicly reachable at all — Cloudflare demands a login at
the edge and only then forwards the request. A stranger who finds the URL gets
a login page, not your calendar.

That gives you two independent layers: Access decides *who gets to the app*, the
token decides *what may write to it*. Either alone is weaker than both.

## Setup

Four scripts do the parts that can be scripted. The two dashboard steps (3 and
4) can't be — they're clicks in Cloudflare's UI.

| | Where you run it | What it does |
|---|---|---|
| `setup/deploy-tunnel-lxc.sh` | Proxmox host | Creates the connector's container |
| `setup/install-tunnel.sh` | Inside that container | Installs and registers `cloudflared` |
| `setup/harden-for-public.sh` | Calendar host | Closes the loopback hole |
| `setup/verify-public-access.sh` | Anywhere outside your network | Checks what the internet can see |

Run them in that order. `harden-for-public.sh` is the one that matters most —
without it the tunnel publishes an app that never asks for the token.

### 1. A tunnel container on Proxmox

The script runs **on the Proxmox host**, as root, in an interactive shell — it
asks before every change, so it needs a terminal to ask on. Either the web UI
shell (node → Shell) or `ssh -t` works; `ssh host 'bash -s' < script` does not,
and the script says so rather than half-running.

```bash
scp -r wall-calendar/setup root@192.168.14.100:/root/wc-setup
ssh -t root@192.168.14.100 'bash /root/wc-setup/deploy-tunnel-lxc.sh'
```

It runs the pre-flight checks first (storage pool, bridge, free CTID, Debian 12
template), prints the plan, and shows you every `pct` command for approval
before it runs — per the house rules in `CLAUDE.md`. It picks the first free
CTID from 200 and refuses to reuse an existing one. Override anything with
environment variables:

```bash
CTID=205 STORAGE=local-zfs BRIDGE=vmbr1 ./setup/deploy-tunnel-lxc.sh
```

An unprivileged LXC is the right call here: the connector needs no special
capabilities and no inbound ports (it dials out), and keeping it separate from
the calendar means a problem in one isn't automatically a problem in the other.

If you'd rather not have another container, `install-tunnel.sh` runs fine
directly on the box that runs the calendar — skip this step.

### 2. Create the tunnel

In the Cloudflare dashboard: **Zero Trust → Networks → Tunnels → Create a
tunnel**, choose *Cloudflared*, name it. It shows you an install command
containing a long token starting `eyJ`. You want the token, not the command:

```bash
# From the calendar checkout, on the Proxmox host:
pct push <CTID> setup/install-tunnel.sh /root/install-tunnel.sh
pct exec <CTID> -- bash /root/install-tunnel.sh 'eyJ...'
```

That installs `cloudflared` from Cloudflare's own repository, registers the
connector as a service, and confirms it came up. Re-running it with a different
token repoints the connector — that's how you rotate one.

Then add a **public hostname** on the tunnel:

| Field | Value |
|---|---|
| Subdomain | `calendar` |
| Domain | your domain |
| Service type | `HTTP` |
| URL | `192.168.1.x:8080` (the calendar's LAN address, or `localhost:8080` if same box) |

Your domain has to be on Cloudflare (nameservers pointed at them). A `.com` is
about $10/year; Cloudflare's own registrar sells at cost.

HTTPS is handled at the edge automatically — you get a valid certificate with
nothing to renew.

### 3. Put Access in front — do not skip this

**Zero Trust → Access → Applications → Add an application → Self-hosted.**

| Field | Value |
|---|---|
| Application domain | `calendar.yourdomain.com` |
| Session duration | 1 month (so family aren't re-logging-in constantly) |

Add a policy: **Allow**, with rule type **Emails** and the addresses of the two
or three people who should have access. They get a one-time PIN by email the
first time on each device, then nothing for a month. Google/Apple sign-in works
too if everyone already has one.

Removing someone later is deleting their email from the policy — which is the
thing a shared token can never do.

### 4. Bypass Access for the phone Shortcut only

Cloudflare Access will block the iPhone Shortcut that forwards appointment texts
(step 2 of the phase-2 importer), because a Shortcut can't do an interactive
login. Two options:

- **Service token** (better): Zero Trust → Access → Service Auth → create a
  token, then add a policy of action **Service Auth** for the path
  `/api/import/message`. The Shortcut sends `CF-Access-Client-Id` and
  `CF-Access-Client-Secret` headers alongside `X-Wall-Token`.
- **Bypass that one path**: a policy with action **Bypass** on
  `/api/import/message`. Simpler, but then that endpoint is protected by the
  wall-calendar token alone.

### 5. Harden the calendar host

This is the step that closes the loopback hole described at the top. On the
machine running the calendar:

```bash
sudo ./setup/harden-for-public.sh
```

It sets `remote.trust_loopback: false`, generates a `remote.token` if there
isn't a usable one, repoints the kiosk at `?token=…` so the panel still works
after, restarts the services, and then proves the fix by making the exact
request a tunnel makes — loopback peer, `X-Forwarded-For` header — and failing
loudly if that doesn't come back `401`. It prints the access code at the end;
that's what the remote page, the Shortcut, and the panel all need.

Config edits are made in place and keep their comments, so the file stays
readable afterwards.

### 6. Check it from outside

From a phone on mobile data, or anywhere that isn't your own network:

```bash
./setup/verify-public-access.sh calendar.yourdomain.com <ACCESS_CODE>
```

It checks the hostname resolves over HTTPS, that an **unauthenticated request
does not return your calendar**, whether Access is actually challenging, that
the code still works through the tunnel, and that the security headers survived
the proxy. Exit code is non-zero if anything is exposed.

Running it from inside the house proves nothing — you may be reaching the app
directly, not through Cloudflare.

By eye, the same check:

```
https://calendar.yourdomain.com/remote
```

Cloudflare login, then the access-code gate, then the page. If you get the page
without either, stop and re-run step 5.

## The honest comparison

| | Custom domain | VPN app needed | Auth | Home IP exposed |
|---|---|---|---|---|
| **Cloudflare Tunnel + Access** | yes | no | Cloudflare login + token | no |
| Cloudflare Tunnel alone | yes | no | **token only** | no |
| Tailscale (what this repo defaulted to) | no (`*.ts.net`) | yes | device-level | no |
| Tailscale Funnel | no | no | **token only** | no |
| Port forward + DuckDNS + Caddy | yes | no | token only | **yes** |

Tunnel **+ Access** is the only row that gets you a custom URL, no VPN, and real
per-person authentication. Tunnel alone gets you the first two and leaves the
medical calendar behind one shared string — workable, but know that's the trade.

Port forwarding is the one to avoid. It publishes your home IP address and puts
the app directly in the path of everything that scans the internet all day.

## Things worth knowing

**Cloudflare terminates TLS.** They can see the traffic — that's how the free
tunnel works. For a family calendar that's a normal trade; if it isn't
acceptable to you, Tailscale keeps traffic between your own devices and is the
better fit.

**The token stays useful.** Access controls the door; the token still gates
writes, and it's what the Shortcut and any automation authenticate with.

**Rotating access:** change `remote.token` and restart to invalidate every
saved code at once. Remove an email from the Access policy to cut off one
person without disturbing anyone else.

**Rate limiting** is built in — ten bad codes from one address within five
minutes gets a 429 for a while. That's damping, not a defence; Access is the
defence.

**Uptime:** the tunnel is outbound, so it survives a changing home IP address
and needs no dynamic DNS. If the Proxmox host reboots, make sure the LXC is set
to start on boot, or the URL goes dark until you notice.
