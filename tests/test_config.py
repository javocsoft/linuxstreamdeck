from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from linuxstreamdeck.core.config import (
    EXPORT_AUDIO_PREFIX,
    EXPORT_CONFIG_FILE,
    EXPORT_MANIFEST_FILE,
    EXPORT_VERSION,
    KIND_SINGLE,
    Config,
    KeyConfig,
)


class PortableAudioTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.audio = self.root / "notification.mp3"
        self.audio.write_bytes(b"portable audio")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _config(self, audio_path: Path) -> Config:
        config = Config()
        config.pages[0].set_key(
            0,
            KeyConfig(
                kind=KIND_SINGLE,
                action="sys.audio",
                params={
                    "file": str(audio_path),
                    "volume": 80,
                    "duration": "",
                },
            ),
        )
        return config

    def test_export_bundles_audio_and_import_restores_a_local_copy(self) -> None:
        source = self._config(self.audio)
        bundle = self.root / "configuration.lsdconfig"

        exported = source.export_bundle(bundle)

        self.assertEqual(exported.bundled_audio, 1)
        self.assertEqual(exported.missing_audio, 0)
        self.assertEqual(source.pages[0].key(0).params["file"], str(self.audio))
        with zipfile.ZipFile(bundle) as archive:
            manifest = json.loads(archive.read(EXPORT_MANIFEST_FILE))
            raw = json.loads(archive.read(EXPORT_CONFIG_FILE))
            bundled_ref = raw["profiles"][0]["pages"][0]["keys"]["0"][
                "params"
            ]["file"]
            self.assertEqual(manifest["version"], EXPORT_VERSION)
            self.assertTrue(bundled_ref.startswith(EXPORT_AUDIO_PREFIX))
            archive_name = bundled_ref.removeprefix(EXPORT_AUDIO_PREFIX)
            self.assertEqual(archive.read(archive_name), b"portable audio")

        restored = Config()
        imported = restored.import_bundle(bundle)

        restored_path = Path(restored.pages[0].key(0).params["file"])
        self.assertEqual(imported.restored_audio, 1)
        self.assertTrue(restored_path.is_file())
        self.assertEqual(restored_path.read_bytes(), b"portable audio")

    def test_export_warns_when_an_audio_file_is_missing(self) -> None:
        source = self._config(self.root / "missing.mp3")

        exported = source.export_bundle(self.root / "missing.lsdconfig")

        self.assertEqual(exported.bundled_audio, 0)
        self.assertEqual(exported.missing_audio, 1)

    def test_timer_completion_sound_is_bundled_and_deduplicated(self) -> None:
        source = self._config(self.audio)
        source.pages[0].set_key(
            1,
            KeyConfig(
                kind=KIND_SINGLE,
                action="sys.timer",
                params={
                    "duration": "01:00",
                    "sound": str(self.audio),
                    "volume": 75,
                },
            ),
        )
        bundle = self.root / "timer-sound.lsdconfig"

        exported = source.export_bundle(bundle)

        self.assertEqual(exported.bundled_audio, 1)
        with zipfile.ZipFile(bundle) as archive:
            raw = json.loads(archive.read(EXPORT_CONFIG_FILE))
        keys = raw["profiles"][0]["pages"][0]["keys"]
        self.assertEqual(
            keys["0"]["params"]["file"],
            keys["1"]["params"]["sound"],
        )

        restored = Config()
        imported = restored.import_bundle(bundle)

        audio_path = restored.pages[0].key(0).params["file"]
        timer_path = restored.pages[0].key(1).params["sound"]
        self.assertEqual(imported.restored_audio, 1)
        self.assertEqual(audio_path, timer_path)
        self.assertTrue(Path(timer_path).is_file())

    def test_import_rejects_an_audio_path_outside_its_bundle_directory(
        self,
    ) -> None:
        source = self._config(self.audio)
        original = self.root / "original.lsdconfig"
        malicious = self.root / "malicious.lsdconfig"
        source.export_bundle(original)
        with zipfile.ZipFile(original) as archive:
            manifest = archive.read(EXPORT_MANIFEST_FILE)
            raw = json.loads(archive.read(EXPORT_CONFIG_FILE))
        raw["profiles"][0]["pages"][0]["keys"]["0"]["params"][
            "file"
        ] = f"{EXPORT_AUDIO_PREFIX}../outside.mp3"
        with zipfile.ZipFile(malicious, "w") as archive:
            archive.writestr(EXPORT_MANIFEST_FILE, manifest)
            archive.writestr(EXPORT_CONFIG_FILE, json.dumps(raw))

        with self.assertRaisesRegex(ValueError, "invalid audio path"):
            Config().import_bundle(malicious)


if __name__ == "__main__":
    unittest.main()
