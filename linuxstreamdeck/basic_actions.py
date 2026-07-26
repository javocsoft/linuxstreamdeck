"""System actions and navigation between deck pages."""

from __future__ import annotations

import logging
import subprocess
import webbrowser

from .core import apps, keystrokes, media
from .core.actions import Action, Param, apply_default_icons, parse_duration, register
from .core.audio import SUPPORTED_AUDIO_EXTENSIONS, play_audio

log = logging.getLogger(__name__)

CAT_SYSTEM = "System"
CAT_NAV = "Navigation"

# Upper bound for a single Wait step. One hour is far beyond any real use and
# prevents a mistaken value from holding an action worker indefinitely.
MAX_WAIT_SECONDS = 3600
TIMER_FINISHED_COLOR = "#8a4b08"

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
        "Play a local WAV, MP3, OGG, FLAC or Opus file. A multiple-action "
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
    ]
    running_feedback = True
    restart_on_repress = True

    def execute(self, ctx, p):
        play_audio(
            p.get("file", ""),
            p.get("volume", 100),
            parse_duration(p.get("duration")),
            stop_requested=ctx.stop_requested,
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
    ]
    immediate = True

    def execute(self, ctx, p):
        identifier = media.identifier_for(str(p.get("action", "") or ""))
        try:
            media.perform(identifier)
        except ValueError as error:
            ctx.bus.emit("status", text=str(error))


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
