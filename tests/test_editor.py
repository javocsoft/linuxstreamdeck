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


if __name__ == "__main__":
    unittest.main()
