"""Diálogo de conexión con OBS (obs-websocket v5)."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402


class ObsSettingsDialog(Adw.Window):
    def __init__(self, parent, app) -> None:
        super().__init__(
            transient_for=parent, modal=True, title="Conexión con OBS",
            default_width=420, default_height=380,
        )
        self.app = app

        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())

        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(
            title="obs-websocket",
            description=(
                "En OBS: Herramientas → Ajustes del servidor WebSocket.\n"
                "Activa el servidor y copia aquí el puerto y la contraseña."
            ),
        )

        cfg = app.config.obs
        self.host = Adw.EntryRow(title="Servidor", text=cfg.host)
        self.port = Adw.SpinRow.new_with_range(1, 65535, 1)
        self.port.set_title("Puerto")
        self.port.set_value(cfg.port)
        self.password = Adw.PasswordEntryRow(title="Contraseña", text=cfg.password)
        for row in (self.host, self.port, self.password):
            group.add(row)
        page.add(group)

        actions = Adw.PreferencesGroup()
        connect = Gtk.Button(label="Guardar y conectar", margin_top=6)
        connect.add_css_class("suggested-action")
        connect.connect("clicked", self._connect)
        actions.add(connect)
        self.status = Gtk.Label(margin_top=10)
        self.status.add_css_class("dim-label")
        actions.add(self.status)
        page.add(actions)

        view.set_content(page)
        self.set_content(view)
        self._update_status()

        app.bus.subscribe("obs.connected", lambda t, d: self._update_status())
        app.bus.subscribe("obs.disconnected", lambda t, d: self._update_status())

    def _connect(self, _btn) -> None:
        cfg = self.app.config.obs
        cfg.host = self.host.get_text().strip() or "localhost"
        cfg.port = int(self.port.get_value())
        cfg.password = self.password.get_text()
        self.app.config.save()
        self.app.obs.configure(cfg.host, cfg.port, cfg.password)
        self.app.obs.reconnect_now()
        self.status.set_label("Conectando…")

    def _update_status(self) -> None:
        if not self.get_visible():
            return
        self.status.set_label(
            "✓ Conectado" if self.app.obs.connected else "Sin conexión con OBS"
        )
