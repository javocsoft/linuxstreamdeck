"""Device code authorization, and the token lifecycle that follows it.

Twitch offers this flow for clients that cannot keep a secret, which is exactly
a desktop application: there is no client secret, no redirect URI and no local
web server to open a port for. The user reads a short code off the screen,
types it at twitch.tv/activate, and this module polls until Twitch says yes.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, replace
from typing import Any, Callable

from . import http
from .constants import (
    DEVICE_ERROR_MESSAGES,
    DEVICE_GRANT,
    DEVICE_URL,
    GENERIC_DEVICE_ERROR,
    PENDING_CODE,
    REFRESH_MARGIN_SECONDS,
    REVOKE_URL,
    SLOW_DOWN_CODE,
    TOKEN_URL,
    VALIDATE_URL,
)

log = logging.getLogger(__name__)

# The floor for how often the poll loop asks. Twitch states an interval in its
# device response and this only stops a malformed one from turning the loop
# into a hot spin.
MIN_POLL_SECONDS = 1.0
# A device code is short lived; this bounds the wait even when Twitch omits it.
MAX_FLOW_SECONDS = 1800.0


class TwitchAuthError(http.TwitchError):
    """Authorization did not complete, and retrying the same code will not help."""


@dataclass(frozen=True)
class DeviceCode:
    """What the user has to be shown to authorize this application."""

    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int = 1800
    interval: int = 5
    # The same page with the code already filled in, when Twitch offers it.
    verification_uri_complete: str = ""

    @property
    def open_uri(self) -> str:
        """Where the button should send someone: the shortest correct path."""
        return self.verification_uri_complete or self.verification_uri


@dataclass(frozen=True)
class Tokens:
    """A linked account.

    `expires_at` is wall-clock rather than monotonic on purpose: it is written
    to the keyring and read back in a later process, possibly after a reboot,
    and a monotonic value means nothing across either.
    """

    access: str
    refresh: str
    expires_at: float = 0.0
    login: str = ""
    user_id: str = ""
    scopes: tuple[str, ...] = ()

    def expiring(self, now: float | None = None) -> bool:
        """Whether this token is close enough to expiry to renew it early."""
        if not self.expires_at:
            return False
        moment = time.time() if now is None else now
        return moment >= self.expires_at - REFRESH_MARGIN_SECONDS

    def to_dict(self) -> dict[str, Any]:
        return {
            "access": self.access,
            "refresh": self.refresh,
            "expires_at": self.expires_at,
            "login": self.login,
            "user_id": self.user_id,
            "scopes": list(self.scopes),
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "Tokens | None":
        """Rebuild a stored pair, answering None for anything unusable.

        A token blob that cannot be read is treated as no account rather than
        as an error: the only sensible repair is to link again, and a stored
        value is not something the user can be asked to fix.
        """
        if not isinstance(raw, dict):
            return None
        access = str(raw.get("access") or "")
        refresh = str(raw.get("refresh") or "")
        if not access or not refresh:
            return None
        scopes = raw.get("scopes")
        try:
            expires_at = float(raw.get("expires_at") or 0.0)
        except (TypeError, ValueError):
            expires_at = 0.0
        return cls(
            access=access,
            refresh=refresh,
            expires_at=expires_at,
            login=str(raw.get("login") or ""),
            user_id=str(raw.get("user_id") or ""),
            scopes=tuple(str(s) for s in scopes) if isinstance(scopes, list) else (),
        )


def request_device_code(client_id: str, scopes: tuple[str, ...]) -> DeviceCode:
    """Ask Twitch to start an authorization the user can complete elsewhere."""
    if not client_id:
        raise TwitchAuthError("No Twitch application Client ID is configured")
    try:
        answer = http.request_json(
            "POST",
            DEVICE_URL,
            # This endpoint spells the parameter `scopes`, unlike the rest of
            # OAuth.
            form={"client_id": client_id, "scopes": " ".join(scopes)},
        )
    except http.TwitchHTTPError as error:
        # A refusal here is almost always a Client ID Twitch does not know, and
        # it arrives as a code rather than as a sentence.
        raise TwitchAuthError(describe_error(error.message)) from error
    code = str(answer.get("device_code") or "")
    user_code = str(answer.get("user_code") or "")
    uri = str(answer.get("verification_uri") or "")
    complete = str(answer.get("verification_uri_complete") or "")
    if not code or not user_code or not (uri or complete):
        raise TwitchAuthError("Twitch did not return a usable authorization code")
    return DeviceCode(
        device_code=code,
        user_code=user_code,
        verification_uri=uri or complete,
        # Twitch also offers a URL with the code already in it. Opening that
        # one saves retyping six characters that were just read off a screen,
        # which is exactly where this flow goes wrong; the plain URL and the
        # code stay visible for anyone finishing on a phone.
        verification_uri_complete=complete,
        expires_in=_positive_int(answer.get("expires_in"), 1800),
        interval=_positive_int(answer.get("interval"), 5),
    )


def poll_for_tokens(
    client_id: str,
    code: DeviceCode,
    *,
    should_stop: Callable[[], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> Tokens | None:
    """Wait for the user to authorize, answering None if they never do.

    Twitch reports a still-pending authorization as HTTP 400 carrying the words
    "authorization pending" rather than the structured `authorization_pending`
    the device flow specifies, so the message is what distinguishes "not yet"
    from a real refusal. Treating every 400 as pending would loop forever on a
    revoked code; treating every 400 as fatal would end the flow the instant it
    began.

    `should_stop` lets the dialog cancel it, and both the clock and the sleep
    are injected so the loop is testable without spending real seconds.
    """
    deadline = now() + min(float(code.expires_in), MAX_FLOW_SECONDS)
    interval = max(MIN_POLL_SECONDS, float(code.interval))
    while True:
        if should_stop is not None and should_stop():
            return None
        if now() >= deadline:
            return None
        sleep(interval)
        if should_stop is not None and should_stop():
            return None
        try:
            answer = http.request_json(
                "POST",
                TOKEN_URL,
                form={
                    "client_id": client_id,
                    "device_code": code.device_code,
                    "grant_type": DEVICE_GRANT,
                },
            )
        except http.TwitchHTTPError as error:
            if error.status == 400 and _is_pending(error.message):
                continue
            if error.status == 429 or _is_slow_down(error.message):
                # Backing off rather than giving up: the user has not done
                # anything wrong, this loop simply asked too often. `slow_down`
                # is the device flow's own way of saying so, and it arrives as
                # a 400 like every other outcome.
                interval = min(interval * 2, 30.0)
                continue
            raise TwitchAuthError(describe_error(error.message)) from error
        return _tokens_from(answer)


def refresh_tokens(client_id: str, refresh_token: str) -> Tokens:
    """Exchange a refresh token for a new pair.

    Twitch refresh tokens are **single use**: the one spent here is dead the
    moment this returns, and the answer carries its replacement. The caller has
    to persist the result before relying on it, or a crash in between leaves a
    stored token that can no longer be renewed and an account that appears
    linked while every request fails.
    """
    if not refresh_token:
        raise TwitchAuthError("There is no Twitch refresh token to renew")
    try:
        answer = http.request_json(
            "POST",
            TOKEN_URL,
            form={
                "client_id": client_id,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
    except http.TwitchHTTPError as error:
        if error.status in (400, 401):
            raise TwitchAuthError(
                "Twitch no longer accepts the stored authorization; link the "
                "account again"
            ) from error
        raise
    return _tokens_from(answer)


def validate(access_token: str) -> dict[str, Any]:
    """Identify the account behind a token and confirm it is still good.

    Also the cheapest way to learn the user id, which every Helix call in this
    integration needs and which the token response does not carry.
    """
    return http.request_json(
        "GET",
        VALIDATE_URL,
        headers={"Authorization": f"OAuth {access_token}"},
    )


def identify(tokens: Tokens) -> Tokens:
    """Fill in the login, user id and scopes of a freshly obtained pair."""
    answer = validate(tokens.access)
    scopes = answer.get("scopes")
    expires_in = _positive_int(answer.get("expires_in"), 0)
    return replace(
        tokens,
        login=str(answer.get("login") or tokens.login),
        user_id=str(answer.get("user_id") or tokens.user_id),
        scopes=(
            tuple(str(s) for s in scopes) if isinstance(scopes, list) else tokens.scopes
        ),
        expires_at=(time.time() + expires_in) if expires_in else tokens.expires_at,
    )


def revoke(client_id: str, access_token: str) -> None:
    """Invalidate the access token this application was given.

    **The access token only, deliberately.** Revoking the refresh token beside
    it was tried and had to be undone: RFC 7009 says an authorization server
    should invalidate every token issued from the same grant, and Twitch does
    — so disconnecting an account tore down the whole authorization. Someone
    who then reconnected got a session whose EventSub subscriptions were
    revoked with `authorization_revoked` seconds later, which is exactly as
    baffling as it sounds. An access token expires in about four hours by
    itself; a torn-down grant does not repair.

    It also does not remove the application from the user's Twitch connections
    page: Twitch has no API for that, and only the user can, at
    `CONNECTIONS_URL`. And it is best effort — unlinking has already removed
    the stored tokens, so a network failure here must never stop somebody
    disconnecting.
    """
    if not client_id or not access_token:
        return
    try:
        http.request_json(
            "POST", REVOKE_URL, form={"client_id": client_id, "token": access_token}
        )
    except http.TwitchError:
        log.debug("Could not revoke the Twitch token", exc_info=True)


def missing_scopes(tokens: Tokens, wanted: tuple[str, ...]) -> tuple[str, ...]:
    """Scopes an existing authorization lacks.

    A user who linked before a new action existed holds a token that cannot
    perform it, and Twitch answers that with a 401 that reads exactly like an
    expired token. Naming the gap is the difference between "link again" and an
    error nobody can act on.
    """
    held = set(tokens.scopes)
    if not held:
        # An authorization whose scopes are unknown is not evidence of a gap.
        return ()
    return tuple(scope for scope in wanted if scope not in held)


def _tokens_from(answer: dict[str, Any]) -> Tokens:
    access = str(answer.get("access_token") or "")
    refresh = str(answer.get("refresh_token") or "")
    if not access or not refresh:
        raise TwitchAuthError("Twitch did not return a usable token")
    expires_in = _positive_int(answer.get("expires_in"), 0)
    scopes = answer.get("scope")
    return Tokens(
        access=access,
        refresh=refresh,
        expires_at=(time.time() + expires_in) if expires_in else 0.0,
        scopes=tuple(str(s) for s in scopes) if isinstance(scopes, list) else (),
    )


def _normalize(message: str) -> str:
    """An error code in a form that can be compared.

    RFC 8628 codes arrive as `authorization_pending`, but nothing guarantees
    the separator, so underscores and hyphens are flattened to spaces before
    matching. Comparing the raw string is what made the first version treat
    every "not yet" as a refusal.
    """
    text = (message or "").strip().lower()
    for separator in ("_", "-"):
        text = text.replace(separator, " ")
    return " ".join(text.split())


def _is_pending(message: str) -> bool:
    return PENDING_CODE in _normalize(message)


def _is_slow_down(message: str) -> bool:
    return SLOW_DOWN_CODE in _normalize(message)


def describe_error(message: str) -> str:
    """A sentence for something that went wrong, never an identifier.

    Twitch mixes two kinds of text in the same field: machine codes such as
    `expired_token` from the device flow, and real prose such as
    "Missing scope: clips:edit" from Helix. Prose is worth showing as it is;
    a code is not, because it tells whoever is trying to connect nothing they
    can act on, so an unrecognised one becomes the generic sentence.
    """
    text = (message or "").strip()
    if not text:
        return GENERIC_DEVICE_ERROR
    known = DEVICE_ERROR_MESSAGES.get(_normalize(text))
    if known:
        return known
    if _looks_like_code(text):
        return GENERIC_DEVICE_ERROR
    return text


def _looks_like_code(text: str) -> bool:
    """Whether this reads as an identifier rather than as something written."""
    return " " not in text.strip() and ("_" in text or text.islower())


def _positive_int(value: Any, fallback: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return number if number > 0 else fallback
