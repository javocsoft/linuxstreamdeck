#!/usr/bin/env bash
#
# Build/prepare LinuxStreamDeck:
#   - checks (or optionally installs) the system dependencies
#   - creates the .venv virtual environment (with access to the system GTK/PyGObject)
#   - installs the package and its Python dependencies
#   - checks that all the code compiles
#
# Usage:
#   ./build.sh          # prepares everything (warns if system packages are missing)
#   ./build.sh --apt    # also installs the system packages with apt (sudo)
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

# ---- optional system dependencies via --apt ----
if [[ "${1:-}" == "--apt" ]]; then
    info "Installing system dependencies with apt (requires sudo)…"
    sudo apt update
    sudo apt install -y "${SYSTEM_PKGS[@]}"
    shift || true
fi

command -v "$PY" >/dev/null 2>&1 || { err "$PY not found. Install Python 3.10+."; exit 1; }

# ---- system dependency check ----
missing=()
"$PY" -c "import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1')" 2>/dev/null \
    || missing+=(gir1.2-gtk-4.0 gir1.2-adw-1 python3-gi)
ldconfig -p 2>/dev/null | grep -q "libhidapi" || missing+=(libhidapi-libusb0)

if ((${#missing[@]})); then
    warn "Missing system packages: ${missing[*]}"
    warn "Install them with:  sudo apt install ${SYSTEM_PKGS[*]}"
    warn "…or re-run:  ./build.sh --apt"
else
    info "System dependencies: OK"
fi

# ---- virtual environment ----
if [[ ! -x "$VENV/bin/python" ]]; then
    info "Creating virtual environment in .venv (with access to system packages)…"
    "$PY" -m venv --system-site-packages "$VENV"
else
    info "Virtual environment already exists (.venv)"
fi

# ---- Python dependencies + editable install ----
info "Upgrading pip and installing the package and its dependencies…"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -e .

# ---- compilation check ----
info "Checking that the code compiles…"
"$VENV/bin/python" -m compileall -q linuxstreamdeck

info "Done ✔  Launch the app with:  ./run.sh"
if ((${#missing[@]})); then
    warn "Remember to install the missing system packages first (see above)."
fi
