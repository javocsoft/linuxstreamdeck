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
import time
from pathlib import Path

from ..core import preflight, sysstats
from ..core.actions import (
    REGISTRY,
    Action,
    ActionContext,
    Param,
    apply_default_icons,
    register,
)

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

# Live preview rates, as a key may ask for them. Off by default: each capture
# makes OBS render and encode the scene on the machine that is also encoding the
# stream, so it is the user who decides which keys are worth that.
PREVIEW_OFF = "off"
PREVIEW_SLOW = "slow"
PREVIEW_SMOOTH = "smooth"
PREVIEW_RATES = {PREVIEW_SLOW: 1.0, PREVIEW_SMOOTH: 0.5}
PREVIEW_LABELS = {
    PREVIEW_OFF: "No",
    PREVIEW_SLOW: "Yes, once a second",
    PREVIEW_SMOOTH: "Yes, twice a second",
}


def preview_interval(value) -> float:
    """Seconds between captures for a stored preview setting, 0 when off."""
    return PREVIEW_RATES.get(str(value or PREVIEW_OFF), 0.0)


def _preview_param() -> Param:
    return Param(
        "preview",
        "Live preview",
        kind="choice",
        default=PREVIEW_OFF,
        choices=[PREVIEW_OFF, PREVIEW_SLOW, PREVIEW_SMOOTH],
        choice_labels=dict(PREVIEW_LABELS),
    )


def _preview_image(ctx, source: str, value) -> bytes | None:
    """The live thumbnail for a key that asked for one, if it can be had."""
    interval = preview_interval(value)
    if not interval or not source:
        return None
    size = getattr(ctx.controller, "key_image_size", (72, 72))
    try:
        # Accepts a frame up to one interval old, so several keys previewing
        # the same scene share one capture instead of each forcing its own.
        return ctx.obs.source_thumbnail(source, size, interval)
    except Exception:
        log.debug("Live preview of %s failed", source, exc_info=True)
        return None


@register
class SceneSwitch(Action):
    id = "obs.scene_switch"
    name = "Switch scene"
    category = CAT_SCENES
    description = (
        "Switch the program scene. The key lights up when it is the active "
        "scene, and can show a live preview of what that scene is showing."
    )
    params = [
        Param("scene", "Scene", choices_source="scenes"),
        _preview_param(),
    ]

    def execute(self, ctx, p):
        ctx.obs.request("SetCurrentProgramScene", {"sceneName": p.get("scene", "")})

    def feedback(self, ctx, p):
        scene = p.get("scene")
        state = {"active": ctx.obs.state.current_scene == scene}
        image = _preview_image(ctx, scene, p.get("preview"))
        if image is not None:
            state["image"] = image
        return state


@register
class ScenePreview(Action):
    id = "obs.scene_preview"
    name = "Set preview scene"
    category = CAT_SCENES
    description = (
        "Put a scene in the studio mode preview. The key can show a live "
        "preview of what that scene is showing."
    )
    params = [
        Param("scene", "Scene", choices_source="scenes"),
        _preview_param(),
    ]

    def execute(self, ctx, p):
        ctx.obs.request("SetCurrentPreviewScene", {"sceneName": p.get("scene", "")})

    def feedback(self, ctx, p):
        scene = p.get("scene")
        state = {"active": ctx.obs.state.preview_scene == scene}
        image = _preview_image(ctx, scene, p.get("preview"))
        if image is not None:
            state["image"] = image
        return state


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
        "Start or stop the live stream. «toggle» switches between the two, so "
        "one key does both. The key turns green and shows LIVE while you are "
        "broadcasting."
    )
    params = [
        Param("mode", "Mode", kind="choice", default="toggle",
              choices=["toggle", "start", "stop"]),
    ]

    def execute(self, ctx, p):
        # An older key has no mode and toggled, so that stays the default --
        # exactly as obs.record does, which is the key this one sits next to.
        mode = p.get("mode", "toggle")
        if mode == "start":
            ctx.obs.request("StartStream")
        elif mode == "stop":
            ctx.obs.request("StopStream")
        else:
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
class RecordChapter(Action):
    """Mark a moment in the recording, for finding it again afterwards.

    This is what a physical key is for: something worth keeping happens, you
    press once, and the chapter is waiting for you in the editor instead of
    somewhere in three hours of footage.

    It needs OBS 30.2 or newer (obs-websocket 5.5) and a recording format that
    can carry chapters, which today means hybrid MP4. Both are reported in
    words, because "request failed" on a key pressed mid-stream tells nobody
    what to change.
    """

    id = "obs.record_chapter"
    name = "Add recording chapter"
    category = CAT_OUTPUT
    description = (
        "Mark this moment in the recording so it is easy to find later. "
        "Needs OBS 30.2+ recording to hybrid MP4."
    )
    params = [
        Param(
            "name",
            "Chapter name",
            default="",
            placeholder="Numbered automatically when left blank",
        ),
    ]

    def execute(self, ctx, p):
        if not ctx.obs.state.recording:
            ctx.bus.emit("status", text="There is no recording to mark")
            return
        name = str(p.get("name") or "").strip()
        try:
            ctx.obs.request(
                "CreateRecordChapter", {"chapterName": name} if name else None
            )
        except Exception as error:
            raise RuntimeError(_CHAPTER_HELP.format(error=error)) from error
        ctx.bus.emit(
            "status", text=f"Chapter «{name}» added" if name else "Chapter added"
        )

    def feedback(self, ctx, p):
        # Lit while there is a recording to mark, so the key says when it can
        # be used instead of only when it has been pressed in vain.
        return {"active": ctx.obs.state.recording}


