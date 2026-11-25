#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
root=$PWD
data=${XDG_DATA_HOME:-"$HOME/.local/share"}
config=${XDG_CONFIG_HOME:-"$HOME/.config"}
id=io.github.lyricflow.LyricFlow
desktop="$data/applications/$id.desktop"
icon="$data/icons/hicolor/scalable/apps/$id.svg"
case ${1:-} in
    --uninstall)
        rm -f -- "$desktop" "$icon" "$config/autostart/$id.desktop"
        printf '%s\n' 'Launcher removed. The project virtual environment and local lyric library are retained.'
        exit 0
        ;;
    ""|--dev) ;;
    *) printf 'Usage: %s [--dev|--uninstall]\n' "$0" >&2; exit 2 ;;
esac
"${LYRICFLOW_PYTHON:-/usr/bin/python3}" -m venv .venv
if [[ ${1:-} == --dev ]]; then
    .venv/bin/python -m pip install '.[test]'
else
    .venv/bin/python -m pip install .
fi
install -Dm644 src/lyricflow/ui/icon.svg "$icon"
mkdir -p -- "$(dirname -- "$desktop")"
.venv/bin/python - "$root" "$desktop" <<'PY'
import sys
from pathlib import Path

root, destination = map(Path, sys.argv[1:])
command = str(root / "run.sh")
if "\n" in command or "\r" in command:
    raise SystemExit("The installation path cannot contain newlines")
# Exec field escaping occurs after desktop-entry string unescaping.
for character in ('\\', '"', '`', '$'):
    if character == '\\':
        command = command.replace(character, '\\\\')
    else:
        command = command.replace(character, '\\' + character)
command = command.replace('\\', '\\\\').replace('%', '%%')
source = (root / "packaging" / "io.github.lyricflow.LyricFlow.desktop").read_text()
destination.write_text(source.replace("Exec=lyricflow\n", f'Exec="{command}"\n'), encoding="utf-8")
PY
if command -v update-desktop-database >/dev/null; then
    update-desktop-database "$data/applications"
fi
printf '%s\n' 'LyricFlow is installed. Launch it from GNOME or run ./run.sh.'
