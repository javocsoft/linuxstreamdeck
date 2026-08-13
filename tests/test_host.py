"""Reaching host tools from a sandbox, and changing nothing outside one.

The risk this guards is asymmetric. Getting it wrong inside a Flatpak means a
few keys do not work and say so. Getting it wrong *outside* one means every
machine changes behaviour, which is why most of these tests are about the
uncached, unwrapped pass-through being exactly what it was before.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from linuxstreamdeck.core import host


class PassThroughTests(unittest.TestCase):
    """Outside a sandbox nothing may change."""

    def setUp(self) -> None:
        host.forget()
        self.addCleanup(host.forget)

    def test_a_command_is_left_alone(self) -> None:
        with patch.object(host, "in_flatpak", lambda: False):
            self.assertEqual(host.argv(["pactl", "info"]), ["pactl", "info"])

    def test_which_is_plain_shutil_and_is_not_cached(self) -> None:
        """A tool installed while the application runs has to be noticed.

        The first version cached every answer, which both hid that and leaked
        stale answers between tests in the suite.
        """
        answers = iter([None, "/usr/bin/playerctl"])
        with patch.object(host, "in_flatpak", lambda: False), \
             patch.object(host.shutil, "which", lambda _n: next(answers)):
            self.assertIsNone(host.which("playerctl"))
            self.assertEqual(host.which("playerctl"), "/usr/bin/playerctl")


class SandboxTests(unittest.TestCase):
    def setUp(self) -> None:
        host.forget()
        self.addCleanup(host.forget)

    def test_a_command_is_run_on_the_machine(self) -> None:
        with patch.object(host, "in_flatpak", lambda: True):
            self.assertEqual(
                host.argv(["pactl", "info"]),
                ["flatpak-spawn", "--host", "pactl", "info"],
            )

    def test_an_empty_command_is_not_wrapped(self) -> None:
        with patch.object(host, "in_flatpak", lambda: True):
            self.assertEqual(host.argv([]), [])

    def test_no_portal_means_the_tool_is_absent(self) -> None:
        """Without the permission there is nothing to ask, and "absent" is the
        answer every caller already knows how to report."""
        with patch.object(host, "in_flatpak", lambda: True), \
             patch.object(host.shutil, "which", lambda _n: None):
            self.assertIsNone(host.which("pactl"))

    def test_the_host_answer_is_used(self) -> None:
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            return type("R", (), {"returncode": 0, "stdout": "/usr/bin/pactl\n"})()

        with patch.object(host, "in_flatpak", lambda: True), \
             patch.object(host.shutil, "which", lambda _n: "/usr/bin/flatpak-spawn"), \
             patch.object(host.subprocess, "run", fake_run):
            self.assertEqual(host.which("pactl"), "/usr/bin/pactl")

        self.assertEqual(calls, [["flatpak-spawn", "--host", "which", "pactl"]])

    def test_a_missing_tool_reports_absent_rather_than_raising(self) -> None:
        def fake_run(argv, **kwargs):
            return type("R", (), {"returncode": 1, "stdout": ""})()

        with patch.object(host, "in_flatpak", lambda: True), \
             patch.object(host.shutil, "which", lambda _n: "/usr/bin/flatpak-spawn"), \
             patch.object(host.subprocess, "run", fake_run):
            self.assertIsNone(host.which("ydotool"))

    def test_a_wedged_portal_reports_absent_rather_than_raising(self) -> None:
        """A key press must never surface a subprocess error from here."""
        def boom(argv, **kwargs):
            raise OSError("no portal")

        with patch.object(host, "in_flatpak", lambda: True), \
             patch.object(host.shutil, "which", lambda _n: "/usr/bin/flatpak-spawn"), \
             patch.object(host.subprocess, "run", boom):
            self.assertIsNone(host.which("pactl"))

    def test_the_sandboxed_answer_is_asked_for_once(self) -> None:
        """It costs a process, and `available()` runs on the render worker."""
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            return type("R", (), {"returncode": 0, "stdout": "/usr/bin/pactl\n"})()

        with patch.object(host, "in_flatpak", lambda: True), \
             patch.object(host.shutil, "which", lambda _n: "/usr/bin/flatpak-spawn"), \
             patch.object(host.subprocess, "run", fake_run):
            for _ in range(5):
                host.which("pactl")

        self.assertEqual(len(calls), 1, "the host should be asked once, not per repaint")


class DetectionTests(unittest.TestCase):
    def setUp(self) -> None:
        host.forget()
        self.addCleanup(host.forget)

    def test_the_marker_file_is_enough(self) -> None:
        with patch.object(host.os.path, "exists", lambda p: p == "/.flatpak-info"), \
             patch.dict(host.os.environ, {}, clear=True):
            self.assertTrue(host.in_flatpak())

    def test_the_environment_variable_is_enough(self) -> None:
        with patch.object(host.os.path, "exists", lambda _p: False), \
             patch.dict(host.os.environ, {"FLATPAK_ID": "com.example.App"}):
            self.assertTrue(host.in_flatpak())

    def test_an_ordinary_machine_is_not_a_sandbox(self) -> None:
        with patch.object(host.os.path, "exists", lambda _p: False), \
             patch.dict(host.os.environ, {}, clear=True):
            self.assertFalse(host.in_flatpak())


if __name__ == "__main__":
    unittest.main()
