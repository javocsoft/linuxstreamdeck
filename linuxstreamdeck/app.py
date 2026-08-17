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
from .core import autostart, keylight, webrequest  # noqa: E402
from .core.config import (  # noqa: E402
    CLOSE_ACTION_TRAY,
    CLOSE_ACTIONS,
    DEFAULT_CLOSE_ACTION,
    DEFAULT_SCREENSAVER,
    EXIT_DISPLAY_DEFAULT,
    EXIT_DISPLAY_MODES,
    SCREENSAVER_IDS,
    Config,
)
from .core.controller import DeckController  # noqa: E402
from .core.events import EventBus  # noqa: E402
from .core.homeassistant import HomeAssistantClient  # noqa: E402
from .core.secrets import (  # noqa: E402
    ApiKeyStore,
    HomeAssistantTokenStore,
    SecretStore,
    TwitchTokenStore,
)
from .core.starter import is_first_run  # noqa: E402
from .device.manager import DeckManager  # noqa: E402
from .obs.client import OBSClient  # noqa: E402
from .twitch.client import TwitchClient  # noqa: E402
from .twitch.eventsub import EventSubSession  # noqa: E402

# register the action catalogs (the @register decorators run on import)
from . import basic_actions  # noqa: E402,F401
from . import ha_actions as _ha_actions  # noqa: E402,F401
from . import light_actions as _light_actions  # noqa: E402,F401
from . import system_stats as _system_stats  # noqa: E402,F401
from . import web_actions as _web_actions  # noqa: E402,F401
from .obs import actions as _obs_actions  # noqa: E402,F401
from .twitch import actions as _twitch_actions  # noqa: E402,F401

log = logging.getLogger(__name__)

# How long a hidden start waits for the status area to accept the icon before
# falling back to showing the window.
HIDDEN_START_GRACE_SECONDS = 5


