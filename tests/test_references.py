"""Finding keys that point at OBS objects which no longer exist.

This is the one failure the application never used to report: rename a scene in
OBS and the key stays where it is, with its icon, and silently does nothing.

Two constraints shape it. obs-websocket can only list what the **loaded** scene
collection holds, so every answer is relative to what OBS has open; and a
collection switch renames everything at once, so the check never runs by itself
— the user starts it, standing where they are, and reads the result knowing the
context. What is tested here is that it reports only what is genuinely broken.
"""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from linuxstreamdeck import basic_actions as _basic_actions  # noqa: F401
from linuxstreamdeck.core import references
from linuxstreamdeck.core.config import (
    KIND_DIAL,
    KIND_FOLDER,
    KIND_MULTI,
    KIND_SINGLE,
    ActionStep,
    Config,
    Folder,
    KeyConfig,
)
from linuxstreamdeck.core.controller import DeckController
from linuxstreamdeck.core.events import EventBus
from linuxstreamdeck.obs import actions as _obs_actions  # noqa: F401


class FakeDeck:
    key_count = 15
    image_size = (72, 72)
    columns = 5
    dial_count = 4

    def __init__(self) -> None:
        self.screensaver_active = False

    def set_key_image(self, *_args) -> None:
        pass

    def set_touchscreen_image(self, *_args) -> None:
        pass

    def record_activity(self) -> bool:
        return False

    def set_brightness(self, _value) -> None:
        pass

    def configure_screensaver(self, *_args) -> None:
        pass


class FakeObs:
    """An OBS holding one collection, whose scene was renamed to "Cam"."""

    connected = True

    def __init__(self, scenes=("Cam", "BRB"), collection="Gaming") -> None:
        self._scenes = list(scenes)
        self.collection = collection

    def try_request(self, name, _data=None):
        if name == "GetSceneCollectionList":
            return {"currentSceneCollectionName": self.collection}
        return {}

    def get_scenes(self):
        return list(self._scenes)

    def get_inputs(self):
        return ["Mic", "Desktop Audio"]

    def get_media_inputs(self):
        return ["Sting"]

    def get_text_inputs(self):
        return ["Title"]

    def get_browser_inputs(self):
        return ["Alerts"]

    def get_transitions(self):
        return ["Fade"]

    def get_scene_collections(self):
        return ["Gaming", "Podcast"]

    def get_profiles(self):
        return ["Default"]

    def get_sources_in_scene(self, _scene):
        return ["Webcam", "Chat"]

    def get_audio_sources_in_scene(self, _scene):
        return ["Mic"]

    def get_filters_of_source(self, _source):
        return ["Color"]


def scene_key(scene: str) -> KeyConfig:
    return KeyConfig(kind=KIND_SINGLE, action="obs.scene_switch",
                     params={"scene": scene})


