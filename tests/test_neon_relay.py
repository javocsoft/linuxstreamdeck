from __future__ import annotations

import random
import unittest

from PIL import Image, ImageChops

from linuxstreamdeck.games.common import PHASE_PLAYING, PHASE_RESULTS, game_layout
from linuxstreamdeck.games.neon_relay import (
    DIFFICULTIES,
    PHASE_RECOVER,
    PHASE_SECTOR_CLEAR,
    PHASE_UPGRADE,
    NeonRelayEngine,
    direction_between,
    tile_connections,
)
from linuxstreamdeck.games.render import render_keys, touchscreen_hud


class NeonRelayEngineTests(unittest.TestCase):
    @staticmethod
    def _engine(
        keys: int = 15,
        columns: int = 5,
        difficulty: str = "normal",
        *,
        plus: bool = False,
        seed: int = 17,
    ) -> NeonRelayEngine:
        engine = NeonRelayEngine(
            game_layout(keys, columns, plus),
            difficulty=difficulty,
            sound_enabled=False,
            rng=random.Random(seed),
        )
        engine.press(engine.layout.start_key, 0.0)
        return engine

    @staticmethod
    def _align_route(engine: NeonRelayEngine) -> None:
        for index in engine._route:
            tile = engine._tiles[index]
            tile.rotation = tile.required_rotation

    @classmethod
    def _finish_sector(cls, engine: NeonRelayEngine) -> tuple:
        cls._align_route(engine)
        events = []
        guard = 0
        while engine.phase == PHASE_PLAYING:
            events.extend(engine.tick(engine._next_step + 0.001))
            guard += 1
            if guard > engine.layout.key_count * 2:
                raise AssertionError("the spark did not leave its guaranteed route")
        return tuple(events)

    def test_every_generated_route_is_contiguous_and_connects_two_edges(self) -> None:
        for keys, columns in ((6, 3), (8, 4), (15, 5), (32, 8)):
            for difficulty in DIFFICULTIES:
                for seed in range(12):
                    with self.subTest(keys=keys, difficulty=difficulty, seed=seed):
                        engine = self._engine(keys, columns, difficulty, seed=seed)
                        snapshot = engine.snapshot(0.0)
                        self.assertEqual(len(snapshot.route), len(set(snapshot.route)))
                        self.assertGreaterEqual(len(snapshot.route), 2)
                        self.assertIsNone(
                            engine._neighbour(snapshot.entry_key, snapshot.entry_side)
                        )
                        self.assertIsNone(
                            engine._neighbour(snapshot.exit_key, snapshot.exit_side)
                        )
                        for first, second in zip(snapshot.route, snapshot.route[1:]):
                            self.assertIn(second, engine._neighbours(first))
                        for position, index in enumerate(snapshot.route):
                            tile = snapshot.tiles[index]
                            incoming = (
                                snapshot.entry_side
                                if position == 0
                                else direction_between(
                                    index,
                                    snapshot.route[position - 1],
                                    columns,
                                )
                            )
                            outgoing = (
                                snapshot.exit_side
                                if position == len(snapshot.route) - 1
                                else direction_between(
                                    index,
                                    snapshot.route[position + 1],
                                    columns,
                                )
                            )
                            self.assertEqual(
                                set(tile_connections(tile.kind, tile.required_rotation)),
                                {incoming, outgoing},
                            )

    def test_aligning_the_route_carries_the_spark_into_the_next_sector(self) -> None:
        engine = self._engine()

        events = self._finish_sector(engine)

        self.assertEqual(engine.phase, PHASE_SECTOR_CLEAR)
        self.assertGreater(engine.score, 0)
        self.assertEqual(engine.combo, 1)
        self.assertIn("gate", {event.cue for event in events})

    def test_a_broken_entry_uses_a_shield_or_finishes_the_run(self) -> None:
        easy = self._engine(difficulty="easy")
        easy.press(easy._entry_key, 0.1)
        events = easy.tick(easy._next_step + 0.001)
        self.assertEqual(easy.phase, PHASE_RECOVER)
        self.assertEqual(easy.shields, DIFFICULTIES["easy"].shields - 1)
        self.assertEqual({event.cue for event in events}, {"crash", "shield"})

        hard = self._engine(difficulty="hard")
        hard.press(hard._entry_key, 0.1)
        hard.tick(hard._next_step + 0.001)
        self.assertEqual(hard.phase, PHASE_RESULTS)

    def test_speed_is_distinct_by_difficulty_and_accelerates_by_sector(self) -> None:
        speeds = [
            self._engine(difficulty=difficulty).snapshot(0.0).speed_seconds
            for difficulty in ("easy", "normal", "hard")
        ]
        self.assertGreater(speeds[0], speeds[1])
        self.assertGreater(speeds[1], speeds[2])

        engine = self._engine(difficulty="normal")
        first = engine.snapshot(0.0).speed_seconds
        engine.sector = 6
        self.assertLess(engine.snapshot(0.0).speed_seconds, first)

    def test_every_third_sector_offers_and_applies_an_upgrade(self) -> None:
        engine = self._engine()
        now = 0.0
        for sector in range(1, 4):
            self._finish_sector(engine)
            now = engine._phase_deadline + 0.001
            engine.tick(now)
            if sector < 3:
                self.assertEqual(engine.phase, PHASE_PLAYING)

        self.assertEqual(engine.phase, PHASE_UPGRADE)
        self.assertEqual(set(engine._upgrade_choices), {"shield", "stasis", "surge"})
        self.assertEqual(len(engine._upgrade_keys), 3)
        events = engine.press(engine._upgrade_keys[0], now + 0.1)
        self.assertEqual(engine.phase, PHASE_PLAYING)
        self.assertEqual(engine.sector, 4)
        self.assertEqual(events[0].cue, "upgrade")

    def test_plus_dials_rotate_columns_and_press_spends_charge_on_stasis(self) -> None:
        engine = self._engine(8, 4, plus=True)
        before = tuple(tile.rotation for tile in engine._tiles)
        column = (engine._entry_key + 1) % engine.layout.columns

        events = engine.dial(column, "right", 1, 0.1)

        after = tuple(tile.rotation for tile in engine._tiles)
        changed = {index for index, pair in enumerate(zip(before, after)) if pair[0] != pair[1]}
        self.assertTrue(changed)
        self.assertTrue(all(index % engine.layout.columns == column for index in changed))
        self.assertEqual(events[0].cue, "rotate")

        engine._overdrive_level = 40
        deadline = engine._next_step
        events = engine.dial(0, "press", 1, 0.2)
        self.assertEqual(events[0].cue, "stasis")
        self.assertEqual(engine._overdrive_level, 0)
        self.assertGreater(engine._next_step, deadline)
        self.assertTrue(engine.snapshot(0.3).stasis_active)

    def test_overdrive_activates_at_full_charge_and_then_expires(self) -> None:
        engine = self._engine()
        self.assertTrue(engine._charge_overdrive(100, 1.0))
        self.assertTrue(engine.snapshot(1.1).overdrive)
        engine.tick(8.0)
        self.assertFalse(engine.snapshot(8.0).overdrive)


