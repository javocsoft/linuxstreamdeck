"""Reusable components of the key editor:

- StepEditor   : selects category + action + parameters of ONE step.
- StepList     : reorderable list of StepEditor (for multiple actions).
- AppearanceBox: label + font size + icon + background color of one key state.

StepEditor concentrates the logic of filling the dropdowns live from OBS, which
used to live in EditorPanel, so it can be reused for each step.
"""

from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, GObject, Gtk, Pango  # noqa: E402

from ..core import actions as registry  # noqa: E402
from ..core.actions import Action, Param, format_duration, parse_duration  # noqa: E402
from ..obs.client import hotkey_display_name  # noqa: E402
from ..core.config import (  # noqa: E402
    DEFAULT_KEY_BG,
    KEY_FONT_SIZE_AUTO,
    KEY_FONT_SIZE_CHOICES,
    KEY_TEXT_COLOR_AUTO,
    ActionStep,
)
# The renderer owns the default label colour; the editor only has to show what
# an inherited (empty) value will actually look like.
from ..device.renderer import TEXT_COLOR as DEFAULT_TEXT_COLOR  # noqa: E402

log = logging.getLogger(__name__)

CATEGORY_ORDER = [
    "OBS · Scenes", "OBS · Recording & Streaming", "OBS · Audio",
    "OBS · Sources & Filters", "OBS · Media", "OBS · Advanced",
    "System", "Navigation",
]

# Option sources answered locally, without OBS. They are the same ones
# `_fetch_choices` resolves above its `obs.connected` guard: keep both in sync,
# or a local dropdown stops filling while OBS is closed.
LOCAL_CHOICE_SOURCES = frozenset({"pages", "deck_profiles", "applications"})

# Internal drag payload used to reorder the steps of one list.
_STEP_DRAG_PREFIX = "linuxstreamdeck-step:"

# Live suggestions under a text field (Param.completion_source).
# The pause after a keystroke before anything is asked: every search past it is
# a request over the network, and asking per character would spend a rate limit
# on prefixes nobody meant to search for.
COMPLETION_DEBOUNCE_MS = 320
# Below this, a query matches most of the catalogue and suggests nothing useful.
COMPLETION_MIN_CHARS = 2
COMPLETION_LIMIT = 8
# Twitch box art is 3:4. Big enough to recognise a game at a glance, which is
# the whole point: "Detroit", "Detroit: Become Human" and "The Detroit After"
# are hard to tell apart as words and immediate as pictures.
COMPLETION_ART_SIZE = (40, 53)


class _CompletionPopup:
    """Suggestions under a text entry, fetched off the GTK main thread.

    Built by hand rather than with `Gtk.EntryCompletion`, which is deprecated
    and drags in the whole `Gtk.TreeModel` stack, and which assumes the options
    are already known. These are not: each one is a request to Twitch, so the
    interesting parts of this are the debounce, the worker and the guard that
    throws away an answer to a query the field has already moved past.

    The popover is deliberately **not** autohiding. An autohiding one takes the
    focus when it opens, which stops the typing that opened it.
    """

    def __init__(self, entry: Gtk.Entry, search, artwork=None) -> None:
        self.entry = entry
        self._search = search               # (query) -> suggestions, blocking
        self._artwork = artwork             # (url) -> bytes | None, blocking
        self._timer = 0
        # Two counters, because they answer two different questions. `_search`
        # says which query is current and is bumped by `close()`, since an
        # answer arriving for a field that has stopped asking must not reopen
        # it. `_fill` says which set of rows is on screen; closing does not
        # replace those rows, so sharing one counter meant every close threw
        # away artwork belonging to rows that were still there.
        self._generation = 0
        self._fill_generation = 0
        # Everything the service has confirmed exists, as {casefolded: real
        # name}. The value the field opens with is seeded in: it was either
        # chosen from this list when it was set or it predates the list, and
        # discarding it because nobody happened to search for it again would
        # empty a working key just for being opened.
        initial = entry.get_text().strip()
        self._known: dict[str, str] = {initial.casefold(): initial} if initial else {}
        self._list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self._list.add_css_class("completion-list")
        self._popover = Gtk.Popover(
            child=self._list,
            autohide=False,
            has_arrow=False,
            position=Gtk.PositionType.BOTTOM,
            # With no arrow, a popover aligns itself inside the rectangle it
            # points at according to this. Left by default it is centered on
            # the field, which reads as a floating window rather than as a list
            # belonging to the line being typed in.
            halign=Gtk.Align.START,
        )
        self._popover.set_parent(entry)
        self._popover.add_css_class("completion-popup")
        entry.connect("changed", self._on_changed)
        entry.connect("notify::has-focus", self._on_focus_changed)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        entry.add_controller(keys)
        # A popover parented to a widget is not destroyed with it.
        entry.connect("destroy", lambda *_a: self.close())

    # ---------- typing ----------

    def _on_changed(self, _entry) -> None:
        self._cancel_timer()
        text = self.entry.get_text().strip()
        if len(text) < COMPLETION_MIN_CHARS:
            self.close()
            # An empty or barely started field is unfinished, not wrong.
            self._mark_unsettled()
            return
        if self.settled_value():
            # Typing back onto a name the service already confirmed, so the
            # warning comes off without waiting for another search.
            self._mark_unsettled()
        self._timer = GLib.timeout_add(COMPLETION_DEBOUNCE_MS, self._start, text)

    def _start(self, text: str) -> bool:
        self._timer = 0
        self._generation += 1
        generation = self._generation
        import threading

        threading.Thread(
            target=self._work,
            args=(text, generation),
            daemon=True,
            name="completion",
        ).start()
        return False

    def _work(self, text: str, generation: int) -> None:
        try:
            names = self._search(text)
        except Exception:
            log.debug("Could not fetch suggestions", exc_info=True)
            names = []
        GLib.idle_add(self._show, names, generation, text)

    def _show(self, names: list[str], generation: int, text: str) -> bool:
        # An answer to a query the field has already moved past would replace
        # the current suggestions with older ones, which is worse than none.
        if generation != self._generation or self.entry.get_text().strip() != text:
            return False
        self._fill(names)
        return False

    def _fill(self, suggestions: list) -> None:
        # These rows replace whatever was there, so any artwork still in flight
        # belongs to a list nobody is looking at.
        self._fill_generation += 1
        while (row := self._list.get_first_child()) is not None:
            self._list.remove(row)
        if not suggestions or not self.entry.get_root():
            # A search that matched nothing is itself an answer: what is in the
            # field is not a real value, so it is worth saying now.
            self.close()
            self._mark_unsettled()
            return
        shown = suggestions[:COMPLETION_LIMIT]
        pictures: list[tuple[str, Gtk.Picture]] = []
        for suggestion in shown:
            row, picture = self._row(suggestion)
            self._list.append(row)
            name = getattr(suggestion, "name", str(suggestion))
            # Anything the service offered exists, whether it is clicked or
            # typed out in full, so seeing it is enough to confirm it.
            self._known[name.strip().casefold()] = name
            url = getattr(suggestion, "box_art_url", "")
            if url and picture is not None:
                pictures.append((url, picture))
        self._mark_unsettled()
        self._place()
        self._popover.popup()
        if pictures and self._artwork is not None:
            self._load_artwork(pictures, self._fill_generation)

    def _row(self, suggestion) -> tuple[Gtk.Widget, Gtk.Picture | None]:
        """One suggestion, chosen on the press rather than on the click.

        A `clicked` signal needs the press and the release to reach the same
        widget while it is still mapped, and pressing here moves the focus off
        the entry, which closes this popover — so the release landed on a
        widget that had already gone and the suggestion was never applied. A
        CAPTURE-phase gesture acts on the press, before any of that.
        """
        name = getattr(suggestion, "name", str(suggestion))
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        picture: Gtk.Picture | None = None
        if self._artwork is not None:
            # Reserved whether or not the picture ever arrives, so a late or
            # missing one cannot make the rows jump as the list fills.
            picture = Gtk.Picture(
                can_shrink=True,
                content_fit=Gtk.ContentFit.COVER,
                width_request=COMPLETION_ART_SIZE[0],
                height_request=COMPLETION_ART_SIZE[1],
            )
            picture.add_css_class("completion-art")
            box.append(picture)
        label = Gtk.Label(
            label=name, xalign=0, hexpand=True,
            ellipsize=Pango.EllipsizeMode.END,
        )
        box.append(label)
        row = Gtk.ListBoxRow(child=box, activatable=True)
        gesture = Gtk.GestureClick()
        gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        gesture.connect("pressed", lambda *_a, value=name: self._choose(value))
        row.add_controller(gesture)
        return row, picture

    # ---------- artwork ----------

    def _load_artwork(self, wanted, generation: int) -> None:
        """Fetch the row pictures on a worker, in the order they are shown.

        One thread rather than one per row: these are downloads, the first rows
        are the ones being looked at, and a burst of eight parallel requests
        buys nothing anybody would notice.
        """
        import threading

        threading.Thread(
            target=self._artwork_work,
            args=(list(wanted), generation),
            daemon=True,
            name="completion-art",
        ).start()

    def _artwork_work(self, wanted, generation: int) -> None:
        for url, picture in wanted:
            if generation != self._fill_generation:
                return
            try:
                data = self._artwork(url)
            except Exception:
                log.debug("Could not fetch category art", exc_info=True)
                data = None
            if data:
                GLib.idle_add(self._apply_artwork, picture, data, generation)

    def _apply_artwork(self, picture: Gtk.Picture, data: bytes, generation: int):
        # The list may have been replaced while this was in flight, in which
        # case these rows are gone. Closing is deliberately not a reason to
        # drop it: the rows are still there and reopening shows them again.
        if generation != self._fill_generation or picture.get_parent() is None:
            return False
        try:
            texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
        except GLib.Error:
            log.debug("Unreadable category art", exc_info=True)
            return False
        picture.set_paintable(texture)
        return False

    def _place(self) -> None:
        """Put the list directly under the field, matching its width.

        Without an explicit rectangle the popover points at the whole entry
        including its shadow, which is wider than the visible field and starts
        slightly to its left.
        """
        width = self.entry.get_width()
        if width <= 0:
            return
        self._list.set_size_request(width, -1)
        rect = Gdk.Rectangle()
        rect.x = 0
        rect.y = self.entry.get_height()
        rect.width = width
        rect.height = 1
        self._popover.set_pointing_to(rect)

    def settled_value(self) -> str:
        """The typed text, but only when the service recognises it.

        Answers the name as the service spells it rather than as it was typed,
        so what gets stored is a real category rather than a near miss that
        happens to resolve. Anything unrecognised answers empty: the key is
        then plainly unconfigured instead of one that fails the first time it
        is pressed on air.
        """
        return self._known.get(self.entry.get_text().strip().casefold(), "")

    def _mark_unsettled(self) -> None:
        """Show, while typing, that what is in the field is not a real value.

        Done once a search has answered rather than on every keystroke: half a
        word is not yet wrong, and marking it as it is typed would be noise.
        """
        if self.settled_value() or not self.entry.get_text().strip():
            self.entry.remove_css_class("unsettled")
        else:
            self.entry.add_css_class("unsettled")

    def _choose(self, name: str) -> None:
        self._known[name.strip().casefold()] = name
        self.entry.set_text(name)
        self.entry.set_position(-1)
        # Setting the text fires `changed`, which would schedule a search for
        # the name just chosen and reopen the list under it. Closing after that
        # is what makes picking a suggestion final.
        self.close()
        self._mark_unsettled()
        self.entry.grab_focus()
        self.entry.set_position(-1)

    # ---------- lifetime ----------

    def _on_key(self, _controller, keyval, _code, _state) -> bool:
        if keyval == Gdk.KEY_Escape and self._popover.get_visible():
            self.close()
            return True
        return False

    def _on_focus_changed(self, *_a) -> None:
        """Close when the field is left, unless it was left *for* this list.

        Clicking a suggestion moves the focus off the entry, so closing on any
        focus loss takes the list away from under the pointer mid-click.
        """
        if self.entry.has_focus() or self._focus_is_inside():
            return
        self.close()

    def _focus_is_inside(self) -> bool:
        root = self.entry.get_root()
        focus = root.get_focus() if root is not None else None
        while focus is not None:
            if focus is self._popover:
                return True
            focus = focus.get_parent()
        return False

    def _cancel_timer(self) -> None:
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    def close(self) -> None:
        """Idempotent: focus loss, Escape and a rebuild can all reach it."""
        self._cancel_timer()
        # Any answer still in flight belongs to a field that is no longer
        # asking, so it must not reopen this.
        self._generation += 1
        if self._popover.get_visible():
            self._popover.popdown()


