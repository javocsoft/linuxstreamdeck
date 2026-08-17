"""Pillow frames for Circuit Breaker."""

from __future__ import annotations

from PIL import Image, ImageDraw

from ..core.icons import RENDER_LOCK
from ..device import renderer
from .circuit_breaker import CircuitSnapshot
from .common import PHASE_LOBBY, PHASE_RESULTS
from .rendering import (
    BG,
    CYAN,
    GOLD,
    GREEN,
    INK,
    draw_hud_header,
    lobby_control,
)


def _cell(size: tuple[int, int], lit: bool, pressed: bool) -> Image.Image:
    width, height = size
    background = "#0d3741" if lit else "#111820"
    image = Image.new("RGB", size, background)
    draw = ImageDraw.Draw(image)
    line = "#37dff2" if lit else "#293946"
    trace_width = max(2, min(width, height) // 26)
    centre = (width // 2, height // 2)
    draw.line((0, centre[1], width, centre[1]), fill=line, width=trace_width)
    draw.line((centre[0], 0, centre[0], height), fill=line, width=trace_width)
    radius = max(7, min(width, height) // 5)
    draw.ellipse(
        (
            centre[0] - radius,
            centre[1] - radius,
            centre[0] + radius,
            centre[1] + radius,
        ),
        fill="#baf8ff" if lit else "#17242d",
        outline="#ffffff" if lit else "#4b6574",
        width=max(2, trace_width),
    )
    for x, y in ((centre[0], 5), (centre[0], height - 6),
                 (5, centre[1]), (width - 6, centre[1])):
        dot = max(2, trace_width)
        draw.ellipse((x - dot, y - dot, x + dot, y + dot), fill=line)
    if pressed:
        draw.rounded_rectangle(
            (2, 2, width - 3, height - 3),
            radius=max(5, width // 10),
            outline=GOLD,
            width=max(2, min(width, height) // 16),
        )
    return image


def render_circuit_keys(
    snapshot: CircuitSnapshot,
    size: tuple[int, int],
) -> tuple[Image.Image, ...]:
    with RENDER_LOCK:
        images = []
        best = f"Best {snapshot.high_score}" if snapshot.high_score else "No best"
        for index in range(snapshot.layout.key_count):
            if snapshot.phase in (PHASE_LOBBY, PHASE_RESULTS):
                image = lobby_control(snapshot, index, size, best)
                if image is None:
                    if snapshot.phase == PHASE_RESULTS:
                        image = renderer.compose(
                            size=size,
                            bg="#12392f",
                            center_text=str(snapshot.moves),
                            label="NEW BEST" if snapshot.new_high_score else "Moves",
                            text_color=GOLD if snapshot.new_high_score else INK,
                        )
                    else:
                        image = _cell(size, (index + snapshot.layout.columns) % 3 == 0, False)
            else:
                image = _cell(
                    size,
                    snapshot.lights[index],
                    index == snapshot.last_pressed,
                )
            images.append(image)
        return tuple(images)


def circuit_hud(snapshot: CircuitSnapshot, size: tuple[int, int]) -> Image.Image:
    with RENDER_LOCK:
        image = Image.new("RGB", size, BG)
        draw, value_font, small = draw_hud_header(
            image, "CIRCUIT BREAKER", snapshot.difficulty, snapshot.progress, CYAN
        )
        width, height = size
        moves = f"MOVES  {snapshot.moves}"
        lights = f"LIGHTS  {sum(snapshot.lights)}"
        draw.text(((width - draw.textlength(moves, font=value_font)) / 2, 12), moves, font=value_font, fill=INK)
        draw.text((width - draw.textlength(lights, font=value_font) - 18, 12), lights, font=value_font, fill=GREEN if not any(snapshot.lights) else INK)
        best = f"BEST  {snapshot.high_score or '--'}"
        draw.text((width - draw.textlength(best, font=small) - 18, height - 26), best, font=small, fill=GOLD)
        return image
