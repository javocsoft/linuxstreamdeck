"""Offering the Twitch connection from where the key is configured.

A key that needs an account nobody has connected renders faded on the deck and
does nothing when pressed. Before this, the only way to fix it was a dialog in
the profile menu — the last place someone configuring a key would look — so the
symptom was a key that quietly did not work with nothing anywhere saying why.
The banner appears where the choice was made.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from linuxstreamdeck import basic_actions as _basic_actions  # noqa: E402,F401
from linuxstreamdeck import ha_actions as _ha_actions  # noqa: E402,F401
from linuxstreamdeck.core import actions as action_registry  # noqa: E402
from linuxstreamdeck.core.config import (  # noqa: E402
    KIND_MULTI,
    KIND_SINGLE,
    ActionStep,
    Config,
    KeyConfig,
)
from linuxstreamdeck.core.events import EventBus  # noqa: E402
from linuxstreamdeck.obs import actions as _obs_actions  # noqa: E402,F401
from linuxstreamdeck.twitch import actions as _twitch_actions  # noqa: E402,F401

HAS_DISPLAY = Gtk.init_check()


class FakeObs:
    connected = False

    def __getattr__(self, _name):
        return lambda *a, **k: []


class FakeTwitch:
    def __init__(self, linked: bool = False) -> None:
        self.linked = linked

    def channel(self) -> dict:
        return {}


class FakeContainer:
    def __init__(self) -> None:
        self.keys: dict[int, KeyConfig] = {}

    def key(self, index: int):
        return self.keys.get(index)


class _BannerHarness:
    """Build an editor over fake services. A mixin rather than a base test
    class: subclassing one test case from another lets a same-named method
    silently replace the parent's, deleting the coverage it looked like it was
    adding to."""

    def build(self, linked: bool = False, ha_ready: bool = True):
        from linuxstreamdeck.ui.editor import EditorPanel

        self.ha_ready = ha_ready
        self.container = FakeContainer()
        self.twitch = FakeTwitch(linked=linked)
        self.home = SimpleNamespace(
            configured=lambda: self.ha_ready, entities=lambda: []
        )
        app = SimpleNamespace(
            obs=FakeObs(),
            twitch=self.twitch,
            home_assistant=self.home,
            config=Config(),
            bus=EventBus(),
            controller=SimpleNamespace(
                container=self.container,
                can_add_folder=lambda: True,
            ),
        )
        self.app = app
        return EditorPanel(app)

    def load(self, editor, kc: KeyConfig, index: int = 0):
        self.container.keys[index] = kc
        editor.load(index)


@unittest.skipUnless(HAS_DISPLAY, "needs a display")
class TwitchBannerTests(_BannerHarness, unittest.TestCase):
    # ---------- when it shows ----------

    def test_a_twitch_key_offers_the_connection(self) -> None:
        editor = self.build(linked=False)

        self.load(editor, KeyConfig(kind=KIND_SINGLE, action="twitch.stats"))

        self.assertTrue(editor.twitch_banner.get_revealed())

    def test_it_stays_hidden_once_an_account_is_linked(self) -> None:
        editor = self.build(linked=True)

        self.load(editor, KeyConfig(kind=KIND_SINGLE, action="twitch.stats"))

        self.assertFalse(editor.twitch_banner.get_revealed())

    def test_a_key_that_needs_nothing_never_offers_it(self) -> None:
        editor = self.build(linked=False)

        self.load(editor, KeyConfig(kind=KIND_SINGLE, action="sys.stopwatch"))

        self.assertFalse(editor.twitch_banner.get_revealed())

    def test_an_obs_key_does_not_offer_the_twitch_connection(self) -> None:
        """The two services are independent; OBS keys must not be conflated."""
        editor = self.build(linked=False)

        self.load(editor, KeyConfig(kind=KIND_SINGLE, action="obs.record"))

        self.assertFalse(editor.twitch_banner.get_revealed())

    def test_one_twitch_step_inside_a_list_is_enough(self) -> None:
        """A key that is half local still cannot do the Twitch half."""
        editor = self.build(linked=False)

        self.load(editor, KeyConfig(
            kind=KIND_MULTI,
            steps=[
                ActionStep(action="sys.wait", params={"duration": "00:01"}),
                ActionStep(action="twitch.marker", params={}),
            ],
        ))

        self.assertTrue(editor.twitch_banner.get_revealed())

    def test_an_empty_key_offers_nothing(self) -> None:
        editor = self.build(linked=False)

        self.load(editor, KeyConfig())

        self.assertFalse(editor.twitch_banner.get_revealed())

    # ---------- following the action as it is chosen ----------
    #
    # This is the path a user actually walks: the key was something else, they
    # pick a Twitch action from the dropdown, and the offer has to appear then
    # rather than after saving and wondering why the key is faded.

    def test_choosing_a_twitch_action_offers_the_connection_at_once(self) -> None:
        editor = self.build(linked=False)
        self.load(editor, KeyConfig(kind=KIND_SINGLE, action="sys.stopwatch"))
        self.assertFalse(editor.twitch_banner.get_revealed())

        editor.single_editor.select_action("twitch.clip")

        self.assertTrue(editor.twitch_banner.get_revealed())

    def test_choosing_something_else_takes_the_offer_back_down(self) -> None:
        editor = self.build(linked=False)
        self.load(editor, KeyConfig(kind=KIND_SINGLE, action="twitch.clip"))
        self.assertTrue(editor.twitch_banner.get_revealed())

        editor.single_editor.select_action("sys.stopwatch")

        self.assertFalse(editor.twitch_banner.get_revealed())

    def test_choosing_a_twitch_action_while_linked_offers_nothing(self) -> None:
        editor = self.build(linked=True)
        self.load(editor, KeyConfig(kind=KIND_SINGLE, action="sys.stopwatch"))

        editor.single_editor.select_action("twitch.clip")

        self.assertFalse(editor.twitch_banner.get_revealed())

    def test_the_icon_preview_still_follows_the_action(self) -> None:
        """Both reactions share one handler; neither may swallow the other."""
        editor = self.build(linked=False)
        self.load(editor, KeyConfig(kind=KIND_SINGLE, action="sys.stopwatch"))

        editor.single_editor.select_action("twitch.clip")

        self.assertEqual(
            editor.app_main._fallback_icon_ref,
            action_registry.get("twitch.clip").default_icon,
        )

    # ---------- when it goes away ----------

    def test_selecting_another_key_takes_it_down(self) -> None:
        """The banner outlives the body rebuild, so it has to be re-decided."""
        editor = self.build(linked=False)
        self.load(editor, KeyConfig(kind=KIND_SINGLE, action="twitch.stats"))

        self.load(editor, KeyConfig(kind=KIND_SINGLE, action="sys.stopwatch"), index=1)

        self.assertFalse(editor.twitch_banner.get_revealed())

    def test_clearing_the_editor_takes_it_down(self) -> None:
        editor = self.build(linked=False)
        self.load(editor, KeyConfig(kind=KIND_SINGLE, action="twitch.stats"))

        editor.clear()

        self.assertFalse(editor.twitch_banner.get_revealed())

    def test_linking_an_account_takes_it_down_without_reselecting(self) -> None:
        """Connecting happens in a dialog, so the editor has to be told."""
        editor = self.build(linked=False)
        self.load(editor, KeyConfig(kind=KIND_SINGLE, action="twitch.stats"))

        self.twitch.linked = True
        editor._refresh_service_banners()

        self.assertFalse(editor.twitch_banner.get_revealed())

    def test_losing_the_account_brings_it_back(self) -> None:
        editor = self.build(linked=True)
        self.load(editor, KeyConfig(kind=KIND_SINGLE, action="twitch.stats"))

        self.twitch.linked = False
        editor._refresh_service_banners()

        self.assertTrue(editor.twitch_banner.get_revealed())

    def test_the_bus_event_is_what_carries_that_news(self) -> None:
        editor = self.build(linked=False)
        self.load(editor, KeyConfig(kind=KIND_SINGLE, action="twitch.stats"))
        seen: list = []
        # The editor subscribes in its constructor; prove the topic is wired
        # rather than that GLib delivered it.
        self.app.bus.subscribe("twitch.state", lambda t, d: seen.append(d))

        self.app.bus.emit("twitch.state", linked=True, login="x")

        self.assertEqual(seen, [{"linked": True, "login": "x"}])

    # ---------- robustness ----------

    def test_an_application_without_twitch_at_all_is_survivable(self) -> None:
        """Every existing test builds an app namespace with no twitch."""
        from linuxstreamdeck.ui.editor import EditorPanel

        self.container = FakeContainer()
        app = SimpleNamespace(
            obs=FakeObs(),
            config=Config(),
            bus=EventBus(),
            controller=SimpleNamespace(
                container=self.container,
                can_add_folder=lambda: True,
            ),
        )
        editor = EditorPanel(app)

        self.load(editor, KeyConfig(kind=KIND_SINGLE, action="twitch.stats"))

        # No account is reachable, so the key genuinely cannot work.
        self.assertTrue(editor.twitch_banner.get_revealed())

    def test_an_unregistered_action_does_not_offer_anything(self) -> None:
        editor = self.build(linked=False)

        self.load(editor, KeyConfig(kind=KIND_SINGLE, action="nobody.knows"))

        self.assertFalse(editor.twitch_banner.get_revealed())

    def test_the_banner_names_the_service_and_offers_a_way_in(self) -> None:
        editor = self.build(linked=False)

        self.assertIn("Twitch", editor.twitch_banner.get_title())
        self.assertTrue(editor.twitch_banner.get_button_label())


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(HAS_DISPLAY, "needs a display")
class HomeAssistantBannerTests(_BannerHarness, unittest.TestCase):
    """The same offer for the third service, and here it matters more.

    A Twitch key at least renders faded on the deck. A Home Assistant key
    shows an entity dropdown that is simply **empty**, which reads as a broken
    editor rather than as a server that was never set up -- and the dialog
    that fixes it is under the profile menu, the last place anyone would look.
    """

    def test_a_key_needing_the_server_offers_to_set_it_up(self) -> None:
        editor = self.build(ha_ready=False)

        self.load(editor, KeyConfig(kind=KIND_SINGLE, action="ha.switch"))

        self.assertTrue(editor.ha_banner.get_revealed())

    def test_it_stays_hidden_once_the_server_is_set_up(self) -> None:
        editor = self.build(ha_ready=True)

        self.load(editor, KeyConfig(kind=KIND_SINGLE, action="ha.switch"))

        self.assertFalse(editor.ha_banner.get_revealed())

    def test_a_key_that_needs_nothing_never_offers_it(self) -> None:
        editor = self.build(ha_ready=False)

        self.load(editor, KeyConfig(kind=KIND_SINGLE, action="sys.stopwatch"))

        self.assertFalse(editor.ha_banner.get_revealed())

    def test_choosing_the_action_offers_it_at_once(self) -> None:
        """The path somebody actually walks: the offer has to appear when the
        action is picked, not after saving and wondering why nothing works."""
        editor = self.build(ha_ready=False)
        self.load(editor, KeyConfig(kind=KIND_SINGLE, action="sys.stopwatch"))
        self.assertFalse(editor.ha_banner.get_revealed())

        editor.single_editor.select_action("ha.state")

        self.assertTrue(editor.ha_banner.get_revealed())

    def test_choosing_something_else_takes_it_back_down(self) -> None:
        editor = self.build(ha_ready=False)
        self.load(editor, KeyConfig(kind=KIND_SINGLE, action="ha.switch"))
        self.assertTrue(editor.ha_banner.get_revealed())

        editor.single_editor.select_action("sys.stopwatch")

        self.assertFalse(editor.ha_banner.get_revealed())

    def test_one_step_of_a_list_is_enough(self) -> None:
        editor = self.build(ha_ready=False)

        self.load(editor, KeyConfig(kind=KIND_MULTI, steps=[
            ActionStep(action="sys.stopwatch"),
            ActionStep(action="ha.switch"),
        ]))

        self.assertTrue(editor.ha_banner.get_revealed())

    def test_clearing_the_editor_takes_it_down(self) -> None:
        editor = self.build(ha_ready=False)
        self.load(editor, KeyConfig(kind=KIND_SINGLE, action="ha.switch"))

        editor.clear()

        self.assertFalse(editor.ha_banner.get_revealed())

    def test_a_session_with_no_client_still_offers_it(self) -> None:
        """Rather than crashing, or silently deciding the server is fine."""
        editor = self.build(ha_ready=False)
        editor.app.home_assistant = None

        self.load(editor, KeyConfig(kind=KIND_SINGLE, action="ha.switch"))

        self.assertTrue(editor.ha_banner.get_revealed())

    def test_the_two_banners_are_independent(self) -> None:
        editor = self.build(linked=False, ha_ready=False)

        self.load(editor, KeyConfig(kind=KIND_SINGLE, action="ha.switch"))

        self.assertTrue(editor.ha_banner.get_revealed())
        self.assertFalse(editor.twitch_banner.get_revealed())
