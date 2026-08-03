"""What is playing, on a key.

Every fixture here is real `playerctl 2.4` output captured from VLC, because
the two things worth pinning are both about its exact shape: the separator
must be something a title cannot contain, and a metadata key the player does
not publish renders as *empty* rather than as an error or as the template.
"""

from __future__ import annotations

import subprocess
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace

from linuxstreamdeck import basic_actions
from linuxstreamdeck.core import nowplaying
from linuxstreamdeck.core.actions import REGISTRY

SEP = nowplaying.SEPARATOR
# Captured from VLC: playing, with no cover published.
NO_ART = f"Playing{SEP}Pink Floyd{SEP}Wish You Were Here{SEP}\n"
# The same track once VLC had extracted the embedded cover to its own cache.
WITH_ART = (
    f"Playing{SEP}Pink Floyd{SEP}Wish You Were Here{SEP}"
    "file:///home/javier/.cache/vlc/art/artistalbum/Pink%20Floyd/cover.jpg\n"
)
PAUSED = f"Paused{SEP}Pink Floyd{SEP}Wish You Were Here{SEP}\n"


class ReadingTestCase(unittest.TestCase):
    def use(self, stdout: str = NO_ART, code: int = 0, missing: bool = False):
        self.runs: list = []

        def run(argv, **_kwargs):
            self.runs.append(argv)
            return subprocess.CompletedProcess(argv, code, stdout, "")

        for target, value in (
            ("shutil.which", lambda _n: None if missing else "/usr/bin/playerctl"),
            ("subprocess.run", run),
        ):
            patch = unittest.mock.patch(
                f"linuxstreamdeck.core.nowplaying.{target}", value
            )
            patch.start()
            self.addCleanup(patch.stop)
        nowplaying.forget()
        self.addCleanup(nowplaying.forget)

    def setUp(self) -> None:
        self.use()


class ParseTests(unittest.TestCase):
    def test_it_reads_what_playerctl_prints(self) -> None:
        track = nowplaying.parse(NO_ART)

        self.assertEqual(track.status, "Playing")
        self.assertEqual(track.artist, "Pink Floyd")
        self.assertEqual(track.title, "Wish You Were Here")
        self.assertTrue(track.playing)

    def test_a_cover_the_player_published_is_read(self) -> None:
        self.assertTrue(nowplaying.parse(WITH_ART).art_url.startswith("file://"))

    def test_a_key_the_player_does_not_publish_is_simply_empty(self) -> None:
        """Verified against playerctl 2.4: a missing `mpris:artUrl` renders
        as nothing, not as an error and not as the template itself."""
        self.assertEqual(nowplaying.parse(NO_ART).art_url, "")

    def test_paused_is_not_playing(self) -> None:
        track = nowplaying.parse(PAUSED)

        self.assertFalse(track.playing)
        self.assertEqual(track.caption, "Pink Floyd")

    def test_the_separator_cannot_appear_in_a_title(self) -> None:
        """A punctuation character would eventually land inside somebody's
        song title and split it in half."""
        self.assertEqual(nowplaying.SEPARATOR, "\x1f")
        self.assertFalse(nowplaying.SEPARATOR.isprintable())

    def test_a_stream_with_no_artist_falls_back_to_the_title(self) -> None:
        """A radio station or a podcast often publishes no artist, and an
        empty key would be worse than a long one."""
        track = nowplaying.parse(f"Playing{SEP}{SEP}Some Long Show Name{SEP}")

        self.assertEqual(track.caption, "Some Long Show Name")

    def test_nothing_playing_is_nothing(self) -> None:
        for text in ("", "\n", f"{SEP}{SEP}{SEP}", "   "):
            with self.subTest(text=text):
                self.assertIsNone(nowplaying.parse(text))

    def test_rubbish_never_raises(self) -> None:
        for text in ("garbage", f"Playing{SEP}only two", None):
            with self.subTest(text=text):
                nowplaying.parse(text)


class ReadingTests(ReadingTestCase):
    def test_it_asks_playerctl_for_one_line(self) -> None:
        nowplaying.current()

        self.assertEqual(
            self.runs[0], ["playerctl", "metadata", "--format", nowplaying.FORMAT]
        )

    def test_one_reading_serves_every_key(self) -> None:
        """feedback() runs on the single render worker and each reading is a
        process: a page of media keys must not spawn one each."""
        for _ in range(15):
            nowplaying.current(now=1000.0)

        self.assertEqual(len(self.runs), 1)

    def test_a_stale_reading_is_replaced(self) -> None:
        nowplaying.current(now=1000.0)

        nowplaying.current(now=1000.0 + nowplaying.STATE_TTL + 0.1)

        self.assertEqual(len(self.runs), 2)

    def test_no_player_running_is_not_a_failure(self) -> None:
        """`playerctl` exits non-zero with "No players found", which is the
        ordinary answer when nothing is playing."""
        self.use(stdout="No players found\n", code=1)

        self.assertIsNone(nowplaying.current())

    def test_without_playerctl_nothing_is_asked(self) -> None:
        self.use(missing=True)

        self.assertIsNone(nowplaying.current())
        self.assertEqual(self.runs, [])

    def test_a_crashing_playerctl_never_raises(self) -> None:
        def explode(*_a, **_k):
            raise OSError("boom")

        with unittest.mock.patch(
            "linuxstreamdeck.core.nowplaying.subprocess.run", explode
        ):
            self.assertIsNone(nowplaying.current())


