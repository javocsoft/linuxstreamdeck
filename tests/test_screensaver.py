from __future__ import annotations

import threading
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import ImageChops, ImageStat

from linuxstreamdeck.core.config import (
    DEFAULT_SCREENSAVER,
    SCREENSAVER_CHOICES,
    Config,
)
from linuxstreamdeck.core.events import EventBus
from linuxstreamdeck.device.manager import DeckManager
from linuxstreamdeck.device.screensaver import TITLE, screensaver_frame


class ScreenSaverFrameTests(unittest.TestCase):
    def test_every_installed_style_fills_the_deck_and_animates(self) -> None:
        self.assertEqual(len(SCREENSAVER_CHOICES), 11)

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


class EmberScreenSaverTests(unittest.TestCase):
    """Flame climbing the deck, built from noise that must never be random."""

    @staticmethod
    def _frame(elapsed: float, intensity: int = 100):
        return screensaver_frame("ember_field", elapsed, 15, (72, 72), intensity)

    @staticmethod
    def _mean(image) -> list[float]:
        return ImageStat.Stat(image).mean

    def test_the_style_is_installed(self) -> None:
        self.assertIn(
            "ember_field", [choice[0] for choice in SCREENSAVER_CHOICES]
        )

    def test_the_flame_is_warm(self) -> None:
        red, green, blue = self._mean(_deck_image(self._frame(1.3).images))

        self.assertGreater(red, 40)
        self.assertGreater(red, green * 1.5)
        self.assertGreater(green, blue)

    def test_it_burns_from_the_bottom_up(self) -> None:
        images = self._frame(1.3).images
        rows = [
            sum(self._mean(images[column + row * 5])[0] for column in range(5))
            for row in range(3)
        ]

        self.assertGreater(rows[2], rows[1])
        self.assertGreater(rows[1], rows[0])

    def test_the_same_moment_always_paints_the_same_frame(self) -> None:
        """The whole reason its noise is seeded rather than generated."""
        for before, after in zip(self._frame(2.0).images, self._frame(2.0).images):
            self.assertIsNone(ImageChops.difference(before, after).getbbox())

    def test_the_noise_is_rebuilt_identically_from_its_seed(self) -> None:
        """A random tile would flicker and break that determinism."""
        from linuxstreamdeck.device import screensaver as module

        first = module._noise_strip(module.EMBER_SEED, 7, (72, 72)).copy()
        module._noise_strip.cache_clear()
        self.addCleanup(module._noise_strip.cache_clear)
        second = module._noise_strip(module.EMBER_SEED, 7, (72, 72))

        self.assertIsNone(ImageChops.difference(first, second).getbbox())

    def test_the_noise_meets_itself_without_a_seam(self) -> None:
        """It is cropped at any offset, so its top edge has to match its bottom."""
        from linuxstreamdeck.device import screensaver as module

        strip = module._noise_strip(module.EMBER_SEED, 7, (72, 72))
        self.addCleanup(module._noise_strip.cache_clear)
        top = strip.crop((0, 0, 72, 1))
        wrapped = strip.crop((0, 72, 72, 73))

        self.assertIsNone(ImageChops.difference(top, wrapped).getbbox())


