"""Elgato Key Lights over the local network.

Nothing here needs a light: every answer is the shape the firmware really
returns, and every discovery line is real `avahi-browse -rpt` output captured
from this machine. Most of what is pinned is the two things that are silent
when wrong -- the mired conversion, whose direction is inverted twice, and the
rule that a `PUT` must carry only the field being changed.
"""

from __future__ import annotations

import json
import subprocess
import unittest
import unittest.mock
from types import SimpleNamespace

from linuxstreamdeck import light_actions  # noqa: F401
from linuxstreamdeck.core import keylight, webrequest
from linuxstreamdeck.core.actions import REGISTRY
from linuxstreamdeck.core.keylight import KeyLightError

# Two lights, exactly as avahi prints them. The second carries an escaped
# non-ASCII name, which avahi writes as decimal bytes.
BROWSE = (
    "+;wlp0s20f3;IPv4;Elgato Key Light Air 8CB5;_elg._tcp;local\n"
    "=;wlp0s20f3;IPv4;Elgato Key Light Air 8CB5;_elg._tcp;local;"
    "elgato-key-light-air-8cb5.local;192.168.1.50;9123;\"mf=Elgato\"\n"
    "=;wlp0s20f3;IPv6;Elgato Key Light Air 8CB5;_elg._tcp;local;"
    "elgato-key-light-air-8cb5.local;fe80::1;9123;\"mf=Elgato\"\n"
    "=;wlp0s20f3;IPv4;Luz Sal\\195\\179n;_elg._tcp;local;"
    "elgato-key-light-9f2a.local;192.168.1.51;9123;\"mf=Elgato\"\n"
)


def answer(on=1, brightness=30, temperature=213):
    """What GET/PUT /elgato/lights returns."""
    return json.dumps({
        "numberOfLights": 1,
        "lights": [{"on": on, "brightness": brightness,
                    "temperature": temperature}],
    })


class FakeHttp:
    """Stands in for webrequest.request and records what was sent."""

    def __init__(self, body=None, error=None) -> None:
        self.body = answer() if body is None else body
        self.error = error
        self.calls: list[tuple] = []

    def __call__(self, url, method="GET", headers=None, body="", timeout=None):
        self.calls.append((url, method, body, timeout))
        if self.error is not None:
            raise self.error
        return 200, self.body

    @property
    def sent(self) -> dict:
        """The JSON body of the last call."""
        return json.loads(self.calls[-1][2] or "{}")


class KeyLightTestCase(unittest.TestCase):
    def use(self, http: FakeHttp) -> FakeHttp:
        self.http = http
        patch = unittest.mock.patch.object(
            keylight.webrequest, "request", http
        )
        patch.start()
        self.addCleanup(patch.stop)
        keylight.forget_states()
        keylight.forget_discovery()
        self.addCleanup(keylight.forget_states)
        self.addCleanup(keylight.forget_discovery)
        return http

    def setUp(self) -> None:
        self.use(FakeHttp())


class TemperatureTests(unittest.TestCase):
    """Mireds, and the two inversions that make this easy to get backwards."""

    def test_the_ends_of_the_range_convert_both_ways(self) -> None:
        self.assertEqual(keylight.kelvin_to_device(7000), keylight.MIN_MIRED)
        self.assertEqual(keylight.kelvin_to_device(2900), keylight.MAX_MIRED)
        self.assertEqual(keylight.device_to_kelvin(keylight.MIN_MIRED), 7000)
        self.assertEqual(keylight.device_to_kelvin(keylight.MAX_MIRED), 2900)

    def test_a_higher_device_number_is_a_warmer_light(self) -> None:
        """The inversion that is silent when wrong: mireds run the opposite
        way to kelvin, so a naive scale sends "warmer" to daylight."""
        self.assertLess(
            keylight.kelvin_to_device(6500), keylight.kelvin_to_device(3000)
        )

    def test_kelvin_outside_the_range_is_clamped(self) -> None:
        self.assertEqual(keylight.kelvin_to_device(100), keylight.MAX_MIRED)
        self.assertEqual(keylight.kelvin_to_device(99999), keylight.MIN_MIRED)

    def test_a_round_trip_keeps_the_value_recognisable(self) -> None:
        for kelvin in range(3000, 7001, 500):
            with self.subTest(kelvin=kelvin):
                back = keylight.device_to_kelvin(
                    keylight.kelvin_to_device(kelvin)
                )
                self.assertLessEqual(abs(back - kelvin), keylight.KELVIN_STEP)


