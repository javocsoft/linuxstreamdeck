"""A virtual output others can listen to, so a sound key reaches the stream.

The feature is one device rather than any audio code, so most of what is
pinned here is about that device's lifetime: it must not be created twice, it
must survive a restart of this application, and it must **not** be taken away
on shutdown -- another application stores it by name.
"""

from __future__ import annotations

import subprocess
import unittest
import unittest.mock
from types import SimpleNamespace

from linuxstreamdeck import basic_actions
from linuxstreamdeck.core import soundboard
from linuxstreamdeck.core.actions import REGISTRY
from linuxstreamdeck.core.soundboard import SoundboardError

OTHER_SINKS = (
    "56\talsa_output.hdmi\tPipeWire\ts24le\tSUSPENDED\n"
    "59\talsa_output.speaker\tPipeWire\ts32le\tRUNNING\n"
)


class FakePactl:
    """Answers like `pactl` and records every command."""

    def __init__(self, *, missing=False, present=False, refuse=()) -> None:
        self.missing = missing
        self.present = present
        self.refuse = refuse
        self.commands: list[list[str]] = []

    def which(self, _name):
        return None if self.missing else "/usr/bin/pactl"

    def run(self, argv, **_kwargs):
        args = list(argv[1:])
        self.commands.append(args)
        if args[:3] == ["list", "short", "sinks"]:
            listing = OTHER_SINKS + (
                f"99\t{soundboard.SINK_NAME}\tPipeWire\tfloat32le\tIDLE\n"
                if self.present else ""
            )
            return _done(listing)
        if args[:1] == ["load-module"]:
            module = args[1]
            if module in self.refuse:
                return _done("", code=1, stderr=f"{module} refused")
            if module == "module-null-sink":
                self.present = True
            return _done("536870916")
        if args[:1] == ["unload-module"]:
            if args[1] == "module-null-sink":
                self.present = False
            return _done("")
        return _done("", code=1)

    @property
    def loaded(self) -> list[str]:
        return [c[1] for c in self.commands if c[:1] == ["load-module"]]


