from __future__ import annotations

import threading
import unittest

from linuxstreamdeck.core.clocks import (
    ClockRuntime,
    format_clock,
)


class ClockRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = [100.0]
        self.refreshed = []
        self.completed = []
        self.runtime = ClockRuntime(
            lambda keys: self.refreshed.append(keys),
            self.completed.append,
            monotonic=lambda: self.now[0],
            start_thread=False,
        )
        self.key = (0, 0, 3)

    def tearDown(self) -> None:
        self.runtime.shutdown()

    def test_countdown_updates_finishes_once_and_resets(self) -> None:
        started = self.runtime.toggle_timer(
            self.key,
            5,
            "/tmp/finished.mp3",
            65,
        )
        self.assertTrue(started)
        self.assertEqual(
            self.runtime.timer_snapshot(self.key, 5).display,
            "00:00:05",
        )

        self.refreshed.clear()
        self.now[0] = 102.2
        self.runtime.tick()
        snapshot = self.runtime.timer_snapshot(self.key, 5)
        self.assertEqual(snapshot.display, "00:00:03")
        self.assertTrue(snapshot.running)
        self.assertEqual(self.refreshed, [(self.key,)])

        self.now[0] = 105.0
        self.runtime.tick()
        finished = self.runtime.timer_snapshot(self.key, 5)
        self.assertEqual(finished.display, "00:00:00")
        self.assertTrue(finished.finished)
        self.assertEqual(len(self.completed), 1)
        self.assertEqual(self.completed[0].sound_file, "/tmp/finished.mp3")
        self.assertEqual(self.completed[0].volume, 65)
        self.assertTrue(
            self.runtime.is_current_completion(self.completed[0])
        )

        self.runtime.tick()
        self.assertEqual(len(self.completed), 1)

        self.assertFalse(self.runtime.toggle_timer(self.key, 5))
        idle = self.runtime.timer_snapshot(self.key, 5)
        self.assertEqual(idle.display, "00:00:05")
        self.assertFalse(idle.running)
        self.assertFalse(
            self.runtime.is_current_completion(self.completed[0])
        )

    def test_pressing_an_active_timer_stops_and_resets_it(self) -> None:
        self.assertTrue(self.runtime.toggle_timer(self.key, 30))
        self.now[0] = 112.0
        self.assertEqual(
            self.runtime.timer_snapshot(self.key, 30).display,
            "00:00:18",
        )

        self.assertFalse(self.runtime.toggle_timer(self.key, 30))

        snapshot = self.runtime.timer_snapshot(self.key, 30)
        self.assertEqual(snapshot.display, "00:00:30")
        self.assertFalse(snapshot.running)

    def test_stopwatch_counts_hours_and_second_press_resets(self) -> None:
        self.assertTrue(self.runtime.toggle_stopwatch(self.key))
        self.now[0] = 3761.9

        snapshot = self.runtime.stopwatch_snapshot(self.key)
        self.assertEqual(snapshot.display, "01:01:01")
        self.assertTrue(snapshot.running)

        self.assertFalse(self.runtime.toggle_stopwatch(self.key))
        reset = self.runtime.stopwatch_snapshot(self.key)
        self.assertEqual(reset.display, "00:00:00")
        self.assertFalse(reset.running)

    def test_clock_state_moves_with_its_key(self) -> None:
        destination = (0, 0, 12)
        self.runtime.toggle_stopwatch(self.key)
        self.now[0] = 110.0

        self.runtime.swap(self.key, destination)

        self.assertFalse(self.runtime.stopwatch_snapshot(self.key).running)
        moved = self.runtime.stopwatch_snapshot(destination)
        self.assertTrue(moved.running)
        self.assertEqual(moved.display, "00:00:10")

    def test_clock_format_is_always_hours_minutes_seconds(self) -> None:
        self.assertEqual(format_clock(0), "00:00:00")
        self.assertEqual(format_clock(90061), "25:01:01")

    def test_scheduler_completes_a_timer_and_shuts_down(self) -> None:
        finished = threading.Event()
        runtime = ClockRuntime(
            lambda _keys: None,
            lambda _completion: finished.set(),
        )
        try:
            runtime.toggle_timer((0, 0, 0), 0.05)
            self.assertTrue(finished.wait(0.5))
        finally:
            runtime.shutdown()


if __name__ == "__main__":
    unittest.main()
