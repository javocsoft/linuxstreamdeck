# AGENTS.md

Operational guide for AI coding agents working on **LinuxStreamDeck**. Read this
before making changes. It is the single source of truth for how the project is
built, how it is structured, and the non-obvious rules you must respect.
(`CLAUDE.md` builds on this file with Claude-specific notes.)

---

## 1. What this project is

LinuxStreamDeck is a **GTK4 / Libadwaita desktop app** that turns an **Elgato
Stream Deck** into a control surface on **Linux**, built around **deep OBS Studio
integration** (obs-websocket v5). It also works as a *virtual deck* on screen, so
it is fully usable and testable **without the physical hardware connected**.

- **Language / UI:** Python ≥ 3.10, PyGObject (GTK 4 + Libadwaita/`Adw`).
- **Key dependencies:** `streamdeck` (python-elgato-streamdeck, HID), `pillow`
  (key image composition), `obsws-python` (obs-websocket v5 client), GStreamer
  1.0 `playbin` (local audio playback).
- **Target device:** Stream Deck Original V2 (`0fd9:006d`), 15 keys, 5 columns,
  keys rendered at 72×72 px. The grid layout constant lives in `ui/window.py`.
- **License:** GPL-3.0-or-later. **Author:** JavocSoft.
- **Repo:** github.com/javocsoft/linuxstreamdeck

---

## 2. Build, run, verify

All commands run from the project root. The virtual environment lives in `.venv`
and is created **with `--system-site-packages`** so it can import the system GTK /
PyGObject.

```bash
./build.sh              # check agent adapters; create .venv, install package + deps, compile-check
./build.sh --apt        # same, and install the system packages first (needs sudo)
sudo ./install-udev.sh  # one-time USB permissions for the Stream Deck
./run.sh                # launch the app
LSD_DEBUG=1 ./run.sh    # launch with debug logging
```

System packages required: `gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-secret-1
gir1.2-gstreamer-1.0 gstreamer1.0-plugins-base gstreamer1.0-plugins-good
gnome-keyring libhidapi-libusb0 python3-gi python3-gi-cairo`.

**Compile check (use after any code change):**

```bash
.venv/bin/python -m compileall -q linuxstreamdeck
```

The automated `unittest` suite lives under `tests/`. Run it with an isolated
configuration directory so no test can reach the user's real configuration:

```bash
TEST_CONFIG_DIR="$(mktemp -d)"
LSD_CONFIG_DIR="$TEST_CONFIG_DIR" .venv/bin/python -m unittest discover -s tests -v
```

Also verify behaviour with targeted isolated scripts when appropriate (see §6)
and, for rendering, by composing key PNGs offscreen. Do **not** rely on launching
the GUI to "see" a change (see §5).

**Debian package:** `./packaging/build-deb.sh [X.Y.Z]` produces
`dist/linux-stream-deck-<version>.deb`. It is `Architecture: all`: the pure-Python
app plus the two pip-only deps (`StreamDeck`, `obsws_python`) are vendored under
`/usr/lib/linuxstreamdeck/_vendor`, while GTK4/Adw (PyGObject), Secret Service,
GNOME Keyring, GStreamer plus its base/good plugins, Pillow, hidapi,
websocket-client and `ca-certificates` come from apt `Depends`. AI provider HTTPS
calls and the local audio wrapper use the Python standard library / system GI
bindings, so there is no additional pip dependency. The launcher
`/usr/bin/linuxstreamdeck` runs the system `python3` with those paths on
`sys.path`. It also installs a
desktop entry, the app icon, an **AppStream metainfo** (so installed software
catalogues can discover its icon, description and screenshot), and the udev rule
(reloaded by the `postinst`). Version defaults to `pyproject.toml`; the build
**syncs it into both** `pyproject.toml` and `linuxstreamdeck/__init__.py::VERSION`,
so passing `X.Y.Z` also bumps those sources. After installing or upgrading the
package, run `sudo ./packaging/refresh-appstream.sh` to refresh the system
AppStream cache for software centres, then reopen the software centre.

---

## 3. Architecture

Composition root is `app.py::LinuxStreamDeckApp`, which wires everything together
and wraps a single-instance `Adw.Application`. Components communicate through a
thread-safe **pub/sub `EventBus`**; UI never talks to the device or OBS directly.
`LinuxStreamDeckApp` also owns shutdown: it stops controller workers before the
HID manager and OBS client, so no background work outlives application teardown.

