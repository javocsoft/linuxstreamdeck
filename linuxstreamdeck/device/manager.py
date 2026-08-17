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

from ..core.config import (
    DEFAULT_SCREENSAVER,
    DIAL_TOUCH_SIZE,
    EXIT_DISPLAY_BLANK,
    EXIT_DISPLAY_DEFAULT,
    EXIT_DISPLAY_MODES,
    SCREENSAVER_IDS,
)
from ..core.events import EventBus
from .exit_display import blank_exit_tiles, exit_image_tiles
from .screensaver import ScreenSaverFrame, screensaver_frame
from .startup_animation import startup_frames

log = logging.getLogger(__name__)

SCAN_SECONDS = 3
# Consecutive failed opens before saying so. A deck that has just been plugged
# in often refuses the first attempt while it is still enumerating, and crying
# wolf on that would be worse than staying quiet.
OPEN_FAILURES_BEFORE_WARNING = 2
DEFAULT_KEYS = 15            # Stream Deck MK.2
DEFAULT_COLUMNS = 5          # ...and its 5x3 grid
DEFAULT_IMAGE_SIZE = (72, 72)


def _device_id(device) -> str:
    """Something stable to tell two unopened devices apart.

    The serial number needs the device to be open, and opening a deck only to
    name it in a warning would disturb the USB bus for nothing.
    """
    try:
        return str(device.id())
    except Exception:
        return repr(device)


def _device_name(device) -> str:
    try:
        return str(device.deck_type())
    except Exception:
        return "Stream Deck"


def _is_visual(device) -> bool:
    """Whether the device has key displays at all.

    The Stream Deck Pedal has three keys and no screens. Assumed true when the
    driver does not say, because refusing a deck we simply failed to ask about
    would be far worse than trying to draw on it.
    """
    try:
        return bool(device.is_visual())
    except Exception:
        return True


def _device_columns(device, key_count: int) -> int:
    """Columns of the device's key grid, as `key_layout()` reports them.

    Every full-deck image is split along this, so a wrong value scrambles the
    screen saver, the startup sequence and the custom exit image across the
    keys rather than merely looking odd.
    """
    try:
        _rows, columns = device.key_layout()
        columns = int(columns)
    except Exception:
        return min(DEFAULT_COLUMNS, max(1, key_count))
    if columns < 1:
        return min(DEFAULT_COLUMNS, max(1, key_count))
    return columns


def _dial_count(device) -> int:
    """How many encoders the device has, 0 for every deck that is not a Plus."""
    try:
        return max(0, int(device.dial_count()))
    except Exception:
        return 0


def _touch_size(device) -> tuple[int, int]:
    """The LCD strip's pixel size, or the documented default when unavailable."""
    try:
        fmt = device.touchscreen_image_format()
        width, height = fmt["size"]
        if int(width) > 0 and int(height) > 0:
            return int(width), int(height)
    except Exception:
        pass
    return DIAL_TOUCH_SIZE


def _dial_event(event, value) -> tuple[str | None, int]:
    """Normalise a library dial event into a direction and a tick count.

    A push arrives as a boolean and a turn as a signed count, so this is the
    one place that has to know the difference. The release edge is dropped:
    a dial runs its action on the way down, like a key.
    """
    name = str(getattr(event, "name", event)).upper()
    if "PUSH" in name:
        return ("press", 1) if bool(value) else (None, 0)
    if "TURN" in name:
        try:
            ticks = int(value)
        except (TypeError, ValueError):
            return (None, 0)
        if ticks == 0:
            return (None, 0)
        return ("right" if ticks > 0 else "left", abs(ticks))
    return (None, 0)


