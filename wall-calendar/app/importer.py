"""Phase 2: appointments out of email and forwarded texts.

Two ways in, one pipeline:

  - `POST /api/import/message` -- an iPhone Shortcut, an Android automation, or
    anything else forwards a message body here.
  - The IMAP poller -- watches one labelled Gmail folder, so only mail a filter
    has already flagged as appointment-ish ever reaches the extractor.

The pipeline hands the text to Claude with a schema, gates on the result, and
writes the event with `confirmed: false` -- it renders dashed and amber on the
panel until a person taps Confirm. An extractor mis-reading a reschedule and
silently writing a wrong date into a medical calendar is worse than no
automation, so nothing imported is ever trusted automatically.

Ships with `dry_run: true`: everything is parsed and logged to
data/import-log.jsonl, nothing is written to the calendar. Run it that way for
a week, read the log, then flip the flag. Extraction quality on *your* mail is
what decides whether this feature is worth having.
"""

from __future__ import annotations

import asyncio
import email
import email.header
import hashlib
import imaplib
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)

MAX_BODY_CHARS = 8000

EXTRACTION_PROMPT = """Extract appointment details from this message.

Today is {today} ({weekday}). Resolve relative dates ("next Tuesday",
"tomorrow") against it. If the message gives a date without a year, choose the
next occurrence. If no specific date and time is given, set is_appointment to
false -- a message asking someone to call and book is not an appointment.

From: {sender}
Subject: {subject}

{body}"""


class Extractor(Protocol):
    def extract(self, *, sender: str, subject: str, body: str, today: datetime) -> dict: ...


