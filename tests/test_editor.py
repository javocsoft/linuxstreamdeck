from __future__ import annotations

import unittest

from linuxstreamdeck import basic_actions as _basic_actions  # noqa: F401
from linuxstreamdeck.core.config import ActionStep
from linuxstreamdeck.obs import actions as _obs_actions  # noqa: F401
from linuxstreamdeck.ui.editor import EditorPanel


class EditorIconTests(unittest.TestCase):
    def test_single_action_default_icon_is_resolved(self) -> None:
        self.assertEqual(
            EditorPanel._action_icon("obs.stream"),
            "mdi:broadcast",
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


if __name__ == "__main__":
    unittest.main()