class AddressTests(KeyLightTestCase):
    def test_the_default_port_is_added(self) -> None:
        keylight.state("192.168.1.50")

        self.assertEqual(
            self.http.calls[-1][0], "http://192.168.1.50:9123/elgato/lights"
        )

    def test_an_explicit_port_is_kept(self) -> None:
        keylight.state("192.168.1.50:9999")

        self.assertIn(":9999/", self.http.calls[-1][0])

    def test_a_host_name_works_as_well_as_an_address(self) -> None:
        keylight.state("elgato-key-light-air-8cb5.local")

        self.assertIn("elgato-key-light-air-8cb5.local:9123", self.http.calls[-1][0])

    def test_a_key_with_no_light_says_so(self) -> None:
        with self.assertRaises(KeyLightError) as caught:
            keylight.state("")

        self.assertIn("no light", str(caught.exception))

    def test_a_url_is_refused_rather_than_doubled_up(self) -> None:
        with self.assertRaises(KeyLightError):
            keylight.state("http://192.168.1.50")

    def test_it_does_not_wait_the_full_web_timeout(self) -> None:
        """A light is on the same network. Anything slower is one that is
        unplugged, and a key must not hang finding that out."""
        keylight.state("192.168.1.50")

        self.assertEqual(self.http.calls[-1][3], keylight.TIMEOUT)
        self.assertLess(keylight.TIMEOUT, webrequest.REQUEST_TIMEOUT)


class StateTests(KeyLightTestCase):
    def test_it_reads_what_the_light_is_doing(self) -> None:
        self.use(FakeHttp(answer(on=1, brightness=42, temperature=213)))

        self.assertEqual(
            keylight.state("192.168.1.50"),
            {"on": True, "brightness": 42, "kelvin": 4700},
        )

    def test_something_that_is_not_a_light_is_reported_as_such(self) -> None:
        self.use(FakeHttp('{"hello": "world"}'))

        with self.assertRaises(KeyLightError) as caught:
            keylight.state("192.168.1.50")

        self.assertIn("not like a Key Light", str(caught.exception))

    def test_a_page_that_is_not_json_is_reported_as_such(self) -> None:
        self.use(FakeHttp("<html>router login</html>"))

        with self.assertRaises(KeyLightError):
            keylight.state("192.168.1.50")

    def test_an_unreachable_light_says_so(self) -> None:
        self.use(FakeHttp(error=webrequest.WebRequestError("timed out")))

        with self.assertRaises(KeyLightError) as caught:
            keylight.state("192.168.1.50")

        self.assertIn("Could not reach the light", str(caught.exception))


class ApplyTests(KeyLightTestCase):
    def test_only_the_field_being_changed_is_sent(self) -> None:
        """A PUT replaces the fields it carries. Sending a whole light object
        to change one value pushes back whatever was last read -- turning a
        light on again because a brightness key remembered it that way."""
        keylight.apply("192.168.1.50", brightness=50)

        self.assertEqual(self.http.sent["lights"], [{"brightness": 50}])

    def test_switching_on_sends_only_the_power(self) -> None:
        keylight.apply("192.168.1.50", on=True)

        self.assertEqual(self.http.sent["lights"], [{"on": 1}])

    def test_switching_off_sends_zero_not_false(self) -> None:
        keylight.apply("192.168.1.50", on=False)

        self.assertEqual(self.http.sent["lights"], [{"on": 0}])

    def test_a_temperature_is_converted_before_it_is_sent(self) -> None:
        keylight.apply("192.168.1.50", kelvin=3000)

        self.assertEqual(
            self.http.sent["lights"], [{"temperature": keylight.kelvin_to_device(3000)}]
        )

    def test_brightness_is_clamped_to_what_the_firmware_takes(self) -> None:
        keylight.apply("192.168.1.50", brightness=500)
        self.assertEqual(self.http.sent["lights"][0]["brightness"], 100)

        keylight.apply("192.168.1.50", brightness=-20)
        self.assertEqual(self.http.sent["lights"][0]["brightness"], 0)

    def test_it_uses_put_rather_than_get(self) -> None:
        keylight.apply("192.168.1.50", on=True)

        self.assertEqual(self.http.calls[-1][1], "PUT")

    def test_changing_nothing_asks_rather_than_writing(self) -> None:
        keylight.apply("192.168.1.50")

        self.assertEqual(self.http.calls[-1][1], "GET")

    def test_the_answer_updates_what_the_key_will_draw(self) -> None:
        self.use(FakeHttp(answer(on=1, brightness=80)))

        keylight.apply("192.168.1.50", on=True)

        self.assertEqual(
            keylight.cached_state("192.168.1.50")["brightness"], 80
        )


