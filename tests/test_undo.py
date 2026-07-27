"""Taking back a key change.

Clearing, pasting and drag-swapping a key used to be irreversible, guarded only
by a confirmation dialog: once confirmed there was no way back. The history is
deliberately scoped to the grid on screen — an entry restored into a page the
user has left would be invisible, and inside a folder the same index is a
different key entirely.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from linuxstreamdeck import basic_actions as _basic_actions  # noqa: F401
from linuxstreamdeck.core.config import (
    KIND_FOLDER,
    KIND_SINGLE,
    Config,
    Folder,
    KeyConfig,
    Page,
)
from linuxstreamdeck.core.controller import UNDO_DEPTH, DeckController
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


class UndoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = Config()
        self.controller = DeckController(
            self.config, EventBus(), SimpleNamespace(), FakeDeck()
        )
        self.addCleanup(self.controller.shutdown)
        self.page = self.config.pages[0]
        self.page.set_key(1, key("first"))
        self.page.set_key(2, key("second"))

    def _label(self, index: int) -> str | None:
        stored = self.controller.container.key(index)
        return stored.label if stored is not None else None

    # ---------- the three reversible operations ----------

    def test_clearing_a_key_can_be_taken_back(self) -> None:
        self.controller.clear_key(1)
        self.assertIsNone(self._label(1))

        self.assertEqual(self.controller.undo(), "clearing Key 2")

        self.assertEqual(self._label(1), "first")

    def test_pasting_over_a_key_can_be_taken_back(self) -> None:
        self.controller.paste_key(1, key("pasted"))
        self.assertEqual(self._label(1), "pasted")

        self.controller.undo()

        self.assertEqual(self._label(1), "first")

    def test_pasting_into_an_empty_key_undoes_back_to_empty(self) -> None:
        self.controller.paste_key(7, key("pasted"))

        self.controller.undo()

        self.assertIsNone(self._label(7))

    def test_a_swap_is_taken_back_as_a_whole(self) -> None:
        self.controller.swap_keys(1, 2)
        self.assertEqual((self._label(1), self._label(2)), ("second", "first"))

        self.controller.undo()

        self.assertEqual((self._label(1), self._label(2)), ("first", "second"))

    def test_undoing_a_restored_key_gives_back_an_independent_copy(self) -> None:
        """Editing what came back must not reach into the history."""
        self.controller.clear_key(1)
        self.controller.undo()

        self.controller.container.key(1).label = "edited"
        self.controller.clear_key(1)
        self.controller.undo()

        self.assertEqual(self._label(1), "edited")

    # ---------- how far back it goes ----------

    def test_changes_are_taken_back_newest_first(self) -> None:
        self.controller.clear_key(1)
        self.controller.clear_key(2)

        self.controller.undo()
        self.assertEqual((self._label(1), self._label(2)), (None, "second"))

        self.controller.undo()
        self.assertEqual((self._label(1), self._label(2)), ("first", "second"))

    def test_the_history_is_bounded(self) -> None:
        for round_trip in range(UNDO_DEPTH + 5):
            self.controller.paste_key(1, key(f"v{round_trip}"))

        self.assertEqual(len(self.controller._undo), UNDO_DEPTH)

    def test_undoing_with_nothing_to_undo_is_harmless(self) -> None:
        self.assertFalse(self.controller.can_undo())
        self.assertEqual(self.controller.undo(), "")
        self.assertEqual(self._label(1), "first")

    # ---------- scoped to the grid on screen ----------

    def test_changing_page_drops_the_history(self) -> None:
        self.config.pages.append(Page(name="Second"))
        self.controller.clear_key(1)
        self.assertTrue(self.controller.can_undo())

        self.controller.set_page(1)

        self.assertFalse(self.controller.can_undo())

    def test_changing_profile_drops_the_history(self) -> None:
        self.controller.add_profile("Other")
        self.controller.set_profile(0)
        self.controller.clear_key(1)
        self.assertTrue(self.controller.can_undo())

        self.controller.set_profile(1)

        self.assertFalse(self.controller.can_undo())

    def test_entering_a_folder_drops_the_history(self) -> None:
        """The same index inside a folder is a different key."""
        folder = KeyConfig(kind=KIND_FOLDER, folder=Folder())
        folder.folder.set_key(3, key("inside"))
        self.page.set_key(5, folder)
        self.controller.clear_key(1)
        self.assertTrue(self.controller.can_undo())

        self.controller.open_folder(5)

        self.assertFalse(self.controller.can_undo())

    def test_a_change_inside_a_folder_is_undone_there(self) -> None:
        folder = KeyConfig(kind=KIND_FOLDER, folder=Folder())
        folder.folder.set_key(3, key("inside"))
        self.page.set_key(5, folder)
        self.controller.open_folder(5)

        self.controller.clear_key(3)
        self.assertIsNone(self._label(3))
        self.controller.undo()

        self.assertEqual(self._label(3), "inside")
        # The page it sits in was never touched.
        self.controller.close_folder()
        self.assertEqual(self._label(1), "first")

    def test_deleting_a_page_drops_the_history(self) -> None:
        """Its indices shift, so a restore would land on the wrong key."""
        self.config.pages.append(Page(name="Second"))
        self.controller.clear_key(1)

        self.controller.delete_page(1)

        self.assertFalse(self.controller.can_undo())

    def test_the_reserved_back_key_is_never_recorded(self) -> None:
        folder = KeyConfig(kind=KIND_FOLDER, folder=Folder())
        self.page.set_key(5, folder)
        self.controller.open_folder(5)

        self.controller.clear_key(0)

        self.assertFalse(self.controller.can_undo())


class RedoTests(unittest.TestCase):
    """Putting back what was taken back.

    Undo and redo are each other's mirror image: applying an entry stores the
    entry that reverses it, so a change can be walked back and forward any
    number of times without the two stacks drifting apart.
    """

    def setUp(self) -> None:
        self.config = Config()
        self.controller = DeckController(
            self.config, EventBus(), SimpleNamespace(), FakeDeck()
        )
        self.addCleanup(self.controller.shutdown)
        self.page = self.config.pages[0]
        self.page.set_key(1, key("first"))

    def _label(self, index: int) -> str | None:
        stored = self.controller.container.key(index)
        return stored.label if stored is not None else None

    def test_nothing_can_be_redone_before_anything_is_undone(self) -> None:
        self.controller.clear_key(1)

        self.assertFalse(self.controller.can_redo())
        self.assertEqual(self.controller.redo(), "")

    def test_an_undone_change_can_be_put_back(self) -> None:
        self.controller.clear_key(1)
        self.controller.undo()
        self.assertEqual(self._label(1), "first")

        self.assertEqual(self.controller.redo(), "clearing Key 2")

        self.assertIsNone(self._label(1))

    def test_a_redone_change_can_be_taken_back_again(self) -> None:
        self.controller.paste_key(1, key("pasted"))
        self.controller.undo()
        self.controller.redo()

        self.controller.undo()

        self.assertEqual(self._label(1), "first")

    def test_walking_back_and_forward_repeatedly_stays_consistent(self) -> None:
        self.controller.paste_key(1, key("second"))
        self.controller.paste_key(1, key("third"))

        for _ in range(5):
            self.controller.undo()
            self.controller.undo()
            self.assertEqual(self._label(1), "first")
            self.controller.redo()
            self.controller.redo()
            self.assertEqual(self._label(1), "third")

    def test_a_swap_is_redone_as_a_whole(self) -> None:
        self.page.set_key(2, key("second"))
        self.controller.swap_keys(1, 2)
        self.controller.undo()

        self.controller.redo()

        self.assertEqual((self._label(1), self._label(2)), ("second", "first"))

    def test_a_new_change_discards_the_redo_future(self) -> None:
        """Branching away from an undone change makes it unreachable."""
        self.controller.clear_key(1)
        self.controller.undo()
        self.assertTrue(self.controller.can_redo())

        self.controller.paste_key(1, key("elsewhere"))

        self.assertFalse(self.controller.can_redo())

    def test_a_redone_key_is_an_independent_copy(self) -> None:
        self.controller.clear_key(1)
        self.controller.undo()
        self.controller.redo()
        self.controller.undo()

        self.controller.container.key(1).label = "edited"

        self.controller.redo()
        self.controller.undo()
        self.assertEqual(self._label(1), "edited")

    def test_the_redo_history_is_bounded(self) -> None:
        for round_trip in range(UNDO_DEPTH + 5):
            self.controller.paste_key(1, key(f"v{round_trip}"))
        for _ in range(UNDO_DEPTH + 5):
            self.controller.undo()

        self.assertEqual(len(self.controller._redo), UNDO_DEPTH)

    def test_changing_page_drops_the_redo_history(self) -> None:
        self.config.pages.append(Page(name="Second"))
        self.controller.clear_key(1)
        self.controller.undo()
        self.assertTrue(self.controller.can_redo())

        self.controller.set_page(1)

        self.assertFalse(self.controller.can_redo())

    def test_a_change_inside_a_folder_is_redone_there(self) -> None:
        folder = KeyConfig(kind=KIND_FOLDER, folder=Folder())
        folder.folder.set_key(3, key("inside"))
        self.page.set_key(5, folder)
        self.controller.open_folder(5)

        self.controller.clear_key(3)
        self.controller.undo()
        self.controller.redo()

        self.assertIsNone(self._label(3))
        self.controller.close_folder()
        self.assertEqual(self._label(1), "first")


if __name__ == "__main__":
    unittest.main()
