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
- **Target devices:** every Stream Deck with key displays — Mini (3x2), Neo
  (4x2), Original/MK.2 (5x3, `0fd9:006d`), Stream Deck + (4x2 keys, 4 dials and
  an 800x100 LCD strip) and XL
  (8x4). The Pedal has no displays and is refused. Geometry comes from
  `key_layout()` at connection time, never from a constant: `GRID_COLS` in
  `ui/window.py` and `GRID_COLUMNS` in the renderers are only the
  pre-connection default. Only the 15-key Original is tested on real hardware;
  the rest are verified in simulation (`tests/test_multi_deck.py`).
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

`tests/test_invariants.py` is not a feature suite: it makes the silent rules of
§5 fail loudly (BASIC font layout, `RENDER_LOCK`, the OBS request lock and the
shutdown order). Treat a failure there as a real regression, never as a test that
needs relaxing.

Also verify behaviour with targeted isolated scripts when appropriate (see §6)
and, for rendering, by composing key PNGs offscreen. Do **not** rely on launching
the GUI to "see" a change (see §5).

**Debian package:** `./packaging/build-deb.sh [X.Y.Z]` produces
`dist/linux-stream-deck-<version>.deb`. It is `Architecture: all`: the pure-Python
app plus the two pip-only deps (`StreamDeck`, `obsws_python`) are vendored under
`/usr/lib/linuxstreamdeck/_vendor`, while GTK4/Adw (PyGObject), Secret Service,
GNOME Keyring, GStreamer plus its base/good plugins, Pillow, hidapi,
websocket-client and `ca-certificates` come from apt `Depends`. `ydotool` (key
injection) and `playerctl` (media transport) are `Recommends`, so apt installs
them by default while the package still installs where they are unavailable and
the app degrades to a status message. `fonts-noto-cjk` is only a `Suggests`: it
supplies the katakana of the **Matrix Code** screen saver, which falls back to
Latin without it, and 91 MB of fonts is far too much to pull in by default for
one animation. AI provider HTTPS
calls and the local audio wrapper use the Python standard library / system GI
bindings, so there is no additional pip dependency. The launcher
`/usr/bin/linuxstreamdeck` runs the system `python3` with those paths on
`sys.path`. It also installs a
desktop entry, the app icon, an **AppStream metainfo** (so installed software
catalogues can discover its icon, description and screenshot), and the udev rule
(reloaded by the `postinst`). Version defaults to `pyproject.toml`; the build
**syncs it into both** `pyproject.toml` and `linuxstreamdeck/__init__.py::VERSION`,
so passing `X.Y.Z` also bumps those sources.

Everything else derives that one value: `ui/about.py` reads `VERSION`,
`__main__.py` answers `--version` / `-V` **before GTK parses argv** (an unknown
option would otherwise be rejected, and the bug report template tells people to
run it), and the AppStream metainfo ships `@VERSION@` / `@DATE@` placeholders
that the build substitutes. Keep that release entry templated: a hardcoded one
would go stale on the next build without the scan below ever noticing, because
the scan only recognises a version that is *wrong*, never one that is missing.

Before that sync, `check_hardcoded_versions()` scans every tracked file and fails
the build when one still names a different version of this application — the way
an issue template or a document silently goes stale after a release. Dependency
constraints (`streamdeck>=0.9.5`) and lines marked `version-check: ignore` are
exempt, and the two version sources are skipped because `sync_version` owns them.
It deliberately runs **before** any file is rewritten, so a failed build leaves
the working tree untouched. Keep documentation examples as literal `X.Y.Z` rather
than a plausible number, or they trip the scan. After installing or upgrading the
package, run `sudo ./packaging/refresh-appstream.sh` to refresh the system
AppStream cache for software centres, then reopen the software centre.

---

## 3. Architecture

Composition root is `app.py::LinuxStreamDeckApp`, which wires everything together
and wraps a single-instance `Adw.Application`. Components communicate through a
thread-safe **pub/sub `EventBus`**; UI never talks to the device or OBS directly.
`LinuxStreamDeckApp` also owns shutdown, in a fixed order: status icon,
controller workers, HID manager, OBS client. Controller workers must stop before
the HID manager so no render is submitted to a closed device, and the deck must
stop before OBS so nothing is left waiting on a request that can no longer be
answered. `_on_shutdown` also sets `_shutting_down` *before* stopping the icon,
so `hides_on_close()` cannot intercept a quit already in progress. It reads like
tidy-uppable boilerplate, so it is pinned by `ShutdownOrderTests` in
`tests/test_invariants.py`. `DeckManager.stop()` joins the screen-saver thread,
then the monitor thread, applies the configured clean-exit display and finally
closes HID, so no background work outlives application teardown.

```
linuxstreamdeck/
├── __main__.py        Entry point; logging setup; `linuxstreamdeck` console script.
├── app.py             LinuxStreamDeckApp: builds config, credential stores, AI service,
│                      EventBus, OBS client, deck/controller, MainWindow and the
│                      status icon; app lifecycle and hide-on-close policy.
├── basic_actions.py   System actions (including clocks/audio) plus explicit page navigation.
├── ai/
│   ├── constants.py   OpenAI/Claude provider ids, labels and default models.
│   └── service.py     Provider calls, bounded optional context, local proposal validation.
├── core/
│   ├── events.py      EventBus (pub/sub). Emitters may run on any thread; a
│   │                  `dispatcher` (GLib.idle_add) marshals callbacks to the UI thread.
│   ├── config.py      Data model + atomic user-only JSON persistence: Config →
│   │                  Profile → Page → KeyConfig → Folder (+ OBS/AI/deck-display
│   │                  settings). Legacy action/profile migration, backup,
│   │                  `.lsdconfig` and single-key `.lsdkey` I/O.
│   ├── actions.py     Action framework: `Action` base, declarative `Param`,
│   │                  `ActionContext`, global `REGISTRY`, `@register`, `by_category`.
│   ├── audio.py       Blocking local playback via GStreamer playbin; shutdown-aware.
│   ├── autostart.py   XDG autostart entry: read/write/remove, hidden-start flag.
│   ├── apps.py        Desktop applications: list, resolve, launch, find and close.
│   ├── media.py       MPRIS transport (playerctl) and session volume (wpctl/pactl).
│   ├── keystrokes.py  Shortcut parsing, Linux presets, ydotool/wtype/xdotool.
│   ├── clocks.py      Thread-safe countdown/stopwatch runtime keyed by RuntimeKey.
│   ├── controller.py  DeckController: presses, action/render workers, running feedback,
│   │                  clocks/audio notifications, folder navigation, profile/key
│   │                  operations and imports.
│   ├── search.py      Key search across profiles, pages and nested folders.
│   ├── icons.py       Built-in icon library (Material Design Icons glyphs via
│   │                  Pillow, recolorable, cached). `RENDER_LOCK`.
│   └── secrets.py     Async Secret Service storage for OBS and per-provider API keys.
├── device/
│   ├── manager.py     DeckManager: HID hotplug, startup/saver threads, exit display, I/O.
│   ├── exit_display.py Validate/crop static full-deck images for clean shutdown.
│   ├── screensaver.py Pure-Pillow coordinated full-deck animation renderer.
│   ├── startup_animation.py 33-frame offscreen physical-deck wake/title sequence.
│   ├── touchscreen.py Stream Deck + LCD strip: one labelled panel per encoder.
│   ├── layout_sheet.py Whole profile as one printable, captioned PNG.
│   └── renderer.py    `compose()` — builds each configured key PNG with Pillow
│                      (bg, icon or centered value, sized label, badge, lighting,
│                      running halo).
├── obs/
│   ├── client.py      OBSClient: obs-websocket v5 connection, auto-reconnect,
│   │                  thread-safe requests, re-emits OBS events onto the bus.
│   └── actions.py     Catalogue of OBS actions (scenes, recording/streaming,
│                      audio, sources/filters, media, advanced + raw request).
├── ui/
│   ├── window.py      MainWindow: key grid (virtual deck), header (profiles,
│   │                  import/export, pages, deck display, brightness, OBS, About),
│   │                  folder breadcrumb and navigation, explicit saver activity,
│   │                  DnD, copy/paste, single-key export/import, unsaved guard,
│   │                  arrow-key grid navigation, OBS connection dot, status bar.
│   ├── editor.py      EditorPanel: key editor, canonical draft/baseline and key type.
│   ├── dials.py       Stream Deck + encoder row under the grid, and its editor.
│   ├── ai_assistant.py OpenAI/Claude proposal dialog and explicit editor handoff.
│   ├── steps.py       StepEditor / StepList / AppearanceBox — reused by the editor
│   │                  for single, multi and toggle key types.
│   ├── icon_picker.py Searchable grid to pick a library icon.
│   ├── action_picker.py Searchable list of every action, by name or by what it does.
│   ├── key_search.py  Find a key in any profile, page or folder, and go to it.
│   ├── tray.py       StatusNotifierItem + dbusmenu status icon (no GTK widgets).
│   ├── preferences.py Close behaviour and start-on-login settings dialog.
│   ├── about.py       About dialog: application identity, credits, license and source link.
│   ├── obs_settings.py OBS connection dialog (host/port/password).
│   ├── screensaver_settings.py Screen saver and clean-exit display settings.
│   └── profile_dialog.py New/edit profile dialog (name + description).
└── assets/icons/      MDI font (TTF) + icons.json index (bundled, Apache-2.0).
```

### EventBus topics

| Topic | Payload | Meaning |
| --- | --- | --- |
| `deck.key` | `index:int, pressed:bool` | Physical key pressed/released. |
| `deck.dial` | `index:int, direction:str, ticks:int` | Stream Deck + encoder turned (`left`/`right`) or pushed (`press`). |
| `deck.touch` | `index:int, event:str` | LCD strip tapped, resolved to the dial under it. |
| `deck.connected` | `model:str, keys:int` | Physical device ready after startup. |
| `deck.disconnected` | — | Device removed. |
| `deck.screensaver` | `active:bool, preview:bool, style:str` | Screen saver started/stopped; controller restores keys on stop. |
| `obs.connected` / `obs.disconnected` | — | OBS websocket state. |
| `obs.state` | `what:str` | Any OBS state change (drives key feedback). |
| `page.changed` | `index:int, name:str` | Active page changed. |
| `folder.changed` | `path:tuple[int, ...], trail:list` | A folder was opened, left or reset to the page root. |
| `profile.changed` | `name, description, …` | Active profile changed. |
| `ui.key_image` | `index:int, png:bytes` | A key was rendered; UI paints it. |
| `ui.screensaver_frame` | `images:tuple[bytes, ...]` | One animated full-deck frame for the virtual deck. |
| `status` | `text:str` | Transient status-bar message. |

