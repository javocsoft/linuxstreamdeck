"""Twitch actions: what the channel is doing, and the few things worth a key.

Everything here reports failure by raising. The controller turns that into the
key's own red border and a status message, which is where someone pressing a
key is looking; catching the error and emitting a status instead would leave
the key looking like it had worked.
"""

from __future__ import annotations

import logging

from ..core.actions import REGISTRY, Action, Param, apply_default_icons, register
from .client import uptime_seconds
from .http import TwitchError

log = logging.getLogger(__name__)

CAT_TWITCH = "Twitch"

# Shown where a measurement genuinely has no value: not linked, or an outage
# long enough that the last reading stopped being true.
NO_VALUE = "--"

# Shown where the channel is confirmed off air, which is an answer rather than
# an absence. It is Twitch's own word — a channel page says "Offline" — so it
# needs no translating by whoever reads the key. "OFF" was tried first and only
# works on the live/offline key, where LIVE beside it supplies the meaning;
# over a label like "Viewers" it reads as the key being switched off.
OFF_AIR = "OFFLINE"

STAT_OK_COLOR = "#1f2d1f"
STAT_WARN_COLOR = "#4a3a12"
STAT_LIVE_COLOR = "#3a1220"

# How often a Twitch statistics key is recomposed. The client's own cache
# decides when a request actually happens, so this only governs repainting.
STATS_REFRESH_SECONDS = 2.0


def _count(value: float) -> str:
    """A number that has to fit on a key.

    Above ten thousand the exact figure stops being readable at this size and
    stops mattering, so it becomes "12k"; below that every digit is kept,
    because the difference between 90 and 900 viewers is the whole message.
    """
    number = int(value)
    if number >= 10000:
        return f"{number / 1000:.0f}k"
    return str(number)


def _clock(seconds: float) -> str:
    total = max(0, int(seconds))
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


# Each metric: how it is labelled, how a snapshot becomes a value, how that
# value becomes text, and what to show when the channel is off air. One table,
# so adding a measurement is one entry.
#
# `offline` is what separates an answer from a gap. Without it a viewer count
# read "--" both when the channel was off air and when the connection had gone
# quiet, which are entirely different messages: one says "you are not
# streaming", the other says "I cannot tell you". A metric with no `offline`
# entry genuinely has no answer off air, and keeps NO_VALUE.
STAT_METRICS: dict[str, dict] = {
    "viewers": {
        "label": "Viewers",
        "icon": "mdi:account-group",
        "read": lambda s: s.get("viewers") if s.get("live") else None,
        "text": _count,
        # Not "0". Nobody watching a live channel is a real and alarming state,
        # and it must not look identical to not being on air at all.
        "offline": OFF_AIR,
    },
    "followers": {
        "label": "Followers",
        "icon": "mdi:account-heart",
        # Answers while the channel is offline: a follower count is a property
        # of the channel, not of the broadcast. No `offline` text, so a missing
        # one stays NO_VALUE — that really is a gap, usually a missing scope.
        "read": lambda s: s.get("followers"),
        "text": _count,
    },
    "uptime": {
        "label": "Stream uptime",
        "icon": "mdi:timer-outline",
        "read": uptime_seconds,
        "text": _clock,
        "offline": OFF_AIR,
    },
    "status": {
        "label": "Live or offline",
        "icon": "mdi:broadcast",
        # `live` is a real answer either way, so this reader returns the
        # snapshot itself and lets the formatter speak. Returning None for
        # "offline" would render it as "no data", which is a different claim.
        "read": lambda s: s if s else None,
        # The same word the other two metrics use, so two keys side by side
        # cannot appear to be saying different things about one channel.
        "text": lambda s: "LIVE" if s.get("live") else OFF_AIR,
        "color": lambda s: STAT_LIVE_COLOR if s.get("live") else "",
    },
}


@register
class Stats(Action):
    id = "twitch.stats"
    name = "Twitch statistics"
    category = CAT_TWITCH
    description = (
        "Show a live Twitch measurement on the key: viewers, followers, "
        "stream uptime, or whether the channel is live."
    )
    params = [
        Param(
            "metric",
            "Measurement",
            kind="choice",
            default="viewers",
            choices=list(STAT_METRICS),
            choice_labels={
                key: metric["label"] for key, metric in STAT_METRICS.items()
            },
        ),
        Param(
            "colored",
            "Color the key",
            kind="choice",
            default="yes",
            choices=["yes", "no"],
            choice_labels={"yes": "Yes", "no": "No"},
        ),
    ]

    def execute(self, ctx, p):
        """A statistics key exists to be read, so a press states it in words.

        A key that does nothing at all when pressed feels broken, and the
        window is often hidden, so this is the one place the full label and
        value appear together.
        """
        metric = STAT_METRICS.get(p.get("metric") or "viewers")
        if metric is None:
            return
        value = self._text(ctx, metric)
        name = metric["label"].lower()
        if value == NO_VALUE:
            text = f"The Twitch {name} is not available right now"
        elif value == metric.get("offline"):
            # The key has room for one word; the message has room to say why.
            text = f"The channel is not live, so there is no {name} to show"
        else:
            text = f"Twitch {name}: {value}"
        ctx.bus.emit("status", text=text)

    def feedback(self, ctx, p):
        metric = STAT_METRICS.get(p.get("metric") or "viewers")
        if metric is None:
            return {}
        snapshot = self._snapshot(ctx)
        raw = self._read(metric, snapshot)
        state = {"display": self._format(metric, raw, snapshot)}
        if raw is not None and str(p.get("colored", "yes")) != "no":
            painter = metric.get("color")
            color = ""
            if painter is not None:
                try:
                    color = painter(raw)
                except (AttributeError, TypeError, ValueError):
                    color = ""
            if color:
                state["color"] = color
        return state

    @staticmethod
    def _snapshot(ctx) -> dict | None:
        """What is currently known about the channel, or None when nothing is.

        `channel()` never performs a request: this runs on a render worker
        while a key image is being composed, and a round trip to Twitch there
        would hold that worker for every repaint. It answers an empty mapping
        once a reading has gone stale, which is why that becomes None here —
        the key must be able to tell "off air" from "no longer being told".
        """
        twitch = getattr(ctx, "twitch", None)
        if twitch is None or not twitch.linked:
            return None
        return twitch.channel() or None

    @staticmethod
    def _read(metric: dict, snapshot: dict | None):
        if snapshot is None:
            return None
        try:
            return metric["read"](snapshot)
        except (AttributeError, TypeError, ValueError):
            return None

    def _text(self, ctx, metric: dict) -> str:
        snapshot = self._snapshot(ctx)
        return self._format(metric, self._read(metric, snapshot), snapshot)

    @staticmethod
    def _format(metric: dict, raw, snapshot: dict | None = None) -> str:
        if raw is None:
            # A confirmed off-air channel is an answer; only an unknown one is
            # a gap. Both used to render as NO_VALUE, so a key could not say
            # which of the two it meant.
            if snapshot is not None and not snapshot.get("live"):
                return metric.get("offline", NO_VALUE)
            return NO_VALUE
        try:
            return metric["text"](raw)
        except (AttributeError, TypeError, ValueError):
            return NO_VALUE


