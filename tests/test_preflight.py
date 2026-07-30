"""The checks worth running before going live.

What is tested hardest here is not that the checks work: it is that they never
overclaim. Every failure mode of a checker is the same one — saying something
is fine when it was never established — so "not checked" being a visible,
first-class result is the property most of these tests defend.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from linuxstreamdeck import basic_actions as _basic_actions  # noqa: F401
from linuxstreamdeck.core import preflight
from linuxstreamdeck.core.preflight import FAIL, OK, UNCHECKED, WARN, Check, Report
from linuxstreamdeck.obs import actions as obs_actions  # noqa: F401


class FakeObs:
    connected = True
    host = "localhost"

    def __init__(self, **overrides) -> None:
        self.state = SimpleNamespace(streaming=False, recording=False)
        self._audio = overrides.get("audio", {"Mic/Aux": -20.0})
        self._muted = overrides.get("muted", {})
        self._cameras = overrides.get("cameras", {})
        self._collection = overrides.get("collection", ("Live", 3))
        self._target = overrides.get("target", ("twitch", True))
        self._folder = overrides.get("folder", tempfile.gettempdir())
        for name, value in overrides.items():
            if name in ("connected", "host"):
                setattr(self, name, value)

    def measure_audio(self, _seconds):
        return self._audio

    def muted_inputs(self, names):
        return {n: self._muted.get(n, False) for n in (names or ())}

    def scene_collection(self):
        return self._collection

    def capture_sources(self):
        return dict(self._cameras)

    def stream_target(self):
        return self._target

    def record_directory(self):
        return self._folder


class AudioCheckTests(unittest.TestCase):
    """Muted is a fact; silence is not.

    Someone running a pre-flight is usually not talking, so treating silence as
    a failure would cry wolf on nearly every run. Being muted is unambiguous,
    and measured live it is also the case levels cannot catch: OBS meters read
    before the mute, so a muted microphone still shows a level.
    """

    def test_sound_reaching_obs_passes(self) -> None:
        check = preflight.check_audio({"Mic/Aux": -22.0, "Desktop": -96.0})

        self.assertEqual(check.state, OK)
        self.assertIn("Mic/Aux", check.detail)

    def test_a_muted_input_fails_and_is_named(self) -> None:
        check = preflight.check_audio({"Mic/Aux": -22.0}, {"Mic/Aux": True})

        self.assertEqual(check.state, FAIL)
        self.assertIn("Mic/Aux", check.detail)
        self.assertIn("muted", check.detail)

    def test_a_muted_input_fails_even_while_showing_a_level(self) -> None:
        """Measured live: OBS meters are pre-mute, so the level lies here."""
        check = preflight.check_audio({"Mic/Aux": -12.0}, {"Mic/Aux": True})

        self.assertEqual(check.state, FAIL)

    def test_silence_with_nothing_muted_is_not_a_failure(self) -> None:
        """You are usually not speaking when you run this."""
        check = preflight.check_audio(
            {"Mic/Aux": -96.0, "Desktop": -96.0}, {"Mic/Aux": False}
        )

        self.assertEqual(check.state, WARN)
        self.assertIn("not speaking", check.detail)

    def test_no_readings_at_all_is_not_a_failure(self) -> None:
        """It is a question that went unanswered, and must not read as one."""
        check = preflight.check_audio(None)

        self.assertEqual(check.state, UNCHECKED)

    def test_no_inputs_is_also_unchecked(self) -> None:
        self.assertEqual(preflight.check_audio({}).state, UNCHECKED)

    def test_a_pass_says_why_the_level_alone_proves_nothing(self) -> None:
        """OBS meters read before the mute, so a level is not audibility."""
        check = preflight.check_audio({"Mic/Aux": -20.0})

        self.assertIn("before the mute", check.detail)

    def test_being_told_to_try_again_is_actionable(self) -> None:
        check = preflight.check_audio({"m": -96.0})

        self.assertIn("run it again", check.detail)


class CameraCheckTests(unittest.TestCase):
    """Asked of the kernel, because the picture cannot answer it."""

    def test_a_held_device_passes(self) -> None:
        check = preflight.check_cameras(
            {"Cam": "/dev/video2"}, {"/dev/video2"}, local=True
        )

        self.assertEqual(check.state, OK)

    def test_a_device_obs_does_not_hold_fails(self) -> None:
        check = preflight.check_cameras(
            {"Cam": "/dev/video2"}, {"/dev/video0"}, local=True
        )

        self.assertEqual(check.state, FAIL)
        self.assertIn("Cam", check.detail)

    def test_a_by_id_symlink_resolves_to_the_same_device(self) -> None:
        """Measured live: comparing the two as strings failed a working camera.

        A source is commonly configured through a stable /dev/v4l/by-id path
        while the open descriptor reports /dev/videoN.
        """
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "video4"
            real.write_text("", encoding="utf-8")
            link = Path(tmp) / "usb-Some_Webcam-video-index0"
            link.symlink_to(real)

            check = preflight.check_cameras(
                {"Cam": str(link)}, {str(real)}, local=True
            )

            self.assertEqual(check.state, OK)

    def test_a_source_with_no_device_fails(self) -> None:
        check = preflight.check_cameras({"Cam": ""}, set(), local=True)

        self.assertEqual(check.state, FAIL)

    def test_a_remote_obs_is_not_guessed_at(self) -> None:
        check = preflight.check_cameras({"Cam": "/dev/video0"}, set(), local=False)

        self.assertEqual(check.state, UNCHECKED)
        self.assertIn("another computer", check.detail)

    def test_no_cameras_is_unchecked_rather_than_a_pass(self) -> None:
        self.assertEqual(
            preflight.check_cameras({}, set(), local=True).state, UNCHECKED
        )

    def test_a_pass_admits_it_never_looked_at_the_picture(self) -> None:
        """A closed privacy shutter reads as fine, and that has to be said."""
        check = preflight.check_cameras(
            {"Cam": "/dev/video2"}, {"/dev/video2"}, local=True
        )

        self.assertIn("privacy shutter", check.detail)
        self.assertIn("V4L2", check.detail)


class LocalOnlyTests(unittest.TestCase):
    def test_the_usual_local_hosts_count(self) -> None:
        for host in ("localhost", "127.0.0.1", "::1", "", "LOCALHOST"):
            with self.subTest(host=host):
                self.assertTrue(preflight.is_local(host))

    def test_anything_else_does_not(self) -> None:
        for host in ("192.168.1.40", "obs.local", "streaming-pc"):
            with self.subTest(host=host):
                self.assertFalse(preflight.is_local(host))

    def test_disk_and_folder_are_not_guessed_for_a_remote_obs(self) -> None:
        for check in (
            preflight.check_disk("/tmp", local=False),
            preflight.check_record_folder("/tmp", local=False),
        ):
            with self.subTest(check=check.id):
                self.assertEqual(check.state, UNCHECKED)
                self.assertIn("another computer", check.detail)


class DiskAndFolderTests(unittest.TestCase):
    def test_a_real_folder_reports_its_free_space(self) -> None:
        check = preflight.check_disk(tempfile.gettempdir(), local=True)

        self.assertIn(check.state, (OK, WARN, FAIL))
        self.assertIn("free on", check.detail)

    def test_a_missing_recording_folder_fails(self) -> None:
        missing = str(Path(tempfile.gettempdir()) / "lsd-no-such-folder-42")

        self.assertEqual(
            preflight.check_record_folder(missing, local=True).state, FAIL
        )

    def test_a_writable_recording_folder_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                preflight.check_record_folder(tmp, local=True).state, OK
            )

    def test_a_read_only_recording_folder_fails(self) -> None:
        if os.geteuid() == 0:
            self.skipTest("root can write anywhere")
        with tempfile.TemporaryDirectory() as tmp:
            locked = Path(tmp) / "locked"
            locked.mkdir(mode=0o500)

            self.assertEqual(
                preflight.check_record_folder(str(locked), local=True).state, FAIL
            )


class StreamTargetTests(unittest.TestCase):
    def test_a_configured_key_passes(self) -> None:
        self.assertEqual(preflight.check_stream_target("twitch", True).state, OK)

    def test_a_missing_key_fails(self) -> None:
        self.assertEqual(preflight.check_stream_target("twitch", False).state, FAIL)

    def test_an_unreadable_destination_is_unchecked(self) -> None:
        self.assertEqual(
            preflight.check_stream_target("", None).state, UNCHECKED
        )

    def test_it_says_the_key_itself_was_not_tested(self) -> None:
        check = preflight.check_stream_target("twitch", True)

        self.assertIn("never its value", check.detail)
        self.assertIn("not known until you go live", check.detail)

    def test_the_check_is_given_a_boolean_and_never_the_key(self) -> None:
        """The signature is the safeguard: there is no key to leak.

        The log is a file on disk now, so a stream key reaching a detail string
        would be written to it in plain text.
        """
        import inspect

        parameters = list(
            inspect.signature(preflight.check_stream_target).parameters
        )

        self.assertEqual(parameters, ["service", "has_key"])


class SceneCollectionTests(unittest.TestCase):
    """What replaced a check that could not be decided.

    Comparing the deck's keys against OBS cannot be automated: only the loaded
    collection can be listed, so a key belonging to another one looks exactly
    like a key whose scene was renamed. Measured on a real configuration with
    nine collections, the automatic version called 99 good references broken.
    What is decidable is which collection is loaded, and with several of them
    that is the thing that gets forgotten.
    """

    def test_it_reports_the_loaded_collection_by_name(self) -> None:
        check = preflight.check_collection("Retro night", 9)

        self.assertEqual(check.state, OK)
        self.assertIn("Retro night", check.detail)

    def test_it_says_how_many_there_are_to_confuse(self) -> None:
        self.assertIn("of 9", preflight.check_collection("Retro night", 9).detail)

    def test_a_single_collection_is_not_counted_at_you(self) -> None:
        self.assertNotIn("of 1", preflight.check_collection("Live", 1).detail)

    def test_it_judges_nothing_about_the_keys(self) -> None:
        """It states a fact; deciding is the user's, from where they stand."""
        check = preflight.check_collection("Retro night", 9)

        self.assertIn("cannot tell whether your keys", check.detail)
        self.assertIn("Check keys against OBS", check.detail)

    def test_an_unknown_collection_is_unchecked(self) -> None:
        self.assertEqual(preflight.check_collection("").state, UNCHECKED)

    def test_the_automatic_key_check_is_gone(self) -> None:
        """It could not be decided, so it must not be offered."""
        self.assertFalse(hasattr(preflight, "check_keys"))

    def test_no_check_compares_keys_against_obs(self) -> None:
        ids = {c.id for c in preflight.run(FakeObs(), None)}

        self.assertNotIn("keys", ids)


