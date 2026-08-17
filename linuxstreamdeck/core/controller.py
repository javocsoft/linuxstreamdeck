"""Central controller: ties together configuration, device, actions and UI.

- Receives key presses (physical via `deck.key` or virtual from the UI) and
  runs configured actions on worker threads.
- Re-renders the active page when the OBS state, the page or the configuration
  changes, sending each image to the physical deck and to the UI. Rendering has
  its own worker so long actions cannot starve visual feedback.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import actions as action_registry
from .actions import ActionContext, parse_duration
from .audio import play_audio
from .clocks import ClockRuntime, ClockSnapshot, TimerCompletion
from .config import (
    DEFAULT_FOLDER_ICON,
    FOLDER_BACK_INDEX,
    KIND_FOLDER,
    KIND_MULTI,
    KIND_PRESS,
    KIND_RANDOM,
    KIND_SINGLE,
    KIND_TOGGLE,
    MAX_FOLDER_DEPTH,
    ON_ERROR_CONTINUE,
    ON_ERROR_STOP,
    STEP_FIELDS,
    ActionStep,
    Config,
    ImportResult,
    KeyConfig,
    KeyGrid,
    folder_depth,
)
from .events import EventBus
from ..twitch import events as twitch_events
from ..twitch.attention import Attention
from ..device.manager import DeckManager
from ..device import renderer
from ..games import GameManager

log = logging.getLogger(__name__)

BUSY_PULSE_SECONDS = 0.75

# How long a key stays marked after one of its actions failed. Long enough to be
# noticed by someone looking at the deck a moment later, short enough that it
# does not go on hiding the key's real state. The message it used to rely on
# went to a status bar in a window that is normally hidden behind the status
# icon, so the deck itself has to say it.
ERROR_FEEDBACK_SECONDS = 4.0

# Press-gesture keys. Holding at least LONG_PRESS_SECONDS is a long press; a
# second tap within DOUBLE_PRESS_SECONDS of the first release is a double press.
# A single press therefore only resolves once that window has passed.
LONG_PRESS_SECONDS = 0.5
DOUBLE_PRESS_SECONDS = 0.35

# Which list of a "Single / double / long press" key to run. Only a caller that
# already knows the gesture passes one of these: the physical deck resolves the
# real gesture from its own timing, and a virtual click stays a single press.
GESTURE_SINGLE = "single"
GESTURE_DOUBLE = "double"
GESTURE_LONG = "long"

# How many key changes can be taken back. The history is dropped on every
# change of view, so this only ever bounds one grid's worth of edits.
UNDO_DEPTH = 20


def _game_active(owner) -> bool:
    """Read optional game ownership from real or reduced controllers."""
    games = getattr(owner, "games", None)
    return bool(games is not None and getattr(games, "active", False))


def _stops_on_error(kc) -> bool:
    """Whether a failing action should abandon the rest of this key.

    Only a list of actions can answer yes: a single-action key has nothing left
    to abandon, so its choice would be meaningless either way.
    """
    return getattr(kc, "on_error", ON_ERROR_CONTINUE) == ON_ERROR_STOP


def gesture_steps(kc, gesture: str) -> list:
    """The list a gesture key runs for one gesture, defaulting to single."""
    if gesture == GESTURE_DOUBLE:
        return kc.steps_double
    if gesture == GESTURE_LONG:
        return kc.steps_long
    return kc.steps_single


# The reserved Back key inside a folder is chrome, not content, so it is drawn
# slightly lighter than the default key background.
FOLDER_BACK_BG = "#2a2a36"
FOLDER_BACK_ICON = "mdi:arrow-left-circle"
FOLDER_BACK_LABEL = "Back"

# Transient key identity: (profile, page, folder path, key index). The folder
# path is empty at the page root, so keys inside a folder can never collide
# with the keys of the page that holds it.
RuntimeKey = tuple[int, int, tuple[int, ...], int]

# The folder path a dial's runtime state is filed under. A real path holds key
# indices, which are never negative, so this can never name a folder.
DIAL_PATH = (-1,)

# How the three encoder gestures map onto the dial's stored action lists.
DIAL_STEPS = {
    "left": "steps_left",
    "right": "steps_right",
    "press": "steps_press",
}
# A spin reports several ticks at once, and each one queues the dial's steps.
# Without a ceiling one flick of the wrist could keep an action worker busy
# long after the hand stopped.
MAX_DIAL_TICKS = 8

# How often a visible statistics key is recomposed. It only has to be as fast
# as a number is worth reading; the client caches the sample behind it.
STATS_REFRESH_SECONDS = 1.0
STATS_ACTION_ID = "obs.stats"

# The same for Twitch, but slower: its numbers come over the network and Twitch
# aggregates them on its own side, so repainting faster would only spend
# requests without ever showing a different value.
TWITCH_REFRESH_SECONDS = 2.0
TWITCH_STATS_ACTION_ID = "twitch.stats"
# Alert keys repaint on the clock too: the number they show is how long
# somebody has been waiting, which changes with nothing happening.
ALERT_ACTION_ID = "twitch.alert"
ALERT_REFRESH_SECONDS = 1.0
# A key showing a value from an HTTP endpoint. Its interval is chosen per key
# rather than fixed here, because the endpoint is somebody else's server and
# only its owner knows what it will tolerate.
WEB_ACTION_ID = "web.request"
# A mute key has to be believed, and the mixer can be changed from anywhere --
# the desktop's own volume panel, a headset button. Only mute keys ask for it,
# and one cached reading of the mixer serves all of them at once.
MIXER_ACTION_ID = "sys.volume"
# A machine measurement on a key. It shares the OBS statistics interval: both
# are numbers that change with nothing happening, and both are local reads.
SYSTEM_STATS_ACTION_ID = "sys.stats"
# A key showing what a Home Assistant entity reports. Its interval is per key,
# because the server is somebody's own and only they know what it will take.
HA_STATE_ACTION_ID = "ha.state"
# A switch key draws whether its entity is on, so it has to keep up with a
# light somebody turned off from their phone. It has no interval of its own --
# an on/off key is not something anybody wants to tune -- and the client's own
# cache decides when a repaint becomes a request.
# A media key that also shows what is playing. Only then: a plain transport
# key has no state to draw and must cost nothing.
MEDIA_ACTION_ID = "sys.media"
MEDIA_REFRESH_SECONDS = 2.0
HA_SWITCH_ACTION_ID = "ha.switch"
HA_SWITCH_REFRESH_SECONDS = 10.0
MIXER_REFRESH_SECONDS = 2.0
# How long each half of an alert key's breath lasts. The activity thread's own
# phase only advances while something is running, so a key that breathes
# without running needs one derived from the clock instead.
ALERT_PULSE_SECONDS = 1.0

# The whole deck lit at once for an alert, for somebody looking at a game rather
# than at the deck: a sound can be missed under headphones, a panel going bright
# in the corner of an eye cannot. Three pulses in a shade over a second, which
# keeps it under three flashes a second. A Stream Deck is nowhere near the
# screen area photosensitivity guidance is written for, but staying the right
# side of that threshold costs nothing at all.
FLASH_PULSES = 3
FLASH_ON_SECONDS = 0.18
FLASH_OFF_SECONDS = 0.16
FLASH_OFF_COLOR = "#000000"
# How long to give the screen saver to let go of the deck before painting.
FLASH_WAKE_SECONDS = 1.0

# Actions that can draw a live thumbnail of an OBS scene. The tick has to be at
# least as fast as the quickest rate any of them offers, or a key asking for two
# frames a second would silently get one.
PREVIEW_ACTION_IDS = frozenset({"obs.scene_switch", "obs.scene_preview"})
LIVE_TICK_SECONDS = 0.5


class _ObsNames:
    """What OBS currently holds, fetched once per check and reused.

    Every list is asked for at most once: a page can hold a dozen references to
    the same scene, and each lookup is a request through the one serialized
    connection.
    """

    def __init__(self, obs) -> None:
        self._obs = obs
        self._cache: dict[tuple[str, str], list[str] | None] = {}
        current = obs.try_request("GetSceneCollectionList") or {}
        self.collection = current.get("currentSceneCollectionName", "")

    def available(self, kind: str, parent: str) -> list[str] | None:
        key = (kind, parent)
        if key not in self._cache:
            self._cache[key] = self._fetch(kind, parent)
        return self._cache[key]

    def _fetch(self, kind: str, parent: str) -> list[str] | None:
        obs = self._obs
        flat = {
            "scenes": obs.get_scenes,
            "inputs": obs.get_inputs,
            "media_inputs": obs.get_media_inputs,
            "text_inputs": obs.get_text_inputs,
            "browser_inputs": obs.get_browser_inputs,
            "transitions": obs.get_transitions,
            "scene_collections": obs.get_scene_collections,
            "profiles": obs.get_profiles,
        }
        try:
            if kind in flat:
                return list(flat[kind]())
            if not parent or not self._parent_exists(kind, parent):
                # Unknown, not missing. A source whose scene is itself gone
                # cannot be judged, and reporting both would count one rename
                # twice and offer a fix that could not work.
                return None
            if kind == "sources_in_scene":
                return list(obs.get_sources_in_scene(parent))
            if kind == "audio_sources_in_scene":
                return list(obs.get_audio_sources_in_scene(parent))
            if kind == "filters_of_source":
                return list(obs.get_filters_of_source(parent))
        except Exception:
            log.debug("Could not list %s for the check", kind, exc_info=True)
        return None

    def _parent_exists(self, kind: str, parent: str) -> bool:
        """Whether the scene or source a dependent value hangs off is still there."""
        if kind in ("sources_in_scene", "audio_sources_in_scene"):
            return parent in (self.available("scenes", "") or [])
        if kind == "filters_of_source":
            # A source may be an input or a scene item, so both are accepted.
            if parent in (self.available("inputs", "") or []):
                return True
            return parent in (self.available("scenes", "") or [])
        return True


class _ExecutionControl:
    def __init__(self, predecessor: threading.Event | None = None) -> None:
        self.cancel = threading.Event()
        self.finished = threading.Event()
        self.predecessor = predecessor


class _UndoEntry:
    """One reversible key change: what those slots held, and where."""

    def __init__(self, label: str, container, before: dict) -> None:
        self.label = label
        self.container = container      # the Page or Folder it happened in
        self.before = before            # index -> KeyConfig or None


class DeckController:
    def __init__(
        self,
        config: Config,
        bus: EventBus,
        obs,
        deck: DeckManager,
        twitch=None,
        home_assistant=None,
    ) -> None:
        self.config = config
        self.bus = bus
        self.obs = obs
        self.deck = deck
        self.twitch = twitch
        self.home_assistant = home_assistant
        self.ctx = ActionContext(
            obs=obs, controller=self, bus=bus, twitch=twitch,
            home_assistant=home_assistant,
        )
        # Which folder of the active page is open. View state only: it is never
        # persisted, so the deck always starts at the page root.
        self._folder_path: tuple[int, ...] = ()
        # Reversible key changes for the grid on screen; see _record_undo.
        self._undo: list[_UndoEntry] = []
        self._redo: list[_UndoEntry] = []
        self._stopping = threading.Event()
        self._action_executor = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="action",
        )
        self._notification_executor = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="timer-sound",
        )
        self._notification_lock = threading.Lock()
        self._notification_controls: dict[
            RuntimeKey,
            threading.Event,
        ] = {}
        # Rendering must not wait behind long-running actions such as Wait.
        self._render_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="render",
        )
        self._render_pending = threading.Event()
        self._clocks = ClockRuntime(
            self._refresh_runtime_keys,
            self._on_timer_finished,
        )
        # What Twitch has reported and nobody has acknowledged yet. Owned here
        # for the same reason ClockRuntime is: it is transient key state, and
        # the sound it may trigger belongs on the notification executor.
        self.attention = Attention(self._on_alert)
        # When each alert key last made a noise, for the optional reminder
        # interval only. Whether it should make one at all is asked of the
        # alerts themselves in _on_alert, never of this.
        self._alerting: dict[RuntimeKey, float] = {}
        # Set while the whole deck is being lit for an alert. Normal renders
        # stand aside for it exactly as they do for the screen saver, or they
        # would paint real keys over half the pulses.
        self._flashing = threading.Event()
        # ON/OFF state of toggle keys, keyed by RuntimeKey
        self._toggle: dict[RuntimeKey, bool] = {}
        # What each counter key is showing. Transient exactly like the
        # toggle state beside it: a count is a tally kept during a session,
        # and writing it to the configuration on every press would be a
        # file write plus a backup rotation per press.
        self._counters: dict[RuntimeKey, int] = {}
        # Number of queued/running feedback-enabled invocations for each key.
        self._running: dict[RuntimeKey, int] = {}
        # Keys whose last run failed, and when that mark stops being shown.
        self._failed: dict[RuntimeKey, float] = {}
        # A temporary report drawn over the whole grid; see show_board().
        self._board: dict[int, dict] | None = None
        self._running_lock = threading.Lock()
        self._busy_phase = False
        self._busy_wakeup = threading.Event()
        # Restartable executions (currently audio) are replaced per key.
        self._execution_lock = threading.Lock()
        self._execution_controls: dict[
            RuntimeKey,
            _ExecutionControl,
        ] = {}
        # Press-gesture keys: when each one went down, and the timer waiting to
        # see whether a second tap turns a single press into a double one.
        self._gesture_lock = threading.Lock()
        self._gesture_down: dict[RuntimeKey, float] = {}
        self._gesture_timers: dict[RuntimeKey, threading.Timer] = {}
        # Games are a real exclusive mode, not a display overlay: their input
        # is consumed before configured actions and normal renders stand aside
        # until the session restores the active page.
        self.games = GameManager(
            bus,
            deck,
            config.games,
            restore=self._restore_after_game,
        )
        self._busy_thread = threading.Thread(
            target=self._busy_loop,
            daemon=True,
            name="key-activity",
        )
        self._busy_thread.start()
        # Statistics and live previews change with nothing happening. Nothing
        # on the bus announces that — `obs.state` fires on state changes, and a
        # rising frame counter or a moving camera is not one — so they are the
        # only keys repainted by the clock rather than by an event.
        self._live_thread = threading.Thread(
            target=self._live_loop,
            daemon=True,
            name="key-live",
        )
        self._live_thread.start()

        bus.subscribe("deck.key", self._on_deck_key)
        bus.subscribe("deck.dial", self._on_deck_dial)
        bus.subscribe("deck.touch", self._on_deck_touch)
        bus.subscribe("deck.connected", lambda t, d: self.refresh())
        bus.subscribe("deck.screensaver", self._on_screensaver)
        bus.subscribe("obs.state", lambda t, d: self.refresh())
        # Linking or losing a Twitch account changes which keys can work, and
        # therefore which of them are faded.
        bus.subscribe("twitch.state", lambda t, d: self.refresh())

    # ---------- pages ----------

    @property
    def current_profile(self) -> int:
        return self.config.current_profile

    @property
    def current_page(self) -> int:
        return self.config.current_page

    @property
    def page(self):
        return self.config.pages[self.config.current_page]

    def _tkey(self, index: int) -> RuntimeKey:
        """Stable identity for transient key state (see RuntimeKey)."""
        return self._path_tkey(self._folder_path, index)

    def _path_tkey(self, path: tuple[int, ...], index: int) -> RuntimeKey:
        """The same identity for a slot of a grid that is not the open one.

        Drag and drop can now cross a folder boundary, so a key's state has to
        be addressable where it came from as well as where it is going.
        """
        return (self.current_profile, self.current_page, tuple(path), index)

    def _dial_tkey(self, index: int) -> RuntimeKey:
        """The same identity for a dial, in its own namespace.

        Dials are numbered independently of keys, so dial 0 and key 0 must not
        share transient state. `DIAL_PATH` cannot collide with a real folder
        path, whose entries are all key indices and therefore never negative.
        """
        return (self.current_profile, self.current_page, DIAL_PATH, index)

    def _view(self) -> tuple[int, int, tuple[int, ...]]:
        """What the deck is showing right now."""
        return (self.current_profile, self.current_page, self._folder_path)

    # ---------- folders ----------

    @property
    def folder_path(self) -> tuple[int, ...]:
        """Key indices walked from the page root to the open folder."""
        return self._folder_path

    @property
    def container(self) -> KeyGrid:
        """The grid on screen: the active page, or the folder opened in it."""
        grid: KeyGrid = self.page
        for index in self._folder_path:
            contents = self._folder_at(grid, index)
            if contents is None:
                # The path stopped resolving (its folder was cleared, or the
                # configuration was replaced under us); fall back to the page.
                self._folder_path = ()
                return self.page
            grid = contents
        return grid

    @staticmethod
    def _folder_at(grid: KeyGrid, index: int):
        kc = grid.key(index)
        return kc.contents if kc is not None else None

    def _grid_at(self, path: tuple[int, ...]) -> KeyGrid | None:
        """The grid at a folder path, or None when the path no longer resolves.

        Unlike `container` this never heals a broken path back to the page: a
        caller asking for one particular grid must be told it has gone rather
        than handed a different one.
        """
        grid: KeyGrid = self.page
        for index in tuple(path):
            contents = self._folder_at(grid, index)
            if contents is None:
                return None
            grid = contents
        return grid

    def is_reserved_key(self, index: int) -> bool:
        """Whether this slot is the folder Back key rather than a real key."""
        return bool(self._folder_path) and index == FOLDER_BACK_INDEX

    def folder_trail(self) -> list[tuple[tuple[int, ...], str]]:
        """(path, name) for each open folder, outermost first."""
        trail: list[tuple[tuple[int, ...], str]] = []
        grid: KeyGrid = self.page
        path: tuple[int, ...] = ()
        for index in self._folder_path:
            kc = grid.key(index)
            contents = kc.contents if kc is not None else None
            if contents is None:
                break
            path += (index,)
            trail.append((path, kc.folder_name()))
            grid = contents
        return trail

    def open_folder(self, index: int) -> bool:
        """Show the folder held by this key. Returns whether it opened."""
        if self._folder_at(self.container, index) is None:
            return False
        if len(self._folder_path) >= MAX_FOLDER_DEPTH:
            self.bus.emit(
                "status",
                text=f"Folders cannot be nested more than {MAX_FOLDER_DEPTH} levels",
            )
            return False
        self._set_folder_path(self._folder_path + (index,))
        return True

    def close_folder(self) -> bool:
        """Leave the open folder for the one (or page) that holds it."""
        if not self._folder_path:
            return False
        self._set_folder_path(self._folder_path[:-1])
        return True

    def set_folder_path(self, path) -> None:
        """Jump straight to a folder path; an empty path is the page root."""
        resolved: tuple[int, ...] = ()
        grid: KeyGrid = self.page
        for index in tuple(path)[:MAX_FOLDER_DEPTH]:
            contents = self._folder_at(grid, index)
            if contents is None:
                break
            resolved += (index,)
            grid = contents
        self._set_folder_path(resolved)

    def _set_folder_path(self, path: tuple[int, ...]) -> None:
        """Move to another grid. Deliberately transient: nothing is saved.

        Which folder is open is view state, not configuration: restarting, or
        changing page or profile, always comes back to the page root.
        """
        if path == self._folder_path:
            return
        self._folder_path = path
        # Its indices address the grid being left.
        self.forget_undo()
        # A pending gesture timer holds an index of the grid being left.
        self._clear_gestures()
        self.bus.emit(
            "folder.changed",
            path=path,
            trail=self.folder_trail(),
        )
        self.refresh()

    def _leave_folders(self) -> None:
        """Return to the page root.

        An open folder never survives a page, profile or configuration change:
        its indices would refer to a different grid entirely.
        """
        if not self._folder_path:
            return
        self._folder_path = ()
        self.forget_undo()
        self._clear_gestures()
        self.bus.emit("folder.changed", path=(), trail=[])

    def can_add_folder(self) -> bool:
        """Whether a new, empty folder still fits at the current depth."""
        return len(self._folder_path) < MAX_FOLDER_DEPTH

    def fits_here(self, kc: KeyConfig | None) -> bool:
        """Whether a key, with the folders it carries, fits at this depth."""
        if kc is None:
            return True
        return len(self._folder_path) + folder_depth(kc) <= MAX_FOLDER_DEPTH

    # ---------- profiles ----------

    def set_profile(self, index: int) -> None:
        """Switch profile (loads its set of pages/keys)."""
        if not (0 <= index < len(self.config.profiles)) or index == self.current_profile:
            return
        self.config.current_profile = index
        self._leave_folders()
        self.forget_undo()
        self.config.save()
        prof = self.config.profile
        self.bus.emit("profile.changed", index=index, name=prof.name,
                      description=prof.description)
        # the current page is defined by the profile itself
        self.bus.emit("page.changed", index=self.current_page, name=self.page.name)
        self.refresh()

    def add_profile(self, name: str, description: str = "") -> None:
        from .config import Profile

        self.config.profiles.append(Profile(name=name or "Profile", description=description))
        self.config.save()
        self.set_profile(len(self.config.profiles) - 1)

    def duplicate_profile(self, index: int) -> None:
        """Copy a whole profile, so a working setup can be varied safely.

        The copy is independent down to every key, and its page names are kept,
        so a `nav.page.go` inside it points at the copy's own page rather than
        back at the original.
        """
        from .config import unique_name

        profiles = self.config.profiles
        if not 0 <= index < len(profiles):
            return
        source = profiles[index]
        name = unique_name(source.name, (p.name for p in profiles))
        profiles.append(source.clone(name))
        self.config.save()
        self.set_profile(len(profiles) - 1)
        self.bus.emit("status", text=f"Profile duplicated as {name}")

    def update_profile(self, name: str, description: str) -> None:
        prof = self.config.profile
        prof.name = name or prof.name
        prof.description = description
        self.config.save()
        self.bus.emit("profile.changed", index=self.current_profile,
                      name=prof.name, description=prof.description)

    def delete_profile(self, index: int) -> None:
        if len(self.config.profiles) <= 1:
            self.bus.emit("status", text="You can't delete the only profile")
            return
        del self.config.profiles[index]
        # no need to discard the deleted profile's ON/OFF states individually:
        # clear them all and re-render whichever profile becomes active.
        self._toggle.clear()
        self._counters.clear()
        self._clear_time_actions()
        # Those keys are gone, or their indices have shifted under it.
        self.forget_undo()
        self._leave_folders()
        self.config.current_profile = min(self.current_profile, len(self.config.profiles) - 1)
        self.config.save()
        prof = self.config.profile
        self.bus.emit("profile.changed", index=self.current_profile,
                      name=prof.name, description=prof.description)
        self.bus.emit("page.changed", index=self.current_page, name=self.page.name)
        self.refresh()

    def set_page(self, index: int) -> None:
        if (
            0 <= index < len(self.config.pages)
            and index != self.current_page
        ):
            self.config.current_page = index
            self._leave_folders()
            self.forget_undo()
            self.config.save()
            self.bus.emit("page.changed", index=index, name=self.page.name)
            self.refresh()

    def set_page_by_name(self, name: str) -> bool:
        for i, p in enumerate(self.config.pages):
            if p.name == name:
                self.set_page(i)
                return True
        return False

    def add_page(self, name: str) -> None:
        from .config import Page

        if any(page.name == name for page in self.config.pages):
            self.bus.emit(
                "status",
                text=f"A page named {name} already exists",
            )
            return
        self.config.pages.append(Page(name=name))
        self.config.save()
        self.set_page(len(self.config.pages) - 1)

    def rename_page(self, name: str) -> None:
        if not name:
            return
        old_name = self.page.name
        if name == old_name:
            return
        if any(page.name == name for page in self.config.pages):
            self.bus.emit(
                "status",
                text=f"A page named {name} already exists",
            )
            return
        self.page.name = name
        for page in self.config.pages:
            # Destinations inside a folder must be rewritten too, and a dial
            # can navigate exactly as a key can.
            for top_key in (*page.keys.values(), *page.dials.values()):
                for key in self.config._walk_keys(top_key):
                    for action, params in self.config._action_params(key):
                        if (
                            action == "nav.page.go"
                            and params.get("page") == old_name
                        ):
                            params["page"] = name
        self.config.save()
        self.bus.emit("page.changed", index=self.current_page, name=self.page.name)

    def move_page(self, index: int, target: int) -> None:
        """Move a page to another position, keeping the same page on screen.

        Nothing has to be rewritten: page navigation is by name, and a move
        changes no name. The transient toggle and clock state is keyed by page
        index, though, and every position between the two has just come to mean
        a different page, so it is dropped exactly as a deletion drops it.
        """
        pages = self.config.pages
        if not (0 <= index < len(pages) and 0 <= target < len(pages)):
            return
        if index == target:
            return
        pages.insert(target, pages.pop(index))
        self._toggle.clear()
        self._counters.clear()
        self._clear_time_actions()
        self.forget_undo()
        self._leave_folders()
        self.config.current_page = self._shifted_index(
            self.current_page, index, target
        )
        self.config.save()
        self.bus.emit("page.changed", index=self.current_page, name=self.page.name)
        self.refresh()

    @staticmethod
    def _shifted_index(current: int, index: int, target: int) -> int:
        """Where `current` ends up once the item at `index` moves to `target`."""
        if current == index:
            return target
        if index < current <= target:
            return current - 1
        if target <= current < index:
            return current + 1
        return current

    def duplicate_page(self, index: int) -> None:
        """Copy a page and its keys, placing the copy right after the original."""
        from .config import unique_name

        pages = self.config.pages
        if not 0 <= index < len(pages):
            return
        source = pages[index]
        name = unique_name(source.name, (page.name for page in pages))
        pages.insert(index + 1, source.clone(name))
        # Indices at and after the insertion now mean a different page.
        self._toggle.clear()
        self._counters.clear()
        self._clear_time_actions()
        self.forget_undo()
        self._leave_folders()
        self.config.current_page = self.current_page + (
            1 if self.current_page > index else 0
        )
        self.config.save()
        self.set_page(index + 1)
        self.bus.emit("status", text=f"Page duplicated as {name}")

    def delete_page(self, index: int) -> None:
        pages = self.config.pages
        if len(pages) <= 1:
            self.bus.emit("status", text="You can't delete the only page")
            return
        del pages[index]
        # the ON/OFF states of toggle keys reference the page index, which shifts
        # after deletion; they are transient, so clear them and re-render.
        self._toggle.clear()
        self._counters.clear()
        self._clear_time_actions()
        # Those keys are gone, or their indices have shifted under it.
        self.forget_undo()
        self._leave_folders()
        self.config.current_page = min(self.current_page, len(pages) - 1)
        self.config.save()
        self.bus.emit("page.changed", index=self.current_page, name=self.page.name)
        self.refresh()

    # ---------- checking references against OBS ----------

    def check_references(self):
        """Check the grid you are looking at against what OBS has loaded.

        Deliberately only on demand and only here: switching scene collection
        replaces every name at once, so a checker that ran by itself would cry
        wolf on every switch and be ignored by the time it mattered.

        It is also why nothing may call this on its own: only the loaded
        collection can be listed, so a key belonging to another one is
        indistinguishable from a broken key, and only the user standing in
        front of the grid knows which is which.
        """
        from . import references

        if not self.obs.connected:
            raise ConnectionError("Connect to OBS before checking the keys")
        # Dials belong to the page, so they are only in scope at the page root.
        dials = self.page.dials if not self._folder_path else None
        found = references.collect(self.container, dials)
        lookup = _ObsNames(self.obs)
        findings = references.check(found, lookup.available)
        return references.Report(
            collection=lookup.collection,
            findings=findings,
            checked=len(found),
            keys=self.container.configured_keys(),
        )

    def apply_reference_fix(self, finding, replacement: str) -> int:
        """Repoint a finding's references, after snapshotting the current state.

        A bulk rewrite touches more keys than the undo history is scoped to
        hold, so the safety net is a forced backup instead: an unwanted fix is
        undone by restoring it.
        """
        from . import references
        from .config import CONFIG_FILE, Config

        # Snapshotting copies the file on disk, so there has to be one: on a
        # configuration that has never been saved the backup would fail
        # silently and the promise the dialog makes would be false.
        if not CONFIG_FILE.exists():
            self.config.save()
        Config.rotate_backups(force=True)
        fixed = references.apply_fix(finding, replacement)
        if fixed:
            self.config.save()
            self.refresh()
        return fixed

    # ---------- configuration import ----------

    def import_configuration(self, source: Path) -> ImportResult:
        """Replace the configuration and apply its runtime settings."""
        result = self.config.import_bundle(source)
        self._adopt_replaced_configuration()
        return result

    def restore_backup(self, path: Path):
        """Roll the configuration back to one of the automatic backups.

        Identical to an import once the file is read: the whole configuration
        has been replaced, so every stored index means something else and every
        runtime setting has to be applied again.
        """
        info = self.config.restore_backup(path)
        self._adopt_replaced_configuration()
        return info

    def _adopt_replaced_configuration(self) -> None:
        self.games.adopt_settings(self.config.games)
        self._toggle.clear()
        self._counters.clear()
        self._clear_time_actions()
        # Those keys are gone, or their indices have shifted under it.
        self.forget_undo()
        self._leave_folders()
        self.deck.set_brightness(self.config.brightness)
        screen = self.config.screensaver
        self.deck.configure_screensaver(
            screen.enabled,
            screen.style,
            screen.idle_minutes,
            screen.intensity,
        )
        exit_display = self.config.exit_display
        self.deck.configure_exit_display(
            exit_display.mode,
            exit_display.image_path,
        )
        cfg = self.config.obs
        self.obs.configure(cfg.host, cfg.port, cfg.password)
        self.obs.reconnect_now()
        profile = self.config.profile
        self.bus.emit(
            "profile.changed",
            index=self.current_profile,
            name=profile.name,
            description=profile.description,
        )
        self.bus.emit(
            "page.changed",
            index=self.current_page,
            name=self.page.name,
        )
        self.refresh()

    # ---------- key editing (move / copy / paste / clear) ----------

    # ---------- undo ----------

    @staticmethod
    def _snapshot(label: str, container, indices) -> _UndoEntry:
        """What those slots hold right now, as a restorable entry."""
        before = {}
        for index in indices:
            stored = container.key(index)
            before[index] = stored.clone() if stored is not None else None
        return _UndoEntry(label, container, before)

    def _record_undo(self, label: str, *indices: int) -> None:
        """Remember what the affected keys held, so the change can be taken back.

        Only the keys themselves are kept, never the transient toggle/clock
        state: undoing restores a saved configuration, and a restored key starts
        from a clean runtime state exactly as a pasted one does.
        """
        self._undo.append(self._snapshot(label, self.container, indices))
        del self._undo[:-UNDO_DEPTH]
        # A fresh change branches away from anything that had been undone, so
        # the redo future no longer describes this configuration.
        self._redo.clear()

    def _apply_entry(self, entry: _UndoEntry) -> _UndoEntry:
        """Restore an entry, returning the entry that reverses it.

        Taking the inverse before writing is what makes undo and redo each
        other's mirror image, so a change can be walked back and forward any
        number of times without the two stacks drifting apart.
        """
        inverse = self._snapshot(entry.label, entry.container, entry.before)
        for index, stored in entry.before.items():
            entry.container.set_key(
                index, stored.clone() if stored is not None else None
            )
            self.key_config_changed(index, drop_folder_state=True)
        self.config.save()
        self.refresh()
        return inverse

    def can_undo(self) -> bool:
        """Whether the last change can still be taken back from this view.

        Both stacks are dropped on every page, profile and folder change, so an
        entry can only ever belong to the grid on screen. Restoring a key into
        a grid the user is no longer looking at would be invisible and, for a
        folder, would address a different key entirely.
        """
        return bool(self._undo) and self._undo[-1].container is self.container

    def undo(self) -> str:
        """Take back the last key change. Returns its label, or "" if none."""
        if not self.can_undo():
            return ""
        entry = self._undo.pop()
        self._redo.append(self._apply_entry(entry))
        del self._redo[:-UNDO_DEPTH]
        return entry.label

    def can_redo(self) -> bool:
        """Whether an undone change can be put back from this view."""
        return bool(self._redo) and self._redo[-1].container is self.container

    def redo(self) -> str:
        """Reapply the last undone change. Returns its label, or "" if none."""
        if not self.can_redo():
            return ""
        entry = self._redo.pop()
        self._undo.append(self._apply_entry(entry))
        del self._undo[:-UNDO_DEPTH]
        return entry.label

    def forget_undo(self) -> None:
        """Drop both histories, for every change of what the grid is showing."""
        self._undo.clear()
        self._redo.clear()

    def swap_keys(self, a: int, b: int) -> None:
        """Swap two keys (for drag & drop). Their ON/OFF state too."""
        if a == b or self.is_reserved_key(a) or self.is_reserved_key(b):
            return
        self._record_undo(f"moving Key {a + 1}", a, b)
        grid = self.container
        grid.keys[str(a)], grid.keys[str(b)] = grid.key(b), grid.key(a)
        # drop the None entries that set_key normally removes
        for i in (a, b):
            if grid.keys.get(str(i)) is None:
                grid.keys.pop(str(i), None)
        self._swap_key_state(self._tkey(a), self._tkey(b))
        # A folder that moved would leave its contents' state under the old
        # index. Those keys are transient, so drop them and re-render, exactly
        # as deleting a page does.
        self._discard_folder_state(a)
        self._discard_folder_state(b)
        self.config.save()
        self.refresh()

    def _swap_key_state(self, a: RuntimeKey, b: RuntimeKey) -> None:
        """Move two slots' transient state with the keys that just changed place.

        Shared by the swap inside one grid and the move across a folder
        boundary, so the two can never drift apart on what travels with a key.
        """
        # the toggle state travels with the key
        sa, sb = self._toggle.pop(a, None), self._toggle.pop(b, None)
        if sb is not None:
            self._toggle[a] = sb
        if sa is not None:
            self._toggle[b] = sa
        # A count travels with its key for the same reason a toggle does: it
        # belongs to that key, not to the position it happened to sit in.
        ca, cb = self._counters.pop(a, None), self._counters.pop(b, None)
        if cb is not None:
            self._counters[a] = cb
        if ca is not None:
            self._counters[b] = ca
        self._clocks.swap(a, b)
        for key in (a, b):
            self._cancel_timer_sound(key)
            # A mark refers to the action that failed, which is no longer here.
            self._forget_failure(key)
            # A gesture in flight belonged to the key that just moved away.
            self._cancel_gesture(key)

    def move_key_to(
        self,
        source_path,
        source_index: int,
        dest_index: int,
    ) -> bool:
        """Move a key from another grid of this page into the one on screen.

        A drag crosses a folder boundary when a folder springs open under a
        paused pointer, so by the time the key is dropped it is no longer in
        the grid it lands in. Inside one grid that is `swap_keys`; across two
        it cannot be, because an index, a `RuntimeKey` and an undo entry are
        each relative to one container.
        """
        source_path = tuple(source_path)
        dest_path = self._folder_path
        if source_path == dest_path:
            self.swap_keys(source_index, dest_index)
            return True
        if self.is_reserved_key(dest_index) or (
            source_path and source_index == FOLDER_BACK_INDEX
        ):
            return False
        source_grid = self._grid_at(source_path)
        if source_grid is None:
            return False
        moving = source_grid.key(source_index)
        if moving is None:
            return False
        # A folder cannot end up inside itself: the destination grid only
        # exists because the key being carried holds it.
        inside = source_path + (source_index,)
        if dest_path[: len(inside)] == inside:
            self.bus.emit("status", text="A folder cannot be moved inside itself")
            return False
        dest_grid = self.container
        displaced = dest_grid.key(dest_index)
        # Each key carries its own folders into the other one's depth.
        if len(dest_path) + folder_depth(moving) > MAX_FOLDER_DEPTH:
            self.bus.emit(
                "status", text="That folder has too many levels to fit here"
            )
            return False
        if displaced is not None and (
            len(source_path) + folder_depth(displaced) > MAX_FOLDER_DEPTH
        ):
            self.bus.emit(
                "status",
                text="The key already there has too many levels to fit back",
            )
            return False
        dest_grid.set_key(dest_index, moving)
        source_grid.set_key(source_index, displaced)
        self._swap_key_state(
            self._path_tkey(source_path, source_index),
            self._path_tkey(dest_path, dest_index),
        )
        # A folder that moved leaves its contents' state under the path it came
        # from, exactly as a swap within one grid does.
        self._discard_folder_state(source_index, source_path)
        self._discard_folder_state(dest_index, dest_path)
        # Every entry names indices of one container; this change spans two.
        self.forget_undo()
        self.config.save()
        self.refresh()
        return True

    def paste_key(self, index: int, kc: KeyConfig) -> None:
        """Place an independent copy of kc at position index."""
        if self.is_reserved_key(index):
            return
        self._record_undo(f"replacing Key {index + 1}", index)
        self.container.set_key(index, kc.clone())
        self.key_config_changed(index, drop_folder_state=True)
        self.config.save()
        self.refresh()

    def clear_key(self, index: int) -> None:
        if self.is_reserved_key(index):
            return
        self._record_undo(f"clearing Key {index + 1}", index)
        self.container.set_key(index, None)
        self.key_config_changed(index, drop_folder_state=True)
        self.config.save()
        self.refresh()

    def key_config_changed(
        self, index: int, drop_folder_state: bool = False
    ) -> None:
        """Reset transient state after replacing a key's saved configuration.

        `drop_folder_state` also discards the state of everything inside the
        key, for the callers that replace a whole folder rather than edit it.
        """
        key = self._tkey(index)
        self._toggle.pop(key, None)
        self._counters.pop(key, None)
        self._clocks.reset(key, refresh=False)
        self._cancel_timer_sound(key)
        self._cancel_gesture(key)
        # The mark belonged to the action that used to be here.
        self._forget_failure(key)
        if drop_folder_state:
            self._discard_folder_state(index)

    def _discard_folder_state(
        self, index: int, path: tuple[int, ...] | None = None
    ) -> None:
        """Forget the transient state of every key inside a folder slot.

        `path` names the grid holding that slot; it defaults to the one on
        screen, and is given explicitly when a key is moved out of another.
        """
        profile, page, current = self._view()
        prefix = (current if path is None else tuple(path)) + (index,)

        def inside(key: RuntimeKey) -> bool:
            key_profile, key_page, key_path, _key_index = key
            return (
                (key_profile, key_page) == (profile, page)
                and key_path[: len(prefix)] == prefix
            )

        for key in [key for key in self._toggle if inside(key)]:
            self._toggle.pop(key, None)
        for key in [key for key in self._counters if inside(key)]:
            self._counters.pop(key, None)
        with self._running_lock:
            for key in [key for key in self._failed if inside(key)]:
                self._failed.pop(key, None)
        self._clocks.discard(inside)
        with self._notification_lock:
            controls = [
                self._notification_controls.pop(key)
                for key in list(self._notification_controls)
                if inside(key)
            ]
        for control in controls:
            control.set()
        with self._execution_lock:
            for key in [key for key in self._execution_controls if inside(key)]:
                self._execution_controls.pop(key).cancel.set()
        with self._gesture_lock:
            for key in [key for key in self._gesture_down if inside(key)]:
                self._gesture_down.pop(key, None)
            timers = [
                self._gesture_timers.pop(key)
                for key in list(self._gesture_timers)
                if inside(key)
            ]
        for timer in timers:
            timer.cancel()

    def _cancel_gesture(self, key: RuntimeKey) -> None:
        """Drop any half-finished press gesture for one key."""
        with self._gesture_lock:
            self._gesture_down.pop(key, None)
            timer = self._gesture_timers.pop(key, None)
        if timer is not None:
            timer.cancel()

    # ---------- presses ----------

    def _on_deck_key(self, topic: str, data: dict) -> None:
        index = data.get("index")
        if index is None:
            return
        if self.games.handle_key(index, bool(data.get("pressed"))):
            return
        if data.get("pressed"):
            self.key_down(index)
        else:
            self.key_up(index)

    # ---------- press gestures (single / double / long) ----------

    def _gesture_mode(self, index: int) -> str:
        """How a key must be timed: "press", "long", or "" for none."""
        if self.is_reserved_key(index):
            # Leaving a folder must feel instant, so Back runs on the press.
            return ""
        kc = self.container.key(index)
        if kc is None or kc.is_empty():
            return ""
        if kc.kind == KIND_PRESS:
            return "press"
        if kc.kind == KIND_SINGLE:
            action = action_registry.get(kc.action)
            if action is not None and action.supports_long_press:
                return "long"
        return ""

    def key_down(self, index: int) -> None:
        """Physical press. Only gesture keys need to wait for the release."""
        if not self._gesture_mode(index):
            self.press(index)
            return
        if self._stopping.is_set() or self.deck.record_activity():
            return
        with self._gesture_lock:
            self._gesture_down[self._tkey(index)] = time.monotonic()

    def key_up(self, index: int) -> None:
        """Physical release. Resolves which gesture the key received."""
        mode = self._gesture_mode(index)
        if not mode or self._stopping.is_set():
            return
        if mode == "long":
            self._resolve_long_press_action(index)
            return
        key = self._tkey(index)
        with self._gesture_lock:
            started = self._gesture_down.pop(key, None)
            if started is None:
                # A release whose press was consumed elsewhere (screen saver
                # wake, or a key reconfigured while held).
                return
            held = time.monotonic() - started
            pending = self._gesture_timers.pop(key, None)
        if pending is not None:
            pending.cancel()
        if held >= LONG_PRESS_SECONDS:
            self._run_gesture(index, "long")
            return
        if pending is not None:
            self._run_gesture(index, "double")
            return
        timer = threading.Timer(
            DOUBLE_PRESS_SECONDS, self._resolve_single_press, args=(index, key)
        )
        timer.daemon = True
        with self._gesture_lock:
            self._gesture_timers[key] = timer
        timer.start()

    def _resolve_long_press_action(self, index: int) -> None:
        """Single-action key whose action handles being held down."""
        key = self._tkey(index)
        with self._gesture_lock:
            started = self._gesture_down.pop(key, None)
        if started is None:
            return
        kc = self.container.key(index)
        action = action_registry.get(kc.action) if kc is not None else None
        if action is None:
            return
        if time.monotonic() - started >= LONG_PRESS_SECONDS:
            # A long press that the action declines falls through to a normal
            # press, so "Nothing" behaves exactly like a short press.
            self._action_executor.submit(
                self._run_long_press, action, dict(kc.params), index, key
            )
            return
        self.press(index)

    def _run_long_press(self, action, params: dict, index: int, key) -> None:
        try:
            handled = action.long_press(self.ctx.for_key(key), params)
        except Exception as error:
            log.exception("Long press of %s failed", action.id)
            self.bus.emit("status", text=f"Error: {error}")
            self._note_failure(key)
            return
        if not handled and not self._stopping.is_set():
            self.press(index)
        else:
            # Closing the application changes its running feedback.
            self.refresh()

    def _resolve_single_press(self, index: int, key: RuntimeKey) -> None:
        with self._gesture_lock:
            if self._gesture_timers.pop(key, None) is None:
                return
        if not self._stopping.is_set():
            self._run_gesture(index, "single")

    def _run_gesture(self, index: int, gesture: str) -> None:
        kc = self.container.key(index)
        if kc is None or kc.kind != KIND_PRESS:
            return
        steps = list(getattr(kc, f"steps_{gesture}", []))
        if not steps:
            return
        self._submit_steps(
            steps,
            index,
            show_running=True,
            stop_on_error=_stops_on_error(kc),
        )

    def _clear_gestures(self) -> None:
        with self._gesture_lock:
            timers = list(self._gesture_timers.values())
            self._gesture_timers.clear()
            self._gesture_down.clear()
        for timer in timers:
            timer.cancel()

    def _on_screensaver(self, topic: str, data: dict) -> None:
        if not data.get("active"):
            self.refresh()

    def toggle_state(self, index: int) -> bool:
        """ON/OFF state of a toggle key on the current page/profile."""
        return self._toggle.get(self._tkey(index), False)

    def press(self, index: int, gesture: str = GESTURE_SINGLE) -> None:
        """Run the key (physical or virtual press) according to its type.

        `gesture` only means anything for a gesture key, and only a caller that
        already knows which one it wants passes it — the editor's Test buttons.
        Naming a gesture is a different thing from making the on-screen deck
        wait out the double-press window, which it must never do.
        """
        if _game_active(self):
            self.games.press_virtual(index)
            return
        if self._stopping.is_set() or self.deck.record_activity():
            return
        if self.dismiss_board():
            # A report was up: the press that dismisses it must not also run
            # whatever key happens to be underneath.
            return
        if self.is_reserved_key(index):
            self.close_folder()
            return
        kc = self.container.key(index)
        if kc is None or kc.is_empty():
            return
        if kc.kind == KIND_FOLDER:
            # Navigation, not an action: it never reaches an action worker.
            self.open_folder(index)
            return
        if kc.kind == KIND_SINGLE:
            steps = [ActionStep(action=kc.action, params=kc.params)]
        elif kc.kind == KIND_MULTI:
            steps = list(kc.steps)
        elif kc.kind == KIND_RANDOM:
            steps = [random.choice(kc.steps)] if kc.steps else []
        elif kc.kind == KIND_PRESS:
            # A virtual press has no release to time, so it defaults to the
            # single-press list; the physical deck resolves the real gesture in
            # key_up(), and the editor can ask for one by name.
            steps = list(gesture_steps(kc, gesture))
        elif kc.kind == KIND_TOGGLE:
            key = self._tkey(index)
            new_state = not self._toggle.get(key, False)
            self._toggle[key] = new_state
            steps = list(kc.steps_on if new_state else kc.steps_off)
        else:
            return
        if not steps:
            return
        single_action = (
            action_registry.get(kc.action)
            if kc.kind == KIND_SINGLE
            else None
        )
        if single_action is not None and single_action.immediate:
            self._run_immediate(
                single_action,
                dict(kc.params),
                index,
                self._tkey(index),
            )
            return
        self._submit_steps(
            steps,
            index,
            show_running=(
                kc.kind in (KIND_MULTI, KIND_TOGGLE, KIND_RANDOM, KIND_PRESS)
                or bool(single_action and single_action.running_feedback)
            ),
            stop_on_error=_stops_on_error(kc),
        )

    # ---------- dials (Stream Deck +) ----------

    def dial(self, index: int):
        """The dial's configuration on the active page, if it has one."""
        return self.page.dial(index)

    def _on_deck_dial(self, _topic: str, data: dict) -> None:
        if _game_active(self):
            return
        self.turn_dial(
            int(data.get("index", 0)),
            str(data.get("direction", "")),
            int(data.get("ticks", 1)),
        )

    def _on_deck_touch(self, _topic: str, data: dict) -> None:
        """A tap on the strip runs the dial's push action.

        The strip has no configuration of its own: it labels the encoders, so
        tapping a panel is a second way of pressing the dial under it.
        """
        if not _game_active(self):
            self.turn_dial(int(data.get("index", 0)), "press", 1)

    def turn_dial(self, index: int, direction: str, ticks: int = 1) -> None:
        """Run what an encoder gesture is configured to do.

        A turn arrives with a tick count, and the steps run once per tick so a
        fast spin moves a volume by the amount the hand actually turned. The
        count is bounded: the actions are queued on a worker, and a spin that
        outran the queue could otherwise leave it running for a long time.
        """
        if _game_active(self):
            return
        if self._stopping.is_set() or self.deck.record_activity():
            return
        field = DIAL_STEPS.get(direction)
        if field is None:
            return
        dial = self.page.dial(index)
        if dial is None or dial.is_empty():
            return
        steps = list(getattr(dial, field, []))
        if not steps:
            return
        execution_key = self._dial_tkey(index)
        for _ in range(max(1, min(int(ticks), MAX_DIAL_TICKS))):
            self._submit_steps(
                steps,
                index,
                show_running=False,
                execution_key=execution_key,
                stop_on_error=_stops_on_error(dial),
            )

    def refresh_touchscreen(self) -> None:
        """Redraw the LCD strip on its own, after editing a dial."""
        if not getattr(self.deck, "dial_count", 0):
            return
        try:
            self._render_executor.submit(
                self._render_touchscreen, self._page_dials()
            )
        except RuntimeError:
            if not self._stopping.is_set():
                raise

    def dial_config_changed(self, index: int) -> None:
        """Drop a dial's transient state after it is edited, and repaint."""
        key = self._dial_tkey(index)
        self._clocks.reset(key, refresh=False)
        self._cancel_timer_sound(key)
        self.refresh_touchscreen()

    def _render_touchscreen(self, dials: dict) -> None:
        from ..device.touchscreen import touchscreen_image

        try:
            image = touchscreen_image(
                dials,
                size=self.deck.touch_size,
                count=self.deck.dial_count,
            )
            self.deck.set_touchscreen_image(image)
        except Exception:
            log.debug("Could not refresh the touchscreen", exc_info=True)

    def _submit_steps(
        self,
        steps,
        index: int,
        show_running: bool,
        execution_key: RuntimeKey | None = None,
        stop_on_error: bool = False,
    ) -> None:
        """Queue a key's steps on an action worker, with running feedback.

        `execution_key` is passed only by a caller whose identity is not a key
        of the visible grid — a dial, which is numbered separately.
        """
        if execution_key is None:
            execution_key = self._tkey(index)
        runtime_key = execution_key if show_running else None
        control = self._prepare_execution(execution_key, steps)
        if runtime_key is not None:
            self._begin_running(runtime_key)
        try:
            self._action_executor.submit(
                self._run_steps,
                steps,
                index,
                runtime_key,
                control,
                execution_key,
                stop_on_error,
            )
        except RuntimeError:
            if control is not None:
                self._finish_execution(execution_key, control)
            if runtime_key is not None:
                self._end_running(runtime_key)
            if not self._stopping.is_set():
                raise

    def _run_immediate(
        self,
        action,
        params: dict,
        index: int,
        key: RuntimeKey,
    ) -> None:
        run_ctx = self.ctx.for_key(key)
        try:
            action.execute(run_ctx, params)
            log.debug("Ran %s immediately (key %d)", action.id, index)
        except Exception as error:
            log.exception("Error running %s", action.id)
            self.bus.emit(
                "status",
                text=f"Error in «{action.name}»: {error}",
            )
            self._note_failure(key)

    def _run_steps(
        self,
        steps: list[ActionStep],
        index: int,
        runtime_key: RuntimeKey | None = None,
        control: _ExecutionControl | None = None,
        execution_key: RuntimeKey | None = None,
        stop_on_error: bool = False,
    ) -> None:
        # Derived rather than built, so this path cannot fall behind what a
        # context carries. Enumerating the fields here is what once left the
        # Twitch client out of every execution: the keys rendered fine and
        # failed on the press.
        run_ctx = self.ctx.for_run(
            execution_key, control.cancel if control is not None else None
        )
        try:
            if control is not None and control.predecessor is not None:
                while not control.predecessor.wait(0.05):
                    # The predecessor also observes shutdown and will finish.
                    if self._stopping.is_set():
                        return
            for step in steps:
                if run_ctx.stop_requested():
                    return
                action = action_registry.get(step.action)
                if action is None:
                    if step.action:
                        log.warning("Unknown action on key %d: %s", index, step.action)
                    continue
                try:
                    action.execute(run_ctx, dict(step.params))
                    log.debug("Ran %s (key %d)", action.id, index)
                except Exception as e:
                    log.exception("Error running %s", action.id)
                    text = f"Error in «{action.name}»: {e}"
                    if stop_on_error:
                        text += " - the rest of this key was not run"
                    self.bus.emit("status", text=text)
                    self._note_failure(
                        execution_key or runtime_key or self._tkey(index)
                    )
                    if stop_on_error:
                        return
        finally:
            if control is not None:
                self._finish_execution(
                    execution_key or runtime_key or self._tkey(index),
                    control,
                )
            if runtime_key is not None:
                self._end_running(runtime_key)

    # ---------- replaceable executions ----------

    def _prepare_execution(
        self,
        key: RuntimeKey,
        steps: list[ActionStep],
    ) -> _ExecutionControl | None:
        restartable = any(
            bool(
                (action := action_registry.get(step.action))
                and action.restart_on_repress
            )
            for step in steps
        )
        with self._execution_lock:
            previous = self._execution_controls.get(key)
            if previous is not None:
                previous.cancel.set()
            if not restartable and previous is None:
                return None
            control = _ExecutionControl(
                previous.finished if previous is not None else None
            )
            if restartable:
                self._execution_controls[key] = control
            else:
                self._execution_controls.pop(key, None)
            return control

    def _finish_execution(
        self,
        key: RuntimeKey,
        control: _ExecutionControl,
    ) -> None:
        control.finished.set()
        with self._execution_lock:
            if self._execution_controls.get(key) is control:
                self._execution_controls.pop(key, None)

    # ---------- running feedback ----------

    def _begin_running(self, key: RuntimeKey) -> None:
        with self._running_lock:
            self._running[key] = self._running.get(key, 0) + 1
            self._busy_wakeup.set()
        self._refresh_runtime_keys((key,))

    def _end_running(self, key: RuntimeKey) -> None:
        with self._running_lock:
            count = self._running.get(key, 0)
            if count <= 1:
                self._running.pop(key, None)
                became_idle = True
            else:
                self._running[key] = count - 1
                became_idle = False
        if became_idle:
            self._refresh_runtime_keys((key,))

    def _busy_state(self, index: int) -> tuple[bool, bool]:
        key = self._tkey(index)
        with self._running_lock:
            return key in self._running, self._busy_phase

    # ---------- failure feedback ----------

    def _note_failure(self, key: RuntimeKey | None) -> None:
        """Mark a key as having just failed, and repaint it.

        The activity thread is woken because it is what expires the mark; it is
        the same thread that pulses running keys, so a failure needs no timer
        and no second thread of its own.
        """
        if key is None:
            return
        with self._running_lock:
            self._failed[key] = time.monotonic() + ERROR_FEEDBACK_SECONDS
            self._busy_wakeup.set()
        self._refresh_runtime_keys((key,))

    def _failed_state(self, index: int) -> bool:
        with self._running_lock:
            return self._tkey(index) in self._failed

    def _forget_failure(self, key: RuntimeKey) -> None:
        """Drop the mark, for a key whose meaning has just changed."""
        with self._running_lock:
            self._failed.pop(key, None)

    def _expire_failures(self) -> tuple[RuntimeKey, ...]:
        """Marks whose time is up, removed and returned so they repaint once."""
        now = time.monotonic()
        with self._running_lock:
            done = tuple(
                key for key, until in self._failed.items() if until <= now
            )
            for key in done:
                self._failed.pop(key, None)
        return done

    def _busy_loop(self) -> None:
        while not self._stopping.is_set():
            self._busy_wakeup.wait()
            if self._stopping.is_set():
                return
            if self._stopping.wait(BUSY_PULSE_SECONDS):
                return
            expired = self._expire_failures()
            with self._running_lock:
                if not self._running and not self._failed:
                    # Nothing left to animate or to expire; sleep until the next
                    # run or failure. The keys that just expired still repaint.
                    self._busy_phase = False
                    self._busy_wakeup.clear()
                    keys = expired
                else:
                    self._busy_phase = not self._busy_phase
                    keys = tuple({*self._running, *expired})
            if keys:
                self._refresh_runtime_keys(keys)

    @property
    def key_image_size(self) -> tuple[int, int]:
        """Pixel size of one key, for an action that renders its own artwork."""
        return getattr(self.deck, "image_size", (72, 72))

    def _live_loop(self) -> None:
        """Repaint keys whose value changes with nothing happening.

        Statistics and live scene previews are the only two: no bus event
        announces a rising frame counter or a moving camera, so they are the
        only keys repainted by the clock rather than by an event.

        It asks OBS for nothing itself. It only asks the render worker to
        recompose those keys; their `feedback()` reads the client's shared
        caches, so six keys previewing one scene still cost one capture.
        """
        due: dict[RuntimeKey, float] = {}
        while not self._stopping.wait(LIVE_TICK_SECONDS):
            # No OBS guard here: a machine-wide measurement comes from the
            # kernel and must keep ticking while OBS is closed. Whether a key
            # is worth repainting at all is `_live_interval`'s decision.
            if self.deck.screensaver_active:
                continue
            now = time.monotonic()
            wanted = self._live_keys()
            # Rebuilt from what is on screen, so leaving a page forgets its
            # keys instead of growing this dict for the life of the process.
            due = {key: due.get(key, 0.0) for key in wanted}
            ready = tuple(
                key for key, interval in wanted.items() if now >= due[key]
            )
            for key in ready:
                due[key] = now + wanted[key]
            if ready:
                self._refresh_runtime_keys(ready)

    def _live_keys(self) -> dict[RuntimeKey, float]:
        """Visible keys that repaint on a clock, and how often each wants it."""
        try:
            grid = self.container
        except Exception:
            return {}
        found: dict[RuntimeKey, float] = {}
        for raw, kc in list(grid.keys.items()):
            if kc is None or kc.kind != KIND_SINGLE:
                continue
            interval = self._live_interval(kc)
            if interval <= 0:
                continue
            try:
                found[self._tkey(int(raw))] = interval
            except (TypeError, ValueError):
                continue
        return found

    def _live_interval(self, kc: KeyConfig) -> float:
        """How often this key needs recomposing, or 0 when it does not.

        Only a single-action key can want it: the value comes from
        `feedback()`, which is resolved for a key's own action and never for a
        step inside a list. A key that can show nothing without OBS asks for
        nothing while OBS is closed.
        """
        if kc.action == STATS_ACTION_ID:
            from ..obs.actions import stat_needs_obs

            if self.obs.connected or not stat_needs_obs(kc.params.get("metric")):
                return STATS_REFRESH_SECONDS
            return 0.0
        if kc.action == ALERT_ACTION_ID:
            return ALERT_REFRESH_SECONDS if self._twitch_linked() else 0.0
        if kc.action == MEDIA_ACTION_ID:
            from ..basic_actions import shows_now_playing
            from .nowplaying import available as player_tool

            if not shows_now_playing(kc.params) or not player_tool():
                return 0.0
            return MEDIA_REFRESH_SECONDS
        if kc.action == HA_SWITCH_ACTION_ID:
            if self.home_assistant is None or not self.home_assistant.configured():
                return 0.0
            return HA_SWITCH_REFRESH_SECONDS
        if kc.action == HA_STATE_ACTION_ID:
            from ..ha_actions import refresh_seconds as ha_refresh

            if self.home_assistant is None or not self.home_assistant.configured():
                return 0.0
            return ha_refresh(kc.params.get("refresh"))
        if kc.action == SYSTEM_STATS_ACTION_ID:
            # Every reading is the kernel's, so it never waits on anything and
            # never depends on a connection.
            return STATS_REFRESH_SECONDS
        if kc.action == MIXER_ACTION_ID:
            from .mixer import MUTE_MODES, available

            if not available() or str(kc.params.get("mode") or "") not in MUTE_MODES:
                return 0.0
            return MIXER_REFRESH_SECONDS
        if kc.action == WEB_ACTION_ID:
            from ..web_actions import refresh_seconds, shows_value

            # Repainting is what makes the key ask again; the cache's own
            # minimum age decides when that becomes a request, so a key that
            # shows nothing costs nothing at all.
            if not shows_value(kc.params):
                return 0.0
            return refresh_seconds(kc.params.get("refresh"))
        if kc.action == TWITCH_STATS_ACTION_ID:
            # Repainting is what makes the key ask for a fresh snapshot; the
            # client's own cache decides when that becomes a request, so this
            # interval costs nothing but a recompose.
            return TWITCH_REFRESH_SECONDS if self._twitch_linked() else 0.0
        if kc.action in PREVIEW_ACTION_IDS and self.obs.connected:
            from ..obs.actions import preview_interval

            return preview_interval(kc.params.get("preview"))
        return 0.0

    def _refresh_runtime_keys(
        self,
        keys: tuple[RuntimeKey, ...],
    ) -> None:
        if self._stopping.is_set():
            return
        view = self._view()
        indices = sorted({
            index
            for profile, page, path, index in keys
            if (profile, page, path) == view
        })
        if not indices:
            return
        try:
            self._render_executor.submit(self._render_keys, indices, view)
        except RuntimeError:
            if not self._stopping.is_set():
                raise

    # ---------- counter state ----------

    def counter_value(self, key: RuntimeKey | None, start: int = 0) -> int:
        """What this counter is showing, or its starting value.

        `start` is the key's own configuration rather than something stored,
        so changing it in the editor moves an untouched counter with it -- and
        editing the key resets the count anyway, as it does every other piece
        of transient key state.
        """
        if key is None:
            return int(start)
        return self._counters.get(key, int(start))

    def bump_counter(self, key: RuntimeKey | None, step: int, start: int) -> int:
        if key is None:
            return int(start)
        value = self.counter_value(key, start) + int(step)
        self._counters[key] = value
        self._refresh_runtime_keys((key,))
        return value

    def reset_counter(self, key: RuntimeKey | None, start: int = 0) -> int:
        if key is None:
            return int(start)
        self._counters.pop(key, None)
        self._refresh_runtime_keys((key,))
        return int(start)

    # ---------- timer and stopwatch state ----------

    def toggle_countdown(
        self,
        key: RuntimeKey,
        duration: float,
        sound_file: str,
        volume,
    ) -> bool:
        started = self._clocks.toggle_timer(
            key,
            duration,
            sound_file,
            volume,
        )
        self._cancel_timer_sound(key)
        return started

    def toggle_stopwatch(self, key: RuntimeKey) -> bool:
        started = self._clocks.toggle_stopwatch(key)
        self._cancel_timer_sound(key)
        return started

    def countdown_snapshot(
        self,
        key: RuntimeKey | None,
        idle_seconds: float,
    ) -> ClockSnapshot:
        return self._clocks.timer_snapshot(key, idle_seconds)

    def stopwatch_snapshot(
        self,
        key: RuntimeKey | None,
    ) -> ClockSnapshot:
        return self._clocks.stopwatch_snapshot(key)

    def _on_alert(self, alert) -> None:
        """Something arrived from Twitch: repaint, and maybe make a noise.

        The sound is decided per key rather than per alert, because two keys
        can be watching different things with different sounds, and because
        the rule is the mailbox one: a noise when a key goes from quiet to
        somebody-waiting, never one per message.
        """
        if self._stopping.is_set():
            return
        from ..twitch import actions as twitch_actions
        from ..twitch.attention import should_sound

        self.bus.emit("status", text=twitch_events.describe(alert))
        # Fetched now, off the render path, so the key finds it in the cache.
        if self.twitch is not None and alert.user_id:
            try:
                self.twitch.prefetch_avatar(alert.user_id)
            except Exception:
                log.debug("Could not prefetch a Twitch avatar", exc_info=True)
        for runtime_key, kc in self._alert_keys().items():
            if not twitch_actions.alert_matches(kc.params, alert):
                continue
            pending = self.attention.pending(
                runtime_key,
                twitch_actions.alert_sources(kc.params),
                twitch_actions.alert_filter(kc.params),
            )
            # Whether this key was already showing somebody waiting, asked of
            # the alerts themselves rather than remembered as a flag. A flag
            # has to be cleared when the key goes quiet again, and nothing was
            # in a position to see that happen: alerts expire on their own
            # clock and the key is never told. So it made its noise once and
            # then stayed silent for good.
            earlier = [other for other in pending if other is not alert]
            reminded = self._alerting.get(runtime_key, 0.0)
            remind_after = float(parse_duration(kc.params.get("remind_after")))
            if should_sound(bool(earlier), pending, reminded, remind_after):
                self._alerting[runtime_key] = time.monotonic()
                self._play_alert_sound(kc.params)
                # On the same rule as the sound, deliberately. A flash per
                # message would be far worse than a noise per message: a busy
                # chat would leave the deck strobing continuously.
                if twitch_actions.alert_flashes(kc.params):
                    self._start_flash(
                        twitch_actions.alert_flash_color(kc.params, alert),
                        twitch_actions.alert_flash_word(alert),
                    )
        self._refresh_runtime_keys(tuple(self._alert_keys()))

    def _alert_keys(self) -> dict[RuntimeKey, KeyConfig]:
        """The visible alert keys. Only these can make a noise or repaint."""
        try:
            grid = self.container
        except Exception:
            return {}
        found: dict[RuntimeKey, KeyConfig] = {}
        for raw, kc in list(grid.keys.items()):
            if kc is None or kc.kind != KIND_SINGLE or kc.action != ALERT_ACTION_ID:
                continue
            try:
                found[self._tkey(int(raw))] = kc
            except (TypeError, ValueError):
                continue
        return found

    def _play_alert_sound(self, params: dict) -> None:
        sound = str(params.get("sound") or "")
        if not sound or self._stopping.is_set():
            return
        try:
            volume = max(0, min(100, int(params.get("volume", 70))))
        except (TypeError, ValueError):
            volume = 70
        try:
            self._notification_executor.submit(self._alert_sound, sound, volume)
        except RuntimeError:
            # Submitted during shutdown; nothing left to announce.
            log.debug("Alert sound submitted after shutdown")

    def _start_flash(self, color: str, word: str = "") -> None:
        """Light the whole deck, for an alert nobody was going to hear."""
        if (
            self._stopping.is_set()
            or self._flashing.is_set()
            or _game_active(self)
        ):
            # Already lit: a second flash on top of the first would only cut
            # it short, and the deck is plainly already asking to be looked at.
            return
        # Set before submitting, so a render queued in between stands aside
        # rather than painting real keys over the first pulse.
        self._flashing.set()
        try:
            self._notification_executor.submit(self._flash_deck, color, word)
        except RuntimeError:
            self._flashing.clear()
            log.debug("Deck flash submitted after shutdown")

    def _flash_deck(self, color: str, word: str = "") -> None:
        """Pulse the whole deck between one colour and black, then put it back.

        A lit frame is the same solid colour on every key but the middle one,
        which carries the word, so the whole flash is **three** composed images
        rather than one per key per pulse -- which on an XL would be a hundred
        and ninety-two, on the one worker that also draws every real key.
        """
        try:
            self._wake_for_flash()
            size = self.deck.image_size
            count = max(0, int(self.deck.key_count))
            lit = self._flash_frame(size, color, word, count)
            dark = self._flash_frame(size, FLASH_OFF_COLOR, "", count)
            for pulse in range(FLASH_PULSES * 2):
                if _game_active(self):
                    return
                on = pulse % 2 == 0
                self._paint_frame(lit if on else dark, count)
                if self._stopping.wait(
                    FLASH_ON_SECONDS if on else FLASH_OFF_SECONDS
                ):
                    return
        except Exception:
            log.warning("Could not flash the deck", exc_info=True)
        finally:
            # Before the repaint, or refresh() would stand aside for a flash
            # that has finished and leave the deck black.
            self._flashing.clear()
            self.refresh()

    def _flash_frame(self, size, color: str, word: str, count: int) -> list:
        """One (image, png) per key, built once and reused by every pulse."""
        plain = self._flash_key(size, color, "")
        frame = [plain] * count
        middle = self._flash_center(count)
        if word and 0 <= middle < count:
            frame[middle] = self._flash_key(size, color, word)
        return frame

    def _flash_key(self, size, color: str, word: str):
        image = renderer.compose(
            size=size,
            bg=color,
            center_text=word,
            # The colour is the user's to choose, so nothing here may assume
            # the word on it still reads in black.
            text_color=renderer.contrasting_ink(color),
        )
        return image, renderer.to_png_bytes(image)

    def _flash_center(self, count: int) -> int:
        """The key the word goes on: the middle of whatever grid is connected."""
        columns = max(1, int(getattr(self.deck, "columns", 0) or 1))
        rows = max(1, -(-count // columns))
        return min(max(0, count - 1), (rows // 2) * columns + columns // 2)

    def _paint_frame(self, frame: list, count: int) -> None:
        for index in range(count):
            if self._stopping.is_set() or _game_active(self):
                return
            image, png = frame[index]
            self.deck.set_key_image(index, image)
            self.bus.emit("ui.key_image", index=index, png=png)

    def _wake_for_flash(self) -> None:
        """Bring the deck back from the screen saver, which owns it while on.

        Without this the flash would be dropped by the very render guards that
        keep the saver coherent — and asleep is exactly the state somebody deep
        in a game is in, so the feature would fail precisely when it is needed.
        Waking counts as activity, so the idle timer starts again from here.
        """
        if not self.deck.screensaver_active:
            return
        self.deck.record_activity()
        # It lets go on its own thread, between frames, so painting straight
        # away would put the first pulses into a deck that is still refusing
        # them.
        deadline = time.monotonic() + FLASH_WAKE_SECONDS
        while self.deck.screensaver_active and time.monotonic() < deadline:
            if self._stopping.wait(0.02):
                return

    def _alert_sound(self, sound: str, volume: int) -> None:
        """Play it, and say so when it cannot be played.

        `play_audio` takes the volume as a **percentage** and the stop signal
        as something it can **call**; handing it a fraction and an Event made
        every alert sound play at a hundredth of its volume and then raise on
        the first turn of its loop. Nothing noticed, because the executor keeps
        a worker's exception in a Future nobody reads -- which is why the
        wrapper matters as much as the arguments do.
        """
        try:
            play_audio(sound, volume, stop_requested=self._stopping.is_set)
        except Exception as error:
            if self._stopping.is_set():
                return
            log.exception("Could not play the Twitch alert sound")
            self.bus.emit("status", text=f"Alert sound failed: {error}")

    def _on_timer_finished(self, completion: TimerCompletion) -> None:
        control = None
        with self._notification_lock:
            if (
                self._stopping.is_set()
                or not self._clocks.is_current_completion(completion)
            ):
                return
            if completion.sound_file:
                control = threading.Event()
                previous = self._notification_controls.get(completion.key)
                if previous is not None:
                    previous.set()
                self._notification_controls[completion.key] = control
        self.bus.emit("status", text="Timer finished")
        if control is None:
            return
        try:
            self._notification_executor.submit(
                self._play_timer_sound,
                completion,
                control,
            )
        except RuntimeError:
            control.set()
            with self._notification_lock:
                if self._notification_controls.get(completion.key) is control:
                    self._notification_controls.pop(completion.key, None)
            if not self._stopping.is_set():
                raise

    def _play_timer_sound(
        self,
        completion: TimerCompletion,
        control: threading.Event,
    ) -> None:
        try:
            play_audio(
                completion.sound_file,
                completion.volume,
                stop_requested=lambda: (
                    control.is_set() or self._stopping.is_set()
                ),
            )
        except Exception as error:
            if not control.is_set() and not self._stopping.is_set():
                log.exception("Could not play timer completion sound")
                self.bus.emit(
                    "status",
                    text=f"Timer sound failed: {error}",
                )
        finally:
            with self._notification_lock:
                if self._notification_controls.get(completion.key) is control:
                    self._notification_controls.pop(completion.key, None)

    def _cancel_timer_sound(self, key: RuntimeKey) -> None:
        with self._notification_lock:
            control = self._notification_controls.pop(key, None)
        if control is not None:
            control.set()

    def _clear_time_actions(self) -> None:
        self._clocks.clear()
        self._cancel_all_timer_sounds()
        # Stored indices shift with the profile/page, so a pending gesture would
        # resolve against a different key, and a failure mark would be shown on
        # whatever key now sits where the one that failed used to be.
        self._clear_gestures()
        with self._running_lock:
            self._failed.clear()
        # A report describes the grid it was run from.
        self._board = None

    def _cancel_all_timer_sounds(self) -> None:
        with self._notification_lock:
            controls = tuple(self._notification_controls.values())
            self._notification_controls.clear()
        for control in controls:
            control.set()

    # ---------- rendering ----------

    def refresh(self) -> None:
        """Re-render the active page (coalesces bursts of events)."""
        if (
            self._stopping.is_set()
            or self.deck.screensaver_active
            or self._flashing.is_set()
            or _game_active(self)
            or self._render_pending.is_set()
        ):
            return
        self._render_pending.set()
        try:
            self._render_executor.submit(self._render_page)
        except RuntimeError:
            self._render_pending.clear()
            if not self._stopping.is_set():
                raise

    def _standing_aside(self) -> bool:
        """Whether something else owns the deck right now.

        Three things do, and a normal render has to yield to all of them: the
        screen saver and a game draw coordinated frames, while an alert flash
        would otherwise have half its pulses painted over.
        """
        return (
            self.deck.screensaver_active
            or self._flashing.is_set()
            or _game_active(self)
        )

    # ---------- built-in games ----------

    def start_game(self, game_id: str = "mole_smash") -> bool:
        """Enter a built-in game after clearing transient overlays and gestures."""
        self.dismiss_board()
        self._clear_gestures()
        started = self.games.start(game_id)
        if started:
            self.bus.emit(
                "status",
                text=f"{self.games.game_name} is ready on the Stream Deck",
            )
        return started

    def stop_game(self) -> bool:
        """Ask the active game worker to restore the configured page."""
        return self.games.stop()

    def _restore_after_game(self) -> None:
        """Discard delayed overlays, then restore only the configured page."""
        # A pre-flight or OBS board may have completed on another worker after
        # start_game() dismissed the old one. It must not suddenly appear when
        # the game releases the pixels; the documented contract is to return
        # to the page that was underneath it.
        self._board = None
        self.refresh()

    def _render_page(self) -> None:
        self._render_pending.clear()
        self._render_keys(range(self.deck.key_count), self._view())
        # The strip labels the encoders, so it follows the page exactly as the
        # keys do. Already on the render worker, so it draws inline.
        if getattr(self.deck, "dial_count", 0):
            self._render_touchscreen(self._page_dials())

    def _page_dials(self) -> dict:
        return {
            int(raw): dial
            for raw, dial in self.page.dials.items()
            if str(raw).lstrip("-").isdigit()
        }

    def _render_keys(self, indices, view: tuple[int, int, tuple[int, ...]]) -> None:
        if view != self._view() or self._standing_aside():
            return
        grid = self.container
        size = self.deck.image_size
        for index in indices:
            if self._stopping.is_set() or self._standing_aside():
                return
            spec = self._key_spec(index, grid.key(index), size)
            image = renderer.compose(**spec)
            if view != self._view() or self._standing_aside():
                return
            self.deck.set_key_image(index, image)
            self.bus.emit("ui.key_image", index=index, png=renderer.to_png_bytes(image))

    # ---------- key specs (feedback + compose parameters) ----------

    # ---------- temporary board over the whole grid ----------

    def show_board(self, specs: dict[int, dict] | None) -> None:
        """Paint an arbitrary set of key images over the grid, or clear it.

        A *layer*, not a device mode. The screen saver owns the deck and needs
        its own thread, its own brightness and a wake press; this only replaces
        what each key draws, so it costs one lookup in `_key_spec` and cannot
        interfere with the saver or with hotplug. Whatever is showing it, the
        real configuration underneath is untouched.
        """
        self._board = dict(specs) if specs else None
        self.refresh()

    def board_active(self) -> bool:
        return self._board is not None

    def dismiss_board(self) -> bool:
        """Take a report off the deck now. Returns whether one was showing.

        The single way a report is put away, so whoever is holding it up notices
        at once: a press on the deck and closing the report window are the same
        act, and the second used to leave the deck showing a report the user had
        already read and dismissed for the rest of the hold.
        """
        if self._board is None:
            return False
        self.show_board(None)
        return True

    def _key_spec(self, index: int, kc: KeyConfig | None, size) -> dict:
        # Read once: it is replaced wholesale from an action worker while the
        # render worker is reading it.
        board = self._board
        if board is not None:
            spec = dict(board.get(index) or {})
            spec["size"] = size
            return spec
        if self.is_reserved_key(index):
            return self._back_spec(size)
        if kc is None or kc.is_empty():
            return {"size": size}
        if kc.kind == KIND_FOLDER:
            spec = self._folder_spec(kc, size)
        elif kc.kind == KIND_TOGGLE:
            spec = self._toggle_spec(index, kc, size)
        elif kc.kind in (KIND_MULTI, KIND_RANDOM, KIND_PRESS):
            spec = self._sequence_spec(index, kc, size)
        else:
            spec = self._single_spec(index, kc, size)
        return self._mark_key_state(index, kc, spec)

    def _sequence_spec(self, index: int, kc: KeyConfig, size) -> dict:
        busy, phase = self._busy_state(index)
        if kc.kind == KIND_RANDOM:
            steps, fallback, badge = kc.steps, "mdi:shuffle-variant", "?"
        elif kc.kind == KIND_PRESS:
            steps = [*kc.steps_single, *kc.steps_double, *kc.steps_long]
            fallback, badge = "mdi:gesture-tap", "⋮"
        else:
            steps, fallback, badge = kc.steps, "mdi:playlist-play", "⋯"
        icon = kc.icon or self._first_step_icon(steps) or fallback
        return {"size": size, "label": kc.label, "icon_path": icon,
                "bg": kc.bg_color, "busy": busy, "busy_phase": phase,
                "badge": "RUN" if busy else badge,
                "font_size": kc.font_size,
                "text_color": kc.text_color}

    def _mark_key_state(self, index: int, kc: KeyConfig, spec: dict) -> dict:
        """Add the two states that are about the key rather than its action.

        Both exist because the deck used to be silent about them. A failure
        only reached a status bar in a window that is normally hidden behind
        the status icon, and a key that could not work at all rendered exactly
        like one that was merely idle, so the only way to tell was to press it
        and watch nothing happen.
        """
        if self._failed_state(index):
            spec["failed"] = True
            if not spec.get("busy"):
                # A run that is still going keeps saying so; the mark outlives
                # it and takes the badge back when it ends.
                spec["badge"] = renderer.ERROR_BADGE
        if self._unavailable(kc):
            spec["unavailable"] = True
        return spec

    def _unavailable(self, kc: KeyConfig) -> bool:
        """Whether this key can do nothing whatsoever right now.

        Every action of the key has to be blocked, and by something that is
        actually missing. One that mixes an OBS action with a local one still
        does half its job, and fading it would overstate the problem; an action
        that is not registered at all is unknowable, and unknown is not the
        same as unavailable.

        A single action may be blocked by either connection, which is why this
        asks per action rather than per key: a key that sets the Twitch title
        fades with no account linked, while OBS being closed says nothing
        about it.
        """
        actions = self._key_actions(kc)
        if not actions:
            return False
        return all(
            self._action_blocked(action, params or {}) for action, params in actions
        )

    def _action_blocked(self, action, params: dict) -> bool:
        obs_blocked = action.requires_obs(params) and not self.obs.connected
        twitch_blocked = action.requires_twitch(params) and not self._twitch_allows(
            action
        )
        home_blocked = action.requires_home_assistant(params) and not (
            self.home_assistant is not None and self.home_assistant.configured()
        )
        return obs_blocked or twitch_blocked or home_blocked

    def _twitch_linked(self) -> bool:
        return bool(self.twitch is not None and self.twitch.linked)

    def _twitch_allows(self, action) -> bool:
        """Whether the linked account can actually perform this action.

        A connection is not enough. An account linked before an action existed
        holds a token that was never granted what it needs, and Twitch only
        says so on the press — which for these actions means finding out live.
        Fading the key says it beforehand, the same way an OBS key says OBS is
        closed.
        """
        if not self._twitch_linked():
            return False
        scope = getattr(action, "twitch_scope", "")
        if scope:
            try:
                missing = self.twitch.missing_scopes()
            except Exception:
                log.debug("Could not read the Twitch scopes", exc_info=True)
                missing = ()
            # An authorization whose scopes were never recorded answers empty,
            # so this stays unknown rather than blocked: unknown is not
            # unavailable.
            if scope in missing:
                return False
        if getattr(action, "twitch_needs_affiliate", False):
            try:
                allowed = self.twitch.can_run_ads()
            except Exception:
                log.debug("Could not read the Twitch account type", exc_info=True)
                allowed = None
            # None means it was never established, and a lookup that failed
            # must not disable a key that would have worked.
            if allowed is False:
                return False
        return True

    @staticmethod
    def _key_actions(kc: KeyConfig) -> list[tuple]:
        """Every registered action this key would run, with its parameters.

        Empty when the key runs nothing, or when any one of its actions is
        unregistered: a key holding something this build does not know about
        cannot be judged at all.
        """
        if kc.kind == KIND_FOLDER:
            return []
        if kc.kind == KIND_SINGLE:
            pairs = [(kc.action, kc.params)]
        else:
            pairs = [
                (step.action, step.params)
                for field in STEP_FIELDS
                for step in getattr(kc, field, [])
            ]
        pairs = [(action_id, params) for action_id, params in pairs if action_id]
        if not pairs:
            return []
        actions = [(action_registry.get(a), p) for a, p in pairs]
        if any(action is None for action, _ in actions):
            return []
        return actions

    def _back_spec(self, size) -> dict:
        """The reserved key that leaves the open folder.

        It shows the folder's own name, so the physical deck always says where
        it is, and it can never be configured away.
        """
        trail = self.folder_trail()
        return {
            "size": size,
            "label": trail[-1][1] if trail else FOLDER_BACK_LABEL,
            "icon_path": FOLDER_BACK_ICON,
            "bg": FOLDER_BACK_BG,
        }

    @staticmethod
    def _folder_spec(kc: KeyConfig, size) -> dict:
        contents = kc.contents
        configured = contents.configured_keys() if contents is not None else 0
        return {
            "size": size,
            "label": kc.label,
            "icon_path": kc.icon or DEFAULT_FOLDER_ICON,
            "bg": kc.bg_color,
            # How many keys are inside, so a folder is not a black box.
            "badge": str(configured) if configured else "",
            "font_size": kc.font_size,
            "text_color": kc.text_color,
        }

    @staticmethod
    def _first_step_icon(steps) -> str:
        for step in steps:
            action = action_registry.get(step.action)
            if action is None:
                continue
            if icon := action.icon_for(step.params):
                return icon
        return ""

    @staticmethod
    def _pulse_phase() -> bool:
        """Which half of the breath it is, from the clock.

        Derived from time rather than from the activity thread's phase, which
        only advances while something is actually running: an alert key
        breathes because somebody is waiting, not because work is in flight.
        """
        return bool(int(time.monotonic() / ALERT_PULSE_SECONDS) % 2)

    def _single_spec(self, index: int, kc: KeyConfig, size) -> dict:
        fb = None
        action = action_registry.get(kc.action)
        if action is not None:
            try:
                fb = action.feedback(
                    self.ctx.for_key(self._tkey(index)),
                    kc.params,
                )
            except Exception:
                log.debug("feedback of %s failed", kc.action, exc_info=True)
        fb = fb or {}
        # the key's own icon, or the action's default icon. Only the label the
        # user set explicitly is shown (without it the icon stays centered; the
        # action name is not used, which used to push the icon upwards).
        icon = kc.icon or (action.icon_for(kc.params) if action else "")
        busy, phase = self._busy_state(index)
        pulse = bool(fb.get("pulse", False))
        return {
            "size": size,
            # An action may replace the label, falling back to the one the user
            # typed. Only "what is playing" uses it, and it needs exactly this
            # mechanism: the label is the one place that wraps to two lines at
            # a constant size, while centered text is fitted to the key width
            # and so is drawn huge or unreadably small depending on its length.
            # The fallback is what makes a media key show its own name again
            # the moment nothing is playing.
            "label": fb.get("label") or kc.label,
            "icon_path": icon,
            "bg": fb.get("color") or kc.bg_color,
            "active": fb.get("active", False),
            "busy": busy,
            "busy_phase": phase if busy else (pulse and self._pulse_phase()),
            "badge": "RUN" if busy else fb.get("badge", ""),
            "center_text": fb.get("display", ""),
            "font_size": kc.font_size,
            "text_color": kc.text_color,
            "image": fb.get("image"),
            # An action may ask to breathe in its own colour rather than the
            # accent one `busy` uses.
            "pulse": pulse,
            # And it may mark itself with a border, which is the only signal
            # that survives a key showing a picture.
            "border": fb.get("border", ""),
        }

    def _toggle_spec(self, index: int, kc: KeyConfig, size) -> dict:
        busy, phase = self._busy_state(index)
        if self.toggle_state(index):
            icon = kc.icon or self._first_step_icon(kc.steps_on) or "mdi:toggle-switch"
            return {"size": size, "label": kc.label, "icon_path": icon,
                    "bg": kc.bg_color, "active": True, "busy": busy,
                    "busy_phase": phase, "badge": "RUN" if busy else "ON",
                    "font_size": kc.font_size,
                    "text_color": kc.text_color}
        icon = (kc.icon_off or kc.icon
                or self._first_step_icon(kc.steps_off) or "mdi:toggle-switch-off-outline")
        return {"size": size, "label": kc.label_off or kc.label, "icon_path": icon,
                "bg": kc.bg_color_off, "busy": busy, "busy_phase": phase,
                "badge": "RUN" if busy else "OFF",
                "font_size": kc.font_size_off or kc.font_size,
                "text_color": kc.text_color_off or kc.text_color}

    def wait_until_stopped(self, timeout: float) -> bool:
        """Wait for timeout seconds, returning early when shutdown begins."""
        return self._stopping.wait(timeout)

    def shutdown(self) -> None:
        """Cancel queued work and wait for running actions/renders to finish."""
        self._stopping.set()
        self.games.shutdown()
        self._clocks.shutdown()
        self._cancel_all_timer_sounds()
        self._clear_gestures()
        self._busy_wakeup.set()
        self._render_pending.clear()
        self._action_executor.shutdown(wait=True, cancel_futures=True)
        self._notification_executor.shutdown(wait=True, cancel_futures=True)
        self._busy_thread.join()
        # Joined alongside the activity thread and for the same reason: both
        # submit to the render executor, so neither may outlive it.
        self._live_thread.join()
        self._render_executor.shutdown(wait=True, cancel_futures=True)
