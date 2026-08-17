#!/usr/bin/env bash
# Runs inside the container from Dockerfile. Not meant to be run directly -
# use packaging/build-appimage.sh, which builds the image and calls this.
set -euo pipefail

SRC=/src
OUT=/out
APPID="com.javocsoft.LinuxStreamDeck"
APPDIR=/tmp/AppDir
VERSION="${VERSION:-$(grep -oP '^version\s*=\s*"\K[^"]+' "$SRC/pyproject.toml")}"

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }

rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/lib" "$APPDIR/usr/share"

# ---- the application and its pip-only dependencies ------------------------
# --system-site-packages so PyGObject, cairo and Pillow come from the distro
# rather than being rebuilt; those are the ones with C extensions that have to
# match the GTK the AppImage bundles.
info "Installing the application into the AppDir…"
PYVER="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
SITE="$APPDIR/usr/lib/python${PYVER}/site-packages"
mkdir -p "$SITE"

# /src is mounted read-only on purpose, so the build cannot dirty the working
# tree it was started from. setuptools insists on writing an egg-info directory
# beside the sources, so it gets a copy to write into instead.
WORK=/tmp/src
rm -rf "$WORK"
cp -a "$SRC" "$WORK"
rm -rf "$WORK/.git" "$WORK/.venv" "$WORK/build" "$WORK/dist" "$WORK/.flatpak-builder"

pip3 install --break-system-packages --no-compile --target="$SITE" \
    "$WORK" "streamdeck>=0.9.5" "obsws-python>=1.7"

# Pillow and PyGObject come from the system packages; linuxdeploy copies the
# shared libraries they need, but the Python packages themselves have to be
# carried across by hand.
for pkg in PIL gi cairo; do
    src="$(python3 -c "import $pkg, os; print(os.path.dirname($pkg.__file__))" 2>/dev/null || true)"
    [ -n "$src" ] && [ -d "$src" ] && cp -r "$src" "$SITE/" && info "  carried $pkg"
