"""Searchable list of every registered action.

The two chained dropdowns in the step editor need the category to be known
before the action can be found, which is fine for the handful of actions someone
uses daily and useless for the rest. This searches all of them at once, and
shows the description, so an action can be found by what it does rather than by
where it lives.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ..core import actions as registry  # noqa: E402

SEARCH_DEBOUNCE_MS = 120


def matches(action, query: str) -> bool:
    """Whether an action satisfies every term of a query.

    Terms are ANDed and matched against the name, category, description and id
    together, so "obs mute" and "mute obs" both find the same thing, and an id
    pasted from a config file still resolves.
    """
    haystack = " ".join(
        (action.name, action.category, action.description or "", action.id)
    ).lower()
    return all(term in haystack for term in query.lower().split())


def ranked(query: str) -> list:
    """Every matching action, the ones named after the query first."""
    found = [
        action
        for actions in registry.by_category().values()
        for action in actions
        if not query or matches(action, query)
    ]
    lowered = query.lower().strip()

    def rank(action) -> tuple[int, str]:
        name = action.name.lower()
        if not lowered:
            return (2, name)
        if name.startswith(lowered):
            return (0, name)
        if lowered in name:
            return (1, name)
        return (2, name)

    return sorted(found, key=rank)


class ActionPickerDialog(Adw.Window):
    def __init__(self, parent, on_selected, current: str = "") -> None:
        super().__init__(
            transient_for=parent,
            modal=True,
            title="Choose an action",
            default_width=560,
            default_height=620,
        )
        self._on_selected = on_selected
        self._current = current
        self._search_source = 0

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
            placeholder_text="Search action (e.g. mute, scene, timer)…"
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

    # ---------- filtering ----------

    def _schedule(self) -> None:
        # Coalesce fast keystrokes so the list is not rebuilt on every one.
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
        self._shown = ranked(self.search.get_text().strip())
        while (row := self.list.get_first_child()) is not None:
            self.list.remove(row)
        for action in self._shown:
            self.list.append(self._row(action))
        total = sum(len(a) for a in registry.by_category().values())
        self.count.set_label(
            f"{len(self._shown)} of {total} actions"
            if len(self._shown) != total
            else f"{total} actions"
        )

    def _row(self, action) -> Gtk.Widget:
        row = Adw.ActionRow(
            title=action.name,
            subtitle=action.description or action.category,
            activatable=True,
        )
        row.set_subtitle_lines(2)
        if action.description:
            # The category is what the dropdowns organise by, so it stays
            # visible even when the description takes the subtitle.
            tag = Gtk.Label(label=action.category)
            tag.add_css_class("dim-label")
            tag.add_css_class("caption")
            row.add_suffix(tag)
        if action.id == self._current:
            mark = Gtk.Image.new_from_icon_name("object-select-symbolic")
            row.add_prefix(mark)
        row.connect("activated", lambda _r, a=action: self._choose(a.id))
        return row

    def _activate_first(self) -> None:
        """Enter picks the best match, so a search never needs the mouse."""
        if self._shown:
            self._choose(self._shown[0].id)

    def _choose(self, action_id: str) -> None:
        if self._search_source:
            GLib.source_remove(self._search_source)
            self._search_source = 0
        self.close()
        self._on_selected(action_id)
