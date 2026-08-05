# Phase 2 — pulling appointments out of email and texts

Not built yet. The panel is designed for it, though: the database, the API, and
the UI already handle imported-but-unconfirmed events, so the importer is a
separate small program that talks to the same `POST /api/events` endpoint.

Read the "Confirmation" section before anything else. It's the part that decides
whether this feature is useful or actively harmful.

## The shape of it

```
Gmail (IMAP poll) ─┐
                   ├─→ importer ─→ Claude extracts ─→ POST /api/events
iPhone (Shortcut) ─┘                                  (confirmed: false)
                                                            │
                                                            ▼
                                              panel shows it dashed, amber
                                              "Needs confirming — tap to review"
                                                            │
                                                    you tap Confirm
                                                            ▼
                                                   normal event
```

## Confirmation: why imported events start unconfirmed

An LLM misreading "reschedule from Tuesday to Thursday" and silently writing the
wrong date into your medical calendar is worse than having no automation at all —
you'd trust the panel and miss the appointment. So imported events land with
`confirmed: false`, and the panel renders them with a dashed border and an amber
"Needs confirming" flag until you tap Confirm.

This is already implemented end to end. The importer just has to set the flag:

```json
POST /api/events
{
  "title": "MRI — knee",
  "start": "2026-08-12T10:30:00",
  "end":   "2026-08-12T11:30:00",
  "location": "Radiology, 2nd floor",
  "calendar": "From email",
  "color": "#c98bdb",
  "source": "email",
  "sourceRef": "gmail:18f2a9c4b7e1",
  "confirmed": false
}
```

`sourceRef` is the dedupe key — a unique index enforces it in SQLite, so
re-polling the same message returns `{"status": "duplicate"}` instead of adding a
second copy. Use a stable per-message identifier: the Gmail message ID, or a hash
of the SMS body plus sender.

## Before you build this: the server has no authentication

`POST /api/events` is unauthenticated. That's fine for a panel on your own LAN,
and it's what makes the Home Assistant presence integration a two-line
automation. It is **not** fine to expose to the internet so a phone Shortcut can
reach it — anyone who finds the URL can write to your calendar.

Two workable options, in order of preference:

1. **Tailscale (or WireGuard) on the panel.** Your phone and the panel join the
   same private network; the Shortcut posts to the panel's Tailscale IP. Nothing
   is exposed publicly. This is the right answer for a household.
2. **Add a shared-secret header** and put a reverse proxy with TLS in front. If
   you go this route, add the check in `app/main.py` before anything else — a
   dependency that compares a header against a value in `config.yaml` using
   `hmac.compare_digest`, applied to the write endpoints.

Do not skip this step and port-forward 8080.

## Gmail

The clean path, and worth doing first — most appointment confirmations arrive by
email anyway, including the ones your doctor's office sends after a phone call.

**Filter first, extract second.** Do not feed your whole inbox to an LLM: it's
slow, it costs real money, and it sends far more of your mail to a third party
than the task needs. Create a Gmail filter that labels likely appointment mail
(`from:(*@*health* OR *@*clinic* OR *@*dental*) OR subject:(appointment OR
"your visit" OR reschedule)`) and have the importer poll only that label.

