"""Stream Deck + encoders and the LCD strip above them.

A dial reuses `KeyConfig` with a kind of its own, so every walk over a key's
actions and assets — portable bundles, page renames, legacy migration — reaches
a dial without being taught about it. What differs is storage: dials are
numbered independently of keys, so they live in their own mapping on the page
and their transient state is filed under a folder path no real folder can have.

None of this has been run on a physical Stream Deck +; it is verified here the
same way multi-model support is, by driving the code with what the library
reports.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from linuxstreamdeck import basic_actions as _basic_actions  # noqa: F401
from linuxstreamdeck.core.config import (
    DIAL_STEP_FIELDS,
    KIND_DIAL,
    KIND_SINGLE,
    MAX_DIALS,
    STEP_FIELDS,
    ActionStep,
    Config,
    KeyConfig,
    Page,
)
from linuxstreamdeck.core.controller import (
    DIAL_PATH,
    MAX_DIAL_TICKS,
    DeckController,
)
from linuxstreamdeck.core.events import EventBus
from linuxstreamdeck.device.manager import _dial_event, touch_segment
from linuxstreamdeck.device.touchscreen import segment_bounds, touchscreen_image
from linuxstreamdeck.obs import actions as _obs_actions  # noqa: F401


class FakeDeck:
    """A deck that reports four encoders."""

    key_count = 8
    image_size = (72, 72)
    columns = 4

    def __init__(self, dial_count: int = 4) -> None:
        self.images: dict[int, bytes] = {}
        self.screensaver_active = False
        self.dial_count = dial_count
        self.touch_size = (800, 100)
        self.strips: list = []

    def set_key_image(self, index, image) -> None:
        self.images[index] = image

    def set_touchscreen_image(self, image) -> None:
        self.strips.append(image)

    def record_activity(self) -> bool:
        return self.screensaver_active

    def set_brightness(self, _value) -> None:
        pass

    def configure_screensaver(self, *_args) -> None:
        pass


def dial(**steps) -> KeyConfig:
    config = KeyConfig(kind=KIND_DIAL)
    for field, actions in steps.items():
        setattr(config, field, [ActionStep(action=a) for a in actions])
    return config


class DialEventTests(unittest.TestCase):
    """Turning the library's two shapes of event into one direction."""

    def test_a_push_runs_on_the_way_down(self) -> None:
        self.assertEqual(_dial_event("DIAL_PUSH", True), ("press", 1))

    def test_a_release_does_nothing(self) -> None:
        """A dial acts on the press, like a key; the release is not a second one."""
        self.assertEqual(_dial_event("DIAL_PUSH", False), (None, 0))

    def test_a_positive_turn_is_right_and_a_negative_one_is_left(self) -> None:
        self.assertEqual(_dial_event("DIAL_TURN", 1), ("right", 1))
        self.assertEqual(_dial_event("DIAL_TURN", -1), ("left", 1))

    def test_a_fast_turn_keeps_its_tick_count(self) -> None:
        """Flattening it would make a quick spin move a volume by one step."""
        self.assertEqual(_dial_event("DIAL_TURN", 5), ("right", 5))
        self.assertEqual(_dial_event("DIAL_TURN", -4), ("left", 4))

    def test_a_turn_of_no_steps_is_ignored(self) -> None:
        self.assertEqual(_dial_event("DIAL_TURN", 0), (None, 0))

    def test_an_unparseable_value_is_ignored(self) -> None:
        self.assertEqual(_dial_event("DIAL_TURN", "sideways"), (None, 0))

    def test_an_unknown_event_is_ignored(self) -> None:
        self.assertEqual(_dial_event("SOMETHING_ELSE", 1), (None, 0))

    def test_an_enum_style_event_is_read_by_name(self) -> None:
        event = SimpleNamespace(name="TURN")

        self.assertEqual(_dial_event(event, 2), ("right", 2))