class CpuAndOutputTests(unittest.TestCase):
    def test_a_quiet_machine_passes(self) -> None:
        self.assertEqual(preflight.check_cpu(9.0).state, OK)

    def test_a_loaded_machine_warns_then_fails(self) -> None:
        self.assertEqual(preflight.check_cpu(80.0).state, WARN)
        self.assertEqual(preflight.check_cpu(95.0).state, FAIL)

    def test_an_unreadable_cpu_is_unchecked(self) -> None:
        self.assertEqual(preflight.check_cpu(None).state, UNCHECKED)

    def test_already_streaming_is_worth_saying(self) -> None:
        check = preflight.check_outputs(streaming=True, recording=False)

        self.assertEqual(check.state, WARN)
        self.assertIn("streaming", check.detail)

    def test_nothing_running_passes(self) -> None:
        self.assertEqual(
            preflight.check_outputs(streaming=False, recording=False).state, OK
        )


class ReportTests(unittest.TestCase):
    def _report(self, *states) -> Report:
        return Report(checks=[Check(str(i), "x", s, "") for i, s in enumerate(states)])

    def test_the_summary_always_names_what_was_not_checked(self) -> None:
        """Otherwise "8 ok" reads as "everything is fine"."""
        summary = self._report(OK, OK, UNCHECKED).summary()

        self.assertIn("2 ok", summary)
        self.assertIn("1 not checked", summary)

    def test_it_never_claims_readiness(self) -> None:
        summary = self._report(OK, OK, OK).summary()

        for word in ("ready", "READY", "all good", "safe"):
            self.assertNotIn(word, summary)

    def test_the_worst_state_leads(self) -> None:
        self.assertEqual(self._report(OK, WARN, FAIL).worst(), FAIL)
        self.assertEqual(self._report(OK, WARN).worst(), WARN)
        self.assertEqual(self._report(OK, UNCHECKED).worst(), UNCHECKED)
        self.assertEqual(self._report(OK, OK).worst(), OK)

    def test_unchecked_outranks_ok(self) -> None:
        """A board of ticks and one question mark is not a clean board."""
        self.assertNotEqual(self._report(OK, UNCHECKED).worst(), OK)

    def test_an_empty_report_says_so(self) -> None:
        self.assertIn("nothing to check", Report().summary())


