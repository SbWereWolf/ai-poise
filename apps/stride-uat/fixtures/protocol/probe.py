#!/usr/bin/env python3
"""Read-only, independent observation of the actual reduced kernel identity."""
import json
import os
from pathlib import Path
import sys

status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
print(json.dumps({
    "uid": os.getuid(), "euid": os.geteuid(), "gid": os.getgid(), "egid": os.getegid(),
    "groups": os.getgroups(),
    "caps": {key: status[key].strip() for key in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")},
    "no_new_privs": int(status["NoNewPrivs"].strip()),
    "access": {path: {"read": os.access(path, os.R_OK), "write": os.access(path, os.W_OK), "search": os.access(path, os.X_OK)} for path in sys.argv[1:]}
}, separators=(",", ":")))
