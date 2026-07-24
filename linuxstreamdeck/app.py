"""Application composition: config + bus + OBS + deck + controller + UI."""

from __future__ import annotations

import logging
from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib  # noqa: E402

from . import APP_ID  # noqa: E402
from .ai.service import AIService  # noqa: E402
from .core.config import (  # noqa: E402
    DEFAULT_SCREENSAVER,
    SCREENSAVER_IDS,
    Config,
)
from .core.controller import DeckController  # noqa: E402
from .core.events import EventBus  # noqa: E402
from .core.secrets import ApiKeyStore, SecretStore  # noqa: E402
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
        self._shutting_down = False
        self._obs_started = False
        self.obs_password_ready = False

        self.config = Config.load()
        self.secrets = SecretStore()
        self.ai_keys = ApiKeyStore()
        self.ai = AIService()
        self.bus = EventBus()
        self.bus.dispatcher = GLib.idle_add
        self.obs = OBSClient(self.bus)
        screen = self.config.screensaver
        self.deck = DeckManager(
            self.bus,
            brightness=self.config.brightness,
            screensaver_enabled=screen.enabled,
            screensaver_style=screen.style,
            screensaver_idle_minutes=screen.idle_minutes,
            screensaver_intensity=screen.intensity,
        )
        self.controller = DeckController(self.config, self.bus, self.obs, self.deck)

    def run(self, argv) -> int:
        return self.gtk_app.run(argv)

    def _on_activate(self, _app) -> None:
        if self.window is None:
            from .ui.window import MainWindow

            self.window = MainWindow(self)
            self.deck.start()
            self.controller.refresh()
            self._load_obs_password()
        self.window.present()

    def _on_shutdown(self, _app) -> None:
        self._shutting_down = True
        log.info("Shutting down…")
        self.controller.shutdown()
        self.deck.stop()
        self.obs.stop()
        log.info("Shutdown complete")

    # ---------- secure OBS credentials ----------

    def _load_obs_password(self) -> None:
        cfg = self.config.obs
        if self.config.obs_password_needs_migration and cfg.password:
            self._activate_obs_password(cfg.password)
            try:
                self.config.finish_password_migration()
            except Exception:
                log.exception("Could not sanitize legacy OBS credentials")
                self.bus.emit(
                    "status",
                    text="A legacy configuration file could not be sanitized",
                )
            self.secrets.store(cfg.password, self._on_password_migrated)
            return
        if self.config.obs_password_needs_migration:
            try:
                self.config.finish_password_migration()
            except Exception:
                log.exception("Could not sanitize empty legacy credentials")
        self.secrets.lookup(self._on_password_loaded)

    def _on_password_loaded(
        self, password: str, error: Exception | None
    ) -> None:
        if self._shutting_down:
            return
        if error is not None:
            log.warning("Secure password storage is unavailable: %s", error)
            self.bus.emit(
                "status",
                text=(
                    "Secure password storage is unavailable; the OBS password "
                    "will not persist"
                ),
            )
            password = ""
        else:
            if not self.config.scrub_plaintext_password_files():
                self.bus.emit(
                    "status",
                    text="A legacy configuration backup could not be sanitized",
                )
        self._activate_obs_password(password)

    def _on_password_migrated(
        self, stored: bool, error: Exception | None
    ) -> None:
        if self._shutting_down:
            return
        if stored:
            self.bus.emit(
                "status", text="OBS password moved to secure storage"
            )
            return
        log.warning("Could not migrate the OBS password: %s", error)
        self.bus.emit(
            "status",
            text=(
                "Secure password storage is unavailable; the OBS password "
                "will not persist"
            ),
        )

    def _activate_obs_password(self, password: str) -> None:
        if self._shutting_down:
            return
        cfg = self.config.obs
        cfg.password = password
        self.obs.configure(cfg.host, cfg.port, password)
        if not self._obs_started:
            self._obs_started = True
            self.obs.start()
        self.obs_password_ready = True
        if self.window is not None:
            self.window.obs_btn.set_sensitive(True)

    def update_obs_settings(
        self,
        host: str,
        port: int,
        password: str,
        callback: Callable[[bool, Exception | None], None],
    ) -> None:
        self.secrets.store(
            password,
            lambda stored, error: self._on_obs_password_stored(
                host, port, password, callback, stored, error
            ),
        )

    def update_screensaver_settings(
        self,
        enabled: bool,
        style: str,
        idle_minutes: int,
        intensity: int,
    ) -> None:
        cfg = self.config.screensaver
        cfg.enabled = bool(enabled)
        cfg.style = style if style in SCREENSAVER_IDS else DEFAULT_SCREENSAVER
        cfg.idle_minutes = max(1, min(1440, int(idle_minutes)))
        cfg.intensity = max(5, min(100, int(intensity)))
        self.config.save()
        self.deck.configure_screensaver(
            cfg.enabled,
            cfg.style,
            cfg.idle_minutes,
            cfg.intensity,
        )
        self.bus.emit(
            "status",
            text=(
                "Screen saver enabled"
                if cfg.enabled
                else "Screen saver disabled"
            ),
        )

    def _on_obs_password_stored(
        self,
        host: str,
        port: int,
        password: str,
        callback: Callable[[bool, Exception | None], None],
        stored: bool,
        error: Exception | None,
    ) -> None:
        cfg = self.config.obs
        cfg.host = host
        cfg.port = port
        cfg.password = password
        self.config.obs_password_needs_migration = False
        persistence_error = error
        try:
            self.config.finish_password_migration()
        except Exception as save_error:
            log.exception("Could not save OBS settings")
            persistence_error = save_error
        if self._shutting_down:
            return
        self.obs.configure(host, port, password)
        if self._obs_started:
            self.obs.reconnect_now()
        else:
            self._obs_started = True
            self.obs.start()
        callback(stored and persistence_error is None, persistence_error)
