from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from linuxstreamdeck.core.config import (
    CLOSE_ACTION_QUIT,
    CLOSE_ACTION_TRAY,
    DEFAULT_CLOSE_ACTION,
    Config,
)


class CloseActionTests(unittest.TestCase):
    def test_default_keeps_the_application_running(self) -> None:
        self.assertEqual(Config().close_action, CLOSE_ACTION_TRAY)
        self.assertEqual(DEFAULT_CLOSE_ACTION, CLOSE_ACTION_TRAY)

    def test_both_actions_survive_a_round_trip(self) -> None:
        for action in (CLOSE_ACTION_TRAY, CLOSE_ACTION_QUIT):
            with self.subTest(action=action):
                restored = Config.from_dict(
                    {"profiles": [], "close_action": action}
                )

                self.assertEqual(restored.close_action, action)

    def test_unknown_action_falls_back_to_the_default(self) -> None:
        restored = Config.from_dict({"profiles": [], "close_action": "explode"})

        self.assertEqual(restored.close_action, DEFAULT_CLOSE_ACTION)

    def test_configuration_without_the_field_still_loads(self) -> None:
        restored = Config.from_dict({"profiles": []})

        self.assertEqual(restored.close_action, DEFAULT_CLOSE_ACTION)

    def test_the_action_is_serialized(self) -> None:
        config = Config()
        config.close_action = CLOSE_ACTION_QUIT

        raw = config._serializable_dict()

        self.assertEqual(raw["close_action"], CLOSE_ACTION_QUIT)


class AutostartEntryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        patcher = patch.dict(
            os.environ, {"LSD_AUTOSTART_DIR": str(self.root / "autostart")}
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        from linuxstreamdeck.core import autostart

        self.autostart = importlib.reload(autostart)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        # Leave the module bound to the real environment for other tests.
        importlib.reload(self.autostart)

    def test_disabled_when_no_entry_exists(self) -> None:
        self.assertFalse(self.autostart.is_enabled())

    def test_enabling_writes_a_readable_entry(self) -> None:
        self.autostart.set_enabled(True)

        self.assertTrue(self.autostart.is_enabled())
        content = self.autostart.AUTOSTART_FILE.read_text(encoding="utf-8")
        self.assertIn("[Desktop Entry]", content)
        self.assertIn("Name=LinuxStreamDeck", content)
        self.assertIn(self.autostart.HIDDEN_FLAG, content)
        self.assertIn("X-GNOME-Autostart-enabled=true", content)

    def test_disabling_removes_the_entry(self) -> None:
        self.autostart.set_enabled(True)
        self.autostart.set_enabled(False)

        self.assertFalse(self.autostart.is_enabled())
        self.assertFalse(self.autostart.AUTOSTART_FILE.exists())

    def test_disabling_twice_is_harmless(self) -> None:
        self.autostart.set_enabled(False)
        self.autostart.set_enabled(False)

        self.assertFalse(self.autostart.is_enabled())

    def test_enabling_twice_keeps_one_entry(self) -> None:
        self.autostart.set_enabled(True)
        self.autostart.set_enabled(True)

        self.assertTrue(self.autostart.is_enabled())
        self.assertEqual(
            len(list(self.autostart.AUTOSTART_DIR.iterdir())), 1
        )

    def test_an_entry_hidden_by_the_desktop_counts_as_disabled(self) -> None:
        self.autostart.set_enabled(True)
        self.autostart.AUTOSTART_FILE.write_text(
            self.autostart.desktop_entry() + "Hidden=true\n", encoding="utf-8"
        )

        self.assertFalse(self.autostart.is_enabled())

    def test_an_entry_switched_off_by_gnome_counts_as_disabled(self) -> None:
        self.autostart.AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
        self.autostart.AUTOSTART_FILE.write_text(
            self.autostart.desktop_entry().replace(
                "X-GNOME-Autostart-enabled=true",
                "X-GNOME-Autostart-enabled=false",
            ),
            encoding="utf-8",
        )

        self.assertFalse(self.autostart.is_enabled())

    def test_launch_command_falls_back_to_this_interpreter(self) -> None:
        with patch("shutil.which", return_value=None):
            command = self.autostart.launch_command()

        self.assertIn("-m linuxstreamdeck", command)
        self.assertTrue(command.endswith(self.autostart.HIDDEN_FLAG))

    def test_launch_command_prefers_the_installed_script(self) -> None:
        with patch("shutil.which", return_value="/usr/bin/linuxstreamdeck"):
            command = self.autostart.launch_command()

        self.assertEqual(
            command, f"/usr/bin/linuxstreamdeck {self.autostart.HIDDEN_FLAG}"
        )


class HiddenFlagTests(unittest.TestCase):
    def setUp(self) -> None:
        from linuxstreamdeck.core import autostart

        self.autostart = autostart

    def test_flag_is_detected_and_removed(self) -> None:
        argv, hidden = self.autostart.strip_hidden_flag(
            ["linuxstreamdeck", self.autostart.HIDDEN_FLAG]
        )

        self.assertEqual(argv, ["linuxstreamdeck"])
        self.assertTrue(hidden)

    def test_argv_without_the_flag_is_untouched(self) -> None:
        argv, hidden = self.autostart.strip_hidden_flag(["linuxstreamdeck"])

        self.assertEqual(argv, ["linuxstreamdeck"])
        self.assertFalse(hidden)

    def test_other_arguments_are_preserved(self) -> None:
        argv, hidden = self.autostart.strip_hidden_flag(
            ["linuxstreamdeck", self.autostart.HIDDEN_FLAG, "--verbose"]
        )

        self.assertEqual(argv, ["linuxstreamdeck", "--verbose"])
        self.assertTrue(hidden)


class HideOnCloseTests(unittest.TestCase):
    """The rule that decides whether closing hides or quits."""

    @staticmethod
    def _decide(close_action: str, tray_available: bool, quitting: bool) -> bool:
        from linuxstreamdeck.app import LinuxStreamDeckApp

        return LinuxStreamDeckApp.hides_on_close(
            SimpleNamespace(
                _quitting=quitting,
                config=SimpleNamespace(close_action=close_action),
                tray_available=tray_available,
            )
        )

    def test_hides_when_configured_and_a_status_area_exists(self) -> None:
        self.assertTrue(self._decide(CLOSE_ACTION_TRAY, True, False))

    def test_quits_without_a_status_area(self) -> None:
        self.assertFalse(self._decide(CLOSE_ACTION_TRAY, False, False))

    def test_quits_when_the_user_chose_to_quit(self) -> None:
        self.assertFalse(self._decide(CLOSE_ACTION_QUIT, True, False))

    def test_an_explicit_quit_is_never_intercepted(self) -> None:
        self.assertFalse(self._decide(CLOSE_ACTION_TRAY, True, True))


class HiddenStartTests(unittest.TestCase):
    """A hidden start must never leave the application with no way in."""

    @staticmethod
    def _verify(tray_available: bool, shutting_down: bool = False) -> list:
        from linuxstreamdeck.app import LinuxStreamDeckApp

        shown: list = []
        LinuxStreamDeckApp._verify_hidden_start(
            SimpleNamespace(
                tray_available=tray_available,
                _shutting_down=shutting_down,
                present_window=lambda: shown.append(True),
            )
        )
        return shown

    def test_stays_hidden_once_the_icon_is_accepted(self) -> None:
        self.assertEqual(self._verify(tray_available=True), [])

    def test_shows_the_window_when_the_icon_was_refused(self) -> None:
        with self.assertLogs("linuxstreamdeck.app", level="WARNING"):
            self.assertEqual(self._verify(tray_available=False), [True])

    def test_does_nothing_while_shutting_down(self) -> None:
        self.assertEqual(
            self._verify(tray_available=False, shutting_down=True), []
        )

    def test_the_check_does_not_repeat(self) -> None:
        from linuxstreamdeck.app import LinuxStreamDeckApp

        repeat = LinuxStreamDeckApp._verify_hidden_start(
            SimpleNamespace(
                tray_available=True,
                _shutting_down=False,
                present_window=lambda: None,
            )
        )
        self.assertFalse(repeat)


class CloseRequestTests(unittest.TestCase):
    """MainWindow.close-request, exercised without a real window."""

    @staticmethod
    def _window(hides: bool, unsaved: bool):
        state = {"visible": True, "confirmed": None, "status": []}

        def set_visible(value):
            state["visible"] = value

        def confirm(destination, action, offer_save=True):
            state["confirmed"] = destination

        window = SimpleNamespace(
            _allow_close=False,
            app=SimpleNamespace(
                hides_on_close=lambda: hides,
                bus=SimpleNamespace(
                    emit=lambda _t, text="": state["status"].append(text)
                ),
            ),
            editor=SimpleNamespace(has_unsaved_changes=lambda: unsaved),
            set_visible=set_visible,
            _confirm_unsaved_changes=confirm,
            _close_after_unsaved_confirmation=lambda: None,
        )
        return window, state

    def _close(self, hides: bool, unsaved: bool):
        from linuxstreamdeck.ui.window import MainWindow

        window, state = self._window(hides, unsaved)
        return MainWindow._on_close_request(window), state

    def test_hiding_stops_the_close_and_keeps_the_window(self) -> None:
        handled, state = self._close(hides=True, unsaved=False)

        self.assertTrue(handled)
        self.assertFalse(state["visible"])
        self.assertIn("Still running in the status area", state["status"])

    def test_hiding_never_asks_about_unsaved_changes(self) -> None:
        handled, state = self._close(hides=True, unsaved=True)

        self.assertTrue(handled)
        self.assertFalse(state["visible"])
        self.assertIsNone(state["confirmed"])

    def test_quitting_cleanly_closes_the_window(self) -> None:
        handled, state = self._close(hides=False, unsaved=False)

        self.assertFalse(handled)
        self.assertTrue(state["visible"])

    def test_quitting_with_unsaved_changes_asks_first(self) -> None:
        handled, state = self._close(hides=False, unsaved=True)

        self.assertTrue(handled)
        self.assertEqual(state["confirmed"], "closing LinuxStreamDeck")


if __name__ == "__main__":
    unittest.main()
