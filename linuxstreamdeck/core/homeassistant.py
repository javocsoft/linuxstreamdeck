"""Home Assistant, over its REST API.

`web.request` can already talk to Home Assistant, and this exists for the one
thing it cannot do: **fill a dropdown with the entities that server actually
has**. Typing `light.kitchen_ceiling_2` from memory is where the real failures
live -- a key that saves cleanly, looks configured and does nothing when
pressed, which for a stream means during one.

Only the base URL lives in `Config`. The long-lived access token goes to Secret
Service through `HomeAssistantTokenStore`, exactly as the OBS password, the AI
provider keys and the Twitch tokens do, so it never reaches `config.json`, its
backup or a `.lsdconfig` export.

The token is a **long-lived** one, created by hand in Home Assistant's own
profile page. There is deliberately no login flow: Home Assistant's is designed
around a browser redirect back to a registered address, which a desktop
application on somebody's LAN cannot offer, and asking for the account password
instead would be worse than asking for a token that can be revoked on its own.
"""

from __future__ import annotations

import json
import logging
import threading
import time

from . import webrequest

log = logging.getLogger(__name__)

TIMEOUT = 8.0
# What a state a key is drawing may be. Well inside how long anything in a
# house takes to change, and long enough that a page of keys costs one request.
STATE_TTL = 3.0
# Past this a value stops being shown at all: a server that went away leaves a
# dash rather than a reading that stopped being true. The same pair of bounds
# `web.request` and the Twitch snapshot use.
STATE_STALE = 60.0
# How long the entity list is reused. It is fetched on the GTK thread while the
# editor builds a dropdown, and a house has hundreds of entities.
ENTITY_TTL = 60.0

# Services that work on anything, so one key covers a light, a switch, a fan,
# a media player, a scene and a script alike.
TURN_ON = "turn_on"
TURN_OFF = "turn_off"
TOGGLE = "toggle"
# What counts as **off**. Everything else is on.
#
# An exclusion list rather than an inclusion one, because no list of "on"
# states can ever be complete: a media player is `playing`, `paused`, `idle`
# or `buffering`, a vacuum is `cleaning` or `returning`, a cover is `open` or
# `opening`, a climate is `heat` or `cool`. The first version listed the "on"
# ones and read an idle Chromecast as switched off.
#
# Home Assistant's own media player toggle settled on exactly this rule in
# 2025: turn on from `off` or `standby`, turn off from anything else.
OFF_STATES = ("off", "standby", "unavailable", "unknown", "none", "")

# Domains where "toggle" has no meaning: they happen rather than hold a state.
RUN_ONLY_DOMAINS = ("scene", "script", "button", "input_button")


class HomeAssistantError(Exception):
    """Anything that stopped a request from producing an answer."""


class NotConfigured(HomeAssistantError):
    """No server address or no token yet."""