def touch_segment(x, width: int, dials: int) -> int | None:
    """Which dial the strip position `x` belongs to.

    The strip spans the encoders, so a tap is only meaningful as "the dial
    under it"; anything outside the strip is discarded rather than clamped,
    since a coordinate we do not understand should do nothing at all.
    """
    if dials <= 0 or width <= 0:
        return None
    try:
        position = int(x)
    except (TypeError, ValueError):
        return None
    if not 0 <= position < width:
        return None
    return min(dials - 1, position * dials // width)


class DeckManager:
    def __init__(
        self,
        bus: EventBus,
        brightness: int = 80,
        screensaver_enabled: bool = False,
        screensaver_style: str = DEFAULT_SCREENSAVER,
        screensaver_idle_minutes: int = 5,
        screensaver_intensity: int = 35,
        exit_display_mode: str = EXIT_DISPLAY_DEFAULT,
        exit_display_image: str = "",
    ) -> None:
        self.bus = bus
        self.brightness = brightness
        self.deck = None
        self.key_count = DEFAULT_KEYS
        # Columns of the connected deck. Every full-deck image is laid out with
        # it, so assuming 5 scrambled the art on a Mini (3) or an XL (8).
        self.columns = DEFAULT_COLUMNS
        self.image_size = DEFAULT_IMAGE_SIZE
        # Stream Deck + only: 0 on every other model, which is what every
        # dial and touchscreen path checks before doing anything.
        self.dial_count = 0
        self.touch_size = DIAL_TOUCH_SIZE
        # Which set of devices the "only one deck is used" notice was last
        # given for; see _report_extra_devices.
        self._reported_devices: tuple[str, ...] = ()
        # Same idea for a deck we refuse: the scan retries every few seconds.
        self._rejected_device: tuple[str, ...] = ()
        # A deck that is present but will not open; see _note_open_failure.
        self._failed_device = ""
        self._failed_attempts = 0
        self._reported_failure = ""
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
        self._screensaver_suppression_reasons: set[str] = set()
        self._screensaver_suppressed = False
        self._screensaver_preview: tuple[str, int] | None = None
        self._screensaver_brightness: int | None = None
        self._last_activity = time.monotonic()
        self._suppressed_keys: set[int] = set()
        self._exit_display_mode = (
            exit_display_mode
            if exit_display_mode in EXIT_DISPLAY_MODES
            else EXIT_DISPLAY_DEFAULT
        )
        self._exit_display_image = str(exit_display_image or "")

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
        self._close(emit=False, apply_exit_display=True)

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

    def _report_extra_devices(self, devices) -> None:
        """Say so when more than one deck is plugged in, since one is used.

        Only the first device is opened. Dropping the rest without a word looked
        exactly like a broken second deck, so the limitation is stated instead.

        Reported once per distinct set of devices: `_try_open` runs every few
        seconds while nothing is connected, and repeating this in the status bar
        would bury everything else.
        """
        if len(devices) < 2:
            # Unplugging back down to one arms the notice again.
            self._reported_devices = ()
            return
        seen = tuple(sorted(_device_id(device) for device in devices))
        if seen == self._reported_devices:
            return
        self._reported_devices = seen
        message = (
            f"{len(devices)} Stream Decks found. Only the first "
            f"({_device_name(devices[0])}) is used; multiple decks are not "
            "supported yet."
        )
        log.warning(message)
        self.bus.emit("status", text=message)

    def _reject_device(self, device) -> None:
        """Say why a deck without displays is not taken, once per device."""
        name = _device_name(device)
        identity = (_device_id(device),)
        if identity == self._rejected_device:
            return
        self._rejected_device = identity
        message = (
            f"{name} has no key displays, so it is not supported. "
            "LinuxStreamDeck needs a deck with screens on its keys."
        )
        log.warning(message)
        self.bus.emit("status", text=message)

    def _note_open_failure(self, device) -> None:
        """Say something when a deck is plugged in but will not open.

        Until now the only sign was the deck never appearing, with the reason
        buried in the log. The usual cause is the USB permission rule not being
        installed, and the second is another program already holding the device
        — a previous instance that never exited, most often.
        """
        identity = _device_id(device)
        if identity != self._failed_device:
            self._failed_device = identity
            self._failed_attempts = 0
        self._failed_attempts += 1
        if self._failed_attempts < OPEN_FAILURES_BEFORE_WARNING:
            return
        if self._reported_failure == identity:
            return
        self._reported_failure = identity
        message = (
            f"{_device_name(device)} was found but could not be opened. "
            "Check the USB permission rule (install-udev.sh, or reinstall the "
            "package) and that no other program is using the deck."
        )
        log.warning(message)
        self.bus.emit("status", text=message)

    def _clear_open_failures(self) -> None:
        """Forget the failures once a deck actually opens."""
        self._failed_device = ""
        self._failed_attempts = 0
        self._reported_failure = ""

    def _try_open(self) -> None:
        try:
            from StreamDeck.DeviceManager import DeviceManager
            devices = DeviceManager().enumerate()
        except Exception as e:
            # typical: libhidapi not installed yet
            log.debug("Could not enumerate HID devices: %s", e)
            return
        self._report_extra_devices(devices)
        for dev in devices:
            if self._stop.is_set():
                return
            try:
                self.record_activity()
                dev.open()
                if self._stop.is_set():
                    dev.close()
                    return
                if not _is_visual(dev):
                    # A Stream Deck Pedal has keys but no displays at all, and
                    # most of this application is about what gets drawn.
                    self._reject_device(dev)
                    dev.close()
                    return
                dev.reset()
                key_count = dev.key_count()
                columns = _device_columns(dev, key_count)
                fmt = dev.key_image_format()
                image_size = tuple(fmt["size"])
                if not self._play_startup_animation(
                    dev,
                    key_count,
                    image_size,
                    columns=columns,
                ):
                    if self._stop.is_set():
                        self._apply_exit_display(
                            dev,
                            key_count=key_count,
                            image_size=image_size,
                            columns=columns,
                        )
                    dev.close()
                    return
                if not dev.connected():
                    raise OSError("Stream Deck disconnected during startup")
                dev.set_key_callback(self._on_key)
                dials = _dial_count(dev)
                if dials:
                    # Only the Plus answers these, and asking a deck that has
                    # no encoders must not cost it its connection.
                    try:
                        dev.set_dial_callback(self._on_dial)
                        dev.set_touchscreen_callback(self._on_touchscreen)
                    except Exception:
                        log.warning(
                            "Dials were reported but could not be registered",
                            exc_info=True,
                        )
                        dials = 0
                self.deck = dev
                self._clear_open_failures()
                self.key_count = key_count
                self.columns = columns
                self.image_size = image_size
                self.dial_count = dials
                self.touch_size = _touch_size(dev) if dials else DIAL_TOUCH_SIZE
                self.record_activity()
                log.info(
                    "Connected: %s (%d keys in %d columns, %sx%s)",
                    dev.deck_type(), self.key_count, self.columns,
                    *self.image_size,
                )
                self.bus.emit(
                    "deck.connected",
                    model=dev.deck_type(),
                    keys=self.key_count,
                    columns=self.columns,
                )
                return
            except Exception as e:
                # With the traceback: without it a TypeError from our own code
                # reads exactly like a missing udev rule.
                log.warning("Could not open %s: %s", dev, e, exc_info=True)
                self._note_open_failure(dev)
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
        columns: int = DEFAULT_COLUMNS,
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
                columns=columns,
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

    def _close(self, emit: bool, apply_exit_display: bool = False) -> None:
        with self._lock:
            deck, self.deck = self.deck, None
            # No device means no encoders: leaving a stale count behind would
            # keep the touchscreen renderer drawing for hardware that is gone.
            had_dials, self.dial_count = self.dial_count, 0
        if deck is not None:
            try:
                deck.set_key_callback(None)
            except Exception:
                pass
            if had_dials:
                for setter in ("set_dial_callback", "set_touchscreen_callback"):
                    try:
                        getattr(deck, setter)(None)
                    except Exception:
                        pass
            if apply_exit_display:
                self._apply_exit_display(deck)
            else:
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

    def _on_dial(self, deck, index: int, event, value) -> None:
        """Encoder turned or pushed, on the library's read thread.

        The library reports a turn as a signed tick count and a push as a
        boolean, so both are normalised here into one direction the controller
        can act on. A turn has no release, which is why it carries `ticks`: a
        fast spin arrives as several ticks in one event and must not be
        flattened into a single step.
        """
        with self._screensaver_lock:
            # A dial wakes the screen saver exactly as a key does, and the
            # gesture that woke it is consumed rather than acted on.
            if self._screensaver_active.is_set():
                self._last_activity = time.monotonic()
                self._screensaver_preview = None
                self._screensaver_wakeup.set()
                return
            self._last_activity = time.monotonic()
        direction, ticks = _dial_event(event, value)
        if direction is None:
            return
        self.bus.emit(
            "deck.dial", index=index, direction=direction, ticks=ticks
        )

    def _on_touchscreen(self, deck, event, value) -> None:
        """A tap on the LCD strip, mapped to the dial it sits above."""
        with self._screensaver_lock:
            if self._screensaver_active.is_set():
                self._last_activity = time.monotonic()
                self._screensaver_preview = None
                self._screensaver_wakeup.set()
                return
            self._last_activity = time.monotonic()
        if not isinstance(value, dict):
            return
        index = touch_segment(value.get("x"), self.touch_size[0], self.dial_count)
        if index is None:
            return
        self.bus.emit("deck.touch", index=index, event=str(event))

    def set_touchscreen_image(self, image: Image.Image) -> None:
        """Write the full LCD strip of a Stream Deck +."""
        if self._screensaver_active.is_set():
            return
        with self._lock:
            deck = self.deck
            if (
                deck is None
                or not self.dial_count
                or self._screensaver_active.is_set()
            ):
                return
            try:
                from StreamDeck.ImageHelpers import PILHelper

                native = PILHelper.to_native_touchscreen_format(deck, image)
                deck.set_touchscreen_image(
                    native, 0, 0, image.width, image.height
                )
            except Exception as e:
                log.warning("Failed writing the touchscreen: %s", e)

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

    # ---------- display after exit ----------

    def configure_exit_display(self, mode: str, image_path: str) -> None:
        """Configure what a connected deck retains after a clean app exit."""
        self._exit_display_mode = (
            mode if mode in EXIT_DISPLAY_MODES else EXIT_DISPLAY_DEFAULT
        )
        self._exit_display_image = str(image_path or "")

    def _apply_exit_display(
        self,
        deck,
        key_count: int | None = None,
        image_size: tuple[int, int] | None = None,
        columns: int | None = None,
    ) -> None:
        """Apply the selected persistent hardware state before closing HID."""
        mode = self._exit_display_mode
        count = self.key_count if key_count is None else key_count
        size = self.image_size if image_size is None else image_size
        cols = self.columns if columns is None else columns
        if mode == EXIT_DISPLAY_DEFAULT:
            try:
                deck.reset()
            except Exception:
                log.debug(
                    "Could not restore the device standby image",
                    exc_info=True,
                )
            return

        try:
            from StreamDeck.ImageHelpers import PILHelper

            if mode == EXIT_DISPLAY_BLANK:
                images = blank_exit_tiles(count, size)
            else:
                images = exit_image_tiles(
                    self._exit_display_image,
                    count,
                    size,
                    columns=cols,
                )
                deck.set_brightness(self.brightness)
            for index, image in enumerate(images):
                native = PILHelper.to_native_key_format(deck, image)
                deck.set_key_image(index, native)
            if mode == EXIT_DISPLAY_BLANK:
                deck.set_brightness(0)
        except Exception as error:
            log.warning(
                "Could not apply the configured exit display (%s); using "
                "the device default",
                error,
            )
            try:
                deck.reset()
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

    def set_screensaver_suppressed(
        self,
        suppressed: bool,
        *,
        reason: str = "external",
    ) -> None:
        """Prevent automatic activation for one independent live activity.

        OBS and a game can overlap. A single boolean lets whichever one stops
        first accidentally re-enable the saver underneath the other, so the
        effective policy is the union of named reasons while the legacy
        one-argument call remains the default external reason.
        """
        reason = str(reason or "external")
        with self._screensaver_lock:
            was_suppressed = bool(self._screensaver_suppression_reasons)
            if suppressed:
                self._screensaver_suppression_reasons.add(reason)
            else:
                self._screensaver_suppression_reasons.discard(reason)
            is_suppressed = bool(self._screensaver_suppression_reasons)
            self._screensaver_suppressed = is_suppressed
            if was_suppressed == is_suppressed:
                return
            # Treat both edges as activity: entering suppression wakes an
            # active saver, and leaving it starts a fresh idle interval.
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
                        columns=self.columns,
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
            # A game owns both input and pixels. Unlike OBS suppression, it
            # must also stop a settings dialog that was already open from
            # starting a manual preview over the live game board.
            if "game" in self._screensaver_suppression_reasons:
                return None
            if self._screensaver_preview is not None:
                style, intensity = self._screensaver_preview
                return style, intensity, True
            if self._screensaver_suppressed:
                return None
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
