"""Exclusive Stream Deck game session and its worker lifecycle."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from ..core.config import GameSettings
from .audio import GameSoundPlayer
from .mole_smash import (
    GAME_ID,
    GAME_NAME,
    PHASE_LOBBY,
    EngineEvent,
    MoleSmashEngine,
    game_layout,
)
from .render import render_keys, to_png_bytes, touchscreen_hud

log = logging.getLogger(__name__)

FRAME_SECONDS = 0.05
SAVER_RELEASE_SECONDS = 2.0


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
        self._engine: MoleSmashEngine | None = None
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

    def start(self, game_id: str = GAME_ID) -> bool:
        if game_id != GAME_ID or self._shutdown.is_set():
            return False
        with self._lock:
            if self._active.is_set() or self._finishing:
                return False
            if self._sound is None:
                self._sound = GameSoundPlayer(self._sound_error)
            layout = self._layout()
            score_key = f"{layout.score_id}:{self.settings.mole_difficulty}"
            self._engine = MoleSmashEngine(
                layout,
                difficulty=self.settings.mole_difficulty,
                sound_enabled=self.settings.mole_sound_enabled,
                high_score=self.settings.mole_high_scores.get(score_key, 0),
                now=self._monotonic(),
            )
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
                name="game-mole-smash",
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
            log.exception("Mole Smash session failed")
            self.bus.emit("status", text="Mole Smash stopped after an unexpected error")
        finally:
            self._finish()

    def _handle_events_locked(self, events: tuple[EngineEvent, ...]) -> None:
        engine = self._engine
        if engine is None:
            return
        persist = False
        for event in events:
            if event.exit_requested:
                self._stop.set()
            if event.settings_changed:
                self.settings.mole_difficulty = engine.difficulty
                self.settings.mole_sound_enabled = engine.sound_enabled
                engine.set_high_score(
                    self.settings.mole_high_scores.get(engine.score_key, 0)
                )
                persist = True
            if event.high_score_changed:
                self.settings.mole_high_scores[engine.score_key] = engine.high_score
                persist = True
            if event.cue and engine.sound_enabled:
                if self._sound is not None:
                    self._sound.play(event.cue, self.settings.mole_volume)
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
                log.exception("Could not restore the deck after Mole Smash")
                self.bus.emit(
                    "status",
                    text="Mole Smash ended, but the deck could not be restored",
                )
            finally:
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
        self.bus.emit(
            "game.state",
            active=bool(active),
            game=GAME_ID if active else "",
            name=GAME_NAME if active else "",
            phase=str(phase),
        )

    def _sound_error(self, text: str) -> None:
        self.bus.emit("status", text=text)

    def _on_deck_disconnected(self, _topic, _data) -> None:
        if self.stop():
            self.bus.emit("status", text="Mole Smash ended because the Stream Deck disconnected")

    def _on_deck_connected(self, _topic, _data) -> None:
        # A game started against the virtual default geometry cannot safely
        # continue when a differently shaped physical deck appears underneath.
        if self.stop():
            self.bus.emit("status", text="Mole Smash ended because the deck layout changed")
