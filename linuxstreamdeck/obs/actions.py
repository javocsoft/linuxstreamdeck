"""Full catalogue of OBS actions (obs-websocket v5).

Covers (and exceeds) the OBS plugin of Elgato's official software: scenes, studio
mode, recording, streaming, virtual camera, replay buffer, audio, sources,
filters, transitions, media, screenshots, hotkeys, collections/profiles and a
"raw request" action that gives access to 100% of the protocol.
"""

from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path

from ..core.actions import Action, ActionContext, Param, apply_default_icons, register

log = logging.getLogger(__name__)

CAT_SCENES = "OBS · Scenes"
CAT_OUTPUT = "OBS · Recording & Streaming"
CAT_AUDIO = "OBS · Audio"
CAT_SOURCES = "OBS · Sources & Filters"
CAT_MEDIA = "OBS · Media"
CAT_ADVANCED = "OBS · Advanced"

COLOR_REC = "#a51d2d"
COLOR_LIVE = "#26a269"
COLOR_ACTIVE = "#1a5fb4"


# ============================== Scenes ==============================

@register
class SceneSwitch(Action):
    id = "obs.scene_switch"
    name = "Switch scene"
    category = CAT_SCENES
    description = "Switch the program scene. The key lights up when it is the active scene."
    params = [Param("scene", "Scene", choices_source="scenes")]

    def execute(self, ctx, p):
        ctx.obs.request("SetCurrentProgramScene", {"sceneName": p.get("scene", "")})

    def feedback(self, ctx, p):
        return {"active": ctx.obs.state.current_scene == p.get("scene")}


@register
class ScenePreview(Action):
    id = "obs.scene_preview"
    name = "Set preview scene"
    category = CAT_SCENES
    description = "Put a scene in the studio mode preview."
    params = [Param("scene", "Scene", choices_source="scenes")]

    def execute(self, ctx, p):
        ctx.obs.request("SetCurrentPreviewScene", {"sceneName": p.get("scene", "")})

    def feedback(self, ctx, p):
        return {"active": ctx.obs.state.preview_scene == p.get("scene")}


@register
class StudioModeToggle(Action):
    id = "obs.studio_mode"
    name = "Studio mode on/off"
    category = CAT_SCENES
    description = (
        "Turn studio mode on or off, so scenes are staged in preview "
        "before going to program. The key lights up while it is on."
    )

    def execute(self, ctx, p):
        ctx.obs.request(
            "SetStudioModeEnabled", {"studioModeEnabled": not ctx.obs.state.studio_mode}
        )

    def feedback(self, ctx, p):
        return {"active": ctx.obs.state.studio_mode}


@register
class StudioTransition(Action):
    id = "obs.studio_transition"
    name = "Transition (studio)"
    category = CAT_SCENES
    description = "Send the preview to program."

    def execute(self, ctx, p):
        ctx.obs.request("TriggerStudioModeTransition")


@register
class TransitionSet(Action):
    id = "obs.transition_set"
    name = "Set transition"
    category = CAT_SCENES
    description = (
        "Choose the transition used between scenes, and optionally how "
        "long it lasts in milliseconds."
    )
    params = [
        Param("transition", "Transition", choices_source="transitions"),
        Param("duration_ms", "Duration (ms, 0 = keep)", kind="int", default=0),
    ]

    def execute(self, ctx, p):
        ctx.obs.request(
            "SetCurrentSceneTransition", {"transitionName": p.get("transition", "")}
        )
        if int(p.get("duration_ms") or 0) > 0:
            ctx.obs.request(
                "SetCurrentSceneTransitionDuration",
                {"transitionDuration": int(p["duration_ms"])},
            )

    def feedback(self, ctx, p):
        return {"active": ctx.obs.state.current_transition == p.get("transition")}


@register
class SceneCollectionSet(Action):
    id = "obs.scene_collection"
    name = "Scene collection"
    category = CAT_SCENES
    description = (
        "Switch to another OBS scene collection, swapping the whole set "
        "of scenes and sources at once."
    )
    params = [Param("collection", "Collection", choices_source="scene_collections")]

    def execute(self, ctx, p):
        ctx.obs.request(
            "SetCurrentSceneCollection", {"sceneCollectionName": p.get("collection", "")}
        )


