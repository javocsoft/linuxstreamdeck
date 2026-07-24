"""Key image composition with Pillow.

Each key is composed of: background (configurable color or state color),
optional icon, text label and active-state decoration (accent border + badge).
"""

from __future__ import annotations

import io
import logging
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..core.icons import RENDER_LOCK, library as icon_library

log = logging.getLogger(__name__)

ACCENT = "#62a0ea"
EMPTY_BG = "#141418"
TEXT_COLOR = "#ffffff"

_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/firasans/FiraSans-Bold.ttf",
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
]


@lru_cache(maxsize=16)
def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            try:
                # BASIC layout: avoids Pillow's harfbuzz (clashes with GTK's)
                return ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.BASIC)
            except Exception:
                continue
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    """Split the text into up to 2 lines that fit within max_width."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for w in words:
        candidate = f"{current} {w}".strip()
        if draw.textlength(candidate, font=font) <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = w
            if len(lines) == 2:
                break
    if current and len(lines) < 2:
        lines.append(current)
    # trim the last line with an ellipsis if it still doesn't fit
    if lines and draw.textlength(lines[-1], font=font) > max_width:
        line = lines[-1]
        while line and draw.textlength(line + "…", font=font) > max_width:
            line = line[:-1]
        lines[-1] = line + "…"
    return lines


def compose(
    size: tuple[int, int] = (72, 72),
    label: str = "",
    icon_path: str = "",
    bg: str = EMPTY_BG,
    active: bool = False,
    badge: str = "",
    icon_color: str = "#ffffff",
) -> Image.Image:
    w, h = size
    # all text drawing runs under the shared lock (FreeType is not thread-safe);
    # it is reentrant, so icon_library.render can re-acquire it without blocking.
    with RENDER_LOCK:
        img = Image.new("RGB", size, bg or EMPTY_BG)
        draw = ImageDraw.Draw(img)

        font_size = max(10, h // 6)
        font = _font(font_size)
        label_lines = _wrap(draw, label, font, w - 8) if label else []
        label_height = len(label_lines) * (font_size + 2)

        # icon centered in the free space above the label. Accepts both a library
        # icon ("mdi:name") and a path to the user's own image.
        if icon_path:
            box = h - label_height - 14
            if (icon := icon_library.render(icon_path, box, icon_color)) is not None:
                x = (w - icon.width) // 2
                y = (h - label_height - icon.height) // 2
                img.paste(icon, (x, max(2, y)), icon)

        # label at the bottom
        y = h - label_height - 4
        for line in label_lines:
            tw = draw.textlength(line, font=font)
            draw.text(((w - tw) // 2, y), line, font=font, fill=TEXT_COLOR)
            y += font_size + 2

        # state badge (top-right corner)
        if badge:
            bfont = _font(max(10, h // 6))
            bw = draw.textlength(badge, font=bfont)
            draw.text((w - bw - 5, 3), badge, font=bfont, fill="#ffffff")

        # accent border when the action is active
        if active:
            for i in range(3):
                draw.rectangle([i, i, w - 1 - i, h - 1 - i], outline=ACCENT)

    return img


def to_png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
