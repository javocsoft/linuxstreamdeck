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
import unittest
from types import SimpleNamespace

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


if __name__ == "__main__":
    unittest.main()
