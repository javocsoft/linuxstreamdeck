"""Editor step widgets, in particular dropdowns whose options depend on another
parameter (a scene's sources, a source's filters)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from linuxstreamdeck import basic_actions as _basic_actions  # noqa: E402,F401
from linuxstreamdeck.core.config import ActionStep, Config  # noqa: E402
from linuxstreamdeck.obs import actions as _obs_actions  # noqa: E402,F401

HAS_DISPLAY = Gtk.init_check()


class FakeObs:
    """Just enough of OBSClient for the editor's dropdowns.

    `_fetch_choices` binds every getter into one lookup table, so a double has
    to answer all of them even when a test only exercises one.
    """

    def __init__(self, connected: bool = True) -> None:
        self.connected = connected
        self.scenes = ["Empty scene", "Full scene"]
        self.sources = {"Empty scene": [], "Full scene": ["Camera", "Mic"]}
        self.audio = {"Empty scene": ["Mic/Aux"], "Full scene": ["Mic", "Mic/Aux"]}
        self.filters = {"Camera": ["Color Correction"], "Mic": []}

    def get_scenes(self) -> list[str]:
        return list(self.scenes)

    def get_sources_in_scene(self, scene: str) -> list[str]:
        return list(self.sources.get(scene, []))

    def get_audio_sources_in_scene(self, scene: str) -> list[str]:
        return list(self.audio.get(scene, []))

    def get_filters_of_source(self, source: str) -> list[str]:
        return list(self.filters.get(source, []))

    def get_inputs(self) -> list[str]:
        return []

    def get_media_inputs(self) -> list[str]:
        return []

    def get_transitions(self) -> list[str]:
        return []

    def get_scene_collections(self) -> list[str]:
        return []

    def get_profiles(self) -> list[str]:
        return []

    def get_hotkeys(self) -> list[str]:
        return []


def fake_app(obs: FakeObs) -> SimpleNamespace:
    return SimpleNamespace(obs=obs, config=Config())


@unittest.skipUnless(HAS_DISPLAY, "GTK needs a display to build widgets")
class DependentDropdownTests(unittest.TestCase):
    def setUp(self) -> None:
        from linuxstreamdeck.ui.steps import StepEditor

        self.obs = FakeObs()
        self.editor = StepEditor(fake_app(self.obs))

    def _widget(self, name: str):
        return self.editor._param_widgets[name][1]

    def _select(self, name: str, option: str) -> None:
        """Pick an option by its visible text, as a user would."""
        widget = self._widget(name)
        model = widget.get_model()
        labels = [model.get_string(i) for i in range(model.get_n_items())]
        widget.set_selected(labels.index(option))

    def test_sources_appear_after_choosing_a_scene_with_sources(self) -> None:
        """The first scene may have no sources; picking another must still work."""
        self.editor.load(ActionStep(action="obs.source_visibility"))
        self.assertEqual(self.editor.get_step().params["scene"], "Empty scene")

        self._select("scene", "Full scene")

        source = self._widget("source")
        self.assertIsInstance(source, Gtk.DropDown)
        model = source.get_model()
        self.assertEqual(
            [model.get_string(i) for i in range(model.get_n_items())],
            ["Camera", "Mic"],
        )
        self.assertEqual(self.editor.get_step().params["source"], "Camera")

    def test_sources_are_listed_when_the_first_scene_has_them(self) -> None:
        self.obs.scenes = ["Full scene", "Empty scene"]
        self.editor.load(ActionStep(action="obs.source_visibility"))

        self.assertIsInstance(self._widget("source"), Gtk.DropDown)
        self.assertEqual(self.editor.get_step().params["source"], "Camera")

    def test_choosing_a_scene_without_sources_empties_the_list(self) -> None:
        self.obs.scenes = ["Full scene", "Empty scene"]
        self.editor.load(ActionStep(action="obs.source_visibility"))

        self._select("scene", "Empty scene")

        source = self._widget("source")
        self.assertIsInstance(source, Gtk.DropDown)
        self.assertEqual(source.get_model().get_n_items(), 0)
        self.assertEqual(self.editor.get_step().params["source"], "")

    def test_filters_follow_the_selected_source(self) -> None:
        self.editor.load(ActionStep(action="obs.filter"))
        # obs.filter has no scene parameter, so its sources come from the scene
        # OBS reports as current.
        self.obs.sources[""] = ["Camera", "Mic"]
        self.editor.load(ActionStep(action="obs.filter"))

        self._select("source", "Camera")

        filters = self._widget("filter")
        self.assertIsInstance(filters, Gtk.DropDown)
        model = filters.get_model()
        self.assertEqual(
            [model.get_string(i) for i in range(model.get_n_items())],
            ["Color Correction"],
        )

    def test_a_saved_value_survives_an_empty_option_list(self) -> None:
        """Opening a key while OBS reports nothing must not drop its value."""
        self.editor.load(
            ActionStep(
                action="obs.source_visibility",
                params={"scene": "Empty scene", "source": "Overlay"},
            )
        )
        self.assertEqual(self.editor.get_step().params["source"], "Overlay")

    def test_a_disconnected_obs_still_allows_typing_a_value(self) -> None:
        self.obs.connected = False
        self.editor.load(ActionStep(action="obs.source_visibility"))

        self.assertIsInstance(self._widget("scene"), Gtk.Entry)
        self.assertIsInstance(self._widget("source"), Gtk.Entry)


@unittest.skipUnless(HAS_DISPLAY, "GTK needs a display to build widgets")
class AudioInputDropdownTests(unittest.TestCase):
    """An audio action lists the audio inputs of the chosen scene, not all."""

    def setUp(self) -> None:
        from linuxstreamdeck.ui.steps import StepEditor

        self.obs = FakeObs()
        self.editor = StepEditor(fake_app(self.obs))
        self.editor.load(ActionStep(action="obs.mute"))

    def _options(self, name: str) -> list[str]:
        model = self.editor._param_widgets[name][1].get_model()
        return [model.get_string(i) for i in range(model.get_n_items())]

    def test_the_inputs_are_the_audio_ones_of_the_first_scene(self) -> None:
        self.assertEqual(self._options("scene"), ["Empty scene", "Full scene"])
        self.assertEqual(self._options("input"), ["Mic/Aux"])

    def test_choosing_another_scene_updates_the_inputs(self) -> None:
        self.editor._param_widgets["scene"][1].set_selected(1)

        self.assertEqual(self._options("input"), ["Mic", "Mic/Aux"])

    def test_an_input_available_in_both_scenes_stays_selected(self) -> None:
        """A global device such as Mic/Aux is in every scene, so keep it."""
        self.editor._param_widgets["scene"][1].set_selected(1)

        self.assertEqual(self.editor.get_step().params["input"], "Mic/Aux")

    def test_an_input_missing_from_the_new_scene_is_dropped(self) -> None:
        self.editor._param_widgets["scene"][1].set_selected(1)
        inputs = self.editor._param_widgets["input"][1]
        inputs.set_selected(self._options("input").index("Mic"))

        self.editor._param_widgets["scene"][1].set_selected(0)

        self.assertEqual(self._options("input"), ["Mic/Aux"])
        self.assertEqual(self.editor.get_step().params["input"], "Mic/Aux")

    def test_video_only_sources_never_reach_the_input_list(self) -> None:
        self.editor._param_widgets["scene"][1].set_selected(1)
        # "Camera" is a source of that scene, but not an audio one.
        self.assertIn("Camera", self.obs.get_sources_in_scene("Full scene"))
        self.assertNotIn("Camera", self._options("input"))


@unittest.skipUnless(HAS_DISPLAY, "GTK needs a display to build widgets")
class StepNameTests(unittest.TestCase):
    """An optional per-step name, so a long list stays readable."""

    def setUp(self) -> None:
        from linuxstreamdeck.ui.steps import StepList

        self.app = fake_app(FakeObs())
        self.list = StepList(self.app)

    def _titles(self) -> list[str]:
        return [
            self.list.step_title(i) for i in range(len(self.list._editors))
        ]

    def test_a_step_without_a_name_is_listed_by_its_action(self) -> None:
        self.list.load([ActionStep(action="obs.record")])
        self.assertEqual(self._titles(), ["1. Record on/off"])

    def test_a_named_step_is_listed_by_its_name(self) -> None:
        self.list.load(
            [ActionStep(action="obs.record", label="Roll camera")]
        )
        self.assertEqual(self._titles(), ["1. Roll camera"])

    def test_typing_a_name_renames_the_list_entry_immediately(self) -> None:
        self.list.load([ActionStep(action="obs.record")])
        self.list._editors[0].label_entry.set_text("Roll camera")
        self.assertEqual(self._titles(), ["1. Roll camera"])

    def test_clearing_the_name_falls_back_to_the_action(self) -> None:
        self.list.load(
            [ActionStep(action="obs.record", label="Roll camera")]
        )
        self.list._editors[0].label_entry.set_text("")
        self.assertEqual(self._titles(), ["1. Record on/off"])

    def test_a_name_of_only_spaces_does_not_hide_the_action(self) -> None:
        self.list.load([ActionStep(action="obs.record", label="   ")])
        self.assertEqual(self._titles(), ["1. Record on/off"])
        self.assertEqual(self.list.get_steps()[0].label, "")

    def test_names_survive_reading_the_steps_back(self) -> None:
        self.list.load(
            [
                ActionStep(action="obs.record", label="Roll camera"),
                ActionStep(action="sys.wait", params={"duration": "00:05"}),
            ]
        )
        steps = self.list.get_steps()
        self.assertEqual([s.label for s in steps], ["Roll camera", ""])

    def test_names_follow_a_step_that_is_moved(self) -> None:
        self.list.load(
            [
                ActionStep(action="obs.record", label="Roll camera"),
                ActionStep(action="sys.wait", label="Breathe"),
            ]
        )
        self.list._move(0, +1)
        self.assertEqual(self._titles(), ["1. Breathe", "2. Roll camera"])

    def test_a_single_action_key_has_no_step_name_field(self) -> None:
        """Its own label already lives in Appearance; a second one would confuse."""
        from linuxstreamdeck.ui.steps import StepEditor

        editor = StepEditor(self.app)
        editor.load(ActionStep(action="obs.record"))
        self.assertIsNone(editor.label_entry)
        self.assertEqual(editor.get_step().label, "")


@unittest.skipUnless(HAS_DISPLAY, "GTK needs a display to build widgets")
class StepListExpansionTests(unittest.TestCase):
    """Rebuilding the list must not reopen rows the user closed."""

    def setUp(self) -> None:
        from linuxstreamdeck.ui.steps import StepList

        self.list = StepList(fake_app(FakeObs()))
        self.list.load(
            [
                ActionStep(action="obs.record"),
                ActionStep(action="obs.stream"),
            ]
        )

    def _expanded(self) -> list[bool]:
        return [editor._expander.get_expanded() for editor in self.list._editors]

    def _add_action(self) -> None:
        self.list._add(ActionStep(), expand=True)

    def test_a_new_step_starts_open_and_the_others_stay_closed(self) -> None:
        self.assertEqual(self._expanded(), [False, False])

        self._add_action()

        self.assertEqual(self._expanded(), [False, False, True])

    def test_adding_a_step_does_not_reopen_a_row_the_user_closed(self) -> None:
        """The reported bug: earlier steps opened on their own."""
        self._add_action()
        self.list._editors[2]._expander.set_expanded(False)

        self._add_action()

        self.assertEqual(self._expanded(), [False, False, False, True])

    def test_adding_a_step_keeps_a_row_the_user_opened(self) -> None:
        self.list._editors[0]._expander.set_expanded(True)

        self._add_action()

        self.assertEqual(self._expanded(), [True, False, True])

    def test_moving_a_step_preserves_every_open_state(self) -> None:
        self.list._editors[0]._expander.set_expanded(True)

        self.list._move(0, +1)

        self.assertEqual(self._expanded(), [False, True])

    def test_deleting_a_step_preserves_the_open_state_of_the_rest(self) -> None:
        self.list._editors[1]._expander.set_expanded(True)

        self.list._delete(0)

        self.assertEqual(self._expanded(), [True])

    def test_adding_twice_in_a_row_keeps_the_rows_intact(self) -> None:
        self._add_action()
        self._add_action()

        for editor in self.list._editors:
            self.assertTrue(
                _contains(editor._expander.get_child(), editor),
                "a step is not inside the row that represents it",
            )

    def test_a_rebuild_works_while_the_previous_rows_are_still_referenced(
        self,
    ) -> None:
        """Re-parenting must not depend on the old row being garbage collected.

        Anything still holding it — a queued scroll, a signal handler — used to
        leave the step attached to the row it had just been taken out of.
        """
        held = [editor._expander for editor in self.list._editors]

        self._add_action()

        for editor in self.list._editors:
            self.assertTrue(
                _contains(editor._expander.get_child(), editor),
                "a step is not inside the row that represents it",
            )
        self.assertEqual(len(held), 2)


def _contains(container: Gtk.Widget, widget: Gtk.Widget) -> bool:
    """Whether widget sits below container in the widget tree.

    It walks down from the expander's child rather than from the expander
    itself: a collapsed GtkExpander keeps its child out of the traversable
    tree, while get_child() returns it either way.
    """
    child = container.get_first_child()
    while child is not None:
        if child is widget or _contains(child, widget):
            return True
        child = child.get_next_sibling()
    return False

    def test_revealing_a_step_outside_a_scrolled_window_is_harmless(self) -> None:
        from linuxstreamdeck.ui.steps import StepList

        self.assertFalse(StepList._scroll_into_view(self.list._editors[0]))


@unittest.skipUnless(HAS_DISPLAY, "GTK needs a display to build widgets")
class StepDuplicateTests(unittest.TestCase):
    def setUp(self) -> None:
        from linuxstreamdeck.ui.steps import StepList

        self.list = StepList(fake_app(FakeObs()))

    def _titles(self) -> list[str]:
        return [
            self.list.step_title(i) for i in range(len(self.list._editors))
        ]

    def test_a_duplicate_is_appended_with_the_same_settings(self) -> None:
        self.list.load(
            [
                ActionStep(action="obs.record", label="Roll camera"),
                ActionStep(action="sys.wait", params={"duration": "00:05"}),
            ]
        )

        self.list._duplicate(0)

        steps = self.list.get_steps()
        self.assertEqual(
            [(s.action, s.label) for s in steps],
            [
                ("obs.record", "Roll camera"),
                ("sys.wait", ""),
                ("obs.record", "Roll camera"),
            ],
        )

    def test_a_duplicate_copies_the_parameters(self) -> None:
        self.list.load(
            [ActionStep(action="sys.wait", params={"duration": "00:07"})]
        )

        self.list._duplicate(0)

        steps = self.list.get_steps()
        self.assertEqual(steps[1].params["duration"], "00:07")

    def test_editing_a_duplicate_leaves_the_original_alone(self) -> None:
        self.list.load([ActionStep(action="obs.record", label="Roll camera")])

        self.list._duplicate(0)
        self.list._editors[1].label_entry.set_text("Stop camera")

        steps = self.list.get_steps()
        self.assertEqual([s.label for s in steps], ["Roll camera", "Stop camera"])

    def test_a_duplicate_opens_and_leaves_the_others_as_they_were(self) -> None:
        self.list.load(
            [
                ActionStep(action="obs.record"),
                ActionStep(action="obs.stream"),
            ]
        )

        self.list._duplicate(0)

        self.assertEqual(
            [editor._expander.get_expanded() for editor in self.list._editors],
            [False, False, True],
        )
        self.assertEqual(self._titles()[2], "3. Record on/off")

    def test_duplicating_an_index_that_is_gone_does_nothing(self) -> None:
        self.list.load([ActionStep(action="obs.record")])

        self.list._duplicate(5)

        self.assertEqual(len(self.list.get_steps()), 1)


@unittest.skipUnless(HAS_DISPLAY, "GTK needs a display to build widgets")
class StepRowLayoutTests(unittest.TestCase):
    """The controls sit in the row header, so no step has to be opened."""

    def setUp(self) -> None:
        from linuxstreamdeck.ui.steps import StepList

        self.list = StepList(fake_app(FakeObs()))
        self.list.load(
            [
                ActionStep(action="obs.record", label="Roll camera"),
                ActionStep(action="sys.wait"),
            ]
        )

    def _header_children(self, index: int) -> list[Gtk.Widget]:
        header = self.list._editors[index]._expander.get_label_widget()
        children, child = [], header.get_first_child()
        while child is not None:
            children.append(child)
            child = child.get_next_sibling()
        return children

    def test_the_header_carries_the_handle_title_and_controls(self) -> None:
        children = self._header_children(0)

        self.assertIsInstance(children[0], Gtk.Image)
        self.assertIsInstance(children[1], Gtk.Label)
        self.assertEqual(children[1].get_text(), "1. Roll camera")
        self.assertEqual(
            [c.get_tooltip_text() for c in children[2:]],
            [
                "Move up",
                "Move down",
                "Duplicate this action at the end of the list",
                "Remove action",
            ],
        )

    def test_the_title_expands_so_the_controls_sit_at_the_end(self) -> None:
        self.assertTrue(self._header_children(0)[1].get_hexpand())

    def test_the_step_editor_is_the_only_thing_left_inside(self) -> None:
        content = self.list._editors[0]._expander.get_child()
        self.assertIs(content.get_first_child(), self.list._editors[0])

    def test_the_remove_button_is_styled_red_while_staying_flat(self) -> None:
        """destructive-action paints a background a flat button drops, which
        left the icon in the normal text colour."""
        remove = self._header_children(0)[-1]

        self.assertTrue(remove.has_css_class("step-remove"))
        self.assertTrue(remove.has_css_class("flat"))
        self.assertFalse(remove.has_css_class("destructive-action"))

    def test_a_row_cannot_open_itself_while_something_is_dragged_over_it(
        self,
    ) -> None:
        """GtkExpander auto-expands on drag hover; a reorder must not."""
        for editor in self.list._editors:
            controllers = list(editor._expander.observe_controllers())
            self.assertFalse(
                any(
                    isinstance(c, Gtk.DropControllerMotion)
                    for c in controllers
                ),
                "the built-in hover-expand controller is still installed",
            )
            # The reordering controllers are still there.
            self.assertTrue(
                any(isinstance(c, Gtk.DragSource) for c in controllers)
            )
            self.assertTrue(
                any(isinstance(c, Gtk.DropTarget) for c in controllers)
            )

    def test_moving_a_step_hands_focus_to_the_arrow_that_moved_it(self) -> None:
        """A viewport scrolls to whatever takes focus, so the rebuilt arrow has
        to take it back or the list jumps to the top."""
        asked: list[tuple[int, str]] = []
        self.list._focus_control = lambda index, name: asked.append((index, name))

        self.list._move(0, +1)
        self.assertEqual(asked, [(1, "down")])

        asked.clear()
        self.list._move(1, -1)
        self.assertEqual(asked, [(0, "up")])

    def test_each_row_exposes_its_controls_by_name(self) -> None:
        controls = self.list._editors[0]._controls
        header = self._header_children(0)

        self.assertEqual(
            [controls["up"], controls["down"], controls["duplicate"],
             controls["delete"]],
            header[2:],
        )

    def test_move_buttons_are_disabled_at_the_ends(self) -> None:
        first, last = self._header_children(0), self._header_children(1)
        self.assertFalse(first[2].get_sensitive())    # first cannot move up
        self.assertTrue(first[3].get_sensitive())
        self.assertTrue(last[2].get_sensitive())
        self.assertFalse(last[3].get_sensitive())     # last cannot move down


@unittest.skipUnless(HAS_DISPLAY, "GTK needs a display to build widgets")
class StepDragTests(unittest.TestCase):
    """Reordering by drag and drop, with the same payload discipline as the grid."""

    def setUp(self) -> None:
        from linuxstreamdeck.ui.steps import StepList

        self.list = StepList(fake_app(FakeObs()))
        self.list.load(
            [
                ActionStep(action="obs.record", label="one"),
                ActionStep(action="sys.wait", label="two"),
                ActionStep(action="obs.stream", label="three"),
            ]
        )

    def _labels(self) -> list[str]:
        return [step.label for step in self.list.get_steps()]

    def _drop(self, source: int, destination: int) -> bool:
        self.list._drag_step = source
        return self.list._on_step_drop(
            None, f"linuxstreamdeck-step:{source}", 0, 0, destination
        )

    def test_dragging_a_step_down_moves_it_to_that_position(self) -> None:
        self.assertTrue(self._drop(0, 2))
        self.assertEqual(self._labels(), ["two", "three", "one"])

    def test_dragging_a_step_up_moves_it_to_that_position(self) -> None:
        self.assertTrue(self._drop(2, 0))
        self.assertEqual(self._labels(), ["three", "one", "two"])

    def test_a_drop_moves_rather_than_swaps(self) -> None:
        self.assertTrue(self._drop(0, 1))
        self.assertEqual(self._labels(), ["two", "one", "three"])

    def test_dropping_a_step_on_itself_changes_nothing(self) -> None:
        self.assertFalse(self._drop(1, 1))
        self.assertEqual(self._labels(), ["one", "two", "three"])

    def test_foreign_and_stale_payloads_are_rejected(self) -> None:
        self.list._drag_step = 0
        self.assertFalse(
            self.list._on_step_drop(None, "somebody-elses-drag", 0, 0, 2)
        )
        # A payload that does not match the drag in progress.
        self.assertFalse(
            self.list._on_step_drop(
                None, "linuxstreamdeck-step:2", 0, 0, 1
            )
        )
        self.assertEqual(self._labels(), ["one", "two", "three"])

    def test_a_drop_outside_the_list_is_rejected(self) -> None:
        self.assertFalse(self._drop(0, 9))
        self.assertEqual(self._labels(), ["one", "two", "three"])

    def test_reordering_keeps_each_row_open_or_closed_as_it_was(self) -> None:
        self.list._editors[2]._expander.set_expanded(True)

        self._drop(2, 0)

        self.assertEqual(
            [e._expander.get_expanded() for e in self.list._editors],
            [True, False, False],
        )

    def test_the_titles_are_renumbered_after_a_drop(self) -> None:
        self._drop(0, 2)
        self.assertEqual(
            [self.list.step_title(i) for i in range(3)],
            ["1. two", "2. three", "3. one"],
        )


class StepNamePersistenceTests(unittest.TestCase):
    """No display needed: the model side of the step name."""

    def test_a_step_name_survives_a_configuration_round_trip(self) -> None:
        import json
        from dataclasses import asdict

        from linuxstreamdeck.core.config import KIND_MULTI, KeyConfig

        key = KeyConfig(
            kind=KIND_MULTI,
            steps=[ActionStep(action="obs.record", label="Roll camera")],
        )
        restored = KeyConfig.from_dict(json.loads(json.dumps(asdict(key))))
        self.assertEqual(restored.steps[0].label, "Roll camera")

    def test_a_configuration_without_step_names_still_loads(self) -> None:
        from linuxstreamdeck.core.config import KIND_MULTI, KeyConfig

        restored = KeyConfig.from_dict(
            {"kind": KIND_MULTI, "steps": [{"action": "obs.record_toggle"}]}
        )
        self.assertEqual(restored.steps[0].label, "")

    def test_the_name_counts_as_an_edit(self) -> None:
        """The editor's unsaved-change check compares whole steps."""
        first = ActionStep(action="obs.record")
        second = ActionStep(action="obs.record", label="Roll camera")
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