class _StepClipboard:
    """The one copied step, shared by every action editor on screen.

    It deliberately does not live on a StepList: the editor rebuilds all of its
    lists whenever the selected key or the key type changes, so a copy held
    there could never be pasted into another list, let alone another key.
    """

    def __init__(self) -> None:
        self._step: ActionStep | None = None

    def has_step(self) -> bool:
        return self._step is not None

    def get(self) -> ActionStep | None:
        """A fresh copy, so pasting twice cannot share one params dict."""
        return _copy_step(self._step)

    def set(self, step: ActionStep | None) -> None:
        # A step with no action is what an empty row reads as; copying it would
        # only offer a paste that adds nothing.
        self._step = _copy_step(step) if step is not None and step.action else None

    def clear(self) -> None:
        self._step = None


def _copy_step(step: ActionStep | None) -> ActionStep | None:
    if step is None:
        return None
    return ActionStep(action=step.action, params=dict(step.params), label=step.label)


STEP_CLIPBOARD = _StepClipboard()


def _report_copied(app, name: str) -> None:
    # Copying changes nothing on screen, so the status bar is the only sign it
    # worked, and the only place to say where the copy can go.
    app.bus.emit(
        "status",
        text=f"Copied “{name}”; right-click an action list to paste it",
    )


def _report_pasted(app, name: str) -> None:
    app.bus.emit("status", text=f"Pasted “{name}”")


class _ContextMenu:
    """One right-click menu at a time, unparented as soon as it closes.

    The rows it is shown on are destroyed by every StepList rebuild, so it must
    never outlive its anchor; `close()` is therefore idempotent and is called
    both when the menu is dismissed and before a rebuild.
    """

    def __init__(self) -> None:
        self._popover: Gtk.Popover | None = None
        self._on_close = None

    def show(
        self, anchor: Gtk.Widget, x: float, y: float, items, on_close=None
    ) -> Gtk.Popover:
        """Pop up `items` at the pointer.

        Each item is `(label, enabled, callback)`, or `None` for a separator.
        `on_close` runs whenever the menu goes away, however it goes away.
        """
        self.close()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.set_size_request(180, -1)
        for item in items:
            if item is None:
                box.append(Gtk.Separator(margin_top=3, margin_bottom=3))
                continue
            label, enabled, callback = item
            button = Gtk.Button(label=label)
            button.add_css_class("flat")
            button.set_sensitive(enabled)
            if isinstance(child := button.get_child(), Gtk.Label):
                child.set_xalign(0)
            button.connect("clicked", self._activate, callback)
            box.append(button)
        popover = Gtk.Popover(child=box, has_arrow=False)
        popover.add_css_class("step-menu")
        popover.set_parent(anchor)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        popover.set_pointing_to(rect)
        # Dismissing it by clicking away must not leave it parented to a row the
        # list may rebuild later.
        popover.connect("closed", lambda _p: GLib.idle_add(self.close))
        self._popover = popover
        self._on_close = on_close
        popover.popup()
        return popover

    def _activate(self, _button, callback) -> None:
        # Closed first: the callback usually rebuilds the list, which destroys
        # the row this menu is parented to.
        self.close()
        callback()

    def close(self) -> bool:
        popover, self._popover = self._popover, None
        on_close, self._on_close = self._on_close, None
        if popover is not None:
            popover.popdown()
            if popover.get_parent() is not None:
                popover.unparent()
        if on_close is not None:
            on_close()
        return False