def _done(stdout: str, code: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess([], code, stdout, stderr)


class SoundboardTestCase(unittest.TestCase):
    def use(self, pactl: FakePactl) -> FakePactl:
        self.pactl = pactl
        for target, value in (("shutil.which", pactl.which),
                              ("subprocess.run", pactl.run)):
            patch = unittest.mock.patch(
                f"linuxstreamdeck.core.soundboard.{target}", value
            )
            patch.start()
            self.addCleanup(patch.stop)
        return pactl

    def setUp(self) -> None:
        self.use(FakePactl())


class DeviceTests(SoundboardTestCase):
    def test_it_creates_the_output_and_its_monitoring(self) -> None:
        """The loopback is not decoration: without it the person pressing the
        key hears nothing, which is indistinguishable from a broken key."""
        soundboard.ensure()

        self.assertEqual(
            self.pactl.loaded, ["module-null-sink", "module-loopback"]
        )

    def test_the_device_is_named_so_others_can_find_it(self) -> None:
        soundboard.ensure()

        created = next(
            c for c in self.pactl.commands if c[:2] == ["load-module", "module-null-sink"]
        )
        self.assertIn(f"sink_name={soundboard.SINK_NAME}", created)
        self.assertIn(
            f"sink_properties=device.description={soundboard.SINK_DESCRIPTION}",
            created,
        )

    def test_the_monitoring_listens_to_the_device_it_just_made(self) -> None:
        soundboard.ensure()

        loopback = next(
            c for c in self.pactl.commands if c[:2] == ["load-module", "module-loopback"]
        )
        self.assertIn(f"source={soundboard.MONITOR_NAME}", loopback)

    def test_an_existing_device_is_reused(self) -> None:
        """It survives this application: the module belongs to the audio
        session, so a day of restarts must not leave a dozen of them."""
        self.use(FakePactl(present=True))

        self.assertEqual(soundboard.ensure(), soundboard.SINK_NAME)
        self.assertEqual(self.pactl.loaded, [])

    def test_asking_twice_creates_one_device(self) -> None:
        soundboard.ensure()
        soundboard.ensure()

        self.assertEqual(self.pactl.loaded.count("module-null-sink"), 1)

    def test_it_is_not_confused_by_other_sinks(self) -> None:
        self.assertFalse(soundboard.sink_exists())

    def test_a_refused_device_is_reported(self) -> None:
        self.use(FakePactl(refuse=("module-null-sink",)))

        with self.assertRaises(SoundboardError) as caught:
            soundboard.ensure()

        self.assertIn("refused", str(caught.exception))

    def test_a_refused_loopback_still_leaves_a_usable_device(self) -> None:
        """The sound still reaches the stream; only the local monitoring is
        missing, and losing that must not lose the feature."""
        self.use(FakePactl(refuse=("module-loopback",)))

        self.assertEqual(soundboard.ensure(), soundboard.SINK_NAME)

    def test_without_pactl_it_says_what_to_install(self) -> None:
        self.use(FakePactl(missing=True))

        with self.assertRaises(SoundboardError) as caught:
            soundboard.ensure()

        self.assertIn("pulseaudio-utils", str(caught.exception))

    def test_availability_is_answerable_without_raising(self) -> None:
        self.use(FakePactl(missing=True))

        self.assertFalse(soundboard.available())


class LifetimeTests(SoundboardTestCase):
    def test_removing_takes_both_modules_away(self) -> None:
        soundboard.ensure()

        soundboard.remove()

        self.assertEqual(
            [c[1] for c in self.pactl.commands if c[:1] == ["unload-module"]],
            ["module-loopback", "module-null-sink"],
        )

    def test_shutdown_deliberately_leaves_it_alone(self) -> None:
        """Another application stores this device by name -- OBS as an audio
        source, Discord as an input -- so removing it on exit would silently
        break their configuration every time this one closed."""
        import inspect

        from linuxstreamdeck.app import LinuxStreamDeckApp

        self.assertNotIn(
            "soundboard", inspect.getsource(LinuxStreamDeckApp._on_shutdown)
        )


class _RoutingGst:
    """A Gst double that tells the two elements apart.

    `tests.test_audio.FakeGst` answers every factory with the same object,
    which cannot show whether the output element was created and configured
    separately from the player -- which is the whole of what routing is.
    """

    SECOND = 1_000_000_000
    MSECOND = 1_000_000
    State = SimpleNamespace(PLAYING="playing", NULL="null")
    StateChangeReturn = SimpleNamespace(FAILURE="failure")
    MessageType = SimpleNamespace(ERROR=1, EOS=2)

    def __init__(self, output_available: bool = True) -> None:
        from tests.test_audio import FakeBus, FakeGst, FakeMessage, FakePlayer

        # One EOS, so playback ends instead of polling for ever.
        self.bus = FakeBus([FakeMessage(FakeGst.MessageType.EOS)])
        self.player = FakePlayer(self.bus)
        self.output = FakePlayer(self.bus) if output_available else None
        self.made: list[str] = []
        self.ElementFactory = SimpleNamespace(make=self._make)

    def _make(self, factory, _name):
        self.made.append(factory)
        return self.output if factory == "pulsesink" else self.player


class RoutingTests(unittest.TestCase):
    """Getting the sound into that device rather than out of the speakers."""

    def setUp(self) -> None:
        import tempfile
        from pathlib import Path

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.file = Path(self.temp.name) / "tone.wav"
        self.file.write_bytes(b"tone")

    def _play(self, gst=None, **kwargs):
        from linuxstreamdeck.core.audio import play_audio

        gst = gst or _RoutingGst()
        play_audio(self.file, 100, 0, gst=gst, **kwargs)
        return gst

    def test_no_sink_leaves_playback_exactly_as_it_was(self) -> None:
        """Every key already configured must keep behaving identically."""
        gst = self._play()

        self.assertNotIn("audio-sink", gst.player.properties)
        self.assertNotIn("pulsesink", gst.made)

    def test_a_sink_is_routed_to_by_name(self) -> None:
        gst = self._play(sink="linuxstreamdeck")

        self.assertEqual(gst.output.properties["device"], "linuxstreamdeck")
        self.assertIs(gst.player.properties["audio-sink"], gst.output)

    def test_a_missing_output_plugin_is_reported_not_ignored(self) -> None:
        """Falling back to the default output would play a soundboard cue out
        of the speakers while the stream heard nothing, which looks like it
        worked and is not."""
        from linuxstreamdeck.core.audio import AudioPlaybackError

        with self.assertRaises(AudioPlaybackError) as caught:
            self._play(gst=_RoutingGst(output_available=False),
                       sink="linuxstreamdeck")

        self.assertIn("stream", str(caught.exception))


class ActionTests(SoundboardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.messages: list[str] = []
        self.played: list[dict] = []
        self.ctx = SimpleNamespace(
            stop_requested=lambda: False,
            bus=SimpleNamespace(
                emit=lambda topic, **d: self.messages.append(d.get("text", ""))
            ),
        )
        patch = unittest.mock.patch.object(
            basic_actions, "play_audio",
            lambda *a, **k: self.played.append(k),
        )
        patch.start()
        self.addCleanup(patch.stop)

    def test_the_default_key_plays_locally_and_makes_no_device(self) -> None:
        REGISTRY["sys.audio"].execute(self.ctx, {"file": "/tone.wav"})

        self.assertEqual(self.played[-1]["sink"], "")
        self.assertEqual(self.pactl.loaded, [])

    def test_a_shared_key_creates_the_device_and_plays_into_it(self) -> None:
        REGISTRY["sys.audio"].execute(
            self.ctx, {"file": "/tone.wav", "output": basic_actions.AUDIO_SHARED}
        )

        self.assertEqual(self.played[-1]["sink"], soundboard.SINK_NAME)
        self.assertIn("module-null-sink", self.pactl.loaded)

    def test_a_device_that_cannot_be_made_reports_and_plays_nothing(self) -> None:
        """Falling back to the speakers would play the cue where the person
        pressing it can hear it and the stream cannot, which looks like it
        worked and is not."""
        self.use(FakePactl(refuse=("module-null-sink",)))

        REGISTRY["sys.audio"].execute(
            self.ctx, {"file": "/tone.wav", "output": basic_actions.AUDIO_SHARED}
        )

        self.assertEqual(self.played, [])
        self.assertTrue(self.messages)

    def test_an_unknown_choice_falls_back_to_local(self) -> None:
        """So no stored key changes behaviour by being loaded."""
        REGISTRY["sys.audio"].execute(
            self.ctx, {"file": "/tone.wav", "output": "broadcast-everywhere"}
        )

        self.assertEqual(self.played[-1]["sink"], "")

    def test_every_choice_is_labelled(self) -> None:
        self.assertEqual(
            sorted(basic_actions.AUDIO_OUTPUT_LABELS),
            sorted(basic_actions.AUDIO_OUTPUTS),
        )

    def test_the_default_is_the_behaviour_that_already_existed(self) -> None:
        param = next(
            p for p in REGISTRY["sys.audio"].params if p.name == "output"
        )

        self.assertEqual(param.default, basic_actions.AUDIO_LOCAL)