class CollectionTests(unittest.TestCase):
    """What counts as a reference, and what deliberately does not."""

    def setUp(self) -> None:
        self.config = Config()
        self.page = self.config.pages[0]

    def _values(self, kind: str) -> list[str]:
        found = references.collect(self.page, self.page.dials)
        return [r.value for r in found if r.kind == kind]

    def test_a_scene_reference_is_found(self) -> None:
        self.page.set_key(0, scene_key("Camara"))

        self.assertEqual(self._values("scenes"), ["Camara"])

    def test_a_blank_value_is_not_a_reference(self) -> None:
        self.page.set_key(0, scene_key(""))

        self.assertEqual(references.collect(self.page), [])

    def test_steps_inside_a_list_are_found(self) -> None:
        self.page.set_key(
            0,
            KeyConfig(
                kind=KIND_MULTI,
                steps=[
                    ActionStep(action="obs.scene_switch", params={"scene": "A"}),
                    ActionStep(action="obs.scene_switch", params={"scene": "B"}),
                ],
            ),
        )

        self.assertEqual(self._values("scenes"), ["A", "B"])

    def test_keys_inside_folders_are_found(self) -> None:
        """A folder is part of where you are, so it is part of the check."""
        folder = KeyConfig(kind=KIND_FOLDER, label="Scenes", folder=Folder())
        folder.folder.set_key(1, scene_key("Camara"))
        self.page.set_key(5, folder)

        self.assertEqual(self._values("scenes"), ["Camara"])

    def test_a_folder_reference_says_which_folder(self) -> None:
        folder = KeyConfig(kind=KIND_FOLDER, label="Scenes", folder=Folder())
        folder.folder.set_key(1, scene_key("Camara"))
        self.page.set_key(5, folder)

        found = references.collect(self.page)

        self.assertIn("Scenes", found[0].where)

    def test_dials_are_checked_too(self) -> None:
        dial = KeyConfig(kind=KIND_DIAL)
        dial.steps_press = [
            ActionStep(action="obs.scene_switch", params={"scene": "Camara"})
        ]
        self.page.set_dial(0, dial)

        found = references.collect(self.page, self.page.dials)

        self.assertEqual([r.value for r in found], ["Camara"])
        self.assertIn("Dial 1", found[0].where)

    def test_an_editor_only_filter_is_not_a_reference(self) -> None:
        """The audio actions' scene narrows a list; it is never sent to OBS."""
        self.page.set_key(
            0,
            KeyConfig(
                kind=KIND_SINGLE, action="obs.mute",
                params={"scene": "Camara", "input": "Mic"},
            ),
        )

        self.assertEqual(self._values("scenes"), [])

    def test_a_dependent_value_records_its_parent(self) -> None:
        self.page.set_key(
            0,
            KeyConfig(
                kind=KIND_SINGLE, action="obs.source_visibility",
                params={"scene": "Cam", "source": "Webcam"},
            ),
        )

        found = [r for r in references.collect(self.page) if r.param == "source"]

        self.assertEqual(found[0].parent, "Cam")

    def test_local_references_are_left_alone(self) -> None:
        """A renamed page is already rewritten everywhere when it happens."""
        self.page.set_key(
            0,
            KeyConfig(kind=KIND_SINGLE, action="nav.page.go",
                      params={"page": "Somewhere"}),
        )

        self.assertEqual(references.collect(self.page), [])

    def test_an_unknown_action_is_skipped(self) -> None:
        self.page.set_key(
            0, KeyConfig(kind=KIND_SINGLE, action="obs.from_the_future",
                         params={"scene": "Camara"})
        )

        self.assertEqual(references.collect(self.page), [])


class SuggestionTests(unittest.TestCase):
    def test_a_small_rename_is_suggested(self) -> None:
        self.assertEqual(references.suggest("Camara", ["Cam", "BRB"]), "Cam")

    def test_a_typo_is_suggested(self) -> None:
        self.assertEqual(
            references.suggest("Gameplya", ["Gameplay", "BRB"]), "Gameplay"
        )

    def test_a_dropped_word_is_suggested(self) -> None:
        """Containment catches what similarity scoring rates too low."""
        self.assertEqual(
            references.suggest("Camera", ["Camera main", "BRB"]), "Camera main"
        )

    def test_nothing_alike_suggests_nothing(self) -> None:
        self.assertEqual(references.suggest("Chat", ["Gameplay", "BRB"]), "")

    def test_no_candidates_suggests_nothing(self) -> None:
        self.assertEqual(references.suggest("Chat", []), "")


