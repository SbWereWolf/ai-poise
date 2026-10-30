#!/usr/bin/env bash
set -euo pipefail
exec "$2" -B "$1/protocol_fixture.py" prepare "$@"
