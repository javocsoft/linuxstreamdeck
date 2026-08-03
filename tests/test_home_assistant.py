"""Home Assistant on a key.

`web.request` could already call these endpoints, so what is pinned here is
mostly what it could *not* do and what would go wrong doing it by hand: the
entity dropdown, an address people paste in three different shapes, a refusal
turned into a sentence, and the rule that drawing a key never waits on a house.
"""

from __future__ import annotations

import json
import unittest
import unittest.mock
from types import SimpleNamespace

from linuxstreamdeck import ha_actions
from linuxstreamdeck.core import homeassistant as ha
from linuxstreamdeck.core import webrequest
from linuxstreamdeck.core.actions import REGISTRY
from linuxstreamdeck.core.config import Config
from linuxstreamdeck.core.homeassistant import (
    HomeAssistantClient, HomeAssistantError, NotConfigured,
)

# Distinctive on purpose. The first version was "tok", which is a substring of
# the word "token", so the leak test below passed or failed on the wording of
# a message rather than on whether the secret had leaked.
TOKEN = "llat-9f2a-secret"

STATES = [
    {"entity_id": "light.kitchen", "state": "on",
     "attributes": {"friendly_name": "Kitchen ceiling"}},
    {"entity_id": "switch.desk_lamp", "state": "off",
     "attributes": {"friendly_name": "Desk lamp"}},
    {"entity_id": "sensor.office_temp", "state": "21.4",
     "attributes": {"friendly_name": "Office temperature"}},
    {"entity_id": "scene.movie", "state": "unknown", "attributes": {}},
]


class FakeHttp:
    """Stands in for webrequest.request and records every call."""

    def __init__(self, answers=None, error=None, raw=None) -> None:
        self.answers = answers or {}
        self.error = error
        self.raw = raw
        self.calls: list[tuple] = []

    def __call__(self, url, method="GET", headers=None, body="", timeout=None):
        self.calls.append((url, method, headers or {}, body, timeout))
        if self.error is not None:
            raise self.error
        if self.raw is not None:
            return 200, self.raw
        for path, answer in self.answers.items():
            if url.endswith(path):
                return 200, json.dumps(answer)
        return 200, "null"

    @property
    def last_body(self) -> dict:
        return json.loads(self.service_calls[-1][3] or "{}")

    @property
    def service_calls(self) -> list:
        """Only the service invocations.

        `act()` may read the state before and after calling one, so indexing
        the raw call list from the end is fragile: which call is last depends
        on whether the server said what it ended in.
        """
        return [call for call in self.calls if "/api/services/" in call[0]]

    @property
    def last_service(self) -> str:
        return self.service_calls[-1][0] if self.service_calls else ""


DEFAULT_ANSWERS = {
    "/api/states": STATES,
    "/api/": {"message": "API running."},
    "/api/config": {"version": "2025.7.1"},
    "/api/states/light.kitchen": STATES[0],
    "/api/states/sensor.office_temp": STATES[2],
}


class ClientTestCase(unittest.TestCase):
    def client(self, http=None, token=TOKEN, url="http://box:8123"):
        self.http = http or FakeHttp(DEFAULT_ANSWERS)
        patch = unittest.mock.patch.object(ha.webrequest, "request", self.http)
        patch.start()
        self.addCleanup(patch.stop)
        ha._labels.clear()
        self.addCleanup(ha._labels.clear)
        return HomeAssistantClient(
            store=SimpleNamespace(load=lambda: token), base_url=url
        )


class AddressTests(unittest.TestCase):
    def test_the_three_shapes_people_paste_all_work(self) -> None:
        """`http://box:8123/api/api/states` fails with a 404 that says nothing
        about what went wrong."""
        for typed in (
            "http://box:8123", "http://box:8123/", "http://box:8123/api",
            "http://box:8123/api/", "  http://box:8123  ",
        ):
            with self.subTest(typed=typed):
                self.assertEqual(ha._clean_url(typed), "http://box:8123")

    def test_a_bare_host_gets_a_scheme(self) -> None:
        self.assertEqual(ha._clean_url("box:8123"), "http://box:8123")

    def test_nothing_stays_nothing(self) -> None:
        self.assertEqual(ha._clean_url("  "), "")


