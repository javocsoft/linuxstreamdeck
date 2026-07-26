from __future__ import annotations

import unittest
from types import SimpleNamespace

from linuxstreamdeck import basic_actions as _basic_actions  # noqa: F401
from linuxstreamdeck.core.config import ActionStep, KeyConfig
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

    def test_clock_action_default_icons_are_resolved(self) -> None:
        self.assertEqual(
            EditorPanel._action_icon("sys.timer"),
            "mdi:timer-outline",
        )
        self.assertEqual(
            EditorPanel._action_icon("sys.stopwatch"),
            "mdi:clock-outline",
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


class ScreenSaverActivityTests(unittest.TestCase):
    @staticmethod
    def _window(calls: list) -> SimpleNamespace:
        return SimpleNamespace(
            app=SimpleNamespace(
                deck=SimpleNamespace(
                    record_activity=lambda: calls.append("activity")
                ),
                controller=SimpleNamespace(
                    is_reserved_key=lambda _index: False
                ),
            ),
            _select=lambda index: calls.append(("select", index)),
            open_folder=lambda index: calls.append(("open", index)),
            _last_key_click=None,
        )

    def test_virtual_key_interaction_records_activity_before_selection(
        self,
    ) -> None:
        calls = []
        window = self._window(calls)

        MainWindow._on_key_clicked(window, None, 6)

        self.assertEqual(calls, ["activity", ("select", 6)])

    def test_a_second_quick_click_opens_the_key_instead_of_reselecting(
        self,
    ) -> None:
        """Double-clicking a folder on the virtual deck goes inside it."""
        calls = []
        window = self._window(calls)

        MainWindow._on_key_clicked(window, None, 6)
        MainWindow._on_key_clicked(window, None, 6)

        self.assertEqual(
            calls, ["activity", ("select", 6), "activity", ("open", 6)]
        )

    def test_a_third_click_starts_a_new_pair(self) -> None:
        """Otherwise it would reopen the folder that was just entered."""
        calls = []
        window = self._window(calls)

        for _ in range(3):
            MainWindow._on_key_clicked(window, None, 6)

        self.assertEqual(calls[-1], ("select", 6))


class PasteActionOntoKeyTests(unittest.TestCase):
    """The copied step can also become a whole single-action key."""

    def setUp(self) -> None:
        from linuxstreamdeck.ui.steps import STEP_CLIPBOARD

        STEP_CLIPBOARD.clear()
        self.addCleanup(STEP_CLIPBOARD.clear)
        self.clipboard = STEP_CLIPBOARD
        self.pasted: list[tuple[int, KeyConfig]] = []
        self.status: list[str] = []
        self.window = SimpleNamespace(
            selected=4,
            app=SimpleNamespace(
                bus=SimpleNamespace(
                    emit=lambda _t, **d: self.status.append(d.get("text", ""))
                ),
                controller=SimpleNamespace(
                    is_reserved_key=lambda _i: False,
                    paste_key=lambda i, kc: self.pasted.append((i, kc)),
                ),
            ),
            editor=SimpleNamespace(load=lambda _i: None),
            # Both guards pass straight through here; they have their own tests.
            _confirm_unsaved_changes=lambda _d, go, offer_save=True: go(),
            _replacing_folder=lambda _i, _d, go: go(),
        )
        # The real one, so what it writes to the controller is under test.
        self.window._apply_paste_action = (
            lambda i, kc: MainWindow._apply_paste_action(self.window, i, kc)
        )

    def _paste(self, index: int) -> None:
        MainWindow._paste_action(self.window, index)

    def test_pasting_makes_a_single_action_key(self) -> None:
        self.clipboard.set(
            ActionStep(
                action="sys.wait",
                params={"duration": "00:05"},
                label="Breathe",
            )
        )

        self._paste(4)

        (index, key), = self.pasted
        self.assertEqual(index, 4)
        self.assertEqual(key.kind, "single")
        self.assertEqual(key.action, "sys.wait")
        self.assertEqual(key.params["duration"], "00:05")

    def test_the_step_name_does_not_become_the_key_label(self) -> None:
        """A key's own label lives in Appearance, as when pasted on its editor."""
        self.clipboard.set(ActionStep(action="obs.record", label="Roll camera"))

        self._paste(4)

        self.assertEqual(self.pasted[0][1].label, "")

    def test_pasting_with_nothing_copied_says_so(self) -> None:
        self._paste(4)

        self.assertEqual(self.pasted, [])
        self.assertEqual(self.status, ["No action copied"])

    def test_the_back_key_of_a_folder_is_never_replaced(self) -> None:
        self.clipboard.set(ActionStep(action="obs.record"))
        self.window.app.controller.is_reserved_key = lambda _i: True

        self._paste(0)

        self.assertEqual(self.pasted, [])

    def test_the_status_names_the_action_that_landed(self) -> None:
        self.clipboard.set(ActionStep(action="obs.record"))

        self._paste(4)

        self.assertEqual(self.status, ["Pasted “Record on/off” into key 5"])

    def test_pasting_does_not_consume_the_copy(self) -> None:
        """The same action can go onto several keys in a row."""
        self.clipboard.set(ActionStep(action="obs.record"))

        self._paste(4)
        self.window.selected = 5
        self._paste(5)

        self.assertEqual([i for i, _kc in self.pasted], [4, 5])


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
            app=SimpleNamespace(
                controller=SimpleNamespace(
                    is_reserved_key=lambda _index: False
                )
            ),
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
            app=SimpleNamespace(
                controller=SimpleNamespace(
                    is_reserved_key=lambda _index: False
                )
            ),
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


class KeyExportNameTests(unittest.TestCase):
    @staticmethod
    def _name(label: str, index: int = 4) -> str:
        return MainWindow._key_export_name(KeyConfig(label=label), index)

    def test_label_becomes_a_readable_file_name(self) -> None:
        self.assertEqual(self._name("Start Streaming"), "start-streaming")

    def test_unsafe_characters_are_replaced(self) -> None:
        self.assertEqual(self._name("Mute / Mic!"), "mute-mic")

    def test_separators_are_collapsed_and_trimmed(self) -> None:
        self.assertEqual(self._name("  Rec  ***  now  "), "rec-now")

    def test_dashes_and_underscores_are_kept(self) -> None:
        self.assertEqual(self._name("Mute-Mic_1"), "mute-mic_1")

    def test_a_key_without_a_usable_label_falls_back_to_its_position(self) -> None:
        self.assertEqual(self._name(""), "key-5")
        self.assertEqual(self._name("***"), "key-5")


if __name__ == "__main__":
    unittest.main()