class ArtworkTests(ReadingTestCase):
    def setUp(self) -> None:
        super().setUp()
        import tempfile

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cover = Path(self.temp.name) / "cover art.jpg"
        self.cover.write_bytes(b"\xff\xd8jpeg-bytes")
        self.jobs: list = []
        patch = unittest.mock.patch.object(
            nowplaying.webrequest, "background",
            lambda work, *args: (self.jobs.append((work, args)), True)[1],
        )
        patch.start()
        self.addCleanup(patch.stop)

    def test_a_local_cover_is_read_at_once(self) -> None:
        """It is local, it is the same read `compose()` already does for a
        custom icon, and doing it in the background would leave the key blank
        for a refresh on every track change."""
        data = nowplaying.artwork(self.cover.as_uri())

        self.assertEqual(data, b"\xff\xd8jpeg-bytes")
        self.assertEqual(self.jobs, [])

    def test_an_escaped_path_is_unescaped(self) -> None:
        """VLC writes its cache path with %20 for a space."""
        url = "file://" + str(self.cover).replace(" ", "%20")

        self.assertIsNotNone(nowplaying.artwork(url))

    def test_a_remote_cover_never_waits_on_the_network(self) -> None:
        """Spotify publishes an https address, and feedback() runs on the
        single render worker."""
        def explode(*_a, **_k):
            raise AssertionError("artwork fetched on the calling thread")

        with unittest.mock.patch.object(
            nowplaying.webrequest, "fetch_bytes", explode
        ):
            self.assertIsNone(
                nowplaying.artwork("https://i.scdn.co/image/abc")
            )
        self.assertEqual(len(self.jobs), 1)

    def test_a_burst_of_repaints_is_one_fetch(self) -> None:
        for _ in range(20):
            nowplaying.artwork("https://i.scdn.co/image/abc")

        self.assertEqual(len(self.jobs), 1)

    def test_a_scheme_that_is_not_a_picture_is_refused_once(self) -> None:
        """And remembered as "no cover", so it is not examined again on every
        repaint."""
        for scheme in ("data:image/png;base64,AAAA", "ftp://x/y", "/etc/passwd"):
            with self.subTest(scheme=scheme):
                self.assertIsNone(nowplaying.artwork(scheme))
        self.assertEqual(self.jobs, [])

    def test_no_cover_asks_for_nothing(self) -> None:
        self.assertIsNone(nowplaying.artwork(""))
        self.assertEqual(self.jobs, [])

    def test_an_oversized_local_cover_is_refused(self) -> None:
        big = Path(self.temp.name) / "big.jpg"
        big.write_bytes(b"x" * (nowplaying.MAX_ART_BYTES + 1))

        self.assertIsNone(nowplaying.artwork(big.as_uri()))

    def test_a_missing_file_is_not_an_error(self) -> None:
        gone = Path(self.temp.name) / "gone.jpg"

        self.assertIsNone(nowplaying.artwork(gone.as_uri()))

    def test_the_cache_is_bounded(self) -> None:
        """Every track played in a session would otherwise be kept for ever."""
        for index in range(nowplaying.ART_CACHE_LIMIT + 5):
            nowplaying.artwork(f"ftp://cover/{index}")

        self.assertLessEqual(len(nowplaying._art), nowplaying.ART_CACHE_LIMIT)

    def test_a_fetch_that_fails_releases_its_mark(self) -> None:
        """Otherwise that cover is never asked for again. The mark has to be
        set first, or the test passes without the release doing anything."""
        address = "https://i.scdn.co/image/abc"
        nowplaying._art_pending.add(address)

        with unittest.mock.patch.object(
            nowplaying.webrequest, "fetch_bytes", side_effect=RuntimeError("x")
        ):
            nowplaying._fetch_art(address)

        self.assertNotIn(address, nowplaying._art_pending)


