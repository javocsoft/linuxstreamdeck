"""Built-in icon library (Material Design Icons).

A single font file (.ttf) provides ~7,400 vector icons that Pillow renders
directly at any size and color, without depending on SVG or the system icon
theme.

Icon references used in the configuration:
  - "mdi:home"          → library glyph by name
  - "/path/image.png"   → the user's own image (any absolute path)
  - ""                  → no icon
"""

from __future__ import annotations

import json
import logging
import threading
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)

# FreeType (used by Pillow to draw text/glyphs) is NOT thread-safe. The deck is
# rendered on a worker thread and the icon picker/preview on the main thread;
# without serializing, concurrent use corrupts the glyphs (they come out blank).
# This reentrant lock protects ALL text drawing, both here and in
# device/renderer.py (which imports it).
RENDER_LOCK = threading.RLock()

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "icons"
FONT_FILE = ASSETS / "materialdesignicons-webfont.ttf"
INDEX_FILE = ASSETS / "icons.json"

LIBRARY_PREFIX = "mdi:"


class Icon:
    __slots__ = ("name", "codepoint", "category", "search")

    def __init__(self, name: str, codepoint: str, category: str, search: str):
        self.name = name
        self.codepoint = int(codepoint, 16)
        self.category = category
        self.search = search

    @property
    def ref(self) -> str:
        return LIBRARY_PREFIX + self.name


class IconLibrary:
    """Icon index + glyph rendering with Pillow."""

    def __init__(self) -> None:
        self._by_name: dict[str, Icon] = {}
        self._categories: list[str] = []
        self._loaded = False

    # ---------- lazy loading ----------

    def _ensure(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            data = json.loads(INDEX_FILE.read_text())
        except Exception:
            log.exception("Could not read the icon library %s", INDEX_FILE)
            return
        cats: set[str] = set()
        for it in data.get("icons", []):
            icon = Icon(it["n"], it["c"], it.get("cat", "Other"), it.get("s", it["n"]))
            self._by_name[icon.name] = icon
            cats.add(icon.category)
        self._categories = sorted(cats)
        log.info("Icon library: %d icons, %d categories",
                 len(self._by_name), len(self._categories))

    # ---------- queries ----------

    def available(self) -> bool:
        self._ensure()
        return bool(self._by_name)

    def categories(self) -> list[str]:
        self._ensure()
        return self._categories

    def get(self, name: str) -> Icon | None:
        self._ensure()
        return self._by_name.get(name)

    def search(self, query: str = "", category: str = "", limit: int = 400):
        """Return (list of Icon up to `limit`, total number of matches)."""
        self._ensure()
        query = (query or "").strip().lower()
        terms = query.split()
        results = []
        total = 0
        for icon in self._by_name.values():
            if category and icon.category != category:
                continue
            if terms and not all(t in icon.search for t in terms):
                continue
            total += 1
            if len(results) < limit:
                results.append(icon)
        # the ones that start with the query first
        if query:
            results.sort(key=lambda i: (not i.name.startswith(query), i.name))
        else:
            results.sort(key=lambda i: i.name)
        return results, total

    # ---------- rendering ----------

    @staticmethod
    def is_library_ref(ref: str) -> bool:
        return bool(ref) and ref.startswith(LIBRARY_PREFIX)

    def render(self, ref: str, box: int, color: str = "#ffffff") -> Image.Image | None:
        """Render an icon reference to a `box`×`box` RGBA image.

        Accepts both "mdi:name" and a file path.
        """
        if self.is_library_ref(ref):
            name = ref[len(LIBRARY_PREFIX):]
            icon = self.get(name)
            if icon is None:
                return None
            return self._render_glyph(icon.codepoint, box, color)
        return _load_image(ref, box)

    def _render_glyph(self, codepoint: int, box: int, color: str) -> Image.Image | None:
        return _render_glyph_cached(str(FONT_FILE), codepoint, box, color)


# ---------- module-level caches ----------

@lru_cache(maxsize=32)
def _font(size: int) -> ImageFont.FreeTypeFont:
    # BASIC layout: avoids Pillow's raqm/harfbuzz engine, whose global state
    # clashes with the system harfbuzz (GTK/Pango) and corrupted the render.
    return ImageFont.truetype(str(FONT_FILE), size, layout_engine=ImageFont.Layout.BASIC)


# Manual cache: ONLY correct renders are stored. A transient failure (blank
# glyph) is NOT cached, so it does not stay blank for the whole session.
_glyph_cache: dict = {}


def _draw_glyph(codepoint: int, box: int, color: str) -> Image.Image | None:
    """Draw the centered glyph. Returns None if FreeType drew nothing.

    It is drawn at normal size with textbbox (no anchor="mm" or supersampling),
    the REAL ink is measured and shifted to the exact center (the font metrics
    leave a ~3px bias).
    """
    font = _font(max(8, int(box * 0.86)))
    img = Image.new("RGBA", (box, box), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    ch = chr(codepoint)
    bbox = draw.textbbox((0, 0), ch, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((box - w) / 2 - bbox[0], (box - h) / 2 - bbox[1]),
              ch, font=font, fill=color)
    ink = img.getbbox()
    if ink is None:
        return None                       # the glyph drew nothing (shouldn't happen)
    dx = round(box / 2 - (ink[0] + ink[2]) / 2)
    dy = round(box / 2 - (ink[1] + ink[3]) / 2)
    if dx or dy:
        centered = Image.new("RGBA", (box, box), (0, 0, 0, 0))
        centered.paste(img, (dx, dy))
        img = centered
    return img


def _render_glyph_cached(font_file: str, codepoint: int, box: int, color: str) -> Image.Image | None:
    """Render (and cache) a glyph from the icon font.

    Manual cache: ONLY correct renders are stored (a safety net in case one ever
    comes out blank, so it does not stay blank for the whole session).
    """
    key = (font_file, codepoint, box, color)
    with RENDER_LOCK:                     # serializes PIL rendering across our threads
        cached = _glyph_cache.get(key)
        if cached is not None:
            return cached
        try:
            img = _draw_glyph(codepoint, box, color)
        except Exception:
            log.debug("Error drawing glyph %#x", codepoint, exc_info=True)
            img = None
        if img is not None:
            _glyph_cache[key] = img
        return img


def clear_glyph_cache() -> None:
    _glyph_cache.clear()


@lru_cache(maxsize=128)
def _load_image(path: str, box: int) -> Image.Image | None:
    if not path:
        return None
    try:
        img = Image.open(path).convert("RGBA")
        img.thumbnail((box, box), Image.LANCZOS)
        return img
    except Exception:
        log.warning("Could not load the image %s", path)
        return None


# shared instance
library = IconLibrary()
