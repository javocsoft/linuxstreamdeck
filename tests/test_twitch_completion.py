"""Live category suggestions under the editor's text field.

Twitch has tens of thousands of categories, so no dropdown could offer them and
free text meant a typo was only discovered when the key was pressed — live, on
air. Searching as the value is typed is the only shape that covers both.

Two things here are load-bearing and neither is about suggestions. The search
must never run on the GTK main thread, because each one is a request over the
network. And an answer to a query the field has already moved past must be
thrown away, or a slow reply replaces the current suggestions with older ones.
"""

from __future__ import annotations

import threading
import unittest

from linuxstreamdeck.core import actions as registry
from linuxstreamdeck.core.events import EventBus
from linuxstreamdeck.twitch import actions as _twitch_actions  # noqa: F401
from linuxstreamdeck.twitch import client as client_module
from linuxstreamdeck.twitch.client import TwitchClient
from linuxstreamdeck.twitch.http import TwitchError, TwitchHTTPError

try:  # The popup needs GTK, which a headless runner may not have.
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib, Gtk

    HAS_DISPLAY = Gtk.init_check()
except Exception:  # pragma: no cover - depends on the runner
    HAS_DISPLAY = False


class SearchTests(unittest.TestCase):
    """The client half: what a query asks Twitch and what it answers."""

    def setUp(self) -> None:
        self.answers: list = []
        self.requests: list[dict] = []
        original = client_module.request_json
        client_module.request_json = self._transport
        self.addCleanup(setattr, client_module, "request_json", original)
        self.client = TwitchClient(EventBus(), store=None, client_id="c")
        self.client._pool.shutdown(wait=False)
        self.client._tokens = self._tokens()
        self.addCleanup(self.client.stop)

    def _transport(self, method, url, **kwargs):
        self.requests.append({"method": method, "url": url, **kwargs})
        if not self.answers:
            raise AssertionError(f"Unexpected request to {url}")
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    @staticmethod
    def _tokens():
        from linuxstreamdeck.twitch import auth

        return auth.Tokens(
            access="a", refresh="r", expires_at=10**10, login="x", user_id="42"
        )

    def test_it_returns_the_matching_names(self) -> None:
        self.answers = [{"data": [
            {"id": "1", "name": "Just Chatting"},
            {"id": "2", "name": "Just Dance"},
        ]}]

        found = self.client.search_categories("just")

        self.assertEqual(
            [c.name for c in found], ["Just Chatting", "Just Dance"]
        )

    def test_it_asks_twitch_for_what_was_typed(self) -> None:
        self.answers = [{"data": []}]

        self.client.search_categories("doom")

        self.assertIn("search/categories", self.requests[0]["url"])
        self.assertEqual(self.requests[0]["params"]["query"], "doom")

    def test_an_empty_query_never_reaches_twitch(self) -> None:
        self.assertEqual(self.client.search_categories("   "), [])
        self.assertEqual(self.requests, [])

    def test_the_list_is_bounded(self) -> None:
        self.answers = [{"data": [
            {"id": str(i), "name": f"Game {i}"} for i in range(20)
        ]}]

        self.assertEqual(len(self.client.search_categories("game", limit=3)), 3)

    def test_the_box_art_url_has_its_size_filled_in(self) -> None:
        """Twitch leaves {width}x{height} in it, so an unsubstituted address
        fetches nothing at all."""
        self.answers = [{"data": [{
            "id": "1", "name": "Doom",
            "box_art_url": "https://static-cdn.jtvnw.net/x-{width}x{height}.jpg",
        }]}]

        found = self.client.search_categories("doom")

        self.assertNotIn("{width}", found[0].box_art_url)
        self.assertIn(str(client_module.BOX_ART_SIZE[0]), found[0].box_art_url)

    def test_a_category_without_artwork_is_still_offered(self) -> None:
        self.answers = [{"data": [{"id": "1", "name": "Doom"}]}]

        found = self.client.search_categories("doom")

        self.assertEqual(found[0].name, "Doom")
        self.assertEqual(found[0].box_art_url, "")

    def test_a_failure_answers_nothing_rather_than_raising(self) -> None:
        """A suggestion list that cannot be filled is a missing convenience,
        not something worth interrupting somebody's typing with."""
        self.answers = [TwitchError("network down")]

        self.assertEqual(self.client.search_categories("doom"), [])

    def test_a_refusal_answers_nothing_either(self) -> None:
        self.answers = [TwitchHTTPError(503, "unavailable")]

        self.assertEqual(self.client.search_categories("doom"), [])

    def test_the_same_query_is_not_searched_twice(self) -> None:
        """Correcting a typo walks back over prefixes already asked about."""
        self.answers = [{"data": [{"id": "1", "name": "Doom"}]}]

        self.client.search_categories("doom")
        self.client.search_categories("DOOM")

        self.assertEqual(len(self.requests), 1)

    def test_searching_primes_the_press_time_lookup(self) -> None:
        """Picking a suggestion should not cost a second request when the key
        is pressed, since the id was already in the answer."""
        self.answers = [{"data": [{"id": "7", "name": "Doom"}]}, {}]

        self.client.search_categories("doo")
        self.client.set_category("Doom")

        searches = [r for r in self.requests if "search/categories" in r["url"]]
        self.assertEqual(len(searches), 1)

    def test_the_cache_stays_bounded(self) -> None:
        for i in range(client_module.SEARCH_CACHE_LIMIT + 5):
            self.answers = [{"data": [{"id": str(i), "name": f"G{i}"}]}]
            self.client.search_categories(f"query{i}")

        self.assertLessEqual(
            len(self.client._searches), client_module.SEARCH_CACHE_LIMIT
        )


