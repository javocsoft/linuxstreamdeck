"""Calling an HTTP endpoint from a key, and showing what it answers.

One action, deliberately generic. A great many single-purpose plugins in the
Stream Deck ecosystem are one REST call with an icon on top -- home automation
bridges, dashboards, webhooks, CI servers, uptime monitors -- so an endpoint
plus a value read out of its answer covers all of them at once, and covers the
next one nobody has written yet.

It is excluded from the catalogue offered to an AI provider, exactly as
`sys.command` and `obs.raw` are: a proposal that could reach an arbitrary
address is not something to accept from generated text.
"""

from __future__ import annotations

import logging

from .core import webrequest
from .core.actions import Action, Param, apply_default_icons, register

log = logging.getLogger(__name__)

CAT_WEB = "Web"

YES = "yes"
NO = "no"

# How often a key showing a value asks again. There is no "never": a key that
# displays something stale for ever is worse than one that does not display
# anything, and `display` already answers "should this key show a value at
# all". The floor is 2 s because every tick is a request over the network.
REFRESH_CHOICES = ("2", "5", "15", "60")
REFRESH_LABELS = {
    "2": "Twice in 5 seconds",
    "5": "Every 5 seconds",
    "15": "Every 15 seconds",
    "60": "Every minute",
}
DEFAULT_REFRESH = "15"


def refresh_seconds(value) -> float:
    """The polling interval this key asked for, as the controller reads it."""
    try:
        seconds = float(str(value or DEFAULT_REFRESH))
    except (TypeError, ValueError):
        seconds = float(DEFAULT_REFRESH)
    return seconds if seconds > 0 else float(DEFAULT_REFRESH)


def shows_value(params: dict) -> bool:
    return str((params or {}).get("display") or NO) == YES


@register
class WebRequest(Action):
    id = "web.request"
    name = "Web request"
    category = CAT_WEB
    description = (
        "Call an HTTP endpoint and, if you want, show a value from its answer "
        "on the key. Covers anything that speaks REST: a home automation "
        "bridge, a webhook, a dashboard, your own server."
    )
    # A request goes over the network, so it can take seconds. Without this a
    # single-action key would look like nothing had happened until it finished.
    running_feedback = True
    params = [
        Param("url", "URL", placeholder="https://example.com/api/state"),
        Param(
            "method",
            "Method",
            kind="choice",
            default="GET",
            choices=list(webrequest.METHODS),
        ),
        Param(
            "headers",
            "Headers",
            placeholder="One per line:  Authorization: Bearer abc123",
        ),
        Param(
            "body",
            "Body",
            placeholder='{"state": "on"}',
            # A GET with a body is legal and almost always a mistake, so the
            # field is hidden rather than dropped for the methods that ignore
            # it -- switching back to POST keeps whatever was typed.
            depends_on="method",
            depends_values=list(webrequest.BODY_METHODS),
        ),
        Param(
            "display",
            "Show the answer on the key",
            kind="choice",
            default=NO,
            choices=[NO, YES],
            choice_labels={NO: "No", YES: "Yes"},
        ),
        Param(
            "value_path",
            "Value to show",
            placeholder="Blank shows the whole answer; or  state  or  data.0.name",
            depends_on="display",
            depends_values=[YES],
        ),
        Param(
            "refresh",
            "Ask again",
            kind="choice",
            default=DEFAULT_REFRESH,
            choices=list(REFRESH_CHOICES),
            choice_labels=dict(REFRESH_LABELS),
            depends_on="display",
            depends_values=[YES],
        ),
    ]

    def execute(self, ctx, p):
        status, text = webrequest.request(
            p.get("url", ""),
            p.get("method", "GET"),
            webrequest.parse_headers(p.get("headers", "")),
            p.get("body", ""),
        )
        # The press is also the freshest reading this key will get, so it
        # replaces the cached one rather than leaving the display a refresh
        # behind what was just done.
        detail = ""
        if shows_value(p):
            value = webrequest.remember(p, text)
            detail = f" · {value}" if value else ""
        ctx.bus.emit(
            "status", text=f"{webrequest.method_for(p.get('method'))} "
                           f"answered HTTP {status}{detail}"
        )

    def feedback(self, ctx, p):
        if not shows_value(p):
            return {}
        value = webrequest.cached_value(p, refresh_seconds(p.get("refresh")))
        return {"display": value} if value else {}


apply_default_icons({WebRequest.id: "mdi:web"})
