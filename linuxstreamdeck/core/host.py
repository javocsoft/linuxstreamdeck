"""Reaching the machine's own tools from inside a sandbox.

Nine features here work by running a program that belongs to the desktop rather
than to this application: `pactl` for per-application audio and the soundboard,
`playerctl` for media transport and what is playing, `ydotool` for shortcuts,
`avahi-browse` to find Key Lights, `nvidia-smi` for GPU load, `pgrep` to see
whether an application is running. Outside a sandbox they are simply on PATH.

Inside a Flatpak they are not, and `shutil.which` answers None for every one of
them, so the application would report the whole desktop as missing on a machine
where all of it is installed. `flatpak-spawn --host` runs a command on the
machine instead of in the sandbox, which is what this module wraps.

**That is a deliberate hole in the sandbox and it should be understood as
one.** The Flatpak asks for `--talk-name=org.freedesktop.Flatpak`, and an
application that can spawn host processes is not meaningfully confined - it is
the same permission a terminal emulator or an IDE asks for, and for the same
reason. It is granted here because the alternative is an application whose
audio, media and shortcut keys silently do nothing. Flathub is unlikely to
accept it; this is for a Flatpak you build and install yourself.

When the permission is *not* granted, `flatpak-spawn` fails and every caller
takes the path it already takes on a machine with the tool missing: a status
message naming what to install. Nothing here has to handle that specially.

Outside Flatpak this module is a pass-through, so no existing behaviour
changes.
"""

from __future__ import annotations

import functools
import logging
import os
import shutil
import subprocess

log = logging.getLogger(__name__)

SPAWN = "flatpak-spawn"
_FLATPAK_MARKER = "/.flatpak-info"
# Long enough for a cold host process, short enough that a wedged one cannot
# hold a render or action worker. The callers' own timeouts are 5 s.
_PROBE_TIMEOUT = 4


@functools.lru_cache(maxsize=1)
def in_flatpak() -> bool:
    """Whether this process is running inside a Flatpak sandbox.

    `/.flatpak-info` is the documented marker and exists in every Flatpak;
    `FLATPAK_ID` is checked too because a `flatpak run` of a shell for testing
    sets it, and getting the answer wrong in either direction is worse than
    checking twice.
    """
    return os.path.exists(_FLATPAK_MARKER) or bool(os.environ.get("FLATPAK_ID"))


def argv(command: list[str]) -> list[str]:
    """The command to actually execute, host-prefixed when sandboxed."""
    if not command or not in_flatpak():
        return command
    return [SPAWN, "--host", *command]


def which(name: str) -> str | None:
    """Where `name` is, looking at the machine rather than at the sandbox.

    Outside a sandbox this is `shutil.which` and nothing else, deliberately
    uncached: it is a PATH lookup, it costs nothing, and caching it would mean
    a tool installed while the application is running was never noticed.
    """
    if not in_flatpak():
        return shutil.which(name)
    return _host_which(name)


@functools.lru_cache(maxsize=64)
def _host_which(name: str) -> str | None:
    """The sandboxed answer, which costs a process and so is remembered.

    The availability checks that reach here run on the render worker, once per
    key repaint; spawning `which` on the host that often would be absurd.
    """
    if shutil.which(SPAWN) is None:
        # No portal to ask through; report the tool as absent, which is the
        # honest answer and the one every caller already handles.
        return None
    try:
        result = subprocess.run(
            [SPAWN, "--host", "which", name],
            capture_output=True, text=True, timeout=_PROBE_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as error:
        log.debug("Could not ask the host for %s: %s", name, error)
        return None
    if result.returncode != 0:
        return None
    return (result.stdout or "").strip() or None


def forget() -> None:
    """Drop the cached answers. Only tests need this."""
    in_flatpak.cache_clear()
    _host_which.cache_clear()
