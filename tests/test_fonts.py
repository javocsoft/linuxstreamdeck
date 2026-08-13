"""The text font has to be found on every distribution, and in a sandbox.

This is not a feature suite. It exists because the failure it guards is
completely silent: Pillow answers a missing font with `ImageFont.load_default()`
rather than raising, so a machine whose fonts live somewhere unexpected renders
every key label, the startup title and the screen saver at roughly 40% size and
logs nothing at all. Measured: "Record" at size 20 is 79 px wide with a real
font and 32 px with the default one.

Every candidate list used to hold Debian and Arch paths only, which meant
Fedora, openSUSE and the `org.freedesktop.Platform` runtime the Flatpak build
sits on all took that path.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import ImageFont

from linuxstreamdeck.core import fonts
from linuxstreamdeck.device import layout_sheet, renderer, screensaver
from linuxstreamdeck.device import startup_animation


def _probe(module, candidates, size=20, attr="_FONT_CANDIDATES"):
    """Resolve a font through one module's own loader and measure it."""
    loader = module._font
    if hasattr(loader, "cache_clear"):
        loader.cache_clear()
    with patch.object(module, attr, candidates):
        font = loader(size)
    if hasattr(loader, "cache_clear"):
        loader.cache_clear()
    return font


class BundledFallbackTests(unittest.TestCase):
    def test_the_package_carries_a_font_of_its_own(self) -> None:
        """An AppImage may run where no font is installed at all."""
        for path in (fonts.BUNDLED_BOLD, fonts.BUNDLED_REGULAR):
            self.assertTrue(
                Path(path).exists(),
                f"{path} is missing; the package must ship its own font",
            )

    def test_the_bundled_font_is_the_last_resort_not_the_first_choice(self) -> None:
        """A machine's own DejaVu stays preferred, so nothing changes there."""
        for candidates in (fonts.SANS_BOLD, fonts.SANS_REGULAR):
            self.assertGreater(len(candidates), 1)
            self.assertIn(fonts.BUNDLED_BOLD if candidates is fonts.SANS_BOLD
                          else fonts.BUNDLED_REGULAR, candidates)
            self.assertEqual(
                candidates[-1],
                fonts.BUNDLED_BOLD if candidates is fonts.SANS_BOLD
                else fonts.BUNDLED_REGULAR,
                "the bundled font must come last, or it would override the "
                "font the machine actually has",
            )

    def test_the_bundled_font_renders_at_the_same_size_as_a_system_one(self) -> None:
        system = _probe(renderer, fonts.SANS_BOLD)
        bundled = _probe(renderer, (fonts.BUNDLED_BOLD,))

        self.assertEqual(
            system.getbbox("Record"), bundled.getbbox("Record"),
            "the bundled font must render identically to the system one",
        )

    def test_finding_no_font_at_all_is_visibly_worse(self) -> None:
        """Pins the symptom, so this test cannot pass for the wrong reason."""
        real = _probe(renderer, fonts.SANS_BOLD).getbbox("Record")
        none = _probe(renderer, ()).getbbox("Record")

        self.assertLess(
            none[2] - none[0], (real[2] - real[0]) * 0.7,
            "load_default() should be dramatically smaller; if it is not, "
            "this suite can no longer tell a found font from a missing one",
        )


class DistributionCoverageTests(unittest.TestCase):
    """The paths that were missing, named so a regression is obvious."""

    def test_every_renderer_shares_one_list(self) -> None:
        """Four separate lists is how three of them went stale."""
        self.assertIs(renderer._FONT_CANDIDATES, fonts.SANS_BOLD)
        self.assertIs(screensaver._FONT_CANDIDATES, fonts.SANS_BOLD)
        self.assertIs(startup_animation._FONT_CANDIDATES, fonts.SANS_BOLD)
        self.assertIs(layout_sheet._FONT_CANDIDATES, fonts.SANS_REGULAR)
        self.assertIs(screensaver._CJK_FONT_CANDIDATES, fonts.CJK)

    def test_the_flatpak_runtime_path_is_covered(self) -> None:
        """org.freedesktop.Platform ships DejaVu here; no old list had it."""
        self.assertIn("/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf", fonts.SANS_BOLD)
        self.assertIn("/usr/share/fonts/dejavu/DejaVuSans.ttf", fonts.SANS_REGULAR)

    def test_fedora_paths_are_covered(self) -> None:
        self.assertIn(
            "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf", fonts.SANS_BOLD
        )
        self.assertIn(
            "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf", fonts.SANS_REGULAR
        )

    def test_first_present_answers_empty_rather_than_raising(self) -> None:
        self.assertEqual(fonts.first_present(()), "")
        self.assertEqual(fonts.first_present(("/nowhere/at/all.ttf",)), "")
        self.assertEqual(
            fonts.first_present(("/nowhere.ttf", fonts.BUNDLED_BOLD)),
            fonts.BUNDLED_BOLD,
        )


class LayoutEngineTests(unittest.TestCase):
    """AGENTS.md section 5.1 still applies to the bundled font."""

    def test_the_bundled_font_is_loaded_with_the_basic_engine(self) -> None:
        seen = []
        real = ImageFont.truetype

        def record(path, size=10, *args, **kwargs):
            seen.append(kwargs.get("layout_engine"))
            return real(path, size, *args, **kwargs)

        with patch.object(ImageFont, "truetype", record):
            _probe(renderer, (fonts.BUNDLED_BOLD,))

        self.assertTrue(seen, "no font was loaded; the test is blind")
        self.assertTrue(
            all(engine is ImageFont.Layout.BASIC for engine in seen),
            "the bundled font must load with BASIC layout like every other",
        )


if __name__ == "__main__":
    unittest.main()
