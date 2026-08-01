"""What is still waiting on you, and for how long.

This is the whole idea behind the alert keys, and it is deliberately not a
counter. "3 messages" says nothing about whether you are being rude; "somebody
has been waiting four minutes" says exactly that, and it is the number that
corresponds to the viewer who writes once, gets nothing back, and does not
return.

So the history is kept once and each key reads it through its own
acknowledgement: two keys watching different things forget independently, and
pressing one never silences another.
"""

from __future__ import annotations

import threading
import time

from .events import Alert, matches

# How many alerts are remembered at all. A busy chat produces them faster than
# anyone reads them, and nothing here ever shows more than a count.
HISTORY_LIMIT = 200
# How long an unacknowledged alert stays interesting. Past this it is not
# waiting on you any more, it is history — and a key that never goes quiet
# again is one nobody looks at.
FORGET_SECONDS = 30 * 60.0

# How the key escalates, in seconds waited. The first is "somebody is there",
# the last is "this is now rude".
URGENCY_STEPS = (30.0, 120.0, 300.0)

# What a key that has never been pressed has acknowledged. It cannot be 0.0:
# monotonic time counts from boot, so on a machine that started a moment ago
# 0.0 is a perfectly ordinary timestamp, and anything stamped before it would
# be silently dropped as already seen.
NEVER_ACKNOWLEDGED = float("-inf")


class Attention:
    """One shared history, read through per-key acknowledgements."""

    def __init__(self, on_change=None) -> None:
        self._lock = threading.Lock()
        self._alerts: list[Alert] = []
        self._acked: dict[object, float] = {}
        self._on_change = on_change

    # ---------- what happened ----------

    def add(self, alert: Alert) -> None:
        with self._lock:
            self._alerts.append(alert)
            if len(self._alerts) > HISTORY_LIMIT:
                del self._alerts[: len(self._alerts) - HISTORY_LIMIT]
        if self._on_change is not None:
            self._on_change(alert)

    def clear(self) -> None:
        """Forget everything, for a profile change or shutdown."""
        with self._lock:
            self._alerts.clear()
            self._acked.clear()

    # ---------- what one key sees ----------

    def pending(self, key, sources, chat_filter: str, now: float | None = None):
        """The alerts this key has not been told about yet, oldest first."""
        moment = time.monotonic() if now is None else now
        with self._lock:
            since = self._acked.get(key, NEVER_ACKNOWLEDGED)
            alerts = list(self._alerts)
        return [
            alert
            for alert in alerts
            if alert.at > since
            and moment - alert.at <= FORGET_SECONDS
            and matches(alert, sources, chat_filter)
        ]

    def acknowledge(self, key, now: float | None = None) -> None:
        """Mark everything up to now as seen, for this key only."""
        with self._lock:
            self._acked[key] = time.monotonic() if now is None else now

    def forget_key(self, key) -> None:
        """Drop a key's acknowledgement, for one that no longer exists."""
        with self._lock:
            self._acked.pop(key, None)


def waiting(alerts, now: float | None = None) -> float:
    """How long the oldest of these has been waiting."""
    if not alerts:
        return 0.0
    moment = time.monotonic() if now is None else now
    return max(0.0, moment - min(alert.at for alert in alerts))


def urgency(seconds: float) -> int:
    """0 to 3, by how long somebody has been left waiting.

    A count cannot say this. Three messages that arrived a second ago are
    fine; one that arrived five minutes ago is the thing worth interrupting
    for, and the key has to look different in those two cases.
    """
    level = 0
    for step in URGENCY_STEPS:
        if seconds >= step:
            level += 1
    return level


def clock(seconds: float) -> str:
    """The wait, in the shortest form that stays readable on a key."""
    total = max(0, int(seconds))
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}:{total % 60:02d}"
    return f"{total // 3600}h{(total % 3600) // 60:02d}"


def should_sound(
    previously_waiting: bool,
    alerts,
    reminded_at: float,
    remind_after: float,
    now: float | None = None,
) -> bool:
    """Whether this arrival is worth making a noise about.

    The mailbox rule, not the keystroke one. A sound per message is unbearable
    the moment a chat wakes up, and the first thing anyone does about that is
    turn it off — which puts them back to missing messages entirely. So it
    sounds when the key goes from quiet to somebody-waiting, and again only if
    a reminder interval has passed with somebody still waiting.
    """
    if not alerts:
        return False
    if not previously_waiting:
        return True
    if remind_after <= 0:
        return False
    moment = time.monotonic() if now is None else now
    return moment - reminded_at >= remind_after
