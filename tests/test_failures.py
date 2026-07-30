"""What the deck does when a key cannot do its job.

Three separate silences, all of which used to leave the user pressing a key and
watching nothing happen:

- an action that failed only ever said so in a status bar that shows a message
  for five seconds, inside a window that is normally hidden behind the status
  icon, while the traceback went to a stderr that a session autostart discards;
- a list of actions carried on after one of them failed, with no way to say it
  should not;
- a key that could not work at all rendered exactly like one that was idle.
"""

from __future__ import annotations

import logging
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from linuxstreamdeck import basic_actions as _basic_actions  # noqa: F401
from linuxstreamdeck.core import actions as action_registry
from linuxstreamdeck.core.actions import Action, Param
from linuxstreamdeck.core.config import (
    KIND_FOLDER,
    KIND_MULTI,
    KIND_SINGLE,
    KIND_TOGGLE,
    ON_ERROR_CONTINUE,
    ON_ERROR_STOP,
    ActionStep,
    Config,
    Folder,
    KeyConfig,
)
from linuxstreamdeck.core.controller import DeckController
from linuxstreamdeck.core.events import EventBus
from linuxstreamdeck.device import renderer
from linuxstreamdeck.obs import actions as _obs_actions  # noqa: F401


class FakeDeck:
    connected = False
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


# Two throwaway actions, so a sequence can be made to fail at a chosen point
# without depending on any real action's behaviour. They are put into the
# registry per test and taken out again: it is global state, and leaving them
# there makes them turn up in every other test that audits what is registered.
_RAN: list[str] = []


class _Works(Action):
    id = "test.works"
    name = "Works"
    category = "Test"
    description = "Records that it ran."
    params = [Param("tag", "Tag", default="")]

    def execute(self, ctx, p):
        _RAN.append(p.get("tag", ""))


class _Breaks(Action):
    id = "test.breaks"
    name = "Breaks"
    category = "Test"
    description = "Always raises."

    def execute(self, ctx, p):
        raise RuntimeError("nope")


def use_test_actions(case: unittest.TestCase) -> None:
    """Register the throwaway actions for one test and remove them after."""
    for cls in (_Works, _Breaks):
        action = cls()
        action_registry.REGISTRY[action.id] = action
        case.addCleanup(action_registry.REGISTRY.pop, action.id, None)


def step(action: str, tag: str = "") -> ActionStep:
    return ActionStep(action=action, params={"tag": tag} if tag else {})


class _ControllerCase(unittest.TestCase):
    def setUp(self) -> None:
        _RAN.clear()
        use_test_actions(self)
        # These tests make actions fail on purpose; the controller logs each one
        # with its traceback, which would bury the suite's own output.
        controller_log = logging.getLogger("linuxstreamdeck.core.controller")
        previous = controller_log.level
        controller_log.setLevel(logging.CRITICAL)
        self.addCleanup(controller_log.setLevel, previous)
        self.config = Config()
        self.bus = EventBus()
        self.deck = FakeDeck()
        self.messages: list[str] = []
        self.bus.subscribe("status", lambda _t, d: self.messages.append(d.get("text", "")))
        self.obs = SimpleNamespace(connected=False)
        self.controller = DeckController(self.config, self.bus, self.obs, self.deck)
        self.addCleanup(self.controller.shutdown)
        self.page = self.config.pages[0]

    def _press_and_settle(self, index: int) -> None:
        self.controller.press(index)
        # The steps run on an action worker; wait for them rather than sleeping
        # a fixed amount, so the test neither flakes nor pads the suite.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if self.controller._failed or _RAN:
                break
            time.sleep(0.005)
        time.sleep(0.05)