```
linuxstreamdeck/
├── __main__.py        Entry point; logging setup; `linuxstreamdeck` console script.
├── app.py             LinuxStreamDeckApp: builds config, credential stores, AI service,
│                      EventBus, OBS client, deck/controller and MainWindow; app lifecycle.
├── basic_actions.py   System/navigation actions (command, URL, wait, audio, page…).
├── ai/
│   ├── constants.py   OpenAI/Claude provider ids, labels and default models.
│   └── service.py     Provider calls, bounded optional context, local proposal validation.
├── core/
│   ├── events.py      EventBus (pub/sub). Emitters may run on any thread; a
│   │                  `dispatcher` (GLib.idle_add) marshals callbacks to the UI thread.
│   ├── config.py      Data model + atomic user-only JSON persistence: Config →
│   │                  Profile → Page → KeyConfig (+ ObsSettings, AISettings, ActionStep).
│   │                  Migration, backup and portable `.lsdconfig` import/export.
│   ├── actions.py     Action framework: `Action` base, declarative `Param`,
│   │                  `ActionContext`, global `REGISTRY`, `@register`, `by_category`.
│   ├── audio.py       Blocking local playback via GStreamer playbin; shutdown-aware.
│   ├── controller.py  DeckController: handles presses, separate action/render workers,
│   │                  running feedback, page/profile/key operations and imports.
│   ├── icons.py       Built-in icon library (Material Design Icons glyphs via
│   │                  Pillow, recolorable, cached). `RENDER_LOCK`.
│   └── secrets.py     Async Secret Service storage for OBS and per-provider API keys.
├── device/
│   ├── manager.py     DeckManager: HID hotplug, exclusive startup, key callbacks.
│   ├── startup_animation.py 33-frame offscreen physical-deck wake/title sequence.
│   └── renderer.py    `compose()` — builds each configured key PNG with Pillow
│                      (bg, icon, label, badge, active-state lighting, running halo).
├── obs/
│   ├── client.py      OBSClient: obs-websocket v5 connection, auto-reconnect,
│   │                  thread-safe requests, re-emits OBS events onto the bus.
│   └── actions.py     Catalogue of OBS actions (scenes, recording/streaming,
│                      audio, sources/filters, media, advanced + raw request).
├── ui/
│   ├── window.py      MainWindow: key grid (virtual deck), header (profiles,
│   │                  configuration import/export, pages, brightness, OBS settings,
│   │                  About), DnD move, copy/paste, unsaved-change guard, status bar.
│   ├── editor.py      EditorPanel: key editor, canonical draft/baseline and key type.
│   ├── ai_assistant.py OpenAI/Claude proposal dialog and explicit editor handoff.
│   ├── steps.py       StepEditor / StepList / AppearanceBox — reused by the editor
│   │                  for single, multi and toggle key types.
│   ├── icon_picker.py Searchable grid to pick a library icon.
│   ├── about.py       About dialog: application identity, credits, license and source link.
│   ├── obs_settings.py OBS connection dialog (host/port/password).
│   └── profile_dialog.py New/edit profile dialog (name + description).
└── assets/icons/      MDI font (TTF) + icons.json index (bundled, Apache-2.0).
```

### EventBus topics

| Topic | Payload | Meaning |
| --- | --- | --- |
| `deck.key` | `index:int, pressed:bool` | Physical key pressed/released. |
| `deck.connected` | `model:str, keys:int` | Physical device ready after startup. |
| `deck.disconnected` | — | Device removed. |
| `obs.connected` / `obs.disconnected` | — | OBS websocket state. |
| `obs.state` | `what:str` | Any OBS state change (drives key feedback). |
| `page.changed` | `index:int, name:str` | Active page changed. |
| `profile.changed` | `name, description, …` | Active profile changed. |
| `ui.key_image` | `index:int, png:bytes` | A key was rendered; UI paints it. |
| `status` | `text:str` | Transient status-bar message. |

The `*` topic receives every event. `EventBus.dispatcher` is `GLib.idle_add` in
the app, so subscribers always run on the GTK main thread even when the emit came
from the deck read thread or the obs-websocket event thread.

