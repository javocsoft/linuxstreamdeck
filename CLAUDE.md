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
  cancel it and every exit must restore brightness. See AGENTS.md §3 and §5.
- Preserve page-navigation compatibility: keep legacy `nav.page` migration while
  exposing only the three explicit IDs, keep page names unique per profile and
  rewrite every `nav.page.go` target on rename. See AGENTS.md §3 and §5.
- Preserve grid-level DnD: CAPTURE must win over child buttons, only configured
  keys may start a primary-button drag, internal payloads must match the active
  source, and moves/swaps must keep the unsaved-edit guard. See AGENTS.md §3 and
  §5.

### Standard verification loop

After editing code:

```bash
.venv/bin/python -m compileall -q linuxstreamdeck        # must pass
TEST_CONFIG_DIR="$(mktemp -d)"
LSD_CONFIG_DIR="$TEST_CONFIG_DIR" .venv/bin/python -m unittest discover -s tests -v
```

For rendering changes, compose the relevant key PNG(s) offscreen and read the
image back, using a temp `LSD_CONFIG_DIR` if the path touches config. For a live
GUI check, hand it to the user with `./run.sh` (they must close any old window
first — single instance).

### Working style

- Prefer the dedicated Read/Edit/Grep/Glob tools over shelling out to
  `cat`/`sed`/`grep`.
- When adding an OBS or system action, follow the declarative pattern in
  `core/actions.py` (`Param`, `choices_source`, `feedback`, `default_icon`) and
  register it with `@register`; register default icons via `apply_default_icons`.
- Match the style of the file you touch (naming, comment density, private `_helpers`).

### Keeping documentation current

When a change affects user-facing behaviour, structure, commands or conventions,
update the docs — or delegate to the **`documenter`** subagent
(`.claude/agents/documenter.md`, generated from
`agent-definitions/prompts/documenter.md`), which reviews every `.md` file and
brings it in line with the current code and canonical agent definitions. Invoke
it when the user asks to "update the documentation", or proactively after a
change that clearly makes a doc stale.
