#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: bash tests/smoke.sh /absolute/path/to/python" >&2
  exit 2
fi

task_python=$1

exec "$task_python" -B -m pytest -q \
  tests/test_happy_path.py::test_full_tdd_flow_commits_without_remote_push_and_stops \
  tests/batch/test_work_tools.py::test_single_packet_result_artifacts_messages_and_replay \
  tests/projects/test_service.py::test_setup_creates_independent_runtime_config_without_task_or_git_mutation \
  tests/sprints/test_service.py::test_bootstrap_by_sprint_id_returns_ready_set_without_claim \
  tests/hook_transport/test_hooks.py::test_session_launcher_is_quoted_and_executable \
  tests/result_integration/test_service.py::test_completed_result_integration_uses_live_config_without_digest_gate