### Physical deck startup and connection ordering

`device/startup_animation.py::startup_frames()` creates a 33-frame offscreen
sequence for the physical 15-key, 5×3 deck: wake/energy wave, burst, progressive
title, hold, fade and a final black frame. The 15-character
`LinuxStreamDeck` title is assigned one character per key in row-major order:
`Linux` / `Strea` / `mDeck`. Frame brightness changes smoothly but
`_scaled_brightness()` never exceeds the user's configured target.

`DeckManager` plays the sequence directly on a newly opened device from the
monitor thread. Until it completes, the manager must not assign `self.deck`,
register the key callback or emit `deck.connected`; this keeps normal controller
renders and physical presses from interleaving with startup writes. The final
`deck.connected` event triggers the controller's existing configured-key refresh
after the black frame. The virtual deck does not display this animation.

Every wait and per-key write checks the monitor stop event. Shutdown therefore
cancels startup promptly and closes the provisional device without publishing a
connection. Rendering or HID I/O failure skips the remainder safely so connection
can continue when the device remains available, and `DeckManager` restores the
current configured brightness in `finally` on every exit path.

### Action framework

Actions are declarative and self-registering:

- Subclass `Action`, set `id`, `name`, `category`, `params`, optional
  `default_icon` (`"mdi:name"`), and decorate with `@register`.
- Each `Param` has a `kind` (`string | int | float | choice | duration |
  optional_duration | file`). Duration fields accept `MM:SS` / `H:MM:SS`;
  `optional_duration` may stay blank. Numeric parameters may declare
  `minimum` / `maximum` / `step`; file parameters may declare `extensions` and
  `file_filter_name` for the native chooser. A parameter may also set
  `choices_source` so the editor fills the dropdown **live from OBS**
  (`scenes`, `inputs`, `media_inputs`, `transitions`, `scene_collections`,
  `profiles`, `sources_in_scene`, `filters_of_source`, `hotkeys`, `pages`).
- `execute(ctx, params)` performs the action; `feedback(ctx, params)` optionally
  returns `{"active": bool, "color": "#rrggbb", "badge": str}` for live key state.
- Set `running_feedback = True` on a blocking action that should show the
  controller's temporary `RUN`/breathing feedback even as a single-action key.
- Set `restart_on_repress = True` when pressing the same profile/page/key again
  should cancel its prior invocation and restart the complete key sequence.
  Long-running actions must cooperate through `ActionContext.stop_requested()`
  or `ActionContext.wait_until_stopped()`, which observe both application
  shutdown and replacement cancellation.
- `apply_default_icons({action_id: "mdi:…"})` assigns default icons after
  registration. Registration happens on import (`app.py` imports the catalogues).

An empty `KeyConfig.icon` or `icon_off` is an inheritance marker, not a missing
render value. The editor Appearance preview must resolve the same effective
action/default icon as the controller and deck grid, but must keep the stored
reference empty. Clearing an explicit icon therefore restores inheritance instead
of copying the current fallback into config.

### Local audio action

`sys.audio` (**Play audio file**) uses GStreamer `playbin` for local `.wav`,
`.wave`, `.mp3`, `.ogg`, `.oga`, `.flac` and `.opus` files. Its declarative
parameters are a filtered `file` chooser, integer volume from 0 to 100 with a
step of 5, and an optional maximum duration. Blank duration means play to EOS;
otherwise `MM:SS` / `H:MM:SS` limits playback.

Playback is deliberately blocking on the action worker so the next multi/toggle
step starts only after EOS or the limit. The player polls
`ActionContext.stop_requested()`, returns promptly during application shutdown or
replacement cancellation and always resets the pipeline to `NULL`.
Missing/unsupported files, pipeline creation/start failures and decoder errors
must surface through the normal action error status. Both `sys.audio` and
`sys.wait` opt single-action keys into running feedback.

`sys.audio` sets `restart_on_repress = True`, making its entire containing key
sequence restartable. Execution controls are scoped by `(profile, page, key)`.
A same-key press signals the prior control and chains its replacement behind the
prior `finished` event, so the old GStreamer pipeline reaches `NULL` before the
new invocation starts. Rapid presses cancel queued replacements before they run,
collapsing the queue to the latest invocation. `sys.wait` uses
`ActionContext.wait_until_stopped()` so a wait anywhere in an audio-containing
sequence can be interrupted promptly before the restart.

