"""Per-application volume, microphone mute and the default audio device.

`media.py` already drives the master volume, which is what a media key does.
This is the other half, and it is the half a stream needs: turning the game
down without touching the microphone, muting the microphone without muting
everything, and moving the whole session between speakers and a headset.

**Everything here goes through `pactl -f json`.** Three separate reasons, and
each of them ruled out the obvious alternative:

- `wpctl` cannot list what is playing in any parseable form. Its `status` is a
  human-readable tree, drawn for a terminal rather than for a program.
- `pactl` without `-f json` answers in the **user's own language**. On this
  machine it says `Silenciado: no` and `Entrada del destino #108`, so anything
  matching English words works until it is run by somebody who is not English.
- `pactl` is present on PulseAudio and on PipeWire alike, through
  `pipewire-pulse`, so one backend covers both.

It is therefore a hard requirement for these actions rather than a preference,
and its absence is reported as a status message like every other missing
backend -- never as an exception that breaks a multi-action key.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import threading
import time

from . import host

log = logging.getLogger(__name__)

PACTL = "pactl"
TIMEOUT = 5.0

# What a volume key can be pointed at.
TARGET_OUTPUT = "output"
TARGET_INPUT = "input"
TARGET_APP = "app"
TARGETS = (TARGET_OUTPUT, TARGET_INPUT, TARGET_APP)
TARGET_LABELS = {
    TARGET_OUTPUT: "Speakers (default output)",
    TARGET_INPUT: "Microphone (default input)",
    TARGET_APP: "One application",
}

# What it does to it.
MODE_UP = "up"
MODE_DOWN = "down"
MODE_SET = "set"
MODE_TOGGLE = "toggle"
MODE_MUTE = "mute"
MODE_UNMUTE = "unmute"
MODES = (MODE_UP, MODE_DOWN, MODE_SET, MODE_TOGGLE, MODE_MUTE, MODE_UNMUTE)
MODE_LABELS = {
    MODE_UP: "Volume up",
    MODE_DOWN: "Volume down",
    MODE_SET: "Set the volume",
    MODE_TOGGLE: "Mute / unmute",
    MODE_MUTE: "Mute",
    MODE_UNMUTE: "Unmute",
}
VOLUME_MODES = (MODE_UP, MODE_DOWN, MODE_SET)
MUTE_MODES = (MODE_TOGGLE, MODE_MUTE, MODE_UNMUTE)

# A raise is capped here rather than left to the mixer. Above 100 % the sample
# is amplified in software, which distorts; a key held down should not be able
# to walk into that by accident.
MAX_VOLUME_PERCENT = 100
DEFAULT_STEP_PERCENT = 5

MISSING_BACKEND = (
    "Install pulseaudio-utils (or pipewire-pulse) to control audio from a key"
)


class MixerError(Exception):
    """Anything that stopped a mixer command from doing its job."""


class Device:
    """One output or input the session can use."""

    def __init__(self, name: str, description: str, kind: str, default: bool):
        self.name = name
        self.description = description or name
        self.kind = kind
        self.default = default

    def __repr__(self) -> str:  # pragma: no cover - debugging only
        return f"<Device {self.kind} {self.name!r}>"


# ---------- talking to pactl ----------

def available() -> bool:
    return host.which(PACTL) is not None


def _run(args: list[str]) -> subprocess.CompletedProcess:
    if not available():
        raise MixerError(MISSING_BACKEND)
    try:
        return subprocess.run(
            host.argv([PACTL, *args]),
            capture_output=True, text=True, timeout=TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise MixerError(f"The audio mixer did not respond: {error}") from error


def _command(args: list[str]) -> None:
    result = _run(args)
    if result.returncode != 0:
        raise MixerError(
            (result.stderr or "").strip() or "The audio mixer refused that"
        )


def _query(args: list[str]):
    """Ask pactl for structured data.

    `-f json` arrived in PulseAudio 15. Where it is missing the only other
    answer is prose in the user's language, which is not something to parse,
    so this says so plainly instead of guessing.
    """
    result = _run(["-f", "json", *args])
    if result.returncode != 0:
        raise MixerError(
            (result.stderr or "").strip() or "The audio mixer refused that"
        )
    try:
        return json.loads(result.stdout or "[]")
    except ValueError as error:
        raise MixerError(
            "This version of pactl cannot report the mixer in a readable form"
        ) from error


# ---------- what exists ----------

# Filled by `devices()` so the editor can show a description while the key
# stores the stable name. Same shape as the OBS hotkey map, and for the same
# reason: what the protocol takes is not what anyone wants to read.
_labels: dict[str, str] = {}


def devices() -> list[Device]:
    """Every usable output and then every usable input."""
    found = _outputs() + _inputs()
    _labels.update({device.name: device.description for device in found})
    return found


def _outputs() -> list[Device]:
    default = _default("sink")
    return [
        Device(entry.get("name", ""), entry.get("description", ""),
               TARGET_OUTPUT, entry.get("name") == default)
        for entry in _query(["list", "sinks"])
        if entry.get("name")
    ]


def _inputs() -> list[Device]:
    default = _default("source")
    return [
        Device(entry.get("name", ""), entry.get("description", ""),
               TARGET_INPUT, entry.get("name") == default)
        # A monitor is the loopback of an output, not a microphone. Offering
        # them would double the list with entries nobody means to record from.
        for entry in _query(["list", "sources"])
        if entry.get("name") and not entry.get("monitor_source")
    ]


def _default(kind: str) -> str:
    result = _run([f"get-default-{kind}"])
    return (result.stdout or "").strip() if result.returncode == 0 else ""


def device_choices() -> list[str]:
    """Device names for the editor, outputs first."""
    return [device.name for device in devices()]


def device_label(name: str) -> str:
    """What to show for a stored device name."""
    return _labels.get(name, name)


def playing_applications() -> list[str]:
    """Applications with audio playing right now, by name.

    A name rather than an index or a PID: those change every time the
    application restarts, and a key has to keep working tomorrow.
    """
    names: list[str] = []
    for entry in _query(["list", "sink-inputs"]):
        name = _app_name(entry)
        if name and name not in names:
            names.append(name)
    return sorted(names, key=str.casefold)


def _app_name(entry: dict) -> str:
    properties = entry.get("properties") or {}
    return str(
        properties.get("application.name")
        or properties.get("application.process.binary")
        or ""
    ).strip()


# ---------- changing something ----------

def switch_to(name: str) -> str:
    """Make this device the session default. Returns what to tell the user."""
    wanted = str(name or "").strip()
    if not wanted:
        raise MixerError("This key has no audio device chosen.")
    for device in devices():
        if device.name == wanted:
            kind = "sink" if device.kind == TARGET_OUTPUT else "source"
            _command([f"set-default-{kind}", device.name])
            forget_state()
            return device.description
    raise MixerError(f"No audio device named {wanted} is connected.")


def toggle_between(first: str, second: str) -> str:
    """Switch to whichever of the two is not currently the default.

    One key for two devices is the whole point of it: speakers and a headset,
    without spending a second key or looking at which one is live.
    """
    current = {device.name for device in devices() if device.default}
    return switch_to(second if first in current else first)


def apply(
    target: str,
    identifier: str,
    mode: str,
    amount: int = DEFAULT_STEP_PERCENT,
) -> str:
    """Perform one volume or mute change. Returns what to tell the user."""
    kind = target if target in TARGETS else TARGET_OUTPUT
    action = mode if mode in MODES else MODE_TOGGLE
    subjects = _subjects(kind, identifier)
    if not subjects:
        raise MixerError(_nothing_to_change(kind, identifier))
    for verb, subject, current in subjects:
        if action in MUTE_MODES:
            state = {MODE_TOGGLE: "toggle", MODE_MUTE: "1", MODE_UNMUTE: "0"}
            _command([f"set-{verb}-mute", subject, state[action]])
        else:
            level = _level(action, current, amount)
            _command([f"set-{verb}-volume", subject, f"{level}%"])
    # The press just changed what the cache holds, so the repaint that follows
    # it must not show the state from before it.
    forget_state()
    return _described(kind, identifier, action, amount)


def _level(mode: str, current: int, amount: int) -> int:
    """The absolute level to set, capped at MAX_VOLUME_PERCENT.

    Written as an absolute value rather than as pactl's own `+5%`, because
    pactl clamps nothing: a key pressed a dozen times would walk the sink to
    160 %, where the sample is amplified in software and distorts. Reading the
    level first is what puts a ceiling on it.
    """
    step = max(0, int(amount))
    if mode == MODE_SET:
        wanted = step
    elif mode == MODE_UP:
        wanted = int(current) + step
    else:
        wanted = int(current) - step
    return max(0, min(MAX_VOLUME_PERCENT, wanted))


def _subjects(kind: str, identifier: str) -> list[tuple[str, str, int]]:
    """`(pactl noun, target, current level)` triples this key acts on.

    An application can own several streams at once -- a browser commonly does
    -- so all of them move together. Acting on only the first would leave one
    tab audible after the key said it had muted it. Each carries its own level
    so a relative change keeps whatever difference they were set to.
    """
    if kind in (TARGET_OUTPUT, TARGET_INPUT):
        output = kind == TARGET_OUTPUT
        noun, alias = ("sink", "@DEFAULT_SINK@") if output else (
            "source", "@DEFAULT_SOURCE@"
        )
        default = _default(noun)
        for entry in _query(["list", f"{noun}s"]):
            if entry.get("name") == default:
                return [(noun, alias, _percent(entry))]
        return []
    wanted = str(identifier or "").strip().casefold()
    return [
        ("sink-input", str(entry.get("index")), _percent(entry))
        for entry in _query(["list", "sink-inputs"])
        if wanted and _app_name(entry).casefold() == wanted
    ]


def _nothing_to_change(kind: str, identifier: str) -> str:
    if kind != TARGET_APP:
        return "No default audio device is available."
    if not str(identifier or "").strip():
        return "This key has no application chosen."
    return f"{identifier} is not playing any audio right now."


def _described(kind: str, identifier: str, mode: str, amount: int) -> str:
    who = identifier if kind == TARGET_APP else TARGET_LABELS[kind].split(" (")[0]
    if mode in MUTE_MODES:
        return f"{MODE_LABELS[mode]}: {who}"
    if mode == MODE_SET:
        return f"{who} volume set to {int(amount)}%"
    direction = "up" if mode == MODE_UP else "down"
    return f"{who} volume {direction} {int(amount)}%"


# ---------- reading state back ----------

# How long one reading of the mixer is reused. `feedback()` runs on the single
# render worker, and each reading is four processes; without this a page of
# fifteen mute keys would spawn sixty of them per repaint. One second is well
# inside what a key needs to look right and turns that page into four.
STATE_TTL = 1.0

_state_lock = threading.Lock()
_snapshot: tuple[float, dict] = (0.0, {})


def _read_all() -> dict:
    return {
        "sinks": _query(["list", "sinks"]),
        "sources": _query(["list", "sources"]),
        "sink-inputs": _query(["list", "sink-inputs"]),
        "default-sink": _default("sink"),
        "default-source": _default("source"),
    }


def snapshot(now: float | None = None) -> dict:
    """One reading of the mixer, shared by every key that asks within TTL."""
    global _snapshot
    moment = time.monotonic() if now is None else now
    with _state_lock:
        taken, data = _snapshot
        if data and moment - taken <= STATE_TTL:
            return data
    # Read outside the lock: several keys asking at once should not queue
    # behind one another, and the worst a race costs is one extra reading.
    fresh = _read_all()
    with _state_lock:
        _snapshot = (moment, fresh)
    return fresh


def forget_state() -> None:
    """Drop the cached reading, so the next look asks the mixer again."""
    global _snapshot
    with _state_lock:
        _snapshot = (0.0, {})


def state(target: str, identifier: str = "") -> tuple[bool, int] | None:
    """`(muted, volume percent)`, or None when it cannot be established.

    None is deliberately a third answer. A key that shows "not muted" because
    the question went unanswered is worse than one that shows nothing: the
    whole reason a microphone key exists is to be believed.
    """
    kind = target if target in TARGETS else TARGET_OUTPUT
    try:
        data = snapshot()
    except MixerError:
        return None
    if kind == TARGET_APP:
        return _app_state(data, identifier)
    noun = "sinks" if kind == TARGET_OUTPUT else "sources"
    default = data.get(
        "default-sink" if kind == TARGET_OUTPUT else "default-source", ""
    )
    for entry in data.get(noun, []):
        if entry.get("name") == default:
            return bool(entry.get("mute")), _percent(entry)
    return None


def _app_state(data: dict, identifier: str) -> tuple[bool, int] | None:
    wanted = str(identifier or "").strip().casefold()
    if not wanted:
        return None
    found = [
        entry for entry in data.get("sink-inputs", [])
        if _app_name(entry).casefold() == wanted
    ]
    if not found:
        return None
    # Muted only when every stream of it is: one audible tab means the
    # application can still be heard, and the key must not claim otherwise.
    muted = all(bool(entry.get("mute")) for entry in found)
    return muted, max(_percent(entry) for entry in found)


def _percent(entry: dict) -> int:
    channels = (entry.get("volume") or {}).values()
    levels = []
    for channel in channels:
        raw = str((channel or {}).get("value_percent", "")).strip().rstrip("%")
        try:
            levels.append(int(float(raw)))
        except ValueError:
            continue
    return max(levels) if levels else 0
