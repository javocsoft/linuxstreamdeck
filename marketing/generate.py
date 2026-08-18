#!/usr/bin/env python3
"""Generate the LinuxStreamDeck marketing gallery from real application output.

The gallery deliberately avoids invented UI and third-party hardware mockups.
Configured keys go through ``device.renderer.compose()``, games through their
engines and dispatcher, screen savers through ``screensaver_frame()``, and the
Stream Deck + strip through ``touchscreen_image()``.  The main-window panel uses
the repository's canonical current screenshot.
"""

from __future__ import annotations

import random
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent
WIDTH = 1600
HEIGHT = 900

BACKGROUND = "#0c0f15"
PANEL = "#171b24"
PANEL_BORDER = "#303746"
INK = "#f5f7fb"
MUTED = "#a8b0c1"
DIM = "#737d91"
ACCENT = "#58a6ff"
CYAN = "#61d7ec"
GREEN = "#43d19e"
RED = "#ee6675"
GOLD = "#f2bc57"


@lru_cache(maxsize=32)
def _font(size: int, bold: bool = False):
    from linuxstreamdeck.core import fonts

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


@lru_cache(maxsize=8)
def _logo(size: int) -> Image.Image:
    import gi

    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf

    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(
        str(ROOT / "packaging" / "com.javocsoft.LinuxStreamDeck.svg"),
        size,
        size,
    )
    mode = "RGBA" if pixbuf.get_has_alpha() else "RGB"
    return Image.frombytes(
        mode,
        (pixbuf.get_width(), pixbuf.get_height()),
        pixbuf.get_pixels(),
        "raw",
        mode,
        pixbuf.get_rowstride(),
    ).convert("RGBA")


def _base(kicker: str, title: str, subtitle: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)

    glow = Image.new("RGB", image.size, BACKGROUND)
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse((-260, -330, 760, 470), fill="#173a68")
    glow_draw.ellipse((1180, 580, 1840, 1180), fill="#183e38")
    image = Image.blend(image, glow.filter(ImageFilter.GaussianBlur(190)), 0.72)
    draw = ImageDraw.Draw(image)

    logo = _logo(60)
    image.paste(logo, (68, 40), logo)
    draw.text((142, 50), "LinuxStreamDeck", font=_font(31, True), fill=INK)
    draw.text((68, 126), kicker.upper(), font=_font(17, True), fill=ACCENT)
    draw.text((68, 158), title, font=_font(48, True), fill=INK)
    draw.text((70, 222), subtitle, font=_font(23), fill=MUTED)
    return image, draw


def _footer(draw: ImageDraw.ImageDraw, note: str = "") -> None:
    draw.line((68, 842, 1532, 842), fill="#252b37", width=1)
    if note:
        draw.text((68, 856), note, font=_font(14), fill=DIM)
    footer = "Open source  |  GPL-3.0-or-later  |  javocsoft.github.io/linuxstreamdeck"
    width = draw.textlength(footer, font=_font(14, True))
    draw.text((1532 - width, 856), footer, font=_font(14, True), fill="#8c95a8")


def _panel(
    image: Image.Image,
    box: tuple[int, int, int, int],
    *,
    fill: str = PANEL,
    radius: int = 22,
) -> ImageDraw.ImageDraw:
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=PANEL_BORDER, width=2)
    return draw


def _contain(source: Image.Image, size: tuple[int, int]) -> Image.Image:
    copy = source.copy()
    copy.thumbnail(size, Image.Resampling.LANCZOS)
    return copy


def _paste_center(
    destination: Image.Image,
    source: Image.Image,
    box: tuple[int, int, int, int],
) -> None:
    left, top, right, bottom = box
    fitted = _contain(source, (right - left, bottom - top))
    x = left + (right - left - fitted.width) // 2
    y = top + (bottom - top - fitted.height) // 2
    if fitted.mode == "RGBA":
        destination.paste(fitted, (x, y), fitted)
    else:
        destination.paste(fitted, (x, y))


def _key_images(specs: list[dict], size: int) -> list[Image.Image]:
    from linuxstreamdeck.device.renderer import compose

    return [compose(size=(size, size), **spec) for spec in specs]