class ConfigurationTests(ClientTestCase):
    def test_it_is_not_configured_without_both_halves(self) -> None:
        self.assertFalse(self.client(token="").configured())
        self.assertFalse(self.client(url="").configured())
        self.assertTrue(self.client().configured())

    def test_the_token_is_read_from_the_keyring_once(self) -> None:
        reads: list = []
        client = HomeAssistantClient(
            store=SimpleNamespace(load=lambda: reads.append(1) or TOKEN),
            base_url="http://box:8123",
        )

        for _ in range(5):
            client.token()

        self.assertEqual(len(reads), 1)

    def test_a_missing_server_is_reported_before_any_request(self) -> None:
        client = self.client(url="")

        with self.assertRaises(NotConfigured):
            client.states()
        self.assertEqual(self.http.calls, [])

    def test_a_missing_token_is_reported_before_any_request(self) -> None:
        client = self.client(token="")

        with self.assertRaises(NotConfigured):
            client.states()
        self.assertEqual(self.http.calls, [])

    def test_changing_server_forgets_what_the_other_one_had(self) -> None:
        client = self.client()
        client.entities()
        client._remember("light.kitchen", "on")

        client.configure("http://other:8123")

        self.assertEqual(client._states, {})
        self.assertEqual(client._entities[1], [])

    def test_forgetting_removes_the_token_from_the_session(self) -> None:
        client = self.client()
        self.assertTrue(client.configured())

        client.forget()

        self.assertFalse(client.configured())


class RequestTests(ClientTestCase):
    def test_the_token_is_sent_as_a_bearer(self) -> None:
        self.client().states()

        self.assertEqual(
            self.http.calls[-1][2]["Authorization"], f"Bearer {TOKEN}"
        )

    def test_the_token_never_appears_in_a_status_message(self) -> None:
        """It is a ten-year credential, and the log is a file on disk."""
        client = self.client(FakeHttp(error=webrequest.WebRequestError(
            "The server answered HTTP 401: bad token"
        )))

        with self.assertRaises(HomeAssistantError) as caught:
            client.states()

        self.assertNotIn(TOKEN, str(caught.exception))

    def test_a_refusal_becomes_something_somebody_can_act_on(self) -> None:
        client = self.client(FakeHttp(error=webrequest.WebRequestError(
            "The server answered HTTP 401: unauthorized"
        )))

        with self.assertRaises(HomeAssistantError) as caught:
            client.states()

        self.assertIn("Create a new one", str(caught.exception))

    def test_a_wrong_address_says_so_rather_than_showing_a_number(self) -> None:
        client = self.client(FakeHttp(error=webrequest.WebRequestError(
            "The server answered HTTP 404: not found"
        )))

        with self.assertRaises(HomeAssistantError) as caught:
            client.states()

        self.assertIn("did not recognise that address", str(caught.exception))

    def test_something_that_is_not_home_assistant_is_reported(self) -> None:
        client = self.client(FakeHttp({"/api/states": {"nope": 1}}))

        with self.assertRaises(HomeAssistantError) as caught:
            client.states()

        self.assertIn("not like Home Assistant", str(caught.exception))

    def test_a_page_that_is_not_json_is_reported(self) -> None:
        """A router login page on the address somebody typed."""
        client = self.client(FakeHttp(raw="<html>router login</html>"))

        with self.assertRaises(HomeAssistantError) as caught:
            client.states()

        self.assertIn("not like Home Assistant", str(caught.exception))

    def test_the_check_confirms_both_halves_and_names_the_version(self) -> None:
        self.assertEqual(self.client().check(), "2025.7.1")


