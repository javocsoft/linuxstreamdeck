#!/usr/bin/env bash
# Build and install LinuxStreamDeck as a Flatpak.
#
#   ./packaging/build-flatpak.sh              build and install for this user
#   ./packaging/build-flatpak.sh --bundle     also write a single .flatpak file
#   ./packaging/build-flatpak.sh --clean      discard the build tree first
#
# The .flatpak bundle is the thing to hand to someone on Fedora, openSUSE,
# Arch or anything else: it carries the application and everything built for
# it, and installs with `flatpak install ./linuxstreamdeck.flatpak`.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APPID="com.javocsoft.LinuxStreamDeck"
MANIFEST="$ROOT/packaging/flatpak/$APPID.yml"
BUILDDIR="$ROOT/build/flatpak"
REPO="$ROOT/build/flatpak-repo"
RUNTIME_VERSION="50"

info()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m warning:\033[0m %s\n' "$*" >&2; }
err()   { printf '\033[1;31m error:\033[0m %s\n' "$*" >&2; }

BUNDLE=0
CLEAN=0
for arg in "$@"; do
    case "$arg" in
        --bundle) BUNDLE=1 ;;
        --clean)  CLEAN=1 ;;
        -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
        *) err "Unknown option: $arg"; exit 1 ;;
    esac
done

command -v flatpak >/dev/null 2>&1 || {
    err "flatpak not found."
    err "  Fedora:  sudo dnf install flatpak flatpak-builder"
    err "  Debian:  sudo apt install flatpak flatpak-builder"
    err "  Arch:    sudo pacman -S flatpak flatpak-builder"
    exit 1
}
command -v flatpak-builder >/dev/null 2>&1 || {
    err "flatpak-builder not found (see the install lines above)."
    exit 1
}

# ---- the remote and the runtime -------------------------------------------
# Installed per user, so nothing here needs root.
if ! flatpak remotes --user | grep -q '^flathub'; then
    info "Adding the flathub remote for this user…"
    flatpak remote-add --user --if-not-exists flathub \
        https://dl.flathub.org/repo/flathub.flatpakrepo
fi

for ref in "org.gnome.Platform//$RUNTIME_VERSION" "org.gnome.Sdk//$RUNTIME_VERSION"; do
    if ! flatpak info --user "$ref" >/dev/null 2>&1; then
        info "Installing $ref (this is about 1 GB the first time)…"
        flatpak install --user --noninteractive flathub "$ref"
    fi
done

# ---- build ----------------------------------------------------------------
if (( CLEAN )); then
    info "Discarding the previous build tree…"
    rm -rf "$BUILDDIR" "$REPO"
fi
mkdir -p "$(dirname "$BUILDDIR")"

info "Building $APPID…"
flatpak-builder --user --force-clean --install-deps-from=flathub \
    --repo="$REPO" \
    "$BUILDDIR" "$MANIFEST"

info "Installing for this user…"
flatpak install --user --noninteractive --reinstall "$REPO" "$APPID"

if (( BUNDLE )); then
    OUT="$ROOT/dist/linuxstreamdeck-$(grep -oP '^version\s*=\s*"\K[^"]+' "$ROOT/pyproject.toml").flatpak"
    mkdir -p "$(dirname "$OUT")"
    info "Writing a single-file bundle…"
    flatpak build-bundle "$REPO" "$OUT" "$APPID"
    # INSTALL.md travels with the bundle: none of what follows is visible to
    # somebody who was handed the file and double-clicked it.
    cp -f "$ROOT/INSTALL.md" "$(dirname "$OUT")/INSTALL.md"
    info "Bundle: $OUT"
    echo "         beside it: $(dirname "$OUT")/INSTALL.md"
    echo "         install it with: flatpak install ./$(basename "$OUT")"
fi

# ---- the two things a Flatpak cannot do for itself ------------------------
cat <<'NOTE'

--------------------------------------------------------------------------
Two things still have to happen on the host. A Flatpak cannot do either.

1. USB permissions. Without this the deck is never opened, and the status
   bar says so after the second attempt:

       sudo install -m644 data/udev/70-linuxstreamdeck.rules /etc/udev/rules.d/
       sudo udevadm control --reload-rules && sudo udevadm trigger

   Then unplug the deck and plug it back in.

2. The optional host tools, if you want the keys that use them. They are
   run on the host rather than in the sandbox, so install them the normal
   way for your distribution:

       ydotool     keyboard shortcut keys
       playerctl   media transport, and showing what is playing
       pactl       per-application volume, mute, audio device, soundboard
                   (package: pulseaudio-utils, or pipewire-pulse's tools)
       avahi-utils finding Elgato Key Lights on the network

   Each one that is missing disables only its own keys, and the key says
   what to install rather than failing silently.
--------------------------------------------------------------------------

Run it with:  flatpak run com.javocsoft.LinuxStreamDeck
NOTE
