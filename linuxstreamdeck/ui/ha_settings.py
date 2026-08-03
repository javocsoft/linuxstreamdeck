"""Home Assistant server dialog: address, token, and a check that both work."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ..core import homeassistant as ha  # noqa: E402
from ..core import webrequest  # noqa: E402

# A saved token is shown as a fixed mask, never as itself. It is only a
# presence indicator: the request always uses the separately stored value, the
# same contract the AI provider keys follow.
TOKEN_MASK = "••••••••••••••••"


class HomeAssistantSettingsDialog(Adw.Window):
    def __init__(self, parent, app) -> None:
        super().__init__(
            transient_for=parent, modal=True, title="Home Assistant",
            default_width=480, default_height=520,
        )
        self.app = app
        self._client = app.home_assistant

        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())

        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(
            title="Server",
            description=(
                "The address of your Home Assistant, and a long-lived access "
                "token.\n"
                "In Home Assistant: click your user name (bottom left) → "
                "Security → Long-lived access tokens → Create token.\n"
                "The token is stored in your desktop keyring, never in the "
                "configuration file and never in an export."
            ),
        )
        self.url = Adw.EntryRow(
            title="Address", text=app.config.home_assistant.base_url
        )
        self.url.set_show_apply_button(False)
        self.token = Adw.PasswordEntryRow(title="Long-lived access token")
        self._has_token = bool(self._client.token())
        if self._has_token:
            self.token.set_text(TOKEN_MASK)
        for row in (self.url, self.token):
            group.add(row)
        page.add(group)

        actions = Adw.PreferencesGroup()
        self.save = Gtk.Button(label="Save and check", margin_top=6)
        self.save.add_css_class("suggested-action")
        self.save.connect("clicked", self._save)
        actions.add(self.save)
        self.forget = Gtk.Button(label="Forget this server", margin_top=6)
        self.forget.add_css_class("destructive-action")
        self.forget.connect("clicked", self._forget)
        actions.add(self.forget)
        self.status = Gtk.Label(margin_top=10, wrap=True)
        self.status.add_css_class("dim-label")
        actions.add(self.status)
        page.add(actions)

        view.set_content(page)
        self.set_content(view)
        self._describe()

    # ---------- what it says ----------

    def _describe(self, text: str = "") -> None:
        if text:
            self.status.set_label(text)
            return
        if self._client.configured():
            self.status.set_label(
                f"Connected to {self._client.base_url}"
            )
        elif self._client.base_url:
            self.status.set_label("No token saved yet.")
        else:
            self.status.set_label("No server set up yet.")
        self.forget.set_visible(
            bool(self._client.base_url) or self._has_token
        )

    # ---------- saving ----------

    def _save(self, _button) -> None:
        url = self.url.get_text().strip()
        if not url:
            self._describe("Enter the address of your Home Assistant.")
            return
        typed = self.token.get_text()
        # The mask is only a presence indicator, so leaving it untouched means
        # "keep the token I already saved" rather than "my token is bullets".
        token = "" if (self._has_token and typed == TOKEN_MASK) else typed.strip()
        if not token and not self._has_token:
            self._describe("Enter a long-lived access token.")
            return

        self.app.config.home_assistant.base_url = url
        self.app.config.save()
        self._client.configure(url)
        if token:
            self.app.ha_tokens.save(token)
            self._client.set_token(token)
            self._has_token = True
            self.token.set_text(TOKEN_MASK)

        self.save.set_sensitive(False)
        self._describe("Checking…")
        # Off the GTK thread: the server is on the network, and a house behind
        # a slow link would otherwise freeze the dialog for the timeout.
        webrequest.background(self._check)

    def _check(self) -> None:
        try:
            version = self._client.check()
            message = (
                f"Connected to Home Assistant {version}" if version
                else "Connected to Home Assistant"
            )
        except ha.HomeAssistantError as error:
            message = str(error)
        GLib.idle_add(self._checked, message)

    def _checked(self, message: str) -> bool:
        self.save.set_sensitive(True)
        self._describe(message)
        self.forget.set_visible(True)
        return False

    def _forget(self, _button) -> None:
        self.app.ha_tokens.clear()
        self._client.forget()
        self._client.configure("")
        self.app.config.home_assistant.base_url = ""
        self.app.config.save()
        self._has_token = False
        self.url.set_text("")
        self.token.set_text("")
        self._describe("Forgotten. The token has been removed from the keyring.")
