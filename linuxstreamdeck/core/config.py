"""Configuration model and JSON persistence.

File: ~/.config/linuxstreamdeck/config.json
"""

from __future__ import annotations

import copy
import datetime
import hashlib
import json
import logging
import os
import shutil
import tempfile
import time
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

from ..ai.constants import (
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_OPENAI_MODEL,
    PROVIDER_OPENAI,
    PROVIDERS,
)
from .actions import format_duration
from .audio import SUPPORTED_AUDIO_EXTENSIONS

log = logging.getLogger(__name__)

# The path can be redirected with LSD_CONFIG_DIR (useful for tests, so the user's
# real configuration is not touched, and for keeping separate configurations).
CONFIG_DIR = Path(
    os.environ.get("LSD_CONFIG_DIR", Path.home() / ".config" / "linuxstreamdeck")
)
CONFIG_FILE = CONFIG_DIR / "config.json"
BACKUP_FILE = CONFIG_DIR / "config.json.bak"

# Rolling history of earlier configurations, kept alongside the single-step
# config.json.bak. BACKUP_MIN_INTERVAL is the point of it: saves are frequent
# (a page switch, a brightness change and every key save is one), so without a
# floor between snapshots a burst of saves would push every older state out of
# the history at exactly the moment it becomes worth having.
BACKUP_DIR = CONFIG_DIR / "backups"
BACKUP_PREFIX = "config-"
BACKUP_SUFFIX = ".json"
BACKUP_KEEP = 8
BACKUP_MIN_INTERVAL = 300.0

# Where the log is kept. It used to go to stderr only, which is unreachable for
# the normal case of a session autostart, so an action that failed left no trace
# anywhere the user could look. Small and rotated: it is evidence for a bug
# report, not a history of everything the deck has ever done.
LOG_FILE = CONFIG_DIR / "linuxstreamdeck.log"
LOG_MAX_BYTES = 512 * 1024
LOG_KEEP = 2

DEFAULT_KEY_BG = "#1e1e28"

# Label font size. An empty value is an inheritance marker (the renderer picks a
# size from the key height), mirroring how an empty icon inherits its default.
KEY_FONT_SIZE_AUTO = ""
KEY_FONT_SIZE_CHOICES = (
    (KEY_FONT_SIZE_AUTO, "Automatic"),
    ("xs", "Extra small"),
    ("s", "Small"),
    ("m", "Medium"),
    ("l", "Large"),
    ("xl", "Extra large"),
)
KEY_FONT_SIZES = frozenset(choice[0] for choice in KEY_FONT_SIZE_CHOICES)

# Label colour. Empty is an inheritance marker exactly like the font size: the
# renderer then uses its own default, so a key that never chose one keeps
# rendering identically and clearing the choice restores that rather than
# freezing today's default into the configuration.
KEY_TEXT_COLOR_AUTO = ""

# What closing the window does. "tray" keeps the application running behind its
# status icon; it falls back to quitting when no status area is available, so the
# window can never disappear without a way back.
CLOSE_ACTION_QUIT = "quit"
CLOSE_ACTION_TRAY = "tray"
CLOSE_ACTION_CHOICES = (
    (
        CLOSE_ACTION_TRAY,
        "Keep running in the status area",
        "Hide the window and leave the Stream Deck working in the background.",
    ),
    (
        CLOSE_ACTION_QUIT,
        "Quit LinuxStreamDeck",
        "Stop the application and release the Stream Deck.",
    ),
)
CLOSE_ACTIONS = frozenset(choice[0] for choice in CLOSE_ACTION_CHOICES)
DEFAULT_CLOSE_ACTION = CLOSE_ACTION_TRAY

# How wide the editor panel is, as the user last left it. The window clamps it
# again to whatever the current window can actually give, so an imported width
# from a much wider screen is harmless rather than something to strip out. The
# lower bound is what the panel's own contents need; the upper one only stops a
# hand-edited file asking for a panel nobody could drag back.
# Bound on a stored server address. Not a real limit -- it only stops a
# hand-edited configuration carrying something absurd into a request.
MAX_URL_CHARS = 300

DEFAULT_EDITOR_WIDTH = 440
MIN_EDITOR_WIDTH = 380
MAX_EDITOR_WIDTH = 1600

# Built-in games keep only small preferences and local high scores in the
# normal configuration.  The score key includes the deck geometry and
# difficulty, so a Mini never competes with an XL that exposes many more
# targets.  Values are bounded on load because config.json is user-editable.
GAME_DIFFICULTIES = ("easy", "normal", "hard")
DEFAULT_GAME_DIFFICULTY = "normal"
DEFAULT_GAME_VOLUME = 55
MAX_GAME_HIGH_SCORES = 100
MAX_GAME_SCORE = 999_999_999

SCREENSAVER_CHOICES = (
    (
        "neon_pipes",
        "Neon Pipes",
        "Retro glowing pipes grow and turn across the whole deck.",
    ),
    (
        "digital_rain",
        "Digital Rain",
        "Elegant cyan data trails fall through a dark digital grid.",
    ),
    (
        "aurora_flow",
        "Aurora Flow",
        "Layered blue, violet and teal light waves drift slowly.",
    ),
    (
        "orbital_core",
        "Orbital Core",
        "A futuristic core with rotating rings and orbiting particles.",
    ),
    (
        "circuit_pulse",
        "Circuit Pulse",
        "Energy pulses travel through a refined circuit-board network.",
    ),
    (
        "ember_field",
        "Ember Field",
        "Flames climb from the bottom of the deck and flare in the dark.",
    ),
    (
        "hyperspace",
        "Hyperspace",
        "A warping wormhole with stars spiralling out of its throat.",
    ),
    (
        "matrix_code",
        "Matrix Code",
        "Green glyph columns rain down a black screen and mutate as they fall.",
    ),
    (
        "hal_9000",
        "HAL 9000",
        "A lone red camera eye breathing calmly, lighting the keys around it.",
    ),
    (
        "split_flap",
        "Split-Flap Board",
        "Amber flap modules riffle and settle, one character per key.",
    ),
    (
        "linuxstreamdeck",
        "LinuxStreamDeck",
        "A black screen with the LinuxStreamDeck name breathing softly.",
    ),
)
SCREENSAVER_IDS = frozenset(choice[0] for choice in SCREENSAVER_CHOICES)
DEFAULT_SCREENSAVER = SCREENSAVER_CHOICES[0][0]

EXIT_DISPLAY_DEFAULT = "device_default"
EXIT_DISPLAY_BLANK = "blank"
EXIT_DISPLAY_CUSTOM = "custom"
EXIT_DISPLAY_CHOICES = (
    (
        EXIT_DISPLAY_DEFAULT,
        "Device default",
        "Show the Stream Deck standby image supplied by the device firmware.",
    ),
    (
        EXIT_DISPLAY_BLANK,
        "Off",
        "Leave every key black with the display brightness set to zero.",
    ),
    (
        EXIT_DISPLAY_CUSTOM,
        "Custom",
        "Crop one image across the complete Stream Deck key grid.",
    ),
)
EXIT_DISPLAY_MODES = frozenset(choice[0] for choice in EXIT_DISPLAY_CHOICES)
SUPPORTED_EXIT_IMAGE_EXTENSIONS = frozenset(
    {".bmp", ".jpeg", ".jpg", ".png", ".webp"}
)

EXPORT_FORMAT = "linuxstreamdeck-configuration"
EXPORT_VERSION = 4
# Version 4 introduced folder keys. Older versions stay importable; older
# applications reject a v4 bundle instead of silently dropping whole folders.
SUPPORTED_EXPORT_VERSIONS = (1, 2, 3, EXPORT_VERSION)
EXPORT_CONFIG_FILE = "config.json"
EXPORT_MANIFEST_FILE = "manifest.json"
# Single-key bundles reuse the archive layout and asset prefixes of a full
# configuration export, with their own format id so the two cannot be confused.
KEY_EXPORT_FORMAT = "linuxstreamdeck-key"
KEY_EXPORT_VERSION = 2
SUPPORTED_KEY_EXPORT_VERSIONS = (1, KEY_EXPORT_VERSION)
KEY_EXPORT_FILE = "key.json"
EXPORT_ICON_PREFIX = "bundle:"
EXPORT_AUDIO_PREFIX = "bundle-audio:"
EXPORT_EXIT_IMAGE_PREFIX = "bundle-exit-image:"
MAX_CONFIG_BYTES = 10 * 1024 * 1024
MAX_ICON_BYTES = 50 * 1024 * 1024
MAX_TOTAL_ICON_BYTES = 200 * 1024 * 1024
MAX_AUDIO_BYTES = 200 * 1024 * 1024
MAX_TOTAL_AUDIO_BYTES = 500 * 1024 * 1024
MAX_EXIT_IMAGE_BYTES = 50 * 1024 * 1024

