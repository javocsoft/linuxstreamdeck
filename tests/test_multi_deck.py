"""Only the first Stream Deck is used, and that is now said out loud.

`_try_open()` enumerates every device and keeps the first. Dropping the rest
without a word was indistinguishable from a second deck that had failed, so the
limitation is reported. It must not become status-bar noise: the scan runs every
few seconds for as long as nothing is connected.
"""

from __future__ import annotations

import unittest

from linuxstreamdeck.core.events import EventBus
from linuxstreamdeck.device.manager import DeckManager


class FakeDevice:
    def __init__(self, path: str, kind: str = "Stream Deck Original V2") -> None:
        self._path = path
        self._kind = kind

    def id(self) -> str:
        return self._path

    def deck_type(self) -> str:
        return self._kind


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


if __name__ == "__main__":
    unittest.main()