class CheckTests(unittest.TestCase):
    def _available(self, scenes=("Cam", "BRB"), sources=None):
        def available(kind, parent):
            if kind == "scenes":
                return list(scenes)
            if kind == "transitions":
                return ["Fade"]
            if kind in ("sources_in_scene", "audio_sources_in_scene"):
                return None if parent not in scenes else list(sources or ["Webcam"])
            return None

        return available

    def _refs(self, page):
        return references.collect(page, page.dials)

    def test_a_surviving_name_is_not_reported(self) -> None:
        config = Config()
        config.pages[0].set_key(0, scene_key("Cam"))

        findings = references.check(self._refs(config.pages[0]), self._available())

        self.assertEqual(findings, ())

    def test_a_missing_name_is_reported_once_for_all_its_keys(self) -> None:
        config = Config()
        for index in range(3):
            config.pages[0].set_key(index, scene_key("Camara"))

        findings = references.check(self._refs(config.pages[0]), self._available())

        self.assertEqual(len(findings), 1)
        self.assertEqual(len(findings[0].references), 3)
        self.assertIn("3 keys", findings[0].summary())

    def test_the_widest_problem_is_reported_first(self) -> None:
        config = Config()
        config.pages[0].set_key(0, scene_key("Camara"))
        config.pages[0].set_key(1, scene_key("Camara"))
        config.pages[0].set_key(
            2,
            KeyConfig(kind=KIND_SINGLE, action="obs.transition_set",
                      params={"transition": "Cut"}),
        )

        findings = references.check(self._refs(config.pages[0]), self._available())

        self.assertEqual(findings[0].value, "Camara")

    def test_a_missing_name_carries_its_suggestion(self) -> None:
        config = Config()
        config.pages[0].set_key(0, scene_key("Camara"))

        findings = references.check(self._refs(config.pages[0]), self._available())

        self.assertEqual(findings[0].suggestion, "Cam")

    def test_a_source_inside_a_missing_scene_is_not_reported_twice(self) -> None:
        """One rename must not be counted as two problems."""
        config = Config()
        config.pages[0].set_key(
            0,
            KeyConfig(
                kind=KIND_SINGLE, action="obs.source_visibility",
                params={"scene": "Camara", "source": "Webcam"},
            ),
        )

        findings = references.check(self._refs(config.pages[0]), self._available())

        self.assertEqual([f.value for f in findings], ["Camara"])

    def test_an_unknown_answer_is_never_reported_as_missing(self) -> None:
        config = Config()
        config.pages[0].set_key(
            0,
            KeyConfig(kind=KIND_SINGLE, action="obs.media",
                      params={"input": "Sting", "mode": "play"}),
        )

        findings = references.check(self._refs(config.pages[0]), lambda k, p: None)

        self.assertEqual(findings, ())


class ApplyFixTests(unittest.TestCase):
    def test_every_reference_is_repointed(self) -> None:
        config = Config()
        page = config.pages[0]
        for index in range(3):
            page.set_key(index, scene_key("Camara"))
        findings = references.check(
            references.collect(page), lambda k, p: ["Cam"] if k == "scenes" else None
        )

        fixed = references.apply_fix(findings[0], "Cam")

        self.assertEqual(fixed, 3)
        self.assertEqual(
            [page.key(i).params["scene"] for i in range(3)], ["Cam"] * 3
        )

    def test_a_blank_replacement_changes_nothing(self) -> None:
        config = Config()
        page = config.pages[0]
        page.set_key(0, scene_key("Camara"))
        findings = references.check(
            references.collect(page), lambda k, p: ["Cam"] if k == "scenes" else None
        )

        self.assertEqual(references.apply_fix(findings[0], "  "), 0)
        self.assertEqual(page.key(0).params["scene"], "Camara")


class ControllerCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        for path in Config.backup_history():
            path.unlink(missing_ok=True)
        self.config = Config()
        self.obs = FakeObs()
        self.controller = DeckController(
            self.config, EventBus(), self.obs, FakeDeck()
        )
        self.addCleanup(self.controller.shutdown)
        self.page = self.config.pages[0]

    def test_the_report_names_the_collection_it_checked_against(self) -> None:
        """The result is meaningless without knowing what it was compared to."""
        report = self.controller.check_references()

        self.assertEqual(report.collection, "Gaming")

    def test_a_clean_page_reports_clean(self) -> None:
        self.page.set_key(0, scene_key("Cam"))

        report = self.controller.check_references()

        self.assertTrue(report.is_clean())
        self.assertEqual(report.checked, 1)

    def test_a_broken_key_is_found(self) -> None:
        self.page.set_key(0, scene_key("Camara"))

        report = self.controller.check_references()

        self.assertEqual(len(report.findings), 1)
        self.assertEqual(report.findings[0].suggestion, "Cam")

    def test_the_report_says_how_much_it_looked_at(self) -> None:
        """A checker that only speaks on failure never says it looked."""
        self.page.set_key(0, scene_key("Cam"))
        self.page.set_key(1, scene_key("Camara"))

        report = self.controller.check_references()

        self.assertEqual(report.checked, 2)
        self.assertEqual(report.keys, 2)
        self.assertEqual(report.broken_keys(), 1)

    def test_only_the_grid_you_are_in_is_checked(self) -> None:
        """Standing inside a folder checks that folder, not the page."""
        folder = KeyConfig(kind=KIND_FOLDER, label="Scenes", folder=Folder())
        folder.folder.set_key(1, scene_key("Inside"))
        self.page.set_key(5, folder)
        self.page.set_key(0, scene_key("Outside"))

        self.controller.open_folder(5)
        report = self.controller.check_references()

        self.assertEqual([f.value for f in report.findings], ["Inside"])

    def test_dials_are_out_of_scope_inside_a_folder(self) -> None:
        """They belong to the page, so they are only in scope at its root."""
        dial = KeyConfig(kind=KIND_DIAL)
        dial.steps_press = [
            ActionStep(action="obs.scene_switch", params={"scene": "Camara"})
        ]
        self.page.set_dial(0, dial)
        folder = KeyConfig(kind=KIND_FOLDER, label="Scenes", folder=Folder())
        self.page.set_key(5, folder)

        self.controller.open_folder(5)

        self.assertTrue(self.controller.check_references().is_clean())

    def test_it_refuses_to_guess_without_obs(self) -> None:
        self.controller.obs = SimpleNamespace(connected=False)

        with self.assertRaises(ConnectionError):
            self.controller.check_references()

    def test_each_list_is_asked_for_once(self) -> None:
        """A page can hold a dozen references to one scene."""
        asked: list[str] = []
        original = self.obs.get_scenes
        self.obs.get_scenes = lambda: (asked.append("scenes"), original())[1]
        for index in range(6):
            self.page.set_key(index, scene_key("Camara"))

        self.controller.check_references()

        self.assertEqual(len(asked), 1)


class ControllerFixTests(unittest.TestCase):
    def setUp(self) -> None:
        for path in Config.backup_history():
            path.unlink(missing_ok=True)
        self.config = Config()
        self.controller = DeckController(
            self.config, EventBus(), FakeObs(), FakeDeck()
        )
        self.addCleanup(self.controller.shutdown)
        self.page = self.config.pages[0]
        self.page.set_key(0, scene_key("Camara"))
        self.page.set_key(1, scene_key("Camara"))
        # The snapshot copies the file on disk, which in the running
        # application always holds the live configuration. The suite shares one
        # configuration directory, so that has to be made true here as well.
        self.config.save()
        for path in Config.backup_history():
            path.unlink(missing_ok=True)

    def test_the_fix_is_applied_and_saved(self) -> None:
        report = self.controller.check_references()

        fixed = self.controller.apply_reference_fix(report.findings[0], "Cam")

        self.assertEqual(fixed, 2)
        self.assertEqual(self.page.key(0).params["scene"], "Cam")

    def test_a_backup_is_taken_before_the_change(self) -> None:
        """A bulk rewrite outgrows the undo history, so this is the safety net."""
        report = self.controller.check_references()

        self.controller.apply_reference_fix(report.findings[0], "Cam")

        history = Config.backup_history()
        self.assertTrue(history)
        saved = json.loads(history[0].read_text(encoding="utf-8"))
        stored = saved["profiles"][0]["pages"][0]["keys"]["0"]["params"]["scene"]
        self.assertEqual(stored, "Camara")

    def test_the_page_is_clean_afterwards(self) -> None:
        report = self.controller.check_references()
        self.controller.apply_reference_fix(report.findings[0], "Cam")

        self.assertTrue(self.controller.check_references().is_clean())


if __name__ == "__main__":
    unittest.main()
