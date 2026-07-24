"""Application composition: config + bus + OBS + deck + controller + UI."""

from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib  # noqa: E402

from . import APP_ID  # noqa: E402
from .core.config import Config  # noqa: E402
from .core.controller import DeckController  # noqa: E402
from .core.events import EventBus  # noqa: E402
from .device.manager import DeckManager  # noqa: E402
from .obs.client import OBSClient  # noqa: E402

# register the action catalogs (the @register decorators run on import)
from . import basic_actions  # noqa: E402,F401
from .obs import actions as _obs_actions  # noqa: E402,F401

log = logging.getLogger(__name__)


class LinuxStreamDeckApp:
    """Container for the components; wraps GTK's Adw.Application."""

    def __init__(self) -> None:
        self.gtk_app = Adw.Application(application_id=APP_ID)
        self.gtk_app.connect("activate", self._on_activate)
        self.gtk_app.connect("shutdown", self._on_shutdown)
        self.window = None

        self.config = Config.load()
        self.bus = EventBus()
        self.bus.dispatcher = GLib.idle_add
        self.obs = OBSClient(self.bus)
        self.deck = DeckManager(self.bus, brightness=self.config.brightness)
        self.controller = DeckController(self.config, self.bus, self.obs, self.deck)

    def run(self, argv) -> int:
        return self.gtk_app.run(argv)

    def _on_activate(self, _app) -> None:
        if self.window is None:
            from .ui.window import MainWindow

            self.window = MainWindow(self)
            cfg = self.config.obs
            self.obs.configure(cfg.host, cfg.port, cfg.password)
            self.obs.start()
            self.deck.start()
            self.controller.refresh()
        self.window.present()

    def _on_shutdown(self, _app) -> None:
        log.info("Shutting down…")
        self.controller.shutdown()
        self.deck.stop()
        self.obs.stop()
        log.info("Shutdown complete")
