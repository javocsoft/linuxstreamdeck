"""Pure single-player Tic-Tac-Toe state machine."""

from __future__ import annotations

import random
from dataclasses import dataclass
from functools import lru_cache

from ..core.config import DEFAULT_GAME_DIFFICULTY, GAME_DIFFICULTIES
from .common import (
    PHASE_LOBBY,
    PHASE_PLAYING,
    PHASE_RESULTS,
    EngineEvent,
    GameLayout,
)

GAME_ID = "tic_tac_toe"
GAME_NAME = "Tic-Tac-Toe"
PHASE_AI_TURN = "ai_turn"
AI_DELAY_SECONDS = 0.34


@dataclass(frozen=True)
class TicTacToeSnapshot:
    phase: str
    layout: GameLayout
    difficulty: str
    sound_enabled: bool
    board_keys: tuple[int, ...]
    board_columns: int
    marks: tuple[str, ...]
    winning_cells: tuple[int, ...]
    result_again_key: int
    result_back_key: int
    winner: str
    high_score: int
    new_high_score: bool
    progress: float


def _board_geometry(layout: GameLayout) -> tuple[tuple[int, ...], int]:
    if layout.key_count < 9:
        return tuple(range(layout.key_count)), layout.columns
    start_row = max(0, (layout.rows - 3) // 2)
    start_column = max(0, (layout.columns - 3) // 2)
    keys = tuple(
        (start_row + row) * layout.columns + start_column + column
        for row in range(3)
        for column in range(3)
        if (start_row + row) * layout.columns + start_column + column
        < layout.key_count
    )
    if len(keys) == 9:
        return keys, 3
    return tuple(range(9)), 3


def _winning_lines(count: int, columns: int) -> tuple[tuple[int, int, int], ...]:
    coordinates = {
        (index // columns, index % columns): index for index in range(count)
    }
    lines: set[tuple[int, int, int]] = set()
    for row, column in coordinates:
        for row_step, column_step in ((0, 1), (1, 0), (1, 1), (1, -1)):
            line = tuple(
                coordinates.get((row + step * row_step, column + step * column_step), -1)
                for step in range(3)
            )
            if all(index >= 0 for index in line):
                lines.add(line)
    return tuple(sorted(lines))


class TicTacToeEngine:
    """Player X versus a difficulty-scaled O opponent."""

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
            difficulty if difficulty in GAME_DIFFICULTIES else DEFAULT_GAME_DIFFICULTY
        )
        self.sound_enabled = bool(sound_enabled)
        self.high_score = max(0, int(high_score))
        self.phase = PHASE_LOBBY
        self.new_high_score = False
        self.winner = ""
        self._rng = rng or random.Random()
        self._board_keys, self._board_columns = _board_geometry(layout)
        self._key_to_cell = {
            key: cell for cell, key in enumerate(self._board_keys)
        }
        self._lines = _winning_lines(len(self._board_keys), self._board_columns)
        self._marks = [""] * len(self._board_keys)
        self._winning_cells: tuple[int, ...] = ()
        self._ai_deadline = float(now)

    @property
    def score_key(self) -> str:
        return f"{self.layout.score_id}:{self.difficulty}"

    def set_high_score(self, value: int) -> None:
        self.high_score = max(0, int(value))

    def press(self, index: int, now: float) -> tuple[EngineEvent, ...]:
        index = int(index)
        now = float(now)
        if self.phase == PHASE_RESULTS:
            again_key, back_key = self._result_control_keys()
            if index == back_key:
                return (EngineEvent(exit_requested=True),)
            if index == again_key:
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
        cell = self._key_to_cell.get(index)
        if cell is None or self._marks[cell]:
            return ()
        self._marks[cell] = "X"
        outcome = self._finish_if_needed("X")
        if outcome:
            return outcome
        self.phase = PHASE_AI_TURN
        self._ai_deadline = now + AI_DELAY_SECONDS
        return (EngineEvent(cue="mark"),)

    def tick(self, now: float) -> tuple[EngineEvent, ...]:
        if self.phase != PHASE_AI_TURN or float(now) < self._ai_deadline:
            return ()
        move = self._choose_ai_move()
        if move is None:
            self.phase = PHASE_RESULTS
            self.winner = "draw"
            return (EngineEvent(cue="draw"),)
        self._marks[move] = "O"
        outcome = self._finish_if_needed("O")
        if outcome:
            return outcome
        self.phase = PHASE_PLAYING
        return (EngineEvent(cue="ai"),)

    def snapshot(self, _now: float) -> TicTacToeSnapshot:
        again_key, back_key = self._result_control_keys()
        return TicTacToeSnapshot(
            phase=self.phase,
            layout=self.layout,
            difficulty=self.difficulty,
            sound_enabled=self.sound_enabled,
            board_keys=self._board_keys,
            board_columns=self._board_columns,
            marks=tuple(self._marks),
            winning_cells=self._winning_cells,
            result_again_key=again_key,
            result_back_key=back_key,
            winner=self.winner,
            high_score=self.high_score,
            new_high_score=self.new_high_score,
            progress=sum(bool(mark) for mark in self._marks)
            / max(1, len(self._marks)),
        )

    def _begin(self) -> None:
        self.phase = PHASE_PLAYING
        self.new_high_score = False
        self.winner = ""
        self._marks = [""] * len(self._board_keys)
        self._winning_cells = ()

    def _finish_if_needed(self, mark: str) -> tuple[EngineEvent, ...]:
        line = self._winner(self._marks, mark)
        if line:
            self._winning_cells = line
            self.winner = mark
            self.phase = PHASE_RESULTS
            if mark == "X":
                self.high_score += 1
                self.new_high_score = True
                return (EngineEvent(cue="win", high_score_changed=True),)
            return (EngineEvent(cue="lose"),)
        if all(self._marks):
            self.winner = "draw"
            self.phase = PHASE_RESULTS
            return (EngineEvent(cue="draw"),)
        return ()

    def _winner(self, marks: list[str] | tuple[str, ...], mark: str):
        return next(
            (line for line in self._lines if all(marks[index] == mark for index in line)),
            (),
        )

    def _result_control_keys(self) -> tuple[int, int]:
        board = set(self._board_keys)
        winning = {
            self._board_keys[cell]
            for cell in self._winning_cells
            if 0 <= cell < len(self._board_keys)
        }
        outside = [
            index for index in range(self.layout.key_count) if index not in board
        ]
        non_winning_board = [
            index for index in self._board_keys if index not in winning
        ]
        candidates = outside + non_winning_board
        if len(candidates) < 2:
            candidates = list(range(self.layout.key_count))
        back = (
            self.layout.exit_key
            if self.layout.exit_key in candidates
            else candidates[0]
        )
        again = next(index for index in candidates if index != back)
        return again, back

    def _choose_ai_move(self) -> int | None:
        available = [index for index, mark in enumerate(self._marks) if not mark]
        if not available:
            return None
        if self.difficulty == "easy":
            return self._rng.choice(available)
        for mark in ("O", "X"):
            for index in available:
                candidate = self._marks.copy()
                candidate[index] = mark
                if self._winner(candidate, mark):
                    return index
        if self.difficulty == "normal":
            return self._rng.choice(available)
        scores = [(self._minimax_move(index), index) for index in available]
        best = max(score for score, _index in scores)
        return self._rng.choice([index for score, index in scores if score == best])

    def _minimax_move(self, index: int) -> int:
        candidate = self._marks.copy()
        candidate[index] = "O"

        @lru_cache(maxsize=None)
        def search(state: tuple[str, ...], turn: str) -> int:
            if self._winner(state, "O"):
                return 10
            if self._winner(state, "X"):
                return -10
            free = [cell for cell, mark in enumerate(state) if not mark]
            if not free:
                return 0
            values = []
            for cell in free:
                next_state = list(state)
                next_state[cell] = turn
                values.append(search(tuple(next_state), "X" if turn == "O" else "O"))
            return max(values) if turn == "O" else min(values)

        return search(tuple(candidate), "X")
