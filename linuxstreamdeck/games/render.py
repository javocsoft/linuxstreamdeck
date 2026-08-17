"""Pillow renderer for Mole Smash, shared by physical and virtual decks."""

from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps

from ..core import fonts
from ..core.icons import RENDER_LOCK
from ..device import renderer
from .mole_smash import (
    DIFFICULTY_LABELS,
    PHASE_COUNTDOWN,
    PHASE_LOBBY,
    PHASE_PLAYING,
    PHASE_RESULTS,
    GameSnapshot,
    TargetView,
)

ASSET_DIR = Path(__file__).resolve().parent.parent / "assets" / "games" / "mole_smash"
MOLE_ASSET = ASSET_DIR / "mole.png"

BG = "#11151b"
HOLE = "#080a0d"
HOLE_RIM = "#303945"
CYAN = "#35c8e6"
GREEN = "#36c98f"
GOLD = "#ffcc4d"
RED = "#ef6a73"
INK = "#f7fbff"


def to_png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@lru_cache(maxsize=8)
def _font(size: int, bold: bool = True):
    choices = fonts.SANS_BOLD if bold else fonts.SANS_REGULAR
    for path in choices:
        try:
            return ImageFont.truetype(
                path,
                max(8, int(size)),
                layout_engine=ImageFont.Layout.BASIC,
            )
        except OSError:
            continue
    return ImageFont.load_default()


@lru_cache(maxsize=16)
def _mole(size: int, golden: bool) -> Image.Image:
    source = Image.open(MOLE_ASSET).convert("RGBA")
    if box := source.getbbox():
        source = source.crop(box)
    source.thumbnail((size, size), Image.Resampling.LANCZOS)
    if golden:
        coloured = ImageOps.colorize(
            ImageOps.grayscale(source),
            black="#6b3508",
            white="#ffe57a",
        ).convert("RGBA")
        coloured.putalpha(source.getchannel("A"))
        source = ImageEnhance.Contrast(coloured).enhance(1.08)
    return source


