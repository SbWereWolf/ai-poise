"""Forward real Popen except the explicit post-durable-intent test fault."""
import json
import os
from pathlib import Path
import subprocess

original = subprocess.Popen
used = False

def observed(*args, **kwargs):
    global used
    path = os.environ.get("POISE_T030_FAULT_PROTOCOL")
    journal = None
    if path and not used:
        try:
            journal = json.loads(Path(path).read_text())
        except (OSError, ValueError):
            pass
    action = os.environ.get("POISE_T030_FAULT_ACTION")
    eligible = journal and any(step.get("state") == "RUNNING" for step in journal.get("steps", []))
    if action == "corrupt-prefix":
        eligible = eligible and len(journal["steps"]) == 2 and journal["steps"][0]["state"] == "PASS"
    if eligible:
        used = True
        trace = Path(os.environ["POISE_T030_FAULT_TRACE"])
        with trace.open("a", encoding="utf-8") as out:
            out.write(json.dumps({"event": "post_durable_intent", "action": os.environ["POISE_T030_FAULT_ACTION"],
                                  "argv": list(args[0]) if args else kwargs.get("args"), "journal": journal})+"\n")
            out.flush(); os.fsync(out.fileno())
        if action == "corrupt-prefix":
            result_path = Path(journal["steps"][0]["outputs"]["result"]["path"])
            with result_path.open("ab") as out:
                out.write(b"\n "); out.flush(); os.fsync(out.fileno())
        if os.environ["POISE_T030_FAULT_ACTION"] == "crash":
            os._exit(94)
        if os.environ["POISE_T030_FAULT_ACTION"] == "drift":
            with Path(os.environ["POISE_T030_FAULT_SOURCE"]).open("ab") as out:
                out.write(b"\n# actual source drift after durable intent\n")
    return original(*args, **kwargs)

subprocess.Popen = observed
