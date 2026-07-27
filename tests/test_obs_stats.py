"""Live OBS measurements drawn on a key.

Two things make this different from every other action. Its value changes with
nothing happening, so no bus event announces it and the controller has to
repaint it on a clock. And several keys can ask for it at once, so the sample
is cached in the client: without that, a page showing six statistics keys would
fire eighteen websocket requests per repaint through the one serialized
connection, competing with the feedback of every other key.
"""

from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace

from linuxstreamdeck import basic_actions as _basic_actions  # noqa: F401
from linuxstreamdeck.core import actions as registry
from linuxstreamdeck.core.config import (
    KIND_MULTI,
    KIND_SINGLE,
    ActionStep,
    Config,
    KeyConfig,
)
from linuxstreamdeck.core.controller import (
    STATS_ACTION_ID,
    STATS_REFRESH_SECONDS,
    DeckController,
)
from linuxstreamdeck.core.events import EventBus
from linuxstreamdeck.obs import actions as _obs_actions  # noqa: F401
from linuxstreamdeck.obs.actions import NO_VALUE, STAT_METRICS
from linuxstreamdeck.obs.client import OBSClient


class FakeDeck:
    """Enough of DeckManager for the controller to render into nothing."""

    key_count = 15
    image_size = (72, 72)
    columns = 5
    dial_count = 0

    def __init__(self) -> None:
        self.images: dict[int, bytes] = {}
        self.screensaver_active = False

    def set_key_image(self, index, image) -> None:
        self.images[index] = image

    def record_activity(self) -> bool:
        return self.screensaver_active

    def set_brightness(self, _value) -> None:
        pass

    def configure_screensaver(self, *_args) -> None:
        pass


FULL_SAMPLE = {
    "cpu": 82.4,
    "memory_mb": 1200,
    "disk_mb": 1536,
    "fps": 60.0,
    "render_skipped": 3,
    "render_total": 12000,
    "streaming": True,
    "stream_seconds": 4517.0,
    "stream_skipped": 62,
    "stream_total": 9000,
    "congestion": 0.31,
    "recording": True,
    "record_seconds": 754.0,
    "bitrate_kbps": 6234.0,
}


def context(sample: dict | None = FULL_SAMPLE, connected: bool = True):
    messages: list[str] = []
    return SimpleNamespace(
        obs=SimpleNamespace(connected=connected, stats=lambda: sample or {}),
        bus=SimpleNamespace(
            emit=lambda _t, **kw: messages.append(kw.get("text", ""))
        ),
        messages=messages,
    )


