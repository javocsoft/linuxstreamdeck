from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

from PIL import Image

from linuxstreamdeck.core.config import EXIT_DISPLAY_BLANK
from linuxstreamdeck.core.events import EventBus
from linuxstreamdeck.device.manager import DeckManager
from linuxstreamdeck.device.startup_animation import (
    AnimationFrame,
    TITLE,
    startup_frames,
)


class StartupAnimationFrameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.frames = list(startup_frames(15, (72, 72), 80))

    def test_sequence_covers_wake_title_hold_and_fade(self) -> None:
        stages = [frame.stage for frame in self.frames]

        self.assertEqual(len(self.frames), 33)
        self.assertEqual(stages[0], "wake")
        self.assertIn("burst", stages)
        self.assertIn("title", stages)
        self.assertIn("hold", stages)
        self.assertIn("fade", stages)
        self.assertEqual(stages[-1], "black")
        self.assertGreater(sum(frame.delay for frame in self.frames), 1.5)
        self.assertLess(sum(frame.delay for frame in self.frames), 3.0)

    def test_every_frame_matches_the_device_and_respects_brightness(self) -> None:
        for frame in self.frames:
            self.assertEqual(len(frame.images), 15)
            self.assertTrue(all(image.size == (72, 72) for image in frame.images))
            self.assertTrue(all(image.mode == "RGB" for image in frame.images))
            self.assertGreaterEqual(frame.brightness, 0)
            self.assertLessEqual(frame.brightness, 80)

    def test_title_uses_all_fifteen_keys_then_fades_to_black(self) -> None:
        title = next(frame for frame in self.frames if frame.stage == "hold")
        bright_keys = 0
        for image in title.images:
            pixels = image.load()
            bright_pixels = sum(
                1
                for y in range(image.height)
                for x in range(image.width)
                for red, green, blue in (pixels[x, y],)
                if red > 130 and green > 170 and blue > 190
            )
            if bright_pixels > 100:
                bright_keys += 1

        self.assertEqual(len(TITLE), 15)
        self.assertEqual(bright_keys, 15)
        self.assertTrue(
            all(image.getbbox() is None for image in self.frames[-1].images)
        )


class FakePhysicalDeck:
    def __init__(self) -> None:
        self.brightness = []
        self.images = []
        self.callback = None
        self.closed = False

    def set_brightness(self, value) -> None:
        self.brightness.append(value)

    def set_key_image(self, index, image) -> None:
        self.images.append((index, image))

    def open(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    def reset(self) -> None:
        pass

    def key_count(self) -> int:
        return 15

    def key_image_format(self):
        return {"size": (72, 72)}

    def connected(self) -> bool:
        return not self.closed

    def set_key_callback(self, callback) -> None:
        self.callback = callback

    def deck_type(self) -> str:
        return "Fake Deck"


class StartupAnimationPlaybackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = DeckManager(EventBus(), brightness=73)

    def test_manager_writes_frames_and_restores_configured_brightness(self) -> None:
        deck = FakePhysicalDeck()
        frames = (
            AnimationFrame(
                (
                    Image.new("RGB", (2, 2), "red"),
                    Image.new("RGB", (2, 2), "blue"),
                ),
                20,
                0,
                "wake",
            ),
            AnimationFrame(
                (
                    Image.new("RGB", (2, 2), "black"),
                    Image.new("RGB", (2, 2), "black"),
                ),
                10,
                0,
                "black",
            ),
        )

        with patch(
            "linuxstreamdeck.device.manager.startup_frames",
            return_value=iter(frames),
        ):
            completed = self.manager._play_startup_animation(
                deck,
                2,
                (2, 2),
                native_converter=lambda _deck, image: image.getpixel((0, 0)),
            )

        self.assertTrue(completed)
        self.assertEqual(deck.brightness, [20, 10, 73])
        self.assertEqual(
            deck.images,
            [
                (0, (255, 0, 0)),
                (1, (0, 0, 255)),
                (0, (0, 0, 0)),
                (1, (0, 0, 0)),
            ],
        )

    def test_shutdown_cancels_animation_and_still_restores_brightness(self) -> None:
        deck = FakePhysicalDeck()
        self.manager._stop.set()

        completed = self.manager._play_startup_animation(
            deck,
            15,
            (72, 72),
            native_converter=lambda _deck, image: image,
        )

        self.assertFalse(completed)
        self.assertEqual(deck.images, [])
        self.assertEqual(deck.brightness[-1], 73)

    def test_connection_is_published_only_after_animation(self) -> None:
        deck = FakePhysicalDeck()
        events = []
        self.manager.bus.subscribe(
            "deck.connected",
            lambda _topic, _data: events.append("connected"),
        )

        def animate(_deck, _key_count, _image_size, **_kwargs) -> bool:
            self.assertIsNone(self.manager.deck)
            self.assertIsNone(deck.callback)
            events.append("animation")
            return True

        with (
            patch(
                "StreamDeck.DeviceManager.DeviceManager"
            ) as device_manager,
            patch.object(
                self.manager,
                "_play_startup_animation",
                side_effect=animate,
            ),
        ):
            device_manager.return_value.enumerate.return_value = [deck]
            self.manager._try_open()

        self.assertEqual(events, ["animation", "connected"])
        self.assertIs(self.manager.deck, deck)
        self.assertIsNotNone(deck.callback)

    def test_shutdown_during_animation_still_applies_the_exit_display(
        self,
    ) -> None:
        deck = FakePhysicalDeck()
        self.manager.configure_exit_display(EXIT_DISPLAY_BLANK, "")

        def cancel_animation(*_args, **_kwargs) -> bool:
            self.manager._stop.set()
            return False

        with (
            patch(
                "StreamDeck.DeviceManager.DeviceManager"
            ) as device_manager,
            patch.object(
                self.manager,
                "_play_startup_animation",
                side_effect=cancel_animation,
            ),
            patch(
                "StreamDeck.ImageHelpers.PILHelper.to_native_key_format",
                side_effect=lambda _deck, image: image.getpixel((0, 0)),
            ),
        ):
            device_manager.return_value.enumerate.return_value = [deck]
            self.manager._try_open()

        self.assertTrue(deck.closed)
        self.assertEqual(len(deck.images), 15)
        self.assertTrue(
            all(image == (0, 0, 0) for _index, image in deck.images)
        )
        self.assertEqual(deck.brightness[-1], 0)
        self.assertIsNone(self.manager.deck)


if __name__ == "__main__":
    unittest.main()
