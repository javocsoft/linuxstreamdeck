"""Live thumbnails of an OBS scene drawn on a key.

The point of it is not decoration: a scene whose capture card died looks black
here, so you see it is broken *before* you cut to it instead of live on air.

Everything about it is opt-in and defensive. Each capture makes OBS render and
encode the scene on the machine that is also encoding the stream, so no key
asks for one unless it was configured to; several keys previewing one scene
share a single capture; and anything that fails falls back to the ordinary key
rather than leaving a blank square.
"""

from __future__ import annotations

import base64
import io
import unittest
from types import SimpleNamespace

from PIL import Image

from linuxstreamdeck import basic_actions as _basic_actions  # noqa: F401
from linuxstreamdeck.core import actions as registry
from linuxstreamdeck.core.config import KIND_MULTI, KIND_SINGLE, ActionStep, Config, KeyConfig
from linuxstreamdeck.core.controller import DeckController
from linuxstreamdeck.core.events import EventBus
from linuxstreamdeck.device.renderer import compose
from linuxstreamdeck.obs import actions as _obs_actions  # noqa: F401
from linuxstreamdeck.obs.actions import (
    PREVIEW_OFF,
    PREVIEW_SLOW,
    PREVIEW_SMOOTH,
    preview_interval,
)
from linuxstreamdeck.obs.client import OBSClient, _decode_image_data


def jpeg(color: str = "#3a4a6a", size=(320, 180)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, "JPEG", quality=85)
    return buffer.getvalue()


class FakeDeck:
    key_count = 15
    image_size = (72, 72)
    columns = 5
    dial_count = 0

    def __init__(self) -> None:
        self.screensaver_active = False

    def set_key_image(self, index, image) -> None:
        pass

    def record_activity(self) -> bool:
        return False

    def set_brightness(self, _value) -> None:
        pass

    def configure_screensaver(self, *_args) -> None:
        pass


class PreviewRateTests(unittest.TestCase):
    def test_off_asks_for_nothing(self) -> None:
        self.assertEqual(preview_interval(PREVIEW_OFF), 0.0)

    def test_a_key_saved_before_the_option_existed_is_off(self) -> None:
        self.assertEqual(preview_interval(None), 0.0)
        self.assertEqual(preview_interval(""), 0.0)

    def test_an_unknown_value_is_off(self) -> None:
        """A hand-edited file must not talk OBS into capturing constantly."""
        self.assertEqual(preview_interval("as fast as possible"), 0.0)

    def test_smooth_is_faster_than_slow(self) -> None:
        self.assertLess(
            preview_interval(PREVIEW_SMOOTH), preview_interval(PREVIEW_SLOW)
        )


class ImageDataTests(unittest.TestCase):
    def test_a_data_uri_is_decoded(self) -> None:
        payload = base64.b64encode(b"binary").decode()

        self.assertEqual(
            _decode_image_data(f"data:image/jpg;base64,{payload}"), b"binary"
        )

    def test_plain_base64_is_decoded(self) -> None:
        self.assertEqual(
            _decode_image_data(base64.b64encode(b"binary").decode()), b"binary"
        )

    def test_nonsense_decodes_to_nothing_rather_than_raising(self) -> None:
        self.assertIsNone(_decode_image_data("not base64 at all!!"))
        self.assertIsNone(_decode_image_data(None))
        self.assertIsNone(_decode_image_data(""))


class FakeReqClient:
    def __init__(self) -> None:
        self.captures: list[dict] = []
        self.payload = jpeg()

    def send(self, name, data=None, raw=True):
        if name != "GetSourceScreenshot":
            return {}
        self.captures.append(data)
        encoded = base64.b64encode(self.payload).decode()
        return {"imageData": f"data:image/jpg;base64,{encoded}"}


class ThumbnailCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = OBSClient(EventBus())
        self.req = FakeReqClient()
        self.client._req = self.req
        self.client.connected = True

    def test_a_scene_is_captured(self) -> None:
        data = self.client.source_thumbnail("Live", (72, 72), 1.0)

        self.assertEqual(data, self.req.payload)
        self.assertEqual(self.req.captures[0]["sourceName"], "Live")

    def test_the_capture_is_asked_for_at_the_key_size(self) -> None:
        """A key is 72 pixels; asking for a full frame would be absurd."""
        self.client.source_thumbnail("Live", (72, 72), 1.0)

        self.assertEqual(self.req.captures[0]["imageWidth"], 72)
        self.assertEqual(self.req.captures[0]["imageHeight"], 72)

    def test_several_keys_on_one_scene_share_a_capture(self) -> None:
        for _ in range(5):
            self.client.source_thumbnail("Live", (72, 72), 1.0)

        self.assertEqual(len(self.req.captures), 1)

    def test_different_scenes_are_captured_separately(self) -> None:
        self.client.source_thumbnail("Live", (72, 72), 1.0)
        self.client.source_thumbnail("BRB", (72, 72), 1.0)

        self.assertEqual(len(self.req.captures), 2)

    def test_a_caller_that_wants_a_fresher_frame_gets_one(self) -> None:
        """A key at two frames a second must not be held to another's rate."""
        self.client.source_thumbnail("Live", (72, 72), 1.0)

        self.client.source_thumbnail("Live", (72, 72), 0.0)

        self.assertEqual(len(self.req.captures), 2)

    def test_a_disconnected_obs_is_never_asked(self) -> None:
        self.client.connected = False

        self.assertIsNone(self.client.source_thumbnail("Live", (72, 72), 1.0))
        self.assertEqual(self.req.captures, [])

    def test_no_source_asks_for_nothing(self) -> None:
        self.assertIsNone(self.client.source_thumbnail("", (72, 72), 1.0))
        self.assertEqual(self.req.captures, [])

    def test_a_failed_capture_keeps_serving_the_last_good_frame(self) -> None:
        """One dropped request should not blank a key that was working."""
        good = self.client.source_thumbnail("Live", (72, 72), 1.0)
        self.client._req = None

        stale = self.client.source_thumbnail("Live", (72, 72), 0.0)

        self.assertEqual(stale, good)

    def test_the_cache_cannot_grow_without_bound(self) -> None:
        from linuxstreamdeck.obs.client import MAX_THUMBNAILS

        for index in range(MAX_THUMBNAILS + 20):
            self.client.source_thumbnail(f"Scene {index}", (72, 72), 1.0)

        self.assertLessEqual(len(self.client._thumbs), MAX_THUMBNAILS)


class PreviewFeedbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.action = registry.get("obs.scene_switch")
        self.asked: list[tuple] = []

    def _context(self, current: str = "Live", data: bytes | None = None):
        def thumbnail(source, size, max_age):
            self.asked.append((source, size, max_age))
            return data

        return SimpleNamespace(
            obs=SimpleNamespace(
                connected=True,
                state=SimpleNamespace(current_scene=current, preview_scene=""),
                source_thumbnail=thumbnail,
            ),
            controller=SimpleNamespace(key_image_size=(72, 72)),
            bus=SimpleNamespace(emit=lambda *a, **k: None),
        )

    def test_a_key_with_preview_off_never_asks_obs(self) -> None:
        ctx = self._context()

        state = self.action.feedback(ctx, {"scene": "Live", "preview": PREVIEW_OFF})

        self.assertEqual(self.asked, [])
        self.assertNotIn("image", state)

    def test_a_key_saved_before_the_option_existed_never_asks(self) -> None:
        ctx = self._context()

        self.action.feedback(ctx, {"scene": "Live"})

        self.assertEqual(self.asked, [])

    def test_a_key_with_preview_on_gets_an_image(self) -> None:
        data = jpeg()
        ctx = self._context(data=data)

        state = self.action.feedback(ctx, {"scene": "Live", "preview": PREVIEW_SLOW})

        self.assertEqual(state["image"], data)

    def test_the_capture_is_asked_for_at_the_deck_key_size(self) -> None:
        ctx = self._context(data=jpeg())

        self.action.feedback(ctx, {"scene": "Live", "preview": PREVIEW_SLOW})

        self.assertEqual(self.asked[0][1], (72, 72))

    def test_a_failed_capture_leaves_the_key_working(self) -> None:
        ctx = self._context(data=None)

        state = self.action.feedback(ctx, {"scene": "Live", "preview": PREVIEW_SLOW})

        self.assertNotIn("image", state)
        self.assertTrue(state["active"])

    def test_a_raising_client_does_not_break_the_key(self) -> None:
        ctx = self._context()
        ctx.obs.source_thumbnail = lambda *a: 1 / 0

        state = self.action.feedback(ctx, {"scene": "Live", "preview": PREVIEW_SLOW})

        self.assertNotIn("image", state)
        self.assertIn("active", state)

    def test_the_active_scene_is_still_reported(self) -> None:
        ctx = self._context(current="BRB", data=jpeg())

        state = self.action.feedback(ctx, {"scene": "Live", "preview": PREVIEW_SLOW})

        self.assertFalse(state["active"])

    def test_the_studio_preview_action_previews_too(self) -> None:
        action = registry.get("obs.scene_preview")
        ctx = self._context(data=jpeg())

        state = action.feedback(ctx, {"scene": "Live", "preview": PREVIEW_SLOW})

        self.assertIn("image", state)


