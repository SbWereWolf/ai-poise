#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $1 != /* || ! -f $1 ]]; then
  printf '%s\n' 'Stride requires an existing absolute bootstrap .env path.' >&2
  exit 2
fi
stride_bootstrap=$1
shift
stride_arguments=("$@")
unset STRIDE_PYTHON STRIDE_CONFIG
# This is an explicitly selected, trusted operator configuration.
source "$stride_bootstrap"
for stride_name in STRIDE_PYTHON STRIDE_CONFIG; do
  if [[ -z ${!stride_name:-} || ${!stride_name} != /* ]]; then
    printf '%s: an explicit absolute path is required.\n' "$stride_name" >&2
    exit 2
  fi
done
if [[ ! -x $STRIDE_PYTHON ]]; then
  printf 'STRIDE_PYTHON is not executable: %s\n' "$STRIDE_PYTHON" >&2
  exit 2
fi
if [[ ! -f $STRIDE_CONFIG ]]; then
  printf 'STRIDE_CONFIG does not exist: %s\n' "$STRIDE_CONFIG" >&2
  exit 2
fi
exec "$STRIDE_PYTHON" -I -B -m poise.stride --config "$STRIDE_CONFIG" -- "${stride_arguments[@]}"
