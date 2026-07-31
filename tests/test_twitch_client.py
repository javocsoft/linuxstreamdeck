"""The Twitch connection: its cache, its token renewal and how it fails.

The rule this file exists to defend is that `channel()` performs no network
request. It is called from `feedback()` while a key image is being composed, on
a render worker, so a Helix round trip there would hold that worker for the
latency of the internet on every repaint.

The second rule is that a refresh token is spent exactly once. Twitch
invalidates it on use, so two workers renewing at the same time, or a renewal
adopted without being stored, both end with an account that looks linked and
can never be renewed again.
"""

from __future__ import annotations

import threading
import time
import unittest

from linuxstreamdeck.core.events import EventBus
from linuxstreamdeck.twitch import auth, client as client_module
from linuxstreamdeck.twitch.client import TwitchClient
from linuxstreamdeck.twitch.http import TwitchError, TwitchHTTPError


class FakeStore:
    """A keyring that keeps its contents in a dict and counts writes."""

    def __init__(self, initial=None) -> None:
        self.value = dict(initial or {})
        self.saves: list[dict] = []
        self.cleared = 0

    def load(self) -> dict:
        return dict(self.value)

    def save(self, tokens: dict) -> bool:
        self.value = dict(tokens)
        self.saves.append(dict(tokens))
        return True

    def clear(self) -> bool:
        self.value = {}
        self.cleared += 1
        return True


class Recorder:
    """Stands in for the client's worker pool so scheduling is observable."""

    def __init__(self) -> None:
        self.jobs: list = []

    def submit(self, fn) -> None:
        self.jobs.append(fn)

    def shutdown(self, wait=False) -> None:
        pass

    def run_all(self) -> int:
        jobs, self.jobs = self.jobs, []
        for job in jobs:
            job()
        return len(jobs)


LIVE_STREAM = {
    "data": [{
        "viewer_count": 128,
        "title": "Live title",
        "game_name": "Just Chatting",
        "started_at": "2026-07-31T10:00:00Z",
    }]
}
CHANNEL_INFO = {
    "data": [{"title": "Channel title", "game_name": "Just Chatting"}]
}
FOLLOWERS = {"total": 4210}
# What /oauth2/validate answers for a good stored token. Loading an account
# always confirms it, because only Twitch can say whether it is still valid and
# the answer is also the cheapest source of the user id.
VALIDATE = {
    "login": "crucetaplay",
    "user_id": "42",
    "scopes": ["channel:manage:broadcast", "clips:edit",
               "moderator:read:followers"],
    "expires_in": 14400,
}


class ClientCase(unittest.TestCase):
    def build(self, *, tokens=None, answers=()) -> TwitchClient:
        self.bus = EventBus()
        self.events: list[tuple] = []
        self.bus.subscribe("twitch.state", lambda t, d: self.events.append((t, d)))
        self.bus.subscribe("status", lambda t, d: self.events.append((t, d)))
        stored = tokens.to_dict() if tokens is not None else {}
        self.store = FakeStore(stored)
        client = TwitchClient(self.bus, store=self.store, client_id="client-abc")
        client._pool.shutdown(wait=False)
        self.pool = Recorder()
        client._pool = self.pool
        self.requests: list[dict] = []
        self.answers = list(answers)
        original = client_module.request_json
        client_module.request_json = self._transport
        self.addCleanup(setattr, client_module, "request_json", original)
        auth_original = auth.http.request_json
        auth.http.request_json = self._transport
        self.addCleanup(setattr, auth.http, "request_json", auth_original)
        self.addCleanup(client.stop)
        return client

    def load(self, client, *answers) -> None:
        """Bring the client up as if it had started with a stored account."""
        self.answers = [VALIDATE, *answers]
        client._load_account()
        self.pool.jobs.clear()
        self.requests.clear()

    def _transport(self, method, url, **kwargs):
        self.requests.append({"method": method, "url": url, **kwargs})
        if not self.answers:
            raise AssertionError(f"Unexpected request to {url}")
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    @staticmethod
    def tokens(**overrides) -> auth.Tokens:
        values = {
            "access": "access-1",
            "refresh": "refresh-1",
            "expires_at": 10**10,
            "login": "crucetaplay",
            "user_id": "42",
            "scopes": ("channel:manage:broadcast",),
        }
        values.update(overrides)
        return auth.Tokens(**values)


