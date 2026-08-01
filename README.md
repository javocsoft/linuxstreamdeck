<div align="center">

# 🎛️ LinuxStreamDeck

### Your Elgato Stream Deck, finally at home on Linux — with deep OBS Studio integration.

[![Latest release](https://img.shields.io/github/v/release/javocsoft/linuxstreamdeck?label=release&color=success)](https://github.com/javocsoft/linuxstreamdeck/releases/latest)
![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![Platform: Linux](https://img.shields.io/badge/platform-Linux-informational)
![UI: GTK4 / Libadwaita](https://img.shields.io/badge/UI-GTK4%20%2F%20Libadwaita-4A90D9)

<img src="docs/screenshot.png" alt="LinuxStreamDeck Streaming profile with a 15-key virtual deck and the Record action editor" width="840">

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

It also talks to **Twitch**, because a deck built for OBS is built for people who
stream, and the title, the category, your viewer count and a clip button belong
on the same hardware as the scene switches.

## 🚀 Features

- 🎬 **Deep OBS integration** — covering nearly all of OBS’s features (see below).
- 🔴 **Real-time feedback on the keys** — active scene highlighted, recording in red,
  streaming in green, muted mic marked… straight from obs-websocket events.
- 🗂️ **Profiles & pages** — each profile keeps its own set of pages and keys; switch
  between "Streaming", "Work", "Gaming" in one click. Rename, duplicate, delete or
  describe a profile in one click. Pages can be renamed, duplicated, reordered (moved
  up or down) and deleted; assign keys to navigate directly, forward or backward
  through the profile's pages.
- 🧩 **Six key types** — *single action*; *multiple actions* (run in sequence, with an
  optional **Wait** action to pause between them); *toggle (ON/OFF)* with two action
  lists and its own look per state; *random action*, which picks one of its actions
  at each press; *single / double / long press*, with a separate action list per
  gesture; and *folder*, which opens its own grid of keys. Multi-action keys show
  **RUN** with a subtle slow blue breathing halo while their actions are queued or
  running; blocking single **Wait** and **Play audio file** actions use the same
  feedback.
- 🏷️ **Name each action in a list** — give any step in a multiple, toggle, random
  or press key an optional **Step name**, and the list shows it instead of the
  action name, so a long sequence stays readable at a glance. Copy any step and
  paste it into another list, another key type or another key entirely.
- 📁 **Folders on a key** — group related actions inside a key instead of spending
  a whole page on them. Give the folder a name and an icon, nest folders up to
  five levels deep, and go back with the reserved first key that every folder
  provides.
- 🔊 **Local audio playback** — play WAV, MP3, OGG, FLAC or Opus files at a chosen
  volume, for the full file or an optional maximum duration.
- ⏱️ **On-key timer and stopwatch** — show a live `HH:MM:SS` value in the
  center of a key. Timers can play an optional completion sound, and both clocks
  keep counting while you visit another page or profile.
- 🎨 **Built-in icon library** — ~7,400 Material Design Icons, categorized and searchable.
  Every action ships with a sensible default icon; pick another, or use your own image.
- ✋ **Drag & drop and copy/paste** — drag any configured key with the primary
  mouse button to an empty position to move it, or onto another configured key
  to swap them. The source dims and the destination highlights while dragging.
  **Hold the drag over a folder** and it opens, so the key can be dropped
  inside; holding it over the Back key comes back out again.
  Duplicate any key with right-click → Copy/Paste (or `Ctrl+C`/`Ctrl+V`).
- 🖥️ **Virtual deck** — the on-screen grid mirrors the physical device, so you can
  configure and test everything **without the hardware even connected**, single,
  double and long press included.
- 🔎 **Search everything** — find an action by what it does instead of which
  category it lives in, and find a key by label, action or value across every
  profile, page and folder.
- ↩️ **Undo and redo** — `Ctrl+Z` takes back a clear, a paste or a move; `Ctrl+Y` puts it forward again.
- ⌨️ **Keyboard navigation** — arrow keys move focus across the key grid without triggering the unsaved-change dialog; `Enter` selects the focused key.
- 🩺 **Check your keys against OBS** — renamed a scene? Run the check from where
  you are and it lists every key that now points at something OBS no longer has,
  suggests what you probably meant, and repoints them all in one go. It saves a
  backup first, so a wrong fix is undone by restoring it.
- 🖨️ **Printable layout sheet** — export any profile as a single PNG with every page and folder laid out, each key captioned with what it actually does. Useful for printing or sharing.
- 🌌 **Animated full-deck screen saver** — choose from eleven coordinated effects,
  set the idle delay and independent light intensity, and preview them on both
  the virtual and physical decks.
- 📴 **Display after exit** — leave the physical deck on its firmware standby
  image, turn every key fully off, or keep one custom image across the full grid
  after LinuxStreamDeck closes cleanly.
- ✨ **Physical deck startup animation** — a newly connected deck wakes with a
  short full-deck sequence and spells its name across the keys, using the
  longest form that fits the grid, before loading your configured page.
- 🔌 **Auto-reconnect & hotplug** — connects to OBS on its own and picks up the deck when
  you plug it in. Adapts to the deck's own key layout — Mini, Original/MK.2, XL,
  Neo or Stream Deck + — and lays the screen savers, startup sequence and exit
  image out across its real grid. One deck at a time: with several connected it
  uses the first and tells you so in the status bar. The Pedal has no key
  displays, so it is not supported.
- 🫥 **Keeps running in the status area** — closing the window can leave the deck
  working in the background. Its status icon switches profiles, reopens the
  window and quits.
- 🚀 **Start with your session** — an optional startup entry launches
  LinuxStreamDeck at login, straight into its status icon.
- 🖼️ **Live scene previews on the keys** — a scene key can show what that scene
  is actually sending out, refreshed once or twice a second. Beyond looking
  great, it is the fastest way to spot a scene that has broken: a dead capture
  card or a source that failed to load shows up as a black key, so you see it
  **before** you cut to it instead of live on air. Off by default and chosen
  per key, because each frame costs OBS a render.
- 📊 **Live stats on a key** — put dropped frames, stream bitrate, congestion,
  stream or recording time, free disk space, FPS, OBS's own CPU and memory, or
  your whole machine's CPU straight on the deck, refreshed while you work. The key turns amber and then red as a
  measurement gets worse, so you notice you are dropping frames or running out
  of disk without leaving what you were doing. Free disk space and system CPU are
  read from the kernel, so those keys keep answering while OBS is closed — which
  is exactly when you check whether there is room to record. Point the disk key
  at your recording drive if it is not your home folder.
- 🚦 **Pre-flight check** — one key, pressed in the minute before you go live.
  The whole deck turns into a board: is anything actually being *heard*, does
  OBS really hold each of your capture devices, is there room on disk, is the
  recording folder writable, is the machine already busy, which scene
  collection is loaded, is a stream key set — and, if you have connected an
  account, is your Twitch title and category set and are you already live.
  Results appear one at a time, and the deck goes back to normal on any press —
  or the moment you close the report window, since by then you have read it.
  It reports; it never switches a scene, activates a source or changes a
  setting. And it does not pretend: anything it could not establish shows as a
  faded key with a question mark, never as a tick, and the window lists what
  each answer does **not** cover. There is no "all good" — a board you read is
  honest, a green light would be a promise it cannot keep.
- 💜 **Twitch on the deck** — see your viewer count, followers or stream uptime
  without alt-tabbing, set the stream title and the category from a key, clip
  the last few seconds, and drop a stream marker so you can find the good bit
  later. Connecting is a six-character code you type at `twitch.tv/activate`:
  no password, no browser plugin, nothing secret stored. Your tokens go in your
  desktop keyring, never into the configuration file or an export, and a key
  that needs an account you have not connected is faded rather than silently
  dead. Pair the Twitch marker with the OBS chapter marker on one key and a
  single press marks both the broadcast and the recording.
- 🔔 **Never miss somebody talking to you** — a key that stays dark while chat
  is quiet and, the moment somebody writes, shows **their picture, how long
  they have been waiting and how many are unread**. The border climbs from blue
  to amber to red as the wait grows, and the key breathes so you catch it from
  the corner of your eye. Press it to mark them seen.
  It counts **waiting**, not messages, because "3 messages" says nothing about
  whether you are ignoring somebody and "four minutes" says exactly that. The
  sound follows the same idea: one when the key goes from quiet to
  somebody-waiting, and an optional reminder — never one per message, which is
  what makes people turn the sound off and go back to missing everything.
  Filter it to **questions and mentions** or to **first-time chatters** and it
  keeps working on a busy channel. The same key also watches **new followers,
  subscriptions and raids**, together or one per key.
  For when a sound is not enough — headphones on, deep in a game — switch on
  **Flash the whole deck** and every key pulses three times in the colour of
  what arrived: blue for chat, green for a follower, purple for a subscription,
  orange for a raid. The middle key says which it was in one word — `CHAT`,
  `FOLLOW`, `SUB`, `RAID` — so you do not have to have learnt the colours, and
  if you would rather it were always your own colour, type one in **Flash
  colour** and the word switches to black or white to stay readable on it. It
  follows the same rule as the sound rather than firing per message, and it
  wakes the deck if the screen saver had it.
- 🔴 **The deck tells you when something went wrong** — a key whose action fails
  is outlined in red with an exclamation mark for a few seconds, so you find out
  from the hardware you were already looking at rather than from a message in a
  window that is probably hidden. A key that cannot work at all — an OBS key with
  OBS closed — is faded, so "not ready" no longer looks exactly like "idle". A
  full log is kept at `~/.config/linuxstreamdeck/linuxstreamdeck.log` and opens
  straight from the menu, which is what to attach to a bug report.
- 🛑 **Stop a sequence when a step fails** — a key with several actions normally
  runs the rest even if one fails, which is right when they are independent. When
  the list is a recipe — switch scene, wait, start recording — set **If an action
  fails** to *Stop the sequence*, so a failed switch cannot leave you recording
  the wrong scene.
- 🔖 **Chapter markers and recording splits** — mark the moment something worth
  keeping happens and find it as a chapter in your editor instead of hunting
  through three hours of footage, or close the current recording file and carry
  on into a new one without stopping. Both need a recent OBS and say so plainly
  if it is too old.
- 🎬 **Something on the deck from the first minute** — a fresh install offers to
  put a handful of keys on your deck: recording, streaming, a chapter marker,
  studio mode, a stopwatch and live CPU and disk readings. None need setting up,
  and you can change or remove any of them. Decline it and the deck stays empty.
- 💾 **Portable configuration backups** — export or import profiles, pages, keys,
  settings, custom key icons, the custom exit image and referenced playback or
  timer audio in one file.
- 🕓 **Automatic backups you can roll back to** — earlier configurations are kept
  on their own; pick one by date and by what it holds, and restore it. The
  current one is saved first, so a restore can itself be undone.
- 🔤 **Adjustable label size and color** — pick the font size drawn on each key, from
  *Extra small* to *Extra large*, or leave it on *Automatic*. Choose a custom text
  color for the label, value and badge, or leave it inherited. Toggle keys can use
  a different size and color for their ON and OFF states.
- 📤 **Share a single key** — right-click any key to export it as a portable
  `.lsdkey` file, with its custom icon and audio bundled in, and import it back
  into any key on any computer.
- 🔐 **Secure OBS password storage** — the password stays in your desktop keyring,
  never in the configuration file or an export.
- ✨ **AI-assisted key creation** — describe the key you want, get a locally
  validated proposal from OpenAI or Claude, review it in the normal editor, and
  decide whether to save it.

### 🎬 What you can do with OBS

| Area | Actions |
| --- | --- |
| **Scenes** | Switch program / preview · **live scene preview on the key** · studio mode · transitions (type & duration) |
| **Before going live** | Pre-flight check: nothing muted and audio arriving, capture devices held by OBS, disk space, recording folder, machine load, which scene collection is loaded, stream key set, and your Twitch title, category and account |
| **Recording & streaming** | Record (start, stop or toggle) · pause recording · add a chapter marker · split the recording file · stream start/stop · virtual camera |
| **Replay & capture** | Enable & save replay buffer · source screenshots to PNG |
| **Audio** | Mute (with feedback) · raise/lower volume · set volume in dB. Pick a scene and the list narrows to its audio inputs, plus Desktop Audio and Mic/Aux |
| **Sources & filters** | Show/hide sources per scene (including sources inside groups) · enable/disable filters · **set a text source** · **refresh a browser source** · move/scale/rotate a source |
| **Media** | Play / pause / restart / stop / next / previous |
| **Advanced** | Scene collections & profiles · internal hotkeys · **live statistics on a key** · **raw request** (100% of the API) |

### 💜 What you can do with Twitch

| Area | Actions |
| --- | --- |
| **On the key** | Viewers · followers · stream uptime · live/offline, refreshed while you work |
| **Your stream** | Set the stream title · set the category, picked from live Twitch suggestions with box art |
| **Moments** | Create a clip · create a stream marker |
| **On air** | Start an ad break of a chosen length (Affiliates and Partners only) · raid another channel (or cancel it) · post a highlighted announcement in your chat |
| **Do not miss it** | A key that lights up, shows who is waiting and for how long, plays a sound and can flash the whole deck: chat messages, new followers, subscriptions and raids |
| **Before going live** | The pre-flight checks your Twitch account, title, category and whether you are already live |

Connecting asks for no password. The dialog shows a short code, you enter it at
`twitch.tv/activate`, and that is the whole setup. The access and refresh tokens
are kept in your desktop keyring and never reach `config.json`, its backup or an
exported configuration, so sharing a configuration never shares your account.
You can disconnect at any time from **Twitch account…**, which also tells Twitch
to forget the authorization.

The follower count and the title answer while you are offline; the viewer count
does not, because there is no audience to count when you are off air, and
showing zero would be a claim rather than an absence.

**The category has to be a real one.** Start typing and Twitch's own categories
appear with their box art; pick one. Text Twitch does not recognise is marked
as you type and is saved as *no category at all*, so the key is plainly
unconfigured rather than one that looks right and fails the first time you
press it on air. A category set before this existed is left alone.

### 🖥️ System and navigation actions

| Category | What you get |
| --- | --- |
| **Run & open** | Run a command · open a URL · **Open** a file, folder or program · **Open application** from the installed list · **Close application** (politely or forced) |
| **Media** | **Media action**: previous track, play/pause, next track, stop, mute, volume up and volume down |
| **Keyboard** | **Keyboard shortcut** with editable presets · **Shortcut switch** alternating between two shortcuts |
| **Timing** | Wait · countdown timer · stopwatch · play a local audio file |
| **Navigation** | Next page · previous page · go to page · **Page indicator** · **Change profile** |

Media control goes through **MPRIS** (`playerctl`) and the session mixer
(`wpctl` or `pactl`), so it works the same on Wayland and X11 without simulating
media keys. **Open application** can also close the app on a long press, and
shows a lit key while it is running.

## 🎛️ Supported devices

LinuxStreamDeck reads the key layout from the device, so it lays every full-deck
image out across the grid your deck actually has.

| Device | Keys | Grid | |
| --- | --- | --- | --- |
| Stream Deck Mini | 6 | 3 × 2 | ✅ |
| Stream Deck Neo | 8 | 4 × 2 | ✅ |
| Stream Deck Original / MK.2 | 15 | 5 × 3 | ✅ tested on hardware |
| Stream Deck + | 8 | 4 × 2 | ✅ keys, plus the 4 dials and the LCD strip |
| Stream Deck XL | 32 | 8 × 4 | ✅ |
| Stream Deck Pedal | 3 | — | ❌ no key displays |

One deck at a time: with several connected it uses the first and says so in the
status bar.

On a **Stream Deck +**, a row of dial buttons appears under the key grid. Each
dial takes its own action list for turning left, turning right and pushing, and
the LCD strip above it is drawn with that dial's name and icon — a fast turn
runs its list once per step, so a dial mapped to volume moves by as much as your
hand actually turned. Tapping a panel on the strip presses the dial under it.

> Every layout is verified in simulation, but the only model I own and can test
> on real hardware is the 15-key Original. The dials and the LCD strip have
> therefore never run on a physical Stream Deck +. If you have another model and
> something looks wrong, please
> [open an issue](https://github.com/javocsoft/linuxstreamdeck/issues).

## 📦 Requirements

- A supported Stream Deck (see above) — or none at all: the virtual deck works
  without hardware
- Linux desktop (Pop!_OS / Ubuntu 24.04 or similar), Python ≥ 3.10
- OBS Studio 28+ with the WebSocket server enabled (*Tools → WebSocket Server Settings*)
- Secret Service through GNOME Keyring (installed by the `.deb` or `./build.sh --apt`)
- GStreamer 1.0 with the base and good plugin sets for local audio playback

## ⚙️ Installation

### Option A — Install the `.deb` (recommended)

On Debian/Ubuntu/Pop!_OS, download the latest `linux-stream-deck-<version>.deb`
from the [**Releases page**](https://github.com/javocsoft/linuxstreamdeck/releases/latest)
and let apt pull the dependencies:

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
sudo apt install gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-secret-1 \
  gir1.2-gstreamer-1.0 gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
  gnome-keyring libhidapi-libusb0 python3-gi python3-gi-cairo

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

> **Using a USB hub or dock?** Opening the deck detaches its kernel driver and
> unbinds the USB interface. On a dock that also carries video or network, that
> can make the dock re-enumerate, and the desktop may briefly restart its panel
> or flicker. Connecting the Stream Deck straight to the computer avoids it.

<details>
<summary>Build the <code>.deb</code> yourself</summary>

```bash
./build.sh                       # once, to check agents and create the .venv it reads deps from
./packaging/build-deb.sh         # version from pyproject.toml
./packaging/build-deb.sh X.Y.Z   # …or an explicit version (also bumps the sources)

# → dist/linux-stream-deck-<version>.deb  (Architecture: all)
```

The build keeps the version in sync across `pyproject.toml` and
`linuxstreamdeck/__init__.py`, so passing an explicit `X.Y.Z` bumps both. The
package is architecture-independent: it vendors the two pip-only Python
dependencies and pulls GTK4/Libadwaita, Secret Service, GNOME Keyring, Pillow,
hidapi, websocket-client, GStreamer with its playback plugins and the HTTPS CA
certificate bundle through apt. AI provider calls use Python's standard library,
so they add no pip dependency.

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
   In a list of actions, each step also takes an optional **Step name**; the list
   then shows that name instead of the action, which makes a long sequence much
   easier to follow. Every row carries **move up**, **move down**, **copy** and
   **remove**, all on the row itself, so nothing has to be opened first. Rows can
   also be **dragged by their handle** to any position. A newly added step opens
   and scrolls into view, and the rows you opened or collapsed stay as you left
   them.
   **Right-click a row** for everything it can do: **Copy action** and **Paste
   action**, **Move up** / **Move down** / **Move to top** / **Move to bottom**,
   and **Remove action**. The row stays highlighted while the menu is open, so
   it is always clear which one the entries act on, and entries that would do
   nothing are greyed out.
   **Copy an action from one list to another** — press the row's copy button, or
   use **Copy action**. Then right-click wherever it should go: on a row to
   insert it there, pushing that row down, or anywhere else in the list to add it
   at the end. A pasted row is scrolled into view but stays closed, since it is
   already configured. The copy survives switching lists, key types and keys, so
   it can go into the ON list of a toggle, into a different gesture's list, or
   into another key entirely — including a **Single action** key, whose editor
   takes the same right-click menu and replaces its action with the copied one.
   To turn a whole key into that action, right-click it on the grid and choose
   **Paste action**: the key becomes a Single action key running it, and the copy
   stays available for the next key.
3. Under **Icon**, pick one from the built-in library, use your own image, or keep the
   action's default. When inherited, the Appearance preview shows the same default
   icon as the deck without turning it into a custom override. Add a **Label** only
   if you want text on the key.
4. **Test** runs the action without needing the physical deck. For a
   **Single / double / long press** key the **••** and **—** buttons beside it
   run the other two lists, so the whole key is testable without hardware.
   Save and Test both light up when pressed — and Save briefly reads **Saved** —
   because everything they do happens somewhere else: on disk, on the deck, or
   in OBS.
   Next to the **Action** dropdown, the magnifier searches **every** action at
   once — by name, by category or by what its description says — so you do not
   have to know which category it lives in.
5. **Undo** the last key change with `Ctrl+Z`, or right-click a key and choose
   **Undo last change**. Clearing, pasting and moving keys can all be taken
   back. The history belongs to the grid you are looking at, so changing page,
   profile or folder starts a fresh one.
6. **Find a key** with `Ctrl+F`, or the ⋮ menu → **Find a key…**. It searches
   every profile, page and folder by label, action or parameter value — "mic",
   "scene Live", "05:00" — and jumps straight to whichever key you pick.
7. **Reorder** configured keys by dragging with the primary mouse button. Drop
   onto an empty position to move the key, or onto an occupied position to swap
   both keys; dragging works in any direction. Empty keys are destinations, not
   drag sources. To move a key **into** a folder, hold the drag still over that
   folder for about a second: it opens, and you drop the key on a position
   inside it. Holding over the Back key leaves the folder the same way, so a key
   can also be dragged back out. **Duplicate** a key with right-click → Copy,
   then Paste onto another (or `Ctrl+C`/`Ctrl+V`).
8. **Folders** — set a key's type to **Folder** and save it; then double-click it
   on the grid (or right-click → Open folder) to configure the keys it holds.
   Pressing it on the physical deck opens it too.
9. **Pages** — use the menu (⋮) next to the page selector to add a new page, rename or
   delete the current one. Page names must be unique within their profile.
10. **Profiles** — switch with the header selector; use the menu (⋮) to create, edit or
   delete a profile. Each profile has its own pages and keys.
11. **Stream Deck display** — use its button in the header to configure
   the screen saver and what the physical deck shows after a clean exit.
12. **About** — click the About button in the header for application details,
   licensing and the GitHub link.

If the selected key has unsaved edits, LinuxStreamDeck protects them before you
select another key, move keys, switch or create a page/profile, import a
configuration or close the window. Choose **Save and continue**, **Discard
changes** or **Keep editing**. Reverting every field to its last loaded or saved
value clears the warning, and AI-loaded proposals receive the same protection.
Replacing or clearing the edited key offers only **Discard changes** or **Keep
editing**, because saving immediately before overwriting it would have no effect.

> 💡 The app is **single-instance**: close any previous window before opening another.

Your non-secret configuration lives in `~/.config/linuxstreamdeck/config.json` (with an
automatic backup in `config.json.bak`). Point `LSD_CONFIG_DIR` somewhere else to relocate it.

### ✨ Physical deck startup

When LinuxStreamDeck opens a physical deck, it plays a short 33-frame wake,
energy burst, title, hold and fade sequence. The name appears one letter per key,
from left to right and top to bottom, centered in whatever grid the deck has: on
the 15-key Original that is `Linux` / `Strea` / `mDeck`. A deck too small for the
full name shows the longest form that fits — `Linux` on a Mini — rather than a
cut-off fragment. The animation ends on black, then your configured keys replace
it.

The sequence never raises the hardware above your configured brightness and
restores that setting when it finishes. Closing the application cancels startup
promptly. A disconnect, rendering problem or device I/O failure is handled safely
without leaving a partially initialized deck. This startup sequence runs only on
the physical Stream Deck; the virtual deck always shows the configured keys.

### 🌌 Configure the animated screen saver

Click the **Stream Deck display** button in the header to enable an animation
after the selected idle period. The idle delay accepts 1 to 1440 minutes.

| Effect | What it looks like |
| --- | --- |
| **Neon Pipes** | Retro glowing pipes grow and turn across the whole deck. |
| **Digital Rain** | Cyan data trails fall through a dark digital grid. |
| **Aurora Flow** | Blue, violet and teal light waves drift slowly. |
| **Orbital Core** | A core with rotating rings and orbiting particles. |
| **Circuit Pulse** | Energy pulses travel a circuit-board network. |
| **Ember Field** | Flames climb from the bottom of the deck, embers lifting off into the dark. |
| **Hyperspace** | A warping wormhole tunnel twists past while stars spiral out of its throat. |
| **Matrix Code** | Columns of mirrored glyphs rain down, each stream led by a white-hot character. |
| **HAL 9000** | One red camera eye watching from the middle, breathing and lighting the keys around it. |
| **Split-Flap Board** | Every key becomes a flap module, riffling and settling into a word. |
| **LinuxStreamDeck** | The name breathing softly across a black full-deck background. |

**Matrix Code** draws half-width katakana where a Japanese font is installed and
falls back to Latin letters and digits where none is, so it always works. If you
want the authentic glyphs, install one: `sudo apt install fonts-noto-cjk`. The
`.deb` only *suggests* that package rather than recommending it, so installing
LinuxStreamDeck does not drag in 91 MB of fonts. **Light intensity** ranges from 5 to
100% and is independent of the normal deck brightness.

**Preview now** starts the selected effect immediately on the physical and
virtual decks, even when automatic activation is disabled. It also works without
physical hardware, so every style can be checked on screen. Changing the
animation or intensity updates a running preview. Stop the preview, close the
dialog or press **Save** to return to the configured keys; only **Save** persists
the enable switch, style, delay and intensity.

Physical Stream Deck key activity and explicit virtual-deck interactions, such
as selecting or testing a key or opening the Stream Deck display controls,
restart the idle countdown. When the screen saver wakes, the normal brightness
and configured key images return. The first physical key press only wakes the
deck and is consumed together with its release, so it cannot accidentally run
the assigned action; press the key again to run it.

### 📴 Choose what the physical deck shows after exit

The same **Stream Deck display** dialog controls what remains on the hardware
after LinuxStreamDeck closes cleanly:

- **Device default** resets the deck to the standby image supplied by its
  firmware.
- **Off** writes black to every key and sets the display brightness to zero.
- **Custom** center-crops one BMP, JPEG, PNG or WebP image across the complete
  key grid and leaves it visible at your normal configured brightness.

A custom image may be up to 50 MiB. Press **Save** in the dialog to persist the
selected state and image. If the custom file is unavailable when the app closes,
LinuxStreamDeck falls back to **Device default**.

This state is applied while LinuxStreamDeck still owns the USB device during an
orderly shutdown. A forced termination, system crash or power loss cannot
guarantee that it will be written to the deck.

### 📁 Group keys inside a folder

A folder is a key that opens its own grid, so related actions can live together
without spending a whole page on them.

1. Select an empty key, set **Key type** to **Folder**, give it a **Label** (that
   is the folder name) and optionally an **Icon** — the default is a folder
   glyph. Press **Save**.
2. **Double-click** the key on the grid to go inside (right-click → **Open
   folder**, or the **Open folder** button in the editor, do the same). On the
   physical deck a normal press opens it.
3. Configure the keys inside exactly as anywhere else: any key type, drag & drop,
   copy/paste, single-key export and import all work the same. A key can be
   dragged in from outside by resting the drag on the folder until it opens, and
   dragged back out by then resting it on the Back key. Dropping a key onto an
   occupied position inside sends that position's key back where the dragged one
   came from, exactly as a swap on one grid does.
4. The **first key of every folder is a reserved Back key** showing the folder
   name. It cannot be configured, moved or overwritten, so the physical deck can
   never enter a folder it is unable to leave.
5. On screen, the path above the grid (`Page 1 ▸ Scenes ▸ Audio`) shows where you
   are; click any step to jump straight back to it.

Folders can hold folders, up to **five levels**. A folder key shows how many keys
it holds as a small badge. Which folder is open is never saved: starting the
application, changing page or switching profile always returns to the page root,
while timers, stopwatches and toggle states inside a folder keep their own state
per folder position.

Copying, clearing or overwriting a folder that holds keys asks for confirmation
first, and a folder whose type you want to change has to be cleared first, so its
contents can never disappear silently. Exported `.lsdkey` files and `.lsdconfig`
backups carry a folder's whole contents, including the icons and audio of the
keys inside it.

### 🗂️ Navigate between pages from a key

Choose the **Navigation** category in the key editor:

- **Next page** and **Previous page** need no parameters. They wrap around, so
  next from the last page opens the first and previous from the first opens the
  last.
- **Go to page** offers a **Destination page** dropdown populated from the
  current profile. If that named page is later deleted, pressing the key reports
  that the page was not found.

Renaming a page automatically updates every **Go to page** reference in the same
profile, and duplicate page names are rejected so targets remain unambiguous.
Keys created with the older combined page-navigation action are migrated
automatically; after the next save or export they use the three current actions.

### ⏱️ Use a countdown timer or stopwatch

Choose **System → Countdown timer** to set a duration and, optionally, a
completion sound and volume. The idle key shows the configured duration. Press
it once to start; the value counts down in the center of the key as
`HH:MM:SS`. Press it while running to stop and reset it. At zero the key remains
visibly finished and can play a WAV, MP3, OGG/OGA, FLAC or Opus sound; press it
again to reset the timer and stop any completion sound.

Choose **System → Stopwatch** for a counter that starts at `00:00:00`. Press
once to start and again to stop and reset it to zero. Both clocks react
immediately on single-action keys without occupying an action worker. They keep
running when you switch pages or profiles and show the current value when you
return.

Dragging a configured clock key moves its live state with it. Saving a changed
key, pasting over it or clearing it resets that position. Clock state is
temporary rather than part of the saved configuration, so deleting a page or
profile, importing a configuration or closing LinuxStreamDeck clears it.

### 🔊 Play a local audio file

Choose **System → Play audio file**, then select a local WAV (`.wav` or `.wave`),
MP3, OGG/OGA, FLAC or Opus file with the file chooser. Volume is adjustable from
0 to 100%. Leave **Maximum play time** blank to play the full file, or enter
`MM:SS` / `H:MM:SS` to stop earlier.

In a multiple-action or toggle sequence, the next step waits until playback
reaches the end of the file or the configured limit. The key shows the animated
**RUN** feedback while playback is active, including when audio is the only
action. Playback stops promptly when LinuxStreamDeck shuts down, and file,
decoder or pipeline failures appear as an action error.

Press the same key again during playback to stop its current invocation and
restart that key's sequence, including the audio, from the beginning. The
replacement waits until the previous audio pipeline has stopped, so clips never
overlap. If you press rapidly several times, canceled queued invocations are
skipped and only the latest restart runs.

### 🔐 OBS password storage

Your OBS password is stored asynchronously in the desktop Secret Service (GNOME
Keyring), never in `config.json`, `config.json.bak` or an export. On its first
run after upgrading, LinuxStreamDeck automatically moves any existing plaintext
password to the keyring and removes it from both configuration files. Configuration
files are atomically written with user-only (`0600`) permissions.

If secure storage is unavailable, any plaintext password is still removed. The
password then lasts only for the current session, so enter it again after restarting
the app.

### 💜 Connecting your Twitch account

Pick any Twitch action in the key editor and a **Connect** banner appears right
there, so you never have to go looking. You can also open it at any time from
the menu next to the profile selector, under **Twitch account…**.

1. Press **Connect a Twitch account**.
2. A short code appears. Press **Open Twitch** — it opens the activation page
   with the code already filled in, and the code stays on screen in case you
   would rather finish on your phone.
3. Approve the permissions. The dialog notices by itself and says who is
   connected.

Nothing secret is ever sent from your machine, and there is no password, no
browser extension and no local server. LinuxStreamDeck asks for exactly three
permissions, which is everything the Twitch keys use and nothing more:

| Permission | Used by |
| --- | --- |
| `channel:manage:broadcast` | Set the title, set the category, create a stream marker |
| `clips:edit` | Create a clip |
| `moderator:read:followers` | Show the follower count |

**About the Client ID.** You do not need one — LinuxStreamDeck ships its own, so
the **Client ID** field in the dialog is there only if you would rather use an
application registered under your own Twitch account. To do that, register one
at [dev.twitch.tv/console/apps](https://dev.twitch.tv/console/apps) with the
OAuth Redirect URL `http://localhost` and the client type **Public**, then paste
its Client ID in.

A Client ID is a public identifier, not a secret — this is the authorization
flow designed for applications that cannot keep secrets, which is why there is
no client secret anywhere in the setup. Twitch counts its rate limit per Client
ID **per user**, so a shared one costs you nothing.

Your access and refresh tokens are stored in the desktop keyring under their own
entry, never in `config.json`, its backup or an exported configuration. Moving a
configuration to another computer therefore never carries your Twitch account
with it; connect again there, exactly as you re-enter the OBS password.

**Ad breaks need a Twitch Affiliate or Partner account.** If yours is neither,
that key is faded and pressing it says so — Twitch's own answer is a cooldown
that never expires, and sometimes a success for an ad that never ran, so
LinuxStreamDeck checks your account type instead of trusting it. The Twitch
account dialog tells you which you are.

**Ad breaks, raids and announcements need permissions the earlier release did
not ask for.** If you connected your account before this version, the Twitch
account dialog says which ones are missing — connect again to grant them. The
pre-flight reports it too.

**Disconnecting takes two steps, and that is Twitch's rule rather than ours.**
**Disconnect** removes the tokens from your keyring and revokes them, so this
application can no longer do anything with your account. Twitch, however, keeps
the authorization listed under **Settings → Connections** until you remove it
there, and offers no way for an application to do that for you — so the dialog
gives you a button that opens that page.

### Sending keyboard shortcuts

Wayland deliberately blocks applications from injecting key events, so the
**Keyboard shortcut** and **Shortcut switch** actions need a helper that works
below the compositor.

**The `.deb` pulls it in for you**: `ydotool` is listed under `Recommends`, so
`sudo apt install ./linux-stream-deck-<version>.deb` installs it along with
`playerctl` for media control. Both stay optional — the package still installs
if your distribution does not carry them.

Running from source? Install it once yourself:

```bash
sudo apt install ydotool          # works on Wayland and X11, any compositor
```

`wtype` (wlroots) and `xdotool` (X11) are used instead when they are what you
have installed. Both major ydotool versions are supported: the 0.x that Debian
and Ubuntu ship and the newer 1.x, whose command syntax differs. Nothing else in
LinuxStreamDeck depends on any of them — without a backend these two actions
simply report what to install, and everything else keeps working.

Pick a **preset** to fill the shortcut field — Cut, Copy, Paste, Undo, Save,
snap window left/right, screenshots and more — then edit it, because desktops do
rebind these. Shortcuts are written as `ctrl+shift+s`, `super+left` or `print`.

> **Note:** ydotool writes to `/dev/uinput`. If a shortcut reports a permission
> error, either give your user access to that device or run the `ydotoold`
> service.

### Running in the status area

Open the profiles menu (⋮) → **Preferences…** to choose what closing the window
does and whether LinuxStreamDeck starts with your session.

- **Keep running in the status area** (default) hides the window and leaves the
  Stream Deck fully working. Nothing is lost: an unfinished key edit is still
  there when you reopen the window, so nothing is asked when you close it.
- **Quit LinuxStreamDeck** closes the application and releases the deck, asking
  first if a key has unsaved changes.

Clicking the status icon opens a menu to **switch profile**, **open the window**
and **quit**. Switching a profile from there still asks about unsaved key
changes, reopening the window if needed.

#### Desktop support

The icon uses the *StatusNotifierItem* standard, so no extra package is needed
where the desktop already speaks it:

| Desktop | Status icon |
| --- | --- |
| KDE Plasma, COSMIC, Budgie, Cinnamon, LXQt | ✅ Works out of the box |
| Ubuntu (GNOME session) | ✅ Ships the AppIndicator extension enabled |
| GNOME (Fedora, Debian, Arch…) | ⚠️ Needs the **AppIndicator and KStatusNotifierItem Support** extension |
| XFCE / MATE | ⚠️ Needs the panel's *Status Notifier* plugin (`xfce4-statusnotifier-plugin`) |
| Sway, Hyprland, i3 with Waybar | ✅ Waybar's `tray` module |
| Bare XEmbed trays (`stalonetray`, `trayer`, polybar) | ❌ XEmbed only, no StatusNotifierItem |

The icon is also published when no status area is running yet, so a panel that
starts after LinuxStreamDeck — the usual case at session login — still picks it
up without restarting the app.

Where the standard is unavailable, nothing breaks: the preferences dialog says
so and closing the window quits as usual. The window can never disappear with no
way back.

**Start automatically on login** writes a normal startup entry to
`~/.config/autostart/`, launching LinuxStreamDeck straight into its status icon.
It applies to this computer only and is never part of an exported configuration,
and your desktop's own *Startup Applications* tool can disable it too.

## ✨ Create a key with AI

Select a key and click **Create with AI...** to ask OpenAI or Claude for a key
proposal. Choose the provider and model, enter your own provider API key, and
describe the result you want. API access is billed separately by the selected
provider; it is not included with LinuxStreamDeck.

The dialog opens on whichever part you still need. The first time, the provider
settings are open and waiting for an API key. Once one is stored they fold into a
single row that states what will be used — *Claude · claude-haiku-4-5 · API key
saved · OBS context on* — so the description field is the first thing you see and
the first thing focused. Click that row whenever you want the settings back.
**Generate proposal** is pinned to the bottom of the dialog and is always
visible.

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
action catalogue and converted into a preview. Proposals with several actions
also come with a **Step name** on each one, so the list reads as plain steps;
the preview always shows that name next to the real action it runs. Nothing is executed or saved
automatically: load the proposal into the existing editor, review every action
and parameter, then press **Save** only if you want to keep it.

## 💾 Import and export configuration

Use the profiles menu (⋮) in the header to choose **Export configuration** or
**Import configuration**.

- **Export** creates a portable `.lsdconfig` ZIP archive. Format v4 contains the
  full JSON configuration including folder keys and everything inside them,
  custom key icons, supported audio referenced by
  **Play audio file** or a countdown timer's completion sound, screen saver and
  after-exit display settings, the selected custom exit image, and non-secret
  OBS settings, but never the OBS password or provider API keys.
  Identical audio files are stored once, even when both actions reference the
  same content. Each audio file is limited to 200 MiB and bundled audio to
  500 MiB total; the custom exit image is limited to 50 MiB. Built-in Material
  Design Icons remain lightweight `mdi:` references because they ship with
  LinuxStreamDeck. Missing or oversized files, and files with an unsupported
  extension, keep their original local reference and produce an export warning.
- **Import** replaces all current profiles, pages, keys and settings after you
  confirm the warning. The previous configuration is saved as
  `~/.config/linuxstreamdeck/config.json.bak`. Bundled custom icons and audio are
  restored under `~/.config/linuxstreamdeck/imported-icons/` and
  `~/.config/linuxstreamdeck/imported-audio/`; a bundled custom exit image is
  restored under `~/.config/linuxstreamdeck/imported-exit-images/`. Archive paths
  and sizes are validated before files are written. Brightness, screen saver,
  after-exit display and OBS settings are applied immediately, and OBS reconnects
  with the imported settings. The current v4 plus older v1, v2 and v3 exports
  are accepted; an older LinuxStreamDeck refuses a v4 file rather than
  silently dropping its folders. The import keeps this computer's keyring credentials and ignores
  password fields in older exports. When moving to another computer, enter the
  OBS password and any provider API keys again.

### Sharing a single key

Right-click a key in the grid and choose **Export key…** or **Import key…**.

- **Export key** writes a `.lsdkey` ZIP archive with that key alone: its type,
  actions and parameters, appearance and label size, plus its custom icon and any
  audio referenced by **Play audio file** or a countdown timer's completion sound.
  A folder key travels with every key inside it, and their assets too.
  Built-in `mdi:` icons stay as references and identical audio is stored once.
  The same size limits as a full export apply, and missing files produce a
  warning instead of failing the export.
- **Import key** validates the archive and writes it into the key you
  right-clicked, replacing whatever was there. Bundled icons and audio are
  restored under `~/.config/linuxstreamdeck/imported-icons/` and
  `~/.config/linuxstreamdeck/imported-audio/`, with archive paths and sizes
  validated first. A full `.lsdconfig` export is rejected here, and a `.lsdkey`
  file is rejected by **Import configuration**.

## 🗂️ Project structure

```
build.sh · run.sh · install-udev.sh    # prepare / launch / USB permissions
packaging/         # build-deb.sh, .desktop, icon, AppStream metainfo, scripts → .deb
linuxstreamdeck/
├── ai/            # OpenAI/Claude requests, bounded context and proposal validation
├── core/          # events, config, actions, controller, clocks, audio, secrets, icons
├── device/        # physical deck, startup/saver/exit displays and key rendering
├── obs/           # obs-websocket v5 client + full catalogue of OBS actions
├── twitch/        # device-code authorization, Helix client and Twitch actions
├── ui/            # GTK4/Libadwaita: window, editor, AI, OBS/Twitch/deck settings
└── assets/icons/  # icon library (Material Design Icons font + index)
data/udev/         # udev rule for device access
```

## 🙌 Acknowledgements

- The Linux Stream Deck community and the open-source projects that paved the way.
- [python-elgato-streamdeck](https://github.com/abcminiuser/python-elgato-streamdeck) and
  [obsws-python](https://github.com/aatikturk/obsws-python) — the libraries this stands on.
- [Material Design Icons](https://pictogrammers.com/library/mdi/) (Apache-2.0), bundled as
  the built-in icon library.

## Contributing

Contributions are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) before
submitting a change and follow the [Code of Conduct](CODE_OF_CONDUCT.md).
Report suspected vulnerabilities privately according to
[SECURITY.md](SECURITY.md).

## 📄 License

[GPL-3.0-or-later](LICENSE) — © JavocSoft
