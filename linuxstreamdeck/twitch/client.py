"""The Twitch connection: tokens, Helix requests and one cached channel snapshot.

The rule this module exists to enforce is that **`feedback()` never performs a
network request**. Key feedback is resolved while composing a key image, on a
render worker, and a Helix call there would hold that worker for the round trip
to Twitch — far worse than the OBS captures that already had to be cached.
`channel()` therefore only ever returns what is already known and schedules its
own refresh elsewhere, so a viewer count key costs a render nothing.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from . import auth
from .constants import (
    DEFAULT_CLIENT_ID,
    HELIX_BASE,
    SCOPES,
)
from .http import (
    TwitchError,
    TwitchHTTPError,
    TwitchScopeError,
    request_bytes,
    request_json,
)

log = logging.getLogger(__name__)

# How old a snapshot may be before `channel()` schedules a new one. Twitch
# aggregates viewer counts on its own side and does not move faster than this,
# so asking more often spends requests without showing anything new.
CHANNEL_TTL = 20.0
# How old a snapshot may be before it stops being shown at all. A brief network
# failure keeps the last value, which is what makes a key feel steady; a
# sustained one blanks it, because a number that stopped being true is worse
# than no number.
CHANNEL_STALE = 90.0
# Resolved category names, so a key pressed repeatedly does not search again.
CATEGORY_CACHE_LIMIT = 32
# Suggestion lists, keyed by what was typed. Someone correcting a typo walks
# back over prefixes they have already asked about, so this is worth keeping.
SEARCH_CACHE_LIMIT = 64
# Box art thumbnails, keyed by address. A few kilobytes each, and the same
# handful of categories come back on every prefix of the same word.
ART_CACHE_LIMIT = 128
# Twitch box art is 3:4. Small enough to sit in a suggestion row without
# turning each keystroke into a meaningful download.
BOX_ART_SIZE = (40, 53)
# Channel ids resolved from names, for raids. The same few channels get raided
# again and again, so the lookup is worth remembering between presses.
USER_CACHE_LIMIT = 64
# Twitch's own limit; a longer announcement is refused outright, and losing the
# message over its tail would be a poor trade.
MAX_ANNOUNCEMENT_CHARS = 500
# What Twitch accepts for an ad break, in seconds.
COMMERCIAL_LENGTHS = (30, 60, 90, 120, 150, 180)
# How an announcement can be highlighted.
ANNOUNCEMENT_COLORS = ("primary", "blue", "green", "orange", "purple")
# Who Twitch lets run commercials. An ordinary account reports an empty
# broadcaster type, and Twitch refuses its ad requests in ways that do not say
# so — see `can_run_ads`.
ADS_BROADCASTER_TYPES = ("affiliate", "partner")
# Profile pictures, keyed by account id. A chat key shows whoever is waiting,
# and the same handful of people write again and again during one stream.
AVATAR_CACHE_LIMIT = 128


@dataclass(frozen=True)
class CategorySuggestion:
    """One row of the editor's category list: a name and its artwork.

    The artwork is what makes the list usable at a glance. A search for
    "detroit" answers "Detroit", "Detroit: Become Human" and "The Detroit
    After", which are hard to tell apart as words and immediate as pictures.
    """

    name: str
    box_art_url: str = ""


class TwitchClient:
    """Everything Twitch-facing, with the token lifecycle it implies."""

    def __init__(self, bus, store=None, client_id: str = "") -> None:
        self.bus = bus
        self._store = store
        self._lock = threading.Lock()
        # Held only across a token renewal, never across an ordinary request:
        # a refresh token is single use, so two workers renewing at once would
        # spend the same one twice and lose the account.
        self._renew_lock = threading.Lock()
        self._tokens: auth.Tokens | None = None
        self._client_id = client_id or DEFAULT_CLIENT_ID
        self._loaded = False
        self._channel: dict[str, Any] = {}
        self._channel_at = 0.0
        self._channel_pending = False
        self._categories: dict[str, str] = {}
        self._searches: dict[str, list[CategorySuggestion]] = {}
        self._art: dict[str, bytes | None] = {}
        self._users: dict[str, str] = {}
        # None until established: "not looked up" must stay distinct from "an
        # ordinary account", or a failed lookup would disable a working key.
        self._broadcaster_type: str | None = None
        self._avatars: dict[str, bytes | None] = {}
        self._stopping = threading.Event()
        # One worker: every background refresh here is a Twitch request, and
        # serializing them keeps a slow answer from multiplying into a burst.
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="twitch")

    # ---------- lifecycle ----------

    def start(self) -> None:
        """Read the stored account, off the calling thread.

        The keyring can block while it is unlocked, and this runs during window
        creation, so it must not be done inline.
        """
        self._submit(self._load_account)

    def stop(self) -> None:
        self._stopping.set()
        self._pool.shutdown(wait=False)

    def _submit(self, fn) -> None:
        if self._stopping.is_set():
            return
        try:
            self._pool.submit(fn)
        except RuntimeError:
            # Submitted during shutdown; nothing left to refresh.
            log.debug("Twitch work submitted after shutdown")

    def _load_account(self) -> None:
        stored = self._store.load() if self._store is not None else {}
        tokens = auth.Tokens.from_dict(stored)
        with self._lock:
            self._tokens = tokens
            self._loaded = True
        if tokens is None:
            self._announce()
            return
        # A stored token says who it belongs to, but only Twitch can say
        # whether it is still valid, and the answer also supplies the user id.
        try:
            self._tokens = self._ensure_identity(tokens)
        except auth.TwitchAuthError:
            self._drop_account("The stored Twitch authorization is no longer valid")
            return
        except TwitchError:
            # Unreachable is not unlinked: keep the account and try later.
            log.debug("Could not validate the Twitch account yet", exc_info=True)
        self._announce()

    # ---------- account state ----------

    @property
    def client_id(self) -> str:
        with self._lock:
            return self._client_id

    def set_client_id(self, client_id: str) -> None:
        with self._lock:
            self._client_id = client_id or DEFAULT_CLIENT_ID

    @property
    def linked(self) -> bool:
        """Whether a Twitch account is available to act on."""
        with self._lock:
            return self._tokens is not None

    @property
    def account(self) -> str:
        with self._lock:
            return self._tokens.login if self._tokens else ""

    @property
    def scopes(self) -> tuple[str, ...]:
        with self._lock:
            return self._tokens.scopes if self._tokens else ()

    def missing_scopes(self) -> tuple[str, ...]:
        with self._lock:
            tokens = self._tokens
        return auth.missing_scopes(tokens, SCOPES) if tokens else ()

    def link(self, tokens: auth.Tokens) -> None:
        """Adopt a freshly authorized account and persist it."""
        identified = tokens
        try:
            identified = auth.identify(tokens)
        except TwitchError:
            log.debug("Could not identify the new Twitch account", exc_info=True)
        self._persist(identified)
        with self._lock:
            self._channel = {}
            self._channel_at = 0.0
            self._broadcaster_type = None
        missing = self.missing_scopes()
        log.info(
            "Twitch account linked as %s; granted %d of %d permissions%s",
            identified.login or "?", len(SCOPES) - len(missing), len(SCOPES),
            f" (missing {', '.join(missing)})" if missing else "",
        )
        self._announce()

    def unlink(self) -> None:
        """Forget the account here and invalidate its tokens on Twitch.

        What this cannot do is remove the application from the user's Twitch
        connections page. Twitch has no API for it — revoking kills the token
        and leaves the link — so the dialog points the user at that page rather
        than claiming the disconnection is complete.
        """
        with self._lock:
            tokens = self._tokens
            client_id = self._client_id
            self._tokens = None
            self._channel = {}
            self._channel_at = 0.0
            self._broadcaster_type = None
        if self._store is not None:
            self._store.clear()
        if tokens is not None:
            self._submit(lambda: self._revoke_if_still_gone(client_id, tokens))
        self._announce()

    def _revoke_if_still_gone(self, client_id: str, tokens: auth.Tokens) -> None:
        """Give back the old token, unless an account was linked meanwhile.

        Revoking runs off the calling thread so the dialog does not wait on the
        network, which means it can land after somebody has already reconnected
        — and a revocation arriving then tore down the authorization they had
        just granted. The account they now hold is the one that matters.
        """
        if self.linked:
            log.debug("Not revoking: an account was linked in the meantime")
            return
        auth.revoke(client_id, tokens.access)

    def _drop_account(self, reason: str) -> None:
        with self._lock:
            self._tokens = None
            self._channel = {}
            self._channel_at = 0.0
        if self._store is not None:
            self._store.clear()
        log.warning("Twitch account dropped: %s", reason)
        self.bus.emit("status", text=reason)
        self._announce()

    def _announce(self) -> None:
        self.bus.emit("twitch.state", linked=self.linked, login=self.account)

    def _persist(self, tokens: auth.Tokens) -> None:
        """Store a token pair, then adopt it.

        The order matters and is the whole reason this is a method: a refresh
        token is spent the moment it is used, so a pair that was adopted but
        not stored would be lost on the next start with no way to renew.
        """
        if self._store is not None:
            self._store.save(tokens.to_dict())
        with self._lock:
            self._tokens = tokens

    # ---------- the cached snapshot ----------

    def channel(self) -> dict[str, Any]:
        """What is currently known about the channel. Never performs a request.

        Returns an empty mapping when nothing is known yet or when what is
        known has gone stale, so a caller showing a number can tell the
        difference between "offline" and "no longer being told".
        """
        now = time.monotonic()
        with self._lock:
            linked = self._tokens is not None
            snapshot = dict(self._channel)
            age = now - self._channel_at if self._channel_at else None
            wanted = linked and (age is None or age >= CHANNEL_TTL)
            schedule = wanted and not self._channel_pending
            if schedule:
                self._channel_pending = True
        if schedule:
            self._submit(self._refresh_channel)
        if age is not None and age >= CHANNEL_STALE:
            return {}
        return snapshot

    def _refresh_channel(self) -> None:
        try:
            snapshot = self._read_channel()
        except auth.TwitchAuthError as error:
            self._drop_account(str(error))
            return
        except TwitchError:
            # Left alone deliberately: the previous snapshot keeps being served
            # until CHANNEL_STALE, so one failed request is invisible while a
            # lasting outage still blanks the key.
            log.debug("Could not refresh the Twitch channel", exc_info=True)
            return
        finally:
            with self._lock:
                self._channel_pending = False
        with self._lock:
            self._channel = snapshot
            self._channel_at = time.monotonic()

    def _read_channel(self) -> dict[str, Any]:
        user_id = self._user_id()
        snapshot: dict[str, Any] = {
            "live": False,
            "viewers": None,
            "title": "",
            "category": "",
            "started_at": None,
            "followers": None,
        }
        stream = self._get("/streams", {"user_id": user_id})
        entries = stream.get("data")
        if isinstance(entries, list) and entries:
            first = entries[0] if isinstance(entries[0], dict) else {}
            snapshot["live"] = True
            snapshot["viewers"] = _as_int(first.get("viewer_count"))
            snapshot["title"] = str(first.get("title") or "")
            snapshot["category"] = str(first.get("game_name") or "")
            snapshot["started_at"] = _as_epoch(first.get("started_at"))
        # Asked even while live: this is the authoritative title and category,
        # and it is the only source of them when the channel is offline, which
        # is exactly when someone is setting them before going on air.
        channel = self._get("/channels", {"broadcaster_id": user_id})
        entries = channel.get("data")
        if isinstance(entries, list) and entries and isinstance(entries[0], dict):
            snapshot["title"] = str(entries[0].get("title") or snapshot["title"])
            snapshot["category"] = str(
                entries[0].get("game_name") or snapshot["category"]
            )
        try:
            followers = self._get(
                "/channels/followers", {"broadcaster_id": user_id, "first": 1}
            )
            snapshot["followers"] = _as_int(followers.get("total"))
        except TwitchHTTPError as error:
            # The one field with its own scope. An account linked before this
            # feature existed simply has no follower count; that is not a
            # reason to lose the viewer count next to it.
            if error.status not in (401, 403):
                raise
            log.debug("No permission to read the Twitch follower count")
        return snapshot

    # ---------- actions ----------

    def set_title(self, title: str) -> None:
        text = (title or "").strip()
        if not text:
            raise TwitchError("The stream title is empty")
        self._patch("/channels", {"broadcaster_id": self._user_id()}, {"title": text})
        self._invalidate()

    def set_category(self, name: str) -> str:
        """Set the stream category, resolving its name to an id.

        Returns the name Twitch matched, which is not always what was typed:
        the search is what turns "gta v" into "Grand Theft Auto V", and saying
        which one was applied is the difference between a key that worked and
        a key that silently set the wrong game.
        """
        wanted = (name or "").strip()
        if not wanted:
            raise TwitchError("No Twitch category was given")
        game_id, matched = self._resolve_category(wanted)
        self._patch(
            "/channels", {"broadcaster_id": self._user_id()}, {"game_id": game_id}
        )
        self._invalidate()
        return matched

    def create_clip(self) -> str:
        """Clip the last few seconds; answers the URL to edit it."""
        answer = self._post("/clips", {"broadcaster_id": self._user_id()})
        entries = answer.get("data")
        if isinstance(entries, list) and entries and isinstance(entries[0], dict):
            edit_url = str(entries[0].get("edit_url") or "")
            if edit_url:
                return edit_url
        return ""

    def broadcaster_type(self, refresh: bool = False) -> str | None:
        """"partner", "affiliate", or "" for an ordinary account.

        None means it has not been established, which is deliberately distinct
        from "ordinary": nothing should be refused because a lookup failed.
        """
        with self._lock:
            known = self._broadcaster_type
        if known is not None and not refresh:
            return known
        try:
            answer = self._get("/users")
        except TwitchError:
            log.debug("Could not read the Twitch broadcaster type", exc_info=True)
            return None
        entries = answer.get("data")
        if not (isinstance(entries, list) and entries
                and isinstance(entries[0], dict)):
            return None
        found = str(entries[0].get("broadcaster_type") or "")
        with self._lock:
            self._broadcaster_type = found
        return found

    def can_run_ads(self, refresh: bool = False) -> bool | None:
        """Whether this account is allowed to run commercials at all.

        Twitch will not say so usefully. Its own issue tracker has this
        endpoint returning a misleading 429 to an ordinary account, and
        sometimes a plain success — a key that reported "ad break started" and
        did nothing. So eligibility is established here instead, from the one
        field that states it.
        """
        kind = self.broadcaster_type(refresh)
        return None if kind is None else kind in ADS_BROADCASTER_TYPES

    def start_commercial(self, seconds: int) -> tuple[int, int]:
        """Start an ad break, answering how long it runs and the cooldown.

        The cooldown matters more than the confirmation: Twitch refuses another
        break until it passes, so a key that only said "started" would leave
        someone pressing it again into a refusal.
        """
        # Asked before sending, because Twitch's own answer cannot be trusted
        # to say it: an ordinary account gets a cooldown refusal it can never
        # wait out, or a success for an ad that never ran.
        if self.can_run_ads() is False:
            raise TwitchError(
                "This Twitch account cannot run ads. Only Affiliates and "
                "Partners can, and this one is neither."
            )
        answer = self._post(
            "/channels/commercial",
            body={"broadcaster_id": self._user_id(), "length": int(seconds)},
        )
        entries = answer.get("data")
        first = entries[0] if isinstance(entries, list) and entries else {}
        if not isinstance(first, dict):
            first = {}
        return (
            _as_int(first.get("length")) or int(seconds),
            _as_int(first.get("retry_after")) or 0,
        )

    def start_raid(self, channel: str) -> str:
        """Offer a raid to another channel; answers the name it resolved.

        Twitch does not move anyone here: it opens the countdown on the
        broadcaster's own chat, which they then confirm. Saying so is the
        difference between a key someone trusts and one they are afraid of.
        """
        login = (channel or "").strip().lstrip("@")
        if not login:
            raise TwitchError("No channel to raid was given")
        target = self.find_user(login)
        if not target:
            raise TwitchError(f"Twitch has no channel called {login!r}")
        self._post(
            "/raids",
            params={
                "from_broadcaster_id": self._user_id(),
                "to_broadcaster_id": target,
            },
        )
        return login

    def cancel_raid(self) -> None:
        self._request(
            "DELETE", "/raids", params={"broadcaster_id": self._user_id()}
        )

    def announce(self, message: str, color: str = "primary") -> None:
        text = (message or "").strip()
        if not text:
            raise TwitchError("The announcement is empty")
        user_id = self._user_id()
        self._post(
            "/chat/announcements",
            # The moderator is this account acting on its own channel, which is
            # what the scope authorizes; both ids are required all the same.
            params={"broadcaster_id": user_id, "moderator_id": user_id},
            body={"message": text[:MAX_ANNOUNCEMENT_CHARS], "color": color},
        )

    def user_id(self) -> str:
        """The linked account's numeric id, or empty when there is none.

        The public form of `_user_id`, which raises: the event session wants to
        ask without being interrupted when no account is linked yet.
        """
        try:
            return self._user_id()
        except TwitchError:
            return ""

    def create_subscription(
        self, name: str, version: str, condition: dict, session_id: str
    ) -> None:
        """Ask Twitch to send one kind of event to an open session."""
        from .eventsub import subscription_body

        self._post(
            "/eventsub/subscriptions",
            body=subscription_body(name, version, condition, session_id),
        )

    def avatar(self, user_id: str) -> bytes | None:
        """One person's profile picture, cached by account.

        Fetched only where blocking is allowed. Key feedback reads
        `cached_avatar()` instead, because it runs on a render worker.
        """
        key = (user_id or "").strip()
        if not key:
            return None
        with self._lock:
            if key in self._avatars:
                return self._avatars[key]
        url = ""
        try:
            answer = self._get("/users", {"id": key})
            entries = answer.get("data")
            if isinstance(entries, list) and entries and isinstance(entries[0], dict):
                url = str(entries[0].get("profile_image_url") or "")
        except TwitchError:
            log.debug("Could not read a Twitch profile", exc_info=True)
        data = None
        if url:
            try:
                data = request_bytes(url)
            except TwitchError:
                log.debug("Could not fetch a Twitch avatar", exc_info=True)
        with self._lock:
            if len(self._avatars) >= AVATAR_CACHE_LIMIT:
                self._avatars.clear()
            # Stored even when it failed, so a private or missing picture is
            # not re-requested on every repaint of the key showing it.
            self._avatars[key] = data
        return data

    def cached_avatar(self, user_id: str) -> bytes | None:
        """What is already known, without ever reaching for the network."""
        with self._lock:
            return self._avatars.get((user_id or "").strip())

    def prefetch_avatar(self, user_id: str) -> None:
        """Fetch a picture off the calling thread, for a key to find later."""
        key = (user_id or "").strip()
        if not key:
            return
        with self._lock:
            if key in self._avatars:
                return
        self._submit(lambda: self.avatar(key))

    def find_user(self, login: str) -> str:
        """A channel's numeric id from its name, cached.

        Every raid needs one, and the same handful of channels get raided
        repeatedly, so this is worth remembering between presses.
        """
        key = (login or "").strip().casefold()
        if not key:
            return ""
        with self._lock:
            cached = self._users.get(key)
        if cached is not None:
            return cached
        answer = self._get("/users", {"login": key})
        entries = answer.get("data")
        found = ""
        if isinstance(entries, list) and entries and isinstance(entries[0], dict):
            found = str(entries[0].get("id") or "")
        with self._lock:
            if len(self._users) >= USER_CACHE_LIMIT:
                self._users.clear()
            self._users[key] = found
        return found

    def search_channels(self, query: str, limit: int = 8) -> list[str]:
        """Channel names matching what has been typed, for the raid target.

        Live channels first, because a raid goes to somebody who is streaming;
        Twitch returns both and the order is the only thing that says which.
        """
        text = (query or "").strip().lstrip("@")
        if not text:
            return []
        try:
            answer = self._get(
                "/search/channels", {"query": text, "first": 20, "live_only": False}
            )
        except TwitchError:
            log.debug("Could not search Twitch channels", exc_info=True)
            return []
        entries = answer.get("data")
        rows = [e for e in (entries if isinstance(entries, list) else [])
                if isinstance(e, dict) and e.get("broadcaster_login")]
        rows.sort(key=lambda e: not e.get("is_live"))
        names = [str(e["broadcaster_login"]) for e in rows]
        with self._lock:
            for entry in rows:
                if entry.get("id"):
                    self._users[str(entry["broadcaster_login"]).casefold()] = str(
                        entry["id"]
                    )
        return names[:limit]

    def refresh_channel(self) -> dict[str, Any]:
        """Read the channel state now, blocking until it answers.

        The opposite of `channel()`, and only for a caller that can afford to
        wait: the pre-flight runs on an action worker and is asked once, so a
        cached answer from up to twenty seconds ago would be exactly the wrong
        thing to make a going-live decision on.
        """
        snapshot = self._read_channel()
        with self._lock:
            self._channel = snapshot
            self._channel_at = time.monotonic()
        return dict(snapshot)

    def create_marker(self, description: str = "") -> None:
        body: dict[str, Any] = {"user_id": self._user_id()}
        text = (description or "").strip()
        if text:
            # Twitch rejects anything longer, and losing the marker over a long
            # note would be a poor trade.
            body["description"] = text[:140]
        self._post("/streams/markers", body=body)

    def search_categories(
        self, query: str, limit: int = 8
    ) -> list["CategorySuggestion"]:
        """Categories matching what has been typed so far.

        For the editor's suggestion list, so a category is picked rather than
        guessed. Blocking, and deliberately so: it is called from a worker the
        editor owns, never from the GTK thread, because every keystroke past
        the debounce is a request to Twitch.

        A refusal answers an empty list. A suggestion list that cannot be
        filled is a missing convenience, not an error worth interrupting
        somebody's typing with.

        Note what is **not** here. Twitch's own search shows a viewer and
        follower count beside each category; Helix does not expose either, and
        the only source is Twitch's private GraphQL API. Guessing them, or
        reaching into an interface with no stability promise, would both be
        worse than a list that shows what it can stand behind.
        """
        text = (query or "").strip()
        if not text:
            return []
        key = text.casefold()
        with self._lock:
            cached = self._searches.get(key)
        if cached is not None:
            return list(cached[:limit])
        try:
            answer = self._get("/search/categories", {"query": text, "first": 20})
        except TwitchError:
            log.debug("Could not search Twitch categories", exc_info=True)
            return []
        entries = answer.get("data")
        found = [
            CategorySuggestion(
                name=str(entry.get("name") or ""),
                box_art_url=_box_art_url(entry.get("box_art_url")),
            )
            for entry in (entries if isinstance(entries, list) else [])
            if isinstance(entry, dict) and entry.get("name")
        ]
        with self._lock:
            if len(self._searches) >= SEARCH_CACHE_LIMIT:
                self._searches.clear()
            self._searches[key] = found
            # Every result is a name that resolves to itself, so typing on
            # after picking one costs no second lookup at press time.
            for entry in (entries if isinstance(entries, list) else []):
                if isinstance(entry, dict) and entry.get("name") and entry.get("id"):
                    self._categories[str(entry["name"]).casefold()] = str(entry["id"])
        return found[:limit]

    def box_art(self, url: str) -> bytes | None:
        """One category's artwork, cached by address.

        Blocking, like the search it belongs to, and called from the same kind
        of worker. A failure answers None: a row without its picture is still a
        usable suggestion, so nothing here is worth reporting.
        """
        if not url:
            return None
        with self._lock:
            if url in self._art:
                return self._art[url]
        try:
            data = request_bytes(url)
        except TwitchError:
            log.debug("Could not fetch category art", exc_info=True)
            data = None
        with self._lock:
            if len(self._art) >= ART_CACHE_LIMIT:
                self._art.clear()
            self._art[url] = data
        return data

    def _resolve_category(self, wanted: str) -> tuple[str, str]:
        key = wanted.casefold()
        with self._lock:
            cached = self._categories.get(key)
        if cached:
            return cached, wanted
        answer = self._get("/search/categories", {"query": wanted, "first": 20})
        entries = answer.get("data")
        if not isinstance(entries, list) or not entries:
            raise TwitchError(f"Twitch has no category called {wanted!r}")
        games = [entry for entry in entries if isinstance(entry, dict)]
        # An exact name wins over search ranking: searching "Doom" also matches
        # "Doom Eternal", and picking the ranked first result would quietly set
        # the wrong game for anyone who typed the exact one.
        chosen = next(
            (g for g in games if str(g.get("name") or "").casefold() == key), games[0]
        )
        game_id = str(chosen.get("id") or "")
        matched = str(chosen.get("name") or wanted)
        if not game_id:
            raise TwitchError(f"Twitch has no category called {wanted!r}")
        with self._lock:
            if len(self._categories) >= CATEGORY_CACHE_LIMIT:
                self._categories.clear()
            self._categories[key] = game_id
        return game_id, matched

    def _invalidate(self) -> None:
        """Force the next `channel()` to schedule a refresh.

        A key that just changed the title should not keep showing the old one
        for the rest of the cache interval.
        """
        with self._lock:
            self._channel_at = 0.0

    # ---------- requests ----------

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request("GET", path, params=params)

    def _post(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._request("POST", path, params=params, body=body)

    def _patch(
        self, path: str, params: dict[str, Any], body: dict[str, Any]
    ) -> dict[str, Any]:
        return self._request("PATCH", path, params=params, body=body)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = self._headers()
        try:
            return request_json(
                method, f"{HELIX_BASE}{path}", params=params, headers=headers, body=body
            )
        except TwitchHTTPError as error:
            if error.status == 401 and _is_scope_error(error.message):
                # The account was linked before this action existed, so its
                # token was never granted what the action needs. Twitch names
                # the permission and stops there; what someone pressing a key
                # needs is the sentence that follows.
                raise TwitchScopeError(
                    error.status, error.message,
                    missing_scope_message(error.message),
                ) from error
            if error.status == 401:
                # The token was accepted a moment ago and is not any more.
                # Renewing once and retrying turns the common case — an
                # expiry this client had not noticed — into no visible failure.
                #
                # A missing scope is deliberately excluded. It is also a 401,
                # but no amount of renewing grants a permission that was never
                # asked for, and since a refresh token is single use, retrying
                # it would spend one on every refresh of a key that can never
                # succeed. Relinking is the only fix, which the account dialog
                # says.
                headers = self._headers(force_renew=True)
                return request_json(
                    method,
                    f"{HELIX_BASE}{path}",
                    params=params,
                    headers=headers,
                    body=body,
                )
            raise

    def _headers(self, force_renew: bool = False) -> dict[str, str]:
        with self._lock:
            tokens = self._tokens
            client_id = self._client_id
        if tokens is None:
            raise TwitchError("No Twitch account is linked")
        if force_renew or tokens.expiring():
            tokens = self._renew(tokens, client_id)
        return {
            "Authorization": f"Bearer {tokens.access}",
            "Client-Id": client_id,
        }

    def _renew(self, stale: auth.Tokens, client_id: str) -> auth.Tokens:
        """Exchange the refresh token, once, however many callers ask at once."""
        with self._renew_lock:
            with self._lock:
                current = self._tokens
            if current is None:
                raise TwitchError("No Twitch account is linked")
            if current.access != stale.access:
                # Another worker renewed while this one waited; that pair is
                # the live one and the token this call held is already spent.
                return current
            try:
                fresh = auth.refresh_tokens(client_id, current.refresh)
            except auth.TwitchAuthError as error:
                self._drop_account(str(error))
                raise
            # The renewal answer carries no identity, so it is carried over
            # rather than looked up again on every expiry.
            fresh = replace(
                fresh,
                login=current.login,
                user_id=current.user_id,
                scopes=fresh.scopes or current.scopes,
            )
            self._persist(fresh)
            return fresh

    def _ensure_identity(self, tokens: auth.Tokens) -> auth.Tokens:
        """Confirm a stored token and fill in what it does not carry."""
        try:
            identified = auth.identify(tokens)
        except TwitchHTTPError as error:
            if error.status in (400, 401):
                # Expired rather than revoked: renewing is the normal path.
                renewed = auth.refresh_tokens(self.client_id, tokens.refresh)
                self._persist(renewed)
                identified = auth.identify(renewed)
            else:
                raise
        self._persist(identified)
        return identified

    def _user_id(self) -> str:
        with self._lock:
            tokens = self._tokens
        if tokens is None:
            raise TwitchError("No Twitch account is linked")
        if tokens.user_id:
            return tokens.user_id
        identified = self._ensure_identity(tokens)
        if not identified.user_id:
            raise TwitchError("Twitch did not identify the linked account")
        return identified.user_id


def _box_art_url(template: Any) -> str:
    """Fill in the size Twitch leaves as placeholders in a box art address.

    They arrive as `.../33214-{width}x{height}.jpg`, so an unsubstituted one
    fetches nothing. A template that carries neither placeholder is used as it
    stands, since some entries already name a size.
    """
    if not isinstance(template, str) or not template:
        return ""
    width, height = BOX_ART_SIZE
    return template.replace("{width}", str(width)).replace("{height}", str(height))


def missing_scope_message(message: str) -> str:
    """Turn Twitch's refusal into something the person pressing can act on.

    It says "User access token requires the X scope." and stops, which states
    the fact and leaves the fix unsaid. The account only has to be connected
    again — the authorization simply predates the action.
    """
    scope = _named_scope(message)
    named = f" ({scope})" if scope else ""
    return (
        f"Your Twitch account has not granted this permission{named}. It was "
        "connected before this action existed: open «Twitch account…» and "
        "connect again."
    )


def _named_scope(message: str) -> str:
    """The scope Twitch named, if it named one."""
    for word in (message or "").replace(",", " ").split():
        # Every Twitch scope is colon-separated, and nothing else in that
        # sentence is, so this needs no pattern to keep in step with them.
        if ":" in word:
            return word.strip(".'\"")
    return ""


def _is_scope_error(message: str) -> bool:
    """Whether a refusal is about permission rather than about the token.

    Twitch words it as "Missing scope: ...". Reading the message is not
    beautiful, but the alternative is treating an unfixable refusal as an
    expiry, and the cost of that is a spent refresh token every time.
    """
    return "scope" in (message or "").lower()


def uptime_seconds(snapshot: dict[str, Any], now: float | None = None) -> float | None:
    """How long the current stream has been running, or None when offline."""
    started = snapshot.get("started_at")
    if not snapshot.get("live") or not started:
        return None
    moment = time.time() if now is None else now
    return max(0.0, moment - float(started))


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_epoch(value: Any) -> float | None:
    """Twitch timestamps are RFC 3339 in UTC, written with a trailing Z."""
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()
