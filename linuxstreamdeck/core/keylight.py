"""Elgato Key Light, Key Light Air and Ring Light, over the local network.

The other half of the Elgato desk that has no Linux software at all. The lights
run an unauthenticated HTTP server on port 9123 and answer `GET`/`PUT` on
`/elgato/lights`, so nothing here needs an account, a cloud service or a
vendor library -- just the address, which `discover()` finds over mDNS.

Two things about the protocol are worth knowing before changing anything:

**Temperature is in mireds, not kelvin.** The device takes 143 to 344, which
is `1000000 / kelvin`, so a *higher* number is a *warmer* light. Nobody thinks
in mireds, so the actions speak kelvin and this module converts. Getting the
direction backwards is the easy mistake, and it is silent.

**A `PUT` replaces the fields it names and nothing else**, so a brightness
change must not resend a stale power state. `apply()` therefore sends only
what it was asked to change.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import threading
import time

from . import webrequest

from . import host

log = logging.getLogger(__name__)

PORT = 9123
LIGHTS_PATH = "/elgato/lights"
INFO_PATH = "/elgato/accessory-info"
# A light is on the same network; anything slower than this is a light that is
# unplugged, and a key must not hang waiting to find that out.
TIMEOUT = 3.0

# What Elgato's own firmware accepts.
MIN_BRIGHTNESS = 0
MAX_BRIGHTNESS = 100
MIN_KELVIN = 2900
MAX_KELVIN = 7000
KELVIN_STEP = 100
# The device's own unit at those two ends. Written down rather than derived
# from the kelvin pair: 1000000/7000 is 142.86 and the firmware's real floor is
# 143, so a formula is off by one at exactly the end a "coolest" key lands on.
MIN_MIRED = 143
MAX_MIRED = 344

# mDNS service the lights publish themselves under.
SERVICE = "_elg._tcp"
DISCOVERY_TOOL = "avahi-browse"
# `avahi-browse -t` dumps the daemon's cache and exits; measured at almost
# exactly one second whether or not anything answers.
DISCOVERY_TIMEOUT = 6.0
# How long a discovered list is reused. The editor fills its dropdown on the
# GTK thread, so without this every rebuild of the row would cost that second.
DISCOVERY_TTL = 30.0

# How long a light's own state is reused. Read from `feedback()`, which runs on
# the render worker, so it is never fetched there -- see `cached_state`.
STATE_TTL = 5.0
STATE_STALE = 60.0

MISSING_DISCOVERY = (
    "Install avahi-utils to find Key Lights automatically, or type the "
    "address of the light"
)


class KeyLightError(Exception):
    """Anything that stopped a light from being reached or changed."""


class Light:
    """One light found on the network."""

    def __init__(self, name: str, host: str, address: str, port: int) -> None:
        self.name = name
        self.host = host
        self.address = address
        self.port = port

    @property
    def target(self) -> str:
        """What a key stores.

        The mDNS host name rather than the address: a light picks up a new
        address from DHCP often enough, and the name it publishes does not
        change. `avahi` resolving it is guaranteed here, because finding the
        light in the first place is what proves avahi is running.
        """
        return self.host or self.address

    def __repr__(self) -> str:  # pragma: no cover - debugging only
        return f"<Light {self.name!r} at {self.target}>"


# ---------- units ----------

def kelvin_to_device(kelvin: int) -> int:
    """Kelvin as the firmware wants it, clamped to what it accepts."""
    wanted = max(MIN_KELVIN, min(MAX_KELVIN, int(kelvin)))
    return max(MIN_MIRED, min(MAX_MIRED, round(1000000 / wanted)))


def device_to_kelvin(value: int) -> int:
    """The firmware's number back as kelvin, rounded to something readable."""
    mired = max(MIN_MIRED, min(MAX_MIRED, int(value)))
    kelvin = round(1000000 / mired / KELVIN_STEP) * KELVIN_STEP
    return max(MIN_KELVIN, min(MAX_KELVIN, kelvin))


# ---------- talking to a light ----------

def _url(target: str, path: str = LIGHTS_PATH) -> str:
    host = str(target or "").strip()
    if not host:
        raise KeyLightError("This key has no light chosen.")
    if "://" in host:
        raise KeyLightError("Give the light's address, not a URL.")
    if ":" not in host:
        host = f"{host}:{PORT}"
    return f"http://{host}{path}"


def _call(target: str, method: str = "GET", body: str = "") -> dict:
    try:
        _status, text = webrequest.request(
            _url(target), method,
            {"Content-Type": "application/json"} if body else None,
            body, timeout=TIMEOUT,
        )
    except webrequest.WebRequestError as error:
        raise KeyLightError(f"Could not reach the light: {error}") from error
    try:
        answer = json.loads(text or "{}")
    except ValueError as error:
        raise KeyLightError(
            "That address answered, but not like a Key Light does."
        ) from error
    lights = (answer or {}).get("lights") or []
    if not lights:
        raise KeyLightError(
            "That address answered, but not like a Key Light does."
        )
    return lights[0]


def state(target: str) -> dict:
    """What the light is doing now: `on`, `brightness` and `kelvin`."""
    first = _call(target)
    return {
        "on": bool(first.get("on")),
        "brightness": int(first.get("brightness") or 0),
        "kelvin": device_to_kelvin(first.get("temperature") or MIN_MIRED),
    }


