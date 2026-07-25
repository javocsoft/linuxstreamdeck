from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from linuxstreamdeck.core.config import (
    CONFIG_DIR,
    EXPORT_AUDIO_PREFIX,
    EXPORT_ICON_PREFIX,
    EXPORT_MANIFEST_FILE,
    KEY_EXPORT_FILE,
    KEY_EXPORT_FORMAT,
    KEY_EXPORT_VERSION,
    KIND_MULTI,
    KIND_SINGLE,
    KIND_TOGGLE,
    ActionStep,
    Config,
    KeyConfig,
)


class KeyBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.icon = self.root / "icon.png"
        self.icon.write_bytes(b"portable icon")
        self.audio = self.root / "beep.mp3"
        self.audio.write_bytes(b"portable audio")
        self.destination = self.root / "key.lsdkey"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_bundle(self, key_payload: dict, members: dict | None = None) -> Path:
        path = self.root / "crafted.lsdkey"
        with zipfile.ZipFile(path, mode="w") as archive:
            archive.writestr(
                EXPORT_MANIFEST_FILE,
                json.dumps(
                    {"format": KEY_EXPORT_FORMAT, "version": KEY_EXPORT_VERSION}
                ),
            )
            archive.writestr(KEY_EXPORT_FILE, json.dumps(key_payload))
            for name, data in (members or {}).items():
                archive.writestr(name, data)
        return path

    # ---------- export ----------

    def test_export_writes_a_manifest_and_the_key(self) -> None:
        key = KeyConfig(kind=KIND_SINGLE, action="sys.wait", label="Wait")

        result = Config.export_key_bundle(key, self.destination)

        self.assertEqual(result.bundled_icons, 0)
        self.assertEqual(result.bundled_audio, 0)
        with zipfile.ZipFile(self.destination) as archive:
            manifest = json.loads(archive.read(EXPORT_MANIFEST_FILE))
            self.assertEqual(manifest["format"], KEY_EXPORT_FORMAT)
            self.assertEqual(manifest["version"], KEY_EXPORT_VERSION)
            self.assertEqual(
                json.loads(archive.read(KEY_EXPORT_FILE))["label"], "Wait"
            )

    def test_export_bundles_custom_icons_and_audio(self) -> None:
        key = KeyConfig(
            kind=KIND_MULTI,
            steps=[
                ActionStep(action="sys.audio", params={"file": str(self.audio)}),
            ],
            icon=str(self.icon),
        )

        result = Config.export_key_bundle(key, self.destination)

        self.assertEqual(result.bundled_icons, 1)
        self.assertEqual(result.bundled_audio, 1)
        self.assertEqual(result.missing_icons, 0)
        self.assertEqual(result.missing_audio, 0)
        with zipfile.ZipFile(self.destination) as archive:
            raw = json.loads(archive.read(KEY_EXPORT_FILE))
            self.assertTrue(raw["icon"].startswith(EXPORT_ICON_PREFIX))
            self.assertTrue(
                raw["steps"][0]["params"]["file"].startswith(EXPORT_AUDIO_PREFIX)
            )

    def test_export_keeps_built_in_icons_as_references(self) -> None:
        key = KeyConfig(kind=KIND_SINGLE, action="sys.wait", icon="mdi:timer-outline")

        Config.export_key_bundle(key, self.destination)

        with zipfile.ZipFile(self.destination) as archive:
            raw = json.loads(archive.read(KEY_EXPORT_FILE))
            self.assertEqual(raw["icon"], "mdi:timer-outline")
            self.assertEqual(
                [n for n in archive.namelist() if n.startswith("icons/")], []
            )

    def test_export_reports_a_missing_icon_without_failing(self) -> None:
        key = KeyConfig(
            kind=KIND_SINGLE, action="sys.wait", icon=str(self.root / "gone.png")
        )

        result = Config.export_key_bundle(key, self.destination)

        self.assertEqual(result.bundled_icons, 0)
        self.assertEqual(result.missing_icons, 1)
        self.assertTrue(self.destination.is_file())

    def test_export_deduplicates_audio_across_actions(self) -> None:
        key = KeyConfig(
            kind=KIND_MULTI,
            steps=[
                ActionStep(action="sys.audio", params={"file": str(self.audio)}),
                ActionStep(
                    action="sys.timer",
                    params={"duration": "00:30", "sound": str(self.audio)},
                ),
            ],
        )

        result = Config.export_key_bundle(key, self.destination)

        self.assertEqual(result.bundled_audio, 1)
        with zipfile.ZipFile(self.destination) as archive:
            self.assertEqual(
                len([n for n in archive.namelist() if n.startswith("audio/")]), 1
            )

    # ---------- import ----------

    def test_round_trip_preserves_the_key(self) -> None:
        key = KeyConfig(
            kind=KIND_TOGGLE,
            steps_on=[ActionStep(action="sys.wait", params={"duration": "00:05"})],
            steps_off=[ActionStep(action="sys.wait", params={"duration": "00:02"})],
            label="Air horn",
            bg_color="#334455",
            font_size="l",
            label_off="Off",
            font_size_off="xs",
        )
        Config.export_key_bundle(key, self.destination)

        imported = Config.import_key_bundle(self.destination)

        self.assertEqual(imported.key, key)

    def test_import_restores_assets_below_the_config_directory(self) -> None:
        key = KeyConfig(
            kind=KIND_MULTI,
            steps=[ActionStep(action="sys.audio", params={"file": str(self.audio)})],
            icon=str(self.icon),
        )
        Config.export_key_bundle(key, self.destination)

        imported = Config.import_key_bundle(self.destination)

        self.assertEqual(imported.restored_icons, 1)
        self.assertEqual(imported.restored_audio, 1)
        restored_icon = Path(imported.key.icon)
        restored_audio = Path(imported.key.steps[0].params["file"])
        self.assertTrue(
            restored_icon.is_relative_to(CONFIG_DIR / "imported-icons")
        )
        self.assertTrue(
            restored_audio.is_relative_to(CONFIG_DIR / "imported-audio")
        )
        self.assertEqual(restored_icon.read_bytes(), b"portable icon")
        self.assertEqual(restored_audio.read_bytes(), b"portable audio")

    def test_import_rejects_a_full_configuration_export(self) -> None:
        bundle = self.root / "full.lsdconfig"
        Config().export_bundle(bundle)

        with self.assertRaises(ValueError):
            Config.import_key_bundle(bundle)

    def test_import_rejects_a_file_that_is_not_an_archive(self) -> None:
        plain = self.root / "plain.lsdkey"
        plain.write_text("not a zip", encoding="utf-8")

        with self.assertRaises(ValueError):
            Config.import_key_bundle(plain)

    def test_import_rejects_an_unsupported_version(self) -> None:
        path = self.root / "future.lsdkey"
        with zipfile.ZipFile(path, mode="w") as archive:
            archive.writestr(
                EXPORT_MANIFEST_FILE,
                json.dumps({"format": KEY_EXPORT_FORMAT, "version": 99}),
            )
            archive.writestr(KEY_EXPORT_FILE, json.dumps({"kind": KIND_SINGLE}))

        with self.assertRaises(ValueError):
            Config.import_key_bundle(path)

    def test_import_rejects_an_icon_path_outside_the_archive(self) -> None:
        path = self._write_bundle(
            {
                "kind": KIND_SINGLE,
                "action": "sys.wait",
                "icon": f"{EXPORT_ICON_PREFIX}../../escaped.png",
            },
            {"../../escaped.png": b"escaped"},
        )

        with self.assertRaises(ValueError):
            Config.import_key_bundle(path)

    def test_import_rejects_an_unsupported_audio_extension(self) -> None:
        path = self._write_bundle(
            {
                "kind": KIND_SINGLE,
                "action": "sys.audio",
                "params": {"file": f"{EXPORT_AUDIO_PREFIX}audio/payload.sh"},
            },
            {"audio/payload.sh": b"#!/bin/sh"},
        )

        with self.assertRaises(ValueError):
            Config.import_key_bundle(path)

    def test_import_rejects_a_missing_key_document(self) -> None:
        path = self.root / "empty.lsdkey"
        with zipfile.ZipFile(path, mode="w") as archive:
            archive.writestr(
                EXPORT_MANIFEST_FILE,
                json.dumps(
                    {"format": KEY_EXPORT_FORMAT, "version": KEY_EXPORT_VERSION}
                ),
            )

        with self.assertRaises(ValueError):
            Config.import_key_bundle(path)

    def test_import_migrates_a_legacy_page_action(self) -> None:
        path = self._write_bundle(
            {
                "kind": KIND_SINGLE,
                "action": "nav.page",
                "params": {"mode": "next"},
            }
        )

        imported = Config.import_key_bundle(path)

        self.assertEqual(imported.key.action, "nav.page.next")
        self.assertEqual(imported.key.params, {})

    def test_exporting_does_not_modify_the_source_key(self) -> None:
        key = KeyConfig(
            kind=KIND_MULTI,
            steps=[ActionStep(action="sys.audio", params={"file": str(self.audio)})],
            icon=str(self.icon),
        )

        Config.export_key_bundle(key, self.destination)

        self.assertEqual(key.icon, str(self.icon))
        self.assertEqual(key.steps[0].params["file"], str(self.audio))


if __name__ == "__main__":
    unittest.main()