def rgba_to_hex(rgba: Gdk.RGBA) -> str:
    return "#{:02x}{:02x}{:02x}".format(
        int(rgba.red * 255), int(rgba.green * 255), int(rgba.blue * 255)
    )


def _row(label: str, widget: Gtk.Widget) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
    lbl = Gtk.Label(label=label, xalign=0)
    lbl.add_css_class("caption-heading")
    box.append(lbl)
    box.append(widget)
    return box


def _display_options(source: str, options: list[str]) -> tuple[list[str], dict[str, str]]:
    """Labels to show for a dynamic dropdown, plus their stored values.

    Only OBS hotkeys need this: their identifiers ("OBSBasic.StartStreaming")
    are what the protocol takes but not what anyone wants to read. Everything
    else already lists real names, so it maps to itself.
    """
    if source != "hotkeys":
        return list(options), {}
    labels: list[str] = []
    values: dict[str, str] = {}
    for option in options:
        label = hotkey_display_name(option) or option
        if label in values and values[label] != option:
            # Two different hotkeys reading the same: keep them distinguishable.
            label = f"{label} ({option})"
        if label in values:
            continue
        labels.append(label)
        values[label] = option
    return labels, values


def _labelled_choices(param: Param) -> tuple[list[str], dict[str, str]]:
    """A static dropdown's visible labels, plus their stored values.

    Without `choice_labels` a choice shows exactly what it stores, which is
    right for values that already read as words ("toggle", "start"). It is
    wrong for identifiers: a list reading "stream_time" and "render_lag" tells
    nobody what those keys would show.
    """
    if not param.choice_labels:
        return list(param.choices), {}
    labels: list[str] = []
    stored: dict[str, str] = {}
    for choice in param.choices:
        label = param.choice_labels.get(choice) or choice
        # Two choices reading the same would make one unreachable.
        if label in stored:
            label = f"{label} ({choice})"
        labels.append(label)
        stored[label] = choice
    return labels, stored


def _label_for(values: dict[str, str], value) -> str:
    """The label a stored value is shown as."""
    text = str(value or "")
    if not values:
        return text
    return next((label for label, stored in values.items() if stored == text), text)


def _clear(box: Gtk.Box) -> None:
    while (child := box.get_first_child()) is not None:
        box.remove(child)


class _FileEntry(Gtk.Box):
    """Editable path plus a filtered native chooser, for a file or a folder."""

    def __init__(self, param: Param, value) -> None:
        super().__init__(spacing=6)
        self.param = param
        self.entry = Gtk.Entry(
            text=str(value if value is not None else param.default or ""),
            hexpand=True,
        )
        self.entry.set_placeholder_text(
            param.placeholder
            or ("Choose a local folder" if param.directory else "Choose a local file")
        )
        self.entry.connect(
            "changed",
            lambda entry: entry.set_tooltip_text(entry.get_text() or None),
        )
        self.entry.set_tooltip_text(self.entry.get_text() or None)
        self.append(self.entry)

        button = Gtk.Button.new_from_icon_name(
            "folder-symbolic" if param.directory else "document-open-symbolic"
        )
        button.set_tooltip_text("Choose folder" if param.directory else "Choose file")
        button.connect("clicked", self._choose)
        self.append(button)

    def get_text(self) -> str:
        return self.entry.get_text()

    def _choose(self, _button) -> None:
        dialog = Gtk.FileDialog(title=f"Choose {self.param.label.lower()}")
        if self.param.directory:
            # A folder has no extensions to filter on, so the filter is skipped.
            dialog.select_folder(self.get_root(), None, self._chosen)
            return
        if self.param.extensions:
            file_filter = Gtk.FileFilter()
            file_filter.set_name(self.param.file_filter_name or "Supported files")
            for extension in self.param.extensions:
                suffix = extension if extension.startswith(".") else f".{extension}"
                file_filter.add_pattern(f"*{suffix.lower()}")
                file_filter.add_pattern(f"*{suffix.upper()}")
            dialog.set_default_filter(file_filter)
        dialog.open(self.get_root(), None, self._chosen)

    def _chosen(self, dialog, result) -> None:
        try:
            file = (
                dialog.select_folder_finish(result)
                if self.param.directory
                else dialog.open_finish(result)
            )
        except Exception:
            return
        if file is not None and (path := file.get_path()):
            self.entry.set_text(path)


# =========================== single-step editor ===========================