_ACTION_AUDIO_PARAMETERS = {
    "sys.audio": "file",
    "sys.timer": "sound",
}

# Key types
KIND_SINGLE = "single"          # a single action (with state feedback)
KIND_MULTI = "multi"            # ordered list of actions run in sequence
KIND_TOGGLE = "multi_toggle"    # toggle: two action lists (ON/OFF state)
KIND_RANDOM = "random"          # one action picked at random from the list
KIND_PRESS = "press"            # separate lists for single/double/long press
KIND_FOLDER = "folder"          # opens its own grid of keys
KIND_DIAL = "dial"              # a Stream Deck + encoder: turn left/right, push

# Stream Deck + hardware. The four encoders sit under an 800x100 LCD strip, so
# each one owns a quarter of it and the strip is what tells you which dial does
# what — the deck has no other way to say so.
DIAL_TOUCH_SIZE = (800, 100)
MAX_DIALS = 4

# Folders group keys without spending a page. Their contents share the deck's
# own key numbering, and the first slot is always the Back key, so the physical
# deck can never enter a folder it cannot leave.
FOLDER_BACK_INDEX = 0
DEFAULT_FOLDER_ICON = "mdi:folder"
DEFAULT_FOLDER_NAME = "Folder"
# Nesting is bounded so a hand-written or corrupt file cannot make loading,
# exporting or navigation recurse without end.
MAX_FOLDER_DEPTH = 5

# Every field of KeyConfig that holds a list of ActionStep. Anything walking a
# key's actions (portable bundles, migrations) must cover all of them.
STEP_FIELDS = (
    "steps",
    "steps_on",
    "steps_off",
    "steps_single",
    "steps_double",
    "steps_long",
    "steps_left",
    "steps_right",
    "steps_press",
)

# A Stream Deck + dial reuses KeyConfig rather than getting a type of its own,
# so every walk over a key's actions or assets — portable bundles, legacy
# migration, the layout sheet — reaches a dial without being taught about it.
# What differs is where they are stored: dials are numbered independently of
# keys, so they live in their own mapping on the page.
DIAL_STEP_FIELDS = ("steps_left", "steps_right", "steps_press")

# What a list of actions does when one of them fails. Carrying on is the old
# behaviour and stays the default, because most sequences are independent things
# done together. Stopping matters when the list is a recipe: "switch scene, wait,
# start recording" should not record the wrong scene because the switch failed.
ON_ERROR_CONTINUE = "continue"
ON_ERROR_STOP = "stop"
ON_ERROR_CHOICES = (
    (ON_ERROR_CONTINUE, "Run the remaining actions"),
    (ON_ERROR_STOP, "Stop the sequence"),
)


def _on_error(value) -> str:
    """Normalize the stored choice; anything unknown carries on as before."""
    choice = str(value or "").strip().lower()
    return choice if choice in dict(ON_ERROR_CHOICES) else ON_ERROR_CONTINUE


def _migrate_page_action(action, params) -> tuple[str, dict]:
    """Split the legacy combined page action into one explicit action."""
    action_id = str(action or "")
    values = dict(params) if isinstance(params, dict) else {}
    if action_id != "nav.page":
        return action_id, values
    mode = str(values.get("mode", "go to") or "go to").strip().lower()
    if mode in ("next", "next page"):
        return "nav.page.next", {}
    if mode in ("previous", "previous page"):
        return "nav.page.previous", {}
    return "nav.page.go", {"page": str(values.get("page", "") or "")}


def _font_size(value) -> str:
    """Keep only a known label size; anything else falls back to automatic."""
    size = str(value or "").strip().lower()
    return size if size in KEY_FONT_SIZES else KEY_FONT_SIZE_AUTO


