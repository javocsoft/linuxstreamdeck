"""Executable versions of the invariants in AGENTS.md section 5.

These do not test a feature. They test the rules whose violation is *silent*:
blank glyphs that appear only on some launches, a protocol corruption that needs
two threads to reproduce, a teardown order that looks arbitrary. Every one of
them has already cost real debugging time, and none of them shows up as a failed
assertion anywhere else in this suite.
"""

from __future__ import annotations

import threading
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

from PIL import ImageDraw, ImageFont

from linuxstreamdeck.core import icons
from linuxstreamdeck.device import exit_display, renderer, screensaver
from linuxstreamdeck.device import layout_sheet, startup_animation, touchscreen
from linuxstreamdeck.games import (
    circuit_render,
    mastermind_render,
    memory_render,
    minesweeper_render,
    neon_relay_render,
    pulse_render,
    tic_tac_toe_render,
)
from linuxstreamdeck.games import render as game_render
from linuxstreamdeck.games import rendering as game_rendering


def _clear_font_caches() -> None:
    """Every cached font loader, so a patched loader is actually reached."""
    for cached in (
        icons._font,
        renderer._font,
        screensaver._font,
        screensaver._matrix_font,
        screensaver._matrix_font_path,
        screensaver._matrix_glyph,
        screensaver._flap_glyph,
        startup_animation._font,
        touchscreen._font,
        layout_sheet._font,
        game_render._font,
        game_render._mole,
        game_rendering.game_font,
    ):
        cached.cache_clear()


def _render_everything() -> None:
    """Touch every path that draws text or holds the render lock."""
    icons.library.render("mdi:play", 48, "#ffffff")
    renderer.compose((72, 72), label="Live", icon_path="mdi:play", badge="RUN")
    renderer.compose((72, 72), label="Timer", center_text="00:12")
    renderer.compose((72, 72), label="Live", image=_preview_frame(), active=True)
    # A value over a picture, which draws its text with an outline.
    renderer.compose((72, 72), label="Chat", center_text="15s",
                     image=_preview_frame(), border="#5aa0e8", pulse=True)
    # The two states that mark a key rather than its action: both draw, and the
    # faded one also composites the finished image.
    renderer.compose((72, 72), label="Rec", icon_path="mdi:play", failed=True,
                     badge=renderer.ERROR_BADGE)
    renderer.compose((72, 72), label="Rec", icon_path="mdi:play", unavailable=True)
    for style in ("linuxstreamdeck", "split_flap", "matrix_code", "hal_9000"):
        screensaver.screensaver_frame(style, 1.5, 15, (72, 72), 40)
    for frame in startup_animation.startup_frames(15, (72, 72), 40):
        del frame
    exit_display.blank_exit_tiles(15, (72, 72))
    touchscreen.touchscreen_image(
        {0: _dial()}, values={1: "72%"}, size=(800, 100), count=4
    )
    layout_sheet.profile_sheet(_sheet_profile(), 5, 3)
    _render_games()


def _render_games() -> None:
    """Exercise every built-in renderer under the same font/lock probes."""
    import random

    from linuxstreamdeck.games.circuit_breaker import CircuitBreakerEngine
    from linuxstreamdeck.games.common import COUNTDOWN_SECONDS, game_layout
    from linuxstreamdeck.games.memory_match import MemoryMatchEngine
    from linuxstreamdeck.games.mastermind import MastermindEngine
    from linuxstreamdeck.games.minesweeper import MinesweeperEngine
    from linuxstreamdeck.games.mole_smash import MoleSmashEngine
    from linuxstreamdeck.games.neon_relay import NeonRelayEngine
    from linuxstreamdeck.games.pulse_memory import PulseMemoryEngine
    from linuxstreamdeck.games.tic_tac_toe import TicTacToeEngine

    layout = game_layout(6, 3)
    engines = (
        MoleSmashEngine(layout, rng=random.Random(1)),
        CircuitBreakerEngine(layout, rng=random.Random(1)),
        PulseMemoryEngine(layout, difficulty="easy", rng=random.Random(1)),
        MemoryMatchEngine(layout, difficulty="hard", rng=random.Random(1)),
        MinesweeperEngine(layout, rng=random.Random(1)),
        TicTacToeEngine(layout, rng=random.Random(1)),
        MastermindEngine(layout, rng=random.Random(1)),
        NeonRelayEngine(layout, rng=random.Random(1)),
    )
    for engine in engines:
        engine.press(layout.start_key, 0.0)
    engines[0].tick(COUNTDOWN_SECONDS + 0.01)
    engines[0].tick(COUNTDOWN_SECONDS + 0.02)
    engines[2].tick(COUNTDOWN_SECONDS + 0.01)
    engines[3].press(0, 0.1)
    engines[4].press(0, 0.1)
    engines[5].press(0, 0.1)
    engines[6].press(0, 0.1)
    for engine in engines:
        game_render.render_keys(engine.snapshot(COUNTDOWN_SECONDS + 0.02), (72, 72))


