"""The keys a brand new configuration is offered on its first run.

A fresh install opened on an empty deck and an editor to decipher, so the first
thirty seconds did nothing at all. These fill that gap, and they are offered
rather than imposed: a configuration nobody asked for is worse than an empty
one.

Everything here needs **no configuration whatsoever**, which is the whole
selection rule. A key that has to be pointed at a scene or an audio input would
be broken on arrival, on a machine where OBS may not even be running yet, and a
broken example teaches nothing. The OBS keys among them still cannot run until
OBS is reachable, but the deck now says so by fading them, so that reads as an
explanation rather than a fault.
"""

from __future__ import annotations

from .config import CONFIG_FILE, KIND_SINGLE, Config, KeyConfig

# In order of how much someone installing this actually wants the key, because a
# small deck takes the first few and stops. Each entry is (action, params,
# label).
STARTER_KEYS: tuple[tuple[str, dict, str], ...] = (
    ("obs.record", {"mode": "toggle"}, "Record"),
    ("obs.stream", {}, "Stream"),
    ("obs.record_chapter", {}, "Chapter"),
    ("obs.studio_mode", {}, "Studio"),
    ("obs.stats", {"metric": "system_cpu"}, "CPU"),
    ("obs.stats", {"metric": "disk"}, "Disk"),
    ("sys.stopwatch", {}, "Stopwatch"),
)


def is_first_run() -> bool:
    """Whether no configuration has ever been written on this computer."""
    return not CONFIG_FILE.exists()


def starter_keys(capacity: int) -> dict[int, KeyConfig]:
    """The starter keys that fit on a deck of `capacity` keys.

    Bounded by the hardware because a Mini has six keys: placing more would
    store keys that cannot be seen or reached, which is exactly the kind of
    invisible state a first run should not create.
    """
    keys: dict[int, KeyConfig] = {}
    for index, (action, params, label) in enumerate(STARTER_KEYS):
        if index >= max(0, capacity):
            break
        keys[index] = KeyConfig(
            kind=KIND_SINGLE,
            action=action,
            params=dict(params),
            label=label,
        )
    return keys


def apply_starter_keys(config: Config, capacity: int) -> int:
    """Put the starter keys on the current page and save. Returns how many.

    It refuses a page that already holds something, so accepting the offer can
    never overwrite work: the offer is made at start-up and the answer arrives
    whenever the user gives it.
    """
    page = config.pages[config.current_page]
    if page.configured_keys():
        return 0
    keys = starter_keys(capacity)
    for index, key in keys.items():
        page.set_key(index, key)
    if keys:
        config.save()
    return len(keys)
