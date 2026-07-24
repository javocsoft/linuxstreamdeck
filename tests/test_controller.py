from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace

from linuxstreamdeck import basic_actions as _basic_actions  # noqa: F401
from linuxstreamdeck.core.config import (
    KIND_MULTI,
    KIND_TOGGLE,
    ActionStep,
    Config,
    KeyConfig,
    Page,
)
from linuxstreamdeck.core.controller import DeckController
from linuxstreamdeck.core.events import EventBus
from linuxstreamdeck.device.renderer import compose


class FakeDeck:
    key_count = 15
    image_size = (72, 72)

    def __init__(self) -> None:
        self.images = {}

    def set_key_image(self, index, image) -> None:
        self.images[index] = image


class ControllerActivityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = Config()
        self.bus = EventBus()
        self.deck = FakeDeck()
        self.controller = DeckController(
            self.config,
            self.bus,
            SimpleNamespace(),
            self.deck,
        )

    def tearDown(self) -> None:
        self.controller.shutdown()

    def test_running_count_keeps_feedback_until_every_invocation_finishes(
        self,
    ) -> None:
        key = self.controller._tkey(0)

        self.controller._begin_running(key)
        self.controller._begin_running(key)
        self.assertEqual(self.controller._running[key], 2)
        self.assertEqual(
            self.controller._key_spec(
                0,
                KeyConfig(
                    kind=KIND_MULTI,
                    steps=[ActionStep(action="sys.wait")],
                ),
                self.deck.image_size,
            )["badge"],
            "RUN",
        )

        self.controller._end_running(key)
        self.assertTrue(self.controller._busy_state(0)[0])
        self.controller._end_running(key)
        self.assertFalse(self.controller._busy_state(0)[0])

    def test_running_feedback_is_cleared_in_run_steps_finally(self) -> None:
        key = self.controller._tkey(0)
        self.controller._begin_running(key)

        self.controller._run_steps([], 0, key)

        self.assertFalse(self.controller._busy_state(0)[0])

    def test_running_feedback_is_isolated_by_page(self) -> None:
        self.config.pages.append(Page(name="Page 2"))
        key = self.controller._tkey(0)
        self.controller._begin_running(key)

        self.config.current_page = 1
        self.assertFalse(self.controller._busy_state(0)[0])
        self.config.current_page = 0
        self.assertTrue(self.controller._busy_state(0)[0])

        self.controller._end_running(key)

    def test_toggle_restores_its_state_badge_after_running(self) -> None:
        key = self.controller._tkey(0)
        toggle = KeyConfig(
            kind=KIND_TOGGLE,
            steps_on=[ActionStep(action="sys.wait")],
        )
        self.controller._toggle[key] = True
        self.controller._begin_running(key)

        busy = self.controller._key_spec(0, toggle, self.deck.image_size)
        self.assertEqual(busy["badge"], "RUN")
        self.assertTrue(busy["active"])

        self.controller._end_running(key)
        idle = self.controller._key_spec(0, toggle, self.deck.image_size)
        self.assertEqual(idle["badge"], "ON")
        self.assertTrue(idle["active"])

    def test_rendering_is_not_starved_by_two_wait_actions(self) -> None:
        rendered = set()
        both_rendered = threading.Event()

        def on_image(_topic, data) -> None:
            rendered.add(data["index"])
            if {0, 1}.issubset(rendered):
                both_rendered.set()

        self.bus.subscribe("ui.key_image", on_image)
        wait_key = KeyConfig(
            kind=KIND_MULTI,
            steps=[
                ActionStep(
                    action="sys.wait",
                    params={"duration": "00:05"},
                )
            ],
        )
        self.config.pages[0].set_key(0, wait_key)
        self.config.pages[0].set_key(1, wait_key.clone())

        self.controller.press(0)
        self.controller.press(1)

        self.assertTrue(both_rendered.wait(0.75))
        self.assertTrue(self.controller._busy_state(0)[0])
        self.assertTrue(self.controller._busy_state(1)[0])

    def test_wait_action_pulses_while_it_is_running(self) -> None:
        images = set()
        pulse_rendered = threading.Event()

        def on_image(_topic, data) -> None:
            if data["index"] != 0:
                return
            images.add(data["png"])
            if len(images) >= 2:
                pulse_rendered.set()

        self.bus.subscribe("ui.key_image", on_image)
        self.config.pages[0].set_key(
            0,
            KeyConfig(
                kind=KIND_MULTI,
                steps=[
                    ActionStep(
                        action="sys.wait",
                        params={"duration": "00:05"},
                    )
                ],
            ),
        )

        self.controller.press(0)

        self.assertTrue(pulse_rendered.wait(1.5))
        self.assertTrue(self.controller._busy_state(0)[0])


class BusyRenderingTests(unittest.TestCase):
    def test_busy_phases_create_a_subtle_visible_change(self) -> None:
        low = compose(bg="#141418", busy=True, busy_phase=False)
        high = compose(bg="#141418", busy=True, busy_phase=True)

        low_center = low.getpixel((36, 36))
        high_center = high.getpixel((36, 36))
        low_halo = low.getpixel((36, 1))
        high_halo = high.getpixel((36, 1))

        self.assertNotEqual(low_halo, high_halo)
        self.assertNotEqual(low_center, high_center)
        self.assertLessEqual(
            max(abs(a - b) for a, b in zip(low_center, high_center)),
            8,
        )


if __name__ == "__main__":
    unittest.main()
