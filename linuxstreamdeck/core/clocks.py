"""Runtime state for countdown timers and elapsed-time stopwatches."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

ClockKey = tuple[int, int, int]

KIND_TIMER = "timer"
KIND_STOPWATCH = "stopwatch"

_TICK_SECONDS = 0.1


def format_clock(seconds) -> str:
    """Format elapsed or remaining seconds as HH:MM:SS."""
    try:
        total = max(0, int(seconds))
    except (TypeError, ValueError, OverflowError):
        total = 0
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


@dataclass(frozen=True)
class ClockSnapshot:
    display: str
    running: bool = False
    finished: bool = False


@dataclass(frozen=True)
class TimerCompletion:
    key: ClockKey
    token: object
    sound_file: str
    volume: int


@dataclass
class _ClockState:
    kind: str
    started_at: float
    token: object
    duration: float = 0
    sound_file: str = ""
    volume: int = 100
    finished: bool = False
    last_second: int = 0


class ClockRuntime:
    """Own clock state and refresh only keys whose displayed second changed."""

    def __init__(
        self,
        refresh: Callable[[tuple[ClockKey, ...]], None],
        on_timer_finished: Callable[[TimerCompletion], None],
        *,
        monotonic: Callable[[], float] = time.monotonic,
        start_thread: bool = True,
    ) -> None:
        self._refresh = refresh
        self._on_timer_finished = on_timer_finished
        self._monotonic = monotonic
        self._states: dict[ClockKey, _ClockState] = {}
        self._lock = threading.Lock()
        self._wakeup = threading.Event()
        self._stopping = threading.Event()
        self._thread = None
        if start_thread:
            self._thread = threading.Thread(
                target=self._run,
                daemon=True,
                name="key-clocks",
            )
            self._thread.start()

    def toggle_timer(
        self,
        key: ClockKey,
        duration: float,
        sound_file: str = "",
        volume: int = 100,
    ) -> bool:
        """Start an idle timer, or reset an existing clock on this key."""
        try:
            seconds = float(duration)
        except (TypeError, ValueError):
            seconds = 0
        if not math.isfinite(seconds):
            seconds = 0
        seconds = max(0.0, seconds)
        try:
            volume_percent = int(volume)
        except (TypeError, ValueError):
            volume_percent = 100
        with self._lock:
            if key in self._states:
                self._states.pop(key, None)
                started = False
            else:
                self._states[key] = _ClockState(
                    kind=KIND_TIMER,
                    started_at=self._monotonic(),
                    token=object(),
                    duration=seconds,
                    sound_file=str(sound_file or ""),
                    volume=max(0, min(100, volume_percent)),
                    last_second=max(0, math.ceil(seconds)),
                )
                started = True
        self._wakeup.set()
        self._refresh((key,))
        return started

    def toggle_stopwatch(self, key: ClockKey) -> bool:
        """Start an idle stopwatch, or reset an existing clock on this key."""
        with self._lock:
            if key in self._states:
                self._states.pop(key, None)
                started = False
            else:
                self._states[key] = _ClockState(
                    kind=KIND_STOPWATCH,
                    started_at=self._monotonic(),
                    token=object(),
                )
                started = True
        self._wakeup.set()
        self._refresh((key,))
        return started

    def timer_snapshot(
        self,
        key: ClockKey | None,
        idle_seconds: float,
    ) -> ClockSnapshot:
        if key is None:
            return ClockSnapshot(format_clock(idle_seconds))
        with self._lock:
            state = self._states.get(key)
            if state is None or state.kind != KIND_TIMER:
                return ClockSnapshot(format_clock(idle_seconds))
            if state.finished:
                return ClockSnapshot(
                    format_clock(0),
                    finished=True,
                )
            remaining = max(
                0,
                math.ceil(state.started_at + state.duration - self._monotonic()),
            )
            return ClockSnapshot(
                format_clock(remaining),
                running=True,
            )

    def stopwatch_snapshot(self, key: ClockKey | None) -> ClockSnapshot:
        if key is None:
            return ClockSnapshot(format_clock(0))
        with self._lock:
            state = self._states.get(key)
            if state is None or state.kind != KIND_STOPWATCH:
                return ClockSnapshot(format_clock(0))
            elapsed = max(0, int(self._monotonic() - state.started_at))
            return ClockSnapshot(
                format_clock(elapsed),
                running=True,
            )

    def is_current_completion(self, completion: TimerCompletion) -> bool:
        with self._lock:
            state = self._states.get(completion.key)
            return bool(
                state is not None
                and state.kind == KIND_TIMER
                and state.finished
                and state.token is completion.token
            )

    def reset(self, key: ClockKey, *, refresh: bool = True) -> bool:
        with self._lock:
            removed = self._states.pop(key, None) is not None
        if removed:
            self._wakeup.set()
            if refresh:
                self._refresh((key,))
        return removed

    def swap(self, first: ClockKey, second: ClockKey) -> None:
        with self._lock:
            first_state = self._states.pop(first, None)
            second_state = self._states.pop(second, None)
            if second_state is not None:
                self._states[first] = second_state
            if first_state is not None:
                self._states[second] = first_state
        self._wakeup.set()

    def clear(self) -> None:
        with self._lock:
            self._states.clear()
        self._wakeup.set()

    def tick(self) -> None:
        """Advance runtime state once. Exposed for deterministic tests."""
        now = self._monotonic()
        changed: list[ClockKey] = []
        completed: list[TimerCompletion] = []
        with self._lock:
            for key, state in self._states.items():
                if state.finished:
                    continue
                if state.kind == KIND_TIMER:
                    remaining = max(
                        0,
                        math.ceil(state.started_at + state.duration - now),
                    )
                    if now >= state.started_at + state.duration:
                        state.finished = True
                        remaining = 0
                        completed.append(
                            TimerCompletion(
                                key=key,
                                token=state.token,
                                sound_file=state.sound_file,
                                volume=state.volume,
                            )
                        )
                else:
                    remaining = max(0, int(now - state.started_at))
                if remaining != state.last_second:
                    state.last_second = remaining
                    changed.append(key)
        if changed:
            self._refresh(tuple(changed))
        for completion in completed:
            self._on_timer_finished(completion)

    def shutdown(self) -> None:
        self._stopping.set()
        self._wakeup.set()
        if self._thread is not None:
            self._thread.join()
        self.clear()

    def _has_running_clocks(self) -> bool:
        with self._lock:
            return any(not state.finished for state in self._states.values())

    def _run(self) -> None:
        while not self._stopping.is_set():
            if not self._has_running_clocks():
                self._wakeup.wait()
            else:
                self._wakeup.wait(_TICK_SECONDS)
            self._wakeup.clear()
            if self._stopping.is_set():
                return
            self.tick()
