"""System actions and navigation between deck pages."""

from __future__ import annotations

import logging
import subprocess
import webbrowser

from .core.actions import Action, Param, apply_default_icons, parse_duration, register
from .core.audio import SUPPORTED_AUDIO_EXTENSIONS, play_audio

log = logging.getLogger(__name__)

CAT_SYSTEM = "System"
CAT_NAV = "Navigation"

# Upper bound for a single Wait step. One hour is far beyond any real use and
# prevents a mistaken value from holding an action worker indefinitely.
MAX_WAIT_SECONDS = 3600
TIMER_FINISHED_COLOR = "#8a4b08"


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


apply_default_icons({
    "sys.command": "mdi:console",
    "sys.url": "mdi:web",
    "sys.wait": "mdi:timer-sand",
    "sys.audio": "mdi:music-note",
    "sys.timer": "mdi:timer-outline",
    "sys.stopwatch": "mdi:clock-outline",
    "nav.page.next": "mdi:page-next",
    "nav.page.previous": "mdi:page-previous",
    "nav.page.go": "mdi:book-open-page-variant",
})
