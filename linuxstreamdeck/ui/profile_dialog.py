"""Dialog to create or edit a profile (name + short description)."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402


class ProfileDialog(Adw.Window):
    def __init__(self, parent, title: str, name: str, description: str, on_save) -> None:
        super().__init__(
            transient_for=parent, modal=True, title=title,
            default_width=440, default_height=360,
        )
        self._on_save = on_save

        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())

        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(
            title=title,
            description="Each profile stores its own set of pages and keys.",
        )
        self.name = Adw.EntryRow(title="Profile name", text=name)
        self.desc = Adw.EntryRow(title="Short description", text=description)
        group.add(self.name)
        group.add(self.desc)
        page.add(group)

        actions = Adw.PreferencesGroup()
        save = Gtk.Button(label="Save", margin_top=6)
        save.add_css_class("suggested-action")
        save.connect("clicked", self._save)
        actions.add(save)
        page.add(actions)

        view.set_content(page)
        self.set_content(view)
        self.name.grab_focus()

    def _save(self, _btn) -> None:
        name = self.name.get_text().strip()
        if not name:
            self.name.grab_focus()
            return
        self._on_save(name, self.desc.get_text().strip())
        self.close()
