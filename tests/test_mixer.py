"""Per-application volume, microphone mute and the default audio device.

Every test here feeds `pactl -f json` output captured from a real session, so
nothing needs a mixer to run and nothing depends on the machine it runs on.
That matters more than usual: plain `pactl` answers in the user's own language
-- on the machine this was written on it says `Silenciado: no` -- so the JSON
form is not a convenience but the only output that can be read at all.
"""

from __future__ import annotations

import json
import subprocess
import unittest
import unittest.mock
from types import SimpleNamespace

from linuxstreamdeck import basic_actions as _basic_actions  # noqa: F401
from linuxstreamdeck.core import mixer
from linuxstreamdeck.core.actions import REGISTRY
from linuxstreamdeck.core.mixer import MixerError

SINKS = [
    {"index": 58, "name": "alsa_output.hdmi", "mute": False,
     "description": "HDMI / DisplayPort 1 Output",
     "volume": {"front-left": {"value_percent": "100%"},
                "front-right": {"value_percent": "100%"}}},
    {"index": 59, "name": "alsa_output.speaker", "mute": False,
     "description": "Built-in Speaker",
     "volume": {"front-left": {"value_percent": "86%"},
                "front-right": {"value_percent": "86%"}}},
]
SOURCES = [
    {"index": 59, "name": "alsa_output.speaker.monitor", "mute": False,
     "description": "Monitor of Built-in Speaker",
     "monitor_source": "alsa_output.speaker", "volume": {}},
    {"index": 60, "name": "alsa_input.headset", "mute": True,
     "description": "Headset Microphone", "monitor_source": None,
     "volume": {"mono": {"value_percent": "100%"}}},
]
SINK_INPUTS = [
    {"index": 143, "mute": False,
     "properties": {"application.name": "Firefox",
                    "application.process.binary": "firefox"},
     "volume": {"mono": {"value_percent": "70%"}}},
    {"index": 144, "mute": True,
     "properties": {"application.name": "Firefox",
                    "application.process.binary": "firefox"},
     "volume": {"mono": {"value_percent": "40%"}}},
    {"index": 145, "mute": False,
     "properties": {"application.process.binary": "gst-launch-1.0"},
     "volume": {"mono": {"value_percent": "100%"}}},
]


class FakePactl:
    """Answers like `pactl` does, and records what it was asked to change."""

    def __init__(self, *, missing: bool = False, plain: bool = False) -> None:
        self.missing = missing
        self.plain = plain            # a pactl too old for -f json
        self.commands: list[list[str]] = []
        self.sinks = [dict(entry) for entry in SINKS]
        self.sources = [dict(entry) for entry in SOURCES]
        self.inputs = [dict(entry) for entry in SINK_INPUTS]
        self.default_sink = "alsa_output.speaker"
        self.default_source = "alsa_input.headset"

    def which(self, _name):
        return None if self.missing else "/usr/bin/pactl"

    def run(self, argv, **_kwargs):
        args = list(argv[1:])
        self.commands.append(args)
        if args[:1] == ["-f"]:
            return self._json(args[2:])
        if args[0] == "get-default-sink":
            return _done(self.default_sink)
        if args[0] == "get-default-source":
            return _done(self.default_source)
        if args[0].startswith("set-"):
            return _done("")
        return _done("", code=1)

    def _json(self, args):
        if self.plain:
            return _done("Entrada del destino #108\n\tSilenciado: no")
        table = {"sinks": self.sinks, "sources": self.sources,
                 "sink-inputs": self.inputs}
        return _done(json.dumps(table.get(args[1], [])))


def _done(stdout: str, code: int = 0):
    return subprocess.CompletedProcess([], code, stdout, "")


