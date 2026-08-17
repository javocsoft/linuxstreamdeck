from __future__ import annotations

import random
import unittest
from dataclasses import replace

from PIL import Image, ImageChops

from linuxstreamdeck.games.common import PHASE_PLAYING, PHASE_RESULTS, game_layout
from linuxstreamdeck.games.audio import CUE_FILES
from linuxstreamdeck.games.catalog import GAMES
from linuxstreamdeck.games.manager import _GAME_ENGINES, _SETTING_FIELDS
from linuxstreamdeck.games.mastermind import MastermindEngine
from linuxstreamdeck.games.minesweeper import MinesweeperEngine
from linuxstreamdeck.games.render import render_keys, touchscreen_hud
from linuxstreamdeck.games.tic_tac_toe import PHASE_AI_TURN, TicTacToeEngine
from linuxstreamdeck.core.config import GameSettings


class GameRegistrationTests(unittest.TestCase):
    def test_catalog_engine_settings_and_audio_registries_match(self) -> None:
        game_ids = {game.id for game in GAMES}

        self.assertEqual(game_ids, set(_GAME_ENGINES))
        self.assertEqual(game_ids, set(_SETTING_FIELDS))
        self.assertEqual(game_ids, set(CUE_FILES))
        settings = GameSettings()
        for fields in _SETTING_FIELDS.values():
            for field in fields:
                self.assertTrue(hasattr(settings, field), field)


class MinesweeperTests(unittest.TestCase):
    def _engine(self, difficulty="normal") -> MinesweeperEngine:
        engine = MinesweeperEngine(
            game_layout(15, 5),
            difficulty=difficulty,
            sound_enabled=False,
            rng=random.Random(12),
        )
        engine.press(engine.layout.start_key, 0.0)
        return engine

    def test_first_reveal_is_safe_and_uses_a_safe_neighbourhood(self) -> None:
        engine = self._engine()
        first = engine._cell_keys[6]

        engine.press(first, 0.1)

        self.assertNotIn(first, engine._mines)
        self.assertTrue(set(engine._neighbours(first)).isdisjoint(engine._mines))
        self.assertIn(first, engine._revealed)

    def test_flag_mode_marks_and_protects_a_hidden_cell(self) -> None:
        engine = self._engine()
        cell = engine._cell_keys[0]

        engine.press(engine._mode_key, 0.1)
        events = engine.press(cell, 0.2)
        engine.press(engine._mode_key, 0.3)

        self.assertEqual(events[0].cue, "flag")
        self.assertIn(cell, engine._flagged)
        self.assertEqual(engine.press(cell, 0.4), ())
        self.assertNotIn(cell, engine._revealed)

    def test_revealing_a_mine_finishes_with_an_explosion(self) -> None:
        engine = self._engine()
        engine.press(engine._cell_keys[0], 0.1)
        mine = next(iter(engine._mines))

        events = engine.press(mine, 1.0)

        self.assertEqual(events[0].cue, "explosion")
        self.assertEqual(engine.phase, PHASE_RESULTS)
        self.assertFalse(engine.won)

    def test_revealing_every_safe_cell_records_the_time(self) -> None:
        engine = self._engine("easy")
        engine.press(engine._cell_keys[0], 0.1)
        events = ()
        for step, cell in enumerate(engine._cell_keys, 1):
            if cell not in engine._mines and cell not in engine._revealed:
                events = engine.press(cell, step + 0.1)
                if engine.phase == PHASE_RESULTS:
                    break

        self.assertEqual(engine.phase, PHASE_RESULTS)
        self.assertTrue(engine.won)
        self.assertGreater(engine.high_score, 0)
        self.assertTrue(events[0].high_score_changed)

    def test_difficulty_increases_mine_density(self) -> None:
        counts = [
            self._engine(difficulty)._mine_count
            for difficulty in ("easy", "normal", "hard")
        ]

        self.assertEqual(counts, sorted(counts))
        self.assertGreater(counts[-1], counts[0])

    def test_compact_decks_keep_all_three_difficulties_distinct(self) -> None:
        for keys, columns in ((6, 3), (8, 4)):
            counts = []
            for difficulty in ("easy", "normal", "hard"):
                engine = MinesweeperEngine(
                    game_layout(keys, columns),
                    difficulty=difficulty,
                )
                engine.press(engine.layout.start_key, 0.0)
                counts.append(engine._mine_count)
            with self.subTest(keys=keys):
                self.assertEqual(counts, [1, 2, 3])

    def test_results_keep_the_field_and_offer_again_and_back(self) -> None:
        engine = self._engine("normal")
        engine.press(engine._cell_keys[0], 0.1)
        exploded = next(iter(engine._mines))
        wrong_flag = next(
            index
            for index in engine._cell_keys
            if index not in engine._mines and index != engine._cell_keys[0]
        )
        engine._revealed.discard(wrong_flag)
        engine._flagged.add(wrong_flag)
        engine.press(exploded, 0.2)
        snapshot = engine.snapshot(0.2)
        images = render_keys(snapshot, (72, 72))

        self.assertEqual(
            next(cell.state for cell in snapshot.cells if cell.index == exploded),
            "exploded",
        )
        self.assertNotEqual(snapshot.result_back_key, exploded)
        self.assertNotEqual(snapshot.result_back_key, wrong_flag)
        self.assertEqual(
            next(cell.state for cell in snapshot.cells if cell.index == wrong_flag),
            "wrong_flag",
        )
        self.assertGreater(len({image.tobytes() for image in images}), 3)
        self.assertTrue(
            engine.press(snapshot.result_back_key, 0.3)[0].exit_requested
        )

        engine = self._engine("normal")
        engine.press(engine._cell_keys[0], 0.1)
        engine.press(next(iter(engine._mines)), 0.2)
        engine.press(engine._mode_key, 0.3)
        self.assertEqual(engine.phase, PHASE_PLAYING)