@register
class ProfileSet(Action):
    id = "obs.profile"
    name = "OBS profile"
    category = CAT_SCENES
    description = (
        "Switch to another OBS profile, swapping its encoding, output and "
        "recording settings. This is an OBS profile, not a "
        "LinuxStreamDeck one."
    )
    params = [Param("profile", "Profile", choices_source="profiles")]

    def execute(self, ctx, p):
        ctx.obs.request("SetCurrentProfile", {"profileName": p.get("profile", "")})


# ======================= Recording & Streaming =========================

@register
class RecordToggle(Action):
    id = "obs.record"
    name = "Record on/off"
    category = CAT_OUTPUT
    description = (
        "Start or stop recording. «toggle» switches between the two, so one key "
        "does both."
    )
    params = [
        Param("mode", "Mode", kind="choice", default="toggle",
              choices=["toggle", "start", "stop"]),
    ]

    def execute(self, ctx, p):
        # An older key has no mode and toggled, so that stays the default.
        mode = p.get("mode", "toggle")
        if mode == "start":
            ctx.obs.request("StartRecord")
        elif mode == "stop":
            ctx.obs.request("StopRecord")
        else:
            ctx.obs.request("ToggleRecord")

    def feedback(self, ctx, p):
        s = ctx.obs.state
        if not s.recording:
            return {"active": False}
        badge = "⏸" if s.record_paused else "●"
        return {"active": True, "color": COLOR_REC, "badge": badge}


@register
class RecordPauseToggle(Action):
    id = "obs.record_pause"
    name = "Pause recording"
    category = CAT_OUTPUT
    description = (
        "Pause or resume a recording in progress, without closing the "
        "file. The key lights up while it is paused."
    )

    def execute(self, ctx, p):
        ctx.obs.request("ToggleRecordPause")

    def feedback(self, ctx, p):
        return {"active": ctx.obs.state.record_paused}


@register
class StreamToggle(Action):
    id = "obs.stream"
    name = "Stream on/off"
    category = CAT_OUTPUT
    description = (
        "Start or stop the live stream. The key turns green and shows "
        "LIVE while you are broadcasting."
    )

    def execute(self, ctx, p):
        ctx.obs.request("ToggleStream")

    def feedback(self, ctx, p):
        if ctx.obs.state.streaming:
            return {"active": True, "color": COLOR_LIVE, "badge": "LIVE"}
        return {"active": False}


@register
class VirtualCamToggle(Action):
    id = "obs.virtualcam"
    name = "Virtual camera on/off"
    category = CAT_OUTPUT
    description = (
        "Start or stop the virtual camera, so OBS shows up as a webcam "
        "in Zoom, Meet, Discord and anything else."
    )

    def execute(self, ctx, p):
        ctx.obs.request("ToggleVirtualCam")

    def feedback(self, ctx, p):
        return {"active": ctx.obs.state.virtualcam}


@register
class ReplayBufferToggle(Action):
    id = "obs.replay"
    name = "Replay buffer on/off"
    category = CAT_OUTPUT
    description = (
        "Start or stop the replay buffer, which keeps the last seconds "
        "of video in memory ready to be saved."
    )

    def execute(self, ctx, p):
        ctx.obs.request("ToggleReplayBuffer")

    def feedback(self, ctx, p):
        return {"active": ctx.obs.state.replay_active}


@register
class ReplaySave(Action):
    id = "obs.replay_save"
    name = "Save replay"
    category = CAT_OUTPUT
    description = (
        "Save what the replay buffer is holding to a file, capturing the "
        "moment that just happened."
    )

    def execute(self, ctx, p):
        ctx.obs.request("SaveReplayBuffer")


@register
class Screenshot(Action):
    id = "obs.screenshot"
    name = "Source screenshot"
    category = CAT_OUTPUT
    description = "Save a PNG screenshot of a source or scene."
    params = [
        Param("source", "Source or scene", choices_source="sources_in_scene"),
        Param("directory", "Destination folder", default=str(Path.home() / "Pictures")),
    ]

    def execute(self, ctx, p):
        source = p.get("source") or ctx.obs.state.current_scene
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        outdir = Path(p.get("directory") or Path.home()).expanduser()
        outdir.mkdir(parents=True, exist_ok=True)
        ctx.obs.request(
            "SaveSourceScreenshot",
            {
                "sourceName": source,
                "imageFormat": "png",
                "imageFilePath": str(outdir / f"obs-{stamp}.png"),
            },
        )


