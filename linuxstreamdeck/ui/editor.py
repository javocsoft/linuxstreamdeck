"""Key editor panel.

Lets you choose the key type:
  - Single action     → one action (with state feedback)
  - Multiple actions  → ordered list of actions run in sequence
  - Toggle (ON/OFF)   → two action lists, one per state

The logic of a single step (category/action/parameters) and the appearance live
in `steps.py`; here they are only composed according to the chosen type.
"""

from __future__ import annotations

import logging
from functools import partial

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ..core import actions as action_registry  # noqa: E402
from ..core.controller import (  # noqa: E402
    GESTURE_DOUBLE,
    GESTURE_LONG,
    GESTURE_SINGLE,
)
from ..core.config import (  # noqa: E402
    DEFAULT_FOLDER_ICON,
    KIND_FOLDER,
    KIND_MULTI,
    KIND_PRESS,
    KIND_RANDOM,
    KIND_SINGLE,
    KIND_TOGGLE,
    ON_ERROR_CHOICES,
    ON_ERROR_CONTINUE,
    STEP_FIELDS,
    ActionStep,
    Folder,
    KeyConfig,
)
from .steps import AppearanceBox, StepEditor, StepList  # noqa: E402

log = logging.getLogger(__name__)

KINDS = [
    (KIND_SINGLE, "Single action"),
    (KIND_MULTI, "Multiple actions"),
    (KIND_TOGGLE, "Toggle (ON/OFF)"),
    (KIND_RANDOM, "Random action"),
    (KIND_PRESS, "Single / double / long press"),
    (KIND_FOLDER, "Folder"),
]
KIND_IDS = [k for k, _ in KINDS]

# The key types that run a list of actions, and can therefore be asked what a
# failure should do to the rest of it.
ERROR_POLICY_KINDS = (KIND_MULTI, KIND_TOGGLE, KIND_RANDOM, KIND_PRESS)

# How long a button whose result is not on the button keeps saying it was
# pressed. Long enough to read a word, short enough that it reads as a pulse
# rather than as a state the button is now stuck in.
PRESS_ECHO_MS = 900


def acknowledge_press(button: Gtk.Button, label: str = "") -> None:
    """Make a button whose result is somewhere else show that it was pressed.

    This panel stays exactly as it was when Save writes the key to disk and to
    the deck, and when Test runs it: the only thing that moved was a line in
    the status bar at the far end of the window. The theme's own `:active`
    state does not fill that gap either, because a quick click is released
    before it is ever painted -- press and release land in the same frame -- so
    the press is acknowledged deliberately here instead.

    `label` replaces the button's text for the same moment. That is only safe
    on a button with room to spare, or the row it sits in shifts under the
    pointer: Save hexpands, so a longer word cannot move anything.
    """
    pending = getattr(button, "_echo_timer", None)
    if pending is not None:
        GLib.source_remove(pending)
    else:
        # Only the first press of a run captures the real text. A second one
        # while the echo is still up would otherwise capture the echo itself
        # and leave the button reading "Saved" for good.
        button._echo_label = button.get_label()
    button.add_css_class("press-echo")
    button.add_css_class("press-echo-on")
    if label:
        button.set_label(label)

    def restore() -> bool:
        button._echo_timer = None
        button.remove_css_class("press-echo-on")
        if label:
            button.set_label(button._echo_label)
        return False

    button._echo_timer = GLib.timeout_add(PRESS_ECHO_MS, restore)


