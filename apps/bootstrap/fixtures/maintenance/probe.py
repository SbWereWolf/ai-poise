#!/usr/bin/env python3
"""Disposable external command, with explicit audit and business-state separation."""
from pathlib import Path
import json
import os
import subprocess
import sys
import time


def main():
    settings_path = Path(sys.argv[1])
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    state, audit = Path(settings["state"]), Path(settings["audit"])
    operation = sys.argv[2]
    if operation == "late":
        time.sleep(2)
        state.write_text("late orphan effect", encoding="utf-8")
        return 0
    request = json.loads(sys.stdin.read()) if operation == "repair" else None
    mode = request["mode"] if request else operation
    entry = {"mode": mode, "cwd": os.getcwd(), "args": sys.argv[3:],
             "environment": {key: os.environ.get(key) for key in
                             ("T027_INHERITED", "T027_NOT_INHERITED", "T027_EXPLICIT")}}
    if request:
        entry["request"] = request
        if request.get("schema") != "environment-maintenance/repair-request/v1" or request.get("requirement_id") != "r":
            raise ValueError("Unbound fixture repair request")
    if settings["spawn"] and mode == "check":
        child = subprocess.Popen([sys.executable, __file__, str(settings_path), "late"], start_new_session=True)
        entry["child"] = child.pid
    audit.parent.mkdir(parents=True, exist_ok=True)
    with audit.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
    if mode == "apply":
        if settings["materialize"]:
            state.parent.mkdir(parents=True, exist_ok=True)
            state.write_text("ready", encoding="utf-8")
        for mutation in settings["mutations"]:
            path = Path(mutation["path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(mutation["value"], ensure_ascii=False) + "\n", encoding="utf-8")
    time.sleep(settings["sleep"].get(mode, 0))
    code = (0 if state.exists() else 1) if mode == "check" else settings["apply_exit"] if mode == "apply" else 0
    if request:
        status = ("satisfied" if code == 0 else "action_required") if mode == "check" else (
            "repaired" if code == 0 else "failed") if mode == "apply" else "recommended"
        result = {"schema": "environment-maintenance/repair-result/v1", "requirement_id": "r", "mode": mode,
                  "status": status, "recommendation": "Operator repair required"}
        if settings["echo"]:
            result["observed_parameters"] = request["parameters"]
        print(json.dumps(result, ensure_ascii=False))
        return 3 if code != 0 and mode == "check" else code
    size = settings["stdout_bytes"].get(mode)
    print("X" * size if size is not None else "|".join(sys.argv[3:]) if settings["echo"] else mode.upper())
    return code


if __name__ == "__main__":
    raise SystemExit(main())
