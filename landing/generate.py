#!/usr/bin/env python3
"""Rebuild everything on this site that is derived from the application.

The action catalogue, every key picture, the social sharing card and the
indexable copy of the action list all come from the real code, so the site
cannot drift from what the software does. Run it after adding an action or
changing how a key is drawn:

    LSD_CONFIG_DIR="$(mktemp -d)" .venv/bin/python landing/generate.py

`LSD_CONFIG_DIR` matters: importing the package reaches configuration code,
and this must never touch the real one.

Two options change what is written rather than merely refreshing it:

    --site-url https://example.com/   where the site will be served from
    --version X.Y.Z                   the version to stamp (default: the
                                      package's own)

`--site-url` is not decoration. Facebook, WhatsApp and X all refuse a
relative `og:image`, so the sharing card only appears if these files carry the
absolute address the site really answers on.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
IMG = HERE / "assets" / "img"
INDEX = HERE / "index.html"
sys.path.insert(0, str(ROOT))

if not os.environ.get("LSD_CONFIG_DIR"):
    raise SystemExit(
        "Refusing to run without LSD_CONFIG_DIR set to a temporary directory."
    )

# The sharing card. 1200x630 is the size Facebook, X, LinkedIn, WhatsApp,
# Telegram, Slack and Discord all render whole rather than crop, so it is the
# one shape worth building. Everything is kept a clear margin inside the edges
# regardless, since a client that decides to crop always crops inwards.
OG_WIDTH, OG_HEIGHT = 1200, 630


# --------------------------------------------------------------- catalogue

def catalogue() -> dict:
    """Every registered action, as the search index reads it."""
    from linuxstreamdeck import basic_actions  # noqa: F401
    from linuxstreamdeck import ha_actions  # noqa: F401
    from linuxstreamdeck import light_actions  # noqa: F401
    from linuxstreamdeck import system_stats  # noqa: F401
    from linuxstreamdeck import web_actions  # noqa: F401
    from linuxstreamdeck.obs import actions as _obs  # noqa: F401
    from linuxstreamdeck.twitch import actions as _twitch  # noqa: F401
    from linuxstreamdeck import VERSION
    from linuxstreamdeck.core.actions import REGISTRY

    actions = []
    for action in REGISTRY.values():
        actions.append({
            "id": action.id,
            "name": action.name,
            "category": action.category,
            "description": " ".join((action.description or "").split()),
            "icon": action.default_icon,
            "params": [
                {
                    "name": p.name,
                    "label": p.label,
                    "kind": p.kind,
                    "choices": [str(c) for c in (p.choices or [])],
                }
                for p in action.params
            ],
            "needs": [
                name for name, wanted in (
                    ("OBS", action.needs_obs),
                    ("Twitch", action.needs_twitch),
                    ("Home Assistant", action.requires_home_assistant({})),
                ) if wanted
            ],
        })
    # This is the only place the catalogue order is decided. app.js renders the
    # file as it comes, so the list a crawler reads and the list a visitor sees
    # cannot disagree. Case-folded because a plain sort puts every capitalised
    # name above every lowercase one: "Open URL" landed above "Open
    # application", which reads as no order at all.
    actions.sort(key=lambda a: (a["category"].lower(), a["name"].lower()))
    return {"version": VERSION, "actions": actions}


# ---------------------------------------------------------------- pictures

def _demo_cover(size: int = 192) -> bytes:
    """An invented album sleeve, as JPEG bytes for `compose(image=...)`.

    Only ever used to show what a now-playing key looks like. The real one
    comes from whatever MPRIS player is running.
    """
    from PIL import Image, ImageDraw

    cover = Image.new("RGB", (size, size))
    draw = ImageDraw.Draw(cover)
    for y in range(size):
        t = y / (size - 1)
        draw.line(
            [(0, y), (size, y)],
            fill=(int(28 + 92 * t), int(16 + 24 * t), int(58 + 96 * t)),
        )
    draw.ellipse(
        [size * 0.16, size * 0.16, size * 0.84, size * 0.84],
        outline=(240, 196, 96), width=max(2, size // 32),
    )
    draw.ellipse(
        [size * 0.40, size * 0.40, size * 0.60, size * 0.60], fill=(240, 196, 96)
    )
    import io

    buffer = io.BytesIO()
    cover.save(buffer, "JPEG", quality=88)
    return buffer.getvalue()


def pictures() -> list[str]:
    """Key images composed offscreen, exactly as the deck draws them.

    Never a screenshot of a running window: the application is single
    instance, and launching it to take one leaves a stale, cached view. These
    go through the real renderer instead, so what the site shows is what the
    hardware shows.
    """
    from PIL import Image

    from linuxstreamdeck.device import renderer

    made: list[str] = []
    KEY, GAP = 96, 10

    def sheet(name: str, rows: list[list[dict]], bg="#15161a") -> None:
        cols = max(len(row) for row in rows)
        width = cols * KEY + (cols + 1) * GAP
        height = len(rows) * KEY + (len(rows) + 1) * GAP
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        for y, row in enumerate(rows):
            for x, spec in enumerate(row):
                if spec is None:
                    continue
                tile = renderer.compose(size=(KEY, KEY), **spec).convert("RGBA")
                canvas.paste(tile, (GAP + x * (KEY + GAP), GAP + y * (KEY + GAP)))
        canvas.save(IMG / f"{name}.png")
        made.append(name)

    # A streaming page, with the live feedback that is the whole point.
    sheet("keys-obs", [[
        dict(icon_path="mdi:video-switch", label="Camera", active=True,
             bg="#1a5fb4"),
        dict(icon_path="mdi:monitor-screenshot", label="Screen"),
        dict(icon_path="mdi:record-circle", label="Record", active=True,
             bg="#a51d2d", badge="●"),
        dict(icon_path="mdi:broadcast", label="Go live", active=True,
             bg="#26a269", badge="LIVE"),
        dict(icon_path="mdi:microphone-off", label="Mic", active=True,
             bg="#a51d2d"),
    ]])

    # Everything that draws a live value.
    sheet("keys-live", [[
        dict(label="CPU", center_text="43%", bg="#1e3a24"),
        dict(label="CPU °", center_text="88°", bg="#5a4410"),
        dict(label="GPU", center_text="97%", bg="#5a4410"),
        dict(label="Upload", center_text="6.0Mb"),
        dict(label="Disk", center_text="2.9G", bg="#5c1622"),
        dict(label="Deaths", center_text="7"),
    ]])

    # The three services.
    sheet("keys-services", [[
        dict(icon_path="mdi:message-text", label="Chat", border="#e8a33a",
             center_text="2m", badge="3"),
        dict(icon_path="mdi:movie-open", label="Clip"),
        dict(icon_path="mdi:home-automation", label="Kitchen", active=True,
             bg="#1a5fb4"),
        dict(icon_path="mdi:home-thermometer", label="Office",
             center_text="21.4"),
        dict(icon_path="mdi:lightbulb-on-outline", label="Key Light",
             active=True),
        dict(icon_path="mdi:web", label="Uptime", center_text="99.9"),
    ]])

    # Audio, which is the part no other Linux deck does per application.
    sheet("keys-audio", [[
        dict(icon_path="mdi:microphone", label="Mic"),
        dict(icon_path="mdi:microphone-off", label="Mic", active=True,
             bg="#a51d2d"),
        dict(icon_path="mdi:volume-high", label="Game +"),
        dict(icon_path="mdi:volume-off", label="Discord", active=True,
             bg="#a51d2d"),
        dict(icon_path="mdi:speaker-multiple", label="Headset", active=True),
        dict(icon_path="mdi:music-note", label="Airhorn"),
    ]])

    # Transport, with the per-action icons, plus the now-playing key that is
    # the point of that section. Its cover is generated rather than borrowed:
    # a real sleeve here would be somebody else's artwork, and pairing a real
    # one with an invented artist would be worse.
    sheet("keys-media", [[
        dict(icon_path="mdi:skip-previous", label="Prev"),
        dict(image=_demo_cover(), label="Static Palm"),
        dict(icon_path="mdi:skip-next", label="Next"),
        dict(icon_path="mdi:volume-minus", label="Vol -"),
        dict(icon_path="mdi:volume-plus", label="Vol +"),
    ]])

    # One key that cannot work, next to one that failed: the deck says so.
    sheet("keys-honest", [[
        dict(icon_path="mdi:video-switch", label="Scene",
             unavailable=True),
        dict(icon_path="mdi:play", label="Media", failed=True),
        dict(icon_path="mdi:playlist-play", label="Go live", busy=True,
             busy_phase=True, badge="RUN"),
        dict(icon_path="mdi:folder", label="Scenes"),
        dict(icon_path="mdi:timer-outline", label="Break",
             center_text="04:12", active=True),
    ]])
    return made


def screensaver() -> None:
    """One frame of an animated screen saver, on the real grid."""
    from PIL import Image

    from linuxstreamdeck.device.screensaver import screensaver_frame

    frame = screensaver_frame(
        "hyperspace", elapsed=4.0, key_count=15, key_size=(96, 96),
        intensity=100, columns=5,
    )
    images = frame.images
    KEY, GAP = 96, 10
    canvas = Image.new("RGBA", (5 * KEY + 6 * GAP, 3 * KEY + 4 * GAP), (0, 0, 0, 0))
    for index, raw in enumerate(images):
        tile = raw if isinstance(raw, Image.Image) else Image.open(raw)
        canvas.paste(
            tile.convert("RGBA"),
            (GAP + (index % 5) * (KEY + GAP), GAP + (index // 5) * (KEY + GAP)),
        )
    canvas.save(IMG / "keys-screensaver.png")


# ------------------------------------------------------------------ icons

def _logo(size: int):
    """The application icon, rasterized at `size` through GdkPixbuf.

    GdkPixbuf comes with PyGObject, which this project already depends on, so
    this adds nothing to install. No standalone rasterizer (rsvg-convert,
    Inkscape, ImageMagick) can be assumed present.
    """
    import gi

    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
    from PIL import Image

    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(
        str(IMG / "logo.svg"), size, size
    )
    mode = "RGBA" if pixbuf.get_has_alpha() else "RGB"
    return Image.frombytes(
        mode, (pixbuf.get_width(), pixbuf.get_height()),
        pixbuf.get_pixels(), "raw", mode, pixbuf.get_rowstride(),
    ).convert("RGBA")


def favicons() -> list[str]:
    """A PNG icon beside the SVG one, and one for an iOS home screen.

    The SVG alone is enough for current desktop browsers and not enough for
    everything else: several feed readers and older mobile browsers only look
    for a PNG, and iOS composites an apple-touch-icon onto its own background,
    so that one is given the site's own instead of transparency.
    """
    from PIL import Image

    from linuxstreamdeck.core.icons import RENDER_LOCK

    made = []
    with RENDER_LOCK:
        for name, size in (("favicon-32.png", 32), ("favicon-180.png", 180)):
            icon = _logo(size)
            if size >= 180:  # iOS has no transparency; give it the site's own.
                plate = Image.new("RGBA", (size, size), (14, 15, 19, 255))
                plate.alpha_composite(icon)
                icon = plate
            icon.save(IMG / name)
            made.append(name)
    return made


# ----------------------------------------------------------- sharing card

def social_card(version: str, action_count: int) -> None:
    """The 1200x630 image a link to this site unfurls into.

    Sharing the plain window screenshot was the obvious thing and reads badly
    at the size a timeline shows it: the point of the project is not legible,
    and cropped to WhatsApp's square it becomes an unidentifiable grey panel.
    This one says what the software is in the two lines anyone actually reads,
    over keys drawn by the real renderer.
    """
    from PIL import Image, ImageDraw, ImageFilter

    from linuxstreamdeck.core.icons import RENDER_LOCK
    from linuxstreamdeck.device import renderer

    with RENDER_LOCK:
        card = Image.new("RGB", (OG_WIDTH, OG_HEIGHT), (14, 15, 19))

        # One soft accent wash behind the title, so the card is not a flat
        # rectangle in a feed full of flat rectangles.
        glow = Image.new("RGB", (OG_WIDTH, OG_HEIGHT), (14, 15, 19))
        ImageDraw.Draw(glow).ellipse(
            [-160, -300, 700, 360], fill=(26, 58, 104)
        )
        card = Image.blend(card, glow.filter(ImageFilter.GaussianBlur(150)), 0.85)
        draw = ImageDraw.Draw(card)

        card.paste(_logo(76), (72, 62), _logo(76))
        draw.text((166, 78), "LinuxStreamDeck", font=renderer._font(40),
                  fill=(231, 233, 238))

        draw.text((72, 186), "Your Elgato Stream Deck,", font=renderer._font(62),
                  fill=(255, 255, 255))
        draw.text((72, 258), "at home on Linux.", font=renderer._font(62),
                  fill=(74, 158, 255))

        draw.text(
            (74, 356),
            "Deep OBS Studio integration, plus Twitch, Home Assistant,\n"
            "Key Lights and per-application audio — built in.",
            font=renderer._font(27), fill=(160, 166, 180), spacing=12,
        )

        # Real keys, drawn by the renderer that draws the hardware.
        keys = [
            dict(icon_path="mdi:video-switch", label="Camera", active=True,
                 bg="#1a5fb4"),
            dict(icon_path="mdi:record-circle", label="Record", active=True,
                 bg="#a51d2d", badge="●"),
            dict(icon_path="mdi:broadcast", label="Go live", active=True,
                 bg="#26a269", badge="LIVE"),
            dict(icon_path="mdi:microphone-off", label="Mic", active=True,
                 bg="#a51d2d"),
            dict(icon_path="mdi:message-text", label="Chat", border="#e8a33a",
                 center_text="2m", badge="3"),
            dict(icon_path="mdi:home-automation", label="Kitchen", active=True,
                 bg="#1a5fb4"),
        ]
        size, gap = 92, 14
        top = OG_HEIGHT - size - 62
        for index, spec in enumerate(keys):
            card.paste(
                renderer.compose(size=(size, size), **spec).convert("RGB"),
                (72 + index * (size + gap), top),
            )

        draw.text(
            (72, OG_HEIGHT - 46),
            f"{action_count} actions · GPL-3.0 · v{version}"
            "   github.com/javocsoft/linuxstreamdeck",
            font=renderer._font(21), fill=(111, 118, 132),
        )
        card.save(IMG / "og-card.png", optimize=True)


# ------------------------------------------------------- SEO / social HTML

def _escape(value: str) -> str:
    return (
        str(value).replace("&", "&amp;").replace("<", "&lt;")
        .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;")
    )


def _card_html(action: dict) -> str:
    """One action card, byte-identical to what `app.js` builds for it.

    The catalogue is the most useful thing on this page and it arrives over
    `fetch`, so to anything that does not run JavaScript the page has no
    content at all - which is most social scrapers and every crawler that only
    reads the served HTML. Writing the unsearched list into the document makes
    it indexable, and makes the page usable with scripting off; the first
    render replaces it with exactly the same markup, so nothing moves.
    """
    needs = "".join(
        f'<li class="need">needs {_escape(n)}</li>' for n in action["needs"]
    )
    params = "".join(
        f'<li>{_escape(p["label"])}</li>' for p in action["params"][:6]
    )
    return (
        '<article class="card">'
        "<header>"
        f'<h4>{_escape(action["name"])}</h4>'
        f'<span class="cat">{_escape(action["category"])}</span>'
        "</header>"
        f'<p>{_escape(action["description"])}</p>'
        f'<ul>{needs}{params}<li class="aid">{_escape(action["id"])}</li></ul>'
        "</article>"
    )


def _region(html: str, name: str, body: str) -> str:
    """Replace one `<!-- generated:NAME -->…<!-- /generated:NAME -->` block."""
    open_tag, close_tag = f"<!-- generated:{name} -->", f"<!-- /generated:{name} -->"
    pattern = re.compile(
        re.escape(open_tag) + ".*?" + re.escape(close_tag), re.S
    )
    if not pattern.search(html):
        raise SystemExit(f"index.html has no generated:{name} region")
    return pattern.sub(lambda _: open_tag + body + close_tag, html)


def current_site_url() -> str:
    """Where index.html currently says it is served from."""
    match = re.search(
        r'<link rel="canonical" href="([^"]+)"', INDEX.read_text(encoding="utf-8")
    )
    if not match:
        raise SystemExit("index.html has no canonical link to read the site URL from")
    return match.group(1)


def seo(data: dict, site_url: str) -> None:
    """Stamp the address, the version and the indexable catalogue into the page.

    Also writes robots.txt and sitemap.xml, which need the same address.
    """
    html = INDEX.read_text(encoding="utf-8")
    previous = current_site_url()
    if site_url != previous:
        # Every absolute reference is built from this one string, so replacing
        # it moves the canonical link, og:url, og:image, twitter:image and the
        # structured data together. They cannot drift apart.
        html = html.replace(previous, site_url)

    actions = data["actions"]
    html = _region(html, "cards", "".join(_card_html(a) for a in actions))
    html = _region(html, "count", f"{len(actions)} actions")
    html = re.sub(
        r"(<(b|span) data-version>)[^<]*(</\2>)",
        lambda m: m.group(1) + _escape(data["version"]) + m.group(3), html,
    )
    html = re.sub(
        r"(<(b|span) data-action-count>)[^<]*(</\2>)",
        lambda m: m.group(1) + str(len(actions)) + m.group(3), html,
    )
    INDEX.write_text(html, encoding="utf-8")

    (HERE / "robots.txt").write_text(
        "User-agent: *\nAllow: /\n\nSitemap: " + site_url + "sitemap.xml\n",
        encoding="utf-8",
    )
    # One page, so the sitemap exists to declare the canonical address rather
    # than to enumerate anything. The guide anchors are part of this document
    # and must not be listed as separate URLs.
    (HERE / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f" <url><loc>{_escape(site_url)}</loc><changefreq>monthly</changefreq>"
        "<priority>1.0</priority></url>\n"
        "</urlset>\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-url", default=None,
                        help="absolute address the site is served from")
    parser.add_argument("--version", default=None,
                        help="version to stamp (default: the package's own)")
    options = parser.parse_args()

    site_url = options.site_url or current_site_url()
    if not site_url.startswith(("http://", "https://")):
        raise SystemExit(f"--site-url must be absolute, got {site_url!r}")
    if not site_url.endswith("/"):
        site_url += "/"

    IMG.mkdir(parents=True, exist_ok=True)
    data = catalogue()
    if options.version:
        data["version"] = options.version
    (HERE / "assets" / "actions.json").write_text(
        json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"actions.json: {len(data['actions'])} actions, v{data['version']}")
    for name in pictures():
        print(f"  {name}.png")
    try:
        screensaver()
        print("  keys-screensaver.png")
    except Exception as error:  # pragma: no cover - decorative only
        print(f"  (screen saver frame skipped: {error})")
    for name in favicons():
        print(f"  {name}")
    social_card(data["version"], len(data["actions"]))
    print("  og-card.png")
    seo(data, site_url)
    print(f"index.html, robots.txt, sitemap.xml: {site_url}")