class ServiceTests(ClientTestCase):
    def test_one_key_works_on_anything(self) -> None:
        """Addressed to the `homeassistant` domain rather than the entity's
        own, so a light, a switch, a fan and a media player are one action."""
        self.client().act("light.kitchen", ha.TURN_ON)

        url, method, _headers, _body, _timeout = self.http.service_calls[-1]
        self.assertTrue(url.endswith("/api/services/homeassistant/turn_on"))
        self.assertEqual(method, "POST")

    def test_the_entity_travels_in_the_body(self) -> None:
        self.client().act("switch.desk_lamp", ha.TURN_ON)

        self.assertEqual(self.http.last_body, {"entity_id": "switch.desk_lamp"})

    def test_toggling_an_idle_media_player_turns_it_off(self) -> None:
        """The bug this fix exists for. `homeassistant.toggle` reads `idle` as
        switched off and turns it on again, so a Chromecast key toggled on and
        never off."""
        client = self.client(FakeHttp({
            "/api/states/media_player.altavoz": {"state": "idle"},
        }))

        client.act("media_player.altavoz", ha.TOGGLE)

        self.assertTrue(
            self.http.last_service.endswith("/services/homeassistant/turn_off")
        )

    def test_toggling_something_off_turns_it_on(self) -> None:
        client = self.client(FakeHttp({
            "/api/states/light.kitchen": {"state": "off"},
        }))

        client.act("light.kitchen", ha.TOGGLE)

        self.assertTrue(
            self.http.last_service.endswith("/services/homeassistant/turn_on")
        )

    def test_a_toggle_never_reaches_the_generic_service(self) -> None:
        """It is resolved here so every domain follows `is_on()` rather than
        whatever the generic service makes of that domain's vocabulary."""
        client = self.client(FakeHttp({
            "/api/states/light.kitchen": {"state": "on"},
        }))

        client.act("light.kitchen", ha.TOGGLE)

        self.assertNotIn(
            "/services/homeassistant/toggle",
            " ".join(call[0] for call in self.http.calls),
        )

    def test_a_state_that_cannot_be_read_turns_it_on(self) -> None:
        """The recoverable guess: the next press has the state and does the
        right thing, while guessing off leaves somebody pressing a dead key."""
        client = self.client(FakeHttp(
            error=webrequest.WebRequestError("The server answered HTTP 404: x")
        ))

        with self.assertRaises(HomeAssistantError):
            client.act("light.kitchen", ha.TOGGLE)

        self.assertTrue(
            self.http.last_service.endswith("/services/homeassistant/turn_on")
        )

    def test_a_scene_is_run_rather_than_toggled(self) -> None:
        """A scene has nothing to toggle: it happens.

        The state matters here. Home Assistant reports a scene's state as the
        **timestamp it was last activated**, which `is_on()` reads as on -- so
        without the short-circuit, toggling a scene that had ever run would
        resolve to `turn_off` and do nothing at all.
        """
        for entity in ("scene.movie", "script.bedtime", "button.doorbell"):
            with self.subTest(entity=entity):
                client = self.client(FakeHttp({
                    f"/api/states/{entity}": {
                        "state": "2026-08-03T09:15:00.000000+00:00"
                    },
                }))

                client.act(entity, ha.TOGGLE)

                self.assertTrue(self.http.last_service.endswith("/turn_on"))

    def test_a_light_that_is_on_is_turned_off(self) -> None:
        client = self.client(FakeHttp({
            "/api/states/light.kitchen": {"state": "on"},
        }))

        client.act("light.kitchen", ha.TOGGLE)

        self.assertTrue(self.http.last_service.endswith("/turn_off"))

    def test_a_key_with_no_entity_says_so(self) -> None:
        with self.assertRaises(HomeAssistantError) as caught:
            self.client().act("", ha.TOGGLE)

        self.assertIn("no Home Assistant entity", str(caught.exception))

    def test_something_that_is_not_an_entity_id_is_refused(self) -> None:
        with self.assertRaises(HomeAssistantError):
            self.client().act("kitchen", ha.TOGGLE)

    def test_it_reports_the_state_the_server_says_it_ended_in(self) -> None:
        """Home Assistant answers 200 for a `turn_on` the entity cannot
        perform. The changed-states list is the only thing that tells them
        apart, and discarding it is how a key comes to report success while
        nothing happened."""
        client = self.client(FakeHttp({
            "/api/services/homeassistant/turn_on": [
                {"entity_id": "light.kitchen", "state": "on"},
            ],
        }))

        self.assertEqual(client.act("light.kitchen", ha.TURN_ON), "on")

    def test_an_entity_that_ignored_the_service_reports_nothing(self) -> None:
        """A Chromecast that cannot be woken is exactly this case."""
        client = self.client(FakeHttp({
            "/api/services/homeassistant/turn_on": [],
        }))

        self.assertEqual(client.act("media_player.salon", ha.TURN_ON), "")

    def test_a_change_to_some_other_entity_is_not_claimed(self) -> None:
        """Turning on a group changes several things; only this key's entity
        answers for this key."""
        client = self.client(FakeHttp({
            "/api/services/homeassistant/turn_on": [
                {"entity_id": "light.hall", "state": "on"},
            ],
        }))

        self.assertEqual(client.act("light.kitchen", ha.TURN_ON), "")

    def test_a_malformed_answer_never_raises(self) -> None:
        # A bare number is the one that matters: `for entry in 5` raises,
        # while a dict or a string merely iterates into nothing.
        for answer in ({"not": "a list"}, [None], ["text"], [], 5, "text"):
            with self.subTest(answer=answer):
                client = self.client(FakeHttp({
                    "/api/services/homeassistant/turn_on": answer,
                }))

                self.assertEqual(client.act("light.kitchen", ha.TURN_ON), "")

    def test_an_unknown_service_falls_back_to_toggling(self) -> None:
        """So no stored key changes behaviour by being loaded."""
        client = self.client(FakeHttp({
            "/api/states/light.kitchen": {"state": "on"},
        }))

        client.act("light.kitchen", "explode")

        self.assertTrue(self.http.last_service.endswith("/turn_off"))