class MetricTests(unittest.TestCase):
    def setUp(self) -> None:
        self.action = registry.get(STATS_ACTION_ID)

    def test_the_action_is_registered(self) -> None:
        self.assertIsNotNone(self.action)

    @staticmethod
    def _obs_metrics() -> list[str]:
        """The metrics that come from an OBS sample.

        System readings come from the kernel instead, so a sample says nothing
        about them; they have their own tests.
        """
        return [
            key
            for key, metric in STAT_METRICS.items()
            if metric.get("needs_obs", True)
        ]

    def test_every_metric_renders_a_value(self) -> None:
        ctx = context()
        for metric in self._obs_metrics():
            with self.subTest(metric=metric):
                display = self.action.feedback(ctx, {"metric": metric})["display"]
                self.assertNotEqual(display, NO_VALUE)
                self.assertTrue(display)

    def test_values_are_formatted_for_a_72_pixel_key(self) -> None:
        """A number nobody can read is worse than no key at all."""
        ctx = context()
        for metric in self._obs_metrics():
            with self.subTest(metric=metric):
                display = self.action.feedback(ctx, {"metric": metric})["display"]
                self.assertLessEqual(len(display), 8)

    def test_a_small_percentage_keeps_a_decimal(self) -> None:
        """Rounding to whole numbers printed both 1.4% and 0.6% as "1%"."""
        ctx = context({"cpu": 1.4})

        self.assertEqual(
            self.action.feedback(ctx, {"metric": "cpu"})["display"], "1.4%"
        )

    def test_a_large_percentage_drops_the_decimal(self) -> None:
        ctx = context({"cpu": 82.4})

        self.assertEqual(
            self.action.feedback(ctx, {"metric": "cpu"})["display"], "82%"
        )

    def test_obs_cpu_says_it_is_obs(self) -> None:
        """It measures the OBS process, not the machine, and must say so."""
        self.assertIn("OBS", STAT_METRICS["cpu"]["label"])

    def test_obs_memory_is_offered(self) -> None:
        ctx = context({"memory_mb": 1536})

        self.assertEqual(
            self.action.feedback(ctx, {"metric": "memory"})["display"], "1.5GB"
        )

    def test_known_samples_produce_the_expected_text(self) -> None:
        ctx = context()
        expected = {
            "dropped": "0.7%",
            "bitrate": "6.2Mb",
            "congestion": "31%",
            "stream_time": "1:15:17",
            "record_time": "12:34",
            "disk": "2GB",
            "cpu": "82%",
            "fps": "60",
        }
        for metric, text in expected.items():
            with self.subTest(metric=metric):
                self.assertEqual(
                    self.action.feedback(ctx, {"metric": metric})["display"], text
                )

    def test_a_short_stream_drops_the_hour(self) -> None:
        ctx = context({"streaming": True, "stream_seconds": 95.0})

        self.assertEqual(
            self.action.feedback(ctx, {"metric": "stream_time"})["display"],
            "01:35",
        )

    def test_bitrate_falls_back_to_kilobits_when_small(self) -> None:
        ctx = context({"bitrate_kbps": 850.0})

        self.assertEqual(
            self.action.feedback(ctx, {"metric": "bitrate"})["display"], "850kb"
        )

    def test_disk_space_switches_to_megabytes_when_low(self) -> None:
        ctx = context({"disk_mb": 700})

        self.assertEqual(
            self.action.feedback(ctx, {"metric": "disk"})["display"], "700MB"
        )

    # ---------- nothing to show ----------

    def test_a_disconnected_obs_shows_no_value(self) -> None:
        ctx = context(connected=False)

        self.assertEqual(
            self.action.feedback(ctx, {"metric": "cpu"})["display"], NO_VALUE
        )

    def test_a_missing_measurement_shows_no_value(self) -> None:
        ctx = context({"cpu": None})

        self.assertEqual(
            self.action.feedback(ctx, {"metric": "cpu"})["display"], NO_VALUE
        )

    def test_time_is_blank_while_the_output_is_stopped(self) -> None:
        ctx = context({"streaming": False, "stream_seconds": 4517.0})

        self.assertEqual(
            self.action.feedback(ctx, {"metric": "stream_time"})["display"],
            NO_VALUE,
        )

    def test_a_frame_count_of_zero_does_not_divide_by_zero(self) -> None:
        """OBS reports zero totals for the first moment of an output."""
        ctx = context({"stream_skipped": 0, "stream_total": 0})

        self.assertEqual(
            self.action.feedback(ctx, {"metric": "dropped"})["display"], "0.0%"
        )

    def test_an_unknown_metric_renders_nothing_rather_than_raising(self) -> None:
        self.assertEqual(self.action.feedback(context(), {"metric": "made up"}), {})

    def test_a_key_saved_without_a_metric_still_works(self) -> None:
        self.assertIn("display", self.action.feedback(context(), {}))

    # ---------- colour ----------

    def test_a_healthy_value_is_calm_and_a_bad_one_is_not(self) -> None:
        calm = self.action.feedback(context({"cpu": 12.0}), {"metric": "cpu"})
        bad = self.action.feedback(context({"cpu": 95.0}), {"metric": "cpu"})

        self.assertNotEqual(calm["color"], bad["color"])

    def test_severity_has_three_steps(self) -> None:
        colors = {
            self.action.feedback(context({"cpu": value}), {"metric": "cpu"})["color"]
            for value in (10.0, 75.0, 95.0)
        }

        self.assertEqual(len(colors), 3)

    def test_running_out_of_disk_warns_before_the_last_gigabyte(self) -> None:
        """Filling the disk mid-recording is a classic way to lose a session."""
        plenty = self.action.feedback(context({"disk_mb": 200000}), {"metric": "disk"})
        low = self.action.feedback(context({"disk_mb": 5000}), {"metric": "disk"})

        self.assertNotEqual(plenty["color"], low["color"])

    def test_colour_can_be_turned_off(self) -> None:
        state = self.action.feedback(
            context({"cpu": 95.0}), {"metric": "cpu", "colored": "no"}
        )

        self.assertNotIn("color", state)

    def test_a_metric_with_no_threshold_is_never_coloured(self) -> None:
        state = self.action.feedback(context(), {"metric": "fps"})

        self.assertNotIn("color", state)

    # ---------- pressing it ----------

    def test_pressing_reports_the_measurement(self) -> None:
        """A key that does nothing at all on press feels broken."""
        ctx = context()

        self.action.execute(ctx, {"metric": "cpu"})

        self.assertTrue(ctx.messages)
        self.assertIn("CPU usage", ctx.messages[0])

    def test_pressing_says_so_when_there_is_nothing_to_report(self) -> None:
        ctx = context(connected=False)

        self.action.execute(ctx, {"metric": "cpu"})

        self.assertIn("not available", ctx.messages[0])


