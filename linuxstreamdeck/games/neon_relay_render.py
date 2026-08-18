"""Code-native neon circuit frames and Plus HUD for Neon Relay."""

from __future__ import annotations

import math

from PIL import Image, ImageDraw

from ..core.icons import RENDER_LOCK
from ..device import renderer
from .common import PHASE_LOBBY, PHASE_PLAYING, PHASE_RESULTS
from .neon_relay import (
    DIFFICULTIES,
    EAST,
    NORTH,
    PHASE_RECOVER,
    PHASE_SECTOR_CLEAR,
    PHASE_UPGRADE,
    SOUTH,
    WEST,
    NeonRelaySnapshot,
    tile_connections,
)
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

DECOY = "#243343"
DECOY_CORE = "#4a647a"
ROUTE = "#16d8ef"
ROUTE_CORE = "#bffaff"
MAGENTA = "#ef54ff"
VIOLET = "#7054ff"
SHIELD = "#58a6ff"
SURGE = "#ff8e43"


def _side_point(direction: int, size: tuple[int, int]) -> tuple[int, int]:
    width, height = size
    centre = (width // 2, height // 2)
    return {
        NORTH: (centre[0], -1),
        EAST: (width, centre[1]),
        SOUTH: (centre[0], height),
        WEST: (-1, centre[1]),
    }[int(direction)]


def _blend(left: str, right: str, amount: float) -> str:
    amount = max(0.0, min(1.0, float(amount)))
    first = tuple(int(left[index : index + 2], 16) for index in (1, 3, 5))
    second = tuple(int(right[index : index + 2], 16) for index in (1, 3, 5))
    values = tuple(round(a + (b - a) * amount) for a, b in zip(first, second))
    return "#" + "".join(f"{value:02x}" for value in values)


def _ambient_key(index: int, size: tuple[int, int]) -> Image.Image:
    image = Image.new("RGB", size, BG)
    draw = ImageDraw.Draw(image)
    width, height = size
    centre = (width // 2, height // 2)
    rotation = index % 4
    sides = tile_connections("corner" if index % 3 else "straight", rotation)
    for side in sides:
        draw.line(
            (_side_point(side, size), centre),
            fill="#172738",
            width=max(2, min(size) // 18),
        )
    draw.ellipse(
        (centre[0] - 2, centre[1] - 2, centre[0] + 2, centre[1] + 2),
        fill="#28465d",
    )
    return image


def _circuit_key(
    snapshot: NeonRelaySnapshot,
    index: int,
    size: tuple[int, int],
) -> Image.Image:
    tile = snapshot.tiles[index]
    width, height = size
    centre = (width // 2, height // 2)
    image = Image.new("RGB", size, "#090e16")
    draw = ImageDraw.Draw(image)

    # Subtle panel detail makes inactive keys belong to one machine without
    # competing with the route that has to be read at a glance.
    grid = max(8, min(size) // 6)
    for coordinate in range(grid, width, grid):
        draw.line((coordinate, 0, coordinate, height), fill="#0d1722", width=1)
    for coordinate in range(grid, height, grid):
        draw.line((0, coordinate, width, coordinate), fill="#0d1722", width=1)

    light = DIFFICULTIES[snapshot.difficulty].route_light
    if tile.on_route:
        accent = MAGENTA if snapshot.overdrive else ROUTE
        rail = _blend("#183546", accent, light * 0.58)
        core = _blend("#5d8593", ROUTE_CORE, light)
    else:
        rail = DECOY
        core = DECOY_CORE
    connectors = tile_connections(tile.kind, tile.rotation)
    outer = max(8, min(size) // 7)
    middle = max(5, min(size) // 12)
    inner = max(2, min(size) // 28)
    for side in connectors:
        endpoint = _side_point(side, size)
        draw.line((endpoint, centre), fill="#04070b", width=outer + 4)
        draw.line((endpoint, centre), fill=rail, width=outer)
        draw.line((endpoint, centre), fill=_blend(rail, core, 0.45), width=middle)
        draw.line((endpoint, centre), fill=core, width=inner)
    draw.ellipse(
        (
            centre[0] - middle,
            centre[1] - middle,
            centre[0] + middle,
            centre[1] + middle,
        ),
        fill=rail,
        outline=core,
        width=max(1, inner),
    )

    if tile.crystal and not tile.collected:
        radius = max(5, min(size) // 10)
        points = (
            (centre[0], centre[1] - radius),
            (centre[0] + radius, centre[1]),
            (centre[0], centre[1] + radius),
            (centre[0] - radius, centre[1]),
        )
        draw.polygon(points, fill=GOLD, outline="#fff1a8")
        draw.line(
            (centre[0], centre[1] - radius, centre[0], centre[1] + radius),
            fill="#fffbe8",
            width=1,
        )

    if index == snapshot.entry_key:
        _portal(draw, snapshot.entry_side, size, GOLD, inward=True)
    if index == snapshot.exit_key:
        _portal(draw, snapshot.exit_side, size, GREEN, inward=False)

    if index in snapshot.flashed_keys:
        draw.rounded_rectangle(
            (2, 2, width - 3, height - 3),
            radius=max(5, min(size) // 9),
            outline="#eaffff",
            width=max(2, min(size) // 24),
        )

    if index == snapshot.spark_key and snapshot.spark_incoming is not None:
        _spark(draw, snapshot, size)

    if index == snapshot.crashed_key and snapshot.phase in (PHASE_RECOVER, PHASE_RESULTS):
        radius = max(5, round(min(size) * (0.16 + snapshot.effect_progress * 0.42)))
        draw.ellipse(
            (
                centre[0] - radius,
                centre[1] - radius,
                centre[0] + radius,
                centre[1] + radius,
            ),
            outline=RED,
            width=max(2, min(size) // 18),
        )
        if snapshot.phase == PHASE_RECOVER:
            shield_radius = max(radius + 4, round(min(size) * 0.46))
            draw.arc(
                (
                    centre[0] - shield_radius,
                    centre[1] - shield_radius,
                    centre[0] + shield_radius,
                    centre[1] + shield_radius,
                ),
                210,
                510,
                fill=SHIELD,
                width=max(2, min(size) // 20),
            )

    if snapshot.phase == PHASE_SECTOR_CLEAR:
        distance = abs(index - snapshot.exit_key)
        wave = snapshot.effect_progress * (snapshot.layout.key_count + 4)
        if abs(distance - wave) < 2.2:
            draw.rounded_rectangle(
                (2, 2, width - 3, height - 3),
                radius=max(5, min(size) // 9),
                outline=GOLD if snapshot.perfect_sector else GREEN,
                width=max(2, min(size) // 18),
            )

    if snapshot.overdrive:
        draw.rounded_rectangle(
            (0, 0, width - 1, height - 1),
            radius=max(5, min(size) // 9),
            outline=MAGENTA,
            width=max(1, min(size) // 32),
        )

    if index == snapshot.entry_key and snapshot.phase == PHASE_PLAYING:
        font = game_font(max(8, height // 9), bold=False)
        draw.text((4, 3), f"S{snapshot.sector}", font=font, fill=GOLD)
    if index == snapshot.exit_key and snapshot.combo >= 2:
        font = game_font(max(8, height // 9), bold=False)
        text = f"x{snapshot.combo}"
        draw.text((width - draw.textlength(text, font=font) - 4, 3), text, font=font, fill=GREEN)
    return image


def _portal(
    draw: ImageDraw.ImageDraw,
    side: int,
    size: tuple[int, int],
    colour: str,
    *,
    inward: bool,
) -> None:
    width, height = size
    point = _side_point(side, size)
    radius = max(7, min(size) // 8)
    if side in (NORTH, SOUTH):
        box = (point[0] - radius, point[1] - radius, point[0] + radius, point[1] + radius)
    else:
        box = (point[0] - radius, point[1] - radius, point[0] + radius, point[1] + radius)
    draw.ellipse(box, outline=colour, width=max(2, min(size) // 20))
    centre = (width // 2, height // 2)
    start = point
    end = (
        round(point[0] * 0.72 + centre[0] * 0.28),
        round(point[1] * 0.72 + centre[1] * 0.28),
    )
    if not inward:
        start, end = end, start
    draw.line((start, end), fill=colour, width=max(2, min(size) // 28))


def _spark(
    draw: ImageDraw.ImageDraw,
    snapshot: NeonRelaySnapshot,
    size: tuple[int, int],
) -> None:
    centre = (size[0] // 2, size[1] // 2)
    incoming = _side_point(snapshot.spark_incoming, size)
    outgoing = (
        _side_point(snapshot.spark_outgoing, size)
        if snapshot.spark_outgoing is not None
        else centre
    )
    progress = snapshot.spark_progress
    if progress <= 0.5:
        amount = progress * 2.0
        start, end = incoming, centre
    else:
        amount = (progress - 0.5) * 2.0
        start, end = centre, outgoing
    point = (
        round(start[0] + (end[0] - start[0]) * amount),
        round(start[1] + (end[1] - start[1]) * amount),
    )
    colour = MAGENTA if snapshot.overdrive else "#d9ffff"
    for radius, fill in ((10, "#164b68"), (6, ROUTE), (3, colour)):
        scaled = max(2, round(radius * min(size) / 72))
        draw.ellipse(
            (point[0] - scaled, point[1] - scaled, point[0] + scaled, point[1] + scaled),
            fill=fill,
        )


def _upgrade_key(choice: str, size: tuple[int, int]) -> Image.Image:
    details = {
        "shield": ("mdi:shield-plus", "SHIELD", SHIELD, "+1 SAVE"),
        "stasis": ("mdi:snowflake", "STASIS", CYAN, "SLOWER"),
        "surge": ("mdi:lightning-bolt", "SURGE", SURGE, "+300"),
    }
    icon, label, colour, badge = details[choice]
    return renderer.compose(
        size=size,
        bg=_blend(BG, colour, 0.22),
        icon_path=icon,
        label=label,
        badge=badge,
        icon_color=colour,
        text_color=INK,
        active=True,
    )


def render_neon_relay_keys(
    snapshot: NeonRelaySnapshot,
    size: tuple[int, int],
) -> tuple[Image.Image, ...]:
    """Render one complete logical Neon Relay frame."""
    with RENDER_LOCK:
        controls = {
            snapshot.layout.start_key,
            snapshot.layout.exit_key,
            snapshot.layout.difficulty_key,
            snapshot.layout.sound_key,
            snapshot.layout.record_key,
        }
        images: list[Image.Image] = []
        for index in range(snapshot.layout.key_count):
            if snapshot.phase == PHASE_LOBBY:
                control = lobby_control(
                    snapshot,
                    index,
                    size,
                    f"Best {snapshot.high_score}",
                )
                image = control if control is not None else _ambient_key(index, size)
            elif snapshot.phase == PHASE_UPGRADE:
                if index in snapshot.upgrade_keys:
                    choice = snapshot.upgrade_choices[
                        snapshot.upgrade_keys.index(index)
                    ]
                    image = _upgrade_key(choice, size)
                else:
                    image = renderer.compose(
                        size=size,
                        bg=BG,
                        center_text="CHOOSE" if index == snapshot.layout.start_key else "",
                        text_color=GOLD,
                    )
            elif snapshot.phase == PHASE_RESULTS and index in controls:
                control = lobby_control(
                    snapshot,
                    index,
                    size,
                    f"Best {snapshot.high_score}",
                )
                image = control if control is not None else _ambient_key(index, size)
            elif snapshot.tiles:
                image = _circuit_key(snapshot, index, size)
            else:
                image = _ambient_key(index, size)
            images.append(image)

        if snapshot.phase == PHASE_RESULTS:
            free = [index for index in range(snapshot.layout.key_count) if index not in controls]
            if free:
                index = free[len(free) // 2]
                images[index] = renderer.compose(
                    size=size,
                    bg="#261d35" if snapshot.new_high_score else "#152d3b",
                    center_text=str(snapshot.score),
                    label="NEW BEST" if snapshot.new_high_score else "SCORE",
                    text_color=GOLD if snapshot.new_high_score else INK,
                )
        return tuple(images)


def neon_relay_hud(
    snapshot: NeonRelaySnapshot,
    size: tuple[int, int],
) -> Image.Image:
    """Panoramic reactor HUD for Stream Deck Plus."""
    with RENDER_LOCK:
        width, height = size
        image = Image.new("RGB", size, BG)
        accent = MAGENTA if snapshot.overdrive else ROUTE
        draw, value_font, small = draw_hud_header(
            image,
            "NEON RELAY",
            snapshot.difficulty,
            snapshot.progress,
            accent,
        )
        if snapshot.phase == PHASE_LOBBY:
            line = "ROTATE THE ROUTE  ·  KEEP THE SPARK ALIVE"
            instruction_font = game_font(max(13, height // 5))
            draw.text(
                (190, 16),
                line,
                font=instruction_font,
                fill=INK,
            )
            hint = "Dials rotate columns"
            draw.text(
                (width - draw.textlength(hint, font=small) - 18, height - 26),
                hint,
                font=small,
                fill="#9aa8b7",
            )
            return image
        if snapshot.phase == PHASE_UPGRADE:
            line = "CHOOSE A REACTOR UPGRADE"
            draw.text(
                ((width - draw.textlength(line, font=value_font)) / 2, 14),
                line,
                font=value_font,
                fill=GOLD,
            )
            return image

        stat_font = game_font(max(14, height // 4))
        score = f"SCORE {snapshot.score}"
        sector = f"SECTOR {snapshot.sector}"
        shield = f"SHIELD {snapshot.shields}"
        draw.text((195, 16), score, font=stat_font, fill=INK)
        draw.text((410, 16), sector, font=stat_font, fill=GOLD)
        draw.text(
            (width - draw.textlength(shield, font=stat_font) - 18, 16),
            shield,
            font=stat_font,
            fill=SHIELD if snapshot.shields else RED,
        )

        bar_left, bar_right = 195, width - 18
        bar_top = height - 21
        draw.rounded_rectangle(
            (bar_left, bar_top, bar_right, bar_top + 8),
            radius=4,
            fill="#202b38",
        )
        charge_width = round((bar_right - bar_left) * snapshot.overdrive_level / 100)
        if charge_width:
            draw.rounded_rectangle(
                (bar_left, bar_top, bar_left + charge_width, bar_top + 8),
                radius=4,
                fill=accent,
            )
        if snapshot.overdrive:
            state = "OVERDRIVE"
        elif snapshot.stasis_active:
            state = "STASIS"
        else:
            state = f"{snapshot.speed_seconds:.2f}s / tile"
        draw.text(
            (bar_left, height - 38),
            state,
            font=small,
            fill=accent if snapshot.overdrive else "#aebdca",
        )
        controls = "Turn: rotate column  ·  Press: stasis at 40%"
        draw.text(
            (width - draw.textlength(controls, font=small) - 18, height - 38),
            controls,
            font=small,
            fill="#9aa8b7",
        )
        return image
