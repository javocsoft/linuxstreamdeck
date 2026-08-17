from __future__ import annotations

import itertools
import random
import time
import unittest
from collections import deque

from PIL import Image, ImageChops

from linuxstreamdeck.core.config import Config, GameSettings
from linuxstreamdeck.core.events import EventBus
from linuxstreamdeck.games.audio import ASSET_ROOT, CUE_FILES
from linuxstreamdeck.games.circuit_breaker import CircuitBreakerEngine
from linuxstreamdeck.games.manager import GameManager
from linuxstreamdeck.games.memory_match import (
    PHASE_MISMATCH,
    PHASE_PREVIEW,
    MemoryMatchEngine,
)
from linuxstreamdeck.games.mole_smash import (
    COUNTDOWN_SECONDS,
    PHASE_PLAYING,
    PHASE_RESULTS,
    game_layout,
)
from linuxstreamdeck.games.pulse_memory import (
    PHASE_INPUT,
    PHASE_ROUND_PAUSE,
    PHASE_SHOWING,
    PulseMemoryEngine,
)
from linuxstreamdeck.games.render import render_keys, touchscreen_hud


def _circuit_press(state: tuple[bool, ...], index: int, columns: int):
    result = list(state)
    rows = (len(result) + columns - 1) // columns
    row, column = divmod(index, columns)
    for candidate_row, candidate_column in (
        (row, column),
        (row - 1, column),
        (row + 1, column),
        (row, column - 1),
        (row, column + 1),
    ):
        candidate = candidate_row * columns + candidate_column
        if (
            0 <= candidate_row < rows
            and 0 <= candidate_column < columns
            and 0 <= candidate < len(result)
        ):
            result[candidate] = not result[candidate]
    return tuple(result)


def _circuit_solution(state: tuple[bool, ...], columns: int) -> tuple[int, ...]:
    target = (False,) * len(state)
    queue = deque([(state, ())])
    seen = {state}
    while queue:
        current, path = queue.popleft()
        if current == target:
            return path
        for index in range(len(current)):
            candidate = _circuit_press(current, index, columns)
            if candidate not in seen:
                seen.add(candidate)
                queue.append((candidate, path + (index,)))
    raise AssertionError("generated puzzle is not solvable")


class CircuitBreakerTests(unittest.TestCase):
    def test_generated_puzzles_are_non_empty_and_solvable(self) -> None:
        layout = game_layout(6, 3)
        for difficulty, seed in itertools.product(("easy", "normal", "hard"), range(8)):
            with self.subTest(difficulty=difficulty, seed=seed):
                engine = CircuitBreakerEngine(
                    layout,
                    difficulty=difficulty,
                    sound_enabled=False,
                    rng=random.Random(seed),
                )
                engine.press(layout.start_key, 0.0)
                state = engine.snapshot(0.0).lights
                self.assertTrue(any(state))
                solution = _circuit_solution(state, layout.columns)
                for move, index in enumerate(solution, 1):
                    engine.press(index, move * 0.1)
                self.assertEqual(engine.phase, PHASE_RESULTS)

    def test_a_press_toggles_only_the_cross_and_counts_a_move(self) -> None:
        layout = game_layout(15, 5)
        engine = CircuitBreakerEngine(layout, sound_enabled=False)
        engine.press(layout.start_key, 0.0)
        before = engine.snapshot(0.0).lights

        engine.press(7, 0.1)

        after = engine.snapshot(0.1).lights
        changed = {index for index, values in enumerate(zip(before, after)) if values[0] != values[1]}
        self.assertEqual(changed, {2, 6, 7, 8, 12})
        self.assertEqual(engine.moves, 1)

    def test_fewer_moves_replaces_the_record(self) -> None:
        layout = game_layout(6, 3)
        engine = CircuitBreakerEngine(layout, high_score=5, sound_enabled=False)
        engine.phase = PHASE_PLAYING
        engine._lights = list(_circuit_press((False,) * 6, 0, 3))

        events = engine.press(0, 1.0)

        self.assertEqual(engine.phase, PHASE_RESULTS)
        self.assertEqual(engine.high_score, 1)
        self.assertTrue(events[0].high_score_changed)


