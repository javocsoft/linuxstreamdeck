"""Pure Lights Out-style state machine for Circuit Breaker."""

from __future__ import annotations

import random
from dataclasses import dataclass

from ..core.config import DEFAULT_GAME_DIFFICULTY, GAME_DIFFICULTIES
from .common import (
    PHASE_LOBBY,
    PHASE_PLAYING,
    PHASE_RESULTS,
    EngineEvent,
    GameLayout,
)

GAME_ID = "circuit_breaker"
GAME_NAME = "Circuit Breaker"
PRESS_FLASH_SECONDS = 0.16

SCRAMBLE_DENSITY = {
    "easy": 0.28,
    "normal": 0.52,
    "hard": 0.82,
}


@dataclass(frozen=True)
class CircuitSnapshot:
    phase: str
    layout: GameLayout
    difficulty: str
    sound_enabled: bool
    lights: tuple[bool, ...]
    moves: int
    high_score: int
    new_high_score: bool
    elapsed_seconds: int
    last_pressed: int | None
    progress: float


class CircuitBreakerEngine:
    """One lobby and any number of guaranteed-solvable light puzzles."""

    def __init__(
        self,
        layout: GameLayout,
        difficulty: str = DEFAULT_GAME_DIFFICULTY,
        sound_enabled: bool = True,
        high_score: int = 0,
        *,
        rng: random.Random | None = None,
        now: float = 0.0,
    ) -> None:
        self.layout = layout
        self.difficulty = (
            difficulty if difficulty in SCRAMBLE_DENSITY else DEFAULT_GAME_DIFFICULTY
        )
        self.sound_enabled = bool(sound_enabled)
        self.high_score = max(0, int(high_score))
        self.phase = PHASE_LOBBY
        self.moves = 0
        self.new_high_score = False
        self._rng = rng or random.Random()
        self._lights = [False] * layout.key_count
        self._started = float(now)
        self._last_pressed: int | None = None
        self._flash_until = 0.0

    @property
    def score_key(self) -> str:
        return f"{self.layout.score_id}:{self.difficulty}"

    def set_high_score(self, value: int) -> None:
        self.high_score = max(0, int(value))

    def press(self, index: int, now: float) -> tuple[EngineEvent, ...]:
        index = int(index)
        now = float(now)
        if self.phase in (PHASE_LOBBY, PHASE_RESULTS):
            if index == self.layout.exit_key:
                return (EngineEvent(exit_requested=True),)
            if index == self.layout.difficulty_key:
                current = GAME_DIFFICULTIES.index(self.difficulty)
                self.difficulty = GAME_DIFFICULTIES[
                    (current + 1) % len(GAME_DIFFICULTIES)
                ]
                self.new_high_score = False
                return (EngineEvent(cue="select", settings_changed=True),)
            if index == self.layout.sound_key:
                self.sound_enabled = not self.sound_enabled
                return (EngineEvent(cue="select", settings_changed=True),)
            if index == self.layout.start_key:
                self._begin(now)
                return (EngineEvent(cue="go"),)
            return ()
        if self.phase != PHASE_PLAYING or not 0 <= index < len(self._lights):
            return ()

        self._toggle(index)
        self.moves += 1
        self._last_pressed = index
        self._flash_until = now + PRESS_FLASH_SECONDS
        if any(self._lights):
            return (EngineEvent(cue="circuit"),)

        self.phase = PHASE_RESULTS
        changed = self.high_score == 0 or self.moves < self.high_score
        if changed:
            self.high_score = self.moves
            self.new_high_score = True
        return (
            EngineEvent(
                cue="record" if changed else "finish",
                high_score_changed=changed,
            ),
        )

    def tick(self, now: float) -> tuple[EngineEvent, ...]:
        if float(now) >= self._flash_until:
            self._last_pressed = None
        return ()

    def snapshot(self, now: float) -> CircuitSnapshot:
        on_count = sum(self._lights)
        return CircuitSnapshot(
            phase=self.phase,
            layout=self.layout,
            difficulty=self.difficulty,
            sound_enabled=self.sound_enabled,
            lights=tuple(self._lights),
            moves=self.moves,
            high_score=self.high_score,
            new_high_score=self.new_high_score,
            elapsed_seconds=(
                max(0, int(float(now) - self._started))
                if self.phase in (PHASE_PLAYING, PHASE_RESULTS)
                else 0
            ),
            last_pressed=self._last_pressed,
            progress=(len(self._lights) - on_count) / max(1, len(self._lights)),
        )

    def _begin(self, now: float) -> None:
        self.phase = PHASE_PLAYING
        self.moves = 0
        self.new_high_score = False
        self._started = float(now)
        self._last_pressed = None
        self._lights = [False] * self.layout.key_count
        count = max(
            1,
            min(
                self.layout.key_count,
                round(self.layout.key_count * SCRAMBLE_DENSITY[self.difficulty]),
            ),
        )
        choices = list(range(self.layout.key_count))
        for _step in range(count):
            chosen = self._rng.choice(choices)
            choices.remove(chosen)
            self._toggle(chosen)
        # Some rectangular Lights Out matrices have a non-trivial kernel. A
        # rare all-off scramble would look like an already completed round;
        # one extra legal move keeps the puzzle solvable and non-empty.
        if not any(self._lights):
            self._toggle(self._rng.choice(range(self.layout.key_count)))

    def _toggle(self, index: int) -> None:
        rows = self.layout.rows
        columns = self.layout.columns
        row, column = divmod(index, columns)
        candidates = (
            (row, column),
            (row - 1, column),
            (row + 1, column),
            (row, column - 1),
            (row, column + 1),
        )
        for candidate_row, candidate_column in candidates:
            candidate = candidate_row * columns + candidate_column
            if (
                0 <= candidate_row < rows
                and 0 <= candidate_column < columns
                and 0 <= candidate < self.layout.key_count
            ):
                self._lights[candidate] = not self._lights[candidate]
