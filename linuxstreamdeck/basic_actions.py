"""System actions and navigation between deck pages."""

from __future__ import annotations

import logging
import subprocess
import webbrowser

from .core import apps, keystrokes, media, mixer, nowplaying, soundboard
from .core.actions import Action, Param, apply_default_icons, parse_duration, register
from .core.audio import SUPPORTED_AUDIO_EXTENSIONS, play_audio

log = logging.getLogger(__name__)

CAT_SYSTEM = "System"
CAT_NAV = "Navigation"

# Upper bound for a single Wait step. One hour is far beyond any real use and
# prevents a mistaken value from holding an action worker indefinitely.
MAX_WAIT_SECONDS = 3600
TIMER_FINISHED_COLOR = "#8a4b08"
# A muted key is lit in the same red OBS uses for a muted input, so the two
# read as the same state whichever of them a key is pointed at.
MUTED_COLOR = "#a51d2d"

# Bounds for a counter. Neither is a real limit anyone would reach; they
# only stop a hand-edited configuration from producing a value too wide to
# draw on a key.
# Where a sound key plays. "Shared" routes it through the virtual output other
# applications listen to, which is the whole of what makes a soundboard: the
# default keeps every key already configured playing exactly as it did.
# Whether a media key also shows what is playing. Off by default: a key that
# does not ask costs nothing, and every key already configured keeps the icon
# and label it was given.
# One icon per media action. Without this every one of the seven inherits the
# play/pause picture, and a row of transport keys is unreadable. The volume
# three share a silhouette and differ by x, - and +, which read as actions --
# unlike a speaker with more or fewer waves, which reads as a level.
MEDIA_ICONS = {
    "previous": "mdi:skip-previous",
    "play_pause": "mdi:play-pause",
    "next": "mdi:skip-next",
    "stop": "mdi:stop",
    "mute": "mdi:volume-mute",
    "volume_up": "mdi:volume-plus",
    "volume_down": "mdi:volume-minus",
}

NOW_PLAYING_NO = "no"
NOW_PLAYING_YES = "yes"
NOW_PLAYING_CHOICES = (NOW_PLAYING_NO, NOW_PLAYING_YES)
NOW_PLAYING_LABELS = {
    NOW_PLAYING_NO: "No",
    NOW_PLAYING_YES: "Album art and artist",
}

AUDIO_LOCAL = "local"
AUDIO_SHARED = "shared"
AUDIO_OUTPUTS = (AUDIO_LOCAL, AUDIO_SHARED)
AUDIO_OUTPUT_LABELS = {
    AUDIO_LOCAL: "Only this computer",
    AUDIO_SHARED: "This computer and the virtual microphone",
}

MAX_COUNTER_STEP = 1000
MAX_COUNTER_VALUE = 999999

# Page indicator display modes.
PAGE_SHOW_POSITION = "Number and total"
PAGE_SHOW_NUMBER = "Number"
PAGE_SHOW_NAME = "Page name"

# What a long press does on an "Open application" key.
LONG_PRESS_NOTHING = "Nothing"
LONG_PRESS_CLOSE = "Close the application"
LONG_PRESS_FORCE_CLOSE = "Force close the application"

YES = "Yes"
NO = "No"


@register
class RunCommand(Action):
    id = "sys.command"
    name = "Run command"
    category = CAT_SYSTEM
    description = "Run a shell command in the background."
    params = [Param("command", "Command")]

    def execute(self, ctx, p):
        cmd = p.get("command", "").strip()
        if cmd:
            subprocess.Popen(cmd, shell=True, start_new_session=True)


@register
class OpenUrl(Action):
    id = "sys.url"
    name = "Open URL"
    category = CAT_SYSTEM
    description = "Open a web address in the default browser."
    params = [Param("url", "URL", default="https://")]

    def execute(self, ctx, p):
        url = p.get("url", "").strip()
        if url:
            webbrowser.open(url)


@register
class Wait(Action):
    id = "sys.wait"
    name = "Wait"
    category = CAT_SYSTEM
    description = ("Pause for the given time (MM:SS) before the next action. "
                  "Meant for multiple / toggle keys, between two actions.")
    params = [Param("duration", "Wait time (MM:SS)", kind="duration", default="00:05")]
    running_feedback = True

    def execute(self, ctx, p):
        seconds = parse_duration(p.get("duration"))
        if seconds > 0:
            ctx.wait_until_stopped(min(seconds, MAX_WAIT_SECONDS))


