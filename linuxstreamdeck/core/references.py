"""Finding keys that point at OBS objects which no longer exist.

Renaming a scene in OBS is the one failure this application never reports: the
key stays where it is, with its icon, and simply does nothing when pressed. You
find out live.

Two things bound what a checker can honestly say. obs-websocket can only list
what is in the **loaded scene collection** — there is no way to ask what a
collection that is not loaded contains without switching to it — so every
answer is relative to what OBS has open right now. And a collection switch
changes every name at once, which is why this never runs by itself: the user
starts it, standing where they are, and therefore knows the context of the
result.

Nothing here talks to OBS or to GTK. It walks a grid and compares against
whatever lists it is handed.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field

from . import actions as registry
from .config import STEP_FIELDS, KeyConfig

# Option sources that name something inside OBS. `pages`, `deck_profiles` and
# `applications` are deliberately absent: they are local, and a renamed page is
# already rewritten across the configuration when it happens.
OBS_SOURCES = frozenset({
    "scenes",
    "inputs",
    "media_inputs",
    "text_inputs",
    "browser_inputs",
    "transitions",
    "scene_collections",
    "profiles",
    "sources_in_scene",
    "audio_sources_in_scene",
    "filters_of_source",
})

# Sources resolved inside another parameter's value rather than globally, and
# the parameter that supplies it.
DEPENDENT_SOURCES = {
    "sources_in_scene": "scene",
    "audio_sources_in_scene": "scene",
    "filters_of_source": "source",
}

# Readable names, for a report that has to be understood at a glance.
KIND_NAMES = {
    "scenes": "Scene",
    "inputs": "Input",
    "media_inputs": "Media source",
    "text_inputs": "Text source",
    "browser_inputs": "Browser source",
    "transitions": "Transition",
    "scene_collections": "Scene collection",
    "profiles": "OBS profile",
    "sources_in_scene": "Source",
    "audio_sources_in_scene": "Audio input",
    "filters_of_source": "Filter",
}

# How alike a surviving name has to be before it is offered as the thing the
# user meant. Low enough to catch a typo or a small rename, high enough not to
# propose "Camera 2" for "Chat".
SUGGESTION_CUTOFF = 0.6


@dataclass(frozen=True)
class Reference:
    """One stored value that names something in OBS."""

    kind: str
    value: str
    params: dict = field(compare=False, repr=False)
    param: str
    action: str
    where: str
    parent: str = ""

    def kind_name(self) -> str:
        return KIND_NAMES.get(self.kind, self.kind)


@dataclass(frozen=True)
class Finding:
    """One missing name, and everything that points at it."""

    kind: str
    value: str
    references: tuple[Reference, ...]
    suggestion: str = ""

    def kind_name(self) -> str:
        return KIND_NAMES.get(self.kind, self.kind)

    def summary(self) -> str:
        count = len(self.references)
        return (
            f"{self.kind_name()} «{self.value}» — used by "
            f"{count} key{'s' if count != 1 else ''}"
        )


@dataclass(frozen=True)
class Report:
    """What one check found, including what it found nothing wrong with."""

    collection: str
    findings: tuple[Finding, ...]
    checked: int
    keys: int

    def is_clean(self) -> bool:
        return not self.findings

    def broken_keys(self) -> int:
        return len({id(ref.params) for f in self.findings for ref in f.references})


def _key_steps(kc: KeyConfig):
    """Every (action id, params) a key would run, its own action included."""
    if kc.action and isinstance(kc.params, dict):
        yield kc.action, kc.params
    for field_name in STEP_FIELDS:
        for step in getattr(kc, field_name, []):
            if isinstance(step.params, dict):
                yield step.action, step.params


def _references_of(action_id: str, params: dict, where: str):
    """The OBS names a single step points at."""
    action = registry.get(action_id)
    if action is None:
        return
    for param in action.params:
        source = param.choices_source
        if source not in OBS_SOURCES or param.advisory:
            continue
        value = str(params.get(param.name, "") or "").strip()
        if not value:
            continue
        parent_name = DEPENDENT_SOURCES.get(source, "")
        parent = str(params.get(parent_name, "") or "").strip() if parent_name else ""
        yield Reference(
            kind=source,
            value=value,
            params=params,
            param=param.name,
            action=action_id,
            where=where,
            parent=parent,
        )


def collect(grid, dials=None, trail: str = "") -> list[Reference]:
    """Every OBS name referenced by a grid and everything nested inside it.

    Folders are walked because they are part of where you are: standing on a
    page, the folders on it belong to that page.
    """
    found: list[Reference] = []
    for raw, kc in sorted(grid.keys.items(), key=lambda item: _as_int(item[0])):
        if kc is None:
            continue
        where = f"{trail}Key {_as_int(raw) + 1}"
        for action_id, params in _key_steps(kc):
            found.extend(_references_of(action_id, params, where))
        contents = kc.contents
        if contents is not None:
            name = kc.folder_name()
            found.extend(collect(contents, trail=f"{trail}{name} › "))
    for raw, dial in sorted((dials or {}).items(), key=lambda item: _as_int(item[0])):
        if dial is None:
            continue
        where = f"{trail}Dial {_as_int(raw) + 1}"
        for action_id, params in _key_steps(dial):
            found.extend(_references_of(action_id, params, where))
    return found


def _as_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def suggest(missing: str, candidates) -> str:
    """The surviving name closest to a missing one, or "" when none is close."""
    options = [name for name in candidates if name]
    if not options:
        return ""
    close = difflib.get_close_matches(missing, options, n=1, cutoff=SUGGESTION_CUTOFF)
    if close:
        return close[0]
    # A rename that only added or dropped a word scores badly against a short
    # name, so containment is tried before giving up.
    lowered = missing.casefold()
    for name in options:
        folded = name.casefold()
        if lowered in folded or folded in lowered:
            return name
    return ""


def check(references, available) -> tuple[Finding, ...]:
    """Group the references that no longer resolve, newest problem first.

    `available(kind, parent)` returns the names OBS currently has, or None when
    the answer is unknown — a source whose scene is itself missing cannot be
    judged, and guessing there would report one rename twice.
    """
    missing: dict[tuple[str, str], list[Reference]] = {}
    for reference in references:
        names = available(reference.kind, reference.parent)
        if names is None:
            continue
        if reference.value in names:
            continue
        missing.setdefault((reference.kind, reference.value), []).append(reference)

    findings = []
    for (kind, value), refs in missing.items():
        names = available(kind, refs[0].parent) or []
        findings.append(
            Finding(
                kind=kind,
                value=value,
                references=tuple(refs),
                suggestion=suggest(value, names),
            )
        )
    # Most-used first: fixing the widest problem first is the natural order.
    findings.sort(key=lambda f: (-len(f.references), f.kind, f.value))
    return tuple(findings)


def apply_fix(finding: Finding, replacement: str) -> int:
    """Point every reference of a finding at `replacement`. Returns how many.

    The parameter dictionaries are the live ones, so this edits the
    configuration in place; the caller saves it and is responsible for having
    taken a backup first.
    """
    replacement = str(replacement or "").strip()
    if not replacement:
        return 0
    for reference in finding.references:
        reference.params[reference.param] = replacement
    return len(finding.references)
