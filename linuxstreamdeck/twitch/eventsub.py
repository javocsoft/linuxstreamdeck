"""The live connection to Twitch: one WebSocket, six subscriptions.

This is the first persistent socket in this application other than OBS, and it
behaves differently from a request client in three ways that matter.

**Silence is a failure.** Twitch sends a keepalive whenever nothing else has
arrived, so going quiet for longer than the session's own timeout means the
connection is gone even though the socket still looks open. A watchdog is the
only thing that notices; without it the deck would sit there reporting nothing
and looking perfectly healthy.

**Subscriptions belong to a session, not to the connection.** They are created
against the `session_id` from the welcome message and are lost when it ends, so
every fresh session recreates them — except across a `session_reconnect`, where
Twitch carries them over and recreating would only duplicate.

**It runs while the deck does.** The thread is joined on shutdown before the
client that issues its requests goes away.
"""

from __future__ import annotations

import json
import logging
import threading
import time

import websocket

from . import events
from .constants import HELIX_BASE
from .http import TwitchError

log = logging.getLogger(__name__)

WS_URL = "wss://eventsub.wss.twitch.tv/ws"

# How long a recv may block before the loop looks at the stop flag again.
RECV_TIMEOUT = 1.0
# Twitch states its own keepalive interval in the welcome message; this is only
# the fallback for one that never said.
DEFAULT_KEEPALIVE = 10.0
# How much longer than that interval to wait before calling the session dead.
# One missed keepalive is a hiccup; two is a connection that has gone.
KEEPALIVE_GRACE = 2.0

# Reconnect backoff. It starts quickly because the common case is a blip, and
# tops out low enough that a stream coming back is noticed within a minute.
RECONNECT_MIN = 2.0
RECONNECT_MAX = 30.0

# Every subscription this asks for: type, version, and how its condition names
# the channel. `moderator_user_id` is the same account acting on itself, which
# is what `moderator:read:followers` authorizes.
# The fourth element is the permission Twitch requires. It is carried here
# because Twitch refuses a subscription the token cannot have with a flat
# "subscription missing proper authorization", which names nothing anyone can
# act on; the log has to say which permission and how to grant it.
SUBSCRIPTIONS = (
    ("channel.chat.message", "1", ("broadcaster_user_id", "user_id"),
     "user:read:chat"),
    ("channel.follow", "2", ("broadcaster_user_id", "moderator_user_id"),
     "moderator:read:followers"),
    ("channel.subscribe", "1", ("broadcaster_user_id",),
     "channel:read:subscriptions"),
    ("channel.subscription.message", "1", ("broadcaster_user_id",),
     "channel:read:subscriptions"),
    ("channel.subscription.gift", "1", ("broadcaster_user_id",),
     "channel:read:subscriptions"),
    # A raid names the receiving side, and only one side may be given.
    ("channel.raid", "1", ("to_broadcaster_user_id",), ""),
)


