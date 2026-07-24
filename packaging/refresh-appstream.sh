#!/bin/sh
# Refresh the system AppStream cache after installing a locally built package.
set -eu

APP_ID="com.javocsoft.LinuxStreamDeck"
METAINFO="/usr/share/metainfo/$APP_ID.metainfo.xml"

info() {
    printf '%s\n' "$*"
}

if [ "$(id -u)" -ne 0 ]; then
    info "Run this script as root: sudo ./packaging/refresh-appstream.sh"
    exit 1
fi

if ! command -v appstreamcli >/dev/null 2>&1; then
    info "appstreamcli is not installed. Install the 'appstream' package and try again."
    exit 1
fi

if [ ! -f "$METAINFO" ]; then
    info "$METAINFO was not found. Install the LinuxStreamDeck .deb first."
    exit 1
fi

info "Refreshing the system AppStream cache..."
appstreamcli refresh-cache --force

if appstreamcli get "$APP_ID" >/dev/null 2>&1; then
    info "AppStream metadata for LinuxStreamDeck is available."
else
    info "Warning: LinuxStreamDeck is not present in the AppStream cache yet."
fi

info "Close and reopen your software centre or package viewer to see the refreshed metadata."
