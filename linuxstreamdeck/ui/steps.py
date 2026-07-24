"""Reusable components of the key editor:

- StepEditor   : selects category + action + parameters of ONE step.
- StepList     : reorderable list of StepEditor (for multiple actions).
- AppearanceBox: label + icon + background color of one key state.

StepEditor concentrates the logic of filling the dropdowns live from OBS, which
used to live in EditorPanel, so it can be reused for each step.
"""

from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from ..core import actions as registry  # noqa: E402
from ..core.actions import Action, Param, format_duration, parse_duration  # noqa: E402
from ..core.config import DEFAULT_KEY_BG, ActionStep  # noqa: E402

log = logging.getLogger(__name__)

CATEGORY_ORDER = [
    "OBS · Scenes", "OBS · Recording & Streaming", "OBS · Audio",
    "OBS · Sources & Filters", "OBS · Media", "OBS · Advanced",
    "System", "Navigation",
]


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


def _clear(box: Gtk.Box) -> None:
    while (child := box.get_first_child()) is not None:
        box.remove(child)


# =========================== single-step editor ===========================

class StepEditor(Gtk.Box):
    def __init__(self, app, on_change=None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.app = app
        self._on_change = on_change          # called when the chosen action changes
        self._param_widgets: dict[str, tuple[Param, Gtk.Widget]] = {}
        # id of the notify::selected handler of each dependent dropdown, so it
        # can be blocked while repopulating (avoids signal loops)
        self._dep_handlers: dict[str, int] = {}
        self._building = False

        cats = registry.by_category()
        self._cat_names = [c for c in CATEGORY_ORDER if c in cats] + [
            c for c in cats if c not in CATEGORY_ORDER
        ]

        self.cat_dd = Gtk.DropDown.new_from_strings(self._cat_names)
        self.append(_row("Category", self.cat_dd))
        self.action_dd = Gtk.DropDown.new_from_strings([])
        self.append(_row("Action", self.action_dd))
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
        return ActionStep(action=action.id, params=params)

    def action_name(self) -> str:
        a = self._current_action()
        return a.name if a else "No action"

    # ---------- selection ----------

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
        self._dep_handlers.clear()
        action = self._current_action()
        if action is None:
            self.desc.set_visible(False)
            return
        self.desc.set_label(action.description)
        self.desc.set_visible(bool(action.description))
        for param in action.params:
            widget = self._param_widget(param, values.get(param.name))
            self._param_widgets[param.name] = (param, widget)
            self.params_box.append(_row(param.label, widget))

    def _param_widget(self, param: Param, value) -> Gtk.Widget:
        if param.kind == "choice":
            return self._choice_dd(param.choices, value or param.default)
        if param.kind == "duration":
            return self._duration_entry(value if value is not None else param.default)
        if param.kind in ("int", "float"):
            digits = 0 if param.kind == "int" else 1
            adj = Gtk.Adjustment(
                value=float(value if value is not None else param.default or 0),
                lower=-100000, upper=100000, step_increment=1,
            )
            return Gtk.SpinButton(adjustment=adj, digits=digits)
        if param.choices_source:
            options = self._fetch_choices(param.choices_source)
            if options:
                if value and value not in options:
                    options.insert(0, value)
                dd = self._choice_dd(options, value)
                if param.name == "scene":
                    self._dep_handlers["scene"] = dd.connect(
                        "notify::selected", self._on_scene_changed
                    )
                elif param.name == "source":
                    self._dep_handlers["source"] = dd.connect(
                        "notify::selected", self._on_source_changed
                    )
                return dd
        return Gtk.Entry(text=str(value if value is not None else param.default or ""))

    # When the SCENE changes, the source list is repopulated (and, in cascade,
    # the filter list). When the SOURCE changes, only the filter list is
    # repopulated. The dropdown that triggered the change is never repopulated,
    # and its handler is blocked during repopulation: a signal loop is impossible.

    def _on_scene_changed(self, *_a) -> None:
        if self._building:
            return
        log.debug("[editor] scene changed → repopulating sources/filters")
        self._repopulate("sources_in_scene")
        self._repopulate("filters_of_source")
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
        for name, (param, widget) in list(self._param_widgets.items()):
            if param.choices_source != choices_source or not isinstance(widget, Gtk.DropDown):
                continue
            options = self._fetch_choices(choices_source)
            current = self._widget_value(param, widget)
            hid = self._dep_handlers.get(name)
            if hid is not None:
                widget.handler_block(hid)          # avoids reentry via notify::selected
            widget.set_model(Gtk.StringList.new(options))
            if current in options:
                widget.set_selected(options.index(current))
            if hid is not None:
                widget.handler_unblock(hid)

    def _fetch_choices(self, source: str) -> list[str]:
        import threading
        import time as _time
        obs = self.app.obs
        t0 = _time.time()
        try:
            if source == "pages":
                return [p.name for p in self.app.config.pages]
            if not obs.connected:
                log.debug("[editor] _fetch_choices(%s): OBS disconnected", source)
                return []
            table = {
                "scenes": obs.get_scenes,
                "inputs": obs.get_inputs,
                "media_inputs": obs.get_media_inputs,
                "transitions": obs.get_transitions,
                "scene_collections": obs.get_scene_collections,
                "profiles": obs.get_profiles,
                "hotkeys": obs.get_hotkeys,
            }
            if source in table:
                result = table[source]()
            elif source == "sources_in_scene":
                result = obs.get_sources_in_scene(self._sibling_value("scene"))
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
        if param.kind == "duration":
            return format_duration(parse_duration(widget.get_text()))
        if isinstance(widget, Gtk.SpinButton):
            return int(widget.get_value()) if param.kind == "int" else widget.get_value()
        if isinstance(widget, Gtk.DropDown):
            item = widget.get_selected_item()
            return item.get_string() if item is not None else ""
        if isinstance(widget, Gtk.Entry):
            return widget.get_text()
        return ""

    @staticmethod
    def _choice_dd(options: list[str], value) -> Gtk.DropDown:
        dd = Gtk.DropDown.new_from_strings(options)
        if value in options:
            dd.set_selected(options.index(value))
        return dd

    @staticmethod
    def _duration_entry(value) -> Gtk.Entry:
        """Small 'MM:SS' time field that normalizes itself when edited."""
        entry = Gtk.Entry(
            text=format_duration(parse_duration(value)),
            placeholder_text="MM:SS",
            max_width_chars=6, width_chars=6,
            xalign=0.5, halign=Gtk.Align.START,
        )

        def _normalize(*_a):
            entry.set_text(format_duration(parse_duration(entry.get_text())))

        entry.connect("activate", _normalize)
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", _normalize)
        entry.add_controller(focus)
        return entry


# ============================ step list =============================

class StepList(Gtk.Box):
    """Reorderable list of StepEditor with add / up / down / remove."""

    def __init__(self, app) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.app = app
        self._editors: list[StepEditor] = []

        self._list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.append(self._list_box)
        add = Gtk.Button(label="Add action", halign=Gtk.Align.START)
        add.set_icon_name("list-add-symbolic")
        add.connect("clicked", lambda _b: self._add(ActionStep(), expand=True))
        self.append(add)

    def load(self, steps: list[ActionStep]) -> None:
        self._editors.clear()
        for step in steps:
            self._add(step, expand=False, rebuild=False)
        self._rebuild()

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

    def _add(self, step: ActionStep, expand: bool, rebuild: bool = True) -> None:
        editor = StepEditor(self.app, on_change=self._refresh_titles)
        editor.load(step)
        editor._want_expand = expand  # type: ignore[attr-defined]
        self._editors.append(editor)
        if rebuild:
            self._rebuild()

    def _rebuild(self) -> None:
        _clear(self._list_box)
        for i, editor in enumerate(self._editors):
            self._list_box.append(self._wrap(i, editor))

    def _wrap(self, i: int, editor: StepEditor) -> Gtk.Expander:
        exp = Gtk.Expander(label=f"{i + 1}. {editor.action_name()}")
        exp.set_expanded(getattr(editor, "_want_expand", False))
        editor._expander = exp  # type: ignore[attr-defined]

        toolbar = Gtk.Box(spacing=4, margin_bottom=6)
        up = Gtk.Button.new_from_icon_name("go-up-symbolic")
        up.set_tooltip_text("Move up")
        up.set_sensitive(i > 0)
        up.connect("clicked", lambda _b: self._move(i, -1))
        down = Gtk.Button.new_from_icon_name("go-down-symbolic")
        down.set_tooltip_text("Move down")
        down.set_sensitive(i < len(self._editors) - 1)
        down.connect("clicked", lambda _b: self._move(i, +1))
        delete = Gtk.Button(child=self._delete_icon())
        delete.set_tooltip_text("Remove action")
        delete.add_css_class("destructive-action")
        delete.connect("clicked", lambda _b: self._delete(i))
        for b in (up, down, delete):
            toolbar.append(b)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                          margin_start=8, margin_top=4, margin_bottom=6)
        content.append(toolbar)
        content.append(editor)
        exp.set_child(content)
        return exp

    def _move(self, i: int, delta: int) -> None:
        j = i + delta
        if 0 <= j < len(self._editors):
            self._editors[i]._want_expand = True   # type: ignore[attr-defined]
            self._editors[i], self._editors[j] = self._editors[j], self._editors[i]
            self._rebuild()

    def _delete(self, i: int) -> None:
        del self._editors[i]
        self._rebuild()

    def _refresh_titles(self) -> None:
        for i, editor in enumerate(self._editors):
            exp = getattr(editor, "_expander", None)
            if exp is not None:
                exp.set_label(f"{i + 1}. {editor.action_name()}")