def apply(
    target: str,
    on: bool | None = None,
    brightness: int | None = None,
    kelvin: int | None = None,
) -> dict:
    """Change only what was named, and return the light's new state.

    A `PUT` replaces the fields it carries, so sending a whole light object to
    change one value would push back whatever this application last read --
    turning a light on again because a brightness key remembered it that way.
    """
    fields: dict = {}
    if on is not None:
        fields["on"] = 1 if on else 0
    if brightness is not None:
        fields["brightness"] = max(
            MIN_BRIGHTNESS, min(MAX_BRIGHTNESS, int(brightness))
        )
    if kelvin is not None:
        fields["temperature"] = kelvin_to_device(kelvin)
    if not fields:
        return state(target)
    body = json.dumps({"numberOfLights": 1, "lights": [fields]})
    first = _call(target, "PUT", body)
    fresh = {
        "on": bool(first.get("on")),
        "brightness": int(first.get("brightness") or 0),
        "kelvin": device_to_kelvin(first.get("temperature") or MIN_MIRED),
    }
    _remember(target, fresh)
    return fresh


# ---------- state a key can draw, fetched off the render worker ----------

_lock = threading.Lock()
_states: dict[str, tuple[float, dict]] = {}
_pending: set[str] = set()


def cached_state(target: str, now: float | None = None) -> dict | None:
    """What this light was last seen doing, refreshing if it is due.

    Never performs a request. `feedback()` runs on the single render worker,
    and a light is on the network: waiting for it there would stall every
    other key on the deck. None means "not established", which a key must draw
    as nothing rather than as off.
    """
    host = str(target or "").strip()
    if not host:
        return None
    moment = time.monotonic() if now is None else now
    with _lock:
        fetched, value = _states.get(host, (0.0, {}))
        fresh = bool(value) and moment - fetched <= STATE_STALE
        due = moment - fetched >= STATE_TTL
        start = due and host not in _pending
        if start:
            _pending.add(host)
    if start and not webrequest.background(_refresh, host):
        with _lock:
            _pending.discard(host)
    return dict(value) if fresh else None


def _refresh(host: str) -> None:
    try:
        _remember(host, state(host))
    except KeyLightError as error:
        # Keep the last known state; STATE_STALE is what eventually drops it.
        # A light behind a slow access point must not make its key flicker.
        log.debug("Could not read the state of %s: %s", host, error)
    except Exception:
        log.warning("Unexpected failure reading a Key Light", exc_info=True)
    finally:
        with _lock:
            _pending.discard(host)


def _remember(host: str, value: dict) -> None:
    with _lock:
        _states[str(host)] = (time.monotonic(), dict(value))


def forget_states() -> None:
    """Drop everything known about lights, for a replaced configuration."""
    with _lock:
        _states.clear()
        _pending.clear()


# ---------- finding them ----------

_discovery: tuple[float, list[Light]] = (0.0, [])


def discovery_available() -> bool:
    return host.which(DISCOVERY_TOOL) is not None


def discover(now: float | None = None) -> list[Light]:
    """Every Key Light the daemon knows about, cached for DISCOVERY_TTL.

    The editor calls this on the GTK thread while building a dropdown, and
    `avahi-browse -t` takes about a second even when nothing answers, so the
    cache is what keeps rebuilding a row from costing that every time.
    """
    global _discovery
    moment = time.monotonic() if now is None else now
    taken, found = _discovery
    if found and moment - taken <= DISCOVERY_TTL:
        return list(found)
    if not discovery_available():
        return []
    fresh = _browse()
    _discovery = (moment, fresh)
    return list(fresh)


def forget_discovery() -> None:
    global _discovery
    _discovery = (0.0, [])


def _browse() -> list[Light]:
    try:
        result = subprocess.run(
            host.argv([DISCOVERY_TOOL, "-rpt", SERVICE]),
            capture_output=True, text=True, timeout=DISCOVERY_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as error:
        log.debug("Could not browse for Key Lights: %s", error)
        return []
    return parse_browse(result.stdout or "")


def parse_browse(text: str) -> list[Light]:
    """Read `avahi-browse -rpt` output.

    Its parseable form is semicolon separated:
    `=;iface;protocol;name;type;domain;host;address;port;txt...`. Only the
    resolved lines (`=`) carry an address, and only IPv4 is kept: a light is
    on the local network and an IPv6 record for the same device would list it
    twice under the same name.
    """
    found: list[Light] = []
    seen: set[str] = set()
    for line in str(text or "").splitlines():
        parts = line.split(";")
        if len(parts) < 9 or parts[0] != "=" or parts[2] != "IPv4":
            continue
        name = _unescape(parts[3])
        host, address = parts[6].strip(), parts[7].strip()
        try:
            port = int(parts[8])
        except ValueError:
            port = PORT
        light = Light(name or host or address, host, address, port)
        if light.target and light.target not in seen:
            seen.add(light.target)
            found.append(light)
    return sorted(found, key=lambda item: item.name.casefold())


def _unescape(value: str) -> str:
    r"""avahi escapes non-ASCII as `\195\179`, one decimal byte at a time."""
    raw = str(value or "")
    if "\\" not in raw:
        return raw
    out = bytearray()
    index = 0
    while index < len(raw):
        if raw[index] == "\\" and raw[index + 1: index + 4].isdigit():
            out.append(int(raw[index + 1: index + 4]) & 0xFF)
            index += 4
        else:
            out.extend(raw[index].encode("utf-8"))
            index += 1
    return out.decode("utf-8", "replace")


# Filled by `discover()` so the editor can show a name while a key stores the
# address, exactly as the audio devices and the OBS hotkeys do.
def light_choices() -> list[str]:
    return [light.target for light in discover()]


def light_label(target: str) -> str:
    for light in discover():
        if light.target == target:
            return f"{light.name} ({light.address})" if light.address else light.name
    return target