class EntityListTests(ClientTestCase):
    def test_it_lists_what_the_server_really_has(self) -> None:
        """The whole reason this exists over `web.request`: typing
        `light.kitchen_ceiling_2` from memory is where the failures live."""
        entities = self.client().entities()

        self.assertIn(("light.kitchen", "Kitchen ceiling"), entities)

    def test_it_is_grouped_by_domain(self) -> None:
        domains = [
            entity.split(".", 1)[0] for entity, _name in self.client().entities()
        ]

        self.assertEqual(domains, sorted(domains))

    def test_an_entity_with_no_friendly_name_shows_its_id(self) -> None:
        entities = dict(self.client().entities())

        self.assertEqual(entities["scene.movie"], "scene.movie")

    def test_the_editor_shows_a_name_and_stores_an_id(self) -> None:
        self.client().entities()

        self.assertIn("Kitchen ceiling", ha.entity_label("light.kitchen"))
        self.assertIn("light.kitchen", ha.entity_label("light.kitchen"))

    def test_an_unknown_entity_shows_as_itself(self) -> None:
        self.assertEqual(ha.entity_label("light.gone"), "light.gone")

    def test_the_list_is_cached(self) -> None:
        """The editor asks for it on the GTK thread while building a row, and
        a house has hundreds of entities."""
        client = self.client()

        client.entities(now=1000.0)
        client.entities(now=1000.0 + ha.ENTITY_TTL - 1)
        self.assertEqual(len(self.http.calls), 1)

        client.entities(now=1000.0 + ha.ENTITY_TTL + 1)
        self.assertEqual(len(self.http.calls), 2)

    def test_listing_also_updates_every_state_it_carried(self) -> None:
        """The answer holds them all, so a dropdown refresh is a free update
        of everything a key on this page might be drawing."""
        client = self.client()

        client.entities(now=1000.0)

        self.assertEqual(
            client.cached_state("light.kitchen", now=1000.5), "on"
        )

    def test_an_unreachable_server_answers_an_empty_list(self) -> None:
        """The field then stays a plain text entry, so an entity id can still
        be typed by hand."""
        client = self.client(FakeHttp(error=webrequest.WebRequestError("down")))

        self.assertEqual(client.entities(), [])

    def test_nothing_is_asked_before_it_is_configured(self) -> None:
        client = self.client(token="")

        self.assertEqual(client.entities(), [])
        self.assertEqual(self.http.calls, [])


