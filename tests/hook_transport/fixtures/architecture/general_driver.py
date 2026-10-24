"""Observe actual isolated public entry usage; never substitute a product result.

The sentinel is installed before importing Poise. It observes Task imports,
Poise construction and current_task calls without blocking or replacing them.
The emitted envelope is test instrumentation, separate from actual API output.
"""
import importlib.abc
import io
import json
from pathlib import Path
import sys


observed = {"task_imports": [], "poise_constructions": 0, "current_task_calls": 0}


class ImportObserver(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "poise.runtime" or fullname.startswith("poise.modules.tasks"):
            observed["task_imports"].append(fullname)
        return None


def profile(frame, event, arg):
    if event != "call":
        return
    module = frame.f_globals.get("__name__", "")
    name = frame.f_code.co_name
    if module.startswith("poise.") and name == "current_task":
        observed["current_task_calls"] += 1
    if module == "poise.runtime" and name == "__init__":
        obj = frame.f_locals.get("self")
        if obj is not None and type(obj).__name__ == "Poise":
            observed["poise_constructions"] += 1


sys.meta_path.insert(0, ImportObserver())
sys.setprofile(profile)
request = json.load(sys.stdin)
result = {"reached": [], "exit_code": None, "stdout": "", "stderr": ""}
try:
    from poise.infrastructure.project_binding import read_project_checkout
    result["reached"].append("read_project_checkout")
    bound = read_project_checkout(request["config"], request["checkout"])
    operation = request["operation"]
    if operation == "admit":
        result.update(exit_code=0, project=bound.config["project"], checkout=str(bound.checkout))
    elif operation == "session":
        from poise.infrastructure.session_establishment import direct_caller, establish_project_session
        import os
        result["reached"].append("establish_project_session")
        caller = direct_caller(bound.config["project"], os.environ)
        first = establish_project_session(bound, caller)
        second = establish_project_session(bound, caller)
        result.update(exit_code=0, first=first.session_id, second=second.session_id,
                      origin=first.origin)
    elif operation == "native":
        from poise.infrastructure.hook_transport import HookService
        import sqlite3
        result["reached"].append("HookService.install/event")
        service = HookService(request["settings"])
        installed = service.install({"request_id": "general-native-install", "expected_revision": None,
                                     "definition": request["definition"]})
        responses, work, messages = [], None, None
        binding = None
        for event in request["events"]:
            responses.append(service.event(installed["definition_path"], event))
            if event["hook_event_name"] == "SessionStart":
                binding = service.latest_binding(event["session_id"], "primary")
                if request.get("task") is not None:
                    work = service.work(binding["binding_path"], {"operation": "bootstrap",
                        "input": {"task": request["task"], "decision": None,
                                  "feedback": None, "rework_stage": None}, "messages": []})
            if request.get("task") is not None and event["hook_event_name"] == "UserPromptSubmit":
                messages = service.work(binding["binding_path"], {"operation": "show", "input": {
                    "queries": [{"id": "messages", "kind": "messages"}]}, "messages": []})
        with sqlite3.connect(service.settings.database) as db:
            rows = db.execute("SELECT id,event,data FROM hook_events WHERE binding=? ORDER BY id",
                              (binding["session_id"],)).fetchall()
        ended = [row for row in rows if row[1] == "SessionEnd"]
        diagnostic = service.diagnose({"definition_path": installed["definition_path"],
            "session_id": binding["session_id"], "event": "SessionEnd",
            "event_id": ended[-1][0] if ended else None, "run_ids": [], "probe_cwd": None})
        result.update(exit_code=0, responses=responses, work=work, messages=messages, binding=binding,
                      event_rows=rows, diagnostic=diagnostic)
    else:
        output, error = io.StringIO(), io.StringIO()
        body = io.BytesIO(json.dumps(request["packet"], ensure_ascii=False).encode())
        from poise.infrastructure.clock import SystemClock
        if operation == "placement":
            from poise.interfaces.placement import execute
            result["reached"].append("placement.execute")
            code = execute(request["config"], request["checkout"], request["policy"], body, output)
        elif operation in ("checks", "checks-cli"):
            from poise.interfaces.project_checks import execute, execute_cli
            result["reached"].append("project_checks." + ("execute_cli" if operation.endswith("cli") else "execute"))
            args = [request["config"], request["checkout"], request["policy"]]
            if operation == "checks-cli":
                args.append("brief")
            args.extend([body, output, error, SystemClock()])
            code = (execute_cli if operation == "checks-cli" else execute)(*args)
        elif operation in ("tool-result", "tool-result-cli"):
            from poise.interfaces.tool_results import execute
            result["reached"].append("tool_results.execute")
            code = execute(request["config"], request["checkout"], request["policy"],
                           "agent" if operation.endswith("cli") else "machine", body, output)
        elif operation == "reader":
            from poise.interfaces.content_reader import execute
            result["reached"].append("content_reader.execute")
            code = execute(request["config"], request["checkout"], request["policy"],
                           body, output, error, SystemClock())
        else:
            raise AssertionError("Unknown test operation: " + operation)
        result.update(exit_code=code, stdout=output.getvalue(), stderr=error.getvalue())
except Exception as exc:
    result["exception"] = {"type": type(exc).__name__, "message": str(exc)}
    result["exit_code"] = None
finally:
    sys.setprofile(None)
result["sentinel"] = observed
sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