@register
class PlayAudio(Action):
    id = "sys.audio"
    name = "Play audio file"
    category = CAT_SYSTEM
    description = (
        "Play a local WAV, MP3, OGG, FLAC or Opus file. Send it to the "
        "virtual microphone and it becomes a soundboard: OBS, Discord or a "
        "call can pick that up and everyone hears it. A multiple-action "
        "sequence continues after playback finishes or reaches its time limit."
    )
    params = [
        Param(
            "file",
            "Audio file",
            kind="file",
            file_filter_name="Audio files",
            extensions=list(SUPPORTED_AUDIO_EXTENSIONS),
        ),
        Param(
            "volume",
            "Volume (%)",
            kind="int",
            default=100,
            minimum=0,
            maximum=100,
            step=5,
        ),
        Param(
            "duration",
            "Maximum play time (optional)",
            kind="optional_duration",
            default="",
        ),
        Param(
            "output",
            "Who hears it",
            kind="choice",
            default=AUDIO_LOCAL,
            choices=list(AUDIO_OUTPUTS),
            choice_labels=dict(AUDIO_OUTPUT_LABELS),
        ),
    ]
    running_feedback = True
    restart_on_repress = True

    def execute(self, ctx, p):
        sink = ""
        if str(p.get("output") or AUDIO_LOCAL) == AUDIO_SHARED:
            try:
                sink = soundboard.ensure()
            except soundboard.SoundboardError as error:
                # Reported rather than played locally: a cue that came out of
                # the speakers while the stream heard nothing would look like
                # it had worked.
                ctx.bus.emit("status", text=str(error))
                return
        play_audio(
            p.get("file", ""),
            p.get("volume", 100),
            parse_duration(p.get("duration")),
            stop_requested=ctx.stop_requested,
            sink=sink,
        )


@register
class CountdownTimer(Action):
    id = "sys.timer"
    name = "Countdown timer"
    category = CAT_SYSTEM
    description = (
        "Start a countdown shown as HH:MM:SS. Press again while it is running "
        "or after it finishes to stop and reset it. An optional sound can play "
        "on completion."
    )
    params = [
        Param(
            "duration",
            "Timer duration (MM:SS)",
            kind="duration",
            default="01:00",
        ),
        Param(
            "sound",
            "Completion sound (optional)",
            kind="file",
            file_filter_name="Audio files",
            extensions=list(SUPPORTED_AUDIO_EXTENSIONS),
        ),
        Param(
            "volume",
            "Sound volume (%)",
            kind="int",
            default=100,
            minimum=0,
            maximum=100,
            step=5,
        ),
    ]
    immediate = True

    def execute(self, ctx, p):
        duration = parse_duration(p.get("duration"))
        if duration <= 0:
            ctx.bus.emit(
                "status",
                text="Timer duration must be greater than zero",
            )
            return
        if ctx.key is None:
            return
        started = ctx.controller.toggle_countdown(
            ctx.key,
            duration,
            p.get("sound", ""),
            p.get("volume", 100),
        )
        ctx.bus.emit(
            "status",
            text="Timer started" if started else "Timer reset",
        )

    def feedback(self, ctx, p):
        snapshot = ctx.controller.countdown_snapshot(
            ctx.key,
            parse_duration(p.get("duration")),
        )
        feedback = {
            "display": snapshot.display,
            "active": snapshot.running or snapshot.finished,
        }
        if snapshot.finished:
            feedback["color"] = TIMER_FINISHED_COLOR
        return feedback


@register
class Stopwatch(Action):
    id = "sys.stopwatch"
    name = "Stopwatch"
    category = CAT_SYSTEM
    description = (
        "Count elapsed time as HH:MM:SS. Press again to stop and reset it "
        "to zero."
    )
    immediate = True

    def execute(self, ctx, p):
        if ctx.key is None:
            return
        started = ctx.controller.toggle_stopwatch(ctx.key)
        ctx.bus.emit(
            "status",
            text="Stopwatch started" if started else "Stopwatch reset",
        )

    def feedback(self, ctx, p):
        snapshot = ctx.controller.stopwatch_snapshot(ctx.key)
        return {
            "display": snapshot.display,
            "active": snapshot.running,
        }