class StopOnErrorTests(_ControllerCase):
    """Whether a failing action abandons the rest of the key."""

    def _multi(self, on_error: str) -> KeyConfig:
        return KeyConfig(
            kind=KIND_MULTI,
            steps=[step("test.works", "first"), step("test.breaks"),
                   step("test.works", "third")],
            on_error=on_error,
        )

    def test_carrying_on_is_the_default(self) -> None:
        """No existing key may change behaviour by being loaded."""
        self.assertEqual(KeyConfig().on_error, ON_ERROR_CONTINUE)

    def test_by_default_the_rest_of_the_key_still_runs(self) -> None:
        self.page.set_key(0, self._multi(ON_ERROR_CONTINUE))

        self._press_and_settle(0)

        self.assertEqual(_RAN, ["first", "third"])

    def test_stopping_abandons_what_is_left(self) -> None:
        """'Switch scene, wait, record' must not record the wrong scene."""
        self.page.set_key(0, self._multi(ON_ERROR_STOP))

        self._press_and_settle(0)

        self.assertEqual(_RAN, ["first"])

    def test_stopping_says_so_rather_than_only_naming_the_error(self) -> None:
        self.page.set_key(0, self._multi(ON_ERROR_STOP))

        self._press_and_settle(0)

        self.assertTrue(any("was not run" in m for m in self.messages), self.messages)

    def test_carrying_on_does_not_claim_anything_was_skipped(self) -> None:
        self.page.set_key(0, self._multi(ON_ERROR_CONTINUE))

        self._press_and_settle(0)

        self.assertFalse(any("was not run" in m for m in self.messages))

    def test_a_toggle_list_honours_it_too(self) -> None:
        self.page.set_key(
            0,
            KeyConfig(
                kind=KIND_TOGGLE,
                steps_on=[step("test.breaks"), step("test.works", "after")],
                on_error=ON_ERROR_STOP,
            ),
        )

        self._press_and_settle(0)

        self.assertEqual(_RAN, [])

    def test_a_successful_run_is_untouched_by_the_setting(self) -> None:
        self.page.set_key(
            0,
            KeyConfig(
                kind=KIND_MULTI,
                steps=[step("test.works", "a"), step("test.works", "b")],
                on_error=ON_ERROR_STOP,
            ),
        )

        self._press_and_settle(0)

        self.assertEqual(_RAN, ["a", "b"])


class OnErrorStorageTests(unittest.TestCase):
    def test_it_survives_a_round_trip(self) -> None:
        import dataclasses

        key = KeyConfig(kind=KIND_MULTI, on_error=ON_ERROR_STOP)

        restored = KeyConfig.from_dict(dataclasses.asdict(key))

        self.assertEqual(restored.on_error, ON_ERROR_STOP)

    def test_a_key_saved_before_it_existed_carries_on(self) -> None:
        self.assertEqual(
            KeyConfig.from_dict({"kind": KIND_MULTI}).on_error, ON_ERROR_CONTINUE
        )

    def test_a_hand_edited_value_falls_back_rather_than_raising(self) -> None:
        self.assertEqual(
            KeyConfig.from_dict({"on_error": "explode"}).on_error, ON_ERROR_CONTINUE
        )


