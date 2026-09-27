"""Test-owned action protocol used by the accepted component CLI checks."""

import json
import os
from pathlib import Path
import sys
import time


role, state_name, log_name, requirement_id, fail_action = sys.argv[1:]
state_path = Path(state_name)
with Path(log_name).open("a", encoding="utf-8") as log:
    log.write(json.dumps({
        "action": role,
        "cwd": os.getcwd(),
        "token": os.environ.get("TOKEN"),
        "parent": os.environ.get("P001_PARENT"),
        "denied": os.environ.get("P001_DENIED"),
    }) + "\n")

if fail_action == role:
    sys.exit(7)
if fail_action == "slow" and role == "check":
    time.sleep(0.25)

if role == "check":
    state = state_path.read_text(encoding="utf-8")
    packet = {
        "schema": "environment-maintenance/action-result/v2",
        "requirement_id": requirement_id,
        "action": "check",
        "state": state,
    }
    if state == "unavailable":
        packet["cause"] = "repository is offline"
        packet["recommendations"] = ["restore repository access"]
else:
    state_path.write_text("satisfied", encoding="utf-8")
    packet = {
        "schema": "environment-maintenance/action-result/v2",
        "requirement_id": requirement_id,
        "action": role,
        "outcome": "completed",
    }

print(json.dumps(packet, separators=(",", ":")))