done
cp -r /usr/lib/python3/dist-packages/*.pth "$SITE/" 2>/dev/null || true

# ---- desktop integration --------------------------------------------------
install -Dm644 "$SRC/packaging/$APPID.desktop" \
    "$APPDIR/usr/share/applications/$APPID.desktop"
install -Dm644 "$SRC/packaging/$APPID.svg" \
    "$APPDIR/usr/share/icons/hicolor/scalable/apps/$APPID.svg"
install -Dm644 "$SRC/packaging/$APPID.svg" "$APPDIR/$APPID.svg"
# Keep the source metainfo templated so every package derives its release from
# the same version source.  appimagetool still discovers the legacy .appdata.xml
# filename, while AppStream consumers use .metainfo.xml, so carry both names.
RELEASE_DATE="$(date -u +%F)"
mkdir -p "$APPDIR/usr/share/metainfo"
sed -e "s/@VERSION@/$VERSION/g" -e "s/@DATE@/$RELEASE_DATE/g" \
    "$SRC/packaging/$APPID.metainfo.xml" \
    > "$APPDIR/usr/share/metainfo/$APPID.metainfo.xml"
cp "$APPDIR/usr/share/metainfo/$APPID.metainfo.xml" \
    "$APPDIR/usr/share/metainfo/$APPID.appdata.xml"

# The udev rule travels inside the image so the user can install it without
# having to find the repository. An AppImage cannot install it itself.
install -Dm644 "$SRC/data/udev/70-linuxstreamdeck.rules" \
    "$APPDIR/usr/share/linuxstreamdeck/70-linuxstreamdeck.rules"

# ---- the interpreter ------------------------------------------------------
# CPython is bundled, and it has to be. Using the host's python3 was the first
# design and it is broken beyond repair: every C extension carried here - all
# of PyGObject, Pillow and pycairo - is compiled against one CPython ABI
# (cpython-312, from this base image), and an interpreter of any other version
# cannot load them. Fedora 41 ships 3.13, so the AppImage failed there with
# "No module named linuxstreamdeck"; fixing only the path would have moved the
# failure one step later, onto the ABI.
#
# Bundling the interpreter is what makes those extensions loadable at all: it
# can never disagree with what they were built against.
info "Bundling CPython ${PYVER}…"
cp "$(readlink -f "$(command -v python3)")" "$APPDIR/usr/bin/python3"

STDLIB="/usr/lib/python${PYVER}"
mkdir -p "$APPDIR/usr/lib/python${PYVER}"
cp -a "$STDLIB"/. "$APPDIR/usr/lib/python${PYVER}/"
# Only what matters when developing; lib-dynload stays, it is the stdlib's own
# C extensions and nothing imports without it.
rm -rf "$APPDIR/usr/lib/python${PYVER}/test" \
       "$APPDIR/usr/lib/python${PYVER}/idlelib" \
       "$APPDIR/usr/lib/python${PYVER}/tkinter" \
       "$APPDIR/usr/lib/python${PYVER}/turtledemo"
find "$APPDIR/usr/lib/python${PYVER}" -name '__pycache__' -type d -prune \
     -exec rm -rf {} + 2>/dev/null || true

# ---- the launcher ---------------------------------------------------------
# Unquoted heredoc: PYVER is baked in at build time. Asking the machine for it
# is exactly the bug above - the bundled interpreter is the only one that can
# load the bundled extensions, so its version is not a run-time question.
cat > "$APPDIR/usr/bin/linuxstreamdeck" <<LAUNCH
#!/usr/bin/env bash
HERE="\$(dirname "\$(readlink -f "\$0")")"
ROOT="\$(dirname "\$(dirname "\$HERE")")"
PYVER="${PYVER}"
# PYTHONHOME is what makes the bundled interpreter find the bundled standard
# library rather than the host's.
# linuxdeploy gives every deployed .so an rpath of \$ORIGIN - its own
# directory. That is right for pillow.libs, whose dependencies are siblings,
# and wrong for gi/_gi.so, which sits three levels down in site-packages and
# needs libgirepository from usr/lib. Without this the application dies on
# "import gi" with libgirepository-1.0.so.1: cannot open shared object file.
export LD_LIBRARY_PATH="\$ROOT/usr/lib:\${LD_LIBRARY_PATH:-}"
export GI_TYPELIB_PATH="\$ROOT/usr/lib/girepository-1.0:\${GI_TYPELIB_PATH:-}"
export PYTHONHOME="\$ROOT/usr"
export PYTHONPATH="\$ROOT/usr/lib/python\${PYVER}/site-packages:\${PYTHONPATH:-}"
# GStreamer looks for its plugins in a directory it is told about, and a stale
# registry from the host would otherwise point at libraries that are not here.
export GST_PLUGIN_SYSTEM_PATH="\$ROOT/usr/lib/gstreamer-1.0"
export GST_PLUGIN_PATH="\$ROOT/usr/lib/gstreamer-1.0"
export GST_REGISTRY="\${XDG_CACHE_HOME:-\$HOME/.cache}/linuxstreamdeck-gst.registry"
export GST_PLUGIN_SCANNER="\$ROOT/usr/lib/gstreamer1.0/gstreamer-1.0/gst-plugin-scanner"
exec "\$ROOT/usr/bin/python3" -m linuxstreamdeck "\$@"
LAUNCH
chmod +x "$APPDIR/usr/bin/linuxstreamdeck"

# ---- bundle the GTK stack -------------------------------------------------
info "Bundling GTK, libadwaita and their run-time data…"
export DEPLOY_GTK_VERSION=4
export LDAI_UPDATE_INFORMATION=""
# Everything reached through a GObject-Introspection typelib has to be named
# explicitly. linuxdeploy follows ELF dependencies, and PyGObject loads these
# by name at run time, so nothing links to them and nothing traces to them.
# Verified the hard way: the first AppImage bundled Adw-1.typelib without
# libadwaita, started perfectly on a machine that happened to have libadwaita
# installed, and would have failed on the Fedora box it exists for.
LIB=/usr/lib/x86_64-linux-gnu
RUNTIME_LIBS=(
    "$LIB/libpython${PYVER}.so.1.0"   # the bundled interpreter needs it
    "$LIB/libhidapi-libusb.so.0"      # opening the deck
    "$LIB/libhidapi-hidraw.so.0"
    # hidapi-libusb is useless without it, and it is the one library on
    # linuxdeploy's "every desktop has this" list that a minimal Fedora
    # install really can lack. Without it the application starts and no
    # deck is ever found, which reads as a broken application.
    "$LIB/libusb-1.0.so.0"
    # GTK4 will not dlopen without these, and girepository dlopens
    # libgtk-4 to resolve its GTypes. When that fails it does not raise:
    # every type comes back as TYPE_NONE and PyGObject dies on an assert
    # inside gi/overrides/Gdk.py that mentions nothing about libraries.
    # A Wayland desktop has them; anything else, and a container, does not.
    # The soundboard routes a sound key into a null sink through
    # pulsesink. Without libpulse that plugin is unloadable and the
    # prune below removes it, which loses the feature silently.
    "$LIB/libpulse.so.0"
    # libpulse pulls this in, and the plugin prune below only follows a
    # plugin's direct NEEDED entries, not its libraries' own.
    "$LIB/libX11-xcb.so.1"
    "$LIB/libwayland-client.so.0"
    "$LIB/libwayland-cursor.so.0"
    "$LIB/libwayland-egl.so.1"
    "$LIB/libadwaita-1.so.0"          # the entire user interface
    "$LIB/libsecret-1.so.0"           # the keyring: OBS, Twitch, Home Assistant
    "$LIB/libgstreamer-1.0.so.0"      # sys.audio playback
)
# Every libgst* rather than a hand-written list. The list was wrong twice:
# once missing libgstriff, without which wavparse is unloadable and .wav - the
# commonest sound-key format - silently stops working. They are a few hundred
# kilobytes each and enumerating them cannot go stale.
for so in "$LIB"/libgst*-1.0.so.0; do
    [ -f "$so" ] && RUNTIME_LIBS+=("$so")
done

LIB_ARGS=()
for so in "${RUNTIME_LIBS[@]}"; do
    if [ -f "$so" ]; then
        LIB_ARGS+=(--library "$so")
    else
        echo "WARNING: $so is not on this build host and will not be bundled" >&2
    fi
done

# The interpreter is handed over as an executable, not as a library, so
# linuxdeploy traces what *it* needs. Naming libpython alone was not enough:
# the bundled python3 binary also wants libexpat, libz, libffi and more, and a
# Fedora container with no python at all stopped at "libexpat.so.1: cannot
# open shared object file". --deploy-deps-only does the same for lib-dynload,
# the standard library's own C extensions - _ssl wants OpenSSL, _sqlite3 wants
# SQLite, and each one that is missing breaks an import rather than the start.
/opt/tools/linuxdeploy/AppRun \
    --appdir "$APPDIR" \
    --plugin gtk \
    --desktop-file "$APPDIR/usr/share/applications/$APPID.desktop" \
    --icon-file "$APPDIR/usr/share/icons/hicolor/scalable/apps/$APPID.svg" \
    --executable "$APPDIR/usr/bin/python3" \
    --deploy-deps-only "$APPDIR/usr/lib/python${PYVER}/lib-dynload" \
    --deploy-deps-only "$SITE" \
    "${LIB_ARGS[@]}"

# The GTK plugin deploys the typelibs it thinks are needed and its idea is
# incomplete: it shipped GdkWayland-4.0, GdkX11-4.0 and Gtk-4.0 but not the
# core Gdk-4.0, and not Adw-1 either. The symptom is not a missing-file error
# - girepository simply answers TYPE_NONE for every type it cannot describe,
# and PyGObject dies on "assert g_type != TYPE_NONE" deep inside
# gi/overrides/Gdk.py. Copying the whole directory is a few megabytes and
# removes the entire class of problem.
TYPELIB_SRC="$LIB/girepository-1.0"
if [ -d "$TYPELIB_SRC" ]; then
    mkdir -p "$APPDIR/usr/lib/girepository-1.0"
    cp -an "$TYPELIB_SRC"/*.typelib "$APPDIR/usr/lib/girepository-1.0/" 2>/dev/null || true
    info "  carried $(ls "$APPDIR/usr/lib/girepository-1.0" | wc -l) typelibs"
fi

# GStreamer finds its plugins through a directory, not through the linker, so
# the plugins have to be copied and the launcher has to point at them.
GST_SRC="$LIB/gstreamer-1.0"
if [ -d "$GST_SRC" ]; then
    mkdir -p "$APPDIR/usr/lib/gstreamer-1.0"
    cp -a "$GST_SRC"/*.so "$APPDIR/usr/lib/gstreamer-1.0/" 2>/dev/null || true
    # A plugin whose own dependencies did not come along cannot load, and
    # GStreamer complains about each one on every start. That reads as a broken
    # installation and is only noise: the ones that fail are codecs and
    # analysis this application never asks for.
    #
    # The check must NOT be `ldd`. ldd resolves against this build container,
    # where every one of those libraries is installed as a dependency of the
    # gstreamer packages - so it finds everything and drops nothing, and the
    # plugins fail on the target instead. That is the same mistake as testing
    # the AppImage on the machine that built it. Each NEEDED entry is compared
    # against what the AppDir actually carries, plus the handful of libraries
    # every Linux has and no AppImage should bundle.
    UNIVERSAL='^(libc|libm|libdl|librt|libpthread|libgcc_s|libstdc\+\+|libresolv|ld-linux.*)\.so'
    dropped=0
    for plugin in "$APPDIR/usr/lib/gstreamer-1.0"/*.so; do
        [ -f "$plugin" ] || continue
        for need in $(objdump -p "$plugin" 2>/dev/null | awk '/NEEDED/{print $2}'); do
            [ -e "$APPDIR/usr/lib/$need" ] && continue
            echo "$need" | grep -qE "$UNIVERSAL" && continue
            rm -f "$plugin"
            dropped=$((dropped + 1))
            break
        done
    done
    info "  carried $(ls "$APPDIR/usr/lib/gstreamer-1.0" | wc -l) GStreamer plugins ($dropped dropped as unloadable)"

    # GStreamer inspects plugins in a helper process. Without the binary it
    # warns on every start about GST_PLUGIN_SCANNER.
    SCANNER="$(find /usr/lib/x86_64-linux-gnu -name gst-plugin-scanner 2>/dev/null | head -1)"
    if [ -n "$SCANNER" ]; then
        install -Dm755 "$SCANNER" "$APPDIR/usr/lib/gstreamer1.0/gstreamer-1.0/gst-plugin-scanner"
    fi
fi

# ---- pack -----------------------------------------------------------------
info "Packing the AppImage…"
mkdir -p "$OUT"
ARCH=x86_64 /opt/tools/appimagetool/AppRun \
    "$APPDIR" "$OUT/LinuxStreamDeck-${VERSION}-x86_64.AppImage"

chmod +x "$OUT/LinuxStreamDeck-${VERSION}-x86_64.AppImage"

# The container runs as root, so without this the AppImage lands in the user's
# dist/ owned by root: they cannot chmod it, move it or delete it without sudo,
# and the outer script's own chmod fails. HOST_UID/HOST_GID come from the
# caller.
if [ -n "${HOST_UID:-}" ] && [ -n "${HOST_GID:-}" ]; then
    chown "$HOST_UID:$HOST_GID" "$OUT/LinuxStreamDeck-${VERSION}-x86_64.AppImage"
fi
info "Wrote $OUT/LinuxStreamDeck-${VERSION}-x86_64.AppImage"