class ClaudeExtractor:
    """Extraction via the Claude API with a validated schema.

    Imported lazily so the panel runs fine without the `anthropic` package or
    an API key -- the importer just reports itself unavailable.
    """

    def __init__(self, model: str, api_key: str = "") -> None:
        self.model = model
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._client = None

    @property
    def available(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, "no API key (set importer.api_key or ANTHROPIC_API_KEY)"
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False, "the anthropic package is not installed (pip install anthropic)"
        return True, ""

    def _schema(self):
        from pydantic import BaseModel, Field

        class Appointment(BaseModel):
            is_appointment: bool = Field(
                description="True only for a specific scheduled appointment with a "
                "date. False for marketing, reminders about past visits, or "
                "requests to call and book."
            )
            title: str = Field(
                description='Short, e.g. "Dr. Patel - follow-up". Empty if not an appointment.'
            )
            start: str = Field(
                description="ISO 8601 local time, e.g. 2026-08-12T10:30:00. Empty if unknown."
            )
            duration_minutes: int = Field(description="Stated duration, or 60 if not stated.")
            location: str = Field(description="Address or room. Empty if not stated.")
            confidence: float = Field(
                description="0.0-1.0. Below 0.8 means a person should check the date."
            )
            notes: str = Field(
                description="Anything the person needs to know: fasting, arrive early, bring ID."
            )

        return Appointment

    def extract(self, *, sender: str, subject: str, body: str, today: datetime) -> dict:
        import anthropic

        if self._client is None:
            self._client = anthropic.Anthropic(api_key=self.api_key)

        response = self._client.messages.parse(
            model=self.model,
            max_tokens=2000,
            messages=[{
                "role": "user",
                "content": EXTRACTION_PROMPT.format(
                    today=today.date().isoformat(),
                    weekday=today.strftime("%A"),
                    sender=sender or "(unknown)",
                    subject=subject or "(none)",
                    body=body[:MAX_BODY_CHARS],
                ),
            }],
            output_format=self._schema(),
        )
        return response.parsed_output.model_dump()


class ImporterManager:
    def __init__(self, config, store, tz, broadcast) -> None:
        self.config = dict(config)
        self.store = store
        self.tz = tz
        self.broadcast = broadcast
        self.extractor: Extractor = ClaudeExtractor(
            model=self.config.get("model", "claude-opus-5"),
            api_key=self.config.get("api_key", ""),
        )
        self.log_path = Path(self.config["_log_path"])
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------- lifecycle

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled", False))

    @property
    def dry_run(self) -> bool:
        return bool(self.config.get("dry_run", True))

    def describe(self) -> dict:
        """For /api/status, so the remote page can say why nothing imports."""
        available, why = getattr(self.extractor, "available", (True, ""))
        gmail = self.config.get("gmail", {})
        return {
            "enabled": self.enabled,
            "dryRun": self.dry_run,
            "extractorReady": available,
            "extractorProblem": why or None,
            "watchingMailbox": bool(gmail.get("username") and gmail.get("app_password")),
        }

    async def start(self) -> None:
        gmail = self.config.get("gmail", {})
        if self.enabled and gmail.get("username") and gmail.get("app_password"):
            self._task = asyncio.create_task(self._poll_loop(), name="importer-imap")
            log.info("importer: watching %s for %s", gmail.get("folder"), gmail["username"])
        elif self.enabled:
            log.info("importer: enabled, message endpoint only (no mailbox configured)")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # -------------------------------------------------------------- pipeline

    async def process(
        self, *, text: str, sender: str = "", subject: str = "", ref: str | None = None
    ) -> dict:
        """One message through extract -> gate -> (maybe) write."""
        if not self.enabled:
            return {"action": "disabled"}

        available, why = getattr(self.extractor, "available", (True, ""))
        if not available:
            return {"action": "unavailable", "reason": why}

        # A stable ref makes re-sending the same message a no-op. Callers that
        # have a real one (an IMAP message id) pass it; otherwise hash content.
        if not ref:
            ref = "hash:" + hashlib.sha1(f"{sender}|{text}".encode()).hexdigest()[:24]

        now = datetime.now(self.tz)
        try:
            extraction = await asyncio.to_thread(
                self.extractor.extract, sender=sender, subject=subject, body=text, today=now
            )
        except Exception as exc:
            log.warning("importer: extraction failed for %s: %s", ref, exc)
            self._log(ref, sender, subject, {"error": str(exc)}, "error")
            return {"action": "error", "reason": str(exc)}

        result = self._gate(extraction, ref=ref, sender=sender)
        self._log(ref, sender, subject, extraction, result["action"])
        return result

    def _gate(self, extraction: dict, *, ref: str, sender: str) -> dict:
        min_confidence = float(self.config.get("min_confidence", 0.5))

        if not extraction.get("is_appointment"):
            return {"action": "skipped", "reason": "not an appointment", "extraction": extraction}
        if float(extraction.get("confidence", 0)) < min_confidence:
            return {"action": "skipped", "reason": "low confidence", "extraction": extraction}

        try:
            start = datetime.fromisoformat(extraction["start"])
        except (KeyError, ValueError, TypeError):
            return {"action": "skipped", "reason": "no usable date", "extraction": extraction}
        if start.tzinfo is None:
            start = start.replace(tzinfo=self.tz)
        minutes = max(5, min(24 * 60, int(extraction.get("duration_minutes") or 60)))

        if self.dry_run:
            return {"action": "dry_run", "extraction": extraction}

        event = self.store.add_event(
            title=(extraction.get("title") or "Appointment")[:200],
            start=start,
            end=start + timedelta(minutes=minutes),
            location=(extraction.get("location") or None),
            notes=(extraction.get("notes") or None),
            calendar=self.config.get("calendar", "From email"),
            color=self.config.get("color", "#7a3fa0"),
            source="import",
            source_ref=ref,
            confirmed=False,  # never trusted until a person taps Confirm
        )
        if event is None:
            return {"action": "duplicate", "extraction": extraction}

        self.broadcast({"type": "events-changed"})
        log.info("importer: added %r from %s (unconfirmed)", event["title"], sender or ref)
        return {"action": "imported", "event": event, "extraction": extraction}

    def _log(self, ref: str, sender: str, subject: str, extraction: dict, action: str) -> None:
        """Append-only JSONL, the review surface for the dry-run period."""
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a") as f:
                f.write(json.dumps({
                    "at": datetime.now(timezone.utc).isoformat(),
                    "ref": ref,
                    "sender": sender,
                    "subject": subject,
                    "action": action,
                    "extraction": extraction,
                }) + "\n")
        except OSError as exc:
            log.warning("importer: could not write log: %s", exc)

    # ------------------------------------------------------------------ IMAP

    async def _poll_loop(self) -> None:
        minutes = max(2, int(self.config.get("gmail", {}).get("poll_minutes", 10)))
        while True:
            try:
                handled = await asyncio.to_thread(self._poll_once)
                if handled:
                    log.info("importer: processed %d message(s) from mailbox", handled)
            except Exception as exc:
                log.warning("importer: mailbox poll failed: %s", exc)
            await asyncio.sleep(minutes * 60)

    def _poll_once(self) -> int:
        gmail = self.config["gmail"]
        handled = 0
        with imaplib.IMAP4_SSL(gmail.get("host", "imap.gmail.com")) as mail:
            mail.login(gmail["username"], gmail["app_password"])
            status, _ = mail.select(f'"{gmail.get("folder", "Appointments")}"')
            if status != "OK":
                raise RuntimeError(f"folder {gmail.get('folder')!r} not found")
            _, ids = mail.search(None, "UNSEEN")
            for msg_id in ids[0].split():
                _, data = mail.fetch(msg_id, "(RFC822)")
                message = email.message_from_bytes(data[0][1])
                ref = "imap:" + hashlib.sha1(
                    (message.get("Message-ID") or msg_id.decode()).encode()
                ).hexdigest()[:24]
                # Blocking sub-call is fine: we're already in a worker thread.
                asyncio.run_coroutine_threadsafe(
                    self.process(
                        text=_body_text(message),
                        sender=_decode(message.get("From", "")),
                        subject=_decode(message.get("Subject", "")),
                        ref=ref,
                    ),
                    self._loop,
                ).result(timeout=120)
                handled += 1
        return handled

    async def start_with_loop(self) -> None:
        self._loop = asyncio.get_running_loop()
        await self.start()


def _decode(raw: str) -> str:
    parts = email.header.decode_header(raw or "")
    return "".join(
        p.decode(enc or "utf-8", "replace") if isinstance(p, bytes) else p for p, enc in parts
    )


def _body_text(message: email.message.Message) -> str:
    """The text/plain part, or a crude strip of the HTML if that's all there is."""
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode(part.get_content_charset() or "utf-8", "replace")
    payload = message.get_payload(decode=True)
    if not payload:
        return ""
    text = payload.decode(message.get_content_charset() or "utf-8", "replace")
    if message.get_content_type() == "text/html":
        import re

        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text)
    return text.strip()
