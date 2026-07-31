"""Ad breaks, raids, chat announcements, and Twitch inside the pre-flight.

The pre-flight half carries the weight here. Its rule has always been that a
question which could not be answered is reported as unanswered rather than
quietly dropped or blamed on the user, and Twitch adds three new ways for that
to matter: no account connected, an account whose authorization predates some
of the keys, and a network that happened to be down at the moment somebody
asked whether they were ready to go live.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from linuxstreamdeck import basic_actions as _basic_actions  # noqa: F401
from linuxstreamdeck.core import actions as registry
from linuxstreamdeck.core import preflight
from linuxstreamdeck.core.events import EventBus
from linuxstreamdeck.core.preflight import FAIL, OK, UNCHECKED, WARN
from linuxstreamdeck.obs import actions as _obs_actions  # noqa: F401
from linuxstreamdeck.twitch import actions as twitch_actions  # noqa: F401
from linuxstreamdeck.twitch import client as client_module
from linuxstreamdeck.twitch.client import TwitchClient
from linuxstreamdeck.twitch.constants import SCOPES
from linuxstreamdeck.twitch.http import TwitchError


class ClientCase(unittest.TestCase):
    def setUp(self) -> None:
        self.answers: list = []
        self.requests: list[dict] = []
        original = client_module.request_json
        client_module.request_json = self._transport
        self.addCleanup(setattr, client_module, "request_json", original)
        self.client = TwitchClient(EventBus(), store=None, client_id="c")
        self.client._pool.shutdown(wait=False)
        from linuxstreamdeck.twitch import auth

        self.client._tokens = auth.Tokens(
            access="a", refresh="r", expires_at=10**10, login="me", user_id="42"
        )
        self.addCleanup(self.client.stop)

    def _transport(self, method, url, **kwargs):
        self.requests.append({"method": method, "url": url, **kwargs})
        if not self.answers:
            raise AssertionError(f"Unexpected request to {url}")
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class CommercialTests(ClientCase):
    def setUp(self) -> None:
        super().setUp()
        # Eligibility is established before the request, so an account that can
        # run ads has to be stated or every test here stops at the refusal.
        self.client._broadcaster_type = "affiliate"

    def test_it_asks_for_the_requested_length(self) -> None:
        self.answers = [{"data": [{"length": 60, "retry_after": 480}]}]

        length, retry = self.client.start_commercial(60)

        self.assertEqual(self.requests[0]["body"]["length"], 60)
        self.assertEqual((length, retry), (60, 480))

    def test_a_silent_answer_still_reports_the_length_asked_for(self) -> None:
        self.answers = [{}]

        length, retry = self.client.start_commercial(90)

        self.assertEqual(length, 90)
        self.assertEqual(retry, 0)

    def test_the_key_reports_the_cooldown(self) -> None:
        """Without it the next press is a refusal nobody saw coming."""
        bus = EventBus()
        seen: list[str] = []
        bus.subscribe("status", lambda t, d: seen.append(d["text"]))
        self.answers = [{"data": [{"length": 60, "retry_after": 480}]}]
        ctx = SimpleNamespace(
            twitch=SimpleNamespace(
                linked=True, start_commercial=self.client.start_commercial
            ),
            bus=bus,
            obs=SimpleNamespace(connected=False),
        )

        registry.get("twitch.commercial").execute(ctx, {"length": "60"})

        self.assertIn("8m 00s", seen[0])

    def test_every_offered_length_is_one_twitch_accepts(self) -> None:
        action = registry.get("twitch.commercial")
        param = next(p for p in action.params if p.name == "length")

        self.assertEqual(
            [int(c) for c in param.choices], list(client_module.COMMERCIAL_LENGTHS)
        )


class AdEligibilityTests(ClientCase):
    """Only Affiliates and Partners can run ads, and Twitch will not say so.

    Its own issue tracker has this endpoint answering an ordinary account with
    a cooldown it can never wait out — the 429 that started this — and
    sometimes with a plain success, for an ad that never ran. Neither can be
    read, so eligibility is established from the one field that states it.
    """

    def test_an_affiliate_may_run_ads(self) -> None:
        self.answers = [{"data": [{"broadcaster_type": "affiliate"}]}]

        self.assertIs(self.client.can_run_ads(), True)

    def test_a_partner_may_too(self) -> None:
        self.answers = [{"data": [{"broadcaster_type": "partner"}]}]

        self.assertIs(self.client.can_run_ads(), True)

    def test_an_ordinary_account_may_not(self) -> None:
        """Twitch reports it as an empty broadcaster type."""
        self.answers = [{"data": [{"broadcaster_type": ""}]}]

        self.assertIs(self.client.can_run_ads(), False)

    def test_a_failed_lookup_answers_unknown_rather_than_no(self) -> None:
        """A lookup that could not be made must never disable a working key."""
        self.answers = [TwitchError("network down")]

        self.assertIsNone(self.client.can_run_ads())

    def test_it_is_looked_up_once(self) -> None:
        self.answers = [{"data": [{"broadcaster_type": "affiliate"}]}]

        self.client.can_run_ads()
        self.client.can_run_ads()

        self.assertEqual(len(self.requests), 1)

    def test_an_ineligible_account_is_refused_before_asking_twitch(self) -> None:
        """Otherwise the answer is a cooldown that never expires."""
        self.client._broadcaster_type = ""

        with self.assertRaises(TwitchError) as caught:
            self.client.start_commercial(60)

        self.assertIn("Affiliates and Partners", str(caught.exception))
        self.assertEqual(self.requests, [])

    def test_an_unknown_account_type_still_tries(self) -> None:
        """Unknown is not a refusal; the request is where the truth is."""
        self.answers = [
            TwitchError("could not look up"),
            {"data": [{"length": 60, "retry_after": 480}]},
        ]

        length, _retry = self.client.start_commercial(60)

        self.assertEqual(length, 60)

    def test_linking_another_account_forgets_the_old_type(self) -> None:
        from linuxstreamdeck.twitch import auth

        self.client._broadcaster_type = "partner"

        self.client.link(auth.Tokens(access="a", refresh="r"))

        self.assertIsNone(self.client._broadcaster_type)

    def test_the_action_declares_that_it_needs_one(self) -> None:
        self.assertTrue(registry.get("twitch.commercial").twitch_needs_affiliate)

    def test_no_other_action_claims_to(self) -> None:
        """Only ads depend on it; marking anything else would fade a key that
        works perfectly well."""
        for action_id, action in registry.REGISTRY.items():
            if action_id == "twitch.commercial":
                continue
            with self.subTest(action=action_id):
                self.assertFalse(action.twitch_needs_affiliate)


class RaidTests(ClientCase):
    def test_a_raid_resolves_the_channel_and_sends_both_ids(self) -> None:
        self.answers = [{"data": [{"id": "99", "login": "friend"}]}, {}]

        self.client.start_raid("friend")

        self.assertIn("users", self.requests[0]["url"])
        self.assertEqual(self.requests[1]["params"]["from_broadcaster_id"], "42")
        self.assertEqual(self.requests[1]["params"]["to_broadcaster_id"], "99")

    def test_a_leading_at_sign_is_accepted(self) -> None:
        """People write channel names with one, and it is not part of it."""
        self.answers = [{"data": [{"id": "99"}]}, {}]

        self.client.start_raid("@friend")

        self.assertEqual(self.requests[0]["params"]["login"], "friend")

    def test_a_channel_that_does_not_exist_is_reported(self) -> None:
        self.answers = [{"data": []}]

        with self.assertRaises(TwitchError) as caught:
            self.client.start_raid("nobody")

        self.assertIn("nobody", str(caught.exception))

    def test_an_empty_target_never_reaches_twitch(self) -> None:
        with self.assertRaises(TwitchError):
            self.client.start_raid("   ")

        self.assertEqual(self.requests, [])

    def test_the_same_channel_is_resolved_once(self) -> None:
        self.answers = [{"data": [{"id": "99"}]}, {}, {}]

        self.client.start_raid("friend")
        self.client.start_raid("FRIEND")

        lookups = [r for r in self.requests if "users" in r["url"]]
        self.assertEqual(len(lookups), 1)

    def test_cancelling_uses_delete(self) -> None:
        self.answers = [{}]

        self.client.cancel_raid()

        self.assertEqual(self.requests[0]["method"], "DELETE")
        self.assertEqual(self.requests[0]["params"]["broadcaster_id"], "42")

    def test_the_key_says_the_raid_still_has_to_be_confirmed(self) -> None:
        """Twitch opens a countdown on the broadcaster's chat rather than
        moving anyone, and a key that implied otherwise would be frightening
        to press."""
        bus = EventBus()
        seen: list[str] = []
        bus.subscribe("status", lambda t, d: seen.append(d["text"]))
        self.answers = [{"data": [{"id": "99"}]}, {}]
        ctx = SimpleNamespace(
            twitch=SimpleNamespace(
                linked=True,
                start_raid=self.client.start_raid,
                cancel_raid=self.client.cancel_raid,
            ),
            bus=bus,
            obs=SimpleNamespace(connected=False),
        )

        registry.get("twitch.raid").execute(
            ctx, {"mode": "start", "channel": "friend"}
        )

        self.assertIn("confirm", seen[0].lower())

    def test_a_key_with_no_target_says_what_to_do(self) -> None:
        ctx = SimpleNamespace(
            twitch=SimpleNamespace(linked=True), bus=EventBus(),
            obs=SimpleNamespace(connected=False),
        )

        with self.assertRaises(TwitchError) as caught:
            registry.get("twitch.raid").execute(
                ctx, {"mode": "start", "channel": ""}
            )

        self.assertIn("pick one", str(caught.exception).lower())

    def test_cancelling_needs_no_channel(self) -> None:
        cancelled: list[bool] = []
        ctx = SimpleNamespace(
            twitch=SimpleNamespace(
                linked=True, cancel_raid=lambda: cancelled.append(True)
            ),
            bus=EventBus(),
            obs=SimpleNamespace(connected=False),
        )

        registry.get("twitch.raid").execute(ctx, {"mode": "cancel", "channel": ""})

        self.assertEqual(cancelled, [True])


class ChannelSearchTests(ClientCase):
    def test_live_channels_are_offered_first(self) -> None:
        """A raid goes to somebody who is streaming, and Twitch returns both."""
        self.answers = [{"data": [
            {"broadcaster_login": "offline_one", "id": "1", "is_live": False},
            {"broadcaster_login": "live_one", "id": "2", "is_live": True},
        ]}]

        names = self.client.search_channels("one")

        self.assertEqual(names[0], "live_one")

    def test_searching_primes_the_raid_lookup(self) -> None:
        self.answers = [
            {"data": [{"broadcaster_login": "friend", "id": "99", "is_live": True}]},
            {},
        ]

        self.client.search_channels("frie")
        self.client.start_raid("friend")

        lookups = [r for r in self.requests if "helix/users" in r["url"]]
        self.assertEqual(lookups, [])

    def test_a_failure_answers_nothing(self) -> None:
        self.answers = [TwitchError("down")]

        self.assertEqual(self.client.search_channels("x"), [])

    def test_an_empty_query_asks_nothing(self) -> None:
        self.assertEqual(self.client.search_channels("  "), [])
        self.assertEqual(self.requests, [])


class AnnouncementTests(ClientCase):
    def test_it_posts_as_the_broadcaster_moderating_their_own_chat(self) -> None:
        self.answers = [{}]

        self.client.announce("hello", "green")

        params = self.requests[0]["params"]
        self.assertEqual(params["broadcaster_id"], "42")
        self.assertEqual(params["moderator_id"], "42")
        self.assertEqual(self.requests[0]["body"]["color"], "green")

    def test_a_long_message_is_trimmed_rather_than_refused(self) -> None:
        self.answers = [{}]

        self.client.announce("x" * 900)

        self.assertEqual(
            len(self.requests[0]["body"]["message"]),
            client_module.MAX_ANNOUNCEMENT_CHARS,
        )

    def test_an_empty_message_never_reaches_twitch(self) -> None:
        with self.assertRaises(TwitchError):
            self.client.announce("   ")

        self.assertEqual(self.requests, [])

    def test_every_offered_colour_is_one_twitch_accepts(self) -> None:
        action = registry.get("twitch.announce")
        param = next(p for p in action.params if p.name == "color")

        self.assertEqual(
            list(param.choices), list(client_module.ANNOUNCEMENT_COLORS)
        )


class MissingScopeTests(ClientCase):
    """An account linked before an action existed cannot perform it.

    Twitch names the permission and stops. What somebody pressing a key needs
    is the sentence that follows, and — better still — to have seen the key
    faded before pressing it, because for these actions the press happens live.
    """

    def test_the_refusal_becomes_something_to_act_on(self) -> None:
        from linuxstreamdeck.twitch.http import TwitchHTTPError

        self.answers = [TwitchHTTPError(
            401, "User access token requires the moderator:manage:announcements scope."
        )]

        with self.assertRaises(TwitchError) as caught:
            self.client.announce("hello")

        text = str(caught.exception)
        self.assertIn("connect again", text.lower())
        self.assertIn("moderator:manage:announcements", text)

    def test_it_stays_an_http_error_so_optional_scopes_still_degrade(
        self,
    ) -> None:
        """The follower count tolerates a 401 and keeps the rest of the
        snapshot. Making this a plain error broke exactly that and lost the
        viewer count along with it."""
        from linuxstreamdeck.twitch.http import TwitchHTTPError, TwitchScopeError

        self.assertTrue(issubclass(TwitchScopeError, TwitchHTTPError))
        error = TwitchScopeError(401, "Missing scope: x:y", "do this instead")
        self.assertEqual(error.status, 401)
        self.assertEqual(str(error), "do this instead")

    def test_a_refusal_naming_no_scope_still_says_what_to_do(self) -> None:
        from linuxstreamdeck.twitch.client import missing_scope_message

        text = missing_scope_message("this token lacks permission")

        self.assertIn("connect again", text.lower())

    def test_the_named_scope_is_pulled_out_of_the_sentence(self) -> None:
        from linuxstreamdeck.twitch.client import _named_scope

        self.assertEqual(
            _named_scope("User access token requires the clips:edit scope."),
            "clips:edit",
        )
        self.assertEqual(_named_scope("nothing here"), "")

    def test_every_action_that_needs_a_permission_declares_it(self) -> None:
        """The declaration is what lets the deck fade the key beforehand."""
        for action_id in (
            "twitch.set_title", "twitch.set_category", "twitch.clip",
            "twitch.marker", "twitch.commercial", "twitch.raid",
            "twitch.announce",
        ):
            with self.subTest(action=action_id):
                scope = registry.get(action_id).twitch_scope
                self.assertTrue(scope)
                self.assertIn(scope, SCOPES)


class ScopeFadeTests(unittest.TestCase):
    """Which keys the deck dims once a permission can be missing."""

    def setUp(self) -> None:
        from linuxstreamdeck.core.config import Config
        from linuxstreamdeck.core.controller import DeckController

        self.twitch = SimpleNamespace(
            linked=True,
            missing_scopes=lambda: self.missing,
            can_run_ads=lambda: True,
        )
        self.missing: tuple = ()
        deck = SimpleNamespace(
            key_count=15, image_size=(72, 72), columns=5, dial_count=0,
            screensaver_active=False, set_key_image=lambda *a: None,
            record_activity=lambda: False, set_brightness=lambda *a: None,
            configure_screensaver=lambda *a: None,
        )
        self.controller = DeckController(
            Config(), EventBus(), SimpleNamespace(connected=False), deck,
            twitch=self.twitch,
        )
        self.addCleanup(self.controller.shutdown)

    def _key(self, action_id: str):
        from linuxstreamdeck.core.config import KIND_SINGLE, KeyConfig

        return KeyConfig(kind=KIND_SINGLE, action=action_id)

    def test_a_key_needing_a_permission_the_account_lacks_is_faded(self) -> None:
        self.missing = ("moderator:manage:announcements",)

        self.assertTrue(self.controller._unavailable(self._key("twitch.announce")))

    def test_another_key_is_left_alone(self) -> None:
        """Only the actions that need the missing permission are affected."""
        self.missing = ("moderator:manage:announcements",)

        self.assertFalse(self.controller._unavailable(self._key("twitch.clip")))

    def test_a_complete_authorization_fades_nothing(self) -> None:
        self.missing = ()

        for action_id in ("twitch.announce", "twitch.raid", "twitch.commercial"):
            with self.subTest(action=action_id):
                self.assertFalse(
                    self.controller._unavailable(self._key(action_id))
                )

    def test_an_authorization_with_unrecorded_scopes_is_not_treated_as_broken(
        self,
    ) -> None:
        """Unknown is not unavailable, the same rule the rest of the deck
        follows."""
        self.twitch.missing_scopes = lambda: ()

        self.assertFalse(self.controller._unavailable(self._key("twitch.announce")))

    def test_a_client_that_cannot_answer_does_not_fade_the_key(self) -> None:
        def boom():
            raise RuntimeError("no")

        self.twitch.missing_scopes = boom

        self.assertFalse(self.controller._unavailable(self._key("twitch.announce")))

    def test_no_account_still_fades_everything(self) -> None:
        self.twitch.linked = False

        self.assertTrue(self.controller._unavailable(self._key("twitch.announce")))

    def test_an_ad_key_is_faded_for_an_account_that_cannot_run_ads(self) -> None:
        """Said before the key is pressed, because Twitch's own answer on the
        press is a cooldown that never expires."""
        self.twitch.can_run_ads = lambda: False

        self.assertTrue(self.controller._unavailable(self._key("twitch.commercial")))

    def test_an_affiliate_keeps_the_ad_key(self) -> None:
        self.twitch.can_run_ads = lambda: True

        self.assertFalse(self.controller._unavailable(self._key("twitch.commercial")))

    def test_an_unknown_account_type_does_not_fade_it(self) -> None:
        """A lookup that never happened is not a refusal."""
        self.twitch.can_run_ads = lambda: None

        self.assertFalse(self.controller._unavailable(self._key("twitch.commercial")))

    def test_the_ad_check_never_touches_another_key(self) -> None:
        self.twitch.can_run_ads = lambda: False

        for action_id in ("twitch.announce", "twitch.clip", "twitch.raid"):
            with self.subTest(action=action_id):
                self.assertFalse(
                    self.controller._unavailable(self._key(action_id))
                )

    def test_a_client_that_cannot_answer_the_ad_question_is_survivable(
        self,
    ) -> None:
        def boom():
            raise RuntimeError("no")

        self.twitch.can_run_ads = boom

        self.assertFalse(self.controller._unavailable(self._key("twitch.commercial")))


class ScopeTests(unittest.TestCase):
    def test_the_new_actions_brought_their_scopes(self) -> None:
        for scope in (
            "channel:edit:commercial",
            "channel:manage:raids",
            "moderator:manage:announcements",
        ):
            with self.subTest(scope=scope):
                self.assertIn(scope, SCOPES)

    def test_no_scope_is_asked_for_twice(self) -> None:
        self.assertEqual(len(SCOPES), len(set(SCOPES)))

    def test_every_twitch_action_still_needs_an_account(self) -> None:
        for action_id, action in registry.REGISTRY.items():
            if action.category != twitch_actions.CAT_TWITCH:
                continue
            with self.subTest(action=action_id):
                self.assertTrue(action.needs_twitch)
                self.assertTrue(action.default_icon.startswith("mdi:"))


class FakeTwitch:
    def __init__(self, linked=True, snapshot=None, missing=(), fail=False) -> None:
        self.linked = linked
        self.account = "crucetaplay" if linked else ""
        self._snapshot = snapshot or {}
        self._missing = tuple(missing)
        self._fail = fail
        self.refreshed = 0

    def missing_scopes(self):
        return self._missing

    def refresh_channel(self):
        self.refreshed += 1
        if self._fail:
            raise TwitchError("network down")
        return dict(self._snapshot)


READY = {"title": "Building a Stream Deck app", "category": "Software and Game Development",
         "live": False}


class PreFlightAccountTests(unittest.TestCase):
    def test_a_good_account_passes(self) -> None:
        check = preflight.check_twitch_account(True, "me", ())

        self.assertEqual(check.state, OK)
        self.assertIn("me", check.detail)

    def test_no_account_is_unchecked_rather_than_failed(self) -> None:
        """Somebody who does not use Twitch has nothing wrong with their setup,
        and the board must not tell them they have."""
        check = preflight.check_twitch_account(False, "", ())

        self.assertEqual(check.state, UNCHECKED)

    def test_an_authorization_missing_a_scope_warns_and_names_it(self) -> None:
        """It reads exactly like an expired token when the key is pressed, so
        naming the gap is the difference between a fix and a mystery."""
        check = preflight.check_twitch_account(True, "me", ("clips:edit",))

        self.assertEqual(check.state, WARN)
        self.assertIn("clips:edit", check.detail)


class PreFlightFieldTests(unittest.TestCase):
    def test_a_missing_title_fails(self) -> None:
        self.assertEqual(preflight.check_twitch_title("", True).state, FAIL)
        self.assertEqual(preflight.check_twitch_title("   ", True).state, FAIL)

    def test_a_title_passes_and_says_what_it_cannot_judge(self) -> None:
        check = preflight.check_twitch_title("Tonight", True)

        self.assertEqual(check.state, OK)
        self.assertIn("Tonight", check.detail)
        self.assertIn("your call", check.detail)

    def test_a_missing_category_fails(self) -> None:
        self.assertEqual(preflight.check_twitch_category("", True).state, FAIL)

    def test_a_category_says_it_cannot_know_it_is_current(self) -> None:
        """Left on yesterday's game is the classic mistake and cannot be caught
        here, so the check must not imply that it was."""
        check = preflight.check_twitch_category("Doom", True)

        self.assertEqual(check.state, OK)
        self.assertIn("yesterday", check.detail)

    def test_already_live_warns(self) -> None:
        self.assertEqual(preflight.check_twitch_live(True, True).state, WARN)

    def test_not_live_passes(self) -> None:
        self.assertEqual(preflight.check_twitch_live(False, True).state, OK)

    def test_every_field_is_unchecked_without_an_account(self) -> None:
        for check in (
            preflight.check_twitch_title("", False),
            preflight.check_twitch_category("", False),
            preflight.check_twitch_live(False, False),
        ):
            with self.subTest(check=check.id):
                self.assertEqual(check.state, UNCHECKED)


class PreFlightRunTests(unittest.TestCase):
    """How the Twitch checks join a whole run."""

    @staticmethod
    def _checks(twitch, obs=None):
        obs = obs or SimpleNamespace(connected=False)
        return {c.id: c for c in preflight.run(obs, None, twitch)}

    def test_they_are_included(self) -> None:
        checks = self._checks(FakeTwitch(snapshot=READY))

        for check_id in ("twitch", "twitch_title", "twitch_category", "twitch_live"):
            with self.subTest(check=check_id):
                self.assertIn(check_id, checks)

    def test_they_run_even_with_obs_closed(self) -> None:
        """Twitch is a different service: "is my title set" does not stop being
        worth knowing because OBS happens not to be running."""
        twitch = FakeTwitch(snapshot=READY)

        checks = self._checks(twitch, SimpleNamespace(connected=False))

        self.assertEqual(checks["twitch_title"].state, OK)
        self.assertEqual(twitch.refreshed, 1)

    def test_a_run_without_twitch_at_all_still_reports_it(self) -> None:
        """Silence would be indistinguishable from a pass."""
        checks = self._checks(None)

        self.assertEqual(checks["twitch"].state, UNCHECKED)
        self.assertEqual(checks["twitch_title"].state, UNCHECKED)

    def test_an_unreachable_twitch_is_unchecked_not_failed(self) -> None:
        """Reporting an empty title because the network dropped would send
        somebody hunting for a problem they do not have."""
        checks = self._checks(FakeTwitch(fail=True))

        self.assertEqual(checks["twitch"].state, OK)
        for check_id in ("twitch_title", "twitch_category", "twitch_live"):
            with self.subTest(check=check_id):
                self.assertEqual(checks[check_id].state, UNCHECKED)

    def test_it_reads_the_channel_now_rather_than_from_the_cache(self) -> None:
        """`channel()` answers from up to twenty seconds ago, which is the
        right trade for a repainting key and the wrong one for this."""
        twitch = FakeTwitch(snapshot=READY)

        self._checks(twitch)

        self.assertEqual(twitch.refreshed, 1)

    def test_no_account_costs_no_request(self) -> None:
        twitch = FakeTwitch(linked=False)

        self._checks(twitch)

        self.assertEqual(twitch.refreshed, 0)

    def test_an_empty_title_reaches_the_report_as_a_failure(self) -> None:
        checks = self._checks(
            FakeTwitch(snapshot={"title": "", "category": "Doom", "live": False})
        )

        self.assertEqual(checks["twitch_title"].state, FAIL)

    def test_the_summary_still_names_what_was_skipped(self) -> None:
        report = preflight.Report(
            checks=list(preflight.run(SimpleNamespace(connected=False), None, None))
        )

        self.assertIn("not checked", report.summary())

    def test_every_twitch_check_carries_a_label_short_enough_for_a_key(
        self,
    ) -> None:
        for check in preflight.run(SimpleNamespace(connected=False), None,
                                   FakeTwitch(snapshot=READY)):
            if not check.id.startswith("twitch"):
                continue
            with self.subTest(check=check.id):
                self.assertLessEqual(len(check.label), 10)
                self.assertTrue(check.detail.endswith("."))
                self.assertTrue(check.icon.startswith("mdi:"))


if __name__ == "__main__":
    unittest.main()
