"""Screen saver and persistent display settings for the physical deck."""

from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from ..core.config import (  # noqa: E402
    EXIT_DISPLAY_CHOICES,
    EXIT_DISPLAY_CUSTOM,
    SCREENSAVER_CHOICES,
    SUPPORTED_EXIT_IMAGE_EXTENSIONS,
)
from ..device.exit_display import validate_exit_image  # noqa: E402


class ScreenSaverSettingsDialog(Adw.Window):
    def __init__(self, parent, app) -> None:
        super().__init__(
            transient_for=parent,
            modal=True,
            title="Stream Deck display",
            default_width=560,
            default_height=760,
        )
        self.app = app
        self._previewing = False
        self._closing = False
        self._exit_image_path = app.config.exit_display.image_path

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

        exit_group = Adw.PreferencesGroup(
            title="After LinuxStreamDeck closes",
            description=(
                "Choose what the physical Stream Deck keeps showing after a "
                "clean application exit."
            ),
        )
        exit_group.set_hexpand(True)
        exit_group.set_size_request(430, -1)

        self.exit_mode = Adw.ComboRow(title="Display state")
        self.exit_mode.set_model(
            Gtk.StringList.new(
                [choice[1] for choice in EXIT_DISPLAY_CHOICES]
            )
        )
        exit_selected = next(
            (
                index
                for index, choice in enumerate(EXIT_DISPLAY_CHOICES)
                if choice[0] == app.config.exit_display.mode
            ),
            0,
        )
        self.exit_mode.set_selected(exit_selected)
        self.exit_mode.connect(
            "notify::selected",
            self._on_exit_mode_changed,
        )
        exit_group.add(self.exit_mode)

        self.exit_image = Adw.ActionRow(title="Custom image")
        self.remove_exit_image = Gtk.Button.new_from_icon_name(
            "edit-delete-symbolic"
        )
        self.remove_exit_image.set_tooltip_text("Remove the selected image")
        self.remove_exit_image.set_valign(Gtk.Align.CENTER)
        self.remove_exit_image.connect(
            "clicked",
            self._remove_exit_image,
        )
        self.exit_image.add_suffix(self.remove_exit_image)
        choose_image = Gtk.Button(label="Choose…")
        choose_image.set_valign(Gtk.Align.CENTER)
        choose_image.connect("clicked", self._choose_exit_image)
        self.exit_image.add_suffix(choose_image)
        exit_group.add(self.exit_image)
        page.add(exit_group)

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
        self._on_exit_mode_changed()
        self._update_exit_image_row()
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

    def _selected_exit_choice(self) -> tuple[str, str, str]:
        index = min(
            int(self.exit_mode.get_selected()),
            len(EXIT_DISPLAY_CHOICES) - 1,
        )
        return EXIT_DISPLAY_CHOICES[index]

    def _on_exit_mode_changed(self, *_args) -> None:
        choice = self._selected_exit_choice()
        self.exit_mode.set_subtitle(choice[2])
        self.exit_image.set_sensitive(choice[0] == EXIT_DISPLAY_CUSTOM)

    def _choose_exit_image(self, _button) -> None:
        dialog = Gtk.FileDialog(title="Choose the image shown after exit")
        image_filter = Gtk.FileFilter()
        image_filter.set_name("Supported images")
        for suffix in sorted(SUPPORTED_EXIT_IMAGE_EXTENSIONS):
            image_filter.add_pattern(f"*{suffix}")
            image_filter.add_pattern(f"*{suffix.upper()}")
        dialog.set_default_filter(image_filter)
        dialog.open(self, None, self._on_exit_image_chosen)

    def _on_exit_image_chosen(self, dialog, result) -> None:
        try:
            file = dialog.open_finish(result)
        except Exception:
            return
        path = file.get_path() if file is not None else None
        if not path:
            self._show_error("Choose a local image file.")
            return
        try:
            self._exit_image_path = str(validate_exit_image(path))
        except ValueError as error:
            self._show_error(str(error))
            return
        self._update_exit_image_row()

    def _remove_exit_image(self, _button) -> None:
        self._exit_image_path = ""
        self._update_exit_image_row()

    def _update_exit_image_row(self) -> None:
        if self._exit_image_path:
            path = Path(self._exit_image_path)
            self.exit_image.set_subtitle(path.name)
            self.exit_image.set_tooltip_text(str(path))
            self.remove_exit_image.set_sensitive(True)
        else:
            self.exit_image.set_subtitle("No image selected")
            self.exit_image.set_tooltip_text(None)
            self.remove_exit_image.set_sensitive(False)

    def _show_error(self, body: str) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Custom image unavailable",
            body=body,
        )
        dialog.add_response("close", "Close")
        dialog.present()

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
        exit_mode = self._selected_exit_choice()[0]
        if exit_mode == EXIT_DISPLAY_CUSTOM:
            try:
                validate_exit_image(self._exit_image_path)
            except ValueError as error:
                self._show_error(str(error))
                return
        self._closing = True
        self.app.deck.stop_screensaver()
        self.app.update_deck_display_settings(
            self.enabled.get_active(),
            self._selected_choice()[0],
            int(self.idle.get_value()),
            int(self.intensity.get_value()),
            exit_mode,
            self._exit_image_path,
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
