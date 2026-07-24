"""Pub/sub event bus to decouple device, OBS and UI.

Emitters can run on any thread (the deck's read thread, the obs-websocket event
thread...). If a `dispatcher` is set (in the GTK app it is GLib.idle_add), every
call to the subscribers runs on the main thread.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

log = logging.getLogger(__name__)

# Topics used across the application:
#   deck.key          index:int, pressed:bool   — key press on the physical deck
#   deck.connected    model:str, keys:int
#   deck.disconnected
#   deck.screensaver  active:bool, preview:bool, style:str
#   obs.connected
#   obs.disconnected
#   obs.state         what:str                  — any OBS state change
#   page.changed      index:int, name:str
#   ui.key_image      index:int, png:bytes      — rendered image for the UI
#   ui.screensaver_frame images:tuple[bytes, ...]
#   status            text:str                  — messages for the status bar


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[Callable]] = {}
        self._lock = threading.Lock()
        # callable(fn) that runs fn() on the main thread; None = direct call
        self.dispatcher: Callable[[Callable], object] | None = None

    def subscribe(self, topic: str, callback: Callable) -> None:
        """callback(topic, data:dict). The '*' topic receives every event."""
        with self._lock:
            self._subs.setdefault(topic, []).append(callback)

    def unsubscribe(self, topic: str, callback: Callable) -> None:
        """Remove one callback previously registered for a topic."""
        with self._lock:
            callbacks = self._subs.get(topic)
            if callbacks is None:
                return
            self._subs[topic] = [
                registered
                for registered in callbacks
                if registered != callback
            ]
            if not self._subs[topic]:
                self._subs.pop(topic, None)

    def emit(self, topic: str, **data) -> None:
        with self._lock:
            callbacks = list(self._subs.get(topic, [])) + list(self._subs.get("*", []))
        for cb in callbacks:
            if self.dispatcher is not None:
                self.dispatcher(self._safe_call(cb, topic, data))
            else:
                self._safe_call(cb, topic, data)()

    @staticmethod
    def _safe_call(cb: Callable, topic: str, data: dict) -> Callable:
        def run():
            try:
                cb(topic, data)
            except Exception:
                log.exception("Error in subscriber of %s", topic)
            return False  # compatible with GLib.idle_add (don't repeat)

        return run
