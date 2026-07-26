"""Animated full-deck screen saver frames rendered offscreen with Pillow."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from ..core.config import DEFAULT_SCREENSAVER, SCREENSAVER_IDS
from ..core.icons import RENDER_LOCK
from .startup_animation import title_layout

GRID_COLUMNS = 5
FRAME_DELAY = 0.14
TITLE = "LinuxStreamDeck"

# Breathing rates of the HAL 9000 eye, in cycles per second: about nine seconds
# for the iris and four for the center point, so the two never lock together.
HAL_BREATH_CYCLES = 0.11
HAL_DOT_CYCLES = 0.23

_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/firasans/FiraSans-Bold.ttf",
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
)

# Fonts that may carry half-width katakana, for the Matrix Code rain. None of
# them is a dependency: `_matrix_alphabet()` falls back to Latin and digits when
# the machine has no Japanese font, so the style always renders.
_CJK_FONT_CANDIDATES = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansJP-Regular.otf",
    "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
    "/usr/share/fonts/truetype/vlgothic/VL-Gothic-Regular.ttf",
    "/usr/share/fonts/truetype/takao-gothic/TakaoPGothic.ttf",
    "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",
    "/usr/share/fonts/OTF/NotoSansCJK-Regular.ttc",
)

# The film's glyph set: half-width katakana, digits and a few symbols.
MATRIX_KATAKANA = "".join(chr(code) for code in range(0xFF66, 0xFF9E))
MATRIX_SYMBOLS = "0123456789:=*+-<>|"
# Latin shapes narrow enough to read as code when no katakana is installed.
MATRIX_FALLBACK = "ACDEFHIJKLMNPRSTUVWXYZ"
# How fast a single cell swaps its glyph, in changes per second. Each cell gets
# its own rate inside this range, so the screen mutates cell by cell instead of
# every glyph changing on the same tick.
MATRIX_CHURN_RANGE = (1.6, 4.6)

# Ember Field. Its noise is generated once from this seed and then only
# scrolled, so a given `elapsed` always paints the same flame.
EMBER_SEED = 0x5EED
EMBER_FLARE_CYCLES = 0.13

# Hyperspace. Stars are spread by the golden angle so no two share a spoke.
HYPERSPACE_STARS = 130
HYPERSPACE_GOLDEN_ANGLE = math.pi * (3.0 - math.sqrt(5.0))
HYPERSPACE_SURGE_CYCLES = 0.08
HYPERSPACE_RINGS = 11        # tunnel walls rushing out of the vanishing point
HYPERSPACE_SEGMENTS = 56     # points per ring; enough that a warp reads smooth
HYPERSPACE_SWIRL = 1.5       # radians a star bends over its whole flight
HYPERSPACE_TWIST = 2.4       # radians the tunnel turns between near and far
HYPERSPACE_ABERRATION = 0.005  # chromatic split, as a fraction of the canvas

# Split-Flap Board. Every word is laid out one character per key, padded or cut
# to whatever grid the deck actually has.
SPLIT_FLAP_WORDS = (
    "LINUXSTREAMDECK",
    "STAND BY FOR INPUT",
    "ALL SYSTEMS READY",
    "READY FOR TAKEOFF",
)
# What a deck too small for those spells instead. Truncating a longer word gave
# a six-key Mini "LINUXS" and "STANDB", and a board that never becomes readable
# is worse than a short word that does.
SPLIT_FLAP_SHORT_WORDS = (
    "LINUX",
    "READY",
    "ON AIR",
    "LIVE",
)
SPLIT_FLAP_ALPHABET = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.:-!"
SPLIT_FLAP_SPIN = 2.6      # seconds of riffling before the last cell settles
SPLIT_FLAP_HOLD = 3.4      # seconds the finished word stays up
SPLIT_FLAP_RATE = 5.5      # how many flaps a cell still has to turn, squared


@dataclass(frozen=True)
class ScreenSaverFrame:
    """One frame covering every key plus its device brightness."""

    images: tuple[Image.Image, ...]
    brightness: int
    delay: float = FRAME_DELAY


def screensaver_frame(
    style: str,
    elapsed: float,
    key_count: int,
    key_size: tuple[int, int],
    intensity: int,
    columns: int = GRID_COLUMNS,
) -> ScreenSaverFrame:
    """Render one deterministic frame of a selected screen saver."""
    count = max(1, int(key_count))
    key_width, key_height = (max(1, int(value)) for value in key_size)
    cols = max(1, min(int(columns), count))
    rows = math.ceil(count / cols)
    canvas_size = (cols * key_width, rows * key_height)
    style = style if style in SCREENSAVER_IDS else DEFAULT_SCREENSAVER
    elapsed = max(0.0, float(elapsed))
    intensity = max(5, min(100, int(intensity)))

    renderers = {
        "neon_pipes": _pipes_canvas,
        "digital_rain": _digital_rain_canvas,
        "aurora_flow": _aurora_canvas,
        "orbital_core": _orbital_canvas,
        "circuit_pulse": _circuit_canvas,
        "ember_field": _ember_canvas,
        "hyperspace": _hyperspace_canvas,
        "matrix_code": _matrix_canvas,
        "hal_9000": _hal_canvas,
        "split_flap": lambda size, now: _split_flap_canvas(
            size,
            now,
            cols,
            rows,
        ),
        "linuxstreamdeck": lambda size, now: _title_canvas(
            size,
            now,
            cols,
            rows,
        ),
    }
    brightness_factors = {
        "neon_pipes": 0.82 + 0.12 * _wave(elapsed * 0.36),
        "digital_rain": 0.68 + 0.09 * _wave(elapsed * 0.28),
        "aurora_flow": 0.72 + 0.13 * _wave(elapsed * 0.20),
        "orbital_core": 0.78 + 0.12 * _wave(elapsed * 0.32),
        "circuit_pulse": 0.70 + 0.18 * _wave(elapsed * 0.42),
        # Both ride their own animation's wave, so the device brightness
        # surges with the flame and with the jump instead of against them.
        "ember_field": 0.68 + 0.18 * _wave(elapsed * EMBER_FLARE_CYCLES),
        "hyperspace": 0.70 + 0.18 * _wave(elapsed * HYPERSPACE_SURGE_CYCLES),
        "matrix_code": 0.66 + 0.10 * _wave(elapsed * 0.24),
        "split_flap": 0.66 + 0.08 * _wave(elapsed * 0.18),
        # Deliberately on the same slow wave as the iris, so the device
        # brightness breathes with the eye instead of against it.
        "hal_9000": 0.52 + 0.24 * _wave(elapsed * HAL_BREATH_CYCLES),
        "linuxstreamdeck": 0.58 + 0.18 * _wave(elapsed * 0.20),
    }

    with RENDER_LOCK:
        canvas = renderers[style](canvas_size, elapsed)
        images = _split_canvas(
            canvas,
            count,
            (key_width, key_height),
            cols,
        )
    brightness = max(
        1,
        min(intensity, round(intensity * brightness_factors[style])),
    )
    return ScreenSaverFrame(images=images, brightness=brightness)


def _split_canvas(
    canvas: Image.Image,
    key_count: int,
    key_size: tuple[int, int],
    columns: int,
) -> tuple[Image.Image, ...]:
    width, height = key_size
    return tuple(
        canvas.crop(
            (
                (index % columns) * width,
                (index // columns) * height,
                (index % columns + 1) * width,
                (index // columns + 1) * height,
            )
        ).convert("RGB")
        for index in range(key_count)
    )


def _dark_canvas(
    size: tuple[int, int],
    top: tuple[int, int, int] = (2, 6, 16),
    bottom: tuple[int, int, int] = (5, 12, 28),
) -> Image.Image:
    width, height = size
    canvas = Image.new("RGB", size, "black")
    draw = ImageDraw.Draw(canvas)
    for y in range(height):
        ratio = y / max(1, height - 1)
        color = tuple(
            round(start + (end - start) * ratio)
            for start, end in zip(top, bottom)
        )
        draw.line((0, y, width, y), fill=color)
    return canvas


def _pipes_canvas(size: tuple[int, int], elapsed: float) -> Image.Image:
    width, height = size
    canvas = _dark_canvas(size, (1, 3, 10), (3, 7, 17)).convert("RGBA")
    pipes = (
        (
            ((-0.08, 0.22), (0.18, 0.22), (0.18, 0.70), (0.48, 0.70),
             (0.48, 0.34), (0.78, 0.34), (0.78, 0.82), (1.08, 0.82)),
            (31, 220, 255),
            0.00,
        ),
        (
            ((0.12, -0.08), (0.12, 0.48), (0.37, 0.48), (0.37, 0.14),
             (0.68, 0.14), (0.68, 0.60), (0.94, 0.60), (0.94, 1.08)),
            (154, 92, 255),
            0.34,
        ),
        (
            ((-0.08, 0.92), (0.26, 0.92), (0.26, 0.60), (0.58, 0.60),
             (0.58, 0.90), (0.86, 0.90), (0.86, 0.46), (1.08, 0.46)),
            (38, 255, 165),
            0.68,
        ),
    )
    glow = Image.new("RGBA", size, (0, 0, 0, 0))
    sharp = Image.new("RGBA", size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    sharp_draw = ImageDraw.Draw(sharp)
    pipe_width = max(5, height // 24)

    for raw_path, color, offset in pipes:
        path = tuple((x * width, y * height) for x, y in raw_path)
        sharp_draw.line(
            path,
            fill=(*color, 25),
            width=max(2, pipe_width // 2),
            joint="curve",
        )
        progress = (elapsed * 0.075 + offset) % 1.0
        trail = _path_trail(path, progress, 0.28, 38)
        for segment in trail:
            if len(segment) < 2:
                continue
            glow_draw.line(
                segment,
                fill=(*color, 190),
                width=pipe_width * 3,
                joint="curve",
            )
            sharp_draw.line(
                segment,
                fill=(*color, 225),
                width=pipe_width,
                joint="curve",
            )
            for point in segment[1:-1]:
                radius = pipe_width * 0.72
                sharp_draw.ellipse(
                    (
                        point[0] - radius,
                        point[1] - radius,
                        point[0] + radius,
                        point[1] + radius,
                    ),
                    fill=(*color, 225),
                )
        head = _point_on_path(path, progress)
        glow_draw.ellipse(
            (
                head[0] - pipe_width * 3,
                head[1] - pipe_width * 3,
                head[0] + pipe_width * 3,
                head[1] + pipe_width * 3,
            ),
            fill=(*color, 210),
        )
        sharp_draw.ellipse(
            (
                head[0] - pipe_width,
                head[1] - pipe_width,
                head[0] + pipe_width,
                head[1] + pipe_width,
            ),
            fill=(225, 252, 255, 245),
            outline=(*color, 255),
            width=max(1, pipe_width // 3),
        )

    glow = glow.filter(ImageFilter.GaussianBlur(max(5, height // 22)))
    return Image.alpha_composite(
        Image.alpha_composite(canvas, glow),
        sharp,
    ).convert("RGB")


def _digital_rain_canvas(size: tuple[int, int], elapsed: float) -> Image.Image:
    width, height = size
    canvas = _dark_canvas(size, (0, 5, 10), (1, 12, 20)).convert("RGBA")
    grid = Image.new("RGBA", size, (0, 0, 0, 0))
    grid_draw = ImageDraw.Draw(grid)
    spacing = max(16, width // 22)
    for x in range(0, width, spacing):
        grid_draw.line((x, 0, x, height), fill=(40, 185, 205, 11))
    for y in range(0, height, spacing):
        grid_draw.line((0, y, width, y), fill=(40, 185, 205, 8))
    canvas = Image.alpha_composite(canvas, grid)

    trails = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(trails)
    columns = max(12, width // max(12, height // 10))
    column_width = width / columns
    dash_height = max(3, height // 38)
    gap = dash_height * 2.4
    for column in range(columns):
        speed = 22 + (column * 13 % 31)
        head = (elapsed * speed + column * 37 + column * column * 3) % (
            height + height * 0.65
        ) - height * 0.18
        x = int((column + 0.5) * column_width)
        for tail in range(10):
            y = head - tail * gap
            if not (-dash_height <= y <= height + dash_height):
                continue
            alpha = max(0, 225 - tail * 22)
            color = (
                174 if tail == 0 else 25,
                255 if tail < 2 else 204,
                255 if tail < 3 else 185,
                alpha,
            )
            half = max(1, int(column_width * (0.16 + (tail % 3) * 0.05)))
            draw.rounded_rectangle(
                (x - half, y, x + half, y + dash_height),
                radius=max(1, dash_height // 3),
                fill=color,
            )
    glow = trails.filter(ImageFilter.GaussianBlur(max(2, height // 70)))
    glow.putalpha(glow.getchannel("A").point(lambda value: int(value * 0.48)))
    return Image.alpha_composite(
        Image.alpha_composite(canvas, glow),
        trails,
    ).convert("RGB")


def _aurora_canvas(size: tuple[int, int], elapsed: float) -> Image.Image:
    width, height = size
    canvas = _dark_canvas(size, (2, 4, 18), (4, 11, 28)).convert("RGBA")
    lights = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(lights)
    waves = (
        ((36, 224, 205), 0.31, 0.00, 0.42),
        ((57, 143, 255), 0.39, 2.10, 0.54),
        ((170, 78, 255), 0.27, 4.15, 0.65),
    )
    samples = max(80, width // 3)
    for color, amplitude, offset, base in waves:
        points = []
        for index in range(samples + 1):
            ratio = index / samples
            x = ratio * width
            y = height * (
                base
                + math.sin(ratio * math.tau * 1.25 + elapsed * 0.48 + offset)
                * amplitude
                * 0.24
                + math.sin(ratio * math.tau * 2.7 - elapsed * 0.23 + offset)
                * amplitude
                * 0.10
            )
            points.append((x, y))
        band_depth = height * (0.15 + amplitude * 0.18)
        band = points + [
            (x, min(height * 1.12, y + band_depth))
            for x, y in reversed(points)
        ]
        draw.polygon(band, fill=(*color, 76))
        for layer in range(6, 0, -1):
            draw.line(
                points,
                fill=(*color, 58 + layer * 18),
                width=max(3, height // (12 + layer * 2)),
                joint="curve",
            )
    lights = lights.filter(ImageFilter.GaussianBlur(max(5, height // 24)))
    canvas = Image.alpha_composite(canvas, lights)

    stars = Image.new("RGBA", size, (0, 0, 0, 0))
    star_draw = ImageDraw.Draw(stars)
    for seed in range(28):
        x = (seed * 83 + seed * seed * 7) % width
        y = (seed * 47 + 11) % height
        pulse = _wave(elapsed * 0.17 + seed * 0.071)
        alpha = round(22 + pulse * 68)
        radius = 1 if seed % 5 else 2
        star_draw.ellipse(
            (x - radius, y - radius, x + radius, y + radius),
            fill=(165, 224, 255, alpha),
        )
    return Image.alpha_composite(canvas, stars).convert("RGB")


def _orbital_canvas(size: tuple[int, int], elapsed: float) -> Image.Image:
    width, height = size
    canvas = _dark_canvas(size, (1, 4, 15), (4, 8, 24)).convert("RGBA")
    center = (width * 0.5, height * 0.5)
    unit = min(width, height)
    orbit = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(orbit)

    for seed in range(24):
        x = (seed * 109 + seed * seed * 5) % width
        y = (seed * 61 + seed * 7) % height
        draw.point((x, y), fill=(92, 158, 220, 35 + seed % 4 * 14))

    for index, radius_factor in enumerate((0.20, 0.34, 0.49, 0.66)):
        radius = unit * radius_factor
        angle = elapsed * (0.28 + index * 0.06) * (-1 if index % 2 else 1)
        box = (
            center[0] - radius * 1.55,
            center[1] - radius * 0.72,
            center[0] + radius * 1.55,
            center[1] + radius * 0.72,
        )
        start = math.degrees(angle) % 360
        draw.ellipse(
            box,
            outline=(55, 139, 218, 34 + index * 10),
            width=max(1, height // 90),
        )
        draw.arc(
            box,
            start=start,
            end=start + 74 + index * 13,
            fill=(63, 219, 255, 180),
            width=max(2, height // 48),
        )
        particle_angle = angle + index * 1.7
        particle = (
            center[0] + math.cos(particle_angle) * radius * 1.55,
            center[1] + math.sin(particle_angle) * radius * 0.72,
        )
        particle_radius = max(2, height // 48)
        draw.ellipse(
            (
                particle[0] - particle_radius,
                particle[1] - particle_radius,
                particle[0] + particle_radius,
                particle[1] + particle_radius,
            ),
            fill=(180, 246, 255, 235),
        )

    sweep = elapsed * 0.36
    length = unit * 0.72
    draw.line(
        (
            center,
            (
                center[0] + math.cos(sweep) * length,
                center[1] + math.sin(sweep) * length,
            ),
        ),
        fill=(80, 206, 255, 70),
        width=max(1, height // 90),
    )
    glow = orbit.filter(ImageFilter.GaussianBlur(max(4, height // 38)))
    canvas = Image.alpha_composite(canvas, glow)
    canvas = Image.alpha_composite(canvas, orbit)

    core = Image.new("RGBA", size, (0, 0, 0, 0))
    core_draw = ImageDraw.Draw(core)
    pulse = 0.78 + 0.22 * _wave(elapsed * 0.55)
    radius = unit * 0.075 * pulse
    core_draw.ellipse(
        (
            center[0] - radius,
            center[1] - radius,
            center[0] + radius,
            center[1] + radius,
        ),
        fill=(175, 244, 255, 240),
    )
    core_glow = core.filter(ImageFilter.GaussianBlur(max(8, height // 18)))
    return Image.alpha_composite(
        Image.alpha_composite(canvas, core_glow),
        core,
    ).convert("RGB")


def _circuit_canvas(size: tuple[int, int], elapsed: float) -> Image.Image:
    width, height = size
    canvas = _dark_canvas(size, (1, 7, 12), (3, 13, 20)).convert("RGBA")
    routes = (
        ((-0.05, 0.18), (0.22, 0.18), (0.22, 0.43), (0.51, 0.43),
         (0.51, 0.72), (0.80, 0.72), (0.80, 1.05)),
        ((0.10, -0.05), (0.10, 0.62), (0.35, 0.62), (0.35, 0.88),
         (0.66, 0.88), (0.66, 0.34), (1.05, 0.34)),
        ((-0.05, 0.87), (0.16, 0.87), (0.16, 0.32), (0.43, 0.32),
         (0.43, 0.58), (0.91, 0.58), (0.91, -0.05)),
        ((0.31, -0.05), (0.31, 0.12), (0.73, 0.12), (0.73, 0.46),
         (1.05, 0.46)),
    )
    routes = tuple(
        tuple((x * width, y * height) for x, y in route)
        for route in routes
    )
    circuit = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(circuit)
    line_width = max(2, height // 72)

    for route in routes:
        draw.line(
            route,
            fill=(41, 160, 172, 63),
            width=line_width,
            joint="curve",
        )
        for point in route[1:-1]:
            radius = max(2, line_width * 1.5)
            draw.ellipse(
                (
                    point[0] - radius,
                    point[1] - radius,
                    point[0] + radius,
                    point[1] + radius,
                ),
                outline=(60, 198, 205, 90),
                width=1,
            )

    pulses = Image.new("RGBA", size, (0, 0, 0, 0))
    pulse_draw = ImageDraw.Draw(pulses)
    colors = (
        (60, 235, 255),
        (63, 255, 180),
        (132, 112, 255),
        (59, 181, 255),
    )
    for index, route in enumerate(routes):
        progress = (elapsed * (0.11 + index * 0.009) + index * 0.21) % 1.0
        point = _point_on_path(route, progress)
        color = colors[index]
        radius = max(3, height // 36)
        pulse_draw.ellipse(
            (
                point[0] - radius * 2.5,
                point[1] - radius * 2.5,
                point[0] + radius * 2.5,
                point[1] + radius * 2.5,
            ),
            fill=(*color, 170),
        )
        draw.ellipse(
            (
                point[0] - radius,
                point[1] - radius,
                point[0] + radius,
                point[1] + radius,
            ),
            fill=(205, 255, 255, 235),
        )
    pulses = pulses.filter(ImageFilter.GaussianBlur(max(4, height // 30)))
    return Image.alpha_composite(
        Image.alpha_composite(canvas, pulses),
        circuit,
    ).convert("RGB")


@lru_cache(maxsize=8)
def _noise_strip(seed: int, cells: int, size: tuple[int, int]) -> Image.Image:
    """Smooth value noise, twice `size` tall and seamless top to bottom.

    Built once per layer and then only scrolled, because `screensaver_frame` is
    a pure function of `elapsed`: `Image.effect_noise()` draws from Pillow's own
    generator and answers differently on every call, which would both flicker
    and stop the same moment painting the same frame.

    The grid repeats its first row and column in its last, so upscaling it makes
    a tile that meets itself without a seam; stacking that tile twice gives a
    strip any vertical offset can be cropped out of.
    """
    width, height = size
    rng = random.Random(seed)
    values = [[rng.randrange(256) for _ in range(cells)] for _ in range(cells)]
    raw = bytes(
        values[y % cells][x % cells]
        for y in range(cells + 1)
        for x in range(cells + 1)
    )
    tile = Image.frombytes("L", (cells + 1, cells + 1), raw).resize(
        size, Image.Resampling.BICUBIC
    )
    strip = Image.new("L", (width, height * 2))
    strip.paste(tile, (0, 0))
    strip.paste(tile, (0, height))
    return strip


@lru_cache(maxsize=4)
def _ember_falloff(size: tuple[int, int]) -> Image.Image:
    """Full strength along the bottom edge, nothing at the top."""
    width, height = size
    ramp = Image.new("L", (1, height))
    draw = ImageDraw.Draw(ramp)
    for y in range(height):
        # Curved, so the flame keeps a hot base and thin licking tips, while
        # still climbing far enough to reach the top row of keys.
        draw.point((0, y), fill=round(255 * (y / max(1, height - 1)) ** 1.65))
    return ramp.resize(size, Image.Resampling.BILINEAR)


@lru_cache(maxsize=1)
def _ember_palette() -> bytes:
    """Intensity to flame colour: black, deep red, orange, yellow, white."""
    stops = (
        (0, (0, 0, 0)),
        (40, (26, 0, 0)),
        (96, (150, 20, 4)),
        (160, (238, 104, 12)),
        (212, (255, 194, 58)),
        (255, (255, 248, 220)),
    )
    table = bytearray()
    for value in range(256):
        lower = max(s for s in stops if s[0] <= value)
        upper = min((s for s in stops if s[0] >= value), default=stops[-1])
        span = upper[0] - lower[0]
        ratio = 0.0 if span == 0 else (value - lower[0]) / span
        table.extend(
            round(start + (end - start) * ratio)
            for start, end in zip(lower[1], upper[1])
        )
    return bytes(table)


def _ember_canvas(size: tuple[int, int], elapsed: float) -> Image.Image:
    """Flame climbing the deck: three noise layers scrolling at three speeds."""
    width, height = size
    layers = (
        # seed, cells, speed in canvas heights per second, weight
        (EMBER_SEED, 7, 0.30, 1.0),
        (EMBER_SEED + 1, 13, 0.46, 0.55),
        (EMBER_SEED + 2, 23, 0.72, 0.30),
    )
    flame: Image.Image | None = None
    for seed, cells, speed, weight in layers:
        strip = _noise_strip(seed, cells, size)
        offset = int(elapsed * speed * height) % height
        layer = strip.crop((0, offset, width, offset + height))
        flame = layer if flame is None else Image.blend(flame, layer, weight / 2)

    flame = ImageChops.multiply(flame, _ember_falloff(size))
    # A slow swell, so the whole bed of fire surges and settles.
    flare = 1.30 + 0.55 * _wave(elapsed * EMBER_FLARE_CYCLES)
    flame = flame.point(lambda value: min(255, round(value * flare)))
    fire = flame.convert("P")
    fire.putpalette(_ember_palette())
    canvas = fire.convert("RGBA")

    # Embers lifting off the flame into the dark above it, each on its own loop
    # and drifting sideways as it climbs.
    # Drawn as a mask and colored afterwards, never as blurred RGBA: blurring
    # RGBA mixes the black of its transparent pixels into the colour, which
    # turned these embers into olive rings instead of warm points of light.
    trace = Image.new("L", size, 0)
    draw = ImageDraw.Draw(trace)
    radius = max(1.5, height / 90)
    for index in range(20):
        rise = 0.20 + (index % 7) * 0.05
        progress = (elapsed * rise + index * 0.137) % 1.0
        x = (
            index * 97.3 + math.sin(elapsed * 0.7 + index) * width * 0.04
        ) % width
        y = height * (1.0 - progress)
        # Brightest just off the flame, cooling to nothing near the top.
        level = (1.0 - progress) ** 1.4
        draw.ellipse(
            (x - radius, y - radius * 1.6, x + radius, y + radius * 1.6),
            fill=round(255 * level),
        )
    trace = trace.filter(ImageFilter.GaussianBlur(max(1, height // 110)))
    sparks = Image.new("RGBA", size, (0, 0, 0, 0))
    sparks.paste((255, 206, 96, 255), (0, 0), trace)
    return Image.alpha_composite(canvas, sparks).convert("RGB")


def _scatter(index: int, salt: int) -> float:
    """A stable, well-spread 0..1 for an index, with no linear drift.

    Hyperspace needs this rather than `index * some_fraction`: its angles are
    already a linear sequence, so a linear phase correlates with them and every
    star lands on one curve — at `elapsed` 0 the whole field collapsed into a
    spiral, and the saver starts at 0 every time it wakes.
    """
    return (((index + 1) * 2654435761) ^ (salt * 40503)) % 65521 / 65521.0


def _hyperspace_depth(progress: float, reach: float) -> float:
    """How far out something at `progress` through its flight has travelled.

    Raised to a power on purpose: it crawls near the vanishing point and tears
    past at the rim, which is what stretches a moving point into a streak
    without faking motion blur.
    """
    return reach * max(0.0, progress) ** 2.4


def _hyperspace_tunnel(
    size: tuple[int, int],
    elapsed: float,
    center: tuple[float, float],
    reach: float,
    unit: float,
    surge: float,
) -> Image.Image:
    """The wormhole wall: rings rushing outward, warped and twisting."""
    tunnel = Image.new("RGB", size, (0, 0, 0))
    draw = ImageDraw.Draw(tunnel)
    for ring in range(HYPERSPACE_RINGS):
        progress = (
            elapsed * 0.16 * surge + ring / HYPERSPACE_RINGS
        ) % 1.0
        radius = _hyperspace_depth(progress, reach)
        if radius < unit * 0.03:
            continue
        # The far end of the tunnel is turned further round than the near end,
        # so the whole throat reads as twisting rather than as flat rings.
        twist = HYPERSPACE_TWIST * progress + elapsed * 0.22
        points = []
        for step in range(HYPERSPACE_SEGMENTS + 1):
            angle = math.tau * step / HYPERSPACE_SEGMENTS + twist
            # Two out-of-step waves, so the mouth ripples instead of pulsing.
            warp = (
                1.0
                + 0.13 * math.sin(3 * angle + elapsed * 1.15)
                + 0.07 * math.sin(5 * angle - elapsed * 0.8)
            )
            points.append(
                (
                    center[0] + math.cos(angle) * radius * warp,
                    center[1] + math.sin(angle) * radius * warp,
                )
            )
        # Fades in from the throat and back out as it sweeps past, so a ring
        # reads as passing the viewer rather than as a contour line on black.
        fade = 1.0 if progress < 0.70 else max(0.0, (1.0 - progress) / 0.30)
        level = min(1.0, progress * 3.2) * fade
        # Violet deep in the throat, turning cyan as it rushes past.
        draw.line(
            points,
            fill=(
                round((130 - 60 * progress) * level),
                round((45 + 190 * progress) * level),
                round((220 + 35 * progress) * level),
            ),
            width=max(2, round(unit * 0.022 * (0.35 + progress))),
            joint="curve",
        )
    # Its own glow, so the wall has body instead of being a wire outline.
    return ImageChops.add(
        tunnel,
        tunnel.filter(ImageFilter.GaussianBlur(max(3, round(unit * 0.045)))),
    )


def _hyperspace_streaks(
    size: tuple[int, int],
    elapsed: float,
    center: tuple[float, float],
    reach: float,
    unit: float,
    surge: float,
) -> Image.Image:
    """Stars bent into spiral streaks, split into colour by the distortion."""
    trails = Image.new("L", size, 0)
    draw = ImageDraw.Draw(trails)
    for index in range(HYPERSPACE_STARS):
        base = index * HYPERSPACE_GOLDEN_ANGLE
        speed = 0.19 + _scatter(index, 2) * 0.34
        progress = (elapsed * speed * surge + _scatter(index, 1)) % 1.0
        # Sampled along its path rather than drawn straight: the angle keeps
        # turning as it flies, so the streak curves the way the tunnel does.
        points = []
        for step in range(5):
            moment = progress - 0.075 * step / 4
            angle = base + HYPERSPACE_SWIRL * max(0.0, moment)
            radius = _hyperspace_depth(moment, reach)
            points.append(
                (
                    center[0] + math.cos(angle) * radius,
                    center[1] + math.sin(angle) * radius,
                )
            )
        # Fades in out of the vanishing point instead of popping into being.
        draw.line(
            points,
            fill=round(255 * min(1.0, progress * 3.4)),
            width=max(1, round(unit * 0.008 * (0.35 + progress))),
            joint="curve",
        )
    return trails


def _hyperspace_aberration(mask: Image.Image, offset: int) -> Image.Image:
    """Split one mask into colour by scaling its channels apart.

    Blue focuses short and red long, so red lands slightly outside the streak
    and blue slightly inside. Doing it to the whole layer at once costs two
    resizes instead of drawing every streak three times over.
    """
    width, height = mask.size

    def scaled(delta: int) -> Image.Image:
        target = (max(1, width + delta), max(1, height + delta))
        out = Image.new("L", mask.size, 0)
        out.paste(
            mask.resize(target, Image.Resampling.BILINEAR),
            ((width - target[0]) // 2, (height - target[1]) // 2),
        )
        return out

    return Image.merge(
        "RGB",
        (
            scaled(offset).point(lambda value: round(value * 0.82)),
            mask.point(lambda value: round(value * 0.94)),
            scaled(-offset),
        ),
    )


def _hyperspace_canvas(size: tuple[int, int], elapsed: float) -> Image.Image:
    """A wormhole: a warping tunnel with stars spiralling out of its throat."""
    width, height = size
    center = (width * 0.5, height * 0.5)
    # Far enough that a star leaves by the corner, not by the nearest edge.
    reach = math.hypot(width, height) * 0.5
    unit = min(width, height)
    surge = 0.72 + 0.60 * _wave(elapsed * HYPERSPACE_SURGE_CYCLES)

    # Everything is additive on black, which is both how light behaves and why
    # these layers can be blurred safely: black is the neutral value, so no
    # transparent-pixel colour can bleed into them.
    tunnel = _hyperspace_tunnel(size, elapsed, center, reach, unit, surge)
    trails = _hyperspace_streaks(size, elapsed, center, reach, unit, surge)
    canvas = ImageChops.add(
        tunnel,
        _hyperspace_aberration(
            trails, max(1, round(unit * HYPERSPACE_ABERRATION))
        ),
    )

    core = Image.new("RGB", size, (0, 0, 0))
    core_radius = unit * 0.055 * (0.8 + 0.35 * _wave(elapsed * 0.45))
    ImageDraw.Draw(core).ellipse(
        _circle(center, core_radius), fill=(120, 190, 255)
    )
    canvas = ImageChops.add(
        canvas, core.filter(ImageFilter.GaussianBlur(max(4, round(unit * 0.09))))
    )

    # One bloom pass over the finished frame, swelling at the top of the surge
    # so the jump itself flares rather than just running faster.
    flare = _wave(elapsed * HYPERSPACE_SURGE_CYCLES) ** 3
    bloom = canvas.filter(ImageFilter.GaussianBlur(max(3, round(unit * 0.055))))
    return ImageChops.add(
        canvas, bloom.point(lambda value: round(value * (0.30 + 0.45 * flare)))
    )


def _flap_words(count: int) -> tuple[str, ...]:
    """The words a board of `count` modules can actually spell.

    The full phrases are exactly fifteen characters, for the 15-key deck they
    were written for. A smaller board gets the short set rather than a cut-off
    fragment of a long one.
    """
    full = tuple(word.replace(" ", "") for word in SPLIT_FLAP_WORDS)
    if all(len(word) <= count for word in full):
        return full
    short = tuple(word.replace(" ", "") for word in SPLIT_FLAP_SHORT_WORDS)
    fitting = tuple(word for word in short if len(word) <= count)
    # A deck too small even for those still shows something rather than nothing.
    return fitting or (full[0][:max(1, count)],)


def _flap_word(elapsed: float, count: int) -> str:
    """The word on the board right now, centered in whatever grid this deck has."""
    cycle = SPLIT_FLAP_SPIN + SPLIT_FLAP_HOLD
    words = _flap_words(count)
    return words[int(elapsed / cycle) % len(words)].center(count)


def _flap_state(
    index: int, count: int, moment: float, letters: str
) -> tuple[str, str, float]:
    """What module `index` shows at `moment`: outgoing, incoming and flip.

    `flip` runs 0 to 1 through one leaf turning, and is exactly 1 when the
    module has stopped on its character.
    """
    # Staggered, so the word assembles across the deck rather than landing all
    # at once.
    settles = SPLIT_FLAP_SPIN * (0.30 + 0.70 * index / max(1, count - 1))
    target = SPLIT_FLAP_ALPHABET.find(letters[index])
    target = target if target >= 0 else 0
    remaining = max(0.0, settles - moment)
    # Squared, so a module slows down as it closes on its character.
    position = remaining * remaining * SPLIT_FLAP_RATE
    turns = int(position)
    size_of = len(SPLIT_FLAP_ALPHABET)
    return (
        SPLIT_FLAP_ALPHABET[(target - turns - 1) % size_of],
        SPLIT_FLAP_ALPHABET[(target - turns) % size_of],
        # Counts down to the target, so the last leaf still animates into place
        # and `position == 0` is the module at rest.
        1.0 - (position - turns),
    )


def _split_flap_canvas(
    size: tuple[int, int],
    elapsed: float,
    columns: int,
    rows: int,
) -> Image.Image:
    """One flap module per key, riffling to a word and settling left to right."""
    width, height = size
    canvas = Image.new("RGBA", size, (0, 0, 0, 255))
    cell_width = width // max(1, columns)
    cell_height = height // max(1, rows)
    count = max(1, columns * rows)

    letters = _flap_word(elapsed, count)
    moment = elapsed % (SPLIT_FLAP_SPIN + SPLIT_FLAP_HOLD)

    board = Image.new("RGBA", size, (0, 0, 0, 0))
    for index in range(count):
        outgoing, incoming, flip = _flap_state(index, count, moment, letters)
        board.paste(
            _flap_module(
                (cell_width, cell_height), outgoing, incoming, flip
            ),
            ((index % columns) * cell_width, (index // columns) * cell_height),
        )

    glow = board.filter(ImageFilter.GaussianBlur(max(2, cell_height // 14)))
    glow.putalpha(glow.getchannel("A").point(lambda value: int(value * 0.35)))
    return Image.alpha_composite(
        Image.alpha_composite(canvas, glow),
        board,
    ).convert("RGB")


def _flap_module(
    cell: tuple[int, int], outgoing: str, incoming: str, flip: float
) -> Image.Image:
    """One module: the new glyph above the seam, the old below, flap between.

    That is how a real board reads mid-turn — the leaf that is falling still
    carries the *old* character on its way down and the *new* one on its way
    back up — and it is what stops this looking like letters simply changing.
    """
    cell_width, cell_height = cell
    module = Image.new("RGBA", cell, (0, 0, 0, 0))
    draw = ImageDraw.Draw(module)
    inset = max(1, cell_height // 24)
    draw.rounded_rectangle(
        (inset, inset, cell_width - inset - 1, cell_height - inset - 1),
        radius=max(2, cell_height // 9),
        fill=(19, 15, 11, 255),
    )
    seam = cell_height // 2
    amber = (255, 176, 42, 255)

    top = _flap_glyph(incoming, cell).crop((0, 0, cell_width, seam))
    module.paste(amber, (0, 0), top)
    # At rest both halves are the same character. The old one only shows below
    # the seam while the leaf carrying the new one is still on its way down —
    # leaving it there when settled spelled every word with mismatched halves.
    bottom = _flap_glyph(
        incoming if flip >= 1.0 else outgoing, cell
    ).crop((0, seam, cell_width, cell_height))
    module.paste(amber, (0, seam), bottom)

    # The leaf in motion, foreshortening to nothing as it passes horizontal.
    if flip < 1.0:
        if flip < 0.5:
            leaf = _flap_glyph(outgoing, cell).crop((0, 0, cell_width, seam))
            leaf_height = max(1, round(seam * (1.0 - flip * 2)))
            offset = seam - leaf_height
        else:
            leaf = _flap_glyph(incoming, cell).crop(
                (0, seam, cell_width, cell_height)
            )
            leaf_height = max(1, round(seam * (flip - 0.5) * 2))
            offset = seam
        leaf = leaf.resize(
            (cell_width, leaf_height), Image.Resampling.BILINEAR
        )
        panel = Image.new("RGBA", (cell_width, leaf_height), (26, 20, 14, 255))
        module.paste(panel, (0, offset))
        module.paste(amber, (0, offset), leaf)

    draw.line((0, seam, cell_width, seam), fill=(0, 0, 0, 255), width=1)
    return module


def _matrix_canvas(size: tuple[int, int], elapsed: float) -> Image.Image:
    """Columns of glyphs raining down black, each with a white-hot leading cell.

    Unlike `digital_rain`, which is abstract cyan dashes, this one is actual
    characters: half-width katakana where a Japanese font is installed, Latin
    and digits where none is.
    """
    width, height = size
    canvas = Image.new("RGBA", size, (0, 0, 0, 255))
    # Twelve rows over the deck: denser reads more like the film, but the deck
    # is only 72 px per key and the glyphs stop being legible on the hardware.
    cell_height = max(8, height // 12)
    cell_width = max(6, round(cell_height * 0.74))
    columns = max(1, width // cell_width)
    rows = max(1, height // cell_height)
    alphabet = _matrix_alphabet()
    cell = (cell_width, cell_height)

    rain = Image.new("RGBA", size, (0, 0, 0, 0))
    for column in range(columns):
        # Everything about a column is derived from its index, so a given
        # `elapsed` always paints the same frame.
        speed = 3.4 + (column * 7 % 11) * 0.55        # cells per second
        trail = 7 + (column * 5 % 9)
        cycle = rows + trail + 5
        head = int((elapsed * speed + column * 3.7) % cycle) - trail
        x = column * cell_width

        for step in range(trail):
            row = head - step
            if not 0 <= row < rows:
                continue
            if step == 0:
                # The leading cell is almost white; it is what reads as the
                # front of the stream rather than just the brightest green.
                color = (208, 255, 214)
            else:
                fade = 1.0 - (step - 1) / max(1, trail - 1)
                color = (
                    round(24 * fade),
                    round(58 + 197 * fade),
                    round(28 + 58 * fade),
                )
            seed = column * 71 + row * 131
            low, high = MATRIX_CHURN_RANGE
            rate = low + (seed % 7) / 6 * (high - low)
            churn = int(elapsed * rate + seed % 13 * 0.31)
            glyph = alphabet[(seed + churn * 37) % len(alphabet)]
            rain.paste(
                (*color, 255),
                (x, row * cell_height),
                _matrix_glyph(glyph, cell),
            )

    # The film mirrors its glyphs. Flipping the finished layer does that for
    # every one of them at no cost: it also swaps the column order, which is
    # invisible because each column is seeded from its own index anyway.
    rain = rain.transpose(Image.Transpose.FLIP_LEFT_RIGHT)

    glow = rain.filter(ImageFilter.GaussianBlur(max(2, cell_height // 3)))
    glow.putalpha(glow.getchannel("A").point(lambda value: int(value * 0.55)))
    return Image.alpha_composite(
        Image.alpha_composite(canvas, glow),
        rain,
    ).convert("RGB")


def _hal_canvas(size: tuple[int, int], elapsed: float) -> Image.Image:
    """A single red camera eye, centered and still, breathing on pure black.

    The lens deliberately does not move or scan: what makes it recognisable is
    that it only ever watches. Everything animated here is brightness — the
    iris on a slow breath, the center point on a slower one of its own.
    """
    width, height = size
    canvas = Image.new("RGBA", size, (0, 0, 0, 255))
    center = (width * 0.5, height * 0.5)
    unit = min(width, height)
    breath = _wave(elapsed * HAL_BREATH_CYCLES)
    # The eye keeps most of its glow at the bottom of the breath, so it reads
    # as alive rather than as something switching on and off.
    iris_level = 0.66 + 0.34 * breath

    housing_radius = unit * 0.335
    iris_radius = unit * 0.300

    # Ambient bounce: the far, faint wash the eye throws across the rest of the
    # deck, so the keys around it are lit by it rather than cut out of black.
    # It breathes with the iris, which is what sells it as one light source.
    bounce = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(bounce).ellipse(
        _circle(center, unit * 0.64),
        fill=(255, 38, 20, round(88 * iris_level)),
    )
    bounce = bounce.filter(ImageFilter.GaussianBlur(max(12, round(unit * 0.34))))
    canvas = Image.alpha_composite(canvas, bounce)

    lens = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(lens)

    # Housing: the dark rim the lens sits in. Stepped from black at its edge to
    # a faint ember next to the iris; a flat disc with an outline read as a ring
    # drawn on top of the glow rather than as a body holding the eye.
    housing_steps = max(6, round(housing_radius - iris_radius))
    for step in range(housing_steps, 0, -1):
        ratio = step / housing_steps
        draw.ellipse(
            _circle(center, iris_radius + (housing_radius - iris_radius) * ratio),
            fill=(round(3 + 22 * (1.0 - ratio)), 3, 4, 255),
        )

    # Iris: concentric steps from a near-black rim to the hot middle. Drawn as
    # rings rather than one disc, which is what gives it depth.
    steps = max(12, round(iris_radius))
    for step in range(steps, 0, -1):
        ratio = step / steps
        radius = iris_radius * ratio
        # Bright core falling off quickly toward the rim.
        glow = (1.0 - ratio) ** 1.7
        draw.ellipse(
            _circle(center, radius),
            fill=(
                round((70 + 185 * glow) * iris_level),
                round((4 + 44 * glow) * iris_level),
                round((3 + 26 * glow) * iris_level),
                255,
            ),
        )

    # The dark fish-eye pupil the bright point sits in.
    pupil_radius = unit * 0.085
    draw.ellipse(
        _circle(center, pupil_radius),
        fill=(round(46 * iris_level), round(5 * iris_level), 4, 255),
    )
    canvas = Image.alpha_composite(canvas, lens)

    # Glare, composited *over* the finished lens rather than behind it. Behind
    # it, any spill bright enough to be seen turned the opaque housing into a
    # black cut-out ring; over it, the bloom washes across the rim exactly as a
    # camera sees it, and the lens blends into the light it is casting.
    glare = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(glare).ellipse(
        _circle(center, iris_radius * 0.92),
        fill=(255, 26, 12, round(120 * iris_level)),
    )
    glare = glare.filter(ImageFilter.GaussianBlur(max(8, round(unit * 0.11))))
    canvas = Image.alpha_composite(canvas, glare)

    dot_level = 0.45 + 0.55 * _wave(elapsed * HAL_DOT_CYCLES)
    dot_radius = max(1.5, unit * 0.028)
    point = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(point).ellipse(
        _circle(center, dot_radius * 3.0),
        fill=(255, 214, 96, round(150 * dot_level)),
    )
    point = point.filter(ImageFilter.GaussianBlur(max(2, round(unit * 0.035))))
    ImageDraw.Draw(point).ellipse(
        _circle(center, dot_radius),
        fill=(
            255,
            round(150 + 88 * dot_level),
            round(40 + 130 * dot_level),
            255,
        ),
    )
    return Image.alpha_composite(canvas, point).convert("RGB")


def _circle(
    center: tuple[float, float], radius: float
) -> tuple[float, float, float, float]:
    """The bounding box Pillow wants for a circle around `center`."""
    return (
        center[0] - radius,
        center[1] - radius,
        center[0] + radius,
        center[1] + radius,
    )


def _title_canvas(
    size: tuple[int, int],
    elapsed: float,
    columns: int,
    rows: int,
) -> Image.Image:
    width, height = size
    canvas = Image.new("RGBA", size, (0, 0, 0, 255))
    cell_width = width // max(1, columns)
    cell_height = height // max(1, rows)
    font = _fit_font(
        "W",
        int(cell_width * 0.70),
        max(18, int(cell_height * 0.62)),
    )
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    # Shared with the startup sequence: laid out for whatever grid exists, and
    # shortened rather than cut when the full name does not fit. The position
    # in reading order still drives the breathing offset, so the wave travels
    # along the word and not along the keys.
    for index, (cell, character) in enumerate(title_layout(columns, rows)):
        bbox = draw.textbbox((0, 0), character, font=font)
        character_width = bbox[2] - bbox[0]
        character_height = bbox[3] - bbox[1]
        column = cell % columns
        row = cell // columns
        x = (
            column * cell_width
            + (cell_width - character_width) // 2
            - bbox[0]
        )
        y = (
            row * cell_height
            + (cell_height - character_height) // 2
            - bbox[1]
        )
        local = _wave(elapsed * 0.18 - index * 0.035)
        draw.text(
            (x, y),
            character,
            font=font,
            fill=round(142 + local * 113),
        )

    glow_mask = mask.filter(ImageFilter.GaussianBlur(max(2, height // 48)))
    glow = Image.new("RGBA", size, (35, 154, 255, 0))
    glow.putalpha(glow_mask.point(lambda value: int(value * 0.36)))
    canvas = Image.alpha_composite(canvas, glow)
    letters = Image.new("RGBA", size, (191, 238, 255, 0))
    letters.putalpha(mask)
    return Image.alpha_composite(canvas, letters).convert("RGB")


def _path_lengths(
    points: tuple[tuple[float, float], ...],
) -> tuple[tuple[float, ...], float]:
    lengths = []
    total = 0.0
    for start, end in zip(points, points[1:]):
        total += math.hypot(end[0] - start[0], end[1] - start[1])
        lengths.append(total)
    return tuple(lengths), total


def _point_on_path(
    points: tuple[tuple[float, float], ...],
    progress: float,
) -> tuple[float, float]:
    lengths, total = _path_lengths(points)
    if total <= 0:
        return points[0]
    target = max(0.0, min(1.0, progress)) * total
    previous_length = 0.0
    for index, end_length in enumerate(lengths):
        if target <= end_length:
            segment_length = max(0.0001, end_length - previous_length)
            local = (target - previous_length) / segment_length
            start, end = points[index], points[index + 1]
            return (
                start[0] + (end[0] - start[0]) * local,
                start[1] + (end[1] - start[1]) * local,
            )
        previous_length = end_length
    return points[-1]


def _path_trail(
    points: tuple[tuple[float, float], ...],
    head: float,
    length: float,
    samples: int,
) -> tuple[tuple[tuple[float, float], ...], ...]:
    segments: list[list[tuple[float, float]]] = [[]]
    for index in range(samples + 1):
        raw = head - length + length * index / samples
        wrapped = raw % 1.0
        if index and wrapped < (raw - length / samples) % 1.0:
            segments.append([])
        segments[-1].append(_point_on_path(points, wrapped))
    return tuple(tuple(segment) for segment in segments if segment)


def _wave(cycles: float) -> float:
    return 0.5 + 0.5 * math.sin(cycles * math.tau)


def _glyph_ink(font, char: str) -> Image.Image:
    image = Image.new("L", (48, 48), 0)
    ImageDraw.Draw(image).text((8, 8), char, font=font, fill=255)
    return image


def _draws_katakana(font) -> bool:
    """Whether a font really has the glyph or is substituting `.notdef`.

    `getbbox()` cannot tell: a Latin-only font answers with a perfectly good
    box for any codepoint at all, because it measures the empty rectangle it
    puts there instead. So the glyph is drawn next to an unassigned private-use
    codepoint, which nothing has: if the font drew the same thing twice, it has
    neither, and accepting it would fill the rain with tofu boxes.
    """
    sample = _glyph_ink(font, MATRIX_KATAKANA[0])
    if sample.getbbox() is None:
        return False
    notdef = _glyph_ink(font, "")
    return ImageChops.difference(sample, notdef).getbbox() is not None


@lru_cache(maxsize=1)
def _matrix_font_path() -> str:
    """A local font that really has half-width katakana, or "" if none does."""
    for path in _CJK_FONT_CANDIDATES:
        if not Path(path).exists():
            continue
        try:
            font = ImageFont.truetype(
                path, 16, layout_engine=ImageFont.Layout.BASIC
            )
            usable = _draws_katakana(font)
        except Exception:
            continue
        if usable:
            return path
    return ""


@lru_cache(maxsize=1)
def _matrix_alphabet() -> str:
    """What the rain is made of, given the fonts this machine actually has."""
    katakana = MATRIX_KATAKANA if _matrix_font_path() else MATRIX_FALLBACK
    return katakana + MATRIX_SYMBOLS


@lru_cache(maxsize=16)
def _matrix_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = _matrix_font_path()
    if path:
        try:
            return ImageFont.truetype(
                path, size, layout_engine=ImageFont.Layout.BASIC
            )
        except Exception:
            pass
    return _font(size)


def _glyph_mask(char: str, cell: tuple[int, int], font) -> Image.Image:
    """One glyph centered in a cell, as a grayscale mask.

    Frames colorize these instead of drawing text: painting text per cell per
    frame is far too slow once a frame has hundreds of cells, and the alphabets
    involved are small enough that every glyph ever needed stays cached.
    """
    cell_width, cell_height = cell
    mask = Image.new("L", cell, 0)
    box = font.getbbox(char)
    if box is None:
        return mask
    ImageDraw.Draw(mask).text(
        (
            (cell_width - (box[2] - box[0])) / 2 - box[0],
            (cell_height - (box[3] - box[1])) / 2 - box[1],
        ),
        char,
        font=font,
        fill=255,
    )
    return mask


@lru_cache(maxsize=256)
def _matrix_glyph(char: str, cell: tuple[int, int]) -> Image.Image:
    return _glyph_mask(char, cell, _matrix_font(max(6, round(cell[1] * 0.92))))


@lru_cache(maxsize=256)
def _flap_glyph(char: str, cell: tuple[int, int]) -> Image.Image:
    return _glyph_mask(char, cell, _font(max(8, round(cell[1] * 0.62))))


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
