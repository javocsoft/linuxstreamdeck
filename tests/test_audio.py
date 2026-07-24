from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from linuxstreamdeck.core.audio import AudioPlaybackError, play_audio


class FakeMessage:
    def __init__(self, message_type, error="decoder failed", debug="details") -> None:
        self.type = message_type
        self._error = error
        self._debug = debug

    def parse_error(self):
        return SimpleNamespace(message=self._error), self._debug


class FakeBus:
    def __init__(self, messages=None) -> None:
        self.messages = list(messages or [])
        self.timeouts = []

    def timed_pop_filtered(self, timeout, _message_types):
        self.timeouts.append(timeout)
        return self.messages.pop(0) if self.messages else None


class FakePlayer:
    def __init__(self, bus) -> None:
        self.bus = bus
        self.properties = {}
        self.states = []

    def get_bus(self):
        return self.bus

    def set_property(self, name, value) -> None:
        self.properties[name] = value

    def set_state(self, state):
        self.states.append(state)
        return "success"


class FakeGst:
    SECOND = 1_000_000_000
    MSECOND = 1_000_000
    State = SimpleNamespace(PLAYING="playing", NULL="null")
    StateChangeReturn = SimpleNamespace(FAILURE="failure")
    MessageType = SimpleNamespace(ERROR=1, EOS=2)

    def __init__(self, messages=None) -> None:
        self.bus = FakeBus(messages)
        self.player = FakePlayer(self.bus)
        self.ElementFactory = SimpleNamespace(
            make=lambda _factory, _name: self.player
        )


class AudioPlaybackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.audio_file = Path(self.temp_dir.name) / "tone.mp3"
        self.audio_file.write_bytes(b"test audio")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_plays_until_end_of_stream_with_selected_volume(self) -> None:
        gst = FakeGst([FakeMessage(FakeGst.MessageType.EOS)])

        play_audio(self.audio_file, 35, gst=gst)

        self.assertEqual(gst.player.properties["uri"], self.audio_file.as_uri())
        self.assertEqual(gst.player.properties["volume"], 0.35)
        self.assertEqual(gst.player.states, ["playing", "null"])

    def test_optional_time_limit_stops_playback(self) -> None:
        gst = FakeGst()
        times = iter((10.0, 10.0, 11.1))

        play_audio(
            self.audio_file,
            maximum_seconds=1,
            gst=gst,
            monotonic=lambda: next(times),
        )

        self.assertEqual(gst.player.states[-1], "null")
        self.assertEqual(gst.bus.timeouts[0], 100 * FakeGst.MSECOND)

    def test_shutdown_request_stops_playback(self) -> None:
        gst = FakeGst()

        play_audio(
            self.audio_file,
            gst=gst,
            stop_requested=lambda: True,
        )

        self.assertEqual(gst.player.states, ["playing", "null"])
        self.assertEqual(gst.bus.timeouts, [])

    def test_pipeline_errors_are_user_facing_and_always_release_player(self) -> None:
        gst = FakeGst([FakeMessage(FakeGst.MessageType.ERROR)])

        with self.assertRaisesRegex(
            AudioPlaybackError,
            "Could not play audio: decoder failed",
        ):
            play_audio(self.audio_file, gst=gst)

        self.assertEqual(gst.player.states[-1], "null")

    def test_missing_and_unsupported_files_are_rejected(self) -> None:
        with self.assertRaisesRegex(AudioPlaybackError, "Audio file not found"):
            play_audio(Path(self.temp_dir.name) / "missing.wav", gst=FakeGst())

        unsupported = Path(self.temp_dir.name) / "tone.txt"
        unsupported.write_bytes(b"not audio")
        with self.assertRaisesRegex(AudioPlaybackError, "Unsupported audio format"):
            play_audio(unsupported, gst=FakeGst())

    def test_volume_is_limited_to_zero_through_one_hundred_percent(self) -> None:
        loud = FakeGst([FakeMessage(FakeGst.MessageType.EOS)])
        muted = FakeGst([FakeMessage(FakeGst.MessageType.EOS)])

        play_audio(self.audio_file, 150, gst=loud)
        play_audio(self.audio_file, -10, gst=muted)

        self.assertEqual(loud.player.properties["volume"], 1.0)
        self.assertEqual(muted.player.properties["volume"], 0.0)

    def test_non_finite_volume_and_duration_are_rejected(self) -> None:
        with self.assertRaisesRegex(AudioPlaybackError, "Volume must"):
            play_audio(self.audio_file, float("nan"), gst=FakeGst())
        with self.assertRaisesRegex(AudioPlaybackError, "Maximum play time must"):
            play_audio(
                self.audio_file,
                maximum_seconds=float("inf"),
                gst=FakeGst(),
            )


if __name__ == "__main__":
    unittest.main()
