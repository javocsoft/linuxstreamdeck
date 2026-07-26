"""Finding a key by what it does, and finding an action by what it is called."""

from __future__ import annotations

import unittest

from linuxstreamdeck import basic_actions as _basic_actions  # noqa: F401
from linuxstreamdeck.core import search
from linuxstreamdeck.core.config import (
    KIND_FOLDER,
    KIND_MULTI,
    KIND_SINGLE,
    KIND_TOGGLE,
    ActionStep,
    Config,
    Folder,
    KeyConfig,
    Page,
)
from linuxstreamdeck.obs import actions as _obs_actions  # noqa: F401


class KeySearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = Config()
        page = self.config.pages[0]
        page.set_key(1, KeyConfig(
            kind=KIND_SINGLE, action="obs.mute",
            params={"input": "Mic/Aux"}, label="Mic",
        ))
        page.set_key(2, KeyConfig(kind=KIND_SINGLE, action="obs.record"))
        folder = KeyConfig(kind=KIND_FOLDER, folder=Folder(), label="Scenes")
        folder.folder.set_key(4, KeyConfig(
            kind=KIND_SINGLE, action="obs.scene_switch", params={"scene": "Live"},
        ))
        page.set_key(5, folder)
        self.config.pages.append(Page(name="Second"))
        self.config.pages[1].set_key(0, KeyConfig(
            kind=KIND_SINGLE, action="sys.timer",
            params={"duration": "05:00"}, label="Break",
        ))

    def _found(self, query: str) -> list[str]:
        return [f"{f.page_name}:{f.index}" for f in search.search(self.config, query)]

    def test_a_key_is_found_by_its_label(self) -> None:
        self.assertEqual(self._found("mic"), ["Page 1:1"])

    def test_a_key_is_found_by_its_action_name(self) -> None:
        """Nobody remembers the action id, they remember what it is called."""
        self.assertEqual(self._found("record on/off"), ["Page 1:2"])

    def test_a_key_is_found_by_its_action_id(self) -> None:
        self.assertEqual(self._found("obs.mute"), ["Page 1:1"])

    def test_a_key_is_found_by_a_parameter_value(self) -> None:
        self.assertEqual(self._found("Mic/Aux"), ["Page 1:1"])
        self.assertEqual(self._found("05:00"), ["Second:0"])

    def test_terms_are_combined(self) -> None:
        self.assertEqual(self._found("scene live"), ["Page 1:4"])
        self.assertEqual(self._found("scene nonsense"), [])

    def test_the_order_of_terms_does_not_matter(self) -> None:
        self.assertEqual(self._found("live scene"), self._found("scene live"))

    def test_keys_inside_folders_are_searched(self) -> None:
        found = search.search(self.config, "Live")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].path, (5,))
        self.assertEqual(found[0].trail, ("Scenes",))

    def test_a_folder_is_found_by_its_own_name(self) -> None:
        found = search.search(self.config, "Scenes")
        self.assertIn("Page 1:5", [f"{f.page_name}:{f.index}" for f in found])

    def test_a_folder_does_not_answer_for_what_is_inside_it(self) -> None:
        """Otherwise every folder matches every search made under it."""
        found = {f.index for f in search.search(self.config, "Mic/Aux")}
        self.assertNotIn(5, found)

    def test_every_step_of_a_multi_key_is_searched(self) -> None:
        self.config.pages[0].set_key(6, KeyConfig(kind=KIND_MULTI, steps=[
            ActionStep(action="sys.wait", params={"duration": "00:03"}),
            ActionStep(action="obs.stream"),
        ]))
        # Matched on its second step, not just the first.
        self.assertIn("Page 1:6", self._found("stream on/off"))
        self.assertIn("Page 1:6", self._found("00:03"))

    def test_both_lists_of_a_toggle_key_are_searched(self) -> None:
        self.config.pages[0].set_key(7, KeyConfig(
            kind=KIND_TOGGLE,
            steps_on=[ActionStep(action="obs.virtualcam")],
            steps_off=[ActionStep(action="obs.replay")],
        ))
        self.assertEqual(self._found("replay"), ["Page 1:7"])

    def test_a_step_name_is_searched(self) -> None:
        self.config.pages[0].set_key(8, KeyConfig(kind=KIND_MULTI, steps=[
            ActionStep(action="obs.record", label="Roll camera"),
        ]))
        self.assertEqual(self._found("roll camera"), ["Page 1:8"])

    def test_an_empty_query_finds_nothing(self) -> None:
        self.assertEqual(self._found(""), [])
        self.assertEqual(self._found("   "), [])

    def test_a_result_says_where_it_is(self) -> None:
        found = search.search(self.config, "Live")[0]

        self.assertIn("Page 1", found.where())
        self.assertIn("Scenes", found.where())
        self.assertIn("Key 5", found.where())

    def test_a_result_describes_a_key_with_no_label(self) -> None:
        found = search.search(self.config, "record")[0]
        self.assertEqual(found.what(), "Record on/off")

    def test_a_labelled_key_is_described_by_its_label(self) -> None:
        found = search.search(self.config, "Mic/Aux")[0]
        self.assertEqual(found.what(), "Mic")

    def test_a_multi_key_summarises_how_many_actions_it_runs(self) -> None:
        self.config.pages[0].set_key(9, KeyConfig(kind=KIND_MULTI, steps=[
            ActionStep(action="obs.record"),
            ActionStep(action="obs.stream"),
        ]))
        found = [f for f in search.search(self.config, "stream") if f.index == 9]
        self.assertEqual(found[0].what(), "Record on/off +1")