class PreviewRenderingTests(unittest.TestCase):
    def _luminance(self, pixel) -> float:
        return 0.2126 * pixel[0] + 0.7152 * pixel[1] + 0.0722 * pixel[2]

    def test_the_photo_becomes_the_key(self) -> None:
        plain = compose(size=(72, 72), label="Live", bg="#1e1e28")

        with_photo = compose(size=(72, 72), label="Live", image=jpeg("#c04040"))

        self.assertNotEqual(plain.tobytes(), with_photo.tobytes())

    def test_the_photo_replaces_the_icon(self) -> None:
        """The picture already says what the key is; an icon over it is noise."""
        with_icon = compose(
            size=(72, 72), icon_path="mdi:play", image=jpeg("#202020")
        )
        without = compose(size=(72, 72), image=jpeg("#202020"))

        self.assertEqual(with_icon.tobytes(), without.tobytes())

    def test_a_label_stays_readable_over_a_bright_scene(self) -> None:
        """White text on a pale scene is the case the gradient exists for.

        Sampled in the left margin: the label is centred, so those columns are
        the darkened scene rather than the glyphs themselves.
        """
        image = compose(size=(72, 72), label="BRB", image=jpeg("#f4f2ec"))
        pixels = image.load()

        behind = [
            self._luminance(pixels[x, y])
            for y in range(72 - 16, 72)
            for x in range(2, 8)
        ]

        self.assertLess(max(behind), 190)

    def test_the_scene_above_the_label_is_left_alone(self) -> None:
        """The gradient must not wash out the picture it sits on."""
        image = compose(size=(72, 72), label="BRB", image=jpeg("#f4f2ec"))
        pixels = image.load()

        self.assertGreater(self._luminance(pixels[36, 8]), 200)

    def test_a_key_with_no_label_gets_no_gradient(self) -> None:
        labelled = compose(size=(72, 72), label="X", image=jpeg("#f4f2ec"))
        bare = compose(size=(72, 72), image=jpeg("#f4f2ec"))
        pixels = bare.load()

        self.assertGreater(self._luminance(pixels[36, 70]), 200)
        self.assertNotEqual(labelled.tobytes(), bare.tobytes())

    def test_the_live_scene_is_marked_with_a_border(self) -> None:
        """Lightening the background is invisible over a photograph."""
        idle = compose(size=(72, 72), label="Live", image=jpeg("#202020"))

        live = compose(
            size=(72, 72), label="Live", image=jpeg("#202020"), active=True
        )

        self.assertNotEqual(idle.tobytes(), live.tobytes())

    def test_unreadable_data_falls_back_to_the_ordinary_key(self) -> None:
        """A broken frame must never leave a blank square on the deck."""
        broken = compose(
            size=(72, 72), label="Live", icon_path="mdi:play", bg="#1e1e28",
            image=b"this is not an image",
        )
        normal = compose(
            size=(72, 72), label="Live", icon_path="mdi:play", bg="#1e1e28"
        )

        self.assertEqual(broken.tobytes(), normal.tobytes())

    def test_no_image_renders_exactly_as_before(self) -> None:
        with_none = compose(size=(72, 72), label="Live", bg="#1e1e28", image=None)
        without = compose(size=(72, 72), label="Live", bg="#1e1e28")

        self.assertEqual(with_none.tobytes(), without.tobytes())


class PreviewRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = Config()
        self.controller = DeckController(
            self.config,
            EventBus(),
            SimpleNamespace(connected=True),
            FakeDeck(),
        )
        self.addCleanup(self.controller.shutdown)
        self.page = self.config.pages[0]

    def _key(self, preview: str) -> KeyConfig:
        return KeyConfig(
            kind=KIND_SINGLE,
            action="obs.scene_switch",
            params={"scene": "Live", "preview": preview},
        )

    def test_a_preview_key_is_repainted_on_a_clock(self) -> None:
        self.page.set_key(2, self._key(PREVIEW_SLOW))

        keys = self.controller._live_keys()

        self.assertEqual(list(keys), [self.controller._tkey(2)])

    def test_it_is_repainted_at_the_rate_it_asked_for(self) -> None:
        self.page.set_key(2, self._key(PREVIEW_SLOW))
        self.page.set_key(3, self._key(PREVIEW_SMOOTH))

        keys = self.controller._live_keys()

        self.assertLess(
            keys[self.controller._tkey(3)], keys[self.controller._tkey(2)]
        )

    def test_a_scene_key_without_preview_costs_nothing(self) -> None:
        self.page.set_key(2, self._key(PREVIEW_OFF))

        self.assertEqual(self.controller._live_keys(), {})

    def test_a_scene_step_inside_a_list_is_never_previewed(self) -> None:
        """Feedback is resolved for a key's own action, never for a step."""
        self.page.set_key(
            4,
            KeyConfig(
                kind=KIND_MULTI,
                steps=[
                    ActionStep(
                        action="obs.scene_switch",
                        params={"scene": "Live", "preview": PREVIEW_SMOOTH},
                    )
                ],
            ),
        )

        self.assertEqual(self.controller._live_keys(), {})

    def test_the_key_size_comes_from_the_connected_deck(self) -> None:
        self.assertEqual(self.controller.key_image_size, (72, 72))


if __name__ == "__main__":
    unittest.main()
