"""A live machine measurement on a key.

`obs.stats` already shows OBS's own numbers, plus two that come from the kernel
because they are worth having with OBS closed. This is the rest of that second
group and it lives in **System** rather than under OBS, because a key showing
GPU temperature has nothing to do with OBS and should not be found by looking
there.

The two overlapping metrics stay in `obs.stats` as well. Moving them would
silently change what every key already configured with them shows, and a
duplicate entry in a dropdown costs nothing next to that.

Every reader answers None when the kernel does not report it -- an integrated
graphics chip has no busy counter, a desktop board may have no package sensor
-- and the key then shows a dash rather than a zero. A zero is a claim.
"""

from __future__ import annotations

import logging

from .core import sysstats
from .core.actions import Action, Param, apply_default_icons, register

log = logging.getLogger(__name__)

CAT_SYSTEM = "System"

NO_VALUE = "--"
OK_COLOR = "#1e3a24"
WARN_COLOR = "#5a4410"
ALARM_COLOR = "#5c1622"


def _percent_text(value: float) -> str:
    """A percentage with the precision it deserves.

    Whole numbers throw away most of the information exactly where these
    values live: 1.4 % and 0.6 % both print "1%".
    """
    return f"{value:.1f}%" if value < 10 else f"{value:.0f}%"


def _size_text(megabytes: float) -> str:
    if megabytes >= 1024:
        return f"{megabytes / 1024:.1f}G"
    return f"{megabytes:.0f}M"


def _rate_text(bytes_per_second: float) -> str:
    """Throughput, in the unit somebody would say out loud.

    Bits rather than bytes: a connection is sold in megabits, so a key that
    reads 12M against a 100 Mb line invites the wrong conclusion entirely.
    """
    bits = bytes_per_second * 8
    if bits >= 1_000_000_000:
        return f"{bits / 1_000_000_000:.1f}Gb"
    if bits >= 1_000_000:
        return f"{bits / 1_000_000:.1f}Mb"
    return f"{bits / 1000:.0f}kb"


def _memory_used():
    reading = sysstats.memory()
    return None if reading is None else reading[0]


def _gpu_field(name):
    def read(_params):
        value = sysstats.gpu().get(name)
        return None if value is None else float(value)

    return read


def _network(index: int):
    def read(params):
        rate = sysstats.network_rate(str((params or {}).get("interface") or ""))
        return None if rate is None else rate[index]

    return read


# Each metric: how it is labelled, how a sample becomes text, and optional
# warning tests. One table, so adding a measurement is one entry rather than a
# branch in three methods -- the same shape `obs.stats` uses.
METRICS: dict[str, dict] = {
    "cpu": {
        "label": "CPU usage",
        "icon": "mdi:cpu-64-bit",
        "read": lambda _p: sysstats.cpu_percent(),
        "text": _percent_text,
        "warn": lambda v: v >= 80.0,
        "alarm": lambda v: v >= 95.0,
    },
    "cpu_temp": {
        "label": "CPU temperature",
        "icon": "mdi:thermometer",
        "read": lambda _p: sysstats.cpu_temperature(),
        "text": lambda v: f"{v:.0f}°",
        "warn": lambda v: v >= 80.0,
        "alarm": lambda v: v >= 90.0,
    },
    "memory": {
        "label": "Memory usage",
        "icon": "mdi:memory",
        "read": lambda _p: sysstats.memory_percent(),
        "text": _percent_text,
        "warn": lambda v: v >= 85.0,
        "alarm": lambda v: v >= 95.0,
    },
    "memory_used": {
        "label": "Memory used",
        "icon": "mdi:memory",
        "read": lambda _p: _memory_used(),
        "text": _size_text,
    },
    "gpu": {
        "label": "GPU usage",
        "icon": "mdi:expansion-card",
        "read": _gpu_field("percent"),
        "text": _percent_text,
        "warn": lambda v: v >= 90.0,
        "alarm": lambda v: v >= 98.0,
    },
    "gpu_memory": {
        "label": "GPU memory",
        "icon": "mdi:expansion-card-variant",
        "read": _gpu_field("memory_percent"),
        "text": _percent_text,
        "warn": lambda v: v >= 85.0,
        "alarm": lambda v: v >= 95.0,
    },
    "gpu_temp": {
        "label": "GPU temperature",
        "icon": "mdi:thermometer-high",
        "read": _gpu_field("temperature"),
        "text": lambda v: f"{v:.0f}°",
        "warn": lambda v: v >= 80.0,
        "alarm": lambda v: v >= 90.0,
    },
    "net_down": {
        "label": "Network download",
        "icon": "mdi:download-network",
        "read": _network(0),
        "text": _rate_text,
    },
    "net_up": {
        "label": "Network upload",
        "icon": "mdi:upload-network",
        "read": _network(1),
        "text": _rate_text,
    },
    "disk": {
        "label": "Free disk space",
        "icon": "mdi:harddisk",
        "read": lambda p: sysstats.disk_free_mb((p or {}).get("disk_folder")),
        "text": _size_text,
        # Low is bad here, unlike everything else in this table.
        "warn": lambda v: v < 20 * 1024,
        "alarm": lambda v: v < 5 * 1024,
    },
}

