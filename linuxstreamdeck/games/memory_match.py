"""Pure pair-matching state machine for Memory Match."""

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

GAME_ID = "memory_match"
GAME_NAME = "Memory Match"
PHASE_PREVIEW = "preview"
PHASE_MISMATCH = "mismatch"


@dataclass(frozen=True)
class MemoryDifficulty:
    preview_seconds: float
    mismatch_seconds: float


DIFFICULTIES = {
    "easy": MemoryDifficulty(2.2, 1.10),
    "normal": MemoryDifficulty(1.1, 0.85),
    "hard": MemoryDifficulty(0.0, 0.58),
}


@dataclass(frozen=True)
class CardView:
    index: int
    symbol: int
    state: str


@dataclass(frozen=True)
class MemorySnapshot:
    phase: str
    layout: GameLayout
    difficulty: str
    sound_enabled: bool
    cards: tuple[CardView, ...]
    status_key: int | None
    moves: int
    pairs_found: int
    pair_count: int
    high_score: int
    new_high_score: bool
    elapsed_seconds: int
    progress: float


class MemoryMatchEngine:
    """Reveal pairs, hold mismatches briefly, and count completed turns."""

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
            difficulty if difficulty in DIFFICULTIES else DEFAULT_GAME_DIFFICULTY
        )
        self.sound_enabled = bool(sound_enabled)
        self.high_score = max(0, int(high_score))
        self.phase = PHASE_LOBBY
        self.moves = 0
        self.new_high_score = False
        self._rng = rng or random.Random()
        card_count = layout.key_count - (layout.key_count % 2)
        self._card_keys = tuple(range(card_count))
        self._status_key = card_count if card_count < layout.key_count else None
        self._symbols: dict[int, int] = {}
        self._matched: set[int] = set()
        self._revealed: list[int] = []
        self._deadline = float(now)
        self._started = float(now)

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
                return (
                    EngineEvent(cue="select" if self.phase == PHASE_PREVIEW else "go"),
                )
            return ()
        if self.phase != PHASE_PLAYING or index not in self._symbols:
            return ()
        if index in self._matched or index in self._revealed:
            return ()
        self._revealed.append(index)
        if len(self._revealed) == 1:
            return (EngineEvent(cue="select"),)

        self.moves += 1
        first, second = self._revealed
        if self._symbols[first] == self._symbols[second]:
            self._matched.update((first, second))
            self._revealed.clear()
            if len(self._matched) == len(self._symbols):
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
            return (EngineEvent(cue="hit"),)

        self.phase = PHASE_MISMATCH
        self._deadline = now + DIFFICULTIES[self.difficulty].mismatch_seconds
        return (EngineEvent(cue="wrong"),)

    def tick(self, now: float) -> tuple[EngineEvent, ...]:
        now = float(now)
        if self.phase == PHASE_PREVIEW and now >= self._deadline:
            self.phase = PHASE_PLAYING
            self._started = now
            return (EngineEvent(cue="go"),)
        if self.phase == PHASE_MISMATCH and now >= self._deadline:
            self._revealed.clear()
            self.phase = PHASE_PLAYING
        return ()

    def snapshot(self, now: float) -> MemorySnapshot:
        preview = self.phase == PHASE_PREVIEW
        cards = []
        for index in self._card_keys:
            if index in self._matched:
                state = "matched"
            elif preview or index in self._revealed:
                state = "revealed"
            else:
                state = "hidden"
            cards.append(CardView(index, self._symbols.get(index, 0), state))
        found = len(self._matched) // 2
        pair_count = len(self._card_keys) // 2
        return MemorySnapshot(
            phase=self.phase,
            layout=self.layout,
            difficulty=self.difficulty,
            sound_enabled=self.sound_enabled,
            cards=tuple(cards),
            status_key=self._status_key,
            moves=self.moves,
            pairs_found=found,
            pair_count=pair_count,
            high_score=self.high_score,
            new_high_score=self.new_high_score,
            elapsed_seconds=(
                max(0, int(float(now) - self._started))
                if self.phase in (PHASE_PLAYING, PHASE_MISMATCH, PHASE_RESULTS)
                else 0
            ),
            progress=found / max(1, pair_count),
        )

    def _begin(self, now: float) -> None:
        pair_count = len(self._card_keys) // 2
        values = list(range(pair_count)) * 2
        self._rng.shuffle(values)
        self._symbols = dict(zip(self._card_keys, values, strict=True))
        self._matched.clear()
        self._revealed.clear()
        self.moves = 0
        self.new_high_score = False
        self._started = float(now)
        preview = DIFFICULTIES[self.difficulty].preview_seconds
        if preview > 0:
            self.phase = PHASE_PREVIEW
            self._deadline = float(now) + preview
        else:
            self.phase = PHASE_PLAYING
