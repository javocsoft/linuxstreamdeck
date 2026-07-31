"""Device code authorization and the token lifecycle behind it.

The two things worth pinning here are both invisible when they break. Twitch
reports a still-pending authorization as an HTTP 400 carrying a message rather
than through a status code, so the poll loop distinguishes "not yet" from "no"
by reading text; and its refresh tokens are single use, so a renewal that is
adopted without being stored leaves an account that can never be renewed again.
"""

from __future__ import annotations

import unittest

from linuxstreamdeck.twitch import auth, http
from linuxstreamdeck.twitch.constants import SCOPES


class FakeTransport:
    """Stands in for `http.request_json`, recording what was asked."""

    def __init__(self, answers) -> None:
        self.answers = list(answers)
        self.calls: list[dict] = []

    def __call__(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.answers:
            raise AssertionError(f"Unexpected extra request to {url}")
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class TransportCase(unittest.TestCase):
    def use(self, *answers) -> FakeTransport:
        transport = FakeTransport(answers)
        original = http.request_json
        auth.http.request_json = transport
        self.addCleanup(setattr, auth.http, "request_json", original)
        return transport


DEVICE_ANSWER = {
    "device_code": "dev-code",
    "user_code": "ABCD-1234",
    "verification_uri": "https://www.twitch.tv/activate",
    "expires_in": 1800,
    "interval": 5,
}

TOKEN_ANSWER = {
    "access_token": "access-1",
    "refresh_token": "refresh-1",
    "expires_in": 14400,
    "scope": list(SCOPES),
}


class DeviceCodeTests(TransportCase):
    def test_it_asks_for_a_code_with_no_client_secret(self) -> None:
        transport = self.use(DEVICE_ANSWER)

        code = auth.request_device_code("client-abc", SCOPES)

        self.assertEqual(code.user_code, "ABCD-1234")
        self.assertEqual(code.verification_uri, "https://www.twitch.tv/activate")
        form = transport.calls[0]["form"]
        self.assertEqual(form["client_id"], "client-abc")
        # The whole reason this flow was chosen: nothing secret is sent.
        self.assertNotIn("client_secret", form)

    def test_the_scopes_parameter_is_spelled_the_way_twitch_wants_it(self) -> None:
        """This endpoint says `scopes`; every other OAuth endpoint says `scope`."""
        transport = self.use(DEVICE_ANSWER)

        auth.request_device_code("client-abc", ("a", "b"))

        self.assertEqual(transport.calls[0]["form"]["scopes"], "a b")

    def test_an_answer_without_a_code_is_refused(self) -> None:
        self.use({"user_code": "ABCD"})

        with self.assertRaises(auth.TwitchAuthError):
            auth.request_device_code("client-abc", SCOPES)

    def test_it_refuses_to_start_without_a_client_id(self) -> None:
        with self.assertRaises(auth.TwitchAuthError):
            auth.request_device_code("", SCOPES)


class PollingTests(TransportCase):
    @staticmethod
    def _code(**overrides) -> auth.DeviceCode:
        values = {
            "device_code": "dev-code",
            "user_code": "ABCD",
            "verification_uri": "https://twitch.tv/activate",
            "expires_in": 60,
            "interval": 1,
        }
        values.update(overrides)
        return auth.DeviceCode(**values)

    def test_a_pending_authorization_keeps_waiting(self) -> None:
        """The message is the only signal; a 400 alone must not end the flow."""
        self.use(
            http.TwitchHTTPError(400, "authorization_pending"),
            http.TwitchHTTPError(400, "authorization_pending"),
            TOKEN_ANSWER,
        )

        tokens = auth.poll_for_tokens(
            "client-abc", self._code(), sleep=lambda _s: None
        )

        self.assertIsNotNone(tokens)
        self.assertEqual(tokens.access, "access-1")

    def test_the_pending_code_is_recognised_however_it_is_spelled(self) -> None:
        """This is the bug that broke the whole flow in the first build.

        Twitch sends the RFC 8628 code `authorization_pending`; the constant
        was written as "authorization pending" with a space, so the very first
        poll read a perfectly normal "not yet" as a refusal and abandoned the
        flow while the user was still typing the code into Twitch.
        """
        for spelling in (
            "authorization_pending",
            "authorization pending",
            "authorization-pending",
            "Authorization_Pending",
            "  authorization_pending  ",
        ):
            with self.subTest(spelling=spelling):
                self.use(http.TwitchHTTPError(400, spelling), TOKEN_ANSWER)

                tokens = auth.poll_for_tokens(
                    "client-abc", self._code(), sleep=lambda _s: None
                )

                self.assertIsNotNone(tokens, f"{spelling!r} ended the flow")

    def test_slow_down_backs_off_instead_of_ending_the_flow(self) -> None:
        """The device flow's own way of saying "too often", sent as a 400."""
        waits: list[float] = []
        self.use(http.TwitchHTTPError(400, "slow_down"), TOKEN_ANSWER)

        tokens = auth.poll_for_tokens(
            "client-abc", self._code(), sleep=waits.append
        )

        self.assertIsNotNone(tokens)
        self.assertGreater(waits[-1], waits[0])

    def test_a_different_400_ends_the_flow(self) -> None:
        """Treating every 400 as pending would loop forever on a dead code."""
        self.use(http.TwitchHTTPError(400, "invalid device code"))

        with self.assertRaises(auth.TwitchAuthError):
            auth.poll_for_tokens(
                "client-abc", self._code(), sleep=lambda _s: None
            )

    def test_being_rate_limited_backs_off_instead_of_giving_up(self) -> None:
        waits: list[float] = []
        self.use(http.TwitchHTTPError(429, "too many requests"), TOKEN_ANSWER)

        tokens = auth.poll_for_tokens(
            "client-abc", self._code(), sleep=waits.append
        )

        self.assertIsNotNone(tokens)
        self.assertGreater(waits[-1], waits[0])

    def test_it_gives_up_when_the_code_expires(self) -> None:
        self.use()
        clock = iter([0.0, 100.0])

        tokens = auth.poll_for_tokens(
            "client-abc",
            self._code(expires_in=10),
            sleep=lambda _s: None,
            now=lambda: next(clock),
        )

        self.assertIsNone(tokens)

    def test_cancelling_stops_before_asking_anything(self) -> None:
        transport = self.use()

        tokens = auth.poll_for_tokens(
            "client-abc",
            self._code(),
            should_stop=lambda: True,
            sleep=lambda _s: None,
        )

        self.assertIsNone(tokens)
        self.assertEqual(transport.calls, [])

    def test_a_malformed_interval_cannot_become_a_hot_spin(self) -> None:
        waits: list[float] = []
        self.use(TOKEN_ANSWER)

        auth.poll_for_tokens(
            "client-abc", self._code(interval=0), sleep=waits.append
        )

        self.assertGreaterEqual(waits[0], auth.MIN_POLL_SECONDS)


class RefreshTests(TransportCase):
    def test_a_refusal_says_the_account_has_to_be_linked_again(self) -> None:
        self.use(http.TwitchHTTPError(400, "Invalid refresh token"))

        with self.assertRaises(auth.TwitchAuthError):
            auth.refresh_tokens("client-abc", "refresh-old")

    def test_it_refuses_without_a_refresh_token(self) -> None:
        with self.assertRaises(auth.TwitchAuthError):
            auth.refresh_tokens("client-abc", "")

    def test_a_renewal_returns_the_replacement_refresh_token(self) -> None:
        """Single use: the answer's refresh token is the only one left alive."""
        self.use({
            "access_token": "access-2",
            "refresh_token": "refresh-2",
            "expires_in": 14400,
        })

        tokens = auth.refresh_tokens("client-abc", "refresh-1")

        self.assertEqual(tokens.access, "access-2")
        self.assertEqual(tokens.refresh, "refresh-2")

    def test_a_server_error_is_not_reported_as_a_dead_authorization(self) -> None:
        """A 500 is Twitch having a bad day, not the user needing to relink."""
        self.use(http.TwitchHTTPError(500, "internal"))

        with self.assertRaises(http.TwitchHTTPError):
            auth.refresh_tokens("client-abc", "refresh-1")


class TokenTests(unittest.TestCase):
    def test_a_stored_pair_survives_a_round_trip(self) -> None:
        tokens = auth.Tokens(
            access="a",
            refresh="r",
            expires_at=123.0,
            login="crucetaplay",
            user_id="42",
            scopes=("clips:edit",),
        )

        restored = auth.Tokens.from_dict(tokens.to_dict())

        self.assertEqual(restored, tokens)

    def test_an_unusable_blob_reads_as_no_account(self) -> None:
        """The only repair is to link again, so it must not raise."""
        for value in ({}, None, "nonsense", {"access": "a"}, {"refresh": "r"}):
            with self.subTest(value=value):
                self.assertIsNone(auth.Tokens.from_dict(value))

    def test_a_corrupt_expiry_does_not_stop_the_account_loading(self) -> None:
        restored = auth.Tokens.from_dict(
            {"access": "a", "refresh": "r", "expires_at": "soon"}
        )

        self.assertIsNotNone(restored)
        self.assertEqual(restored.expires_at, 0.0)

    def test_a_token_near_its_expiry_is_renewed_early(self) -> None:
        tokens = auth.Tokens(access="a", refresh="r", expires_at=1000.0)

        self.assertTrue(tokens.expiring(now=999.0))
        self.assertFalse(tokens.expiring(now=1.0))

    def test_a_token_with_no_known_expiry_is_not_renewed_on_a_guess(self) -> None:
        tokens = auth.Tokens(access="a", refresh="r", expires_at=0.0)

        self.assertFalse(tokens.expiring(now=10**9))


class ScopeTests(unittest.TestCase):
    def test_it_names_what_an_older_authorization_cannot_do(self) -> None:
        tokens = auth.Tokens(access="a", refresh="r", scopes=("clips:edit",))

        missing = auth.missing_scopes(tokens, SCOPES)

        self.assertIn("channel:manage:broadcast", missing)
        self.assertNotIn("clips:edit", missing)

    def test_unknown_scopes_are_not_reported_as_a_gap(self) -> None:
        """An authorization whose scopes were never recorded proves nothing."""
        tokens = auth.Tokens(access="a", refresh="r", scopes=())

        self.assertEqual(auth.missing_scopes(tokens, SCOPES), ())


class RevokeTests(TransportCase):
    def test_a_failed_revoke_never_stops_someone_disconnecting(self) -> None:
        self.use(http.TwitchError("network down"), http.TwitchError("network down"))

        auth.revoke("client-abc", "access-1", "refresh-1")  # must not raise

    def test_both_tokens_are_offered(self) -> None:
        """Revoking an access token does not revoke the refresh token beside
        it, so leaving that one alive would keep a usable credential."""
        transport = self.use({}, {})

        auth.revoke("client-abc", "access-1", "refresh-1")

        sent = [call["form"]["token"] for call in transport.calls]
        self.assertEqual(sent, ["access-1", "refresh-1"])

    def test_a_refused_refresh_revoke_does_not_undo_the_first(self) -> None:
        """Twitch may well reject the second call; that is not a failure."""
        transport = self.use({}, http.TwitchHTTPError(400, "invalid token"))

        auth.revoke("client-abc", "access-1", "refresh-1")

        self.assertEqual(len(transport.calls), 2)

    def test_nothing_is_sent_when_there_is_nothing_to_revoke(self) -> None:
        transport = self.use()

        auth.revoke("client-abc", "")

        self.assertEqual(transport.calls, [])

    def test_a_missing_client_id_sends_nothing(self) -> None:
        transport = self.use()

        auth.revoke("", "access-1", "refresh-1")

        self.assertEqual(transport.calls, [])


class ErrorMessageTests(unittest.TestCase):
    def test_the_pending_message_is_read_out_of_a_refusal_body(self) -> None:
        """Losing this text would make the poll loop unable to tell pending."""
        body = b'{"status":400,"message":"authorization_pending"}'

        self.assertEqual(http._error_message(body), "authorization_pending")

    def test_an_unreadable_body_yields_no_message_rather_than_raising(self) -> None:
        self.assertEqual(http._error_message(b"<html>nope</html>"), "")


class ReadableErrorTests(unittest.TestCase):
    """No identifier may reach somebody trying to connect an account."""

    def test_known_codes_become_sentences(self) -> None:
        for code in ("access_denied", "expired_token", "invalid_client"):
            with self.subTest(code=code):
                text = auth.describe_error(code)

                self.assertNotIn("_", text)
                self.assertTrue(text.endswith("."), text)

    def test_an_unknown_code_never_reaches_the_user(self) -> None:
        """A code Twitch adds later must not be shown raw either."""
        text = auth.describe_error("some_future_error")

        self.assertEqual(text, auth.GENERIC_DEVICE_ERROR)

    def test_real_prose_is_left_alone(self) -> None:
        """Helix answers in words, and those words are worth showing."""
        message = "Missing scope: clips:edit"

        self.assertEqual(auth.describe_error(message), message)

    def test_an_empty_message_still_says_something(self) -> None:
        self.assertEqual(auth.describe_error(""), auth.GENERIC_DEVICE_ERROR)

    def test_a_refused_client_id_is_explained_rather_than_echoed(self) -> None:
        transport = FakeTransport([http.TwitchHTTPError(400, "invalid_client")])
        original = http.request_json
        auth.http.request_json = transport
        self.addCleanup(setattr, auth.http, "request_json", original)

        with self.assertRaises(auth.TwitchAuthError) as caught:
            auth.request_device_code("wrong-id", SCOPES)

        self.assertIn("Client ID", str(caught.exception))
        self.assertNotIn("_", str(caught.exception))


class VerificationUriTests(TransportCase):
    def test_the_prefilled_url_is_preferred_for_opening(self) -> None:
        """It saves retyping six characters just read off a screen, which is
        exactly where this flow goes wrong."""
        self.use(dict(DEVICE_ANSWER, verification_uri_complete="https://tw/x?c=1"))

        code = auth.request_device_code("client-abc", SCOPES)

        self.assertEqual(code.open_uri, "https://tw/x?c=1")
        # The plain address stays, for anyone finishing on a phone.
        self.assertEqual(code.verification_uri, "https://www.twitch.tv/activate")

    def test_it_falls_back_to_the_plain_address(self) -> None:
        self.use(DEVICE_ANSWER)

        code = auth.request_device_code("client-abc", SCOPES)

        self.assertEqual(code.open_uri, "https://www.twitch.tv/activate")

    def test_only_a_prefilled_url_is_still_usable(self) -> None:
        answer = dict(DEVICE_ANSWER)
        del answer["verification_uri"]
        answer["verification_uri_complete"] = "https://tw/x?c=1"
        self.use(answer)

        code = auth.request_device_code("client-abc", SCOPES)

        self.assertEqual(code.open_uri, "https://tw/x?c=1")


if __name__ == "__main__":
    unittest.main()