class SystemCpuTests(unittest.TestCase):
    """The whole-machine reading, which is what a system monitor shows.

    OBS reports only its own process, so someone comparing that against their
    system monitor is right to think the numbers disagree. They measure
    different things, and both are offered under names that say which.
    """

    def setUp(self) -> None:
        from linuxstreamdeck.core import sysstats

        self.sysstats = sysstats
        # The module holds its own state and reader, so both are put back:
        # leaking a fixture out of one test blinded the one that reads the
        # real kernel.
        original_reader = sysstats._read_totals
        original_interval = sysstats.MIN_INTERVAL

        def restore() -> None:
            sysstats._read_totals = original_reader
            sysstats.MIN_INTERVAL = original_interval
            sysstats.reset()

        sysstats.reset()
        self.addCleanup(restore)
        self.action = registry.get(STATS_ACTION_ID)

    def _feed(self, samples) -> None:
        """Drive the reader from fixed jiffy counters instead of /proc."""
        self._samples = list(samples)
        self.sysstats._read_totals = lambda: (
            self._samples.pop(0) if self._samples else None
        )
        self.sysstats.MIN_INTERVAL = 0.0

    def test_the_first_reading_has_nothing_to_compare_against(self) -> None:
        """Use since boot is not use now."""
        self._feed([(1000, 900)])

        self.assertIsNone(self.sysstats.cpu_percent())

    def test_the_second_reading_is_the_load_between_them(self) -> None:
        # 100 jiffies passed, 75 of them idle: a quarter of the machine busy.
        self._feed([(1000, 900), (1100, 975)])
        self.sysstats.cpu_percent()

        self.assertAlmostEqual(self.sysstats.cpu_percent(), 25.0, places=3)

    def test_a_fully_idle_machine_reads_zero(self) -> None:
        self._feed([(1000, 900), (1100, 1000)])
        self.sysstats.cpu_percent()

        self.assertAlmostEqual(self.sysstats.cpu_percent(), 0.0, places=3)

    def test_a_saturated_machine_reads_one_hundred(self) -> None:
        self._feed([(1000, 900), (1100, 900)])
        self.sysstats.cpu_percent()

        self.assertAlmostEqual(self.sysstats.cpu_percent(), 100.0, places=3)

    def test_no_elapsed_time_keeps_the_previous_answer(self) -> None:
        self._feed([(1000, 900), (1100, 975), (1100, 975)])
        self.sysstats.cpu_percent()
        value = self.sysstats.cpu_percent()

        self.assertEqual(self.sysstats.cpu_percent(), value)

    def test_an_unreadable_proc_answers_nothing(self) -> None:
        self._feed([])

        self.assertIsNone(self.sysstats.cpu_percent())

    def test_it_reports_a_plausible_value_on_this_machine(self) -> None:
        """Against the real kernel, not a fixture."""
        import time

        self.sysstats.MIN_INTERVAL = 0.0
        value = None
        # Jiffies advance in ticks, so back-to-back reads can see no elapsed
        # time at all; a short pause guarantees there is something to divide.
        for _ in range(3):
            self.sysstats.cpu_percent()
            time.sleep(0.02)
            value = self.sysstats.cpu_percent()
            if value is not None:
                break

        self.assertIsNotNone(value)
        self.assertGreaterEqual(value, 0.0)
        self.assertLessEqual(value, 100.0)

    def test_the_key_keeps_working_while_obs_is_closed(self) -> None:
        """It comes from the kernel, so OBS being shut has nothing to do with it."""
        self._feed([(1000, 900), (1100, 975)])
        self.sysstats.cpu_percent()
        ctx = context(connected=False)

        display = self.action.feedback(ctx, {"metric": "system_cpu"})["display"]

        self.assertNotEqual(display, NO_VALUE)

    def test_it_is_named_apart_from_the_obs_reading(self) -> None:
        labels = {STAT_METRICS["cpu"]["label"], STAT_METRICS["system_cpu"]["label"]}

        self.assertEqual(len(labels), 2)
        self.assertIn("System", STAT_METRICS["system_cpu"]["label"])


