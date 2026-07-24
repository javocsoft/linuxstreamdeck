<div align="center">

# 🎛️ LinuxStreamDeck

### Your Elgato Stream Deck, finally at home on Linux — with deep OBS Studio integration.

![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![Platform: Linux](https://img.shields.io/badge/platform-Linux-informational)
![UI: GTK4 / Libadwaita](https://img.shields.io/badge/UI-GTK4%20%2F%20Libadwaita-4A90D9)

<img src="docs/screenshot.png" alt="LinuxStreamDeck controlling OBS Studio" width="840">

</div>

---

Point-and-click control of your **Elgato Stream Deck** on Linux, built around **full OBS
Studio integration** (obs-websocket v5) with **live feedback right on the keys** — the
recording key turns red, the go-live key glows green, a muted mic is marked, the active
scene lights up. Configure everything from a clean GTK4 app, with a **built-in library of
~7,400 icons** so you never have to hunt for images.

## ✨ Why LinuxStreamDeck?

The Stream Deck is a fantastic little device — but Elgato only ships software for macOS
and Windows. The Linux community has built some genuinely great open-source projects to
fill that gap, and they deserve credit. In my own testing, though, I kept hitting tools
that were either fiddly to set up or missing the OBS features I actually reach for day to
day.

So this is my take on the problem: something that **just works out of the box**, is
**genuinely easy to configure**, and **covers OBS deeply** — including a "raw request"
escape hatch that exposes 100% of the obs-websocket protocol when you need it. No half-
implemented actions, no guessing. It runs, and it's useful.

## 🚀 Features

- 🎬 **Deep OBS integration** — more complete than most Linux alternatives (see below).
- 🔴 **Real-time feedback on the keys** — active scene highlighted, recording in red,
  streaming in green, muted mic marked… straight from obs-websocket events.
- 🗂️ **Profiles & pages** — each profile keeps its own set of pages and keys; switch
  between "Streaming", "Work", "Gaming" in one click. Rename, delete, describe.
- 🧩 **Three key types** — *single action*, *multiple actions* (run in sequence, with an
  optional **Wait** action to pause between them), and *toggle (ON/OFF)* with two action
  lists and its own look per state.
- 🎨 **Built-in icon library** — ~7,400 Material Design Icons, categorized and searchable.
  Every action ships with a sensible default icon; pick another, or use your own image.
- ✋ **Drag & drop and copy/paste** — reorder keys by dragging, duplicate any key with
  right-click → Copy/Paste (or `Ctrl+C`/`Ctrl+V`). Works with any key type.
- 🖥️ **Virtual deck** — the on-screen grid mirrors the physical device, so you can
  configure and test everything **without the hardware even connected**.
- 🔌 **Auto-reconnect & hotplug** — connects to OBS on its own and picks up the deck when
  you plug it in.
- 💾 **Portable configuration backups** — export or import profiles, pages, keys,
  settings and custom key icons in one file.
- 🔐 **Secure OBS password storage** — the password stays in your desktop keyring,
  never in the configuration file or an export.
- ✨ **AI-assisted key creation** — describe the key you want, get a locally
  validated proposal from OpenAI or Claude, review it in the normal editor, and
  decide whether to save it.

### 🎬 What you can do with OBS

| Area | Actions |
| --- | --- |
| **Scenes** | Switch program / preview · studio mode · transitions (type & duration) |
| **Recording & streaming** | Record start/stop/pause · stream start/stop · virtual camera |
| **Replay & capture** | Enable & save replay buffer · source screenshots to PNG |
| **Audio** | Mute (with feedback) · raise/lower volume · set volume in dB |
| **Sources & filters** | Show/hide sources per scene · enable/disable filters |
| **Media** | Play / pause / restart / stop / next / previous |
| **Advanced** | Scene collections & profiles · internal hotkeys · **raw request** (100% of the API) |

Plus system actions (run a command, open a URL, wait a set time) and navigation
between pages.

## 📦 Requirements

- Linux desktop (Pop!_OS / Ubuntu 24.04 or similar), Python ≥ 3.10
- OBS Studio 28+ with the WebSocket server enabled (*Tools → WebSocket Server Settings*)
- Secret Service through GNOME Keyring (installed by the `.deb` or `./build.sh --apt`)

## ⚙️ Installation

### Option A — Install the `.deb` (recommended)

On Debian/Ubuntu/Pop!_OS, grab `linux-stream-deck-<version>.deb` and let apt pull
the dependencies:

```bash
sudo apt install ./linux-stream-deck-<version>.deb
```

That's it — **LinuxStreamDeck** shows up in your app menu, the `linuxstreamdeck`
command is on your `PATH`, and the USB access rule is installed and reloaded for
you (unplug and reconnect the deck once after installing). To remove it later:
`sudo apt remove linux-stream-deck`.

### Option B — From source

Quick way with the included scripts:

```bash
# 1. Prepare the project: verifies custom agent adapters, creates the virtual
#    environment, installs dependencies and checks that it compiles. With --apt
#    it also installs the system packages.
./build.sh --apt

# 2. USB permissions for the Stream Deck (one time only)
sudo ./install-udev.sh
```

<details>
<summary>Manual steps (equivalent, without build.sh)</summary>

```bash
# System dependencies
sudo apt install gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-secret-1 gnome-keyring libhidapi-libusb0 python3-gi python3-gi-cairo

# Verify the generated Claude and Codex custom agent adapters
python3 agent-definitions/sync.py --check

# Python environment (with access to the system GTK/PyGObject)
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -e .

# Compile check
.venv/bin/python -m compileall -q linuxstreamdeck

# USB permissions
sudo ./install-udev.sh
```
</details>

<details>
<summary>Build the <code>.deb</code> yourself</summary>

```bash
./build.sh                       # once, to check agents and create the .venv it reads deps from
./packaging/build-deb.sh         # version from pyproject.toml
./packaging/build-deb.sh 1.2.3   # …or an explicit X.Y.Z (also bumps the version)

# → dist/linux-stream-deck-<version>.deb  (Architecture: all)
```

The build keeps the version in sync across `pyproject.toml` and
`linuxstreamdeck/__init__.py`, so passing an explicit `X.Y.Z` bumps both. The
package is architecture-independent: it vendors the two pip-only Python
dependencies and pulls GTK4/Libadwaita, Secret Service, GNOME Keyring, Pillow,
hidapi, websocket-client and the HTTPS CA certificate bundle through apt. AI
provider calls use Python's standard library, so they add no pip dependency.

After installing or upgrading the package, refresh the system AppStream cache
so software centres show the current application metadata:

```bash
sudo ./packaging/refresh-appstream.sh
```

Close and reopen the software centre afterwards to load the refreshed cache.
</details>

## 🕹️ Usage

```bash
./run.sh                 # starts the app
LSD_DEBUG=1 ./run.sh     # with debug logging
```

*(equivalent to `.venv/bin/linuxstreamdeck`)*

1. Click the network button in the header and set your obs-websocket host / port / password.
2. Click a key in the grid, choose the **key type**, the category and the action, fill in
   the parameters (dropdowns are populated **live from OBS**) and press **Save**.
3. Under **Icon**, pick one from the built-in library, use your own image, or keep the
   action's default. When inherited, the Appearance preview shows the same default
   icon as the deck without turning it into a custom override. Add a **Label** only
   if you want text on the key.
4. **Test** runs the action without needing the physical deck.
5. **Reorder** keys by dragging them, and **duplicate** a key with right-click → Copy, then
   Paste onto another (or `Ctrl+C`/`Ctrl+V`).
6. **Pages** — use the menu (⋮) next to the page selector to add a new page, rename or
   delete the current one.
7. **Profiles** — switch with the header selector; use the menu (⋮) to create, edit or
   delete a profile. Each profile has its own pages and keys.
8. **About** — click the About button in the header for application details,
   licensing and the GitHub link.

> 💡 The app is **single-instance**: close any previous window before opening another.

Your non-secret configuration lives in `~/.config/linuxstreamdeck/config.json` (with an
automatic backup in `config.json.bak`). Point `LSD_CONFIG_DIR` somewhere else to relocate it.

### 🔐 OBS password storage

Your OBS password is stored asynchronously in the desktop Secret Service (GNOME
Keyring), never in `config.json`, `config.json.bak` or an export. On its first
run after upgrading, LinuxStreamDeck automatically moves any existing plaintext
password to the keyring and removes it from both configuration files. Configuration
files are atomically written with user-only (`0600`) permissions.

If secure storage is unavailable, any plaintext password is still removed. The
password then lasts only for the current session, so enter it again after restarting
the app.

## ✨ Create a key with AI

Select a key and click **Create with AI...** to ask OpenAI or Claude for a key
proposal. Choose the provider and model, enter your own provider API key, and
describe the result you want. API access is billed separately by the selected
provider; it is not included with LinuxStreamDeck.

Each provider API key is stored separately in the desktop Secret Service (GNOME
Keyring). Keys are never written to `config.json`, its backup, or a configuration
export. If the selected provider already has a key, the dialog shows a fixed,
read-only mask and uses the saved secret, never the mask itself. Choose **Replace
saved API key** to enter another key, **Use saved API key** to return to the stored
one, or **Forget saved API key** to remove it.

The optional context switch sends only a bounded set of OBS and page names to help
the provider choose existing values; it never sends passwords, commands, or the
full configuration.

AI-assisted creation cannot propose **Run Command** (`sys.command`) or **Raw OBS
Request** (`obs.raw`). Every response is validated locally against the installed
action catalogue and converted into a preview. Nothing is executed or saved
automatically: load the proposal into the existing editor, review every action
and parameter, then press **Save** only if you want to keep it.

## 💾 Import and export configuration

Use the profiles menu (⋮) in the header to choose **Export configuration** or
**Import configuration**.

- **Export** creates a portable `.lsdconfig` ZIP archive. It contains the full
  JSON configuration, custom key icon files and non-secret OBS settings, but
  never the OBS password or provider API keys. Built-in Material Design Icons
  remain lightweight `mdi:` references because they are bundled with
  LinuxStreamDeck. If a custom icon file can no longer be found, it is not
  included and the app shows a warning.
- **Import** replaces all current profiles, pages, keys and settings after you
  confirm the warning. The previous configuration is saved as
  `~/.config/linuxstreamdeck/config.json.bak`. Bundled custom icons are restored
  under `~/.config/linuxstreamdeck/imported-icons/`; brightness and OBS settings
  are applied immediately, and OBS reconnects with the imported settings. The
  import keeps this computer's keyring credentials and ignores password fields in
  older exports. When moving to another computer, enter the OBS password and any
  provider API keys again.

## 🗂️ Project structure

```
build.sh · run.sh · install-udev.sh    # prepare / launch / USB permissions
packaging/         # build-deb.sh, .desktop, icon, AppStream metainfo, scripts → .deb
linuxstreamdeck/
├── ai/            # OpenAI/Claude requests, bounded context and proposal validation
├── core/          # event bus, config, credential storage, action registry, controller, icons
├── device/        # physical Stream Deck (hidapi) and key rendering (Pillow)
├── obs/           # obs-websocket v5 client + full catalogue of OBS actions
├── ui/            # GTK4/Libadwaita: window, editor, AI assistant, OBS settings
└── assets/icons/  # icon library (Material Design Icons font + index)
data/udev/         # udev rule for device access
```

## 🙌 Acknowledgements

- The Linux Stream Deck community and the open-source projects that paved the way.
- [python-elgato-streamdeck](https://github.com/abcminiuser/python-elgato-streamdeck) and
  [obsws-python](https://github.com/aatikturk/obsws-python) — the libraries this stands on.
- [Material Design Icons](https://pictogrammers.com/library/mdi/) (Apache-2.0), bundled as
  the built-in icon library.

## 📄 License

GPL-3.0-or-later — © JavocSoft