class BoxArtTests(unittest.TestCase):
    """The pictures beside each suggestion, and where they may come from."""

    def setUp(self) -> None:
        self.fetched: list[str] = []
        self.answer: object = b"PNGDATA"
        original = client_module.request_bytes
        client_module.request_bytes = self._fetch
        self.addCleanup(setattr, client_module, "request_bytes", original)
        self.client = TwitchClient(EventBus(), store=None, client_id="c")
        self.client._pool.shutdown(wait=False)
        self.addCleanup(self.client.stop)

    def _fetch(self, url, **_kwargs):
        self.fetched.append(url)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer

    def test_it_returns_the_image(self) -> None:
        self.assertEqual(self.client.box_art("https://cdn/x.jpg"), b"PNGDATA")

    def test_the_same_address_is_fetched_once(self) -> None:
        """The same handful of categories come back on every prefix typed."""
        self.client.box_art("https://cdn/x.jpg")
        self.client.box_art("https://cdn/x.jpg")

        self.assertEqual(len(self.fetched), 1)

    def test_a_failure_answers_none_and_is_not_retried(self) -> None:
        """A row without its picture is still a usable suggestion."""
        self.answer = TwitchError("no")

        self.assertIsNone(self.client.box_art("https://cdn/x.jpg"))
        self.assertIsNone(self.client.box_art("https://cdn/x.jpg"))
        self.assertEqual(len(self.fetched), 1)

    def test_an_empty_address_asks_for_nothing(self) -> None:
        self.assertIsNone(self.client.box_art(""))
        self.assertEqual(self.fetched, [])

    def test_the_cache_stays_bounded(self) -> None:
        for i in range(client_module.ART_CACHE_LIMIT + 5):
            self.client.box_art(f"https://cdn/{i}.jpg")

        self.assertLessEqual(
            len(self.client._art), client_module.ART_CACHE_LIMIT
        )


