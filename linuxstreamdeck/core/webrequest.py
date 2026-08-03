"""HTTP requests from a key, and the value one can show.

This is the widest single action in the catalogue: an endpoint plus a value to
read out of its answer covers home automation bridges, dashboards, webhooks,
CI servers and anything else that speaks REST. It uses the standard library,
like the AI providers and Twitch do, so it adds no packaging dependency.

Two rules shape everything here.

**A key that shows a value must never fetch it while being drawn.**
`feedback()` runs on the render worker, so a request there would hold that
worker for the latency of the internet on every repaint -- the same reason
`TwitchClient.channel()` performs no request. `cached_value()` therefore
answers from `_VALUES` and schedules its own refresh; `_pending` stops a burst
of renders from starting the same fetch several times over.

**A response is data from outside.** It is capped before it is read, parsed
defensively, and reduced to one short string. Nothing here evaluates it, and
the action is excluded from the catalogue offered to an AI provider, exactly
as `sys.command` and `obs.raw` are.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)

REQUEST_TIMEOUT = 15.0
# A response is data from outside, so it is bounded before it is read rather
# than trusted to be small. Generous enough for any dashboard payload.
MAX_RESPONSE_BYTES = 1024 * 1024
# Only these can be reached. A key is configured by hand, so this is not a
# sandbox -- it is what stops a typo or a paste from opening a local file.
ALLOWED_SCHEMES = ("http", "https")
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
# Methods that carry a body. A GET with one is legal and almost always a
# mistake, so the editor's body field simply does not apply to them.
BODY_METHODS = ("POST", "PUT", "PATCH")
# What a key can show, at most. `compose()` fits centered text to the key
# width, so anything longer is drawn too small to read anyway.
MAX_VALUE_CHARS = 12
# How long a fetched value stays worth showing. Past this the key blanks
# rather than showing a number that stopped being true -- the same rule the
# Twitch snapshot follows.
VALUE_STALE_SECONDS = 90.0


class WebRequestError(Exception):
    """Anything that stopped a request from producing an answer."""


# ---------- the request itself ----------

def check_url(url: str) -> str:
    """The URL to call, or raise saying what is wrong with it."""
    address = str(url or "").strip()
    if not address:
        raise WebRequestError("This key has no URL to call.")
    parts = urlsplit(address)
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise WebRequestError(
            f"Only {' and '.join(ALLOWED_SCHEMES)} addresses can be called."
        )
    if not parts.netloc:
        raise WebRequestError("That URL names no server.")
    return address


def parse_headers(text: str) -> dict[str, str]:
    """`Name: value` per line, blanks and comments ignored.

    Deliberately forgiving: a header list is typed by hand, and a stray blank
    line or a trailing colon should not stop the key from working.
    """
    headers: dict[str, str] = {}
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        name, _, value = line.partition(":")
        name = name.strip()
        if name:
            headers[name] = value.strip()
    return headers


def method_for(value: str) -> str:
    method = str(value or "GET").strip().upper()
    return method if method in METHODS else "GET"


def request(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: str = "",
    timeout: float = REQUEST_TIMEOUT,
    *,
    opener=urlopen,
) -> tuple[int, str]:
    """Perform one request and return `(status, text)`.

    `opener` is injected so every test runs with no network at all.
    """
    address = check_url(url)
    verb = method_for(method)
    payload = None
    sending = dict(headers or {})
    if verb in BODY_METHODS and str(body or "").strip():
        payload = str(body).encode("utf-8")
        # Only when the user did not say otherwise: a form post or a plain
        # text body is just as valid as JSON.
        if not any(name.lower() == "content-type" for name in sending):
            sending["Content-Type"] = "application/json"
    call = Request(address, data=payload, headers=sending, method=verb)
    try:
        with opener(call, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            status = int(getattr(response, "status", 200) or 200)
    except HTTPError as error:
        # An error status is still an answer, and its body usually says what
        # was wrong. Reporting only the number would hide that.
        detail = _short(_read_error(error))
        raise WebRequestError(
            f"The server answered HTTP {error.code}"
            + (f": {detail}" if detail else "")
        ) from error
    except URLError as error:
        raise WebRequestError(f"Could not reach the server: {error.reason}") from error
    except (OSError, ValueError) as error:
        raise WebRequestError(f"The request failed: {error}") from error
    if len(raw) > MAX_RESPONSE_BYTES:
        raise WebRequestError("The response was too large to read.")
    return status, raw.decode("utf-8", "replace")


def fetch_bytes(url: str, limit: int, *, opener=urlopen) -> bytes | None:
    """Download something binary, bounded, or None.

    Separate from `request()` because an image is not text and must not be
    decoded as any. The cap is passed in rather than fixed here: a cover and a
    dashboard payload have nothing to do with each other.
    """
    address = check_url(url)
    try:
        with opener(Request(address), timeout=REQUEST_TIMEOUT) as response:
            raw = response.read(limit + 1)
    except (HTTPError, URLError, OSError, ValueError) as error:
        log.debug("Could not fetch %s: %s", address, error)
        return None
    return None if len(raw) > limit else raw


def _read_error(error: HTTPError) -> str:
    try:
        return (error.read() or b"").decode("utf-8", "replace")
    except Exception:
        return ""


def _short(text: str, limit: int = 120) -> str:
    flat = " ".join(str(text or "").split())
    return flat[: limit - 1] + "…" if len(flat) > limit else flat


# ---------- picking one value out of the answer ----------

def extract(text: str, path: str) -> str:
    """The value at a dotted path, as something short enough for a key.

    An empty path means the whole body, which is right for an endpoint that
    answers a bare number. Numeric segments index a list, so `data.0.name`
    works without a second syntax for it. Anything that does not resolve
    answers "" rather than raising: a key showing nothing is a far better
    failure than a key that goes red every second.
    """
    body = str(text or "").strip()
    steps = [step for step in str(path or "").strip().split(".") if step]
    if not steps:
        return _value_text(body)
    try:
        value = json.loads(body)
    except (TypeError, ValueError):
        return ""
    for step in steps:
        if isinstance(value, dict):
            if step not in value:
                return ""
            value = value[step]
        elif isinstance(value, list):
            try:
                value = value[int(step)]
            except (IndexError, ValueError):
                return ""
        else:
            return ""
    return _value_text(value)


def _value_text(value) -> str:
    if isinstance(value, bool):
        # Python spells these True/False; every API that produced them spells
        # them the other way, and the key should agree with the API.
        return "true" if value else "false"
    if value is None:
        return ""
    if isinstance(value, float):
        # A JSON number is a float even when it is whole, and "12" reads far
        # better on a key than "12.0".
        text = f"{value:.2f}".rstrip("0").rstrip(".")
    elif isinstance(value, (dict, list)):
        # A whole object cannot be shown on a key, and truncating its JSON
        # would only ever be noise.
        return ""
    else:
        text = str(value)
    return " ".join(text.split())[:MAX_VALUE_CHARS]


# ---------- values shown on a key, fetched off the render worker ----------

_lock = threading.Lock()
_values: dict[tuple, tuple[float, str]] = {}
_pending: set[tuple] = set()
_executor: ThreadPoolExecutor | None = None
_stopped = False


def cached_value(
    params: dict, min_age: float, now: float | None = None
) -> str:
    """What this key should show, refreshing in the background if it is due.

    Never performs a request: this is called from `feedback()`, on the render
    worker. `min_age` is what stops a burst of repaints -- an `obs.state`
    storm, a page change -- from becoming a burst of requests: the value is
    only asked for again once it is older than the interval the key chose.

    A value past `VALUE_STALE_SECONDS` is dropped rather than shown, so a
    sustained outage blanks the key instead of leaving a number that stopped
    being true, while a brief one keeps the last good value. That is the same
    pair of bounds the Twitch channel snapshot uses, for the same reason.
    """
    signature = _signature(params)
    moment = time.monotonic() if now is None else now
    with _lock:
        fetched, value = _values.get(signature, (0.0, ""))
        age = moment - fetched
        fresh = bool(value) and age <= VALUE_STALE_SECONDS
        due = age >= max(0.0, min_age)
        start = due and signature not in _pending
        if start:
            _pending.add(signature)
    if start:
        _submit(signature, dict(params))
    return value if fresh else ""


def remember(params: dict, text: str, now: float | None = None) -> str:
    """Store the value carried by an answer this key has just received.

    A press is the freshest reading the key will get, so it replaces the
    cached one instead of leaving the display a refresh behind what was
    plainly just done.
    """
    value = extract(text, (params or {}).get("value_path", ""))
    signature = _signature(params)
    with _lock:
        _values[signature] = (time.monotonic() if now is None else now, value)
    return value


def _signature(params: dict) -> tuple:
    p = params or {}
    return (
        str(p.get("url") or ""),
        method_for(p.get("method")),
        str(p.get("headers") or ""),
        str(p.get("body") or ""),
        str(p.get("value_path") or ""),
    )


def background(work, *args) -> bool:
    """Run a short network job off whichever thread asked for it.

    Public because anything drawing a key has the same problem this module
    solves: `feedback()` runs on the single render worker, so a round trip
    there stalls every other key for the latency of the network. One executor
    serves them all, and `shutdown()` already stops it in the documented order
    -- a second one would be a second thing to remember to stop.

    Answers whether the work was accepted, so a caller can release whatever
    marks it set when it was not.
    """
    global _executor
    with _lock:
        if _stopped:
            return False
        if _executor is None:
            _executor = ThreadPoolExecutor(
                max_workers=2, thread_name_prefix="web-request"
            )
        executor = _executor
    try:
        executor.submit(work, *args)
    except RuntimeError:
        return False
    return True


def _submit(signature: tuple, params: dict) -> None:
    if not background(_fetch, signature, params):
        with _lock:
            _pending.discard(signature)


def _fetch(signature: tuple, params: dict) -> None:
    try:
        _status, text = request(
            params.get("url", ""),
            params.get("method", "GET"),
            parse_headers(params.get("headers", "")),
            params.get("body", ""),
        )
        value = extract(text, params.get("value_path", ""))
    except WebRequestError as error:
        # Keep serving the last good value; the staleness bound is what
        # eventually blanks the key. A live value is not worth a red key on
        # every tick of a flaky connection.
        log.debug("Web request for a key value failed: %s", error)
        value = None
    except Exception:
        log.warning("Unexpected failure fetching a key value", exc_info=True)
        value = None
    with _lock:
        if value is not None:
            _values[signature] = (time.monotonic(), value)
        _pending.discard(signature)


def forget_values() -> None:
    """Drop everything known about key values, for a replaced configuration.

    The pending marks go too. A fetch still in flight belongs to a key that
    may no longer exist, and leaving its mark behind would stop whatever takes
    that key's place from ever asking. The worst that follows is one duplicate
    request; the answer lands under its own signature either way.
    """
    with _lock:
        _values.clear()
        _pending.clear()


def shutdown() -> None:
    """Stop the background fetches. Called from application shutdown."""
    global _executor, _stopped
    with _lock:
        _stopped = True
        executor, _executor = _executor, None
    if executor is not None:
        executor.shutdown(wait=False)
