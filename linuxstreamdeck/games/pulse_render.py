"""Pillow frames for Pulse Memory."""

from __future__ import annotations

from PIL import Image, ImageDraw

from ..core.icons import RENDER_LOCK
from ..device import renderer
from .common import PHASE_COUNTDOWN, PHASE_LOBBY, PHASE_RESULTS
from .pulse_memory import PHASE_INPUT, PHASE_ROUND_PAUSE, PHASE_SHOWING, PulseSnapshot
from .rendering import (
    BG,
    CYAN,
    GOLD,
    INK,
    RED,
    draw_hud_header,
    game_color,
    lobby_control,
)


def _pulse_key(
    size: tuple[int, int],
    index: int,
    count: int,
    active: bool,
    wrong: bool,
) -> Image.Image:
    width, height = size
    color = RED if wrong else game_color(index, count, light=0.62)
    image = Image.new("RGB", size, color if active or wrong else "#151d27")
    draw = ImageDraw.Draw(image)
    radius = max(6, min(width, height) // (3 if active else 4))
    cx, cy = width // 2, height // 2
    draw.ellipse(
        (cx - radius, cy - radius, cx + radius, cy + radius),
        fill="#ffffff" if active else color,
        outline=color,
        width=max(2, min(width, height) // 18),
    )
    if not active and not wrong:
        inner = max(3, radius // 2)
        draw.ellipse((cx - inner, cy - inner, cx + inner, cy + inner), fill="#202b37")
    if wrong:
        stroke = max(3, min(width, height) // 14)
        draw.line((width * 0.28, height * 0.28, width * 0.72, height * 0.72), fill=INK, width=stroke)
        draw.line((width * 0.72, height * 0.28, width * 0.28, height * 0.72), fill=INK, width=stroke)
    return image


def render_pulse_keys(
    snapshot: PulseSnapshot,
    size: tuple[int, int],
) -> tuple[Image.Image, ...]:
    with RENDER_LOCK:
        images = []
        for index in range(snapshot.layout.key_count):
            if snapshot.phase in (PHASE_LOBBY, PHASE_RESULTS):
                best = f"Best {snapshot.high_score}" if snapshot.high_score else "No best"
                image = lobby_control(snapshot, index, size, best)
                if image is None:
                    if snapshot.phase == PHASE_RESULTS:
                        image = renderer.compose(
                            size=size,
                            bg="#3d2028" if snapshot.wrong_key is not None else "#173244",
                            center_text=str(snapshot.score),
                            label="Sequence",
                            text_color=GOLD if snapshot.new_high_score else INK,
                        )
                    else:
                        image = _pulse_key(size, index, snapshot.layout.key_count, index == snapshot.layout.start_key, False)
            elif snapshot.phase == PHASE_COUNTDOWN:
                image = renderer.compose(
                    size=size,
                    bg="#123444" if index == snapshot.layout.start_key else BG,
                    center_text=str(snapshot.countdown) if index == snapshot.layout.start_key else "",
                    text_color=CYAN,
                )
            else:
                image = _pulse_key(
                    size,
                    index,
                    snapshot.layout.key_count,
                    index == snapshot.active_key,
                    index == snapshot.wrong_key,
                )
            images.append(image)
        return tuple(images)


def pulse_hud(snapshot: PulseSnapshot, size: tuple[int, int]) -> Image.Image:
    with RENDER_LOCK:
        image = Image.new("RGB", size, BG)
        draw, value_font, small = draw_hud_header(
            image, "PULSE MEMORY", snapshot.difficulty, snapshot.progress, CYAN
        )
        width, height = size
        sequence = f"SEQUENCE  {snapshot.sequence_length}"
        if snapshot.phase == PHASE_INPUT:
            state = f"REPEAT  {snapshot.input_position}/{snapshot.sequence_length}"
        elif snapshot.phase == PHASE_SHOWING:
            state = "WATCH"
        elif snapshot.phase == PHASE_ROUND_PAUSE:
            state = "CORRECT"
        else:
            state = f"BEST  {snapshot.high_score or '--'}"
        draw.text(((width - draw.textlength(sequence, font=value_font)) / 2, 12), sequence, font=value_font, fill=INK)
        draw.text((width - draw.textlength(state, font=value_font) - 18, 12), state, font=value_font, fill=GOLD if state == "CORRECT" else INK)
        best = f"BEST  {snapshot.high_score or '--'}"
        draw.text((width - draw.textlength(best, font=small) - 18, height - 26), best, font=small, fill=GOLD)
        return image
