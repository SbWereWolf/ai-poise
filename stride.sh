#!/usr/bin/env bash
set -euo pipefail

stride_poise_root=
stride_project=
while [[ $# -gt 0 ]]; do
  case "$1" in
    --poise-root)
      stride_poise_root=$2
      shift 2
      ;;
    --project)
      stride_project=$2
      shift 2
      ;;
    *) break ;;
  esac
done
if [[ -z $stride_poise_root || -z $stride_project || $# -lt 1 ]]; then
  printf '%s\n' 'Usage: stride.sh --poise-root PATH --project ID BOOTSTRAP.env [arguments...]' >&2
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
export PYTHONPATH="$stride_poise_root/src:$stride_poise_root"
exec "$STRIDE_PYTHON" -P -s -B -m poise.stride --config "$STRIDE_CONFIG" --project "$stride_project" -- "${stride_arguments[@]}"
