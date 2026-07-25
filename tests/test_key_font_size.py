from __future__ import annotations

import unittest
from types import SimpleNamespace

from linuxstreamdeck import basic_actions as _basic_actions  # noqa: F401
from linuxstreamdeck.core.config import (
    KEY_FONT_SIZE_AUTO,
    KEY_FONT_SIZE_CHOICES,
    KIND_MULTI,
    KIND_SINGLE,
    KIND_TOGGLE,
    ActionStep,
    Config,
    KeyConfig,
)
from linuxstreamdeck.core.controller import DeckController
from linuxstreamdeck.core.events import EventBus
from linuxstreamdeck.device.renderer import _label_font_size, compose


class FakeDeck:
    key_count = 15
    image_size = (72, 72)

    def __init__(self) -> None:
        self.images = {}
        self.screensaver_active = False

    def set_key_image(self, index, image) -> None:
        self.images[index] = image

    def record_activity(self) -> bool:
        return self.screensaver_active

    def set_brightness(self, _value) -> None:
        pass

    def configure_screensaver(self, *_args) -> None:
        pass


class FontSizeModelTests(unittest.TestCase):
    def test_default_is_automatic(self) -> None:
        key = KeyConfig()

        self.assertEqual(key.font_size, KEY_FONT_SIZE_AUTO)
        self.assertEqual(key.font_size_off, KEY_FONT_SIZE_AUTO)

    def test_known_sizes_survive_a_serialization_round_trip(self) -> None:
        for size, _name in KEY_FONT_SIZE_CHOICES:
            with self.subTest(size=size):
                restored = KeyConfig.from_dict(
                    {
                        "kind": KIND_SINGLE,
                        "action": "sys.wait",
                        "font_size": size,
                        "font_size_off": size,
                    }
                )

                self.assertEqual(restored.font_size, size)
                self.assertEqual(restored.font_size_off, size)

    def test_unknown_size_falls_back_to_automatic(self) -> None:
        restored = KeyConfig.from_dict(
            {"kind": KIND_SINGLE, "font_size": "enormous", "font_size_off": 42}
        )

        self.assertEqual(restored.font_size, KEY_FONT_SIZE_AUTO)
        self.assertEqual(restored.font_size_off, KEY_FONT_SIZE_AUTO)

    def test_size_is_normalized(self) -> None:
        restored = KeyConfig.from_dict({"kind": KIND_SINGLE, "font_size": "  XL "})

        self.assertEqual(restored.font_size, "xl")

    def test_missing_field_keeps_old_configurations_loadable(self) -> None:
        restored = KeyConfig.from_dict({"kind": KIND_SINGLE, "action": "sys.wait"})

        self.assertEqual(restored.font_size, KEY_FONT_SIZE_AUTO)


class FontSizeRenderingTests(unittest.TestCase):
    def test_named_sizes_are_ordered_and_bounded(self) -> None:
        sizes = [_label_font_size(72, size) for size in ("xs", "s", "m", "l", "xl")]

        self.assertEqual(sizes, sorted(sizes))
        self.assertEqual(len(set(sizes)), len(sizes))
        self.assertGreaterEqual(min(sizes), 8)

    def test_automatic_matches_the_previous_behaviour(self) -> None:
        for height in (72, 96, 120):
            with self.subTest(height=height):
                self.assertEqual(
                    _label_font_size(height, KEY_FONT_SIZE_AUTO),
                    max(10, height // 6),
                )

    def test_unknown_size_renders_as_automatic(self) -> None:
        self.assertEqual(
            _label_font_size(72, "enormous"),
            _label_font_size(72, KEY_FONT_SIZE_AUTO),
        )

    def test_sizes_scale_with_the_key_height(self) -> None:
        self.assertLess(_label_font_size(72, "l"), _label_font_size(144, "l"))

    def test_compose_accepts_every_choice(self) -> None:
        for size, _name in KEY_FONT_SIZE_CHOICES:
            with self.subTest(size=size):
                image = compose(
                    size=(72, 72),
                    label="Record",
                    icon_path="mdi:record",
                    font_size=size,
                )

                self.assertEqual(image.size, (72, 72))

    def test_a_larger_size_draws_a_taller_label(self) -> None:
        def label_rows(size: str) -> int:
            image = compose(size=(72, 72), label="Rec", bg="#000000", font_size=size)
            pixels = image.load()
            return sum(
                1
                for y in range(72)
                if any(pixels[x, y] != (0, 0, 0) for x in range(72))
            )

        self.assertGreater(label_rows("xl"), label_rows("xs"))


class FontSizeControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = Config()
        self.bus = EventBus()
        self.deck = FakeDeck()
        self.controller = DeckController(
            self.config, self.bus, SimpleNamespace(), self.deck
        )

    def tearDown(self) -> None:
        self.controller.shutdown()

    def _spec(self, key: KeyConfig) -> dict:
        return self.controller._key_spec(0, key, self.deck.image_size)

    def test_single_key_passes_its_font_size(self) -> None:
        spec = self._spec(
            KeyConfig(kind=KIND_SINGLE, action="sys.wait", font_size="l")
        )

        self.assertEqual(spec["font_size"], "l")

    def test_multi_key_passes_its_font_size(self) -> None:
        spec = self._spec(
            KeyConfig(
                kind=KIND_MULTI,
                steps=[ActionStep(action="sys.wait")],
                font_size="s",
            )
        )

        self.assertEqual(spec["font_size"], "s")

    def test_toggle_off_state_inherits_the_on_size(self) -> None:
        key = KeyConfig(
            kind=KIND_TOGGLE,
            steps_on=[ActionStep(action="sys.wait")],
            steps_off=[ActionStep(action="sys.wait")],
            font_size="xl",
        )

        self.assertEqual(self._spec(key)["font_size"], "xl")

    def test_toggle_off_state_can_override_the_on_size(self) -> None:
        key = KeyConfig(
            kind=KIND_TOGGLE,
            steps_on=[ActionStep(action="sys.wait")],
            steps_off=[ActionStep(action="sys.wait")],
            font_size="xl",
            font_size_off="xs",
        )

        self.assertEqual(self._spec(key)["font_size"], "xs")
        self.controller._toggle[self.controller._tkey(0)] = True
        self.assertEqual(self._spec(key)["font_size"], "xl")


if __name__ == "__main__":
    unittest.main()
