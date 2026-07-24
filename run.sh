#!/usr/bin/env bash
#
# Launch LinuxStreamDeck using the project's virtual environment.
#
# Usage:
#   ./run.sh                # starts the app
#   LSD_DEBUG=1 ./run.sh    # starts with debug logging
#   ./run.sh [args...]      # the arguments are passed to the app
#
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

VENV="$DIR/.venv"
if [[ ! -x "$VENV/bin/python" ]]; then
    echo "No virtual environment. Run first:  ./build.sh" >&2
    exit 1
fi

# The app is single-instance: an old window would show cached state.
# Warn (without blocking) if one is already running.
if pgrep -f "bin/linuxstreamdeck" >/dev/null 2>&1; then
    echo "Warning: a LinuxStreamDeck instance already seems to be running." >&2
    echo "         Close it before opening another to avoid seeing an old window." >&2
fi

if [[ -x "$VENV/bin/linuxstreamdeck" ]]; then
    exec "$VENV/bin/linuxstreamdeck" "$@"
else
    exec "$VENV/bin/python" -m linuxstreamdeck "$@"
fi
