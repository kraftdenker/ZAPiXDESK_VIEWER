#!/usr/bin/env bash
# ZAPiXDeskViewerQt — launcher for Linux/macOS.
#
# Uses the project's venv if it exists; otherwise, creates one and installs
# the dependencies (Pillow + PySide6) automatically.
#
# The Qt viewer (ZAPiXDeskViewerQt.py) requires PySide6, which lives in the
# venv. Do not use the system python3 directly.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PY="$DIR/venv/bin/python"

if [[ -x "$VENV_PY" ]]; then
    exec "$VENV_PY" "$DIR/ZAPiXDeskViewerQt.py" "$@"
fi

echo "venv not found — creating it and installing dependencies…" >&2
python3 -m venv "$DIR/venv"
"$DIR/venv/bin/python" -m pip install --quiet --upgrade pip
"$DIR/venv/bin/python" -m pip install --quiet -r "$DIR/requirements.txt"
exec "$VENV_PY" "$DIR/ZAPiXDeskViewerQt.py" "$@"
