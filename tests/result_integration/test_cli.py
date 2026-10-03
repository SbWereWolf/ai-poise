import json
import os
import subprocess
import sys
from pathlib import Path

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
    source_root = Path(__file__).resolve().parents[2] / "src"
    result = subprocess.run(
        [sys.executable, "-m", "poise", "work"],
        input=json.dumps(packet),
        capture_output=True,
        text=True,
        cwd=project["app"],
        env={
            **{key: value for key, value in os.environ.items()
               if key not in ("CODEX_SESSION_ID", "CODEX_THREAD_ID")},
            "POISE_CONFIG": str(project["config_path"]),
            "POISE_CALLER_BINDING": str(project["root"] / "cli-integrator.json"),
            "PYTHONPATH": str(source_root),
        },
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    response = json.loads(result.stdout)
    if "response_path" in response:
        response = json.loads(Path(response["response_path"]).read_text())
    assert response["status"] == "integrated"
    assert response["task"] == "T1"
    assert response["source_commit"] == source
    assert response["target_after"] == git(project["app"], "rev-parse", "HEAD")
    assert not source_worktree.exists()
