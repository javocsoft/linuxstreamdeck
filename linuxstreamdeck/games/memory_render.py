"""Pillow frames for Memory Match."""

from __future__ import annotations

import math

from PIL import Image, ImageDraw

from ..core.icons import RENDER_LOCK
from ..device import renderer
from .memory_match import MemorySnapshot
from .common import PHASE_LOBBY, PHASE_RESULTS
from .rendering import (
    BG,
    CYAN,
    GOLD,
    GREEN,
    INK,
    draw_hud_header,
    game_color,
    lobby_control,
)


def _regular_polygon(cx: float, cy: float, radius: float, sides: int, offset: float = 0.0):
    return [
        (
            cx + math.cos(offset + math.tau * point / sides) * radius,
            cy + math.sin(offset + math.tau * point / sides) * radius,
        )
        for point in range(sides)
    ]


def _hidden_card(size: tuple[int, int]) -> Image.Image:
    width, height = size
    image = Image.new("RGB", size, "#182431")
    draw = ImageDraw.Draw(image)
    pad = max(4, min(width, height) // 12)
    draw.rounded_rectangle((pad, pad, width - pad, height - pad), radius=max(5, pad), fill="#21384a", outline="#3b7891", width=max(2, pad // 2))
    spacing = max(8, min(width, height) // 4)
    dot = max(2, spacing // 7)
    for y in range(spacing // 2, height, spacing):
        for x in range(spacing // 2, width, spacing):
            draw.polygon(((x, y - dot), (x + dot, y), (x, y + dot), (x - dot, y)), fill="#35a5c0")
    return image


def _symbol_card(size: tuple[int, int], symbol: int, matched: bool) -> Image.Image:
    width, height = size
    color = game_color(symbol, 16, light=0.56)
    image = Image.new("RGB", size, "#17352f" if matched else "#eef5f8")
    draw = ImageDraw.Draw(image)
    pad = max(4, min(width, height) // 12)
    draw.rounded_rectangle((pad, pad, width - pad, height - pad), radius=max(5, pad), outline=GREEN if matched else color, width=max(3, pad // 2))
    cx, cy = width / 2, height / 2
    radius = min(width, height) * 0.25
    shape = symbol % 8
    if shape == 0:
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=color)
    elif shape == 1:
        draw.rectangle((cx - radius, cy - radius, cx + radius, cy + radius), fill=color)
    elif shape == 2:
        draw.polygon(_regular_polygon(cx, cy, radius * 1.15, 3, -math.pi / 2), fill=color)
    elif shape == 3:
        draw.polygon(_regular_polygon(cx, cy, radius * 1.2, 4, math.pi / 4), fill=color)
    elif shape == 4:
        thick = radius * 0.42
        draw.rounded_rectangle((cx - thick, cy - radius, cx + thick, cy + radius), radius=3, fill=color)
        draw.rounded_rectangle((cx - radius, cy - thick, cx + radius, cy + thick), radius=3, fill=color)
    elif shape == 5:
        points = []
        for point in range(10):
            angle = -math.pi / 2 + math.pi * point / 5
            distance = radius if point % 2 == 0 else radius * 0.42
            points.append((cx + math.cos(angle) * distance, cy + math.sin(angle) * distance))
        draw.polygon(points, fill=color)
    elif shape == 6:
        stroke = max(4, round(radius * 0.34))
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=color, width=stroke)
    else:
        draw.polygon(_regular_polygon(cx, cy, radius * 1.05, 6), fill=color)
        inner = radius * 0.42
        draw.ellipse((cx - inner, cy - inner, cx + inner, cy + inner), fill="#eef5f8")
    if symbol >= 8:
        mark = max(3, min(width, height) // 18)
        draw.ellipse((width - pad - mark * 2, pad + mark, width - pad, pad + mark * 3), fill=color)
    return image


def render_memory_keys(
    snapshot: MemorySnapshot,
    size: tuple[int, int],
) -> tuple[Image.Image, ...]:
    with RENDER_LOCK:
        cards = {card.index: card for card in snapshot.cards}
        images = []
        for index in range(snapshot.layout.key_count):
            if snapshot.phase in (PHASE_LOBBY, PHASE_RESULTS):
                best = f"Best {snapshot.high_score}" if snapshot.high_score else "No best"
                image = lobby_control(snapshot, index, size, best)
                if image is None:
                    if snapshot.phase == PHASE_RESULTS:
                        image = renderer.compose(
                            size=size,
                            bg="#17352f",
                            center_text=str(snapshot.moves),
                            label="NEW BEST" if snapshot.new_high_score else "Moves",
                            text_color=GOLD if snapshot.new_high_score else INK,
                        )
                    else:
                        image = _hidden_card(size)
            elif index == snapshot.status_key:
                image = renderer.compose(
                    size=size,
                    bg="#20303c",
                    center_text=str(snapshot.moves),
                    label=f"Pairs {snapshot.pairs_found}/{snapshot.pair_count}",
                    text_color=INK,
                )
            else:
                card = cards[index]
                image = (
                    _hidden_card(size)
                    if card.state == "hidden"
                    else _symbol_card(size, card.symbol, card.state == "matched")
                )
            images.append(image)
        return tuple(images)


def memory_hud(snapshot: MemorySnapshot, size: tuple[int, int]) -> Image.Image:
    with RENDER_LOCK:
        image = Image.new("RGB", size, BG)
        draw, value_font, small = draw_hud_header(
            image, "MEMORY MATCH", snapshot.difficulty, snapshot.progress, CYAN
        )
        width, height = size
        moves = f"MOVES  {snapshot.moves}"
        pairs = f"PAIRS  {snapshot.pairs_found}/{snapshot.pair_count}"
        draw.text(((width - draw.textlength(moves, font=value_font)) / 2, 12), moves, font=value_font, fill=INK)
        draw.text((width - draw.textlength(pairs, font=value_font) - 18, 12), pairs, font=value_font, fill=GREEN if snapshot.pairs_found == snapshot.pair_count else INK)
        best = f"BEST  {snapshot.high_score or '--'}"
        draw.text((width - draw.textlength(best, font=small) - 18, height - 26), best, font=small, fill=GOLD)
        return image