@register
class RecordSplit(Action):
    id = "obs.record_split"
    name = "Split recording file"
    category = CAT_OUTPUT
    description = (
        "Close the current recording file and carry on into a new one, "
        "without stopping. Needs OBS 30+."
    )

    def execute(self, ctx, p):
        if not ctx.obs.state.recording:
            ctx.bus.emit("status", text="There is no recording to split")
            return
        try:
            ctx.obs.request("SplitRecordFile")
        except Exception as error:
            raise RuntimeError(_SPLIT_HELP.format(error=error)) from error
        ctx.bus.emit("status", text="Recording continues in a new file")

    def feedback(self, ctx, p):
        return {"active": ctx.obs.state.recording}


# Both requests are recent additions to obs-websocket, so the likeliest reason
# either fails is a version or a format rather than anything the user did wrong.
_CHAPTER_HELP = (
    "chapters need OBS 30.2 or newer, recording to hybrid MP4 ({error})"
)
_SPLIT_HELP = "splitting the recording file needs OBS 30 or newer ({error})"


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
            # Never sent to OBS; see Param.advisory.
            advisory=True,
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
            # Never sent to OBS; see Param.advisory.
            advisory=True,
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
            # Never sent to OBS; see Param.advisory.
            advisory=True,
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
        # The stored names are the ones obs-websocket takes; the labels are for
        # the person choosing between them.
        Param("property", "Property", kind="choice", default="positionX",
              choices=["positionX", "positionY", "scaleX", "scaleY",
                       "rotation"],
              choice_labels={
                  "positionX": "Horizontal position",
                  "positionY": "Vertical position",
                  "scaleX": "Horizontal scale",
                  "scaleY": "Vertical scale",
                  "rotation": "Rotation",
              }),
        Param("mode", "Mode", kind="choice", default="set",
              choices=["set", "adjust"],
              choice_labels={
                  "set": "Set to this value",
                  "adjust": "Add to the current value",
              }),
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


# Board colours, matching the statistics keys so one vocabulary covers both.
PREFLIGHT_COLORS = {
    preflight.OK: "#1e3a24",
    preflight.WARN: "#5a4410",
    preflight.FAIL: "#5c1622",
    preflight.UNCHECKED: "#26262e",
}
# A tick, a warning, a cross — and a question mark for what was never asked.
# That last one is the important glyph: it is the difference between "checked
# and fine" and "could not be checked", which must never look the same.
PREFLIGHT_BADGES = {
    preflight.OK: "✓",
    preflight.WARN: "!",
    preflight.FAIL: "✗",
    preflight.UNCHECKED: "?",
}
# Beat between results appearing. Long enough to read as a sequence rather than
# a flicker, short enough that the whole board is up in a couple of seconds.
PREFLIGHT_BEAT = 0.22
# How long the finished board stays before the deck returns. Any press ends it.
PREFLIGHT_HOLD = 12.0


