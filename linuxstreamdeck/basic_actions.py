"""System actions and navigation between deck pages."""

from __future__ import annotations

import logging
import subprocess
import webbrowser

from .core.actions import Action, Param, apply_default_icons, register

log = logging.getLogger(__name__)

CAT_SYSTEM = "System"
CAT_NAV = "Navigation"


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
    "nav.page": "mdi:book-open-page-variant",
})
