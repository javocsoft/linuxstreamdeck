"""What EventSub reports, reduced to one shape the deck can act on.

Six subscription types arrive with six different payloads; every key that shows
them asks the same three questions — who, when, and does it still need me. So
they are normalized here, once, and nothing downstream has to know how Twitch
words any of it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

# --- what an alert can be about ---

CHAT = "chat"
FOLLOW = "follow"
SUBSCRIBE = "subscribe"
RAID = "raid"

SOURCES = (CHAT, FOLLOW, SUBSCRIBE, RAID)
SOURCE_LABELS = {
    CHAT: "Chat messages",
    FOLLOW: "New followers",
    SUBSCRIBE: "Subscriptions",
    RAID: "Raids",
}

# --- which chat messages are worth interrupting for ---

FILTER_ALL = "all"
FILTER_ATTENTION = "attention"
FILTER_FIRST = "first"

CHAT_FILTERS = (FILTER_ALL, FILTER_ATTENTION, FILTER_FIRST)
CHAT_FILTER_LABELS = {
    FILTER_ALL: "Every message",
    FILTER_ATTENTION: "Questions and mentions",
    FILTER_FIRST: "First-time chatters",
}


@dataclass(frozen=True)
class Alert:
    """One thing that happened, and whether it is still waiting on you."""

    source: str
    display: str                  # what to call the person
    text: str = ""                # the message, or a description of the event
    user_id: str = ""             # for the avatar
    at: float = field(default_factory=time.monotonic)
    first: bool = False           # their first ever message in this channel
    mention: bool = False         # they said your name
    question: bool = False        # they asked something
    viewers: int = 0              # a raid brought this many

    def waited(self, now: float | None = None) -> float:
        return max(0.0, (time.monotonic() if now is None else now) - self.at)

    def wants_attention(self) -> bool:
        """Whether this is one somebody is actually waiting on an answer for.

        Only chat can be: a follow or a raid is worth knowing about and needs
        nothing back, while a question left hanging is the thing this whole
        feature exists to stop happening.
        """
        return self.source == CHAT and (
            self.question or self.mention or self.first
        )


def matches(alert: Alert, sources, chat_filter: str = FILTER_ALL) -> bool:
    """Whether one key cares about this alert.

    The filter is what makes the same key usable on a channel with three
    viewers and one with three hundred. Unfiltered, "somebody said something"
    stops meaning anything the moment the room is busy; `attention` and `first`
    keep answering.
    """
    if alert.source not in sources:
        return False
    if alert.source != CHAT:
        return True
    if chat_filter == FILTER_FIRST:
        return alert.first
    if chat_filter == FILTER_ATTENTION:
        return alert.question or alert.mention or alert.first
    return True


def describe(alert: Alert) -> str:
    """One line for the status bar, in words rather than field names."""
    if alert.source == FOLLOW:
        return f"{alert.display} followed you"
    if alert.source == SUBSCRIBE:
        return f"{alert.display} subscribed" + (
            f": {alert.text}" if alert.text else ""
        )
    if alert.source == RAID:
        return (
            f"{alert.display} raided with {alert.viewers} "
            + ("viewer" if alert.viewers == 1 else "viewers")
        )
    prefix = "First message from " if alert.first else ""
    return f"{prefix}{alert.display}: {alert.text}" if alert.text else (
        f"{prefix}{alert.display} wrote in chat"
    )


# --- turning an EventSub payload into one of the above ---

def from_notification(
    subscription_type: str, event: dict, channel_login: str = ""
) -> Alert | None:
    """Normalize one notification, or None for a type nothing here shows."""
    if not isinstance(event, dict):
        return None
    builder = _BUILDERS.get(subscription_type)
    return builder(event, channel_login) if builder is not None else None


def _chat(event: dict, channel_login: str) -> Alert:
    message = event.get("message")
    text = ""
    if isinstance(message, dict):
        text = str(message.get("text") or "")
    display = str(
        event.get("chatter_user_name") or event.get("chatter_user_login") or ""
    )
    # Twitch marks a channel's first-ever message from someone with its own
    # message type rather than with a flag, and that is the one nobody can
    # afford to miss.
    first = str(event.get("message_type") or "") == "user_intro"
    lowered = text.casefold()
    return Alert(
        source=CHAT,
        display=display,
        text=text,
        user_id=str(event.get("chatter_user_id") or ""),
        first=first,
        mention=bool(channel_login) and channel_login.casefold() in lowered,
        question="?" in text,
    )


def _follow(event: dict, _channel: str) -> Alert:
    return Alert(
        source=FOLLOW,
        display=str(event.get("user_name") or event.get("user_login") or ""),
        user_id=str(event.get("user_id") or ""),
    )


def _subscribe(event: dict, _channel: str) -> Alert:
    return Alert(
        source=SUBSCRIBE,
        display=str(event.get("user_name") or event.get("user_login") or ""),
        text=_tier(event.get("tier")),
        user_id=str(event.get("user_id") or ""),
    )


def _resubscribe(event: dict, _channel: str) -> Alert:
    months = event.get("cumulative_months")
    detail = _tier(event.get("tier"))
    if months:
        detail = f"{detail}, {months} months".strip(", ")
    return Alert(
        source=SUBSCRIBE,
        display=str(event.get("user_name") or event.get("user_login") or ""),
        text=detail,
        user_id=str(event.get("user_id") or ""),
    )


def _gift(event: dict, _channel: str) -> Alert:
    # An anonymous gifter has no name and no avatar, and saying "Anonymous"
    # is better than a blank key.
    anonymous = bool(event.get("is_anonymous"))
    total = event.get("total") or 1
    return Alert(
        source=SUBSCRIBE,
        display="Anonymous" if anonymous else str(
            event.get("user_name") or event.get("user_login") or ""
        ),
        text=f"gifted {total}",
        user_id="" if anonymous else str(event.get("user_id") or ""),
    )


def _raid(event: dict, _channel: str) -> Alert:
    try:
        viewers = int(event.get("viewers") or 0)
    except (TypeError, ValueError):
        viewers = 0
    return Alert(
        source=RAID,
        display=str(
            event.get("from_broadcaster_user_name")
            or event.get("from_broadcaster_user_login")
            or ""
        ),
        user_id=str(event.get("from_broadcaster_user_id") or ""),
        viewers=viewers,
    )


def _tier(raw) -> str:
    """Twitch numbers tiers 1000/2000/3000; nobody says it that way."""
    return {"1000": "Tier 1", "2000": "Tier 2", "3000": "Tier 3"}.get(
        str(raw or ""), ""
    )


_BUILDERS = {
    "channel.chat.message": _chat,
    "channel.follow": _follow,
    "channel.subscribe": _subscribe,
    "channel.subscription.message": _resubscribe,
    "channel.subscription.gift": _gift,
    "channel.raid": _raid,
}
