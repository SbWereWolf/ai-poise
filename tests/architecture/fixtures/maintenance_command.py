"""Independent external process: explicit scenario, actual argv and effect log."""

import argparse
import json
import os
from pathlib import Path
import signal
import sys


parser = argparse.ArgumentParser()
parser.add_argument("action")
parser.add_argument("--tool-config", required=True)
parser.add_argument("--catalog-dir", required=True)
parser.add_argument("--values", action="append", default=[])
parser.add_argument("--set", action="append", default=[])
parser.add_argument("--dry-run", action="store_true")
args = parser.parse_args()
config = json.loads(Path(args.tool_config).read_text(encoding="utf-8"))
with Path(config["log"]).open("a", encoding="utf-8") as log:
    log.write(json.dumps({"argv": sys.argv[1:], "cwd": str(Path.cwd()),
                          "marker": config["marker"]}, ensure_ascii=False) + "\n")

scenario = config["scenario"]
state = Path(config["state"])
status, code = "ready", 0
if scenario == "signal":
    sys.stdout.write("partial external stdout\n")
    sys.stdout.flush()
    sys.stderr.write("partial external stderr\n")
    sys.stderr.flush()
    os.kill(os.getpid(), signal.SIGTERM)
if scenario == "unknown":
    sys.stdout.write("unrecognized external reply\n")
    sys.stderr.write("external failure details\n")
    raise SystemExit(9)
if scenario == "contradictory":
    sys.stdout.write('{"schema":"environment-maintenance/result/v2","status":"ready"}\n')
    raise SystemExit(7)
if scenario == "unavailable":
    status, code = "unavailable", 3
if scenario == "failed":
    status, code = "failed", 4
if scenario in {"repair-success", "repair-failure", "verify-failure"}:
    if args.action == "repair":
        if scenario == "repair-failure":
            status, code = "failed", 4
        else:
            state.write_text("corrected", encoding="utf-8")
    elif args.action == "verify":
        if scenario != "repair-success":
            status, code = "action_required", 2
    else:
        status, code = "action_required", 2
if scenario == "malformed":
    sys.stdout.write("{incomplete result\n")
    raise SystemExit(0)
if scenario == "empty":
    raise SystemExit(0)
packet = {"schema": "environment-maintenance/result/v2", "status": status}
if scenario == "foreign-schema":
    packet["schema"] = "another-tool/result/v1"
if scenario == "large":
    packet["details"] = "Ж" * 24000 + "END-OF-FULL-RESULT"
sys.stdout.write(json.dumps(packet, ensure_ascii=False, separators=(",", ":")) + "\n")
sys.stderr.write("external diagnostic: " + args.action + "\n")
raise SystemExit(code)