@register
class SetTitle(Action):
    id = "twitch.set_title"
    name = "Set stream title"
    category = CAT_TWITCH
    description = "Change the title of the Twitch stream."
    running_feedback = True
    params = [
        Param("title", "Stream title", kind="string", default=""),
    ]

    def execute(self, ctx, p):
        title = str(p.get("title", "") or "")
        _client(ctx).set_title(title)
        ctx.bus.emit("status", text=f"Twitch title set to «{title.strip()}»")


@register
class SetCategory(Action):
    id = "twitch.set_category"
    name = "Set stream category"
    category = CAT_TWITCH
    description = (
        "Change the Twitch category (the game). The name is looked up on "
        "Twitch when the key is pressed, so an approximate one still works."
    )
    running_feedback = True
    params = [
        # A text field with live suggestions rather than a dropdown. Twitch has
        # tens of thousands of categories, so no list short enough to offer
        # could express the one a given person streams; searching as the value
        # is typed covers all of them while still letting the value be picked
        # rather than guessed. It keeps storing the name, so a key configured
        # before the suggestions existed still loads and still works.
        Param(
            "category",
            "Category",
            kind="string",
            default="",
            placeholder="Start typing, for example Just Chatting",
            completion_source="twitch_categories",
        ),
    ]

    def execute(self, ctx, p):
        wanted = str(p.get("category", "") or "").strip()
        if not wanted:
            # An empty category is not an oversight but the editor's answer to
            # text Twitch did not recognise, so this is the message that case
            # produces and it has to say what to do about it.
            raise TwitchError(
                "This key has no Twitch category set. Open it and pick one "
                "from the suggestions."
            )
        matched = _client(ctx).set_category(wanted)
        ctx.bus.emit("status", text=f"Twitch category set to «{matched}»")


@register
class CreateClip(Action):
    id = "twitch.clip"
    name = "Create clip"
    category = CAT_TWITCH
    description = (
        "Clip the last few seconds of the stream, the same as the Twitch clip "
        "button. The channel has to be live."
    )
    running_feedback = True

    def execute(self, ctx, p):
        edit_url = _client(ctx).create_clip()
        ctx.bus.emit(
            "status",
            text=(
                f"Twitch clip created; edit it at {edit_url}"
                if edit_url
                else "Twitch clip created"
            ),
        )


@register
class CreateMarker(Action):
    id = "twitch.marker"
    name = "Create stream marker"
    category = CAT_TWITCH
    description = (
        "Mark this moment in the Twitch broadcast so it is easy to find "
        "afterwards. Pairs with the OBS recording chapter marker."
    )
    running_feedback = True
    params = [
        Param(
            "description",
            "Note",
            kind="string",
            default="",
            placeholder="Optional, shown next to the marker",
        ),
    ]

    def execute(self, ctx, p):
        _client(ctx).create_marker(str(p.get("description", "") or ""))
        ctx.bus.emit("status", text="Twitch stream marker created")


def _client(ctx):
    """The Twitch connection, or a refusal that says what is missing.

    An unlinked account is the overwhelmingly common reason one of these keys
    cannot run, and it is fixable, so it is worth naming rather than letting an
    attribute error reach the log.
    """
    twitch = getattr(ctx, "twitch", None)
    if twitch is None or not twitch.linked:
        raise TwitchError("No Twitch account is linked; connect one first")
    return twitch


apply_default_icons({
    "twitch.stats": "mdi:twitch",
    "twitch.set_title": "mdi:format-title",
    "twitch.set_category": "mdi:gamepad-variant",
    "twitch.clip": "mdi:movie-open-play",
    "twitch.marker": "mdi:bookmark-outline",
})


def _mark_twitch_dependency() -> None:
    """Tell the deck that everything defined here needs a linked account.

    Derived from the category rather than written on each class, for the same
    reason the OBS catalogue does it: an action added later cannot forget the
    flag and render as usable while no account is connected.
    """
    for action in REGISTRY.values():
        if action.category == CAT_TWITCH:
            action.needs_twitch = True


_mark_twitch_dependency()
