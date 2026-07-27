# CUSTOMAGENTS.md

Reference for the project's custom Claude Code and OpenAI Codex subagents: what
each one does and when it should run. This is the human-readable catalogue;
`AGENTS.md` remains the shared operational guide that every agent enforces.

## One source, two native formats

Shared system prompts live in `agent-definitions/prompts/*.md`, while
`agent-definitions/manifest.json` holds trigger descriptions and
provider-specific tools, models and sandbox settings. The deterministic generator
produces the native files each provider discovers:

- Claude Code: `.claude/agents/*.md` (Markdown with YAML frontmatter).
- OpenAI Codex: `.codex/agents/*.toml` (standalone TOML agent config).

Never edit either generated directory directly. Change the canonical prompt or
manifest, then synchronize and check both providers:

```bash
python3 agent-definitions/sync.py
python3 agent-definitions/sync.py --check
```

The adapters are versioned with the repository, so a clone is immediately usable
without running the generator. See `agent-definitions/README.md` for the
maintenance contract.

## How subagents trigger

- **Claude Code:** delegates when a task matches the generated agent description,
  or when the user names or mentions the agent.
- **Codex:** delegates when explicitly requested or when applicable `AGENTS.md` or
  skill guidance calls for it; the generated description identifies the intended
  specialist.
- **Both:** the main session owns orchestration and receives the specialist's
  report or changes.

> **Caveat — not a deterministic hook.** Delegation is a model decision, not a
> callback that runs on every file save. Invoke an agent explicitly when a check
> must run, and use CI or provider hooks for mechanically enforced guarantees.

---

## `reviewer` — invariants guard

- **Automatic:** after changes to `obs/`, `device/`, `core/icons.py`,
  `core/controller.py`, `core/events.py` or `ui/`; or when you ask to review a
  diff / check for regressions.
- **Manual:** *"review this change"*, *"run the reviewer on the branch"*.
- **What it does:** checks the hard-won checklist — OBS lock covering the whole
  `send`, `RENDER_LOCK`, Pillow `BASIC` layout, config isolation via
  `LSD_CONFIG_DIR`, single-instance / no self-launch of the GUI, the EventBus
  threading model, running-feedback concurrency, default-icon inheritance,
  unsaved-editor guards, GStreamer audio lifecycle, same-key restart cancellation
  and ordering, physical startup exclusivity/cancellation/brightness restoration,
  device-geometry/multi-deck handling (columns derived from `key_layout()`, first-
  device selection, non-visual device rejection), page-navigation migration/
  reference integrity, portable-archive limits/path safety and audio deduplication,
  stateful clock identity/ticking/reset/completion
  lifecycle, screen-saver rendering/explicit activity/wake/brightness/thread
  shutdown with no global legacy GTK activity hook, clean-exit display
  mode/render/fallback/HID ordering and portable custom images, grid-DnD
  gesture/payload validation, AI credential/context/proposal safety, reusing
  feedback color constants, and English-only with no accents. It only reports;
  it does not edit code.

## `obs-action-author` — action scaffolder

- **Automatic / Manual:** when you want to add a new action or extend the OBS
  catalogue (*"add an action for X"*, *"expose that obs-websocket request on a
  key"*).
- **What it does:** generates the action with the correct declarative pattern
  (`Param` kinds/bounds/file filters, `choices_source`, feedback with the shared
  colors or centered display, key-scoped `ActionContext`, fast non-blocking
  `immediate` actions, blocking-action running feedback, cooperative cancellation
  and optional same-key restart semantics, profile-scoped page choices,
  `default_icon`, `@register`, `apply_default_icons`), OBS access always under
  the lock, verifies the id is unique and the category exists, compiles, and
  flags when the docs need updating.

## `render-qa` — offscreen render verifier

- **Automatic:** after touching `device/renderer.py`, `device/screensaver.py`,
  `device/startup_animation.py`, `device/exit_display.py`, `core/icons.py`, the
  icon assets, or active-state logic.
- **Manual:** *"verify how the keys look"*, *"check there are no blank glyphs"*.
- **What it does:** composes key PNGs **without launching the GUI** (with an
  isolated `LSD_CONFIG_DIR`) and objectively checks that glyphs are not blank, are
  centered, that the active background lights up without a border, and that colors
  are preserved. It also compares both running-feedback phases to confirm the
  subtle breathing halo and `RUN` badge remain visible without obscuring key art.
  Dynamic centered values such as `HH:MM:SS` clocks are checked with and without
  labels to ensure they replace the icon cleanly and remain legible.
  It also verifies every installed screen-saver style as animated full-deck
  frames across representative device shapes (Mini, Neo, the 15-key Original and
  XL), including per-key size/mode, intensity bounds and the
  LinuxStreamDeck-on-black layout. It checks custom exit images as coherent
  center-cropped full-deck grids and verifies the **Off** tiles are fully black.
  For the physical startup sequence, it can inspect the complete offscreen frame
  grid for any of those shapes, title mapping, brightness bounds and fade to
  black. It eyeballs the images itself, and reports without fixing unless asked.

## `documenter` — documentation keeper

- **Automatic:** after changes that affect user-facing behaviour, commands,
  structure, dependencies or conventions — anything that could make a `.md` stale.
- **Manual:** *"update the documentation"*, *"review the docs"*.
- **What it does:** inventories every Markdown file (README.md, AGENTS.md,
  CLAUDE.md, CUSTOMAGENTS.md, `docs/`, canonical agent prompts and any others),
  treats the **code and canonical agent definitions as ground truth**, and edits
  documentation to match reality. It checks both generated provider directories
  with `agent-definitions/sync.py --check`, keeps English with no accents,
  preserves each file's tone, maintains the AGENTS.md / CLAUDE.md relationship,
  and ends with a report of changes and any drift a human should decide on.
