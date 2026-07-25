"""Application preferences: what closing does, and starting with the session."""

from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from ..core import autostart  # noqa: E402
from ..core.config import CLOSE_ACTION_CHOICES, CLOSE_ACTION_QUIT  # noqa: E402

log = logging.getLogger(__name__)


class PreferencesDialog(Adw.Window):
    def __init__(self, parent, app) -> None:
        # No default height: the content decides it (see the scroller below), so
        # the buttons are never cut off, whatever the settings list grows to.
        super().__init__(
            transient_for=parent,
            modal=True,
            title="Preferences",
            default_width=560,
        )
        self.app = app

        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        page = Adw.PreferencesPage()
        page.set_hexpand(True)

        window_group = Adw.PreferencesGroup(
            title="Closing the window",
            description=(
                "LinuxStreamDeck can keep controlling the Stream Deck after you "
                "close its window. Its status icon then switches profiles, "
                "reopens the window and quits."
            ),
        )
        window_group.set_hexpand(True)
        window_group.set_size_request(430, -1)

        self.close_action = Adw.ComboRow(title="When the window is closed")
        self.close_action.set_model(
            Gtk.StringList.new([choice[1] for choice in CLOSE_ACTION_CHOICES])
        )
        self.close_action.set_selected(self._close_action_index())
        self.close_action.connect("notify::selected", self._on_close_action_changed)
        window_group.add(self.close_action)

        self.close_action_hint = Adw.ActionRow()
        self.close_action_hint.set_activatable(False)
        window_group.add(self.close_action_hint)
        page.add(window_group)

        session_group = Adw.PreferencesGroup(
            title="Session",
            description=(
                "A startup entry is added to this computer only. It is never "
                "part of an exported configuration."
            ),
        )
        session_group.set_hexpand(True)
        session_group.set_size_request(430, -1)
        self.start_on_login = Adw.SwitchRow(
            title="Start automatically on login",
            subtitle=(
                "Launch LinuxStreamDeck with the desktop session, straight into "
                "its status icon"
            ),
        )
        self.start_on_login.set_active(autostart.is_enabled())
        session_group.add(self.start_on_login)
        page.add(session_group)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(page)
        # A scrolled window reports almost no natural height of its own, so the
        # window would open too short to show everything. Propagating the page's
        # natural height makes it open at the right size instead.
        scroller.set_propagate_natural_height(True)
        view.set_content(scroller)

        # The buttons live outside the scroller, so they stay on screen even if
        # the settings list ever outgrows the window.
        buttons = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=10,
            margin_top=10,
            margin_bottom=10,
            margin_start=14,
            margin_end=14,
        )
        cancel = Gtk.Button(label="Cancel", hexpand=True)
        cancel.connect("clicked", lambda _b: self.close())
        buttons.append(cancel)
        save = Gtk.Button(label="Save", hexpand=True)
        save.add_css_class("suggested-action")
        save.connect("clicked", self._save)
        buttons.append(save)
        view.add_bottom_bar(buttons)

        self.set_content(view)
        self._on_close_action_changed()

    # ---------- state ----------

    def _close_action_index(self) -> int:
        current = self.app.config.close_action
        return next(
            (
                index
                for index, choice in enumerate(CLOSE_ACTION_CHOICES)
                if choice[0] == current
            ),
            0,
        )

    def _selected_close_action(self) -> tuple[str, str, str]:
        index = min(
            int(self.close_action.get_selected()),
            len(CLOSE_ACTION_CHOICES) - 1,
        )
        return CLOSE_ACTION_CHOICES[index]

    def _on_close_action_changed(self, *_args) -> None:
        action, _name, description = self._selected_close_action()
        # Without a status area there is nothing to hide into, so say so instead
        # of letting the user pick a setting the session cannot honour.
        if action != CLOSE_ACTION_QUIT and not self.app.tray_available:
            self.close_action_hint.set_title(
                "No status area was found in this session"
            )
            self.close_action_hint.set_subtitle(
                "Closing the window will quit until one is available. On GNOME, "
                "install an AppIndicator extension."
            )
            self.close_action_hint.add_css_class("warning")
        else:
            self.close_action_hint.set_title(description)
            self.close_action_hint.set_subtitle("")
            self.close_action_hint.remove_css_class("warning")

    # ---------- saving ----------

    def _save(self, _button) -> None:
        action = self._selected_close_action()[0]
        error = self.app.update_application_settings(
            action, self.start_on_login.get_active()
        )
        if error is not None:
            self._show_error(str(error))
            return
        self.close()

    def _show_error(self, message: str) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Could not update the startup entry",
            body=message,
        )
        dialog.add_response("close", "Close")
        dialog.present()