class SnapshotTests(ClientCase):
    def test_reading_the_channel_never_performs_a_request(self) -> None:
        """The rule the whole module exists for: feedback must not block."""
        client = self.build(tokens=self.tokens())
        self.load(client)

        snapshot = client.channel()

        self.assertEqual(snapshot, {})
        self.assertEqual(self.requests, [])
        # It scheduled the work instead of doing it.
        self.assertEqual(len(self.pool.jobs), 1)

    def test_the_scheduled_refresh_fills_the_snapshot(self) -> None:
        client = self.build(tokens=self.tokens())
        self.load(client, LIVE_STREAM, CHANNEL_INFO, FOLLOWERS)

        client.channel()
        self.pool.run_all()
        snapshot = client.channel()

        self.assertTrue(snapshot["live"])
        self.assertEqual(snapshot["viewers"], 128)
        self.assertEqual(snapshot["followers"], 4210)
        self.assertEqual(snapshot["title"], "Channel title")

    def test_a_fresh_snapshot_schedules_nothing(self) -> None:
        """Six keys on one page must not become six refreshes."""
        client = self.build(tokens=self.tokens())
        self.load(client, LIVE_STREAM, CHANNEL_INFO, FOLLOWERS)
        client.channel()
        self.pool.run_all()

        for _ in range(6):
            client.channel()

        self.assertEqual(self.pool.jobs, [])

    def test_a_refresh_already_in_flight_is_not_started_twice(self) -> None:
        client = self.build(tokens=self.tokens())
        self.load(client)

        client.channel()
        client.channel()
        client.channel()

        self.assertEqual(len(self.pool.jobs), 1)

    def test_an_unlinked_client_schedules_nothing(self) -> None:
        client = self.build()
        self.load(client)

        self.assertEqual(client.channel(), {})
        self.assertEqual(self.pool.jobs, [])

    def test_a_brief_failure_keeps_showing_the_last_value(self) -> None:
        """One dropped request must not make a steady key flicker to nothing."""
        client = self.build(tokens=self.tokens())
        self.load(client, LIVE_STREAM, CHANNEL_INFO, FOLLOWERS)
        client.channel()
        self.pool.run_all()
        client._channel_at -= client_module.CHANNEL_TTL + 1

        self.answers = [TwitchError("network down")]
        client.channel()
        self.pool.run_all()

        self.assertEqual(client.channel()["viewers"], 128)

    def test_a_lasting_outage_blanks_the_key_instead_of_lying(self) -> None:
        client = self.build(tokens=self.tokens())
        self.load(client, LIVE_STREAM, CHANNEL_INFO, FOLLOWERS)
        client.channel()
        self.pool.run_all()

        client._channel_at -= client_module.CHANNEL_STALE + 1

        self.assertEqual(client.channel(), {})

    def test_an_offline_channel_still_reports_its_title_and_category(self) -> None:
        """Setting them before going live is the main reason to look."""
        client = self.build(tokens=self.tokens())
        self.load(client, {"data": []}, CHANNEL_INFO, FOLLOWERS)

        client.channel()
        self.pool.run_all()
        snapshot = client.channel()

        self.assertFalse(snapshot["live"])
        self.assertIsNone(snapshot["viewers"])
        self.assertEqual(snapshot["category"], "Just Chatting")

    def test_a_missing_follower_scope_does_not_lose_the_viewer_count(self) -> None:
        client = self.build(tokens=self.tokens())
        self.load(client, LIVE_STREAM, CHANNEL_INFO,
                  TwitchHTTPError(401, "Missing scope: moderator:read:followers"))

        client.channel()
        self.pool.run_all()
        snapshot = client.channel()

        self.assertEqual(snapshot["viewers"], 128)
        self.assertIsNone(snapshot["followers"])

    def test_uptime_is_none_while_the_channel_is_offline(self) -> None:
        self.assertIsNone(client_module.uptime_seconds({"live": False}))
        self.assertIsNone(client_module.uptime_seconds({}))

    def test_uptime_counts_from_the_reported_start(self) -> None:
        snapshot = {"live": True, "started_at": 1000.0}

        self.assertEqual(client_module.uptime_seconds(snapshot, now=1090.0), 90.0)

    def test_a_clock_skewed_start_never_reports_negative_uptime(self) -> None:
        snapshot = {"live": True, "started_at": 2000.0}

        self.assertEqual(client_module.uptime_seconds(snapshot, now=1000.0), 0.0)


