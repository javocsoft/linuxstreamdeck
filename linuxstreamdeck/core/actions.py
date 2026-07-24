"""Action system: base class, declarative parameters and global registry.

Each action declares its parameters with `Param`; the UI editor generates the
widgets from them automatically. `choices_source` tells the editor where to pull
the live options from (OBS scenes, audio inputs...).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class Param:
    name: str
    label: str
    kind: str = "string"          # string | int | float | choice | duration
    default: Any = None
    choices: list[str] = field(default_factory=list)   # for kind == "choice"
    # Dynamic source of options that the editor fills in live:
    #   scenes | inputs | media_inputs | transitions | scene_collections
    #   profiles | sources_in_scene | filters_of_source | hotkeys | pages
    choices_source: str = ""


# --- duration parameters (kind == "duration") ---
# Stored and shown as a "MM:SS" string; the editor renders a small time field.

def parse_duration(text) -> int:
    """Parse a duration into whole seconds. Accepts 'MM:SS' (or 'H:MM:SS'), a
    plain number of seconds, or empty. Tolerant of blanks and bad input → 0."""
    if text is None:
        return 0
    s = str(text).strip()
    if not s:
        return 0
    try:
        if ":" in s:
            total = 0
            for part in s.split(":"):        # left→right, each part is 60× the next
                total = total * 60 + max(0, int(part.strip() or 0))
            return total
        return max(0, int(float(s)))
    except (ValueError, TypeError):
        return 0


def format_duration(seconds) -> str:
    """Format whole seconds as 'MM:SS' (minutes grow past 99 if needed)."""
    try:
        total = max(0, int(seconds))
    except (ValueError, TypeError):
        total = 0
    return f"{total // 60:02d}:{total % 60:02d}"


class ActionContext:
    """Everything an action needs to run."""

    def __init__(self, obs, controller, bus):
        self.obs = obs                # linuxstreamdeck.obs.client.OBSClient
        self.controller = controller  # linuxstreamdeck.core.controller.DeckController
        self.bus = bus


class Action:
    id: str = ""
    name: str = ""
    category: str = ""
    params: list[Param] = []
    description: str = ""
    default_icon: str = ""      # "mdi:..." reference used when the key has no icon of its own

    def execute(self, ctx: ActionContext, params: dict) -> None:
        raise NotImplementedError

    def feedback(self, ctx: ActionContext, params: dict) -> dict | None:
        """Visual state of the key: {'active': bool, 'color': '#rrggbb', 'badge': str}.

        Returns None if the action has no state.
        """
        return None


REGISTRY: dict[str, Action] = {}


def register(cls: type[Action]) -> type[Action]:
    """Decorator: instantiate and register the action."""
    inst = cls()
    if not inst.id:
        raise ValueError(f"Action {cls.__name__} has no id")
    REGISTRY[inst.id] = inst
    return cls


def get(action_id: str) -> Action | None:
    return REGISTRY.get(action_id)


def apply_default_icons(mapping: dict[str, str]) -> None:
    """Assign the default icon (mdi:...) to already-registered actions."""
    for action_id, ref in mapping.items():
        action = REGISTRY.get(action_id)
        if action is not None:
            action.default_icon = ref


def by_category() -> dict[str, list[Action]]:
    cats: dict[str, list[Action]] = {}
    for a in REGISTRY.values():
        cats.setdefault(a.category, []).append(a)
    return cats