class HyperspaceScreenSaverTests(unittest.TestCase):
    """Stars stretching out of the vanishing point."""

    @staticmethod
    def _frame(elapsed: float, intensity: int = 100):
        return screensaver_frame("hyperspace", elapsed, 15, (72, 72), intensity)

    def test_the_style_is_installed(self) -> None:
        self.assertIn(
            "hyperspace", [choice[0] for choice in SCREENSAVER_CHOICES]
        )

    def test_the_field_surrounds_the_vanishing_point_from_the_first_frame(
        self,
    ) -> None:
        """The saver starts at 0 every time it wakes, so 0 has to look right.

        Phases derived linearly from the star index lined up with the angles,
        which are linear too, and the whole field collapsed onto one spiral —
        leaving entire quadrants of the deck empty.
        """
        deck = _deck_image(self._frame(0.0).images)
        half_width, half_height = deck.width // 2, deck.height // 2
        quadrants = (
            (0, 0, half_width, half_height),
            (half_width, 0, deck.width, half_height),
            (0, half_height, half_width, deck.height),
            (half_width, half_height, deck.width, deck.height),
        )

        for box in quadrants:
            self.assertIsNotNone(
                deck.crop(box).getbbox(), f"quadrant {box} is empty"
            )

    def test_the_vanishing_point_is_the_brightest_part_of_the_deck(self) -> None:
        """Everything radiates from the middle key, so that is where it packs."""
        deck = _deck_image(self._frame(3.0).images)
        middle = ImageStat.Stat(deck.crop((144, 72, 216, 144))).mean[2]

        for box in ((0, 72, 72, 144), (288, 72, 360, 144),
                    (144, 0, 216, 72), (144, 144, 216, 216)):
            self.assertGreater(middle, ImageStat.Stat(deck.crop(box)).mean[2])

    def test_the_streaks_reach_the_outer_keys(self) -> None:
        """A warp that only lit the middle would just be a blinking dot."""
        images = self._frame(3.0).images

        for corner in (0, 4, 10, 14):
            self.assertIsNotNone(
                images[corner].getbbox(), f"key {corner} is empty"
            )

    def test_the_same_moment_always_paints_the_same_frame(self) -> None:
        for before, after in zip(self._frame(2.0).images, self._frame(2.0).images):
            self.assertIsNone(ImageChops.difference(before, after).getbbox())


class SplitFlapScreenSaverTests(unittest.TestCase):
    """One flap module per key, riffling to a word and settling."""

    def setUp(self) -> None:
        from linuxstreamdeck.device import screensaver as module

        self.module = module

    @staticmethod
    def _frame(elapsed: float, intensity: int = 100):
        return screensaver_frame("split_flap", elapsed, 15, (72, 72), intensity)

    def _states(self, moment: float, count: int = 15):
        letters = self.module._flap_word(0.0, count)
        return [
            self.module._flap_state(index, count, moment, letters)
            for index in range(count)
        ]

    def test_the_style_is_installed(self) -> None:
        self.assertIn(
            "split_flap", [choice[0] for choice in SCREENSAVER_CHOICES]
        )

    def test_every_word_fills_the_deck_exactly(self) -> None:
        for word in self.module.SPLIT_FLAP_WORDS:
            self.assertEqual(len(word.replace(" ", "")), 15, word)

    def test_the_board_settles_on_the_word(self) -> None:
        letters = self.module._flap_word(0.0, 15)
        self.assertEqual(letters, "LINUXSTREAMDECK")

        for index, (_out, incoming, flip) in enumerate(
            self._states(self.module.SPLIT_FLAP_SPIN)
        ):
            self.assertEqual(incoming, letters[index])
            self.assertEqual(flip, 1.0)

    def test_the_modules_are_still_turning_at_the_start(self) -> None:
        turning = [
            incoming
            for _out, incoming, flip in self._states(0.0)
            if flip < 1.0
        ]

        self.assertEqual(len(turning), 15)

    def test_the_word_assembles_from_the_left(self) -> None:
        """A staggered settle, not the whole board landing at once."""
        midway = self._states(self.module.SPLIT_FLAP_SPIN * 0.6)
        settled = [index for index, state in enumerate(midway) if state[2] == 1.0]

        self.assertTrue(settled, "nothing had settled halfway through")
        self.assertLess(max(settled), 14, "everything settled at once")
        self.assertEqual(settled, list(range(len(settled))))

    def test_the_words_cycle(self) -> None:
        cycle = self.module.SPLIT_FLAP_SPIN + self.module.SPLIT_FLAP_HOLD
        shown = [
            self.module._flap_word(cycle * step + 0.1, 15)
            for step in range(len(self.module.SPLIT_FLAP_WORDS))
        ]

        self.assertEqual(len(set(shown)), len(self.module.SPLIT_FLAP_WORDS))

    def test_a_settled_module_shows_one_character_in_both_halves(self) -> None:
        """It used to keep the previous character below the seam, which spelled
        every word with mismatched halves."""
        settled = self.module._flap_module((72, 72), "Q", "A", 1.0)
        clean = self.module._flap_module((72, 72), "A", "A", 1.0)

        self.assertIsNone(ImageChops.difference(settled, clean).getbbox())

    def test_a_turning_module_does_show_both_characters(self) -> None:
        mid_turn = self.module._flap_module((72, 72), "Q", "A", 0.25)
        clean = self.module._flap_module((72, 72), "A", "A", 1.0)

        self.assertIsNotNone(ImageChops.difference(mid_turn, clean).getbbox())

    def test_a_smaller_deck_still_gets_a_full_board(self) -> None:
        frame = screensaver_frame("split_flap", 3.0, 6, (72, 72), 60, columns=3)

        self.assertEqual(len(frame.images), 6)
        self.assertTrue(all(image.getbbox() is not None for image in frame.images))


