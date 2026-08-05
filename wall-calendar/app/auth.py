"""Access control for the remote (caregiver) surface.

The panel itself runs on the device and talks to 127.0.0.1, so it is always
trusted. Anything arriving over the network needs the token once a token is
configured -- which is what makes it safe to reach the device from a phone
over Tailscale without exposing the calendar to the whole LAN.

With no token set the server behaves as it always did: open on the LAN, no
remote surface. That is a deliberate default for a device with no keyboard --
somebody has to be able to get it working before they can lock it down.
"""

from __future__ import annotations

import hmac
import ipaddress

from fastapi import HTTPException, Request

TOKEN_HEADER = "x-wall-token"
MIN_TOKEN_LENGTH = 16


def is_loopback(request: Request) -> bool:
    host = request.client.host if request.client else None
    if host is None:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


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

    def __call__(self, request: Request) -> None:
        if not self.enabled:
            if self.remote_only and not is_loopback(request):
                raise HTTPException(
                    status_code=503,
                    detail="Remote access is off. Set remote.token in config.yaml.",
                )
            return
        if is_loopback(request):
            return
        supplied = presented_token(request)
        # compare_digest on every path, so a wrong token and a missing one take
        # the same time to reject.
        if supplied is None or not hmac.compare_digest(supplied, self.token):
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