class AssetHostTests(unittest.TestCase):
    """An image address arrives inside an API response, so it is data from
    outside rather than a URL this application chose."""

    def test_twitch_hosts_are_accepted(self) -> None:
        from linuxstreamdeck.twitch.http import _is_twitch_asset

        for url in (
            "https://static-cdn.jtvnw.net/ttv-boxart/1-40x53.jpg",
            "https://jtvnw.net/x.jpg",
        ):
            with self.subTest(url=url):
                self.assertTrue(_is_twitch_asset(url))

    def test_anywhere_else_is_refused(self) -> None:
        from linuxstreamdeck.twitch.http import _is_twitch_asset

        for url in (
            "https://evil.example/x.jpg",
            "https://jtvnw.net.evil.example/x.jpg",
            "http://static-cdn.jtvnw.net/x.jpg",   # plain HTTP
            "file:///etc/passwd",
            "",
            "not a url",
        ):
            with self.subTest(url=url):
                self.assertFalse(_is_twitch_asset(url))

    def test_fetching_from_elsewhere_opens_nothing_at_all(self) -> None:
        """Reaching out and failing raises the same error as refusing, so the
        opener itself has to be watched: the point is that nothing is
        contacted, not that an error comes back."""
        from linuxstreamdeck.twitch import http

        opened: list[str] = []

        def spy(request, **_kwargs):
            opened.append(getattr(request, "full_url", str(request)))
            raise AssertionError("should never be reached")

        original = http.urlopen
        http.urlopen = spy
        self.addCleanup(setattr, http, "urlopen", original)

        with self.assertRaises(TwitchError):
            http.request_bytes("https://evil.example/x.jpg")

        self.assertEqual(opened, [])


