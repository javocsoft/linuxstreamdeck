# The build environment for the AppImage.
#
# Ubuntu 24.04 is not an arbitrary choice. The application uses Adw.Dialog and
# Adw.ToolbarView, which need libadwaita 1.5, and 24.04 is the oldest Ubuntu
# that ships it. An AppImage cannot run on a glibc older than the one it was
# built against, so this base decides the floor: glibc 2.39.
#
# That covers Fedora 40 and newer, Ubuntu 24.04 and newer, Arch, openSUSE
# Tumbleweed and Pop!_OS 24.04. It does NOT cover Debian 12 (glibc 2.36),
# Ubuntu 22.04 (2.35) or RHEL 9. Those need the Flatpak, which carries its own
# runtime and does not care what the host has.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv python3-gi python3-gi-cairo \
        gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-secret-1 gir1.2-gstreamer-1.0 \
        gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
        libhidapi-libusb0 libhidapi-hidraw0 \
        libgtk-4-1 libadwaita-1-0 libsecret-1-0 \
        # The -dev packages are here only for their .pc files: the GTK
        # plugin asks pkg-config for gtk4's libdir to find the modules
        # directory, and without gtk4.pc it stops with
        # "there is no 'libdir' variable for 'gtk4' library".
        libgtk-4-dev libadwaita-1-dev \
        fonts-dejavu-core \
        python3-pil \
        ca-certificates curl file desktop-file-utils patchelf zsync \
        # Prerequisites of linuxdeploy-plugin-gtk, read out of the plugin
        # script itself rather than discovered one failed build at a time:
        # dpkg-architecture, pkg-config, find, and the two query tools it
        # uses to regenerate the loader and schema caches inside the AppDir.
        dpkg-dev pkg-config findutils \
        libgdk-pixbuf2.0-bin libglib2.0-bin librsvg2-common \
        libfuse2t64 xz-utils \
    && rm -rf /var/lib/apt/lists/*

# linuxdeploy and its GTK plugin do the part that is genuinely hard: finding
# the GI typelibs, the gdk-pixbuf loaders, the GSettings schemas and the
# GIO modules that GTK will look for at run time, and rewriting the rpaths.
# The GTK plugin has no releases at all - it is a single shell script on the
# default branch - so it is pinned to a commit rather than tracking master,
# which would let the build change under us without anything being edited here.
ARG GTK_PLUGIN_COMMIT=7a3fbc31a9e5075073ff8790f26effbac5f84453

WORKDIR /opt/tools
RUN curl -fsSLO https://github.com/linuxdeploy/linuxdeploy/releases/download/continuous/linuxdeploy-x86_64.AppImage \
 && curl -fsSL -o linuxdeploy-plugin-gtk.sh \
      https://raw.githubusercontent.com/linuxdeploy/linuxdeploy-plugin-gtk/${GTK_PLUGIN_COMMIT}/linuxdeploy-plugin-gtk.sh \
 && curl -fsSLO https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage \
 && chmod +x linuxdeploy-x86_64.AppImage appimagetool-x86_64.AppImage linuxdeploy-plugin-gtk.sh

# The AppImages here cannot mount FUSE inside a container, so they are
# extracted once and the extracted trees are used instead.
RUN ./linuxdeploy-x86_64.AppImage --appimage-extract >/dev/null \
 && mv squashfs-root linuxdeploy \
 && ./appimagetool-x86_64.AppImage --appimage-extract >/dev/null \
 && mv squashfs-root appimagetool \
 && rm -f ./*.AppImage

ENV PATH="/opt/tools:${PATH}"
WORKDIR /src
