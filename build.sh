#!/usr/bin/env bash
#
# Compila/prepara LinuxStreamDeck:
#   - comprueba (u opcionalmente instala) las dependencias del sistema
#   - crea el entorno virtual .venv (con acceso a GTK/PyGObject del sistema)
#   - instala el paquete y sus dependencias de Python
#   - comprueba que todo el código compila
#
# Uso:
#   ./build.sh          # prepara todo (avisa si faltan paquetes del sistema)
#   ./build.sh --apt    # además instala los paquetes del sistema con apt (sudo)
#
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

VENV="$DIR/.venv"
PY="${PYTHON:-python3}"
SYSTEM_PKGS=(gir1.2-gtk-4.0 gir1.2-adw-1 libhidapi-libusb0 python3-gi python3-gi-cairo)

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
err()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; }

# ---- dependencias del sistema opcionales vía --apt ----
if [[ "${1:-}" == "--apt" ]]; then
    info "Instalando dependencias del sistema con apt (requiere sudo)…"
    sudo apt update
    sudo apt install -y "${SYSTEM_PKGS[@]}"
    shift || true
fi

command -v "$PY" >/dev/null 2>&1 || { err "No se encuentra $PY. Instala Python 3.10+."; exit 1; }

# ---- comprobación de dependencias del sistema ----
missing=()
"$PY" -c "import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1')" 2>/dev/null \
    || missing+=(gir1.2-gtk-4.0 gir1.2-adw-1 python3-gi)
ldconfig -p 2>/dev/null | grep -q "libhidapi" || missing+=(libhidapi-libusb0)

if ((${#missing[@]})); then
    warn "Faltan paquetes del sistema: ${missing[*]}"
    warn "Instálalos con:  sudo apt install ${SYSTEM_PKGS[*]}"
    warn "…o vuelve a ejecutar:  ./build.sh --apt"
else
    info "Dependencias del sistema: OK"
fi

# ---- entorno virtual ----
if [[ ! -x "$VENV/bin/python" ]]; then
    info "Creando entorno virtual en .venv (con acceso a los paquetes del sistema)…"
    "$PY" -m venv --system-site-packages "$VENV"
else
    info "Entorno virtual ya existe (.venv)"
fi

# ---- dependencias de Python + instalación editable ----
info "Actualizando pip e instalando el paquete y sus dependencias…"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -e .

# ---- comprobación de compilación ----
info "Comprobando que el código compila…"
"$VENV/bin/python" -m compileall -q linuxstreamdeck

info "Listo ✔  Lanza la app con:  ./run.sh"
if ((${#missing[@]})); then
    warn "Recuerda instalar antes los paquetes del sistema que faltan (ver arriba)."
fi
