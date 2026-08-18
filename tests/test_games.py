from __future__ import annotations

import random
import time
import unittest

from PIL import Image, ImageChops

from linuxstreamdeck.core.config import Config, GameSettings
from linuxstreamdeck.core.events import EventBus
from linuxstreamdeck.games.manager import GameManager
from linuxstreamdeck.games.mole_smash import (
    COUNTDOWN_SECONDS,
    GOLDEN_POINTS,
    NORMAL_POINTS,
    PHASE_COUNTDOWN,
    PHASE_PLAYING,
    PHASE_RESULTS,
    ROUND_SECONDS,
    WRONG_PENALTY,
    MoleSmashEngine,
    game_layout,
)
from linuxstreamdeck.games.render import MOLE_ASSET, render_keys, touchscreen_hud


class LayoutTests(unittest.TestCase):
    def test_every_supported_shape_keeps_controls_and_playable_holes(self) -> None:
        for keys, columns, expected in (
            (6, 3, 4),
            (8, 4, 6),
            (15, 5, 13),
            (32, 8, 30),
        ):
            with self.subTest(keys=keys):
                layout = game_layout(keys, columns)
                controls = {
                    layout.start_key,
                    layout.difficulty_key,
                    layout.sound_key,
                    layout.record_key,
                    layout.exit_key,
                }
                self.assertEqual(len(controls), 5)
                self.assertEqual(len(layout.playable), expected)
                self.assertTrue(all(0 <= index < keys for index in controls))

    def test_plus_uses_the_strip_and_keeps_all_eight_keys_playable(self) -> None:
        layout = game_layout(8, 4, touchscreen_hud=True)

        self.assertIsNone(layout.score_key)
        self.assertIsNone(layout.time_key)
        self.assertEqual(layout.playable, tuple(range(8)))
        self.assertEqual(layout.score_id, "4x2+lcd")


class EngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.layout = game_layout(15, 5)
        self.engine = MoleSmashEngine(
            self.layout,
            sound_enabled=False,
            rng=random.Random(8),
            now=0.0,
        )

    def _begin(self) -> float:
        self.engine.press(self.layout.start_key, 0.0)
        self.assertEqual(self.engine.phase, PHASE_COUNTDOWN)
        started = COUNTDOWN_SECONDS + 0.01
        self.engine.tick(started)
        self.engine.tick(started + 0.01)
        self.assertEqual(self.engine.phase, PHASE_PLAYING)
        return started + 0.01

    def test_a_visible_mole_scores_and_an_empty_hole_penalizes(self) -> None:
        now = self._begin()
        snapshot = self.engine.snapshot(now)
        target = snapshot.targets[0]

        events = self.engine.press(target.index, now + 0.01)
        self.assertEqual(events[0].cue, "hit")
        self.assertEqual(self.engine.score, NORMAL_POINTS)
        empty = next(index for index in self.layout.playable if index != target.index)
        events = self.engine.press(empty, now + 0.02)
        self.assertEqual(events[0].cue, "wrong")
        self.assertEqual(self.engine.score, NORMAL_POINTS - WRONG_PENALTY)
        self.assertEqual(self.engine.combo, 0)

    def test_lobby_controls_cycle_difficulty_sound_and_start(self) -> None:
        self.engine.press(self.layout.difficulty_key, 0.0)
        self.assertEqual(self.engine.difficulty, "hard")
        self.engine.press(self.layout.sound_key, 0.0)
        self.assertTrue(self.engine.sound_enabled)
        self.engine.press(self.layout.start_key, 0.0)
        self.assertEqual(self.engine.phase, PHASE_COUNTDOWN)

    def test_round_finishes_and_records_the_score(self) -> None:
        now = self._begin()
        target = self.engine.snapshot(now).targets[0]
        self.engine.press(target.index, now + 0.01)

        events = self.engine.tick(COUNTDOWN_SECONDS + ROUND_SECONDS + 0.02)

        self.assertEqual(self.engine.phase, PHASE_RESULTS)
        self.assertTrue(events[0].high_score_changed)
        self.assertEqual(self.engine.high_score, NORMAL_POINTS)

    def test_golden_mole_is_worth_more(self) -> None:
        class GoldenRandom:
            @staticmethod
            def choice(values):
                return values[0]

            @staticmethod
            def random():
                return 0.0

        engine = MoleSmashEngine(
            self.layout,
            sound_enabled=False,
            rng=GoldenRandom(),
            now=0.0,
        )
        engine.press(self.layout.start_key, 0.0)
        engine.tick(COUNTDOWN_SECONDS + 0.01)
        engine.tick(COUNTDOWN_SECONDS + 0.02)
        target = engine.snapshot(COUNTDOWN_SECONDS + 0.02).targets[0]

        events = engine.press(target.index, COUNTDOWN_SECONDS + 0.03)

        self.assertTrue(target.golden)
        self.assertEqual(events[0].cue, "golden")
        self.assertEqual(engine.score, GOLDEN_POINTS)