class ActionTests(ReadingTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.action = REGISTRY["sys.media"]
        self.ctx = SimpleNamespace(
            bus=SimpleNamespace(emit=lambda *a, **k: None)
        )

    def test_it_is_off_by_default(self) -> None:
        """A plain transport key must cost nothing at all."""
        self.assertEqual(self.action.feedback(self.ctx, {}), {})
        self.assertEqual(self.runs, [])

    def test_the_artist_becomes_the_label(self) -> None:
        """The title does not fit: measured at 96 px it wraps to two lines and
        is cut, and centered it is drawn huge or unreadably small depending on
        its length."""
        state = self.action.feedback(self.ctx, {"show": "yes"})

        self.assertEqual(state["label"], "Pink Floyd")

    def test_a_playing_track_marks_the_key(self) -> None:
        self.assertTrue(self.action.feedback(self.ctx, {"show": "yes"})["active"])

    def test_a_paused_track_does_not(self) -> None:
        self.use(stdout=PAUSED)

        self.assertFalse(self.action.feedback(self.ctx, {"show": "yes"})["active"])

    def test_nothing_playing_gives_the_key_back(self) -> None:
        """Its own icon and its own label, exactly as it was configured --
        rather than a blank or a stale artist."""
        self.use(stdout="No players found\n", code=1)

        self.assertEqual(self.action.feedback(self.ctx, {"show": "yes"}), {})

    def test_a_player_with_no_metadata_gives_the_key_back_too(self) -> None:
        """A stream that has just started is playing and publishes neither an
        artist nor a title. Replacing the label with nothing would leave the
        key blank rather than showing what it was configured as."""
        self.use(stdout=f"Playing{SEP}{SEP}{SEP}\n")

        self.assertEqual(self.action.feedback(self.ctx, {"show": "yes"}), {})

    def test_a_press_drops_the_reading_it_invalidated(self) -> None:
        """Otherwise the repaint after pressing pause shows the state from
        before it."""
        with unittest.mock.patch.object(
            basic_actions.media, "perform", lambda _i: None
        ):
            nowplaying.current(now=1000.0)
            self.action.execute(self.ctx, {"show": "yes"})

        self.assertEqual(nowplaying._reading, (0.0, None))

    def test_a_failed_press_changes_nothing(self) -> None:
        with unittest.mock.patch.object(
            basic_actions.media, "perform",
            side_effect=ValueError("No media player is running"),
        ):
            self.action.execute(self.ctx, {"show": "yes"})

    def test_every_choice_is_labelled(self) -> None:
        self.assertEqual(
            sorted(basic_actions.NOW_PLAYING_LABELS),
            sorted(basic_actions.NOW_PLAYING_CHOICES),
        )


class LabelContractTests(unittest.TestCase):
    """An action may replace the key's label, falling back to the user's."""

    def _spec(self, feedback: dict, label: str = "Music") -> dict:
        from linuxstreamdeck.core.config import KIND_SINGLE, KeyConfig
        from linuxstreamdeck.core.controller import DeckController

        action = SimpleNamespace(
            feedback=lambda _ctx, _p: feedback,
            icon_for=lambda _p: "mdi:play-pause",
        )
        controller = SimpleNamespace(
            ctx=SimpleNamespace(for_key=lambda _k: None),
            _tkey=lambda index: (0, 0, (), index),
            _busy_state=lambda index: (False, False),
            _pulse_phase=lambda: False,
        )
        with unittest.mock.patch(
            "linuxstreamdeck.core.controller.action_registry.get",
            lambda _id: action,
        ):
            return DeckController._single_spec(
                controller, 0,
                KeyConfig(kind=KIND_SINGLE, action="sys.media", label=label),
                (96, 96),
            )

    def test_an_action_can_replace_the_label(self) -> None:
        self.assertEqual(self._spec({"label": "Pink Floyd"})["label"], "Pink Floyd")

    def test_without_one_the_user_keeps_theirs(self) -> None:
        self.assertEqual(self._spec({})["label"], "Music")

    def test_an_empty_label_falls_back_rather_than_blanking(self) -> None:
        """Which is what makes a media key show its own name again the moment
        nothing is playing."""
        self.assertEqual(self._spec({"label": ""})["label"], "Music")


class LiveRefreshTests(ReadingTestCase):
    def _interval(self, params):
        from linuxstreamdeck.core.config import KIND_SINGLE, KeyConfig
        from linuxstreamdeck.core.controller import DeckController

        controller = SimpleNamespace(
            obs=SimpleNamespace(connected=False),
            _twitch_linked=lambda: False,
            home_assistant=None,
        )
        return DeckController._live_interval(
            controller,
            KeyConfig(kind=KIND_SINGLE, action="sys.media", params=params),
        )

    def test_a_showing_key_repaints_on_the_clock(self) -> None:
        from linuxstreamdeck.core.controller import MEDIA_REFRESH_SECONDS

        self.assertEqual(self._interval({"show": "yes"}), MEDIA_REFRESH_SECONDS)

    def test_a_plain_transport_key_never_repaints(self) -> None:
        self.assertEqual(self._interval({}), 0.0)
        self.assertEqual(self._interval({"show": "no"}), 0.0)

    def test_nothing_repaints_without_playerctl(self) -> None:
        self.use(missing=True)

        self.assertEqual(self._interval({"show": "yes"}), 0.0)
