from __future__ import annotations

import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from linuxstreamdeck import basic_actions as _basic_actions  # noqa: F401
from linuxstreamdeck.core import controller as controller_module
from linuxstreamdeck.core.config import (
    KIND_PRESS,
    KIND_RANDOM,
    KIND_SINGLE,
    ActionStep,
    Config,
    KeyConfig,
)
from linuxstreamdeck.core.controller import DeckController
from linuxstreamdeck.core.events import EventBus

# Short enough to keep the suite fast, long enough to be reliable.
LONG = 0.08
DOUBLE = 0.06
SETTLE = 0.25


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


class GestureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = Config()
        self.bus = EventBus()
        self.deck = FakeDeck()
        self.controller = DeckController(
            self.config, self.bus, SimpleNamespace(connected=False), self.deck
        )
        self.ran: list = []
        # Capture what the controller decides to run instead of executing it.
        patcher = patch.object(
            DeckController,
            "_submit_steps",
            lambda _self, steps, index, show_running=True,
            stop_on_error=False: self.ran.append(
                [step.params.get("command") for step in steps]
            ),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        for name, value in (
            ("LONG_PRESS_SECONDS", LONG),
            ("DOUBLE_PRESS_SECONDS", DOUBLE),
        ):
            constant = patch.object(controller_module, name, value)
            constant.start()
            self.addCleanup(constant.stop)

    def tearDown(self) -> None:
        self.controller.shutdown()

    def _gesture_key(self) -> KeyConfig:
        return KeyConfig(
            kind=KIND_PRESS,
            steps_single=[ActionStep(action="sys.command", params={"command": "single"})],
            steps_double=[ActionStep(action="sys.command", params={"command": "double"})],
            steps_long=[ActionStep(action="sys.command", params={"command": "long"})],
        )

    def _tap(self, index: int = 0, hold: float = 0.0) -> None:
        self.controller.key_down(index)
        if hold:
            time.sleep(hold)
        self.controller.key_up(index)

    def test_a_quick_tap_runs_the_single_press_list(self) -> None:
        self.controller.page.set_key(0, self._gesture_key())

        self._tap()
        time.sleep(SETTLE)

        self.assertEqual(self.ran, [["single"]])

    # ---------- naming a gesture, for the editor's Test buttons ----------

    def test_a_plain_virtual_press_still_runs_the_single_list(self) -> None:
        """A click has no release to time, so nothing about it may change."""
        self.controller.page.set_key(0, self._gesture_key())

        self.controller.press(0)

        self.assertEqual(self.ran, [["single"]])

    def test_a_named_gesture_runs_that_list(self) -> None:
        self.controller.page.set_key(0, self._gesture_key())

        self.controller.press(0, controller_module.GESTURE_DOUBLE)
        self.controller.press(0, controller_module.GESTURE_LONG)
        self.controller.press(0, controller_module.GESTURE_SINGLE)

        self.assertEqual(self.ran, [["double"], ["long"], ["single"]])

    def test_an_unknown_gesture_falls_back_to_single(self) -> None:
        self.controller.page.set_key(0, self._gesture_key())

        self.controller.press(0, "nonsense")

        self.assertEqual(self.ran, [["single"]])

    def test_naming_a_gesture_on_a_plain_key_changes_nothing(self) -> None:
        self.controller.page.set_key(0, KeyConfig(
            kind=KIND_SINGLE, action="sys.command", params={"command": "plain"},
        ))

        self.controller.press(0, controller_module.GESTURE_LONG)

        self.assertEqual(self.ran, [["plain"]])

    def test_an_empty_gesture_list_runs_nothing(self) -> None:
        self.controller.page.set_key(0, KeyConfig(
            kind=KIND_PRESS,
            steps_single=[
                ActionStep(action="sys.command", params={"command": "single"})
            ],
        ))

        self.controller.press(0, controller_module.GESTURE_DOUBLE)

        self.assertEqual(self.ran, [])

    def test_holding_runs_the_long_press_list(self) -> None:
        self.controller.page.set_key(0, self._gesture_key())

        self._tap(hold=LONG * 2)
        time.sleep(SETTLE)

        self.assertEqual(self.ran, [["long"]])

    def test_two_quick_taps_run_the_double_press_list_only(self) -> None:
        self.controller.page.set_key(0, self._gesture_key())

        self._tap()
        self._tap()
        time.sleep(SETTLE)

        self.assertEqual(self.ran, [["double"]])

    def test_two_slow_taps_are_two_single_presses(self) -> None:
        self.controller.page.set_key(0, self._gesture_key())

        self._tap()
        time.sleep(DOUBLE * 3)
        self._tap()
        time.sleep(SETTLE)

        self.assertEqual(self.ran, [["single"], ["single"]])

    def test_an_empty_gesture_list_does_nothing(self) -> None:
        self.controller.page.set_key(
            0, KeyConfig(kind=KIND_PRESS, steps_long=[ActionStep(action="sys.command")])
        )

        self._tap()
        time.sleep(SETTLE)

        self.assertEqual(self.ran, [])

    def test_a_release_without_its_press_is_ignored(self) -> None:
        self.controller.page.set_key(0, self._gesture_key())

        self.controller.key_up(0)
        time.sleep(SETTLE)

        self.assertEqual(self.ran, [])

    def test_editing_the_key_cancels_a_pending_gesture(self) -> None:
        self.controller.page.set_key(0, self._gesture_key())

        self._tap()
        self.controller.key_config_changed(0)
        time.sleep(SETTLE)

        self.assertEqual(self.ran, [])

    def test_other_kinds_still_run_on_the_press(self) -> None:
        self.controller.page.set_key(
            0,
            KeyConfig(
                kind=KIND_SINGLE, action="sys.command", params={"command": "plain"}
            ),
        )

        self.controller.key_down(0)

        self.assertEqual(self.ran, [["plain"]])

    def test_a_release_of_a_plain_key_does_nothing_extra(self) -> None:
        self.controller.page.set_key(
            0,
            KeyConfig(
                kind=KIND_SINGLE, action="sys.command", params={"command": "plain"}
            ),
        )

        self.controller.key_down(0)
        self.controller.key_up(0)
        time.sleep(SETTLE)

        self.assertEqual(self.ran, [["plain"]])

    def test_a_virtual_press_runs_the_single_press_list(self) -> None:
        self.controller.page.set_key(0, self._gesture_key())

        self.controller.press(0)

        self.assertEqual(self.ran, [["single"]])

    def test_gestures_are_dropped_when_pages_change(self) -> None:
        self.controller.page.set_key(0, self._gesture_key())

        self._tap()
        self.controller._clear_time_actions()
        time.sleep(SETTLE)

        self.assertEqual(self.ran, [])


class RandomKeyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = Config()
        self.deck = FakeDeck()
        self.controller = DeckController(
            self.config, EventBus(), SimpleNamespace(connected=False), self.deck
        )
        self.ran: list = []
        patcher = patch.object(
            DeckController,
            "_submit_steps",
            lambda _self, steps, index, show_running=True,
            stop_on_error=False: self.ran.append(
                [step.params.get("command") for step in steps]
            ),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self.controller.shutdown()

    def _random_key(self, commands: list[str]) -> KeyConfig:
        return KeyConfig(
            kind=KIND_RANDOM,
            steps=[
                ActionStep(action="sys.command", params={"command": command})
                for command in commands
            ],
        )

    def test_exactly_one_action_runs_per_press(self) -> None:
        self.controller.page.set_key(0, self._random_key(["a", "b", "c"]))

        for _ in range(10):
            self.controller.press(0)

        self.assertEqual(len(self.ran), 10)
        self.assertTrue(all(len(run) == 1 for run in self.ran))

    def test_only_configured_actions_are_chosen(self) -> None:
        self.controller.page.set_key(0, self._random_key(["a", "b", "c"]))

        for _ in range(30):
            self.controller.press(0)

        self.assertTrue({run[0] for run in self.ran} <= {"a", "b", "c"})

    def test_every_action_is_reachable(self) -> None:
        self.controller.page.set_key(0, self._random_key(["a", "b", "c"]))

        with patch("random.choice", side_effect=lambda items: items[-1]):
            self.controller.press(0)

        self.assertEqual(self.ran, [["c"]])

    def test_a_single_action_always_runs(self) -> None:
        self.controller.page.set_key(0, self._random_key(["only"]))

        self.controller.press(0)

        self.assertEqual(self.ran, [["only"]])

    def test_an_empty_random_key_does_nothing(self) -> None:
        self.controller.page.set_key(0, KeyConfig(kind=KIND_RANDOM))

        self.controller.press(0)

        self.assertEqual(self.ran, [])


class KeySpecTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = Config()
        self.deck = FakeDeck()
        self.controller = DeckController(
            self.config, EventBus(), SimpleNamespace(connected=False), self.deck
        )

    def tearDown(self) -> None:
        self.controller.shutdown()

    def _spec(self, key: KeyConfig) -> dict:
        return self.controller._key_spec(0, key, self.deck.image_size)

    def test_a_random_key_has_its_own_badge_and_icon(self) -> None:
        spec = self._spec(
            KeyConfig(kind=KIND_RANDOM, steps=[ActionStep(action="sys.wait")])
        )

        self.assertEqual(spec["badge"], "?")
        self.assertTrue(spec["icon_path"])

    def test_a_gesture_key_has_its_own_badge(self) -> None:
        spec = self._spec(
            KeyConfig(kind=KIND_PRESS, steps_single=[ActionStep(action="sys.wait")])
        )

        self.assertEqual(spec["badge"], "⋮")

    def test_a_gesture_key_borrows_an_icon_from_any_list(self) -> None:
        spec = self._spec(
            KeyConfig(
                kind=KIND_PRESS,
                steps_long=[ActionStep(action="sys.timer")],
            )
        )

        self.assertEqual(spec["icon_path"], "mdi:timer-outline")


if __name__ == "__main__":
    unittest.main()