class FakeReqClient:
    """Counts requests, so the cache can be shown to be doing its job."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.bytes_sent = 1_000_000

    def send(self, name, data=None, raw=True):
        self.calls.append(name)
        if name == "GetStats":
            return {"cpuUsage": 12.0, "availableDiskSpace": 50000.0,
                    "activeFps": 60.0, "renderSkippedFrames": 1,
                    "renderTotalFrames": 1000}
        if name == "GetStreamStatus":
            return {"outputActive": True, "outputDuration": 60000,
                    "outputBytes": self.bytes_sent, "outputSkippedFrames": 2,
                    "outputTotalFrames": 1800, "outputCongestion": 0.1}
        if name == "GetRecordStatus":
            return {"outputActive": False, "outputDuration": 0}
        return {}


class StatsCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = OBSClient(EventBus())
        self.req = FakeReqClient()
        self.client._req = self.req
        self.client.connected = True

    def test_a_sample_is_taken_from_obs(self) -> None:
        sample = self.client.stats()

        self.assertEqual(sample["cpu"], 12.0)
        self.assertTrue(sample["streaming"])

    def test_repeated_calls_within_the_interval_reuse_one_sample(self) -> None:
        for _ in range(6):
            self.client.stats()

        self.assertEqual(len(self.req.calls), 3, self.req.calls)

    def test_a_disconnected_client_asks_obs_nothing(self) -> None:
        self.client.connected = False

        self.assertEqual(self.client.stats(), {})
        self.assertEqual(self.req.calls, [])

    def test_bitrate_needs_two_samples(self) -> None:
        """A counter that started at zero would report an absurd first rate."""
        first = self.client.stats()

        self.assertIsNone(first["bitrate_kbps"])

    def test_bitrate_is_derived_from_the_bytes_between_samples(self) -> None:
        self.client.stats()
        self.client._stats_at = 0.0          # let the next call resample
        self.req.bytes_sent += 750_000

        second = self.client.stats()

        self.assertIsNotNone(second["bitrate_kbps"])
        self.assertGreater(second["bitrate_kbps"], 0)

    def test_a_counter_that_went_backwards_reports_nothing(self) -> None:
        """OBS restarts the byte count on every new stream."""
        self.client.stats()
        self.client._stats_at = 0.0
        self.req.bytes_sent = 5

        self.assertIsNone(self.client.stats()["bitrate_kbps"])

    def test_stopping_the_stream_forgets_the_previous_bytes(self) -> None:
        self.client.stats()

        self.client._stream_bitrate(None, 10.0, streaming=False)

        self.assertIsNone(self.client._stream_bytes)

    def test_the_cache_is_safe_to_read_from_several_threads(self) -> None:
        errors: list[BaseException] = []

        def hammer():
            try:
                for _ in range(40):
                    self.client.stats()
            except BaseException as error:      # noqa: BLE001
                errors.append(error)

        threads = [threading.Thread(target=hammer) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])


class StatsRefreshTests(unittest.TestCase):
    """Which keys the periodic repaint picks up."""

    def setUp(self) -> None:
        self.config = Config()
        self.controller = DeckController(
            self.config,
            EventBus(),
            SimpleNamespace(connected=True, stats=lambda: FULL_SAMPLE),
            FakeDeck(),
        )
        self.addCleanup(self.controller.shutdown)
        self.page = self.config.pages[0]

    def test_a_statistics_key_is_picked_up(self) -> None:
        self.page.set_key(
            3, KeyConfig(kind=KIND_SINGLE, action=STATS_ACTION_ID)
        )

        keys = self.controller._live_keys()

        self.assertEqual(list(keys), [self.controller._tkey(3)])

    def test_a_page_without_one_costs_nothing(self) -> None:
        self.page.set_key(1, KeyConfig(kind=KIND_SINGLE, action="obs.record"))

        self.assertEqual(self.controller._live_keys(), {})

    def test_every_statistics_key_on_the_page_is_found(self) -> None:
        for index in (0, 4, 7):
            self.page.set_key(
                index, KeyConfig(kind=KIND_SINGLE, action=STATS_ACTION_ID)
            )

        self.assertEqual(len(self.controller._live_keys()), 3)

    def test_a_statistics_step_inside_a_list_is_not_one(self) -> None:
        """Only a key's own action produces feedback, never a step of a list."""
        self.page.set_key(
            2,
            KeyConfig(
                kind=KIND_MULTI, steps=[ActionStep(action=STATS_ACTION_ID)]
            ),
        )

        self.assertEqual(self.controller._live_keys(), {})

    def test_a_statistics_key_asks_for_the_statistics_rate(self) -> None:
        self.page.set_key(
            3, KeyConfig(kind=KIND_SINGLE, action=STATS_ACTION_ID)
        )

        keys = self.controller._live_keys()

        self.assertEqual(list(keys.values()), [STATS_REFRESH_SECONDS])

    def test_the_repaint_thread_is_joined_on_shutdown(self) -> None:
        """It submits to the render executor, so it may not outlive it."""
        controller = DeckController(
            self.config, EventBus(), SimpleNamespace(connected=False), FakeDeck()
        )
        thread = controller._live_thread
        self.assertTrue(thread.is_alive())

        controller.shutdown()

        self.assertFalse(thread.is_alive())


if __name__ == "__main__":
    unittest.main()