@register
class PageNext(Action):
    id = "nav.page.next"
    name = "Next page"
    category = CAT_NAV
    description = (
        "Switch to the next page, wrapping to the first page after the last."
    )

    def execute(self, ctx, p):
        controller = ctx.controller
        controller.set_page(
            (controller.current_page + 1) % len(controller.config.pages)
        )


@register
class PagePrevious(Action):
    id = "nav.page.previous"
    name = "Previous page"
    category = CAT_NAV
    description = (
        "Switch to the previous page, wrapping to the last page from the first."
    )

    def execute(self, ctx, p):
        controller = ctx.controller
        controller.set_page(
            (controller.current_page - 1) % len(controller.config.pages)
        )


@register
class MediaControl(Action):
    id = "sys.media"
    name = "Media action"
    category = CAT_SYSTEM
    description = (
        "Control the running media player through MPRIS, or the session volume."
    )
    params = [
        Param(
            "action",
            "Action",
            kind="choice",
            choices=list(media.MEDIA_ACTION_LABELS),
            default=media.label_for(media.DEFAULT_MEDIA_ACTION),
        ),
        Param(
            "show",
            "Show what is playing",
            kind="choice",
            default=NOW_PLAYING_NO,
            choices=list(NOW_PLAYING_CHOICES),
            choice_labels=dict(NOW_PLAYING_LABELS),
        ),
    ]
    immediate = True

    def execute(self, ctx, p):
        identifier = media.identifier_for(str(p.get("action", "") or ""))
        try:
            media.perform(identifier)
        except ValueError as error:
            ctx.bus.emit("status", text=str(error))
            return
        # The press just changed what is playing, so the reading taken before
        # it must not be what the key repaints with.
        if shows_now_playing(p):
            nowplaying.forget()

    def icon_for(self, params):
        """The picture for the transport action this key was pointed at."""
        chosen = media.identifier_for(str((params or {}).get("action", "") or ""))
        return MEDIA_ICONS.get(chosen, self.default_icon)

    def feedback(self, ctx, p):
        if not shows_now_playing(p):
            return {}
        track = nowplaying.current()
        # Nothing playing: the key goes back to being the key that was
        # configured, with its own icon and its own label.
        if track is None or not track.caption:
            return {}
        return {
            "label": track.caption,
            "image": nowplaying.artwork(track.art_url),
            "active": track.playing,
        }


def shows_now_playing(p: dict) -> bool:
    return str((p or {}).get("show") or NOW_PLAYING_NO) == NOW_PLAYING_YES


@register
class Counter(Action):
    id = "sys.counter"
    name = "Counter"
    category = CAT_SYSTEM
    description = (
        "Count something on the key: deaths, takes, attempts. A press adds, "
        "and holding the key resets it. Give it a negative step to count down."
    )
    params = [
        Param(
            "step",
            "Each press adds",
            kind="int",
            default=1,
            minimum=-MAX_COUNTER_STEP,
            maximum=MAX_COUNTER_STEP,
        ),
        Param(
            "start",
            "Starts at",
            kind="int",
            default=0,
            minimum=-MAX_COUNTER_VALUE,
            maximum=MAX_COUNTER_VALUE,
        ),
    ]
    # Adding to a number in memory. Occupying an action worker for that would
    # make it the slowest key on the deck to answer.
    immediate = True
    # Holding it resets. A second key just to reset a counter is a key spent on
    # something that happens once a session.
    supports_long_press = True

    def execute(self, ctx, p):
        value = ctx.controller.bump_counter(
            ctx.key, _counter_step(p), _counter_start(p)
        )
        ctx.bus.emit("status", text=f"Counter: {value}")

    def long_press(self, ctx, p) -> bool:
        start = _counter_start(p)
        ctx.controller.reset_counter(ctx.key, start)
        ctx.bus.emit("status", text=f"Counter reset to {start}")
        return True

    def feedback(self, ctx, p):
        return {
            "display": str(
                ctx.controller.counter_value(ctx.key, _counter_start(p))
            )
        }