def preflight_spec(check) -> dict:
    """One result as a key image."""
    return {
        "label": check.label,
        "icon_path": check.icon,
        "bg": PREFLIGHT_COLORS.get(check.state, PREFLIGHT_COLORS[preflight.UNCHECKED]),
        "badge": PREFLIGHT_BADGES.get(check.state, "?"),
        # An unanswered question is drawn faded, the same treatment the deck
        # already uses for a key that cannot do anything: it reads as "no
        # answer here" rather than as a quiet pass.
        "unavailable": check.state == preflight.UNCHECKED,
    }


@register
class PreFlight(Action):
    """Everything worth knowing before going live, on the deck at once.

    It reports; it never fixes, switches or activates anything. Several checks
    can only be answered on the machine OBS runs on, and those say so on their
    own key rather than being left out, because a check that is quietly absent
    looks exactly like one that passed.
    """

    id = "obs.preflight"
    name = "Pre-flight check"
    category = CAT_ADVANCED
    description = (
        "Check audio, cameras, disk, CPU, the stream destination and your "
        "Twitch title and category, and show the results across the whole "
        "deck. Reports only; changes nothing."
    )
    running_feedback = True

    def execute(self, ctx, p):
        controller = ctx.controller
        if controller is None:
            return
        count = max(1, int(getattr(controller.deck, "key_count", 15)))
        specs: dict[int, dict] = {}
        results = []
        try:
            for check in preflight.run(
                ctx.obs, controller, getattr(ctx, "twitch", None)
            ):
                results.append(check)
                if len(specs) < count:
                    specs[len(specs)] = preflight_spec(check)
                    controller.show_board(specs)
                if ctx.wait_until_stopped(PREFLIGHT_BEAT):
                    return
            report = preflight.Report(checks=results)
            ctx.bus.emit("status", text=report.summary())
            # The keys carry two words each; the window carries the sentences,
            # including what every result does not cover.
            ctx.bus.emit("preflight.report", checks=tuple(results))
            if len(results) > count:
                ctx.bus.emit(
                    "status",
                    text=(
                        f"{report.summary()} - only {count} of {len(results)} "
                        "fit on the deck; open the window for the rest"
                    ),
                )
            self._hold(ctx, controller)
        finally:
            # Always: leaving a report on the deck would hide every real key.
            controller.show_board(None)

    @staticmethod
    def _hold(ctx, controller) -> None:
        """Keep the report on the deck, but only for as long as it is wanted.

        The hold is for someone reading the deck, so anything that puts the
        report away ends it: a press on the deck, closing the report window, or
        leaving the page it describes. Waiting the whole of `PREFLIGHT_HOLD`
        regardless left the key pulsing `RUN` after the user had plainly
        finished with it.
        """
        deadline = time.monotonic() + PREFLIGHT_HOLD
        while time.monotonic() < deadline:
            if ctx.wait_until_stopped(PREFLIGHT_BEAT):
                return
            if not controller.board_active():
                return


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


# ============================ Statistics =============================

def stat_needs_obs(metric_id) -> bool:
    """Whether this measurement is worth refreshing with OBS closed.

    A machine-wide reading comes from the kernel, so its key keeps working and
    keeps being repainted while OBS is not running.
    """
    metric = STAT_METRICS.get(str(metric_id or "dropped"))
    return True if metric is None else metric.get("needs_obs", True)


def _percent_text(value: float) -> str:
    """A percentage with the precision it deserves.

    Rounding to whole numbers throws away most of the information exactly where
    these values live: an OBS process at 1.4% and one at 0.6% both printed "1%".
    """
    return f"{value:.1f}%" if value < 10 else f"{value:.0f}%"