class PulseMemoryTests(unittest.TestCase):
    @staticmethod
    def _advance_to_input(engine: PulseMemoryEngine, now: float) -> float:
        while engine.phase == PHASE_SHOWING:
            now = engine._next_transition + 0.001
            engine.tick(now)
        return now

    def test_sequence_is_shown_then_requires_the_exact_order(self) -> None:
        layout = game_layout(8, 4)
        engine = PulseMemoryEngine(
            layout,
            difficulty="easy",
            sound_enabled=False,
            rng=random.Random(2),
        )
        engine.press(layout.start_key, 0.0)
        engine.tick(COUNTDOWN_SECONDS + 0.01)
        self.assertEqual(engine.phase, PHASE_SHOWING)
        sequence = tuple(engine._sequence)

        now = self._advance_to_input(engine, COUNTDOWN_SECONDS + 0.01)
        self.assertEqual(engine.phase, PHASE_INPUT)
        for position, index in enumerate(sequence):
            engine.press(index, now + position * 0.01)

        self.assertEqual(engine.phase, PHASE_ROUND_PAUSE)
        self.assertEqual(engine.score, len(sequence))
        self.assertEqual(engine.high_score, len(sequence))

    def test_input_is_ignored_while_the_sequence_is_showing(self) -> None:
        layout = game_layout(6, 3)
        engine = PulseMemoryEngine(layout, sound_enabled=False)
        engine.press(layout.start_key, 0.0)
        engine.tick(COUNTDOWN_SECONDS + 0.01)

        self.assertEqual(engine.press(0, COUNTDOWN_SECONDS + 0.02), ())
        self.assertEqual(engine._input_position, 0)

    def test_one_wrong_key_ends_the_round(self) -> None:
        layout = game_layout(6, 3)
        engine = PulseMemoryEngine(
            layout,
            difficulty="easy",
            sound_enabled=False,
            rng=random.Random(3),
        )
        engine.press(layout.start_key, 0.0)
        now = COUNTDOWN_SECONDS + 0.01
        engine.tick(now)
        now = self._advance_to_input(engine, now)
        expected = engine._sequence[0]
        wrong = next(index for index in range(layout.key_count) if index != expected)

        events = engine.press(wrong, now + 0.01)

        self.assertEqual(engine.phase, PHASE_RESULTS)
        self.assertEqual(events[0].cue, "wrong")
        self.assertEqual(engine.snapshot(now + 0.01).wrong_key, wrong)

    def test_a_correct_input_flashes_the_pressed_key(self) -> None:
        layout = game_layout(6, 3)
        engine = PulseMemoryEngine(
            layout,
            difficulty="easy",
            sound_enabled=False,
            rng=random.Random(6),
        )
        engine.press(layout.start_key, 0.0)
        now = COUNTDOWN_SECONDS + 0.01
        engine.tick(now)
        now = self._advance_to_input(engine, now)
        expected = engine._sequence[0]

        engine.press(expected, now + 0.01)

        self.assertEqual(engine.snapshot(now + 0.02).active_key, expected)
        engine.tick(now + 1.0)
        self.assertIsNone(engine.snapshot(now + 1.0).active_key)


