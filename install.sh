#!/usr/bin/env bash
# Linux/macOS wrapper around install.py (which also works on Windows: `python install.py`).
#   ./install.sh                 install (venv + Textual, commands on PATH, config)
#   ./install.sh --apply-hooks   also add the Claude Code hooks (settings backed up first)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
command -v python3 >/dev/null || { echo "python3 not found. Install Python 3.9 or newer."; exit 1; }
exec python3 "$HERE/install.py" "$@"
