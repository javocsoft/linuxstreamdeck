"""Calling an HTTP endpoint from a key, and showing what it answers.

The widest action in the catalogue, so most of what is pinned here is about
what it refuses to do: reach a scheme that is not HTTP, read an unbounded
response, raise over a value that merely could not be found, or perform a
request on the thread that is drawing the key.
"""

from __future__ import annotations

import io
import threading
import unittest
import unittest.mock
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

from linuxstreamdeck import basic_actions as _basic_actions  # noqa: F401
from linuxstreamdeck import web_actions
from linuxstreamdeck.core import webrequest
from linuxstreamdeck.core.actions import REGISTRY
from linuxstreamdeck.core.webrequest import WebRequestError


class _Response:
    """The context manager `urlopen` returns."""

    def __init__(self, body: bytes = b"", status: int = 200) -> None:
        self._body = body
        self.status = status

    def read(self, limit: int | None = None) -> bytes:
        return self._body if limit is None else self._body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> bool:
        return False


def opener_for(body: bytes = b"", status: int = 200, record: list | None = None):
    def opener(request, timeout=None):
        if record is not None:
            record.append((request, timeout))
        return _Response(body, status)

    return opener


class UrlTests(unittest.TestCase):
    def test_a_key_with_no_url_says_so(self) -> None:
        with self.assertRaises(WebRequestError) as caught:
            webrequest.check_url("   ")

        self.assertIn("no URL", str(caught.exception))

    def test_only_http_and_https_can_be_reached(self) -> None:
        """A typo or a paste must not be able to open a local file."""
        for address in (
            "file:///etc/passwd",
            "ftp://example.com/x",
            "gopher://example.com",
            "/etc/passwd",
        ):
            with self.subTest(address=address):
                with self.assertRaises(WebRequestError):
                    webrequest.check_url(address)

    def test_an_address_with_no_server_is_refused(self) -> None:
        with self.assertRaises(WebRequestError):
            webrequest.check_url("https://")

    def test_a_normal_address_passes_through(self) -> None:
        self.assertEqual(
            webrequest.check_url("  https://example.com/api  "),
            "https://example.com/api",
        )


class HeaderTests(unittest.TestCase):
    def test_headers_are_read_one_per_line(self) -> None:
        headers = webrequest.parse_headers(
            "Authorization: Bearer abc\nAccept: application/json"
        )

        self.assertEqual(
            headers, {"Authorization": "Bearer abc", "Accept": "application/json"}
        )

    def test_it_forgives_what_a_hand_typed_list_contains(self) -> None:
        """Blank lines, comments and a stray line without a colon must not
        stop the key from working."""
        headers = webrequest.parse_headers(
            "\n  # a note\nX-One: 1\nnonsense\n\n  X-Two:  2  \n: novalue\n"
        )

        self.assertEqual(headers, {"X-One": "1", "X-Two": "2"})


