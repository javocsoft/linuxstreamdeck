"""Printable layout sheet: a whole profile rendered as one PNG.

A deck is only readable while you are looking at it, and a page you visit once
a week is exactly the one you forget. This composes every page of a profile,
and every folder inside it, into a single image that can be printed, pinned up
or shared.

It reuses `renderer.compose()` so a key looks here exactly as it does on the
hardware, and adds the one thing the deck itself cannot show: a caption saying
what each key actually does.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..core import actions as action_registry
from ..core.config import (
    FOLDER_BACK_INDEX,
    KIND_FOLDER,
    KIND_MULTI,
    KIND_PRESS,
    KIND_RANDOM,
    KIND_TOGGLE,
    KeyConfig,
)
from ..core.icons import RENDER_LOCK
from . import renderer

# Rendered larger than the hardware so the sheet stays readable on paper.
KEY_PX = 96
KEY_GAP = 12
CAPTION_HEIGHT = 30
CAPTION_SIZE = 13
PAGE_TITLE_SIZE = 22
SHEET_TITLE_SIZE = 30
MARGIN = 28
SECTION_GAP = 26

BACKGROUND = "#101014"
TITLE_COLOR = "#ffffff"
SUBTITLE_COLOR = "#9aa0b0"
CAPTION_COLOR = "#c8ccd8"
RULE_COLOR = "#2a2a34"

_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/firasans/FiraSans-Regular.ttf",
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
]
_BOLD_CANDIDATES = renderer._FONT_CANDIDATES


@lru_cache(maxsize=16)
def _font(size: int, bold: bool = False):
    for path in (_BOLD_CANDIDATES if bold else _FONT_CANDIDATES):
        if Path(path).exists():
            try:
                # BASIC layout, like every other renderer here: Pillow's bundled
                # harfbuzz intermittently draws blank glyphs beside GTK's.
                return ImageFont.truetype(
                    path, size, layout_engine=ImageFont.Layout.BASIC
                )
            except Exception:
                continue
    return ImageFont.load_default()


def key_caption(kc: KeyConfig | None) -> str:
    """One short line saying what a key does.

    This is the whole reason the sheet beats a photograph of the deck: an
    icon-only key is unidentifiable once you are away from it.
    """
    if kc is None or kc.is_empty():
        return ""
    if kc.kind == KIND_FOLDER:
        contents = kc.contents
        count = len(contents.keys) if contents is not None else 0
        return f"Folder ({count})" if count else "Folder"
    if kc.kind == KIND_TOGGLE:
        return _steps_caption(kc.steps_on, "Toggle")
    if kc.kind == KIND_RANDOM:
        return _steps_caption(kc.steps, "Random")
    if kc.kind == KIND_PRESS:
        return _steps_caption(
            [*kc.steps_single, *kc.steps_double, *kc.steps_long], "Gestures"
        )
    if kc.kind == KIND_MULTI:
        return _steps_caption(kc.steps, "Actions")
    return _action_name(kc.action)


def _steps_caption(steps, fallback: str) -> str:
    """The first step's name, with a count when there is more than one."""
    if not steps:
        return fallback
    first = _action_name(steps[0].action) or fallback
    return f"{first} +{len(steps) - 1}" if len(steps) > 1 else first


def _action_name(action_id: str) -> str:
    action = action_registry.get(action_id)
    return action.name if action is not None else action_id


def _sections(profile) -> list[tuple[str, dict, int]]:
    """Every grid worth drawing: each page, then each folder within it.

    Folders are separate sections rather than a footnote, because on the deck
    they are separate grids and the sheet is meant to match what you press.
    """
    out: list[tuple[str, dict, int]] = []
    for page in profile.pages:
        out.append((page.name, page.keys, -1))
        _folder_sections(page.name, page.keys, out)
    return out


def _folder_sections(trail: str, keys: dict, out: list) -> None:
    for raw_index, kc in sorted(keys.items(), key=lambda kv: int(kv[0])):
        contents = kc.contents if kc is not None else None
        if contents is None:
            continue
        name = f"{trail} › {kc.folder_name()}"
        out.append((name, contents.keys, FOLDER_BACK_INDEX))
        _folder_sections(name, contents.keys, out)


def _draw_caption(draw, text: str, center_x: float, y: int, width: int) -> None:
    """Caption centered under a key, ellipsised rather than overflowing."""
    if not text:
        return
    font = _font(CAPTION_SIZE)
    if draw.textlength(text, font=font) > width:
        # Trim against the room left once the ellipsis is paid for. Checking
        # whether `text + "…"` fits on each pass never succeeds: by then the
        # bare text fits too, the loop ends, and the caption is left cut off
        # mid-word with nothing to show it was shortened.
        room = width - draw.textlength("…", font=font)
        while text and draw.textlength(text, font=font) > room:
            text = text[:-1]
        text = text.rstrip() + "…"
    draw.text(
        (center_x - draw.textlength(text, font=font) / 2, y),
        text,
        font=font,
        fill=CAPTION_COLOR,
    )


