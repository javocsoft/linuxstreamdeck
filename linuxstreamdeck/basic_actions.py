"""System actions and navigation between deck pages."""

from __future__ import annotations

import logging
import subprocess
import time
import webbrowser

from .core.actions import Action, Param, apply_default_icons, parse_duration, register

log = logging.getLogger(__name__)

CAT_SYSTEM = "System"
CAT_NAV = "Navigation"

# Upper bound for a single Wait step. It runs on a shared worker thread, so an
# unbounded sleep could starve rendering; one hour is far beyond any real use.
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

    def execute(self, ctx, p):
        seconds = parse_duration(p.get("duration"))
        if seconds > 0:
            time.sleep(min(seconds, MAX_WAIT_SECONDS))


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
    "nav.page": "mdi:book-open-page-variant",
})