class FailureFeedbackTests(_ControllerCase):
    """The key itself reports the failure, because nothing else reliably can."""

    def _failing_key(self) -> None:
        self.page.set_key(
            0, KeyConfig(kind=KIND_MULTI, steps=[step("test.breaks")])
        )

    def test_a_failing_key_is_marked(self) -> None:
        self._failing_key()

        self._press_and_settle(0)

        self.assertTrue(self.controller._failed_state(0))

    def test_the_mark_reaches_the_rendered_key(self) -> None:
        self._failing_key()

        self._press_and_settle(0)
        spec = self.controller._key_spec(0, self.page.key(0), (72, 72))

        self.assertTrue(spec["failed"])
        self.assertEqual(spec["badge"], renderer.ERROR_BADGE)

    def test_a_key_that_worked_is_not_marked(self) -> None:
        self.page.set_key(
            0, KeyConfig(kind=KIND_MULTI, steps=[step("test.works", "ok")])
        )

        self._press_and_settle(0)

        self.assertFalse(self.controller._failed_state(0))

    def test_a_single_action_key_is_marked_too(self) -> None:
        self.page.set_key(0, KeyConfig(kind=KIND_SINGLE, action="test.breaks"))

        self._press_and_settle(0)

        self.assertTrue(self.controller._failed_state(0))

    def test_the_mark_expires(self) -> None:
        """It must not go on hiding the key's real state for ever."""
        key = self.controller._tkey(0)
        self.controller._note_failure(key)
        with self.controller._running_lock:
            self.controller._failed[key] = time.monotonic() - 1

        expired = self.controller._expire_failures()

        self.assertEqual(expired, (key,))
        self.assertFalse(self.controller._failed_state(0))

    def test_an_expired_mark_repaints_the_key_one_last_time(self) -> None:
        """Otherwise it stays on the deck until something else repaints the key.

        The tick that expires the last mark is also the tick where the activity
        thread goes back to sleep, so it is the easiest one to return early
        from — and the deck is left showing a failure that is over.
        """
        from linuxstreamdeck.core import controller as controller_module

        previous = controller_module.BUSY_PULSE_SECONDS
        controller_module.BUSY_PULSE_SECONDS = 0.01
        self.addCleanup(
            setattr, controller_module, "BUSY_PULSE_SECONDS", previous
        )
        refreshed: list = []
        self.controller._refresh_runtime_keys = refreshed.extend
        key = self.controller._tkey(0)

        # Set directly, so only the activity thread's own repaint is recorded.
        with self.controller._running_lock:
            self.controller._failed[key] = time.monotonic() - 1
            self.controller._busy_wakeup.set()

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and key not in refreshed:
            time.sleep(0.005)

        self.assertIn(key, refreshed)

    def test_an_unexpired_mark_stays(self) -> None:
        self.controller._note_failure(self.controller._tkey(0))

        self.assertEqual(self.controller._expire_failures(), ())
        self.assertTrue(self.controller._failed_state(0))

    def test_editing_the_key_drops_the_mark(self) -> None:
        """It belonged to the action that used to be there."""
        self._failing_key()
        self._press_and_settle(0)

        self.controller.key_config_changed(0)

        self.assertFalse(self.controller._failed_state(0))

    def test_clearing_the_key_drops_the_mark(self) -> None:
        self._failing_key()
        self._press_and_settle(0)

        self.controller.clear_key(0)

        self.assertFalse(self.controller._failed_state(0))

    def test_moving_the_key_drops_the_mark(self) -> None:
        self._failing_key()
        self._press_and_settle(0)

        self.controller.swap_keys(0, 4)

        self.assertFalse(self.controller._failed_state(0))
        self.assertFalse(self.controller._failed_state(4))

    def test_changing_page_drops_every_mark(self) -> None:
        """Stored indices shift, so a mark would land on a different key."""
        self._failing_key()
        self._press_and_settle(0)

        self.controller._clear_time_actions()

        self.assertFalse(self.controller._failed)

    def test_a_running_key_still_says_it_is_running(self) -> None:
        """A run in flight is the more useful message while it lasts."""
        key = self.controller._tkey(0)
        self.page.set_key(
            0, KeyConfig(kind=KIND_MULTI, steps=[step("test.works")])
        )
        self.controller._note_failure(key)
        self.controller._begin_running(key)
        self.addCleanup(self.controller._end_running, key)

        spec = self.controller._key_spec(0, self.page.key(0), (72, 72))

        self.assertTrue(spec["failed"])
        self.assertEqual(spec["badge"], "RUN")


class UnavailableKeyTests(_ControllerCase):
    """A key that cannot work must not look like one that is merely idle."""

    def _spec(self, key: KeyConfig) -> dict:
        self.page.set_key(0, key)
        return self.controller._key_spec(0, key, (72, 72))

    def test_an_obs_key_is_faded_while_obs_is_closed(self) -> None:
        spec = self._spec(KeyConfig(kind=KIND_SINGLE, action="obs.record"))

        self.assertTrue(spec["unavailable"])

    def test_the_same_key_is_normal_once_obs_is_connected(self) -> None:
        self.obs.connected = True

        spec = self._spec(KeyConfig(kind=KIND_SINGLE, action="obs.record"))

        self.assertNotIn("unavailable", spec)

    def test_a_local_key_is_never_faded(self) -> None:
        spec = self._spec(KeyConfig(kind=KIND_SINGLE, action="sys.stopwatch"))

        self.assertNotIn("unavailable", spec)

    def test_a_key_that_can_still_do_half_its_job_is_not_faded(self) -> None:
        """Fading it would overstate the problem."""
        spec = self._spec(
            KeyConfig(
                kind=KIND_MULTI,
                steps=[step("obs.record"), step("sys.stopwatch")],
            )
        )

        self.assertNotIn("unavailable", spec)

    def test_a_list_of_only_obs_actions_is_faded(self) -> None:
        spec = self._spec(
            KeyConfig(
                kind=KIND_MULTI,
                steps=[step("obs.record"), step("obs.stream")],
            )
        )

        self.assertTrue(spec["unavailable"])

    def test_a_kernel_statistic_keeps_working_so_it_is_not_faded(self) -> None:
        spec = self._spec(
            KeyConfig(
                kind=KIND_SINGLE,
                action="obs.stats",
                params={"metric": "system_cpu"},
            )
        )

        self.assertNotIn("unavailable", spec)

    def test_free_disk_space_is_not_faded_either(self) -> None:
        spec = self._spec(
            KeyConfig(
                kind=KIND_SINGLE, action="obs.stats", params={"metric": "disk"}
            )
        )

        self.assertNotIn("unavailable", spec)

    def test_a_statistic_that_does_come_from_obs_is_faded(self) -> None:
        spec = self._spec(
            KeyConfig(
                kind=KIND_SINGLE, action="obs.stats", params={"metric": "bitrate"}
            )
        )

        self.assertTrue(spec["unavailable"])

    def test_a_folder_is_navigation_and_never_faded(self) -> None:
        spec = self._spec(KeyConfig(kind=KIND_FOLDER, folder=Folder()))

        self.assertNotIn("unavailable", spec)

    def test_an_unknown_action_is_not_judged(self) -> None:
        """Unknown is not the same as unavailable."""
        spec = self._spec(KeyConfig(kind=KIND_SINGLE, action="obs.from_the_future"))

        self.assertNotIn("unavailable", spec)


