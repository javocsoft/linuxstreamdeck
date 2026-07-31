"""Main window: grid that mirrors the physical deck (and acts as a virtual deck
to test without hardware) + key editor + status bar."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

from .. import APP_NAME  # noqa: E402
from ..core import actions as action_registry  # noqa: E402
from ..core.config import KIND_SINGLE, KeyConfig  # noqa: E402
from .about import AboutDialog  # noqa: E402
from .dials import DialRow  # noqa: E402
from .editor import EditorPanel  # noqa: E402
from .obs_settings import ObsSettingsDialog  # noqa: E402
from .screensaver_settings import ScreenSaverSettingsDialog  # noqa: E402
from .steps import STEP_CLIPBOARD  # noqa: E402

log = logging.getLogger(__name__)

GRID_COLS = 5
KEY_PIXELS = 96

# How long a second click on the same key still counts as a double click.
DOUBLE_CLICK_SECONDS = 0.4

# Where the hint under the grid wraps. Kept well inside the key grid's own
# width (5 columns of KEY_PIXELS plus spacing = 520 px) rather than right at it,
# so a larger desktop font still cannot push it past the keys.
HINT_MAX_CHARS = 58

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
.deck-key.reserved { opacity: 0.9; }
/* Keyboard focus has to stay distinguishable from selection: arrows move the
   focus, and only activating the key selects it. */
.deck-key:focus-visible {
    outline: 2px dashed @accent_bg_color;
    outline-offset: -5px;
}
.statusbar { padding: 4px 10px; font-size: 0.85em; }
.breadcrumb { padding: 2px 0; }
/* Steps of a multi-action key (ui/steps.py): reordering and the remove button. */
.step-row.drag-source { opacity: 0.55; }
.step-row.drop-target {
    background-color: alpha(@accent_bg_color, 0.14);
    border-radius: 6px;
}
/* The row a right-click menu is acting on, while that menu is open. */
.step-row.menu-target {
    background-color: alpha(@accent_bg_color, 0.22);
    border-radius: 6px;
}
.step-remove { color: @error_color; }
.step-remove:hover { background-color: alpha(@error_color, 0.15); }
/* The editor's "connect a service" banner (ui/editor.py). Adw.Banner gives its
   button the same muted tone as the banner itself, which is nearly invisible
   against it; this is the one thing on that strip meant to be pressed, so it
   gets the accent it would have had as a suggested action. The button is
   built by Adw.Banner and never exposed, hence the descendant selector. */
.service-banner button {
    background-color: @accent_bg_color;
    color: @accent_fg_color;
    font-weight: bold;
}
.service-banner button:hover { background-color: shade(@accent_bg_color, 1.15); }
/* Live suggestions under a text field (ui/steps.py). Kept narrow enough to
   read as a list attached to the entry rather than a floating window. */
.completion-popup > contents { padding: 2px; }
.completion-list > row { padding: 4px 8px; }
.completion-list > row:hover { background-color: alpha(@accent_bg_color, 0.18); }
/* The box art keeps its slot whether or not the picture ever arrives, so a
   late or missing one cannot make the rows jump as the list fills. */
.completion-art {
    border-radius: 3px;
    background-color: alpha(currentColor, 0.10);
}
/* Text in a suggestion field that the service does not recognise. Saving it
   would store nothing, so this says as much before the key is saved rather
   than leaving it to be discovered on air. */
entry.unsettled { box-shadow: inset 0 0 0 1px @warning_color; }
entry.unsettled text { color: @warning_color; }
/* Right-click copy/paste menu of an action list (ui/steps.py). */
.step-menu button { padding: 4px 12px; min-height: 26px; }
/* Connection dot in the header. The state used to live only in the status bar
   text, which has to be read; a colour is seen without looking for it. */
.conn-dot {
    min-width: 10px;
    min-height: 10px;
    border-radius: 999px;
    margin: 0 2px;
    background-color: @error_color;
}
.conn-dot.online { background-color: @success_color; }
"""

_KEY_DRAG_PREFIX = "linuxstreamdeck-key:"

# The line under the key grid. It lives here rather than inline so the width
# regression test measures the real text instead of a copy that drifts from it.
GRID_HINT = (
    "Drag to move · right-click to copy/paste · "
    "double-click a folder to open it · "
    "arrow keys move between keys · "
    "Ctrl+Z undoes, Ctrl+Shift+Z redoes"
)

# Both the arrow keys and their keypad twins, so navigation works either way.
_ARROW_DIRECTIONS = {
    Gdk.KEY_Left: "left",
    Gdk.KEY_KP_Left: "left",
    Gdk.KEY_Right: "right",
    Gdk.KEY_KP_Right: "right",
    Gdk.KEY_Up: "up",
    Gdk.KEY_KP_Up: "up",
    Gdk.KEY_Down: "down",
    Gdk.KEY_KP_Down: "down",
}


def action_name(action_id: str) -> str:
    """What an action is called, falling back to its id if it is gone."""
    action = action_registry.get(action_id)
    return action.name if action is not None else action_id


def neighbour_index(
    index: int, direction: str, columns: int, count: int
) -> int | None:
    """The key an arrow moves focus to, or None when there is nowhere to go.

    Left and right walk the deck's own numbering, so the end of a row leads
    into the start of the next one: the grid is one sequence of keys drawn in
    rows, not a set of independent rows. Up and down simply stop at the edges;
    wrapping them would throw the focus to the far end of the deck.
    """
    if count <= 0 or columns <= 0 or not 0 <= index < count:
        return None
    step = {"left": -1, "right": 1, "up": -columns, "down": columns}.get(direction)
    if step is None:
        return None
    target = index + step
    return target if 0 <= target < count else None


