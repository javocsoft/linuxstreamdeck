"""Pure adaptive colour Mastermind state machine."""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass

from ..core.config import DEFAULT_GAME_DIFFICULTY, GAME_DIFFICULTIES
from .common import (
    PHASE_LOBBY,
    PHASE_PLAYING,
    PHASE_RESULTS,
    EngineEvent,
    GameLayout,
)

GAME_ID = "mastermind"
GAME_NAME = "Colour Mastermind"


@dataclass(frozen=True)
class MastermindDifficulty:
    code_length: int
    color_count: int
    attempts: int


DIFFICULTIES = {
    "easy": MastermindDifficulty(3, 4, 8),
    "normal": MastermindDifficulty(4, 6, 10),
    "hard": MastermindDifficulty(5, 8, 12),
}


@dataclass(frozen=True)
class GuessView:
    colors: tuple[int, ...]
    exact: int
    color_only: int


@dataclass(frozen=True)
class MastermindSnapshot:
    phase: str
    layout: GameLayout
    difficulty: str
    sound_enabled: bool
    slot_keys: tuple[int, ...]
    submit_key: int
    clear_key: int | None
    history_keys: tuple[int, ...]
    current: tuple[int, ...]
    color_count: int
    history: tuple[GuessView, ...]
    solution: tuple[int, ...]
    attempts_left: int
    max_attempts: int
    high_score: int
    new_high_score: bool
    won: bool | None
    progress: float


class MastermindEngine:
    """Cycle each peg, submit it, and score exact and colour-only matches."""

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
        del now
        self.layout = layout
        self.difficulty = (
            difficulty if difficulty in DIFFICULTIES else DEFAULT_GAME_DIFFICULTY
        )
        self.sound_enabled = bool(sound_enabled)
        self.high_score = max(0, int(high_score))
        self.phase = PHASE_LOBBY
        self.new_high_score = False
        self.won: bool | None = None
        self._rng = rng or random.Random()
        self._slot_keys: tuple[int, ...] = ()
        self._submit_key = 0
        self._clear_key: int | None = None
        self._history_keys: tuple[int, ...] = ()
        self._current: list[int] = []
        self._solution: tuple[int, ...] = ()
        self._history: list[GuessView] = []
        self._configure_board()

    @property
    def score_key(self) -> str:
        return f"{self.layout.score_id}:{self.difficulty}"

    def set_high_score(self, value: int) -> None:
        self.high_score = max(0, int(value))

    def press(self, index: int, _now: float) -> tuple[EngineEvent, ...]:
        index = int(index)
        if self.phase == PHASE_RESULTS:
            if index == self._clear_key:
                return (EngineEvent(exit_requested=True),)
            if index == self._submit_key:
                self._begin()
                return (EngineEvent(cue="go"),)
            return ()
        if self.phase == PHASE_LOBBY:
            if index == self.layout.exit_key:
                return (EngineEvent(exit_requested=True),)
            if index == self.layout.difficulty_key:
                current = GAME_DIFFICULTIES.index(self.difficulty)
                self.difficulty = GAME_DIFFICULTIES[
                    (current + 1) % len(GAME_DIFFICULTIES)
                ]
                self._configure_board()
                self.new_high_score = False
                return (EngineEvent(cue="select", settings_changed=True),)
            if index == self.layout.sound_key:
                self.sound_enabled = not self.sound_enabled
                return (EngineEvent(cue="select", settings_changed=True),)
            if index == self.layout.start_key:
                self._begin()
                return (EngineEvent(cue="go"),)
            return ()
        if self.phase != PHASE_PLAYING:
            return ()
        if index in self._slot_keys:
            slot = self._slot_keys.index(index)
            self._current[slot] = (self._current[slot] + 1) % self._settings.color_count
            return (EngineEvent(cue="peg"),)
        if self._clear_key is not None and index == self._clear_key:
            self._current = [0] * len(self._slot_keys)
            return (EngineEvent(cue="select"),)
        if index != self._submit_key:
            return ()

        exact, color_only = self._score(tuple(self._current), self._solution)
        self._history.append(GuessView(tuple(self._current), exact, color_only))
        attempts = len(self._history)
        if exact == len(self._solution):
            self.won = True
            self.phase = PHASE_RESULTS
            changed = self.high_score == 0 or attempts < self.high_score
            if changed:
                self.high_score = attempts
                self.new_high_score = True
            return (
                EngineEvent(
                    cue="record" if changed else "finish",
                    high_score_changed=changed,
                ),
            )
        if attempts >= self._settings.attempts:
            self.won = False
            self.phase = PHASE_RESULTS
            return (EngineEvent(cue="wrong"),)
        self._current = [0] * len(self._slot_keys)
        return (EngineEvent(cue="submit"),)

    def tick(self, _now: float) -> tuple[EngineEvent, ...]:
        return ()

    def snapshot(self, _now: float) -> MastermindSnapshot:
        attempts = len(self._history)
        return MastermindSnapshot(
            phase=self.phase,
            layout=self.layout,
            difficulty=self.difficulty,
            sound_enabled=self.sound_enabled,
            slot_keys=self._slot_keys,
            submit_key=self._submit_key,
            clear_key=self._clear_key,
            history_keys=self._history_keys,
            current=tuple(self._current),
            color_count=self._settings.color_count,
            history=tuple(self._history),
            solution=self._solution if self.phase == PHASE_RESULTS else (),
            attempts_left=max(0, self._settings.attempts - attempts),
            max_attempts=self._settings.attempts,
            high_score=self.high_score,
            new_high_score=self.new_high_score,
            won=self.won,
            progress=min(1.0, attempts / self._settings.attempts),
        )

    @property
    def _settings(self) -> MastermindDifficulty:
        return DIFFICULTIES[self.difficulty]

    def _configure_board(self) -> None:
        slot_count = max(2, min(self._settings.code_length, self.layout.key_count - 2))
        self._slot_keys = tuple(range(slot_count))
        self._submit_key = slot_count
        self._clear_key = slot_count + 1 if slot_count + 1 < self.layout.key_count else None
        controls = {*self._slot_keys, self._submit_key}
        if self._clear_key is not None:
            controls.add(self._clear_key)
        self._history_keys = tuple(
            index for index in range(self.layout.key_count) if index not in controls
        )
        self._current = [0] * slot_count

    def _begin(self) -> None:
        self._configure_board()
        self.phase = PHASE_PLAYING
        self.new_high_score = False
        self.won = None
        self._history.clear()
        self._current = [0] * len(self._slot_keys)
        self._solution = tuple(
            self._rng.randrange(self._settings.color_count)
            for _slot in self._slot_keys
        )

    @staticmethod
    def _score(guess: tuple[int, ...], solution: tuple[int, ...]) -> tuple[int, int]:
        exact = sum(left == right for left, right in zip(guess, solution, strict=True))
        shared = sum((Counter(guess) & Counter(solution)).values())
        return exact, shared - exact
