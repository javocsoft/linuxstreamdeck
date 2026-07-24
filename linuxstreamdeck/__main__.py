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

    return LinuxStreamDeckApp().run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