class RenewalTests(ClientCase):
    def test_a_renewal_is_stored_before_it_is_adopted(self) -> None:
        """A refresh token is spent on use; losing its replacement is fatal."""
        client = self.build(tokens=self.tokens(expires_at=1.0))
        client._tokens = self.tokens(expires_at=1.0)
        self.answers = [{
            "access_token": "access-2",
            "refresh_token": "refresh-2",
            "expires_in": 14400,
        }]

        client._headers()

        self.assertEqual(self.store.value["refresh"], "refresh-2")
        self.assertEqual(client._tokens.access, "access-2")

    def test_a_renewal_keeps_the_identity_the_answer_does_not_carry(self) -> None:
        client = self.build(tokens=self.tokens(expires_at=1.0))
        client._tokens = self.tokens(expires_at=1.0)
        self.answers = [{
            "access_token": "access-2",
            "refresh_token": "refresh-2",
            "expires_in": 14400,
        }]

        client._headers()

        self.assertEqual(client._tokens.user_id, "42")
        self.assertEqual(client._tokens.login, "crucetaplay")

    def test_two_workers_renewing_at_once_spend_one_refresh_token(self) -> None:
        """The renewal has to be held open long enough for the race to be real.

        Twitch invalidates a refresh token on use, so two workers that both
        reach the token endpoint spend the same one twice and the second answer
        is a refusal that unlinks a perfectly good account. An instant fake
        transport would serialize by luck and prove nothing, so this one sleeps
        inside the request and counts how many callers got in.
        """
        client = self.build(tokens=self.tokens(expires_at=1.0))
        client._tokens = self.tokens(expires_at=1.0)
        started = threading.Barrier(2, timeout=5)
        renewals: list[str] = []
        answer = {
            "access_token": "access-2",
            "refresh_token": "refresh-2",
            "expires_in": 14400,
        }

        def slow_transport(method, url, **kwargs):
            renewals.append(kwargs.get("form", {}).get("refresh_token", ""))
            time.sleep(0.05)
            return answer

        client_module.request_json = slow_transport
        auth.http.request_json = slow_transport

        def worker():
            started.wait()
            client._headers()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        self.assertEqual(renewals, ["refresh-1"])

    def test_a_dead_authorization_drops_the_account(self) -> None:
        client = self.build(tokens=self.tokens(expires_at=1.0))
        client._tokens = self.tokens(expires_at=1.0)
        self.answers = [TwitchHTTPError(400, "Invalid refresh token")]

        with self.assertRaises(auth.TwitchAuthError):
            client._headers()

        self.assertFalse(client.linked)
        self.assertEqual(self.store.cleared, 1)

    def test_a_dropped_account_is_announced_on_the_bus(self) -> None:
        """The deck fades those keys, so the change has to be published."""
        client = self.build(tokens=self.tokens(expires_at=1.0))
        client._tokens = self.tokens(expires_at=1.0)
        self.answers = [TwitchHTTPError(400, "Invalid refresh token")]

        with self.assertRaises(auth.TwitchAuthError):
            client._headers()

        topics = [topic for topic, _ in self.events]
        self.assertIn("twitch.state", topics)

    def test_a_rejected_request_is_retried_once_with_a_new_token(self) -> None:
        """An expiry this client had not noticed should be invisible."""
        client = self.build(tokens=self.tokens())
        client._tokens = self.tokens()
        self.answers = [
            TwitchHTTPError(401, "invalid oauth token"),
            {"access_token": "access-2", "refresh_token": "refresh-2",
             "expires_in": 14400},
            {"data": [{"title": "ok"}]},
        ]

        answer = client._get("/channels", {"broadcaster_id": "42"})

        self.assertEqual(answer["data"][0]["title"], "ok")

    def test_a_missing_scope_never_spends_a_refresh_token(self) -> None:
        """It is also a 401, and no renewal can grant a permission never asked
        for; retrying it would spend a single-use token on every refresh."""
        client = self.build(tokens=self.tokens())
        client._tokens = self.tokens()
        self.answers = [TwitchHTTPError(401, "Missing scope: clips:edit")]

        with self.assertRaises(TwitchHTTPError):
            client._get("/clips")

        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.store.saves, [])

    def test_requests_without_an_account_refuse_rather_than_crash(self) -> None:
        client = self.build()
        client._load_account()

        with self.assertRaises(TwitchError):
            client._headers()


