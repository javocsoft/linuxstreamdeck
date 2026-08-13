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

from PIL import Image, ImageDraw, ImageFont, ImageOps

from ..core.icons import RENDER_LOCK, library as icon_library
from ..core import fonts

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
# The same breathing for a key whose background already means something. It
# lightens rather than tinting, so the colour keeps saying what it said; the
# amounts are larger because lightening reads as less of a change than a hue
# shift does.
PULSE_TOWARDS = "#ffffff"
PULSE_BG_BLEND = (0.06, 0.16)

# Label font size. Each named scale is a divisor of the key height, so a chosen
# size keeps its proportion on any key geometry. "Medium" matches the automatic
# size used when the key does not select one.
AUTO_FONT_DIVISOR = 6.0
FONT_SIZE_DIVISORS = {
    "xs": 9.0,
    "s": 7.2,
    "m": 6.0,
    "l": 5.0,
    "xl": 4.2,
}
MIN_FONT_SIZE = 8

# A key whose action just failed. Drawn as a border rather than a background so
# it survives a key that carries its own artwork or a live scene preview, where
# a background change is invisible.
ERROR_BORDER = "#f06b73"
ERROR_BG_BLEND = 0.30
ERROR_BADGE = "!"

# A key that cannot do anything right now, because what it drives is not
# reachable. Kept as a plain fade of the finished key: it is the one treatment
# every desktop already uses for "unavailable", so it needs no explaining, and
# it composes with any artwork underneath.
UNAVAILABLE_FADE = 0.42

# How dark the gradient behind the label gets over a live preview. Enough for
# white text over a bright scene, light enough to still see what is under it.
SCRIM_STRENGTH = 0.82
# Curve of that gradient. Below 1 it darkens early and flattens, so the top of
# the glyphs is already on a dark background; a linear ramp is not.
SCRIM_RAMP = 0.35

# How thick the outline around a value drawn over a picture is, as a fraction
# of the text size. The label gets a gradient instead, because it sits along an
# edge; a value sits in the middle of the subject, where a band would cover the
# very thing the picture is there to show.
OUTLINE_DIVISOR = 7.0


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


def _blend(color, towards, amount: float) -> tuple[int, int, int]:
    """Move a color part of the way towards another."""
    return tuple(
        round(channel + (target - channel) * amount)
        for channel, target in zip(_rgb(color), _rgb(towards))
    )


def _accent_blend(color, amount: float) -> tuple[int, int, int]:
    """Blend a color gently towards the application accent."""
    return _blend(color, ACCENT, amount)


def _outline_width(font) -> int:
    """How thick an outline the value needs to survive any picture behind it.

    Proportional to the text, so it reads the same on a Mini and on an XL, and
    at least one pixel or it does nothing at all.
    """
    return max(1, round(getattr(font, "size", 12) / OUTLINE_DIVISOR))


def _contrasting(color) -> tuple[int, int, int]:
    """Black or white, whichever the given colour is not.

    The outline exists to separate the value from whatever is behind it, so it
    has to be the opposite of the value rather than a fixed colour: a key whose
    text colour was set to something dark needs a light one.
    """
    r, g, b = _rgb(color)
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    return (0, 0, 0) if luminance > 0.5 else (255, 255, 255)


def contrasting_ink(color) -> str:
    """"#000000" or "#ffffff", whichever reads on the given colour.

    The hex form of `_contrasting`, for a caller passing it back in as
    `text_color`: the alert flash lets the user choose the colour of the whole
    deck, so nothing can assume the word on it stays legible in black.
    """
    return "#000000" if _contrasting(color) == (0, 0, 0) else "#ffffff"


def _pulse_blend(color, amount: float) -> tuple[int, int, int]:
    """Breathe a color by lightening it, without changing what colour it is.

    The running-key pulse blends towards the accent, which is right when the
    background carries no meaning of its own. It is wrong when it does: an
    alert key whose red says "somebody has been waiting five minutes" rendered
    blue once the accent was mixed in, which is precisely the message lost.
    """
    return _blend(color, PULSE_TOWARDS, amount)


# One shared list, covering every distribution and the Flatpak runtime, and
# ending in the font the package carries. See core/fonts.py for why.
_FONT_CANDIDATES = fonts.SANS_BOLD


@lru_cache(maxsize=32)
def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            try:
                # BASIC layout: avoids Pillow's harfbuzz (clashes with GTK's)
                return ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.BASIC)
            except Exception:
                continue
    return ImageFont.load_default()


