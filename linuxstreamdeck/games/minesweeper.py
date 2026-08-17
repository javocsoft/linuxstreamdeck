"""Pure adaptive Minesweeper state machine."""

from __future__ import annotations

import math
import random
from collections import deque
from dataclasses import dataclass

from ..core.config import DEFAULT_GAME_DIFFICULTY, GAME_DIFFICULTIES
from .common import (
    PHASE_LOBBY,
    PHASE_PLAYING,
    PHASE_RESULTS,
    EngineEvent,
    GameLayout,
)

GAME_ID = "minesweeper"
GAME_NAME = "Minesweeper"

MINE_DENSITY = {
    "easy": 0.12,
    "normal": 0.18,
    "hard": 0.25,
}


@dataclass(frozen=True)
class MineCellView:
    index: int
    state: str
    adjacent: int


@dataclass(frozen=True)
class MinesweeperSnapshot:
    phase: str
    layout: GameLayout
    difficulty: str
    sound_enabled: bool
    cells: tuple[MineCellView, ...]
    mode_key: int
    result_back_key: int
    flag_mode: bool
    flags: int
    mine_count: int
    elapsed_seconds: int
    high_score: int
    new_high_score: bool
    won: bool | None
    progress: float


class MinesweeperEngine:
    """Reveal an adaptive minefield with a dedicated Reveal/Flag key."""

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
            difficulty if difficulty in MINE_DENSITY else DEFAULT_GAME_DIFFICULTY
        )
        self.sound_enabled = bool(sound_enabled)
        self.high_score = max(0, int(high_score))
        self.phase = PHASE_LOBBY
        self.new_high_score = False
        self.won: bool | None = None
        self._rng = rng or random.Random()
        self._mode_key = max(0, layout.key_count - 1)
        self._cell_keys = tuple(
            index for index in range(layout.key_count) if index != self._mode_key
        )
        if not self._cell_keys:
            self._cell_keys = (0,)
        self._mine_count = self._mine_total()
        self._mines: set[int] = set()
        self._revealed: set[int] = set()
        self._flagged: set[int] = set()
        self._exploded: int | None = None
        self._flag_mode = False
        self._started = float(now)
        self._finished = float(now)

    @property
    def score_key(self) -> str:
        return f"{self.layout.score_id}:{self.difficulty}"

    def set_high_score(self, value: int) -> None:
        self.high_score = max(0, int(value))

    def press(self, index: int, now: float) -> tuple[EngineEvent, ...]:
        index = int(index)
        now = float(now)
        if self.phase == PHASE_RESULTS:
            if index == self._mode_key:
                self._begin(now)
                return (EngineEvent(cue="go"),)
            if index == self._result_back_key():
                return (EngineEvent(exit_requested=True),)
            return ()
        if self.phase == PHASE_LOBBY:
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

        if self.phase != PHASE_PLAYING:
            return ()
        if index == self._mode_key:
            self._flag_mode = not self._flag_mode
            return (EngineEvent(cue="select"),)
        if index not in self._cell_keys or index in self._revealed:
            return ()
        if self._flag_mode:
            if index in self._flagged:
                self._flagged.remove(index)
            elif len(self._flagged) < self._mine_count:
                self._flagged.add(index)
            return (EngineEvent(cue="flag"),)
        if index in self._flagged:
            return ()

        if not self._mines:
            self._place_mines(index)
        if index in self._mines:
            self._exploded = index
            self._finished = now
            self.won = False
            self.phase = PHASE_RESULTS
            return (EngineEvent(cue="explosion"),)

        self._reveal_region(index)
        if self._safe_cells().issubset(self._revealed):
            self._finished = now
            self.won = True
            self.phase = PHASE_RESULTS
            elapsed = self._elapsed(now)
            changed = self.high_score == 0 or elapsed < self.high_score
            if changed:
                self.high_score = elapsed
                self.new_high_score = True
            return (
                EngineEvent(
                    cue="record" if changed else "finish",
                    high_score_changed=changed,
                ),
            )
        return (EngineEvent(cue="reveal"),)

    def tick(self, _now: float) -> tuple[EngineEvent, ...]:
        return ()

    def snapshot(self, now: float) -> MinesweeperSnapshot:
        cells = []
        results = self.phase == PHASE_RESULTS
        for index in self._cell_keys:
            adjacent = self._adjacent_mines(index)
            if index == self._exploded:
                state = "exploded"
            elif results and index in self._mines:
                state = "mine"
            elif results and index in self._flagged and index not in self._mines:
                state = "wrong_flag"
            elif index in self._revealed:
                state = "revealed"
            elif index in self._flagged:
                state = "flagged"
            else:
                state = "hidden"
            cells.append(MineCellView(index, state, adjacent))
        safe_count = max(1, len(self._cell_keys) - self._mine_count)
        elapsed_at = self._finished if results else float(now)
        return MinesweeperSnapshot(
            phase=self.phase,
            layout=self.layout,
            difficulty=self.difficulty,
            sound_enabled=self.sound_enabled,
            cells=tuple(cells),
            mode_key=self._mode_key,
            result_back_key=self._result_back_key(),
            flag_mode=self._flag_mode,
            flags=len(self._flagged),
            mine_count=self._mine_count,
            elapsed_seconds=(
                self._elapsed(elapsed_at)
                if self.phase in (PHASE_PLAYING, PHASE_RESULTS)
                else 0
            ),
            high_score=self.high_score,
            new_high_score=self.new_high_score,
            won=self.won,
            progress=min(1.0, len(self._revealed) / safe_count),
        )

    def _begin(self, now: float) -> None:
        self.phase = PHASE_PLAYING
        self.new_high_score = False
        self.won = None
        self._mine_count = self._mine_total()
        self._mines.clear()
        self._revealed.clear()
        self._flagged.clear()
        self._exploded = None
        self._flag_mode = False
        self._started = float(now)
        self._finished = float(now)

    def _mine_total(self) -> int:
        available = max(1, len(self._cell_keys))
        if available <= 8:
            compact_count = GAME_DIFFICULTIES.index(self.difficulty) + 1
            return max(1, min(available - 1, compact_count))
        return max(
            1,
            min(available - 1, round(available * MINE_DENSITY[self.difficulty])),
        )

    def _place_mines(self, first: int) -> None:
        safe_zone = {first, *self._neighbours(first)}
        candidates = [index for index in self._cell_keys if index not in safe_zone]
        if len(candidates) < self._mine_count:
            candidates = [index for index in self._cell_keys if index != first]
        count = min(self._mine_count, len(candidates))
        self._mine_count = count
        self._mines = set(self._rng.sample(candidates, count))

    def _safe_cells(self) -> set[int]:
        return set(self._cell_keys) - self._mines

    def _result_back_key(self) -> int:
        groups = (
            [
                index
                for index in self._cell_keys
                if index in self._revealed and index not in self._mines
            ],
            [
                index
                for index in self._cell_keys
                if index not in self._mines and index not in self._flagged
            ],
            [index for index in self._cell_keys if index not in self._mines],
            [index for index in self._cell_keys if index != self._exploded],
        )
        for candidates in groups:
            if self.layout.exit_key in candidates:
                return self.layout.exit_key
            if candidates:
                return candidates[0]
        return self._mode_key

    def _neighbours(self, index: int) -> tuple[int, ...]:
        row, column = divmod(index, self.layout.columns)
        cells = set(self._cell_keys)
        result = []
        for row_delta in (-1, 0, 1):
            for column_delta in (-1, 0, 1):
                if row_delta == 0 and column_delta == 0:
                    continue
                candidate_row = row + row_delta
                candidate_column = column + column_delta
                candidate = candidate_row * self.layout.columns + candidate_column
                if (
                    0 <= candidate_row < self.layout.rows
                    and 0 <= candidate_column < self.layout.columns
                    and candidate in cells
                ):
                    result.append(candidate)
        return tuple(result)

    def _adjacent_mines(self, index: int) -> int:
        return sum(candidate in self._mines for candidate in self._neighbours(index))

    def _reveal_region(self, first: int) -> None:
        queue = deque([first])
        while queue:
            index = queue.popleft()
            if index in self._revealed or index in self._flagged:
                continue
            self._revealed.add(index)
            if self._adjacent_mines(index) == 0:
                queue.extend(
                    neighbour
                    for neighbour in self._neighbours(index)
                    if neighbour not in self._mines
                )

    def _elapsed(self, now: float) -> int:
        return max(1, math.ceil(max(0.0, float(now) - self._started)))
