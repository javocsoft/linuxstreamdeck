#!/usr/bin/env bash
#
# Build a versioned Debian package:  dist/linux-stream-deck-<version>.deb
#
# The package is Architecture: all — it ships the pure-Python app plus the two
# pip-only dependencies (StreamDeck, obsws_python) vendored under
# /usr/lib/linuxstreamdeck/_vendor, and pulls the system pieces (GTK4/Adw via
# PyGObject, GStreamer, Pillow, hidapi and websocket-client) through apt Depends.
#
# Usage:
#   ./packaging/build-deb.sh            # version taken from pyproject.toml
#   ./packaging/build-deb.sh X.Y.Z      # explicit version
#   VERSION=X.Y.Z ./packaging/build-deb.sh
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
cd "$ROOT"

PKG="linux-stream-deck"            # Debian package name (and .deb filename stem)
APPDIR="linuxstreamdeck"           # install dir under /usr/lib and the command name
APPID="com.javocsoft.LinuxStreamDeck"
MAINTAINER="JavocSoft <javocsoft@gmail.com>"
HOMEPAGE="https://github.com/javocsoft/linuxstreamdeck"

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
err()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; }

# Rewrite `<key> = "..."` in $1 to the target version $3, keeping the two version
# sources (pyproject.toml and linuxstreamdeck/__init__.py) in sync with the build.
sync_version() {
    local file="$1" key="$2" target="$3" current
    current="$(grep -oP "^${key}\s*=\s*\"\K[^\"]+" "$file" || true)"
    if [[ -z "$current" ]]; then
        err "Could not find '${key} = \"...\"' in $file"; exit 1
    fi
    if [[ "$current" != "$target" ]]; then
        sed -i -E "s/^${key}[[:space:]]*=[[:space:]]*\".*\"/${key} = \"${target}\"/" "$file"
        info "  $file: ${key} ${current} -> ${target}"
    else
        info "  $file: ${key} already ${target}"
    fi
}

# Fail when a tracked file still hardcodes a different version of this app, the
# way an issue template or a document silently goes stale after a release.
# Exempt: dependency constraints (">=1.2.3"), which are somebody else's version,
# and any line marked "version-check: ignore".
#
# An IP address is not a version, and the naive three-number pattern reads the
# first three octets of one as though it were: 127.0.0.1 and 192.168.1.40 were
# both reported as stale versions once the pre-flight put addresses in the
# tree. Marking those lines as ignored would paper over it and cost an edit on
# every release, so the match instead refuses to start or end in the middle of
# a longer dotted run. A version at the end of a sentence still matches,
# because only a dot *followed by a digit* disqualifies one.
#
# Note the rule this comment obeys: write examples as X.Y.Z rather than as a
# plausible number, or the scan trips over the file explaining it.
check_hardcoded_versions() {
    local target="$1" stale=0 entry file line content found
    if ! command -v git >/dev/null 2>&1 || ! git rev-parse --git-dir >/dev/null 2>&1; then
        info "  not a git checkout; skipping the stale-version scan"
        return 0
    fi
    while IFS= read -r entry; do
        file="${entry%%:*}"; entry="${entry#*:}"
        line="${entry%%:*}"; content="${entry#*:}"
        # The two version sources are sync_version's job, and it runs after this.
        [[ "$file" == "pyproject.toml" || "$file" == "linuxstreamdeck/__init__.py" ]] \
            && continue
        [[ "$content" == *"version-check: ignore"* ]] && continue
        [[ "$content" =~ (\>=|\<=|==|~=) ]] && continue
        while IFS= read -r found; do
            [[ -z "$found" || "$found" == "$target" ]] && continue
            err "$file:$line hardcodes version $found (building $target)"
            err "    $(printf '%s' "$content" | sed 's/^[[:space:]]*//')"
            stale=1
        done < <(
            printf '%s' "$content" \
                | grep -oP '(?<![\d.])\d+\.\d+\.\d+(?!\.?\d)' || true
        )
    done < <(
        git ls-files -z \
            | xargs -0 grep -IHnE '[0-9]+\.[0-9]+\.[0-9]+' 2>/dev/null || true
    )
    if (( stale )); then
        err "Update those files, or mark the line with 'version-check: ignore'."
        return 1
    fi
    info "  no stale versions in tracked files"
    return 0
}

command -v dpkg-deb >/dev/null 2>&1 || { err "dpkg-deb not found (install 'dpkg-dev')."; exit 1; }

