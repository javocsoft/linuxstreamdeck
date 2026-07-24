"""Physical Stream Deck management: detection, hotplug and image sending.

If no device is connected (or libhidapi is missing), the application keeps
working with the UI's virtual deck: `key_count` and `image_size` take the MK.2
values (15 keys of 72x72).
"""

from __future__ import annotations

import logging
import threading

from PIL import Image

from ..core.events import EventBus

log = logging.getLogger(__name__)

SCAN_SECONDS = 3
DEFAULT_KEYS = 15            # Stream Deck MK.2
DEFAULT_IMAGE_SIZE = (72, 72)


class DeckManager:
    def __init__(self, bus: EventBus, brightness: int = 80) -> None:
        self.bus = bus
        self.brightness = brightness
        self.deck = None
        self.key_count = DEFAULT_KEYS
        self.image_size = DEFAULT_IMAGE_SIZE
        self._lock = threading.Lock()   # HID writes are not reentrant
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def connected(self) -> bool:
        return self.deck is not None

    # ---------- lifecycle ----------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._scan_loop, daemon=True, name="deck-monitor"
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join()
        self._close(emit=False)

    def _scan_loop(self) -> None:
        while not self._stop.is_set():
            try:
                if self.deck is None:
                    self._try_open()
                elif not self._alive():
                    log.info("Stream Deck disconnected")
                    self._close(emit=True)
            except Exception:
                log.exception("Error while scanning for devices")
            self._stop.wait(SCAN_SECONDS)

    def _try_open(self) -> None:
        try:
            from StreamDeck.DeviceManager import DeviceManager
            devices = DeviceManager().enumerate()
        except Exception as e:
            # typical: libhidapi not installed yet
            log.debug("Could not enumerate HID devices: %s", e)
            return
        for dev in devices:
            if self._stop.is_set():
                return
            try:
                dev.open()
                if self._stop.is_set():
                    dev.close()
                    return
                dev.reset()
                dev.set_brightness(self.brightness)
                dev.set_key_callback(self._on_key)
                self.deck = dev
                self.key_count = dev.key_count()
                fmt = dev.key_image_format()
                self.image_size = tuple(fmt["size"])
                log.info(
                    "Connected: %s (%d keys, %sx%s)",
                    dev.deck_type(), self.key_count, *self.image_size,
                )
                self.bus.emit(
                    "deck.connected", model=dev.deck_type(), keys=self.key_count
                )
                return
            except Exception as e:
                log.warning("Could not open %s: %s", dev, e)
                try:
                    dev.close()
                except Exception:
                    pass

    def _alive(self) -> bool:
        try:
            return bool(self.deck and self.deck.connected())
        except Exception:
            return False

    def _close(self, emit: bool) -> None:
        with self._lock:
            deck, self.deck = self.deck, None
        if deck is not None:
            try:
                deck.set_key_callback(None)
            except Exception:
                pass
            try:
                deck.reset()
            except Exception:
                pass
            try:
                deck.close()
            except Exception:
                pass
            reader = getattr(deck, "read_thread", None)
            if reader is not None and reader is not threading.current_thread():
                reader.join()
            if emit:
                self.bus.emit("deck.disconnected")

    # ---------- I/O ----------

    def _on_key(self, deck, index: int, pressed: bool) -> None:
        # arrives on the library's read thread; the bus takes it to the main one
        self.bus.emit("deck.key", index=index, pressed=pressed)

    def set_key_image(self, index: int, image: Image.Image) -> None:
        with self._lock:
            deck = self.deck
            if deck is None or index >= self.key_count:
                return
            try:
                from StreamDeck.ImageHelpers import PILHelper
                native = PILHelper.to_native_key_format(deck, image)
                deck.set_key_image(index, native)
            except Exception as e:
                log.warning("Failed writing key %d: %s", index, e)

    def set_brightness(self, pct: int) -> None:
        self.brightness = pct
        with self._lock:
            if self.deck is not None:
                try:
                    self.deck.set_brightness(pct)
                except Exception:
                    pass
