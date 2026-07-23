"""Sistema de acciones: clase base, parámetros declarativos y registro global.

Cada acción declara sus parámetros con `Param`; el editor de la UI genera los
widgets automáticamente a partir de ellos. `choices_source` indica al editor
de dónde sacar las opciones en vivo (escenas de OBS, entradas de audio...).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class Param:
    name: str
    label: str
    kind: str = "string"          # string | int | float | choice
    default: Any = None
    choices: list[str] = field(default_factory=list)   # para kind == "choice"
    # Fuente dinámica de opciones que el editor rellena en vivo:
    #   scenes | inputs | media_inputs | transitions | scene_collections
    #   profiles | sources_in_scene | filters_of_source | hotkeys | pages
    choices_source: str = ""


class ActionContext:
    """Lo que una acción necesita para ejecutarse."""

    def __init__(self, obs, controller, bus):
        self.obs = obs                # linuxstreamdeck.obs.client.OBSClient
        self.controller = controller  # linuxstreamdeck.core.controller.DeckController
        self.bus = bus


class Action:
    id: str = ""
    name: str = ""
    category: str = ""
    params: list[Param] = []
    description: str = ""
    default_icon: str = ""      # referencia "mdi:..." usada si la tecla no tiene icono propio

    def execute(self, ctx: ActionContext, params: dict) -> None:
        raise NotImplementedError

    def feedback(self, ctx: ActionContext, params: dict) -> dict | None:
        """Estado visual de la tecla: {'active': bool, 'color': '#rrggbb', 'badge': str}.

        Devuelve None si la acción no tiene estado.
        """
        return None


REGISTRY: dict[str, Action] = {}


def register(cls: type[Action]) -> type[Action]:
    """Decorador: instancia y registra la acción."""
    inst = cls()
    if not inst.id:
        raise ValueError(f"La acción {cls.__name__} no tiene id")
    REGISTRY[inst.id] = inst
    return cls


def get(action_id: str) -> Action | None:
    return REGISTRY.get(action_id)


def apply_default_icons(mapping: dict[str, str]) -> None:
    """Asigna el icono por defecto (mdi:...) a las acciones ya registradas."""
    for action_id, ref in mapping.items():
        action = REGISTRY.get(action_id)
        if action is not None:
            action.default_icon = ref


def by_category() -> dict[str, list[Action]]:
    cats: dict[str, list[Action]] = {}
    for a in REGISTRY.values():
        cats.setdefault(a.category, []).append(a)
    return cats