# ---- version: arg > env > pyproject.toml ----
VERSION="${1:-${VERSION:-$(grep -oP '^version\s*=\s*"\K[^"]+' pyproject.toml)}}"
if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    err "Version '$VERSION' is not X.Y.Z"; exit 1
fi
info "Building $PKG $VERSION (Architecture: all)"

# ---- keep the two version sources in sync with what we build ----
info "Syncing version to $VERSION…"
check_hardcoded_versions "$VERSION" || exit 1
sync_version "pyproject.toml" "version" "$VERSION"
sync_version "linuxstreamdeck/__init__.py" "VERSION" "$VERSION"

# ---- locate the vendored pure-Python deps from the project venv ----
VENV_PY="$ROOT/.venv/bin/python"
[[ -x "$VENV_PY" ]] || { err "No .venv — run ./build.sh first."; exit 1; }
read -r SD_DIR OBS_DIR < <("$VENV_PY" - <<'PY'
import os, StreamDeck, obsws_python
print(os.path.dirname(StreamDeck.__file__), os.path.dirname(obsws_python.__file__))
PY
)
[[ -d "$SD_DIR" && -d "$OBS_DIR" ]] || { err "Could not locate StreamDeck/obsws_python in the venv."; exit 1; }

# ---- staging tree ----
STAGE="$ROOT/build/deb/${PKG}_${VERSION}"
LIB="$STAGE/usr/lib/$APPDIR"
rm -rf "$STAGE"
mkdir -p "$STAGE/DEBIAN" \
         "$LIB/_vendor" \
         "$STAGE/usr/bin" \
         "$STAGE/usr/share/applications" \
         "$STAGE/usr/share/metainfo" \
         "$STAGE/usr/share/icons/hicolor/scalable/apps" \
         "$STAGE/usr/lib/udev/rules.d" \
         "$STAGE/usr/share/doc/$PKG"

# ---- the application package (without caches) ----
info "Staging the application and vendored dependencies…"
cp -r "$ROOT/linuxstreamdeck" "$LIB/"
cp -r "$SD_DIR" "$LIB/_vendor/"
cp -r "$OBS_DIR" "$LIB/_vendor/"
find "$LIB" -type d -name '__pycache__' -prune -exec rm -rf {} +
find "$LIB" -type f -name '*.py[co]' -delete

# ---- launcher (uses the system python3 + apt/system modules + vendored deps) ----
cat > "$STAGE/usr/bin/$APPDIR" <<LAUNCH
#!/usr/bin/python3
import sys
sys.path.insert(0, "/usr/lib/$APPDIR/_vendor")
sys.path.insert(0, "/usr/lib/$APPDIR")
from linuxstreamdeck.__main__ import main
sys.exit(main())
LAUNCH
chmod 755 "$STAGE/usr/bin/$APPDIR"

# ---- desktop entry, AppStream metadata, icon, udev rule, docs ----
cp "$HERE/$APPID.desktop" "$STAGE/usr/share/applications/"
cp "$HERE/$APPID.svg"     "$STAGE/usr/share/icons/hicolor/scalable/apps/"
# AppStream metainfo makes software centres show the app icon, summary and
# screenshot instead of a generic package entry. Inject the built version/date.
sed -e "s/@VERSION@/$VERSION/" -e "s/@DATE@/$(date +%F)/" \
    "$HERE/$APPID.metainfo.xml" > "$STAGE/usr/share/metainfo/$APPID.metainfo.xml"
cp "$ROOT/data/udev/70-linuxstreamdeck.rules" "$STAGE/usr/lib/udev/rules.d/"
cp "$ROOT/README.md" "$STAGE/usr/share/doc/$PKG/"
cat > "$STAGE/usr/share/doc/$PKG/copyright" <<EOF
Upstream-Name: LinuxStreamDeck
Source: $HOMEPAGE

Files: *
Copyright: JavocSoft
License: GPL-3.0-or-later
 This program is free software: you can redistribute it and/or modify it under
 the terms of the GNU General Public License as published by the Free Software
 Foundation, either version 3 of the License, or (at your option) any later
 version. On Debian systems the full text is in /usr/share/common-licenses/GPL-3.

Files: usr/lib/$APPDIR/linuxstreamdeck/assets/icons/*
Copyright: Pictogrammers (Material Design Icons)
License: Apache-2.0
EOF

# ---- maintainer scripts ----
cp "$HERE/postinst" "$STAGE/DEBIAN/postinst"
cp "$HERE/postrm"   "$STAGE/DEBIAN/postrm"
chmod 755 "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/postrm"

# ---- control ----
SIZE_KB="$(du -sk "$STAGE/usr" | cut -f1)"
cat > "$STAGE/DEBIAN/control" <<EOF
Package: $PKG
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Maintainer: $MAINTAINER
Homepage: $HOMEPAGE
Installed-Size: $SIZE_KB
Depends: python3 (>= 3.10), ca-certificates, python3-gi, python3-gi-cairo, gir1.2-gtk-4.0, gir1.2-adw-1, gir1.2-secret-1, gir1.2-gstreamer-1.0, gstreamer1.0-plugins-base, gstreamer1.0-plugins-good, gnome-keyring, libhidapi-libusb0, python3-pil, python3-websocket
Recommends: ydotool, playerctl, pulseaudio-utils, avahi-utils
Suggests: fonts-noto-cjk
Description: Elgato Stream Deck controller for Linux with OBS Studio integration
 LinuxStreamDeck is a GTK4/Libadwaita desktop application to control the Elgato
 Stream Deck on Linux, built around full OBS Studio integration (obs-websocket
 v5) with live feedback on the keys: the active scene lights up, recording turns
 red, going live turns green, a muted mic is marked.
 .
 It ships a large built-in icon library and works as a virtual on-screen deck,
 so you can configure and test everything even without the hardware connected.
EOF

# ---- build ----
mkdir -p "$ROOT/dist"
OUT="$ROOT/dist/${PKG}-${VERSION}.deb"
info "Assembling $OUT…"
dpkg-deb --root-owner-group --build "$STAGE" "$OUT" >/dev/null

info "Done ✔"
echo
dpkg-deb --info "$OUT"
echo
echo "Install with:  sudo apt install $OUT"
echo "        (or):  sudo dpkg -i $OUT && sudo apt -f install"