class MemoryMatchTests(unittest.TestCase):
    @staticmethod
    def _pairs(engine: MemoryMatchEngine):
        pairs = {}
        for index, symbol in engine._symbols.items():
            pairs.setdefault(symbol, []).append(index)
        return list(pairs.values())

    def test_odd_decks_reserve_one_status_key_and_pair_every_other_key(self) -> None:
        engine = MemoryMatchEngine(game_layout(15, 5), difficulty="hard")
        engine.press(engine.layout.start_key, 0.0)
        snapshot = engine.snapshot(0.0)

        self.assertEqual(len(snapshot.cards), 14)
        self.assertEqual(snapshot.status_key, 14)
        self.assertEqual(snapshot.pair_count, 7)

    def test_easy_previews_cards_before_hiding_them(self) -> None:
        engine = MemoryMatchEngine(game_layout(6, 3), difficulty="easy")
        engine.press(engine.layout.start_key, 0.0)
        self.assertEqual(engine.phase, PHASE_PREVIEW)
        self.assertTrue(all(card.state == "revealed" for card in engine.snapshot(0.0).cards))

        engine.tick(3.0)

        self.assertEqual(engine.phase, PHASE_PLAYING)
        self.assertTrue(all(card.state == "hidden" for card in engine.snapshot(3.0).cards))

    def test_mismatch_blocks_input_until_both_cards_hide(self) -> None:
        engine = MemoryMatchEngine(
            game_layout(6, 3),
            difficulty="hard",
            rng=random.Random(4),
        )
        engine.press(engine.layout.start_key, 0.0)
        first = next(iter(engine._symbols))
        second = next(index for index, symbol in engine._symbols.items() if symbol != engine._symbols[first])
        third = next(index for index in engine._symbols if index not in (first, second))
        engine.press(first, 0.1)
        engine.press(second, 0.2)
        self.assertEqual(engine.phase, PHASE_MISMATCH)

        self.assertEqual(engine.press(third, 0.3), ())
        self.assertEqual(len(engine._revealed), 2)
        engine.tick(1.0)
        self.assertEqual(engine.phase, PHASE_PLAYING)
        self.assertEqual(engine._revealed, [])

    def test_matching_every_pair_finishes_and_records_moves(self) -> None:
        engine = MemoryMatchEngine(
            game_layout(6, 3),
            difficulty="hard",
            sound_enabled=False,
            rng=random.Random(5),
        )
        engine.press(engine.layout.start_key, 0.0)
        events = ()
        for move, pair in enumerate(self._pairs(engine), 1):
            engine.press(pair[0], move)
            events = engine.press(pair[1], move + 0.1)

        self.assertEqual(engine.phase, PHASE_RESULTS)
        self.assertEqual(engine.moves, 3)
        self.assertEqual(engine.high_score, 3)
        self.assertTrue(events[0].high_score_changed)


class ClassicGameRenderTests(unittest.TestCase):
    def _snapshots(self, keys: int, columns: int):
        layout = game_layout(keys, columns, keys == 8 and columns == 4)
        circuit = CircuitBreakerEngine(layout, rng=random.Random(1))
        circuit.press(layout.start_key, 0.0)
        pulse = PulseMemoryEngine(layout, difficulty="easy", rng=random.Random(1))
        pulse.press(layout.start_key, 0.0)
        pulse.tick(COUNTDOWN_SECONDS + 0.01)
        memory = MemoryMatchEngine(layout, difficulty="hard", rng=random.Random(1))
        memory.press(layout.start_key, 0.0)
        memory.press(0, 0.1)
        return (circuit.snapshot(0.1), pulse.snapshot(COUNTDOWN_SECONDS + 0.01), memory.snapshot(0.1))

    def test_every_game_fills_every_supported_geometry(self) -> None:
        for keys, columns in ((6, 3), (8, 4), (15, 5), (32, 8)):
            for snapshot in self._snapshots(keys, columns):
                with self.subTest(keys=keys, game=type(snapshot).__name__):
                    images = render_keys(snapshot, (72, 72))
                    self.assertEqual(len(images), keys)
                    self.assertTrue(all(image.size == (72, 72) for image in images))
                    self.assertGreater(len({image.tobytes() for image in images}), 1)

    def test_each_plus_hud_contains_visible_content(self) -> None:
        for snapshot in self._snapshots(8, 4):
            with self.subTest(game=type(snapshot).__name__):
                image = touchscreen_hud(snapshot, (800, 100))
                background = Image.new("RGB", image.size, "#11151b")
                self.assertIsNotNone(ImageChops.difference(image, background).getbbox())


