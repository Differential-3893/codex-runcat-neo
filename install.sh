#!/bin/sh
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON_BIN=$(command -v python3) || { echo "python3 was not found on PATH." >&2; exit 1; }
export RUNCAT_PYTHON_BIN="$PYTHON_BIN"
exec "$PYTHON_BIN" "$SCRIPT_DIR/scripts/manage_install.py" install
