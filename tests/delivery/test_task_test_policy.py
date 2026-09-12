from pathlib import Path


SMOKE_NODE_IDS = (
    "tests/test_happy_path.py::test_full_tdd_flow_commit_push_and_stop",
    "tests/batch/test_work_tools.py::test_single_packet_result_artifacts_messages_and_replay",
    "tests/projects/test_service.py::test_setup_creates_independent_runtime_config_without_task_or_git_mutation",
    "tests/sprints/test_service.py::test_bootstrap_by_sprint_id_returns_ready_set_without_claim",
    "tests/hook_transport/test_hooks.py::test_session_launcher_is_quoted_and_executable",
    "tests/result_integration/test_service.py::test_completed_result_integration_uses_live_config_without_digest_gate",
)


def test_smoke_entry_is_explicit_and_bounded():
    root = Path(__file__).resolve().parents[2]
    runner = root / "tests/smoke.sh"
    assert runner.is_file(), "smoke entry point missing"

    source = runner.read_text()
    assert 'if [ "$#" -ne 1 ]' in source
    assert 'task_python=$1' in source
    assert 'exec "$task_python" -B -m pytest -q' in source
    assert all(node_id in source for node_id in SMOKE_NODE_IDS)
    assert source.count("::test_") == len(SMOKE_NODE_IDS)
    assert "pytest -q tests" not in source
