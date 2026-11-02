"""Independent external process for persistent Bootstrap entry contracts."""

import argparse
import json
from pathlib import Path
import sys


parser = argparse.ArgumentParser()
parser.add_argument("operation")
parser.add_argument("context")
parser.add_argument("marker")
parser.add_argument("log")
parser.add_argument("scenario")
parser.add_argument("--values", action="append", default=[])
parser.add_argument("--set", action="append", default=[])
parser.add_argument("--dry-run", action="store_true")
args = parser.parse_args()
with Path(args.log).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps({"argv": sys.argv[1:], "cwd": str(Path.cwd())},
                            ensure_ascii=False) + "\n")
if args.scenario == "unknown":
    print("unknown external result")
    print("external diagnostic", file=sys.stderr)
    raise SystemExit(9)
print("external ready")
