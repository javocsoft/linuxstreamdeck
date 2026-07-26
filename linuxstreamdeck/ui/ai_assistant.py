"""AI-assisted key configuration dialog."""

from __future__ import annotations

import logging
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ..ai.constants import (  # noqa: E402
    PROVIDER_LABELS,
    PROVIDERS,
)
from ..ai.service import (  # noqa: E402
    AIProposal,
    collect_generation_context,
    format_proposal,
)

log = logging.getLogger(__name__)

SAVED_API_KEY_MASK = "************"
# The configuration folds away once it is usable, so the description field can
# open at a size worth typing into instead of being cut off by the fold.
PROMPT_INITIAL_HEIGHT = 420
PROMPT_MIN_HEIGHT = 180
PROMPT_MAX_HEIGHT = 700


class AIKeyDialog(Adw.Window):
    """Generate a proposal, preview it, then load it into the key editor."""

    def __init__(self, parent, app, on_apply) -> None:
        super().__init__(
            transient_for=parent,
            modal=True,
            title="Create key with AI",
            default_width=580,
            default_height=820,
        )
        self.app = app
        self._on_apply = on_apply
        self._proposal: AIProposal | None = None
        self._closed = False
        self._stored_key = ""
        self._showing_saved_key = False
        self._key_lookup_id = 0
        self._generation_id = 0
        # Whether the opening state has been settled yet. It is decided once,
        # from what is stored, and never again: folding the settings away while
        # the user is looking at them is exactly what makes a dialog feel like
        # it moved under the cursor.
        self._settings_settled = False
        self._current_provider = app.config.ai.provider
        self._models = {
            "openai": app.config.ai.openai_model,
            "anthropic": app.config.ai.anthropic_model,
        }

        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.stack.add_named(self._build_request_page(), "request")
        self.stack.add_named(self._build_preview_page(), "preview")
        view.set_content(self.stack)

        # The action of each page is pinned here rather than left at the end of
        # the scrolled content, where the button that does the dialog's whole
        # job sat below the fold on every open.
        self.action_stack = Gtk.Stack()
        self.action_stack.add_named(self._build_request_actions(), "request")
        self.action_stack.add_named(self._build_preview_actions(), "preview")
        view.add_bottom_bar(self.action_stack)
        self.set_content(view)

        self.connect("close-request", self._on_close_request)
        self._load_api_key()

    def _show_page(self, name: str) -> None:
        """Move both the content and its pinned action bar together."""
        self.stack.set_visible_child_name(name)
        self.action_stack.set_visible_child_name(name)

    def _build_request_page(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage()

        # One row standing in for the whole configuration. Its title and
        # subtitle carry the complete state, so nothing has to be expanded to
        # know what will be used: a bare "Settings" row is what would leave
        # someone wondering where everything went.
        settings_group = Adw.PreferencesGroup()
        self.settings = Adw.ExpanderRow(title="Provider", subtitle="")
        self.settings.set_expanded(False)
        settings_group.add(self.settings)

        self.provider = Adw.ComboRow(title="AI provider")
        self.provider.set_model(
            Gtk.StringList.new([PROVIDER_LABELS[item] for item in PROVIDERS])
        )
        try:
            selected = PROVIDERS.index(self._current_provider)
        except ValueError:
            selected = 0
            self._current_provider = PROVIDERS[0]
        self.provider.set_selected(selected)
        self.provider.connect("notify::selected", self._on_provider_changed)

        self.model = Adw.EntryRow(
            title="Model",
            text=self._models[self._current_provider],
        )
        self.model.connect("changed", lambda *_a: self._refresh_summary())
        self.api_key = Adw.PasswordEntryRow(title="API key")
        self.api_key.connect("changed", lambda *_a: self._refresh_summary())
        self.settings.add_row(self.provider)
        self.settings.add_row(self.model)
        self.settings.add_row(self.api_key)

        self.key_status = Gtk.Label(wrap=True, xalign=0)
        self.key_status.add_css_class("dim-label")
        self.forget_key = Gtk.Button(
            label="Forget saved API key",
            halign=Gtk.Align.START,
        )
        self.forget_key.set_sensitive(False)
        self.forget_key.connect("clicked", self._forget_api_key)
        self.replace_key = Gtk.Button(
            label="Replace saved API key",
            halign=Gtk.Align.START,
        )
        self.replace_key.set_visible(False)
        self.replace_key.connect("clicked", self._replace_api_key)
        key_actions = Gtk.Box(spacing=8)
        key_actions.append(self.replace_key)
        key_actions.append(self.forget_key)

        keyring_note = Gtk.Label(
            label=(
                "The API key is stored in your desktop keyring and is never "
                "exported."
            ),
            wrap=True,
            xalign=0,
        )
        keyring_note.add_css_class("dim-label")
        key_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=8,
            margin_top=10,
            margin_bottom=10,
            margin_start=12,
            margin_end=12,
        )
        key_box.append(keyring_note)
        key_box.append(self.key_status)
        key_box.append(key_actions)
        self.settings.add_row(key_box)

        self.include_context = Adw.SwitchRow(
            title="Include OBS and page names",
            subtitle=(
                "Only OBS and page names are sent. Passwords, commands and the "
                "full configuration are never included."
            ),
        )
        self.include_context.set_active(
            self.app.config.ai.include_obs_context
        )
        self.include_context.connect(
            "notify::active", lambda *_a: self._refresh_summary()
        )
        self.settings.add_row(self.include_context)
        page.add(settings_group)

        prompt_group = Adw.PreferencesGroup(
            title="Describe the key",
            description=(
                "The result is only a proposal. It will not run or save anything."
            ),
        )
        self.prompt = Gtk.TextView(
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
            top_margin=10,
            bottom_margin=10,
            left_margin=10,
            right_margin=10,
        )
        self.prompt_scroller = Gtk.ScrolledWindow(child=self.prompt)
        self.prompt_scroller.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC
        )
        self._prompt_height = PROMPT_INITIAL_HEIGHT
        self._prompt_drag_height = PROMPT_INITIAL_HEIGHT
        self.prompt_scroller.set_size_request(-1, self._prompt_height)
        self.prompt_scroller.add_css_class("card")

        self.prompt_container = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=2,
        )
        self.prompt_resize_handle = self._build_prompt_resize_handle()
        self.prompt_container.append(self.prompt_scroller)
        self.prompt_container.append(self.prompt_resize_handle)
        prompt_group.add(self.prompt_container)
        page.add(prompt_group)
        return page

    def _build_request_actions(self) -> Gtk.Widget:
        box = Gtk.Box(
            spacing=10,
            margin_top=6,
            margin_bottom=6,
            margin_start=12,
            margin_end=12,
        )
        self.spinner = Gtk.Spinner()
        self.status = Gtk.Label(wrap=True, xalign=0, hexpand=True)
        self.status.add_css_class("dim-label")
        box.append(self.spinner)
        box.append(self.status)
        self.generate = Gtk.Button(label="Generate proposal")
        self.generate.add_css_class("suggested-action")
        self.generate.connect("clicked", self._generate)
        box.append(self.generate)
        return box

    def _build_preview_actions(self) -> Gtk.Widget:
        box = Gtk.Box(
            spacing=8,
            homogeneous=True,
            margin_top=6,
            margin_bottom=6,
            margin_start=12,
            margin_end=12,
        )
        back = Gtk.Button(label="Back")
        back.connect("clicked", lambda _button: self._show_page("request"))
        apply_button = Gtk.Button(label="Load into editor")
        apply_button.add_css_class("suggested-action")
        apply_button.connect("clicked", self._apply)
        box.append(back)
        box.append(apply_button)
        return box

    # ---------- opening state ----------

    def _configuration_ready(self) -> bool:
        """Whether this dialog could generate right now without any setup."""
        key = (
            self._stored_key
            if self._showing_saved_key
            else self.api_key.get_text().strip()
        )
        return bool(key) and bool(self.model.get_text().strip())

    def _key_summary(self) -> str:
        if self._showing_saved_key and self._stored_key:
            return "API key saved"
        if self.api_key.get_text().strip():
            return "API key entered"
        return "API key required"

    def _refresh_summary(self) -> None:
        """Restate the whole configuration on the folded row."""
        provider = PROVIDER_LABELS[self._selected_provider()]
        model = self.model.get_text().strip()
        self.settings.set_title(f"{provider} · {model}" if model else provider)
        self.settings.set_subtitle(
            " · ".join(
                (
                    self._key_summary(),
                    "OBS context on"
                    if self.include_context.get_active()
                    else "OBS context off",
                    "billed by your provider",
                )
            )
        )

    def _settle_opening_state(self) -> None:
        """Open on the task when it is set up, on the setup when it is not.

        Decided once per dialog, as soon as the stored key is known.
        """
        if self._settings_settled:
            return
        self._settings_settled = True
        ready = self._configuration_ready()
        self.settings.set_expanded(not ready)
        self._focus_later(self.prompt if ready else self.api_key)

    @staticmethod
    def _focus_later(widget: Gtk.Widget) -> None:
        def take_focus() -> bool:
            widget.grab_focus()
            # Never the bare method: grab_focus answers True when it succeeds,
            # and a source that returns True is kept and re-run forever.
            return False

        GLib.idle_add(take_focus)

    def _build_prompt_resize_handle(self) -> Gtk.DrawingArea:
        handle = Gtk.DrawingArea(
            content_height=18,
            hexpand=True,
        )
        handle.set_cursor_from_name("ns-resize")
        handle.set_tooltip_text("Drag to resize the description field")

        def draw(widget, context, width, height) -> None:
            color = widget.get_color()
            context.set_source_rgba(
                color.red,
                color.green,
                color.blue,
                color.alpha * 0.55,
            )
            x = width / 2 - 18
            y = height / 2
            for offset in (-3, 0, 3):
                context.rectangle(x, y + offset, 36, 1)
            context.fill()

        handle.set_draw_func(draw)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._prompt_resize_begin)
        drag.connect("drag-update", self._prompt_resize_update)
        handle.add_controller(drag)
        return handle

    def _prompt_resize_begin(self, _gesture, _x: float, _y: float) -> None:
        self._prompt_drag_height = self._prompt_height

    def _prompt_resize_update(
        self,
        _gesture,
        _offset_x: float,
        offset_y: float,
    ) -> None:
        height = max(
            PROMPT_MIN_HEIGHT,
            min(
                PROMPT_MAX_HEIGHT,
                round(self._prompt_drag_height + offset_y),
            ),
        )
        if height == self._prompt_height:
            return
        self._prompt_height = height
        self.prompt_scroller.set_size_request(-1, height)

    def _build_preview_page(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(
            title="Review the proposal",
            description=(
                "Loading it only fills the editor. Press Save in the editor "
                "after checking every action and parameter."
            ),
        )
        self.preview = Gtk.TextView(
            editable=False,
            cursor_visible=False,
            monospace=True,
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
            top_margin=10,
            bottom_margin=10,
            left_margin=10,
            right_margin=10,
        )
        preview_scroller = Gtk.ScrolledWindow(child=self.preview)
        preview_scroller.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC
        )
        preview_scroller.set_min_content_height(380)
        preview_scroller.set_vexpand(True)
        preview_scroller.add_css_class("card")
        group.add(preview_scroller)
        page.add(group)
        return page

    def _selected_provider(self) -> str:
        index = self.provider.get_selected()
        if 0 <= index < len(PROVIDERS):
            return PROVIDERS[index]
        return PROVIDERS[0]

    def _on_provider_changed(self, *_args) -> None:
        self._models[self._current_provider] = self.model.get_text().strip()
        self._current_provider = self._selected_provider()
        self.model.set_text(self._models[self._current_provider])
        self._clear_api_key_field()
        self._stored_key = ""
        self._load_api_key()

    def _load_api_key(self) -> None:
        provider = self._selected_provider()
        self._key_lookup_id += 1
        lookup_id = self._key_lookup_id
        self.key_status.set_label("Checking secure storage...")
        self._update_key_controls()
        self.app.ai_keys.lookup(
            provider,
            lambda key, error: self._api_key_loaded(
                lookup_id, provider, key, error
            ),
        )

    def _api_key_loaded(
        self,
        lookup_id: int,
        provider: str,
        api_key: str,
        error: Exception | None,
    ) -> None:
        if (
            lookup_id != self._key_lookup_id
            or provider != self._selected_provider()
            or self._closed
        ):
            return
        if error is not None:
            self._stored_key = ""
            self._clear_api_key_field()
            self.key_status.set_label(
                "Secure storage is unavailable. A newly entered key will be "
                "used only for this dialog."
            )
        elif api_key:
            self._show_saved_api_key(api_key)
            self.key_status.set_label(
                "The masked field shows that an API key is stored securely."
            )
        else:
            self._stored_key = ""
            self._clear_api_key_field()
            self.key_status.set_label(
                "Enter an API key. It will be saved securely when you generate."
            )
        self._update_key_controls()
        self._settle_opening_state()

    def _show_saved_api_key(self, api_key: str) -> None:
        self._stored_key = api_key
        self._showing_saved_key = True
        self.api_key.set_text(SAVED_API_KEY_MASK)
        self.api_key.set_editable(False)

    def _clear_api_key_field(self) -> None:
        self._showing_saved_key = False
        self.api_key.set_editable(True)
        self.api_key.set_text("")

    def _replace_api_key(self, _button) -> None:
        if not self._stored_key:
            return
        if self._showing_saved_key:
            self._clear_api_key_field()
            self.key_status.set_label(
                "Enter a replacement key, or use the saved key again."
            )
            self.api_key.grab_focus()
        else:
            self._show_saved_api_key(self._stored_key)
            self.key_status.set_label(
                "The masked field shows that an API key is stored securely."
            )
        self._update_key_controls()

    def _update_key_controls(self) -> None:
        has_stored_key = bool(self._stored_key)
        self.replace_key.set_visible(has_stored_key)
        self.replace_key.set_label(
            "Use saved API key"
            if has_stored_key and not self._showing_saved_key
            else "Replace saved API key"
        )
        self.replace_key.set_sensitive(
            self.generate.get_sensitive() and has_stored_key
        )
        self.forget_key.set_sensitive(
            self.generate.get_sensitive() and has_stored_key
        )
        self._refresh_summary()

    def _forget_api_key(self, _button) -> None:
        provider = self._selected_provider()
        self.replace_key.set_sensitive(False)
        self.forget_key.set_sensitive(False)
        self.key_status.set_label("Removing the saved API key...")
        self.app.ai_keys.store(
            provider,
            "",
            lambda cleared, error: self._api_key_forgotten(
                provider, cleared, error
            ),
        )

    def _api_key_forgotten(
        self,
        provider: str,
        cleared: bool,
        error: Exception | None,
    ) -> None:
        if provider != self._selected_provider() or self._closed:
            return
        if cleared:
            self._stored_key = ""
            self._clear_api_key_field()
            self.key_status.set_label("The saved API key was removed.")
            self._update_key_controls()
            return
        self.key_status.set_label(
            f"Could not remove the saved API key: {error or 'unknown error'}"
        )
        self._update_key_controls()

    def _generate(self, _button) -> None:
        prompt = self._prompt_text().strip()
        if not prompt:
            self._show_status("Describe the key you want to create")
            self.prompt.grab_focus()
            return
        provider = self._selected_provider()
        model = self.model.get_text().strip()
        if self._showing_saved_key:
            entered_key = ""
            api_key = self._stored_key
        else:
            entered_key = self.api_key.get_text().strip()
            api_key = entered_key
        if not api_key:
            # The field being complained about lives behind the folded row, so
            # the complaint has to open it.
            self.settings.set_expanded(True)
            self._show_status(
                f"Enter an API key for {PROVIDER_LABELS[provider]}"
            )
            self.api_key.grab_focus()
            return

        self._models[provider] = model
        cfg = self.app.config.ai
        cfg.provider = provider
        cfg.openai_model = self._models["openai"]
        cfg.anthropic_model = self._models["anthropic"]
        cfg.include_obs_context = self.include_context.get_active()
        try:
            self.app.config.save()
        except Exception:
            log.exception("Could not save AI assistant settings")

        self._set_generating(True)
        if entered_key:
            self.app.ai_keys.store(
                provider,
                entered_key,
                lambda stored, error: self._api_key_stored_for_generation(
                    provider,
                    entered_key,
                    prompt,
                    model,
                    stored,
                    error,
                ),
            )
        else:
            self._start_generation(provider, model, api_key, prompt)

    def _api_key_stored_for_generation(
        self,
        provider: str,
        api_key: str,
        prompt: str,
        model: str,
        stored: bool,
        error: Exception | None,
    ) -> None:
        if self._closed:
            return
        if stored:
            self._show_saved_api_key(api_key)
            self.key_status.set_label("The API key is stored securely.")
            self._update_key_controls()
        else:
            self.key_status.set_label(
                "Secure storage is unavailable. The key is being used only "
                "for this request."
            )
            if error is not None:
                log.warning("Could not store an AI API key: %s", error)
        self._start_generation(provider, model, api_key, prompt)

    def _start_generation(
        self, provider: str, model: str, api_key: str, prompt: str
    ) -> None:
        self._generation_id += 1
        generation_id = self._generation_id
        include_context = self.include_context.get_active()
        self._show_status("Contacting the AI provider...")

        def worker() -> None:
            try:
                context = (
                    collect_generation_context(self.app.config, self.app.obs)
                    if include_context
                    else {}
                )
                proposal = self.app.ai.generate(
                    provider=provider,
                    model=model,
                    api_key=api_key,
                    prompt=prompt,
                    context=context,
                )
                error = None
            except Exception as caught:
                proposal = None
                error = caught
            GLib.idle_add(
                self._generation_finished,
                generation_id,
                proposal,
                error,
            )

        threading.Thread(
            target=worker,
            name="ai-key-generator",
            daemon=True,
        ).start()

    def _generation_finished(
        self,
        generation_id: int,
        proposal: AIProposal | None,
        error: Exception | None,
    ) -> bool:
        if generation_id != self._generation_id or self._closed:
            return False
        self._set_generating(False)
        if error is not None:
            log.warning("AI key generation failed: %s", error)
            self._show_status(str(error) or "AI key generation failed")
            return False
        if proposal is None:
            self._show_status("The AI provider returned no proposal")
            return False
        self._proposal = proposal
        self.preview.get_buffer().set_text(format_proposal(proposal))
        self._show_page("preview")
        return False

    def _set_generating(self, generating: bool) -> None:
        self.generate.set_sensitive(not generating)
        self.provider.set_sensitive(not generating)
        self.model.set_sensitive(not generating)
        self.api_key.set_sensitive(not generating)
        self.include_context.set_sensitive(not generating)
        self.spinner.set_spinning(generating)
        self._update_key_controls()

    def _show_status(self, text: str) -> None:
        self.status.set_label(text)

    def _prompt_text(self) -> str:
        buffer = self.prompt.get_buffer()
        start, end = buffer.get_bounds()
        return buffer.get_text(start, end, False)

    def _apply(self, _button) -> None:
        if self._proposal is None:
            return
        proposal = self._proposal
        self.close()
        self._on_apply(proposal.key.clone())

    def _on_close_request(self, *_args) -> bool:
        self._closed = True
        self._generation_id += 1
        self._key_lookup_id += 1
        return False