class HomeAssistantClient:
    """One server, its entity list and the states a key is drawing.

    Like `TwitchClient.channel()`, `cached_state()` performs no request: it is
    called from `feedback()` on the single render worker, and a server on the
    network would stall every other key for the latency of the house.
    """

    def __init__(self, store=None, base_url: str = "", on_change=None) -> None:
        self._store = store
        # Called when an entity's state really changes, so the deck can repaint
        # without waiting for the next tick of the live loop. Only on a real
        # change: most polls find the same value, and a full refresh per poll
        # would redraw every key on the page for nothing.
        self.on_change = on_change
        self._lock = threading.Lock()
        self._base = _clean_url(base_url)
        self._token = ""
        self._token_read = False
        self._states: dict[str, tuple[float, str]] = {}
        self._pending: set[str] = set()
        self._entities: tuple[float, list[tuple[str, str]]] = (0.0, [])

    # ---------- configuration ----------

    def configure(self, base_url: str) -> None:
        base = _clean_url(base_url)
        with self._lock:
            if base == self._base:
                return
            self._base = base
            # A different server has different entities and different states.
            self._states.clear()
            self._entities = (0.0, [])

    @property
    def base_url(self) -> str:
        with self._lock:
            return self._base

    def set_token(self, token: str) -> None:
        with self._lock:
            self._token = str(token or "")
            self._token_read = True
            self._states.clear()
            self._entities = (0.0, [])

    def token(self) -> str:
        """The stored token, read from the keyring once and remembered."""
        with self._lock:
            if self._token_read:
                return self._token
        value = self._store.load() if self._store is not None else ""
        with self._lock:
            self._token = str(value or "")
            self._token_read = True
            return self._token

    def configured(self) -> bool:
        return bool(self.base_url) and bool(self.token())

    def forget(self) -> None:
        with self._lock:
            self._token = ""
            self._token_read = True
            self._states.clear()
            self._entities = (0.0, [])

    # ---------- talking to the server ----------

    def _call(self, path: str, method: str = "GET", body: str = ""):
        base, token = self.base_url, self.token()
        if not base:
            raise NotConfigured("No Home Assistant server is set up yet.")
        if not token:
            raise NotConfigured("No Home Assistant token has been saved yet.")
        headers = {"Authorization": f"Bearer {token}"}
        if body:
            headers["Content-Type"] = "application/json"
        try:
            _status, text = webrequest.request(
                f"{base}{path}", method, headers, body, timeout=TIMEOUT
            )
        except webrequest.WebRequestError as error:
            raise HomeAssistantError(_explain(error)) from error
        try:
            return json.loads(text or "null")
        except ValueError as error:
            raise HomeAssistantError(
                "That address answered, but not like Home Assistant does."
            ) from error

    def check(self) -> str:
        """Confirm the address and token work, returning the version."""
        answer = self._call("/api/")
        if not isinstance(answer, dict) or "message" not in answer:
            raise HomeAssistantError(
                "That address answered, but not like Home Assistant does."
            )
        config = self._call("/api/config")
        version = (config or {}).get("version") if isinstance(config, dict) else ""
        return str(version or "")

    def states(self) -> list[dict]:
        answer = self._call("/api/states")
        if not isinstance(answer, list):
            raise HomeAssistantError(
                "That address answered, but not like Home Assistant does."
            )
        return [entry for entry in answer if isinstance(entry, dict)]

    def call_service(self, domain: str, service: str, entity_id: str) -> list:
        """Run a service, returning the states that changed while it ran.

        That list is the only thing that distinguishes a service which did
        something from one the entity quietly ignored, and the REST API hands
        it back for free.
        """
        answer = self._call(
            f"/api/services/{domain}/{service}",
            "POST",
            json.dumps({"entity_id": entity_id}),
        )
        return answer if isinstance(answer, list) else []

    def act(self, entity_id: str, service: str) -> str:
        """Turn something on, off, or over. Returns the entity's new state.

        Addressed to the `homeassistant` domain rather than the entity's own,
        which is what makes one key work on a light, a switch, a fan, a media
        player, a scene and a script without knowing which it is.

        The answer matters as much as the call. Home Assistant accepts
        `turn_on` for **any** entity and returns 200 whether or not the device
        can do it -- a Chromecast that cannot be woken is the case that found
        this -- so a key that only checked for an error reported success while
        nothing happened. Reporting the state the server says it ended in is
        the difference between a key that lies and one that can be debugged.
        """
        entity = str(entity_id or "").strip()
        if not entity or "." not in entity:
            raise HomeAssistantError("This key has no Home Assistant entity.")
        wanted = service if service in (TURN_ON, TURN_OFF, TOGGLE) else TOGGLE
        if wanted == TOGGLE:
            wanted = self._resolve_toggle(entity)
        changed = self.call_service("homeassistant", wanted, entity)
        state = self._new_state(entity, changed)
        if state:
            # The authoritative state, already in hand. Storing it is what
            # makes the key show its new state on the repaint that follows the
            # press, instead of blanking and waiting for a round trip -- the
            # gap in which somebody presses again thinking it failed.
            self._remember(entity, state)
            return state
        # The server did not say what it ended in. Ask, here on the action
        # worker, so the key keeps showing RUN until the answer is in rather
        # than going blank in the meantime.
        self.forget_state(entity)
        try:
            answer = self._call(f"/api/states/{entity}")
            settled = str((answer or {}).get("state") or "")
        except HomeAssistantError:
            return ""
        if settled:
            self._remember(entity, settled)
        return ""

    def _resolve_toggle(self, entity: str) -> str:
        """Turn a toggle into the explicit service this entity needs.

        Decided here rather than left to `homeassistant.toggle`, which reads a
        media player sitting `idle` as switched off and turns it on again --
        so a Chromecast key toggled on and never off. Reading the state first
        costs one request on a press, and it means every domain follows the
        same rule as `is_on()` rather than whatever the generic service makes
        of that domain's vocabulary.

        A scene or a script has nothing to toggle: it happens.
        """
        if entity.split(".", 1)[0] in RUN_ONLY_DOMAINS:
            return TURN_ON
        try:
            answer = self._call(f"/api/states/{entity}")
            state = str((answer or {}).get("state") or "")
        except HomeAssistantError:
            # Unknown is not off. Turning it on is the recoverable guess: the
            # next press has the state and does the right thing, while
            # guessing off leaves somebody pressing a dead key.
            return TURN_ON
        return TURN_OFF if is_on(state) else TURN_ON

    @staticmethod
    def _new_state(entity: str, changed) -> str:
        """The entity's state among what changed, or "" if it is not there.

        Empty is deliberately **not** reported as a failure: an entity that
        was already on does not change either. The caller states the fact and
        leaves the verdict to the person who pressed the key -- the same rule
        the pre-flight board follows.
        """
        for entry in changed or []:
            if isinstance(entry, dict) and entry.get("entity_id") == entity:
                return str(entry.get("state") or "")
        return ""

    # ---------- what a key draws ----------

    def cached_state(self, entity_id: str, now: float | None = None) -> str | None:
        """This entity's last known state, refreshing in the background.

        Never performs a request: `feedback()` runs on the render worker.
        None means "not established", which a key must draw as nothing rather
        than as off.
        """
        entity = str(entity_id or "").strip()
        if not entity or not self.configured():
            return None
        moment = time.monotonic() if now is None else now
        with self._lock:
            fetched, value = self._states.get(entity, (0.0, ""))
            age = moment - fetched
            fresh = bool(value) and age <= STATE_STALE
            start = age >= STATE_TTL and entity not in self._pending
            if start:
                self._pending.add(entity)
        if start and not webrequest.background(self._refresh, entity):
            with self._lock:
                self._pending.discard(entity)
        return value if fresh else None

    def _refresh(self, entity: str) -> None:
        try:
            answer = self._call(f"/api/states/{entity}")
            state = str((answer or {}).get("state") or "")
            if state:
                self._remember(entity, state)
        except HomeAssistantError as error:
            # Keep the last known state; STATE_STALE is what drops it. A house
            # server behind a slow link must not make its key flicker.
            log.debug("Could not read %s: %s", entity, error)
        except Exception:
            log.warning("Unexpected failure reading a state", exc_info=True)
        finally:
            with self._lock:
                self._pending.discard(entity)

    def _remember(self, entity: str, state: str) -> None:
        with self._lock:
            _when, previous = self._states.get(entity, (0.0, ""))
            self._states[entity] = (time.monotonic(), state)
            moved = state != previous
        if moved and self.on_change is not None:
            try:
                self.on_change()
            except Exception:
                log.debug("Could not announce a state change", exc_info=True)

    def forget_state(self, entity: str = "") -> None:
        """Drop what is cached, so the next draw asks again."""
        with self._lock:
            if entity:
                self._states.pop(entity, None)
            else:
                self._states.clear()

    # ---------- the editor's dropdown ----------

    def entities(self, now: float | None = None) -> list[tuple[str, str]]:
        """`(entity_id, friendly name)` for everything the server has.

        Cached because the editor asks for it on the GTK thread while building
        a row, and a house has hundreds of entities.
        """
        if not self.configured():
            return []
        moment = time.monotonic() if now is None else now
        with self._lock:
            taken, found = self._entities
            if found and moment - taken <= ENTITY_TTL:
                return list(found)
        try:
            entries = self.states()
        except HomeAssistantError as error:
            log.debug("Could not list Home Assistant entities: %s", error)
            return []
        fresh = sorted(
            (
                (str(entry.get("entity_id") or ""), _friendly(entry))
                for entry in entries
                if entry.get("entity_id")
            ),
            key=lambda pair: (pair[0].split(".", 1)[0], pair[1].casefold()),
        )
        _labels.update(dict(fresh))
        with self._lock:
            self._entities = (moment, fresh)
            # The list carries every state, so a dropdown refresh doubles as a
            # free update of everything a key on this page might be drawing.
            for entry in entries:
                entity = str(entry.get("entity_id") or "")
                state = str(entry.get("state") or "")
                if entity and state:
                    self._states[entity] = (moment, state)
        return list(fresh)

    def entity_label(self, entity_id: str) -> str:
        return entity_label(entity_id)