class TouchSegmentTests(unittest.TestCase):
    def test_each_quarter_of_the_strip_maps_to_its_dial(self) -> None:
        for x, expected in ((0, 0), (199, 0), (200, 1), (450, 2), (799, 3)):
            with self.subTest(x=x):
                self.assertEqual(touch_segment(x, 800, 4), expected)

    def test_a_position_outside_the_strip_is_discarded(self) -> None:
        self.assertIsNone(touch_segment(-1, 800, 4))
        self.assertIsNone(touch_segment(800, 800, 4))

    def test_a_deck_with_no_dials_has_no_segments(self) -> None:
        self.assertIsNone(touch_segment(10, 800, 0))

    def test_an_unusable_coordinate_is_discarded(self) -> None:
        self.assertIsNone(touch_segment(None, 800, 4))
        self.assertIsNone(touch_segment("left", 800, 4))


class SegmentBoundsTests(unittest.TestCase):
    def test_the_segments_tile_the_strip_exactly(self) -> None:
        edge = 0
        for index in range(4):
            left, _top, right, _bottom = segment_bounds(index, (800, 100), 4)
            self.assertEqual(left, edge)
            edge = right
        self.assertEqual(edge, 800)

    def test_a_width_that_does_not_divide_leaves_no_seam(self) -> None:
        """Rounded per-segment widths would leave a visible gap on black."""
        edge = 0
        for index in range(3):
            left, _top, right, _bottom = segment_bounds(index, (801, 100), 3)
            self.assertEqual(left, edge)
            edge = right
        self.assertEqual(edge, 801)

    def test_an_out_of_range_dial_is_clamped(self) -> None:
        self.assertEqual(
            segment_bounds(9, (800, 100), 4), segment_bounds(3, (800, 100), 4)
        )


class DialConfigTests(unittest.TestCase):
    def test_every_dial_field_is_walked_as_a_step_field(self) -> None:
        """Missing one silently loses a dial's bundled audio and migrations."""
        for field in DIAL_STEP_FIELDS:
            self.assertIn(field, STEP_FIELDS)

    def test_a_dial_with_no_actions_is_empty(self) -> None:
        self.assertTrue(KeyConfig(kind=KIND_DIAL).is_empty())

    def test_a_dial_with_any_gesture_is_configured(self) -> None:
        for field in DIAL_STEP_FIELDS:
            with self.subTest(field=field):
                self.assertFalse(dial(**{field: ["obs.record"]}).is_empty())

    def test_dials_survive_a_save_and_load(self) -> None:
        page = Page(name="Live")
        page.set_dial(1, dial(steps_right=["obs.volume_adjust"]))

        restored = Page.from_dict(
            {
                "name": page.name,
                "keys": {},
                "dials": {
                    "1": {
                        "kind": KIND_DIAL,
                        "steps_right": [
                            {"action": "obs.volume_adjust", "params": {}}
                        ],
                    }
                },
            }
        )

        self.assertEqual(restored.dial(1).steps_right[0].action,
                         "obs.volume_adjust")

    def test_a_configuration_without_dials_still_loads(self) -> None:
        restored = Page.from_dict({"name": "Live", "keys": {}})

        self.assertEqual(restored.dials, {})

    def test_a_dial_beyond_the_hardware_count_is_dropped(self) -> None:
        """A hand-edited file cannot invent a fifth encoder."""
        restored = Page.from_dict(
            {
                "name": "Live",
                "keys": {},
                "dials": {str(MAX_DIALS): {"kind": KIND_DIAL}},
            }
        )

        self.assertEqual(restored.dials, {})

    def test_clearing_a_dial_removes_it(self) -> None:
        page = Page(name="Live")
        page.set_dial(0, dial(steps_press=["obs.record"]))

        page.set_dial(0, None)

        self.assertIsNone(page.dial(0))

    def test_an_empty_dial_is_not_stored(self) -> None:
        page = Page(name="Live")

        page.set_dial(0, KeyConfig(kind=KIND_DIAL))

        self.assertEqual(page.dials, {})


class DialControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = Config()
        self.deck = FakeDeck()
        self.controller = DeckController(
            self.config, EventBus(), SimpleNamespace(connected=False), self.deck
        )
        self.addCleanup(self.controller.shutdown)
        self.ran: list[tuple] = []
        self.controller._submit_steps = (
            lambda steps, index, show_running, execution_key=None,
            stop_on_error=False: self.ran.append(
                (tuple(s.action for s in steps), index, execution_key)
            )
        )

    def test_each_gesture_runs_its_own_list(self) -> None:
        self.config.pages[0].set_dial(
            0,
            dial(
                steps_left=["obs.volume_adjust"],
                steps_right=["obs.record"],
                steps_press=["obs.mute"],
            ),
        )

        for direction, expected in (
            ("left", "obs.volume_adjust"),
            ("right", "obs.record"),
            ("press", "obs.mute"),
        ):
            with self.subTest(direction=direction):
                self.ran.clear()
                self.controller.turn_dial(0, direction)
                self.assertEqual(self.ran[0][0], (expected,))

    def test_a_turn_runs_once_per_tick(self) -> None:
        self.config.pages[0].set_dial(0, dial(steps_right=["obs.volume_adjust"]))

        self.controller.turn_dial(0, "right", ticks=3)

        self.assertEqual(len(self.ran), 3)

    def test_a_runaway_spin_is_bounded(self) -> None:
        """A flick reports many ticks; each one queues work on a worker."""
        self.config.pages[0].set_dial(0, dial(steps_right=["obs.volume_adjust"]))

        self.controller.turn_dial(0, "right", ticks=500)

        self.assertEqual(len(self.ran), MAX_DIAL_TICKS)

    def test_an_unconfigured_dial_does_nothing(self) -> None:
        self.controller.turn_dial(2, "right")

        self.assertEqual(self.ran, [])

    def test_a_gesture_with_an_empty_list_does_nothing(self) -> None:
        self.config.pages[0].set_dial(0, dial(steps_press=["obs.record"]))

        self.controller.turn_dial(0, "left")

        self.assertEqual(self.ran, [])

    def test_an_unknown_direction_does_nothing(self) -> None:
        self.config.pages[0].set_dial(0, dial(steps_press=["obs.record"]))

        self.controller.turn_dial(0, "inwards")

        self.assertEqual(self.ran, [])

    def test_a_dial_never_shares_state_with_the_key_of_the_same_number(self) -> None:
        self.config.pages[0].set_dial(0, dial(steps_press=["obs.record"]))

        self.controller.turn_dial(0, "press")

        _steps, _index, execution_key = self.ran[0]
        self.assertEqual(execution_key, self.controller._dial_tkey(0))
        self.assertNotEqual(execution_key, self.controller._tkey(0))

    def test_the_dial_namespace_can_never_be_a_real_folder(self) -> None:
        """A folder path holds key indices, which are never negative."""
        self.assertTrue(all(entry < 0 for entry in DIAL_PATH))

    def test_a_touch_presses_the_dial_under_it(self) -> None:
        self.config.pages[0].set_dial(2, dial(steps_press=["obs.mute"]))

        self.controller._on_deck_touch("deck.touch", {"index": 2, "event": "SHORT"})

        self.assertEqual(self.ran[0][0], ("obs.mute",))

    def test_a_bus_event_reaches_the_dial(self) -> None:
        self.config.pages[0].set_dial(1, dial(steps_right=["obs.record"]))

        self.controller._on_deck_dial(
            "deck.dial", {"index": 1, "direction": "right", "ticks": 2}
        )

        self.assertEqual(len(self.ran), 2)

    def test_dials_belong_to_the_page(self) -> None:
        self.config.pages.append(Page(name="Second"))
        self.config.pages[0].set_dial(0, dial(steps_press=["obs.record"]))
        self.config.pages[1].set_dial(0, dial(steps_press=["obs.stream"]))

        self.controller.set_page(1)
        self.controller.turn_dial(0, "press")

        self.assertEqual(self.ran[0][0], ("obs.stream",))