class TicTacToeTests(unittest.TestCase):
    def test_standard_decks_get_a_centered_three_by_three_board(self) -> None:
        engine = TicTacToeEngine(game_layout(15, 5))

        self.assertEqual(len(engine._board_keys), 9)
        self.assertEqual(engine._board_columns, 3)
        self.assertIn((0, 1, 2), engine._lines)

    def test_mini_and_plus_get_playable_compact_three_in_a_row_boards(self) -> None:
        for keys, columns in ((6, 3), (8, 4)):
            with self.subTest(keys=keys):
                engine = TicTacToeEngine(game_layout(keys, columns))
                self.assertEqual(len(engine._board_keys), keys)
                self.assertTrue(engine._lines)

    def test_player_line_wins_and_increments_the_persisted_win_count(self) -> None:
        engine = TicTacToeEngine(game_layout(15, 5), sound_enabled=False)
        engine.press(engine.layout.start_key, 0.0)
        line = engine._lines[0]
        engine._marks[line[0]] = "X"
        engine._marks[line[1]] = "X"

        events = engine.press(engine._board_keys[line[2]], 0.1)

        self.assertEqual(engine.phase, PHASE_RESULTS)
        self.assertEqual(engine.winner, "X")
        self.assertEqual(engine.high_score, 1)
        self.assertTrue(events[0].high_score_changed)

    def test_normal_ai_blocks_an_immediate_player_win(self) -> None:
        engine = TicTacToeEngine(
            game_layout(15, 5),
            difficulty="normal",
            rng=random.Random(3),
        )
        engine.press(engine.layout.start_key, 0.0)
        line = engine._lines[0]
        engine._marks[line[0]] = "X"
        engine._marks[line[1]] = "X"
        engine.phase = PHASE_AI_TURN
        engine._ai_deadline = 0.0

        engine.tick(1.0)

        self.assertEqual(engine._marks[line[2]], "O")
        self.assertEqual(engine.phase, PHASE_PLAYING)

    def test_player_press_waits_for_the_visible_ai_turn(self) -> None:
        engine = TicTacToeEngine(game_layout(15, 5))
        engine.press(engine.layout.start_key, 0.0)
        move = engine._board_keys[0]

        events = engine.press(move, 0.1)

        self.assertEqual(events[0].cue, "mark")
        self.assertEqual(engine.phase, PHASE_AI_TURN)
        self.assertEqual(engine.tick(0.2), ())
        self.assertTrue(engine.tick(1.0))

    def test_hard_ai_answers_an_opening_corner_with_the_center(self) -> None:
        engine = TicTacToeEngine(
            game_layout(15, 5),
            difficulty="hard",
            rng=random.Random(7),
        )
        engine.press(engine.layout.start_key, 0.0)
        engine.press(engine._board_keys[0], 0.1)

        engine.tick(1.0)

        self.assertEqual(engine._marks[4], "O")

    def test_results_keep_the_winning_line_and_offer_again_and_back(self) -> None:
        engine = TicTacToeEngine(game_layout(6, 3), sound_enabled=False)
        engine.press(engine.layout.start_key, 0.0)
        line = engine._lines[0]
        engine._marks[line[0]] = "X"
        engine._marks[line[1]] = "X"
        engine.press(engine._board_keys[line[2]], 0.1)
        snapshot = engine.snapshot(0.1)

        self.assertTrue(
            set(snapshot.winning_cells).isdisjoint(
                {
                    engine._key_to_cell[snapshot.result_again_key],
                    engine._key_to_cell[snapshot.result_back_key],
                }
            )
        )
        images = render_keys(snapshot, (72, 72))
        without_highlight = render_keys(
            replace(snapshot, winning_cells=()),
            (72, 72),
        )
        for cell in snapshot.winning_cells:
            key = snapshot.board_keys[cell]
            self.assertIsNotNone(
                ImageChops.difference(images[key], without_highlight[key]).getbbox()
            )
        self.assertTrue(
            engine.press(snapshot.result_back_key, 0.2)[0].exit_requested
        )

        engine = TicTacToeEngine(game_layout(6, 3), sound_enabled=False)
        engine.press(engine.layout.start_key, 0.0)
        line = engine._lines[0]
        engine._marks[line[0]] = "X"
        engine._marks[line[1]] = "X"
        engine.press(engine._board_keys[line[2]], 0.1)
        engine.press(engine.snapshot(0.1).result_again_key, 0.2)
        self.assertEqual(engine.phase, PHASE_PLAYING)

    def test_compact_board_visibly_changes_while_the_ai_thinks(self) -> None:
        engine = TicTacToeEngine(game_layout(6, 3), sound_enabled=False)
        engine.press(engine.layout.start_key, 0.0)
        engine.press(engine._board_keys[0], 0.1)
        waiting = engine.snapshot(0.1)
        player_turn = replace(waiting, phase=PHASE_PLAYING)

        waiting_images = render_keys(waiting, (72, 72))
        player_images = render_keys(player_turn, (72, 72))

        self.assertNotEqual(
            tuple(image.tobytes() for image in waiting_images),
            tuple(image.tobytes() for image in player_images),
        )


