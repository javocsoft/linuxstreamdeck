from __future__ import annotations

import unittest
from unittest.mock import patch

from linuxstreamdeck.obs.client import OBSClient, hotkey_display_name
from linuxstreamdeck.ui.steps import _display_options, _label_for


class HotkeyDisplayNameTests(unittest.TestCase):
    def test_a_core_hotkey_drops_its_generic_prefix(self) -> None:
        self.assertEqual(
            hotkey_display_name("OBSBasic.StartStreaming"), "Start streaming"
        )
        self.assertEqual(hotkey_display_name("libobs.mute"), "Mute")

    def test_separators_become_words(self) -> None:
        self.assertEqual(
            hotkey_display_name("libobs.push-to-mute"), "Push to mute"
        )

    def test_a_meaningful_prefix_is_kept_as_context(self) -> None:
        self.assertEqual(
            hotkey_display_name("SlideShow.PlayPause"), "Slide show: Play pause"
        )

    def test_acronyms_survive(self) -> None:
        self.assertEqual(
            hotkey_display_name("VLCSource.PlayPause"), "VLC source: Play pause"
        )

    def test_a_name_without_a_prefix_still_reads(self) -> None:
        self.assertEqual(hotkey_display_name("WeirdNameNoDot"), "Weird name no dot")

    def test_an_empty_name_stays_empty(self) -> None:
        self.assertEqual(hotkey_display_name(""), "")
        self.assertEqual(hotkey_display_name(None), "")

    def test_an_unreadable_name_falls_back_to_itself(self) -> None:
        self.assertEqual(hotkey_display_name("..."), "...")


class HotkeyListTests(unittest.TestCase):
    def test_repeated_names_are_offered_once(self) -> None:
        client = OBSClient.__new__(OBSClient)
        raw = {
            "hotkeys": [
                "OBSBasic.StartStreaming",
                "libobs.mute",
                "libobs.mute",
                "libobs.unmute",
                "libobs.mute",
            ]
        }

        with patch.object(OBSClient, "try_request", return_value=raw):
            self.assertEqual(
                client.get_hotkeys(),
                ["OBSBasic.StartStreaming", "libobs.mute", "libobs.unmute"],
            )

    def test_the_original_order_is_kept(self) -> None:
        client = OBSClient.__new__(OBSClient)
        raw = {"hotkeys": ["b", "a", "b", "c"]}

        with patch.object(OBSClient, "try_request", return_value=raw):
            self.assertEqual(client.get_hotkeys(), ["b", "a", "c"])

    def test_a_failed_request_gives_no_hotkeys(self) -> None:
        client = OBSClient.__new__(OBSClient)

        with patch.object(OBSClient, "try_request", return_value=None):
            self.assertEqual(client.get_hotkeys(), [])


class DisplayOptionTests(unittest.TestCase):
    HOTKEYS = [
        "OBSBasic.StartStreaming",
        "libobs.mute",
        "SlideShow.PlayPause",
    ]

    def test_hotkeys_are_shown_readable_but_stored_raw(self) -> None:
        labels, values = _display_options("hotkeys", self.HOTKEYS)

        self.assertEqual(
            labels, ["Start streaming", "Mute", "Slide show: Play pause"]
        )
        self.assertEqual(values["Start streaming"], "OBSBasic.StartStreaming")
        self.assertEqual(values["Slide show: Play pause"], "SlideShow.PlayPause")

    def test_other_sources_are_left_alone(self) -> None:
        scenes = ["Intro", "Gameplay"]

        labels, values = _display_options("scenes", scenes)

        self.assertEqual(labels, scenes)
        self.assertEqual(values, {})

    def test_two_hotkeys_reading_alike_stay_distinguishable(self) -> None:
        labels, values = _display_options(
            "hotkeys", ["Thing.play-pause", "Thing.PlayPause"]
        )

        self.assertEqual(len(labels), 2)
        self.assertEqual(len(set(labels)), 2)
        self.assertEqual(set(values.values()), {"Thing.play-pause", "Thing.PlayPause"})

    def test_an_identical_name_is_not_offered_twice(self) -> None:
        labels, _values = _display_options("hotkeys", ["libobs.mute", "libobs.mute"])

        self.assertEqual(labels, ["Mute"])

    def test_a_stored_value_resolves_to_its_label(self) -> None:
        _labels, values = _display_options("hotkeys", self.HOTKEYS)

        self.assertEqual(_label_for(values, "libobs.mute"), "Mute")

    def test_an_unknown_value_is_shown_as_itself(self) -> None:
        _labels, values = _display_options("hotkeys", self.HOTKEYS)

        self.assertEqual(_label_for(values, "Gone.Away"), "Gone.Away")

    def test_a_source_without_a_map_shows_the_value(self) -> None:
        self.assertEqual(_label_for({}, "Intro"), "Intro")

    def test_the_labels_a_dropdown_shows_all_map_back(self) -> None:
        labels, values = _display_options("hotkeys", self.HOTKEYS)

        # Whatever the dropdown lists, the editor must be able to store a real
        # OBS identifier for it.
        self.assertEqual(
            [values[label] for label in labels], list(self.HOTKEYS)
        )


if __name__ == "__main__":
    unittest.main()
