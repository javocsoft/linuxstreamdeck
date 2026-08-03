"""Home Assistant on a key.

Two actions and no more. `homeassistant.turn_on` / `turn_off` / `toggle` work
on anything the server has, so one key covers a light, a switch, a fan, a
media player, a scene and a script without knowing which it is -- and the
second action shows a value, which is the other half of what a wall panel is
for.

What makes these worth having over `web.request`, which could already call the
same endpoints, is the dropdown: the entity is picked from what that server
actually reports. Typing `light.kitchen_ceiling_2` from memory is where the
real failures live -- a key that saves cleanly, looks configured, and does
nothing when pressed.
"""

from __future__ import annotations

import logging

from .core import homeassistant as ha
from .core.actions import Action, Param, apply_default_icons, register

log = logging.getLogger(__name__)

CAT_HA = "Home Assistant"

MODES = (ha.TOGGLE, ha.TURN_ON, ha.TURN_OFF)
MODE_LABELS = {
    ha.TOGGLE: "On / Off",
    ha.TURN_ON: "On (or run it)",
    ha.TURN_OFF: "Off",
}

# How long a value on a key may be before it is asked for again.
REFRESH_CHOICES = ("3", "10", "30", "60")
REFRESH_LABELS = {
    "3": "Every 3 seconds",
    "10": "Every 10 seconds",
    "30": "Every 30 seconds",
    "60": "Every minute",
}
DEFAULT_REFRESH = "10"
NO_VALUE = "--"
# The same blue `obs/actions.py` uses for the active scene. `active` alone only
# lightens the key, which rendered almost indistinguishable from an idle one at
# 96 px -- and telling on from off at a glance is the entire job of this key.
# Written here rather than imported, so the Home Assistant actions do not
# depend on the OBS catalogue.
ON_COLOR = "#1a5fb4"


def refresh_seconds(value) -> float:
    try:
        seconds = float(str(value or DEFAULT_REFRESH))
    except (TypeError, ValueError):
        seconds = float(DEFAULT_REFRESH)
    return seconds if seconds > 0 else float(DEFAULT_REFRESH)


def _client(ctx):
    client = getattr(ctx, "home_assistant", None)
    if client is None:
        raise ha.NotConfigured("Home Assistant is not set up in this session.")
    return client


def _entity(p: dict) -> str:
    return str((p or {}).get("entity") or "")


def _entity_param() -> Param:
    return Param(
        "entity",
        "Entity",
        choices_source="ha_entities",
        placeholder="Pick one, or type its entity id",
    )


class _HomeAssistantAction(Action):
    category = CAT_HA
    # The server is on the network, so a press is a round trip.
    running_feedback = True

    def requires_home_assistant(self, params: dict) -> bool:
        return True


@register
class HomeAssistantSwitch(_HomeAssistantAction):
    id = "ha.switch"
    name = "Switch or run"
    description = (
        "Turn a Home Assistant entity on or off, or run a scene or script. "
        "The key lights up while the entity is on."
    )
    params = [
        _entity_param(),
        Param("mode", "Do what", kind="choice", default=ha.TOGGLE,
              choices=list(MODES), choice_labels=dict(MODE_LABELS)),
    ]

    def execute(self, ctx, p):
        entity = _entity(p)
        state = _client(ctx).act(entity, str(p.get("mode") or ha.TOGGLE))
        # What the server says actually happened, not merely that it accepted
        # the request. Home Assistant answers 200 for a `turn_on` an entity
        # cannot perform, so "no change" is the only sign a key gets that it
        # asked for something impossible.
        ctx.bus.emit(
            "status",
            text=(
                f"{entity} is now {state}" if state
                else f"{entity} reported no change"
            ),
        )

    def feedback(self, ctx, p):
        client = getattr(ctx, "home_assistant", None)
        if client is None:
            return {}
        state = client.cached_state(_entity(p))
        # None means the server has not answered. Drawing that as off would be
        # a light that looks switched off while it is lighting the room.
        if state is None:
            return {}
        on = ha.is_on(state)
        return {"active": on, "color": ON_COLOR if on else None}


@register
class HomeAssistantValue(_HomeAssistantAction):
    id = "ha.state"
    name = "Show a value"
    description = (
        "Show what a Home Assistant entity reports, live on the key: a "
        "temperature, a door, whether the washing machine is running."
    )
    params = [
        _entity_param(),
        Param(
            "refresh",
            "Ask again",
            kind="choice",
            default=DEFAULT_REFRESH,
            choices=list(REFRESH_CHOICES),
            choice_labels=dict(REFRESH_LABELS),
        ),
    ]
    # It exists to be read, so a press only reports; nothing is switched.
    running_feedback = False

    def execute(self, ctx, p):
        entity = _entity(p)
        client = _client(ctx)
        # A press is the one moment somebody is asking, so it is worth a fresh
        # reading rather than whatever the cache last saw.
        client.forget_state(entity)
        state = client.cached_state(entity)
        ctx.bus.emit(
            "status",
            text=(
                f"{entity}: {state}" if state
                else f"{entity} has not answered yet"
            ),
        )

    def feedback(self, ctx, p):
        client = getattr(ctx, "home_assistant", None)
        if client is None:
            return {}
        state = client.cached_state(_entity(p))
        return {"display": _short(state) if state else NO_VALUE}


def _short(state: str) -> str:
    """A state as something a key can show.

    Home Assistant reports a number as a plain string, so the only real work
    is trimming the ones that are words -- `unavailable` is eleven characters
    and would be drawn at half the size of everything else.
    """
    text = " ".join(str(state or "").split())
    return text[:10]


apply_default_icons({
    HomeAssistantSwitch.id: "mdi:home-automation",
    HomeAssistantValue.id: "mdi:home-thermometer",
})
