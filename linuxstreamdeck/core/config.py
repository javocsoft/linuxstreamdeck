"""Modelo de configuración y persistencia en JSON.

Archivo: ~/.config/linuxstreamdeck/config.json
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / ".config" / "linuxstreamdeck"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_KEY_BG = "#1e1e28"

# Tipos de tecla
KIND_SINGLE = "single"          # una sola acción (con feedback de estado)
KIND_MULTI = "multi"            # lista ordenada de acciones ejecutadas en secuencia
KIND_TOGGLE = "multi_toggle"    # conmutable: dos listas de acciones (estado ON/OFF)


@dataclass
class ActionStep:
    """Un paso dentro de una tecla de acciones múltiples."""
    action: str = ""                      # id de acción registrada
    params: dict = field(default_factory=dict)
    delay_ms: int = 0                     # espera tras ejecutar este paso


@dataclass
class KeyConfig:
    kind: str = KIND_SINGLE

    # kind == single
    action: str = ""                      # id de acción registrada, p.ej. "obs.scene_switch"
    params: dict = field(default_factory=dict)

    # kind == multi
    steps: list[ActionStep] = field(default_factory=list)

    # kind == multi_toggle (steps_on se ejecuta al pasar a ON; steps_off al pasar a OFF)
    steps_on: list[ActionStep] = field(default_factory=list)
    steps_off: list[ActionStep] = field(default_factory=list)

    # apariencia (estado principal / ON en las conmutables)
    label: str = ""
    icon: str = ""                        # ruta a una imagen opcional
    bg_color: str = DEFAULT_KEY_BG

    # apariencia del estado OFF (solo conmutables)
    label_off: str = ""
    icon_off: str = ""
    bg_color_off: str = DEFAULT_KEY_BG

    def is_empty(self) -> bool:
        if self.kind == KIND_SINGLE:
            return not self.action
        if self.kind == KIND_MULTI:
            return not self.steps
        if self.kind == KIND_TOGGLE:
            return not (self.steps_on or self.steps_off)
        return True

    @classmethod
    def from_dict(cls, d: dict) -> "KeyConfig":
        def steps(name: str) -> list[ActionStep]:
            return [
                ActionStep(
                    action=s.get("action", ""),
                    params=s.get("params", {}),
                    delay_ms=s.get("delay_ms", 0),
                )
                for s in d.get(name, [])
            ]

        return cls(
            kind=d.get("kind", KIND_SINGLE),
            action=d.get("action", ""),
            params=d.get("params", {}),
            steps=steps("steps"),
            steps_on=steps("steps_on"),
            steps_off=steps("steps_off"),
            label=d.get("label", ""),
            icon=d.get("icon", ""),
            bg_color=d.get("bg_color", DEFAULT_KEY_BG),
            label_off=d.get("label_off", ""),
            icon_off=d.get("icon_off", ""),
            bg_color_off=d.get("bg_color_off", DEFAULT_KEY_BG),
        )


@dataclass
class Page:
    name: str = "Página 1"
    # claves como str por compatibilidad JSON: "0".."14"
    keys: dict[str, KeyConfig] = field(default_factory=dict)

    def key(self, index: int) -> KeyConfig | None:
        return self.keys.get(str(index))

    def set_key(self, index: int, kc: KeyConfig | None) -> None:
        if kc is None:
            self.keys.pop(str(index), None)
        else:
            self.keys[str(index)] = kc


@dataclass
class ObsSettings:
    host: str = "localhost"
    port: int = 4455
    password: str = ""


@dataclass
class Config:
    pages: list[Page] = field(default_factory=lambda: [Page()])
    current_page: int = 0
    obs: ObsSettings = field(default_factory=ObsSettings)
    brightness: int = 80

    # ---------- persistencia ----------

    @classmethod
    def load(cls) -> "Config":
        if not CONFIG_FILE.exists():
            return cls()
        try:
            raw = json.loads(CONFIG_FILE.read_text())
            pages = [
                Page(
                    name=p.get("name", f"Página {i + 1}"),
                    keys={k: KeyConfig.from_dict(v) for k, v in p.get("keys", {}).items()},
                )
                for i, p in enumerate(raw.get("pages", []))
            ] or [Page()]
            cfg = cls(
                pages=pages,
                current_page=min(raw.get("current_page", 0), len(pages) - 1),
                obs=ObsSettings(**raw.get("obs", {})),
                brightness=raw.get("brightness", 80),
            )
            return cfg
        except Exception:
            log.exception("No se pudo leer %s; se usa configuración vacía", CONFIG_FILE)
            return cls()

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False))
        log.debug("Configuración guardada en %s", CONFIG_FILE)