class MixerTestCase(unittest.TestCase):
    def use(self, pactl: FakePactl) -> FakePactl:
        self.pactl = pactl
        patches = [
            unittest.mock.patch("linuxstreamdeck.core.mixer.shutil.which",
                                pactl.which),
            unittest.mock.patch("linuxstreamdeck.core.mixer.subprocess.run",
                                pactl.run),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        mixer.forget_state()
        self.addCleanup(mixer.forget_state)
        return pactl

    def setUp(self) -> None:
        self.use(FakePactl())


class DeviceListTests(MixerTestCase):
    def test_outputs_come_before_inputs(self) -> None:
        kinds = [device.kind for device in mixer.devices()]

        self.assertEqual(
            kinds, ["output", "output", "input"]
        )

    def test_a_monitor_is_not_offered_as_a_microphone(self) -> None:
        """It is the loopback of an output. Offering them would double the
        list with entries nobody means to record from."""
        names = [device.name for device in mixer.devices()]

        self.assertNotIn("alsa_output.speaker.monitor", names)

    def test_the_one_in_use_is_marked(self) -> None:
        defaults = {d.name for d in mixer.devices() if d.default}

        self.assertEqual(defaults, {"alsa_output.speaker", "alsa_input.headset"})

    def test_the_editor_shows_a_description_and_stores_a_name(self) -> None:
        """A name survives a reboot; a description is the only part anyone can
        recognise."""
        choices = mixer.device_choices()

        self.assertIn("alsa_output.speaker", choices)
        self.assertEqual(
            mixer.device_label("alsa_output.speaker"), "Built-in Speaker"
        )

    def test_an_unknown_name_shows_as_itself(self) -> None:
        self.assertEqual(mixer.device_label("gone.away"), "gone.away")


class ApplicationListTests(MixerTestCase):
    def test_each_application_is_listed_once(self) -> None:
        """Firefox owns two streams here and is still one entry."""
        self.assertEqual(
            mixer.playing_applications(), ["Firefox", "gst-launch-1.0"]
        )

    def test_a_stream_with_no_name_falls_back_to_its_binary(self) -> None:
        self.assertIn("gst-launch-1.0", mixer.playing_applications())


class SwitchTests(MixerTestCase):
    def test_switching_sets_the_right_kind_of_default(self) -> None:
        mixer.switch_to("alsa_input.headset")

        self.assertIn(
            ["set-default-source", "alsa_input.headset"], self.pactl.commands
        )

    def test_switching_an_output_uses_the_sink_command(self) -> None:
        mixer.switch_to("alsa_output.hdmi")

        self.assertIn(
            ["set-default-sink", "alsa_output.hdmi"], self.pactl.commands
        )

    def test_a_device_that_is_gone_is_reported_by_name(self) -> None:
        with self.assertRaises(MixerError) as caught:
            mixer.switch_to("alsa_output.unplugged")

        self.assertIn("alsa_output.unplugged", str(caught.exception))

    def test_a_key_with_no_device_says_so(self) -> None:
        with self.assertRaises(MixerError) as caught:
            mixer.switch_to("")

        self.assertIn("no audio device", str(caught.exception))

    def test_one_key_moves_between_two_devices(self) -> None:
        """Speakers and a headset on one key is the whole point of it."""
        mixer.toggle_between("alsa_output.speaker", "alsa_output.hdmi")
        self.assertIn(
            ["set-default-sink", "alsa_output.hdmi"], self.pactl.commands
        )

        self.pactl.default_sink = "alsa_output.hdmi"
        mixer.forget_state()
        self.pactl.commands.clear()
        mixer.toggle_between("alsa_output.speaker", "alsa_output.hdmi")

        self.assertIn(
            ["set-default-sink", "alsa_output.speaker"], self.pactl.commands
        )


class VolumeTests(MixerTestCase):
    def _volume_commands(self) -> list[list[str]]:
        return [c for c in self.pactl.commands if "volume" in c[0]]

    def test_raising_the_speakers_writes_an_absolute_level(self) -> None:
        """pactl clamps nothing, so a relative +5% pressed a dozen times walks
        the sink to 160 %, where the sample is amplified and distorts."""
        mixer.apply("output", "", "up", 10)

        self.assertEqual(
            self._volume_commands(),
            [["set-sink-volume", "@DEFAULT_SINK@", "96%"]],
        )

    def test_a_raise_stops_at_the_ceiling(self) -> None:
        mixer.apply("output", "", "up", 200)

        self.assertEqual(
            self._volume_commands(),
            [["set-sink-volume", "@DEFAULT_SINK@", "100%"]],
        )

    def test_a_drop_stops_at_silence(self) -> None:
        mixer.apply("output", "", "down", 200)

        self.assertEqual(
            self._volume_commands(),
            [["set-sink-volume", "@DEFAULT_SINK@", "0%"]],
        )

    def test_setting_a_level_ignores_the_current_one(self) -> None:
        mixer.apply("output", "", "set", 30)

        self.assertEqual(
            self._volume_commands(),
            [["set-sink-volume", "@DEFAULT_SINK@", "30%"]],
        )

    def test_the_microphone_is_a_source_not_a_sink(self) -> None:
        mixer.apply("input", "", "down", 10)

        self.assertEqual(
            self._volume_commands(),
            [["set-source-volume", "@DEFAULT_SOURCE@", "90%"]],
        )

    def test_every_stream_of_an_application_moves_together(self) -> None:
        """A browser commonly owns several. Acting on only the first would
        leave one tab audible after the key said it had muted it."""
        mixer.apply("app", "Firefox", "toggle")

        self.assertEqual(
            [c for c in self.pactl.commands if "mute" in c[0]],
            [["set-sink-input-mute", "143", "toggle"],
             ["set-sink-input-mute", "144", "toggle"]],
        )

    def test_each_stream_keeps_its_own_level_on_a_relative_change(self) -> None:
        mixer.apply("app", "Firefox", "down", 10)

        self.assertEqual(
            self._volume_commands(),
            [["set-sink-input-volume", "143", "60%"],
             ["set-sink-input-volume", "144", "30%"]],
        )

    def test_an_application_name_is_matched_regardless_of_case(self) -> None:
        mixer.apply("app", "firefox", "mute")

        self.assertTrue(
            [c for c in self.pactl.commands if c[0] == "set-sink-input-mute"]
        )

    def test_muting_and_unmuting_are_explicit(self) -> None:
        mixer.apply("output", "", "mute")
        mixer.apply("output", "", "unmute")

        self.assertEqual(
            [c for c in self.pactl.commands if "mute" in c[0]],
            [["set-sink-mute", "@DEFAULT_SINK@", "1"],
             ["set-sink-mute", "@DEFAULT_SINK@", "0"]],
        )

    def test_an_application_that_stopped_playing_is_reported(self) -> None:
        with self.assertRaises(MixerError) as caught:
            mixer.apply("app", "Ardour", "toggle")

        self.assertIn("Ardour", str(caught.exception))
        self.assertIn("not playing", str(caught.exception))

    def test_a_key_with_no_application_chosen_says_so(self) -> None:
        with self.assertRaises(MixerError) as caught:
            mixer.apply("app", "", "toggle")

        self.assertIn("no application", str(caught.exception))

    def test_an_unknown_target_falls_back_to_the_speakers(self) -> None:
        """So no stored key changes behaviour by being loaded."""
        mixer.apply("nonsense", "", "down", 10)

        self.assertEqual(self._volume_commands()[0][0], "set-sink-volume")

    def test_an_unknown_mode_falls_back_to_toggling_mute(self) -> None:
        mixer.apply("output", "", "explode")

        self.assertEqual(
            [c for c in self.pactl.commands if "mute" in c[0]],
            [["set-sink-mute", "@DEFAULT_SINK@", "toggle"]],
        )


class StateTests(MixerTestCase):
    def test_it_reads_the_default_output(self) -> None:
        self.assertEqual(mixer.state("output"), (False, 86))

    def test_it_reads_the_microphone(self) -> None:
        self.assertEqual(mixer.state("input"), (True, 100))

    def test_an_application_is_muted_only_when_all_of_it_is(self) -> None:
        """One audible tab means it can still be heard, and the key must not
        claim otherwise."""
        self.assertEqual(mixer.state("app", "Firefox"), (False, 70))

        for entry in self.pactl.inputs:
            entry["mute"] = True
        mixer.forget_state()

        self.assertEqual(mixer.state("app", "Firefox"), (True, 70))

    def test_an_application_that_is_not_playing_is_unknown(self) -> None:
        self.assertIsNone(mixer.state("app", "Ardour"))

    def test_an_unanswerable_question_is_unknown_not_unmuted(self) -> None:
        """A key showing "not muted" because nothing answered is worse than a
        key showing nothing: a microphone key exists to be believed."""
        self.use(FakePactl(missing=True))

        self.assertIsNone(mixer.state("input"))

    def test_a_pactl_too_old_for_json_says_so_rather_than_guessing(self) -> None:
        """Its other output is prose in the user's own language, which is not
        something to parse."""
        self.use(FakePactl(plain=True))

        with self.assertRaises(MixerError) as caught:
            mixer.devices()

        self.assertIn("readable form", str(caught.exception))


class SnapshotTests(MixerTestCase):
    def test_one_reading_serves_every_key_that_asks(self) -> None:
        """feedback() runs on the single render worker, and each reading is
        four processes: a page of fifteen mute keys must not spawn sixty."""
        for _ in range(15):
            mixer.state("input")

        self.assertEqual(
            len([c for c in self.pactl.commands if c[:1] == ["-f"]]), 3
        )

    def test_a_stale_reading_is_replaced(self) -> None:
        mixer.snapshot(now=1000.0)
        before = len(self.pactl.commands)

        mixer.snapshot(now=1000.0 + mixer.STATE_TTL + 0.1)

        self.assertGreater(len(self.pactl.commands), before)

    def test_a_change_drops_the_reading_it_invalidated(self) -> None:
        """Otherwise the repaint that follows a press shows the state from
        before it."""
        mixer.state("output")
        mixer.apply("output", "", "mute")

        self.assertEqual(mixer._snapshot, (0.0, {}))


class MissingBackendTests(MixerTestCase):
    def setUp(self) -> None:
        self.use(FakePactl(missing=True))

    def test_it_reports_what_to_install(self) -> None:
        with self.assertRaises(MixerError) as caught:
            mixer.apply("output", "", "up", 5)

        self.assertIn("pulseaudio-utils", str(caught.exception))

    def test_availability_is_answerable_without_raising(self) -> None:
        self.assertFalse(mixer.available())


class ActionTests(MixerTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.messages: list[str] = []
        self.ctx = SimpleNamespace(
            bus=SimpleNamespace(
                emit=lambda topic, **d: self.messages.append(d.get("text", ""))
            )
        )

    def test_the_volume_key_reports_what_it_did(self) -> None:
        REGISTRY["sys.volume"].execute(
            self.ctx, {"target": "output", "mode": "down", "amount": 10}
        )

        self.assertIn("down", self.messages[-1])

    def test_a_missing_backend_is_a_message_not_an_exception(self) -> None:
        """It would otherwise abandon the rest of a multi-action key."""
        self.use(FakePactl(missing=True))

        REGISTRY["sys.volume"].execute(self.ctx, {"target": "output"})

        self.assertIn("pulseaudio-utils", self.messages[-1])

    def test_a_mute_key_lights_up_while_muted(self) -> None:
        state = REGISTRY["sys.volume"].feedback(
            self.ctx, {"target": "input", "mode": "toggle"}
        )

        self.assertTrue(state["active"])
        self.assertTrue(state["color"])

    def test_a_volume_key_shows_no_state(self) -> None:
        """Only a mute key is about state; a volume key would be lit
        permanently and mean nothing by it."""
        self.assertEqual(
            REGISTRY["sys.volume"].feedback(
                self.ctx, {"target": "output", "mode": "up"}
            ),
            {},
        )

    def test_an_unknowable_state_leaves_the_key_alone(self) -> None:
        self.use(FakePactl(missing=True))

        self.assertEqual(
            REGISTRY["sys.volume"].feedback(
                self.ctx, {"target": "input", "mode": "toggle"}
            ),
            {},
        )

    def test_the_device_key_lights_up_while_its_device_is_in_use(self) -> None:
        action = REGISTRY["sys.audio_device"]

        self.assertTrue(
            action.feedback(self.ctx, {"device": "alsa_output.speaker"})["active"]
        )
        self.assertFalse(
            action.feedback(self.ctx, {"device": "alsa_output.hdmi"})["active"]
        )

    def test_the_device_key_toggles_when_a_second_one_is_set(self) -> None:
        REGISTRY["sys.audio_device"].execute(
            self.ctx,
            {"device": "alsa_output.speaker", "device_alt": "alsa_output.hdmi"},
        )

        self.assertIn(
            ["set-default-sink", "alsa_output.hdmi"], self.pactl.commands
        )

    def test_both_keys_are_immediate(self) -> None:
        """Two short local commands; occupying an action worker would make a
        volume key feel slower than the media keys beside it."""
        for identifier in ("sys.volume", "sys.audio_device"):
            with self.subTest(identifier=identifier):
                self.assertTrue(REGISTRY[identifier].immediate)

    def test_the_application_field_only_applies_to_an_application(self) -> None:
        param = next(
            p for p in REGISTRY["sys.volume"].params if p.name == "application"
        )

        self.assertEqual(param.depends_on, "target")
        self.assertEqual(list(param.depends_values), ["app"])

    def test_the_amount_only_applies_to_a_volume_change(self) -> None:
        param = next(
            p for p in REGISTRY["sys.volume"].params if p.name == "amount"
        )

        self.assertEqual(param.depends_on, "mode")
        self.assertEqual(list(param.depends_values), list(mixer.VOLUME_MODES))

    def test_every_mode_and_target_is_labelled(self) -> None:
        self.assertEqual(sorted(mixer.MODE_LABELS), sorted(mixer.MODES))
        self.assertEqual(sorted(mixer.TARGET_LABELS), sorted(mixer.TARGETS))


class LiveRefreshTests(MixerTestCase):
    def _interval(self, params):
        from linuxstreamdeck.core.config import KIND_SINGLE, KeyConfig
        from linuxstreamdeck.core.controller import DeckController

        controller = SimpleNamespace(
            obs=SimpleNamespace(connected=False), _twitch_linked=lambda: False
        )
        return DeckController._live_interval(
            controller,
            KeyConfig(kind=KIND_SINGLE, action="sys.volume", params=params),
        )

    def test_a_mute_key_keeps_itself_up_to_date(self) -> None:
        """The mixer can be changed from the desktop's own volume panel or a
        headset button, and a mute key has to be believed."""
        from linuxstreamdeck.core.controller import MIXER_REFRESH_SECONDS

        self.assertEqual(
            self._interval({"mode": "toggle"}), MIXER_REFRESH_SECONDS
        )

    def test_a_volume_key_is_never_repainted(self) -> None:
        self.assertEqual(self._interval({"mode": "up"}), 0.0)

    def test_nothing_is_repainted_without_a_mixer(self) -> None:
        self.use(FakePactl(missing=True))

        self.assertEqual(self._interval({"mode": "toggle"}), 0.0)


class ChoiceSourceTests(unittest.TestCase):
    def test_both_sources_fill_without_obs(self) -> None:
        from linuxstreamdeck.ui.steps import LOCAL_CHOICE_SOURCES

        self.assertIn("audio_apps", LOCAL_CHOICE_SOURCES)
        self.assertIn("audio_devices", LOCAL_CHOICE_SOURCES)
