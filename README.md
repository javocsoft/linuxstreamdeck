# LinuxStreamDeck

Software for the **Elgato Stream Deck** on Linux with **full OBS Studio integration**
(obs-websocket v5) and real-time state feedback on the keys.

## Features

- **Virtual deck in the UI**: the window grid mirrors the physical deck and lets you
  configure and test actions without the device connected.
- **Profiles**: each profile stores its own set of pages and keys (with a name and a
  short description). Switch profiles from the header selector to alternate between
  configurations as you need (e.g. "Work", "Streaming").
- **Easy organization**: move keys with **drag & drop** (swaps positions) and
  **copy/paste** any key to duplicate it (right-click → Copy/Paste, or `Ctrl+C`/`Ctrl+V`;
  `Delete` clears it). Works with any key type.
- **Three key types**:
  - *Single action* — one action, with state feedback on the key.
  - *Multiple actions* — ordered list of actions run in sequence when pressed
    (with an optional delay between steps).
  - *Toggle (ON/OFF)* — two action lists; each press toggles the state and runs the
    matching list, with its own appearance per state.
- **Deep OBS integration**, more complete than most alternatives on Linux:
  - Scenes: switch program/preview, studio mode, transitions (type and duration)
  - Recording: start/stop/pause · Streaming: start/stop · Virtual camera
  - Replay buffer: enable and save · Source screenshots to PNG
  - Audio: mute (with feedback), raise/lower volume, set volume in dB
  - Sources: show/hide per scene · Filters: enable/disable
  - Media: play/pause/restart/stop/next/previous
  - Scene collections and profiles · Internal OBS hotkeys
  - **Raw request**: any obs-websocket protocol request → 100% coverage
- **Real-time feedback**: active scene highlighted, recording key in red, muted
  microphone marked… via obs-websocket events.
- **Built-in icon library** (~7,400 Material Design Icons, categorized and
  searchable): every action comes with a default icon, you can pick another from the
  library, or use your own image. Nothing to upload by hand.
- System actions (run command, open URL) and navigation between pages.
- Automatic reconnection to OBS and device hotplug.

## Requirements

- Pop!_OS / Ubuntu 24.04 or similar, Python ≥ 3.10
- OBS Studio 28+ with the WebSocket server enabled
  (*Tools → WebSocket Server Settings*)

## Installation

Quick way with the included scripts:

```bash
# 1. Prepare the project: creates the virtual environment, installs dependencies
#    and checks that it compiles. With --apt it also installs the system packages.
./build.sh --apt

# 2. USB permissions for the Stream Deck (one time only)
sudo ./install-udev.sh
```

<details>
<summary>Manual steps (equivalent, without build.sh)</summary>

```bash
# System dependencies
sudo apt install gir1.2-gtk-4.0 gir1.2-adw-1 libhidapi-libusb0 python3-gi python3-gi-cairo

# Python environment (with access to the system GTK/PyGObject)
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -e .

# USB permissions
sudo ./install-udev.sh
```
</details>

## Usage

```bash
./run.sh                 # starts the app
LSD_DEBUG=1 ./run.sh     # with debug logging
```

(equivalent to `.venv/bin/linuxstreamdeck`)

1. Click the network button in the header and set the obs-websocket host/port/password.
2. Click a key in the grid, choose the **key type** (single, multiple or toggle), the
   category and the action, fill in the parameters (the dropdowns are filled live from
   OBS) and press **Save**.
3. Under **Icon**, pick one from the built-in library, use your own image, or keep the
   action's default. Add a **Label** only if you want text on the key.
4. **Test** runs the action without needing the physical deck.
5. **Reorder** keys by dragging them from one position to another, and **duplicate** a
   key with right-click → Copy and then Paste onto another (or `Ctrl+C`/`Ctrl+V`).
6. **Pages**: use the `+` button to add a page and the menu button (⋮) next to the page
   selector to **rename** or **delete** the current page.
7. **Profiles**: use the header selector to switch profiles. With the menu button (⋮)
   you can **create** a new profile (with a name and description), **edit** it or
   **delete** it. Each profile has its own pages and keys.

> The app is **single-instance**: close any previous window before opening another.

The configuration is saved to `~/.config/linuxstreamdeck/config.json` (with a backup in
`config.json.bak`). You can change the path with the `LSD_CONFIG_DIR` environment
variable.

## Structure

```
build.sh · run.sh · install-udev.sh    # prepare / launch / USB permissions
linuxstreamdeck/
├── core/          # event bus, config, action registry, controller, icons
├── device/        # physical Stream Deck (hidapi) and key rendering (Pillow)
├── obs/           # obs-websocket v5 client + full catalogue of OBS actions
├── ui/            # GTK4/Libadwaita: window, editor, icon picker, OBS settings
└── assets/icons/  # icon library (Material Design Icons font + index)
data/udev/         # udev rule for device access
```

## License

GPL-3.0-or-later — © JavocSoft