class MastermindTests(unittest.TestCase):
    def test_duplicate_colours_are_scored_once(self) -> None:
        self.assertEqual(
            MastermindEngine._score((0, 0, 1, 1), (0, 1, 0, 2)),
            (1, 2),
        )

    def test_peg_press_cycles_and_reset_restores_the_first_colour(self) -> None:
        engine = MastermindEngine(game_layout(15, 5))
        engine.press(engine.layout.start_key, 0.0)

        events = engine.press(engine._slot_keys[0], 0.1)
        engine.press(engine._clear_key, 0.2)

        self.assertEqual(events[0].cue, "peg")
        self.assertEqual(engine._current, [0] * len(engine._slot_keys))

    def test_exact_code_finishes_and_records_fewer_attempts(self) -> None:
        engine = MastermindEngine(
            game_layout(15, 5),
            sound_enabled=False,
            rng=random.Random(5),
        )
        engine.press(engine.layout.start_key, 0.0)
        engine._current = list(engine._solution)

        events = engine.press(engine._submit_key, 0.1)

        self.assertEqual(engine.phase, PHASE_RESULTS)
        self.assertTrue(engine.won)
        self.assertEqual(engine.high_score, 1)
        self.assertTrue(events[0].high_score_changed)

    def test_running_out_of_attempts_reveals_the_solution(self) -> None:
        engine = MastermindEngine(game_layout(6, 3), difficulty="easy")
        engine.press(engine.layout.start_key, 0.0)
        engine._solution = (1,) * len(engine._slot_keys)
        for attempt in range(engine._settings.attempts):
            engine.press(engine._submit_key, attempt + 0.1)

        snapshot = engine.snapshot(20.0)
        self.assertEqual(engine.phase, PHASE_RESULTS)
        self.assertFalse(engine.won)
        self.assertEqual(snapshot.solution, engine._solution)

    def test_code_length_adapts_to_the_available_keys(self) -> None:
        mini = MastermindEngine(game_layout(6, 3), difficulty="hard")
        original = MastermindEngine(game_layout(15, 5), difficulty="hard")

        self.assertEqual(len(mini._slot_keys), 4)
        self.assertEqual(len(original._slot_keys), 5)
        self.assertIsNotNone(mini._clear_key)

    def test_mini_submit_key_keeps_the_latest_clue_visible(self) -> None:
        engine = MastermindEngine(game_layout(6, 3), difficulty="normal")
        engine.press(engine.layout.start_key, 0.0)
        engine._solution = (1,) * len(engine._slot_keys)
        before = render_keys(engine.snapshot(0.0), (72, 72))[engine._submit_key]

        engine.press(engine._submit_key, 0.1)
        after = render_keys(engine.snapshot(0.1), (72, 72))[engine._submit_key]

        self.assertFalse(engine._history_keys)
        self.assertIsNotNone(ImageChops.difference(before, after).getbbox())

    def test_results_show_the_whole_code_and_have_again_and_back(self) -> None:
        engine = MastermindEngine(game_layout(6, 3), difficulty="normal")
        engine.press(engine.layout.start_key, 0.0)
        engine._solution = tuple(range(len(engine._slot_keys)))
        engine._current = list(engine._solution)
        engine.press(engine._submit_key, 0.1)

        images = render_keys(engine.snapshot(0.1), (72, 72))

        self.assertEqual(
            len({images[index].tobytes() for index in engine._slot_keys}),
            len(engine._slot_keys),
        )
        self.assertTrue(engine.press(engine._clear_key, 0.2)[0].exit_requested)

        engine = MastermindEngine(game_layout(6, 3), difficulty="normal")
        engine.press(engine.layout.start_key, 0.0)
        engine._solution = tuple(engine._current)
        engine.press(engine._submit_key, 0.1)
        engine.press(engine._submit_key, 0.2)
        self.assertEqual(engine.phase, PHASE_PLAYING)