class RunTests(unittest.TestCase):
    def test_it_yields_results_one_at_a_time(self) -> None:
        """So the deck can paint them as they arrive rather than all at once."""
        import types

        self.assertIsInstance(preflight.run(FakeObs(), None), types.GeneratorType)

    def test_a_disconnected_obs_reports_everything_as_unchecked(self) -> None:
        """Not as failures: that would blame the setup for the connection."""
        checks = list(preflight.run(FakeObs(connected=False), None))

        self.assertEqual(checks[0].state, FAIL)
        self.assertEqual(checks[0].id, "obs")
        self.assertTrue(all(c.state == UNCHECKED for c in checks[1:]))

    def test_every_check_appears_even_when_disconnected(self) -> None:
        connected = [c.id for c in preflight.run(FakeObs(), None)]
        offline = [c.id for c in preflight.run(FakeObs(connected=False), None)]

        self.assertEqual(connected, offline)

    def test_a_remote_obs_skips_the_local_checks(self) -> None:
        checks = {c.id: c for c in preflight.run(FakeObs(host="10.0.0.5"), None)}

        for check_id in ("cameras", "disk", "recording"):
            with self.subTest(check=check_id):
                self.assertEqual(checks[check_id].state, UNCHECKED)

    def test_the_connection_is_reported_first(self) -> None:
        self.assertEqual(next(iter(preflight.run(FakeObs(), None))).id, "obs")

    def test_every_check_carries_a_sentence_and_an_icon(self) -> None:
        for check in preflight.run(FakeObs(), None):
            with self.subTest(check=check.id):
                self.assertTrue(check.detail.strip())
                self.assertTrue(check.icon)
                self.assertTrue(check.label.strip())

    def test_labels_are_short_enough_for_a_key(self) -> None:
        for check in preflight.run(FakeObs(), None):
            with self.subTest(check=check.id):
                self.assertLessEqual(len(check.label), 10)


