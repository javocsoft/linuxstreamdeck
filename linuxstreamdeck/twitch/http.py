"""One place where this integration talks to the network.

Everything Twitch-facing goes through `request_json`, which is a module-level
function on purpose: a test replaces it and the whole integration runs with no
network at all, which is the only way an authorization flow and a token refresh
can be exercised repeatedly.

It uses the standard library, like the AI providers do, so Twitch support adds
no packaging dependency.
"""

from __future__ import annotations

import json
import logging
import socket
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)

REQUEST_TIMEOUT = 15.0
# Helix answers are small; a category search is the largest of them. The cap is
# a guard against a redirected or hostile endpoint, not a real expectation.
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
# A thumbnail of box art is a few kilobytes. The cap is what stops a wrong or
# redirected address from streaming something large into memory.
MAX_IMAGE_BYTES = 512 * 1024
# Where Twitch serves its own images. Anything else is refused, because these
# addresses come out of an API response rather than out of this application.
ASSET_HOSTS = ("jtvnw.net", "twitchcdn.net")


class TwitchError(Exception):
    """Anything that stopped a Twitch request from producing an answer."""


class TwitchHTTPError(TwitchError):
    """Twitch answered, and the answer was a refusal."""

    def __init__(self, status: int, message: str = "") -> None:
        self.status = status
        self.message = message
        detail = f": {message}" if message else ""
        super().__init__(f"Twitch returned HTTP {status}{detail}")


def request_json(
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    form: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    timeout: float = REQUEST_TIMEOUT,
) -> dict[str, Any]:
    """Perform one request and decode its JSON object.

    `form` sends url-encoded parameters, which the identity endpoints want;
    `body` sends JSON, which Helix wants. An empty response body decodes to an
    empty mapping rather than raising: several Helix endpoints answer 204.
    """
    target = f"{url}?{urlencode(params)}" if params else url
    data: bytes | None = None
    sent = dict(headers or {})
    if form is not None:
        data = urlencode(form).encode("utf-8")
        sent.setdefault("Content-Type", "application/x-www-form-urlencoded")
    elif body is not None:
        data = json.dumps(body, ensure_ascii=True).encode("utf-8")
        sent.setdefault("Content-Type", "application/json")
    request = Request(target, data=data, headers=sent, method=method.upper())
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        raise TwitchHTTPError(
            error.code, _error_message(error.read(64 * 1024))
        ) from error
    except (URLError, TimeoutError, socket.timeout) as error:
        raise TwitchError(
            "Could not reach Twitch; check the network connection"
        ) from error
    if len(raw) > MAX_RESPONSE_BYTES:
        raise TwitchError("The Twitch response was unexpectedly large")
    return _decode(raw)


def request_bytes(
    url: str,
    *,
    timeout: float = REQUEST_TIMEOUT,
    max_bytes: int = MAX_IMAGE_BYTES,
) -> bytes:
    """Fetch a binary asset, refusing anything not served by Twitch itself.

    The only thing this fetches is category box art, whose address arrives
    inside an API response. That makes it data from outside rather than a URL
    this application chose, so the host is checked before anything is opened:
    an answer that pointed somewhere else would otherwise have this reaching
    out to it.
    """
    if not _is_twitch_asset(url):
        raise TwitchError("Refusing to fetch an image from outside Twitch")
    request = Request(url, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read(max_bytes + 1)
    except HTTPError as error:
        raise TwitchHTTPError(error.code, "") from error
    except (URLError, TimeoutError, socket.timeout) as error:
        raise TwitchError("Could not reach the Twitch image server") from error
    if len(raw) > max_bytes:
        raise TwitchError("That Twitch image was unexpectedly large")
    return raw


def _is_twitch_asset(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme != "https" or not parts.hostname:
        return False
    host = parts.hostname.lower()
    return any(
        host == allowed or host.endswith(f".{allowed}")
        for allowed in ASSET_HOSTS
    )


def _decode(raw: bytes) -> dict[str, Any]:
    if not raw.strip():
        return {}
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TwitchError("Twitch returned an invalid response") from error
    if not isinstance(value, dict):
        raise TwitchError("Twitch returned an invalid response")
    return value


def _error_message(raw: bytes) -> str:
    """The readable part of a refusal.

    Twitch uses `message` on Helix and both `message` and `error` on the
    identity endpoints, and the device flow reports a pending authorization
    through this text rather than through a status code, so losing it would
    make the poll loop unable to tell "not yet" from "no".
    """
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ""
    if not isinstance(value, dict):
        return ""
    for field in ("message", "error_description", "error"):
        text = value.get(field)
        if isinstance(text, str) and text.strip():
            return " ".join(text.split())[:300]
    return ""
