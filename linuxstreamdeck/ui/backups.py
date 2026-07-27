"""Restore one of the automatic configuration backups.

The rolling backups are written on every save, but a safety net you can only
reach with a file manager is only half a safety net. This lists them by date
and by what they hold, because a filename alone cannot tell you which copy you
want back.

Restoring goes through the same path as importing a bundle — the whole
configuration is replaced, so it takes the unsaved-change guard with it — and
it snapshots the current state first, so choosing the wrong copy is not the end
of the story.
"""

from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from ..core.config import Config  # noqa: E402

log = logging.getLogger(__name__)


class BackupDialog(Adw.Window):
    def __init__(self, window) -> None:
        super().__init__(
            transient_for=window,
            modal=True,
            title="Restore a backup",
            default_width=520,
            default_height=460,
        )
        self.window = window
        self.app = window.app

        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())

        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(
            title="Automatic backups",
            description=(
                "LinuxStreamDeck keeps a rolling set of earlier configurations. "
                "Restoring replaces everything: profiles, pages and keys. The "
                "current configuration is backed up first, and your saved "
                "passwords are left untouched."
            ),
        )
        self._backups = [
            Config.describe_backup(path) for path in Config.backup_history()
        ]
        if self._backups:
            for info in self._backups:
                group.add(self._row(info))
        else:
            empty = Adw.ActionRow(
                title="No backups yet",
                subtitle="One is written the first time you change something.",
            )
            empty.set_sensitive(False)
            group.add(empty)
        page.add(group)
        view.set_content(page)
        self.set_content(view)

    def _row(self, info) -> Adw.ActionRow:
        row = Adw.ActionRow(title=info.label())
        row.set_subtitle(info.path.name)
        button = Gtk.Button(label="Restore", valign=Gtk.Align.CENTER)
        # Readable or not, the row is listed: a backup that cannot be parsed is
        # something the user should see rather than something quietly hidden.
        button.set_sensitive(info.readable)
        button.connect("clicked", self._confirm, info)
        row.add_suffix(button)
        row.set_activatable_widget(button if info.readable else None)
        return row

    def _confirm(self, _button, info) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Restore this backup?",
            body=(
                f"Everything will be replaced with the configuration from "
                f"{info.label()}.\n\nThe current one is saved as a new backup "
                f"first, so this can be undone by restoring that."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("restore", "Restore")
        dialog.set_response_appearance(
            "restore", Adw.ResponseAppearance.DESTRUCTIVE
        )
        dialog.connect("response", self._on_response, info)
        dialog.present()

    def _on_response(self, _dialog, response: str, info) -> None:
        if response != "restore":
            return
        # Replacing the whole configuration invalidates whatever the editor is
        # showing, so it runs through the same guard as importing a bundle.
        self.window.restore_backup(info)
        self.close()
