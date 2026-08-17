"""Shared value objects and adaptive controls for built-in game engines."""

from __future__ import annotations

import math
from dataclasses import dataclass

COUNTDOWN_SECONDS = 3.0

PHASE_LOBBY = "lobby"
PHASE_COUNTDOWN = "countdown"
PHASE_PLAYING = "playing"
PHASE_RESULTS = "results"

DIFFICULTY_LABELS = {
    "easy": "Easy",
    "normal": "Normal",
    "hard": "Hard",
}


@dataclass(frozen=True)
class GameLayout:
    key_count: int
    columns: int
    rows: int
    touchscreen_hud: bool
    score_key: int | None
    time_key: int | None
    playable: tuple[int, ...]
    start_key: int
    difficulty_key: int
    sound_key: int
    record_key: int
    exit_key: int

    @property
    def score_id(self) -> str:
        suffix = "+lcd" if self.touchscreen_hud else ""
        return f"{self.columns}x{self.rows}{suffix}"


def _claim(available: set[int], preferred: int) -> int:
    """Claim the free key closest to a semantic position."""
    if not available:
        return 0
    chosen = min(available, key=lambda value: (abs(value - preferred), value))
    available.remove(chosen)
    return chosen


def game_layout(
    key_count: int,
    columns: int,
    touchscreen_hud: bool = False,
) -> GameLayout:
    """Adaptive lobby controls for every visual Stream Deck geometry."""
    count = max(1, int(key_count))
    cols = max(1, min(count, int(columns or 1)))
    rows = max(1, math.ceil(count / cols))
    available = set(range(count))

    centre = min(count - 1, (rows // 2) * cols + cols // 2)
    start = _claim(available, centre)
    exit_key = _claim(available, 0)
    difficulty = _claim(available, cols - 1)
    sound = _claim(available, count - cols)
    record = _claim(available, count - 1)

    score_key: int | None = None
    time_key: int | None = None
    if not touchscreen_hud and count >= 3:
        score_key = 0
        time_key = min(count - 1, cols - 1)
        if time_key == score_key:
            time_key = count - 1
    reserved = {value for value in (score_key, time_key) if value is not None}
    playable = tuple(index for index in range(count) if index not in reserved)
    if not playable:
        playable = tuple(range(count))

    return GameLayout(
        key_count=count,
        columns=cols,
        rows=rows,
        touchscreen_hud=bool(touchscreen_hud),
        score_key=score_key,
        time_key=time_key,
        playable=playable,
        start_key=start,
        difficulty_key=difficulty,
        sound_key=sound,
        record_key=record,
        exit_key=exit_key,
    )


@dataclass(frozen=True)
class EngineEvent:
    cue: str = ""
    settings_changed: bool = False
    high_score_changed: bool = False
    exit_requested: bool = False
