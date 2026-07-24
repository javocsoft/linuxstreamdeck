"""OBS client: synchronous requests + real-time events + reconnection.

Uses obs-websocket v5 (built into OBS 28+) through obsws-python. Keeps a state
cache (`OBSState`) fed by events so key feedback is instant, and emits
`obs.state` on the bus on any change so the controller re-renders.
"""

from __future__ import annotations

import logging
import threading
import time

import obsws_python as obsws

from ..core.events import EventBus

log = logging.getLogger(__name__)

RECONNECT_SECONDS = 3


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

    # ---------- lists for the editor ----------

    def get_scenes(self) -> list[str]:
        d = self.try_request("GetSceneList") or {}
        return [s["sceneName"] for s in reversed(d.get("scenes", []))]

    def get_inputs(self) -> list[str]:
        d = self.try_request("GetInputList") or {}
        return sorted(i["inputName"] for i in d.get("inputs", []))

    def get_media_inputs(self) -> list[str]:
        d = self.try_request("GetInputList") or {}
        media_kinds = ("ffmpeg_source", "vlc_source")
        return sorted(
            i["inputName"]
            for i in d.get("inputs", [])
            if any(k in i.get("inputKind", "") for k in media_kinds)
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
        if not scene:
            scene = self.state.current_scene
        d = self.try_request("GetSceneItemList", {"sceneName": scene}) or {}
        return [i["sourceName"] for i in d.get("sceneItems", [])]

    def get_filters_of_source(self, source: str) -> list[str]:
        d = self.try_request("GetSourceFilterList", {"sourceName": source}) or {}
        return [f["filterName"] for f in d.get("filters", [])]

    def get_hotkeys(self) -> list[str]:
        d = self.try_request("GetHotkeyList") or {}
        return d.get("hotkeys", [])

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
