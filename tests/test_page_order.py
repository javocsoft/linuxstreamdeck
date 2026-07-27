"""Reordering and duplicating pages and profiles.

Both operations shift what a stored index means, so they clear the transient
toggle/clock state and the undo history exactly as deleting a page does. What
they must never disturb is `nav.page.go`: it targets a page by name, and
neither moving a page nor copying a whole profile changes a name it would have
to be rewritten for.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from linuxstreamdeck import basic_actions as _basic_actions  # noqa: F401
from linuxstreamdeck.core.config import (
    KIND_SINGLE,
    ActionStep,
    Config,
    KeyConfig,
    Page,
    unique_name,
)
from linuxstreamdeck.core.controller import DeckController
from linuxstreamdeck.core.events import EventBus
from linuxstreamdeck.obs import actions as _obs_actions  # noqa: F401


class FakeDeck:
    """Enough of DeckManager for the controller to render into nothing."""

    key_count = 15
    image_size = (72, 72)

    def __init__(self) -> None:
        self.images: dict[int, bytes] = {}
        self.screensaver_active = False

    def set_key_image(self, index, image) -> None:
        self.images[index] = image

    def record_activity(self) -> bool:
        return self.screensaver_active

    def set_brightness(self, _value) -> None:
        pass

    def configure_screensaver(self, *_args) -> None:
        pass


def key(label: str) -> KeyConfig:
    return KeyConfig(kind=KIND_SINGLE, action="obs.record", label=label)


class UniqueNameTests(unittest.TestCase):
    def test_an_unused_name_is_taken_as_is(self) -> None:
        self.assertEqual(unique_name("Live", []), "Live copy")

    def test_a_clash_is_numbered_from_two(self) -> None:
        self.assertEqual(unique_name("Live", ["Live copy"]), "Live copy 2")

    def test_numbering_skips_every_name_already_taken(self) -> None:
        taken = ["Live copy", "Live copy 2", "Live copy 3"]
        self.assertEqual(unique_name("Live", taken), "Live copy 4")


class MovePageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = Config()
        self.config.profile.pages = [Page(name=n) for n in ("A", "B", "C", "D")]
        self.controller = DeckController(
            self.config, EventBus(), SimpleNamespace(), FakeDeck()
        )
        self.addCleanup(self.controller.shutdown)

    def _names(self) -> list[str]:
        return [page.name for page in self.config.pages]

    def test_a_page_moves_later(self) -> None:
        self.controller.move_page(0, 2)

        self.assertEqual(self._names(), ["B", "C", "A", "D"])

    def test_a_page_moves_earlier(self) -> None:
        self.controller.move_page(3, 1)

        self.assertEqual(self._names(), ["A", "D", "B", "C"])

    def test_the_same_page_stays_on_screen_wherever_it_started(self) -> None:
        """Whichever page was active is still the active one afterwards."""
        for start in range(4):
            with self.subTest(start=start):
                self.config.profile.pages = [
                    Page(name=n) for n in ("A", "B", "C", "D")
                ]
                self.config.current_page = start
                watching = self.config.pages[start].name

                self.controller.move_page(0, 2)

                self.assertEqual(
                    self.config.pages[self.config.current_page].name, watching
                )

    def test_moving_a_page_onto_itself_changes_nothing(self) -> None:
        self.controller.move_page(1, 1)

        self.assertEqual(self._names(), ["A", "B", "C", "D"])

    def test_an_out_of_range_move_is_ignored(self) -> None:
        self.controller.move_page(9, 1)
        self.controller.move_page(1, -3)

        self.assertEqual(self._names(), ["A", "B", "C", "D"])

    def test_moving_drops_the_undo_history(self) -> None:
        """Every index between the two positions now means a different page."""
        self.config.pages[0].set_key(1, key("first"))
        self.controller.clear_key(1)
        self.assertTrue(self.controller.can_undo())

        self.controller.move_page(0, 2)

        self.assertFalse(self.controller.can_undo())

    def test_named_navigation_survives_a_move(self) -> None:
        target = KeyConfig(
            kind=KIND_SINGLE, action="nav.page.go", params={"page": "C"}
        )
        self.config.pages[0].set_key(4, target)

        self.controller.move_page(2, 0)

        # "C" moved to the front, so the page holding the key is now second.
        self.assertEqual(self._names(), ["C", "A", "B", "D"])
        self.assertEqual(
            self.config.pages[1].key(4).params["page"], "C"
        )
        self.assertTrue(self.controller.set_page_by_name("C"))


class DuplicatePageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = Config()
        self.config.profile.pages = [Page(name="Live"), Page(name="BRB")]
        self.config.pages[0].set_key(3, key("REC"))
        self.controller = DeckController(
            self.config, EventBus(), SimpleNamespace(), FakeDeck()
        )
        self.addCleanup(self.controller.shutdown)

    def _names(self) -> list[str]:
        return [page.name for page in self.config.pages]

    def test_the_copy_lands_right_after_the_original(self) -> None:
        self.controller.duplicate_page(0)

        self.assertEqual(self._names(), ["Live", "Live copy", "BRB"])

    def test_the_copy_becomes_the_active_page(self) -> None:
        self.controller.duplicate_page(0)

        self.assertEqual(self.config.pages[self.config.current_page].name,
                         "Live copy")

    def test_the_keys_are_copied(self) -> None:
        self.controller.duplicate_page(0)

        self.assertEqual(self.config.pages[1].key(3).label, "REC")

    def test_the_copy_is_independent_of_the_original(self) -> None:
        self.controller.duplicate_page(0)

        self.config.pages[1].key(3).label = "edited"

        self.assertEqual(self.config.pages[0].key(3).label, "REC")

    def test_duplicating_twice_keeps_the_names_unique(self) -> None:
        self.controller.duplicate_page(0)
        self.controller.duplicate_page(0)

        self.assertEqual(len(set(self._names())), len(self._names()))

    def test_an_out_of_range_page_is_ignored(self) -> None:
        self.controller.duplicate_page(7)

        self.assertEqual(self._names(), ["Live", "BRB"])

    def test_nested_steps_are_copied_not_shared(self) -> None:
        multi = KeyConfig(kind="multi", steps=[ActionStep(action="obs.record")])
        self.config.pages[0].set_key(6, multi)

        self.controller.duplicate_page(0)

        copied = self.config.pages[1].key(6)
        self.assertIsNot(copied.steps[0], multi.steps[0])


class DuplicateProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = Config()
        self.config.profile.name = "General"
        self.config.profile.pages = [Page(name="Live"), Page(name="BRB")]
        self.config.pages[0].set_key(3, key("REC"))
        self.controller = DeckController(
            self.config, EventBus(), SimpleNamespace(), FakeDeck()
        )
        self.addCleanup(self.controller.shutdown)

    def test_the_copy_is_appended_and_becomes_active(self) -> None:
        self.controller.duplicate_profile(0)

        self.assertEqual([p.name for p in self.config.profiles],
                         ["General", "General copy"])
        self.assertEqual(self.config.profile.name, "General copy")

    def test_every_page_comes_across(self) -> None:
        self.controller.duplicate_profile(0)

        self.assertEqual([p.name for p in self.config.profile.pages],
                         ["Live", "BRB"])

    def test_the_keys_are_copied(self) -> None:
        self.controller.duplicate_profile(0)

        self.assertEqual(self.config.profile.pages[0].key(3).label, "REC")

    def test_the_copy_is_independent_of_the_original(self) -> None:
        self.controller.duplicate_profile(0)

        self.config.profile.pages[0].key(3).label = "edited"

        self.assertEqual(self.config.profiles[0].pages[0].key(3).label, "REC")

    def test_page_names_are_kept_so_navigation_points_inside_the_copy(self) -> None:
        """A copied `nav.page.go` must reach the copy's page, not the original."""
        self.config.pages[0].set_key(
            4,
            KeyConfig(kind=KIND_SINGLE, action="nav.page.go",
                      params={"page": "BRB"}),
        )

        self.controller.duplicate_profile(0)

        self.assertEqual(
            self.config.profile.pages[0].key(4).params["page"], "BRB"
        )
        self.assertTrue(self.controller.set_page_by_name("BRB"))
        self.assertIs(self.config.pages, self.config.profile.pages)

    def test_duplicating_twice_keeps_the_names_unique(self) -> None:
        self.controller.duplicate_profile(0)
        self.controller.duplicate_profile(0)

        names = [p.name for p in self.config.profiles]
        self.assertEqual(len(set(names)), len(names))

    def test_an_out_of_range_profile_is_ignored(self) -> None:
        self.controller.duplicate_profile(9)

        self.assertEqual(len(self.config.profiles), 1)


if __name__ == "__main__":
    unittest.main()
