"""Acciones de sistema y de navegación entre páginas del deck."""

from __future__ import annotations

import logging
import subprocess
import webbrowser

from .core.actions import Action, Param, apply_default_icons, register

log = logging.getLogger(__name__)

CAT_SYSTEM = "Sistema"
CAT_NAV = "Navegación"


@register
class RunCommand(Action):
    id = "sys.command"
    name = "Ejecutar comando"
    category = CAT_SYSTEM
    description = "Ejecuta un comando de shell en segundo plano."
    params = [Param("command", "Comando")]

    def execute(self, ctx, p):
        cmd = p.get("command", "").strip()
        if cmd:
            subprocess.Popen(cmd, shell=True, start_new_session=True)


@register
class OpenUrl(Action):
    id = "sys.url"
    name = "Abrir URL"
    category = CAT_SYSTEM
    params = [Param("url", "URL", default="https://")]

    def execute(self, ctx, p):
        url = p.get("url", "").strip()
        if url:
            webbrowser.open(url)


@register
class PageGo(Action):
    id = "nav.page"
    name = "Ir a página"
    category = CAT_NAV
    description = "Cambia la página activa del deck."
    params = [
        Param("mode", "Modo", kind="choice", default="ir a",
              choices=["ir a", "siguiente", "anterior"]),
        Param("page", "Página (para 'ir a')", choices_source="pages"),
    ]

    def execute(self, ctx, p):
        c = ctx.controller
        mode = p.get("mode", "ir a")
        if mode == "siguiente":
            c.set_page((c.current_page + 1) % len(c.config.pages))
        elif mode == "anterior":
            c.set_page((c.current_page - 1) % len(c.config.pages))
        else:
            c.set_page_by_name(p.get("page", ""))


apply_default_icons({
    "sys.command": "mdi:console",
    "sys.url": "mdi:web",
    "nav.page": "mdi:book-open-page-variant",
})
