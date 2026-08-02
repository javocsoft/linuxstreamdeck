from __future__ import annotations

import inspect
import unittest
import unittest.mock
from types import SimpleNamespace

from linuxstreamdeck import basic_actions as _basic_actions  # noqa: F401
from linuxstreamdeck.core.config import ActionStep, KeyConfig
from linuxstreamdeck.obs import actions as _obs_actions  # noqa: F401
from linuxstreamdeck.ui.editor import EditorPanel, acknowledge_press
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


class GridHintWidthTests(unittest.TestCase):
    """The hint under the grid must never be wider than the grid itself.

    The grid box is centered, so it takes the width of its widest child. An
    unwrapped hint made that box wider than the window and the text ran off the
    right edge.
    """

    @staticmethod
    def _gtk_ready() -> bool:
        try:
            import gi

            gi.require_version("Gtk", "4.0")
            gi.require_version("Adw", "1")
            from gi.repository import Adw, Gtk
        except (ImportError, ValueError):
            return False
        if not Gtk.init_check():
            return False
        Adw.init()
        return True

    def setUp(self) -> None:
        if not self._gtk_ready():
            self.skipTest("GTK needs a display to measure widgets")

    def test_the_hint_wraps_inside_the_key_grid(self) -> None:
        from gi.repository import Gtk

        from linuxstreamdeck.ui.window import (
            GRID_COLS,
            GRID_HINT,
            HINT_MAX_CHARS,
            KEY_PIXELS,
            MainWindow,
        )

        # The window builds this very string, so lengthening it fails here
        # rather than silently pushing the centered grid off the window.
        self.assertIn("GRID_HINT", inspect.getsource(MainWindow._build_ui))
        hint = Gtk.Label(
            label=GRID_HINT,
            wrap=True,
            justify=Gtk.Justification.CENTER,
            max_width_chars=HINT_MAX_CHARS,
        )
        hint.add_css_class("dim-label")
        _, natural, _, _ = hint.measure(Gtk.Orientation.HORIZONTAL, -1)

        grid_width = GRID_COLS * KEY_PIXELS + (GRID_COLS - 1) * 10
        self.assertLess(
            natural,
            grid_width,
            "the grid hint asks for more width than the key grid, so it will "
            "push the centered grid box past the window edge",
        )

    def test_an_unwrapped_hint_would_have_been_caught(self) -> None:
        """Confirms the check above can actually fail."""
        from gi.repository import Gtk

        from linuxstreamdeck.ui.window import GRID_COLS, GRID_HINT, KEY_PIXELS

        unwrapped = Gtk.Label(label=GRID_HINT)
        unwrapped.add_css_class("dim-label")
        _, natural, _, _ = unwrapped.measure(Gtk.Orientation.HORIZONTAL, -1)

        self.assertGreater(natural, GRID_COLS * KEY_PIXELS + (GRID_COLS - 1) * 10)


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

    @staticmethod
    def _drop_window(moves, source, hovered, path=(), destinations=None):
        """A window stub whose real `_is_drag_source` decides source identity."""
        window = SimpleNamespace(
            app=SimpleNamespace(
                controller=SimpleNamespace(
                    is_reserved_key=lambda _index: False,
                    folder_path=path,
                )
            ),
            _drag_source=source,
            _key_at_grid_point=lambda _x, _y: hovered,
            _decode_key_drag=MainWindow._decode_key_drag,
            _cancel_spring=lambda: None,
            _confirm_unsaved_changes=lambda _text, callback: callback(),
            _apply_key_drop=lambda source_path, source, destination: moves.append(
                (source_path, source, destination)
            ),
            _set_drag_destination=(
                (lambda index: destinations.append(index))
                if destinations is not None
                else (lambda _index: None)
            ),
        )
        window._is_drag_source = lambda index: MainWindow._is_drag_source(
            window, index
        )
        return window

    def test_drop_uses_the_key_under_the_pointer_in_any_direction(self) -> None:
        moves: list = []
        destinations: list = []
        window = self._drop_window(
            moves, ((), 11), 1, destinations=destinations
        )

        accepted = MainWindow._on_drop(
            window,
            None,
            "linuxstreamdeck-key:11",
            20,
            20,
        )

        self.assertTrue(accepted)
        self.assertEqual(moves, [((), 11, 1)])
        self.assertEqual(destinations, [None])

    def test_drop_rejects_foreign_or_stale_drag_data(self) -> None:
        moves: list = []
        window = self._drop_window(moves, ((), 4), 9)

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

    def test_a_drop_carries_the_grid_the_drag_started_in(self) -> None:
        """A folder that sprang open mid-drag left the source behind in it."""
        moves: list = []
        window = self._drop_window(moves, ((3,), 7), 5, path=())

        accepted = MainWindow._on_drop(
            window,
            None,
            "linuxstreamdeck-key:7",
            20,
            20,
        )

        self.assertTrue(accepted)
        self.assertEqual(moves, [((3,), 7, 5)])

    def test_the_same_index_in_another_grid_is_not_the_dragged_key(self) -> None:
        """Without the path, dropping on slot 7 of a folder would be refused."""
        moves: list = []
        window = self._drop_window(moves, ((), 7), 7, path=(3,))

        accepted = MainWindow._on_drop(
            window,
            None,
            "linuxstreamdeck-key:7",
            20,
            20,
        )

        self.assertTrue(accepted)
        self.assertEqual(moves, [((), 7, 7)])

    def test_dropping_a_key_on_itself_is_still_refused(self) -> None:
        moves: list = []
        window = self._drop_window(moves, ((3,), 7), 7, path=(3,))

        accepted = MainWindow._on_drop(
            window,
            None,
            "linuxstreamdeck-key:7",
            20,
            20,
        )

        self.assertFalse(accepted)
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


