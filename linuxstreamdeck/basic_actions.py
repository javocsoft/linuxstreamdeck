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
class PageGo(Action):
    id = "nav.page"
    name = "Go to page"
    category = CAT_NAV
    description = "Switch the active deck page."
    params = [
        Param("mode", "Mode", kind="choice", default="go to",
              choices=["go to", "next", "previous"]),
        Param("page", "Page (for 'go to')", choices_source="pages"),
    ]

    def execute(self, ctx, p):
        c = ctx.controller
        mode = p.get("mode", "go to")
        if mode == "next":
            c.set_page((c.current_page + 1) % len(c.config.pages))
        elif mode == "previous":
            c.set_page((c.current_page - 1) % len(c.config.pages))
        else:
            c.set_page_by_name(p.get("page", ""))


apply_default_icons({
    "sys.command": "mdi:console",
    "sys.url": "mdi:web",
    "sys.wait": "mdi:timer-sand",
    "sys.audio": "mdi:music-note",
    "nav.page": "mdi:book-open-page-variant",
})