# ============================== Audio ================================

@register
class MuteToggle(Action):
    id = "obs.mute"
    name = "Mute input"
    category = CAT_AUDIO
    description = (
        "The key lights up when the input is muted. The scene only narrows "
        "the list of inputs to choose from; muting applies to the input "
        "itself, on every scene."
    )
    params = [
        Param(
            "scene",
            "Scene (narrows the list below)",
            choices_source="scenes",
            default="",
        ),
        Param(
            "input",
            "Audio input",
            choices_source="audio_sources_in_scene",
        ),
        Param("mode", "Mode", kind="choice", default="toggle",
              choices=["toggle", "mute", "unmute"]),
    ]

    def execute(self, ctx, p):
        name = p.get("input", "")
        mode = p.get("mode", "toggle")
        if mode == "toggle":
            ctx.obs.request("ToggleInputMute", {"inputName": name})
        else:
            ctx.obs.request(
                "SetInputMute", {"inputName": name, "inputMuted": mode == "mute"}
            )

    def feedback(self, ctx, p):
        muted = ctx.obs.state.muted.get(p.get("input", ""))
        return {"active": bool(muted), "color": COLOR_REC if muted else None}


@register
class VolumeAdjust(Action):
    id = "obs.volume_adjust"
    name = "Raise/lower volume"
    category = CAT_AUDIO
    description = (
        "The scene only narrows the list of inputs to choose from; the "
        "change applies to the input itself, on every scene."
    )
    params = [
        Param(
            "scene",
            "Scene (narrows the list below)",
            choices_source="scenes",
            default="",
        ),
        Param(
            "input",
            "Audio input",
            choices_source="audio_sources_in_scene",
        ),
        Param("delta_db", "Change (dB, negative lowers)", kind="float", default=3.0),
    ]

    def execute(self, ctx, p):
        name = p.get("input", "")
        d = ctx.obs.request("GetInputVolume", {"inputName": name})
        new_db = max(-100.0, min(26.0, d.get("inputVolumeDb", 0.0) + float(p.get("delta_db") or 0)))
        ctx.obs.request("SetInputVolume", {"inputName": name, "inputVolumeDb": new_db})


@register
class VolumeSet(Action):
    id = "obs.volume_set"
    name = "Set volume"
    category = CAT_AUDIO
    description = (
        "The scene only narrows the list of inputs to choose from; the "
        "change applies to the input itself, on every scene."
    )
    params = [
        Param(
            "scene",
            "Scene (narrows the list below)",
            choices_source="scenes",
            default="",
        ),
        Param(
            "input",
            "Audio input",
            choices_source="audio_sources_in_scene",
        ),
        Param("db", "Volume (dB)", kind="float", default=0.0),
    ]

    def execute(self, ctx, p):
        ctx.obs.request(
            "SetInputVolume",
            {"inputName": p.get("input", ""), "inputVolumeDb": float(p.get("db") or 0)},
        )


# ======================= Sources & Filters ===========================

@register
class SourceVisibility(Action):
    id = "obs.source_visibility"
    name = "Show/hide source"
    category = CAT_SOURCES
    description = (
        "Show, hide or toggle a source in a scene. Sources inside a group "
        "can be picked too. The key lights up while the source is visible."
    )
    params = [
        Param("scene", "Scene", choices_source="scenes"),
        Param("source", "Source", choices_source="sources_in_scene"),
        Param("mode", "Mode", kind="choice", default="toggle",
              choices=["toggle", "show", "hide"]),
    ]

    def _item_id(self, ctx, p) -> tuple[str, int]:
        # Resolved through the client, because a source inside a group is not
        # an item of the scene: it answers to the group's name instead, and
        # every request below has to be addressed to whatever holds it.
        return ctx.obs.find_scene_item(
            p.get("scene") or ctx.obs.state.current_scene,
            p.get("source", ""),
        )

    def execute(self, ctx, p):
        scene, item_id = self._item_id(ctx, p)
        mode = p.get("mode", "toggle")
        if mode == "toggle":
            d = ctx.obs.request(
                "GetSceneItemEnabled", {"sceneName": scene, "sceneItemId": item_id}
            )
            enabled = not d.get("sceneItemEnabled", True)
        else:
            enabled = mode == "show"
        ctx.obs.request(
            "SetSceneItemEnabled",
            {"sceneName": scene, "sceneItemId": item_id, "sceneItemEnabled": enabled},
        )

    def feedback(self, ctx, p):
        if not ctx.obs.connected or not p.get("source"):
            return None
        try:
            scene, item_id = self._item_id(ctx, p)
            d = ctx.obs.request(
                "GetSceneItemEnabled", {"sceneName": scene, "sceneItemId": item_id}
            )
            return {"active": d.get("sceneItemEnabled", False)}
        except Exception:
            return None