class _FakeButton:
    def __init__(self, label: str = "") -> None:
        self.classes: set[str] = set()
        self.label = label

    def add_css_class(self, name: str) -> None:
        self.classes.add(name)

    def remove_css_class(self, name: str) -> None:
        self.classes.discard(name)

    def get_label(self) -> str:
        return self.label

    def set_label(self, label: str) -> None:
        self.label = label


class _FakePaned:
    """Just enough Gtk.Paned to exercise the width restore and the save.

    A real one is no use here: an offscreen window never runs a proper
    allocation cycle, so its width stays 0 and set_position does nothing --
    which is precisely the state the retry exists for, and would make every
    one of these tests pass for the wrong reason.
    """

    def __init__(self, width: int = 0, position: int | None = None) -> None:
        self.width = width
        self.position = position

    def get_width(self) -> int:
        return self.width

    def get_position(self) -> int:
        return self.position or 0

    def set_position(self, position: int) -> None:
        self.position = position


def _width_stub(paned, config=None, restored=False):
    """A stand-in MainWindow carrying the real width methods.

    They call each other, so binding them is what makes the retry path
    reachable at all.
    """
    from linuxstreamdeck.ui.window import MainWindow

    window = SimpleNamespace(
        _paned=paned,
        _width_restored=restored,
        _width_save_id=0,
        app=SimpleNamespace(config=config),
    )
    for name in (
        "_restore_editor_width",
        "_apply_editor_width",
        "_on_editor_width_changed",
        "_save_editor_width",
    ):
        setattr(window, name, getattr(MainWindow, name).__get__(window, type(window)))
    return window


class _FakeGLib:
    """Just enough of GLib to run the hover timer without a main loop."""

    def __init__(self) -> None:
        self.timers: dict[int, tuple] = {}
        self.removed: list[int] = []
        self._next = 1

    def timeout_add(self, _ms, callback, *args) -> int:
        source = self._next
        self._next += 1
        self.timers[source] = (callback, args)
        return source

    def source_remove(self, source: int) -> None:
        self.removed.append(source)
        self.timers.pop(source, None)

    def fire(self, source: int):
        callback, args = self.timers.pop(source)
        return callback(*args)


