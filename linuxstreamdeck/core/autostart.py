"""Start LinuxStreamDeck automatically when the desktop session begins.

This writes a normal XDG autostart entry to `~/.config/autostart`, which every
mainstream Linux desktop reads. The state lives in that file, not in
`config.json`: a configuration exported on one computer must never silently
enable autostart on another, and the user may also remove the entry with their
desktop's own startup-applications tool.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path

from .. import APP_ID, APP_NAME

log = logging.getLogger(__name__)

# The autostart directory follows the XDG base directory specification and is
# independent of LSD_CONFIG_DIR, except in tests (see AUTOSTART_DIR below).
AUTOSTART_DIR = Path(
    os.environ.get(
        "LSD_AUTOSTART_DIR",
        Path(
            os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
        )
        / "autostart",
    )
)
AUTOSTART_FILE = AUTOSTART_DIR / f"{APP_ID}.desktop"

# Passed by the autostart entry so a session login does not pop the window open;
# the application then starts straight into its status icon.
HIDDEN_FLAG = "--hidden"


def strip_hidden_flag(argv: list[str]) -> tuple[list[str], bool]:
    """Split the hidden-start flag out of argv.

    GTK parses whatever argv it is given, so the flag is removed before the
    application sees it.
    """
    remaining = [argument for argument in argv if argument != HIDDEN_FLAG]
    return remaining, len(remaining) != len(argv)


def launch_command() -> str:
    """Command the desktop should run at login.

    Prefers the installed console script and falls back to the interpreter that
    is running now, so an autostart entry written from a source checkout still
    starts the same code.
    """
    executable = shutil.which("linuxstreamdeck")
    if executable:
        return f"{executable} {HIDDEN_FLAG}"
    return f"{sys.executable} -m linuxstreamdeck {HIDDEN_FLAG}"


def desktop_entry() -> str:
    return "\n".join(
        (
            "[Desktop Entry]",
            "Type=Application",
            f"Name={APP_NAME}",
            "Comment=Control your Elgato Stream Deck with deep OBS Studio "
            "integration",
            f"Exec={launch_command()}",
            f"Icon={APP_ID}",
            "Terminal=false",
            "Categories=AudioVideo;GTK;",
            f"StartupWMClass={APP_ID}",
            "X-GNOME-Autostart-enabled=true",
            "",
        )
    )


def is_enabled() -> bool:
    """Whether an enabled autostart entry exists for this application."""
    try:
        if not AUTOSTART_FILE.is_file():
            return False
        content = AUTOSTART_FILE.read_text(encoding="utf-8")
    except OSError:
        log.debug("Could not read the autostart entry", exc_info=True)
        return False
    # Desktop environments disable an entry by flagging it rather than deleting
    # it, so honour both markers instead of trusting the file's presence alone.
    for line in content.splitlines():
        key, _, value = line.partition("=")
        key, value = key.strip().lower(), value.strip().lower()
        if key == "hidden" and value == "true":
            return False
        if key == "x-gnome-autostart-enabled" and value == "false":
            return False
    return True


def set_enabled(enabled: bool) -> None:
    """Create or remove the autostart entry. Raises OSError on failure."""
    if enabled:
        _write_entry()
    else:
        AUTOSTART_FILE.unlink(missing_ok=True)


def _write_entry() -> None:
    AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
    payload = desktop_entry().encode("utf-8")
    temporary = tempfile.NamedTemporaryFile(
        prefix=f".{AUTOSTART_FILE.name}.",
        suffix=".tmp",
        dir=AUTOSTART_DIR,
        delete=False,
    )
    temporary_path = Path(temporary.name)
    try:
        temporary.write(payload)
        temporary.close()
        os.chmod(temporary_path, 0o644)
        temporary_path.replace(AUTOSTART_FILE)
    finally:
        temporary.close()
        temporary_path.unlink(missing_ok=True)