class BoardAppearanceTests(unittest.TestCase):
    """What the deck draws must not let an unanswered question pass for a tick."""

    def _spec(self, state: str) -> dict:
        return obs_actions.preflight_spec(Check("x", "X", state, "", "mdi:cog"))

    def test_every_state_has_its_own_colour(self) -> None:
        colours = {self._spec(s)["bg"] for s in (OK, WARN, FAIL, UNCHECKED)}

        self.assertEqual(len(colours), 4)

    def test_every_state_has_its_own_badge(self) -> None:
        badges = {self._spec(s)["badge"] for s in (OK, WARN, FAIL, UNCHECKED)}

        self.assertEqual(len(badges), 4)

    def test_an_unchecked_result_is_faded(self) -> None:
        """It reads as "no answer here" rather than as a quiet pass."""
        self.assertTrue(self._spec(UNCHECKED)["unavailable"])

    def test_a_checked_result_is_not_faded(self) -> None:
        for state in (OK, WARN, FAIL):
            with self.subTest(state=state):
                self.assertFalse(self._spec(state)["unavailable"])

    def test_the_unchecked_badge_is_a_question(self) -> None:
        self.assertEqual(self._spec(UNCHECKED)["badge"], "?")

    def test_a_spec_renders(self) -> None:
        from linuxstreamdeck.device import renderer

        for state in (OK, WARN, FAIL, UNCHECKED):
            with self.subTest(state=state):
                image = renderer.compose(size=(72, 72), **self._spec(state))
                self.assertEqual(image.size, (72, 72))


