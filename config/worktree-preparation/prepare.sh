#!/usr/bin/env bash
set -euo pipefail
# Keep the native nine-argument contract. The selected publisher is a permanent
# byte-identical copy of the canonical profile owner, not a Task-local module.
exec "$2" -B -c '
import json, os, sys
with open(sys.argv[1], encoding="utf-8") as stream:
    publisher = json.load(stream)["publisher"]
os.execv(sys.executable, [sys.executable, "-B", publisher, "prepare", *sys.argv[2:]])
' "$9" "$@"