def _counter_step(p: dict) -> int:
    try:
        step = int((p or {}).get("step", 1))
    except (TypeError, ValueError):
        return 1
    # A step of zero is a key that looks like it works and never changes
    # anything, so it counts as the default rather than as a choice.
    return max(-MAX_COUNTER_STEP, min(MAX_COUNTER_STEP, step)) or 1


def _counter_start(p: dict) -> int:
    try:
        start = int((p or {}).get("start", 0))
    except (TypeError, ValueError):
        return 0
    return max(-MAX_COUNTER_VALUE, min(MAX_COUNTER_VALUE, start))


@register
class VolumeControl(Action):
    id = "sys.volume"
    name = "Volume and mute"
    category = CAT_SYSTEM
    description = (
        "Change the volume of the speakers, the microphone or one application "
        "on its own. A mute key lights up while whatever it points at is "
        "muted."
    )
    params = [
        Param(
            "target",
            "What to change",
            kind="choice",
            default=mixer.TARGET_OUTPUT,
            choices=list(mixer.TARGETS),
            choice_labels=dict(mixer.TARGET_LABELS),
        ),
        Param(
            "application",
            "Application",
            choices_source="audio_apps",
            placeholder="An application that is playing audio",
            depends_on="target",
            depends_values=[mixer.TARGET_APP],
        ),
        Param(
            "mode",
            "Do what",
            kind="choice",
            default=mixer.MODE_TOGGLE,
            choices=list(mixer.MODES),
            choice_labels=dict(mixer.MODE_LABELS),
        ),
        Param(
            "amount",
            "By how much",
            kind="int",
            default=mixer.DEFAULT_STEP_PERCENT,
            minimum=0,
            maximum=mixer.MAX_VOLUME_PERCENT,
            step=5,
            depends_on="mode",
            depends_values=list(mixer.VOLUME_MODES),
        ),
    ]
    # Two short local commands. Occupying an action worker for that would make
    # a volume key feel slower than the media keys beside it.
    immediate = True

    def execute(self, ctx, p):
        try:
            done = mixer.apply(
                str(p.get("target") or mixer.TARGET_OUTPUT),
                str(p.get("application") or ""),
                str(p.get("mode") or mixer.MODE_TOGGLE),
                int(p.get("amount") or mixer.DEFAULT_STEP_PERCENT),
            )
        except mixer.MixerError as error:
            # A missing backend or an application that stopped playing is a
            # message, never an exception that would abandon the rest of a
            # multi-action key.
            ctx.bus.emit("status", text=str(error))
            return
        ctx.bus.emit("status", text=done)

    def feedback(self, ctx, p):
        if str(p.get("mode") or "") not in mixer.MUTE_MODES:
            return {}
        current = mixer.state(
            str(p.get("target") or mixer.TARGET_OUTPUT),
            str(p.get("application") or ""),
        )
        # None means the question went unanswered, which must not be drawn as
        # "not muted": the whole reason a microphone key exists is to be
        # believed about exactly this.
        if current is None:
            return {}
        muted, _level = current
        return {"active": muted, "color": MUTED_COLOR if muted else None}


@register
class AudioDevice(Action):
    id = "sys.audio_device"
    name = "Switch audio device"
    category = CAT_SYSTEM
    description = (
        "Make a device the one the session uses. Choose a second device too "
        "and one key moves between them, which is what speakers and a headset "
        "want. The key lights up while its device is the one in use."
    )
    params = [
        Param("device", "Device", choices_source="audio_devices"),
        Param(
            "device_alt",
            "And back to",
            choices_source="audio_devices",
            placeholder="Leave empty to always switch to the one above",
        ),
    ]
    immediate = True

    def execute(self, ctx, p):
        first = str(p.get("device") or "")
        second = str(p.get("device_alt") or "")
        try:
            if second:
                done = mixer.toggle_between(first, second)
            else:
                done = mixer.switch_to(first)
        except mixer.MixerError as error:
            ctx.bus.emit("status", text=str(error))
            return
        ctx.bus.emit("status", text=f"Audio device: {done}")

    def feedback(self, ctx, p):
        wanted = str(p.get("device") or "")
        if not wanted:
            return {}
        try:
            data = mixer.snapshot()
        except mixer.MixerError:
            return {}
        defaults = {
            data.get("default-sink", ""), data.get("default-source", "")
        }
        return {"active": wanted in defaults}


