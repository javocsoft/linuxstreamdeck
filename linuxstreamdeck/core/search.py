"""Finding a key among every profile, page and folder.

With profiles times pages times folders there is no way to answer "which key
mutes my mic?" other than opening each grid in turn. This walks all of them and
matches a query against what a key *is* — its labels, its actions by name and by
id, and its parameter values — so a key can be found by what it does.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import actions as registry
from .config import (
    STEP_FIELDS,
    ActionStep,
    Config,
    KeyConfig,
    KeyGrid,
)


@dataclass(frozen=True)
class KeyLocation:
    """One key and everything needed to navigate back to it."""

    profile: int
    page: int
    path: tuple[int, ...]
    index: int
    key: KeyConfig
    profile_name: str = ""
    page_name: str = ""
    trail: tuple[str, ...] = field(default_factory=tuple)

    def where(self) -> str:
        """Human-readable position, e.g. "Streaming / Live ▸ Scenes / Key 4"."""
        place = " ▸ ".join((self.page_name, *self.trail))
        return f"{self.profile_name} / {place} / Key {self.index + 1}"

    def what(self) -> str:
        """What the key is called, or what it does when it has no label."""
        return self.key.label.strip() or key_summary(self.key)


def key_steps(kc: KeyConfig) -> list[ActionStep]:
    """Every step a key runs, without descending into its folder.

    Nested keys are reached by the walk itself, so recursing here would credit
    a folder with everything inside it and make every search match it.
    """
    steps: list[ActionStep] = []
    if kc.action:
        steps.append(ActionStep(action=kc.action, params=kc.params))
    for name in STEP_FIELDS:
        steps.extend(getattr(kc, name, ()) or ())
    return steps


def key_summary(kc: KeyConfig) -> str:
    """A short description of what a key does, for a result with no label."""
    if kc.contents is not None:
        return kc.folder_name()
    names = []
    for step in key_steps(kc):
        action = registry.get(step.action)
        name = action.name if action is not None else step.action
        if name and name not in names:
            names.append(name)
    if not names:
        return "Empty key"
    return names[0] if len(names) == 1 else f"{names[0]} +{len(names) - 1}"


def key_terms(kc: KeyConfig) -> str:
    """Everything about a key that is worth searching, lowercased."""
    parts: list[str] = [kc.label, kc.label_off]
    if kc.contents is not None:
        parts.append(kc.folder_name())
    for step in key_steps(kc):
        parts.append(step.action)
        parts.append(step.label)
        action = registry.get(step.action)
        if action is not None:
            parts.extend((action.name, action.category))
        parts.extend(str(value) for value in step.params.values())
    return " ".join(part for part in parts if part).lower()


def matches(kc: KeyConfig, query: str) -> bool:
    """Whether a key satisfies every term of a query."""
    terms = query.lower().split()
    if not terms:
        return False
    haystack = key_terms(kc)
    return all(term in haystack for term in terms)


def locations(config: Config):
    """Every configured key, with where it lives."""
    for profile_index, profile in enumerate(config.profiles):
        for page_index, page in enumerate(profile.pages):
            yield from _walk(
                page, profile_index, page_index, (), (), profile.name, page.name
            )


def _walk(
    grid: KeyGrid,
    profile: int,
    page: int,
    path: tuple[int, ...],
    trail: tuple[str, ...],
    profile_name: str,
    page_name: str,
):
    for raw_index, kc in grid.keys.items():
        try:
            index = int(raw_index)
        except (TypeError, ValueError):
            continue
        yield KeyLocation(
            profile=profile,
            page=page,
            path=path,
            index=index,
            key=kc,
            profile_name=profile_name,
            page_name=page_name,
            trail=trail,
        )
        contents = kc.contents
        if contents is not None:
            yield from _walk(
                contents,
                profile,
                page,
                path + (index,),
                trail + (kc.folder_name(),),
                profile_name,
                page_name,
            )


def search(config: Config, query: str) -> list[KeyLocation]:
    """Every key matching `query`, in the order the deck holds them."""
    if not query.strip():
        return []
    return [
        found for found in locations(config) if matches(found.key, query)
    ]
