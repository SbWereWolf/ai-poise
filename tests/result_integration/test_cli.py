import json
import os
import subprocess
import sys

from conftest import git

from .helpers import integration_input, prepare_completed_task


def test_work_cli_exposes_one_result_integration_operation(project):
    _, source_worktree, source = prepare_completed_task(
        project, lambda tree: (tree / "src" / "feature.py").write_text("VALUE = 1\n")
    )
    packet = {
        "operation": "integrate",
        "input": integration_input(project, source, request_id="cli-integration"),
        "messages": [],
    }
    result = subprocess.run(
        [sys.executable, "-m", "harness", "work"],
        input=json.dumps(packet),
        capture_output=True,
        text=True,
        cwd=project["app"],
        env={
            **os.environ,
            "HARNESS_CONFIG": str(project["config_path"]),
            "HARNESS_SESSION": "cli-integrator",
        },
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    response = json.loads(result.stdout)
    assert response["status"] == "integrated"
    assert response["task"] == "T1"
    assert response["source_commit"] == source
    assert response["target_after"] == git(project["app"], "rev-parse", "HEAD")
    assert not source_worktree.exists()
