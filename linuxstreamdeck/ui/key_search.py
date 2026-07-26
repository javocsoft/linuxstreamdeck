"""Find a key anywhere in the configuration and go to it."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ..core.search import search  # noqa: E402

SEARCH_DEBOUNCE_MS = 140
MAX_RESULTS = 200


class KeySearchDialog(Adw.Window):
    def __init__(self, parent, config, on_selected) -> None:
        super().__init__(
            transient_for=parent,
            modal=True,
            title="Find a key",
            default_width=620,
            default_height=560,
        )
        self._config = config
        self._on_selected = on_selected
        self._search_source = 0
        self._shown: list = []

        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())

        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=8,
            margin_top=10,
            margin_bottom=10,
            margin_start=10,
            margin_end=10,
        )
        self.search = Gtk.SearchEntry(
            placeholder_text="Search by label, action or value (e.g. mic, scene Live)…"
        )
        self.search.connect("search-changed", lambda *_a: self._schedule())
        self.search.connect("activate", lambda *_a: self._activate_first())
        box.append(self.search)

        self.count = Gtk.Label(xalign=0)
        self.count.add_css_class("dim-label")
        box.append(self.count)

        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list.add_css_class("boxed-list")
        scroller = Gtk.ScrolledWindow(child=self.list, vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        box.append(scroller)

        view.set_content(box)
        self.set_content(view)
        self._populate()
        self.search.grab_focus()

    def _schedule(self) -> None:
        if self._search_source:
            GLib.source_remove(self._search_source)
        self._search_source = GLib.timeout_add(
            SEARCH_DEBOUNCE_MS, self._search_timeout
        )

    def _search_timeout(self) -> bool:
        self._search_source = 0
        self._populate()
        return False

    def _populate(self) -> None:
        query = self.search.get_text().strip()
        found = search(self._config, query)
        self._shown = found[:MAX_RESULTS]
        while (row := self.list.get_first_child()) is not None:
            self.list.remove(row)
        for location in self._shown:
            self.list.append(self._row(location))
        if not query:
            self.count.set_label("Type to search every profile, page and folder")
        elif not found:
            self.count.set_label("No key matches")
        elif len(found) > len(self._shown):
            self.count.set_label(
                f"{len(self._shown)} of {len(found)} matching keys"
            )
        else:
            self.count.set_label(f"{len(found)} matching key(s)")

    def _row(self, location) -> Gtk.Widget:
        row = Adw.ActionRow(
            title=location.what(),
            subtitle=location.where(),
            activatable=True,
        )
        row.connect(
            "activated", lambda _r, found=location: self._choose(found)
        )
        return row

    def _activate_first(self) -> None:
        if self._shown:
            self._choose(self._shown[0])

    def _choose(self, location) -> None:
        if self._search_source:
            GLib.source_remove(self._search_source)
            self._search_source = 0
        self.close()
        self._on_selected(location)
