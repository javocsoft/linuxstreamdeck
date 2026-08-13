"""A virtual output others can listen to, so a sound key reaches the stream.

`sys.audio` plays a file on the machine, which is right for a cue meant for
the person at the deck and useless for a soundboard: nobody in the stream or
the call hears it. The missing piece is not audio code at all -- it is one
device.

**A null sink plus its monitor.** PipeWire (and PulseAudio before it) can
create an output that goes nowhere and exposes what was played into it as a
*source*. OBS, Discord, a browser or anything else then picks
`linuxstreamdeck.monitor` as an input and hears exactly what a key played,
with nothing else from the machine mixed in.

**A loopback comes with it**, from that monitor back to the normal output, so
the person pressing the key hears it too. Without it a soundboard key is
indistinguishable from a broken one: something happens somewhere and the room
stays silent.

Everything goes through `pactl`, for the reasons `mixer.py` sets out at
length: `wpctl` cannot be parsed and plain `pactl` answers in the user's own
language. Its absence is a status message, never an exception.
"""

from __future__ import annotations

import logging
import shutil
import subprocess

from . import host

log = logging.getLogger(__name__)

PACTL = "pactl"
TIMEOUT = 5.0

# What the device is called. The name is the identifier other applications
# store, so it must not change; the description is what they show in a list.
SINK_NAME = "linuxstreamdeck"
SINK_DESCRIPTION = "LinuxStreamDeck"
MONITOR_NAME = f"{SINK_NAME}.monitor"

# Enough to bridge a buffer without being heard as an echo against the same
# sound arriving on the stream.
LOOPBACK_LATENCY_MS = 50

MISSING_BACKEND = (
    "Install pulseaudio-utils (or pipewire-pulse) to send audio to the stream"
)


class SoundboardError(Exception):
    """Anything that stopped the virtual output from being made ready."""


def available() -> bool:
    return host.which(PACTL) is not None


def _run(args: list[str]) -> subprocess.CompletedProcess:
    if not available():
        raise SoundboardError(MISSING_BACKEND)
    try:
        return subprocess.run(
            host.argv([PACTL, *args]),
            capture_output=True, text=True, timeout=TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise SoundboardError(f"The audio server did not respond: {error}") from error


def sink_exists() -> bool:
    """Whether the virtual output is already there.

    It survives this application: the module belongs to the audio session, so
    a restart finds the device it made last time. Checking rather than
    creating blindly is what stops a day of restarts leaving a dozen of them.
    """
    result = _run(["list", "short", "sinks"])
    if result.returncode != 0:
        return False
    return any(
        line.split("\t")[1:2] == [SINK_NAME]
        for line in (result.stdout or "").splitlines()
    )


def ensure() -> str:
    """Make sure the virtual output exists, and answer its name."""
    if sink_exists():
        return SINK_NAME
    created = _run([
        "load-module", "module-null-sink",
        f"sink_name={SINK_NAME}",
        f"sink_properties=device.description={SINK_DESCRIPTION}",
    ])
    if created.returncode != 0:
        raise SoundboardError(
            (created.stderr or "").strip()
            or "The audio server refused to create the virtual output"
        )
    # Best effort: without it the key is silent to the person pressing it,
    # which is confusing, but the sound still reaches the stream -- so a
    # failure here must not stop the sink being used.
    monitoring = _run([
        "load-module", "module-loopback",
        f"source={MONITOR_NAME}",
        f"latency_msec={LOOPBACK_LATENCY_MS}",
    ])
    if monitoring.returncode != 0:
        log.info(
            "The virtual output was created without local monitoring: %s",
            (monitoring.stderr or "").strip(),
        )
    return SINK_NAME


def remove() -> None:
    """Take the virtual output away again.

    Deliberately **not** called on shutdown. Another application stores this
    device by name -- OBS keeps it as an audio source, Discord as an input --
    so removing it on exit would silently break their configuration every
    time this one closed. The audio session clears it at logout anyway.
    """
    for module in ("module-loopback", "module-null-sink"):
        result = _run(["unload-module", module])
        if result.returncode != 0:
            log.debug(
                "Could not unload %s: %s", module, (result.stderr or "").strip()
            )