class CachedStateTests(ClientTestCase):
    def setUp(self) -> None:
        self.jobs: list = []
        patch = unittest.mock.patch.object(
            ha.webrequest, "background",
            lambda work, *args: (self.jobs.append((work, args)), True)[1],
        )
        patch.start()
        self.addCleanup(patch.stop)

    def test_it_never_reaches_the_network_itself(self) -> None:
        """It runs on the single render worker, and the server is a box in the
        house: waiting there would stall every other key."""
        client = self.client()

        def explode(*_a, **_k):
            raise AssertionError("cached_state performed a request")

        with unittest.mock.patch.object(ha.webrequest, "request", explode):
            client.cached_state("light.kitchen")

    def test_an_unknown_entity_draws_nothing_rather_than_off(self) -> None:
        """A light that looks switched off while it is lighting the room."""
        self.assertIsNone(self.client().cached_state("light.kitchen"))

    def test_a_burst_of_repaints_is_one_refresh(self) -> None:
        client = self.client()

        for _ in range(20):
            client.cached_state("light.kitchen")

        self.assertEqual(len(self.jobs), 1)

    def test_it_does_not_ask_again_until_the_interval_has_passed(self) -> None:
        client = self.client()
        client._states["light.kitchen"] = (1000.0, "on")

        client.cached_state("light.kitchen", now=1000.0 + ha.STATE_TTL - 1)
        self.assertEqual(self.jobs, [])

        client.cached_state("light.kitchen", now=1000.0 + ha.STATE_TTL + 1)
        self.assertEqual(len(self.jobs), 1)

    def test_a_brief_outage_keeps_the_last_state(self) -> None:
        client = self.client()
        client._states["light.kitchen"] = (1000.0, "on")

        self.assertEqual(
            client.cached_state("light.kitchen", now=1030.0), "on"
        )

    def test_a_sustained_outage_blanks_the_key(self) -> None:
        client = self.client()
        client._states["light.kitchen"] = (1000.0, "on")

        self.assertIsNone(
            client.cached_state(
                "light.kitchen", now=1000.0 + ha.STATE_STALE + 1
            )
        )

    def test_nothing_is_asked_before_it_is_configured(self) -> None:
        client = self.client(token="")

        self.assertIsNone(client.cached_state("light.kitchen"))
        self.assertEqual(self.jobs, [])

    def test_a_failed_refresh_releases_its_mark(self) -> None:
        client = self.client(FakeHttp(error=webrequest.WebRequestError("down")))
        client._pending.add("light.kitchen")

        client._refresh("light.kitchen")

        self.assertNotIn("light.kitchen", client._pending)

    def test_acting_stores_the_state_the_server_reported(self) -> None:
        """The gap this closes: the key used to blank after a press and wait
        for a round trip before showing its new state, which is exactly when
        somebody presses again thinking it failed. The authoritative state is
        already in the service call's answer."""
        client = self.client(FakeHttp({
            "/api/services/homeassistant/turn_off": [
                {"entity_id": "light.kitchen", "state": "off"},
            ],
        }))
        client._states["light.kitchen"] = (1000.0, "on")

        client.act("light.kitchen", ha.TURN_OFF)

        self.assertEqual(client.cached_state("light.kitchen"), "off")

    def test_it_asks_before_returning_when_the_server_did_not_say(self) -> None:
        """On the action worker, so the key keeps showing RUN until the answer
        is in rather than going blank in the meantime."""
        client = self.client(FakeHttp({
            "/api/services/homeassistant/turn_on": [],
            "/api/states/media_player.salon": {"state": "playing"},
        }))

        client.act("media_player.salon", ha.TURN_ON)

        self.assertEqual(
            client.cached_state("media_player.salon"), "playing"
        )
        self.assertTrue(
            self.http.calls[-1][0].endswith("/api/states/media_player.salon")
        )

    def test_a_state_that_still_cannot_be_read_leaves_no_claim(self) -> None:
        client = self.client(FakeHttp({
            "/api/services/homeassistant/turn_on": [],
        }))
        client._states["light.kitchen"] = (1000.0, "on")

        client.act("light.kitchen", ha.TURN_ON)

        self.assertNotIn("light.kitchen", client._states)