@register
class Open(Action):
    id = "sys.open"
    name = "Open"
    category = CAT_SYSTEM
    description = (
        "Open a file, folder or program with the desktop's own handler."
    )
    params = [
        Param("target", "File, folder or program", kind="file"),
    ]

    def execute(self, ctx, p):
        try:
            apps.open_target(str(p.get("target", "") or ""))
        except ValueError as error:
            ctx.bus.emit("status", text=str(error))


@register
class OpenApplication(Action):
    id = "sys.app.open"
    name = "Open application"
    category = CAT_SYSTEM
    description = (
        "Start an installed application, optionally closing it again with a "
        "long press."
    )
    params = [
        Param("application", "Application", choices_source="applications"),
        Param(
            "if_running",
            "If it is already running",
            kind="choice",
            choices=[YES, NO],
            default=YES,
        ),
        Param(
            "long_press",
            "On long press",
            kind="choice",
            choices=[LONG_PRESS_NOTHING, LONG_PRESS_CLOSE, LONG_PRESS_FORCE_CLOSE],
            default=LONG_PRESS_NOTHING,
        ),
    ]
    supports_long_press = True

    def execute(self, ctx, p):
        name = str(p.get("application", "") or "")
        if not name:
            ctx.bus.emit("status", text="Choose an application")
            return
        try:
            if apps.is_running(name):
                if str(p.get("if_running", YES) or YES) == NO:
                    ctx.bus.emit("status", text=f"{name} is already running")
                    return
                # Starting it again is what raises an existing window: the
                # desktop routes the launch to the running instance.
            apps.launch(name)
        except ValueError as error:
            ctx.bus.emit("status", text=str(error))

    def long_press(self, ctx, p) -> bool:
        """Close the application instead of starting it. Returns whether it ran."""
        mode = str(p.get("long_press", LONG_PRESS_NOTHING) or LONG_PRESS_NOTHING)
        if mode == LONG_PRESS_NOTHING:
            return False
        name = str(p.get("application", "") or "")
        try:
            apps.close(name, force=mode == LONG_PRESS_FORCE_CLOSE)
            ctx.bus.emit("status", text=f"Closed {name}")
        except ValueError as error:
            ctx.bus.emit("status", text=str(error))
        return True

    def feedback(self, ctx, p):
        name = str(p.get("application", "") or "")
        return {"active": bool(name) and apps.is_running(name)}


@register
class CloseApplication(Action):
    id = "sys.app.close"
    name = "Close application"
    category = CAT_SYSTEM
    description = "Ask an application to quit, or force it to stop."
    params = [
        Param("application", "Application", choices_source="applications"),
        Param("force", "Force close", kind="choice", choices=[NO, YES], default=NO),
    ]

    def execute(self, ctx, p):
        name = str(p.get("application", "") or "")
        if not name:
            ctx.bus.emit("status", text="Choose an application")
            return
        try:
            apps.close(name, force=str(p.get("force", NO) or NO) == YES)
            ctx.bus.emit("status", text=f"Closed {name}")
        except ValueError as error:
            ctx.bus.emit("status", text=str(error))


@register
class KeyboardShortcut(Action):
    id = "sys.shortcut"
    name = "Keyboard shortcut"
    category = CAT_SYSTEM
    description = (
        "Send a keyboard shortcut to the focused application. Pick a preset to "
        "fill in the shortcut, then edit it if your desktop uses another one."
    )
    params = [
        Param(
            "preset",
            "Preset",
            kind="choice",
            choices=list(keystrokes.PRESET_LABELS),
            default="",
        ),
        Param("shortcut", "Shortcut", default="ctrl+c"),
    ]
    running_feedback = False

    def execute(self, ctx, p):
        try:
            keystrokes.send(str(p.get("shortcut", "") or ""))
        except ValueError as error:
            ctx.bus.emit("status", text=str(error))


