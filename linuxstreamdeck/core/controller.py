"""Controlador central: une configuración, dispositivo, acciones y UI.

- Recibe pulsaciones (físicas por `deck.key` o virtuales desde la UI) y
  ejecuta la acción configurada en un hilo de trabajo.
- Re-renderiza la página activa cuando cambia el estado de OBS, la página o
  la configuración, enviando cada imagen al deck físico y a la UI.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import actions as action_registry
from .actions import ActionContext
from .config import (
    KIND_MULTI,
    KIND_SINGLE,
    KIND_TOGGLE,
    ActionStep,
    Config,
    KeyConfig,
)
from .events import EventBus
from ..device.manager import DeckManager
from ..device import renderer

log = logging.getLogger(__name__)


class DeckController:
    def __init__(self, config: Config, bus: EventBus, obs, deck: DeckManager) -> None:
        self.config = config
        self.bus = bus
        self.obs = obs
        self.deck = deck
        self.ctx = ActionContext(obs=obs, controller=self, bus=bus)
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="action")
        self._render_pending = threading.Event()
        # estado ON/OFF de las teclas conmutables, por (página, tecla)
        self._toggle: dict[tuple[int, int], bool] = {}

        bus.subscribe("deck.key", self._on_deck_key)
        bus.subscribe("deck.connected", lambda t, d: self.refresh())
        bus.subscribe("obs.state", lambda t, d: self.refresh())

    # ---------- páginas ----------

    @property
    def current_page(self) -> int:
        return self.config.current_page

    @property
    def page(self):
        return self.config.pages[self.config.current_page]

    def set_page(self, index: int) -> None:
        if 0 <= index < len(self.config.pages):
            self.config.current_page = index
            self.config.save()
            self.bus.emit("page.changed", index=index, name=self.page.name)
            self.refresh()

    def set_page_by_name(self, name: str) -> None:
        for i, p in enumerate(self.config.pages):
            if p.name == name:
                self.set_page(i)
                return

    def add_page(self, name: str) -> None:
        from .config import Page

        self.config.pages.append(Page(name=name))
        self.config.save()
        self.set_page(len(self.config.pages) - 1)

    # ---------- edición de teclas (mover / copiar / pegar / limpiar) ----------

    def swap_keys(self, a: int, b: int) -> None:
        """Intercambia dos teclas (para drag & drop). También su estado ON/OFF."""
        if a == b:
            return
        page = self.page
        page.keys[str(a)], page.keys[str(b)] = page.key(b), page.key(a)
        # limpiar entradas None que set_key normalmente elimina
        for i in (a, b):
            if page.keys.get(str(i)) is None:
                page.keys.pop(str(i), None)
        # el estado de conmutación viaja con la tecla
        pa, pb = (self.current_page, a), (self.current_page, b)
        sa, sb = self._toggle.pop(pa, None), self._toggle.pop(pb, None)
        if sb is not None:
            self._toggle[pa] = sb
        if sa is not None:
            self._toggle[pb] = sa
        self.config.save()
        self.refresh()

    def paste_key(self, index: int, kc: KeyConfig) -> None:
        """Coloca una copia independiente de kc en la posición index."""
        self.page.set_key(index, kc.clone())
        self._toggle.pop((self.current_page, index), None)  # estado nuevo (OFF)
        self.config.save()
        self.refresh()

    def clear_key(self, index: int) -> None:
        self.page.set_key(index, None)
        self._toggle.pop((self.current_page, index), None)
        self.config.save()
        self.refresh()

    # ---------- pulsaciones ----------

    def _on_deck_key(self, topic: str, data: dict) -> None:
        if data.get("pressed"):
            self.press(data["index"])

    def toggle_state(self, index: int) -> bool:
        """Estado ON/OFF de una tecla conmutable en la página actual."""
        return self._toggle.get((self.current_page, index), False)

    def press(self, index: int) -> None:
        """Ejecuta la tecla (pulsación física o virtual) según su tipo."""
        kc = self.page.key(index)
        if kc is None or kc.is_empty():
            return
        if kc.kind == KIND_SINGLE:
            steps = [ActionStep(action=kc.action, params=kc.params)]
        elif kc.kind == KIND_MULTI:
            steps = list(kc.steps)
        elif kc.kind == KIND_TOGGLE:
            key = (self.current_page, index)
            new_state = not self._toggle.get(key, False)
            self._toggle[key] = new_state
            steps = list(kc.steps_on if new_state else kc.steps_off)
            self.refresh()  # refleja el nuevo estado en la tecla
        else:
            return
        self._executor.submit(self._run_steps, steps, index)

    def _run_steps(self, steps: list[ActionStep], index: int) -> None:
        for step in steps:
            action = action_registry.get(step.action)
            if action is None:
                if step.action:
                    log.warning("Acción desconocida en tecla %d: %s", index, step.action)
                continue
            try:
                action.execute(self.ctx, dict(step.params))
                log.debug("Ejecutada %s (tecla %d)", action.id, index)
            except Exception as e:
                log.exception("Error ejecutando %s", action.id)
                self.bus.emit("status", text=f"Error en «{action.name}»: {e}")
            if step.delay_ms > 0:
                time.sleep(step.delay_ms / 1000)

    # ---------- renderizado ----------

    def refresh(self) -> None:
        """Re-renderiza la página activa (agrupa ráfagas de eventos)."""
        if self._render_pending.is_set():
            return
        self._render_pending.set()
        self._executor.submit(self._render_page)

    def _render_page(self) -> None:
        self._render_pending.clear()
        size = self.deck.image_size
        for index in range(self.deck.key_count):
            spec = self._key_spec(index, self.page.key(index), size)
            image = renderer.compose(**spec)
            self.deck.set_key_image(index, image)
            self.bus.emit("ui.key_image", index=index, png=renderer.to_png_bytes(image))

    # ---------- specs de tecla (feedback + parámetros de composición) ----------

    def _key_spec(self, index: int, kc: KeyConfig | None, size) -> dict:
        if kc is None or kc.is_empty():
            return {"size": size}
        if kc.kind == KIND_TOGGLE:
            return self._toggle_spec(index, kc, size)
        if kc.kind == KIND_MULTI:
            icon = kc.icon or self._first_step_icon(kc.steps) or "mdi:playlist-play"
            return {"size": size, "label": kc.label, "icon_path": icon,
                    "bg": kc.bg_color, "badge": "⋯"}
        return self._single_spec(kc, size)

    @staticmethod
    def _first_step_icon(steps) -> str:
        for step in steps:
            action = action_registry.get(step.action)
            if action is not None and action.default_icon:
                return action.default_icon
        return ""

    def _single_spec(self, kc: KeyConfig, size) -> dict:
        fb = None
        action = action_registry.get(kc.action)
        if action is not None:
            try:
                fb = action.feedback(self.ctx, kc.params)
            except Exception:
                log.debug("feedback de %s falló", kc.action, exc_info=True)
        fb = fb or {}
        # icono propio de la tecla, o el icono por defecto de la acción. Solo se
        # muestra la etiqueta que el usuario haya puesto (sin ella el icono queda
        # centrado; no se mete el nombre de la acción, que lo desplazaba arriba).
        icon = kc.icon or (action.default_icon if action else "")
        return {
            "size": size,
            "label": kc.label,
            "icon_path": icon,
            "bg": fb.get("color") or kc.bg_color,
            "active": fb.get("active", False),
            "badge": fb.get("badge", ""),
        }

    def _toggle_spec(self, index: int, kc: KeyConfig, size) -> dict:
        if self.toggle_state(index):
            icon = kc.icon or self._first_step_icon(kc.steps_on) or "mdi:toggle-switch"
            return {"size": size, "label": kc.label, "icon_path": icon,
                    "bg": kc.bg_color, "active": True, "badge": "ON"}
        icon = (kc.icon_off or kc.icon
                or self._first_step_icon(kc.steps_off) or "mdi:toggle-switch-off-outline")
        return {"size": size, "label": kc.label_off or kc.label, "icon_path": icon,
                "bg": kc.bg_color_off, "badge": "OFF"}

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