class ChangeNoticeTests(ClientTestCase):
    """A state that moved repaints the deck at once.

    Without it the key waits out the live loop's interval -- up to ten seconds
    of showing the wrong thing after a light was switched from a phone.
    """

    def test_a_real_change_is_announced(self) -> None:
        seen: list = []
        client = self.client()
        client.on_change = lambda: seen.append(1)

        client._remember("light.kitchen", "on")

        self.assertEqual(len(seen), 1)

    def test_the_same_value_again_announces_nothing(self) -> None:
        """Most polls find no change, and a full refresh per poll would redraw
        every key on the page for nothing."""
        seen: list = []
        client = self.client()
        client._remember("light.kitchen", "on")
        client.on_change = lambda: seen.append(1)

        for _ in range(5):
            client._remember("light.kitchen", "on")

        self.assertEqual(seen, [])

    def test_a_failing_listener_never_breaks_the_fetch(self) -> None:
        client = self.client()
        client.on_change = lambda: 1 / 0

        client._remember("light.kitchen", "on")

        self.assertEqual(client.cached_state("light.kitchen"), "on")

    def test_the_deck_is_what_listens(self) -> None:
        """Wired after the controller exists, since it is the controller's
        repaint that has to be called."""
        import inspect

        from linuxstreamdeck.app import LinuxStreamDeckApp

        source = inspect.getsource(LinuxStreamDeckApp.__init__)
        self.assertIn(
            "self.home_assistant.on_change = self.controller.refresh", source
        )


class OnStateTests(unittest.TestCase):
    """Which states mean the thing is doing something.

    An exclusion list, because no list of "on" states can be complete. The
    first version enumerated them and read an idle Chromecast as switched off,
    which made the key show the wrong thing and the toggle turn it on twice.
    """

    def test_the_obvious_ones_count_as_on(self) -> None:
        for state in ("on", "open", "home", "playing", "ON", "heat"):
            with self.subTest(state=state):
                self.assertTrue(ha.is_on(state))

    def test_a_media_player_between_tracks_is_still_on(self) -> None:
        """`idle` is the state that found this. Home Assistant's own media
        player toggle settled on the same rule in 2025."""
        for state in ("idle", "paused", "buffering"):
            with self.subTest(state=state):
                self.assertTrue(ha.is_on(state))

    def test_a_domain_nobody_enumerated_still_works(self) -> None:
        """A vacuum, a cover, a lawn mower: an inclusion list would have to
        grow for each of them and would be wrong until it did."""
        for state in ("cleaning", "returning", "opening", "mowing"):
            with self.subTest(state=state):
                self.assertTrue(ha.is_on(state))

    def test_off_and_standby_are_off(self) -> None:
        for state in ("off", "standby", "OFF"):
            with self.subTest(state=state):
                self.assertFalse(ha.is_on(state))

    def test_unknown_is_off_rather_than_on(self) -> None:
        """A key must not claim a device is on because nothing answered."""
        for state in ("unavailable", "unknown", "none", "", None, "  "):
            with self.subTest(state=state):
                self.assertFalse(ha.is_on(state))


