"""Pillow frames for Colour Mastermind."""

from __future__ import annotations

from PIL import Image, ImageDraw

from ..core.icons import RENDER_LOCK
from ..device import renderer
from .common import PHASE_LOBBY, PHASE_RESULTS
from .mastermind import GuessView, MastermindSnapshot
from .rendering import BG, CYAN, GOLD, GREEN, INK, RED, draw_hud_header, game_font, lobby_control

MASTER_COLORS = (
    "#ef5350",
    "#42a5f5",
    "#fdd835",
    "#66bb6a",
    "#ab67d5",
    "#ff8a45",
    "#35d5d0",
    "#f2f4f7",
)


def _peg(size: tuple[int, int], color: int, label: str = "") -> Image.Image:
    width, height = size
    image = Image.new("RGB", size, "#151b24")
    draw = ImageDraw.Draw(image)
    radius = max(9, min(width, height) // 3)
    cx, cy = width // 2, height // 2 - (4 if label else 0)
    fill = MASTER_COLORS[color % len(MASTER_COLORS)]
    draw.ellipse(
        (cx - radius, cy - radius, cx + radius, cy + radius),
        fill=fill,
        outline="#ffffff",
        width=max(2, radius // 7),
    )
    shine = max(3, radius // 4)
    draw.ellipse(
        (cx - radius // 2, cy - radius // 2, cx - radius // 2 + shine, cy - radius // 2 + shine),
        fill="#ffffff",
    )
    if label:
        font = game_font(max(8, height // 9), bold=False)
        draw.text(((width - draw.textlength(label, font=font)) / 2, height - max(13, height // 6)), label, font=font, fill="#b7c2cc")
    return image


def _history(size: tuple[int, int], guess: GuessView, number: int) -> Image.Image:
    width, height = size
    image = Image.new("RGB", size, "#202733")
    draw = ImageDraw.Draw(image)
    count = len(guess.colors)
    peg_radius = max(3, min(width // max(3, count * 2 + 1), height // 9))
    spacing = width / max(1, count)
    for index, color in enumerate(guess.colors):
        cx = round(spacing * (index + 0.5))
        cy = round(height * 0.32)
        draw.ellipse(
            (cx - peg_radius, cy - peg_radius, cx + peg_radius, cy + peg_radius),
            fill=MASTER_COLORS[color],
        )
    font = game_font(max(9, height // 7))
    score = f"E{guess.exact}   C{guess.color_only}"
    draw.text(
        ((width - draw.textlength(score, font=font)) / 2, height * 0.55),
        score,
        font=font,
        fill=INK,
    )
    small = game_font(max(8, height // 10), bold=False)
    draw.text((4, height - max(12, height // 7)), f"#{number}", font=small, fill="#8996a4")
    return image


def render_mastermind_keys(
    snapshot: MastermindSnapshot,
    size: tuple[int, int],
) -> tuple[Image.Image, ...]:
    with RENDER_LOCK:
        slots = {key: slot for slot, key in enumerate(snapshot.slot_keys)}
        history = list(reversed(snapshot.history))
        images = []
        best = f"Best {snapshot.high_score}" if snapshot.high_score else "No best"
        for index in range(snapshot.layout.key_count):
            if snapshot.phase == PHASE_RESULTS:
                if index in slots:
                    slot = slots[index]
                    image = _peg(size, snapshot.solution[slot], f"CODE {slot + 1}")
                elif index == snapshot.submit_key:
                    image = renderer.compose(
                        size=size,
                        bg="#17382e",
                        icon_path="mdi:replay",
                        label="Again",
                        icon_color=GREEN,
                    )
                elif index == snapshot.clear_key:
                    image = renderer.compose(
                        size=size,
                        bg="#4a2028",
                        icon_path="mdi:arrow-left",
                        label="Back",
                        icon_color=INK,
                    )
                else:
                    image = renderer.compose(
                        size=size,
                        bg="#17382e" if snapshot.won else "#4a2028",
                        center_text=str(len(snapshot.history)),
                        label=(
                            "NEW BEST"
                            if snapshot.new_high_score
                            else ("Solved" if snapshot.won else "Attempts")
                        ),
                        text_color=GOLD if snapshot.new_high_score else INK,
                    )
            elif snapshot.phase == PHASE_LOBBY:
                image = lobby_control(snapshot, index, size, best)
                if image is None:
                    image = _peg(size, index % snapshot.color_count)
            elif index in slots:
                slot = slots[index]
                image = _peg(size, snapshot.current[slot], f"PEG {slot + 1}")
            elif index == snapshot.submit_key:
                latest = snapshot.history[-1] if snapshot.history else None
                image = renderer.compose(
                    size=size,
                    bg="#17382e",
                    icon_path="mdi:check-bold",
                    label=(
                        f"E{latest.exact} C{latest.color_only}"
                        if latest is not None
                        else "Submit"
                    ),
                    badge=str(snapshot.attempts_left),
                    icon_color=GREEN,
                )
            elif index == snapshot.clear_key:
                image = renderer.compose(
                    size=size,
                    bg="#3b2930",
                    icon_path="mdi:backup-restore",
                    label="Reset pegs",
                    icon_color=RED,
                )
            else:
                history_index = snapshot.history_keys.index(index)
                image = (
                    _history(size, history[history_index], len(history) - history_index)
                    if history_index < len(history)
                    else renderer.compose(
                        size=size,
                        bg="#202733",
                        center_text="?",
                        label="History",
                        text_color="#667482",
                    )
                )
            images.append(image)
        return tuple(images)


def mastermind_hud(snapshot: MastermindSnapshot, size: tuple[int, int]) -> Image.Image:
    with RENDER_LOCK:
        image = Image.new("RGB", size, BG)
        draw, value_font, small = draw_hud_header(
            image, "COLOUR MASTERMIND", snapshot.difficulty, snapshot.progress, CYAN
        )
        width, height = size
        attempts = f"LEFT  {snapshot.attempts_left}"
        if snapshot.history:
            latest = snapshot.history[-1]
            feedback = f"E {latest.exact}   C {latest.color_only}"
        else:
            feedback = "BUILD CODE"
        draw.text(((width - draw.textlength(feedback, font=value_font)) / 2, 12), feedback, font=value_font, fill=INK)
        draw.text((width - draw.textlength(attempts, font=value_font) - 18, 12), attempts, font=value_font, fill=GOLD)
        best = f"BEST  {snapshot.high_score or '--'}"
        draw.text((width - draw.textlength(best, font=small) - 18, height - 26), best, font=small, fill=GREEN)
        legend = "E = exact   C = colour"
        draw.text(
            ((width - draw.textlength(legend, font=small)) / 2, height - 26),
            legend,
            font=small,
            fill="#8794a2",
        )
        return image
