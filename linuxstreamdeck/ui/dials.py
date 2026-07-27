"""Stream Deck + encoders: the row under the grid and the dial editor.

Dials are deliberately kept out of `EditorPanel`. That panel's unsaved-change
guard compares a canonical draft against a baseline for one slot of the key
grid, and a dial is not a slot of that grid: it is numbered separately and
lives in its own mapping on the page. Giving it its own dialog keeps that
guard exactly as it is rather than teaching it about a second kind of index.

The row only exists when a deck with encoders is connected, so nothing here is
reachable on hardware that has none.
"""

from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from ..core.config import KIND_DIAL, KeyConfig  # noqa: E402
from .steps import AppearanceBox, StepList  # noqa: E402

log = logging.getLogger(__name__)

# The three encoder gestures, in the order the dialog shows them.
DIAL_GESTURES = (
    ("steps_left", "Turn left", "Runs once per step of the turn"),
    ("steps_right", "Turn right", "Runs once per step of the turn"),
    ("steps_press", "Push", "Also runs when the strip above is tapped"),
)


def dial_summary(dial: KeyConfig | None) -> str:
    """What a dial button says under the grid."""
    if dial is None or dial.is_empty():
        return "Not set"
    if dial.label.strip():
        return dial.label.strip()
    counts = sum(
        1
        for field, _title, _hint in DIAL_GESTURES
        if getattr(dial, field, [])
    )
    return f"{counts} gesture{'s' if counts != 1 else ''}"


class DialRow(Gtk.Box):
    """One button per encoder, shown under the key grid on a Stream Deck +."""

    def __init__(self, window) -> None:
        super().__init__(spacing=8, halign=Gtk.Align.CENTER)
        self.window = window
        self.app = window.app
        self._buttons: list[Gtk.Button] = []
        self.set_margin_top(10)
        self.rebuild()

    def rebuild(self) -> None:
        """Match the connected hardware: no encoders means no row at all."""
        while (child := self.get_first_child()) is not None:
            self.remove(child)
        self._buttons.clear()
        count = int(getattr(self.app.deck, "dial_count", 0) or 0)
        self.set_visible(bool(count))
        if not count:
            return
        for index in range(count):
            button = Gtk.Button()
            button.set_size_request(120, -1)
            button.connect("clicked", self._open, index)
            self._buttons.append(button)
            self.append(button)
        self.refresh()

    def refresh(self) -> None:
        """Re-label the buttons from the active page."""
        page = self.app.controller.page
        for index, button in enumerate(self._buttons):
            dial = page.dial(index)
            button.set_child(
                Gtk.Label(
                    label=f"Dial {index + 1}\n{dial_summary(dial)}",
                    justify=Gtk.Justification.CENTER,
                    use_markup=False,
                )
            )
            button.set_tooltip_text(f"Configure dial {index + 1}")

    def _open(self, _button, index: int) -> None:
        DialDialog(self.window, index, on_saved=self.refresh).present()


class DialDialog(Adw.Window):
    """Edit one encoder: three action lists plus what the strip shows."""

    def __init__(self, window, index: int, on_saved=None) -> None:
        super().__init__(
            transient_for=window,
            modal=True,
            title=f"Dial {index + 1}",
            default_width=560,
            default_height=680,
        )
        self.window = window
        self.app = window.app
        self.index = index
        self._on_saved = on_saved
        self._lists: dict[str, StepList] = {}

        dial = self.app.controller.page.dial(index) or KeyConfig(kind=KIND_DIAL)

        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())

        body = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=12,
            margin_top=14, margin_bottom=14, margin_start=14, margin_end=14,
        )
        hint = Gtk.Label(
            label="Each gesture runs its own list. Leave one empty to ignore "
                  "it. A fast turn reports several steps at once and runs the "
                  "list once per step.",
            wrap=True, xalign=0,
        )
        hint.add_css_class("dim-label")
        body.append(hint)

        for field, title, subtitle in DIAL_GESTURES:
            steps = StepList(self.app)
            steps.load(list(getattr(dial, field, [])))
            self._lists[field] = steps
            body.append(self._frame(title, subtitle, steps))

        self.appearance = AppearanceBox("Strip appearance")
        self.appearance.load(
            dial.label,
            dial.icon,
            dial.bg_color,
            self._first_icon(dial),
            dial.font_size,
            dial.text_color,
        )
        body.append(Gtk.Separator())
        strip_hint = Gtk.Label(
            label="Shown on the LCD strip above this dial, which is the only "
                  "thing that can say what it does.",
            wrap=True, xalign=0,
        )
        strip_hint.add_css_class("dim-label")
        body.append(strip_hint)
        body.append(self.appearance)

        scroller = Gtk.ScrolledWindow(child=body)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        view.set_content(scroller)

        actions = Gtk.Box(
            spacing=8, halign=Gtk.Align.END,
            margin_top=8, margin_bottom=8, margin_start=12, margin_end=12,
        )
        clear = Gtk.Button(label="Clear dial")
        clear.add_css_class("destructive-action")
        clear.connect("clicked", self._clear)
        actions.append(clear)
        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")
        save.connect("clicked", self._save)
        actions.append(save)
        view.add_bottom_bar(actions)

        self.set_content(view)

    @staticmethod
    def _frame(title: str, subtitle: str, child: Gtk.Widget) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        heading = Gtk.Label(label=title, xalign=0)
        heading.add_css_class("heading")
        box.append(heading)
        note = Gtk.Label(label=subtitle, xalign=0)
        note.add_css_class("dim-label")
        box.append(note)
        box.append(child)
        return box

    @staticmethod
    def _first_icon(dial: KeyConfig) -> str:
        from ..core import actions as registry

        for field in ("steps_press", "steps_right", "steps_left"):
            for step in getattr(dial, field, []):
                action = registry.get(step.action)
                if action is not None and action.default_icon:
                    return action.default_icon
        return "mdi:knob"

    def current_dial(self) -> KeyConfig:
        """Build the dial from the dialog without saving it."""
        dial = KeyConfig(kind=KIND_DIAL)
        for field, _title, _subtitle in DIAL_GESTURES:
            setattr(dial, field, self._lists[field].get_steps())
        dial.label = self.appearance.label()
        dial.icon = self.appearance.icon()
        dial.bg_color = self.appearance.color()
        dial.font_size = self.appearance.font_size()
        dial.text_color = self.appearance.text_color()
        return dial

    def _save(self, _button) -> None:
        self._apply(self.current_dial())

    def _clear(self, _button) -> None:
        self._apply(None)

    def _apply(self, dial: KeyConfig | None) -> None:
        controller = self.app.controller
        controller.page.set_dial(self.index, dial)
        controller.dial_config_changed(self.index)
        self.app.config.save()
        if self._on_saved is not None:
            self._on_saved()
        self.app.bus.emit(
            "status",
            text=(
                f"Dial {self.index + 1} cleared"
                if dial is None or dial.is_empty()
                else f"Dial {self.index + 1} saved"
            ),
        )
        self.close()


__all__ = ["DIAL_GESTURES", "DialDialog", "DialRow", "dial_summary"]
