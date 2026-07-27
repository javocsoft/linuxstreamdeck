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
from .actions import ActionContext
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
    ActionStep,
    Config,
    ImportResult,
    KeyConfig,
    KeyGrid,
    folder_depth,
)
from .events import EventBus
from ..device.manager import DeckManager
from ..device import renderer

log = logging.getLogger(__name__)

BUSY_PULSE_SECONDS = 0.75

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
    def __init__(self, config: Config, bus: EventBus, obs, deck: DeckManager) -> None:
        self.config = config
        self.bus = bus
        self.obs = obs
        self.deck = deck
        self.ctx = ActionContext(obs=obs, controller=self, bus=bus)
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
        # ON/OFF state of toggle keys, keyed by RuntimeKey
        self._toggle: dict[RuntimeKey, bool] = {}
        # Number of queued/running feedback-enabled invocations for each key.
        self._running: dict[RuntimeKey, int] = {}
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
        self._busy_thread = threading.Thread(
            target=self._busy_loop,
            daemon=True,
            name="key-activity",
        )
        self._busy_thread.start()

        bus.subscribe("deck.key", self._on_deck_key)
        bus.subscribe("deck.dial", self._on_deck_dial)
        bus.subscribe("deck.touch", self._on_deck_touch)
        bus.subscribe("deck.connected", lambda t, d: self.refresh())
        bus.subscribe("deck.screensaver", self._on_screensaver)
        bus.subscribe("obs.state", lambda t, d: self.refresh())

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
        return (
            self.current_profile,
            self.current_page,
            self._folder_path,
            index,
        )

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
        self._clear_time_actions()
        # Those keys are gone, or their indices have shifted under it.
        self.forget_undo()
        self._leave_folders()
        self.config.current_page = min(self.current_page, len(pages) - 1)
        self.config.save()
        self.bus.emit("page.changed", index=self.current_page, name=self.page.name)
        self.refresh()

    # ---------- configuration import ----------

    def import_configuration(self, source: Path) -> ImportResult:
        """Replace the configuration and apply its runtime settings."""
        result = self.config.import_bundle(source)
        self._toggle.clear()
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
        return result

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
        # the toggle state travels with the key
        pa, pb = self._tkey(a), self._tkey(b)
        sa, sb = self._toggle.pop(pa, None), self._toggle.pop(pb, None)
        if sb is not None:
            self._toggle[pa] = sb
        if sa is not None:
            self._toggle[pb] = sa
        self._clocks.swap(pa, pb)
        self._cancel_timer_sound(pa)
        self._cancel_timer_sound(pb)
        # A gesture in flight belonged to the key that just moved away.
        self._cancel_gesture(pa)
        self._cancel_gesture(pb)
        # A folder that moved would leave its contents' state under the old
        # index. Those keys are transient, so drop them and re-render, exactly
        # as deleting a page does.
        self._discard_folder_state(a)
        self._discard_folder_state(b)
        self.config.save()
        self.refresh()

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
        self._clocks.reset(key, refresh=False)
        self._cancel_timer_sound(key)
        self._cancel_gesture(key)
        if drop_folder_state:
            self._discard_folder_state(index)

    def _discard_folder_state(self, index: int) -> None:
        """Forget the transient state of every key inside a folder slot."""
        profile, page, path = self._view()
        prefix = path + (index,)

        def inside(key: RuntimeKey) -> bool:
            key_profile, key_page, key_path, _key_index = key
            return (
                (key_profile, key_page) == (profile, page)
                and key_path[: len(prefix)] == prefix
            )

        for key in [key for key in self._toggle if inside(key)]:
            self._toggle.pop(key, None)
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
        self._submit_steps(steps, index, show_running=True)

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
        if self._stopping.is_set() or self.deck.record_activity():
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
        )

    # ---------- dials (Stream Deck +) ----------

    def dial(self, index: int):
        """The dial's configuration on the active page, if it has one."""
        return self.page.dial(index)

    def _on_deck_dial(self, _topic: str, data: dict) -> None:
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
        self.turn_dial(int(data.get("index", 0)), "press", 1)

    def turn_dial(self, index: int, direction: str, ticks: int = 1) -> None:
        """Run what an encoder gesture is configured to do.

        A turn arrives with a tick count, and the steps run once per tick so a
        fast spin moves a volume by the amount the hand actually turned. The
        count is bounded: the actions are queued on a worker, and a spin that
        outran the queue could otherwise leave it running for a long time.
        """
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

    def _run_steps(
        self,
        steps: list[ActionStep],
        index: int,
        runtime_key: RuntimeKey | None = None,
        control: _ExecutionControl | None = None,
        execution_key: RuntimeKey | None = None,
    ) -> None:
        run_ctx = ActionContext(
            obs=self.obs,
            controller=self,
            bus=self.bus,
            cancellation=control.cancel if control is not None else None,
            key=execution_key,
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
                    self.bus.emit("status", text=f"Error in «{action.name}»: {e}")
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

    def _busy_loop(self) -> None:
        while not self._stopping.is_set():
            self._busy_wakeup.wait()
            if self._stopping.is_set():
                return
            if self._stopping.wait(BUSY_PULSE_SECONDS):
                return
            with self._running_lock:
                if not self._running:
                    self._busy_phase = False
                    self._busy_wakeup.clear()
                    continue
                self._busy_phase = not self._busy_phase
                keys = tuple(self._running)
            self._refresh_runtime_keys(keys)

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
        # resolve against a different key.
        self._clear_gestures()

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
        if view != self._view() or self.deck.screensaver_active:
            return
        grid = self.container
        size = self.deck.image_size
        for index in indices:
            if self._stopping.is_set() or self.deck.screensaver_active:
                return
            spec = self._key_spec(index, grid.key(index), size)
            image = renderer.compose(**spec)
            if view != self._view() or self.deck.screensaver_active:
                return
            self.deck.set_key_image(index, image)
            self.bus.emit("ui.key_image", index=index, png=renderer.to_png_bytes(image))

    # ---------- key specs (feedback + compose parameters) ----------

    def _key_spec(self, index: int, kc: KeyConfig | None, size) -> dict:
        if self.is_reserved_key(index):
            return self._back_spec(size)
        if kc is None or kc.is_empty():
            return {"size": size}
        if kc.kind == KIND_FOLDER:
            return self._folder_spec(kc, size)
        if kc.kind == KIND_TOGGLE:
            return self._toggle_spec(index, kc, size)
        if kc.kind in (KIND_MULTI, KIND_RANDOM, KIND_PRESS):
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
        return self._single_spec(index, kc, size)

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
            if action is not None and action.default_icon:
                return action.default_icon
        return ""

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
        icon = kc.icon or (action.default_icon if action else "")
        busy, phase = self._busy_state(index)
        return {
            "size": size,
            "label": kc.label,
            "icon_path": icon,
            "bg": fb.get("color") or kc.bg_color,
            "active": fb.get("active", False),
            "busy": busy,
            "busy_phase": phase,
            "badge": "RUN" if busy else fb.get("badge", ""),
            "center_text": fb.get("display", ""),
            "font_size": kc.font_size,
            "text_color": kc.text_color,
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
        self._clocks.shutdown()
        self._cancel_all_timer_sounds()
        self._clear_gestures()
        self._busy_wakeup.set()
        self._render_pending.clear()
        self._action_executor.shutdown(wait=True, cancel_futures=True)
        self._notification_executor.shutdown(wait=True, cancel_futures=True)
        self._busy_thread.join()
        self._render_executor.shutdown(wait=True, cancel_futures=True)