class ActionSearchTests(unittest.TestCase):
    """The action picker: 42 actions across 8 categories, found by any word."""

    def _names(self, query: str) -> list[str]:
        from linuxstreamdeck.ui.action_picker import ranked

        return [action.name for action in ranked(query)]

    def test_an_empty_query_lists_everything(self) -> None:
        from linuxstreamdeck.core import actions as registry

        total = sum(len(a) for a in registry.by_category().values())
        self.assertEqual(len(self._names("")), total)

    def test_an_action_is_found_by_its_name(self) -> None:
        self.assertIn("Mute input", self._names("mute"))

    def test_an_action_is_found_by_its_category(self) -> None:
        self.assertIn("Countdown timer", self._names("system"))

    def test_an_action_is_found_by_its_description(self) -> None:
        """The point of the picker: find it by what it does."""
        self.assertIn("Set text source", self._names("back in 5"))

    def test_an_action_is_found_by_its_id(self) -> None:
        self.assertIn("Raw request (advanced)", self._names("obs.raw"))

    def test_terms_are_combined_in_any_order(self) -> None:
        self.assertEqual(self._names("obs mute"), self._names("mute obs"))

    def test_a_name_match_outranks_a_description_match(self) -> None:
        """Enter picks the first result, so the obvious one has to be first."""
        self.assertEqual(self._names("stopwatch")[0], "Stopwatch")
        self.assertEqual(self._names("wait")[0], "Wait")

    def test_a_query_matching_nothing_returns_nothing(self) -> None:
        self.assertEqual(self._names("zzzzz"), [])

    def test_every_action_has_a_description(self) -> None:
        """The picker searches descriptions, so one without it is harder to
        find and shows only its category in the list."""
        from linuxstreamdeck.core import actions as registry

        undescribed = [
            action.id
            for actions in registry.by_category().values()
            for action in actions
            if not (action.description or "").strip()
        ]

        self.assertEqual(undescribed, [])

    def test_actions_are_findable_by_what_their_description_says(self) -> None:
        for query, expected in (
            ("webcam", "Virtual camera on/off"),
            ("chroma key", "Enable/disable filter"),
            ("browser", "Refresh browser source"),
            ("milliseconds", "Set transition"),
        ):
            self.assertIn(expected, self._names(query), query)


if __name__ == "__main__":
    unittest.main()