class SpringLoadedFolderTests(unittest.TestCase):
    """A drag resting on a folder opens it, so the key can be dropped inside."""

    def setUp(self) -> None:
        self.glib = _FakeGLib()
        patcher = unittest.mock.patch(
            "linuxstreamdeck.ui.window.GLib", self.glib
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.navigated: list = []
        self.status: list[str] = []

    def _window(
        self,
        *,
        path: tuple[int, ...] = (),
        folders: tuple[int, ...] = (3,),
        keys: tuple[int, ...] = (5, 9),
        source=((), 5),
        unsaved: bool = False,
        depth_left: bool = True,
    ):
        def key(index):
            if index in folders:
                return SimpleNamespace(contents=object())
            if index in keys:
                return SimpleNamespace(contents=None)
            return None

        window = SimpleNamespace(
            app=SimpleNamespace(
                controller=SimpleNamespace(
                    folder_path=path,
                    is_reserved_key=lambda i: bool(path) and i == 0,
                    container=SimpleNamespace(key=key),
                    can_add_folder=lambda: depth_left,
                    open_folder=lambda i: self.navigated.append(("open", i)),
                    close_folder=lambda: self.navigated.append(("back",)),
                )
            ),
            editor=SimpleNamespace(has_unsaved_changes=lambda: unsaved),
            _drag_source=source,
            _key_buttons=[_FakeButton() for _ in range(15)],
            _drag_destination_index=None,
            _spring_index=None,
            _spring_timer=None,
            _flash_status=self.status.append,
        )
        for name in (
            "_is_drag_source",
            "_springs_open",
            "_arm_spring",
            "_cancel_spring",
            "_spring_open",
            "_set_drag_destination",
            "_refresh_drag_source_feedback",
        ):
            setattr(window, name, getattr(MainWindow, name).__get__(window))
        return window

    # --- what springs open ---

    def test_a_folder_key_springs_open(self) -> None:
        window = self._window()
        self.assertTrue(window._springs_open(3))

    def test_an_ordinary_key_does_not(self) -> None:
        window = self._window()
        # 9 holds a key that is not a folder, 11 holds nothing at all.
        self.assertFalse(window._springs_open(9))
        self.assertFalse(window._springs_open(11))

    def test_the_back_key_springs_out_so_a_key_can_leave_a_folder(self) -> None:
        window = self._window(path=(3,))
        self.assertTrue(window._springs_open(0))

    def test_a_folder_at_the_depth_limit_does_not_open(self) -> None:
        window = self._window(depth_left=False)
        self.assertFalse(window._springs_open(3))

    def test_the_key_being_dragged_never_springs_open_under_itself(self) -> None:
        window = self._window(source=((), 3))
        self.assertFalse(window._springs_open(3))

    def test_nothing_springs_open_without_a_drag(self) -> None:
        window = self._window(source=None)
        self.assertFalse(window._springs_open(3))

    # --- the countdown ---

    def test_resting_on_a_folder_marks_it_and_arms_the_timer(self) -> None:
        window = self._window()

        window._arm_spring(3)

        self.assertEqual(window._spring_index, 3)
        self.assertIn("spring-target", window._key_buttons[3].classes)
        self.assertEqual(len(self.glib.timers), 1)

    def test_moving_within_the_same_key_keeps_the_countdown_running(self) -> None:
        window = self._window()

        window._arm_spring(3)
        armed = window._spring_timer
        window._arm_spring(3)

        self.assertEqual(window._spring_timer, armed)
        self.assertEqual(self.glib.removed, [])

    def test_crossing_to_another_key_restarts_it(self) -> None:
        """Passing over a folder on the way somewhere else opens nothing."""
        window = self._window()

        window._arm_spring(3)
        first = window._spring_timer
        window._arm_spring(5)

        self.assertEqual(self.glib.removed, [first])
        self.assertIsNone(window._spring_timer)
        self.assertNotIn("spring-target", window._key_buttons[3].classes)

    def test_leaving_the_grid_cancels_it(self) -> None:
        window = self._window()

        window._arm_spring(3)
        window._arm_spring(None)

        self.assertIsNone(window._spring_timer)
        self.assertEqual(self.glib.timers, {})

    # --- what firing does ---

    def test_the_timer_enters_the_folder(self) -> None:
        window = self._window()
        window._arm_spring(3)

        self.glib.fire(window._spring_timer)

        self.assertEqual(self.navigated, [("open", 3)])
        self.assertNotIn("spring-target", window._key_buttons[3].classes)
        self.assertIsNone(window._spring_index)

    def test_the_timer_on_the_back_key_leaves_the_folder(self) -> None:
        window = self._window(path=(3,))
        window._arm_spring(0)

        self.glib.fire(window._spring_timer)

        self.assertEqual(self.navigated, [("back",)])

    def test_an_unsaved_key_edit_stops_it_rather_than_asking(self) -> None:
        """A drag in progress cannot put up a modal dialog, and entering a
        folder clears the selection the editor is holding."""
        window = self._window(unsaved=True)
        window._arm_spring(3)

        self.glib.fire(window._spring_timer)

        self.assertEqual(self.navigated, [])
        self.assertEqual(len(self.status), 1)

    def test_the_source_stays_marked_only_in_its_own_grid(self) -> None:
        window = self._window()
        window._refresh_drag_source_feedback()
        self.assertIn("drag-source", window._key_buttons[5].classes)

        # The folder sprang open: slot 5 is now somebody else's key.
        window.app.controller.folder_path = (3,)
        window._refresh_drag_source_feedback()

        self.assertNotIn("drag-source", window._key_buttons[5].classes)

    def test_coming_back_out_marks_the_source_again(self) -> None:
        window = self._window(path=(3,))
        window._refresh_drag_source_feedback()
        self.assertNotIn("drag-source", window._key_buttons[5].classes)

        window.app.controller.folder_path = ()
        window._refresh_drag_source_feedback()

        self.assertIn("drag-source", window._key_buttons[5].classes)


class PressFeedbackTests(unittest.TestCase):
    """Buttons whose result is not on the button have to say they were pressed.

    Save writes to disk and to the deck, Test runs the key, and the editor
    panel looks identical afterwards either way; a quick click never even
    paints the theme's own pressed state, since press and release land in the
    same frame.
    """

    def setUp(self) -> None:
        self.glib = _FakeGLib()
        patcher = unittest.mock.patch(
            "linuxstreamdeck.ui.editor.GLib", self.glib
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_press_lights_the_button_and_names_what_happened(self) -> None:
        button = _FakeButton("Save")

        acknowledge_press(button, "Saved")

        self.assertEqual(button.label, "Saved")
        self.assertIn("press-echo-on", button.classes)

    def test_it_goes_back_to_normal_by_itself(self) -> None:
        button = _FakeButton("Save")

        acknowledge_press(button, "Saved")
        self.glib.fire(button._echo_timer)

        self.assertEqual(button.label, "Save")
        self.assertNotIn("press-echo-on", button.classes)
        # The base class carries the fade back, so it has to stay.
        self.assertIn("press-echo", button.classes)

    def test_pressing_again_while_lit_does_not_capture_the_echo(self) -> None:
        """Otherwise the button would read "Saved" for the rest of the session."""
        button = _FakeButton("Save")

        acknowledge_press(button, "Saved")
        first = button._echo_timer
        acknowledge_press(button, "Saved")

        self.assertEqual(self.glib.removed, [first])
        self.glib.fire(button._echo_timer)
        self.assertEqual(button.label, "Save")

    def test_a_button_with_no_word_only_lights_up(self) -> None:
        button = _FakeButton("Test")

        acknowledge_press(button)
        self.assertEqual(button.label, "Test")

        self.glib.fire(button._echo_timer)
        self.assertEqual(button.label, "Test")

    def test_save_confirms_only_when_a_key_was_actually_saved(self) -> None:
        button = _FakeButton("Save")
        panel = SimpleNamespace(save=lambda: False)

        EditorPanel._save(panel, button)

        self.assertEqual(button.label, "Save")
        self.assertEqual(button.classes, set())

    def test_save_confirms_when_it_wrote_the_key(self) -> None:
        button = _FakeButton("Save")
        panel = SimpleNamespace(save=lambda: True)

        EditorPanel._save(panel, button)

        self.assertEqual(button.label, "Saved")

    def test_test_acknowledges_the_press_after_running_the_key(self) -> None:
        button = _FakeButton("Test")
        ran: list = []
        panel = SimpleNamespace(
            index=4,
            app=SimpleNamespace(
                controller=SimpleNamespace(
                    press=lambda i, g: ran.append((i, g))
                )
            ),
        )

        EditorPanel._test(panel, button, "single")

        self.assertEqual(ran, [(4, "single")])
        self.assertIn("press-echo-on", button.classes)

    def test_no_selected_key_neither_runs_nor_confirms(self) -> None:
        button = _FakeButton("Test")
        panel = SimpleNamespace(index=None, app=None)

        EditorPanel._test(panel, button, "single")

        self.assertEqual(button.classes, set())


class EditorWidthTests(unittest.TestCase):
    """The editor panel must not change how much width it claims by itself.

    A pane shares its spare width only between the children that resize. The
    panel's Save button hexpands and the whole button row is hidden while no
    key is selected, so the panel's *inherited* expand flag flipped off when
    the editor was empty and on again when a key was loaded. The key grid
    therefore went from receiving all the spare width to half of it on the very
    click that selected a key, and because the grid box is centered it slid
    left by a quarter of that -- 247 px measured at 1920.

    The pointer was then two keys away from the one it had just been over,
    which broke the second click of a folder's double-click. None of it is
    visible in the code, and none of it happens in a window small enough to
    have no spare width, which is why it needs pinning here.

    Dragging the handle is the one thing that may change the width, and that is
    the user asking for it.
    """

    def setUp(self) -> None:
        if not GridHintWidthTests._gtk_ready():
            self.skipTest("GTK needs a display to measure widgets")

    def test_the_panel_never_expands_on_its_own(self) -> None:
        from gi.repository import Gtk

        from linuxstreamdeck.ui.window import MainWindow

        source = inspect.getsource(MainWindow._build_ui)
        self.assertIn("self.editor.set_hexpand(False)", source)

        # And that it is the explicit flag rather than a hopeful default:
        # without it the value is computed from whatever the panel holds.
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        box.append(Gtk.Button(label="Save", hexpand=True))
        self.assertTrue(box.compute_expand(Gtk.Orientation.HORIZONTAL))
        box.set_hexpand(False)
        self.assertFalse(box.compute_expand(Gtk.Orientation.HORIZONTAL))

    def test_the_button_row_cannot_move_the_handle(self) -> None:
        """The panel's expand flag must stay put as its contents come and go,
        or selecting a key renegotiates the split all over again."""
        from gi.repository import Gtk

        from linuxstreamdeck.core.config import MIN_EDITOR_WIDTH

        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        panel.set_size_request(MIN_EDITOR_WIDTH, -1)
        panel.set_hexpand(False)
        buttons = Gtk.Box()
        buttons.append(Gtk.Button(label="Save", hexpand=True))
        panel.append(buttons)

        expands = []
        for visible in (False, True):
            buttons.set_visible(visible)
            expands.append(panel.compute_expand(Gtk.Orientation.HORIZONTAL))

        self.assertEqual(
            expands,
            [False, False],
            "the panel's expand flag still follows its button row, so the "
            "key grid will jump the moment a key is selected",
        )

    def test_the_deck_is_what_stops_the_handle(self) -> None:
        """The cap is the grid's own minimum width rather than a number kept
        somewhere else, so it cannot drift from the deck actually connected."""
        from linuxstreamdeck.ui.window import MainWindow

        source = inspect.getsource(MainWindow._build_ui)
        self.assertIn("content.set_shrink_start_child(False)", source)
        self.assertIn("content.set_shrink_end_child(False)", source)
        # The grid area absorbs a window resize; the panel keeps its width.
        self.assertIn("content.set_resize_start_child(True)", source)
        self.assertIn("content.set_resize_end_child(False)", source)

    def test_the_stored_width_is_never_narrower_than_the_panel_needs(self) -> None:
        """The panel asked for 380 px before any of this, and measures the same
        in every state, so a lower bound below that squeezes every key's
        parameter rows rather than only the crowded ones."""
        from linuxstreamdeck.core.config import (
            DEFAULT_EDITOR_WIDTH, MAX_EDITOR_WIDTH, MIN_EDITOR_WIDTH,
        )

        self.assertGreaterEqual(MIN_EDITOR_WIDTH, 380)
        self.assertGreaterEqual(DEFAULT_EDITOR_WIDTH, MIN_EDITOR_WIDTH)
        self.assertGreater(MAX_EDITOR_WIDTH, DEFAULT_EDITOR_WIDTH)

    def test_the_remembered_width_becomes_a_handle_position(self) -> None:
        window = _width_stub(
            _FakePaned(width=1600), SimpleNamespace(editor_width=640)
        )
        window._restore_editor_width(None)

        # 1600 wide, 640 of it the panel: the handle sits at 960.
        self.assertEqual(window._paned.position, 960)

    def test_restoring_it_happens_once_and_not_on_every_reopen(self) -> None:
        """`map` fires again when the window comes back from the status area,
        and re-running this would throw away a width set since."""
        window = _width_stub(
            _FakePaned(width=1600), SimpleNamespace(editor_width=640)
        )
        window._restore_editor_width(None)
        window._paned.position = 1200          # the user drags it since
        window._restore_editor_width(None)

        self.assertEqual(window._paned.position, 1200)

    def test_it_gives_up_rather_than_spinning_on_a_pane_with_no_width(self) -> None:
        """The pane has no width at `map`, so the retry is the normal path --
        which is exactly why it needs a bound."""
        window = _width_stub(_FakePaned(width=0))
        scheduled: list = []
        with unittest.mock.patch(
            "linuxstreamdeck.ui.window.GLib.timeout_add",
            lambda _ms, fn, *args: scheduled.append((fn, args)),
        ):
            window._apply_editor_width(640, 1)
            self.assertEqual(len(scheduled), 1, "it never retried at all")
            _fn, args = scheduled[0]
            window._apply_editor_width(*args)

        self.assertEqual(len(scheduled), 1, "the retry never stops")
        self.assertIsNone(window._paned.position)

    def test_a_drag_is_written_once_it_comes_to_rest(self) -> None:
        """A drag emits a position change per pixel, and every save is a file
        write plus a backup rotation."""
        saves: list = []
        config = SimpleNamespace(editor_width=440, save=lambda: saves.append(1))
        window = _width_stub(
            _FakePaned(width=1600, position=960), config, restored=True
        )
        pending: list = []
        with unittest.mock.patch(
            "linuxstreamdeck.ui.window.GLib.timeout_add",
            lambda _ms, fn: (pending.append(fn), 7)[1],
        ), unittest.mock.patch(
            "linuxstreamdeck.ui.window.GLib.source_remove", lambda _id: None
        ):
            for _ in range(40):                 # the drag itself
                window._on_editor_width_changed(None, None)

            self.assertEqual(saves, [], "saved while still dragging")
            pending[-1]()                       # it comes to rest

        self.assertEqual(config.editor_width, 640)
        self.assertEqual(len(saves), 1)

    def test_a_position_set_before_the_restore_is_not_a_choice(self) -> None:
        """GTK gives the pane a position of its own during the first
        allocation, which arrives before the remembered width is applied.
        Treating that as a drag would overwrite the stored width with a default
        the user never picked -- and it has to be caught as *nothing
        scheduled*, since with no main loop a scheduled save never runs and an
        assertion about saves passes either way.
        """
        saves: list = []
        window = _width_stub(
            _FakePaned(width=1600, position=960),
            SimpleNamespace(editor_width=440, save=lambda: saves.append(1)),
        )
        scheduled: list = []
        with unittest.mock.patch(
            "linuxstreamdeck.ui.window.GLib.timeout_add",
            lambda _ms, fn: (scheduled.append(fn), 7)[1],
        ):
            window._on_editor_width_changed(None, None)

        self.assertEqual(scheduled, [], "the initial position was saved")
        self.assertEqual(saves, [])

    def test_the_panel_reserves_room_for_its_own_scrollbar(self) -> None:
        """An overlay scrollbar is drawn over the content rather than taking
        width of its own, and it landed on the right-hand end of every row --
        the arrow of each dropdown, the action picker's search button. Fine
        over a document, wrong over a column of controls."""
        from gi.repository import Gtk

        from linuxstreamdeck.ui.editor import EditorPanel

        source = inspect.getsource(EditorPanel.__init__)
        self.assertIn("self.scroller.set_overlay_scrolling(False)", source)

        scroller = Gtk.ScrolledWindow()
        self.assertTrue(scroller.get_overlay_scrolling())
        scroller.set_overlay_scrolling(False)
        self.assertFalse(scroller.get_overlay_scrolling())


class WindowStylesheetTests(unittest.TestCase):
    """The stylesheet is one byte blob that nothing else validates."""

    def setUp(self) -> None:
        if not GridHintWidthTests._gtk_ready():
            self.skipTest("GTK needs a display to parse a stylesheet")

    def test_the_stylesheet_parses_without_a_single_error(self) -> None:
        from gi.repository import Gtk

        from linuxstreamdeck.ui.window import _CSS

        # GtkCssProvider reports a bad rule on a signal instead of raising, so
        # a typo here is silently dropped and the style it carried never
        # appears -- with nothing in the log that names the file.
        provider = Gtk.CssProvider()
        errors: list[str] = []
        provider.connect(
            "parsing-error",
            lambda _p, _section, error: errors.append(error.message),
        )
        provider.load_from_data(_CSS)

        self.assertEqual(errors, [])

    def test_every_class_the_editor_applies_is_styled(self) -> None:
        """They are added in ui/editor.py and defined in ui/window.py."""
        from linuxstreamdeck.ui.window import _CSS

        source = inspect.getsource(acknowledge_press)
        applied = {
            name
            for name in ("press-echo", "press-echo-on")
            if f'"{name}"' in source
        }
        self.assertEqual(applied, {"press-echo", "press-echo-on"})
        for name in applied:
            self.assertIn(f".{name}".encode(), _CSS)


if __name__ == "__main__":
    unittest.main()
