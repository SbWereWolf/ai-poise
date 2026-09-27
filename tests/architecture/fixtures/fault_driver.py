"""Inject process-boundary failures without changing Bootstrap product code."""

import json
import os
from pathlib import Path
import runpy
import subprocess
import sys


product, mode = sys.argv[1:]
calls = Path(os.environ["BOOTSTRAP_FAULT_CALLS"])
count = 0


def fake_run(argv, *args, **kwargs):
    global count
    count += 1
    with calls.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(list(argv)) + "\n")
    if mode == "probe_spawn" and count == 1:
        raise OSError("synthetic probe spawn failure")
    if mode == "probe_signal" and count == 1:
        return subprocess.CompletedProcess(argv, -15, b"probe-partial", b"probe-error")
    if mode == "probe_unexpected" and count == 1:
        return subprocess.CompletedProcess(argv, 99, b"", b"probe-error")
    if mode == "action_spawn" and count == 2:
        raise OSError("synthetic action spawn failure")
    return subprocess.CompletedProcess(argv, 0, b"", b"")


subprocess.run = fake_run
sys.argv = [product, "check", "--catalog-dir", "/selected"]
sys.path.insert(0, str(Path(product).parent))
runpy.run_path(product, run_name="__main__")
