"""The printable layout sheet.

It documents a profile, so it must show every grid the user can reach — each
page and each folder inside it — take its shape from the connected deck rather
than a hardcoded 5x3, and caption each key with what it does, since an
icon-only key is unidentifiable away from the hardware.
"""

from __future__ import annotations

import unittest

from PIL import Image, ImageDraw

from linuxstreamdeck import basic_actions as _basic_actions  # noqa: F401
from linuxstreamdeck.core.config import (
    KIND_FOLDER,
    KIND_MULTI,
    KIND_PRESS,
    KIND_RANDOM,
    KIND_SINGLE,
    KIND_TOGGLE,
    ActionStep,
    Config,
    Folder,
    KeyConfig,
    Page,
)
from linuxstreamdeck.device import layout_sheet
from linuxstreamdeck.obs import actions as _obs_actions  # noqa: F401


def _profile() -> object:
    config = Config()
    profile = config.profile
    profile.name = "Streaming"
    profile.pages = [Page(name="Live"), Page(name="BRB")]
    return profile


class CaptionTests(unittest.TestCase):
    def test_an_empty_key_has_no_caption(self) -> None:
        self.assertEqual(layout_sheet.key_caption(None), "")
        self.assertEqual(layout_sheet.key_caption(KeyConfig()), "")

    def test_a_single_key_is_named_after_its_action(self) -> None:
        kc = KeyConfig(kind=KIND_SINGLE, action="obs.record")

        self.assertEqual(layout_sheet.key_caption(kc), "Record on/off")

    def test_a_multi_key_names_the_first_action_and_counts_the_rest(self) -> None:
        kc = KeyConfig(
            kind=KIND_MULTI,
            steps=[
                ActionStep(action="obs.stream"),
                ActionStep(action="sys.wait"),
                ActionStep(action="obs.record"),
            ],
        )

        self.assertEqual(layout_sheet.key_caption(kc), "Stream on/off +2")

    def test_a_single_step_list_carries_no_count(self) -> None:
        kc = KeyConfig(kind=KIND_MULTI, steps=[ActionStep(action="obs.record")])

        self.assertEqual(layout_sheet.key_caption(kc), "Record on/off")

    def test_a_toggle_is_named_after_its_on_list(self) -> None:
        kc = KeyConfig(
            kind=KIND_TOGGLE, steps_on=[ActionStep(action="obs.mute")]
        )

        self.assertEqual(layout_sheet.key_caption(kc), "Mute input")

    def test_a_gesture_key_covers_all_three_lists(self) -> None:
        kc = KeyConfig(
            kind=KIND_PRESS,
            steps_single=[ActionStep(action="obs.record")],
            steps_long=[ActionStep(action="obs.stream")],
        )

        self.assertEqual(layout_sheet.key_caption(kc), "Record on/off +1")

    def test_an_empty_step_list_falls_back_to_the_kind(self) -> None:
        self.assertEqual(
            layout_sheet.key_caption(KeyConfig(kind=KIND_RANDOM, steps=[])),
            "",
        )

    def test_a_folder_is_captioned_with_how_many_keys_it_holds(self) -> None:
        folder = KeyConfig(kind=KIND_FOLDER, folder=Folder())
        folder.folder.set_key(1, KeyConfig(kind=KIND_SINGLE, action="obs.record"))

        self.assertEqual(layout_sheet.key_caption(folder), "Folder (1)")

    def test_an_empty_folder_still_says_it_is_one(self) -> None:
        folder = KeyConfig(kind=KIND_FOLDER, folder=Folder())

        self.assertEqual(layout_sheet.key_caption(folder), "Folder")


class CaptionDrawingTests(unittest.TestCase):
    """A caption may be shortened, but it must never leave its column."""

    def _painted_columns(self, text: str, width: int) -> tuple[int, int]:
        image = Image.new("RGB", (400, 24), layout_sheet.BACKGROUND)
        draw = ImageDraw.Draw(image)
        layout_sheet._draw_caption(draw, text, width / 2, 4, width)
        painted = [
            x
            for x in range(400)
            for y in range(24)
            if image.getpixel((x, y)) != (16, 16, 20)
        ]
        return (min(painted), max(painted)) if painted else (0, 0)

    def test_a_long_caption_stays_within_its_width(self) -> None:
        _left, right = self._painted_columns("Countdown timer", 102)

        self.assertLessEqual(right, 102)

    def test_a_shortened_caption_says_so(self) -> None:
        """Cutting mid-word with no ellipsis reads like the real action name."""
        image = Image.new("RGB", (400, 24), layout_sheet.BACKGROUND)
        draw = ImageDraw.Draw(image)
        font = layout_sheet._font(layout_sheet.CAPTION_SIZE)
        long_text = "Countdown timer"
        self.assertGreater(draw.textlength(long_text, font=font), 102)

        _left, right = self._painted_columns(long_text, 102)
        plain = draw.textlength(long_text[: len(long_text) - 3], font=font)

        # An ellipsis was added, so the drawn width is not simply a prefix.
        self.assertNotAlmostEqual(right, plain, delta=1)

    def test_a_short_caption_is_left_alone(self) -> None:
        left, right = self._painted_columns("Ok", 102)

        self.assertGreater(left, 0)
        self.assertLess(right, 102)