@register
class FilterToggle(Action):
    id = "obs.filter"
    name = "Enable/disable filter"
    category = CAT_SOURCES
    description = (
        "Enable, disable or toggle a filter applied to a source, such as "
        "a chroma key, a colour correction or a noise gate."
    )
    params = [
        Param("source", "Source", choices_source="sources_in_scene"),
        Param("filter", "Filter", choices_source="filters_of_source"),
        Param("mode", "Mode", kind="choice", default="toggle",
              choices=["toggle", "enable", "disable"]),
    ]

    def execute(self, ctx, p):
        source, filt = p.get("source", ""), p.get("filter", "")
        mode = p.get("mode", "toggle")
        if mode == "toggle":
            d = ctx.obs.request(
                "GetSourceFilter", {"sourceName": source, "filterName": filt}
            )
            enabled = not d.get("filterEnabled", True)
        else:
            enabled = mode == "enable"
        ctx.obs.request(
            "SetSourceFilterEnabled",
            {"sourceName": source, "filterName": filt, "filterEnabled": enabled},
        )

    def feedback(self, ctx, p):
        if not ctx.obs.connected or not p.get("filter"):
            return None
        try:
            d = ctx.obs.request(
                "GetSourceFilter",
                {"sourceName": p.get("source", ""), "filterName": p.get("filter", "")},
            )
            return {"active": d.get("filterEnabled", False)}
        except Exception:
            return None


@register
class TextSourceSet(Action):
    id = "obs.text"
    name = "Set text source"
    category = CAT_SOURCES
    description = (
        "Replace what a text source displays. Useful for a countdown message, "
        "a «back in 5» card or a now-playing line."
    )
    params = [
        Param("input", "Text source", choices_source="text_inputs"),
        Param("text", "Text"),
    ]

    def execute(self, ctx, p):
        ctx.obs.request(
            "SetInputSettings",
            {
                "inputName": p.get("input", ""),
                "inputSettings": {"text": str(p.get("text", ""))},
                # Merged into the existing settings, so font, colour and every
                # other property the user configured in OBS survive.
                "overlay": True,
            },
        )


@register
class BrowserSourceRefresh(Action):
    id = "obs.browser_refresh"
    name = "Refresh browser source"
    category = CAT_SOURCES
    description = "Reload a browser source, bypassing its cache."
    params = [
        Param("input", "Browser source", choices_source="browser_inputs"),
    ]

    def execute(self, ctx, p):
        ctx.obs.request(
            "PressInputPropertiesButton",
            {
                "inputName": p.get("input", ""),
                # The button OBS itself labels "Refresh cache of current page".
                "propertyName": "refreshnocache",
            },
        )


@register
class SourceTransform(Action):
    id = "obs.transform"
    name = "Move/scale source"
    category = CAT_SOURCES
    description = (
        "Change one transform property of a source in a scene. «adjust» adds "
        "to the current value, so a key can nudge it repeatedly."
    )
    params = [
        Param("scene", "Scene", choices_source="scenes"),
        Param("source", "Source", choices_source="sources_in_scene"),
        Param("property", "Property", kind="choice", default="positionX",
              choices=["positionX", "positionY", "scaleX", "scaleY",
                       "rotation"]),
        Param("mode", "Mode", kind="choice", default="set",
              choices=["set", "adjust"]),
        Param("value", "Value", kind="float", default=0.0, step=0.1),
    ]

    def execute(self, ctx, p):
        # A grouped source answers to its group, exactly as show/hide does.
        container, item_id = ctx.obs.find_scene_item(
            p.get("scene") or ctx.obs.state.current_scene,
            p.get("source", ""),
        )
        prop = p.get("property", "positionX")
        value = float(p.get("value") or 0.0)
        if p.get("mode", "set") == "adjust":
            current = ctx.obs.request(
                "GetSceneItemTransform",
                {"sceneName": container, "sceneItemId": item_id},
            ).get("sceneItemTransform", {})
            value += float(current.get(prop, 0.0))
        ctx.obs.request(
            "SetSceneItemTransform",
            {
                "sceneName": container,
                "sceneItemId": item_id,
                "sceneItemTransform": {prop: value},
            },
        )


