"""Pure sequence-recall state machine for Pulse Memory."""

from __future__ import annotations

import random
from dataclasses import dataclass

from ..core.config import DEFAULT_GAME_DIFFICULTY, GAME_DIFFICULTIES
from .common import (
    COUNTDOWN_SECONDS,
    PHASE_COUNTDOWN,
    PHASE_LOBBY,
    PHASE_RESULTS,
    EngineEvent,
    GameLayout,
)

GAME_ID = "pulse_memory"
GAME_NAME = "Pulse Memory"
PHASE_SHOWING = "showing"
PHASE_INPUT = "input"
PHASE_ROUND_PAUSE = "round_pause"
ROUND_PAUSE_SECONDS = 0.65
INPUT_FLASH_SECONDS = 0.14


@dataclass(frozen=True)
class PulseDifficulty:
    start_length: int
    lit_seconds: float
    gap_seconds: float


DIFFICULTIES = {
    "easy": PulseDifficulty(2, 0.66, 0.20),
    "normal": PulseDifficulty(3, 0.48, 0.14),
    "hard": PulseDifficulty(4, 0.34, 0.10),
}


@dataclass(frozen=True)
class PulseSnapshot:
    phase: str
    layout: GameLayout
    difficulty: str
    sound_enabled: bool
    score: int
    high_score: int
    new_high_score: bool
    countdown: int
    active_key: int | None
    wrong_key: int | None
    sequence_length: int
    input_position: int
    progress: float


class PulseMemoryEngine:
    """Show an increasing sequence and accept it only in exact order."""

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
        self.new_high_score = False
        self._rng = rng or random.Random()
        self._phase_started = float(now)
        self._next_transition = float(now)
        self._sequence: list[int] = []
        self._shown_index = 0
        self._input_position = 0
        self._active_key: int | None = None
        self._wrong_key: int | None = None
        self._lit = False
        self._input_flash_until = 0.0
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
        if self.phase != PHASE_INPUT:
            return ()
        expected = self._sequence[self._input_position]
        if index != expected:
            self._wrong_key = index
            self.phase = PHASE_RESULTS
            return (EngineEvent(cue="wrong"),)

        self._input_position += 1
        self._active_key = index
        self._input_flash_until = now + INPUT_FLASH_SECONDS
        events = [EngineEvent(cue=self._tone(index))]
        if self._input_position == len(self._sequence):
            self.score = len(self._sequence)
            changed = self.score > self.high_score
            if changed:
                self.high_score = self.score
                self.new_high_score = True
                events.append(EngineEvent(high_score_changed=True))
            self.phase = PHASE_ROUND_PAUSE
            self._next_transition = now + ROUND_PAUSE_SECONDS
            events.append(EngineEvent(cue="hit"))
        return tuple(events)

    def tick(self, now: float) -> tuple[EngineEvent, ...]:
        now = float(now)
        events: list[EngineEvent] = []
        if (
            self.phase != PHASE_SHOWING
            and self._active_key is not None
            and now >= self._input_flash_until
        ):
            self._active_key = None
        if self.phase == PHASE_COUNTDOWN:
            elapsed = now - self._phase_started
            if elapsed >= COUNTDOWN_SECONDS:
                self._build_initial_sequence()
                events.extend(self._start_showing(now))
                events.insert(0, EngineEvent(cue="go"))
            else:
                value = max(1, 3 - int(elapsed))
                if value != self._countdown_announced:
                    self._countdown_announced = value
                    events.append(EngineEvent(cue="countdown"))
            return tuple(events)

        if self.phase == PHASE_ROUND_PAUSE and now >= self._next_transition:
            self._append_step()
            return self._start_showing(now)

        # FRAME_SECONDS is much smaller than either interval. The loop also
        # makes an injected clock jump deterministic without leaving the engine
        # stuck on a pulse whose deadline is already in the past.
        transitions = 0
        while self.phase == PHASE_SHOWING and now >= self._next_transition:
            transitions += 1
            if transitions > len(self._sequence) * 2 + 2:
                break
            timing = DIFFICULTIES[self.difficulty]
            if self._lit:
                self._lit = False
                self._active_key = None
                self._next_transition += timing.gap_seconds
                continue
            self._shown_index += 1
            if self._shown_index >= len(self._sequence):
                self.phase = PHASE_INPUT
                self._input_position = 0
                self._active_key = None
                break
            self._lit = True
            self._active_key = self._sequence[self._shown_index]
            self._next_transition += timing.lit_seconds
            events.append(EngineEvent(cue=self._tone(self._active_key)))
        return tuple(events)

    def snapshot(self, now: float) -> PulseSnapshot:
        countdown = (
            max(1, 3 - int(max(0.0, float(now) - self._phase_started)))
            if self.phase == PHASE_COUNTDOWN
            else 0
        )
        return PulseSnapshot(
            phase=self.phase,
            layout=self.layout,
            difficulty=self.difficulty,
            sound_enabled=self.sound_enabled,
            score=self.score,
            high_score=self.high_score,
            new_high_score=self.new_high_score,
            countdown=countdown,
            active_key=self._active_key,
            wrong_key=self._wrong_key,
            sequence_length=len(self._sequence),
            input_position=self._input_position,
            progress=(
                self._input_position / max(1, len(self._sequence))
                if self.phase == PHASE_INPUT
                else 0.0
            ),
        )

    def _begin_countdown(self, now: float) -> None:
        self.phase = PHASE_COUNTDOWN
        self._phase_started = float(now)
        self._countdown_announced = 3
        self.score = 0
        self.new_high_score = False
        self._sequence.clear()
        self._wrong_key = None
        self._active_key = None
        self._input_flash_until = 0.0

    def _build_initial_sequence(self) -> None:
        self._sequence.clear()
        for _step in range(DIFFICULTIES[self.difficulty].start_length):
            self._append_step()

    def _append_step(self) -> None:
        choices = list(range(self.layout.key_count))
        if len(choices) > 1 and self._sequence:
            choices.remove(self._sequence[-1])
        self._sequence.append(self._rng.choice(choices))

    def _start_showing(self, now: float) -> tuple[EngineEvent, ...]:
        self.phase = PHASE_SHOWING
        self._shown_index = 0
        self._input_position = 0
        self._active_key = self._sequence[0]
        self._lit = True
        self._next_transition = now + DIFFICULTIES[self.difficulty].lit_seconds
        return (EngineEvent(cue=self._tone(self._active_key)),)

    def _tone(self, index: int) -> str:
        return f"pulse_{int(index) % 6}"
