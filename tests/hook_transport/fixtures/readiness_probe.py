"""Readiness fixture command with independently observed invocation effects."""
import json
import sys
import time
from pathlib import Path

counter, state, mode = map(Path, sys.argv[1:4])
with counter.open("a", encoding="utf-8") as stream:
    stream.write("called\n")
if str(mode) == "timeout":
    time.sleep(1)
if str(mode) == "output":
    print("ready" * 10000)
elif str(mode) == "project":
    print(json.dumps({"project": str(Path.cwd())}))
else:
    print("ready")
raise SystemExit(0 if state.read_text(encoding="utf-8").strip() == "available" else 7)