def _preview_frame() -> bytes:
    """A live scene thumbnail, which composes a key from a photograph."""
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (160, 90), "#f4f2ec").save(buffer, "JPEG")
    return buffer.getvalue()


def _dial():
    from linuxstreamdeck.core.config import KIND_DIAL, ActionStep, KeyConfig

    dial = KeyConfig(kind=KIND_DIAL, label="Mic")
    dial.steps_press = [ActionStep(action="obs.mute")]
    return dial


def _sheet_profile():
    from linuxstreamdeck.core.config import KIND_SINGLE, KeyConfig, Page, Profile

    page = Page(name="Live")
    page.set_key(0, KeyConfig(kind=KIND_SINGLE, action="obs.record", label="REC"))
    return Profile(name="Sheet", pages=[page])


class BasicFontLayoutTests(unittest.TestCase):
    """AGENTS.md section 5.1: every font must load with `Layout.BASIC`.

    Pillow's wheel bundles its own harfbuzz. Alongside GTK/Pango's system copy
    it intermittently draws blank glyphs — random per process, so some launches
    render every icon and others render none. Nothing else in this suite would
    fail if a `layout_engine=` argument were dropped.
    """

    def setUp(self) -> None:
        _clear_font_caches()
        self.addCleanup(_clear_font_caches)

    def test_every_font_is_loaded_with_the_basic_layout_engine(self) -> None:
        loads: list[tuple[str, object]] = []
        real = ImageFont.truetype

        def record(font, size=10, *args, **kwargs):
            loads.append((str(font), kwargs.get("layout_engine")))
            return real(font, size, *args, **kwargs)

        with patch.object(ImageFont, "truetype", record):
            _render_everything()

        self.assertTrue(loads, "no font was loaded at all; the test is blind")
        wrong = [path for path, engine in loads if engine is not ImageFont.Layout.BASIC]
        self.assertEqual(
            wrong,
            [],
            "these fonts were loaded without ImageFont.Layout.BASIC, which "
            "intermittently renders blank glyphs",
        )

    def test_the_bundled_icon_font_really_draws_a_glyph(self) -> None:
        """A blank render is the symptom, so check the pixels, not just the call."""
        image = icons.library.render("mdi:play", 48, "#ffffff")

        self.assertIsNotNone(image)
        self.assertIsNotNone(image.getbbox(), "the icon rendered completely blank")


class _TrackedLock:
    """Stands in for RENDER_LOCK and remembers whether it is currently held."""

    def __init__(self, real) -> None:
        self._real = real
        self.depth = 0

    def __enter__(self):
        self._real.__enter__()
        self.depth += 1
        return self

    def __exit__(self, *args):
        self.depth -= 1
        return self._real.__exit__(*args)


class RenderLockTests(unittest.TestCase):
    """AGENTS.md section 5.2: Pillow drawing is serialized by `RENDER_LOCK`.

    Configured keys render on a worker, the screen saver on its own thread and
    the icon picker on the main thread. Concurrent Pillow use produced blank —
    and blank-cached — glyphs. Each module imports the lock by value, so each
    one has to be checked separately: patching only `icons.RENDER_LOCK` would
    leave the others holding the original object and prove nothing.
    """

    def setUp(self) -> None:
        _clear_font_caches()
        self.addCleanup(_clear_font_caches)

    def test_nothing_draws_outside_the_render_lock(self) -> None:
        tracked = _TrackedLock(icons.RENDER_LOCK)
        unguarded: list[str] = []
        real_draw = ImageDraw.Draw

        def guarded_draw(image, *args, **kwargs):
            if tracked.depth == 0:
                unguarded.append(f"{image.mode} {image.size}")
            return real_draw(image, *args, **kwargs)

        holders = (
            icons, renderer, screensaver, startup_animation, exit_display,
            touchscreen, layout_sheet, game_render, circuit_render,
            pulse_render, memory_render, minesweeper_render,
            tic_tac_toe_render, mastermind_render,
            neon_relay_render,
        )
        with patch.object(ImageDraw, "Draw", guarded_draw):
            with ExitStack() as stack:
                for holder in holders:
                    stack.enter_context(
                        patch.object(holder, "RENDER_LOCK", tracked)
                    )
                _render_everything()

        self.assertEqual(
            unguarded,
            [],
            "drawing happened while RENDER_LOCK was not held",
        )
        self.assertEqual(tracked.depth, 0, "the lock was left held")

    def test_every_drawing_module_holds_the_same_lock_object(self) -> None:
        """Imported by value, so a second lock would serialize nothing."""
        for module in (
            renderer, screensaver, startup_animation, exit_display,
            touchscreen, layout_sheet, game_render, circuit_render,
            pulse_render, memory_render, minesweeper_render,
            tic_tac_toe_render, mastermind_render,
            neon_relay_render,
        ):
            self.assertIs(
                module.RENDER_LOCK,
                icons.RENDER_LOCK,
                f"{module.__name__} holds a different lock",
            )

    def test_the_lock_is_reentrant(self) -> None:
        """`compose()` calls `library.render`, which takes it again."""
        with icons.RENDER_LOCK:
            with icons.RENDER_LOCK:
                pass


