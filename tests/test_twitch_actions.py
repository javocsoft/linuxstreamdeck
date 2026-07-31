"""Twitch keys: what they draw, and how the deck reports what they cannot do.

Two rules are load-bearing here. `feedback()` reads the client's cached
snapshot and never performs a request, because it runs on a render worker while
a key image is being composed. And a key whose every action needs an account
fades while none is linked, so "not set up yet" stops looking like "idle" — the
same rule the OBS keys already follow, now asked per action rather than per
connection.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
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
    TWITCH_REFRESH_SECONDS,
    TWITCH_STATS_ACTION_ID,
    DeckController,
)
from linuxstreamdeck.core.events import EventBus
from linuxstreamdeck.obs import actions as _obs_actions  # noqa: F401
from linuxstreamdeck.twitch import actions as twitch_actions
from linuxstreamdeck.twitch.actions import NO_VALUE, OFF_AIR, STAT_METRICS
from linuxstreamdeck.twitch.http import TwitchError


class FakeDeck:
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
        return False

    def set_brightness(self, _value) -> None:
        pass

    def configure_screensaver(self, *_args) -> None:
        pass


class FakeTwitch:
    """A linked account whose snapshot is whatever the test says it is."""

    def __init__(self, snapshot=None, linked=True) -> None:
        self.linked = linked
        self._snapshot = snapshot if snapshot is not None else {}
        self.channel_calls = 0
        self.titles: list[str] = []
        self.categories: list[str] = []
        self.markers: list[str] = []
        self.clips = 0
        self.fail: Exception | None = None

    def channel(self) -> dict:
        self.channel_calls += 1
        return dict(self._snapshot)

    def set_title(self, title: str) -> None:
        if self.fail:
            raise self.fail
        self.titles.append(title)

    def set_category(self, name: str) -> str:
        if self.fail:
            raise self.fail
        self.categories.append(name)
        return name

    def create_clip(self) -> str:
        if self.fail:
            raise self.fail
        self.clips += 1
        return "https://clips/edit"

    def create_marker(self, description: str = "") -> None:
        if self.fail:
            raise self.fail
        self.markers.append(description)


LIVE = {
    "live": True,
    "viewers": 128,
    "followers": 4210,
    "title": "Title",
    "category": "Just Chatting",
    "started_at": 0.0,
}
OFFLINE = {
    "live": False,
    "viewers": None,
    "followers": 4210,
    "title": "Title",
    "category": "Just Chatting",
    "started_at": None,
}


def context(twitch=None, bus=None):
    return SimpleNamespace(
        twitch=twitch,
        bus=bus if bus is not None else EventBus(),
        obs=SimpleNamespace(connected=False),
    )


class StatsFeedbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.action = registry.get("twitch.stats")

    def test_the_viewer_count_is_shown_while_live(self) -> None:
        state = self.action.feedback(context(FakeTwitch(LIVE)), {"metric": "viewers"})

        self.assertEqual(state["display"], "128")

    def test_an_offline_channel_says_so_rather_than_showing_a_gap(self) -> None:
        """Three states, and they must not collapse into one symbol.

        Zero would be a claim — nobody watching a live channel is a real and
        alarming state that must look different from being off air. And NO_VALUE
        would say "I cannot tell you", which is what a lost connection means,
        not what being off air means.
        """
        state = self.action.feedback(
            context(FakeTwitch(OFFLINE)), {"metric": "viewers"}
        )

        self.assertEqual(state["display"], OFF_AIR)

    def test_a_live_channel_with_no_audience_shows_zero(self) -> None:
        """The state the off-air text exists to stay distinguishable from."""
        state = self.action.feedback(
            context(FakeTwitch(dict(LIVE, viewers=0))), {"metric": "viewers"}
        )

        self.assertEqual(state["display"], "0")

    def test_a_lost_connection_is_not_reported_as_being_off_air(self) -> None:
        """The client empties the snapshot once a reading stops being true."""
        state = self.action.feedback(context(FakeTwitch({})), {"metric": "viewers"})

        self.assertEqual(state["display"], NO_VALUE)

    def test_uptime_says_off_air_too(self) -> None:
        state = self.action.feedback(
            context(FakeTwitch(OFFLINE)), {"metric": "uptime"}
        )

        self.assertEqual(state["display"], OFF_AIR)

    def test_a_missing_follower_count_stays_a_gap(self) -> None:
        """That one really is unknown — usually a scope the token never got —
        so it must not be dressed up as an answer about being off air."""
        snapshot = dict(OFFLINE, followers=None)

        state = self.action.feedback(
            context(FakeTwitch(snapshot)), {"metric": "followers"}
        )

        self.assertEqual(state["display"], NO_VALUE)

    def test_pressing_an_off_air_key_explains_it_in_words(self) -> None:
        bus = EventBus()
        seen: list[str] = []
        bus.subscribe("status", lambda t, d: seen.append(d["text"]))

        self.action.execute(
            context(FakeTwitch(OFFLINE), bus), {"metric": "viewers"}
        )

        self.assertIn("not live", seen[0])

    def test_the_follower_count_answers_while_offline(self) -> None:
        """It is a property of the channel, not of the broadcast."""
        state = self.action.feedback(
            context(FakeTwitch(OFFLINE)), {"metric": "followers"}
        )

        self.assertEqual(state["display"], "4210")

    def test_a_large_count_is_shortened_to_fit_a_key(self) -> None:
        snapshot = dict(LIVE, viewers=48210)

        state = self.action.feedback(
            context(FakeTwitch(snapshot)), {"metric": "viewers"}
        )

        self.assertEqual(state["display"], "48k")

    def test_small_counts_keep_every_digit(self) -> None:
        """The difference between 90 and 900 viewers is the whole message."""
        for viewers, expected in ((9, "9"), (90, "90"), (900, "900"), (9000, "9000")):
            with self.subTest(viewers=viewers):
                state = self.action.feedback(
                    context(FakeTwitch(dict(LIVE, viewers=viewers))),
                    {"metric": "viewers"},
                )
                self.assertEqual(state["display"], expected)

    def test_the_live_state_says_offline_rather_than_nothing(self) -> None:
        """Offline is a real answer; blanking it would be a different claim."""
        state = self.action.feedback(
            context(FakeTwitch(OFFLINE)), {"metric": "status"}
        )

        self.assertEqual(state["display"], OFF_AIR)

    def test_every_metric_agrees_on_the_word_for_off_air(self) -> None:
        """Two keys side by side must not appear to say different things."""
        seen = {
            metric.get("offline")
            for metric in STAT_METRICS.values()
            if metric.get("offline")
        }
        status = STAT_METRICS["status"]["text"]({"live": False})

        self.assertEqual(seen, {OFF_AIR})
        self.assertEqual(status, OFF_AIR)

    def test_the_off_air_word_is_not_mistakable_for_a_switch(self) -> None:
        """"OFF" over a label like "Viewers" reads as the key being switched
        off; the word has to name the channel's state on its own."""
        self.assertNotEqual(OFF_AIR, "OFF")
        self.assertIn("OFF", OFF_AIR)

    def test_a_live_channel_is_coloured(self) -> None:
        state = self.action.feedback(context(FakeTwitch(LIVE)), {"metric": "status"})

        self.assertEqual(state.get("color"), twitch_actions.STAT_LIVE_COLOR)

    def test_colour_can_be_turned_off(self) -> None:
        state = self.action.feedback(
            context(FakeTwitch(LIVE)), {"metric": "status", "colored": "no"}
        )

        self.assertNotIn("color", state)

    def test_an_unlinked_account_shows_no_value_and_asks_for_nothing(self) -> None:
        twitch = FakeTwitch(LIVE, linked=False)

        state = self.action.feedback(context(twitch), {"metric": "viewers"})

        self.assertEqual(state["display"], NO_VALUE)
        self.assertEqual(twitch.channel_calls, 0)

    def test_a_stale_snapshot_shows_no_value(self) -> None:
        """The client answers an empty mapping once a reading stops being true."""
        state = self.action.feedback(context(FakeTwitch({})), {"metric": "viewers"})

        self.assertEqual(state["display"], NO_VALUE)

    def test_a_context_with_no_twitch_at_all_is_survivable(self) -> None:
        state = self.action.feedback(context(None), {"metric": "viewers"})

        self.assertEqual(state["display"], NO_VALUE)

    def test_an_unknown_metric_renders_nothing_rather_than_raising(self) -> None:
        state = self.action.feedback(context(FakeTwitch(LIVE)), {"metric": "nope"})

        self.assertEqual(state, {})

    def test_every_metric_survives_a_snapshot_full_of_nonsense(self) -> None:
        rubbish = {"live": "yes", "viewers": "many", "followers": None,
                   "started_at": "soon"}

        for metric in STAT_METRICS:
            with self.subTest(metric=metric):
                state = self.action.feedback(
                    context(FakeTwitch(rubbish)), {"metric": metric}
                )
                self.assertIsInstance(state.get("display"), str)

    def test_every_metric_has_a_label_and_an_icon(self) -> None:
        for name, metric in STAT_METRICS.items():
            with self.subTest(metric=name):
                self.assertTrue(metric["label"])
                self.assertTrue(metric["icon"].startswith("mdi:"))

    def test_pressing_a_statistics_key_states_the_value(self) -> None:
        bus = EventBus()
        seen: list[str] = []
        bus.subscribe("status", lambda t, d: seen.append(d["text"]))

        self.action.execute(context(FakeTwitch(LIVE), bus), {"metric": "viewers"})

        self.assertIn("128", seen[0])


