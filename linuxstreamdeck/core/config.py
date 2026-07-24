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

from ..ai.constants import (
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_OPENAI_MODEL,
    PROVIDER_OPENAI,
    PROVIDERS,
)
from .actions import format_duration
from .audio import SUPPORTED_AUDIO_EXTENSIONS

log = logging.getLogger(__name__)

# The path can be redirected with LSD_CONFIG_DIR (useful for tests, so the user's
# real configuration is not touched, and for keeping separate configurations).
CONFIG_DIR = Path(
    os.environ.get("LSD_CONFIG_DIR", Path.home() / ".config" / "linuxstreamdeck")
)
CONFIG_FILE = CONFIG_DIR / "config.json"
BACKUP_FILE = CONFIG_DIR / "config.json.bak"

DEFAULT_KEY_BG = "#1e1e28"

SCREENSAVER_CHOICES = (
    (
        "neon_pipes",
        "Neon Pipes",
        "Retro glowing pipes grow and turn across the whole deck.",
    ),
    (
        "digital_rain",
        "Digital Rain",
        "Elegant cyan data trails fall through a dark digital grid.",
    ),
    (
        "aurora_flow",
        "Aurora Flow",
        "Layered blue, violet and teal light waves drift slowly.",
    ),
    (
        "orbital_core",
        "Orbital Core",
        "A futuristic core with rotating rings and orbiting particles.",
    ),
    (
        "circuit_pulse",
        "Circuit Pulse",
        "Energy pulses travel through a refined circuit-board network.",
    ),
    (
        "linuxstreamdeck",
        "LinuxStreamDeck",
        "A black screen with the LinuxStreamDeck name breathing softly.",
    ),
)
SCREENSAVER_IDS = frozenset(choice[0] for choice in SCREENSAVER_CHOICES)
DEFAULT_SCREENSAVER = SCREENSAVER_CHOICES[0][0]

EXPORT_FORMAT = "linuxstreamdeck-configuration"
EXPORT_VERSION = 2
EXPORT_CONFIG_FILE = "config.json"
EXPORT_MANIFEST_FILE = "manifest.json"
EXPORT_ICON_PREFIX = "bundle:"
EXPORT_AUDIO_PREFIX = "bundle-audio:"
MAX_CONFIG_BYTES = 10 * 1024 * 1024
MAX_ICON_BYTES = 50 * 1024 * 1024
MAX_TOTAL_ICON_BYTES = 200 * 1024 * 1024
MAX_AUDIO_BYTES = 200 * 1024 * 1024
MAX_TOTAL_AUDIO_BYTES = 500 * 1024 * 1024

_ACTION_AUDIO_PARAMETERS = {
    "sys.audio": "file",
    "sys.timer": "sound",
}

# Key types
KIND_SINGLE = "single"          # a single action (with state feedback)
KIND_MULTI = "multi"            # ordered list of actions run in sequence
KIND_TOGGLE = "multi_toggle"    # toggle: two action lists (ON/OFF state)


def _migrate_page_action(action, params) -> tuple[str, dict]:
    """Split the legacy combined page action into one explicit action."""
    action_id = str(action or "")
    values = dict(params) if isinstance(params, dict) else {}
    if action_id != "nav.page":
        return action_id, values
    mode = str(values.get("mode", "go to") or "go to").strip().lower()
    if mode in ("next", "next page"):
        return "nav.page.next", {}
    if mode in ("previous", "previous page"):
        return "nav.page.previous", {}
    return "nav.page.go", {"page": str(values.get("page", "") or "")}


@dataclass(frozen=True)
class ExportResult:
    bundled_icons: int
    missing_icons: int
    bundled_audio: int = 0
    missing_audio: int = 0