@unittest.skipUnless(HAS_DISPLAY, "needs a display")
class SettledValueTests(unittest.TestCase):
    """Only a value the service recognises is worth storing.

    Free text that happens to look like a category is the worst case: the key
    saves cleanly, looks configured, and fails the first time it is pressed —
    which for this action means live, on air. Saving nothing instead makes the
    key plainly unconfigured.

    The other half matters just as much. A value that cannot be judged must
    never be cleared, or opening a working key and saving it would empty it.
    """

    def setUp(self) -> None:
        from linuxstreamdeck.ui import steps

        self.steps = steps
        self.entry = Gtk.Entry()
        self.window = Gtk.Window()
        self.window.set_child(self.entry)
        self.addCleanup(self.window.destroy)

    def _popup(self, text: str = ""):
        self.entry.set_text(text)
        popup = self.steps._CompletionPopup(
            self.entry, lambda _t: [], artwork=None
        )
        self.addCleanup(popup.close)
        self.entry._completion = popup
        return popup

    @staticmethod
    def _suggestion(name: str):
        from linuxstreamdeck.twitch.client import CategorySuggestion

        return CategorySuggestion(name, "")

    def test_a_chosen_suggestion_is_stored(self) -> None:
        popup = self._popup()

        popup._choose("Just Chatting")

        self.assertEqual(popup.settled_value(), "Just Chatting")

    def test_typed_text_the_service_never_offered_is_stored_as_nothing(
        self,
    ) -> None:
        popup = self._popup()
        popup._fill([self._suggestion("Detroit")])

        self.entry.set_text("not a real game at all")

        self.assertEqual(popup.settled_value(), "")

    def test_a_name_that_was_offered_counts_even_if_it_was_typed_out(
        self,
    ) -> None:
        """Someone who types the whole name correctly has not done anything
        wrong, and the list showed it to them."""
        popup = self._popup()
        popup._fill([self._suggestion("Detroit: Become Human")])

        self.entry.set_text("Detroit: Become Human")

        self.assertEqual(popup.settled_value(), "Detroit: Become Human")

    def test_it_stores_the_name_as_the_service_spells_it(self) -> None:
        """So the configuration holds a real category rather than a near miss
        that only happens to resolve."""
        popup = self._popup()
        popup._fill([self._suggestion("Grand Theft Auto V")])

        self.entry.set_text("grand theft auto v")

        self.assertEqual(popup.settled_value(), "Grand Theft Auto V")

    def test_a_value_the_field_opened_with_is_kept(self) -> None:
        """Opening a working key and saving it must not empty it, and nothing
        here can judge a value nobody searched for."""
        popup = self._popup("Software and Game Development")

        self.assertEqual(
            popup.settled_value(), "Software and Game Development"
        )

    def test_an_empty_field_stays_empty(self) -> None:
        popup = self._popup()

        self.assertEqual(popup.settled_value(), "")

    def test_the_editor_stores_the_settled_value(self) -> None:
        """The whole point: what reaches the key is what the service knows."""
        from linuxstreamdeck.core.actions import Param
        from linuxstreamdeck.ui.steps import StepEditor

        popup = self._popup()
        popup._fill([self._suggestion("Detroit")])
        self.entry.set_text("garbage")
        param = Param("category", "Category", completion_source="twitch_categories")

        self.assertEqual(StepEditor._widget_value(param, self.entry), "")

        self.entry.set_text("Detroit")

        self.assertEqual(StepEditor._widget_value(param, self.entry), "Detroit")

    def test_a_field_without_suggestions_keeps_whatever_was_typed(self) -> None:
        """With no account there is nothing to check against, and unknown is
        not the same as wrong."""
        from linuxstreamdeck.core.actions import Param
        from linuxstreamdeck.ui.steps import StepEditor

        plain = Gtk.Entry(text="Something typed by hand")
        param = Param("category", "Category", completion_source="twitch_categories")

        self.assertEqual(
            StepEditor._widget_value(param, plain), "Something typed by hand"
        )

    def test_unrecognised_text_is_marked_while_typing(self) -> None:
        """Said before the key is saved rather than after it fails on air."""
        popup = self._popup()
        self.entry.set_text("not a real game")

        popup._fill([])

        self.assertTrue(self.entry.has_css_class("unsettled"))

    def test_choosing_a_suggestion_clears_the_mark(self) -> None:
        popup = self._popup()
        self.entry.set_text("not a real game")
        popup._fill([])
        self.assertTrue(self.entry.has_css_class("unsettled"))

        popup._choose("Detroit")

        self.assertFalse(self.entry.has_css_class("unsettled"))

    def test_an_empty_field_is_not_marked_as_wrong(self) -> None:
        """Unfinished is not the same as incorrect."""
        popup = self._popup()
        self.entry.set_text("not a real game")
        popup._fill([])

        self.entry.set_text("")

        self.assertFalse(self.entry.has_css_class("unsettled"))


class ParameterTests(unittest.TestCase):
    def test_the_category_parameter_declares_a_completion_source(self) -> None:
        action = registry.get("twitch.set_category")
        param = next(p for p in action.params if p.name == "category")

        self.assertEqual(param.completion_source, "twitch_categories")

    def test_it_stays_free_text_so_existing_keys_still_load(self) -> None:
        """The suggestions make a real name easy to type; they do not turn the
        stored value into something a saved key would not recognise."""
        action = registry.get("twitch.set_category")
        param = next(p for p in action.params if p.name == "category")

        self.assertEqual(param.kind, "string")
        self.assertEqual(param.choices, [])


