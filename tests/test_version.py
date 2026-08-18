"""The version is one value, reported consistently everywhere it is promised."""

from __future__ import annotations

import io
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from linuxstreamdeck import APP_NAME, VERSION
from linuxstreamdeck.__main__ import main, version_requested

ROOT = Path(__file__).resolve().parent.parent


class VersionSourceTests(unittest.TestCase):
    def test_the_version_looks_like_a_release(self) -> None:
        self.assertRegex(VERSION, r"^\d+\.\d+\.\d+$")

    def test_pyproject_agrees_with_the_package(self) -> None:
        """build-deb.sh syncs both; a mismatch means one was edited by hand."""
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        found = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
        self.assertIsNotNone(found)
        self.assertEqual(found.group(1), VERSION)

    def test_the_appstream_release_is_templated_not_hardcoded(self) -> None:
        """A hardcoded release would silently go stale after every build."""
        metainfo = ROOT / "packaging" / "com.javocsoft.LinuxStreamDeck.metainfo.xml"
        text = metainfo.read_text(encoding="utf-8")
        self.assertIn('<release version="@VERSION@"', text)
        self.assertNotRegex(text, r'<release version="\d+\.\d+\.\d+"')

    def test_the_bug_report_version_is_a_generic_prompt(self) -> None:
        """Reporters need their installed version, not the latest release."""
        template = ROOT / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml"
        text = template.read_text(encoding="utf-8")
        self.assertIn('placeholder: "X.Y.Z"', text)
        self.assertNotIn(f'placeholder: "{VERSION}"', text)


class VersionFlagTests(unittest.TestCase):
    """The bug report template tells people to run `linuxstreamdeck --version`."""

    def test_the_flag_is_recognised(self) -> None:
        self.assertTrue(version_requested(["linuxstreamdeck", "--version"]))
        self.assertTrue(version_requested(["linuxstreamdeck", "-V"]))

    def test_running_normally_is_not_a_version_request(self) -> None:
        self.assertFalse(version_requested(["linuxstreamdeck"]))
        self.assertFalse(version_requested(["linuxstreamdeck", "--hidden"]))

    def test_it_prints_the_version_and_exits_cleanly(self) -> None:
        argv = sys.argv
        sys.argv = ["linuxstreamdeck", "--version"]
        output = io.StringIO()
        try:
            with redirect_stdout(output):
                code = main()
        finally:
            sys.argv = argv

        self.assertEqual(code, 0)
        self.assertEqual(output.getvalue().strip(), f"{APP_NAME} {VERSION}")

    def test_the_bug_report_template_asks_for_a_command_that_works(self) -> None:
        template = ROOT / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml"
        text = template.read_text(encoding="utf-8")
        if "--version" not in text:
            self.skipTest("the template no longer mentions the flag")
        with tempfile.TemporaryDirectory() as config_dir:
            result = subprocess.run(
                [sys.executable, "-m", "linuxstreamdeck", "--version"],
                capture_output=True,
                text=True,
                cwd=ROOT,
                env={"PATH": "/usr/bin:/bin", "LSD_CONFIG_DIR": config_dir},
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(VERSION, result.stdout)


if __name__ == "__main__":
    unittest.main()
