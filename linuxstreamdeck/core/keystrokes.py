"""Sending keyboard shortcuts to whichever application has focus.

Wayland deliberately blocks synthetic input, so this needs a helper that injects
events below the compositor. `ydotool` is preferred because it writes to the
kernel's uinput device and therefore works on every compositor and on X11;
`wtype` (wlroots) and `xdotool` (X11) are used when they are what is installed.

None of them is a hard dependency: when no backend is present the action reports
what to install and changes nothing, so the rest of the application is
unaffected.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from functools import lru_cache

log = logging.getLogger(__name__)

INSTALL_HINT = (
    "No key injection tool found. Install ydotool to send keyboard shortcuts"
)

# Modifier aliases accepted in a shortcut string, normalized to these names.
MODIFIERS = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "shift": "shift",
    "alt": "alt",
    "altgr": "altgr",
    "super": "super",
    "meta": "super",
    "win": "super",
    "cmd": "super",
}

# A key is held canonically as its Linux input name (the KEY_* constant without
# the prefix), which is what ydotool speaks. Each backend renders that canonical
# name in its own dialect.
_MODIFIER_CODES = {
    "ctrl": 29,      # KEY_LEFTCTRL
    "shift": 42,     # KEY_LEFTSHIFT
    "alt": 56,       # KEY_LEFTALT
    "altgr": 100,    # KEY_RIGHTALT
    "super": 125,    # KEY_LEFTMETA
}
# ydotool 0.x spells its modifiers like this; it has no "altgr" alias.
_MODIFIER_NAMES = {
    "ctrl": "ctrl",
    "shift": "shift",
    "alt": "alt",
    "altgr": "rightalt",
    "super": "super",
}
_KEY_CODES = {
    "esc": 1,
    "1": 2, "2": 3, "3": 4, "4": 5, "5": 6,
    "6": 7, "7": 8, "8": 9, "9": 10, "0": 11,
    "minus": 12, "equal": 13, "backspace": 14, "tab": 15,
    "q": 16, "w": 17, "e": 18, "r": 19, "t": 20, "y": 21, "u": 22,
    "i": 23, "o": 24, "p": 25,
    "leftbrace": 26, "rightbrace": 27, "enter": 28,
    "a": 30, "s": 31, "d": 32, "f": 33, "g": 34, "h": 35, "j": 36,
    "k": 37, "l": 38,
    "semicolon": 39, "apostrophe": 40, "grave": 41, "backslash": 43,
    "z": 44, "x": 45, "c": 46, "v": 47, "b": 48, "n": 49, "m": 50,
    "comma": 51, "dot": 52, "slash": 53,
    "space": 57, "capslock": 58,
    "f1": 59, "f2": 60, "f3": 61, "f4": 62, "f5": 63, "f6": 64,
    "f7": 65, "f8": 66, "f9": 67, "f10": 68, "f11": 87, "f12": 88,
    "sysrq": 99,
    "home": 102, "up": 103, "pageup": 104, "left": 105, "right": 106,
    "end": 107, "down": 108, "pagedown": 109, "insert": 110, "delete": 111,
    "menu": 127,
}
# What someone may reasonably type, mapped onto the canonical name above.
_KEY_ALIASES = {
    "escape": "esc",
    "return": "enter",
    "period": "dot", ".": "dot",
    "print": "sysrq", "printscreen": "sysrq",
    "bracketleft": "leftbrace", "[": "leftbrace",
    "bracketright": "rightbrace", "]": "rightbrace",
    "-": "minus", "=": "equal", ",": "comma", "/": "slash",
    ";": "semicolon", "'": "apostrophe", "`": "grave", "\\": "backslash",
    "pgup": "pageup", "pgdn": "pagedown", "pagedn": "pagedown",
    "del": "delete", "ins": "insert",
}

# X keysym names for xdotool / wtype, where the spelling differs from ours.
_XKB_NAMES = {
    "ctrl": "ctrl", "shift": "shift", "alt": "alt", "altgr": "ISO_Level3_Shift",
    "super": "super",
    "esc": "Escape", "enter": "Return",
    "tab": "Tab", "space": "space", "backspace": "BackSpace",
    "delete": "Delete", "insert": "Insert", "home": "Home", "end": "End",
    "pageup": "Prior", "pagedown": "Next",
    "left": "Left", "right": "Right", "up": "Up", "down": "Down",
    "sysrq": "Print",
    "minus": "minus", "equal": "equal", "comma": "comma", "dot": "period",
    "slash": "slash", "semicolon": "semicolon", "apostrophe": "apostrophe",
    "grave": "grave", "backslash": "backslash",
    "leftbrace": "bracketleft", "rightbrace": "bracketright",
}

# Preconfigured shortcuts, adapted to Linux desktops. Windows-only entries of
# the original catalogue (Game Bar, Task Manager) are either dropped or mapped
# to the closest standard Linux binding. Every one of them stays editable in the
# editor, because desktops do rebind these.
SHORTCUT_PRESETS = (
    ("", ""),
    ("Cut", "ctrl+x"),
    ("Copy", "ctrl+c"),
    ("Paste", "ctrl+v"),
    ("Paste without formatting", "ctrl+shift+v"),
    ("Undo", "ctrl+z"),
    ("Redo", "ctrl+shift+z"),
    ("Select all", "ctrl+a"),
    ("Save", "ctrl+s"),
    ("Find", "ctrl+f"),
    ("Print", "ctrl+p"),
    ("Emoji picker", "ctrl+period"),
    # General
    ("Open file manager", "super+e"),
    ("Open run dialog", "alt+f2"),
    ("Lock screen", "super+l"),
    ("Open terminal", "ctrl+alt+t"),
    # Window management
    ("Snap window left", "super+left"),
    ("Snap window right", "super+right"),
    ("Maximize window", "super+up"),
    ("Minimize window", "super+h"),
    ("Close window", "alt+f4"),
    ("Show desktop", "super+d"),
    ("Switch application", "alt+tab"),
    # Screenshots
    ("Screenshot to file", "print"),
    ("Screenshot to clipboard", "ctrl+print"),
    ("Screenshot of active window", "alt+print"),
    ("Screenshot of an area", "shift+print"),
)
PRESET_LABELS = tuple(label for label, _shortcut in SHORTCUT_PRESETS)
PRESET_SHORTCUTS = dict(SHORTCUT_PRESETS)


def parse(shortcut: str) -> tuple[list[str], str]:
    """Split "ctrl+shift+s" into its modifiers and its key.

    Raises ValueError with a user-facing message when it cannot be sent.
    """
    text = str(shortcut or "").strip()
    if not text:
        raise ValueError("Enter a keyboard shortcut")
    parts = [part.strip().lower() for part in text.split("+") if part.strip()]
    if not parts:
        raise ValueError("Enter a keyboard shortcut")
    modifiers: list[str] = []
    key = ""
    for part in parts:
        if part in MODIFIERS:
            modifier = MODIFIERS[part]
            if modifier not in modifiers:
                modifiers.append(modifier)
        elif key:
            raise ValueError(f"Only one key is allowed: {shortcut}")
        else:
            key = _KEY_ALIASES.get(part, part)
    if not key:
        raise ValueError(f"{shortcut} has modifiers but no key")
    if key not in _KEY_CODES:
        raise ValueError(f"Unsupported key: {key}")
    return modifiers, key


def backend() -> str:
    """Name of the injection tool available, or an empty string."""
    for tool in ("ydotool", "wtype", "xdotool"):
        if shutil.which(tool):
            return tool
    return ""


def is_available() -> bool:
    return bool(backend())


@lru_cache(maxsize=4)
def ydotool_syntax(executable: str) -> str:
    """Which argument style this ydotool build takes: "names" or "codes".

    The two major versions are incompatible: 0.x takes "ctrl+c" while 1.x takes
    press/release key codes. Debian and Ubuntu still ship 0.x, so the style is
    detected from the help text rather than assumed.
    """
    try:
        result = subprocess.run(
            [executable, "key", "--help"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "codes"
    help_text = f"{result.stdout}{result.stderr}".lower()
    if "key sequence" in help_text:
        return "names"
    if "keycode" in help_text:
        return "codes"
    return "codes"


def _ydotool_command(modifiers: list[str], key: str) -> list[str]:
    executable = shutil.which("ydotool") or "ydotool"
    if ydotool_syntax(executable) == "names":
        sequence = "+".join([_MODIFIER_NAMES[m] for m in modifiers] + [key])
        return ["ydotool", "key", sequence]
    # 1.x: press the modifiers and the key, then release in reverse order.
    codes = [_MODIFIER_CODES[m] for m in modifiers] + [_KEY_CODES[key]]
    pressed = [f"{code}:1" for code in codes]
    released = [f"{code}:0" for code in reversed(codes)]
    return ["ydotool", "key", *pressed, *released]


def _xdotool_command(modifiers: list[str], key: str) -> list[str]:
    names = [_XKB_NAMES.get(m, m) for m in modifiers]
    names.append(_XKB_NAMES.get(key, key))
    return ["xdotool", "key", "+".join(names)]


def _wtype_command(modifiers: list[str], key: str) -> list[str]:
    command = ["wtype"]
    for modifier in modifiers:
        command += ["-M", _XKB_NAMES.get(modifier, modifier)]
    command += ["-k", _XKB_NAMES.get(key, key)]
    for modifier in reversed(modifiers):
        command += ["-m", _XKB_NAMES.get(modifier, modifier)]
    return command


def command_for(shortcut: str, tool: str = "") -> list[str]:
    """Build the command that sends this shortcut with the given backend."""
    tool = tool or backend()
    if not tool:
        raise ValueError(INSTALL_HINT)
    modifiers, key = parse(shortcut)
    builders = {
        "ydotool": _ydotool_command,
        "xdotool": _xdotool_command,
        "wtype": _wtype_command,
    }
    builder = builders.get(tool)
    if builder is None:
        raise ValueError(f"Unsupported key injection tool: {tool}")
    return builder(modifiers, key)


def send(shortcut: str) -> None:
    """Send one shortcut to the focused application."""
    command = command_for(shortcut)
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=5)
    except FileNotFoundError as error:
        raise ValueError(INSTALL_HINT) from error
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError(f"Could not send {shortcut}: {error}") from error
    if result.returncode != 0:
        message = (result.stderr or "").strip()
        if "uinput" in message.lower() or "permission" in message.lower():
            raise ValueError(
                "No permission to inject keys. Check access to /dev/uinput "
                "or start the ydotoold service"
            )
        raise ValueError(message or f"Could not send {shortcut}")
