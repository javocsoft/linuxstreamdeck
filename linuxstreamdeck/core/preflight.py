"""Checks worth running in the minute before going live.

The point is the minute before you press Stream, when you would otherwise have
to look at six things in three windows. Every check here is one of those six.

Three rules shape all of it, and they matter more than the checks themselves:

- **"Not checked" is a result, not a gap.** Several of these can only be
  answered on the machine OBS is running on, and one of them only covers V4L2
  devices. A check that quietly reports nothing in those cases would be worse
  than absent, because it would look like a pass.
- **Nothing here has a side effect.** No scene is switched, no source is
  activated, no setting is written. A check that changed what goes out to make
  itself answerable would be unusable during a show.
- **Every result states its own limit.** `detail` is what the user reads, and
  it says what was *not* established as plainly as what was.

There is deliberately no overall verdict. A board of results is honest; the
word "READY" is a promise this cannot keep, and the first time something broke
with everything green it would be worthless for ever after.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import sysstats

log = logging.getLogger(__name__)

# Result of one check. `unchecked` is not a failure and not a pass: it means
# this machine or this setup cannot answer the question without side effects.
OK = "ok"
WARN = "warn"
FAIL = "fail"
UNCHECKED = "unchecked"

STATE_ORDER = (FAIL, WARN, UNCHECKED, OK)

# Loud enough to be a voice rather than a noise floor. Digital silence reads as
# an enormous negative number, so anything near it is nothing at all.
AUDIO_ALIVE_DB = -50.0
AUDIO_SECONDS = 2.0

# Below this the machine has nothing left for the encoder.
CPU_WARN_PERCENT = 75.0
CPU_FAIL_PERCENT = 90.0

# Free space on the recording drive. The same thresholds the disk key uses.
DISK_WARN_MB = 10240
DISK_FAIL_MB = 2048

LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "")


@dataclass
class Check:
    """One question, its answer, and what the answer does not cover."""

    id: str
    label: str                 # two words at most: it has to fit on a key
    state: str
    detail: str                # the full sentence, for the report dialog
    icon: str = ""

    @property
    def is_ok(self) -> bool:
        return self.state == OK


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {
            state: sum(1 for c in self.checks if c.state == state)
            for state in (OK, WARN, FAIL, UNCHECKED)
        }

    def summary(self) -> str:
        """One line that never hides what was skipped."""
        counts = self.counts()
        parts = []
        if counts[FAIL]:
            parts.append(f"{counts[FAIL]} failed")
        if counts[WARN]:
            parts.append(f"{counts[WARN]} to look at")
        if counts[OK]:
            parts.append(f"{counts[OK]} ok")
        # Always last and always present when it applies: the whole reason this
        # line exists is that "everything ok" must never be able to mean
        # "everything I bothered to look at was ok".
        if counts[UNCHECKED]:
            parts.append(f"{counts[UNCHECKED]} not checked")
        return "Pre-flight: " + (", ".join(parts) if parts else "nothing to check")

    def worst(self) -> str:
        for state in STATE_ORDER:
            if any(c.state == state for c in self.checks):
                return state
        return OK


def is_local(host: str) -> bool:
    """Whether OBS is on this computer, so local readings mean anything.

    Free space, the recording folder and the capture devices are all properties
    of the machine OBS runs on. Against a remote OBS they would be measuring
    this one, and a green light about the wrong computer is worse than none.
    """
    return str(host or "").strip().lower() in LOCAL_HOSTS


# --------------------------------------------------------------------------
# individual checks. Each takes what it needs and returns exactly one Check.
# --------------------------------------------------------------------------

def check_connection(connected: bool) -> Check:
    return Check(
        id="obs",
        label="OBS",
        state=OK if connected else FAIL,
        detail=(
            "OBS is connected."
            if connected
            else "OBS is not reachable, so nothing about it could be checked."
        ),
        icon="mdi:video-box" if connected else "mdi:video-off",
    )


def check_audio(
    peaks: dict[str, float] | None, muted: dict[str, bool] | None = None
) -> Check:
    """Is the audio going to work?

    Two signals, and only one of them is decidable. **Muted** is a fact: a
    muted microphone is a mistake every time, and it is the classic way to talk
    to nobody for ten minutes. **Silence** is not: a quiet room with nobody
    speaking measures exactly like a dead input, and someone running this
    before going live is usually not talking, so silence alone can never be a
    failure without crying wolf on almost every run.

    `peaks` maps input name to its loudest level in dB over the sampling
    window, or None when no meter events arrived at all — which is a third
    thing again, a question that went unanswered.
    """
    silenced = sorted(name for name, is_muted in (muted or {}).items() if is_muted)
    if silenced:
        return Check(
            id="audio",
            label="Audio",
            state=FAIL,
            detail=(
                f"{'These inputs are' if len(silenced) > 1 else 'This input is'} "
                f"muted in OBS: {', '.join(f'«{name}»' for name in silenced)}."
            ),
            icon="mdi:microphone-off",
        )
    if peaks is None:
        return Check(
            id="audio",
            label="Audio",
            state=UNCHECKED,
            detail=(
                "OBS sent no audio level readings, so it is not known whether "
                "anything is being heard."
            ),
            icon="mdi:microphone-question",
        )
    alive = {name: db for name, db in peaks.items() if db >= AUDIO_ALIVE_DB}
    if alive:
        loudest = max(alive.items(), key=lambda kv: kv[1])
        return Check(
            id="audio",
            label="Audio",
            state=OK,
            detail=(
                f"{len(alive)} of {len(peaks)} inputs are making sound; "
                f"loudest is «{loudest[0]}» at {loudest[1]:.0f} dB. "
                "Nothing is muted and sound is reaching OBS. Note that OBS "
                "measures levels before the mute, so a level on its own never "
                "meant it would be heard; the mute state is what does."
            ),
            icon="mdi:microphone",
        )
    if not peaks:
        return Check(
            id="audio",
            label="Audio",
            state=UNCHECKED,
            detail="OBS reported no audio inputs at all.",
            icon="mdi:microphone-question",
        )
    return Check(
        id="audio",
        label="Audio",
        state=WARN,
        detail=(
            f"Nothing was muted, but all {len(peaks)} audio inputs stayed "
            f"silent for {AUDIO_SECONDS:.0f} seconds. If you were not speaking "
            "that is exactly what silence looks like, so this is not a fault "
            "on its own: say something and run it again to be sure."
        ),
        icon="mdi:microphone-question",
    )


def check_cameras(sources: dict[str, str], held: set[str], local: bool) -> Check:
    """Does OBS actually hold a capture device for each camera source?

    `sources` maps each V4L2 source name to the device it is configured for;
    `held` is the set of video devices the OBS process really has open.

    This asks the kernel instead of looking at the picture, and that is
    deliberate. A screenshot of a camera that is not on screen comes back black
    whether the camera is broken or the scene simply is not live, and at the
    moment someone runs this the program scene is usually a holding card. Asked
    that way it reported two of three working cameras as dead. The device
    handle is unambiguous, needs no rendering, and cannot disturb what is going
    out.
    """
    if not local:
        return Check(
            id="cameras",
            label="Cameras",
            state=UNCHECKED,
            detail=(
                "OBS is running on another computer, so its capture devices "
                "cannot be inspected from here."
            ),
            icon="mdi:camera-off",
        )
    if not sources:
        return Check(
            id="cameras",
            label="Cameras",
            state=UNCHECKED,
            detail="There are no V4L2 camera or capture sources to check.",
            icon="mdi:camera-off",
        )
    missing = sorted(
        name for name, device in sources.items()
        if not device or os.path.realpath(device) not in held
    )
    caveat = (
        " This covers V4L2 webcams and capture cards only, and it says OBS has "
        "the device open, not that the picture is any good: a closed privacy "
        "shutter still reads as fine."
    )
    if missing:
        return Check(
            id="cameras",
            label="Cameras",
            state=FAIL,
            detail=(
                f"OBS does not hold a device for {len(missing)} of "
                f"{len(sources)} sources: {', '.join(missing)}." + caveat
            ),
            icon="mdi:camera-off",
        )
    return Check(
        id="cameras",
        label="Cameras",
        state=OK,
        detail=(
            f"OBS holds a capture device for all {len(sources)} camera "
            f"sources." + caveat
        ),
        icon="mdi:camera",
    )


def check_disk(folder: str, local: bool) -> Check:
    if not local:
        return Check(
            id="disk",
            label="Disk",
            state=UNCHECKED,
            detail=(
                "OBS records on another computer, so the free space here says "
                "nothing about it."
            ),
            icon="mdi:harddisk",
        )
    free = sysstats.disk_free_mb(folder)
    if free is None:
        return Check(
            id="disk",
            label="Disk",
            state=UNCHECKED,
            detail=f"The free space of «{folder}» could not be read.",
            icon="mdi:harddisk",
        )
    text = f"{free / 1024:.0f} GB free on {folder}."
    if free < DISK_FAIL_MB:
        return Check(id="disk", label="Disk", state=FAIL,
                     detail=text + " That will not last a recording.",
                     icon="mdi:harddisk")
    if free < DISK_WARN_MB:
        return Check(id="disk", label="Disk", state=WARN,
                     detail=text + " Enough for a short session only.",
                     icon="mdi:harddisk")
    return Check(id="disk", label="Disk", state=OK, detail=text,
                 icon="mdi:harddisk")


def check_record_folder(folder: str, local: bool) -> Check:
    """OBS reports this failure when you press Record, which is too late."""
    if not local:
        return Check(
            id="recording",
            label="Rec path",
            state=UNCHECKED,
            detail="The recording folder is on another computer.",
            icon="mdi:folder-question",
        )
    if not folder:
        return Check(id="recording", label="Rec path", state=UNCHECKED,
                     detail="OBS did not report a recording folder.",
                     icon="mdi:folder-question")
    path = Path(folder)
    if not path.is_dir():
        return Check(id="recording", label="Rec path", state=FAIL,
                     detail=f"The recording folder «{folder}» does not exist.",
                     icon="mdi:folder-remove")
    if not os.access(path, os.W_OK):
        return Check(id="recording", label="Rec path", state=FAIL,
                     detail=f"«{folder}» cannot be written to.",
                     icon="mdi:folder-remove")
    return Check(id="recording", label="Rec path", state=OK,
                 detail=f"Recording to {folder}, which is writable.",
                 icon="mdi:folder-check")


def check_cpu(percent: float | None) -> Check:
    if percent is None:
        return Check(id="cpu", label="CPU", state=UNCHECKED,
                     detail="The machine's CPU use could not be read yet.",
                     icon="mdi:chip")
    text = f"The machine is at {percent:.0f}% CPU before going live."
    if percent >= CPU_FAIL_PERCENT:
        return Check(id="cpu", label="CPU", state=FAIL,
                     detail=text + " There is nothing left for the encoder.",
                     icon="mdi:chip")
    if percent >= CPU_WARN_PERCENT:
        return Check(id="cpu", label="CPU", state=WARN,
                     detail=text + " Expect dropped frames under load.",
                     icon="mdi:chip")
    return Check(id="cpu", label="CPU", state=OK, detail=text, icon="mdi:chip")


def check_stream_target(service: str, has_key: bool | None) -> Check:
    """Presence only. The stream key is a secret and never leaves this check.

    `has_key` is a boolean the caller derived; the key itself must never be
    passed in here, logged, shown, or written to the report — the log is a file
    on disk now, and one careless line would put someone's stream key in it.
    """
    if has_key is None:
        return Check(id="stream", label="Stream", state=UNCHECKED,
                     detail="The streaming destination could not be read.",
                     icon="mdi:broadcast-off")
    if not has_key:
        return Check(id="stream", label="Stream", state=FAIL,
                     detail=(
                         f"No stream key is set for «{service}», so "
                         "going live would fail."
                     ),
                     icon="mdi:broadcast-off")
    return Check(id="stream", label="Stream", state=OK,
                 detail=(
                     f"A stream key is configured for «{service}». "
                     "Only its presence was checked, never its value, and "
                     "whether it is accepted is not known until you go live."
                 ),
                 icon="mdi:broadcast")


def check_outputs(streaming: bool, recording: bool) -> Check:
    running = [
        name for name, active in (("streaming", streaming), ("recording", recording))
        if active
    ]
    if running:
        return Check(id="outputs", label="Already on", state=WARN,
                     detail=f"You are already {' and '.join(running)}.",
                     icon="mdi:alert-circle")
    return Check(id="outputs", label="Outputs", state=OK,
                 detail="Nothing is streaming or recording yet.",
                 icon="mdi:stop-circle-outline")


def check_collection(name: str, total: int = 0) -> Check:
    """Which scene collection OBS has loaded.

    This is here in place of a check that had to be removed, and the reason is
    worth keeping. Comparing the deck's keys against OBS cannot be automated:
    obs-websocket can only list what the **loaded** collection contains, so a
    key belonging to any other one is indistinguishable from a key whose scene
    was renamed. Measured on a real configuration with nine collections, an
    automatic check reported 99 perfectly good references as broken. Only the
    user, standing in front of a grid they chose, can tell those apart, which
    is what «Check keys against OBS» is for.

    What can be established is which collection is loaded, and with several of
    them that is exactly what gets forgotten: going live on the right scenes of
    the wrong collection. So this states a fact and judges nothing.
    """
    if not name:
        return Check(id="collection", label="Collection", state=UNCHECKED,
                     detail="OBS did not report which scene collection is loaded.",
                     icon="mdi:folder-multiple-image")
    of_many = f" of {total}" if total > 1 else ""
    return Check(
        id="collection",
        label="Collection",
        state=OK,
        detail=(
            f"OBS has «{name}» loaded{of_many}. Check it is the one you mean: "
            "this only reports which it is, and cannot tell whether your keys "
            "were built for it. Use «Check keys against OBS» from inside the "
            "folder you are about to use for that."
        ),
        icon="mdi:folder-multiple-image",
    )


def check_twitch_account(linked: bool, login: str, missing: tuple) -> Check:
    """Whether the account these keys act through is still usable.

    A token that expired since the last session is the failure this exists to
    catch: every Twitch key still looks configured, and the first press of one
    is the moment it turns out otherwise.
    """
    if not linked:
        # Not a failure. Someone who does not use Twitch has nothing wrong with
        # their setup, and the board must not tell them they have.
        return Check(id="twitch", label="Twitch", state=UNCHECKED,
                     detail="No Twitch account is connected, so nothing about "
                            "it was checked.",
                     icon="mdi:twitch")
    if missing:
        return Check(
            id="twitch", label="Twitch", state=WARN,
            detail=(
                f"Connected as {login}, but this authorization predates some "
                f"of the keys: {', '.join(missing)} was never granted. "
                "Connect the account again to allow it."
            ),
            icon="mdi:twitch",
        )
    return Check(id="twitch", label="Twitch", state=OK,
                 detail=f"Connected as {login}, and Twitch still accepts it.",
                 icon="mdi:twitch")


def check_twitch_title(title: str, linked: bool) -> Check:
    if not linked:
        return Check(id="twitch_title", label="Title", state=UNCHECKED,
                     detail="No Twitch account is connected.",
                     icon="mdi:format-title")
    if not (title or "").strip():
        return Check(id="twitch_title", label="Title", state=FAIL,
                     detail="The Twitch stream has no title.",
                     icon="mdi:format-title")
    return Check(
        id="twitch_title", label="Title", state=OK,
        detail=(
            f"The Twitch title is «{title}». This only checks there is one; "
            "whether it is the right one for today is your call."
        ),
        icon="mdi:format-title",
    )


def check_twitch_category(category: str, linked: bool) -> Check:
    """Left on yesterday's game is the classic one, and it cannot be caught
    here: only that a category is set at all can be established."""
    if not linked:
        return Check(id="twitch_category", label="Category", state=UNCHECKED,
                     detail="No Twitch account is connected.",
                     icon="mdi:gamepad-variant")
    if not (category or "").strip():
        return Check(id="twitch_category", label="Category", state=FAIL,
                     detail="The Twitch channel has no category set.",
                     icon="mdi:gamepad-variant")
    return Check(
        id="twitch_category", label="Category", state=OK,
        detail=(
            f"The Twitch category is «{category}». This only checks there is "
            "one; it cannot know whether it is still yesterday's."
        ),
        icon="mdi:gamepad-variant",
    )


def check_twitch_live(live: bool, linked: bool) -> Check:
    if not linked:
        return Check(id="twitch_live", label="On air", state=UNCHECKED,
                     detail="No Twitch account is connected.",
                     icon="mdi:broadcast")
    if live:
        return Check(id="twitch_live", label="On air", state=WARN,
                     detail="Twitch already shows this channel as live.",
                     icon="mdi:broadcast")
    return Check(id="twitch_live", label="On air", state=OK,
                 detail="Twitch does not show this channel as live yet.",
                 icon="mdi:broadcast-off")


# --------------------------------------------------------------------------
# local facts the checks need
# --------------------------------------------------------------------------

def obs_pid() -> int | None:
    """The running OBS process, or None. Linux only, like the rest of this."""
    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        return None
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            if (entry / "comm").read_text(encoding="ascii").strip() == "obs":
                return int(entry.name)
        except (OSError, ValueError, UnicodeDecodeError):
            continue
    return None


def held_video_devices(pid: int | None) -> set[str]:
    """Video devices the OBS process actually has open, resolved to real paths.

    Resolving matters on both sides. A source is commonly configured through a
    stable `/dev/v4l/by-id/...` symlink while the open descriptor reports
    `/dev/videoN`; comparing the two as strings reports a working camera as
    broken.
    """
    if pid is None:
        return set()
    held: set[str] = set()
    try:
        descriptors = list(Path(f"/proc/{pid}/fd").iterdir())
    except OSError:
        return held
    for descriptor in descriptors:
        try:
            target = os.readlink(descriptor)
        except OSError:
            continue
        if target.startswith("/dev/video"):
            held.add(os.path.realpath(target))
    return held


def free_space_mb(folder: str) -> float | None:
    """Free megabytes of a folder, for callers that are not the disk check."""
    try:
        return shutil.disk_usage(folder).free / (1024 * 1024)
    except OSError:
        return None


# --------------------------------------------------------------------------
# running them
# --------------------------------------------------------------------------

def run(obs, controller=None, twitch=None):
    """Yield each check as it completes, in the order they are worth reading.

    A generator rather than a list on purpose. The audio check listens for two
    seconds, so a caller painting results as they arrive shows honest progress
    instead of freezing and then dumping everything at once — and on a deck too
    small to hold them all, the ones that matter most are the ones that fit.

    `obs` is duck-typed, so the whole engine is testable without OBS.
    """
    connected = bool(getattr(obs, "connected", False))
    yield check_connection(connected)
    if not connected:
        # Everything OBS-facing asks OBS something. Reporting those as failures
        # would blame the setup for a connection problem already reported
        # above. Twitch is a different service and still has real answers, so
        # it deliberately runs anyway: "is my title set" does not stop being
        # worth knowing because OBS happens to be closed.
        for check in _all_unchecked("OBS is not connected."):
            yield check
        for check in _twitch_checks(twitch):
            yield check
        return

    local = is_local(getattr(obs, "host", ""))

    # Machine CPU only exists as a difference between two readings, so the
    # first call after start-up has nothing to compare against and answers
    # nothing. Taking it here costs nothing and the audio check below spends
    # exactly the gap it needs, so by the time the value is read it is real.
    sysstats.cpu_percent()

    peaks = obs.measure_audio(AUDIO_SECONDS)
    # Only the inputs that reported a level: those are the audio ones, so this
    # needs no separate list and asks OBS about nothing else.
    yield check_audio(peaks, obs.muted_inputs(peaks or ()))

    sources = obs.capture_sources() if local else {}
    yield check_cameras(sources, held_video_devices(obs_pid()), local)

    folder = obs.record_directory()
    yield check_disk(folder, local)
    yield check_record_folder(folder, local)
    yield check_collection(*obs.scene_collection())

    yield check_cpu(sysstats.cpu_percent())

    service, has_key = obs.stream_target()
    yield check_stream_target(service, has_key)

    state = getattr(obs, "state", None)
    yield check_outputs(
        bool(getattr(state, "streaming", False)),
        bool(getattr(state, "recording", False)),
    )

    # Last, because not everybody streams to Twitch, and a deck too small to
    # hold every check should spend its keys on the ones that apply to all.
    for check in _twitch_checks(twitch):
        yield check


def _twitch_checks(twitch):
    """The Twitch half, or four honest blanks when it cannot be asked.

    Reads the channel **now** rather than through `channel()`. That one answers
    from a cache up to twenty seconds old, which is the right trade for a key
    repainting itself and exactly the wrong one for a decision about going
    live.
    """
    linked = bool(twitch is not None and getattr(twitch, "linked", False))
    login = getattr(twitch, "account", "") if linked else ""
    missing = tuple(twitch.missing_scopes()) if linked else ()
    yield check_twitch_account(linked, login, missing)
    if not linked:
        yield check_twitch_title("", False)
        yield check_twitch_category("", False)
        yield check_twitch_live(False, False)
        return
    try:
        snapshot = twitch.refresh_channel()
    except Exception:
        # Unreachable is not misconfigured. Reporting an empty title because
        # the network dropped would send somebody hunting for a problem they
        # do not have.
        log.debug("Could not read the Twitch channel for the pre-flight",
                  exc_info=True)
        for check_id, label, icon in (
            ("twitch_title", "Title", "mdi:format-title"),
            ("twitch_category", "Category", "mdi:gamepad-variant"),
            ("twitch_live", "On air", "mdi:broadcast"),
        ):
            yield Check(check_id, label, UNCHECKED,
                        "Twitch could not be reached just now.", icon)
        return
    yield check_twitch_title(str(snapshot.get("title") or ""), True)
    yield check_twitch_category(str(snapshot.get("category") or ""), True)
    yield check_twitch_live(bool(snapshot.get("live")), True)


def _all_unchecked(reason: str):
    for check_id, label, icon in (
        ("audio", "Audio", "mdi:microphone-question"),
        ("cameras", "Cameras", "mdi:camera-off"),
        ("disk", "Disk", "mdi:harddisk"),
        ("recording", "Rec path", "mdi:folder-question"),
        ("collection", "Collection", "mdi:folder-multiple-image"),
        ("cpu", "CPU", "mdi:chip"),
        ("stream", "Stream", "mdi:broadcast-off"),
        ("outputs", "Outputs", "mdi:stop-circle-outline"),
    ):
        yield Check(check_id, label, UNCHECKED, reason, icon)