### AI-assisted key proposals

`AIService` supports OpenAI and Claude with user-selected models. Provider API
keys are stored separately per provider through `ApiKeyStore` in Secret Service /
GNOME Keyring. They must never enter `Config`, `config.json`, its backup, or a
`.lsdconfig` export. Provider API billing is separate from LinuxStreamDeck.
When a saved key is displayed, its fixed read-only mask is only a presence
indicator. The request must use the separately held stored key, never the mask.
Replacing, returning to the saved key, and forgetting it are explicit UI actions.

The optional generation context is an explicit user opt-in. It contains only
bounded OBS and page names collected by `collect_generation_context()`; never
send passwords, commands, the full configuration, or other secrets. The action
catalogue exposed to a provider always excludes `sys.command` and `obs.raw`.

A provider response is untrusted data. It must pass local structural, action,
parameter, choice and icon validation before becoming an `AIProposal`. Generation
must never execute or save a key. The user first previews the proposal, explicitly
loads it into the existing editor, reviews it, and presses **Save** to persist it.

### Config / key model

`Config` holds `profiles` and delegates `pages`/`current_page` to the active
`Profile`. Each `KeyConfig.kind` is one of:

- `single` (`KIND_SINGLE`) — one action, with state feedback.
- `multi` (`KIND_MULTI`) — an ordered list of `ActionStep`s run in sequence;
  pauses are an explicit `Wait` action (`sys.wait`, a `duration` param), not a
  per-step delay.
- `multi_toggle` (`KIND_TOGGLE`) — two lists (`steps_on` / `steps_off`) with an
  ON/OFF state; the state is keyed by (profile, page, key) in the controller.

### Running key feedback and workers

Multi and multi-toggle invocations, plus single actions with
`running_feedback = True`, are counted from queueing through completion, keyed by
`(profile, page, key)`. While the count is nonzero, that key shows a `RUN` badge
and a subtle slow blue breathing halo. Repeated presses increment the count; for
restartable sequences, canceled running or queued invocations remain counted
until they finish unwinding, so feedback stays visible across the replacement.
Toggle keys keep their ON/OFF state underneath the temporary running feedback
and restore the appropriate badge when the final invocation completes.

Action execution and rendering use separate `ThreadPoolExecutor` instances. Two
long `Wait` actions may occupy both action workers, but must never prevent a key
image or activity pulse from rendering. The activity thread alternates the pulse
phase and asks the render executor to refresh only busy keys on the active
profile/page; activity belonging to another profile or page must not leak into the
current view.

Controller shutdown order is deliberate: set the stopping signal, shut down the
action executor, join the activity thread, then shut down the render executor.
`Wait` observes both the stopping signal and per-invocation cancellation through
its `ActionContext`, so shutdown and same-key replacement can end it promptly.
Do not merge the executors or reorder teardown in a way that lets
actions/activity submit work after the render executor has stopped.

### Unsaved editor protection

`EditorPanel.current_key_config()` must reconstruct the complete canonical
`KeyConfig` draft without persisting it. On load and after a successful save, the
editor stores a cloned baseline and reports unsaved changes by comparing the
current draft with that baseline. Do not replace this with a sticky dirty flag:
action changes, parameter values/order, appearance and AI-loaded proposals must
all count, while reverting every value must become clean again.

The editor retains both its source `Page` object and key index. A deferred
**Save and continue** must write to that source page, never whichever page became
active later. `MainWindow` gates key selection, key moves, page/profile switches
and creation, configuration import and window close through the unsaved-change
dialog. It offers **Keep editing**, **Discard changes** and, when the destination
will preserve the saved key, **Save and continue**. Paste or clear of the edited
key deliberately omits the save option because the next operation overwrites that
same key.

Clicking the already selected key must not reload the editor. Page-change handling
tracks the selected page by object identity so renaming that same page preserves
the selection, while a real page change clears it.