class EventSubSession:
    """One live connection, restarted for as long as it is wanted."""

    def __init__(self, client, bus, on_alert) -> None:
        self.client = client              # TwitchClient, for requests and ids
        self.bus = bus
        self._on_alert = on_alert
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self._connected = False
        self._lock = threading.Lock()

    # ---------- lifecycle ----------

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._connected

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stopping.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="twitch-eventsub"
        )
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        """Signal the loop and wait for it, so no request outlives the client."""
        self._stopping.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=timeout)
        self._set_connected(False)

    def _run(self) -> None:
        delay = RECONNECT_MIN
        url = WS_URL
        while not self._stopping.is_set():
            if not self.client.linked:
                # Nothing to subscribe to yet. Waiting rather than spinning:
                # linking happens in a dialog, at human speed.
                if self._stopping.wait(RECONNECT_MIN):
                    return
                continue
            try:
                url = self._session(url) or WS_URL
                # A session that ended cleanly is not a failure, so the next
                # attempt starts from the short delay again.
                delay = RECONNECT_MIN
            except Exception:
                log.debug("Twitch EventSub session ended", exc_info=True)
                url = WS_URL
                delay = min(delay * 2, RECONNECT_MAX)
            self._set_connected(False)
            if self._stopping.wait(delay):
                return

    # ---------- one session ----------

    def _session(self, url: str) -> str | None:
        """Run one connection to exhaustion; answer a reconnect URL if given."""
        socket = websocket.create_connection(url, timeout=RECV_TIMEOUT)
        try:
            socket.settimeout(RECV_TIMEOUT)
            return self._pump(socket, resuming=url != WS_URL)
        finally:
            try:
                socket.close()
            except Exception:
                log.debug("Could not close the EventSub socket", exc_info=True)

    def _pump(self, socket, resuming: bool) -> str | None:
        deadline = time.monotonic() + DEFAULT_KEEPALIVE + KEEPALIVE_GRACE
        while not self._stopping.is_set():
            try:
                raw = socket.recv()
            except websocket.WebSocketTimeoutException:
                if time.monotonic() >= deadline:
                    # Twitch keeps a healthy session noisy on purpose, so
                    # silence past its own interval means the socket is open
                    # onto nothing.
                    log.info("Twitch EventSub went silent; reconnecting")
                    return None
                continue
            if not raw:
                return None
            try:
                message = json.loads(raw)
            except (TypeError, ValueError):
                log.debug("Unreadable EventSub frame")
                continue
            keepalive, reconnect = self._handle(message, resuming)
            if reconnect:
                return reconnect
            if keepalive:
                deadline = time.monotonic() + keepalive + KEEPALIVE_GRACE
            else:
                deadline = time.monotonic() + DEFAULT_KEEPALIVE + KEEPALIVE_GRACE
        return None

    def _handle(self, message: dict, resuming: bool) -> tuple[float, str | None]:
        metadata = message.get("metadata") or {}
        payload = message.get("payload") or {}
        kind = str(metadata.get("message_type") or "")
        if kind == "session_welcome":
            return self._welcome(payload, resuming), None
        if kind == "session_keepalive":
            return 0.0, None
        if kind == "session_reconnect":
            session = payload.get("session") or {}
            new_url = str(session.get("reconnect_url") or "")
            log.info("Twitch EventSub asked us to move connection")
            # Subscriptions survive this, so the new session must not recreate
            # them or every event would arrive twice.
            return 0.0, new_url or None
        if kind == "revocation":
            subscription = payload.get("subscription") or {}
            log.warning(
                "Twitch revoked the %s subscription (%s)",
                subscription.get("type"), subscription.get("status"),
            )
            return 0.0, None
        if kind == "notification":
            self._notify(metadata, payload)
        return 0.0, None

    def _welcome(self, payload: dict, resuming: bool) -> float:
        session = payload.get("session") or {}
        session_id = str(session.get("id") or "")
        try:
            keepalive = float(session.get("keepalive_timeout_seconds") or 0)
        except (TypeError, ValueError):
            keepalive = 0.0
        if not session_id:
            raise TwitchError("Twitch did not supply an EventSub session")
        if not resuming:
            self._subscribe(session_id)
        self._set_connected(True)
        return keepalive or DEFAULT_KEEPALIVE

    def _subscribe(self, session_id: str) -> None:
        """Create every subscription, tolerating the ones this token cannot.

        A permission the account never granted costs that one subscription, not
        the connection: someone who never authorized subscription reading
        should still get their chat and their raids.
        """
        user_id = self.client.user_id()
        if not user_id:
            raise TwitchError("Twitch did not identify the account")
        wanted = 0
        refused: list[str] = []
        for name, version, fields, scope in SUBSCRIPTIONS:
            condition = {field: user_id for field in fields}
            try:
                self.client.create_subscription(
                    name, version, condition, session_id
                )
                wanted += 1
            except TwitchError as error:
                if scope:
                    refused.append(scope)
                log.info(
                    "No Twitch %s events: %s",
                    name,
                    f"the account has not granted {scope}" if scope else error,
                )
        if refused:
            # Said once, in words, and on the bus rather than only in the log:
            # the keys watching those events are simply silent otherwise, which
            # is indistinguishable from nothing having happened yet.
            missing = ", ".join(sorted(set(refused)))
            self.bus.emit(
                "status",
                text=(
                    f"Twitch has not granted {missing}. Open «Twitch account…» "
                    "and connect again to receive those events."
                ),
            )
        if not wanted:
            raise TwitchError("Twitch accepted none of the event subscriptions")
        log.info("Twitch EventSub listening (%d subscriptions)", wanted)

    def _notify(self, metadata: dict, payload: dict) -> None:
        alert = events.from_notification(
            str(metadata.get("subscription_type") or ""),
            payload.get("event") or {},
            self.client.account,
        )
        if alert is None:
            return
        try:
            self._on_alert(alert)
        except Exception:
            log.exception("Could not deliver a Twitch alert")

    def _set_connected(self, value: bool) -> None:
        with self._lock:
            changed = self._connected != value
            self._connected = value
        if changed:
            self.bus.emit("twitch.live", connected=value)


def subscription_body(
    name: str, version: str, condition: dict, session_id: str
) -> dict:
    """The request body Twitch wants, kept here beside the types it goes with."""
    return {
        "type": name,
        "version": version,
        "condition": dict(condition),
        "transport": {"method": "websocket", "session_id": session_id},
    }


SUBSCRIPTION_URL = f"{HELIX_BASE}/eventsub/subscriptions"