The `*` topic receives every event. `EventBus.dispatcher` is `GLib.idle_add` in
the app, so subscribers always run on the GTK main thread even when the emit came
from the deck read thread or the obs-websocket event thread.

### One deck at a time, but any shape of deck

`DeckManager._try_open()` enumerates every Stream Deck and opens the **first**
one; the rest are ignored. `_report_extra_devices()` says so on the bus instead
of dropping them silently, which was indistinguishable from a second deck that
had failed to open. It reports once per distinct set of device ids, because the
scan runs every `SCAN_SECONDS` for as long as nothing is connected, and it never
opens a device just to name it — a serial number needs an open handle, and
opening a deck disturbs the USB bus (see §5.19).

**Geometry comes from the device, never from a constant.** `_device_columns()`
reads `key_layout()` and `DeckManager.columns` carries it to every full-deck
renderer: `startup_frames()`, `screensaver_frame()` and `exit_image_tiles()` all
split their canvas along it. Assuming five columns scrambled the screen saver,
the startup sequence and the custom exit image on every model that is not the
15-key original — a Mini has three columns, an XL eight. Per-key rendering was
always fine, because `compose()` takes the real key size, and every HID write
goes through `PILHelper.to_native_key_format()`, which applies each model's own
size, flip and rotation.

`MainWindow` builds its grid through `_build_key_grid()` rather than once at
startup: the window exists before anything is plugged in, so it opens on the
MK.2 defaults and `_on_deck_connected` reshapes it when a deck of another shape
arrives, dropping the selection first because every index the editor holds
refers to the old grid. `GRID_COLS` is only the pre-connection default.

**Anything that spreads text one character per key has to shrink, not truncate.**
`startup_animation.title_layout()` places the name across whatever grid exists,
picking the longest entry of `TITLE_FORMS` that fits — a six-key Mini gets
`Linux`, not the fragment `LinuxS` that cutting the name to length produced. The
block is centered vertically but each row starts at the left, because these are
a wrapped word rather than centered lines. The `linuxstreamdeck` screen saver
shares that helper: it is nothing *but* the title, so returning an empty layout
there would be a black screen that looks broken.

The Split-Flap Board has the same problem and its own answer:
`_flap_words()` falls back to `SPLIT_FLAP_SHORT_WORDS` when the fifteen-character
phrases do not fit, because a board that spells `LINUXS` and `STANDB` never
becomes readable.

A deck with no key displays is refused outright: `_is_visual()` gates it and
`_reject_device()` explains why, once per device. The Stream Deck Pedal has three
keys and no screens, and most of this application is about what gets drawn. When
the driver cannot answer, the deck is **assumed** visual — refusing one we merely
failed to ask about would be worse than trying to draw on it.

**Every broad handler in this module keeps its traceback.** `_try_open()` catches
`Exception` on purpose — it drives a hotplug loop over hardware that fails for a
dozen legitimate reasons — but it logs with `exc_info=True`, because without it a
`TypeError` from our own code reads exactly like a missing udev rule and costs a
bisection to find. `OpenFailureLoggingTests` pins that, and it is mutation-tested.
`_note_open_failure()` also surfaces a status message after
`OPEN_FAILURES_BEFORE_WARNING` consecutive failures on the same device: the
reason used to live only in the log, so the symptom was a deck that silently
never appeared. It waits for a second failure because a deck just plugged in
often refuses the first attempt while it is still enumerating.

Everything above is verified in simulation for every model the library knows
(`tests/test_multi_deck.py`), but only the 15-key original has been run on real
hardware. Supporting more than one deck at once is a different problem: `deck.key`
carries no device identity, `RuntimeKey` has no device field, and `app.py` builds
one manager, one controller and one window rather than a collection.

### Stream Deck + dials and touchscreen

The Plus adds four rotary encoders and an 800x100 LCD strip above them. Both are
gated on `DeckManager.dial_count`, which is 0 on every other model, so nothing
here is reachable on hardware that has none.

**A dial reuses `KeyConfig`** with `KIND_DIAL` and three lists — `steps_left`,
`steps_right`, `steps_press`, all three in `STEP_FIELDS`. That is the whole
reason it is not its own dataclass: every walk over a key's actions and assets
(portable bundles, `nav.page` migration, `rename_page`, the layout sheet) then
reaches a dial without being taught about it. What genuinely differs is storage.
Dials are numbered independently of keys, so they live in `Page.dials`, and
`_key_configs()` plus the bundle exporter iterate that mapping alongside
`page.keys`. `Page.from_dict` drops any index at or beyond `MAX_DIALS`, so a
hand-edited file cannot invent a fifth encoder.

**Transient state uses `DIAL_PATH`**, the folder path `(-1,)`. A real path holds
key indices, which are never negative, so dial 0 and key 0 can never collide in
`RuntimeKey` — and no change to that type was needed.

`_dial_event()` is the one place that knows the library reports a push as a
boolean and a turn as a signed tick count. It drops the release edge (a dial
acts on the way down, like a key) and keeps the tick count, because a fast spin
arrives as several ticks in one event: flattening it would move a volume by one
step no matter how far the hand turned. `turn_dial()` therefore runs the list
once per tick, bounded by `MAX_DIAL_TICKS` so one flick cannot occupy an action
worker long after the hand stopped.

`device/touchscreen.py` draws the strip as one panel per encoder. It is the only
thing that can say what a dial does, so panels are labelled rather than
free-form. `segment_bounds()` derives each panel from the running edge instead
of a rounded per-segment width: four panels of `800 // 4` happen to tile, four
of `801 // 4` leave a one-pixel seam that is plainly visible on black. A tap is
resolved by `touch_segment()` into the dial beneath it and runs that dial's push
list, which is why the strip needs no configuration of its own; a coordinate
outside the strip is discarded rather than clamped.

`ui/dials.py` keeps dials out of `EditorPanel` deliberately. That panel's
unsaved-change guard compares a canonical draft against a baseline for one slot
of the key grid, and a dial is not a slot of that grid; its own dialog leaves
that guard untouched instead of teaching it a second kind of index.

None of this has run on physical hardware — see the note in §1.

### Physical deck startup and connection ordering

`device/startup_animation.py::startup_frames()` creates a 33-frame offscreen
sequence for whatever grid the connected deck has: wake/energy wave, burst,
progressive title, hold, fade and a final black frame. `title_layout()` assigns
the name one character per key in row-major order — on the 15-key 5×3 deck that
is `Linux` / `Strea` / `mDeck` — shortening it through `TITLE_FORMS` rather than
cutting it when the grid is smaller. Frame brightness changes smoothly but
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

### Animated screen saver

`ScreenSaverSettings` persists four top-level configuration values:

| Field | Range/default | Meaning |
| --- | --- | --- |
| `enabled` | `False` | Start automatically after inactivity. |
| `style` | `neon_pipes` | One of the eleven installed animation IDs. |
| `idle_minutes` | 1-1440, default 5 | Delay since the last tracked physical or virtual-deck activity. |
| `intensity` | 5-100, default 35 | Screen-saver brightness, independent of normal deck brightness. |

The available ID/display-name pairs are `neon_pipes` / **Neon Pipes**,
`digital_rain` / **Digital Rain**, `aurora_flow` / **Aurora Flow**,
`orbital_core` / **Orbital Core**, `circuit_pulse` / **Circuit Pulse**,
`ember_field` / **Ember Field**, `hyperspace` / **Hyperspace**,
`matrix_code` / **Matrix Code**, `hal_9000` / **HAL 9000**,
`split_flap` / **Split-Flap Board** and
`linuxstreamdeck` / **LinuxStreamDeck**. An unknown
persisted ID falls back to Neon Pipes. Adding a style means three edits that must
stay together: the tuple in `SCREENSAVER_CHOICES`, the renderer in the
`renderers` table and its entry in `brightness_factors` — a style present in the
first but missing from either table raises a `KeyError` on the frame that
selects it. Nothing else enumerates them: the settings dialog builds its list
from `SCREENSAVER_CHOICES`.

**Ember Field** is procedural on purpose. The obvious way to do fire is the
Doom algorithm, where each frame is derived from the previous one, and **that
cannot work here**: `screensaver_frame()` is a pure function of `elapsed`, so
nothing may accumulate between frames. It instead scrolls three `_noise_strip()`
layers at three speeds under a `_ember_falloff()` gradient and maps the result
through `_ember_palette()` with `point()`, which keeps it all at C level.
`Image.effect_noise()` is equally unusable — it draws from Pillow's own
generator and answers differently on every call. `_noise_strip()` therefore
seeds its own `random.Random`, and repeats the grid's first row and column in
its last so the upscaled tile meets itself without a seam and any vertical
offset can be cropped out of a double-height strip.

Its embers are drawn as an **`L` mask that is blurred and colored afterwards**,
never as blurred RGBA. Blurring RGBA mixes the black of its transparent pixels
into the colour, which turned them into olive rings instead of warm points of
light. The larger glow layers elsewhere get away with it because they are blurred
copies of content in the same hue; small isolated sprites do not.

**Hyperspace** is a wormhole rather than a starfield, and it is built from four
things that each carry part of that: `_hyperspace_tunnel()` draws rings rushing
out of the throat, warped by two out-of-step sine waves and turned further the
deeper they are (`HYPERSPACE_TWIST`) so the throat twists; `_hyperspace_streaks()`
samples each star along its path instead of drawing a straight line, with the
angle turning as it flies (`HYPERSPACE_SWIRL`) so the streak curves the way the
tunnel does; `_hyperspace_aberration()` splits the streak layer into colour; and
a final bloom pass swells with the surge so the jump flares.

Three things there are load-bearing:

- **Star phases and speeds come from `_scatter()`, a multiplicative hash, never
  from a linear function of the index.** The angles are a linear sequence
  already, so a linear phase correlates with them and every star lands on one
  curve — the field collapsed into a spiral at `elapsed` 0, which is exactly
  when the saver starts every time it wakes.
- **Depth is `_hyperspace_depth()`, a power law.** A star crawls near the middle
  and tears past at the rim, which is what stretches it into a streak without
  faking motion blur, and it is what gives the rings their perspective.
- **The whole style composites additively on black.** That is both how light
  behaves and why these layers can be blurred safely: black is the neutral
  value, so no transparent-pixel colour can bleed in the way it did into Ember
  Field's sparks.

Chromatic aberration is applied to the finished streak layer with two resizes
(red scaled out, blue scaled in) rather than by drawing every streak three
times. Keep `HYPERSPACE_ABERRATION` small — the split grows with distance from
the centre, and at 0.013 the streaks near the rim separated into visibly
distinct red and cyan bars instead of fringing.

**Split-Flap Board** is the one style whose grid is the point: one module per
key, so the physical gaps between keys read as the gaps between modules.
`_flap_word()` picks and centres the word, `_flap_state()` returns
`(outgoing, incoming, flip)` for one module, and `_flap_module()` draws it. Both
helpers are pure so the timing can be asserted without rendering.

Its one real trap is what a module shows **at rest**: both halves have to be the
*same* character. The old one only lingers below the seam while the leaf
carrying the new one is still falling — keeping it there when settled spells
every word with mismatched halves, and the board never becomes readable. `flip`
counts down to exactly `1.0` at rest, so `flip >= 1.0` is the test for it.

**Matrix Code** is the only style that draws real characters, which makes it the
only one with a font problem. It wants half-width katakana (`MATRIX_KATAKANA`,
U+FF66-U+FF9D), and no font carrying them is a dependency: `fonts-noto-cjk` is a
`Suggests` of the `.deb`, not a `Recommends`, because apt would then pull 91 MB
of fonts for one screen saver. `_matrix_alphabet()` therefore falls back to
`MATRIX_FALLBACK` (Latin plus digits) and the style always renders.

Detecting a usable font is the subtle part. **`getbbox()` cannot answer it**: a
Latin-only font returns a perfectly good box for any codepoint, because it
measures the empty rectangle it substitutes. `_draws_katakana()` instead draws
the glyph next to an unassigned private-use codepoint and compares the two
renderings; a font that drew the same thing twice has neither, and accepting it
would fill the rain with tofu boxes. Keep that check — the obvious `getbbox()`
version accepts DejaVu.

Two more things there are deliberate. Glyphs are pre-rendered once into cached
grayscale masks (`_matrix_glyph`) that a frame only colorizes, because drawing
text per cell per frame is far too slow for the hundreds of cells a frame paints.
And the film's mirrored glyphs come from flipping the **finished** rain layer
rather than each glyph: that also reverses the column order, which is invisible
because every column is seeded from its own index anyway.

**HAL 9000** is the one style that is a single still object rather than a moving
field: one red camera eye centered on black, whose iris breathes on a slow wave
(`HAL_BREATH_CYCLES`, about nine seconds) and whose center point breathes on a
faster one of its own (`HAL_DOT_CYCLES`), so the two never lock together. Its
`brightness_factors` entry rides the same iris wave deliberately, so the device
breathes with the eye instead of against it. The lens does not move, scan or look
around — what makes it recognisable is that it only ever watches.

Its four layers go on in a fixed order, and the order is the whole trick:

1. **bounce** — a wide, faint wash reaching the rest of the deck, so the keys
   around the eye are lit by it rather than cut out of black. It scales with the
   same `iris_level`, because a spill that does not breathe with what casts it
   reads as a separate light.
2. **lens** — housing rim, iris and pupil, opaque.
3. **glare** — composited **over** the finished lens, not behind it. Behind it,
   any spill bright enough to be seen turned the opaque housing into a black
   cut-out ring, because the glow peaked exactly where the rim is; over it, the
   bloom washes across the rim as a camera sees it and the lens blends into the
   light it is casting.
4. **point** — the center dot and its own glow, brightest and last.

So brightness on this style is not free: raising the bounce lifts the whole deck,
and the tests pin the falloff (eye > neighbour > far key, far key lit but well
under the eye) rather than any absolute value. The settings dialog can start any selection immediately as a preview,
even while automatic activation is disabled and without physical hardware.
Closing or saving stops the preview; only Save persists the four values. The
dialog must unsubscribe its temporary `deck.screensaver` callback on close.

`device/screensaver.py::screensaver_frame()` builds one coordinated canvas for
the complete deck, splits it into per-key RGB images and supplies a frame delay
and brightness. Rendering is pure Pillow under the shared `RENDER_LOCK`; its
title font must use `ImageFont.Layout.BASIC`. Every animated brightness is at
least 1 and no greater than the configured screen-saver intensity. That intensity
does not modify `Config.brightness`.

`DeckManager` owns a dedicated `deck-screensaver` thread and tracks monotonic
idle time. Physical key handling and deliberate virtual-deck entry points call
`record_activity()`; these include selecting or testing a key and opening the
Stream Deck display controls. Do not infer activity from every window event or
attach a broad `Gtk.EventControllerLegacy` hook. While the saver is active,
normal controller/HID key renders are suppressed and frames are sent to both the
physical deck and the virtual grid. A physical wake press and its matching
release are consumed; a later press executes normally. Waking clears preview
state, restores normal brightness, emits inactive `deck.screensaver`, and the
controller refreshes configured key images.

Screen-saver settings are part of normal JSON serialization and `.lsdconfig`
import/export. Import applies them immediately through
`DeckManager.configure_screensaver()` and restarts idle tracking. Shutdown sets
the shared deck stop/wakeup signals, joins the screen-saver thread before the
monitor thread, and proceeds to the configured clean-exit display only after both
have exited.

### Physical display after clean exit

`ExitDisplaySettings` persists `mode` and `image_path` at the top level of the
configuration. The **Stream Deck display** dialog saves one of three modes:

| ID / display name | Clean-exit behavior |
| --- | --- |
| `device_default` / **Device default** | Call the firmware reset so the device supplies its standby image. |
| `blank` / **Off** | Write black to every key, then set brightness to 0. |
| `custom` / **Custom** | Center-crop one image across the full key grid and leave it at normal configured brightness. |

Custom images may be BMP, JPEG, PNG or WebP and are limited to 50 MiB.
`device/exit_display.py::exit_image_tiles()` validates the local file, uses
`ImageOps.fit()` under `RENDER_LOCK` to fill the complete 5-column grid, then
returns one RGB tile per physical key. `blank_exit_tiles()` produces the black
tiles used by **Off**.

`DeckManager.stop()` stops and joins both device workers before
`_close(..., apply_exit_display=True)` writes the selected state and closes HID.
Shutdown during the provisional startup animation also applies the selected
state before closing that device. A missing, invalid or unwritable custom image
falls back to the firmware default. This behavior is guaranteed only for a clean
application shutdown; forced termination, a system crash or power loss may
prevent the final HID writes.

The setting is part of normal JSON persistence. A custom image is bundled in
`.lsdconfig` format v3 and restored below
`CONFIG_DIR/imported-exit-images`; importing applies both its mode and restored
path immediately through `DeckManager.configure_exit_display()`.

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
  `profiles`, `sources_in_scene`, `audio_sources_in_scene`, `filters_of_source`,
  `text_inputs`, `browser_inputs`, `hotkeys`, `pages`,
  `deck_profiles`, `applications`). `pages`, `deck_profiles` and `applications`
  are the `LOCAL_CHOICE_SOURCES` and must stay **above** the `obs.connected`
  guard in `_fetch_choices`, so they still fill when OBS is not running. Note
  that `profiles` means OBS profiles; LinuxStreamDeck's own are `deck_profiles`.
  `sources_in_scene`, `audio_sources_in_scene` and `filters_of_source` **depend
  on another parameter** (`scene`, `scene` and `source`, read with
  `_sibling_value()`), so changing the parent rebuilds them through
  `_repopulate()`. That rebuild replaces the whole widget,
  not just its model: `_choices_known()` decides between a dropdown and a plain
  text field, and a dependent list that was empty when the step was built starts
  out as a text field. Updating only the model left it a text field for good, so
  a scene whose sources appeared later showed nothing until the key was saved
  and reopened. A stored value is always kept selectable (`keep_unknown`), while
  a value inherited from the previous parent is deliberately dropped.
  A dropdown may show something other than what it stores: `_display_options()`
  returns the labels plus a `display -> value` map, kept on the widget as
  `_value_map` and consulted by `_widget_value()`. `hotkeys` is the one source
  that uses it, because `GetHotkeyList` only returns identifiers such as
  `OBSBasic.StartStreaming` and `TriggerHotkeyByName` needs exactly that string
  back. `hotkey_display_name()` in `obs/client.py` derives the readable text, and
  `get_hotkeys()` removes the duplicates OBS emits once per source. The map is
  rebuilt with the widget, so it can never drift from the options on screen.
- `execute(ctx, params)` performs the action; `feedback(ctx, params)` may return
  any of `active`, `color`, `badge` and `display` for live key state. `display`
  replaces the icon with fitted, centered text.
- `ActionContext.key` identifies the executing `(profile, page, key)` when
  available. Use `ctx.for_key(key)` to derive a context with that identity while
  preserving its cancellation controls.
- Set `immediate = True` only for fast, non-blocking state changes. A
  single-action key then executes synchronously on press instead of occupying an
  action worker; multi/toggle sequences still use their normal worker.
- Set `running_feedback = True` on a blocking action that should show the
  controller's temporary `RUN`/breathing feedback even as a single-action key.
- Set `supports_long_press = True` and override `long_press(ctx, params)` when
  holding a single-action key should do something else. Returning `False` means
  "not handled", and the controller runs the normal press instead.
- Set `restart_on_repress = True` when pressing the same profile/page/key again
  should cancel its prior invocation and restart the complete key sequence.
  Long-running actions must cooperate through `ActionContext.stop_requested()`
  or `ActionContext.wait_until_stopped()`, which observe both application
  shutdown and replacement cancellation.