# Filled by `entities()` so the editor can show a friendly name while a key
# stores the entity id. A module-level map rather than a client method because
# `_display_options` is a free function with no way to reach the client, and
# there is exactly one client per application -- the same shape `keylight` uses
# for its own labels.
_labels: dict[str, str] = {}


def entity_label(entity_id: str) -> str:
    """What to show for a stored entity id."""
    name = _labels.get(entity_id, "")
    return f"{name} ({entity_id})" if name else entity_id


def is_on(state) -> bool:
    """Whether this state means the thing is doing something.

    Unknown counts as off, deliberately: a key must not claim a device is on
    because nothing answered.
    """
    return str(state or "").strip().casefold() not in OFF_STATES


def _friendly(entry: dict) -> str:
    attributes = entry.get("attributes") or {}
    name = str(attributes.get("friendly_name") or "").strip()
    return name or str(entry.get("entity_id") or "")


def _clean_url(value: str) -> str:
    """The server address, without a trailing slash or an /api suffix.

    People paste both, and `http://box:8123/api/api/states` fails with a 404
    that says nothing about what went wrong.
    """
    base = str(value or "").strip().rstrip("/")
    if not base:
        return ""
    if "://" not in base:
        base = f"http://{base}"
    if base.endswith("/api"):
        base = base[: -len("/api")]
    return base


def _explain(error: Exception) -> str:
    """A Home Assistant refusal in words somebody can act on."""
    text = str(error)
    if "HTTP 401" in text or "HTTP 403" in text:
        return "Home Assistant refused the token. Create a new one and save it."
    if "HTTP 404" in text:
        return "Home Assistant did not recognise that address."
    return text
