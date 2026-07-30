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

    def get_text_inputs(self) -> list[str]:
        return []

    def get_browser_inputs(self) -> list[str]:
        return []

    def get_transitions(self) -> list[str]:
        return []

    def get_scene_collections(self) -> list[str]:
        return []

    def get_profiles(self) -> list[str]:
        return []

    def get_hotkeys(self) -> list[str]:
        return []


class FakeBus:
    """Records the status messages the step widgets emit."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def emit(self, topic: str, **data) -> None:
        if topic == "status":
            self.messages.append(data.get("text", ""))


def fake_app(obs: FakeObs) -> SimpleNamespace:
    return SimpleNamespace(obs=obs, config=Config(), bus=FakeBus())


# A context menu is a list of (label, enabled, callback), with None for a
# separator. These read one back the way the popover builder does.

def menu_labels(items) -> list:
    return [None if item is None else item[0] for item in items]


def menu_entries(items) -> dict[str, bool]:
    return {label: enabled for label, enabled, _cb in filter(None, items)}


def menu_callback(items, label: str):
    return next(cb for name, _e, cb in filter(None, items) if name == label)


class LabelledChoiceTests(unittest.TestCase):
    """A static dropdown may read differently from what it stores.

    Choices whose values are already words ("toggle", "start") show them as
    they are. Choices whose values are identifiers do not: a list reading
    "stream_time" and "render_lag" tells nobody what those keys would show.
    The stored value stays the identifier either way, so rewording a label
    cannot invalidate a saved key.
    """

    def _param(self, action_id: str, name: str):
        from linuxstreamdeck.core import actions as registry

        action = registry.get(action_id)
        return next(p for p in action.params if p.name == name)

    def test_a_choice_without_labels_shows_what_it_stores(self) -> None:
        from linuxstreamdeck.ui.steps import _labelled_choices

        labels, stored = _labelled_choices(self._param("obs.record", "mode"))

        self.assertEqual(labels, ["toggle", "start", "stop"])
        self.assertEqual(stored, {})

    def test_identifier_choices_are_shown_as_readable_text(self) -> None:
        from linuxstreamdeck.ui.steps import _labelled_choices

        labels, _stored = _labelled_choices(self._param("obs.stats", "metric"))

        self.assertIn("Dropped frames", labels)
        self.assertIn("Skipped render frames", labels)
        self.assertNotIn("render_lag", labels)

    def test_the_stored_value_is_still_the_identifier(self) -> None:
        from linuxstreamdeck.ui.steps import _labelled_choices

        _labels, stored = _labelled_choices(self._param("obs.stats", "metric"))

        self.assertEqual(stored["Dropped frames"], "dropped")
        self.assertEqual(stored["Skipped render frames"], "render_lag")

    def test_every_choice_survives_the_round_trip(self) -> None:
        """Label lookup and value lookup must be exact inverses."""
        from linuxstreamdeck.ui.steps import _label_for, _labelled_choices

        for action_id, name in (
            ("obs.stats", "metric"),
            ("obs.stats", "colored"),
            ("obs.transform", "property"),
            ("obs.transform", "mode"),
            ("obs.record", "mode"),
        ):
            param = self._param(action_id, name)
            _labels, stored = _labelled_choices(param)
            for value in param.choices:
                with self.subTest(action=action_id, value=value):
                    label = _label_for(stored, value)
                    self.assertEqual(stored.get(label, label), value)

    def test_a_label_is_offered_for_every_choice(self) -> None:
        from linuxstreamdeck.ui.steps import _labelled_choices

        param = self._param("obs.stats", "metric")

        labels, _stored = _labelled_choices(param)

        self.assertEqual(len(labels), len(param.choices))

    def test_two_choices_reading_alike_stay_distinguishable(self) -> None:
        """A duplicate label would make one of them unreachable."""
        from linuxstreamdeck.core.actions import Param
        from linuxstreamdeck.ui.steps import _labelled_choices

        param = Param(
            "x", "X", kind="choice", choices=["a", "b"],
            choice_labels={"a": "Same", "b": "Same"},
        )

        labels, stored = _labelled_choices(param)

        self.assertEqual(len(set(labels)), 2)
        self.assertEqual(set(stored.values()), {"a", "b"})

    def test_the_ai_catalogue_still_offers_the_stored_values(self) -> None:
        """Proposals are validated against the identifiers, not the labels."""
        param = self._param("obs.stats", "metric")

        self.assertIn("render_lag", param.choices)
        self.assertNotIn("Skipped render frames", param.choices)


@unittest.skipUnless(HAS_DISPLAY, "GTK needs a display to build widgets")
class LabelledChoiceWidgetTests(unittest.TestCase):
    def setUp(self) -> None:
        from linuxstreamdeck.ui.steps import StepEditor

        self.editor = StepEditor(fake_app(FakeObs()))

    def test_the_dropdown_shows_labels(self) -> None:
        self.editor.load(ActionStep(action="obs.stats"))

        widget = self.editor._param_widgets["metric"][1]
        model = widget.get_model()
        shown = [model.get_string(i) for i in range(model.get_n_items())]

        self.assertIn("Free disk space", shown)
        self.assertNotIn("disk", shown)

    def test_choosing_a_label_stores_its_identifier(self) -> None:
        self.editor.load(ActionStep(action="obs.stats"))
        widget = self.editor._param_widgets["metric"][1]
        model = widget.get_model()
        shown = [model.get_string(i) for i in range(model.get_n_items())]

        widget.set_selected(shown.index("Free disk space"))

        self.assertEqual(self.editor.get_step().params["metric"], "disk")

    def test_a_saved_key_reopens_on_its_own_label(self) -> None:
        """A key stored before the labels existed must still select correctly."""
        self.editor.load(
            ActionStep(action="obs.stats", params={"metric": "render_lag"})
        )

        widget = self.editor._param_widgets["metric"][1]
        item = widget.get_selected_item()

        self.assertEqual(item.get_string(), "Skipped render frames")
        self.assertEqual(self.editor.get_step().params["metric"], "render_lag")


@unittest.skipUnless(HAS_DISPLAY, "GTK needs a display to build widgets")
class FolderParameterTests(unittest.TestCase):
    """A `file` parameter that picks a folder rather than a file.

    `obs.stats` needs one for the drive whose free space it reports, and a file
    chooser cannot select a drive.
    """

    def setUp(self) -> None:
        from linuxstreamdeck.ui.steps import StepEditor

        self.editor = StepEditor(fake_app(FakeObs()))

    def _widget(self):
        return self.editor._param_widgets["disk_folder"][1]

    def test_it_offers_a_folder_chooser(self) -> None:
        self.editor.load(ActionStep(action="obs.stats"))

        self.assertTrue(self.editor._param_widgets["disk_folder"][0].directory)

    def test_a_chosen_folder_round_trips(self) -> None:
        self.editor.load(
            ActionStep(
                action="obs.stats",
                params={"metric": "disk", "disk_folder": "/srv/video"},
            )
        )

        self.assertEqual(self._widget().get_text(), "/srv/video")
        self.assertEqual(
            self.editor.get_step().params["disk_folder"], "/srv/video"
        )

    def test_leaving_it_blank_stores_nothing(self) -> None:
        """Blank means the home folder; it must not become a literal path."""
        self.editor.load(ActionStep(action="obs.stats", params={"metric": "disk"}))

        self.assertEqual(self.editor.get_step().params["disk_folder"], "")

    def test_the_empty_field_says_what_blank_means(self) -> None:
        self.editor.load(ActionStep(action="obs.stats"))

        self.assertIn("Home folder", self._widget().entry.get_placeholder_text())


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
class StepCopyPasteTests(unittest.TestCase):
    """Copying puts a step aside; pasting decides where it lands."""

    def setUp(self) -> None:
        from linuxstreamdeck.ui.steps import STEP_CLIPBOARD, StepList

        STEP_CLIPBOARD.clear()
        self.addCleanup(STEP_CLIPBOARD.clear)
        self.clipboard = STEP_CLIPBOARD
        self.app = fake_app(FakeObs())
        self.list = StepList(self.app)

    def _titles(self) -> list[str]:
        return [
            self.list.step_title(i) for i in range(len(self.list._editors))
        ]

    def _load_two(self) -> None:
        self.list.load(
            [
                ActionStep(action="obs.record", label="Roll camera"),
                ActionStep(action="sys.wait", params={"duration": "00:05"}),
            ]
        )

    def test_copying_inserts_nothing_by_itself(self) -> None:
        self._load_two()

        self.list._copy(0)

        self.assertEqual(len(self.list.get_steps()), 2)
        self.assertTrue(self.clipboard.has_step())

    def test_pasting_on_a_row_pushes_that_row_down(self) -> None:
        self._load_two()

        self.list._copy(0)
        self.list._paste(1)

        self.assertEqual(
            [(s.action, s.label) for s in self.list.get_steps()],
            [
                ("obs.record", "Roll camera"),
                ("obs.record", "Roll camera"),
                ("sys.wait", ""),
            ],
        )

    def test_pasting_with_no_position_appends(self) -> None:
        self._load_two()

        self.list._copy(0)
        self.list._paste(None)

        self.assertEqual(
            [s.action for s in self.list.get_steps()],
            ["obs.record", "sys.wait", "obs.record"],
        )

    def test_a_paste_carries_the_parameters(self) -> None:
        self.list.load(
            [ActionStep(action="sys.wait", params={"duration": "00:07"})]
        )

        self.list._copy(0)
        self.list._paste(0)

        self.assertEqual(self.list.get_steps()[0].params["duration"], "00:07")

    def test_editing_a_pasted_step_leaves_the_original_alone(self) -> None:
        self.list.load([ActionStep(action="obs.record", label="Roll camera")])

        self.list._copy(0)
        self.list._paste(None)
        self.list._editors[1].label_entry.set_text("Stop camera")

        self.assertEqual(
            [s.label for s in self.list.get_steps()],
            ["Roll camera", "Stop camera"],
        )

    def test_the_copy_outlives_the_list_it_came_from(self) -> None:
        """The point of the change: paste into another key's list."""
        from linuxstreamdeck.ui.steps import StepList

        self._load_two()
        self.list._copy(0)

        other = StepList(self.app)
        other.load([ActionStep(action="obs.stream")])
        other._paste(0)

        self.assertEqual(
            [(s.action, s.label) for s in other.get_steps()],
            [("obs.record", "Roll camera"), ("obs.stream", "")],
        )

    def test_pasting_twice_gives_two_independent_steps(self) -> None:
        self.list.load(
            [ActionStep(action="sys.wait", params={"duration": "00:07"})]
        )

        self.list._copy(0)
        self.list._paste(None)
        self.list._paste(None)
        self.list._editors[1].label_entry.set_text("Second")

        self.assertEqual(
            [s.label for s in self.list.get_steps()], ["", "Second", ""]
        )

    def test_pasting_nothing_does_nothing(self) -> None:
        self._load_two()

        self.list._paste(0)

        self.assertEqual(len(self.list.get_steps()), 2)

    def test_an_empty_step_is_never_copied(self) -> None:
        """It would only offer a paste that adds nothing."""
        self.clipboard.set(ActionStep())

        self.assertFalse(self.clipboard.has_step())
        self.assertIsNone(self.clipboard.get())

    def test_copying_an_index_that_is_gone_does_nothing(self) -> None:
        self.list.load([ActionStep(action="obs.record")])

        self.list._copy(5)

        self.assertFalse(self.clipboard.has_step())

    def test_a_pasted_step_stays_collapsed(self) -> None:
        """The copy is already configured; opening it would push the list down."""
        self.list.load(
            [
                ActionStep(action="obs.record"),
                ActionStep(action="obs.stream"),
            ]
        )

        self.list._copy(0)
        self.list._paste(1)

        self.assertEqual(
            [editor._expander.get_expanded() for editor in self.list._editors],
            [False, False, False],
        )
        self.assertEqual(self._titles(), [
            "1. Record on/off", "2. Record on/off", "3. Stream on/off",
        ])

    def test_a_paste_leaves_the_rows_the_user_opened_open(self) -> None:
        self.list.load(
            [
                ActionStep(action="obs.record"),
                ActionStep(action="obs.stream"),
            ]
        )
        self.list._editors[1]._expander.set_expanded(True)

        self.list._copy(0)
        self.list._paste(0)

        self.assertEqual(
            [editor._expander.get_expanded() for editor in self.list._editors],
            [False, False, True],
        )

    def test_paste_is_offered_only_once_something_is_copied(self) -> None:
        self._load_two()

        self.assertIs(menu_entries(self.list.row_menu_items(0))["Paste action"], False)
        self.assertIs(menu_entries(self.list.list_menu_items())["Paste action"], False)

        self.list._copy(0)

        self.assertIs(menu_entries(self.list.row_menu_items(0))["Paste action"], True)
        self.assertIs(menu_entries(self.list.list_menu_items())["Paste action"], True)

    def test_the_row_menu_carries_copy_move_and_remove(self) -> None:
        self.list.load(
            [
                ActionStep(action="obs.record"),
                ActionStep(action="obs.stream"),
                ActionStep(action="sys.wait"),
            ]
        )

        self.assertEqual(
            menu_labels(self.list.row_menu_items(1)),
            [
                "Copy action", "Paste action", None,
                "Move up", "Move down", "Move to top", "Move to bottom", None,
                "Remove action",
            ],
        )

    def test_the_move_entries_are_disabled_at_the_ends(self) -> None:
        self.list.load(
            [
                ActionStep(action="obs.record"),
                ActionStep(action="obs.stream"),
                ActionStep(action="sys.wait"),
            ]
        )

        first = menu_entries(self.list.row_menu_items(0))
        middle = menu_entries(self.list.row_menu_items(1))
        last = menu_entries(self.list.row_menu_items(2))

        self.assertEqual(
            [first["Move up"], first["Move to top"]], [False, False]
        )
        self.assertEqual(
            [first["Move down"], first["Move to bottom"]], [True, True]
        )
        self.assertTrue(all(middle[name] for name in (
            "Move up", "Move down", "Move to top", "Move to bottom"
        )))
        self.assertEqual(
            [last["Move down"], last["Move to bottom"]], [False, False]
        )
        self.assertEqual(
            [last["Move up"], last["Move to top"]], [True, True]
        )

    def test_the_only_row_of_a_list_cannot_be_moved_anywhere(self) -> None:
        self.list.load([ActionStep(action="obs.record")])

        only = menu_entries(self.list.row_menu_items(0))

        self.assertFalse(any(only[name] for name in (
            "Move up", "Move down", "Move to top", "Move to bottom"
        )))
        self.assertTrue(only["Remove action"])

    def test_the_move_entries_reorder_the_list(self) -> None:
        self.list.load(
            [
                ActionStep(action="obs.record", label="one"),
                ActionStep(action="obs.stream", label="two"),
                ActionStep(action="sys.wait", label="three"),
            ]
        )

        menu_callback(self.list.row_menu_items(2), "Move to top")()
        self.assertEqual(
            [s.label for s in self.list.get_steps()], ["three", "one", "two"]
        )

        menu_callback(self.list.row_menu_items(0), "Move to bottom")()
        self.assertEqual(
            [s.label for s in self.list.get_steps()], ["one", "two", "three"]
        )

        menu_callback(self.list.row_menu_items(0), "Move down")()
        self.assertEqual(
            [s.label for s in self.list.get_steps()], ["two", "one", "three"]
        )

        menu_callback(self.list.row_menu_items(2), "Move up")()
        self.assertEqual(
            [s.label for s in self.list.get_steps()], ["two", "three", "one"]
        )

    def test_the_remove_entry_deletes_that_row(self) -> None:
        self.list.load(
            [
                ActionStep(action="obs.record", label="one"),
                ActionStep(action="obs.stream", label="two"),
            ]
        )

        menu_callback(self.list.row_menu_items(0), "Remove action")()

        self.assertEqual([s.label for s in self.list.get_steps()], ["two"])

    def test_both_the_rows_and_the_list_answer_a_right_click(self) -> None:
        self._load_two()

        def secondary(widget) -> list:
            return [
                c for c in widget.observe_controllers()
                if isinstance(c, Gtk.GestureClick) and c.get_button() == 3
            ]

        self.assertEqual(len(secondary(self.list)), 1)
        for editor in self.list._editors:
            self.assertEqual(len(secondary(editor._expander)), 1)

    def test_a_row_menu_is_not_replaced_by_the_list_menu(self) -> None:
        """Both gestures see one click; the row's answer has to win.

        Claiming should already stop the list gesture, but the copy entry is
        too important to rest on GTK's claiming order alone.
        """
        self._load_two()
        shown = []
        self.list._menu = SimpleNamespace(
            show=lambda _w, _x, _y, items, on_close=None: shown.append(
                menu_labels(items)
            ),
            close=lambda: None,
        )
        gesture = SimpleNamespace(
            set_state=lambda _state: None, get_widget=lambda: self.list
        )

        self.list._on_row_menu(gesture, 1, 0, 0, 0)
        self.list._on_list_menu(gesture, 1, 0, 0)

        self.assertEqual(len(shown), 1)
        self.assertIn("Copy action", shown[0])

    def test_the_list_menu_works_again_on_the_next_click(self) -> None:
        self._load_two()
        shown = []
        self.list._menu = SimpleNamespace(
            show=lambda _w, _x, _y, items, on_close=None: shown.append(
                menu_labels(items)
            ),
            close=lambda: None,
        )
        gesture = SimpleNamespace(
            set_state=lambda _state: None, get_widget=lambda: self.list
        )

        self.list._on_row_menu(gesture, 1, 0, 0, 0)
        # The idle that runs between two clicks releases the guard.
        self.list._clear_row_menu_click()
        self.list._on_list_menu(gesture, 1, 0, 0)

        self.assertEqual(shown[-1], ["Paste action"])

    def test_the_right_clicked_row_is_marked_until_the_menu_closes(self) -> None:
        """The menu covers part of the list, so which row it acts on must show."""
        self._load_two()
        closers = []
        self.list._menu = SimpleNamespace(
            show=lambda _w, _x, _y, _items, on_close=None: closers.append(on_close),
            close=lambda: None,
        )
        row = self.list._editors[1]._expander
        gesture = SimpleNamespace(
            set_state=lambda _state: None, get_widget=lambda: row
        )

        self.list._on_row_menu(gesture, 1, 0, 0, 1)
        self.assertTrue(row.has_css_class("menu-target"))

        closers[0]()
        self.assertFalse(row.has_css_class("menu-target"))

    def test_only_one_row_is_marked_at_a_time(self) -> None:
        self._load_two()
        first = self.list._editors[0]._expander
        second = self.list._editors[1]._expander

        self.list._set_menu_row(first)
        self.list._set_menu_row(second)

        self.assertFalse(first.has_css_class("menu-target"))
        self.assertTrue(second.has_css_class("menu-target"))

    def test_copying_says_where_the_copy_can_go(self) -> None:
        self._load_two()

        self.list._copy(0)

        self.assertIn("Roll camera", self.app.bus.messages[-1])
        self.assertIn("right-click", self.app.bus.messages[-1])

    def test_a_single_action_editor_copies_and_pastes_too(self) -> None:
        """A key with no list is still a paste destination."""
        from linuxstreamdeck.ui.steps import StepEditor

        source = StepEditor(self.app)
        source.load(ActionStep(action="sys.wait", params={"duration": "00:09"}))
        source.copy_step()

        target = StepEditor(self.app)
        target.load(ActionStep(action="obs.record"))
        target.paste_step()

        step = target.get_step()
        self.assertEqual(step.action, "sys.wait")
        self.assertEqual(step.params["duration"], "00:09")

    def test_a_step_name_is_dropped_when_pasted_on_a_single_action_key(
        self,
    ) -> None:
        """It has no list row to name, and its own label lives in Appearance."""
        from linuxstreamdeck.ui.steps import StepEditor

        self.list.load([ActionStep(action="obs.record", label="Roll camera")])
        self.list._copy(0)

        target = StepEditor(self.app)
        target.paste_step()

        self.assertEqual(target.get_step().label, "")
        # The clipboard still carries it, for a list that can show it.
        self.assertEqual(self.clipboard.get().label, "Roll camera")


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
                "Copy this action; right-click any action list to paste it",
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
            [controls["up"], controls["down"], controls["copy"],
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