- `apply_default_icons({action_id: "mdi:…"})` assigns default icons after
  registration. Registration happens on import (`app.py` imports the catalogues).

### Countdown timer and stopwatch

The **System** category includes two stateful clock actions:

| ID | Parameters | Press behavior | Default icon |
| --- | --- | --- | --- |
| `sys.timer` | `duration`; optional supported audio `sound`; `volume` 0-100 | Idle shows the configured duration; first press starts, a running press resets, completion stays at `00:00:00`, and a finished press resets/stops its sound | `mdi:timer-outline` |
| `sys.stopwatch` | None | Idle shows `00:00:00`; first press starts and the next resets to zero | `mdi:clock-outline` |

Both actions return their `HH:MM:SS` value through feedback `display`, which the
renderer centers in place of the icon while retaining any label. They set
`immediate = True`, so single-action keys update without entering the action
executor. A timer rejects a non-positive duration. Its optional completion sound
accepts the same `.wav`, `.wave`, `.mp3`, `.ogg`, `.oga`, `.flac` and `.opus`
formats as `sys.audio`, with independently clamped volume.

`ClockRuntime` owns transient timer/stopwatch state under a lock, keyed by
`(profile, page, key)`. Its scheduler checks running clocks every 0.1 seconds but
requests a render only when a displayed second changes, and only the visible
profile/page is repainted. State continues to advance across page/profile
switches. Completion is emitted once; its optional sound runs on the separate
notification executor so it cannot consume action workers.

Drag/drop moves or swaps clock state with its configured key. Editing/saving,
pasting over or clearing a key resets that position and cancels its timer sound.
Profile/page deletion clears all clock state because stored indices shift, as do
configuration import and controller shutdown. Pressing a running timer resets it;
pressing a finished timer also cancels its completion sound.

### Page navigation actions and migration

The visible **Navigation** category contains three explicit actions:

| ID | Parameters | Behavior | Default icon |
| --- | --- | --- | --- |
| `nav.page.next` | None | Next page, wrapping last → first | `mdi:page-next` |
| `nav.page.previous` | None | Previous page, wrapping first → last | `mdi:page-previous` |
| `nav.page.go` | `page`, labelled **Destination page**, `choices_source="pages"` | Named page in the active profile; emits `status` when missing | `mdi:book-open-page-variant` |

The old combined `nav.page` action is not registered or visible. AI proposals
therefore see only the three current IDs. `KeyConfig.from_dict()` transparently
migrates the legacy ID in single actions and every `steps`, `steps_on` and
`steps_off` entry. Legacy `next` / `next page` and `previous` / `previous page`
modes become their parameterless actions; other modes become `nav.page.go` with
the stored page name. Normal serialization writes the new IDs on the next
configuration save or export, so old local configs and imported bundles remain
compatible without preserving the deprecated shape.

Named targets must stay unambiguous within a profile. `add_page()` and
`rename_page()` reject an exact duplicate name and emit `status` without saving.
A successful rename traverses every key on every page in the active profile,
including single, multi and toggle steps, and rewrites matching
`nav.page.go` parameters. Deleting a referenced destination is allowed;
`PageGo.execute()` reports the missing name when the key is pressed.
`set_page_by_name()` returns whether the name exists, while `set_page()` treats
the already active page as a no-op to avoid redundant saves and events.

An empty `KeyConfig.icon` or `icon_off` is an inheritance marker, not a missing
render value. The editor Appearance preview must resolve the same effective
action/default icon as the controller and deck grid, but must keep the stored
reference empty. Clearing an explicit icon therefore restores inheritance instead
of copying the current fallback into config.

### Desktop integration backends

Three modules keep the system-facing work out of the action definitions, so each
action stays declarative and every backend is unit-testable on its own.

`core/apps.py` reads the application list from `Gio.AppInfo`, which is already
available through GTK, so listing and launching need no extra dependency. Keys
store the **display name**; `find_application()` accepts either that or a desktop
id. `running_pids()` uses `pgrep` against the entry's executable, deliberately
ignoring wrapper names (`env`, `sh`, `flatpak`) that would match everything.
Relaunching a running application is what raises its window — the desktop routes
the launch to the existing instance — so there is no separate "activate" path.

`core/media.py` drives transport through MPRIS (`playerctl`) and volume through
the session mixer (`wpctl`, falling back to `pactl`). Never implement these by
faking media keys: MPRIS and the mixer work identically on Wayland and X11 and
need no privileges. Volume raises are capped at 1.0 so a key cannot push the sink
past 100 %.

`core/keystrokes.py` is the only place that injects input. Wayland blocks
synthetic events, so it shells out to `ydotool` (preferred: it writes to uinput
and therefore works on every compositor), `wtype` or `xdotool`. They are
`Recommends` of the `.deb`, never `Depends`: apt installs them by default while
the package still installs on a distribution that lacks them, and `backend()`
returns `""` when none is present so the action reports what to install.

**The two ydotool generations are incompatible.** 0.x — still what Debian and
Ubuntu ship — takes a key sequence (`ydotool key ctrl+c`), while 1.x takes
press/release event codes (`ydotool key 29:1 46:1 46:0 29:0`). Never assume one:
`ydotool_syntax()` detects it from the help text and caches the answer. A key is
therefore held canonically as its **Linux input name** (`esc`, `dot`, `sysrq`,
`leftbrace`), with `_KEY_ALIASES` accepting what a user might type, `_KEY_CODES`
giving the 1.x codes and `_XKB_NAMES` the X keysyms for the other two backends.
Presets are adapted to Linux — Windows-only entries of the original catalogue are
dropped — and always stay editable, because desktops rebind them.

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

`AIKeyDialog` is laid out around the fact that its configuration is set up once
and its description field is used every time. Provider, model, API key and the
context switch live inside one `Adw.ExpanderRow`, and the primary action sits in
an `Adw.ToolbarView` bottom bar rather than at the end of the scrolled page.
Measured at the default 580x820, that took the request page from 936 px of
content in a 773 px viewport — description field starting 487 px down, Generate
button entirely below the fold — to 606 px in a 727 px viewport, which needs no
scrolling at all.

Four rules keep it from feeling like something is hidden or moving:

- **The folded row restates the whole configuration** in its title and subtitle
  (`_refresh_summary()`): provider, model, key state, context and billing. A bare
  "Settings" row is the version that leaves people hunting.
- **The opening state is decided once**, in `_settle_opening_state()`, from what
  the keyring holds: folded with the description focused when
  `_configuration_ready()`, expanded with the API key focused when not. It is
  guarded by `_settings_settled` so a later lookup — a provider switch triggers
  one — can never fold the settings away while they are being used.
- **Validation opens what it complains about.** `_generate()` expands the row
  before reporting a missing API key, or the message points at a field nobody
  can see.
- **`_show_page()` moves the content stack and the action stack together.** The
  bottom bar is shared by both pages, so switching only one leaves the wrong
  buttons under the wrong page.

The reassurance under **Describe the key** — that a proposal never runs or saves
anything — stays outside the expander on purpose. It is a safety statement about
what the feature does, not configuration, so it must be readable without
expanding anything.

**A group's children are not items of the scene.** OBS reports a group as one
scene item and hides everything inside it: `GetSceneItemList` never mentions the
children, and `GetSceneItemId` against the scene fails for them, because OBS
addresses them with the **group's** name in place of the scene's. Both halves of
that have to be handled together, or the feature is broken either way round:

- `get_sources_in_scene()` walks each `isGroup` item through
  `GetGroupSceneItemList` and lists its children after it, keeping the group
  itself selectable and de-duplicating a source that sits in two groups. Without
  this the editor offered only the group, so nothing inside it could be picked.
- `find_scene_item()` returns the **container** that actually holds a source,
  not the scene, trying the scene first and then its groups. Every later
  scene-item request must be addressed to that container. Listing nested sources
  without this would offer sources that then fail on press.

OBS does not allow a group inside a group, so one level is the whole tree.
`get_audio_sources_in_scene()` is built on the same walk and inherits the fix.

The **Audio** actions scope their input list to a scene:
`OBSClient.get_audio_sources_in_scene()` takes the scene's items, adds the
special inputs from `GetSpecialInputs` (Desktop Audio, Mic/Aux belong to no
scene yet are audible on all of them) and keeps only the ones that answer
`GetInputMute`, since OBS has no request for "the audio sources of a scene".
Their `scene` parameter is an **editor filter only**: it defaults to `""`, is
never sent to OBS, and the actions still target the input globally, so keys
saved before it existed keep working untouched.

The optional generation context is an explicit user opt-in. It contains only
bounded OBS and page names collected by `collect_generation_context()`; never
send passwords, commands, the full configuration, or other secrets. The action
catalogue exposed to a provider always excludes `sys.command` and `obs.raw`.

A provider response is untrusted data. It must pass local structural, action,
parameter, choice and icon validation before becoming an `AIProposal`. Generation
must never execute or save a key. The user first previews the proposal, explicitly
loads it into the existing editor, reviews it, and presses **Save** to persist it.

A proposed step may carry an optional `label`, the same descriptive name the
editor's step list shows. `_step_label()` treats it as what it is — text that
names a row and is never matched against an action — so it only bounds it to
`MAX_STEP_LABEL_CHARS` and flattens it to one line; a missing one is simply
empty, and a non-string is rejected. The action id alone still decides what
runs, so no label can select or reach an action. `format_proposal()` prints the
name **and** its action (`1. Cut to camera — Switch scene`), so the preview can
never hide behind a friendly name what the key would actually do.

### Config / key model

`Config` holds `profiles` and delegates `pages`/`current_page` to the active
`Profile`. Each `KeyConfig.kind` is one of:

- `single` (`KIND_SINGLE`) — one action, with state feedback.
- `multi` (`KIND_MULTI`) — an ordered list of `ActionStep`s run in sequence;
  pauses are an explicit `Wait` action (`sys.wait`, a `duration` param), not a
  per-step delay.