# ============================= appearance ==============================

class AppearanceBox(Gtk.Box):
    PREVIEW = 40

    def __init__(self, title: str = "Appearance") -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._icon_ref = ""      # "mdi:name" or a file path, or ""

        heading = Gtk.Label(label=title, xalign=0)
        heading.add_css_class("heading")
        self.append(heading)

        self.label_entry = Gtk.Entry(placeholder_text="Key label")
        self.append(_row("Label", self.label_entry))

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
        clear_btn.set_tooltip_text("Remove icon")
        clear_btn.connect("clicked", lambda _b: self._set_icon(""))
        for b in (lib_btn, file_btn, clear_btn):
            icon_box.append(b)
        self.append(_row("Icon", icon_box))

        self.color_btn = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog())
        self.append(_row("Background color", self.color_btn))

    def load(self, label: str, icon: str, color: str) -> None:
        self.label_entry.set_text(label)
        self._set_icon(icon)
        rgba = Gdk.RGBA()
        rgba.parse(color or DEFAULT_KEY_BG)
        self.color_btn.set_rgba(rgba)

    def label(self) -> str:
        return self.label_entry.get_text()

    def icon(self) -> str:
        return self._icon_ref

    def color(self) -> str:
        return rgba_to_hex(self.color_btn.get_rgba())

    # ---------- icon selection ----------

    def _set_icon(self, ref: str) -> None:
        self._icon_ref = ref
        tex = self._preview_texture(ref)
        if tex is not None:
            self.preview.set_from_paintable(tex)
        else:
            self.preview.clear()

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