class RenderTests(unittest.TestCase):
    def test_original_sprite_is_transparent_and_bundled(self) -> None:
        sprite = Image.open(MOLE_ASSET)

        self.assertEqual(sprite.mode, "RGBA")
        self.assertEqual(sprite.getpixel((0, 0))[3], 0)
        self.assertIsNotNone(sprite.getbbox())

    def test_every_geometry_renders_a_complete_distinct_lobby(self) -> None:
        for keys, columns in ((6, 3), (8, 4), (15, 5), (32, 8)):
            with self.subTest(keys=keys):
                engine = MoleSmashEngine(game_layout(keys, columns), now=0.0)
                images = render_keys(engine.snapshot(0.0), (72, 72))
                self.assertEqual(len(images), keys)
                self.assertTrue(all(image.size == (72, 72) for image in images))
                self.assertGreater(len({image.tobytes() for image in images}), 2)

    def test_visible_mole_differs_from_an_empty_hole(self) -> None:
        layout = game_layout(15, 5)
        engine = MoleSmashEngine(layout, sound_enabled=False, now=0.0)
        engine.press(layout.start_key, 0.0)
        engine.tick(COUNTDOWN_SECONDS + 0.01)
        engine.tick(COUNTDOWN_SECONDS + 0.02)
        snapshot = engine.snapshot(COUNTDOWN_SECONDS + 0.25)
        images = render_keys(snapshot, (72, 72))
        target = snapshot.targets[0].index
        empty = next(index for index in layout.playable if index != target)

        difference = ImageChops.difference(images[target], images[empty])
        self.assertIsNotNone(difference.getbbox())

    def test_plus_touchscreen_hud_has_visible_content(self) -> None:
        engine = MoleSmashEngine(
            game_layout(8, 4, touchscreen_hud=True),
            now=0.0,
        )
        image = touchscreen_hud(engine.snapshot(0.0), (800, 100))
        background = Image.new("RGB", image.size, "#11151b")

        self.assertEqual(image.size, (800, 100))
        self.assertIsNotNone(ImageChops.difference(image, background).getbbox())


class FakeSound:
    def __init__(self) -> None:
        self.cues = []
        self.cancelled = 0
        self.stopped = 0

    def play(self, game_id, cue, volume) -> None:
        self.cues.append((game_id, cue, volume))

    def cancel(self) -> None:
        self.cancelled += 1

    def shutdown(self) -> None:
        self.stopped += 1


class FakeDeck:
    key_count = 15
    columns = 5
    image_size = (72, 72)
    dial_count = 0
    touch_size = (800, 100)
    screensaver_active = False

    def __init__(self) -> None:
        self.images = {}
        self.suppression = []
        self.wakes = 0

    def set_key_image(self, index, image) -> None:
        self.images[index] = image.copy()

    def set_screensaver_suppressed(self, value, *, reason="external") -> None:
        self.suppression.append((value, reason))

    def stop_screensaver(self) -> None:
        self.wakes += 1


class FakePlusDeck(FakeDeck):
    key_count = 8
    columns = 4
    dial_count = 4

    def __init__(self) -> None:
        super().__init__()
        self.touch_images = []

    def set_touchscreen_image(self, image) -> None:
        self.touch_images.append(image.copy())


class ManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = EventBus()
        self.deck = FakeDeck()
        self.sound = FakeSound()
        self.restored = 0
        self.settings = GameSettings(mole_sound_enabled=False)
        self.manager = GameManager(
            self.bus,
            self.deck,
            self.settings,
            self._restore,
            sound_player=self.sound,
        )

    def tearDown(self) -> None:
        self.manager.shutdown()

    def _restore(self) -> None:
        self.restored += 1

    @staticmethod
    def _until(condition, timeout=2.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return True
            time.sleep(0.01)
        return bool(condition())

    def test_session_suppresses_saver_paints_and_restores(self) -> None:
        states = []
        self.bus.subscribe(
            "game.state",
            lambda _topic, data: states.append(data.copy()),
        )

        self.assertTrue(self.manager.start())
        self.assertTrue(self._until(lambda: len(self.deck.images) == 15))
        self.assertEqual(self.deck.suppression[0], (True, "game"))
        self.assertEqual(self.deck.wakes, 1)
        self.assertTrue(states[0]["active"])

        self.assertTrue(self.manager.stop())
        self.assertTrue(self._until(lambda: not self.manager.active))
        self.assertEqual(self.deck.suppression[-1], (False, "game"))
        self.assertEqual(self.restored, 1)
        self.assertFalse(states[-1]["active"])

    def test_both_edges_of_a_game_press_are_consumed_after_exit(self) -> None:
        self.assertTrue(self.manager.start())
        exit_key = self.manager._engine.layout.exit_key

        self.assertTrue(self.manager.handle_key(exit_key, True))
        self.assertTrue(self._until(lambda: not self.manager.active))
        self.assertTrue(self.manager.handle_key(exit_key, False))
        self.assertFalse(self.manager.handle_key(exit_key, False))

    def test_a_reentrant_restart_waits_until_restore_is_complete(self) -> None:
        attempts = []
        self.manager._restore = lambda: attempts.append(self.manager.start())

        self.assertTrue(self.manager.start())
        self.assertTrue(self.manager.stop())
        self.assertTrue(self._until(lambda: not self.manager.active))

        self.assertEqual(attempts, [False])
        self.assertTrue(self.manager.start())

    def test_replaced_configuration_supplies_future_game_settings(self) -> None:
        replacement = GameSettings(
            mole_difficulty="hard",
            mole_sound_enabled=False,
            mole_high_scores={"5x3:hard": 91},
        )

        self.manager.adopt_settings(replacement)
        self.assertTrue(self.manager.start())

        self.assertIs(self.manager.settings, replacement)
        self.assertEqual(self.manager._engine.difficulty, "hard")
        self.assertEqual(self.manager._engine.high_score, 91)

    def test_plus_hud_reaches_both_the_strip_and_virtual_deck(self) -> None:
        deck = FakePlusDeck()
        hud_events = []
        self.bus.subscribe(
            "ui.game_hud",
            lambda _topic, data: hud_events.append(data["png"]),
        )
        manager = GameManager(
            self.bus,
            deck,
            GameSettings(mole_sound_enabled=False),
            lambda: None,
            sound_player=FakeSound(),
        )
        try:
            self.assertTrue(manager.start())
            self.assertTrue(self._until(lambda: bool(deck.touch_images)))
            self.assertTrue(any(hud_events))
            self.assertEqual(deck.touch_images[-1].size, deck.touch_size)
            self.assertTrue(manager.stop())
            self.assertTrue(self._until(lambda: not manager.active))
            self.assertEqual(hud_events[-1], b"")
        finally:
            manager.shutdown()

    def test_neon_relay_owns_and_uses_plus_dials(self) -> None:
        deck = FakePlusDeck()
        manager = GameManager(
            self.bus,
            deck,
            GameSettings(relay_sound_enabled=False),
            lambda: None,
            sound_player=FakeSound(),
        )
        try:
            self.assertTrue(manager.start("neon_relay"))
            self.assertTrue(manager.press_virtual(manager._engine.layout.start_key))
            with manager._lock:
                engine = manager._engine
                column = (engine._entry_key + 1) % engine.layout.columns
                before = tuple(tile.rotation for tile in engine._tiles)

            self.assertTrue(manager.handle_dial(column, "right", 1))

            with manager._lock:
                after = tuple(tile.rotation for tile in engine._tiles)
                engine._overdrive_level = 40
            changed = {
                index
                for index, rotations in enumerate(zip(before, after))
                if rotations[0] != rotations[1]
            }
            self.assertTrue(changed)
            self.assertTrue(
                all(index % engine.layout.columns == column for index in changed)
            )
            self.assertTrue(manager.handle_dial(0, "press", 1))
            with manager._lock:
                self.assertTrue(engine.snapshot(manager._monotonic()).stasis_active)
            self.assertTrue(manager.stop())
            self.assertTrue(self._until(lambda: not manager.active))
            self.assertFalse(manager.handle_dial(0, "right", 1))
        finally:
            manager.shutdown()


class GameConfigTests(unittest.TestCase):
    def test_preferences_and_high_scores_round_trip(self) -> None:
        raw = Config()._serializable_dict()
        raw["games"] = {
            "mole_difficulty": "hard",
            "mole_sound_enabled": False,
            "mole_volume": 73,
            "mole_high_scores": {"5x3:hard": 321},
        }

        loaded = Config.from_dict(raw)

        self.assertEqual(loaded.games.mole_difficulty, "hard")
        self.assertFalse(loaded.games.mole_sound_enabled)
        self.assertEqual(loaded.games.mole_volume, 73)
        self.assertEqual(loaded.games.mole_high_scores, {"5x3:hard": 321})

    def test_invalid_game_settings_fall_back_safely(self) -> None:
        raw = Config()._serializable_dict()
        raw["games"] = {
            "mole_difficulty": "impossible",
            "mole_sound_enabled": "yes",
            "mole_volume": 200,
            "mole_high_scores": {"bad": -4},
        }

        loaded = Config.from_dict(raw)

        self.assertEqual(loaded.games.mole_difficulty, "normal")
        self.assertTrue(loaded.games.mole_sound_enabled)
        self.assertEqual(loaded.games.mole_volume, 100)
        self.assertEqual(loaded.games.mole_high_scores, {"bad": 0})


if __name__ == "__main__":
    unittest.main()