@unittest.skipUnless(HAS_DISPLAY, "needs a display")
class PopupTests(unittest.TestCase):
    """The editor half: debouncing, threading and discarding stale answers."""

    def setUp(self) -> None:
        from linuxstreamdeck.ui import steps

        self.steps = steps
        self.entry = Gtk.Entry()
        self.window = Gtk.Window()
        self.window.set_child(self.entry)
        self.addCleanup(self.window.destroy)
        self.queries: list[str] = []
        self.threads: list[str] = []
        self.release = threading.Event()
        self.release.set()
        self.answers: dict[str, list[str]] = {}

    def _search(self, text: str) -> list[str]:
        self.queries.append(text)
        self.threads.append(threading.current_thread().name)
        self.release.wait(timeout=5)
        return self.answers.get(text, [])

    def _popup(self):
        popup = self.steps._CompletionPopup(self.entry, self._search)
        self.addCleanup(popup.close)
        return popup

    @staticmethod
    def _pump(rounds: int = 60) -> None:
        context = GLib.MainContext.default()
        for _ in range(rounds):
            while context.pending():
                context.iteration(False)

    def _settle(self, seconds: float = 1.5) -> None:
        import time

        deadline = time.monotonic() + seconds
        context = GLib.MainContext.default()
        while time.monotonic() < deadline:
            while context.pending():
                context.iteration(False)
            time.sleep(0.01)

    def test_a_short_query_asks_nothing(self) -> None:
        """One character matches most of the catalogue and suggests nothing."""
        self._popup()

        self.entry.set_text("d")
        self._settle(0.8)

        self.assertEqual(self.queries, [])

    def test_typing_eventually_searches(self) -> None:
        self._popup()
        self.answers["doom"] = ["Doom", "Doom Eternal"]

        self.entry.set_text("doom")
        self._settle()

        self.assertEqual(self.queries, ["doom"])

    def test_the_search_never_runs_on_the_gtk_thread(self) -> None:
        """Each one is a request over the network; on the main thread it would
        freeze the editor for the round trip."""
        self._popup()
        self.answers["doom"] = ["Doom"]

        self.entry.set_text("doom")
        self._settle()

        self.assertTrue(self.threads)
        self.assertNotIn(threading.main_thread().name, self.threads)

    def test_fast_typing_searches_once_for_the_final_text(self) -> None:
        """The debounce is what keeps a rate limit from being spent on
        prefixes nobody meant to search for."""
        self._popup()
        self.answers["doom"] = ["Doom"]

        for text in ("do", "doo", "doom"):
            self.entry.set_text(text)
            self._pump()
        self._settle()

        self.assertEqual(self.queries, ["doom"])

    def test_clearing_the_field_closes_the_suggestions(self) -> None:
        popup = self._popup()
        self.answers["doom"] = ["Doom"]
        self.entry.set_text("doom")
        self._settle()

        self.entry.set_text("")
        self._pump()

        self.assertFalse(popup._popover.get_visible())

    def test_a_stale_answer_is_discarded(self) -> None:
        """A slow reply must not replace the current suggestions with older
        ones for a query the field has already moved past."""
        popup = self._popup()
        shown: list[list[str]] = []
        popup._fill = shown.append

        popup._show(["Old result"], popup._generation - 1, "doom")

        self.assertEqual(shown, [])

    def test_an_answer_for_different_text_is_discarded(self) -> None:
        popup = self._popup()
        shown: list[list[str]] = []
        popup._fill = shown.append
        self.entry.set_text("something else")

        popup._show(["Doom"], popup._generation, "doom")

        self.assertEqual(shown, [])

    def test_the_matching_answer_is_shown(self) -> None:
        popup = self._popup()
        shown: list[list[str]] = []
        popup._fill = shown.append
        self.entry.set_text("doom")

        popup._show(["Doom"], popup._generation, "doom")

        self.assertEqual(shown, [["Doom"]])

    def test_closing_invalidates_anything_still_in_flight(self) -> None:
        popup = self._popup()
        shown: list[list[str]] = []
        generation = popup._generation
        self.entry.set_text("doom")

        popup.close()
        popup._fill = shown.append
        popup._show(["Doom"], generation, "doom")

        self.assertEqual(shown, [])

    def test_closing_twice_is_harmless(self) -> None:
        popup = self._popup()

        popup.close()
        popup.close()

    def test_choosing_a_suggestion_fills_the_field(self) -> None:
        popup = self._popup()

        popup._choose("Grand Theft Auto V")

        self.assertEqual(self.entry.get_text(), "Grand Theft Auto V")
        self.assertFalse(popup._popover.get_visible())

    def test_choosing_does_not_reopen_the_list_under_the_new_text(self) -> None:
        """Filling the field fires `changed`, which would schedule a search for
        the very name just chosen. Picking a suggestion has to be final."""
        popup = self._popup()
        self.answers["Doom"] = ["Doom", "Doom Eternal"]

        popup._choose("Doom")
        self._settle()

        self.assertEqual(self.queries, [])
        self.assertFalse(popup._popover.get_visible())

    def test_a_suggestion_is_taken_on_the_press_not_on_the_click(self) -> None:
        """`clicked` needs press and release on a widget that is still mapped,
        and pressing moves the focus off the entry, which closes this popover.
        The release then landed on a widget that had gone, and the suggestion
        was silently never applied.
        """
        popup = self._popup()
        popup._fill(["Detroit"])
        row = popup._list.get_first_child()

        gestures = [
            controller
            for controller in row.observe_controllers()
            if isinstance(controller, Gtk.GestureClick)
        ]

        self.assertTrue(gestures, "the row acts on no press gesture")
        self.assertEqual(
            gestures[0].get_propagation_phase(), Gtk.PropagationPhase.CAPTURE
        )

    def test_a_row_shows_its_artwork_slot_and_its_name(self) -> None:
        popup = self.steps._CompletionPopup(
            self.entry, self._search, artwork=lambda _url: None
        )
        self.addCleanup(popup.close)
        from linuxstreamdeck.twitch.client import CategorySuggestion

        popup._fill([CategorySuggestion("Detroit", "https://cdn/x.jpg")])
        row = popup._list.get_first_child()
        children = []
        child = row.get_child().get_first_child()
        while child is not None:
            children.append(child)
            child = child.get_next_sibling()

        self.assertIsInstance(children[0], Gtk.Picture)
        self.assertIsInstance(children[-1], Gtk.Label)
        self.assertEqual(children[-1].get_label(), "Detroit")

    def test_the_artwork_slot_is_reserved_before_the_picture_arrives(
        self,
    ) -> None:
        """A late or missing picture must not make the rows jump as the list
        fills."""
        popup = self.steps._CompletionPopup(
            self.entry, self._search, artwork=lambda _url: None
        )
        self.addCleanup(popup.close)
        from linuxstreamdeck.twitch.client import CategorySuggestion

        popup._fill([CategorySuggestion("Detroit", "https://cdn/x.jpg")])
        picture = popup._list.get_first_child().get_child().get_first_child()

        request = picture.get_size_request()

        self.assertEqual(
            (request.width, request.height), self.steps.COMPLETION_ART_SIZE
        )
        self.assertIsNone(picture.get_paintable())

    def test_artwork_arriving_for_a_replaced_list_is_dropped(self) -> None:
        """A picture that arrives after the typing moved on belongs to rows
        that are no longer on screen."""
        popup = self.steps._CompletionPopup(
            self.entry, self._search, artwork=lambda _url: None
        )
        self.addCleanup(popup.close)
        picture = Gtk.Picture()

        applied = popup._apply_artwork(
            picture, b"x", popup._fill_generation - 1
        )

        self.assertFalse(applied)
        self.assertIsNone(picture.get_paintable())

    def test_artwork_for_an_unparented_row_is_dropped(self) -> None:
        popup = self.steps._CompletionPopup(
            self.entry, self._search, artwork=lambda _url: None
        )
        self.addCleanup(popup.close)
        orphan = Gtk.Picture()

        popup._apply_artwork(orphan, b"x", popup._fill_generation)

        self.assertIsNone(orphan.get_paintable())

    def test_unreadable_artwork_leaves_the_row_alone(self) -> None:
        popup = self.steps._CompletionPopup(
            self.entry, self._search, artwork=lambda _url: None
        )
        self.addCleanup(popup.close)
        from linuxstreamdeck.twitch.client import CategorySuggestion

        popup._fill([CategorySuggestion("Detroit", "https://cdn/x.jpg")])
        picture = popup._list.get_first_child().get_child().get_first_child()

        popup._apply_artwork(
            picture, b"not an image", popup._fill_generation
        )

        self.assertIsNone(picture.get_paintable())

    def test_closing_does_not_throw_away_artwork_for_rows_still_there(
        self,
    ) -> None:
        """The search counter and the row counter answer different questions.

        Sharing one meant every close — and one fires whenever the field loses
        focus — discarded the pictures of rows that were still on screen, so
        the list showed empty placeholders and never filled in.
        """
        import io

        from PIL import Image

        from linuxstreamdeck.twitch.client import CategorySuggestion

        buf = io.BytesIO()
        Image.new("RGB", (40, 53), (150, 40, 60)).save(buf, "PNG")
        popup = self.steps._CompletionPopup(
            self.entry, self._search, artwork=lambda _url: buf.getvalue()
        )
        self.addCleanup(popup.close)
        # Force the two counters apart. Left equal, this test passes
        # whichever one the code happens to consult, which is exactly how the
        # bug survived being written about.
        for _ in range(3):
            popup.close()
        popup._fill([CategorySuggestion("Detroit", "https://cdn/a")])
        picture = popup._list.get_first_child().get_child().get_first_child()
        generation = popup._fill_generation
        self.assertNotEqual(generation, popup._generation)

        popup.close()
        popup._apply_artwork(picture, buf.getvalue(), generation)

        self.assertIsNotNone(picture.get_paintable())

    def test_replacing_the_list_does_throw_it_away(self) -> None:
        import io

        from PIL import Image

        from linuxstreamdeck.twitch.client import CategorySuggestion

        buf = io.BytesIO()
        Image.new("RGB", (40, 53), (10, 20, 30)).save(buf, "PNG")
        popup = self.steps._CompletionPopup(
            self.entry, self._search, artwork=lambda _url: None
        )
        self.addCleanup(popup.close)
        popup._fill([CategorySuggestion("Detroit", "https://cdn/a")])
        picture = popup._list.get_first_child().get_child().get_first_child()
        generation = popup._fill_generation

        popup._fill([CategorySuggestion("Something else", "https://cdn/b")])
        # Real image bytes: the only thing left that can refuse them is the
        # counter, so the rejection means what this test says it means.
        popup._apply_artwork(picture, buf.getvalue(), generation)

        self.assertIsNone(picture.get_paintable())

    def test_replacing_the_list_stops_the_worker_fetching_the_rest(self) -> None:
        """The counter's real job in the worker is to stop downloading.

        Rejecting a picture when it arrives is too late: by then it has already
        been fetched for a row that is gone. The worker checks between each
        one, so typing on abandons the rest of the previous list's images.
        """
        from linuxstreamdeck.twitch.client import CategorySuggestion

        fetched: list[str] = []
        popup = self.steps._CompletionPopup(
            self.entry, self._search, artwork=fetched.append
        )
        self.addCleanup(popup.close)
        # No addresses, so filling starts no worker of its own to race with.
        popup._fill([CategorySuggestion("First", "")])
        generation = popup._fill_generation
        popup._fill([CategorySuggestion("Second", "")])

        popup._artwork_work(
            [(f"https://cdn/{n}", Gtk.Picture()) for n in "abc"], generation
        )

        self.assertEqual(fetched, [])

    def test_the_worker_fetches_for_the_list_that_is_current(self) -> None:
        from linuxstreamdeck.twitch.client import CategorySuggestion

        fetched: list[str] = []
        popup = self.steps._CompletionPopup(
            self.entry, self._search, artwork=fetched.append
        )
        self.addCleanup(popup.close)
        popup._fill([CategorySuggestion("First", "")])

        popup._artwork_work(
            [("https://cdn/a", Gtk.Picture())], popup._fill_generation
        )

        self.assertEqual(fetched, ["https://cdn/a"])

    def test_a_suggestion_with_no_artwork_still_gets_a_row(self) -> None:
        popup = self.steps._CompletionPopup(
            self.entry, self._search, artwork=lambda _url: None
        )
        self.addCleanup(popup.close)
        from linuxstreamdeck.twitch.client import CategorySuggestion

        popup._fill([CategorySuggestion("Detroit", "")])

        self.assertIsNotNone(popup._list.get_first_child())

    def test_plain_strings_still_build_rows(self) -> None:
        """Any other completion source supplies names, not categories."""
        popup = self._popup()

        popup._fill(["Something"])
        row = popup._list.get_first_child()

        self.assertIsNotNone(row)

    def test_no_suggestion_row_relies_on_a_clicked_signal(self) -> None:
        popup = self._popup()
        popup._fill(["Detroit"])
        row = popup._list.get_first_child()

        self.assertNotIsInstance(row.get_child(), Gtk.Button)

    def test_leaving_the_field_for_the_list_keeps_it_open(self) -> None:
        """Closing on any focus loss takes the list out from under the
        pointer halfway through choosing from it."""
        popup = self._popup()
        popup._fill(["Detroit"])
        closed: list[bool] = []
        popup.close = lambda: closed.append(True)
        popup._focus_is_inside = lambda: True

        popup._on_focus_changed()

        self.assertEqual(closed, [])

    def test_leaving_the_field_for_anything_else_closes_it(self) -> None:
        popup = self._popup()
        popup._fill(["Detroit"])
        closed: list[bool] = []
        popup.close = lambda: closed.append(True)
        popup._focus_is_inside = lambda: False

        popup._on_focus_changed()

        self.assertEqual(closed, [True])

    def test_the_list_is_placed_under_the_field_and_matches_its_width(
        self,
    ) -> None:
        """Left by default a popover centres itself on the field, which reads
        as a floating window rather than as a list belonging to that line."""
        popup = self._popup()
        self.window.set_default_size(620, 200)
        self.window.present()
        self._pump()
        popup._fill(["Detroit"])

        found, rect = popup._popover.get_pointing_to()
        width = self.entry.get_width()

        self.assertEqual(popup._popover.get_halign(), Gtk.Align.START)
        if width > 0:
            self.assertTrue(found)
            self.assertEqual(rect.x, 0)
            self.assertEqual(rect.width, width)
            self.assertEqual(rect.y, self.entry.get_height())