def _deck_image(
    images: list[Image.Image] | tuple[Image.Image, ...],
    columns: int,
    *,
    key_size: int,
    gap: int,
) -> Image.Image:
    rows = (len(images) + columns - 1) // columns
    width = columns * key_size + (columns + 1) * gap
    height = rows * key_size + (rows + 1) * gap
    canvas = Image.new("RGB", (width, height), "#07090d")
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=16, fill="#07090d")
    for index, source in enumerate(images):
        tile = source.convert("RGB")
        if tile.size != (key_size, key_size):
            tile = tile.resize((key_size, key_size), Image.Resampling.LANCZOS)
        x = gap + (index % columns) * (key_size + gap)
        y = gap + (index // columns) * (key_size + gap)
        canvas.paste(tile, (x, y))
    return canvas


def _section_label(draw: ImageDraw.ImageDraw, x: int, y: int, title: str, text: str) -> None:
    draw.text((x, y), title, font=_font(24, True), fill=INK)
    draw.text((x, y + 37), text, font=_font(17), fill=MUTED, spacing=7)


def main_window_capture() -> None:
    image, draw = _base(
        "The complete desktop application",
        "A native Linux control surface",
        "Configure the physical deck and use the same layout as a virtual deck.",
    )
    _panel(image, (46, 286, 1554, 808))

    screenshot = Image.open(ROOT / "docs" / "screenshot.png").convert("RGB")
    screenshot = ImageOps.expand(screenshot, border=1, fill="#495164")
    _paste_center(image, screenshot, (420, 310, 1525, 782))

    draw.rounded_rectangle((72, 316, 380, 778), radius=18, fill="#111620")
    _section_label(
        draw,
        98,
        350,
        "Visual editor",
        "Configure actions, appearance\nand live feedback without\nleaving the main window.",
    )
    _section_label(
        draw,
        98,
        493,
        "Flexible layouts",
        "Profiles, pages, folders,\nmulti-actions and press\ngestures.",
    )
    _section_label(
        draw,
        98,
        627,
        "Linux first",
        "GTK4 and Libadwaita, with\nDebian, Flatpak and AppImage\npackages.",
    )
    _footer(draw, "Current LinuxStreamDeck application window")
    image.save(OUT / "01-main-window.png", optimize=True)


def obs_capture() -> None:
    image, draw = _base(
        "OBS Studio control and live feedback",
        "A broadcast desk on fifteen keys",
        "Actions, state, health checks and machine telemetry remain visible at a glance.",
    )

    specs = [
        dict(icon_path="mdi:video-switch", label="Camera", active=True, bg="#1a5fb4"),
        dict(icon_path="mdi:monitor-screenshot", label="Screen"),
        dict(icon_path="mdi:presentation-play", label="Intro"),
        dict(icon_path="mdi:eye", label="Overlay", active=True, badge="ON"),
        dict(icon_path="mdi:message-text", label="Chat", border="#e8a33a", center_text="2m", badge="3"),
        dict(icon_path="mdi:record-circle", label="Record", active=True, bg="#a51d2d", badge="REC"),
        dict(icon_path="mdi:broadcast", label="Go live", active=True, bg="#26845d", badge="LIVE"),
        dict(icon_path="mdi:microphone-off", label="Mic", active=True, bg="#a51d2d"),
        dict(icon_path="mdi:movie-open", label="Replay"),
        dict(icon_path="mdi:bookmark-plus", label="Chapter"),
        dict(label="OBS CPU", center_text="4.2%", bg="#1e3a24"),
        dict(label="Bitrate", center_text="6.0Mb"),
        dict(label="Disk", center_text="2.9G", bg="#5c1622"),
        dict(icon_path="mdi:playlist-check", label="Pre-flight", bg="#24334a"),
        dict(icon_path="mdi:playlist-play", label="Scene macro", busy=True, busy_phase=True, badge="RUN"),
    ]
    deck = _deck_image(_key_images(specs, 124), 5, key_size=124, gap=13)
    _panel(image, (48, 286, 858, 807))
    _paste_center(image, deck, (72, 312, 834, 782))

    _panel(image, (890, 286, 1552, 807))
    _section_label(draw, 930, 326, "Scenes and sources", "Switch scenes, preview them live,\nand control visibility, filters and media.")
    _section_label(draw, 930, 450, "Recording and streaming", "Record, stream, replay, chapters,\nstudio mode and virtual camera.")
    _section_label(draw, 930, 574, "Know before going live", "Pre-flight checks cover OBS audio,\ncameras, disk, CPU and Twitch setup.")
    _section_label(draw, 930, 698, "Honest feedback", "Active, unavailable, running and failed\nstates are drawn directly on each key.")
    _footer(draw, "Representative live values rendered by LinuxStreamDeck")
    image.save(OUT / "02-obs-and-live-feedback.png", optimize=True)


def integrations_capture() -> None:
    image, draw = _base(
        "Services, sound and desktop control",
        "One deck, many workflows",
        "LinuxStreamDeck connects streaming tools with the rest of the Linux desktop.",
    )
    specs = [
        dict(icon_path="mdi:message-text", label="Chat", border="#e8a33a", center_text="45s", badge="2"),
        dict(icon_path="mdi:movie-open", label="Clip"),
        dict(icon_path="mdi:map-marker-plus", label="Marker"),
        dict(icon_path="mdi:account-group", label="Viewers", center_text="184"),
        dict(icon_path="mdi:bullhorn", label="Announce", bg="#4b2a63"),
        dict(icon_path="mdi:home-automation", label="Kitchen", active=True, bg="#1a5fb4"),
        dict(icon_path="mdi:home-thermometer", label="Office", center_text="21.4"),
        dict(icon_path="mdi:lightbulb-on-outline", label="Key Light", active=True, bg="#584a18"),
        dict(icon_path="mdi:web", label="Endpoint", center_text="99.9"),
        dict(icon_path="mdi:application", label="Browser"),
        dict(icon_path="mdi:microphone", label="Mic"),
        dict(icon_path="mdi:volume-plus", label="Game +"),
        dict(icon_path="mdi:volume-off", label="Discord", active=True, bg="#a51d2d"),
        dict(icon_path="mdi:speaker-multiple", label="Headset", active=True, bg="#1a5fb4"),
        dict(icon_path="mdi:music-note", label="Soundboard", bg="#4b2a63"),
    ]
    deck = _deck_image(_key_images(specs, 124), 5, key_size=124, gap=13)
    _panel(image, (48, 286, 858, 807))
    _paste_center(image, deck, (72, 312, 834, 782))

    _panel(image, (890, 286, 1552, 807))
    _section_label(draw, 930, 326, "Twitch", "Alerts, clips, markers, raids, ads,\nchannel settings and live statistics.")
    _section_label(draw, 930, 450, "Home and web", "Home Assistant entities, Elgato Key\nLights and generic HTTP endpoints.")
    _section_label(draw, 930, 574, "Linux desktop", "Applications, shortcuts, system stats,\nmedia transport and per-app audio.")
    _section_label(draw, 930, 698, "Soundboard", "Play WAV, MP3, OGG, FLAC or Opus\nlocally or into a virtual microphone.")
    _footer(draw, "Every key image is produced by the application renderer")
    image.save(OUT / "03-integrations-and-audio.png", optimize=True)


def _game_snapshots() -> dict[str, tuple[Image.Image, ...]]:
    from linuxstreamdeck.games.circuit_breaker import CircuitBreakerEngine
    from linuxstreamdeck.games.common import game_layout
    from linuxstreamdeck.games.mastermind import MastermindEngine
    from linuxstreamdeck.games.memory_match import MemoryMatchEngine
    from linuxstreamdeck.games.minesweeper import MinesweeperEngine
    from linuxstreamdeck.games.mole_smash import MoleSmashEngine
    from linuxstreamdeck.games.neon_relay import NeonRelayEngine
    from linuxstreamdeck.games.pulse_memory import PulseMemoryEngine
    from linuxstreamdeck.games.render import render_keys
    from linuxstreamdeck.games.tic_tac_toe import TicTacToeEngine

    layout = game_layout(15, 5)
    snapshots: dict[str, object] = {}

    mole = MoleSmashEngine(layout, sound_enabled=False, rng=random.Random(7))
    mole.press(layout.start_key, 0.0)
    mole.tick(3.01)
    mole.tick(3.02)
    snapshots["Mole Smash"] = mole.snapshot(3.24)

    circuit = CircuitBreakerEngine(layout, sound_enabled=False, rng=random.Random(9))
    circuit.press(layout.start_key, 0.0)
    circuit.press(7, 0.15)
    snapshots["Circuit Breaker"] = circuit.snapshot(0.3)

    pulse = PulseMemoryEngine(layout, sound_enabled=False, rng=random.Random(5))
    pulse.press(layout.start_key, 0.0)
    pulse.tick(3.01)
    snapshots["Pulse Memory"] = pulse.snapshot(3.16)

    memory = MemoryMatchEngine(
        layout,
        difficulty="easy",
        sound_enabled=False,
        rng=random.Random(11),
    )
    memory.press(layout.start_key, 0.0)
    snapshots["Memory Match"] = memory.snapshot(0.45)

    mines = MinesweeperEngine(layout, sound_enabled=False, rng=random.Random(12))
    mines.press(layout.start_key, 0.0)
    first = mines._cell_keys[len(mines._cell_keys) // 2]
    mines.press(first, 0.1)
    hidden = next(cell.index for cell in mines.snapshot(0.2).cells if cell.state == "hidden")
    mines.press(mines._mode_key, 0.25)
    mines.press(hidden, 0.3)
    snapshots["Minesweeper"] = mines.snapshot(4.0)

    tic = TicTacToeEngine(layout, sound_enabled=False, rng=random.Random(4))
    tic.press(layout.start_key, 0.0)
    tic.press(tic._board_keys[0], 0.1)
    tic.tick(0.6)
    second = next(key for key in tic._board_keys if not tic._marks[tic._key_to_cell[key]])
    tic.press(second, 0.7)
    snapshots["Tic-Tac-Toe"] = tic.snapshot(0.8)

    mastermind = MastermindEngine(layout, sound_enabled=False, rng=random.Random(18))
    mastermind.press(layout.start_key, 0.0)
    for slot, key in enumerate(mastermind._slot_keys):
        for turn in range(slot + 1):
            mastermind.press(key, 0.1 + slot * 0.03 + turn * 0.005)
    mastermind.press(mastermind._submit_key, 0.4)
    for slot, key in enumerate(mastermind._slot_keys):
        for turn in range(slot + 2):
            mastermind.press(key, 0.5 + slot * 0.03 + turn * 0.005)
    snapshots["Colour Mastermind"] = mastermind.snapshot(0.8)

    relay = NeonRelayEngine(layout, sound_enabled=False, rng=random.Random(31))
    relay.press(layout.start_key, 0.0)
    snapshots["Neon Relay"] = relay.snapshot(0.36)

    return {
        name: tuple(render_keys(snapshot, (72, 72)))
        for name, snapshot in snapshots.items()
    }


def games_capture() -> None:
    image, draw = _base(
        "Eight games built for illuminated keys",
        "The deck becomes the game board",
        "Each title has its own adaptive engine, graphics, sound effects and records.",
    )
    games = _game_snapshots()
    order = (
        "Circuit Breaker",
        "Colour Mastermind",
        "Memory Match",
        "Minesweeper",
        "Mole Smash",
        "Neon Relay",
        "Pulse Memory",
        "Tic-Tac-Toe",
    )
    card_width = 350
    card_height = 246
    start_x = 58
    start_y = 286
    hgap = 30
    vgap = 24
    for position, name in enumerate(order):
        column = position % 4
        row = position // 4
        left = start_x + column * (card_width + hgap)
        top = start_y + row * (card_height + vgap)
        _panel(image, (left, top, left + card_width, top + card_height), radius=18)
        draw.text((left + 18, top + 15), name, font=_font(20, True), fill=INK)
        deck = _deck_image(list(games[name]), 5, key_size=48, gap=5)
        _paste_center(image, deck, (left + 16, top + 54, left + card_width - 16, top + card_height - 14))
    _footer(draw, "Deterministic gameplay states rendered through the eight real game engines")
    image.save(OUT / "04-built-in-games.png", optimize=True)


def _screensaver_deck(style: str, elapsed: float) -> Image.Image:
    from linuxstreamdeck.device.screensaver import screensaver_frame

    frame = screensaver_frame(
        style,
        elapsed=elapsed,
        key_count=15,
        key_size=(72, 72),
        intensity=100,
        columns=5,
    )
    images = [entry if isinstance(entry, Image.Image) else Image.open(entry) for entry in frame.images]
    return _deck_image(images, 5, key_size=48, gap=5)


def screensavers_capture() -> None:
    image, draw = _base(
        "Coordinated full-deck animation",
        "Eleven animated screen savers",
        "One canvas flows across every key, with independent brightness and OBS-aware idle activation.",
    )
    styles = (
        ("Neon Pipes", "neon_pipes", 3.4),
        ("Digital Rain", "digital_rain", 4.2),
        ("Aurora Flow", "aurora_flow", 2.8),
        ("Orbital Core", "orbital_core", 3.6),
        ("Hyperspace", "hyperspace", 4.0),
        ("Matrix Code", "matrix_code", 3.2),
        ("HAL 9000", "hal_9000", 5.1),
        ("Split-Flap Board", "split_flap", 5.7),
    )
    card_width = 350
    card_height = 246
    start_x = 58
    start_y = 286
    hgap = 30
    vgap = 24
    for position, (name, style, elapsed) in enumerate(styles):
        column = position % 4
        row = position // 4
        left = start_x + column * (card_width + hgap)
        top = start_y + row * (card_height + vgap)
        _panel(image, (left, top, left + card_width, top + card_height), radius=18)
        draw.text((left + 18, top + 15), name, font=_font(20, True), fill=INK)
        deck = _screensaver_deck(style, elapsed)
        _paste_center(image, deck, (left + 16, top + 54, left + card_width - 16, top + card_height - 14))
    _footer(draw, "Eight real frames shown; three additional styles are also included in the application")
    image.save(OUT / "05-animated-screensavers.png", optimize=True)


def _layout_specs(count: int) -> list[dict]:
    base = [
        dict(icon_path="mdi:video-switch", label="Camera", active=True, bg="#1a5fb4"),
        dict(icon_path="mdi:record-circle", label="Record", active=True, bg="#a51d2d", badge="REC"),
        dict(icon_path="mdi:broadcast", label="Live", active=True, bg="#26845d", badge="LIVE"),
        dict(icon_path="mdi:microphone-off", label="Mic", active=True, bg="#a51d2d"),
        dict(icon_path="mdi:message-text", label="Chat", border="#e8a33a", badge="2"),
        dict(icon_path="mdi:home-automation", label="Home", active=True, bg="#1a5fb4"),
        dict(icon_path="mdi:lightbulb-on-outline", label="Light", active=True, bg="#584a18"),
        dict(label="CPU", center_text="43%", bg="#1e3a24"),
        dict(icon_path="mdi:folder", label="Scenes"),
        dict(icon_path="mdi:music-note", label="Audio", bg="#4b2a63"),
        dict(icon_path="mdi:timer-outline", label="Break", center_text="04:12", active=True),
        dict(icon_path="mdi:page-next", label="Next"),
        dict(icon_path="mdi:page-previous", label="Previous"),
        dict(icon_path="mdi:application", label="Apps"),
        dict(icon_path="mdi:playlist-check", label="Pre-flight", bg="#24334a"),
        dict(label="GPU", center_text="67%", bg="#1e3a24"),
        dict(label="Disk", center_text="41G"),
        dict(icon_path="mdi:movie-open", label="Replay"),
        dict(icon_path="mdi:volume-plus", label="Game +"),
        dict(icon_path="mdi:speaker-multiple", label="Headset", active=True),
        dict(icon_path="mdi:web", label="Endpoint", center_text="OK"),
        dict(icon_path="mdi:bookmark-plus", label="Marker"),
        dict(icon_path="mdi:camera", label="Capture"),
        dict(icon_path="mdi:monitor", label="Screen"),
        dict(icon_path="mdi:pause", label="Pause"),
        dict(icon_path="mdi:stop", label="Stop"),
        dict(icon_path="mdi:skip-previous", label="Prev"),
        dict(icon_path="mdi:play", label="Play"),
        dict(icon_path="mdi:skip-next", label="Next"),
        dict(icon_path="mdi:volume-minus", label="Vol -"),
        dict(icon_path="mdi:volume-plus", label="Vol +"),
        dict(icon_path="mdi:cog", label="Tools"),
    ]
    return base[:count]


def device_layouts_capture() -> None:
    from linuxstreamdeck.core.config import ActionStep, KeyConfig, KIND_DIAL
    from linuxstreamdeck.device.touchscreen import touchscreen_image

    image, draw = _base(
        "Adaptive hardware layouts",
        "From Mini to XL and Stream Deck +",
        "The connected device supplies its geometry; the application does not assume a fixed grid.",
    )

    layouts = (
        ("Mini  |  3 x 2", 6, 3, 60, 7, (70, 306, 420, 548)),
        ("Neo  |  4 x 2", 8, 4, 60, 7, (438, 306, 866, 548)),
        ("MK.2  |  5 x 3", 15, 5, 52, 6, (884, 306, 1530, 548)),
        ("XL  |  8 x 4", 32, 8, 39, 5, (70, 582, 668, 808)),
    )
    for name, count, columns, key_size, gap, box in layouts:
        left, top, right, bottom = box
        _panel(image, box, radius=18)
        draw.text((left + 18, top + 14), name, font=_font(19, True), fill=INK)
        deck = _deck_image(_key_images(_layout_specs(count), key_size), columns, key_size=key_size, gap=gap)
        _paste_center(image, deck, (left + 14, top + 48, right - 14, bottom - 12))

    plus_box = (694, 582, 1530, 808)
    _panel(image, plus_box, radius=18)
    draw.text((716, 596), "Stream Deck +  |  4 x 2, four encoders and LCD touch strip", font=_font(19, True), fill=INK)
    plus_deck = _deck_image(_key_images(_layout_specs(8), 52), 4, key_size=52, gap=6)
    image.paste(plus_deck, (718, 642))

    dials = {
        0: KeyConfig(
            kind=KIND_DIAL,
            label="Master",
            icon="mdi:volume-high",
            bg_color="#183650",
            steps_left=[ActionStep(action="sys.volume", params={"target": "output", "mode": "down", "amount": 5})],
            steps_right=[ActionStep(action="sys.volume", params={"target": "output", "mode": "up", "amount": 5})],
            steps_press=[ActionStep(action="sys.volume", params={"target": "output", "mode": "toggle", "amount": 5})],
        ),
        1: KeyConfig(
            kind=KIND_DIAL,
            label="Mic",
            icon="mdi:microphone",
            bg_color="#4a2028",
            steps_left=[ActionStep(action="sys.volume", params={"target": "input", "mode": "down", "amount": 5})],
            steps_right=[ActionStep(action="sys.volume", params={"target": "input", "mode": "up", "amount": 5})],
            steps_press=[ActionStep(action="sys.volume", params={"target": "input", "mode": "toggle", "amount": 5})],
        ),
        2: KeyConfig(
            kind=KIND_DIAL,
            label="Media",
            icon="mdi:music-note",
            bg_color="#352447",
            steps_left=[ActionStep(action="sys.media", params={"action": "Volume down", "show": "no"})],
            steps_right=[ActionStep(action="sys.media", params={"action": "Volume up", "show": "no"})],
            steps_press=[ActionStep(action="sys.media", params={"action": "Play / Pause", "show": "no"})],
        ),
        3: KeyConfig(
            kind=KIND_DIAL,
            label="Pages",
            icon="mdi:book-open-page-variant",
            bg_color="#173d32",
            steps_left=[ActionStep(action="nav.page.previous")],
            steps_right=[ActionStep(action="nav.page.next")],
        ),
    }
    strip = touchscreen_image(dials, size=(800, 100), count=4)
    strip = strip.resize((520, 65), Image.Resampling.LANCZOS)
    image.paste(strip, (985, 660))
    draw.text((985, 740), "Configured dial labels and icons use the real LCD renderer.", font=_font(14), fill=MUTED)

    _footer(draw, "MK.2 tested on physical hardware; other layouts are verified in simulation")
    image.save(OUT / "06-adaptive-device-layouts.png", optimize=True)


def main() -> None:
    main_window_capture()
    obs_capture()
    integrations_capture()
    games_capture()
    screensavers_capture()
    device_layouts_capture()
    print(f"Generated 6 marketing images in {OUT}")


if __name__ == "__main__":
    main()