def profile_sheet(profile, columns: int, rows: int) -> Image.Image:
    """Compose the whole profile into one printable image.

    `columns`/`rows` come from the connected deck, so the sheet has the shape of
    the hardware it documents rather than a hardcoded 5x3.
    """
    columns = max(1, int(columns))
    rows = max(1, int(rows))
    sections = _sections(profile)
    cell = KEY_PX + KEY_GAP
    grid_width = columns * cell - KEY_GAP
    row_height = KEY_PX + CAPTION_HEIGHT + KEY_GAP

    header = MARGIN + SHEET_TITLE_SIZE + 18
    section_height = PAGE_TITLE_SIZE + 14 + rows * row_height + SECTION_GAP
    width = grid_width + MARGIN * 2
    height = header + section_height * len(sections) + MARGIN

    with RENDER_LOCK:
        sheet = Image.new("RGB", (width, height), BACKGROUND)
        draw = ImageDraw.Draw(sheet)

        draw.text(
            (MARGIN, MARGIN),
            profile.name,
            font=_font(SHEET_TITLE_SIZE, bold=True),
            fill=TITLE_COLOR,
        )
        y = header

        for title, keys, reserved in sections:
            draw.text(
                (MARGIN, y),
                title,
                font=_font(PAGE_TITLE_SIZE, bold=True),
                fill=TITLE_COLOR,
            )
            title_font = _font(PAGE_TITLE_SIZE, bold=True)
            rule_x = MARGIN + draw.textlength(title, font=title_font) + 12
            rule_y = y + PAGE_TITLE_SIZE // 2
            if rule_x < width - MARGIN:
                draw.line(
                    (rule_x, rule_y, width - MARGIN, rule_y), fill=RULE_COLOR
                )
            y += PAGE_TITLE_SIZE + 14

            for index in range(columns * rows):
                column, row = index % columns, index // columns
                x = MARGIN + column * cell
                key_y = y + row * row_height
                if index == reserved:
                    image = renderer.compose(
                        size=(KEY_PX, KEY_PX),
                        label="Back",
                        icon_path="mdi:arrow-left-circle",
                        bg="#20202a",
                    )
                    caption = "Leave folder"
                else:
                    kc = keys.get(str(index))
                    image = _key_image(kc)
                    caption = key_caption(kc)
                sheet.paste(image, (x, key_y))
                # Captions may use the gap between keys, so a name survives
                # a few characters longer before it has to be cut.
                _draw_caption(
                    draw, caption, x + KEY_PX / 2, key_y + KEY_PX + 6, cell - 6
                )

            y += rows * row_height + SECTION_GAP

    return sheet


def _key_image(kc: KeyConfig | None) -> Image.Image:
    """A key exactly as the deck draws it, in its resting state.

    Live feedback is deliberately not consulted: a printed sheet documents the
    layout, and asking OBS what colour a key is right now would make the same
    profile render differently every time.
    """
    if kc is None or kc.is_empty():
        return renderer.compose(size=(KEY_PX, KEY_PX))
    if kc.kind == KIND_FOLDER:
        contents = kc.contents
        count = len(contents.keys) if contents is not None else 0
        return renderer.compose(
            size=(KEY_PX, KEY_PX),
            label=kc.label,
            icon_path=kc.icon or "mdi:folder",
            bg=kc.bg_color,
            badge=str(count) if count else "",
            font_size=kc.font_size,
            text_color=kc.text_color,
        )
    return renderer.compose(
        size=(KEY_PX, KEY_PX),
        label=kc.label,
        icon_path=kc.icon or _default_icon(kc),
        bg=kc.bg_color,
        font_size=kc.font_size,
        text_color=kc.text_color,
    )


def _default_icon(kc: KeyConfig) -> str:
    """The icon the key inherits, mirroring what the controller resolves."""
    if kc.kind == KIND_TOGGLE:
        steps = kc.steps_on
    elif kc.kind == KIND_PRESS:
        steps = [*kc.steps_single, *kc.steps_double, *kc.steps_long]
    elif kc.kind in (KIND_MULTI, KIND_RANDOM):
        steps = kc.steps
    else:
        action = action_registry.get(kc.action)
        return action.default_icon if action is not None else ""
    for step in steps:
        action = action_registry.get(step.action)
        if action is not None and action.default_icon:
            return action.default_icon
    return ""


def save_profile_sheet(profile, columns: int, rows: int, target: Path) -> Path:
    """Render and write the sheet, returning the path written."""
    target = Path(target).expanduser()
    sheet = profile_sheet(profile, columns, rows)
    target.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(target, format="PNG")
    return target
