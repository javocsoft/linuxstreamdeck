"""Small Pillow helpers shared by the built-in game renderers."""

from __future__ import annotations

import colorsys
import io
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

from ..core import fonts
from ..device import renderer
from .common import DIFFICULTY_LABELS, PHASE_LOBBY

BG = "#11151b"
CYAN = "#35c8e6"
GREEN = "#36c98f"
GOLD = "#ffcc4d"
RED = "#ef6a73"
INK = "#f7fbff"


def to_png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@lru_cache(maxsize=32)
def game_font(size: int, bold: bool = True):
    choices = fonts.SANS_BOLD if bold else fonts.SANS_REGULAR
    for path in choices:
        try:
            return ImageFont.truetype(
                path,
                max(8, int(size)),
                layout_engine=ImageFont.Layout.BASIC,
            )
        except OSError:
            continue
    return ImageFont.load_default()


def game_color(index: int, count: int, *, light: float = 0.55) -> str:
    hue = (int(index) / max(1, int(count)) + 0.52) % 1.0
    red, green, blue = colorsys.hls_to_rgb(hue, light, 0.72)
    return f"#{round(red * 255):02x}{round(green * 255):02x}{round(blue * 255):02x}"


def lobby_control(snapshot, index: int, size, record_label: str) -> Image.Image | None:
    layout = snapshot.layout
    if index == layout.start_key:
        return renderer.compose(
            size=size,
            bg=GREEN,
            center_text="START" if snapshot.phase == PHASE_LOBBY else "AGAIN",
            text_color="#071711",
        )
    if index == layout.exit_key:
        return renderer.compose(
            size=size,
            bg="#4a2028",
            icon_path="mdi:arrow-left",
            label="Back",
            icon_color=INK,
        )
    if index == layout.difficulty_key:
        return renderer.compose(
            size=size,
            bg="#1c3442",
            icon_path="mdi:speedometer",
            label=DIFFICULTY_LABELS[snapshot.difficulty],
            icon_color=CYAN,
        )
    if index == layout.sound_key:
        return renderer.compose(
            size=size,
            bg="#25303a",
            icon_path=(
                "mdi:volume-high" if snapshot.sound_enabled else "mdi:volume-off"
            ),
            label="Sound",
            badge="ON" if snapshot.sound_enabled else "OFF",
            icon_color=CYAN if snapshot.sound_enabled else "#8b96a3",
        )
    if index == layout.record_key:
        return renderer.compose(
            size=size,
            bg="#3c3014",
            icon_path="mdi:trophy",
            label=record_label,
            icon_color=GOLD,
        )
    return None


def draw_hud_header(
    image: Image.Image,
    title: str,
    difficulty: str,
    progress: float,
    accent: str,
) -> tuple[ImageDraw.ImageDraw, ImageFont.ImageFont, ImageFont.ImageFont]:
    width, height = image.size
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, max(1, int(width * progress)), 3), fill=accent)
    title_font = game_font(max(11, height // 5))
    value_font = game_font(max(16, height // 3))
    small = game_font(max(9, height // 7), bold=False)
    draw.text((18, 10), title, font=title_font, fill=accent)
    draw.text((18, height - 26), DIFFICULTY_LABELS[difficulty], font=small, fill="#9aa8b7")
    return draw, value_font, small
