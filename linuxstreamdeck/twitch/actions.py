"""Twitch actions: what the channel is doing, and the few things worth a key.

Everything here reports failure by raising. The controller turns that into the
key's own red border and a status message, which is where someone pressing a
key is looking; catching the error and emitting a status instead would leave
the key looking like it had worked.
"""

from __future__ import annotations

import logging

from ..core.actions import REGISTRY, Action, Param, apply_default_icons, register
from ..core.audio import SUPPORTED_AUDIO_EXTENSIONS
from . import attention, events
from .client import ANNOUNCEMENT_COLORS, COMMERCIAL_LENGTHS, uptime_seconds
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
    twitch_scope = "channel:manage:broadcast"
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
    twitch_scope = "channel:manage:broadcast"
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
    twitch_scope = "clips:edit"
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
    twitch_scope = "channel:manage:broadcast"
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


@register
class Commercial(Action):
    id = "twitch.commercial"
    twitch_scope = "channel:edit:commercial"
    twitch_needs_affiliate = True
    name = "Start ad break"
    category = CAT_TWITCH
    description = (
        "Run an ad break of the chosen length. Only Twitch Affiliates and "
        "Partners can run ads; the key is faded for an account that cannot. "
        "The channel also has to be live, and Twitch enforces its own gap "
        "between breaks."
    )
    running_feedback = True
    params = [
        Param(
            "length",
            "Length",
            kind="choice",
            default="60",
            choices=[str(n) for n in COMMERCIAL_LENGTHS],
            choice_labels={
                str(n): (f"{n} seconds" if n < 60 else f"{n // 60} min {n % 60:02d} s")
                for n in COMMERCIAL_LENGTHS
            },
        ),
    ]

    def execute(self, ctx, p):
        try:
            seconds = int(p.get("length") or 60)
        except (TypeError, ValueError):
            seconds = 60
        length, retry_after = _client(ctx).start_commercial(seconds)
        # The cooldown is the useful half. Without it the next press is a
        # refusal nobody saw coming.
        wait = (
            f"; the next one can start in {retry_after // 60}m {retry_after % 60:02d}s"
            if retry_after
            else ""
        )
        ctx.bus.emit("status", text=f"Twitch ad break started ({length}s){wait}")


@register
class Raid(Action):
    id = "twitch.raid"
    twitch_scope = "channel:manage:raids"
    name = "Raid a channel"
    category = CAT_TWITCH
    description = (
        "Offer a raid to another channel, or cancel one already offered. "
        "Twitch opens the countdown on your chat; it never moves anyone by "
        "itself."
    )
    running_feedback = True
    params = [
        Param(
            "mode",
            "Action",
            kind="choice",
            default="start",
            choices=["start", "cancel"],
            choice_labels={"start": "Start a raid", "cancel": "Cancel the raid"},
        ),
        Param(
            "channel",
            "Channel to raid",
            kind="string",
            default="",
            placeholder="Start typing a channel name",
            completion_source="twitch_channels",
        ),
    ]

    def execute(self, ctx, p):
        client = _client(ctx)
        if str(p.get("mode") or "start") == "cancel":
            client.cancel_raid()
            ctx.bus.emit("status", text="Twitch raid cancelled")
            return
        channel = str(p.get("channel", "") or "").strip()
        if not channel:
            raise TwitchError(
                "This key has no channel to raid. Open it and pick one from "
                "the suggestions."
            )
        raided = client.start_raid(channel)
        ctx.bus.emit(
            "status",
            text=(
                f"Raid to «{raided}» offered; confirm it in your Twitch chat "
                "within 90 seconds"
            ),
        )


@register
class Announce(Action):
    id = "twitch.announce"
    twitch_scope = "moderator:manage:announcements"
    name = "Announce in chat"
    category = CAT_TWITCH
    description = "Post a highlighted announcement in your own Twitch chat."
    running_feedback = True
    params = [
        Param("message", "Message", kind="string", default=""),
        Param(
            "color",
            "Highlight",
            kind="choice",
            default="primary",
            choices=list(ANNOUNCEMENT_COLORS),
            choice_labels={
                "primary": "Channel colour",
                "blue": "Blue",
                "green": "Green",
                "orange": "Orange",
                "purple": "Purple",
            },
        ),
    ]

    def execute(self, ctx, p):
        message = str(p.get("message", "") or "").strip()
        if not message:
            raise TwitchError("This key has no announcement to post.")
        _client(ctx).announce(message, str(p.get("color") or "primary"))
        ctx.bus.emit("status", text="Announcement posted in Twitch chat")


