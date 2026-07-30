from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from linuxstreamdeck import basic_actions as _basic_actions  # noqa: F401
from linuxstreamdeck.core import actions as action_registry
from linuxstreamdeck.core.config import (
    KIND_MULTI,
    KIND_SINGLE,
    KIND_TOGGLE,
    ActionStep,
    Config,
    KeyConfig,
    Page,
)
from linuxstreamdeck.core.clocks import TimerCompletion
from linuxstreamdeck.core.controller import DeckController
from linuxstreamdeck.core.events import EventBus
from linuxstreamdeck.device.renderer import compose


class FakeDeck:
    key_count = 15
    image_size = (72, 72)

    def __init__(self) -> None:
        self.images = {}
        self.screensaver_active = False
        self.activity = 0

    def set_key_image(self, index, image) -> None:
        self.images[index] = image

    def record_activity(self) -> bool:
        self.activity += 1
        return self.screensaver_active

    def set_brightness(self, _value) -> None:
        pass

    def configure_screensaver(self, *_args) -> None:
        pass


class ControllerActivityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = Config()
        self.bus = EventBus()
        self.deck = FakeDeck()
        self.controller = DeckController(
            self.config,
            self.bus,
            SimpleNamespace(connected=False),
            self.deck,
        )

    def tearDown(self) -> None:
        self.controller.shutdown()

    def test_running_count_keeps_feedback_until_every_invocation_finishes(
        self,
    ) -> None:
        key = self.controller._tkey(0)

        self.controller._begin_running(key)
        self.controller._begin_running(key)
        self.assertEqual(self.controller._running[key], 2)
        self.assertEqual(
            self.controller._key_spec(
                0,
                KeyConfig(
                    kind=KIND_MULTI,
                    steps=[ActionStep(action="sys.wait")],
                ),
                self.deck.image_size,
            )["badge"],
            "RUN",
        )

        self.controller._end_running(key)
        self.assertTrue(self.controller._busy_state(0)[0])
        self.controller._end_running(key)
        self.assertFalse(self.controller._busy_state(0)[0])

    def test_running_feedback_is_cleared_in_run_steps_finally(self) -> None:
        key = self.controller._tkey(0)
        self.controller._begin_running(key)

        self.controller._run_steps([], 0, key)

        self.assertFalse(self.controller._busy_state(0)[0])

    def test_running_feedback_is_isolated_by_page(self) -> None:
        self.config.pages.append(Page(name="Page 2"))
        key = self.controller._tkey(0)
        self.controller._begin_running(key)

        self.config.current_page = 1
        self.assertFalse(self.controller._busy_state(0)[0])
        self.config.current_page = 0
        self.assertTrue(self.controller._busy_state(0)[0])

        self.controller._end_running(key)

    def test_toggle_restores_its_state_badge_after_running(self) -> None:
        key = self.controller._tkey(0)
        toggle = KeyConfig(
            kind=KIND_TOGGLE,
            steps_on=[ActionStep(action="sys.wait")],
        )
        self.controller._toggle[key] = True
        self.controller._begin_running(key)

        busy = self.controller._key_spec(0, toggle, self.deck.image_size)
        self.assertEqual(busy["badge"], "RUN")
        self.assertTrue(busy["active"])

        self.controller._end_running(key)
        idle = self.controller._key_spec(0, toggle, self.deck.image_size)
        self.assertEqual(idle["badge"], "ON")
        self.assertTrue(idle["active"])

    def test_single_audio_action_uses_running_feedback(self) -> None:
        key = self.controller._tkey(0)
        audio = KeyConfig(
            kind=KIND_SINGLE,
            action="sys.audio",
            params={"file": "/tmp/tone.mp3"},
        )
        self.controller._begin_running(key)

        self.assertEqual(
            self.controller._key_spec(0, audio, self.deck.image_size)["badge"],
            "RUN",
        )

        self.controller._end_running(key)
        self.assertEqual(
            self.controller._key_spec(0, audio, self.deck.image_size)["badge"],
            "",
        )

    def test_countdown_starts_immediately_and_second_press_resets(self) -> None:
        timer = KeyConfig(
            kind=KIND_SINGLE,
            action="sys.timer",
            params={
                "duration": "00:10",
                "sound": "",
                "volume": 100,
            },
        )
        self.config.pages[0].set_key(0, timer)

        with patch.object(self.controller._action_executor, "submit") as submit:
            self.controller.press(0)

        submit.assert_not_called()
        running = self.controller.countdown_snapshot(self.controller._tkey(0), 10)
        self.assertTrue(running.running)
        spec = self.controller._key_spec(0, timer, self.deck.image_size)
        self.assertEqual(spec["center_text"], "00:00:10")
        self.assertTrue(spec["active"])

        self.controller.press(0)
        reset = self.controller.countdown_snapshot(self.controller._tkey(0), 10)
        self.assertEqual(reset.display, "00:00:10")
        self.assertFalse(reset.running)

    def test_waking_the_screen_saver_does_not_run_the_key_action(self) -> None:
        timer = KeyConfig(
            kind=KIND_SINGLE,
            action="sys.timer",
            params={"duration": "00:10", "sound": "", "volume": 100},
        )
        self.config.pages[0].set_key(0, timer)
        self.deck.screensaver_active = True

        self.controller.press(0)

        snapshot = self.controller.countdown_snapshot(self.controller._tkey(0), 10)
        self.assertFalse(snapshot.running)
        self.deck.screensaver_active = False
        self.controller.press(0)
        self.assertTrue(
            self.controller.countdown_snapshot(self.controller._tkey(0), 10).running
        )

    def test_resetting_a_finished_timer_stops_its_sound(self) -> None:
        started = threading.Event()
        stopped = threading.Event()
        completion = TimerCompletion(
            key=(0, 0, (), 4),
            token=object(),
            sound_file="/tmp/finished.mp3",
            volume=70,
        )
        real_clocks = self.controller._clocks
        self.controller._clocks = SimpleNamespace(
            is_current_completion=lambda _completion: True,
        )

        def fake_play_audio(
            _path,
            _volume,
            stop_requested,
        ) -> None:
            started.set()
            while not stop_requested():
                stopped.wait(0.01)
            stopped.set()

        try:
            with patch(
                "linuxstreamdeck.core.controller.play_audio",
                side_effect=fake_play_audio,
            ):
                self.controller._on_timer_finished(completion)
                self.assertTrue(started.wait(1))
                self.controller._cancel_timer_sound(completion.key)
                self.assertTrue(stopped.wait(1))
        finally:
            self.controller._clocks = real_clocks

    def test_replacing_a_key_clears_all_transient_state(self) -> None:
        key = self.controller._tkey(6)
        sound = threading.Event()
        self.controller._toggle[key] = True
        self.controller._clocks.toggle_stopwatch(key)
        self.controller._notification_controls[key] = sound

        self.controller.key_config_changed(6)

        self.assertNotIn(key, self.controller._toggle)
        self.assertFalse(
            self.controller.stopwatch_snapshot(key).running
        )
        self.assertTrue(sound.is_set())

    def test_rapid_audio_repress_stops_then_starts_latest_invocation(self) -> None:
        key_config = KeyConfig(
            kind=KIND_SINGLE,
            action="sys.audio",
            params={"marker": "first"},
        )
        self.config.pages[0].set_key(0, key_config)
        audio_action = action_registry.get("sys.audio")
        self.assertIsNotNone(audio_action)

        first_started = threading.Event()
        first_cancelled = threading.Event()
        release_first = threading.Event()
        latest_started = threading.Event()
        release_latest = threading.Event()
        latest_finished = threading.Event()
        state_lock = threading.Lock()
        order = []
        active = 0
        maximum_active = 0

        def fake_execute(ctx, params) -> None:
            nonlocal active, maximum_active
            marker = params["marker"]
            with state_lock:
                active += 1
                maximum_active = max(maximum_active, active)
                order.append(f"start:{marker}")
            try:
                if marker == "first":
                    first_started.set()
                    if ctx.wait_until_stopped(1):
                        first_cancelled.set()
                    release_first.wait(1)
                elif marker == "third":
                    latest_started.set()
                    release_latest.wait(1)
                else:
                    release_latest.wait(1)
            finally:
                with state_lock:
                    order.append(f"stop:{marker}")
                    active -= 1
                if marker == "third":
                    latest_finished.set()

        with patch.object(audio_action, "execute", side_effect=fake_execute):
            self.controller.press(0)
            self.assertTrue(first_started.wait(1))

            key_config.params = {"marker": "second"}
            self.controller.press(0)
            self.assertTrue(first_cancelled.wait(1))

            key_config.params = {"marker": "third"}
            self.controller.press(0)
            release_first.set()

            self.assertTrue(latest_started.wait(1))
            with state_lock:
                self.assertEqual(
                    order,
                    ["start:first", "stop:first", "start:third"],
                )
                self.assertEqual(maximum_active, 1)

            release_latest.set()
            self.assertTrue(latest_finished.wait(1))

    def test_rendering_is_not_starved_by_two_wait_actions(self) -> None:
        rendered = set()
        both_rendered = threading.Event()

        def on_image(_topic, data) -> None:
            rendered.add(data["index"])
            if {0, 1}.issubset(rendered):
                both_rendered.set()

        self.bus.subscribe("ui.key_image", on_image)
        wait_key = KeyConfig(
            kind=KIND_MULTI,
            steps=[
                ActionStep(
                    action="sys.wait",
                    params={"duration": "00:05"},
                )
            ],
        )
        self.config.pages[0].set_key(0, wait_key)
        self.config.pages[0].set_key(1, wait_key.clone())

        self.controller.press(0)
        self.controller.press(1)

        self.assertTrue(both_rendered.wait(0.75))
        self.assertTrue(self.controller._busy_state(0)[0])
        self.assertTrue(self.controller._busy_state(1)[0])

    def test_wait_action_pulses_while_it_is_running(self) -> None:
        images = set()
        pulse_rendered = threading.Event()

        def on_image(_topic, data) -> None:
            if data["index"] != 0:
                return
            images.add(data["png"])
            if len(images) >= 2:
                pulse_rendered.set()

        self.bus.subscribe("ui.key_image", on_image)
        self.config.pages[0].set_key(
            0,
            KeyConfig(
                kind=KIND_MULTI,
                steps=[
                    ActionStep(
                        action="sys.wait",
                        params={"duration": "00:05"},
                    )
                ],
            ),
        )

        self.controller.press(0)

        self.assertTrue(pulse_rendered.wait(1.5))
        self.assertTrue(self.controller._busy_state(0)[0])

    def test_swap_moves_a_key_to_empty_targets_in_both_directions(self) -> None:
        page = self.config.pages[0]
        key = KeyConfig(action="nav.page.next")
        page.set_key(11, key)
        self.controller._toggle[self.controller._tkey(11)] = True
        self.config.save = lambda: None
        self.controller.refresh = lambda: None

        self.controller.swap_keys(11, 1)
        self.assertIs(page.key(1), key)
        self.assertIsNone(page.key(11))
        self.assertTrue(self.controller._toggle[self.controller._tkey(1)])

        self.controller.swap_keys(1, 13)
        self.assertIs(page.key(13), key)
        self.assertIsNone(page.key(1))
        self.assertTrue(self.controller._toggle[self.controller._tkey(13)])

    def test_swap_exchanges_two_configured_keys(self) -> None:
        page = self.config.pages[0]
        previous = KeyConfig(action="nav.page.previous")
        following = KeyConfig(action="nav.page.next")
        page.set_key(0, previous)
        page.set_key(14, following)
        self.config.save = lambda: None
        self.controller.refresh = lambda: None

        self.controller.swap_keys(14, 0)

        self.assertIs(page.key(0), following)
        self.assertIs(page.key(14), previous)


class BusyRenderingTests(unittest.TestCase):
    def test_busy_phases_create_a_subtle_visible_change(self) -> None:
        low = compose(bg="#141418", busy=True, busy_phase=False)
        high = compose(bg="#141418", busy=True, busy_phase=True)

        low_center = low.getpixel((36, 36))
        high_center = high.getpixel((36, 36))
        low_halo = low.getpixel((36, 1))
        high_halo = high.getpixel((36, 1))

        self.assertNotEqual(low_halo, high_halo)
        self.assertNotEqual(low_center, high_center)
        self.assertLessEqual(
            max(abs(a - b) for a, b in zip(low_center, high_center)),
            8,
        )

    def test_centered_clock_text_replaces_the_icon_area(self) -> None:
        empty = compose(bg="#141418")
        clock = compose(
            bg="#141418",
            icon_path="mdi:clock-outline",
            center_text="12:34:56",
        )

        self.assertNotEqual(empty.tobytes(), clock.tobytes())


if __name__ == "__main__":
    unittest.main()
