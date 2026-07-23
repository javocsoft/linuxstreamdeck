"""Catálogo completo de acciones OBS (obs-websocket v5).

Cubre y supera al plugin OBS del software oficial de Elgato: escenas, studio
mode, grabación, streaming, cámara virtual, replay buffer, audio, fuentes,
filtros, transiciones, media, capturas, hotkeys, colecciones/perfiles y una
acción "petición cruda" que da acceso al 100% del protocolo.
"""

from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path

from ..core.actions import Action, ActionContext, Param, apply_default_icons, register

log = logging.getLogger(__name__)

CAT_SCENES = "OBS · Escenas"
CAT_OUTPUT = "OBS · Grabación y directo"
CAT_AUDIO = "OBS · Audio"
CAT_SOURCES = "OBS · Fuentes y filtros"
CAT_MEDIA = "OBS · Media"
CAT_ADVANCED = "OBS · Avanzado"

COLOR_REC = "#a51d2d"
COLOR_LIVE = "#26a269"
COLOR_ACTIVE = "#1a5fb4"


# ============================== Escenas ==============================

@register
class SceneSwitch(Action):
    id = "obs.scene_switch"
    name = "Cambiar escena"
    category = CAT_SCENES
    description = "Cambia la escena de programa. La tecla se ilumina si es la escena activa."
    params = [Param("scene", "Escena", choices_source="scenes")]

    def execute(self, ctx, p):
        ctx.obs.request("SetCurrentProgramScene", {"sceneName": p.get("scene", "")})

    def feedback(self, ctx, p):
        return {"active": ctx.obs.state.current_scene == p.get("scene")}


@register
class ScenePreview(Action):
    id = "obs.scene_preview"
    name = "Escena a previsualización"
    category = CAT_SCENES
    description = "Pone una escena en la previsualización del modo estudio."
    params = [Param("scene", "Escena", choices_source="scenes")]

    def execute(self, ctx, p):
        ctx.obs.request("SetCurrentPreviewScene", {"sceneName": p.get("scene", "")})

    def feedback(self, ctx, p):
        return {"active": ctx.obs.state.preview_scene == p.get("scene")}


@register
class StudioModeToggle(Action):
    id = "obs.studio_mode"
    name = "Modo estudio on/off"
    category = CAT_SCENES

    def execute(self, ctx, p):
        ctx.obs.request(
            "SetStudioModeEnabled", {"studioModeEnabled": not ctx.obs.state.studio_mode}
        )

    def feedback(self, ctx, p):
        return {"active": ctx.obs.state.studio_mode}


@register
class StudioTransition(Action):
    id = "obs.studio_transition"
    name = "Transición (estudio)"
    category = CAT_SCENES
    description = "Pasa la previsualización a programa."

    def execute(self, ctx, p):
        ctx.obs.request("TriggerStudioModeTransition")