class _FakeSound:
    def __init__(self) -> None:
        self.calls = []

    def play(self, game_id, cue, volume) -> None:
        self.calls.append((game_id, cue, volume))

    def cancel(self) -> None:
        pass

    def shutdown(self) -> None:
        pass


class _FakeDeck:
    key_count = 6
    columns = 3
    image_size = (72, 72)
    dial_count = 0
    touch_size = (800, 100)
    screensaver_active = False

    def __init__(self) -> None:
        self.images = {}

    def set_key_image(self, index, image) -> None:
        self.images[index] = image

    def set_screensaver_suppressed(self, _value, *, reason="external") -> None:
        self.reason = reason

    def stop_screensaver(self) -> None:
        pass


class ClassicGameManagerTests(unittest.TestCase):
    @staticmethod
    def _until(condition, timeout=2.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return True
            time.sleep(0.01)
        return bool(condition())

    def test_manager_starts_and_restores_each_registered_game(self) -> None:
        for game_id, name in (
            ("circuit_breaker", "Circuit Breaker"),
            ("pulse_memory", "Pulse Memory"),
            ("memory_match", "Memory Match"),
            ("minesweeper", "Minesweeper"),
            ("tic_tac_toe", "Tic-Tac-Toe"),
            ("mastermind", "Colour Mastermind"),
        ):
            with self.subTest(game=game_id):
                bus = EventBus()
                deck = _FakeDeck()
                restored = []
                manager = GameManager(
                    bus,
                    deck,
                    GameSettings(),
                    lambda: restored.append(True),
                    sound_player=_FakeSound(),
                )
                try:
                    self.assertTrue(manager.start(game_id))
                    self.assertEqual(manager.game_name, name)
                    self.assertTrue(self._until(lambda: len(deck.images) == 6))
                    self.assertTrue(manager.stop())
                    self.assertTrue(self._until(lambda: not manager.active))
                    self.assertEqual(restored, [True])
                finally:
                    manager.shutdown()

    def test_unknown_game_never_takes_ownership(self) -> None:
        manager = GameManager(
            EventBus(), _FakeDeck(), GameSettings(), lambda: None,
            sound_player=_FakeSound(),
        )
        try:
            self.assertFalse(manager.start("not_a_game"))
            self.assertFalse(manager.active)
        finally:
            manager.shutdown()

    def test_manager_persists_the_selected_game_preferences_and_record(self) -> None:
        bus = EventBus()
        settings = GameSettings(circuit_sound_enabled=False)
        changed = []
        bus.subscribe("game.settings", lambda *_args: changed.append(True))
        manager = GameManager(
            bus, _FakeDeck(), settings, lambda: None, sound_player=_FakeSound()
        )
        try:
            self.assertTrue(manager.start("circuit_breaker"))
            difficulty_key = manager._engine.layout.difficulty_key
            self.assertTrue(manager.press_virtual(difficulty_key))
            self.assertEqual(settings.circuit_difficulty, "hard")

            with manager._lock:
                engine = manager._engine
                engine.phase = PHASE_PLAYING
                engine.moves = 0
                engine._lights = list(
                    _circuit_press(
                        (False,) * engine.layout.key_count,
                        0,
                        engine.layout.columns,
                    )
                )
            self.assertTrue(manager.press_virtual(0))

            self.assertEqual(settings.circuit_best_moves["3x2:hard"], 1)
            self.assertGreaterEqual(len(changed), 2)
        finally:
            manager.shutdown()

    def test_manager_routes_a_cue_through_the_active_games_asset_set(self) -> None:
        sound = _FakeSound()
        manager = GameManager(
            EventBus(), _FakeDeck(), GameSettings(), lambda: None,
            sound_player=sound,
        )
        try:
            self.assertTrue(manager.start("pulse_memory"))
            manager.press_virtual(manager._engine.layout.difficulty_key)

            self.assertEqual(sound.calls, [("pulse_memory", "select", 55)])
        finally:
            manager.shutdown()


class ClassicGameConfigTests(unittest.TestCase):
    def test_new_preferences_and_records_round_trip(self) -> None:
        raw = Config()._serializable_dict()
        raw["games"].update(
            {
                "circuit_difficulty": "easy",
                "circuit_sound_enabled": False,
                "circuit_volume": 31,
                "circuit_best_moves": {"5x3:easy": 8},
                "pulse_difficulty": "hard",
                "pulse_sound_enabled": False,
                "pulse_volume": 42,
                "pulse_high_scores": {"5x3:hard": 12},
                "memory_difficulty": "easy",
                "memory_sound_enabled": False,
                "memory_volume": 63,
                "memory_best_moves": {"5x3:easy": 9},
                "mines_difficulty": "hard",
                "mines_sound_enabled": False,
                "mines_volume": 24,
                "mines_best_times": {"5x3:hard": 38},
                "tic_tac_toe_difficulty": "easy",
                "tic_tac_toe_sound_enabled": False,
                "tic_tac_toe_volume": 35,
                "tic_tac_toe_wins": {"5x3:easy": 7},
                "mastermind_difficulty": "normal",
                "mastermind_sound_enabled": False,
                "mastermind_volume": 46,
                "mastermind_best_attempts": {"5x3:normal": 4},
            }
        )

        settings = Config.from_dict(raw).games

        self.assertEqual(settings.circuit_difficulty, "easy")
        self.assertFalse(settings.circuit_sound_enabled)
        self.assertEqual(settings.circuit_volume, 31)
        self.assertEqual(settings.circuit_best_moves, {"5x3:easy": 8})
        self.assertEqual(settings.pulse_difficulty, "hard")
        self.assertFalse(settings.pulse_sound_enabled)
        self.assertEqual(settings.pulse_volume, 42)
        self.assertEqual(settings.pulse_high_scores, {"5x3:hard": 12})
        self.assertEqual(settings.memory_difficulty, "easy")
        self.assertFalse(settings.memory_sound_enabled)
        self.assertEqual(settings.memory_volume, 63)
        self.assertEqual(settings.memory_best_moves, {"5x3:easy": 9})
        self.assertEqual(settings.mines_difficulty, "hard")
        self.assertFalse(settings.mines_sound_enabled)
        self.assertEqual(settings.mines_volume, 24)
        self.assertEqual(settings.mines_best_times, {"5x3:hard": 38})
        self.assertEqual(settings.tic_tac_toe_difficulty, "easy")
        self.assertFalse(settings.tic_tac_toe_sound_enabled)
        self.assertEqual(settings.tic_tac_toe_volume, 35)
        self.assertEqual(settings.tic_tac_toe_wins, {"5x3:easy": 7})
        self.assertEqual(settings.mastermind_difficulty, "normal")
        self.assertFalse(settings.mastermind_sound_enabled)
        self.assertEqual(settings.mastermind_volume, 46)
        self.assertEqual(
            settings.mastermind_best_attempts,
            {"5x3:normal": 4},
        )


class ClassicGameAudioTests(unittest.TestCase):
    def test_every_game_owns_a_complete_bundled_pcm_sound_set(self) -> None:
        for game_id, cues in CUE_FILES.items():
            with self.subTest(game=game_id):
                directory = ASSET_ROOT / game_id
                self.assertTrue(directory.is_dir())
                for filename in cues.values():
                    path = directory / filename
                    self.assertTrue(path.is_file())
                    self.assertEqual(path.read_bytes()[:4], b"RIFF")

    def test_no_game_reaches_into_another_games_asset_directory(self) -> None:
        for game_id, cues in CUE_FILES.items():
            own_files = {path.name for path in (ASSET_ROOT / game_id).glob("*.wav")}
            self.assertEqual(own_files, set(cues.values()))


if __name__ == "__main__":
    unittest.main()