class StepEditor(Gtk.Box):
    def __init__(self, app, on_change=None, show_label: bool = False) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.app = app
        self._on_change = on_change          # called when the chosen action changes
        self._param_widgets: dict[str, tuple[Param, Gtk.Widget]] = {}
        # The row holding each parameter widget, so a dependent parameter can be
        # rebuilt in place when its options change (see _repopulate).
        self._param_rows: dict[str, Gtk.Box] = {}
        self._building = False
        self._menu = _ContextMenu()

        cats = registry.by_category()
        self._cat_names = [c for c in CATEGORY_ORDER if c in cats] + [
            c for c in cats if c not in CATEGORY_ORDER
        ]

        # Only steps inside a list can be named: a single-action key has no list
        # entry to name, and its own label already lives in Appearance.
        self.label_entry: Gtk.Entry | None = None
        if show_label:
            self.label_entry = Gtk.Entry(
                placeholder_text="Optional, shown instead of the action name"
            )
            self.label_entry.set_tooltip_text(
                "Name this step so it is easy to recognize in the list"
            )
            self.label_entry.connect("changed", self._on_step_label_changed)
            self.append(_row("Step name", self.label_entry))

        self.cat_dd = Gtk.DropDown.new_from_strings(self._cat_names)
        self.append(_row("Category", self.cat_dd))
        self.action_dd = Gtk.DropDown.new_from_strings([])
        self.action_dd.set_hexpand(True)
        # The dropdowns need the category to be known before the action can be
        # found. This searches every action at once, by what it does.
        self.action_search = Gtk.Button.new_from_icon_name("edit-find-symbolic")
        self.action_search.set_tooltip_text("Search all actions")
        self.action_search.connect("clicked", self._open_action_picker)
        action_box = Gtk.Box(spacing=6)
        action_box.append(self.action_dd)
        action_box.append(self.action_search)
        self.append(_row("Action", action_box))
        self.desc = Gtk.Label(wrap=True, xalign=0)
        self.desc.add_css_class("dim-label")
        self.append(self.desc)
        self.params_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.append(self.params_box)

        self.cat_dd.connect("notify::selected", self._on_category_changed)
        self.action_dd.connect("notify::selected", self._on_action_changed)

    # ---------- load / read ----------

    def load(self, step: ActionStep) -> None:
        self._building = True
        if self.label_entry is not None:
            self.label_entry.set_text(step.label)
        action = registry.get(step.action)
        if action is not None and action.category in self._cat_names:
            self.cat_dd.set_selected(self._cat_names.index(action.category))
        else:
            self.cat_dd.set_selected(0)
        self._building = False
        self._populate_actions(preselect=step.action, params=step.params)

    def get_step(self) -> ActionStep:
        action = self._current_action()
        if action is None:
            return ActionStep()
        params = {
            name: self._widget_value(param, widget)
            for name, (param, widget) in self._param_widgets.items()
        }
        return ActionStep(action=action.id, params=params, label=self.step_label())

    def step_label(self) -> str:
        return self.label_entry.get_text().strip() if self.label_entry else ""

    def action_name(self) -> str:
        a = self._current_action()
        return a.name if a else "No action"

    def display_name(self) -> str:
        """What identifies this step in a list: its name, or its action."""
        return self.step_label() or self.action_name()

    def _on_step_label_changed(self, *_a) -> None:
        # Renaming updates the list entry as it is typed.
        if not self._building and self._on_change:
            self._on_change()

    # ---------- copy / paste ----------

    def enable_context_menu(self) -> None:
        """Offer copy/paste on a step that is not part of a list.

        A single-action key has no row to right-click, so the menu goes on the
        editor itself. Inside a StepList the row owns the menu instead, which is
        why this is opt-in rather than always installed.
        """
        gesture = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        gesture.connect("pressed", self._on_context_menu)
        self.add_controller(gesture)

    def _on_context_menu(self, gesture, _n_press: int, x: float, y: float) -> None:
        self._menu.show(gesture.get_widget(), x, y, self.menu_items())

    def menu_items(self) -> list[tuple[str, bool, object]]:
        return [
            ("Copy action", self._current_action() is not None, self.copy_step),
            ("Paste action", STEP_CLIPBOARD.has_step(), self.paste_step),
        ]

    def copy_step(self) -> None:
        STEP_CLIPBOARD.set(self.get_step())
        _report_copied(self.app, self.display_name())

    def paste_step(self) -> None:
        """Replace this step with the copied one. `load` reports the change."""
        step = STEP_CLIPBOARD.get()
        if step is not None:
            self.load(step)
            _report_pasted(self.app, self.display_name())

    # ---------- selection ----------

    def _open_action_picker(self, _button) -> None:
        from .action_picker import ActionPickerDialog

        current = self._current_action()
        ActionPickerDialog(
            self.get_root(),
            self.select_action,
            current.id if current is not None else "",
        ).present()

    def select_action(self, action_id: str) -> None:
        """Point both dropdowns at one action, wherever its category is."""
        action = registry.get(action_id)
        if action is None or action.category not in self._cat_names:
            return
        self._building = True
        self.cat_dd.set_selected(self._cat_names.index(action.category))
        self._building = False
        # Rebuilds the parameter widgets and notifies the change, exactly as
        # picking it through the dropdowns would.
        self._populate_actions(preselect=action_id)

    def _on_category_changed(self, *_a) -> None:
        if not self._building:
            self._populate_actions()

    def _on_action_changed(self, *_a) -> None:
        if not self._building:
            self._build_params({})
            if self._on_change:
                self._on_change()

    def _current_category(self) -> str:
        i = self.cat_dd.get_selected()
        return self._cat_names[i] if i != Gtk.INVALID_LIST_POSITION else ""

    def _current_action(self) -> Action | None:
        i = self.action_dd.get_selected()
        acts = registry.by_category().get(self._current_category(), [])
        return acts[i] if 0 <= i < len(acts) else None

    def _populate_actions(self, preselect: str = "", params: dict | None = None) -> None:
        self._building = True
        acts = registry.by_category().get(self._current_category(), [])
        self.action_dd.set_model(Gtk.StringList.new([a.name for a in acts]))
        sel = next((i for i, a in enumerate(acts) if a.id == preselect), 0)
        self.action_dd.set_selected(sel)
        self._building = False
        self._build_params(params or {})
        if self._on_change:
            self._on_change()

    # ---------- dynamic parameters ----------

    def _build_params(self, values: dict) -> None:
        _clear(self.params_box)
        self._param_widgets.clear()
        self._param_rows.clear()
        action = self._current_action()
        if action is None:
            self.desc.set_visible(False)
            return
        self.desc.set_label(action.description)
        self.desc.set_visible(bool(action.description))
        for param in action.params:
            widget = self._param_widget(param, values.get(param.name))
            row = _row(param.label, widget)
            self._param_widgets[param.name] = (param, widget)
            self._param_rows[param.name] = row
            self.params_box.append(row)

    def _param_widget(
        self, param: Param, value, keep_unknown: bool = True
    ) -> Gtk.Widget:
        if param.kind == "choice":
            labels, stored = _labelled_choices(param)
            dd = self._choice_dd(
                labels, _label_for(stored, value or param.default)
            )
            # Same contract as a dynamic dropdown: the label is what is read,
            # the map is what _widget_value gives back.
            dd._value_map = stored
            if param.name == "preset":
                # Picking a preset writes its shortcut into the editable field,
                # which the user is then free to change.
                dd.connect("notify::selected", self._on_preset_changed)
            return dd
        if param.kind in ("duration", "optional_duration"):
            return self._duration_entry(
                value if value is not None else param.default,
                optional=param.kind == "optional_duration",
            )
        if param.kind == "file":
            return _FileEntry(param, value)
        if param.kind in ("int", "float"):
            digits = 0 if param.kind == "int" else 1
            lower = param.minimum if param.minimum is not None else -100000
            upper = param.maximum if param.maximum is not None else 100000
            adj = Gtk.Adjustment(
                value=float(value if value is not None else param.default or 0),
                lower=lower,
                upper=upper,
                step_increment=param.step,
                page_increment=param.step * 10,
            )
            return Gtk.SpinButton(adjustment=adj, digits=digits)
        if param.choices_source:
            options = self._fetch_choices(param.choices_source)
            # A value already stored on the key stays selectable even when OBS
            # no longer reports it, so opening a key never silently drops it.
            if keep_unknown and value and value not in options:
                options.insert(0, value)
            if options or self._choices_known(param.choices_source):
                labels, values = _display_options(param.choices_source, options)
                dd = self._choice_dd(labels, _label_for(values, value))
                # What the user reads is not always what OBS needs; remember the
                # mapping so _widget_value can give back the stored value.
                dd._value_map = values
                if param.name == "scene":
                    dd.connect("notify::selected", self._on_scene_changed)
                elif param.name == "source":
                    dd.connect("notify::selected", self._on_source_changed)
                return dd
        entry = Gtk.Entry(
            text=str(value if value is not None else param.default or "")
        )
        if param.completion_source:
            self._attach_completion(entry, param.completion_source)
        return entry

    def _attach_completion(self, entry: Gtk.Entry, source: str) -> None:
        """Give a text field live suggestions, when its source can answer.

        Held on the entry so it lives exactly as long as the widget does: the
        popover is parented to it, and a rebuild of the parameter widgets has
        to take the suggestions with it.
        """
        search = self._completion_search(source)
        if search is None:
            return
        entry._completion = _CompletionPopup(
            entry, search, self._completion_artwork(source)
        )

    def _completion_search(self, source: str):
        """The blocking search behind a suggestion list, or None when there is
        no way to answer it right now."""
        if source == "twitch_categories":
            twitch = getattr(self.app, "twitch", None)
            if twitch is None or not twitch.linked:
                # Without an account there is nothing to search; the field
                # stays a plain text box rather than one that never suggests.
                return None
            return lambda text: twitch.search_categories(text, COMPLETION_LIMIT)
        return None

    def _completion_artwork(self, source: str):
        """How a suggestion's picture is fetched, when it has one."""
        if source == "twitch_categories":
            twitch = getattr(self.app, "twitch", None)
            fetch = getattr(twitch, "box_art", None)
            return fetch if callable(fetch) else None
        return None

    def _choices_known(self, source: str) -> bool:
        """Whether an empty option list is an answer or just missing data.

        With OBS connected, "no options" really means there is nothing to pick —
        an empty scene has no sources — and the parameter stays a dropdown that
        fills in as soon as its parent changes. With OBS unreachable the list
        says nothing at all, so it falls back to a text field that can still be
        filled in by hand.
        """
        return source in LOCAL_CHOICE_SOURCES or bool(self.app.obs.connected)

    # When the SCENE changes, the source list is repopulated (and, in cascade,
    # the filter list). When the SOURCE changes, only the filter list is
    # repopulated. The dropdown that triggered the change is never repopulated,
    # and a rebuilt dropdown only connects its handler after its selection is
    # set, so a signal loop is impossible.

    def _on_scene_changed(self, *_a) -> None:
        if self._building:
            return
        log.debug("[editor] scene changed → repopulating sources/filters")
        self._repopulate("sources_in_scene")
        self._repopulate("audio_sources_in_scene")
        self._repopulate("filters_of_source")
        if self._on_change:
            self._on_change()

    def _on_preset_changed(self, *_a) -> None:
        """Fill the shortcut field from the chosen preset."""
        if self._building:
            return
        from ..core.keystrokes import PRESET_SHORTCUTS

        entry = self._param_widgets.get("shortcut")
        preset = self._param_widgets.get("preset")
        if entry is None or preset is None:
            return
        label = self._widget_value(preset[0], preset[1])
        shortcut = PRESET_SHORTCUTS.get(str(label or ""), "")
        if shortcut and isinstance(entry[1], Gtk.Entry):
            entry[1].set_text(shortcut)
        if self._on_change:
            self._on_change()

    def _on_source_changed(self, *_a) -> None:
        if self._building:
            return
        log.debug("[editor] source changed → repopulating filters")
        self._repopulate("filters_of_source")
        if self._on_change:
            self._on_change()

    def _repopulate(self, choices_source: str) -> None:
        """Rebuild the parameters whose options depend on another parameter.

        The widget itself may have to change kind, not just its contents: a
        dependent list that was empty when the step was built is a plain text
        field, and it has to become a dropdown as soon as its parent offers
        options. Updating only the model left that field empty for good, so
        picking a scene whose sources appeared later showed nothing.
        """
        for name, (param, widget) in list(self._param_widgets.items()):
            row = self._param_rows.get(name)
            if param.choices_source != choices_source or row is None:
                continue
            # A value that belonged to the previous parent is not an option of
            # the new one, so it is deliberately not carried over.
            current = self._widget_value(param, widget)
            replacement = self._param_widget(param, current, keep_unknown=False)
            row.remove(widget)
            row.append(replacement)
            self._param_widgets[name] = (param, replacement)

    def _fetch_choices(self, source: str) -> list[str]:
        import threading
        import time as _time
        obs = self.app.obs
        t0 = _time.time()
        try:
            if source == "pages":
                return [p.name for p in self.app.config.pages]
            if source == "deck_profiles":
                return [p.name for p in self.app.config.profiles]
            if source == "applications":
                from ..core.apps import application_choices

                return application_choices()
            if not obs.connected:
                log.debug("[editor] _fetch_choices(%s): OBS disconnected", source)
                return []
            table = {
                "scenes": obs.get_scenes,
                "inputs": obs.get_inputs,
                "media_inputs": obs.get_media_inputs,
                "text_inputs": obs.get_text_inputs,
                "browser_inputs": obs.get_browser_inputs,
                "transitions": obs.get_transitions,
                "scene_collections": obs.get_scene_collections,
                "profiles": obs.get_profiles,
                "hotkeys": obs.get_hotkeys,
            }
            if source in table:
                result = table[source]()
            elif source == "sources_in_scene":
                result = obs.get_sources_in_scene(self._sibling_value("scene"))
            elif source == "audio_sources_in_scene":
                result = obs.get_audio_sources_in_scene(
                    self._sibling_value("scene")
                )
            elif source == "filters_of_source":
                src = self._sibling_value("source")
                result = obs.get_filters_of_source(src) if src else []
            else:
                result = []
            log.debug(
                "[editor] _fetch_choices(%s) → %d options in %.0f ms [thread %s]",
                source, len(result), (_time.time() - t0) * 1000,
                threading.current_thread().name,
            )
            return result
        except Exception:
            log.debug("Could not fetch options for %s", source, exc_info=True)
            return []

    def _sibling_value(self, name: str) -> str:
        pair = self._param_widgets.get(name)
        return str(self._widget_value(*pair)) if pair else ""

    # ---------- utilities ----------

    @staticmethod
    def _widget_value(param: Param, widget: Gtk.Widget):
        if param.kind in ("duration", "optional_duration"):
            if param.kind == "optional_duration" and not widget.get_text().strip():
                return ""
            return format_duration(parse_duration(widget.get_text()))
        if isinstance(widget, _FileEntry):
            return widget.get_text()
        if isinstance(widget, Gtk.SpinButton):
            return int(widget.get_value()) if param.kind == "int" else widget.get_value()
        if isinstance(widget, Gtk.DropDown):
            item = widget.get_selected_item()
            label = item.get_string() if item is not None else ""
            # Sources with readable labels store the underlying value instead.
            return getattr(widget, "_value_map", {}).get(label, label)
        if isinstance(widget, Gtk.Entry):
            completion = getattr(widget, "_completion", None)
            if completion is not None:
                # A field with suggestions stores only a value the service
                # recognises. Text that was typed and never matched is saved
                # as nothing, so the key plainly has no category rather than
                # one that will fail the first time it is pressed on air.
                return completion.settled_value()
            return widget.get_text()
        return ""

    @staticmethod
    def _choice_dd(options: list[str], value) -> Gtk.DropDown:
        dd = Gtk.DropDown.new_from_strings(options)
        if value in options:
            dd.set_selected(options.index(value))
        return dd

    @staticmethod
    def _duration_entry(value, optional: bool = False) -> Gtk.Entry:
        """Small 'MM:SS' time field that normalizes itself when edited."""
        text = str(value or "").strip()
        entry = Gtk.Entry(
            text="" if optional and not text else format_duration(parse_duration(text)),
            placeholder_text="MM:SS (full file)" if optional else "MM:SS",
            max_width_chars=16 if optional else 6,
            width_chars=16 if optional else 6,
            xalign=0.5, halign=Gtk.Align.START,
        )

        def _normalize(*_a):
            current = entry.get_text().strip()
            if optional and not current:
                return
            entry.set_text(format_duration(parse_duration(current)))

        entry.connect("activate", _normalize)
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", _normalize)
        entry.add_controller(focus)
        return entry


