"""Folder keys: model, navigation, key-scoped state and portable bundles."""

from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from linuxstreamdeck import basic_actions as _basic_actions  # noqa: F401
from linuxstreamdeck.core.config import (
    DEFAULT_FOLDER_ICON,
    DEFAULT_FOLDER_NAME,
    EXPORT_AUDIO_PREFIX,
    EXPORT_ICON_PREFIX,
    EXPORT_MANIFEST_FILE,
    FOLDER_BACK_INDEX,
    KEY_EXPORT_FILE,
    KEY_EXPORT_FORMAT,
    KIND_FOLDER,
    KIND_MULTI,
    KIND_SINGLE,
    MAX_FOLDER_DEPTH,
    ActionStep,
    Config,
    Folder,
    KeyConfig,
    Page,
    folder_depth,
)
from linuxstreamdeck.core.controller import (
    FOLDER_BACK_ICON,
    DeckController,
)
from linuxstreamdeck.core.events import EventBus


def folder_key(label: str = "Scenes", **keys: KeyConfig) -> KeyConfig:
    """A folder key holding the given keys, addressed by index name ("k3")."""
    return KeyConfig(
        kind=KIND_FOLDER,
        label=label,
        folder=Folder(
            keys={name.removeprefix("k"): value for name, value in keys.items()}
        ),
    )


def action_key(action: str = "sys.url", **params) -> KeyConfig:
    return KeyConfig(kind=KIND_SINGLE, action=action, params=params or {"url": "x"})


class FolderModelTests(unittest.TestCase):
    def test_a_folder_survives_a_serialization_round_trip(self) -> None:
        page = Page(name="Main")
        page.set_key(3, folder_key("Scenes", k2=action_key(url="https://a")))

        restored = Page.from_dict(json.loads(json.dumps(_as_dict(page))))

        folder = restored.key(3)
        self.assertEqual(folder.kind, KIND_FOLDER)
        self.assertEqual(folder.label, "Scenes")
        self.assertEqual(folder.contents.key(2).params["url"], "https://a")

    def test_an_empty_folder_is_still_a_configured_key(self) -> None:
        key = KeyConfig(kind=KIND_FOLDER, folder=Folder())
        self.assertFalse(key.is_empty())
        self.assertEqual(key.folder_name(), DEFAULT_FOLDER_NAME)
        self.assertEqual(folder_key("Audio").folder_name(), "Audio")

    def test_only_a_folder_key_exposes_contents(self) -> None:
        self.assertIsNone(action_key().contents)
        self.assertIsNotNone(folder_key().contents)
        # A non-folder key never keeps stray folder data when it is loaded.
        loaded = KeyConfig.from_dict(
            {"kind": KIND_SINGLE, "action": "sys.url", "folder": {"keys": {}}}
        )
        self.assertIsNone(loaded.folder)

    def test_folder_contents_are_excluded_from_key_equality(self) -> None:
        """The editor's dirty check must not see edits made inside a folder."""
        first = folder_key("Scenes")
        second = folder_key("Scenes", k1=action_key())
        self.assertEqual(first, second)
        second.label = "Renamed"
        self.assertNotEqual(first, second)

    def test_the_reserved_back_slot_is_never_loaded(self) -> None:
        loaded = KeyConfig.from_dict(
            {
                "kind": KIND_FOLDER,
                "folder": {
                    "keys": {
                        str(FOLDER_BACK_INDEX): {"kind": KIND_SINGLE, "action": "sys.url"},
                        "4": {"kind": KIND_SINGLE, "action": "sys.url"},
                    }
                },
            }
        )
        self.assertEqual(sorted(loaded.contents.keys), ["4"])

    def test_nesting_deeper_than_the_limit_drops_only_its_contents(self) -> None:
        payload: dict = {"kind": KIND_SINGLE, "action": "sys.url"}
        for _ in range(MAX_FOLDER_DEPTH + 2):
            payload = {"kind": KIND_FOLDER, "folder": {"keys": {"1": payload}}}

        loaded = KeyConfig.from_dict(payload)

        self.assertEqual(loaded.kind, KIND_FOLDER)
        self.assertEqual(folder_depth(loaded), MAX_FOLDER_DEPTH)

    def test_folder_depth_counts_the_deepest_branch(self) -> None:
        self.assertEqual(folder_depth(action_key()), 0)
        self.assertEqual(folder_depth(folder_key()), 1)
        self.assertEqual(
            folder_depth(folder_key("Outer", k1=folder_key("Inner"))), 2
        )

    def test_a_folder_clone_is_independent(self) -> None:
        original = folder_key("Scenes", k1=action_key(url="https://a"))
        copy = original.clone()
        copy.contents.key(1).params["url"] = "https://b"
        self.assertEqual(original.contents.key(1).params["url"], "https://a")


