"""Entry point: `linuxstreamdeck` or `python -m linuxstreamdeck`."""

from __future__ import annotations

import logging
import os
import sys


LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def version_requested(argv: list[str]) -> bool:
    """Whether the command line only asks what version this is."""
    return any(arg in ("--version", "-V") for arg in argv[1:])


def _add_file_logging(level: int) -> None:
    """Also write the log to a small rotated file in the config directory.

    Until this existed the log went to stderr and nowhere else, which for the
    normal case — started by the session at login — means it went nowhere at
    all. An action that failed then left no trace the user could ever reach,
    and a bug report had nothing to attach.

    It must never stop the application from starting: a read-only or full home
    directory is a reason to lose the log, not to lose the deck.
    """
    from logging.handlers import RotatingFileHandler

    from .core.config import CONFIG_DIR, LOG_FILE, LOG_KEEP, LOG_MAX_BYTES

    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            LOG_FILE,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_KEEP,
            encoding="utf-8",
        )
    except OSError:
        logging.getLogger(__name__).warning(
            "Could not open the log file at %s", LOG_FILE, exc_info=True
        )
        return
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logging.getLogger().addHandler(handler)


def main() -> int:
    # Answered before GTK sees argv: the bug report template asks people to run
    # this, and Adw.Application rejects any option it does not know.
    if version_requested(sys.argv):
        from . import APP_NAME, VERSION

        print(f"{APP_NAME} {VERSION}")
        return 0

    level = logging.DEBUG if os.environ.get("LSD_DEBUG") else logging.INFO
    logging.basicConfig(
        level=level, format=LOG_FORMAT
    )
    _add_file_logging(level)
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