class MatrixScreenSaverTests(unittest.TestCase):
    """Columns of real glyphs raining down black, not abstract dashes."""

    def setUp(self) -> None:
        from linuxstreamdeck.device import screensaver as module

        self.module = module
        self.addCleanup(self._clear_font_caches)
        self._clear_font_caches()

    def _clear_font_caches(self) -> None:
        for cached in (
            self.module._matrix_font_path,
            self.module._matrix_alphabet,
            self.module._matrix_font,
            self.module._matrix_glyph,
        ):
            cached.cache_clear()

    @staticmethod
    def _frame(elapsed: float, intensity: int = 100):
        return screensaver_frame("matrix_code", elapsed, 15, (72, 72), intensity)

    def test_the_style_is_installed(self) -> None:
        self.assertIn(
            "matrix_code", [choice[0] for choice in SCREENSAVER_CHOICES]
        )

    def test_the_rain_is_green_on_black(self) -> None:
        red, green, blue = ImageStat.Stat(
            _deck_image(self._frame(0.9).images)
        ).mean

        self.assertGreater(green, 6)
        self.assertGreater(green, red * 3)
        self.assertGreater(green, blue * 2)

    def test_it_reaches_every_key_rather_than_one_corner(self) -> None:
        images = self._frame(0.9).images

        lit = sum(1 for image in images if image.getbbox() is not None)
        self.assertGreaterEqual(lit, 12)

    def test_the_leading_cell_of_a_stream_is_near_white(self) -> None:
        """What reads as the front of the stream, not just brighter green."""
        deck = _deck_image(self._frame(0.9).images)
        whitest = max(
            deck.getcolors(maxcolors=360 * 216),
            key=lambda entry: min(entry[1]),
        )[1]

        self.assertGreater(min(whitest), 150)

    def test_the_same_moment_always_paints_the_same_frame(self) -> None:
        """Frames are generated from `elapsed`, so they cannot drift."""
        for before, after in zip(self._frame(1.4).images, self._frame(1.4).images):
            self.assertIsNone(ImageChops.difference(before, after).getbbox())

    def test_the_glyphs_mutate_without_the_whole_screen_changing(self) -> None:
        first = _deck_image(self._frame(2.0).images)
        # A twelfth of a second: far too short for a stream to advance a cell,
        # so any change is glyphs swapping in place.
        second = _deck_image(self._frame(2.08).images)

        self.assertIsNotNone(ImageChops.difference(first, second).getbbox())
        changed = ImageChops.difference(first, second).convert("L")
        moved = sum(1 for value in changed.histogram()[24:])
        self.assertGreater(moved, 0)

    def test_katakana_is_used_when_a_japanese_font_is_installed(self) -> None:
        if not self.module._matrix_font_path():
            self.skipTest("no Japanese font on this machine")

        self.assertIn(
            self.module.MATRIX_KATAKANA[0], self.module._matrix_alphabet()
        )

    def test_it_still_renders_with_no_japanese_font_at_all(self) -> None:
        """The .deb only suggests one, so most machines may not have it."""
        with patch.object(self.module, "_CJK_FONT_CANDIDATES", ()):
            self._clear_font_caches()
            alphabet = self.module._matrix_alphabet()
            frame = self._frame(0.9)

            self.assertEqual(self.module._matrix_font_path(), "")
            self.assertNotIn(self.module.MATRIX_KATAKANA[0], alphabet)
            self.assertIn("A", alphabet)
            self.assertTrue(
                any(image.getbbox() is not None for image in frame.images),
                "the fallback alphabet rendered nothing at all",
            )

    def test_a_font_without_katakana_is_not_accepted(self) -> None:
        """Being on disk is not enough; it has to draw the glyph."""
        with patch.object(
            self.module,
            "_CJK_FONT_CANDIDATES",
            self.module._FONT_CANDIDATES,     # Latin-only DejaVu and friends
        ):
            self._clear_font_caches()
            self.assertEqual(self.module._matrix_font_path(), "")