@unittest.skipUnless(HAS_DISPLAY, "needs a display")
class AttachmentTests(unittest.TestCase):
    """When the editor gives a field suggestions at all."""

    def _editor(self, twitch):
        from types import SimpleNamespace

        from linuxstreamdeck.core.config import Config
        from linuxstreamdeck.ui.steps import StepEditor

        app = SimpleNamespace(
            obs=SimpleNamespace(connected=False),
            twitch=twitch,
            config=Config(),
            bus=EventBus(),
        )
        return StepEditor(app)

    def test_a_linked_account_supplies_the_search(self) -> None:
        from types import SimpleNamespace

        editor = self._editor(
            SimpleNamespace(linked=True, search_categories=lambda t, n: ["Doom"])
        )

        search = editor._completion_search("twitch_categories")

        self.assertIsNotNone(search)
        self.assertEqual(search("doom"), ["Doom"])

    def test_without_an_account_the_field_stays_plain(self) -> None:
        """A field that can never suggest anything should not pretend to."""
        from types import SimpleNamespace

        editor = self._editor(SimpleNamespace(linked=False))

        self.assertIsNone(editor._completion_search("twitch_categories"))

    def test_an_application_without_twitch_is_survivable(self) -> None:
        editor = self._editor(None)

        self.assertIsNone(editor._completion_search("twitch_categories"))

    def test_an_unknown_source_is_ignored(self) -> None:
        from types import SimpleNamespace

        editor = self._editor(SimpleNamespace(linked=True))

        self.assertIsNone(editor._completion_search("something_else"))


if __name__ == "__main__":
    unittest.main()