class RequestTests(unittest.TestCase):
    def test_it_returns_the_status_and_the_body(self) -> None:
        status, text = webrequest.request(
            "https://example.com", opener=opener_for(b'{"a": 1}', 201)
        )

        self.assertEqual((status, text), (201, '{"a": 1}'))

    def test_the_method_is_sent_and_normalised(self) -> None:
        sent: list = []
        webrequest.request(
            "https://example.com", "post", opener=opener_for(record=sent)
        )

        self.assertEqual(sent[0][0].get_method(), "POST")

    def test_an_unknown_method_falls_back_to_get(self) -> None:
        sent: list = []
        webrequest.request(
            "https://example.com", "LAUNCH", opener=opener_for(record=sent)
        )

        self.assertEqual(sent[0][0].get_method(), "GET")

    def test_a_body_is_only_sent_by_the_methods_that_carry_one(self) -> None:
        for method, expected in (
            ("POST", b'{"on": 1}'), ("PUT", b'{"on": 1}'),
            ("PATCH", b'{"on": 1}'), ("GET", None), ("DELETE", None),
        ):
            with self.subTest(method=method):
                sent: list = []
                webrequest.request(
                    "https://example.com", method, body='{"on": 1}',
                    opener=opener_for(record=sent),
                )

                self.assertEqual(sent[0][0].data, expected)

    def test_a_body_is_declared_as_json_unless_told_otherwise(self) -> None:
        sent: list = []
        webrequest.request(
            "https://example.com", "POST", body="x=1",
            opener=opener_for(record=sent),
        )
        self.assertEqual(sent[0][0].get_header("Content-type"), "application/json")

        sent.clear()
        webrequest.request(
            "https://example.com", "POST", {"Content-Type": "text/plain"},
            body="x=1", opener=opener_for(record=sent),
        )
        self.assertEqual(sent[0][0].get_header("Content-type"), "text/plain")

    def test_an_error_status_reports_what_the_server_said(self) -> None:
        """The number alone hides the body, which is usually the whole
        explanation."""

        def opener(_request, timeout=None):
            raise HTTPError(
                "https://example.com", 403, "Forbidden", {},
                io.BytesIO(b"token expired"),
            )

        with self.assertRaises(WebRequestError) as caught:
            webrequest.request("https://example.com", opener=opener)

        self.assertIn("403", str(caught.exception))
        self.assertIn("token expired", str(caught.exception))

    def test_an_unreachable_server_is_reported_in_words(self) -> None:
        def opener(_request, timeout=None):
            raise URLError("Name or service not known")

        with self.assertRaises(WebRequestError) as caught:
            webrequest.request("https://nowhere.invalid", opener=opener)

        self.assertIn("Could not reach", str(caught.exception))

    def test_a_response_larger_than_the_cap_is_refused(self) -> None:
        oversized = b"x" * (webrequest.MAX_RESPONSE_BYTES + 1)

        with self.assertRaises(WebRequestError) as caught:
            webrequest.request(
                "https://example.com", opener=opener_for(oversized)
            )

        self.assertIn("too large", str(caught.exception))

    def test_a_response_at_the_cap_is_still_read(self) -> None:
        exact = b"x" * webrequest.MAX_RESPONSE_BYTES

        _status, text = webrequest.request(
            "https://example.com", opener=opener_for(exact)
        )

        self.assertEqual(len(text), webrequest.MAX_RESPONSE_BYTES)


class ExtractTests(unittest.TestCase):
    def test_a_dotted_path_reaches_into_an_object(self) -> None:
        body = '{"state": {"temperature": 21.5}}'

        self.assertEqual(webrequest.extract(body, "state.temperature"), "21.5")

    def test_a_number_in_a_path_indexes_a_list(self) -> None:
        body = '{"data": [{"name": "first"}, {"name": "second"}]}'

        self.assertEqual(webrequest.extract(body, "data.1.name"), "second")

    def test_no_path_shows_the_whole_answer(self) -> None:
        """An endpoint that answers a bare number needs no path at all."""
        self.assertEqual(webrequest.extract("42", ""), "42")

    def test_a_path_that_does_not_resolve_shows_nothing(self) -> None:
        """A key showing nothing is a far better failure than a key that goes
        red on every tick of a live refresh."""
        body = '{"state": {"temperature": 21}}'

        for path in ("missing", "state.missing", "state.temperature.deeper",
                     "state.9", "data.0.name"):
            with self.subTest(path=path):
                self.assertEqual(webrequest.extract(body, path), "")

    def test_an_answer_that_is_not_json_never_raises(self) -> None:
        self.assertEqual(webrequest.extract("<html>nope</html>", "state"), "")

    def test_a_whole_number_is_not_shown_with_a_decimal_point(self) -> None:
        """JSON has one number type, so 12 arrives as 12.0 and "12.0" on a key
        reads like a measurement it is not."""
        self.assertEqual(webrequest.extract('{"n": 12}', "n"), "12")
        self.assertEqual(webrequest.extract('{"n": 12.50}', "n"), "12.5")

    def test_a_boolean_is_spelled_the_way_the_api_spells_it(self) -> None:
        self.assertEqual(webrequest.extract('{"on": true}', "on"), "true")
        self.assertEqual(webrequest.extract('{"on": false}', "on"), "false")

    def test_null_and_whole_objects_show_nothing(self) -> None:
        """Neither can be read on a key, and truncated JSON is only noise."""
        self.assertEqual(webrequest.extract('{"a": null}', "a"), "")
        self.assertEqual(webrequest.extract('{"a": {"b": 1}}', "a"), "")
        self.assertEqual(webrequest.extract('{"a": [1, 2]}', "a"), "")

    def test_a_long_value_is_cut_to_what_a_key_can_show(self) -> None:
        body = '{"name": "an extremely long value indeed"}'

        self.assertEqual(
            len(webrequest.extract(body, "name")), webrequest.MAX_VALUE_CHARS
        )

    def test_a_value_is_flattened_to_one_line(self) -> None:
        self.assertEqual(webrequest.extract('{"a": "one\\ntwo"}', "a"), "one two")