def _completes_double_click(
    previous: tuple[int, float] | None, index: int, now: float
) -> bool:
    """Whether a click on `index` at `now` closes a double click on that key."""
    if previous is None:
        return False
    last_index, last_time = previous
    return last_index == index and now - last_time <= DOUBLE_CLICK_SECONDS


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
        self._selected_container = None
        self._unsaved_dialog: Adw.MessageDialog | None = None
        self._allow_close = False
        self._clipboard = None            # copied KeyConfig (for pasting)
        self._drag_source_index: int | None = None
        self._drag_destination_index: int | None = None
        self._last_key_click: tuple[int, float] | None = None
        # Columns the on-screen grid is currently laid out in; see
        # _build_key_grid. GRID_COLS is only the pre-connection default.
        self._grid_columns = GRID_COLS

        css = Gtk.CssProvider()
        css.load_from_data(_CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self._setup_key_editing()
        self._build_ui()
        self._add_window_shortcuts()
        self._connect_bus()
        self._refresh_profile_dropdown()
        self._refresh_page_dropdown()
        self._refresh_breadcrumb()
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
        profile_menu.append("Duplicate profile", "win.profile-duplicate")
        profile_menu.append("Delete profile", "win.profile-delete")
        layout_menu = Gio.Menu()
        layout_menu.append("Export layout sheet…", "win.profile-sheet")
        layout_menu.append("Check keys against OBS…", "win.check-references")
        profile_menu.append_section(None, layout_menu)
        configuration_menu = Gio.Menu()
        configuration_menu.append(
            "Export configuration…", "win.config-export"
        )
        configuration_menu.append(
            "Import configuration…", "win.config-import"
        )
        configuration_menu.append(
            "Restore a backup…", "win.config-backups"
        )
        profile_menu.append_section(None, configuration_menu)
        find_menu = Gio.Menu()
        find_menu.append("Find a key…", "win.key-find")
        profile_menu.append_section(None, find_menu)
        service_menu = Gio.Menu()
        service_menu.append("Twitch account…", "win.twitch-account")
        profile_menu.append_section(None, service_menu)
        application_menu = Gio.Menu()
        application_menu.append("Preferences…", "win.app-preferences")
        # The log is the only place an action failure survives once its status
        # message has passed, so it needs a way in that is not a file manager.
        application_menu.append("Open log file", "win.app-log")
        profile_menu.append_section(None, application_menu)
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
        page_menu.append("Duplicate page", "win.page-duplicate")
        page_menu.append("Delete page", "win.page-delete")
        order_menu = Gio.Menu()
        order_menu.append("Move page up", "win.page-move-up")
        order_menu.append("Move page down", "win.page-move-down")
        page_menu.append_section(None, order_menu)
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
        # Packed after the button, so it ends up to its left: the dot reads as
        # belonging to the OBS control rather than to the brightness one.
        self.obs_dot = Gtk.Box(valign=Gtk.Align.CENTER)
        self.obs_dot.add_css_class("conn-dot")
        header.pack_end(self.obs_dot)
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
        screensaver_btn.set_tooltip_text("Configure Stream Deck display")
        screensaver_btn.connect("clicked", self._on_screensaver_settings)
        header.pack_end(screensaver_btn)

        view.add_top_bar(header)

        # content: grid + editor
        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)

        grid_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER, hexpand=True,
        )
        # Where the grid currently is: the page, then each open folder.
        self._breadcrumb = Gtk.Box(spacing=4, halign=Gtk.Align.CENTER)
        self._breadcrumb.add_css_class("breadcrumb")
        self._breadcrumb.set_margin_bottom(10)
        grid_box.append(self._breadcrumb)
        self._key_grid = Gtk.Grid(row_spacing=10, column_spacing=10)
        # The drag and key controllers live on the grid itself, so they survive
        # the rebuild that a deck of another shape triggers.
        self._add_grid_dnd(self._key_grid)
        self._add_grid_key_navigation(self._key_grid)
        self._build_key_grid()
        grid_box.append(self._key_grid)
        # Wrapped and width-capped: the grid box is centered, so it takes the
        # width of its widest child. An unwrapped hint therefore made the box
        # wider than the window and ran off the edge.
        hint = Gtk.Label(
            label=GRID_HINT,
            wrap=True,
            justify=Gtk.Justification.CENTER,
            max_width_chars=HINT_MAX_CHARS,
        )
        hint.add_css_class("dim-label")
        hint.set_margin_top(12)
        grid_box.append(hint)
        # Encoders of a Stream Deck +. The row hides itself on every other
        # model, so nothing about it is reachable without the hardware.
        self.dial_row = DialRow(self)
        grid_box.append(self.dial_row)
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

    def _build_key_grid(self) -> None:
        """Fill the grid with one button per key, in the deck's own columns.

        Called again when a deck of another shape connects: the window is built
        before anything is plugged in, so it starts on the MK.2 defaults and an
        XL or a Mini has to reshape it.
        """
        grid = self._key_grid
        while (child := grid.get_first_child()) is not None:
            grid.remove(child)
        self._key_buttons.clear()
        self._key_pictures.clear()
        self._grid_columns = max(1, int(self.app.deck.columns))
        for index in range(max(1, int(self.app.deck.key_count))):
            btn = Gtk.Button()
            btn.add_css_class("deck-key")
            pic = Gtk.Picture()
            pic.set_size_request(KEY_PIXELS, KEY_PIXELS)
            btn.set_child(pic)
            btn.connect("clicked", self._on_key_clicked, index)
            self._add_key_contextmenu(btn, index)
            self._add_key_shortcuts(btn, index)
            grid.attach(
                btn,
                index % self._grid_columns,
                index // self._grid_columns,
                1,
                1,
            )
            self._key_buttons.append(btn)
            self._key_pictures.append(pic)

    def _on_deck_connected(self, _topic: str, data: dict) -> None:
        """Reshape the on-screen grid if the deck that arrived is a different one."""
        deck = self.app.deck
        shape = (int(deck.key_count), max(1, int(deck.columns)))
        if shape != (len(self._key_buttons), self._grid_columns):
            # Every index the editor and the selection hold refers to the old
            # grid, so both are dropped before the buttons are replaced.
            self._clear_selection()
            self._build_key_grid()
            self._refresh_reserved_key()
            self.app.controller.refresh()
        # A Plus brings encoders with it and every other model takes them away.
        self.dial_row.rebuild()
        self._update_status()

    def _connect_bus(self) -> None:
        bus = self.app.bus
        bus.subscribe("ui.key_image", self._on_key_image)
        bus.subscribe("ui.screensaver_frame", self._on_screensaver_frame)
        bus.subscribe("profile.changed", self._on_profile_changed)
        bus.subscribe("page.changed", lambda t, d: self._on_page_changed())
        bus.subscribe("folder.changed", lambda t, d: self._on_folder_changed())
        bus.subscribe("deck.connected", self._on_deck_connected)
        for topic in ("deck.disconnected",
                      "obs.connected", "obs.disconnected"):
            bus.subscribe(topic, lambda t, d: self._update_status())
        bus.subscribe("status", lambda t, d: self._flash_status(d.get("text", "")))
        bus.subscribe("preflight.report", self._on_preflight_report)

    # ---------- callbacks ----------

    def _on_key_image(self, topic: str, data: dict) -> None:
        index, png = data["index"], data["png"]
        if index < len(self._key_pictures):
            texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(png))
            self._key_pictures[index].set_paintable(texture)

    def _on_key_clicked(self, btn, index: int) -> None:
        """Single click selects the key; a second one opens it if it is a folder.

        The double click is timed from "clicked" rather than detected with an
        extra Gtk.GestureClick: a GtkButton claims the primary-button sequence
        on press, which cancels any other primary gesture on the same widget, so
        that gesture never reached n_press == 2. The secondary-button context
        menu is unaffected, because GtkButton never claims that one.
        """
        self.app.deck.record_activity()
        if self.app.controller.is_reserved_key(index):
            # The Back key is navigation, not a configurable key.
            self._last_key_click = None
            self._leave_folder()
            return
        now = time.monotonic()
        double = _completes_double_click(self._last_key_click, index, now)
        # Consumed on a double click, so a third one starts a new pair instead
        # of opening the folder that was just entered.
        self._last_key_click = None if double else (index, now)
        if double:
            self.open_folder(index)
            return
        self._select(index)

    # ---------- folders ----------

    def open_folder(self, index: int) -> None:
        """Enter the folder held by a key, protecting unsaved editor changes."""
        kc = self.app.controller.container.key(index)
        if kc is None or kc.contents is None:
            # A folder just chosen in the editor has no grid until it is saved,
            # so offer that instead of silently doing nothing.
            if self.selected == index and self.editor.has_unsaved_changes():
                self._confirm_unsaved_changes(
                    f"opening Key {index + 1}",
                    lambda: self._enter_folder(index),
                )
            return
        self._confirm_unsaved_changes(
            f"opening “{kc.folder_name()}”",
            lambda: self._enter_folder(index),
        )

    def _enter_folder(self, index: int) -> None:
        if not self.app.controller.open_folder(index):
            self._flash_status("This key does not hold a folder")

    def _leave_folder(self) -> None:
        self._confirm_unsaved_changes(
            "leaving this folder",
            self.app.controller.close_folder,
        )

    def _go_to_folder(self, path: tuple[int, ...]) -> None:
        self._confirm_unsaved_changes(
            "moving to another folder",
            lambda: self.app.controller.set_folder_path(path),
        )

    def _on_folder_changed(self) -> None:
        self._clear_selection()
        self._refresh_breadcrumb()

    def _refresh_breadcrumb(self) -> None:
        """Rebuild the path shown above the grid: page ▸ folder ▸ folder."""
        while (child := self._breadcrumb.get_first_child()) is not None:
            self._breadcrumb.remove(child)
        controller = self.app.controller
        trail = controller.folder_trail()
        root = Gtk.Button(label=controller.page.name)
        root.add_css_class("flat")
        root.set_sensitive(bool(trail))
        root.connect("clicked", lambda _b: self._go_to_folder(()))
        self._breadcrumb.append(root)
        for position, (path, name) in enumerate(trail):
            self._breadcrumb.append(Gtk.Label(label="▸"))
            button = Gtk.Button(label=name)
            button.add_css_class("flat")
            last = position == len(trail) - 1
            button.set_sensitive(not last)
            if last:
                button.add_css_class("heading")
            button.connect(
                "clicked", lambda _b, p=path: self._go_to_folder(p)
            )
            self._breadcrumb.append(button)
        self._refresh_reserved_key()

    def _refresh_reserved_key(self) -> None:
        """Mark the Back slot so it does not look like a configurable key."""
        controller = self.app.controller
        for index, button in enumerate(self._key_buttons):
            reserved = controller.is_reserved_key(index)
            if reserved:
                button.add_css_class("reserved")
                button.set_tooltip_text("Leave this folder")
            else:
                button.remove_css_class("reserved")
                button.set_tooltip_text(None)

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
        if self.app.controller.is_reserved_key(index):
            return
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
        self._selected_container = self.app.controller.container
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
        # Hiding to the status area keeps the whole session, including an
        # unfinished key edit, so it deliberately asks nothing.
        if not self._allow_close and self.app.hides_on_close():
            self.set_visible(False)
            self.app.bus.emit(
                "status", text="Still running in the status area"
            )
            return True
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

    # ---------- entry points used by the status icon ----------

    def request_quit(self) -> None:
        """Quit for good, confirming unsaved key changes first."""
        self._present_for_dialog()
        self._confirm_unsaved_changes(
            "quitting LinuxStreamDeck",
            self.app.quit,
        )

    def request_profile(self, index: int) -> None:
        """Switch profile from outside the window, honouring unsaved changes."""
        if index == self.app.config.current_profile:
            return
        self._present_for_dialog()
        self._confirm_unsaved_changes(
            "switching profiles",
            lambda: self.app.controller.set_profile(index),
        )

    def _present_for_dialog(self) -> None:
        """A confirmation is modal to this window, so it must be on screen."""
        if self.editor.has_unsaved_changes() and not self.get_visible():
            self.set_visible(True)
            self.present()

    # ---------- move / copy / paste / clear keys ----------

    def _setup_key_editing(self) -> None:
        """Context menu (right-click) and copy/paste/clear actions."""
        menu = Gio.Menu()
        menu.append("Open folder", "win.key-open")
        menu.append("Copy", "win.key-copy")
        menu.append("Paste", "win.key-paste")
        menu.append("Paste action", "win.key-paste-action")
        menu.append("Clear key", "win.key-clear")
        undo_menu = Gio.Menu()
        undo_menu.append("Undo last change", "win.key-undo")
        undo_menu.append("Redo last change", "win.key-redo")
        menu.append_section(None, undo_menu)
        portable_menu = Gio.Menu()
        portable_menu.append("Export key…", "win.key-export")
        portable_menu.append("Import key…", "win.key-import")
        menu.append_section(None, portable_menu)
        self._key_popover = Gtk.PopoverMenu.new_from_model(menu)

        self._key_actions = {}
        for name, cb in (("key-open", self._open_selected_folder),
                         ("key-copy", self._copy_selected),
                         ("key-paste", self._paste_selected),
                         ("key-paste-action", self._paste_action_selected),
                         ("key-clear", self._clear_selected),
                         ("key-undo", lambda: self.undo_key_change()),
                         ("key-redo", lambda: self.redo_key_change()),
                         ("key-find", lambda: self.find_key()),
                         ("key-export", self._export_selected),
                         ("key-import", self._import_selected),
                         ("profile-new", self._new_profile),
                         ("profile-edit", self._edit_profile),
                         ("profile-duplicate", self._duplicate_profile),
                         ("profile-delete", self._delete_profile),
                         ("profile-sheet", self._export_layout_sheet),
                         ("check-references", self._check_references),
                         ("page-new", self._new_page),
                         ("page-rename", self._rename_page),
                         ("page-duplicate", self._duplicate_page),
                         ("page-move-up", lambda: self._move_page(-1)),
                         ("page-move-down", lambda: self._move_page(1)),
                         ("page-delete", self._delete_page),
                         ("config-export", self._export_configuration),
                         ("config-import", self._import_configuration),
                         ("config-backups", self._open_backups),
                         ("app-log", self._open_log_file),
                         ("twitch-account", self._open_twitch_settings),
                         ("app-preferences", self._open_preferences)):
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
        self.dial_row.refresh()
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

    def _export_layout_sheet(self) -> None:
        """Save the active profile as one printable PNG."""
        name = self.app.config.profile.name.strip() or "profile"
        safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in name)
        chooser = Gtk.FileDialog(title="Export layout sheet")
        chooser.set_initial_name(f"{safe.strip('-') or 'profile'}-layout.png")
        chooser.save(self, None, self._on_layout_sheet_chosen)

    def _on_layout_sheet_chosen(self, chooser, result) -> None:
        from ..device.layout_sheet import save_profile_sheet

        try:
            file = chooser.save_finish(result)
        except Exception:
            return
        path = file.get_path() if file is not None else None
        if not path:
            self._show_configuration_error(
                "Export failed", "Choose a local destination for the sheet."
            )
            return
        destination = Path(path)
        if destination.suffix.lower() != ".png":
            destination = Path(f"{destination}.png")
        # The sheet documents the hardware it will be used with, so it takes
        # its shape from the connected deck rather than from the default grid.
        columns = max(1, getattr(self.app.deck, "columns", GRID_COLS))
        keys = getattr(self.app.deck, "key_count", columns * 3) or columns * 3
        try:
            written = save_profile_sheet(
                self.app.config.profile,
                columns,
                max(1, keys // columns),
                destination,
            )
        except Exception as error:
            log.exception("Could not export the layout sheet")
            self._show_configuration_error("Export failed", str(error))
            return
        self.app.bus.emit("status", text=f"Layout sheet saved to {written}")

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
        if (
            exported.missing_icons
            or exported.missing_audio
            or exported.missing_exit_image
        ):
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
            if exported.missing_exit_image:
                warnings.append(
                    "The custom exit image could not be found or included."
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
        if imported.restored_exit_image:
            restored.append("the custom exit image")
        if restored:
            text += f"; restored {', '.join(restored)}"
        GLib.idle_add(lambda: (self._flash_status(text), False)[1])

    def _check_references(self) -> None:
        """Check the grid on screen against the OBS collection loaded now."""
        from .reference_check import ReferenceCheckDialog

        try:
            report = self.app.controller.check_references()
        except ConnectionError as error:
            self._flash_status(str(error))
            return
        except Exception as error:
            log.exception("Could not check the keys against OBS")
            self._show_configuration_error("Check failed", str(error))
            return
        ReferenceCheckDialog(self, report).present()

    def _open_backups(self) -> None:
        from .backups import BackupDialog

        BackupDialog(self).present()

    def restore_backup(self, info) -> None:
        """Roll back to an automatic backup, from the restore dialog.

        Public because the dialog calls it: replacing the whole configuration
        invalidates the editor's selection, so it goes through the same
        unsaved-change guard as importing a bundle.
        """
        self._confirm_unsaved_changes(
            "restoring a backup",
            lambda: self._apply_backup_restore(info),
        )

    def _apply_backup_restore(self, info) -> None:
        try:
            self.app.controller.restore_backup(info.path)
        except Exception as error:
            log.exception("Could not restore the backup")
            self._show_configuration_error("Restore failed", str(error))
            return
        self.brightness.set_value(self.app.config.brightness)
        text = (
            f"Restored the backup from {info.label()}"
            if info.when is not None
            else "Backup restored"
        )
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

    def _duplicate_page(self) -> None:
        """Copy the active page. It becomes the active one, so guard the editor."""
        self._confirm_unsaved_changes(
            "duplicating the page",
            lambda: self.app.controller.duplicate_page(
                self.app.config.current_page
            ),
        )

    def _move_page(self, offset: int) -> None:
        controller = self.app.controller
        current = self.app.config.current_page
        target = current + offset
        if not 0 <= target < len(self.app.config.pages):
            self._flash_status(
                "The page is already first" if offset < 0
                else "The page is already last"
            )
            return
        # Moving keeps the same page on screen, but it drops the transient
        # state, so the editor is guarded exactly as any other page change.
        self._confirm_unsaved_changes(
            "moving the page",
            lambda: controller.move_page(current, target),
        )

    def _duplicate_profile(self) -> None:
        self._confirm_unsaved_changes(
            "duplicating the profile",
            lambda: self.app.controller.duplicate_profile(
                self.app.config.current_profile
            ),
        )

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

    # --- keyboard navigation of the grid ---

    def _add_grid_key_navigation(self, grid: Gtk.Grid) -> None:
        """Arrow keys walk the grid in the deck's own key order.

        Deliberately in the bubble phase and scoped to the grid: it only ever
        sees a key press while the focus is already on one of the keys, so no
        entry or dropdown elsewhere in the window is affected.
        """
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.BUBBLE)
        keys.connect("key-pressed", self._on_grid_key_pressed)
        grid.add_controller(keys)

    def _on_grid_key_pressed(self, _controller, keyval, _keycode, state) -> bool:
        """Move the focus between keys. Selecting still needs an explicit press.

        Focus alone must not select: the selection runs the unsaved-change
        guard, and arrowing across the grid would then raise a dialog per key.
        """
        if state & (
            Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK
        ):
            return False
        direction = _ARROW_DIRECTIONS.get(keyval)
        if direction is None:
            return False
        focused = self._focused_key_index()
        if focused is None:
            return False
        target = neighbour_index(
            focused, direction, self._grid_columns, len(self._key_buttons)
        )
        if target is not None:
            self._key_buttons[target].grab_focus()
        # Handled either way: falling through at an edge would hand the focus
        # to GTK's own directional search and leave the grid entirely.
        return True

    def _focused_key_index(self) -> int | None:
        for index, button in enumerate(self._key_buttons):
            if button.has_focus():
                return index
        return None

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
        if (
            index is None
            or self.app.controller.is_reserved_key(index)
            or self.app.controller.container.key(index) is None
        ):
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
        if (
            destination == self._drag_source_index
            or (
                destination is not None
                and self.app.controller.is_reserved_key(destination)
            )
        ):
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
            or self.app.controller.is_reserved_key(destination)
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
        if self.app.controller.is_reserved_key(index):
            return
        self._select(
            index,
            lambda: self._show_key_context_menu(index, x, y),
        )

    def _show_key_context_menu(self, index: int, x: float, y: float) -> None:
        controller = self.app.controller
        kc = controller.container.key(index)
        self._key_actions["key-open"].set_enabled(
            kc is not None and kc.contents is not None
        )
        self._key_actions["key-copy"].set_enabled(kc is not None)
        self._key_actions["key-clear"].set_enabled(kc is not None)
        self._key_actions["key-export"].set_enabled(
            kc is not None and not kc.is_empty()
        )
        self._key_actions["key-paste"].set_enabled(
            self._clipboard is not None and controller.fits_here(self._clipboard)
        )
        self._key_actions["key-paste-action"].set_enabled(
            STEP_CLIPBOARD.has_step()
        )
        self._key_actions["key-undo"].set_enabled(controller.can_undo())
        self._key_actions["key-redo"].set_enabled(controller.can_redo())
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
                          ("Delete", self.clear_key)):
            sc.add_shortcut(Gtk.Shortcut.new(
                Gtk.ShortcutTrigger.parse_string(accel),
                Gtk.CallbackAction.new(lambda w, a, cb=cb, i=index: (cb(i), True)[1]),
            ))
        # Undo and redo take no index: they act on whatever the last change was.
        for accel, cb in (("<Control>z", self.undo_key_change),
                          ("<Control><Shift>z", self.redo_key_change),
                          ("<Control>y", self.redo_key_change)):
            sc.add_shortcut(Gtk.Shortcut.new(
                Gtk.ShortcutTrigger.parse_string(accel),
                Gtk.CallbackAction.new(lambda w, a, cb=cb: (cb(), True)[1]),
            ))
        btn.add_controller(sc)

    # ---------- finding a key ----------

    def _add_window_shortcuts(self) -> None:
        """Whole-window accelerators, so they work with no key focused."""
        controller = Gtk.ShortcutController()
        controller.set_scope(Gtk.ShortcutScope.GLOBAL)
        for accel, callback in (
            ("<Control>f", self.find_key),
            ("<Control>z", self.undo_key_change),
            # Both redo conventions: Ctrl+Shift+Z is the GNOME one, Ctrl+Y is
            # what most people arrive with. Neither is used for anything else.
            ("<Control><Shift>z", self.redo_key_change),
            ("<Control>y", self.redo_key_change),
        ):
            controller.add_shortcut(Gtk.Shortcut.new(
                Gtk.ShortcutTrigger.parse_string(accel),
                Gtk.CallbackAction.new(
                    lambda w, a, cb=callback: (cb(), True)[1]
                ),
            ))
        self.add_controller(controller)

    def find_key(self) -> None:
        from .key_search import KeySearchDialog

        KeySearchDialog(self, self.app.config, self._go_to_key).present()

    def _go_to_key(self, location) -> None:
        """Navigate to a search result, through the usual unsaved guard."""
        self._confirm_unsaved_changes(
            f"going to Key {location.index + 1}",
            lambda: self._apply_go_to_key(location),
        )

    def _apply_go_to_key(self, location) -> None:
        controller = self.app.controller
        # Order matters: the profile owns the pages, and the folder path is
        # resolved against whichever page ends up active.
        controller.set_profile(location.profile)
        controller.set_page(location.page)
        controller.set_folder_path(location.path)
        if controller.is_reserved_key(location.index):
            return
        self._apply_selection(location.index)
        self._key_buttons[location.index].grab_focus()

    def undo_key_change(self) -> None:
        """Take back the last key change, if the grid still holds it."""
        controller = self.app.controller
        if not controller.can_undo():
            self._flash_status("Nothing to undo")
            return
        # The editor may be showing the key that is about to change underneath
        # it, so the same guard as any other replacement applies.
        self._confirm_unsaved_changes(
            "undoing the last change",
            self._apply_undo,
            offer_save=False,
        )

    def _apply_undo(self) -> None:
        self._announce_history(self.app.controller.undo(), "Undid")

    def redo_key_change(self) -> None:
        """Put back the last undone key change."""
        controller = self.app.controller
        if not controller.can_redo():
            self._flash_status("Nothing to redo")
            return
        self._confirm_unsaved_changes(
            "redoing the last change",
            self._apply_redo,
            offer_save=False,
        )

    def _apply_redo(self) -> None:
        self._announce_history(self.app.controller.redo(), "Redid")

    def _announce_history(self, label: str, verb: str) -> None:
        """Reload the editor over the restored key and report what happened."""
        if not label:
            return
        if self.selected is not None:
            self.editor.load(self.selected)
        self.app.bus.emit("status", text=f"{verb} {label}")

    # --- operations ---

    def _copy_key(self, index: int) -> None:
        kc = self.app.controller.container.key(index)
        self._clipboard = kc.clone() if kc is not None else None
        if self._clipboard is not None:
            self.app.bus.emit("status", text=f"Key {index + 1} copied")

    def _paste_key(self, index: int) -> None:
        if self._clipboard is None:
            self.app.bus.emit("status", text="No key copied")
            return
        if self.app.controller.is_reserved_key(index):
            return
        if not self.app.controller.fits_here(self._clipboard):
            self._flash_status(
                "That folder has too many levels to fit here"
            )
            return

        def paste() -> None:
            self._replacing_folder(
                index,
                f"replacing Key {index + 1}",
                lambda: self._apply_paste(index),
            )

        if self.selected == index:
            self._confirm_unsaved_changes(
                f"replacing Key {index + 1} with the copied key",
                paste,
                offer_save=False,
            )
        else:
            paste()

    def _apply_paste(self, index: int) -> None:
        self.app.controller.paste_key(index, self._clipboard)
        if self.selected == index:
            self.editor.load(index)
        self.app.bus.emit("status", text=f"Pasted into key {index + 1}")

    def _paste_action(self, index: int) -> None:
        """Turn a key into a single-action key running the copied step.

        The step's name is deliberately dropped: it names a row in an action
        list, and a key's own label lives in Appearance, exactly as when the
        same step is pasted onto a single-action editor.
        """
        step = STEP_CLIPBOARD.get()
        if step is None:
            self.app.bus.emit("status", text="No action copied")
            return
        if self.app.controller.is_reserved_key(index):
            return
        key = KeyConfig(kind=KIND_SINGLE, action=step.action, params=step.params)

        def paste() -> None:
            self._replacing_folder(
                index,
                f"replacing Key {index + 1}",
                lambda: self._apply_paste_action(index, key),
            )

        if self.selected == index:
            self._confirm_unsaved_changes(
                f"replacing Key {index + 1} with the copied action",
                paste,
                offer_save=False,
            )
        else:
            paste()

    def _apply_paste_action(self, index: int, key: KeyConfig) -> None:
        self.app.controller.paste_key(index, key)
        if self.selected == index:
            self.editor.load(index)
        name = action_name(key.action)
        self.app.bus.emit(
            "status", text=f"Pasted “{name}” into key {index + 1}"
        )

    def clear_key(self, index: int) -> None:
        """Empty a key, confirming unsaved edits and discarded folders first."""
        if self.app.controller.is_reserved_key(index):
            return

        def clear() -> None:
            self._replacing_folder(
                index,
                f"clearing Key {index + 1}",
                lambda: self._apply_clear(index),
            )

        if self.selected == index:
            self._confirm_unsaved_changes(
                f"clearing Key {index + 1}",
                clear,
                offer_save=False,
            )
        else:
            clear()

    def _apply_clear(self, index: int) -> None:
        self.app.controller.clear_key(index)
        if self.selected == index:
            self.editor.load(index)

    def _replacing_folder(
        self,
        index: int,
        destination: str,
        continue_action: Callable[[], None],
    ) -> None:
        """Confirm before an operation throws away a folder's contents."""
        kc = self.app.controller.container.key(index)
        contents = kc.contents if kc is not None else None
        if contents is None or not contents.keys:
            continue_action()
            return
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Discard folder contents?",
            body=(
                f"“{kc.folder_name()}” holds {len(contents.keys)} key(s). "
                f"Continuing by {destination} deletes them as well."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("discard", "Delete folder")
        dialog.set_response_appearance(
            "discard", Adw.ResponseAppearance.DESTRUCTIVE
        )
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect(
            "response",
            lambda _d, response: (
                continue_action() if response == "discard" else None
            ),
        )
        dialog.present()

    # --- portable single-key export / import ---

    def _export_key(self, index: int) -> None:
        kc = self.app.controller.container.key(index)
        if kc is None or kc.is_empty():
            self._flash_status("This key has nothing to export")
            return
        chooser = Gtk.FileDialog(title=f"Export key {index + 1}")
        chooser.set_initial_name(f"{self._key_export_name(kc, index)}.lsdkey")
        chooser.set_default_filter(self._key_file_filter())
        chooser.save(
            self, None,
            lambda dialog, result: self._on_key_export_chosen(dialog, result, kc),
        )

    def _on_key_export_chosen(self, chooser, result, kc) -> None:
        try:
            file = chooser.save_finish(result)
        except Exception:
            return
        path = file.get_path() if file is not None else None
        if not path:
            self._show_configuration_error(
                "Export failed", "Choose a local destination for the exported key."
            )
            return
        destination = Path(path)
        if destination.suffix.lower() != ".lsdkey":
            destination = Path(f"{destination}.lsdkey")
        try:
            exported = self.app.config.export_key_bundle(kc, destination)
        except Exception as error:
            log.exception("Could not export the key")
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
                "Key exported with warnings",
                f"Saved to {destination}.\n\n" + "\n".join(warnings),
            )
        else:
            self._flash_status(f"Key exported to {destination.name}")

    def _import_key(self, index: int) -> None:
        chooser = Gtk.FileDialog(title=f"Import into key {index + 1}")
        chooser.set_default_filter(self._key_file_filter())
        chooser.open(
            self, None,
            lambda dialog, result: self._on_key_import_chosen(dialog, result, index),
        )

    def _on_key_import_chosen(self, chooser, result, index: int) -> None:
        try:
            file = chooser.open_finish(result)
        except Exception:
            return
        path = file.get_path() if file is not None else None
        if not path:
            self._show_configuration_error(
                "Import failed", "Choose a local LinuxStreamDeck key export."
            )
            return
        try:
            imported = self.app.config.import_key_bundle(Path(path))
        except Exception as error:
            log.exception("Could not import the key")
            self._show_configuration_error("Import failed", str(error))
            return
        if not self.app.controller.fits_here(imported.key):
            self._show_configuration_error(
                "Import failed",
                "This key contains folders nested too deeply to fit here.",
            )
            return

        def restore() -> None:
            self.app.controller.paste_key(index, imported.key)
            if self.selected == index:
                self.editor.load(index)
            text = f"Imported {Path(path).name} into key {index + 1}"
            restored = []
            if imported.restored_icons:
                restored.append(f"{imported.restored_icons} custom icon(s)")
            if imported.restored_audio:
                restored.append(f"{imported.restored_audio} audio file(s)")
            if restored:
                text += f"; restored {', '.join(restored)}"
            self._flash_status(text)

        def apply() -> None:
            self._replacing_folder(
                index, f"replacing Key {index + 1}", restore
            )

        if self.selected == index:
            self._confirm_unsaved_changes(
                f"replacing Key {index + 1} with the imported key",
                apply,
                offer_save=False,
            )
        else:
            apply()

    @staticmethod
    def _key_export_name(kc, index: int) -> str:
        """Readable default file name built from the key label."""
        allowed = [c if c.isalnum() or c in "-_" else "-" for c in kc.label.strip()]
        name = "".join(allowed).strip("-").lower()
        while "--" in name:
            name = name.replace("--", "-")
        return name or f"key-{index + 1}"

    @staticmethod
    def _key_file_filter() -> Gtk.FileFilter:
        file_filter = Gtk.FileFilter()
        file_filter.set_name("LinuxStreamDeck key")
        file_filter.add_pattern("*.lsdkey")
        return file_filter

    def _copy_selected(self):
        if self.selected is not None:
            self._copy_key(self.selected)

    def _paste_selected(self):
        if self.selected is not None:
            self._paste_key(self.selected)

    def _paste_action_selected(self):
        if self.selected is not None:
            self._paste_action(self.selected)

    def _clear_selected(self):
        if self.selected is not None:
            self.clear_key(self.selected)

    def _open_selected_folder(self):
        if self.selected is not None:
            self.open_folder(self.selected)

    def _export_selected(self):
        if self.selected is not None:
            self._export_key(self.selected)

    def _import_selected(self):
        if self.selected is not None:
            self._import_key(self.selected)

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
        self._refresh_breadcrumb()
        # Dials belong to the page, so their row follows it.
        self.dial_row.refresh()
        if self._selected_container is self.app.controller.container:
            return
        self._clear_selection()

    def _clear_selection(self) -> None:
        self.editor.clear()
        if self.selected is not None:
            self._key_buttons[self.selected].remove_css_class("sel")
            self.selected = None
        self._selected_container = None

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

    def _open_twitch_settings(self) -> None:
        from .twitch_settings import TwitchSettingsDialog

        TwitchSettingsDialog(self, self.app).present()

    def _open_preferences(self) -> None:
        from .preferences import PreferencesDialog

        PreferencesDialog(self, self.app).present()

    def _on_preflight_report(self, _topic: str, data: dict) -> None:
        """Show the full report when a pre-flight key was pressed.

        The deck shows two words per check; this is where the sentences and the
        scope of each answer live. It only presents itself if the window is
        already open: the report is worth reading, but not worth interrupting
        someone who is looking at their deck rather than at this.
        """
        checks = data.get("checks") or ()
        if not checks or not self.get_visible():
            return
        from .preflight import PreFlightDialog

        PreFlightDialog(self, checks).present()

    # ---------- first run ----------

    def offer_starter_keys(self) -> bool:
        """Offer to fill an empty deck, the first time the application runs.

        Offered, never imposed: a configuration nobody asked for is worse than
        an empty one. It is also silently dropped if the page already holds
        anything, since the answer can arrive long after the question.
        """
        from ..core.starter import starter_keys

        if self.app.controller.container.configured_keys():
            return False
        if not starter_keys(self.app.deck.key_count):
            return False
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Start with a few keys?",
            body=(
                "Your deck is empty. LinuxStreamDeck can put a few keys on it "
                "to get you going: recording, streaming, a chapter marker, "
                "studio mode, a stopwatch and live CPU and disk readings.\n\n"
                "None of them need setting up, and you can change or remove "
                "any of them afterwards."
            ),
        )
        dialog.add_response("empty", "Start empty")
        dialog.add_response("add", "Add the keys")
        dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("add")
        dialog.connect("response", self._on_starter_response)
        dialog.present()
        return False

    def _on_starter_response(self, _dialog, response: str) -> None:
        if response != "add":
            return
        from ..core.starter import apply_starter_keys

        added = apply_starter_keys(self.app.config, self.app.deck.key_count)
        if not added:
            return
        # Nothing preceded these keys, so there is nothing to take back to.
        self.app.controller.forget_undo()
        self.app.controller.refresh()
        self._flash_status(
            f"Added {added} keys to get started; select one to change it"
        )

    def _open_log_file(self) -> None:
        """Hand the log to whatever the desktop opens text files with.

        It reports the path either way. A missing file is worth saying plainly,
        and so is the path itself: attaching it to a bug report is the point,
        and nobody can attach a file they cannot find.
        """
        from ..core.config import LOG_FILE

        if not LOG_FILE.exists():
            self._flash_status(f"No log file yet at {LOG_FILE}")
            return
        launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(str(LOG_FILE)))
        launcher.launch(self, None, self._on_log_launched)

    def _on_log_launched(self, launcher, result) -> None:
        try:
            launcher.launch_finish(result)
        except Exception:
            from ..core.config import LOG_FILE

            # No handler for a .log file is common enough to be worth naming
            # the path rather than reporting a failure the user cannot act on.
            self._flash_status(f"Log file: {LOG_FILE}")

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
        connected = self.app.obs.connected
        icon = (
            "network-transmit-receive-symbolic"
            if connected
            else "network-offline-symbolic"
        )
        self.obs_btn.set_icon_name(icon)
        # The dot carries the colour; the tooltip carries the words, so the
        # state is still available to anyone who cannot rely on the colour.
        if connected:
            self.obs_dot.add_css_class("online")
        else:
            self.obs_dot.remove_css_class("online")
        self.obs_dot.set_tooltip_text(obs_txt)

    def _flash_status(self, text: str) -> None:
        self.status.set_label(text)
        GLib.timeout_add_seconds(5, lambda: (self._update_status(), False)[1])