Auth: an [app password](https://myaccount.google.com/apppasswords) with plain
IMAP is the least moving parts. The Gmail API with OAuth is more robust long-term
but needs a consent flow and token refresh — not worth it for one mailbox.

```python
import imaplib, email
from email.header import decode_header

with imaplib.IMAP4_SSL("imap.gmail.com") as M:
    M.login(ADDRESS, APP_PASSWORD)
    M.select('"Appointments"')          # the label your filter applies
    _, ids = M.search(None, "UNSEEN")
    for msg_id in ids[0].split():
        _, data = M.fetch(msg_id, "(RFC822)")
        message = email.message_from_bytes(data[0][1])
        ...  # hand the body to the extractor below
```

Poll every 10–15 minutes from a systemd timer. There's no need for push.

## iPhone texts

This is the hard one, and it's worth being clear about why: **iOS does not let
any app read your messages in the background.** There is no API, no entitlement,
and no workaround. Anything claiming otherwise is either wrong or is describing
an Android feature.

What actually works, best first:

**1. A Shortcuts personal automation.** Settings → Shortcuts → Automation → New →
Message. Trigger on messages containing "appointment", "confirmed", "scheduled",
or on messages from specific senders (your clinic's number). Since iOS 17 these
can run without a confirmation tap. The action is `Get Contents of URL`:

- URL: `http://<panel-tailscale-ip>:8081/import/sms`
- Method: POST
- Request Body: JSON → `text` = Shortcut Input, `sender` = Sender

This is genuinely hands-off once configured, and it's the only iOS option that
is. The catch is that it's per-trigger — you're choosing keywords up front rather
than scanning everything.

**2. A Share Sheet shortcut.** One tap from the Messages app: select the text,
Share, tap "Add to Wall Calendar". Manual, but zero setup fragility and it
handles the messages your keywords missed.

**3. Forward to a dedicated email address.** Falls back to the Gmail path above,
which you've already built. Slowest, most reliable.

**Android, for contrast:** if anyone in the house is on Android, a MacroDroid or
Tasker profile can forward every incoming SMS to the importer with no keyword
filter and no user interaction. If the appointment texts land on an Android
phone, use that phone.

## Extraction

Give the model the message and a schema; let it decide whether there's an
appointment in there at all. The `is_appointment` field matters as much as the
date fields — most of what you feed it will be marketing.

```python
from datetime import date
from anthropic import Anthropic
from pydantic import BaseModel, Field

client = Anthropic()


class Appointment(BaseModel):
    is_appointment: bool = Field(
        description="True only for a specific scheduled appointment with a date. "
                    "False for marketing, reminders about past visits, or requests to call and book."
    )
    title: str = Field(description='Short, e.g. "Dr. Patel — follow-up". Empty if not an appointment.')
    start: str = Field(description="ISO 8601 local time, e.g. 2026-08-12T10:30:00. Empty if unknown.")
    duration_minutes: int = Field(description="Stated duration, or 60 if not stated.")
    location: str = Field(description="Address or room. Empty if not stated.")
    confidence: float = Field(description="0.0-1.0. Below 0.8 means a human should check the date.")
    notes: str = Field(description="Anything the person needs to know: fasting, arrive early, bring ID.")


PROMPT = """Extract appointment details from this message.

Today is {today}. Resolve relative dates ("next Tuesday", "tomorrow") against it.
If the message gives a date without a year, choose the next occurrence.
If no specific date and time is given, set is_appointment to false — a message
asking someone to call and book is not an appointment.

From: {sender}
Subject: {subject}

{body}"""


def extract(sender: str, subject: str, body: str) -> Appointment:
    response = client.messages.parse(
        model="claude-opus-5",
        max_tokens=2000,
        messages=[{
            "role": "user",
            "content": PROMPT.format(
                today=date.today().isoformat(), sender=sender, subject=subject, body=body[:8000],
            ),
        }],
        output_format=Appointment,
    )
    return response.parsed_output
```

`messages.parse()` validates the response against the schema, so `parsed_output`
is a real `Appointment` — no JSON parsing, no retry loop for malformed output.

Then gate on the result before posting:

```python
result = extract(sender, subject, body)
if result.is_appointment and result.start and result.confidence >= 0.5:
    post_event(result, source_ref=f"gmail:{message_id}", confirmed=False)
```

Everything imported stays unconfirmed regardless of confidence — confidence only
decides whether it's worth showing you at all. Below 0.5 it's noise; log it and
drop it.

**Cost.** A typical appointment email is ~1.5K tokens in and ~200 out. On
`claude-opus-5` ($5 / $25 per million) that's about **1.3¢ per message**. At a
dozen filtered messages a week it's a few cents a month — the Gmail filter is
what keeps it there, not the model choice. If you end up feeding it much more
volume, `claude-haiku-4-5` ($1 / $5) does simple extraction well at about a fifth
the price; date arithmetic on vague phrasing is where you'd notice the
difference.

**Privacy.** Message bodies go to Anthropic's API. That's a real consideration
for medical mail — the filter is doing double duty here, keeping both cost and
exposure down. If you'd rather nothing left the house, a local model via Ollama
can do this, but on a Pi or an N100 with no GPU, extraction quality drops enough
that you'd be confirming almost everything by hand. At that point the Share Sheet
shortcut is the better trade.

## Build order

1. **The importer skeleton** — a `POST /import/sms` endpoint and an IMAP poller,
   both writing to a log file instead of the calendar. Run it for a week and read
   the log. You'll learn what your actual appointment mail looks like, and your
   Gmail filter will be wrong in ways you can't predict from here.
2. **Add extraction**, still logging rather than posting. Check the dates it
   produces against the messages by hand.
3. **Start posting** with `confirmed: false`. Live with it for a few weeks.
4. **Only then** consider auto-confirming high-confidence events from senders
   you've confirmed correctly many times before — and keep medical appointments
   manual regardless.

Steps 1 and 2 are not optional busywork. Extraction quality on your real mail is
the thing that determines whether this feature is worth having, and you cannot
find that out without looking at your real mail.