class CachedValueTests(unittest.TestCase):
    """`feedback()` runs on the render worker, so it may never wait on a
    request. These pin that it does not, and that a burst of repaints does not
    become a burst of requests."""

    def setUp(self) -> None:
        webrequest.forget_values()
        self.submitted: list = []
        patcher = unittest.mock.patch.object(
            webrequest, "_submit",
            lambda signature, params: self.submitted.append(signature),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(webrequest.forget_values)

    def _params(self, **extra):
        return {"url": "https://example.com", "value_path": "state", **extra}

    def test_the_first_look_shows_nothing_and_asks(self) -> None:
        value = webrequest.cached_value(self._params(), 15.0, now=1000.0)

        self.assertEqual(value, "")
        self.assertEqual(len(self.submitted), 1)

    def test_it_never_performs_a_request_itself(self) -> None:
        """Pinned by patching the module's own opener: reaching the network
        from here would hold the render worker for its latency."""
        def explode(*_a, **_k):
            raise AssertionError("cached_value performed a request")

        with unittest.mock.patch.object(webrequest, "request", explode):
            webrequest.cached_value(self._params(), 15.0, now=1000.0)

    def test_a_burst_of_repaints_is_still_one_request(self) -> None:
        for _ in range(20):
            webrequest.cached_value(self._params(), 15.0, now=1000.0)

        self.assertEqual(len(self.submitted), 1)

    def test_it_does_not_ask_again_until_the_interval_has_passed(self) -> None:
        webrequest.remember(self._params(), '{"state": "on"}', now=1000.0)

        webrequest.cached_value(self._params(), 15.0, now=1010.0)
        self.assertEqual(self.submitted, [])

        webrequest.cached_value(self._params(), 15.0, now=1016.0)
        self.assertEqual(len(self.submitted), 1)

    def test_a_stored_value_is_shown(self) -> None:
        webrequest.remember(self._params(), '{"state": "on"}', now=1000.0)

        self.assertEqual(
            webrequest.cached_value(self._params(), 15.0, now=1001.0), "on"
        )

    def test_a_brief_outage_keeps_the_last_value(self) -> None:
        webrequest.remember(self._params(), '{"state": "on"}', now=1000.0)

        self.assertEqual(
            webrequest.cached_value(self._params(), 15.0, now=1050.0), "on"
        )

    def test_a_sustained_outage_blanks_the_key(self) -> None:
        """A number that stopped being true is worse than no number."""
        webrequest.remember(self._params(), '{"state": "on"}', now=1000.0)

        self.assertEqual(
            webrequest.cached_value(
                self._params(), 15.0,
                now=1000.0 + webrequest.VALUE_STALE_SECONDS + 1,
            ),
            "",
        )

    def test_two_keys_on_different_endpoints_do_not_share_a_value(self) -> None:
        webrequest.remember(self._params(), '{"state": "on"}', now=1000.0)

        other = self._params(url="https://elsewhere.com")

        self.assertEqual(webrequest.cached_value(other, 15.0, now=1001.0), "")

    def test_the_path_is_part_of_what_identifies_a_value(self) -> None:
        """Two keys on one endpoint showing different fields are two values."""
        body = '{"a": 1, "b": 2}'
        webrequest.remember(self._params(value_path="a"), body, now=1000.0)
        webrequest.remember(self._params(value_path="b"), body, now=1000.0)

        self.assertEqual(
            webrequest.cached_value(self._params(value_path="a"), 15.0, now=1001.0),
            "1",
        )
        self.assertEqual(
            webrequest.cached_value(self._params(value_path="b"), 15.0, now=1001.0),
            "2",
        )


class FetchTests(unittest.TestCase):
    def setUp(self) -> None:
        webrequest.forget_values()
        self.addCleanup(webrequest.forget_values)

    def test_a_failed_fetch_keeps_the_previous_value(self) -> None:
        """A live value is decoration over a key that must keep working; a
        flaky connection must not blank it on the first hiccup."""
        params = {"url": "https://example.com", "value_path": "state"}
        webrequest.remember(params, '{"state": "on"}', now=1000.0)
        signature = webrequest._signature(params)
        webrequest._pending.add(signature)

        with unittest.mock.patch.object(
            webrequest, "request", side_effect=WebRequestError("down")
        ):
            webrequest._fetch(signature, dict(params))

        self.assertEqual(webrequest._values[signature][1], "on")
        self.assertNotIn(signature, webrequest._pending)

    def test_a_fetch_always_releases_its_pending_mark(self) -> None:
        """Otherwise one failure stops that key ever asking again."""
        params = {"url": "https://example.com"}
        signature = webrequest._signature(params)
        webrequest._pending.add(signature)

        with unittest.mock.patch.object(
            webrequest, "request", side_effect=RuntimeError("unexpected")
        ):
            webrequest._fetch(signature, dict(params))

        self.assertNotIn(signature, webrequest._pending)

    def test_nothing_is_queued_after_shutdown(self) -> None:
        """And the mark is released. Leaving it behind would mean that key
        never asks again if the application somehow carried on."""
        signature = ("x",)
        try:
            webrequest.shutdown()
            webrequest._pending.add(signature)

            webrequest._submit(signature, {"url": "https://example.com"})

            self.assertEqual(webrequest._pending, set())
        finally:
            webrequest._stopped = False
            webrequest._pending.discard(signature)

    def test_the_shared_worker_reports_whether_it_took_the_job(self) -> None:
        """Key Light state rides the same executor, and it has its own marks
        to release when the job is refused."""
        try:
            self.assertTrue(webrequest.background(lambda: None))
            webrequest.shutdown()
            self.assertFalse(webrequest.background(lambda: None))
        finally:
            webrequest._stopped = False


class ActionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.action = REGISTRY["web.request"]
        self.messages: list = []
        self.ctx = SimpleNamespace(
            bus=SimpleNamespace(
                emit=lambda topic, **d: self.messages.append(d.get("text", ""))
            ),
            obs=SimpleNamespace(connected=False),
            key=(0, 0, 0),
        )
        webrequest.forget_values()
        self.addCleanup(webrequest.forget_values)

    def test_pressing_it_calls_the_endpoint_and_reports(self) -> None:
        with unittest.mock.patch.object(
            webrequest, "request", return_value=(204, "")
        ):
            self.action.execute(self.ctx, {"url": "https://example.com",
                                           "method": "POST"})

        self.assertIn("POST", self.messages[-1])
        self.assertIn("204", self.messages[-1])

    def test_a_failure_raises_so_the_key_marks_itself(self) -> None:
        """Catching it and emitting a status would leave the key looking as if
        it had worked."""
        with unittest.mock.patch.object(
            webrequest, "request", side_effect=WebRequestError("nope")
        ):
            with self.assertRaises(WebRequestError):
                self.action.execute(self.ctx, {"url": "https://example.com"})

    def test_a_press_updates_what_the_key_shows(self) -> None:
        params = {"url": "https://example.com", "display": "yes",
                  "value_path": "state"}
        with unittest.mock.patch.object(
            webrequest, "request", return_value=(200, '{"state": "warm"}')
        ):
            self.action.execute(self.ctx, params)

        self.assertIn("warm", self.messages[-1])
        self.assertEqual(self.action.feedback(self.ctx, params)["display"], "warm")

    def test_a_key_that_shows_nothing_asks_for_nothing(self) -> None:
        called: list = []
        with unittest.mock.patch.object(
            webrequest, "cached_value", lambda *a, **k: called.append(1) or ""
        ):
            state = self.action.feedback(self.ctx, {"url": "https://example.com"})

        self.assertEqual(state, {})
        self.assertEqual(called, [])

    def test_the_body_field_only_applies_to_methods_that_send_one(self) -> None:
        body = next(p for p in self.action.params if p.name == "body")

        self.assertEqual(body.depends_on, "method")
        self.assertEqual(list(body.depends_values), list(webrequest.BODY_METHODS))

    def test_the_value_fields_only_apply_when_it_shows_a_value(self) -> None:
        for name in ("value_path", "refresh"):
            with self.subTest(name=name):
                param = next(p for p in self.action.params if p.name == name)

                self.assertEqual(param.depends_on, "display")
                self.assertEqual(list(param.depends_values), ["yes"])

    def test_it_shows_running_feedback(self) -> None:
        """A request goes over the network and can take seconds; without this
        a single-action key looks like nothing happened."""
        self.assertTrue(self.action.running_feedback)

    def test_it_does_not_claim_to_need_obs(self) -> None:
        self.assertFalse(self.action.needs_obs)


class RefreshChoiceTests(unittest.TestCase):
    def test_every_choice_is_a_positive_number_of_seconds(self) -> None:
        for choice in web_actions.REFRESH_CHOICES:
            with self.subTest(choice=choice):
                self.assertGreater(web_actions.refresh_seconds(choice), 0)

    def test_every_choice_is_labelled(self) -> None:
        self.assertEqual(
            sorted(web_actions.REFRESH_LABELS), sorted(web_actions.REFRESH_CHOICES)
        )

    def test_rubbish_falls_back_to_the_default(self) -> None:
        for value in ("", None, "soon", "0", "-5"):
            with self.subTest(value=value):
                self.assertEqual(
                    web_actions.refresh_seconds(value),
                    float(web_actions.DEFAULT_REFRESH),
                )

    def test_no_choice_ticks_faster_than_the_live_loop(self) -> None:
        """The loop it rides samples at LIVE_TICK_SECONDS, so a key asking for
        anything quicker would silently get the slower rate."""
        from linuxstreamdeck.core.controller import LIVE_TICK_SECONDS

        for choice in web_actions.REFRESH_CHOICES:
            with self.subTest(choice=choice):
                self.assertGreaterEqual(
                    web_actions.refresh_seconds(choice), LIVE_TICK_SECONDS
                )


class LiveRefreshTests(unittest.TestCase):
    """It has to be wired into the loop that repaints keys on a clock, or the
    value is only ever as fresh as the last unrelated event."""

    def _interval(self, params):
        from linuxstreamdeck.core.config import KIND_SINGLE, KeyConfig
        from linuxstreamdeck.core.controller import DeckController

        controller = SimpleNamespace(
            obs=SimpleNamespace(connected=False),
            _twitch_linked=lambda: False,
        )
        return DeckController._live_interval(
            controller,
            KeyConfig(kind=KIND_SINGLE, action="web.request", params=params),
        )

    def test_a_key_showing_a_value_repaints_on_its_chosen_interval(self) -> None:
        self.assertEqual(
            self._interval({"display": "yes", "refresh": "5"}), 5.0
        )

    def test_a_key_showing_nothing_is_never_repainted(self) -> None:
        """Repainting is what makes it ask again, so a key with no display
        must cost nothing at all."""
        self.assertEqual(self._interval({"display": "no"}), 0.0)
        self.assertEqual(self._interval({}), 0.0)


class SafetyTests(unittest.TestCase):
    def test_it_is_never_offered_to_an_ai_provider(self) -> None:
        """A proposal is untrusted text, and somebody reviewing one cannot be
        expected to audit a URL."""
        from linuxstreamdeck.ai.service import BLOCKED_ACTIONS

        self.assertIn("web.request", BLOCKED_ACTIONS)

    def test_the_shutdown_helper_can_be_called_twice(self) -> None:
        try:
            webrequest.shutdown()
            webrequest.shutdown()
        finally:
            webrequest._stopped = False

    def test_the_cache_is_guarded_by_a_lock(self) -> None:
        """It is read from render workers and written from fetch workers."""
        self.assertIsInstance(webrequest._lock, type(threading.Lock()))
