"""Panel editor de teclas.

Permite elegir el tipo de tecla:
  - Acción simple            → una acción (con feedback de estado)
  - Acciones múltiples       → lista ordenada de acciones ejecutadas en secuencia
  - Conmutable (ON/OFF)      → dos listas de acciones, una por estado

La lógica de un paso (categoría/acción/parámetros) y la apariencia viven en
`steps.py`; aquí solo se componen según el tipo elegido.
"""

from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ..core.config import (  # noqa: E402
    KIND_MULTI,
    KIND_SINGLE,
    KIND_TOGGLE,
    ActionStep,
    KeyConfig,
)
from .steps import AppearanceBox, StepEditor, StepList  # noqa: E402

log = logging.getLogger(__name__)

KINDS = [
    (KIND_SINGLE, "Acción simple"),
    (KIND_MULTI, "Acciones múltiples"),
    (KIND_TOGGLE, "Conmutable (ON/OFF)"),
]
KIND_IDS = [k for k, _ in KINDS]


class EditorPanel(Gtk.Box):
    def __init__(self, app) -> None:
        super().__init__(
            orientation=Gtk.Orientation.VERTICAL, spacing=10,
            margin_top=14, margin_bottom=14, margin_start=14, margin_end=14,
        )
        self.app = app
        self.index: int | None = None
        self._building = False

        self.title = Gtk.Label(xalign=0)
        self.title.add_css_class("title-3")
        self.append(self.title)

        self.kind_dd = Gtk.DropDown.new_from_strings([name for _, name in KINDS])
        self.kind_row = self._labelled("Tipo de tecla", self.kind_dd)
        self.append(self.kind_row)
        self.kind_dd.connect("notify::selected", self._on_kind_changed)

        # cuerpo con scroll: crece para ocupar el espacio disponible y desplaza
        # su contenido cuando se añaden pasos, sin agrandar la ventana
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.scroller = Gtk.ScrolledWindow(child=self.body)
        self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroller.set_vexpand(True)
        self.scroller.set_min_content_height(180)
        self.append(self.scroller)

        # botones fijos al pie (siempre visibles aunque el cuerpo desborde)
        self.buttons = self._build_buttons()
        self.append(self.buttons)

        # sub-widgets según el tipo (se rellenan en _build_body)
        self.single_editor: StepEditor | None = None
        self.multi_list: StepList | None = None
        self.on_list: StepList | None = None
        self.off_list: StepList | None = None
        self.app_main: AppearanceBox | None = None
        self.app_off: AppearanceBox | None = None

        self.clear()

    # ---------- API pública ----------

    def clear(self) -> None:
        self.title.set_label("Ninguna tecla seleccionada")
        self.kind_row.set_visible(False)
        self.buttons.set_visible(False)
        self._clear(self.body)
        info = Gtk.Label(
            label="Selecciona una tecla de la rejilla para configurarla.",
            wrap=True, xalign=0,
        )
        info.add_css_class("dim-label")
        self.body.append(info)
        self.index = None

    def load(self, index: int) -> None:
        self.index = index
        kc = self.app.controller.page.key(index) or KeyConfig()
        self.title.set_label(f"Tecla {index + 1}")
        self.kind_row.set_visible(True)
        self.buttons.set_visible(True)
        self._building = True
        self.kind_dd.set_selected(KIND_IDS.index(kc.kind) if kc.kind in KIND_IDS else 0)
        self._building = False
        self._build_body(kc)

    # ---------- construcción del cuerpo ----------

    def _on_kind_changed(self, *_a) -> None:
        if self._building or self.index is None:
            return
        # cambiar de tipo arranca con una configuración vacía de ese tipo
        self._build_body(KeyConfig(kind=self._current_kind()))

    def _current_kind(self) -> str:
        i = self.kind_dd.get_selected()
        return KIND_IDS[i] if i != Gtk.INVALID_LIST_POSITION else KIND_SINGLE

    def _build_body(self, kc: KeyConfig) -> None:
        self._clear(self.body)
        self.single_editor = self.multi_list = None
        self.on_list = self.off_list = self.app_main = self.app_off = None
        kind = self._current_kind()

        if kind == KIND_SINGLE:
            self.single_editor = StepEditor(self.app)
            self.single_editor.load(ActionStep(action=kc.action, params=kc.params))
            self.body.append(self.single_editor)
            self.body.append(Gtk.Separator())
            self.app_main = AppearanceBox("Apariencia")
            self.app_main.load(kc.label, kc.icon, kc.bg_color)
            self.body.append(self.app_main)

        elif kind == KIND_MULTI:
            self.body.append(self._hint(
                "Se ejecutan en orden al pulsar. Usa «Espera después» para pausas."
            ))
            self.multi_list = StepList(self.app)
            self.multi_list.load(kc.steps or [ActionStep()])
            self.body.append(self.multi_list)
            self.body.append(Gtk.Separator())
            self.app_main = AppearanceBox("Apariencia")
            self.app_main.load(kc.label, kc.icon, kc.bg_color)
            self.body.append(self.app_main)

        else:  # KIND_TOGGLE
            self.body.append(self._hint(
                "Cada pulsación alterna el estado y ejecuta su lista de acciones."
            ))
            self.on_list = StepList(self.app)
            self.on_list.load(kc.steps_on or [ActionStep()])
            self.app_main = AppearanceBox("Apariencia estado ON")
            self.app_main.load(kc.label, kc.icon, kc.bg_color)
            self.body.append(self._frame("▶ Estado ON", [self.on_list, self.app_main]))

            self.off_list = StepList(self.app)
            self.off_list.load(kc.steps_off or [ActionStep()])
            self.app_off = AppearanceBox("Apariencia estado OFF")
            self.app_off.load(kc.label_off, kc.icon_off, kc.bg_color_off)
            self.body.append(self._frame("■ Estado OFF", [self.off_list, self.app_off]))

    def _build_buttons(self) -> Gtk.Box:
        btns = Gtk.Box(spacing=6, margin_top=10)
        save = Gtk.Button(label="Guardar", hexpand=True)
        save.add_css_class("suggested-action")
        save.connect("clicked", self._save)
        test = Gtk.Button(label="Probar")
        test.connect("clicked", lambda _b: self.app.controller.press(self.index))
        wipe = Gtk.Button(label="Limpiar")
        wipe.add_css_class("destructive-action")
        wipe.connect("clicked", self._wipe)
        for b in (save, test, wipe):
            btns.append(b)
        return btns

    # ---------- guardar / limpiar ----------

    def _save(self, _btn) -> None:
        if self.index is None:
            return
        kind = self._current_kind()
        kc = KeyConfig(kind=kind)

        if kind == KIND_SINGLE:
            step = self.single_editor.get_step()
            kc.action, kc.params = step.action, step.params
        elif kind == KIND_MULTI:
            kc.steps = self.multi_list.get_steps()
        else:
            kc.steps_on = self.on_list.get_steps()
            kc.steps_off = self.off_list.get_steps()
            kc.label_off = self.app_off.label()
            kc.icon_off = self.app_off.icon()
            kc.bg_color_off = self.app_off.color()

        kc.label = self.app_main.label()
        kc.icon = self.app_main.icon()
        kc.bg_color = self.app_main.color()

        self.app.controller.page.set_key(self.index, kc)
        self.app.config.save()
        self.app.controller.refresh()
        name = dict(KINDS)[kind]
        self.app.bus.emit("status", text=f"Tecla {self.index + 1} guardada ({name})")

    def _wipe(self, _btn) -> None:
        if self.index is None:
            return
        self.app.controller.page.set_key(self.index, None)
        self.app.config.save()
        self.app.controller.refresh()
        self.load(self.index)

    # ---------- helpers de layout ----------

    @staticmethod
    def _labelled(label: str, widget: Gtk.Widget) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        lbl = Gtk.Label(label=label, xalign=0)
        lbl.add_css_class("caption-heading")
        box.append(lbl)
        box.append(widget)
        return box

    @staticmethod
    def _hint(text: str) -> Gtk.Label:
        lbl = Gtk.Label(label=text, wrap=True, xalign=0)
        lbl.add_css_class("dim-label")
        return lbl

    @staticmethod
    def _frame(title: str, children: list[Gtk.Widget]) -> Gtk.Frame:
        frame = Gtk.Frame(label=title)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                      margin_top=8, margin_bottom=8, margin_start=8, margin_end=8)
        for i, child in enumerate(children):
            if i:
                box.append(Gtk.Separator())
            box.append(child)
        frame.set_child(box)
        return frame

    @staticmethod
    def _clear(box: Gtk.Box) -> None:
        while (child := box.get_first_child()) is not None:
            box.remove(child)
