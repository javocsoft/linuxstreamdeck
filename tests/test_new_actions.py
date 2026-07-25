from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from linuxstreamdeck import basic_actions
from linuxstreamdeck.core import actions as action_registry
from linuxstreamdeck.core import apps, keystrokes, media
from linuxstreamdeck.core.config import (
    KIND_PRESS,
    KIND_RANDOM,
    STEP_FIELDS,
    ActionStep,
    KeyConfig,
    Profile,
)


class FakeController:
    def __init__(self) -> None:
        self.config = SimpleNamespace(
            profiles=[Profile(name="General"), Profile(name="Podcast")],
            pages=[SimpleNamespace(name="Main"), SimpleNamespace(name="Scenes")],
            current_profile=0,
        )
        self.switched: list[int] = []
        self.current_page = 0

    def set_profile(self, index: int) -> None:
        self.switched.append(index)
        self.config.current_profile = index


def context(controller=None):
    messages: list[str] = []
    return (
        SimpleNamespace(
            controller=controller or FakeController(),
            bus=SimpleNamespace(
                emit=lambda _topic, text="": messages.append(text)
            ),
            key=(0, 0, 0),
        ),
        messages,
    )


class ShortcutParsingTests(unittest.TestCase):
    def test_modifiers_and_key_are_separated(self) -> None:
        self.assertEqual(keystrokes.parse("ctrl+shift+z"), (["ctrl", "shift"], "z"))

    def test_aliases_are_normalized(self) -> None:
        self.assertEqual(keystrokes.parse("Control+C")[0], ["ctrl"])
        self.assertEqual(keystrokes.parse("win+d")[0], ["super"])
        self.assertEqual(keystrokes.parse("meta+d")[0], ["super"])

    def test_key_names_are_canonical(self) -> None:
        # Whatever the user types resolves to the Linux input name.
        for typed, canonical in (
            ("escape", "esc"),
            ("return", "enter"),
            ("period", "dot"),
            (".", "dot"),
            ("print", "sysrq"),
            ("[", "leftbrace"),
            ("del", "delete"),
        ):
            with self.subTest(typed=typed):
                self.assertEqual(keystrokes.parse(typed)[1], canonical)

    def test_a_bare_key_needs_no_modifier(self) -> None:
        self.assertEqual(keystrokes.parse("print"), ([], "sysrq"))
        self.assertEqual(keystrokes.parse("f5"), ([], "f5"))

    def test_repeated_modifiers_are_collapsed(self) -> None:
        self.assertEqual(keystrokes.parse("ctrl+ctrl+a")[0], ["ctrl"])

    def test_invalid_shortcuts_are_rejected(self) -> None:
        for text in ("", "   ", "ctrl+", "ctrl+a+b", "ctrl+nosuchkey"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    keystrokes.parse(text)

    def test_every_preset_is_sendable(self) -> None:
        for label, shortcut in keystrokes.SHORTCUT_PRESETS:
            if not shortcut:
                continue
            with self.subTest(preset=label):
                self.assertTrue(keystrokes.parse(shortcut))

    def test_presets_start_with_an_empty_entry(self) -> None:
        self.assertEqual(keystrokes.SHORTCUT_PRESETS[0], ("", ""))
        self.assertEqual(keystrokes.PRESET_LABELS[0], "")

    def test_preset_labels_are_unique(self) -> None:
        labels = list(keystrokes.PRESET_LABELS)

        self.assertEqual(len(labels), len(set(labels)))


class ShortcutCommandTests(unittest.TestCase):
    def test_ydotool_1x_uses_press_and_release_key_codes(self) -> None:
        with patch.object(keystrokes, "ydotool_syntax", return_value="codes"):
            command = keystrokes.command_for("ctrl+c", "ydotool")

        # 29 = KEY_LEFTCTRL, 46 = KEY_C; released in reverse order.
        self.assertEqual(command, ["ydotool", "key", "29:1", "46:1", "46:0", "29:0"])

    def test_ydotool_0x_uses_a_key_sequence(self) -> None:
        # Debian and Ubuntu still ship 0.x, which takes names, not codes.
        with patch.object(keystrokes, "ydotool_syntax", return_value="names"):
            self.assertEqual(
                keystrokes.command_for("ctrl+shift+z", "ydotool"),
                ["ydotool", "key", "ctrl+shift+z"],
            )
            self.assertEqual(
                keystrokes.command_for("print", "ydotool"),
                ["ydotool", "key", "sysrq"],
            )

    def test_the_0x_help_text_is_recognised(self) -> None:
        help_text = (
            "Usage: key [--delay <ms>] <key sequence> ...\n"
            "Each key sequence can be any number of modifiers and keys"
        )
        completed = SimpleNamespace(stdout=help_text, stderr="")

        with patch("subprocess.run", return_value=completed):
            keystrokes.ydotool_syntax.cache_clear()
            self.assertEqual(keystrokes.ydotool_syntax("/usr/bin/ydotool"), "names")
        keystrokes.ydotool_syntax.cache_clear()

    def test_the_1x_help_text_is_recognised(self) -> None:
        completed = SimpleNamespace(
            stdout="Usage: key [OPTION]... [KEYCODE:PRESSED]...", stderr=""
        )

        with patch("subprocess.run", return_value=completed):
            keystrokes.ydotool_syntax.cache_clear()
            self.assertEqual(keystrokes.ydotool_syntax("/usr/bin/ydotool"), "codes")
        keystrokes.ydotool_syntax.cache_clear()

    def test_an_unreadable_ydotool_falls_back_to_the_modern_syntax(self) -> None:
        keystrokes.ydotool_syntax.cache_clear()
        self.assertEqual(
            keystrokes.ydotool_syntax("/definitely/not/here/ydotool"), "codes"
        )
        keystrokes.ydotool_syntax.cache_clear()

    def test_xdotool_uses_key_names(self) -> None:
        self.assertEqual(
            keystrokes.command_for("super+left", "xdotool"),
            ["xdotool", "key", "super+Left"],
        )

    def test_wtype_holds_and_releases_each_modifier(self) -> None:
        command = keystrokes.command_for("ctrl+shift+s", "wtype")

        self.assertEqual(
            command,
            ["wtype", "-M", "ctrl", "-M", "shift", "-k", "s", "-m", "shift", "-m", "ctrl"],
        )

    def test_an_unknown_tool_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            keystrokes.command_for("ctrl+c", "nosuchtool")

    def test_without_a_backend_the_message_says_what_to_install(self) -> None:
        with patch.object(keystrokes, "backend", return_value=""):
            with self.assertRaises(ValueError) as raised:
                keystrokes.command_for("ctrl+c")

        self.assertIn("ydotool", str(raised.exception))

    def test_backend_detection_prefers_ydotool(self) -> None:
        with patch("shutil.which", side_effect=lambda tool: tool in ("ydotool", "xdotool")):
            self.assertEqual(keystrokes.backend(), "ydotool")
        with patch("shutil.which", side_effect=lambda tool: tool == "xdotool"):
            self.assertEqual(keystrokes.backend(), "xdotool")
        with patch("shutil.which", return_value=None):
            self.assertEqual(keystrokes.backend(), "")
            self.assertFalse(keystrokes.is_available())


class MediaTests(unittest.TestCase):
    def test_labels_and_identifiers_map_both_ways(self) -> None:
        for identifier, label in media.MEDIA_ACTIONS:
            with self.subTest(identifier=identifier):
                self.assertEqual(media.label_for(identifier), label)
                self.assertEqual(media.identifier_for(label), identifier)

    def test_the_catalogue_matches_the_official_software(self) -> None:
        self.assertEqual(
            list(media.MEDIA_ACTION_LABELS),
            [
                "Previous track",
                "Play / Pause",
                "Next track",
                "Stop",
                "Mute",
                "Volume up",
                "Volume down",
            ],
        )

    def test_an_unknown_label_falls_back_to_the_default(self) -> None:
        self.assertEqual(media.identifier_for("Nope"), media.DEFAULT_MEDIA_ACTION)

    def test_an_unknown_action_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            media.perform("teleport")

    def test_transport_without_playerctl_explains_itself(self) -> None:
        with patch.object(media, "transport_available", return_value=False):
            with self.assertRaises(ValueError) as raised:
                media.perform("play_pause")

        self.assertIn("playerctl", str(raised.exception))

    def test_volume_without_a_mixer_explains_itself(self) -> None:
        with patch.object(media, "mixer_command", return_value=None):
            with self.assertRaises(ValueError) as raised:
                media.perform("volume_up")

        self.assertIn("mixer", str(raised.exception).lower())


class ApplicationTests(unittest.TestCase):
    def test_choices_are_names_and_are_sorted(self) -> None:
        choices = apps.application_choices()

        self.assertEqual(choices, sorted(choices, key=str.casefold))

    def test_an_unknown_application_is_not_resolved(self) -> None:
        self.assertIsNone(apps.find_application("no-such-application-xyz"))
        self.assertIsNone(apps.find_application(""))

    def test_launching_an_unknown_application_is_reported(self) -> None:
        with self.assertRaises(ValueError):
            apps.launch("no-such-application-xyz")

    def test_opening_nothing_is_reported(self) -> None:
        with self.assertRaises(ValueError):
            apps.open_target("")

    def test_opening_a_missing_path_is_reported(self) -> None:
        with self.assertRaises(ValueError):
            apps.open_target("/tmp/definitely-not-here-42/file.txt")

    def test_closing_something_not_running_is_reported(self) -> None:
        with patch.object(apps, "running_pids", return_value=[]):
            with self.assertRaises(ValueError):
                apps.close("Whatever")


class PageIndicatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.action = action_registry.get("nav.page.indicator")
        self.ctx, self.messages = context()

    def test_it_shows_the_position_by_default(self) -> None:
        feedback = self.action.feedback(self.ctx, {})

        self.assertEqual(feedback["display"], "1/2")

    def test_it_can_show_only_the_number(self) -> None:
        self.ctx.controller.current_page = 1
        feedback = self.action.feedback(
            self.ctx, {"show": basic_actions.PAGE_SHOW_NUMBER}
        )

        self.assertEqual(feedback["display"], "2")

    def test_it_can_show_the_page_name(self) -> None:
        self.ctx.controller.current_page = 1
        feedback = self.action.feedback(
            self.ctx, {"show": basic_actions.PAGE_SHOW_NAME}
        )

        self.assertEqual(feedback["display"], "Scenes")

    def test_pressing_it_changes_nothing(self) -> None:
        self.action.execute(self.ctx, {})

        self.assertEqual(self.messages, [])

    def test_it_never_occupies_an_action_worker(self) -> None:
        self.assertTrue(self.action.immediate)


class ChangeProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.action = action_registry.get("nav.profile.go")
        self.ctx, self.messages = context()

    def test_it_switches_to_the_named_profile(self) -> None:
        self.action.execute(self.ctx, {"profile": "Podcast"})

        self.assertEqual(self.ctx.controller.switched, [1])

    def test_an_unknown_profile_is_reported(self) -> None:
        self.action.execute(self.ctx, {"profile": "Nope"})

        self.assertEqual(self.ctx.controller.switched, [])
        self.assertIn("Nope", self.messages[0])

    def test_an_empty_selection_asks_for_one(self) -> None:
        self.action.execute(self.ctx, {})

        self.assertEqual(self.ctx.controller.switched, [])
        self.assertTrue(self.messages)

    def test_its_options_come_from_the_deck_profiles(self) -> None:
        self.assertEqual(self.action.params[0].choices_source, "deck_profiles")


class ShortcutSwitchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.action = action_registry.get("sys.shortcut.switch")
        self.action._state.clear()
        self.ctx, self.messages = context()
        self.sent: list[str] = []
        patcher = patch.object(
            keystrokes, "send", side_effect=lambda value: self.sent.append(value)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_presses_alternate_between_the_two_shortcuts(self) -> None:
        params = {"first": "ctrl+c", "second": "ctrl+v"}

        for _ in range(4):
            self.action.execute(self.ctx, params)

        self.assertEqual(self.sent, ["ctrl+c", "ctrl+v", "ctrl+c", "ctrl+v"])

    def test_the_badge_shows_which_one_comes_next(self) -> None:
        params = {"first": "ctrl+c", "second": "ctrl+v"}

        self.assertEqual(self.action.feedback(self.ctx, params)["badge"], "1")
        self.action.execute(self.ctx, params)
        self.assertEqual(self.action.feedback(self.ctx, params)["badge"], "2")

    def test_state_is_kept_per_key(self) -> None:
        params = {"first": "ctrl+c", "second": "ctrl+v"}
        other = SimpleNamespace(**vars(self.ctx))
        other.key = (0, 0, 1)

        self.action.execute(self.ctx, params)
        self.action.execute(other, params)

        self.assertEqual(self.sent, ["ctrl+c", "ctrl+c"])

    def test_a_failure_is_reported_instead_of_raising(self) -> None:
        with patch.object(keystrokes, "send", side_effect=ValueError("no backend")):
            self.action.execute(self.ctx, {"first": "ctrl+c"})

        self.assertEqual(self.messages, ["no backend"])


class OpenApplicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.action = action_registry.get("sys.app.open")
        self.ctx, self.messages = context()

    def test_it_declares_long_press_support(self) -> None:
        self.assertTrue(self.action.supports_long_press)

    def test_a_long_press_set_to_nothing_declines(self) -> None:
        handled = self.action.long_press(
            self.ctx, {"application": "X", "long_press": basic_actions.LONG_PRESS_NOTHING}
        )

        self.assertFalse(handled)

    def test_a_long_press_can_close_the_application(self) -> None:
        with patch.object(apps, "close", return_value=1) as close:
            handled = self.action.long_press(
                self.ctx,
                {"application": "X", "long_press": basic_actions.LONG_PRESS_CLOSE},
            )

        self.assertTrue(handled)
        close.assert_called_once_with("X", force=False)

    def test_a_long_press_can_force_the_close(self) -> None:
        with patch.object(apps, "close", return_value=1) as close:
            self.action.long_press(
                self.ctx,
                {
                    "application": "X",
                    "long_press": basic_actions.LONG_PRESS_FORCE_CLOSE,
                },
            )

        close.assert_called_once_with("X", force=True)

    def test_it_reports_when_it_cannot_close(self) -> None:
        with patch.object(apps, "close", side_effect=ValueError("not running")):
            handled = self.action.long_press(
                self.ctx,
                {"application": "X", "long_press": basic_actions.LONG_PRESS_CLOSE},
            )

        self.assertTrue(handled)
        self.assertEqual(self.messages, ["not running"])

    def test_it_can_refuse_to_relaunch_a_running_application(self) -> None:
        with patch.object(apps, "is_running", return_value=True):
            with patch.object(apps, "launch") as launch:
                self.action.execute(
                    self.ctx,
                    {"application": "X", "if_running": basic_actions.NO},
                )

        launch.assert_not_called()

    def test_it_relaunches_to_raise_the_window_by_default(self) -> None:
        with patch.object(apps, "is_running", return_value=True):
            with patch.object(apps, "launch") as launch:
                self.action.execute(
                    self.ctx,
                    {"application": "X", "if_running": basic_actions.YES},
                )

        launch.assert_called_once_with("X")

    def test_feedback_marks_a_running_application(self) -> None:
        with patch.object(apps, "is_running", return_value=True):
            self.assertTrue(
                self.action.feedback(self.ctx, {"application": "X"})["active"]
            )


class CloseApplicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.action = action_registry.get("sys.app.close")
        self.ctx, self.messages = context()

    def test_it_asks_politely_by_default(self) -> None:
        with patch.object(apps, "close", return_value=1) as close:
            self.action.execute(self.ctx, {"application": "X"})

        close.assert_called_once_with("X", force=False)

    def test_it_can_force_the_close(self) -> None:
        with patch.object(apps, "close", return_value=1) as close:
            self.action.execute(
                self.ctx, {"application": "X", "force": basic_actions.YES}
            )

        close.assert_called_once_with("X", force=True)

    def test_an_empty_selection_asks_for_one(self) -> None:
        self.action.execute(self.ctx, {})

        self.assertTrue(self.messages)


class NewKindTests(unittest.TestCase):
    def test_every_step_list_is_covered_by_step_fields(self) -> None:
        key = KeyConfig()

        for name in STEP_FIELDS:
            with self.subTest(field=name):
                self.assertIsInstance(getattr(key, name), list)

    def test_a_random_key_needs_actions(self) -> None:
        self.assertTrue(KeyConfig(kind=KIND_RANDOM).is_empty())
        self.assertFalse(
            KeyConfig(kind=KIND_RANDOM, steps=[ActionStep(action="sys.wait")]).is_empty()
        )

    def test_a_gesture_key_needs_at_least_one_list(self) -> None:
        self.assertTrue(KeyConfig(kind=KIND_PRESS).is_empty())
        for name in ("steps_single", "steps_double", "steps_long"):
            with self.subTest(field=name):
                key = KeyConfig(kind=KIND_PRESS, **{name: [ActionStep(action="sys.wait")]})
                self.assertFalse(key.is_empty())

    def test_the_new_kinds_survive_a_round_trip(self) -> None:
        key = KeyConfig(
            kind=KIND_PRESS,
            steps_single=[ActionStep(action="sys.wait", params={"duration": "00:01"})],
            steps_double=[ActionStep(action="sys.command", params={"command": "x"})],
            steps_long=[ActionStep(action="sys.url", params={"url": "https://a"})],
            label="Gestures",
        )

        restored = KeyConfig.from_dict(
            {
                "kind": key.kind,
                "label": key.label,
                "steps_single": [{"action": "sys.wait", "params": {"duration": "00:01"}}],
                "steps_double": [{"action": "sys.command", "params": {"command": "x"}}],
                "steps_long": [{"action": "sys.url", "params": {"url": "https://a"}}],
            }
        )

        self.assertEqual(restored, key)

    def test_a_legacy_page_action_still_migrates_in_the_new_lists(self) -> None:
        restored = KeyConfig.from_dict(
            {
                "kind": KIND_PRESS,
                "steps_long": [{"action": "nav.page", "params": {"mode": "next"}}],
            }
        )

        self.assertEqual(restored.steps_long[0].action, "nav.page.next")


if __name__ == "__main__":
    unittest.main()
