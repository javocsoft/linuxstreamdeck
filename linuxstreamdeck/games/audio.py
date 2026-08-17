"""Bounded, cancellable playback of the bundled game effects."""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from pathlib import Path

from ..core.audio import play_audio

log = logging.getLogger(__name__)

ASSET_ROOT = Path(__file__).resolve().parent.parent / "assets" / "games"

# Every game owns a complete sound set below its own asset directory. Some
# waveforms are intentionally alike, but they are generated into each consumer
# rather than making one game depend on another game's files.
CUE_FILES = {
    "mole_smash": {
        "countdown": "countdown.wav",
        "finish": "finish.wav",
        "go": "go.wav",
        "golden": "golden.wav",
        "hit": "hit.wav",
        "pop": "pop.wav",
        "record": "record.wav",
        "select": "select.wav",
        "wrong": "wrong.wav",
    },
    "circuit_breaker": {
        "circuit": "circuit.wav",
        "finish": "finish.wav",
        "go": "go.wav",
        "record": "record.wav",
        "select": "select.wav",
    },
    "pulse_memory": {
        "countdown": "countdown.wav",
        "go": "go.wav",
        "hit": "hit.wav",
        "pulse_0": "pulse-0.wav",
        "pulse_1": "pulse-1.wav",
        "pulse_2": "pulse-2.wav",
        "pulse_3": "pulse-3.wav",
        "pulse_4": "pulse-4.wav",
        "pulse_5": "pulse-5.wav",
        "select": "select.wav",
        "wrong": "wrong.wav",
    },
    "memory_match": {
        "finish": "finish.wav",
        "go": "go.wav",
        "hit": "hit.wav",
        "record": "record.wav",
        "select": "select.wav",
        "wrong": "wrong.wav",
    },
    "minesweeper": {
        "explosion": "explosion.wav",
        "finish": "finish.wav",
        "flag": "flag.wav",
        "go": "go.wav",
        "record": "record.wav",
        "reveal": "reveal.wav",
        "select": "select.wav",
    },
    "tic_tac_toe": {
        "ai": "ai.wav",
        "draw": "draw.wav",
        "go": "go.wav",
        "lose": "lose.wav",
        "mark": "mark.wav",
        "select": "select.wav",
        "win": "win.wav",
    },
    "mastermind": {
        "finish": "finish.wav",
        "go": "go.wav",
        "peg": "peg.wav",
        "record": "record.wav",
        "select": "select.wav",
        "submit": "submit.wav",
        "wrong": "wrong.wav",
    },
}


class GameSoundPlayer:
    """Small worker pool whose queue can never grow behind rapid hits."""

    def __init__(self, on_error: Callable[[str], None] | None = None) -> None:
        self._on_error = on_error
        self._queue: queue.Queue[tuple[int, Path, int] | None] = queue.Queue(8)
        self._lock = threading.Lock()
        self._generation = 0
        self._stopping = threading.Event()
        self._reported_error = False
        self._workers = [
            threading.Thread(
                target=self._worker,
                daemon=True,
                name=f"game-audio-{index + 1}",
            )
            for index in range(2)
        ]
        for worker in self._workers:
            worker.start()

    def play(self, game_id: str, cue: str, volume: int) -> None:
        filename = CUE_FILES.get(str(game_id), {}).get(str(cue))
        if filename is None or self._stopping.is_set():
            return
        with self._lock:
            generation = self._generation
        path = ASSET_ROOT / str(game_id) / filename
        item = (generation, path, max(0, min(100, int(volume))))
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            # Reaction feedback is useful only now. Dropping an old sound is
            # better than playing it after three newer hits.
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(item)
            except queue.Full:
                pass

    def cancel(self) -> None:
        with self._lock:
            self._generation += 1
        while True:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except queue.Empty:
                return

    def shutdown(self) -> None:
        if self._stopping.is_set():
            return
        self._stopping.set()
        self.cancel()
        for _worker in self._workers:
            self._queue.put(None)
        for worker in self._workers:
            if worker is not threading.current_thread():
                worker.join()

    def _cancelled(self, generation: int) -> bool:
        with self._lock:
            return self._stopping.is_set() or generation != self._generation

    def _worker(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is None:
                    return
                generation, path, volume = item
                if self._cancelled(generation):
                    continue
                play_audio(
                    path,
                    volume,
                    stop_requested=lambda: self._cancelled(generation),
                )
            except Exception as error:
                if self._stopping.is_set():
                    continue
                log.warning("Could not play a game sound", exc_info=True)
                if not self._reported_error and self._on_error is not None:
                    self._reported_error = True
                    self._on_error(f"Game sound failed: {error}")
            finally:
                self._queue.task_done()
