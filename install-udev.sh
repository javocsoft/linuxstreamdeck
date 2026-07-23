#!/usr/bin/env bash
# Instala las reglas udev que dan acceso al Stream Deck sin root.
# Uso: sudo ./install-udev.sh
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Ejecuta con sudo: sudo $0" >&2
    exit 1
fi

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
install -m 644 "$DIR/data/udev/70-linuxstreamdeck.rules" /etc/udev/rules.d/
udevadm control --reload-rules
udevadm trigger
echo "Reglas udev instaladas. Desconecta y vuelve a conectar el Stream Deck."
