"""The Stream Deck + LCD strip: one segment per encoder.

The strip is the only thing that can say what a dial does. Without it the four
encoders are indistinguishable, so the segments are drawn as labelled panels
lined up over the dials they belong to rather than as free-form artwork.

Rendering follows the same rules as every other renderer here: Pillow only,
under the shared `RENDER_LOCK`, with `ImageFont.Layout.BASIC`.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..core.config import DIAL_TOUCH_SIZE
from ..core.icons import RENDER_LOCK, library as icon_library
from .renderer import (
    FONT_SIZE_DIVISORS,
    MIN_FONT_SIZE,
    TEXT_COLOR,
    _FONT_CANDIDATES,
    _rgb,
)

BACKGROUND = "#000000"
PANEL_BG = "#14141c"
PANEL_EMPTY_BG = "#0a0a0e"
DIVIDER = "#26262e"
SUBTITLE_COLOR = "#8b90a0"

PANEL_INSET = 4
PANEL_RADIUS = 8
ICON_BOX = 40
LABEL_SIZE = 18
VALUE_SIZE = 15
# What a named font size is measured against here. Chosen so "Medium" lands on
# LABEL_SIZE, which is the size the strip used before the setting existed.
LABEL_REFERENCE = 108


@lru_cache(maxsize=8)
def _font(size: int):
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            try:
                # BASIC layout: Pillow's bundled harfbuzz draws blank glyphs
                # beside GTK's system one, at random, once per process.
                return ImageFont.truetype(
                    path, size, layout_engine=ImageFont.Layout.BASIC
                )
            except Exception:
                continue
    return ImageFont.load_default()


def segment_bounds(
    index: int, size: tuple[int, int] = DIAL_TOUCH_SIZE, dials: int = 4
) -> tuple[int, int, int, int]:
    """The pixel box of one dial's segment of the strip.

    Widths are derived from the running edge rather than from a rounded
    per-segment width, so the segments always tile the strip exactly: four
    panels of `800 // 4` happen to fit, four of `801 // 4` would leave a
    one-pixel seam that is visible on a black background.
    """
    width, height = size
    dials = max(1, int(dials))
    index = max(0, min(int(index), dials - 1))
    left = width * index // dials
    right = width * (index + 1) // dials
    return (left, 0, right, height)


def _label_size(height: int, font_size: str) -> int:
    """Resolve the key's named label size against the strip's own height.

    The panel is 100 px tall against a key's 72, so reusing the key divisors
    directly would draw every label larger here than on the deck. Scaling them
    to a fixed reference keeps a dial's label the size its name promises.
    """
    divisor = FONT_SIZE_DIVISORS.get(str(font_size or "").lower())
    if divisor is None:
        return LABEL_SIZE
    return max(MIN_FONT_SIZE, int(LABEL_REFERENCE / divisor))


def _fitted(draw, text: str, font, max_width: int) -> str:
    """Shorten text to fit, marking that it was shortened."""
    if not text or draw.textlength(text, font=font) <= max_width:
        return text
    room = max_width - draw.textlength("…", font=font)
    while text and draw.textlength(text, font=font) > room:
        text = text[:-1]
    return text.rstrip() + "…"


def _centered(draw, text: str, font, box: tuple[int, int, int, int], y: int,
              fill: str) -> None:
    left, _top, right, _bottom = box
    text = _fitted(draw, text, font, right - left - 8)
    if not text:
        return
    width = draw.textlength(text, font=font)
    draw.text((left + (right - left - width) / 2, y), text, font=font, fill=fill)


def _panel(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    dial,
    value: str,
) -> None:
    """One dial's panel: its icon, its name and an optional live value."""
    left, top, right, bottom = box
    inner = (
        left + PANEL_INSET,
        top + PANEL_INSET,
        right - PANEL_INSET - 1,
        bottom - PANEL_INSET - 1,
    )
    configured = dial is not None and not dial.is_empty()
    background = (dial.bg_color if configured else "") or (
        PANEL_BG if configured else PANEL_EMPTY_BG
    )
    draw.rounded_rectangle(inner, radius=PANEL_RADIUS, fill=_rgb(background))
    if not configured:
        return

    ink = dial.text_color or TEXT_COLOR
    label = dial.label or ""
    height = bottom - top
    icon_ref = dial.icon or _default_icon(dial)
    # The value replaces the icon rather than joining it: the strip is 100 px
    # tall and a live number is worth more than a glyph beside it.
    if value:
        _centered(draw, value, _font(VALUE_SIZE + 6), box, top + 16, ink)
        if label:
            _centered(draw, label, _font(VALUE_SIZE), box, height - 30,
                      SUBTITLE_COLOR)
        return
    label_size = _label_size(height, dial.font_size)
    if icon_ref:
        icon = icon_library.render(icon_ref, ICON_BOX, ink)
        if icon is not None:
            x = left + (right - left - icon.width) // 2
            y = top + (height - icon.height) // 2 - (12 if label else 0)
            image.paste(icon, (x, max(top + 4, y)), icon)
    if label:
        _centered(
            draw,
            label,
            _font(label_size),
            box,
            height - label_size - 12 if icon_ref else (height - label_size) // 2,
            ink,
        )


def _default_icon(dial) -> str:
    """The icon a dial inherits from the first action it would run."""
    from ..core import actions as action_registry

    for steps in (dial.steps_press, dial.steps_right, dial.steps_left):
        for step in steps:
            action = action_registry.get(step.action)
            if action is not None and action.default_icon:
                return action.default_icon
    return ""


def touchscreen_image(
    dials,
    values=None,
    size: tuple[int, int] = DIAL_TOUCH_SIZE,
    count: int = 4,
) -> Image.Image:
    """Compose the whole strip from the page's dial configuration.

    `dials` maps a dial index to its `KeyConfig`; `values` optionally maps one
    to live text (a volume percentage, a clock) shown in place of the icon.
    """
    values = values or {}
    count = max(1, int(count))
    with RENDER_LOCK:
        image = Image.new("RGB", size, BACKGROUND)
        draw = ImageDraw.Draw(image)
        for index in range(count):
            box = segment_bounds(index, size, count)
            _panel(image, draw, box, dials.get(index), str(values.get(index, "")))
            if index:
                draw.line((box[0], 6, box[0], size[1] - 6), fill=DIVIDER)
    return image
