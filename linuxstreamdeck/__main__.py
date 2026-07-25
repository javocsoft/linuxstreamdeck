"""Entry point: `linuxstreamdeck` or `python -m linuxstreamdeck`."""

from __future__ import annotations

import logging
import os
import sys


def main() -> int:
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
