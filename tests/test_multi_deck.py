"""Only the first Stream Deck is used, and that is now said out loud.

`_try_open()` enumerates every device and keeps the first. Dropping the rest
without a word was indistinguishable from a second deck that had failed, so the
limitation is reported. It must not become status-bar noise: the scan runs every
few seconds for as long as nothing is connected.
"""

from __future__ import annotations

import unittest

from linuxstreamdeck.core.events import EventBus
from linuxstreamdeck.device.manager import (
    OPEN_FAILURES_BEFORE_WARNING,
    DeckManager,
    _device_columns,
    _is_visual,
)
from linuxstreamdeck.device.startup_animation import (
    TITLE,
    TITLE_FORMS,
    title_layout as _title_layout,
)


class FakeDevice:
    def __init__(
        self,
        path: str,
        kind: str = "Stream Deck Original V2",
        layout: tuple[int, int] = (3, 5),
        visual: bool = True,
    ) -> None:
        self._path = path
        self._kind = kind
        self._layout = layout
        self._visual = visual

    def id(self) -> str:
        return self._path

    def deck_type(self) -> str:
        return self._kind

    def key_layout(self) -> tuple[int, int]:
        return self._layout

    def is_visual(self) -> bool:
        return self._visual


class SilentDevice(FakeDevice):
    """A device whose driver answers nothing useful before it is opened."""

    def id(self):
        raise RuntimeError("not open")

    def deck_type(self):
        raise RuntimeError("not open")


class ExtraDeviceNoticeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.messages: list[str] = []
        bus = EventBus()
        bus.subscribe("status", lambda _t, d: self.messages.append(d["text"]))
        self.manager = DeckManager(bus)

    def _report(self, *devices) -> None:
        self.manager._report_extra_devices(list(devices))

    def test_one_deck_says_nothing(self) -> None:
        self._report(FakeDevice("/dev/a"))
        self.assertEqual(self.messages, [])

    def test_no_deck_says_nothing(self) -> None:
        self._report()
        self.assertEqual(self.messages, [])

    def test_two_decks_are_reported_once(self) -> None:
        self._report(FakeDevice("/dev/a"), FakeDevice("/dev/b"))

        self.assertEqual(len(self.messages), 1)
        self.assertIn("2 Stream Decks found", self.messages[0])
        self.assertIn("Only the first", self.messages[0])

    def test_the_notice_names_the_deck_that_is_actually_used(self) -> None:
        self._report(
            FakeDevice("/dev/a", "Stream Deck XL"),
            FakeDevice("/dev/b", "Stream Deck Mini"),
        )

        self.assertIn("Stream Deck XL", self.messages[0])
        self.assertNotIn("Mini", self.messages[0])

    def test_rescanning_the_same_decks_does_not_repeat_it(self) -> None:
        """The scan loop runs every few seconds while nothing is connected."""
        for _ in range(5):
            self._report(FakeDevice("/dev/a"), FakeDevice("/dev/b"))

        self.assertEqual(len(self.messages), 1)

    def test_the_order_devices_are_enumerated_in_does_not_repeat_it(self) -> None:
        self._report(FakeDevice("/dev/a"), FakeDevice("/dev/b"))
        self._report(FakeDevice("/dev/b"), FakeDevice("/dev/a"))

        self.assertEqual(len(self.messages), 1)

    def test_plugging_in_another_deck_reports_again(self) -> None:
        self._report(FakeDevice("/dev/a"), FakeDevice("/dev/b"))
        self._report(
            FakeDevice("/dev/a"), FakeDevice("/dev/b"), FakeDevice("/dev/c")
        )

        self.assertEqual(len(self.messages), 2)
        self.assertIn("3 Stream Decks found", self.messages[1])

    def test_unplugging_back_to_one_arms_the_notice_again(self) -> None:
        self._report(FakeDevice("/dev/a"), FakeDevice("/dev/b"))
        self._report(FakeDevice("/dev/a"))
        self._report(FakeDevice("/dev/a"), FakeDevice("/dev/b"))

        self.assertEqual(len(self.messages), 2)

    def test_a_device_that_answers_nothing_is_still_reported(self) -> None:
        """Naming it must never be what stops the warning being given."""
        self._report(SilentDevice("/dev/a"), SilentDevice("/dev/b"))

        self.assertEqual(len(self.messages), 1)
        self.assertIn("Stream Deck", self.messages[0])


