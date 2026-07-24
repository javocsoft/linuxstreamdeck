"""Main window: grid that mirrors the physical deck (and acts as a virtual deck
to test without hardware) + key editor + status bar."""

from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

from .. import APP_NAME  # noqa: E402
from .about import AboutDialog  # noqa: E402
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
        self._updating_profiles = False
        self._clipboard = None            # copied KeyConfig (for pasting)

        css = Gtk.CssProvider()
        css.load_from_data(_CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self._setup_key_editing()
        self._build_ui()
        self._connect_bus()
        self._refresh_profile_dropdown()
        self._refresh_page_dropdown()

    # ---------- construction ----------

    def _build_ui(self) -> None:
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()

        # PROFILE selector + profiles menu
        self.profile_dropdown = Gtk.DropDown.new_from_strings([])
        self.profile_dropdown.set_tooltip_text("Active profile")
        self.profile_dropdown.connect("notify::selected", self._on_profile_selected)
        header.pack_start(self.profile_dropdown)
        profile_menu = Gio.Menu()
        profile_menu.append("New profile…", "win.profile-new")
        profile_menu.append("Edit profile…", "win.profile-edit")
        profile_menu.append("Delete profile", "win.profile-delete")
        menu_btn = Gtk.MenuButton(icon_name="view-more-symbolic",
                                  tooltip_text="Manage profiles",
                                  menu_model=profile_menu)
        header.pack_start(menu_btn)

        header.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        # page selector + pages menu (of the active profile), mirroring profiles
        self.page_dropdown = Gtk.DropDown.new_from_strings([])
        self.page_dropdown.set_tooltip_text("Active page")
        self.page_dropdown.connect("notify::selected", self._on_page_selected)
        header.pack_start(self.page_dropdown)
        page_menu = Gio.Menu()
        page_menu.append("New page…", "win.page-new")
        page_menu.append("Rename page…", "win.page-rename")
        page_menu.append("Delete page", "win.page-delete")
        page_menu_btn = Gtk.MenuButton(icon_name="view-more-symbolic",
                                       tooltip_text="Manage pages",
                                       menu_model=page_menu)
        header.pack_start(page_menu_btn)

        # brightness + OBS settings + about
        self.obs_btn = Gtk.Button.new_from_icon_name("network-offline-symbolic")
        self.obs_btn.set_tooltip_text("OBS connection settings")
        self.obs_btn.connect("clicked", self._on_obs_settings)
        header.pack_end(self.obs_btn)
        about_btn = Gtk.Button.new_from_icon_name("help-about-symbolic")
        about_btn.set_tooltip_text("About LinuxStreamDeck")
        about_btn.connect("clicked", self._on_about)
        header.pack_end(about_btn)
        brightness = Gtk.ScaleButton.new(
            10, 100, 10, ["display-brightness-symbolic"]
        )
        brightness.set_tooltip_text("Adjust Stream Deck brightness")
        brightness.set_value(self.app.config.brightness)
        brightness.connect("value-changed", self._on_brightness)
        header.pack_end(brightness)

        view.add_top_bar(header)

        # content: grid + editor
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
            label="Drag to move · right-click to copy/paste · "
                  "“Test” runs the action"
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

        # status bar
        self.status = Gtk.Label(label="", xalign=0)
        self.status.add_css_class("statusbar")
        view.add_bottom_bar(self.status)

        self.set_content(view)
        self._update_status()

    def _connect_bus(self) -> None:
        bus = self.app.bus
        bus.subscribe("ui.key_image", self._on_key_image)
        bus.subscribe("profile.changed", self._on_profile_changed)
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

    def _on_about(self, _button) -> None:
        AboutDialog().present(self)

    def _select(self, index: int) -> None:
        """Select a key (marks the button and loads the editor)."""
        if self.selected is not None and self.selected < len(self._key_buttons):
            self._key_buttons[self.selected].remove_css_class("sel")
        self.selected = index
        self._key_buttons[index].add_css_class("sel")
        self.editor.load(index)

    # ---------- move / copy / paste / clear keys ----------

    def _setup_key_editing(self) -> None:
        """Context menu (right-click) and copy/paste/clear actions."""
        menu = Gio.Menu()
        menu.append("Copy", "win.key-copy")
        menu.append("Paste", "win.key-paste")
        menu.append("Clear key", "win.key-clear")
        self._key_popover = Gtk.PopoverMenu.new_from_model(menu)

        self._key_actions = {}
        for name, cb in (("key-copy", self._copy_selected),
                         ("key-paste", self._paste_selected),
                         ("key-clear", self._clear_selected),
                         ("profile-new", self._new_profile),
                         ("profile-edit", self._edit_profile),
                         ("profile-delete", self._delete_profile),
                         ("page-new", self._new_page),
                         ("page-rename", self._rename_page),
                         ("page-delete", self._delete_page)):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", lambda a, p, cb=cb: cb())
            self.add_action(act)
            self._key_actions[name] = act

    # ---------- profiles ----------

    def _refresh_profile_dropdown(self) -> None:
        self._updating_profiles = True
        profiles = self.app.config.profiles
        self.profile_dropdown.set_model(Gtk.StringList.new([p.name for p in profiles]))
        self.profile_dropdown.set_selected(self.app.config.current_profile)
        desc = self.app.config.profile.description
        self.profile_dropdown.set_tooltip_text(desc or "Active profile")
        self._updating_profiles = False

    def _on_profile_selected(self, dropdown, _pspec) -> None:
        if self._updating_profiles:
            return
        index = dropdown.get_selected()
        if index != Gtk.INVALID_LIST_POSITION and index != self.app.config.current_profile:
            self.app.controller.set_profile(index)

    def _on_profile_changed(self, topic: str, data: dict) -> None:
        self._refresh_profile_dropdown()
        desc = data.get("description", "")
        text = f"Profile: {data.get('name', '')}"
        if desc:
            text += f" — {desc}"
        self._flash_status(text)

    def _new_profile(self) -> None:
        from .profile_dialog import ProfileDialog
        ProfileDialog(
            self, "New profile", "", "",
            on_save=lambda name, desc: self.app.controller.add_profile(name, desc),
        ).present()

    def _edit_profile(self) -> None:
        from .profile_dialog import ProfileDialog
        prof = self.app.config.profile
        ProfileDialog(
            self, "Edit profile", prof.name, prof.description,
            on_save=lambda name, desc: self.app.controller.update_profile(name, desc),
        ).present()

    def _delete_profile(self) -> None:
        if len(self.app.config.profiles) <= 1:
            self._flash_status("You can't delete the only profile")
            return
        prof = self.app.config.profile
        dialog = Adw.MessageDialog(
            transient_for=self, heading="Delete profile",
            body=f"Delete the profile “{prof.name}” and all its pages and keys?",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_delete_response)
        dialog.present()

    def _on_delete_response(self, dialog, response) -> None:
        if response == "delete":
            self.app.controller.delete_profile(self.app.config.current_profile)

    # --- pages ---

    def _new_page(self) -> None:
        default = f"Page {len(self.app.config.pages) + 1}"
        self._page_name_dialog(
            "New page", default,
            on_save=lambda name: self.app.controller.add_page(name),
        )

    def _rename_page(self) -> None:
        self._page_name_dialog(
            "Rename page", self.app.controller.page.name,
            on_save=lambda name: self.app.controller.rename_page(name),
        )

    def _page_name_dialog(self, title: str, initial: str, on_save) -> None:
        """Small dialog asking for a page name (shared by new/rename)."""
        dialog = Adw.Window(transient_for=self, modal=True, title=title,
                            default_width=440, default_height=240)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        pg = Adw.PreferencesPage()
        group = Adw.PreferencesGroup()
        entry = Adw.EntryRow(title="Page name", text=initial)
        group.add(entry)
        pg.add(group)
        actions = Adw.PreferencesGroup()
        save = Gtk.Button(label="Save", margin_top=6)
        save.add_css_class("suggested-action")

        def do_save(_b):
            name = entry.get_text().strip()
            if name:
                on_save(name)
            dialog.close()

        save.connect("clicked", do_save)
        entry.connect("entry-activated", do_save)
        actions.add(save)
        pg.add(actions)
        view.set_content(pg)
        dialog.set_content(view)
        entry.grab_focus()
        dialog.present()

    def _delete_page(self) -> None:
        if len(self.app.config.pages) <= 1:
            self._flash_status("You can't delete the only page")
            return
        page = self.app.controller.page
        dialog = Adw.MessageDialog(
            transient_for=self, heading="Delete page",
            body=f"Delete the page “{page.name}” and all its keys?",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_delete_page_response)
        dialog.present()

    def _on_delete_page_response(self, dialog, response) -> None:
        if response == "delete":
            self.app.controller.delete_page(self.app.config.current_page)

    # --- drag & drop (swaps two keys) ---

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
        if paintable is not None:                       # the drag icon = the key
            source.set_icon(paintable, KEY_PIXELS // 2, KEY_PIXELS // 2)

    def _on_drop(self, target, value, x, y, index):
        if isinstance(value, GObject.Value):     # GTK normally already delivers an int
            value = value.get_int()
        src = int(value)
        if src != index:
            self.app.controller.swap_keys(src, index)
            self._select(index)
        return True

    # --- context menu (right-click) ---

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

    # --- keyboard shortcuts (active when the key has focus) ---

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

    # --- operations ---

    def _copy_key(self, index: int) -> None:
        kc = self.app.controller.page.key(index)
        self._clipboard = kc.clone() if kc is not None else None
        if self._clipboard is not None:
            self.app.bus.emit("status", text=f"Key {index + 1} copied")

    def _paste_key(self, index: int) -> None:
        if self._clipboard is None:
            self.app.bus.emit("status", text="No key copied")
            return
        self.app.controller.paste_key(index, self._clipboard)
        if self.selected == index:
            self.editor.load(index)
        self.app.bus.emit("status", text=f"Pasted into key {index + 1}")

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

    def _on_brightness(self, _btn, value: float) -> None:
        self.app.config.brightness = int(value)
        self.app.config.save()
        self.app.deck.set_brightness(int(value))

    def _on_obs_settings(self, _btn) -> None:
        ObsSettingsDialog(self, self.app).present()

    # ---------- state ----------

    def _update_status(self) -> None:
        deck = self.app.deck
        deck_txt = (
            f"Deck: connected ({deck.key_count} keys)"
            if deck.connected
            else "Deck: not connected (virtual deck active)"
        )
        obs_txt = (
            f"OBS: connected to {self.app.obs.host}:{self.app.obs.port}"
            if self.app.obs.connected
            else "OBS: disconnected"
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
