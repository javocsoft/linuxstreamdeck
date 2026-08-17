"""Pillow frames for Minesweeper."""

from __future__ import annotations

from PIL import Image, ImageDraw

from ..core.icons import RENDER_LOCK
from ..device import renderer
from .common import PHASE_LOBBY, PHASE_RESULTS
from .minesweeper import MineCellView, MinesweeperSnapshot
from .rendering import (
    BG,
    CYAN,
    GOLD,
    GREEN,
    INK,
    RED,
    draw_hud_header,
    game_font,
    lobby_control,
)

NUMBER_COLORS = {
    1: "#4aa8ff",
    2: "#45d88b",
    3: "#ff696f",
    4: "#a58aff",
    5: "#ff9a55",
    6: "#45dce0",
    7: "#f4f6f8",
    8: "#a7b1bc",
}


def _cell(size: tuple[int, int], cell: MineCellView) -> Image.Image:
    width, height = size
    if cell.state in ("hidden", "flagged"):
        image = Image.new("RGB", size, "#1b2d3a")
        draw = ImageDraw.Draw(image)
        pad = max(3, min(width, height) // 14)
        draw.rounded_rectangle(
            (pad, pad, width - pad, height - pad),
            radius=max(5, pad),
            fill="#274356",
            outline="#4d7890",
            width=max(2, pad // 2),
        )
        if cell.state == "flagged":
            pole_x = width * 0.42
            draw.line(
                (pole_x, height * 0.24, pole_x, height * 0.72),
                fill=INK,
                width=max(3, width // 20),
            )
            draw.polygon(
                (
                    (pole_x, height * 0.25),
                    (width * 0.72, height * 0.36),
                    (pole_x, height * 0.48),
                ),
                fill=GOLD,
            )
            draw.line(
                (width * 0.27, height * 0.74, width * 0.62, height * 0.74),
                fill=INK,
                width=max(3, width // 20),
            )
        return image

    background = "#5d2228" if cell.state in ("exploded", "wrong_flag") else "#202830"
    image = Image.new("RGB", size, background)
    draw = ImageDraw.Draw(image)
    pad = max(3, min(width, height) // 14)
    draw.rounded_rectangle(
        (pad, pad, width - pad, height - pad),
        radius=max(4, pad),
        outline="#394653",
        width=max(2, pad // 3),
    )
    if cell.state in ("mine", "exploded"):
        cx, cy = width // 2, height // 2
        radius = max(8, min(width, height) // 5)
        stroke = max(3, radius // 4)
        for x1, y1, x2, y2 in (
            (cx - radius * 1.5, cy, cx + radius * 1.5, cy),
            (cx, cy - radius * 1.5, cx, cy + radius * 1.5),
            (cx - radius, cy - radius, cx + radius, cy + radius),
            (cx + radius, cy - radius, cx - radius, cy + radius),
        ):
            draw.line((x1, y1, x2, y2), fill=RED if cell.state == "exploded" else INK, width=stroke)
        draw.ellipse(
            (cx - radius, cy - radius, cx + radius, cy + radius),
            fill="#ff695f" if cell.state == "exploded" else "#11151b",
            outline=GOLD if cell.state == "exploded" else "#66717d",
            width=max(2, stroke // 2),
        )
    elif cell.state == "wrong_flag":
        stroke = max(4, min(width, height) // 12)
        draw.line((width * 0.25, height * 0.25, width * 0.75, height * 0.75), fill=INK, width=stroke)
        draw.line((width * 0.75, height * 0.25, width * 0.25, height * 0.75), fill=INK, width=stroke)
    elif cell.adjacent:
        font = game_font(max(18, int(min(width, height) * 0.48)))
        text = str(cell.adjacent)
        box = draw.textbbox((0, 0), text, font=font)
        draw.text(
            ((width - (box[2] - box[0])) / 2, (height - (box[3] - box[1])) / 2 - box[1]),
            text,
            font=font,
            fill=NUMBER_COLORS.get(cell.adjacent, INK),
        )
    return image


def render_minesweeper_keys(
    snapshot: MinesweeperSnapshot,
    size: tuple[int, int],
) -> tuple[Image.Image, ...]:
    with RENDER_LOCK:
        cells = {cell.index: cell for cell in snapshot.cells}
        images = []
        best = f"Best {snapshot.high_score}s" if snapshot.high_score else "No best"
        for index in range(snapshot.layout.key_count):
            if snapshot.phase == PHASE_RESULTS:
                if index == snapshot.mode_key:
                    image = renderer.compose(
                        size=size,
                        bg="#17382e",
                        icon_path="mdi:replay",
                        label="Again",
                        icon_color=GREEN,
                    )
                elif index == snapshot.result_back_key:
                    image = renderer.compose(
                        size=size,
                        bg="#4a2028",
                        icon_path="mdi:arrow-left",
                        label="Back",
                        icon_color=INK,
                    )
                else:
                    image = _cell(size, cells[index])
            elif snapshot.phase == PHASE_LOBBY:
                image = lobby_control(snapshot, index, size, best)
                if image is None:
                    preview = MineCellView(index, "hidden", 0)
                    image = _cell(size, preview)
            elif index == snapshot.mode_key:
                image = renderer.compose(
                    size=size,
                    bg="#4a3820" if snapshot.flag_mode else "#173a45",
                    icon_path="mdi:flag" if snapshot.flag_mode else "mdi:shovel",
                    label="Flag" if snapshot.flag_mode else "Reveal",
                    badge=f"{snapshot.flags}/{snapshot.mine_count}",
                    icon_color=GOLD if snapshot.flag_mode else CYAN,
                )
            else:
                image = _cell(size, cells[index])
            images.append(image)
        return tuple(images)


def minesweeper_hud(snapshot: MinesweeperSnapshot, size: tuple[int, int]) -> Image.Image:
    with RENDER_LOCK:
        image = Image.new("RGB", size, BG)
        draw, value_font, small = draw_hud_header(
            image, "MINESWEEPER", snapshot.difficulty, snapshot.progress, GREEN
        )
        width, height = size
        timer = f"TIME  {snapshot.elapsed_seconds}s"
        flags = f"FLAGS  {snapshot.flags}/{snapshot.mine_count}"
        mode = "FLAG" if snapshot.flag_mode else "REVEAL"
        draw.text(((width - draw.textlength(timer, font=value_font)) / 2, 12), timer, font=value_font, fill=INK)
        draw.text((width - draw.textlength(flags, font=value_font) - 18, 12), flags, font=value_font, fill=GOLD)
        draw.text((width - draw.textlength(mode, font=small) - 18, height - 26), mode, font=small, fill=GOLD if snapshot.flag_mode else CYAN)
        return image
