"""Elgato Key Lights on a key.

Whoever owns a Stream Deck very often owns a Key Light too, and on Linux
neither of them has any software from Elgato at all. The lights speak plain
HTTP on the local network, so this needs no account and no cloud service.

Three actions rather than one with a mode for everything: the action picker
searches names and descriptions, so "Light on/off", "Light brightness" and
"Light temperature" are each found by the words somebody would actually type.
"""

from __future__ import annotations

import logging

from .core import keylight
from .core.actions import Action, Param, apply_default_icons, register

log = logging.getLogger(__name__)

CAT_LIGHTS = "Lights"

MODE_TOGGLE = "toggle"
MODE_ON = "on"
MODE_OFF = "off"
POWER_MODES = (MODE_TOGGLE, MODE_ON, MODE_OFF)
POWER_LABELS = {
    MODE_TOGGLE: "On / Off", MODE_ON: "On", MODE_OFF: "Off",
}

MODE_UP = "up"
MODE_DOWN = "down"
MODE_SET = "set"
LEVEL_MODES = (MODE_UP, MODE_DOWN, MODE_SET)
BRIGHTNESS_LABELS = {
    MODE_UP: "Brighter", MODE_DOWN: "Dimmer", MODE_SET: "Set to",
}
TEMPERATURE_LABELS = {
    MODE_UP: "Warmer", MODE_DOWN: "Cooler", MODE_SET: "Set to",
}

DEFAULT_BRIGHTNESS_STEP = 10
DEFAULT_KELVIN_STEP = 500

_LIGHT_PARAM = dict(
    name="light",
    label="Light",
    choices_source="key_lights",
    placeholder="A Key Light on this network, or its address",
)


def _light_param() -> Param:
    return Param(**_LIGHT_PARAM)


def _target(p: dict) -> str:
    return str((p or {}).get("light") or "")


def _report(ctx, fresh: dict) -> None:
    ctx.bus.emit(
        "status",
        text=(
            f"Light {'on' if fresh['on'] else 'off'} · "
            f"{fresh['brightness']}% · {fresh['kelvin']}K"
        ),
    )


class _LightAction(Action):
    """What the three of them share: the same key drawing and the same
    refusal to reach the network while being drawn."""

    category = CAT_LIGHTS
    # A light is on the network, so a press is a round trip. Without this a
    # single-action key looks like nothing happened until it comes back.
    running_feedback = True

    def feedback(self, ctx, p):
        current = keylight.cached_state(_target(p))
        # None means the light has not answered. Drawing that as "off" would
        # be a light that looks switched off while it is lighting the room.
        if current is None:
            return {}
        return {"active": bool(current.get("on"))}


@register
class LightPower(_LightAction):
    id = "light.power"
    name = "Light on/off"
    description = (
        "Switch an Elgato Key Light on or off. The key lights up while the "
        "light is on."
    )
    params = [
        _light_param(),
        Param("mode", "Do what", kind="choice", default=MODE_TOGGLE,
              choices=list(POWER_MODES), choice_labels=dict(POWER_LABELS)),
    ]

    def execute(self, ctx, p):
        target = _target(p)
        mode = str(p.get("mode") or MODE_TOGGLE)
        if mode == MODE_TOGGLE:
            # Asked rather than remembered: the light may have been switched
            # from Elgato's own app, a phone, or the button on its back.
            wanted = not keylight.state(target)["on"]
        else:
            wanted = mode == MODE_ON
        _report(ctx, keylight.apply(target, on=wanted))


@register
class LightBrightness(_LightAction):
    id = "light.brightness"
    name = "Light brightness"
    description = "Make an Elgato Key Light brighter or dimmer, or set a level."
    params = [
        _light_param(),
        Param("mode", "Do what", kind="choice", default=MODE_UP,
              choices=list(LEVEL_MODES),
              choice_labels=dict(BRIGHTNESS_LABELS)),
        Param("amount", "Brightness (%)", kind="int",
              default=DEFAULT_BRIGHTNESS_STEP,
              minimum=keylight.MIN_BRIGHTNESS, maximum=keylight.MAX_BRIGHTNESS,
              step=5),
    ]

    def execute(self, ctx, p):
        target = _target(p)
        mode = str(p.get("mode") or MODE_UP)
        amount = int(p.get("amount") or DEFAULT_BRIGHTNESS_STEP)
        if mode == MODE_SET:
            level = amount
        else:
            current = keylight.state(target)["brightness"]
            level = current + (amount if mode == MODE_UP else -amount)
        _report(ctx, keylight.apply(target, brightness=level))


@register
class LightTemperature(_LightAction):
    id = "light.temperature"
    name = "Light temperature"
    description = (
        "Make an Elgato Key Light warmer or cooler, or set a colour "
        f"temperature between {keylight.MIN_KELVIN}K and "
        f"{keylight.MAX_KELVIN}K."
    )
    params = [
        _light_param(),
        Param("mode", "Do what", kind="choice", default=MODE_UP,
              choices=list(LEVEL_MODES),
              choice_labels=dict(TEMPERATURE_LABELS)),
        Param("amount", "Temperature (K)", kind="int",
              default=DEFAULT_KELVIN_STEP,
              minimum=0, maximum=keylight.MAX_KELVIN,
              step=keylight.KELVIN_STEP),
    ]

    def execute(self, ctx, p):
        target = _target(p)
        mode = str(p.get("mode") or MODE_UP)
        amount = int(p.get("amount") or DEFAULT_KELVIN_STEP)
        if mode == MODE_SET:
            kelvin = amount
        else:
            current = keylight.state(target)["kelvin"]
            # "Warmer" is a lower colour temperature, and the device's own
            # unit runs the other way again. Both inversions live here so the
            # rest of the module can stay in the direction people mean.
            kelvin = current + (-amount if mode == MODE_UP else amount)
        _report(ctx, keylight.apply(target, kelvin=kelvin))


apply_default_icons({
    LightPower.id: "mdi:lightbulb-on-outline",
    LightBrightness.id: "mdi:brightness-6",
    LightTemperature.id: "mdi:thermometer",
})