# ============================ step list =============================

class StepList(Gtk.Box):
    """Reorderable list of StepEditor with add / up / down / remove."""

    def __init__(self, app, on_change=None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.app = app
        self._on_change = on_change
        self._editors: list[StepEditor] = []
        self._drag_step: int | None = None
        self._drop_step: int | None = None
        self._menu = _ContextMenu()
        self._row_menu_click = False
        self._menu_row: Gtk.Expander | None = None

        self._list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.append(self._list_box)
        add = Gtk.Button(label="Add action", halign=Gtk.Align.START)
        add.set_icon_name("list-add-symbolic")
        add.connect("clicked", lambda _b: self._add(ActionStep(), expand=True))
        self.append(add)

        # Right-clicking the list itself, rather than one of its rows, pastes at
        # the end.
        menu = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        menu.connect("pressed", self._on_list_menu)
        self.add_controller(menu)

    def load(self, steps: list[ActionStep]) -> None:
        self._editors.clear()
        for step in steps:
            self._add(step, expand=False, rebuild=False)
        self._rebuild()
        self._notify_change()

    def get_steps(self) -> list[ActionStep]:
        return [s for ed in self._editors if (s := ed.get_step()).action]

    @staticmethod
    def _delete_icon() -> Gtk.DrawingArea:
        icon = Gtk.DrawingArea()
        icon.set_content_width(16)
        icon.set_content_height(16)

        def draw(widget, context, width, height) -> None:
            color = widget.get_color()
            context.set_source_rgba(color.red, color.green, color.blue, color.alpha)
            context.translate((width - 16) / 2, (height - 16) / 2)
            context.rectangle(6, 1, 4, 2)
            context.rectangle(2, 3, 12, 2)
            context.move_to(4, 6)
            context.line_to(12, 6)
            context.line_to(11, 15)
            context.line_to(5, 15)
            context.close_path()
            context.fill()

        icon.set_draw_func(draw)
        return icon

    def _add(
        self,
        step: ActionStep,
        expand: bool,
        rebuild: bool = True,
        position: int | None = None,
    ) -> None:
        """Append a step, or insert it at `position`, pushing that row down."""
        editor = StepEditor(self.app, show_label=True)
        editor.load(step)
        editor._on_change = self._editors_changed
        editor._want_expand = expand  # type: ignore[attr-defined]
        if position is None:
            self._editors.append(editor)
        else:
            self._editors.insert(max(0, min(position, len(self._editors))), editor)
        if rebuild:
            self._rebuild()
            self._notify_change()
            if expand:
                self._reveal(editor)

    # --- copy / paste ---

    def _copy(self, index: int) -> None:
        """Put a step on the shared clipboard; nothing is inserted yet."""
        if not 0 <= index < len(self._editors):
            return
        self._editors[index].copy_step()

    def _paste(self, index: int | None) -> None:
        """Insert the copied step at `index`, or at the end when it is None."""
        step = STEP_CLIPBOARD.get()
        if step is None:
            return
        position = (
            len(self._editors) if index is None
            else max(0, min(index, len(self._editors)))
        )
        # Collapsed like every other row: the copy is already configured, so
        # opening it would only push the rest of the list out of view.
        self._add(step, expand=False, position=position)
        self._reveal(self._editors[position])
        _report_pasted(self.app, self._editors[position].display_name())

    def _rebuild(self) -> None:
        # A menu is parented to a row that is about to be destroyed.
        self._menu.close()
        # Rebuilding recreates every expander, so what the user opened or closed
        # by hand has to be read back first. Without this, adding a step
        # restored the state each row had when it was created, silently
        # reopening rows the user had collapsed.
        self._remember_expansion()
        # Every editor is about to be wrapped in a fresh expander, so detach it
        # from the old one explicitly. Letting that expander be garbage
        # collected instead only works while nothing else holds it, and a
        # pending reveal does exactly that.
        for editor in self._editors:
            if (parent := editor.get_parent()) is not None:
                parent.remove(editor)
        _clear(self._list_box)
        for i, editor in enumerate(self._editors):
            self._list_box.append(self._wrap(i, editor))

    def _remember_expansion(self) -> None:
        for editor in self._editors:
            expander = getattr(editor, "_expander", None)
            if expander is not None:
                editor._want_expand = expander.get_expanded()

    def _reveal(self, editor: StepEditor) -> None:
        """Scroll a newly added step into view once the layout has settled."""
        GLib.idle_add(
            self._scroll_into_view, editor, priority=GLib.PRIORITY_LOW
        )

    def _rebuild_keeping_scroll(self) -> None:
        """Rebuild without losing the reader's place.

        Clearing the list collapses its height, so the scrolled window clamps
        its adjustment to zero and reordering a step jumped back to the top.
        The offset is restored on a low-priority idle, after the new rows have
        been measured.
        """
        offset = self._scroll_offset()
        self._rebuild()
        if offset is not None:
            GLib.idle_add(
                self._apply_scroll, offset, priority=GLib.PRIORITY_LOW
            )

    def _adjustment(self) -> Gtk.Adjustment | None:
        scroller = self.get_ancestor(Gtk.ScrolledWindow)
        return scroller.get_vadjustment() if scroller is not None else None

    def _scroll_offset(self) -> float | None:
        adjustment = self._adjustment()
        return adjustment.get_value() if adjustment is not None else None

    def _apply_scroll(self, offset: float) -> bool:
        adjustment = self._adjustment()
        if adjustment is None:
            return False
        adjustment.set_value(
            max(
                adjustment.get_lower(),
                min(offset, adjustment.get_upper() - adjustment.get_page_size()),
            )
        )
        return False

    def _scroll_into_view(self, editor: StepEditor) -> bool:
        # Resolved late on purpose: another rebuild in the meantime gives the
        # step a different expander, and that is the one to scroll to.
        widget = getattr(editor, "_expander", None)
        scroller = self.get_ancestor(Gtk.ScrolledWindow)
        if widget is None or scroller is None:
            return False
        found, bounds = widget.compute_bounds(scroller)
        if not found:
            return False
        # compute_bounds is relative to the visible area, so the offset adds to
        # where the view already is.
        offset = self._scroll_offset()
        if offset is None:
            return False
        return self._apply_scroll(offset + bounds.get_y())

    def _wrap(self, i: int, editor: StepEditor) -> Gtk.Expander:
        exp = Gtk.Expander()
        exp.set_expanded(getattr(editor, "_want_expand", False))
        editor._expander = exp  # type: ignore[attr-defined]
        # The controls live in the expander's own title, so reordering, copying
        # or removing a step never means opening it first.
        exp.set_label_widget(self._header(i, editor))
        exp.add_css_class("step-row")
        self._disable_hover_expand(exp)
        self._add_row_dnd(exp, i)
        self._add_row_menu(exp, i)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                          margin_start=8, margin_top=4, margin_bottom=6)
        content.append(editor)
        exp.set_child(content)
        return exp

    def _header(self, i: int, editor: StepEditor) -> Gtk.Box:
        header = Gtk.Box(spacing=4, hexpand=True)
        handle = Gtk.Image.new_from_icon_name("list-drag-handle-symbolic")
        handle.set_tooltip_text("Drag to reorder")
        handle.add_css_class("dim-label")
        header.append(handle)

        label = Gtk.Label(
            label=f"{i + 1}. {editor.display_name()}",
            xalign=0,
            hexpand=True,
            ellipsize=Pango.EllipsizeMode.END,
        )
        editor._title = label  # type: ignore[attr-defined]
        header.append(label)

        up = Gtk.Button.new_from_icon_name("go-up-symbolic")
        up.set_tooltip_text("Move up")
        up.set_sensitive(i > 0)
        up.connect("clicked", lambda _b: self._move(i, -1))
        down = Gtk.Button.new_from_icon_name("go-down-symbolic")
        down.set_tooltip_text("Move down")
        down.set_sensitive(i < len(self._editors) - 1)
        down.connect("clicked", lambda _b: self._move(i, +1))
        copy = Gtk.Button.new_from_icon_name("edit-copy-symbolic")
        copy.set_tooltip_text(
            "Copy this action; right-click any action list to paste it"
        )
        copy.connect("clicked", lambda _b: self._copy(i))
        delete = Gtk.Button(child=self._delete_icon())
        delete.set_tooltip_text("Remove action")
        # Flat like its neighbours, but red: "destructive-action" paints a solid
        # red background that a flat button drops, which left the icon in the
        # normal text color.
        delete.add_css_class("step-remove")
        delete.connect("clicked", lambda _b: self._delete(i))
        for button in (up, down, copy, delete):
            button.add_css_class("flat")
            header.append(button)
        # Kept so focus can be handed to the replacement after a rebuild.
        editor._controls = {  # type: ignore[attr-defined]
            "up": up, "down": down, "copy": copy, "delete": delete,
        }
        return header

    @staticmethod
    def _disable_hover_expand(row: Gtk.Expander) -> None:
        """Stop a row from opening itself while a step is dragged over it.

        GtkExpander installs its own GtkDropControllerMotion that expands after
        a short hover during any drag, which is meant for tree views. Here the
        drag is a reorder, so the row underneath must stay exactly as it is.
        """
        for controller in list(row.observe_controllers()):
            if isinstance(controller, Gtk.DropControllerMotion):
                row.remove_controller(controller)

    # --- context menu (right-click) ---

    def _add_row_menu(self, row: Gtk.Expander, i: int) -> None:
        gesture = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        gesture.connect("pressed", self._on_row_menu, i)
        row.add_controller(gesture)

    def _on_row_menu(self, gesture, _n_press: int, x: float, y: float, i: int) -> None:
        """Copy or paste onto one row.

        A right click inside a parameter entry never gets here: the entry claims
        it first and shows its own text menu, which is what it should do.
        """
        # Claiming should already deny the list's gesture, one step further up
        # the bubble chain. `_row_menu_click` does not depend on that: bubble
        # delivery reaches this row before the list, and both handlers run in
        # the same event delivery, so the flag is set before the list reads it.
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self._row_menu_click = True
        GLib.idle_add(self._clear_row_menu_click)
        row = gesture.get_widget()
        # Highlighted until the menu goes away, so it is never in doubt which
        # row the entries act on — the menu itself covers part of the list.
        self._set_menu_row(row)
        self._menu.show(
            row, x, y, self.row_menu_items(i),
            on_close=lambda: self._set_menu_row(None),
        )

    def _set_menu_row(self, row: Gtk.Expander | None) -> None:
        # The row is held as a widget, not an index: a menu entry may reorder
        # the list, and the highlight must come off whatever it went on.
        if self._menu_row is not None:
            self._menu_row.remove_css_class("menu-target")
        self._menu_row = row
        if row is not None:
            row.add_css_class("menu-target")

    def _clear_row_menu_click(self) -> bool:
        self._row_menu_click = False
        return False

    def _on_list_menu(self, gesture, _n_press: int, x: float, y: float) -> None:
        """Right-clicking the list beside its rows pastes at the end."""
        if self._row_menu_click:
            # A row already answered this very click; replacing its menu with
            # the paste-at-the-end one would drop the copy entry.
            return
        self._menu.show(gesture.get_widget(), x, y, self.list_menu_items())

    def row_menu_items(self, i: int) -> list:
        """Everything right-clicking row `i` offers, `None` being a separator."""
        last = len(self._editors) - 1
        return [
            ("Copy action", 0 <= i <= last, lambda: self._copy(i)),
            ("Paste action", STEP_CLIPBOARD.has_step(), lambda: self._paste(i)),
            None,
            ("Move up", i > 0, lambda: self._move(i, -1)),
            ("Move down", 0 <= i < last, lambda: self._move(i, +1)),
            ("Move to top", i > 0, lambda: self._reorder(i, 0)),
            ("Move to bottom", 0 <= i < last, lambda: self._reorder(i, last)),
            None,
            ("Remove action", 0 <= i <= last, lambda: self._delete(i)),
        ]

    def list_menu_items(self) -> list:
        """What right-clicking the list itself offers: paste at the end."""
        return [
            ("Paste action", STEP_CLIPBOARD.has_step(), lambda: self._paste(None)),
        ]

    # --- drag & drop reordering ---

    def _add_row_dnd(self, row: Gtk.Expander, i: int) -> None:
        """One source/target pair per row, in the bubble phase.

        Bubble rather than capture, so the buttons in the title and the
        expander's own toggle keep their clicks; only a real drag reorders.
        """
        drag = Gtk.DragSource()
        drag.set_actions(Gdk.DragAction.MOVE)
        drag.set_button(Gdk.BUTTON_PRIMARY)
        drag.connect("prepare", self._on_step_drag_prepare, i)
        drag.connect("drag-begin", self._on_step_drag_begin, i)
        drag.connect("drag-end", lambda *_a: self._clear_step_drag_feedback())
        row.add_controller(drag)

        drop = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        drop.set_preload(True)
        drop.connect("enter", self._on_step_drag_motion, i)
        drop.connect("motion", self._on_step_drag_motion, i)
        drop.connect("leave", lambda *_a: self._set_step_drop_row(None))
        drop.connect("drop", self._on_step_drop, i)
        row.add_controller(drop)

    def _on_step_drag_prepare(self, _source, _x, _y, i: int):
        self._drag_step = i
        return Gdk.ContentProvider.new_for_value(
            GObject.Value(GObject.TYPE_STRING, f"{_STEP_DRAG_PREFIX}{i}")
        )

    def _on_step_drag_begin(self, source, _drag, i: int) -> None:
        if 0 <= i < len(self._editors):
            expander = getattr(self._editors[i], "_expander", None)
            if expander is not None:
                expander.add_css_class("drag-source")
                source.set_icon(Gtk.WidgetPaintable.new(expander), 0, 0)

    def _on_step_drag_motion(self, _target, *args):
        destination = args[-1]
        if self._drag_step is None or destination == self._drag_step:
            self._set_step_drop_row(None)
            return Gdk.DragAction(0)
        self._set_step_drop_row(destination)
        return Gdk.DragAction.MOVE

    def _on_step_drop(self, _target, value, _x, _y, destination: int) -> bool:
        if isinstance(value, GObject.Value):
            value = value.get_string()
        source = self._decode_step_drag(value)
        self._set_step_drop_row(None)
        if (
            source is None
            or source != self._drag_step
            or source == destination
            or not 0 <= destination < len(self._editors)
        ):
            log.debug(
                "Rejected step drop: source=%r active=%r destination=%r",
                source, self._drag_step, destination,
            )
            return False
        self._reorder(source, destination)
        return True

    @staticmethod
    def _decode_step_drag(value) -> int | None:
        if not isinstance(value, str) or not value.startswith(_STEP_DRAG_PREFIX):
            return None
        try:
            return int(value.removeprefix(_STEP_DRAG_PREFIX))
        except ValueError:
            return None

    def _set_step_drop_row(self, index: int | None) -> None:
        if self._drop_step == index:
            return
        for position in (self._drop_step, index):
            if position is None or not 0 <= position < len(self._editors):
                continue
            expander = getattr(self._editors[position], "_expander", None)
            if expander is not None:
                if position == index:
                    expander.add_css_class("drop-target")
                else:
                    expander.remove_css_class("drop-target")
        self._drop_step = index

    def _clear_step_drag_feedback(self) -> None:
        self._set_step_drop_row(None)
        if self._drag_step is not None and 0 <= self._drag_step < len(self._editors):
            expander = getattr(self._editors[self._drag_step], "_expander", None)
            if expander is not None:
                expander.remove_css_class("drag-source")
        self._drag_step = None

    def _reorder(self, source: int, destination: int) -> None:
        """Move one step to another position, rather than swapping two."""
        if source == destination or not 0 <= source < len(self._editors):
            return
        if not 0 <= destination < len(self._editors):
            return
        self._remember_expansion()
        editor = self._editors.pop(source)
        self._editors.insert(destination, editor)
        self._rebuild_keeping_scroll()
        self._notify_change()

    def _move(self, i: int, delta: int) -> None:
        j = i + delta
        if 0 <= j < len(self._editors):
            # Reordering deliberately changes nothing else: each step keeps the
            # open or closed state the user gave it.
            self._remember_expansion()
            self._editors[i], self._editors[j] = self._editors[j], self._editors[i]
            self._rebuild_keeping_scroll()
            self._notify_change()
            # The rebuild destroyed the arrow that was just clicked. A viewport
            # scrolls to whatever takes focus next, which is what threw the list
            # back to the top, so focus goes to the same arrow of the moved row.
            self._focus_control(j, "down" if delta > 0 else "up")

    def _focus_control(self, index: int, name: str) -> None:
        if not 0 <= index < len(self._editors):
            return
        button = getattr(self._editors[index], "_controls", {}).get(name)
        if button is None:
            return

        def take_focus() -> bool:
            # Wrapped rather than handed to idle_add directly: grab_focus
            # answers True when it succeeds, and a source that returns True is
            # kept, so the focus grab would repeat for the rest of the session.
            button.grab_focus()
            return False

        # After the pending layout, so the new row can actually take it.
        GLib.idle_add(take_focus, priority=GLib.PRIORITY_LOW)

    def _delete(self, i: int) -> None:
        if not 0 <= i < len(self._editors):
            return
        del self._editors[i]
        self._rebuild_keeping_scroll()
        self._notify_change()

    def _editors_changed(self) -> None:
        for i, editor in enumerate(self._editors):
            title = getattr(editor, "_title", None)
            if title is not None:
                title.set_text(f"{i + 1}. {editor.display_name()}")
        self._notify_change()

    def step_title(self, index: int) -> str:
        """The text shown on one row: its position and name."""
        title = getattr(self._editors[index], "_title", None)
        return title.get_text() if title is not None else ""

    def _notify_change(self) -> None:
        if self._on_change is not None:
            self._on_change()


