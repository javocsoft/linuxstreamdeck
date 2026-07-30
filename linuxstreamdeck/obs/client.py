"""OBS client: synchronous requests + real-time events + reconnection.

Uses obs-websocket v5 (built into OBS 28+) through obsws-python. Keeps a state
cache (`OBSState`) fed by events so key feedback is instant, and emits
`obs.state` on the bus on any change so the controller re-renders.
"""

from __future__ import annotations

import base64
import binascii
import logging
import math
import re
import threading
import time

import obsws_python as obsws

from ..core.events import EventBus

log = logging.getLogger(__name__)

RECONNECT_SECONDS = 3

# How often the statistics sample may be refreshed. Anything faster costs three
# serialized websocket requests for a number nobody can read changing that fast.
STATS_INTERVAL = 1.0

# Floor for a reported audio level. A silent input's raw multiplier turns into
# about -750 dB, which is noise in a report rather than information.
AUDIO_SILENCE_DB = -96.0

# Thumbnails held at once. A deck shows at most a screenful of preview keys, and
# a stale entry is only worth keeping while its key may come back on screen.
MAX_THUMBNAILS = 48

# Splits CamelCase, including an acronym followed by a word ("OBSBasic").
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_SEPARATORS = re.compile(r"[-_.\s]+")
# Prefixes that only say where a hotkey lives, adding nothing for the reader.
_GENERIC_HOTKEY_GROUPS = frozenset({"obsbasic", "libobs", "obs"})


def _decode_image_data(value) -> bytes | None:
    """The bytes behind obs-websocket's `data:image/jpg;base64,...` string."""
    if not isinstance(value, str) or not value:
        return None
    payload = value.split(",", 1)[1] if value.startswith("data:") else value
    try:
        return base64.b64decode(payload, validate=True)
    except (ValueError, binascii.Error):
        log.debug("Could not decode a source screenshot")
        return None


def _milliseconds(value) -> float | None:
    """An obs-websocket duration in milliseconds, as seconds."""
    try:
        return max(0.0, float(value) / 1000.0)
    except (TypeError, ValueError):
        return None


def _to_db(level: float) -> float:
    """A multiplier level as dB. Digital silence is a floor, not minus infinity.

    OBS reports levels as multipliers, and a truly silent input comes through
    as a number so small that its logarithm is around -750 dB. Clamping keeps
    that out of anything a person reads while still being unmistakably silent.
    """
    if level <= 0.0:
        return AUDIO_SILENCE_DB
    return max(AUDIO_SILENCE_DB, 20.0 * math.log10(level))


def _words(text: str) -> list[str]:
    parts: list[str] = []
    for chunk in _SEPARATORS.split(text):
        if chunk:
            parts.extend(part for part in _CAMEL_BOUNDARY.split(chunk) if part)
    return parts


def _sentence(words: list[str]) -> str:
    if not words:
        return ""
    # Keep acronyms as they are, lowercase the rest, capitalize the first word.
    rendered = [word if word.isupper() else word.lower() for word in words]
    first = rendered[0]
    rendered[0] = first if first.isupper() else first.capitalize()
    return " ".join(rendered)


def hotkey_display_name(name: str) -> str:
    """Readable text for an internal OBS hotkey name.

    obs-websocket's GetHotkeyList only returns identifiers such as
    "OBSBasic.StartStreaming" or "libobs.push-to-mute", with no localized
    description, so the label is derived here. The identifier itself stays the
    stored value, because that is what TriggerHotkeyByName needs.
    """
    text = str(name or "").strip()
    if not text:
        return ""
    group, separator, rest = text.partition(".")
    if not separator or not rest:
        group, rest = "", text
    label = _sentence(_words(rest))
    if not label:
        return text
    if group and group.lower() not in _GENERIC_HOTKEY_GROUPS:
        return f"{_sentence(_words(group))}: {label}"
    return label


