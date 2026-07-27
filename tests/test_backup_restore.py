"""Rolling back to an automatic backup.

The rotating backups existed but could only be reached with a file manager,
which is half a safety net. Restoring replaces the whole configuration, so it
behaves exactly like importing a bundle: transient state is dropped, runtime
settings are reapplied, and the keyring credentials of this computer are left
alone. It also snapshots the current state first, so restoring the wrong copy
is recoverable.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from linuxstreamdeck import basic_actions as _basic_actions  # noqa: F401
from linuxstreamdeck.core import config as config_module
from linuxstreamdeck.core.config import (
    KIND_SINGLE,
    Config,
    KeyConfig,
    Page,
)
from linuxstreamdeck.core.controller import DeckController
from linuxstreamdeck.core.events import EventBus
from linuxstreamdeck.obs import actions as _obs_actions  # noqa: F401


class FakeDeck:
    key_count = 15
    image_size = (72, 72)
    columns = 5
    dial_count = 0

    def __init__(self) -> None:
        self.screensaver_active = False
        self.brightness = None

    def set_key_image(self, index, image) -> None:
        pass

    def record_activity(self) -> bool:
        return False

    def set_brightness(self, value) -> None:
        self.brightness = value

    def configure_screensaver(self, *_args) -> None:
        pass

    def configure_exit_display(self, *_args) -> None:
        pass


class BackupRestoreTests(unittest.TestCase):
    """`LSD_CONFIG_DIR` already points at a temporary directory for the suite."""

    def setUp(self) -> None:
        for path in config_module.Config.backup_history():
            path.unlink(missing_ok=True)
        self.config = Config()
        self.config.profile.name = "Original"
        self.config.pages[0].set_key(
            0, KeyConfig(kind=KIND_SINGLE, action="obs.record", label="GOOD")
        )
        self.config.save()
        Config.rotate_backups(force=True)
        self.backup = Config.backup_history()[0]

    def _wreck(self) -> None:
        self.config.pages[0].set_key(0, None)
        self.config.profile.name = "Wrecked"
        self.config.pages.append(Page(name="Junk"))
        self.config.save()

    # ---------- describing a backup ----------

    def test_a_backup_is_described_by_what_it_holds(self) -> None:
        """A filename alone cannot tell you which copy you want back."""
        info = Config.describe_backup(self.backup)

        self.assertTrue(info.readable)
        self.assertEqual((info.profiles, info.pages, info.keys), (1, 1, 1))
        self.assertIn("profile(s)", info.label())

    def test_the_label_carries_a_date(self) -> None:
        info = Config.describe_backup(self.backup)

        self.assertIsNotNone(info.when)
        self.assertIn(str(info.when.year), info.label())

    def test_an_unreadable_backup_is_reported_not_hidden(self) -> None:
        broken = self.backup.parent / "config-19990101-000000.json"
        broken.write_text("this is not json", encoding="utf-8")
        self.addCleanup(broken.unlink, True)

        info = Config.describe_backup(broken)

        self.assertFalse(info.readable)
        self.assertIn("unreadable", info.label())

    def test_a_missing_file_is_described_without_raising(self) -> None:
        info = Config.describe_backup(self.backup.parent / "gone.json")

        self.assertFalse(info.readable)

    # ---------- restoring ----------

    def test_restoring_brings_the_keys_back(self) -> None:
        self._wreck()

        self.config.restore_backup(self.backup)

        self.assertEqual(self.config.pages[0].key(0).label, "GOOD")

    def test_restoring_brings_the_profile_back(self) -> None:
        self._wreck()

        self.config.restore_backup(self.backup)

        self.assertEqual(self.config.profile.name, "Original")

    def test_pages_added_after_the_backup_are_gone(self) -> None:
        self._wreck()

        self.config.restore_backup(self.backup)

        self.assertEqual([p.name for p in self.config.pages], ["Page 1"])

    def test_the_file_on_disk_is_restored_too(self) -> None:
        """Not just the object in memory: the next launch must agree."""
        self._wreck()

        self.config.restore_backup(self.backup)

        raw = json.loads(config_module.CONFIG_FILE.read_text(encoding="utf-8"))
        self.assertEqual(raw["profiles"][0]["name"], "Original")

    def test_the_current_state_is_snapshotted_first(self) -> None:
        """Choosing the wrong copy must not be the end of the story."""
        self._wreck()
        before = len(Config.backup_history())

        self.config.restore_backup(self.backup)

        self.assertGreater(len(Config.backup_history()), before)

    def test_the_keyring_password_is_kept(self) -> None:
        """Credentials belong to this computer, never to the file."""
        self.config.obs.password = "local-secret"
        self._wreck()

        self.config.restore_backup(self.backup)

        self.assertEqual(self.config.obs.password, "local-secret")

    def test_a_backup_can_be_restored_and_then_undone(self) -> None:
        self._wreck()
        self.config.restore_backup(self.backup)

        newest = Config.backup_history()[0]
        self.config.restore_backup(newest)

        self.assertEqual(self.config.profile.name, "Wrecked")

    # ---------- refusing what it should ----------

    def test_a_path_outside_the_backup_directory_is_refused(self) -> None:
        """The dialog never offers one, so nothing else may reach the loader."""
        outside = config_module.CONFIG_DIR / "config.json"

        with self.assertRaises(ValueError):
            self.config.restore_backup(outside)

    def test_a_missing_backup_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.config.restore_backup(self.backup.parent / "gone.json")

    def test_an_unreadable_backup_is_refused(self) -> None:
        broken = self.backup.parent / "config-19990101-000000.json"
        broken.write_text("not json at all", encoding="utf-8")
        self.addCleanup(broken.unlink, True)

        with self.assertRaises(ValueError):
            self.config.restore_backup(broken)

    def test_a_refused_restore_changes_nothing(self) -> None:
        self._wreck()

        with self.assertRaises(ValueError):
            self.config.restore_backup(Path("/etc/hostname"))

        self.assertEqual(self.config.profile.name, "Wrecked")


class ControllerRestoreTests(unittest.TestCase):
    def setUp(self) -> None:
        for path in Config.backup_history():
            path.unlink(missing_ok=True)
        self.config = Config()
        self.config.brightness = 30
        self.config.pages[0].set_key(
            0, KeyConfig(kind=KIND_SINGLE, action="obs.record", label="GOOD")
        )
        self.config.save()
        Config.rotate_backups(force=True)
        self.backup = Config.backup_history()[0]
        self.deck = FakeDeck()
        self.obs = SimpleNamespace(
            connected=False,
            configure=lambda *a: None,
            reconnect_now=lambda: None,
        )
        self.controller = DeckController(
            self.config, EventBus(), self.obs, self.deck
        )
        self.addCleanup(self.controller.shutdown)

    def test_restoring_applies_the_backup_brightness_to_the_deck(self) -> None:
        self.config.brightness = 90
        self.config.save()

        self.controller.restore_backup(self.backup)

        self.assertEqual(self.deck.brightness, 30)

    def test_restoring_drops_the_undo_history(self) -> None:
        """Every stored index refers to keys that have just been replaced."""
        self.controller.clear_key(0)
        self.assertTrue(self.controller.can_undo())

        self.controller.restore_backup(self.backup)

        self.assertFalse(self.controller.can_undo())

    def test_restoring_returns_to_the_page_root(self) -> None:
        self.controller.restore_backup(self.backup)

        self.assertEqual(self.controller.folder_path, ())

    def test_the_restored_keys_are_live(self) -> None:
        self.controller.clear_key(0)

        self.controller.restore_backup(self.backup)

        self.assertEqual(self.controller.container.key(0).label, "GOOD")


if __name__ == "__main__":
    unittest.main()