def _hole_key(size: tuple[int, int], wrong: bool = False) -> Image.Image:
    width, height = size
    image = Image.new("RGB", size, BG)
    draw = ImageDraw.Draw(image)
    pad = max(3, width // 14)
    top = int(height * 0.61)
    draw.ellipse((pad, top, width - pad, height - pad), fill=HOLE_RIM)
    inner = max(2, width // 24)
    draw.ellipse(
        (pad + inner, top + inner, width - pad - inner, height - pad - inner),
        fill=HOLE,
    )
    if wrong:
        stroke = max(2, min(width, height) // 18)
        draw.rounded_rectangle(
            (1, 1, width - 2, height - 2),
            radius=max(5, width // 9),
            outline=RED,
            width=stroke,
        )
    return image


def _target_key(
    size: tuple[int, int],
    target: TargetView,
    wrong: bool = False,
) -> Image.Image:
    width, height = size
    image = _hole_key(size, wrong)
    sprite = _mole(int(min(width, height) * 0.92), target.golden).copy()
    if target.state == "hit":
        scale = max(0.42, 0.72 - target.progress * 0.18)
        sprite = sprite.resize(
            (sprite.width, max(1, int(sprite.height * scale))),
            Image.Resampling.LANCZOS,
        )
        visible = 1.0
    else:
        visible = max(0.08, target.progress)
    x = (width - sprite.width) // 2
    final_y = max(1, int(height * 0.04))
    hidden_y = int(height * 0.68)
    y = round(hidden_y + (final_y - hidden_y) * visible)
    image.paste(sprite, (x, y), sprite)

    # The front rim has to cover the body: it is what makes the character look
    # as though it rises from a hole rather than floating over a black oval.
    draw = ImageDraw.Draw(image)
    pad = max(3, width // 14)
    rim_y = int(height * 0.73)
    draw.arc(
        (pad, int(height * 0.60), width - pad, height - pad),
        start=5,
        end=175,
        fill=GOLD if target.golden else HOLE_RIM,
        width=max(2, width // 18),
    )
    if target.state == "hit":
        star_font = _font(max(9, height // 5))
        draw.text((3, 1), "✦", font=star_font, fill=GOLD)
        draw.text((width - height // 5, 4), "✦", font=star_font, fill=CYAN)
    return image


def _control_key(snapshot: GameSnapshot, index: int, size) -> Image.Image:
    layout = snapshot.layout
    if index == layout.start_key:
        return renderer.compose(
            size=size,
            bg=GREEN,
            center_text="START" if snapshot.phase == PHASE_LOBBY else "AGAIN",
            text_color="#071711",
        )
    if index == layout.exit_key:
        return renderer.compose(
            size=size,
            bg="#4a2028",
            icon_path="mdi:arrow-left",
            label="Back",
            icon_color=INK,
        )
    if index == layout.difficulty_key:
        return renderer.compose(
            size=size,
            bg="#1c3442",
            icon_path="mdi:speedometer",
            label=DIFFICULTY_LABELS[snapshot.difficulty],
            icon_color=CYAN,
        )
    if index == layout.sound_key:
        return renderer.compose(
            size=size,
            bg="#25303a",
            icon_path=(
                "mdi:volume-high" if snapshot.sound_enabled else "mdi:volume-off"
            ),
            label="Sound",
            badge="ON" if snapshot.sound_enabled else "OFF",
            icon_color=CYAN if snapshot.sound_enabled else "#8b96a3",
        )
    if index == layout.record_key:
        return renderer.compose(
            size=size,
            bg="#3c3014",
            icon_path="mdi:trophy",
            label=f"Best {snapshot.high_score}",
            icon_color=GOLD,
        )
    return _hole_key(size)


def render_keys(
    snapshot: GameSnapshot,
    size: tuple[int, int],
) -> tuple[Image.Image, ...]:
    """One complete logical frame; the manager writes only changed keys."""
    with RENDER_LOCK:
        targets = {target.index: target for target in snapshot.targets}
        wrong = set(snapshot.wrong_keys)
        images = []
        for index in range(snapshot.layout.key_count):
            if snapshot.phase in (PHASE_LOBBY, PHASE_RESULTS):
                image = _control_key(snapshot, index, size)
                if snapshot.phase == PHASE_RESULTS and index not in {
                    snapshot.layout.start_key,
                    snapshot.layout.exit_key,
                    snapshot.layout.difficulty_key,
                    snapshot.layout.sound_key,
                    snapshot.layout.record_key,
                }:
                    label = "NEW BEST" if snapshot.new_high_score else "SCORE"
                    image = renderer.compose(
                        size=size,
                        bg="#173244" if snapshot.new_high_score else "#202832",
                        center_text=str(snapshot.score),
                        label=label,
                        text_color=GOLD if snapshot.new_high_score else INK,
                    )
            elif snapshot.phase == PHASE_COUNTDOWN:
                centre = snapshot.layout.start_key
                image = renderer.compose(
                    size=size,
                    bg="#123444" if index == centre else BG,
                    center_text=str(snapshot.countdown) if index == centre else "",
                    text_color=CYAN,
                )
            elif index == snapshot.layout.score_key:
                image = renderer.compose(
                    size=size,
                    bg="#193247",
                    center_text=str(snapshot.score),
                    label="Score",
                    text_color=INK,
                )
            elif index == snapshot.layout.time_key:
                image = renderer.compose(
                    size=size,
                    bg="#3c2530" if snapshot.seconds_left <= 10 else "#23303b",
                    center_text=str(snapshot.seconds_left),
                    label="Time",
                    text_color=RED if snapshot.seconds_left <= 10 else INK,
                )
            elif index in targets:
                image = _target_key(size, targets[index], index in wrong)
            else:
                image = _hole_key(size, index in wrong)
            images.append(image)
        return tuple(images)


def touchscreen_hud(snapshot: GameSnapshot, size: tuple[int, int]) -> Image.Image:
    """Stream Deck + strip: stable score/time HUD, leaving all keys playable."""
    with RENDER_LOCK:
        width, height = size
        image = Image.new("RGB", size, BG)
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, max(1, int(width * snapshot.progress)), 3), fill=CYAN)
        title_font = _font(max(11, height // 5))
        value_font = _font(max(16, height // 3))
        small = _font(max(9, height // 7), bold=False)
        draw.text((18, 10), "MOLE SMASH", font=title_font, fill=CYAN)
        draw.text((18, height - 26), DIFFICULTY_LABELS[snapshot.difficulty], font=small, fill="#9aa8b7")
        score = f"SCORE  {snapshot.score}"
        time_text = f"TIME  {snapshot.seconds_left}"
        score_width = draw.textlength(score, font=value_font)
        time_width = draw.textlength(time_text, font=value_font)
        draw.text(((width - score_width) / 2, 12), score, font=value_font, fill=INK)
        draw.text((width - time_width - 18, 12), time_text, font=value_font, fill=RED if snapshot.seconds_left <= 10 else INK)
        if snapshot.combo >= 2:
            combo = f"COMBO x{snapshot.combo}"
            draw.text((width - draw.textlength(combo, font=small) - 18, height - 26), combo, font=small, fill=GOLD)
        return image