class CachedStateTests(KeyLightTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.jobs: list = []
        patch = unittest.mock.patch.object(
            keylight.webrequest, "background",
            lambda work, *args: (self.jobs.append((work, args)), True)[1],
        )
        patch.start()
        self.addCleanup(patch.stop)

    def test_it_never_reaches_the_network_itself(self) -> None:
        """It runs on the single render worker, and a light is on the network:
        waiting there would stall every other key on the deck."""
        def explode(*_a, **_k):
            raise AssertionError("cached_state performed a request")

        with unittest.mock.patch.object(keylight.webrequest, "request", explode):
            keylight.cached_state("192.168.1.50")

    def test_an_unknown_light_draws_nothing_rather_than_off(self) -> None:
        """A light that looks switched off while it is lighting the room is
        worse than a key that shows nothing."""
        self.assertIsNone(keylight.cached_state("192.168.1.50"))

    def test_a_burst_of_repaints_is_one_refresh(self) -> None:
        """While one is in flight the pending mark holds the rest off."""
        for _ in range(20):
            keylight.cached_state("192.168.1.50")

        self.assertEqual(len(self.jobs), 1)

    def test_it_does_not_ask_again_until_the_interval_has_passed(self) -> None:
        """The pending mark only covers a refresh still in flight. Once one
        has landed, TTL is the only thing between a repaint and a request --
        and repaints are far more frequent than the live loop."""
        keylight._remember("192.168.1.50", {"on": True})
        stored = keylight._states["192.168.1.50"][1]
        keylight._states["192.168.1.50"] = (1000.0, stored)
        self.jobs.clear()

        keylight.cached_state("192.168.1.50", now=1000.0 + keylight.STATE_TTL - 1)
        self.assertEqual(self.jobs, [])

        keylight.cached_state("192.168.1.50", now=1000.0 + keylight.STATE_TTL + 1)
        self.assertEqual(len(self.jobs), 1)

    def test_a_known_state_is_returned(self) -> None:
        keylight._remember("192.168.1.50", {"on": True, "brightness": 10,
                                            "kelvin": 4000})

        self.assertTrue(keylight.cached_state("192.168.1.50")["on"])

    def test_a_light_that_stops_answering_eventually_goes_unknown(self) -> None:
        keylight._remember("192.168.1.50", {"on": True})
        stored = keylight._states["192.168.1.50"][1]
        keylight._states["192.168.1.50"] = (1000.0, stored)

        self.assertIsNotNone(
            keylight.cached_state("192.168.1.50", now=1000.0 + 10)
        )
        self.assertIsNone(
            keylight.cached_state(
                "192.168.1.50", now=1000.0 + keylight.STATE_STALE + 1
            )
        )

    def test_a_key_with_no_light_asks_for_nothing(self) -> None:
        self.assertIsNone(keylight.cached_state(""))
        self.assertEqual(self.jobs, [])

    def test_a_failed_refresh_releases_its_mark(self) -> None:
        """Otherwise that light is never asked about again."""
        keylight._pending.add("192.168.1.50")

        with unittest.mock.patch.object(
            keylight, "state", side_effect=KeyLightError("gone")
        ):
            keylight._refresh("192.168.1.50")

        self.assertNotIn("192.168.1.50", keylight._pending)


class DiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        keylight.forget_discovery()
        self.addCleanup(keylight.forget_discovery)

    def test_it_reads_avahi_output(self) -> None:
        found = keylight.parse_browse(BROWSE)

        self.assertEqual(len(found), 2)
        self.assertEqual(found[0].host, "elgato-key-light-air-8cb5.local")
        self.assertEqual(found[0].address, "192.168.1.50")
        self.assertEqual(found[0].port, 9123)

    def test_the_same_light_is_not_listed_once_per_protocol(self) -> None:
        """avahi answers for IPv4 and IPv6, and both name the same device."""
        names = [light.name for light in keylight.parse_browse(BROWSE)]

        self.assertEqual(len(names), len(set(names)))

    def test_an_ipv6_only_record_is_not_offered_as_an_address(self) -> None:
        """A link-local address cannot be put in a URL without a zone index,
        so a light with no published host name must not fall back to one. The
        name deduplication does not cover this: the two records differ."""
        lines = (
            "=;eth0;IPv4;Light;_elg._tcp;local;;192.168.1.77;9123;\n"
            "=;eth0;IPv6;Light;_elg._tcp;local;;fe80::1;9123;\n"
        )

        found = keylight.parse_browse(lines)

        self.assertEqual([light.target for light in found], ["192.168.1.77"])

    def test_an_escaped_name_is_read_back_as_written(self) -> None:
        found = keylight.parse_browse(BROWSE)

        self.assertIn("Luz Salon", [light.name.replace("ó", "o") for light in found])

    def test_unresolved_lines_are_skipped(self) -> None:
        """A `+` line announces a service without an address."""
        self.assertEqual(keylight.parse_browse(BROWSE.splitlines()[0]), [])

    def test_rubbish_never_raises(self) -> None:
        for text in ("", "nonsense", "=;too;few;fields", "=;a;IPv4;b;c;d;e;f;x"):
            with self.subTest(text=text):
                keylight.parse_browse(text)

    def test_a_key_stores_the_host_name_not_the_address(self) -> None:
        """An address comes from DHCP and changes; the published name does
        not, and avahi resolving it is guaranteed by having found the light."""
        light = keylight.parse_browse(BROWSE)[0]

        self.assertEqual(light.target, "elgato-key-light-air-8cb5.local")

    def test_a_light_with_no_host_name_falls_back_to_its_address(self) -> None:
        line = "=;eth0;IPv4;Light;_elg._tcp;local;;192.168.1.77;9123;"

        self.assertEqual(keylight.parse_browse(line)[0].target, "192.168.1.77")

    def test_the_result_is_cached(self) -> None:
        """The editor fills its dropdown on the GTK thread and avahi-browse
        takes about a second even when nothing answers."""
        runs: list = []

        def browse():
            runs.append(1)
            return keylight.parse_browse(BROWSE)

        with unittest.mock.patch.object(keylight, "_browse", browse), \
                unittest.mock.patch.object(
                    keylight, "discovery_available", lambda: True):
            keylight.discover(now=1000.0)
            keylight.discover(now=1000.0 + keylight.DISCOVERY_TTL - 1)
            self.assertEqual(len(runs), 1)

            keylight.discover(now=1000.0 + keylight.DISCOVERY_TTL + 1)
            self.assertEqual(len(runs), 2)

    def test_without_avahi_it_answers_nothing_rather_than_raising(self) -> None:
        """The field then stays a plain text entry, so an address can still be
        typed by hand."""
        with unittest.mock.patch.object(
            keylight, "discovery_available", lambda: False
        ):
            self.assertEqual(keylight.discover(), [])

    def test_a_browse_that_fails_answers_nothing(self) -> None:
        with unittest.mock.patch.object(
            keylight.subprocess, "run",
            side_effect=subprocess.TimeoutExpired("avahi-browse", 1),
        ):
            self.assertEqual(keylight._browse(), [])

    def test_the_editor_shows_a_name_and_stores_an_address(self) -> None:
        with unittest.mock.patch.object(
            keylight, "_browse", lambda: keylight.parse_browse(BROWSE)
        ), unittest.mock.patch.object(
            keylight, "discovery_available", lambda: True
        ):
            choices = keylight.light_choices()

            self.assertIn("elgato-key-light-air-8cb5.local", choices)
            self.assertIn(
                "192.168.1.50",
                keylight.light_label("elgato-key-light-air-8cb5.local"),
            )

    def test_an_unknown_light_shows_as_itself(self) -> None:
        with unittest.mock.patch.object(
            keylight, "discovery_available", lambda: False
        ):
            self.assertEqual(keylight.light_label("10.0.0.9"), "10.0.0.9")


class ActionTests(KeyLightTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.messages: list[str] = []
        self.ctx = SimpleNamespace(
            bus=SimpleNamespace(
                emit=lambda topic, **d: self.messages.append(d.get("text", ""))
            )
        )

    def _params(self, **extra):
        return {"light": "192.168.1.50", **extra}

    def test_toggling_asks_the_light_rather_than_remembering(self) -> None:
        """It may have been switched from Elgato's own app, a phone, or the
        button on its back."""
        self.use(FakeHttp(answer(on=1)))

        REGISTRY["light.power"].execute(self.ctx, self._params(mode="toggle"))

        self.assertEqual(self.http.sent["lights"], [{"on": 0}])

    def test_on_and_off_do_not_need_to_ask(self) -> None:
        REGISTRY["light.power"].execute(self.ctx, self._params(mode="on"))

        self.assertEqual(len(self.http.calls), 1)
        self.assertEqual(self.http.sent["lights"], [{"on": 1}])

    def test_brighter_adds_to_what_the_light_reports(self) -> None:
        self.use(FakeHttp(answer(brightness=30)))

        REGISTRY["light.brightness"].execute(
            self.ctx, self._params(mode="up", amount=15)
        )

        self.assertEqual(self.http.sent["lights"], [{"brightness": 45}])

    def test_setting_a_level_ignores_the_current_one(self) -> None:
        self.use(FakeHttp(answer(brightness=30)))

        REGISTRY["light.brightness"].execute(
            self.ctx, self._params(mode="set", amount=70)
        )

        self.assertEqual(self.http.sent["lights"], [{"brightness": 70}])
        self.assertEqual(len(self.http.calls), 1)

    def test_warmer_lowers_the_colour_temperature(self) -> None:
        """Both inversions at once: warmer is fewer kelvin, and the device's
        own unit runs the other way again."""
        self.use(FakeHttp(answer(temperature=keylight.kelvin_to_device(5000))))

        REGISTRY["light.temperature"].execute(
            self.ctx, self._params(mode="up", amount=1000)
        )

        self.assertEqual(
            self.http.sent["lights"][0]["temperature"],
            keylight.kelvin_to_device(4000),
        )

    def test_cooler_raises_it(self) -> None:
        self.use(FakeHttp(answer(temperature=keylight.kelvin_to_device(4000))))

        REGISTRY["light.temperature"].execute(
            self.ctx, self._params(mode="down", amount=1000)
        )

        self.assertEqual(
            self.http.sent["lights"][0]["temperature"],
            keylight.kelvin_to_device(5000),
        )

    def test_a_failure_raises_so_the_key_marks_itself(self) -> None:
        self.use(FakeHttp(error=webrequest.WebRequestError("no route")))

        with self.assertRaises(KeyLightError):
            REGISTRY["light.power"].execute(self.ctx, self._params(mode="on"))

    def test_the_status_says_what_the_light_is_now_doing(self) -> None:
        REGISTRY["light.power"].execute(self.ctx, self._params(mode="on"))

        self.assertIn("%", self.messages[-1])
        self.assertIn("K", self.messages[-1])

    def test_a_power_key_lights_up_while_the_light_is_on(self) -> None:
        keylight._remember("192.168.1.50", {"on": True})

        self.assertTrue(
            REGISTRY["light.power"].feedback(self.ctx, self._params())["active"]
        )

    def test_an_unanswered_light_leaves_its_key_alone(self) -> None:
        with unittest.mock.patch.object(
            keylight.webrequest, "background", lambda *a: True
        ):
            self.assertEqual(
                REGISTRY["light.power"].feedback(self.ctx, self._params()), {}
            )

    def test_all_three_show_running_feedback(self) -> None:
        """A press is a round trip over the network."""
        for identifier in ("light.power", "light.brightness", "light.temperature"):
            with self.subTest(identifier=identifier):
                self.assertTrue(REGISTRY[identifier].running_feedback)

    def test_none_of_them_needs_obs(self) -> None:
        for identifier in ("light.power", "light.brightness", "light.temperature"):
            with self.subTest(identifier=identifier):
                self.assertFalse(REGISTRY[identifier].needs_obs)

    def test_every_mode_is_labelled(self) -> None:
        self.assertEqual(
            sorted(light_actions.POWER_LABELS), sorted(light_actions.POWER_MODES)
        )
        for labels in (light_actions.BRIGHTNESS_LABELS,
                       light_actions.TEMPERATURE_LABELS):
            self.assertEqual(sorted(labels), sorted(light_actions.LEVEL_MODES))

    def test_the_light_field_fills_without_obs(self) -> None:
        from linuxstreamdeck.ui.steps import LOCAL_CHOICE_SOURCES

        self.assertIn("key_lights", LOCAL_CHOICE_SOURCES)
