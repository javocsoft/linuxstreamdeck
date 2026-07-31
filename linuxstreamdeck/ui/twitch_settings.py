"""Twitch account dialog: link an account with a device code, or disconnect it.

The whole flow happens off the GTK thread. Requesting a code is one network
round trip and polling for the answer takes as long as the user takes to type
it into a browser, so doing either inline would freeze the window for minutes.
Every result comes back through `GLib.idle_add`.
"""

from __future__ import annotations

import logging
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ..twitch import auth  # noqa: E402
from ..twitch.constants import (  # noqa: E402
    CONNECTIONS_URL,
    DEFAULT_CLIENT_ID,
    SCOPES,
)
from ..twitch.http import TwitchError  # noqa: E402

log = logging.getLogger(__name__)


class TwitchSettingsDialog(Adw.Window):
    def __init__(self, parent, app) -> None:
        super().__init__(
            transient_for=parent,
            modal=True,
            title="Twitch account",
            default_width=520,
            default_height=560,
        )
        self.app = app
        self._flow: threading.Thread | None = None
        self._cancel = threading.Event()

        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        page = Adw.PreferencesPage()

        self.account_group = Adw.PreferencesGroup(title="Account")
        self.account_row = Adw.ActionRow(title="Not connected")
        self.account_group.add(self.account_row)
        page.add(self.account_group)

        # The code the user has to type, hidden until there is one. It is
        # selectable because reading six characters off a screen and typing
        # them into a phone is exactly where people make mistakes.
        self.code_group = Adw.PreferencesGroup(
            title="Authorize this application",
            description=(
                "Open the address below and enter this code. Nothing is sent "
                "anywhere else, and you can revoke it at any time from your "
                "Twitch settings."
            ),
        )
        self.code_label = Gtk.Label(selectable=True, margin_top=6)
        self.code_label.add_css_class("title-1")
        self.code_group.add(self.code_label)
        self.open_button = Gtk.Button(label="Open Twitch", margin_top=6)
        self.open_button.add_css_class("suggested-action")
        self.open_button.connect("clicked", self._open_verification)
        self.code_group.add(self.open_button)
        self.code_group.set_visible(False)
        page.add(self.code_group)

        self.settings_group = Adw.PreferencesGroup(
            title="Application",
            description=self._client_id_help(),
        )
        self.client_id = Adw.EntryRow(
            title="Client ID", text=app.config.twitch.client_id
        )
        self.settings_group.add(self.client_id)
        page.add(self.settings_group)

        actions = Adw.PreferencesGroup()
        self.link_button = Gtk.Button(label="Connect a Twitch account", margin_top=6)
        self.link_button.add_css_class("suggested-action")
        self.link_button.connect("clicked", self._start_link)
        actions.add(self.link_button)
        self.unlink_button = Gtk.Button(label="Disconnect", margin_top=6)
        self.unlink_button.add_css_class("destructive-action")
        self.unlink_button.connect("clicked", self._unlink)
        actions.add(self.unlink_button)
        # Shown only after disconnecting. Twitch keeps the authorization in its
        # own list whatever this application does with the token, and there is
        # no API to remove it, so the honest end of the flow is to hand the
        # user the page where they can.
        self.connections_button = Gtk.Button(
            label="Remove it on Twitch too", margin_top=6, visible=False
        )
        self.connections_button.connect("clicked", self._open_connections)
        actions.add(self.connections_button)
        self.status = Gtk.Label(margin_top=10, wrap=True, justify=Gtk.Justification.CENTER)
        self.status.add_css_class("dim-label")
        actions.add(self.status)
        page.add(actions)

        view.set_content(page)
        self.set_content(view)

        # Kept as a bound method rather than a lambda: `unsubscribe` matches on
        # the callback itself, so a fresh lambda could never be removed.
        app.bus.subscribe("twitch.state", self._on_twitch_state)
        self.connect("close-request", self._on_close)
        self._refresh()

    def _on_twitch_state(self, _topic, _data) -> None:
        GLib.idle_add(self._refresh)

    # ---------- presentation ----------

    @staticmethod
    def _client_id_help() -> str:
        if DEFAULT_CLIENT_ID:
            return (
                "This application has its own Twitch Client ID, so you do not "
                "need one. Fill this in only if you want to use your own "
                "registered application."
            )
        return (
            "This build has no Client ID of its own, so you need to supply "
            "one. Create an application at dev.twitch.tv/console/apps with "
            "the OAuth Redirect URL http://localhost and the client type "
            "Public, then paste its Client ID here. It is a public "
            "identifier, not a secret."
        )

    def _refresh(self) -> bool:
        twitch = self.app.twitch
        linked = twitch.linked
        login = twitch.account
        self.account_row.set_title(
            f"Connected as {login}" if login else
            "Connected" if linked else "Not connected"
        )
        missing = twitch.missing_scopes() if linked else ()
        if missing:
            # An account linked before an action existed holds a token that
            # cannot perform it, and Twitch reports that as a 401 that reads
            # exactly like an expired token. Saying so here is the difference
            # between a fixable problem and an error nobody can act on.
            self.account_row.set_subtitle(
                "Connect again to allow: " + ", ".join(missing)
            )
        else:
            self.account_row.set_subtitle(
                "Keys that set the title, the category, clips and markers can "
                "use this account."
                if linked
                else "No Twitch key can do anything until an account is connected."
            )
        self.unlink_button.set_visible(linked)
        self.link_button.set_label(
            "Connect a different account" if linked else "Connect a Twitch account"
        )
        return False

    # ---------- linking ----------

    def _start_link(self, _btn) -> None:
        client_id = self.client_id.get_text().strip() or DEFAULT_CLIENT_ID
        if not client_id:
            self.status.set_label(
                "Enter a Twitch application Client ID first."
            )
            return
        self._persist_client_id()
        self._cancel.clear()
        self.link_button.set_sensitive(False)
        self.status.set_label("Asking Twitch for a code…")
        self._flow = threading.Thread(
            target=self._run_flow,
            args=(client_id,),
            daemon=True,
            name="twitch-link",
        )
        self._flow.start()

    def _persist_client_id(self) -> None:
        """Save the Client ID, and tell the live client about it.

        Saved before the flow rather than after it succeeds: a code that is
        never completed still leaves the value the user typed in place, so a
        second attempt does not start by asking for it again.
        """
        value = self.client_id.get_text().strip()
        if value == self.app.config.twitch.client_id:
            self.app.twitch.set_client_id(value)
            return
        self.app.config.twitch.client_id = value
        self.app.config.save()
        self.app.twitch.set_client_id(value)

    def _run_flow(self, client_id: str) -> None:
        """The whole device flow, on its own thread."""
        try:
            code = auth.request_device_code(client_id, SCOPES)
        except TwitchError as error:
            GLib.idle_add(self._flow_failed, str(error))
            return
        GLib.idle_add(self._show_code, code)
        try:
            tokens = auth.poll_for_tokens(
                client_id, code, should_stop=self._cancel.is_set
            )
        except TwitchError as error:
            GLib.idle_add(self._flow_failed, str(error))
            return
        if tokens is None:
            GLib.idle_add(
                self._flow_failed,
                "The code expired before it was entered. Try again."
                if not self._cancel.is_set()
                else "",
            )
            return
        GLib.idle_add(self._flow_succeeded, tokens)

    def _show_code(self, code: auth.DeviceCode) -> bool:
        self._verification_uri = code.open_uri
        self.code_label.set_label(code.user_code)
        self.code_group.set_visible(True)
        self.status.set_label("Waiting for you to authorize it on Twitch…")
        return False

    def _flow_succeeded(self, tokens: auth.Tokens) -> bool:
        self.code_group.set_visible(False)
        self.connections_button.set_visible(False)
        self.link_button.set_sensitive(True)
        self.app.twitch.link(tokens)
        self.status.set_label("Twitch account connected.")
        self._refresh()
        return False

    def _flow_failed(self, message: str) -> bool:
        self.code_group.set_visible(False)
        self.link_button.set_sensitive(True)
        self.status.set_label(message)
        return False

    def _open_verification(self, _btn) -> None:
        uri = getattr(self, "_verification_uri", "")
        if not uri:
            return
        Gtk.UriLauncher(uri=uri).launch(self, None, None)

    def _unlink(self, _btn) -> None:
        self._cancel.set()
        self.code_group.set_visible(False)
        self.app.twitch.unlink()
        # Deliberately not "disconnected", full stop. The tokens are gone from
        # this computer and have been revoked, but Twitch has no API to undo
        # the authorization itself, so it stays listed under Connections until
        # the user removes it there. Claiming otherwise would be the kind of
        # unearned reassurance the pre-flight board exists to avoid.
        self.status.set_label(
            "Disconnected here, and the tokens have been revoked. Twitch still "
            "lists the application under Connections until you remove it there."
        )
        self.connections_button.set_visible(True)

    def _open_connections(self, _btn) -> None:
        Gtk.UriLauncher(uri=CONNECTIONS_URL).launch(self, None, None)

    # ---------- lifetime ----------

    def _on_close(self, *_a) -> bool:
        """Stop the poll loop and drop the bus subscription.

        A dialog that closed while polling would keep a thread asking Twitch
        for an authorization nobody is going to give, and a subscriber left on
        the bus would touch widgets that no longer exist.
        """
        self._cancel.set()
        self.app.bus.unsubscribe("twitch.state", self._on_twitch_state)
        return False
