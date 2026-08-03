"""Whole-machine measurements, read straight from the kernel.

OBS reports its *own* process usage, which is the number its Stats window
shows. That is not what a system monitor shows and not what someone asking
"how loaded is my machine" means, so the two are kept as separate readings
rather than one being passed off as the other.

Free disk space is here for a different reason. OBS does report it, but only
while OBS is running, and "is there room to record" is precisely the question
someone asks *before* opening it. The filesystem knows the answer either way.

Linux only, which this application already is. Anything unreadable answers
None, and the key showing it falls back to a dash.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

PROC_STAT = Path("/proc/stat")
# Minimum gap between readings. CPU use only exists as a difference between two
# samples, and sampling twice in quick succession measures noise rather than
# load, so a caller asking sooner is given the last answer.
MIN_INTERVAL = 0.9
# Free space changes slowly compared to how often a key repaints, and a page can
# hold several keys watching the same drive, so a reading is shared for a moment.
DISK_INTERVAL = 2.0
# Bound on how many folders are remembered at once. Each comes from a configured
# key, so this is generous; it only exists so a hand-edited configuration full of
# distinct paths cannot grow the cache without end.
DISK_CACHE_LIMIT = 16

_lock = threading.Lock()
_previous: tuple[int, int] | None = None      # (total jiffies, idle jiffies)
_value: float | None = None
_sampled_at = 0.0

_disk_lock = threading.Lock()
_disk: dict[str, tuple[float, float | None]] = {}   # folder -> (when, free MB)


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


def disk_folder(path=None) -> str:
    """The folder a free-space reading measures.

    Blank means the home folder, which is where a desktop records by default.
    It is a deliberate, fixed answer rather than "whatever OBS is set to": a
    key that reads the home drive with OBS closed and the recording drive with
    it open would silently be showing two different numbers.
    """
    text = str(path or "").strip()
    if text:
        try:
            return str(Path(text).expanduser())
        except RuntimeError:
            return text
    try:
        return str(Path.home())
    except RuntimeError:              # no home in this environment
        return ""


def disk_free_mb(path=None) -> float | None:
    """Free megabytes on the filesystem holding `path`, or None.

    A folder that does not exist answers None instead of being walked up to its
    nearest existing parent. An unmounted recording drive would otherwise report
    the root filesystem's free space, and on a key whose whole job is to warn
    you, a confidently wrong number is worse than a dash.
    """
    target = disk_folder(path)
    if not target:
        return None
    now = time.monotonic()
    with _disk_lock:
        cached = _disk.get(target)
        if cached is not None and now - cached[0] < DISK_INTERVAL:
            return cached[1]
    try:
        free = shutil.disk_usage(target).free / (1024 * 1024)
    except OSError:
        free = None
    with _disk_lock:
        if len(_disk) >= DISK_CACHE_LIMIT and target not in _disk:
            _disk.clear()
        _disk[target] = (now, free)
    return free


# ---------- memory ----------

PROC_MEMINFO = Path("/proc/meminfo")


def _meminfo() -> dict[str, int]:
    """`/proc/meminfo` as kilobytes, or an empty mapping."""
    values: dict[str, int] = {}
    try:
        with PROC_MEMINFO.open(encoding="ascii") as handle:
            for line in handle:
                name, _, rest = line.partition(":")
                parts = rest.split()
                if parts:
                    try:
                        values[name.strip()] = int(parts[0])
                    except ValueError:
                        continue
    except OSError:
        return {}
    return values


def memory() -> tuple[float, float] | None:
    """`(used MB, total MB)`, or None.

    Used is derived from **MemAvailable**, not from MemFree. Free memory on a
    healthy Linux box is nearly zero because the kernel spends it on cache,
    so a key built on MemFree reads 98 % on a machine doing nothing at all and
    is worse than no key.
    """
    values = _meminfo()
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    if not total:
        return None
    if available is None:
        # Very old kernels have no MemAvailable. This is the approximation the
        # kernel documents for them.
        available = (
            values.get("MemFree", 0)
            + values.get("Cached", 0)
            + values.get("Buffers", 0)
        )
    used = max(0, total - available)
    return used / 1024.0, total / 1024.0


def memory_percent() -> float | None:
    reading = memory()
    if reading is None or reading[1] <= 0:
        return None
    return max(0.0, min(100.0, reading[0] / reading[1] * 100.0))


# ---------- network throughput ----------

PROC_NET_DEV = Path("/proc/net/dev")
# Interfaces that are never what somebody means by "my connection".
SKIP_INTERFACES = ("lo",)
SKIP_PREFIXES = ("docker", "veth", "br-", "virbr", "tun", "tap")

_net_lock = threading.Lock()
_net_previous: dict[str, tuple[float, int, int]] = {}
_net_value: dict[str, tuple[float, float]] = {}


def _read_interfaces() -> dict[str, tuple[int, int]]:
    """`interface -> (bytes received, bytes sent)` since boot."""
    found: dict[str, tuple[int, int]] = {}
    try:
        lines = PROC_NET_DEV.read_text(encoding="ascii").splitlines()
    except OSError:
        return {}
    for line in lines[2:]:
        name, _, rest = line.partition(":")
        fields = rest.split()
        if len(fields) < 9:
            continue
        try:
            found[name.strip()] = (int(fields[0]), int(fields[8]))
        except ValueError:
            continue
    return found


def network_interfaces() -> list[str]:
    """Interfaces worth offering, busiest first.

    Sorted by traffic so the one somebody actually uses is at the top: a
    laptop lists half a dozen, and the alphabetical first is rarely it.
    """
    counters = _read_interfaces()
    usable = [
        (name, rx + tx) for name, (rx, tx) in counters.items()
        if name not in SKIP_INTERFACES
        and not name.startswith(SKIP_PREFIXES)
    ]
    usable.sort(key=lambda item: (-item[1], item[0]))
    return [name for name, _total in usable]


SYS_NET = Path("/sys/class/net")
# What each naming scheme means, when the kernel offers nothing better. The
# predictable-names prefixes: `wl` wireless, `en` ethernet, `ww` mobile.
NAME_PREFIXES = (
    ("wl", "Wi-Fi"),
    ("ww", "Mobile broadband"),
    ("en", "Ethernet"),
    ("eth", "Ethernet"),
    ("wlan", "Wi-Fi"),
)


def _sysfs(interface: str, *parts: str) -> str:
    try:
        return (SYS_NET.joinpath(interface, *parts)).read_text().strip()
    except OSError:
        return ""


def _hardware_name(interface: str) -> str:
    """What the device says it is, for one that publishes it.

    A USB adapter carries a real product string -- "Realtek USB 10/100 LAN" --
    and that is by far the most useful label, because `enx00e04c3676eb` is a
    name derived from the MAC address and tells nobody anything. A PCI card
    publishes only numeric vendor and device ids, which would need a hardware
    database to read, so those fall back to what kind of link it is.
    """
    product = _sysfs(interface, "device", "..", "product")
    if not product:
        return ""
    maker = _sysfs(interface, "device", "..", "manufacturer")
    if maker and not product.casefold().startswith(maker.casefold()):
        return f"{maker} {product}"
    return product


def _link_kind(interface: str) -> str:
    """Wi-Fi, Ethernet or mobile, from the kernel rather than from the name."""
    if (SYS_NET / interface / "wireless").is_dir():
        return "Wi-Fi"
    devtype = ""
    for line in _sysfs(interface, "uevent").splitlines():
        if line.startswith("DEVTYPE="):
            devtype = line.partition("=")[2].strip().casefold()
    if devtype == "wlan":
        return "Wi-Fi"
    if devtype == "wwan":
        return "Mobile broadband"
    for prefix, kind in NAME_PREFIXES:
        if interface.startswith(prefix):
            return kind
    return ""


def interface_label(interface: str) -> str:
    """Something a person can recognise, with the name still on it.

    The key stores the interface name, because that is what `/proc/net/dev`
    is keyed by; this is only what the editor shows. Same contract as the
    audio devices and the Home Assistant entities.
    """
    name = str(interface or "").strip()
    if not name:
        return ""
    readable = _hardware_name(name) or _link_kind(name)
    return f"{readable} ({name})" if readable else name


def network_rate(interface: str = "") -> tuple[float, float] | None:
    """`(received bytes/s, sent bytes/s)`, or None until there are two samples.

    A blank interface means every usable one added together, which is what
    "my connection" means on a machine with wired and wireless both up.
    Throughput only exists as a difference between two readings, so the first
    call after start-up answers None rather than reporting an average since
    boot -- exactly as `cpu_percent()` does.
    """
    wanted = str(interface or "").strip()
    counters = _read_interfaces()
    if not counters:
        return None
    if wanted:
        if wanted not in counters:
            return None
        received, sent = counters[wanted]
    else:
        names = network_interfaces()
        if not names:
            return None
        received = sum(counters[name][0] for name in names)
        sent = sum(counters[name][1] for name in names)
    now = time.monotonic()
    key = wanted or "*"
    with _net_lock:
        cached = _net_value.get(key)
        # Sampling twice in quick succession measures noise rather than
        # throughput, so a caller asking sooner is given the last answer --
        # the same rule `cpu_percent()` follows.
        if cached is not None and now - cached[0] < MIN_INTERVAL:
            return cached[1], cached[2]
        previous = _net_previous.get(key)
        _net_previous[key] = (now, received, sent)
        if previous is None:
            return None
        elapsed = now - previous[0]
        if elapsed <= 0:
            return None
        # A counter that went backwards is an interface that was reset or
        # replaced. Reporting that jump as a rate would show an absurd spike,
        # so it counts as no traffic and the next reading starts again.
        down = max(0, received - previous[1]) / elapsed
        up = max(0, sent - previous[2]) / elapsed
        _net_value[key] = (now, down, up)
        return down, up


# ---------- temperature ----------

HWMON_ROOT = Path("/sys/class/hwmon")
# Chips that report the processor package, best first. `k10temp` is AMD,
# `coretemp` Intel, `acpitz` the motherboard's own zone as a last resort.
CPU_SENSORS = ("coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz")
# What the package sensor calls itself, as opposed to an individual core.
PACKAGE_LABELS = ("package id 0", "tctl", "tdie", "cpu")


def _hwmon_chips() -> list[tuple[str, Path]]:
    try:
        entries = sorted(HWMON_ROOT.iterdir())
    except OSError:
        return []
    chips = []
    for entry in entries:
        try:
            chips.append(((entry / "name").read_text().strip(), entry))
        except OSError:
            continue
    return chips


def _read_temp(path: Path) -> float | None:
    try:
        return int(path.read_text().strip()) / 1000.0
    except (OSError, ValueError):
        return None


def cpu_temperature() -> float | None:
    """The processor package temperature in Celsius, or None.

    The package rather than the hottest core: a single core spiking is normal
    and says nothing, while the package is the number a cooling problem shows
    up in. Falls back to the first reading the chip offers when no input is
    labelled as the package.
    """
    chips = {name: path for name, path in _hwmon_chips()}
    for wanted in CPU_SENSORS:
        path = chips.get(wanted)
        if path is None:
            continue
        inputs = sorted(path.glob("temp*_input"))
        for candidate in inputs:
            label_file = candidate.with_name(
                candidate.name.replace("_input", "_label")
            )
            try:
                label = label_file.read_text().strip().casefold()
            except OSError:
                continue
            if label in PACKAGE_LABELS:
                reading = _read_temp(candidate)
                if reading is not None:
                    return reading
        for candidate in inputs:
            reading = _read_temp(candidate)
            if reading is not None:
                return reading
    return None


# ---------- graphics ----------

AMD_BUSY = "/sys/class/drm/card*/device/gpu_busy_percent"
NVIDIA_TOOL = "nvidia-smi"
NVIDIA_FIELDS = "utilization.gpu,memory.used,memory.total,temperature.gpu"
# Measured at about 30 ms, which is cheap enough to call on a clock but far too
# expensive to repeat per key, so a reading is shared like the disk one.
GPU_INTERVAL = 2.0

_gpu_lock = threading.Lock()
_gpu: tuple[float, dict] = (0.0, {})


def gpu() -> dict:
    """`{"percent", "memory_percent", "temperature"}` for the first GPU.

    Missing keys mean the driver does not report that one, which is normal:
    the AMD sysfs interface has no temperature here and an integrated Intel
    chip has no busy counter at all.
    """
    global _gpu
    now = time.monotonic()
    with _gpu_lock:
        taken, value = _gpu
        if value and now - taken < GPU_INTERVAL:
            return dict(value)
    # Read outside the lock: nvidia-smi is a process, and holding the lock
    # across it would queue every other key behind one reading.
    fresh = _read_gpu()
    with _gpu_lock:
        _gpu = (now, fresh)
    return dict(fresh)


def _read_gpu() -> dict:
    reading = _read_nvidia()
    return reading if reading else _read_amd()


def _read_nvidia() -> dict:
    if shutil.which(NVIDIA_TOOL) is None:
        return {}
    try:
        result = subprocess.run(
            [NVIDIA_TOOL, f"--query-gpu={NVIDIA_FIELDS}",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if result.returncode != 0:
        return {}
    first = (result.stdout or "").strip().splitlines()
    if not first:
        return {}
    parts = [part.strip() for part in first[0].split(",")]
    if len(parts) < 4:
        return {}
    try:
        used, total = float(parts[1]), float(parts[2])
        return {
            "percent": float(parts[0]),
            "memory_percent": (used / total * 100.0) if total else None,
            "temperature": float(parts[3]),
        }
    except ValueError:
        return {}


def _read_amd() -> dict:
    for path in sorted(Path("/sys/class/drm").glob("card*/device/gpu_busy_percent")):
        try:
            busy = float(path.read_text().strip())
        except (OSError, ValueError):
            continue
        reading: dict = {"percent": busy}
        device = path.parent
        for name, target in (("mem_info_vram_used", "used"),
                             ("mem_info_vram_total", "total")):
            try:
                reading[target] = float((device / name).read_text().strip())
            except (OSError, ValueError):
                reading.pop(target, None)
        used, total = reading.pop("used", None), reading.pop("total", None)
        if used is not None and total:
            reading["memory_percent"] = used / total * 100.0
        temperature = _amd_temperature(device)
        if temperature is not None:
            reading["temperature"] = temperature
        return reading
    return {}


def _amd_temperature(device: Path) -> float | None:
    try:
        chips = sorted((device / "hwmon").iterdir())
    except OSError:
        return None
    for chip in chips:
        reading = _read_temp(chip / "temp1_input")
        if reading is not None:
            return reading
    return None


def reset() -> None:
    """Forget the previous sample. For tests, and for a fresh first reading."""
    global _previous, _value, _sampled_at, _gpu
    with _lock:
        _previous, _value, _sampled_at = None, None, 0.0
    with _disk_lock:
        _disk.clear()
    with _net_lock:
        _net_previous.clear()
        _net_value.clear()
    with _gpu_lock:
        _gpu = (0.0, {})