class ObsRequestSerializationTests(unittest.TestCase):
    """AGENTS.md section 5.3: the lock is held for the WHOLE `req.send(...)`.

    `obsws_python.ReqClient` drives a single websocket that is not thread-safe,
    and requests genuinely arrive from two threads at once: the GTK thread
    filling editor dropdowns and the render worker resolving key feedback.
    Holding the lock only while reading the client pointer corrupts the protocol
    — the symptom is a hang at about 73% CPU and a disconnect, which no
    single-threaded test would ever reproduce.
    """

    def _client(self, send):
        from linuxstreamdeck.core.events import EventBus
        from linuxstreamdeck.obs.client import OBSClient

        client = OBSClient(EventBus())
        client.connected = True
        client._req = SimpleNamespace(send=send)
        return client

    def test_a_second_request_cannot_enter_send_while_one_is_inside_it(
        self,
    ) -> None:
        inside = threading.Event()
        release = threading.Event()
        overlapped = threading.Event()
        concurrent = threading.Semaphore(1)

        def send(name, data=None, raw=False):
            if not concurrent.acquire(blocking=False):
                overlapped.set()
            try:
                if name == "First":
                    inside.set()
                    # Held open until the other thread has had every chance to
                    # get in. If the lock only guarded the pointer, it would.
                    release.wait(2.0)
                return {}
            finally:
                concurrent.release()

        client = self._client(send)
        blocked = threading.Event()

        def second() -> None:
            self.assertTrue(inside.wait(2.0))
            started = threading.Event()

            def run() -> None:
                started.set()
                client.request("Second")
                blocked.set()

            worker = threading.Thread(target=run, daemon=True)
            worker.start()
            self.assertTrue(started.wait(2.0))
            # The second request must still be waiting on the lock.
            entered_anyway = blocked.wait(0.4)
            release.set()
            worker.join(2.0)
            self.assertFalse(
                entered_anyway,
                "a second request completed while the first was inside send()",
            )

        helper = threading.Thread(target=second, daemon=True)
        helper.start()
        client.request("First")
        helper.join(5.0)

        self.assertFalse(helper.is_alive(), "the helper thread never finished")
        self.assertFalse(
            overlapped.is_set(), "two requests were inside send() at once"
        )

    def test_a_failing_request_still_releases_the_lock(self) -> None:
        """A raising send must not wedge every later request."""

        def send(name, data=None, raw=False):
            raise RuntimeError("websocket died")

        client = self._client(send)
        with self.assertRaises(RuntimeError):
            client.request("Boom")

        self.assertTrue(
            client._lock.acquire(timeout=1.0), "the lock was never released"
        )
        client._lock.release()

    def test_a_disconnected_client_refuses_instead_of_sending(self) -> None:
        client = self._client(lambda *a, **k: {})
        client._req = None

        with self.assertRaises(ConnectionError):
            client.request("GetSceneList")


