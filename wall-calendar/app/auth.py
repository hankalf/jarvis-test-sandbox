"""Access control for the remote (caregiver) surface.

The panel itself runs on the device and talks to 127.0.0.1, so it is always
trusted. Anything arriving over the network needs the token once a token is
configured.

That loopback trust is safe for a panel on a LAN, and dangerous the moment
anything proxies to the app -- a tunnel connector, a reverse proxy, Nginx --
because those connect to 127.0.0.1 too, so requests from the whole internet
would arrive looking local. Loopback is therefore only trusted when the
request carries no proxy-forwarding headers, and `remote.trust_loopback: false`
turns it off outright for anyone who would rather not rely on that inference.
"""

from __future__ import annotations

import hmac
import ipaddress
import logging
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

log = logging.getLogger(__name__)

TOKEN_HEADER = "x-wall-token"
MIN_TOKEN_LENGTH = 16

# Any of these means something forwarded the request on someone else's behalf,
# so the peer address is the proxy's, not the caller's.
PROXY_HEADERS = (
    "x-forwarded-for",
    "x-real-ip",
    "forwarded",
    "cf-connecting-ip",       # Cloudflare Tunnel / proxied DNS
    "x-forwarded-host",
    "cf-ray",
)

# Brute-force damping. Small numbers on purpose: a household has a handful of
# devices, and a wrong code is a typo, not a campaign.
MAX_FAILURES = 10
FAILURE_WINDOW_SECONDS = 300
_failures: dict[str, deque] = defaultdict(deque)


def via_proxy(request: Request) -> bool:
    return any(header in request.headers for header in PROXY_HEADERS)


def is_loopback(request: Request) -> bool:
    """True only for a genuinely local caller.

    A proxied request also arrives from 127.0.0.1, so the forwarding headers
    are what separates "the panel's own browser" from "the internet, via a
    tunnel that happens to terminate here".
    """
    if via_proxy(request):
        return False
    host = request.client.host if request.client else None
    if host is None:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def client_label(request: Request) -> str:
    """Best-effort caller identity for rate limiting and logs."""
    for header in ("cf-connecting-ip", "x-real-ip"):
        value = request.headers.get(header)
        if value:
            return value.strip()[:64]
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # Left-most entry is the original client; the rest are proxies.
        return forwarded.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "unknown")[:64]


def presented_token(request: Request) -> str | None:
    header = request.headers.get(TOKEN_HEADER)
    if header:
        return header
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:]
    # Query parameter exists so a caregiver can open a bookmarked link on a
    # phone; the page immediately stores it and strips it from the URL.
    return request.query_params.get("token")


def _note_failure(who: str) -> int:
    now = time.monotonic()
    attempts = _failures[who]
    while attempts and now - attempts[0] > FAILURE_WINDOW_SECONDS:
        attempts.popleft()
    attempts.append(now)
    return len(attempts)


def _too_many_failures(who: str) -> bool:
    now = time.monotonic()
    attempts = _failures.get(who)
    if not attempts:
        return False
    while attempts and now - attempts[0] > FAILURE_WINDOW_SECONDS:
        attempts.popleft()
    return len(attempts) >= MAX_FAILURES


def reset_failures() -> None:
    """Test hook -- the counters are process-global."""
    _failures.clear()


class TokenGuard:
    """Callable dependency. `remote_only=True` marks endpoints that exist purely
    for the caregiver surface -- those stay closed when remote access is off,
    rather than falling open."""

    def __init__(self, config, *, remote_only: bool = False) -> None:
        self.config = config
        self.remote_only = remote_only

    @property
    def token(self) -> str:
        return str(self.config.remote.get("token") or "")

    @property
    def enabled(self) -> bool:
        return bool(self.config.remote.get("enabled", True)) and bool(self.token)

    @property
    def trust_loopback(self) -> bool:
        return bool(self.config.remote.get("trust_loopback", True))

    def _local(self, request: Request) -> bool:
        return self.trust_loopback and is_loopback(request)

    def __call__(self, request: Request) -> None:
        if not self.enabled:
            if self.remote_only and not self._local(request):
                raise HTTPException(
                    status_code=503,
                    detail="Remote access is off. Set remote.token in config.yaml.",
                )
            return
        if self._local(request):
            return

        who = client_label(request)
        if _too_many_failures(who):
            raise HTTPException(
                status_code=429,
                detail="Too many bad access codes. Wait a few minutes.",
            )

        supplied = presented_token(request)
        # compare_digest on every path, so a wrong token and a missing one take
        # the same time to reject.
        if supplied is None or not hmac.compare_digest(supplied, self.token):
            count = _note_failure(who)
            if count in (1, MAX_FAILURES):
                log.warning("rejected access code from %s (%d in window)", who, count)
            raise HTTPException(status_code=401, detail="Bad or missing token")


def token_warnings(config) -> list[str]:
    """Surfaced on the status endpoint so the caregiver page can nag."""
    warnings = []
    token = str(config.remote.get("token") or "")
    if not config.remote.get("enabled", True):
        return ["Remote access is disabled in config.yaml."]
    if not token:
        warnings.append(
            "No remote token set -- anyone on the network can read and change "
            "the calendar. Set remote.token in config.yaml."
        )
    elif len(token) < MIN_TOKEN_LENGTH:
        warnings.append(
            f"Remote token is shorter than {MIN_TOKEN_LENGTH} characters. "
            "Use a long random string."
        )
    return warnings