@register
class ShortcutSwitch(Action):
    id = "sys.shortcut.switch"
    name = "Shortcut switch"
    category = CAT_SYSTEM
    description = (
        "Alternate between two keyboard shortcuts, sending the next one on "
        "each press."
    )
    params = [
        Param("first", "First shortcut", default="ctrl+c"),
        Param("second", "Second shortcut", default="ctrl+v"),
    ]

    # Which shortcut each (profile, page, key) sends next.
    _state: dict[tuple[int, int, int], bool] = {}

    def execute(self, ctx, p):
        key = ctx.key or (0, 0, 0)
        second_turn = self._state.get(key, False)
        shortcut = p.get("second" if second_turn else "first", "")
        self._state[key] = not second_turn
        try:
            keystrokes.send(str(shortcut or ""))
        except ValueError as error:
            ctx.bus.emit("status", text=str(error))

    def feedback(self, ctx, p):
        key = ctx.key or (0, 0, 0)
        return {"badge": "2" if self._state.get(key, False) else "1"}


@register
class PageGo(Action):
    id = "nav.page.go"
    name = "Go to page"
    category = CAT_NAV
    description = "Switch directly to a selected page in the current profile."
    params = [
        Param("page", "Destination page", choices_source="pages"),
    ]

    def execute(self, ctx, p):
        page = str(p.get("page", "") or "")
        if not ctx.controller.set_page_by_name(page):
            ctx.bus.emit(
                "status",
                text=(
                    f"Page not found: {page}"
                    if page
                    else "Choose a destination page"
                ),
            )


@register
class ProfileGo(Action):
    id = "nav.profile.go"
    name = "Change profile"
    category = CAT_NAV
    description = "Switch to a selected profile and its own set of pages."
    params = [
        Param("profile", "Destination profile", choices_source="deck_profiles"),
    ]

    def execute(self, ctx, p):
        name = str(p.get("profile", "") or "")
        if not name:
            ctx.bus.emit("status", text="Choose a destination profile")
            return
        profiles = ctx.controller.config.profiles
        index = next(
            (i for i, profile in enumerate(profiles) if profile.name == name),
            None,
        )
        if index is None:
            ctx.bus.emit("status", text=f"Profile not found: {name}")
            return
        ctx.controller.set_profile(index)


@register
class PageIndicator(Action):
    id = "nav.page.indicator"
    name = "Page indicator"
    category = CAT_NAV
    description = (
        "Show which page is active. Pressing it does nothing, so it can sit "
        "next to the page navigation keys."
    )
    params = [
        Param(
            "show",
            "Show",
            kind="choice",
            choices=[PAGE_SHOW_NUMBER, PAGE_SHOW_NAME, PAGE_SHOW_POSITION],
            default=PAGE_SHOW_POSITION,
        ),
    ]
    immediate = True

    def execute(self, ctx, p):
        """Deliberately does nothing: this key only reports the active page."""

    def feedback(self, ctx, p):
        controller = ctx.controller
        try:
            pages = controller.config.pages
            current = controller.current_page
            name = pages[current].name
        except (IndexError, AttributeError):
            return {}
        mode = str(p.get("show", "") or PAGE_SHOW_POSITION)
        if mode == PAGE_SHOW_NAME:
            display = name
        elif mode == PAGE_SHOW_NUMBER:
            display = str(current + 1)
        else:
            display = f"{current + 1}/{len(pages)}"
        return {"display": display}


apply_default_icons({
    "sys.command": "mdi:console",
    "sys.url": "mdi:web",
    "sys.wait": "mdi:timer-sand",
    "sys.audio": "mdi:music-note",
    "sys.timer": "mdi:timer-outline",
    "sys.stopwatch": "mdi:clock-outline",
    "sys.media": "mdi:play-pause",
    "sys.counter": "mdi:counter",
    "sys.volume": "mdi:volume-high",
    "sys.audio_device": "mdi:speaker-multiple",
    "sys.open": "mdi:folder-open",
    "sys.app.open": "mdi:rocket-launch",
    "sys.app.close": "mdi:close-box",
    "sys.shortcut": "mdi:keyboard",
    "sys.shortcut.switch": "mdi:keyboard-outline",
    "nav.page.next": "mdi:page-next",
    "nav.page.previous": "mdi:page-previous",
    "nav.page.go": "mdi:book-open-page-variant",
    "nav.page.indicator": "mdi:book-open-variant",
    "nav.profile.go": "mdi:account-switch",
})