# ============================= appearance ==============================

class AppearanceBox(Gtk.Box):
    PREVIEW = 40

    def __init__(self, title: str = "Appearance") -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._icon_ref = ""      # "mdi:name" or a file path, or ""
        self._fallback_icon_ref = ""

        heading = Gtk.Label(label=title, xalign=0)
        heading.add_css_class("heading")
        self.append(heading)

        self.label_entry = Gtk.Entry(placeholder_text="Key label")
        self.append(_row("Label", self.label_entry))

        self.font_size_dd = Gtk.DropDown.new_from_strings(
            [name for _, name in KEY_FONT_SIZE_CHOICES]
        )
        self.font_size_dd.set_tooltip_text(
            "Size of the label text drawn on the key"
        )
        self.append(_row("Font size", self.font_size_dd))

        # preview + library / file / remove buttons
        icon_box = Gtk.Box(spacing=6)
        self.preview = Gtk.Image()
        self.preview.set_pixel_size(self.PREVIEW)
        self.preview.set_size_request(self.PREVIEW, self.PREVIEW)
        self.preview.add_css_class("card")
        icon_box.append(self.preview)
        lib_btn = Gtk.Button(label="Library…", hexpand=True)
        lib_btn.set_tooltip_text("Choose from the built-in icon library")
        lib_btn.connect("clicked", self._pick_from_library)
        file_btn = Gtk.Button.new_from_icon_name("document-open-symbolic")
        file_btn.set_tooltip_text("Use your own image")
        file_btn.connect("clicked", self._pick_file)
        clear_btn = Gtk.Button.new_from_icon_name("edit-clear-symbolic")
        clear_btn.set_tooltip_text("Use the action's default icon")
        clear_btn.connect("clicked", lambda _b: self._set_icon(""))
        for b in (lib_btn, file_btn, clear_btn):
            icon_box.append(b)
        self.append(_row("Icon", icon_box))

        self.color_btn = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog())
        self.append(_row("Background color", self.color_btn))

        # Text colour keeps the icon's inheritance shape rather than the
        # background's: an empty value means "whatever the renderer uses", so
        # the button shows that colour while the key stores nothing, and the
        # clear button gives it back.
        self._text_color_ref = KEY_TEXT_COLOR_AUTO
        self.text_color_btn = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog())
        self.text_color_btn.set_hexpand(True)
        self._text_color_handler = self.text_color_btn.connect(
            "notify::rgba", self._on_text_color_picked
        )
        text_box = Gtk.Box(spacing=6)
        text_box.append(self.text_color_btn)
        text_clear = Gtk.Button.new_from_icon_name("edit-clear-symbolic")
        text_clear.set_tooltip_text("Use the default text color")
        text_clear.connect(
            "clicked", lambda _b: self._set_text_color(KEY_TEXT_COLOR_AUTO)
        )
        text_box.append(text_clear)
        self.append(_row("Text color", text_box))

    def load(
        self,
        label: str,
        icon: str,
        color: str,
        fallback_icon: str = "",
        font_size: str = KEY_FONT_SIZE_AUTO,
        text_color: str = KEY_TEXT_COLOR_AUTO,
    ) -> None:
        self.label_entry.set_text(label)
        self._fallback_icon_ref = fallback_icon
        self._set_icon(icon)
        rgba = Gdk.RGBA()
        rgba.parse(color or DEFAULT_KEY_BG)
        self.color_btn.set_rgba(rgba)
        self.font_size_dd.set_selected(self._font_size_index(font_size))
        self._set_text_color(text_color)

    def label(self) -> str:
        return self.label_entry.get_text()

    def font_size(self) -> str:
        index = self.font_size_dd.get_selected()
        if index == Gtk.INVALID_LIST_POSITION or index >= len(KEY_FONT_SIZE_CHOICES):
            return KEY_FONT_SIZE_AUTO
        return KEY_FONT_SIZE_CHOICES[index][0]

    @staticmethod
    def _font_size_index(value: str) -> int:
        for position, (size, _name) in enumerate(KEY_FONT_SIZE_CHOICES):
            if size == value:
                return position
        return 0

    def icon(self) -> str:
        return self._icon_ref

    def set_fallback_icon(self, ref: str) -> None:
        self._fallback_icon_ref = ref
        if not self._icon_ref:
            self._set_icon("")

    def color(self) -> str:
        return rgba_to_hex(self.color_btn.get_rgba())

    def text_color(self) -> str:
        return self._text_color_ref

    # ---------- text color ----------

    def _set_text_color(self, value: str) -> None:
        """Store the reference and show the colour it resolves to.

        The handler is blocked while the button is updated. Showing the
        inherited default is otherwise indistinguishable from the user picking
        that exact colour, so clearing the choice would immediately write the
        current default back into the key and inheritance would be lost.
        """
        self._text_color_ref = value
        rgba = Gdk.RGBA()
        rgba.parse(value or DEFAULT_TEXT_COLOR)
        self.text_color_btn.handler_block(self._text_color_handler)
        self.text_color_btn.set_rgba(rgba)
        self.text_color_btn.handler_unblock(self._text_color_handler)
        self.text_color_btn.set_tooltip_text(
            "Using the default text color"
            if not value
            else "Color of the label, value and badge drawn on the key"
        )

    def _on_text_color_picked(self, *_args) -> None:
        self._text_color_ref = rgba_to_hex(self.text_color_btn.get_rgba())
        self.text_color_btn.set_tooltip_text(
            "Color of the label, value and badge drawn on the key"
        )

    # ---------- icon selection ----------

    def _set_icon(self, ref: str) -> None:
        self._icon_ref = ref
        effective_ref = ref or self._fallback_icon_ref
        tex = self._preview_texture(effective_ref)
        if tex is not None:
            self.preview.set_from_paintable(tex)
        else:
            self.preview.clear()
        self.preview.set_tooltip_text(
            "Using the action's default icon"
            if not ref and effective_ref
            else None
        )

    @staticmethod
    def _preview_texture(ref: str):
        import io
        from ..core.icons import library
        img = library.render(ref, AppearanceBox.PREVIEW, "#e3e3e8") if ref else None
        if img is None:
            return None
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return Gdk.Texture.new_from_bytes(GLib.Bytes.new(buf.getvalue()))

    def _pick_from_library(self, _btn) -> None:
        from .icon_picker import IconPickerDialog
        IconPickerDialog(self.get_root(), self._set_icon).present()

    def _pick_file(self, _btn) -> None:
        dialog = Gtk.FileDialog(title="Choose image")
        f = Gtk.FileFilter()
        f.set_name("Images")
        for mime in ("image/png", "image/jpeg", "image/webp", "image/bmp"):
            f.add_mime_type(mime)
        dialog.set_default_filter(f)
        dialog.open(self.get_root(), None, self._file_chosen)

    def _file_chosen(self, dialog, result) -> None:
        try:
            file = dialog.open_finish(result)
        except Exception:
            return
        if file is not None:
            self._set_icon(file.get_path() or "")