def _label_font_size(height: int, font_size: str) -> int:
    """Resolve a named label size into pixels for this key height."""
    divisor = FONT_SIZE_DIVISORS.get(str(font_size or "").lower())
    if divisor is None:
        return max(10, int(height // AUTO_FONT_DIVISOR))
    return max(MIN_FONT_SIZE, int(height / divisor))


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


def _photo_background(data: bytes, size: tuple[int, int]) -> Image.Image | None:
    """A live thumbnail cropped to fill the key, or None if it is unusable.

    Never raises: the picture is decoration over a key that must keep working,
    so anything unreadable falls back to the normal background.
    """
    try:
        with Image.open(io.BytesIO(data)) as source:
            return ImageOps.fit(source.convert("RGB"), size, Image.LANCZOS)
    except Exception:
        log.debug("Could not use a live preview image", exc_info=True)
        return None


def _scrim(img: Image.Image, height: int, strength: float = SCRIM_STRENGTH) -> None:
    """Darken the bottom of a photo so the label stays readable over it.

    Without this the label is white on whatever the scene happens to be
    showing: fine over a dark game, invisible over a bright one.

    The ramp is deliberately not linear. A straight gradient is still almost
    transparent where the top of the glyphs sits, so a pale scene left the
    first row of the text at nearly its own brightness; `SCRIM_RAMP` under 1
    darkens early and then flattens, which puts the whole label on a usable
    background while the fade into the picture above stays invisible.
    """
    if height <= 0:
        return
    width = img.width
    top = max(0, img.height - height)
    gradient = Image.new("L", (1, height))
    for row in range(height):
        position = row / max(1, height - 1)
        gradient.putpixel(
            (0, row), int(255 * strength * (position ** SCRIM_RAMP))
        )
    mask = gradient.resize((width, height))
    img.paste(Image.new("RGB", (width, height), (0, 0, 0)), (0, top), mask)


def compose(
    size: tuple[int, int] = (72, 72),
    label: str = "",
    icon_path: str = "",
    bg: str = EMPTY_BG,
    active: bool = False,
    busy: bool = False,
    busy_phase: bool = False,
    pulse: bool = False,
    badge: str = "",
    center_text: str = "",
    icon_color: str = "#ffffff",
    font_size: str = "",
    text_color: str = "",
    image: bytes | None = None,
    failed: bool = False,
    border: str = "",
    unavailable: bool = False,
) -> Image.Image:
    w, h = size
    # Everything drawn as text shares one colour: the label, a centered value
    # such as a clock, and the state badge. Leaving the badge white would keep
    # the very problem this setting exists for, since a pale background hides it
    # exactly as it hid the label.
    ink = text_color or TEXT_COLOR
    # when active, the whole key is softly lit instead of getting a border
    base_bg = _active_bg(bg or EMPTY_BG) if active else (bg or EMPTY_BG)
    if busy:
        base_bg = _accent_blend(base_bg, BUSY_BG_BLEND[int(busy_phase)])
    if pulse:
        base_bg = _pulse_blend(base_bg, PULSE_BG_BLEND[int(busy_phase)])
    if failed:
        base_bg = _blend(base_bg, ERROR_BORDER, ERROR_BG_BLEND)
    # all text drawing runs under the shared lock (FreeType is not thread-safe);
    # it is reentrant, so icon_library.render can re-acquire it without blocking.
    with RENDER_LOCK:
        photo = _photo_background(image, size) if image else None
        img = photo if photo is not None else Image.new("RGB", size, base_bg)
        draw = ImageDraw.Draw(img)

        label_size = _label_font_size(h, font_size)
        font = _font(label_size)
        label_lines = _wrap(draw, label, font, w - 8) if label else []
        label_height = len(label_lines) * (label_size + 2)

        if photo is not None:
            # The picture replaces the icon: it already says what the key is.
            icon_path = ""
            _scrim(img, label_height + 12 if label_lines else 0)

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
            # Over a picture the value needs an outline of its own. The label
            # has `_scrim()` because it sits along one edge, where a gradient
            # is invisible; this one sits in the middle of the subject, where a
            # band would cover the very thing the picture is there to show. A
            # bright avatar left "15s" white on yellow, over the face.
            outline = _outline_width(value_font) if photo is not None else 0
            draw.text(
                (x, max(2, y)),
                center_text,
                font=value_font,
                fill=ink,
                stroke_width=outline,
                stroke_fill=_contrasting(ink),
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
            draw.text(((w - tw) // 2, y), line, font=font, fill=ink)
            y += label_size + 2

        # state badge (top-right corner)
        if badge:
            bfont = _font(max(10, h // 6))
            bw = draw.textlength(badge, font=bfont)
            draw.text((w - bw - 5, 3), badge, font=bfont, fill=ink)

        if photo is not None and active:
            # The lit-background treatment of the active state is invisible over
            # a photograph, so the live scene is marked with a border instead.
            inset = max(1, min(w, h) // 36)
            draw.rounded_rectangle(
                (inset, inset, w - inset - 1, h - inset - 1),
                radius=max(4, min(w, h) // 10),
                outline=ACCENT,
                width=max(2, min(w, h) // 24),
            )

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

        # Both of these are drawn last, so they sit over artwork, a live
        # preview and any halo: the point of each is that it cannot be missed
        # on a glance at the deck, and a background change is invisible on a
        # key carrying a picture. A failure outranks any other border, because
        # "this did not work" is the more urgent of the two messages.
        outline = ERROR_BORDER if failed else (border or "")
        if outline:
            inset = max(1, min(w, h) // 48)
            draw.rounded_rectangle(
                (inset, inset, w - inset - 1, h - inset - 1),
                radius=max(5, min(w, h) // 9),
                outline=outline,
                width=max(2, min(w, h) // 22),
            )

        if unavailable:
            # Faded as a whole, after everything else, so nothing on the key can
            # escape it and look usable.
            img = Image.blend(Image.new("RGB", size, "black"), img, UNAVAILABLE_FADE)

    return img


def to_png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
