"""Local audio playback through GStreamer."""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable
from pathlib import Path

SUPPORTED_AUDIO_EXTENSIONS = (
    ".wav",
    ".wave",
    ".mp3",
    ".ogg",
    ".oga",
    ".flac",
    ".opus",
)
SUPPORTED_AUDIO_FORMATS = "WAV, MP3, OGG, FLAC or Opus"

_POLL_MILLISECONDS = 100
_gst_lock = threading.Lock()
_gst_initialized = False

log = logging.getLogger(__name__)


class AudioPlaybackError(RuntimeError):
    """A user-facing local audio playback failure."""


def _load_gst():
    global _gst_initialized

    try:
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
    except (ImportError, ValueError) as error:
        raise AudioPlaybackError(
            "GStreamer is unavailable; install its Python bindings and plugins"
        ) from error

    with _gst_lock:
        if not _gst_initialized:
            Gst.init(None)
            _gst_initialized = True
    return Gst


def _audio_path(value) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise AudioPlaybackError("Choose an audio file")
    path = Path(raw).expanduser()
    if not path.is_file():
        raise AudioPlaybackError(f"Audio file not found: {path}")
    if path.suffix.lower() not in SUPPORTED_AUDIO_EXTENSIONS:
        raise AudioPlaybackError(
            f"Unsupported audio format. Choose {SUPPORTED_AUDIO_FORMATS}"
        )
    return path.resolve()


def _volume_fraction(value) -> float:
    try:
        percent = float(value)
    except (TypeError, ValueError) as error:
        raise AudioPlaybackError("Volume must be between 0 and 100") from error
    if not math.isfinite(percent):
        raise AudioPlaybackError("Volume must be between 0 and 100")
    return max(0.0, min(100.0, percent)) / 100.0


def play_audio(
    file_path,
    volume_percent=100,
    maximum_seconds=0,
    stop_requested: Callable[[], bool] | None = None,
    *,
    gst=None,
    monotonic: Callable[[], float] = time.monotonic,
) -> None:
    """Play a local file until EOS, a time limit, or an explicit stop request."""
    path = _audio_path(file_path)
    volume = _volume_fraction(volume_percent)
    try:
        limit = max(0.0, float(maximum_seconds or 0))
    except (TypeError, ValueError) as error:
        raise AudioPlaybackError("Maximum play time must be a duration") from error
    if not math.isfinite(limit):
        raise AudioPlaybackError("Maximum play time must be a duration")

    Gst = gst or _load_gst()
    player = Gst.ElementFactory.make("playbin", "linuxstreamdeck-audio")
    if player is None:
        raise AudioPlaybackError("GStreamer playbin is unavailable")
    bus = player.get_bus()
    if bus is None:
        raise AudioPlaybackError("GStreamer could not create an audio bus")

    player.set_property("uri", path.as_uri())
    player.set_property("volume", volume)
    deadline = monotonic() + limit if limit > 0 else None

    try:
        changed = player.set_state(Gst.State.PLAYING)
        if changed == Gst.StateChangeReturn.FAILURE:
            raise AudioPlaybackError("GStreamer could not start audio playback")

        message_types = Gst.MessageType.ERROR | Gst.MessageType.EOS
        while True:
            if stop_requested is not None and stop_requested():
                return

            timeout = _POLL_MILLISECONDS * Gst.MSECOND
            if deadline is not None:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    return
                timeout = min(timeout, max(1, int(remaining * Gst.SECOND)))

            message = bus.timed_pop_filtered(timeout, message_types)
            if message is None:
                continue
            if message.type == Gst.MessageType.EOS:
                return
            if message.type == Gst.MessageType.ERROR:
                error, debug = message.parse_error()
                detail = getattr(error, "message", str(error))
                if debug:
                    log.debug("GStreamer audio error details: %s", debug)
                raise AudioPlaybackError(f"Could not play audio: {detail}")
    finally:
        player.set_state(Gst.State.NULL)
