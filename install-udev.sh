#!/usr/bin/env bash
# Installs the udev rules that give access to the Stream Deck without root.
# Usage: sudo ./install-udev.sh
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run with sudo: sudo $0" >&2
    exit 1
fi

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
install -m 644 "$DIR/data/udev/70-linuxstreamdeck.rules" /etc/udev/rules.d/
udevadm control --reload-rules
udevadm trigger
echo "udev rules installed. Unplug and reconnect the Stream Deck."
