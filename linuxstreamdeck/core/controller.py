"""Central controller: ties together configuration, device, actions and UI.

- Receives key presses (physical via `deck.key` or virtual from the UI) and
  runs configured actions on worker threads.
- Re-renders the active page when the OBS state, the page or the configuration
  changes, sending each image to the physical deck and to the UI. Rendering has
  its own worker so long actions cannot starve visual feedback.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import actions as action_registry
from .actions import ActionContext
from .config import (
    KIND_MULTI,
    KIND_SINGLE,
    KIND_TOGGLE,
    ActionStep,
    Config,
    ImportResult,
    KeyConfig,
)
from .events import EventBus
from ..device.manager import DeckManager
from ..device import renderer

log = logging.getLogger(__name__)

BUSY_PULSE_SECONDS = 0.75


class _ExecutionControl:
    def __init__(self, predecessor: threading.Event | None = None) -> None:
        self.cancel = threading.Event()
        self.finished = threading.Event()
        self.predecessor = predecessor


class DeckController:
    def __init__(self, config: Config, bus: EventBus, obs, deck: DeckManager) -> None:
        self.config = config
        self.bus = bus
        self.obs = obs
        self.deck = deck
        self.ctx = ActionContext(obs=obs, controller=self, bus=bus)
        self._action_executor = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="action",
        )
        # Rendering must not wait behind long-running actions such as Wait.
        self._render_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="render",
        )
        self._render_pending = threading.Event()
        self._stopping = threading.Event()
        # ON/OFF state of toggle keys, keyed by (profile, page, key)
        self._toggle: dict[tuple[int, int, int], bool] = {}
        # Number of queued/running feedback-enabled invocations for each key.
        self._running: dict[tuple[int, int, int], int] = {}
        self._running_lock = threading.Lock()
        self._busy_phase = False
        self._busy_wakeup = threading.Event()
        # Restartable executions (currently audio) are replaced per key.
        self._execution_lock = threading.Lock()
        self._execution_controls: dict[
            tuple[int, int, int],
            _ExecutionControl,
        ] = {}
        self._busy_thread = threading.Thread(
            target=self._busy_loop,
            daemon=True,
            name="key-activity",
        )
        self._busy_thread.start()

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
        if (
            0 <= index < len(self.config.pages)
            and index != self.current_page
        ):
            self.config.current_page = index
            self.config.save()
            self.bus.emit("page.changed", index=index, name=self.page.name)
            self.refresh()

    def set_page_by_name(self, name: str) -> bool:
        for i, p in enumerate(self.config.pages):
            if p.name == name:
                self.set_page(i)
                return True
        return False

    def add_page(self, name: str) -> None:
        from .config import Page

        if any(page.name == name for page in self.config.pages):
            self.bus.emit(
                "status",
                text=f"A page named {name} already exists",
            )
            return
        self.config.pages.append(Page(name=name))
        self.config.save()
        self.set_page(len(self.config.pages) - 1)

    def rename_page(self, name: str) -> None:
        if not name:
            return
        old_name = self.page.name
        if name == old_name:
            return
        if any(page.name == name for page in self.config.pages):
            self.bus.emit(
                "status",
                text=f"A page named {name} already exists",
            )
            return
        self.page.name = name
        for page in self.config.pages:
            for key in page.keys.values():
                for action, params in self.config._action_params(key):
                    if (
                        action == "nav.page.go"
                        and params.get("page") == old_name
                    ):
                        params["page"] = name
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

    # ---------- configuration import ----------

    def import_configuration(self, source: Path) -> ImportResult:
        """Replace the configuration and apply its runtime settings."""
        result = self.config.import_bundle(source)
        self._toggle.clear()
        self.deck.set_brightness(self.config.brightness)
        cfg = self.config.obs
        self.obs.configure(cfg.host, cfg.port, cfg.password)
        self.obs.reconnect_now()
        profile = self.config.profile
        self.bus.emit(
            "profile.changed",
            index=self.current_profile,
            name=profile.name,
            description=profile.description,
        )
        self.bus.emit(
            "page.changed",
            index=self.current_page,
            name=self.page.name,
        )
        self.refresh()
        return result

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
        else:
            return
        single_action = (
            action_registry.get(kc.action)
            if kc.kind == KIND_SINGLE
            else None
        )
        show_running = (
            kc.kind in (KIND_MULTI, KIND_TOGGLE)
            or bool(single_action and single_action.running_feedback)
        )
        execution_key = self._tkey(index)
        runtime_key = execution_key if show_running else None
        control = self._prepare_execution(execution_key, steps)
        if runtime_key is not None:
            self._begin_running(runtime_key)
        try:
            self._action_executor.submit(
                self._run_steps,
                steps,
                index,
                runtime_key,
                control,
                execution_key,
            )
        except RuntimeError:
            if control is not None:
                self._finish_execution(execution_key, control)
            if runtime_key is not None:
                self._end_running(runtime_key)
            if not self._stopping.is_set():
                raise

    def _run_steps(
        self,
        steps: list[ActionStep],
        index: int,
        runtime_key: tuple[int, int, int] | None = None,
        control: _ExecutionControl | None = None,
        execution_key: tuple[int, int, int] | None = None,
    ) -> None:
        run_ctx = ActionContext(
            obs=self.obs,
            controller=self,
            bus=self.bus,
            cancellation=control.cancel if control is not None else None,
        )
        try:
            if control is not None and control.predecessor is not None:
                while not control.predecessor.wait(0.05):
                    # The predecessor also observes shutdown and will finish.
                    if self._stopping.is_set():
                        return
            for step in steps:
                if run_ctx.stop_requested():
                    return
                action = action_registry.get(step.action)
                if action is None:
                    if step.action:
                        log.warning("Unknown action on key %d: %s", index, step.action)
                    continue
                try:
                    action.execute(run_ctx, dict(step.params))
                    log.debug("Ran %s (key %d)", action.id, index)
                except Exception as e:
                    log.exception("Error running %s", action.id)
                    self.bus.emit("status", text=f"Error in «{action.name}»: {e}")
        finally:
            if control is not None:
                self._finish_execution(
                    execution_key or runtime_key or self._tkey(index),
                    control,
                )
            if runtime_key is not None:
                self._end_running(runtime_key)

    # ---------- replaceable executions ----------

    def _prepare_execution(
        self,
        key: tuple[int, int, int],
        steps: list[ActionStep],
    ) -> _ExecutionControl | None:
        restartable = any(
            bool(
                (action := action_registry.get(step.action))
                and action.restart_on_repress
            )
            for step in steps
        )
        with self._execution_lock:
            previous = self._execution_controls.get(key)
            if previous is not None:
                previous.cancel.set()
            if not restartable and previous is None:
                return None
            control = _ExecutionControl(
                previous.finished if previous is not None else None
            )
            if restartable:
                self._execution_controls[key] = control
            else:
                self._execution_controls.pop(key, None)
            return control

    def _finish_execution(
        self,
        key: tuple[int, int, int],
        control: _ExecutionControl,
    ) -> None:
        control.finished.set()
        with self._execution_lock:
            if self._execution_controls.get(key) is control:
                self._execution_controls.pop(key, None)

    # ---------- running feedback ----------

    def _begin_running(self, key: tuple[int, int, int]) -> None:
        with self._running_lock:
            self._running[key] = self._running.get(key, 0) + 1
            self._busy_wakeup.set()
        self._refresh_runtime_keys((key,))

    def _end_running(self, key: tuple[int, int, int]) -> None:
        with self._running_lock:
            count = self._running.get(key, 0)
            if count <= 1:
                self._running.pop(key, None)
                became_idle = True
            else:
                self._running[key] = count - 1
                became_idle = False
        if became_idle:
            self._refresh_runtime_keys((key,))

    def _busy_state(self, index: int) -> tuple[bool, bool]:
        key = self._tkey(index)
        with self._running_lock:
            return key in self._running, self._busy_phase

    def _busy_loop(self) -> None:
        while not self._stopping.is_set():
            self._busy_wakeup.wait()
            if self._stopping.is_set():
                return
            if self._stopping.wait(BUSY_PULSE_SECONDS):
                return
            with self._running_lock:
                if not self._running:
                    self._busy_phase = False
                    self._busy_wakeup.clear()
                    continue
                self._busy_phase = not self._busy_phase
                keys = tuple(self._running)
            self._refresh_runtime_keys(keys)

    def _refresh_runtime_keys(
        self,
        keys: tuple[tuple[int, int, int], ...],
    ) -> None:
        if self._stopping.is_set():
            return
        view = (self.current_profile, self.current_page)
        indices = sorted({
            index
            for profile, page, index in keys
            if (profile, page) == view
        })
        if not indices:
            return
        try:
            self._render_executor.submit(self._render_keys, indices, view)
        except RuntimeError:
            if not self._stopping.is_set():
                raise

    # ---------- rendering ----------

    def refresh(self) -> None:
        """Re-render the active page (coalesces bursts of events)."""
        if self._stopping.is_set() or self._render_pending.is_set():
            return
        self._render_pending.set()
        try:
            self._render_executor.submit(self._render_page)
        except RuntimeError:
            self._render_pending.clear()
            if not self._stopping.is_set():
                raise

    def _render_page(self) -> None:
        self._render_pending.clear()
        view = (self.current_profile, self.current_page)
        self._render_keys(range(self.deck.key_count), view)

    def _render_keys(self, indices, view: tuple[int, int]) -> None:
        if view != (self.current_profile, self.current_page):
            return
        page = self.page
        size = self.deck.image_size
        for index in indices:
            if self._stopping.is_set():
                return
            spec = self._key_spec(index, page.key(index), size)
            image = renderer.compose(**spec)
            if view != (self.current_profile, self.current_page):
                return
            self.deck.set_key_image(index, image)
            self.bus.emit("ui.key_image", index=index, png=renderer.to_png_bytes(image))

    # ---------- key specs (feedback + compose parameters) ----------

    def _key_spec(self, index: int, kc: KeyConfig | None, size) -> dict:
        if kc is None or kc.is_empty():
            return {"size": size}
        if kc.kind == KIND_TOGGLE:
            return self._toggle_spec(index, kc, size)
        if kc.kind == KIND_MULTI:
            busy, phase = self._busy_state(index)
            icon = kc.icon or self._first_step_icon(kc.steps) or "mdi:playlist-play"
            return {"size": size, "label": kc.label, "icon_path": icon,
                    "bg": kc.bg_color, "busy": busy, "busy_phase": phase,
                    "badge": "RUN" if busy else "⋯"}
        return self._single_spec(index, kc, size)

    @staticmethod
    def _first_step_icon(steps) -> str:
        for step in steps:
            action = action_registry.get(step.action)
            if action is not None and action.default_icon:
                return action.default_icon
        return ""

    def _single_spec(self, index: int, kc: KeyConfig, size) -> dict:
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
        busy, phase = self._busy_state(index)
        return {
            "size": size,
            "label": kc.label,
            "icon_path": icon,
            "bg": fb.get("color") or kc.bg_color,
            "active": fb.get("active", False),
            "busy": busy,
            "busy_phase": phase,
            "badge": "RUN" if busy else fb.get("badge", ""),
        }

    def _toggle_spec(self, index: int, kc: KeyConfig, size) -> dict:
        busy, phase = self._busy_state(index)
        if self.toggle_state(index):
            icon = kc.icon or self._first_step_icon(kc.steps_on) or "mdi:toggle-switch"
            return {"size": size, "label": kc.label, "icon_path": icon,
                    "bg": kc.bg_color, "active": True, "busy": busy,
                    "busy_phase": phase, "badge": "RUN" if busy else "ON"}
        icon = (kc.icon_off or kc.icon
                or self._first_step_icon(kc.steps_off) or "mdi:toggle-switch-off-outline")
        return {"size": size, "label": kc.label_off or kc.label, "icon_path": icon,
                "bg": kc.bg_color_off, "busy": busy, "busy_phase": phase,
                "badge": "RUN" if busy else "OFF"}

    def wait_until_stopped(self, timeout: float) -> bool:
        """Wait for timeout seconds, returning early when shutdown begins."""
        return self._stopping.wait(timeout)

    def shutdown(self) -> None:
        """Cancel queued work and wait for running actions/renders to finish."""
        self._stopping.set()
        self._busy_wakeup.set()
        self._render_pending.clear()
        self._action_executor.shutdown(wait=True, cancel_futures=True)
        self._busy_thread.join()
        self._render_executor.shutdown(wait=True, cancel_futures=True)
