"""Pillow frames for adaptive Tic-Tac-Toe."""

from __future__ import annotations

from PIL import Image, ImageDraw

from ..core.icons import RENDER_LOCK
from ..device import renderer
from .common import PHASE_LOBBY, PHASE_RESULTS
from .rendering import BG, CYAN, GOLD, GREEN, INK, RED, draw_hud_header, lobby_control
from .tic_tac_toe import PHASE_AI_TURN, TicTacToeSnapshot


def _square(
    size: tuple[int, int],
    mark: str,
    winning: bool = False,
    waiting: bool = False,
) -> Image.Image:
    width, height = size
    image = Image.new("RGB", size, "#211f18" if waiting else "#151d27")
    draw = ImageDraw.Draw(image)
    pad = max(6, min(width, height) // 7)
    draw.rounded_rectangle(
        (3, 3, width - 4, height - 4),
        radius=max(5, pad),
        outline=GOLD if winning else ("#806a32" if waiting else "#2b4152"),
        width=max(3, min(width, height) // (12 if winning else 22)),
    )
    stroke = max(5, min(width, height) // 10)
    if mark == "X":
        draw.line((pad, pad, width - pad, height - pad), fill=CYAN, width=stroke)
        draw.line((width - pad, pad, pad, height - pad), fill=CYAN, width=stroke)
    elif mark == "O":
        draw.ellipse((pad, pad, width - pad, height - pad), outline=RED, width=stroke)
    return image


def _result_label(snapshot: TicTacToeSnapshot) -> tuple[str, str]:
    if snapshot.winner == "X":
        return "YOU WIN", "#17382e"
    if snapshot.winner == "O":
        return "AI WINS", "#4a2028"
    return "DRAW", "#313344"


def render_tic_tac_toe_keys(
    snapshot: TicTacToeSnapshot,
    size: tuple[int, int],
) -> tuple[Image.Image, ...]:
    with RENDER_LOCK:
        by_key = {key: cell for cell, key in enumerate(snapshot.board_keys)}
        winning = set(snapshot.winning_cells)
        extra_keys = [
            index for index in range(snapshot.layout.key_count) if index not in by_key
        ]
        status_key = extra_keys[len(extra_keys) // 2] if extra_keys else None
        images = []
        for index in range(snapshot.layout.key_count):
            if snapshot.phase == PHASE_RESULTS:
                if index == snapshot.result_again_key:
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
                elif index in by_key:
                    cell = by_key[index]
                    image = _square(size, snapshot.marks[cell], cell in winning)
                elif index == status_key:
                    label, background = _result_label(snapshot)
                    image = renderer.compose(
                        size=size,
                        bg=background,
                        center_text=(
                            "X"
                            if snapshot.winner == "X"
                            else ("O" if snapshot.winner == "O" else "=")
                        ),
                        label=label,
                        text_color=GOLD if snapshot.new_high_score else INK,
                    )
                else:
                    image = renderer.compose(
                        size=size,
                        bg=BG,
                        center_text="·",
                        text_color="#31404d",
                    )
            elif snapshot.phase == PHASE_LOBBY:
                image = lobby_control(snapshot, index, size, f"Wins {snapshot.high_score}")
                if image is None:
                    cell = by_key.get(index, index % max(1, len(snapshot.marks)))
                    mark = "X" if cell % 3 == 0 else ("O" if cell % 3 == 1 else "")
                    image = _square(size, mark)
            elif index in by_key:
                cell = by_key[index]
                image = _square(
                    size,
                    snapshot.marks[cell],
                    cell in winning,
                    snapshot.phase == PHASE_AI_TURN,
                )
            elif index == status_key:
                image = renderer.compose(
                    size=size,
                    bg="#33252b" if snapshot.phase == PHASE_AI_TURN else "#17313d",
                    center_text="O" if snapshot.phase == PHASE_AI_TURN else "X",
                    label="AI thinking" if snapshot.phase == PHASE_AI_TURN else "Your turn",
                    text_color=RED if snapshot.phase == PHASE_AI_TURN else CYAN,
                )
            else:
                image = renderer.compose(
                    size=size,
                    bg=BG,
                    center_text="·",
                    text_color="#31404d",
                )
            images.append(image)
        return tuple(images)


def tic_tac_toe_hud(snapshot: TicTacToeSnapshot, size: tuple[int, int]) -> Image.Image:
    with RENDER_LOCK:
        image = Image.new("RGB", size, BG)
        draw, value_font, small = draw_hud_header(
            image, "TIC-TAC-TOE", snapshot.difficulty, snapshot.progress, CYAN
        )
        width, height = size
        if snapshot.phase == PHASE_AI_TURN:
            state = "AI THINKING"
        elif snapshot.phase == PHASE_RESULTS:
            state = _result_label(snapshot)[0]
        else:
            state = "YOUR TURN"
        draw.text(((width - draw.textlength(state, font=value_font)) / 2, 12), state, font=value_font, fill=INK)
        wins = f"WINS  {snapshot.high_score}"
        draw.text((width - draw.textlength(wins, font=value_font) - 18, 12), wins, font=value_font, fill=GREEN)
        variant = "3 x 3" if len(snapshot.board_keys) == 9 else f"COMPACT {snapshot.board_columns} x 2"
        draw.text((width - draw.textlength(variant, font=small) - 18, height - 26), variant, font=small, fill=GOLD)
        return image
