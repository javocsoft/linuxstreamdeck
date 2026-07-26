"""Offscreen frames for the physical Stream Deck startup animation."""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..core.icons import RENDER_LOCK

GRID_COLUMNS = 5
TITLE = "LinuxStreamDeck"
# Shorter forms for decks with too few keys to spell the full name, longest
# first. A Mini has six keys, so it gets "Linux" rather than the fragment
# "LinuxS" that simply cutting the name produced.
TITLE_FORMS = (TITLE, "LinuxDeck", "Linux", "Deck")

_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/firasans/FiraSans-Bold.ttf",
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
)


@dataclass(frozen=True)
class AnimationFrame:
    """One full-deck frame and the hardware state used to display it."""

    images: tuple[Image.Image, ...]
    brightness: int
    delay: float
    stage: str


def startup_frames(
    key_count: int,
    key_size: tuple[int, int],
    target_brightness: int,
    columns: int = GRID_COLUMNS,
):
    """Yield a short wake, title and fade sequence for a key grid."""
    count = max(1, int(key_count))
    key_width, key_height = (max(1, int(value)) for value in key_size)
    cols = max(1, min(int(columns), count))
    rows = math.ceil(count / cols)
    canvas_size = (cols * key_width, rows * key_height)
    target = max(0, min(100, int(target_brightness)))
    frames = []

    with RENDER_LOCK:
        for index in range(8):
            progress = index / 7
            canvas = _energy_canvas(canvas_size, progress)
            frames.append(
                _frame(
                    canvas,
                    count,
                    (key_width, key_height),
                    cols,
                    _scaled_brightness(target, 0.22 + 0.58 * _ease(progress)),
                    0.055,
                    "wake",
                )
            )

        for index in range(6):
            progress = index / 5
            canvas = _burst_canvas(canvas_size, progress)
            frames.append(
                _frame(
                    canvas,
                    count,
                    (key_width, key_height),
                    cols,
                    _scaled_brightness(
                        target,
                        0.80 + 0.20 * math.sin(progress * math.pi / 2),
                    ),
                    0.055,
                    "burst",
                )
            )

        for index in range(6):
            progress = index / 5
            canvas = _title_canvas(
                canvas_size,
                reveal=_ease(progress),
                shimmer=progress,
                columns=cols,
                rows=rows,
            )
            frames.append(
                _frame(
                    canvas,
                    count,
                    (key_width, key_height),
                    cols,
                    _scaled_brightness(target, 0.92 + 0.08 * progress),
                    0.060,
                    "title",
                )
            )

        title = _title_canvas(
            canvas_size,
            reveal=1.0,
            shimmer=1.0,
            pulse=0.5,
            columns=cols,
            rows=rows,
        )
        for index in range(5):
            progress = index / 4
            pulse = 0.5 + 0.5 * math.sin(progress * math.pi)
            canvas = _title_canvas(
                canvas_size,
                reveal=1.0,
                shimmer=progress,
                pulse=pulse,
                columns=cols,
                rows=rows,
            )
            frames.append(
                _frame(
                    canvas,
                    count,
                    (key_width, key_height),
                    cols,
                    _scaled_brightness(target, 0.94 + 0.06 * pulse),
                    0.095,
                    "hold",
                )
            )

        black = Image.new("RGB", canvas_size, "#000000")
        for index in range(7):
            progress = (index + 1) / 7
            canvas = Image.blend(title, black, _ease(progress))
            frames.append(
                _frame(
                    canvas,
                    count,
                    (key_width, key_height),
                    cols,
                    _scaled_brightness(target, 1.0 - 0.78 * progress),
                    0.060,
                    "fade",
                )
            )

        frames.append(
            _frame(
                black,
                count,
                (key_width, key_height),
                cols,
                _scaled_brightness(target, 0.18),
                0.070,
                "black",
            )
        )

    yield from frames


def title_for(cells: int) -> str:
    """The longest form of the name that fits in `cells` keys, or "".

    Cutting the full name to length spelled fragments like "LinuxS" on a
    six-key Mini. A shorter but complete word says far more.
    """
    for text in TITLE_FORMS:
        if len(text) <= cells:
            return text
    return ""