class NeonRelayRenderTests(unittest.TestCase):
    def test_game_fills_every_supported_deck_with_distinct_neon_tiles(self) -> None:
        for keys, columns in ((6, 3), (8, 4), (15, 5), (32, 8)):
            with self.subTest(keys=keys):
                engine = NeonRelayEngine(
                    game_layout(keys, columns),
                    rng=random.Random(21),
                )
                engine.press(engine.layout.start_key, 0.0)
                images = render_keys(engine.snapshot(0.24), (72, 72))
                self.assertEqual(len(images), keys)
                self.assertTrue(all(image.mode == "RGB" for image in images))
                self.assertTrue(all(image.getbbox() is not None for image in images))
                self.assertGreater(len({image.tobytes() for image in images}), 2)

    def test_plus_hud_shows_live_reactor_information(self) -> None:
        engine = NeonRelayEngine(
            game_layout(8, 4, True),
            rng=random.Random(22),
        )
        engine.press(engine.layout.start_key, 0.0)

        image = touchscreen_hud(engine.snapshot(0.2), (800, 100))

        background = Image.new("RGB", image.size, "#11151b")
        self.assertEqual(image.mode, "RGB")
        self.assertIsNotNone(ImageChops.difference(image, background).getbbox())


if __name__ == "__main__":
    unittest.main()