# Each metric: how it is labelled, how a sample becomes text, and an optional
# warning test. Keeping them in one table means adding a metric is one entry
# rather than a branch in three methods.
#
# `read` is handed the OBS sample, except for a `needs_obs: False` metric, which
# is handed the key's own parameters instead: it reads the kernel, so what it
# needs is configured rather than reported.
STAT_METRICS: dict[str, dict] = {
    "dropped": {
        "label": "Dropped frames",
        "icon": "mdi:filmstrip-off",
        "read": lambda s: _percentage(s.get("stream_skipped"), s.get("stream_total")),
        "text": lambda v: f"{v:.1f}%",
        "warn": lambda v: v >= 1.0,
        "alarm": lambda v: v >= 5.0,
    },
    "bitrate": {
        "label": "Stream bitrate",
        "icon": "mdi:speedometer",
        "read": lambda s: s.get("bitrate_kbps"),
        "text": lambda v: (
            f"{v / 1000:.1f}Mb" if v >= 1000 else f"{v:.0f}kb"
        ),
    },
    "congestion": {
        "label": "Stream congestion",
        "icon": "mdi:network-strength-2-alert",
        "read": lambda s: _ratio(s.get("congestion")),
        "text": lambda v: f"{v * 100:.0f}%",
        "warn": lambda v: v >= 0.2,
        "alarm": lambda v: v >= 0.5,
    },
    "stream_time": {
        "label": "Stream time",
        "icon": "mdi:broadcast",
        "read": lambda s: s.get("stream_seconds") if s.get("streaming") else None,
        "text": lambda v: _clock(v),
    },
    "record_time": {
        "label": "Recording time",
        "icon": "mdi:record-circle",
        "read": lambda s: s.get("record_seconds") if s.get("recording") else None,
        "text": lambda v: _clock(v),
    },
    "disk": {
        "label": "Free disk space",
        "icon": "mdi:harddisk",
        # Read from the kernel rather than from OBS, which only reports it while
        # it is running. "Have I got room to record?" is asked before opening
        # OBS at least as often as during a session, and the filesystem can
        # answer either way.
        "read": lambda p: sysstats.disk_free_mb(p.get("disk_folder")),
        "text": lambda v: (
            f"{v / 1024:.0f}GB" if v >= 1024 else f"{v:.0f}MB"
        ),
        # Running out of disk mid-recording is one of the classic ways to lose
        # a session, so it warns early rather than at the last gigabyte.
        "warn": lambda v: v < 10240,
        "alarm": lambda v: v < 2048,
        "needs_obs": False,
    },
    # OBS reports its own process, which is the number its Stats window shows.
    # Naming it plainly "CPU usage" reads as the machine's, and someone
    # comparing it against a system monitor is right to think it is wrong.
    "cpu": {
        "label": "OBS CPU usage",
        "icon": "mdi:cpu-64-bit",
        "read": lambda s: s.get("cpu"),
        "text": _percent_text,
        "warn": lambda v: v >= 70,
        "alarm": lambda v: v >= 90,
    },
    "system_cpu": {
        "label": "System CPU usage",
        "icon": "mdi:chip",
        # Read from the kernel, not from OBS: this is the whole machine, so it
        # answers even while OBS is closed.
        "read": lambda _p: sysstats.cpu_percent(),
        "text": _percent_text,
        "warn": lambda v: v >= 75,
        "alarm": lambda v: v >= 92,
        "needs_obs": False,
    },
    "memory": {
        "label": "OBS memory",
        "icon": "mdi:memory",
        "read": lambda s: s.get("memory_mb"),
        "text": lambda v: (
            f"{v / 1024:.1f}GB" if v >= 1024 else f"{v:.0f}MB"
        ),
    },
    "fps": {
        "label": "Render FPS",
        "icon": "mdi:video-high-definition",
        "read": lambda s: s.get("fps"),
        "text": lambda v: f"{v:.0f}",
    },
    "render_lag": {
        "label": "Skipped render frames",
        "icon": "mdi:alert-decagram",
        "read": lambda s: _percentage(s.get("render_skipped"), s.get("render_total")),
        "text": lambda v: f"{v:.1f}%",
        "warn": lambda v: v >= 1.0,
        "alarm": lambda v: v >= 5.0,
    },
}

STAT_OK_COLOR = "#1e3a24"
STAT_WARN_COLOR = "#5a4410"
STAT_ALARM_COLOR = "#5c1622"
NO_VALUE = "--"


def _percentage(part, total):
    try:
        part, total = float(part), float(total)
    except (TypeError, ValueError):
        return None
    return 0.0 if total <= 0 else part / total * 100.0


def _ratio(value):
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return None


