"""Diálogo para elegir un icono de la biblioteca integrada.

Muestra una rejilla de iconos filtrable por categoría y por texto. Devuelve la
referencia elegida (p.ej. "mdi:home") a través de un callback.
"""

from __future__ import annotations

import io
import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from ..core.icons import library  # noqa: E402

log = logging.getLogger(__name__)

THUMB = 44           # tamaño del glifo en la miniatura
RESULT_LIMIT = 400   # tope de iconos mostrados a la vez
_THUMB_COLOR = "#e3e3e8"

_CSS = b"""
.icon-cell { padding: 6px; border-radius: 8px; }
.icon-cell:hover { background: alpha(@accent_bg_color, 0.20); }
"""


def _texture(ref: str) -> Gdk.Texture | None:
    img = library.render(ref, THUMB, _THUMB_COLOR)
    if img is None:
        return None
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Gdk.Texture.new_from_bytes(GLib.Bytes.new(buf.getvalue()))


class IconPickerDialog(Adw.Window):
    def __init__(self, parent, on_selected) -> None:
        super().__init__(
            transient_for=parent, modal=True, title="Biblioteca de iconos",
            default_width=680, default_height=560,
        )
        self._on_selected = on_selected
        self._search_source = 0

        css = Gtk.CssProvider()
        css.load_from_data(_CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        view = Adw.ToolbarView()
        header = Adw.HeaderBar()

        self.category_dd = Gtk.DropDown.new_from_strings(
            ["Todas las categorías"] + library.categories()
        )
        self.category_dd.connect("notify::selected", lambda *_: self._schedule())
        header.pack_start(self.category_dd)
        view.add_top_bar(header)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                      margin_top=10, margin_bottom=10, margin_start=10, margin_end=10)

        self.search = Gtk.SearchEntry(placeholder_text="Buscar icono (p.ej. mic, camera, record)…")
        self.search.connect("search-changed", lambda *_: self._schedule())
        box.append(self.search)

        self.count_label = Gtk.Label(xalign=0)
        self.count_label.add_css_class("dim-label")
        box.append(self.count_label)

        self.flow = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
            min_children_per_line=4, max_children_per_line=12,
            row_spacing=4, column_spacing=4, valign=Gtk.Align.START,
        )
        scroller = Gtk.ScrolledWindow(child=self.flow, vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        box.append(scroller)

        view.set_content(box)
        self.set_content(view)
        self._populate()

    # ---------- filtrado ----------

    def _current_category(self) -> str:
        i = self.category_dd.get_selected()
        return "" if i <= 0 else library.categories()[i - 1]

    def _schedule(self) -> None:
        # agrupa pulsaciones rápidas para no repoblar en cada tecla
        if self._search_source:
            GLib.source_remove(self._search_source)
        self._search_source = GLib.timeout_add(180, self._populate)

    def _populate(self) -> bool:
        self._search_source = 0
        self._clear()
        icons, total = library.search(
            self.search.get_text(), self._current_category(), RESULT_LIMIT
        )
        for icon in icons:
            self.flow.append(self._cell(icon))
        if total > len(icons):
            self.count_label.set_label(
                f"Mostrando {len(icons)} de {total} — afina la búsqueda para ver más"
            )
        else:
            self.count_label.set_label(f"{total} iconos")
        return False  # no repetir (timeout de un disparo)

    def _cell(self, icon) -> Gtk.Button:
        button = Gtk.Button()
        button.add_css_class("flat")
        button.add_css_class("icon-cell")
        button.set_tooltip_text(icon.name)
        button.connect("clicked", self._choose, icon.ref)
        image = Gtk.Image()
        image.set_pixel_size(THUMB)
        image.set_size_request(THUMB, THUMB)
        if (tex := _texture(icon.ref)) is not None:
            image.set_from_paintable(tex)
        button.set_child(image)
        return button

    def _choose(self, _btn, ref: str) -> None:
        self._on_selected(ref)
        self.close()

    def _clear(self) -> None:
        while (child := self.flow.get_first_child()) is not None:
            self.flow.remove(child)
