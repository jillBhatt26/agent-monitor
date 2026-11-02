#!/usr/bin/env bash
# Kept for old instructions: the monitor is now part of the normal install. Same as ./install.sh.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$HERE/install.sh" "$@"
