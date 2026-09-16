#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: recover_runtime.sh --settings ABSOLUTE_PATH --python ABSOLUTE_PATH

Rebuild the configured Poise virtual environment from an explicitly selected
compatible Python executable. The script validates Poise and pytest before
publishing the environment. It does not modify the Task database or create a
caller/session identity. After success, repeat a native Codex event so the
existing binding or launcher can be used or regenerated.
EOF
}

settings=''
bootstrap_python=''
while (($#)); do
  case "$1" in
    --settings)
      (($# >= 2)) || { usage >&2; exit 2; }
      settings=$2
      shift 2
      ;;
    --python)
      (($# >= 2)) || { usage >&2; exit 2; }
      bootstrap_python=$2
      shift 2
      ;;
    --help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown argument: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

[[ $settings = /* ]] || { printf '%s\n' '--settings must be an absolute path' >&2; exit 2; }
[[ $bootstrap_python = /* ]] || { printf '%s\n' '--python must be an absolute path' >&2; exit 2; }
[[ -f $settings ]] || { printf 'Hook settings do not exist: %s\n' "$settings" >&2; exit 2; }
[[ -x $bootstrap_python ]] || { printf 'Bootstrap Python is not executable: %s\n' "$bootstrap_python" >&2; exit 2; }

configured_python=$(
  "$bootstrap_python" -B - "$settings" <<'PY'
import json
from pathlib import Path
import sys

path = Path(sys.argv[1])
value = json.loads(path.read_text(encoding="utf-8"))
selected = value.get("python")
if not isinstance(selected, str) or not Path(selected).is_absolute():
    raise SystemExit("hook settings require an absolute python path")
print(selected)
PY
)

if [[ $(basename "$configured_python") != python || $(basename "$(dirname "$configured_python")") != bin ]]; then
  printf 'Configured interpreter must use the recoverable ENV/bin/python layout: %s\n' "$configured_python" >&2
  exit 2
fi

environment_root=$(dirname "$(dirname "$configured_python")")
if [[ -e $environment_root ]]; then
  printf 'Configured environment root already exists; preserve or move it before recovery: %s\n' "$environment_root" >&2
  exit 2
fi

candidate="${environment_root}.recovery.$$"
cleanup() {
  if [[ -e $candidate ]]; then
    find "$candidate" -depth -delete
  fi
}
trap cleanup EXIT

repository_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)
"$bootstrap_python" -B -m venv "$candidate"
"$candidate/bin/python" -B -m pip install --disable-pip-version-check -r "$repository_root/requirements-dev.txt"
PYTHONPATH="$repository_root/src" "$candidate/bin/python" -B -c 'import poise, pytest'
mv "$candidate" "$environment_root"
trap - EXIT

printf 'Recovered configured Poise runtime: %s\n' "$configured_python"
printf '%s\n' 'Repeat the native Codex event to resume with the existing session identity.'
