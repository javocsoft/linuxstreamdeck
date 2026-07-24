from __future__ import annotations

import unittest
from types import SimpleNamespace

from linuxstreamdeck import basic_actions as _basic_actions  # noqa: F401
from linuxstreamdeck.core.config import ActionStep
from linuxstreamdeck.obs import actions as _obs_actions  # noqa: F401
from linuxstreamdeck.ui.editor import EditorPanel
from linuxstreamdeck.ui.window import MainWindow


class EditorIconTests(unittest.TestCase):
    def test_single_action_default_icon_is_resolved(self) -> None:
        self.assertEqual(
            EditorPanel._action_icon("obs.stream"),
            "mdi:broadcast",
        )

    def test_audio_action_default_icon_is_resolved(self) -> None:
        self.assertEqual(
            EditorPanel._action_icon("sys.audio"),
            "mdi:music-note",
        )

    def test_multiple_action_uses_first_available_default_icon(self) -> None:
        steps = [
            ActionStep(action=""),
            ActionStep(action="obs.record"),
            ActionStep(action="obs.stream"),
        ]

        self.assertEqual(
            EditorPanel._steps_icon(steps, "mdi:playlist-play"),
            "mdi:record-circle",
        )

    def test_empty_action_list_does_not_show_a_fallback(self) -> None:
        self.assertEqual(
            EditorPanel._steps_icon([], "mdi:playlist-play"),
            "",
        )


class UnsavedResponseTests(unittest.TestCase):
    def test_save_response_saves_before_continuing(self) -> None:
        calls = []
        window = SimpleNamespace(
            _unsaved_dialog=object(),
            editor=SimpleNamespace(save=lambda: calls.append("save") or True),
        )

        MainWindow._on_unsaved_response(
            window,
            None,
            "save",
            lambda: calls.append("continue"),
            True,
        )

        self.assertEqual(calls, ["save", "continue"])
        self.assertIsNone(window._unsaved_dialog)

    def test_discard_response_continues_without_saving(self) -> None:
        calls = []
        window = SimpleNamespace(
            _unsaved_dialog=object(),
            editor=SimpleNamespace(save=lambda: calls.append("save") or True),
        )

        MainWindow._on_unsaved_response(
            window,
            None,
            "discard",
            lambda: calls.append("continue"),
            True,
        )

        self.assertEqual(calls, ["continue"])

    def test_cancel_response_keeps_editing(self) -> None:
        calls = []
        window = SimpleNamespace(
            _unsaved_dialog=object(),
            editor=SimpleNamespace(save=lambda: calls.append("save") or True),
        )

        MainWindow._on_unsaved_response(
            window,
            None,
            "cancel",
            lambda: calls.append("continue"),
            True,
        )

        self.assertEqual(calls, [])


class KeyDragTests(unittest.TestCase):
    def test_grid_point_resolves_a_button_through_its_child(self) -> None:
        class Widget:
            def __init__(self, parent=None) -> None:
                self.parent = parent

            def get_parent(self):
                return self.parent

        grid = Widget()
        button = Widget(grid)
        picture = Widget(button)
        grid.pick = lambda _x, _y, _flags: picture
        window = SimpleNamespace(
            _key_grid=grid,
            _key_buttons=[Widget(grid), button, Widget(grid)],
        )

        self.assertEqual(
            MainWindow._key_at_grid_point(window, 25, 25),
            1,
        )

    def test_drag_payload_only_accepts_internal_key_indices(self) -> None:
        self.assertEqual(
            MainWindow._decode_key_drag("linuxstreamdeck-key:12"),
            12,
        )
        self.assertIsNone(MainWindow._decode_key_drag("12"))
        self.assertIsNone(
            MainWindow._decode_key_drag("linuxstreamdeck-key:not-a-number")
        )
        self.assertIsNone(MainWindow._decode_key_drag(None))

    def test_drop_uses_the_key_under_the_pointer_in_any_direction(self) -> None:
        moves = []
        destinations = []
        window = SimpleNamespace(
            _drag_source_index=11,
            _key_at_grid_point=lambda _x, _y: 1,
            _decode_key_drag=MainWindow._decode_key_drag,
            _confirm_unsaved_changes=lambda _text, callback: callback(),
            _apply_key_drop=lambda source, destination: moves.append(
                (source, destination)
            ),
            _set_drag_destination=lambda index: destinations.append(index),
        )

        accepted = MainWindow._on_drop(
            window,
            None,
            "linuxstreamdeck-key:11",
            20,
            20,
        )

        self.assertTrue(accepted)
        self.assertEqual(moves, [(11, 1)])
        self.assertEqual(destinations, [None])

    def test_drop_rejects_foreign_or_stale_drag_data(self) -> None:
        moves = []
        window = SimpleNamespace(
            _drag_source_index=4,
            _key_at_grid_point=lambda _x, _y: 9,
            _decode_key_drag=MainWindow._decode_key_drag,
            _confirm_unsaved_changes=lambda _text, callback: callback(),
            _apply_key_drop=lambda source, destination: moves.append(
                (source, destination)
            ),
            _set_drag_destination=lambda _index: None,
        )

        foreign = MainWindow._on_drop(
            window,
            None,
            "unrelated-text",
            20,
            20,
        )
        stale = MainWindow._on_drop(
            window,
            None,
            "linuxstreamdeck-key:3",
            20,
            20,
        )

        self.assertFalse(foreign)
        self.assertFalse(stale)
        self.assertEqual(moves, [])


if __name__ == "__main__":
    unittest.main()
