"""Key editor panel.

Lets you choose the key type:
  - Single action     → one action (with state feedback)
  - Multiple actions  → ordered list of actions run in sequence
  - Toggle (ON/OFF)   → two action lists, one per state

The logic of a single step (category/action/parameters) and the appearance live
in `steps.py`; here they are only composed according to the chosen type.
"""

from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ..core.config import (  # noqa: E402
    KIND_MULTI,
    KIND_SINGLE,
    KIND_TOGGLE,
    ActionStep,
    KeyConfig,
)
from .steps import AppearanceBox, StepEditor, StepList  # noqa: E402

log = logging.getLogger(__name__)

KINDS = [
    (KIND_SINGLE, "Single action"),
    (KIND_MULTI, "Multiple actions"),
    (KIND_TOGGLE, "Toggle (ON/OFF)"),
]
KIND_IDS = [k for k, _ in KINDS]


class EditorPanel(Gtk.Box):
    def __init__(self, app) -> None:
        super().__init__(
            orientation=Gtk.Orientation.VERTICAL, spacing=10,
            margin_top=14, margin_bottom=14, margin_start=14, margin_end=14,
        )
        self.app = app
        self.index: int | None = None
        self._building = False

        self.title = Gtk.Label(xalign=0)
        self.title.add_css_class("title-3")
        self.append(self.title)

        self.kind_dd = Gtk.DropDown.new_from_strings([name for _, name in KINDS])
        self.kind_row = self._labelled("Key type", self.kind_dd)
        self.append(self.kind_row)
        self.kind_dd.connect("notify::selected", self._on_kind_changed)

        # scrollable body: grows to fill the available space and scrolls its
        # content when steps are added, without enlarging the window
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.scroller = Gtk.ScrolledWindow(child=self.body)
        self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroller.set_vexpand(True)
        self.scroller.set_min_content_height(180)
        self.append(self.scroller)

        # buttons pinned at the bottom (always visible even if the body overflows)
        self.buttons = self._build_buttons()
        self.append(self.buttons)

        # sub-widgets by type (filled in _build_body)
        self.single_editor: StepEditor | None = None
        self.multi_list: StepList | None = None
        self.on_list: StepList | None = None
        self.off_list: StepList | None = None
        self.app_main: AppearanceBox | None = None
        self.app_off: AppearanceBox | None = None

        self.clear()

    # ---------- public API ----------

    def clear(self) -> None:
        self.title.set_label("No key selected")
        self.kind_row.set_visible(False)
        self.buttons.set_visible(False)
        self._clear(self.body)
        info = Gtk.Label(
            label="Select a key from the grid to configure it.",
            wrap=True, xalign=0,
        )
        info.add_css_class("dim-label")
        self.body.append(info)
        self.index = None

    def load(self, index: int) -> None:
        self.index = index
        kc = self.app.controller.page.key(index) or KeyConfig()
        self.title.set_label(f"Key {index + 1}")
        self.kind_row.set_visible(True)
        self.buttons.set_visible(True)
        self._building = True
        self.kind_dd.set_selected(KIND_IDS.index(kc.kind) if kc.kind in KIND_IDS else 0)
        self._building = False
        self._build_body(kc)

    # ---------- body construction ----------

    def _on_kind_changed(self, *_a) -> None:
        if self._building or self.index is None:
            return
        # changing type starts with an empty configuration of that type
        self._build_body(KeyConfig(kind=self._current_kind()))

    def _current_kind(self) -> str:
        i = self.kind_dd.get_selected()
        return KIND_IDS[i] if i != Gtk.INVALID_LIST_POSITION else KIND_SINGLE

    def _build_body(self, kc: KeyConfig) -> None:
        self._clear(self.body)
        self.single_editor = self.multi_list = None
        self.on_list = self.off_list = self.app_main = self.app_off = None
        kind = self._current_kind()

        if kind == KIND_SINGLE:
            self.single_editor = StepEditor(self.app)
            self.single_editor.load(ActionStep(action=kc.action, params=kc.params))
            self.body.append(self.single_editor)
            self.body.append(Gtk.Separator())
            self.app_main = AppearanceBox("Appearance")
            self.app_main.load(kc.label, kc.icon, kc.bg_color)
            self.body.append(self.app_main)

        elif kind == KIND_MULTI:
            self.body.append(self._hint(
                "They run in order when pressed. Add a «Wait» action for pauses."
            ))
            self.multi_list = StepList(self.app)
            self.multi_list.load(kc.steps)
            self.body.append(self.multi_list)
            self.body.append(Gtk.Separator())
            self.app_main = AppearanceBox("Appearance")
            self.app_main.load(kc.label, kc.icon, kc.bg_color)
            self.body.append(self.app_main)

        else:  # KIND_TOGGLE
            self.body.append(self._hint(
                "Each press toggles the state and runs its action list."
            ))
            self.on_list = StepList(self.app)
            self.on_list.load(kc.steps_on)
            self.app_main = AppearanceBox("ON state appearance")
            self.app_main.load(kc.label, kc.icon, kc.bg_color)
            self.body.append(self._frame("▶ ON state", [self.on_list, self.app_main]))

            self.off_list = StepList(self.app)
            self.off_list.load(kc.steps_off)
            self.app_off = AppearanceBox("OFF state appearance")
            self.app_off.load(kc.label_off, kc.icon_off, kc.bg_color_off)
            self.body.append(self._frame("■ OFF state", [self.off_list, self.app_off]))

    def _build_buttons(self) -> Gtk.Box:
        btns = Gtk.Box(spacing=6, margin_top=10)
        save = Gtk.Button(label="Save", hexpand=True)
        save.add_css_class("suggested-action")
        save.connect("clicked", self._save)
        test = Gtk.Button(label="Test")
        test.connect("clicked", lambda _b: self.app.controller.press(self.index))
        wipe = Gtk.Button(label="Clear")
        wipe.add_css_class("destructive-action")
        wipe.connect("clicked", self._wipe)
        for b in (save, test, wipe):
            btns.append(b)
        return btns

    # ---------- save / clear ----------

    def _save(self, _btn) -> None:
        if self.index is None:
            return
        kind = self._current_kind()
        kc = KeyConfig(kind=kind)

        if kind == KIND_SINGLE:
            step = self.single_editor.get_step()
            kc.action, kc.params = step.action, step.params
        elif kind == KIND_MULTI:
            kc.steps = self.multi_list.get_steps()
        else:
            kc.steps_on = self.on_list.get_steps()
            kc.steps_off = self.off_list.get_steps()
            kc.label_off = self.app_off.label()
            kc.icon_off = self.app_off.icon()
            kc.bg_color_off = self.app_off.color()

        kc.label = self.app_main.label()
        kc.icon = self.app_main.icon()
        kc.bg_color = self.app_main.color()

        self.app.controller.page.set_key(self.index, kc)
        self.app.config.save()
        self.app.controller.refresh()
        name = dict(KINDS)[kind]
        self.app.bus.emit("status", text=f"Key {self.index + 1} saved ({name})")

    def _wipe(self, _btn) -> None:
        if self.index is None:
            return
        self.app.controller.page.set_key(self.index, None)
        self.app.config.save()
        self.app.controller.refresh()
        self.load(self.index)

    # ---------- layout helpers ----------

    @staticmethod
    def _labelled(label: str, widget: Gtk.Widget) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        lbl = Gtk.Label(label=label, xalign=0)
        lbl.add_css_class("caption-heading")
        box.append(lbl)
        box.append(widget)
        return box

    @staticmethod
    def _hint(text: str) -> Gtk.Label:
        lbl = Gtk.Label(label=text, wrap=True, xalign=0)
        lbl.add_css_class("dim-label")
        return lbl

    @staticmethod
    def _frame(title: str, children: list[Gtk.Widget]) -> Gtk.Frame:
        frame = Gtk.Frame(label=title)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                      margin_top=8, margin_bottom=8, margin_start=8, margin_end=8)
        for i, child in enumerate(children):
            if i:
                box.append(Gtk.Separator())
            box.append(child)
        frame.set_child(box)
        return frame

    @staticmethod
    def _clear(box: Gtk.Box) -> None:
        while (child := box.get_first_child()) is not None:
            box.remove(child)
