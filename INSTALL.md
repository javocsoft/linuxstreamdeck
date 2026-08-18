# Installing LinuxStreamDeck

Three formats. Pick one.

| Your system | Use |
| --- | --- |
| Debian, Ubuntu, Pop!_OS, Mint | the **`.deb`** |
| Fedora, Arch, openSUSE Tumbleweed | the **AppImage** or the **Flatpak** |
| Debian 12, Ubuntu 22.04, RHEL 9 | the **Flatpak** (the AppImage needs a newer glibc) |
| Anything else | the **Flatpak** |

**The `.deb` will not install on Fedora.** It is a Debian package and its
dependencies are Debian package names; there is nothing to be done about that.

---

## Whichever you choose: the USB rule

**Do this, or the deck is never opened and the application looks broken.**
Linux does not give ordinary users access to a USB device by default, and no
portable package can install a system rule for you. The `.deb` installs it;
Flatpak and AppImage users must install it with the commands below.

Every method below writes the same one-line rule and reloads it. Afterwards,
**unplug the Stream Deck and plug it back in.**

---

## The `.deb` (Debian, Ubuntu, Pop!_OS, Mint)

```bash
sudo apt install ./linux-stream-deck-<version>.deb
```

The USB rule is installed and reloaded for you. Replug the deck once, then
launch **LinuxStreamDeck** from the applications menu. The package also refreshes
the desktop entry and hicolor icon caches automatically, so the application menu
and dock can resolve its installed icon.

---

## The Flatpak (any distribution)

Double-clicking the `.flatpak` opens your software centre, which is fine. From
a terminal:

```bash
flatpak install ./linuxstreamdeck-<version>.flatpak
```

> **It will download about 400 MB.** The file itself is only ~6 MB: it carries
> the application, not the GTK/GNOME runtime it runs on. That comes from
> Flathub the first time, and is shared with every other Flatpak afterwards.
>
> If the install complains that it cannot find `org.gnome.Platform`, add
> Flathub first:
> ```bash
> flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
> ```

Then the USB rule:

```bash
flatpak run --command=cat com.javocsoft.LinuxStreamDeck /app/share/linuxstreamdeck/70-linuxstreamdeck.rules | sudo tee /etc/udev/rules.d/70-linuxstreamdeck.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
```

Replug the deck, then run it:

```bash
flatpak run com.javocsoft.LinuxStreamDeck
```

---

## The AppImage (one file, nothing installed)

**Double-clicking it usually does nothing**, because files do not arrive
executable. Mark it, then run it:

```bash
chmod +x LinuxStreamDeck-<version>-x86_64.AppImage
./LinuxStreamDeck-<version>-x86_64.AppImage
```

If it still does not start, your system probably has no FUSE. This form needs
none:

```bash
./LinuxStreamDeck-<version>-x86_64.AppImage --appimage-extract-and-run
```

Then the USB rule:

```bash
./LinuxStreamDeck-<version>-x86_64.AppImage --appimage-extract usr/share/linuxstreamdeck/70-linuxstreamdeck.rules
sudo install -m644 squashfs-root/usr/share/linuxstreamdeck/70-linuxstreamdeck.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

Replug the deck and run it again.

> **Needs glibc 2.39 or newer** — Fedora 40+, Ubuntu 24.04+, Arch, openSUSE
> Tumbleweed. On Debian 12, Ubuntu 22.04 or RHEL 9 it will refuse to start;
> use the Flatpak, which brings its own runtime.

---

## Optional: four desktop tools

Everything built on the network works without these: **OBS, Twitch, Home
Assistant, Elgato Key Lights by IP address, HTTP keys, timers, the screen
saver.** Install a tool only if you want the keys that use it.

| Tool | The keys it enables |
| --- | --- |
| `playerctl` | media transport, and showing what is playing |
| `pactl` | per-application volume and mute, audio device switching, soundboard |
| `ydotool` | keyboard shortcut keys |
| `avahi-browse` | finding Key Lights on the network automatically |

```bash
# Fedora
sudo dnf install playerctl pulseaudio-utils ydotool avahi-tools
# Debian, Ubuntu, Pop!_OS
sudo apt install playerctl pulseaudio-utils ydotool avahi-utils
# Arch
sudo pacman -S playerctl libpulse ydotool avahi
```

A missing tool disables only its own keys, and the key says what to install
rather than failing silently.

`ydotool` also needs its daemon running and access to `/dev/uinput`; see its own
documentation for your distribution.

---

## If something is wrong

| What you see | What it means |
| --- | --- |
| The deck never appears | The USB rule. Install it as above and replug the deck. |
| A faded key | Every action on it needs a connection that is not there: OBS closed, no Twitch account, no Home Assistant. |
| A key outlined in red | Its action just failed. The status bar says why. |
| A key showing `--` | That measurement is not available on this machine. A zero would be a claim; a dash is the truth. |
| A dropdown that is empty | Whatever fills it could not be asked. The field stays typable so you can enter a value by hand. |

The full log is at `~/.config/linuxstreamdeck/linuxstreamdeck.log` and opens
from the profile menu. That is the thing to attach to a bug report:
<https://github.com/javocsoft/linuxstreamdeck/issues>

---

## A note on the Flatpak sandbox

The Flatpak asks for permission to run programs on your machine
(`--talk-name=org.freedesktop.Flatpak`). It needs that for the four tools
above: they belong to your desktop, not to the application, and none exists
inside a sandbox.

An application that can start host processes is not meaningfully confined - it
is the same permission a terminal emulator or an IDE asks for. If you would
rather keep the confinement, build the Flatpak yourself with that line removed
from the manifest; the audio, media and shortcut keys then report what is
missing, and everything else is unaffected.
