"""Action system: base class, declarative parameters and global registry.

Each action declares its parameters with `Param`; the UI editor generates the
widgets from them automatically. `choices_source` tells the editor where to pull
the live options from (OBS scenes, audio inputs...).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from threading import Event
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class Param:
    name: str
    label: str
    # string | int | float | choice | duration | optional_duration | file
    # ("file" covers a folder too; see `directory` below)
    kind: str = "string"
    default: Any = None
    choices: list[str] = field(default_factory=list)   # for kind == "choice"
    # Readable text for those choices, as {stored value: label}. Only needed
    # when the stored value is an identifier rather than something anyone would
    # want to read; the editor then shows the label and still stores the value,
    # so wording can change without invalidating saved keys.
    choice_labels: dict[str, str] = field(default_factory=dict)
    minimum: float | None = None
    maximum: float | None = None
    step: float = 1
    file_filter_name: str = ""
    extensions: list[str] = field(default_factory=list)
    # For kind == "file": pick a folder instead of a file. Extensions no longer
    # apply, since a directory has none.
    directory: bool = False
    # Hint shown in an empty field. Worth setting when blank is a meaningful
    # value rather than an unfinished one, so the field can say what it means.
    placeholder: str = ""
    # True when the value only narrows the editor's own dropdowns and is never
    # sent anywhere. The audio actions' `scene` is the case: it filters the
    # input list and the action still targets the input globally. A reference
    # checker must not call such a value broken, because nothing breaks.
    advisory: bool = False
    # Dynamic source of options that the editor fills in live:
    #   scenes | inputs | media_inputs | transitions | scene_collections
    #   profiles | sources_in_scene | audio_sources_in_scene
    #   filters_of_source | text_inputs | browser_inputs | hotkeys
    #   pages | deck_profiles | applications
    # The last three are LOCAL_CHOICE_SOURCES: they fill without OBS.
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
    except (ValueError, TypeError, OverflowError):
        return 0


def format_duration(seconds) -> str:
    """Format whole seconds as 'MM:SS' (minutes grow past 99 if needed)."""
    try:
        total = max(0, int(seconds))
    except (ValueError, TypeError, OverflowError):
        total = 0
    return f"{total // 60:02d}:{total % 60:02d}"


class ActionContext:
    """Everything an action needs to run."""

    def __init__(
        self,
        obs,
        controller,
        bus,
        cancellation: Event | None = None,
        key: tuple[int, int, int] | None = None,
    ):
        self.obs = obs                # linuxstreamdeck.obs.client.OBSClient
        self.controller = controller  # linuxstreamdeck.core.controller.DeckController
        self.bus = bus
        self.key = key                # (profile, page, key), when key-specific
        self._cancellation = cancellation

    def for_key(self, key: tuple[int, int, int]) -> "ActionContext":
        return ActionContext(
            obs=self.obs,
            controller=self.controller,
            bus=self.bus,
            cancellation=self._cancellation,
            key=key,
        )

    def stop_requested(self) -> bool:
        """Whether app shutdown or a replacement execution cancelled this run."""
        return (
            bool(self._cancellation and self._cancellation.is_set())
            or self.controller.wait_until_stopped(0)
        )

    def wait_until_stopped(self, timeout: float) -> bool:
        """Wait up to timeout, returning early for shutdown or replacement."""
        deadline = time.monotonic() + max(0.0, timeout)
        while not self.stop_requested():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            if self.controller.wait_until_stopped(min(0.1, remaining)):
                return True
        return True


class Action:
    id: str = ""
    name: str = ""
    category: str = ""
    params: list[Param] = []
    description: str = ""
    default_icon: str = ""      # "mdi:..." reference used when the key has no icon of its own
    running_feedback: bool = False
    restart_on_repress: bool = False
    immediate: bool = False      # press-thread execution; must never block
    # Set when holding a single-action key should do something other than
    # execute(); the controller then waits for the release to tell them apart.
    supports_long_press: bool = False
    # Whether the action can do anything at all without the OBS connection. The
    # deck dims a key that cannot, because an OBS key with OBS closed otherwise
    # renders identically to one that is simply idle, and the only way to tell
    # them apart was to press it and watch nothing happen.
    needs_obs: bool = False

    def requires_obs(self, params: dict) -> bool:
        """Whether this key, as configured, needs OBS to be reachable.

        Almost always the class answer. `obs.stats` overrides it because some of
        its measurements come from the kernel and keep working regardless.
        """
        return self.needs_obs

    def execute(self, ctx: ActionContext, params: dict) -> None:
        raise NotImplementedError

    def long_press(self, ctx: ActionContext, params: dict) -> bool:
        """Handle a held press. Return False to run the normal action instead."""
        return False

    def feedback(self, ctx: ActionContext, params: dict) -> dict | None:
        """Visual state: active, color, badge and/or centered display text.

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