class ShutdownOrderTests(unittest.TestCase):
    """AGENTS.md section 3: stop the icon, then the controller, then the device.

    The order is not arbitrary. Controller workers must stop before the HID
    manager so no render is submitted to a closed device, and the deck must be
    stopped before the network clients so nothing is waiting on a request that
    can no longer be answered. It reads like tidy-uppable boilerplate, which is
    exactly why it needs pinning.
    """

    @staticmethod
    def _app(calls: list[str], tray=True):
        from linuxstreamdeck.app import LinuxStreamDeckApp

        app = SimpleNamespace(
            _shutting_down=False,
            tray=(
                SimpleNamespace(stop=lambda: calls.append("tray"))
                if tray
                else None
            ),
            controller=SimpleNamespace(
                shutdown=lambda: calls.append("controller")
            ),
            deck=SimpleNamespace(stop=lambda: calls.append("deck")),
            obs=SimpleNamespace(stop=lambda: calls.append("obs")),
            events=SimpleNamespace(stop=lambda: calls.append("events")),
            twitch=SimpleNamespace(stop=lambda: calls.append("twitch")),
        )
        return LinuxStreamDeckApp._on_shutdown, app

    def test_everything_stops_in_the_documented_order(self) -> None:
        calls: list[str] = []
        shutdown, app = self._app(calls)

        shutdown(app, None)

        self.assertEqual(
            calls, ["tray", "controller", "deck", "obs", "events", "twitch"]
        )

    def test_shutdown_marks_the_application_as_stopping_first(self) -> None:
        """`hides_on_close()` must not intercept a quit already in progress."""
        seen: list[bool] = []
        calls: list[str] = []
        shutdown, app = self._app(calls)
        app.tray = SimpleNamespace(
            stop=lambda: seen.append(app._shutting_down)
        )

        shutdown(app, None)

        self.assertEqual(seen, [True])

    def test_a_session_with_no_status_icon_still_shuts_down(self) -> None:
        calls: list[str] = []
        shutdown, app = self._app(calls, tray=False)

        shutdown(app, None)

        self.assertEqual(
            calls, ["controller", "deck", "obs", "events", "twitch"]
        )

    def test_the_status_icon_is_dropped_so_it_cannot_be_stopped_twice(
        self,
    ) -> None:
        calls: list[str] = []
        shutdown, app = self._app(calls)

        shutdown(app, None)
        shutdown(app, None)

        self.assertEqual(calls.count("tray"), 1)


class ActionContextCompletenessTests(unittest.TestCase):
    """Every service an action can reach has to survive into the run context.

    This is not a feature test. A service present when a key is *rendered* and
    missing when it is *pressed* produces the worst possible symptom: the key
    looks perfectly configured, its live value updates, and the action fails
    with a message saying the thing is not connected. That is exactly what
    happened to the Twitch client, because three separate places each built an
    `ActionContext` by listing its fields and one of them was never updated.

    `ActionContext.derive()` is now the single place that knows those fields,
    and this fails loudly if a caller goes back to enumerating them.
    """

    @staticmethod
    def _services(ctx) -> dict:
        """Everything on a context that an action could reach out through."""
        return {
            name: getattr(ctx, name)
            for name in vars(ctx)
            if not name.startswith("_") and name != "key"
        }

    def _controller(self):
        from linuxstreamdeck.core.config import Config
        from linuxstreamdeck.core.controller import DeckController
        from linuxstreamdeck.core.events import EventBus

        deck = SimpleNamespace(
            key_count=15,
            image_size=(72, 72),
            columns=5,
            dial_count=0,
            screensaver_active=False,
            set_key_image=lambda *a: None,
            record_activity=lambda: False,
            set_brightness=lambda *a: None,
            configure_screensaver=lambda *a: None,
        )
        controller = DeckController(
            Config(),
            EventBus(),
            SimpleNamespace(connected=False),
            deck,
            twitch=SimpleNamespace(linked=True),
        )
        self.addCleanup(controller.shutdown)
        return controller

    def test_a_run_context_carries_every_service_the_base_one_has(self) -> None:
        controller = self._controller()

        run_ctx = controller.ctx.for_run((0, 0, 0), None)

        self.assertEqual(
            self._services(run_ctx), self._services(controller.ctx)
        )

    def test_a_key_scoped_context_carries_them_too(self) -> None:
        controller = self._controller()

        self.assertEqual(
            self._services(controller.ctx.for_key((0, 0, 0))),
            self._services(controller.ctx),
        )

    def test_what_actually_executes_can_reach_them(self) -> None:
        """The end-to-end version: capture the context an action is handed."""
        from linuxstreamdeck.core import actions as registry
        from linuxstreamdeck.core.config import ActionStep

        controller = self._controller()
        seen: list = []

        class Spy(registry.Action):
            id = "test.context_spy"
            name = "Context spy"
            category = "Testing"

            def execute(self, ctx, params):
                seen.append(ctx)

        registry.REGISTRY[Spy.id] = Spy()
        self.addCleanup(registry.REGISTRY.pop, Spy.id, None)

        controller._run_steps([ActionStep(action=Spy.id, params={})], 0)

        self.assertEqual(len(seen), 1)
        self.assertEqual(
            self._services(seen[0]), self._services(controller.ctx)
        )

    def test_derive_replaces_only_what_it_is_given(self) -> None:
        controller = self._controller()
        cancellation = threading.Event()

        derived = controller.ctx.derive(cancellation=cancellation)

        self.assertIs(derived.twitch, controller.ctx.twitch)
        self.assertIs(derived.obs, controller.ctx.obs)
        self.assertTrue(derived.derive().stop_requested() is False)
        cancellation.set()
        self.assertTrue(derived.stop_requested())


if __name__ == "__main__":
    unittest.main()