class DeckGeometryTests(unittest.TestCase):
    """Full-deck images are split along the deck's real column count.

    Assuming five columns scrambled the screen saver, the startup sequence and
    the custom exit image on every model that is not the 15-key original: a Mini
    has three columns and an XL has eight.
    """

    def test_the_column_count_comes_from_the_device(self) -> None:
        for kind, layout, expected in (
            ("Stream Deck Mini", (2, 3), 3),
            ("Stream Deck Original V2", (3, 5), 5),
            ("Stream Deck XL", (4, 8), 8),
            ("Stream Deck +", (2, 4), 4),
        ):
            device = FakeDevice("/dev/a", kind, layout=layout)
            self.assertEqual(
                _device_columns(device, 15), expected, kind
            )

    def test_a_driver_that_cannot_say_falls_back_sanely(self) -> None:
        """Never more columns than there are keys, whatever happens."""
        class Mute(FakeDevice):
            def key_layout(self):
                raise RuntimeError("no layout")

        self.assertEqual(_device_columns(Mute("/dev/a"), 15), 5)
        self.assertEqual(_device_columns(Mute("/dev/a"), 3), 3)

    def test_a_nonsense_layout_is_not_trusted(self) -> None:
        device = FakeDevice("/dev/a", layout=(1, 0))
        self.assertGreaterEqual(_device_columns(device, 15), 1)

    def test_every_model_splits_into_exactly_its_own_keys(self) -> None:
        from linuxstreamdeck.device.exit_display import blank_exit_tiles
        from linuxstreamdeck.device.screensaver import screensaver_frame
        from linuxstreamdeck.device.startup_animation import startup_frames

        for name, keys, columns in (
            ("Mini", 6, 3), ("Neo", 8, 4), ("Original", 15, 5), ("XL", 32, 8),
        ):
            saver = screensaver_frame(
                "matrix_code", 1.0, keys, (72, 72), 40, columns=columns
            )
            first = next(iter(startup_frames(keys, (72, 72), 40, columns=columns)))
            self.assertEqual(len(saver.images), keys, name)
            self.assertEqual(len(first.images), keys, name)
            self.assertEqual(len(blank_exit_tiles(keys, (72, 72))), keys, name)


class NonVisualDeckTests(unittest.TestCase):
    """A Stream Deck Pedal has keys but no screens, so it is refused."""

    def setUp(self) -> None:
        self.messages: list[str] = []
        bus = EventBus()
        bus.subscribe("status", lambda _t, d: self.messages.append(d["text"]))
        self.manager = DeckManager(bus)

    def test_a_deck_without_displays_is_not_visual(self) -> None:
        self.assertFalse(_is_visual(FakeDevice("/dev/a", visual=False)))
        self.assertTrue(_is_visual(FakeDevice("/dev/a")))

    def test_a_driver_that_cannot_say_is_assumed_visual(self) -> None:
        """Refusing a deck we merely failed to ask about would be worse."""
        class Mute:
            pass

        self.assertTrue(_is_visual(Mute()))

    def test_refusing_one_says_why(self) -> None:
        self.manager._reject_device(
            FakeDevice("/dev/a", "Stream Deck Pedal", visual=False)
        )

        self.assertEqual(len(self.messages), 1)
        self.assertIn("Stream Deck Pedal", self.messages[0])
        self.assertIn("no key displays", self.messages[0])

    def test_the_refusal_is_not_repeated_every_scan(self) -> None:
        pedal = FakeDevice("/dev/a", "Stream Deck Pedal", visual=False)
        for _ in range(5):
            self.manager._reject_device(pedal)

        self.assertEqual(len(self.messages), 1)


