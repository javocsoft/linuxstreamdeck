"""Key image composition with Pillow.

Each key is composed of: background (configurable color or state color; softly
lit when the action is active), optional icon, text label and a state badge.
"""

from __future__ import annotations

import colorsys
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

# "Active" state: instead of a border, the whole key is softly lit (like the
# native Stream Deck software). ACTIVE_LIGHTEN raises the background lightness
# (preserving hue, so a red key gets brighter red); ACTIVE_ACCENT adds a faint
# accent glow scaled by how neutral the color is, so it mainly shows on
# dark/neutral backgrounds and barely touches saturated ones.
ACTIVE_LIGHTEN = 0.15
ACTIVE_ACCENT = 0.12
# Running multi-actions use a slow two-step "breathing" glow. The values stay
# deliberately low so the effect is visible without competing with the key art.
BUSY_BG_BLEND = (0.035, 0.07)
BUSY_HALO_BLEND = (0.24, 0.38)


def _rgb(color) -> tuple[int, int, int]:
    """Parse a '#rrggbb' (or '#rgb') string into an (r, g, b) tuple."""
    if isinstance(color, (tuple, list)) and len(color) >= 3:
        try:
            return tuple(max(0, min(255, int(v))) for v in color[:3])
        except (ValueError, TypeError):
            return (20, 20, 24)
    c = str(color).lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    try:
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except (ValueError, IndexError):
        return (20, 20, 24)


def _active_bg(bg) -> tuple[int, int, int]:
    """Softly 'lit' version of a background, used for the active state."""
    r, g, b = (v / 255 for v in _rgb(bg))
    h, lightness, s = colorsys.rgb_to_hls(r, g, b)
    r, g, b = colorsys.hls_to_rgb(h, min(1.0, lightness + ACTIVE_LIGHTEN), s)
    # accent glow only on desaturated (neutral/dark) backgrounds
    ar, ag, ab = (v / 255 for v in _rgb(ACCENT))
    am = ACTIVE_ACCENT * (1 - s)
    r += (ar - r) * am
    g += (ag - g) * am
    b += (ab - b) * am
    return (min(255, int(r * 255)), min(255, int(g * 255)), min(255, int(b * 255)))


def _accent_blend(color, amount: float) -> tuple[int, int, int]:
    """Blend a color gently towards the application accent."""
    base = _rgb(color)
    accent = _rgb(ACCENT)
    return tuple(
        round(channel + (target - channel) * amount)
        for channel, target in zip(base, accent)
    )


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


def _fitted_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    preferred_size: int,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for size in range(preferred_size, 8, -1):
        font = _font(size)
        left, _top, right, _bottom = draw.textbbox((0, 0), text, font=font)
        if right - left <= max_width:
            return font
    return _font(9)


def compose(
    size: tuple[int, int] = (72, 72),
    label: str = "",
    icon_path: str = "",
    bg: str = EMPTY_BG,
    active: bool = False,
    busy: bool = False,
    busy_phase: bool = False,
    badge: str = "",
    center_text: str = "",
    icon_color: str = "#ffffff",
) -> Image.Image:
    w, h = size
    # when active, the whole key is softly lit instead of getting a border
    base_bg = _active_bg(bg or EMPTY_BG) if active else (bg or EMPTY_BG)
    if busy:
        base_bg = _accent_blend(base_bg, BUSY_BG_BLEND[int(busy_phase)])
    # all text drawing runs under the shared lock (FreeType is not thread-safe);
    # it is reentrant, so icon_library.render can re-acquire it without blocking.
    with RENDER_LOCK:
        img = Image.new("RGB", size, base_bg)
        draw = ImageDraw.Draw(img)

        font_size = max(10, h // 6)
        font = _font(font_size)
        label_lines = _wrap(draw, label, font, w - 8) if label else []
        label_height = len(label_lines) * (font_size + 2)

        # Dynamic values such as clocks replace the icon and use the full
        # available width. Otherwise show the configured or built-in icon.
        if center_text:
            value_font = _fitted_font(
                draw,
                center_text,
                w - 6,
                max(12, h // 4),
            )
            left, top, right, bottom = draw.textbbox(
                (0, 0),
                center_text,
                font=value_font,
            )
            value_width = right - left
            value_height = bottom - top
            available_height = h - label_height
            x = (w - value_width) // 2 - left
            y = (available_height - value_height) // 2 - top
            draw.text(
                (x, max(2, y)),
                center_text,
                font=value_font,
                fill=TEXT_COLOR,
            )
        elif icon_path:
            # Icons accept both a library reference ("mdi:name") and a path to
            # the user's own image.
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

        if busy:
            # A narrow rounded halo leaves custom artwork readable. Alternating
            # only its intensity creates a calm breathing effect, not a flash.
            inset = max(1, min(w, h) // 48)
            halo = _accent_blend(
                base_bg,
                BUSY_HALO_BLEND[int(busy_phase)],
            )
            draw.rounded_rectangle(
                (inset, inset, w - inset - 1, h - inset - 1),
                radius=max(5, min(w, h) // 9),
                outline=halo,
                width=max(1, min(w, h) // 36),
            )

    return img


def to_png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
