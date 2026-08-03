"""A key that counts something: deaths, takes, attempts.

The count is transient key state, exactly like a toggle's ON/OFF, so most of
what is pinned here is that it follows the same lifecycle: it travels with the
key when it moves, it is dropped when the key's configuration is replaced, and
it survives a page change -- which is the one people would notice, because a
tally is kept over a session and a page switch happens by accident.
"""

from __future__ import annotations

import unittest
import unittest.mock
from types import SimpleNamespace

from linuxstreamdeck import basic_actions
from linuxstreamdeck.core.actions import REGISTRY
from linuxstreamdeck.core.controller import DeckController


class _Controller:
    """A DeckController reduced to the counter methods and their state."""

    def __init__(self) -> None:
        self._counters: dict = {}
        self._toggle: dict = {}
        self.refreshed: list = []
        for name in ("counter_value", "bump_counter", "reset_counter",
                     "_swap_key_state"):
            setattr(self, name,
                    getattr(DeckController, name).__get__(self, type(self)))

    def _refresh_runtime_keys(self, keys) -> None:
        self.refreshed.append(tuple(keys))

    # What _swap_key_state also touches, reduced to nothing.
    _clocks = SimpleNamespace(swap=lambda *a: None)

    def _cancel_timer_sound(self, key) -> None:
        pass

    def _forget_failure(self, key) -> None:
        pass

    def _cancel_gesture(self, key) -> None:
        pass


KEY_A = (0, 0, (), 3)
KEY_B = (0, 0, (), 7)


class CounterStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = _Controller()

    def test_an_untouched_counter_shows_where_it_starts(self) -> None:
        self.assertEqual(self.controller.counter_value(KEY_A, 10), 10)

    def test_a_press_adds_the_step(self) -> None:
        self.controller.bump_counter(KEY_A, 1, 0)
        self.controller.bump_counter(KEY_A, 1, 0)

        self.assertEqual(self.controller.counter_value(KEY_A), 2)

    def test_a_negative_step_counts_down(self) -> None:
        self.controller.bump_counter(KEY_A, -1, 10)

        self.assertEqual(self.controller.counter_value(KEY_A, 10), 9)

    def test_resetting_goes_back_to_the_starting_value(self) -> None:
        self.controller.bump_counter(KEY_A, 5, 100)

        self.assertEqual(self.controller.reset_counter(KEY_A, 100), 100)
        self.assertEqual(self.controller.counter_value(KEY_A, 100), 100)

    def test_two_keys_count_separately(self) -> None:
        self.controller.bump_counter(KEY_A, 1, 0)

        self.assertEqual(self.controller.counter_value(KEY_B), 0)

    def test_changing_the_starting_value_moves_an_untouched_counter(self) -> None:
        """It is read from the key's own configuration rather than stored, and
        editing the key resets the count anyway."""
        self.assertEqual(self.controller.counter_value(KEY_A, 3), 3)
        self.assertEqual(self.controller.counter_value(KEY_A, 8), 8)

    def test_a_counter_that_was_pressed_keeps_its_own_value(self) -> None:
        self.controller.bump_counter(KEY_A, 1, 0)

        self.assertEqual(self.controller.counter_value(KEY_A, 50), 1)

    def test_a_key_with_no_identity_never_stores_anything(self) -> None:
        """A dial or a test press from the editor can have none."""
        self.assertEqual(self.controller.bump_counter(None, 1, 4), 4)
        self.assertEqual(self.controller.reset_counter(None, 4), 4)
        self.assertEqual(self.controller.counter_value(None, 4), 4)
        self.assertEqual(self.controller._counters, {})

    def test_a_change_repaints_the_key(self) -> None:
        """Nothing else would: the value changes on the press, and no clock
        repaints this key."""
        self.controller.bump_counter(KEY_A, 1, 0)
        self.controller.reset_counter(KEY_A, 0)

        self.assertEqual(self.controller.refreshed, [(KEY_A,), (KEY_A,)])

    def test_a_count_travels_with_the_key_that_moves(self) -> None:
        """It belongs to that key, not to the position it sat in."""
        self.controller.bump_counter(KEY_A, 7, 0)

        self.controller._swap_key_state(KEY_A, KEY_B)

        self.assertEqual(self.controller.counter_value(KEY_B), 7)
        self.assertEqual(self.controller.counter_value(KEY_A), 0)

    def test_two_counts_swap_rather_than_one_overwriting_the_other(self) -> None:
        self.controller.bump_counter(KEY_A, 3, 0)
        self.controller.bump_counter(KEY_B, 9, 0)

        self.controller._swap_key_state(KEY_A, KEY_B)

        self.assertEqual(self.controller.counter_value(KEY_A), 9)
        self.assertEqual(self.controller.counter_value(KEY_B), 3)