class OpenFailureTests(unittest.TestCase):
    """A deck that is plugged in but never opens should say so.

    The reason was only ever in the log, so the symptom was a deck that simply
    never appeared. The usual cause is the USB permission rule.
    """

    def setUp(self) -> None:
        self.messages: list[str] = []
        bus = EventBus()
        bus.subscribe("status", lambda _t, d: self.messages.append(d["text"]))
        self.manager = DeckManager(bus)
        self.device = FakeDevice("/dev/a", "Stream Deck XL")

    def _fail(self, times: int = 1, device=None) -> None:
        for _ in range(times):
            self.manager._note_open_failure(device or self.device)

    def test_one_failure_stays_quiet(self) -> None:
        """A deck just plugged in often refuses the first attempt."""
        self._fail()
        self.assertEqual(self.messages, [])

    def test_a_second_failure_reports_it(self) -> None:
        self._fail(OPEN_FAILURES_BEFORE_WARNING)

        self.assertEqual(len(self.messages), 1)
        self.assertIn("Stream Deck XL", self.messages[0])
        self.assertIn("could not be opened", self.messages[0])

    def test_the_message_points_at_the_likely_causes(self) -> None:
        self._fail(OPEN_FAILURES_BEFORE_WARNING)

        self.assertIn("permission", self.messages[0])
        self.assertIn("other program", self.messages[0])

    def test_it_is_not_repeated_on_every_scan(self) -> None:
        self._fail(12)
        self.assertEqual(len(self.messages), 1)

    def test_another_deck_failing_is_reported_on_its_own(self) -> None:
        self._fail(OPEN_FAILURES_BEFORE_WARNING)
        self._fail(
            OPEN_FAILURES_BEFORE_WARNING,
            device=FakeDevice("/dev/b", "Stream Deck Mini"),
        )

        self.assertEqual(len(self.messages), 2)
        self.assertIn("Stream Deck Mini", self.messages[1])

    def test_a_deck_that_finally_opens_clears_the_history(self) -> None:
        """Unplug, fix the rule, plug back in: it must warn again if it fails."""
        self._fail(OPEN_FAILURES_BEFORE_WARNING)
        self.manager._clear_open_failures()

        self._fail(OPEN_FAILURES_BEFORE_WARNING)

        self.assertEqual(len(self.messages), 2)

    def test_a_device_that_answers_nothing_is_still_reported(self) -> None:
        self._fail(OPEN_FAILURES_BEFORE_WARNING, device=SilentDevice("/dev/a"))

        self.assertEqual(len(self.messages), 1)
        self.assertIn("Stream Deck", self.messages[0])


class OpenFailureLoggingTests(unittest.TestCase):
    """Every broad handler in the manager keeps its traceback.

    The one in `_try_open` did not, so a TypeError from our own code read
    exactly like a missing udev rule. That cost a bisection once.
    """

    def test_try_open_logs_the_traceback(self) -> None:
        import inspect

        from linuxstreamdeck.device import manager

        source = inspect.getsource(manager.DeckManager._try_open)
        opening = source[source.index("except Exception as e:"):]

        self.assertIn("exc_info=True", opening)


class StartupTitleLayoutTests(unittest.TestCase):
    """The name is spread one character per key, so it depends on the grid."""

    def _rendered(self, columns: int, rows: int) -> str:
        return "".join(c for _cell, c in _title_layout(columns, rows))

    def test_the_original_deck_is_unchanged(self) -> None:
        """The one model actually tested on hardware must not shift."""
        layout = _title_layout(5, 3)
        self.assertEqual([cell for cell, _c in layout], list(range(15)))
        self.assertEqual(self._rendered(5, 3), TITLE)

    def test_a_bigger_deck_gets_the_whole_name_centered(self) -> None:
        layout = _title_layout(8, 4)          # XL
        self.assertEqual(self._rendered(8, 4), TITLE)
        # Two rows of a four-row grid, so it sits in the middle, not the top.
        rows = {cell // 8 for cell, _c in layout}
        self.assertEqual(rows, {1, 2})

    def test_a_small_deck_gets_a_shorter_but_complete_name(self) -> None:
        """A fragment like "LinuxS" says less than a shorter whole word.

        The title screen saver shares this, and it would be a black screen if
        a deck too small simply showed nothing.
        """
        self.assertEqual(self._rendered(3, 2), "Linux")   # Mini
        self.assertEqual(self._rendered(4, 2), "Linux")   # Neo, Stream Deck +
        for text in TITLE_FORMS:
            self.assertNotIn(text, ("LinuxS", "LinuxSt"))

    def test_a_continuation_row_starts_at_the_left(self) -> None:
        """A lone trailing character centered in a row reads as a mistake."""
        layout = _title_layout(4, 2)
        self.assertEqual(layout[-1][0], 4)                # first cell of row 1

    def test_no_two_characters_share_a_key(self) -> None:
        for columns, rows in ((5, 3), (8, 4), (5, 4), (15, 1)):
            layout = _title_layout(columns, rows)
            cells = [cell for cell, _c in layout]
            self.assertEqual(len(cells), len(set(cells)), (columns, rows))

    def test_every_character_lands_inside_the_grid(self) -> None:
        for columns, rows in ((5, 3), (8, 4), (5, 4), (16, 1)):
            for cell, _c in _title_layout(columns, rows):
                self.assertLess(cell, columns * rows, (columns, rows))

    def test_the_reveal_order_is_still_the_reading_order(self) -> None:
        self.assertEqual(self._rendered(8, 4), TITLE)
        self.assertEqual(self._rendered(5, 4), TITLE)


if __name__ == "__main__":
    unittest.main()
