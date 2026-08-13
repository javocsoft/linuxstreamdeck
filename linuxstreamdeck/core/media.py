"""Media transport and volume control.

Transport goes through MPRIS, the D-Bus interface every mainstream Linux player
implements, driven by `playerctl`. Volume uses the session's own mixer: `wpctl`
on PipeWire, falling back to `pactl` on PulseAudio. Both are the tools the
desktop already relies on, so no synthetic media keys are needed and this works
identically on Wayland and X11.
"""

from __future__ import annotations

import logging
import shutil
import subprocess

from . import host

log = logging.getLogger(__name__)

VOLUME_STEP_PERCENT = 5
SINK = "@DEFAULT_AUDIO_SINK@"

# Action id -> (label, kind). "kind" selects the backend below.
MEDIA_ACTIONS = (
    ("previous", "Previous track"),
    ("play_pause", "Play / Pause"),
    ("next", "Next track"),
    ("stop", "Stop"),
    ("mute", "Mute"),
    ("volume_up", "Volume up"),
    ("volume_down", "Volume down"),
)
MEDIA_ACTION_IDS = tuple(identifier for identifier, _label in MEDIA_ACTIONS)
MEDIA_ACTION_LABELS = tuple(label for _identifier, label in MEDIA_ACTIONS)
DEFAULT_MEDIA_ACTION = "play_pause"

_PLAYERCTL_COMMANDS = {
    "previous": "previous",
    "play_pause": "play-pause",
    "next": "next",
    "stop": "stop",
}


def label_for(identifier: str) -> str:
    for known, label in MEDIA_ACTIONS:
        if known == identifier:
            return label
    return identifier


def identifier_for(label: str) -> str:
    for identifier, known in MEDIA_ACTIONS:
        if known == label:
            return identifier
    return DEFAULT_MEDIA_ACTION


def _run(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        host.argv(command), capture_output=True, text=True, timeout=5
    )


def transport_available() -> bool:
    return host.which("playerctl") is not None


def mixer_command() -> list[str] | None:
    """The mixer front end available on this session, if any."""
    if host.which("wpctl"):
        return ["wpctl"]
    if host.which("pactl"):
        return ["pactl"]
    return None


def perform(identifier: str) -> None:
    """Run one media action. Raises ValueError with a user-facing message."""
    identifier = str(identifier or "").strip() or DEFAULT_MEDIA_ACTION
    if identifier in _PLAYERCTL_COMMANDS:
        _transport(identifier)
        return
    if identifier == "mute":
        _mute()
        return
    if identifier in ("volume_up", "volume_down"):
        _volume(identifier == "volume_up")
        return
    raise ValueError(f"Unknown media action: {identifier}")


def _transport(identifier: str) -> None:
    if not transport_available():
        raise ValueError(
            "Install playerctl to control media playback"
        )
    result = _run(["playerctl", _PLAYERCTL_COMMANDS[identifier]])
    if result.returncode != 0:
        message = (result.stderr or "").strip().lower()
        if "no players" in message:
            raise ValueError("No media player is running")
        raise ValueError(
            (result.stderr or "").strip() or "The media player did not respond"
        )


def _mute() -> None:
    mixer = mixer_command()
    if mixer is None:
        raise ValueError("No audio mixer was found (install wireplumber or pulseaudio-utils)")
    if mixer[0] == "wpctl":
        command = ["wpctl", "set-mute", SINK, "toggle"]
    else:
        command = ["pactl", "set-sink-mute", SINK, "toggle"]
    _check(_run(command))


def _volume(up: bool) -> None:
    mixer = mixer_command()
    if mixer is None:
        raise ValueError("No audio mixer was found (install wireplumber or pulseaudio-utils)")
    if mixer[0] == "wpctl":
        change = f"{VOLUME_STEP_PERCENT}%{'+' if up else '-'}"
        # -l keeps a raise from pushing the sink above 100 %.
        command = ["wpctl", "set-volume", "-l", "1.0", SINK, change]
    else:
        change = f"{'+' if up else '-'}{VOLUME_STEP_PERCENT}%"
        command = ["pactl", "set-sink-volume", SINK, change]
    _check(_run(command))


def _check(result: subprocess.CompletedProcess) -> None:
    if result.returncode != 0:
        raise ValueError(
            (result.stderr or "").strip() or "The audio mixer did not respond"
        )