Persistence is atomic JSON with user-only (`0600`) permissions at
`~/.config/linuxstreamdeck/config.json`, with a `config.json.bak` backup written
on every save and migration from the old single-profile format on load. The OBS
password is stored asynchronously in Secret Service / GNOME Keyring, never in
either JSON file. On first run, legacy plaintext password fields are migrated and
removed from both files; if Secret Service is unavailable, they are still removed
and the password is session-only. OpenAI and Claude API keys are also stored only
in Secret Service, under separate provider identities; AI preferences in config
never contain either key.

The profiles menu exports `.lsdconfig` ZIP format v2 with the full JSON
configuration, available custom key icons and supported files referenced by
`sys.audio`; identical audio content is deduplicated. Audio is limited to 200 MiB
per file and 500 MiB total. Built-in `mdi:` icons stay as references, and OBS
passwords and provider API keys are never exported. Missing, unreadable,
unsupported or oversized audio remains a local reference and produces an export
warning.

Import accepts format v1 and v2, validates archive member paths and size limits,
restores bundled icons below `CONFIG_DIR/imported-icons` and audio below
`CONFIG_DIR/imported-audio`, replaces the complete configuration and writes the
prior configuration to `config.json.bak`. It keeps the destination computer's
keyring credentials and ignores password fields in old exports, so an OBS password
must be entered once after moving to a new computer.

---

## 4. Conventions

- **English only, everywhere.** All user-facing text (action names, categories,
  descriptions, parameter labels, choice values, dialogs, menus, tooltips,
  status messages) **and** all code comments, docstrings, log messages, README,
  shell scripts, the udev rule and `pyproject.toml`. No Spanish and **no accented
  characters** in any versioned file. When you add any text, write it in English.
- **Match the surrounding style.** Follow the existing naming, comment density and
  idioms of the file you are editing. Keep helpers small and private (`_name`).
- **Threading discipline is not optional** — see §5.
- Reference code as `path:line` in explanations.

---

## 5. Critical gotchas (read before editing rendering, devices, OBS or the UI)

These are hard-won and easy to reintroduce. Violating them causes intermittent,
hard-to-diagnose failures.

1. **Pillow text layout must be BASIC.** Pillow's pip wheel bundles its *own*
   harfbuzz; used alongside GTK/Pango's system harfbuzz it intermittently draws
   **blank glyphs** (random per process — some launches all icons fine, others all
   blank). The fix, which must stay in place, is
   `ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.BASIC)` in
   `core/icons.py`, `device/renderer.py` and `device/startup_animation.py`. Do
   **not** use raqm, `anchor="mm"`, or oversized fonts for glyphs (the latter
   caused giant masks →
   `DecompressionBombWarning` + blank keys). Configured-key glyph centering uses
   `textbbox` + ink-bbox recenter; the startup title centers each character from
   its text bounding box within one hardware-key cell.

2. **Rendering is not thread-safe → `RENDER_LOCK`.** Pillow/FreeType is not
   thread-safe. The deck renders on a worker thread while the icon picker/preview
   render on the main thread; concurrent use produced blank (and blank-cached)
   glyphs. A shared reentrant `RENDER_LOCK` (defined in `core/icons.py`, imported
   by `device/renderer.py` and `device/startup_animation.py`) serializes all text
   drawing. It is reentrant so `compose()` can call `library.render` without
   deadlocking. The glyph cache is manual and **never caches failures** (safety
   net).

3. **OBS requests must be fully serialized.** `obsws_python.ReqClient` uses a
   single websocket that is **not** thread-safe. In `obs/client.py` the `_lock`
   must be held for the **entire** `req.send(...)`, not just while reading the
   client pointer. Requests come from two threads at once (the GTK thread filling
   editor dropdowns, and the render worker for `feedback()` calls like
   `obs.source_visibility` / filters that query OBS live). Overlap corrupts the
   protocol → hang at ~73% CPU and disconnect.

4. **Single-instance app.** It is a single-instance `Adw.Application`. Editing
   `.py` files does **not** affect an already-running process, and relaunching
   while a window is open merely re-activates the existing one. After a code
   change the running window must be **fully closed** before relaunching. Proof
   that a given instance is the live one: its log shows the deck "connected" line
   (HID access is exclusive — only one process can open the device).

