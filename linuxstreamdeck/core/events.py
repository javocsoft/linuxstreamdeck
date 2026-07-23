"""Bus de eventos pub/sub para desacoplar dispositivo, OBS y UI.

Los emisores pueden estar en cualquier hilo (hilo de lectura del deck, hilo
de eventos de obs-websocket...). Si se asigna un `dispatcher` (en la app GTK
será GLib.idle_add), todas las llamadas a los suscriptores se ejecutan en el
hilo principal.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

log = logging.getLogger(__name__)

# Temas usados en la aplicación:
#   deck.key          index:int, pressed:bool   — pulsación en el deck físico
#   deck.connected    model:str, keys:int
#   deck.disconnected
#   obs.connected
#   obs.disconnected
#   obs.state         what:str                  — cualquier cambio de estado de OBS
#   page.changed      index:int, name:str
#   ui.key_image      index:int, png:bytes      — imagen renderizada para la UI
#   status            text:str                  — mensajes para la barra de estado


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[Callable]] = {}
        self._lock = threading.Lock()
        # callable(fn) que ejecuta fn() en el hilo principal; None = llamada directa
        self.dispatcher: Callable[[Callable], object] | None = None

    def subscribe(self, topic: str, callback: Callable) -> None:
        """callback(topic, data:dict). El tema '*' recibe todos los eventos."""
        with self._lock:
            self._subs.setdefault(topic, []).append(callback)

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
                log.exception("Error en suscriptor de %s", topic)
            return False  # compatible con GLib.idle_add (no repetir)

        return run