class ObsDependencyTests(unittest.TestCase):
    def test_every_obs_action_declares_the_dependency(self) -> None:
        """Derived from the category, so a new action cannot forget it."""
        obs_actions = [
            a for a in action_registry.REGISTRY.values()
            if a.category.startswith("OBS")
        ]

        self.assertTrue(obs_actions)
        self.assertTrue(all(a.needs_obs for a in obs_actions))

    def test_no_local_action_claims_it(self) -> None:
        for action in action_registry.REGISTRY.values():
            if action.category.startswith("OBS"):
                continue
            with self.subTest(action=action.id):
                self.assertFalse(action.needs_obs)


class LogFileTests(unittest.TestCase):
    """The log had nowhere to go for the normal case of a session autostart."""

    def setUp(self) -> None:
        from linuxstreamdeck import __main__ as entry

        self.entry = entry
        root = logging.getLogger()
        before = list(root.handlers)

        def restore() -> None:
            for handler in [h for h in root.handlers if h not in before]:
                root.removeHandler(handler)
                handler.close()

        self.addCleanup(restore)

    def test_it_writes_to_the_configuration_directory(self) -> None:
        from linuxstreamdeck.core.config import LOG_FILE

        self.entry._add_file_logging(logging.INFO)
        # At WARNING, so the root logger's own default level cannot filter it
        # before the handler under test ever sees it.
        logging.getLogger("test.log").warning("a line worth keeping")
        for handler in logging.getLogger().handlers:
            handler.flush()

        self.assertTrue(LOG_FILE.exists())
        self.assertIn("a line worth keeping", LOG_FILE.read_text(encoding="utf-8"))

    def test_it_is_rotated_rather_than_growing_for_ever(self) -> None:
        from logging.handlers import RotatingFileHandler

        self.entry._add_file_logging(logging.INFO)
        added = [
            h for h in logging.getLogger().handlers
            if isinstance(h, RotatingFileHandler)
        ]

        self.assertTrue(added)
        self.assertGreater(added[-1].maxBytes, 0)
        self.assertGreater(added[-1].backupCount, 0)

    def test_an_unwritable_directory_does_not_stop_the_application(self) -> None:
        """Losing the log is acceptable; losing the deck is not."""
        from linuxstreamdeck.core import config as config_module

        # It reports the problem, which is the right behaviour and is what would
        # otherwise bury the suite's output.
        entry_log = logging.getLogger("linuxstreamdeck.__main__")
        previous = entry_log.level
        entry_log.setLevel(logging.CRITICAL)
        self.addCleanup(entry_log.setLevel, previous)

        with tempfile.TemporaryDirectory() as tmp:
            blocked = Path(tmp) / "not-a-directory"
            blocked.write_text("", encoding="utf-8")
            original = config_module.CONFIG_DIR, config_module.LOG_FILE
            config_module.CONFIG_DIR = blocked / "inside"
            config_module.LOG_FILE = blocked / "inside" / "x.log"
            try:
                self.entry._add_file_logging(logging.INFO)
            finally:
                config_module.CONFIG_DIR, config_module.LOG_FILE = original


if __name__ == "__main__":
    unittest.main()