class ActionTests(ClientTestCase):
    def setUp(self) -> None:
        self.messages: list[str] = []
        self.client_obj = self.client()
        self.ctx = SimpleNamespace(
            home_assistant=self.client_obj,
            bus=SimpleNamespace(
                emit=lambda topic, **d: self.messages.append(d.get("text", ""))
            ),
        )

    def test_the_press_says_what_the_entity_ended_up_doing(self) -> None:
        self.ctx.home_assistant = self.client(FakeHttp({
            "/api/services/homeassistant/turn_on": [
                {"entity_id": "light.kitchen", "state": "on"},
            ],
        }))

        REGISTRY["ha.switch"].execute(
            self.ctx, {"entity": "light.kitchen", "mode": ha.TURN_ON}
        )

        self.assertIn("is now on", self.messages[-1])

    def test_the_press_says_so_when_nothing_changed(self) -> None:
        """The only sign a key gets that it asked for something the entity
        cannot do. Without it the key reports success and the user is left
        watching a device that did not move."""
        self.ctx.home_assistant = self.client(FakeHttp({
            "/api/services/homeassistant/turn_on": [],
        }))

        REGISTRY["ha.switch"].execute(
            self.ctx, {"entity": "media_player.salon", "mode": ha.TURN_ON}
        )

        self.assertIn("no change", self.messages[-1])

    def test_pressing_the_switch_calls_the_service(self) -> None:
        REGISTRY["ha.switch"].execute(
            self.ctx, {"entity": "light.kitchen", "mode": ha.TURN_ON}
        )

        self.assertTrue(self.http.last_service.endswith("/turn_on"))

    def test_a_failure_raises_so_the_key_marks_itself(self) -> None:
        self.ctx.home_assistant = self.client(
            FakeHttp(error=webrequest.WebRequestError("down"))
        )

        with self.assertRaises(HomeAssistantError):
            REGISTRY["ha.switch"].execute(self.ctx, {"entity": "light.kitchen"})

    def test_a_session_with_no_client_says_so_rather_than_crashing(self) -> None:
        with self.assertRaises(NotConfigured):
            REGISTRY["ha.switch"].execute(
                SimpleNamespace(home_assistant=None), {"entity": "light.kitchen"}
            )

    def test_an_on_key_is_coloured_not_merely_lightened(self) -> None:
        """`active` alone renders almost indistinguishable from an idle key at
        96 px, and telling on from off at a glance is this key's whole job."""
        self.client_obj._remember("light.kitchen", "on")

        state = REGISTRY["ha.switch"].feedback(
            self.ctx, {"entity": "light.kitchen"}
        )

        self.assertEqual(state["color"], ha_actions.ON_COLOR)

    def test_an_off_key_carries_no_colour(self) -> None:
        self.client_obj._remember("light.kitchen", "off")

        state = REGISTRY["ha.switch"].feedback(
            self.ctx, {"entity": "light.kitchen"}
        )

        self.assertFalse(state["active"])
        self.assertIsNone(state["color"])

    def test_the_switch_lights_up_while_the_entity_is_on(self) -> None:
        self.client_obj._remember("light.kitchen", "on")

        self.assertTrue(
            REGISTRY["ha.switch"].feedback(
                self.ctx, {"entity": "light.kitchen"}
            )["active"]
        )

    def test_an_unanswered_entity_leaves_its_key_alone(self) -> None:
        with unittest.mock.patch.object(
            ha.webrequest, "background", lambda *a: True
        ):
            self.assertEqual(
                REGISTRY["ha.switch"].feedback(
                    self.ctx, {"entity": "light.kitchen"}
                ),
                {},
            )

    def test_the_value_key_shows_what_the_entity_reports(self) -> None:
        self.client_obj._remember("sensor.office_temp", "21.4")

        self.assertEqual(
            REGISTRY["ha.state"].feedback(
                self.ctx, {"entity": "sensor.office_temp"}
            )["display"],
            "21.4",
        )

    def test_a_value_that_has_not_arrived_shows_a_dash(self) -> None:
        with unittest.mock.patch.object(
            ha.webrequest, "background", lambda *a: True
        ):
            self.assertEqual(
                REGISTRY["ha.state"].feedback(
                    self.ctx, {"entity": "sensor.office_temp"}
                )["display"],
                ha_actions.NO_VALUE,
            )

    def test_a_long_state_is_cut_to_what_a_key_can_show(self) -> None:
        """`unavailable` is eleven characters and would be drawn at half the
        size of everything else."""
        self.assertLessEqual(len(ha_actions._short("unavailable")), 10)

    def test_both_actions_declare_they_need_the_server(self) -> None:
        """So the deck fades a key that cannot work, rather than leaving it
        looking idle."""
        for identifier in ("ha.switch", "ha.state"):
            with self.subTest(identifier=identifier):
                self.assertTrue(
                    REGISTRY[identifier].requires_home_assistant({})
                )

    def test_neither_claims_to_need_obs_or_twitch(self) -> None:
        for identifier in ("ha.switch", "ha.state"):
            with self.subTest(identifier=identifier):
                self.assertFalse(REGISTRY[identifier].needs_obs)
                self.assertFalse(REGISTRY[identifier].needs_twitch)

    def test_every_mode_and_interval_is_labelled(self) -> None:
        self.assertEqual(sorted(ha_actions.MODE_LABELS), sorted(ha_actions.MODES))
        self.assertEqual(
            sorted(ha_actions.REFRESH_LABELS), sorted(ha_actions.REFRESH_CHOICES)
        )

    def test_a_switch_key_keeps_itself_up_to_date(self) -> None:
        """It draws whether the entity is on, so it has to notice a light
        somebody turned off from their phone. Without this the state is
        fetched in the background and then nothing ever repaints the key."""
        from linuxstreamdeck.core.config import KIND_SINGLE, KeyConfig
        from linuxstreamdeck.core.controller import (
            HA_SWITCH_REFRESH_SECONDS, DeckController,
        )

        controller = SimpleNamespace(
            obs=SimpleNamespace(connected=False),
            _twitch_linked=lambda: False,
            home_assistant=SimpleNamespace(configured=lambda: True),
        )

        self.assertEqual(
            DeckController._live_interval(
                controller,
                KeyConfig(kind=KIND_SINGLE, action="ha.switch", params={}),
            ),
            HA_SWITCH_REFRESH_SECONDS,
        )

    def test_nothing_repaints_without_a_server(self) -> None:
        from linuxstreamdeck.core.config import KIND_SINGLE, KeyConfig
        from linuxstreamdeck.core.controller import DeckController

        controller = SimpleNamespace(
            obs=SimpleNamespace(connected=False),
            _twitch_linked=lambda: False,
            home_assistant=SimpleNamespace(configured=lambda: False),
        )

        for action_id in ("ha.switch", "ha.state"):
            with self.subTest(action=action_id):
                self.assertEqual(
                    DeckController._live_interval(
                        controller,
                        KeyConfig(kind=KIND_SINGLE, action=action_id, params={}),
                    ),
                    0.0,
                )

    def test_the_entity_list_fills_without_obs(self) -> None:
        from linuxstreamdeck.ui.steps import LOCAL_CHOICE_SOURCES

        self.assertIn("ha_entities", LOCAL_CHOICE_SOURCES)


