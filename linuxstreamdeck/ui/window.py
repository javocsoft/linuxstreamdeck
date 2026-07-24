"""Main window: grid that mirrors the physical deck (and acts as a virtual deck
to test without hardware) + key editor + status bar."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

from .. import APP_NAME  # noqa: E402
from .about import AboutDialog  # noqa: E402
from .editor import EditorPanel  # noqa: E402
from .obs_settings import ObsSettingsDialog  # noqa: E402
from .screensaver_settings import ScreenSaverSettingsDialog  # noqa: E402

log = logging.getLogger(__name__)

GRID_COLS = 5
KEY_PIXELS = 96

_CSS = b"""
.deck-key {
    padding: 3px;
    border-radius: 10px;
    border: 2px solid transparent;   /* always reserved to prevent grid reflow */
}
.deck-key.sel { border-color: @accent_bg_color; }
.deck-key.drag-source { opacity: 0.65; }
.deck-key.drop-target {
    border-color: @accent_bg_color;
    background-color: alpha(@accent_bg_color, 0.14);
}
.statusbar { padding: 4px 10px; font-size: 0.85em; }
"""

_KEY_DRAG_PREFIX = "linuxstreamdeck-key:"


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
        self._selected_page = None
        self._unsaved_dialog: Adw.MessageDialog | None = None
        self._allow_close = False
        self._clipboard = None            # copied KeyConfig (for pasting)
        self._drag_source_index: int | None = None
        self._drag_destination_index: int | None = None

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
        self.connect("close-request", self._on_close_request)

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
        configuration_menu = Gio.Menu()
        configuration_menu.append(
            "Export configuration…", "win.config-export"
        )
        configuration_menu.append(
            "Import configuration…", "win.config-import"
        )
        profile_menu.append_section(None, configuration_menu)
        menu_btn = Gtk.MenuButton(icon_name="view-more-symbolic",
                                  tooltip_text="Manage profiles and configuration",
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

        # screen saver + brightness + OBS settings + about
        self.obs_btn = Gtk.Button.new_from_icon_name("network-offline-symbolic")
        self.obs_btn.set_tooltip_text("OBS connection settings")
        self.obs_btn.set_sensitive(self.app.obs_password_ready)
        self.obs_btn.connect("clicked", self._on_obs_settings)
        header.pack_end(self.obs_btn)
        about_btn = Gtk.Button.new_from_icon_name("help-about-symbolic")
        about_btn.set_tooltip_text("About LinuxStreamDeck")
        about_btn.connect("clicked", self._on_about)
        header.pack_end(about_btn)
        self.brightness = Gtk.ScaleButton.new(
            10, 100, 10, ["display-brightness-symbolic"]
        )
        self.brightness.set_tooltip_text("Adjust Stream Deck brightness")
        self.brightness.set_value(self.app.config.brightness)
        self.brightness.connect("value-changed", self._on_brightness)
        header.pack_end(self.brightness)
        screensaver_btn = Gtk.Button.new_from_icon_name(
            "preferences-desktop-screensaver-symbolic"
        )
        screensaver_btn.set_tooltip_text("Configure Stream Deck screen saver")
        screensaver_btn.connect("clicked", self._on_screensaver_settings)
        header.pack_end(screensaver_btn)

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
            self._add_key_contextmenu(btn, index)
            self._add_key_shortcuts(btn, index)
            grid.attach(btn, index % GRID_COLS, index // GRID_COLS, 1, 1)
            self._key_buttons.append(btn)
            self._key_pictures.append(pic)
        self._key_grid = grid
        self._add_grid_dnd(grid)
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
        bus.subscribe("ui.screensaver_frame", self._on_screensaver_frame)
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
        self.app.deck.record_activity()
        self._select(index)

    def _on_screensaver_frame(self, _topic: str, data: dict) -> None:
        for index, png in enumerate(data.get("images", ())):
            if index >= len(self._key_pictures):
                break
            texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(png))
            self._key_pictures[index].set_paintable(texture)

    def _on_about(self, _button) -> None:
        AboutDialog().present(self)

    def _select(self, index: int, on_selected=None) -> None:
        """Request a key selection, protecting unsaved editor changes."""
        if self.selected == index:
            if on_selected is not None:
                on_selected()
            return
        self._confirm_unsaved_changes(
            f"switching to Key {index + 1}",
            lambda: self._apply_selection(index, on_selected),
        )

    def _apply_selection(self, index: int, on_selected=None) -> None:
        if self.selected is not None and self.selected < len(self._key_buttons):
            self._key_buttons[self.selected].remove_css_class("sel")
        self.selected = index
        self._selected_page = self.app.controller.page
        self._key_buttons[index].add_css_class("sel")
        self.editor.load(index)
        if on_selected is not None:
            on_selected()

    def _confirm_unsaved_changes(
        self,
        destination: str,
        continue_action: Callable[[], None],
        offer_save: bool = True,
    ) -> None:
        if not self.editor.has_unsaved_changes():
            continue_action()
            return
        if self._unsaved_dialog is not None:
            self._unsaved_dialog.present()
            return

        key_number = (self.editor.index or 0) + 1
        if offer_save:
            body = (
                f"Key {key_number} has changes that have not been saved. "
                f"Save them before {destination}, or discard them."
            )
        else:
            body = (
                f"Key {key_number} has changes that have not been saved. "
                f"Continuing by {destination} will discard them."
            )
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Unsaved key changes",
            body=body,
        )
        dialog.add_response("cancel", "Keep editing")
        dialog.add_response("discard", "Discard changes")
        dialog.set_response_appearance(
            "discard",
            Adw.ResponseAppearance.DESTRUCTIVE,
        )
        if offer_save:
            dialog.add_response("save", "Save and continue")
            dialog.set_response_appearance(
                "save",
                Adw.ResponseAppearance.SUGGESTED,
            )
            dialog.set_default_response("save")
        else:
            dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect(
            "response",
            self._on_unsaved_response,
            continue_action,
            offer_save,
        )
        self._unsaved_dialog = dialog
        dialog.present()

    def _on_unsaved_response(
        self,
        _dialog,
        response: str,
        continue_action: Callable[[], None],
        offer_save: bool,
    ) -> None:
        self._unsaved_dialog = None
        if response == "save" and offer_save:
            if self.editor.save():
                continue_action()
        elif response == "discard":
            continue_action()

    def _on_close_request(self, *_args) -> bool:
        if self._allow_close or not self.editor.has_unsaved_changes():
            return False
        self._confirm_unsaved_changes(
            "closing LinuxStreamDeck",
            self._close_after_unsaved_confirmation,
        )
        return True

    def _close_after_unsaved_confirmation(self) -> None:
        self._allow_close = True
        self.close()

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
                         ("page-delete", self._delete_page),
                         ("config-export", self._export_configuration),
                         ("config-import", self._import_configuration)):
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
            if self.editor.has_unsaved_changes():
                self._refresh_profile_dropdown()
            self._confirm_unsaved_changes(
                "switching profiles",
                lambda: self.app.controller.set_profile(index),
            )

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
            on_save=lambda name, desc: self._confirm_unsaved_changes(
                "creating a new profile",
                lambda: self.app.controller.add_profile(name, desc),
            ),
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

    # --- configuration import / export ---

    def _export_configuration(self) -> None:
        chooser = Gtk.FileDialog(title="Export configuration")
        chooser.set_initial_name("linuxstreamdeck-config.lsdconfig")
        chooser.set_default_filter(self._configuration_file_filter())
        chooser.save(self, None, self._on_export_file_chosen)

    def _on_export_file_chosen(self, chooser, result) -> None:
        try:
            file = chooser.save_finish(result)
        except Exception:
            return
        path = file.get_path() if file is not None else None
        if not path:
            self._show_configuration_error(
                "Export failed", "Choose a local destination for the export."
            )
            return
        destination = Path(path)
        if destination.suffix.lower() != ".lsdconfig":
            destination = Path(f"{destination}.lsdconfig")
        try:
            exported = self.app.config.export_bundle(destination)
        except Exception as error:
            log.exception("Could not export the configuration")
            self._show_configuration_error("Export failed", str(error))
            return
        if exported.missing_icons or exported.missing_audio:
            warnings = []
            if exported.missing_icons:
                warnings.append(
                    f"{exported.missing_icons} custom icon file(s) could not "
                    "be found or included."
                )
            if exported.missing_audio:
                warnings.append(
                    f"{exported.missing_audio} audio file(s) could not be "
                    "found or included."
                )
            self._show_configuration_error(
                "Configuration exported with warnings",
                (
                    f"Saved to {destination}.\n\n"
                    + "\n".join(warnings)
                ),
            )
        else:
            self._flash_status(
                f"Configuration exported to {destination.name}"
            )

    def _import_configuration(self) -> None:
        chooser = Gtk.FileDialog(title="Import configuration")
        chooser.set_default_filter(self._configuration_file_filter())
        chooser.open(self, None, self._on_import_file_chosen)

    def _on_import_file_chosen(self, chooser, result) -> None:
        try:
            file = chooser.open_finish(result)
        except Exception:
            return
        path = file.get_path() if file is not None else None
        if not path:
            self._show_configuration_error(
                "Import failed", "Choose a local LinuxStreamDeck export."
            )
            return
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Replace current configuration?",
            body=(
                f"Import {Path(path).name} and replace all current profiles, "
                "pages, keys and settings?\n\nThe current configuration will "
                "be saved as config.json.bak."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("import", "Import")
        dialog.set_response_appearance(
            "import", Adw.ResponseAppearance.DESTRUCTIVE
        )
        dialog.connect(
            "response", self._on_import_confirmation_response, Path(path)
        )
        dialog.present()

    def _on_import_confirmation_response(
        self, _dialog, response: str, source: Path
    ) -> None:
        if response != "import":
            return
        self._confirm_unsaved_changes(
            "importing a configuration",
            lambda: self._apply_configuration_import(source),
        )

    def _apply_configuration_import(self, source: Path) -> None:
        try:
            imported = self.app.controller.import_configuration(source)
        except Exception as error:
            log.exception("Could not import the configuration")
            self._show_configuration_error("Import failed", str(error))
            return
        self.brightness.set_value(self.app.config.brightness)
        text = (
            f"Imported {imported.profiles} profile(s), {imported.pages} "
            f"page(s) and {imported.keys} configured key(s)"
        )
        restored = []
        if imported.restored_icons:
            restored.append(f"{imported.restored_icons} custom icon(s)")
        if imported.restored_audio:
            restored.append(f"{imported.restored_audio} audio file(s)")
        if restored:
            text += f"; restored {', '.join(restored)}"
        GLib.idle_add(lambda: (self._flash_status(text), False)[1])

    @staticmethod
    def _configuration_file_filter() -> Gtk.FileFilter:
        file_filter = Gtk.FileFilter()
        file_filter.set_name("LinuxStreamDeck configuration")
        file_filter.add_pattern("*.lsdconfig")
        return file_filter

    def _show_configuration_error(self, heading: str, body: str) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=heading,
            body=body,
        )
        dialog.add_response("close", "Close")
        dialog.present()

    # --- pages ---

    def _new_page(self) -> None:
        default = f"Page {len(self.app.config.pages) + 1}"
        self._page_name_dialog(
            "New page", default,
            on_save=lambda name: self._confirm_unsaved_changes(
                "creating a new page",
                lambda: self.app.controller.add_page(name),
            ),
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

    def _add_grid_dnd(self, grid: Gtk.Grid) -> None:
        """Use one grid-level DnD pair so child buttons cannot steal gestures."""
        drag = Gtk.DragSource()
        drag.set_actions(Gdk.DragAction.MOVE)
        drag.set_button(Gdk.BUTTON_PRIMARY)
        drag.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        drag.connect("prepare", self._on_drag_prepare)
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-end", self._on_drag_end)
        grid.add_controller(drag)

        drop = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        drop.set_preload(True)
        drop.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        drop.connect("enter", self._on_drag_motion)
        drop.connect("motion", self._on_drag_motion)
        drop.connect("leave", self._on_drag_leave)
        drop.connect("drop", self._on_drop)
        grid.add_controller(drop)

    def _key_at_grid_point(self, x: float, y: float) -> int | None:
        widget = self._key_grid.pick(x, y, Gtk.PickFlags.DEFAULT)
        while widget is not None and widget is not self._key_grid:
            try:
                return self._key_buttons.index(widget)
            except ValueError:
                widget = widget.get_parent()
        return None

    def _on_drag_prepare(self, source, x, y):
        index = self._key_at_grid_point(x, y)
        if index is None or self.app.controller.page.key(index) is None:
            self._drag_source_index = None
            return None
        self._drag_source_index = index
        payload = GObject.Value(
            GObject.TYPE_STRING,
            f"{_KEY_DRAG_PREFIX}{index}",
        )
        return Gdk.ContentProvider.new_for_value(payload)

    def _on_drag_begin(self, source, drag):
        index = self._drag_source_index
        if index is None:
            return
        self._key_buttons[index].add_css_class("drag-source")
        paintable = self._key_pictures[index].get_paintable()
        if paintable is not None:                       # the drag icon = the key
            source.set_icon(paintable, KEY_PIXELS // 2, KEY_PIXELS // 2)

    def _on_drag_end(self, source, drag, delete_data) -> None:
        self._clear_drag_feedback()

    def _on_drag_motion(self, target, x, y):
        destination = self._key_at_grid_point(x, y)
        if destination == self._drag_source_index:
            destination = None
        self._set_drag_destination(destination)
        if self._drag_source_index is None or destination is None:
            return Gdk.DragAction(0)
        return Gdk.DragAction.MOVE

    def _on_drag_leave(self, target) -> None:
        self._set_drag_destination(None)

    def _on_drop(self, target, value, x, y):
        if isinstance(value, GObject.Value):
            value = value.get_string()
        source = self._decode_key_drag(value)
        destination = self._key_at_grid_point(x, y)
        if (
            source is None
            or source != self._drag_source_index
            or destination is None
            or source == destination
        ):
            log.debug(
                "Rejected key drop: source=%r active=%r destination=%r",
                source,
                self._drag_source_index,
                destination,
            )
            self._set_drag_destination(None)
            return False
        self._confirm_unsaved_changes(
            "moving these keys",
            lambda: self._apply_key_drop(source, destination),
        )
        self._set_drag_destination(None)
        return True

    @staticmethod
    def _decode_key_drag(value) -> int | None:
        if not isinstance(value, str) or not value.startswith(_KEY_DRAG_PREFIX):
            return None
        try:
            return int(value.removeprefix(_KEY_DRAG_PREFIX))
        except ValueError:
            return None

    def _set_drag_destination(self, index: int | None) -> None:
        previous = self._drag_destination_index
        if previous == index:
            return
        if previous is not None and previous < len(self._key_buttons):
            self._key_buttons[previous].remove_css_class("drop-target")
        self._drag_destination_index = index
        if index is not None:
            self._key_buttons[index].add_css_class("drop-target")

    def _clear_drag_feedback(self) -> None:
        source = self._drag_source_index
        if source is not None and source < len(self._key_buttons):
            self._key_buttons[source].remove_css_class("drag-source")
        self._drag_source_index = None
        self._set_drag_destination(None)

    def _apply_key_drop(self, source: int, destination: int) -> None:
        self.app.controller.swap_keys(source, destination)
        self._apply_selection(destination)

    # --- context menu (right-click) ---

    def _add_key_contextmenu(self, btn: Gtk.Button, index: int) -> None:
        gesture = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        gesture.connect("pressed", self._on_key_right_click, index)
        btn.add_controller(gesture)

    def _on_key_right_click(self, gesture, n_press, x, y, index):
        self._select(
            index,
            lambda: self._show_key_context_menu(index, x, y),
        )

    def _show_key_context_menu(self, index: int, x: float, y: float) -> None:
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

        def paste() -> None:
            self.app.controller.paste_key(index, self._clipboard)
            if self.selected == index:
                self.editor.load(index)
            self.app.bus.emit("status", text=f"Pasted into key {index + 1}")

        if self.selected == index:
            self._confirm_unsaved_changes(
                f"replacing Key {index + 1} with the copied key",
                paste,
                offer_save=False,
            )
        else:
            paste()

    def _clear_key(self, index: int) -> None:
        def clear() -> None:
            self.app.controller.clear_key(index)
            if self.selected == index:
                self.editor.load(index)

        if self.selected == index:
            self._confirm_unsaved_changes(
                f"clearing Key {index + 1}",
                clear,
                offer_save=False,
            )
        else:
            clear()

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
            if self.editor.has_unsaved_changes():
                self._refresh_page_dropdown()
            self._confirm_unsaved_changes(
                "switching pages",
                lambda: self.app.controller.set_page(index),
            )

    def _on_page_changed(self) -> None:
        self._refresh_page_dropdown()
        if self._selected_page is self.app.controller.page:
            return
        self._clear_selection()

    def _clear_selection(self) -> None:
        self.editor.clear()
        if self.selected is not None:
            self._key_buttons[self.selected].remove_css_class("sel")
            self.selected = None
        self._selected_page = None

    def _refresh_page_dropdown(self) -> None:
        self._updating_pages = True
        names = [p.name for p in self.app.config.pages]
        self.page_dropdown.set_model(Gtk.StringList.new(names))
        self.page_dropdown.set_selected(self.app.config.current_page)
        self._updating_pages = False

    def _on_brightness(self, _btn, value: float) -> None:
        brightness = int(value)
        if self.app.config.brightness == brightness:
            return
        self.app.config.brightness = brightness
        self.app.config.save()
        self.app.deck.set_brightness(brightness)

    def _on_obs_settings(self, _btn) -> None:
        ObsSettingsDialog(self, self.app).present()

    def _on_screensaver_settings(self, _btn) -> None:
        self.app.deck.record_activity()
        ScreenSaverSettingsDialog(self, self.app).present()

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
