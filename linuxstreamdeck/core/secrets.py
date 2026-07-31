"""Secure credential storage through Secret Service (GNOME Keyring)."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

import gi

gi.require_version("Secret", "1")
from gi.repository import Secret  # noqa: E402

from .. import APP_ID  # noqa: E402

log = logging.getLogger(__name__)

PasswordCallback = Callable[[str, Exception | None], None]
StoreCallback = Callable[[bool, Exception | None], None]

_SCHEMA = Secret.Schema.new(
    f"{APP_ID}.OBS",
    Secret.SchemaFlags.NONE,
    {"service": Secret.SchemaAttributeType.STRING},
)
_ATTRIBUTES = {"service": "obs-websocket"}
_LABEL = "LinuxStreamDeck OBS password"

_AI_SCHEMA = Secret.Schema.new(
    f"{APP_ID}.AI",
    Secret.SchemaFlags.NONE,
    {"provider": Secret.SchemaAttributeType.STRING},
)
_AI_LABELS = {
    "openai": "LinuxStreamDeck OpenAI API key",
    "anthropic": "LinuxStreamDeck Claude API key",
}

_TWITCH_SCHEMA = Secret.Schema.new(
    f"{APP_ID}.Twitch",
    Secret.SchemaFlags.NONE,
    {"account": Secret.SchemaAttributeType.STRING},
)
_TWITCH_ATTRIBUTES = {"account": "twitch"}
_TWITCH_LABEL = "LinuxStreamDeck Twitch account"


class SecretStore:
    """Asynchronous access to the desktop user's secure password collection."""

    def __init__(self, backend=Secret) -> None:
        self._backend = backend

    def lookup(self, callback: PasswordCallback) -> None:
        try:
            self._backend.password_lookup(
                _SCHEMA,
                _ATTRIBUTES,
                None,
                self._lookup_finished,
                callback,
            )
        except Exception as error:
            callback("", error)

    def store(self, password: str, callback: StoreCallback) -> None:
        try:
            if password:
                self._backend.password_store(
                    _SCHEMA,
                    _ATTRIBUTES,
                    Secret.COLLECTION_DEFAULT,
                    _LABEL,
                    password,
                    None,
                    self._store_finished,
                    callback,
                )
            else:
                self._backend.password_clear(
                    _SCHEMA,
                    _ATTRIBUTES,
                    None,
                    self._clear_finished,
                    callback,
                )
        except Exception as error:
            callback(False, error)

    def _lookup_finished(self, _source, result, callback: PasswordCallback) -> None:
        try:
            password = self._backend.password_lookup_finish(result) or ""
        except Exception as error:
            callback("", error)
            return
        callback(password, None)

    def _store_finished(self, _source, result, callback: StoreCallback) -> None:
        try:
            stored = bool(self._backend.password_store_finish(result))
        except Exception as error:
            callback(False, error)
            return
        callback(stored, None if stored else RuntimeError("Password was not stored"))

    def _clear_finished(self, _source, result, callback: StoreCallback) -> None:
        try:
            cleared = bool(self._backend.password_clear_finish(result))
        except Exception as error:
            callback(False, error)
            return
        callback(cleared, None if cleared else RuntimeError("Password was not cleared"))


class ApiKeyStore:
    """Asynchronous, provider-scoped AI API key storage."""

    def __init__(self, backend=Secret) -> None:
        self._backend = backend

    def lookup(self, provider: str, callback: PasswordCallback) -> None:
        try:
            attributes = self._attributes(provider)
            self._backend.password_lookup(
                _AI_SCHEMA,
                attributes,
                None,
                self._lookup_finished,
                callback,
            )
        except Exception as error:
            callback("", error)

    def store(
        self, provider: str, api_key: str, callback: StoreCallback
    ) -> None:
        try:
            attributes = self._attributes(provider)
            if api_key:
                self._backend.password_store(
                    _AI_SCHEMA,
                    attributes,
                    Secret.COLLECTION_DEFAULT,
                    _AI_LABELS[provider],
                    api_key,
                    None,
                    self._store_finished,
                    callback,
                )
            else:
                self._backend.password_clear(
                    _AI_SCHEMA,
                    attributes,
                    None,
                    self._clear_finished,
                    callback,
                )
        except Exception as error:
            callback(False, error)

    @staticmethod
    def _attributes(provider: str) -> dict[str, str]:
        if provider not in _AI_LABELS:
            raise ValueError("Unsupported AI provider")
        return {"provider": provider}

    def _lookup_finished(self, _source, result, callback: PasswordCallback) -> None:
        try:
            api_key = self._backend.password_lookup_finish(result) or ""
        except Exception as error:
            callback("", error)
            return
        callback(api_key, None)

    def _store_finished(self, _source, result, callback: StoreCallback) -> None:
        try:
            stored = bool(self._backend.password_store_finish(result))
        except Exception as error:
            callback(False, error)
            return
        callback(stored, None if stored else RuntimeError("API key was not stored"))

    def _clear_finished(self, _source, result, callback: StoreCallback) -> None:
        try:
            cleared = bool(self._backend.password_clear_finish(result))
        except Exception as error:
            callback(False, error)
            return
        callback(cleared, None if cleared else RuntimeError("API key was not cleared"))


class TwitchTokenStore:
    """The linked Twitch account's tokens.

    Synchronous, unlike the two stores above, because its caller is not the GTK
    thread: the Twitch client reads and rewrites these tokens from its own
    worker while refreshing them, and a callback-based lookup there would need
    a main loop it does not have. Blocking that worker is harmless — the worst
    case is a locked keyring delaying a viewer count.

    The pair is stored as one JSON item rather than as two secrets so a refresh
    replaces both together. Twitch refresh tokens are single use, so a write
    that saved the new access token and lost the new refresh token would leave
    an account that looks linked and can never renew.
    """

    def __init__(self, backend=Secret) -> None:
        self._backend = backend

    def load(self) -> dict:
        """The stored token blob, or an empty mapping when there is none."""
        try:
            raw = self._backend.password_lookup_sync(
                _TWITCH_SCHEMA, _TWITCH_ATTRIBUTES, None
            )
        except Exception:
            log.debug("Could not read the Twitch tokens", exc_info=True)
            return {}
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            log.debug("The stored Twitch tokens could not be decoded")
            return {}
        return value if isinstance(value, dict) else {}

    def save(self, tokens: dict) -> bool:
        """Persist the token blob, answering whether it really was stored."""
        try:
            return bool(
                self._backend.password_store_sync(
                    _TWITCH_SCHEMA,
                    _TWITCH_ATTRIBUTES,
                    Secret.COLLECTION_DEFAULT,
                    _TWITCH_LABEL,
                    json.dumps(tokens),
                    None,
                )
            )
        except Exception:
            log.exception("Could not store the Twitch tokens")
            return False

    def clear(self) -> bool:
        try:
            return bool(
                self._backend.password_clear_sync(
                    _TWITCH_SCHEMA, _TWITCH_ATTRIBUTES, None
                )
            )
        except Exception:
            log.debug("Could not clear the Twitch tokens", exc_info=True)
            return False