class OBSState:
    """Cache of the OBS state relevant to key feedback."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.current_scene: str = ""
        self.preview_scene: str = ""
        self.recording: bool = False
        self.record_paused: bool = False
        self.streaming: bool = False
        self.virtualcam: bool = False
        self.replay_active: bool = False
        self.studio_mode: bool = False
        self.current_transition: str = ""
        self.muted: dict[str, bool] = {}   # inputName -> muted


class OBSClient:
    def __init__(self, bus: EventBus) -> None:
        self.bus = bus
        self.state = OBSState()
        self.connected = False
        self.host = "localhost"
        self.port = 4455
        self.password = ""
        self._req: obsws.ReqClient | None = None
        self._events: obsws.EventClient | None = None
        self._lock = threading.Lock()          # protects _req (concurrent requests)
        self._stop = threading.Event()
        self._monitor: threading.Thread | None = None
        # Statistics cache; see stats(). Its own lock, never the request lock:
        # holding that one while sampling would serialize the whole client
        # behind a refresh that is only a display value.
        self._stats_lock = threading.Lock()
        self._stats: dict | None = None
        self._stats_at = 0.0
        self._stream_bytes: tuple[int, float] | None = None
        # Live key thumbnails; see source_thumbnail(). `_thumbs_pending` keeps a
        # slow capture from being started twice while the first is still in
        # flight, which a render burst would otherwise do.
        self._thumb_lock = threading.Lock()
        self._thumbs: dict[tuple[str, tuple[int, int]], tuple[float, bytes]] = {}
        self._thumbs_pending: set[tuple[str, tuple[int, int]]] = set()

    # ---------- lifecycle ----------

    def configure(self, host: str, port: int, password: str) -> None:
        self.host, self.port, self.password = host, port, password

    def start(self) -> None:
        """Start the monitor thread that connects and reconnects automatically."""
        if self._monitor and self._monitor.is_alive():
            return
        self._stop.clear()
        self._monitor = threading.Thread(
            target=self._monitor_loop, daemon=True, name="obs-monitor"
        )
        self._monitor.start()

    def stop(self) -> None:
        self._stop.set()
        self._teardown(emit=False)
        monitor, self._monitor = self._monitor, None
        if monitor is not None and monitor is not threading.current_thread():
            monitor.join()

    def reconnect_now(self) -> None:
        """Force an immediate reconnection (e.g. when settings change)."""
        self._teardown(emit=True)

    def _monitor_loop(self) -> None:
        while not self._stop.is_set():
            if not self.connected:
                self._try_connect()
            else:
                # the event thread dies when OBS closes the connection
                ev = self._events
                if ev is None or not ev.worker.is_alive():
                    log.info("OBS connection lost")
                    self._teardown(emit=True)
            self._stop.wait(RECONNECT_SECONDS)

    def _try_connect(self) -> None:
        req = None
        events = None
        try:
            kwargs = dict(
                host=self.host, port=self.port, password=self.password, timeout=5
            )
            req = obsws.ReqClient(**kwargs)
            events = obsws.EventClient(**kwargs)
            events.callback.register(self._event_handlers())
        except Exception as e:
            self._disconnect_clients(req, events)
            log.debug("OBS unavailable (%s): %s", type(e).__name__, e)
            return
        with self._lock:
            if self._stop.is_set():
                discard = True
            else:
                self._req, self._events = req, events
                self.connected = True
                discard = False
        if discard:
            self._disconnect_clients(req, events)
            return
        self._prime_state()
        if self._stop.is_set() or not self.connected:
            return
        log.info("Connected to OBS at %s:%s", self.host, self.port)
        self.bus.emit("obs.connected")
        self.bus.emit("obs.state", what="connected")

    def _teardown(self, emit: bool) -> None:
        with self._lock:
            req, events = self._req, self._events
            self._req = self._events = None
            was_connected = self.connected
            self.connected = False
        self.state.reset()
        self._disconnect_clients(req, events)
        if emit and was_connected:
            self.bus.emit("obs.disconnected")
            self.bus.emit("obs.state", what="disconnected")

    @staticmethod
    def _disconnect_clients(req, events) -> None:
        for client in (req, events):
            try:
                if client is not None:
                    client.disconnect()
            except Exception:
                pass

    # ---------- requests ----------

    def request(self, name: str, data: dict | None = None) -> dict:
        """Raw obs-websocket v5 request. Returns responseData (camelCase).

        The lock is held for the WHOLE send: obsws' ReqClient uses a single
        websocket that is not thread-safe. Without this, a request from the main
        thread (filling dropdowns) and one from the render thread (a key's
        feedback) overlap and corrupt the protocol → hang.
        """
        thread = threading.current_thread().name
        waiting = time.time()
        with self._lock:
            acquired = time.time()
            req = self._req
            if req is None:
                raise ConnectionError("OBS is not connected")
            result = req.send(name, data, raw=True) or {}
            done = time.time()
        blocked = (acquired - waiting) * 1000
        elapsed = (done - acquired) * 1000
        if blocked > 50 or elapsed > 200:
            log.warning(
                "[obs] %s [thread %s] waited lock %.0f ms, send %.0f ms",
                name, thread, blocked, elapsed,
            )
        else:
            log.debug(
                "[obs] %s [thread %s] lock %.0f ms, send %.0f ms",
                name, thread, blocked, elapsed,
            )
        return result

    def try_request(self, name: str, data: dict | None = None) -> dict | None:
        try:
            return self.request(name, data)
        except Exception as e:
            log.debug("Request %s failed: %s", name, e)
            return None

    # ---------- pre-flight helpers ----------

    def measure_audio(self, seconds: float) -> dict[str, float] | None:
        """Loudest level per input, in dB, over `seconds`. None if unanswered.

        Volume meters are a high-volume subscription: obs-websocket sends them
        twenty times a second for every active input, which is why the normal
        connection deliberately does not ask for them. This opens a **second,
        short-lived** connection that subscribes to nothing else, listens, and
        closes.

        That separation is not tidiness. The second socket only ever receives,
        so it makes no request and never touches `_lock` — the one that must
        serialize every request or the protocol corrupts. The flood lands on a
        socket of its own and stops when this returns.

        Returning None rather than an empty mapping is the whole point of the
        check that uses it: no readings at all is a question that went
        unanswered, while readings that are all silent means nothing is being
        heard.
        """
        if not self.connected:
            return None
        peaks: dict[str, float] = {}
        seen = False
        lock = threading.Lock()

        def on_input_volume_meters(data) -> None:
            nonlocal seen
            with lock:
                seen = True
                for entry in getattr(data, "inputs", None) or []:
                    name = entry.get("inputName") or ""
                    if not name:
                        continue
                    loudest = 0.0
                    for channel in entry.get("inputLevelsMul") or []:
                        for value in channel:
                            try:
                                loudest = max(loudest, float(value))
                            except (TypeError, ValueError):
                                continue
                    peaks[name] = max(peaks.get(name, 0.0), loudest)

        events = None
        try:
            events = obsws.EventClient(
                host=self.host,
                port=self.port,
                password=self.password,
                timeout=5,
                subs=obsws.Subs.INPUTVOLUMEMETERS,
            )
            events.callback.register(on_input_volume_meters)
            self._stop.wait(max(0.1, seconds))
        except Exception as error:
            log.debug("Could not measure audio levels: %s", error)
            return None
        finally:
            if events is not None:
                try:
                    events.disconnect()
                except Exception:
                    log.debug("Meter connection did not close cleanly",
                              exc_info=True)
        with lock:
            if not seen:
                return None
            return {name: _to_db(level) for name, level in peaks.items()}

    def muted_inputs(self, names) -> dict[str, bool]:
        """Which of these inputs are muted in OBS.

        Unlike a level reading this is unambiguous: a quiet room and a muted
        microphone both measure as silence, but only one of them is a mistake.
        Anything that does not answer is left out rather than guessed at.
        """
        states: dict[str, bool] = {}
        for name in names or ():
            result = self.try_request("GetInputMute", {"inputName": name})
            if result and "inputMuted" in result:
                states[name] = bool(result["inputMuted"])
        return states

    def capture_sources(self) -> dict[str, str]:
        """Each V4L2 source and the device it is configured for.

        Only V4L2: a webcam or capture card is the thing whose absence ruins a
        show, and it is the only source kind whose health can be established
        from outside OBS without rendering anything.
        """
        found: dict[str, str] = {}
        listing = self.try_request("GetInputList") or {}
        for entry in listing.get("inputs") or []:
            if entry.get("inputKind") != "v4l2_input":
                continue
            name = entry.get("inputName") or ""
            if not name:
                continue
            settings = self.try_request("GetInputSettings", {"inputName": name})
            device = ""
            if settings:
                device = str(
                    (settings.get("inputSettings") or {}).get("device_id") or ""
                )
            # OBS stores the device as a path that may carry a trailing
            # description, and commonly as a stable /dev/v4l/by-id symlink.
            found[name] = device.split()[0] if device else ""
        return found

    def stream_target(self) -> tuple[str, bool | None]:
        """The streaming service, and whether a key is set. Never the key.

        `GetStreamServiceSettings` returns the stream key in the clear. It must
        not be returned, logged or shown: the application writes a log file
        now, so one careless line would put it on disk in plain text.
        """
        result = self.try_request("GetStreamServiceSettings")
        if not result:
            return "", None
        settings = result.get("streamServiceSettings") or {}
        return str(result.get("streamServiceType") or ""), bool(settings.get("key"))

    def record_directory(self) -> str:
        result = self.try_request("GetRecordDirectory") or {}
        return str(result.get("recordDirectory") or "")

    def scene_collection(self) -> tuple[str, int]:
        """The loaded scene collection and how many exist."""
        result = self.try_request("GetSceneCollectionList") or {}
        return (
            str(result.get("currentSceneCollectionName") or ""),
            len(result.get("sceneCollections") or ()),
        )

    # ---------- live statistics ----------

    def stats(self) -> dict:
        """OBS statistics, fetched at most once per `STATS_INTERVAL`.

        Every statistics key asks for this while its image is composed, so
        without the cache a page showing four of them would fire twelve
        websocket requests per repaint — all of them through the single
        serialized connection, competing with the feedback of every other key.
        One sample per interval serves all of them.

        Returns an empty dict when OBS is not connected, which is what the
        actions render as a dash rather than a stale number.
        """
        now = time.monotonic()
        with self._stats_lock:
            fresh = self._stats
            if fresh is not None and now - self._stats_at < STATS_INTERVAL:
                return fresh
        sample = self._sample_stats(now)
        with self._stats_lock:
            self._stats, self._stats_at = sample, now
        return sample

    def _sample_stats(self, now: float) -> dict:
        """One round of statistics requests, plus the derived stream bitrate."""
        if not self.connected:
            with self._stats_lock:
                self._stream_bytes = None
            return {}
        general = self.try_request("GetStats") or {}
        stream = self.try_request("GetStreamStatus") or {}
        record = self.try_request("GetRecordStatus") or {}
        sample = {
            "cpu": general.get("cpuUsage"),
            "memory_mb": general.get("memoryUsage"),
            # `availableDiskSpace` is deliberately not carried: free space is
            # read from the kernel so the key still answers with OBS closed.
            "fps": general.get("activeFps"),
            "render_skipped": general.get("renderSkippedFrames"),
            "render_total": general.get("renderTotalFrames"),
            "streaming": stream.get("outputActive", False),
            "stream_seconds": _milliseconds(stream.get("outputDuration")),
            "stream_skipped": stream.get("outputSkippedFrames"),
            "stream_total": stream.get("outputTotalFrames"),
            "congestion": stream.get("outputCongestion"),
            "recording": record.get("outputActive", False),
            "record_seconds": _milliseconds(record.get("outputDuration")),
        }
        sample["bitrate_kbps"] = self._stream_bitrate(
            stream.get("outputBytes"), now, bool(sample["streaming"])
        )
        return sample

    def _stream_bitrate(self, total_bytes, now: float, streaming: bool):
        """Bits per second since the previous sample.

        OBS reports bytes sent in total, never a rate, so the rate only exists
        as a difference between two samples. The first sample after connecting
        or after going live has nothing to compare against and reports nothing
        rather than a meaningless spike from a counter that started at zero.
        """
        with self._stats_lock:
            previous = self._stream_bytes
            if not streaming or total_bytes is None:
                self._stream_bytes = None
                return None
            self._stream_bytes = (total_bytes, now)
        if previous is None:
            return None
        last_bytes, last_at = previous
        elapsed = now - last_at
        if elapsed <= 0 or total_bytes < last_bytes:
            return None
        return (total_bytes - last_bytes) * 8 / elapsed / 1000

    # ---------- live thumbnails ----------

    def source_thumbnail(
        self, source: str, size: tuple[int, int], max_age: float
    ) -> bytes | None:
        """A small JPEG of what a scene or source is showing, or None.

        Cached per source and size, and shared: three keys previewing the same
        scene cost one capture, not three. `max_age` is how stale the caller
        will accept, so a key refreshing twice a second and one refreshing once
        a second can share the same cache without either being throttled to the
        other's rate.

        This is not free on the OBS side — it renders and encodes the source on
        demand, on the machine that is also encoding the stream — which is why
        nothing asks for it unless a key was explicitly configured to.
        """
        if not source or not self.connected:
            return None
        key = (source, size)
        now = time.monotonic()
        with self._thumb_lock:
            cached = self._thumbs.get(key)
            if cached is not None and now - cached[0] <= max(0.0, max_age):
                return cached[1]
            pending = self._thumbs_pending
        # Outside the lock: the request is slow, and holding it would make
        # every other key's feedback queue behind this one capture.
        if key in pending:
            return cached[1] if cached is not None else None
        with self._thumb_lock:
            self._thumbs_pending.add(key)
        try:
            data = self._capture(source, size)
        finally:
            with self._thumb_lock:
                self._thumbs_pending.discard(key)
        if data is None:
            return cached[1] if cached is not None else None
        with self._thumb_lock:
            self._thumbs[key] = (now, data)
            self._prune_thumbnails()
        return data

    def _capture(self, source: str, size: tuple[int, int]) -> bytes | None:
        width, height = size
        result = self.try_request(
            "GetSourceScreenshot",
            {
                "sourceName": source,
                "imageFormat": "jpg",
                "imageWidth": max(8, int(width)),
                "imageHeight": max(8, int(height)),
            },
        )
        if not result:
            return None
        return _decode_image_data(result.get("imageData"))

    def _prune_thumbnails(self) -> None:
        """Keep the cache to the keys a deck could plausibly be showing."""
        if len(self._thumbs) <= MAX_THUMBNAILS:
            return
        for key, _value in sorted(
            self._thumbs.items(), key=lambda item: item[1][0]
        )[: len(self._thumbs) - MAX_THUMBNAILS]:
            self._thumbs.pop(key, None)

    # ---------- lists for the editor ----------

    def get_scenes(self) -> list[str]:
        d = self.try_request("GetSceneList") or {}
        return [s["sceneName"] for s in reversed(d.get("scenes", []))]

    def get_inputs(self) -> list[str]:
        d = self.try_request("GetInputList") or {}
        return sorted(i["inputName"] for i in d.get("inputs", []))

    def get_audio_sources_in_scene(self, scene: str) -> list[str]:
        """Sources of one scene that carry audio, plus the global audio devices.

        OBS has no request that lists "the audio sources of a scene": audio
        belongs to inputs, not to scene items. So the scene's items are asked
        one by one whether they can be muted, which is exactly what the audio
        actions need, and anything that answers is kept.

        Desktop Audio and Mic/Aux are always included even though they belong to
        no scene, because they are the inputs people reach for most and OBS
        keeps them audible on every scene.
        """
        names = list(self.get_sources_in_scene(scene))
        specials = self.try_request("GetSpecialInputs") or {}
        for key, value in specials.items():
            if key != "requestType" and isinstance(value, str) and value:
                names.append(value)
        audio: list[str] = []
        for name in dict.fromkeys(names):
            if self.try_request("GetInputMute", {"inputName": name}) is not None:
                audio.append(name)
        return sorted(audio)

    def get_media_inputs(self) -> list[str]:
        return self._inputs_of_kind(("ffmpeg_source", "vlc_source"))

    def get_text_inputs(self) -> list[str]:
        """Text sources, whichever backend OBS built them with."""
        return self._inputs_of_kind(("text_gdiplus", "text_ft2_source"))

    def get_browser_inputs(self) -> list[str]:
        return self._inputs_of_kind(("browser_source",))

    def _inputs_of_kind(self, kinds: tuple[str, ...]) -> list[str]:
        """Inputs whose kind *contains* one of `kinds`.

        Matched as a substring rather than exactly because OBS suffixes these
        kinds across versions and platforms ("text_gdiplus_v2", "_v3"), and an
        exact match would quietly return nothing after an OBS update.
        """
        d = self.try_request("GetInputList") or {}
        return sorted(
            i["inputName"]
            for i in d.get("inputs", [])
            if any(k in i.get("inputKind", "") for k in kinds)
        )

    def get_transitions(self) -> list[str]:
        d = self.try_request("GetSceneTransitionList") or {}
        return [t["transitionName"] for t in d.get("transitions", [])]

    def get_scene_collections(self) -> list[str]:
        d = self.try_request("GetSceneCollectionList") or {}
        return d.get("sceneCollections", [])

    def get_profiles(self) -> list[str]:
        d = self.try_request("GetProfileList") or {}
        return d.get("profiles", [])

    def get_sources_in_scene(self, scene: str) -> list[str]:
        """Every source of a scene, including the ones inside its groups.

        A group is a scene item like any other, and its children are **not**
        items of the scene at all: they only appear through
        `GetGroupSceneItemList`, keyed by the group's own name. Listing the
        scene alone therefore showed the group and hid everything in it.

        The group itself stays in the list — toggling a whole group is a normal
        thing to want — and its children follow it, so the order reads like the
        OBS sources panel. OBS does not allow a group inside a group, so one
        level is the entire tree.
        """
        if not scene:
            scene = self.state.current_scene
        d = self.try_request("GetSceneItemList", {"sceneName": scene}) or {}
        names: list[str] = []
        for item in d.get("sceneItems", []):
            name = item.get("sourceName", "")
            if not name:
                continue
            names.append(name)
            if item.get("isGroup"):
                names.extend(self._group_children(name))
        # The same source may sit in more than one group; keep its first place.
        return list(dict.fromkeys(names))

    def _group_children(self, group: str) -> list[str]:
        d = self.try_request("GetGroupSceneItemList", {"sceneName": group}) or {}
        return [
            item["sourceName"]
            for item in d.get("sceneItems", [])
            if item.get("sourceName")
        ]

    def _groups_in_scene(self, scene: str) -> list[str]:
        d = self.try_request("GetSceneItemList", {"sceneName": scene}) or {}
        return [
            item["sourceName"]
            for item in d.get("sceneItems", [])
            if item.get("isGroup") and item.get("sourceName")
        ]

    def find_scene_item(self, scene: str, source: str) -> tuple[str, int]:
        """The container actually holding `source`, and its scene item id.

        OBS addresses a group's children with the **group's** name in place of
        the scene's, so asking the scene for a nested source simply fails. The
        container is returned rather than the scene because every later
        scene-item request has to be addressed to it too.
        """
        if not scene:
            scene = self.state.current_scene
        source = source or ""
        try:
            found = self.request(
                "GetSceneItemId", {"sceneName": scene, "sourceName": source}
            )
            return scene, int(found["sceneItemId"])
        except Exception:
            # Not a direct item of the scene: it may live in one of its groups.
            pass
        for group in self._groups_in_scene(scene):
            try:
                found = self.request(
                    "GetSceneItemId", {"sceneName": group, "sourceName": source}
                )
                return group, int(found["sceneItemId"])
            except Exception:
                continue
        raise LookupError(f"'{source}' is not in scene '{scene}'")

    def get_filters_of_source(self, source: str) -> list[str]:
        d = self.try_request("GetSourceFilterList", {"sourceName": source}) or {}
        return [f["filterName"] for f in d.get("filters", [])]

    def get_hotkeys(self) -> list[str]:
        d = self.try_request("GetHotkeyList") or {}
        # OBS registers some hotkeys once per source, so the same name comes
        # back several times. They are indistinguishable to TriggerHotkeyByName,
        # so only the first occurrence is worth offering.
        return list(dict.fromkeys(d.get("hotkeys", [])))

    # ---------- initial state ----------

    def _prime_state(self) -> None:
        s = self.state
        if d := self.try_request("GetCurrentProgramScene"):
            s.current_scene = d.get("currentProgramSceneName") or d.get("sceneName", "")
        if d := self.try_request("GetRecordStatus"):
            s.recording = d.get("outputActive", False)
            s.record_paused = d.get("outputPaused", False)
        if d := self.try_request("GetStreamStatus"):
            s.streaming = d.get("outputActive", False)
        if d := self.try_request("GetVirtualCamStatus"):
            s.virtualcam = d.get("outputActive", False)
        if d := self.try_request("GetReplayBufferStatus"):
            s.replay_active = d.get("outputActive", False)
        if d := self.try_request("GetStudioModeEnabled"):
            s.studio_mode = d.get("studioModeEnabled", False)
        if s.studio_mode and (d := self.try_request("GetCurrentPreviewScene")):
            s.preview_scene = d.get("currentPreviewSceneName") or d.get("sceneName", "")
        if d := self.try_request("GetSceneTransitionList"):
            s.current_transition = d.get("currentSceneTransitionName", "")
        # mute state of every input (those that don't support it fail and are skipped)
        if d := self.try_request("GetInputList"):
            for i in d.get("inputs", []):
                name = i["inputName"]
                if m := self.try_request("GetInputMute", {"inputName": name}):
                    s.muted[name] = m.get("inputMuted", False)

    # ---------- events ----------

    def _event_handlers(self) -> list:
        s = self.state
        bus = self.bus

        def changed(what: str) -> None:
            bus.emit("obs.state", what=what)

        def on_current_program_scene_changed(d):
            s.current_scene = d.scene_name
            changed("scene")

        def on_current_preview_scene_changed(d):
            s.preview_scene = d.scene_name
            changed("preview")

        def on_record_state_changed(d):
            s.recording = d.output_active
            state = getattr(d, "output_state", "")
            if state == "OBS_WEBSOCKET_OUTPUT_PAUSED":
                s.record_paused = True
            elif state in ("OBS_WEBSOCKET_OUTPUT_RESUMED", "OBS_WEBSOCKET_OUTPUT_STARTED",
                           "OBS_WEBSOCKET_OUTPUT_STOPPED"):
                s.record_paused = False
            changed("record")

        def on_stream_state_changed(d):
            s.streaming = d.output_active
            changed("stream")

        def on_virtualcam_state_changed(d):
            s.virtualcam = d.output_active
            changed("virtualcam")

        def on_replay_buffer_state_changed(d):
            s.replay_active = d.output_active
            changed("replay")

        def on_studio_mode_state_changed(d):
            s.studio_mode = d.studio_mode_enabled
            if not s.studio_mode:
                s.preview_scene = ""
            changed("studio")

        def on_input_mute_state_changed(d):
            s.muted[d.input_name] = d.input_muted
            changed("mute")

        def on_current_scene_transition_changed(d):
            s.current_transition = d.transition_name
            changed("transition")

        def on_scene_item_enable_state_changed(d):
            changed("visibility")

        def on_source_filter_enable_state_changed(d):
            changed("filter")

        def on_scene_list_changed(d):
            changed("scenes_list")

        def on_input_name_changed(d):
            s.muted.pop(getattr(d, "old_input_name", ""), None)
            changed("inputs_list")

        def on_exit_started(d):
            log.info("OBS is shutting down")

        return [
            on_current_program_scene_changed,
            on_current_preview_scene_changed,
            on_record_state_changed,
            on_stream_state_changed,
            on_virtualcam_state_changed,
            on_replay_buffer_state_changed,
            on_studio_mode_state_changed,
            on_input_mute_state_changed,
            on_current_scene_transition_changed,
            on_scene_item_enable_state_changed,
            on_source_filter_enable_state_changed,
            on_scene_list_changed,
            on_input_name_changed,
            on_exit_started,
        ]