Each `StepList` row is a `Gtk.Expander` whose **title is a widget**
(`set_label_widget`): a drag handle, the hexpanding title label and the move /
copy / remove buttons, so none of them needs the step to be opened first.
Rows also reorder by drag and drop, with one source/target pair per row in the
**bubble** phase — capture would swallow the clicks of those very buttons and
the expander's own toggle. Each row must also drop GtkExpander's **own**
`GtkDropControllerMotion` (`_disable_hover_expand()`): it exists to spring open
a tree node after a hover, so without removing it the row under the pointer
opens itself mid-reorder. The remove button uses the `.step-remove` class rather
than `.destructive-action`, because the latter paints a background that `.flat`
drops, leaving the icon in the normal text colour instead of red. The payload is the internal typed string
`linuxstreamdeck-step:<index>`, validated against the drag in progress exactly
like the key grid's, and a drop **moves** the step to that position rather than
swapping two. Because the title is a widget, renaming a row sets
`editor._title`, not `Gtk.Expander.set_label()`; `step_title()` reads it back.

`StepList` rebuilds all of its rows whenever one is added, pasted, moved or
removed, which makes two things easy to get wrong. It must call
`_remember_expansion()` first, so each row keeps the state the **user** gave it
rather than the one it was created with — otherwise adding a step silently
reopens rows that were collapsed. And it must unparent each `StepEditor`
explicitly before re-wrapping it, instead of relying on the previous expander
being garbage collected: anything still holding that expander (a queued reveal,
a handler) leaves the editor attached to it and the rebuild fails with
`gtk_box_append: assertion 'gtk_widget_get_parent (child) == NULL' failed`.
`_reveal()` scrolls a newly added or pasted step into view on a low-priority
idle and resolves the expander **late**, so a rebuild in between scrolls to the
current row. Reordering and removing instead go through
`_rebuild_keeping_scroll()`, and `_move()` hands focus back to the same arrow on
the row's new position: `GtkViewport:scroll-to-focus` is on by default, so the
button being destroyed mid-click is what threw the list back to the top. That
grab must be wrapped in a callback returning `False`: `grab_focus()` answers
`True` when it succeeds, and `GLib.idle_add(button.grab_focus, …)` therefore
kept the source alive and re-ran it for the rest of the session. Note that a
collapsed `GtkExpander` keeps its child out of the traversable widget tree, so
tests must walk down from `get_child()`.

### Copying a step between action lists

`STEP_CLIPBOARD` (a module-level `_StepClipboard` in `ui/steps.py`) holds **one**
copied `ActionStep`. It cannot live on a `StepList`: `EditorPanel._build_body()`
rebuilds every list whenever the selected key or the key type changes, so a copy
kept there could never reach another list, let alone another key. It stores and
returns a fresh copy each time, so pasting twice cannot share one `params` dict,
and it refuses a step with no action.

The row header's copy button only fills that clipboard; nothing is inserted
until a paste. Right-clicking a **row** offers everything that row can do —
*Copy action* and *Paste action*, then *Move up* / *Move down* / *Move to top* /
*Move to bottom*, then *Remove action* — with `None` between the groups standing
for a separator. A paste lands at that row's index, pushing it down
(`_add(..., position=)`); the move entries reuse `_move()` and `_reorder()`, and
each is disabled where it would do nothing, so the only row of a list can only be
copied, pasted onto or removed. Right-clicking the **list** beside its rows
offers only *Paste action*, at the end. A right click inside a parameter entry
reaches neither: the entry claims it first and shows its own text menu.

While a row menu is open its row carries the `menu-target` CSS class, because the
popover covers part of the list and the entries act on a row the pointer has
already left. `_set_menu_row()` holds that row as a **widget, not an index**: an
entry may reorder the list, and the highlight has to come off whatever it went
on. `_ContextMenu.show(..., on_close=)` clears it however the menu ends —
choosing an entry, dismissing it, or a `_rebuild()` closing it.

A pasted row arrives **collapsed** — `_add(..., expand=False)` followed by an
explicit `_reveal()`. It is scrolled into view but never opened: the copy is
already configured, so expanding it would only push the rest of the list out of
sight. Adding a *new* step still opens, because that one has to be filled in.

Both gestures are in the bubble phase and both see the same click, so the row's
answer has to win twice over. `_on_row_menu()` claims the sequence, which should
already deny the list's gesture, **and** raises `_row_menu_click`, which
`_on_list_menu()` checks before showing anything; it is cleared on the following
idle, so the next click works normally. Do not drop the flag and rely on
claiming alone, and do not resolve the row from `Gtk.Widget.pick()` instead:
`pick()` answers `None` on an unmapped widget, which makes that path impossible
to verify without showing a window. `row_menu_items()` / `list_menu_items()` /
`StepEditor.menu_items()` build the entries as plain `(label, enabled, callback)`
tuples, so a test can assert them without popping up a popover.

A single-action key has no row to right-click, so `EditorPanel` calls
`StepEditor.enable_context_menu()` on its editor; pasting there replaces the
action and its parameters, and drops the step name, which only a list can show.
That is opt-in precisely because a `StepEditor` inside a list must let the right
click bubble to its row.

The same copy can also become a whole key: the grid's context menu carries
**Paste action** (`win.key-paste-action`), enabled from `STEP_CLIPBOARD`, which
`MainWindow._paste_action()` turns into a `KIND_SINGLE` `KeyConfig` and applies
through the usual `DeckController.paste_key()`. It goes through the same guards
as pasting a key — the unsaved-change dialog with `offer_save=False`, and
`_replacing_folder()` — and it refuses the reserved Back slot. It drops the step
name for the same reason the single-action editor does, and it leaves the
clipboard filled, so one action can be dropped onto several keys in a row.

`_ContextMenu` keeps at most one popover, parented to the row it was opened on.
Since every `StepList` rebuild destroys those rows, `_rebuild()` closes it first,
and `close()` is idempotent — it also runs from the popover's `closed` signal, so
dismissing the menu by clicking away cannot strand it either.

Every `ActionStep` also carries an optional `label`: a name shown in the editor's
step list instead of the action name, so a long sequence stays readable. It is
purely descriptive — nothing at run time reads it — and it is stripped of
surrounding blanks, so a name of only spaces falls back to the action name.
`StepEditor(show_label=True)` draws the field, which `StepList` sets and the
single-action editor does not: a single-action key has no list entry to name, and
its own label already lives in Appearance. Because the field is outside
`params_box`, a name survives changing the step's action. Old configurations
without the field load with an empty label, and older builds ignore it, so no
bundle version changes.
- `multi_toggle` (`KIND_TOGGLE`) — two lists (`steps_on` / `steps_off`) with an
  ON/OFF state; the state is keyed by `RuntimeKey` in the controller.
- `random` (`KIND_RANDOM`) — reuses `steps`, but a press runs exactly one entry
  chosen with `random.choice`.
- `press` (`KIND_PRESS`) — `steps_single`, `steps_double` and `steps_long`, one
  per press gesture.
- `folder` (`KIND_FOLDER`) — holds a `Folder` of nested keys in `folder` and runs
  no action at all; see **Folder keys** below.

`STEP_FIELDS` lists every field holding `ActionStep`s. Anything walking a key's
actions — portable bundles, legacy migration — must iterate it rather than
hard-coding the three original names, or the new lists silently lose their
bundled audio and their `nav.page` migration. For the same reason it must walk
nested folder keys with `Config._walk_keys()` / `_walk_raw_keys()`.

### Undo, action search and key search

`DeckController` keeps a bounded history (`UNDO_DEPTH`) of the key changes that
can be taken back: `clear_key()`, `paste_key()` and `swap_keys()` each record
what the affected slots held before they ran. Only the `KeyConfig`s are kept,
never transient state — an undone key starts from a clean runtime state exactly
as a pasted one does.

The history is **scoped to the grid on screen**. `can_undo()` requires the top
entry's container to be the current one, and `forget_undo()` drops it on every
page, profile and folder change and on deleting a page or profile or importing a
configuration. Restoring into a grid the user has left would be invisible, and
inside a folder the same index is a different key entirely. `MainWindow` routes
undo through the usual unsaved-change guard, since it may replace the key the
editor is showing.

`ui/action_picker.py` searches all registered actions at once — name, category,
description and id, terms ANDed — because the two chained dropdowns need the
category to be known before the action can be found. Results starting with the
query rank first, so Enter picks the obvious one.

`core/search.py` finds a key anywhere in the configuration. `locations()` walks
every profile, page and nested folder yielding a `KeyLocation` with enough to
navigate back; `key_terms()` builds the haystack from labels, action ids **and
their registered names**, step names and parameter values, so a key is found by
what it does rather than by where it is. `key_steps()` deliberately does not
descend into a folder: nested keys are yielded by the walk itself, and crediting
a folder with its contents would make every folder match every search.

### Press gestures

`LONG_PRESS_SECONDS` (0.5) and `DOUBLE_PRESS_SECONDS` (0.35) live in
`core/controller.py`. `_on_deck_key` now routes both edges: `key_down()` and
`key_up()`. `_gesture_mode()` decides what a key needs — `"press"` for
`KIND_PRESS`, `"long"` for a single-action key whose action sets
`supports_long_press`, and `""` for everything else, which keeps running on the
press exactly as before.

A gesture key resolves on release: held past the long threshold runs `steps_long`;
otherwise a `threading.Timer` waits out the double-press window, and a second
release inside it cancels that timer and runs `steps_double` instead of
`steps_single`. Pending timers must be cancelled whenever the key's meaning
changes — `key_config_changed()`, `swap_keys()`, `_clear_time_actions()` (page or
profile change) and `shutdown()` all call into `_cancel_gesture()` or
`_clear_gestures()`, because a stored index refers to a different key afterwards.

A virtual press has no release to time, so `press()` runs `steps_single` directly;
the editor says so. It also accepts an explicit `gesture` (`GESTURE_SINGLE` /
`_DOUBLE` / `_LONG`, resolved by `gesture_steps()`), which only the editor's
Test buttons pass: naming a gesture is a different thing from making the virtual
deck wait out the double-press window, which it must never do. `Action.long_press(ctx, params)` returns `False` to decline,
and the controller then falls through to a normal press, which is what makes
"Nothing" behave exactly like a short press.

### Status icon and application lifetime

