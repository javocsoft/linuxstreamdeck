"""Configuration model and JSON persistence.

File: ~/.config/linuxstreamdeck/config.json
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import shutil
import tempfile
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

from .actions import format_duration

log = logging.getLogger(__name__)

# The path can be redirected with LSD_CONFIG_DIR (useful for tests, so the user's
# real configuration is not touched, and for keeping separate configurations).
CONFIG_DIR = Path(
    os.environ.get("LSD_CONFIG_DIR", Path.home() / ".config" / "linuxstreamdeck")
)
CONFIG_FILE = CONFIG_DIR / "config.json"
BACKUP_FILE = CONFIG_DIR / "config.json.bak"

DEFAULT_KEY_BG = "#1e1e28"

EXPORT_FORMAT = "linuxstreamdeck-configuration"
EXPORT_VERSION = 1
EXPORT_CONFIG_FILE = "config.json"
EXPORT_MANIFEST_FILE = "manifest.json"
EXPORT_ICON_PREFIX = "bundle:"
MAX_CONFIG_BYTES = 10 * 1024 * 1024
MAX_ICON_BYTES = 50 * 1024 * 1024
MAX_TOTAL_ICON_BYTES = 200 * 1024 * 1024

# Key types
KIND_SINGLE = "single"          # a single action (with state feedback)
KIND_MULTI = "multi"            # ordered list of actions run in sequence
KIND_TOGGLE = "multi_toggle"    # toggle: two action lists (ON/OFF state)


@dataclass(frozen=True)
class ExportResult:
    bundled_icons: int
    missing_icons: int


@dataclass(frozen=True)
class ImportResult:
    profiles: int
    pages: int
    keys: int
    restored_icons: int


@dataclass
class ActionStep:
    """A step inside a multi-action key. Pauses between steps are expressed as an
    explicit Wait action (sys.wait), not a per-step delay."""
    action: str = ""                      # id of a registered action
    params: dict = field(default_factory=dict)


@dataclass
class KeyConfig:
    kind: str = KIND_SINGLE

    # kind == single
    action: str = ""                      # id of a registered action, e.g. "obs.scene_switch"
    params: dict = field(default_factory=dict)

    # kind == multi
    steps: list[ActionStep] = field(default_factory=list)

    # kind == multi_toggle (steps_on runs when turning ON; steps_off when turning OFF)
    steps_on: list[ActionStep] = field(default_factory=list)
    steps_off: list[ActionStep] = field(default_factory=list)

    # appearance (main state / ON state on toggles)
    label: str = ""
    icon: str = ""                        # path to an optional image
    bg_color: str = DEFAULT_KEY_BG

    # OFF state appearance (toggles only)
    label_off: str = ""
    icon_off: str = ""
    bg_color_off: str = DEFAULT_KEY_BG

    def is_empty(self) -> bool:
        if self.kind == KIND_SINGLE:
            return not self.action
        if self.kind == KIND_MULTI:
            return not self.steps
        if self.kind == KIND_TOGGLE:
            return not (self.steps_on or self.steps_off)
        return True

    def clone(self) -> "KeyConfig":
        """Independent deep copy (for copy/paste and moving keys)."""
        return copy.deepcopy(self)

    @classmethod
    def from_dict(cls, d: dict) -> "KeyConfig":
        if not isinstance(d, dict):
            raise ValueError("Each key configuration must be a JSON object")

        def steps(name: str) -> list[ActionStep]:
            out: list[ActionStep] = []
            raw_steps = d.get(name, [])
            if not isinstance(raw_steps, list):
                raise ValueError(f"The {name} field must be a list")
            for s in raw_steps:
                if not isinstance(s, dict):
                    raise ValueError("Each action step must be a JSON object")
                out.append(ActionStep(action=s.get("action", ""),
                                      params=s.get("params", {})))
                # legacy: a per-step "delay after (ms)" becomes an explicit Wait
                # action (whole seconds), so old configs keep their pauses.
                secs = round(s.get("delay_ms", 0) / 1000)
                if secs > 0:
                    out.append(ActionStep(action="sys.wait",
                                          params={"duration": format_duration(secs)}))
            return out

        return cls(
            kind=d.get("kind", KIND_SINGLE),
            action=d.get("action", ""),
            params=d.get("params", {}),
            steps=steps("steps"),
            steps_on=steps("steps_on"),
            steps_off=steps("steps_off"),
            label=d.get("label", ""),
            icon=d.get("icon", ""),
            bg_color=d.get("bg_color", DEFAULT_KEY_BG),
            label_off=d.get("label_off", ""),
            icon_off=d.get("icon_off", ""),
            bg_color_off=d.get("bg_color_off", DEFAULT_KEY_BG),
        )


@dataclass
class Page:
    name: str = "Page 1"
    # keys as str for JSON compatibility: "0".."14"
    keys: dict[str, KeyConfig] = field(default_factory=dict)

    def key(self, index: int) -> KeyConfig | None:
        return self.keys.get(str(index))

    def set_key(self, index: int, kc: KeyConfig | None) -> None:
        if kc is None:
            self.keys.pop(str(index), None)
        else:
            self.keys[str(index)] = kc

    @classmethod
    def from_dict(cls, d: dict, index: int = 0) -> "Page":
        if not isinstance(d, dict):
            raise ValueError("Each page must be a JSON object")
        raw_keys = d.get("keys", {})
        if not isinstance(raw_keys, dict):
            raise ValueError("The page keys field must be a JSON object")
        return cls(
            name=d.get("name", f"Page {index + 1}"),
            keys={str(k): KeyConfig.from_dict(v) for k, v in raw_keys.items()},
        )


@dataclass
class Profile:
    """A set of pages/keys with a name and a short description."""
    name: str = "General"
    description: str = ""
    pages: list[Page] = field(default_factory=lambda: [Page()])
    current_page: int = 0

    @classmethod
    def from_dict(cls, d: dict) -> "Profile":
        if not isinstance(d, dict):
            raise ValueError("Each profile must be a JSON object")
        raw_pages = d.get("pages", [])
        if not isinstance(raw_pages, list):
            raise ValueError("The profile pages field must be a list")
        pages = [Page.from_dict(p, i) for i, p in enumerate(raw_pages)] or [Page()]
        try:
            current_page = int(d.get("current_page", 0))
        except (TypeError, ValueError):
            current_page = 0
        return cls(
            name=d.get("name", "General"),
            description=d.get("description", ""),
            pages=pages,
            current_page=max(0, min(current_page, len(pages) - 1)),
        )


@dataclass
class ObsSettings:
    host: str = "localhost"
    port: int = 4455
    password: str = ""


@dataclass
class Config:
    profiles: list[Profile] = field(default_factory=lambda: [Profile()])
    current_profile: int = 0
    obs: ObsSettings = field(default_factory=ObsSettings)
    brightness: int = 80

    # ---------- access to the active profile ----------
    # `pages` and `current_page` delegate to the active profile so the rest of the
    # code (controller, actions, UI) keeps using them exactly as before.

    @property
    def profile(self) -> Profile:
        return self.profiles[self.current_profile]

    @property
    def pages(self) -> list[Page]:
        return self.profile.pages

    @property
    def current_page(self) -> int:
        return self.profile.current_page

    @current_page.setter
    def current_page(self, value: int) -> None:
        self.profile.current_page = value

    # ---------- persistence ----------

    @classmethod
    def load(cls) -> "Config":
        if not CONFIG_FILE.exists():
            return cls()
        try:
            raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            return cls.from_dict(raw)
        except Exception:
            log.exception("Could not read %s; using an empty configuration", CONFIG_FILE)
            return cls()

    @classmethod
    def from_dict(cls, raw: dict) -> "Config":
        if not isinstance(raw, dict):
            raise ValueError("The configuration root must be a JSON object")
        if "profiles" in raw:
            raw_profiles = raw["profiles"]
            if not isinstance(raw_profiles, list):
                raise ValueError("The profiles field must be a list")
            profiles = [Profile.from_dict(p) for p in raw_profiles] or [Profile()]
            current = cls._bounded_index(
                raw.get("current_profile", 0), len(profiles)
            )
        else:
            # Old format (pages at the top level): migrate to a single
            # "General" profile so no configuration is lost.
            raw_pages = raw.get("pages", [])
            if not isinstance(raw_pages, list):
                raise ValueError("The pages field must be a list")
            pages = [Page.from_dict(p, i) for i, p in enumerate(raw_pages)] or [Page()]
            profiles = [Profile(
                name="General",
                pages=pages,
                current_page=cls._bounded_index(
                    raw.get("current_page", 0), len(pages)
                ),
            )]
            current = 0

        raw_obs = raw.get("obs", {})
        if not isinstance(raw_obs, dict):
            raise ValueError("The OBS settings must be a JSON object")
        try:
            obs = ObsSettings(
                host=str(raw_obs.get("host", "localhost")),
                port=int(raw_obs.get("port", 4455)),
                password=str(raw_obs.get("password", "")),
            )
            brightness = max(10, min(100, int(raw.get("brightness", 80))))
        except (TypeError, ValueError) as error:
            raise ValueError("The configuration contains an invalid number") from error
        return cls(
            profiles=profiles,
            current_profile=current,
            obs=obs,
            brightness=brightness,
        )

    @staticmethod
    def _bounded_index(value, length: int) -> int:
        try:
            index = int(value)
        except (TypeError, ValueError):
            index = 0
        return max(0, min(index, length - 1))

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        # back up the previous version before overwriting, so it can be recovered
        # if a save leaves it broken (see config.json.bak)
        if CONFIG_FILE.exists():
            try:
                shutil.copy2(CONFIG_FILE, BACKUP_FILE)
            except Exception:
                log.debug("Could not back up the configuration", exc_info=True)
        CONFIG_FILE.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        log.debug("Configuration saved to %s", CONFIG_FILE)

    # ---------- portable import / export ----------

    def export_bundle(self, destination: Path) -> ExportResult:
        """Write a portable configuration archive, including custom key icons."""
        raw = asdict(self)
        icon_files: dict[str, bytes] = {}
        missing_icons: set[str] = set()

        for profile in raw["profiles"]:
            for page in profile["pages"]:
                for key in page["keys"].values():
                    for field_name in ("icon", "icon_off"):
                        ref = key.get(field_name, "")
                        if not ref or ref.startswith("mdi:"):
                            continue
                        try:
                            source = Path(ref).expanduser()
                            if not source.is_file() or source.stat().st_size > MAX_ICON_BYTES:
                                missing_icons.add(ref)
                                continue
                            data = source.read_bytes()
                        except OSError:
                            missing_icons.add(ref)
                            continue
                        digest = hashlib.sha256(data).hexdigest()
                        suffix = self._safe_icon_suffix(source.suffix)
                        archive_name = f"icons/{digest}{suffix}"
                        icon_files.setdefault(archive_name, data)
                        key[field_name] = f"{EXPORT_ICON_PREFIX}{archive_name}"

        manifest = {
            "format": EXPORT_FORMAT,
            "version": EXPORT_VERSION,
        }
        destination = Path(destination).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.NamedTemporaryFile(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        )
        temporary_path = Path(temporary.name)
        temporary.close()
        try:
            with zipfile.ZipFile(
                temporary_path, mode="w", compression=zipfile.ZIP_DEFLATED
            ) as archive:
                archive.writestr(
                    EXPORT_MANIFEST_FILE,
                    json.dumps(manifest, indent=2),
                )
                archive.writestr(
                    EXPORT_CONFIG_FILE,
                    json.dumps(raw, indent=2, ensure_ascii=False),
                )
                for archive_name, data in icon_files.items():
                    archive.writestr(archive_name, data)
            temporary_path.replace(destination)
        finally:
            temporary_path.unlink(missing_ok=True)
        return ExportResult(
            bundled_icons=len(icon_files),
            missing_icons=len(missing_icons),
        )

    def import_bundle(self, source: Path) -> ImportResult:
        """Validate and apply a portable configuration archive."""
        source = Path(source).expanduser()
        try:
            archive = zipfile.ZipFile(source, mode="r")
        except (OSError, zipfile.BadZipFile) as error:
            raise ValueError("This is not a valid LinuxStreamDeck export") from error

        with archive:
            manifest = self._read_bundle_json(
                archive, EXPORT_MANIFEST_FILE, MAX_CONFIG_BYTES
            )
            if (
                manifest.get("format") != EXPORT_FORMAT
                or manifest.get("version") != EXPORT_VERSION
            ):
                raise ValueError("This LinuxStreamDeck export version is not supported")
            raw = self._read_bundle_json(
                archive, EXPORT_CONFIG_FILE, MAX_CONFIG_BYTES
            )
            replacement = Config.from_dict(raw)
            icon_payloads: dict[Path, bytes] = {}
            restored_members: dict[str, Path] = {}
            total_icon_bytes = 0

            for key in replacement._key_configs():
                for field_name in ("icon", "icon_off"):
                    ref = getattr(key, field_name)
                    if not ref.startswith(EXPORT_ICON_PREFIX):
                        continue
                    archive_name = ref.removeprefix(EXPORT_ICON_PREFIX)
                    if archive_name in restored_members:
                        setattr(key, field_name, str(restored_members[archive_name]))
                        continue
                    self._validate_icon_member(archive_name)
                    data = self._read_bundle_member(
                        archive, archive_name, MAX_ICON_BYTES
                    )
                    total_icon_bytes += len(data)
                    if total_icon_bytes > MAX_TOTAL_ICON_BYTES:
                        raise ValueError("The exported icon files are too large")
                    suffix = self._safe_icon_suffix(
                        PurePosixPath(archive_name).suffix
                    )
                    digest = hashlib.sha256(data).hexdigest()
                    target = CONFIG_DIR / "imported-icons" / f"{digest}{suffix}"
                    icon_payloads[target] = data
                    restored_members[archive_name] = target
                    setattr(key, field_name, str(target))

        for target, data in icon_payloads.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            self._write_imported_icon(target, data)

        replacement.save()
        self.profiles = replacement.profiles
        self.current_profile = replacement.current_profile
        self.obs = replacement.obs
        self.brightness = replacement.brightness
        return ImportResult(
            profiles=len(self.profiles),
            pages=sum(len(profile.pages) for profile in self.profiles),
            keys=sum(
                len(page.keys)
                for profile in self.profiles
                for page in profile.pages
            ),
            restored_icons=len(icon_payloads),
        )

    def _key_configs(self):
        for profile in self.profiles:
            for page in profile.pages:
                yield from page.keys.values()

    @staticmethod
    def _safe_icon_suffix(suffix: str) -> str:
        suffix = suffix.lower()
        if (
            suffix.startswith(".")
            and len(suffix) <= 10
            and suffix[1:].isalnum()
        ):
            return suffix
        return ".img"

    @staticmethod
    def _write_imported_icon(target: Path, data: bytes) -> None:
        temporary = tempfile.NamedTemporaryFile(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
            delete=False,
        )
        temporary_path = Path(temporary.name)
        try:
            temporary.write(data)
            temporary.close()
            temporary_path.replace(target)
        finally:
            temporary.close()
            temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _validate_icon_member(name: str) -> None:
        path = PurePosixPath(name)
        if (
            path.is_absolute()
            or len(path.parts) != 2
            or path.parts[0] != "icons"
            or any(part in ("", ".", "..") for part in path.parts)
        ):
            raise ValueError("The export contains an invalid icon path")

    @staticmethod
    def _read_bundle_member(
        archive: zipfile.ZipFile, name: str, maximum_size: int
    ) -> bytes:
        try:
            info = archive.getinfo(name)
        except KeyError as error:
            raise ValueError(f"The export is missing {name}") from error
        if info.is_dir() or info.file_size > maximum_size:
            raise ValueError(f"The exported file {name} is invalid or too large")
        return archive.read(info)

    @classmethod
    def _read_bundle_json(
        cls, archive: zipfile.ZipFile, name: str, maximum_size: int
    ) -> dict:
        data = cls._read_bundle_member(archive, name, maximum_size)
        try:
            value = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"The exported file {name} is not valid JSON") from error
        if not isinstance(value, dict):
            raise ValueError(f"The exported file {name} must contain a JSON object")
        return value