@register
class TransitionSet(Action):
    id = "obs.transition_set"
    name = "Elegir transición"
    category = CAT_SCENES
    params = [
        Param("transition", "Transición", choices_source="transitions"),
        Param("duration_ms", "Duración (ms, 0 = no cambiar)", kind="int", default=0),
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
    name = "Colección de escenas"
    category = CAT_SCENES
    params = [Param("collection", "Colección", choices_source="scene_collections")]

    def execute(self, ctx, p):
        ctx.obs.request(
            "SetCurrentSceneCollection", {"sceneCollectionName": p.get("collection", "")}
        )


@register
class ProfileSet(Action):
    id = "obs.profile"
    name = "Perfil de OBS"
    category = CAT_SCENES
    params = [Param("profile", "Perfil", choices_source="profiles")]

    def execute(self, ctx, p):
        ctx.obs.request("SetCurrentProfile", {"profileName": p.get("profile", "")})


# ======================= Grabación y directo =========================

@register
class RecordToggle(Action):
    id = "obs.record"
    name = "Grabar on/off"
    category = CAT_OUTPUT

    def execute(self, ctx, p):
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
    name = "Pausar grabación"
    category = CAT_OUTPUT

    def execute(self, ctx, p):
        ctx.obs.request("ToggleRecordPause")

    def feedback(self, ctx, p):
        return {"active": ctx.obs.state.record_paused}


@register
class StreamToggle(Action):
    id = "obs.stream"
    name = "Directo on/off"
    category = CAT_OUTPUT

    def execute(self, ctx, p):
        ctx.obs.request("ToggleStream")

    def feedback(self, ctx, p):
        if ctx.obs.state.streaming:
            return {"active": True, "color": COLOR_LIVE, "badge": "LIVE"}
        return {"active": False}


@register
class VirtualCamToggle(Action):
    id = "obs.virtualcam"
    name = "Cámara virtual on/off"
    category = CAT_OUTPUT

    def execute(self, ctx, p):
        ctx.obs.request("ToggleVirtualCam")

    def feedback(self, ctx, p):
        return {"active": ctx.obs.state.virtualcam}


@register
class ReplayBufferToggle(Action):
    id = "obs.replay"
    name = "Replay buffer on/off"
    category = CAT_OUTPUT

    def execute(self, ctx, p):
        ctx.obs.request("ToggleReplayBuffer")

    def feedback(self, ctx, p):
        return {"active": ctx.obs.state.replay_active}


@register
class ReplaySave(Action):
    id = "obs.replay_save"
    name = "Guardar replay"
    category = CAT_OUTPUT

    def execute(self, ctx, p):
        ctx.obs.request("SaveReplayBuffer")


@register
class Screenshot(Action):
    id = "obs.screenshot"
    name = "Captura de fuente"
    category = CAT_OUTPUT
    description = "Guarda una captura PNG de una fuente o escena."
    params = [
        Param("source", "Fuente o escena", choices_source="sources_in_scene"),
        Param("directory", "Carpeta destino", default=str(Path.home() / "Imágenes")),
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
    name = "Silenciar entrada"
    category = CAT_AUDIO
    description = "La tecla se ilumina cuando la entrada está silenciada."
    params = [
        Param("input", "Entrada de audio", choices_source="inputs"),
        Param("mode", "Modo", kind="choice", default="alternar",
              choices=["alternar", "silenciar", "activar"]),
    ]

    def execute(self, ctx, p):
        name = p.get("input", "")
        mode = p.get("mode", "alternar")
        if mode == "alternar":
            ctx.obs.request("ToggleInputMute", {"inputName": name})
        else:
            ctx.obs.request(
                "SetInputMute", {"inputName": name, "inputMuted": mode == "silenciar"}
            )

    def feedback(self, ctx, p):
        muted = ctx.obs.state.muted.get(p.get("input", ""))
        return {"active": bool(muted), "color": COLOR_REC if muted else None}


@register
class VolumeAdjust(Action):
    id = "obs.volume_adjust"
    name = "Subir/bajar volumen"
    category = CAT_AUDIO
    params = [
        Param("input", "Entrada de audio", choices_source="inputs"),
        Param("delta_db", "Cambio (dB, negativo baja)", kind="float", default=3.0),
    ]

    def execute(self, ctx, p):
        name = p.get("input", "")
        d = ctx.obs.request("GetInputVolume", {"inputName": name})
        new_db = max(-100.0, min(26.0, d.get("inputVolumeDb", 0.0) + float(p.get("delta_db") or 0)))
        ctx.obs.request("SetInputVolume", {"inputName": name, "inputVolumeDb": new_db})


@register
class VolumeSet(Action):
    id = "obs.volume_set"
    name = "Fijar volumen"
    category = CAT_AUDIO
    params = [
        Param("input", "Entrada de audio", choices_source="inputs"),
        Param("db", "Volumen (dB)", kind="float", default=0.0),
    ]

    def execute(self, ctx, p):
        ctx.obs.request(
            "SetInputVolume",
            {"inputName": p.get("input", ""), "inputVolumeDb": float(p.get("db") or 0)},
        )


# ======================= Fuentes y filtros ===========================

@register
class SourceVisibility(Action):
    id = "obs.source_visibility"
    name = "Mostrar/ocultar fuente"
    category = CAT_SOURCES
    params = [
        Param("scene", "Escena", choices_source="scenes"),
        Param("source", "Fuente", choices_source="sources_in_scene"),
        Param("mode", "Modo", kind="choice", default="alternar",
              choices=["alternar", "mostrar", "ocultar"]),
    ]

    def _item_id(self, ctx, p) -> tuple[str, int]:
        scene = p.get("scene") or ctx.obs.state.current_scene
        d = ctx.obs.request(
            "GetSceneItemId", {"sceneName": scene, "sourceName": p.get("source", "")}
        )
        return scene, d["sceneItemId"]

    def execute(self, ctx, p):
        scene, item_id = self._item_id(ctx, p)
        mode = p.get("mode", "alternar")
        if mode == "alternar":
            d = ctx.obs.request(
                "GetSceneItemEnabled", {"sceneName": scene, "sceneItemId": item_id}
            )
            enabled = not d.get("sceneItemEnabled", True)
        else:
            enabled = mode == "mostrar"
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
    name = "Activar/desactivar filtro"
    category = CAT_SOURCES
    params = [
        Param("source", "Fuente", choices_source="sources_in_scene"),
        Param("filter", "Filtro", choices_source="filters_of_source"),
        Param("mode", "Modo", kind="choice", default="alternar",
              choices=["alternar", "activar", "desactivar"]),
    ]

    def execute(self, ctx, p):
        source, filt = p.get("source", ""), p.get("filter", "")
        mode = p.get("mode", "alternar")
        if mode == "alternar":
            d = ctx.obs.request(
                "GetSourceFilter", {"sourceName": source, "filterName": filt}
            )
            enabled = not d.get("filterEnabled", True)
        else:
            enabled = mode == "activar"
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


# =============================== Media ===============================

@register
class MediaControl(Action):
    id = "obs.media"
    name = "Control de media"
    category = CAT_MEDIA
    description = "Controla fuentes de vídeo/audio (Fuente multimedia, VLC)."
    params = [
        Param("input", "Fuente de media", choices_source="media_inputs"),
        Param("op", "Operación", kind="choice", default="reproducir/pausar",
              choices=["reproducir/pausar", "reiniciar", "detener", "siguiente", "anterior"]),
    ]

    _OPS = {
        "reiniciar": "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART",
        "detener": "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_STOP",
        "siguiente": "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_NEXT",
        "anterior": "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_PREVIOUS",
    }

    def execute(self, ctx, p):
        name = p.get("input", "")
        op = p.get("op", "reproducir/pausar")
        if op == "reproducir/pausar":
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


# ============================= Avanzado ==============================

@register
class HotkeyTrigger(Action):
    id = "obs.hotkey"
    name = "Hotkey de OBS"
    category = CAT_ADVANCED
    description = "Dispara cualquier hotkey interno de OBS por nombre."
    params = [Param("hotkey", "Hotkey", choices_source="hotkeys")]

    def execute(self, ctx, p):
        ctx.obs.request("TriggerHotkeyByName", {"hotkeyName": p.get("hotkey", "")})


@register
class RawRequest(Action):
    id = "obs.raw"
    name = "Petición cruda (avanzado)"
    category = CAT_ADVANCED
    description = (
        "Envía cualquier petición del protocolo obs-websocket v5. "
        "Cobertura del 100% del API."
    )
    params = [
        Param("request_type", "Tipo de petición", default="GetVersion"),
        Param("payload", "Payload JSON", default="{}"),
    ]

    def execute(self, ctx, p):
        payload = json.loads(p.get("payload") or "{}")
        result = ctx.obs.request(p.get("request_type", ""), payload or None)
        log.info("Respuesta de %s: %s", p.get("request_type"), result)


# Iconos por defecto (biblioteca integrada). Se usan si la tecla no tiene icono propio.
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
    "obs.media": "mdi:play-circle",
    "obs.hotkey": "mdi:keyboard",
    "obs.raw": "mdi:code-json",
})
