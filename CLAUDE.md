# CLAUDE.md

Guidance for **Claude Code** when working in this repository.

The full project guide — architecture, build/run commands, conventions and the
critical gotchas — lives in **AGENTS.md**. It is imported below and is the source
of truth; everything here is Claude-specific and additive.

@AGENTS.md

---

## Claude-specific operating notes

### Golden rules (from AGENTS.md — do not violate)

- **Never overwrite the real config.** Any script that reaches `Config.save()`
  must run with `LSD_CONFIG_DIR` pointed at a temp dir, set **before** importing
  `linuxstreamdeck.core.config`. See AGENTS.md §6.
- **Never launch the GUI yourself** (foreground or background) to "show" the user
  a change. It is single-instance; your launch leaves zombies and the user sees a
  stale, cached window. Verify by compiling and by rendering key PNGs offscreen;
  when a live check is needed, ask the user to close any window and run `./run.sh`.
  See AGENTS.md §5.
- **English only, no accented characters** in any versioned file. See AGENTS.md §4.
- Keep the **BASIC** Pillow layout engine, the shared **`RENDER_LOCK`**, and the
  full-`send` **OBS `_lock`** intact — removing any of them reintroduces
  intermittent blank-icon / hang bugs. See AGENTS.md §5.
- Treat AI output as untrusted proposal data. Keep provider keys in Secret
  Service, context opt-in and bounded, dangerous actions excluded, and the final
  save under explicit user control. See AGENTS.md §3 and §5.
- Keep action execution separate from rendering so long waits or audio playback
  cannot starve running-key feedback, including the controller's teardown order.
  See AGENTS.md §3 and §5.
- Keep restartable action cancellation scoped by profile/page/key. A replacement
  must wait for its predecessor to release resources, and canceled queued runs
  must not execute. Use the cancellation-aware `ActionContext` methods for
  blocking work. See AGENTS.md §3 and §5.
- Keep physical startup exclusive: render it offscreen under `RENDER_LOCK`, never
  exceed configured brightness, and do not assign the deck, register presses or
  publish `deck.connected` until it finishes or is safely skipped. Shutdown must
  cancel it; cancellation during provisional startup must still apply the
  configured clean-exit display before closing HID. See AGENTS.md §3 and §5.
- Preserve page-navigation compatibility: keep legacy `nav.page` migration while
  exposing only the three explicit IDs, keep page names unique per profile and
  rewrite every `nav.page.go` target on rename. See AGENTS.md §3 and §5.
- Preserve grid-level DnD: CAPTURE must win over child buttons, only configured
  keys may start a primary-button drag, internal payloads must match the active
  source, and moves/swaps must keep the unsaved-edit guard. The drag source is a
  `(folder path, index)` pair because a folder can spring open mid-drag; a spring
  never runs while the editor is dirty, and `move_key_to()` keeps the depth,
  Back-slot and folder-inside-itself refusals. See AGENTS.md §3 and §5.
- Keep countdown/stopwatch state scoped by profile/page/key. Immediate clock
  presses must stay off action workers, ticks must repaint only changed seconds
  in the visible view, DnD must move runtime state, and edits/import/shutdown
  must reset it and stop completion audio. See AGENTS.md §3 and §5.
- Keep the screen saver exclusive and wake-safe: render its full-deck canvas
  under `RENDER_LOCK`/BASIC, cap brightness by its independent intensity, pause
  normal key renders, consume the first physical wake press, restore configured
  brightness/images, and join its thread before HID closes. Dialog subscribers
  must unsubscribe on close. Track idle activity only at physical-key and
  explicit virtual-deck entry points; never add a broad/global
  `Gtk.EventControllerLegacy` activity hook. See AGENTS.md §3 and §5.
- Keep hiding to the status area reversible: `hides_on_close()` is the only
  decision point and must still require an explicit-quit check, the configured
  action and a registered icon. Hide by returning `True` from `close-request`
  without destroying the window, route icon-driven quit/profile changes through
  the unsaved-change guard, and keep autostart state in the XDG entry rather than
  in `config.json`. Never nest a built `GLib.Variant` inside a format string when
  replying to dbusmenu. See AGENTS.md §3 and §5.
- Keep folder keys bounded and reversible. The first slot inside a folder is
  always the Back key and must never be stored or edited, nesting stays within
  `MAX_FOLDER_DEPTH` on load/create/paste, `KeyConfig.folder` stays
  `compare=False`, the open folder is never persisted, and every walk over a
  key's actions or assets recurses into folders. See AGENTS.md §3 and §5.
- Preserve the physical clean-exit display contract: firmware reset for
  **Device default**, black keys plus brightness 0 for **Off**, and one validated
  full-grid image at normal brightness for **Custom**. Apply it after device
  workers join and before HID closes, fall back to the device default on failure,
  and never claim forced termination can apply it. See AGENTS.md §3 and §5.

### Standard verification loop

After editing code:

```bash
.venv/bin/python -m compileall -q linuxstreamdeck        # must pass
TEST_CONFIG_DIR="$(mktemp -d)"
LSD_CONFIG_DIR="$TEST_CONFIG_DIR" .venv/bin/python -m unittest discover -s tests -v
```

For rendering changes, compose the relevant key PNG(s) offscreen and read the
image back, using `screensaver_frame()` for screen-saver changes,
`exit_image_tiles()` for custom exit displays and a temp `LSD_CONFIG_DIR` if the
path touches config. For a live GUI check, hand it to the user with `./run.sh`
(they must close any old window first — single instance).

### Working style

- Prefer the dedicated Read/Edit/Grep/Glob tools over shelling out to
  `cat`/`sed`/`grep`.
- When adding an OBS or system action, follow the declarative pattern in
  `core/actions.py` (`Param`, `choices_source`, key-scoped `ActionContext`,
  `feedback`, `immediate`, `default_icon`) and register it with `@register`;
  register default icons via `apply_default_icons`.
- Match the style of the file you touch (naming, comment density, private `_helpers`).

### Keeping documentation current

When a change affects user-facing behaviour, structure, commands or conventions,
update the docs — or delegate to the **`documenter`** subagent
(`.claude/agents/documenter.md`, generated from
`agent-definitions/prompts/documenter.md`), which reviews every `.md` file and
brings it in line with the current code and canonical agent definitions. Invoke
it when the user asks to "update the documentation", or proactively after a
change that clearly makes a doc stale.
