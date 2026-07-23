"""Ventana principal: rejilla que refleja el deck físico (y sirve de deck
virtual para probar sin hardware) + editor de teclas + barra de estado."""

from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

from .. import APP_NAME  # noqa: E402
from .editor import EditorPanel  # noqa: E402
from .obs_settings import ObsSettingsDialog  # noqa: E402

log = logging.getLogger(__name__)

GRID_COLS = 5
KEY_PIXELS = 96

_CSS = b"""
.deck-key {
    padding: 3px;
    border-radius: 10px;
    border: 2px solid transparent;   /* reservado siempre para no reflowar la rejilla */
}
.deck-key.sel { border-color: @accent_bg_color; }
.statusbar { padding: 4px 10px; font-size: 0.85em; }
"""


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app) -> None:
        super().__init__(application=app.gtk_app, title=APP_NAME)
        self.app = app
        self.set_default_size(980, 560)
        self.selected: int | None = None
        self._key_buttons: list[Gtk.Button] = []
        self._key_pictures: list[Gtk.Picture] = []
        self._updating_pages = False
        self._clipboard = None            # KeyConfig copiada (para pegar)

        css = Gtk.CssProvider()
        css.load_from_data(_CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self._setup_key_editing()
        self._build_ui()
        self._connect_bus()
        self._refresh_page_dropdown()

    # ---------- construcción ----------

    def _build_ui(self) -> None:
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()

        # selector de páginas + añadir página
        self.page_dropdown = Gtk.DropDown.new_from_strings([])
        self.page_dropdown.connect("notify::selected", self._on_page_selected)
        header.pack_start(self.page_dropdown)
        add_btn = Gtk.Button.new_from_icon_name("list-add-symbolic")
        add_btn.set_tooltip_text("Añadir página")
        add_btn.connect("clicked", self._on_add_page)
        header.pack_start(add_btn)

        # brillo + ajustes OBS
        self.obs_btn = Gtk.Button.new_from_icon_name("network-offline-symbolic")
        self.obs_btn.set_tooltip_text("Ajustes de conexión con OBS")
        self.obs_btn.connect("clicked", self._on_obs_settings)
        header.pack_end(self.obs_btn)
        brightness = Gtk.ScaleButton.new(
            10, 100, 10, ["display-brightness-symbolic"]
        )
        brightness.set_value(self.app.config.brightness)
        brightness.connect("value-changed", self._on_brightness)
        header.pack_end(brightness)

        view.add_top_bar(header)

        # contenido: rejilla + editor
        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)

        grid_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER, hexpand=True,
        )
        grid = Gtk.Grid(row_spacing=10, column_spacing=10)
        rows = (self.app.deck.key_count + GRID_COLS - 1) // GRID_COLS
        for index in range(self.app.deck.key_count):
            btn = Gtk.Button()
            btn.add_css_class("deck-key")
            pic = Gtk.Picture()
            pic.set_size_request(KEY_PIXELS, KEY_PIXELS)
            btn.set_child(pic)
            btn.connect("clicked", self._on_key_clicked, index)
            self._add_key_dnd(btn, index)
            self._add_key_contextmenu(btn, index)
            self._add_key_shortcuts(btn, index)
            grid.attach(btn, index % GRID_COLS, index // GRID_COLS, 1, 1)
            self._key_buttons.append(btn)
            self._key_pictures.append(pic)
        grid_box.append(grid)
        hint = Gtk.Label(
            label="Arrastra para mover · clic derecho para copiar/pegar · "
                  "«Probar» ejecuta la acción"
        )
        hint.add_css_class("dim-label")
        hint.set_margin_top(12)
        grid_box.append(hint)
        content.append(grid_box)

        content.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        self.editor = EditorPanel(self.app)
        self.editor.set_size_request(380, -1)
        self.editor.set_vexpand(True)
        content.append(self.editor)

        view.set_content(content)

        # barra de estado
        self.status = Gtk.Label(label="", xalign=0)
        self.status.add_css_class("statusbar")
        view.add_bottom_bar(self.status)

        self.set_content(view)
        self._update_status()

    def _connect_bus(self) -> None:
        bus = self.app.bus
        bus.subscribe("ui.key_image", self._on_key_image)
        bus.subscribe("page.changed", lambda t, d: self._on_page_changed())
        for topic in ("deck.connected", "deck.disconnected",
                      "obs.connected", "obs.disconnected"):
            bus.subscribe(topic, lambda t, d: self._update_status())
        bus.subscribe("status", lambda t, d: self._flash_status(d.get("text", "")))

    # ---------- callbacks ----------

    def _on_key_image(self, topic: str, data: dict) -> None:
        index, png = data["index"], data["png"]
        if index < len(self._key_pictures):
            texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(png))
            self._key_pictures[index].set_paintable(texture)

    def _on_key_clicked(self, btn, index: int) -> None:
        self._select(index)

    def _select(self, index: int) -> None:
        """Selecciona una tecla (marca el botón y carga el editor)."""
        if self.selected is not None and self.selected < len(self._key_buttons):
            self._key_buttons[self.selected].remove_css_class("sel")
        self.selected = index
        self._key_buttons[index].add_css_class("sel")
        self.editor.load(index)

    # ---------- mover / copiar / pegar / limpiar teclas ----------

    def _setup_key_editing(self) -> None:
        """Menú contextual (clic derecho) y acciones de copiar/pegar/limpiar."""
        menu = Gio.Menu()
        menu.append("Copiar", "win.key-copy")
        menu.append("Pegar", "win.key-paste")
        menu.append("Limpiar tecla", "win.key-clear")
        self._key_popover = Gtk.PopoverMenu.new_from_model(menu)

        self._key_actions = {}
        for name, cb in (("key-copy", self._copy_selected),
                         ("key-paste", self._paste_selected),
                         ("key-clear", self._clear_selected)):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", lambda a, p, cb=cb: cb())
            self.add_action(act)
            self._key_actions[name] = act

    # --- drag & drop (intercambia dos teclas) ---

    def _add_key_dnd(self, btn: Gtk.Button, index: int) -> None:
        drag = Gtk.DragSource()
        drag.set_actions(Gdk.DragAction.MOVE)
        drag.connect("prepare", self._on_drag_prepare, index)
        drag.connect("drag-begin", self._on_drag_begin, index)
        btn.add_controller(drag)

        drop = Gtk.DropTarget.new(GObject.TYPE_INT, Gdk.DragAction.MOVE)
        drop.connect("drop", self._on_drop, index)
        btn.add_controller(drop)

    def _on_drag_prepare(self, source, x, y, index):
        return Gdk.ContentProvider.new_for_value(GObject.Value(GObject.TYPE_INT, index))

    def _on_drag_begin(self, source, drag, index):
        paintable = self._key_pictures[index].get_paintable()
        if paintable is not None:                       # el icono arrastrado = la tecla
            source.set_icon(paintable, KEY_PIXELS // 2, KEY_PIXELS // 2)

    def _on_drop(self, target, value, x, y, index):
        if isinstance(value, GObject.Value):     # normalmente GTK ya entrega un int
            value = value.get_int()
        src = int(value)
        if src != index:
            self.app.controller.swap_keys(src, index)
            self._select(index)
        return True

    # --- menú contextual (clic derecho) ---

    def _add_key_contextmenu(self, btn: Gtk.Button, index: int) -> None:
        gesture = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        gesture.connect("pressed", self._on_key_right_click, index)
        btn.add_controller(gesture)

    def _on_key_right_click(self, gesture, n_press, x, y, index):
        self._select(index)
        kc = self.app.controller.page.key(index)
        self._key_actions["key-copy"].set_enabled(kc is not None)
        self._key_actions["key-clear"].set_enabled(kc is not None)
        self._key_actions["key-paste"].set_enabled(self._clipboard is not None)
        pop = self._key_popover
        if pop.get_parent() is not None:
            pop.unparent()
        pop.set_parent(self._key_buttons[index])
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        pop.set_pointing_to(rect)
        pop.popup()

    # --- atajos de teclado (activos cuando la tecla tiene el foco) ---

    def _add_key_shortcuts(self, btn: Gtk.Button, index: int) -> None:
        sc = Gtk.ShortcutController()
        for accel, cb in (("<Control>c", self._copy_key),
                          ("<Control>v", self._paste_key),
                          ("Delete", self._clear_key)):
            sc.add_shortcut(Gtk.Shortcut.new(
                Gtk.ShortcutTrigger.parse_string(accel),
                Gtk.CallbackAction.new(lambda w, a, cb=cb, i=index: (cb(i), True)[1]),
            ))
        btn.add_controller(sc)

    # --- operaciones ---

    def _copy_key(self, index: int) -> None:
        kc = self.app.controller.page.key(index)
        self._clipboard = kc.clone() if kc is not None else None
        if self._clipboard is not None:
            self.app.bus.emit("status", text=f"Tecla {index + 1} copiada")

    def _paste_key(self, index: int) -> None:
        if self._clipboard is None:
            self.app.bus.emit("status", text="No hay ninguna tecla copiada")
            return
        self.app.controller.paste_key(index, self._clipboard)
        if self.selected == index:
            self.editor.load(index)
        self.app.bus.emit("status", text=f"Pegada en la tecla {index + 1}")

    def _clear_key(self, index: int) -> None:
        self.app.controller.clear_key(index)
        if self.selected == index:
            self.editor.load(index)

    def _copy_selected(self):
        if self.selected is not None:
            self._copy_key(self.selected)

    def _paste_selected(self):
        if self.selected is not None:
            self._paste_key(self.selected)

    def _clear_selected(self):
        if self.selected is not None:
            self._clear_key(self.selected)

    def _on_page_selected(self, dropdown, _pspec) -> None:
        if self._updating_pages:
            return
        index = dropdown.get_selected()
        if index != Gtk.INVALID_LIST_POSITION and index != self.app.config.current_page:
            self.app.controller.set_page(index)

    def _on_page_changed(self) -> None:
        self._refresh_page_dropdown()
        self.editor.clear()
        if self.selected is not None:
            self._key_buttons[self.selected].remove_css_class("sel")
            self.selected = None

    def _refresh_page_dropdown(self) -> None:
        self._updating_pages = True
        names = [p.name for p in self.app.config.pages]
        self.page_dropdown.set_model(Gtk.StringList.new(names))
        self.page_dropdown.set_selected(self.app.config.current_page)
        self._updating_pages = False

    def _on_add_page(self, _btn) -> None:
        n = len(self.app.config.pages) + 1
        self.app.controller.add_page(f"Página {n}")

    def _on_brightness(self, _btn, value: float) -> None:
        self.app.config.brightness = int(value)
        self.app.config.save()
        self.app.deck.set_brightness(int(value))

    def _on_obs_settings(self, _btn) -> None:
        ObsSettingsDialog(self, self.app).present()

    # ---------- estado ----------

    def _update_status(self) -> None:
        deck = self.app.deck
        deck_txt = (
            f"Deck: conectado ({deck.key_count} teclas)"
            if deck.connected
            else "Deck: no conectado (deck virtual activo)"
        )
        obs_txt = (
            f"OBS: conectado a {self.app.obs.host}:{self.app.obs.port}"
            if self.app.obs.connected
            else "OBS: desconectado"
        )
        self.status.set_label(f"{deck_txt}   ·   {obs_txt}")
        icon = (
            "network-transmit-receive-symbolic"
            if self.app.obs.connected
            else "network-offline-symbolic"
        )
        self.obs_btn.set_icon_name(icon)

    def _flash_status(self, text: str) -> None:
        self.status.set_label(text)
        GLib.timeout_add_seconds(5, lambda: (self._update_status(), False)[1])
