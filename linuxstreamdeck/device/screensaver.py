"""Animated full-deck screen saver frames rendered offscreen with Pillow."""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..core.config import DEFAULT_SCREENSAVER, SCREENSAVER_IDS
from ..core.icons import RENDER_LOCK

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
        "hal_9000": _hal_canvas,
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
    visible = min(len(TITLE), columns * rows)
    for index, character in enumerate(TITLE[:visible]):
        bbox = draw.textbbox((0, 0), character, font=font)
        character_width = bbox[2] - bbox[0]
        character_height = bbox[3] - bbox[1]
        column = index % columns
        row = index // columns
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