class LifecycleTests(unittest.TestCase):
    """The counter has to be dropped everywhere the other transient key state
    is, or a stored index ends up naming a different key."""

    def _source(self, name: str) -> str:
        import inspect

        return inspect.getsource(getattr(DeckController, name))

    def test_replacing_a_key_drops_its_count(self) -> None:
        source = self._source("key_config_changed")

        self.assertIn("self._counters.pop(key, None)", source)
        self.assertIn("self._toggle.pop(key, None)", source)

    def test_clearing_a_folder_drops_the_counts_inside_it(self) -> None:
        source = self._source("_discard_folder_state")

        self.assertIn("self._counters", source)

    def test_an_index_shift_clears_every_count(self) -> None:
        """Deleting a page or profile, moving one, duplicating one, or
        replacing the whole configuration: a stored index then means a
        different key."""
        import inspect

        from linuxstreamdeck.core import controller as module

        text = inspect.getsource(module)
        self.assertEqual(
            text.count("self._counters.clear()"),
            text.count("self._toggle.clear()"),
        )

    def test_a_page_change_keeps_the_count(self) -> None:
        """The one people would notice: a tally is kept over a session, and a
        page switch happens by accident."""
        source = self._source("set_page")

        self.assertNotIn("_counters", source)
        self.assertNotIn("_clear_time_actions", source)


class ActionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.action = REGISTRY["sys.counter"]
        self.controller = _Controller()
        self.messages: list[str] = []
        self.ctx = SimpleNamespace(
            controller=self.controller,
            key=KEY_A,
            bus=SimpleNamespace(
                emit=lambda topic, **d: self.messages.append(d.get("text", ""))
            ),
        )

    def test_pressing_it_counts_up(self) -> None:
        self.action.execute(self.ctx, {"step": 1, "start": 0})

        self.assertEqual(self.action.feedback(self.ctx, {})["display"], "1")

    def test_the_key_shows_its_starting_value_before_any_press(self) -> None:
        self.assertEqual(
            self.action.feedback(self.ctx, {"start": 12})["display"], "12"
        )

    def test_holding_it_resets(self) -> None:
        """A second key just to reset a counter is a key spent on something
        that happens once a session."""
        self.action.execute(self.ctx, {"step": 1})
        self.action.execute(self.ctx, {"step": 1})

        handled = self.action.long_press(self.ctx, {"start": 0})

        self.assertTrue(handled)
        self.assertEqual(self.action.feedback(self.ctx, {})["display"], "0")

    def test_a_long_press_claims_the_gesture(self) -> None:
        """Returning False would let the controller run the normal press as
        well, so holding the key would reset it and then add one."""
        self.assertIs(self.action.long_press(self.ctx, {}), True)

    def test_the_press_reports_the_new_value(self) -> None:
        self.action.execute(self.ctx, {"step": 5})

        self.assertIn("5", self.messages[-1])

    def test_a_step_of_zero_would_be_a_key_that_does_nothing(self) -> None:
        """It looks configured and never changes anything, so it counts as the
        default rather than as a choice."""
        self.action.execute(self.ctx, {"step": 0})

        self.assertEqual(self.action.feedback(self.ctx, {})["display"], "1")

    def test_rubbish_in_the_parameters_never_raises(self) -> None:
        for params in ({"step": "many"}, {"start": None}, {"step": []}, {}):
            with self.subTest(params=params):
                self.action.execute(self.ctx, params)
                self.action.feedback(self.ctx, params)

    def test_a_hand_edited_value_is_bounded(self) -> None:
        """Only so a value too wide to draw on a key cannot be configured."""
        self.assertEqual(
            basic_actions._counter_step({"step": 10_000_000}),
            basic_actions.MAX_COUNTER_STEP,
        )
        self.assertEqual(
            basic_actions._counter_start({"start": -10_000_000}),
            -basic_actions.MAX_COUNTER_VALUE,
        )

    def test_it_is_immediate(self) -> None:
        """Adding to a number in memory. An action worker for that would make
        it the slowest key on the deck to answer."""
        self.assertTrue(self.action.immediate)

    def test_it_needs_neither_obs_nor_twitch(self) -> None:
        self.assertFalse(self.action.needs_obs)
        self.assertFalse(self.action.needs_twitch)

    def test_it_is_never_repainted_on_a_clock(self) -> None:
        """Its value changes only on a press, and the press repaints it."""
        from linuxstreamdeck.core.config import KIND_SINGLE, KeyConfig

        controller = SimpleNamespace(
            obs=SimpleNamespace(connected=False), _twitch_linked=lambda: False
        )

        self.assertEqual(
            DeckController._live_interval(
                controller,
                KeyConfig(kind=KIND_SINGLE, action="sys.counter", params={}),
            ),
            0.0,
        )