class DialRenderingTests(unittest.TestCase):
    def test_the_strip_is_the_size_the_device_reports(self) -> None:
        image = touchscreen_image({}, size=(800, 100), count=4)

        self.assertEqual(image.size, (800, 100))

    def test_a_configured_dial_is_drawn(self) -> None:
        blank = touchscreen_image({}, size=(800, 100), count=4)

        filled = touchscreen_image(
            {0: dial(steps_press=["obs.record"])}, size=(800, 100), count=4
        )

        self.assertNotEqual(blank.tobytes(), filled.tobytes())

    def test_a_live_value_changes_what_is_drawn(self) -> None:
        dials = {0: dial(steps_right=["obs.volume_adjust"])}
        without = touchscreen_image(dials, size=(800, 100), count=4)

        with_value = touchscreen_image(
            dials, values={0: "72%"}, size=(800, 100), count=4
        )

        self.assertNotEqual(without.tobytes(), with_value.tobytes())

    def test_the_strip_is_refreshed_with_the_page(self) -> None:
        config = Config()
        deck = FakeDeck()
        controller = DeckController(
            config, EventBus(), SimpleNamespace(connected=False), deck
        )
        self.addCleanup(controller.shutdown)
        config.pages[0].set_dial(0, dial(steps_press=["obs.record"]))

        controller._render_page()

        self.assertTrue(deck.strips)

    def test_a_deck_without_dials_is_never_drawn_to(self) -> None:
        config = Config()
        deck = FakeDeck(dial_count=0)
        controller = DeckController(
            config, EventBus(), SimpleNamespace(connected=False), deck
        )
        self.addCleanup(controller.shutdown)

        controller._render_page()

        self.assertEqual(deck.strips, [])


class DialBundleTests(unittest.TestCase):
    """A dial holds actions, so everything that walks a key must reach it."""

    def test_a_dial_is_visited_when_walking_the_configuration(self) -> None:
        config = Config()
        config.pages[0].set_dial(0, dial(steps_press=["obs.record"]))

        walked = list(config._key_configs())

        self.assertTrue(
            any(entry.kind == KIND_DIAL for entry in walked),
            "dials are skipped, so their icons and audio would not be bundled",
        )

    def test_renaming_a_page_rewrites_a_dial_that_navigates_to_it(self) -> None:
        config = Config()
        config.pages.append(Page(name="BRB"))
        target = KeyConfig(kind=KIND_DIAL)
        target.steps_press = [
            ActionStep(action="nav.page.go", params={"page": "BRB"})
        ]
        config.pages[0].set_dial(0, target)
        controller = DeckController(
            config, EventBus(), SimpleNamespace(connected=False), FakeDeck()
        )
        self.addCleanup(controller.shutdown)
        controller.set_page(1)

        controller.rename_page("Away")

        self.assertEqual(
            config.pages[0].dial(0).steps_press[0].params["page"], "Away"
        )

    def test_duplicating_a_page_copies_its_dials(self) -> None:
        config = Config()
        config.pages[0].set_dial(0, dial(steps_press=["obs.record"]))
        controller = DeckController(
            config, EventBus(), SimpleNamespace(connected=False), FakeDeck()
        )
        self.addCleanup(controller.shutdown)

        controller.duplicate_page(0)

        copied = config.pages[1].dial(0)
        self.assertIsNotNone(copied)
        self.assertIsNot(copied, config.pages[0].dial(0))

    def test_a_single_key_kind_is_unaffected_by_the_new_fields(self) -> None:
        key = KeyConfig(kind=KIND_SINGLE, action="obs.record")

        self.assertFalse(key.is_empty())
        self.assertEqual(key.steps_left, [])


if __name__ == "__main__":
    unittest.main()