class ActionRequestTests(ClientCase):
    def test_setting_the_title_sends_it_and_invalidates_the_snapshot(self) -> None:
        client = self.build(tokens=self.tokens())
        client._tokens = self.tokens()
        client._channel_at = 10**9
        self.answers = [{}]

        client.set_title("  New title  ")

        self.assertEqual(self.requests[0]["method"], "PATCH")
        self.assertEqual(self.requests[0]["body"], {"title": "New title"})
        self.assertEqual(client._channel_at, 0.0)

    def test_an_empty_title_is_refused_before_reaching_twitch(self) -> None:
        client = self.build(tokens=self.tokens())
        client._tokens = self.tokens()

        with self.assertRaises(TwitchError):
            client.set_title("   ")

        self.assertEqual(self.requests, [])

    def test_an_exact_category_name_beats_the_search_ranking(self) -> None:
        """Searching "Doom" also matches "Doom Eternal"; the exact one wins."""
        client = self.build(tokens=self.tokens())
        client._tokens = self.tokens()
        self.answers = [
            {"data": [
                {"id": "99", "name": "Doom Eternal"},
                {"id": "7", "name": "Doom"},
            ]},
            {},
        ]

        matched = client.set_category("doom")

        self.assertEqual(matched, "Doom")
        self.assertEqual(self.requests[1]["body"], {"game_id": "7"})

    def test_an_approximate_name_takes_the_best_match(self) -> None:
        client = self.build(tokens=self.tokens())
        client._tokens = self.tokens()
        self.answers = [
            {"data": [{"id": "32982", "name": "Grand Theft Auto V"}]},
            {},
        ]

        matched = client.set_category("grand theft")

        self.assertEqual(matched, "Grand Theft Auto V")

    def test_a_category_nobody_has_is_reported_rather_than_guessed(self) -> None:
        client = self.build(tokens=self.tokens())
        client._tokens = self.tokens()
        self.answers = [{"data": []}]

        with self.assertRaises(TwitchError):
            client.set_category("not a real game")

    def test_a_resolved_category_is_not_searched_again(self) -> None:
        client = self.build(tokens=self.tokens())
        client._tokens = self.tokens()
        self.answers = [{"data": [{"id": "7", "name": "Doom"}]}, {}, {}]

        client.set_category("Doom")
        client.set_category("Doom")

        searches = [r for r in self.requests if "search/categories" in r["url"]]
        self.assertEqual(len(searches), 1)

    def test_creating_a_clip_answers_the_edit_url(self) -> None:
        client = self.build(tokens=self.tokens())
        client._tokens = self.tokens()
        self.answers = [{"data": [{"id": "c1", "edit_url": "https://clips/edit"}]}]

        self.assertEqual(client.create_clip(), "https://clips/edit")

    def test_a_marker_note_is_bounded(self) -> None:
        """Twitch refuses a long description; losing the marker over a note
        would be a poor trade."""
        client = self.build(tokens=self.tokens())
        client._tokens = self.tokens()
        self.answers = [{}]

        client.create_marker("x" * 500)

        self.assertEqual(len(self.requests[0]["body"]["description"]), 140)

    def test_a_marker_without_a_note_sends_no_description(self) -> None:
        client = self.build(tokens=self.tokens())
        client._tokens = self.tokens()
        self.answers = [{}]

        client.create_marker("   ")

        self.assertNotIn("description", self.requests[0]["body"])


class AccountTests(ClientCase):
    def test_an_unreachable_twitch_does_not_unlink_a_good_account(self) -> None:
        """Unreachable is not revoked; the account has to survive an outage."""
        client = self.build(tokens=self.tokens())
        self.answers = [TwitchError("network down")]

        client._load_account()

        self.assertTrue(client.linked)
        self.assertEqual(self.store.cleared, 0)

    def test_unlinking_forgets_the_tokens_and_asks_twitch_to_as_well(self) -> None:
        client = self.build(tokens=self.tokens())
        client._tokens = self.tokens()

        client.unlink()

        self.assertFalse(client.linked)
        self.assertEqual(self.store.cleared, 1)
        self.assertEqual(len(self.pool.jobs), 1)

    def test_unlinking_revokes_both_tokens(self) -> None:
        """The refresh token survives an access-token revoke on its own."""
        client = self.build(tokens=self.tokens())
        client._tokens = self.tokens()
        client.unlink()
        self.answers = [{}, {}]

        self.pool.run_all()

        sent = [call["form"]["token"] for call in self.requests]
        self.assertEqual(sent, ["access-1", "refresh-1"])

    def test_the_account_is_gone_locally_even_if_twitch_cannot_be_told(
        self,
    ) -> None:
        """Disconnecting must never depend on the network being there."""
        client = self.build(tokens=self.tokens())
        client._tokens = self.tokens()

        client.unlink()
        self.answers = [TwitchError("network down"), TwitchError("network down")]
        self.pool.run_all()

        self.assertFalse(client.linked)
        self.assertEqual(self.store.value, {})

    def test_a_client_id_override_replaces_the_default(self) -> None:
        client = self.build()

        client.set_client_id("mine-123")

        self.assertEqual(client.client_id, "mine-123")

    def test_the_missing_scopes_of_an_older_authorization_are_reported(self) -> None:
        client = self.build()
        client._tokens = self.tokens(scopes=("clips:edit",))

        self.assertIn("channel:manage:broadcast", client.missing_scopes())


class TimestampTests(unittest.TestCase):
    def test_a_twitch_timestamp_becomes_an_epoch(self) -> None:
        moment = client_module._as_epoch("2026-07-31T10:00:00Z")

        self.assertIsNotNone(moment)
        self.assertGreater(moment, 0)

    def test_an_unparseable_timestamp_answers_none(self) -> None:
        for value in ("", None, "not a date", 12345):
            with self.subTest(value=value):
                self.assertIsNone(client_module._as_epoch(value))


if __name__ == "__main__":
    unittest.main()