GTK 4 has no tray API, so `ui/tray.py` publishes the icon directly on D-Bus with
`org.kde.StatusNotifierItem` plus `com.canonical.dbusmenu`, over
`Gio.DBusConnection`. It adds no dependency and works on COSMIC and KDE natively,
and on GNOME with an AppIndicator extension. The module creates no GTK widgets;
D-Bus handlers run on the GTK main thread, and every user action is still routed
back through `GLib.idle_add`.

`TrayIcon.start()` exports both objects, owns an
`org.kde.StatusNotifierItem-<pid>-1` bus name and then watches the watcher name,
so the icon survives a panel restart and can be published before the status area
exists. `is_supported()` is only informational: `app.py` must start the icon
even when it returns `False`, because a session that is still logging in — the
normal autostart case — very often has no status area yet, and the name watcher
publishes the icon the moment one appears. `RegisterStatusNotifierItem` is called
**asynchronously**; it runs on the GTK main thread, so a synchronous call would
freeze the window whenever the panel is slow. Until the reply arrives the icon
counts as unregistered, which is the safe direction. The menu is **Open**, a **Profile** submenu of radio entries and
**Quit**; `ItemIsMenu` is true, so a plain click opens it. `menu_items()` and
`build_layout()` are pure functions, which is what the tests exercise. Profile
entry IDs start at `PROFILE_ID_BASE` so they never collide with the fixed ones.
`app.py` subscribes to `profile.changed` and calls `refresh()`, which bumps the
revision and emits `LayoutUpdated`; that one event already covers adding,
renaming, deleting and switching a profile.

`Config.close_action` is `tray` (default) or `quit`.
`LinuxStreamDeckApp.hides_on_close()` is the single decision point and requires
all three of: no explicit quit in progress, the configured `tray` action, and a
**registered** icon. A missing or unregistered status area therefore always
falls back to quitting, so the window can never vanish with no way back.
`MainWindow` hides instead of closing by returning `True` from `close-request`;
the window is only hidden, never destroyed, so `Adw.Application` keeps the
process alive and the whole session — including an unsaved key draft — survives.
Hiding deliberately asks nothing.

Quitting and switching profiles from the icon go through `MainWindow.request_quit()`
and `request_profile()`, which reuse the normal unsaved-change guard and present
the window first when a confirmation is needed, because that dialog is modal to
it. `app.quit()` sets the explicit-quit flag before `Gtk.Application.quit()` so
the hide rule cannot intercept it, and `_on_shutdown` stops the icon before the
controller, deck and OBS client.

`core/autostart.py` owns the XDG entry at `~/.config/autostart/<APP_ID>.desktop`
(overridable with `LSD_AUTOSTART_DIR` in tests). Its state lives in that file and
never in `config.json`: an exported configuration must not enable autostart on
another computer, and the desktop's own startup tool may disable the entry, which
`is_enabled()` honours by checking both `Hidden` and `X-GNOME-Autostart-enabled`.
The entry runs the installed console script when there is one, falling back to
the current interpreter, and always adds the `--hidden` flag that
`strip_hidden_flag()` removes before GTK parses argv. A hidden start only skips
presenting the window when the icon actually registered.

### Folder keys

A `KIND_FOLDER` key opens its own grid instead of running anything, so related
actions group without spending a page. Its contents live in `KeyConfig.folder`
(a `Folder`), its name is the key's own `label` (`folder_name()` falls back to
`DEFAULT_FOLDER_NAME`) and its icon is the key's `icon`, defaulting to
`DEFAULT_FOLDER_ICON`. `Page` and `Folder` both extend the plain `KeyGrid` mixin,
which supplies `key()`, `set_key()` and `configured_keys()`; the mixin declares no
dataclass fields, so both keep their own field order and JSON layout.

Three rules make the shape safe:

- **`folder` is `compare=False`.** Its contents are edited by navigating inside
  and saved there, so the editor's unsaved-change comparison must not read an
  edit made *inside* a folder as a pending edit of the folder key itself.
- **Slot `FOLDER_BACK_INDEX` (0) is reserved.** Inside a folder it always renders
  the Back key (`_back_spec`, showing the folder name), it is never stored, and
  `is_reserved_key()` blocks selecting, dragging, dropping, pasting, clearing and
  swapping it. The physical deck can therefore never enter a folder it cannot
  leave. Folder contents keep the deck's own numbering, so no index shifts.
- **Nesting stops at `MAX_FOLDER_DEPTH` (5).** `_folder_contents()` drops the
  contents of anything deeper while keeping the key itself, so one hand-written
  branch cannot cost the whole configuration or make loading recurse without end.
  `can_add_folder()` and `fits_here()` apply the same limit to new folders and to
  pasted or imported subtrees.

`DeckController._folder_path` is a tuple of key indices from the page root, and
`container` resolves it into the grid on screen, self-healing back to the page
when a step stops resolving. It is **view state, never configuration**: opening
or closing a folder saves nothing, and `_leave_folders()` returns to the page
root on every page, profile and configuration change, because those indices would
otherwise address a different grid. `folder.changed` carries `path` and the
`folder_trail()` of `(path, name)` pairs that the window renders as a clickable
breadcrumb.

On the virtual deck a folder opens by **double-clicking** its key. `MainWindow`
times that from the button's own `clicked` signal (`_last_key_click` plus the
pure `_completes_double_click()`), never from an extra `Gtk.GestureClick` on the
key button: a `GtkButton` claims the primary-button sequence on press, which
cancels any other primary gesture on the same widget, so such a gesture never
reaches `n_press == 2`. The secondary-button context menu on those same buttons
is unaffected, because `GtkButton` never claims that sequence. The second click
is consumed, so a third one starts a new pair instead of reopening the folder
just entered.

Transient state therefore keys on `RuntimeKey` — `(profile, page, folder path,
key index)` — so the same index inside a folder is a different key from the one
on its page, and clocks inside a folder keep running while you are elsewhere.
`_discard_folder_state()` drops every toggle, clock, timer sound, execution
control and gesture under a folder slot when that folder is cleared, pasted over
or moved; editing the folder key itself keeps them, so renaming a folder does not
reset the timers inside it. Pressing a folder key calls `open_folder()` directly
and never reaches an action worker.

Anything walking a key's actions or assets must recurse with `_walk_keys()` /
`_walk_raw_keys()`: `_key_configs()`, both bundle exporters (through the shared
`_bundle_key_icons()` / `_bundle_key_audio()`), `import_key_bundle()` and
`rename_page()` all do. Missing one silently loses a folder's nested audio,
icons or `nav.page.go` rewrites.

The editor offers **Folder** as a key type, hides it once the depth limit is
reached, and locks the whole type list while the stored key is a folder that
holds keys, so its contents cannot be replaced by switching type. Clearing,
pasting over or importing onto a non-empty folder asks for confirmation first.
The AI catalogue never exposes folders: `ai/service.py` still restricts proposals
to single, multi and toggle.

### Key label font size

`KeyConfig.font_size` and `KeyConfig.font_size_off` hold one of the IDs in
`KEY_FONT_SIZE_CHOICES`: `""` / **Automatic**, `xs` / **Extra small**, `s` /
**Small**, `m` / **Medium**, `l` / **Large**, `xl` / **Extra large**. Like an
empty `icon`, the empty value is an inheritance marker, not a missing value:
`renderer._label_font_size()` then derives the size from the key height
(`max(10, h // 6)`, the historical automatic size). Every named ID is a divisor
of the key height in `FONT_SIZE_DIVISORS`, so a chosen size keeps its proportion
on any key geometry, and no resolved size drops below `MIN_FONT_SIZE`.

`KeyConfig.from_dict()` normalizes the stored value and falls back to automatic
for anything unknown, so old configurations and hand-edited files stay loadable.
The controller passes `font_size` through every `_key_spec` variant; a toggle's
OFF state uses `font_size_off or font_size`, mirroring how `label_off` falls back
to `label`. The badge keeps its own fixed size, and because the label box grows
with the font, the icon and any centered clock value shrink to fit.

### Key label colour

`KeyConfig.text_color` / `text_color_off` follow exactly the same inheritance
rule: empty means the renderer's own `TEXT_COLOR`, so a key that never chose one
renders identically and clearing the choice restores inheritance rather than
freezing today's default into the configuration. `_text_color()` validates the
stored value to `#rgb` / `#rrggbb` at load, because a hand-edited file would
otherwise reach Pillow mid-render, on a worker thread, once per repaint.

`compose()` resolves it once into `ink` and uses it for **the label, a centered
value such as a clock, and the badge**. The badge is included on purpose:
leaving it white would preserve the very problem the setting exists for, since a
pale background hides a white badge exactly as it hid the label.

`AppearanceBox` blocks its own `notify::rgba` handler while showing the
inherited colour. Without that, displaying the default is indistinguishable from
the user picking it, and clearing the choice would immediately write the current
default back into the key.

### Reordering and duplicating pages and profiles

`move_page()`, `duplicate_page()` and `duplicate_profile()` all shift what a
stored index means, so each clears the transient toggle/clock state and the undo
history exactly as `delete_page()` does. What they must not disturb is
`nav.page.go`, which targets a page **by name**: moving a page changes no name,
and `Profile.clone()` keeps every page name so a copied profile's navigation
points inside the copy rather than back at the original. `unique_name()` supplies
the "Live copy", "Live copy 2" sequence — duplication is the one operation where
a clashing name is guaranteed rather than possible, so the user is never asked.

`_shifted_index()` keeps the page the user is looking at on screen after a move;
the arithmetic is worth keeping because every off-by-one there silently switches
the visible page.

### Redo

`_apply_entry()` restores an undo entry and returns the entry that reverses it,
which is what makes undo and redo mirror images: a change can be walked back and
forward any number of times without the two stacks drifting apart. `_redo` is
bounded by the same `UNDO_DEPTH`, cleared by `_record_undo()` (a new change
branches away from the undone future) and by `forget_undo()`, and gated by
`can_redo()` on the same container check as `can_undo()`.

### Layout sheet

`device/layout_sheet.py` composes a whole profile into one printable PNG: every
page, then every folder inside it, with each key drawn by the real `compose()`
and captioned with what it does. The caption is the point — an icon-only key is
unidentifiable away from the hardware. It takes `columns`/`rows` from the
connected deck rather than assuming 5x3, and it deliberately never consults live
OBS feedback, or the same profile would render differently every time.

