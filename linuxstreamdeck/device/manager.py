"""Physical Stream Deck management: detection, hotplug and image sending.

If no device is connected (or libhidapi is missing), the application keeps
working with the UI's virtual deck: `key_count` and `image_size` take the MK.2
values (15 keys of 72x72).
"""

from __future__ import annotations

import logging
import threading
import time
from io import BytesIO

from PIL import Image

from ..core.config import DEFAULT_SCREENSAVER, SCREENSAVER_IDS
from ..core.events import EventBus
from .screensaver import ScreenSaverFrame, screensaver_frame
from .startup_animation import startup_frames

log = logging.getLogger(__name__)

SCAN_SECONDS = 3
DEFAULT_KEYS = 15            # Stream Deck MK.2
DEFAULT_IMAGE_SIZE = (72, 72)


class DeckManager:
    def __init__(
        self,
        bus: EventBus,
        brightness: int = 80,
        screensaver_enabled: bool = False,
        screensaver_style: str = DEFAULT_SCREENSAVER,
        screensaver_idle_minutes: int = 5,
        screensaver_intensity: int = 35,
    ) -> None:
        self.bus = bus
        self.brightness = brightness
        self.deck = None
        self.key_count = DEFAULT_KEYS
        self.image_size = DEFAULT_IMAGE_SIZE
        self._lock = threading.Lock()   # HID writes are not reentrant
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._screensaver_thread: threading.Thread | None = None
        self._screensaver_lock = threading.Lock()
        self._screensaver_wakeup = threading.Event()
        self._screensaver_active = threading.Event()
        self._screensaver_enabled = bool(screensaver_enabled)
        self._screensaver_style = (
            screensaver_style
            if screensaver_style in SCREENSAVER_IDS
            else DEFAULT_SCREENSAVER
        )
        self._screensaver_idle_minutes = max(
            1,
            min(1440, int(screensaver_idle_minutes)),
        )
        self._screensaver_intensity = max(
            5,
            min(100, int(screensaver_intensity)),
        )
        self._screensaver_preview: tuple[str, int] | None = None
        self._screensaver_brightness: int | None = None
        self._last_activity = time.monotonic()
        self._suppressed_keys: set[int] = set()

    @property
    def connected(self) -> bool:
        return self.deck is not None

    @property
    def screensaver_active(self) -> bool:
        return self._screensaver_active.is_set()

    # ---------- lifecycle ----------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._screensaver_wakeup.clear()
        with self._screensaver_lock:
            self._last_activity = time.monotonic()
            self._screensaver_preview = None
            self._suppressed_keys.clear()
        self._thread = threading.Thread(
            target=self._scan_loop, daemon=True, name="deck-monitor"
        )
        self._screensaver_thread = threading.Thread(
            target=self._screensaver_loop,
            daemon=True,
            name="deck-screensaver",
        )
        self._thread.start()
        self._screensaver_thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._screensaver_wakeup.set()
        screen_thread, self._screensaver_thread = self._screensaver_thread, None
        if (
            screen_thread is not None
            and screen_thread is not threading.current_thread()
        ):
            screen_thread.join()
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
                self.record_activity()
                dev.open()
                if self._stop.is_set():
                    dev.close()
                    return
                dev.reset()
                key_count = dev.key_count()
                fmt = dev.key_image_format()
                image_size = tuple(fmt["size"])
                if not self._play_startup_animation(
                    dev,
                    key_count,
                    image_size,
                ):
                    dev.close()
                    return
                if not dev.connected():
                    raise OSError("Stream Deck disconnected during startup")
                dev.set_key_callback(self._on_key)
                self.deck = dev
                self.key_count = key_count
                self.image_size = image_size
                self.record_activity()
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

    def _play_startup_animation(
        self,
        dev,
        key_count: int,
        image_size: tuple[int, int],
        native_converter=None,
    ) -> bool:
        """Play the exclusive pre-connection animation on an opened device."""
        last_brightness = None
        try:
            if native_converter is None:
                from StreamDeck.ImageHelpers import PILHelper

                native_converter = PILHelper.to_native_key_format
            for frame in startup_frames(
                key_count,
                image_size,
                self.brightness,
            ):
                frame_started = time.monotonic()
                if self._stop.is_set():
                    return False
                brightness = min(
                    frame.brightness,
                    max(0, min(100, int(self.brightness))),
                )
                if brightness != last_brightness:
                    dev.set_brightness(brightness)
                    last_brightness = brightness
                for index, image in enumerate(frame.images):
                    if self._stop.is_set():
                        return False
                    native = native_converter(dev, image)
                    dev.set_key_image(index, native)
                remaining = frame.delay - (time.monotonic() - frame_started)
                if remaining > 0 and self._stop.wait(remaining):
                    return False
        except Exception:
            log.warning("Startup animation could not be completed", exc_info=True)
        finally:
            try:
                dev.set_brightness(self.brightness)
            except Exception:
                pass
        return not self._stop.is_set()

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
        with self._screensaver_lock:
            if index in self._suppressed_keys:
                if not pressed:
                    self._suppressed_keys.discard(index)
                self._last_activity = time.monotonic()
                return
            if self._screensaver_active.is_set():
                if pressed:
                    self._suppressed_keys.add(index)
                self._last_activity = time.monotonic()
                self._screensaver_preview = None
                self._screensaver_wakeup.set()
                return
            self._last_activity = time.monotonic()
        self.bus.emit("deck.key", index=index, pressed=pressed)

    def set_key_image(self, index: int, image: Image.Image) -> None:
        if self._screensaver_active.is_set():
            return
        with self._lock:
            deck = self.deck
            if (
                deck is None
                or index >= self.key_count
                or self._screensaver_active.is_set()
            ):
                return
            try:
                from StreamDeck.ImageHelpers import PILHelper
                native = PILHelper.to_native_key_format(deck, image)
                deck.set_key_image(index, native)
            except Exception as e:
                log.warning("Failed writing key %d: %s", index, e)

    def set_brightness(self, pct: int) -> None:
        self.brightness = pct
        if self._screensaver_active.is_set():
            return
        with self._lock:
            if self.deck is not None:
                try:
                    self.deck.set_brightness(pct)
                except Exception:
                    pass

    # ---------- screen saver ----------

    def configure_screensaver(
        self,
        enabled: bool,
        style: str,
        idle_minutes: int,
        intensity: int,
    ) -> None:
        """Apply persisted screen saver settings and restart idle tracking."""
        with self._screensaver_lock:
            self._screensaver_enabled = bool(enabled)
            self._screensaver_style = (
                style if style in SCREENSAVER_IDS else DEFAULT_SCREENSAVER
            )
            self._screensaver_idle_minutes = max(
                1,
                min(1440, int(idle_minutes)),
            )
            self._screensaver_intensity = max(
                5,
                min(100, int(intensity)),
            )
            self._screensaver_preview = None
            self._last_activity = time.monotonic()
        self._screensaver_wakeup.set()

    def preview_screensaver(self, style: str, intensity: int) -> None:
        """Start a selected screen saver immediately without persisting it."""
        with self._screensaver_lock:
            selected = style if style in SCREENSAVER_IDS else DEFAULT_SCREENSAVER
            self._screensaver_preview = (
                selected,
                max(5, min(100, int(intensity))),
            )
            self._last_activity = time.monotonic()
        self._screensaver_wakeup.set()

    def record_activity(self) -> bool:
        """Wake the deck and return whether a screen saver was active."""
        active = self._screensaver_active.is_set()
        with self._screensaver_lock:
            self._last_activity = time.monotonic()
            self._screensaver_preview = None
        self._screensaver_wakeup.set()
        return active

    def stop_screensaver(self) -> None:
        self.record_activity()

    def _screensaver_loop(self) -> None:
        while not self._stop.is_set():
            selection = self._screensaver_selection(time.monotonic())
            if selection is None:
                self._screensaver_wakeup.wait(0.5)
                self._screensaver_wakeup.clear()
                continue

            style, intensity, preview = selection
            started = time.monotonic()
            self._screensaver_brightness = None
            self._screensaver_active.set()
            self.bus.emit(
                "deck.screensaver",
                active=True,
                preview=preview,
                style=style,
            )
            try:
                while not self._stop.is_set():
                    current = self._screensaver_selection(time.monotonic())
                    if current is None:
                        break
                    style, intensity, preview = current
                    frame_started = time.monotonic()
                    frame = screensaver_frame(
                        style,
                        frame_started - started,
                        self.key_count,
                        self.image_size,
                        intensity,
                    )
                    self._show_screensaver_frame(frame)
                    remaining = frame.delay - (
                        time.monotonic() - frame_started
                    )
                    if remaining > 0:
                        self._screensaver_wakeup.wait(remaining)
                    self._screensaver_wakeup.clear()
            except Exception:
                log.warning("Screen saver frame failed", exc_info=True)
                self._stop.wait(0.5)
            finally:
                self._screensaver_active.clear()
                self._restore_brightness()
                self.bus.emit(
                    "deck.screensaver",
                    active=False,
                    preview=preview,
                    style=style,
                )

    def _screensaver_selection(
        self,
        now: float,
    ) -> tuple[str, int, bool] | None:
        with self._screensaver_lock:
            if self._screensaver_preview is not None:
                style, intensity = self._screensaver_preview
                return style, intensity, True
            idle_seconds = now - self._last_activity
            if (
                self._screensaver_enabled
                and idle_seconds >= self._screensaver_idle_minutes * 60
            ):
                return (
                    self._screensaver_style,
                    self._screensaver_intensity,
                    False,
                )
        return None

    def _show_screensaver_frame(self, frame: ScreenSaverFrame) -> None:
        with self._lock:
            deck = self.deck
            if deck is not None and self._screensaver_active.is_set():
                try:
                    from StreamDeck.ImageHelpers import PILHelper

                    if frame.brightness != self._screensaver_brightness:
                        deck.set_brightness(frame.brightness)
                        self._screensaver_brightness = frame.brightness
                    for index, image in enumerate(frame.images):
                        if (
                            self._stop.is_set()
                            or self._screensaver_wakeup.is_set()
                            or not self._screensaver_active.is_set()
                        ):
                            break
                        native = PILHelper.to_native_key_format(deck, image)
                        deck.set_key_image(index, native)
                except Exception as error:
                    log.warning("Failed writing a screen saver frame: %s", error)
        if (
            not self._stop.is_set()
            and not self._screensaver_wakeup.is_set()
            and self._screensaver_active.is_set()
        ):
            self.bus.emit(
                "ui.screensaver_frame",
                images=tuple(_to_png_bytes(image) for image in frame.images),
            )

    def _restore_brightness(self) -> None:
        self._screensaver_brightness = None
        with self._lock:
            if self.deck is not None:
                try:
                    self.deck.set_brightness(self.brightness)
                except Exception:
                    pass


def _to_png_bytes(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
