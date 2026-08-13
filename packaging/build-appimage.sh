#!/usr/bin/env bash
# Build LinuxStreamDeck as an AppImage, in a container.
#
#   ./packaging/build-appimage.sh            build dist/LinuxStreamDeck-X.Y.Z-x86_64.AppImage
#   ./packaging/build-appimage.sh --rebuild  rebuild the container image first
#
# The build runs in a container rather than on this machine on purpose. An
# AppImage cannot run on a glibc older than the one it was built against, so
# the base image is what decides which distributions the result works on, and
# building on whatever happens to be installed here would make that accidental.
#
# Ubuntu 24.04 is the base because the application uses Adw.Dialog and
# Adw.ToolbarView, which need libadwaita 1.5, and 24.04 is the oldest Ubuntu
# with it. The floor that sets is glibc 2.39:
#
#   works:      Fedora 40+, Ubuntu 24.04+, Pop!_OS 24.04+, Arch,
#               openSUSE Tumbleweed
#   too old:    Debian 12 (glibc 2.36), Ubuntu 22.04 (2.35), RHEL 9
#
# For those, use the Flatpak: it brings its own runtime and does not care what
# the host has.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="linuxstreamdeck-appimage-builder"
CONTEXT="$ROOT/packaging/appimage"

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
err()  { printf '\033[1;31m error:\033[0m %s\n' "$*" >&2; }

RUNNER=""
for candidate in podman docker; do
    command -v "$candidate" >/dev/null 2>&1 && { RUNNER="$candidate"; break; }
done
[ -n "$RUNNER" ] || {
    err "Neither podman nor docker was found; one of them builds the image."
    err "  Fedora:  sudo dnf install podman"
    err "  Debian:  sudo apt install podman"
    exit 1
}

REBUILD=0
[ "${1:-}" = "--rebuild" ] && REBUILD=1

VERSION="$(grep -oP '^version\s*=\s*"\K[^"]+' "$ROOT/pyproject.toml")"

if (( REBUILD )) || ! "$RUNNER" image inspect "$IMAGE" >/dev/null 2>&1; then
    info "Building the container image (a few minutes the first time)…"
    "$RUNNER" build -t "$IMAGE" "$CONTEXT"
fi

mkdir -p "$ROOT/dist"

info "Building the AppImage for version $VERSION…"
# --privileged is not needed: the tools were extracted at image build time
# precisely so no FUSE mount is required here.
"$RUNNER" run --rm \
    -v "$ROOT":/src:ro \
    -v "$ROOT/dist":/out \
    -e VERSION="$VERSION" \
    -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
    "$IMAGE" \
    bash /src/packaging/appimage/build-inside.sh

OUT="$ROOT/dist/LinuxStreamDeck-${VERSION}-x86_64.AppImage"
[ -f "$OUT" ] || { err "The build finished but $OUT is not there."; exit 1; }
chmod +x "$OUT" 2>/dev/null || true
# INSTALL.md travels with the AppImage: a double click shows none of this.
cp -f "$ROOT/INSTALL.md" "$ROOT/dist/INSTALL.md"

cat <<NOTE

--------------------------------------------------------------------------
$OUT

Run it with:   ./$(basename "$OUT")

Two things the AppImage cannot do for itself:

1. USB permissions, without which the deck is never opened. The rule
   travels inside the image:

       ./$(basename "$OUT") --appimage-extract usr/share/linuxstreamdeck/70-linuxstreamdeck.rules
       sudo install -m644 squashfs-root/usr/share/linuxstreamdeck/70-linuxstreamdeck.rules /etc/udev/rules.d/
       sudo udevadm control --reload-rules && sudo udevadm trigger

   Then unplug the deck and plug it back in.

2. The optional host tools, for the keys that use them: ydotool for
   keyboard shortcuts, playerctl for media and what is playing, pactl
   (pulseaudio-utils) for per-application audio and the soundboard,
   avahi-utils for finding Key Lights. Each one missing disables only its
   own keys, and the key says what to install.

Requires glibc 2.39 or newer. On Debian 12, Ubuntu 22.04 or RHEL 9, build
the Flatpak instead: ./packaging/build-flatpak.sh
--------------------------------------------------------------------------
NOTE
