#!/usr/bin/env python3
"""Real test-owned external adapter. Produces literals from the public contract."""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

mode, context_path = sys.argv[1], Path(sys.argv[2])
context = json.loads(context_path.read_text(encoding="utf-8"))
public = subprocess.run(context["public_argv"], input=b"", capture_output=True, check=False)
status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
denials = {}
for label, path in (("production_write", Path(os.environ["PROTECTED_CANARY"])),
                    ("production_create", Path(os.environ["PROTECTED_ROOT"])/"MUST_NOT_EXIST"),
                    ("context_write", context_path)):
    try:
        with path.open("ab") as stream:
            stream.write(b"UNEXPECTED-WRITE")
        denials[label] = False
    except PermissionError:
        denials[label] = True
if context["step_id"] == "second":
    for operation in ("read", "write", "replace"):
        prior = Path.cwd().parent/"first"/"result.json"
        try:
            if operation == "read": prior.read_bytes()
            elif operation == "write": prior.write_bytes(b"UNEXPECTED-WRITE")
            else: (Path.cwd().parent/"first").rename(Path.cwd().parent/"REPLACED")
            denials["prefix_"+operation] = False
        except PermissionError:
            denials["prefix_"+operation] = True
observation = {
    "uid": os.getuid(), "euid": os.geteuid(), "gid": os.getgid(), "egid": os.getegid(),
    "groups": os.getgroups(), "caps": {key: status[key].strip() for key in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")},
    "no_new_privs": int(status["NoNewPrivs"].strip()), "environment": dict(os.environ),
    "cwd": str(Path.cwd()), "context_path": str(context_path), "context": context,
    "public_exit": public.returncode, "public_stdout": public.stdout.decode("utf-8"),
    "public_stderr": public.stderr.decode("utf-8"), "arguments": sys.argv[3:],
    "stdin": sys.stdin.read(), "denials": denials
}
Path("adapter-observation.json").write_text(json.dumps(observation, ensure_ascii=False)+"\n", encoding="utf-8")
Path("started").write_text("real adapter reached\n", encoding="utf-8")
result = {"schema": "stride-uat-case-result/v1", "run_id": context["run_id"],
          "step_id": context["step_id"], "application_id": context["application_id"],
          "status": "PASS", "reason": None, "graph": None}
if public.returncode != 0:
    result.update(status="FAIL", reason="public application failed")
if context["graph"] is not None:
    graph = context["graph"]
    raw = Path(graph["path"]).read_bytes()
    proof = {"selected_graph": json.loads(raw), "graph_sha256": hashlib.sha256(raw).hexdigest(),
             "public_argv": context["public_argv"], "public_exit": public.returncode,
             "public_stdout": public.stdout.decode("utf-8")}
    Path("graph-proof.json").write_text(json.dumps(proof, ensure_ascii=False)+"\n", encoding="utf-8")
    result["graph"] = {"id": graph["id"], "sha256": hashlib.sha256(raw).hexdigest(),
                       "checks": [{"id": "public-toy-graph", "status": "PASS", "evidence_paths": ["graph-proof.json"]}]}
    if mode == "graph-wrong": result["graph"]["sha256"] = "0"*64
    if mode == "graph-fail": result["graph"]["checks"][0]["status"] = "FAIL"
    if mode == "graph-missing": Path("graph-proof.json").unlink()
    if mode == "graph-escape": result["graph"]["checks"][0]["evidence_paths"] = ["../escape.json"]
    if mode == "graph-duplicate": result["graph"]["checks"] *= 2
    if mode == "graph-link":
        Path("graph-proof.json").unlink()
        Path("graph-proof.json").symlink_to(Path.cwd().parent/"outside.json")
    if mode == "graph-null": result["graph"] = None
if mode in ("fail", "fail-zero"):
    result.update(status="FAIL", reason="fixture failed")
if mode in ("blocked", "blocked-zero"):
    result.update(status="BLOCKED", reason="fixture_blocked")
if mode == "wrong-id": result["run_id"] = "another-run"
if mode == "extra-field": result["extra"] = True
if mode == "bad-reason": result["reason"] = "PASS must have null reason"
if mode == "wrong-status": result["status"] = "passed"
if mode == "wrong-schema": result["schema"] = "unknown/v1"
path = Path(context["result_path"])
if mode != "missing-result":
    path.write_text(json.dumps(result, ensure_ascii=False)+"\n", encoding="utf-8")
if mode == "malformed-result": path.write_bytes(b"{bad")
if mode == "duplicate-result": path.write_bytes(b'{"schema":1,"schema":2}')
if mode == "nonfinite-result": path.write_bytes(b'{"status":NaN}')
if mode == "invalid-utf8-result": path.write_bytes(b'\xff')
if mode == "oversized-result": path.write_bytes(b"x"*2097152)
if mode == "result-link":
    path.unlink(); path.symlink_to(Path.cwd().parent/"outside-result.json")
if mode == "sleep": time.sleep(12)
if mode == "signal": os.kill(os.getpid(), signal.SIGTERM)
if mode == "stdout-overflow":
    sys.stdout.buffer.write(b"x"*131072); sys.stdout.buffer.flush()
elif mode == "stderr-overflow":
    sys.stderr.buffer.write(b"x"*131072); sys.stderr.buffer.flush()
else:
    sys.stdout.write(context["step_id"]+"\n")
    sys.stderr.write("diag-"+context["step_id"]+"\n")
raise SystemExit(3 if mode == "blocked" else 1 if mode in ("fail", "pass-nonzero") else 0)