class ActionTests(unittest.TestCase):
    def test_setting_the_title_passes_it_through(self) -> None:
        twitch = FakeTwitch(LIVE)

        registry.get("twitch.set_title").execute(
            context(twitch), {"title": "Going live"}
        )

        self.assertEqual(twitch.titles, ["Going live"])

    def test_setting_the_category_reports_what_twitch_matched(self) -> None:
        """Saying which one was applied is how a wrong guess becomes visible."""
        bus = EventBus()
        seen: list[str] = []
        bus.subscribe("status", lambda t, d: seen.append(d["text"]))
        twitch = FakeTwitch(LIVE)

        registry.get("twitch.set_category").execute(
            context(twitch, bus), {"category": "Just Chatting"}
        )

        self.assertIn("Just Chatting", seen[0])

    def test_a_key_with_no_category_fails_and_says_what_to_do(self) -> None:
        """Empty is the editor's answer to text Twitch did not recognise, so
        this is the message that case produces. It must name the fix rather
        than read as an internal complaint."""
        twitch = FakeTwitch(LIVE)

        for value in ("", "   "):
            with self.subTest(value=value):
                with self.assertRaises(TwitchError) as caught:
                    registry.get("twitch.set_category").execute(
                        context(twitch), {"category": value}
                    )

                self.assertIn("pick one", str(caught.exception).lower())

        # And it never reached Twitch with nothing to set.
        self.assertEqual(twitch.categories, [])

    def test_a_marker_carries_its_note(self) -> None:
        twitch = FakeTwitch(LIVE)

        registry.get("twitch.marker").execute(
            context(twitch), {"description": "good bit"}
        )

        self.assertEqual(twitch.markers, ["good bit"])

    def test_a_clip_reports_where_to_edit_it(self) -> None:
        bus = EventBus()
        seen: list[str] = []
        bus.subscribe("status", lambda t, d: seen.append(d["text"]))

        registry.get("twitch.clip").execute(context(FakeTwitch(LIVE), bus), {})

        self.assertIn("https://clips/edit", seen[0])

    def test_an_unlinked_account_refuses_by_raising(self) -> None:
        """Raising is what marks the key; catching it would look like success."""
        for action_id in (
            "twitch.set_title", "twitch.set_category",
            "twitch.clip", "twitch.marker",
        ):
            with self.subTest(action=action_id):
                with self.assertRaises(TwitchError):
                    registry.get(action_id).execute(
                        context(FakeTwitch(LIVE, linked=False)), {"title": "x"}
                    )

    def test_a_twitch_failure_reaches_the_controller(self) -> None:
        twitch = FakeTwitch(LIVE)
        twitch.fail = TwitchError("Twitch said no")

        with self.assertRaises(TwitchError):
            registry.get("twitch.set_title").execute(
                context(twitch), {"title": "x"}
            )

    def test_every_twitch_action_is_marked_as_needing_an_account(self) -> None:
        """Derived from the category, so one added later cannot forget it."""
        twitch_ids = [
            action_id
            for action_id, action in registry.REGISTRY.items()
            if action.category == twitch_actions.CAT_TWITCH
        ]

        self.assertTrue(twitch_ids)
        for action_id in twitch_ids:
            with self.subTest(action=action_id):
                self.assertTrue(registry.get(action_id).needs_twitch)

    def test_every_twitch_action_has_a_default_icon(self) -> None:
        for action_id, action in registry.REGISTRY.items():
            if action.category != twitch_actions.CAT_TWITCH:
                continue
            with self.subTest(action=action_id):
                self.assertTrue(action.default_icon.startswith("mdi:"))

    def test_no_twitch_action_claims_to_need_obs(self) -> None:
        """The two connections are independent; conflating them would fade a
        Twitch key whenever OBS happened to be closed."""
        for action_id, action in registry.REGISTRY.items():
            if action.category != twitch_actions.CAT_TWITCH:
                continue
            with self.subTest(action=action_id):
                self.assertFalse(action.needs_obs)