# =============================== Media ===============================

@register
class MediaControl(Action):
    id = "obs.media"
    name = "Media control"
    category = CAT_MEDIA
    description = "Control video/audio sources (Media Source, VLC)."
    params = [
        Param("input", "Media source", choices_source="media_inputs"),
        Param("op", "Operation", kind="choice", default="play/pause",
              choices=["play/pause", "restart", "stop", "next", "previous"]),
    ]

    _OPS = {
        "restart": "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART",
        "stop": "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_STOP",
        "next": "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_NEXT",
        "previous": "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_PREVIOUS",
    }

    def execute(self, ctx, p):
        name = p.get("input", "")
        op = p.get("op", "play/pause")
        if op == "play/pause":
            d = ctx.obs.request("GetMediaInputStatus", {"inputName": name})
            playing = d.get("mediaState") == "OBS_MEDIA_STATE_PLAYING"
            media_action = (
                "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_PAUSE"
                if playing
                else "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_PLAY"
            )
        else:
            media_action = self._OPS[op]
        ctx.obs.request(
            "TriggerMediaInputAction", {"inputName": name, "mediaAction": media_action}
        )


# ============================= Advanced ==============================

@register
class HotkeyTrigger(Action):
    id = "obs.hotkey"
    name = "OBS hotkey"
    category = CAT_ADVANCED
    description = "Trigger any internal OBS hotkey by name."
    params = [Param("hotkey", "Hotkey", choices_source="hotkeys")]

    def execute(self, ctx, p):
        ctx.obs.request("TriggerHotkeyByName", {"hotkeyName": p.get("hotkey", "")})


@register
class RawRequest(Action):
    id = "obs.raw"
    name = "Raw request (advanced)"
    category = CAT_ADVANCED
    description = (
        "Send any obs-websocket v5 protocol request. 100% API coverage."
    )
    params = [
        Param("request_type", "Request type", default="GetVersion"),
        Param("payload", "JSON payload", default="{}"),
    ]

    def execute(self, ctx, p):
        payload = json.loads(p.get("payload") or "{}")
        result = ctx.obs.request(p.get("request_type", ""), payload or None)
        log.info("Response from %s: %s", p.get("request_type"), result)


# Default icons (built-in library). Used when a key has no icon of its own.
apply_default_icons({
    "obs.scene_switch": "mdi:image-multiple",
    "obs.scene_preview": "mdi:eye-outline",
    "obs.studio_mode": "mdi:view-dashboard",
    "obs.studio_transition": "mdi:transition",
    "obs.transition_set": "mdi:transition",
    "obs.scene_collection": "mdi:folder-multiple-image",
    "obs.profile": "mdi:account-cog",
    "obs.record": "mdi:record-circle",
    "obs.record_pause": "mdi:pause-circle",
    "obs.stream": "mdi:broadcast",
    "obs.virtualcam": "mdi:webcam",
    "obs.replay": "mdi:backup-restore",
    "obs.replay_save": "mdi:content-save",
    "obs.screenshot": "mdi:camera",
    "obs.mute": "mdi:microphone-off",
    "obs.volume_adjust": "mdi:volume-medium",
    "obs.volume_set": "mdi:volume-high",
    "obs.source_visibility": "mdi:eye",
    "obs.filter": "mdi:image-filter-black-white",
    "obs.text": "mdi:format-text",
    "obs.browser_refresh": "mdi:refresh",
    "obs.transform": "mdi:cursor-move",
    "obs.media": "mdi:play-circle",
    "obs.hotkey": "mdi:keyboard",
    "obs.raw": "mdi:code-json",
})
