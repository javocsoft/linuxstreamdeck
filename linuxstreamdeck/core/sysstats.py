"""Whole-machine measurements, read straight from the kernel.

OBS reports its *own* process usage, which is the number its Stats window
shows. That is not what a system monitor shows and not what someone asking
"how loaded is my machine" means, so the two are kept as separate readings
rather than one being passed off as the other.

Linux only, which this application already is. Anything unreadable answers
None, and the key showing it falls back to a dash.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

PROC_STAT = Path("/proc/stat")
# Minimum gap between readings. CPU use only exists as a difference between two
# samples, and sampling twice in quick succession measures noise rather than
# load, so a caller asking sooner is given the last answer.
MIN_INTERVAL = 0.9

_lock = threading.Lock()
_previous: tuple[int, int] | None = None      # (total jiffies, idle jiffies)
_value: float | None = None
_sampled_at = 0.0


def _read_totals() -> tuple[int, int] | None:
    """Total and idle jiffies since boot, from the aggregate `cpu` line."""
    try:
        with PROC_STAT.open(encoding="ascii") as handle:
            fields = handle.readline().split()
    except OSError:
        return None
    if len(fields) < 6 or fields[0] != "cpu":
        return None
    try:
        values = [int(field) for field in fields[1:]]
    except ValueError:
        return None
    # idle + iowait: a core waiting on disk is not doing work either.
    return sum(values), values[3] + values[4]


def cpu_percent() -> float | None:
    """Machine-wide CPU use since the previous reading, 0-100.

    None until there are two samples to compare, which is the first call after
    start-up: a single reading is use since boot, not use now.
    """
    global _previous, _value, _sampled_at

    now = time.monotonic()
    with _lock:
        if _value is not None and now - _sampled_at < MIN_INTERVAL:
            return _value
        totals = _read_totals()
        if totals is None:
            return _value
        previous, _previous, _sampled_at = _previous, totals, now
        if previous is None:
            return _value
        elapsed = totals[0] - previous[0]
        idle = totals[1] - previous[1]
        if elapsed <= 0:
            return _value
        _value = max(0.0, min(100.0, (1.0 - idle / elapsed) * 100.0))
        return _value


def reset() -> None:
    """Forget the previous sample. For tests, and for a fresh first reading."""
    global _previous, _value, _sampled_at
    with _lock:
        _previous, _value, _sampled_at = None, None, 0.0