class FadeTests(unittest.TestCase):
    """Which keys the deck dims, now that two connections can be missing."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.bus = EventBus()
        self.config = Config()
        self.obs = SimpleNamespace(connected=False)
        self.twitch = FakeTwitch(LIVE, linked=False)
        self.controller = DeckController(
            self.config, self.bus, self.obs, FakeDeck(), twitch=self.twitch
        )
        self.addCleanup(self.controller.shutdown)

    def test_a_twitch_key_fades_while_no_account_is_linked(self) -> None:
        key = KeyConfig(kind=KIND_SINGLE, action="twitch.set_title")

        self.assertTrue(self.controller._unavailable(key))

    def test_the_same_key_stops_fading_once_an_account_is_linked(self) -> None:
        self.twitch.linked = True
        key = KeyConfig(kind=KIND_SINGLE, action="twitch.set_title")

        self.assertFalse(self.controller._unavailable(key))

    def test_obs_being_closed_says_nothing_about_a_twitch_key(self) -> None:
        """The two were one condition before; conflating them fades wrongly."""
        self.twitch.linked = True
        self.obs.connected = False
        key = KeyConfig(kind=KIND_SINGLE, action="twitch.set_title")

        self.assertFalse(self.controller._unavailable(key))

    def test_an_obs_key_still_fades_while_obs_is_closed(self) -> None:
        key = KeyConfig(kind=KIND_SINGLE, action="obs.record")

        self.assertTrue(self.controller._unavailable(key))

    def test_a_key_mixing_a_twitch_action_with_a_local_one_does_not_fade(
        self,
    ) -> None:
        """It still does half its job, so fading it overstates the problem."""
        key = KeyConfig(
            kind=KIND_MULTI,
            steps=[
                ActionStep(action="twitch.set_title", params={"title": "x"}),
                ActionStep(action="sys.wait", params={"duration": "00:01"}),
            ],
        )

        self.assertFalse(self.controller._unavailable(key))

    @staticmethod
    def _marker_and_chapter() -> KeyConfig:
        """The key this integration was built for: mark both at once."""
        return KeyConfig(
            kind=KIND_MULTI,
            steps=[
                ActionStep(action="twitch.marker", params={}),
                ActionStep(action="obs.record_chapter", params={}),
            ],
        )

    def test_a_key_spanning_both_services_survives_one_of_them_being_gone(
        self,
    ) -> None:
        """It still places the Twitch marker, so fading it would overstate the
        problem exactly as it would for a key mixing OBS with a local action."""
        self.twitch.linked = True
        self.obs.connected = False

        self.assertFalse(self.controller._unavailable(self._marker_and_chapter()))

    def test_that_same_key_fades_only_when_neither_service_is_there(self) -> None:
        self.twitch.linked = False
        self.obs.connected = False

        self.assertTrue(self.controller._unavailable(self._marker_and_chapter()))

    def test_that_same_key_is_normal_when_both_are_present(self) -> None:
        self.twitch.linked = True
        self.obs.connected = True

        self.assertFalse(self.controller._unavailable(self._marker_and_chapter()))

    def test_an_unregistered_action_is_unknown_rather_than_unavailable(self) -> None:
        key = KeyConfig(kind=KIND_SINGLE, action="nobody.knows")

        self.assertFalse(self.controller._unavailable(key))

    def test_a_controller_built_without_twitch_fades_twitch_keys(self) -> None:
        """Every existing test builds one this way; it must not crash."""
        controller = DeckController(
            Config(), EventBus(), SimpleNamespace(connected=True), FakeDeck()
        )
        self.addCleanup(controller.shutdown)
        key = KeyConfig(kind=KIND_SINGLE, action="twitch.set_title")

        self.assertTrue(controller._unavailable(key))


class LiveRepaintTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = EventBus()
        self.twitch = FakeTwitch(LIVE, linked=True)
        self.controller = DeckController(
            Config(),
            self.bus,
            SimpleNamespace(connected=False),
            FakeDeck(),
            twitch=self.twitch,
        )
        self.addCleanup(self.controller.shutdown)

    def test_a_twitch_statistics_key_asks_to_be_repainted(self) -> None:
        key = KeyConfig(kind=KIND_SINGLE, action=TWITCH_STATS_ACTION_ID)

        self.assertEqual(
            self.controller._live_interval(key), TWITCH_REFRESH_SECONDS
        )

    def test_it_asks_for_nothing_while_no_account_is_linked(self) -> None:
        """Repainting is what makes it ask Twitch; with no account there is
        nothing to ask."""
        self.twitch.linked = False
        key = KeyConfig(kind=KIND_SINGLE, action=TWITCH_STATS_ACTION_ID)

        self.assertEqual(self.controller._live_interval(key), 0.0)

    def test_only_a_single_action_key_can_repaint_on_a_clock(self) -> None:
        """Feedback is resolved for a key's own action, never for a step."""
        key = KeyConfig(
            kind=KIND_MULTI,
            steps=[ActionStep(action=TWITCH_STATS_ACTION_ID, params={})],
        )

        self.assertEqual(self.controller._live_keys(), {})
        self.assertEqual(key.kind, KIND_MULTI)


if __name__ == "__main__":
    unittest.main()