# How a waiting key escalates, drawn as a border. Quiet has none; the rest
# climb from "somebody is there" to "this is now rude". They are bright rather
# than dark because a border is thin and sits over a photograph.
ALERT_COLORS = ("", "#5aa0e8", "#e8a33a", "#e8564f")

# Which sources each choice watches. "Everything" exists because most people
# want one key, not four.
ALERT_SOURCES = {
    "chat": (events.CHAT,),
    "followers": (events.FOLLOW,),
    "subscriptions": (events.SUBSCRIBE,),
    "raids": (events.RAID,),
    "everything": events.SOURCES,
}
ALERT_SOURCE_LABELS = {
    "chat": "Chat messages",
    "followers": "New followers",
    "subscriptions": "Subscriptions",
    "raids": "Raids",
    "everything": "Everything",
}


def alert_sources(params: dict) -> tuple:
    return ALERT_SOURCES.get(str((params or {}).get("source") or "chat"), ())


def alert_filter(params: dict) -> str:
    value = str((params or {}).get("chat_filter") or events.FILTER_ALL)
    return value if value in events.CHAT_FILTERS else events.FILTER_ALL


# The whole deck lit at once, for somebody looking at a game rather than at the
# deck. It takes the colour of what arrived, so a flash caught out of the corner
# of an eye already says which of them it was.
FLASH_COLORS = {
    events.CHAT: "#3fa9ff",
    events.FOLLOW: "#4fd06a",
    events.SUBSCRIBE: "#c47bff",
    events.RAID: "#ff8a3d",
}
FLASH_DEFAULT_COLOR = "#ffffff"

# The word that goes on the middle key while the deck is lit. One word, upper
# case and no longer than FLASH_WORD_CHARS on purpose: `compose()` fits centered
# text to the key's width, so a long word is drawn small, and a pulse lasts a
# fraction of a second. Measured at 72 px, "MESSAGE" and "FOLLOWER" came out at
# roughly half the size of "CHAT" and "RAID" -- readable if peered at, which is
# exactly what somebody deep in a game is not doing. The colour carries the rest
# of the message anyway.
FLASH_WORD_CHARS = 6
FLASH_WORDS = {
    events.CHAT: "CHAT",
    events.FOLLOW: "FOLLOW",
    events.SUBSCRIBE: "SUB",
    events.RAID: "RAID",
}
FLASH_DEFAULT_WORD = "TWITCH"


def alert_flashes(params: dict) -> bool:
    """Whether this key lights the whole deck when something arrives."""
    return str((params or {}).get("flash") or "no") == "yes"


def alert_flash_color(params: dict, alert) -> str:
    """The colour the deck lights up in: the chosen one, or one per event."""
    return _hex_color((params or {}).get("flash_color")) or FLASH_COLORS.get(
        getattr(alert, "source", ""), FLASH_DEFAULT_COLOR
    )


def alert_flash_word(alert) -> str:
    """What the middle key says while the deck is lit."""
    return FLASH_WORDS.get(getattr(alert, "source", ""), FLASH_DEFAULT_WORD)


def _hex_color(value) -> str:
    """A '#rgb' / '#rrggbb' colour, or "" for anything else.

    Checked here rather than left to the renderer: this reaches Pillow on a
    worker thread, and a colour typed with a digit missing would raise there
    rather than simply not being used.
    """
    color = str(value or "").strip().lower()
    if len(color) not in (4, 7) or not color.startswith("#"):
        return ""
    return color if all(c in "0123456789abcdef" for c in color[1:]) else ""


def alert_matches(params: dict, alert) -> bool:
    """Whether a key configured this way cares about this alert.

    Public because the controller asks it when an alert arrives, to decide
    whether that key should make a noise — the same question the key answers
    when it draws itself.
    """
    return events.matches(alert, alert_sources(params), alert_filter(params))