class NewGameRenderTests(unittest.TestCase):
    @staticmethod
    def _snapshots(keys: int, columns: int):
        layout = game_layout(keys, columns, keys == 8 and columns == 4)
        mines = MinesweeperEngine(layout, rng=random.Random(1))
        mines.press(layout.start_key, 0.0)
        mines.press(mines._cell_keys[0], 0.1)
        tic = TicTacToeEngine(layout, rng=random.Random(1))
        tic.press(layout.start_key, 0.0)
        tic.press(tic._board_keys[0], 0.1)
        master = MastermindEngine(layout, rng=random.Random(1))
        master.press(layout.start_key, 0.0)
        master.press(master._slot_keys[0], 0.1)
        return mines.snapshot(0.2), tic.snapshot(0.2), master.snapshot(0.2)

    def test_every_new_game_fills_every_supported_geometry(self) -> None:
        for keys, columns in ((6, 3), (8, 4), (15, 5), (32, 8)):
            for snapshot in self._snapshots(keys, columns):
                with self.subTest(keys=keys, game=type(snapshot).__name__):
                    images = render_keys(snapshot, (72, 72))
                    self.assertEqual(len(images), keys)
                    self.assertTrue(all(image.size == (72, 72) for image in images))
                    self.assertGreater(len({image.tobytes() for image in images}), 1)

    def test_each_new_plus_hud_contains_visible_content(self) -> None:
        for snapshot in self._snapshots(8, 4):
            with self.subTest(game=type(snapshot).__name__):
                image = touchscreen_hud(snapshot, (800, 100))
                background = Image.new("RGB", image.size, "#11151b")
                self.assertIsNotNone(ImageChops.difference(image, background).getbbox())


if __name__ == "__main__":
    unittest.main()