`_draw_caption()` trims against the room left **once the ellipsis is paid for**.
Testing whether `text + "…"` fits on each pass never succeeds: by then the bare
text fits too, the loop ends, and the caption is left cut mid-word with nothing
to show it was shortened.

### Rotating configuration backups

Alongside the single-step `config.json.bak`, `rotate_backups()` keeps up to
`BACKUP_KEEP` timestamped copies under `CONFIG_DIR/backups`. `BACKUP_MIN_INTERVAL`
is the whole point of it: saves are frequent — a page switch, a brightness change
and every key save is one — so without a floor between snapshots a burst of saves
would push every older state out of the history at exactly the moment it becomes
worth having. `import_bundle()` passes `force=True`, because replacing the entire
configuration is precisely when the previous one is worth keeping. Rotation never
raises: losing a backup must not stop the save it precedes.

### Keyboard navigation of the key grid

`neighbour_index()` walks the deck's own numbering, so left and right lead from
the end of one row into the start of the next — the grid is one sequence of keys
drawn in rows, not a set of independent rows. Up and down stop at the edges;
wrapping them would throw the focus to the far end of the deck.

Focus is **not** selection. Selecting runs the unsaved-change guard, so moving
the focus with an arrow key would raise a dialog per key press; activating a key
still selects it. `_on_grid_key_pressed()` returns `True` even at an edge,
because falling through hands the focus to GTK's own directional search and
leaves the grid entirely. It is a scoped, bubble-phase `Gtk.EventControllerKey`
on the grid — not the broad `Gtk.EventControllerLegacy` hook that §5.15 forbids.

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

Controller shutdown order is deliberate: set the stopping signal, stop and clear
the clock runtime/completion sounds, shut down the action executor, shut down the
timer-notification executor, join the activity thread, then shut down the render
executor. `Wait` observes both the stopping signal and per-invocation cancellation
through its `ActionContext`, so shutdown and same-key replacement can end it
promptly. Do not merge the executors or reorder teardown in a way that lets
actions, clocks or activity submit work after their destination executor has
stopped.

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

### Grid drag and drop

`MainWindow` owns one `Gtk.DragSource` / `Gtk.DropTarget` pair on the entire key
grid, not one pair per button. Both controllers use
`Gtk.PropagationPhase.CAPTURE`, the source accepts only
`Gdk.BUTTON_PRIMARY`, and the target enables preload. This lets the grid claim
the gesture before a child `Gtk.Button` or `Gtk.Picture` consumes it.

Drag data is an internal `GObject.TYPE_STRING` value prefixed with
`linuxstreamdeck-key:`. Preparation resolves the source from the pointer with
`Gtk.Grid.pick()` and walks parent widgets until it finds the owning key button.
An empty key returns no content provider and therefore cannot start a drag.
Motion and drop resolve the destination the same way, so configured keys can move
upward or downward into any empty slot, or swap with any occupied slot.

The drop handler must decode the typed payload and require its source index to
match the active drag. Foreign, malformed, stale, outside-grid and same-key drops
are rejected. CSS classes dim the source and subtly highlight the current valid
destination. Destination feedback clears on leave/drop, and drag end clears all
remaining feedback. A valid drop still goes through the unsaved-change
confirmation before `DeckController.swap_keys()`; the key configuration and
transient toggle/clock state travel together, and selection follows the
destination. Copy/paste remains a separate context-menu/shortcut operation.

Persistence is atomic JSON with user-only (`0600`) permissions at
`~/.config/linuxstreamdeck/config.json`, with a `config.json.bak` backup written
on every save. Loading migrates both the old single-profile format and legacy
combined `nav.page` actions before the model reaches the editor/controller. The
OBS password is stored asynchronously in Secret Service / GNOME Keyring, never in
either JSON file. On first run, legacy plaintext password fields are migrated and
removed from both files; if Secret Service is unavailable, they are still removed
and the password is session-only. OpenAI and Claude API keys are also stored only
in Secret Service, under separate provider identities; AI preferences in config
never contain either key.

The profiles menu exports `.lsdconfig` ZIP format v4 with the full JSON
configuration, including screen-saver and clean-exit display settings, available
custom key icons, the selected custom exit image and supported files referenced
by `sys.audio` or the `sound` parameter of `sys.timer`; identical audio content
is deduplicated even across both actions. Audio is limited to 200 MiB per file
and 500 MiB total, while the exit image is limited to 50 MiB. Built-in `mdi:`
icons stay as references, and OBS passwords and provider API keys are never
exported. `close_action` travels with the configuration like `brightness` does,
but the autostart entry never does: it stays local to each computer. Missing, unreadable, unsupported or oversized portable files remain
local references and produce an export warning.

Import accepts every version in `SUPPORTED_EXPORT_VERSIONS` (v1 to v4),
validates archive member paths and size
limits, restores bundled icons below `CONFIG_DIR/imported-icons`, audio below
`CONFIG_DIR/imported-audio` and the exit image below
`CONFIG_DIR/imported-exit-images`, replaces the complete configuration and writes
the prior configuration to `config.json.bak`. It keeps the destination
computer's keyring credentials and ignores password fields in old exports, so an
OBS password must be entered once after moving to a new computer. The controller
applies imported normal brightness, screen-saver and clean-exit display settings
immediately before reconnecting OBS.

### Portable single-key bundles

The key context menu exports one key as a `.lsdkey` ZIP through
`Config.export_key_bundle()` and reads it back with `Config.import_key_bundle()`.
Both are classmethods that reuse the configuration bundle's archive layout,
`EXPORT_ICON_PREFIX` / `EXPORT_AUDIO_PREFIX` rewriting, size limits, member
validation and atomic `_write_bundle_archive()` writer. The manifest carries its
own `KEY_EXPORT_FORMAT` (`linuxstreamdeck-key`) and `KEY_EXPORT_VERSION`, so a
full `.lsdconfig` cannot be imported as a key and a `.lsdkey` cannot be imported
as a configuration; the key itself lives in `key.json`. Both formats were raised
when folder keys landed (bundle v4, key v2) so an older application refuses the
file instead of silently dropping whole folders; the loaders still accept every
earlier version.

Export never mutates the source `KeyConfig` (it works on an `asdict()` copy),
bundles only that key's custom icons plus `sys.audio` / `sys.timer` audio,
deduplicates identical audio across both actions and reports missing files as
warnings instead of failing. Import validates paths and sizes before writing,
restores assets below `CONFIG_DIR/imported-icons` and `CONFIG_DIR/imported-audio`,
runs the value through `KeyConfig.from_dict()` so legacy `nav.page` actions still
migrate, and returns the key without saving. `MainWindow` then applies it through
`DeckController.paste_key()`, which resets that position's toggle/clock state and
persists the configuration. Replacing the key currently open in the editor goes
through the unsaved-change guard with `offer_save=False`, exactly like paste.

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

The four whose violation is *silent* — nothing crashes, nothing asserts, and the
symptom only appears on some launches or under concurrency — are guarded by
`tests/test_invariants.py`. That file tests no feature: it exists so these rules
fail loudly instead of being enforced by prose alone. Each guard is
**mutation-tested**, meaning it has been confirmed to fail when its invariant is
deliberately broken; if you change one, re-confirm that, because a guard that
cannot fail is worse than no guard. When mutation-testing by hand, purge
`__pycache__` between runs: bytecode is invalidated on source mtime *and size*,
so a revert that restores the same byte count within the same second is silently
ignored and you will chase a ghost.

1. **Pillow text layout must be BASIC.** Pillow's pip wheel bundles its *own*
   harfbuzz; used alongside GTK/Pango's system harfbuzz it intermittently draws
   **blank glyphs** (random per process — some launches all icons fine, others all
   blank). The fix, which must stay in place, is
   `ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.BASIC)` in
   `core/icons.py`, `device/renderer.py`, `device/startup_animation.py` and
   `device/screensaver.py`. Do **not** use raqm, `anchor="mm"`, or oversized
   fonts for glyphs (the latter caused giant masks →
   `DecompressionBombWarning` + blank keys). Configured-key glyph centering uses
   `textbbox` + ink-bbox recenter; the startup title centers each character from
   its text bounding box within one hardware-key cell, and the screen-saver title
   uses the same BASIC-only discipline. User-selectable label sizes stay bounded
   for the same reason: keep them expressed as `FONT_SIZE_DIVISORS` of the key
   height rather than absolute point sizes, so no choice can grow into an
   oversized mask.
   *Guarded by* `BasicFontLayoutTests`, which records every `ImageFont.truetype`
   call made while rendering an icon, key images, four screen-saver styles, the
   startup sequence and the exit tiles, and fails if any of them omitted
   `layout_engine`. Clear the cached font loaders first or a patched loader is
   never reached.

2. **Rendering is not thread-safe → `RENDER_LOCK`.** Pillow/FreeType is not
   thread-safe. Configured keys render on a worker, the screen saver renders on
   its own thread, and the icon picker/preview render on the main thread;
   concurrent use produced blank (and blank-cached) glyphs. A shared reentrant
   `RENDER_LOCK` (defined in `core/icons.py`, imported by `device/renderer.py`,
   `device/startup_animation.py`, `device/screensaver.py` and
   `device/exit_display.py`) serializes drawing. It is reentrant so `compose()`
   can call `library.render` without deadlocking. The glyph cache is manual and
   **never caches failures** (safety net).
   *Guarded by* `RenderLockTests`, which swaps the lock for a depth-tracking
   proxy, patches `ImageDraw.Draw` to record anything drawn at depth 0, and also
   pins that all five modules hold the *same* object: each imports it by value,
   so a second lock would serialize nothing while still looking correct.

3. **OBS requests must be fully serialized.** `obsws_python.ReqClient` uses a
   single websocket that is **not** thread-safe. In `obs/client.py` the `_lock`
   must be held for the **entire** `req.send(...)`, not just while reading the
   client pointer. Requests come from two threads at once (the GTK thread filling
   editor dropdowns, and the render worker for `feedback()` calls like
   `obs.source_visibility` / filters that query OBS live). Overlap corrupts the
   protocol → hang at ~73% CPU and disconnect.
   *Guarded by* `ObsRequestSerializationTests`, the only test that builds a real
   `OBSClient` rather than a fake: a stub `send` blocks while a second thread
   tries to get in, and a semaphore fails the test if two calls are ever inside
   `send` at once, so it catches the overlap even if the timing shifts. It also
   pins that a raising `send` releases the lock instead of wedging every later
   request.

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

