from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageChops

from linuxstreamdeck.core.config import (
    EXIT_DISPLAY_BLANK,
    EXIT_DISPLAY_CUSTOM,
    EXIT_DISPLAY_DEFAULT,
    EXPORT_CONFIG_FILE,
    EXPORT_EXIT_IMAGE_PREFIX,
    EXPORT_MANIFEST_FILE,
    Config,
)
from linuxstreamdeck.core.events import EventBus
from linuxstreamdeck.device.exit_display import (
    blank_exit_tiles,
    exit_image_tiles,
    validate_exit_image,
)
from linuxstreamdeck.device.manager import DeckManager


class ExitDisplayConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.image = self.root / "after-exit.png"
        Image.new("RGB", (40, 24), "#2389da").save(self.image)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_settings_round_trip_and_invalid_mode_falls_back(self) -> None:
        configured = Config.from_dict(
            {
                "exit_display": {
                    "mode": EXIT_DISPLAY_CUSTOM,
                    "image_path": str(self.image),
                }
            }
        )
        restored = Config.from_dict(configured._serializable_dict())
        invalid = Config.from_dict(
            {"exit_display": {"mode": "unknown", "image_path": ""}}
        )

        self.assertEqual(restored.exit_display.mode, EXIT_DISPLAY_CUSTOM)
        self.assertEqual(restored.exit_display.image_path, str(self.image))
        self.assertEqual(invalid.exit_display.mode, EXIT_DISPLAY_DEFAULT)

    def test_export_bundles_and_import_restores_the_exit_image(self) -> None:
        source = Config()
        source.exit_display.mode = EXIT_DISPLAY_CUSTOM
        source.exit_display.image_path = str(self.image)
        bundle = self.root / "configuration.lsdconfig"

        exported = source.export_bundle(bundle)

        self.assertTrue(exported.bundled_exit_image)
        self.assertFalse(exported.missing_exit_image)
        with zipfile.ZipFile(bundle) as archive:
            raw = json.loads(archive.read(EXPORT_CONFIG_FILE))
            bundled_ref = raw["exit_display"]["image_path"]
            self.assertTrue(bundled_ref.startswith(EXPORT_EXIT_IMAGE_PREFIX))

        config_dir = self.root / "restored-config"
        with (
            patch("linuxstreamdeck.core.config.CONFIG_DIR", config_dir),
            patch(
                "linuxstreamdeck.core.config.CONFIG_FILE",
                config_dir / "config.json",
            ),
            patch(
                "linuxstreamdeck.core.config.BACKUP_FILE",
                config_dir / "config.json.bak",
            ),
        ):
            restored = Config()
            imported = restored.import_bundle(bundle)

        restored_path = Path(restored.exit_display.image_path)
        self.assertTrue(imported.restored_exit_image)
        self.assertTrue(restored_path.is_file())
        self.assertEqual(restored_path.read_bytes(), self.image.read_bytes())

    def test_export_reports_a_missing_exit_image(self) -> None:
        source = Config()
        source.exit_display.mode = EXIT_DISPLAY_CUSTOM
        source.exit_display.image_path = str(self.root / "missing.png")

        exported = source.export_bundle(self.root / "missing.lsdconfig")

        self.assertFalse(exported.bundled_exit_image)
        self.assertTrue(exported.missing_exit_image)

    def test_import_rejects_an_exit_image_outside_its_bundle_directory(
        self,
    ) -> None:
        source = Config()
        source.exit_display.mode = EXIT_DISPLAY_CUSTOM
        source.exit_display.image_path = str(self.image)
        original = self.root / "original.lsdconfig"
        malicious = self.root / "malicious.lsdconfig"
        source.export_bundle(original)
        with zipfile.ZipFile(original) as archive:
            manifest = archive.read(EXPORT_MANIFEST_FILE)
            raw = json.loads(archive.read(EXPORT_CONFIG_FILE))
        raw["exit_display"]["image_path"] = (
            f"{EXPORT_EXIT_IMAGE_PREFIX}../outside.png"
        )
        with zipfile.ZipFile(malicious, "w") as archive:
            archive.writestr(EXPORT_MANIFEST_FILE, manifest)
            archive.writestr(EXPORT_CONFIG_FILE, json.dumps(raw))

        with self.assertRaisesRegex(ValueError, "invalid exit image path"):
            Config().import_bundle(malicious)


class ExitDisplayImageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_custom_image_is_split_across_the_complete_grid(self) -> None:
        source = Image.new("RGB", (20, 10), "red")
        source.paste(Image.new("RGB", (10, 10), "blue"), (10, 0))
        path = self.root / "split.png"
        source.save(path)

        tiles = exit_image_tiles(path, 2, (10, 10), columns=2)

        self.assertEqual(len(tiles), 2)
        self.assertEqual(tiles[0].getpixel((5, 5)), (255, 0, 0))
        self.assertEqual(tiles[1].getpixel((5, 5)), (0, 0, 255))

    def test_blank_mode_produces_only_black_key_images(self) -> None:
        tiles = blank_exit_tiles(3, (8, 6))
        black = Image.new("RGB", (8, 6), "black")

        self.assertEqual(len(tiles), 3)
        self.assertTrue(
            all(
                ImageChops.difference(tile, black).getbbox() is None
                for tile in tiles
            )
        )

    def test_validation_rejects_missing_and_unsupported_images(self) -> None:
        text = self.root / "not-an-image.txt"
        text.write_text("not an image", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "supported image"):
            validate_exit_image(text)
        with self.assertRaisesRegex(ValueError, "does not exist"):
            validate_exit_image(self.root / "missing.png")


class ExitDisplayRuntimeTests(unittest.TestCase):
    class FakeDeck:
        def __init__(self) -> None:
            self.callbacks = []
            self.reset_count = 0
            self.close_count = 0
            self.brightness = []
            self.images = []
            self.read_thread = None

        def set_key_callback(self, callback) -> None:
            self.callbacks.append(callback)

        def reset(self) -> None:
            self.reset_count += 1

        def close(self) -> None:
            self.close_count += 1

        def set_brightness(self, value) -> None:
            self.brightness.append(value)

        def set_key_image(self, index, image) -> None:
            self.images.append((index, image))

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.manager = DeckManager(EventBus(), brightness=73)
        self.manager.key_count = 2
        self.manager.image_size = (8, 8)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _close_with(self, mode: str, image_path: str = ""):
        deck = self.FakeDeck()
        self.manager.deck = deck
        self.manager.configure_exit_display(mode, image_path)
        with patch(
            "StreamDeck.ImageHelpers.PILHelper.to_native_key_format",
            side_effect=lambda _deck, image: image.getpixel((0, 0)),
        ):
            self.manager._close(emit=False, apply_exit_display=True)
        return deck

    def test_device_default_uses_the_firmware_reset(self) -> None:
        deck = self._close_with(EXIT_DISPLAY_DEFAULT)

        self.assertEqual(deck.reset_count, 1)
        self.assertEqual(deck.images, [])
        self.assertEqual(deck.close_count, 1)

    def test_blank_writes_black_keys_and_turns_brightness_off(self) -> None:
        deck = self._close_with(EXIT_DISPLAY_BLANK)

        self.assertEqual(deck.reset_count, 0)
        self.assertEqual(deck.images, [(0, (0, 0, 0)), (1, (0, 0, 0))])
        self.assertEqual(deck.brightness, [0])
        self.assertEqual(deck.close_count, 1)

    def test_custom_image_is_left_visible_at_normal_brightness(self) -> None:
        image = self.root / "custom.png"
        Image.new("RGB", (16, 8), "#12a064").save(image)

        deck = self._close_with(EXIT_DISPLAY_CUSTOM, str(image))

        self.assertEqual(deck.reset_count, 0)
        self.assertEqual([index for index, _image in deck.images], [0, 1])
        self.assertEqual(deck.brightness, [73])
        self.assertEqual(deck.close_count, 1)

    def test_invalid_custom_image_falls_back_to_device_default(self) -> None:
        with self.assertLogs(
            "linuxstreamdeck.device.manager",
            level="WARNING",
        ):
            deck = self._close_with(
                EXIT_DISPLAY_CUSTOM,
                str(self.root / "missing.png"),
            )

        self.assertEqual(deck.reset_count, 1)
        self.assertEqual(deck.close_count, 1)


if __name__ == "__main__":
    unittest.main()
