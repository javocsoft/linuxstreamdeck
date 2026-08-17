"""Pure state machine for Mole Smash.

The engine knows nothing about GTK, HID, Pillow or GStreamer.  Its clock and
random source are injected so a complete round can be verified without waiting
or touching hardware; the manager is the only code that turns snapshots into
images and sound effects.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from ..core.config import DEFAULT_GAME_DIFFICULTY, GAME_DIFFICULTIES

GAME_ID = "mole_smash"
GAME_NAME = "Mole Smash"
ROUND_SECONDS = 45.0
COUNTDOWN_SECONDS = 3.0
HIT_SECONDS = 0.18
WRONG_SECONDS = 0.20
EMERGE_SECONDS = 0.16
NORMAL_POINTS = 10
GOLDEN_POINTS = 25
WRONG_PENALTY = 2
GOLDEN_CHANCE = 0.08

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
class Difficulty:
    visible: float
    gap: float


DIFFICULTIES = {
    "easy": Difficulty(visible=1.15, gap=0.26),
    "normal": Difficulty(visible=0.84, gap=0.18),
    "hard": Difficulty(visible=0.60, gap=0.12),
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
    """Adaptive controls for every visual Stream Deck geometry."""
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


@dataclass
class _Target:
    index: int
    spawned_at: float
    expires_at: float
    golden: bool = False
    hit_at: float | None = None


@dataclass(frozen=True)
class TargetView:
    index: int
    state: str
    progress: float
    golden: bool


@dataclass(frozen=True)
class GameSnapshot:
    phase: str
    layout: GameLayout
    difficulty: str
    sound_enabled: bool
    score: int
    high_score: int
    new_high_score: bool
    seconds_left: int
    countdown: int
    combo: int
    progress: float
    targets: tuple[TargetView, ...]
    wrong_keys: tuple[int, ...]


@dataclass(frozen=True)
class EngineEvent:
    cue: str = ""
    settings_changed: bool = False
    high_score_changed: bool = False
    exit_requested: bool = False


class MoleSmashEngine:
    """One session, from the lobby through any number of rounds."""

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
        self.score = 0
        self.combo = 0
        self.new_high_score = False
        self._rng = rng or random.Random()
        self._phase_started = float(now)
        self._round_started = 0.0
        self._next_spawn = float(now)
        self._targets: dict[int, _Target] = {}
        self._wrong_until: dict[int, float] = {}
        self._last_target: int | None = None
        self._countdown_announced = 3

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
                self._begin_countdown(now)
                return (EngineEvent(cue="countdown"),)
            return ()

        if self.phase != PHASE_PLAYING:
            return ()
        target = self._targets.get(index)
        if target is not None and target.hit_at is None:
            target.hit_at = now
            target.expires_at = now + HIT_SECONDS
            self.score += GOLDEN_POINTS if target.golden else NORMAL_POINTS
            self.combo += 1
            return (EngineEvent(cue="golden" if target.golden else "hit"),)
        if index in (self.layout.score_key, self.layout.time_key):
            return ()
        self.score = max(0, self.score - WRONG_PENALTY)
        self.combo = 0
        self._wrong_until[index] = now + WRONG_SECONDS
        return (EngineEvent(cue="wrong"),)

    def tick(self, now: float) -> tuple[EngineEvent, ...]:
        now = float(now)
        events: list[EngineEvent] = []
        self._wrong_until = {
            index: until for index, until in self._wrong_until.items() if until > now
        }
        if self.phase == PHASE_COUNTDOWN:
            elapsed = now - self._phase_started
            if elapsed >= COUNTDOWN_SECONDS:
                self.phase = PHASE_PLAYING
                self._phase_started = now
                self._round_started = now
                self._next_spawn = now
                events.append(EngineEvent(cue="go"))
            else:
                value = max(1, 3 - int(elapsed))
                if value != self._countdown_announced:
                    self._countdown_announced = value
                    events.append(EngineEvent(cue="countdown"))
            return tuple(events)

        if self.phase != PHASE_PLAYING:
            return ()

        elapsed = now - self._round_started
        if elapsed >= ROUND_SECONDS:
            self.phase = PHASE_RESULTS
            self._phase_started = now
            self._targets.clear()
            changed = self.score > self.high_score
            if changed:
                self.high_score = self.score
                self.new_high_score = True
            events.append(
                EngineEvent(
                    cue="record" if changed else "finish",
                    high_score_changed=changed,
                )
            )
            return tuple(events)

        expired = []
        for index, target in self._targets.items():
            if target.expires_at <= now:
                expired.append(index)
                if target.hit_at is None:
                    self.combo = 0
        for index in expired:
            self._targets.pop(index, None)
            self._next_spawn = max(
                self._next_spawn,
                now + DIFFICULTIES[self.difficulty].gap,
            )

        desired = 1 if len(self.layout.playable) <= 16 else 2
        if ROUND_SECONDS - elapsed <= 10 and len(self.layout.playable) >= 8:
            desired += 1
        if now >= self._next_spawn and len(self._targets) < desired:
            if self._spawn(now, elapsed):
                events.append(EngineEvent(cue="pop"))
                self._next_spawn = now + DIFFICULTIES[self.difficulty].gap
        return tuple(events)

    def snapshot(self, now: float) -> GameSnapshot:
        now = float(now)
        elapsed = max(0.0, now - self._round_started)
        if self.phase == PHASE_PLAYING:
            left = max(0, math.ceil(ROUND_SECONDS - elapsed))
            progress = max(0.0, min(1.0, elapsed / ROUND_SECONDS))
        else:
            left = int(ROUND_SECONDS)
            progress = 0.0
        countdown = (
            max(1, 3 - int(max(0.0, now - self._phase_started)))
            if self.phase == PHASE_COUNTDOWN
            else 0
        )
        targets = []
        for target in self._targets.values():
            if target.hit_at is not None:
                state = "hit"
                amount = max(0.0, min(1.0, (now - target.hit_at) / HIT_SECONDS))
            else:
                amount = max(
                    0.0,
                    min(1.0, (now - target.spawned_at) / EMERGE_SECONDS),
                )
                state = "visible" if amount >= 1.0 else "emerging"
            targets.append(TargetView(target.index, state, amount, target.golden))
        return GameSnapshot(
            phase=self.phase,
            layout=self.layout,
            difficulty=self.difficulty,
            sound_enabled=self.sound_enabled,
            score=self.score,
            high_score=self.high_score,
            new_high_score=self.new_high_score,
            seconds_left=left,
            countdown=countdown,
            combo=self.combo,
            progress=progress,
            targets=tuple(sorted(targets, key=lambda target: target.index)),
            wrong_keys=tuple(sorted(self._wrong_until)),
        )

    def _begin_countdown(self, now: float) -> None:
        self.phase = PHASE_COUNTDOWN
        self._phase_started = now
        self._countdown_announced = 3
        self.score = 0
        self.combo = 0
        self.new_high_score = False
        self._targets.clear()
        self._wrong_until.clear()
        self._last_target = None

    def _spawn(self, now: float, elapsed: float) -> bool:
        choices = [
            index for index in self.layout.playable if index not in self._targets
        ]
        if len(choices) > 1 and self._last_target in choices:
            choices.remove(self._last_target)
        if not choices:
            return False
        index = self._rng.choice(choices)
        self._last_target = index
        progress = max(0.0, min(1.0, elapsed / ROUND_SECONDS))
        speed = 1.0 - progress * 0.42
        golden = self._rng.random() < GOLDEN_CHANCE
        visible = DIFFICULTIES[self.difficulty].visible * speed
        if golden:
            visible *= 0.72
        self._targets[index] = _Target(
            index=index,
            spawned_at=now,
            expires_at=now + max(0.28, visible),
            golden=golden,
        )
        return True