def title_layout(columns: int, rows: int) -> list[tuple[int, str]]:
    """Which key each character of the name lands on, centered in the grid.

    Returned in reading order, so the reveal still runs left to right whatever
    the deck's shape. Shared with the `linuxstreamdeck` screen saver, which
    would otherwise be a black screen on a deck too small for the full name.
    """
    columns = max(1, columns)
    rows = max(1, rows)
    title = title_for(columns * rows)
    if not title:
        return []
    needed_rows = -(-len(title) // columns)
    top = (rows - needed_rows) // 2
    placed: list[tuple[int, str]] = []
    for position, character in enumerate(title):
        # The block is centered vertically, but each row starts at the left:
        # these are a wrapped word, not centered lines, and a lone trailing
        # character floating in the middle of a row reads as a mistake.
        placed.append(
            ((top + position // columns) * columns + position % columns,
             character)
        )
    return placed


def _frame(
    canvas: Image.Image,
    key_count: int,
    key_size: tuple[int, int],
    columns: int,
    brightness: int,
    delay: float,
    stage: str,
) -> AnimationFrame:
    width, height = key_size
    images = tuple(
        canvas.crop(
            (
                (index % columns) * width,
                (index // columns) * height,
                (index % columns + 1) * width,
                (index // columns + 1) * height,
            )
        )
        for index in range(key_count)
    )
    return AnimationFrame(images, brightness, delay, stage)


def _energy_canvas(size: tuple[int, int], progress: float) -> Image.Image:
    width, height = size
    canvas = _base_canvas(size, energy=0.35 + progress * 0.25)
    glow = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(glow)

    center_x = int(-width * 0.18 + progress * width * 1.36)
    center_y = int(
        height * 0.5
        + math.sin(progress * math.pi * 2) * height * 0.20
    )
    radius = max(width, height) // 3
    draw.ellipse(
        (
            center_x - radius,
            center_y - radius,
            center_x + radius,
            center_y + radius,
        ),
        fill=(32, 164, 255, 145),
    )

    ribbon_x = int(-width * 0.35 + progress * width * 1.70)
    draw.line(
        (ribbon_x - height, height, ribbon_x + height, 0),
        fill=(86, 212, 255, 180),
        width=max(4, height // 22),
    )
    draw.line(
        (ribbon_x - height - width // 8, height, ribbon_x + height - width // 8, 0),
        fill=(92, 92, 255, 105),
        width=max(8, height // 12),
    )
    glow = glow.filter(ImageFilter.GaussianBlur(max(6, height // 14)))
    canvas = Image.alpha_composite(canvas.convert("RGBA"), glow)

    sparks = Image.new("RGBA", size, (0, 0, 0, 0))
    spark_draw = ImageDraw.Draw(sparks)
    for seed in range(12):
        x = int((seed * 79 + progress * width * 1.9) % (width + 30) - 15)
        y = int((seed * 43 + seed * seed * 3) % height)
        radius = 1 + seed % 3
        alpha = 80 + (seed * 17) % 130
        spark_draw.ellipse(
            (x - radius, y - radius, x + radius, y + radius),
            fill=(160, 232, 255, alpha),
        )
    return Image.alpha_composite(canvas, sparks).convert("RGB")


def _burst_canvas(size: tuple[int, int], progress: float) -> Image.Image:
    width, height = size
    canvas = _base_canvas(size, energy=0.55)
    glow = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(glow)
    center = (width // 2, height // 2)

    outer = int(min(width, height) * (0.08 + progress * 0.70))
    core = max(4, int(height * (0.10 - progress * 0.055)))
    draw.ellipse(
        (
            center[0] - core,
            center[1] - core,
            center[0] + core,
            center[1] + core,
        ),
        fill=(178, 239, 255, round(210 - progress * 120)),
    )
    for offset, alpha, color in (
        (0, 175, (91, 218, 255)),
        (max(4, height // 16), 115, (90, 120, 255)),
        (max(10, height // 8), 55, (42, 82, 190)),
    ):
        radius = outer + offset
        draw.ellipse(
            (
                center[0] - radius,
                center[1] - radius,
                center[0] + radius,
                center[1] + radius,
            ),
            outline=(*color, alpha),
            width=max(2, height // 36),
        )

    for ray in range(18):
        angle = ray * math.tau / 18 + progress * 0.35
        inner = outer * 0.24
        length = outer * (0.75 + (ray % 4) * 0.08)
        draw.line(
            (
                center[0] + math.cos(angle) * inner,
                center[1] + math.sin(angle) * inner,
                center[0] + math.cos(angle) * length,
                center[1] + math.sin(angle) * length,
            ),
            fill=(105, 215, 255, 65 + (ray % 3) * 28),
            width=max(1, height // 72),
        )

    glow = glow.filter(ImageFilter.GaussianBlur(max(3, height // 32)))
    return Image.alpha_composite(canvas.convert("RGBA"), glow).convert("RGB")


def _title_canvas(
    size: tuple[int, int],
    reveal: float,
    shimmer: float,
    pulse: float = 0.0,
    columns: int = GRID_COLUMNS,
    rows: int = 3,
) -> Image.Image:
    width, height = size
    canvas = _base_canvas(size, energy=0.62 + pulse * 0.10).convert("RGBA")
    columns = max(1, columns)
    rows = max(1, rows)
    cell_width = width // columns
    cell_height = height // rows
    font = _fit_font(
        "W",
        int(cell_width * 0.72),
        max(18, int(cell_height * 0.62)),
    )
    text_mask = Image.new("L", size, 0)
    mask_draw = ImageDraw.Draw(text_mask)
    reveal_position = reveal * (len(TITLE) + 3) - 2
    for index, (cell, character) in enumerate(title_layout(columns, rows)):
        local_reveal = _ease(reveal_position - index)
        if local_reveal <= 0:
            continue
        bbox = mask_draw.textbbox((0, 0), character, font=font)
        character_width = bbox[2] - bbox[0]
        character_height = bbox[3] - bbox[1]
        column = cell % columns
        row = cell // columns
        text_x = (
            column * cell_width
            + (cell_width - character_width) // 2
            - bbox[0]
        )
        text_y = (
            row * cell_height
            + (cell_height - character_height) // 2
            - bbox[1]
        )
        mask_draw.text(
            (text_x, text_y),
            character,
            font=font,
            fill=round(255 * local_reveal),
        )

    glow_mask = text_mask.filter(ImageFilter.GaussianBlur(max(3, height // 28)))
    glow = Image.new("RGBA", size, (42, 153, 255, 0))
    glow.putalpha(glow_mask.point(lambda value: int(value * 0.70)))
    canvas = Image.alpha_composite(canvas, glow)

    text_gradient = Image.new("RGBA", size, (0, 0, 0, 0))
    gradient_draw = ImageDraw.Draw(text_gradient)
    for y in range(height):
        ratio = y / max(1, height - 1)
        gradient_draw.line(
            (0, y, width, y),
            fill=(
                int(218 - ratio * 75),
                int(248 - ratio * 45),
                255,
                255,
            ),
        )
    text_gradient.putalpha(text_mask)
    canvas = Image.alpha_composite(canvas, text_gradient)

    accent = Image.new("RGBA", size, (0, 0, 0, 0))
    accent_draw = ImageDraw.Draw(accent)
    for index, (cell, _character) in enumerate(title_layout(columns, rows)):
        local_reveal = _ease(reveal_position - index)
        if local_reveal <= 0:
            continue
        column = cell % columns
        row = cell // columns
        line_width = int(cell_width * 0.42)
        line_x = column * cell_width + (cell_width - line_width) // 2
        line_y = (row + 1) * cell_height - max(8, cell_height // 7)
        accent_draw.rounded_rectangle(
            (
                line_x,
                line_y,
                line_x + line_width,
                line_y + max(2, cell_height // 36),
            ),
            radius=2,
            fill=(
                70,
                201,
                255,
                round((95 + pulse * 80) * local_reveal),
            ),
        )
    shimmer_x = int(-width * 0.15 + shimmer * width * 1.30)
    accent_draw.line(
        (shimmer_x - height // 2, height, shimmer_x + height // 2, 0),
        fill=(222, 250, 255, 95),
        width=max(5, height // 14),
    )
    accent = accent.filter(ImageFilter.GaussianBlur(max(2, height // 48)))
    return Image.alpha_composite(canvas, accent).convert("RGB")


def _base_canvas(size: tuple[int, int], energy: float) -> Image.Image:
    width, height = size
    canvas = Image.new("RGB", size)
    draw = ImageDraw.Draw(canvas)
    for y in range(height):
        ratio = y / max(1, height - 1)
        draw.line(
            (0, y, width, y),
            fill=(
                int(3 + energy * 9 + ratio * 3),
                int(7 + energy * 18 + ratio * 4),
                int(18 + energy * 42 + ratio * 10),
            ),
        )

    grid = Image.new("RGBA", size, (0, 0, 0, 0))
    grid_draw = ImageDraw.Draw(grid)
    spacing = max(18, height // 5)
    for x in range(-height, width + height, spacing):
        grid_draw.line(
            (x, height, x + height, 0),
            fill=(67, 139, 210, 13),
            width=1,
        )
    return Image.alpha_composite(canvas.convert("RGBA"), grid).convert("RGB")


def _scaled_brightness(target: int, factor: float) -> int:
    if target <= 0:
        return 0
    return max(1, min(target, round(target * factor)))


def _ease(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return value * value * (3.0 - 2.0 * value)


@lru_cache(maxsize=24)
def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(
                    path,
                    size,
                    layout_engine=ImageFont.Layout.BASIC,
                )
            except Exception:
                continue
    return ImageFont.load_default()


def _fit_font(
    text: str,
    max_width: int,
    max_size: int,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for size in range(max_size, 11, -1):
        font = _font(size)
        bbox = font.getbbox(text)
        if bbox[2] - bbox[0] <= max_width:
            return font
    return _font(12)