@register
class AlertKey(Action):
    id = "twitch.alert"
    name = "Chat and event alerts"
    category = CAT_TWITCH
    description = (
        "Light up when somebody is waiting on you: a chat message, a new "
        "follower, a subscription or a raid. Shows who it was, how long they "
        "have waited and how many are unread, with an optional sound. Press "
        "it to mark them seen."
    )
    params = [
        Param(
            "source",
            "Watch for",
            kind="choice",
            default="chat",
            choices=list(ALERT_SOURCES),
            choice_labels=dict(ALERT_SOURCE_LABELS),
        ),
        Param(
            "chat_filter",
            "Which chat messages",
            kind="choice",
            default=events.FILTER_ALL,
            choices=list(events.CHAT_FILTERS),
            choice_labels=dict(events.CHAT_FILTER_LABELS),
            # It answers a question only chat has, so it is hidden while the
            # key is watching anything else.
            depends_on="source",
            depends_values=["chat", "everything"],
        ),
        Param(
            "sound",
            "Sound",
            kind="file",
            default="",
            file_filter_name="Audio files",
            extensions=list(SUPPORTED_AUDIO_EXTENSIONS),
            placeholder="No sound",
        ),
        Param("volume", "Volume", kind="int", default=70,
              minimum=0, maximum=100, step=5),
        Param(
            "flash",
            "Flash the whole deck",
            kind="choice",
            default="no",
            choices=["no", "yes"],
            choice_labels={"no": "No", "yes": "Yes"},
        ),
        Param(
            "flash_color",
            "Flash colour",
            kind="string",
            default="",
            # Blank is a meaningful answer here rather than an unfinished one,
            # so the empty field says what it does -- and names the format
            # while it is at it, since anything else falls silently back to it.
            placeholder="A colour for each kind of event, or #ff8a3d",
            # It only means anything while the flash is on.
            depends_on="flash",
            depends_values=["yes"],
        ),
        Param(
            "remind_after",
            "Remind again after",
            kind="optional_duration",
            default="",
            placeholder="Only once, when the first one arrives",
        ),
        Param(
            "avatar",
            "Show who it was",
            kind="choice",
            default="yes",
            choices=["yes", "no"],
            choice_labels={"yes": "Yes", "no": "No"},
        ),
    ]

    def execute(self, ctx, p):
        """Pressing it means "I have seen them"."""
        attention = _attention(ctx)
        pending = self._pending(ctx, p)
        if attention is not None and ctx.key is not None:
            attention.acknowledge(ctx.key)
        if not pending:
            ctx.bus.emit("status", text="Nothing is waiting on Twitch")
            return
        newest = pending[-1]
        ctx.bus.emit(
            "status",
            text=(
                f"{events.describe(newest)}"
                + (f" (+{len(pending) - 1} more)" if len(pending) > 1 else "")
            ),
        )

    def feedback(self, ctx, p):
        pending = self._pending(ctx, p)
        if not pending:
            return {}
        waited = attention.waiting(pending)
        level = attention.urgency(waited)
        state = {
            "display": attention.clock(waited),
            "badge": str(len(pending)) if len(pending) > 1 else "",
            # Breathing is what catches the eye from the corner of it. It
            # lightens rather than tinting, because this key's colour is the
            # message: tinting it towards the accent turned "waiting five
            # minutes" red into a calm blue.
            "pulse": level >= 1,
        }
        # A border rather than a background: this key usually carries the
        # waiting person's picture, and a background change behind a photograph
        # is invisible — the same reason the failure mark is a border.
        color = ALERT_COLORS[min(level, len(ALERT_COLORS) - 1)]
        if color:
            state["border"] = color
        if str(p.get("avatar", "yes")) != "no":
            picture = self._avatar(ctx, pending[0])
            if picture:
                state["image"] = picture
        return state

    @staticmethod
    def _pending(ctx, p):
        runtime = _attention(ctx)
        if runtime is None or ctx.key is None:
            return []
        return runtime.pending(ctx.key, alert_sources(p), alert_filter(p))

    @staticmethod
    def _avatar(ctx, alert):
        """The waiting person's picture, from the cache only.

        This runs on a render worker while the key image is being composed, so
        it must never reach for the network; the fetch was started when the
        alert arrived.
        """
        twitch = getattr(ctx, "twitch", None)
        if twitch is None or not alert.user_id:
            return None
        try:
            return twitch.cached_avatar(alert.user_id)
        except Exception:
            log.debug("Could not read a cached avatar", exc_info=True)
            return None


def _attention(ctx):
    """The shared alert history, or None when nothing is listening."""
    controller = getattr(ctx, "controller", None)
    return getattr(controller, "attention", None)


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
    "twitch.commercial": "mdi:currency-usd",
    "twitch.raid": "mdi:rocket-launch-outline",
    "twitch.announce": "mdi:bullhorn-outline",
    "twitch.alert": "mdi:message-alert",
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
