"""Launching, focusing and closing desktop applications.

The application list comes from `Gio.AppInfo`, which already reads the XDG
desktop entries the session exposes, so nothing extra has to be installed. An
entry is identified by its desktop file id (`org.gnome.Calculator.desktop`),
which stays stable across reboots and is what the key configuration stores.
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess

from gi.repository import Gio, GLib

log = logging.getLogger(__name__)

# How long a polite terminate is given before a forced close may follow.
TERMINATE_GRACE_SECONDS = 4.0


def available_applications() -> list[tuple[str, str]]:
    """(desktop id, display name) of every application the session shows."""
    applications = []
    for info in Gio.AppInfo.get_all():
        if not info.should_show():
            continue
        identifier = info.get_id()
        name = info.get_display_name() or info.get_name() or identifier
        if identifier:
            applications.append((identifier, name))
    applications.sort(key=lambda item: item[1].casefold())
    return applications


def application_choices() -> list[str]:
    """Display names for the editor dropdown, which is what gets stored."""
    return [name for _identifier, name in available_applications()]


def find_application(reference: str) -> Gio.AppInfo | None:
    """Resolve a stored value, accepting a display name or a desktop id."""
    reference = str(reference or "").strip()
    if not reference:
        return None
    for info in Gio.AppInfo.get_all():
        if info.get_id() == reference:
            return info
    folded = reference.casefold()
    for info in Gio.AppInfo.get_all():
        name = info.get_display_name() or info.get_name() or ""
        if name.casefold() == folded:
            return info
    return None


def launch(reference: str) -> None:
    """Start an application. Raises ValueError when it cannot be resolved."""
    info = find_application(reference)
    if info is None:
        raise ValueError(f"Application not found: {reference}")
    context = Gio.AppLaunchContext()
    try:
        info.launch([], context)
    except GLib.Error as error:
        raise ValueError(f"Could not start {reference}: {error.message}") from error


def open_target(target: str) -> None:
    """Open a file, folder, URI or executable with the desktop's own handler."""
    target = str(target or "").strip()
    if not target:
        raise ValueError("Choose something to open")
    if "://" in target:
        uri = target
    else:
        path = os.path.expanduser(target)
        if os.path.isfile(path) and os.access(path, os.X_OK):
            # A plain executable is run directly; letting the desktop "open" it
            # would offer to edit it instead.
            subprocess.Popen([path], start_new_session=True)
            return
        if not os.path.exists(path):
            if (info := find_application(target)) is not None:
                launch(target)
                return
            raise ValueError(f"Not found: {target}")
        uri = GLib.filename_to_uri(os.path.abspath(path), None)
    if not Gio.AppInfo.launch_default_for_uri(uri, Gio.AppLaunchContext()):
        raise ValueError(f"Nothing can open {target}")


def _executable_names(info: Gio.AppInfo) -> set[str]:
    """Plausible process names for an application, used to find it running."""
    names: set[str] = set()
    executable = info.get_executable() or ""
    if executable:
        names.add(os.path.basename(executable))
    command = info.get_commandline() or ""
    for token in command.split():
        if token.startswith("%") or token.startswith("-"):
            continue
        candidate = os.path.basename(token)
        if candidate and not candidate.endswith("="):
            names.add(candidate)
            break
    return {name for name in names if name not in ("env", "sh", "bash", "flatpak")}


def running_pids(reference: str) -> list[int]:
    """PIDs that look like the given application, newest last."""
    info = find_application(reference)
    if info is None:
        return []
    names = _executable_names(info)
    if not names:
        return []
    pids: list[int] = []
    pgrep = shutil.which("pgrep")
    if pgrep is None:
        return []
    for name in names:
        try:
            result = subprocess.run(
                [pgrep, "-u", str(os.getuid()), "-x", name],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        for line in result.stdout.split():
            try:
                pid = int(line)
            except ValueError:
                continue
            if pid != os.getpid():
                pids.append(pid)
    return sorted(set(pids))


def is_running(reference: str) -> bool:
    return bool(running_pids(reference))


def close(reference: str, force: bool = False) -> int:
    """Close an application. Returns how many processes were signalled."""
    pids = running_pids(reference)
    if not pids:
        raise ValueError(f"{reference} is not running")
    number = signal.SIGKILL if force else signal.SIGTERM
    signalled = 0
    for pid in pids:
        try:
            os.kill(pid, number)
            signalled += 1
        except ProcessLookupError:
            continue
        except PermissionError:
            log.warning("Not allowed to signal process %s", pid)
    if not signalled:
        raise ValueError(f"Could not close {reference}")
    return signalled
