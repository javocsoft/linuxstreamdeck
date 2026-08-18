"""Exclusive Stream Deck game session and its worker lifecycle."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from ..core.config import GameSettings
from .audio import GameSoundPlayer
from .catalog import DEFAULT_GAME_ID, game_info
from .common import PHASE_LOBBY, EngineEvent, game_layout
from .circuit_breaker import CircuitBreakerEngine
from .memory_match import MemoryMatchEngine
from .mastermind import MastermindEngine
from .minesweeper import MinesweeperEngine
from .mole_smash import MoleSmashEngine
from .neon_relay import NeonRelayEngine
from .pulse_memory import PulseMemoryEngine
from .tic_tac_toe import TicTacToeEngine
from .render import render_keys, to_png_bytes, touchscreen_hud

log = logging.getLogger(__name__)

FRAME_SECONDS = 0.05
SAVER_RELEASE_SECONDS = 2.0

_GAME_ENGINES = {
    "mole_smash": MoleSmashEngine,
    "circuit_breaker": CircuitBreakerEngine,
    "pulse_memory": PulseMemoryEngine,
    "memory_match": MemoryMatchEngine,
    "minesweeper": MinesweeperEngine,
    "tic_tac_toe": TicTacToeEngine,
    "mastermind": MastermindEngine,
    "neon_relay": NeonRelayEngine,
}

_SETTING_FIELDS = {
    "mole_smash": (
        "mole_difficulty",
        "mole_sound_enabled",
        "mole_volume",
        "mole_high_scores",
    ),
    "circuit_breaker": (
        "circuit_difficulty",
        "circuit_sound_enabled",
        "circuit_volume",
        "circuit_best_moves",
    ),
    "pulse_memory": (
        "pulse_difficulty",
        "pulse_sound_enabled",
        "pulse_volume",
        "pulse_high_scores",
    ),
    "memory_match": (
        "memory_difficulty",
        "memory_sound_enabled",
        "memory_volume",
        "memory_best_moves",
    ),
    "minesweeper": (
        "mines_difficulty",
        "mines_sound_enabled",
        "mines_volume",
        "mines_best_times",
    ),
    "tic_tac_toe": (
        "tic_tac_toe_difficulty",
        "tic_tac_toe_sound_enabled",
        "tic_tac_toe_volume",
        "tic_tac_toe_wins",
    ),
    "mastermind": (
        "mastermind_difficulty",
        "mastermind_sound_enabled",
        "mastermind_volume",
        "mastermind_best_attempts",
    ),
    "neon_relay": (
        "relay_difficulty",
        "relay_sound_enabled",
        "relay_volume",
        "relay_high_scores",
    ),
}


class GameManager:
    """Owns input and pixels while one built-in game is active."""

    def __init__(
        self,
        bus,
        deck,
        settings: GameSettings,
        restore: Callable[[], None],
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sound_player: GameSoundPlayer | None = None,
    ) -> None:
        self.bus = bus
        self.deck = deck
        self.settings = settings
        self._restore = restore
        self._monotonic = monotonic
        self._lock = threading.RLock()
        self._active = threading.Event()
        self._stop = threading.Event()
        self._shutdown = threading.Event()
        self._thread: threading.Thread | None = None
        self._finishing = False
        self._engine = None
        self._game_id = ""
        self._game_name = ""
        self._owned_down: set[int] = set()
        self._last_png: dict[int, bytes] = {}
        self._last_touch_png = b""
        self._announced_phase = ""
        # Lazily created on the first session. Most controller tests and users
        # who never open Games should not pay for two idle audio workers.
        self._sound = sound_player
        bus.subscribe("deck.disconnected", self._on_deck_disconnected)
        bus.subscribe("deck.connected", self._on_deck_connected)

    @property
    def active(self) -> bool:
        return self._active.is_set()

    @property
    def phase(self) -> str:
        with self._lock:
            return self._engine.phase if self._engine is not None else ""

    @property
    def game_id(self) -> str:
        with self._lock:
            return self._game_id

    @property
    def game_name(self) -> str:
        with self._lock:
            return self._game_name

    def start(self, game_id: str = DEFAULT_GAME_ID) -> bool:
        info = game_info(game_id)
        engine_type = _GAME_ENGINES.get(str(game_id))
        fields = _SETTING_FIELDS.get(str(game_id))
        if (
            info is None
            or engine_type is None
            or fields is None
            or self._shutdown.is_set()
        ):
            return False
        with self._lock:
            if self._active.is_set() or self._finishing:
                return False
            if self._sound is None:
                self._sound = GameSoundPlayer(self._sound_error)
            layout = self._layout()
            difficulty_field, sound_field, _volume_field, scores_field = fields
            difficulty = getattr(self.settings, difficulty_field)
            score_key = f"{layout.score_id}:{difficulty}"
            scores = getattr(self.settings, scores_field)
            self._engine = engine_type(
                layout,
                difficulty=difficulty,
                sound_enabled=getattr(self.settings, sound_field),
                high_score=scores.get(score_key, 0),
                now=self._monotonic(),
            )
            self._game_id = info.id
            self._game_name = info.name
            self._stop.clear()
            self._owned_down.clear()
            self._last_png.clear()
            self._last_touch_png = b""
            self._announced_phase = PHASE_LOBBY
            self._active.set()
            self._suppress_saver(True)
            stop_saver = getattr(self.deck, "stop_screensaver", None)
            if callable(stop_saver):
                stop_saver()
            self._thread = threading.Thread(
                target=self._loop,
                daemon=True,
                name=f"game-{info.id}",
            )
            self._thread.start()
        self._emit_state(True, PHASE_LOBBY)
        return True

    def stop(self) -> bool:
        if not self._active.is_set():
            return False
        self._stop.set()
        return True

    def adopt_settings(self, settings: GameSettings) -> None:
        """Follow a configuration object replaced by import or backup restore."""
        with self._lock:
            self.settings = settings

    def handle_key(self, index: int, pressed: bool) -> bool:
        """Consume both edges of every press that began inside the game."""
        index = int(index)
        with self._lock:
            if pressed and self._active.is_set():
                self._owned_down.add(index)
                self._press_locked(index)
                return True
            if not pressed and index in self._owned_down:
                self._owned_down.discard(index)
                return True
            return self._active.is_set()

    def press_virtual(self, index: int) -> bool:
        with self._lock:
            if not self._active.is_set():
                return False
            self._press_locked(int(index))
            return True

    def handle_dial(self, index: int, direction: str, ticks: int = 1) -> bool:
        """Give an active game first refusal of a Plus dial or strip gesture."""
        with self._lock:
            if not self._active.is_set():
                return False
            engine = self._engine
            handler = getattr(engine, "dial", None)
            if callable(handler):
                events = handler(
                    int(index),
                    str(direction),
                    int(ticks),
                    self._monotonic(),
                )
                self._handle_events_locked(events)
            # Every game owns the whole device during its session. A game that
            # ignores dials must not leak the gesture to a configured action.
            return True

    def shutdown(self) -> None:
        self._shutdown.set()
        self._stop.set()
        with self._lock:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join()
        with self._lock:
            sound = self._sound
            self._sound = None
        if sound is not None:
            sound.shutdown()

    def _layout(self):
        return game_layout(
            int(getattr(self.deck, "key_count", 15)),
            int(getattr(self.deck, "columns", 5)),
            bool(getattr(self.deck, "dial_count", 0)),
        )

    def _press_locked(self, index: int) -> None:
        engine = self._engine
        if engine is None:
            return
        events = engine.press(index, self._monotonic())
        self._handle_events_locked(events)

    def _loop(self) -> None:
        try:
            deadline = self._monotonic() + SAVER_RELEASE_SECONDS
            while (
                bool(getattr(self.deck, "screensaver_active", False))
                and self._monotonic() < deadline
                and not self._stop.wait(0.02)
            ):
                pass
            while not self._stop.is_set() and not self._shutdown.is_set():
                started = self._monotonic()
                with self._lock:
                    engine = self._engine
                    if engine is None:
                        break
                    events = engine.tick(started)
                    self._handle_events_locked(events)
                    snapshot = engine.snapshot(started)
                if self._stop.is_set():
                    break
                self._paint(snapshot)
                remaining = FRAME_SECONDS - (self._monotonic() - started)
                if remaining > 0:
                    self._stop.wait(remaining)
        except Exception:
            name = self.game_name or "The game"
            log.exception("%s session failed", name)
            self.bus.emit("status", text=f"{name} stopped after an unexpected error")
        finally:
            self._finish()

    def _handle_events_locked(self, events: tuple[EngineEvent, ...]) -> None:
        engine = self._engine
        if engine is None:
            return
        fields = _SETTING_FIELDS.get(self._game_id)
        if fields is None:
            return
        difficulty_field, sound_field, volume_field, scores_field = fields
        persist = False
        for event in events:
            if event.exit_requested:
                self._stop.set()
            if event.settings_changed:
                setattr(self.settings, difficulty_field, engine.difficulty)
                setattr(self.settings, sound_field, engine.sound_enabled)
                scores = getattr(self.settings, scores_field)
                engine.set_high_score(scores.get(engine.score_key, 0))
                persist = True
            if event.high_score_changed:
                scores = getattr(self.settings, scores_field)
                scores[engine.score_key] = engine.high_score
                persist = True
            if event.cue and engine.sound_enabled:
                if self._sound is not None:
                    self._sound.play(
                        self._game_id,
                        event.cue,
                        getattr(self.settings, volume_field),
                    )
        if persist:
            self.bus.emit("game.settings")
        if engine.phase != self._announced_phase:
            self._announced_phase = engine.phase
            self._emit_state(True, engine.phase)

    def _paint(self, snapshot) -> None:
        images = render_keys(snapshot, tuple(self.deck.image_size))
        for index, image in enumerate(images):
            png = to_png_bytes(image)
            if self._last_png.get(index) == png:
                continue
            self._last_png[index] = png
            self.deck.set_key_image(index, image)
            self.bus.emit("ui.key_image", index=index, png=png)
        if snapshot.layout.touchscreen_hud and getattr(self.deck, "dial_count", 0):
            touch = touchscreen_hud(snapshot, tuple(self.deck.touch_size))
            png = to_png_bytes(touch)
            if png != self._last_touch_png:
                self._last_touch_png = png
                self.deck.set_touchscreen_image(touch)
                self.bus.emit("ui.game_hud", png=png)

    def _finish(self) -> None:
        with self._lock:
            was_active = self._active.is_set()
            self._finishing = True
            sound = self._sound
            name = self._game_name or "The game"
        if sound is not None:
            sound.cancel()
        self._suppress_saver(False)
        # Keep _finishing true through the restore. A menu callback dispatched
        # by the inactive event must not start a replacement session while this
        # one is still releasing its screen-saver reason and pixels.
        with self._lock:
            self._active.clear()
            self._engine = None
            self._last_png.clear()
            self._last_touch_png = b""
            self.bus.emit("ui.game_hud", png=b"")
            self._announced_phase = ""
            try:
                if was_active:
                    self._emit_state(False, "")
                    if not self._shutdown.is_set():
                        self._restore()
            except Exception:
                log.exception("Could not restore the deck after %s", name)
                self.bus.emit(
                    "status",
                    text=f"{name} ended, but the deck could not be restored",
                )
            finally:
                self._game_id = ""
                self._game_name = ""
                self._thread = None
                self._finishing = False

    def _suppress_saver(self, suppressed: bool) -> None:
        setter = getattr(self.deck, "set_screensaver_suppressed", None)
        if not callable(setter):
            return
        try:
            setter(bool(suppressed), reason="game")
        except TypeError:
            # Test doubles and third-party integrations written against the
            # earlier one-argument method keep working.
            setter(bool(suppressed))

    def _emit_state(self, active: bool, phase: str) -> None:
        info = game_info(self._game_id) if active else None
        self.bus.emit(
            "game.state",
            active=bool(active),
            game=self._game_id if active else "",
            name=self._game_name if active else "",
            phase=str(phase),
            hint=info.hint if info is not None else "",
        )

    def _sound_error(self, text: str) -> None:
        self.bus.emit("status", text=text)

    def _on_deck_disconnected(self, _topic, _data) -> None:
        name = self.game_name or "The game"
        if self.stop():
            self.bus.emit(
                "status",
                text=f"{name} ended because the Stream Deck disconnected",
            )

    def _on_deck_connected(self, _topic, _data) -> None:
        # A game started against the virtual default geometry cannot safely
        # continue when a differently shaped physical deck appears underneath.
        name = self.game_name or "The game"
        if self.stop():
            self.bus.emit(
                "status",
                text=f"{name} ended because the deck layout changed",
            )
