"""About dialog with project, authorship, license, and source details."""

from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .. import APP_ID, VERSION  # noqa: E402

PROJECT_URL = "https://github.com/javocsoft/linuxstreamdeck"


class AboutDialog(Adw.Dialog):
    def __init__(self) -> None:
        super().__init__()
        self.set_title("About LinuxStreamDeck")
        self.set_content_width(460)
        self.set_content_height(570)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        content.set_margin_top(28)
        content.set_margin_bottom(28)
        content.set_margin_start(24)
        content.set_margin_end(24)

        icon = self._application_icon()
        icon.set_pixel_size(96)
        icon.set_halign(Gtk.Align.CENTER)
        content.append(icon)

        name = Gtk.Label()
        name.set_markup('<span size="xx-large" weight="bold">LinuxStreamDeck</span>')
        name.set_halign(Gtk.Align.CENTER)
        content.append(name)

        version = Gtk.Label(label=f"Version {VERSION}")
        version.add_css_class("dim-label")
        version.set_halign(Gtk.Align.CENTER)
        content.append(version)

        description = Gtk.Label(
            label=(
                "Control your Elgato Stream Deck on Linux with deep OBS Studio "
                "integration and live feedback on every key."
            )
        )
        description.set_wrap(True)
        description.set_justify(Gtk.Justification.CENTER)
        description.set_halign(Gtk.Align.CENTER)
        description.set_max_width_chars(52)
        content.append(description)

        developer = Gtk.Label()
        developer.set_markup("Developed by <b>JavocSoft</b>, 2026")
        developer.set_halign(Gtk.Align.CENTER)
        content.append(developer)

        content.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        open_source = Gtk.Label()
        open_source.set_markup("<b>Open-source software</b>")
        open_source.set_halign(Gtk.Align.CENTER)
        content.append(open_source)

        license_label = Gtk.Label(
            label=(
                "Licensed under the GNU General Public License v3.0 or later "
                "(GPL-3.0-or-later)."
            )
        )
        license_label.set_wrap(True)
        license_label.set_justify(Gtk.Justification.CENTER)
        license_label.set_halign(Gtk.Align.CENTER)
        content.append(license_label)

        source = Gtk.LinkButton.new_with_label(PROJECT_URL, "View source code on GitHub")
        source.set_halign(Gtk.Align.CENTER)
        content.append(source)

        clamp = Adw.Clamp()
        clamp.set_maximum_size(420)
        clamp.set_child(content)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(clamp)
        toolbar.set_content(scroller)
        self.set_child(toolbar)

    @staticmethod
    def _application_icon() -> Gtk.Image:
        data_dirs = (
            Path(GLib.get_user_data_dir()),
            *(Path(directory) for directory in GLib.get_system_data_dirs()),
        )
        candidates = (
            Path(__file__).resolve().parents[2] / "packaging" / f"{APP_ID}.svg",
            *(
                directory
                / "icons"
                / "hicolor"
                / "scalable"
                / "apps"
                / f"{APP_ID}.svg"
                for directory in data_dirs
            ),
        )
        for icon_path in candidates:
            if icon_path.is_file():
                return Gtk.Image.new_from_file(str(icon_path))
        return Gtk.Image.new_from_icon_name(APP_ID)