5. **Do not launch the GUI yourself to verify changes.** Background launches leave
   zombie instances and cross-activations, and the user ends up looking at a stale
   window with cached state (e.g. blank glyphs cached before a fix). To verify UI
   rendering, compose key PNGs **offscreen** (build objects directly, or use
   `Gtk.WidgetPaintable` + `Gtk.Snapshot` + `renderer.render_texture` →
   `save_to_png`). To verify the running app, ask the user to close any old window
   and launch it themselves with `./run.sh`.

6. **AI output is an untrusted proposal, never an action.** Keep provider API
   keys in `ApiKeyStore`; a display mask must never become a request credential.
   Keep context optional and limited to bounded OBS/page names, and exclude
   `sys.command` and `obs.raw` from the provider catalogue. Validate every response
   locally. Generation must not execute or save anything; only the user's explicit
   **Save** from the existing editor persists the key.

7. **Unsaved key edits must survive navigation decisions.** Dirty state is a
   canonical `KeyConfig` comparison, not a sticky UI flag. Keep the source page
   reference stable across deferred dialog responses, preserve the guarded paths
   described in §3, and never reload on a same-key click or a rename event for the
   same page object.

8. **Action work must not starve running feedback.** Keep action and render
   executors separate, count every queued/running invocation per profile/page/key,
   pulse only busy keys in the active view, and preserve shutdown order: action
   executor, activity thread, render executor.

9. **Restartable actions must never overlap.** Keep cancellation controls scoped
   by profile/page/key, cancel the prior same-key invocation and wait for its
   `finished` event before starting the replacement. Long-running actions and
   waits must observe their `ActionContext`; canceled queued replacements must
   exit without executing so rapid presses collapse to the latest invocation.

10. **Portable audio must stay bounded and path-safe.** Keep `.lsdconfig` v1 import
   compatibility while exporting v2. Bundle only supported `sys.audio` files,
   deduplicate content, enforce per-file/total limits, validate archive paths
   before extraction and restore only below `CONFIG_DIR/imported-audio`.

11. **Physical startup must remain exclusive and cancellable.** Generate frames
   offscreen under `RENDER_LOCK`, keep their brightness at or below the configured
   target and restore that target in `finally`. Play them directly on the
   provisional device before assigning `self.deck`, installing its callback or
   emitting `deck.connected`. Check the monitor stop event during writes and
   waits; only a completed or safely skipped animation may proceed to connection
   publication and the configured-key refresh.

---

## 6. Safe local experimentation

`Config.save()` **always** writes to `~/.config/linuxstreamdeck/config.json`
unless `LSD_CONFIG_DIR` is set. Any script that exercises code calling `save()`
(e.g. `set_page`, `set_profile`, `add_profile`, `add_page`, `rename_page`,
`paste_key`, `clear_key`, brightness changes, saving a key) will **overwrite the
user's real config** — this has happened and lost real keys and settings.
`Config.import_bundle()` also saves a replacement configuration and writes imported
icons and audio below the config directory, so it must always be isolated too.

**Always redirect the config directory before importing the package:**

```bash
LSD_CONFIG_DIR="$SOME_TMP_DIR" .venv/bin/python your_script.py
```

Set `LSD_CONFIG_DIR` in the environment **before** `linuxstreamdeck.core.config`
is imported. A `config.json.bak` backup is written on each save, but do not rely
on it — isolate first.

---

## 7. Documentation

The project's documentation must stay in sync with the code. Documentation files:

- `README.md` — user-facing overview, features, install/usage.
- `AGENTS.md` — this file (agent operational guide).
- `CLAUDE.md` — Claude-specific notes; imports this file.
- `CUSTOMAGENTS.md` — provider-neutral catalogue of the custom subagents.
- `agent-definitions/` — canonical prompts, provider settings and the generator
  for Claude/Codex agent adapters.
- `docs/` — assets such as `screenshot.png`.

Custom agent prompts must be edited only below `agent-definitions/`; files below
`.claude/agents/` and `.codex/agents/` are generated provider adapters. After an
agent prompt or manifest change, run:

```bash
python3 agent-definitions/sync.py
python3 agent-definitions/sync.py --check
```

There is a dedicated **`documenter` agent** for both Claude and Codex. Its job is
to review every Markdown file and bring it up to date with the current code and
canonical agent definitions. Invoke it when code or agent changes affect the
docs, or when asked to "update the documentation". It must include generated
agent Markdown in its audit and finish with the synchronization check above.
