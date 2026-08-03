"""Machine measurements on a key, read straight from the kernel.

Every reading is fed from a temporary directory standing in for `/proc` and
`/sys`, so nothing here depends on the machine it runs on -- which matters,
because the interesting cases are the ones this laptop does not have: a
desktop with no package sensor, an integrated GPU with no busy counter, a
kernel too old for MemAvailable.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace

from linuxstreamdeck import system_stats
from linuxstreamdeck.core import sysstats
from linuxstreamdeck.core.actions import REGISTRY

MEMINFO = """MemTotal:       32555812 kB
MemFree:          679976 kB
MemAvailable:   24537028 kB
Buffers:          460784 kB
Cached:          3843060 kB
"""

NET_DEV = """Inter-|   Receive                    |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes packets errs drop fifo colls carrier compressed
    lo: 4737795   32908    0    0    0     0          0         0  4737795   32908    0    0    0     0       0        0
wlp0s20f3: 1000000   900    0    0    0     0          0         0   500000     400    0    0    0     0       0        0
enp43s0:       0       0    0    0    0     0          0         0        0       0    0    0    0     0       0        0
docker0:  99999     10    0    0    0     0          0         0    88888      10    0    0    0     0       0        0
"""


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


class SysfsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        sysstats.reset()
        self.addCleanup(sysstats.reset)

    def patch(self, name: str, value) -> None:
        patch = unittest.mock.patch.object(sysstats, name, value)
        patch.start()
        self.addCleanup(patch.stop)

    def hwmon(self, chips: dict[str, dict[str, str]]) -> Path:
        """Build a /sys/class/hwmon tree: {chip name: {file: contents}}."""
        root = self.root / "hwmon"
        for index, (name, files) in enumerate(chips.items()):
            chip = root / f"hwmon{index}"
            write(chip / "name", name)
            for filename, contents in files.items():
                write(chip / filename, contents)
        root.mkdir(parents=True, exist_ok=True)
        self.patch("HWMON_ROOT", root)
        return root


class MemoryTests(SysfsTestCase):
    def test_used_comes_from_available_not_from_free(self) -> None:
        """Free memory on a healthy Linux box is nearly zero because the
        kernel spends it on cache. A key built on MemFree reads 98 % on a
        machine doing nothing, and is worse than no key."""
        self.patch("PROC_MEMINFO", write(self.root / "meminfo", MEMINFO))

        used, total = sysstats.memory()

        self.assertAlmostEqual(total, 32555812 / 1024, places=3)
        self.assertAlmostEqual(used, (32555812 - 24537028) / 1024, places=3)
        self.assertLess(sysstats.memory_percent(), 50)

    def test_a_kernel_without_memavailable_still_answers(self) -> None:
        older = "\n".join(
            line for line in MEMINFO.splitlines()
            if not line.startswith("MemAvailable")
        )
        self.patch("PROC_MEMINFO", write(self.root / "meminfo", older))

        used, _total = sysstats.memory()

        # free + cached + buffers is what the kernel documents as the
        # approximation for those, so used is what is left.
        expected = (32555812 - (679976 + 3843060 + 460784)) / 1024
        self.assertAlmostEqual(used, expected, places=3)

    def test_an_unreadable_meminfo_answers_nothing(self) -> None:
        self.patch("PROC_MEMINFO", self.root / "gone")

        self.assertIsNone(sysstats.memory())
        self.assertIsNone(sysstats.memory_percent())

    def test_rubbish_never_raises(self) -> None:
        self.patch("PROC_MEMINFO", write(self.root / "meminfo", "nonsense\n:\n"))

        self.assertIsNone(sysstats.memory())


class NetworkTests(SysfsTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.path = write(self.root / "net_dev", NET_DEV)
        self.patch("PROC_NET_DEV", self.path)

    def test_loopback_and_containers_are_not_offered(self) -> None:
        """Nobody means `lo` or `docker0` by "my connection"."""
        self.assertEqual(
            sysstats.network_interfaces(), ["wlp0s20f3", "enp43s0"]
        )

    def test_the_busiest_interface_comes_first(self) -> None:
        """A laptop lists several and the alphabetical first is rarely the
        one in use."""
        self.assertEqual(sysstats.network_interfaces()[0], "wlp0s20f3")

    def test_the_first_reading_answers_nothing(self) -> None:
        """Throughput only exists as a difference between two samples;
        reporting the first would be an average since boot."""
        self.assertIsNone(sysstats.network_rate("wlp0s20f3"))

    def test_the_second_reading_is_a_rate(self) -> None:
        with unittest.mock.patch.object(
            sysstats.time, "monotonic", side_effect=[100.0, 110.0]
        ):
            sysstats.network_rate("wlp0s20f3")
            write(self.path, NET_DEV.replace("1000000   900", "1010000   950"))

            down, up = sysstats.network_rate("wlp0s20f3")

        self.assertAlmostEqual(down, 1000.0)      # 10000 bytes over 10 s
        self.assertAlmostEqual(up, 0.0)

    def test_a_counter_that_went_backwards_is_not_a_spike(self) -> None:
        """An interface that was reset or replaced. Reporting the negative
        jump as a rate would show an absurd number."""
        with unittest.mock.patch.object(
            sysstats.time, "monotonic", side_effect=[100.0, 110.0]
        ):
            sysstats.network_rate("wlp0s20f3")
            write(self.path, NET_DEV.replace("1000000   900", "10   9"))

            down, _up = sysstats.network_rate("wlp0s20f3")

        self.assertEqual(down, 0.0)

    def test_no_interface_means_every_usable_one(self) -> None:
        with unittest.mock.patch.object(
            sysstats.time, "monotonic", side_effect=[100.0, 110.0]
        ):
            sysstats.network_rate()
            write(self.path, NET_DEV.replace("1000000   900", "1010000   950"))

            down, _up = sysstats.network_rate()

        self.assertAlmostEqual(down, 1000.0)

    def test_an_interface_that_is_gone_answers_nothing(self) -> None:
        self.assertIsNone(sysstats.network_rate("wwan0"))

    def test_an_unreadable_file_answers_nothing(self) -> None:
        self.patch("PROC_NET_DEV", self.root / "gone")

        self.assertIsNone(sysstats.network_rate())
        self.assertEqual(sysstats.network_interfaces(), [])


class InterfaceNameTests(SysfsTestCase):
    """`enx00e04c3676eb` is derived from a MAC address and tells nobody
    anything. The key still stores that name -- it is what /proc/net/dev is
    keyed by -- and only the editor shows something recognisable."""

    def net(self, interfaces: dict) -> Path:
        """Build a /sys/class/net tree: {name: {relative path: contents}}."""
        root = self.root / "net"
        for name, files in interfaces.items():
            for relative, contents in files.items():
                write(root / name / relative, contents)
            (root / name).mkdir(parents=True, exist_ok=True)
        root.mkdir(parents=True, exist_ok=True)
        self.patch("SYS_NET", root)
        return root

    def test_a_usb_adapter_says_what_it_really_is(self) -> None:
        """The best case there is: the device publishes a product string, and
        the MAC-derived name it was given is meaningless."""
        self.net({"enx00e04c3676eb": {
            "device/../product": "USB 10/100 LAN",
            "device/../manufacturer": "Realtek",
        }})

        self.assertEqual(
            sysstats.interface_label("enx00e04c3676eb"),
            "Realtek USB 10/100 LAN (enx00e04c3676eb)",
        )

    def test_a_maker_already_in_the_product_is_not_repeated(self) -> None:
        self.net({"enx0": {
            "device/../product": "Realtek USB LAN",
            "device/../manufacturer": "Realtek",
        }})

        self.assertEqual(
            sysstats.interface_label("enx0"), "Realtek USB LAN (enx0)"
        )

    def test_a_product_with_no_maker_still_works(self) -> None:
        self.net({"enx0": {"device/../product": "USB LAN"}})

        self.assertEqual(sysstats.interface_label("enx0"), "USB LAN (enx0)")

    def test_wireless_is_recognised_from_the_kernel(self) -> None:
        """The `wireless` directory, not the name: a renamed interface is
        still Wi-Fi."""
        self.net({"myrouter": {"wireless/x": "", "uevent": "INTERFACE=myrouter"}})

        self.assertEqual(
            sysstats.interface_label("myrouter"), "Wi-Fi (myrouter)"
        )

    def test_the_uevent_devtype_is_read_too(self) -> None:
        """The interface is deliberately named something the prefix table
        cannot recognise, or that table answers and this branch is never the
        thing under test."""
        self.net({"net0": {"uevent": "DEVTYPE=wlan\nINTERFACE=net0"}})

        self.assertEqual(sysstats.interface_label("net0"), "Wi-Fi (net0)")

    def test_mobile_broadband_is_named(self) -> None:
        self.net({"mob0": {"uevent": "DEVTYPE=wwan\nINTERFACE=mob0"}})

        self.assertEqual(
            sysstats.interface_label("mob0"), "Mobile broadband (mob0)"
        )

    def test_the_usual_mobile_name_works_too(self) -> None:
        self.net({"wwan0": {"uevent": "INTERFACE=wwan0"}})

        self.assertEqual(
            sysstats.interface_label("wwan0"), "Mobile broadband (wwan0)"
        )

    def test_no_prefix_shadows_another_with_a_different_answer(self) -> None:
        """The table is scanned in order, so a prefix of another entry would
        make that entry unreachable. Nothing currently overlaps, and this is
        what keeps it that way when one is added."""
        for prefix, kind in sysstats.NAME_PREFIXES:
            for other, other_kind in sysstats.NAME_PREFIXES:
                if prefix == other:
                    continue
                with self.subTest(prefix=prefix, other=other):
                    if other.startswith(prefix):
                        self.assertEqual(kind, other_kind)

    def test_a_pci_card_falls_back_to_the_kind_of_link(self) -> None:
        """It publishes only numeric vendor and device ids, which would need a
        hardware database to read."""
        self.net({"enp43s0": {"uevent": "INTERFACE=enp43s0"}})

        self.assertEqual(
            sysstats.interface_label("enp43s0"), "Ethernet (enp43s0)"
        )

    def test_an_old_style_name_is_understood(self) -> None:
        self.net({"eth0": {"uevent": "INTERFACE=eth0"}})

        self.assertEqual(sysstats.interface_label("eth0"), "Ethernet (eth0)")

    def test_something_unrecognised_shows_as_itself(self) -> None:
        """Rather than being guessed at, or shown as blank."""
        self.net({"zzz0": {"uevent": "INTERFACE=zzz0"}})

        self.assertEqual(sysstats.interface_label("zzz0"), "zzz0")

    def test_an_unreadable_sysfs_never_raises(self) -> None:
        self.patch("SYS_NET", self.root / "gone")

        self.assertEqual(sysstats.interface_label("eth0"), "Ethernet (eth0)")

    def test_nothing_stays_nothing(self) -> None:
        self.assertEqual(sysstats.interface_label(""), "")
        self.assertEqual(sysstats.interface_label(None), "")

    def test_the_name_is_always_still_on_the_label(self) -> None:
        """Two USB adapters of the same model would otherwise be identical in
        the list, and the stored value is the name."""
        self.net({"enx0": {"device/../product": "USB LAN"}})

        self.assertIn("enx0", sysstats.interface_label("enx0"))


class InterfaceDropdownTests(unittest.TestCase):
    def test_the_editor_shows_the_label_and_stores_the_name(self) -> None:
        from linuxstreamdeck.ui.steps import _display_options

        with unittest.mock.patch.object(
            sysstats, "interface_label", lambda name: f"Wi-Fi ({name})"
        ):
            labels, values = _display_options("network_interfaces", ["wlp0s20f3"])

        self.assertEqual(labels, ["Wi-Fi (wlp0s20f3)"])
        self.assertEqual(values, {"Wi-Fi (wlp0s20f3)": "wlp0s20f3"})


class TemperatureTests(SysfsTestCase):
    def test_the_package_is_preferred_over_a_single_core(self) -> None:
        """One core spiking is normal and says nothing; the package is where
        a cooling problem shows up.

        The core is deliberately the one that sorts *first*. On a real chip it
        does: `temp10_input` precedes `temp1_input` because "0" sorts before
        "_", so a version that simply took the first labelled input would look
        correct on a tidy fixture and report a core on real hardware.
        """
        self.hwmon({
            "coretemp": {
                "temp0_label": "Core 0", "temp0_input": "95000",
                "temp1_label": "Package id 0", "temp1_input": "50000",
            },
        })

        self.assertEqual(sysstats.cpu_temperature(), 50.0)

    def test_the_real_sort_order_puts_cores_before_the_package(self) -> None:
        """The reason the test above is built the way it is."""
        names = sorted(["temp1_input", "temp10_input", "temp14_input"])

        self.assertEqual(names[-1], "temp1_input")

    def test_an_amd_chip_is_read_too(self) -> None:
        self.hwmon({"k10temp": {"temp1_label": "Tctl", "temp1_input": "61000"}})

        self.assertEqual(sysstats.cpu_temperature(), 61.0)

    def test_a_processor_sensor_wins_over_the_motherboard_one(self) -> None:
        self.hwmon({
            "acpitz": {"temp1_input": "40000"},
            "coretemp": {"temp1_label": "Package id 0", "temp1_input": "70000"},
        })

        self.assertEqual(sysstats.cpu_temperature(), 70.0)

    def test_an_unlabelled_chip_falls_back_to_its_first_reading(self) -> None:
        """Plenty of desktop boards label nothing at all."""
        self.hwmon({"coretemp": {"temp1_input": "44000"}})

        self.assertEqual(sysstats.cpu_temperature(), 44.0)

    def test_a_machine_with_no_sensor_answers_nothing(self) -> None:
        self.hwmon({"BAT1": {"temp1_input": "30000"}})

        self.assertIsNone(sysstats.cpu_temperature())

    def test_a_missing_hwmon_tree_never_raises(self) -> None:
        self.patch("HWMON_ROOT", self.root / "gone")

        self.assertIsNone(sysstats.cpu_temperature())


class GpuTests(SysfsTestCase):
    def _nvidia(self, stdout: str, code: int = 0):
        return unittest.mock.patch.multiple(
            sysstats,
            shutil=SimpleNamespace(
                which=lambda name: "/usr/bin/nvidia-smi",
                disk_usage=sysstats.shutil.disk_usage,
            ),
            subprocess=SimpleNamespace(
                run=lambda *a, **k: subprocess.CompletedProcess([], code, stdout, ""),
                SubprocessError=subprocess.SubprocessError,
            ),
        )

    def test_an_nvidia_card_reports_all_three(self) -> None:
        with self._nvidia("0, 2048, 4096, 49\n"):
            reading = sysstats.gpu()

        self.assertEqual(reading["percent"], 0.0)
        self.assertEqual(reading["memory_percent"], 50.0)
        self.assertEqual(reading["temperature"], 49.0)

    def test_a_failing_tool_answers_nothing_rather_than_zero(self) -> None:
        """A zero is a claim: it says the GPU is idle, not that nobody asked."""
        with self._nvidia("", code=9):
            self.assertEqual(sysstats.gpu(), {})

    def test_unexpected_output_answers_nothing(self) -> None:
        with self._nvidia("something went wrong\n"):
            self.assertEqual(sysstats.gpu(), {})

    def test_a_reading_is_shared_rather_than_taken_per_key(self) -> None:
        """nvidia-smi is a process. A page of keys must not each spawn one."""
        runs: list = []

        def counted():
            runs.append(1)
            return {"percent": 5.0}

        with unittest.mock.patch.object(sysstats, "_read_gpu", counted):
            for _ in range(10):
                sysstats.gpu()

        self.assertEqual(len(runs), 1)


class MetricTests(unittest.TestCase):
    """The table, read the way the action reads it."""

    def setUp(self) -> None:
        self.action = REGISTRY["sys.stats"]
        self.messages: list[str] = []
        self.ctx = SimpleNamespace(
            bus=SimpleNamespace(
                emit=lambda topic, **d: self.messages.append(d.get("text", ""))
            )
        )

    def _feedback(self, metric: str, value, **extra):
        with unittest.mock.patch.dict(
            system_stats.METRICS[metric], {"read": lambda _p: value}
        ):
            return self.action.feedback(self.ctx, {"metric": metric, **extra})

    def test_a_measurement_the_machine_lacks_shows_a_dash(self) -> None:
        """A zero would say the GPU is idle rather than absent."""
        self.assertEqual(
            self._feedback("gpu", None)["display"], system_stats.NO_VALUE
        )

    def test_a_percentage_keeps_a_decimal_while_it_is_small(self) -> None:
        self.assertEqual(self._feedback("cpu", 1.4)["display"], "1.4%")
        self.assertEqual(self._feedback("cpu", 43.2)["display"], "43%")

    def test_a_temperature_reads_as_degrees(self) -> None:
        self.assertEqual(self._feedback("cpu_temp", 61.4)["display"], "61°")

    def test_throughput_is_shown_in_bits_not_bytes(self) -> None:
        """A connection is sold in megabits. Bytes invite exactly the wrong
        conclusion about whether a line is keeping up."""
        self.assertEqual(
            self._feedback("net_up", 1_000_000)["display"], "8.0Mb"
        )

    def test_a_size_switches_unit_where_it_gets_unreadable(self) -> None:
        self.assertEqual(self._feedback("memory_used", 512)["display"], "512M")
        self.assertEqual(self._feedback("memory_used", 7900)["display"], "7.7G")

    def test_a_hot_processor_is_coloured(self) -> None:
        self.assertEqual(
            self._feedback("cpu_temp", 95)["color"], system_stats.ALARM_COLOR
        )
        self.assertEqual(
            self._feedback("cpu_temp", 85)["color"], system_stats.WARN_COLOR
        )
        self.assertEqual(
            self._feedback("cpu_temp", 40)["color"], system_stats.OK_COLOR
        )

    def test_free_disk_warns_when_it_is_low_not_high(self) -> None:
        """The one metric in the table where a small number is the bad one."""
        self.assertEqual(
            self._feedback("disk", 1024)["color"], system_stats.ALARM_COLOR
        )
        self.assertEqual(
            self._feedback("disk", 500 * 1024)["color"], system_stats.OK_COLOR
        )

    def test_a_metric_with_no_threshold_is_never_painted(self) -> None:
        """Green reads as "checked, and fine". A measurement with no opinion
        must not claim that."""
        self.assertNotIn("color", self._feedback("net_down", 1000))

    def test_colouring_can_be_turned_off(self) -> None:
        self.assertNotIn("color", self._feedback("cpu", 99, colored="no"))

    def test_an_unavailable_value_is_never_coloured(self) -> None:
        self.assertNotIn("color", self._feedback("gpu", None))

    def test_every_metric_is_labelled_and_has_an_icon(self) -> None:
        for name, metric in system_stats.METRICS.items():
            with self.subTest(metric=name):
                self.assertTrue(metric["label"])
                self.assertTrue(metric["icon"].startswith("mdi:"))

    def test_an_unknown_metric_never_raises(self) -> None:
        self.assertEqual(self.action.feedback(self.ctx, {"metric": "moon"}), {})
        self.action.execute(self.ctx, {"metric": "moon"})

    def test_pressing_it_states_the_measurement(self) -> None:
        with unittest.mock.patch.dict(
            system_stats.METRICS["cpu"], {"read": lambda _p: 42.0}
        ):
            self.action.execute(self.ctx, {"metric": "cpu"})

        self.assertIn("CPU usage", self.messages[-1])
        self.assertIn("42%", self.messages[-1])

    def test_pressing_an_unavailable_one_says_so_in_words(self) -> None:
        """The only place with room to explain a dash."""
        with unittest.mock.patch.dict(
            system_stats.METRICS["gpu"], {"read": lambda _p: None}
        ):
            self.action.execute(self.ctx, {"metric": "gpu"})

        self.assertIn("not available", self.messages[-1])


class ActionWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.action = REGISTRY["sys.stats"]

    def test_it_never_needs_obs(self) -> None:
        """Every reading is the kernel's."""
        self.assertFalse(self.action.needs_obs)
        self.assertFalse(self.action.requires_obs({}))

    def test_it_lives_in_system_not_under_obs(self) -> None:
        """A key showing GPU temperature has nothing to do with OBS and must
        not have to be looked for there."""
        self.assertEqual(self.action.category, "System")

    def test_the_interface_field_only_applies_to_network_metrics(self) -> None:
        param = next(p for p in self.action.params if p.name == "interface")

        self.assertEqual(param.depends_on, "metric")
        self.assertEqual(
            list(param.depends_values), list(system_stats.NETWORK_METRICS)
        )

    def test_the_folder_field_only_applies_to_free_disk_space(self) -> None:
        param = next(p for p in self.action.params if p.name == "disk_folder")

        self.assertEqual(list(param.depends_values), list(system_stats.DISK_METRICS))
        self.assertTrue(param.directory)

    def test_the_interface_list_fills_without_obs(self) -> None:
        from linuxstreamdeck.ui.steps import LOCAL_CHOICE_SOURCES

        self.assertIn("network_interfaces", LOCAL_CHOICE_SOURCES)

    def test_it_repaints_on_the_clock(self) -> None:
        """Its value changes with nothing happening, so no event announces it."""
        from linuxstreamdeck.core.config import KIND_SINGLE, KeyConfig
        from linuxstreamdeck.core.controller import (
            STATS_REFRESH_SECONDS, DeckController,
        )

        controller = SimpleNamespace(
            obs=SimpleNamespace(connected=False), _twitch_linked=lambda: False
        )
        interval = DeckController._live_interval(
            controller,
            KeyConfig(kind=KIND_SINGLE, action="sys.stats", params={"metric": "cpu"}),
        )

        self.assertEqual(interval, STATS_REFRESH_SECONDS)

    def test_it_keeps_repainting_while_obs_is_closed(self) -> None:
        from linuxstreamdeck.core.config import KIND_SINGLE, KeyConfig
        from linuxstreamdeck.core.controller import DeckController

        controller = SimpleNamespace(
            obs=SimpleNamespace(connected=False), _twitch_linked=lambda: False
        )

        self.assertGreater(
            DeckController._live_interval(
                controller,
                KeyConfig(kind=KIND_SINGLE, action="sys.stats", params={}),
            ),
            0,
        )

    def test_the_two_shared_metrics_still_exist_under_obs(self) -> None:
        """Moving them would silently change what every key already using
        them shows. A duplicate dropdown entry costs nothing next to that."""
        from linuxstreamdeck.obs.actions import STAT_METRICS

        for shared in ("system_cpu", "disk"):
            with self.subTest(metric=shared):
                self.assertIn(shared, STAT_METRICS)
