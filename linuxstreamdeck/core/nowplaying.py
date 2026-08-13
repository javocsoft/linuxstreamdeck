"""What is playing, for a key that shows it.

MPRIS is the D-Bus interface every mainstream Linux player implements, so one
reading covers Spotify, VLC, Firefox, mpv and the rest without knowing which
is running. `playerctl` is already how `media.py` drives transport, so this
adds no dependency.

**A song title does not fit on a key.** Measured at 96 px: as a label it wraps
to two lines and is cut -- "Wish You Were Here" becomes "Wish You Were" --
and centered it is worse, because `compose()` fits centered text to the key
width, so a short title is drawn huge and a long one unreadably small. The
artist does fit, every time, because artist names are short by nature.

So a key shows the **album art with the artist over it**. The picture is what
makes it recognisable before anything is read; the artist is what stays
legible when there is no picture.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit

from . import webrequest

from . import host

log = logging.getLogger(__name__)

PLAYERCTL = "playerctl"
TIMEOUT = 3.0

# The unit separator: it cannot appear in a title or an artist, unlike any
# punctuation somebody might have used. Verified against playerctl 2.4,
# where a metadata key the player does not publish renders as empty rather
# than as an error or as the literal template.
SEPARATOR = "\x1f"
FORMAT = SEPARATOR.join((
    "{{status}}", "{{artist}}", "{{title}}", "{{mpris:artUrl}}",
))

# How long one reading is reused. `feedback()` runs on the single render
# worker and each reading is a process (measured at ~7 ms), so without this a
# page of media keys would spawn one per key per repaint.
STATE_TTL = 2.0

# Album art, kept by URL. A handful is plenty: it is the current track and
# whatever was playing just before.
ART_CACHE_LIMIT = 8
# What a cover can weigh before it is refused. Generous for a JPEG and small
# enough that a wrong or redirected address cannot stream something large
# into memory.
MAX_ART_BYTES = 4 * 1024 * 1024

PLAYING = "Playing"
PAUSED = "Paused"


class Track:
    """What one player is doing, reduced to what a key can draw."""

    def __init__(
        self, status: str = "", artist: str = "", title: str = "",
        art_url: str = "",
    ) -> None:
        self.status = status
        self.artist = artist
        self.title = title
        self.art_url = art_url

    @property
    def playing(self) -> bool:
        return self.status == PLAYING

    @property
    def caption(self) -> str:
        """What goes on the key.

        The artist, falling back to the title: a radio stream or a podcast
        often publishes no artist at all, and an empty key would be worse than
        a long one.
        """
        return self.artist or self.title

    def __repr__(self) -> str:  # pragma: no cover - debugging only
        return f"<Track {self.status} {self.artist!r} {self.title!r}>"


def available() -> bool:
    return host.which(PLAYERCTL) is not None


# ---------- the reading, shared by every key ----------

_lock = threading.Lock()
_reading: tuple[float, Track | None] = (0.0, None)


def current(now: float | None = None) -> Track | None:
    """What is playing, or None when nothing is.

    Cached for `STATE_TTL`, so a page of media keys costs one process rather
    than one each.
    """
    global _reading
    moment = time.monotonic() if now is None else now
    with _lock:
        taken, track = _reading
        if taken and moment - taken <= STATE_TTL:
            return track
    fresh = _read()
    with _lock:
        _reading = (moment, fresh)
    return fresh


def _read() -> Track | None:
    if not available():
        return None
    try:
        result = subprocess.run(
            host.argv([PLAYERCTL, "metadata", "--format", FORMAT]),
            capture_output=True, text=True, timeout=TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as error:
        log.debug("Could not read what is playing: %s", error)
        return None
    if result.returncode != 0:
        # "No players found" is the ordinary answer when nothing is running,
        # not a failure worth reporting anywhere.
        return None
    return parse(result.stdout)


def parse(text: str) -> Track | None:
    """Read one line of `playerctl metadata --format`."""
    line = (str(text or "").splitlines() or [""])[0]
    if not line.strip(SEPARATOR).strip():
        return None
    parts = line.split(SEPARATOR)
    parts += [""] * (4 - len(parts))
    status, artist, title, art = (part.strip() for part in parts[:4])
    if not status and not artist and not title:
        return None
    return Track(status=status, artist=artist, title=title, art_url=art)


def forget() -> None:
    """Drop the cached reading and every cached cover."""
    global _reading
    with _lock:
        _reading = (0.0, None)
    with _art_lock:
        _art.clear()
        _art_pending.clear()


# ---------- album art ----------

_art_lock = threading.Lock()
_art: dict[str, bytes | None] = {}
_art_pending: set[str] = set()


def artwork(url: str) -> bytes | None:
    """The cover for this track, or None until there is one.

    A `file://` cover is read here: it is local, it is the same kind of read
    `compose()` already does for a custom key icon, and doing it in the
    background would leave the key blank for a refresh on every track change.
    An `http(s)` cover -- which is what Spotify publishes -- is fetched off
    this thread, because `feedback()` runs on the single render worker and
    must never wait on the network.
    """
    address = str(url or "").strip()
    if not address:
        return None
    with _art_lock:
        if address in _art:
            return _art[address]
        already = address in _art_pending
    parts = urlsplit(address)
    if parts.scheme == "file":
        return _remember_art(address, _read_file(parts.path))
    if parts.scheme not in ("http", "https"):
        # Not something to open. Remembered as "no cover" so it is not
        # examined again on every repaint.
        return _remember_art(address, None)
    if not already:
        with _art_lock:
            _art_pending.add(address)
        if not webrequest.background(_fetch_art, address):
            with _art_lock:
                _art_pending.discard(address)
    return None


def _read_file(path: str) -> bytes | None:
    try:
        target = Path(unquote(path))
        if target.stat().st_size > MAX_ART_BYTES:
            return None
        return target.read_bytes()
    except OSError:
        return None


def _fetch_art(address: str) -> None:
    data: bytes | None = None
    try:
        data = webrequest.fetch_bytes(address, MAX_ART_BYTES)
    except Exception:
        log.debug("Could not fetch album art from %s", address, exc_info=True)
    finally:
        with _art_lock:
            _art_pending.discard(address)
        _remember_art(address, data)


def _remember_art(address: str, data: bytes | None) -> bytes | None:
    with _art_lock:
        if len(_art) >= ART_CACHE_LIMIT and address not in _art:
            _art.clear()
        _art[address] = data
    return data
