"""Entry point: `linuxstreamdeck` or `python -m linuxstreamdeck`."""

from __future__ import annotations

import logging
import os
import sys


def version_requested(argv: list[str]) -> bool:
    """Whether the command line only asks what version this is."""
    return any(arg in ("--version", "-V") for arg in argv[1:])


def main() -> int:
    # Answered before GTK sees argv: the bug report template asks people to run
    # this, and Adw.Application rejects any option it does not know.
    if version_requested(sys.argv):
        from . import APP_NAME, VERSION

        print(f"{APP_NAME} {VERSION}")
        return 0

    level = logging.DEBUG if os.environ.get("LSD_DEBUG") else logging.INFO
    logging.basicConfig(
        level=level, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    )
    # silence obsws reconnection noise
    logging.getLogger("obsws_python").setLevel(logging.WARNING)

    from .app import LinuxStreamDeckApp
    from .core.autostart import strip_hidden_flag

    # The autostart entry adds a flag so a session login goes straight to the
    # status icon, without opening the window.
    argv, start_hidden = strip_hidden_flag(sys.argv)

    return LinuxStreamDeckApp(start_hidden=start_hidden).run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
