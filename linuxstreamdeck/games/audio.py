"""Bounded, cancellable playback of the bundled Mole Smash effects."""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable

from ..core.audio import play_audio
from .render import ASSET_DIR

log = logging.getLogger(__name__)

CUE_FILES = {
    "countdown": "countdown.wav",
    "finish": "finish.wav",
    "go": "go.wav",
    "golden": "golden.wav",
    "hit": "hit.wav",
    "pop": "pop.wav",
    "record": "record.wav",
    "select": "select.wav",
    "wrong": "wrong.wav",
}


class GameSoundPlayer:
    """Small worker pool whose queue can never grow behind rapid hits."""

    def __init__(self, on_error: Callable[[str], None] | None = None) -> None:
        self._on_error = on_error
        self._queue: queue.Queue[tuple[int, str, int] | None] = queue.Queue(8)
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

    def play(self, cue: str, volume: int) -> None:
        filename = CUE_FILES.get(str(cue))
        if filename is None or self._stopping.is_set():
            return
        with self._lock:
            generation = self._generation
        item = (generation, filename, max(0, min(100, int(volume))))
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
                generation, filename, volume = item
                if self._cancelled(generation):
                    continue
                play_audio(
                    ASSET_DIR / filename,
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
