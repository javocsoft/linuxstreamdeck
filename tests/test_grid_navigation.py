"""Walking the key grid with the arrow keys.

Left and right follow the deck's own numbering, so the end of a row leads into
the start of the next one — the grid is one sequence of keys drawn in rows, and
that is also the order the physical deck reports them in. Up and down stop at
the edges instead of wrapping, which would throw the focus to the far end.

Focus is deliberately not selection: selecting runs the unsaved-change guard,
so arrowing across a grid while a key is being edited would raise a dialog per
key press.
"""

from __future__ import annotations

import unittest

import gi

gi.require_version("Gtk", "4.0")

from linuxstreamdeck.ui.window import _ARROW_DIRECTIONS, neighbour_index  # noqa: E402

MK2 = (5, 15)
MINI = (3, 6)
XL = (8, 32)


class NeighbourTests(unittest.TestCase):
    def test_horizontal_movement_within_a_row(self) -> None:
        columns, count = MK2

        self.assertEqual(neighbour_index(0, "right", columns, count), 1)
        self.assertEqual(neighbour_index(3, "left", columns, count), 2)

    def test_the_end_of_a_row_leads_into_the_next_one(self) -> None:
        columns, count = MK2

        self.assertEqual(neighbour_index(4, "right", columns, count), 5)
        self.assertEqual(neighbour_index(5, "left", columns, count), 4)

    def test_vertical_movement_steps_a_whole_row(self) -> None:
        columns, count = MK2

        self.assertEqual(neighbour_index(0, "down", columns, count), 5)
        self.assertEqual(neighbour_index(9, "up", columns, count), 4)

    def test_the_edges_do_not_wrap(self) -> None:
        columns, count = MK2

        self.assertIsNone(neighbour_index(0, "up", columns, count))
        self.assertIsNone(neighbour_index(0, "left", columns, count))
        self.assertIsNone(neighbour_index(14, "down", columns, count))
        self.assertIsNone(neighbour_index(14, "right", columns, count))

    def test_it_follows_whatever_shape_the_deck_has(self) -> None:
        for columns, count in (MINI, MK2, XL):
            with self.subTest(columns=columns):
                self.assertEqual(
                    neighbour_index(0, "down", columns, count), columns
                )
                self.assertIsNone(neighbour_index(0, "up", columns, count))
                self.assertIsNone(
                    neighbour_index(count - 1, "right", columns, count)
                )

    def test_every_key_is_reachable_by_walking_right(self) -> None:
        """No shape may leave a key that the keyboard cannot get to."""
        for columns, count in (MINI, MK2, XL):
            with self.subTest(columns=columns):
                seen, index = {0}, 0
                while (index := neighbour_index(
                    index, "right", columns, count
                )) is not None:
                    seen.add(index)
                self.assertEqual(len(seen), count)

    def test_a_degenerate_grid_is_refused_rather_than_dividing_by_zero(self) -> None:
        self.assertIsNone(neighbour_index(0, "right", 0, 15))
        self.assertIsNone(neighbour_index(0, "right", 5, 0))

    def test_an_out_of_range_index_goes_nowhere(self) -> None:
        columns, count = MK2

        self.assertIsNone(neighbour_index(99, "right", columns, count))
        self.assertIsNone(neighbour_index(-1, "right", columns, count))

    def test_an_unknown_direction_goes_nowhere(self) -> None:
        self.assertIsNone(neighbour_index(0, "sideways", 5, 15))

    def test_the_keypad_arrows_map_to_the_same_directions(self) -> None:
        """A numeric keypad must navigate the grid exactly like the arrows."""
        from gi.repository import Gdk

        pairs = (
            (Gdk.KEY_Left, Gdk.KEY_KP_Left),
            (Gdk.KEY_Right, Gdk.KEY_KP_Right),
            (Gdk.KEY_Up, Gdk.KEY_KP_Up),
            (Gdk.KEY_Down, Gdk.KEY_KP_Down),
        )
        for arrow, keypad in pairs:
            with self.subTest(arrow=arrow):
                self.assertEqual(
                    _ARROW_DIRECTIONS[arrow], _ARROW_DIRECTIONS[keypad]
                )


class _FakeButton:
    """Only what the focus lookup asks of a key button.

    Real focus needs a mapped window, and showing one is exactly what this
    project does not do to verify itself.
    """

    def __init__(self, focused: bool = False) -> None:
        self._focused = focused

    def has_focus(self) -> bool:
        return self._focused


class FocusLookupTests(unittest.TestCase):
    """Turning the focused widget back into a key index."""

    def _window(self, buttons):
        from linuxstreamdeck.ui.window import MainWindow

        window = MainWindow.__new__(MainWindow)
        window._key_buttons = buttons
        return window

    def test_the_focused_button_is_reported_by_index(self) -> None:
        buttons = [_FakeButton() for _ in range(6)]
        buttons[4] = _FakeButton(focused=True)

        self.assertEqual(self._window(buttons)._focused_key_index(), 4)

    def test_nothing_focused_reports_no_index(self) -> None:
        buttons = [_FakeButton() for _ in range(6)]

        self.assertIsNone(self._window(buttons)._focused_key_index())

    def test_an_empty_grid_reports_no_index(self) -> None:
        self.assertIsNone(self._window([])._focused_key_index())


if __name__ == "__main__":
    unittest.main()