# Metrics whose row needs the folder field, and the ones that need the
# interface field. Named here so the editor hides the rest.
DISK_METRICS = ("disk",)
NETWORK_METRICS = ("net_down", "net_up")


@register
class SystemStats(Action):
    id = "sys.stats"
    name = "System monitor"
    category = CAT_SYSTEM
    description = (
        "Show a live machine measurement on the key: CPU, memory, GPU, "
        "temperatures, network throughput or free disk space. None of it "
        "needs OBS."
    )
    params = [
        Param(
            "metric",
            "Measurement",
            kind="choice",
            default="cpu",
            choices=list(METRICS),
            # The stored value stays an identifier, so rewording a label
            # cannot invalidate a saved key.
            choice_labels={
                key: metric["label"] for key, metric in METRICS.items()
            },
        ),
        Param(
            "interface",
            "Network interface",
            choices_source="network_interfaces",
            placeholder="Every connection added together",
            depends_on="metric",
            depends_values=list(NETWORK_METRICS),
        ),
        Param(
            "disk_folder",
            "Disk folder",
            kind="file",
            default="",
            directory=True,
            placeholder="Home folder, unless you pick another drive",
            depends_on="metric",
            depends_values=list(DISK_METRICS),
        ),
        Param(
            "colored",
            "Warn with color",
            kind="choice",
            default="yes",
            choices=["yes", "no"],
            choice_labels={"yes": "Yes", "no": "No"},
        ),
    ]

    def execute(self, ctx, p):
        """The key exists to be read, not pressed.

        A key that does nothing at all feels broken, so a press states the
        measurement in words -- which is also the only place there is room to
        say that something is not available.
        """
        metric = METRICS.get(p.get("metric") or "cpu")
        if metric is None:
            return
        value = self._text(metric, metric["read"](p or {}))
        ctx.bus.emit(
            "status",
            text=(
                f"{metric['label']}: {value}"
                if value != NO_VALUE
                else f"{metric['label']} is not available on this machine"
            ),
        )

    def feedback(self, ctx, p):
        metric = METRICS.get(p.get("metric") or "cpu")
        if metric is None:
            return {}
        raw = metric["read"](p or {})
        state = {"display": self._text(metric, raw)}
        if raw is not None and str(p.get("colored", "yes")) != "no":
            color = self._color(metric, raw)
            if color:
                state["color"] = color
        return state

    @staticmethod
    def _text(metric: dict, raw) -> str:
        if raw is None:
            return NO_VALUE
        try:
            return metric["text"](float(raw))
        except (TypeError, ValueError):
            return NO_VALUE

    @staticmethod
    def _color(metric: dict, raw) -> str:
        warn, alarm = metric.get("warn"), metric.get("alarm")
        if warn is None:
            # No threshold means no opinion, and a key with no opinion must
            # not be painted green: that reads as "checked, and fine".
            return ""
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return ""
        if alarm is not None and alarm(value):
            return ALARM_COLOR
        return WARN_COLOR if warn(value) else OK_COLOR


apply_default_icons({
    SystemStats.id: METRICS["cpu"]["icon"],
})
