#!/usr/bin/env bash
# Invoke from the existing operator-owned post-commit/post-merge hook.
set -eu
if [ "$#" -ne 3 ]; then
    printf '%s\n' 'Usage: bash source-index-post-commit.example.sh PYTHON PROJECT_PROFILE POLICY' >&2
    exit 2
fi
index_python=$1
index_profile=$2
index_policy=$3
index_checkout=$(git rev-parse --show-toplevel)
exec "$index_python" -B -m poise source-index \
    --config "$index_profile" --checkout "$index_checkout" --policy "$index_policy" \
    <<'JSON'
{"operation":"update","arguments":[]}
JSON