@dataclass(frozen=True)
class ImportResult:
    profiles: int
    pages: int
    keys: int
    restored_icons: int
    restored_audio: int = 0


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
                action, params = _migrate_page_action(
                    s.get("action", ""),
                    s.get("params", {}),
                )
                out.append(ActionStep(action=action, params=params))
                # legacy: a per-step "delay after (ms)" becomes an explicit Wait
                # action (whole seconds), so old configs keep their pauses.
                secs = round(s.get("delay_ms", 0) / 1000)
                if secs > 0:
                    out.append(ActionStep(action="sys.wait",
                                          params={"duration": format_duration(secs)}))
            return out

        action, params = _migrate_page_action(
            d.get("action", ""),
            d.get("params", {}),
        )
        return cls(
            kind=d.get("kind", KIND_SINGLE),
            action=action,
            params=params,
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
class AISettings:
    provider: str = PROVIDER_OPENAI
    openai_model: str = DEFAULT_OPENAI_MODEL
    anthropic_model: str = DEFAULT_ANTHROPIC_MODEL
    include_obs_context: bool = False


@dataclass
class ScreenSaverSettings:
    enabled: bool = False
    style: str = DEFAULT_SCREENSAVER
    idle_minutes: int = 5
    intensity: int = 35


@dataclass
class Config:
    profiles: list[Profile] = field(default_factory=lambda: [Profile()])
    current_profile: int = 0
    obs: ObsSettings = field(default_factory=ObsSettings)
    ai: AISettings = field(default_factory=AISettings)
    screensaver: ScreenSaverSettings = field(default_factory=ScreenSaverSettings)
    brightness: int = 80
    obs_password_needs_migration: bool = field(
        default=False, repr=False, compare=False
    )

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
        raw_ai = raw.get("ai", {})
        if not isinstance(raw_ai, dict):
            raise ValueError("The AI settings must be a JSON object")
        raw_screensaver = raw.get("screensaver", {})
        if not isinstance(raw_screensaver, dict):
            raise ValueError("The screen saver settings must be a JSON object")
        try:
            legacy_password = str(raw_obs.get("password", ""))
            obs = ObsSettings(
                host=str(raw_obs.get("host", "localhost")),
                port=int(raw_obs.get("port", 4455)),
                password=legacy_password,
            )
            provider = str(raw_ai.get("provider", PROVIDER_OPENAI)).lower()
            if provider not in PROVIDERS:
                provider = PROVIDER_OPENAI
            ai = AISettings(
                provider=provider,
                openai_model=cls._model_setting(
                    raw_ai.get("openai_model"), DEFAULT_OPENAI_MODEL
                ),
                anthropic_model=cls._model_setting(
                    raw_ai.get("anthropic_model"), DEFAULT_ANTHROPIC_MODEL
                ),
                include_obs_context=(
                    raw_ai.get("include_obs_context", False)
                    if isinstance(raw_ai.get("include_obs_context", False), bool)
                    else False
                ),
            )
            screen_style = str(
                raw_screensaver.get("style", DEFAULT_SCREENSAVER)
            )
            if screen_style not in SCREENSAVER_IDS:
                screen_style = DEFAULT_SCREENSAVER
            screensaver = ScreenSaverSettings(
                enabled=(
                    raw_screensaver.get("enabled", False)
                    if isinstance(raw_screensaver.get("enabled", False), bool)
                    else False
                ),
                style=screen_style,
                idle_minutes=max(
                    1,
                    min(1440, int(raw_screensaver.get("idle_minutes", 5))),
                ),
                intensity=max(
                    5,
                    min(100, int(raw_screensaver.get("intensity", 35))),
                ),
            )
            brightness = max(10, min(100, int(raw.get("brightness", 80))))
        except (TypeError, ValueError) as error:
            raise ValueError("The configuration contains an invalid number") from error
        return cls(
            profiles=profiles,
            current_profile=current,
            obs=obs,
            ai=ai,
            screensaver=screensaver,
            brightness=brightness,
            obs_password_needs_migration="password" in raw_obs,
        )

    @staticmethod
    def _model_setting(value, default: str) -> str:
        model = str(value or default).strip()
        return model[:160] or default

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
        self._write_json_file(
            CONFIG_FILE,
            self._serializable_dict(include_legacy_password=True),
        )
        log.debug("Configuration saved to %s", CONFIG_FILE)

    def finish_password_migration(self) -> None:
        """Persist without the legacy password and scrub it from both JSON files."""
        self.obs_password_needs_migration = False
        self.save()
        if not self.scrub_plaintext_password_files():
            raise OSError("A legacy configuration file could not be sanitized")

    def scrub_plaintext_password_files(self) -> bool:
        """Remove a legacy OBS password field from current and backup JSON files."""
        success = True
        for path in (CONFIG_FILE, BACKUP_FILE):
            if not path.exists():
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                obs = raw.get("obs")
                if isinstance(obs, dict) and "password" in obs:
                    obs.pop("password")
                    self._write_json_file(path, raw)
            except Exception:
                log.warning(
                    "Could not remove a legacy password from %s",
                    path,
                    exc_info=True,
                )
                success = False
        return success

    def _serializable_dict(self, include_legacy_password: bool = False) -> dict:
        raw = asdict(self)
        raw.pop("obs_password_needs_migration", None)
        if not (
            include_legacy_password and self.obs_password_needs_migration
        ):
            raw["obs"].pop("password", None)
        return raw

    # ---------- portable import / export ----------

    def export_bundle(self, destination: Path) -> ExportResult:
        """Write a portable archive with custom icons and action audio files."""
        raw = self._serializable_dict()
        icon_files: dict[str, bytes] = {}
        missing_icons: set[str] = set()
        audio_files: dict[str, bytes] = {}
        missing_audio: set[str] = set()
        total_audio_bytes = 0

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
                    for action, params in self._raw_action_params(key):
                        parameter = _ACTION_AUDIO_PARAMETERS.get(action)
                        if parameter is None:
                            continue
                        ref = str(params.get(parameter, "") or "")
                        if not ref:
                            continue
                        try:
                            source = Path(ref).expanduser()
                            if (
                                not source.is_file()
                                or source.stat().st_size > MAX_AUDIO_BYTES
                                or source.suffix.lower()
                                not in SUPPORTED_AUDIO_EXTENSIONS
                            ):
                                missing_audio.add(ref)
                                continue
                            data = source.read_bytes()
                        except OSError:
                            missing_audio.add(ref)
                            continue
                        digest = hashlib.sha256(data).hexdigest()
                        suffix = source.suffix.lower()
                        archive_name = f"audio/{digest}{suffix}"
                        if archive_name not in audio_files:
                            if total_audio_bytes + len(data) > MAX_TOTAL_AUDIO_BYTES:
                                missing_audio.add(ref)
                                continue
                            audio_files[archive_name] = data
                            total_audio_bytes += len(data)
                        params[parameter] = (
                            f"{EXPORT_AUDIO_PREFIX}{archive_name}"
                        )

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
                for archive_name, data in audio_files.items():
                    archive.writestr(archive_name, data)
            temporary_path.replace(destination)
        finally:
            temporary_path.unlink(missing_ok=True)
        return ExportResult(
            bundled_icons=len(icon_files),
            missing_icons=len(missing_icons),
            bundled_audio=len(audio_files),
            missing_audio=len(missing_audio),
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
                or manifest.get("version") not in (1, EXPORT_VERSION)
            ):
                raise ValueError("This LinuxStreamDeck export version is not supported")
            raw = self._read_bundle_json(
                archive, EXPORT_CONFIG_FILE, MAX_CONFIG_BYTES
            )
            current_password = self.obs.password
            password_needs_migration = self.obs_password_needs_migration
            replacement = Config.from_dict(raw)
            replacement.obs.password = current_password
            replacement.obs_password_needs_migration = password_needs_migration
            icon_payloads: dict[Path, bytes] = {}
            audio_payloads: dict[Path, bytes] = {}
            restored_members: dict[str, Path] = {}
            restored_audio_members: dict[str, Path] = {}
            total_icon_bytes = 0
            total_audio_bytes = 0

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
                for action, params in replacement._action_params(key):
                    parameter = _ACTION_AUDIO_PARAMETERS.get(action)
                    if parameter is None:
                        continue
                    ref = str(params.get(parameter, "") or "")
                    if not ref.startswith(EXPORT_AUDIO_PREFIX):
                        continue
                    archive_name = ref.removeprefix(EXPORT_AUDIO_PREFIX)
                    if archive_name in restored_audio_members:
                        params[parameter] = str(
                            restored_audio_members[archive_name]
                        )
                        continue
                    self._validate_audio_member(archive_name)
                    data = self._read_bundle_member(
                        archive, archive_name, MAX_AUDIO_BYTES
                    )
                    total_audio_bytes += len(data)
                    if total_audio_bytes > MAX_TOTAL_AUDIO_BYTES:
                        raise ValueError("The exported audio files are too large")
                    suffix = PurePosixPath(archive_name).suffix.lower()
                    digest = hashlib.sha256(data).hexdigest()
                    target = CONFIG_DIR / "imported-audio" / f"{digest}{suffix}"
                    audio_payloads[target] = data
                    restored_audio_members[archive_name] = target
                    params[parameter] = str(target)

        for target, data in icon_payloads.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            self._write_imported_file(target, data)
        for target, data in audio_payloads.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            self._write_imported_file(target, data)

        replacement.save()
        self.profiles = replacement.profiles
        self.current_profile = replacement.current_profile
        self.obs = replacement.obs
        self.ai = replacement.ai
        self.screensaver = replacement.screensaver
        self.brightness = replacement.brightness
        self.obs_password_needs_migration = (
            replacement.obs_password_needs_migration
        )
        return ImportResult(
            profiles=len(self.profiles),
            pages=sum(len(profile.pages) for profile in self.profiles),
            keys=sum(
                len(page.keys)
                for profile in self.profiles
                for page in profile.pages
            ),
            restored_icons=len(icon_payloads),
            restored_audio=len(audio_payloads),
        )

    def _key_configs(self):
        for profile in self.profiles:
            for page in profile.pages:
                yield from page.keys.values()

    @staticmethod
    def _raw_action_params(key: dict):
        action = key.get("action", "")
        params = key.get("params", {})
        if isinstance(params, dict):
            yield action, params
        for field_name in ("steps", "steps_on", "steps_off"):
            for step in key.get(field_name, []):
                if isinstance(step, dict) and isinstance(step.get("params"), dict):
                    yield step.get("action", ""), step["params"]

    @staticmethod
    def _action_params(key: KeyConfig):
        if isinstance(key.params, dict):
            yield key.action, key.params
        for step in (*key.steps, *key.steps_on, *key.steps_off):
            if isinstance(step.params, dict):
                yield step.action, step.params

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
    def _write_imported_file(target: Path, data: bytes) -> None:
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
    def _write_json_file(path: Path, value: dict) -> None:
        payload = json.dumps(value, indent=2, ensure_ascii=False).encode("utf-8")
        temporary = tempfile.NamedTemporaryFile(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        )
        temporary_path = Path(temporary.name)
        try:
            temporary.write(payload)
            temporary.close()
            os.chmod(temporary_path, 0o600)
            temporary_path.replace(path)
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
    def _validate_audio_member(name: str) -> None:
        path = PurePosixPath(name)
        if (
            path.is_absolute()
            or len(path.parts) != 2
            or path.parts[0] != "audio"
            or any(part in ("", ".", "..") for part in path.parts)
            or path.suffix.lower() not in SUPPORTED_AUDIO_EXTENSIONS
        ):
            raise ValueError("The export contains an invalid audio path")

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