class HeldDeviceTests(unittest.TestCase):
    def test_a_missing_process_holds_nothing(self) -> None:
        self.assertEqual(preflight.held_video_devices(None), set())

    def test_an_unreadable_process_holds_nothing(self) -> None:
        """A pid that has gone must answer empty rather than raise."""
        self.assertEqual(preflight.held_video_devices(999999), set())

    def test_this_process_holds_no_video_devices(self) -> None:
        """Reads a real /proc entry, so the parsing runs against real data."""
        self.assertEqual(preflight.held_video_devices(os.getpid()), set())

    def test_looking_for_obs_never_raises(self) -> None:
        """Whether OBS is running here is not the point; not crashing is."""
        found = preflight.obs_pid()

        self.assertTrue(found is None or isinstance(found, int))


class BoardOverlayTests(unittest.TestCase):
    """The report is a layer over the grid, not a second device mode."""

    def setUp(self) -> None:
        from linuxstreamdeck.core.config import KIND_SINGLE, Config, KeyConfig
        from linuxstreamdeck.core.controller import DeckController
        from linuxstreamdeck.core.events import EventBus

        class Deck:
            connected = False
            key_count = 15
            image_size = (72, 72)
            columns = 5
            dial_count = 0
            screensaver_active = False

            def set_key_image(self, index, image):
                pass

            def record_activity(self):
                return False

            def set_brightness(self, value):
                pass

            def configure_screensaver(self, *args):
                pass

        self.config = Config()
        self.controller = DeckController(
            self.config, EventBus(), SimpleNamespace(connected=False), Deck()
        )
        self.addCleanup(self.controller.shutdown)
        self.page = self.config.pages[0]
        self.page.set_key(
            0, KeyConfig(kind=KIND_SINGLE, action="sys.stopwatch", label="Watch")
        )

    def test_the_board_replaces_what_a_key_draws(self) -> None:
        self.controller.show_board({0: {"label": "Audio", "badge": "?"}})

        spec = self.controller._key_spec(0, self.page.key(0), (72, 72))

        self.assertEqual(spec["label"], "Audio")

    def test_clearing_it_brings_the_real_key_back(self) -> None:
        self.controller.show_board({0: {"label": "Audio"}})
        self.controller.show_board(None)

        spec = self.controller._key_spec(0, self.page.key(0), (72, 72))

        self.assertEqual(spec["label"], "Watch")

    def test_a_slot_the_board_does_not_use_goes_dark(self) -> None:
        """A half-covered grid would read as part of the report."""
        self.controller.show_board({0: {"label": "Audio"}})

        self.assertEqual(
            self.controller._key_spec(4, None, (72, 72)), {"size": (72, 72)}
        )

    def test_the_size_always_comes_from_the_deck(self) -> None:
        self.controller.show_board({0: {"label": "Audio", "size": (99, 99)}})

        spec = self.controller._key_spec(0, self.page.key(0), (72, 72))

        self.assertEqual(spec["size"], (72, 72))

    def test_a_press_dismisses_it_instead_of_running_the_key(self) -> None:
        ran = []
        self.controller._submit_steps = lambda *a, **k: ran.append(a)
        self.controller.show_board({0: {"label": "Audio"}})

        self.controller.press(0)

        self.assertFalse(self.controller.board_active())
        self.assertEqual(ran, [])

    def test_it_does_not_survive_a_change_of_view(self) -> None:
        """A report describes the grid it was run from."""
        self.controller.show_board({0: {"label": "Audio"}})

        self.controller._clear_time_actions()

        self.assertFalse(self.controller.board_active())

    def test_the_configuration_underneath_is_untouched(self) -> None:
        self.controller.show_board({0: {"label": "Audio"}})

        self.assertEqual(self.page.key(0).label, "Watch")


if __name__ == "__main__":
    unittest.main()
