#!/usr/bin/env python3
"""Finite test-owned apply child; no service, database or host state is touched."""
import json
from pathlib import Path
import subprocess
import sys
import time

state, audit, mode = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
if mode == "late":
    time.sleep(0.7)
    state.write_text("late effect", encoding="utf-8")
    raise SystemExit(0)
if mode != "apply":
    raise SystemExit(2)
state.write_text("ready", encoding="utf-8")
child = subprocess.Popen([sys.executable, __file__, str(state), str(audit), "late"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
with audit.open("a", encoding="utf-8") as stream:
    stream.write(json.dumps({"mode": "apply", "child": child.pid}) + "\n")
print("APPLY")
