"""Biblioteca de iconos integrada (Material Design Icons).

Un único archivo de fuente (.ttf) proporciona ~7.400 iconos vectoriales que
Pillow renderiza directamente a cualquier tamaño y color, sin depender de SVG
ni del tema de iconos del sistema.

Referencias de icono usadas en la configuración:
  - "mdi:home"          → glifo de la biblioteca por nombre
  - "/ruta/imagen.png"  → imagen propia del usuario (cualquier ruta absoluta)
  - ""                  → sin icono
"""

from __future__ import annotations

import json
import logging
import threading
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)

# FreeType (usado por Pillow para dibujar texto/glifos) NO es seguro entre hilos.
# El deck se renderiza en un hilo de trabajo y el selector/vista previa en el
# hilo principal; sin serializar, el uso concurrente corrompe los glifos (salen
# en blanco). Este candado reentrante protege TODO el dibujado de texto, tanto
# aquí como en device/renderer.py (que lo importa).
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
    """Índice de iconos + renderizado de glifos con Pillow."""

    def __init__(self) -> None:
        self._by_name: dict[str, Icon] = {}
        self._categories: list[str] = []
        self._loaded = False

    # ---------- carga perezosa ----------

    def _ensure(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            data = json.loads(INDEX_FILE.read_text())
        except Exception:
            log.exception("No se pudo leer la biblioteca de iconos %s", INDEX_FILE)
            return
        cats: set[str] = set()
        for it in data.get("icons", []):
            icon = Icon(it["n"], it["c"], it.get("cat", "Otros"), it.get("s", it["n"]))
            self._by_name[icon.name] = icon
            cats.add(icon.category)
        self._categories = sorted(cats)
        log.info("Biblioteca de iconos: %d iconos, %d categorías",
                 len(self._by_name), len(self._categories))

    # ---------- consulta ----------

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
        """Devuelve (lista de Icon hasta `limit`, total de coincidencias)."""
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
        # los que empiezan por la consulta primero
        if query:
            results.sort(key=lambda i: (not i.name.startswith(query), i.name))
        else:
            results.sort(key=lambda i: i.name)
        return results, total

    # ---------- renderizado ----------

    @staticmethod
    def is_library_ref(ref: str) -> bool:
        return bool(ref) and ref.startswith(LIBRARY_PREFIX)

    def render(self, ref: str, box: int, color: str = "#ffffff") -> Image.Image | None:
        """Renderiza una referencia de icono a una imagen RGBA de `box`×`box`.

        Acepta tanto "mdi:nombre" como una ruta de archivo.
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


# ---------- cachés a nivel de módulo ----------

@lru_cache(maxsize=32)
def _font(size: int) -> ImageFont.FreeTypeFont:
    # layout BASIC: evita el motor raqm/harfbuzz de Pillow, cuyo estado global
    # choca con el harfbuzz del sistema (GTK/Pango) y corrompía el render.
    return ImageFont.truetype(str(FONT_FILE), size, layout_engine=ImageFont.Layout.BASIC)


# Caché manual: SOLO se guardan los renders correctos. Un fallo transitorio
# (glifo vacío) NO se cachea, para que no quede en blanco toda la sesión.
_glyph_cache: dict = {}


def _draw_glyph(codepoint: int, box: int, color: str) -> Image.Image | None:
    """Dibuja el glifo centrado. Devuelve None si FreeType dibujó vacío.

    Se dibuja a tamaño normal con textbbox (sin anchor="mm" ni supermuestreo),
    se mide la tinta REAL y se desplaza al centro exacto (las métricas de la
    fuente dejan un sesgo de ~3px).
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
        return None                       # el glifo no dibujó nada (no debería)
    dx = round(box / 2 - (ink[0] + ink[2]) / 2)
    dy = round(box / 2 - (ink[1] + ink[3]) / 2)
    if dx or dy:
        centered = Image.new("RGBA", (box, box), (0, 0, 0, 0))
        centered.paste(img, (dx, dy))
        img = centered
    return img


def _render_glyph_cached(font_file: str, codepoint: int, box: int, color: str) -> Image.Image | None:
    """Renderiza (y cachea) un glifo de la fuente de iconos.

    Caché manual: SOLO se guardan los renders correctos (red de seguridad por si
    alguna vez saliera vacío, para que no quede en blanco toda la sesión).
    """
    key = (font_file, codepoint, box, color)
    with RENDER_LOCK:                     # serializa el render PIL entre nuestros hilos
        cached = _glyph_cache.get(key)
        if cached is not None:
            return cached
        try:
            img = _draw_glyph(codepoint, box, color)
        except Exception:
            log.debug("Error dibujando glifo %#x", codepoint, exc_info=True)
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
        log.warning("No se pudo cargar la imagen %s", path)
        return None


# instancia compartida
library = IconLibrary()
