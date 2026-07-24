"""Central controller: ties together configuration, device, actions and UI.

- Receives key presses (physical via `deck.key` or virtual from the UI) and
  runs the configured action on a worker thread.
- Re-renders the active page when the OBS state, the page or the configuration
  changes, sending each image to the physical deck and to the UI.
"""

from __future__ import annotations

import logging
import threading
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
        self._stopping = threading.Event()
        # ON/OFF state of toggle keys, keyed by (profile, page, key)
        self._toggle: dict[tuple[int, int, int], bool] = {}

        bus.subscribe("deck.key", self._on_deck_key)
        bus.subscribe("deck.connected", lambda t, d: self.refresh())
        bus.subscribe("obs.state", lambda t, d: self.refresh())

    # ---------- pages ----------

    @property
    def current_profile(self) -> int:
        return self.config.current_profile

    @property
    def current_page(self) -> int:
        return self.config.current_page

    @property
    def page(self):
        return self.config.pages[self.config.current_page]

    def _tkey(self, index: int) -> tuple[int, int, int]:
        """Key of a toggle's ON/OFF state: (profile, page, key)."""
        return (self.current_profile, self.current_page, index)

    # ---------- profiles ----------

    def set_profile(self, index: int) -> None:
        """Switch profile (loads its set of pages/keys)."""
        if not (0 <= index < len(self.config.profiles)) or index == self.current_profile:
            return
        self.config.current_profile = index
        self.config.save()
        prof = self.config.profile
        self.bus.emit("profile.changed", index=index, name=prof.name,
                      description=prof.description)
        # the current page is defined by the profile itself
        self.bus.emit("page.changed", index=self.current_page, name=self.page.name)
        self.refresh()

    def add_profile(self, name: str, description: str = "") -> None:
        from .config import Profile

        self.config.profiles.append(Profile(name=name or "Profile", description=description))
        self.config.save()
        self.set_profile(len(self.config.profiles) - 1)

    def update_profile(self, name: str, description: str) -> None:
        prof = self.config.profile
        prof.name = name or prof.name
        prof.description = description
        self.config.save()
        self.bus.emit("profile.changed", index=self.current_profile,
                      name=prof.name, description=prof.description)

    def delete_profile(self, index: int) -> None:
        if len(self.config.profiles) <= 1:
            self.bus.emit("status", text="You can't delete the only profile")
            return
        del self.config.profiles[index]
        # no need to discard the deleted profile's ON/OFF states individually:
        # clear them all and re-render whichever profile becomes active.
        self._toggle.clear()
        self.config.current_profile = min(self.current_profile, len(self.config.profiles) - 1)
        self.config.save()
        prof = self.config.profile
        self.bus.emit("profile.changed", index=self.current_profile,
                      name=prof.name, description=prof.description)
        self.bus.emit("page.changed", index=self.current_page, name=self.page.name)
        self.refresh()

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

    def rename_page(self, name: str) -> None:
        if not name:
            return
        self.page.name = name
        self.config.save()
        self.bus.emit("page.changed", index=self.current_page, name=self.page.name)

    def delete_page(self, index: int) -> None:
        pages = self.config.pages
        if len(pages) <= 1:
            self.bus.emit("status", text="You can't delete the only page")
            return
        del pages[index]
        # the ON/OFF states of toggle keys reference the page index, which shifts
        # after deletion; they are transient, so clear them and re-render.
        self._toggle.clear()
        self.config.current_page = min(self.current_page, len(pages) - 1)
        self.config.save()
        self.bus.emit("page.changed", index=self.current_page, name=self.page.name)
        self.refresh()

    # ---------- key editing (move / copy / paste / clear) ----------

    def swap_keys(self, a: int, b: int) -> None:
        """Swap two keys (for drag & drop). Their ON/OFF state too."""
        if a == b:
            return
        page = self.page
        page.keys[str(a)], page.keys[str(b)] = page.key(b), page.key(a)
        # drop the None entries that set_key normally removes
        for i in (a, b):
            if page.keys.get(str(i)) is None:
                page.keys.pop(str(i), None)
        # the toggle state travels with the key
        pa, pb = self._tkey(a), self._tkey(b)
        sa, sb = self._toggle.pop(pa, None), self._toggle.pop(pb, None)
        if sb is not None:
            self._toggle[pa] = sb
        if sa is not None:
            self._toggle[pb] = sa
        self.config.save()
        self.refresh()

    def paste_key(self, index: int, kc: KeyConfig) -> None:
        """Place an independent copy of kc at position index."""
        self.page.set_key(index, kc.clone())
        self._toggle.pop(self._tkey(index), None)  # fresh state (OFF)
        self.config.save()
        self.refresh()

    def clear_key(self, index: int) -> None:
        self.page.set_key(index, None)
        self._toggle.pop(self._tkey(index), None)
        self.config.save()
        self.refresh()

    # ---------- presses ----------

    def _on_deck_key(self, topic: str, data: dict) -> None:
        if data.get("pressed"):
            self.press(data["index"])

    def toggle_state(self, index: int) -> bool:
        """ON/OFF state of a toggle key on the current page/profile."""
        return self._toggle.get(self._tkey(index), False)

    def press(self, index: int) -> None:
        """Run the key (physical or virtual press) according to its type."""
        if self._stopping.is_set():
            return
        kc = self.page.key(index)
        if kc is None or kc.is_empty():
            return
        if kc.kind == KIND_SINGLE:
            steps = [ActionStep(action=kc.action, params=kc.params)]
        elif kc.kind == KIND_MULTI:
            steps = list(kc.steps)
        elif kc.kind == KIND_TOGGLE:
            key = self._tkey(index)
            new_state = not self._toggle.get(key, False)
            self._toggle[key] = new_state
            steps = list(kc.steps_on if new_state else kc.steps_off)
            self.refresh()  # reflect the new state on the key
        else:
            return
        try:
            self._executor.submit(self._run_steps, steps, index)
        except RuntimeError:
            if not self._stopping.is_set():
                raise

    def _run_steps(self, steps: list[ActionStep], index: int) -> None:
        for step in steps:
            if self._stopping.is_set():
                return
            action = action_registry.get(step.action)
            if action is None:
                if step.action:
                    log.warning("Unknown action on key %d: %s", index, step.action)
                continue
            try:
                action.execute(self.ctx, dict(step.params))
                log.debug("Ran %s (key %d)", action.id, index)
            except Exception as e:
                log.exception("Error running %s", action.id)
                self.bus.emit("status", text=f"Error in «{action.name}»: {e}")

    # ---------- rendering ----------

    def refresh(self) -> None:
        """Re-render the active page (coalesces bursts of events)."""
        if self._stopping.is_set() or self._render_pending.is_set():
            return
        self._render_pending.set()
        try:
            self._executor.submit(self._render_page)
        except RuntimeError:
            self._render_pending.clear()
            if not self._stopping.is_set():
                raise

    def _render_page(self) -> None:
        self._render_pending.clear()
        size = self.deck.image_size
        for index in range(self.deck.key_count):
            if self._stopping.is_set():
                return
            spec = self._key_spec(index, self.page.key(index), size)
            image = renderer.compose(**spec)
            self.deck.set_key_image(index, image)
            self.bus.emit("ui.key_image", index=index, png=renderer.to_png_bytes(image))

    # ---------- key specs (feedback + compose parameters) ----------

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
                log.debug("feedback of %s failed", kc.action, exc_info=True)
        fb = fb or {}
        # the key's own icon, or the action's default icon. Only the label the
        # user set explicitly is shown (without it the icon stays centered; the
        # action name is not used, which used to push the icon upwards).
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

    def wait_until_stopped(self, timeout: float) -> bool:
        """Wait for timeout seconds, returning early when shutdown begins."""
        return self._stopping.wait(timeout)

    def shutdown(self) -> None:
        """Cancel queued work and wait for running actions/renders to finish."""
        self._stopping.set()
        self._render_pending.clear()
        self._executor.shutdown(wait=True, cancel_futures=True)