8. **Action work must not starve running feedback.** Keep action, timer-sound and
   render executors separate, count every queued/running invocation per
   profile/page/key, and pulse only busy keys in the active view. Preserve the
   complete controller shutdown order: set the stopping signal; stop the clock
   scheduler; clear clock state and signal completion sounds; wake activity and
   clear the pending-render marker; shut down the action executor; shut down the
   timer-sound executor; join the activity thread; then shut down the render
   executor.

9. **Restartable actions must never overlap.** Keep cancellation controls scoped
   by profile/page/key, cancel the prior same-key invocation and wait for its
   `finished` event before starting the replacement. Long-running actions and
   waits must observe their `ActionContext`; canceled queued replacements must
   exit without executing so rapid presses collapse to the latest invocation.

10. **Portable assets must stay bounded and path-safe.** Keep `.lsdconfig` v1/v2
   import compatibility while exporting v3. Bundle only supported `sys.audio`
   files and `sys.timer` `sound` parameters, deduplicate audio content across
   both actions and restore it only below `CONFIG_DIR/imported-audio`. Bundle the
   supported BMP/JPEG/PNG/WebP custom exit image at most once, enforce its 50 MiB
   limit and restore it only below `CONFIG_DIR/imported-exit-images`. Validate
   every archive path and size before extraction. Single-key `.lsdkey` bundles
   follow the same rules and must keep their distinct manifest format, so neither
   bundle type can be imported through the other's entry point. Export must not
   mutate the `KeyConfig` it is given, and import must not save the
   configuration itself.

11. **Physical startup must remain exclusive and cancellable.** Generate frames
   offscreen under `RENDER_LOCK`, keep their brightness at or below the configured
   target and restore that target in `finally`. Play them directly on the
   provisional device before assigning `self.deck`, installing its callback or
   emitting `deck.connected`. Check the monitor stop event during writes and
   waits; only a completed or safely skipped animation may proceed to connection
   publication and the configured-key refresh. If shutdown cancels provisional
   startup, apply the configured clean-exit display before closing that device.

12. **Named page navigation must stay compatible and unambiguous.** Keep the
   legacy `nav.page` loader migration for single/multi/toggle actions, but never
   expose that ID in the registry or AI catalogue. Populate `nav.page.go` choices
   from the active profile, reject duplicate names and update every same-profile
   reference on rename. Next/previous must wrap, missing names must report status
   and a same-page selection must not save or emit redundant events.

13. **Grid DnD must survive child widgets and reject foreign data.** Keep one
   CAPTURE-phase, primary-button source/target pair on the grid, preload the typed
   internal string payload and resolve source/destination by walking from the
   picked child to its key button. Empty keys cannot be sources; empty and
   occupied keys are valid destinations in any direction. Validate the payload
   against the active source, preserve unsaved-change confirmation, move toggle
   and clock state with the key and always clear source/destination feedback.

14. **Stateful clocks must remain key-scoped and cheap.** Keep countdown and
   stopwatch state keyed by profile/page/key and pass that identity through
   `ActionContext`. A single-action `immediate` clock must never occupy an action
   worker. The scheduler may poll at 0.1 seconds but should render only changed
   seconds in the visible view; switching views must not pause state. Move state
   with DnD, reset it on edit/paste/clear, clear it when indices or configuration
   are replaced, and emit each timer completion once. Completion audio must stay
   on its separate executor and stop on reset or shutdown.

15. **The screen saver must own the deck without stealing actions.** Render every
   style as a coordinated full-deck frame under `RENDER_LOCK` and BASIC text
   layout, with frame brightness capped by its independent intensity. While
   active, suppress normal controller/HID renders. Track physical key activity
   and deliberate virtual-deck interactions through explicit
   `record_activity()` calls. Never attach a broad/global
   `Gtk.EventControllerLegacy` activity hook: nullable events previously caused
   `AttributeError` log storms, near-100% CPU use and application freezes on
   Pop!_OS. Consume both edges of the first physical wake press, then restore
   normal brightness and configured keys. Preview must work while disabled and
   without hardware, and temporary dialog subscribers must unsubscribe on close.
   On shutdown, signal the saver wakeup, join its thread before the monitor
   thread, and apply the clean-exit display before HID closes.

16. **Never nest a built `GLib.Variant` inside a format string.** The dbusmenu
   reply is `(u(ia{sv}av))` and its layout is already a typed variant. Passing it
   as a tuple element of `GLib.Variant("(u(ia{sv}av))", …)` makes PyGObject try to
   rebuild it from its unpacked value and fail with
   `TypeError: Expected GLib.Variant, but got str`. Assemble such replies with
   `GLib.Variant.new_tuple(...)` instead. This only shows up on a real D-Bus call,
   never when building the layout in isolation, so keep the reply-shape test.

17. **Hiding to the status area must always be reversible.** Keep
   `hides_on_close()` as the only decision point and keep all three of its
   conditions, so a missing, unregistered or vanished status area falls back to
   quitting. Hide by returning `True` from `close-request` and only setting the
   window invisible; destroying it would end the process and lose the session.
   Route icon-driven quit and profile switches through the window so the
   unsaved-change guard still runs, and present the window before any
   confirmation, since those dialogs are modal to it. Autostart state belongs in
   the XDG entry, never in `config.json` or an export. Never gate starting the
   icon on `is_supported()` and never register with `call_sync`: the status area
   commonly appears after the application at session login, and a blocking call
   on the GTK main thread freezes the window for its whole timeout. Keep the
   late-appearance regression test.

18. **Press gestures must not leak across keys.** Resolve them on release, keep
   the pending double-press timer keyed by profile/page/key, and cancel it from
   every path that changes what that index means: key edit, drag/drop swap, page
   or profile change and shutdown. A key with no gesture mode must still run on
   the press, so plain keys keep their current latency. Never make the virtual
   deck wait out the double-press window; it runs the single-press list directly.

19. **Opening the deck disturbs the USB bus, and that is not our bug.** hidapi
   detaches the kernel driver and unbinds the interface, which removes the deck's
   input node (`eventN`) and, on a hub or dock, can make the whole dock
   re-enumerate. On a dock that also carries video, the desktop may restart its
   panel. This was diagnosed once on COSMIC and traced to the dock: connecting
   the deck directly made it stop. Do not "fix" it in the application or with a
   `LIBINPUT_IGNORE_DEVICE` udev rule — neither is the cause. When something like
   this appears, correlate `udevadm monitor` with the affected process's PID, and
   check how the deck is physically connected before changing any code.

20. **Never fake media or modifier keys.** Media transport goes through MPRIS and
   volume through the session mixer, both of which work unprivileged on Wayland.
   Only genuine application shortcuts use `core/keystrokes.py`, and its backends
   must stay optional: no code path may assume ydotool exists, and a missing
   backend must surface as a status message, never an exception that breaks a
   multi-action sequence. Keep `ydotool_syntax()` detection: emitting 1.x key
   codes to the 0.x binary that Debian and Ubuntu ship fails silently for the
   user.

21. **The clean-exit display must be the final HID state.** Keep
   `device_default` as a firmware reset, `blank` as black tiles followed by
   brightness 0, and `custom` as a validated full-grid center crop written at
   normal configured brightness. Apply it only after device workers have joined
   and immediately before closing HID; if custom preparation or writes fail,
   fall back to the device default. Do not promise this state after forced
   termination, a crash or power loss.

22. **Folders must stay bounded, reversible and key-scoped.** Slot
   `FOLDER_BACK_INDEX` inside a folder is always the Back key: never store,
   select, drag, drop, paste, clear or swap it, or the physical deck can enter a
   folder with no way out. Keep `MAX_FOLDER_DEPTH` enforced in three places —
   loading (`_folder_contents`), creating (`can_add_folder`) and pasting or
   importing a subtree (`fits_here`). Keep `KeyConfig.folder` `compare=False`, or
   editing a key inside a folder makes its parent key look permanently dirty.
   Keep the open folder out of `config.json`: it is view state, and
   `_leave_folders()` must reset it on every page, profile and configuration
   change. Keep transient state on the full `RuntimeKey`, and discard a subtree's
   state when its folder is replaced or moved but **not** when the folder key
   itself is merely edited. Every walk over a key's actions or assets must
   recurse with `_walk_keys()` / `_walk_raw_keys()`.

23. **Dials are keys everywhere except in storage.** Keep `KIND_DIAL` on
   `KeyConfig` and its three lists in `STEP_FIELDS`, so bundles, migrations and
   renames reach a dial for free; keep them in `Page.dials`, walked alongside
   `page.keys` by `_key_configs()` and the bundle exporter. Keep `DIAL_PATH`
   negative so a dial can never share a `RuntimeKey` with the key of the same
   number. Preserve the tick count of a turn and the `MAX_DIAL_TICKS` ceiling,
   and derive touchscreen panels from the running edge so they tile without a
   seam. Everything dial-related must stay gated on `dial_count`, which is 0 on
   every model but the Plus.

24. **An empty appearance value means inherit, never "the default".** That holds
   for `icon`, `font_size` and now `text_color`: store nothing and let the
   renderer decide, or clearing a choice silently freezes the current default
   into the configuration. When the editor shows an inherited value it must
   block its own change handler while doing so.

---

## 6. Safe local experimentation

`Config.save()` **always** writes to `~/.config/linuxstreamdeck/config.json`
unless `LSD_CONFIG_DIR` is set. Any script that exercises code calling `save()`
(e.g. `set_page`, `set_profile`, `add_profile`, `add_page`, `rename_page`,
`paste_key`, `clear_key`, brightness, screen-saver or exit-display changes,
saving a key) will **overwrite the user's real config** — this has happened and
lost real keys and settings.
`Config.import_bundle()` also saves a replacement configuration and writes imported
icons, audio and exit images below the config directory, so it must always be
isolated too.

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
