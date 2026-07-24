# CUSTOMAGENTS.md

Reference for the project's **custom Claude Code subagents** — what each one does
and when it activates. The agent definitions live in `.claude/agents/*.md`; this
file is the human-readable index. See `AGENTS.md` for the project's operational
guide that these agents enforce.

## How subagents trigger

In Claude Code a subagent fires in one of two ways:

- **Automatically**, when the task matches the agent's `description` (the
  definitions say "Use PROACTIVELY…" where that is intended).
- **Explicitly**, when you ask for it by name (e.g. *"use the reviewer agent"*).

The main session can also delegate to them when the work calls for it. All agents
live in `.claude/agents/`, are written in English with no accented characters, and
are versioned with the repo (not gitignored), so anyone who clones the project
gets them.

> **Caveat — not a deterministic hook.** The "automatic" trigger is a decision the
> main agent makes based on each `description`; it is **not** a hook that runs on
> every file save. If you need a hard guarantee that an agent runs on every change,
> configure a hook in `settings.json` instead.

---

## `reviewer` — invariants guard

- **Automatic:** after changes to `obs/`, `device/`, `core/icons.py`,
  `core/controller.py`, `core/events.py` or `ui/`; or when you ask to review a
  diff / check for regressions.
- **Manual:** *"review this change"*, *"run the reviewer on the branch"*.
- **What it does:** checks the hard-won checklist — OBS lock covering the whole
  `send`, `RENDER_LOCK`, Pillow `BASIC` layout, config isolation via
  `LSD_CONFIG_DIR`, single-instance / no self-launch of the GUI, the EventBus
  threading model, reusing feedback color constants, and English-only with no
  accents. It only reports; it does not edit code.

## `obs-action-author` — action scaffolder

- **Automatic / Manual:** when you want to add a new action or extend the OBS
  catalogue (*"add an action for X"*, *"expose that obs-websocket request on a
  key"*).
- **What it does:** generates the action with the correct declarative pattern
  (`Param` / `choices_source`, `feedback` with the shared colors, `default_icon`,
  `@register`, `apply_default_icons`), OBS access always under the lock, verifies
  the id is unique and the category exists, compiles, and flags when the docs need
  updating.

## `render-qa` — offscreen render verifier

- **Automatic:** after touching `device/renderer.py`, `core/icons.py`, the icon
  assets, or the active-state logic.
- **Manual:** *"verify how the keys look"*, *"check there are no blank glyphs"*.
- **What it does:** composes key PNGs **without launching the GUI** (with an
  isolated `LSD_CONFIG_DIR`) and objectively checks that glyphs are not blank, are
  centered, that the active background lights up without a border, and that colors
  are preserved — plus it eyeballs the images itself. It reports; it does not fix
  unless asked.

## `documenter` — documentation keeper

- **Automatic:** after changes that affect user-facing behaviour, commands,
  structure, dependencies or conventions — anything that could make a `.md` stale.
- **Manual:** *"update the documentation"*, *"review the docs"*.
- **What it does:** inventories every Markdown file (README.md, AGENTS.md,
  CLAUDE.md, `docs/`, and any others), treats the **code as the ground truth**, and
  edits the docs to match reality — verifying commands, dependencies/versions, the
  module tree, EventBus topics, the action catalogue, config paths and the critical
  gotchas. It edits **documentation only** (never application code), keeps English
  with no accents, preserves each file's tone (README's friendly/gracious voice,
  AGENTS/CLAUDE's terse operational style), maintains the AGENTS.md ⇄ CLAUDE.md
  relationship, and ends with a report of what changed and any drift a human should
  decide on.