class LinuxStreamDeckApp:
    """Container for the components; wraps GTK's Adw.Application."""

    def __init__(self, start_hidden: bool = False) -> None:
        self.gtk_app = Adw.Application(application_id=APP_ID)
        self.gtk_app.connect("activate", self._on_activate)
        self.gtk_app.connect("shutdown", self._on_shutdown)
        self.window = None
        self.tray = None
        self._shutting_down = False
        self._obs_started = False
        self._start_hidden = start_hidden
        self._quitting = False
        self.obs_password_ready = False

        # Asked before loading, which is what creates the file: a first run is
        # the one moment an empty deck is worth offering to fill.
        self.first_run = is_first_run()
        self.config = Config.load()
        self.secrets = SecretStore()
        self.ai_keys = ApiKeyStore()
        self.ai = AIService()
        self.bus = EventBus()
        self.bus.dispatcher = GLib.idle_add
        self.obs = OBSClient(self.bus)
        self.twitch = TwitchClient(
            self.bus,
            store=TwitchTokenStore(),
            client_id=self.config.twitch.client_id,
        )
        self.ha_tokens = HomeAssistantTokenStore()
        self.home_assistant = HomeAssistantClient(
            store=self.ha_tokens,
            base_url=self.config.home_assistant.base_url,
        )
        screen = self.config.screensaver
        exit_display = self.config.exit_display
        self.deck = DeckManager(
            self.bus,
            brightness=self.config.brightness,
            screensaver_enabled=screen.enabled,
            screensaver_style=screen.style,
            screensaver_idle_minutes=screen.idle_minutes,
            screensaver_intensity=screen.intensity,
            exit_display_mode=exit_display.mode,
            exit_display_image=exit_display.image_path,
        )
        self.bus.subscribe("obs.outputs", self._sync_obs_screensaver_policy)
        self.controller = DeckController(
            self.config, self.bus, self.obs, self.deck, twitch=self.twitch,
            home_assistant=self.home_assistant,
        )
        # A state that changed while nobody was looking repaints the deck at
        # once, rather than waiting out the live loop's interval. Wired after
        # the controller exists, and only fires on a real change.
        self.home_assistant.on_change = self.controller.refresh
        # The live event feed. It only reaches the deck through the
        # controller's attention runtime, so nothing here knows about keys.
        self.events = EventSubSession(
            self.twitch, self.bus, self.controller.attention.add
        )

    def run(self, argv) -> int:
        return self.gtk_app.run(argv)

    def _sync_obs_screensaver_policy(self, _topic, _data) -> None:
        """Keep automatic screen saving out of an active OBS session."""
        self.deck.set_screensaver_suppressed(
            bool(_data["recording"] or _data["streaming"])
        )

    def _on_activate(self, _app) -> None:
        if self.window is None:
            from .ui.window import MainWindow

            self.window = MainWindow(self)
            self._start_tray()
            self.deck.start()
            self.controller.refresh()
            self._load_obs_password()
            # Reads the keyring on its own worker, so a locked collection
            # cannot delay the window appearing.
            self.twitch.start()
            self.events.start()
            # Only a session-login start may stay hidden, and only when the
            # status icon is really there to bring the window back.
            if self._start_hidden and self.tray is not None:
                log.info("Started hidden in the status area")
                # Registration completes asynchronously; show the window after
                # all if the status area never took the icon, so a hidden start
                # can never leave the application with no way in.
                GLib.timeout_add_seconds(
                    HIDDEN_START_GRACE_SECONDS, self._verify_hidden_start
                )
                return
        self.present_window()
        if self.first_run:
            # Once only, and after the window is up: the offer is a dialog that
            # is modal to it.
            self.first_run = False
            GLib.idle_add(self.window.offer_starter_keys)

    def _verify_hidden_start(self) -> bool:
        if not self.tray_available and not self._shutting_down:
            log.warning(
                "The status icon was not accepted; showing the window instead"
            )
            self.present_window()
        return False

    def _on_shutdown(self, _app) -> None:
        self._shutting_down = True
        log.info("Shutting down…")
        if self.tray is not None:
            self.tray.stop()
            self.tray = None
        self.controller.shutdown()
        self.deck.stop()
        self.obs.stop()
        # The event session before the client it makes requests through, for
        # the same reason the deck stops before OBS: nothing may be left
        # waiting on something that has already gone.
        self.events.stop()
        self.twitch.stop()
        # Last, and after the controller: its workers are what ask for a key's
        # value, so nothing can queue a fetch once they have stopped.
        keylight.forget_states()
        webrequest.shutdown()
        log.info("Shutdown complete")

    # ---------- status icon and window lifetime ----------

    def _start_tray(self) -> None:
        from .ui.tray import TrayIcon, is_supported

        if not is_supported():
            # Not a reason to give up: the icon follows the watcher name, so a
            # panel that is still starting (very common at session login) picks
            # it up as soon as it appears. Until then the icon counts as
            # unregistered and closing the window quits.
            log.info(
                "No status area yet; the icon will appear if one shows up"
            )
        tray = TrayIcon(
            on_open=self.present_window,
            on_quit=self.request_quit,
            on_select_profile=self.select_profile,
            profiles=self._tray_profiles,
        )
        if not tray.start():
            return
        self.tray = tray
        # Adding, renaming, deleting or switching a profile all publish this
        # event, so the icon's profile list never goes stale.
        self.bus.subscribe("profile.changed", lambda *_a: tray.refresh())

    def _tray_profiles(self) -> tuple[list[str], int]:
        return (
            [profile.name for profile in self.config.profiles],
            self.config.current_profile,
        )

    @property
    def tray_available(self) -> bool:
        return self.tray is not None and self.tray.registered

    def hides_on_close(self) -> bool:
        """Whether closing the window should hide it instead of quitting."""
        return (
            not self._quitting
            and self.config.close_action == CLOSE_ACTION_TRAY
            and self.tray_available
        )

    def present_window(self) -> None:
        """Show the main window, creating it if the application started hidden."""
        if self.window is None:
            self.gtk_app.activate()
            return
        self.window.set_visible(True)
        self.window.present()

    def select_profile(self, index: int) -> None:
        """Switch profile from the status icon, respecting unsaved key edits."""
        if self.window is not None:
            self.window.request_profile(index)
        else:
            self.controller.set_profile(index)

    def request_quit(self) -> None:
        """Quit from the status icon, giving the window a chance to confirm."""
        if self.window is not None:
            self.window.request_quit()
        else:
            self.quit()

    def quit(self) -> None:
        """Really terminate the application, bypassing the hide-on-close rule."""
        self._quitting = True
        self.gtk_app.quit()

    def update_application_settings(
        self, close_action: str, start_on_login: bool
    ) -> Exception | None:
        """Save the close behaviour and apply the autostart entry."""
        self.config.close_action = (
            close_action if close_action in CLOSE_ACTIONS else DEFAULT_CLOSE_ACTION
        )
        self.config.save()
        error: Exception | None = None
        try:
            autostart.set_enabled(start_on_login)
        except OSError as autostart_error:
            log.exception("Could not update the autostart entry")
            error = autostart_error
        self.bus.emit("status", text="Application settings saved")
        return error

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

    def update_deck_display_settings(
        self,
        enabled: bool,
        style: str,
        idle_minutes: int,
        intensity: int,
        exit_display_mode: str,
        exit_display_image: str,
    ) -> None:
        cfg = self.config.screensaver
        cfg.enabled = bool(enabled)
        cfg.style = style if style in SCREENSAVER_IDS else DEFAULT_SCREENSAVER
        cfg.idle_minutes = max(1, min(1440, int(idle_minutes)))
        cfg.intensity = max(5, min(100, int(intensity)))
        exit_cfg = self.config.exit_display
        exit_cfg.mode = (
            exit_display_mode
            if exit_display_mode in EXIT_DISPLAY_MODES
            else EXIT_DISPLAY_DEFAULT
        )
        exit_cfg.image_path = str(exit_display_image or "")
        self.config.save()
        self.deck.configure_screensaver(
            cfg.enabled,
            cfg.style,
            cfg.idle_minutes,
            cfg.intensity,
        )
        self.deck.configure_exit_display(
            exit_cfg.mode,
            exit_cfg.image_path,
        )
        self.bus.emit(
            "status",
            text="Stream Deck display settings saved",
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
