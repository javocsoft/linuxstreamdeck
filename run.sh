#!/usr/bin/env bash
#
# Lanza LinuxStreamDeck usando el entorno virtual del proyecto.
#
# Uso:
#   ./run.sh                # arranca la app
#   LSD_DEBUG=1 ./run.sh    # arranca con log de depuración
#   ./run.sh [args...]      # los argumentos se pasan a la app
#
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

VENV="$DIR/.venv"
if [[ ! -x "$VENV/bin/python" ]]; then
    echo "No hay entorno virtual. Ejecuta primero:  ./build.sh" >&2
    exit 1
fi

# La app es de instancia única: una ventana vieja mostraría estado cacheado.
# Avisamos (sin bloquear) si ya hay una en ejecución.
if pgrep -f "bin/linuxstreamdeck" >/dev/null 2>&1; then
    echo "Aviso: ya parece haber una instancia de LinuxStreamDeck en ejecución." >&2
    echo "       Ciérrala antes de abrir otra para evitar ver una ventana antigua." >&2
fi

if [[ -x "$VENV/bin/linuxstreamdeck" ]]; then
    exec "$VENV/bin/linuxstreamdeck" "$@"
else
    exec "$VENV/bin/python" -m linuxstreamdeck "$@"
fi
