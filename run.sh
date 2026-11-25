#!/usr/bin/env bash
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ ! -x "$root/.venv/bin/lyricflow" ]]; then
    printf '%s\n' 'Run ./install.sh first.' >&2
    exit 1
fi
exec "$root/.venv/bin/lyricflow" "$@"
