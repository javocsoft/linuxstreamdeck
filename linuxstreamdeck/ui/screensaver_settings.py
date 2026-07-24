"""Screen saver selection, idle delay, intensity and live preview."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from ..core.config import SCREENSAVER_CHOICES  # noqa: E402


class ScreenSaverSettingsDialog(Adw.Window):
    def __init__(self, parent, app) -> None:
        super().__init__(
            transient_for=parent,
            modal=True,
            title="Screen saver",
            default_width=560,
            default_height=650,
        )
        self.app = app
        self._previewing = False
        self._closing = False

        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        page = Adw.PreferencesPage()
        page.set_hexpand(True)

        behavior = Adw.PreferencesGroup(
            title="Stream Deck screen saver",
            description=(
                "Animate the physical and virtual decks after a period without "
                "activity. The first key press wakes the deck without "
                "running its action."
            ),
        )
        behavior.set_hexpand(True)
        behavior.set_size_request(430, -1)
        cfg = app.config.screensaver
        self.enabled = Adw.SwitchRow(
            title="Enable screen saver",
            subtitle="Start automatically after the selected idle time",
        )
        self.enabled.set_active(cfg.enabled)
        behavior.add(self.enabled)

        self.style = Adw.ComboRow(title="Style")
        self.style.set_model(
            Gtk.StringList.new([choice[1] for choice in SCREENSAVER_CHOICES])
        )
        selected = next(
            (
                index
                for index, choice in enumerate(SCREENSAVER_CHOICES)
                if choice[0] == cfg.style
            ),
            0,
        )
        self.style.set_selected(selected)
        self.style.connect("notify::selected", self._on_style_changed)
        behavior.add(self.style)

        self.idle = Adw.SpinRow.new_with_range(1, 1440, 1)
        self.idle.set_title("Start after")
        self.idle.set_subtitle("Minutes without physical or virtual deck activity")
        self.idle.set_value(cfg.idle_minutes)
        behavior.add(self.idle)

        self.intensity = Adw.SpinRow.new_with_range(5, 100, 5)
        self.intensity.set_title("Light intensity")
        self.intensity.set_subtitle(
            "Screen saver brightness, independent of normal deck brightness"
        )
        self.intensity.set_value(cfg.intensity)
        self.intensity.connect(
            "notify::value",
            self._on_intensity_changed,
        )
        behavior.add(self.intensity)
        page.add(behavior)

        actions = Adw.PreferencesGroup(
            description=(
                "Preview works with the virtual deck too, so every animation "
                "can be tested without physical hardware."
            )
        )
        actions.set_hexpand(True)
        actions.set_size_request(430, -1)
        buttons = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=10,
            margin_top=6,
        )
        self.preview = Gtk.Button(label="Preview now", hexpand=True)
        self.preview.connect("clicked", self._toggle_preview)
        buttons.append(self.preview)
        save = Gtk.Button(label="Save", hexpand=True)
        save.add_css_class("suggested-action")
        save.connect("clicked", self._save)
        buttons.append(save)
        actions.add(buttons)
        page.add(actions)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(page)
        view.set_content(scroller)
        self.set_content(view)
        self._on_style_changed()
        self.connect("close-request", self._on_close_request)
        app.bus.subscribe("deck.screensaver", self._on_screensaver_event)

    def _selected_choice(self) -> tuple[str, str, str]:
        index = min(
            int(self.style.get_selected()),
            len(SCREENSAVER_CHOICES) - 1,
        )
        return SCREENSAVER_CHOICES[index]

    def _on_style_changed(self, *_args) -> None:
        self.style.set_subtitle(self._selected_choice()[2])
        if self._previewing:
            self.app.deck.preview_screensaver(
                self._selected_choice()[0],
                int(self.intensity.get_value()),
            )

    def _on_intensity_changed(self, *_args) -> None:
        if self._previewing:
            self.app.deck.preview_screensaver(
                self._selected_choice()[0],
                int(self.intensity.get_value()),
            )

    def _toggle_preview(self, _button) -> None:
        if self._previewing:
            self.app.deck.stop_screensaver()
            return
        self.app.deck.preview_screensaver(
            self._selected_choice()[0],
            int(self.intensity.get_value()),
        )

    def _on_screensaver_event(self, _topic: str, data: dict) -> None:
        if not self.get_visible():
            return
        self._previewing = bool(data.get("active") and data.get("preview"))
        self.preview.set_label(
            "Stop preview" if self._previewing else "Preview now"
        )

    def _save(self, _button) -> None:
        self._closing = True
        self.app.deck.stop_screensaver()
        self.app.update_screensaver_settings(
            self.enabled.get_active(),
            self._selected_choice()[0],
            int(self.idle.get_value()),
            int(self.intensity.get_value()),
        )
        self.close()

    def _on_close_request(self, *_args) -> bool:
        if not self._closing and self._previewing:
            self.app.deck.stop_screensaver()
        self.app.bus.unsubscribe(
            "deck.screensaver",
            self._on_screensaver_event,
        )
        return False
