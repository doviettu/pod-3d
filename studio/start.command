#!/bin/zsh
set -eu
studio_root="$(cd "$(dirname "$0")" && pwd)"
exec "$studio_root/../.venv/bin/python" "$studio_root/studio.py" "$@"
