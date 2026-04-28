#!/usr/bin/env sh
set -eu

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

if command -v python3 >/dev/null 2>&1; then
  exec python3 "$script_dir/deploy_sub2api_local.py" "$@"
fi

exec python "$script_dir/deploy_sub2api_local.py" "$@"