class SectionTests(unittest.TestCase):
    def test_every_page_becomes_a_section(self) -> None:
        profile = _profile()

        titles = [title for title, _keys, _reserved in layout_sheet._sections(profile)]

        self.assertEqual(titles, ["Live", "BRB"])

    def test_a_folder_becomes_its_own_section_under_its_page(self) -> None:
        profile = _profile()
        folder = KeyConfig(kind=KIND_FOLDER, label="Scenes", folder=Folder())
        profile.pages[0].set_key(7, folder)

        titles = [title for title, _keys, _reserved in layout_sheet._sections(profile)]

        self.assertEqual(titles, ["Live", "Live › Scenes", "BRB"])

    def test_nested_folders_are_reached_too(self) -> None:
        profile = _profile()
        inner = KeyConfig(kind=KIND_FOLDER, label="Cameras", folder=Folder())
        outer = KeyConfig(kind=KIND_FOLDER, label="Scenes", folder=Folder())
        outer.folder.set_key(3, inner)
        profile.pages[0].set_key(7, outer)

        titles = [title for title, _keys, _reserved in layout_sheet._sections(profile)]

        self.assertIn("Live › Scenes › Cameras", titles)

    def test_only_a_folder_section_reserves_the_back_slot(self) -> None:
        profile = _profile()
        profile.pages[0].set_key(
            7, KeyConfig(kind=KIND_FOLDER, label="Scenes", folder=Folder())
        )

        reserved = {
            title: slot for title, _keys, slot in layout_sheet._sections(profile)
        }

        self.assertEqual(reserved["Live"], -1)
        self.assertEqual(reserved["Live › Scenes"], 0)


class SheetTests(unittest.TestCase):
    def test_the_sheet_has_the_shape_of_the_connected_deck(self) -> None:
        """A Mini is three columns wide; an XL is eight."""
        profile = _profile()

        mini = layout_sheet.profile_sheet(profile, 3, 2)
        xl = layout_sheet.profile_sheet(profile, 8, 4)

        self.assertLess(mini.width, xl.width)
        self.assertLess(mini.height, xl.height)

    def test_a_folder_makes_the_sheet_taller(self) -> None:
        profile = _profile()
        without = layout_sheet.profile_sheet(profile, 5, 3).height

        profile.pages[0].set_key(
            7, KeyConfig(kind=KIND_FOLDER, label="Scenes", folder=Folder())
        )
        with_folder = layout_sheet.profile_sheet(profile, 5, 3).height

        self.assertGreater(with_folder, without)

    def test_a_configured_key_is_actually_drawn(self) -> None:
        profile = _profile()
        blank = layout_sheet.profile_sheet(profile, 5, 3)

        profile.pages[0].set_key(
            0,
            KeyConfig(
                kind=KIND_SINGLE, action="obs.record", label="REC",
                bg_color="#7a1020",
            ),
        )
        filled = layout_sheet.profile_sheet(profile, 5, 3)

        self.assertEqual(blank.size, filled.size)
        self.assertNotEqual(blank.tobytes(), filled.tobytes())

    def test_a_degenerate_geometry_still_renders(self) -> None:
        """Nothing here may divide by a column count of zero."""
        sheet = layout_sheet.profile_sheet(_profile(), 0, 0)

        self.assertGreater(sheet.width, 0)
        self.assertGreater(sheet.height, 0)

    def test_the_sheet_is_written_to_disk(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "nested" / "sheet.png"

            written = layout_sheet.save_profile_sheet(_profile(), 5, 3, target)

            self.assertTrue(written.is_file())
            with Image.open(written) as saved:
                self.assertEqual(saved.format, "PNG")


if __name__ == "__main__":
    unittest.main()