class FadeTests(unittest.TestCase):
    """A key that cannot work must not look idle."""

    def _blocked(self, client) -> bool:
        from linuxstreamdeck.core.controller import DeckController

        controller = SimpleNamespace(
            obs=SimpleNamespace(connected=True),
            home_assistant=client,
            _twitch_allows=lambda action: True,
        )
        return DeckController._action_blocked(
            controller, REGISTRY["ha.switch"], {}
        )

    def test_it_fades_with_no_server(self) -> None:
        self.assertTrue(self._blocked(None))
        self.assertTrue(
            self._blocked(SimpleNamespace(configured=lambda: False))
        )

    def test_it_does_not_fade_once_the_server_is_set_up(self) -> None:
        self.assertFalse(
            self._blocked(SimpleNamespace(configured=lambda: True))
        )


class SettingsTests(unittest.TestCase):
    def test_only_the_address_is_persisted(self) -> None:
        """The token is a ten-year credential and belongs in the keyring, like
        the OBS password, the AI keys and the Twitch tokens."""
        config = Config()
        config.home_assistant.base_url = "http://box:8123"

        raw = config._serializable_dict()

        self.assertEqual(raw["home_assistant"], {"base_url": "http://box:8123"})
        self.assertNotIn("token", json.dumps(raw))

    def test_an_address_survives_a_round_trip(self) -> None:
        restored = Config.from_dict(
            {"profiles": [], "home_assistant": {"base_url": "http://box:8123"}}
        )

        self.assertEqual(restored.home_assistant.base_url, "http://box:8123")

    def test_a_configuration_without_the_field_still_loads(self) -> None:
        self.assertEqual(
            Config.from_dict({"profiles": []}).home_assistant.base_url, ""
        )

    def test_an_absurd_address_is_bounded(self) -> None:
        from linuxstreamdeck.core.config import MAX_URL_CHARS

        restored = Config.from_dict(
            {"profiles": [], "home_assistant": {"base_url": "h" * 5000}}
        )

        self.assertEqual(len(restored.home_assistant.base_url), MAX_URL_CHARS)

    def test_the_address_travels_with_an_imported_configuration(self) -> None:
        """A configuration moved to another computer in the same house still
        points at the right box."""
        import inspect

        from linuxstreamdeck.core.config import Config as C

        for name in ("import_bundle", "restore_backup"):
            with self.subTest(path=name):
                self.assertIn(
                    "self.home_assistant = replacement.home_assistant",
                    inspect.getsource(getattr(C, name)),
                )
