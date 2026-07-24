from __future__ import annotations

import threading
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import ImageChops

from linuxstreamdeck.core.config import (
    DEFAULT_SCREENSAVER,
    SCREENSAVER_CHOICES,
    Config,
)
from linuxstreamdeck.core.events import EventBus
from linuxstreamdeck.device.manager import DeckManager
from linuxstreamdeck.device.screensaver import TITLE, screensaver_frame


class ScreenSaverFrameTests(unittest.TestCase):
    def test_all_six_styles_fill_the_deck_and_animate(self) -> None:
        self.assertEqual(len(SCREENSAVER_CHOICES), 6)

        for style, _name, _description in SCREENSAVER_CHOICES:
            first = screensaver_frame(style, 0.0, 15, (72, 72), 47)
            later = screensaver_frame(style, 1.7, 15, (72, 72), 47)

            self.assertEqual(len(first.images), 15)
            self.assertTrue(
                all(image.size == (72, 72) for image in first.images)
            )
            self.assertTrue(all(image.mode == "RGB" for image in first.images))
            self.assertGreaterEqual(first.brightness, 1)
            self.assertLessEqual(first.brightness, 47)
            self.assertTrue(
                any(
                    ImageChops.difference(before, after).getbbox() is not None
                    for before, after in zip(first.images, later.images)
                ),
                style,
            )

    def test_unknown_style_falls_back_to_neon_pipes(self) -> None:
        fallback = screensaver_frame("unknown", 2.0, 15, (72, 72), 40)
        expected = screensaver_frame(
            DEFAULT_SCREENSAVER,
            2.0,
            15,
            (72, 72),
            40,
        )

        self.assertEqual(fallback.brightness, expected.brightness)
        for actual, wanted in zip(fallback.images, expected.images):
            self.assertIsNone(ImageChops.difference(actual, wanted).getbbox())

    def test_title_style_uses_all_keys_on_a_black_background(self) -> None:
        frame = screensaver_frame(
            "linuxstreamdeck",
            1.0,
            15,
            (72, 72),
            35,
        )

        self.assertEqual(len(TITLE), 15)
        self.assertTrue(all(image.getbbox() is not None for image in frame.images))
        black_pixels = sum(
            count
            for image in frame.images
            for count, color in image.getcolors(maxcolors=72 * 72)
            if color == (0, 0, 0)
        )
        total_pixels = 15 * 72 * 72
        self.assertGreater(black_pixels / total_pixels, 0.70)


class ScreenSaverConfigTests(unittest.TestCase):
    def test_settings_round_trip_with_the_complete_configuration(self) -> None:
        config = Config.from_dict(
            {
                "screensaver": {
                    "enabled": True,
                    "style": "orbital_core",
                    "idle_minutes": 12,
                    "intensity": 55,
                }
            }
        )

        restored = Config.from_dict(config._serializable_dict())

        self.assertTrue(restored.screensaver.enabled)
        self.assertEqual(restored.screensaver.style, "orbital_core")
        self.assertEqual(restored.screensaver.idle_minutes, 12)
        self.assertEqual(restored.screensaver.intensity, 55)

    def test_invalid_settings_are_safely_normalized(self) -> None:
        config = Config.from_dict(
            {
                "screensaver": {
                    "enabled": "yes",
                    "style": "not-installed",
                    "idle_minutes": 0,
                    "intensity": 500,
                }
            }
        )

        self.assertFalse(config.screensaver.enabled)
        self.assertEqual(config.screensaver.style, DEFAULT_SCREENSAVER)
        self.assertEqual(config.screensaver.idle_minutes, 1)
        self.assertEqual(config.screensaver.intensity, 100)

    def test_portable_export_includes_screen_saver_settings(self) -> None:
        source = Config()
        source.screensaver.enabled = True
        source.screensaver.style = "digital_rain"
        source.screensaver.idle_minutes = 9
        source.screensaver.intensity = 45

        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "screen-saver.lsdconfig"
            source.export_bundle(bundle)
            restored = Config()
            restored.import_bundle(bundle)

        self.assertTrue(restored.screensaver.enabled)
        self.assertEqual(restored.screensaver.style, "digital_rain")
        self.assertEqual(restored.screensaver.idle_minutes, 9)
        self.assertEqual(restored.screensaver.intensity, 45)


class ScreenSaverRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = EventBus()
        self.manager = DeckManager(
            self.bus,
            screensaver_enabled=True,
            screensaver_idle_minutes=1,
        )

    def tearDown(self) -> None:
        self.manager.stop()

    def _start_without_device_scan(self) -> None:
        with patch.object(
            self.manager,
            "_scan_loop",
            side_effect=lambda: self.manager._stop.wait(),
        ):
            self.manager.start()

    def test_idle_animation_wakes_and_consumes_the_first_press(self) -> None:
        active = threading.Event()
        inactive = threading.Event()
        frame_seen = threading.Event()
        key_events = []

        def on_state(_topic, data) -> None:
            (active if data["active"] else inactive).set()

        self.bus.subscribe("deck.screensaver", on_state)
        self.bus.subscribe(
            "ui.screensaver_frame",
            lambda _topic, _data: frame_seen.set(),
        )
        self.bus.subscribe(
            "deck.key",
            lambda _topic, data: key_events.append(
                (data["index"], data["pressed"])
            ),
        )
        self._start_without_device_scan()
        with self.manager._screensaver_lock:
            self.manager._last_activity = time.monotonic() - 61
        self.manager._screensaver_wakeup.set()

        self.assertTrue(active.wait(1.0))
        self.assertTrue(frame_seen.wait(1.0))
        self.manager._on_key(None, 4, True)
        self.manager._on_key(None, 4, False)
        self.assertTrue(inactive.wait(1.0))
        self.assertEqual(key_events, [])

        self.manager._on_key(None, 4, True)
        self.manager._on_key(None, 4, False)
        self.assertEqual(key_events, [(4, True), (4, False)])

    def test_preview_works_while_automatic_mode_is_disabled(self) -> None:
        self.manager.configure_screensaver(
            False,
            "circuit_pulse",
            10,
            42,
        )
        started = threading.Event()
        stopped = threading.Event()
        states = []

        def on_state(_topic, data) -> None:
            states.append((data["active"], data["preview"], data["style"]))
            (started if data["active"] else stopped).set()

        self.bus.subscribe("deck.screensaver", on_state)
        self._start_without_device_scan()
        self.manager.preview_screensaver("circuit_pulse", 42)

        self.assertTrue(started.wait(1.0))
        self.manager.stop_screensaver()
        self.assertTrue(stopped.wait(1.0))
        self.assertEqual(states[0], (True, True, "circuit_pulse"))
        self.assertEqual(states[-1], (False, True, "circuit_pulse"))

    def test_screen_saver_owns_hid_output_and_restores_normal_brightness(
        self,
    ) -> None:
        class FakeDeck:
            def __init__(self) -> None:
                self.brightness = []
                self.images = []

            def set_brightness(self, value) -> None:
                self.brightness.append(value)

            def set_key_image(self, index, image) -> None:
                self.images.append((index, image))

        deck = FakeDeck()
        self.manager.deck = deck
        self.manager._screensaver_active.set()
        frame = screensaver_frame(
            "orbital_core",
            2.0,
            2,
            (8, 8),
            41,
            columns=2,
        )

        with patch(
            "StreamDeck.ImageHelpers.PILHelper.to_native_key_format",
            side_effect=lambda _deck, image: image.getpixel((0, 0)),
        ):
            self.manager._show_screensaver_frame(frame)

        self.assertEqual(deck.brightness, [frame.brightness])
        self.assertEqual([item[0] for item in deck.images], [0, 1])
        self.manager.set_key_image(0, frame.images[0])
        self.assertEqual(len(deck.images), 2)
        self.manager._screensaver_active.clear()
        self.manager._restore_brightness()
        self.assertEqual(deck.brightness[-1], self.manager.brightness)


if __name__ == "__main__":
    unittest.main()
