"""Bounded genuine host-protocol fixture around existing public native owners."""
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess


def exercise(request):
    from poise.infrastructure.hook_transport import HookService
    from poise.interfaces.content_reader import execute
    from poise.infrastructure.clock import SystemClock
    inputs = request["builtin_inputs"]
    service = HookService(request["settings"])
    installed = service.install({"request_id": "general-builtins-install", "expected_revision": None,
                                 "definition": request["definition"]})
    policy = Path(inputs["reader_policy"])
    raw = json.loads(policy.read_text())
    raw["admission"] = {"hook_settings": request["settings"],
                        "definition_path": installed["definition_path"]}
    policy.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def reader(operation):
        out, err = io.StringIO(), io.StringIO()
        code = execute(request["config"], request["checkout"], policy,
                       io.BytesIO(json.dumps({"operation": operation}).encode()), out, err, SystemClock())
        assert code == 0 and err.getvalue() == "", (code, out.getvalue(), err.getvalue())
        return json.loads(out.getvalue())

    template = reader("prepare_template")
    responses, commands, checkpoints = [], [], []
    coverage_before, coverage_after, readiness = None, None, None
    binding = None
    for original in request["events"]:
        event = dict(original)
        kind = event["hook_event_name"]
        if kind == "PostToolUse":
            cp = subprocess.run(["bash", "-c", event["tool_input"]["command"]],
                                cwd=request["checkout"], env=dict(os.environ),
                                capture_output=True, timeout=10, check=False)
            commands.append({"actual_exit_code": cp.returncode, "stdout": cp.stdout.decode(),
                             "stderr": cp.stderr.decode()})
            assert cp.returncode == 0
            event["tool_response"] = {"stdout": cp.stdout.decode(), "stderr": cp.stderr.decode(),
                                      "exit_code": cp.returncode}
        response = service.event(installed["definition_path"], event)
        if kind == "PreToolUse":
            assert response["hookSpecificOutput"]["permissionDecision"] == "allow", response
        responses.append({"event": event, "response": response})
        if kind == "SessionStart":
            binding = service.latest_binding(event["session_id"], "primary")
            summary = Path(binding["binding_path"]).parent / inputs["summary_file"]
            readiness = json.loads(summary.read_text(encoding="utf-8"))
            if event["source"] == "compact":
                coverage_after = reader("coverage")
        if kind == "PostToolUse":
            coverage_before = reader("coverage")
        if kind == "PreCompact":
            checkpoints = [json.loads(p.read_text())
                           for p in Path(raw["session"]["temporary_root"]).rglob(inputs["checkpoint_file"])]
    with sqlite3.connect(service.settings.database) as db:
        rows = db.execute("SELECT id,event,data FROM hook_events WHERE binding=? ORDER BY id",
                          (binding["session_id"],)).fetchall()
    end = next(row[0] for row in reversed(rows) if row[1] == "SessionEnd")
    diagnostic = service.diagnose({"definition_path": installed["definition_path"],
        "session_id": binding["session_id"], "event": "SessionEnd", "event_id": end,
        "run_ids": [], "probe_cwd": None})
    witnesses = [json.loads(p.read_text()) for p in Path(inputs["witnesses"]).glob("*.json")]
    return {"exit_code": 0, "responses": responses, "commands": commands, "event_rows": rows,
            "binding": binding, "diagnostic": diagnostic, "template": template, "readiness": readiness,
            "coverage_before": coverage_before, "coverage_after": coverage_after,
            "checkpoints": checkpoints, "consumer_observations": witnesses}