def _as_index(value) -> int:
    """A stored mapping key as an int, or -1 when it is not one at all."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _text_color(value) -> str:
    """Keep only a '#rgb' / '#rrggbb' colour; anything else inherits.

    Validating here rather than in the renderer keeps a hand-edited file from
    reaching Pillow with something it would raise on mid-render, on a worker
    thread, once per repaint.
    """
    color = str(value or "").strip().lower()
    if len(color) not in (4, 7) or not color.startswith("#"):
        return KEY_TEXT_COLOR_AUTO
    return color if all(c in "0123456789abcdef" for c in color[1:]) else KEY_TEXT_COLOR_AUTO


@dataclass(frozen=True)
class BackupInfo:
    """One rolling backup, as the restore dialog lists it."""

    path: Path
    when: "datetime.datetime | None"
    profiles: int
    pages: int
    keys: int
    readable: bool = True

    def label(self) -> str:
        when = self.when.strftime("%d %b %Y, %H:%M") if self.when else "unknown date"
        if not self.readable:
            return f"{when} — unreadable"
        return (
            f"{when} — {self.profiles} profile(s), "
            f"{self.pages} page(s), {self.keys} key(s)"
        )


@dataclass(frozen=True)
class ExportResult:
    bundled_icons: int
    missing_icons: int
    bundled_audio: int = 0
    missing_audio: int = 0
    bundled_exit_image: bool = False
    missing_exit_image: bool = False


@dataclass(frozen=True)
class ImportResult:
    profiles: int
    pages: int
    keys: int
    restored_icons: int
    restored_audio: int = 0
    restored_exit_image: bool = False


@dataclass(frozen=True)
class KeyExportResult:
    bundled_icons: int
    missing_icons: int
    bundled_audio: int
    missing_audio: int


@dataclass(frozen=True)
class KeyImportResult:
    key: "KeyConfig"
    restored_icons: int
    restored_audio: int


@dataclass
class ActionStep:
    """A step inside a multi-action key. Pauses between steps are expressed as an
    explicit Wait action (sys.wait), not a per-step delay."""
    action: str = ""                      # id of a registered action
    params: dict = field(default_factory=dict)
    # Optional name for this step, shown in the editor's list instead of the
    # action name. Purely descriptive: nothing at run time reads it.
    label: str = ""


class KeyGrid:
    """Shared key access for anything holding one grid of keys.

    Deliberately not a dataclass: it contributes no fields, so `Page` and
    `Folder` keep their own field order (and therefore their JSON layout).
    """

    keys: dict[str, "KeyConfig"]

    def key(self, index: int) -> "KeyConfig | None":
        return self.keys.get(str(index))

    def set_key(self, index: int, kc: "KeyConfig | None") -> None:
        if kc is None:
            self.keys.pop(str(index), None)
        else:
            self.keys[str(index)] = kc

    def configured_keys(self) -> int:
        return sum(1 for kc in self.keys.values() if kc is not None)


@dataclass
class KeyConfig:
    kind: str = KIND_SINGLE

    # kind == single
    action: str = ""                      # id of a registered action, e.g. "obs.scene_switch"
    params: dict = field(default_factory=dict)

    # kind == multi (run in order) and kind == random (one picked at random)
    steps: list[ActionStep] = field(default_factory=list)

    # kind == multi_toggle (steps_on runs when turning ON; steps_off when turning OFF)
    steps_on: list[ActionStep] = field(default_factory=list)
    steps_off: list[ActionStep] = field(default_factory=list)

    # kind == press: one list per press gesture
    steps_single: list[ActionStep] = field(default_factory=list)
    steps_double: list[ActionStep] = field(default_factory=list)
    steps_long: list[ActionStep] = field(default_factory=list)

    # kind == dial: one list per encoder gesture (Stream Deck + only)
    steps_left: list[ActionStep] = field(default_factory=list)
    steps_right: list[ActionStep] = field(default_factory=list)
    steps_press: list[ActionStep] = field(default_factory=list)

    # What every list above does when one of its actions fails. Single-action
    # keys ignore it: there is nothing after the action that failed.
    on_error: str = ON_ERROR_CONTINUE

    # kind == folder: the grid of keys it opens. Excluded from equality on
    # purpose: its contents are edited by navigating into the folder and saved
    # there, so the editor's unsaved-change check must not read an edit made
    # inside the folder as a pending edit of the folder key itself.
    folder: "Folder | None" = field(default=None, compare=False)

    # appearance (main state / ON state on toggles)
    label: str = ""
    icon: str = ""                        # path to an optional image
    bg_color: str = DEFAULT_KEY_BG
    font_size: str = KEY_FONT_SIZE_AUTO   # "" inherits the automatic size
    text_color: str = KEY_TEXT_COLOR_AUTO  # "" inherits the renderer default

    # OFF state appearance (toggles only)
    label_off: str = ""
    icon_off: str = ""
    bg_color_off: str = DEFAULT_KEY_BG
    font_size_off: str = KEY_FONT_SIZE_AUTO
    text_color_off: str = KEY_TEXT_COLOR_AUTO

    def is_empty(self) -> bool:
        if self.kind == KIND_SINGLE:
            return not self.action
        if self.kind in (KIND_MULTI, KIND_RANDOM):
            return not self.steps
        if self.kind == KIND_TOGGLE:
            return not (self.steps_on or self.steps_off)
        if self.kind == KIND_PRESS:
            return not (self.steps_single or self.steps_double or self.steps_long)
        if self.kind == KIND_DIAL:
            return not (self.steps_left or self.steps_right or self.steps_press)
        if self.kind == KIND_FOLDER:
            # A folder is configured as soon as it exists; filling it comes
            # afterwards, so an empty one must still render and open.
            return False
        return True

    @property
    def contents(self) -> "Folder | None":
        """The folder this key opens, or None when it is not a folder."""
        return self.folder if self.kind == KIND_FOLDER else None

    def folder_name(self) -> str:
        """Readable folder name: the key label, or a generic default."""
        return self.label.strip() or DEFAULT_FOLDER_NAME

    def clone(self) -> "KeyConfig":
        """Independent deep copy (for copy/paste and moving keys)."""
        return copy.deepcopy(self)

    @classmethod
    def from_dict(cls, d: dict, depth: int = 0) -> "KeyConfig":
        if not isinstance(d, dict):
            raise ValueError("Each key configuration must be a JSON object")

        def steps(name: str) -> list[ActionStep]:
            out: list[ActionStep] = []
            raw_steps = d.get(name, [])
            if not isinstance(raw_steps, list):
                raise ValueError(f"The {name} field must be a list")
            for s in raw_steps:
                if not isinstance(s, dict):
                    raise ValueError("Each action step must be a JSON object")
                action, params = _migrate_page_action(
                    s.get("action", ""),
                    s.get("params", {}),
                )
                out.append(
                    ActionStep(
                        action=action,
                        params=params,
                        label=str(s.get("label", "") or ""),
                    )
                )
                # legacy: a per-step "delay after (ms)" becomes an explicit Wait
                # action (whole seconds), so old configs keep their pauses.
                secs = round(s.get("delay_ms", 0) / 1000)
                if secs > 0:
                    out.append(ActionStep(action="sys.wait",
                                          params={"duration": format_duration(secs)}))
            return out

        action, params = _migrate_page_action(
            d.get("action", ""),
            d.get("params", {}),
        )
        kind = d.get("kind", KIND_SINGLE)
        return cls(
            kind=kind,
            action=action,
            params=params,
            steps=steps("steps"),
            steps_on=steps("steps_on"),
            steps_off=steps("steps_off"),
            steps_single=steps("steps_single"),
            steps_double=steps("steps_double"),
            steps_long=steps("steps_long"),
            steps_left=steps("steps_left"),
            steps_right=steps("steps_right"),
            steps_press=steps("steps_press"),
            on_error=_on_error(d.get("on_error")),
            folder=_folder_contents(d.get("folder"), kind, depth),
            label=d.get("label", ""),
            icon=d.get("icon", ""),
            bg_color=d.get("bg_color", DEFAULT_KEY_BG),
            font_size=_font_size(d.get("font_size")),
            text_color=_text_color(d.get("text_color")),
            label_off=d.get("label_off", ""),
            icon_off=d.get("icon_off", ""),
            bg_color_off=d.get("bg_color_off", DEFAULT_KEY_BG),
            font_size_off=_font_size(d.get("font_size_off")),
            text_color_off=_text_color(d.get("text_color_off")),
        )


@dataclass
class Folder(KeyGrid):
    """The grid of keys a folder key opens.

    It shares the deck's own numbering, so no index has to shift when a key
    moves in or out. `FOLDER_BACK_INDEX` is reserved for the Back key and is
    therefore never stored.
    """

    keys: dict[str, KeyConfig] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d, depth: int = 1) -> "Folder":
        """Load a folder that sits `depth` levels below its page."""
        if d is None:
            return cls()
        if not isinstance(d, dict):
            raise ValueError("A folder must be a JSON object")
        raw_keys = d.get("keys", {})
        if not isinstance(raw_keys, dict):
            raise ValueError("The folder keys field must be a JSON object")
        return cls(
            keys={
                str(k): KeyConfig.from_dict(v, depth)
                for k, v in raw_keys.items()
                if str(k) != str(FOLDER_BACK_INDEX)
            }
        )


def _folder_contents(raw, kind: str, depth: int) -> Folder | None:
    """The contents of a folder key, bounded by the nesting limit.

    `depth` is the level the key itself lives at, so a key one level below the
    limit keeps its own grid while anything deeper stays a plain (unopenable)
    folder key. That keeps loading, exporting and navigation bounded without
    letting one hand-written branch cost the whole configuration.
    """
    if kind != KIND_FOLDER:
        return None
    if depth >= MAX_FOLDER_DEPTH:
        if isinstance(raw, dict) and raw.get("keys"):
            log.warning(
                "Ignoring folder contents nested deeper than %d levels",
                MAX_FOLDER_DEPTH,
            )
        return None
    return Folder.from_dict(raw, depth + 1)


def folder_depth(kc: KeyConfig) -> int:
    """How many folder levels a key adds. 0 when it is not a folder."""
    contents = kc.contents
    if contents is None:
        return 0
    nested = [folder_depth(child) for child in contents.keys.values()]
    return 1 + max(nested, default=0)


@dataclass
class Page(KeyGrid):
    name: str = "Page 1"
    # keys as str for JSON compatibility: "0".."14"
    keys: dict[str, KeyConfig] = field(default_factory=dict)
    # Stream Deck + encoders, numbered independently of the keys. Absent from
    # older configurations and ignored by older builds, so no format version
    # changes: a deck without dials simply never reads them.
    dials: dict[str, KeyConfig] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict, index: int = 0) -> "Page":
        if not isinstance(d, dict):
            raise ValueError("Each page must be a JSON object")
        raw_keys = d.get("keys", {})
        if not isinstance(raw_keys, dict):
            raise ValueError("The page keys field must be a JSON object")
        raw_dials = d.get("dials", {})
        if not isinstance(raw_dials, dict):
            raise ValueError("The page dials field must be a JSON object")
        return cls(
            name=d.get("name", f"Page {index + 1}"),
            keys={str(k): KeyConfig.from_dict(v) for k, v in raw_keys.items()},
            dials={
                str(k): KeyConfig.from_dict(v)
                for k, v in raw_dials.items()
                if 0 <= _as_index(k) < MAX_DIALS
            },
        )

    def dial(self, index: int) -> KeyConfig | None:
        return self.dials.get(str(index))

    def set_dial(self, index: int, config: KeyConfig | None) -> None:
        if config is None or config.is_empty():
            self.dials.pop(str(index), None)
        else:
            self.dials[str(index)] = config

    def configured_dials(self) -> int:
        return sum(1 for d in self.dials.values() if not d.is_empty())

    def clone(self, name: str = "") -> "Page":
        """Independent deep copy, optionally renamed."""
        copied = copy.deepcopy(self)
        if name:
            copied.name = name
        return copied


@dataclass
class Profile:
    """A set of pages/keys with a name and a short description."""
    name: str = "General"
    description: str = ""
    pages: list[Page] = field(default_factory=lambda: [Page()])
    current_page: int = 0

    @classmethod
    def from_dict(cls, d: dict) -> "Profile":
        if not isinstance(d, dict):
            raise ValueError("Each profile must be a JSON object")
        raw_pages = d.get("pages", [])
        if not isinstance(raw_pages, list):
            raise ValueError("The profile pages field must be a list")
        pages = [Page.from_dict(p, i) for i, p in enumerate(raw_pages)] or [Page()]
        try:
            current_page = int(d.get("current_page", 0))
        except (TypeError, ValueError):
            current_page = 0
        return cls(
            name=d.get("name", "General"),
            description=d.get("description", ""),
            pages=pages,
            current_page=max(0, min(current_page, len(pages) - 1)),
        )

    def clone(self, name: str = "") -> "Profile":
        """Independent deep copy, optionally renamed.

        Page names are unique per profile, so a copied profile keeps every one
        of them and every `nav.page.go` inside it keeps pointing at the right
        page of the copy.
        """
        copied = copy.deepcopy(self)
        if name:
            copied.name = name
        return copied


def unique_name(base: str, taken, suffix: str = "copy") -> str:
    """A name derived from `base` that is not in `taken`.

    Used for duplicated pages and profiles: "Live" becomes "Live copy", then
    "Live copy 2". Duplicating is the one operation where a clashing name is
    guaranteed rather than possible, so the caller never has to ask for one.
    """
    existing = set(taken)
    candidate = f"{base} {suffix}".strip()
    if candidate not in existing:
        return candidate
    counter = 2
    while f"{candidate} {counter}" in existing:
        counter += 1
    return f"{candidate} {counter}"


@dataclass
class ObsSettings:
    host: str = "localhost"
    port: int = 4455
    password: str = ""


@dataclass
class AISettings:
    provider: str = PROVIDER_OPENAI
    openai_model: str = DEFAULT_OPENAI_MODEL
    anthropic_model: str = DEFAULT_ANTHROPIC_MODEL
    include_obs_context: bool = False


@dataclass
class TwitchSettings:
    """What is worth persisting about the Twitch link.

    Deliberately only the Client ID. The tokens themselves live in Secret
    Service, exactly like the OBS password and the AI provider keys, so they
    never reach `config.json`, its backup or a `.lsdconfig` export. A Client ID
    is a public identifier and travels harmlessly.
    """

    client_id: str = ""


@dataclass
class HomeAssistantSettings:
    """What is worth persisting about the Home Assistant server.

    Deliberately only the address. The long-lived access token lives in Secret
    Service, exactly like the OBS password, the AI provider keys and the Twitch
    tokens, so it never reaches `config.json`, its backup or a `.lsdconfig`
    export. An address on somebody's own network travels harmlessly, and a
    configuration moved to another computer in the same house still points at
    the right box.
    """

    base_url: str = ""


@dataclass
class ScreenSaverSettings:
    enabled: bool = False
    style: str = DEFAULT_SCREENSAVER
    idle_minutes: int = 5
    intensity: int = 35


@dataclass
class ExitDisplaySettings:
    mode: str = EXIT_DISPLAY_DEFAULT
    image_path: str = ""


@dataclass
class GameSettings:
    mole_difficulty: str = DEFAULT_GAME_DIFFICULTY
    mole_sound_enabled: bool = True
    mole_volume: int = DEFAULT_GAME_VOLUME
    mole_high_scores: dict[str, int] = field(default_factory=dict)
    circuit_difficulty: str = DEFAULT_GAME_DIFFICULTY
    circuit_sound_enabled: bool = True
    circuit_volume: int = DEFAULT_GAME_VOLUME
    circuit_best_moves: dict[str, int] = field(default_factory=dict)
    pulse_difficulty: str = DEFAULT_GAME_DIFFICULTY
    pulse_sound_enabled: bool = True
    pulse_volume: int = DEFAULT_GAME_VOLUME
    pulse_high_scores: dict[str, int] = field(default_factory=dict)
    memory_difficulty: str = DEFAULT_GAME_DIFFICULTY
    memory_sound_enabled: bool = True
    memory_volume: int = DEFAULT_GAME_VOLUME
    memory_best_moves: dict[str, int] = field(default_factory=dict)
    mines_difficulty: str = DEFAULT_GAME_DIFFICULTY
    mines_sound_enabled: bool = True
    mines_volume: int = DEFAULT_GAME_VOLUME
    mines_best_times: dict[str, int] = field(default_factory=dict)
    tic_tac_toe_difficulty: str = DEFAULT_GAME_DIFFICULTY
    tic_tac_toe_sound_enabled: bool = True
    tic_tac_toe_volume: int = DEFAULT_GAME_VOLUME
    tic_tac_toe_wins: dict[str, int] = field(default_factory=dict)
    mastermind_difficulty: str = DEFAULT_GAME_DIFFICULTY
    mastermind_sound_enabled: bool = True
    mastermind_volume: int = DEFAULT_GAME_VOLUME
    mastermind_best_attempts: dict[str, int] = field(default_factory=dict)


@dataclass
class Config:
    profiles: list[Profile] = field(default_factory=lambda: [Profile()])
    current_profile: int = 0
    obs: ObsSettings = field(default_factory=ObsSettings)
    ai: AISettings = field(default_factory=AISettings)
    twitch: TwitchSettings = field(default_factory=TwitchSettings)
    home_assistant: HomeAssistantSettings = field(
        default_factory=lambda: HomeAssistantSettings()
    )
    screensaver: ScreenSaverSettings = field(default_factory=ScreenSaverSettings)
    exit_display: ExitDisplaySettings = field(default_factory=ExitDisplaySettings)
    games: GameSettings = field(default_factory=GameSettings)
    brightness: int = 80
    close_action: str = DEFAULT_CLOSE_ACTION
    editor_width: int = DEFAULT_EDITOR_WIDTH
    obs_password_needs_migration: bool = field(
        default=False, repr=False, compare=False
    )

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
            raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            return cls.from_dict(raw)
        except Exception:
            log.exception("Could not read %s; using an empty configuration", CONFIG_FILE)
            return cls()

    @classmethod
    def from_dict(cls, raw: dict) -> "Config":
        if not isinstance(raw, dict):
            raise ValueError("The configuration root must be a JSON object")
        if "profiles" in raw:
            raw_profiles = raw["profiles"]
            if not isinstance(raw_profiles, list):
                raise ValueError("The profiles field must be a list")
            profiles = [Profile.from_dict(p) for p in raw_profiles] or [Profile()]
            current = cls._bounded_index(
                raw.get("current_profile", 0), len(profiles)
            )
        else:
            # Old format (pages at the top level): migrate to a single
            # "General" profile so no configuration is lost.
            raw_pages = raw.get("pages", [])
            if not isinstance(raw_pages, list):
                raise ValueError("The pages field must be a list")
            pages = [Page.from_dict(p, i) for i, p in enumerate(raw_pages)] or [Page()]
            profiles = [Profile(
                name="General",
                pages=pages,
                current_page=cls._bounded_index(
                    raw.get("current_page", 0), len(pages)
                ),
            )]
            current = 0

        raw_obs = raw.get("obs", {})
        if not isinstance(raw_obs, dict):
            raise ValueError("The OBS settings must be a JSON object")
        raw_ai = raw.get("ai", {})
        if not isinstance(raw_ai, dict):
            raise ValueError("The AI settings must be a JSON object")
        raw_twitch = raw.get("twitch", {})
        if not isinstance(raw_twitch, dict):
            raise ValueError("The Twitch settings must be a JSON object")
        raw_screensaver = raw.get("screensaver", {})
        if not isinstance(raw_screensaver, dict):
            raise ValueError("The screen saver settings must be a JSON object")
        raw_exit_display = raw.get("exit_display", {})
        if not isinstance(raw_exit_display, dict):
            raise ValueError("The exit display settings must be a JSON object")
        raw_games = raw.get("games", {})
        if not isinstance(raw_games, dict):
            raise ValueError("The game settings must be a JSON object")
        try:
            legacy_password = str(raw_obs.get("password", ""))
            obs = ObsSettings(
                host=str(raw_obs.get("host", "localhost")),
                port=int(raw_obs.get("port", 4455)),
                password=legacy_password,
            )
            provider = str(raw_ai.get("provider", PROVIDER_OPENAI)).lower()
            if provider not in PROVIDERS:
                provider = PROVIDER_OPENAI
            ai = AISettings(
                provider=provider,
                openai_model=cls._model_setting(
                    raw_ai.get("openai_model"), DEFAULT_OPENAI_MODEL
                ),
                anthropic_model=cls._model_setting(
                    raw_ai.get("anthropic_model"), DEFAULT_ANTHROPIC_MODEL
                ),
                include_obs_context=(
                    raw_ai.get("include_obs_context", False)
                    if isinstance(raw_ai.get("include_obs_context", False), bool)
                    else False
                ),
            )
            twitch = TwitchSettings(
                client_id=str(raw_twitch.get("client_id", "") or "").strip()
            )
            home_assistant = HomeAssistantSettings(
                base_url=str(
                    (raw.get("home_assistant") or {}).get("base_url", "") or ""
                ).strip()[:MAX_URL_CHARS]
            )
            screen_style = str(
                raw_screensaver.get("style", DEFAULT_SCREENSAVER)
            )
            if screen_style not in SCREENSAVER_IDS:
                screen_style = DEFAULT_SCREENSAVER
            screensaver = ScreenSaverSettings(
                enabled=(
                    raw_screensaver.get("enabled", False)
                    if isinstance(raw_screensaver.get("enabled", False), bool)
                    else False
                ),
                style=screen_style,
                idle_minutes=max(
                    1,
                    min(1440, int(raw_screensaver.get("idle_minutes", 5))),
                ),
                intensity=max(
                    5,
                    min(100, int(raw_screensaver.get("intensity", 35))),
                ),
            )
            exit_mode = str(
                raw_exit_display.get("mode", EXIT_DISPLAY_DEFAULT)
            )
            if exit_mode not in EXIT_DISPLAY_MODES:
                exit_mode = EXIT_DISPLAY_DEFAULT
            exit_display = ExitDisplaySettings(
                mode=exit_mode,
                image_path=str(raw_exit_display.get("image_path", "") or ""),
            )
            def game_difficulty(field_name: str) -> str:
                value = str(
                    raw_games.get(field_name, DEFAULT_GAME_DIFFICULTY)
                    or DEFAULT_GAME_DIFFICULTY
                ).lower()
                return (
                    value
                    if value in GAME_DIFFICULTIES
                    else DEFAULT_GAME_DIFFICULTY
                )

            def game_bool(field_name: str) -> bool:
                value = raw_games.get(field_name, True)
                return value if isinstance(value, bool) else True

            def game_volume(field_name: str) -> int:
                return max(
                    0,
                    min(100, int(raw_games.get(field_name, DEFAULT_GAME_VOLUME))),
                )

            def game_scores(field_name: str) -> dict[str, int]:
                values = raw_games.get(field_name, {})
                if not isinstance(values, dict):
                    return {}
                scores: dict[str, int] = {}
                for raw_key, raw_score in list(values.items())[:MAX_GAME_HIGH_SCORES]:
                    key = str(raw_key or "").strip()[:80]
                    if not key:
                        continue
                    scores[key] = max(0, min(MAX_GAME_SCORE, int(raw_score)))
                return scores

            games = GameSettings(
                mole_difficulty=game_difficulty("mole_difficulty"),
                mole_sound_enabled=game_bool("mole_sound_enabled"),
                mole_volume=game_volume("mole_volume"),
                mole_high_scores=game_scores("mole_high_scores"),
                circuit_difficulty=game_difficulty("circuit_difficulty"),
                circuit_sound_enabled=game_bool("circuit_sound_enabled"),
                circuit_volume=game_volume("circuit_volume"),
                circuit_best_moves=game_scores("circuit_best_moves"),
                pulse_difficulty=game_difficulty("pulse_difficulty"),
                pulse_sound_enabled=game_bool("pulse_sound_enabled"),
                pulse_volume=game_volume("pulse_volume"),
                pulse_high_scores=game_scores("pulse_high_scores"),
                memory_difficulty=game_difficulty("memory_difficulty"),
                memory_sound_enabled=game_bool("memory_sound_enabled"),
                memory_volume=game_volume("memory_volume"),
                memory_best_moves=game_scores("memory_best_moves"),
                mines_difficulty=game_difficulty("mines_difficulty"),
                mines_sound_enabled=game_bool("mines_sound_enabled"),
                mines_volume=game_volume("mines_volume"),
                mines_best_times=game_scores("mines_best_times"),
                tic_tac_toe_difficulty=game_difficulty(
                    "tic_tac_toe_difficulty"
                ),
                tic_tac_toe_sound_enabled=game_bool(
                    "tic_tac_toe_sound_enabled"
                ),
                tic_tac_toe_volume=game_volume("tic_tac_toe_volume"),
                tic_tac_toe_wins=game_scores("tic_tac_toe_wins"),
                mastermind_difficulty=game_difficulty(
                    "mastermind_difficulty"
                ),
                mastermind_sound_enabled=game_bool(
                    "mastermind_sound_enabled"
                ),
                mastermind_volume=game_volume("mastermind_volume"),
                mastermind_best_attempts=game_scores(
                    "mastermind_best_attempts"
                ),
            )
            brightness = max(10, min(100, int(raw.get("brightness", 80))))
            editor_width = max(MIN_EDITOR_WIDTH, min(MAX_EDITOR_WIDTH, int(
                raw.get("editor_width", DEFAULT_EDITOR_WIDTH)
                or DEFAULT_EDITOR_WIDTH
            )))
        except (TypeError, ValueError) as error:
            raise ValueError("The configuration contains an invalid number") from error
        close_action = str(raw.get("close_action", DEFAULT_CLOSE_ACTION) or "")
        if close_action not in CLOSE_ACTIONS:
            close_action = DEFAULT_CLOSE_ACTION
        return cls(
            profiles=profiles,
            current_profile=current,
            obs=obs,
            ai=ai,
            twitch=twitch,
            home_assistant=home_assistant,
            screensaver=screensaver,
            exit_display=exit_display,
            games=games,
            brightness=brightness,
            close_action=close_action,
            editor_width=editor_width,
            obs_password_needs_migration="password" in raw_obs,
        )

    @staticmethod
    def _model_setting(value, default: str) -> str:
        model = str(value or default).strip()
        return model[:160] or default

    @staticmethod
    def _bounded_index(value, length: int) -> int:
        try:
            index = int(value)
        except (TypeError, ValueError):
            index = 0
        return max(0, min(index, length - 1))

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        # back up the previous version before overwriting, so it can be recovered
        # if a save leaves it broken (see config.json.bak)
        if CONFIG_FILE.exists():
            try:
                shutil.copy2(CONFIG_FILE, BACKUP_FILE)
            except Exception:
                log.debug("Could not back up the configuration", exc_info=True)
            self.rotate_backups()
        self._write_json_file(
            CONFIG_FILE,
            self._serializable_dict(include_legacy_password=True),
        )
        log.debug("Configuration saved to %s", CONFIG_FILE)

    @staticmethod
    def backup_history() -> list[Path]:
        """The rolling backups, newest first.

        Ordered by when the snapshot was taken, not by name: a second snapshot
        inside the same second has to be disambiguated, and any suffix that
        does that sorts *before* the plain name rather than after it. The
        modification time has nanosecond resolution and no such ambiguity.
        """
        try:
            entries = [
                path
                for path in BACKUP_DIR.glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}")
                if path.is_file()
            ]
        except OSError:
            return []

        def taken_at(path: Path):
            try:
                return (path.stat().st_mtime, path.name)
            except OSError:
                return (0.0, path.name)

        return sorted(entries, key=taken_at, reverse=True)

    @staticmethod
    def _free_backup_path(stamp: str) -> Path | None:
        """An unused name for a snapshot taken this second.

        Two in the same second are rare but real: a restore forces one
        immediately after the save that precedes it. Skipping the second would
        break the one promise this feature makes.
        """
        candidate = BACKUP_DIR / f"{BACKUP_PREFIX}{stamp}{BACKUP_SUFFIX}"
        if not candidate.exists():
            return candidate
        for sequence in range(1, 100):
            candidate = (
                BACKUP_DIR / f"{BACKUP_PREFIX}{stamp}-{sequence:02d}{BACKUP_SUFFIX}"
            )
            if not candidate.exists():
                return candidate
        return None

    @classmethod
    def rotate_backups(cls, force: bool = False) -> None:
        """Copy the configuration on disk into the history and prune the oldest.

        `force` ignores the interval, for a caller that is about to replace the
        whole configuration rather than edit part of it.

        Never raises: losing a backup must not stop the save it precedes.
        """
        try:
            history = cls.backup_history()
            if history and not force and not cls._backup_due(history[0]):
                return
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
            target = cls._free_backup_path(stamp)
            if target is None:
                return
            # copy, not copy2: the backup is ordered by when it was taken, and
            # copy2 would give it the configuration file's own timestamp.
            shutil.copy(CONFIG_FILE, target)
            os.chmod(target, 0o600)
            for old in cls.backup_history()[BACKUP_KEEP:]:
                old.unlink(missing_ok=True)
        except Exception:
            log.debug("Could not rotate the configuration backups", exc_info=True)

    @staticmethod
    def _backup_due(newest: Path) -> bool:
        try:
            return time.time() - newest.stat().st_mtime >= BACKUP_MIN_INTERVAL
        except OSError:
            return True

    @staticmethod
    def describe_backup(path: Path) -> "BackupInfo":
        """Enough about a backup to choose it from a list.

        A row saying only "config-20260727-114500.json" is unusable: what
        decides which copy you want is what is inside it.
        """
        path = Path(path)
        try:
            when = datetime.datetime.fromtimestamp(path.stat().st_mtime)
        except OSError:
            when = None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            profiles = raw.get("profiles") or []
            pages = sum(len(p.get("pages") or []) for p in profiles)
            keys = sum(
                len(page.get("keys") or {})
                for p in profiles
                for page in (p.get("pages") or [])
            )
            return BackupInfo(path, when, len(profiles), pages, keys, True)
        except Exception:
            log.debug("Could not read the backup %s", path, exc_info=True)
            return BackupInfo(path, when, 0, 0, 0, False)

    def restore_backup(self, path: Path) -> "BackupInfo":
        """Replace the live configuration with one of the rolling backups.

        The current state is snapshotted first, so restoring is itself
        reversible: picking the wrong copy must not be the end of the story.
        Keyring credentials stay as they are, exactly as importing a bundle
        leaves them, because they belong to this computer and not to the file.
        """
        path = Path(path).resolve()
        # Only ever from the backup directory. This is reached from a file
        # chooser-free dialog, and keeping it closed means a restore can never
        # be pointed at an arbitrary path.
        if path.parent != BACKUP_DIR.resolve() or not path.is_file():
            raise ValueError("That backup is no longer available")
        info = self.describe_backup(path)
        if not info.readable:
            raise ValueError("That backup file cannot be read")
        raw = json.loads(path.read_text(encoding="utf-8"))
        replacement = Config.from_dict(raw)
        replacement.obs.password = self.obs.password
        replacement.obs_password_needs_migration = (
            self.obs_password_needs_migration
        )
        self.rotate_backups(force=True)
        replacement.save()
        self.profiles = replacement.profiles
        self.current_profile = replacement.current_profile
        self.obs = replacement.obs
        self.ai = replacement.ai
        self.twitch = replacement.twitch
        self.home_assistant = replacement.home_assistant
        self.screensaver = replacement.screensaver
        self.exit_display = replacement.exit_display
        self.games = replacement.games
        self.brightness = replacement.brightness
        self.close_action = replacement.close_action
        self.editor_width = replacement.editor_width
        return info

    def finish_password_migration(self) -> None:
        """Persist without the legacy password and scrub it from both JSON files."""
        self.obs_password_needs_migration = False
        self.save()
        if not self.scrub_plaintext_password_files():
            raise OSError("A legacy configuration file could not be sanitized")

    def scrub_plaintext_password_files(self) -> bool:
        """Remove a legacy OBS password field from current and backup JSON files."""
        success = True
        for path in (CONFIG_FILE, BACKUP_FILE):
            if not path.exists():
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                obs = raw.get("obs")
                if isinstance(obs, dict) and "password" in obs:
                    obs.pop("password")
                    self._write_json_file(path, raw)
            except Exception:
                log.warning(
                    "Could not remove a legacy password from %s",
                    path,
                    exc_info=True,
                )
                success = False
        return success

    def _serializable_dict(self, include_legacy_password: bool = False) -> dict:
        raw = asdict(self)
        raw.pop("obs_password_needs_migration", None)
        if not (
            include_legacy_password and self.obs_password_needs_migration
        ):
            raw["obs"].pop("password", None)
        return raw

    # ---------- portable import / export ----------

    @classmethod
    def _bundle_key_icons(
        cls,
        key: dict,
        icon_files: dict[str, bytes],
        missing_icons: set[str],
    ) -> None:
        """Copy one key's custom icons into the archive and rewrite its refs."""
        for field_name in ("icon", "icon_off"):
            ref = key.get(field_name, "")
            if not ref or ref.startswith("mdi:"):
                continue
            try:
                source = Path(ref).expanduser()
                if not source.is_file() or source.stat().st_size > MAX_ICON_BYTES:
                    missing_icons.add(ref)
                    continue
                data = source.read_bytes()
            except OSError:
                missing_icons.add(ref)
                continue
            digest = hashlib.sha256(data).hexdigest()
            suffix = cls._safe_icon_suffix(source.suffix)
            archive_name = f"icons/{digest}{suffix}"
            icon_files.setdefault(archive_name, data)
            key[field_name] = f"{EXPORT_ICON_PREFIX}{archive_name}"

    @classmethod
    def _bundle_key_audio(
        cls,
        key: dict,
        audio_files: dict[str, bytes],
        missing_audio: set[str],
        total_audio_bytes: int,
    ) -> int:
        """Copy one key's audio into the archive; returns the new total size."""
        for action, params in cls._raw_action_params(key):
            parameter = _ACTION_AUDIO_PARAMETERS.get(action)
            if parameter is None:
                continue
            ref = str(params.get(parameter, "") or "")
            if not ref:
                continue
            try:
                source = Path(ref).expanduser()
                if (
                    not source.is_file()
                    or source.stat().st_size > MAX_AUDIO_BYTES
                    or source.suffix.lower() not in SUPPORTED_AUDIO_EXTENSIONS
                ):
                    missing_audio.add(ref)
                    continue
                data = source.read_bytes()
            except OSError:
                missing_audio.add(ref)
                continue
            digest = hashlib.sha256(data).hexdigest()
            archive_name = f"audio/{digest}{source.suffix.lower()}"
            if archive_name not in audio_files:
                if total_audio_bytes + len(data) > MAX_TOTAL_AUDIO_BYTES:
                    missing_audio.add(ref)
                    continue
                audio_files[archive_name] = data
                total_audio_bytes += len(data)
            params[parameter] = f"{EXPORT_AUDIO_PREFIX}{archive_name}"
        return total_audio_bytes

    def export_bundle(self, destination: Path) -> ExportResult:
        """Write a portable archive with custom icons, audio and exit image."""
        raw = self._serializable_dict()
        icon_files: dict[str, bytes] = {}
        missing_icons: set[str] = set()
        audio_files: dict[str, bytes] = {}
        missing_audio: set[str] = set()
        total_audio_bytes = 0
        exit_image_file: tuple[str, bytes] | None = None
        missing_exit_image = False

        for profile in raw["profiles"]:
            for page in profile["pages"]:
                # Folders carry their own keys, with their own icons and audio,
                # so a bundle has to reach every nested key too; dials carry the
                # same references from their own mapping.
                entries = [
                    *page["keys"].values(),
                    *page.get("dials", {}).values(),
                ]
                for top_key in entries:
                    for key in self._walk_raw_keys(top_key):
                        self._bundle_key_icons(key, icon_files, missing_icons)
                        total_audio_bytes = self._bundle_key_audio(
                            key, audio_files, missing_audio, total_audio_bytes
                        )

        exit_ref = str(
            raw.get("exit_display", {}).get("image_path", "") or ""
        )
        if exit_ref:
            try:
                source = Path(exit_ref).expanduser()
                if (
                    not source.is_file()
                    or source.stat().st_size > MAX_EXIT_IMAGE_BYTES
                    or source.suffix.lower()
                    not in SUPPORTED_EXIT_IMAGE_EXTENSIONS
                ):
                    missing_exit_image = True
                else:
                    data = source.read_bytes()
                    digest = hashlib.sha256(data).hexdigest()
                    suffix = source.suffix.lower()
                    archive_name = f"exit-image/{digest}{suffix}"
                    exit_image_file = (archive_name, data)
                    raw["exit_display"]["image_path"] = (
                        f"{EXPORT_EXIT_IMAGE_PREFIX}{archive_name}"
                    )
            except OSError:
                missing_exit_image = True

        members: dict[str, bytes] = {
            EXPORT_MANIFEST_FILE: json.dumps(
                {"format": EXPORT_FORMAT, "version": EXPORT_VERSION}, indent=2
            ).encode("utf-8"),
            EXPORT_CONFIG_FILE: json.dumps(
                raw, indent=2, ensure_ascii=False
            ).encode("utf-8"),
            **icon_files,
            **audio_files,
        }
        if exit_image_file is not None:
            members[exit_image_file[0]] = exit_image_file[1]
        self._write_bundle_archive(destination, members)
        return ExportResult(
            bundled_icons=len(icon_files),
            missing_icons=len(missing_icons),
            bundled_audio=len(audio_files),
            missing_audio=len(missing_audio),
            bundled_exit_image=exit_image_file is not None,
            missing_exit_image=missing_exit_image,
        )

    def import_bundle(self, source: Path) -> ImportResult:
        """Validate and apply a portable configuration archive."""
        source = Path(source).expanduser()
        try:
            archive = zipfile.ZipFile(source, mode="r")
        except (OSError, zipfile.BadZipFile) as error:
            raise ValueError("This is not a valid LinuxStreamDeck export") from error

        with archive:
            manifest = self._read_bundle_json(
                archive, EXPORT_MANIFEST_FILE, MAX_CONFIG_BYTES
            )
            if (
                manifest.get("format") != EXPORT_FORMAT
                or manifest.get("version") not in SUPPORTED_EXPORT_VERSIONS
            ):
                raise ValueError("This LinuxStreamDeck export version is not supported")
            raw = self._read_bundle_json(
                archive, EXPORT_CONFIG_FILE, MAX_CONFIG_BYTES
            )
            current_password = self.obs.password
            password_needs_migration = self.obs_password_needs_migration
            replacement = Config.from_dict(raw)
            replacement.obs.password = current_password
            replacement.obs_password_needs_migration = password_needs_migration
            icon_payloads: dict[Path, bytes] = {}
            audio_payloads: dict[Path, bytes] = {}
            restored_members: dict[str, Path] = {}
            restored_audio_members: dict[str, Path] = {}
            exit_image_payload: tuple[Path, bytes] | None = None
            total_icon_bytes = 0
            total_audio_bytes = 0

            for key in replacement._key_configs():
                for field_name in ("icon", "icon_off"):
                    ref = getattr(key, field_name)
                    if not ref.startswith(EXPORT_ICON_PREFIX):
                        continue
                    archive_name = ref.removeprefix(EXPORT_ICON_PREFIX)
                    if archive_name in restored_members:
                        setattr(key, field_name, str(restored_members[archive_name]))
                        continue
                    self._validate_icon_member(archive_name)
                    data = self._read_bundle_member(
                        archive, archive_name, MAX_ICON_BYTES
                    )
                    total_icon_bytes += len(data)
                    if total_icon_bytes > MAX_TOTAL_ICON_BYTES:
                        raise ValueError("The exported icon files are too large")
                    suffix = self._safe_icon_suffix(
                        PurePosixPath(archive_name).suffix
                    )
                    digest = hashlib.sha256(data).hexdigest()
                    target = CONFIG_DIR / "imported-icons" / f"{digest}{suffix}"
                    icon_payloads[target] = data
                    restored_members[archive_name] = target
                    setattr(key, field_name, str(target))
                for action, params in replacement._action_params(key):
                    parameter = _ACTION_AUDIO_PARAMETERS.get(action)
                    if parameter is None:
                        continue
                    ref = str(params.get(parameter, "") or "")
                    if not ref.startswith(EXPORT_AUDIO_PREFIX):
                        continue
                    archive_name = ref.removeprefix(EXPORT_AUDIO_PREFIX)
                    if archive_name in restored_audio_members:
                        params[parameter] = str(
                            restored_audio_members[archive_name]
                        )
                        continue
                    self._validate_audio_member(archive_name)
                    data = self._read_bundle_member(
                        archive, archive_name, MAX_AUDIO_BYTES
                    )
                    total_audio_bytes += len(data)
                    if total_audio_bytes > MAX_TOTAL_AUDIO_BYTES:
                        raise ValueError("The exported audio files are too large")
                    suffix = PurePosixPath(archive_name).suffix.lower()
                    digest = hashlib.sha256(data).hexdigest()
                    target = CONFIG_DIR / "imported-audio" / f"{digest}{suffix}"
                    audio_payloads[target] = data
                    restored_audio_members[archive_name] = target
                    params[parameter] = str(target)

            exit_ref = replacement.exit_display.image_path
            if exit_ref.startswith(EXPORT_EXIT_IMAGE_PREFIX):
                archive_name = exit_ref.removeprefix(
                    EXPORT_EXIT_IMAGE_PREFIX
                )
                self._validate_exit_image_member(archive_name)
                data = self._read_bundle_member(
                    archive,
                    archive_name,
                    MAX_EXIT_IMAGE_BYTES,
                )
                suffix = PurePosixPath(archive_name).suffix.lower()
                digest = hashlib.sha256(data).hexdigest()
                target = (
                    CONFIG_DIR
                    / "imported-exit-images"
                    / f"{digest}{suffix}"
                )
                exit_image_payload = (target, data)
                replacement.exit_display.image_path = str(target)

        for target, data in icon_payloads.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            self._write_imported_file(target, data)
        for target, data in audio_payloads.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            self._write_imported_file(target, data)
        if exit_image_payload is not None:
            target, data = exit_image_payload
            target.parent.mkdir(parents=True, exist_ok=True)
            self._write_imported_file(target, data)

        # An import replaces everything, so the state it is about to overwrite is
        # precisely what someone would want back. Snapshot it whatever the
        # interval says.
        self.rotate_backups(force=True)
        replacement.save()
        self.profiles = replacement.profiles
        self.current_profile = replacement.current_profile
        self.obs = replacement.obs
        self.ai = replacement.ai
        # The Client ID travels, the tokens never do: they live in this
        # computer's keyring, so an imported configuration always has to link
        # its own account, exactly as it has to enter its own OBS password.
        self.twitch = replacement.twitch
        self.home_assistant = replacement.home_assistant
        self.screensaver = replacement.screensaver
        self.exit_display = replacement.exit_display
        self.games = replacement.games
        self.brightness = replacement.brightness
        self.close_action = replacement.close_action
        self.editor_width = replacement.editor_width
        self.obs_password_needs_migration = (
            replacement.obs_password_needs_migration
        )
        return ImportResult(
            profiles=len(self.profiles),
            pages=sum(len(profile.pages) for profile in self.profiles),
            keys=sum(1 for _ in self._key_configs()),
            restored_icons=len(icon_payloads),
            restored_audio=len(audio_payloads),
            restored_exit_image=exit_image_payload is not None,
        )

    # ---------- portable single-key import / export ----------

    @classmethod
    def export_key_bundle(
        cls, key: KeyConfig, destination: Path
    ) -> KeyExportResult:
        """Write one key as a portable archive with its icons and audio."""
        raw = asdict(key)
        icon_files: dict[str, bytes] = {}
        missing_icons: set[str] = set()
        audio_files: dict[str, bytes] = {}
        missing_audio: set[str] = set()
        total_audio_bytes = 0

        # A folder key travels with everything inside it.
        for entry in cls._walk_raw_keys(raw):
            cls._bundle_key_icons(entry, icon_files, missing_icons)
            total_audio_bytes = cls._bundle_key_audio(
                entry, audio_files, missing_audio, total_audio_bytes
            )

        cls._write_bundle_archive(
            destination,
            {
                EXPORT_MANIFEST_FILE: json.dumps(
                    {
                        "format": KEY_EXPORT_FORMAT,
                        "version": KEY_EXPORT_VERSION,
                    },
                    indent=2,
                ).encode("utf-8"),
                KEY_EXPORT_FILE: json.dumps(
                    raw, indent=2, ensure_ascii=False
                ).encode("utf-8"),
                **icon_files,
                **audio_files,
            },
        )
        return KeyExportResult(
            bundled_icons=len(icon_files),
            missing_icons=len(missing_icons),
            bundled_audio=len(audio_files),
            missing_audio=len(missing_audio),
        )

    @classmethod
    def import_key_bundle(cls, source: Path) -> KeyImportResult:
        """Validate a single-key archive and restore its portable files."""
        source = Path(source).expanduser()
        try:
            archive = zipfile.ZipFile(source, mode="r")
        except (OSError, zipfile.BadZipFile) as error:
            raise ValueError("This is not a valid LinuxStreamDeck key export") from error

        with archive:
            manifest = cls._read_bundle_json(
                archive, EXPORT_MANIFEST_FILE, MAX_CONFIG_BYTES
            )
            if manifest.get("format") != KEY_EXPORT_FORMAT:
                raise ValueError("This file does not contain a single exported key")
            if manifest.get("version") not in SUPPORTED_KEY_EXPORT_VERSIONS:
                raise ValueError("This LinuxStreamDeck key export version is not supported")
            key = KeyConfig.from_dict(
                cls._read_bundle_json(archive, KEY_EXPORT_FILE, MAX_CONFIG_BYTES)
            )
            icon_payloads: dict[Path, bytes] = {}
            audio_payloads: dict[Path, bytes] = {}
            restored_members: dict[str, Path] = {}
            restored_audio_members: dict[str, Path] = {}
            total_icon_bytes = 0
            total_audio_bytes = 0

            # A folder key restores the assets of everything inside it.
            for entry in cls._walk_keys(key):
                for field_name in ("icon", "icon_off"):
                    ref = getattr(entry, field_name)
                    if not ref.startswith(EXPORT_ICON_PREFIX):
                        continue
                    archive_name = ref.removeprefix(EXPORT_ICON_PREFIX)
                    if archive_name in restored_members:
                        setattr(entry, field_name, str(restored_members[archive_name]))
                        continue
                    cls._validate_icon_member(archive_name)
                    data = cls._read_bundle_member(archive, archive_name, MAX_ICON_BYTES)
                    total_icon_bytes += len(data)
                    if total_icon_bytes > MAX_TOTAL_ICON_BYTES:
                        raise ValueError("The exported icon files are too large")
                    suffix = cls._safe_icon_suffix(PurePosixPath(archive_name).suffix)
                    digest = hashlib.sha256(data).hexdigest()
                    target = CONFIG_DIR / "imported-icons" / f"{digest}{suffix}"
                    icon_payloads[target] = data
                    restored_members[archive_name] = target
                    setattr(entry, field_name, str(target))

                for action, params in cls._action_params(entry):
                    parameter = _ACTION_AUDIO_PARAMETERS.get(action)
                    if parameter is None:
                        continue
                    ref = str(params.get(parameter, "") or "")
                    if not ref.startswith(EXPORT_AUDIO_PREFIX):
                        continue
                    archive_name = ref.removeprefix(EXPORT_AUDIO_PREFIX)
                    if archive_name in restored_audio_members:
                        params[parameter] = str(restored_audio_members[archive_name])
                        continue
                    cls._validate_audio_member(archive_name)
                    data = cls._read_bundle_member(archive, archive_name, MAX_AUDIO_BYTES)
                    total_audio_bytes += len(data)
                    if total_audio_bytes > MAX_TOTAL_AUDIO_BYTES:
                        raise ValueError("The exported audio files are too large")
                    suffix = PurePosixPath(archive_name).suffix.lower()
                    digest = hashlib.sha256(data).hexdigest()
                    target = CONFIG_DIR / "imported-audio" / f"{digest}{suffix}"
                    audio_payloads[target] = data
                    restored_audio_members[archive_name] = target
                    params[parameter] = str(target)

        for target, data in (*icon_payloads.items(), *audio_payloads.items()):
            target.parent.mkdir(parents=True, exist_ok=True)
            cls._write_imported_file(target, data)
        return KeyImportResult(
            key=key,
            restored_icons=len(icon_payloads),
            restored_audio=len(audio_payloads),
        )

    def _key_configs(self):
        for profile in self.profiles:
            for page in profile.pages:
                for key in page.keys.values():
                    yield from self._walk_keys(key)
                # Dials hold actions and reference icons and audio exactly as
                # keys do, so every bundle and migration has to reach them too.
                for dial in page.dials.values():
                    yield from self._walk_keys(dial)

    @classmethod
    def _walk_keys(cls, key: KeyConfig):
        """A key followed by every key nested inside its folders."""
        yield key
        folder = key.contents
        if folder is not None:
            for nested in folder.keys.values():
                yield from cls._walk_keys(nested)

    @classmethod
    def _walk_raw_keys(cls, key: dict):
        """`_walk_keys` over the serialized form used while bundling."""
        yield key
        folder = key.get("folder")
        if isinstance(folder, dict):
            for nested in folder.get("keys", {}).values():
                if isinstance(nested, dict):
                    yield from cls._walk_raw_keys(nested)

    @staticmethod
    def _raw_action_params(key: dict):
        action = key.get("action", "")
        params = key.get("params", {})
        if isinstance(params, dict):
            yield action, params
        for field_name in STEP_FIELDS:
            for step in key.get(field_name, []):
                if isinstance(step, dict) and isinstance(step.get("params"), dict):
                    yield step.get("action", ""), step["params"]

    @staticmethod
    def _action_params(key: KeyConfig):
        if isinstance(key.params, dict):
            yield key.action, key.params
        for field_name in STEP_FIELDS:
            for step in getattr(key, field_name, []):
                if isinstance(step.params, dict):
                    yield step.action, step.params

    @staticmethod
    def _safe_icon_suffix(suffix: str) -> str:
        suffix = suffix.lower()
        if (
            suffix.startswith(".")
            and len(suffix) <= 10
            and suffix[1:].isalnum()
        ):
            return suffix
        return ".img"

    @staticmethod
    def _write_bundle_archive(destination: Path, members: dict[str, bytes]) -> None:
        """Write a portable archive atomically, replacing any existing file."""
        destination = Path(destination).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.NamedTemporaryFile(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        )
        temporary_path = Path(temporary.name)
        temporary.close()
        try:
            with zipfile.ZipFile(
                temporary_path, mode="w", compression=zipfile.ZIP_DEFLATED
            ) as archive:
                for archive_name, data in members.items():
                    archive.writestr(archive_name, data)
            temporary_path.replace(destination)
        finally:
            temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _write_imported_file(target: Path, data: bytes) -> None:
        temporary = tempfile.NamedTemporaryFile(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
            delete=False,
        )
        temporary_path = Path(temporary.name)
        try:
            temporary.write(data)
            temporary.close()
            temporary_path.replace(target)
        finally:
            temporary.close()
            temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _write_json_file(path: Path, value: dict) -> None:
        payload = json.dumps(value, indent=2, ensure_ascii=False).encode("utf-8")
        temporary = tempfile.NamedTemporaryFile(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        )
        temporary_path = Path(temporary.name)
        try:
            temporary.write(payload)
            temporary.close()
            os.chmod(temporary_path, 0o600)
            temporary_path.replace(path)
        finally:
            temporary.close()
            temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _validate_icon_member(name: str) -> None:
        path = PurePosixPath(name)
        if (
            path.is_absolute()
            or len(path.parts) != 2
            or path.parts[0] != "icons"
            or any(part in ("", ".", "..") for part in path.parts)
        ):
            raise ValueError("The export contains an invalid icon path")

    @staticmethod
    def _validate_audio_member(name: str) -> None:
        path = PurePosixPath(name)
        if (
            path.is_absolute()
            or len(path.parts) != 2
            or path.parts[0] != "audio"
            or any(part in ("", ".", "..") for part in path.parts)
            or path.suffix.lower() not in SUPPORTED_AUDIO_EXTENSIONS
        ):
            raise ValueError("The export contains an invalid audio path")

    @staticmethod
    def _validate_exit_image_member(name: str) -> None:
        path = PurePosixPath(name)
        if (
            path.is_absolute()
            or len(path.parts) != 2
            or path.parts[0] != "exit-image"
            or any(part in ("", ".", "..") for part in path.parts)
            or path.suffix.lower() not in SUPPORTED_EXIT_IMAGE_EXTENSIONS
        ):
            raise ValueError("The export contains an invalid exit image path")

    @staticmethod
    def _read_bundle_member(
        archive: zipfile.ZipFile, name: str, maximum_size: int
    ) -> bytes:
        try:
            info = archive.getinfo(name)
        except KeyError as error:
            raise ValueError(f"The export is missing {name}") from error
        if info.is_dir() or info.file_size > maximum_size:
            raise ValueError(f"The exported file {name} is invalid or too large")
        return archive.read(info)

    @classmethod
    def _read_bundle_json(
        cls, archive: zipfile.ZipFile, name: str, maximum_size: int
    ) -> dict:
        data = cls._read_bundle_member(archive, name, maximum_size)
        try:
            value = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"The exported file {name} is not valid JSON") from error
        if not isinstance(value, dict):
            raise ValueError(f"The exported file {name} must contain a JSON object")
        return value