class EditorPanel(Gtk.Box):
    def __init__(self, app) -> None:
        super().__init__(
            orientation=Gtk.Orientation.VERTICAL, spacing=10,
            margin_top=14, margin_bottom=14, margin_start=14, margin_end=14,
        )
        self.app = app
        self.index: int | None = None
        self._container = None
        self._building = False
        self._baseline: KeyConfig | None = None
        # A folder's contents are edited by navigating into it, so the editor
        # only carries them across a save; the key type list itself changes with
        # the nesting depth, hence a per-load copy of the offered ids.
        self._folder: Folder | None = None
        self._kind_ids: list[str] = list(KIND_IDS)

        self.title = Gtk.Label(xalign=0)
        self.title.add_css_class("title-3")
        self.append(self.title)

        self.ai_button = Gtk.Button(label="Create with AI...")
        self.ai_button.set_tooltip_text(
            "Generate a reviewable key proposal with OpenAI or Claude"
        )
        self.ai_button.connect("clicked", self._open_ai_assistant)
        self.append(self.ai_button)

        self.kind_dd = Gtk.DropDown.new_from_strings([name for _, name in KINDS])
        self.kind_row = self._labelled("Key type", self.kind_dd)
        self.append(self.kind_row)
        self.kind_dd.connect("notify::selected", self._on_kind_changed)

        # Sits above the body rather than inside it, so rebuilding the body for
        # a new key type cannot destroy it mid-edit.
        self.twitch_banner = Adw.Banner(
            title="This key needs a Twitch account",
            button_label="Connect",
            revealed=False,
        )
        self.twitch_banner.add_css_class("service-banner")
        self.twitch_banner.connect("button-clicked", self._open_twitch_settings)
        self.append(self.twitch_banner)
        # Linking from anywhere has to take the banner down again.
        self.app.bus.subscribe("twitch.state", self._on_twitch_state)

        # scrollable body: grows to fill the available space and scrolls its
        # content when steps are added, without enlarging the window
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.scroller = Gtk.ScrolledWindow(child=self.body)
        self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroller.set_vexpand(True)
        self.scroller.set_min_content_height(180)
        # An overlay scrollbar is drawn *over* the content instead of taking
        # width of its own, which put it on top of the right-hand end of every
        # row: the arrow of each dropdown and the action picker's search button
        # sat underneath it. That is fine over a document, where the text simply
        # continues behind it, and wrong over a column of controls whose last
        # few pixels are the part you click. Turning it off makes the bar
        # reserve its own width, so nothing is ever covered.
        self.scroller.set_overlay_scrolling(False)
        self.append(self.scroller)

        # buttons pinned at the bottom (always visible even if the body overflows)
        self.buttons = self._build_buttons()
        self.append(self.buttons)

        # sub-widgets by type (filled in _build_body)
        self.single_editor: StepEditor | None = None
        self.multi_list: StepList | None = None
        self.on_list: StepList | None = None
        self.off_list: StepList | None = None
        self.app_main: AppearanceBox | None = None
        self.app_off: AppearanceBox | None = None
        self.single_press_list: StepList | None = None
        self.double_press_list: StepList | None = None
        self.long_press_list: StepList | None = None
        self.on_error_dd: Gtk.DropDown | None = None

        self.clear()

    # ---------- public API ----------

    def clear(self) -> None:
        self.title.set_label("No key selected")
        self.ai_button.set_visible(False)
        self.kind_row.set_visible(False)
        self.buttons.set_visible(False)
        self._clear(self.body)
        info = Gtk.Label(
            label="Select a key from the grid to configure it.",
            wrap=True, xalign=0,
        )
        info.add_css_class("dim-label")
        self.body.append(info)
        self.index = None
        self._container = None
        self._baseline = None
        self._folder = None
        self.twitch_banner.set_revealed(False)

    def load(self, index: int) -> None:
        self.index = index
        self._container = self.app.controller.container
        kc = self._container.key(index) or KeyConfig()
        self.title.set_label(f"Key {index + 1}")
        self.ai_button.set_visible(True)
        self.kind_row.set_visible(True)
        self.buttons.set_visible(True)
        self._building = True
        self._refresh_kind_choices(kc)
        self.kind_dd.set_selected(
            self._kind_ids.index(kc.kind) if kc.kind in self._kind_ids else 0
        )
        self._building = False
        self._build_body(kc)
        self._baseline = self.current_key_config()

    def _refresh_kind_choices(self, kc: KeyConfig) -> None:
        """Offer the key types that are usable where this key lives.

        A folder is dropped from the list once the nesting limit is reached,
        and the whole list is locked while the stored key is a folder that
        already holds keys, so its contents cannot be replaced by accident.
        """
        controller = self.app.controller
        kinds = [
            (kind, name)
            for kind, name in KINDS
            if kind != KIND_FOLDER
            or kc.kind == KIND_FOLDER
            or controller.can_add_folder()
        ]
        self._kind_ids = [kind for kind, _ in kinds]
        self.kind_dd.set_model(Gtk.StringList.new([name for _, name in kinds]))
        self.kind_dd.set_sensitive(not self._holds_folder_keys(kc))

    @staticmethod
    def _holds_folder_keys(kc: KeyConfig) -> bool:
        contents = kc.contents
        return contents is not None and bool(contents.keys)

    def has_unsaved_changes(self) -> bool:
        return (
            self.index is not None
            and self._container is not None
            and self._baseline is not None
            and self.current_key_config() != self._baseline
        )

    def current_key_config(self) -> KeyConfig:
        """Build a key configuration from the editor without saving it."""
        kind = self._current_kind()
        kc = KeyConfig(kind=kind)

        if kind == KIND_SINGLE and self.single_editor is not None:
            step = self.single_editor.get_step()
            kc.action, kc.params = step.action, step.params
        elif kind == KIND_MULTI and self.multi_list is not None:
            kc.steps = self.multi_list.get_steps()
        elif kind == KIND_RANDOM and self.multi_list is not None:
            kc.steps = self.multi_list.get_steps()
        elif kind == KIND_PRESS:
            if self.single_press_list is not None:
                kc.steps_single = self.single_press_list.get_steps()
            if self.double_press_list is not None:
                kc.steps_double = self.double_press_list.get_steps()
            if self.long_press_list is not None:
                kc.steps_long = self.long_press_list.get_steps()
        elif kind == KIND_FOLDER:
            kc.folder = self._folder if self._folder is not None else Folder()
        elif kind == KIND_TOGGLE:
            if self.on_list is not None:
                kc.steps_on = self.on_list.get_steps()
            if self.off_list is not None:
                kc.steps_off = self.off_list.get_steps()
            if self.app_off is not None:
                kc.label_off = self.app_off.label()
                kc.icon_off = self.app_off.icon()
                kc.bg_color_off = self.app_off.color()
                kc.font_size_off = self.app_off.font_size()
                kc.text_color_off = self.app_off.text_color()

        if kind in ERROR_POLICY_KINDS:
            kc.on_error = self._current_on_error()

        if self.app_main is not None:
            kc.label = self.app_main.label()
            kc.icon = self.app_main.icon()
            kc.bg_color = self.app_main.color()
            kc.font_size = self.app_main.font_size()
            kc.text_color = self.app_main.text_color()
        return kc

    def save(self) -> bool:
        """Persist the current editor state. Return whether a key was selected."""
        if self.index is None or self._container is None:
            return False
        kc = self.current_key_config()
        self._container.set_key(self.index, kc)
        self.app.controller.key_config_changed(self.index)
        self.app.config.save()
        self.app.controller.refresh()
        self._baseline = kc.clone()
        name = dict(KINDS)[kc.kind]
        self.app.bus.emit("status", text=f"Key {self.index + 1} saved ({name})")
        return True

    # ---------- body construction ----------

    def _on_kind_changed(self, *_a) -> None:
        if self._building or self.index is None:
            return
        # changing type starts with an empty configuration of that type
        self._build_body(KeyConfig(kind=self._current_kind()))

    def _current_kind(self) -> str:
        i = self.kind_dd.get_selected()
        if i == Gtk.INVALID_LIST_POSITION or i >= len(self._kind_ids):
            return KIND_SINGLE
        return self._kind_ids[i]

    def _open_ai_assistant(self, _button) -> None:
        if self.index is None:
            return
        from .ai_assistant import AIKeyDialog

        AIKeyDialog(
            self.get_root(),
            self.app,
            self._load_ai_proposal,
        ).present()

    def _open_folder(self, _button) -> None:
        """Save first if needed, then go inside: the key must exist to open."""
        if self.index is None:
            return
        window = self.get_root()
        if window is not None and hasattr(window, "open_folder"):
            window.open_folder(self.index)

    def _load_ai_proposal(self, key: KeyConfig) -> None:
        if self.index is None or key.kind not in self._kind_ids:
            return
        self._building = True
        self.kind_dd.set_selected(self._kind_ids.index(key.kind))
        self._building = False
        self._build_body(key)
        self.app.bus.emit(
            "status",
            text="AI proposal loaded; review it and press Save to keep it",
        )

    def _build_body(self, kc: KeyConfig) -> None:
        self._clear(self.body)
        self.single_editor = self.multi_list = None
        self.on_list = self.off_list = self.app_main = self.app_off = None
        self.single_press_list = self.double_press_list = None
        self.long_press_list = None
        self.on_error_dd = None
        kind = self._current_kind()
        self._show_gesture_tests(kind == KIND_PRESS)
        self._folder = (
            (kc.folder if kc.folder is not None else Folder())
            if kind == KIND_FOLDER
            else None
        )

        if kind == KIND_FOLDER:
            inside = len(self._folder.keys)
            self.body.append(self._hint(
                "A folder holds its own grid of keys, so related actions can "
                "be grouped without spending a page. Double-click it on the "
                "deck grid to go inside; the first key in a folder is always "
                "Back."
            ))
            self.body.append(self._hint(
                f"It currently holds {inside} key(s)." if inside
                else "It is empty: open it and configure its keys."
            ))
            open_button = Gtk.Button(label="Open folder")
            open_button.connect("clicked", self._open_folder)
            self.body.append(open_button)
            if self._holds_folder_keys(kc):
                self.body.append(self._hint(
                    "Clear this key first to change its type; its contents "
                    "would be lost."
                ))
            self.body.append(Gtk.Separator())
            self.app_main = AppearanceBox("Appearance")
            self.app_main.load(
                kc.label,
                kc.icon,
                kc.bg_color,
                DEFAULT_FOLDER_ICON,
                kc.font_size,
                kc.text_color,
            )
            self.body.append(self.app_main)
            self.body.append(self._hint(
                "The label is the folder name."
            ))

        elif kind == KIND_SINGLE:
            self.single_editor = StepEditor(
                self.app,
                on_change=partial(
                    self._on_step_changed, self._update_single_icon_preview
                ),
            )
            self.single_editor.load(ActionStep(action=kc.action, params=kc.params))
            # No list row to right-click, so the editor itself carries the menu.
            self.single_editor.enable_context_menu()
            self.body.append(self.single_editor)
            self.body.append(self._hint(
                "Right-click it to copy this action, or to paste one copied "
                "from another key."
            ))
            self.body.append(Gtk.Separator())
            self.app_main = AppearanceBox("Appearance")
            self.app_main.load(
                kc.label,
                kc.icon,
                kc.bg_color,
                self._action_icon(kc.action),
                kc.font_size,
                kc.text_color,
            )
            self.body.append(self.app_main)

        elif kind == KIND_MULTI:
            self.body.append(self._hint(
                "They run in order when pressed. Add a «Wait» action for "
                "pauses. Right-click an action to copy it, or the list to "
                "paste one at the end."
            ))
            self.multi_list = StepList(
                self.app,
                on_change=partial(
                    self._on_step_changed, self._update_multi_icon_preview
                ),
            )
            self.multi_list.load(kc.steps)
            self.body.append(self.multi_list)
            self.body.append(Gtk.Separator())
            self.app_main = AppearanceBox("Appearance")
            self.app_main.load(
                kc.label,
                kc.icon,
                kc.bg_color,
                self._steps_icon(kc.steps, "mdi:playlist-play"),
                kc.font_size,
                kc.text_color,
            )
            self.body.append(self.app_main)

        elif kind == KIND_RANDOM:
            self.body.append(self._hint(
                "One of these actions is chosen at random on every press."
            ))
            self.multi_list = StepList(
                self.app,
                on_change=partial(
                    self._on_step_changed, self._update_multi_icon_preview
                ),
            )
            self.multi_list.load(kc.steps)
            self.body.append(self.multi_list)
            self.body.append(Gtk.Separator())
            self.app_main = AppearanceBox("Appearance")
            self.app_main.load(
                kc.label,
                kc.icon,
                kc.bg_color,
                self._steps_icon(kc.steps, "mdi:shuffle-variant"),
                kc.font_size,
                kc.text_color,
            )
            self.body.append(self.app_main)

        elif kind == KIND_PRESS:
            self.body.append(self._hint(
                "Each gesture runs its own list. Leave one empty to ignore it. "
                "Only the physical deck can tell the gestures apart, so a click "
                "on the on-screen deck is a single press; use the •• and — "
                "buttons below to test the other two."
            ))
            self.single_press_list = StepList(
                self.app,
                on_change=partial(
                    self._on_step_changed, self._update_press_icon_preview
                ),
            )
            self.single_press_list.load(kc.steps_single)
            self.body.append(self._frame("• Single press", [self.single_press_list]))

            self.double_press_list = StepList(
                self.app,
                on_change=partial(
                    self._on_step_changed, self._update_press_icon_preview
                ),
            )
            self.double_press_list.load(kc.steps_double)
            self.body.append(self._frame("•• Double press", [self.double_press_list]))

            self.long_press_list = StepList(
                self.app,
                on_change=partial(
                    self._on_step_changed, self._update_press_icon_preview
                ),
            )
            self.long_press_list.load(kc.steps_long)
            self.body.append(self._frame("— Long press", [self.long_press_list]))

            self.body.append(Gtk.Separator())
            self.app_main = AppearanceBox("Appearance")
            self.app_main.load(
                kc.label,
                kc.icon,
                kc.bg_color,
                self._steps_icon(
                    [*kc.steps_single, *kc.steps_double, *kc.steps_long],
                    "mdi:gesture-tap",
                ),
                kc.font_size,
                kc.text_color,
            )
            self.body.append(self.app_main)

        else:  # KIND_TOGGLE
            self.body.append(self._hint(
                "Each press toggles the state and runs its action list."
            ))
            self.on_list = StepList(
                self.app,
                on_change=partial(
                    self._on_step_changed, self._update_on_icon_preview
                ),
            )
            self.on_list.load(kc.steps_on)
            self.app_main = AppearanceBox("ON state appearance")
            self.app_main.load(
                kc.label,
                kc.icon,
                kc.bg_color,
                self._steps_icon(kc.steps_on, "mdi:toggle-switch"),
                kc.font_size,
                kc.text_color,
            )
            self.body.append(self._frame("▶ ON state", [self.on_list, self.app_main]))

            self.off_list = StepList(
                self.app,
                on_change=partial(
                    self._on_step_changed, self._update_off_icon_preview
                ),
            )
            self.off_list.load(kc.steps_off)
            self.app_off = AppearanceBox("OFF state appearance")
            self.app_off.load(
                kc.label_off,
                kc.icon_off,
                kc.bg_color_off,
                kc.icon
                or self._steps_icon(
                    kc.steps_off,
                    "mdi:toggle-switch-off-outline",
                ),
                kc.font_size_off,
                kc.text_color_off,
            )
            self.body.append(self._frame("■ OFF state", [self.off_list, self.app_off]))

        if kind in ERROR_POLICY_KINDS:
            self._append_error_policy(kc)

        # The one place every rebuild passes through — selecting a key,
        # switching key type and loading an AI proposal all land here — so the
        # banner cannot be left showing for a key that no longer needs it.
        self._refresh_twitch_banner()

    def _append_error_policy(self, kc: KeyConfig) -> None:
        """What a failing action does to the rest of the key.

        Only a list can answer it: a single action has nothing left to abandon,
        and a folder runs nothing at all. Carrying on stays the default, so no
        existing key changes behaviour by being opened and saved.
        """
        self.on_error_dd = Gtk.DropDown.new_from_strings(
            [name for _, name in ON_ERROR_CHOICES]
        )
        values = [value for value, _ in ON_ERROR_CHOICES]
        self.on_error_dd.set_selected(
            values.index(kc.on_error) if kc.on_error in values else 0
        )
        self.body.append(
            self._labelled("If an action fails", self.on_error_dd)
        )

    def _current_on_error(self) -> str:
        if self.on_error_dd is None:
            return ON_ERROR_CONTINUE
        index = self.on_error_dd.get_selected()
        values = [value for value, _ in ON_ERROR_CHOICES]
        if index == Gtk.INVALID_LIST_POSITION or index >= len(values):
            return ON_ERROR_CONTINUE
        return values[index]

    @staticmethod
    def _action_icon(action_id: str) -> str:
        action = action_registry.get(action_id)
        return action.default_icon if action is not None else ""

    @classmethod
    def _steps_icon(cls, steps: list[ActionStep], fallback: str) -> str:
        for step in steps:
            if icon := cls._action_icon(step.action):
                return icon
        return fallback if steps else ""

    # ---------- offering the connection a key needs ----------

    def _on_twitch_state(self, _topic, _data) -> None:
        from gi.repository import GLib

        GLib.idle_add(self._refresh_twitch_banner)

    def _on_step_changed(self, update_preview) -> None:
        """Everything that reacts to a step being edited, in one place.

        The icon preview and the banner both follow the chosen action, so they
        are refreshed together rather than each hooking the change separately.
        """
        update_preview()
        self._refresh_twitch_banner()

    def _refresh_twitch_banner(self) -> bool:
        """Offer to connect Twitch while editing a key that needs it.

        Without this the only way in is a dialog in the profile menu, which is
        the last place someone configuring a key would look: the key renders
        faded on the deck, does nothing when pressed, and nothing anywhere says
        what is missing. The banner appears exactly where the choice was made.
        """
        twitch = getattr(self.app, "twitch", None)
        linked = bool(twitch is not None and twitch.linked)
        wanted = (
            self.index is not None
            and not linked
            and self._draft_needs_twitch()
        )
        self.twitch_banner.set_revealed(wanted)
        return False

    def _draft_needs_twitch(self) -> bool:
        """Whether the key as it currently stands would need an account.

        Asked of the draft rather than of the stored key, so picking a Twitch
        action offers the connection immediately instead of after a save. Any
        one action is enough: a key that is half local still cannot do that
        half.
        """
        try:
            kc = self.current_key_config()
        except Exception:
            log.debug("Could not inspect the draft for Twitch actions", exc_info=True)
            return False
        pairs = [(kc.action, kc.params)] if kc.kind == KIND_SINGLE else [
            (step.action, step.params)
            for field in STEP_FIELDS
            for step in getattr(kc, field, [])
        ]
        for action_id, params in pairs:
            if not action_id:
                continue
            action = action_registry.get(action_id)
            if action is not None and action.requires_twitch(params or {}):
                return True
        return False

    def _open_twitch_settings(self, _banner) -> None:
        from .twitch_settings import TwitchSettingsDialog

        window = self.get_root()
        if window is not None:
            TwitchSettingsDialog(window, self.app).present()

    def _update_single_icon_preview(self) -> None:
        if self.single_editor is not None and self.app_main is not None:
            self.app_main.set_fallback_icon(
                self._action_icon(self.single_editor.get_step().action)
            )

    def _update_multi_icon_preview(self) -> None:
        if self.multi_list is not None and self.app_main is not None:
            self.app_main.set_fallback_icon(
                self._steps_icon(
                    self.multi_list.get_steps(),
                    "mdi:playlist-play",
                )
            )

    def _update_press_icon_preview(self) -> None:
        if self.app_main is None:
            return
        steps: list[ActionStep] = []
        for step_list in (
            self.single_press_list,
            self.double_press_list,
            self.long_press_list,
        ):
            if step_list is not None:
                steps.extend(step_list.get_steps())
        self.app_main.set_fallback_icon(
            self._steps_icon(steps, "mdi:gesture-tap")
        )

    def _update_on_icon_preview(self) -> None:
        if self.on_list is not None and self.app_main is not None:
            self.app_main.set_fallback_icon(
                self._steps_icon(
                    self.on_list.get_steps(),
                    "mdi:toggle-switch",
                )
            )

    def _update_off_icon_preview(self) -> None:
        if self.off_list is not None and self.app_off is not None:
            self.app_off.set_fallback_icon(
                (self.app_main.icon() if self.app_main is not None else "")
                or self._steps_icon(
                    self.off_list.get_steps(),
                    "mdi:toggle-switch-off-outline",
                )
            )

    def _build_buttons(self) -> Gtk.Box:
        btns = Gtk.Box(spacing=6, margin_top=10)
        save = Gtk.Button(label="Save", hexpand=True)
        save.add_css_class("suggested-action")
        save.connect("clicked", self._save)
        test = Gtk.Button(label="Test")
        test.set_tooltip_text("Run this key now")
        test.connect("clicked", self._test, GESTURE_SINGLE)
        # A gesture key can only be half-tested from a click, which has no
        # release to time. These name the gesture instead, so the whole key is
        # testable without the physical deck.
        self.test_double = Gtk.Button(label="••")
        self.test_double.set_tooltip_text("Run the double-press list")
        self.test_double.connect("clicked", self._test, GESTURE_DOUBLE)
        self.test_long = Gtk.Button(label="—")
        self.test_long.set_tooltip_text("Run the long-press list")
        self.test_long.connect("clicked", self._test, GESTURE_LONG)
        wipe = Gtk.Button(label="Clear")
        wipe.add_css_class("destructive-action")
        wipe.connect("clicked", self._wipe)
        for b in (save, test, self.test_double, self.test_long, wipe):
            btns.append(b)
        return btns

    def _test(self, button: Gtk.Button, gesture: str) -> None:
        if self.index is None:
            return
        self.app.controller.press(self.index, gesture)
        # Deliberately no word here, unlike Save. The press is what landed; a
        # key that fails says so with its own red mark and a status message,
        # and a button claiming otherwise would be quicker and louder.
        acknowledge_press(button)

    def _show_gesture_tests(self, visible: bool) -> None:
        self.test_double.set_visible(visible)
        self.test_long.set_visible(visible)

    # ---------- save / clear ----------

    def _save(self, button) -> None:
        # Only a save that happened says so: with no key selected there is
        # nothing to confirm, and the button must not claim otherwise.
        if self.save():
            acknowledge_press(button, "Saved")

    def _wipe(self, _btn) -> None:
        if self.index is None or self._container is None:
            return
        window = self.get_root()
        if window is not None and hasattr(window, "clear_key"):
            # The window owns the confirmations (unsaved edits, and discarding
            # a folder's contents) and reloads this panel afterwards.
            window.clear_key(self.index)
            return
        self._container.set_key(self.index, None)
        self.app.config.save()
        self.app.controller.refresh()
        self.load(self.index)

    # ---------- layout helpers ----------

    @staticmethod
    def _labelled(label: str, widget: Gtk.Widget) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        lbl = Gtk.Label(label=label, xalign=0)
        lbl.add_css_class("caption-heading")
        box.append(lbl)
        box.append(widget)
        return box

    @staticmethod
    def _hint(text: str) -> Gtk.Label:
        lbl = Gtk.Label(label=text, wrap=True, xalign=0)
        lbl.add_css_class("dim-label")
        return lbl

    @staticmethod
    def _frame(title: str, children: list[Gtk.Widget]) -> Gtk.Frame:
        frame = Gtk.Frame(label=title)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                      margin_top=8, margin_bottom=8, margin_start=8, margin_end=8)
        for i, child in enumerate(children):
            if i:
                box.append(Gtk.Separator())
            box.append(child)
        frame.set_child(box)
        return frame

    @staticmethod
    def _clear(box: Gtk.Box) -> None:
        while (child := box.get_first_child()) is not None:
            box.remove(child)