def _clock(seconds: float) -> str:
    """H:MM:SS, dropping the hour while there is none."""
    total = int(max(0.0, seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


@register
class Stats(Action):
    id = "obs.stats"
    name = "OBS statistics"
    category = CAT_ADVANCED
    description = (
        "Show a live measurement on the key: dropped frames, bitrate, "
        "stream or recording time, free disk space, CPU or FPS. System CPU and "
        "free disk space keep working while OBS is closed."
    )
    params = [
        Param(
            "metric",
            "Measurement",
            kind="choice",
            default="dropped",
            choices=list(STAT_METRICS),
            # The stored value stays an identifier; only the list is readable.
            choice_labels={
                key: metric["label"] for key, metric in STAT_METRICS.items()
            },
        ),
        Param(
            "colored",
            "Warn with color",
            kind="choice",
            default="yes",
            choices=["yes", "no"],
            choice_labels={"yes": "Yes", "no": "No"},
        ),
        # Only free disk space uses it, and only a machine that records
        # somewhere other than home needs to set it. It is asked here rather
        # than taken from OBS so the reading means the same thing whether OBS
        # happens to be running; see `sysstats.disk_folder`.
        Param(
            "disk_folder",
            "Disk folder (free disk space)",
            kind="file",
            default="",
            directory=True,
            placeholder="Home folder, unless you pick your recording drive",
        ),
    ]

    def requires_obs(self, params: dict) -> bool:
        """Only the measurements that actually come from OBS.

        A key showing free disk space or system CPU keeps working with OBS
        closed, so dimming it would be a lie.
        """
        return stat_needs_obs((params or {}).get("metric"))

    def execute(self, ctx, p):
        """Pressing a statistics key reports the value it is showing.

        The key exists to be read, not pressed, but a key that does nothing at
        all feels broken, so a press states the measurement in words.
        """
        metric = STAT_METRICS.get(p.get("metric") or "dropped")
        if metric is None:
            return
        value = self._value(ctx, p)
        ctx.bus.emit(
            "status",
            text=(
                f"{metric['label']}: {value}"
                if value != NO_VALUE
                else f"{metric['label']} is not available right now"
            ),
        )

    def feedback(self, ctx, p):
        metric = STAT_METRICS.get(p.get("metric") or "dropped")
        if metric is None:
            return {}
        raw = self._read(ctx, metric, p)
        state = {"display": self._format(metric, raw)}
        if raw is not None and str(p.get("colored", "yes")) != "no":
            color = self._color(metric, raw)
            if color:
                state["color"] = color
        return state

    def _value(self, ctx, p) -> str:
        metric = STAT_METRICS[p.get("metric") or "dropped"]
        return self._format(metric, self._read(ctx, metric, p))

    @staticmethod
    def _read(ctx, metric: dict, p):
        """This metric's current value, asking OBS only when it has to.

        A measurement that comes from the kernel must keep answering while OBS
        is closed rather than going blank with it. Those readers are handed the
        key's own parameters in place of an OBS sample, since what they need is
        configured rather than reported.
        """
        if not metric.get("needs_obs", True):
            return metric["read"](p or {})
        sample = ctx.obs.stats() if ctx.obs.connected else {}
        return metric["read"](sample) if sample else None

    @staticmethod
    def _format(metric: dict, raw) -> str:
        if raw is None:
            return NO_VALUE
        try:
            return metric["text"](raw)
        except (TypeError, ValueError):
            return NO_VALUE

    @staticmethod
    def _color(metric: dict, raw) -> str:
        """Background by severity. Only metrics with a threshold get one."""
        alarm, warn = metric.get("alarm"), metric.get("warn")
        if warn is None:
            return ""
        try:
            if alarm is not None and alarm(raw):
                return STAT_ALARM_COLOR
            return STAT_WARN_COLOR if warn(raw) else STAT_OK_COLOR
        except (TypeError, ValueError):
            return ""


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
    "obs.record_chapter": "mdi:bookmark-plus-outline",
    "obs.record_split": "mdi:content-cut",
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
    "obs.stats": "mdi:chart-line",
    "obs.preflight": "mdi:clipboard-check-outline",
})


def _mark_obs_dependency() -> None:
    """Tell the deck that everything defined here needs the connection.

    Derived from the category rather than written on each class, so an action
    added later cannot forget it: every action in this module is an OBS action
    by definition, and one that quietly missed the flag would render as usable
    while OBS is closed, which is the exact confusion this exists to remove.
    """
    for action in REGISTRY.values():
        if action.category.startswith("OBS"):
            action.needs_obs = True


_mark_obs_dependency()
