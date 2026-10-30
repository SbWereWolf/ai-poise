#!/usr/bin/env bash
set -euo pipefail
exec "${LIVE_CONTRACT_PYTHON:?}" -B "${LIVE_CONTRACT_FIXTURE:?}" "$@"