def _deck_image(images) -> "object":
    """The 15 key images reassembled into the 5x3 canvas they were split from."""
    from PIL import Image

    deck = Image.new("RGB", (5 * 72, 3 * 72))
    for index, image in enumerate(images):
        deck.paste(image, ((index % 5) * 72, (index // 5) * 72))
    return deck


class HalScreenSaverTests(unittest.TestCase):
    """A single red eye, centered and still, breathing on black."""

    @staticmethod
    def _frame(elapsed: float, intensity: int = 100):
        return screensaver_frame("hal_9000", elapsed, 15, (72, 72), intensity)

    @staticmethod
    def _mean(image) -> list[float]:
        """Average red, green and blue of one key."""
        return ImageStat.Stat(image).mean

    def test_the_style_is_installed(self) -> None:
        self.assertIn(
            "hal_9000", [choice[0] for choice in SCREENSAVER_CHOICES]
        )

    def test_the_eye_sits_on_the_middle_key(self) -> None:
        images = self._frame(0.0).images
        # 5 columns x 3 rows: key 7 is the center, keys 0/4/10/14 the corners.
        center = self._mean(images[7])[0]

        for corner in (0, 4, 10, 14):
            self.assertLess(
                self._mean(images[corner])[0],
                center,
                f"key {corner} is not darker than the center",
            )

    def test_the_eye_is_red(self) -> None:
        red, green, blue = self._mean(self._frame(0.0).images[7])

        self.assertGreater(red, 40)
        self.assertGreater(red, green * 2)
        self.assertGreater(red, blue * 2)

    def test_the_light_falls_off_across_the_deck(self) -> None:
        """The eye lights what is around it, dimmer the further out it goes."""
        images = self._frame(2.2).images
        # Middle row, from the eye outward: center, its neighbour, the far key.
        eye, neighbour, far = (self._mean(images[i])[0] for i in (7, 6, 5))

        self.assertGreater(eye, neighbour)
        self.assertGreater(neighbour, far)
        # Lit, not black: a key that catches nothing looks cut out of the deck.
        self.assertGreater(far, 1.0)
        # Still clearly a dark deck with one eye on it, not a red wash.
        self.assertLess(far, eye * 0.15)

    def test_the_surroundings_brighten_with_the_eye(self) -> None:
        """One light source: the spill has to breathe with what casts it."""
        dim, bright = self._frame(6.8).images, self._frame(2.2).images

        for key in (5, 6, 8, 9, 0, 14):
            self.assertGreater(
                self._mean(bright[key])[0],
                self._mean(dim[key])[0],
                f"key {key} does not follow the breath",
            )

    def test_the_eye_is_centered_left_to_right_and_top_to_bottom(self) -> None:
        images = self._frame(0.0).images
        brightness = [self._mean(image)[0] for image in images]

        # Mirrored keys carry the same amount of light.
        for left, right in ((5, 9), (6, 8), (0, 4), (11, 13)):
            self.assertAlmostEqual(
                brightness[left], brightness[right], delta=0.5
            )
        for top, bottom in ((1, 11), (2, 12), (3, 13)):
            self.assertAlmostEqual(
                brightness[top], brightness[bottom], delta=0.5
            )

    def test_the_eye_stays_put_and_only_its_glow_changes(self) -> None:
        """It watches; it never scans or looks around."""
        dim = self._frame(6.8).images[7]
        bright = self._frame(2.2).images[7]

        self.assertGreater(self._mean(bright)[0], self._mean(dim)[0])
        # Same shape in both: the lit area does not move or resize much.
        self.assertEqual(dim.getbbox(), bright.getbbox())

    def test_the_breathing_reaches_the_device_brightness(self) -> None:
        levels = {self._frame(t, 60).brightness for t in (0.0, 2.2, 4.5, 6.8)}

        self.assertGreater(len(levels), 1)
        for level in levels:
            self.assertGreaterEqual(level, 1)
            self.assertLessEqual(level, 60)


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
