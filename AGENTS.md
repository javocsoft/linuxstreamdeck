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
  (key image composition), `obsws-python` (obs-websocket v5 client).
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
./build.sh              # create .venv, install the package (editable) + deps, compile-check
./build.sh --apt        # same, and install the system packages first (needs sudo)
sudo ./install-udev.sh  # one-time USB permissions for the Stream Deck
./run.sh                # launch the app
LSD_DEBUG=1 ./run.sh    # launch with debug logging
```

System packages required: `gir1.2-gtk-4.0 gir1.2-adw-1 libhidapi-libusb0
python3-gi python3-gi-cairo`.

**Compile check (use after any code change):**

```bash
.venv/bin/python -m compileall -q linuxstreamdeck
```

There is **no automated test suite**. Verify behaviour by compiling, by isolated
scripts (see §6 for the safe way), and — for rendering — by composing key PNGs
offscreen. Do **not** rely on launching the GUI to "see" a change (see §5).

**Debian package:** `./packaging/build-deb.sh [X.Y.Z]` produces
`dist/linux-stream-deck-<version>.deb`. It is `Architecture: all`: the pure-Python
app plus the two pip-only deps (`StreamDeck`, `obsws_python`) are vendored under
`/usr/lib/linuxstreamdeck/_vendor`, while GTK4/Adw (PyGObject), Pillow, hidapi and
websocket-client come from apt `Depends`. The launcher `/usr/bin/linuxstreamdeck`
runs the system `python3` with those paths on `sys.path`. It also installs a
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
├── app.py             LinuxStreamDeckApp: builds Config, EventBus, OBSClient,
│                      DeckManager, DeckController, MainWindow; app lifecycle.
├── basic_actions.py   System/navigation actions (run command, open URL, wait, go to page…).
├── core/
│   ├── events.py      EventBus (pub/sub). Emitters may run on any thread; a
│   │                  `dispatcher` (GLib.idle_add) marshals callbacks to the UI thread.
│   ├── config.py      Data model + JSON persistence: Config → Profile → Page →
│   │                  KeyConfig (+ ObsSettings, ActionStep). Migration, backup,
│   │                  and portable `.lsdconfig` import/export with custom icons.
│   ├── actions.py     Action framework: `Action` base, declarative `Param`,
│   │                  `ActionContext`, global `REGISTRY`, `@register`, `by_category`.
│   ├── controller.py  DeckController: handles presses, runs action steps, renders
│   │                  every key, owns page/profile/key operations, and applies imports.
│   └── icons.py       Built-in icon library (Material Design Icons glyphs via
│                      Pillow, recolorable, cached). `RENDER_LOCK`.
├── device/
│   ├── manager.py     DeckManager: physical device via HID, hotplug, key callbacks.
│   └── renderer.py    `compose()` — builds each key PNG with Pillow (bg, icon,
│                      label, badge, active-state soft lighting).
├── obs/
│   ├── client.py      OBSClient: obs-websocket v5 connection, auto-reconnect,
│   │                  thread-safe requests, re-emits OBS events onto the bus.
│   └── actions.py     Catalogue of OBS actions (scenes, recording/streaming,
│                      audio, sources/filters, media, advanced + raw request).
├── ui/
│   ├── window.py      MainWindow: key grid (virtual deck), header (profiles,
│   │                  configuration import/export, pages, brightness, OBS settings,
│   │                  About), DnD move, copy/paste, status bar.
│   ├── editor.py      EditorPanel: right-hand key editor; key-type selector.
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
| `deck.connected` | `model:str, keys:int` | Device plugged in. |
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

### Action framework

Actions are declarative and self-registering:

- Subclass `Action`, set `id`, `name`, `category`, `params`, optional
  `default_icon` (`"mdi:name"`), and decorate with `@register`.
- Each `Param` has a `kind` (`string | int | float | choice | duration`, the last
  a `MM:SS` time field — see `parse_duration`/`format_duration`) and may set
  `choices_source` so the editor fills the dropdown **live from OBS**
  (`scenes`, `inputs`, `media_inputs`, `transitions`, `scene_collections`,
  `profiles`, `sources_in_scene`, `filters_of_source`, `hotkeys`, `pages`).
- `execute(ctx, params)` performs the action; `feedback(ctx, params)` optionally
  returns `{"active": bool, "color": "#rrggbb", "badge": str}` for live key state.
- `apply_default_icons({action_id: "mdi:…"})` assigns default icons after
  registration. Registration happens on import (`app.py` imports the catalogues).

### Config / key model

`Config` holds `profiles` and delegates `pages`/`current_page` to the active
`Profile`. Each `KeyConfig.kind` is one of:

- `single` (`KIND_SINGLE`) — one action, with state feedback.
- `multi` (`KIND_MULTI`) — an ordered list of `ActionStep`s run in sequence;
  pauses are an explicit `Wait` action (`sys.wait`, a `duration` param), not a
  per-step delay.
- `multi_toggle` (`KIND_TOGGLE`) — two lists (`steps_on` / `steps_off`) with an
  ON/OFF state; the state is keyed by (profile, page, key) in the controller.

Persistence is JSON at `~/.config/linuxstreamdeck/config.json` with a
`config.json.bak` backup written on every save, and migration from the old
single-profile format on load. The profiles menu can export a portable
`.lsdconfig` ZIP archive containing the full JSON configuration and available
custom key icons. Built-in `mdi:` icons stay as references. Import validates the
archive, restores bundled custom icons below `CONFIG_DIR/imported-icons`, replaces
the complete configuration and writes the prior configuration to `config.json.bak`.
An export includes the OBS password, so it must be handled as private data.

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

## 5. Critical gotchas (read before editing rendering, OBS or the UI)

These are hard-won and easy to reintroduce. Violating them causes intermittent,
hard-to-diagnose failures.

1. **Pillow text layout must be BASIC.** Pillow's pip wheel bundles its *own*
   harfbuzz; used alongside GTK/Pango's system harfbuzz it intermittently draws
   **blank glyphs** (random per process — some launches all icons fine, others all
   blank). The fix, which must stay in place, is
   `ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.BASIC)` in both
   `core/icons.py` and `device/renderer.py`. Do **not** use raqm, `anchor="mm"`,
   or oversized fonts for glyphs (the latter caused giant masks →
   `DecompressionBombWarning` + blank keys). Glyph centering uses
   `textbbox` + ink-bbox recenter.

2. **Rendering is not thread-safe → `RENDER_LOCK`.** Pillow/FreeType is not
   thread-safe. The deck renders on a worker thread while the icon picker/preview
   render on the main thread; concurrent use produced blank (and blank-cached)
   glyphs. A shared reentrant `RENDER_LOCK` (defined in `core/icons.py`, imported
   by `device/renderer.py`) serializes all text drawing. It is reentrant so
   `compose()` can call `library.render` without deadlocking. The glyph cache is
   manual and **never caches failures** (safety net).

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

---

## 6. Safe local experimentation

`Config.save()` **always** writes to `~/.config/linuxstreamdeck/config.json`
unless `LSD_CONFIG_DIR` is set. Any script that exercises code calling `save()`
(e.g. `set_page`, `set_profile`, `add_profile`, `add_page`, `rename_page`,
`paste_key`, `clear_key`, brightness changes, saving a key) will **overwrite the
user's real config** — this has happened and lost real keys and the OBS password.
`Config.import_bundle()` also saves a replacement configuration and writes imported
icons below the config directory, so it must always be isolated too.

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
- `docs/` — assets such as `screenshot.png`.

There is a dedicated **`documenter` agent** (`.claude/agents/documenter.md`) whose
job is to review all `.md` files and bring them up to date with the current state
of the project. Invoke it when the code has changed in ways that affect the docs,
or when asked to "update the documentation".
