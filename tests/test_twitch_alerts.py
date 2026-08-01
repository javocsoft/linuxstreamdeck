"""Live Twitch events on the deck: what is waiting, and for how long.

The point of these keys is not a counter. "3 messages" says nothing about
whether somebody is being ignored; "somebody has been waiting four minutes"
says exactly that, and it is the number that corresponds to the viewer who
writes once, gets nothing back, and does not return.

So most of what is pinned here is about *not* being noisy: a sound per message
is unbearable the moment a chat wakes up, and the first thing anyone does about
that is turn it off — which puts them back to missing messages entirely.
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace

from tests.test_audio import FakeGst, FakeMessage

from linuxstreamdeck import basic_actions as _basic_actions  # noqa: F401
from linuxstreamdeck.core import actions as registry
from linuxstreamdeck.core.events import EventBus
from linuxstreamdeck.obs import actions as _obs_actions  # noqa: F401
from linuxstreamdeck.twitch import actions as twitch_actions  # noqa: F401
from linuxstreamdeck.twitch import attention as att
from linuxstreamdeck.twitch import events, eventsub
from linuxstreamdeck.twitch.attention import Attention
from linuxstreamdeck.twitch.constants import SCOPES
from linuxstreamdeck.twitch.events import Alert


def chat(text="hello", display="ana", first=False, at=None, user_id="7"):
    """An alert that arrived *now* unless a test pins the moment itself.

    The default matters: the runtime forgets anything older than
    FORGET_SECONDS, so a fixed timestamp would make every alert stale before
    the test looked at it.
    """
    return Alert(
        source=events.CHAT, display=display, text=text, user_id=user_id,
        at=att.time.monotonic() if at is None else at,
        first=first, mention="crucetaplay" in text.casefold(),
        question="?" in text,
    )


class NormalisationTests(unittest.TestCase):
    """Six payload shapes, one thing the deck has to show."""

    def test_a_chat_message_carries_who_and_what(self) -> None:
        alert = events.from_notification("channel.chat.message", {
            "chatter_user_name": "Ana", "chatter_user_id": "7",
            "message": {"text": "hello there"}, "message_type": "text",
        })

        self.assertEqual(alert.source, events.CHAT)
        self.assertEqual(alert.display, "Ana")
        self.assertEqual(alert.user_id, "7")
        self.assertFalse(alert.first)

    def test_a_first_ever_message_is_recognised(self) -> None:
        """Twitch marks it with its own message type, not a flag, and it is
        the one nobody can afford to miss."""
        alert = events.from_notification("channel.chat.message", {
            "chatter_user_name": "Ana", "message": {"text": "hi"},
            "message_type": "user_intro",
        })

        self.assertTrue(alert.first)

    def test_a_question_and_a_mention_are_noticed(self) -> None:
        alert = events.from_notification(
            "channel.chat.message",
            {"chatter_user_name": "Ana",
             "message": {"text": "hey CrucetaPlay, how do you do that?"}},
            channel_login="crucetaplay",
        )

        self.assertTrue(alert.question)
        self.assertTrue(alert.mention)

    def test_a_follow_becomes_an_alert(self) -> None:
        alert = events.from_notification(
            "channel.follow", {"user_name": "Ana", "user_id": "7"}
        )

        self.assertEqual(alert.source, events.FOLLOW)
        self.assertIn("followed", events.describe(alert))

    def test_a_subscription_names_its_tier_in_words(self) -> None:
        alert = events.from_notification(
            "channel.subscribe", {"user_name": "Ana", "tier": "2000"}
        )

        self.assertEqual(alert.text, "Tier 2")

    def test_a_gift_from_nobody_still_has_a_name(self) -> None:
        alert = events.from_notification(
            "channel.subscription.gift", {"is_anonymous": True, "total": 5}
        )

        self.assertEqual(alert.display, "Anonymous")
        self.assertEqual(alert.user_id, "")

    def test_a_raid_carries_how_many_came(self) -> None:
        alert = events.from_notification("channel.raid", {
            "from_broadcaster_user_name": "Ana", "viewers": 42,
        })

        self.assertEqual(alert.viewers, 42)
        self.assertIn("42", events.describe(alert))

    def test_one_viewer_is_not_pluralised(self) -> None:
        alert = events.from_notification("channel.raid", {
            "from_broadcaster_user_name": "Ana", "viewers": 1,
        })

        self.assertIn("1 viewer", events.describe(alert))
        self.assertNotIn("viewers", events.describe(alert))

    def test_an_unknown_type_is_ignored_rather_than_guessed_at(self) -> None:
        self.assertIsNone(
            events.from_notification("channel.something.new", {"a": 1})
        )

    def test_rubbish_never_raises(self) -> None:
        self.assertIsNone(events.from_notification("channel.follow", None))
        for name, _v, _c, _s in eventsub.SUBSCRIPTIONS:
            with self.subTest(subscription=name):
                self.assertIsNotNone(events.from_notification(name, {}))


class FilterTests(unittest.TestCase):
    """What makes one key work on a channel of three and one of three hundred."""

    def test_everything_passes_the_open_filter(self) -> None:
        self.assertTrue(
            events.matches(chat(), (events.CHAT,), events.FILTER_ALL)
        )

    def test_the_attention_filter_keeps_only_what_needs_an_answer(self) -> None:
        sources = (events.CHAT,)

        self.assertTrue(events.matches(
            chat("how does that work?"), sources, events.FILTER_ATTENTION))
        self.assertTrue(events.matches(
            chat("hi", first=True), sources, events.FILTER_ATTENTION))
        self.assertFalse(events.matches(
            chat("lol"), sources, events.FILTER_ATTENTION))

    def test_the_first_filter_keeps_only_newcomers(self) -> None:
        sources = (events.CHAT,)

        self.assertTrue(events.matches(
            chat("hi", first=True), sources, events.FILTER_FIRST))
        self.assertFalse(events.matches(
            chat("anyone?"), sources, events.FILTER_FIRST))

    def test_a_chat_filter_never_hides_a_follow_or_a_raid(self) -> None:
        """The filter answers a question only chat has."""
        follow = Alert(source=events.FOLLOW, display="Ana")

        self.assertTrue(
            events.matches(follow, events.SOURCES, events.FILTER_FIRST)
        )

    def test_a_key_ignores_sources_it_does_not_watch(self) -> None:
        self.assertFalse(
            events.matches(chat(), (events.FOLLOW,), events.FILTER_ALL)
        )


class AttentionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.attention = Attention()

    def test_a_key_sees_what_arrived_after_it_last_looked(self) -> None:
        self.attention.add(chat(at=100.0))
        self.attention.acknowledge("k", now=150.0)
        self.attention.add(chat(at=200.0))

        pending = self.attention.pending(
            "k", (events.CHAT,), events.FILTER_ALL, now=210.0
        )

        self.assertEqual(len(pending), 1)

    def test_two_keys_forget_independently(self) -> None:
        """Pressing one must never silence another watching something else."""
        self.attention.add(chat(at=100.0))
        self.attention.acknowledge("a", now=150.0)

        self.assertEqual(
            self.attention.pending("a", (events.CHAT,), "all", now=160.0), []
        )
        self.assertEqual(
            len(self.attention.pending("b", (events.CHAT,), "all", now=160.0)), 1
        )

    def test_old_alerts_stop_waiting(self) -> None:
        """A key that never goes quiet again is one nobody looks at."""
        self.attention.add(chat(at=0.0))

        pending = self.attention.pending(
            "k", (events.CHAT,), "all", now=att.FORGET_SECONDS + 10
        )

        self.assertEqual(pending, [])

    def test_a_key_that_was_never_pressed_has_acknowledged_nothing(self) -> None:
        """The sentinel cannot be 0.0, which is a real monotonic timestamp.

        Monotonic time counts from boot, so on a machine that started moments
        ago an alert can legitimately be stamped near — or, in a test that
        works backwards from now, below — zero. Treating that as already seen
        made a key stay quiet with somebody waiting on it.
        """
        self.attention.add(chat(at=-50.0))

        pending = self.attention.pending("k", (events.CHAT,), "all", now=0.0)

        self.assertEqual(len(pending), 1)

    def test_the_history_is_bounded(self) -> None:
        for i in range(att.HISTORY_LIMIT + 50):
            self.attention.add(chat(at=float(i)))

        self.assertLessEqual(len(self.attention._alerts), att.HISTORY_LIMIT)

    def test_clearing_forgets_everything(self) -> None:
        self.attention.add(chat())

        self.attention.clear()

        self.assertEqual(self.attention.pending("k", events.SOURCES, "all"), [])

    def test_arrival_is_announced_so_the_deck_can_react(self) -> None:
        seen = []
        runtime = Attention(seen.append)

        runtime.add(chat())

        self.assertEqual(len(seen), 1)


class WaitingTests(unittest.TestCase):
    def test_the_wait_is_measured_from_the_oldest(self) -> None:
        """The oldest is the person being ignored; the newest is not."""
        alerts = [chat(at=100.0), chat(at=190.0)]

        self.assertEqual(att.waiting(alerts, now=200.0), 100.0)

    def test_nothing_waiting_is_no_wait(self) -> None:
        self.assertEqual(att.waiting([]), 0.0)

    def test_urgency_climbs_with_the_wait(self) -> None:
        self.assertEqual(att.urgency(0), 0)
        self.assertEqual(att.urgency(att.URGENCY_STEPS[0]), 1)
        self.assertEqual(att.urgency(att.URGENCY_STEPS[-1] + 1), 3)

    def test_the_clock_stays_readable_on_a_key(self) -> None:
        self.assertEqual(att.clock(9), "9s")
        self.assertEqual(att.clock(75), "1:15")
        self.assertEqual(att.clock(3700), "1h01")

    def test_a_count_alone_could_not_say_this(self) -> None:
        """Three messages a second old are fine; one five minutes old is not,
        and a counter renders both identically."""
        fresh = [chat(at=199.0), chat(at=199.5), chat(at=200.0)]
        stale = [chat(at=0.0)]

        self.assertEqual(att.urgency(att.waiting(fresh, now=200.0)), 0)
        self.assertEqual(att.urgency(att.waiting(stale, now=400.0)), 3)


class SoundRuleTests(unittest.TestCase):
    """The mailbox rule, not the keystroke one."""

    def test_it_sounds_when_the_key_goes_from_quiet_to_waiting(self) -> None:
        self.assertTrue(att.should_sound(False, [chat()], 0.0, 0.0))

    def test_it_stays_quiet_for_every_message_after_that(self) -> None:
        """A sound per message is what makes people turn the sound off, which
        puts them back to missing messages entirely."""
        self.assertFalse(att.should_sound(True, [chat(), chat()], 100.0, 0.0))

    def test_it_reminds_once_the_interval_has_passed(self) -> None:
        self.assertTrue(
            att.should_sound(True, [chat()], 100.0, 60.0, now=200.0)
        )

    def test_it_does_not_remind_before_the_interval(self) -> None:
        self.assertFalse(
            att.should_sound(True, [chat()], 100.0, 60.0, now=120.0)
        )

    def test_nothing_waiting_never_sounds(self) -> None:
        self.assertFalse(att.should_sound(False, [], 0.0, 60.0))


class SessionTests(unittest.TestCase):
    """The parts of the socket lifecycle that are silent when they break."""

    def setUp(self) -> None:
        self.created: list[tuple] = []
        self.client = SimpleNamespace(
            linked=True,
            account="crucetaplay",
            user_id=lambda: "42",
            create_subscription=lambda *a: self.created.append(a),
        )
        self.alerts: list = []
        self.session = eventsub.EventSubSession(
            self.client, EventBus(), self.alerts.append
        )

    @staticmethod
    def _welcome(session_id="s1", keepalive=10):
        return {
            "metadata": {"message_type": "session_welcome"},
            "payload": {"session": {
                "id": session_id, "keepalive_timeout_seconds": keepalive,
            }},
        }

    def test_a_welcome_creates_every_subscription(self) -> None:
        self.session._handle(self._welcome(), resuming=False)

        self.assertEqual(len(self.created), len(eventsub.SUBSCRIPTIONS))
        self.assertTrue(self.session.connected)

    def test_a_reconnect_does_not_create_them_again(self) -> None:
        """Twitch carries subscriptions across a reconnect, so recreating
        them would deliver every event twice."""
        self.session._handle(self._welcome(), resuming=True)

        self.assertEqual(self.created, [])
        self.assertTrue(self.session.connected)

    def test_the_welcome_reports_the_keepalive_twitch_asked_for(self) -> None:
        keepalive, _reconnect = self.session._handle(
            self._welcome(keepalive=30), resuming=False
        )

        self.assertEqual(keepalive, 30)

    def test_a_welcome_without_a_session_is_refused(self) -> None:
        with self.assertRaises(Exception):
            self.session._handle(self._welcome(session_id=""), resuming=False)

    def test_a_reconnect_message_hands_back_the_new_address(self) -> None:
        _keepalive, reconnect = self.session._handle({
            "metadata": {"message_type": "session_reconnect"},
            "payload": {"session": {"reconnect_url": "wss://elsewhere"}},
        }, resuming=False)

        self.assertEqual(reconnect, "wss://elsewhere")

    def test_a_revocation_is_survived(self) -> None:
        self.session._handle({
            "metadata": {"message_type": "revocation"},
            "payload": {"subscription": {"type": "channel.follow",
                                         "status": "user_removed"}},
        }, resuming=False)

    def test_a_notification_reaches_the_deck(self) -> None:
        self.session._handle({
            "metadata": {"message_type": "notification",
                         "subscription_type": "channel.follow"},
            "payload": {"event": {"user_name": "Ana", "user_id": "7"}},
        }, resuming=False)

        self.assertEqual(len(self.alerts), 1)
        self.assertEqual(self.alerts[0].display, "Ana")

    def test_a_subscription_the_token_cannot_have_costs_only_itself(
        self,
    ) -> None:
        """Somebody who never granted subscription reading should still get
        their chat and their raids."""
        from linuxstreamdeck.twitch.http import TwitchError

        def refuse_subs(name, *_a):
            if "subscri" in name:
                raise TwitchError("missing scope")
            self.created.append(name)

        self.client.create_subscription = refuse_subs

        self.session._handle(self._welcome(), resuming=False)

        self.assertTrue(self.created)
        self.assertTrue(self.session.connected)

    def test_a_session_that_can_subscribe_to_nothing_is_a_failure(self) -> None:
        from linuxstreamdeck.twitch.http import TwitchError

        def refuse_all(*_a):
            raise TwitchError("no")

        self.client.create_subscription = refuse_all

        with self.assertRaises(TwitchError):
            self.session._handle(self._welcome(), resuming=False)

    def test_the_subscription_body_is_the_shape_twitch_wants(self) -> None:
        body = eventsub.subscription_body(
            "channel.follow", "2", {"broadcaster_user_id": "42"}, "s1"
        )

        self.assertEqual(body["transport"],
                         {"method": "websocket", "session_id": "s1"})
        self.assertEqual(body["version"], "2")
        json.dumps(body)  # must be serialisable as it stands

    def test_every_subscription_names_the_permission_it_needs(self) -> None:
        """Twitch refuses one the token cannot have with a flat "subscription
        missing proper authorization", which names nothing anyone can act on.
        Carrying the scope here is what lets the log say which one."""
        for name, _v, _f, scope in eventsub.SUBSCRIPTIONS:
            with self.subTest(subscription=name):
                if name == "channel.raid":
                    self.assertEqual(scope, "")   # raids need none
                else:
                    self.assertIn(scope, SCOPES)

    def test_a_refused_subscription_says_which_permission_is_missing(
        self,
    ) -> None:
        from linuxstreamdeck.twitch.http import TwitchError

        seen: list[str] = []
        bus = EventBus()
        bus.subscribe("status", lambda t, d: seen.append(d["text"]))
        session = eventsub.EventSubSession(self.client, bus, self.alerts.append)

        def refuse_chat(name, *_a):
            if name == "channel.chat.message":
                raise TwitchError("subscription missing proper authorization")

        self.client.create_subscription = refuse_chat
        session._handle(self._welcome(), resuming=False)

        self.assertTrue(seen)
        self.assertIn("user:read:chat", seen[0])

    def test_every_subscription_asks_for_a_version(self) -> None:
        """A missing or wrong version is accepted by nothing and reported by
        Twitch as a generic refusal."""
        for name, version, fields, _scope in eventsub.SUBSCRIPTIONS:
            with self.subTest(subscription=name):
                self.assertTrue(version.isdigit())
                self.assertTrue(fields)

    def test_the_follow_subscription_uses_version_two(self) -> None:
        """Version 1 was withdrawn; asking for it gets nothing."""
        follow = dict((n, v) for n, v, _f, _s in eventsub.SUBSCRIPTIONS)

        self.assertEqual(follow["channel.follow"], "2")

    def test_a_raid_names_only_the_receiving_side(self) -> None:
        """Twitch refuses a condition that names both."""
        raid = next(
            f for n, _v, f, _s in eventsub.SUBSCRIPTIONS
            if n == "channel.raid"
        )

        self.assertEqual(raid, ("to_broadcaster_user_id",))

    def test_stopping_an_unstarted_session_is_harmless(self) -> None:
        self.session.stop()


class ScopeTests(unittest.TestCase):
    def test_the_event_scopes_are_asked_for(self) -> None:
        for scope in ("user:read:chat", "channel:read:subscriptions",
                      "moderator:read:followers"):
            with self.subTest(scope=scope):
                self.assertIn(scope, SCOPES)

    def test_no_scope_is_asked_for_twice(self) -> None:
        self.assertEqual(len(SCOPES), len(set(SCOPES)))


class AlertKeyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.action = registry.get("twitch.alert")
        self.attention = Attention()
        self.avatars: dict[str, bytes] = {}
        self.ctx = SimpleNamespace(
            key=(0, 0, 0),
            bus=EventBus(),
            obs=SimpleNamespace(connected=False),
            twitch=SimpleNamespace(
                linked=True,
                cached_avatar=self.avatars.get,
            ),
            controller=SimpleNamespace(attention=self.attention),
        )

    def test_a_quiet_key_shows_nothing(self) -> None:
        self.assertEqual(self.action.feedback(self.ctx, {"source": "chat"}), {})

    def test_a_waiting_key_shows_the_wait(self) -> None:
        self.attention.add(chat(at=att.time.monotonic() - 75))

        state = self.action.feedback(self.ctx, {"source": "chat"})

        self.assertIn(":", state["display"])
        # It breathes in its own colour rather than the accent one running
        # keys use: this key's colour is the message, and tinting it towards
        # the accent turned "waiting five minutes" red into a calm blue.
        self.assertTrue(state["pulse"])
        self.assertNotIn("busy", state)

    def test_one_waiting_message_needs_no_count(self) -> None:
        self.attention.add(chat())

        state = self.action.feedback(self.ctx, {"source": "chat"})

        self.assertEqual(state.get("badge", ""), "")

    def test_several_are_counted(self) -> None:
        for _ in range(3):
            self.attention.add(chat())

        state = self.action.feedback(self.ctx, {"source": "chat"})

        self.assertEqual(state["badge"], "3")

    def test_the_avatar_is_read_from_the_cache_only(self) -> None:
        """This runs on a render worker while the key image is composed."""
        self.avatars["7"] = b"PNG"
        self.attention.add(chat(user_id="7"))

        state = self.action.feedback(self.ctx, {"source": "chat"})

        self.assertEqual(state["image"], b"PNG")

    def test_a_missing_avatar_still_draws_the_key(self) -> None:
        self.attention.add(chat(user_id="nobody"))

        state = self.action.feedback(self.ctx, {"source": "chat"})

        self.assertNotIn("image", state)
        self.assertIn("display", state)

    def test_the_avatar_can_be_turned_off(self) -> None:
        self.avatars["7"] = b"PNG"
        self.attention.add(chat(user_id="7"))

        state = self.action.feedback(
            self.ctx, {"source": "chat", "avatar": "no"}
        )

        self.assertNotIn("image", state)

    def test_pressing_it_marks_them_seen(self) -> None:
        self.attention.add(chat())

        self.action.execute(self.ctx, {"source": "chat"})

        self.assertEqual(self.action.feedback(self.ctx, {"source": "chat"}), {})

    def test_pressing_a_quiet_key_says_so(self) -> None:
        seen: list[str] = []
        self.ctx.bus.subscribe("status", lambda t, d: seen.append(d["text"]))

        self.action.execute(self.ctx, {"source": "chat"})

        self.assertIn("Nothing", seen[0])

    def test_pressing_it_reports_who_was_waiting(self) -> None:
        seen: list[str] = []
        self.ctx.bus.subscribe("status", lambda t, d: seen.append(d["text"]))
        self.attention.add(chat(display="Ana", text="hello"))

        self.action.execute(self.ctx, {"source": "chat"})

        self.assertIn("Ana", seen[0])

    def test_a_key_watching_everything_sees_a_raid(self) -> None:
        self.attention.add(Alert(source=events.RAID, display="Ana", viewers=9))

        state = self.action.feedback(self.ctx, {"source": "everything"})

        self.assertIn("display", state)

    def test_a_chat_key_ignores_a_follow(self) -> None:
        self.attention.add(Alert(source=events.FOLLOW, display="Ana"))

        self.assertEqual(self.action.feedback(self.ctx, {"source": "chat"}), {})

    def test_a_context_with_no_runtime_is_survivable(self) -> None:
        ctx = SimpleNamespace(
            key=(0, 0, 0), bus=EventBus(),
            obs=SimpleNamespace(connected=False),
            twitch=None, controller=SimpleNamespace(),
        )

        self.assertEqual(self.action.feedback(ctx, {"source": "chat"}), {})

    def test_every_source_choice_maps_to_real_sources(self) -> None:
        for choice in twitch_actions.ALERT_SOURCES:
            with self.subTest(choice=choice):
                sources = twitch_actions.alert_sources({"source": choice})
                self.assertTrue(sources)
                for source in sources:
                    self.assertIn(source, events.SOURCES)

    def test_the_action_needs_an_account_like_the_others(self) -> None:
        self.assertTrue(self.action.needs_twitch)

    def test_urgency_is_a_border_and_never_a_background(self) -> None:
        """This key usually carries the waiting person's picture, and a
        background change behind a photograph is invisible — the same reason
        the failure mark is drawn as a border."""
        self.attention.add(chat(at=att.time.monotonic() - 400))

        state = self.action.feedback(self.ctx, {"source": "chat"})

        self.assertTrue(state.get("border"))
        self.assertNotIn("color", state)

    def test_the_border_climbs_with_the_wait(self) -> None:
        colours = []
        for waited in (5, 60, 200, 400):
            runtime = Attention()
            runtime.add(chat(at=att.time.monotonic() - waited))
            self.ctx.controller = SimpleNamespace(attention=runtime)
            state = self.action.feedback(self.ctx, {"source": "chat"})
            colours.append(state.get("border", ""))

        self.assertEqual(colours[0], "")
        self.assertEqual(len(set(colours)), len(colours))


try:
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    HAS_DISPLAY = Gtk.init_check()
except Exception:  # pragma: no cover - depends on the runner
    HAS_DISPLAY = False


@unittest.skipUnless(HAS_DISPLAY, "needs a display")
class DependentParameterTests(unittest.TestCase):
    """A field that cannot apply is noise, and worse when it looks settable.

    The chat filter answers a question only chat has, so it has no business
    being offered while the key is watching raids.
    """

    def _editor(self):
        from linuxstreamdeck.core.config import Config
        from linuxstreamdeck.ui.steps import StepEditor

        app = SimpleNamespace(
            obs=SimpleNamespace(connected=False), twitch=None,
            config=Config(), bus=EventBus(),
        )
        editor = StepEditor(app)
        editor.select_action("twitch.alert")
        return editor

    @staticmethod
    def _visible(editor, name: str) -> bool:
        return editor._param_rows[name].get_visible()

    @staticmethod
    def _set(editor, name: str, value: str) -> None:
        param, widget = editor._param_widgets[name]
        labels = list(widget._value_map or {})
        wanted = next(
            label for label, stored in widget._value_map.items()
            if stored == value
        ) if widget._value_map else value
        model = widget.get_model()
        options = [model.get_string(i) for i in range(model.get_n_items())]
        widget.set_selected(options.index(wanted))
        del labels, param

    def test_the_chat_filter_is_offered_for_chat(self) -> None:
        editor = self._editor()

        self.assertTrue(self._visible(editor, "chat_filter"))

    def test_it_is_hidden_for_followers(self) -> None:
        editor = self._editor()

        self._set(editor, "source", "followers")

        self.assertFalse(self._visible(editor, "chat_filter"))

    def test_it_is_hidden_for_raids_and_subscriptions(self) -> None:
        editor = self._editor()

        for source in ("raids", "subscriptions"):
            with self.subTest(source=source):
                self._set(editor, "source", source)
                self.assertFalse(self._visible(editor, "chat_filter"))

    def test_it_returns_for_everything_which_includes_chat(self) -> None:
        editor = self._editor()
        self._set(editor, "source", "raids")

        self._set(editor, "source", "everything")

        self.assertTrue(self._visible(editor, "chat_filter"))

    def test_hiding_it_never_loses_what_was_chosen(self) -> None:
        """Switching a key to raids and back must not silently reset the
        filter that was picked for it."""
        editor = self._editor()
        self._set(editor, "chat_filter", events.FILTER_FIRST)

        self._set(editor, "source", "raids")
        self._set(editor, "source", "chat")

        self.assertEqual(
            editor.get_step().params["chat_filter"], events.FILTER_FIRST
        )

    def test_a_hidden_field_is_still_saved(self) -> None:
        editor = self._editor()
        self._set(editor, "chat_filter", events.FILTER_ATTENTION)

        self._set(editor, "source", "followers")

        self.assertEqual(
            editor.get_step().params["chat_filter"], events.FILTER_ATTENTION
        )

    def test_parameters_with_no_dependency_are_always_shown(self) -> None:
        editor = self._editor()

        self._set(editor, "source", "raids")

        for name in ("source", "sound", "volume", "avatar"):
            with self.subTest(param=name):
                self.assertTrue(self._visible(editor, name))

    def test_an_action_without_dependencies_still_builds(self) -> None:
        editor = self._editor()

        editor.select_action("twitch.clip")

        self.assertIsNotNone(editor.get_step().action)


class BorderRenderTests(unittest.TestCase):
    """The border is the signal that survives a key showing a picture."""

    @staticmethod
    def _edge_pixels(image):
        from itertools import chain

        w, h = image.size
        rgb = image.convert("RGB")
        return set(chain(
            (rgb.getpixel((x, 2)) for x in range(6, w - 6)),
            (rgb.getpixel((2, y)) for y in range(6, h - 6)),
        ))

    def test_a_requested_border_is_drawn(self) -> None:
        from linuxstreamdeck.device import renderer

        image = renderer.compose(size=(72, 72), label="Chat", border="#00ff00")

        self.assertIn((0, 255, 0), self._edge_pixels(image))

    def test_a_failure_outranks_it(self) -> None:
        """"This did not work" is the more urgent of the two messages, so a
        failing alert key must not paint over its own failure mark."""
        from linuxstreamdeck.device import renderer

        image = renderer.compose(
            size=(72, 72), label="Chat", border="#00ff00", failed=True
        )
        edge = self._edge_pixels(image)

        self.assertNotIn((0, 255, 0), edge)
        self.assertIn(renderer._rgb(renderer.ERROR_BORDER), edge)

    def test_no_border_is_drawn_when_none_is_asked_for(self) -> None:
        from linuxstreamdeck.device import renderer

        plain = renderer.compose(size=(72, 72), label="Chat")

        self.assertEqual(len(self._edge_pixels(plain)), 1)

    @staticmethod
    def _flat_photo(colour):
        """A picture with nothing to hide behind — the hard case."""
        import io

        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (150, 150), colour).save(buffer, "PNG")
        return buffer.getvalue()

    def test_a_value_over_a_picture_survives_a_bright_one(self) -> None:
        """A white "15s" on a white avatar is not dim, it is gone.

        The label has a gradient for this, but it sits along an edge; a value
        sits in the middle of the subject, where a band would cover the very
        thing the picture is there to show.
        """
        from linuxstreamdeck.device import renderer

        image = renderer.compose(
            size=(72, 72), label="Chat", center_text="15s",
            image=self._flat_photo((252, 252, 252)),
        ).convert("RGB")

        band = [
            image.getpixel((x, y))
            for y in range(16, 44) for x in range(8, 64)
        ]
        # Something dark has to separate the glyphs from the picture.
        self.assertTrue(
            any(sum(pixel) < 200 for pixel in band),
            "the value is invisible against a white picture",
        )

    def test_a_value_on_a_plain_background_is_left_alone(self) -> None:
        """The outline is for pictures; a plain key needs none and looks
        heavy-handed with one."""
        from linuxstreamdeck.device import renderer

        widths: list[int] = []
        real = renderer._outline_width
        renderer._outline_width = lambda font: widths.append(1) or real(font)
        self.addCleanup(setattr, renderer, "_outline_width", real)

        renderer.compose(size=(72, 72), label="Chat", center_text="15s")

        self.assertEqual(widths, [])

    def test_the_outline_is_the_opposite_of_the_text(self) -> None:
        """A key whose text colour was set to something dark needs a light
        outline, not a fixed black one."""
        from linuxstreamdeck.device import renderer

        self.assertEqual(renderer._contrasting("#ffffff"), (0, 0, 0))
        self.assertEqual(renderer._contrasting("#111111"), (255, 255, 255))

    def test_dark_text_over_a_dark_picture_gets_a_light_outline(self) -> None:
        """The end-to-end half of the rule above. Testing `_contrasting()` on
        its own proves it computes the right answer, not that the answer is
        used — a hardcoded black outline passes that and vanishes here."""
        from linuxstreamdeck.device import renderer

        image = renderer.compose(
            size=(72, 72), label="Chat", center_text="15s",
            text_color="#111111", image=self._flat_photo((12, 12, 16)),
        ).convert("RGB")

        band = [
            image.getpixel((x, y))
            for y in range(16, 44) for x in range(8, 64)
        ]

        self.assertTrue(
            any(sum(pixel) > 600 for pixel in band),
            "dark text on a dark picture was outlined in dark too",
        )

    def test_the_outline_scales_with_the_text(self) -> None:
        """So it reads the same on a Mini and on an XL."""
        from linuxstreamdeck.device import renderer

        small = renderer._outline_width(renderer._font(10))
        large = renderer._outline_width(renderer._font(40))

        self.assertGreaterEqual(small, 1)
        self.assertGreater(large, small)

    def test_the_pulse_keeps_the_colour_it_was_given(self) -> None:
        """Blending towards the accent turned a red "waiting five minutes"
        key into a calm blue, which is exactly the message lost."""
        from linuxstreamdeck.device import renderer

        red = "#8a1020"
        quiet = renderer.compose(size=(72, 72), bg=red)
        breathed = renderer.compose(
            size=(72, 72), bg=red, pulse=True, busy_phase=True
        )
        tinted = renderer.compose(
            size=(72, 72), bg=red, busy=True, busy_phase=True
        )

        def centre(img):
            return img.convert("RGB").getpixel((36, 36))

        def hue(pixel):
            import colorsys

            return colorsys.rgb_to_hls(*[c / 255 for c in pixel])[0]

        # The claim is about hue, not about how red-versus-blue the pixel is:
        # measured that way both blends happened to agree exactly, which is
        # what a weaker assertion here would have hidden.
        self.assertGreater(sum(centre(breathed)), sum(centre(quiet)))
        self.assertAlmostEqual(hue(centre(breathed)), hue(centre(quiet)), places=2)
        self.assertNotAlmostEqual(hue(centre(tinted)), hue(centre(quiet)), places=2)


def follow(display="ana", at=None, user_id="7"):
    return Alert(
        source=events.FOLLOW, display=display, text="", user_id=user_id,
        at=att.time.monotonic() if at is None else at,
    )


class AlertSoundDeliveryTests(unittest.TestCase):
    """From "this should make a noise" to a noise actually coming out.

    Everything about the sound used to be tested as policy — `should_sound`
    alone — and the call that plays it was not tested at all. Both bugs that
    made a configured follower sound completely silent lived in that gap: the
    volume was handed over as a fraction where a percentage was expected, so a
    key set to 70 played at 0.7%, and the shutdown flag was handed over as an
    Event where a callable was expected, so playback raised on its first
    iteration -- into a Future nobody reads.
    """

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.sound = Path(self.temp.name) / "ding.mp3"
        self.sound.write_bytes(b"not really audio, the player is a fake")

    def _played(self, params: dict):
        """The arguments the controller ends up giving the audio player."""
        from linuxstreamdeck.core import controller as controller_module
        from linuxstreamdeck.core.controller import DeckController

        jobs: list = []
        stub = SimpleNamespace(
            _stopping=threading.Event(),
            _notification_executor=SimpleNamespace(
                submit=lambda fn, *a: jobs.append(lambda: fn(*a))
            ),
            bus=SimpleNamespace(emit=lambda *a, **k: None),
        )
        stub._alert_sound = DeckController._alert_sound.__get__(
            stub, type(stub)
        )
        DeckController._play_alert_sound(stub, params)

        recorded: list = []
        with unittest.mock.patch.object(
            controller_module,
            "play_audio",
            lambda *a, **k: recorded.append((a, k)),
        ):
            for job in jobs:
                job()
        return recorded

    def test_what_is_submitted_really_plays(self) -> None:
        """The end-to-end check: those arguments, through the real player.

        Asserting the controller called *something* is what let both bugs
        through; this feeds what it passed to `play_audio` itself.
        """
        from linuxstreamdeck.core.audio import play_audio

        (args, kwargs), = self._played(
            {"sound": str(self.sound), "volume": 70}
        )
        gst = FakeGst([FakeMessage(FakeGst.MessageType.EOS)])

        play_audio(*args, **kwargs, gst=gst)

        self.assertEqual(gst.player.properties["volume"], 0.7)
        self.assertEqual(gst.player.states, ["playing", "null"])

    def test_the_volume_is_a_percentage_like_everywhere_else(self) -> None:
        from linuxstreamdeck.core.audio import play_audio

        (args, kwargs), = self._played(
            {"sound": str(self.sound), "volume": 25}
        )
        gst = FakeGst([FakeMessage(FakeGst.MessageType.EOS)])

        play_audio(*args, **kwargs, gst=gst)

        self.assertEqual(gst.player.properties["volume"], 0.25)

    def test_shutdown_is_asked_rather_than_handed_over(self) -> None:
        """`play_audio` calls it; an Event would raise on the first loop."""
        (args, kwargs), = self._played(
            {"sound": str(self.sound), "volume": 70}
        )
        stop = kwargs.get("stop_requested", args[3] if len(args) > 3 else None)

        self.assertTrue(callable(stop))
        self.assertIs(stop(), False)

    def test_a_key_with_no_sound_plays_nothing(self) -> None:
        self.assertEqual(self._played({"sound": "", "volume": 70}), [])

    def test_a_sound_that_cannot_be_played_says_so(self) -> None:
        """The executor keeps a worker's error in a Future nobody reads, so
        without this a missing file is silent exactly like a working one."""
        from linuxstreamdeck.core.controller import DeckController

        said: list = []
        jobs: list = []
        stub = SimpleNamespace(
            _stopping=threading.Event(),
            _notification_executor=SimpleNamespace(
                submit=lambda fn, *a: jobs.append(lambda: fn(*a))
            ),
            bus=SimpleNamespace(
                emit=lambda _topic, **data: said.append(data.get("text", ""))
            ),
        )
        stub._alert_sound = DeckController._alert_sound.__get__(
            stub, type(stub)
        )

        DeckController._play_alert_sound(
            stub, {"sound": "/nowhere/at/all.mp3", "volume": 70}
        )
        with self.assertLogs("linuxstreamdeck.core.controller", "ERROR"):
            for job in jobs:
                job()

        self.assertEqual(len(said), 1)
        self.assertIn("not found", said[0])


class AlertRunTests(unittest.TestCase):
    """When a key counts as having gone quiet, so the next arrival sounds.

    The mailbox rule needs to know whether this key was *already* showing
    somebody waiting. Remembering that as a flag looked equivalent and was not:
    nothing cleared it once the alerts it referred to were forgotten, so a key
    nobody pressed made its noise once and then stayed silent for good.
    """

    def _controller(self, **params):
        from linuxstreamdeck.core.config import KIND_SINGLE, KeyConfig
        from linuxstreamdeck.core.controller import DeckController

        settings = {"source": "followers", "sound": "/ding.mp3", "volume": 70}
        settings.update(params)
        stub = SimpleNamespace(
            _stopping=threading.Event(),
            bus=SimpleNamespace(emit=lambda *a, **k: None),
            twitch=None,
            attention=Attention(),
            _alerting={},
            _refresh_runtime_keys=lambda _keys: None,
            sounds=[],
            flashes=[],
        )
        stub._start_flash = lambda color, word: stub.flashes.append(
            (color, word)
        )
        stub.attention._on_change = lambda alert: DeckController._on_alert(
            stub, alert
        )
        key = KeyConfig(
            kind=KIND_SINGLE, action="twitch.alert", params=settings
        )
        stub._alert_keys = lambda: {("p", 0, (), 3): key}
        stub._play_alert_sound = lambda params: stub.sounds.append(params)
        return stub

    def test_the_first_follower_makes_a_noise(self) -> None:
        deck = self._controller()

        deck.attention.add(follow())

        self.assertEqual(len(deck.sounds), 1)

    def test_the_second_one_straight_after_does_not(self) -> None:
        """A noise per event is what makes people turn the sound off."""
        deck = self._controller()

        deck.attention.add(follow())
        deck.attention.add(follow(display="bea"))

        self.assertEqual(len(deck.sounds), 1)

    def test_a_key_that_went_quiet_again_makes_a_noise_again(self) -> None:
        deck = self._controller()

        deck.attention.add(follow(at=att.time.monotonic() - 10.0))
        # Long enough that the first one has stopped waiting on anybody.
        deck.attention._alerts[0].__dict__["at"] = (
            att.time.monotonic() - att.FORGET_SECONDS - 60
        )
        deck.attention.add(follow(display="bea"))

        self.assertEqual(len(deck.sounds), 2)

    def test_pressing_the_key_starts_a_new_run(self) -> None:
        deck = self._controller()

        deck.attention.add(follow())
        deck.attention.acknowledge(("p", 0, (), 3))
        deck.attention.add(follow(display="bea"))

        self.assertEqual(len(deck.sounds), 2)

    def test_a_reminder_interval_still_repeats_the_noise(self) -> None:
        deck = self._controller(remind_after="00:01")

        deck.attention.add(follow())
        deck._alerting[("p", 0, (), 3)] = att.time.monotonic() - 120
        deck.attention.add(follow(display="bea"))

        self.assertEqual(len(deck.sounds), 2)

    def test_a_key_that_asked_for_it_lights_the_whole_deck(self) -> None:
        deck = self._controller(flash="yes")

        deck.attention.add(follow())

        self.assertEqual(
            deck.flashes,
            [(twitch_actions.FLASH_COLORS[events.FOLLOW], "FOLLOW")],
        )

    def test_a_key_that_did_not_ask_never_flashes(self) -> None:
        deck = self._controller()

        deck.attention.add(follow())

        self.assertEqual(deck.flashes, [])

    def test_the_flash_follows_the_same_rule_as_the_sound(self) -> None:
        """A flash per message would be far worse than a noise per message:
        a busy chat would leave the deck strobing without a pause."""
        deck = self._controller(flash="yes")

        for _ in range(5):
            deck.attention.add(follow())

        self.assertEqual(len(deck.flashes), 1)


class FlashChoiceTests(unittest.TestCase):
    """Which alerts light the whole deck, and in what colour."""

    def test_it_is_off_unless_it_was_asked_for(self) -> None:
        self.assertFalse(twitch_actions.alert_flashes({}))
        self.assertFalse(twitch_actions.alert_flashes({"flash": "no"}))
        self.assertFalse(twitch_actions.alert_flashes(None))
        self.assertTrue(twitch_actions.alert_flashes({"flash": "yes"}))

    def test_the_key_offers_it(self) -> None:
        action = registry.get("twitch.alert")
        flash = next(p for p in action.params if p.name == "flash")

        self.assertEqual(flash.default, "no")
        self.assertEqual(sorted(flash.choices), ["no", "yes"])

    def test_each_kind_of_event_has_its_own_colour(self) -> None:
        """A flash caught in the corner of an eye says which it was."""
        colours = {
            twitch_actions.alert_flash_color({}, Alert(source=source, display="a"))
            for source in events.SOURCES
        }

        self.assertEqual(len(colours), len(events.SOURCES))

    def test_something_unrecognised_still_flashes(self) -> None:
        self.assertEqual(
            twitch_actions.alert_flash_color({}, Alert(source="whatever", display="a")),
            twitch_actions.FLASH_DEFAULT_COLOR,
        )

    def test_a_chosen_colour_replaces_the_one_per_event(self) -> None:
        alert = Alert(source=events.FOLLOW, display="a")

        self.assertEqual(
            twitch_actions.alert_flash_color({"flash_color": "#ff0000"}, alert),
            "#ff0000",
        )
        self.assertEqual(
            twitch_actions.alert_flash_color({"flash_color": "#F0A"}, alert),
            "#f0a",
        )

    def test_text_that_is_not_a_colour_falls_back(self) -> None:
        """A hand-edited file must not reach Pillow mid-render, on a worker,
        with something it cannot parse."""
        alert = Alert(source=events.FOLLOW, display="a")
        default = twitch_actions.FLASH_COLORS[events.FOLLOW]

        for value in ("", "   ", "red", "#12345", "#ggg", "0033ff", None, 7):
            with self.subTest(value=value):
                self.assertEqual(
                    twitch_actions.alert_flash_color({"flash_color": value}, alert),
                    default,
                )

    def test_the_colour_is_only_asked_for_once_the_flash_is_on(self) -> None:
        """It answers a question only the flash has. Hiding rather than
        dropping the row keeps whatever was chosen when it is turned back on."""
        action = registry.get("twitch.alert")
        colour = next(p for p in action.params if p.name == "flash_color")

        self.assertEqual(colour.depends_on, "flash")
        self.assertEqual(list(colour.depends_values), ["yes"])
        self.assertEqual(colour.default, "")
        self.assertTrue(colour.placeholder)

    def test_each_kind_of_event_says_what_it_was(self) -> None:
        words = {
            twitch_actions.alert_flash_word(Alert(source=source, display="a"))
            for source in events.SOURCES
        }

        self.assertEqual(len(words), len(events.SOURCES))
        for word in words:
            self.assertTrue(word.isupper())

    def test_the_word_stays_short_enough_to_read_at_a_glance(self) -> None:
        """`compose()` fits centered text to the key width, so a long word is
        drawn small -- and a pulse lasts a fraction of a second."""
        for word in (
            *twitch_actions.FLASH_WORDS.values(),
            twitch_actions.FLASH_DEFAULT_WORD,
        ):
            with self.subTest(word=word):
                self.assertLessEqual(len(word), twitch_actions.FLASH_WORD_CHARS)

    def test_something_unrecognised_still_says_something(self) -> None:
        self.assertEqual(
            twitch_actions.alert_flash_word(Alert(source="whatever", display="a")),
            twitch_actions.FLASH_DEFAULT_WORD,
        )


class DeckFlashTests(unittest.TestCase):
    """The whole deck lit at once, for somebody looking at a game.

    A sound can be missed under headphones; a panel going bright in the corner
    of an eye cannot. What is pinned here is mostly that it gets out of the way
    again: a flash that left the deck black, or that normal renders painted
    over half of, is worse than none.
    """

    def _deck(self, *, key_count=15, asleep=False):
        state = {"asleep": asleep, "woken": 0}

        def record_activity():
            state["woken"] += 1
            state["asleep"] = False
            return True

        class Deck:
            """`screensaver_active` is a property because the flash has to see
            it change under it, exactly as it does live."""

            image_size = (72, 72)

            @property
            def screensaver_active(self):
                return state["asleep"]

        deck = Deck()
        deck.key_count = key_count
        deck.set_key_image = lambda index, image: self.painted.append(
            (index, image.convert("RGB").getpixel((36, 36)))
        )
        deck.record_activity = record_activity
        self.state = state
        return deck

    def _controller(self, **deck_args):
        from linuxstreamdeck.core.controller import DeckController

        self.painted: list = []
        self.repaints: list = []
        stub = SimpleNamespace(
            _stopping=threading.Event(),
            _flashing=threading.Event(),
            deck=self._deck(**deck_args),
            bus=SimpleNamespace(emit=lambda *a, **k: None),
            refresh=lambda: self.repaints.append(True),
            _notification_executor=SimpleNamespace(
                submit=lambda fn, *a: self.jobs.append(lambda: fn(*a))
            ),
        )
        self.jobs: list = []
        for name in (
            "_flash_deck",
            "_paint_frame",
            "_flash_frame",
            "_flash_key",
            "_flash_center",
            "_wake_for_flash",
            "_start_flash",
            "_standing_aside",
        ):
            setattr(
                stub, name, getattr(DeckController, name).__get__(stub, type(stub))
            )
        return stub

    @staticmethod
    def _quickly():
        """The same flash with the waits taken out."""
        from linuxstreamdeck.core import controller as controller_module

        return unittest.mock.patch.multiple(
            controller_module,
            FLASH_ON_SECONDS=0.001,
            FLASH_OFF_SECONDS=0.001,
            FLASH_WAKE_SECONDS=0.05,
        )

    def _frames(self):
        """The colour each frame painted, in order."""
        return [colour for index, colour in self.painted if index == 0]

    # ---------- what it draws ----------

    def test_it_pulses_the_whole_deck_light_and_dark(self) -> None:
        from linuxstreamdeck.core.controller import FLASH_PULSES

        deck = self._controller()
        deck._flashing.set()

        with self._quickly():
            deck._flash_deck("#4fd06a")

        frames = self._frames()
        self.assertEqual(len(frames), FLASH_PULSES * 2)
        self.assertEqual(frames[0], (79, 208, 106))
        self.assertEqual(frames[1], (0, 0, 0))
        self.assertEqual(frames[0::2], [frames[0]] * FLASH_PULSES)

    def test_every_key_of_the_deck_is_lit(self) -> None:
        deck = self._controller(key_count=32)
        deck._flashing.set()

        with self._quickly():
            deck._flash_deck("#4fd06a")

        self.assertEqual(
            sorted({index for index, _c in self.painted}), list(range(32))
        )

    def test_the_middle_key_says_what_arrived(self) -> None:
        deck = self._controller()

        frame = deck._flash_frame((72, 72), "#4fd06a", "FOLLOW", 15)

        worded = {index for index, pair in enumerate(frame)
                  if pair is not frame[0]}
        self.assertEqual(worded, {7})

    def test_the_word_lands_in_the_middle_of_any_grid(self) -> None:
        for columns, count, middle in (
            (5, 15, 7),      # Original / MK.2
            (3, 6, 4),       # Mini
            (4, 8, 6),       # Neo, Plus
            (8, 32, 20),     # XL
        ):
            with self.subTest(columns=columns):
                deck = self._controller(key_count=count)
                deck.deck.columns = columns
                self.assertEqual(deck._flash_center(count), middle)

    def test_a_frame_is_composed_once_rather_than_once_per_key(self) -> None:
        """Three images for the whole flash. Composing per key per pulse would
        be a hundred and ninety-two of them on an XL, on the one worker that
        also draws every real key."""
        from linuxstreamdeck.core import controller as controller_module

        deck = self._controller(key_count=32)
        deck.deck.columns = 8
        deck._flashing.set()
        composed = []
        real = controller_module.renderer.compose

        with self._quickly(), unittest.mock.patch.object(
            controller_module.renderer,
            "compose",
            lambda **kw: composed.append(kw) or real(**kw),
        ):
            deck._flash_deck("#4fd06a", "FOLLOW")

        self.assertEqual(len(composed), 3)

    def test_the_word_is_drawn_in_whatever_reads_on_the_colour(self) -> None:
        """The colour is the user's to choose, so black cannot be assumed."""
        from linuxstreamdeck.core import controller as controller_module

        deck = self._controller()
        inks = []
        real = controller_module.renderer.compose
        with unittest.mock.patch.object(
            controller_module.renderer,
            "compose",
            lambda **kw: inks.append(kw["text_color"]) or real(**kw),
        ):
            deck._flash_key((72, 72), "#ffee00", "SUB")
            deck._flash_key((72, 72), "#101820", "SUB")

        self.assertEqual(inks, ["#000000", "#ffffff"])

    def test_it_stays_under_three_flashes_a_second(self) -> None:
        """A Stream Deck is far smaller than what photosensitivity guidance is
        written for, but staying the right side of it costs nothing."""
        from linuxstreamdeck.core.controller import (
            FLASH_OFF_SECONDS,
            FLASH_ON_SECONDS,
        )

        self.assertLessEqual(1.0 / (FLASH_ON_SECONDS + FLASH_OFF_SECONDS), 3.0)

    # ---------- getting out of the way again ----------

    def test_the_deck_goes_back_to_normal_afterwards(self) -> None:
        deck = self._controller()
        deck._flashing.set()

        with self._quickly():
            deck._flash_deck("#4fd06a")

        self.assertFalse(deck._flashing.is_set())
        self.assertEqual(self.repaints, [True])

    def test_the_flag_is_cleared_before_the_repaint(self) -> None:
        """refresh() stands aside for a flash, so the other order leaves the
        deck black until something else happens to repaint it."""
        deck = self._controller()
        deck._flashing.set()
        seen = []
        deck.refresh = lambda: seen.append(deck._flashing.is_set())

        with self._quickly():
            deck._flash_deck("#4fd06a")

        self.assertEqual(seen, [False])

    def test_a_failure_still_puts_the_deck_back(self) -> None:
        deck = self._controller()
        deck._flashing.set()
        deck.deck.set_key_image = lambda *_a: 1 / 0

        with self._quickly(), self.assertLogs(
            "linuxstreamdeck.core.controller", "WARNING"
        ):
            deck._flash_deck("#4fd06a")

        self.assertFalse(deck._flashing.is_set())
        self.assertEqual(self.repaints, [True])

    def test_normal_renders_stand_aside_while_it_runs(self) -> None:
        deck = self._controller()

        self.assertFalse(deck._standing_aside())
        deck._flashing.set()
        self.assertTrue(deck._standing_aside())

    def test_shutdown_ends_it_promptly(self) -> None:
        """Timed with the real delays, so a loop that sleeps through the
        signal takes the whole flash and is told apart from one that does not.
        """
        from linuxstreamdeck.core.controller import (
            FLASH_OFF_SECONDS,
            FLASH_ON_SECONDS,
            FLASH_PULSES,
        )

        deck = self._controller()
        deck._flashing.set()
        # Shut down the moment the first frame reaches the deck.
        deck.deck.set_key_image = lambda index, image: (
            self.painted.append(
                (index, image.convert("RGB").getpixel((36, 36)))
            ),
            deck._stopping.set(),
        )
        started = att.time.monotonic()

        deck._flash_deck("#4fd06a")

        whole = FLASH_PULSES * (FLASH_ON_SECONDS + FLASH_OFF_SECONDS)
        self.assertLess(att.time.monotonic() - started, whole / 2)
        self.assertLess(len(self._frames()), FLASH_PULSES * 2)

    # ---------- one at a time ----------

    def test_a_second_flash_does_not_interrupt_the_first(self) -> None:
        deck = self._controller()

        deck._start_flash("#4fd06a")
        deck._start_flash("#ff8a3d")

        self.assertEqual(len(self.jobs), 1)

    def test_the_flag_is_raised_before_the_work_is_queued(self) -> None:
        """A render queued in between would paint over the first pulse."""
        deck = self._controller()
        raised = []
        deck._notification_executor = SimpleNamespace(
            submit=lambda fn, *a: raised.append(deck._flashing.is_set())
        )

        deck._start_flash("#4fd06a")

        self.assertEqual(raised, [True])

    def test_a_flash_asked_for_during_shutdown_is_dropped(self) -> None:
        deck = self._controller()
        deck._notification_executor = SimpleNamespace(
            submit=lambda *_a: (_ for _ in ()).throw(RuntimeError())
        )

        deck._start_flash("#4fd06a")

        self.assertFalse(deck._flashing.is_set())

    # ---------- the screen saver owns the deck while it is on ----------

    def test_a_sleeping_deck_is_woken_first(self) -> None:
        """Asleep is exactly the state somebody deep in a game is in, so a
        flash that the saver's render guards dropped would fail precisely when
        it is needed."""
        deck = self._controller(asleep=True)
        deck._flashing.set()

        with self._quickly():
            deck._flash_deck("#4fd06a")

        self.assertEqual(self.state["woken"], 1)
        self.assertTrue(self.painted)

    def test_an_awake_deck_is_not_woken_again(self) -> None:
        deck = self._controller()
        deck._flashing.set()

        with self._quickly():
            deck._flash_deck("#4fd06a")

        self.assertEqual(self.state["woken"], 0)

    def test_a_saver_that_will_not_let_go_is_given_up_on(self) -> None:
        deck = self._controller(asleep=True)
        deck.deck.record_activity = lambda: True      # stays asleep
        deck._flashing.set()

        with self._quickly():
            started = att.time.monotonic()
            deck._flash_deck("#4fd06a")

        self.assertLess(att.time.monotonic() - started, 1.0)
        self.assertFalse(deck._flashing.is_set())


if __name__ == "__main__":
    unittest.main()