def _as_dict(page: Page) -> dict:
    from dataclasses import asdict

    return asdict(page)


class FakeDeck:
    key_count = 15
    image_size = (72, 72)

    def __init__(self) -> None:
        self.images = {}
        self.screensaver_active = False

    def set_key_image(self, index, image) -> None:
        self.images[index] = image

    def record_activity(self) -> bool:
        return self.screensaver_active

    def set_brightness(self, _value) -> None:
        pass

    def configure_screensaver(self, *_args) -> None:
        pass


class FolderNavigationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = Config()
        self.bus = EventBus()
        self.deck = FakeDeck()
        self.controller = DeckController(
            self.config, self.bus, SimpleNamespace(connected=False), self.deck
        )
        self.page = self.config.pages[0]
        self.page.set_key(
            3, folder_key("Scenes", k7=action_key(url="https://inside"))
        )

    def tearDown(self) -> None:
        self.controller.shutdown()

    def test_the_container_follows_the_open_folder(self) -> None:
        self.assertIs(self.controller.container, self.page)

        self.assertTrue(self.controller.open_folder(3))

        self.assertEqual(self.controller.folder_path, (3,))
        self.assertIs(self.controller.container, self.page.key(3).contents)
        self.assertEqual(
            self.controller.container.key(7).params["url"], "https://inside"
        )

    def test_opening_a_key_that_is_not_a_folder_does_nothing(self) -> None:
        self.page.set_key(4, action_key())
        self.assertFalse(self.controller.open_folder(4))
        self.assertEqual(self.controller.folder_path, ())

    def test_pressing_a_folder_key_opens_it_without_an_action_worker(self) -> None:
        with patch.object(self.controller._action_executor, "submit") as submit:
            self.controller.press(3)

        submit.assert_not_called()
        self.assertEqual(self.controller.folder_path, (3,))

    def test_the_reserved_key_leaves_the_folder(self) -> None:
        self.controller.open_folder(3)
        self.assertTrue(self.controller.is_reserved_key(FOLDER_BACK_INDEX))

        self.controller.press(FOLDER_BACK_INDEX)

        self.assertEqual(self.controller.folder_path, ())
        self.assertFalse(self.controller.is_reserved_key(FOLDER_BACK_INDEX))

    def test_a_physical_press_opens_and_leaves_a_folder(self) -> None:
        """Both edges of a physical press, as the deck read thread sends them."""
        self.bus.emit("deck.key", index=3, pressed=True)
        self.bus.emit("deck.key", index=3, pressed=False)
        self.assertEqual(self.controller.folder_path, (3,))

        self.bus.emit("deck.key", index=FOLDER_BACK_INDEX, pressed=True)
        self.bus.emit("deck.key", index=FOLDER_BACK_INDEX, pressed=False)
        self.assertEqual(self.controller.folder_path, ())

    def test_a_screen_saver_wake_press_does_not_open_a_folder(self) -> None:
        self.deck.screensaver_active = True
        self.controller.press(3)
        self.assertEqual(self.controller.folder_path, ())

    def test_the_reserved_key_only_exists_inside_a_folder(self) -> None:
        self.assertFalse(self.controller.is_reserved_key(FOLDER_BACK_INDEX))
        self.assertFalse(self.controller.close_folder())

    def test_navigation_is_transient_and_never_saved(self) -> None:
        with patch.object(self.config, "save") as save:
            self.controller.open_folder(3)
            self.controller.close_folder()
        save.assert_not_called()

    def test_opening_a_folder_announces_its_trail(self) -> None:
        seen = []
        self.bus.subscribe("folder.changed", lambda _t, d: seen.append(d))

        self.controller.open_folder(3)

        self.assertEqual(seen[-1]["path"], (3,))
        self.assertEqual(seen[-1]["trail"], [((3,), "Scenes")])

    def test_nested_folders_build_the_whole_trail(self) -> None:
        inner = folder_key("Audio", k5=action_key())
        self.page.set_key(3, folder_key("Scenes", k2=inner))

        self.controller.open_folder(3)
        self.controller.open_folder(2)

        self.assertEqual(
            self.controller.folder_trail(),
            [((3,), "Scenes"), ((3, 2), "Audio")],
        )

    def test_a_page_change_always_returns_to_the_page_root(self) -> None:
        self.config.pages.append(Page(name="Second"))
        self.controller.open_folder(3)

        self.controller.set_page(1)

        self.assertEqual(self.controller.folder_path, ())
        self.assertIs(self.controller.container, self.config.pages[1])

    def test_a_profile_change_always_returns_to_the_page_root(self) -> None:
        self.controller.add_profile("Other")
        self.controller.set_profile(0)
        self.controller.open_folder(3)

        self.controller.set_profile(1)

        self.assertEqual(self.controller.folder_path, ())

    def test_a_path_that_stops_resolving_falls_back_to_the_page(self) -> None:
        self.controller.open_folder(3)
        # Something replaced the folder key behind the controller's back.
        self.page.set_key(3, action_key())

        self.assertIs(self.controller.container, self.page)
        self.assertEqual(self.controller.folder_path, ())

    def test_set_folder_path_stops_at_the_first_unusable_step(self) -> None:
        self.controller.set_folder_path((3, 9))
        self.assertEqual(self.controller.folder_path, (3,))

    def test_depth_limits_reject_folders_that_do_not_fit(self) -> None:
        deepest = action_key()
        for _ in range(MAX_FOLDER_DEPTH):
            deepest = folder_key("Nested", k1=deepest)

        self.assertTrue(self.controller.can_add_folder())
        self.assertTrue(self.controller.fits_here(deepest))
        self.assertTrue(self.controller.fits_here(None))

        self.controller.open_folder(3)

        self.assertFalse(self.controller.fits_here(deepest))
        self.assertTrue(self.controller.fits_here(action_key()))


class FolderKeyStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = Config()
        self.bus = EventBus()
        self.deck = FakeDeck()
        self.controller = DeckController(
            self.config, self.bus, SimpleNamespace(connected=False), self.deck
        )
        self.page = self.config.pages[0]
        self.page.set_key(3, folder_key("Scenes", k7=action_key()))

    def tearDown(self) -> None:
        self.controller.shutdown()

    def test_the_same_index_is_a_different_key_inside_a_folder(self) -> None:
        outside = self.controller._tkey(7)
        self.controller.open_folder(3)
        inside = self.controller._tkey(7)

        self.assertNotEqual(outside, inside)
        self.assertEqual(outside[2], ())
        self.assertEqual(inside[2], (3,))

    def test_toggle_state_does_not_leak_between_a_page_and_its_folder(self) -> None:
        self.controller._toggle[self.controller._tkey(7)] = True
        self.controller.open_folder(3)
        self.assertFalse(self.controller.toggle_state(7))

    def test_clearing_a_folder_discards_the_state_inside_it(self) -> None:
        self.controller.open_folder(3)
        inside = self.controller._tkey(7)
        self.controller.toggle_countdown(inside, 30, "", 100)
        self.controller._toggle[inside] = True
        self.assertTrue(
            self.controller.countdown_snapshot(inside, 30).running
        )

        self.controller.close_folder()
        self.controller.clear_key(3)

        self.assertFalse(
            self.controller.countdown_snapshot(inside, 30).running
        )
        self.assertNotIn(inside, self.controller._toggle)

    def test_moving_a_folder_discards_the_state_inside_it(self) -> None:
        self.controller.open_folder(3)
        inside = self.controller._tkey(7)
        self.controller.toggle_countdown(inside, 30, "", 100)
        self.controller.close_folder()

        self.controller.swap_keys(3, 8)

        self.assertIs(self.page.key(8).contents.key(7).kind, KIND_SINGLE)
        self.assertFalse(
            self.controller.countdown_snapshot(inside, 30).running
        )

    def test_editing_a_folder_key_keeps_the_state_inside_it(self) -> None:
        """Renaming a folder must not reset the clocks of its keys."""
        self.controller.open_folder(3)
        inside = self.controller._tkey(7)
        self.controller.toggle_countdown(inside, 30, "", 100)
        self.controller.close_folder()

        self.controller.key_config_changed(3)

        self.assertTrue(
            self.controller.countdown_snapshot(inside, 30).running
        )

    def test_the_reserved_slot_refuses_every_key_operation(self) -> None:
        self.controller.open_folder(3)
        self.controller.paste_key(FOLDER_BACK_INDEX, action_key())
        self.controller.swap_keys(FOLDER_BACK_INDEX, 7)

        self.assertIsNone(self.controller.container.key(FOLDER_BACK_INDEX))
        self.assertEqual(self.controller.container.key(7).kind, KIND_SINGLE)


class FolderRenderingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = Config()
        self.bus = EventBus()
        self.deck = FakeDeck()
        self.controller = DeckController(
            self.config, self.bus, SimpleNamespace(connected=False), self.deck
        )
        self.page = self.config.pages[0]

    def tearDown(self) -> None:
        self.controller.shutdown()

    def test_a_folder_key_shows_its_icon_and_how_many_keys_it_holds(self) -> None:
        key = folder_key("Scenes", k1=action_key(), k2=action_key())
        spec = self.controller._key_spec(3, key, self.deck.image_size)

        self.assertEqual(spec["icon_path"], DEFAULT_FOLDER_ICON)
        self.assertEqual(spec["label"], "Scenes")
        self.assertEqual(spec["badge"], "2")

    def test_an_empty_folder_shows_no_count(self) -> None:
        spec = self.controller._key_spec(
            3, folder_key("Scenes"), self.deck.image_size
        )
        self.assertEqual(spec["badge"], "")

    def test_a_custom_folder_icon_wins(self) -> None:
        key = folder_key("Scenes")
        key.icon = "mdi:movie"
        spec = self.controller._key_spec(3, key, self.deck.image_size)
        self.assertEqual(spec["icon_path"], "mdi:movie")

    def test_the_reserved_slot_renders_the_named_back_key(self) -> None:
        self.page.set_key(3, folder_key("Scenes", k7=action_key()))
        self.controller.open_folder(3)

        spec = self.controller._key_spec(
            FOLDER_BACK_INDEX,
            self.controller.container.key(FOLDER_BACK_INDEX),
            self.deck.image_size,
        )

        self.assertEqual(spec["icon_path"], FOLDER_BACK_ICON)
        self.assertEqual(spec["label"], "Scenes")

    def test_rendering_inside_a_folder_draws_its_own_keys(self) -> None:
        self.page.set_key(1, action_key())
        self.page.set_key(3, folder_key("Scenes", k7=action_key()))
        self.controller.open_folder(3)

        self.controller._render_keys(
            range(self.deck.key_count), self.controller._view()
        )

        # The page key at index 1 is not part of the folder, so its slot is
        # empty here, while the folder's own key 7 is drawn.
        empty = self.controller._key_spec(1, None, self.deck.image_size)
        self.assertEqual(
            self.controller._key_spec(
                1, self.controller.container.key(1), self.deck.image_size
            ),
            empty,
        )
        self.assertIsNotNone(self.controller.container.key(7))
        self.assertIn(7, self.deck.images)


class FolderPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.audio = self.root / "inside.mp3"
        self.audio.write_bytes(b"nested audio")
        self.icon = self.root / "inside.png"
        self.icon.write_bytes(b"nested icon")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _nested_key(self) -> KeyConfig:
        inner = KeyConfig(
            kind=KIND_MULTI,
            icon=str(self.icon),
            steps=[ActionStep(action="sys.audio", params={"file": str(self.audio)})],
        )
        return folder_key("Sounds", k4=inner)

    def test_a_key_bundle_carries_the_assets_inside_a_folder(self) -> None:
        destination = self.root / "folder.lsdkey"

        exported = Config.export_key_bundle(self._nested_key(), destination)

        self.assertEqual(exported.bundled_icons, 1)
        self.assertEqual(exported.bundled_audio, 1)
        with zipfile.ZipFile(destination) as archive:
            payload = json.loads(archive.read(KEY_EXPORT_FILE))
        nested = payload["folder"]["keys"]["4"]
        self.assertTrue(nested["icon"].startswith(EXPORT_ICON_PREFIX))
        self.assertTrue(
            nested["steps"][0]["params"]["file"].startswith(EXPORT_AUDIO_PREFIX)
        )

    def test_a_key_bundle_restores_the_assets_inside_a_folder(self) -> None:
        destination = self.root / "folder.lsdkey"
        Config.export_key_bundle(self._nested_key(), destination)

        imported = Config.import_key_bundle(destination)

        self.assertEqual(imported.restored_icons, 1)
        self.assertEqual(imported.restored_audio, 1)
        restored = imported.key.contents.key(4)
        self.assertTrue(Path(restored.icon).is_file())
        self.assertTrue(
            Path(restored.steps[0].params["file"]).is_file()
        )

    def test_export_does_not_mutate_the_folder_it_is_given(self) -> None:
        key = self._nested_key()
        Config.export_key_bundle(key, self.root / "folder.lsdkey")
        self.assertEqual(key.contents.key(4).icon, str(self.icon))

    def test_a_configuration_bundle_carries_folder_assets(self) -> None:
        config = Config()
        config.pages[0].set_key(2, self._nested_key())
        destination = self.root / "config.lsdconfig"

        exported = config.export_bundle(destination)

        self.assertEqual(exported.bundled_icons, 1)
        self.assertEqual(exported.bundled_audio, 1)

    def test_a_configuration_bundle_restores_folder_assets(self) -> None:
        config = Config()
        config.pages[0].set_key(2, self._nested_key())
        destination = self.root / "config.lsdconfig"
        config.export_bundle(destination)

        restored = Config()
        result = restored.import_bundle(destination)

        self.assertEqual(result.restored_icons, 1)
        self.assertEqual(result.restored_audio, 1)
        # Nested keys count towards the imported total as well.
        self.assertEqual(result.keys, 2)
        nested = restored.pages[0].key(2).contents.key(4)
        self.assertTrue(Path(nested.icon).is_file())
        self.assertTrue(Path(nested.steps[0].params["file"]).is_file())

    def test_key_configs_walks_into_folders(self) -> None:
        config = Config()
        config.pages[0].set_key(2, self._nested_key())
        self.assertEqual(len(list(config._key_configs())), 2)

    def test_a_version_one_key_bundle_still_imports(self) -> None:
        path = self.root / "old.lsdkey"
        with zipfile.ZipFile(path, mode="w") as archive:
            archive.writestr(
                EXPORT_MANIFEST_FILE,
                json.dumps({"format": KEY_EXPORT_FORMAT, "version": 1}),
            )
            archive.writestr(
                KEY_EXPORT_FILE,
                json.dumps({"kind": KIND_SINGLE, "action": "sys.url"}),
            )

        imported = Config.import_key_bundle(path)

        self.assertEqual(imported.key.action, "sys.url")


class FolderPageRenameTests(unittest.TestCase):
    def test_renaming_a_page_rewrites_targets_inside_folders(self) -> None:
        config = Config()
        bus = EventBus()
        deck = FakeDeck()
        controller = DeckController(config, bus, SimpleNamespace(connected=False), deck)
        self.addCleanup(controller.shutdown)
        config.pages.append(Page(name="Scenes"))
        inside = KeyConfig(
            kind=KIND_SINGLE, action="nav.page.go", params={"page": "Scenes"}
        )
        config.pages[0].set_key(1, folder_key("Group", k6=inside))

        config.current_page = 1
        controller.rename_page("Live")

        self.assertEqual(
            config.pages[0].key(1).contents.key(6).params["page"], "Live"
        )


class DoubleClickTests(unittest.TestCase):
    """Entering a folder from the virtual deck without the editor button.

    The window times the double click from GtkButton's own "clicked" signal: an
    extra Gtk.GestureClick on the same button never saw the second press,
    because GtkButton claims the primary sequence on the first one.
    """

    def setUp(self) -> None:
        from linuxstreamdeck.ui.window import (
            DOUBLE_CLICK_SECONDS,
            _completes_double_click,
        )

        self.completes = _completes_double_click
        self.window = DOUBLE_CLICK_SECONDS

    def test_the_first_click_on_a_key_is_never_a_double_click(self) -> None:
        self.assertFalse(self.completes(None, 3, 100.0))

    def test_two_quick_clicks_on_the_same_key_open_it(self) -> None:
        self.assertTrue(self.completes((3, 100.0), 3, 100.0 + self.window / 2))

    def test_a_slow_second_click_only_selects(self) -> None:
        self.assertFalse(self.completes((3, 100.0), 3, 100.0 + self.window + 0.01))

    def test_clicking_two_different_keys_quickly_opens_neither(self) -> None:
        self.assertFalse(self.completes((3, 100.0), 4, 100.01))


if __name__ == "__main__":
    unittest.main()
