"""Secure credential storage through Secret Service (GNOME Keyring)."""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Secret", "1")
from gi.repository import Secret  # noqa: E402

from .. import APP_ID  # noqa: E402

PasswordCallback = Callable[[str, Exception | None], None]
StoreCallback = Callable[[bool, Exception | None], None]

_SCHEMA = Secret.Schema.new(
    f"{APP_ID}.OBS",
    Secret.SchemaFlags.NONE,
    {"service": Secret.SchemaAttributeType.STRING},
)
_ATTRIBUTES = {"service": "obs-websocket"}
_LABEL = "LinuxStreamDeck OBS password"


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
