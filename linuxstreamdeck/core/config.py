"""Configuration model and JSON persistence.

File: ~/.config/linuxstreamdeck/config.json
"""

from __future__ import annotations

import copy
import json
import logging
import os
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# The path can be redirected with LSD_CONFIG_DIR (useful for tests, so the user's
# real configuration is not touched, and for keeping separate configurations).
CONFIG_DIR = Path(
    os.environ.get("LSD_CONFIG_DIR", Path.home() / ".config" / "linuxstreamdeck")
)
CONFIG_FILE = CONFIG_DIR / "config.json"
BACKUP_FILE = CONFIG_DIR / "config.json.bak"

DEFAULT_KEY_BG = "#1e1e28"

# Key types
KIND_SINGLE = "single"          # a single action (with state feedback)
KIND_MULTI = "multi"            # ordered list of actions run in sequence
KIND_TOGGLE = "multi_toggle"    # toggle: two action lists (ON/OFF state)


@dataclass
class ActionStep:
    """A step inside a multi-action key."""
    action: str = ""                      # id of a registered action
    params: dict = field(default_factory=dict)
    delay_ms: int = 0                     # wait after running this step


@dataclass
class KeyConfig:
    kind: str = KIND_SINGLE

    # kind == single
    action: str = ""                      # id of a registered action, e.g. "obs.scene_switch"
    params: dict = field(default_factory=dict)

    # kind == multi
    steps: list[ActionStep] = field(default_factory=list)

    # kind == multi_toggle (steps_on runs when turning ON; steps_off when turning OFF)
    steps_on: list[ActionStep] = field(default_factory=list)
    steps_off: list[ActionStep] = field(default_factory=list)

    # appearance (main state / ON state on toggles)
    label: str = ""
    icon: str = ""                        # path to an optional image
    bg_color: str = DEFAULT_KEY_BG

    # OFF state appearance (toggles only)
    label_off: str = ""
    icon_off: str = ""
    bg_color_off: str = DEFAULT_KEY_BG

    def is_empty(self) -> bool:
        if self.kind == KIND_SINGLE:
            return not self.action
        if self.kind == KIND_MULTI:
            return not self.steps
        if self.kind == KIND_TOGGLE:
            return not (self.steps_on or self.steps_off)
        return True

    def clone(self) -> "KeyConfig":
        """Independent deep copy (for copy/paste and moving keys)."""
        return copy.deepcopy(self)

    @classmethod
    def from_dict(cls, d: dict) -> "KeyConfig":
        def steps(name: str) -> list[ActionStep]:
            return [
                ActionStep(
                    action=s.get("action", ""),
                    params=s.get("params", {}),
                    delay_ms=s.get("delay_ms", 0),
                )
                for s in d.get(name, [])
            ]

        return cls(
            kind=d.get("kind", KIND_SINGLE),
            action=d.get("action", ""),
            params=d.get("params", {}),
            steps=steps("steps"),
            steps_on=steps("steps_on"),
            steps_off=steps("steps_off"),
            label=d.get("label", ""),
            icon=d.get("icon", ""),
            bg_color=d.get("bg_color", DEFAULT_KEY_BG),
            label_off=d.get("label_off", ""),
            icon_off=d.get("icon_off", ""),
            bg_color_off=d.get("bg_color_off", DEFAULT_KEY_BG),
        )


@dataclass
class Page:
    name: str = "Page 1"
    # keys as str for JSON compatibility: "0".."14"
    keys: dict[str, KeyConfig] = field(default_factory=dict)

    def key(self, index: int) -> KeyConfig | None:
        return self.keys.get(str(index))

    def set_key(self, index: int, kc: KeyConfig | None) -> None:
        if kc is None:
            self.keys.pop(str(index), None)
        else:
            self.keys[str(index)] = kc

    @classmethod
    def from_dict(cls, d: dict, index: int = 0) -> "Page":
        return cls(
            name=d.get("name", f"Page {index + 1}"),
            keys={k: KeyConfig.from_dict(v) for k, v in d.get("keys", {}).items()},
        )


@dataclass
class Profile:
    """A set of pages/keys with a name and a short description."""
    name: str = "General"
    description: str = ""
    pages: list[Page] = field(default_factory=lambda: [Page()])
    current_page: int = 0

    @classmethod
    def from_dict(cls, d: dict) -> "Profile":
        pages = [Page.from_dict(p, i) for i, p in enumerate(d.get("pages", []))] or [Page()]
        return cls(
            name=d.get("name", "General"),
            description=d.get("description", ""),
            pages=pages,
            current_page=min(d.get("current_page", 0), len(pages) - 1),
        )


@dataclass
class ObsSettings:
    host: str = "localhost"
    port: int = 4455
    password: str = ""


@dataclass
class Config:
    profiles: list[Profile] = field(default_factory=lambda: [Profile()])
    current_profile: int = 0
    obs: ObsSettings = field(default_factory=ObsSettings)
    brightness: int = 80

    # ---------- access to the active profile ----------
    # `pages` and `current_page` delegate to the active profile so the rest of the
    # code (controller, actions, UI) keeps using them exactly as before.

    @property
    def profile(self) -> Profile:
        return self.profiles[self.current_profile]

    @property
    def pages(self) -> list[Page]:
        return self.profile.pages

    @property
    def current_page(self) -> int:
        return self.profile.current_page

    @current_page.setter
    def current_page(self, value: int) -> None:
        self.profile.current_page = value

    # ---------- persistence ----------

    @classmethod
    def load(cls) -> "Config":
        if not CONFIG_FILE.exists():
            return cls()
        try:
            raw = json.loads(CONFIG_FILE.read_text())
            if "profiles" in raw:
                profiles = [Profile.from_dict(p) for p in raw["profiles"]] or [Profile()]
                current = min(raw.get("current_profile", 0), len(profiles) - 1)
            else:
                # Old format (pages at the top level): migrate to a single
                # "General" profile so no configuration is lost.
                pages = [Page.from_dict(p, i)
                         for i, p in enumerate(raw.get("pages", []))] or [Page()]
                profiles = [Profile(
                    name="General", pages=pages,
                    current_page=min(raw.get("current_page", 0), len(pages) - 1),
                )]
                current = 0
            return cls(
                profiles=profiles,
                current_profile=current,
                obs=ObsSettings(**raw.get("obs", {})),
                brightness=raw.get("brightness", 80),
            )
        except Exception:
            log.exception("Could not read %s; using an empty configuration", CONFIG_FILE)
            return cls()

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        # back up the previous version before overwriting, so it can be recovered
        # if a save leaves it broken (see config.json.bak)
        if CONFIG_FILE.exists():
            try:
                shutil.copy2(CONFIG_FILE, BACKUP_FILE)
            except Exception:
                log.debug("Could not back up the configuration", exc_info=True)
        CONFIG_FILE.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False))
        log.debug("Configuration saved to %s", CONFIG_FILE)
